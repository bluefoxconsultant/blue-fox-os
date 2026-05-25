import json

from bluefox_welcome.provisioning import (
    load_provisioning,
    merge_branding,
    session_mounts,
    session_pwas,
    user_login,
)

POLICY = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "session": {
        "accent_color": "#FF8800",
        "wallpaper_url": "https://example.com/w.jpg",
        "mounts": [{"name": "NC", "remote_path": "/dav", "mount_point": "~/Nextcloud"}],
        "pwas": [{"name": "Talk", "url": "https://nc/talk", "pinned": True}],
    },
}


def test_load_missing_returns_empty(tmp_path):
    assert load_provisioning(tmp_path / "nope.json") == {}


def test_load_valid(tmp_path):
    p = tmp_path / "provisioning.json"
    p.write_text(json.dumps(POLICY))
    assert load_provisioning(p)["user"]["login"] == "olivier@bluefoxconsultant.com"


def test_load_wrong_schema_ignored(tmp_path):
    p = tmp_path / "provisioning.json"
    p.write_text(json.dumps({"schema": "something-else", "user": {"login": "x"}}))
    assert load_provisioning(p) == {}


def test_load_malformed_ignored(tmp_path):
    p = tmp_path / "provisioning.json"
    p.write_text("{not json")
    assert load_provisioning(p) == {}


def test_accessors():
    assert user_login(POLICY) == "olivier@bluefoxconsultant.com"
    assert session_mounts(POLICY)[0]["mount_point"] == "~/Nextcloud"
    assert session_pwas(POLICY)[0]["name"] == "Talk"
    assert user_login({}) == ""
    assert session_mounts({}) == []


def test_merge_branding_overlays_accent_and_wallpaper():
    tenant = {"slug": "bf", "branding": {"palette": {"primary": "#29ABE1",
                                                     "accent": "#29ABE1"}}}
    merged = merge_branding(tenant, POLICY)
    assert merged["branding"]["palette"]["accent"] == "#FF8800"
    assert merged["branding"]["wallpaper_url"] == "https://example.com/w.jpg"
    # original untouched
    assert tenant["branding"]["palette"]["accent"] == "#29ABE1"
    assert "wallpaper_url" not in tenant["branding"]


def test_merge_branding_no_session_returns_input():
    tenant = {"slug": "bf", "branding": {}}
    assert merge_branding(tenant, {}) is tenant
