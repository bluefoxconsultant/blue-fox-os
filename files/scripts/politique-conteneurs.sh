#!/usr/bin/env bash
# files/scripts/politique-conteneurs.sh — fait verifier les mises a jour de
# l'image par la signature cosign (#27109). Execute DANS l'image, par un module
# `script` place APRES le module `signing` des recipes.
#
# POURQUOI (constats du 2026-10-28, sources d'Anaconda, ostree-ext et skopeo) :
#   - C'est /etc/containers/policy.json qui juge chaque tirage d'image, MEME en
#     `ostree-unverified-registry:` : rpm-ostree passe par le proxy skopeo, qui
#     applique la politique par defaut. `ostree-image-signed:` ajoute seulement
#     le refus d'un `default` en insecureAcceptAnything.
#   - Le module `signing` de BlueBuild ecrit son entree pour
#     ${IMAGE_REGISTRY}/${IMAGE_NAME}, or le build se fait avec
#     IMAGE_REGISTRY=localhost (scripts/publish-image.sh pousse ensuite vers
#     ghcr). L'image publiee portait donc `localhost/blue-fox-os-<slug>`, et
#     ghcr.io/bluefoxconsultant/... tombait sous `"": insecureAcceptAnything` :
#     AUCUNE mise a jour n'etait verifiee. Mesure sur factice:latest (a9b26d3).
#
# CE QUE FAIT CE SCRIPT :
#   1. retire les entrees `localhost/*` de transports.docker et les fichiers
#      registries.d/localhost-*.yaml que le module signing vient d'ecrire ;
#   2. ecrit une entree sigstoreSigned + matchRepository PAR DEPOT (bf,
#      bf-surface, factice), avec DEUX cles (`keyPaths`) : la cle de
#      publication et la cle de secours, hors ligne (decision d'Olivier,
#      2026-10-29). Une signature valide par l'une OU l'autre passe : si la
#      cle de publication fuit ou se perd, les postes acceptent deja une image
#      signee par la cle de secours, sans attendre une image qu'ils ne
#      pourraient plus verifier. PAS par espace de noms : ghcr.io/bluefoxconsultant
#      porte aussi des paquets non signes, qu'une regle d'espace de noms
#      refuserait a quiconque les tire depuis un poste.
#      ⚠️ matchRepository, PAS matchRepoDigestOrExact : la signature cosign
#      porte le depot SANS tag (`docker-reference` =
#      ghcr.io/bluefoxconsultant/blue-fox-os-<slug>), alors qu'on tire `:latest`.
#   3. garde `"": insecureAcceptAnything` dans docker (toolbox, distrobox,
#      images Fedora non signees) : on ne durcit que NOS depots ;
#   4. ECHOUE LE BUILD si `default` n'est pas `reject`, si une entree manque,
#      si l'une des deux cles est absente (ou si ce sont les memes), ou si
#      aucun registries.d ne demande les pieces
#      jointes sigstore pour nos depots (sans `use-sigstore-attachments: true`,
#      skopeo repond « a signature was required, but no signature exists »).
#
# 🔴 UNE POLITIQUE FAUSSE GELE LE PARC. Un poste qui demarre cette image juge
# ses mises a jour suivantes avec ELLE : une cle fausse, un registries.d absent,
# et plus aucune mise a jour ne passe, correctif compris. D'ou l'echec au build
# ici, et la porte canari de publish-image.sh (etape 4b, juste apres la poussee).
#
# Chaque cle est DECODEE au build, pas seulement reconnue a son en-tete
# (relecture adverse 2, M1) : un corps PEM abime passait l'ancien `grep BEGIN`,
# et containers-image le refusait ensuite sur les postes (« parsing public key »,
# « PEM decoding failed ») : plus aucune mise a jour. Deux lectures
# independantes : python3 (bibliotheque standard, toujours dans l'image : base64
# strict, SubjectPublicKeyInfo ECDSA P-256 exact, point sur la courbe) et
# `openssl pkey -pubin` quand openssl est present. cosign generate-key-pair rend
# du P-256 ; une cle d'un autre type demanderait d'elargir ce controle.
#
# Les cles et le registries.d arrivent par le module `files` (avant celui-ci) :
#   files/usr/share/bluefox/keys/cosign-images.pub          (= cosign.pub du depot)
#   files/usr/share/bluefox/keys/cosign-images-secours.pub  (cle de secours, hors ligne)
#   files/etc/containers/registries.d/bluefoxconsultant.yaml
#
# Essais hors image : BFOS_RACINE=<dossier> prefixe tous les chemins lus et
# ecrits (les chemins ecrits DANS la politique restent ceux du systeme).
set -euo pipefail

RACINE="${BFOS_RACINE:-}"
# Essais hors image : BFOS_OPENSSL designe un autre openssl (ou un absent).
OPENSSL="${BFOS_OPENSSL:-openssl}"
CLE_SYSTEME="/usr/share/bluefox/keys/cosign-images.pub"
CLE_SECOURS="/usr/share/bluefox/keys/cosign-images-secours.pub"
DEPOTS="ghcr.io/bluefoxconsultant/blue-fox-os-bf ghcr.io/bluefoxconsultant/blue-fox-os-bf-surface ghcr.io/bluefoxconsultant/blue-fox-os-factice"

