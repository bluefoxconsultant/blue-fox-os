"""bluefox-wipe : ce que le poste fait d'un ordre de depart (#23941, #27365).

Les cas qui comptent, et pourquoi :

- UN ORDRE QUI NE NOMME PAS CE POSTE N'EFFACE RIEN. C'est la garde centrale :
  une politique recopiee d'une autre machine, ou un fichier bricole, doit
  ressortir sans qu'un seul `cryptsetup` ne soit lance — et le refus REMONTE au
  serveur, sinon la fiche reste sur « ordre remis » indefiniment.
- ON N'EFFACE QUE LES VOLUMES DE CE POSTE. Le disque externe chiffre branche au
  moment du passage horaire n'est pas dans le mandat.
- AUCUN VOLUME RETENU N'EST PAS UN SUCCES : rendre « fait » sans avoir efface
  ferait basculer la fiche sur un poste intact.
- RIEN NE SE DETRUIT AVANT D'AVOIR VERIFIE QU'ON POURRA LE DIRE : l'adresse du
  compte rendu se valide avant le premier geste, et le marqueur d'intention
  s'ecrit avant le premier `luksErase`.
- LE COMPTE RENDU PASSE AVANT LE REDEMARRAGE : un poste redemarre ne reparlera
  plus, et « efface » ne doit pas se confondre avec « jamais rallume ». Si le
  compte rendu ne passe pas, le poste coupe ses sessions, garde un marqueur, et
  ne redemarre PAS. S'il est REFUSE par le serveur (409, 400), il garde une
  trace locale et ne redemarre pas non plus : cet etat exige un humain.
- UN EFFACEMENT PARTIEL QUI A EMPORTE LA RACINE SE DIT « disque condamne », pas
  « echec » : l'operateur ne doit pas chercher un probleme de reseau sur un
  disque mort.
- LA CIBLE DE LA PURGE VIENT DE L'ORDRE, jamais de la politique. Un poste
  partage sans usager vise ne purge RIEN : la branche qui vide tout le cache ne
  doit jamais etre le defaut.

⚠️ Stdlib uniquement (lane `test-wizard` de la CI).
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
WIPE_PATH = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-wipe"


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


wipe = _load(WIPE_PATH, "bluefox_wipe")

MACHINE = {
    "machine_uuid": "uuid-1",
    "endpoint": "https://example.com/api/v1/policy/machine",
    "token": "SECRET",
    "hostname": "bf-olivier",
}
# L'ordre tel qu'Odoo le sert : il porte SA cible (contrat 2 de #23941).
ORDRE = {"mode": "wipe", "machine_uuid": "uuid-1", "order_id": "ord-1",
         "seat_only": False, "target_login": "personne@exemple.test"}
# Un ordre d'un serveur qui ne sert pas encore la cible.
ORDRE_SANS_CIBLE = {"mode": "wipe", "machine_uuid": "uuid-1",
                    "order_id": "ord-1"}


@pytest.fixture(autouse=True)
def _poste_de_papier(tmp_path, monkeypatch):
    """Aucun essai ne touche les fichiers du poste de developpement.

    ⚠️ Sans cette garde, un essai lirait le /etc/crypttab et le
    /var/lib/sss/db du poste de dev, et ecrirait son marqueur dans son
    /var/lib/bluefox-welcome. Les quatre chemins se relisent a l'appel, pas a
    la definition — c'est ce qui rend cette redirection possible.
    """
    monkeypatch.setattr(wipe, "MACHINE_JSON", str(tmp_path / "machine.json"))
    monkeypatch.setattr(wipe, "MARQUEUR",
                        str(tmp_path / "departure-pending.json"))
    monkeypatch.setattr(wipe, "TRACE",
                        str(tmp_path / "departure-sans-suite.json"))
    monkeypatch.setattr(wipe, "MARQUEUR_REFUSE",
                        str(tmp_path / "departure-marqueur-refuse.json"))
    monkeypatch.setattr(wipe, "CRYPTTAB", str(tmp_path / "crypttab"))
    monkeypatch.setattr(wipe, "SSSD_DB", str(tmp_path / "sss" / "db"))


def _marqueur():
    return Path(wipe.MARQUEUR)


def _trace():
    return Path(wipe.TRACE)


def _marqueur_refuse():
    return Path(wipe.MARQUEUR_REFUSE)


def _ecrire_marqueur(**champs):
    base = {"order_id": "ord-1", "result": "done", "detail": "d",
            "reboot": True, "machine_uuid": "uuid-1"}
    base.update(champs)
    _marqueur().write_text(json.dumps(base))
    return base


def _ecrire_crypttab(*uuids, options="discard", sources=()):
    """Un /etc/crypttab de papier. `uuids` sont servis en « UUID= » ; `sources`
    passent telles quelles (/dev/..., PARTUUID=...). `options` est le 4e champ,
    celui qui porte `noauto` — le discriminant de la ceinture."""
    lignes = ["# <cible> <source> <cle> <options>"]
    for rang, uuid in enumerate(uuids):
        lignes.append(f"luks-{rang} UUID={uuid} none {options}")
    for rang, source in enumerate(sources):
        lignes.append(f"autre-{rang} {source} none {options}")
    Path(wipe.CRYPTTAB).write_text("\n".join(lignes) + "\n")


def _bases_sssd(*noms):
    base = Path(wipe.SSSD_DB)
    base.mkdir(parents=True, exist_ok=True)
    for nom in noms:
        (base / nom).write_text("x")
    return base


# --------------------------------------------------------- lsblk de papier
def _lsblk(*disques):
    return json.dumps({"blockdevices": list(disques)})


# Le disque du systeme : sa partition chiffree porte la racine (/sysroot sur
# une image ostree) et n'est PAS dans crypttab (deverrouillage par le TPM).
DISQUE_SYSTEME = {
    "name": "/dev/sda", "fstype": None, "rm": False, "hotplug": False,
    "children": [
        {"name": "/dev/sda1", "fstype": "vfat", "mountpoints": ["/boot/efi"]},
        {"name": "/dev/sda2", "fstype": "crypto_LUKS", "uuid": "uuid-sys",
         "rm": False, "hotplug": False, "mountpoints": [None], "children": [
             {"name": "/dev/mapper/luks-sys", "fstype": "btrfs",
              "mountpoints": ["/sysroot", "/var", None]}]}]}

# Un second volume interne, celui-la declare dans crypttab et monte ailleurs.
DISQUE_DONNEES = {
    "name": "/dev/sdc", "fstype": None, "rm": False, "hotplug": False,
    "children": [
        {"name": "/dev/sdc1", "fstype": "crypto_LUKS", "uuid": "uuid-donnees",
         "rm": False, "hotplug": False, "children": [
             {"name": "/dev/mapper/luks-donnees", "fstype": "ext4",
              "mountpoints": ["/srv/donnees"]}]}]}

# Le disque externe chiffre branche au moment du passage horaire.
DISQUE_EXTERNE = {
    "name": "/dev/sdb", "fstype": None, "rm": True, "hotplug": True,
    "children": [
        {"name": "/dev/sdb1", "fstype": "crypto_LUKS", "uuid": "uuid-externe",
         "rm": True, "hotplug": True}]}

# Le poste bi-NVMe : DEUX disques internes, tous deux dans crypttab, tous deux
# annonces HOTPLUG=1 par le noyau parce que le bus PCIe NVMe l'est. C'est la
# configuration que B1 a trouvee : la ceinture RM/HOTPLUG ecartait le second,
# et le compte rendu disait « done, 1/1 » sur un poste dont ce volume gardait
# tous ses emplacements de cles.
NVME_SYSTEME = {
    "name": "/dev/nvme0n1", "fstype": None, "rm": False, "hotplug": True,
    "children": [
        {"name": "/dev/nvme0n1p1", "fstype": "vfat",
         "mountpoints": ["/boot/efi"]},
        {"name": "/dev/nvme0n1p2", "fstype": "crypto_LUKS",
         "uuid": "uuid-nvme-sys", "rm": False, "hotplug": True,
         "mountpoints": [None], "children": [
             {"name": "/dev/mapper/luks-nvme-sys", "fstype": "btrfs",
              "mountpoints": ["/sysroot", "/var", None]}]}]}
NVME_DONNEES = {
    "name": "/dev/nvme1n1", "fstype": None, "rm": False, "hotplug": True,
    "children": [
        {"name": "/dev/nvme1n1p1", "fstype": "crypto_LUKS",
         "uuid": "uuid-nvme-donnees", "rm": False, "hotplug": True,
         "children": [
             {"name": "/dev/mapper/luks-nvme-donnees", "fstype": "ext4",
              "mountpoints": ["/srv/donnees"]}]}]}

_LSBLK_SYSTEME = _lsblk(DISQUE_SYSTEME)


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runs(list):
    """Double de subprocess.run qui note les commandes et rend ce qu'on veut."""

    def __init__(self, reponses=None, espion=None):
        super().__init__()
        self.reponses = reponses or {}
        self.espion = espion

    def __call__(self, argv, **kwargs):
        ligne = " ".join(argv)
        if self.espion is not None:
            self.espion(ligne)
        self.append(list(argv))
        for cle, proc in self.reponses.items():
            if cle in ligne:
                return proc
        return _Proc()

    def lances(self, mot):
        return [c for c in self if mot in " ".join(c)]


