"""Push a Chromium managed policy file to the Brave Flatpak user config.

Path: ~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/
      policies/managed/blue-fox.json

Brave installed via Flatpak ignores /etc/brave/policies/ (sandbox), so we
write inside the flatpak user config tree.

V1 policy ships:
- HomepageLocation pointing at the tenant Nextcloud
- DefaultBrowserSettingEnabled=false (don't nag)
- ExtensionInstallForcelist from the policy's `browser.extensions` (bf_policy
  catalogue, #25966) : Symbifox extensions served signed by the tenant's own
  Odoo, store extensions (Bitwarden…) from the Chrome Web Store. Floccus used
  to be hardcoded here ; bookmarks now live in Odoo (bf_bookmarks).
- `3rdparty` managed storage for extensions that take the instance address
  (Symbifox Signets), so they come pre-configured.
- WebAppInstallForceList for any bf-policy/v2 session PWAs (BFOSI10) — each
  force-installed as a standalone window; pinned ones also opened at startup.

Brave Sync is not used (BFOSP4 superseded 2026-10-02) : bookmarks and
extensions follow the person through the Symbifox extensions above (#25966).
"""
import json
import logging
import re
from pathlib import Path

from ..tenant import get_service_url

LOG = logging.getLogger("bluefox-welcome.apply.brave_policy")

# Forme d'un identifiant Chromium : 32 lettres de a a p.
_EXT_ID_RE = re.compile(r"^[a-p]{32}$")
# Chromium "RestoreOnStartup": 4 = open a fixed list of URLs.
_RESTORE_OPEN_URLS = 4


def _policy_path(home: Path) -> Path:
    return (home / ".var" / "app" / "com.brave.Browser"
            / "config" / "BraveSoftware" / "Brave-Browser"
            / "policies" / "managed" / "blue-fox.json")


def _valid_extensions(extensions: list[dict] | None) -> list[dict]:
    """Garde les entrees bien formees : un id Chromium et une adresse https.

    Une entree mal formee est ecartee et journalisee ici, jamais corrigee :
    c'est dans le journal de l'agent qu'on la retrouve, plutot que dans
    brave://policy d'un poste.
    """
    ok = []
    for ext in extensions or []:
        ext_id = (ext or {}).get("id", "")
        url = (ext or {}).get("update_url", "")
        if _EXT_ID_RE.match(ext_id or "") and str(url).startswith("https://"):
            ok.append(ext)
        else:
            LOG.warning("extension ignoree (forme invalide) : %r", ext)
    return ok


def _build_policy(nextcloud_url: str, pwas: list[dict] | None = None,
                  extensions: list[dict] | None = None) -> dict:
    policy = {
        "HomepageLocation": nextcloud_url,
        "HomepageIsNewTabPage": False,
        "DefaultBrowserSettingEnabled": False,
        "PasswordManagerEnabled": False,
    }
    extensions = _valid_extensions(extensions)
    if extensions:
        policy["ExtensionInstallForcelist"] = [
            f"{e['id']};{e['update_url']}" for e in extensions]
        managed = {e["id"]: e["managed"] for e in extensions
                   if isinstance(e.get("managed"), dict) and e["managed"]}
        if managed:
            policy["3rdparty"] = {"extensions": managed}
    pwas = pwas or []
    force_list = [
        {"url": p["url"], "default_launch_container": "window"}
        for p in pwas if p.get("url")
    ]
    if force_list:
        policy["WebAppInstallForceList"] = force_list
    pinned = [p["url"] for p in pwas if p.get("url") and p.get("pinned")]
    if pinned:
        policy["RestoreOnStartup"] = _RESTORE_OPEN_URLS
        policy["RestoreOnStartupURLs"] = pinned
    return policy


def apply_brave_policy(
    tenant: dict,
    home: Path | None = None,
    pwas: list[dict] | None = None,
    extensions: list[dict] | None = None,
) -> tuple[bool, str]:
    nc_url = get_service_url(tenant, "nextcloud")
    if not nc_url:
        return False, "URL Nextcloud absente du tenant"
    home = home or Path.home()
    target = _policy_path(home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        policy = _build_policy(nc_url, pwas, extensions)
        target.write_text(json.dumps(policy, indent=2))
        n = len([p for p in (pwas or []) if p.get("url")])
        n_ext = len(policy.get("ExtensionInstallForcelist", []))
        suffix = (f" + {n_ext} extension(s)" if n_ext else "") + (f" + {n} PWA(s)" if n else "")
        return True, f"Brave : policy{suffix} poussée"
    except Exception as e:
        LOG.exception("apply_brave_policy failed")
        return False, f"erreur Brave : {e}"
