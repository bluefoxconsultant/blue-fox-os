"""bluefox-wipe : ce que le poste fait d'un ordre de depart (#23941, #27365).

Les cas qui comptent, et pourquoi :

- UN ORDRE QUI NE NOMME PAS CE POSTE N'EFFACE RIEN. C'est la garde centrale :
  une politique recopiee d'une autre machine, ou un fichier bricole, doit
  ressortir sans qu'un seul `cryptsetup` ne soit lance.
- AUCUN VOLUME TROUVE N'EST PAS UN SUCCES : rendre « fait » sans avoir efface
  ferait basculer la fiche sur un poste intact.
- LE COMPTE RENDU PASSE AVANT LE REDEMARRAGE : un poste redemarre ne reparlera
  plus, et « efface » ne doit pas se confondre avec « jamais rallume ». Si le
  compte rendu ne passe pas, le poste coupe ses sessions, garde un marqueur, et
  ne redemarre PAS.
- Un poste partage ne purge que l'usager vise : purger tout le cache fermerait
  la porte aux autres emprunteurs.

⚠️ Stdlib uniquement (lane `test-wizard` de la CI).
"""

import importlib.machinery
import importlib.util
import json
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
ORDRE = {"mode": "wipe", "machine_uuid": "uuid-1", "order_id": "ord-1"}

_LSBLK_UN_VOLUME = json.dumps({"blockdevices": [
    {"name": "/dev/sda", "fstype": None, "children": [
        {"name": "/dev/sda1", "fstype": "vfat"},
        {"name": "/dev/sda2", "fstype": "crypto_LUKS", "children": [
            {"name": "/dev/mapper/luks-1", "fstype": "btrfs"}]}]}]})


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runs(list):
    """Double de subprocess.run qui note les commandes et rend ce qu'on veut."""

    def __init__(self, reponses=None):
        super().__init__()
        self.reponses = reponses or {}

    def __call__(self, argv, **kwargs):
        self.append(list(argv))
        for cle, proc in self.reponses.items():
            if cle in " ".join(argv):
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


def test_un_ordre_valide_passe():
    assert wipe.valider_ordre(ORDRE, MACHINE) == ("wipe", "")
    assert wipe.valider_ordre(dict(ORDRE, mode="lock"), MACHINE)[0] == "lock"


# ------------------------------------------------------------- volumes
def test_les_volumes_luks_se_lisent_en_profondeur():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_UN_VOLUME)})
    assert wipe.volumes_luks(run=runs) == ["/dev/sda2"]


def test_un_lsblk_muet_ne_rend_aucun_volume():
    for proc in (_Proc(returncode=1, stderr="boom"), _Proc(stdout="pas du json")):
        assert wipe.volumes_luks(run=_Runs({"lsblk": proc})) == []


def test_aucun_volume_trouve_n_est_pas_un_succes():
    runs = _Runs({"lsblk": _Proc(stdout=json.dumps({"blockdevices": []}))})
    ok, detail = wipe.effacer_volumes(run=runs)
    assert ok is False
    assert "aucun volume LUKS" in detail
    assert runs.lances("cryptsetup") == []


def test_chaque_volume_est_efface_sans_question():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_UN_VOLUME)})
    ok, detail = wipe.effacer_volumes(run=runs)
    assert ok is True
    assert runs.lances("cryptsetup") == [
        ["cryptsetup", "luksErase", "--batch-mode", "/dev/sda2"]]
    assert "1/1" in detail


def test_un_volume_qui_resiste_fait_echouer_l_ensemble():
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_UN_VOLUME),
                  "luksErase": _Proc(returncode=1, stderr="busy")})
    ok, detail = wipe.effacer_volumes(run=runs)
    assert ok is False
    assert "/dev/sda2" in detail


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
    ok, note = wipe.rendre_compte(machine, "ord-1", "done")
    assert ok is False
    assert "https" in note


def test_le_compte_rendu_porte_le_jeton_de_la_machine_et_l_ordre():
    vu = []
    ok, _note = wipe.rendre_compte(MACHINE, "ord-1", "done", "2 volumes",
                                   opener=_opener(vu=vu))
    assert ok is True
    (req,) = vu
    assert req.get_header("Authorization") == "Bearer bfos-machine SECRET"
    assert json.loads(req.data.decode()) == {
        "order_id": "ord-1", "result": "done", "detail": "2 volumes"}