def _opener(status=200, exc=None, vu=None):
    def ouvrir(req, timeout=None):
        if vu is not None:
            vu.append(req)
        if exc is not None:
            raise exc
        return _RespHttp(status)
    return ouvrir


class _RespHttp:
    def __init__(self, status):
        self.status = status

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sans_attente(_secondes):
    """time.sleep neutralise : les essais ne dorment pas 5 secondes."""


def _corps(req):
    return json.loads(req.data.decode())


def _refus_d_ecriture(*_a, **_k):
    """Un /var plein, ou remonte en lecture seule."""
    raise OSError("read-only file system")


# ------------------------------------------------------------- validation
def test_un_ordre_adresse_a_un_autre_poste_est_refuse():
    mode, raison = wipe.valider_ordre(
        dict(ORDRE, machine_uuid="uuid-du-voisin"), MACHINE)
    assert mode is None
    assert "uuid-du-voisin" in raison


def test_un_ordre_etranger_n_efface_rien_du_tout():
    """La garde se mesure a ce qui N'EST PAS lance."""
    runs = _Runs()
    code = wipe.executer(dict(ORDRE, machine_uuid="uuid-du-voisin"), MACHINE,
                         run=runs, opener=_opener(), dormir=_sans_attente)
    assert code == 2
    assert runs.lances("cryptsetup") == []
    assert runs.lances("sss_cache") == []
    assert runs.lances("reboot") == []


@pytest.mark.parametrize("ordre", [
    None, [], "wipe", {},
    {"mode": "format", "machine_uuid": "uuid-1", "order_id": "o"},
    {"mode": "WIPE", "machine_uuid": "uuid-1", "order_id": "o"},
    {"mode": "wipe", "machine_uuid": "uuid-1"},
    {"mode": "wipe", "order_id": "o"},
    {"mode": "wipe", "machine_uuid": "", "order_id": "o"},
])
def test_un_ordre_mal_forme_est_refuse(ordre):
    mode, raison = wipe.valider_ordre(ordre, MACHINE)
    assert mode is None
    assert raison


@pytest.mark.parametrize("ordre", [
    {"mode": "wipe", "order_id": "o"},
    {"mode": "wipe", "machine_uuid": "", "order_id": "o"},
])
def test_un_ordre_sans_machine_uuid_le_dit_plutot_que_de_comparer(ordre):
    """La garde en profondeur, et son MOTIF.

    Sans elle, la comparaison suivante refuse encore (None != "uuid-1") et
    l'essai general reste vert : une passe de mutation l'a trouvee muette
    (2026-11-08). Le motif est ce qui distingue « ordre sans destinataire » de
    « ordre adresse ailleurs », et c'est lui qu'on lit dans le journal.
    """
    mode, raison = wipe.valider_ordre(ordre, MACHINE)
    assert mode is None
    assert raison == "ordre sans machine_uuid"


@pytest.mark.parametrize("order_id", [12, 12.5, ["ord-1"], {"id": "ord-1"},
                                      True, "   "])
def test_un_order_id_qui_n_est_pas_une_chaine_est_refuse(order_id):
    """W18. L'identifiant repart TEL QUEL dans le compte rendu : un nombre ou
    une liste y devient autre chose, et le serveur ne reconnait plus son propre
    ordre — ou pire, en reconnait un autre."""
    mode, raison = wipe.valider_ordre(dict(ORDRE, order_id=order_id), MACHINE)
    assert mode is None
    assert raison


def test_un_ordre_valide_passe():
    assert wipe.valider_ordre(ORDRE, MACHINE) == ("wipe", "")
    assert wipe.valider_ordre(dict(ORDRE, mode="lock"), MACHINE)[0] == "lock"


@pytest.mark.parametrize("endpoint", [
    "https://e.test/api/v1/policy/me", "https://e.test/autre", "", None])
def test_un_endpoint_sans_adresse_de_compte_rendu_refuse_l_ordre(endpoint):
    """Contrat 6 : rien ne se detruit avant d'avoir verifie qu'on pourra le
    dire. Sans cette garde, le disque etait efface, le compte rendu impossible,
    le poste jamais redemarre et la fiche restait « ordre remis » pour
    toujours."""
    mode, raison = wipe.valider_ordre(ORDRE, dict(MACHINE, endpoint=endpoint))
    assert mode is None
    assert "compte rendu" in raison or "endpoint" in raison


def test_un_endpoint_non_conforme_n_efface_rien_et_le_dit(tmp_path):
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    machine = dict(MACHINE, endpoint="https://e.test/api/v1/policy/me")
    code = wipe.executer(ORDRE, machine, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 2
    assert runs.lances("cryptsetup") == []
    assert runs.lances("lsblk") == []
    assert not _marqueur().exists()


def test_un_endpoint_en_clair_n_efface_rien():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    machine = dict(MACHINE, endpoint="http://e.test/api/v1/policy/machine")
    code = wipe.executer(ORDRE, machine, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 2
    assert runs.lances("cryptsetup") == []


def test_un_ordre_refuse_est_rapporte_au_serveur():
    """W11. Sans ce compte rendu, un poste reenrole laisse la fiche sur
    « ordre remis » indefiniment. L'`order_id` repart TEL QU'IL EST ARRIVE :
    l'adresse du compte rendu doit etre joignable sans ordre valide."""
    vu = []
    code = wipe.executer(dict(ORDRE, machine_uuid="uuid-du-voisin"), MACHINE,
                         run=_Runs(), opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 2
    (req,) = vu
    assert _corps(req)["result"] == "refused"
    assert _corps(req)["order_id"] == "ord-1"
    assert "uuid-du-voisin" in _corps(req)["detail"]


def test_un_refus_sans_identifiant_lisible_ne_rapporte_rien():
    """Sans identifiant, il n'y a rien a fermer cote serveur."""
    vu = []
    code = wipe.executer({"mode": "wipe", "machine_uuid": "uuid-1"}, MACHINE,
                         run=_Runs(), opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 2
    assert vu == []


# ------------------------------------------------------------- volumes
def test_les_volumes_luks_se_lisent_en_profondeur():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert [v["dev"] for v in retenus] == ["/dev/sda2"]
    assert retenus[0]["racine"] is True
    assert ecartes == []


def test_un_lsblk_muet_ne_rend_aucun_volume():
    for proc in (_Proc(returncode=1, stderr="boom"), _Proc(stdout="pas du json")):
        assert wipe.volumes_luks(run=_Runs({"lsblk": proc})) == ([], [])


def test_un_lsblk_qui_ne_connait_pas_une_colonne_retombe_sur_l_ancien_jeu():
    """⚠️ lsblk REFUSE TOUTE la commande sur une colonne inconnue : MOUNTPOINTS
    demande util-linux 2.37, PARTUUID 2.22. Avec un seul jeu de colonnes,
    `volumes_luks` etait une fonction MORTE sur un hote plus ancien — « aucun
    volume retenu », donc echec sur tous les postes de ce genre. Ce serait la
    troisieme fois que ce chantier pose une garde qui tombe partout.

    Et c'est ce repli qui rend le `MOUNTPOINT` singulier atteignable : sans
    lui, il ne pouvait jamais servir (F15)."""
    Path(wipe.CRYPTTAB).write_text("# rien\n")
    ancien = _lsblk({"name": "/dev/sda", "children": [
        {"name": "/dev/sda2", "fstype": "crypto_LUKS", "uuid": "uuid-sys",
         "children": [{"name": "/dev/mapper/luks-sys", "fstype": "btrfs",
                       "mountpoint": "/sysroot"}]}]})

    class _LsblkVieux(_Runs):
        def __call__(self, argv, **kwargs):
            super().__call__(argv, **kwargs)
            colonnes = argv[argv.index("--output") + 1]
            if "MOUNTPOINTS" in colonnes or "PARTUUID" in colonnes:
                return _Proc(returncode=1,
                             stderr="lsblk: unknown column: PARTUUID")
            return _Proc(stdout=ancien)

    runs = _LsblkVieux()
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert [(v["dev"], v["motif"]) for v in retenus] == [("/dev/sda2",
                                                          "racine")]
    assert ecartes == []
    assert len(runs.lances("lsblk")) == 2


def test_un_disque_externe_chiffre_n_est_jamais_efface():
    """W1, le defaut bloquant. `volumes_luks` n'avait AUCUN filtre : le disque
    de sauvegarde du labo branche au moment du passage horaire etait detruit
    hors de tout mandat, et le compte rendu disait « done, 2/2 ».

    Ici le disque externe n'est PAS dans crypttab : il est hors du mandat, et
    la fraction le compte quand meme au denominateur (B1)."""
    _ecrire_crypttab("uuid-donnees")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME, DISQUE_EXTERNE,
                                               DISQUE_DONNEES))})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert sorted(v["dev"] for v in retenus) == ["/dev/sda2", "/dev/sdc1"]
    assert [(e["dev"], e["motif"]) for e in ecartes] == [
        ("/dev/sdb1", "hors de ce poste")]

    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME, DISQUE_EXTERNE,
                                               DISQUE_DONNEES))})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.EFFACE
    efface = [c[-1] for c in runs.lances("luksErase")]
    assert "/dev/sdb1" not in efface
    assert sorted(efface) == ["/dev/sda2", "/dev/sdc1"]
    # ⚠️ B1 : la fraction se compte sur les volumes chiffres VUS. Avec
    # `len(retenus)` au denominateur, l'ecarte disparaissait des DEUX cotes.
    assert "2/3" in detail
    assert "/dev/sdb1 (hors de ce poste)" in detail
    # Ecarte sans mandat : rien ne manque au poste, donc pas d'« INCOMPLET ».
    assert "INCOMPLET" not in detail


