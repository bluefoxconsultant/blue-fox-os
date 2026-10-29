"""Les mises a jour de l'image sont verifiees par la signature cosign (#27109).

Constats du 2026-10-28, mesures sur factice:latest (a9b26d3) et au skopeo :
  - la politique publiee visait `localhost/blue-fox-os-<slug>` (le build se fait
    avec IMAGE_REGISTRY=localhost) : ghcr.io/bluefoxconsultant/... tombait sous
    `"": insecureAcceptAnything`, AUCUNE mise a jour n'etait verifiee ;
  - sans registries.d (`use-sigstore-attachments: true`), une politique
    sigstoreSigned refuse meme l'image signee : « no signature exists » ;
  - en matchRepoDigestOrExact, elle la refuse aussi : la signature porte le
    depot sans tag, on tire `:latest`.

Ce qui est garde ici, sans image ni reseau :
  1. files/scripts/politique-conteneurs.sh, joue sur une politique de la forme
     publiee : ce qu'il ecrit, et ce qui fait echouer le build ;
  2. son branchement dans les recipes (apres `signing`) et la minuterie ;
  3. bluefox-origine-signee (bascule des postes existants) : jamais de
     redemarrage, jamais d'insistance, marqueur une fois demarre en signe ;
  4. bluefox-rebase choisit son transport sur la politique ;
  5. la porte canari de publish-image.sh (etape 7b), jouee avec un faux podman.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "files" / "scripts" / "politique-conteneurs.sh"
CLE = REPO_ROOT / "files" / "usr" / "share" / "bluefox" / "keys" / "cosign-images.pub"
CLE_SECOURS = CLE.with_name("cosign-images-secours.pub")
# Empreinte de la cle de secours retenue par Olivier le 2026-10-29 (cle privee
# hors ligne, jamais dans ce depot) : une autre cle ici n'en serait pas une.
EMPREINTE_SECOURS = "448dc741d3979d96cc62e468c8d8a2b473f5f1302f8272f8fb9d7bed52b21709"
REGISTRIES = REPO_ROOT / "files" / "etc" / "containers" / "registries.d" / "bluefoxconsultant.yaml"
ORIGINE = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-origine-signee"
REBASE = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-rebase"
UNITES = REPO_ROOT / "files" / "usr" / "lib" / "systemd" / "system"
PUBLISH = REPO_ROOT / "scripts" / "publish-image.sh"
PUBLIABLES = ("bf.yml", "bf-surface.yml", "factice.yml")

DEPOTS = tuple(f"ghcr.io/bluefoxconsultant/blue-fox-os-{s}" for s in ("bf", "bf-surface", "factice"))
CLE_SYSTEME = "/usr/share/bluefox/keys/cosign-images.pub"
CLE_SECOURS_SYSTEME = "/usr/share/bluefox/keys/cosign-images-secours.pub"
REGLE = {"type": "sigstoreSigned", "keyPaths": [CLE_SYSTEME, CLE_SECOURS_SYSTEME],
         "signedIdentity": {"type": "matchRepository"}}


def politique_publiee(nom="blue-fox-os-factice"):
    """La forme exacte de /etc/containers/policy.json dans factice:latest
    (couche extraite le 2026-10-28), raccourcie aux transports qui comptent."""
    return {
        "default": [{"type": "reject"}],
        "transports": {
            "docker": {
                f"localhost/{nom}": [{
                    "type": "sigstoreSigned",
                    "keyPath": f"/etc/pki/containers/{nom}.pub",
                    "signedIdentity": {"type": "matchRepository"}}],
                "registry.access.redhat.com": [{
                    "type": "signedBy", "keyType": "GPGKeys",
                    "keyPath": "/etc/pki/rpm-gpg/RPM-GPG-KEY-redhat-release"}],
                "ghcr.io/ublue-os": [{
                    "type": "sigstoreSigned",
                    "keyPaths": ["/etc/pki/containers/ublue-os.pub",
                                 "/etc/pki/containers/ublue-os-backup.pub"],
                    "signedIdentity": {"type": "matchRepository"}}],
                "": [{"type": "insecureAcceptAnything"}],
            },
            "containers-storage": {"": [{"type": "insecureAcceptAnything"}]},
        },
    }


@pytest.fixture
def racine(tmp_path):
    """Une racine d'image telle que la laisse le module signing."""
    r = tmp_path / "racine"
    (r / "etc" / "containers" / "registries.d").mkdir(parents=True)
    (r / "usr" / "share" / "bluefox" / "keys").mkdir(parents=True)
    (r / "etc" / "containers" / "policy.json").write_text(json.dumps(politique_publiee()))
    (r / "etc" / "containers" / "registries.d" / "localhost-blue-fox-os-factice.yaml").write_text(
        "docker:\n  localhost/blue-fox-os-factice:\n    use-sigstore-attachments: true\n")
    shutil.copy(REGISTRIES, r / "etc" / "containers" / "registries.d" / REGISTRIES.name)
    shutil.copy(CLE, r / "usr" / "share" / "bluefox" / "keys" / CLE.name)
    shutil.copy(CLE_SECOURS, r / "usr" / "share" / "bluefox" / "keys" / CLE_SECOURS.name)
    return r


