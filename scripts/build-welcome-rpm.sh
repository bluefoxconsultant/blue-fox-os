#!/usr/bin/env bash
# Build le RPM bluefox-welcome de maniere reproductible.
#
# Usage : scripts/build-welcome-rpm.sh [OUT_DIR]
# Sortie : OUT_DIR/bluefox-welcome-<version>-1.fc<rel>.noarch.rpm
#         (defaut OUT_DIR = welcome/build/RPMS/noarch/)
#
# Requiert : rpmbuild + les macros RPM Fedora (pyproject-rpm-macros,
# systemd-rpm-macros). Sur un hote qui ne les a pas — Garuda/Arch, Debian,
# macOS... — le script rebondit tout seul dans un container `fedora:<N>` via
# podman et s'y rejoue. Mettre BLUEFOX_RPM_NO_CONTAINER=1 pour l'interdire.
#
# ⚠️ <N> EST DERIVE DE L'IMAGE DE BASE, JAMAIS CODE EN DUR (change le 2026-11-15).
# Le RPM que rpmbuild produit porte `Requires: python(abi) = <version du python
# du container>`. L'image de base le resout a l'install. Si les deux Fedora
# divergent, rpm-ostree refuse la transaction avec « nothing provides
# python(abi) = X », et c'est la SEULE chose qui le dit : rien en amont ne
# compare les deux. C'est arrive : `image-version: latest` a fait passer
# kinoite-main a Fedora 45 (Python 3.15) le 2026-10-28 pendant que ce rebond
# restait sur fedora:43 (Python 3.14), et les 10 republications suivantes de bf,
# bf-surface et factice ont echoue — en silence, pendant 18 jours.
#
# SIGNATURE (#23811). Si BLUEFOX_RPM_GPG_NAME est definie, le RPM est signe a la
# fin — TOUJOURS sur l'hote, jamais dans le container : la cle privee ne franchit
# pas cette frontiere. Sinon le script le dit fort et continue : la CI et les
# builds de dev n'ont pas de cle, mais scripts/publish-image.sh, lui, EXIGE la
# variable. C'est la chaine de publication qui ne peut pas produire un paquet non
# signe, pas chaque rpmbuild.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WELCOME_DIR="$REPO_ROOT/welcome"
SPEC="$WELCOME_DIR/welcome.spec"
OUT_DIR="${1:-$WELCOME_DIR/build/RPMS/noarch}"
NAME="bluefox-welcome"

# Extrait Version: depuis le spec (single source of truth).
VERSION=$(awk '/^Version:/ {print $2}' "$SPEC")
if [[ -z "$VERSION" ]]; then
    echo "build-welcome-rpm: impossible d'extraire Version: depuis $SPEC" >&2
    exit 1
fi

# Signature (ou refus explicite de signer), appelee depuis les DEUX chemins :
# build natif et retour du container.
sign_or_warn() {
    local rpms=("$OUT_DIR/${NAME}-${VERSION}"-*.noarch.rpm)
    if [ -n "${BLUEFOX_RPM_GPG_NAME:-}" ]; then
        "$REPO_ROOT/scripts/sign-welcome-rpm.sh" "${rpms[@]}"
    else
        echo "==> ⚠️  RPM NON SIGNE : BLUEFOX_RPM_GPG_NAME n'est pas definie." >&2
        echo "==>     Acceptable pour un build de dev ou la CI de validation." >&2
        echo "==>     Interdit pour une publication — publish-image.sh refusera." >&2
        echo "==>     Creation de la cle : ./scripts/generate-rpm-signing-key.sh" >&2
    fi
}

