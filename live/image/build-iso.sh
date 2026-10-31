#!/bin/bash
# Fabrique l'ISO live de Blue Fox OS (#26870).
#
# A lancer dans le conteneur d'outils (podman, squashfs-tools-ng, xorriso,
# mtools, python3-libselinux), privilegie :
#
#   docker run --rm --privileged \
#     -v <stockage podman>:/var/lib/containers \
#     -v <depot>:/src:ro -v <sortie>:/out \
#     bfos-iso-outils:43 bash /src/live/image/build-iso.sh
#
# Demarrage UEFI seulement (shim et GRUB signes de Fedora : le demarrage
# securise passe tel quel). Racine en squashfs, superposition en memoire vive.
set -euo pipefail

ETIQUETTE=${ETIQUETTE:-BFOS_LIVE}
IMAGE=${IMAGE:-localhost/blue-fox-os-live:dev}
SORTIE=${SORTIE:-/out}
NOM=${NOM:-blue-fox-os-live.iso}
TRAVAIL=$SORTIE/travail
RACINE=$TRAVAIL/iso

echo "== construction de l'image $IMAGE"
podman build --pull=missing -t "$IMAGE" -f /src/live/image/Containerfile /src

rm -rf "$TRAVAIL"
mkdir -p "$RACINE/LiveOS" "$RACINE/images/pxeboot" "$RACINE/EFI/BOOT" "$TRAVAIL/fc"
CTR=$(podman create "$IMAGE" /bin/true)
trap 'podman rm -f "$CTR" >/dev/null 2>&1 || true' EXIT

KVER=$(podman run --rm --entrypoint "" "$IMAGE" ls /usr/lib/modules)
echo "== noyau $KVER"
podman cp "$CTR:/usr/lib/modules/$KVER/vmlinuz" "$RACINE/images/pxeboot/vmlinuz"
podman cp "$CTR:/usr/lib/modules/$KVER/initramfs.img" "$RACINE/images/pxeboot/initrd.img"
podman cp "$CTR:/etc/selinux/targeted/contexts/files/." "$TRAVAIL/fc/"
podman cp "$CTR:/usr/lib/efi" "$TRAVAIL/efi"

SHIM=$(find "$TRAVAIL/efi/shim" -name shimx64.efi | head -1)
MM=$(find "$TRAVAIL/efi/shim" -name mmx64.efi | head -1)
GRUB=$(find "$TRAVAIL/efi/grub2" -name grubx64.efi | head -1)
[ -n "$SHIM" ] && [ -n "$GRUB" ] || { echo "shim ou grub introuvable"; exit 1; }
cp "$SHIM" "$RACINE/EFI/BOOT/BOOTX64.EFI"
cp "$GRUB" "$RACINE/EFI/BOOT/grubx64.efi"
[ -n "$MM" ] && cp "$MM" "$RACINE/EFI/BOOT/mmx64.efi"

CMDLINE="root=live:CDLABEL=$ETIQUETTE rd.live.image rd.live.overlay.overlayfs=1 quiet rhgb"
cat > "$RACINE/EFI/BOOT/grub.cfg" <<EOF
set default=0
set timeout=5
search --no-floppy --set=root -l '$ETIQUETTE'
menuentry 'Blue Fox OS (clé live)' --class fedora {
    linux /images/pxeboot/vmlinuz $CMDLINE
    initrd /images/pxeboot/initrd.img
}
menuentry 'Blue Fox OS (clé live, mode de dépannage)' --class fedora {
    linux /images/pxeboot/vmlinuz root=live:CDLABEL=$ETIQUETTE rd.live.image rd.live.overlay.overlayfs=1 nomodeset systemd.log_level=info
    initrd /images/pxeboot/initrd.img
}
EOF
mkdir -p "$RACINE/EFI/fedora"
cp "$RACINE/EFI/BOOT/grub.cfg" "$RACINE/EFI/fedora/grub.cfg"

echo "== releve des liens physiques (objets ostree)"
podman export "$CTR" | python3 /src/live/image/etiqueter.py --relever-liens "$TRAVAIL/liens.json"

echo "== racine en squashfs (etiquettes SELinux au vol)"
podman export "$CTR" \
  | python3 /src/live/image/etiqueter.py "$TRAVAIL/fc/file_contexts" "$TRAVAIL/liens.json" \
  | tar2sqfs --quiet --compressor zstd --num-jobs "$(nproc)" --force "$RACINE/LiveOS/squashfs.img"
ls -la "$RACINE/LiveOS/squashfs.img"

echo "== controle des etiquettes"
for temoin in /usr/bin/bash:shell_exec_t /usr/lib/systemd/systemd:init_exec_t \
              /etc/shadow:shadow_t /usr/libexec/bfos-live/pam-helper:bfos_live_helper_exec_t \
              /usr/lib64/security/pam_bfos.so:lib_t /usr/bin/greetd:xdm_exec_t; do
    f=${temoin%%:*}; attendu=${temoin##*:}
    lu=$(rdsquashfs -x "$f" "$RACINE/LiveOS/squashfs.img" | sed -n 's/^security.selinux=//p')
    echo "$f : $lu"
    case "$lu" in *":$attendu:"*) ;; *) echo "ETIQUETTE FAUSSE pour $f (attendu $attendu)"; exit 1;; esac
done

echo "== partition EFI"
EFIIMG=$TRAVAIL/efiboot.img
TAILLE_KO=$(( $(du -sk "$RACINE/EFI" | cut -f1) + 2048 ))
mkfs.vfat -n EFIBOOT -C "$EFIIMG" "$TAILLE_KO" >/dev/null
mmd -i "$EFIIMG" ::/EFI ::/EFI/BOOT ::/EFI/fedora
mcopy -i "$EFIIMG" "$RACINE"/EFI/BOOT/* ::/EFI/BOOT/
mcopy -i "$EFIIMG" "$RACINE"/EFI/fedora/grub.cfg ::/EFI/fedora/

echo "== ISO"
xorriso -as mkisofs -iso-level 3 -r -J -joliet-long -V "$ETIQUETTE" \
  -append_partition 2 0xef "$EFIIMG" \
  -e --interval:appended_partition_2:all:: -no-emul-boot \
  -partition_offset 16 -appended_part_as_gpt \
  -o "$SORTIE/$NOM" "$RACINE"

sha256sum "$SORTIE/$NOM" | tee "$SORTIE/$NOM.sha256"
podman image inspect "$IMAGE" --format '{{.Id}}' > "$SORTIE/$NOM.image-id"
rm -rf "$TRAVAIL"
ls -la "$SORTIE"
