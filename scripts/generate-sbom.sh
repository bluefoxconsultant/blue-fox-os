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
# 2026-07-26 sur le poste de construction :
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

# ⚠️⚠️ EXCLURE LE DEPOT OSTREE, SANS QUOI LE SBOM DECRIT L HISTOIRE ET PAS
# L IMAGE (trouve le 2026-09-13, il courait depuis toujours).
#
# Une image rpm-ostree porte `/sysroot/ostree/repo/objects/` — 7,3 Go sur bf —
# et `/ostree` y est un lien symbolique. Ces objets sont ceux du commit de BASE,
# rpmdb comprise. `syft scan dir:` les lit et catalogue des paquets que l image
# N A PLUS : `plasma-welcome`, retire le 2026-09-11, figurait encore au SBOM du
# 2026-09-13, avec son propre aveu dans le document —
#
#     "sourceInfo": "acquired package info from RPM DB:
#                    /sysroot/ostree/repo/objects/40/db53....file"
#
# Le SBOM a donc servi de preuve pendant deux jours que le retrait n avait pas
# pris, alors que le binaire, le .desktop et le module KDED etaient bel et bien
# absents de /usr. Le tell qui aurait du alerter : `firefox`, retire par la MEME
# transaction, ne reapparait pas — parce qu aucun objet de base ne le
# reintroduit par ce chemin. Une sonde qui ne ment que sur la moitie des cas est
# plus couteuse qu une sonde muette.
#
# --source-name / --source-version : le document doit s'identifier par la
# reference PUBLIEE, pas par le chemin overlay du montage.
syft scan "dir:${MNT}" \
    --exclude './sysroot/**' \
    --exclude './ostree/**' \
    --source-name "$IMAGE_REPO" \
    --source-version "$DIGEST" \
    -o "spdx-json=${OUT}"

[ -s "$OUT" ] || die "SBOM vide apres le scan."

# La garde qui prouve l exclusion par contre-exemple : syft ecrit dans
# `sourceInfo` d ou il tient chaque paquet. Si un seul vient encore du depot
# ostree, l inventaire decrit deux systemes a la fois et on ne l atteste pas.
python3 - "$OUT" <<'PYGARDE' || die "le scan a lu le depot ostree : inventaire non fiable, voir ci-dessus."
import json, sys
doc = json.load(open(sys.argv[1]))
fantomes = [p.get("name", "?") for p in doc.get("packages", [])
            if "/sysroot/ostree" in (p.get("sourceInfo") or "")]
if fantomes:
    apercu = ", ".join(sorted(set(fantomes))[:5])
    print(f"[sbom] {len(fantomes)} paquet(s) lus dans /sysroot/ostree : {apercu}"
          " ... — les exclusions --exclude ne mordent plus.", file=sys.stderr)
    sys.exit(1)
PYGARDE

# --- projection au niveau paquet -------------------------------------------
# Le document atteste est un INVENTAIRE DE PAQUETS, et c'est une decision, pas
# un accident de taille. Sur du rpm-ostree, l'image est UN digest signe : chaque
# fichier y est deja verifie de bout en bout, et une liste de fichiers dans le
# SBOM ne prouve rien de plus. L'inventaire de fichiers gagne sa place sur un
# systeme mutable.
#
# DEUX RETRAITS, ET LE SECOND PESE PLUS QUE LE PREMIER
#
# Mesures sur bf le 2026-08-29, `cosign attest-blob` contre le vrai Rekor
# (attest-blob ne touche aucun registre : rien a nettoyer si ca rate) :
#
#     document syft complet                       23,2 Mio   502
#     sans fichiers, relations paquets gardees    19,8 Mio   502
#     sans fichiers ni relations                  11,8 Mio   502
#     ... et en plus sans les CPE devines          5,3 Mio   OK
#
# Bissection du seuil d'acceptation, meme jour, meme commande :
#     0,1 / 2,5 / 5,0 / 7,0 / 9,4 Mio  -> acceptes
#     11,8 Mio                          -> 502
#
# ⚠️ Ce seuil-la (entre 9,4 et 11,8 Mio) est celui d'une entree VALIDE. Il est
# BIEN PLUS BAS que le plafond de 24 Mio que rend une sonde a corps invalide :
# un corps invalide est rejete a l'analyse, il n'atteint jamais le traitement.
# Mesurer avec des corps invalides donne donc un plafond trop genereux — c'est
# l'erreur que la version precedente de ce commentaire a faite.
#
# CE QUI SORT DU DOCUMENT, ET POURQUOI
#
# 1. Les fichiers et les relations. Sur du rpm-ostree l'image est UN digest
#    signe : chaque fichier y est deja verifie de bout en bout, une liste de
#    fichiers dans le SBOM ne prouve rien de plus. La perte reelle est le graphe
#    de dependances ; elle est assumee. La relation DESCRIBES reste, c'est elle
#    qui dit ce que le document decrit.
#
# 2. Les references CPE. C'est le gros morceau : 7,77 Mio des 11,8, soit les
#    deux tiers du document. syft en devine 48 691 pour 8 533 purls — pres de
#    six par paquet. Le purl, lui, est canonique : pour un RPM il porte le nom,
#    la version, l'architecture et la distribution. Les CPE sont des hypotheses
#    que les outils de rapprochement (grype, trivy) refont eux-memes a partir du
#    purl. On garde donc l'identifiant, pas les hypotheses.
#
# Chaque paquet conserve nom, version, licences, fournisseur, empreintes et
# purl. Aucun paquet n'est retire : 8 534 avant, 8 534 apres.
python3 - "$OUT" <<'PY'
import json, sys

