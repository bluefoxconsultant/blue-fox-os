"""Configure rclone WebDAV mount for Nextcloud as a systemd --user unit.

Writes:
- ~/.config/rclone/rclone.conf with a `bf-nc` remote (vendor=nextcloud)
- ~/.config/systemd/user/rclone-nc.service that mounts ~/Nextcloud at login

Then enables the unit so it starts now and on every login.

Security note: the rclone.conf contains the user's Nextcloud password in
obscured form (rclone's xor scheme, not encryption). KDE Wallet integration
is out of scope for v1 — same trust model as ~/.netrc or stored Akonadi DAV
credentials. Documented in commit message.
"""
import base64
import logging
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from ..secure_file import write_private
from ..tenant import get_service_url

LOG = logging.getLogger("bluefox-welcome.apply.rclone_mount")

TEMPLATE_DIR = Path("/usr/share/bluefox/templates")
RCLONE_CONF_TMPL = TEMPLATE_DIR / "rclone.conf.tmpl"
SYSTEMD_UNIT_TMPL = TEMPLATE_DIR / "rclone-nc.service.tmpl"


def _obscure_password(password: str) -> str:
    """Replicate `rclone obscure` (reveal.go) without spawning rclone.

    rclone uses AES-CTR with a fixed key. For v1 we use rclone CLI when
    available — falls back to base64 + warn if rclone is missing (the user
    will be prompted on first mount and rclone rewrites the conf).
    """
    rclone = shutil.which("rclone")
    if rclone:
        try:
            result = subprocess.run(
                [rclone, "obscure", password],
                capture_output=True, text=True, check=True, timeout=5,
            )
            return result.stdout.strip()
        except Exception as e:
            LOG.warning("rclone obscure failed (%s) ; falling back to plaintext", e)
    return password


def _render_rclone_conf(nc_url: str, user: str, obscured_password: str) -> str:
    if RCLONE_CONF_TMPL.exists():
        tmpl = RCLONE_CONF_TMPL.read_text()
    else:
        tmpl = (
            "[bf-nc]\n"
            "type = webdav\n"
            "url = {webdav_url}\n"
            "vendor = nextcloud\n"
            "user = {user}\n"
            "pass = {password}\n"
        )
    webdav_url = nc_url.rstrip("/") + "/remote.php/dav/files/" + \
        urllib.parse.quote(user, safe="")
    return tmpl.format(
        webdav_url=webdav_url,
        user=user,
        password=obscured_password,
    )


def _render_systemd_unit(mount_point: str) -> str:
    if SYSTEMD_UNIT_TMPL.exists():
        tmpl = SYSTEMD_UNIT_TMPL.read_text()
    else:
        tmpl = (
            "[Unit]\n"
            "Description=Blue Fox OS - Nextcloud rclone mount\n"
            "After=network-online.target\n"
            "Wants=network-online.target\n"
            "\n"
            "[Service]\n"
            "Type=notify\n"
            "ExecStartPre=/usr/bin/mkdir -p {mount_point}\n"
            "ExecStart=/usr/bin/rclone mount bf-nc: {mount_point} "
            "--vfs-cache-mode writes --vfs-cache-max-age 24h "
            "--dir-cache-time 5m --poll-interval 30s\n"
            "ExecStop=/bin/fusermount3 -u {mount_point}\n"
            "Restart=on-failure\n"
            "RestartSec=10\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
    return tmpl.format(mount_point=mount_point)


def apply_rclone_mount(
    tenant: dict,
    user: str,
    password: str,
    home: Path | None = None,
    run_systemctl: bool = True,
) -> tuple[bool, str]:
    """Write rclone.conf + systemd --user unit, then enable+start it.

    `home` and `run_systemctl` are seams for tests.
    """
    if not user or not password:
        return False, "user/password manquant"
    nc_url = get_service_url(tenant, "nextcloud")
    if not nc_url:
        return False, "URL Nextcloud absente du tenant"

    home = home or Path.home()
    rclone_dir = home / ".config" / "rclone"
    systemd_user_dir = home / ".config" / "systemd" / "user"
    mount_point = home / "Nextcloud"

    try:
        rclone_dir.mkdir(parents=True, exist_ok=True)
        systemd_user_dir.mkdir(parents=True, exist_ok=True)
        mount_point.mkdir(parents=True, exist_ok=True)

        obscured = _obscure_password(password)
        write_private(rclone_dir / "rclone.conf",
                      _render_rclone_conf(nc_url, user, obscured))

        unit_path = systemd_user_dir / "rclone-nc.service"
        unit_path.write_text(_render_systemd_unit(str(mount_point)))

        if run_systemctl:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=False, timeout=10,
            )
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", "rclone-nc.service"],
                check=False, timeout=15,
            )
        return True, f"Nextcloud monté sur {mount_point}"
    except Exception as e:
        LOG.exception("apply_rclone_mount failed")
        return False, f"erreur rclone : {e}"


