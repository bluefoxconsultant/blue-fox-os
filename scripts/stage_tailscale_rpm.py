#!/usr/bin/env python3
"""Depose le RPM Tailscale dans l'arbre de l'image, avant la construction.

POURQUOI CE SCRIPT EXISTE
-------------------------
🔴 `pkgs.tailscale.com` coupe en plein transfert depuis la machine de build :
« Curl error (56): Failure when receiving data from the peer ». Le paquet fait
39 Mio, la coupure est frequente, et `rpm-ostree` NE REESSAIE PAS — une seule
coupure emporte la construction entiere du locataire. Le 2026-09-21, elle a
tue `bf` et `bf-surface` a trois tours de suite pendant que `factice` passait.

⚠️ Le correctif du 2026-09-14 versionnait le FICHIER `.repo` et affirmait
qu'« une coupure TLS ne tue plus la construction ». C'etait vrai du fichier,
faux du paquet : celui-ci se retelechargait a chaque construction.

⚠️ Et DNF5 n'a PAS d'option `retries` — verifie dans sa documentation avant de
l'ecrire. `timeout` et `minrate` detectent une connexion morte ; rien ne rejoue
un telechargement echoue. Le retour en arriere est donc ici, cote hote, ou
`curl --retry-all-errors` couvre justement l'erreur 56.

CE QU'IL FAIT
-------------
Lit le baseurl dans `files/rpm-ostree/tailscale.repo`, trouve le paquet courant
dans les metadonnees du depot, le telecharge avec des reprises, verifie sa somme
SHA-256 telle que le depot l'annonce, et le depose avec la cle publique du
depot. La VERIFICATION DE SIGNATURE, elle, se fait dans l'image, par
`files/scripts/install-tailscale-rpm.sh` — au meme endroit et de la meme facon
que pour notre propre RPM.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parent.parent
FICHIER_REPO = REPO / "files/rpm-ostree/tailscale.repo"
DEST_RPM = "usr/share/bluefox/rpm-staging/tailscale.rpm"
DEST_CLE = "usr/share/bluefox/keys/RPM-GPG-KEY-tailscale"
ARCH = "x86_64"
NS = {"c": "http://linux.duke.edu/metadata/common",
      "r": "http://linux.duke.edu/metadata/repo"}


def baseurl() -> str:
    for ligne in FICHIER_REPO.read_text(encoding="utf-8").splitlines():
        if ligne.startswith("baseurl="):
            return ligne.split("=", 1)[1].strip().replace("$basearch", ARCH)
    raise SystemExit(f"[tailscale] pas de baseurl dans {FICHIER_REPO}")


def cle_url() -> str:
    for ligne in FICHIER_REPO.read_text(encoding="utf-8").splitlines():
        if ligne.startswith("gpgkey="):
            return ligne.split("=", 1)[1].strip().replace("$basearch", ARCH)
    raise SystemExit(f"[tailscale] pas de gpgkey dans {FICHIER_REPO}")


def _lire(url: str, essais: int = 5) -> bytes:
    """GET avec reprises : c'est tout l'objet de ce script."""
    dernier: Exception | None = None
    for essai in range(1, essais + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 — coupure TLS comprise
            dernier = exc
            print(f"[tailscale] {url} : {exc} (essai {essai}/{essais})",
                  file=sys.stderr)
            if essai < essais:
                time.sleep(10)  # meme rafale que pour le paquet : on la laisse passer
    raise SystemExit(f"[tailscale] echec apres {essais} essais : {dernier}")


# Le miroir coupe par RAFALES (2026-09-22 : bf puis bf-surface tombes a deux
# minutes d'intervalle, curl sorti en 35 apres ses six reprises de 3 s). Une
# rafale dure plus longtemps que six fois trois secondes : on rejoue donc curl
# lui-meme, avec une pause qui laisse a la rafale le temps de retomber.
_PASSES_CURL = 4
_PAUSE_ENTRE_PASSES = 45


def _telecharger(url: str, cible: pathlib.Path, dormir=time.sleep) -> None:
    """curl avec --retry-all-errors : --retry seul NE rejoue PAS une erreur TLS."""
    cible.parent.mkdir(parents=True, exist_ok=True)
    for passe in range(1, _PASSES_CURL + 1):
        try:
            subprocess.run(
                ["curl", "-fL", "--retry", "6", "--retry-all-errors",
                 "--retry-delay", "3", "-C", "-", "--connect-timeout", "20",
                 "-o", str(cible), url],
                check=True)
            return
        except subprocess.CalledProcessError as exc:
            if passe == _PASSES_CURL:
                raise
            print(f"[tailscale] curl sorti en {exc.returncode} (passe "
                  f"{passe}/{_PASSES_CURL}) ; nouvel essai dans "
                  f"{_PAUSE_ENTRE_PASSES} s", file=sys.stderr)
            dormir(_PAUSE_ENTRE_PASSES)


def _somme(chemin: pathlib.Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def deja_depose(cible: pathlib.Path, sha_attendu: str) -> bool:
    """Vrai si le paquet deja sur le disque EST celui que le depot annonce.

    La passe publie trois locataires a la suite depuis le meme arbre : sans ce
    controle, chacun retelechargeait les 39 Mio, et chacun rejouait sa chance
    contre le miroir. Sans somme annoncee, on ne peut rien prouver : on
    retelecharge.
    """
    return bool(sha_attendu) and cible.is_file() and _somme(cible) == sha_attendu


def paquet_courant(base: str) -> tuple[str, str, str]:
    """(url du rpm, sha256 annonce, version) — lus dans les metadonnees."""
    repomd = ET.fromstring(_lire(f"{base}/repodata/repomd.xml"))
    primaire = next(
        d.find("r:location", NS).attrib["href"]
        for d in repomd.findall("r:data", NS) if d.attrib.get("type") == "primary")
    brut = _lire(f"{base}/{primaire}")
    if primaire.endswith(".gz"):
        brut = gzip.decompress(brut)
    racine = ET.fromstring(brut)
    candidats = []
    for p in racine.findall("c:package", NS):
        if (p.findtext("c:name", default="", namespaces=NS) != "tailscale"
                or p.find("c:arch", NS) is None
                or p.findtext("c:arch", default="", namespaces=NS) != ARCH):
            continue
        v = p.find("c:version", NS).attrib["ver"]
        somme = p.find("c:checksum", NS)
        candidats.append((
            tuple(int(x) for x in re.findall(r"\d+", v)),
            v,
            p.find("c:location", NS).attrib["href"],
            somme.text if somme is not None and somme.attrib.get("type") == "sha256" else "",
        ))
    if not candidats:
        raise SystemExit("[tailscale] aucun paquet tailscale dans les metadonnees")
    _, version, href, sha = max(candidats)
    return f"{base}/{href}", sha, version


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("files_root", type=pathlib.Path,
                   help="racine de l'arbre files/ de BlueBuild")
    args = p.parse_args()

    base = baseurl()
    url, sha_attendu, version = paquet_courant(base)
    cible = args.files_root / DEST_RPM
    print(f"[tailscale] paquet courant : {version}")
    if deja_depose(cible, sha_attendu):
        print("[tailscale] deja depose, somme SHA-256 conforme : pas de "
              "telechargement")
    else:
        _telecharger(url, cible)

    if sha_attendu:
        somme = _somme(cible)
        if somme != sha_attendu:
            cible.unlink(missing_ok=True)
            raise SystemExit(
                f"[tailscale] somme SHA-256 differente de celle annoncee par le "
                f"depot : {somme} != {sha_attendu}")
        print("[tailscale] somme SHA-256 conforme aux metadonnees")

    cle = args.files_root / DEST_CLE
    _telecharger(cle_url(), cle)
    print(f"[tailscale] depose : {cible} ({cible.stat().st_size} octets) + {cle.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
