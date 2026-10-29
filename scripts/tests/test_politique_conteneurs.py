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
  5. la porte canari de publish-image.sh (etape 4b), jouee avec un faux podman et
     un faux skopeo ; son juge, scripts/proxy-openimage.py, contre un faux proxy.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
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
# 6. Porte canari de publish-image.sh (etape 4b)
# ---------------------------------------------------------------------------
PROXY = REPO_ROOT / "scripts" / "proxy-openimage.py"


def _bloc_canari():
    texte = PUBLISH.read_text()
    debut = texte.index("# --- 4b. porte canari")
    fin = texte.index("# --- 5. SBOM")
    return texte[debut:fin]


def test_la_porte_suit_la_poussee_et_precede_sbom_et_attestation():
    """M2 de la relecture : la porte arrivait apres SBOM et attestation."""
    t = PUBLISH.read_text()
    ordre = [t.index('PRECEDENT_DIGEST="sha256:'),
             t.index('\nbluebuild build --push "recipes/'),
             t.index("\nPUSHED=1\n"),
             t.index('IMAGE_INDEX_DIGEST="sha256:'),
             t.index("# --- 4b. porte canari"),
             t.index("# --- 5. SBOM"),
             t.index("\ncosign attest --yes"),
             t.index("\ncosign verify --key cosign.pub")]
    assert ordre == sorted(ordre)
    assert "7b" not in t.replace("#  7b.", "")


ANCIEN = "index-ancien-signe\n"
NOUVEAU = "index-nouveau\n"


def _sha(texte):
    import hashlib
    return "sha256:" + hashlib.sha256(texte.encode()).hexdigest()


FAUX_PODMAN = r"""#!/bin/bash
echo "podman $*" >> "$JOURNAL"
case "$1" in
  rmi) exit 0 ;;
  pull) [ "$FAUX_PULL" = ok ] && exit 0; echo "Error: initializing source: dial tcp: i/o timeout" >&2; exit 125 ;;
  run) ;;
  *) exit 99 ;;
esac
# Le juge doit arriver par l'entree standard, et c'est proxy-openimage.py.
grep -q "experimental-image-proxy" || { echo "pas le juge" >&2; exit 98; }
REF="${!#}"
case "$REF" in
  *canari-non-signe) MODE="$FAUX_NON_SIGNE" ;;
  *canari-signe) MODE="$FAUX_SIGNE" ;;
  *) MODE="$FAUX_IMAGE" ;;
esac
case "$MODE" in
  ok) echo "OpenImage $REF : ACCEPTE (protocole 0.2.8)"; exit 0 ;;
  rejet) echo "OpenImage $REF : REFUSE : A signature was required, but no signature exists"; exit 1 ;;
  cle) echo "OpenImage $REF : REFUSE : cryptographic signature verification failed: invalid signature"; exit 1 ;;
  reseau) echo "OpenImage $REF : INCONNU : reading signatures: Get x: i/o timeout"; exit 2 ;;
  absent) echo "OpenImage $REF : INCONNU : reading manifest canari in ghcr.io/x: manifest unknown"; exit 2 ;;
  sans_skopeo) echo "OpenImage $REF : INCONNU : skopeo introuvable ([Errno 2] No such file)"; exit 2 ;;
  sans_python) echo 'Error: crun: executable file `/usr/bin/python3` not found in $PATH' >&2; exit 127 ;;
esac
exit 97
"""

FAUX_SKOPEO = r"""#!/bin/bash
echo "skopeo $*" >> "$JOURNAL"
case "$1" in
  copy) [ "$FAUX_COPIE" = echec ] && exit 1
        [ "$FAUX_COPIE" = ok ] && printf '%s' "$ANCIEN" > "$ETAT_TAG"; exit 0 ;;
  inspect) cat "$ETAT_TAG"; exit 0 ;;
esac
exit 96
"""


