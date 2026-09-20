#!/usr/bin/env bash
# files/scripts/setup-surface-repo.sh — installe le depot linux-surface durci.
#
# Appele par le module `script` de recipes/bf-surface.yml, place AVANT le module
# rpm-ostree (dans une recipe BlueBuild, l'ordre des modules est l'ordre
# d'execution). Uniquement la variante Surface : bf et factice n'ont pas ce
# depot.
#
# POURQUOI ce script plutot qu'une entree `repos:` sur l'URL upstream
# (audit P5.2 du 2026-07-22, constat D1, tache #23812). Le fichier
# https://pkg.surfacelinux.com/fedora/linux-surface.repo tel que servi porte :
#   skip_if_unavailable=1   -> depot injoignable au build = depsolve qui
#                              CONTINUE et produit une image « surface » sans
#                              kernel surface. Panne silencieuse.
#   gpgkey=https://raw.githubusercontent.com/.../master/pkg/keys/surface.asc
#                           -> ancre de confiance tiree d'une branche MUTABLE.
# Ces deux defauts sont dans le fichier amont : on ne peut pas les corriger via
# `repos:`, parce que le module rpm-ostree curl le .repo et enchaine le depsolve
# dans la meme etape, sans point d'accroche entre les deux. D'ou ce pre-module,
# qui ecrit notre propre .repo et epingle la cle en depot.
#
# CE QU'ON N'ESSAIE PAS DE CORRIGER : repo_gpgcheck reste a 0. Les metadonnees
# du depot ne sont pas signees en amont — passer le drapeau a 1 casserait le
# depsolve, pas les metadonnees. Ecart assume et documente (le depot Tailscale,
# lui, signe metadonnees ET paquets). Les PAQUETS sont bien verifies :
# gpgcheck=1 contre la cle epinglee ci-dessous, qui est la seule ancre de
# confiance de cette variante.
#
# ⚠️ skip_if_unavailable=0 vaut aussi pour le systeme installe : ce fichier
# reste dans l'image. Un `rpm-ostree upgrade` lance alors que
# pkg.surfacelinux.com est injoignable echouera, au lieu de proposer une mise a
# jour amputee du kernel surface. C'est le comportement voulu — le timer
# rpm-ostreed-automatic rejouera plus tard.
set -euo pipefail

KEY_SRC="$(dirname "$0")/keys/linux-surface.asc"
KEY_DST="/etc/pki/rpm-gpg/RPM-GPG-KEY-linux-surface"
REPO_FILE="/etc/yum.repos.d/linux-surface.repo"
BASEURL_TMPL='https://pkg.surfacelinux.com/fedora/f$releasever/'

# Ancre de confiance epinglee.
#
# KEY_FPR a ete recoupee le 2026-07-24 sur DEUX serveurs de cles independants
# du depot GitHub d'ou vient le fichier — keys.openpgp.org et
# keyserver.ubuntu.com, ce dernier portant l'uid « Linux-Surface Package Signer
# (signing-key for packages in linux-surface package repositories)
# <luzmaximilian@gmail.com> ». Trois sources, meme empreinte.
#
# KEY_SHA256 est ce qui est VERIFIE ici : gpg n'est pas garanti present dans
# l'image de build, sha256sum l'est (coreutils). Le digest protege le fichier
# commite ; l'empreinte reste la valeur a recouper a la main lors d'une
# rotation amont. Les deux se re-verifient ensemble ou pas du tout.
KEY_FPR="87DEFA4AB94A99A4C8C3112556C464BAAC421453"
KEY_SHA256="ad86d878a07fa0f11e0d7aa89bc9763c05fc8c341f87ba92c9bc10a7ea26a9a9"

# Comment rpm nomme la cle importee, pour verifier que l'import a pris.
#
# ⚠️ rpm 6 la nomme d'apres l'empreinte COMPLETE en minuscules
# (`gpg-pubkey-87defa4a…c421453-<hex>`) ; rpm 4 et 5 n'en gardaient que les 8
# derniers hex. Verifie dans ghcr.io/ublue-os/kinoite-main:43 (rpm 6.0.1) le
# 2026-07-25 : c'est cette difference qui a fait echouer le premier build reel —
# la cle etait bien importee, l'assertion demandait l'ancien format. On accepte
# donc les deux formes.
KEY_FPR_LOWER="$(printf '%s' "$KEY_FPR" | tr 'A-Z' 'a-z')"
KEY_ID_LEGACY="${KEY_FPR_LOWER: -8}"

log() { echo "[surface-repo] $*"; }
die() { echo "[surface-repo] ERREUR: $*" >&2; exit 1; }

# --- 1. cle : presence + digest epingle -----------------------------------
[ -f "$KEY_SRC" ] || die "cle absente : ${KEY_SRC}"

ACTUAL_SHA="$(sha256sum "$KEY_SRC" | cut -d' ' -f1)"
[ "$ACTUAL_SHA" = "$KEY_SHA256" ] || die \
    "digest de la cle inattendu.
  attendu : ${KEY_SHA256}
  obtenu  : ${ACTUAL_SHA}
Si linux-surface a tourne sa cle, recouper l'empreinte sur au moins deux
sources independantes AVANT de mettre a jour KEY_SHA256/KEY_FPR ici."