def test_un_409_ne_se_reessaie_pas():
    """Le serveur ne reconnait plus cet ordre : insister n'aiderait pas."""
    exc = urllib.error.HTTPError("u", 409, "conflit", {}, None)
    ok, note = wipe.rendre_compte(MACHINE, "ord-1", "done", opener=_opener(exc=exc))
    assert ok is True
    assert "409" in note


def test_un_serveur_injoignable_se_reessaie_puis_abandonne():
    essais = []

    def ouvrir(req, timeout=None):
        essais.append(req)
        raise urllib.error.URLError("hors ligne")

    ok, _note = wipe.rendre_compte_avec_essais(
        MACHINE, "ord-1", "done", opener=ouvrir, dormir=_sans_attente)
    assert ok is False
    assert len(essais) == wipe.ESSAIS_RAPPORT


# ------------------------------------------------------------- execution
def _marqueur(tmp_path, monkeypatch):
    chemin = tmp_path / "departure-pending.json"
    monkeypatch.setattr(wipe, "MARQUEUR", str(chemin))
    return chemin


def test_un_effacement_confirme_redemarre_et_ne_laisse_pas_de_marqueur(
        tmp_path, monkeypatch):
    marqueur = _marqueur(tmp_path, monkeypatch)
    monkeypatch.setattr(wipe, "SSSD_DB", str(tmp_path / "sss"))
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_UN_VOLUME)})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(),
                         dormir=_sans_attente)
    assert code == 0
    assert runs.lances("luksErase")
    assert runs.lances("reboot")
    assert not marqueur.exists()


def test_un_effacement_non_confirme_ne_redemarre_pas(tmp_path, monkeypatch):
    """Le cas qui distingue « efface » de « jamais rallume »."""
    marqueur = _marqueur(tmp_path, monkeypatch)
    monkeypatch.setattr(wipe, "SSSD_DB", str(tmp_path / "sss"))
    runs = _Runs({"lsblk": _Proc(stdout=_LSBLK_UN_VOLUME)})
    code = wipe.executer(ORDRE, MACHINE, run=runs,
                         opener=_opener(exc=urllib.error.URLError("coupe")),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("luksErase")
    assert runs.lances("reboot") == []
    # Les sessions, elles, sont coupees tout de suite.
    assert runs.lances("lock-sessions")
    garde = json.loads(marqueur.read_text())
    assert garde["order_id"] == "ord-1"
    assert garde["result"] == "done"
    assert garde["reboot"] is True


def test_un_effacement_qui_echoue_ne_pretend_pas_avoir_efface(
        tmp_path, monkeypatch):
    marqueur = _marqueur(tmp_path, monkeypatch)
    vu = []
    runs = _Runs({"lsblk": _Proc(stdout=json.dumps({"blockdevices": []}))})
    code = wipe.executer(ORDRE, MACHINE, run=runs, opener=_opener(vu=vu),
                         dormir=_sans_attente)
    assert code == 1
    assert runs.lances("reboot") == []
    assert json.loads(vu[0].data.decode())["result"] == "failed"
    assert not marqueur.exists()


def test_un_verrouillage_personnel_vide_le_cache(tmp_path, monkeypatch):
    _marqueur(tmp_path, monkeypatch)
    base = tmp_path / "sss"
    base.mkdir()
    (base / "cache_bf.ldb").write_text("x")
    (base / "config.ldb").write_text("x")
    monkeypatch.setattr(wipe, "SSSD_DB", str(base))
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


def test_un_verrouillage_de_poste_partage_ne_vise_que_l_usager(
        tmp_path, monkeypatch):
    _marqueur(tmp_path, monkeypatch)
    base = tmp_path / "sss"
    base.mkdir()
    (base / "cache_bf.ldb").write_text("x")
    monkeypatch.setattr(wipe, "SSSD_DB", str(base))
    runs = _Runs()
    code = wipe.executer(
        dict(ORDRE, mode="lock", seat=True, login="eleve@ecole.test"),
        MACHINE, run=runs, opener=_opener(), dormir=_sans_attente)
    assert code == 0
    assert runs.lances("sss_cache -u eleve@ecole.test")
    # Les autres emprunteurs gardent leur cache.
    assert runs.lances("stop sssd.service") == []
    assert list(base.iterdir()) == [base / "cache_bf.ldb"]


def test_un_verrouillage_non_confirme_garde_un_marqueur_sans_redemarrage(
        tmp_path, monkeypatch):
    marqueur = _marqueur(tmp_path, monkeypatch)
    monkeypatch.setattr(wipe, "SSSD_DB", str(tmp_path / "sss"))
    runs = _Runs()
    code = wipe.executer(dict(ORDRE, mode="lock"), MACHINE, run=runs,
                         opener=_opener(exc=urllib.error.URLError("coupe")),
                         dormir=_sans_attente)
    assert code == 1
    assert json.loads(marqueur.read_text())["reboot"] is False
    assert runs.lances("reboot") == []


# ---------------------------------------------------------------- rejeu
def test_sans_marqueur_le_rejeu_ne_fait_rien(tmp_path, monkeypatch):
    _marqueur(tmp_path, monkeypatch)
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs == []


def test_le_rejeu_qui_passe_redemarre_et_retire_le_marqueur(
        tmp_path, monkeypatch):
    marqueur = _marqueur(tmp_path, monkeypatch)
    marqueur.write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "detail": "d", "reboot": True}))
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs.lances("reboot")
    assert not marqueur.exists()


