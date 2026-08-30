#!/usr/bin/env bash
# scripts/generate-sbom.sh — produit le SBOM SPDX d'une image PUBLIEE, en
# scannant son rootfs monte plutot que l'image elle-meme.
#
# ⚠️ DOIT tourner sous `podman unshare` : le montage d'image rootless exige
# d'etre dans le namespace utilisateur de podman.
#
#   podman unshare ./scripts/generate-sbom.sh <repo> <digest> <sortie.spdx.json>
#
# Pourquoi ce detour (audit P5.2 B1, #21873 / #22241)
# --------------------------------------------------
# `syft scan` sur une image (registry: ou oci-archive:) passe par stereoscope,
# qui decompresse chaque couche, en cache le contenu sur disque et construit un
# arbre de fichiers PAR COUCHE avant d'aplatir. Sur nos ~10 Go, mesure le
# 2026-07-26 sur charizard :
#
#   source            pic RSS    duree        cache TMPDIR   resultat
#   oci-archive:      18,7 Go    >38 min      15 Go          tue (swap epuise)
#   dir: (ce script)   3,4 Go    4 min 21 s    0             13 240 paquets
#
# GOMEMLIMIT ne pouvait pas sauver le premier cas : c'est une limite SOUPLE,
# elle augmente la pression du ramasse-miettes, elle ne plafonne pas un tas qui
# a reellement besoin de la place. Elle a ete depassee de 2,3x.
#
# Scanner le rootfs monte supprime la cause au lieu de la contenir : pas de
# decompression, pas de cache de couches, un seul arbre.
#
# ⚠️ Ce que ce detour ne doit PAS couter : la garantie que le SBOM decrit ce qui
# est PUBLIE. D'ou le tirage PAR DIGEST depuis le registre (jamais l'artefact
# local, dont le digest differe — bluebuild transforme l'image au push) et
# --source-name/--source-version, sans lesquels le document SPDX s'identifierait
# par le chemin du point de montage overlay. Un SBOM atteste dont le sujet est
# un chemin temporaire local ne prouve pas grand-chose.

set -euo pipefail

IMAGE_REPO="${1:?usage: generate-sbom.sh <repo> <digest> <sortie>}"
DIGEST="${2:?digest manquant (sha256:...)}"
OUT="${3:?chemin de sortie manquant}"

export TMPDIR="${SYFT_TMPDIR:-/var/tmp}"

log() { echo "[sbom] $*"; }
die() { echo "[sbom] ERREUR: $*" >&2; exit 1; }

command -v skopeo >/dev/null 2>&1 || die "skopeo introuvable"
command -v syft   >/dev/null 2>&1 || die "syft introuvable"
command -v podman >/dev/null 2>&1 || die "podman introuvable"

case "$DIGEST" in
    sha256:*) ;;
    *) die "digest attendu sous la forme sha256:... (recu « ${DIGEST} »)" ;;
esac

# Tag local unique et derive du digest : deux publications concurrentes ne se
# marchent pas dessus, et le nom dit de quoi il s'agit.
LOCAL_TAG="localhost/bfos-sbom:${DIGEST#sha256:}"

# Horodatage de depart : le menage des caches stereoscope ne touchera QUE ce que
# ce script a produit. Balayer tous les stereoscope-* casserait un scan
# concurrent, et ce script n'a pas a decider pour les autres.
STARTED_MARKER="$(mktemp "${TMPDIR}/.sbom-start.XXXXXX")"

MOUNTED=0
cleanup() {
    local rc=$?
    [ "$MOUNTED" = "1" ] && podman image umount "$LOCAL_TAG" >/dev/null 2>&1
    podman rmi "$LOCAL_TAG" >/dev/null 2>&1
    # ⚠️ Un syft interrompu laisse son cache de couches : ~15 Go par tentative
    # ratee, constate le 2026-07-26. Sans ce balayage, quelques echecs
    # remplissent le disque sans que rien ne le dise.
    find "$TMPDIR" -maxdepth 1 -name 'stereoscope-*' -newer "$STARTED_MARKER" \
        -exec rm -rf {} + 2>/dev/null || true
    rm -f "$STARTED_MARKER"
    return $rc
}
# INT/TERM en plus d'EXIT : sur un signal non piege, bash meurt SANS jouer le
# trap EXIT — c'est ce qui a laisse 15 Go derriere le 2026-07-26.
trap cleanup EXIT INT TERM

log "tirage par digest ${DIGEST}"
skopeo copy --retry-times 5 \
    "docker://${IMAGE_REPO}@${DIGEST}" \
    "containers-storage:${LOCAL_TAG}"

