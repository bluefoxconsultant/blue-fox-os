#!/usr/bin/env bash
# scripts/verify-image.sh — verifie une image Blue Fox OS TELLE QU'ELLE EST
# PUBLIEE sur le registre. Lecture seule : ni build, ni push, ni signature.
#
# Pourquoi ce script existe (audit P5.2, #21873)
# ---------------------------------------------
# `make verify` ne faisait qu'un `cosign verify`. Or l'audit du 2026-07-22 a
# trouve trois images qui passaient ce controle et qui etaient pourtant :
#   - sans attestation SBOM (B1),
#   - construites deux mois plus tot, sans lien vers leur commit source (B2).
# Une commande de verification qui ne regarde qu'un des trois liens de la
# chaine donne un feu vert qui ne vaut rien — c'est la meme famille de defaut
# que `verify-published`, garde-fou reel mais garde par une condition qui ne se
# realisait jamais sur l'evenement qu'il devait surveiller.
#
# Les 5 controles, dans l'ordre ou ils se cassent en pratique :
#   1. l'image est signee par la cle du depot
#   2. elle porte une attestation SBOM SPDX signee par la meme cle
#   3. ce SBOM decrit vraiment quelque chose (un SPDX vide est valide)
#   4. elle porte une revision qui existe, et qui est publiee sur le remote
#   5. elle porte le depot source (BlueBuild le laisse VIDE hors CI)
#
# Usage :
#   ./scripts/verify-image.sh                 # bf
#   ./scripts/verify-image.sh bf-surface
#   SLUG=factice ./scripts/verify-image.sh
#   ./scripts/verify-image.sh --all           # les 3 tenants
#
# Sortie non nulle des qu'un controle echoue. Les controles s'enchainent tous :
# on veut le tableau complet, pas le premier echec.

set -uo pipefail

REGISTRY="${REGISTRY:-ghcr.io/bluefoxconsultant}"
TAG="${TAG:-latest}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBKEY="${WORKDIR}/cosign.pub"

log() { echo "[verify] $*"; }
ok()   { echo "[verify]   ✅ $*"; }
bad()  { echo "[verify]   ❌ $*" >&2; }

for tool in cosign skopeo python3; do
    command -v "$tool" >/dev/null 2>&1 || { bad "$tool introuvable"; exit 1; }
done
[ -f "$PUBKEY" ] || { bad "cosign.pub introuvable a la racine du depot"; exit 1; }

