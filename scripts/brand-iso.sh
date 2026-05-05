#!/usr/bin/env bash
# scripts/brand-iso.sh — post-process a BIB-produced anaconda-iso to add
# Blue Fox boot-menu branding (GRUB theme, splash, volume label).
#
# BIB hard-codes Lorax templates per distro (see legacy_iso.go::loraxFedoraTemplates);
# overriding upstream is invasive. The pragmatic path: extract the ISO with
# xorriso, swap the chrome assets, repack preserving the EFI/BIOS hybrid
# boot signature via `xorriso -boot_image any replay`.
#
# What this brands:
#   - GRUB boot menu: BF blue selection bar + Lexend font + "Blue Fox OS Installer" title
#   - ISO volume label: "Blue Fox OS Installer"
#   - Boot splash background (when GRUB theme renders it)
#
# What this does NOT brand (out of scope, see plan Phase 3.5):
#   - Anaconda installer GUI chrome (sidebar logo, title bar) — needs an
#     anaconda-product RPM in the install-time root, deferred to v0.1.
#
# Usage: ./scripts/brand-iso.sh path/to/install.iso
#        Output: same path, atomically replaced with the branded ISO.

set -euo pipefail

ISO="${1:?usage: $0 path/to/install.iso}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
BRANDING="${WORKDIR}/branding"
GRUB_THEME_DIR="${BRANDING}/grub-theme"

if [ ! -f "${ISO}" ]; then
    echo "[brand-iso] FAIL: ${ISO} not found" >&2
    exit 1
fi
if ! command -v xorriso >/dev/null 2>&1; then
    cat >&2 <<EOF
[brand-iso] FAIL: xorriso not in PATH.
    sudo apt install -y xorriso       # Ubuntu/Debian
    sudo dnf install -y xorriso       # Fedora/Kinoite
EOF
    exit 1
fi
for asset in "${GRUB_THEME_DIR}/theme.txt" "${GRUB_THEME_DIR}/select_bg.png"; do
    if [ ! -f "${asset}" ]; then
        echo "[brand-iso] FAIL: missing GRUB theme asset ${asset}" >&2
        exit 1
    fi
done

VOLID="BLUE_FOX_OS_INST"   # ISO9660 volume IDs are uppercase, max 32 chars,
                           # ASCII letters/digits/underscore only.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "${TMPDIR}"' EXIT

echo "[brand-iso] iso=${ISO}"
echo "[brand-iso] tmp=${TMPDIR}"

# 1. Locate the existing GRUB config in the ISO. BIB-produced ISOs have it at
# /EFI/BOOT/grub.cfg (UEFI) and sometimes /boot/grub2/grub.cfg too. We extract,
# patch, and put back via xorriso -map.
echo "[brand-iso] extracting grub.cfg from ISO"
xorriso -osirrox on -indev "${ISO}" \
    -extract /EFI/BOOT/grub.cfg "${TMPDIR}/grub.cfg.orig" \
    2>&1 | grep -vE '^(xorriso|libisoburn|libburn|libisofs|GNU)' || true

if [ ! -f "${TMPDIR}/grub.cfg.orig" ]; then
    echo "[brand-iso] WARN: /EFI/BOOT/grub.cfg not found in ISO — skipping GRUB theme injection" >&2
    PATCH_GRUB=0
else
    PATCH_GRUB=1
    # Prepend the Blue Fox theme directives to the existing grub.cfg. We don't
    # rewrite menu entries (they encode kickstart inst.ks=hd:LABEL=... that
    # references our VOLID, see step 2 below) — just add theme + title + bg.
    cat > "${TMPDIR}/grub.cfg.bf-prelude" <<'EOF'
# Blue Fox OS branding injected by scripts/brand-iso.sh.
set theme=/EFI/BOOT/themes/blue-fox/theme.txt
export theme
set color_normal=white/black
set color_highlight=white/blue
set menu_color_normal=white/black
set menu_color_highlight=white/blue
set timeout=5
EOF
    cat "${TMPDIR}/grub.cfg.bf-prelude" "${TMPDIR}/grub.cfg.orig" \
        > "${TMPDIR}/grub.cfg"

    # If menu entries reference the old volume label (e.g. inst.ks=hd:LABEL=Fedora-...),
    # rewrite to our new VOLID so kickstart still loads. xorriso -map updates
    # the volume label below; we sync any LABEL= references here.
    OLD_VOLID=$(xorriso -indev "${ISO}" -toc 2>/dev/null \
        | grep -oE 'Volume id\s*:\s*.*' | head -1 \
        | sed 's/Volume id\s*:\s*//' | tr -d "'\" " || echo "")
    if [ -n "${OLD_VOLID}" ] && [ "${OLD_VOLID}" != "${VOLID}" ]; then
        echo "[brand-iso] rewriting LABEL=${OLD_VOLID} → LABEL=${VOLID} in grub.cfg"
        sed -i "s|LABEL=${OLD_VOLID}|LABEL=${VOLID}|g" "${TMPDIR}/grub.cfg"
    fi
fi

# 2. Repack the ISO: replace volume label, drop in GRUB theme files, replace
# grub.cfg, preserve the original boot signature (UEFI El Torito + BIOS
# isohybrid MBR) via -boot_image any replay.
echo "[brand-iso] repacking ISO with branded chrome"
ISO_OUT="${ISO}.branded"

XORRISO_ARGS=(
    -indev "${ISO}"
    -outdev "${ISO_OUT}"
    -boot_image any replay
    -volid "${VOLID}"
    -map "${GRUB_THEME_DIR}/theme.txt"      "/EFI/BOOT/themes/blue-fox/theme.txt"
    -map "${GRUB_THEME_DIR}/select_bg.png"  "/EFI/BOOT/themes/blue-fox/select_bg.png"
)
if [ -f "${BRANDING}/splash.png" ]; then
    XORRISO_ARGS+=(-map "${BRANDING}/splash.png" "/EFI/BOOT/themes/blue-fox/background.png")
fi
if [ "${PATCH_GRUB}" = "1" ]; then
    XORRISO_ARGS+=(-map "${TMPDIR}/grub.cfg" "/EFI/BOOT/grub.cfg")
fi

xorriso "${XORRISO_ARGS[@]}"

# 3. Atomic swap.
mv "${ISO_OUT}" "${ISO}"
SIZE=$(du -h "${ISO}" | cut -f1)
echo "[brand-iso] OK ${ISO} (${SIZE})"
