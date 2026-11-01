"""Tenant config loader for the welcome agent.

Reads /usr/share/bluefox/tenant.json (shipped by the per-tenant image build)
and exposes typed accessors. Returns {} on missing/malformed file so callers
can degrade gracefully — the wizard must still run with default labels.
"""
import json
import logging
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome.tenant")
TENANT_FILE = Path("/usr/share/bluefox/tenant.json")
# Cle USB live generique (#26870, 2026-10-27) : l'organisation choisie a l'ecran
# de connexion depose SON tenant.json (services, habillage) ; il remplace celui
# de l'image, qui designerait les services d'une autre organisation.
ORGANISATION_FILE = Path("/run/bfos-live-organisation/tenant.json")


def _depose_par_root(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    return path.is_file() and not path.is_symlink() and st.st_uid == 0


def load_tenant(path: Path | None = None) -> dict:
    if path is None:
        path = ORGANISATION_FILE if _depose_par_root(ORGANISATION_FILE) else TENANT_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except PermissionError as e:
            # ⚠️ Pas une absence : sans ces URL, ni montage Nextcloud ni coffre
            # Vaultwarden, et le journal ne disait que « URL absente du
            # tenant » (#26870, 2026-10-26 : tenant.json livre en 0600 root).
            LOG.error("%s existe mais n'est pas lisible (%s) : aucune URL de "
                      "service ne sera appliquee. Attendu : 0644.", path, e)
        except Exception as e:
            LOG.warning("failed to load %s: %s", path, e)
    return {}


def get_service_url(tenant: dict, key: str, default: str = "") -> str:
    return tenant.get("services", {}).get(key, {}).get("url", default)


def get_slug(tenant: dict) -> str:
    return tenant.get("slug", "bf")
