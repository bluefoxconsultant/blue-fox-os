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
#   BLUEFOX_RPM_PUBKEY              cle publique de reference (defaut : celle du depot)
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
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PUBKEY="${BLUEFOX_RPM_PUBKEY:-${REPO_ROOT}/files/usr/share/bluefox/keys/RPM-GPG-KEY-blue-fox-os}"

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

# rpm imprime l'empreinte complete en minuscules quand la cle est connue de sa
# base ; c'est sur cette chaine que porte le controle de verification.
FPR="$(gpg --list-keys --with-colons "$GPG_NAME" | awk -F: '/^fpr:/ {print $10; exit}')"
[ -n "$FPR" ] || die "empreinte illisible pour « ${GPG_NAME} »"
FPR_LOWER="$(printf '%s' "$FPR" | tr 'A-Z' 'a-z')"

[ -f "$PUBKEY" ] || die "cle publique de reference introuvable : ${PUBKEY}"
PUB_FPR="$(gpg --show-keys --with-colons "$PUBKEY" | awk -F: '/^fpr:/ {print $10; exit}')"
[ "$PUB_FPR" = "$FPR" ] || die \
    "la cle qui signe et la cle publique commitee divergent.
  signature : ${FPR}
  depot     : ${PUB_FPR}
L'image embarquerait une cle incapable de verifier le paquet (#23811)."

SIGN_ARGS=(--addsign --define "_gpg_name ${GPG_NAME}")
if [ -n "$PASSFILE" ]; then
    [ -f "$PASSFILE" ] || die "BLUEFOX_RPM_GPG_PASSPHRASE_FILE=${PASSFILE} introuvable"
    SIGN_ARGS+=(--define "_gpg_sign_cmd_extra_args --pinentry-mode=loopback --passphrase-file=${PASSFILE}")
fi

# --- base rpm jetable pour la verification ---------------------------------
# ⚠️ Deux pieges, vecus le 2026-07-25 sur la machine de build :
#
#   1. Arch/Garuda n'a PAS de base rpm (`rpm` n'y est pas le gestionnaire de
#      paquets) : /var/lib/rpm n'existe pas, et TOUTE commande rpm — meme un
#      simple `rpm -qp --qf` sur un fichier — sort en
#      « can't create transaction lock on /var/lib/rpm/.rpm.lock ». Une
#      verification qui avale cette erreur (`2>/dev/null || true`) lit une chaine
#      vide et conclut « paquet non signe » sur un paquet parfaitement signe.
#      D'ou une base jetable, et pas de redirection d'erreur silencieuse.
#
#   2. RPM 6 n'alimente PLUS les tags de signature historiques : sur un paquet
#      signe, `%{SIGPGP:pgpsig}` rend « (none) » et `rpm -qpi` affiche un champ
#      « Signature : » vide. La signature vit dans `%{OPENPGP}` (base64) et
#      `%{RSAHEADER}`. Verifie sur rpm 6.0.1, cote Arch ET cote fedora:43.
#      C'est un piege silencieux : le tag existe toujours, il est juste vide.
RPMDB="$(mktemp -d)"
cleanup() { rm -rf "$RPMDB"; }
trap cleanup EXIT

rpm --dbpath "$RPMDB" --initdb
rpm --dbpath "$RPMDB" --import "$PUBKEY" \
    || die "import de ${PUBKEY} dans la base jetable impossible"

for rpm in "$@"; do
    [ -f "$rpm" ] || die "RPM introuvable : ${rpm}"
    log "signature de $(basename "$rpm") avec ${KEYID}"
    rpmsign "${SIGN_ARGS[@]}" "$rpm"

    # rpmsign sort 0 dans des cas ou la signature n'a PAS ete posee (gpg qui
    # renonce en silence, macro _gpg_name qui ne resout rien). On relit donc
    # l'entete du paquet plutot que le code de retour.
    SIG="$(rpm --dbpath "$RPMDB" -qp --qf '%{OPENPGP}' "$rpm")"
    case "$SIG" in
        ""|"(none)") die "aucune signature dans l'entete de $(basename "$rpm") apres rpmsign" ;;
    esac

    # Et la signature doit VERIFIER contre la cle publique commitee — celle que
    # l'image utilisera. Un paquet signe par une autre cle passerait le controle
    # de presence ci-dessus mais casserait le build.
    KOUT="$(rpm --dbpath "$RPMDB" -Kv "$rpm")" \
        || die "rpm -Kv a echoue sur $(basename "$rpm") :
${KOUT}"
    echo "$KOUT" | grep -Eqi "signature.*${FPR_LOWER}.*: OK" || die \
        "signature non verifiable contre ${PUBKEY} :
${KOUT}
Un « NOKEY » signale un paquet signe par une AUTRE cle que celle commitee."
    log "    OK — $(echo "$KOUT" | grep -i 'signature' | grep -i ': OK' | sed 's/^ *//')"
done
