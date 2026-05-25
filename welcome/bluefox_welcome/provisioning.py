"""Read the install-staged policy JSON (bf-policy/v2) and overlay it on the
baked per-tenant config.

The Anaconda %pre device flow stages /var/lib/bluefox-welcome/provisioning.json
(see install/bfos_provision.py). It carries the authenticated user and the
per-user/org *session* block (accent, wallpaper, mounts, PWAs). When present it
is preferred over the baked /usr/share/bluefox/tenant.json defaults: we know who
the user is (pre-fill the wizard) and which branding/session prefs to apply.

Returns {} on a missing/malformed/foreign file so the wizard still runs from
tenant.json alone.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome.provisioning")
PROVISIONING_FILE = Path("/var/lib/bluefox-welcome/provisioning.json")


def load_provisioning(path: Path = PROVISIONING_FILE) -> dict:
    """Load the staged bf-policy/v2 JSON, or {} if absent/invalid."""
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception as e:  # noqa: BLE001
            LOG.warning("failed to load %s: %s", path, e)
            return {}
        if isinstance(data, dict) and data.get("schema") == "bf-policy/v2":
            return data
        LOG.warning("%s present but not bf-policy/v2 ; ignoring", path)
    return {}


def user_login(prov: dict) -> str:
    return (prov.get("user", {}) or {}).get("login", "") if prov else ""


def session(prov: dict) -> dict:
    return (prov.get("session", {}) or {}) if prov else {}


def session_mounts(prov: dict) -> list:
    return session(prov).get("mounts", []) or []


def session_pwas(prov: dict) -> list:
    return session(prov).get("pwas", []) or []


def merge_branding(tenant: dict, prov: dict) -> dict:
    """Overlay the policy session branding onto a copy of the tenant dict.

    accent_color -> branding.palette.accent ; wallpaper_url -> branding.wallpaper_url.
    The input tenant is left unchanged ; a new dict is returned.
    """
    sess = session(prov)
    if not sess:
        return tenant
    merged = dict(tenant)
    branding = dict(merged.get("branding", {}) or {})
    if sess.get("accent_color"):
        palette = dict(branding.get("palette", {}) or {})
        palette["accent"] = sess["accent_color"]
        branding["palette"] = palette
    if sess.get("wallpaper_url"):
        branding["wallpaper_url"] = sess["wallpaper_url"]
    merged["branding"] = branding
    return merged
