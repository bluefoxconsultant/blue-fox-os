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
- Verrouillage d'ecran (#27109) : le fichier se regenere depuis la base de
  l'image (le fond y vit), une cle absente n'est PAS un 0, et une alteration
  locale est defaite et notee.

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


# Les chemins que le fixture `_poste_de_papier` redirige, et ou.
_REDIRIGES = (
    ("MACHINE_JSON", "machine.json"),
    ("PROVISIONING_JSON", "provisioning.json"),
    ("PUBLIC_JSON", "policy-public.json"),
    ("STATE_JSON", "policy-sync.json"),
    ("FLATPAK_DIR", "flatpaks"),
    ("SSSD_CONF", "sssd.conf"),
    ("LOCK_PATH", "etc/xdg/kscreenlockerrc"),
    ("LOCK_BASE", "usr/etc/xdg/kscreenlockerrc"),
    ("WIPE_HELPER", "usr/libexec/bluefox-wipe"),
    ("DEPARTURE_MARKER", "departure-pending.json"),
    ("DEPARTURE_TRACE", "departure-sans-suite.json"),
    ("APPLY_MODULE", "usr/lib/bluefox/bfos_apply.py"),
)
# Les chemins REELS de l'image, captures AVANT toute redirection : un essai qui
# compare une unite systemd du depot a la constante du module doit lire le vrai
# chemin, pas le tmp_path du jour.
CHEMINS_REELS = {nom: getattr(sync_mod, nom) for nom, _ in _REDIRIGES}


@pytest.fixture(autouse=True)
def _poste_de_papier(tmp_path, monkeypatch):
    """Aucun essai ne touche les fichiers du poste de developpement.

    ⚠️ F13 : `_paths` redirigeait neuf chemins mais PAS `sssd_conf`, qui
    retombait sur le vrai /etc/sssd/sssd.conf. Aucun essai n'y touchait, mais
    la securite etait ACCIDENTELLE : il suffisait d'un essai de poste partage
    qui oublie le parametre. Elle est ici structurelle — les constantes du
    module sont redirigees, donc un essai qui oublie un chemin ecrit quand
    meme dans son tmp_path, comme le fait depuis le debut
    test_departure_wipe.py. Les deux garde-fous se cumulent a dessein.
    """
    for nom, cible in _REDIRIGES:
        monkeypatch.setattr(sync_mod, nom, str(tmp_path / cible))


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
        "public_path": str(tmp_path / "policy-public.json"),
        # Jamais le vrai /etc/xdg : sans ces deux chemins, un essai qui sert
        # une politique avec auto_lock_minutes ecrirait sur le poste de dev.
        "lock_path": str(tmp_path / "etc" / "xdg" / "kscreenlockerrc"),
        "lock_base": str(tmp_path / "usr" / "etc" / "xdg" / "kscreenlockerrc"),
        # Meme raison que les deux precedents : sans ces chemins, un essai
        # irait lire le marqueur d'effacement du poste de dev et lancerait son
        # /usr/libexec/bluefox-wipe.
        "wipe_helper": str(tmp_path / "usr" / "libexec" / "bluefox-wipe"),
        "marker_path": str(tmp_path / "departure-pending.json"),
        "trace_path": str(tmp_path / "departure-sans-suite.json"),
        # ⚠️ F13 : celui-la MANQUAIT, et retombait sur le vrai
        # /etc/sssd/sssd.conf. Le fixture `_poste_de_papier` le redirige aussi,
        # en ceinture.
        "sssd_conf": str(tmp_path / "sssd.conf"),
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


def test_session_copy_follows_the_policy(tmp_path, machine):
    """La copie que lit la session suit la politique, SANS ses secrets."""
    policy = json.loads(json.dumps(POLICY))
    policy["install"]["login"] = {"mode": "sssd", "bind_password": "jeton-de-service"}
    policy["session"] = {"pwas": [{"name": "Odoo", "url": "https://erp.example"}]}
    policy["browser"] = {"extensions": [{"id": "a" * 32, "update_url": "https://x"}]}
    sync_mod.sync(machine_path=str(machine), opener=_opener(json.dumps(policy)),
                  run=_Runs(), **_paths(tmp_path))
    public_path = tmp_path / "policy-public.json"
    assert oct(public_path.stat().st_mode)[-3:] == "644"
    text = public_path.read_text()
    assert "jeton-de-service" not in text, "le secret de liaison est dans la copie de session"
    public = json.loads(text)
    assert public["session"] == policy["session"]
    assert public["browser"] == policy["browser"]
    assert public["install"]["login"] == {"mode": "sssd"}



def test_un_changement_de_services_reecrit_la_copie_de_session(tmp_path, machine):
    """#27109 : l'adresse d'un service changee dans Odoo doit atteindre la
    copie que lit l'agent, sans reinstallation."""
    def sert(nc_url):
        policy = json.loads(json.dumps(POLICY))
        policy["services"] = {"nextcloud": {"url": nc_url, "oidc_client_id": "nc"},
                              "vaultwarden": {"url": "https://coffre.autre.org"}}
        return _opener(json.dumps(policy))

    public_path = tmp_path / "policy-public.json"
    sync_mod.sync(machine_path=str(machine), opener=sert("https://cloud.autre.org"),
                  run=_Runs(), **_paths(tmp_path))
    assert json.loads(public_path.read_text())["services"]["nextcloud"]["url"] \
        == "https://cloud.autre.org"
    sync_mod.sync(machine_path=str(machine), opener=sert("https://nuage.autre.org"),
                  run=_Runs(), **_paths(tmp_path))
    public = json.loads(public_path.read_text())
    assert public["services"]["nextcloud"]["url"] == "https://nuage.autre.org"
    assert public["services"]["vaultwarden"]["url"] == "https://coffre.autre.org"
    assert oct(public_path.stat().st_mode)[-3:] == "644"


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


# ------------------------------------------------- verrouillage d'ecran (#27109)
FOND = ("# Genere par scripts/generate_kde_theme.py — tenant bf.\n"
        "[Greeter][Wallpaper][org.kde.image][General]\n"
        "Image=file:///usr/share/wallpapers/bf/contents/images/wallpaper.jpg\n"
        "FillMode=2\n")


def _lock_policy(minutes=15, **extra):
    policy = json.loads(json.dumps(POLICY))
    policy["policies"] = {"mfa_required": True, **extra}
    if minutes is not _ABSENT:
        policy["policies"]["auto_lock_minutes"] = minutes
    return policy


_ABSENT = object()