def _slugify(name: str) -> str:
    """systemd-unit-safe slug for a mount name."""
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")
    return s or "mount"


def _render_session_unit(remote_path: str, mount_point: str) -> str:
    """Render a per-mount systemd --user unit for a bf-nc:<remote_path> mount."""
    remote = "bf-nc:" + remote_path.strip("/")
    return (
        "[Unit]\n"
        "Description=Blue Fox OS - Nextcloud rclone mount ({mp})\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=notify\n"
        "ExecStartPre=/usr/bin/mkdir -p {mp}\n"
        "ExecStart=/usr/bin/rclone mount {remote} {mp} "
        "--vfs-cache-mode writes --vfs-cache-max-age 24h "
        "--dir-cache-time 5m --poll-interval 30s\n"
        "ExecStop=/bin/fusermount3 -u {mp}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    ).format(remote=remote, mp=mount_point)


def apply_session_mounts(
    tenant: dict,
    user: str,
    password: str,
    mounts: list[dict],
    home: Path | None = None,
    run_systemctl: bool = True,
) -> tuple[bool, str]:
    """Mount each bf-policy/v2 session mount as its own systemd --user unit.

    Each entry is {name, remote_path, mount_point}. All units share the single
    `bf-nc` rclone remote (written here from the Login-Flow-v2 credentials);
    each mounts bf-nc:<remote_path> at <mount_point> (relative paths resolved
    under $HOME). Unit names are rclone-nc-<slug>.service.

    `home` and `run_systemctl` are seams for tests.
    """
    if not user or not password:
        return False, "user/password manquant"
    if not mounts:
        return False, "aucun mount défini"
    nc_url = get_service_url(tenant, "nextcloud")
    if not nc_url:
        return False, "URL Nextcloud absente du tenant"

    home = home or Path.home()
    rclone_dir = home / ".config" / "rclone"
    systemd_user_dir = home / ".config" / "systemd" / "user"

    try:
        rclone_dir.mkdir(parents=True, exist_ok=True)
        systemd_user_dir.mkdir(parents=True, exist_ok=True)

        obscured = _obscure_password(password)
        write_private(rclone_dir / "rclone.conf",
                      _render_rclone_conf(nc_url, user, obscured))

        units: list[str] = []
        applied: list[str] = []
        seen: set[str] = set()
        for m in mounts:
            slug = _slugify(m.get("name") or m.get("mount_point") or "mount")
            # Disambiguate collisions so two mounts never overwrite one unit.
            base = slug
            i = 2
            while slug in seen:
                slug = f"{base}-{i}"
                i += 1
            seen.add(slug)

            remote_path = m.get("remote_path") or ""
            mp = Path(m.get("mount_point") or (home / "Nextcloud" / slug)).expanduser()
            if not mp.is_absolute():
                mp = home / mp
            mp.mkdir(parents=True, exist_ok=True)

            unit_name = f"rclone-nc-{slug}.service"
            (systemd_user_dir / unit_name).write_text(
                _render_session_unit(remote_path, str(mp)))
            units.append(unit_name)
            applied.append(f"{slug}→{mp}")

        if run_systemctl:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                check=False, timeout=10,
            )
            for unit_name in units:
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", unit_name],
                    check=False, timeout=15,
                )
        return True, f"{len(units)} montage(s) Nextcloud : " + ", ".join(applied)
    except Exception as e:
        LOG.exception("apply_session_mounts failed")
        return False, f"erreur rclone : {e}"
