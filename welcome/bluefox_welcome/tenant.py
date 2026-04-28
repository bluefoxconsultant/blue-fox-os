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


def load_tenant(path: Path = TENANT_FILE) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as e:
            LOG.warning("failed to load %s: %s", path, e)
    return {}


def get_service_url(tenant: dict, key: str, default: str = "") -> str:
    return tenant.get("services", {}).get(key, {}).get("url", default)


def get_slug(tenant: dict) -> str:
    return tenant.get("slug", "bf")
