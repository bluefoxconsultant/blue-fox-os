"""Shared fixtures for the welcome agent tests."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def tenant_data():
    return {
        "slug": "bf",
        "version": "1.0",
        "image_ref": "ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest",
        "services": {
            "nextcloud": {"url": "https://nextcloud.bluefoxconsultant.com"},
            "authentik": {"url": "https://auth.bluefoxconsultant.com"},
            "vaultwarden": {"url": "https://vault.bluefoxconsultant.com"},
        },
        "branding": {},
    }


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect Path.home() to a tmp dir for the duration of the test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def tenant_file(tmp_path: Path, tenant_data: dict) -> Path:
    """Write a tenant.json under tmp and return its path."""
    p = tmp_path / "tenant.json"
    p.write_text(json.dumps(tenant_data))
    return p