def test_un_poste_bi_nvme_efface_SES_DEUX_volumes():
    """🔴 B1, le defaut bloquant du tour 2.

    Les deux NVMe sont dans crypttab et tous deux annonces HOTPLUG=1 — le bus
    PCIe NVMe est amovible, pas les disques. Avec RM/HOTPLUG pour discriminant,
    le second etait ecarte, disparaissait des deux cotes de la fraction, et le
    poste rapportait « done, 1/1 volume(s) efface(s) ». Odoo archivait,
    affichait « Efface », levait le marqueur de sequestre, et un gardien
    retirait la phrase de secours du seul volume encore lisible — celui qui
    garde l'emplacement de cle de la personne qui part, qui en a la phrase.
    """
    _ecrire_crypttab("uuid-nvme-sys", "uuid-nvme-donnees")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(NVME_SYSTEME, NVME_DONNEES))})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert sorted(v["dev"] for v in retenus) == ["/dev/nvme0n1p2",
                                                 "/dev/nvme1n1p1"]
    assert ecartes == []

    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(NVME_SYSTEME, NVME_DONNEES))})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.EFFACE
    assert sorted(c[-1] for c in runs.lances("luksErase")) == [
        "/dev/nvme0n1p2", "/dev/nvme1n1p1"]
    assert "2/2" in detail
    assert "INCOMPLET" not in detail


def test_une_entree_noauto_est_ecartee_et_le_detail_le_dit():
    """B1, le bon discriminant. Ce que la ceinture vise est nomme dans la
    conception : « un disque de sauvegarde chiffre externe, souvent en
    `noauto` ». C'est un signal que le POSTE possede, ecrit par qui l'a
    installe, et il ne depend d'aucun bus."""
    Path(wipe.CRYPTTAB).write_text(
        "luks-sys UUID=uuid-sys none discard\n"
        "sauvegarde UUID=uuid-externe none noauto,nofail\n")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_EXTERNE))})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert [v["dev"] for v in retenus] == ["/dev/sda2"]
    (ecarte,) = ecartes
    assert ecarte["dev"] == "/dev/sdb1"
    assert "noauto" in ecarte["motif"]
    assert ecarte["crypttab"] is True


def test_un_volume_de_ce_poste_ecarte_ne_se_rapporte_jamais_en_done_nu():
    """🔴 B1, l'autre moitie. Un volume ecarte qui est une entree de crypttab
    appartient a ce poste : son detail doit COMMENCER par « INCOMPLET », en
    tete pour survivre a la troncature a 255 caracteres du compte rendu.

    On ne rapporte pas `failed` pour autant : ecarter un disque de sauvegarde
    externe est le comportement VOULU, et un echec sur tout poste qui en porte
    un serait une fonction morte, pas une garde."""
    Path(wipe.CRYPTTAB).write_text(
        "luks-sys UUID=uuid-sys none discard\n"
        "sauvegarde UUID=uuid-donnees none noauto\n")
    vu = []
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES))})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 0
    detail = _corps(vu[0])["detail"]
    assert detail.startswith("INCOMPLET : 1 volume(s) non traite(s)")
    assert "/dev/sdc1" in detail
    assert "1/2" in detail


@pytest.mark.parametrize("source,noeud", [
    ("/dev/sdc1", {}),
    ("/dev/disk/by-uuid/uuid-donnees", {}),
    ("PARTUUID=part-donnees", {"partuuid": "part-donnees"}),
    ("/dev/disk/by-partuuid/part-donnees", {"partuuid": "part-donnees"}),
])
def test_les_autres_formes_de_source_de_crypttab_sont_reconnues(source, noeud):
    """🔴 B1. `uuids_crypttab` ne reconnaissait QUE « UUID= » et jetait le
    reste : un poste dont crypttab nomme son volume en /dev/... ou en PARTUUID=
    voyait son propre volume classe « hors de ce poste », donc intact, et le
    compte rendu disait « done » quand meme. Le depot traite deja les deux
    formes ailleurs (bluefox-tpm-enroll : `UUID=*` ET `/dev/*`)."""
    _ecrire_crypttab(sources=[source])
    disque = json.loads(json.dumps(DISQUE_DONNEES))
    disque["children"][0].update(noeud)
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(disque))})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert [v["dev"] for v in retenus] == ["/dev/sdc1"]
    assert [v["motif"] for v in retenus] == ["crypttab"]
    assert ecartes == []


def test_une_source_de_crypttab_non_reconnue_se_journalise(capsys):
    """Une forme qu'on ne sait pas raccrocher ne doit pas passer en silence :
    le volume n'aura pas de mandat, et c'est exactement ce qu'il faut lire."""
    _ecrire_crypttab(sources=["un-nom-de-cible-sans-forme"])
    wipe.volumes_luks(run=_Runs({"lsblk": _Proc(stdout=_lsblk())}))
    assert "source non reconnue" in capsys.readouterr().err


def test_un_volume_qui_remonte_deux_fois_n_a_qu_un_verdict():
    """M8. LUKS sur un RAID : md0 apparait sous CHAQUE disque membre (et en
    multipath, sous chaque chemin). ⚠️ Les deux occurrences ne portent pas les
    memes indices : lsblk ne repete pas toujours l'UUID ni le porteur du
    montage sur la seconde. Le verdict se prenait en DESCENDANT, donc le volume
    atterrissait dans les DEUX tables — efface ET liste comme ecarte — selon
    l'ordre des chemins, et il comptait deux fois au denominateur.

    Les indices de tous les chemins se fusionnent donc, et la decision se
    prend apres le parcours : un volume, un verdict."""
    _ecrire_crypttab("uuid-raid")
    complet = {"name": "/dev/md0", "fstype": "crypto_LUKS",
               "uuid": "uuid-raid",
               "children": [{"name": "/dev/mapper/luks-raid",
                             "fstype": "btrfs",
                             "mountpoints": ["/sysroot"]}]}
    # Le second chemin, tel que lsblk le rend souvent : le meme noeud, sans
    # son UUID ni ses enfants.
    nu = {"name": "/dev/md0", "fstype": "crypto_LUKS"}
    arbre = _lsblk(
        {"name": "/dev/sda", "rm": False, "hotplug": False,
         "children": [complet]},
        {"name": "/dev/sdb", "rm": True, "hotplug": True, "children": [nu]})
    retenus, ecartes = wipe.volumes_luks(run=_Runs({"lsblk":
                                                    _Proc(stdout=arbre)}))
    assert [(v["dev"], v["motif"]) for v in retenus] == [("/dev/md0",
                                                          "racine")]
    assert ecartes == []

    runs = _Runs({"lsblk": _Proc(stdout=arbre)})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.EFFACE
    # Un seul luksErase, et une fraction qui ne compte pas le volume deux fois.
    assert runs.lances("luksErase") == [
        ["cryptsetup", "luksErase", "--batch-mode", "/dev/md0"]]
    assert "1/1" in detail
    assert "ecartes" not in detail


