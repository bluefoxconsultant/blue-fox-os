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


def _strip_comments(body: str) -> str:
    """Les commentaires de ces scripts CITENT les formes fautives pour dire
    pourquoi on ne les emploie pas. Une recherche naive les prend pour du code
    et echoue sur la documentation elle-meme."""
    return "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("#")
    )


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


def test_the_sbom_is_package_level_not_file_level():
    """Arbitrage tranche avec Olivier le 2026-08-08.

    Le defaut de syft catalogue 157 018 fichiers pour les MEMES 13 239 paquets :
    136 Mo au lieu de 18. Meme reponse aux CVE, facteur sept sur l'artefact que
    le registre sert a chaque verification.

    ⚠️ Ce docstring a d'abord justifie la mesure par un refus de Rekor. C'ETAIT
    FAUX : ce que cosign depose dans Rekor est un hashedrekord de 652 octets,
    quelle que soit la taille du document (verifie sur le bundle de bf le
    2026-08-08). La taille est une question de COUT, pas d'acceptation.
    """
    body = _gensbom()
    assert 'SYFT_FILE_METADATA_SELECTION="${SYFT_FILE_METADATA_SELECTION:-none}"' in body, (
        "le SBOM doit rester au niveau paquet par defaut"
    )
    assert ('SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP='
            '"${SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP:-false}"') in body, (
        "les DEUX reglages sont necessaires : selection=none seul laisse "
        "154 978 fichiers et 112 Mo. C'est package-file-ownership qui decide "
        "si syft LISTE les fichiers, selection s'il calcule leurs empreintes"
    )
    for probe in ("file.metadata.selection", "package-file-ownership"):
        assert f"log \"   {probe}" in body, (
            f"{probe} doit etre journalise : une surcharge silencieuse "
            "renvoie le document a 112 Mo sans que rien ne le dise"
        )


def test_an_oversized_predicate_fails_before_the_attestation():
    """Le plafond garde le COUT de l'artefact pousse au registre, pas une
    quelconque limite de Rekor — voir generate-sbom.sh. Il attrape surtout une
    surcharge d'environnement qui renverrait le document a 136 Mo en silence."""
    body = _gensbom()
    assert "SBOM_MAX_MB" in body, "aucun garde-fou de taille avant l'attestation"
    tail = body[body.index("SBOM_MAX_MB"):]
    assert "SYFT_FILE_METADATA_SELECTION" in tail, (
        "le message d'echec doit pointer le reglage en cause, pas seulement la "
        "taille"
    )


def test_the_stale_scope_comment_is_gone():
    """Le commentaire a decrit `--scope squashed` jusqu'au 2026-08-08 alors que
    la source n'etait plus une image mais un repertoire depuis 04eb45e. Un
    commentaire qui survit a son code envoie chercher au mauvais endroit — ici
    il a masque six jours durant le reglage qui comptait."""
    body = _publish()
    scope_claims = [
        line for line in body.splitlines()
        if "--scope squashed" in line and not line.lstrip().startswith("#")
    ]
    assert not scope_claims, "--scope n'est pas passe au scan : le code ment"
    assert "un repertoire n'a pas de\n# couches" in body or "n'a pas de couches" in body, (
        "expliquer pourquoi --scope ne veut plus rien dire ici"
    )


def test_attestation_targets_the_index_digest_not_the_tag():
    """Trois cibles possibles, une seule juste.

    - le TAG : bluebuild reecrit :latest a chaque push, donc il peut bouger
      entre la resolution et l'attestation (cosign l'avertit lui-meme) ;
    - l'ENFANT amd64 : c'est ce que le SBOM scanne, mais une attestation posee
      la devient introuvable pour `cosign verify-attestation <tag>` ;
    - l'INDEX : ce que `:latest` resout, ce que l'etape 7 et verify-image.sh
      verifient. C'est celui-la.

    Mesure sur bf : index dd79faa9..., enfant amd64 5ece13b1... Deux objets.
    """
    attest = _strip_comments(
        _publish().split("--- 6. attestation")[1].split("--- 7.")[0]
    )
    assert '"${IMAGE_REPO}@${IMAGE_INDEX_DIGEST}"' in attest, (
        "l'attestation doit viser l'index publie"
    )
    assert '\n    "$IMAGE"' not in attest, (
        "attester le tag : cosign avertit que ca peut signer une autre image"
    )
    assert "SBOM_MANIFEST_DIGEST" not in attest, (
        "attester l'enfant amd64 rendrait l'attestation introuvable depuis le tag"
    )


def test_both_digests_come_from_a_single_registry_read():
    """Relire le registre deux fois ouvre une fenetre pendant laquelle le tag
    peut bouger : on attesterait alors un index que le SBOM ne decrit pas."""
    body = _publish()
    block = body.split("resolution des digests publies")[1].split("--- 6.")[0]
    assert block.count("skopeo inspect") == 1, (
        "une seule lecture du registre doit servir les deux digests"
    )
    assert 'IMAGE_INDEX_DIGEST="sha256:$(sha256sum' in block, (
        "le digest d'un manifeste est le sha256 de ses octets canoniques : le "
        "calculer localement evite un second appel"
    )


def test_skopeo_is_a_hard_requirement_of_a_real_publish():
    body = _publish()
    m = re.search(r'^\[ "\$DRY_RUN" = "0" \] && REQUIRED_TOOLS\+=\((.+)\)$', body, re.MULTILINE)
    assert m, "liste des outils exiges hors DRY_RUN introuvable"
    assert "skopeo" in m.group(1), (
        "l'etape 5 depend de skopeo : son absence doit echouer au preflight, "
        "pas apres 9 Go de build et un push"
    )
