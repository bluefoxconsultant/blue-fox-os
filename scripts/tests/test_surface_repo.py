"""Garde-fous sur le depot linux-surface durci (#23812, audit P5.2).

Ce que ces tests protegent, et pourquoi ils existent plutot qu'une relecture :
les trois faiblesses corrigees vivent dans un fichier amont qu'on ne controle
pas, et la correction tient a un ORDRE de modules dans une recipe. Les deux se
defont silencieusement — un `repos:` remis a l'URL upstream reconstruit une
image qui a l'air bonne, et un module deplace fait tomber le .repo apres le
depsolve. Rien n'echoue au build dans ces deux cas.

Stdlib seulement : ces tests tournent dans la lane `test-wizard` de la CI, qui
n'installe rien de plus que pytest.
"""

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_SCRIPT = REPO_ROOT / "files" / "scripts" / "setup-surface-repo.sh"
KEY_FILE = REPO_ROOT / "files" / "scripts" / "keys" / "linux-surface.asc"
RECIPE = REPO_ROOT / "recipes" / "bf-surface.yml"

# Empreinte recoupee le 2026-07-24 sur le depot upstream + keys.openpgp.org +
# keyserver.ubuntu.com. Dupliquee ici a dessein : le test doit echouer si
# quelqu'un change la constante du script sans refaire ce recoupement.
EXPECTED_FPR = "87DEFA4AB94A99A4C8C3112556C464BAAC421453"
UPSTREAM_REPO_URL = "https://pkg.surfacelinux.com/fedora/linux-surface.repo"
MUTABLE_KEY_URL = "raw.githubusercontent.com/linux-surface/linux-surface/master"


def _shell_var(name: str) -> str:
    """Valeur d'une affectation `NAME="..."` en tete du script."""
    m = re.search(rf'^{name}="([^"]+)"', SETUP_SCRIPT.read_text(), re.MULTILINE)
    assert m, f"{name} introuvable dans {SETUP_SCRIPT.name}"
    return m.group(1)


def _module_types() -> list[str]:
    """Types de modules de la recipe, dans l'ordre de declaration."""
    return re.findall(r"^  - type: (\S+)", RECIPE.read_text(), re.MULTILINE)


def test_key_is_present():
    assert KEY_FILE.is_file(), "la cle linux-surface doit etre epinglee en depot"
    assert KEY_FILE.read_text().startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")


def test_key_is_tracked_by_git():
    """Presente sur le disque ne veut pas dire commitee.

    .gitignore porte `*.asc` (cles privees) : sans la negation explicite, un
    `git add files/scripts` SAUTE la cle en silence. Le build passe alors en
    local et casse au premier clone frais — ce test-ci est le seul qui le voit,
    puisqu'en CI le fichier n'existe que s'il est suivi.
    """
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("hors arbre git")
    rc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--error-unmatch", str(KEY_FILE)],
        capture_output=True,
    ).returncode
    assert rc == 0, (
        "la cle existe sur le disque mais n'est pas suivie par git — verifier la "
        "negation `!files/scripts/keys/linux-surface.asc` dans .gitignore"
    )


def test_pinned_digest_matches_committed_key():
    digest = hashlib.sha256(KEY_FILE.read_bytes()).hexdigest()
    assert digest == _shell_var("KEY_SHA256"), (
        "KEY_SHA256 ne correspond plus au fichier commite. Si la cle amont a "
        "tourne, recouper l'empreinte sur deux sources independantes avant de "
        "mettre a jour la constante."
    )


def test_pinned_fingerprint_is_the_verified_one():
    assert _shell_var("KEY_FPR") == EXPECTED_FPR


def test_legacy_key_id_is_derived_not_hardcoded():
    """L'ancien format d'identifiant (8 hex, rpm 4/5) doit etre DERIVE de
    l'empreinte, pas ecrit en dur : une constante figee se desynchronise de
    KEY_FPR sans que rien ne le signale."""
    assert 'KEY_ID_LEGACY="${KEY_FPR_LOWER: -8}"' in _setup_code()