def test_un_volume_hors_crypttab_qui_porte_la_racine_est_efface():
    """W1, l'autre bord du filtre : la racine peut etre absente de crypttab
    (cle TPM, deverrouillage par l'initramfs). La filtrer sur le seul crypttab
    donnerait un poste qu'on ne peut plus effacer du tout."""
    Path(wipe.CRYPTTAB).write_text("# rien\n")
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    retenus, _ecartes = wipe.volumes_luks(run=runs)
    assert [v["dev"] for v in retenus] == ["/dev/sda2"]
    assert retenus[0]["motif"] == "racine"


def test_un_volume_interne_hors_crypttab_et_sans_racine_est_ecarte():
    Path(wipe.CRYPTTAB).write_text("# rien\n")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_DONNEES))})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert retenus == []
    assert [e["motif"] for e in ecartes] == ["hors de ce poste"]


@pytest.mark.parametrize("drapeaux", [
    {"rm": True, "hotplug": False}, {"rm": False, "hotplug": True},
    {"rm": "1", "hotplug": "0"}, {"rm": "0", "hotplug": "1"}])
def test_rm_et_hotplug_se_journalisent_mais_ne_decident_plus(drapeaux, capsys):
    """🔴 B1 : RM/HOTPLUG N'EST PLUS UN VERDICT, sur aucune entree.

    lsblk les rend en booleen en --json et en « 0 »/« 1 » ailleurs ; les deux
    formes se lisent encore, pour le journal. Mais un volume de crypttab reste
    retenu malgre eux : sur une entree non-racine, le signal est aussi faux que
    sur la racine — un NVMe interne se declare tres souvent HOTPLUG=1 parce que
    le bus PCIe l'est, et le noyau dit vrai sur le BUS, pas sur la propriete du
    disque. C'est le motif de la reprise du tour 1, qui ne valait que pour la
    racine et laissait le defaut en place partout ailleurs.
    """
    _ecrire_crypttab("uuid-donnees")
    disque = dict(DISQUE_DONNEES, **drapeaux)
    disque["children"] = [dict(DISQUE_DONNEES["children"][0], **drapeaux)]
    retenus, ecartes = wipe.volumes_luks(
        run=_Runs({"lsblk": _Proc(stdout=_lsblk(disque))}))
    assert [v["dev"] for v in retenus] == ["/dev/sdc1"]
    assert ecartes == []
    assert "marque amovible" in capsys.readouterr().err


def test_l_amovibilite_du_disque_couvre_ses_partitions(capsys):
    """lsblk porte RM sur le DISQUE ; la partition chiffree est son enfant.
    L'heritage se lit encore — pour le journal, plus pour le verdict."""
    _ecrire_crypttab("uuid-donnees")
    disque = {"name": "/dev/sdc", "rm": True, "children": [
        {"name": "/dev/sdc1", "fstype": "crypto_LUKS", "uuid": "uuid-donnees"}]}
    retenus, _ecartes = wipe.volumes_luks(
        run=_Runs({"lsblk": _Proc(stdout=_lsblk(disque))}))
    assert [v["dev"] for v in retenus] == ["/dev/sdc1"]
    assert "/dev/sdc1 est marque amovible" in capsys.readouterr().err


def test_un_lsblk_ancien_rend_un_mountpoint_au_singulier():
    """F15. Le repli sur `MOUNTPOINT` (singulier) ne servait jamais : on
    demande MOUNTPOINTS a lsblk. Il reste parce qu'un lsblk anterieur a
    util-linux 2.37 ne connait que le singulier, et parce que sans lui la
    racine d'un tel poste ne serait pas reconnue — donc rien ne serait efface.
    Il est maintenant mesure plutot que suppose."""
    Path(wipe.CRYPTTAB).write_text("# rien\n")
    disque = {"name": "/dev/sda", "children": [
        {"name": "/dev/sda2", "fstype": "crypto_LUKS", "uuid": "uuid-sys",
         "children": [{"name": "/dev/mapper/luks-sys", "fstype": "btrfs",
                       "mountpoint": "/sysroot"}]}]}
    retenus, _ = wipe.volumes_luks(
        run=_Runs({"lsblk": _Proc(stdout=_lsblk(disque))}))
    assert [(v["dev"], v["motif"]) for v in retenus] == [("/dev/sda2",
                                                          "racine")]


def test_un_crypttab_illisible_laisse_la_racine_seule():
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES))})
    retenus, _ = wipe.volumes_luks(run=runs, crypttab="/absent/crypttab")
    assert [v["dev"] for v in retenus] == ["/dev/sda2"]


def test_aucun_volume_retenu_est_un_echec():
    """⚠️ Rendre « fait » sans avoir efface ferait basculer la fiche en
    « efface » sur un poste intact."""
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk())})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.ECHEC
    assert "aucun volume LUKS de ce poste retenu" in detail
    assert runs.lances("cryptsetup") == []


@pytest.mark.parametrize("drapeau", ["rm", "hotplug"])
def test_le_volume_de_la_racine_marque_amovible_est_quand_meme_efface(
        capsys, drapeau):
    """⚠️ La racine gagne sur RM/HOTPLUG.

    Un NVMe interne se declare tres souvent HOTPLUG=1 : le bus PCIe NVMe est
    amovible, pas le disque. L'ecarter de ce chef rendait l'effacement
    inoperant sur une bonne part du parc — un « echec diagnosticable » qui
    tombe sur tous les postes n'est pas une garde, c'est une fonction morte, et
    une VM qui expose du virtio ne l'aurait meme pas vu. Quand les deux indices
    se contredisent, on le journalise, et c'est tout.
    """
    disque = dict(DISQUE_SYSTEME, **{drapeau: True})
    disque["children"] = [dict(e) for e in DISQUE_SYSTEME["children"]]
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(disque))})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.EFFACE
    assert runs.lances("cryptsetup") == [
        ["cryptsetup", "luksErase", "--batch-mode", "/dev/sda2"]]
    assert "1/1" in detail
    journal = capsys.readouterr().err
    assert "marque amovible" in journal


def test_une_entree_noauto_non_racine_est_ecartee_meme_sans_rm():
    """La ceinture tient la ou elle sert, et sur le bon signal : un disque de
    sauvegarde chiffre inscrit dans crypttab en `noauto` n'a rien a faire dans
    un effacement de depart, qu'il s'annonce amovible ou pas (une baie eSATA
    s'annonce RM=0)."""
    _ecrire_crypttab("uuid-sys", options="discard")
    with open(wipe.CRYPTTAB, "a") as fh:
        fh.write("sauvegarde UUID=uuid-donnees none noauto\n")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES))})
    retenus, ecartes = wipe.volumes_luks(run=runs)
    assert [v["dev"] for v in retenus] == ["/dev/sda2"]
    assert [e["dev"] for e in ecartes] == ["/dev/sdc1"]
    assert "noauto" in ecartes[0]["motif"]


def test_chaque_volume_retenu_est_efface_sans_question():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.EFFACE
    assert runs.lances("cryptsetup") == [
        ["cryptsetup", "luksErase", "--batch-mode", "/dev/sda2"]]
    assert "1/1" in detail


def test_un_effacement_partiel_qui_emporte_la_racine_condamne_le_disque():
    """W4. La racine est morte : dire « echec » envoie l'operateur chercher un
    probleme de reseau sur un poste qui ne redemarrera plus."""
    _ecrire_crypttab("uuid-donnees")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES)),
                  "/dev/sdc1": _Proc(returncode=1, stderr="busy")})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.CONDAMNE
    assert "disque condamne" in detail
    assert "/dev/sdc1" in detail


def test_un_effacement_partiel_qui_epargne_la_racine_reste_un_echec():
    _ecrire_crypttab("uuid-donnees")
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES)),
                  "/dev/sda2": _Proc(returncode=1, stderr="busy")})
    etat, detail = wipe.effacer_volumes(run=runs)
    assert etat == wipe.ECHEC
    assert "condamne" not in detail


