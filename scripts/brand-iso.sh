#!/usr/bin/env bash
# scripts/brand-iso.sh — brande le menu de demarrage d'une ISO produite par
# bootc-image-builder, et n'en casse pas l'amorçage.
#
# Usage: ./scripts/brand-iso.sh path/to/install.iso
#        L'ISO est remplacee sur place (echange atomique).
#
# ─────────────────────────────────────────────────────────────────────────────
# CE QU'IL FAUT SAVOIR AVANT DE TOUCHER A CE SCRIPT (BF #23739, 2026-07-19)
#
# 1. UNE ISO ANACONDA EMBARQUE **TROIS** MENUS GRUB, PAS UN :
#
#      /boot/grub2/grub.cfg          -> demarrage BIOS. GNOME Boxes,
#                                       VirtualBox et beaucoup de machines
#                                       demarrent en BIOS PAR DEFAUT : c'est
#                                       tres souvent CE menu que l'on voit.
#      grub.cfg DANS /images/efiboot.img  -> demarrage UEFI. C'est une image
#                                       FAT (El Torito) : elle ne s'edite
#                                       qu'avec mtools, pas avec xorriso -map.
#      /EFI/BOOT/grub.cfg            -> copie dans l'arborescence ISO9660.
#                                       En pratique aucun micrologiciel ne la
#                                       lit. On la garde synchronisee.
#
#    Ne patcher qu'un seul des trois donne un branding invisible et fait
#    conclure a tort que le correctif n'a pas pris.
#
# 2. NE JAMAIS RENOMMER LE VOLUME. L'ancienne version de ce script posait
#    `-volid BLUE_FOX_OS_INST`. Le volid est une propriete GLOBALE de l'image :
#    le renommer invalide d'un coup tous les `inst.stage2=hd:LABEL=...` et
#    `inst.ks=hd:LABEL=...` des menus qu'on n'a pas reecrits. Symptomes vecus :
#      - gel ~3 min sur dracut-initqueue (« still waiting for /dev/root »),
#        puis « missing inst.stage2 or inst.repo » ;
#      - le menu affiche reste Fedora (c'etait le menu d'origine) ;
#      - aucun `inst.profile` -> chrome Anaconda/Barracuda inactif.
#    Le volid n'est que cosmetique (nom du lecteur). L'amorçage, lui, depend
#    de l'accord entre le volid et chaque menu.
#
# 3. ON PATCHE LES MENUS D'ORIGINE, ON N'EN GENERE PAS. L'ancienne version
#    fabriquait un grub.cfg de zero ; il n'aurait jamais demarre meme bien
#    place : il utilisait `linuxefi`/`initrdefi` la ou Fedora 44 emploie
#    `linux`/`initrd`, et omettait le `search --no-floppy --set=root -l ...`
#    sans lequel $root est indefini et le noyau introuvable. Ici, tout ce qui
#    est structurel (preambule, search, noms de commandes, jetons inst.stage2
#    et inst.ks) vient du fichier d'origine, qui fonctionne.
#
# 4. `xorriso` ECRIT SES INFORMATIONS SUR STDERR. Un `-toc 2>/dev/null | grep`
#    retourne toujours vide — c'est ce qui rendait muette l'ancienne
#    reecriture d'etiquettes. Utiliser 2>&1.
#
# Ce que le branding apporte concretement :
#   - titres de menu en français ;
#   - inst.profile=blue-fox-os  -> chrome Anaconda (sidebar Barracuda, CSS,
#     titre). Sans lui les assets sont dans le squashfs mais dorment, car
#     l'auto-detection (`os_id`) ne peut pas matcher : l'image garde ID=fedora ;
#   - inst.keymap=ca + inst.lang=fr_CA.UTF-8 (#22419).
#
# Le thème GRUB (couleurs, barre de selection) n'est PAS injecte : sa
# resolution de chemin depuis efiboot.img n'est pas fiable, et un menu qui ne
# s'affiche pas coute plus cher qu'un menu aux couleurs d'origine. Les assets
# restent dans branding/grub-theme/ pour quand ce sera valide.

set -euo pipefail

ISO="${1:?usage: $0 path/to/install.iso}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"