log "montage du rootfs"
MNT="$(podman image mount "$LOCAL_TAG")"
[ -n "$MNT" ] && [ -d "$MNT" ] \
    || die "montage echoue — ce script tourne-t-il bien sous « podman unshare » ?"
MOUNTED=1
log "   ${MNT}"

# Controle de sanite : sans base rpm, le catalogue serait quasi vide et on
# attesterait un inventaire creux sans une erreur.
[ -d "${MNT}/usr/lib/sysimage/rpm" ] || [ -d "${MNT}/var/lib/rpm" ] \
    || die "aucune base RPM dans le rootfs monte : le SBOM serait vide de paquets systeme."

log "scan syft du rootfs"
# ⚠️ INVENTAIRE DE PAQUETS, PAS DE FICHIERS (arbitrage tranche le 2026-08-08).
#
# Le defaut de syft est file.metadata.selection = "owned-by-package" : il
# catalogue chaque fichier possede par un paquet, avec ses empreintes. Mesure
# sur nos images :
#
#     selection            paquets   fichiers   poids du document
#     owned-by-package      8 514    157 018      121 Mo
#     none                  8 514          0       12,2 Mo
#
# Meme inventaire de paquets, facteur dix.
#
# ⚠️ DEUX reglages produisent cet inventaire de fichiers, et n'en couper qu'un
# ne change RIEN au poids (mesure le 2026-08-08) :
#
#     selection=none seul                      112 Mo, 154 978 fichiers
#     + package-file-ownership=false            36 Mo,   9 736 fichiers
#
# `file.metadata.selection` decide si syft calcule les EMPREINTES des fichiers ;
# c'est `relationships.package-file-ownership` qui decide s'il les LISTE, a
# partir de ce que la base RPM declare. Le residu de 9 736 vient des
# catalogueurs de langages (Go, Python, Cargo), qui referencent leurs binaires —
# on les garde, ils portent des paquets utiles aux CVE.
#
# Le niveau fichier n'apporterait rien ici que l'image ne donne deja : elle est
# UN digest signe, donc chaque fichier y est deja verifie de bout en bout.
# L'inventaire de fichiers gagne sa place sur un systeme mutable, pas sur du
# rpm-ostree. Le seul contenu hors RPM (les 56 fichiers du module `files:`) est
# rattache au commit par org.opencontainers.image.revision, que le controle 4/5
# verifie.
#
# Surchargeable pour un scan medico-legal ponctuel, mais JAMAIS en silence :
# la valeur effective est journalisee, parce qu'un retour a "owned-by-package"
# fait echouer l'attestation 40 minutes plus tard, sur un 502 qui ne dit pas
# pourquoi.
# ⚠️ NE PAS COUPER `relationships.package-file-ownership-overlap`. Essaye le
# 2026-08-29, mesure a l'appui : le document passe de 23,2 a 30,8 Mio. syft
# refuse ce reglage seul (« cannot enable exclude-binary-overlap-by-ownership
# without enabling package-file-ownership-overlap »), et le couper AUSSI fait
# garder tous les paquets binaires synthetiques que la deduplication retirait —
# 8 534 paquets deviennent 9 564, et 5 045 fichiers deviennent 17 851. Le
# reglage qui semblait alleger est celui qui alourdit.
export SYFT_FILE_METADATA_SELECTION="${SYFT_FILE_METADATA_SELECTION:-none}"
export SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP="${SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP:-false}"
log "   file.metadata.selection      = ${SYFT_FILE_METADATA_SELECTION}"
log "   package-file-ownership       = ${SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP}"

# --source-name / --source-version : le document doit s'identifier par la
# reference PUBLIEE, pas par le chemin overlay du montage.
syft scan "dir:${MNT}" \
    --source-name "$IMAGE_REPO" \
    --source-version "$DIGEST" \
    -o "spdx-json=${OUT}"

[ -s "$OUT" ] || die "SBOM vide apres le scan."