def _lock_files(tmp_path, base=FOND):
    paths = _paths(tmp_path)
    if base is not None:
        b = Path(paths["lock_base"])
        b.parent.mkdir(parents=True, exist_ok=True)
        b.write_text(base)
        # L'image livre le meme fichier dans /etc : c'est l'etat d'un poste neuf.
        t = Path(paths["lock_path"])
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(base)
    return paths, Path(paths["lock_path"])


def _run(machine, policy, paths):
    return sync_mod.sync(machine_path=str(machine),
                         opener=_opener(body=json.dumps(policy)), run=_Runs(),
                         **paths)


def _state(paths):
    return json.loads(Path(paths["state_path"]).read_text())


def _lock_changes(paths):
    """La part du verrouillage dans `changes` : la politique, elle, change
    aussi quand auto_lock_minutes change."""
    return [c for c in _state(paths)["changes"] if c.startswith("verrouillage")]


def _bloc(minutes):
    return (sync_mod.LOCK_BEGIN + "\n"
            "[Daemon][$i]\n"
            "Autolock=true\n"
            f"Timeout={minutes}\n"
            "Lock=true\n"
            "LockGrace=5\n"
            "LockOnResume=true\n"
            "RequirePassword=true\n"
            + sync_mod.LOCK_END + "\n")


def test_verrouillage_rendu_fond_puis_bloc_immuable(tmp_path, machine):
    paths, cible = _lock_files(tmp_path)
    assert _run(machine, _lock_policy(15), paths) == 0
    assert cible.read_text() == FOND + "\n" + _bloc(15)
    assert oct(cible.stat().st_mode)[-3:] == "644"
    state = _state(paths)
    assert "verrouillage" in state["changes"]
    assert "15 min" in state["screen_lock"]
    assert state["status"] == "ok"


def test_verrouillage_idempotent(tmp_path, machine):
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    inode = cible.stat().st_ino
    assert _run(machine, _lock_policy(15), paths) == 0
    # os.replace change l'inode : le meme inode prouve qu'on n'a rien reecrit.
    assert cible.stat().st_ino == inode
    assert _state(paths)["changes"] == []


def test_verrouillage_15_puis_5(tmp_path, machine):
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    _run(machine, _lock_policy(5), paths)
    texte = cible.read_text()
    assert texte == FOND + "\n" + _bloc(5)
    assert texte.count("[Daemon]") == 1
    # Un changement de politique n'est pas une alteration locale.
    assert _lock_changes(paths) == ["verrouillage"]


def test_verrouillage_zero_rend_la_base(tmp_path, machine):
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    _run(machine, _lock_policy(0), paths)
    assert cible.read_text() == FOND
    assert _lock_changes(paths) == ["verrouillage"]
    assert "non impose" in _state(paths)["screen_lock"]


def test_verrouillage_zero_sur_poste_neuf_ne_touche_rien(tmp_path, machine):
    paths, cible = _lock_files(tmp_path)
    inode = cible.stat().st_ino
    _run(machine, _lock_policy(0), paths)
    assert cible.stat().st_ino == inode
    assert _lock_changes(paths) == []


def test_verrouillage_cle_absente_n_est_pas_zero(tmp_path, machine):
    """Absente = serveur anterieur : le dernier reglage connu reste."""
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    avant = cible.read_text()
    assert _run(machine, _lock_policy(_ABSENT), paths) == 0
    assert cible.read_text() == avant
    assert "sans auto_lock_minutes" in _state(paths)["screen_lock"]


def test_verrouillage_politique_sans_bloc_policies(tmp_path, machine):
    paths = _paths(tmp_path)
    assert _run(machine, POLICY, paths) == 0
    assert not Path(paths["lock_path"]).exists()


@pytest.mark.parametrize("valeur", [True, False, "15", -3, 10**6, 1441, 1.5, None])
def test_verrouillage_valeur_invalide_laisse_intact(tmp_path, machine, valeur):
    paths, cible = _lock_files(tmp_path)
    inode = cible.stat().st_ino
    assert _run(machine, _lock_policy(valeur), paths) == 0
    assert cible.read_text() == FOND
    assert cible.stat().st_ino == inode
    assert "laisse intact" in _state(paths)["screen_lock"]


@pytest.mark.parametrize("valeur", [1, 1440])
def test_verrouillage_bornes_acceptees(tmp_path, machine, valeur):
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(valeur), paths)
    assert f"\nTimeout={valeur}\n" in cible.read_text()


def test_verrouillage_daemon_de_la_base_retire(tmp_path, machine):
    base = ("[Daemon]\nAutolock=false\nTimeout=60\n\n" + FOND
            + "\n[Daemon][Sous]\nX=1\n\n[Daemon][$i]\nLock=false\n")
    paths, cible = _lock_files(tmp_path, base=base)
    _run(machine, _lock_policy(15), paths)
    texte = cible.read_text()
    assert "Autolock=false" not in texte and "Lock=false" not in texte
    assert "[Daemon][Sous]" not in texte
    assert texte.count("[Daemon]") == 1
    assert FOND.strip() in texte
    assert texte.endswith(_bloc(15))


def test_verrouillage_sans_base_remplace_son_bloc(tmp_path, machine):
    """Image sans base : on repart du fichier en place, sans doubler le bloc."""
    paths, cible = _lock_files(tmp_path, base=None)
    cible.parent.mkdir(parents=True)
    cible.write_text(FOND)
    _run(machine, _lock_policy(15), paths)
    _run(machine, _lock_policy(5), paths)
    texte = cible.read_text()
    assert texte == FOND + "\n" + _bloc(5)
    assert texte.count(sync_mod.LOCK_BEGIN) == 1
    # Le passage de 15 a 5 n'est pas une alteration locale, meme sans base.
    assert _lock_changes(paths) == ["verrouillage"]
    # 0 : le bloc s'en va, le reste du fichier reste.
    _run(machine, _lock_policy(0), paths)
    assert cible.read_text() == FOND


def test_verrouillage_sans_base_ni_fichier(tmp_path, machine):
    paths = _paths(tmp_path)
    _run(machine, _lock_policy(10), paths)
    assert Path(paths["lock_path"]).read_text() == _bloc(10)
    assert _lock_changes(paths) == ["verrouillage"]


def test_verrouillage_sans_base_daemon_local_retire(tmp_path, machine):
    paths, cible = _lock_files(tmp_path, base=None)
    cible.parent.mkdir(parents=True)
    cible.write_text(FOND + "\n[Daemon]\nAutolock=false\n")
    _run(machine, _lock_policy(15), paths)
    assert cible.read_text() == FOND + "\n" + _bloc(15)
    assert _lock_changes(paths) == ["verrouillage (reglage local retabli)"]


