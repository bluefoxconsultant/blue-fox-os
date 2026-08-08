"""Garde-fous sur le controle 3/5 : COHERENCE des attestations (#21873).

Pourquoi ce fichier existe
--------------------------
Le 2026-08-08, l'image bf publiee portait DEUX documents SPDX, tous deux signes
de notre cle et rattaches au meme digest :

    ghcr.io/bluefoxconsultant/blue-fox-os-bf   8514 paquets
    /etc                                          1 paquet

Le second etait un `syft scan` de /etc atteste contre l'image. Deux mecanismes
l'ont laisse passer sans un mot :

  1. `cosign verify-attestation` reussit des qu'UNE SEULE attestation verifie.
     Le controle 2/5 etait donc vert.
  2. Le controle 3/5 prenait le MAX du nombre de paquets sur les enveloppes. Il
     annoncait « 8514 paquets ✅ » — en masquant tres exactement le document
     creux qu'il avait pour mission d'attraper.

C'est la meme famille que le job CI conditionne a un evenement qui ne se
produisait jamais (2026-07-22) et que `make verify` qui ne regardait qu'un
maillon sur trois (2026-07-27) : un controle qui ne touche pas la chose qu'il
pretend valider repond toujours « tout va bien ».

Ce fichier garde donc le comportement, pas seulement le texte : le coeur du
controle est extrait du script et EXECUTE sur des enveloppes fabriquees.

Stdlib seulement (lane `test-wizard`), comme test_sbom_source.py.
"""

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY = REPO_ROOT / "scripts" / "verify-image.sh"

EXPECTED = "ghcr.io/bluefoxconsultant/blue-fox-os-bf"


def _verify() -> str:
    return VERIFY.read_text()


def _verify_code() -> str:
    """Le script sans ses commentaires.

    Les commentaires de ce script CITENT les formes fautives pour expliquer
    pourquoi on ne les emploie pas ; une recherche naive les prendrait pour du
    code et echouerait sur la documentation elle-meme.
    """
    return "\n".join(
        line for line in VERIFY.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def _embedded_python() -> str:
    """Extrait le python du controle 3/5 tel qu'il est reellement embarque.

    On teste le code du script, pas une copie qui pourrait diverger de lui.
    """
    body = _verify()
    m = re.search(
        r"BF_EXPECTED_NAME=\"\$\{REGISTRY\}/blue-fox-os-\$\{slug\}\" python3 -c '\n(.*?)\n' 2>/dev/null\)\"",
        body,
        re.DOTALL,
    )
    assert m, "bloc python du controle 3/5 introuvable — la structure a change"
    return m.group(1)


def _envelope(name: str, n_packages: int) -> str:
    """Une enveloppe DSSE telle que `cosign verify-attestation` en rend."""
    payload = {
        "subject": [{"name": EXPECTED, "digest": {"sha256": "0" * 64}}],
        "predicateType": "https://spdx.dev/Document",
        "predicate": {
            "name": name,
            "packages": [{"name": f"p{i}"} for i in range(n_packages)],
        },
    }
    raw = json.dumps(payload).encode()
    return json.dumps({"payload": base64.b64encode(raw).decode()})


def _run(stdin: str):
    return subprocess.run(
        [sys.executable, "-c", _embedded_python()],
        input=stdin,
        capture_output=True,
        text=True,
        env={"BF_EXPECTED_NAME": EXPECTED, "PATH": "/usr/bin:/bin"},
    )


# --- comportement ---------------------------------------------------------


def test_a_single_coherent_attestation_passes():
    r = _run(_envelope(EXPECTED, 8514) + "\n")
    assert r.returncode == 0, r.stderr
    assert "OK\t" in r.stdout
    assert "8514" in r.stdout


def test_the_etc_attestation_is_caught():
    """Le cas reel du 2026-08-08 : un bon document ET un scan de /etc."""
    r = _run(_envelope(EXPECTED, 8514) + "\n" + _envelope("/etc", 1) + "\n")
    assert r.returncode == 3, (
        "deux attestations dont une qui ne decrit pas l'image doivent faire "
        f"echouer le controle (code 3), obtenu {r.returncode}"
    )
    assert "BAD\t/etc" in r.stdout
    assert "ne decrit pas cette image" in r.stdout
    # le bon document reste liste : on veut le tableau complet, pas le premier echec
    assert "OK\t" + EXPECTED in r.stdout


def test_order_does_not_matter():
    """Prendre la premiere enveloppe serait aussi faux que prendre le max."""
    r = _run(_envelope("/etc", 1) + "\n" + _envelope(EXPECTED, 8514) + "\n")
    assert r.returncode == 3, "l'incoherence ne doit pas dependre de l'ordre"


def test_an_empty_sbom_fails_even_with_the_right_name():
    """Un SPDX a zero paquet est valide — et ne decrit rien."""
    r = _run(_envelope(EXPECTED, 0) + "\n")
    assert r.returncode == 3
    assert "aucun paquet" in r.stdout


def test_no_readable_document_is_distinct_from_incoherent():
    """« illisible » et « incoherent » ne demandent pas le meme geste : l'un se
    corrige dans le parseur, l'autre exige une republication de 9 Go."""
    r = _run("")
    assert r.returncode == 2, "aucun document lisible doit rendre 2, pas 3"


def test_garbage_lines_are_skipped_not_fatal():
    r = _run("pas du json\n" + _envelope(EXPECTED, 12) + "\n")
    assert r.returncode == 0, r.stderr


# --- structure ------------------------------------------------------------


def test_the_max_heuristic_is_gone():
    """C'est le max() qui a rendu le controle aveugle pendant six jours."""
    block = _embedded_python()
    assert "max(" not in block, (
        "prendre le max du nombre de paquets laisse un document creux se cacher "
        "derriere un bon — c'est le defaut du 2026-08-08"
    )


def test_every_envelope_is_examined():
    block = _embedded_python()
    assert "docs.append" in block, "les enveloppes doivent toutes etre collectees"
    assert "for name, n in docs:" in block, (
        "chaque document doit etre juge, pas seulement le meilleur"
    )


def test_the_expected_name_comes_from_the_image_reference():
    """Comparer a une constante en dur casserait sur bf-surface et factice."""
    body = _verify()
    assert 'BF_EXPECTED_NAME="${REGISTRY}/blue-fox-os-${slug}"' in body, (
        "le nom attendu doit se deriver du tenant, comme --source-name dans "
        "generate-sbom.sh"
    )


def test_exit_status_is_not_swallowed_by_local():
    """`local x="$(cmd)"` ecrase $? par le code de retour de `local`.

    Le controle deviendrait muet : toute attestation incoherente passerait
    pour coherente. Le piege est assez discret pour meriter un test.
    """
    code = _verify_code()
    assert re.search(r"^\s*local report rc$", code, re.MULTILINE), (
        "declarer report et rc AVANT l'affectation, sinon $? est perdu"
    )
    assert not re.search(r"local\s+report=", code), (
        "`local report=\"$(...)\"` masque le code de retour de la substitution"
    )


def test_the_three_outcomes_are_handled_distinctly():
    body = _verify()
    for probe in ('"$rc" -eq 0', '"$rc" -eq 3'):
        assert probe in body, f"issue non traitee : {probe}"
    assert "attestations INCOHERENTES" in body
    assert "predicat SPDX illisible" in body
