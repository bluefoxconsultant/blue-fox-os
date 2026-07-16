#!/usr/bin/env bash
# scripts/bootstrap-garuda.sh — prepare une machine Garuda (ou toute base Arch)
# a construire et publier une image Blue Fox OS avec `make publish`.
#
# Usage :
#   ./scripts/bootstrap-garuda.sh          # installe ce qui manque
#   CHECK_ONLY=1 ./scripts/bootstrap-garuda.sh   # ne fait qu'inventorier
#
# Le script est idempotent : relançable sans effet de bord.
#
# Pourquoi un script a part : la chaine Blue Fox OS est pensee Fedora (le RPM
# du welcome agent utilise les macros pyproject-rpm-macros). Sur Arch, deux
# outils cles ne sont packages NI dans les depots officiels NI dans l'AUR —
# bluebuild et syft — et doivent passer par leurs installeurs upstream.
# Le RPM, lui, se construit dans un container fedora:43 : c'est
# scripts/build-welcome-rpm.sh qui s'en charge tout seul (il detecte l'absence
# des macros et rebondit dans podman).

set -euo pipefail

CHECK_ONLY="${CHECK_ONLY:-0}"
SYFT_BIN_DIR="${SYFT_BIN_DIR:-/usr/local/bin}"

log()  { echo "[bootstrap] $*"; }
warn() { echo "[bootstrap] ATTENTION: $*" >&2; }
die()  { echo "[bootstrap] ERREUR: $*" >&2; exit 1; }

# --- 0. sanity : base Arch ? ----------------------------------------------
[ -r /etc/os-release ] || die "/etc/os-release illisible — distro inconnue."
# shellcheck disable=SC1091
. /etc/os-release
if [ "${ID:-}" != "garuda" ] && [[ "${ID_LIKE:-}" != *arch* ]]; then
    die "distro '${ID:-?}' non basee sur Arch. Ce script cible Garuda/Arch ;
       sur Fedora, tout est deja disponible via dnf."
fi
log "distro : ${PRETTY_NAME:-$ID}"

command -v pacman >/dev/null 2>&1 || die "pacman introuvable."

# --- 1. paquets officiels --------------------------------------------------
# Tous dans extra (verifie 2026-07-16). buildah/skopeo : bluebuild s'appuie
# dessus pour construire et pousser. python-pillow : generate_kde_theme.py
# genere les PNG de la barre Plymouth (il sait retomber sur ImageMagick, mais
# autant lui donner Pillow). python-jsonschema : validation de config/*.json.
PACMAN_PKGS=(podman buildah skopeo cosign python-pillow python-jsonschema git curl)

MISSING=()
for p in "${PACMAN_PKGS[@]}"; do
    pacman -Qq "$p" >/dev/null 2>&1 || MISSING+=("$p")
done

if [ "${#MISSING[@]}" -eq 0 ]; then
    log "1/3 paquets pacman : deja tous presents"
elif [ "$CHECK_ONLY" = "1" ]; then
    log "1/3 paquets pacman manquants : ${MISSING[*]}"
else
    log "1/3 installation : ${MISSING[*]}"
    sudo pacman -S --needed --noconfirm "${MISSING[@]}"
fi

# --- 2. syft ---------------------------------------------------------------
# Absent des depots Arch ET de l'AUR (le paquet AUR nomme « syft » est une
# librairie de deep learning sans rapport). On passe donc par l'installeur
# officiel Anchore — exactement ce que faisait le job attest-sbom de la CI.
if command -v syft >/dev/null 2>&1; then
    log "2/3 syft : deja present ($(syft version 2>/dev/null | awk '/^Version:/{print $2}' | head -1))"
elif [ "$CHECK_ONLY" = "1" ]; then
    log "2/3 syft : ABSENT (installeur Anchore -> ${SYFT_BIN_DIR})"
else
    log "2/3 installation de syft dans ${SYFT_BIN_DIR}"
    curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
        | sudo sh -s -- -b "$SYFT_BIN_DIR"
fi

# --- 3. bluebuild ----------------------------------------------------------
# Absent des depots Arch ET de l'AUR (aucun paquet, verifie via l'API AUR).
# Upstream propose : cargo, un installeur en container, un install.sh, nix.
# On prend l'installeur en container : pas de toolchain Rust a trainer, et
# c'est la methode que BlueBuild documente en premier apres cargo.
# Alternative si tu preferes compiler : sudo pacman -S rust && cargo install --locked blue-build
if command -v bluebuild >/dev/null 2>&1; then
    log "3/3 bluebuild : deja present ($(bluebuild --version 2>/dev/null | head -1))"
elif [ "$CHECK_ONLY" = "1" ]; then
    log "3/3 bluebuild : ABSENT (installeur container ghcr.io/blue-build/cli)"
else
    log "3/3 installation de bluebuild via l'installeur container"
    podman run --pull always --rm ghcr.io/blue-build/cli:latest-installer | bash
fi

# --- bilan -----------------------------------------------------------------
echo
log "inventaire :"
RC=0
for t in podman buildah skopeo cosign syft bluebuild; do
    if command -v "$t" >/dev/null 2>&1; then
        printf '  %-10s OK   %s\n' "$t" "$(command -v "$t")"
    else
        printf '  %-10s MANQUANT\n' "$t"
        RC=1
    fi
done

echo
if [ "$RC" -ne 0 ]; then
    warn "il manque des outils — relancer le script, ou les installer a la main."
    exit 1
fi

cat <<'EOF'
[bootstrap] Prerequis outils : OK.

Il reste deux choses, qui ne s'automatisent pas ici :

  1. Auth GHCR (pour pousser l'image) :
       gh auth token | podman login ghcr.io -u "$(gh api user -q .login)" --password-stdin

  2. Cle cosign. `make publish` attend le CONTENU de cosign.key dans
     COSIGN_PRIVATE_KEY (pas son chemin). La cle vit aujourd'hui uniquement
     comme secret GitHub ; il faut la rapatrier ici pour signer en local :
       COSIGN_PRIVATE_KEY="$(cat /chemin/vers/cosign.key)" make publish SLUG=bf

     Si la cle a ete generee sans passphrase (convention BlueBuild), rien de
     plus a faire. Sinon, exporter aussi COSIGN_PASSWORD.

Premier essai conseille, sans rien pousser ni signer :

    DRY_RUN=1 ./scripts/publish-image.sh bf

Puis, pour de vrai :

    COSIGN_PRIVATE_KEY="$(cat cosign.key)" make publish SLUG=bf
EOF
