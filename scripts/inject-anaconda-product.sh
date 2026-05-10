#!/usr/bin/env bash
# scripts/inject-anaconda-product.sh — overlay Blue Fox OS chrome into the
# Anaconda stage2 squashfs of a BIB-produced ISO.
#
# Tracks Odoo tasks BF #22417 (logo), BF #22418 (sidebar pattern), and the
# GUI-title half of BF #22419 (« BLUE FOX OS INSTALLATION »). The runtime
# keyboard + locale halves of #22419 are handled by boot params in
# scripts/brand-iso.sh and don't go through this script.
#
# Mechanism:
#   1. Extract /images/install.img (or LiveOS/squashfs.img) from the ISO.
#   2. unsquashfs into a temp dir.
#   3. Drop in (when present) profile.d/blue-fox-os.conf, the matching CSS,
#      a patched /.buildstamp, and any sidebar pixmaps from branding/anaconda/.
#   4. mksquashfs back with the same compression (xz, 1M block size).
#   5. xorriso -map the new install.img back into the ISO in place.
#
# Skip behaviour: if NONE of the expected asset files exist in
# branding/anaconda/, the script logs "no assets, skipped" and exits 0.
# Individual missing files are also skipped — the overlay applies only the
# files that are present.
#
# Usage: scripts/inject-anaconda-product.sh path/to/install.iso
#        Output: same path, ISO updated in place via atomic swap.
#
# Called by scripts/brand-iso.sh after the bootloader chrome step and
# before the final xorriso repack. Can also be invoked standalone for
# regression testing once André's final assets land.

set -euo pipefail

ISO="${1:?usage: $0 path/to/install.iso}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="${WORKDIR}/branding/anaconda"

# Asset → stage2 path mapping. Order doesn't matter; missing entries are
# silently skipped so a partial drop (e.g. only the CSS + conf, no pixmaps
# yet) still produces a working overlay.
declare -A OVERLAY=(
    ["${ASSETS}/blue-fox-os.conf"]="usr/share/anaconda/profile.d/blue-fox-os.conf"
    ["${ASSETS}/blue-fox-os.css"]="usr/share/anaconda/pixmaps/blue-fox-os.css"
    ["${ASSETS}/buildstamp.ini"]=".buildstamp"
    ["${ASSETS}/sidebar-bg.png"]="usr/share/anaconda/pixmaps/sidebar-bg.png"
    ["${ASSETS}/sidebar-logo.png"]="usr/share/anaconda/pixmaps/sidebar-logo.png"
)

# Skip-fast: no assets at all → nothing to do. The boot-param half of the
# fix (in brand-iso.sh) still applies — installer just keeps Fedora chrome.
PRESENT=()
for src in "${!OVERLAY[@]}"; do
    if [ -f "${src}" ]; then
        PRESENT+=("${src}")
    fi
done

if [ ${#PRESENT[@]} -eq 0 ]; then
    echo "[anaconda-overlay] no assets in ${ASSETS}, skipped"
    exit 0
fi

echo "[anaconda-overlay] iso=${ISO}"
echo "[anaconda-overlay] ${#PRESENT[@]} asset(s) to overlay"

for tool in xorriso unsquashfs mksquashfs; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        cat >&2 <<EOF
[anaconda-overlay] FAIL: ${tool} not in PATH.
    sudo apt install -y xorriso squashfs-tools       # Ubuntu/Debian
    sudo dnf install -y xorriso squashfs-tools       # Fedora/Kinoite
EOF
        exit 1
    fi
done

TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

# 1. Locate stage2. BIB Fedora ISOs put it at /images/install.img.
# Live-ISO style (LiveOS/squashfs.img) is a fallback that older Fedora
# layouts use — keep both probes so a future BIB layout change doesn't
# silently no-op the overlay.
STAGE2_CANDIDATES=("/images/install.img" "/LiveOS/squashfs.img")
STAGE2=""
for path in "${STAGE2_CANDIDATES[@]}"; do
    if xorriso -indev "${ISO}" -find "${path}" 2>/dev/null | grep -q "${path}"; then
        STAGE2="${path}"
        break
    fi
done
if [ -z "${STAGE2}" ]; then
    echo "[anaconda-overlay] FAIL: no stage2 squashfs found in ${ISO} (tried ${STAGE2_CANDIDATES[*]})" >&2
    exit 1
fi
echo "[anaconda-overlay] stage2=${STAGE2}"

# 2. Extract stage2 from the ISO.
mkdir -p "${TMPDIR}/iso-extract"
xorriso -osirrox on -indev "${ISO}" \
    -extract "${STAGE2}" "${TMPDIR}/iso-extract/install.img" \
    2>&1 | grep -vE '^(xorriso|libisoburn|libburn|libisofs|GNU)' || true

if [ ! -f "${TMPDIR}/iso-extract/install.img" ]; then
    echo "[anaconda-overlay] FAIL: could not extract ${STAGE2}" >&2
    exit 1
fi

# 3. unsquashfs. Use -no-xattrs because Anaconda's stage2 has selinux xattrs
# that mksquashfs may not have perms to recreate as a normal user — but
# only the file contents matter for our overlay, the kernel reapplies
# labels at install time via the policy in the install root.
echo "[anaconda-overlay] unsquashfs"
unsquashfs -d "${TMPDIR}/squashfs-root" -no-xattrs -quiet \
    "${TMPDIR}/iso-extract/install.img"

# 4. Apply overlay files individually so partial drops still work.
APPLIED=0
for src in "${PRESENT[@]}"; do
    dst="${TMPDIR}/squashfs-root/${OVERLAY[${src}]}"
    mkdir -p "$(dirname "${dst}")"
    cp -f "${src}" "${dst}"
    echo "[anaconda-overlay] + ${OVERLAY[${src}]}"
    APPLIED=$((APPLIED + 1))
done
echo "[anaconda-overlay] applied ${APPLIED} overlay file(s)"

# 5. mksquashfs back. Match Fedora's stage2 compression (xz, BCJ filter for
# x86_64, 1M block size) to keep the ISO size identical-ish. -noappend
# overwrites any leftover output.
echo "[anaconda-overlay] mksquashfs"
mksquashfs "${TMPDIR}/squashfs-root" "${TMPDIR}/install.img.new" \
    -comp xz -Xbcj x86 -b 1048576 \
    -no-xattrs -noappend -no-progress 2>&1 \
    | tail -3

# 6. Repack the ISO with the new stage2 mapped at the original path.
# -boot_image any replay preserves the EFI El Torito + BIOS isohybrid MBR
# signatures (same trick brand-iso.sh uses for grub.cfg).
echo "[anaconda-overlay] repacking ISO"
ISO_OUT="${ISO}.anaconda-overlay"
xorriso -indev "${ISO}" -outdev "${ISO_OUT}" \
    -boot_image any replay \
    -map "${TMPDIR}/install.img.new" "${STAGE2}"

mv "${ISO_OUT}" "${ISO}"
SIZE=$(du -h "${ISO}" | cut -f1)
echo "[anaconda-overlay] OK ${ISO} (${SIZE})"
