import json
import os

import bfos_apply as ba

POLICY = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "install": {
        "locale": "fr_CA.UTF-8",
        "keymap": "ca",
        "timezone": "America/Montreal",
        "hostname": "bf-olivier",
        "root": "locked",
        "login": {"mode": "sssd",
                  "ldap_uri": "ldaps://ldap.example.com:636",
                  "ldap_base_dn": "dc=ldap,dc=goauthentik,dc=io"},
    },
    "policies": {"offline_login": {"enabled": True, "max_offline_days": 7},
                 "mfa_required": True, "auto_lock_minutes": 15},
    "session": {"accent_color": "#29ABE1", "mounts": [], "pwas": []},
}


def test_render_hostname():
    assert ba.render_hostname(POLICY) == "bf-olivier\n"


def test_render_locale_conf():
    assert ba.render_locale_conf(POLICY) == "LANG=fr_CA.UTF-8\n"


def test_render_vconsole_conf():
    assert ba.render_vconsole_conf(POLICY) == "KEYMAP=ca\n"


def test_render_sssd_conf_cache_on():
    conf = ba.render_sssd_conf(POLICY)
    assert "ldap_uri = ldaps://ldap.example.com:636" in conf
    assert "ldap_search_base = dc=ldap,dc=goauthentik,dc=io" in conf
    assert "cache_credentials = true" in conf
    assert "offline_credentials_expiration = 7" in conf
    assert "simple_allow_users = olivier@bluefoxconsultant.com" in conf


def test_render_sssd_conf_cache_off():
    p = {**POLICY, "policies": {"offline_login": {"enabled": False,
                                                  "max_offline_days": 0}}}
    conf = ba.render_sssd_conf(p)
    assert "cache_credentials = false" in conf
    assert "offline_credentials_expiration = 0" in conf


def test_apply_writes_files_and_enables_sssd(tmp_path):
    calls = []

    def fake_run(argv, check=False):
        calls.append(argv)

    results = ba.apply(POLICY, root=str(tmp_path), run=fake_run)
    actions = {a: ok for a, ok, _ in results}
    assert actions["hostname"] and actions["locale"] and actions["sssd-conf"]

    assert (tmp_path / "etc/hostname").read_text() == "bf-olivier\n"
    assert (tmp_path / "etc/locale.conf").read_text() == "LANG=fr_CA.UTF-8\n"
    sssd = tmp_path / "etc/sssd/sssd.conf"
    assert "ldap_uri" in sssd.read_text()
    assert oct(sssd.stat().st_mode)[-3:] == "600"

    flat = [" ".join(c) for c in calls]
    assert any("authselect select sssd with-mkhomedir" in c for c in flat)
    assert any("systemctl enable sssd.service" in c for c in flat)
    assert any("passwd -l root" in c for c in flat)
    assert any("ln -sf /usr/share/zoneinfo/America/Montreal" in c for c in flat)


def test_apply_local_mode_creates_user(tmp_path):
    calls = []
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: calls.append(argv))
    flat = [" ".join(c) for c in calls]
    assert any("useradd -m -G wheel olivier" in c for c in flat)


# ------------------------------------------------------------ security hardening
def test_render_sssd_conf_sanitizes_injection():
    """A newline in any policy value must not inject extra sssd directives."""
    p = {**POLICY,
         "user": {"login": "olivier@bf.com\nrogue_user = 1"},
         "install": {**POLICY["install"],
                     "login": {"mode": "sssd",
                               "ldap_uri": "ldaps://ok\nrogue_uri = 1",
                               "ldap_base_dn": "dc=x\nrogue_dn = 1"}}}
    lines = ba.render_sssd_conf(p).splitlines()
    assert "rogue_uri = 1" not in lines
    assert "rogue_dn = 1" not in lines
    assert "rogue_user = 1" not in lines
    # the sanitized value is collapsed onto its own legitimate directive line
    assert "ldap_uri = ldaps://okrogue_uri = 1" in lines


def test_render_hostname_rejects_invalid():
    p = {**POLICY, "install": {**POLICY["install"], "hostname": "bad name\nrm -rf"}}
    assert ba.render_hostname(p) == "blue-fox-os\n"


def test_render_hostname_accepts_dotted_label():
    p = {**POLICY, "install": {**POLICY["install"], "hostname": "bf-first.last"}}
    assert ba.render_hostname(p) == "bf-first.last\n"


def test_apply_local_mode_rejects_bad_username(tmp_path):
    calls = []
    p = {**POLICY, "user": {"login": "Bad Name@x"},
         "install": {**POLICY["install"], "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path),
             run=lambda argv, check=False: calls.append(argv))
    assert not any("useradd" in " ".join(c) for c in calls)


def test_main_skips_non_v2_policy(tmp_path, capsys):
    bad = tmp_path / "p.json"
    bad.write_text(json.dumps({"schema": "nope", "install": {}}))
    assert ba.main(["bfos_apply", str(bad)]) == 0
    assert "skipping" in capsys.readouterr().err