ALTERATIONS = {
    "immuabilite retiree": lambda t: t.replace("[Daemon][$i]", "[Daemon]"),
    "autolock coupe": lambda t: t.replace("Autolock=true", "Autolock=false"),
    "delai hors bornes": lambda t: t.replace("Timeout=15", "Timeout=99999"),
    "sans mot de passe": lambda t: t.replace("RequirePassword=true",
                                             "RequirePassword=false"),
    "groupe ajoute avant": lambda t: "[Daemon]\nLock=false\n\n" + t,
    "groupe ajoute apres": lambda t: t + "[Daemon]\nTimeout=600\n",
    "bloc supprime": lambda t: FOND + "\n[Daemon]\nAutolock=false\n",
    "balise de fin perdue": lambda t: t.replace(sync_mod.LOCK_END + "\n", ""),
}


@pytest.mark.parametrize("nom", sorted(ALTERATIONS))
def test_verrouillage_alteration_locale_retablie(tmp_path, machine, nom):
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    attendu = cible.read_text()
    cible.write_text(ALTERATIONS[nom](attendu))
    assert _run(machine, _lock_policy(15), paths) == 0
    assert cible.read_text() == attendu
    state = _state(paths)
    assert state["changes"] == ["verrouillage (reglage local retabli)"]
    assert "reglage local retabli" in state["screen_lock"]


def test_verrouillage_fond_d_une_nouvelle_image_propage(tmp_path, machine):
    """🔴 ostree garde le /etc modifie localement : sans regeneration depuis
    /usr/etc, le fond d'une nouvelle image n'atteindrait jamais l'ecran."""
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    nouveau = FOND.replace("wallpaper.jpg", "wallpaper-v2.jpg")
    Path(paths["lock_base"]).write_text(nouveau)
    _run(machine, _lock_policy(15), paths)
    assert cible.read_text() == nouveau + "\n" + _bloc(15)
    # Mise a jour de l'image, pas alteration locale.
    assert _lock_changes(paths) == ["verrouillage"]


def test_verrouillage_fond_altere_localement_rendu_a_l_image(tmp_path, machine):
    """Le fond suit l'image. Il est remis sans etre note « retabli » : sans
    memoire de l'ancienne base, une retouche locale et une mise a jour de
    l'image se ressemblent, et la note est reservee au verrouillage."""
    paths, cible = _lock_files(tmp_path)
    _run(machine, _lock_policy(15), paths)
    attendu = cible.read_text()
    cible.write_text(attendu.replace("wallpaper.jpg", "autre.jpg"))
    _run(machine, _lock_policy(15), paths)
    assert cible.read_text() == attendu
    assert _lock_changes(paths) == ["verrouillage"]


def test_verrouillage_ecriture_impossible_rc_partiel(tmp_path, machine):
    paths = _paths(tmp_path)
    bloque = tmp_path / "pas-un-dossier"
    bloque.write_text("")
    paths["lock_path"] = str(bloque / "kscreenlockerrc")
    rc = _run(machine, _lock_policy(15), paths)
    assert rc == 1
    assert _state(paths)["status"] == "partial"
    # La politique et les listes passent quand meme.
    assert (tmp_path / "provisioning.json").exists()
    assert (tmp_path / "flatpaks" / "install").exists()


def test_verrouillage_s_applique_sans_bfos_apply(tmp_path, machine):
    paths, cible = _lock_files(tmp_path)
    paths["apply_module"] = str(tmp_path / "pas-la.py")
    assert _run(machine, _lock_policy(15), paths) == 1
    assert cible.read_text().endswith(_bloc(15))


# ----------------------------------------------- rechargement a chaud (#27109)
UNITES_USAGER = REPO_ROOT / "files" / "usr" / "lib" / "systemd" / "user"


def _systemd_user_enabled(recipe_text):
    """Les unites de `user: enabled:` des modules systemd d'une recette.
    Lecture a la ligne (stdlib seulement) : assez pour la forme des recettes."""
    units, module, section, liste = [], None, None, None
    for line in recipe_text.splitlines():
        if line.startswith("  - type:"):
            module = line.split(":", 1)[1].strip()
            section = liste = None
            continue
        if module != "systemd" or not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("    ") and not line.startswith("     "):
            section = line.strip().rstrip(":")
            liste = None
        elif line.startswith("      ") and not line.startswith("       "):
            liste = line.strip().rstrip(":")
        elif line.strip().startswith("- ") and section == "user" and liste == "enabled":
            units.append(line.strip()[2:].split("#")[0].strip())
    return units


@pytest.mark.parametrize("recette", ["bf.yml", "bf-surface.yml", "factice.yml"])
def test_les_recettes_activent_le_rechargement(recette):
    texte = (REPO_ROOT / "recipes" / recette).read_text()
    assert "bluefox-lock-reload.path" in _systemd_user_enabled(texte), (
        f"{recette} n'active pas bluefox-lock-reload.path pour les sessions : "
        "le verrouillage impose ne prendrait qu'a la session suivante")


def test_l_unite_path_surveille_le_fichier_ecrit():
    texte = (UNITES_USAGER / "bluefox-lock-reload.path").read_text()
    assert f"PathChanged={CHEMINS_REELS['LOCK_PATH']}\n" in texte
    assert "Unit=bluefox-lock-reload.service\n" in texte
    assert "WantedBy=" in texte, "sans [Install], systemctl --global enable echoue"


def test_le_service_redemande_la_configuration_sans_echouer():
    texte = (UNITES_USAGER / "bluefox-lock-reload.service").read_text()
    lignes = [l for l in texte.splitlines() if l.startswith("ExecStart=")]
    assert len(lignes) == 1
    cmd = lignes[0].split("=", 1)[1]
    assert cmd.startswith("-"), "hors session Plasma l'appel echoue : il doit etre tolere"
    assert cmd.split()[1:] == ["--user", "call", "org.kde.screensaver",
                               "/ScreenSaver", "org.kde.screensaver", "configure"]


# ------------------------------------------------ postes partages (bf_policy 18.0.2.12.0)
SEAT_POLICY = {
    "schema": "bf-policy/v2",
    "install": {"hostname": "pret-001", "locale": "fr_CA.UTF-8", "login": {
        "mode": "sssd", "ldap_uri": "ldaps://auth.example.com:636",
        "ldap_base_dn": "DC=example,DC=com", "bind_dn": "cn=svc,DC=example,DC=com",
        "bind_password": "pw", "allow_groups": ["it-staff"], "allow_users": ["e00042"]}},
    "user": {"login": ""},
    "seat": {"profile": "pret", "kind": "loan", "borrowers": ["e00042"]},
}


