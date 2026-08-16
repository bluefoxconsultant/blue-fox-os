"""bluefox-policy-sync : ce qu'une machine deja installee fait d'un changement
de politique (#23909).

Les cas qui comptent, et pourquoi :

- Une liste Flatpak VIDEE cote Odoo doit vider le fichier, pas le laisser tel
  quel. C'est la difference volontaire avec l'application a l'installation, et
  c'est ce qui fait que la politique reste la source de verite.
- Une politique SANS bloc « apps » (serveur anterieur) ne doit toucher a rien.
- Un poste hors ligne, un serveur qui repond mal, une machine revoquee :
  la derniere politique connue reste en place et le service sort en SUCCES.
  Un poste ne se retrouve jamais sans configuration parce que le reseau a
  hoquete.
- Rien n'a change → on ne reveille pas system-flatpak-setup pour rien.
- Quelque chose a change → on le reveille, parce que sa propre minuterie est en
  OnBootSec=30 et ne repasserait qu'au prochain demarrage.

⚠️ Stdlib uniquement (lane `test-wizard` de la CI).
"""

import importlib.machinery
import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

# ⚠️ Charger le service ecrit un __pycache__ A COTE DE LUI, c'est-a-dire DANS
# files/ — que le module `files` de BlueBuild copie tel quel dans l'image. Sans
# cette ligne, chaque passage de tests depose des .pyc destines a etre embarques
# dans l'OS.
sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC_PATH = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-policy-sync"
APPLY_PATH = REPO_ROOT / "install" / "bfos_apply.py"


def _load(path, name):
    # `spec_from_file_location` rend None sur un fichier sans extension connue —
    # et le service, lui, s'appelle `bluefox-policy-sync` tout court. D'ou le
    # chargeur explicite.
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


sync_mod = _load(SYNC_PATH, "bluefox_policy_sync")

POLICY = {
    "schema": "bf-policy/v2",
    "install": {"hostname": "bf-olivier", "locale": "fr_CA.UTF-8"},
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "apps": {"install": ["org.gimp.GIMP"], "remove": ["org.mozilla.Thunderbird"]},
}


class _Resp:
    def __init__(self, body):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(body=None, exc=None):
    def open_url(req, timeout=None):
        if exc is not None:
            raise exc
        return _Resp(json.dumps(POLICY) if body is None else body)
    return open_url


class _Runs(list):
    def __call__(self, argv, **kwargs):
        self.append(argv)


@pytest.fixture()
def machine(tmp_path):
    """Un poste enrole, avec ses fichiers la ou le service les attend."""
    path = tmp_path / "machine.json"
    path.write_text(json.dumps({
        "schema": "bf-machine/v1", "machine_uuid": "uuid-1",
        "endpoint": "https://example.com/api/v1/policy/machine",
        "token": "SECRET", "hostname": "bf-olivier"}))
    (tmp_path / "flatpaks").mkdir()
    return path


def _paths(tmp_path):
    return {
        "provisioning_path": str(tmp_path / "provisioning.json"),
        "flatpak_dir": str(tmp_path / "flatpaks"),
        "state_path": str(tmp_path / "policy-sync.json"),
        "apply_module": str(APPLY_PATH),
    }


# ------------------------------------------------------------------ nominal
def test_writes_policy_and_both_lists(tmp_path, machine):
    runs = _Runs()
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                       **_paths(tmp_path))
    assert rc == 0
    staged = json.loads((tmp_path / "provisioning.json").read_text())
    assert staged["install"]["hostname"] == "bf-olivier"
    # 0600 : la politique porte le login de l'operateur et les endpoints LDAP.
    assert oct((tmp_path / "provisioning.json").stat().st_mode)[-3:] == "600"
    install = (tmp_path / "flatpaks" / "install").read_text()
    remove = (tmp_path / "flatpaks" / "remove").read_text()
    assert "org.gimp.GIMP" in install
    assert "org.mozilla.Thunderbird" in remove
    # Les listes ont change → on declenche la convergence tout de suite.
    assert any("system-flatpak-setup.service" in " ".join(a) for a in runs)


def test_second_run_changes_nothing_and_wakes_nobody(tmp_path, machine):
    runs = _Runs()
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=_Runs(),
                  **_paths(tmp_path))
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                       **_paths(tmp_path))
    assert rc == 0
    assert runs == [], "rien n'a change : inutile de reveiller flatpak"