def jouer(racine):
    return subprocess.run(["bash", str(SCRIPT)], env={**os.environ, "BFOS_RACINE": str(racine)},
                          capture_output=True, text=True, timeout=60)


def politique(racine):
    return json.loads((racine / "etc" / "containers" / "policy.json").read_text())


# ---------------------------------------------------------------------------
# 1. Ce que livre le module files
# ---------------------------------------------------------------------------
def test_la_cle_des_images_est_celle_qui_signe():
    """La cle gravee dans l'image doit etre cosign.pub, celle que
    publish-image.sh verifie a l'etape 7. Une autre cle gele le parc."""
    assert CLE.read_bytes() == (REPO_ROOT / "cosign.pub").read_bytes()


def test_la_cle_de_secours_est_celle_retenue_et_n_est_que_publique():
    import hashlib
    contenu = CLE_SECOURS.read_bytes()
    assert hashlib.sha256(contenu).hexdigest() == EMPREINTE_SECOURS
    texte = contenu.decode()
    assert texte.startswith("-----BEGIN PUBLIC KEY-----\n")
    assert texte.rstrip().endswith("-----END PUBLIC KEY-----")
    assert "PRIVATE" not in texte
    assert contenu != CLE.read_bytes()
    # Aucune cle privee nulle part dans files/ (le module files copie tout).
    for f in (REPO_ROOT / "files").rglob("*"):
        if f.is_file() and f.stat().st_size < 1 << 20:
            assert b"PRIVATE KEY" not in f.read_bytes(), f


def test_registries_d_demande_les_pieces_jointes_pour_notre_espace_de_noms():
    texte = REGISTRIES.read_text()
    assert re.search(r"^docker:\n  ghcr\.io/bluefoxconsultant:\n    use-sigstore-attachments: true$",
                     "\n".join(l for l in texte.splitlines() if not l.startswith("#")), re.M)


def test_le_script_est_executable():
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


# ---------------------------------------------------------------------------
# 2. Le script, joue sur la politique publiee
# ---------------------------------------------------------------------------
def test_politique_rendue(racine):
    r = jouer(racine)
    assert r.returncode == 0, r.stderr
    pol = politique(racine)
    docker = pol["transports"]["docker"]
    assert not [k for k in docker if k.startswith("localhost")]
    for depot in DEPOTS:
        assert docker[depot] == [REGLE], depot
    # On ne durcit que NOS depots : toolbox et le reste passent comme avant.
    assert docker[""] == [{"type": "insecureAcceptAnything"}]
    assert "ghcr.io/ublue-os" in docker and "registry.access.redhat.com" in docker
    # Pas de regle d'espace de noms : il porte aussi des paquets non signes.
    assert "ghcr.io/bluefoxconsultant" not in docker
    assert pol["default"] == [{"type": "reject"}]
    assert pol["transports"]["containers-storage"] == {"": [{"type": "insecureAcceptAnything"}]}
    assert sorted(os.listdir(racine / "etc" / "containers" / "registries.d")) == [REGISTRIES.name]
    mode = (racine / "etc" / "containers" / "policy.json").stat().st_mode & 0o777
    assert mode == 0o644


def test_idempotent(racine):
    assert jouer(racine).returncode == 0
    premier = politique(racine)
    assert jouer(racine).returncode == 0
    assert politique(racine) == premier


def test_une_entree_existante_pour_nos_depots_est_remplacee(racine):
    """Si un jour le build pousse directement vers ghcr (IMAGE_REGISTRY juste),
    le module signing ecrira sa propre entree, avec SA cle : on la remplace."""
    p = racine / "etc" / "containers" / "policy.json"
    pol = json.loads(p.read_text())
    pol["transports"]["docker"][DEPOTS[0]] = [{"type": "insecureAcceptAnything"}]
    p.write_text(json.dumps(pol))
    assert jouer(racine).returncode == 0
    assert politique(racine)["transports"]["docker"][DEPOTS[0]] == [REGLE]


@pytest.mark.parametrize("defaut", [
    [{"type": "insecureAcceptAnything"}], [], None,
    [{"type": "reject"}, {"type": "insecureAcceptAnything"}],
])
def test_default_autre_que_reject_echoue_le_build(racine, defaut):
    """ostree-image-signed refuse toute mise a jour sous un `default`
    insecureAcceptAnything : le build doit echouer, pas l'installation."""
    p = racine / "etc" / "containers" / "policy.json"
    pol = json.loads(p.read_text())
    if defaut is None:
        del pol["default"]
    else:
        pol["default"] = defaut
    p.write_text(json.dumps(pol))
    r = jouer(racine)
    assert r.returncode != 0
    assert "default" in r.stderr


