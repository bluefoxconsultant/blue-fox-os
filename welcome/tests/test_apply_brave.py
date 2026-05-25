import json
from pathlib import Path

from bluefox_welcome.apply.brave_policy import (
    FLOCCUS_EXT_ID,
    apply_brave_policy,
)


def _policy_path(home: Path) -> Path:
    return (home / ".var" / "app" / "com.brave.Browser"
            / "config" / "BraveSoftware" / "Brave-Browser"
            / "policies" / "managed" / "blue-fox.json")


def test_apply_brave_policy_writes_json(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_brave_policy(tenant_data, home=tmp_home)
    assert ok, msg
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert payload["HomepageLocation"] == "https://nextcloud.bluefoxconsultant.com"
    assert payload["DefaultBrowserSettingEnabled"] is False


def test_apply_brave_policy_includes_floccus(tmp_home: Path, tenant_data: dict):
    apply_brave_policy(tenant_data, home=tmp_home)
    payload = json.loads(_policy_path(tmp_home).read_text())
    forcelist = payload["ExtensionInstallForcelist"]
    assert any(FLOCCUS_EXT_ID in entry for entry in forcelist)


def test_apply_brave_policy_no_nc_url_fails(tmp_home: Path):
    ok, msg = apply_brave_policy({"slug": "bf"}, home=tmp_home)
    assert not ok
    assert "Nextcloud" in msg


# --- bf-policy/v2 session.pwas[] force-install (BFOSI10) --------------------

def test_apply_brave_policy_installs_pwas(tmp_home: Path, tenant_data: dict):
    pwas = [
        {"name": "Talk", "url": "https://nc.example/call", "pinned": True},
        {"name": "Odoo", "url": "https://erp.example", "pinned": False},
    ]
    ok, msg = apply_brave_policy(tenant_data, home=tmp_home, pwas=pwas)
    assert ok, msg
    payload = json.loads(_policy_path(tmp_home).read_text())
    force = payload["WebAppInstallForceList"]
    assert [e["url"] for e in force] == [
        "https://nc.example/call", "https://erp.example"]
    assert all(e["default_launch_container"] == "window" for e in force)
    # Only the pinned PWA is opened at startup.
    assert payload["RestoreOnStartup"] == 4
    assert payload["RestoreOnStartupURLs"] == ["https://nc.example/call"]
    assert "2 PWA" in msg


def test_apply_brave_policy_no_pwas_omits_keys(tmp_home: Path, tenant_data: dict):
    apply_brave_policy(tenant_data, home=tmp_home)
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert "WebAppInstallForceList" not in payload
    assert "RestoreOnStartup" not in payload


def test_apply_brave_policy_pwas_without_pinned_no_startup(tmp_home: Path,
                                                          tenant_data: dict):
    pwas = [{"name": "Odoo", "url": "https://erp.example"}]
    apply_brave_policy(tenant_data, home=tmp_home, pwas=pwas)
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert len(payload["WebAppInstallForceList"]) == 1
    assert "RestoreOnStartup" not in payload
