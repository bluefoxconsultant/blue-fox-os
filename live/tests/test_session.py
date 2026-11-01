"""Essais de la preparation de session en root (#26870, 2026-10-23).

Aucun vrai disque, aucun vrai cryptsetup : `run` et `http` sont simules et on
verifie les commandes dans l'ordre. Jouer en root (conteneur d'essais), comme
les autres essais de la cle.
"""
import base64
import subprocess
import json
import shutil
import os
import pathlib
import pwd
import stat
import types
import urllib.error

import pytest

from bfos_live import pam_helper as ph
from bfos_live import session as se

SUB = "a1b2c3d4e5f6"
CLE = bytes(range(32))
NOM = "olivier-abc123"


def cfg(tmp_path, **extra):
    c = dict(ph.DEFAUTS)
    c.update({
        "issuer": "https://auth.exemple.test/application/o/blue-fox-os-live/",
        "client_id": "blue-fox-os-live",
        "device_url": "https://auth.exemple.test/application/o/device/",
        "token_url": "https://auth.exemple.test/application/o/token/",
        "allowed_groups": ["bf-team"],
        "state_dir": str(tmp_path / "run"),
        "policy_url": "https://org.exemple.test/api/v1/policy/me",
        "profile_key_url": "https://org.exemple.test/api/v1/policy/live/profile-key",
        "profile_size_mb": 2,
        "home_base": str(tmp_path / "home") + "/",
    })
    c.update(extra)
    os.makedirs(c["state_dir"], exist_ok=True)
    os.makedirs(c["home_base"], exist_ok=True)
    return c


@pytest.fixture(autouse=True)
def chemins(tmp_path, monkeypatch):
    """Les fichiers systeme de la session, rediriges dans tmp_path."""
    w = tmp_path / "welcome"
    monkeypatch.setattr(se, "POLITIQUE_PUBLIQUE", str(w / "policy-public.json"))
    monkeypatch.setattr(se, "IDENTIFIANTS_NC", str(w / "nc-credentials.json"))
    monkeypatch.setattr(se, "FLATPAK_DIR", str(tmp_path / "flatpak"))
    monkeypatch.setattr(se, "PROFIL_MINIMUM", 1024 * 1024)
    monkeypatch.setattr(se, "MARGE_LIBRE", 0)
    monkeypatch.setattr(se, "ZONEINFO", str(tmp_path / "zoneinfo"))
    # Comme dans l'image : /etc/X11/xorg.conf.d n'existe pas (aucun Xorg).
    monkeypatch.setattr(se, "CLAVIER_SYSTEME", str(tmp_path / "X11" / "xorg.conf.d" / "00-keyboard.conf"))
    monkeypatch.setattr(se, "VCONSOLE", str(tmp_path / "vconsole.conf"))
    monkeypatch.setattr(se, "LOCALE_CONF", str(tmp_path / "locale.conf"))
    os.makedirs(w, exist_ok=True)
    os.makedirs(tmp_path / "zoneinfo" / "Pacific", exist_ok=True)
    (tmp_path / "zoneinfo" / "Pacific" / "Auckland").write_text("tz")


def compte(tmp_path, uid=12345):
    maison = tmp_path / "home" / NOM
    maison.mkdir(parents=True, exist_ok=True)
    return pwd.struct_passwd((NOM, "x", uid, uid, "", str(maison), "/bin/bash"))


class R:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


class FauxRun:
    """Commandes simulees ; `reponses` donne la sortie par debut de commande."""

    def __init__(self, reponses=None, echecs=()):
        self.reponses = reponses or {}
        self.echecs = set(echecs)
        self.appels = []

    def __call__(self, argv, **kw):
        self.appels.append((list(argv), kw.get("input")))
        for prefixe in self.echecs:
            if " ".join(argv).startswith(prefixe):
                return R(1, "", "refus simule")
        for prefixe, out in self.reponses.items():
            if " ".join(argv).startswith(prefixe):
                return R(0, out(argv) if callable(out) else out)
        return R(0, "")

    def commandes(self):
        return [" ".join(a[:2]) for a, _ in self.appels]


POLITIQUE = {"schema": "bf-policy/v2", "user": {"login": "olivier@exemple.test"},
             "install": {"login": {"bind_password": "SECRET-LDAP"}},
             "apps": {"install": ["com.brave.Browser", "io.anytype.anytype"],
                      "remove": ["org.gnome.Maps"]},
             "services": {"nextcloud": {"url": "https://nc.exemple.test",
                                        "oidc_client_id": "nc"}}}


class FauxHttp:
    def __init__(self, politique=POLITIQUE, cle=(200, {"key": base64.b64encode(CLE).decode(),
                                                     "created": True}),
                 lien=(200, {"url": "https://erp.exemple.test/odoo?bfos_login=JETON",
                             "expires_in": 300})):
        self.politique = politique
        self.cle = cle
        self.lien = lien
        self.appels = []

    def __call__(self, url, methode="GET", jeton=None, corps=None, entetes=None, delai=30):
        self.appels.append((methode, url, jeton, corps))
        if url.endswith("/policy/me"):
            if isinstance(self.politique, tuple):
                return self.politique
            return 200, json.dumps(self.politique)
        if url.endswith("/profile-key"):
            code, corps_rep = self.cle
            return code, json.dumps(corps_rep)
        if "/apppassword" in url:
            return 200, ""
        if url.endswith("/login-link"):
            code, corps_rep = self.lien
            return code, json.dumps(corps_rep)
        raise AssertionError(url)


class FauxApply:
    @staticmethod
    def render_public_policy(p):
        import copy
        q = copy.deepcopy(p)
        q.get("install", {}).get("login", {}).pop("bind_password", None)
        return json.dumps(q)

    @staticmethod
    def render_flatpak_list(ids, kind):
        return "\n".join([f"# {kind}"] + list(ids)) + "\n"


class FauxProvision:
    def __init__(self, depot=None):
        self.depot = depot
        self.appels = []

    def obtenir_identifiants_nextcloud(self, token, policy, env=None, out=None, path=None, **kw):
        self.appels.append((token, env))
        if not self.depot:
            return None
        with open(path, "w") as f:
            json.dump(self.depot, f)
        os.chmod(path, 0o600)
        return self.depot


DEPOT = {"login": "Olivier", "app_password": "APP-PASS", "url": "https://nc.exemple.test"}


# ------------------------------------------------------------- politique
def test_politique_tiree_avec_le_jeton(tmp_path):
    http = FauxHttp()
    assert se.tirer_politique(cfg(tmp_path), "AT", http=http) == POLITIQUE
    assert http.appels[0][:3] == ("GET", "https://org.exemple.test/api/v1/policy/me", "AT")


@pytest.mark.parametrize("rep", [(401, "{}"), (200, "pas du json"),
                                 (200, json.dumps({"schema": "autre"}))])
def test_politique_refusee_ou_illisible_ne_bloque_pas(tmp_path, rep):
    assert se.tirer_politique(cfg(tmp_path), "AT", http=FauxHttp(politique=rep)) is None


def test_copie_publique_sans_mot_de_passe_ldap(tmp_path):
    se.ecrire_politique(POLITIQUE, FauxApply)
    texte = open(se.POLITIQUE_PUBLIQUE).read()
    assert "SECRET-LDAP" not in texte and "olivier@exemple.test" in texte
    assert stat.S_IMODE(os.stat(se.POLITIQUE_PUBLIQUE).st_mode) == 0o644


def test_applis_listes_et_installation_des_manquantes(tmp_path):
    run = FauxRun()
    manquantes = se.appliquer_applis(POLITIQUE, FauxApply, run=run,
                                     est_installe=lambda a: a == "com.brave.Browser")
    assert manquantes == ["io.anytype.anytype"]
    assert "io.anytype.anytype" in open(os.path.join(se.FLATPAK_DIR, "install")).read()
    assert "org.gnome.Maps" in open(os.path.join(se.FLATPAK_DIR, "remove")).read()
    assert ["systemctl", "start", "--no-block", "system-flatpak-setup.service"] in [a for a, _ in run.appels]


def test_applis_toutes_presentes_rien_a_lancer(tmp_path):
    run = FauxRun()
    assert se.appliquer_applis(POLITIQUE, FauxApply, run=run, est_installe=lambda a: True) == []
    assert run.appels == []


# ------------------------------------------------------------- Nextcloud
def test_identifiants_remis_a_la_personne(tmp_path):
    bp = FauxProvision(DEPOT)
    c = compte(tmp_path)
    assert se.identifiants_nextcloud(cfg(tmp_path), "AT", POLITIQUE, c, bp) == DEPOT
    st = os.stat(se.IDENTIFIANTS_NC)
    assert (st.st_uid, stat.S_IMODE(st.st_mode)) == (c.pw_uid, 0o600)
    assert bp.appels[0][1] == {"BFOS_OIDC_TOKEN_URL": "https://auth.exemple.test/application/o/token/",
                               "BFOS_OIDC_CLIENT_ID": "blue-fox-os-live"}


def test_revocation_nextcloud_en_basic(tmp_path):
    vu = {}

    def http(url, methode="GET", entetes=None, delai=30, **kw):
        vu.update(url=url, methode=methode, entetes=entetes, delai=delai)
        return 200, ""
    se.revoquer_nextcloud(DEPOT, http=http)
    assert vu["methode"] == "DELETE" and vu["url"].endswith("/ocs/v2.php/core/apppassword")
    assert base64.b64decode(vu["entetes"]["Authorization"][6:]) == b"Olivier:APP-PASS"
    assert vu["delai"] <= 5


# ------------------------------------------------------------- cle du profil
def test_cle_servie(tmp_path):
    http = FauxHttp()
    assert se.cle_du_profil(cfg(tmp_path), "AT", http=http) == CLE
    assert http.appels[0] == ("POST", cfg(tmp_path)["profile_key_url"], "AT", {"create": True})


@pytest.mark.parametrize("rep, mot", [((403, {"error": "revoked"}), "révoqué"),
                                      ((503, {"error": "escrow unavailable"}), "refuse"),
                                      ((200, {"key": "pas-du-base64!"}), "illisible"),
                                      ((200, {"key": base64.b64encode(b"court").decode()}), "illisible")])
def test_cle_refusee(tmp_path, rep, mot):
    with pytest.raises(se.Echec) as exc:
        se.cle_du_profil(cfg(tmp_path), "AT", http=FauxHttp(cle=rep))
    assert mot in exc.value.message


def test_cle_sans_url(tmp_path):
    with pytest.raises(se.Echec):
        se.cle_du_profil(cfg(tmp_path, profile_key_url=""), "AT", http=FauxHttp())


# ------------------------------------------------------------- la cle USB
def lsblk_disque(enfants):
    return json.dumps({"blockdevices": [{"name": "/dev/sdb", "type": "disk",
                                         "children": enfants}]})


ENFANTS_ISO = [{"name": "/dev/sdb1", "type": "part", "partlabel": "ISO9660", "fstype": "iso9660"},
               {"name": "/dev/sdb2", "type": "part", "partlabel": "Appended2", "fstype": "vfat"}]


def run_cle(avec_profils=False, source="/dev/sdb1"):
    etat = {"cree": avec_profils}

    def lsblk(argv):
        if "PKNAME" in " ".join(argv):
            return json.dumps({"blockdevices": [{"name": source, "type": "part",
                                                 "pkname": "/dev/sdb"}]})
        enfants = list(ENFANTS_ISO)
        if etat["cree"]:
            enfants.append({"name": "/dev/sdb3", "type": "part",
                            "partlabel": se.PARTLABEL, "fstype": "ext4"})
        return lsblk_disque(enfants)

    def sgdisk_n(argv):
        etat["cree"] = True
        return ""
    return FauxRun({"findmnt": source + "\n", "lsblk": lsblk,
                    "sgdisk -F": "2048\n", "sgdisk -E": "30000000\n",
                    "sgdisk -n": sgdisk_n, "cryptsetup luksDump": ENTETE_OK})


def entete(chiffrement="aes-xts-plain64", emplacements=1, zone="aes-xts-plain64"):
    return json.dumps({
        "keyslots": {str(i): {"type": "luks2", "area": {"encryption": zone}}
                     for i in range(emplacements)},
        "segments": {"0": {"type": "crypt", "encryption": chiffrement}}})


ENTETE_OK = entete()


