#!/usr/bin/env bash
# scripts/build-iso.sh — local Anaconda ISO build via bootc-image-builder.
#
# Mirrors the CI release job (.github/workflows/build.yml :: release) for
# devs who want to test an image locally before tagging. Pulls the
# already-built OCI image from ghcr.io rather than rebuilding it.
#
# Usage:
#   sudo ./scripts/build-iso.sh                # SLUG=bf, TAG=latest
#   sudo ./scripts/build-iso.sh bf-surface     # different tenant
#   sudo SLUG=factice TAG=v26.07 ./scripts/build-iso.sh
#
# Requires:
#   - podman OR docker (auto-detected, podman preferred — BIB native)
#   - ghcr.io credentials in ~/.config/containers/auth.json (podman)
#     or ~/.docker/config.json (docker). One-liner to bootstrap from gh:
#         gh auth token | podman login ghcr.io -u "$(gh api user -q .login)" --password-stdin
#         (or replace `podman` with `docker`)
#   - ~10 GB free disk under $WORKDIR/output/

set -euo pipefail

SLUG="${1:-${SLUG:-bf}}"
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-ghcr.io/bluefoxconsultant}"
IMAGE="${REGISTRY}/blue-fox-os-${SLUG}:${TAG}"
BIB_IMAGE="${BIB_IMAGE:-quay.io/centos-bootc/bootc-image-builder:latest}"

# --- reseau du container BIB -----------------------------------------------
# ⚠️ BIB a besoin de DNS : il depsolve l'environnement Anaconda depuis les
# depots Fedora, PLUS les depots portes par l'image (dont, depuis #23812, notre
# linux-surface.repo en skip_if_unavailable=0 — un depot injoignable fait donc
# echouer la construction de l'ISO, a dessein).
#
# Panne vecue le 2026-07-25 sur charizard : « Could not resolve host:
# mirrors.fedoraproject.org » apres ~4 min de pull. Cause : le seul resolveur de
# l'hote est **Tailscale MagicDNS** (100.100.100.100). tailscaled sert cette
# adresse aux processus LOCAUX ; les paquets qui arrivent d'un pont podman
# ROOTFUL n'y accedent pas. Le build d'image, lui, est rootless et passe par le
# namespace reseau de l'hote — d'ou un build OCI qui marche et une ISO qui
# echoue, sur la meme machine.
#
# --network=host place BIB dans le namespace de l'hote : la resolution emprunte
# exactement le chemin qui fonctionne deja. BIB tourne de toute facon en
# --privileged avec le stockage de l'hote monte, donc ca n'ouvre rien de plus.
# BIB_DNS=1.1.1.1 (ou plusieurs, separes par des virgules) reste disponible si
# un jour l'hote a un resolveur atteignable depuis un pont.
BIB_NETWORK="${BIB_NETWORK:-host}"
DNS_ARGS=()
if [ -n "${BIB_DNS:-}" ]; then
    IFS=',' read -r -a _dns <<< "$BIB_DNS"
    for d in "${_dns[@]}"; do DNS_ARGS+=(--dns="$d"); done
fi

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${WORKDIR}/output"

# Resolve the *invoking* user's home (sudo keeps $HOME unless `-H` was used).
INVOKING_HOME="${HOME}"
if [ "${SUDO_USER:-}" != "" ] && [ -d "/home/${SUDO_USER}" ]; then
    INVOKING_HOME="/home/${SUDO_USER}"
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "[build-iso] this script needs --privileged ; re-run with sudo" >&2
    echo "    sudo $0 ${1:-bf}" >&2
    exit 1
fi

# Pick a container engine. podman is preferred (BIB designed for it,
# storage layout matches). Allow ENGINE=docker|podman to override.
ENGINE="${ENGINE:-}"
if [ -z "$ENGINE" ]; then
    if command -v podman >/dev/null 2>&1; then
        ENGINE=podman
    elif command -v docker >/dev/null 2>&1; then
        ENGINE=docker
    else
        cat >&2 <<EOF
[build-iso] FAIL: neither podman nor docker found in PATH.
    Install one of:
      sudo apt install -y podman          # Ubuntu/Debian — recommended
      sudo dnf install -y podman          # Fedora/Kinoite
EOF
        exit 1
    fi
fi
echo "[build-iso] engine=${ENGINE}"