# --------------------------------------------------------- compte rendu
def test_l_adresse_du_compte_rendu_se_deduit_de_l_endpoint():
    assert wipe.url_rapport("https://e.test/api/v1/policy/machine") == \
        "https://e.test/api/v1/policy/departure-done"
    assert wipe.url_rapport("https://e.test/api/v1/policy/machine/") == \
        "https://e.test/api/v1/policy/departure-done"


@pytest.mark.parametrize("endpoint", [
    "", None, "https://e.test/api/v1/policy/me", "https://e.test/autre"])
def test_un_endpoint_inattendu_ne_donne_aucune_adresse(endpoint):
    assert wipe.url_rapport(endpoint) == ""


def test_le_compte_rendu_ne_part_pas_en_clair():
    machine = dict(MACHINE, endpoint="http://e.test/api/v1/policy/machine")
    etat, note = wipe.rendre_compte(machine, "ord-1", "done")
    assert etat == wipe.RAPPORT_PERMANENT
    assert "https" in note


def test_le_compte_rendu_porte_le_jeton_de_la_machine_et_l_ordre():
    vu = []
    etat, _note = wipe.rendre_compte(MACHINE, "ord-1", "done", "2 volumes",
                                     opener=_opener(vu=vu))
    assert etat == wipe.RAPPORT_OK
    (req,) = vu
    assert req.get_header("Authorization") == "Bearer bfos-machine SECRET"
    assert _corps(req) == {
        "order_id": "ord-1", "result": "done", "detail": "2 volumes"}


def test_un_409_ne_se_reessaie_pas_et_n_est_pas_un_succes():
    """W7. Le serveur ne reconnait plus cet ordre : insister n'aiderait pas, et
    le prendre pour un succes jetait le marqueur d'un disque reellement
    detruit."""
    exc = urllib.error.HTTPError("u", 409, "conflit", {}, None)
    essais = []

    def ouvrir(req, timeout=None):
        essais.append(req)
        raise exc

    etat, note = wipe.rendre_compte_avec_essais(
        MACHINE, "ord-1", "done", opener=ouvrir, dormir=_sans_attente)
    assert etat == wipe.RAPPORT_CONFLIT
    assert "409" in note
    assert len(essais) == 1


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_un_refus_definitif_ne_brule_pas_dix_secondes(code):
    """W22. Un 400 etait reessaye a l'infini : 10 s a chaque passage horaire,
    et le redemarrage empeche pour toujours."""
    essais, dodos = [], []

    def ouvrir(req, timeout=None):
        essais.append(req)
        raise urllib.error.HTTPError("u", code, "non", {}, None)

    etat, _note = wipe.rendre_compte_avec_essais(
        MACHINE, "ord-1", "done", opener=ouvrir, dormir=dodos.append)
    assert etat == wipe.RAPPORT_PERMANENT
    assert len(essais) == 1
    assert dodos == []


@pytest.mark.parametrize("code", [500, 502, 429, 408])
def test_un_serveur_qui_hoquete_se_reessaie(code):
    essais = []

    def ouvrir(req, timeout=None):
        essais.append(req)
        raise urllib.error.HTTPError("u", code, "plus tard", {}, None)

    etat, _note = wipe.rendre_compte_avec_essais(
        MACHINE, "ord-1", "done", opener=ouvrir, dormir=_sans_attente)
    assert etat == wipe.RAPPORT_PASSAGER
    assert len(essais) == wipe.ESSAIS_RAPPORT


def test_un_serveur_injoignable_se_reessaie_puis_abandonne():
    essais = []

    def ouvrir(req, timeout=None):
        essais.append(req)
        raise urllib.error.URLError("hors ligne")

    etat, _note = wipe.rendre_compte_avec_essais(
        MACHINE, "ord-1", "done", opener=ouvrir, dormir=_sans_attente)
    assert etat == wipe.RAPPORT_PASSAGER
    assert len(essais) == wipe.ESSAIS_RAPPORT


def test_un_jeton_qui_ne_tient_pas_dans_un_en_tete_ne_leve_pas():
    """W6. `add_header`, puis l'envoi, levent ValueError / UnicodeEncodeError
    sur un jeton non latin-1 ou porteur d'un saut de ligne. Attrapee la ou elle
    nait, l'exception ne sort plus du chemin destructeur."""
    exc = UnicodeEncodeError("latin-1", "€", 0, 1, "hors de range(256)")
    etat, note = wipe.rendre_compte(MACHINE, "ord-1", "done",
                                    opener=_opener(exc=exc))
    assert etat == wipe.RAPPORT_PERMANENT
    assert note


# --------------------------------------------------------------- purge
def test_une_adresse_complete_est_normalisee_en_forme_courte():
    """W17 / contrat 2. sssd nomme court ; Odoo sert une adresse. Un
    `sss_cache -u personne@exemple.test` ne designe personne et rend 0 : la
    purge disait « fait » sans avoir rien purge."""
    runs = _Runs()
    ok, _detail = wipe.purger_cache(seat_only=True,
                                    target_login="eleve@ecole.test", run=runs)
    assert ok is True
    assert runs.lances("sss_cache") == [["sss_cache", "-u", "eleve"]]
    assert runs.lances("getent") == [["getent", "passwd", "eleve"]]


@pytest.mark.parametrize("login", ["-E", "-u autre", "--help"])
def test_un_login_qui_commence_par_un_tiret_est_ecarte(login):
    """F14. La cible vient d'un champ libre cote Odoo. Ce n'est pas une
    injection — l'ordre ne passe par aucun interpreteur — mais
    `sss_cache -u -E` ferait lire « -E » comme une OPTION de `sss_cache`, donc
    purger tout le cache sur un poste partage : exactement le geste que le mode
    cible existe pour ne pas faire."""
    assert wipe.nom_court(login) == ""
    runs = _Runs()
    ok, detail = wipe.purger_cache(seat_only=True, target_login=login,
                                   run=runs)
    assert ok is False
    assert "AUCUNE purge" in detail
    assert runs == []


def test_un_usager_vise_inconnu_du_poste_est_un_echec():
    """🔴 E1 (a). `sss_cache -u` rend 0 MEME quand la cible ne designe
    personne : son code ne distingue pas « cache invalide » de « aucun compte
    de ce nom ». La branche rendait donc « done » — fiche « Execute et confirme
    par le poste » — sur un ordre qui n'a rien ferme. Un ordre qui ne designe
    personne n'est pas un succes."""
    runs = _Runs({"getent": _Proc(returncode=2)})
    ok, detail = wipe.purger_cache(seat_only=True,
                                   target_login="parti@ecole.test", run=runs)
    assert ok is False
    assert "inconnu de ce poste" in detail
    assert runs.lances("sss_cache") == []


def test_le_mode_cible_retire_le_hache_de_la_seule_cible():
    """🔴 E1 (b). `sss_cache -u` invalide le cache mais ne retire PAS le mot de
    passe hache : la fenetre de `offline_max_days` restait ouverte, et
    `loginctl lock-sessions` ne faisait que verrouiller un ecran que la
    personne visee deverrouille avec son mot de passe intact."""
    base = _bases_sssd("cache_bf.ldb", "config.ldb")
    runs = _Runs()
    ok, detail = wipe.purger_cache(seat_only=True,
                                   target_login="eleve@ecole.test", run=runs)
    assert ok is True
    (ldb,) = runs.lances("ldbmodify")
    assert ldb == ["ldbmodify", "-H", str(base / "cache_bf.ldb")]
    assert "hache retire de 1 base(s)" in detail
    assert "NON fermee" not in detail
    # ⚠️ config.ldb n'est pas un cache d'usagers : on n'y touche pas. Et AUCUNE
    # base n'est supprimee — c'est toute la difference avec le poste personnel.
    assert sorted(p.name for p in base.iterdir()) == ["cache_bf.ldb",
                                                      "config.ldb"]


def test_un_hache_non_retire_le_dit_en_tete_du_detail():
    """🔴 E1 (c). Si l'outil manque de l'image, la fenetre hors ligne reste
    OUVERTE. Le detail doit le dire, en clair et en tete, parce qu'il part sur
    le reseau et s'inscrit sur la fiche : c'est la seule facon pour l'operateur
    de savoir que le verrouillage est incomplet. (Limite au CHANGELOG.)"""
    _bases_sssd("cache_bf.ldb")
    runs = _Runs({"ldbmodify": _Proc(returncode=127, stderr="not found")})
    ok, detail = wipe.purger_cache(seat_only=True,
                                   target_login="eleve@ecole.test", run=runs)
    # Le cache EST invalide : l'ordre a fait ce qu'il pouvait faire, et ecarter
    # tout poste dont l'image n'a pas ldb-tools serait une fonction morte.
    assert ok is True
    assert detail.startswith("fenetre hors ligne NON fermee pour la cible")
    assert "mot de passe hache reste en cache" in detail