def _seat_sync(tmp_path, machine, policy, runs):
    return sync_mod.sync(machine_path=str(machine), opener=_opener(json.dumps(policy)),
                         run=runs, trigger=False, **_paths(tmp_path))


def test_loan_borrower_reaches_sssd(tmp_path, machine):
    runs = _Runs()
    assert _seat_sync(tmp_path, machine, SEAT_POLICY, runs) == 0
    conf = (tmp_path / "sssd.conf").read_text()
    assert "simple_allow_users = e00042" in conf
    assert oct((tmp_path / "sssd.conf").stat().st_mode & 0o777) == "0o600"
    assert ["systemctl", "try-restart", "sssd.service"] in runs
    # Retour du poste : l'emprunteur disparait, sssd est relance.
    back = json.loads(json.dumps(SEAT_POLICY))
    back["install"]["login"]["allow_users"] = []
    runs2 = _Runs()
    _seat_sync(tmp_path, machine, back, runs2)
    assert "e00042" not in (tmp_path / "sssd.conf").read_text()
    assert ["systemctl", "try-restart", "sssd.service"] in runs2


def test_same_access_restarts_nothing(tmp_path, machine):
    _seat_sync(tmp_path, machine, SEAT_POLICY, _Runs())
    runs = _Runs()
    _seat_sync(tmp_path, machine, SEAT_POLICY, runs)
    assert ["systemctl", "try-restart", "sssd.service"] not in runs


def test_personal_machine_sssd_untouched(tmp_path, machine):
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=_Runs(),
                  trigger=False, **_paths(tmp_path))
    assert not (tmp_path / "sssd.conf").exists()


def test_seat_without_directory_keeps_its_file(tmp_path, machine):
    (tmp_path / "sssd.conf").write_text("ancien\n")
    broken = json.loads(json.dumps(SEAT_POLICY))
    broken["install"]["login"]["bind_dn"] = ""
    _seat_sync(tmp_path, machine, broken, _Runs())
    assert (tmp_path / "sssd.conf").read_text() == "ancien\n"


def _sans_mot_de_passe(policy):
    """La reponse de /machine depuis bf_policy 18.0.2.11.2 : pas de mot de passe."""
    withheld = json.loads(json.dumps(policy))
    withheld["install"]["login"]["bind_password"] = ""
    return withheld


def test_seat_keeps_bind_password_withheld_by_machine(tmp_path, machine):
    # L'installation a ecrit le mot de passe (reponse de /me).
    _seat_sync(tmp_path, machine, SEAT_POLICY, _Runs())
    # La synchro suivante recoit /machine sans mot de passe, et un retour de pret.
    back = _sans_mot_de_passe(SEAT_POLICY)
    back["install"]["login"]["allow_users"] = []
    runs = _Runs()
    _seat_sync(tmp_path, machine, back, runs)
    conf = (tmp_path / "sssd.conf").read_text()
    assert "e00042" not in conf
    assert "ldap_default_authtok = pw\n" in conf
    assert ["systemctl", "try-restart", "sssd.service"] in runs
    # Rien d'autre n'a change : pas de reecriture, pas de relance.
    runs2 = _Runs()
    _seat_sync(tmp_path, machine, back, runs2)
    assert ["systemctl", "try-restart", "sssd.service"] not in runs2


def test_seat_withheld_password_alone_changes_nothing(tmp_path, machine):
    _seat_sync(tmp_path, machine, SEAT_POLICY, _Runs())
    avant = (tmp_path / "sssd.conf").read_text()
    runs = _Runs()
    _seat_sync(tmp_path, machine, _sans_mot_de_passe(SEAT_POLICY), runs)
    assert (tmp_path / "sssd.conf").read_text() == avant
    assert ["systemctl", "try-restart", "sssd.service"] not in runs


def test_seat_without_any_bind_password_keeps_its_file(tmp_path, machine):
    (tmp_path / "sssd.conf").write_text("[domain/bluefox]\nldap_uri = ldaps://x\n")
    runs = _Runs()
    _seat_sync(tmp_path, machine, _sans_mot_de_passe(SEAT_POLICY), runs)
    assert (tmp_path / "sssd.conf").read_text() == "[domain/bluefox]\nldap_uri = ldaps://x\n"
    assert ["systemctl", "try-restart", "sssd.service"] not in runs


# ------------------------------------------------- ordre de depart (#23941)
# Le detail de l'effacement est garde dans test_departure_wipe.py. Ici, on
# garde le CHAINAGE : ce que la synchro passe a l'assistant, dans quel ordre,
# et ce qu'elle fait quand il manque.
# L'ordre tel qu'Odoo le sert : il porte SA cible (contrat 2 de #23941),
# la politique ne la porte plus.
ORDRE = {"mode": "wipe", "machine_uuid": "uuid-1", "order_id": "ord-1",
         "seat_only": False, "target_login": "personne@exemple.test"}


class _RunsProc(list):
    """Comme _Runs, mais rend un CompletedProcess : l'assistant est lance avec
    `input=`, et la synchro lit son code de retour.

    `marker_a_retirer` imite ce que fait le vrai assistant quand il a enfin
    rendu compte : il retire le marqueur, puis demande le redemarrage.
    """

    def __init__(self, code=0, marker_a_retirer=None):
        super().__init__()
        self.code = code
        self.entrees = []
        self.marker_a_retirer = marker_a_retirer

    def __call__(self, argv, **kwargs):
        self.append(list(argv))
        self.entrees.append(kwargs.get("input"))
        if self.marker_a_retirer and "bluefox-wipe" in " ".join(argv):
            Path(self.marker_a_retirer).unlink(missing_ok=True)

        class _P:
            returncode = self.code
            stdout = ""
            stderr = ""
        return _P()


def _avec_ordre(ordre=None, **extra):
    """Une politique COMPLETE v2 qui porte quand meme un ordre.

    ⚠️ Cette forme n'existe PLUS dans le contrat (contrat 9 : Odoo sert
    `order-v1` des qu'un ordre attend). Elle reste ici pour l'essai qui
    verifie qu'un serveur qui la servirait se fait REFUSER son ordre, fort.
    """
    policy = json.loads(json.dumps(POLICY))
    policy["departure"] = {"on_revoke": "wipe", "offline_max_days": 7}
    if ordre is not None:
        policy["departure"]["order"] = ordre
    policy.update(extra)
    return policy


