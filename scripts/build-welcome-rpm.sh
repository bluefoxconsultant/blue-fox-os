#!/usr/bin/env bash
# Build le RPM bluefox-welcome de maniere reproductible.
#
# Usage : scripts/build-welcome-rpm.sh [OUT_DIR]
# Sortie : OUT_DIR/bluefox-welcome-<version>-1.fc<rel>.noarch.rpm
#         (defaut OUT_DIR = welcome/build/RPMS/noarch/)
#
# Requiert : rpmbuild + les macros RPM Fedora (pyproject-rpm-macros,
# systemd-rpm-macros). Sur un hote qui ne les a pas — Garuda/Arch, Debian,
# macOS... — le script rebondit tout seul dans un container fedora:43 via
# podman et s'y rejoue. Mettre BLUEFOX_RPM_NO_CONTAINER=1 pour l'interdire.
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
    echo "==> macros RPM Fedora absentes ; rebond dans fedora:43 via podman"
    # fedora:43 = ce que faisait le job build-rpm de la CI. La liste de paquets
    # est la meme, volontairement : c'est la seule chose qui garantit que le RPM
    # local est bit-pour-bit celui que la CI produisait.
    #
    # ⚠️ Plus de `exec` ici (change le 2026-07-24, #23811) : le script doit
    # reprendre la main apres le container pour signer le RPM sur l'HOTE. La cle
    # privee GPG n'entre pas dans le container — ni par montage, ni par variable.
    podman run --rm \
        -v "${REPO_ROOT}:/src:z" \
        -w /src \
        -e BLUEFOX_RPM_IN_CONTAINER=1 \
        fedora:43 \
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
cp -a "$WELCOME_DIR/firstboot.service" "$STAGE/"
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