def test_sans_registries_d_le_build_echoue(racine):
    (racine / "etc" / "containers" / "registries.d" / REGISTRIES.name).unlink()
    r = jouer(racine)
    assert r.returncode != 0
    assert "use-sigstore-attachments" in r.stderr


def test_registries_d_a_false_ne_compte_pas(racine):
    (racine / "etc" / "containers" / "registries.d" / REGISTRIES.name).write_text(
        "docker:\n  ghcr.io/bluefoxconsultant:\n    use-sigstore-attachments: false\n")
    assert jouer(racine).returncode != 0


def test_une_portee_plus_precise_suffit(racine):
    (racine / "etc" / "containers" / "registries.d" / REGISTRIES.name).write_text(
        "docker:\n" + "".join(f"  {d}:\n    use-sigstore-attachments: true\n" for d in DEPOTS))
    assert jouer(racine).returncode == 0


def test_portee_en_double_echoue_le_build(racine):
    """containers-image refuse de lire un registries.d ou une portee figure
    dans deux fichiers : plus AUCUNE signature ne serait trouvee."""
    shutil.copy(REGISTRIES, racine / "etc" / "containers" / "registries.d" / "autre.yaml")
    r = jouer(racine)
    assert r.returncode != 0
    assert "plusieurs" in r.stderr


def test_doublon_de_portee_sans_pieces_jointes_echoue_aussi(racine):
    """Mesure skopeo 1.22.3 (2026-10-29) : une portee declaree deux fois casse
    la lecture du dossier meme si le second fichier ne parle pas de sigstore
    (« namespace ... defined both in ... »). Le doublon se cherche donc parmi
    TOUTES les portees docker."""
    (racine / "etc" / "containers" / "registries.d" / "z-autre.yaml").write_text(
        "docker:\n  ghcr.io/bluefoxconsultant:\n    lookaside-staging: file:///var/tmp/x\n")
    r = jouer(racine)
    assert r.returncode != 0
    assert "plusieurs" in r.stderr and "z-autre.yaml" in r.stderr


def test_default_docker_en_double_echoue(racine):
    d = racine / "etc" / "containers" / "registries.d"
    for nom in ("default.yaml", "z-autre.yaml"):
        (d / nom).write_text("default-docker:\n  lookaside-staging: file:///var/lib/containers/sigstore\n")
    r = jouer(racine)
    assert r.returncode != 0
    assert "default-docker" in r.stderr


def test_les_fichiers_de_fedora_ne_genent_pas(racine):
    """La forme livree par containers-common (Fedora 43)."""
    d = racine / "etc" / "containers" / "registries.d"
    (d / "default.yaml").write_text(
        "# This is a default registries.d configuration file.\n"
        "default-docker:\n#  lookaside: file:///var/lib/containers/sigstore\n"
        "  lookaside-staging: file:///var/lib/containers/sigstore\n")
    (d / "registry.access.redhat.com.yaml").write_text(
        "docker:\n     registry.access.redhat.com:\n"
        "         lookaside: https://access.redhat.com/webassets/docker/content/sigstore\n")
    assert jouer(racine).returncode == 0, jouer(racine).stderr


def test_un_false_plus_precis_annule_notre_true(racine):
    """Mesure skopeo 1.22.3 : `use-sigstore-attachments: false` sur le depot bf,
    dans un autre fichier, l'emporte sur notre `true` d'espace de noms
    (« no signature exists » pour bf, factice accepte)."""
    (racine / "etc" / "containers" / "registries.d" / "z-autre.yaml").write_text(
        f"docker:\n  {DEPOTS[0]}:\n    use-sigstore-attachments: false\n")
    r = jouer(racine)
    assert r.returncode != 0
    assert DEPOTS[0] in r.stderr and "z-autre.yaml" in r.stderr


def test_une_portee_plus_precise_sans_la_cle_ne_decide_pas(racine):
    """Mesure skopeo 1.22.3 : une portee plus precise qui ne regle pas
    use-sigstore-attachments laisse decider l'espace de noms (bf accepte)."""
    (racine / "etc" / "containers" / "registries.d" / "z-autre.yaml").write_text(
        f"docker:\n  {DEPOTS[0]}:\n    lookaside-staging: file:///var/tmp/x\n")
    assert jouer(racine).returncode == 0


def _script_mute(tmp_path, avant, apres):
    texte = SCRIPT.read_text()
    assert avant in texte, avant
    copie = tmp_path / "politique-mutee.sh"
    copie.write_text(texte.replace(avant, apres))
    return copie