# Titres volontairement SANS accents : la console GRUB en mode texte ne rend
# pas les caracteres non-ASCII de façon fiable selon le micrologiciel.
BF_PARAMS="inst.profile=blue-fox-os inst.keymap=ca inst.lang=fr_CA.UTF-8"
ZEROTOUCH_DEFAULT_DOMAIN="bluefoxconsultant.com"

log()  { echo "[brand-iso] $*"; }
die()  { echo "[brand-iso] ERREUR: $*" >&2; exit 1; }

[ -f "${ISO}" ] || die "${ISO} introuvable"

command -v xorriso >/dev/null 2>&1 || die "xorriso introuvable.
    Arch/Garuda : sudo pacman -S --needed libisoburn   (xorriso y est fourni)
    Fedora      : sudo dnf install -y xorriso
    Debian      : sudo apt install -y xorriso"
command -v mcopy >/dev/null 2>&1 || die "mcopy (mtools) introuvable — requis pour
    editer le grub.cfg a l'interieur de /images/efiboot.img.
    Arch/Garuda : sudo pacman -S --needed mtools"

TMPDIR="$(mktemp -d)"
trap 'chmod -R u+w "${TMPDIR}" 2>/dev/null; rm -rf "${TMPDIR}"' EXIT

# Volume id : on le lit pour verifier, jamais pour le changer.
read_volid() {
    xorriso -indev "$1" -toc 2>&1 \
        | sed -n "s/^[[:space:]]*Volume id[[:space:]]*:[[:space:]]*'\{0,1\}//p" \
        | head -1 | sed "s/'[[:space:]]*$//"
}

ISO_VOLID="$(read_volid "${ISO}")"
[ -n "${ISO_VOLID}" ] || die "impossible de lire le volume id de ${ISO}"
log "iso=${ISO}"
log "volume id (preserve) = '${ISO_VOLID}'"

# ── transformation d'un menu ────────────────────────────────────────────────
# Applique aux entrees EXISTANTES : ajout des parametres BF + retitrage.
# Le numero de version Fedora est capture par regex pour survivre a un F45.
patch_menu() {
    local cfg="$1"

    # ⚠️ Idempotent (#23940, 2026-09-01). Sans ce retrait prealable, un second
    # passage du branding ajoutait BF_PARAMS une deuxieme fois et la ligne linux
    # finissait avec inst.profile / inst.keymap / inst.lang en DOUBLE. Valeurs
    # identiques, donc sans consequence au demarrage — mais ca s'empile a chaque
    # passage, et rejouer le branding sur une ISO deja brandee est justement ce
    # qu'on fait pour corriger un menu sans refaire 28 min de build.
    local p k
    for p in ${BF_PARAMS}; do
        k="${p%%=*}"
        sed -i -E "/^[[:space:]]*linux(efi)?[[:space:]]+\/images\/pxeboot\/vmlinuz/ s|[[:space:]]+${k}=[^[:space:]]*||g" "$cfg"
    done

    sed -i -E "s|^([[:space:]]*linux(efi)?[[:space:]]+/images/pxeboot/vmlinuz[[:space:]].*)\$|\1 ${BF_PARAMS}|" "$cfg"
    sed -i -E "s|menuentry 'Install Fedora Linux [0-9]+ in basic graphics mode'|menuentry 'Installer Blue Fox OS (mode graphique de base)'|" "$cfg"
    sed -i -E "s|menuentry 'Test this media \& install Fedora Linux [0-9]+'|menuentry 'Tester le support \\& installer Blue Fox OS'|" "$cfg"
    sed -i -E "s|menuentry 'Install Fedora Linux [0-9]+'|menuentry 'Installer Blue Fox OS'|" "$cfg"
    sed -i -E "s|menuentry 'Rescue a Fedora Linux system'|menuentry 'Depanner un systeme Blue Fox OS'|" "$cfg"
    sed -i -E "s|menuentry 'Boot first drive'|menuentry 'Demarrer sur le premier disque'|" "$cfg"
    sed -i -E "s|submenu 'Troubleshooting -->'|submenu 'Depannage -->'|" "$cfg"
}

