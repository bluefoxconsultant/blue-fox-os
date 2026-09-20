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
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBKEY = REPO_ROOT / "files" / "usr" / "share" / "bluefox" / "keys" / "RPM-GPG-KEY-blue-fox-os"
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


def test_public_key_is_committed_and_tracked():
    """La cle PUBLIQUE de signature doit etre en depot et suivie par git.

    Empreinte au 2026-07-25 : 87AE3740C30E30D4DD7536F88DFE12C567353716
    (uid « Blue Fox OS Package Signing <info@bluefoxconsultant.com> », creee sur
    la machine de build). Les recipes la deposent dans l'image et verifient le
    RPM avec elle : absente, tout build echoue.

    Le nom de fichier n'a pas d'extension .asc a dessein — c'est la convention
    Fedora (/etc/pki/rpm-gpg/RPM-GPG-KEY-*), et ca evite la regle `*.asc` du
    .gitignore qui avait deja avale une cle publique en silence.
    """
    assert PUBKEY.is_file(), f"{PUBKEY} absent — lancer `make rpm-signing-key`"
    assert PUBKEY.read_text().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("hors arbre git")
    rc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", str(PUBKEY)],
        capture_output=True,
    ).returncode
    assert rc == 0, "cle publique presente sur le disque mais non suivie par git"


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
    assert "%{OPENPGP}" in body, "presence de signature dans l'entete non verifiee"
    assert "rpm -Kv" in body, "validite de la signature non verifiee"
    assert "rpm --import" in body


def test_no_script_relies_on_the_legacy_rpm_signature_tags():
    """⚠️ RPM 6 laisse `%{SIGPGP}` / `%{SIGGPG}` VIDES sur un paquet signe.

    Verifie le 2026-07-25 sur rpm 6.0.1, cote Arch comme cote fedora:43 : la
    signature vit dans `%{OPENPGP}`, et `rpm -qpi` affiche un champ
    « Signature : » vide. Un controle qui interroge l'ancien tag conclut donc
    « paquet non signe » sur un paquet parfaitement signe — c'est ce qui a
    fait echouer la premiere validation de bout en bout.
    """
    for path in (SIGN_RPM, INSTALL_RPM):
        body = path.read_text()
        code = "\n".join(
            l for l in body.splitlines() if not l.lstrip().startswith("#")
        )
        for legacy in ("%{SIGPGP", "%{SIGGPG"):
            assert legacy not in code, (
                f"{path.name} interroge {legacy}…}}, vide sous RPM 6 — utiliser %{{OPENPGP}}"
            )


def test_verification_pattern_survives_both_rpm6_wordings():
    """rpm 6 dit « key fingerprint: <40 hex>: OK » si la cle est importee, et
    « key ID <16 hex>: NOKEY » sinon. Un motif qui exige le libelle « Signature,
    key ID » (majuscule, ancien format) ne matche NI l'un NI l'autre."""
    for path in (SIGN_RPM, INSTALL_RPM):
        body = path.read_text()
        assert "Signature, key ID" not in body, (
            f"{path.name} attend l'ancien libelle de rpm 4/5"
        )
        assert re.search(r'grep -Eqi "signature', body), (
            f"{path.name} doit matcher « signature » sans presumer de la casse "
            "ni du libelle (fingerprint vs ID)"
        )


def test_sign_script_uses_a_throwaway_rpmdb():
    """Sur Arch, `rpm` n'est pas le gestionnaire de paquets : /var/lib/rpm
    n'existe pas et TOUTE commande rpm sort en « can't create transaction lock ».
    Sans base jetable, la verification lit une chaine vide et declare le paquet
    non signe."""
    body = SIGN_RPM.read_text()
    assert "--dbpath" in body and "--initdb" in body
    # Sur le CODE seulement : les commentaires citent volontairement le motif
    # fautif pour expliquer le piege.
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    assert "2>/dev/null || true" not in code, (
        "avaler stderr sur une commande rpm cache exactement cette panne"
    )


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