@pytest.mark.parametrize("avant,apres", [
    # La regle en memoire est fausse : l'ancienne garde la comparait a elle-meme.
    ('regle = {"type": "sigstoreSigned", "keyPaths": [cle, cle_secours],',
     'regle = {"type": "sigstoreSigned", "keyPath": cle,'),
    ('regle = {"type": "sigstoreSigned", "keyPaths": [cle, cle_secours],',
     'regle = {"type": "sigstoreSigned", "keyPaths": [cle],'),
    # Ce qui part sur le disque n'est pas ce qui est en memoire.
    ("json.dump(pol, fh, indent=2)",
     'json.dump({"default": pol["default"], "transports": {"docker": {}}}, fh, indent=2)'),
    ("json.dump(pol, fh, indent=2)",
     'json.dump({**pol, "default": [{"type": "insecureAcceptAnything"}]}, fh, indent=2)'),
])
def test_la_garde_relit_ce_qui_est_ecrit(racine, tmp_path, avant, apres):
    copie = _script_mute(tmp_path, avant, apres)
    original = (racine / "etc" / "containers" / "policy.json").read_text()
    r = subprocess.run(["bash", str(copie)], env={**os.environ, "BFOS_RACINE": str(racine)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert "politique relue" in r.stderr
    # Rien n'a remplace la politique d'origine, et aucun .tmp ne traine.
    assert (racine / "etc" / "containers" / "policy.json").read_text() == original
    assert not (racine / "etc" / "containers" / "policy.json.tmp").exists()


@pytest.mark.parametrize("nom", [CLE.name, CLE_SECOURS.name])
def test_sans_cle_le_build_echoue(racine, nom):
    (racine / "usr" / "share" / "bluefox" / "keys" / nom).unlink()
    r = jouer(racine)
    assert r.returncode != 0
    assert nom in r.stderr


@pytest.mark.parametrize("nom", [CLE.name, CLE_SECOURS.name])
def test_une_cle_qui_n_en_est_pas_une_echoue(racine, nom):
    (racine / "usr" / "share" / "bluefox" / "keys" / nom).write_text("bonjour\n")
    assert jouer(racine).returncode != 0


def test_deux_fois_la_meme_cle_echoue(racine):
    shutil.copy(CLE, racine / "usr" / "share" / "bluefox" / "keys" / CLE_SECOURS.name)
    r = jouer(racine)
    assert r.returncode != 0
    assert "aucune cle de secours" in r.stderr


def test_la_regle_porte_les_deux_cles_et_rien_d_autre(racine):
    """containers-policy.json(5) : keyPath et keyPaths s'excluent ; une regle
    qui garderait keyPath a cote serait refusee a la lecture (parc gele)."""
    assert jouer(racine).returncode == 0
    for depot in DEPOTS:
        (regle,) = politique(racine)["transports"]["docker"][depot]
        assert "keyPath" not in regle and "keyData" not in regle
        assert regle["keyPaths"] == [CLE_SYSTEME, CLE_SECOURS_SYSTEME]


def test_sans_politique_le_build_echoue(racine):
    (racine / "etc" / "containers" / "policy.json").unlink()
    r = jouer(racine)
    assert r.returncode != 0
    assert "signing" in r.stderr


def test_politique_sans_transport_docker_echoue(racine):
    (racine / "etc" / "containers" / "policy.json").write_text(
        json.dumps({"default": [{"type": "reject"}], "transports": {}}))
    assert jouer(racine).returncode != 0


# ---------------------------------------------------------------------------
# 3. Branchement dans les recipes
# ---------------------------------------------------------------------------
def _types_de_modules(texte):
    return re.findall(r"^  - type: (\S+)", texte, re.M)


@pytest.mark.parametrize("nom", PUBLIABLES)
def test_le_script_tourne_apres_signing(nom):
    texte = (REPO_ROOT / "recipes" / nom).read_text()
    types = _types_de_modules(texte)
    assert "signing" in types
    i_signing = types.index("signing")
    blocs = re.split(r"^  - type: ", texte, flags=re.M)[1:]
    appels = [i for i, b in enumerate(blocs)
              if b.startswith("script") and "politique-conteneurs.sh" in b]
    assert len(appels) == 1, "politique-conteneurs.sh doit etre appele une fois"
    assert appels[0] > i_signing, "appele AVANT signing : signing rajouterait son entree localhost"
    assert appels[0] > types.index("files"), "la cle et le registries.d viennent du module files"
    # Meme resolution a deux racines que les autres scripts, et echec bruyant.
    assert re.search(r'exec bash "\$D/politique-conteneurs\.sh"; done; echo "ERREUR[^"]*" >&2; exit 1',
                     blocs[appels[0]])


@pytest.mark.parametrize("nom", PUBLIABLES)
def test_la_minuterie_de_bascule_est_activee(nom):
    texte = (REPO_ROOT / "recipes" / nom).read_text()
    systemd = re.search(r"^  - type: systemd\n(.*?)(?=^  - type: )", texte, re.M | re.S).group(1)
    enabled = systemd.split("masked:")[0]
    assert re.search(r"^\s+- bluefox-origine-signee\.timer\b", enabled, re.M)


# ---------------------------------------------------------------------------
# 4. bluefox-origine-signee
# ---------------------------------------------------------------------------
def _charger():
    loader = importlib.machinery.SourceFileLoader("bluefox_origine_signee", str(ORIGINE))
    spec = importlib.util.spec_from_loader("bluefox_origine_signee", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


os_ = _charger()


@pytest.fixture
def pol_rendue(racine):
    assert jouer(racine).returncode == 0
    return str(racine / "etc" / "containers" / "policy.json")


def test_portees_dans_l_ordre_de_containers_image():
    assert os_.portees("ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest") == [
        "ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest",
        "ghcr.io/bluefoxconsultant/blue-fox-os-bf",
        "ghcr.io/bluefoxconsultant", "ghcr.io"]
    assert os_.portees("ostree-unverified-registry:ghcr.io/x/y@sha256:ab")[:2] == [
        "ghcr.io/x/y@sha256:ab", "ghcr.io/x/y"]
    # Un port n'est pas un tag.
    assert os_.portees("registre:5000/x/y")[0] == "registre:5000/x/y"


def test_la_politique_rendue_couvre_nos_trois_depots(pol_rendue):
    for depot in DEPOTS:
        assert os_.politique_couvre(f"{depot}:latest", pol_rendue)
        assert os_.politique_couvre(f"ostree-unverified-registry:{depot}:latest", pol_rendue)


def test_la_politique_publiee_ne_couvre_rien(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(politique_publiee()))
    assert not os_.politique_couvre(f"{DEPOTS[2]}:latest", str(p))


def test_autre_locataire_et_portee_vide_ne_comptent_pas(pol_rendue):
    assert not os_.politique_couvre("ghcr.io/acme/blue-fox-os-acme:latest", pol_rendue)
    assert not os_.politique_couvre("quay.io/x/y:1", pol_rendue)


def test_la_portee_la_plus_precise_decide(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"default": [{"type": "reject"}], "transports": {"docker": {
        "ghcr.io/bluefoxconsultant": [REGLE],
        DEPOTS[0]: [{"type": "insecureAcceptAnything"}]}}}))
    assert not os_.politique_couvre(f"{DEPOTS[0]}:latest", str(p))
    assert os_.politique_couvre(f"{DEPOTS[1]}:latest", str(p))


def test_politique_illisible_ne_couvre_rien(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text("{pas du json")
    assert not os_.politique_couvre(f"{DEPOTS[0]}:latest", str(p))
    assert not os_.politique_couvre(f"{DEPOTS[0]}:latest", str(tmp_path / "absente.json"))
    assert os_.defaut_insecure(str(p))      # dans le doute : on ne bascule pas


SANS_CONTENEUR = object()   # deploiement en attente sans reference conteneur


class FauxRpmOstree:
    def __init__(self, booted, prochain=None, check=77, rebase=0, rebase_err="rebase",
                 delai=None):
        dep = [{"booted": True, "container-image-reference": booted}]
        if prochain is SANS_CONTENEUR:
            dep.insert(0, {"booted": False, "origin": "fedora:fedora/43/x86_64/kinoite"})
        elif prochain:
            dep.insert(0, {"booted": False, "container-image-reference": prochain})
        self.status = json.dumps({"deployments": dep})
        self.check, self.rebase, self.rebase_err, self.appels = check, rebase, rebase_err, []
        self.delai = delai      # sous-commande qui depasse son delai

    def __call__(self, cmd):
        self.appels.append(cmd)
        assert cmd[0] == "rpm-ostree", f"commande inattendue : {cmd}"
        if self.delai and cmd[1] == self.delai:
            raise subprocess.TimeoutExpired(cmd, 3600)
        if cmd[1] == "status":
            return subprocess.CompletedProcess(cmd, 0, self.status, "")
        if cmd[1:] == ["upgrade", "--check"]:
            return subprocess.CompletedProcess(cmd, self.check, "", "check")
        if cmd[1] == "rebase":
            return subprocess.CompletedProcess(cmd, self.rebase, "", self.rebase_err)
        raise AssertionError(f"commande inattendue : {cmd}")


NON_VERIFIEE = f"ostree-unverified-registry:{DEPOTS[0]}:latest"
SIGNEE = f"ostree-image-signed:docker://{DEPOTS[0]}:latest"


def test_bascule_nominale_sans_redemarrage(pol_rendue, tmp_path):
    run = FauxRpmOstree(NON_VERIFIEE)
    marqueur = tmp_path / "m" / "origine-signee"
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(marqueur)) == 0
    assert run.appels[-2] == ["rpm-ostree", "upgrade", "--check"]
    # Meme depot, meme tag : seul le transport change. Aucun --reboot.
    assert run.appels[-1] == ["rpm-ostree", "rebase", SIGNEE]
    # Marqueur seulement une fois DEMARRE sur l'origine signee.
    assert not marqueur.exists()


def test_deja_signee_pose_le_marqueur(pol_rendue, tmp_path):
    run = FauxRpmOstree(SIGNEE)
    marqueur = tmp_path / "m" / "origine-signee"
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(marqueur)) == 0
    assert marqueur.exists()
    assert [c for c in run.appels if c[1] != "status"] == []


