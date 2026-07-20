#!/usr/bin/env bash
# scripts/publish-image.sh — build + push + signature + SBOM + attestation
# d'une image OCI Blue Fox OS, en local.
#
# Remplace les jobs GitHub Actions `build` et `attest-sbom`, retires le
# 2026-07-16 : les images OCI se construisent desormais sur la machine de
# build, au meme endroit que l'assemblage de l'ISO. Le workflow GH ne fait
# plus que de la validation (voir .github/workflows/build.yml).
#
# Usage :
#   COSIGN_PRIVATE_KEY="$(cat /chemin/cosign.key)" ./scripts/publish-image.sh bf
#   SLUG=bf-surface ./scripts/publish-image.sh
#   DRY_RUN=1 ./scripts/publish-image.sh bf      # build local, aucun push
#
# TAG (defaut: latest) ne pilote QUE les etapes 5 a 7 (SBOM, attestation,
# verification). C'est bluebuild qui decide des tags qu'il pousse, a partir de
# la recipe — il publie :latest plus un tag calendaire type 44.20260716. Mettre
# TAG=autre chose sans qu'un tel tag vienne d'etre pousse fait donc attester
# une image plus ancienne, sans erreur visible. En pratique : ne pas y toucher.
#
# Prerequis :
#   - bluebuild, cosign, syft, podman
#     Sur Garuda/Arch : ./scripts/bootstrap-garuda.sh les installe (ni bluebuild
#     ni syft ne sont packages pour Arch — ils passent par leurs installeurs
#     upstream). Sur Fedora, tout est dans dnf.
#   - auth GHCR :
#       gh auth login          # une fois, interactif
#       gh auth token | podman login ghcr.io -u "$(gh api user -q .login)" --password-stdin
#     `gh` doit rester disponible : le preflight en derive BB_USERNAME/BB_PASSWORD
#     pour bluebuild, qui ne sait pas lire la session podman. Contournement si
#     `gh` est absent : exporter BB_USERNAME et BB_PASSWORD soi-meme.
#   - COSIGN_PRIVATE_KEY = contenu de cosign.key (PAS le chemin)
#   - ~20 GB libres : l'image Kinoite fait ~9 GB
#
# Portable hors Fedora : l'etape 1 (RPM) se construit dans un container
# fedora:43 — build-welcome-rpm.sh detecte l'absence des macros RPM Fedora et
# rebondit tout seul dans podman. Rien d'autre dans la chaine n'est Fedora.
#
# Enchainement (miroir de l'ancien pipeline CI) :
#   1. RPM welcome        <- scripts/build-welcome-rpm.sh
#   2. stage du RPM       <- files/usr/share/bluefox/rpm-staging/
#   3. branding + KDE     <- scripts/build_branded_iso.sh (BUILD=0)
#   4. build + push + sig <- bluebuild
#   5. SBOM SPDX          <- syft
#   6. attestation        <- cosign attest
#   7. verification       <- cosign verify + verify-attestation

set -euo pipefail

SLUG="${1:-${SLUG:-bf}}"
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-ghcr.io/bluefoxconsultant}"
IMAGE="${REGISTRY}/blue-fox-os-${SLUG}:${TAG}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN="${DRY_RUN:-0}"

log() { echo "[publish] $*"; }
die() { echo "[publish] ERREUR: $*" >&2; exit 1; }

# --- COSIGN_PASSWORD -------------------------------------------------------
# cosign veut cette variable DEFINIE, meme vide. La cle BlueBuild est un
# ENCRYPTED SIGSTORE PRIVATE KEY (scrypt) quelle que soit la passphrase — la
# convention BlueBuild etant justement de la generer SANS passphrase. Si la
# variable n'existe pas, cosign tente de lire la passphrase au terminal et
# meurt des qu'il n'y a pas de TTY :
#   Enter password for private key: Error: ... reading key:
#   inappropriate ioctl for device
# `${VAR-}` ne se substitue que si VAR est *unset* : une valeur vide fournie
# explicitement est donc preservee. Verifie sur cosign v3.0.6.
export COSIGN_PASSWORD="${COSIGN_PASSWORD-}"

# --- preflight -------------------------------------------------------------
# podman sert au build ET au rebond fedora:43 de l'etape 1. cosign/syft ne
# servent qu'aux etapes 5-7 : inutile de les exiger pour un DRY_RUN.
REQUIRED_TOOLS=(bluebuild podman)
[ "$DRY_RUN" = "0" ] && REQUIRED_TOOLS+=(cosign syft)

for tool in "${REQUIRED_TOOLS[@]}"; do
    command -v "$tool" >/dev/null 2>&1 \
        || die "$tool introuvable. Sur Garuda/Arch : ./scripts/bootstrap-garuda.sh"
done

[ -f "${WORKDIR}/recipes/${SLUG}.yml" ] || die "recipe recipes/${SLUG}.yml introuvable"