def test_new_generated_at_alone_is_not_a_change(tmp_path, machine):
    """Le serveur horodate CHAQUE reponse. Sans neutraliser ce champ, toutes les
    machines se croiraient modifiees tous les jours."""
    first = dict(POLICY, generated_at="2026-07-25T10:00:00Z")
    second = dict(POLICY, generated_at="2026-07-26T10:00:00Z")
    sync_mod.sync(machine_path=str(machine),
                  opener=_opener(body=json.dumps(first)), run=_Runs(),
                  **_paths(tmp_path))
    before = (tmp_path / "provisioning.json").read_text()
    runs = _Runs()
    sync_mod.sync(machine_path=str(machine),
                  opener=_opener(body=json.dumps(second)), run=runs,
                  **_paths(tmp_path))
    assert (tmp_path / "provisioning.json").read_text() == before
    assert runs == []
    assert json.loads((tmp_path / "policy-sync.json").read_text())["changes"] == []


def test_emptied_list_empties_the_file(tmp_path, machine):
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=_Runs(),
                  **_paths(tmp_path))
    assert "org.gimp.GIMP" in (tmp_path / "flatpaks" / "install").read_text()
    # L'organisation retire toutes ses applications supplementaires.
    emptied = dict(POLICY, apps={"install": [], "remove": []})
    runs = _Runs()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(body=json.dumps(emptied)), run=runs,
                       **_paths(tmp_path))
    assert rc == 0
    install = (tmp_path / "flatpaks" / "install").read_text()
    assert "org.gimp.GIMP" not in install, (
        "une liste videe cote Odoo doit vider le fichier — sinon la politique "
        "n'est plus la source de verite")
    assert install.lstrip().startswith("#"), "l'en-tete reste, la liste est vide"
    assert runs, "le changement doit declencher la convergence"


def test_policy_without_apps_block_touches_nothing(tmp_path, machine):
    (tmp_path / "flatpaks" / "install").write_text("com.brave.Browser\n")
    older = dict(POLICY)
    older.pop("apps")
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(body=json.dumps(older)), run=_Runs(),
                       **_paths(tmp_path))
    assert rc == 0
    assert (tmp_path / "flatpaks" / "install").read_text() == "com.brave.Browser\n"


def test_rendering_matches_install_time(tmp_path, machine):
    """Le fichier ecrit par la synchronisation doit etre celui qu'aurait ecrit
    l'installation : meme fonction, donc meme octets."""
    apply_mod = _load(APPLY_PATH, "bfos_apply_ref")
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=_Runs(),
                  **_paths(tmp_path))
    assert ((tmp_path / "flatpaks" / "install").read_text()
            == apply_mod.render_flatpak_list(["org.gimp.GIMP"], "install"))


# ------------------------------------------------------------------- pannes
def test_offline_keeps_the_last_known_policy(tmp_path, machine):
    (tmp_path / "provisioning.json").write_text('{"schema": "bf-policy/v2"}')
    rc = sync_mod.sync(
        machine_path=str(machine),
        opener=_opener(exc=urllib.error.URLError("pas de route")),
        run=_Runs(), **_paths(tmp_path))
    assert rc == 0, "hors ligne n'est pas une panne"
    assert (tmp_path / "provisioning.json").read_text() == '{"schema": "bf-policy/v2"}'
    state = json.loads((tmp_path / "policy-sync.json").read_text())
    assert state["status"] == "skipped"


@pytest.mark.parametrize("code", [401, 403])
def test_revoked_machine_keeps_its_policy(tmp_path, machine, code):
    err = urllib.error.HTTPError("http://x", code, "nope", {}, None)
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(exc=err),
                       run=_Runs(), **_paths(tmp_path))
    assert rc == 0
    assert not (tmp_path / "provisioning.json").exists()
    state = json.loads((tmp_path / "policy-sync.json").read_text())
    assert "revoquee" in state["detail"]


def test_off_schema_response_is_ignored(tmp_path, machine):
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(body='{"schema": "autre-chose"}'),
                       run=_Runs(), **_paths(tmp_path))
    assert rc == 0
    assert not (tmp_path / "provisioning.json").exists()


def test_unenrolled_machine_is_a_no_op(tmp_path):
    rc = sync_mod.sync(machine_path=str(tmp_path / "absent.json"),
                       opener=_opener(), run=_Runs(), **_paths(tmp_path))
    assert rc == 0
    assert not (tmp_path / "provisioning.json").exists()


