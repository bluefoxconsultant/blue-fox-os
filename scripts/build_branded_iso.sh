#!/usr/bin/env bash
# scripts/build_branded_iso.sh - matérialise le branding tenant avant BlueBuild.
#
# Lit le manifeste du tenant, télécharge les assets de branding, prépare le
# répertoire files/usr/ consommé par la recipe BlueBuild, puis appelle
# `bluebuild build`.
#
# SOURCE DU MANIFESTE (tranché le 2026-07-24, #23813). La source unique est
# `config/{slug}.json`, EN DÉPÔT. `config.bluefoxconsultant.com` n'a jamais été
# déployé — il répondait 404 sur `/`, `/bf.json` et `/factice.json` — et il était
# pourtant la valeur par défaut de TENANT_CONFIG, avec un repli in-repo
# silencieux (`curl … 2>/dev/null`, échec avalé). Résultat : des builds qui
# passaient sans que le trou soit visible, et un item du périmètre d'audit P5.2
# qui décrivait un service inexistant.
#
# Décision : pas d'endpoint. Une config tirée du réseau au moment du build est
# une surface d'attaque de plus (un manifeste détourné repeint l'image), et il
# n'y a aucun tenant hors dépôt à servir. TENANT_CONFIG accepte toujours une
# URL — pour le jour où il y en aura un — mais l'échec du fetch est alors FATAL :
# plus jamais de repli muet.
#
# Usage : SLUG=bf ./scripts/build_branded_iso.sh
#         SLUG=bf TENANT_CONFIG=/chemin/vers/manifeste.json ./scripts/build_branded_iso.sh
#         SLUG=bf TENANT_CONFIG=https://exemple/bf.json ./scripts/build_branded_iso.sh

set -euo pipefail

SLUG="${SLUG:?SLUG env var required, ex: SLUG=bf}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
TENANT_CONFIG="${TENANT_CONFIG:-${WORKDIR}/config/${SLUG}.json}"
FILES_ROOT="${WORKDIR}/files"
FILES_DIR="${FILES_ROOT}/usr"

die() { echo "[branded-iso] FAIL: $*" >&2; exit 1; }

echo "[branded-iso] tenant=${SLUG} config=${TENANT_CONFIG}"

# 1. Lire le manifeste + valider contre le schema.
TMP_CFG="$(mktemp)"
trap 'rm -f "$TMP_CFG"' EXIT
case "$TENANT_CONFIG" in
    http://*|https://*)
        # Aucun repli : si quelqu'un demande explicitement un manifeste servi
        # par le réseau, un échec doit casser le build. Un repli produirait une
        # image brandée avec un AUTRE manifeste que celui demandé.
        curl -fsSL "$TENANT_CONFIG" -o "$TMP_CFG" \
            || die "fetch de ${TENANT_CONFIG} impossible. La source par défaut est
       config/{slug}.json en dépôt ; aucun endpoint n'est déployé (#23813)."
        echo "[branded-iso] fetched from $TENANT_CONFIG"
        ;;
    *)
        [ -f "$TENANT_CONFIG" ] || die "manifeste introuvable : ${TENANT_CONFIG}"
        cp "$TENANT_CONFIG" "$TMP_CFG"
        echo "[branded-iso] read from ${TENANT_CONFIG}"
        ;;
esac
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
    #
    # ⚠️ Contrairement au manifeste ci-dessus, le repli est ici VOULU et
    # documenté (branding/README.md) : les assets des tenants in-repo vivent
    # dans branding/, et les URLs de config/*.json ne résolvent pas. Elles ne
    # servent qu'à nommer le fichier local (basename). Le repli est donc le
    # chemin NORMAL de ces tenants, pas une dégradation — d'où un message qui le
    # dit, plutôt qu'un « fallback » qui se lit comme un incident.
    local url="$1"
    local dest="$2"
    local fallback="${WORKDIR}/branding/$(basename "$url")"
    if curl -fsSL "$url" -o "$dest" 2>/dev/null; then
        echo "[branded-iso] fetched $(basename "$dest") from URL"
    elif [ -f "$fallback" ]; then
        cp "$fallback" "$dest"
        echo "[branded-iso] $(basename "$dest") from repo (${fallback}; URL non résolue, attendu)"
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

# 2bis. Papier peint de l'ecran de connexion (#25854) — sombre, pour que le
# formulaire reste lisible. Absent, le greeter garde celui du bureau.
LOGIN_SRC="${WORKDIR}/branding/login-wallpaper.png"
if [ -f "$LOGIN_SRC" ]; then
    cp "$LOGIN_SRC" "${FILES_DIR}/share/bluefox/branding/login-wallpaper.png"
    echo "[branded-iso] papier peint de connexion: $(du -h "$LOGIN_SRC" | cut -f1)"
else
    echo "[branded-iso] pas de papier peint de connexion (${LOGIN_SRC}) — celui du bureau servira"
fi

# 2c. Logo ANSI du renard (#25854) — lu par fastfetch dans un terminal.
# En couleurs vraies, donc reserve aux terminaux qui les rendent : la console
# texte du noyau garde le petit renard cyan de l'installateur.
ANSI_SRC="${WORKDIR}/branding/bluefoxos.ansi"
if [ -f "$ANSI_SRC" ]; then
    cp "$ANSI_SRC" "${FILES_DIR}/share/bluefox/branding/bluefoxos.ansi"
    echo "[branded-iso] logo ANSI: $(wc -l < "$ANSI_SRC") lignes"
else
    echo "[branded-iso] pas de logo ANSI (${ANSI_SRC}) — fastfetch gardera le logo de Fedora"
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
