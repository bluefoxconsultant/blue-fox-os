#!/bin/bash
# Banc de TOURNAGE de la clé live (#26870, vidéo « Blue Fox OS sur clé USB ») :
# la VM de vm-cle.sh, mais accélérée par le GPU de l'hôte et sans fenêtre.
#
#   bash vm-tournage.sh <iso>
#   env RENDU=nvidia bash vm-tournage.sh <iso>     # la RTX au lieu de l'Intel
#   env GARDER_CLE=1 bash vm-tournage.sh <iso>     # même clé, autre « PC »
#
# Affichage : virtio-vga-gl (virgl) rendu par EGL sur le nœud de rendu de
# l'hôte, sans écran (egl-headless). ⚠️ screendump rend « no surface » avec un
# affichage GL : on filme par le VNC local (127.0.0.1:5903, que egl-headless
# alimente) et on pilote clavier et souris par QMP (input-send-event) ou VNC.
# Rien à l'écran de l'hôte, rien à y faire à la main.
#
# Dossier à part (~/bfos-live/vm-tournage) : le banc d'essai vm-cle.sh n'est
# pas touché. Clé et NVRAM neuves à chaque lancement, comme vm-cle.sh.
set -euo pipefail

ISO=$(realpath "${1:?usage : vm-tournage.sh <iso>}")
W=${W:-$HOME/bfos-live/vm-tournage}
MEM=${MEM:-8192}
CPU=${CPU:-4}
case "${RENDU:-intel}" in
    intel)  NOEUD=/dev/dri/by-path/pci-0000:00:02.0-render;;
    nvidia) NOEUD=/dev/dri/by-path/pci-0000:f3:00.0-render;;
    *)      echo "RENDU inconnu : ${RENDU} (intel ou nvidia)" >&2; exit 1;;
esac

[ "$(id -u)" -ne 0 ] || { echo "pas en root" >&2; exit 1; }
[ -r "$ISO" ] && [ -w /dev/kvm ] && [ -r "$(realpath "$NOEUD")" ] \
    || { echo "ISO, /dev/kvm ou $NOEUD inaccessible" >&2; exit 1; }

OVMF_CODE=/usr/share/edk2/x64/OVMF_CODE.4m.fd
OVMF_VARS_GABARIT=/usr/share/edk2/x64/OVMF_VARS.4m.fd
mkdir -p "$W"
rm -f "$W/moniteur.sock" "$W/qmp.sock"
# NVRAM neuve à chaque lancement (un PC inconnu). GARDER_CLE=1 garde la clé
# d'avant : c'est le plan « un autre PC, la même clé ».
cp "$OVMF_VARS_GABARIT" "$W/OVMF_VARS.fd"
if [ "${GARDER_CLE:-0}" = 1 ] && [ -f "$W/cle.qcow2" ]; then
    echo "clé gardée : $W/cle.qcow2"
else
    rm -f "$W/cle.qcow2"
    qemu-img create -q -f qcow2 -b "$ISO" -F raw "$W/cle.qcow2" 16G
fi

exec qemu-system-x86_64 \
    -enable-kvm -m "$MEM" -smp "$CPU" -cpu host -machine q35 \
    -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,unit=1,file="$W/OVMF_VARS.fd" \
    -device qemu-xhci,id=xhci \
    -drive if=none,id=cle,format=qcow2,file="$W/cle.qcow2" \
    -device usb-storage,bus=xhci.0,drive=cle,removable=on,bootindex=0 \
    -device usb-tablet,bus=xhci.0 \
    -netdev user,id=net0 -device virtio-net,netdev=net0 \
    -device virtio-vga-gl,xres=1920,yres=1080 \
    -display egl-headless,rendernode="$(realpath "$NOEUD")" \
    -vnc 127.0.0.1:3 \
    -monitor "unix:$W/moniteur.sock,server,nowait" \
    -qmp "unix:$W/qmp.sock,server,nowait" \
    -name "Blue Fox OS — tournage"