# Entree zero-touch (BFOSD10) : elle va chercher le kickstart servi par
# bf_zerotouch_install. On la CONSTRUIT A PARTIR des lignes linux/initrd de la
# premiere entree d'origine — jamais de zero — pour heriter du bon nom de
# commande et du bon inst.stage2.
#
# ⚠️ AUCUNE INVITE : le domaine est pose en dur (#23940, 2026-09-01)
# ------------------------------------------------------------------
# Cette entree a longtemps demande le domaine par `read bf_domain`. Elle n'a
# JAMAIS pu demarrer en UEFI, donc sur aucune vraie machine :
#
#   Domaine : error: ../../grub-core/script/function.c:
#   grub_script_function_find:119: can't find command `read'.
#
# `read` vit dans read.mod, et l'ISO ne porte de modules que pour i386-pc : zero
# fichier sous boot/grub2/x86_64-efi/. Le grub UEFI est le binaire monolithique
# de efiboot.img, avec un jeu de modules fige et pas de `read` dedans — il n'y a
# donc rien a insmod. L'erreur AVORTE le menuentry : la ligne `linux` n'est
# jamais atteinte, et le repli `if [ -z ... ]` non plus.
#
# En BIOS ca passait, read.mod etant sur le media. D'ou un defaut invisible tant
# que personne n'avait demarre l'entree pour de vrai.
#
# Pointer une autre organisation reste possible : « e » au menu, editer la ligne
# set bf_domain, Ctrl-X. C'est le seul cas ou la saisie servait.
prepend_zerotouch_entry() {
    local cfg="$1"
    local linux_line initrd_line cmd

    # Idempotence : un second passage sur une ISO deja brandee prendrait la
    # ligne linux de l'entree zero-touch elle-meme comme modele, et empilerait
    # un doublon portant deja ${bf_domain}. On retire donc l'entree existante
    # avant de reconstruire, et on choisit un modele qui n'est pas elle.
    if grep -q "menuentry 'Installer Blue Fox OS (zero-touch)'" "$cfg"; then
        awk '
            /^menuentry .Installer Blue Fox OS \(zero-touch\)./ { skip = 1; next }
            skip && /^\}/                                       { skip = 0; next }
            !skip                                               { print }
        ' "$cfg" > "${cfg}.sanszt" && mv "${cfg}.sanszt" "$cfg"
        log "  entree zero-touch precedente retiree (reconstruction)"
    fi

    linux_line="$(grep -m1 -E '^[[:space:]]*linux(efi)?[[:space:]]+/images/pxeboot/vmlinuz' "$cfg" \
        | grep -v 'bf_domain' || true)"
    initrd_line="$(grep -m1 -E '^[[:space:]]*initrd(efi)?[[:space:]]+' "$cfg" || true)"
    if [ -z "${linux_line}" ] || [ -z "${initrd_line}" ]; then
        log "  pas d'entree modele exploitable — zero-touch non ajoutee"
        return 0
    fi
    cmd="$(echo "${linux_line}" | awk '{print $1}')"

    # Remplace le kickstart embarque par celui servi par le domaine saisi.
    local zt_linux
    zt_linux="$(echo "${linux_line}" \
        | sed -E "s|inst\.ks=[^[:space:]]+|inst.ks=https://\\\${bf_domain}/blue-fox-install.ks|")"
    # Si l'entree modele n'avait pas d'inst.ks, on l'ajoute.
    echo "${zt_linux}" | grep -q 'inst\.ks=' \
        || zt_linux="${zt_linux} inst.ks=https://\${bf_domain}/blue-fox-install.ks"
    # Le zero-touch a besoin du reseau.
    echo "${zt_linux}" | grep -q '(^| )ip=' || zt_linux="${zt_linux} ip=dhcp"

    {
        printf "menuentry 'Installer Blue Fox OS (zero-touch)' --class fedora --class gnu-linux {\n"
        printf '    # Domaine en dur : `read` n%s existe pas dans le grub UEFI.\n' "'"
        printf '    # Pour une autre organisation : « e » ici, editer, Ctrl-X.\n'
        printf '    set bf_domain="%s"\n' "${ZEROTOUCH_DEFAULT_DOMAIN}"
        printf '%s\n' "${zt_linux}"
        printf '%s\n' "${initrd_line}"
        printf "}\n\n"
        cat "$cfg"
    } > "${cfg}.zt"
    mv "${cfg}.zt" "$cfg"
    log "  entree zero-touch ajoutee (commande ${cmd}, heritee de l'entree d'origine)"
}

