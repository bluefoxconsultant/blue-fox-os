from pathlib import Path

from bluefox_welcome.apply.rclone_mount import (
    apply_rclone_mount,
    apply_session_mounts,
)


def test_apply_rclone_mount_writes_config(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_rclone_mount(
        tenant_data,
        user="olivier@bluefoxconsultant.com",
        password="hunter2",
        home=tmp_home,
        run_systemctl=False,
    )
    assert ok, msg
    conf = tmp_home / ".config" / "rclone" / "rclone.conf"
    assert conf.exists()
    text = conf.read_text()
    assert "[bf-nc]" in text
    assert "vendor = nextcloud" in text
    assert "olivier%40bluefoxconsultant.com" in text  # url-encoded user in webdav path
    assert oct(conf.stat().st_mode)[-3:] == "600"


def test_apply_rclone_mount_writes_systemd_unit(tmp_home: Path, tenant_data: dict):
    apply_rclone_mount(
        tenant_data,
        user="olivier",
        password="x",
        home=tmp_home,
        run_systemctl=False,
    )
    unit = tmp_home / ".config" / "systemd" / "user" / "rclone-nc.service"
    assert unit.exists()
    text = unit.read_text()
    assert "rclone mount bf-nc:" in text
    assert str(tmp_home / "Nextcloud") in text


def test_apply_rclone_mount_creates_mount_point(tmp_home: Path, tenant_data: dict):
    apply_rclone_mount(
        tenant_data, user="u", password="p",
        home=tmp_home, run_systemctl=False,
    )
    assert (tmp_home / "Nextcloud").is_dir()


def test_apply_rclone_mount_missing_password_fails(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_rclone_mount(
        tenant_data, user="u", password="",
        home=tmp_home, run_systemctl=False,
    )
    assert not ok
    assert "manquant" in msg


def test_apply_rclone_mount_missing_nc_url_fails(tmp_home: Path):
    ok, msg = apply_rclone_mount(
        {"slug": "bf", "services": {}},
        user="u", password="p",
        home=tmp_home, run_systemctl=False,
    )
    assert not ok
    assert "Nextcloud" in msg


# --- bf-policy/v2 session.mounts[] multi-mount (BFOSI10) --------------------

def test_apply_session_mounts_writes_per_mount_units(tmp_home: Path, tenant_data: dict):
    mounts = [
        {"name": "Projets", "remote_path": "Projets",
         "mount_point": str(tmp_home / "Projets")},
        {"name": "Archives 2024", "remote_path": "Arch/2024",
         "mount_point": str(tmp_home / "Archives")},
    ]
    ok, msg = apply_session_mounts(
        tenant_data, user="olivier", password="p",
        mounts=mounts, home=tmp_home, run_systemctl=False,
    )
    assert ok, msg
    unit_dir = tmp_home / ".config" / "systemd" / "user"
    u1 = unit_dir / "rclone-nc-projets.service"
    u2 = unit_dir / "rclone-nc-archives-2024.service"
    assert u1.exists() and u2.exists()
    assert "rclone mount bf-nc:Projets" in u1.read_text()
    assert "rclone mount bf-nc:Arch/2024" in u2.read_text()
    assert str(tmp_home / "Projets") in u1.read_text()
    # Mount points created, single shared remote written once.
    assert (tmp_home / "Projets").is_dir()
    assert "[bf-nc]" in (tmp_home / ".config" / "rclone" / "rclone.conf").read_text()
    assert "2 montage" in msg


def test_apply_session_mounts_dedups_name_collision(tmp_home: Path, tenant_data: dict):
    mounts = [
        {"name": "Projets", "remote_path": "A", "mount_point": str(tmp_home / "A")},
        {"name": "projets!", "remote_path": "B", "mount_point": str(tmp_home / "B")},
    ]
    ok, _ = apply_session_mounts(
        tenant_data, user="u", password="p",
        mounts=mounts, home=tmp_home, run_systemctl=False,
    )
    assert ok
    unit_dir = tmp_home / ".config" / "systemd" / "user"
    assert (unit_dir / "rclone-nc-projets.service").exists()
    assert (unit_dir / "rclone-nc-projets-2.service").exists()


def test_apply_session_mounts_relative_mount_point_under_home(tmp_home: Path,
                                                              tenant_data: dict):
    mounts = [{"name": "Docs", "remote_path": "Docs", "mount_point": "Docs"}]
    ok, _ = apply_session_mounts(
        tenant_data, user="u", password="p",
        mounts=mounts, home=tmp_home, run_systemctl=False,
    )
    assert ok
    unit = (tmp_home / ".config" / "systemd" / "user"
            / "rclone-nc-docs.service").read_text()
    assert str(tmp_home / "Docs") in unit


def test_apply_session_mounts_empty_fails(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_session_mounts(
        tenant_data, user="u", password="p",
        mounts=[], home=tmp_home, run_systemctl=False,
    )
    assert not ok
    assert "aucun mount" in msg


def test_apply_session_mounts_missing_password_fails(tmp_home: Path, tenant_data: dict):
    ok, msg = apply_session_mounts(
        tenant_data, user="u", password="",
        mounts=[{"name": "x"}], home=tmp_home, run_systemctl=False,
    )
    assert not ok
    assert "manquant" in msg
