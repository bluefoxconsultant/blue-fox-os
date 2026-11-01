#!/usr/bin/env bash
# Pose du flux « bfos-live-navigateur » (#26870, 2026-10-24) sur l'Authentik de
# Blue Fox : session Authentik de Brave après le QR de la clé live. Ajoute un
# flux, une étape user_login et une politique d'expression ; ne touche à rien
# d'autre. Source : ~/blue-fox-os-live/live/authentik/bfos-live-navigateur.yaml
#
# Réversible : supprimer le flux « bfos-live-navigateur » (Flows), l'étape
# « bfos-live-navigateur-ouverture » et la politique « bfos-live-navigateur-jeton »,
# ou retirer le paramètre Odoo bf_policy_live.authentik_nav_url (le relais s'éteint).
#
# Lancer : bash <dossier du deploiement Authentik>/blueprints/apply-bfos-live-navigateur.sh
set -uo pipefail
SERVER="authentik-blue-fox-server"
PG="authentik-blue-fox-postgres"
AUTH_HOST="https://auth.bluefoxconsultant.com"
SRC="$HOME/blue-fox-os-live/live/authentik/bfos-live-navigateur.yaml"
BP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BP="$BP_DIR/bfos-live-navigateur.yaml"
say(){ printf '\n== %s ==\n' "$*"; }
pgq(){ docker exec "$PG" psql -U authentik -d authentik -tAc "$1" 2>/dev/null; }

say "1/4  copie du plan"
cmp -s "$SRC" "$BP" || cp "$SRC" "$BP"
docker cp "$BP" "$SERVER:/blueprints/bfos-live-navigateur.yaml" || { echo "docker cp a échoué"; exit 1; }

say "2/4  ak apply_blueprint (transactionnel)"
if ! docker exec "$SERVER" ak apply_blueprint bfos-live-navigateur.yaml >/tmp/ak_apply_nav.log 2>&1; then
  grep -iE '"error"|exception|traceback|invalid' /tmp/ak_apply_nav.log | tail -20; exit 1
fi

say "3/4  objets"
pgq "SELECT slug||' designation='||designation||' authentication='||authentication||' denied='||denied_action FROM authentik_flows_flow WHERE slug='bfos-live-navigateur';"
pgq "SELECT 'liaison ordre='||b.\"order\"||' plan='||b.evaluate_on_plan||' reeval='||b.re_evaluate_policies FROM authentik_flows_flowstagebinding b JOIN authentik_flows_flow f ON f.flow_uuid=b.target_id WHERE f.slug='bfos-live-navigateur';"
pgq "SELECT 'politique '||p.name FROM authentik_policies_policy p WHERE p.name='bfos-live-navigateur-jeton';"

say "4/4  sonde anonyme : code bidon → aucune session, retour sur Odoo"
J=$(mktemp); trap 'rm -f "$J"' EXIT
C=$(printf 'x%.0s' $(seq 43))
curl -s -c "$J" -b "$J" -o /dev/null "$AUTH_HOST/if/flow/bfos-live-navigateur/?c=$C"
curl -s -c "$J" -b "$J" -H 'Accept: application/json' \
  "$AUTH_HOST/api/v3/flows/executor/bfos-live-navigateur/?query=c%3D$C" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("component"), d.get("to"))'
curl -s -b "$J" -o /dev/null -w 'users/me : HTTP %{http_code} (403 attendu)\n' "$AUTH_HOST/api/v3/core/users/me/"