def test_incomplete_machine_file_is_a_no_op(tmp_path):
    path = tmp_path / "machine.json"
    path.write_text('{"endpoint": "https://example.com/x"}')  # pas de jeton
    rc = sync_mod.sync(machine_path=str(path), opener=_opener(), run=_Runs(),
                       **_paths(tmp_path))
    assert rc == 0


def test_missing_apply_module_is_reported(tmp_path, machine):
    paths = _paths(tmp_path)
    paths["apply_module"] = str(tmp_path / "pas-la.py")
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(), run=_Runs(),
                       **paths)
    # La politique passe quand meme (TPM, etc. la relisent), mais l'absence du
    # module est une erreur de fabrication de l'image : elle doit se voir.
    assert rc == 1
    assert (tmp_path / "provisioning.json").exists()


def test_plain_http_endpoint_is_refused(tmp_path):
    """Le secret ne part pas en clair : un portail captif repondrait par une
    redirection et le recupererait."""
    path = tmp_path / "machine.json"
    path.write_text(json.dumps({
        "endpoint": "http://exemple.test/api/v1/policy/machine",
        "token": "SECRET", "machine_uuid": "u"}))
    called = []

    def open_url(req, timeout=None):
        called.append(req)
        return _Resp(json.dumps(POLICY))

    rc = sync_mod.sync(machine_path=str(path), opener=open_url, run=_Runs(),
                       **_paths(tmp_path))
    assert rc == 0
    assert called == [], "aucune requete ne doit partir"
    assert not (tmp_path / "provisioning.json").exists()
    state = json.loads((tmp_path / "policy-sync.json").read_text())
    assert "non chiffre" in state["detail"]


def test_loopback_stays_usable_for_testing(tmp_path):
    path = tmp_path / "machine.json"
    path.write_text(json.dumps({
        "endpoint": "http://127.0.0.1:18069/api/v1/policy/machine",
        "token": "SECRET", "machine_uuid": "u"}))
    rc = sync_mod.sync(machine_path=str(path), opener=_opener(), run=_Runs(),
                       **_paths(tmp_path))
    assert rc == 0
    assert (tmp_path / "provisioning.json").exists()


def test_redirects_are_not_followed():
    """Le handler installe par defaut doit refuser la redirection, pas la
    suivre en emportant l'en-tete Authorization."""
    import urllib.request as ur

    handler = sync_mod._NoRedirect()
    assert isinstance(handler, ur.HTTPRedirectHandler)
    assert handler.redirect_request(None, None, 302, "moved", {},
                                    "https://ailleurs.test/") is None


def test_auth_header_carries_the_machine_prefix(tmp_path, machine):
    seen = {}

    def open_url(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp(json.dumps(POLICY))

    sync_mod.sync(machine_path=str(machine), opener=open_url, run=_Runs(),
                  **_paths(tmp_path))
    assert seen["auth"] == "Bearer bfos-machine SECRET"


def test_le_temporaire_nait_deja_au_bon_mode(tmp_path, monkeypatch):
    """atomic_write etait cite comme le bon exemple du depot ; il portait le
    meme defaut. L'atomicite du rename ne dit RIEN des permissions : appele en
    0o600, il creait d'abord le temporaire au umask (0644), sous un nom
    previsible et dans le repertoire de destination, et ne le restreignait
    qu'ensuite. Ce test garde le mode de l'appel systeme de creation, pas
    l'etat final."""
    import os

    vus = []
    vrai_open = os.open

    def espion(p, flags, mode=0o777, **kw):
        vus.append(mode)
        return vrai_open(p, flags, mode, **kw)

    monkeypatch.setattr(os, "open", espion)
    cible = tmp_path / "sous" / "politique.json"
    sync_mod.atomic_write(str(cible), '{"a": 1}\n', mode=0o600)

    assert vus and vus[-1] == 0o600
    assert oct(cible.stat().st_mode)[-3:] == "600"


def test_atomic_write_garde_son_contrat_de_base(tmp_path):
    cible = tmp_path / "politique.json"
    sync_mod.atomic_write(str(cible), '{"a": 1}\n')
    assert cible.read_text() == '{"a": 1}\n'
    assert oct(cible.stat().st_mode)[-3:] == "644"
    assert not list(tmp_path.glob(".*.tmp"))  # le temporaire a bien ete renomme
