#!/usr/bin/env python3
"""Blue Fox OS install-time policy applier — runs in the Anaconda %post (chroot).

Reads the policy JSON staged by bfos_provision.py (copied into the target by the
post-install no-chroot step) and applies the *install block* to the freshly
installed system:

  - /etc/hostname, /etc/locale.conf, /etc/vconsole.conf, /etc/localtime
  - keyboard: /etc/vconsole.conf (console) AND the graphical layout, which is a
    separate setting entirely — /etc/X11/xorg.conf.d/00-keyboard.conf plus a
    /etc/skel/.config/kxkbrc seed so the account created below starts with it
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
import re
import subprocess
import sys

STAGED_JSON = "/var/lib/bluefox-welcome/provisioning.json"

# A single DNS label or dotted name; must start/end alphanumeric. Anything else
# (spaces, newlines, control or shell chars) is rejected → safe default.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
# POSIX-ish login name; gates the value handed to `useradd` in local mode.
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")
# XKB layout / variant / options tokens, e.g. "ca", "multix", "ca,us",
# "grp:alt_shift_toggle". Deliberately narrow: these values are interpolated
# into an xorg.conf Section and an INI file, both written as root.
_XKB_RE = re.compile(r"^[A-Za-z0-9_,:+()-]*$")


def _ini_safe(value, default="") -> str:
    """Sanitize a policy-supplied value before it lands in an INI-style config
    (sssd.conf): drop CR/LF and control chars so a crafted value can't inject
    extra directives. Defense-in-depth — the policy is first-party over TLS, but
    this file is written as root, so we never trust its contents verbatim."""
    s = str(value if value is not None else default)
    s = s.replace("\r", "").replace("\n", "")
    s = "".join(ch for ch in s if ord(ch) >= 0x20)
    return s.strip()


def render_hostname(policy) -> str:
    name = (policy.get("install", {}).get("hostname") or "").strip()
    if not _HOSTNAME_RE.match(name):
        name = "blue-fox-os"
    return name + "\n"


def render_locale_conf(policy) -> str:
    locale = policy.get("install", {}).get("locale", "fr_CA.UTF-8")
    return f"LANG={locale}\n"


def _xkb(policy) -> tuple:
    """Resolve (layout, variant, options) for the *graphical* session.

    `keymap` alone is not enough: it only ever reaches /etc/vconsole.conf, which
    the console reads and the desktop ignores. The policy may carry an explicit
    x_layout (e.g. keymap 'ca' but layout 'ca' variant 'multix' for the CSA
    Canadian Multilingual layout); when it doesn't, the console keymap is the
    best available guess. Anything failing _XKB_RE degrades to the default
    rather than landing verbatim in a root-written config.
    """
    install = policy.get("install", {})

    def clean(key, default=""):
        value = str(install.get(key) or "").strip()
        return value if _XKB_RE.match(value) else default

    layout = clean("x_layout") or clean("keymap", "ca") or "ca"
    return layout, clean("x_variant"), clean("x_options")


def render_vconsole_conf(policy) -> str:
    """Console keymap + the XKB triple systemd-localed also records here.

    localectl keeps both in this file; writing the XKB keys means a later
    `localectl status` reports what we actually provisioned instead of showing
    the layout as unset.
    """
    keymap = policy.get("install", {}).get("keymap", "ca")
    layout, variant, options = _xkb(policy)
    out = f"KEYMAP={keymap}\nXKBLAYOUT={layout}\n"
    if variant:
        out += f"XKBVARIANT={variant}\n"
    if options:
        out += f"XKBOPTIONS={options}\n"
    return out


def render_x11_keymap_conf(policy) -> str:
    """/etc/X11/xorg.conf.d/00-keyboard.conf — the system-wide graphical layout.

    Canonical location written by `localectl set-x11-keymap`; honoured by X11
    and by Wayland compositors that fall back to the system default.
    """
    layout, variant, options = _xkb(policy)
    lines = [
        "# Written by Blue Fox OS install-time provisioning (bfos_apply.py).",
        "# Change it via the org/user policy in Odoo, not by hand: a re-provision",
        "# rewrites this file.",
        'Section "InputClass"',
        '        Identifier "system-keyboard"',
        '        MatchIsKeyboard "on"',
        f'        Option "XkbLayout" "{layout}"',
    ]
    if variant:
        lines.append(f'        Option "XkbVariant" "{variant}"')
    if options:
        lines.append(f'        Option "XkbOptions" "{options}"')
    lines.append("EndSection")
    return "\n".join(lines) + "\n"


def render_kxkbrc(policy) -> str:
    """/etc/skel/.config/kxkbrc — Plasma's own keyboard config.

    KWin reads kxkbrc first and only consults the system default when the user
    has none, so seeding skel is what makes the layout stick for the account
    created moments later in local mode. `Use=true` is required — without it
    Plasma treats the layout list as inactive.
    """
    layout, variant, options = _xkb(policy)
    out = ("[Layout]\n"
           f"LayoutList={layout}\n"
           f"VariantList={variant}\n"
           "Use=true\n"
           "SwitchMode=Global\n")
    if options:
        out += f"Options={options}\nResetOldOptions=true\n"
    return out


def render_sssd_conf(policy) -> str:
    """Render /etc/sssd/sssd.conf binding the seat login to the Authentik LDAP
    outpost, with offline credential caching gated by the org policy."""
    install = policy.get("install", {})
    login = install.get("login", {})
    pol = policy.get("policies", {})
    offline = pol.get("offline_login", {}) if isinstance(pol, dict) else {}
    user_login = _ini_safe(policy.get("user", {}).get("login", ""))

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
        f"ldap_uri = {_ini_safe(login.get('ldap_uri', ''))}\n"
        f"ldap_search_base = {_ini_safe(login.get('ldap_base_dn', ''))}\n"
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
    record("x11-keymap", lambda: writer(
        "/etc/X11/xorg.conf.d/00-keyboard.conf", render_x11_keymap_conf(policy)))
    # Must precede the useradd below: skel is copied at account creation, so a
    # kxkbrc written afterwards would never reach the user's home.
    record("skel-kxkbrc", lambda: writer(
        "/etc/skel/.config/kxkbrc", render_kxkbrc(policy)))

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
        if username and _USERNAME_RE.match(username):
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
    if not (isinstance(policy, dict) and policy.get("schema") == "bf-policy/v2"
            and isinstance(policy.get("install"), dict)):
        print("[bfos-apply] staged policy missing or not bf-policy/v2 ; skipping",
              file=sys.stderr)
        return 0
    for action, ok, detail in apply(policy):
        print(f"[bfos-apply] {'OK' if ok else 'FAIL'} {action} {detail}".rstrip(),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