def _written_repo_file() -> str:
    """Contenu du .repo que le script ecrit (corps du heredoc), sans ses
    commentaires d'entete — qui citent volontairement les valeurs amont qu'on
    refuse, et fausseraient un simple `in`."""
    m = re.search(r"<<'EOF'\n(.*?)\nEOF\n", SETUP_SCRIPT.read_text(), re.DOTALL)
    assert m, "heredoc du .repo introuvable dans le script"
    return "\n".join(
        line for line in m.group(1).splitlines() if not line.startswith("#")
    )


def test_repo_file_written_is_hardened():
    repo = _written_repo_file()
    assert "skip_if_unavailable=0" in repo, "depot injoignable doit casser le build"
    assert "skip_if_unavailable=1" not in repo
    assert "gpgcheck=1" in repo
    assert "gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-linux-surface" in repo
    assert MUTABLE_KEY_URL not in repo, "la cle ne doit plus venir d'une branche mutable"
    # $releasever doit rester litteral : c'est dnf qui le resout, au build comme
    # au runtime. Un heredoc non quote l'aurait deja substitue (a vide).
    assert "f$releasever/" in repo


def test_recipe_does_not_re_add_the_upstream_repo():
    text = RECIPE.read_text()
    # L'URL peut rester citee en commentaire (elle documente le pourquoi) ; ce
    # qui est interdit, c'est une entree de liste `- <url>` sous `repos:`.
    assert not re.search(rf"^\s*-\s*{re.escape(UPSTREAM_REPO_URL)}\s*$", text, re.MULTILINE)
    assert MUTABLE_KEY_URL not in text


def test_script_module_runs_before_rpm_ostree():
    types = _module_types()
    assert "script" in types and "rpm-ostree" in types
    assert types.index("script") < types.index("rpm-ostree"), (
        "le .repo doit etre en place AVANT le depsolve ; l'ordre des modules "
        "d'une recipe BlueBuild est l'ordre d'execution."
    )


def test_setup_script_is_referenced_by_the_recipe():
    assert "setup-surface-repo.sh" in RECIPE.read_text()


def _setup_code() -> str:
    """Le script sans ses commentaires — ils citent volontairement les motifs
    fautifs pour expliquer les pieges."""
    return "\n".join(
        line for line in SETUP_SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_gpg_runs_with_a_throwaway_gnupghome():
    """⚠️ Dans un container de build bootc, /root est un lien vers var/roothome
    et /var est VIDE : gpg meurt sur « can't create directory '/root/.gnupg' »
    avec un code 2. Meme famille que le piege dracut `--tmpdir /var/tmp`.
    Vecu le 2026-07-25 sur le premier build reel."""
    assert "GNUPGHOME=" in _setup_code(), (
        "sans GNUPGHOME jetable, la verification d'empreinte meurt dans l'image de build"
    )


def test_gpg_stderr_is_never_swallowed():
    """⚠️ `gpg … 2>/dev/null` + `set -euo pipefail` = mort SILENCIEUSE du script :
    trace vide, code 2, aucune piste. C'est ce qui a rendu la panne du
    2026-07-25 illisible pendant plusieurs essais."""
    for line in _setup_code().splitlines():
        if "gpg --show-keys" in line:
            assert "2>/dev/null" not in line, (
                "l'erreur de gpg doit etre capturee et affichee, pas jetee"
            )


def test_rpm_key_assertion_accepts_the_rpm6_naming():
    """⚠️ rpm 6 nomme le gpg-pubkey d'apres l'empreinte COMPLETE en minuscules ;
    rpm 4/5 d'apres ses 8 derniers hex. Une assertion `grep -qx <8 hex>` echoue
    donc sur un import parfaitement reussi — panne exacte du premier build."""
    code = _setup_code()
    assert "KEY_FPR_LOWER" in code
    assert 'grep -qx "$KEY_ID_SHORT"' not in code, "assertion figee sur le format rpm 4/5"
    m = re.search(r"grep -qiE \"\^\((.+?)\)", code)
    assert m and "KEY_FPR_LOWER" in m.group(1), (
        "l'assertion doit accepter l'empreinte complete (rpm 6)"
    )