# ── 1. menu BIOS ────────────────────────────────────────────────────────────
log "1/3 menu BIOS  /boot/grub2/grub.cfg"
MAPS=()
if xorriso -osirrox on -indev "${ISO}" -extract /boot/grub2/grub.cfg \
        "${TMPDIR}/bios.cfg" >/dev/null 2>&1 && [ -f "${TMPDIR}/bios.cfg" ]; then
    patch_menu "${TMPDIR}/bios.cfg"
    prepend_zerotouch_entry "${TMPDIR}/bios.cfg"
    MAPS+=(-map "${TMPDIR}/bios.cfg" /boot/grub2/grub.cfg)
    log "  ok"
else
    log "  absent — ISO sans voie BIOS, on continue"
fi

# ── 2. menu UEFI, dans efiboot.img ──────────────────────────────────────────
log "2/3 menu UEFI  grub.cfg dans /images/efiboot.img"
if xorriso -osirrox on -indev "${ISO}" -extract /images/efiboot.img \
        "${TMPDIR}/efiboot.img" >/dev/null 2>&1 && [ -f "${TMPDIR}/efiboot.img" ]; then
    mcopy -i "${TMPDIR}/efiboot.img" ::/EFI/BOOT/grub.cfg "${TMPDIR}/efi.cfg" 2>/dev/null \
        || die "grub.cfg introuvable dans efiboot.img"
    patch_menu "${TMPDIR}/efi.cfg"
    prepend_zerotouch_entry "${TMPDIR}/efi.cfg"
    mcopy -o -i "${TMPDIR}/efiboot.img" "${TMPDIR}/efi.cfg" ::/EFI/BOOT/grub.cfg
    # Relire pour confirmer que l'ecriture dans le FAT a bien pris.
    mcopy -i "${TMPDIR}/efiboot.img" ::/EFI/BOOT/grub.cfg "${TMPDIR}/efi.verify" 2>/dev/null
    cmp -s "${TMPDIR}/efi.cfg" "${TMPDIR}/efi.verify" \
        || die "la reinjection dans efiboot.img n'a pas pris"
    MAPS+=(-map "${TMPDIR}/efiboot.img" /images/efiboot.img)
    # 3. La copie ISO9660 reçoit le meme contenu, pour qu'aucune des trois
    #    ne diverge si un micrologiciel exotique la lit.
    MAPS+=(-map "${TMPDIR}/efi.cfg" /EFI/BOOT/grub.cfg)
    log "  ok (+ copie ISO9660 synchronisee)"
else
    log "  pas de /images/efiboot.img — ISO non UEFI ?"
fi

[ "${#MAPS[@]}" -gt 0 ] || die "aucun menu trouve dans ${ISO} — rien a brander"

# ── repack ──────────────────────────────────────────────────────────────────
# `-boot_image any replay` preserve les signatures El Torito UEFI + MBR
# isohybrid BIOS. Pas de -volid : voir le point 2 de l'en-tete.
log "repack (volume id inchange)"
ISO_OUT="${ISO}.branded"
xorriso -indev "${ISO}" -outdev "${ISO_OUT}" -boot_image any replay "${MAPS[@]}"
mv "${ISO_OUT}" "${ISO}"
log "ok ${ISO} ($(du -h "${ISO}" | cut -f1))"

# ── chrome Anaconda (assets dans le squashfs stage2) ────────────────────────
"${WORKDIR}/scripts/inject-anaconda-product.sh" "${ISO}"

# ── verification ────────────────────────────────────────────────────────────
# L'echec rc1 etait muet a la construction et ne se voyait qu'apres 3 minutes
# de gel sur du vrai materiel. On refuse desormais de rendre une ISO dont un
# menu ne concorde pas avec le volume.
log "verification"
FINAL_VOLID="$(read_volid "${ISO}")"
[ "${FINAL_VOLID}" = "${ISO_VOLID}" ] \
    || die "le volume id a change pendant le repack : '${ISO_VOLID}' -> '${FINAL_VOLID}'"