if [ "$DRY_RUN" = "0" ]; then
    [ -n "${COSIGN_PRIVATE_KEY:-}" ] || die "COSIGN_PRIVATE_KEY vide. Exporter le CONTENU de cosign.key, pas son chemin."
    podman login --get-login ghcr.io >/dev/null 2>&1 \
        || die "pas authentifie sur ghcr.io. Voir les prerequis en tete de script."

    # --- credentials pour bluebuild --------------------------------------
    # ⚠️ Hors CI, `bluebuild build --push` EXIGE --registry / --username /
    # --password : il ne lit PAS la session podman et ne devine PAS le
    # registre depuis le remote git. Sans eux il retombe sur `localhost` et
    # meurt en boucle sur « pinging container registry localhost: dial tcp
    # [::1]:443: connect: connection refused ». Le cas ne s'etait jamais
    # presente tant que personne n'avait pousse depuis la machine locale.
    # On passe par l'ENVIRONNEMENT (BB_*) et non par des arguments : une
    # ligne de commande est lisible par n'importe qui via `ps`.
    export BB_REGISTRY="${BB_REGISTRY:-${REGISTRY%%/*}}"
    export BB_REGISTRY_NAMESPACE="${BB_REGISTRY_NAMESPACE:-${REGISTRY#*/}}"
    if [ -z "${BB_USERNAME:-}" ] || [ -z "${BB_PASSWORD:-}" ]; then
        command -v gh >/dev/null 2>&1 \
            || die "BB_USERNAME/BB_PASSWORD non definis et 'gh' introuvable pour les deriver."
        BB_USERNAME="${BB_USERNAME:-$(gh api user -q .login)}"
        BB_PASSWORD="${BB_PASSWORD:-$(gh auth token)}"
        export BB_USERNAME BB_PASSWORD
    fi
    [ -n "$BB_PASSWORD" ] || die "BB_PASSWORD vide (gh auth token n'a rien rendu ?)."
    log "registre bluebuild: ${BB_REGISTRY}/${BB_REGISTRY_NAMESPACE} (utilisateur ${BB_USERNAME})"
fi

log "tenant=${SLUG} image=${IMAGE} dry_run=${DRY_RUN}"
cd "$WORKDIR"

# --- 1. RPM du welcome agent ----------------------------------------------
log "1/7 build du RPM bluefox-welcome"
./scripts/build-welcome-rpm.sh

# --- 2. stage du RPM -------------------------------------------------------
# Le module `files` de BlueBuild copie files/usr/* vers /usr/* dans l'image ;
# le module `script` de la recipe fait ensuite un rpm-ostree install sur ce
# chemin. Le nom est aplati car le snippet BlueBuild ne fait pas de glob.
log "2/7 stage du RPM dans files/usr/share/bluefox/rpm-staging/"
RPM_STAGING="${WORKDIR}/files/usr/share/bluefox/rpm-staging"
mkdir -p "$RPM_STAGING"
rm -f "${RPM_STAGING}"/bluefox-welcome*.rpm
RPM_SRC="$(find "${WORKDIR}/welcome/build/RPMS/noarch" -name 'bluefox-welcome-*.noarch.rpm' -print -quit)"
[ -n "$RPM_SRC" ] || die "RPM introuvable apres build-welcome-rpm.sh"
cp "$RPM_SRC" "${RPM_STAGING}/bluefox-welcome.noarch.rpm"
log "    $(basename "$RPM_SRC") -> bluefox-welcome.noarch.rpm"

# --- 3. branding + stack KDE ----------------------------------------------
# BUILD=0 : on veut seulement materialiser files/ + tenant.json + le theme KDE.
# Le build lui-meme est lance a l'etape 4 (avec --push, ce que ce script-la
# ne fait pas).
log "3/7 stage du branding + generation du theme KDE"
SLUG="$SLUG" BUILD=0 ./scripts/build_branded_iso.sh

# --- 4. build + push + signature ------------------------------------------
# bluebuild signe l'image avec COSIGN_PRIVATE_KEY au moment du push (meme
# comportement que blue-build/github-action@v1, qui ne faisait que wrapper
# cette CLI). Le registre vient des BB_* exportes au preflight — il n'est PAS
# derive du remote git (cette croyance a coute un push en echec le 2026-07-19).
if [ "$DRY_RUN" = "1" ]; then
    log "4/7 DRY_RUN : build local sans push"
    # Pas de `--push=false` : dans BlueBuild 0.9.36 `--push` est un DRAPEAU
    # booleen (clap), il n'accepte aucune valeur. `--push=false` echoue avec
    # « unexpected value 'false' for '--push' ». Ne rien passer = ne pas pousser.
    bluebuild build "recipes/${SLUG}.yml"
    log "DRY_RUN termine — etapes 5 a 7 sautees (elles operent sur l'image publiee)."
    exit 0
fi

log "4/7 build + push + signature cosign"
bluebuild build --push "recipes/${SLUG}.yml"

# --- 5. SBOM ---------------------------------------------------------------
# --scope squashed : un seul SBOM sur l'image aplatie plutot que par layer.
# C'est ce que les utilisateurs exploitent au runtime, et c'est nettement
# moins gourmand que le defaut all-layers. Ce scan est precisement l'etape
# qui tuait le runner GitHub (~7 GB de RAM) — d'ou son rapatriement ici.
SBOM="${WORKDIR}/sbom-${SLUG}.spdx.json"
log "5/7 generation du SBOM SPDX -> $(basename "$SBOM")"
syft scan "registry:${IMAGE}" --scope squashed -o "spdx-json=${SBOM}"
log "    $(du -h "$SBOM" | cut -f1)"

# --- 6. attestation --------------------------------------------------------
log "6/7 attestation du SBOM via cosign"
cosign attest --yes \
    --predicate "$SBOM" \
    --type spdx \
    --key env://COSIGN_PRIVATE_KEY \
    "$IMAGE"

# --- 7. verification -------------------------------------------------------
log "7/7 verification signature + attestation"
cosign verify --key cosign.pub "$IMAGE" > /dev/null
cosign verify-attestation --key cosign.pub --type spdx "$IMAGE" > /dev/null

log "OK — ${IMAGE} publie, signe et atteste."
log "Controle : cosign tree ${IMAGE}  (doit lister Signatures ET Attestations)"
