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
#   COSIGN_PUB=<cle.pub> ./scripts/verify-image.sh   # autre cle que cosign.pub
#   ./scripts/verify-image.sh --all           # les 3 tenants
#   BFOS_LECTURES=1 ./scripts/verify-image.sh --all  # une seule lecture par
#                                             # controle, pour mesurer le
#                                             # flottement du lecteur (defaut 3)
#
# Sortie non nulle des qu'un controle echoue. Les controles s'enchainent tous :
# on veut le tableau complet, pas le premier echec.

set -uo pipefail

REGISTRY="${REGISTRY:-ghcr.io/bluefoxconsultant}"
TAG="${TAG:-latest}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
# COSIGN_PUB : la cle publique qui verifie (cosign.pub du depot par defaut).
# Image de transition signee par la cle de secours (README, « Cle de secours ») :
#   COSIGN_PUB=files/usr/share/bluefox/keys/cosign-images-secours.pub ./scripts/verify-image.sh
PUBKEY="${COSIGN_PUB:-${WORKDIR}/cosign.pub}"

log() { echo "[verify] $*"; }
ok()   { echo "[verify]   ✅ $*"; }
bad()  { echo "[verify]   ❌ $*" >&2; }

for tool in cosign skopeo python3; do
    command -v "$tool" >/dev/null 2>&1 || { bad "$tool introuvable"; exit 1; }
done
[ -f "$PUBKEY" ] || { bad "cle publique introuvable : ${PUBKEY} (COSIGN_PUB, cosign.pub par defaut)"; exit 1; }

# `cosign verify` ecrit son rapport sur stdout et ses avertissements sur stderr.
# En succes, on ne garde ni l'un ni l'autre. En ECHEC, on recopie les lignes
# d'erreur de stderr sous le ❌ (bf.note #289, 2026-11-09) : « ABSENTE » couvre
# aussi une lecture ratee (registre, Rekor, reseau). Le 2026-11-09, bf-surface
# est sortie 5/5 puis « attestation ABSENTE » a deux minutes d'ecart, sur le
# meme digest, sans aucune trace de la cause puisque stderr partait a la poubelle.
ERREURS="$(mktemp)"
SORTIE="$(mktemp)"
trap 'rm -f "$ERREURS" "$SORTIE"' EXIT
# Pas de ❌ sur ces lignes : bfos_verify_daily.sh compte les ❌ pour l'alerte.
cause() {
    local lignes
    lignes="$(grep -i -E 'error|erreur|status|timeout|refused|denied|EOF|no such|not found|MANIFEST_UNKNOWN|429|5[0-9][0-9]' "$ERREURS" \
        | grep -v '^error during command execution' | sort -u | head -n 3)"
    [ -n "$lignes" ] || lignes="$(grep -v '^[[:space:]]*$' "$ERREURS" | tail -n 2)"
    [ -n "$lignes" ] || lignes="(cosign n'a rien ecrit sur stderr)"
    printf '%s\n' "$lignes" | cut -c1-300 | sed 's/^/[verify]        cause : /' >&2
}

