#!/usr/bin/env python3
"""Blue Fox OS install-time policy applier — runs in the Anaconda %post (chroot).

Reads the policy JSON staged by bfos_provision.py (copied into the target by the
post-install no-chroot step) and applies the *install block* to the freshly
installed system:

  - /etc/hostname, /etc/locale.conf, /etc/vconsole.conf, /etc/localtime
  - root account locked/enabled per policy
  - seat login: when login.mode == 'sssd', write /etc/sssd/sssd.conf pointed at
    the Authentik LDAP outpost, enable offline credential caching per the policy,
    and select the sssd profile with home-dir creation (login synced to Authentik)
  - when login.mode == 'local', create the provisioning user as a local account

The *session block* (mounts, PWAs, theme) is left in
/var/lib/bluefox-welcome/provisioning.json for the firstboot welcome agent.

stdlib-only. Render functions are pure (return file contents) so they unit-test
without a target system; apply() performs the side effects behind injectable seams.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

STAGED_JSON = "/var/lib/bluefox-welcome/provisioning.json"


def render_hostname(policy) -> str:
    return (policy.get("install", {}).get("hostname") or "blue-fox-os").strip() + "\n"


def render_locale_conf(policy) -> str:
    locale = policy.get("install", {}).get("locale", "fr_CA.UTF-8")
    return f"LANG={locale}\n"


def render_vconsole_conf(policy) -> str:
    keymap = policy.get("install", {}).get("keymap", "ca")
    return f"KEYMAP={keymap}\n"


def render_sssd_conf(policy) -> str:
    """Render /etc/sssd/sssd.conf binding the seat login to the Authentik LDAP
    outpost, with offline credential caching gated by the org policy."""
    install = policy.get("install", {})
    login = install.get("login", {})
    pol = policy.get("policies", {})
    offline = pol.get("offline_login", {}) if isinstance(pol, dict) else {}
    user_login = policy.get("user", {}).get("login", "")

    cache = "true" if offline.get("enabled", True) else "false"
    expire = int(offline.get("max_offline_days", 0) or 0)
    allow_line = (f"simple_allow_users = {user_login}\n"
                  if user_login else "")
    return (
        "[sssd]\n"
        "config_file_version = 2\n"
        "services = nss, pam\n"
        "domains = bluefox\n"
        "\n"
        "[domain/bluefox]\n"
        "id_provider = ldap\n"
        "auth_provider = ldap\n"
        "access_provider = simple\n"
        f"ldap_uri = {login.get('ldap_uri', '')}\n"
        f"ldap_search_base = {login.get('ldap_base_dn', '')}\n"
        "ldap_schema = rfc2307bis\n"
        "ldap_user_object_class = user\n"
        "ldap_group_object_class = group\n"
        "ldap_id_use_start_tls = true\n"
        f"cache_credentials = {cache}\n"
        "enumerate = false\n"
        f"{allow_line}"
        "\n"
        "[pam]\n"
        f"offline_credentials_expiration = {expire}\n"
    )


def apply(policy, root="/", run=subprocess.run, writer=None):
    """Apply the install block to the target rooted at `root`.

    Returns a list of (action, ok, detail) tuples. Best-effort: a failed action
    is recorded but does not abort the others (the install must still complete).
    """
    writer = writer or _default_writer(root)
    install = policy.get("install", {})
    login = install.get("login", {})
    results = []

    def record(action, fn):
        try:
            fn()
            results.append((action, True, ""))
        except Exception as exc:  # noqa: BLE001
            results.append((action, False, str(exc)))

    record("hostname", lambda: writer("/etc/hostname", render_hostname(policy)))
    record("locale", lambda: writer("/etc/locale.conf", render_locale_conf(policy)))
    record("vconsole", lambda: writer("/etc/vconsole.conf", render_vconsole_conf(policy)))

    tz = install.get("timezone")
    if tz:
        record("timezone", lambda: run(
            ["ln", "-sf", f"/usr/share/zoneinfo/{tz}",
             os.path.join(root, "etc/localtime")], check=False))

    if install.get("root") == "locked":
        record("root-lock", lambda: run(
            _chroot(root, ["passwd", "-l", "root"]), check=False))

    if login.get("mode") == "sssd":
        record("sssd-conf", lambda: writer(
            "/etc/sssd/sssd.conf", render_sssd_conf(policy), mode=0o600))
        record("sssd-profile", lambda: run(
            _chroot(root, ["authselect", "select", "sssd", "with-mkhomedir",
                           "--force"]), check=False))
        record("sssd-enable", lambda: run(
            _chroot(root, ["systemctl", "enable", "sssd.service",
                           "oddjobd.service"]), check=False))
    elif login.get("mode") == "local":
        user_login = policy.get("user", {}).get("login", "")
        username = user_login.split("@")[0] if user_login else ""
        if username:
            record("local-user", lambda: run(
                _chroot(root, ["useradd", "-m", "-G", "wheel", username]),
                check=False))

    return results


def _chroot(root, argv):
    return argv if root == "/" else ["chroot", root] + argv


def _default_writer(root):
    def write(path, content, mode=None):
        full = os.path.join(root, path.lstrip("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
        if mode is not None:
            os.chmod(full, mode)
    return write


def main(argv=None):
    path = (argv[1] if argv and len(argv) > 1 else STAGED_JSON)
    try:
        with open(path) as fh:
            policy = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[bfos-apply] cannot read {path}: {exc}", file=sys.stderr)
        return 0  # do not fail the install
    for action, ok, detail in apply(policy):
        print(f"[bfos-apply] {'OK' if ok else 'FAIL'} {action} {detail}".rstrip(),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