# Verification d'empreinte quand gpg est la — en complement du digest, qui
# couvre deja l'integrite du fichier commite.
#
# ⚠️ DEUX pieges ici, les deux vecus le 2026-07-25 sur le premier build reel.
#
#   1. `gpg` MEURT dans un container de build bootc : /root y est un lien
#      symbolique vers var/roothome, et /var est VIDE a la construction — d'ou
#      « gpg: Fatal: can't create directory '/root/.gnupg' » et un code 2. Meme
#      famille que le piege dracut `--tmpdir /var/tmp` des recipes. Corrige par
#      un GNUPGHOME jetable, qui rend le controle deterministe partout.
#
#   2. La version precedente faisait `gpg … 2>/dev/null` : avec
#      `set -euo pipefail`, l'echec de gpg tuait le script AVANT le moindre
#      message — trace vide, code 2, aucune piste. Ne jamais avaler stderr sur
#      une commande dont l'echec doit etre diagnostiquable.
#
# Un echec d'ENVIRONNEMENT de gpg n'est pas fatal (le digest tient l'integrite) ;
# une empreinte qui NE CORRESPOND PAS, si.
if command -v gpg >/dev/null 2>&1; then
    GPG_HOME="$(mktemp -d)"
    GPG_ERR="$(mktemp)"
    if GPG_FPR="$(GNUPGHOME="$GPG_HOME" gpg --show-keys --with-colons "$KEY_SRC" \
            2>"$GPG_ERR" | awk -F: '/^fpr:/ {print $10; exit}')" \
       && [ -n "$GPG_FPR" ]; then
        [ "$GPG_FPR" = "$KEY_FPR" ] || die \
            "empreinte de la cle inattendue (attendu ${KEY_FPR}, obtenu ${GPG_FPR})"
        log "empreinte verifiee : ${KEY_FPR}"
    else
        log "⚠️ gpg n'a pas pu lire la cle ; verification limitee au digest sha256 :"
        sed 's/^/    gpg: /' "$GPG_ERR" >&2
    fi
    rm -rf "$GPG_HOME" "$GPG_ERR"
else
    log "gpg absent ; verification limitee au digest sha256"
fi

# --- 2. import ------------------------------------------------------------
install -D -m 0644 "$KEY_SRC" "$KEY_DST"
rpm --import "$KEY_DST"

# Pas de `2>/dev/null` : une erreur de rpm doit se voir, pas se transformer en
# « cle absente ». C'est ce qui rend ce genre de panne illisible.
rpm -q gpg-pubkey --qf '%{VERSION}\n' | grep -qiE "^(${KEY_FPR_LOWER}|${KEY_ID_LEGACY})\$" \
    || die "la cle ${KEY_FPR_LOWER} n'apparait pas dans le trousseau rpm apres import.
Trousseau actuel :
$(rpm -q gpg-pubkey --qf '  %{VERSION}\n' || true)"
log "cle importee dans le trousseau rpm (gpg-pubkey-${KEY_FPR_LOWER})"

# --- 3. depot durci -------------------------------------------------------
# Heredoc en quotes simples : $releasever doit rester LITTERAL dans le fichier
# ecrit, c'est dnf/rpm-ostree qui le resout — au build comme au runtime.
cat > "$REPO_FILE" <<'EOF'
# Ecrit par files/scripts/setup-surface-repo.sh (Blue Fox OS).
# Ne pas remplacer par le .repo servi par pkg.surfacelinux.com : il porte
# skip_if_unavailable=1 et une gpgkey sur une branche mutable. Voir #23812.
[linux-surface]
name=linux-surface
baseurl=https://pkg.surfacelinux.com/fedora/f$releasever/
enabled=1
type=rpm-md
enabled_metadata=1
# Paquets verifies contre la cle epinglee en depot (pas de fetch reseau).
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-linux-surface
# Metadonnees non signees en amont : laisser a 1 casserait le depsolve.
repo_gpgcheck=0
# Depot injoignable = erreur franche, jamais une image amputee du kernel
# surface (audit P5.2, constat D1).
skip_if_unavailable=0
EOF
log "ecrit ${REPO_FILE} (skip_if_unavailable=0, gpgkey epinglee en local)"

# --- 4. joignabilite -----------------------------------------------------
# skip_if_unavailable=0 fait deja echouer le depsolve si le depot est absent,
# mais l'erreur rpm-ostree qui en sort ne nomme pas la cause. Ce controle-ci
# donne le vrai message, et il attrape aussi le cas du tree f<N> pas encore
# publie en amont — exactement ce qui tient image-version en pin sur "43".
RELEASEVER="$(rpm -E %fedora)"
REPOMD="${BASEURL_TMPL/\$releasever/$RELEASEVER}repodata/repomd.xml"
if ! curl -fsSI --retry 3 --max-time 30 "$REPOMD" >/dev/null 2>&1; then
    die "depot linux-surface injoignable pour Fedora ${RELEASEVER} : ${REPOMD}
Si c'est un 404, le tree f${RELEASEVER} n'existe pas encore en amont : garder
image-version en pin sur la derniere version publiee dans recipes/bf-surface.yml."
fi
log "depot joignable pour Fedora ${RELEASEVER}"
