#!/bin/bash
# Prépare l'arbre de l'ISO de TOURNAGE (#26870, vidéo « Blue Fox OS sur clé USB ») :
# une COPIE du dépôt où la clé vise le banc fictif (Authentik et Odoo jetables, usager
# fictif de Boréal) au lieu de Blue Fox. L'ISO normale n'est jamais touchée.
#
#   live/tournage/preparer-arbre-tournage.sh [<destination>]   (défaut ~/blue-fox-os-tournage)
#   puis : <destination>/live/image/construire-iso.sh tournage-<date>
#
# Remplace dans la copie :
#   live/image/files/etc/bfos-live/live.json   <- live/tournage/live.json
#   live/image/files/usr/share/bluefox/tenant.json  <- live/tournage/tenant.json (ajouté :
#     sans lui, l'image garde le tenant.json de Blue Fox et Brave afficherait nos vrais
#     favoris et services)
# ⚠️ Une ISO de tournage ne se distribue pas : elle ne parle qu'au banc.
set -euo pipefail
ICI=$(cd "$(dirname "$0")" && pwd)
DEPOT=$(cd "$ICI/../.." && pwd)
DEST=${1:-$HOME/blue-fox-os-tournage}
[ "$DEST" != "$DEPOT" ] || { echo "la destination ne peut pas être le dépôt" >&2; exit 1; }
rsync -a --delete --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache "$DEPOT/" "$DEST/"
install -m 0644 "$ICI/live.json" "$DEST/live/image/files/etc/bfos-live/live.json"
install -D -m 0644 "$ICI/tenant.json" "$DEST/live/image/files/usr/share/bluefox/tenant.json"
# La cle de tournage vise le banc, jamais Blue Fox.
! grep -q 'bluefoxconsultant' "$DEST/live/image/files/etc/bfos-live/live.json"
python3 -c "import json,sys; s=json.load(open(sys.argv[1]))['services']; assert 'bluefoxconsultant' not in json.dumps(s)" \
    "$DEST/live/image/files/usr/share/bluefox/tenant.json"
echo "arbre de tournage prêt : $DEST (construire : $DEST/live/image/construire-iso.sh tournage-<date>)"