def _reduite(ordre=None, mode=None):
    """La charge d'ordre telle que bf_policy 18.0.2.11.6 la sert : SON PROPRE
    schema (contrat 1), aucune cle `install`, et QUE l'ordre.

    C'est le SEUL chemin d'un ordre de depart (contrat 9), que l'usager du
    poste soit encore autorise ou non."""
    ordre = ordre or (dict(ORDRE, mode=mode) if mode else ORDRE)
    return {"schema": sync_mod.SCHEMA_ORDRE, "order_only": True,
            "departure": {"on_revoke": ordre["mode"], "offline_max_days": 7,
                          "order": ordre}}


def _helper(tmp_path):
    chemin = tmp_path / "usr" / "libexec" / "bluefox-wipe"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("#!/bin/true\n")
    return chemin


def _sync_ordre(tmp_path, machine, runs, ordre=None, charge=None):
    """Une synchro qui recoit une charge d'ordre (le seul chemin, contrat 9)."""
    paths = _paths(tmp_path)
    reduite = charge if charge is not None else _reduite(ordre)
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(reduite)), run=runs, **paths)
    etat = json.loads(Path(paths["state_path"]).read_text())
    return rc, etat


def test_l_ordre_part_sur_l_entree_standard_et_pas_en_argument(tmp_path,
                                                               machine):
    _helper(tmp_path)
    runs = _RunsProc()
    rc, _etat = _sync_ordre(tmp_path, machine, runs)
    assert rc == 0
    lances = [a for a in runs if "bluefox-wipe" in " ".join(a)]
    assert len(lances) == 1
    # L'ordre n'est NULLE PART dans la ligne de commande : /proc est public.
    assert lances[0][1:] == []
    assert "ord-1" not in " ".join(lances[0])
    charge = json.loads([e for e in runs.entrees if e][0])
    assert charge == ORDRE


def test_l_ordre_part_tel_qu_il_arrive_et_la_politique_n_y_ajoute_rien(
        tmp_path, machine):
    """W2, W3 et contrat 2 : la cible de la purge vient de l'ORDRE.

    ⚠️ La version precedente fabriquait `seat` et `login` depuis la POLITIQUE.
    Un poste partage porte `"user": {"login": ""}` : la portee tombait a
    « poste personnel » et la branche qui vide TOUT le cache de sssd se prenait
    la ou elle ne devait jamais se prendre.
    """
    _helper(tmp_path)
    runs = _RunsProc()
    ordre = dict(ORDRE, mode="lock", seat_only=True,
                 target_login="eleve@ecole.test")
    _sync_ordre(tmp_path, machine, runs, ordre)
    charge = json.loads([e for e in runs.entrees if e][0])
    assert charge == ordre
    assert "seat" not in charge and "login" not in charge


def test_un_ordre_sans_cible_est_signale_dans_l_etat(tmp_path, machine):
    """Code defensif : un serveur qui ne sert pas encore `seat_only` /
    `target_login` ne permet aucune purge ciblee. On le dit, plutot que de
    laisser la fiche croire a une purge d'emprunteur."""
    _helper(tmp_path)
    nu = {"mode": "lock", "machine_uuid": "uuid-1", "order_id": "ord-1"}
    _rc, etat = _sync_ordre(tmp_path, machine, _RunsProc(), nu)
    assert "ordre sans cible" in etat["departure"]


def test_sans_ordre_l_assistant_n_est_jamais_lance(tmp_path, machine):
    _helper(tmp_path)
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(_avec_ordre())), run=runs,
                       **_paths(tmp_path))
    assert rc == 0
    assert [a for a in runs if "bluefox-wipe" in " ".join(a)] == []


def test_une_politique_sans_bloc_de_depart_ne_change_rien(tmp_path, machine):
    _helper(tmp_path)
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                       **_paths(tmp_path))
    assert rc == 0
    assert [a for a in runs if "bluefox-wipe" in " ".join(a)] == []
    etat = json.loads((tmp_path / "policy-sync.json").read_text())
    assert etat["departure"] == "politique sans bloc de depart"


# ------------------------------------------- contrat 9 : un seul canal d'ordre
def test_un_ordre_servi_dans_une_charge_v2_n_est_pas_execute(tmp_path,
                                                             machine):
    """🔴 Contrat 9, cote lecture. `departure.order` ne voyage plus JAMAIS dans
    une charge `bf-policy/v2` : Odoo sert `order-v1` des qu'un ordre attend,
    que l'usager soit encore autorise ou non.

    Pourquoi la branche v2 ne doit plus CHERCHER d'ordre : c'est ce chemin qui
    portait l'ordre complet — login de la personne visee et mode — jusque dans
    `policy-public.json` (0644), a chaque passage, tant que l'ordre echouait
    (E2, C4). La source est fermee cote Odoo ; ici la branche ne le lit plus.

    Et on ne se tait PAS : taire un ordre non execute rejouerait P2, ou la
    fiche affiche « Ordre remis au poste » pour toujours sans qu'aucun des deux
    bouts puisse le savoir. L'unite sort en echec, ce qui amene quelqu'un dans
    l'heure.
    """
    _helper(tmp_path)
    paths = _paths(tmp_path)
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(_avec_ordre(ORDRE))),
                       run=runs, **paths)
    assert rc == 1
    assert [a for a in runs if "bluefox-wipe" in " ".join(a)] == []
    etat = json.loads(Path(paths["state_path"]).read_text())
    assert "NON EXECUTE" in etat["departure"]
    assert sync_mod.SCHEMA_ORDRE in etat["departure"]
    assert etat["status"] == "partial"
    assert "ordre de depart" not in etat["changes"]
    # La politique, elle, est legitime : elle s'applique. Seul le canal de
    # l'ordre est faux.
    assert Path(paths["provisioning_path"]).exists()


def test_rien_de_l_ordre_n_entre_dans_la_copie_de_session(tmp_path, machine):
    """🔴 E2 + contrat 9, la CEINTURE cote poste. `write_public` recopiait le
    bloc `departure` tel quel dans policy-public.json, en 0644 : la personne
    VISEE y lisait son propre login et le mode de l'ordre, donc qu'un
    effacement l'attend. Elle a alors tout interet a ne plus rebrancher le
    poste — ce qui desarme la fonction, puisqu'un poste qui ne se rebranche
    jamais ne s'efface jamais — ou a sortir la cle maitresse du noyau pendant
    que le volume est encore ouvert (W10).

    Aucun essai ne regardait ce qui entre dans ce fichier sur le chemin de
    depart. La ceinture n'est pas redondante avec la fermeture cote Odoo :
    `load_apply` charge la copie de bfos_apply.py posee DANS L'IMAGE, qui peut
    etre anterieure a ce lot.
    """
    _helper(tmp_path)
    paths = _paths(tmp_path)
    sync_mod.sync(machine_path=str(machine),
                  opener=_opener(json.dumps(_avec_ordre(ORDRE))),
                  run=_RunsProc(), **paths)
    public = Path(paths["public_path"]).read_text()
    assert oct(Path(paths["public_path"]).stat().st_mode)[-3:] == "644"
    assert "departure" not in json.loads(public)
    for secret in ("ord-1", "personne@exemple.test", "on_revoke", "wipe"):
        assert secret not in public
    # La politique complete, elle, est en 0600 : elle peut tout porter.
    assert oct(Path(paths["provisioning_path"]).stat().st_mode)[-3:] == "600"


