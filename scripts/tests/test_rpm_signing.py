"""Le RPM bluefox-welcome est signe, et l'image refuse de l'installer sinon.

Audit P5.2 du 2026-07-22, constat E1 (#23811) : de tous les paquets superposes a
l'image, un seul n'etait pas signe — le notre, celui qui porte le welcome agent,
donc le provisionnement OIDC et l'acquisition du credential Nextcloud.

Trois proprietes a tenir, et chacune se perd differemment :
  - la chaine de PUBLICATION exige une cle (un rpmbuild de dev, non) ;
  - la cle privee ne descend jamais dans le container de build du RPM ;
  - l'image VERIFIE la signature avant d'installer — `rpm-ostree install` sur un
    fichier local ne verifie rien du tout.
"""

import re
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_RPM = REPO_ROOT / "scripts" / "build-welcome-rpm.sh"
SIGN_RPM = REPO_ROOT / "scripts" / "sign-welcome-rpm.sh"
GEN_KEY = REPO_ROOT / "scripts" / "generate-rpm-signing-key.sh"
INSTALL_RPM = REPO_ROOT / "files" / "scripts" / "install-welcome-rpm.sh"
PUBLISH = REPO_ROOT / "scripts" / "publish-image.sh"
RECIPES = sorted((REPO_ROOT / "recipes").glob("*.yml"))

PUBLISHABLE = ("bf.yml", "bf-surface.yml", "factice.yml")


def test_scripts_exist_and_are_executable():
    for path in (SIGN_RPM, GEN_KEY, INSTALL_RPM):
        assert path.is_file(), f"{path.name} manquant"
        assert path.stat().st_mode & 0o111, f"{path.name} non executable"


def test_publish_requires_a_signing_key():
    body = PUBLISH.read_text()
    assert "BLUEFOX_RPM_GPG_NAME" in body
    assert re.search(
        r'\[ -n "\$\{BLUEFOX_RPM_GPG_NAME:-\}" \] \|\| die', body
    ), "la chaine de publication doit refuser un RPM non signe"


def test_private_key_never_enters_the_build_container():
    body = BUILD_RPM.read_text()
    # `exec podman run` rendrait impossible toute signature apres le container :
    # le script serait remplace par lui.
    assert "exec podman run" not in body, (
        "le script doit reprendre la main apres le container pour signer sur l'hote"
    )
    assert "podman run --rm" in body
    # Rien qui ressemble a un passage de cle dans le container.
    container_block = body[body.index("podman run --rm") : body.index("sign_or_warn\n    exit 0")]
    assert "GPG" not in container_block, (
        "aucune variable GPG ne doit etre passee au container : "
        f"bloc suspect -> {container_block[:200]}"
    )


def test_unsigned_build_is_loud_but_allowed():
    body = BUILD_RPM.read_text()
    assert "RPM NON SIGNE" in body, "un build sans cle doit le dire fort"
    assert "sign_or_warn" in body


def test_recipes_verify_before_installing():
    for recipe in RECIPES:
        if recipe.name not in PUBLISHABLE:
            continue
        text = recipe.read_text()
        assert "install-welcome-rpm.sh" in text, f"{recipe.name} n'appelle pas le verificateur"
        # Plus aucune installation directe et non verifiee.
        assert not re.search(
            r"^\s*-\s*rpm-ostree install /usr/share/bluefox/rpm-staging", text, re.MULTILINE
        ), f"{recipe.name} installe encore le RPM sans verifier sa signature"


def test_installer_checks_signature_two_ways():
    body = INSTALL_RPM.read_text()
    # Un paquet non signe ne fait PAS echouer `rpm -K` : il n'affiche aucune
    # ligne Signature. Les deux controles sont donc necessaires.
    assert "%{SIGPGP:pgpsig}" in body, "presence de signature dans l'entete non verifiee"
    assert "rpm -Kv" in body, "validite de la signature non verifiee"
    assert "Signature, key ID" in body
    assert "rpm --import" in body


def test_sign_script_refuses_without_arguments():
    proc = subprocess.run(
        ["bash", str(SIGN_RPM)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode != 0
    assert "aucun RPM" in proc.stderr


def test_sign_script_refuses_without_a_key_name():
    with tempfile.NamedTemporaryFile(suffix=".rpm") as tmp:
        proc = subprocess.run(
            ["bash", str(SIGN_RPM), tmp.name],
            capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin"},
        )
    assert proc.returncode != 0
    assert "BLUEFOX_RPM_GPG_NAME" in proc.stderr