# `cosign verify` ecrit son rapport sur stdout et ses avertissements sur stderr :
# on ne garde ni l'un ni l'autre, seul le code de sortie nous interesse.
verify_one() {
    local slug="$1"
    local image="${REGISTRY}/blue-fox-os-${slug}:${TAG}"
    local failures=0

    log "── ${image}"

    # 1. signature -----------------------------------------------------------
    if cosign verify --key "$PUBKEY" "$image" >/dev/null 2>&1; then
        ok "1/5 signature cosign valide"
    else
        bad "1/5 signature cosign INVALIDE ou absente"
        failures=$((failures + 1))
    fi

    # 2. attestation ---------------------------------------------------------
    # ⚠️ C'est le controle qui manquait : les 3 images ont passe deux mois
    # signees et jamais attestees, sans qu'aucune commande ne le dise.
    local att=""
    if att="$(cosign verify-attestation --key "$PUBKEY" --type spdx "$image" 2>/dev/null)"; then
        ok "2/5 attestation SBOM SPDX presente et signee"
    else
        bad "2/5 attestation SBOM SPDX ABSENTE (blocage B1 de l'audit P5.2)"
        failures=$((failures + 1))
    fi

    # 3. contenu du SBOM -----------------------------------------------------
    # Un SPDX sans aucun paquet est un document parfaitement valide. L'OOM de
    # syft du 2026-07-20 produisait exactement ca — attester ce fichier aurait
    # donne une attestation verte decrivant le vide.
    if [ -n "$att" ]; then
        local pkgs
        # ⚠️ Distinguer « SBOM vide » de « je n'ai pas su lire » : les deux
        # meritent un echec, mais pas le meme geste. Confondre les deux ferait
        # relancer une publication de 9 Go pour un parseur a corriger.
        pkgs="$(printf '%s' "$att" | python3 -c '
import base64, json, sys

# cosign rend une enveloppe DSSE par ligne ; le predicat est en base64.
# Selon la version et le --type, le document SPDX est soit le predicat
# lui-meme (spdxjson), soit une chaine sous predicate.Data (spdx).
best = None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        payload = json.loads(base64.b64decode(json.loads(line)["payload"]))
    except Exception:
        continue
    pred = payload.get("predicate")
    if isinstance(pred, dict) and isinstance(pred.get("Data"), str):
        try:
            pred = json.loads(pred["Data"])
        except Exception:
            pass
    if isinstance(pred, dict) and "packages" in pred:
        best = max(best or 0, len(pred["packages"]))

print("PARSE_FAIL" if best is None else best)
' 2>/dev/null || echo PARSE_FAIL)"
        if [ "$pkgs" = "PARSE_FAIL" ]; then
            bad "3/5 predicat SPDX illisible dans l'attestation — verifier le --type de cosign attest, PAS republier"
            failures=$((failures + 1))
        elif [ "$pkgs" -gt 0 ]; then
            ok "3/5 SBOM non vide (${pkgs} paquets)"
        else
            bad "3/5 SBOM VIDE : l'attestation est signee mais ne decrit rien"
            failures=$((failures + 1))
        fi
    else
        bad "3/5 non evalue — pas d'attestation a lire"
        failures=$((failures + 1))
    fi

    # 4. provenance ----------------------------------------------------------
    # Etiquette lue sur le REGISTRE, puis confrontee au depot : une revision
    # qu'on ne peut pas relire ne prouve rien de plus que pas de revision.
    local labels revision source
    labels="$(skopeo inspect "docker://${image}" 2>/dev/null)"
    revision="$(printf '%s' "$labels" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Labels",{}).get("org.opencontainers.image.revision",""))' 2>/dev/null || true)"
    source="$(printf '%s' "$labels" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Labels",{}).get("org.opencontainers.image.source",""))' 2>/dev/null || true)"

    if [ -z "$revision" ]; then
        bad "4/5 org.opencontainers.image.revision ABSENTE (blocage B2)"
        failures=$((failures + 1))
    elif [ "$revision" = '${BF_GIT_REVISION}' ]; then
        # shellexpand n'echoue pas sur variable absente : il publie le litteral.
        bad "4/5 revision publiee comme LITTERAL \${BF_GIT_REVISION} : variable non exportee au build"
        failures=$((failures + 1))
    elif ! git -C "$WORKDIR" cat-file -e "${revision%-dirty}^{commit}" 2>/dev/null; then
        bad "4/5 revision ${revision} inconnue du depot local (git fetch, ou commit jamais pousse)"
        failures=$((failures + 1))
    elif [ -z "$(git -C "$WORKDIR" branch -r --contains "${revision%-dirty}" 2>/dev/null)" ]; then
        bad "4/5 revision ${revision} sur aucune branche distante : personne d'autre ne peut la relire"
        failures=$((failures + 1))
    elif [ "$revision" != "${revision%-dirty}" ]; then
        bad "4/5 revision ${revision} : image construite sur un arbre SALE"
        failures=$((failures + 1))
    else
        ok "4/5 revision ${revision} relisible sur le remote"
    fi

    # 5. depot source --------------------------------------------------------
    # ⚠️ BlueBuild derive cette etiquette des variables de la CI : hors CI elle
    # vaut la chaine vide, et une revision sans depot ne designe rien.
    if [ -n "$source" ]; then
        ok "5/5 source ${source}"
    else
        bad "5/5 org.opencontainers.image.source VIDE : la revision ne renvoie a aucun depot"
        failures=$((failures + 1))
    fi

    if [ "$failures" -eq 0 ]; then
        log "   ${slug} : 5/5 ✅"
    else
        log "   ${slug} : ${failures} controle(s) en echec ❌"
    fi
    return "$failures"
}

TOTAL=0
if [ "${1:-}" = "--all" ]; then
    for slug in bf bf-surface factice; do
        verify_one "$slug" || TOTAL=$((TOTAL + $?))
    done
else
    verify_one "${1:-${SLUG:-bf}}" || TOTAL=$((TOTAL + $?))
fi

echo
if [ "$TOTAL" -eq 0 ]; then
    log "OK — tout est verifie."
else
    log "⚠️  ${TOTAL} controle(s) en echec. Une image qui echoue 2/5 ou 3/5 est"
    log "    publiee et signee mais sans inventaire : relancer make publish."
fi
exit "$([ "$TOTAL" -eq 0 ] && echo 0 || echo 1)"