chemin = sys.argv[1]
doc = json.load(open(chemin))
n_fichiers = len(doc.get("files", []))
n_relations = len(doc.get("relationships", []))

doc.pop("files", None)
doc["relationships"] = [r for r in doc.get("relationships", [])
                        if r.get("relationshipType") == "DESCRIBES"]

n_cpe = 0
for paquet in doc.get("packages", []):
    refs = paquet.get("externalRefs")
    if not refs:
        continue
    purls = [r for r in refs if r.get("referenceType") == "purl"]
    n_cpe += len(refs) - len(purls)
    if purls:
        paquet["externalRefs"] = purls
    else:
        # Pas de purl : on ne laisse pas le paquet sans identifiant, les CPE
        # restent alors sa seule prise.
        pass

with open(chemin, "w") as fh:
    json.dump(doc, fh, separators=(",", ":"))

print(f"[sbom]    projection : {n_fichiers} fichiers, "
      f"{n_relations - len(doc['relationships'])} relations et "
      f"{n_cpe} references CPE retirees "
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
# ⚠️ ET IL Y A DEUX PLAFONDS, PAS UN. Les confondre donne un seuil trois fois
# trop genereux, ce qui est arrive en cours de route le 2026-08-29 :
#
#   1. Le plafond de l'ANALYSE, mesure avec des corps volontairement invalides
#      (rien n'est jamais ecrit dans le journal) : 20 / 23 / 24 Mio -> 422,
#      25 / 28 / 30 / 31 / 32 Mio -> 502. Reproductible depuis deux machines et
#      deux reseaux. Mais un corps invalide est rejete AVANT tout traitement :
#      ce plafond ne dit rien de ce qu'une vraie entree peut faire passer.
#
#   2. Le plafond des entrees VALIDES, mesure avec `cosign attest-blob` contre
#      le vrai Rekor : 0,1 / 2,5 / 5,0 / 7,0 / 9,4 Mio -> acceptes ;
#      11,8 Mio -> 502. C'est CELUI-LA qui compte, et il est bien plus bas.
#
# Le corps que cosign envoie n'est pas un demi-kilo, contrairement a ce qui
# etait ecrit ici : le trafic sortant mesure pendant un cycle de reessai est de
# 8,2 Mio. L'enveloppe DSSE porte bien le predicat.
#
# D'ou le seuil de 9 Mio, sous le premier refus observe (11,8) avec une marge.
# Apres la projection ci-dessus, bf sort a 5,3 Mio : la marge est confortable,
# et elle doit le rester — un SBOM au-dela ne fait pas echouer l'attestation
# « parfois », il ne peut PAS passer.
SBOM_MAX_MB="${SBOM_MAX_MB:-9}"
SBOM_MB="$(( $(stat -c %s "$OUT") / 1048576 ))"
[ "$SBOM_MB" -le "$SBOM_MAX_MB" ] || die \
    "SBOM de ${SBOM_MB} Mio, au-dela du plafond des entrees valides de Rekor
(premier refus observe a 11,8 Mio, dernier succes a 9,4 — mesure 2026-08-29).
cosign attest rendra un 502 a tous les coups, apres le push de l'image.
Plafond local : ${SBOM_MAX_MB} Mio. A verifier, dans cet ordre :
  la projection au niveau paquet a-t-elle bien tourne (elle se journalise) ?
  SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP (attendu « false », vu « ${SYFT_RELATIONSHIPS_PACKAGE_FILE_OWNERSHIP} »)
  SYFT_FILE_METADATA_SELECTION              (attendu « none », vu « ${SYFT_FILE_METADATA_SELECTION} »)
⚠️ NE PAS couper package-file-ownership-overlap : mesure faite, ca ALOURDIT."

log "OK — $(du -h "$OUT" | cut -f1), ${PKGS} paquets, sujet ${IMAGE_REPO}@${DIGEST}"
