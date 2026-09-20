#!/usr/bin/env bash
# scripts/generate-rpm-signing-key.sh — cree la paire GPG de signature des RPM
# Blue Fox, une fois, SUR LA MACHINE DE BUILD.
#
# Pourquoi (audit P5.2 du 2026-07-22, constat E1, tache #23811) : de tous les
# paquets superposes a l'image, un seul n'etait pas signe — le notre.
#   tailscale                                    depot Tailscale, paquets + metadonnees signes
#   rclone, fuse3, plymouth-plugin-script        Fedora / kinoite-main, signes
#   kernel-surface, iptsd, surface-secureboot    linux-surface, paquets signes (cf. #23812)
#   bluefox-welcome                              construit en local, AUCUNE signature
# Et c'est celui qui porte le welcome agent : provisionnement OIDC, acquisition
# du credential Nextcloud. Le maillon le plus sensible etait le seul non verifie.
#
# CETTE CLE EST DISTINCTE DE LA CLE COSIGN. cosign signe l'image OCI, GPG signe
# le RPM : deux artefacts, deux chaines de verification, deux cles. Une seule cle
# pour les deux ferait d'une compromission un doublement de portee.
#
# OU VIT LA CLE PRIVEE (#23815) : sur la machine de build, et nulle part
# ailleurs. Elle n'a rien a faire sur tentaclaude ni dans le depot. Prevoir une
# copie de secours chiffree dans le coffre BF — une machine de build qui meurt
# avec la seule copie de la cle oblige a une rotation en urgence.
#
# Usage :
#   ./scripts/generate-rpm-signing-key.sh
#   BLUEFOX_RPM_GPG_NAME="Autre uid <a@b.c>" ./scripts/generate-rpm-signing-key.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GPG_NAME="${BLUEFOX_RPM_GPG_NAME:-Blue Fox OS Package Signing <info@bluefoxconsultant.com>}"
PUBKEY_DST="${REPO_ROOT}/files/usr/share/bluefox/keys/RPM-GPG-KEY-blue-fox-os"

log() { echo "==> $*"; }
die() { echo "generate-rpm-signing-key: $*" >&2; exit 1; }

command -v gpg >/dev/null 2>&1 || die "gpg introuvable. Sur Garuda/Arch : sudo pacman -S --needed gnupg"

if gpg --list-secret-keys "$GPG_NAME" >/dev/null 2>&1; then
    die "une cle secrete existe deja pour « ${GPG_NAME} ».
Pour en generer une nouvelle, choisir un autre uid (BLUEFOX_RPM_GPG_NAME) ou
suivre la procedure de rotation du runbook — une rotation impose de republier
le RPM ET les images qui l'embarquent, la cle publique etant baked dedans."
fi

# Sans passphrase, a dessein et de facon coherente avec la cle cosign : la
# publication (`make publish`) doit rester non interactive, et la frontiere de
# securite est le disque de la machine de build, pas un secret de plus dans un
# script. Qui veut une passphrase peut en poser une (gpg --edit-key passwd) et
# la fournir via BLUEFOX_RPM_GPG_PASSPHRASE_FILE au moment de signer.
#
# Pas d'expiration : une signature de paquet doit rester verifiable longtemps
# apres le build (c'est la pratique des cles de distribution Fedora). La
# rotation est une decision, pas une echeance qui tombe un dimanche.
log "generation d'une RSA 4096 de signature pour « ${GPG_NAME} »"
gpg --batch --passphrase '' --quick-generate-key "$GPG_NAME" rsa4096 sign never

FPR="$(gpg --list-keys --with-colons "$GPG_NAME" | awk -F: '/^fpr:/ {print $10; exit}')"
[ -n "$FPR" ] || die "empreinte illisible apres generation"

mkdir -p "$(dirname "$PUBKEY_DST")"
gpg --armor --export "$GPG_NAME" > "$PUBKEY_DST"
[ -s "$PUBKEY_DST" ] || die "export de la cle publique vide"

log "cle publique exportee : ${PUBKEY_DST}"
log "empreinte : ${FPR}"
cat <<EOF

Suite (dans cet ordre) :
  1. git add ${PUBKEY_DST#"${REPO_ROOT}/"} && git commit
     La cle publique DOIT etre en depot : les recipes la deposent dans l'image
     et verifient le RPM avec elle avant rpm-ostree install.
  2. Consigner l'empreinte ${FPR} dans le runbook cosign/GPG
     (/Blue Fox/Blue Fox OS/Runbook/02_signing_keys.md).
  3. Deposer une copie de secours chiffree de la cle privee dans le coffre BF.
  4. Exporter BLUEFOX_RPM_GPG_NAME="${GPG_NAME}" dans l'environnement de build
     (publish-image.sh l'EXIGE : sans elle, il refuse de publier).
EOF