def test_disque_de_la_cle_par_sa_partition():
    assert se.disque_de_la_cle(run=run_cle()) == ("/dev/sdb", "/dev/sdb1")


@pytest.mark.parametrize("source", ["/dev/loop0", "LABEL=x", "/dev/sr0"])
def test_support_sans_profil_possible(source):
    run = run_cle(source=source)
    if source == "/dev/sr0":
        run.reponses["lsblk"] = json.dumps({"blockdevices": [{"name": "/dev/sr0", "type": "rom"}]})
    with pytest.raises(se.Echec):
        se.disque_de_la_cle(run=run)


def test_partition_creee_dans_l_espace_libre_du_meme_disque(tmp_path):
    run = run_cle()
    point = se.monter_profils(cfg(tmp_path), run=run, attendre=lambda s: None)
    cmds = [" ".join(a) for a, _ in run.appels]
    i_e = cmds.index("sgdisk -e /dev/sdb")
    i_n = next(i for i, c in enumerate(cmds) if c.startswith("sgdisk -n"))
    assert i_e < i_n, "la copie de secours GPT doit etre deplacee avant l'ajout"
    assert f"0:{se.PARTLABEL}" in cmds[i_n]
    assert any(c.startswith("mkfs.ext4 -q -F -L BFOS_PROFILS") and c.endswith("/dev/sdb3") for c in cmds)
    assert cmds[-1] == f"mount -t ext4 -o nosuid,nodev,noexec /dev/sdb3 {point}"


def test_partition_existante_pas_de_repartitionnement(tmp_path):
    run = run_cle(avec_profils=True)
    se.monter_profils(cfg(tmp_path), run=run)
    assert not any(a[0] in ("sgdisk", "mkfs.ext4") for a, _ in run.appels)


def test_pas_assez_de_place_pour_une_partition(tmp_path):
    run = run_cle()
    run.reponses["sgdisk -E"] = "2100\n"
    with pytest.raises(se.Echec) as exc:
        se.monter_profils(cfg(tmp_path), run=run)
    assert "place" in exc.value.message
    assert not any(" ".join(a).startswith("sgdisk -n") for a, _ in run.appels)


def test_partition_d_un_autre_type_refusee(tmp_path):
    run = run_cle(avec_profils=True)
    run.reponses["lsblk"] = lambda argv: (
        json.dumps({"blockdevices": [{"name": "/dev/sdb1", "type": "part", "pkname": "/dev/sdb"}]})
        if "PKNAME" in " ".join(argv) else
        lsblk_disque(ENFANTS_ISO + [{"name": "/dev/sdb3", "type": "part",
                                     "partlabel": se.PARTLABEL, "fstype": "ntfs"}]))
    with pytest.raises(se.Echec):
        se.monter_profils(cfg(tmp_path), run=run)


# ------------------------------------------------------------- Ventoy
def sysfs_ventoy(tmp_path, cible_partition=False):
    """Faux /sys : sdb (8:16) avec sdb1 (donnees, debut 2048) et sdb2."""
    sys_ = tmp_path / "sys"
    sdb = sys_ / "block" / "sdb"
    for nom, num, debut, taille in (("sdb1", 1, 2048, 60000000), ("sdb2", 2, 60002048, 65536)):
        (sdb / nom).mkdir(parents=True)
        (sdb / nom / "partition").write_text(f"{num}\n")
        (sdb / nom / "start").write_text(f"{debut}\n")
        (sdb / nom / "size").write_text(f"{taille}\n")
    (sys_ / "dev" / "block").mkdir(parents=True)
    os.symlink(sdb, sys_ / "dev" / "block" / "8:16")
    os.symlink(sdb / "sdb1", sys_ / "dev" / "block" / "8:17")
    dm0 = sys_ / "devices" / "virtual" / "block" / "dm-0"
    (dm0 / "dm").mkdir(parents=True)
    os.symlink(dm0, sys_ / "dev" / "block" / "252:0")
    return str(sys_)


TABLE_VENTOY = "0 4000000 linear 8:16 5000000\n4000000 1000 linear 8:16 9100000\n"


def run_ventoy(table=TABLE_VENTOY, etiquettes=("Ventoy", "VTOYEFI"), fs="exfat\n"):
    def lsblk(argv):
        if "PKNAME" in " ".join(argv):
            # Comme sous Ventoy 1.1.18 : la projection faite dans l'initramfs est invisible.
            return json.dumps({"blockdevices": []})
        return json.dumps({"blockdevices": [{"name": "/dev/sdb", "children": [
            {"name": f"/dev/sdb{i + 1}", "label": e} for i, e in enumerate(etiquettes)]}]})
    return FauxRun({"findmnt": "/dev/ventoy 252:0\n", "lsblk": lsblk,
                    "dmsetup table": table, "blkid": fs})


def test_ventoy_reconnu_sur_le_disque(tmp_path):
    run = run_ventoy()
    s = se.support_de_la_cle(run=run, sysfs=sysfs_ventoy(tmp_path))
    assert s == {"genre": "ventoy", "source": "/dev/ventoy", "majmin": "8:16",
                 "debut": 2048, "taille": 60000000, "disque": "/dev/sdb"}
    cmds = [" ".join(a) for a, _ in run.appels]
    assert "dmsetup table -j 252 -m 0" in cmds
    assert not any("PKNAME" in c for c in cmds), "lsblk ne voit pas la projection de Ventoy"


def test_source_hors_projection_reste_gpt(tmp_path):
    # Meme sysfs, mais la source est une vraie partition : on ne part pas sur Ventoy.
    run = run_cle()
    run.reponses["findmnt"] = "/dev/sdb1 8:17\n"
    s = se.support_de_la_cle(run=run, sysfs=sysfs_ventoy(tmp_path))
    assert s["genre"] == "gpt" and s["disque"] == "/dev/sdb"


def test_ventoy_table_sur_la_partition(tmp_path):
    table = "0 4000000 linear 8:17 3000000\n"
    s = se.support_de_la_cle(run=run_ventoy(table=table), sysfs=sysfs_ventoy(tmp_path))
    assert (s["majmin"], s["debut"], s["taille"], s["disque"]) == ("8:17", 0, 60000000, "/dev/sdb")


@pytest.mark.parametrize("table, etiquettes", [
    ("0 4000000 linear 8:16 5000000\n0 10 linear 8:32 0\n", ("Ventoy", "VTOYEFI")),  # deux cibles
    ("0 4000000 striped 2 8 8:16 0 8:32 0\n", ("Ventoy", "VTOYEFI")),               # pas lineaire
    ("0 4000000 delay 8:16 5000000\n", ("Ventoy", "VTOYEFI")),                      # 5 champs, pas lineaire
    ("0 4000000 linear 8:16 1000\n", ("Ventoy", "VTOYEFI")),                         # hors partition 1
    ("0 4000000 linear 8:16 59990000\n", ("Ventoy", "VTOYEFI")),                     # deborde
    (TABLE_VENTOY, ("Ventoy", "EFI")),                                               # pas Ventoy
])
def test_ventoy_suspect_refuse(tmp_path, table, etiquettes):
    with pytest.raises(se.Echec) as exc:
        se.support_de_la_cle(run=run_ventoy(table=table, etiquettes=etiquettes),
                             sysfs=sysfs_ventoy(tmp_path))
    assert "clé USB" in exc.value.message


def test_cle_gravee_reste_gpt():
    assert se.support_de_la_cle(run=run_cle())["genre"] == "gpt"


def support_v():
    return {"genre": "ventoy", "majmin": "8:16", "debut": 2048, "taille": 60000000,
            "disque": "/dev/sdb", "source": "/dev/ventoy"}


def test_ventoy_monte_par_une_seconde_projection(tmp_path):
    run = run_ventoy()
    c = cfg(tmp_path)
    point = se.monter_profils(c, run=run, monte=lambda p: False, support=support_v())
    d = se.dossiers(c)
    cmds = [" ".join(a) for a, _ in run.appels]
    assert cmds[0] == f"dmsetup create {se.VENTOY_DM} --table 0 60000000 linear 8:16 2048"
    assert f"mount -t exfat -o nosuid,nodev,noexec,umask=077 /dev/mapper/{se.VENTOY_DM} {d['ventoy']}" in cmds
    rep = os.path.join(d["ventoy"], se.VENTOY_DOSSIER)
    assert cmds[-1] == f"mount --bind -o nosuid,nodev,noexec {rep} {point}"
    assert os.path.isdir(rep)
    # Jamais de repartitionnement ni de formatage sous Ventoy.
    assert not any(a[0] in ("sgdisk", "mkfs.ext4", "partx") for a, _ in run.appels)


def test_ventoy_format_inconnu_refuse(tmp_path):
    run = run_ventoy(fs="iso9660\n")
    with pytest.raises(se.Echec):
        se.monter_profils(cfg(tmp_path), run=run, monte=lambda p: False, support=support_v())
    assert not any(a[0] == "mount" for a, _ in run.appels)


def test_ventoy_dossier_remplace_par_un_lien_refuse(tmp_path):
    c = cfg(tmp_path)
    d = se.dossiers(c)
    os.makedirs(d["ventoy"])
    os.symlink("/etc", os.path.join(d["ventoy"], se.VENTOY_DOSSIER))
    run = run_ventoy()
    with pytest.raises(se.Echec):
        se.monter_profils(c, run=run, monte=lambda p: p == d["ventoy"], support=support_v())
    assert not any("--bind" in a for a, _ in run.appels)


def test_ventoy_demonte_et_retire_la_projection():
    run = FauxRun()
    c = {"state_dir": "/run/bfos-live"}
    se.demonter_profils_si_libre(c, run=run, lister=lambda d: [], monte=lambda p: True,
                                 existe=lambda p: True)
    assert [" ".join(a) for a, _ in run.appels] == [
        "umount /run/bfos-live/profils", "umount /run/bfos-live/ventoy",
        f"dmsetup remove {se.VENTOY_DM}"]