def test_signee_en_attente_ne_rebase_pas_deux_fois(pol_rendue, tmp_path):
    run = FauxRpmOstree(NON_VERIFIEE, prochain=SIGNEE)
    marqueur = tmp_path / "origine-signee"
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(marqueur)) == 0
    assert [c for c in run.appels if c[1] != "status"] == []
    assert not marqueur.exists()


def test_marqueur_present_ne_fait_rien(pol_rendue, tmp_path):
    marqueur = tmp_path / "origine-signee"
    marqueur.write_text("x")
    run = FauxRpmOstree(NON_VERIFIEE)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(marqueur)) == 0
    assert run.appels == []


def test_politique_pas_encore_a_jour_on_attend(tmp_path):
    """Le poste n'a pas encore l'image qui porte la politique : rien, code 0."""
    p = tmp_path / "policy.json"
    p.write_text(json.dumps(politique_publiee()))
    run = FauxRpmOstree(NON_VERIFIEE)
    assert os_.basculer(run=run, policy_path=str(p), marqueur=str(tmp_path / "m")) == 0
    assert [c for c in run.appels if c[1] != "status"] == []


def test_default_permissif_on_ne_bascule_pas(pol_rendue, tmp_path):
    pol = json.loads(Path(pol_rendue).read_text())
    pol["default"] = [{"type": "insecureAcceptAnything"}]
    Path(pol_rendue).write_text(json.dumps(pol))
    run = FauxRpmOstree(NON_VERIFIEE)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert [c for c in run.appels if c[1] == "rebase"] == []


