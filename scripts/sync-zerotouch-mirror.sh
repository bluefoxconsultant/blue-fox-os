#!/usr/bin/env bash
# scripts/sync-zerotouch-mirror.sh — sync install/blue-fox-install.ks.template
# from this repo to the bf_zerotouch_install Odoo addon's data/ directory.
#
# The Odoo controller reads the template from disk at request time, so a file
# copy is enough — no Odoo restart needed (Python file caching is not in play
# for the data file). For a Python code change in the controller, run a module
# upgrade via /install-odoo-module.
#
# Run this AFTER editing install/blue-fox-install.ks.template, BEFORE pushing
# to the BFOS repo, so the live endpoint and the canonical source stay in sync.
#
# Usage:
#   ./scripts/sync-zerotouch-mirror.sh
#
# Override the destination addon path with BF_ZEROTOUCH_ADDON :
#   BF_ZEROTOUCH_ADDON=/path/to/addon ./scripts/sync-zerotouch-mirror.sh

set -euo pipefail

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${WORKDIR}/install/blue-fox-install.ks.template"
DST_DIR="${BF_ZEROTOUCH_ADDON:-${HOME}/odoo-clients/blue-fox-inc/addons/bf_zerotouch_install}"
DST="${DST_DIR}/data/blue-fox-install.ks.template"

if [ ! -f "${SRC}" ]; then
    echo "[sync-zerotouch-mirror] FAIL: source not found ${SRC}" >&2
    exit 1
fi
if [ ! -d "${DST_DIR}" ]; then
    echo "[sync-zerotouch-mirror] FAIL: destination addon not found ${DST_DIR}" >&2
    echo "    Set BF_ZEROTOUCH_ADDON=... or check out the addon repo." >&2
    exit 1
fi

mkdir -p "$(dirname "${DST}")"
if cmp -s "${SRC}" "${DST}"; then
    echo "[sync-zerotouch-mirror] OK : already in sync (${DST})"
    exit 0
fi
cp "${SRC}" "${DST}"
echo "[sync-zerotouch-mirror] copied ${SRC} -> ${DST}"
echo "[sync-zerotouch-mirror] verify endpoint: curl -fsSL https://bluefoxconsultant.com/blue-fox-install.ks | head -10"