# --- version de Fedora du rebond, DERIVEE de l'image de base ----------------
# Source de verite : la recette QU'ON BATIT, recipes/<slug>.yml — et NON
# recipes/_base.yml, qui ne documente que la base partagee et que BlueBuild
# n'herite jamais (le fichier dit lui-meme qu'il est « a dupliquer dans chaque
# recipe »).
#
# 🔴 La difference n'est pas cosmetique (relecture du 2026-11-17). bf-surface est
# epinglee `image-version: "43"` tant que pkg.surfacelinux.com n'a pas suivi,
# pendant que bf et factice sont en `latest`, soit Fedora 45 aujourd'hui. Deriver
# du socle partage aurait bati le RPM en fc45 pour bf-surface AUSSI, et son image
# F43 l'aurait refuse sur « nothing provides python(abi) = 3.15 » : la panne
# n'aurait pas ete corrigee, elle aurait change d'image — et bf-surface est
# justement la seule des trois qui publiait encore (mesure du 2026-11-15).
#
# L'image declare sa version a DEUX endroits independants ; on exige qu'ils
# s'accordent, et on REFUSE plutot que de retomber sur un defaut. Un numero par
# defaut est precisement ce qui a peri ici : il reste vert et faux.
#
# ⚠️ Chemin NATIF (hote portant deja les macros Fedora) : rpmbuild y batit contre
# le python de l'HOTE, que cette derivation ne touche pas. Sans effet sur la
# publication, qui tourne sur Garuda et rebondit donc toujours — mais une
# publication depuis un hote Fedora reposerait le meme desaccord ailleurs.

champ_du_yml() {  # <fichier> <cle> -> valeur, guillemets YAML retires
    # ⚠️ Les guillemets font partie du champ : bf-surface porte
    # `image-version: "43"`, et une balise `"43"` ne designe aucune image.
    awk -v k="^$2:" '$0 ~ k {print $2; exit}' "$1" | tr -d "\"'"
}

recette_du_rebond() {  # chemin du .yml qui fait foi, ou code 1
    if [ -n "${BLUEFOX_RPM_RECETTE:-}" ]; then
        [ -f "$BLUEFOX_RPM_RECETTE" ] || {
            echo "build-welcome-rpm: BLUEFOX_RPM_RECETTE=${BLUEFOX_RPM_RECETTE} introuvable. Refus." >&2
            return 1
        }
        printf '%s' "$BLUEFOX_RPM_RECETTE"
        return 0
    fi
    if [ -n "${SLUG:-}" ]; then
        # Un slug donne sans recette est une faute de frappe, pas un cas a
        # rattraper en douce sur le socle partage.
        [ -f "$REPO_ROOT/recipes/${SLUG}.yml" ] || {
            echo "build-welcome-rpm: SLUG=${SLUG} mais recipes/${SLUG}.yml introuvable. Refus." >&2
            return 1
        }
        printf '%s' "$REPO_ROOT/recipes/${SLUG}.yml"
        return 0
    fi
    # Aucun slug : build de dev ou de validation, ou le RPM n'entre dans aucune
    # image epinglee. On le DIT — un silence ici est exactement ce qui a laisse
    # passer des publications fausses.
    echo "==> ⚠️  aucun SLUG : Fedora du rebond derivee du socle partage" >&2
    echo "==>     recipes/_base.yml. Pour un RPM destine a une image epinglee," >&2
    echo "==>     passer SLUG=<tenant> (publish-image.sh le fait)." >&2
    printf '%s' "$REPO_ROOT/recipes/_base.yml"
}

