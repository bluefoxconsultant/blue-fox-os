from pathlib import Path

from bluefox_welcome.apply.rclone_mount import apply_rclone_mount


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