def jouer_canari(tmp_path, image="ok", signe="ok", non_signe="rejet", skip=False,
                 copie="ok", precedent=True, pull="ok"):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for nom, code in (("podman", FAUX_PODMAN), ("skopeo", FAUX_SKOPEO)):
        (bin_ / nom).write_text(code)
        (bin_ / nom).chmod(0o755)
    journal = tmp_path / "appels.log"
    etat_tag = tmp_path / "tag"
    etat_tag.write_text(NOUVEAU)
    script = ("set -euo pipefail\n"
              'log() { echo "[publish] $*"; }\n'
              'die() { echo "[publish] ERREUR: $*" >&2; exit 1; }\n'
              'post_push_notice() { echo "NOTICE_PUSH" >&2; }\n'
              # Les traps que publish-image.sh pose apres la poussee : un echec
              # ATTENDU (canari non signe refuse) ne doit pas les declencher.
              "trap 'echo TRAP_ERR >&2; exit 99' ERR\n"
              f"WORKDIR={REPO_ROOT}\n"
              "REGISTRY=ghcr.io/bluefoxconsultant\nPORTE_ETAT=x\n"
              "IMAGE_REPO=$REGISTRY/blue-fox-os-bf\nIMAGE=$IMAGE_REPO:latest\n"
              "IMAGE_INDEX_DIGEST=sha256:nouveau\n"
              f"PRECEDENT_DIGEST={_sha(ANCIEN) if precedent else ''}\n"
              + _bloc_canari() + 'log "FIN porte=$PORTE_ETAT"\n')
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "JOURNAL": str(journal),
           "ETAT_TAG": str(etat_tag), "ANCIEN": ANCIEN, "FAUX_PULL": pull,
           "FAUX_IMAGE": image, "FAUX_SIGNE": signe, "FAUX_NON_SIGNE": non_signe,
           "FAUX_COPIE": copie}
    env.pop("PUBLISH_SKIP_CANARI", None)
    if skip:
        env["PUBLISH_SKIP_CANARI"] = "1"
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    appels = journal.read_text().splitlines() if journal.exists() else []
    return r, appels, etat_tag.read_text()


def _copies(appels):
    return [a for a in appels if a.startswith("skopeo copy")]


