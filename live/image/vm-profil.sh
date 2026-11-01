#!/bin/bash
# Banc du profil persistant de la clé live (#26870, 2026-10-23) : démarre l'ISO
# en VM sur une « clé » de 16 Gio (l'ISO, puis de l'espace libre), avec un
# shell root sur un 2e port série, pour éprouver SANS QR la partition de
# profils, le fichier LUKS, le montage sur /var/home et la survie au
# redémarrage.
#   bash vm-profil.sh <iso>            clé neuve (efface la précédente)
#   bash vm-profil.sh <iso> --garder   même clé : redémarrage, le profil doit y être
# Shell : python3 serie.py vm-profil/shell.sock (socket UNIX du 2e port série).
# Arrêt propre : echo system_powerdown | socat - UNIX:vm-profil/moniteur.sock
set -euo pipefail
ISO=$(realpath "$1"); GARDER=${2:-}
W=$HOME/bfos-live/vm-profil; mkdir -p "$W"; cd "$W"
rm -f console.log shell.sock moniteur.sock
if [ "$GARDER" != "--garder" ] || [ ! -f cle.qcow2 ]; then
  rm -f cle.qcow2
  qemu-img create -q -f qcow2 -b "$ISO" -F raw cle.qcow2 16G
fi
xorriso -osirrox on -indev "$ISO" -extract /images/pxeboot/vmlinuz vmlinuz -extract /images/pxeboot/initrd.img initrd.img >/dev/null 2>&1
exec qemu-system-x86_64 -enable-kvm -m 6144 -smp 4 -cpu host -machine q35 \
  -kernel vmlinuz -initrd initrd.img \
  -append "root=live:CDLABEL=BFOS_LIVE rd.live.image rd.live.overlay.overlayfs=1 console=ttyS0 systemd.debug_shell=/dev/ttyS1" \
  -device qemu-xhci,id=xhci -drive if=none,id=cle,format=qcow2,file=cle.qcow2 \
  -device usb-storage,bus=xhci.0,drive=cle,removable=on \
  -netdev user,id=n0 -device virtio-net,netdev=n0 -device virtio-vga \
  -serial file:console.log -serial unix:shell.sock,server,nowait \
  -monitor unix:moniteur.sock,server,nowait -display none