log "  volume id preserve : '${FINAL_VOLID}'"

verify_menu() {   # $1 = etiquette humaine, $2 = fichier
    local name="$1" cfg="$2" bad=0 nlinux nprof
    while read -r label; do
        [ -n "${label}" ] || continue
        if [ "${label//\\x20/ }" != "${ISO_VOLID}" ]; then
            echo "[brand-iso]   ${name}: etiquette perimee LABEL=${label}" >&2
            bad=$((bad + 1))
        fi
    done < <(grep -oE 'LABEL=[^[:space:]:]+' "$cfg" | sed 's/^LABEL=//' | sort -u)
    [ "${bad}" -eq 0 ] || die "${name} : ${bad} etiquette(s) perimee(s) — c'est le mode
    d'echec de #23739 (gel ~3 min sur dracut-initqueue). ISO non livrable."
    nlinux=$(grep -cE '^[[:space:]]*linux(efi)?[[:space:]]+/images/pxeboot/vmlinuz' "$cfg" || true)
    nprof=$(grep -c 'inst.profile=blue-fox-os' "$cfg" || true)
    [ "${nlinux}" -eq "${nprof}" ] \
        || die "${name} : inst.profile sur ${nprof}/${nlinux} entree(s) — le chrome
    Anaconda resterait Fedora sur les autres."

    # ⚠️ Les commandes doivent EXISTER dans le grub qui lira ce menu (#23940).
    # Jusqu'au 2026-09-01 ce controle validait etiquettes et profil, puis
    # declarait « ISO livrable » un menu UEFI qui ne pouvait pas demarrer :
    # l'entree zero-touch appelait `read`, absent du grub UEFI, et le menuentry
    # avortait avant la ligne `linux`. Un feu vert sur ce qu'on a regarde ne dit
    # rien de ce qu'on n'a pas regarde.
    #
    # L'ISO ne porte de modules que pour i386-pc : en UEFI, rien a insmod. La
    # liste ci-dessous est donc celle des commandes qu'on a vues manquer, pas un
    # inventaire exhaustif du jeu integre.
    local absentes=0 c
    for c in read; do
        if grep -qE "^[[:space:]]*${c}[[:space:]]" "$cfg"; then
            echo "[brand-iso]   ${name}: commande '${c}' absente du grub UEFI" >&2
            absentes=$((absentes + 1))
        fi
    done
    if [ "${name}" = "UEFI " ] && [ "${absentes}" -gt 0 ]; then
        die "${name} : ${absentes} commande(s) indisponible(s) en UEFI. Le menuentry
    avorterait avant la ligne 'linux' — l'entree ne demarrerait sur aucune vraie
    machine. ISO non livrable."
    fi

    # Le zero-touch ne vaut que s'il resout un domaine : ${bf_domain} vide
    # donnerait inst.ks=https:///blue-fox-install.ks.
    if grep -q 'bf_domain' "$cfg" && ! grep -qE '^[[:space:]]*set bf_domain="[^"]+"' "$cfg"; then
        die "${name} : bf_domain est cite mais jamais pose a une valeur non vide.
    Le kickstart serait demande a https:///blue-fox-install.ks."
    fi

    log "  ${name} : ${nlinux} entree(s), etiquettes ok, inst.profile ok, commandes ok"
}

if xorriso -osirrox on -indev "${ISO}" -extract /boot/grub2/grub.cfg \
        "${TMPDIR}/v-bios.cfg" >/dev/null 2>&1 && [ -f "${TMPDIR}/v-bios.cfg" ]; then
    verify_menu "BIOS " "${TMPDIR}/v-bios.cfg"
fi
if xorriso -osirrox on -indev "${ISO}" -extract /images/efiboot.img \
        "${TMPDIR}/v-efi.img" >/dev/null 2>&1 && [ -f "${TMPDIR}/v-efi.img" ]; then
    mcopy -i "${TMPDIR}/v-efi.img" ::/EFI/BOOT/grub.cfg "${TMPDIR}/v-efi.cfg" 2>/dev/null \
        && verify_menu "UEFI " "${TMPDIR}/v-efi.cfg"
fi
log "verification passee — ISO livrable"