log() { echo "[politique-conteneurs] $*"; }
die() { echo "[politique-conteneurs] ERREUR: $*" >&2; exit 1; }

POLICY="${RACINE}/etc/containers/policy.json"
REGISTRIES_D="${RACINE}/etc/containers/registries.d"

[ -f "$POLICY" ] || die "${POLICY} absent : le module signing doit tourner AVANT ce script."
verifier_cle() {  # chemin : la cle doit se lire comme containers-image la lira
    local f="$1"
    python3 - "$f" <<'PY' || die "${f} : cle publique illisible (corps PEM abime ?) : les postes refuseraient toute mise a jour."
import base64
import binascii
import sys

chemin = sys.argv[1]
lignes = open(chemin, encoding="ascii", errors="strict").read().splitlines()
try:
    debut = lignes.index("-----BEGIN PUBLIC KEY-----")
    fin = lignes.index("-----END PUBLIC KEY-----", debut + 1)
except ValueError:
    sys.exit(f"{chemin} : pas de bloc PEM BEGIN/END PUBLIC KEY")
try:
    der = base64.b64decode("".join(l.strip() for l in lignes[debut + 1:fin]), validate=True)
except (binascii.Error, ValueError) as exc:
    sys.exit(f"{chemin} : base64 invalide ({exc})")
# SubjectPublicKeyInfo { ecPublicKey, prime256v1 } BIT STRING 00 04 X Y
ENTETE = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200" "04")
if len(der) != len(ENTETE) + 64 or not der.startswith(ENTETE):
    sys.exit(f"{chemin} : pas une cle ECDSA P-256 non compressee ({len(der)} octets)")
x = int.from_bytes(der[-64:-32], "big")
y = int.from_bytes(der[-32:], "big")
P = 2**256 - 2**224 + 2**192 + 2**96 - 1
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
if not (x < P and y < P and (y * y - (x * x * x - 3 * x + B)) % P == 0):
    sys.exit(f"{chemin} : le point n'est pas sur la courbe P-256")
PY
    if command -v "$OPENSSL" >/dev/null 2>&1; then
        "$OPENSSL" pkey -pubin -noout -in "$f" >/dev/null 2>&1 \
            || die "${f} : openssl ne la lit pas comme cle publique."
    else
        log "openssl absent : ${f} lue par python3 seulement"
    fi
}
for CLE in "$CLE_SYSTEME" "$CLE_SECOURS"; do
    [ -s "${RACINE}${CLE}" ] || die "cle publique absente : ${RACINE}${CLE} (module files)."
    grep -q "BEGIN PUBLIC KEY" "${RACINE}${CLE}" || die "${CLE} n'est pas une cle publique PEM."
    verifier_cle "${RACINE}${CLE}"
done
# Deux fois la meme cle : le secours n'en serait pas un.
cmp -s "${RACINE}${CLE_SYSTEME}" "${RACINE}${CLE_SECOURS}" \
    && die "${CLE_SECOURS} est identique a ${CLE_SYSTEME} : aucune cle de secours."

# Les fichiers registries.d du module signing visent localhost : ils ne servent
# a rien sur un poste, et un second fichier qui declarerait la meme portee
# ferait echouer TOUTE lecture de registries.d (cle en double).
for F in "$REGISTRIES_D"/localhost-*.yaml "$REGISTRIES_D"/localhost-*.yml; do
    [ -e "$F" ] || continue
    rm -f "$F"
    log "retire $(basename "$F")"
done

python3 - "$POLICY" "$REGISTRIES_D" "$CLE_SYSTEME" "$CLE_SECOURS" $DEPOTS <<'PY'
import json
import os
import re
import sys

policy_path, registries_d, cle, cle_secours, *depots = sys.argv[1:]


def die(msg):
    print(f"[politique-conteneurs] ERREUR: {msg}", file=sys.stderr)
    sys.exit(1)


with open(policy_path, encoding="utf-8") as fh:
    pol = json.load(fh)
docker = (pol.get("transports") or {}).get("docker")
if not isinstance(docker, dict):
    die("transports.docker absent de la politique : forme inattendue, relire le module signing.")

for portee in [k for k in docker if k == "localhost" or k.startswith(("localhost/", "localhost:"))]:
    del docker[portee]
    print(f"[politique-conteneurs] retire l'entree {portee}")

# containers-policy.json(5) : keyPaths = plusieurs cles, une signature valide
# par l'UNE d'elles suffit ; exclusif de keyPath. La politique publiee
# l'emploie deja pour ghcr.io/ublue-os (meme containers-image que les postes).
regle = {"type": "sigstoreSigned", "keyPaths": [cle, cle_secours],
         "signedIdentity": {"type": "matchRepository"}}
