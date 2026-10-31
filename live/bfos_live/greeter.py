#!/usr/bin/python3 -I
"""Ecran de connexion de la cle USB live Blue Fox OS (#26870).

Lance par greetd dans un compositeur kiosque (cage), sous l'usager `greetd`.
Il ouvre une tentative de connexion des que le reseau repond ; pam_bfos.so
renvoie la ligne `bfos:device {json}`, que cet ecran dessine en vrai QR.
L'approbation se fait sur le telephone (2FA comprise), puis la session
Plasma demarre. Un code expire est remplace tout seul.

Rien de secret ne transite par cet ecran : il ne voit ni jeton ni mot de
passe Authentik, seulement l'adresse et le code a afficher (et le mot de
passe Wi-Fi si on le tape).
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time

from PyQt6.QtCore import QObject, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QProgressBar,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

# Dans l'image, tout vit a plat dans /usr/libexec/bfos-live (bfos_provision.py
# et le paquet bfos_live a cote de ce fichier) ; dans le depot, ce fichier est
# DANS le paquet et bfos_provision vit dans install/.
_ICI = os.path.dirname(os.path.abspath(__file__))
for _chemin in (_ICI, os.path.join(_ICI, "..", "..", "install")):
    if os.path.exists(os.path.join(_chemin, "bfos_provision.py")):
        sys.path.insert(0, os.path.abspath(_chemin))
        break
if not os.path.isdir(os.path.join(_ICI, "bfos_live")):
    sys.path.insert(0, os.path.abspath(os.path.join(_ICI, "..")))

import bfos_provision as bp  # noqa: E402
from bfos_live import greeter_flow, greetd_ipc, langue, organisation, reseau  # noqa: E402
from bfos_live.langue import _  # noqa: E402

CONFIG = "/etc/bfos-live/live.json"
CONFIG_ECRAN = "/etc/bfos-live/greeter.json"

# Couleurs de marque : anthracite en fond, bleu en ACCENT seulement (jamais en
# texte sur fond sombre : contraste insuffisant).
ANTHRACITE = "#2E3132"
BLEU = "#29ABE2"
TEXTE = "#F2F4F5"
TEXTE_DOUX = "#B9C0C4"
ERREUR = "#FFB4A8"

FEUILLE = f"""
QWidget#fond {{ background: {ANTHRACITE}; }}
QLabel {{ color: {TEXTE}; }}
QLabel#doux {{ color: {TEXTE_DOUX}; }}
QLabel#erreur {{ color: {ERREUR}; }}
QLabel#code {{ color: {TEXTE}; letter-spacing: 4px; }}
QWidget#carte {{ background: rgba(20, 22, 23, 0.82); border-radius: 18px;
                 border-top: 4px solid {BLEU}; }}
QPushButton {{ color: {TEXTE}; background: #3B3F40; border: 1px solid #50565A;
               border-radius: 8px; padding: 8px 16px; }}
QPushButton:hover {{ border-color: {BLEU}; }}
QPushButton#option {{ text-align: left; padding: 0; }}
QProgressBar {{ background: #1E2021; border: none; border-radius: 4px; max-height: 8px; }}
QProgressBar::chunk {{ background: {BLEU}; border-radius: 4px; }}
QPushButton#option:hover, QPushButton#option:focus {{ border: 2px solid {BLEU}; }}
QPushButton#lien {{ background: transparent; border: none; padding: 2px 4px; color: {TEXTE};
                   text-decoration: underline; }}
QPushButton#lien:hover, QPushButton#lien:focus {{ color: #FFFFFF; }}
QLineEdit, QListWidget {{ color: {TEXTE}; background: #1E2021; border: 1px solid #50565A;
                          border-radius: 6px; padding: 6px; }}
QComboBox {{ color: {TEXTE}; background: #3B3F40; border: 1px solid #50565A; border-radius: 8px;
             padding: 7px 12px; }}