# ------------------------------------------------------------- le profil
def statvfs_libre(octets):
    return lambda p: types.SimpleNamespace(f_bavail=octets // 4096, f_frsize=4096)


def ouvrir(tmp_path, run, cle=CLE, uid=12345, **kw):
    c = cfg(tmp_path)
    skel = tmp_path / "skel"
    (skel / ".config").mkdir(parents=True, exist_ok=True)
    (skel / ".config" / "kdeglobals").write_text("[General]\n")
    (skel / ".bashrc").write_text("# bash\n")
    return se.ouvrir_profil(c, NOM, SUB, compte(tmp_path, uid), cle, run=run,
                            attendre=lambda s: None, statvfs=statvfs_libre(64 << 20),
                            squelette=str(skel), **kw), c


def test_profil_neuf_cree_chiffre_et_peuple(tmp_path):
    run = run_cle(avec_profils=True)
    neuf, c = ouvrir(tmp_path, run)
    assert neuf
    fichier = os.path.join(se.dossiers(c)["profils"], se.nom_fichier_profil(SUB))
    assert os.path.getsize(fichier) == 2 << 20 and stat.S_IMODE(os.stat(fichier).st_mode) == 0o600
    cmds = run.commandes()
    assert cmds.index("cryptsetup luksFormat") < cmds.index("cryptsetup open") < cmds.index("mkfs.ext4 -q")
    # La cle passe par l'entree standard, jamais en argument.
    for argv, entree in run.appels:
        if argv[0] == "cryptsetup":
            assert entree == CLE and CLE.hex() not in " ".join(argv)
    maison = tmp_path / "home" / NOM
    assert (maison / ".config" / "kdeglobals").exists()
    assert "chown -R" in cmds and "restorecon -R" in cmds
    # Le nom du fichier ne dit pas a qui il est.
    assert "olivier" not in se.nom_fichier_profil(SUB)


def test_profil_existant_ouvert_sans_reformater(tmp_path):
    run = run_cle(avec_profils=True)
    ouvrir(tmp_path, run)
    run2 = run_cle(avec_profils=True)
    neuf, _c = ouvrir(tmp_path, run2)
    assert not neuf
    cmds = run2.commandes()
    assert "cryptsetup luksFormat" not in cmds and "mkfs.ext4 -q" not in cmds
    assert "cryptsetup open" in cmds and "e2fsck -p" in cmds


def statvfs_profil(maison, occupe, libre_cle, total=1 << 30, partition=1 << 40):
    """statvfs de la maison (profil monte) et de la partition des profils."""
    def st(p):
        if os.path.realpath(p) == os.path.realpath(maison):
            libre = int(total * (1 - occupe)) // 4096
            return types.SimpleNamespace(f_blocks=total // 4096, f_bfree=libre, f_bavail=libre,
                                         f_frsize=4096)
        return types.SimpleNamespace(f_blocks=partition // 4096, f_bfree=libre_cle // 4096,
                                     f_bavail=libre_cle // 4096, f_frsize=4096)
    return st


def un_pas(tmp_path, monkeypatch, occupe, libre_cle, exfat=False, partition=1 << 40,
           boucle_de=lambda dev: "/dev/loop7", **extra):
    """Profil cree (2 Mio) et ouvert, puis UN pas d'agrandissement."""
    monkeypatch.setattr(se, "PAS_MIN", 1 << 20)
    ouvrir(tmp_path, run_cle(avec_profils=True))
    c = cfg(tmp_path, **{"profile_reserve_mb": 0, **extra})
    run = FauxRun()
    maison = tmp_path / "home" / NOM
    fichier = os.path.join(se.dossiers(c)["profils"], se.nom_fichier_profil(SUB))
    pas = se.agrandir_profil(c, se.dossiers(c)["profils"], fichier, se.mapper(NOM),
                             f"/dev/mapper/{se.mapper(NOM)}", str(maison), CLE, run=run,
                             statvfs=statvfs_profil(maison, occupe, libre_cle, partition=partition),
                             exfat=exfat, boucle_de=boucle_de)
    return run, [" ".join(a) for a, _ in run.appels], os.path.getsize(fichier), pas


def test_profil_presque_plein_double(tmp_path, monkeypatch):
    run, cmds, taille, pas = un_pas(tmp_path, monkeypatch, occupe=0.9, libre_cle=1 << 30)
    assert taille == 4 << 20 and pas == 2 << 20
    mappe = se.mapper(NOM)
    assert cmds == ["sync", "losetup --set-capacity /dev/loop7",
                    f"cryptsetup resize --key-file - {mappe}", f"resize2fs /dev/mapper/{mappe}", "sync"]
    for argv, entree in run.appels:
        if argv[:2] == ["cryptsetup", "resize"]:
            assert entree == CLE and CLE.hex() not in " ".join(argv)


def test_profil_pas_plein_ne_grandit_pas(tmp_path, monkeypatch):
    _run, cmds, taille, pas = un_pas(tmp_path, monkeypatch, occupe=0.5, libre_cle=1 << 30)
    assert taille == 2 << 20 and pas == 0 and cmds == []


def test_seuil_sur_l_occupation_reelle(tmp_path, monkeypatch):
    # 5 % reserves a root : f_bavail < f_bfree. On compte f_bfree.
    monkeypatch.setattr(se, "PAS_MIN", 1 << 20)
    maison = tmp_path / "m"
    maison.mkdir()
    st = types.SimpleNamespace(f_blocks=1000, f_bfree=250, f_bavail=150, f_frsize=4096)
    run = FauxRun()
    pas = se.agrandir_profil({}, "/p", str(tmp_path / "x"), "m", "/dev/mapper/m", str(maison), CLE,
                             run=run, statvfs=lambda p: st, boucle_de=lambda d: "/dev/loop7")
    assert pas == 0 and run.appels == []


def test_profil_plein_sans_place(tmp_path, monkeypatch):
    _run, cmds, taille, pas = un_pas(tmp_path, monkeypatch, occupe=0.95,
                                     libre_cle=se.MARGE_LIBRE + (512 << 10))
    assert taille == 2 << 20 and pas == 0 and cmds == ["sync"]


def test_profil_laisse_un_profil_complet_a_une_autre_personne(tmp_path, monkeypatch):
    # Reserve par defaut = profile_size_mb (2 Mio au banc) : 1 Mio de plus que
    # marge + reserve, il grandit de 1 Mio.
    _run, _cmds, taille, _pas = un_pas(tmp_path, monkeypatch, occupe=0.9,
                                       libre_cle=se.MARGE_LIBRE + (2 << 20) + (1 << 20),
                                       profile_reserve_mb=None)
    assert taille == 3 << 20


def test_profil_plafond_regle(tmp_path, monkeypatch):
    _run, _cmds, taille, _pas = un_pas(tmp_path, monkeypatch, occupe=0.9, libre_cle=1 << 30,
                                       profile_max_mb=3)
    assert taille == 3 << 20


def test_profil_plafond_par_defaut_moitie_de_la_partition(tmp_path, monkeypatch):
    _run, _cmds, taille, _pas = un_pas(tmp_path, monkeypatch, occupe=0.9, libre_cle=1 << 30,
                                       partition=6 << 20)
    assert taille == 3 << 20


def test_exfat_petit_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "PAS_MAX_EXFAT", 1 << 20)
    _run, _cmds, taille, _pas = un_pas(tmp_path, monkeypatch, occupe=0.9, libre_cle=1 << 30,
                                       exfat=True)
    assert taille == 3 << 20


def test_boucle_introuvable_rien_n_est_ecrit(tmp_path, monkeypatch):
    def rate(dev):
        raise se.Echec("Erreur interne.", "deux boucles")
    _run, cmds, taille, pas = un_pas(tmp_path, monkeypatch, occupe=0.9, libre_cle=1 << 30,
                                     boucle_de=rate)
    assert taille == 2 << 20 and pas == 0 and cmds == []


def test_delai_depasse_n_est_pas_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "PAS_MIN", 1 << 20)
    ouvrir(tmp_path, run_cle(avec_profils=True))
    c = cfg(tmp_path, profile_reserve_mb=0)

    def run(argv, **kw):
        if argv[0] == "resize2fs":
            raise subprocess.TimeoutExpired(argv, 600)
        return R(0, "")
    maison = tmp_path / "home" / NOM
    fichier = os.path.join(se.dossiers(c)["profils"], se.nom_fichier_profil(SUB))
    assert se.agrandir_profil(c, se.dossiers(c)["profils"], fichier, se.mapper(NOM), "/dev/x",
                              str(maison), CLE, run=run,
                              statvfs=statvfs_profil(maison, 0.9, 1 << 30),
                              boucle_de=lambda d: "/dev/loop7") == 0


def test_boucle_du_profil_lue_sous_le_mappage(tmp_path):
    sysfs = tmp_path / "sys"
    (sysfs / "dev" / "block" / "253:4" / "slaves" / "loop9").mkdir(parents=True)
    st = types.SimpleNamespace(st_rdev=os.makedev(253, 4))
    assert se.boucle_du_profil("/dev/mapper/x", sysfs=str(sysfs), stat=lambda p: st) == "/dev/loop9"
    (sysfs / "dev" / "block" / "253:4" / "slaves" / "loop3").mkdir()
    with pytest.raises(se.Echec):
        se.boucle_du_profil("/dev/mapper/x", sysfs=str(sysfs), stat=lambda p: st)


def etat_persistant(tmp_path, c, **extra):
    d = se.dossiers(c)
    ph.dossier_sur(d["etat"], 0o700)
    maison = tmp_path / "home" / NOM
    maison.mkdir(parents=True, exist_ok=True)
    etat = {"mode": "persistant", "profil_monte": True, "profil_sain": True,
            "maison": str(maison), "profil_fichier": str(tmp_path / "f.luks"),
            "cle_profil": base64.b64encode(CLE).decode(), **extra}
    ph._ecrire(os.path.join(d["etat"], NOM + ".json"), etat, mode=0o600)
    return maison


def test_agrandir_par_pas_puis_s_arrete(tmp_path):
    c = cfg(tmp_path)
    etat_persistant(tmp_path, c)
    appels = []

    def pas(*a, **k):
        appels.append(k["exfat"])
        return (1 << 20) if len(appels) < 3 else 0
    run = FauxRun()
    import unittest.mock as um
    with um.patch.object(se, "agrandir_profil", pas):
        total = se.agrandir(c, NOM, run=run, monte=lambda p: not p.endswith("ventoy"))
    assert total == 2 << 20 and appels == [False] * 3
    # La reprise d'une croissance interrompue passe d'abord.
    assert [" ".join(a) for a, _ in run.appels][0] == f"resize2fs /dev/mapper/{se.mapper(NOM)}"


@pytest.mark.parametrize("sortie, code, attendu", [("2011bab0\n", 0, True), ("ef53\n", 0, False),
                                                   ("58465342\n", 0, False), ("", 1, True)])
def test_est_exfat_par_le_nombre_magique(sortie, code, attendu):
    def run(argv, **kw):
        assert argv[:4] == ["stat", "-f", "-c", "%t"]
        return R(code, sortie)
    assert se.est_exfat("/p", run=run) is attendu


def test_agrandir_petits_pas_sur_exfat(tmp_path):
    c = cfg(tmp_path)
    etat_persistant(tmp_path, c)
    vus = []
    import unittest.mock as um
    with um.patch.object(se, "agrandir_profil", lambda *a, **k: vus.append(k["exfat"]) or 0):
        se.agrandir(c, NOM, run=FauxRun({"stat -f": "2011bab0\n"}), monte=lambda p: True)
    assert vus == [True]


def test_agrandir_borne_le_nombre_de_pas(tmp_path):
    c = cfg(tmp_path)
    etat_persistant(tmp_path, c)
    import unittest.mock as um
    appels = []

    def pas(*a, **k):
        appels.append(1)
        assert len(appels) <= se.PAS_PAR_PASSE, "pas illimites"
        return 1 << 20
    with um.patch.object(se, "agrandir_profil", pas):
        assert se.agrandir(c, NOM, run=FauxRun(), monte=lambda p: True) == se.PAS_PAR_PASSE << 20


@pytest.mark.parametrize("champ, valeur", [("profil_sain", False), ("mode", "ephemere"),
                                           ("cle_profil", None), ("profil_monte", False)])
def test_agrandir_refuse_hors_d_un_profil_sain_ouvert(tmp_path, champ, valeur):
    c = cfg(tmp_path)
    etat_persistant(tmp_path, c, **{champ: valeur})
    run = FauxRun()
    assert se.agrandir(c, NOM, run=run, monte=lambda p: True) == 0 and run.appels == []


def test_e2fsck_en_echec_marque_le_profil_non_sain(tmp_path):
    ouvrir(tmp_path, run_cle(avec_profils=True))
    base = run_cle(avec_profils=True)

    def run(argv, **kw):
        # Code 4 : erreurs laissees non corrigees (1 = corrigees, sain).
        return R(4, "", "") if argv[:2] == ["e2fsck", "-p"] else base(argv, **kw)
    etat = {}
    ouvrir(tmp_path, run, etat=etat)
    assert etat["profil_sain"] is False and etat["profil_fichier"].endswith(".luks")


def test_e2fsck_qui_corrige_reste_sain(tmp_path):
    ouvrir(tmp_path, run_cle(avec_profils=True))
    run = run_cle(avec_profils=True)
    run.echecs.add("e2fsck -p")
    etat = {}
    ouvrir(tmp_path, run, etat=etat)
    assert etat["profil_sain"] is True


