import json
from pathlib import Path

from bluefox_welcome.apply.brave_policy import apply_brave_policy

SIGNETS = "nhekfepjhpbbnpjocfhflekddijjfmjm"
BITWARDEN = "nngceckbapebfimnlniiiahkandclblb"
STORE = "https://clients2.google.com/service/update2/crx"
EXTENSIONS = [
    {"id": SIGNETS, "name": "Symbifox Signets",
     "update_url": "https://bf.example/bf_policy/extensions/update.xml",
     "managed": {"instance": "https://bf.example"}},
    {"id": BITWARDEN, "name": "Bitwarden", "update_url": STORE},
]


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


def test_extensions_come_from_the_policy(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_brave_policy(tenant_data, home=tmp_home, extensions=EXTENSIONS)
    assert ok, msg
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert payload["ExtensionInstallForcelist"] == [
        f"{SIGNETS};https://bf.example/bf_policy/extensions/update.xml",
        f"{BITWARDEN};{STORE}"]
    # Seule l'extension qui prend l'instance recoit un stockage gere.
    assert payload["3rdparty"] == {
        "extensions": {SIGNETS: {"instance": "https://bf.example"}}}
    assert "2 extension(s)" in msg


def test_no_extension_without_policy(tmp_home: Path, tenant_data: dict):
    # Floccus n'est plus impose en dur : sans politique, aucune extension.
    apply_brave_policy(tenant_data, home=tmp_home)
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert "ExtensionInstallForcelist" not in payload
    assert "3rdparty" not in payload


def test_malformed_extensions_are_skipped(tmp_home: Path, tenant_data: dict):
    bad = [
        {"id": "pas-un-id", "update_url": STORE},
        {"id": SIGNETS.upper(), "update_url": STORE},
        {"id": BITWARDEN, "update_url": "http://clair.example/update.xml"},
        {"id": BITWARDEN},
        None,
    ]
    apply_brave_policy(tenant_data, home=tmp_home, extensions=bad + EXTENSIONS[1:])
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert payload["ExtensionInstallForcelist"] == [f"{BITWARDEN};{STORE}"]


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


# --- rafraichissement a chaque ouverture de session (#25966) -----------------

def test_refresh_follows_the_current_policy(tmp_home: Path, tenant_data: dict, monkeypatch):
    from bluefox_welcome import main
    prov = {"schema": "bf-policy/v2", "user": {"login": "o@bf.example"},
            "browser": {"extensions": EXTENSIONS[1:]}}
    monkeypatch.setattr(main, "load_provisioning", lambda: prov)
    monkeypatch.setattr(main, "load_tenant", lambda: tenant_data)
    ok, _ = main.refresh_browser_policy(home=tmp_home)
    assert ok
    payload = json.loads(_policy_path(tmp_home).read_text())
    assert payload["ExtensionInstallForcelist"] == [f"{BITWARDEN};{STORE}"]


def test_refresh_without_policy_leaves_brave_alone(tmp_home: Path, tenant_data: dict,
                                                   monkeypatch):
    from bluefox_welcome import main
    target = _policy_path(tmp_home)
    target.parent.mkdir(parents=True)
    target.write_text('{"deja": "la"}')
    monkeypatch.setattr(main, "load_provisioning", lambda: {})
    monkeypatch.setattr(main, "load_tenant", lambda: tenant_data)
    ok, _ = main.refresh_browser_policy(home=tmp_home)
    assert not ok
    assert target.read_text() == '{"deja": "la"}'