def test_la_ceinture_tient_meme_avec_un_bfos_apply_d_avant_ce_lot(tmp_path):
    """🔴 E2, la raison d'etre de la ceinture cote poste — et ce qu'un essai qui
    passe par le vrai bfos_apply.py NE MESURE PAS.

    `load_apply` charge la copie de bfos_apply.py posee DANS L'IMAGE, qui peut
    etre anterieure a ce lot et donc ne rien expurger : le service tourne alors
    avec un rendu qui recopie le bloc `departure` tel quel. La ceinture est au
    point d'ECRITURE, pour que la protection ne depende ni du serveur ni de
    l'age de l'image. Ici, le module est double par une copie « d'avant ».
    """
    class _ApplyDAvant:
        """Le rendu d'avant #23941 : il n'expurge que le mot de passe."""

        @staticmethod
        def render_public_policy(policy):
            public = json.loads(json.dumps(policy))
            (public.get("install", {}).get("login") or {}).pop(
                "bind_password", None)
            return json.dumps(public, indent=2) + "\n"

    cible = tmp_path / "policy-public.json"
    policy = _avec_ordre(ORDRE)
    assert sync_mod.write_public(str(cible), policy, _ApplyDAvant) is True
    rendu = cible.read_text()
    assert "departure" not in json.loads(rendu)
    for trace in ("ord-1", "personne@exemple.test", "on_revoke"):
        assert trace not in rendu
    # Et la politique passee reste entiere : le service l'ecrit en 0600 juste
    # avant, il ne faut pas la lui amputer.
    assert policy["departure"]["order"]["order_id"] == "ord-1"


def test_la_synchro_annonce_sa_capacite_de_depart(tmp_path, machine):
    """🔴 Contrat 10. « Remis » ne doit plus se confondre avec « remis a un
    agent qui sait le lire » (P2) : un poste du parc dont l'usager est encore
    autorise recevait l'ordre, l'agent de production l'ignorait en sortant 0
    unite verte, et la fiche disait « Ordre remis au poste » pour toujours —
    parce que le poste n'annoncait AUCUNE version. Odoo inscrit la capacite sur
    la fiche et refuse de poser un ordre sur un poste qui ne l'a pas annoncee.
    """
    vu = []

    def ouvrir(req, timeout=None):
        vu.append(req)
        return _Resp(json.dumps(POLICY))

    sync_mod.sync(machine_path=str(machine), opener=ouvrir, run=_RunsProc(),
                  **_paths(tmp_path))
    (req,) = vu
    assert req.get_header("X-bfos-capabilities") == "departure"
    assert sync_mod.EN_TETE_CAPACITES == "X-BFOS-Capabilities"
    assert sync_mod.CAPACITES == ("departure",)


def test_le_chemin_de_la_copie_de_session_est_le_meme_des_deux_cotes():
    """La constante du service et celle de bfos_apply.py ne peuvent pas
    diverger : le service ecrit, l'agent d'accueil lit. Meme forme d'essai que
    pour `secours_voulu`, duplique entre deux scripts du kickstart."""
    apply_mod = _load(APPLY_PATH, "bfos_apply_pour_essai")
    assert CHEMINS_REELS["PUBLIC_JSON"] == apply_mod.PUBLIC_JSON


def test_un_assistant_absent_de_l_image_se_dit_fort(tmp_path, machine):
    """Une image d'avant cet assistant ne doit pas laisser croire a un
    effacement."""
    rc, etat = _sync_ordre(tmp_path, machine, _RunsProc())
    assert rc == 1
    assert "ORDRE NON EXECUTE" in etat["departure"]
    assert "ordre de depart" not in etat["changes"]


def test_un_ordre_refuse_par_l_assistant_fait_sortir_en_echec(tmp_path,
                                                              machine):
    _helper(tmp_path)
    rc, etat = _sync_ordre(tmp_path, machine, _RunsProc(code=2))
    assert rc == 1
    assert "REFUSE" in etat["departure"]


def test_un_ordre_incomplet_est_agi_mais_fait_sortir_en_echec(tmp_path,
                                                              machine):
    """W14. Code 1 : l'assistant a travaille mais n'a pas pu finir — efface,
    compte rendu en attente, pas de redemarrage. C'est un ordre agi, et c'est
    exactement l'etat qui exige un humain : l'unite ne doit pas rester VERTE.
    """
    _helper(tmp_path)
    rc, etat = _sync_ordre(tmp_path, machine, _RunsProc(code=1))
    assert rc == 1
    assert "incomplet" in etat["departure"]
    assert "ordre de depart" in etat["changes"]
    assert etat["status"] == "partial"


@pytest.mark.parametrize("code", [127, 126, 3, 255])
def test_un_code_inattendu_n_est_pas_un_ordre_agi(tmp_path, machine, code):
    """W14. 127 (assistant present mais pas executable, interpreteur absent)
    ecrivait « status: ok » et « ordre de depart » dans les changements, sur un
    poste ou rien ne s'etait passe."""
    _helper(tmp_path)
    rc, etat = _sync_ordre(tmp_path, machine, _RunsProc(code=code))
    assert rc == 1
    assert "ORDRE NON EXECUTE" in etat["departure"]
    assert "ordre de depart" not in etat["changes"]
    assert etat["status"] == "partial"


@pytest.mark.parametrize("signal", [15, 9])
def test_un_assistant_tue_par_un_signal_est_peut_etre_agi(tmp_path, machine,
                                                          signal):
    """🔴 M6. `subprocess.run` rend un code NEGATIF quand l'enfant est tue par
    un signal : -15 pour le SIGTERM du `TimeoutStartSec=5min` de l'unite, -9
    pour le tueur de memoire. Il tombait dans « code inattendu », et l'etat
    local affirmait « ORDRE NON EXECUTE » sur un poste dont la racine peut tres
    bien etre deja effacee — le marqueur d'intention est ecrit avant le premier
    luksErase, justement pour ce cas."""
    _helper(tmp_path)
    rc, etat = _sync_ordre(tmp_path, machine, _RunsProc(code=-signal))
    assert rc == 1
    assert "ORDRE NON EXECUTE" not in etat["departure"]
    assert f"signal {signal}" in etat["departure"]
    assert "PEUT-ETRE AGI" in etat["departure"]
    # Un ordre peut-etre agi EST un changement : le passer sous silence, c'est
    # ce qui faisait croire qu'il ne s'etait rien passe.
    assert "ordre de depart" in etat["changes"]


