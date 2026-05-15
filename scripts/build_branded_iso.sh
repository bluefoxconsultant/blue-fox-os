#!/usr/bin/env bash
# scripts/build_branded_iso.sh - matérialise le branding tenant avant BlueBuild.
#
# Lit config/{slug}.json (NON commité ; vit côté serveur, fetch via curl
# en CI ou en local), télécharge les assets de branding, prépare le
# répertoire files/usr/ consommé par la recipe BlueBuild, puis appelle
# `bluebuild build`.
#
# Usage : SLUG=bf ./scripts/build_branded_iso.sh
#         SLUG=bf TENANT_CONFIG=https://config.bluefoxconsultant.com/bf.json ./scripts/build_branded_iso.sh

set -euo pipefail

SLUG="${SLUG:?SLUG env var required, ex: SLUG=bf}"
TENANT_CONFIG="${TENANT_CONFIG:-https://config.bluefoxconsultant.com/${SLUG}.json}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
FILES_ROOT="${WORKDIR}/files"
FILES_DIR="${FILES_ROOT}/usr"
INREPO_FALLBACK="${WORKDIR}/config/${SLUG}.json"

echo "[branded-iso] tenant=${SLUG} config=${TENANT_CONFIG}"

# 1. Fetch config + valider contre le schema. Fallback in-repo si l'endpoint
# n'est pas accessible (utile en CI ou hors ligne pour les tenants commits).
TMP_CFG="$(mktemp)"
trap 'rm -f "$TMP_CFG"' EXIT
if curl -fsSL "$TENANT_CONFIG" -o "$TMP_CFG" 2>/dev/null; then
    echo "[branded-iso] fetched from $TENANT_CONFIG"
elif [ -f "$INREPO_FALLBACK" ]; then
    echo "[branded-iso] endpoint unreachable, falling back to ${INREPO_FALLBACK}"
    cp "$INREPO_FALLBACK" "$TMP_CFG"
else
    echo "[branded-iso] FAIL: cannot fetch ${TENANT_CONFIG} and no in-repo fallback at ${INREPO_FALLBACK}" >&2
    exit 1
fi
# Validation jsonschema : facultative quand le module n'est pas installe (cas
# Kinoite immutable sans `pip` / installeur RPM). Le JSON est de toute facon
# valide a la lecture par les consommateurs (jq, python json.load) ; sauter
# la validation schema-stricte est OK pour un build local.
python3 -c "
import json, sys
try:
    import jsonschema
except ModuleNotFoundError:
    print('[branded-iso] jsonschema not installed ; skipping schema validation', file=sys.stderr)
    sys.exit(0)
schema = json.load(open('${WORKDIR}/config/schema.v1.json'))
data = json.load(open('${TMP_CFG}'))
jsonschema.validate(data, schema)
print('[branded-iso] config valid')
"

# 2. Télécharger les assets de branding. Fallback: si l'URL n'est pas
# accessible, copier le fichier homonyme depuis branding/ (in-repo).
mkdir -p "${FILES_DIR}/share/bluefox/branding"
LOGO_URL=$(jq -r '.branding.logo_url' "$TMP_CFG")
WALLPAPER_URL=$(jq -r '.branding.wallpaper_url' "$TMP_CFG")
SPLASH_URL=$(jq -r '.branding.splash_url // empty' "$TMP_CFG")
APP_ICON_URL=$(jq -r '.branding.app_icon_url // empty' "$TMP_CFG")

fetch_or_local() {
    # $1 = URL, $2 = destination. Try URL, fallback to branding/<basename>.
    local url="$1"
    local dest="$2"
    local fallback="${WORKDIR}/branding/$(basename "$url")"
    if curl -fsSL "$url" -o "$dest" 2>/dev/null; then
        echo "[branded-iso] fetched $(basename "$dest") from URL"
    elif [ -f "$fallback" ]; then
        cp "$fallback" "$dest"
        echo "[branded-iso] $(basename "$dest") from local fallback ${fallback}"
    else
        echo "[branded-iso] FAIL: $(basename "$dest") missing (URL ${url} unreachable, no ${fallback})" >&2
        return 1
    fi
}

fetch_or_local "$LOGO_URL" "${FILES_DIR}/share/bluefox/branding/logo.png"
fetch_or_local "$WALLPAPER_URL" "${FILES_DIR}/share/bluefox/branding/wallpaper.jpg"
[ -n "$SPLASH_URL" ] && fetch_or_local "$SPLASH_URL" "${FILES_DIR}/share/bluefox/branding/splash.png" || true
[ -n "$APP_ICON_URL" ] && fetch_or_local "$APP_ICON_URL" "${FILES_DIR}/share/bluefox/branding/app-icon.svg" || true

# 2b. Pack wallpapers (#22433) — copier branding/wallpapers/*.png vers l'image.
# Le welcome agent pick une wallpaper random parmi celles-ci au firstboot
# (apply_kde_theme), tout en gardant wallpaper.jpg comme default stable pour
# SDDM/login. Pack absent = degrade gracieux, welcome agent fallback sur wallpaper.jpg.
WALLPAPERS_SRC="${WORKDIR}/branding/wallpapers"
WALLPAPERS_DST="${FILES_DIR}/share/bluefox/branding/wallpapers"
if [ -d "$WALLPAPERS_SRC" ] && compgen -G "${WALLPAPERS_SRC}/*.png" >/dev/null; then
    mkdir -p "$WALLPAPERS_DST"
    cp "$WALLPAPERS_SRC"/*.png "$WALLPAPERS_DST/"
    echo "[branded-iso] wallpapers pack: $(ls -1 "$WALLPAPERS_DST" | wc -l) files"
else
    echo "[branded-iso] no wallpapers pack (${WALLPAPERS_SRC}/*.png) — welcome agent fallbacks to wallpaper.jpg"
fi

# 3. Copier la config dans /usr/share/bluefox/tenant.json (lue par welcome agent).
cp "$TMP_CFG" "${FILES_DIR}/share/bluefox/tenant.json"

# 4. Generer la stack KDE (color scheme, look-and-feel, SDDM, kdeglobals)
# a partir de tenant.json. Symlinke wallpaper/logo vers /usr/share/bluefox/branding/.
echo "[branded-iso] generating KDE theme assets"
python3 "${WORKDIR}/scripts/generate_kde_theme.py" "$TMP_CFG" "$FILES_ROOT"

# 5. Lancer BlueBuild — sauf en mode prep (BUILD=0), utilise par CI ou
# par les workflows qui veulent pre-stager files/ et confier le build a
# blue-build/github-action@v1.
if [ "${BUILD:-1}" = "0" ]; then
    echo "[branded-iso] BUILD=0 ; staging only, skipping bluebuild"
    exit 0
fi

cd "$WORKDIR"
exec bluebuild build "recipes/${SLUG}.yml"