QComboBox:hover, QComboBox:focus {{ border-color: {BLEU}; }}
QComboBox QFrame {{ background: #1E2021; border: 1px solid #50565A; }}
QComboBox QAbstractItemView {{ color: {TEXTE}; background: #1E2021; border: 1px solid #50565A;
                               selection-background-color: #3B3F40; selection-color: {TEXTE}; outline: 0; }}
"""


def lire_json(chemin, defaut=None):
    try:
        with open(chemin, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else (defaut or {})
    except (OSError, ValueError):
        return defaut or {}


def echelle(largeur_px, hauteur_px, largeur_mm):
    """Facteur d'agrandissement de l'ecran de connexion. Le compositeur
    kiosque (cage) annonce toujours 1 : sur une Surface (2736 x 1824 pour
    26 cm), tout etait minuscule (essai du 2026-10-26). D'apres la densite
    reelle quand l'ecran donne sa taille (110 ppp = un ecran de bureau), sinon
    d'apres sa hauteur (1080 lignes = 1) ; jamais au point que la carte ne
    tienne plus en hauteur (900 lignes logiques). Par quarts, au moins 1."""
    if largeur_mm and largeur_mm >= 100 and largeur_px:
        f = largeur_px / (largeur_mm / 25.4) / 110
    else:
        f = (hauteur_px or 1080) / 1080
    if hauteur_px:
        f = min(f, hauteur_px / 900)
    return max(1.0, round(f * 4) / 4)


def code_lisible(code: str) -> str:
    """« 222353898 » -> « 222 353 898 » ; un code avec tiret reste tel quel."""
    code = str(code or "")
    if code.isdigit() and len(code) > 4:
        return " ".join(code[i:i + 3] for i in range(0, len(code), 3))
    return code


def rebours(secondes: float) -> str:
    s = max(0, int(secondes))
    return f"{s // 60}:{s % 60:02d}"


class VueQR(QWidget):
    """Dessine un QR (matrice de bfos_provision) en noir sur blanc."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.matrice = None
        self.setMinimumSize(280, 280)

    def montrer(self, texte):
        """Sans texte, la vue disparait : un carre blanc vide restait a
        l'ecran pendant toute l'ouverture (essai du 2026-10-26)."""
        self.matrice = bp._qr_encode(texte) if texte else None
        self.setVisible(self.matrice is not None)
        self.update()

    def paintEvent(self, _ev):  # noqa: N802 - API Qt
        p = QPainter(self)
        cote = min(self.width(), self.height())
        x0, y0 = (self.width() - cote) / 2, (self.height() - cote) / 2
        p.fillRect(QRectF(x0, y0, cote, cote), QColor("white"))
        if not self.matrice:
            p.end()
            return
        n = len(self.matrice)
        marge = 4
        module = cote / (n + 2 * marge)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("black"))
        for r, ligne in enumerate(self.matrice):
            for c, v in enumerate(ligne):
                if v:
                    p.drawRect(QRectF(x0 + (c + marge) * module, y0 + (r + marge) * module,
                                      module + 0.5, module + 0.5))
        p.end()


class Ligne(QLabel):
    """Une etiquette qui disparait quand elle est vide : quatre lignes vides
    laissaient un grand trou dans la carte (cache jusque-la par le carre
    blanc du QR)."""

    def setText(self, texte):  # noqa: N802 - API Qt
        super().setText(texte)
        self.setVisible(bool(texte))


class BoutonOption(QPushButton):
    """Une option du choix de session : titre et explication qui reviennent a
    la ligne (un QPushButton ne le fait pas : le texte debordait de la carte)."""

    def __init__(self, titre, texte, parent=None):
        super().__init__(parent)
        self.setObjectName("option")
        self.setAccessibleName(titre)
        self.setAccessibleDescription(texte)
        lignes = QVBoxLayout(self)
        lignes.setContentsMargins(16, 12, 16, 12)
        lignes.setSpacing(2)
        self.titre = QLabel(titre)
        self.titre.setFont(QFont("Lexend", 12, QFont.Weight.DemiBold))
        self.texte = QLabel(texte)
        self.texte.setObjectName("doux")
        for etiquette in (self.titre, self.texte):
            etiquette.setWordWrap(True)
            etiquette.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lignes.addWidget(etiquette)
        if not texte:
            self.texte.hide()
        politique = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        politique.setHeightForWidth(True)
        self.setSizePolicy(politique)

    def hasHeightForWidth(self):  # noqa: N802 - API Qt
        return True

    def heightForWidth(self, largeur):  # noqa: N802 - API Qt
        return max(56, self.layout().heightForWidth(largeur))

    def sizeHint(self):  # noqa: N802 - API Qt
        largeur = max(320, self.width())
        return QSize(largeur, self.heightForWidth(largeur))


class Signaux(QObject):
    qr = pyqtSignal(dict, str)
    info = pyqtSignal(str)
    progres = pyqtSignal(dict)
    erreur = pyqtSignal(str)
    attente_reseau = pyqtSignal(str)
    choix = pyqtSignal(dict, str)
    domaine = pyqtSignal(dict, str)
    fin = pyqtSignal()


class RappelsQt(greeter_flow.Rappels):
    def __init__(self, signaux, reponses=None, delai=600):
        self.s = signaux
        self.reponses = reponses
        self.delai = delai

    def sur_choix(self, donnees, humain):
        """Appele dans le fil de travail : attend le clic dans l'interface.
        Sans reponse dans le delai, on annule (le QR repart de zero)."""
        if self.reponses is None:
            return None
        while not self.reponses.empty():
            self.reponses.get_nowait()
        self.s.choix.emit(donnees, humain)
        try:
            return self.reponses.get(timeout=self.delai)
        except queue.Empty:
            return None

    def sur_domaine(self, donnees, humain):
        """Comme le choix : attend le domaine tape dans l'interface."""
        if self.reponses is None:
            return None
        while not self.reponses.empty():
            self.reponses.get_nowait()
        self.s.domaine.emit(donnees, humain)
        try:
            return self.reponses.get(timeout=self.delai)
        except queue.Empty:
            return None

    def sur_qr(self, donnees, humain):
        self.s.qr.emit(donnees, humain)

    def sur_info(self, texte):
        self.s.info.emit(texte)

    def sur_progres(self, donnees):
        self.s.progres.emit(donnees)

    def sur_erreur(self, texte):
        self.s.erreur.emit(texte)

    def sur_fin(self):
        self.s.fin.emit()


class Travail(threading.Thread):
    """Boucle de tentatives, hors du fil de l'interface (greetd bloque
    pendant que PAM attend l'approbation)."""

    def __init__(self, signaux, compte_attente, client_usine=greetd_ipc.Client,
                 connectivite=reseau.connectivite, pause=3.0, dormir=time.sleep,
                 reponses=None):
        super().__init__(daemon=True)
        self.signaux = signaux
        self.reponses = reponses
        self.compte = compte_attente
        self.client_usine = client_usine
        self.connectivite = connectivite
        self.pause = pause
        self.dormir = dormir
        self.arret = threading.Event()

    def run(self):
        client = None
        while not self.arret.is_set():
            etat = self.connectivite()
            if etat in ("none", "unknown"):
                self.signaux.attente_reseau.emit(etat)
                self.dormir(self.pause)
                continue
            if etat == "portal":
                self.signaux.attente_reseau.emit(etat)
            try:
                if client is None:
                    client = self.client_usine()
                if greeter_flow.tentative(client, RappelsQt(self.signaux, self.reponses),
                                          self.compte):
                    return
            except (OSError, greetd_ipc.ErreurIPC) as exc:
                self.signaux.erreur.emit(_("Connexion à greetd perdue ({raison}).").format(raison=exc))
                if client is not None:
                    client.fermer()
                client = None
            self.dormir(self.pause)


class Ecran(QWidget):
    def __init__(self, cfg_ecran=None, reseau_mod=reseau, horloge=time.time):
        super().__init__()
        self.setObjectName("fond")
        self.setStyleSheet(FEUILLE)
        self.reseau = reseau_mod
        self.horloge = horloge
        self.expire_a = None
        cfg_ecran = cfg_ecran or {}
        self.fond = QPixmap(cfg_ecran.get("wallpaper", "")) if cfg_ecran.get("wallpaper") else None

        racine = QVBoxLayout(self)
        racine.setContentsMargins(32, 24, 32, 24)

        haut = QHBoxLayout()
        self.etiquette_reseau = QLabel("")
        self.etiquette_reseau.setObjectName("doux")
        self.heure = QLabel("")
        self.heure.setFont(QFont("Lexend", 16))
        # Avant la connexion, le fuseau n'est connu que si la cle le donne
        # (live.json) : sinon l'heure UTC passerait pour l'heure locale.
        self.heure.setVisible(bool(cfg_ecran.get("horloge", True)))
        haut.addWidget(self.etiquette_reseau)
        haut.addStretch(1)
        haut.addWidget(self.heure)
        racine.addLayout(haut)
        racine.addStretch(1)

        self.carte = QWidget()
        self.carte.setObjectName("carte")
        self.carte.setMaximumWidth(560)
        # Largeur stable d'un etat a l'autre (sans QR, la carte se resserrait).
        self.carte.setMinimumWidth(460)
        c = QVBoxLayout(self.carte)
        c.setContentsMargins(36, 28, 36, 28)
        c.setSpacing(12)
        if cfg_ecran.get("logo") and os.path.exists(cfg_ecran["logo"]):
            logo = QLabel()
            logo.setPixmap(QPixmap(cfg_ecran["logo"]).scaledToHeight(
                56, Qt.TransformationMode.SmoothTransformation))
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c.addWidget(logo)
        titre = QLabel("Blue Fox OS")
        titre.setFont(QFont("Lexend", 26, QFont.Weight.DemiBold))
        titre.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(titre)
        self.consigne = QLabel(_("Préparation de la connexion…"))
        self.consigne.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.consigne.setWordWrap(True)
        c.addWidget(self.consigne)
        self.qr = VueQR()
        self.qr.hide()
        c.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignCenter)
        # A la place du QR pendant l'attente et l'ouverture : une barre (etapes
        # de l'unite de session, ou animee tant qu'on n'en sait pas plus).
        self.barre = QProgressBar()
        self.barre.setTextVisible(False)
        self.barre.setRange(0, 0)
        self.barre.setFixedHeight(8)
        c.addWidget(self.barre)
        self.code = Ligne("")
        self.code.hide()
        self.code.setObjectName("code")
        self.code.setWordWrap(True)
        self.code.setFont(QFont("monospace", 24, QFont.Weight.Bold))
        self.code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(self.code)
        self.aide = Ligne("")
        self.aide.hide()
        self.aide.setObjectName("doux")
        self.aide.setWordWrap(True)
        self.aide.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(self.aide)
        self.etat = Ligne("")
        self.etat.hide()
        self.etat.setObjectName("doux")
        self.etat.setWordWrap(True)
        self.etat.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(self.etat)
        self.erreur = Ligne("")
        self.erreur.hide()
        self.erreur.setObjectName("erreur")
        self.erreur.setWordWrap(True)
        self.erreur.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c.addWidget(self.erreur)
        # Choix de session (ephemere / persistant), cache hors de la question.
        self.zone_choix = QWidget()
        self.lignes_choix = QVBoxLayout(self.zone_choix)
        self.lignes_choix.setContentsMargins(0, 8, 0, 0)
        self.lignes_choix.setSpacing(10)
        self.zone_choix.hide()
        c.addWidget(self.zone_choix)
        # Cle generique : domaine de l'organisation, puis « Organisation : X · Changer ».
        self.zone_domaine = QWidget()
        d = QVBoxLayout(self.zone_domaine)
        d.setContentsMargins(0, 8, 0, 0)
        d.setSpacing(10)
        self.champ_domaine = QLineEdit()
        self.champ_domaine.setPlaceholderText("exemple.com")
        self.champ_domaine.setAccessibleName(_("Domaine de ton organisation"))
        self.champ_domaine.returnPressed.connect(self.repondre_domaine)
        self.bouton_domaine = QPushButton(_("Continuer"))
        self.bouton_domaine.clicked.connect(self.repondre_domaine)
        d.addWidget(self.champ_domaine)
        d.addWidget(self.bouton_domaine)
        self.zone_domaine.hide()
        c.addWidget(self.zone_domaine)
        self.ligne_org = QWidget()
        o = QHBoxLayout(self.ligne_org)
        o.setContentsMargins(0, 0, 0, 0)
        o.addStretch(1)
        self.nom_org = QLabel("")
        self.nom_org.setObjectName("doux")
        self.bouton_changer = QPushButton(_("Changer d'organisation"))
        self.bouton_changer.setObjectName("lien")
        self.bouton_changer.clicked.connect(self.changer_organisation)
        o.addWidget(self.nom_org)
        o.addWidget(self.bouton_changer)
        o.addStretch(1)
        self.ligne_org.hide()
        c.addWidget(self.ligne_org)
        self.reponses = queue.Queue()

        ligne = QHBoxLayout()
        ligne.addStretch(1)
        ligne.addWidget(self.carte)
        ligne.addStretch(1)
        racine.addLayout(ligne)
        racine.addStretch(1)

        # Panneau Wi-Fi, replie par defaut.
        self.panneau = QWidget()
        p = QVBoxLayout(self.panneau)
        self.liste = QListWidget()
        self.liste.setMaximumHeight(160)
        self.mdp = QLineEdit()
        self.mdp.setEchoMode(QLineEdit.EchoMode.Password)
        self.mdp.setPlaceholderText(_("Mot de passe du Wi-Fi"))
        self.bouton_connecter = QPushButton(_("Se connecter au Wi-Fi"))
        self.bouton_connecter.clicked.connect(self.connecter_wifi)
        self.message_wifi = QLabel("")
        self.message_wifi.setObjectName("doux")
        p.addWidget(self.liste)
        p.addWidget(self.mdp)
        p.addWidget(self.bouton_connecter)
        p.addWidget(self.message_wifi)
        self.panneau.setMaximumWidth(560)
        self.panneau.hide()
        bas = QHBoxLayout()
        self.bouton_wifi = QPushButton(_("Wi-Fi"))
        self.bouton_wifi.clicked.connect(self.basculer_wifi)
        bas.addWidget(self.bouton_wifi)
        # Langue de l'ecran (et de l'aide PAM, qui relit le choix) : une par fichier PO.
        self.choix_langue = QComboBox()
        self.choix_langue.setAccessibleName("Langue / Language")
        for code in langue.disponibles():
            self.choix_langue.addItem(langue.NOMS.get(code, code), code)
        self.choix_langue.setCurrentIndex(max(0, self.choix_langue.findData(langue.courante())))
        self.choix_langue.setVisible(self.choix_langue.count() > 1)
        self.choix_langue.currentIndexChanged.connect(
            lambda _i: self.changer_langue(self.choix_langue.currentData()))
        bas.addWidget(self.choix_langue)
        self._dernier = None
        bas.addStretch(1)
        racine.addWidget(self.panneau, 0, Qt.AlignmentFlag.AlignHCenter)
        racine.addLayout(bas)

        self.minuterie = QTimer(self)
        self.minuterie.timeout.connect(self.battre)
        self.minuterie.start(1000)
        self.battre()

    # --------------------------------------------------------------- dessin
    def paintEvent(self, ev):  # noqa: N802 - API Qt
        if self.fond is not None and not self.fond.isNull():
            p = QPainter(self)
            p.drawPixmap(self.rect(), self.fond.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
            p.end()
        else:
            super().paintEvent(ev)

    # ------------------------------------------------------------ evenements
    def battre(self):
        self.heure.setText(time.strftime("%H:%M"))
        if self.expire_a is not None:
            reste = self.expire_a - self.horloge()
            self.etat.setText(_("Code valide encore {duree}").format(duree=rebours(reste)) if reste > 0
                              else _("Nouveau code en préparation…"))

    def occupe(self, total=0, valeur=0):
        """Barre visible : animee (total 0) ou a l'etape `valeur` sur `total`."""
        self.barre.setRange(0, total)
        self.barre.setValue(valeur)
        self.barre.show()

    def montrer_qr(self, donnees, _humain):
        self._dernier = (self.montrer_qr, (donnees, _humain))
        self.erreur.setText("")
        self.barre.hide()
        self.qr.montrer(donnees["uri_complete"])
        self.code.setText(code_lisible(donnees.get("user_code", "")))
        self.consigne.setText(_("Scanne ce code avec ton téléphone pour te connecter."))
        uri = donnees.get("uri") or donnees["uri_complete"]
        self.aide.setText(_("Ou va sur {adresse} et entre le code ci-dessus.").format(
            adresse=uri.removeprefix("https://")))
        try:
            self.expire_a = self.horloge() + int(donnees.get("expires_in") or 600)
        except (TypeError, ValueError):
            self.expire_a = None
        self.zone_domaine.hide()
        nom = str(donnees.get("organisation") or "")
        self.nom_org.setText(_("Organisation : {nom}").format(nom=nom) if nom else "")
        self.bouton_changer.setEnabled(True)
        self.ligne_org.setVisible(bool(nom))
        self.battre()

    def montrer_info(self, texte):
        self._dernier = (self.montrer_info, (texte,))
        self.zone_domaine.hide()
        self.ligne_org.hide()
        self.zone_choix.hide()
        self.qr.montrer(None)
        self.code.setText("")
        self.aide.setText("")
        self.expire_a = None
        self.etat.setText(texte)
        if self.barre.maximum() == 0 or self.barre.isHidden():
            self.occupe()

    def montrer_progres(self, donnees):
        """Etape de l'ouverture (unite de session, relayee par l'aide PAM)."""
        self._dernier = (self.montrer_progres, (donnees,))
        self.zone_domaine.hide()
        self.ligne_org.hide()
        self.zone_choix.hide()
        self.qr.montrer(None)
        self.code.setText("")
        self.aide.setText("")
        self.expire_a = None
        self.consigne.setText(_("Ouverture de ta session"))
        self.etat.setText(_("{etape_texte} ({etape} sur {total})").format(
            etape_texte=donnees["texte"], etape=donnees["etape"], total=donnees["total"]))
        self.occupe(donnees["total"], donnees["etape"])

    def montrer_choix(self, donnees, _humain):
        """Une carte par option ; le clic repond a la question PAM."""
        self._dernier = (self.montrer_choix, (donnees, _humain))
        self.zone_domaine.hide()
        self.ligne_org.hide()
        while self.lignes_choix.count():
            w = self.lignes_choix.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.qr.montrer(None)
        self.code.setText("")
        self.expire_a = None
        self.etat.setText("")
        self.erreur.setText(str(donnees.get("erreur") or ""))
        self.consigne.setText(str(donnees.get("titre") or _("Comment veux-tu ouvrir ta session ?")))
        self.aide.setText("")
        self.barre.hide()
        for option in donnees["options"]:
            bouton = BoutonOption(option["titre"], str(option.get("texte") or ""))
            bouton.clicked.connect(lambda _c=False, i=option["id"]: self.repondre_choix(i))
            self.lignes_choix.addWidget(bouton)
        self.zone_choix.show()

    def repondre_choix(self, ident):
        self._dernier = (self.montrer_info, ("",))
        self.consigne.setText(_("Ouverture de ta session"))
        self.zone_choix.hide()
        self.erreur.setText("")
        self.etat.setText("")
        self.occupe()
        self.reponses.put(ident)

    def montrer_domaine(self, donnees, _humain):
        """Cle generique : le domaine de l'organisation, tape une fois."""
        self._dernier = (self.montrer_domaine, (donnees, _humain))
        self.zone_choix.hide()
        self.ligne_org.hide()
        self.qr.montrer(None)
        self.code.setText("")
        self.expire_a = None
        self.etat.setText("")
        self.barre.hide()
        # Textes fixes traduits ICI (meme catalogue que l'aide) : le rejeu d'un
        # changement de langue retraduit la question deja affichee (tournage du
        # 2026-10-28 : elle restait en francais). L'erreur, par sa forme source.
        self.consigne.setText(str(_("Quel est le domaine de ton organisation ?")))
        self.aide.setText(str(_("Celui de son site Web ou de son Odoo, par exemple exemple.com.")))
        source = donnees.get("erreur_source")
        self.erreur.setText(str(_(source)) if isinstance(source, str) and source
                            else str(donnees.get("erreur") or ""))
        if not self.champ_domaine.text():
            self.champ_domaine.setText(str(donnees.get("valeur") or ""))
        self.zone_domaine.show()
        self.champ_domaine.setFocus()

    def repondre_domaine(self):
        texte = self.champ_domaine.text().strip()
        if not texte or self.zone_domaine.isHidden():
            return
        self._dernier = (self.montrer_info, ("",))
        self.zone_domaine.hide()
        self.aide.setText("")
        self.erreur.setText("")
        self.consigne.setText(_("Recherche de ton organisation…"))
        self.occupe()
        self.reponses.put(texte)

    def changer_organisation(self):
        """Depose le drapeau pour l'aide PAM : le QR en cours s'arrete et le
        domaine est redemande au prochain essai."""
        if deposer_changement():
            self.bouton_changer.setEnabled(False)
            self.champ_domaine.clear()
            self.etat.setText(_("Changement d'organisation…"))

    def changer_langue(self, code):
        """Nouvelle langue : deposee pour l'aide PAM (relue avant chaque question), textes
        fixes retraduits, dernier affichage rejoue. Les messages deja recus de l'aide restent
        dans leur langue ; les suivants arrivent dans la nouvelle."""
        langue.choisir(code)
        deposer_langue(langue.courante())
        self.mdp.setPlaceholderText(_("Mot de passe du Wi-Fi"))
        self.bouton_connecter.setText(_("Se connecter au Wi-Fi"))
        self.bouton_wifi.setText(_("Wi-Fi"))
        self.bouton_domaine.setText(_("Continuer"))
        self.champ_domaine.setAccessibleName(_("Domaine de ton organisation"))
        self.bouton_changer.setText(_("Changer d'organisation"))
        erreur, expire_a = self.erreur.text(), self.expire_a
        if self._dernier is not None:
            methode, args = self._dernier
            methode(*args)
        else:
            self.consigne.setText(_("Préparation de la connexion…"))
        # Le rejeu ne doit ni effacer une erreur affichee ni relancer le rebours du code ;
        # une erreur qu'il vient de retraduire (question du domaine) reste la sienne.
        if not self.erreur.text():
            self.erreur.setText(erreur)
        if expire_a is not None and self.expire_a is not None:
            self.expire_a = expire_a
        self.battre()

    def montrer_erreur(self, texte):
        self.erreur.setText(texte)
        if self.barre.maximum() == 0:
            self.barre.hide()

    def montrer_attente_reseau(self, etat):
        if etat == "portal":
            self.etiquette_reseau.setText(_("Réseau derrière un portail : la connexion pourrait échouer."))
            return
        self._dernier = (self.montrer_attente_reseau, (etat,))
        self.etiquette_reseau.setText(_("Pas de réseau. Branche un câble ou choisis un Wi-Fi."))
        self.consigne.setText(_("En attente du réseau…"))
        self.occupe()
        self.qr.montrer(None)
        self.code.setText("")
        self.expire_a = None
        if self.panneau.isHidden():
            self.basculer_wifi()

    def basculer_wifi(self):
        if self.panneau.isHidden():
            self.liste.clear()
            for ssid, securise, force, actif in self.reseau.reseaux_wifi():
                item = QListWidgetItem(f"{'● ' if actif else ''}{ssid}  {force} %{'  🔒' if securise else ''}")
                item.setData(Qt.ItemDataRole.UserRole, (ssid, securise))
                self.liste.addItem(item)
            if self.liste.count() == 0:
                self.message_wifi.setText(_("Aucun Wi-Fi trouvé."))
            self.panneau.show()
        else:
            self.panneau.hide()

    def connecter_wifi(self):
        item = self.liste.currentItem()
        if item is None:
            self.message_wifi.setText(_("Choisis d'abord un réseau."))
            return
        ssid, securise = item.data(Qt.ItemDataRole.UserRole)
        mdp = self.mdp.text() if securise else None
        if securise and not mdp:
            self.message_wifi.setText(_("Ce réseau demande un mot de passe."))
            return
        self.message_wifi.setText(_("Connexion à {ssid}…").format(ssid=ssid))
        QApplication.processEvents()
        ok, msg = self.reseau.connecter_wifi(ssid, mdp)
        self.mdp.clear()
        self.message_wifi.setText(msg)
        if ok:
            self.etiquette_reseau.setText(msg)
            self.panneau.hide()


def brancher(ecran, signaux, app):
    signaux.qr.connect(ecran.montrer_qr)
    signaux.info.connect(ecran.montrer_info)
    signaux.progres.connect(ecran.montrer_progres)
    signaux.erreur.connect(ecran.montrer_erreur)
    signaux.attente_reseau.connect(ecran.montrer_attente_reseau)
    signaux.choix.connect(ecran.montrer_choix)
    signaux.domaine.connect(ecran.montrer_domaine)
    signaux.fin.connect(app.quit)


def deposer_langue(code, chemin=langue.LANGUE_ECRAN):
    """Le choix de l'ecran, pour l'aide PAM (root) : ecrit puis renomme, jamais bloquant."""
    try:
        tmp = chemin + ".tmp"
        with open(tmp, "w", encoding="ascii") as f:
            f.write(code + "\n")
        os.replace(tmp, chemin)
        return True
    except OSError as exc:
        # Au journal de greetd : sans ce fichier, l'aide parle la langue de la cle.
        print(f"bfos-live greeter : langue non deposee ({exc})", file=sys.stderr)
        return False


def deposer_changement(chemin=organisation.DRAPEAU_CHANGER):
    """« Changer d'organisation » : un fichier vide pour l'aide PAM (root), qui
    le consomme. Jamais bloquant."""
    try:
        with open(chemin, "w", encoding="ascii"):
            pass
        return True
    except OSError as exc:
        print(f"bfos-live greeter : changement d'organisation non depose ({exc})", file=sys.stderr)
        return False


def regler_fuseau(cfg, environ=os.environ, zoneinfo="/usr/share/zoneinfo"):
    """Le fuseau de la cle (live.json « timezone »), s'il est valide. Rend
    True s'il est pose : l'horloge de l'ecran ne s'affiche qu'a ce prix."""
    fuseau = str(cfg.get("timezone") or "")
    if (re.match(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$", fuseau)
            and os.path.isfile(os.path.join(zoneinfo, fuseau))):
        environ["TZ"] = fuseau
        time.tzset()
        return True
    return False


def main():
    cfg = lire_json(CONFIG)
    cfg_ecran = dict(lire_json(CONFIG_ECRAN))
    compte = cfg.get("placeholder_user") or "bfos-connexion"
    cfg_ecran["horloge"] = regler_fuseau(cfg)
    deposer_langue(langue.choisir(cfg.get("langue")))
    app = QApplication(sys.argv)
    if not os.environ.get("QT_SCALE_FACTOR"):
        # Le facteur se fixe avant la creation de l'application : on se relance.
        # Tout echec garde l'echelle 1 : un ecran qui plante, greetd le relance
        # en boucle et personne ne peut plus se connecter.
        try:
            ecran_qt = app.primaryScreen()
            taille = ecran_qt.size() * ecran_qt.devicePixelRatio()
            f = echelle(taille.width(), taille.height(), ecran_qt.physicalSize().width())
            if f > 1:
                os.environ["QT_SCALE_FACTOR"] = f"{f:g}"
                os.execv(sys.executable,
                         [sys.executable, "-I", os.path.abspath(__file__)] + sys.argv[1:])
        except Exception:  # noqa: BLE001
            os.environ.pop("QT_SCALE_FACTOR", None)
    ecran = Ecran(cfg_ecran)
    signaux = Signaux()
    brancher(ecran, signaux, app)
    ecran.showFullScreen()
    Travail(signaux, compte, reponses=ecran.reponses).start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
