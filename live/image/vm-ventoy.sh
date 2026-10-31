#!/bin/bash
# Banc Ventoy de la clé live (#26870, 2026-10-24) : démarre une VRAIE clé
# Ventoy (image disque faite par Ventoy2Disk.sh, l'ISO copiée dans sa partition
# exFAT) en UEFI (OVMF), pour éprouver le profil persistant sous Ventoy.
# La clé vient de cle-ventoy.sh : son ventoy.json choisit l'ISO tout seul et
# injecte un shell root sur le 2e port série (pas de console noyau : Ventoy en
# UEFI ignore conf_replace et les lignes de noyau éditées dans GRUB).
#   bash vm-ventoy.sh <cle.qcow2|.raw>            clé neuve (couche jetable sur l'image)
#   bash vm-ventoy.sh <cle.qcow2|.raw> --garder   même clé : redémarrage
# Shell : python3 serie.py vm-ventoy/shell.sock ; écran : screendump au moniteur.
# Ventoy 1.1.18 projette l'ISO (/dev/ventoy, dm « linear ») ET toute la
# partition 1 (VentoyPart), dans l'initramfs, hors udev : lsblk n'en voit aucune.
set -euo pipefail
IMG=$(realpath "$1"); GARDER=${2:-}
W=$HOME/bfos-live/vm-ventoy; mkdir -p "$W"; cd "$W"
rm -f console.log shell.sock moniteur.sock
if [ "$GARDER" != "--garder" ] || [ ! -f cle.qcow2 ]; then
  rm -f cle.qcow2
  FMT=raw; case "$IMG" in *.qcow2) FMT=qcow2;; esac
  qemu-img create -q -f qcow2 -b "$IMG" -F "$FMT" cle.qcow2
  cp /usr/share/edk2/x64/OVMF_VARS.4m.fd vars.fd
fi
exec qemu-system-x86_64 -enable-kvm -m 6144 -smp 4 -cpu host -machine q35 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,format=raw,file=vars.fd \
  -device qemu-xhci,id=xhci -drive if=none,id=cle,format=qcow2,file=cle.qcow2 \
  -device usb-storage,bus=xhci.0,drive=cle,removable=on,bootindex=0 \
  -netdev user,id=n0 -device virtio-net,netdev=n0 -device virtio-vga \
  -serial file:console.log -serial unix:shell.sock,server,nowait \
  -monitor unix:moniteur.sock,server,nowait -display none
