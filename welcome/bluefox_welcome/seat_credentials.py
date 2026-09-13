"""Identifiants Nextcloud deposes par l'installation.

Le %pre echange le jeton de l'operateur (RFC 8693) contre un jeton destine au
fournisseur Nextcloud, s'en sert une fois pour frapper un mot de passe
d'application, et le %post le depose ici. Au premier demarrage,
bluefox-seat-credentials.service en remet la propriete a l'usager du siege —
l'uid vient de l'annuaire et n'existait pas au moment de l'installation.

L'agent d'accueil les consomme : il les lit, les applique, puis EFFACE le
fichier. Un mot de passe d'application n'a pas a survivre a son emploi ; il
vit ensuite dans rclone.conf, sous la garde de l'usager.

Absent, illisible ou incomplet : on rend None et l'agent retombe sur le
parcours SSO par navigateur, qui fonctionne depuis le debut. C'est une etape
en moins, jamais une etape dont tout depend.
"""
import json
import logging
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome.seat_credentials")

FICHIER = Path("/var/lib/bluefox-welcome/nc-credentials.json")


def lire(path: Path | None = None):
    """Retourne (login, mot_de_passe) ou None."""
    path = path or FICHIER
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except PermissionError as exc:
        # ⚠️ Le cas qui ressemble a « pas d'identifiants » sans en etre un :
        # le fichier est la, mais bluefox-seat-credentials.service ne l'a pas
        # remis a cet usager. Le dire, plutot que de retomber en silence sur
        # le SSO comme si rien n'avait ete prepare.
        LOG.error("%s existe mais n'est pas lisible (%s) — les identifiants "
                  "prepares par l'installation NE SERONT PAS utilises ; "
                  "verifier bluefox-seat-credentials.service", path, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        LOG.warning("%s illisible (%s)", path, exc)
        return None

    login = (data.get("login") or "").strip()
    mot_de_passe = (data.get("app_password") or "").strip()
    if not login or not mot_de_passe:
        LOG.warning("%s incomplet : login=%r, mot de passe %s",
                    path, login, "present" if mot_de_passe else "absent")
        return None
    return login, mot_de_passe


def effacer(path: Path | None = None) -> None:
    """Efface le depot une fois consomme. Silencieux : ne pas pouvoir effacer
    ne doit pas couter le montage qui vient de reussir."""
    path = path or FICHIER
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        LOG.warning("%s n'a pas pu etre efface (%s)", path, exc)