# ⚠️ UNE SEULE LECTURE REND UN VERDICT TIRE AU SORT (#27320, mesure du 2026-11-09).
# Sur la machine de build, cosign 3.1.3 refuse par intermittence une signature ou
# une attestation POURTANT PRESENTE : 4 echecs sur 9 lectures de la signature de
# bf, 2 sur 9 de son attestation, sur des entrees identiques, avec deux messages
# qui alternent (« no valid bundles exist in registry », « no matching
# attestations »). Ce n'est pas le registre (il sert l'index des referents 10 fois
# sur 10 depuis deux hotes), ni le reseau, ni le journal de transparence (l'echec
# survient avec ET sans --insecure-ignore-tlog), et de primetime1 (cosign 3.0.6)
# aucune lecture ne rate. L'alerte du 2026-11-09 au matin etait donc fausse : elle
# annoncait l'attestation de bf-surface absente deux minutes apres que la
# publication l'ait verifiee 5/5 sur le meme digest.
# On relit donc avant de crier. ⚠️ Mais la relecture ne CACHE pas le flottement :
# le nombre de lectures qu'il a fallu est imprime, sinon on remplace une fausse
# alerte par un silence qui ne dit plus que l'outil de lecture est malade.
# BFOS_LECTURES=1 retrouve l'ancien comportement (utile pour mesurer le flottement).
#
# Pourquoi CINQ lectures et une pause de 5 s : 40 lectures d'affilee de la meme
# attestation, le 2026-11-10, ont donne 12 echecs (30 %), en rafales de
# 1, 1, 1, 1, 1, 1, 2 et 4 echecs CONSECUTIFS. A ce taux, lire une fois fait crier
# faux presque chaque passage, trois lectures en laissent ~14 % (6 controles par
# passage), cinq ~1 %. Les 4 pauses de 5 s etalent les cinq lectures sur ~65 s,
# de quoi survivre a la rafale de 4 mesuree. Le cout n'est paye que quand ca va
# mal : une attestation vraiment absente prend 5 lectures par controle, ~7 min
# pour les 3 images, sous le delai de bfos_verify_daily.sh (900 s).
# ⚠️ Le taux est EPISODIQUE (six passages verts du 2 au 7 novembre, 30 % le 10) et
# les echecs arrivent en rafale : cinq lectures reduisent le faux cri, elles ne le
# guerissent pas. Une rafale plus longue que la fenetre passera encore.
LECTURES="${BFOS_LECTURES:-5}"
PAUSE_LECTURE="${BFOS_PAUSE_LECTURE:-5}"
ESSAIS=1
# ⚠️ Un reglage non numerique ferait boucler `relire` SANS FIN : `[ x -ge y ]` sort
# en erreur, donc la condition de sortie serait toujours fausse. On refuse net.
for _v in "$LECTURES" "$PAUSE_LECTURE"; do
    case "$_v" in
        ''|*[!0-9]*) bad "BFOS_LECTURES et BFOS_PAUSE_LECTURE doivent etre des entiers (recu : ${LECTURES} et ${PAUSE_LECTURE})"; exit 1 ;;
    esac
done
[ "$LECTURES" -ge 1 ] || LECTURES=1

# relire <fichier de sortie> <commande...> : rejoue jusqu'a $LECTURES fois,
# s'arrete au premier succes. La sortie standard du dernier essai va dans
# <fichier de sortie>, son stderr dans $ERREURS (que cause() recopie), et le
# nombre d'essais dans $ESSAIS.
relire() {
    local sortie="$1"; shift
    local n=0
    while :; do
        n=$((n + 1))
        if "$@" >"$sortie" 2>"$ERREURS"; then ESSAIS="$n"; return 0; fi
        [ "$n" -ge "$LECTURES" ] && break
        sleep "$PAUSE_LECTURE"
    done
    ESSAIS="$n"
    return 1
}

# « (2e lecture) » quand il a fallu relire, rien quand la premiere a suffi.
mention() { [ "${ESSAIS:-1}" -gt 1 ] && printf ' (%se lecture)' "$ESSAIS"; return 0; }