for depot in depots:
    docker[depot] = [json.loads(json.dumps(regle))]
    print(f"[politique-conteneurs] {depot} : sigstoreSigned, matchRepository, {cle} + {cle_secours}")

# --- gardes : le build echoue plutot que de publier une politique muette ---
if pol.get("default") != [{"type": "reject"}]:
    die(f"default = {pol.get('default')!r}, attendu [{{'type': 'reject'}}] : "
        "ostree-image-signed refuserait toute mise a jour.")


def portees_docker(texte):
    """({portee: valeur de use-sigstore-attachments (True, False ou None)},
    default-docker present ?) pour un fichier registries.d. Analyse
    volontairement etroite (pas de PyYAML garanti dans l'image) : deux niveaux
    d'indentation, la forme que livrent Fedora et ce depot."""
    portees, portee, dans_docker, niveau, defaut = {}, None, False, None, False
    for brute in texte.splitlines():
        ligne = brute.split("#", 1)[0].rstrip()
        contenu = ligne.strip()
        if not contenu:
            continue
        retrait = len(ligne) - len(ligne.lstrip())
        if retrait == 0:
            dans_docker, portee, niveau = contenu == "docker:", None, None
            defaut = defaut or contenu.startswith("default-docker:")
            continue
        if not dans_docker:
            continue
        if niveau is None:
            niveau = retrait
        if retrait == niveau:
            portee = (contenu[:-1].strip().strip("'\"")
                      if contenu.endswith(":") else None)
            if portee:
                portees.setdefault(portee, None)
        elif portee:
            m = re.fullmatch(r"use-sigstore-attachments:\s*(\S+)", contenu)
            if m:
                portees[portee] = m.group(1).lower() == "true"
    return portees, defaut


# containers-image (docker/registries_d.go) refuse de lire TOUT le dossier si
# une portee docker, quelle qu'elle soit, ou default-docker figure dans deux
# fichiers : plus aucune signature ne serait trouvee.
declarees, defauts = {}, []
if os.path.isdir(registries_d):
    for nom in sorted(os.listdir(registries_d)):
        if nom.endswith((".yaml", ".yml")):
            with open(os.path.join(registries_d, nom), encoding="utf-8") as fh:
                portees, defaut = portees_docker(fh.read())
            for p, valeur in portees.items():
                declarees.setdefault(p, []).append((nom, valeur))
            if defaut:
                defauts.append(nom)
for p, decl in declarees.items():
    if len(decl) > 1:
        die(f"portee {p} declaree dans plusieurs fichiers registries.d "
            f"({[n for n, _ in decl]}) : containers-image refuserait de lire le dossier.")
if len(defauts) > 1:
    die(f"default-docker declare dans plusieurs fichiers registries.d ({defauts}) : "
        "containers-image refuserait de lire le dossier.")
for depot in depots:
    # La portee la plus precise QUI REGLE use-sigstore-attachments decide : le
    # depot, puis ses espaces de noms. Un `false` plus precis que notre `true`
    # l'annule.
    morceaux = depot.split("/")
    candidates = ["/".join(morceaux[:i]) for i in range(len(morceaux), 0, -1)]
    decide = next(((c, declarees[c][0]) for c in candidates
                   if c in declarees and declarees[c][0][1] is not None), None)
    if decide is None or decide[1][1] is not True:
        ou = f"{decide[0]} dans {decide[1][0]}" if decide else "aucune portee"
        die(f"aucun registries.d ne demande use-sigstore-attachments pour {depot} "
            f"(decide : {ou}) : skopeo ne trouverait aucune signature et refuserait tout.")

tmp = policy_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(pol, fh, indent=2)
    fh.write("\n")

# --- relecture : ce qui est SUR LE DISQUE, contre une attente ecrite a part ---
# (Ne pas comparer a `regle` : une regle fausse se comparerait a elle-meme.)
ATTENDUE = [{"type": "sigstoreSigned",
             "keyPaths": ["/usr/share/bluefox/keys/cosign-images.pub",
                          "/usr/share/bluefox/keys/cosign-images-secours.pub"],
             "signedIdentity": {"type": "matchRepository"}}]
with open(tmp, encoding="utf-8") as fh:
    relue = json.load(fh)
relu_docker = (relue.get("transports") or {}).get("docker") or {}
if relue.get("default") != [{"type": "reject"}]:
    os.unlink(tmp)
    die(f"politique relue : default = {relue.get('default')!r}")
for depot in depots:
    if relu_docker.get(depot) != ATTENDUE:
        os.unlink(tmp)
        die(f"politique relue : entree manquante ou alteree pour {depot} : "
            f"{relu_docker.get(depot)!r}")
restes = [k for k in relu_docker if k == "localhost" or k.startswith(("localhost/", "localhost:"))]
if restes:
    os.unlink(tmp)
    die(f"politique relue : entrees localhost restantes {restes}")
os.chmod(tmp, 0o644)
os.replace(tmp, policy_path)
print(f"[politique-conteneurs] OK : {len(depots)} depots verifies, default reject.")
PY
