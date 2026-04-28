import json
from pathlib import Path

from bluefox_welcome.apply.bitwarden_prefs import apply_bitwarden_prefs


def _data_path(home: Path) -> Path:
    return (home / ".var" / "app" / "com.bitwarden.desktop"
            / "config" / "Bitwarden" / "data.json")


def test_apply_bitwarden_prefs_creates_data_json(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_bitwarden_prefs(tenant_data, home=tmp_home)
    assert ok, msg
    target = _data_path(tmp_home)
    assert target.exists()
    payload = json.loads(target.read_text())
    assert payload["global"]["environment"]["environments"]["base"] == \
        "https://vault.bluefoxconsultant.com"


def test_apply_bitwarden_prefs_merges_existing(tmp_home: Path, tenant_data: dict):
    target = _data_path(tmp_home)
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "global": {"locale": "fr-CA"},
        "userIds": {"abc": {"email": "u@x.com"}},
    }))
    apply_bitwarden_prefs(tenant_data, home=tmp_home)
    payload = json.loads(target.read_text())
    assert payload["global"]["locale"] == "fr-CA"
    assert payload["userIds"]["abc"]["email"] == "u@x.com"
    assert payload["global"]["environment"]["environments"]["base"] == \
        "https://vault.bluefoxconsultant.com"


def test_apply_bitwarden_prefs_no_vault_url_fails(tmp_home: Path):
    ok, msg = apply_bitwarden_prefs({"slug": "bf"}, home=tmp_home)
    assert not ok
    assert "Vaultwarden" in msg


def test_apply_bitwarden_prefs_handles_malformed(tmp_home: Path, tenant_data: dict):
    target = _data_path(tmp_home)
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    ok, _ = apply_bitwarden_prefs(tenant_data, home=tmp_home)
    assert ok
    assert target.with_suffix(".json.bak").exists()
