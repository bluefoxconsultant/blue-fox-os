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

if [ -z "$AUTH_FILE" ]; then
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
EOF
    exit 1
fi
echo "[build-iso] auth=${AUTH_FILE}"

mkdir -p "${OUTPUT}"

echo "[build-iso] tenant=${SLUG} tag=${TAG}"
echo "[build-iso] image=${IMAGE}"
echo "[build-iso] bib=${BIB_IMAGE}"
echo "[build-iso] output=${OUTPUT}"
echo "[build-iso] expect 15–25 min (pull + squashfs + ISO assembly)"

# BIB persists pulled images into the host's container storage.
# `mkdir -p` keeps Ubuntu hosts happy where the dir doesn't pre-exist.
mkdir -p /var/lib/containers/storage

# Recent BIB versions no longer auto-pull. Pre-pull the BF image into
# rootful storage (BIB reads from there). Rootless auth at /run/user/UID/
# isn't visible to root by default, so pass --authfile explicitly.
if [ "$ENGINE" = "podman" ]; then
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
"${ENGINE}" run \
    --rm \
    --privileged \
    --pull=newer \
    --security-opt label=type:unconfined_t \
    -v "${AUTH_FILE}:/root/.docker/config.json:ro" \
    -v "${OUTPUT}:/output" \
    -v /var/lib/containers/storage:/var/lib/containers/storage \
    "${BIB_IMAGE}" \
    --type anaconda-iso \
    --rootfs btrfs \
    --use-librepo=true \
    "${IMAGE}"

ISO="${OUTPUT}/bootiso/install.iso"
if [ -f "${ISO}" ]; then
    SIZE=$(du -h "${ISO}" | cut -f1)
    echo "[build-iso] OK ${ISO} (${SIZE})"
    echo "[build-iso] burn:  sudo dd if=${ISO} of=/dev/sdX bs=4M status=progress  # replace sdX"
    echo "[build-iso] vm:    qemu-system-x86_64 -enable-kvm -m 8G -boot d -cdrom ${ISO}"
else
    echo "[build-iso] FAIL: ${ISO} not produced ; check output above" >&2
    exit 1
fi