fedora_du_socle() {
    if [ -n "${BLUEFOX_RPM_FEDORA_TAG:-}" ]; then
        echo "==> ⚠️  rebond FORCE sur fedora:${BLUEFOX_RPM_FEDORA_TAG} par" >&2
        echo "==>     BLUEFOX_RPM_FEDORA_TAG : la derivation est contournee." >&2
        printf '%s' "$BLUEFOX_RPM_FEDORA_TAG"
        return 0
    fi

    local socle_yml
    socle_yml=$(recette_du_rebond) || return 1
    [ -f "$socle_yml" ] || {
        echo "build-welcome-rpm: $socle_yml introuvable : impossible de savoir" >&2
        echo "  quelle Fedora doit batir le RPM. Refus." >&2
        return 1
    }
    local img ver
    img=$(champ_du_yml "$socle_yml" base-image)
    ver=$(champ_du_yml "$socle_yml" image-version)
    [ -n "$img" ] && [ -n "$ver" ] || {
        echo "build-welcome-rpm: base-image/image-version absents de $socle_yml." >&2
        return 1
    }

    local brut=""
    if command -v skopeo >/dev/null 2>&1; then
        brut=$(skopeo inspect "docker://${img}:${ver}" 2>/dev/null) || brut=""
    fi
    if [ -z "$brut" ] && command -v podman >/dev/null 2>&1; then
        # Repli : l'image a peut-etre deja ete tiree localement.
        brut=$(podman image inspect "${img}:${ver}" 2>/dev/null \
               | python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; print(json.dumps({"Labels": d.get("Labels") or {}}))' 2>/dev/null) || brut=""
    fi
    [ -n "$brut" ] || {
        echo "build-welcome-rpm: ni skopeo ni une copie locale ne permettent de" >&2
        echo "  lire ${img}:${ver}. Installer skopeo, ou tirer l'image, ou" >&2
        echo "  forcer explicitement BLUEFOX_RPM_FEDORA_TAG=<N>. Refus." >&2
        return 1
    }

    # Deux temoins independants : l'etiquette de version et le noyau ostree.
    local n_version n_noyau
    n_version=$(printf '%s' "$brut" | python3 -c '
import json, re, sys
labels = (json.load(sys.stdin).get("Labels") or {})
v = labels.get("org.opencontainers.image.version") or ""
m = re.match(r"^(\d+)\b", v)
print(m.group(1) if m else "")
' 2>/dev/null)
    n_noyau=$(printf '%s' "$brut" | python3 -c '
import json, re, sys
labels = (json.load(sys.stdin).get("Labels") or {})
m = re.search(r"\.fc(\d+)\b", labels.get("ostree.linux") or "")
print(m.group(1) if m else "")
' 2>/dev/null)

    if [ -n "$n_version" ] && [ -n "$n_noyau" ] && [ "$n_version" != "$n_noyau" ]; then
        echo "build-welcome-rpm: ${img}:${ver} se contredit — etiquette de" >&2
        echo "  version = Fedora ${n_version}, noyau ostree = Fedora ${n_noyau}." >&2
        echo "  Batir le RPM sur une supposition produirait un paquet que" >&2
        echo "  l'image refuse d'installer. Refus." >&2
        return 1
    fi
    local n="${n_version:-$n_noyau}"
    [ -n "$n" ] || {
        echo "build-welcome-rpm: ${img}:${ver} ne declare ni" >&2
        echo "  org.opencontainers.image.version ni ostree.linux exploitables." >&2
        echo "  Refus plutot qu'un numero par defaut." >&2
        return 1
    }
    printf '%s' "$n"
}

# Couture d'essai : dire quelle recette fait foi et quelle balise serait
# demandee, sans reseau, sans podman et sans rien batir (scripts/tests/).
# `=complet` va jusqu'au bout — il interroge l'image, donc il exige le reseau :
# c'est le temoin a jouer sur la machine de build AVANT une republication.
if [ -n "${BLUEFOX_RPM_DIRE_SOCLE:-}" ]; then
    RECETTE=$(recette_du_rebond) || exit 1
    printf 'recette=%s image=%s version=%s\n' "$RECETTE" \
        "$(champ_du_yml "$RECETTE" base-image)" \
        "$(champ_du_yml "$RECETTE" image-version)"
    if [ "$BLUEFOX_RPM_DIRE_SOCLE" = complet ]; then
        FEDORA_N=$(fedora_du_socle) || exit 1
        printf 'fedora=%s\n' "$FEDORA_N"
    fi
    exit 0
fi

# --- bascule container ------------------------------------------------------
# Le .spec s'appuie sur %pyproject_wheel & co, fournis par pyproject-rpm-macros.
# Arch a bien un paquet `rpm-tools` qui fournit rpmbuild, mais PAS ces macros :
# un rpmbuild "reussi" y produirait un RPM faux plutot qu'une erreur franche.
# On teste donc la macro, pas seulement la presence du binaire.
native_rpm_stack() {
    command -v rpmbuild >/dev/null 2>&1 || return 1
    # Macro absente => rpm --eval renvoie le litteral '%pyproject_wheel'.
    [ "$(rpm --eval '%pyproject_wheel' 2>/dev/null)" != '%pyproject_wheel' ]
}

if [ "${BLUEFOX_RPM_IN_CONTAINER:-0}" != "1" ] && ! native_rpm_stack; then
    if [ "${BLUEFOX_RPM_NO_CONTAINER:-0}" = "1" ]; then
        echo "build-welcome-rpm: macros RPM Fedora absentes et container interdit." >&2
        exit 1
    fi
    command -v podman >/dev/null 2>&1 || {
        echo "build-welcome-rpm: ni macros RPM Fedora, ni podman. Sur Garuda/Arch :" >&2
        echo "  sudo pacman -S --needed podman" >&2
        exit 1
    }
    FEDORA_N=$(fedora_du_socle) || exit 1
    echo "==> macros RPM Fedora absentes ; rebond dans fedora:${FEDORA_N} via podman"
    echo "==> (version derivee de l'image de base, pas codee en dur)"
    # La liste de paquets reste celle du job build-rpm de la CI, volontairement.
    # Ce qui a change le 2026-11-15, c'est la BALISE : elle suit l'image de base,
    # parce que c'est le python de l'image qui doit resoudre le python(abi) du
    # RPM — et rien d'autre dans la chaine ne compare les deux.
    #
    # ⚠️ Plus de `exec` ici (change le 2026-07-24, #23811) : le script doit
    # reprendre la main apres le container pour signer le RPM sur l'HOTE. La cle
    # privee GPG n'entre pas dans le container — ni par montage, ni par variable.
    podman run --rm \
        -v "${REPO_ROOT}:/src:z" \
        -w /src \
        -e BLUEFOX_RPM_IN_CONTAINER=1 \
        "fedora:${FEDORA_N}" \
        bash -c '
            set -euo pipefail
            dnf -y -q install rpm-build python3-devel pyproject-rpm-macros \
                systemd-rpm-macros python3-setuptools python3-wheel python3-pip
            exec scripts/build-welcome-rpm.sh "$@"
        ' _ "$@"
    sign_or_warn
    exit 0
fi

echo "==> Build $NAME-$VERSION"

TOPDIR="$WELCOME_DIR/build"
rm -rf "$TOPDIR"
mkdir -p "$TOPDIR"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

# rpmbuild attend Source0 = bluefox-welcome-<version>.tar.gz qui se deballe
# en bluefox-welcome-<version>/ contenant pyproject.toml + bluefox_welcome/.
TARBALL="$TOPDIR/SOURCES/${NAME}-${VERSION}.tar.gz"
STAGE="$TOPDIR/stage/${NAME}-${VERSION}"

mkdir -p "$STAGE"
cp -a "$WELCOME_DIR/bluefox_welcome" "$STAGE/"
cp -a "$WELCOME_DIR/pyproject.toml" "$STAGE/"
cp -a "$WELCOME_DIR/README.md" "$STAGE/"
# Le spec reference %license LICENSE — on copie le LICENSE root du repo.
cp -a "$REPO_ROOT/LICENSE" "$STAGE/LICENSE"

tar -C "$TOPDIR/stage" -czf "$TARBALL" "${NAME}-${VERSION}"
echo "==> Tarball : $TARBALL ($(du -h "$TARBALL" | cut -f1))"

cp "$SPEC" "$TOPDIR/SPECS/"

rpmbuild -bb "$TOPDIR/SPECS/$(basename "$SPEC")" \
    --define "_topdir $TOPDIR" \
    --define "_sourcedir $TOPDIR/SOURCES"

mkdir -p "$OUT_DIR"
RPM_BUILT_DIR="$TOPDIR/RPMS/noarch"
if [[ "$(realpath "$RPM_BUILT_DIR")" != "$(realpath "$OUT_DIR")" ]]; then
    cp "$RPM_BUILT_DIR/${NAME}-${VERSION}"-*.noarch.rpm "$OUT_DIR/"
fi

echo "==> RPM(s) dans $OUT_DIR :"
ls -la "$OUT_DIR/${NAME}-${VERSION}"-*.noarch.rpm

# Dans le container, on ne signe pas : c'est l'hote qui reprend la main juste
# apres le `podman run` (voir sign_or_warn et le commentaire du rebond).
if [ "${BLUEFOX_RPM_IN_CONTAINER:-0}" = "1" ]; then
    echo "==> signature deleguee a l'hote (la cle privee n'entre pas dans le container)"
else
    sign_or_warn
fi
