#!/usr/bin/env bash
# scripts/sign-welcome-rpm.sh — signe un ou plusieurs RPM avec la cle GPG Blue
# Fox, puis VERIFIE que la signature est bien la.
#
# Usage : BLUEFOX_RPM_GPG_NAME="Blue Fox OS Package Signing <…>" \
#           ./scripts/sign-welcome-rpm.sh chemin/vers/*.rpm
#
# Variables :
#   BLUEFOX_RPM_GPG_NAME            uid ou empreinte de la cle (OBLIGATOIRE)
#   BLUEFOX_RPM_GPG_PASSPHRASE_FILE fichier de passphrase, si la cle en a une
#
# Toujours execute SUR L'HOTE, jamais dans le container de build du RPM : la cle
# privee ne franchit pas cette frontiere (#23811, #23815). C'est aussi pourquoi
# build-welcome-rpm.sh ne fait plus `exec podman run` — il a besoin de reprendre
# la main apres le build pour signer ici.
#
# La cle est cree une fois par scripts/generate-rpm-signing-key.sh.
set -euo pipefail

GPG_NAME="${BLUEFOX_RPM_GPG_NAME:-}"
PASSFILE="${BLUEFOX_RPM_GPG_PASSPHRASE_FILE:-}"

log() { echo "==> $*"; }
die() { echo "sign-welcome-rpm: $*" >&2; exit 1; }

[ $# -gt 0 ] || die "aucun RPM passe en argument"
[ -n "$GPG_NAME" ] || die "BLUEFOX_RPM_GPG_NAME non definie (uid ou empreinte de la cle de signature)"

command -v rpmsign >/dev/null 2>&1 \
    || die "rpmsign introuvable. Sur Garuda/Arch : sudo pacman -S --needed rpm-tools"
command -v gpg >/dev/null 2>&1 || die "gpg introuvable"

gpg --list-secret-keys "$GPG_NAME" >/dev/null 2>&1 \
    || die "aucune cle SECRETE pour « ${GPG_NAME} » dans ce trousseau.
Cette machine n'est peut-etre pas la machine de build — la cle privee ne vit que
la (#23815). Sinon : ./scripts/generate-rpm-signing-key.sh"

# Les 16 derniers hex de l'empreinte : c'est ce que rpm affiche dans pgpsig, et
# donc ce qui permet de verifier qu'on a signe avec LA cle attendue et pas avec
# une autre cle du trousseau.
KEYID="$(gpg --list-keys --with-colons "$GPG_NAME" | awk -F: '/^pub:/ {print $5; exit}')"
[ -n "$KEYID" ] || die "identifiant de cle illisible pour « ${GPG_NAME} »"

SIGN_ARGS=(--addsign --define "_gpg_name ${GPG_NAME}")
if [ -n "$PASSFILE" ]; then
    [ -f "$PASSFILE" ] || die "BLUEFOX_RPM_GPG_PASSPHRASE_FILE=${PASSFILE} introuvable"
    SIGN_ARGS+=(--define "_gpg_sign_cmd_extra_args --pinentry-mode=loopback --passphrase-file=${PASSFILE}")
fi

for rpm in "$@"; do
    [ -f "$rpm" ] || die "RPM introuvable : ${rpm}"
    log "signature de $(basename "$rpm") avec ${KEYID}"
    rpmsign "${SIGN_ARGS[@]}" "$rpm"

    # rpmsign sort 0 dans des cas ou la signature n'a PAS ete posee (gpg qui
    # renonce en silence, macro _gpg_name qui ne resout rien). On relit donc
    # l'entete du paquet plutot que le code de retour.
    SIG="$(rpm -qp --qf '%{SIGPGP:pgpsig}' "$rpm" 2>/dev/null || true)"
    case "$SIG" in
        ""|"(none)") die "aucune signature dans l'entete de $(basename "$rpm") apres rpmsign" ;;
    esac
    echo "$SIG" | grep -qi "$KEYID" \
        || die "signature posee par une autre cle que ${KEYID} : ${SIG}"
    log "    OK — ${SIG}"
done
