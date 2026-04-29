#!/usr/bin/env bash
# Build le RPM bluefox-welcome de maniere reproductible.
#
# Usage : scripts/build-welcome-rpm.sh [OUT_DIR]
# Sortie : OUT_DIR/bluefox-welcome-<version>-1.fc<rel>.noarch.rpm
#         (defaut OUT_DIR = welcome/build/RPMS/noarch/)
#
# Requiert : rpmbuild, python3-setuptools, tar (Fedora). Tourne en CI dans
# fedora:41 ; localement, exec dans un container Fedora si non-Fedora.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WELCOME_DIR="$REPO_ROOT/welcome"
SPEC="$WELCOME_DIR/welcome.spec"
OUT_DIR="${1:-$WELCOME_DIR/build/RPMS/noarch}"

# Extrait Version: depuis le spec (single source of truth).
VERSION=$(awk '/^Version:/ {print $2}' "$SPEC")
NAME="bluefox-welcome"

if [[ -z "$VERSION" ]]; then
    echo "build-welcome-rpm: impossible d'extraire Version: depuis $SPEC" >&2
    exit 1
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
