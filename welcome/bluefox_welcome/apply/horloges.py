"""Format de l'heure et seconde horloge du panneau (#25854).

Deux demandes d'Olivier, apres le premier essai complet :

  « Can we add 12/24 hour clock preferences per user? By default 24 h. »
  « If user's timezone is different from Company's general timezone, should
    show 2nd clock. »

CE QUE LA POLITIQUE PORTE, ET CE QUI ARRIVE SANS ELLE
-----------------------------------------------------
Bloc `session.clock` de `bf-policy/v2`, tout entier facultatif :

    {"format": "24h" | "12h",
     "second_timezone": "America/Montreal" | null}

Absent, on applique 24 h et aucune seconde horloge — c'est le defaut demande,
et il ne depend donc pas d'un deploiement Odoo pour etre vrai.

⚠️ CE FICHIER EST ECRIT PAR PLASMA, PAS PAR NOUS. Le panneau et ses applets
sont crees par plasmashell a sa premiere execution : on ne les invente pas, on
retouche ce qu'il a ecrit. Si l'horloge n'y est pas encore, on ne fabrique
rien — un applet pose dans un panneau qui n'existe pas donnerait un panneau
casse, ce qui est bien pire qu'une heure au mauvais format.
"""
from __future__ import annotations

import configparser
import logging
import os
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)

APPLETSRC = Path.home() / ".config/plasma-org.kde.plasma.desktop-appletsrc"
HORLOGE = "org.kde.plasma.digitalclock"
FORMAT_DEFAUT = "24h"


def horloges_de_la_politique(prov: dict | None) -> dict:
    """Le bloc `session.clock`, complete par les defauts."""
    bloc = {}
    if isinstance(prov, dict):
        session = prov.get("session") or {}
        if isinstance(session, dict):
            bloc = session.get("clock") or {}
    if not isinstance(bloc, dict):
        bloc = {}
    fmt = str(bloc.get("format") or FORMAT_DEFAUT).lower()
    if fmt not in ("12h", "24h"):
        fmt = FORMAT_DEFAUT
    second = bloc.get("second_timezone") or None
    return {"format": fmt, "second_timezone": second or None}


def _lire(chemin: Path) -> configparser.ConfigParser:
    conf = configparser.ConfigParser(
        interpolation=None, strict=False, delimiters=("=",))
    conf.optionxform = str  # KConfig distingue la casse des cles
    conf.read(chemin, encoding="utf-8")
    return conf


def _applets_horloge(conf: configparser.ConfigParser) -> list[str]:
    """Les groupes d'applet qui SONT une horloge numerique."""
    # ⚠️ configparser rend « Containments][2][Applets][21 » : le premier crochet
    # et le dernier sont manges par l'en-tete, les autres restent dans le nom.
    return [s for s in conf.sections()
            if "][Applets][" in s and conf.get(s, "plugin", fallback="") == HORLOGE]


def _contenant(section: str) -> str:
    """« Containments][2][Applets][21 » -> « Containments][2 »."""
    return section.split("][Applets][")[0]


def _identifiant(section: str) -> int:
    try:
        return int(section.split("][Applets][")[1].split("]")[0])
    except (IndexError, ValueError):
        return -1


def _prochain_identifiant(conf: configparser.ConfigParser) -> int:
    ids = [_identifiant(s) for s in conf.sections() if "][Applets][" in s]
    return max([i for i in ids if i > 0], default=0) + 1


def appliquer_horloges(prov: dict | None, appletsrc: Path | None = None,
                       relancer=None) -> tuple[bool, str]:
    """Regle le format de l'heure, et ajoute la seconde horloge s'il le faut."""
    reglage = horloges_de_la_politique(prov)
    chemin = appletsrc or APPLETSRC
    if not chemin.is_file():
        return False, f"{chemin.name} absent : Plasma n'a pas encore ecrit son panneau"

    conf = _lire(chemin)
    horloges = _applets_horloge(conf)
    if not horloges:
        return False, "aucune horloge numerique dans le panneau : rien retouche"

    notes = []
    vingt_quatre = "true" if reglage["format"] == "24h" else "false"
    for section in horloges:
        apparence = f"{section}][Configuration][Appearance"
        if not conf.has_section(apparence):
            conf.add_section(apparence)
        # ⚠️ use24hFormat n'est PAS un booleen chez Plasma : 0 = 12 h, 1 = 24 h,
        # 2 = « comme la locale ». Ecrire true/false y laisse la locale decider.
        conf.set(apparence, "use24hFormat", "1" if reglage["format"] == "24h" else "0")
        notes.append(f"{_identifiant(section)}:{reglage['format']}")

    seconde = reglage["second_timezone"]
    if seconde:
        deja = [s for s in horloges
                if seconde in conf.get(f"{s}][Configuration][Appearance",
                                       "selectedTimeZones", fallback="")]
        if deja:
            notes.append(f"seconde({seconde}):deja la")
        else:
            ajoutee = _ajouter_seconde_horloge(conf, horloges[0], seconde, vingt_quatre)
            notes.append(f"seconde({seconde}):{'posee' if ajoutee else 'panneau illisible'}")

    with chemin.open("w", encoding="utf-8") as fh:
        conf.write(fh, space_around_delimiters=False)

    if relancer is None:
        relancer = _relancer_plasmashell
    notes.append(f"plasmashell:{'relance' if relancer() else 'non relance'}")
    return True, "; ".join(notes)


def _ajouter_seconde_horloge(conf: configparser.ConfigParser, modele: str,
                             fuseau: str, vingt_quatre: str) -> bool:
    """Pose une deuxieme horloge a cote de la premiere, sur le fuseau donne.

    Rend False si le panneau ne se laisse pas lire : on prefere une machine
    sans seconde horloge a un panneau ampute.
    """
    contenant = _contenant(modele)
    general = f"{contenant}][General"
    ordre = conf.get(general, "AppletOrder", fallback="")
    if not ordre:
        return False
    neuf = _prochain_identifiant(conf)
    section = f"{contenant}][Applets][{neuf}"
    conf.add_section(section)
    conf.set(section, "immutability", "1")
    conf.set(section, "plugin", HORLOGE)
    apparence = f"{section}][Configuration][Appearance"
    conf.add_section(apparence)
    conf.set(apparence, "use24hFormat", "1" if vingt_quatre == "true" else "0")
    conf.set(apparence, "selectedTimeZones", fuseau)
    conf.set(apparence, "lastSelectedTimezone", fuseau)
    # Le code du fuseau (EDT, NZST) a cote de l'heure : sans lui, deux horloges
    # cote a cote donnent deux nombres sans dire lequel est lequel.
    conf.set(apparence, "displayTimezoneFormat", "Code")
    conf.set(apparence, "showLocalTimezone", "true")
    conf.set(general, "AppletOrder", f"{ordre};{neuf}")
    return True


def _relancer_plasmashell() -> bool:
    """Plasma relit son fichier au demarrage, pas en cours de route.

    ⚠️ Non prouve sur machine : au premier demarrage, l'agent tourne pendant
    que plasmashell finit de s'installer, et c'est peut-etre LUI qui ecrira en
    dernier. A verifier au prochain essai en VM avant de promettre quoi que ce
    soit a l'operateur.
    """
    env = dict(os.environ)
    try:
        r = subprocess.run(
            ["qdbus6", "org.kde.plasmashell", "/PlasmaShell",
             "org.kde.PlasmaShell.refreshCurrentShell"],
            capture_output=True, text=True, timeout=20, env=env)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:  # noqa: BLE001
        LOG.info("plasmashell non relance : %s", e)
        return False
