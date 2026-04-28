"""Pre-seed Bitwarden Desktop Flatpak with the Vaultwarden self-hosted URL.

Writes ~/.var/app/com.bitwarden.desktop/config/Bitwarden/data.json with
`global.environment.environments.base` set to the tenant's Vaultwarden URL.

Bitwarden Desktop reads data.json on first launch and pre-fills the
"Self-hosted environment URL" field in the login screen.

If data.json already exists (user launched Bitwarden first), we deep-merge
the environment block instead of clobbering — preserves login state, keys,
and other settings.
"""
import json
import logging
from pathlib import Path

from ..tenant import get_service_url

LOG = logging.getLogger("bluefox-welcome.apply.bitwarden_prefs")


def _bitwarden_data_path(home: Path) -> Path:
    return (home / ".var" / "app" / "com.bitwarden.desktop"
            / "config" / "Bitwarden" / "data.json")


def _merge_environment(existing: dict, vault_url: str) -> dict:
    out = dict(existing)
    g = dict(out.get("global", {}))
    env = dict(g.get("environment", {}))
    envs = dict(env.get("environments", {}))
    envs["base"] = vault_url
    env["environments"] = envs
    g["environment"] = env
    out["global"] = g
    return out


def apply_bitwarden_prefs(
    tenant: dict,
    home: Path | None = None,
) -> tuple[bool, str]:
    vault_url = get_service_url(tenant, "vaultwarden")
    if not vault_url:
        return False, "URL Vaultwarden absente du tenant"
    home = home or Path.home()
    target = _bitwarden_data_path(home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text())
            except Exception as e:
                LOG.warning("data.json malformed (%s) ; backing up + rewriting", e)
                target.with_suffix(".json.bak").write_text(target.read_text())
                existing = {}
        merged = _merge_environment(existing, vault_url)
        target.write_text(json.dumps(merged, indent=2))
        return True, f"Bitwarden Desktop pointé vers {vault_url}"
    except Exception as e:
        LOG.exception("apply_bitwarden_prefs failed")
        return False, f"erreur Bitwarden : {e}"
