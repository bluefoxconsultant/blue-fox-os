#!/usr/bin/env bash
# scripts/generate-sbom.sh — produit le SBOM SPDX d'une image PUBLIEE, en
# scannant son rootfs monte plutot que l'image elle-meme.
#
# ⚠️ DOIT tourner sous `podman unshare` : le montage d'image rootless exige
# d'etre dans le namespace utilisateur de podman.
#
#   podman unshare ./scripts/generate-sbom.sh <repo> <digest> <sortie.spdx.json>
#
# Pourquoi ce detour (audit P5.2 B1, #21873 / #22241)
# --------------------------------------------------
# `syft scan` sur une image (registry: ou oci-archive:) passe par stereoscope,
# qui decompresse chaque couche, en cache le contenu sur disque et construit un
# arbre de fichiers PAR COUCHE avant d'aplatir. Sur nos ~10 Go, mesure le
# 2026-07-26 sur charizard :
#
#   source            pic RSS    duree        cache TMPDIR   resultat
#   oci-archive:      18,7 Go    >38 min      15 Go          tue (swap epuise)
#   dir: (ce script)   3,4 Go    4 min 21 s    0             13 240 paquets
#
# GOMEMLIMIT ne pouvait pas sauver le premier cas : c'est une limite SOUPLE,
# elle augmente la pression du ramasse-miettes, elle ne plafonne pas un tas qui
# a reellement besoin de la place. Elle a ete depassee de 2,3x.
#
# Scanner le rootfs monte supprime la cause au lieu de la contenir : pas de
# decompression, pas de cache de couches, un seul arbre.
#
# ⚠️ Ce que ce detour ne doit PAS couter : la garantie que le SBOM decrit ce qui
# est PUBLIE. D'ou le tirage PAR DIGEST depuis le registre (jamais l'artefact
# local, dont le digest differe — bluebuild transforme l'image au push) et
# --source-name/--source-version, sans lesquels le document SPDX s'identifierait
# par le chemin du point de montage overlay. Un SBOM atteste dont le sujet est
# un chemin temporaire local ne prouve pas grand-chose.

set -euo pipefail

IMAGE_REPO="${1:?usage: generate-sbom.sh <repo> <digest> <sortie>}"
DIGEST="${2:?digest manquant (sha256:...)}"
OUT="${3:?chemin de sortie manquant}"

export TMPDIR="${SYFT_TMPDIR:-/var/tmp}"

log() { echo "[sbom] $*"; }
die() { echo "[sbom] ERREUR: $*" >&2; exit 1; }

command -v skopeo >/dev/null 2>&1 || die "skopeo introuvable"
command -v syft   >/dev/null 2>&1 || die "syft introuvable"
command -v podman >/dev/null 2>&1 || die "podman introuvable"

case "$DIGEST" in
    sha256:*) ;;
    *) die "digest attendu sous la forme sha256:... (recu « ${DIGEST} »)" ;;
esac

# Tag local unique et derive du digest : deux publications concurrentes ne se
# marchent pas dessus, et le nom dit de quoi il s'agit.
LOCAL_TAG="localhost/bfos-sbom:${DIGEST#sha256:}"

# Horodatage de depart : le menage des caches stereoscope ne touchera QUE ce que
# ce script a produit. Balayer tous les stereoscope-* casserait un scan
# concurrent, et ce script n'a pas a decider pour les autres.
STARTED_MARKER="$(mktemp "${TMPDIR}/.sbom-start.XXXXXX")"

MOUNTED=0
cleanup() {
    local rc=$?
    [ "$MOUNTED" = "1" ] && podman image umount "$LOCAL_TAG" >/dev/null 2>&1
    podman rmi "$LOCAL_TAG" >/dev/null 2>&1
    # ⚠️ Un syft interrompu laisse son cache de couches : ~15 Go par tentative
    # ratee, constate le 2026-07-26. Sans ce balayage, quelques echecs
    # remplissent le disque sans que rien ne le dise.
    find "$TMPDIR" -maxdepth 1 -name 'stereoscope-*' -newer "$STARTED_MARKER" \
        -exec rm -rf {} + 2>/dev/null || true
    rm -f "$STARTED_MARKER"
    return $rc
}
# INT/TERM en plus d'EXIT : sur un signal non piege, bash meurt SANS jouer le
# trap EXIT — c'est ce qui a laisse 15 Go derriere le 2026-07-26.
trap cleanup EXIT INT TERM

