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
# Anaconda product branding (v0.1.1, BF #22417/#22418/#22419):
#   - Runtime keyboard + locale: handled here via `inst.keymap=ca` and
#     `inst.lang=fr_CA.UTF-8` boot params injected in the menuentries below
#     (closes the keyboard half of #22419).
#   - Anaconda installer GUI chrome (sidebar/logo pixmaps + profile.d config
#     + .buildstamp title): overlaid into the stage2 squashfs via
#     scripts/inject-anaconda-product.sh, called at the end of this script.
#     Selected at boot via `inst.profile=blue-fox-os`. The full asset spec
#     is documented in branding/anaconda/README.md — the text-only files
#     (profile config, CSS, .buildstamp) ship here; sidebar PNGs are pending
#     design and the overlay script skips them gracefully until they land.
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

    # Discover vmlinuz + initrd paths from the existing config. BIB-produced
    # Fedora ISOs typically use /images/pxeboot/vmlinuz + initrd.img, but
    # we extract from the file rather than hardcode in case BIB changes.
    VMLINUZ=$(grep -oE '(linuxefi|linux)\s+\S*vmlinuz\S*' "${TMPDIR}/grub.cfg.orig" \
        | head -1 | awk '{print $NF}' || echo "/images/pxeboot/vmlinuz")
    INITRD=$(grep -oE '(initrdefi|initrd)\s+\S*initrd\S*' "${TMPDIR}/grub.cfg.orig" \
        | head -1 | awk '{print $NF}' || echo "/images/pxeboot/initrd.img")
    echo "[brand-iso] discovered vmlinuz=${VMLINUZ} initrd=${INITRD}"

    # Build the new grub.cfg from scratch:
    #   1. Theme prelude
    #   2. Zero-touch menuentry (default) — prompts for org domain via `read`
    #   3. Built-in defaults menuentry (fallback) — uses the embedded BIB kickstart
    #   4. The original BIB-generated entries below as deeper fallbacks
    cat > "${TMPDIR}/grub.cfg" <<EOF
# Blue Fox OS — branded grub.cfg (injected by scripts/brand-iso.sh).
# Original BIB grub.cfg appended below as fallback entries.

set theme=/EFI/BOOT/themes/blue-fox/theme.txt
export theme
set color_normal=white/black
set color_highlight=white/blue
set menu_color_normal=white/black
set menu_color_highlight=white/blue
set timeout=10
set default=0

menuentry 'Install Blue Fox OS (zero-touch)' --class fedora --class gnu-linux {
    set bf_domain=""
    echo ""
    echo "----------------------------------------------------------------"
    echo " Blue Fox OS — zero-touch install"
    echo "----------------------------------------------------------------"
    echo ""
    echo " Type your organization's root domain and press <enter>."
    echo " Example: bluefoxconsultant.com"
    echo ""
    echo " (Empty input falls back to bluefoxconsultant.com.)"
    echo ""
    echo -n " Org domain: "
    read bf_domain
    if [ -z "\${bf_domain}" ]; then
        set bf_domain="bluefoxconsultant.com"
        echo " -> Using bluefoxconsultant.com"
    fi
    echo ""
    echo " Fetching install config from https://\${bf_domain}/blue-fox-install.ks"
    echo " Anaconda will prompt for LUKS passphrase + user creation."
    echo ""
    linuxefi ${VMLINUZ} inst.stage2=hd:LABEL=${VOLID} inst.ks=https://\${bf_domain}/blue-fox-install.ks inst.profile=blue-fox-os inst.keymap=ca inst.lang=fr_CA.UTF-8 ip=dhcp quiet
    initrdefi ${INITRD}
}

menuentry 'Install Blue Fox OS (built-in defaults)' --class fedora --class gnu-linux {
    linuxefi ${VMLINUZ} inst.stage2=hd:LABEL=${VOLID} inst.profile=blue-fox-os inst.keymap=ca inst.lang=fr_CA.UTF-8 quiet
    initrdefi ${INITRD}
}

menuentry 'Test this media & install Blue Fox OS' --class fedora --class gnu-linux {
    linuxefi ${VMLINUZ} inst.stage2=hd:LABEL=${VOLID} inst.profile=blue-fox-os inst.keymap=ca inst.lang=fr_CA.UTF-8 rd.live.check quiet
    initrdefi ${INITRD}
}

