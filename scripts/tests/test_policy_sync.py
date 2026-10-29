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
    assert f"PathChanged={sync_mod.LOCK_PATH}\n" in texte
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
