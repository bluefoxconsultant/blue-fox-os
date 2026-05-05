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
#   - docker (or podman, but this script uses docker)
#   - ~/.docker/config.json with ghcr.io creds (image is private)
#   - ~10 GB free disk under $WORKDIR/output/

set -euo pipefail

SLUG="${1:-${SLUG:-bf}}"
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-ghcr.io/bluefoxconsultant}"
IMAGE="${REGISTRY}/blue-fox-os-${SLUG}:${TAG}"
BIB_IMAGE="${BIB_IMAGE:-quay.io/centos-bootc/bootc-image-builder:latest}"

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="${WORKDIR}/output"

# Auth file: bootc-image-builder runs as root inside its container and pulls
# the BF image with podman. podman reads ~/.docker/config.json if present.
# Here we resolve the *invoking* user's home (since `sudo` keeps $HOME unless
# `-H` was used) and fall back to /home/$SUDO_USER.
INVOKING_HOME="${HOME}"
if [ "${SUDO_USER:-}" != "" ] && [ -d "/home/${SUDO_USER}" ]; then
    INVOKING_HOME="/home/${SUDO_USER}"
fi
DOCKER_CFG="${INVOKING_HOME}/.docker/config.json"

if [ "$(id -u)" -ne 0 ]; then
    echo "[build-iso] this script needs --privileged ; re-run with sudo" >&2
    echo "    sudo $0 ${1:-bf}" >&2
    exit 1
fi

if [ ! -f "${DOCKER_CFG}" ]; then
    echo "[build-iso] FAIL: ${DOCKER_CFG} missing" >&2
    echo "    Run: docker login ghcr.io  (as the non-root user)" >&2
    exit 1
fi

mkdir -p "${OUTPUT}"

echo "[build-iso] tenant=${SLUG} tag=${TAG}"
echo "[build-iso] image=${IMAGE}"
echo "[build-iso] bib=${BIB_IMAGE}"
echo "[build-iso] output=${OUTPUT}"
echo "[build-iso] docker auth=${DOCKER_CFG}"
echo "[build-iso] expect 15–25 min (pull + squashfs + ISO assembly)"

# --pull=newer refreshes the BIB image itself.
# --type anaconda-iso matches CI release job.
# --rootfs btrfs matches Kinoite default.
# --use-librepo=true matches CI for repo metadata fetching.
docker run \
    --rm \
    --privileged \
    --pull=newer \
    -v "${DOCKER_CFG}:/root/.docker/config.json:ro" \
    -v "${OUTPUT}:/output" \
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