def test_un_assistant_qui_ne_se_lance_pas_n_est_pas_un_ordre_agi(tmp_path,
                                                                 machine):
    """Present mais pas executable : `run` leve, et ce n'est pas un ordre."""
    _helper(tmp_path)

    def run_qui_leve(argv, **kwargs):
        if "bluefox-wipe" in " ".join(argv):
            raise PermissionError("Permission denied")
        return _RunsProc()(argv, **kwargs)

    rc, etat = _sync_ordre(tmp_path, machine, run_qui_leve)
    assert rc == 1
    assert "ORDRE NON EXECUTE" in etat["departure"]
    assert "ordre de depart" not in etat["changes"]


def test_une_charge_d_ordre_ne_touche_a_rien_d_autre(tmp_path, machine):
    """Contrat 9 : une charge d'ordre ne porte pas de politique, donc un poste
    a effacer n'attend aucune convergence Flatpak — il n'y en a plus a faire.
    Et le verrou, sssd et la copie de session restent intacts."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(_reduite(mode="lock"))),
                       run=runs, **paths)
    assert rc == 0
    assert [a for a in runs if "system-flatpak-setup" in " ".join(a)] == []
    assert not (tmp_path / "flatpaks" / "install").exists()
    assert not Path(paths["public_path"]).exists()
    assert not Path(paths["lock_path"]).exists()
    assert not Path(paths["sssd_conf"]).exists()
    assert not Path(paths["provisioning_path"]).exists()


def test_un_wipe_reussi_arrete_la_synchronisation_sur_place(tmp_path, machine):
    """W15. `systemctl reboot` REND LA MAIN TOUT DE SUITE : la synchro
    continuait et pouvait reecrire les listes Flatpak, sssd.conf et le verrou
    d'ecran pendant l'extinction d'un poste dont le disque vient d'etre
    condamne."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    runs = _RunsProc()
    rc, etat = _sync_ordre(tmp_path, machine, runs)
    assert rc == 0
    assert [a for a in runs if "system-flatpak-setup" in " ".join(a)] == []
    assert not (tmp_path / "flatpaks" / "install").exists()
    assert not Path(paths["public_path"]).exists()
    assert not Path(paths["lock_path"]).exists()
    assert etat["status"] == "ok"
    assert "ordre de depart" in etat["changes"]


def test_un_compte_rendu_en_attente_se_rejoue_avant_tout(tmp_path, machine):
    _helper(tmp_path)
    paths = _paths(tmp_path)
    Path(paths["marker_path"]).write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "reboot": True}))
    runs = _RunsProc()
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                  **paths)
    lances = [a for a in runs if "bluefox-wipe" in " ".join(a)]
    assert lances == [[paths["wipe_helper"], "--rejouer-le-compte-rendu"]]
    # Et c'est bien le PREMIER geste de la synchronisation.
    assert runs[0] == lances[0]


def test_un_rejeu_qui_redemarre_arrete_la_synchronisation(tmp_path, machine):
    """W15, au rejeu : l'assistant a enfin rendu compte et demande le
    redemarrage. Rien ne se reecrit pendant l'extinction."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    Path(paths["marker_path"]).write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "reboot": True,
         "machine_uuid": "uuid-1"}))
    runs = _RunsProc(marker_a_retirer=paths["marker_path"])
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                       **paths)
    assert rc == 0
    # Le rejeu, et RIEN d'autre : pas meme la politique.
    assert [a for a in runs if "system-flatpak-setup" in " ".join(a)] == []
    assert not Path(paths["provisioning_path"]).exists()
    assert not Path(paths["public_path"]).exists()
    etat = json.loads(Path(paths["state_path"]).read_text())
    assert etat["status"] == "reboot"


def test_un_rejeu_de_verrouillage_laisse_la_synchro_continuer(tmp_path,
                                                              machine):
    """Un marqueur de « lock » ne demande aucun redemarrage : la synchro suit
    son cours."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    Path(paths["marker_path"]).write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "reboot": False,
         "machine_uuid": "uuid-1"}))
    runs = _RunsProc(marker_a_retirer=paths["marker_path"])
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                       **paths)
    assert rc == 0
    assert Path(paths["provisioning_path"]).exists()


def test_sans_marqueur_rien_ne_se_rejoue(tmp_path, machine):
    _helper(tmp_path)
    runs = _RunsProc()
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=runs,
                  **_paths(tmp_path))
    assert [a for a in runs if "rejouer" in " ".join(a)] == []


def test_un_rejeu_qui_ne_passe_pas_ne_laisse_pas_l_unite_verte(tmp_path,
                                                               machine):
    """🔴 M5. Le code de l'assistant partait a la poubelle : un marqueur qu'il
    REFUSE (resultat inutilisable, UUID d'un autre poste) le faisait rendre 0
    sans rien faire, et l'etat ecrivait « compte rendu en attente rejoue
    (code 0) » avec `status: ok`, toutes les heures, indefiniment, sur l'unite
    la plus verte du poste. C'est le cas que W12 visait."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    Path(paths["marker_path"]).write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "reboot": False,
         "machine_uuid": "uuid-1"}))
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(),
                       run=_RunsProc(code=1), **paths)
    assert rc == 1
    etat = json.loads(Path(paths["state_path"]).read_text())
    assert etat["status"] == "partial"


def test_une_trace_sans_suite_tient_l_unite_en_echec_et_entre_dans_l_etat(
        tmp_path, machine):
    """🔴 M9. La trace locale laissee par un compte rendu REFUSE par le serveur
    (409, 400) — un ordre execute dont la fiche ne garde rien — n'etait relue
    par rien, n'entrait pas dans policy-sync.json, et l'unite redevenait verte
    au passage suivant. Cet etat exige un humain, et c'est l'humain qui retire
    le fichier apres avoir reporte l'ordre sur la fiche."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    Path(paths["trace_path"]).write_text(json.dumps(
        {"order_id": "ord-1", "result": "done", "serveur": "HTTP 409"}))
    rc = sync_mod.sync(machine_path=str(machine), opener=_opener(),
                       run=_RunsProc(), **paths)
    assert rc == 1
    etat = json.loads(Path(paths["state_path"]).read_text())
    assert paths["trace_path"] in etat["departure_sans_suite"]
    assert etat["status"] == "partial"
    # Et elle tient aussi quand la politique n'a meme pas pu etre tiree.
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(exc=urllib.error.URLError("hors ligne")),
                       run=_RunsProc(), **paths)
    assert rc == 1
    # Retiree, l'unite redevient verte : c'est bien le fichier qui la tenait.
    Path(paths["trace_path"]).unlink()
    assert sync_mod.sync(machine_path=str(machine), opener=_opener(),
                         run=_RunsProc(), **paths) == 0