submenu 'Original BIB entries (fallback)' --class submenu {
EOF

    # Indent + append the original BIB-generated grub.cfg as a submenu so it's
    # accessible if our injected entries fail. Strip its own `set timeout=` etc.
    # to avoid overriding our prelude.
    sed -E 's/^/    /; /^[[:space:]]*set (timeout|default|theme)/d' \
        "${TMPDIR}/grub.cfg.orig" >> "${TMPDIR}/grub.cfg"

    cat >> "${TMPDIR}/grub.cfg" <<'EOF'
}
EOF

    # Sync any LABEL= references in the appended block to our VOLID.
    OLD_VOLID=$(xorriso -indev "${ISO}" -toc 2>/dev/null \
        | grep -oE 'Volume id\s*:\s*.*' | head -1 \
        | sed 's/Volume id\s*:\s*//' | tr -d "'\" " || echo "")
    if [ -n "${OLD_VOLID}" ] && [ "${OLD_VOLID}" != "${VOLID}" ]; then
        echo "[brand-iso] rewriting LABEL=${OLD_VOLID} → LABEL=${VOLID} in grub.cfg"
        sed -i "s|LABEL=${OLD_VOLID}|LABEL=${VOLID}|g" "${TMPDIR}/grub.cfg"
    fi
fi

# 2. (Optional) BIOS path: if /isolinux/isolinux.cfg exists, mirror the
# zero-touch entry there too so legacy BIOS-boot still picks it up. Modern
# BIB ISOs are EFI-only, but older hardware may need this.
PATCH_ISOLINUX=0
xorriso -osirrox on -indev "${ISO}" \
    -extract /isolinux/isolinux.cfg "${TMPDIR}/isolinux.cfg.orig" \
    2>/dev/null || true
if [ -f "${TMPDIR}/isolinux.cfg.orig" ]; then
    PATCH_ISOLINUX=1
    cat > "${TMPDIR}/isolinux.cfg" <<EOF
# Blue Fox OS — branded isolinux.cfg (injected by scripts/brand-iso.sh).
# isolinux has no equivalent of GRUB \`read\` — BIOS users only get the
# built-in defaults entry. UEFI users get the full zero-touch flow via grub.cfg.

default vesamenu.c32
timeout 100
prompt 0
menu title Blue Fox OS Installer (BIOS)

label builtin
    menu label ^Install Blue Fox OS (built-in defaults)
    menu default
    kernel ${VMLINUZ}
    append initrd=${INITRD} inst.stage2=hd:LABEL=${VOLID} inst.profile=blue-fox-os inst.keymap=ca inst.lang=fr_CA.UTF-8 quiet

label test
    menu label ^Test this media & install
    kernel ${VMLINUZ}
    append initrd=${INITRD} inst.stage2=hd:LABEL=${VOLID} inst.profile=blue-fox-os inst.keymap=ca inst.lang=fr_CA.UTF-8 rd.live.check quiet
EOF
    if [ -n "${OLD_VOLID:-}" ] && [ "${OLD_VOLID}" != "${VOLID}" ]; then
        sed -i "s|LABEL=${OLD_VOLID}|LABEL=${VOLID}|g" "${TMPDIR}/isolinux.cfg"
    fi
fi

# 3. Repack the ISO: replace volume label, drop in GRUB theme files, replace
# grub.cfg + isolinux.cfg, preserve the original boot signature (UEFI El Torito
# + BIOS isohybrid MBR) via -boot_image any replay.
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
if [ "${PATCH_ISOLINUX}" = "1" ]; then
    XORRISO_ARGS+=(-map "${TMPDIR}/isolinux.cfg" "/isolinux/isolinux.cfg")
fi

xorriso "${XORRISO_ARGS[@]}"

# 4. Atomic swap.
mv "${ISO_OUT}" "${ISO}"
SIZE=$(du -h "${ISO}" | cut -f1)
echo "[brand-iso] OK ${ISO} (${SIZE})"

# 5. Anaconda installer GUI chrome (BF #22417/#22418 + title half of #22419).
# Overlays branding/anaconda/* into the stage2 squashfs of the ISO. Skips
# silently when no assets are present, so this is a no-op on a fresh
# checkout until the sidebar pixmaps land alongside the text-only configs
# that ship with this script.
"${WORKDIR}/scripts/inject-anaconda-product.sh" "${ISO}"