def test_un_rejeu_de_verrouillage_ne_redemarre_pas(tmp_path, monkeypatch):
    marqueur = _marqueur(tmp_path, monkeypatch)
    marqueur.write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "detail": "d", "reboot": False}))
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs, opener=_opener(),
                        dormir=_sans_attente) == 0
    assert runs.lances("reboot") == []
    assert not marqueur.exists()


def test_un_rejeu_qui_echoue_garde_le_marqueur(tmp_path, monkeypatch):
    marqueur = _marqueur(tmp_path, monkeypatch)
    marqueur.write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "detail": "d", "reboot": True}))
    runs = _Runs()
    assert wipe.rejouer(MACHINE, run=runs,
                        opener=_opener(exc=urllib.error.URLError("coupe")),
                        dormir=_sans_attente) == 1
    assert runs.lances("reboot") == []
    assert marqueur.exists()


@pytest.mark.parametrize("contenu", ["pas du json", "{}", '{"result": "done"}'])
def test_un_marqueur_abime_ne_se_rejoue_pas(tmp_path, monkeypatch, contenu):
    marqueur = _marqueur(tmp_path, monkeypatch)
    marqueur.write_text(contenu)
    assert wipe.lire_marqueur() is None
    assert wipe.rejouer(MACHINE, run=_Runs(), opener=_opener(),
                        dormir=_sans_attente) == 0


# ----------------------------------------------------------------- entree
def test_sans_fiche_d_enrolement_aucun_ordre_n_est_executable(
        tmp_path, monkeypatch):
    monkeypatch.setattr(wipe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(wipe, "MACHINE_JSON", str(tmp_path / "absent.json"))
    assert wipe.main([]) == 1


@pytest.mark.parametrize("fiche", [
    {}, {"token": "t"}, {"token": "t", "endpoint": "https://e/machine"},
    {"endpoint": "https://e/machine", "machine_uuid": "u"}])
def test_une_fiche_incomplete_est_refusee(tmp_path, fiche):
    chemin = tmp_path / "machine.json"
    chemin.write_text(json.dumps(fiche))
    assert wipe.lire_machine(str(chemin)) is None


def test_un_argument_inconnu_est_refuse(tmp_path, monkeypatch):
    monkeypatch.setattr(wipe.os, "geteuid", lambda: 0)
    chemin = tmp_path / "machine.json"
    chemin.write_text(json.dumps(MACHINE))
    monkeypatch.setattr(wipe, "MACHINE_JSON", str(chemin))
    assert wipe.main(["--efface-tout"]) == 2


def test_hors_root_rien_ne_se_fait(monkeypatch):
    monkeypatch.setattr(wipe.os, "geteuid", lambda: 1000)
    assert wipe.main([]) == 1