def test_profil_rouvert_lance_l_agrandissement_sans_l_attendre(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    ouvrir(tmp_path, run_cle(avec_profils=True))
    deposer_demande(c, "persistant")
    run = run_cle(avec_profils=True)
    etat = se.demarrer(c, NOM, http=FauxHttp(), run=run, modules=(FauxProvision(DEPOT), FauxApply),
                       getpwnam=annuaire_de(c, cpt), attendre=lambda s: None,
                       statvfs=statvfs_libre(64 << 20))
    cmds = [" ".join(a) for a, _ in run.appels]
    assert cmds[-1] == f"systemctl start --no-block bfos-live-agrandir@{NOM}.service"
    assert base64.b64decode(etat["cle_profil"]) == CLE


def test_profil_neuf_ne_lance_pas_l_agrandissement(tmp_path):
    c = cfg(tmp_path)
    deposer_demande(c, "persistant")
    run = run_cle(avec_profils=True)
    se.demarrer(c, NOM, http=FauxHttp(), run=run, modules=(FauxProvision(DEPOT), FauxApply),
                getpwnam=annuaire_de(c, compte(tmp_path)), attendre=lambda s: None,
                statvfs=statvfs_libre(64 << 20))
    assert not any("agrandir" in " ".join(a) for a, _ in run.appels)


def test_mauvaise_cle_message_clair(tmp_path):
    ouvrir(tmp_path, run_cle(avec_profils=True))
    run = run_cle(avec_profils=True)
    run.echecs.add("cryptsetup open")
    with pytest.raises(se.Echec) as exc:
        ouvrir(tmp_path, run, cle=b"\x00" * 32)
    assert "ne s'ouvre pas" in exc.value.message


def test_creation_ratee_ne_laisse_pas_de_conteneur_mort(tmp_path):
    run = run_cle(avec_profils=True)
    run.echecs.add("mkfs.ext4 -q -L profil")
    with pytest.raises(se.Echec):
        ouvrir(tmp_path, run)
    c = cfg(tmp_path)
    assert not os.path.exists(os.path.join(se.dossiers(c)["profils"], se.nom_fichier_profil(SUB)))


def test_plus_de_place_pour_un_nouveau_profil(tmp_path):
    run = run_cle(avec_profils=True)
    c = cfg(tmp_path)
    with pytest.raises(se.Echec) as exc:
        se.ouvrir_profil(c, NOM, SUB, compte(tmp_path), CLE, run=run, attendre=lambda s: None,
                         statvfs=statvfs_libre(1024), squelette=str(tmp_path))
    assert "place" in exc.value.message


def test_dossier_personnel_hors_de_home_refuse(tmp_path):
    c = cfg(tmp_path)
    autre = pwd.struct_passwd((NOM, "x", 1, 1, "", "/etc", "/bin/bash"))
    with pytest.raises(se.Echec):
        se.ouvrir_profil(c, NOM, SUB, autre, CLE, run=run_cle(avec_profils=True),
                         attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))


def test_fermer_profil_attend_puis_detache(tmp_path):
    run = FauxRun(echecs=["umount -R /home"])
    se.fermer_profil(NOM, "/home/x", run=run, attendre=lambda s: None, essais=3,
                     monte=lambda p: True, existe=lambda p: True)
    cmds = [" ".join(a) for a, _ in run.appels]
    assert cmds.count("umount -R /home/x") == 3, "les caches en memoire vive d'abord"
    assert "umount -R -l /home/x" in cmds
    assert f"cryptsetup close {se.mapper(NOM)}" in cmds


# ------------------------------------------------------------- bout en bout
def deposer_demande(c, mode, sub=SUB):
    d = se.dossiers(c)["demandes"]
    ph.dossier_sur(d, 0o700)
    ph._ecrire(os.path.join(d, NOM + ".json"), {"mode": mode, "sub": sub, "access_token": "AT"})


def annuaire_de(c, cpt):
    ph.dossier_sur(os.path.join(c["state_dir"], "users"), 0o755)
    ph._ecrire(os.path.join(c["state_dir"], "users", NOM + ".json"),
               {"sub": SUB, "uid": cpt.pw_uid}, mode=0o644)
    return lambda nom: cpt if nom == NOM else (_ for _ in ()).throw(KeyError(nom))


