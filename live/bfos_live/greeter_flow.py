"""Logique de l'ecran de connexion, sans interface (#26870).

Une tentative = create_session(compte d'attente) puis on suit greetd :
  * info « bfos:device {json} »  -> QR a dessiner (sur_qr) ;
  * info « bfos:progres {json} » -> etape de l'ouverture (sur_progres) ;
  * autre info                   -> texte (sur_info) ;
  * erreur PAM                   -> texte d'erreur (sur_erreur) ;
  * question (visible/secrete)   -> inattendue : on annule ;
  * success                      -> on demarre la session Plasma (sur_fin).
Un echec (code expire, refus, reseau) annule la session greetd et rend False :
l'appelant relance une tentative, donc un nouveau code.

⚠️ greetd ne laisse pas annuler pendant que PAM attend le QR (il garde son
verrou jusqu'a la reponse de PAM). Le code se renouvelle donc a son
expiration, pas sur demande.
"""
from __future__ import annotations

import json

from bfos_live.langue import _

PREFIXE_QR = "bfos:device "
PREFIXE_CHOIX = "bfos:choix "
PREFIXE_PROGRES = "bfos:progres "
PREFIXE_DOMAINE = "bfos:domaine "
# Meme commande que la session Plasma de Fedora (wayland-sessions/plasma.desktop) :
# startplasma-wayland nu demarre sans bus de session propre a la session, et le
# theme, les reglages et l'agent d'accueil s'y appliquent mal.
SESSION_PLASMA = ["/usr/libexec/plasma-dbus-run-session-if-needed",
                  "/usr/bin/startplasma-wayland"]
ENV_PLASMA = ["XDG_SESSION_TYPE=wayland", "XDG_SESSION_DESKTOP=KDE",
              "XDG_CURRENT_DESKTOP=KDE", "DESKTOP_SESSION=plasma"]


def lire_message_qr(texte):
    """(donnees, texte_humain) si le message porte la ligne machine, sinon None."""
    if not texte.startswith(PREFIXE_QR):
        return None
    premiere, _sep, reste = texte.partition("\n")
    try:
        d = json.loads(premiere[len(PREFIXE_QR):])
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("uri_complete"), str):
        return None
    if not d["uri_complete"].startswith("https://"):
        return None
    return d, reste


def lire_message_progres(texte):
    """{etape, total, texte} si le message porte la ligne machine de
    progression (1 <= etape <= total), sinon None."""
    if not texte.startswith(PREFIXE_PROGRES):
        return None
    try:
        d = json.loads(texte[len(PREFIXE_PROGRES):].partition("\n")[0])
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    etape, total, libelle = d.get("etape"), d.get("total"), d.get("texte")
    if (not isinstance(etape, int) or not isinstance(total, int) or not isinstance(libelle, str)
            or not 1 <= etape <= total <= 20):
        return None
    return {"etape": etape, "total": total, "texte": libelle}


def lire_message_choix(texte):
    """(donnees, texte_humain) si la question porte la ligne machine du choix
    de session, sinon None. Chaque option a un `id` et un `titre`."""
    if not texte.startswith(PREFIXE_CHOIX):
        return None
    premiere, _sep, reste = texte.partition("\n")
    try:
        d = json.loads(premiere[len(PREFIXE_CHOIX):])
    except ValueError:
        return None
    options = d.get("options") if isinstance(d, dict) else None
    if (not isinstance(options, list) or not options
            or not all(isinstance(o, dict) and isinstance(o.get("id"), str)
                       and isinstance(o.get("titre"), str) for o in options)):
        return None
    return d, reste


def lire_message_domaine(texte):
    """(donnees, texte_humain) si la question demande le domaine de
    l'organisation (cle generique), sinon None."""
    if not texte.startswith(PREFIXE_DOMAINE):
        return None
    premiere, _sep, reste = texte.partition("\n")
    try:
        d = json.loads(premiere[len(PREFIXE_DOMAINE):])
    except ValueError:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("titre"), str):
        return None
    for cle in ("texte", "erreur", "valeur"):
        if not isinstance(d.get(cle, ""), str):
            return None
    return d, reste


class Rappels:
    """A surcharger par l'interface. Appeles depuis le fil de travail."""

    def sur_qr(self, donnees, humain): ...
    def sur_info(self, texte): ...
    def sur_progres(self, donnees): ...
    def sur_erreur(self, texte): ...
    def sur_fin(self): ...
    def sur_choix(self, donnees, humain):
        """Rend l'`id` de l'option choisie, ou None pour annuler."""
        return None

    def sur_domaine(self, donnees, humain):
        """Rend le domaine tape, ou None pour annuler."""
        return None


def tentative(client, rappels, compte_attente="bfos-connexion",
              cmd=SESSION_PLASMA, env=ENV_PLASMA):
    """Une tentative de connexion. True si la session est demarree."""
    rep = client.creer_session(compte_attente)
    while True:
        genre = rep.get("type")
        if genre == "auth_message":
            style = rep.get("auth_message_type")
            texte = str(rep.get("auth_message") or "")
            if style == "info":
                qr = lire_message_qr(texte)
                progres = lire_message_progres(texte) if qr is None else None
                if qr is not None:
                    rappels.sur_qr(*qr)
                elif progres is not None:
                    rappels.sur_progres(progres)
                else:
                    rappels.sur_info(texte)
                rep = client.repondre(None)
                continue
            if style == "error":
                rappels.sur_erreur(texte)
                rep = client.repondre(None)
                continue
            # Une question : le choix de session (ou de l'organisation), ou le domaine.
            choix = lire_message_choix(texte) if style == "visible" else None
            if choix is not None:
                donnees, humain = choix
                reponse = rappels.sur_choix(donnees, humain)
                ids = [o["id"] for o in donnees["options"]]
                if reponse in ids:
                    rep = client.repondre(reponse)
                    continue
            domaine = lire_message_domaine(texte) if style == "visible" else None
            if domaine is not None:
                reponse = rappels.sur_domaine(*domaine)
                if isinstance(reponse, str) and reponse.strip():
                    rep = client.repondre(reponse.strip()[:253])
                    continue
            rappels.sur_erreur(_("Question inattendue, nouvelle tentative."))
            client.annuler()
            return False
        if genre == "success":
            fin = client.demarrer(cmd, env)
            if fin.get("type") == "success":
                rappels.sur_fin()
                return True
            rappels.sur_erreur(str(fin.get("description") or _("Impossible de démarrer la session.")))
            client.annuler()
            return False
        # error : echec PAM (code expire, refus) ou erreur de greetd.
        if rep.get("error_type") != "auth_error" and rep.get("description"):
            rappels.sur_erreur(str(rep["description"]))
        try:
            client.annuler()
        except Exception:  # noqa: BLE001 - la prochaine tentative repart de zero
            pass
        return False