def test_canari_nominal(tmp_path):
    r, appels, tag = jouer_canari(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "TRAP_ERR" not in r.stderr and "NOTICE_PUSH" not in r.stderr
    assert "[publish] FIN porte=passee" in r.stdout
    assert appels[0] == "podman pull -q ghcr.io/bluefoxconsultant/blue-fox-os-bf@sha256:nouveau"
    runs = [a for a in appels if a.startswith("podman run ")]
    assert len(runs) == 3
    for a in runs:
        # Le juge tourne DANS l'image publiee, par digest, sans nouveau tirage.
        assert "--pull=never" in a and "--entrypoint /usr/bin/python3" in a
        assert " ghcr.io/bluefoxconsultant/blue-fox-os-bf@sha256:nouveau - docker://" in a
    # M3 : l'image publiee elle-meme est jugee, par le chemin de rpm-ostree.
    assert runs[0].endswith("docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf@sha256:nouveau")
    assert runs[1].endswith("docker://ghcr.io/bluefoxconsultant/blue-fox-os-factice:canari-signe")
    assert runs[2].endswith("docker://ghcr.io/bluefoxconsultant/blue-fox-os-factice:canari-non-signe")
    assert "podman rmi ghcr.io/bluefoxconsultant/blue-fox-os-bf@sha256:nouveau" in appels
    assert _copies(appels) == [] and tag == NOUVEAU


@pytest.mark.parametrize("cas", [dict(signe="cle"), dict(signe="rejet"), dict(image="cle"),
                                 dict(image="rejet"), dict(image="sans_skopeo")])
def test_refus_de_la_politique_remet_le_tag_precedent(tmp_path, cas):
    r, appels, tag = jouer_canari(tmp_path, **cas)
    assert r.returncode == 3, r.stderr
    assert "TRAP_ERR" not in r.stderr
    assert _copies(appels) == [
        "skopeo copy --all --preserve-digests --retry-times 3 "
        f"docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf@{_sha(ANCIEN)} "
        "docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest"]
    assert tag == ANCIEN
    assert "REMIS sur l'index precedent" in r.stderr
    # Le message vrai, pas celui d'un SBOM manquant apres une chaine complete.
    assert "NOTICE_PUSH" not in r.stderr
    assert "SBOM et attestation NON produits" in r.stderr
    assert "Code 3" in r.stderr


def test_retour_arriere_impossible_le_dit(tmp_path):
    r, appels, tag = jouer_canari(tmp_path, signe="cle", copie="echec")
    assert r.returncode == 3
    assert "IMPOSSIBLE" in r.stderr and "TOUJOURS l'image refusee" in r.stderr
    assert f"docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf@{_sha(ANCIEN)}" in r.stderr
    assert tag == NOUVEAU


def test_retour_arriere_relu_au_registre(tmp_path):
    """La copie rend 0 mais le tag ne sert pas l'ancien index : IMPOSSIBLE,
    jamais « remis » sur la foi du code de retour."""
    r, appels, tag = jouer_canari(tmp_path, signe="cle", copie="muette")
    assert r.returncode == 3
    assert any(a == "skopeo inspect --raw docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest"
               for a in appels)
    assert "IMPOSSIBLE" in r.stderr and "REMIS" not in r.stderr
    assert tag == NOUVEAU


def test_premiere_publication_sans_precedent(tmp_path):
    r, appels, tag = jouer_canari(tmp_path, signe="cle", precedent=False)
    assert r.returncode == 3
    assert _copies(appels) == []
    assert "premiere publication" in r.stderr


@pytest.mark.parametrize("cas,attendu", [
    (dict(non_signe="ok"), "ACCEPTE l'image NON signee"),
    (dict(non_signe="reseau"), "pas sur la politique"),
    (dict(signe="reseau"), "verdict INCONNU"),
    (dict(image="reseau"), "verdict INCONNU"),
    (dict(signe="absent"), "tag canari absent"),
    (dict(non_signe="absent"), "tag canari absent"),
    (dict(image="sans_python"), "le juge n'a pas tourne"),
    (dict(pull="echec"), "tirage de"),
])
def test_echec_sans_verdict_de_refus_arrete_sans_retour_arriere(tmp_path, cas, attendu):
    """M3 : un i/o timeout n'est pas un refus. Code 3 (la boucle s'arrete),
    mais l'image reste : ni la politique ni le parc ne sont en cause."""
    r, appels, tag = jouer_canari(tmp_path, **cas)
    assert r.returncode == 3, r.stderr
    assert attendu in r.stderr
    assert "TRAP_ERR" not in r.stderr and "NOTICE_PUSH" not in r.stderr
    assert _copies(appels) == [] and tag == NOUVEAU
    assert "reste EN LIGNE" in r.stderr
    assert f"docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf@{_sha(ANCIEN)}" in r.stderr


def test_canari_saute_seulement_en_le_disant(tmp_path):
    r, appels, _ = jouer_canari(tmp_path, signe="cle", non_signe="ok", skip=True)
    assert r.returncode == 0
    assert "PUBLISH_SKIP_CANARI=1" in r.stdout
    assert "porte=SAUTEE" in r.stdout
    assert appels == []


def test_digest_precedent_lu_avant_la_poussee():
    texte = PUBLISH.read_text()
    assert texte.index('PRECEDENT_DIGEST="sha256:') < texte.index('\nbluebuild build --push "recipes/')


def test_le_code_3_est_reserve_a_la_porte():
    """Un 3 rendu par une autre commande devient 1 : maj-images.sh n'arrete sa
    boucle que pour la porte."""
    t = PUBLISH.read_text()
    traps = re.findall(r"^trap '(.*)' ERR$", t, re.M)
    assert len(traps) == 2
    for trap in traps:
        assert '[ "$rc" = 3 ] && rc=1' in trap
    assert t.index("trap 'rc=$?; [ \"$rc\" = 3 ] && rc=1; exit $rc' ERR") < t.index('SLUG="${1:-')
    # Le seul « exit 3 » du script passe par CANARI_RC, dans die_canari.
    assert re.findall(r"\bexit 3\b", t) == []
    assert t.count('exit "$CANARI_RC"') == 1


def test_post_push_notice_dit_l_etat_de_la_porte():
    t = PUBLISH.read_text()
    corps = t[t.index("post_push_notice() {"):t.index("\n}\n", t.index("post_push_notice() {"))]
    assert "${PORTE_ETAT}" in corps
    assert 'PORTE_ETAT="pas encore jouee"' in t


# ---------------------------------------------------------------------------
# 7. Le juge : scripts/proxy-openimage.py contre un faux proxy skopeo
# ---------------------------------------------------------------------------
FAUX_PROXY = r"""#!/usr/bin/env python3
import json, os, socket, sys, time
fd = int(sys.argv[sys.argv.index("--sockfd") + 1])
s = socket.socket(fileno=fd)
mode = os.environ["FAUX_PROXY"]
while True:
    msg = s.recv(65536)
    if not msg:
        break
    req = json.loads(msg)
    m = req["method"]
    if m == "Initialize":
        rep = {"success": True, "value": "0.2.8", "pipeid": 0, "error": ""}
    elif m == "OpenImage":
        if mode == "muet":
            time.sleep(30)
        if mode == "meurt":
            sys.exit(1)
        erreurs = {
            "ok": None,
            "sans_signature": "A signature was required, but no signature exists",
            "cle": "cryptographic signature verification failed: invalid signature when validating ASN.1 encoded signature",
            "reject": "Running image docker://alpine:latest is rejected by policy.",
            "plusieurs": "None of the signatures were accepted, reasons: a; b",
            "timeout_signatures": "reading signatures: Get \"https://ghcr.io/v2/x\": dial tcp: i/o timeout",
            "refus_et_panne": "None of the signatures were accepted, reasons: reading signature 1: unexpected EOF",
            "manifeste": "reading manifest x in ghcr.io/y: manifest unknown",
            "lookaside": "Get \"http://127.0.0.1:9/sigs/x@sha256=643b502c/signature-1\": dial tcp 127.0.0.1:9: connect: connection refused",
            "503": "received unexpected HTTP status: 503 Service Unavailable",
            "digest_502": "cryptographic signature verification failed for sha256:502abc",
        }
        e = erreurs[mode]
        rep = {"success": e is None, "value": 1, "pipeid": 0, "error": e or ""}
    else:
        rep = {"success": True, "value": None, "pipeid": 0, "error": ""}
    s.send(json.dumps(rep).encode())
"""


def _juge():
    loader = importlib.machinery.SourceFileLoader("proxy_openimage", str(PROXY))
    spec = importlib.util.spec_from_loader("proxy_openimage", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


juge = _juge()


@pytest.fixture
def faux_proxy(tmp_path):
    f = tmp_path / "skopeo"
    f.write_text(FAUX_PROXY)
    f.chmod(0o755)
    return str(f)


@pytest.mark.parametrize("mode,code", [
    ("ok", 0),
    ("sans_signature", 1), ("cle", 1), ("reject", 1), ("plusieurs", 1), ("digest_502", 1),
    ("timeout_signatures", 2), ("refus_et_panne", 2), ("manifeste", 2), ("lookaside", 2),
    ("503", 2), ("meurt", 2),
])
def test_le_juge_ne_conclut_au_refus_que_sur_un_verdict(faux_proxy, monkeypatch, mode, code):
    """M3 : « reading signatures: i/o timeout » n'est pas un refus. Les
    messages viennent des mesures skopeo 1.22.3 du 2026-10-29."""
    monkeypatch.setenv("FAUX_PROXY", mode)
    rc, ligne = juge.juger("docker://ghcr.io/x/y:latest", skopeo=faux_proxy, delai=10)
    assert rc == code, ligne
    assert ligne.startswith("OpenImage docker://ghcr.io/x/y:latest : ")
    assert {0: "ACCEPTE", 1: "REFUSE", 2: "INCONNU"}[code] in ligne


def test_le_juge_ne_reste_pas_pendu(faux_proxy, monkeypatch):
    monkeypatch.setenv("FAUX_PROXY", "muet")
    import time
    t0 = time.monotonic()
    rc, ligne = juge.juger("docker://ghcr.io/x/y:latest", skopeo=faux_proxy, delai=1)
    assert rc == 2 and "INCONNU" in ligne
    assert time.monotonic() - t0 < 20


def test_le_juge_sans_skopeo(tmp_path):
    rc, ligne = juge.juger("docker://ghcr.io/x/y:latest", skopeo=str(tmp_path / "absent"))
    assert rc == 2 and "INCONNU : skopeo introuvable" in ligne


def test_le_juge_par_l_entree_standard(faux_proxy):
    """La forme de publish-image.sh : python3 - docker://REF < proxy-openimage.py."""
    env = {**os.environ, "FAUX_PROXY": "cle", "BF_SKOPEO": faux_proxy, "BF_PROXY_DELAI": "10"}
    r = subprocess.run([sys.executable, "-", "docker://ghcr.io/x/y:latest"], stdin=PROXY.open(),
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, r.stderr
    assert "REFUSE : cryptographic signature verification failed" in r.stdout
    r = subprocess.run([sys.executable, "-", "ghcr.io/sans-transport"], stdin=PROXY.open(),
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
