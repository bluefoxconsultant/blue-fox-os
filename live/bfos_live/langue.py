"""Langues de la clé live (#26870, 2026-10-27 : « la clé, toutes langues »).

Les textes affichés restent écrits en français dans le code, marqués `_("…")` (ou `N_("…")`
pour une constante traduite plus tard, à l'affichage). Une langue = un fichier PO standard
`po/<code>.po` à côté de ce module (msgid français, msgstr traduit) : l'ajouter suffit, sans
.mo ni étape de construction. Un texte sans traduction reste en français.

La langue est celle du processus, posée par `choisir()` :
  - l'écran de connexion : celle de la clé (live.json « langue »), puis le choix fait à l'écran ;
  - l'aide PAM : celle de l'écran (fichier `LANGUE_ECRAN`, déposé par l'écran) ;
  - l'unité de session : celle de la demande de l'aide, puis celle de la personne.
"""
from __future__ import annotations

import os
import re
import stat

ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PO = os.path.join(ICI, "po")
SOURCE = "fr"
CODE_RE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")
# Nom de chaque langue dans sa propre langue, pour le sélecteur de l'écran.
NOMS = {"fr": "Français", "en": "English", "es": "Español", "de": "Deutsch", "it": "Italiano",
        "pt": "Português", "nl": "Nederlands", "ar": "العربية", "zh": "中文"}
# Choix fait à l'écran de connexion (usager greetd), relu par l'aide PAM (root).
LANGUE_ECRAN = "/run/bfos-live-ecran/langue"

_catalogues: dict[str, dict[str, str]] = {}
_courante = SOURCE


def _lire_chaine(ligne):
    """Le contenu d'une chaîne PO entre guillemets, échappements compris."""
    corps = ligne[ligne.index('"') + 1:ligne.rindex('"')]
    out, i = [], 0
    while i < len(corps):
        c = corps[i]
        if c == "\\" and i + 1 < len(corps):
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(corps[i + 1], corps[i + 1]))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def lire_po(chemin):
    """{msgid: msgstr} d'un fichier PO (sans pluriels ni contextes ; entrées « fuzzy » écartées)."""
    entrees, msgid, msgstr, champ, flou = {}, None, None, None, False

    def ranger():
        if msgid and msgstr and not flou:
            entrees[msgid] = msgstr

    with open(chemin, encoding="utf-8") as f:
        for brute in f:
            ligne = brute.strip()
            if not ligne:
                continue
            if ligne.startswith("#,"):
                ranger()
                msgid, msgstr, champ, flou = None, None, None, "fuzzy" in ligne
                continue
            if ligne.startswith("#"):
                continue
            if ligne.startswith("msgid "):
                if msgid is not None and champ == "msgstr":
                    ranger()
                    flou = False
                msgid, msgstr, champ = _lire_chaine(ligne), None, "msgid"
            elif ligne.startswith("msgstr "):
                msgstr, champ = _lire_chaine(ligne), "msgstr"
            elif ligne.startswith('"') and champ == "msgid":
                msgid += _lire_chaine(ligne)
            elif ligne.startswith('"') and champ == "msgstr":
                msgstr += _lire_chaine(ligne)
    ranger()
    return entrees


def disponibles():
    """Codes des langues offertes : le français, plus un fichier PO par langue."""
    codes = {SOURCE}
    try:
        codes |= {n[:-3] for n in os.listdir(DOSSIER_PO) if n.endswith(".po") and CODE_RE.fullmatch(n[:-3])}
    except OSError:
        pass
    return sorted(codes, key=lambda c: (c != SOURCE, c))


def normaliser(code):
    """« fr_CA.UTF-8 », « en-CA », « en » -> le code d'une langue offerte, ou None."""
    code = str(code or "").split(".")[0].split("@")[0].replace("-", "_")
    offertes = disponibles()
    if code in offertes:
        return code
    court = code.split("_")[0]
    return court if court in offertes else None


def choisir(code):
    """Pose la langue du processus ; un code inconnu laisse le français. Rend la langue posée."""
    global _courante
    _courante = normaliser(code) or SOURCE
    if _courante != SOURCE and _courante not in _catalogues:
        try:
            _catalogues[_courante] = lire_po(os.path.join(DOSSIER_PO, _courante + ".po"))
        except (OSError, ValueError):   # UnicodeDecodeError compris : un catalogue abîmé = français
            _catalogues[_courante] = {}
    return _courante


def courante():
    return _courante


class _Traduit(str):
    """Un texte traduit qui se formate sans risque : une traduction mal formée (champ
    inconnu, `{x.y}`, accolade seule) retombe sur le texte français au lieu de lever une
    exception dans l'écran de connexion (relecture adverse du 2026-10-27)."""

    def __new__(cls, traduit, source):
        objet = super().__new__(cls, traduit)
        objet.source = source
        return objet

    def __reduce__(self):
        # Copie, deepcopy, pickle : une simple str (sans quoi __new__ exigerait `source`).
        return (str, (str(self),))

    def format(self, *args, **champs):  # noqa: A003 - même nom que str.format, voulu
        try:
            return str.format(self, *args, **champs)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            return str.format(self.source, *args, **champs)


def _(texte):
    """Le texte dans la langue courante (le français s'il n'est pas traduit)."""
    if _courante == SOURCE:
        return texte
    return _Traduit(_catalogues.get(_courante, {}).get(texte, texte), texte)


def N_(texte):
    """Marque un texte à traduire plus tard (constante de module) : rendu tel quel."""
    return texte


def lire_langue_ecran(chemin=LANGUE_ECRAN, proprietaire=None):
    """Le choix déposé par l'écran de connexion, ou None. Lu par root : pas de lien, fichier
    ordinaire et court, du propriétaire attendu si on le donne ; seul un code connu passe."""
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (proprietaire is not None and st.st_uid != proprietaire):
            return None
        brut = os.read(fd, 32).decode("ascii", "replace").strip()
    finally:
        os.close(fd)
    return normaliser(brut) if CODE_RE.fullmatch(brut) else None
