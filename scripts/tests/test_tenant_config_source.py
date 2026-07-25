"""Le manifeste tenant vient du depot, et un fetch rate ne se rattrape pas.

Decision du 2026-07-24 (#23813, audit P5.2 blocage B3) :
config.bluefoxconsultant.com n'a jamais ete deploye — 404 sur /, /bf.json,
/factice.json — et il etait pourtant la valeur par defaut de TENANT_CONFIG, avec
un repli in-repo silencieux. Les builds passaient, le trou restait invisible, et
le perimetre d'audit decrivait un service inexistant.

Ce qui est verifie ici : la source par defaut est bien en depot, et une URL
explicite qui echoue fait echouer le build au lieu de retomber sur autre chose.
Le second point est teste en executant vraiment le script — c'est le
comportement, pas le texte, qui compte.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "build_branded_iso.sh"
WELCOME_README = REPO_ROOT / "welcome" / "README.md"
WELCOME_SPEC = REPO_ROOT / "welcome" / "welcome.spec"

DEAD_ENDPOINT = "config.bluefoxconsultant.com"


def _run(tenant_config: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "SLUG": "bf",
            "TENANT_CONFIG": tenant_config,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_default_manifest_source_is_in_repo():
    m = re.search(r'^TENANT_CONFIG="\$\{TENANT_CONFIG:-(.+?)\}"', SCRIPT.read_text(), re.MULTILINE)
    assert m, "valeur par defaut de TENANT_CONFIG introuvable"
    assert m.group(1) == "${WORKDIR}/config/${SLUG}.json", (
        f"la source par defaut doit etre le manifeste en depot, pas {m.group(1)}"
    )


def test_script_no_longer_points_at_the_dead_endpoint():
    # Le nom peut rester en commentaire (il documente la decision) ; ce qui est
    # interdit, c'est de le voir dans du code.
    code = "\n".join(
        line for line in SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    assert DEAD_ENDPOINT not in code


def test_failed_url_fetch_is_fatal():
    # .invalid ne resout jamais (RFC 2606) : le fetch echoue sans reseau.
    proc = _run("https://nope.invalid/bf.json")
    assert proc.returncode != 0, "un fetch rate doit casser le build, pas retomber en douce"
    assert "23813" in proc.stderr, "le message doit renvoyer a la decision"
    # Preuve que le repli est bien mort : le manifeste in-repo existe, et le
    # script a quand meme refuse.
    assert (REPO_ROOT / "config" / "bf.json").is_file()


def test_missing_local_manifest_is_fatal():
    proc = _run("/nonexistent/bf.json")
    assert proc.returncode != 0
    assert "manifeste introuvable" in proc.stderr


def test_welcome_docs_no_longer_promise_a_network_fetch():
    for path in (WELCOME_README, WELCOME_SPEC):
        for line in path.read_text().splitlines():
            if DEAD_ENDPOINT in line:
                # Seule mention toleree : celle qui dit que l'endpoint n'existe
                # pas / ne sera pas deploye.
                assert re.search(r"jamais|pas deploy|ne le sera pas|#23813", line), (
                    f"{path.name} annonce encore un fetch depuis {DEAD_ENDPOINT} : {line.strip()}"
                )
