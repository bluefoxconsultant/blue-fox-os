#!/usr/bin/env bash
# files/scripts/install-welcome-rpm.sh — verifie la signature du RPM
# bluefox-welcome, puis l'installe. Execute DANS l'image, par le module `script`
# des recipes.
#
# Pourquoi (audit P5.2 du 2026-07-22, constat E1, tache #23811) : bluefox-welcome
# etait le seul paquet superpose non signe de l'image — et c'est le notre, celui
# qui porte le provisionnement OIDC et l'acquisition du credential Nextcloud.
# `rpm-ostree install <chemin>` ne verifie RIEN sur un fichier local : la
# verification doit etre explicite, ici, avant l'installation.
#
# La cle publique arrive par le module `files` (files/usr/share/bluefox/keys/),
# qui tourne AVANT le module `script` dans les recipes. Sa cle privee vit sur la
# machine de build, et nulle part ailleurs (#23815).
set -euo pipefail

KEY_SRC="/usr/share/bluefox/keys/RPM-GPG-KEY-blue-fox-os"
KEY_DST="/etc/pki/rpm-gpg/RPM-GPG-KEY-blue-fox-os"
RPM="/usr/share/bluefox/rpm-staging/bluefox-welcome.noarch.rpm"

log() { echo "[welcome-rpm] $*"; }
die() { echo "[welcome-rpm] ERREUR: $*" >&2; exit 1; }

[ -f "$RPM" ] || die "RPM absent : ${RPM}
scripts/publish-image.sh le construit et le stage aux etapes 1-2 ; un
`bluebuild build` lance a la main saute ces etapes."

[ -f "$KEY_SRC" ] || die "cle publique de signature absente : ${KEY_SRC}
Elle n'a pas encore ete generee, ou pas commitee. Sur la machine de build :
    ./scripts/generate-rpm-signing-key.sh
    git add files/usr/share/bluefox/keys/RPM-GPG-KEY-blue-fox-os && git commit
Ce build echoue a dessein plutot que d'installer un paquet non verifie (#23811)."

# Cle deposee aussi dans /etc/pki/rpm-gpg : c'est la ou un `rpm -K` sur le
# systeme installe ira la chercher, et ou les outils Fedora l'attendent.
install -D -m 0644 "$KEY_SRC" "$KEY_DST"
rpm --import "$KEY_DST"

# Deux controles, et il faut les deux :
#   - l'entete PORTE une signature (un paquet non signe ne fait pas echouer
#     `rpm -K`, il n'affiche simplement aucune ligne Signature) ;
#   - cette signature VERIFIE contre le trousseau (NOKEY / BAD sinon).
SIG="$(rpm -qp --qf '%{SIGPGP:pgpsig}' "$RPM" 2>/dev/null || true)"
case "$SIG" in
    ""|"(none)")
        die "le RPM n'est pas signe.
Publier avec BLUEFOX_RPM_GPG_NAME definie (scripts/publish-image.sh l'exige) —
un `make welcome-rpm` sans cette variable produit un paquet non signe." ;;
esac

KOUT="$(rpm -Kv "$RPM" 2>&1)" || die "rpm -Kv a echoue :
${KOUT}"
echo "$KOUT" | grep -Eq 'Signature, key ID [0-9a-fA-F]+: OK' || die \
    "signature non verifiable contre la cle Blue Fox :
${KOUT}
Un NOKEY signale un RPM signe par une AUTRE cle que celle commitee dans
files/usr/share/bluefox/keys/ — verifier laquelle a servi au build."

log "signature verifiee — ${SIG}"

rpm-ostree install "$RPM"
rm -rf /usr/share/bluefox/rpm-staging
log "installe, staging retire"
