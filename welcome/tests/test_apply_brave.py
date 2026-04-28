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