def test_sonde_en_echec_on_reessaie_plus_tard(pol_rendue, tmp_path):
    run = FauxRpmOstree(NON_VERIFIEE, check=1)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert [c for c in run.appels if c[1] == "rebase"] == []


def test_sonde_avec_mise_a_jour_disponible_bascule(pol_rendue, tmp_path):
    run = FauxRpmOstree(NON_VERIFIEE, check=0)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert run.appels[-1] == ["rpm-ostree", "rebase", SIGNEE]


def test_rebase_en_echec_se_voit(pol_rendue, tmp_path):
    run = FauxRpmOstree(NON_VERIFIEE, rebase=1)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 1
    assert not (tmp_path / "m").exists()


def test_sans_origine_conteneur_rien(pol_rendue, tmp_path):
    run = FauxRpmOstree(None)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert [c for c in run.appels if c[1] != "status"] == []


def test_origine_remote_ostree_intouchee(pol_rendue, tmp_path):
    run = FauxRpmOstree(f"ostree-remote-image:fedora:docker://{DEPOTS[0]}:latest")
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert [c for c in run.appels if c[1] != "status"] == []


@pytest.mark.parametrize("prochain", [
    # rebase manuel vers un autre depot, encore en attente
    f"ostree-unverified-registry:{DEPOTS[1]}:latest",
    f"ostree-image-signed:docker://{DEPOTS[1]}:latest",
    # version epinglee par digest
    f"ostree-unverified-registry:{DEPOTS[0]}@sha256:" + "a" * 64,
    # autre tag du meme depot
    f"ostree-unverified-registry:{DEPOTS[0]}:20261025",
    # deploiement en attente qui n'est plus une image conteneur
    SANS_CONTENEUR,
])
def test_un_choix_en_attente_vers_une_autre_image_n_est_pas_ecrase(pol_rendue, tmp_path, prochain,
                                                                     capsys):
    """M1 de la relecture : la cible venait de l'origine DEMARREE. Un rebase
    manuel bf -> bf-surface, ou une version epinglee, en attente du prochain
    demarrage, aurait ete ecrase par un rebase vers bf:latest signe."""
    run = FauxRpmOstree(NON_VERIFIEE, prochain=prochain)
    marqueur = tmp_path / "m"
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(marqueur)) == 0
    assert [c for c in run.appels if c[1] != "status"] == []
    assert not marqueur.exists()
    assert "autre image" in capsys.readouterr().out