# Locate auth file. podman rootless writes to $XDG_RUNTIME_DIR (transient,
# typically /run/user/UID/containers/auth.json) — that's where `podman login`
# lands by default. Persistent paths (~/.config/containers/, ~/.docker/) are
# also checked. Map whichever is found into BIB at /root/.docker/config.json
# (BIB's internal podman reads that path).
#
# SKIP_PULL=1 : bypass ghcr pull and auth check. Use when the BF OCI image
# is already in rootful /var/lib/containers/storage (e.g. just built locally
# via build_branded_iso.sh + manual `podman tag` to the ghcr ref). Useful
# when GHCR is unreachable (CI billing suspended) or when iterating on a
# local-only image without pushing.
INVOKING_UID=""
if [ "${SUDO_USER:-}" != "" ]; then
    INVOKING_UID="$(id -u "${SUDO_USER}" 2>/dev/null || echo "")"
fi
AUTH_CANDIDATES=(
    "${INVOKING_HOME}/.config/containers/auth.json"
    "${INVOKING_HOME}/.docker/config.json"
)
if [ -n "${INVOKING_UID}" ]; then
    AUTH_CANDIDATES+=("/run/user/${INVOKING_UID}/containers/auth.json")
fi
AUTH_FILE=""
for cand in "${AUTH_CANDIDATES[@]}"; do
    if [ -f "$cand" ]; then
        AUTH_FILE="$cand"
        break
    fi
done

if [ "${SKIP_PULL:-0}" = "1" ]; then
    echo "[build-iso] SKIP_PULL=1 ; bypassing ghcr pull, using local /var/lib/containers/storage"
    if [ -z "$AUTH_FILE" ]; then
        AUTH_FILE="/dev/null"
    fi
elif [ -z "$AUTH_FILE" ]; then
    cat >&2 <<EOF
[build-iso] FAIL: no ghcr.io auth found.
    Tried: ${AUTH_CANDIDATES[*]}

    Bootstrap (as the non-root user, requires gh CLI logged in):
        gh auth token | ${ENGINE} login ghcr.io -u "\$(gh api user -q .login)" --password-stdin

    Or with a PAT (read:packages scope) from https://github.com/settings/tokens/new :
        echo '<TOKEN>' | ${ENGINE} login ghcr.io -u <username> --password-stdin

    Note: podman stores auth in \$XDG_RUNTIME_DIR (transient, lost on reboot).
    To persist, copy it after login:
        cp /run/user/\$(id -u)/containers/auth.json ~/.config/containers/auth.json

    Local-only escape hatch (GHCR unreachable, image already built locally):
        sudo podman tag localhost/blue-fox-os-${SLUG}:latest ${IMAGE}
        sudo SKIP_PULL=1 \$0 ${1:-bf}
EOF
    exit 1
fi
echo "[build-iso] auth=${AUTH_FILE}"

mkdir -p "${OUTPUT}"

# Render install/bib-config.toml from install/bf-os.ks. BIB embeds this as the
# kickstart so Anaconda prompts for LUKS passphrase + user creation per BF spec.
# Without it (BIB called with no --config), Anaconda falls back to a bare
# OSTree-only kickstart and the install completes with no rootpw lock + an
# unattended user account whose password is unknown.
echo "[build-iso] rendering install/bib-config.toml"
python3 "${WORKDIR}/scripts/render_bib_config.py"
BIB_CONFIG="${WORKDIR}/install/bib-config.toml"
if [ ! -f "${BIB_CONFIG}" ]; then
    echo "[build-iso] FAIL: ${BIB_CONFIG} not produced" >&2
    exit 1
fi

echo "[build-iso] tenant=${SLUG} tag=${TAG}"
echo "[build-iso] image=${IMAGE}"
echo "[build-iso] bib=${BIB_IMAGE}"
echo "[build-iso] config=${BIB_CONFIG}"
echo "[build-iso] output=${OUTPUT}"
echo "[build-iso] expect 15–25 min (pull + squashfs + ISO assembly)"

# BIB persists pulled images into the host's container storage.
# `mkdir -p` keeps Ubuntu hosts happy where the dir doesn't pre-exist.
mkdir -p /var/lib/containers/storage

# Recent BIB versions no longer auto-pull. Pre-pull the BF image into
# rootful storage (BIB reads from there). Rootless auth at /run/user/UID/
# isn't visible to root by default, so pass --authfile explicitly.
if [ "${SKIP_PULL:-0}" = "1" ]; then
    echo "[build-iso] SKIP_PULL=1 ; verifying ${IMAGE} present in rootful storage"
    if ! ${ENGINE} image exists "${IMAGE}" 2>/dev/null; then
        cat >&2 <<EOF
