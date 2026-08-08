#!/usr/bin/env bash
# scripts/publish-image.sh — build + push + signature + SBOM + attestation
# d'une image OCI Blue Fox OS, en local.
#
# Remplace les jobs GitHub Actions `build` et `attest-sbom`, retires le
# 2026-07-16 : les images OCI se construisent desormais sur la machine de
# build, au meme endroit que l'assemblage de l'ISO. Le workflow GH ne fait
# plus que de la validation (voir .github/workflows/build.yml).
#
# Usage :
#   COSIGN_PRIVATE_KEY="$(cat /chemin/cosign.key)" ./scripts/publish-image.sh bf
#   SLUG=bf-surface ./scripts/publish-image.sh
#   DRY_RUN=1 ./scripts/publish-image.sh bf      # build local, aucun push
#
# TAG (defaut: latest) ne pilote QUE les etapes 5 a 7 (SBOM, attestation,
# verification). C'est bluebuild qui decide des tags qu'il pousse, a partir de
# la recipe — il publie :latest plus un tag calendaire type 44.20260716. Mettre
# TAG=autre chose sans qu'un tel tag vienne d'etre pousse fait donc attester
# une image plus ancienne, sans erreur visible. En pratique : ne pas y toucher.
#
# Prerequis :
#   - bluebuild, cosign, syft, skopeo, podman
#     Sur Garuda/Arch : ./scripts/bootstrap-garuda.sh les installe (ni bluebuild
#     ni syft ne sont packages pour Arch — ils passent par leurs installeurs
#     upstream). Sur Fedora, tout est dans dnf.
#   - auth GHCR :
#       gh auth login          # une fois, interactif
#       gh auth token | podman login ghcr.io -u "$(gh api user -q .login)" --password-stdin
#     `gh` doit rester disponible : le preflight en derive BB_USERNAME/BB_PASSWORD
#     pour bluebuild, qui ne sait pas lire la session podman. Contournement si
#     `gh` est absent : exporter BB_USERNAME et BB_PASSWORD soi-meme.
#   - COSIGN_PRIVATE_KEY = contenu de cosign.key (PAS le chemin)
#   - ~30 GB libres : l'image Kinoite fait ~9 GB, et l'etape 5 en tire une copie
#     de plus dans le stockage podman (retiree en fin d'etape).
#     ⚠️ Sur Garuda /tmp est un tmpfs, donc de la RAM : ne jamais y pointer
#     SYFT_TMPDIR (defaut /var/tmp, sur le disque).
#   - un arbre git propre, pousse, a jour sur son remote de suivi. Refus de
#     publier sinon (#23810) — voir la section « provenance » plus bas.
#     PUBLISH_ALLOW_UNCLEAN=1 passe outre, en le disant, et etiquette alors la
#     revision « <sha>-dirty ».
#
# Portable hors Fedora : l'etape 1 (RPM) se construit dans un container
# fedora:43 — build-welcome-rpm.sh detecte l'absence des macros RPM Fedora et
# rebondit tout seul dans podman. Rien d'autre dans la chaine n'est Fedora.
#
# Enchainement (miroir de l'ancien pipeline CI) :
#   1. RPM welcome        <- scripts/build-welcome-rpm.sh
#   2. stage du RPM       <- files/usr/share/bluefox/rpm-staging/
#   3. branding + KDE     <- scripts/build_branded_iso.sh (BUILD=0)
#   4. build + push + sig <- bluebuild
#   5. SBOM SPDX          <- scripts/generate-sbom.sh (skopeo + syft, sous
#                            podman unshare : scan du rootfs monte)
#   6. attestation        <- cosign attest
#   7. verification       <- cosign verify + verify-attestation + provenance

set -euo pipefail

SLUG="${1:-${SLUG:-bf}}"
TAG="${TAG:-latest}"
REGISTRY="${REGISTRY:-ghcr.io/bluefoxconsultant}"
IMAGE="${REGISTRY}/blue-fox-os-${SLUG}:${TAG}"
# Sans le tag : l'etape 5 tire l'image PAR DIGEST, un tag ne designant pas un
# artefact stable (bluebuild ecrase :latest a chaque push).
IMAGE_REPO="${REGISTRY}/blue-fox-os-${SLUG}"
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN="${DRY_RUN:-0}"

log() { echo "[publish] $*"; }

