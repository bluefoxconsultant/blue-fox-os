import json

from bluefox_welcome.provisioning import (
    browser_extensions,
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
    assert browser_extensions({}) == []
    assert browser_extensions(POLICY) == []
    assert browser_extensions({"browser": {"extensions": [{"id": "x"}]}}) == [{"id": "x"}]


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


# --------------------------------------- copie expurgee et erreurs visibles
def test_la_copie_expurgee_est_preferee(tmp_path):
    """C'est la seule que la session peut lire : elle doit gagner."""
    import json as _json
    from bluefox_welcome.provisioning import load_provisioning
    complete = tmp_path / "provisioning.json"
    complete.write_text(_json.dumps(
        {"schema": "bf-policy/v2", "user": {"login": "complete@x"}}))
    (tmp_path / "policy-public.json").write_text(_json.dumps(
        {"schema": "bf-policy/v2", "user": {"login": "expurgee@x"}}))
    assert load_provisioning(complete)["user"]["login"] == "expurgee@x"


def test_repli_sur_la_complete_quand_la_copie_manque(tmp_path):
    """Un appelant root (bluefox-policy-sync) lit la complete."""
    import json as _json
    from bluefox_welcome.provisioning import load_provisioning
    complete = tmp_path / "provisioning.json"
    complete.write_text(_json.dumps(
        {"schema": "bf-policy/v2", "user": {"login": "complete@x"}}))
    assert load_provisioning(complete)["user"]["login"] == "complete@x"


def test_une_politique_illisible_se_voit(tmp_path, caplog):
    """🔴 LE defaut du 2026-09-11 : l'exception etait avalee en WARNING et
    l'assistant manuel prenait la main sans que rien ne le dise."""
    import json as _json, logging
    from bluefox_welcome.provisioning import load_provisioning
    complete = tmp_path / "provisioning.json"
    complete.write_text(_json.dumps({"schema": "bf-policy/v2"}))
    complete.chmod(0o000)
    try:
        with caplog.at_level(logging.ERROR):
            assert load_provisioning(complete) == {}
        assert any(r.levelno >= logging.ERROR for r in caplog.records), \
            "une politique presente mais illisible doit se journaliser en ERROR"
    finally:
        complete.chmod(0o600)
