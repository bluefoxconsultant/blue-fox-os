#!/usr/bin/env bash
# scripts/sync-zerotouch-mirror.sh — sync the zero-touch install artifacts from
# this repo to the bf_zerotouch_install Odoo addon's data/ directory:
#   - install/blue-fox-install.ks.template  (the kickstart template)
#   - install/bfos_provision.py             (%pre device-flow, embedded by the
#                                            controller into {{PROVISION_SCRIPT}})
#   - install/bfos_apply.py                 (%post apply, embedded into
#                                            {{APPLY_SCRIPT}})
#
# The Odoo controller reads all three from disk at request time, so a file copy
# is enough — no Odoo restart needed (Python file caching is not in play for the
# data files). For a Python code change in the controller ITSELF, run a module
# upgrade via /install-odoo-module.
#
# Run this AFTER editing any of the three sources, BEFORE pushing to the BFOS
# repo, so the live endpoint and the canonical source stay in sync.
#
# Usage:
#   ./scripts/sync-zerotouch-mirror.sh
#
# Override the destination addon path with BF_ZEROTOUCH_ADDON :
#   BF_ZEROTOUCH_ADDON=/path/to/addon ./scripts/sync-zerotouch-mirror.sh

set -euo pipefail

WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
DST_DIR="${BF_ZEROTOUCH_ADDON:-${HOME}/odoo-clients/blue-fox-inc/addons/bf_zerotouch_install}"

if [ ! -d "${DST_DIR}" ]; then
    echo "[sync-zerotouch-mirror] FAIL: destination addon not found ${DST_DIR}" >&2
    echo "    Set BF_ZEROTOUCH_ADDON=... or check out the addon repo." >&2
    exit 1
fi

ARTIFACTS=(
    "blue-fox-install.ks.template"
    "bfos_provision.py"
    "bfos_apply.py"
)

changed=0
for name in "${ARTIFACTS[@]}"; do
    src="${WORKDIR}/install/${name}"
    dst="${DST_DIR}/data/${name}"
    if [ ! -f "${src}" ]; then
        echo "[sync-zerotouch-mirror] FAIL: source not found ${src}" >&2
        exit 1
    fi
    mkdir -p "$(dirname "${dst}")"
    if cmp -s "${src}" "${dst}"; then
        echo "[sync-zerotouch-mirror] OK   : ${name} already in sync"
        continue
    fi
    cp "${src}" "${dst}"
    echo "[sync-zerotouch-mirror] copied: ${name}"
    changed=1
done

if [ "${changed}" -eq 1 ]; then
    echo "[sync-zerotouch-mirror] verify endpoint: curl -fsSL https://bluefoxconsultant.com/blue-fox-install.ks | head -10"
fi
