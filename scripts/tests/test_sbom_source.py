"""Garde-fous sur CE QUE le SBOM decrit, et sur CE QU'IL COUTE (#21873, B1).

B1 a tenu deux mois : les images publiees etaient signees mais jamais attestees.
L'etape 5 a echoue QUATRE fois, de quatre facons differentes, et les trois
premieres ont fait croire a un probleme de ressources. Mesures du 2026-07-26 :

    source                          pic RSS   duree      cache TMPDIR
    registry: (sans reglage)        13,1 Go   OOM        —
    registry: + GOMEMLIMIT           —        /tmp plein 9,5 Go dans un tmpfs
    registry: + GOMEMLIMIT + TMPDIR  —        ~35 min    reseau (PROTOCOL_ERROR)
    oci-archive:                    18,7 Go   >38 min    15 Go
    dir: (rootfs monte)              3,4 Go   4 min 21 s  0

GOMEMLIMIT ne pouvait rien : c'est une limite SOUPLE. Elle a ete depassee de
2,3x. La cause commune des quatre echecs est stereoscope, la couche « image »
de syft, qui decompresse et met en cache chaque couche. Scanner le rootfs monte
la court-circuite.

Ce fichier garde les trois proprietes que ce detour ne doit pas coster :

  1. le SBOM decrit l'image PUBLIEE, tiree par DIGEST (ni l'artefact local,
     dont le digest differe, ni un tag, qu'un autre push peut bouger) ;
  2. le document SPDX s'identifie par cette reference publiee, pas par le
     chemin overlay de son point de montage ;
  3. rien ne reste derriere : un syft interrompu laisse ~15 Go de cache.

Stdlib seulement (lane `test-wizard`), comme test_provenance.py.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH = REPO_ROOT / "scripts" / "publish-image.sh"
GENSBOM = REPO_ROOT / "scripts" / "generate-sbom.sh"


def _publish() -> str:
    return PUBLISH.read_text()


def _gensbom() -> str:
    return GENSBOM.read_text()


def test_the_sbom_helper_exists_and_is_executable():
    assert GENSBOM.is_file(), "scripts/generate-sbom.sh manquant"
    assert GENSBOM.stat().st_mode & 0o111, "generate-sbom.sh doit etre executable"


def test_sbom_is_scanned_from_a_mounted_rootfs():
    """Les sources « image » de syft passent par stereoscope : 18,7 Go de RSS."""
    body = _gensbom()
    assert 'syft scan "dir:${MNT}"' in body, (
        "le scan doit porter sur le rootfs monte"
    )
    for banned in ('syft scan "registry:', 'syft scan "oci-archive:'):
        assert banned not in body, (
            f"{banned} passe par stereoscope : 18,7 Go de RSS et 15 Go de cache, "
            "mesure le 2026-07-26"
        )


def test_the_image_is_pulled_by_digest_not_by_tag():
    """Un tag ne designe pas un artefact stable."""
    body = _gensbom()
    assert "skopeo copy" in body, "le tirage doit passer par skopeo"
    assert re.search(r'"docker://\$\{IMAGE_REPO\}@\$\{DIGEST\}"', body), (
        "l'image doit etre tiree par digest : sinon le SBOM peut decrire une "
        "autre image que celle qu'on atteste"
    )
    assert re.search(r'case "\$DIGEST" in\s*\n\s*sha256:\*\)', body), (
        "un digest malforme doit etre refuse tot, pas produire une reference "
        "que skopeo interprete de travers"
    )


def test_the_sbom_identifies_the_published_reference():
    """Sans --source-name/--source-version, le document SPDX s'identifie par le
    chemin overlay du montage — un sujet local et temporaire pour un document
    qu'on va attester contre une image publiee."""
    body = _gensbom()
    assert '--source-name "$IMAGE_REPO"' in body
    assert '--source-version "$DIGEST"' in body


def test_digest_resolution_happens_against_the_registry():
    body = _publish()
    assert 'skopeo inspect --raw "docker://${IMAGE}"' in body, (
        "le digest publie se lit sur le registre"
    )
    assert '[ -n "$SBOM_MANIFEST_DIGEST" ]' in body
    assert "digest publie de ${IMAGE} illisible" in body, (
        "un digest illisible doit arreter la chaine"
    )


def test_an_empty_sbom_is_refused_before_attestation():
    """cosign attest avale un SBOM vide sans broncher."""
    for label, body in (("publish", _publish().split("--- 6. attestation")[0]),
                        ("generate-sbom", _gensbom())):
        assert '[ -s "$OUT" ]' in body or '[ -s "$SBOM" ]' in body, (
            f"{label} : SBOM a 0 octet non detecte"
        )
        assert "PKGS" in body or "SBOM_PACKAGES" in body, (
            f"{label} : un SPDX valide mais sans aucun paquet passerait le seul "
            "controle de taille"
        )


def test_rootfs_is_sanity_checked_before_scanning():
    """Un rootfs sans base RPM produirait un inventaire creux, sans erreur."""
    body = _gensbom()
    assert "usr/lib/sysimage/rpm" in body and "var/lib/rpm" in body, (
        "l'absence de base RPM doit etre detectee avant le scan"
    )


def test_layer_cache_is_swept_on_failure_too():
    """~15 Go par tentative ratee (constate le 2026-07-26)."""
    body = _gensbom()
    assert "stereoscope-*" in body, "le cache de couches n'est pas balaye"
    assert re.search(r"trap cleanup EXIT INT TERM", body), (
        "EXIT seul ne suffit pas : sur un signal non piege bash meurt SANS "
        "jouer le trap EXIT, et c'est ce qui a laisse 15 Go derriere"
    )
    assert "-newer" in body, (
        "le balayage doit se limiter a ce que CE script a produit : effacer "
        "tous les stereoscope-* casserait un scan concurrent"
    )


def test_publish_delegates_under_podman_unshare():
    """Le montage rootless l'exige ; le reste du script n'a pas a le subir."""
    body = _publish()
    assert re.search(r"podman unshare \\?\s*\n?\s*\"\$\{WORKDIR\}/scripts/generate-sbom\.sh\"", body), (
        "l'etape 5 doit deleguer a generate-sbom.sh sous podman unshare"
    )


def test_skopeo_is_a_hard_requirement_of_a_real_publish():
    body = _publish()
    m = re.search(r'^\[ "\$DRY_RUN" = "0" \] && REQUIRED_TOOLS\+=\((.+)\)$', body, re.MULTILINE)
    assert m, "liste des outils exiges hors DRY_RUN introuvable"
    assert "skopeo" in m.group(1), (
        "l'etape 5 depend de skopeo : son absence doit echouer au preflight, "
        "pas apres 9 Go de build et un push"
    )