# --- projection au niveau paquet -------------------------------------------
# Le document atteste est un INVENTAIRE DE PAQUETS, et c'est une decision, pas
# un accident de taille. Sur du rpm-ostree, l'image est UN digest signe : chaque
# fichier y est deja verifie de bout en bout, et une liste de fichiers dans le
# SBOM ne prouve rien de plus. L'inventaire de fichiers gagne sa place sur un
# systeme mutable.
#
# Ce que ca retire, mesure sur bf le 2026-08-29 :
#
#     document syft complet                       23,2 Mio  -> enveloppe 30,9 Mio  502
#     sans fichiers, relations paquets gardees    19,8 Mio  -> enveloppe 26,4 Mio  502
#     inventaire de paquets seul                  12,3 Mio  -> enveloppe 16,4 Mio  OK
#
# Seule la troisieme forme passe sous le plafond de la bordure Rekor, et c'est
# exactement la forme du document du 2026-08-08 qui, lui, avait ete atteste.
#
# ⚠️ La perte reelle est le graphe de dependances (DEPENDENCY_OF). Elle est
# assumee : chaque paquet garde son nom, sa version, sa licence et son purl,
# donc tout ce dont un rapprochement CVE a besoin. La relation DESCRIBES est
# conservee — c'est elle qui dit ce que le document decrit.
python3 - "$OUT" <<'PY'
import json, sys

chemin = sys.argv[1]
doc = json.load(open(chemin))
avant = len(doc.get("files", [])), len(doc.get("relationships", []))
doc.pop("files", None)
doc["relationships"] = [r for r in doc.get("relationships", [])
                        if r.get("relationshipType") == "DESCRIBES"]
with open(chemin, "w") as fh:
    json.dump(doc, fh, separators=(",", ":"))
print(f"[sbom]    projection : {avant[0]} fichiers et "
      f"{avant[1] - len(doc['relationships'])} relations retires "
      f"({len(doc['relationships'])} DESCRIBES gardee(s))")
PY

PKGS="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1])).get("packages",[])))' "$OUT")"
[ "$PKGS" -gt 0 ] || die "SBOM sans aucun paquet : document valide mais creux."

# Garde-fou de taille — et il garde bien Rekor, contrairement a ce qui etait
# ecrit ici.
#
# ⚠️ CE COMMENTAIRE A AFFIRME LE CONTRAIRE PENDANT TROIS SEMAINES. Il disait que
# la requete vers Rekor pese 652 octets quelle que soit la taille du SBOM, donc
# que la taille n'avait rien a voir avec les 502. C'etait tire de la lecture d'un
# bundle `hashedrekord` — l'artefact que produit `cosign sign`, PAS `cosign
# attest`. On avait mesure l'artefact voisin.
#
# Ce qui a ete MESURE le 2026-08-29, en sondant l'API avec des corps
# volontairement invalides (rien n'est jamais ecrit dans le journal) :
#
#     corps POST /api/v1/log/entries    reponse
#     20,00 Mio                         422   (rejete par l'application)
#     23,00 Mio                         422
#     24,00 Mio                         422
#     25,00 Mio                         502   (mort a la bordure)
#     28 / 30 / 31 / 32 Mio             502
#
# Reproductible, depuis deux machines et deux reseaux, deux fois chaque palier.
# Le plafond de la bordure Rekor est donc entre 24 et 25 Mio de corps.
#
# Et le corps que cosign envoie n'est pas un demi-kilo : le trafic sortant
# mesure pendant un cycle de reessai est de 8,2 Mio. L'enveloppe DSSE porte le
# predicat encode en base64, soit environ 4/3 de sa taille sur disque — et
# l'envoi est interrompu des que la bordure repond.
#
# D'ou le seuil : le predicat doit rester sous ~18 Mio pour que son encodage
# tienne sous les 24 Mio du plafond. Un SBOM plus gros ne fait pas echouer
# l'attestation « parfois » : il ne peut PAS passer.
SBOM_MAX_MB="${SBOM_MAX_MB:-18}"
SBOM_MB="$(( $(stat -c %s "$OUT") / 1048576 ))"
[ "$SBOM_MB" -le "$SBOM_MAX_MB" ] || die \
    "SBOM de ${SBOM_MB} Mio : son enveloppe base64 fera ~$(( SBOM_MB * 4 / 3 )) Mio,
au-dela du plafond de la bordure Rekor (entre 24 et 25 Mio, mesure 2026-08-29).
cosign attest rendra un 502 a tous les coups, apres le push de l'image.
Plafond local : ${SBOM_MAX_MB} Mio. A verifier, dans cet ordre :
  la projection au niveau paquet a-t-elle bien tourne (elle se journalise) ?
  SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP (attendu « false », vu « ${SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP} »)
  SYFT_FILE_METADATA_SELECTION              (attendu « none », vu « ${SYFT_FILE_METADATA_SELECTION} »)
⚠️ NE PAS couper package-file-ownership-overlap : mesure faite, ca ALOURDIT."

log "OK — $(du -h "$OUT" | cut -f1), ${PKGS} paquets, sujet ${IMAGE_REPO}@${DIGEST}"