def test_un_poste_sans_cache_na_pas_de_hache_a_retirer():
    runs = _Runs()
    ok, detail = wipe.purger_cache(seat_only=True,
                                   target_login="eleve@ecole.test", run=runs)
    assert ok is True
    assert "aucun hache a retirer" in detail
    assert runs.lances("ldbmodify") == []


def test_un_poste_partage_sans_cible_ne_purge_rien():
    """W2, le defaut bloquant. La portee se deduisait de la politique
    (`if seat and login:`), et un poste partage porte `"user": {"login": ""}` :
    la branche « vider TOUT le cache » se prenait donc precisement la ou elle
    ne devait jamais se prendre. Tous les emprunteurs perdaient leur cache."""
    base = _bases_sssd("cache_bf.ldb", "config.ldb")
    runs = _Runs()
    ok, detail = wipe.purger_cache(seat_only=True, target_login="", run=runs)
    assert ok is False
    assert "AUCUNE purge" in detail
    assert runs == []
    assert sorted(p.name for p in base.iterdir()) == ["cache_bf.ldb",
                                                      "config.ldb"]


def test_un_poste_personnel_purge_tout_comme_avant():
    base = _bases_sssd("cache_bf.ldb", "config.ldb")
    runs = _Runs()
    ok, detail = wipe.purger_cache(seat_only=False,
                                   target_login="personne@exemple.test",
                                   run=runs)
    assert ok is True
    assert runs.lances("sss_cache -E")
    assert runs.lances("stop sssd.service")
    assert runs.lances("start sssd.service")
    assert list(base.iterdir()) == []
    assert "2/2" in detail


def test_un_ordre_sans_cible_le_dit_dans_le_detail():
    """Code defensif : la cle absente n'est pas un login vide. Sans cible, pas
    de purge ciblee possible — et la fiche ne doit pas laisser croire a une
    purge d'emprunteur."""
    _bases_sssd("cache_bf.ldb")
    ok, detail = wipe.purger_cache(run=_Runs())
    assert ok is True
    assert "ordre sans cible" in detail


def test_le_login_de_la_personne_ne_voyage_pas_dans_le_detail():
    """W23 (Loi 25). Le detail part sur le reseau et s'inscrit sur la fiche."""
    ok, detail = wipe.purger_cache(seat_only=True,
                                  target_login="eleve@ecole.test",
                                  run=_Runs())
    assert ok is True
    assert "eleve" not in detail
    assert "ecole.test" not in detail


def test_un_stop_sssd_en_echec_ne_rend_pas_done():
    """W8. Le verdict ne tenait qu'au code de `sss_cache` : un demon toujours
    debout reecrit ses bases derriere nous, et la fenetre hors ligne n'est pas
    demontrablement fermee."""
    _bases_sssd("cache_bf.ldb")
    runs = _Runs({"stop sssd": _Proc(returncode=1, stderr="refus")})
    ok, detail = wipe.purger_cache(run=runs)
    assert ok is False
    assert "stop sssd → 1" in detail


def test_un_repertoire_de_bases_dont_rien_ne_part_ne_rend_pas_done(monkeypatch):
    """W8, l'autre moitie : des bases toujours la, c'est une session hors ligne
    toujours possible."""
    _bases_sssd("cache_bf.ldb")

    def refus(_chemin):
        raise OSError("read-only file system")

    monkeypatch.setattr(wipe.os, "unlink", refus)
    ok, detail = wipe.purger_cache(run=_Runs())
    assert ok is False
    assert "0/1" in detail


def test_un_repertoire_de_bases_absent_n_est_pas_un_echec():
    """Un poste sans cache sssd n'a aucune fenetre hors ligne a fermer."""
    ok, detail = wipe.purger_cache(run=_Runs())
    assert ok is True
    assert "aucune base de cache" in detail


# ------------------------------------------------------------- execution
def test_un_effacement_confirme_redemarre_et_ne_laisse_pas_de_marqueur():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 0
    assert runs.lances("luksErase")
    assert runs.lances("reboot")
    assert not _marqueur().exists()
    assert not _trace().exists()


def test_le_marqueur_d_intention_existe_avant_le_premier_cryptsetup():
    """Contrat 6. SIGTERM du delai de l'unite, batterie retiree : sans ce
    marqueur, le poste restait « efface, sans marqueur, sans compte rendu » —
    exactement la confusion que ce chemin pretend fermer."""
    vu = {}

    def espion(ligne):
        if "cryptsetup" in ligne and "avant" not in vu:
            vu["avant"] = (json.loads(_marqueur().read_text())
                           if _marqueur().exists() else None)

    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)}, espion=espion)
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 0
    assert vu["avant"], "aucun marqueur avant le premier luksErase"
    assert vu["avant"]["result"] == "failed"
    assert "intention posee" in vu["avant"]["detail"]
    assert "non encore connu" in vu["avant"]["detail"]
    assert vu["avant"]["reboot"] is False
    assert vu["avant"]["machine_uuid"] == "uuid-1"


def test_une_exception_pendant_le_compte_rendu_laisse_le_marqueur():
    """W6. Une exception non attrapee sortait du chemin destructeur et laissait
    « efface, sans marqueur, sans compte rendu »."""
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})

    def ouvrir(req, timeout=None):
        raise RuntimeError("panne inattendue de la pile http")

    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=ouvrir,
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("reboot") == []
    garde = json.loads(_marqueur().read_text())
    assert garde["result"] == "done"
    assert garde["reboot"] is True


def test_un_effacement_non_confirme_ne_redemarre_pas():
    """Le cas qui distingue « efface » de « jamais rallume »."""
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    code = wipe.executer(ORDRE, MACHINE, run=runs,
                         opener=_opener(exc=urllib.error.URLError("coupe")),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("luksErase")
    assert runs.lances("reboot") == []
    # Les sessions, elles, sont coupees tout de suite.
    assert runs.lances("lock-sessions")
    garde = json.loads(_marqueur().read_text())
    assert garde["order_id"] == "ord-1"
    assert garde["result"] == "done"
    assert garde["reboot"] is True
    assert garde["machine_uuid"] == "uuid-1"


def test_un_409_apres_un_effacement_garde_une_trace_et_ne_redemarre_pas():
    """W7. L'operateur a annule pendant que le poste effacait : le disque est
    detruit et le serveur n'en garde rien. Jeter le marqueur en silence et
    redemarrer effacait la derniere trace de l'evenement."""
    exc = urllib.error.HTTPError("u", 409, "conflit", {}, None)
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(exc=exc),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("luksErase")
    assert runs.lances("reboot") == []
    assert not _marqueur().exists()
    trace = json.loads(_trace().read_text())
    assert trace["order_id"] == "ord-1"
    assert trace["result"] == "done"
    assert "409" in trace["serveur"]
    assert trace["machine_uuid"] == "uuid-1"


def test_un_effacement_condamne_purge_coupe_et_rend_compte():
    """W4. La racine est effacee, un second volume a resiste : ce n'est pas un
    echec, et la suite du depart doit avoir lieu."""
    _ecrire_crypttab("uuid-donnees")
    _bases_sssd("cache_bf.ldb")
    vu = []
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES)),
                  "/dev/sdc1": _Proc(returncode=1, stderr="busy")})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 0
    corps = _corps(vu[0])
    assert corps["result"] == "done"
    assert "disque condamne" in corps["detail"]
    assert runs.lances("sss_cache -E")
    assert runs.lances("lock-sessions")
    assert runs.lances("reboot")


def test_un_effacement_qui_epargne_la_racine_se_dit_en_echec():
    """L'autre cas de W4, et le detail doit dire lequel des deux on est."""
    _ecrire_crypttab("uuid-donnees")
    vu = []
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk(DISQUE_SYSTEME,
                                               DISQUE_DONNEES)),
                  "/dev/sda2": _Proc(returncode=1, stderr="busy")})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 1
    corps = _corps(vu[0])
    assert corps["result"] == "failed"
    assert "condamne" not in corps["detail"]
    assert runs.lances("reboot") == []


