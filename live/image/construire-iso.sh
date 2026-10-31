#!/bin/bash
# Lance la fabrication de l'ISO live (#26870) dans le conteneur d'outils, en
# unite systemd --user (une relance de session ne la tue pas).
#
#   live/image/construire-iso.sh <nom>      ex. 2026-10-27
#
# Sortie : ~/.local/share/bfos-live-out/<nom>/ (build.log, ISO, sha256).
# docker (serveur de build) ou podman sans privileges (poste) ; stockage podman
# du conteneur d'outils : ~/.local/share/bfos-live-podman (la couche Flatpak,
# ~7,7 Gio, y reste en cache d'un build a l'autre).
set -euo pipefail
NOM=${1:?usage: construire-iso.sh <nom>}
DEPOT=$(cd "$(dirname "$0")/../.." && pwd)
SORTIE=$HOME/.local/share/bfos-live-out/$NOM
STOCKAGE=$HOME/.local/share/bfos-live-podman
MOTEUR=$(command -v docker || command -v podman)
mkdir -p "$SORTIE" "$STOCKAGE"
if systemctl --user is-active --quiet bfos-live-iso; then
    echo "un build tourne deja (unite bfos-live-iso)" >&2
    exit 1
fi
if ! "$MOTEUR" image exists bfos-iso-outils:43 2>/dev/null \
        && ! "$MOTEUR" image inspect bfos-iso-outils:43 >/dev/null 2>&1; then
    "$MOTEUR" build -t bfos-iso-outils:43 -f "$DEPOT/live/image/Containerfile.outils" "$DEPOT/live/image"
fi
EXTRA=()
[[ $MOTEUR == *podman ]] && EXTRA=(--device /dev/fuse --security-opt label=disable)
systemctl --user reset-failed bfos-live-iso 2>/dev/null || true
systemd-run --user --unit=bfos-live-iso /usr/bin/bash -c "
    $MOTEUR run --rm --privileged ${EXTRA[*]} \
        -v '$STOCKAGE:/var/lib/containers' -v '$DEPOT:/src:ro' -v '$SORTIE:/out' \
        bfos-iso-outils:43 bash /src/live/image/build-iso.sh > '$SORTIE/build.log' 2>&1
    echo \"fin rc=\$?\" >> '$SORTIE/build.log'"
echo "build lance : tail -f $SORTIE/build.log"