def test_une_mise_a_jour_en_attente_du_meme_depot_n_empeche_pas_la_bascule(pol_rendue, tmp_path):
    """Meme reference, seul le deploiement change (mise a jour en attente) :
    la bascule a lieu, vers cette meme reference."""
    run = FauxRpmOstree(NON_VERIFIEE, prochain=f"ostree-unverified-image:docker://{DEPOTS[0]}:latest")
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert run.appels[-1] == ["rpm-ostree", "rebase", SIGNEE]


def test_apres_le_demarrage_du_choix_la_bascule_suit_sa_reference(pol_rendue, tmp_path):
    epinglee = f"ostree-unverified-registry:{DEPOTS[1]}@sha256:" + "b" * 64
    run = FauxRpmOstree(epinglee)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert run.appels[-1] == ["rpm-ostree", "rebase",
                              f"ostree-image-signed:docker://{DEPOTS[1]}@sha256:" + "b" * 64]


@pytest.mark.parametrize("etape", ["status", "upgrade", "rebase"])
def test_delai_depasse_est_passager(pol_rendue, tmp_path, etape, capsys):
    run = FauxRpmOstree(NON_VERIFIEE, delai=etape)
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert "depasse" in capsys.readouterr().out
    assert not (tmp_path / "m").exists()


def test_transaction_deja_en_cours_est_passagere(pol_rendue, tmp_path):
    run = FauxRpmOstree(NON_VERIFIEE, rebase=1, rebase_err=(
        "error: Transaction in progress: upgrade\n"
        " You can cancel the current transaction with `rpm-ostree cancel`"))
    assert os_.basculer(run=run, policy_path=pol_rendue, marqueur=str(tmp_path / "m")) == 0
    assert run.appels[-1][1] == "rebase"


def test_cli_politique_couvre(pol_rendue, monkeypatch):
    monkeypatch.setattr(os_, "POLICY", pol_rendue)
    assert os_.main(["--politique-couvre", f"{DEPOTS[0]}:latest"]) == 0
    assert os_.main(["--politique-couvre", "ghcr.io/acme/x:latest"]) == 1
    pol = json.loads(Path(pol_rendue).read_text())
    pol["default"] = [{"type": "insecureAcceptAnything"}]
    Path(pol_rendue).write_text(json.dumps(pol))
    # Couvert, mais ostree-image-signed refuserait : l'ancien transport reste.
    assert os_.main(["--politique-couvre", f"{DEPOTS[0]}:latest"]) == 1


def test_aucun_redemarrage_nulle_part():
    code = "\n".join(l for l in ORIGINE.read_text().splitlines() if not l.lstrip().startswith("#"))
    code = re.sub(r'"""(.*?)"""', "", code, flags=re.S)
    assert "reboot" not in code and "systemctl" not in code
    service = (UNITES / "bluefox-origine-signee.service").read_text()
    assert "ExecStart=/usr/libexec/bluefox-origine-signee\n" in service
    assert "reboot" not in service.lower().replace("ne redemarre jamais", "")
    assert "ConditionPathExists=!/var/lib/bluefox/origine-signee" in service
    assert "ConditionPathExists=!/var/lib/bluefox-welcome/needs-rebase" in service
    assert "Before=" not in service and "RequiredBy" not in service
    assert ORIGINE.stat().st_mode & stat.S_IXUSR
    timer = (UNITES / "bluefox-origine-signee.timer").read_text()
    assert "OnCalendar=daily" in timer and "WantedBy=timers.target" in timer


# ---------------------------------------------------------------------------
# 5. bluefox-rebase
# ---------------------------------------------------------------------------
def test_bluefox_rebase_choisit_son_transport_sur_la_politique():
    code = [l for l in REBASE.read_text().splitlines() if not l.lstrip().startswith("#")]
    texte = "\n".join(code)
    assert 'TRANSPORT="ostree-unverified-image:docker://"' in texte
    assert re.search(r'if /usr/libexec/bluefox-origine-signee --politique-couvre "\$IMAGE_REF" ; then\n'
                     r'\s+TRANSPORT="ostree-image-signed:docker://"\nfi', texte)
    rebases = [l for l in code if "rpm-ostree rebase" in l]
    assert rebases == ['if rpm-ostree rebase "${TRANSPORT}${IMAGE_REF}" ; then']


# ---------------------------------------------------------------------------
# 6. Porte canari de publish-image.sh (etape 7b)
# ---------------------------------------------------------------------------
def _bloc_canari():
    texte = PUBLISH.read_text()
    debut = texte.index("# --- 7b. porte canari")
    fin = texte.index('log "OK — ${IMAGE} publie')
    assert texte.index("cosign verify --key cosign.pub") < debut, "la porte suit la verification"
    return texte[debut:fin]