def test_un_effacement_qui_echoue_ne_pretend_pas_avoir_efface():
    vu = []
    runs = _Runs({"lsblk": _Proc(stdout=_lsblk())})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("reboot") == []
    assert _corps(vu[0])["result"] == "failed"
    assert not _marqueur().exists()


def test_un_verrouillage_personnel_vide_le_cache():
    base = _bases_sssd("cache_bf.ldb", "config.ldb")
    runs = _Runs()
    code = wipe.executer(dict(ORDRE, mode="lock"), MACHINE, run=runs,
                         opener=_opener(), dormir=_sans_attente)
    assert code == 0
    assert runs.lances("sss_cache -E")
    assert runs.lances("stop sssd.service")
    assert runs.lances("start sssd.service")
    assert list(base.iterdir()) == []
    assert runs.lances("cryptsetup") == []
    assert runs.lances("reboot") == []


def test_un_verrouillage_coupe_la_session_en_cours():
    """W9. La purge du cache ne touche PAS une session deja ouverte : sans ce
    geste, la personne continuait sa session, alors que la docstring promettait
    que « la session hors ligne cesse tout de suite »."""
    runs = _Runs()
    code = wipe.executer(dict(ORDRE, mode="lock"), MACHINE, run=runs,
                         opener=_opener(), dormir=_sans_attente)
    assert code == 0
    assert runs.lances("lock-sessions")


def test_le_detail_dit_si_les_sessions_ont_ete_coupees():
    """W20. Le retour de `couper_les_sessions` partait a la poubelle."""
    vu = []
    runs = _Runs({"lock-sessions": _Proc(returncode=1, stderr="pas de bus")})
    wipe.executer(dict(ORDRE, mode="lock"), MACHINE, run=runs,
                  opener=_opener(vu=vu), dormir=_sans_attente)
    assert "NON verrouillees" in _corps(vu[0])["detail"]


def test_un_verrouillage_de_poste_partage_ne_vise_que_l_usager():
    base = _bases_sssd("cache_bf.ldb")
    runs = _Runs()
    code = wipe.executer(
        dict(ORDRE, mode="lock", seat_only=True,
             target_login="eleve@ecole.test"),
        MACHINE, run=runs, opener=_opener(), dormir=_sans_attente)
    assert code == 0
    assert runs.lances("sss_cache -u eleve")
    assert runs.lances("ldbmodify")
    # Les autres emprunteurs gardent leur cache : AUCUN `sss_cache -E`, aucune
    # base retiree. sssd est bien arrete le temps de toucher a l'ldb — il sert
    # sa copie en memoire autrement — mais il revient, et rien n'a disparu.
    assert runs.lances("sss_cache -E") == []
    assert [p.name for p in base.iterdir()] == ["cache_bf.ldb"]
    assert runs.lances("start sssd.service")


def test_un_verrouillage_de_poste_partage_sans_cible_ne_purge_rien():
    """W2, de bout en bout : c'est le chemin qu'un poste partage prend
    reellement, et il ne doit JAMAIS vider le cache des autres."""
    base = _bases_sssd("cache_bf.ldb")
    vu = []
    runs = _Runs()
    code = wipe.executer(dict(ORDRE, mode="lock", seat_only=True,
                              target_login=""),
                         MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("sss_cache") == []
    assert runs.lances("stop sssd.service") == []
    assert [p.name for p in base.iterdir()] == ["cache_bf.ldb"]
    assert _corps(vu[0])["result"] == "failed"
    assert "AUCUNE purge" in _corps(vu[0])["detail"]


def test_un_verrouillage_sans_cible_dans_l_ordre_purge_tout():
    """Serveur d'avant le contrat : pas de cle `seat_only` ni `target_login`.
    Pas de purge ciblee possible, donc purge complete — et le detail le dit."""
    base = _bases_sssd("cache_bf.ldb")
    vu = []
    runs = _Runs()
    code = wipe.executer(dict(ORDRE_SANS_CIBLE, mode="lock"), MACHINE,
                         run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 0
    assert runs.lances("sss_cache -E")
    assert list(base.iterdir()) == []
    assert "ordre sans cible" in _corps(vu[0])["detail"]


def test_un_redemarrage_refuse_alerte_reessaie_en_forcant_et_sort_en_echec(
        capsys):
    """M4. Le code de `systemctl reboot` partait a la poubelle. S'il echoue —
    bus systemd injoignable, inhibiteur pose par une application — la fiche est
    deja `done` et archivee, le marqueur est parti, et le poste TOURNE avec son
    volume ouvert et sa cle maitresse dans le noyau : la fenetre reversible de
    W10, cette fois sans borne de temps, alors que le CHANGELOG la borne a
    l'attente du compte rendu."""
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME),
                  "reboot": _Proc(returncode=1, stderr="Failed to set wall")})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("reboot") == [["systemctl", "reboot"],
                                     ["systemctl", "--force", "reboot"]]
    journal = capsys.readouterr().err
    assert "ALERTE" in journal
    assert "REDEMARRAGE IMPOSSIBLE" in journal


def test_un_redemarrage_qui_prend_au_second_essai_reste_un_succes():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME),
                  "systemctl reboot": _Proc(returncode=1, stderr="refus")})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 0
    assert ["systemctl", "--force", "reboot"] in runs


def test_sans_marqueur_d_intention_rien_n_est_detruit(monkeypatch):
    """🔴 M7. L'`OSError` du marqueur d'intention etait avalee et l'effacement
    continuait quand meme : sur un /var plein ou remonte en lecture seule, on
    detruisait sans avoir pu ecrire l'intention, et le contrat 6 — rien ne se
    detruit avant d'avoir verifie qu'on pourra le dire — n'etait plus tenu. Le
    refus ne coute rien, c'est le meme raisonnement que pour `url_rapport`."""
    monkeypatch.setattr(wipe, "MARQUEUR",
                        str(Path(wipe.MARQUEUR).parent / "absent" / "x.json"))
    monkeypatch.setattr(wipe, "_ecrire_json", _refus_d_ecriture)
    vu = []
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 2
    assert runs.lances("cryptsetup") == []
    assert runs.lances("reboot") == []
    # Et le refus REMONTE : sinon la fiche resterait « ordre remis » pour
    # toujours sur un poste qui ne peut pas executer.
    assert _corps(vu[0])["result"] == "refused"
    assert "marqueur d'intention" in _corps(vu[0])["detail"]


def test_un_marqueur_relu_absent_vaut_une_ecriture_manquee(monkeypatch):
    """M7, l'autre moitie : une ecriture qui ne leve pas mais ne laisse rien de
    relisable (replace tronque, montage disparu) doit compter comme un echec.
    C'est la RELECTURE qui le dit, pas le code de retour de l'ecriture."""
    monkeypatch.setattr(wipe, "_ecrire_json", lambda *a, **k: None)
    assert wipe.ecrire_marqueur("ord-1", "done", "d", True, MACHINE) is False


def test_un_verrouillage_non_confirme_garde_un_marqueur_sans_redemarrage():
    runs = _Runs()
    code = wipe.executer(dict(ORDRE, mode="lock"), MACHINE, run=runs,
                         opener=_opener(exc=urllib.error.URLError("coupe")),
                         dormir=_sans_attente)
    assert code == 1
    assert json.loads(_marqueur().read_text())["reboot"] is False
    assert runs.lances("reboot") == []


# ---------------------------------------------------------------- rejeu
def test_sans_marqueur_le_rejeu_ne_fait_rien():
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs == []


def test_le_rejeu_qui_passe_redemarre_et_retire_le_marqueur():
    _ecrire_marqueur(reboot=True)
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs.lances("reboot")
    assert not _marqueur().exists()


def test_un_rejeu_de_verrouillage_ne_redemarre_pas():
    _ecrire_marqueur(reboot=False)
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs.lances("reboot") == []
    assert not _marqueur().exists()


def test_un_rejeu_qui_echoue_garde_le_marqueur():
    _ecrire_marqueur()
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs,
                        opener=_opener(exc=urllib.error.URLError("coupe")),
                        dormir=_sans_attente) == 1
    assert runs.lances("reboot") == []
    assert _marqueur().exists()


def test_un_rejeu_en_conflit_garde_une_trace_et_jette_le_marqueur():
    """W7 au rejeu : garder le marqueur ferait rejouer le meme 409 toutes les
    heures sans jamais redemarrer."""
    _ecrire_marqueur()
    exc = urllib.error.HTTPError("u", 409, "conflit", {}, None)
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(exc=exc),
                        dormir=_sans_attente) == 1
    assert runs.lances("reboot") == []
    assert not _marqueur().exists()
    assert json.loads(_trace().read_text())["order_id"] == "ord-1"