# PUSHED passe a 1 des que l'image est en ligne : au-dela, tout echec laisse un
# artefact publie et incomplet, et doit le dire. Voir post_push_notice().
PUSHED=0
die() {
    echo "[publish] ERREUR: $*" >&2
    [ "$PUSHED" = "1" ] && post_push_notice
    exit 1
}

# ⚠️ Defaut structurel releve par l'audit P5.2 : les etapes 5 a 7 operent sur
# une image DEJA en ligne. Un echec de syft, de cosign attest ou du controle de
# provenance laisse donc une image publiee et signee, sans SBOM — l'etat exact
# du registre pendant deux mois. On ne peut pas inverser l'ordre sans attester un
# artefact local suppose identique au publie — or il ne l'est pas : bluebuild
# transforme l'image au push, et le digest local differe du digest publie
# (verifie le 2026-07-20). L'etape 5 retire donc du registre, par digest. Ce
# qu'on peut faire, c'est ne jamais sortir en silence sur cette fenetre.
post_push_notice() {
    echo "[publish] ---" >&2
    echo "[publish] ⚠️  ${IMAGE} est EN LIGNE et signee, mais la chaine n'est" >&2
    echo "[publish]     pas allee au bout : SBOM, attestation ou provenance" >&2
    echo "[publish]     manquants. L'image publiee n'est PAS complete." >&2
    echo "[publish]     Remediation : relancer ce script, ou retirer le tag du" >&2
    echo "[publish]     registre. Ne pas laisser cet etat en place — c'est" >&2
    echo "[publish]     precisement le blocage B1 de l'audit P5.2." >&2
}

# --- COSIGN_PASSWORD -------------------------------------------------------
# cosign veut cette variable DEFINIE, meme vide. La cle BlueBuild est un
# ENCRYPTED SIGSTORE PRIVATE KEY (scrypt) quelle que soit la passphrase — la
# convention BlueBuild etant justement de la generer SANS passphrase. Si la
# variable n'existe pas, cosign tente de lire la passphrase au terminal et
# meurt des qu'il n'y a pas de TTY :
#   Enter password for private key: Error: ... reading key:
#   inappropriate ioctl for device
# `${VAR-}` ne se substitue que si VAR est *unset* : une valeur vide fournie
# explicitement est donc preservee. Verifie sur cosign v3.0.6.
export COSIGN_PASSWORD="${COSIGN_PASSWORD-}"

# --- preflight -------------------------------------------------------------
# podman sert au build ET au rebond fedora:43 de l'etape 1. cosign/syft/skopeo
# ne servent qu'aux etapes 5-7 : inutile de les exiger pour un DRY_RUN.
# ⚠️ skopeo n'est plus optionnel depuis que l'etape 5 tire l'image par digest
# (il l'etait quand il ne servait qu'au repli de lecture d'etiquette, etape 7).
REQUIRED_TOOLS=(bluebuild podman)
[ "$DRY_RUN" = "0" ] && REQUIRED_TOOLS+=(cosign syft skopeo)

for tool in "${REQUIRED_TOOLS[@]}"; do
    command -v "$tool" >/dev/null 2>&1 \
        || die "$tool introuvable. Sur Garuda/Arch : ./scripts/bootstrap-garuda.sh"
done

[ -f "${WORKDIR}/recipes/${SLUG}.yml" ] || die "recipe recipes/${SLUG}.yml introuvable"

if [ "$DRY_RUN" = "0" ]; then
    [ -n "${COSIGN_PRIVATE_KEY:-}" ] || die "COSIGN_PRIVATE_KEY vide. Exporter le CONTENU de cosign.key, pas son chemin."

    # Le RPM du welcome agent etait le seul paquet superpose non signe de
    # l'image (#23811). C'est la chaine de PUBLICATION qui l'interdit, pas
    # chaque rpmbuild : la CI de validation et les builds de dev n'ont pas de
    # cle, et n'en ont pas besoin.
    [ -n "${BLUEFOX_RPM_GPG_NAME:-}" ] || die \
        "BLUEFOX_RPM_GPG_NAME vide : le RPM bluefox-welcome serait non signe.
