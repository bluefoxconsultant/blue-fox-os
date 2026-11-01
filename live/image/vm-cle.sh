#!/bin/bash
# Démarre l'ISO de la clé live (#26870) en VM, branchée comme une clé USB.
#
#   bash vm-cle.sh <iso>                 # VNC sur 127.0.0.1:5902
#   env AFFICHAGE=gtk bash vm-cle.sh <iso>
#
# (fish : `env VAR=valeur bash ...`, jamais `VAR=valeur bash ...`.)
#
# La « clé » est un disque qcow2 adossé à l'ISO : tout ce que le système
# écrirait sur la clé tombe dans le qcow2, l'ISO reste intacte. Après
# l'extinction, le script compare les deux. Rien d'écrit = la racine en
# lecture seule et la superposition en mémoire vive tiennent leur promesse.
#
# Pendant la VM, une capture d'écran :
#   echo "screendump /tmp/x.ppm" | socat - UNIX-CONNECT:<dossier>/moniteur.sock
#
# UEFI sans Secure Boot : le banc n'a pas de profil OVMF avec les clés de
# Microsoft enrôlées (shim de Fedora). Le Secure Boot reste à éprouver sur une
# vraie machine.
set -euo pipefail

ISO=$(realpath "${1:?usage : vm-cle.sh <iso>}")
W=${W:-$HOME/bfos-live/vm}
MEM=${MEM:-8192}
CPU=${CPU:-4}
AFFICHAGE=${AFFICHAGE:-vnc}

log() { printf '\033[1;34m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m!! %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "pas en root : le banc appartient à l'usager."
[ -r "$ISO" ] || die "ISO illisible : $ISO"
[ -w /dev/kvm ] || die "/dev/kvm inaccessible."

OVMF_CODE=""
for c in /usr/share/edk2/x64/OVMF_CODE.4m.fd /usr/share/OVMF/OVMF_CODE_4M.fd \
         /usr/share/edk2/ovmf/OVMF_CODE.fd; do
    [ -f "$c" ] && { OVMF_CODE=$c; break; }
done
[ -n "$OVMF_CODE" ] || die "OVMF introuvable (paquet edk2-ovmf)."
OVMF_VARS_GABARIT=$(dirname "$OVMF_CODE")/$(basename "$OVMF_CODE" | sed 's/CODE/VARS/')

mkdir -p "$W"
CLE=$W/cle.qcow2
MONITEUR=$W/moniteur.sock
# Une clé neuve et une NVRAM neuve à chaque lancement : c'est le PC
# inconnu sur lequel on branche une clé qui n'a jamais servi.
rm -f "$CLE" "$MONITEUR"
cp "$OVMF_VARS_GABARIT" "$W/OVMF_VARS.fd"
# 16 Gio : l'ISO puis de l'espace libre, ou le profil persistant (#26870,
# 2026-10-23) cree sa partition. Sans espace libre, « garder mon bureau »
# repond « pas assez de place ».
qemu-img create -q -f qcow2 -b "$ISO" -F raw "$CLE" 16G

case "$AFFICHAGE" in
    vnc) AFF=(-display none -vnc 127.0.0.1:2)
         log "VNC sur 127.0.0.1:5902 (depuis ton poste : ssh -L 5902:localhost:5902 <hote-des-essais>)";;
    gtk) AFF=(-display gtk);;
    *)   die "AFFICHAGE inconnu : $AFFICHAGE (gtk ou vnc)";;
esac

log "ISO : $ISO"
log "Moniteur : $MONITEUR"
qemu-system-x86_64 \
    -enable-kvm -m "$MEM" -smp "$CPU" -cpu host -machine q35 \
    -drive if=pflash,format=raw,unit=0,readonly=on,file="$OVMF_CODE" \
    -drive if=pflash,format=raw,unit=1,file="$W/OVMF_VARS.fd" \
    -device qemu-xhci,id=xhci \
    -drive if=none,id=cle,format=qcow2,file="$CLE" \
    -device usb-storage,bus=xhci.0,drive=cle,removable=on,bootindex=0 \
    -netdev user,id=net0 -device virtio-net,netdev=net0 \
    -device virtio-vga \
    -monitor "unix:$MONITEUR,server,nowait" \
    "${AFF[@]}" \
    -name "Blue Fox OS — clé live" || true

log "VM arrêtée. La clé a-t-elle reçu des écritures ?"
# Session ephemere : la cle doit etre restee identique a l'ISO. Session
# gardee : la table de partitions et la partition de profils ont change, c'est
# attendu.
if qemu-img compare -q "$CLE" "$ISO"; then
    printf '\033[1;32mOK : la clé est identique à l\047ISO, rien n\047a été écrit.\033[0m\n'
else
    printf '\033[1;33mÉCRIT : la clé a changé (attendu après « garder mon bureau » ; sinon voir qemu-img map %s).\033[0m\n' "$CLE"
fi
