#!/usr/bin/env bash
# files/scripts/depots-fedora-dans-etc.sh — rend les depots Fedora visibles a
# bootc-image-builder. Execute DANS l'image, par un module `script` des recipes.
#
# POURQUOI (mesure du 2026-11-20, #23941) : a partir de Fedora 45, le paquet
# fedora-repos range ses depots dans /usr/share/dnf5/repos.d/ (repertoire
# fournisseur de dnf5) et plus dans /etc/yum.repos.d/. dnf5 les lit tres bien ;
# bootc-image-builder, lui, ne regarde que /etc/yum.repos.d/ de l'image pour
# resoudre les paquets de l'installateur. Sur l'image bf 45.20261120.1, il n'y
# trouvait que copr, negativo17 et tailscale, et echouait sur « No match for
# argument: glibc ». AUCUNE ISO Fedora 45 ne se batissait plus.
#
# LE GESTE : copier chaque .repo du repertoire fournisseur dans /etc/yum.repos.d
# sous le MEME nom. Pour dnf5, un fichier de /etc portant le nom d'un fichier
# fournisseur le REMPLACE : pas de doublon de depot, et le contenu est celui
# du paquet fedora-repos de l'image (recopie a chaque construction).
# Un fichier deja present dans /etc n'est jamais ecrase.
#
# LA GARDE : apres la copie, /etc/yum.repos.d doit declarer un depot [fedora].
# Sinon on fait ECHOUER la construction de l'image plutot que de publier une
# image dont on ne pourra pas tirer d'ISO (on ne le decouvrirait qu'au moment
# de batir l'ISO, comme le 2026-11-20).
#
# BFOS_DEPOTS_SOURCE et BFOS_DEPOTS_CIBLE ne servent qu'aux essais.
set -euo pipefail

SOURCE="${BFOS_DEPOTS_SOURCE:-/usr/share/dnf5/repos.d}"
CIBLE="${BFOS_DEPOTS_CIBLE:-/etc/yum.repos.d}"

mkdir -p "$CIBLE"
copies=0
if [ -d "$SOURCE" ]; then
  for f in "$SOURCE"/*.repo; do
    [ -e "$f" ] || continue
    nom="$(basename "$f")"
    if [ -e "$CIBLE/$nom" ]; then
      echo "[depots-fedora] $CIBLE/$nom existe deja : garde tel quel"
      continue
    fi
    install -m 0644 "$f" "$CIBLE/$nom"
    copies=$((copies + 1))
    echo "[depots-fedora] copie : $nom"
  done
else
  echo "[depots-fedora] $SOURCE absent (Fedora anterieure a 45 ?) : rien a copier"
fi

if ! grep -h -q -E '^[[:space:]]*\[fedora\][[:space:]]*$' "$CIBLE"/*.repo 2>/dev/null; then
  echo "[depots-fedora] ERREUR : aucun depot [fedora] dans $CIBLE apres copie." >&2
  echo "               bootc-image-builder ne pourra pas batir d'ISO de cette image." >&2
  exit 1
fi
echo "[depots-fedora] $copies depot(s) copie(s) ; [fedora] present dans $CIBLE"
