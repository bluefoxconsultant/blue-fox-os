"""Zone Flatpak de la cle live (#26870, 2026-10-27)."""
import datetime
import json
import os
import types

import pytest

from bfos_live import session as se
from bfos_live import zone_flatpak as zf

IMAGE = {"date": datetime.date(2026, 10, 27), "empreinte": "0123456789abcdef"}
GPT = {"genre": "gpt", "disque": "/dev/sdz", "source": "/dev/sdz1"}
VENTOY = {"genre": "ventoy", "disque": "/dev/sdz", "majmin": "8:1", "debut": 2048, "taille": 999}


class R:
    def __init__(self, rc=0, out="", err=b""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class Run:
    """Commandes simulees, en ordre ; `echecs` : debuts de commande qui echouent (une fois chacun
    si `une_fois`)."""

    def __init__(self, partition=("/dev/sdz3", "ext4"), echecs=(), une_fois=False, stat="ef53",
                 blkid="exfat", findmnt=""):
        self.partition, self.echecs, self.une_fois = partition, list(echecs), une_fois
        self.stat, self.blkid, self.findmnt = stat, blkid, findmnt
        self.appels = []

    def __call__(self, argv, **kw):
        ligne = " ".join(argv)
        self.appels.append(ligne)
        for e in list(self.echecs):
            if ligne.startswith(e):
                if self.une_fois:
                    self.echecs.remove(e)
                return R(1, "", b"refus simule")
        if argv[0] == "lsblk":
            enfants = [] if self.partition[0] is None else [
                {"name": self.partition[0], "type": "part", "partlabel": "BFOS_PROFILS",
                 "fstype": self.partition[1]}]
            return R(0, json.dumps({"blockdevices": [{"name": "/dev/sdz", "children": enfants}]}))
        if argv[0] == "stat":
            return R(0, self.stat + "\n")
        if argv[0] == "blkid":
            return R(0, self.blkid + "\n")
        if argv[0] == "findmnt":
            return R(0 if self.findmnt else 1, self.findmnt)
        return R(0, "")

    def mounts(self):
        return [a for a in self.appels if a.startswith("mount ")]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(zf, "TAILLE", 4 * se.MIO)
    return {"state_dir": str(tmp_path / "etat"), "profile_reserve_mb": 1}


def large(_p):
    return types.SimpleNamespace(f_bavail=1 << 20, f_frsize=4096, f_blocks=1 << 21)


def jamais_monte(_p):
    return False


def preparer(cfg, run, **kw):
    kw.setdefault("support", GPT)
    kw.setdefault("image", IMAGE)
    return zf.preparer(cfg, run=run, statvfs=kw.pop("statvfs", large), monte=jamais_monte, **kw)


def test_lire_image(tmp_path):
    f = tmp_path / "i.txt"
    f.write_text("date=2026-10-27\nempreinte=0123456789abcdef\n")
    assert zf.lire_image(str(f)) == IMAGE
    f.write_text("date=hier\nempreinte=x\n")
    assert zf.lire_image(str(f)) == {}
    assert zf.lire_image(str(tmp_path / "absent")) == {}


def test_sans_empreinte_rien(cfg):
    run = Run()
    assert zf.preparer(cfg, run=run, image={}, monte=jamais_monte) is False
    assert run.appels == []


def test_cle_sans_espace_garde_l_iso(cfg):
    run = Run(partition=(None, None))
    assert preparer(cfg, run) is False
    assert run.mounts() == []


def fond(cfg, run, **kw):
    kw.setdefault("support", GPT)
    kw.setdefault("image", IMAGE)
    return zf.preparer_en_fond(cfg, run=run, statvfs=kw.pop("statvfs", large),
                               monte=jamais_monte, **kw)


def rep_de(cfg, support=GPT):
    p = zf.points(cfg)
    return os.path.join(p["espace"], se.VENTOY_DOSSIER) if support is VENTOY else p["espace"]


def test_demarrage_ne_cree_ni_ne_formate_jamais(cfg):
    run = Run()
    assert preparer(cfg, run) is False
    assert not [a for a in run.appels if a.startswith("mkfs")]
    assert not os.path.exists(os.path.join(rep_de(cfg), zf.FICHIER))
    assert not [a for a in run.mounts() if "loop" in a or "overlay" in a]


def test_en_fond_cree_formate_et_marque_prete(cfg):
    run = Run()
    assert fond(cfg, run) == "preparee"
    rep = rep_de(cfg)
    assert os.path.getsize(os.path.join(rep, zf.FICHIER)) == zf.TAILLE
    mkfs = [a for a in run.appels if a.startswith("mkfs.ext4")]
    assert len(mkfs) == 1 and "nodiscard" in mkfs[0] and "lazy_itable_init=0" in mkfs[0]
    prep = os.path.join(zf.points(cfg)["base"], "preparation")
    assert open(os.path.join(prep, zf.TAMPON)).read().strip() == IMAGE["empreinte"]
    assert open(os.path.join(rep, zf.PRETE)).read().strip() == IMAGE["empreinte"]
    assert f"umount {prep}" in run.appels
    assert fond(cfg, Run()) == "prete", "deja prete : rien a refaire"


def _prete(cfg, support=GPT):
    """Zone prete comme apres preparer_en_fond, et son contenu la ou le demarrage la monte."""
    fond(cfg, Run(stat=se.MAGIC_EXFAT if support is VENTOY else "ef53"), support=support)
    p = zf.points(cfg)
    os.makedirs(p["disque"], exist_ok=True)
    open(os.path.join(p["disque"], zf.TAMPON), "w").write(IMAGE["empreinte"] + "\n")


def test_demarrage_active_une_zone_prete(cfg):
    _prete(cfg)
    run = Run()
    assert preparer(cfg, run) is True
    p = zf.points(cfg)
    fichier = os.path.join(rep_de(cfg), zf.FICHIER)
    m = run.mounts()
    assert m[0].startswith("mount -t ext4 -o nosuid,nodev,noexec /dev/sdz3 ")
    assert m[1] == f"mount -t ext4 -o loop,nosuid,nodev,noatime {fichier} {p['disque']}"
    assert m[2] == (f"mount -t overlay bfos-flatpak -o nosuid,nodev,lowerdir=/var/lib/flatpak,"
                    f"upperdir={p['disque']}/haut,workdir={p['disque']}/travail /var/lib/flatpak")
    assert not [a for a in run.appels if a.startswith("mkfs")]
    assert any(a.startswith("chcon --reference=/var/lib/flatpak") for a in run.appels)
    assert json.load(open(p["active"]))["rep"] == rep_de(cfg)


def test_autre_iso_attend_l_arriere_plan(cfg):
    _prete(cfg)
    autre = dict(IMAGE, empreinte="fedcba9876543210")
    run = Run()
    assert preparer(cfg, run, image=autre) is False
    assert not [a for a in run.appels if a.startswith("mkfs")]
    run = Run()
    assert fond(cfg, run, image=autre) == "preparee"
    assert len([a for a in run.appels if a.startswith("mkfs")]) == 1


def test_zone_a_refaire_attend_l_arriere_plan(cfg):
    _prete(cfg)
    open(os.path.join(rep_de(cfg), zf.A_REFAIRE), "w").write("pleine\n")
    assert preparer(cfg, Run()) is False
    run = Run()
    assert fond(cfg, run) == "preparee"
    assert not os.path.exists(os.path.join(rep_de(cfg), zf.A_REFAIRE))


def test_zone_illisible_marquee_puis_iso(cfg):
    _prete(cfg)
    run = Run(echecs=["mount -t ext4 -o loop"])
    assert preparer(cfg, run) is False
    assert os.path.exists(os.path.join(rep_de(cfg), zf.A_REFAIRE))
    assert not [a for a in run.appels if a.startswith("mkfs")]


def test_tampon_different_marque_a_refaire_et_demonte(cfg):
    _prete(cfg)
    open(os.path.join(zf.points(cfg)["disque"], zf.TAMPON), "w").write("autre\n")
    run = Run()
    assert preparer(cfg, run) is False
    assert os.path.exists(os.path.join(rep_de(cfg), zf.A_REFAIRE))
    assert run.appels[-1] == f"umount {zf.points(cfg)['disque']}"


def test_partition_non_propre_pas_de_zone(cfg):
    _prete(cfg)

    class Sale(Run):
        def __call__(self, argv, **kw):
            if argv[:2] == ["e2fsck", "-p"] and argv[2].startswith("/dev/"):
                self.appels.append(" ".join(argv))
                return R(4, "", b"")
            return super().__call__(argv, **kw)
    run = Sale()
    assert preparer(cfg, run) is False
    assert not [a for a in run.mounts() if "loop" in a]


def test_reservation_ext4_remise_a_chaque_demarrage(cfg, monkeypatch):
    _prete(cfg)
    vus = []
    vrai = os.posix_fallocate
    monkeypatch.setattr(os, "posix_fallocate", lambda fd, a, n: (vus.append((a, n)), vrai(fd, a, n)))
    reel = os.stat

    def creuse(chemin, *a, **k):
        st = reel(chemin, *a, **k)
        if str(chemin).endswith(zf.FICHIER):
            return types.SimpleNamespace(st_blocks=0, st_size=st.st_size, st_mode=st.st_mode)
        return st
    monkeypatch.setattr(os, "stat", creuse)
    assert preparer(cfg, Run()) is True
    assert vus == [(0, zf.TAILLE)]


def test_reservation_impossible_ne_prend_rien(cfg, monkeypatch):
    _prete(cfg)
    reel = os.stat
    def creuse(c, *a, **k):
        st = reel(c, *a, **k)
        if str(c).endswith(zf.FICHIER):
            return types.SimpleNamespace(st_blocks=0, st_size=st.st_size, st_mode=st.st_mode)
        return st
    monkeypatch.setattr(os, "stat", creuse)
    monkeypatch.setattr(os, "posix_fallocate", lambda *a: (_ for _ in ()).throw(AssertionError))

    def petit(_p):
        return types.SimpleNamespace(f_bavail=10, f_frsize=4096, f_blocks=100)
    run = Run()
    assert preparer(cfg, run, statvfs=petit) is False
    assert not [a for a in run.mounts() if "loop" in a]


def test_place_insuffisante_pas_de_zone(cfg):
    def petit(_p):
        return types.SimpleNamespace(f_bavail=10, f_frsize=4096, f_blocks=100)
    run = Run()
    assert fond(cfg, run, statvfs=petit) is None
    assert not os.path.exists(os.path.join(rep_de(cfg), zf.FICHIER))
    assert not [a for a in run.appels if a.startswith("mkfs")]


def test_lien_a_la_place_de_la_zone_refuse(cfg, tmp_path):
    p = zf.points(cfg)
    os.makedirs(p["espace"])
    (tmp_path / "ailleurs").write_bytes(b"x" * zf.TAILLE)
    os.symlink(tmp_path / "ailleurs", os.path.join(p["espace"], zf.FICHIER))
    open(os.path.join(p["espace"], zf.PRETE), "w").write(IMAGE["empreinte"])
    run = Run()
    assert preparer(cfg, run) is False
    assert fond(cfg, Run()) is None
    assert not [a for a in run.mounts() if "loop" in a or "overlay" in a]


def test_ventoy_sans_dossier_de_profils_garde_l_iso(cfg):
    assert preparer(cfg, Run(), support=VENTOY) is False
    assert fond(cfg, Run(), support=VENTOY) is None


def test_ventoy_exfat_zone_en_fond_puis_au_demarrage(cfg):
    p = zf.points(cfg)
    os.makedirs(os.path.join(p["espace"], se.VENTOY_DOSSIER))
    run = Run(stat=se.MAGIC_EXFAT)
    assert fond(cfg, run, support=VENTOY) == "preparee"
    assert os.path.getsize(os.path.join(rep_de(cfg, VENTOY), zf.FICHIER)) == zf.TAILLE
    assert run.mounts()[0].startswith("mount -t exfat -o nosuid,nodev,noexec,umask=077 ")
    os.makedirs(p["disque"], exist_ok=True)
    open(os.path.join(p["disque"], zf.TAMPON), "w").write(IMAGE["empreinte"])
    run = Run(stat=se.MAGIC_EXFAT)
    assert preparer(cfg, run, support=VENTOY) is True
    assert not [a for a in run.appels if a.startswith(("mkfs", "e2fsck -p /dev"))]


def test_echec_de_la_superposition_demonte_la_zone(cfg):
    _prete(cfg)
    run = Run(echecs=["mount -t overlay"])
    assert preparer(cfg, run) is False
    assert run.appels[-1] == f"umount {zf.points(cfg)['disque']}"


def test_signatures_exigees(tmp_path):
    f = tmp_path / "config"
    f.write_text('[core]\nmode=bare-user-only\n\n[remote "flathub"]\nurl=https://dl.flathub.org/repo/\n'
                 'gpg-verify=true\ngpg-verify-summary=true\n')
    assert zf.signatures_exigees(str(f))
    f.write_text('[remote "flathub"]\nurl=https://dl.flathub.org/repo/\ngpg-verify=false\n')
    assert not zf.signatures_exigees(str(f))
    f.write_text('[remote "flathub"]\nurl=http://dl.flathub.org/repo/\ngpg-verify=true\n')
    assert not zf.signatures_exigees(str(f))
    assert not zf.signatures_exigees(str(tmp_path / "absent"))


def _ouvrir(cfg, nom="olivier-abc123", uid=12345):
    d = se.dossiers(cfg)["etat"]
    se.ph.dossier_sur(cfg["state_dir"], 0o755)
    se.ph.dossier_sur(d, 0o700)
    se.ph._ecrire(os.path.join(d, nom + ".json"), {"uid": uid, "mode": "ephemere"})


def test_sans_zone_bulle_quand_l_iso_est_vieille(cfg):
    _ouvrir(cfg)
    run = Run()
    assert zf.mettre_a_jour(cfg, run=run, image=IMAGE, actif=False, preparer_fond=False,
                            aujourd_hui=datetime.date(2027, 1, 15)) == "avis"
    bulles = [a for a in run.appels if "notify-send" in a]
    assert len(bulles) == 1 and "runuser -u olivier-abc123" in bulles[0] and "2026-10-27" in bulles[0]
    assert not [a for a in run.appels if a.startswith("flatpak")]


def test_sans_zone_iso_recente_rien(cfg):
    _ouvrir(cfg)
    run = Run()
    assert zf.mettre_a_jour(cfg, run=run, image=IMAGE, actif=False, preparer_fond=False,
                            aujourd_hui=datetime.date(2026, 12, 1)) == "iso"
    assert run.appels == []


def test_zone_sans_signatures_pas_de_mise_a_jour(cfg):
    run = Run()
    assert zf.mettre_a_jour(cfg, run=run, image=IMAGE, actif=True, signes=False) == "refus"
    assert run.appels == []


def test_zone_mise_a_jour_puis_menage(cfg):
    run = Run()
    assert zf.mettre_a_jour(cfg, run=run, image=IMAGE, actif=True, signes=True) == "fait"
    assert run.appels[:2] == ["flatpak update --system --noninteractive --assumeyes",
                              "flatpak uninstall --system --unused --noninteractive --assumeyes"]


def test_zone_pleine_marquee_a_refaire_a_cote_du_fichier(cfg, tmp_path):
    p = zf.points(cfg)
    se.ph.dossier_sur(cfg["state_dir"], 0o755)
    se.ph.dossier_sur(p["base"], 0o700)
    rep = tmp_path / "rep"
    rep.mkdir()
    se.ph._ecrire_texte(p["active"], json.dumps({"tampon": "x", "rep": str(rep)}), mode=0o600)

    def run(argv, **kw):
        return R(1, "", b"error: Write: No space left on device")
    assert zf.mettre_a_jour(cfg, run=run, image=IMAGE, actif=True, signes=True) == "echec"
    assert (rep / zf.A_REFAIRE).exists()


def test_sans_zone_la_prepare_en_fond_sans_bulle(cfg, monkeypatch):
    _ouvrir(cfg)
    monkeypatch.setattr(zf, "preparer_en_fond", lambda *a, **k: "preparee")
    run = Run()
    assert zf.mettre_a_jour(cfg, run=run, image=IMAGE, actif=False,
                            aujourd_hui=datetime.date(2027, 1, 15)) == "preparee"
    assert not [a for a in run.appels if "notify-send" in a]


def test_zone_active_par_findmnt():
    assert zf.zone_active(run=Run(findmnt="bfos-flatpak overlay\n"))
    assert not zf.zone_active(run=Run(findmnt="overlay overlay\n"))
    assert not zf.zone_active(run=Run(findmnt=""))


def test_unites_et_fstab_de_l_image():
    racine = os.path.join(os.path.dirname(__file__), "..", "image", "files")
    fstab = open(os.path.join(racine, "etc", "fstab")).read().splitlines()
    assert "overlay / overlay defaults,x-gvfs-hide 0 0" in fstab
    assert "bfos-flatpak /var/lib/flatpak overlay noauto,x-gvfs-hide 0 0" in fstab
    unite = open(os.path.join(racine, "usr/lib/systemd/system/bfos-live-flatpak.service")).read()
    assert "Before=greetd.service" in unite and "session zone-flatpak" in unite
    maj = open(os.path.join(racine, "usr/lib/systemd/system/bfos-live-flatpak-maj.service")).read()
    assert "IOSchedulingClass=idle" in maj and "session maj-flatpak" in maj
