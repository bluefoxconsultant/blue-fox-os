#!/usr/bin/python3 -I
"""Ouvre Odoo déjà connecté à l'ouverture du bureau (#26870).

La session (root) a déposé dans /run/bfos-live/connexions/<nom> un lien de
connexion à usage unique, valable 5 minutes, obtenu avec le jeton du QR. On
l'ouvre dans la fenêtre d'application Odoo de Brave : la session Odoo vit
ensuite dans le profil Brave, et l'icône Odoo épinglée s'ouvre connectée.

Sans lien (pas de séquestre, Odoo injoignable, lien trop vieux) : rien. La
personne passe alors par Authentik comme d'habitude.
"""
import os
import pwd
import subprocess
import sys
import time

DOSSIER = "/run/bfos-live/connexions"
VALIDITE = 280  # secondes : le serveur en accorde 300
BRAVE = ["flatpak", "run", "--command=brave", "com.brave.Browser", "--profile-directory=Default"]


def lien_frais(nom, maintenant=None, dossier=None):
    chemin = os.path.join(dossier or DOSSIER, nom)
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd) as f:
        st = os.fstat(f.fileno())
        if st.st_uid != os.getuid():
            return None
        if (maintenant or time.time()) - st.st_mtime > VALIDITE:
            return None
        lien = f.read(4096).strip()
    if not lien.startswith("https://") or "/odoo" not in lien or any(c in lien for c in " \n\r\x00"):
        return None
    return lien


def main(run=subprocess.Popen):
    nom = pwd.getpwuid(os.getuid()).pw_name
    lien = lien_frais(nom)
    if not lien:
        return 0
    run(BRAVE + [f"--app={lien}"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