def test_un_marqueur_sans_resultat_est_refuse():
    """W12. `result` par defaut a « done » faisait archiver la fiche d'un poste
    dont on ne sait rien, sur la foi d'un marqueur tronque ou bricole."""
    _marqueur().write_text(json.dumps({"order_id": "ord-1", "reboot": True,
                                       "machine_uuid": "uuid-1"}))
    assert wipe.lire_marqueur(MACHINE) is None
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 1
    assert runs == []


def test_un_marqueur_refuse_est_range_et_ne_se_rejoue_qu_une_fois(capsys):
    """M5. Refuser le marqueur ne suffisait pas : le fichier restait en place,
    `rejouer` rendait 0 sans rien faire, la synchro ecrivait « compte rendu en
    attente rejoue (code 0) » avec `status: ok`, et l'unite restait VERTE —
    toutes les heures, indefiniment. C'est le cas que W12 visait."""
    _ecrire_marqueur(machine_uuid="uuid-du-voisin")
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 1
    journal = capsys.readouterr().err
    assert "ALERTE" in journal and "REFUSE" in journal
    assert not _marqueur().exists()
    assert json.loads(_marqueur_refuse().read_text())["order_id"] == "ord-1"
    # Au passage suivant il n'y a plus rien a rejouer : boucle fermee.
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs == []


@pytest.mark.parametrize("resultat", ["", "ok", "DONE", None, 1, "efface"])
def test_un_marqueur_au_resultat_inconnu_est_refuse(resultat):
    _ecrire_marqueur(result=resultat)
    assert wipe.lire_marqueur(MACHINE) is None


def test_un_marqueur_d_un_autre_poste_est_refuse():
    """W12. /var recopie, image clonee : un marqueur venu d'ailleurs ferait
    rendre compte a la place d'un autre poste."""
    _ecrire_marqueur(machine_uuid="uuid-du-voisin")
    assert wipe.lire_marqueur(MACHINE) is None
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 1
    assert runs == []


def test_un_marqueur_sans_machine_uuid_est_refuse():
    _marqueur().write_text(json.dumps({"order_id": "ord-1", "result": "done"}))
    assert wipe.lire_marqueur(MACHINE) is None


@pytest.mark.parametrize("contenu", ["pas du json", "{}", '{"result": "done"}',
                                     '{"order_id": 12, "result": "done"}'])
def test_un_marqueur_abime_ne_se_rejoue_pas(contenu):
    """M5 : abime, il est range en trace plutot que rejoue a vide chaque
    heure. Rien n'est rapporte au serveur, et l'unite le dit une fois."""
    _marqueur().write_text(contenu)
    assert wipe.lire_marqueur() is None
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 1
    assert runs == []
    assert not _marqueur().exists()
    assert _marqueur_refuse().read_text() == contenu


def test_le_marqueur_nait_deja_au_bon_mode(monkeypatch):
    """W13. L'atomicite du rename ne dit RIEN des permissions : avec un chmod
    posterieur, le marqueur — qui porte l'identifiant d'ordre du poste —
    existait d'abord au umask, sous un nom previsible, dans le repertoire de
    destination."""
    vus = []
    vrai_open = os.open

    def espion(p, flags, mode=0o777, **kw):
        vus.append(mode)
        return vrai_open(p, flags, mode, **kw)

    monkeypatch.setattr(os, "open", espion)
    wipe.ecrire_marqueur("ord-1", "done", "detail", True, MACHINE)
    assert vus and vus[-1] == 0o600
    assert oct(_marqueur().stat().st_mode)[-3:] == "600"
    assert not list(_marqueur().parent.glob(".*.tmp"))
    assert json.loads(_marqueur().read_text())["machine_uuid"] == "uuid-1"


def test_la_trace_nait_deja_au_bon_mode(monkeypatch):
    vus = []
    vrai_open = os.open

    def espion(p, flags, mode=0o777, **kw):
        vus.append(mode)
        return vrai_open(p, flags, mode, **kw)

    monkeypatch.setattr(os, "open", espion)
    wipe.ecrire_trace("ord-1", "done", "detail", "HTTP 409", MACHINE)
    assert vus and vus[-1] == 0o600
    assert oct(_trace().stat().st_mode)[-3:] == "600"


# ----------------------------------------------------------------- entree
def test_sans_fiche_d_enrolement_aucun_ordre_n_est_executable(monkeypatch):
    monkeypatch.setattr(wipe.os, "geteuid", lambda: 0)
    assert wipe.main([]) == 1


@pytest.mark.parametrize("fiche", [
    {}, {"token": "t"}, {"token": "t", "endpoint": "https://e/machine"},
    {"endpoint": "https://e/machine", "machine_uuid": "u"}])
def test_une_fiche_incomplete_est_refusee(tmp_path, fiche):
    chemin = tmp_path / "fiche.json"
    chemin.write_text(json.dumps(fiche))
    assert wipe.lire_machine(str(chemin)) is None


def test_un_argument_inconnu_est_refuse(monkeypatch):
    """⚠️ CET ESSAI PASSAIT AUSSI POUR LA MAUVAISE RAISON (meme famille que
    E3) : sans la garde, `main` tombait sur `json.load(sys.stdin)`, que pytest
    remplace par un flux vide, donc ValueError, donc 2 — le MEME code. Ici
    l'entree standard porte un ordre VALIDE : sans la garde, l'assistant
    l'executerait et rendrait 0.
    """
    monkeypatch.setattr(wipe.os, "geteuid", lambda: 0)
    Path(wipe.MACHINE_JSON).write_text(json.dumps(MACHINE))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ORDRE)))
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    assert wipe.main(["--efface-tout"], run=runs, opener=_opener(),
                     dormir=_sans_attente) == 2
    assert runs.lances("cryptsetup") == []
    assert not _marqueur().exists()


def _main_hors_root(monkeypatch, euid):
    """Le MEME appel, a un seul detail pres : l'euid. Rend (code, atteint)."""
    Path(wipe.MACHINE_JSON).write_text(json.dumps(MACHINE))
    _ecrire_crypttab("uuid-sys")
    monkeypatch.setattr(wipe.os, "geteuid", lambda: euid)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(ORDRE)))
    atteint = []
    vrai = wipe.effacer_volumes

    def espion(**kw):
        atteint.append(kw)
        return vrai(**kw)

    monkeypatch.setattr(wipe, "effacer_volumes", espion)
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_SYSTEME)})
    code = wipe.main([], run=runs, opener=_opener(), dormir=_sans_attente)
    return code, atteint, runs


def test_sous_root_le_meme_appel_atteint_bien_le_chemin_destructeur(
        monkeypatch):
    """🔴 LE TEMOIN DE L'ESSAI SUIVANT, et ce qui le rend capable d'echouer.

    E3 : l'essai de la garde root ne POUVAIT PAS echouer. `_run`,
    `volumes_luks`, `effacer_volumes`, `executer`, `rejouer` portaient tous
    `run=subprocess.run` en valeur par DEFAUT liee a l'import, et `main()`
    n'avait aucun parametre `run` : le double installe par
    `monkeypatch.setattr(wipe.subprocess, "run", ...)` n'etait jamais consulte,
    « aucune commande lancee » etait vrai par construction, et le mutant qui
    retire la garde survivait. Sous root (conteneur, `sudo pytest`, CI
    privilegiee), la suite aurait lance le vrai `lsblk` et un `luksErase` sur
    la racine de la machine de build.

    Ce temoin-ci demontre que le montage ATTEINT le chemin destructeur quand
    rien ne l'arrete. C'est lui qui donne son sens a l'essai suivant.
    """
    code, atteint, runs = _main_hors_root(monkeypatch, 0)
    assert code == 0
    assert len(atteint) == 1
    assert runs.lances("cryptsetup") == [
        ["cryptsetup", "luksErase", "--batch-mode", "/dev/sda2"]]


def test_hors_root_rien_ne_se_fait(monkeypatch):
    """Hors root, et RIEN n'est tente : ni geste, ni chemin destructeur.

    Exactement le montage du temoin ci-dessus, a l'euid pres : la garde est
    donc la SEULE difference, et retirer la garde fait tomber cet essai.
    """
    code, atteint, runs = _main_hors_root(monkeypatch, 1000)
    assert code == 1
    assert atteint == [], "le chemin destructeur a ete atteint hors root"
    assert list(runs) == []
    assert not _marqueur().exists()