verify_one() {
    local slug="$1"
    local image="${REGISTRY}/blue-fox-os-${slug}:${TAG}"
    local failures=0

    log "── ${image}"

    # 1. signature -----------------------------------------------------------
    if relire /dev/null cosign verify --key "$PUBKEY" "$image"; then
        ok "1/5 signature cosign valide$(mention)"
    else
        bad "1/5 signature cosign INVALIDE ou absente (${ESSAIS} lecture(s))"
        cause
        failures=$((failures + 1))
    fi

    # 2. attestation ---------------------------------------------------------
    # ⚠️ C'est le controle qui manquait : les 3 images ont passe deux mois
    # signees et jamais attestees, sans qu'aucune commande ne le dise.
    local att=""
    if relire "$SORTIE" cosign verify-attestation --key "$PUBKEY" --type spdxjson "$image"; then
        att="$(cat "$SORTIE")"
        ok "2/5 attestation SBOM SPDX presente et signee$(mention)"
    else
        bad "2/5 attestation SBOM SPDX ABSENTE (blocage B1 de l'audit P5.2) (${ESSAIS} lecture(s))"
        cause
        failures=$((failures + 1))
    fi

    # 3. contenu ET COHERENCE des attestations -------------------------------
    # Un SPDX sans aucun paquet est un document parfaitement valide. L'OOM de
    # syft du 2026-07-20 produisait exactement ca — attester ce fichier aurait
    # donne une attestation verte decrivant le vide.
    #
    # ⚠️ Mais une image peut porter PLUSIEURS attestations, et
    # `cosign verify-attestation` reussit des qu'UNE SEULE verifie. Constate le
    # 2026-08-08 sur bf, qui portait deux documents SPDX signes de notre cle et
    # rattaches au meme digest :
    #
    #     ghcr.io/bluefoxconsultant/blue-fox-os-bf   8514 paquets
    #     /etc                                          1 paquet
    #
    # Le second etait un `syft scan` de /etc atteste contre l'image. La version
    # precedente de ce controle prenait le MAX du nombre de paquets : elle
    # annoncait « 8514 paquets ✅ » et masquait ainsi tres exactement le
    # document creux qu'elle avait pour mission d'attraper. Un consommateur qui
    # itere les attestations, ou qui prend la premiere, lit « cette image
    # contient un paquet ».
    #
    # On lit donc TOUTES les enveloppes, chacune doit decrire CETTE image, et
    # une seule incoherente suffit a faire echouer. Le nom attendu est celui que
    # generate-sbom.sh passe en --source-name.
    if [ -n "$att" ]; then
        local report rc
        # ⚠️ `local report rc` sur sa propre ligne : `local report="$(...)"`
        # ecraserait $? par le code de retour de `local` lui-meme, et toute
        # detection d'echec deviendrait muette.
        report="$(printf '%s' "$att" | BF_EXPECTED_NAME="${REGISTRY}/blue-fox-os-${slug}" python3 -c '
import base64, json, os, sys

# cosign rend une enveloppe DSSE par ligne ; le predicat est en base64.
# Selon la version et le --type, le document SPDX est soit le predicat
# lui-meme (spdxjson), soit une chaine sous predicate.Data (spdx).
expected = os.environ["BF_EXPECTED_NAME"]
docs = []
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
        docs.append((pred.get("name") or "(sans nom)", len(pred["packages"])))

if not docs:
    sys.exit(2)

bad_docs = 0
for name, n in docs:
    unit = "paquet" if n == 1 else "paquets"
    if name == expected and n > 0:
        print("OK\t%s\t%d %s\t-" % (name, n, unit))
    else:
        bad_docs += 1
        why = "ne decrit pas cette image" if name != expected else "aucun paquet"
        print("BAD\t%s\t%d %s\t%s" % (name, n, unit, why))
sys.exit(3 if bad_docs else 0)
' 2>/dev/null)"
        rc=$?
        # ⚠️ Trois issues distinctes, trois gestes distincts. Confondre
        # « illisible » et « incoherent » ferait relancer une publication de
        # 9 Go pour un parseur a corriger.
        if [ "$rc" -eq 0 ]; then
            ok "3/5 SBOM non vide, $(printf '%s\n' "$report" | grep -c . ) document(s) coherent(s)"
            printf '%s\n' "$report" | while IFS=$'\t' read -r _ name n _; do
                [ -n "$name" ] && echo "[verify]        ${name} : ${n}"
            done
        elif [ "$rc" -eq 3 ]; then
            bad "3/5 attestations INCOHERENTES : une image ne doit porter que des SBOM qui la decrivent"
            printf '%s\n' "$report" | while IFS=$'\t' read -r st name n why; do
                [ -n "$name" ] || continue
                if [ "$st" = "BAD" ]; then
                    bad "      ${name} : ${n} — ${why}"
                else
                    ok "      ${name} : ${n}"
                fi
            done
            failures=$((failures + 1))
        else
            bad "3/5 predicat SPDX illisible dans l'attestation — verifier le --type de cosign attest, PAS republier"
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
    # Meme relecture que pour cosign : un skopeo tombe une seule fois rendrait une
    # etiquette vide, donc un ❌ « revision ABSENTE » sur une image bien etiquetee.
    labels=""
    relire "$SORTIE" skopeo inspect "docker://${image}" && labels="$(cat "$SORTIE")"
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
    log "⚠️  ${TOTAL} controle(s) en echec."
    log "    2/5 ou « SBOM vide » : l'image est publiee et signee mais sans"
    log "    inventaire — relancer make publish."
    log "    « attestations INCOHERENTES » : republier n'y changera RIEN. Une"
    log "    attestation de trop se RETIRE (cosign clean / suppression du"
    log "    referent OCI) ; l'entree Rekor, elle, est definitive."
fi
exit "$([ "$TOTAL" -eq 0 ] && echo 0 || echo 1)"
