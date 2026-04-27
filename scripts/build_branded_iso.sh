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
FILES_DIR="${WORKDIR}/files/usr"

echo "[branded-iso] tenant=${SLUG} config=${TENANT_CONFIG}"

# 1. Fetch config + valider contre le schema.
TMP_CFG="$(mktemp)"
trap 'rm -f "$TMP_CFG"' EXIT
curl -fsSL "$TENANT_CONFIG" -o "$TMP_CFG"
python3 -c "
import json, jsonschema, sys
schema = json.load(open('${WORKDIR}/config/schema.v1.json'))
data = json.load(open('${TMP_CFG}'))
jsonschema.validate(data, schema)
print('[branded-iso] config valid')
"

# 2. Télécharger les assets de branding.
mkdir -p "${FILES_DIR}/share/bluefox/branding"
LOGO_URL=$(jq -r '.branding.logo_url' "$TMP_CFG")
WALLPAPER_URL=$(jq -r '.branding.wallpaper_url' "$TMP_CFG")
SPLASH_URL=$(jq -r '.branding.splash_url // empty' "$TMP_CFG")

curl -fsSL "$LOGO_URL" -o "${FILES_DIR}/share/bluefox/branding/logo.png"
curl -fsSL "$WALLPAPER_URL" -o "${FILES_DIR}/share/bluefox/branding/wallpaper.jpg"
[ -n "$SPLASH_URL" ] && curl -fsSL "$SPLASH_URL" -o "${FILES_DIR}/share/bluefox/branding/splash.png" || true

# 3. Copier la config dans /usr/share/bluefox/tenant.json (lue par welcome agent).
cp "$TMP_CFG" "${FILES_DIR}/share/bluefox/tenant.json"

# 4. Lancer BlueBuild.
cd "$WORKDIR"
exec bluebuild build "recipes/${SLUG}.yml"
