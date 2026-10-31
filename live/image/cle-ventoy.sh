#!/bin/bash
# Clé Ventoy d'ESSAI pour vm-ventoy.sh (#26870, 2026-10-24) : une image disque
# faite par Ventoy2Disk.sh, l'ISO copiée dans sa partition exFAT, et un greffon
# « injection » qui ouvre un shell root sur le 2e port série de la VM.
#
# Pourquoi l'injection : sous Ventoy en UEFI, ni « conf_replace » ni une ligne
# de noyau modifiée dans GRUB n'atteignent le noyau (essayé : Plymouth reste,
# pas de console série). L'archive injectée est extraite dans l'initramfs ; son
# crochet dracut (pre-pivot) active debug-shell.service sur ttyS1 par /run.
#
# À lancer en root dans un conteneur privilégié (losetup, mount exfat, qemu-img) :
#   bash cle-ventoy.sh <cle-ventoy-vierge.img> <iso> <sortie.qcow2>
# La clé vierge vient de « Ventoy2Disk.sh -i » sur un fichier de 16 Go (MBR,
# partition 1 exFAT « Ventoy », partition 2 VTOYEFI).
set -euo pipefail
VIERGE=$1; ISO=$2; SORTIE=$3
T=$(mktemp -d); trap 'umount "$T/m" 2>/dev/null || true; [ -n "${L:-}" ] && losetup -d "$L" 2>/dev/null || true; rm -rf "$T"' EXIT
cp --sparse=always "$VIERGE" "$T/cle.img"
DEBUT=$(sfdisk -J "$T/cle.img" | python3 -c 'import json,sys; p=json.load(sys.stdin)["partitiontable"]["partitions"][0]; print(p["start"], p["size"])')
read -r S N <<<"$DEBUT"
L=$(losetup -f --show -o $((S * 512)) --sizelimit $((N * 512)) "$T/cle.img")
mkdir -p "$T/m" "$T/inj/var/lib/dracut/hooks/pre-pivot"
mount -t exfat "$L" "$T/m"
cp "$ISO" "$T/m/bfos.iso"
mkdir -p "$T/m/ventoy"
cat > "$T/inj/var/lib/dracut/hooks/pre-pivot/99-bfos-essai.sh" <<'FIN'
d=/run/systemd/system
mkdir -p $d/debug-shell.service.d $d/sysinit.target.wants
printf '[Service]\nTTYPath=/dev/ttyS1\n' > $d/debug-shell.service.d/tty.conf
ln -sf /usr/lib/systemd/system/debug-shell.service $d/sysinit.target.wants/debug-shell.service
FIN
chmod 0755 "$T/inj/var/lib/dracut/hooks/pre-pivot/99-bfos-essai.sh"
tar -C "$T/inj" --owner=0 --group=0 -czf "$T/m/ventoy/essai-shell.tar.gz" var/lib/dracut/hooks/pre-pivot/99-bfos-essai.sh
cat > "$T/m/ventoy/ventoy.json" <<'FIN'
{
  "control": [
    {"VTOY_DEFAULT_IMAGE": "/bfos.iso"},
    {"VTOY_MENU_TIMEOUT": "2"},
    {"VTOY_SECONDARY_BOOT_MENU": "0"}
  ],
  "injection": [
    {"image": "/bfos.iso", "archive": "/ventoy/essai-shell.tar.gz"}
  ]
}
FIN
sync; umount "$T/m"; losetup -d "$L"; L=
qemu-img convert -O qcow2 "$T/cle.img" "$SORTIE"
ls -la "$SORTIE"
