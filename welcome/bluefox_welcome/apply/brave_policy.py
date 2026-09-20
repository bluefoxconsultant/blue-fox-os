"""Push a Chromium managed policy file to the Brave Flatpak user config.

Path: ~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/
      policies/managed/blue-fox.json

Brave installed via Flatpak ignores /etc/brave/policies/ (sandbox), so we
write inside the flatpak user config tree.

V1 policy ships:
- HomepageLocation pointing at the tenant Nextcloud
- DefaultBrowserSettingEnabled=false (don't nag)
- ExtensionInstallForcelist for Floccus (bookmark sync via NC WebDAV)
- WebAppInstallForceList for any bf-policy/v2 session PWAs (BFOSI10) — each
  force-installed as a standalone window; pinned ones also opened at startup.

BraveSync is intentionally left for the user to opt in via the Vaultwarden
seed flow (BFOSP4 v1 = manual seed, v1.1 = self-hosted Brave Sync server).
"""
import json
import logging
from pathlib import Path

from ..tenant import get_service_url

LOG = logging.getLogger("bluefox-welcome.apply.brave_policy")

FLOCCUS_EXT_ID = "fnaicdffflnofjppbagibeoednhnbjhg"
# Chromium "RestoreOnStartup": 4 = open a fixed list of URLs.
_RESTORE_OPEN_URLS = 4


def _policy_path(home: Path) -> Path:
    return (home / ".var" / "app" / "com.brave.Browser"
            / "config" / "BraveSoftware" / "Brave-Browser"
            / "policies" / "managed" / "blue-fox.json")


def _build_policy(nextcloud_url: str, pwas: list[dict] | None = None) -> dict:
    policy = {
        "HomepageLocation": nextcloud_url,
        "HomepageIsNewTabPage": False,
        "DefaultBrowserSettingEnabled": False,
        "PasswordManagerEnabled": False,
        "ExtensionInstallForcelist": [
            f"{FLOCCUS_EXT_ID};https://clients2.google.com/service/update2/crx",
        ],
    }
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
) -> tuple[bool, str]:
    nc_url = get_service_url(tenant, "nextcloud")
    if not nc_url:
        return False, "URL Nextcloud absente du tenant"
    home = home or Path.home()
    target = _policy_path(home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_build_policy(nc_url, pwas), indent=2))
        n = len([p for p in (pwas or []) if p.get("url")])
        suffix = f" + {n} PWA(s)" if n else ""
        return True, f"Brave : policy + Floccus{suffix} poussées"
    except Exception as e:
        LOG.exception("apply_brave_policy failed")
        return False, f"erreur Brave : {e}"