# ------------------------------------------------------------- garde root
def _main_avec_euid(tmp_path, machine, monkeypatch, euid):
    """Le MEME appel, a l'euid pres. Rend (code, lancements)."""
    _helper(tmp_path)
    monkeypatch.setattr(sync_mod.os, "geteuid", lambda: euid)
    runs = _RunsProc()
    code = sync_mod.main(machine_path=str(machine), opener=_opener(),
                         run=runs, **_paths(tmp_path))
    return code, runs


def test_sous_root_la_synchro_fait_bien_quelque_chose(tmp_path, machine,
                                                      monkeypatch):
    """Le temoin de l'essai suivant : sans ce temoin, « rien ne s'est passe »
    serait vrai par construction. Meme famille que E3 — de ce cote-ci, la garde
    root n'etait pas mesuree DU TOUT, parce que `main()` n'avait aucun
    parametre et qu'un essai ne pouvait pas l'appeler sans toucher aux vrais
    fichiers du poste."""
    code, runs = _main_avec_euid(tmp_path, machine, monkeypatch, 0)
    assert code == 0
    assert runs != []
    assert (tmp_path / "provisioning.json").exists()


def test_hors_root_la_synchro_ne_fait_rien(tmp_path, machine, monkeypatch):
    code, runs = _main_avec_euid(tmp_path, machine, monkeypatch, 1000)
    assert code == 1
    assert list(runs) == []
    assert not (tmp_path / "provisioning.json").exists()
    assert not (tmp_path / "policy-sync.json").exists()


def test_une_charge_reduite_n_ecrase_pas_la_politique(tmp_path, machine):
    """Usager non autorise + ordre en attente : la charge ne porte que l'ordre.

    Si elle devenait le provisioning.json du poste, un poste simplement
    verrouille y perdrait toute sa configuration.
    """
    _helper(tmp_path)
    paths = _paths(tmp_path)
    # Une politique complete d'abord, pour avoir quelque chose a perdre.
    sync_mod.sync(machine_path=str(machine), opener=_opener(), run=_RunsProc(),
                  **paths)
    avant = Path(paths["provisioning_path"]).read_text()

    reduite = _reduite()
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(reduite)), run=runs, **paths)
    assert rc == 0
    assert Path(paths["provisioning_path"]).read_text() == avant
    assert [a for a in runs if "bluefox-wipe" in " ".join(a)]
    charge = json.loads([e for e in runs.entrees if e][0])
    assert charge["order_id"] == "ord-1"
    etat = json.loads(Path(paths["state_path"]).read_text())
    assert etat["order_only"] is True
    assert etat["changes"] == ["ordre de depart"]


def test_la_charge_reduite_porte_son_propre_schema(tmp_path, machine):
    """Contrat 1. ⚠️ Annoncee « bf-policy/v2 » avec un `install: {}` vide, la
    charge reduite PASSAIT le controle de forme de l'agent en production : un
    poste du parc ecrivait ce moignon dans son provisioning.json, perdait sa
    configuration de session, et la charge lui revenait a chaque heure. Avec
    son propre schema, l'agent d'avant tombe sur « reponse hors schema
    bf-policy/v2 », n'ecrit RIEN et garde sa politique. Le nouveau la
    reconnait et n'en tire QUE l'ordre."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    reduite = _reduite()
    assert reduite["schema"] == sync_mod.SCHEMA_ORDRE
    assert "install" not in reduite
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(reduite)), run=runs, **paths)
    assert rc == 0
    assert not Path(paths["provisioning_path"]).exists()
    assert not Path(paths["public_path"]).exists()
    charge = json.loads([e for e in runs.entrees if e][0])
    assert charge == ORDRE


def test_la_charge_reduite_sans_order_only_est_reconnue_au_schema(tmp_path,
                                                                 machine):
    """`order_only` reste en ceinture : le schema suffit."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    reduite = _reduite()
    del reduite["order_only"]
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(reduite)), run=runs, **paths)
    assert rc == 0
    assert not Path(paths["provisioning_path"]).exists()
    assert [a for a in runs if "bluefox-wipe" in " ".join(a)]


def test_une_charge_reduite_a_l_ancien_schema_reste_traitee(tmp_path, machine):
    """Ceinture : un serveur qui sert encore l'ancienne forme (v2 +
    `order_only`) ne doit pas se retrouver sans chemin."""
    _helper(tmp_path)
    paths = _paths(tmp_path)
    ancienne = {"schema": "bf-policy/v2", "order_only": True, "install": {},
                "user": {"login": "olivier@bluefoxconsultant.com"},
                "departure": {"on_revoke": "wipe", "offline_max_days": 7,
                              "order": ORDRE}}
    runs = _RunsProc()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(ancienne)), run=runs, **paths)
    assert rc == 0
    assert not Path(paths["provisioning_path"]).exists()
    assert [a for a in runs if "bluefox-wipe" in " ".join(a)]


def test_une_charge_reduite_ne_touche_a_rien_d_autre(tmp_path, machine):
    _helper(tmp_path)
    paths = _paths(tmp_path)
    reduite = _reduite(mode="lock")
    runs = _RunsProc()
    sync_mod.sync(machine_path=str(machine), opener=_opener(json.dumps(reduite)),
                  run=runs, **paths)
    # Ni listes Flatpak, ni sssd.conf, ni verrou d'ecran, ni copie de session.
    assert not (tmp_path / "flatpaks" / "install").exists()
    assert not Path(paths["public_path"]).exists()
    assert not Path(paths["lock_path"]).exists()
    assert [a for a in runs if "system-flatpak-setup" in " ".join(a)] == []


def test_une_charge_reduite_sans_assistant_sort_en_echec(tmp_path, machine):
    paths = _paths(tmp_path)
    reduite = _reduite()
    rc = sync_mod.sync(machine_path=str(machine),
                       opener=_opener(json.dumps(reduite)), run=_RunsProc(),
                       **paths)
    assert rc == 1
    etat = json.loads(Path(paths["state_path"]).read_text())
    assert "ORDRE NON EXECUTE" in etat["departure"]
