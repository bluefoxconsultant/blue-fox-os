import os
from pathlib import Path

import pytest

from bluefox_welcome.apply import rclone_mount

from bluefox_welcome.apply.rclone_mount import (
    apply_rclone_mount,
    apply_session_mounts,
)

_OBSCURE_REEL = rclone_mount._obscure_password


@pytest.fixture(autouse=True)
def _rclone_de_remplacement(monkeypatch):
    """Ces essais portent sur ce qu'on ECRIT, pas sur rclone : ils ne doivent
    pas dependre de sa presence sur la machine qui les joue (le runner CI ne
    l'a pas). Le vrai `_obscure_password` reste eprouve plus bas."""
    monkeypatch.setattr(rclone_mount, "_obscure_password", lambda p: "OBSCURCI")


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


def _espionne_os_open(monkeypatch):
    """Enregistre le mode de chaque creation. `write_text` passe par le open du
    module _io en C et ne touche PAS os.open : sur l'ancien code la liste reste
    donc VIDE, et c'est ce qui fait mordre l'assertion."""
    vus = []
    vrai_open = os.open

    def espion(p, flags, mode=0o777, **kw):
        vus.append((str(p), mode))
        return vrai_open(p, flags, mode, **kw)

    monkeypatch.setattr(os, "open", espion)
    return vus


def test_w1_le_mot_de_passe_ne_transite_pas_par_un_fichier_lisible(
        tmp_home: Path, tenant_data: dict, monkeypatch):
    """W1. L'assertion de mode ci-dessus lit l'etat FINAL, et elle etait verte
    sur du code qui creait rclone.conf en 0664 — avec le mot de passe
    d'application Nextcloud dedans — avant de le ramener a 600. Ce qu'on garde
    ici, c'est le mode que le noyau applique A LA CREATION."""
    vus = _espionne_os_open(monkeypatch)

    ok, msg = apply_rclone_mount(
        tenant_data,
        user="olivier@bluefoxconsultant.com",
        password="hunter2",
        home=tmp_home,
        run_systemctl=False,
    )
    assert ok, msg

    crees = [m for p, m in vus if p.endswith("rclone.conf")]
    assert crees, "rclone.conf n'est pas cree par os.open — mode pose apres coup ?"
    assert crees[-1] == 0o600


def test_w1_meme_garde_sur_les_montages_de_session(tmp_home: Path,
                                                   tenant_data: dict,
                                                   monkeypatch):
    """Deux sites ecrivaient ce fichier ; corriger un seul ne vaut rien."""
    vus = _espionne_os_open(monkeypatch)

    apply_session_mounts(
        tenant_data,
        user="olivier@bluefoxconsultant.com",
        password="hunter2",
        mounts=[{"name": "Documents", "mount_point": "~/Documents"}],
        home=tmp_home,
        run_systemctl=False,
    )

    crees = [m for p, m in vus if p.endswith("rclone.conf")]
    assert crees, "rclone.conf n'est pas cree par os.open — mode pose apres coup ?"
    assert crees[-1] == 0o600


def test_rclone_conf_deja_la_en_0644_est_ramene_a_600(tmp_home: Path,
                                                      tenant_data: dict,
                                                      monkeypatch):
    """Une machine ayant tourne avec la version fautive porte encore un
    rclone.conf lisible : O_CREAT ignore son mode, O_TRUNC ne le remet pas, il
    faut le fchmod. Ce test garde ce fchmod-la."""
    conf_dir = tmp_home / ".config" / "rclone"
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf = conf_dir / "rclone.conf"
    conf.write_text("vieux\n")
    os.chmod(conf, 0o644)

    ok, msg = apply_rclone_mount(
        tenant_data,
        user="olivier@bluefoxconsultant.com",
        password="hunter2",
        home=tmp_home,
        run_systemctl=False,
    )

    assert ok, msg
    assert oct(conf.stat().st_mode)[-3:] == "600"
    assert "hunter2" not in conf.read_text()  # obscurci, pas en clair
    assert "[bf-nc]" in conf.read_text()


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


def test_sans_rclone_aucun_mot_de_passe_en_clair(tmp_home: Path, tenant_data: dict,
                                                 monkeypatch):
    """rclone absent : l'application echoue franchement, et le mot de passe
    n'est ecrit nulle part (l'ancien repli l'ecrivait en clair)."""
    monkeypatch.setattr(rclone_mount, "_obscure_password", _OBSCURE_REEL)
    monkeypatch.setattr(rclone_mount.shutil, "which", lambda _nom: None)
    ok, msg = apply_rclone_mount(
        tenant_data, user="olivier@bluefoxconsultant.com", password="hunter2",
        home=tmp_home, run_systemctl=False)
    assert not ok
    assert "rclone" in msg
    for f in tmp_home.rglob("*"):
        if f.is_file():
            assert "hunter2" not in f.read_text(errors="ignore"), f"clair dans {f}"