FAUX_PODMAN = r"""#!/bin/bash
echo "$*" >> "$JOURNAL"
[ "$1" = rmi ] && exit 0
case "$*" in
  *canari-non-signe*) MODE="$FAUX_NON_SIGNE" ;;
  *canari-signe*) MODE="$FAUX_SIGNE" ;;
esac
case "$MODE" in
  ok) echo "Writing manifest to image destination"; exit 0 ;;
  rejet) echo 'level=fatal msg="Source image rejected: A signature was required, but no signature exists"' >&2; exit 1 ;;
  cle) echo 'level=fatal msg="Source image rejected: cryptographic signature verification failed"' >&2; exit 1 ;;
  reseau) echo 'level=fatal msg="pinging container registry ghcr.io: dial tcp: i/o timeout"' >&2; exit 1 ;;
  absent) echo 'level=fatal msg="reading manifest canari in ghcr.io/x: manifest unknown"' >&2; exit 1 ;;
esac
exit 3
"""


def jouer_canari(tmp_path, signe, non_signe, skip=False):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "podman").write_text(FAUX_PODMAN)
    (bin_ / "podman").chmod(0o755)
    journal = tmp_path / "podman.log"
    script = ("set -euo pipefail\n"
              'log() { echo "[publish] $*"; }\n'
              'die() { echo "[publish] ERREUR: $*" >&2; exit 1; }\n'
              # Le trap ERR que publish-image.sh pose apres la poussee : un echec
              # ATTENDU (canari non signe refuse) ne doit pas le declencher.
              "trap 'echo TRAP_ERR >&2; exit 99' ERR\n"
              "REGISTRY=ghcr.io/bluefoxconsultant\n"
              "IMAGE_REPO=$REGISTRY/blue-fox-os-bf\nIMAGE=$IMAGE_REPO:latest\n"
              "IMAGE_INDEX_DIGEST=sha256:nouveau\nPRECEDENT_DIGEST=sha256:ancien\n"
              + _bloc_canari() + 'log "FIN"\n')
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "JOURNAL": str(journal),
           "FAUX_SIGNE": signe, "FAUX_NON_SIGNE": non_signe}
    if skip:
        env["PUBLISH_SKIP_CANARI"] = "1"
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    appels = journal.read_text().splitlines() if journal.exists() else []
    return r, appels


def test_canari_nominal(tmp_path):
    r, appels = jouer_canari(tmp_path, "ok", "rejet")
    assert r.returncode == 0, r.stderr
    assert "TRAP_ERR" not in r.stderr
    assert "[publish] FIN" in r.stdout
    runs = [a for a in appels if a.startswith("run ")]
    assert len(runs) == 2
    for a in runs:
        # La politique ET le skopeo de l'image publiee, par digest.
        assert "ghcr.io/bluefoxconsultant/blue-fox-os-bf@sha256:nouveau" in a
        assert "--entrypoint /usr/bin/skopeo" in a
    assert "docker://ghcr.io/bluefoxconsultant/blue-fox-os-factice:canari-signe" in runs[0]
    assert "docker://ghcr.io/bluefoxconsultant/blue-fox-os-factice:canari-non-signe" in runs[1]


def test_canari_signe_refuse_arrete_et_dit_le_retour_arriere(tmp_path):
    r, _ = jouer_canari(tmp_path, "cle", "rejet")
    assert r.returncode == 1
    assert "REFUSE l'image signee" in r.stderr
    assert "docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf@sha256:ancien" in r.stderr


def test_canari_non_signe_accepte_arrete(tmp_path):
    r, _ = jouer_canari(tmp_path, "ok", "ok")
    assert r.returncode == 1
    assert "ACCEPTE l'image NON signee" in r.stderr


def test_canari_non_signe_en_panne_reseau_n_est_pas_un_refus(tmp_path):
    r, _ = jouer_canari(tmp_path, "ok", "reseau")
    assert r.returncode == 1
    assert "pas sur la politique" in r.stderr


@pytest.mark.parametrize("signe,non_signe", [("absent", "rejet"), ("ok", "absent")])
def test_tag_canari_absent_arrete(tmp_path, signe, non_signe):
    r, _ = jouer_canari(tmp_path, signe, non_signe)
    assert r.returncode == 1
    assert "tag canari absent" in r.stderr


def test_canari_saute_seulement_en_le_disant(tmp_path):
    r, appels = jouer_canari(tmp_path, "cle", "ok", skip=True)
    assert r.returncode == 0
    assert "PUBLISH_SKIP_CANARI=1" in r.stdout
    assert appels == []


def test_digest_precedent_lu_avant_la_poussee():
    texte = PUBLISH.read_text()
    assert texte.index('PRECEDENT_DIGEST="sha256:') < texte.index('\nbluebuild build --push "recipes/')