def test_demarrer_ephemere(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    http, bp = FauxHttp(), FauxProvision(DEPOT)
    etat = se.demarrer(c, NOM, http=http, run=FauxRun(), modules=(bp, FauxApply),
                       getpwnam=annuaire_de(c, cpt))
    assert etat["mode"] == "ephemere" and etat["nextcloud"] == DEPOT
    assert not os.path.exists(os.path.join(se.dossiers(c)["demandes"], NOM + ".json"))
    assert os.path.exists(se.POLITIQUE_PUBLIQUE) and os.path.exists(se.IDENTIFIANTS_NC)
    assert not any(u.endswith("/profile-key") for _m, u, _j, _c in http.appels)


def test_demarrer_persistant_deja_configure_ne_refrappe_pas_nextcloud(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    ouvrir(tmp_path, run_cle(avec_profils=True))  # le profil existe
    drapeau = tmp_path / "home" / NOM / se.DRAPEAU_ACCUEIL
    drapeau.parent.mkdir(parents=True, exist_ok=True)
    drapeau.touch()
    rclone = tmp_path / "home" / NOM / se.RCLONE_PROFIL
    rclone.parent.mkdir(parents=True, exist_ok=True)
    rclone.write_text("[bf-nc]\n")
    deposer_demande(c, "persistant")
    bp = FauxProvision(DEPOT)
    etat = se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                       modules=(bp, FauxApply), getpwnam=annuaire_de(c, cpt),
                       attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    assert etat["profil_monte"] and "nextcloud" not in etat
    assert bp.appels == []
    assert drapeau.exists()


def accueil_fait_sans_nextcloud(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    ouvrir(tmp_path, run_cle(avec_profils=True))
    drapeau = tmp_path / "home" / NOM / se.DRAPEAU_ACCUEIL
    drapeau.parent.mkdir(parents=True, exist_ok=True)
    drapeau.touch()
    deposer_demande(c, "persistant")
    return c, cpt, drapeau


def test_accueil_fait_sans_nextcloud_est_refait_avec_les_identifiants(tmp_path):
    # 2026-10-25 : l'echange etait refuse par Authentik, l'accueil s'est fait
    # sans Nextcloud ; le profil garde ne devait pas en rester prive a jamais.
    c, cpt, drapeau = accueil_fait_sans_nextcloud(tmp_path)
    bp = FauxProvision(DEPOT)
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(bp, FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    assert len(bp.appels) == 1 and os.path.exists(se.IDENTIFIANTS_NC)
    assert not drapeau.exists()


def test_accueil_refait_une_seule_fois(tmp_path):
    c, cpt, drapeau = accueil_fait_sans_nextcloud(tmp_path)
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    marque = tmp_path / "home" / NOM / se.DRAPEAU_NC_REFAIT
    assert marque.exists() and not drapeau.exists()
    # L'agent refait l'accueil sans rien monter (rclone.conf toujours absent).
    drapeau.touch()
    se.arreter(c, NOM, http=FauxHttp(), run=FauxRun(), fermer=lambda *a, **k: None,
               finir=lambda *a, **k: None, demonter=lambda *a, **k: None)
    deposer_demande(c, "persistant")
    bp = FauxProvision(DEPOT)
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(bp, FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    assert bp.appels == [] and drapeau.exists()


def test_marque_piegee_par_un_lien_ou_un_tube_refuse(tmp_path):
    maison = tmp_path / "m"
    (maison / ".config" / "bluefox-welcome").mkdir(parents=True)
    cible = tmp_path / "cible"
    os.symlink(cible, maison / se.DRAPEAU_NC_REFAIT)
    assert not se.poser_sous(str(maison), se.DRAPEAU_NC_REFAIT) and not cible.exists()
    os.unlink(maison / se.DRAPEAU_NC_REFAIT)
    os.mkfifo(maison / se.DRAPEAU_NC_REFAIT)
    assert not se.poser_sous(str(maison), se.DRAPEAU_NC_REFAIT)


def test_arreter_retire_la_cle_meme_si_la_fermeture_echoue(tmp_path):
    c = cfg(tmp_path)
    etat_persistant(tmp_path, c)

    def fermer(*a, **k):
        raise RuntimeError("sync trop long")
    with pytest.raises(RuntimeError):
        se.arreter(c, NOM, http=FauxHttp(), run=FauxRun(), fermer=fermer,
                   finir=lambda *a, **k: None, demonter=lambda *a, **k: None)
    reste = se.lire_json_root(os.path.join(se.dossiers(c)["etat"], NOM + ".json"))
    assert "cle_profil" not in reste


def test_lancer_l_agrandissement_en_echec_ne_defait_pas_la_session(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    ouvrir(tmp_path, run_cle(avec_profils=True))
    deposer_demande(c, "persistant")
    base = run_cle(avec_profils=True)

    def run(argv, **kw):
        if argv[:2] == ["systemctl", "start"]:
            raise subprocess.TimeoutExpired(argv, 60)
        return base(argv, **kw)
    etat = se.demarrer(c, NOM, http=FauxHttp(), run=run, modules=(FauxProvision(DEPOT), FauxApply),
                       getpwnam=annuaire_de(c, cpt), attendre=lambda s: None,
                       statvfs=statvfs_libre(64 << 20))
    assert etat["profil_monte"]


def test_accueil_pas_refait_si_l_echange_echoue(tmp_path):
    c, cpt, drapeau = accueil_fait_sans_nextcloud(tmp_path)
    bp = FauxProvision(None)
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(bp, FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    assert len(bp.appels) == 1 and drapeau.exists()


def test_accueil_refait_revoque_si_l_agent_n_a_rien_monte(tmp_path):
    c, cpt, _drapeau = accueil_fait_sans_nextcloud(tmp_path)
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    http = FauxHttp()
    se.arreter(c, NOM, http=http, run=FauxRun(), fermer=lambda *a, **k: None,
               finir=lambda *a, **k: None, demonter=lambda *a, **k: None)
    assert [m for m, *_ in http.appels] == ["DELETE"]


def test_accueil_refait_garde_le_mot_de_passe_monte(tmp_path):
    c, cpt, _drapeau = accueil_fait_sans_nextcloud(tmp_path)
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    rclone = tmp_path / "home" / NOM / se.RCLONE_PROFIL
    rclone.parent.mkdir(parents=True, exist_ok=True)
    rclone.write_text("[bf-nc]\n")
    http = FauxHttp()
    se.arreter(c, NOM, http=http, run=FauxRun(), fermer=lambda *a, **k: None,
               finir=lambda *a, **k: None, demonter=lambda *a, **k: None)
    assert http.appels == []


def test_arreter_persistant_stoppe_l_agrandissement_avant_de_fermer(tmp_path):
    c = cfg(tmp_path)
    etat_persistant(tmp_path, c)
    run, ordre = FauxRun(), []
    se.arreter(c, NOM, http=FauxHttp(), run=run, fermer=lambda *a, **k: ordre.append("fermer"),
               finir=lambda *a, **k: None, demonter=lambda *a, **k: None)
    assert [" ".join(a) for a, _ in run.appels][0] == f"systemctl stop bfos-live-agrandir@{NOM}.service"
    assert ordre == ["fermer"]


def test_accueil_fait_sans_nextcloud_dans_la_politique_reste_fait(tmp_path):
    c, cpt, drapeau = accueil_fait_sans_nextcloud(tmp_path)
    bp = FauxProvision(DEPOT)
    sans_nc = {k: v for k, v in POLITIQUE.items() if k != "services"}
    se.demarrer(c, NOM, http=FauxHttp(politique=sans_nc), run=run_cle(avec_profils=True),
                modules=(bp, FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    assert bp.appels == [] and drapeau.exists()


def test_retirer_sous_ne_suit_pas_un_lien(tmp_path):
    ailleurs = tmp_path / "ailleurs" / "bluefox-welcome"
    ailleurs.mkdir(parents=True)
    (ailleurs / "done").touch()
    maison = tmp_path / "maison"
    maison.mkdir()
    os.symlink(tmp_path / "ailleurs", maison / ".config")
    assert not se.retirer_sous(str(maison), se.DRAPEAU_ACCUEIL)
    assert (ailleurs / "done").exists()


def test_retirer_sous_absent_vaut_fait(tmp_path):
    assert se.retirer_sous(str(tmp_path), se.DRAPEAU_ACCUEIL)


def test_demarrer_persistant_garde_le_mot_de_passe_nextcloud(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "persistant")
    etat = se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                       modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt),
                       attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    # Le profil neuf est configure par l'agent : identifiants deposes, mais
    # PAS revoques a la fermeture (ils vivent dans rclone.conf, chiffre).
    assert os.path.exists(se.IDENTIFIANTS_NC) and "nextcloud" not in etat


def test_demarrer_persistant_lance_la_preparation_de_la_zone(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "persistant")
    run = run_cle(avec_profils=True)
    se.demarrer(c, NOM, http=FauxHttp(), run=run, modules=(FauxProvision(DEPOT), FauxApply),
                getpwnam=annuaire_de(c, cpt), attendre=lambda s: None,
                statvfs=statvfs_libre(64 << 20))
    cmds = [" ".join(a) for a, _ in run.appels]
    assert "systemctl start --no-block bfos-live-flatpak-maj.service" in cmds


def test_demarrer_ephemere_ne_touche_pas_a_la_cle(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    run = FauxRun()
    se.demarrer(c, NOM, http=FauxHttp(), run=run, modules=(FauxProvision(DEPOT), FauxApply),
                getpwnam=annuaire_de(c, cpt))
    assert not [a for a, _ in run.appels if "bfos-live-flatpak-maj.service" in a]


def test_demande_d_un_autre_sub_refusee(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere", sub="quelqu-un-d-autre")
    with pytest.raises(se.Echec):
        se.demarrer(c, NOM, http=FauxHttp(), run=FauxRun(), modules=(FauxProvision(), FauxApply),
                    getpwnam=annuaire_de(c, cpt))


def test_demande_lisible_par_d_autres_refusee(tmp_path):
    c = cfg(tmp_path)
    deposer_demande(c, "ephemere")
    os.chmod(os.path.join(se.dossiers(c)["demandes"], NOM + ".json"), 0o644)
    with pytest.raises(se.Echec):
        se.demarrer(c, NOM, http=FauxHttp(), run=FauxRun(), modules=(FauxProvision(), FauxApply))


def test_sans_demande_refus(tmp_path):
    with pytest.raises(se.Echec):
        se.demarrer(cfg(tmp_path), NOM, http=FauxHttp(), run=FauxRun(),
                    modules=(FauxProvision(), FauxApply))


def test_arreter_ephemere_revoque_et_efface(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    se.demarrer(c, NOM, http=FauxHttp(), run=FauxRun(), modules=(FauxProvision(DEPOT), FauxApply),
                getpwnam=annuaire_de(c, cpt))
    http = FauxHttp()
    fermes = []
    se.arreter(c, NOM, http=http, run=FauxRun(), fermer=lambda *a, **k: fermes.append(a),
               finir=lambda *a, **k: None)
    assert any(m == "DELETE" for m, *_ in http.appels)
    assert not os.path.exists(se.IDENTIFIANTS_NC) and not os.path.exists(se.POLITIQUE_PUBLIQUE)
    assert fermes == []


def test_arreter_persistant_ferme_le_profil_sans_revoquer(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "persistant")
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    http = FauxHttp()
    fermes = []
    se.arreter(c, NOM, http=http, run=FauxRun(), fermer=lambda *a, **k: fermes.append(a),
               finir=lambda *a, **k: None, demonter=lambda *a, **k: None)
    assert not any(m == "DELETE" for m, *_ in http.appels)
    assert fermes and fermes[0][0] == NOM


def test_main_en_echec_ecrit_le_message_et_defait(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    monkeypatch.setattr(ph, "charger_config", lambda chemin: c)

    def demarrer(cfg_, nom):
        raise se.Echec("Ton profil persistant a été révoqué par ton organisation.", "test")
    arrets = []
    monkeypatch.setattr(se, "demarrer", demarrer)
    monkeypatch.setattr(se, "arreter", lambda cfg_, nom: arrets.append(nom))
    assert se.main(["demarrer", NOM]) == 1
    lu = json.load(open(os.path.join(se.dossiers(c)["resultats"], NOM + ".json")))
    assert lu == {"ok": False, "message": "Ton profil persistant a été révoqué par ton organisation."}
    assert arrets == [NOM]


@pytest.mark.parametrize("argv", [[], ["demarrer"], ["detruire", NOM], ["demarrer", "../x"]])
def test_main_arguments_refuses(argv, monkeypatch, tmp_path):
    monkeypatch.setattr(ph, "charger_config", lambda chemin: cfg(tmp_path))
    assert se.main(argv) != 0


# ------------------------------------------------- relecture adverse 10-23
@pytest.mark.parametrize("brut", [entete(chiffrement="cipher_null"), entete(zone="cipher_null"),
                                  entete(emplacements=2), entete(emplacements=0),
                                  "pas du json", json.dumps({"keyslots": {}})])
def test_entete_trafique_refuse_avant_de_donner_la_cle(tmp_path, brut):
    ouvrir(tmp_path, run_cle(avec_profils=True))
    run = run_cle(avec_profils=True)
    run.reponses["cryptsetup luksDump"] = brut
    with pytest.raises(se.Echec) as exc:
        ouvrir(tmp_path, run)
    assert "modifié" in exc.value.message
    assert not any(a[:2] == ["cryptsetup", "open"] for a, _ in run.appels)


def test_creation_au_nom_final_seulement_quand_complete(tmp_path):
    run = run_cle(avec_profils=True)
    vu = {}

    def mkfs(argv):
        point = se.dossiers(cfg(tmp_path))["profils"]
        vu["final_pendant_mkfs"] = os.path.exists(os.path.join(point, se.nom_fichier_profil(SUB)))
        return ""
    run.reponses["mkfs.ext4 -q -L profil"] = mkfs
    ouvrir(tmp_path, run)
    assert vu == {"final_pendant_mkfs": False}
    luks = [a for a, _ in run.appels if a[:2] == ["cryptsetup", "luksFormat"]][0]
    assert luks[-1].endswith(".luks.part") and "aes-xts-plain64" in luks


def test_creation_interrompue_retiree_au_passage_suivant(tmp_path):
    c = cfg(tmp_path)
    point = se.dossiers(c)["profils"]
    os.makedirs(point, exist_ok=True)
    orphelin = os.path.join(point, se.nom_fichier_profil(SUB) + ".part")
    open(orphelin, "w").close()
    neuf, _c = ouvrir(tmp_path, run_cle(avec_profils=True))
    assert neuf and not os.path.exists(orphelin)


def test_squelette_copie_apres_le_montage(tmp_path):
    run = run_cle(avec_profils=True)
    maison = tmp_path / "home" / NOM
    vu = {}

    def mount(argv):
        if argv[-1] == str(maison):
            vu["vide_au_montage"] = not (maison / ".bashrc").exists()
        return ""
    run.reponses["mount -t ext4 -o nosuid,nodev,noatime "] = mount
    ouvrir(tmp_path, run)
    assert vu == {"vide_au_montage": True} and (maison / ".bashrc").exists()


def test_profil_existant_du_bon_uid_pas_de_chown(tmp_path):
    ouvrir(tmp_path, run_cle(avec_profils=True), uid=0)
    run = run_cle(avec_profils=True)
    neuf, _c = ouvrir(tmp_path, run, uid=0)  # la maison appartient deja a root (uid 0)
    assert not neuf and "chown -R" not in run.commandes()


def test_maison_par_son_chemin_reel(tmp_path):
    # Fedora atomic : /home est un lien vers /var/home.
    (tmp_path / "var" / "home" / NOM).mkdir(parents=True)
    (tmp_path / "lien").symlink_to(tmp_path / "var" / "home")
    c = cfg(tmp_path, home_base=str(tmp_path / "lien"))
    cpt = pwd.struct_passwd((NOM, "x", 1, 1, "", str(tmp_path / "lien" / NOM), "/bin/bash"))
    assert se.maison_reelle(c, cpt) == str(tmp_path / "var" / "home" / NOM)
    sous = pwd.struct_passwd((NOM, "x", 1, 1, "", str(tmp_path / "lien" / NOM / "x"), "/bin/bash"))
    (tmp_path / "var" / "home" / NOM / "x").mkdir()
    with pytest.raises(se.Echec):
        se.maison_reelle(c, sous)


def test_sgdisk_avertissement_du_noyau_accepte():
    class Res:
        returncode = 4
        stdout = "Warning: The kernel is still using the old partition table.\nThe operation has completed successfully.\n"
        stderr = ""
    assert "completed" in se._sgdisk(["sgdisk", "-e", "/dev/sdb"], lambda *a, **k: Res())

    class Vrai:
        returncode = 4
        stdout = "Problem opening /dev/sdb"
        stderr = ""
    with pytest.raises(se.Echec):
        se._sgdisk(["sgdisk", "-e", "/dev/sdb"], lambda *a, **k: Vrai())


def test_demarrer_purge_les_depots_de_la_personne_precedente(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    for f in (se.POLITIQUE_PUBLIQUE, se.IDENTIFIANTS_NC):
        open(f, "w").write("de quelqu'un d'autre")
    os.makedirs(se.FLATPAK_DIR, exist_ok=True)
    open(os.path.join(se.FLATPAK_DIR, "remove"), "w").write("com.bitwarden.desktop\n")
    deposer_demande(c, "ephemere")
    se.demarrer(c, NOM, http=FauxHttp(politique=(503, "")), run=FauxRun(),
                modules=(FauxProvision(), FauxApply), getpwnam=annuaire_de(c, cpt))
    assert not os.path.exists(se.POLITIQUE_PUBLIQUE) and not os.path.exists(se.IDENTIFIANTS_NC)
    assert not os.path.exists(os.path.join(se.FLATPAK_DIR, "remove"))


def test_arreter_finit_les_processus_avant_de_fermer(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "persistant")
    se.demarrer(c, NOM, http=FauxHttp(), run=run_cle(avec_profils=True),
                modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt),
                attendre=lambda s: None, statvfs=statvfs_libre(64 << 20))
    ordre = []
    run = FauxRun()
    se.arreter(c, NOM, http=FauxHttp(), run=run, attendre=lambda s: None,
               fermer=lambda *a, **k: ordre.append("fermer"),
               demonter=lambda *a, **k: ordre.append("demonter"))
    cmds = [" ".join(a) for a, _ in run.appels]
    assert cmds[:3] == [f"systemctl stop bfos-live-agrandir@{NOM}.service",
                        f"loginctl disable-linger {NOM}", f"loginctl terminate-user {NOM}"]
    assert ordre == ["fermer", "demonter"]


def test_partition_demontee_quand_plus_aucun_profil():
    run = FauxRun()
    c = {"state_dir": "/run/bfos-live"}
    se.demonter_profils_si_libre(c, run=run, lister=lambda d: ["control"],
                                 monte=lambda p: p.endswith("profils"), existe=lambda p: False)
    assert [" ".join(a) for a, _ in run.appels] == ["umount /run/bfos-live/profils"]
    run2 = FauxRun()
    se.demonter_profils_si_libre(c, run=run2, lister=lambda d: [se.mapper("autre-1")],
                                 monte=lambda p: True)
    assert run2.appels == []


@pytest.mark.parametrize("sessions, arrete", [([], True), ([{"user": "autre"}], True),
                                              ([{"user": NOM}], False)])
def test_filet_sans_session_arrete_l_unite(tmp_path, sessions, arrete):
    run = FauxRun({"loginctl list-sessions": json.dumps(sessions)})
    assert se.garder(cfg(tmp_path), NOM, run=run) is arrete
    stop = ["systemctl", "stop", "--no-block", f"bfos-live-session@{NOM}.service"]
    assert (stop in [a for a, _ in run.appels]) is arrete


def test_regle_polkit_liste_fermee():
    regle = open(os.path.join(os.path.dirname(__file__), "..", "image", "files", "etc",
                              "polkit-1", "rules.d", "50-bfos-live-flatpak.rules")).read()
    for interdit in ("install-bundle", "configure-remote", "modify-repo", "app-uninstall",
                     'indexOf("org.freedesktop.Flatpak.")'):
        assert interdit not in regle.split("var BFOS_FLATPAK_PERMIS")[1]
    assert "set-self-linger" in regle and "Result.NO" in regle


# ------------------------------------------------------------- Odoo sans retaper
URL_LIEN = "https://erp.exemple.test/api/v1/policy/live/login-link"


@pytest.mark.parametrize("reponse, attendu", [
    ((200, {"url": "https://erp.exemple.test/odoo?bfos_login=J"}),
     "https://erp.exemple.test/odoo?bfos_login=J&bfos_auth=" + se.code_navigateur("AT")),
    ((200, {"url": "https://ailleurs.test/odoo?bfos_login=J"}), None),    # autre hote
    ((200, {"url": "https://erp.exemple.test/web/x?bfos_login=J"}), None),  # pas /odoo
    ((200, {"url": "https://erp.exemple.test/odoo?a=1\nX"}), None),       # saut de ligne
    ((200, {"url": "http://erp.exemple.test/odoo"}), None),
    ((403, {"error": "x"}), None),
    ((200, {}), None),
])
def test_lien_odoo(tmp_path, reponse, attendu):
    c = cfg(tmp_path, login_link_url=URL_LIEN)
    http = FauxHttp(lien=reponse)
    assert se.lien_odoo(c, "AT", http=http) == attendu
    assert http.appels == [("POST", URL_LIEN, "AT", {})]


def test_lien_odoo_sans_adresse_rien_demande(tmp_path):
    http = FauxHttp()
    assert se.lien_odoo(cfg(tmp_path, login_link_url=""), "AT", http=http) is None
    assert http.appels == []


def test_lien_depose_a_la_personne_hors_de_son_dossier(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    se.deposer_lien_odoo(c, NOM, cpt, "https://erp.exemple.test/odoo?bfos_login=A")
    se.deposer_lien_odoo(c, NOM, cpt, "https://erp.exemple.test/odoo?bfos_login=B")
    d = se.dossiers(c)["connexions"]
    f = os.path.join(d, NOM)
    st = os.stat(f)
    assert (st.st_uid, stat.S_IMODE(st.st_mode)) == (cpt.pw_uid, 0o400)
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o711 and os.stat(d).st_uid == 0
    assert open(f).read() == "https://erp.exemple.test/odoo?bfos_login=B\n"
    se.retirer_lien_odoo(c, NOM)
    assert not os.path.exists(f)


def test_demarrer_depose_le_lien_et_arreter_le_retire(tmp_path):
    c = cfg(tmp_path, login_link_url=URL_LIEN)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    se.demarrer(c, NOM, http=FauxHttp(), run=FauxRun(), modules=(FauxProvision(DEPOT), FauxApply),
                getpwnam=annuaire_de(c, cpt))
    f = os.path.join(se.dossiers(c)["connexions"], NOM)
    assert open(f).read().startswith("https://erp.exemple.test/odoo?bfos_login=")
    se.arreter(c, NOM, http=FauxHttp(), run=FauxRun(), attendre=lambda s: None,
               fermer=lambda *a, **k: None, demonter=lambda *a, **k: None)
    assert not os.path.exists(f)


def test_demarrer_sans_lien_odoo_continue(tmp_path):
    c = cfg(tmp_path, login_link_url=URL_LIEN)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    etat = se.demarrer(c, NOM, http=FauxHttp(lien=(503, {})), run=FauxRun(),
                       modules=(FauxProvision(DEPOT), FauxApply), getpwnam=annuaire_de(c, cpt))
    assert etat["mode"] == "ephemere"
    assert not os.path.exists(os.path.join(se.dossiers(c)["connexions"], NOM))


def test_code_navigateur_meme_empreinte_qu_authentik():
    # Vecteur calcule par la politique bfos-live-navigateur dans un Authentik
    # 2026.8.4 d'essai (jeton « tok-frais ») : les deux cotes doivent concorder.
    assert se.code_navigateur("tok-frais") == "KpQ4u47MkGq0ypHwn226F-_HCboxKUMzw76i4ckbQ3U"
    assert len(se.code_navigateur("x")) == 43


# ------------------------------------------------------------- bureau itinerant
from bfos_live import itinerant as it  # noqa: E402

from test_itinerant import FauxDAV, aller, popen_local  # noqa: E402

RACINE_DAV = "/remote.php/dav/files/Olivier/.bfos-live"


def itinerant_demarre(tmp_path, dav, cle_dav=None):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "itinerant")
    if cle_dav is not None:
        dav.fichiers[RACINE_DAV] = None
        dav.fichiers[RACINE_DAV + "/profil.bfosnc"] = aller(b"autre", cle=cle_dav)
        dav.etags[RACINE_DAV + "/profil.bfosnc"] = '"p"'
    http, bp, run = FauxHttp(), FauxProvision(DEPOT), FauxRun()
    kw = dict(http=http, run=run, modules=(bp, FauxApply), getpwnam=annuaire_de(c, cpt),
              nuage_de=lambda d: it.Nuage(d, ouvreur=dav), popen=popen_local,
              statvfs=lambda p: types.SimpleNamespace(f_bavail=1 << 30, f_frsize=4096))
    return c, cpt, http, bp, run, kw


def test_demarrer_itinerant_neuf_prend_le_verrou_et_lance_la_minuterie(tmp_path):
    dav = FauxDAV()
    c, _cpt, http, bp, run, kw = itinerant_demarre(tmp_path, dav)
    etat = se.demarrer(c, NOM, **kw)
    assert etat["mode"] == "itinerant" and etat["profil_neuf"] is True
    assert etat["itinerant"]["pret"] and etat["itinerant"]["verrou"]
    assert json.loads(dav.fichiers[RACINE_DAV + "/verrou.json"])["session"] == etat["itinerant"]["session"]
    assert any(u.endswith("/profile-key") for _m, u, _j, _c in http.appels)
    assert len(bp.appels) == 2, "un mot de passe pour root, un pour l'agent (bureau neuf)"
    assert f"systemctl start {se.UNITE_SAUVEGARDE.format(nom=NOM)}" in [" ".join(a) for a, _ in run.appels]
    assert "nextcloud" not in etat, "le mot de passe de l'agent vit dans le bureau, comme le persistant"


def test_itinerant_ouvert_ailleurs_refuse_puis_revoque(tmp_path):
    dav = FauxDAV()
    n = it.Nuage(DEPOT, ouvreur=dav)
    it.prendre_verrou(n, "AUTRE-SESSION")
    c, _cpt, http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    with pytest.raises(se.Echec) as exc:
        se.demarrer(c, NOM, **kw)
    assert "déjà ouvert sur un autre poste" in exc.value.message
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               getpwnam=kw["getpwnam"])
    assert json.loads(dav.fichiers[RACINE_DAV + "/verrou.json"])["session"] == "AUTRE-SESSION"
    assert any(m == "DELETE" and "apppassword" in u for m, u, _j, _c in http.appels)


def test_restauration_ratee_n_ecrase_jamais_le_bureau(tmp_path):
    dav = FauxDAV()
    c, _cpt, http, _bp, run, kw = itinerant_demarre(tmp_path, dav, cle_dav=bytes(32))
    avant = dav.fichiers[RACINE_DAV + "/profil.bfosnc"]
    with pytest.raises(se.Echec):
        se.demarrer(c, NOM, **kw)
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               popen=popen_local, getpwnam=kw["getpwnam"], attendre=lambda s: None)
    assert dav.fichiers[RACINE_DAV + "/profil.bfosnc"] == avant
    assert not [p for m, p in dav.journal if m == "PUT" and "profil.envoi" in p]
    assert RACINE_DAV + "/verrou.json" not in dav.fichiers, "verrou rendu"


@pytest.mark.skipif(not (shutil.which("tar") and shutil.which("zstd")), reason="tar et zstd requis")
def test_fermeture_envoie_puis_rend_le_verrou(tmp_path):
    dav = FauxDAV()
    c, cpt, http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    se.demarrer(c, NOM, **kw)
    (tmp_path / "home" / NOM / "Documents").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / NOM / "Documents" / "a.txt").write_text("garde-moi")
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               popen=popen_local, getpwnam=kw["getpwnam"], attendre=lambda s: None)
    profil = dav.fichiers[RACINE_DAV + "/profil.bfosnc"]
    assert profil.startswith(it.MAGIE)
    assert RACINE_DAV + "/verrou.json" not in dav.fichiers
    assert any(m == "DELETE" and "apppassword" in u for m, u, _j, _c in http.appels)
    assert f"systemctl stop {se.UNITE_SAUVEGARDE.format(nom=NOM)}" in [" ".join(a) for a, _ in run.appels]


def test_minuterie_refuse_un_bureau_non_restaure(tmp_path):
    c = cfg(tmp_path)
    d = se.dossiers(c)
    ph.dossier_sur(d["etat"], 0o700)
    ph._ecrire(os.path.join(d["etat"], NOM + ".json"),
               {"mode": "itinerant", "maison": str(tmp_path), "uid": 12345,
                "itinerant": {"depot": DEPOT, "pret": False, "session": "S", "verrou": '"e"',
                              "cle": base64.b64encode(CLE).decode()}}, mode=0o600)
    run, dav = FauxRun(), FauxDAV()
    assert se.sauvegarder(c, NOM, run=run, nuage_de=lambda d: it.Nuage(d, ouvreur=dav)) is False
    assert any(a[:1] == ["runuser"] and "notify-send" in a for a, _ in run.appels)
    assert dav.journal == [], "aucun echange avec Nextcloud pour un bureau non restaure"


def test_verrou_repris_ailleurs_rien_n_est_envoye(tmp_path):
    dav = FauxDAV()
    c, _cpt, http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    etat = se.demarrer(c, NOM, **kw)
    # Une autre session a repris le verrou (le nôtre semblait perimé).
    n = it.Nuage(DEPOT, ouvreur=dav)
    _e, v = n.lire_verrou()
    n.poser_verrou(dict(v, session="AUTRE", vu=v["vu"]), etag=n.lire_verrou()[0])
    chemin_etat = os.path.join(se.dossiers(c)["etat"], NOM + ".json")
    with pytest.raises(se.Echec) as exc:
        se.sauvegarder_itinerant(c, NOM, etat, chemin_etat, nuage_de=kw["nuage_de"], popen=popen_local,
                                 getpwnam=kw["getpwnam"])
    assert "ouvert ailleurs" in exc.value.message
    assert not [p for m, p in dav.journal if m == "PUT" and "profil.envoi" in p]


# ------------------------------------------------------------- mots de passe de root orphelins
class HttpNc(FauxHttp):
    """Revocation : statut par mot de passe (Basic), 200 par defaut."""

    def __init__(self, statuts=None, dav=None, **kw):
        super().__init__(**kw)
        self.statuts, self.revoques, self.dav, self.registre_vu = statuts or {}, [], dav, []

    def __call__(self, url, methode="GET", jeton=None, corps=None, entetes=None, delai=30):
        if "/apppassword" in url:
            mot = base64.b64decode(entetes["Authorization"].split()[1]).decode().split(":", 1)[1]
            self.appels.append((methode, url, jeton, corps))
            if self.dav is not None:
                self.registre_vu.append(registre(self.dav))
            statut = self.statuts.get(mot, 200)
            if isinstance(statut, Exception):
                raise statut
            if statut == 200:
                self.revoques.append(mot)
            return statut, ""
        return super().__call__(url, methode, jeton, corps, entetes, delai)


def registre(dav):
    return [d["app_password"] for d in it.lire_jetons(it.Nuage(DEPOT, ouvreur=dav), CLE)[1]]


def orphelin(dav, *mots):
    it.ecrire_jetons(it.Nuage(DEPOT, ouvreur=dav), CLE,
                     [dict(DEPOT, app_password=m) for m in mots], None)


def test_ouverture_note_son_mot_de_passe_et_revoque_les_orphelins(tmp_path):
    dav = FauxDAV()
    it.Nuage(DEPOT, ouvreur=dav).assurer_dossier()
    orphelin(dav, "ORPHELIN-1", "ORPHELIN-2")
    c, _cpt, _http, _bp, _run, kw = itinerant_demarre(tmp_path, dav)
    http = kw["http"] = HttpNc(statuts={"ORPHELIN-2": OSError("reseau")})
    se.demarrer(c, NOM, **kw)
    assert http.revoques == ["ORPHELIN-1"], "revoque avec ses propres identifiants"
    assert registre(dav) == ["ORPHELIN-2", "APP-PASS"], "l'echec reste note, celui-ci est note"


def test_orphelin_deja_mort_sort_du_registre(tmp_path):
    dav = FauxDAV()
    it.Nuage(DEPOT, ouvreur=dav).assurer_dossier()
    orphelin(dav, "MORT")
    c, _cpt, _http, _bp, _run, kw = itinerant_demarre(tmp_path, dav)
    kw["http"] = HttpNc(statuts={"MORT": 401})
    se.demarrer(c, NOM, **kw)
    assert registre(dav) == ["APP-PASS"]


def test_registre_injoignable_a_l_ouverture_refuse_et_rend_le_verrou(tmp_path):
    dav = FauxDAV()
    dav.pannes["PUT " + it.JETONS] = 507
    c, _cpt, http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    with pytest.raises(se.Echec):
        se.demarrer(c, NOM, **kw)
    assert RACINE_DAV + "/verrou.json" not in dav.fichiers
    assert not [p for m, p in dav.journal if m == "GET" and p.endswith("profil.bfosnc")], \
        "rien n'est restaure avec un mot de passe non note"


@pytest.mark.skipif(not (shutil.which("tar") and shutil.which("zstd")), reason="tar et zstd requis")
def test_fermeture_retire_du_registre_puis_revoque(tmp_path):
    dav = FauxDAV()
    c, _cpt, _http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    http = kw["http"] = HttpNc(dav=dav)
    se.demarrer(c, NOM, **kw)
    assert registre(dav) == ["APP-PASS"]
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               popen=popen_local, getpwnam=kw["getpwnam"], attendre=lambda s: None)
    assert registre(dav) == [] and http.revoques == ["APP-PASS"]
    # Retire AVANT de revoquer : apres, ce mot de passe ne pourrait plus ecrire.
    assert http.registre_vu == [[]]


@pytest.mark.skipif(not (shutil.which("tar") and shutil.which("zstd")), reason="tar et zstd requis")
def test_fermeture_sans_revocation_le_remet_au_registre(tmp_path):
    dav = FauxDAV()
    c, _cpt, _http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    http = kw["http"] = HttpNc(statuts={"APP-PASS": 503})
    se.demarrer(c, NOM, **kw)
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               popen=popen_local, getpwnam=kw["getpwnam"], attendre=lambda s: None)
    assert registre(dav) == ["APP-PASS"], "la prochaine ouverture le revoquera"


@pytest.mark.skipif(not (shutil.which("tar") and shutil.which("zstd")), reason="tar et zstd requis")
def test_fermeture_sans_reseau_laisse_le_registre_pour_la_suivante(tmp_path):
    dav = FauxDAV()
    c, _cpt, _http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    http = kw["http"] = HttpNc(statuts={"APP-PASS": OSError("reseau")})
    se.demarrer(c, NOM, **kw)
    avant = dict(dav.fichiers)

    class Coupe:
        def open(self, req, timeout=None):
            raise urllib.error.URLError("reseau coupe")

    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None,
               nuage_de=lambda d: it.Nuage(d, ouvreur=Coupe()),
               popen=popen_local, getpwnam=kw["getpwnam"], attendre=lambda s: None)
    assert dav.fichiers[RACINE_DAV + "/" + it.JETONS] == avant[RACINE_DAV + "/" + it.JETONS]
    assert registre(dav) == ["APP-PASS"]
    # Le lendemain, une autre cle : l'orphelin est revoque, le nouveau note.
    c2, _cpt, _h, _bp, _run, kw2 = itinerant_demarre(tmp_path / "lendemain", dav)
    http2 = kw2["http"] = HttpNc()
    kw2["modules"] = (FauxProvision(dict(DEPOT, app_password="NOUVEAU")), FauxApply)
    it.Nuage(DEPOT, ouvreur=dav).retirer_verrou(None)
    se.demarrer(c2, NOM, **kw2)
    assert "APP-PASS" in http2.revoques
    assert registre(dav) == ["NOUVEAU"]


def test_restauration_ratee_retire_son_mot_de_passe_du_registre(tmp_path):
    dav = FauxDAV()
    c, _cpt, _http, _bp, run, kw = itinerant_demarre(tmp_path, dav, cle_dav=bytes(32))
    http = kw["http"] = HttpNc()
    with pytest.raises(se.Echec):
        se.demarrer(c, NOM, **kw)
    assert registre(dav) == []
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               popen=popen_local, getpwnam=kw["getpwnam"], attendre=lambda s: None)
    assert http.revoques == ["APP-PASS"]


def test_verrou_refuse_ne_touche_pas_au_registre(tmp_path):
    dav = FauxDAV()
    n = it.Nuage(DEPOT, ouvreur=dav)
    it.prendre_verrou(n, "AUTRE-SESSION")
    orphelin(dav, "DE-L-AUTRE")
    avant = dav.fichiers[RACINE_DAV + "/" + it.JETONS]
    c, _cpt, _http, _bp, run, kw = itinerant_demarre(tmp_path, dav)
    http = kw["http"] = HttpNc()
    with pytest.raises(se.Echec):
        se.demarrer(c, NOM, **kw)
    se.arreter(c, NOM, http=http, run=run, finir=lambda *a, **k: None, nuage_de=kw["nuage_de"],
               getpwnam=kw["getpwnam"])
    assert dav.fichiers[RACINE_DAV + "/" + it.JETONS] == avant
    assert http.revoques == ["APP-PASS"], "le sien seulement, jamais celui de la session qui tient le verrou"



# ------------------------------------------------------------- lot B (essai du 2026-10-26)
import bfos_apply  # noqa: E402

POL_PREFS = dict(POLITIQUE, install={"timezone": "Pacific/Auckland", "keymap": "ca",
                                     "x_layout": "ca", "x_variant": "multix"})


def test_demarrer_publie_ses_etapes(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    vues = []
    vrai = se.annoncer

    def espion(cfg_, nom, etape, texte):
        vues.append(etape)
        vrai(cfg_, nom, etape, texte)
    se.annoncer, ancien = espion, se.annoncer
    try:
        se.demarrer(c, NOM, http=FauxHttp(), run=FauxRun(), modules=(FauxProvision(DEPOT), FauxApply),
                    getpwnam=annuaire_de(c, cpt))
    finally:
        se.annoncer = ancien
    assert vues == [1, 2, 3, 4, 5]
    lu = ph.lire_progres(os.path.join(c["state_dir"], "progres", NOM + ".json"))
    assert lu == {"etape": 5, "total": se.ETAPES, "texte": "Préparation de tes applications…"}
    assert oct(os.stat(os.path.join(c["state_dir"], "progres")).st_mode & 0o777) == "0o700"


def test_preferences_fuseau_et_clavier(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    (pathlib.Path(cpt.pw_dir) / ".config").mkdir()
    run = FauxRun()
    se.appliquer_preferences(POL_PREFS, bfos_apply, cpt, cpt.pw_dir, run=run)
    assert ["timedatectl", "set-timezone", "Pacific/Auckland"] in [a for a, _ in run.appels]
    assert '"XkbVariant" "multix"' in open(se.CLAVIER_SYSTEME).read()
    assert "KEYMAP=ca" in open(se.VCONSOLE).read()
    kx = pathlib.Path(cpt.pw_dir) / ".config" / "kxkbrc"
    assert "LayoutList=ca" in kx.read_text() and "VariantList=multix" in kx.read_text()


def test_kxkbrc_de_la_personne_garde(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    kx = pathlib.Path(cpt.pw_dir) / ".config" / "kxkbrc"
    kx.parent.mkdir()
    kx.write_text("[Layout]\nLayoutList=us\n")
    se.appliquer_preferences(POL_PREFS, bfos_apply, cpt, cpt.pw_dir, run=FauxRun())
    assert kx.read_text() == "[Layout]\nLayoutList=us\n"


def test_kxkbrc_ne_suit_pas_un_lien(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    cible = tmp_path / "ailleurs"
    cible.mkdir()
    (pathlib.Path(cpt.pw_dir) / ".config").symlink_to(cible)
    se.appliquer_preferences(POL_PREFS, bfos_apply, cpt, cpt.pw_dir, run=FauxRun())
    assert not (cible / "kxkbrc").exists()
    lien = pathlib.Path(cpt.pw_dir) / ".config2"
    lien.mkdir()
    (lien / "kxkbrc").symlink_to(tmp_path / "vise")
    assert se.ecrire_sous(cpt.pw_dir, ".config2/kxkbrc", "x", cpt) is False
    assert not (tmp_path / "vise").exists()


@pytest.mark.parametrize("fuseau", ["", "../../etc/shadow", "Pacific/Nulle_part", "Pacific/../Pacific/Auckland",
                                    "Pacific/Auckland; rm -rf /"])
def test_fuseau_douteux_ignore(tmp_path, fuseau):
    cpt = compte(tmp_path, uid=os.getuid())
    run = FauxRun()
    se.appliquer_preferences(dict(POLITIQUE, install={"timezone": fuseau}), bfos_apply, cpt,
                             cpt.pw_dir, run=run)
    assert not [a for a, _ in run.appels if a[0] == "timedatectl"]
    assert not os.path.exists(se.CLAVIER_SYSTEME), "sans clavier dans la politique, rien n'est ecrit"


def test_demarrer_applique_les_preferences(tmp_path):
    c = cfg(tmp_path)
    cpt = compte(tmp_path)
    deposer_demande(c, "ephemere")
    run = FauxRun()

    class Apply(FauxApply):
        render_x11_keymap_conf = staticmethod(bfos_apply.render_x11_keymap_conf)
        render_vconsole_conf = staticmethod(bfos_apply.render_vconsole_conf)
        render_kxkbrc = staticmethod(bfos_apply.render_kxkbrc)
    se.demarrer(c, NOM, http=FauxHttp(politique=POL_PREFS), run=run,
                modules=(FauxProvision(DEPOT), Apply), getpwnam=annuaire_de(c, cpt))
    assert ["timedatectl", "set-timezone", "Pacific/Auckland"] in [a for a, _ in run.appels]
    assert os.path.exists(se.CLAVIER_SYSTEME)


@pytest.mark.skipif(os.geteuid() != 0, reason="chown vers un autre compte : root")
def test_kxkbrc_au_nom_de_la_personne(tmp_path):
    cpt = compte(tmp_path, uid=12345)
    (pathlib.Path(cpt.pw_dir) / ".config").mkdir()
    se.appliquer_preferences(POL_PREFS, bfos_apply, cpt, cpt.pw_dir, run=FauxRun())
    st = os.stat(pathlib.Path(cpt.pw_dir) / ".config" / "kxkbrc")
    assert (st.st_uid, st.st_gid) == (12345, 12345) and st.st_mode & 0o777 == 0o644


def test_un_echec_n_emporte_pas_le_reste(tmp_path, monkeypatch):
    cpt = compte(tmp_path, uid=os.getuid())
    (pathlib.Path(cpt.pw_dir) / ".config").mkdir()
    (tmp_path / "X11").write_text("un fichier, pas un dossier")
    run = FauxRun(echecs=["timedatectl"])
    se.appliquer_preferences(POL_PREFS, bfos_apply, cpt, cpt.pw_dir, run=run)
    assert (pathlib.Path(cpt.pw_dir) / ".config" / "kxkbrc").exists()
    assert "KEYMAP=ca" in open(se.VCONSOLE).read()


def test_clavier_seulement_avec_x_layout(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    (pathlib.Path(cpt.pw_dir) / ".config").mkdir()
    se.appliquer_preferences(dict(POLITIQUE, install={"x_layout": "us"}), bfos_apply, cpt,
                             cpt.pw_dir, run=FauxRun())
    assert "LayoutList=us" in (pathlib.Path(cpt.pw_dir) / ".config" / "kxkbrc").read_text()



def test_langue_de_la_politique(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    (pathlib.Path(cpt.pw_dir) / ".config").mkdir()
    se.appliquer_preferences(dict(POLITIQUE, install={"locale": "fr_CA.UTF-8"}), bfos_apply, cpt,
                             cpt.pw_dir, run=FauxRun())
    rc = (pathlib.Path(cpt.pw_dir) / ".config" / "plasma-localerc").read_text()
    assert "LANG=fr_CA.UTF-8" in rc and "LANGUAGE=fr_CA:fr" in rc
    assert open(se.LOCALE_CONF).read() == "LANG=fr_CA.UTF-8\n"


@pytest.mark.parametrize("locale", ["", "fr_CA", "fr_CA.UTF-8\nLANGUAGE=x", "../x.UTF-8", "fr_CA.UTF-8 ; id",
                                    "fr_CA.UTF-8\n", "fr.UTF-8"])
def test_langue_douteuse_ignoree(tmp_path, locale):
    cpt = compte(tmp_path, uid=os.getuid())
    (pathlib.Path(cpt.pw_dir) / ".config").mkdir()
    se.appliquer_preferences(dict(POLITIQUE, install={"locale": locale}), bfos_apply, cpt,
                             cpt.pw_dir, run=FauxRun())
    assert not (pathlib.Path(cpt.pw_dir) / ".config" / "plasma-localerc").exists()
    assert not os.path.exists(se.LOCALE_CONF)


def test_langue_choisie_par_la_personne_gardee(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    rc = pathlib.Path(cpt.pw_dir) / ".config" / "plasma-localerc"
    rc.parent.mkdir()
    rc.write_text("[Translations]\nLANGUAGE=en_US\n")
    se.appliquer_preferences(dict(POLITIQUE, install={"locale": "fr_CA.UTF-8"}), bfos_apply, cpt,
                             cpt.pw_dir, run=FauxRun())
    assert rc.read_text() == "[Translations]\nLANGUAGE=en_US\n"



def test_localerc_ecrit_par_plasma_complete(tmp_path):
    """startplasma ecrit [Formats] seul au premier demarrage : ce n'est pas un choix."""
    cpt = compte(tmp_path, uid=os.getuid())
    rc = pathlib.Path(cpt.pw_dir) / ".config" / "plasma-localerc"
    rc.parent.mkdir()
    rc.write_text("[Formats]\nLANG=C.UTF-8\n")
    se.appliquer_preferences(dict(POLITIQUE, install={"locale": "fr_CA.UTF-8"}), bfos_apply, cpt,
                             cpt.pw_dir, run=FauxRun())
    texte = rc.read_text()
    assert texte.startswith("[Formats]\nLANG=C.UTF-8\n") and texte.rstrip().endswith("LANGUAGE=fr_CA:fr")


def test_localerc_lien_dur_laisse(tmp_path):
    cpt = compte(tmp_path, uid=os.getuid())
    rc = pathlib.Path(cpt.pw_dir) / ".config" / "plasma-localerc"
    rc.parent.mkdir()
    ailleurs = tmp_path / "ailleurs"
    ailleurs.write_text("[Formats]\nLANG=C\n")
    os.link(ailleurs, rc)
    se.appliquer_preferences(dict(POLITIQUE, install={"locale": "fr_CA.UTF-8"}), bfos_apply, cpt,
                             cpt.pw_dir, run=FauxRun())
    assert ailleurs.read_text() == "[Formats]\nLANG=C\n"


def test_user_dirs_du_squelette_deux_cles():
    texte = (pathlib.Path(__file__).resolve().parent.parent / "image/files/etc/skel/.config/user-dirs.dirs").read_text()
    cles = [l.split("=")[0] for l in texte.splitlines() if l.startswith("XDG_")]
    assert cles == ["XDG_DESKTOP_DIR", "XDG_DOWNLOAD_DIR"]


@pytest.mark.skipif(os.geteuid() != 0, reason="fichier d'un autre compte : root")
def test_localerc_lien_vers_un_fichier_de_root_refuse(tmp_path):
    cpt = compte(tmp_path, uid=12345)
    cfg_dir = pathlib.Path(cpt.pw_dir) / ".config"
    cfg_dir.mkdir()
    cible = tmp_path / "passwd"
    cible.write_text("[Formats]\nLANG=C\n")
    (cfg_dir / "plasma-localerc").symlink_to(cible)
    assert se.completer_localerc(cpt.pw_dir, "[Translations]\nLANGUAGE=fr\n", cpt) is False
    assert cible.read_text() == "[Formats]\nLANG=C\n"


@pytest.mark.skipif(os.geteuid() != 0, reason="fichier d'un autre compte : root")
def test_localerc_d_un_autre_compte_refuse(tmp_path):
    cpt = compte(tmp_path, uid=12345)
    rc = pathlib.Path(cpt.pw_dir) / ".config" / "plasma-localerc"
    rc.parent.mkdir()
    rc.write_text("[Formats]\nLANG=C\n")
    os.chown(rc, 0, 0)
    assert se.completer_localerc(cpt.pw_dir, "[Translations]\nLANGUAGE=fr\n", cpt) is False
    assert rc.read_text() == "[Formats]\nLANG=C\n"


# ------------------------------------------------------------------ cle generique : domaine retenu (2026-10-27)
def test_retenir_organisation(tmp_path):
    c = {"organisation": "Boréal", "domaine": "Boreal.Example"}
    assert se.retenir_organisation(c, str(tmp_path))
    f = tmp_path / "organisation"
    assert f.read_text() == "boreal.example\n" and stat.S_IMODE(f.stat().st_mode) == 0o600
    assert not se.retenir_organisation({"domaine": "boreal.example"}, str(tmp_path / "x")), \
        "cle configuree : rien a retenir"
    assert not se.retenir_organisation(c, None)
    f.unlink()
    os.symlink("/etc/passwd", str(f))
    assert not se.retenir_organisation(c, str(tmp_path)), "jamais a travers un lien"


def test_lire_organisation_retenue_en_lecture_seule(tmp_path):
    sortie = tmp_path / "etat" / "domaine-retenu"
    lsblk = json.dumps({"blockdevices": [{"name": "/dev/sdz", "children": [
        {"name": "/dev/sdz3", "type": "part", "partlabel": "BFOS_PROFILS", "fstype": "ext4"}]}]})

    def monter(argv):
        (pathlib.Path(argv[-1]) / "organisation").write_text("boreal.example\n")
        return ""
    run = FauxRun({"lsblk": lsblk, "mount": monter})
    d = se.lire_organisation_retenue({}, run=run, sortie=str(sortie),
                                    support={"genre": "gpt", "disque": "/dev/sdz"})
    assert d == "boreal.example" and sortie.read_text() == "boreal.example\n"
    montage = next(a for a, _e in run.appels if a[0] == "mount")
    opts = montage[montage.index("-o") + 1]
    assert "ro," in opts and "noload" in opts, "lecture seule, sans rejeu du journal"
    assert any(a[0] == "umount" for a, _e in run.appels)
    assert not any(a[0] in ("sgdisk", "mkfs.ext4") for a, _e in run.appels), "rien n'est cree"


def test_lire_organisation_sans_partition(tmp_path):
    lsblk = json.dumps({"blockdevices": [{"name": "/dev/sdz", "children": []}]})
    run = FauxRun({"lsblk": lsblk})
    assert se.lire_organisation_retenue({}, run=run, sortie=str(tmp_path / "r"),
                                       support={"genre": "gpt", "disque": "/dev/sdz"}) is None
    assert not any(a[0] == "mount" for a, _e in run.appels)


# ------------------------------------------------------------------ relecture adverse n° 1 (2026-10-27)
def test_profil_d_avant_repris_seulement_avec_la_cle_de_cette_organisation(tmp_path):
    ident = "https://auth.exemple.test/application/o/x/\0sub-1"
    neuf = tmp_path / se.nom_fichier_profil(ident)
    ancien = tmp_path / se.nom_fichier_profil("sub-1")
    ancien.write_bytes(b"LUKS")
    refus = FauxRun({"cryptsetup luksDump": ENTETE_OK}, echecs=("cryptsetup open --test-passphrase",))
    assert not se.reprendre_profil_d_avant(str(neuf), ident, b"k", run=refus)
    assert ancien.exists() and not neuf.exists()
    ok = FauxRun({"cryptsetup luksDump": ENTETE_OK})
    assert se.reprendre_profil_d_avant(str(neuf), ident, b"k", run=ok)
    assert neuf.exists() and not ancien.exists()
    assert ("--test-passphrase" in a for a, _e in ok.appels)
    # Cle configuree (identite = sub) : rien a reprendre.
    assert not se.reprendre_profil_d_avant(str(tmp_path / "x.luks"), "sub-1", b"k", run=ok)


def test_cle_generique_ne_retire_jamais_d_appli(tmp_path):
    class Apply:
        @staticmethod
        def render_flatpak_list(ids, cle):
            return "\n".join(ids)
    pol = {"apps": {"install": ["org.exemple.App", "--system", "pas un id", "org.x"],
                    "remove": ["com.bitwarden.desktop"]}}
    se.appliquer_applis(pol, Apply, run=FauxRun(), dossier=str(tmp_path), est_installe=lambda a: True,
                        generique=True)
    assert (tmp_path / "install").read_text() == "org.exemple.App"
    assert (tmp_path / "remove").read_text() == ""
    se.appliquer_applis(pol, Apply, run=FauxRun(), dossier=str(tmp_path), est_installe=lambda a: True)
    assert (tmp_path / "remove").read_text() == "com.bitwarden.desktop"


# ------------------------------------------- caches en memoire vive (persistant)
class Compte:
    def __init__(self, uid, gid):
        self.pw_uid, self.pw_gid = uid, gid


def _faux_mount(vus, rc=0):
    def monter(fd, donnees):
        vus.append((donnees, os.readlink(f"/proc/self/fd/{fd}")))
        return None if rc == 0 else "Invalid argument"
    return monter


def test_caches_en_memoire_cree_et_monte_par_descripteur(tmp_path):
    maison = tmp_path / "m"
    (maison / ".var" / "app" / "com.brave.Browser").mkdir(parents=True)
    vus = []
    moi = Compte(os.getuid(), os.getgid())
    n = se.caches_en_memoire(str(maison), moi, monter=_faux_mount(vus),
                             applis=["com.brave.Browser", "org.mozilla.firefox"])
    assert n == 3
    cibles = [cible for _a, cible in vus]
    assert cibles == [str(maison / ".cache"), str(maison / ".var/app/com.brave.Browser/cache"),
                      str(maison / ".var/app/org.mozilla.firefox/cache")]
    opts = vus[0][0]
    assert f"uid={moi.pw_uid}" in opts and "mode=0700" in opts and "size=20%" in opts
    assert "size=10%" in vus[1][0]
    assert stat.S_IMODE(os.stat(maison / ".cache").st_mode) == 0o700


def test_caches_en_memoire_ne_suit_aucun_lien(tmp_path):
    maison = tmp_path / "m"
    ailleurs = tmp_path / "etc"
    (maison / ".var" / "app").mkdir(parents=True)
    ailleurs.mkdir()
    (maison / ".cache").symlink_to(ailleurs)
    (maison / ".var" / "app" / "com.brave.Browser").symlink_to(ailleurs)
    vus = []
    n = se.caches_en_memoire(str(maison), Compte(os.getuid(), os.getgid()), monter=_faux_mount(vus),
                             applis=["com.brave.Browser"])
    assert n == 0 and vus == []
    assert not os.listdir(ailleurs)


def test_caches_en_memoire_dossier_d_un_autre_laisse(tmp_path):
    maison = tmp_path / "m"
    (maison / ".cache").mkdir(parents=True)
    vus = []
    n = se.caches_en_memoire(str(maison), Compte(os.getuid() + 1, os.getgid()),
                             monter=_faux_mount(vus), applis=[])
    assert n == 0 and vus == []


def test_caches_en_memoire_echec_de_mount_jamais_bloquant(tmp_path):
    maison = tmp_path / "m"
    maison.mkdir()
    assert se.caches_en_memoire(str(maison), Compte(os.getuid(), os.getgid()),
                                monter=_faux_mount([], rc=32), applis=[]) == 0


def test_caches_en_memoire_vrai_montage_par_descripteur(tmp_path):
    """Le vrai mount(2) (root, conteneur privilegie) : mount(8) 2.41 refusait la cible."""
    if os.geteuid() != 0:
        pytest.skip("root exige")
    maison = tmp_path / "m"
    maison.mkdir()
    capacites = int(next(l for l in open("/proc/self/status") if l.startswith("CapEff:")).split()[1], 16)
    if not capacites & (1 << 21):
        pytest.skip("sans CAP_SYS_ADMIN (conteneur non privilegie)")
    n = se.caches_en_memoire(str(maison), Compte(1234, 1234), applis=[])
    assert n == 1, "avec CAP_SYS_ADMIN, le montage doit reussir"
    try:
        ligne = next(l for l in open("/proc/self/mountinfo") if f" {maison}/.cache " in l)
        assert "bfos-cache" in ligne and "nosuid" in ligne and "nodev" in ligne
        assert os.stat(maison / ".cache").st_uid == 1234
    finally:
        subprocess.run(["umount", str(maison / ".cache")])


def test_ancien_cache_vide_sans_suivre_de_lien(tmp_path):
    maison = tmp_path / "m"
    vieux = maison / ".cache" / "brave" / "Cache"
    vieux.mkdir(parents=True)
    (vieux / "data_0").write_text("x")
    ailleurs = tmp_path / "garde"
    ailleurs.mkdir()
    (ailleurs / "precieux").write_text("y")
    (maison / ".cache" / "lien").symlink_to(ailleurs)
    vus = []
    n = se.caches_en_memoire(str(maison), Compte(os.getuid(), os.getgid()),
                             monter=_faux_mount(vus), applis=[])
    assert n == 1 and os.listdir(maison / ".cache") == []
    assert (ailleurs / "precieux").read_text() == "y", "le lien est retire, pas suivi"
