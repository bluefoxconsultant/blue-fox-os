#!/usr/bin/env bash
# files/scripts/install-tailscale-rpm.sh — verifie puis installe le RPM
# Tailscale depose par scripts/stage_tailscale_rpm.py. Execute DANS l'image.
#
# 🔴 POURQUOI ON NE LE TIRE PLUS DU DEPOT PENDANT LA CONSTRUCTION.
# `pkgs.tailscale.com` coupe en plein transfert depuis la machine de build
# (« Curl error (56): Failure when receiving data from the peer »), le paquet
# fait 39 Mio, et rpm-ostree NE REESSAIE PAS : une coupure emporte toute la
# construction du locataire. Le 2026-09-21, trois tours de suite pour bf et
# bf-surface. Le telechargement se fait donc cote hote, avec
# `curl --retry-all-errors` — le seul drapeau qui rejoue une erreur 56.
#
# ⚠️ DNF5 n'a pas d'option `retries` : verifie dans sa documentation avant de
# l'ecrire dans le fichier .repo. `timeout` et `minrate` coupent une connexion
# morte, ils ne rejouent rien.
#
# ⚠️ `rpm-ostree install <chemin>` ne verifie RIEN sur un fichier local. La
# signature se verifie donc ici, explicitement, contre la cle du depot deposee
# a cote — meme geste que pour notre propre RPM (voir install-welcome-rpm.sh).
set -euo pipefail

RPM="/usr/share/bluefox/rpm-staging/tailscale.rpm"
KEY_SRC="/usr/share/bluefox/keys/RPM-GPG-KEY-tailscale"
KEY_DST="/etc/pki/rpm-gpg/RPM-GPG-KEY-tailscale"

log() { echo "[tailscale-rpm] $*"; }
die() { echo "[tailscale-rpm] ERREUR: $*" >&2; exit 1; }

[ -f "$RPM" ] || die "RPM absent : ${RPM}
scripts/publish-image.sh le depose a l'etape 2 (stage_tailscale_rpm.py) ; un
\`bluebuild build\` lance a la main saute cette etape."
[ -f "$KEY_SRC" ] || die "cle publique du depot absente : ${KEY_SRC}"

install -D -m 0644 "$KEY_SRC" "$KEY_DST"
rpm --import "$KEY_DST"

# L'identifiant tel que rpm vient de l'enregistrer — lu, jamais code en dur :
# rpm 6 nomme la cle d'apres l'empreinte complete, rpm 4/5 d'apres 8 hex.
KEY_RPM_ID="$(rpm -q gpg-pubkey --qf '%{VERSION} %{SUMMARY}\n' \
    | grep -i 'tailscale' | awk '{print $1}' | head -1)"
[ -n "$KEY_RPM_ID" ] || die "la cle Tailscale n'apparait pas dans la base rpm apres import"

KOUT="$(rpm -Kv "$RPM" 2>&1)" || die "rpm -Kv a echoue :
${KOUT}"
echo "$KOUT" | grep -Eqi "signature.*${KEY_RPM_ID}.*: OK" || die \
    "signature non verifiable contre la cle du depot Tailscale :
${KOUT}
Un NOKEY signale un RPM signe par une AUTRE cle que celle deposee a cote."

log "signature verifiee — $(echo "$KOUT" | grep -i 'signature' | grep -i ': OK' | sed 's/^ *//' | head -1)"
log "version : $(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$RPM")"
rpm-ostree install "$RPM"
log "installe depuis le disque, sans toucher au reseau"