log "tirage par digest ${DIGEST}"
skopeo copy --retry-times 5 \
    "docker://${IMAGE_REPO}@${DIGEST}" \
    "containers-storage:${LOCAL_TAG}"

log "montage du rootfs"
MNT="$(podman image mount "$LOCAL_TAG")"
[ -n "$MNT" ] && [ -d "$MNT" ] \
    || die "montage echoue — ce script tourne-t-il bien sous « podman unshare » ?"
MOUNTED=1
log "   ${MNT}"

# Controle de sanite : sans base rpm, le catalogue serait quasi vide et on
# attesterait un inventaire creux sans une erreur.
[ -d "${MNT}/usr/lib/sysimage/rpm" ] || [ -d "${MNT}/var/lib/rpm" ] \
    || die "aucune base RPM dans le rootfs monte : le SBOM serait vide de paquets systeme."

log "scan syft du rootfs"
# ⚠️ INVENTAIRE DE PAQUETS, PAS DE FICHIERS (arbitrage tranche le 2026-08-08).
#
# Le defaut de syft est file.metadata.selection = "owned-by-package" : il
# catalogue chaque fichier possede par un paquet, avec ses empreintes. Mesure
# sur nos images :
#
#     selection            paquets   fichiers   poids du document
#     owned-by-package      8 514    157 018      121 Mo
#     none                  8 514          0       12,2 Mo
#
# Meme inventaire de paquets, facteur dix. Et 121 Mo ne passent PAS : Rekor a
# refuse le predicat par un 502 le 2026-08-03 et deux fois le 2026-08-08 (136 Mo
# pour bf-surface). L'attestation de bf, qui verifie, pesait 12,2 Mo.
#
# Le niveau fichier n'apporterait rien ici que l'image ne donne deja : elle est
# UN digest signe, donc chaque fichier y est deja verifie de bout en bout.
# L'inventaire de fichiers gagne sa place sur un systeme mutable, pas sur du
# rpm-ostree. Le seul contenu hors RPM (les 56 fichiers du module `files:`) est
# rattache au commit par org.opencontainers.image.revision, que le controle 4/5
# verifie.
#
# Surchargeable pour un scan medico-legal ponctuel, mais JAMAIS en silence :
# la valeur effective est journalisee, parce qu'un retour a "owned-by-package"
# fait echouer l'attestation 40 minutes plus tard, sur un 502 qui ne dit pas
# pourquoi.
export SYFT_FILE_METADATA_SELECTION="${SYFT_FILE_METADATA_SELECTION:-none}"
log "   file.metadata.selection = ${SYFT_FILE_METADATA_SELECTION}"

# --source-name / --source-version : le document doit s'identifier par la
# reference PUBLIEE, pas par le chemin overlay du montage.
syft scan "dir:${MNT}" \
    --source-name "$IMAGE_REPO" \
    --source-version "$DIGEST" \
    -o "spdx-json=${OUT}"

[ -s "$OUT" ] || die "SBOM vide apres le scan."
PKGS="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1])).get("packages",[])))' "$OUT")"
[ "$PKGS" -gt 0 ] || die "SBOM sans aucun paquet : document valide mais creux."

# ⚠️ Garde-fou de taille. Un predicat trop gros ne produit PAS une erreur
# lisible : cosign attest rend « status 502: Bad Gateway », qui ressemble a une
# panne de Rekor et a coute cinq jours de diagnostic. Mieux vaut echouer ici,
# avant le push de l'attestation, avec la vraie raison.
# Le seuil n'est pas une specification : 12,2 Mo passent, 136 Mo non. 64 Mo est
# posé entre les deux, franchement au-dessus de ce qui marche.
SBOM_MAX_MB="${SBOM_MAX_MB:-64}"
SBOM_MB="$(( $(stat -c %s "$OUT") / 1048576 ))"
[ "$SBOM_MB" -le "$SBOM_MAX_MB" ] || die \
    "SBOM de ${SBOM_MB} Mo : au-dela de ${SBOM_MAX_MB} Mo, Rekor refuse le predicat
et cosign attest rend un 502 qui n'en dit pas la cause. Verifier
SYFT_FILE_METADATA_SELECTION (attendu « none », vu « ${SYFT_FILE_METADATA_SELECTION} »)."

log "OK — $(du -h "$OUT" | cut -f1), ${PKGS} paquets, sujet ${IMAGE_REPO}@${DIGEST}"