Creer la cle une fois avec ./scripts/generate-rpm-signing-key.sh, puis exporter
son uid. Les recipes verifient la signature avant rpm-ostree install, donc un
build sans cle echouerait de toute facon — autant echouer ici, avant 9 GB."
    podman login --get-login ghcr.io >/dev/null 2>&1 \
        || die "pas authentifie sur ghcr.io. Voir les prerequis en tete de script."

    # --- credentials pour bluebuild --------------------------------------
    # ⚠️ Hors CI, `bluebuild build --push` EXIGE --registry / --username /
    # --password : il ne lit PAS la session podman et ne devine PAS le
    # registre depuis le remote git. Sans eux il retombe sur `localhost` et
    # meurt en boucle sur « pinging container registry localhost: dial tcp
    # [::1]:443: connect: connection refused ». Le cas ne s'etait jamais
    # presente tant que personne n'avait pousse depuis la machine locale.
    # On passe par l'ENVIRONNEMENT (BB_*) et non par des arguments : une
    # ligne de commande est lisible par n'importe qui via `ps`.
    export BB_REGISTRY="${BB_REGISTRY:-${REGISTRY%%/*}}"
    export BB_REGISTRY_NAMESPACE="${BB_REGISTRY_NAMESPACE:-${REGISTRY#*/}}"
    if [ -z "${BB_USERNAME:-}" ] || [ -z "${BB_PASSWORD:-}" ]; then
        command -v gh >/dev/null 2>&1 \
            || die "BB_USERNAME/BB_PASSWORD non definis et 'gh' introuvable pour les deriver."
        BB_USERNAME="${BB_USERNAME:-$(gh api user -q .login)}"
        BB_PASSWORD="${BB_PASSWORD:-$(gh auth token)}"
        export BB_USERNAME BB_PASSWORD
    fi
    [ -n "$BB_PASSWORD" ] || die "BB_PASSWORD vide (gh auth token n'a rien rendu ?)."
    log "registre bluebuild: ${BB_REGISTRY}/${BB_REGISTRY_NAMESPACE} (utilisateur ${BB_USERNAME})"
fi

# --- provenance : etat de l'arbre + revision -------------------------------
# Audit P5.2 du 2026-07-22, blocage B2 : blue-fox-os-bf:latest a ete construite
# le 2026-07-22 mais taguee f2661e5-44, commit du 2026-05-18 — deux mois de
# publications depuis un checkout perime, sans un signal. Ce qui l'a revele
# n'est pas un controle mais un LABEL : le prenom corrige en local le 2026-07-16
# etait toujours dans org.opencontainers.image.description publie.
#
# Deux manques a combler, et ils vont ensemble :
#   1. l'image ne portait aucun lien verifiable vers son commit source
#      (org.opencontainers.image.revision absent) ;
#   2. rien n'empechait de publier depuis un arbre sale ou en retard.
# Etiqueter sans controler donne une provenance exacte... vers un commit que
# personne d'autre ne peut relire. Controler sans etiqueter laisse le lien
# invisible depuis le registre. D'ou les deux ici.
#
# L'etiquette elle-meme est posee par les recipes (`labels:` -> ${BF_GIT_REVISION}).
# ⚠️ BlueBuild resout ces valeurs via shellexpand SANS erreur sur variable
# absente : une variable non exportee ne casse rien, elle publie le litteral
# « ${BF_GIT_REVISION} » comme revision. C'est pourquoi l'etape 7 relit
# l'etiquette au lieu de faire confiance a l'export.
git -C "$WORKDIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "pas un arbre git : impossible d'etablir la provenance de l'image."

BF_GIT_REVISION="$(git -C "$WORKDIR" rev-parse HEAD)"

