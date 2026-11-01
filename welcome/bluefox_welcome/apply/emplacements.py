"""Montages Nextcloud dans le panneau « Emplacements » de Dolphin (#26870).

Le panneau lit ~/.local/share/user-places.xbel (KIO, KFilePlacesModel). On
ajoute un signet par montage, sans toucher aux autres.

Fichier absent (premier demarrage) : on l'ecrit avec nos seuls signets et SANS
`kde_places_version`. KIO voit alors une version 0 et ajoute lui-meme ses
emplacements par defaut (Dossier personnel, Bureau, Documents, Telechargements,
Reseau, Corbeille…), chacun seulement s'il manque. Recopier ces defauts ici
les figerait dans une langue et une version de KIO.
"""
from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome.apply.emplacements")

_NS_BOOKMARK = "http://www.freedesktop.org/standards/desktop-bookmarks"
_NS_MIME = "http://www.freedesktop.org/standards/shared-mime-info"
_NS_KDEPRIV = "http://www.kde.org/kdepriv"
_VIDE = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n'
         f'<xbel xmlns:bookmark="{_NS_BOOKMARK}" xmlns:mime="{_NS_MIME}" '
         f'xmlns:kdepriv="{_NS_KDEPRIV}"/>\n')


def _fichier(home: Path) -> Path:
    return home / ".local" / "share" / "user-places.xbel"


def _signet(href: str, titre: str, icone: str) -> ET.Element:
    b = ET.Element("bookmark", {"href": href})
    ET.SubElement(b, "title").text = titre
    info = ET.SubElement(b, "info")
    fd = ET.SubElement(info, "metadata", {"owner": "http://freedesktop.org"})
    ET.SubElement(fd, f"{{{_NS_BOOKMARK}}}icon", {"name": icone})
    return b


def ajouter_emplacements(montages: list[tuple[str, Path]], home: Path | None = None,
                         icone: str = "folder-cloud") -> tuple[bool, str]:
    """`montages` : [(titre, chemin)]. Idempotent : un chemin deja present
    n'est pas ajoute deux fois."""
    if not montages:
        return True, "aucun montage à ajouter"
    home = home or Path.home()
    cible = _fichier(home)
    for prefixe, ns in (("bookmark", _NS_BOOKMARK), ("mime", _NS_MIME), ("kdepriv", _NS_KDEPRIV)):
        ET.register_namespace(prefixe, ns)
    try:
        texte = cible.read_text() if cible.exists() else _VIDE
        racine = ET.fromstring(texte.encode())
    except (OSError, ET.ParseError) as e:
        # Un fichier abime ne se reecrit pas : la personne y a peut-etre ses
        # propres signets. On laisse KIO le gerer.
        LOG.warning("user-places.xbel illisible (%s) : rien ajoute", e)
        return False, f"emplacements illisibles : {e}"
    presents = {b.get("href") for b in racine.iter("bookmark")}
    ajoutes = []
    for titre, chemin in montages:
        href = "file://" + urllib.parse.quote(str(chemin))
        if href in presents:
            continue
        racine.append(_signet(href, titre, icone))
        presents.add(href)
        ajoutes.append(titre)
    if not ajoutes:
        return True, "emplacements déjà présents"
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(racine, space=" ")
        corps = ET.tostring(racine, encoding="unicode")
        tmp = cible.with_suffix(".xbel.bfos")
        tmp.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n' + corps + "\n")
        tmp.replace(cible)
    except OSError as e:
        LOG.exception("ecriture de user-places.xbel")
        return False, f"emplacements : {e}"
    return True, "Dolphin : " + ", ".join(ajoutes)
