#!/bin/bash
# Banc SELinux de la clé live (#26870) : démarre l'ISO en VM, noyau direct,
# avec un shell root sur un 2e port série, pour éprouver la création de
# compte depuis le domaine de greetd SANS approbation par QR.
#   bash vm-selinux.sh <iso>
# Shell : python3 serie.py vm-se/shell.sock (socket UNIX du 2e port série).
# ⚠️ Le shell de dépannage est en initrc_t : il ne peut ni `runcon` vers
# xdm_t ni appeler systemd-run. Pour agir « comme greetd », poser une unité
# dans /run/systemd/system avec SELinuxContext=system_u:system_r:xdm_t:s0-s0:c0.c1023
# et ExecStart= une copie de bash étiquetée xdm_exec_t. Tout script qui doit
# changer de domaine va HORS de /run (monté nosuid : nosuid_transition refusé).
set -euo pipefail
ISO=$(realpath "$1"); W=$HOME/bfos-live/vm-se; mkdir -p "$W"; cd "$W"
rm -f cle.qcow2 console.log shell.sock moniteur.sock
xorriso -osirrox on -indev "$ISO" -extract /images/pxeboot/vmlinuz vmlinuz -extract /images/pxeboot/initrd.img initrd.img >/dev/null 2>&1
qemu-img create -q -f qcow2 -b "$ISO" -F raw cle.qcow2
exec qemu-system-x86_64 -enable-kvm -m 6144 -smp 4 -cpu host -machine q35 \
  -kernel vmlinuz -initrd initrd.img \
  -append "root=live:CDLABEL=BFOS_LIVE rd.live.image rd.live.overlay.overlayfs=1 console=ttyS0 systemd.debug_shell=/dev/ttyS1" \
  -device qemu-xhci,id=xhci -drive if=none,id=cle,format=qcow2,file=cle.qcow2 \
  -device usb-storage,bus=xhci.0,drive=cle,removable=on \
  -netdev user,id=n0 -device virtio-net,netdev=n0 -device virtio-vga \
  -serial file:console.log -serial unix:shell.sock,server,nowait \
  -monitor unix:moniteur.sock,server,nowait -display none