[build-iso] FAIL: SKIP_PULL=1 but ${IMAGE} not found in rootful storage.
    Build the image locally then tag for the GHCR ref this script expects:
        cd ${WORKDIR}
        SLUG=${SLUG} ./scripts/build_branded_iso.sh
        sudo ${ENGINE} tag localhost/blue-fox-os-${SLUG}:latest ${IMAGE}
EOF
        exit 1
    fi
elif [ "$ENGINE" = "podman" ]; then
    echo "[build-iso] pulling ${IMAGE} into rootful storage"
    podman pull --authfile "${AUTH_FILE}" "${IMAGE}"
else
    echo "[build-iso] pulling ${IMAGE}"
    DOCKER_CONFIG="$(dirname "${AUTH_FILE}")" docker pull "${IMAGE}"
fi

# --pull=newer refreshes the BIB image itself.
# --type anaconda-iso matches CI release job.
# --rootfs btrfs matches Kinoite default.
# --use-librepo=true matches CI for repo metadata fetching.
# --security-opt label=type:unconfined_t matches CI on SELinux hosts.
# Controle DNS AVANT de lancer 20 minutes de build : la panne du 2026-07-25 ne
# s'est manifestee qu'apres le pull de BIB et le debut du depsolve. Meme mode
# reseau que le run reel, sinon le controle ne prouve rien.
echo "[build-iso] preflight DNS dans le container (reseau=${BIB_NETWORK})"
if ! "${ENGINE}" run --rm --network="${BIB_NETWORK}" ${DNS_ARGS[@]+"${DNS_ARGS[@]}"} \
        --entrypoint /bin/sh "${BIB_IMAGE}" \
        -c 'getent hosts mirrors.fedoraproject.org >/dev/null 2>&1'; then
    cat >&2 <<EOF
[build-iso] FAIL: pas de DNS dans le container BIB (reseau=${BIB_NETWORK}).

BIB depsolve l'environnement Anaconda depuis les depots Fedora : sans
resolution, il echoue apres plusieurs minutes de pull.

Si l'hote resout via Tailscale MagicDNS (100.100.100.100), un pont podman
ROOTFUL n'y a pas acces — c'est la panne du 2026-07-25. Contournements :
    sudo BIB_NETWORK=host  SKIP_PULL=1 $0 ${SLUG}     # defaut
    sudo BIB_DNS=1.1.1.1   SKIP_PULL=1 $0 ${SLUG}     # resolveur explicite
EOF
    exit 1
fi
echo "[build-iso]   DNS OK"

"${ENGINE}" run \
    --rm \
    --privileged \
    --network="${BIB_NETWORK}" \
    ${DNS_ARGS[@]+"${DNS_ARGS[@]}"} \
    --pull=newer \
    --security-opt label=type:unconfined_t \
    -v "${AUTH_FILE}:/root/.docker/config.json:ro" \
    -v "${BIB_CONFIG}:/config.toml:ro" \
    -v "${OUTPUT}:/output" \
    -v /var/lib/containers/storage:/var/lib/containers/storage \
    "${BIB_IMAGE}" \
    --type anaconda-iso \
    --rootfs btrfs \
    --use-librepo=true \
    --config /config.toml \
    "${IMAGE}"

ISO="${OUTPUT}/bootiso/install.iso"
if [ ! -f "${ISO}" ]; then
    echo "[build-iso] FAIL: ${ISO} not produced ; check output above" >&2
    exit 1
fi

# Brand the ISO chrome (boot menu, GRUB theme, splash). BIB only brands the
# *installed* system; the ISO boot menu and Anaconda chrome are stock Fedora
# without this post-process step.
echo "[build-iso] branding ISO chrome"
"${WORKDIR}/scripts/brand-iso.sh" "${ISO}"

SIZE=$(du -h "${ISO}" | cut -f1)
echo "[build-iso] OK ${ISO} (${SIZE})"
echo "[build-iso] burn:  sudo dd if=${ISO} of=/dev/sdX bs=4M status=progress  # replace sdX"
echo "[build-iso] vm:    qemu-system-x86_64 -enable-kvm -m 8G -boot d -cdrom ${ISO}"
