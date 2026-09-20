"""Photo de l'usager, tiree de sa fiche employe Odoo (#25854).

    « Could we fetch the user's picture based on the Odoo employee? »

La politique porte l'image elle-meme, encodee, et non une adresse : au premier
demarrage la machine n'a pas encore de session Nextcloud, pas de jeton, et
parfois pas de reseau — une adresse a telecharger serait une photo qui manque
une fois sur deux. Quelques dizaines de kilo-octets dans une politique qui en
fait deja cent, c'est le prix d'une photo qui arrive toujours.

Contrat, facultatif, dans le bloc `user` de `bf-policy/v2` :

    {"avatar": "<base64 d'un PNG ou d'un JPEG>"}

Absent, on ne touche a rien : l'usager garde l'avatar par defaut de Plasma.

⚠️ DEUX ENDROITS, ET UN SEUL SUFFIT RAREMENT. `~/.face.icon` sert la session ;
l'ECRAN DE CONNEXION, lui, lit AccountsService. On ecrit donc le fichier, puis
on demande a AccountsService de le prendre — ce qu'une personne a le droit de
faire pour son propre compte, sans mot de passe d'administration.
"""
from __future__ import annotations

import base64
import binascii
import logging
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)

VISAGE = Path.home() / ".face.icon"
# Les deux entetes qu'on accepte : une politique qui porterait autre chose
# (un SVG, du texte) ecrirait un fichier que personne ne sait afficher.
SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"))
TAILLE_MAX = 2 * 1024 * 1024


def photo_de_la_politique(prov: dict | None) -> bytes | None:
    """Les octets de la photo, ou None si la politique n'en porte pas."""
    if not isinstance(prov, dict):
        return None
    usager = prov.get("user") or {}
    brut = usager.get("avatar") if isinstance(usager, dict) else None
    if not brut or not isinstance(brut, str):
        return None
    try:
        octets = base64.b64decode(brut, validate=True)
    except (binascii.Error, ValueError):
        LOG.warning("avatar de la politique illisible (base64)")
        return None
    if len(octets) > TAILLE_MAX:
        LOG.warning("avatar de %d octets : ignore", len(octets))
        return None
    if not any(octets.startswith(s) for s, _ in SIGNATURES):
        LOG.warning("avatar qui n'est ni PNG ni JPEG : ignore")
        return None
    return octets


def appliquer_photo(prov: dict | None, visage: Path | None = None,
                    poser=None) -> tuple[bool, str]:
    """Ecrit la photo et la donne a AccountsService."""
    octets = photo_de_la_politique(prov)
    if octets is None:
        return False, "aucune photo dans la politique"
    cible = visage or VISAGE
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_bytes(octets)
        cible.chmod(0o644)  # l'ecran de connexion la lit sans etre l'usager
    except OSError as e:
        return False, f"ecriture de {cible.name} impossible : {e}"
    if poser is None:
        poser = _poser_dans_accountsservice
    pris = poser(cible)
    return True, f"{cible.name} ({len(octets)} octets); AccountsService:{'ok' if pris else 'refus'}"


def _poser_dans_accountsservice(fichier: Path) -> bool:
    """`SetIconFile` sur son propre compte : autorise sans privilege."""
    try:
        import getpass
        utilisateur = getpass.getuser()
        chemin = subprocess.run(
            ["busctl", "--system", "call", "org.freedesktop.Accounts",
             "/org/freedesktop/Accounts", "org.freedesktop.Accounts",
             "FindUserByName", "s", utilisateur],
            capture_output=True, text=True, timeout=15)
        if chemin.returncode != 0:
            return False
        objet = chemin.stdout.strip().split()[-1].strip('"')
        r = subprocess.run(
            ["busctl", "--system", "call", "org.freedesktop.Accounts", objet,
             "org.freedesktop.Accounts.User", "SetIconFile", "s", str(fichier)],
            capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, IndexError) as e:  # noqa: BLE001
        LOG.info("AccountsService n'a pas pris la photo : %s", e)
        return False