if [ "$DRY_RUN" = "0" ]; then
    VIOLATIONS=()

    [ -z "$(git -C "$WORKDIR" status --porcelain)" ] \
        || VIOLATIONS+=("arbre de travail sale (git status --porcelain non vide)")

    UPSTREAM="$(git -C "$WORKDIR" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
    if [ -z "$UPSTREAM" ]; then
        VIOLATIONS+=("branche sans remote de suivi : la revision etiquetee ne serait relisible par personne")
    elif ! git -C "$WORKDIR" fetch --quiet 2>/dev/null; then
        VIOLATIONS+=("git fetch a echoue : impossible de savoir si le checkout est a jour")
    else
        BEHIND="$(git -C "$WORKDIR" rev-list --count "HEAD..${UPSTREAM}")"
        AHEAD="$(git -C "$WORKDIR" rev-list --count "${UPSTREAM}..HEAD")"
        [ "$BEHIND" = "0" ] || VIOLATIONS+=("checkout en retard de ${BEHIND} commit(s) sur ${UPSTREAM} — c'est EXACTEMENT le cas B2")
        [ "$AHEAD" = "0" ] || VIOLATIONS+=("${AHEAD} commit(s) non pousse(s) : la revision etiquetee n'existerait pas sur le remote")
    fi

    if [ ${#VIOLATIONS[@]} -gt 0 ]; then
        if [ "${PUBLISH_ALLOW_UNCLEAN:-0}" = "1" ]; then
            log "⚠️  PUBLISH_ALLOW_UNCLEAN=1 — publication malgre :"
            for v in "${VIOLATIONS[@]}"; do log "      - $v"; done
            # Une image construite sur du non-commite ne doit pas se presenter
            # comme etant ce commit-la.
            [ -z "$(git -C "$WORKDIR" status --porcelain)" ] \
                || BF_GIT_REVISION="${BF_GIT_REVISION}-dirty"
        else
            for v in "${VIOLATIONS[@]}"; do echo "[publish]   - $v" >&2; done
            die "refus de publier : l'image ne pourrait pas etre rattachee a un commit relisible.
Corriger l'arbre (commit + push, ou git pull), ou assumer explicitement avec
PUBLISH_ALLOW_UNCLEAN=1 — la revision sera alors etiquetee « <sha>-dirty »."
        fi
    fi
fi
export BF_GIT_REVISION
log "provenance: revision=${BF_GIT_REVISION}"

log "tenant=${SLUG} image=${IMAGE} dry_run=${DRY_RUN}"
cd "$WORKDIR"

# --- 1. RPM du welcome agent ----------------------------------------------
log "1/7 build du RPM bluefox-welcome"
./scripts/build-welcome-rpm.sh

# --- 2. stage du RPM -------------------------------------------------------
# Le module `files` de BlueBuild copie files/usr/* vers /usr/* dans l'image ;
# le module `script` de la recipe fait ensuite un rpm-ostree install sur ce
# chemin. Le nom est aplati car le snippet BlueBuild ne fait pas de glob.
log "2/7 stage du RPM dans files/usr/share/bluefox/rpm-staging/"
RPM_STAGING="${WORKDIR}/files/usr/share/bluefox/rpm-staging"
mkdir -p "$RPM_STAGING"
rm -f "${RPM_STAGING}"/bluefox-welcome*.rpm
RPM_SRC="$(find "${WORKDIR}/welcome/build/RPMS/noarch" -name 'bluefox-welcome-*.noarch.rpm' -print -quit)"
[ -n "$RPM_SRC" ] || die "RPM introuvable apres build-welcome-rpm.sh"
cp "$RPM_SRC" "${RPM_STAGING}/bluefox-welcome.noarch.rpm"
log "    $(basename "$RPM_SRC") -> bluefox-welcome.noarch.rpm"

# --- 2b. stage de bfos_apply.py --------------------------------------------
# bluefox-policy-sync reutilise le rendu des listes Flatpak de
# install/bfos_apply.py plutot que d'en garder sa propre copie : le fichier
# ecrit a l'installation et celui reecrit par la synchronisation ne peuvent
# alors pas diverger. On le STAGE au build (comme le RPM) au lieu de commiter
# un doublon dans files/ — un doublon commite finirait par deriver en silence.
log "2b/7 stage de install/bfos_apply.py dans files/usr/lib/bluefox/"
APPLY_STAGING="${WORKDIR}/files/usr/lib/bluefox"
mkdir -p "$APPLY_STAGING"
cp "${WORKDIR}/install/bfos_apply.py" "${APPLY_STAGING}/bfos_apply.py"
# files/ est copie TEL QUEL dans l'image : un __pycache__ laisse par un import
# local (les tests chargent files/usr/libexec/bluefox-policy-sync par chemin)
# partirait avec. On nettoie plutot que d'esperer que personne n'en depose.
find "${WORKDIR}/files" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# --- 3. branding + stack KDE ----------------------------------------------
# BUILD=0 : on veut seulement materialiser files/ + tenant.json + le theme KDE.
# Le build lui-meme est lance a l'etape 4 (avec --push, ce que ce script-la
# ne fait pas).
log "3/7 stage du branding + generation du theme KDE"
SLUG="$SLUG" BUILD=0 ./scripts/build_branded_iso.sh

# --- 4. build + push + signature ------------------------------------------
# bluebuild signe l'image avec COSIGN_PRIVATE_KEY au moment du push (meme
# comportement que blue-build/github-action@v1, qui ne faisait que wrapper
# cette CLI). Le registre vient des BB_* exportes au preflight — il n'est PAS
# derive du remote git (cette croyance a coute un push en echec le 2026-07-19).
if [ "$DRY_RUN" = "1" ]; then
    log "4/7 DRY_RUN : build local sans push"
    # Pas de `--push=false` : dans BlueBuild 0.9.36 `--push` est un DRAPEAU
    # booleen (clap), il n'accepte aucune valeur. `--push=false` echoue avec
    # « unexpected value 'false' for '--push' ». Ne rien passer = ne pas pousser.
    bluebuild build "recipes/${SLUG}.yml"
    log "DRY_RUN termine — etapes 5 a 7 sautees (elles operent sur l'image publiee)."
    exit 0
fi

log "4/7 build + push + signature cosign"
bluebuild build --push "recipes/${SLUG}.yml"

# A partir d'ici, l'image est en ligne : `die` et le trap ERR ajoutent tous deux
# l'avertissement d'artefact incomplet. Le trap couvre les commandes qui meurent
# seules (syft, cosign), `die` couvre les controles explicites.
PUSHED=1
trap 'rc=$?; post_push_notice; exit $rc' ERR

# --- 5. SBOM ---------------------------------------------------------------
# ⚠️ Ce commentaire a decrit `--scope squashed` jusqu'au 2026-08-08, alors que
# le code ne passait plus rien de tel depuis 04eb45e : la source n'est plus une
# IMAGE mais un REPERTOIRE (le rootfs monte), et un repertoire n'a pas de
# couches — `--scope` n'y veut plus rien dire. Un commentaire qui survit au code
# qu'il decrit envoie le prochain lecteur chercher au mauvais endroit ; celui-ci
# a masque six jours durant le reglage qui compte vraiment, la selection des
# fichiers (voir generate-sbom.sh).
#
# Ce scan est l'etape qui tuait le runner GitHub (~7 Go de RAM) — d'ou son
# rapatriement ici.
SBOM="${WORKDIR}/sbom-${SLUG}.spdx.json"
log "5/7 generation du SBOM SPDX -> $(basename "$SBOM")"
# ⚠️ Cette etape a echoue QUATRE fois, de quatre facons differentes. Les trois
# premieres tenaient a scanner l'IMAGE ; la quatrieme a montre que c'etait la
# mauvaise question. Mesures du 2026-07-26 sur charizard (~10 Go d'image) :
#
#   1. `syft scan registry:`, sans reglage        -> OOM a 13,1 Go de RSS
#   2. + GOMEMLIMIT=8GiB                          -> cache de couches dans /tmp
#                                                    (un tmpfs, donc de la RAM)
#                                                    -> no space left on device
#   3. + TMPDIR=/var/tmp                          -> mort reseau a ~35 min,
#                                                    « stream error ...
#                                                    PROTOCOL_ERROR »
#   4. skopeo copy + `syft scan oci-archive:`     -> transfert OK (33 min), mais
#                                                    18,7 Go de RSS et toujours
#                                                    en croissance a 38 min
#
# GOMEMLIMIT ne pouvait pas sauver les cas 1 et 4 : limite SOUPLE, elle
# augmente la pression du ramasse-miettes sans plafonner un tas qui a vraiment
# besoin de la place. Depassee de 2,3x au cas 4.
#
# La cause commune est stereoscope, la couche « image » de syft : elle
# decompresse chaque couche, en cache le contenu sur disque, et batit un arbre
# de fichiers PAR COUCHE avant d'aplatir. Scanner le ROOTFS MONTE la
# court-circuite entierement — meme image, meme catalogue, sans la machinerie :
#
#   dir: (rootfs monte)  ->  3,4 Go de RSS, 4 min 21 s, 0 de cache, 13 240 paquets
#
# D'ou scripts/generate-sbom.sh, qui doit tourner sous `podman unshare` (le
# montage rootless l'exige). Il tire l'image PAR DIGEST depuis le registre —
# jamais l'artefact local, dont le digest differe — et etiquette le document
# SPDX avec la reference publiee, pour qu'un SBOM atteste ne s'identifie pas
# par le chemin overlay de son point de montage.
# ⚠️ DEUX digests, et les confondre casse la chaine dans un sens ou dans l'autre.
#
#   INDEX  (ce que `:latest` resout)  <- sujet de l'attestation, et ce que
#                                        l'etape 7 et verify-image.sh verifient
#   ENFANT linux/amd64                <- ou vit le rootfs, donc ce que le SBOM
#                                        doit scanner
#
# Mesure sur bf le 2026-08-08 : index dd79faa9..., enfant amd64 5ece13b1... Ce
# sont deux objets distincts. Attester l'enfant rendrait l'attestation
# introuvable pour `cosign verify-attestation <tag>` ; scanner l'index ne donne
# aucun rootfs.
#
# UNE SEULE lecture du registre sert les deux : la relire deux fois ouvrirait
# une fenetre pendant laquelle le tag peut bouger — bluebuild reecrit :latest a
# chaque push — et on attesterait alors un index que le SBOM ne decrit pas.
#
# Le digest d'un manifeste EST le sha256 de ses octets canoniques : on le calcule
# donc localement sur la reponse brute, plutot que de refaire un appel.
log "    resolution des digests publies (une seule lecture)"
RAW_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/.bf-manifest.XXXXXX")"
skopeo inspect --raw "docker://${IMAGE}" > "$RAW_MANIFEST" \
    || die "manifeste de ${IMAGE} illisible sur le registre."

IMAGE_INDEX_DIGEST="sha256:$(sha256sum "$RAW_MANIFEST" | cut -d' ' -f1)"
SBOM_MANIFEST_DIGEST="$(python3 -c '
import json, sys
doc = json.load(open(sys.argv[1]))
# Index OCI (ce que bluebuild pousse) : descendre a linux/amd64, la seule
# variante que nous construisons et la seule qui tourne sur les postes.
for m in doc.get("manifests", []):
    p = m.get("platform", {})
    if p.get("architecture") == "amd64" and p.get("os") == "linux":
        print(m["digest"])
        break
' "$RAW_MANIFEST")"
rm -f "$RAW_MANIFEST"

# Manifeste simple (pas un index) : les deux digests se confondent alors, et
# c'est le seul cas ou ils ont le droit de le faire.
[ -n "$SBOM_MANIFEST_DIGEST" ] || SBOM_MANIFEST_DIGEST="$IMAGE_INDEX_DIGEST"

[ -n "$IMAGE_INDEX_DIGEST" ] && [ -n "$SBOM_MANIFEST_DIGEST" ] \
    || die "digest publie de ${IMAGE} illisible : impossible de garantir que le SBOM decrit l'image attestee."
log "    index   ${IMAGE_INDEX_DIGEST}"
log "    amd64   ${SBOM_MANIFEST_DIGEST}"

# Le tirage, le montage, le scan et leur menage vivent dans generate-sbom.sh :
# il doit s'executer sous `podman unshare`, ce que le reste de ce script n'a pas
# a subir. SYFT_TMPDIR lui est transmis par l'environnement.
log "    scan du rootfs monte (via podman unshare)"
SYFT_TMPDIR="${SYFT_TMPDIR:-/var/tmp}" podman unshare \
    "${WORKDIR}/scripts/generate-sbom.sh" \
    "$IMAGE_REPO" "$SBOM_MANIFEST_DIGEST" "$SBOM"

# Deuxieme controle, de ce cote-ci de podman unshare : generate-sbom.sh verifie
# deja, mais un SBOM vide qui atteindrait cosign attest donnerait une
# attestation verte decrivant le neant. Le cout du doublon est nul.
[ -s "$SBOM" ] || die "SBOM vide apres generate-sbom.sh."
SBOM_PACKAGES="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1])).get("packages",[])))' "$SBOM")"
[ "$SBOM_PACKAGES" -gt 0 ] \
    || die "SBOM sans aucun paquet : le scan a produit un document valide mais vide."
log "    $(du -h "$SBOM" | cut -f1), ${SBOM_PACKAGES} paquets"

# --- 6. attestation --------------------------------------------------------
# ⚠️ On atteste le DIGEST DE L'INDEX, pas le tag. L'etape 5 vient de le resoudre
# avec soin pour que le SBOM decrive ce qui est publie ; passer "$IMAGE" ici
# rendait ce soin inutile un cran plus bas.
#
# cosign le disait lui-meme a chaque passage, et personne ne l'a lu :
#
#   WARNING: Image reference ...:latest uses a tag, not a digest, to identify
#   the image to sign. This can lead you to sign a different image than the
#   intended one.
#
# bluebuild reecrit :latest a chaque push : entre la resolution de l'etape 5 et
# cette ligne, le tag peut designer une autre image, et on attacherait alors un
# SBOM a un artefact qu'il ne decrit pas. Le journal du 2026-08-03 porte cet
# avertissement noir sur blanc.
#
# ⚠️ INDEX, pas SBOM_MANIFEST_DIGEST. Le SBOM scanne le rootfs de l'enfant
# amd64, mais l'attestation doit se rattacher a ce que `:latest` resout, sinon
# `cosign verify-attestation` sur le tag — donc l'etape 7 et verify-image.sh —
# ne la trouve jamais.
log "6/7 attestation du SBOM via cosign (sur l'index ${IMAGE_INDEX_DIGEST})"
cosign attest --yes \
    --predicate "$SBOM" \
    --type spdx \
    --key env://COSIGN_PRIVATE_KEY \
    "${IMAGE_REPO}@${IMAGE_INDEX_DIGEST}"

# --- 7. verification -------------------------------------------------------
log "7/7 verification signature + attestation + provenance"
cosign verify --key cosign.pub "$IMAGE" > /dev/null
cosign verify-attestation --key cosign.pub --type spdx "$IMAGE" > /dev/null

# Provenance : relire l'etiquette telle que PUBLIEE, pas telle qu'on croit
# l'avoir passee. Une variable non exportee, une recipe ou le bloc `labels:`
# manque, un shellexpand qui laisse le litteral — les trois donnent une image
# signee et attestee dont la provenance ne veut rien dire, sans une erreur.
#
# skopeo lit le registre (la verite), podman se rabat sur la copie locale
# fraichement poussee (memes octets, meme digest — celui que cosign vient de
# verifier ci-dessus). Si aucun des deux ne rend l'etiquette, on echoue : un
# controle de provenance qui s'auto-desactive ne prouve rien.
REVISION_LABEL=""
if command -v skopeo >/dev/null 2>&1; then
    REVISION_LABEL="$(skopeo inspect "docker://${IMAGE}" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Labels",{}).get("org.opencontainers.image.revision",""))' 2>/dev/null || true)"
    log "    provenance lue via skopeo (registre)"
fi
if [ -z "$REVISION_LABEL" ]; then
    REVISION_LABEL="$(podman image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$IMAGE" 2>/dev/null || true)"
    [ -n "$REVISION_LABEL" ] && log "    provenance lue via podman (copie locale poussee)"
fi

[ -n "$REVISION_LABEL" ] || die \
    "org.opencontainers.image.revision illisible sur ${IMAGE}.
Ni skopeo ni podman n'ont rendu l'etiquette : verifier que la recipe porte bien
son bloc \`labels:\`. L'image est publiee, signee et attestee — mais sans lien
verifiable vers son commit source (#23810)."

[ "$REVISION_LABEL" = "$BF_GIT_REVISION" ] || die \
    "provenance incoherente sur ${IMAGE} :
  attendu : ${BF_GIT_REVISION}
  publie  : ${REVISION_LABEL}
Un litteral « \${BF_GIT_REVISION} » signale une recipe qui declare l'etiquette
sans que la variable soit exportee au moment du build."

log "OK — ${IMAGE} publie, signe, atteste, revision ${BF_GIT_REVISION}."
log "Controle : cosign tree ${IMAGE}  (doit lister Signatures ET Attestations)"
# SLSA (#23810, dernier point) : l'attestation SPDX dit ce qu'il y a DANS
# l'image, pas d'ou elle vient. L'etiquette de revision ci-dessus donne le lien
# vers le commit, mais elle n'est pas signee separement — elle est couverte par
# la signature de l'image, ce qui suffit a la rendre infalsifiable a posteriori,
# pas a prouver QUI a construit. Une attestation de provenance SLSA
# (cosign attest --type slsaprovenance) reste a evaluer ; elle n'a de valeur
# qu'avec une identite de build attestable — soit un builder distant, soit une
# cle dediee a la machine de build. A trancher avec la posture de cle de #23815.
