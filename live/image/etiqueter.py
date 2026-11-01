#!/usr/bin/python3
"""Etiquette SELinux un flux tar au vol (#26870).

    podman export <ctr> | etiqueter.py <file_contexts> | tar2sqfs ...

Une image conteneur ne porte pas les etiquettes SELinux : bootc les pose au
deploiement. Une cle live demarre la racine telle quelle, donc chaque fichier
recoit ici l'attribut `security.selinux` que les regles de l'image lui
donnent (`SCHILY.xattr.security.selinux`, que tar2sqfs recopie). Sans cela,
Blue Fox OS (SELinux en mode enforcing) ne demarrerait pas, ou seulement en
mode permissif.

En flux : aucune copie de la racine sur disque (10 Go).

🔴 Les liens physiques. Une image bootc porte son depot ostree
(`sysroot/ostree/repo/objects/…`) et 166 000 fichiers de /usr y sont des liens
physiques. L'objet passe en PREMIER dans le flux, et un squashfs garde les
attributs de la premiere occurrence d'un inode : /usr/bin/bash heritait donc
de l'etiquette de l'objet (`default_t`), et le systeme n'aurait pas demarre en
mode enforcing. D'ou deux passages :

    podman export <ctr> | etiqueter.py --relever-liens liens.json
    podman export <ctr> | etiqueter.py <file_contexts> liens.json | tar2sqfs ...

Le premier releve, pour chaque cible de lien, le premier chemin hors de
`sysroot/` qui la designe ; le second etiquette la cible d'apres ce chemin.
"""
from __future__ import annotations

import json
import stat
import sys
import tarfile

import selinux

_MODES = {
    tarfile.REGTYPE: stat.S_IFREG, tarfile.AREGTYPE: stat.S_IFREG,
    tarfile.LNKTYPE: stat.S_IFREG, tarfile.CONTTYPE: stat.S_IFREG,
    tarfile.DIRTYPE: stat.S_IFDIR, tarfile.SYMTYPE: stat.S_IFLNK,
    tarfile.CHRTYPE: stat.S_IFCHR, tarfile.BLKTYPE: stat.S_IFBLK,
    tarfile.FIFOTYPE: stat.S_IFIFO,
}
CLE = "SCHILY.xattr.security.selinux"


def chemin_absolu(nom: str) -> str:
    if nom.startswith("./"):
        nom = nom[2:]
    nom = nom.lstrip("/").rstrip("/")
    return "/" + nom if nom and nom != "." else "/"


def etiquette(chemin, info, rechercher=None):
    rechercher = rechercher or selinux.matchpathcon
    mode = _MODES.get(info.type, stat.S_IFREG) | (info.mode & 0o7777)
    try:
        rc, ctx = rechercher(chemin, mode)
    except OSError:
        return None
    return ctx if rc == 0 and ctx and ctx != "<<none>>" else None


def releve_liens(entree):
    """{cible: premier chemin hors de sysroot/ qui la designe par lien physique}."""
    liens = {}
    for info in entree:
        if info.islnk() and not info.name.startswith(("sysroot/", "./sysroot/")):
            liens.setdefault(info.linkname, info.name)
    return liens


def etiqueter_flux(entree, sortie, rechercher=None, liens=None):
    liens = liens or {}
    n = sans = 0
    for info in entree:
        chemin = chemin_absolu(liens.get(info.name, info.name))
        ctx = etiquette(chemin, info, rechercher)
        pax = dict(info.pax_headers)
        if ctx:
            pax[CLE] = ctx
        else:
            sans += 1
        info.pax_headers = pax
        donnees = entree.extractfile(info) if info.isreg() else None
        sortie.addfile(info, donnees)
        n += 1
    return n, sans


def main(argv):
    if len(argv) == 3 and argv[1] == "--relever-liens":
        entree = tarfile.open(fileobj=sys.stdin.buffer, mode="r|")
        liens = releve_liens(entree)
        with open(argv[2], "w", encoding="utf-8") as f:
            json.dump(liens, f)
        print(f"etiqueter: {len(liens)} cibles de liens physiques relevees", file=sys.stderr)
        return 0
    if len(argv) not in (2, 3):
        print("usage: etiqueter.py <file_contexts> [liens.json] | --relever-liens <liens.json>",
              file=sys.stderr)
        return 2
    liens = {}
    if len(argv) == 3:
        with open(argv[2], encoding="utf-8") as f:
            liens = json.load(f)
    selinux.matchpathcon_init(argv[1])
    entree = tarfile.open(fileobj=sys.stdin.buffer, mode="r|")
    sortie = tarfile.open(fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT)
    n, sans = etiqueter_flux(entree, sortie, liens=liens)
    sortie.close()
    print(f"etiqueter: {n} entrees, {sans} sans etiquette, {len(liens)} liens pris en compte",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
