"""Essai du profil persistant DANS la VM (vm-profil.sh), en root, sans QR.

    python3 -I /chemin/essai-profil-vm.py creer     1er demarrage : partition, profil, temoin
    python3 -I /chemin/essai-profil-vm.py relire    apres redemarrage : meme profil, temoin lu

La cle de 32 octets est fixe (ESSAI SEULEMENT) : en vrai, elle vient du
sequestre d'Odoo apres le QR. Le compte est cree comme le ferait l'aide PAM.
"""
import os
import pwd
import subprocess
import sys

sys.path.insert(0, "/usr/libexec/bfos-live")
from bfos_live import pam_helper as ph  # noqa: E402
from bfos_live import session as se  # noqa: E402

NOM, SUB, CLE = "essai-abc123", "sub-essai-vm", bytes(range(32))
cfg = ph.charger_config(ph.CONFIG)
try:
    cpt = pwd.getpwnam(NOM)
except KeyError:
    subprocess.run(["useradd", "--uid", "12345", "--user-group", "--create-home", NOM], check=True)
    cpt = pwd.getpwnam(NOM)
print("maison", cpt.pw_dir, "->", se.maison_reelle(cfg, cpt))
neuf = se.ouvrir_profil(cfg, NOM, SUB, cpt, CLE)
maison = se.maison_reelle(cfg, cpt)
temoin = os.path.join(maison, "temoin.txt")
print("neuf", neuf, "monte", os.path.ismount(maison))
print(subprocess.run(["findmnt", maison], capture_output=True, text=True).stdout)
print(subprocess.run(["ls", "-laZ", maison], capture_output=True, text=True).stdout)
if sys.argv[1] == "creer":
    with open(temoin, "w") as f:
        f.write("persiste\n")
    os.chown(temoin, cpt.pw_uid, cpt.pw_gid)
else:
    print("TEMOIN", open(temoin).read().strip() if os.path.exists(temoin) else "ABSENT")
# Un mauvais en-tete est-il refuse ? (lecture seule)
se.verifier_entete(os.path.join(se.dossiers(cfg)["profils"], se.nom_fichier_profil(SUB)))
print("entete OK")
se.fermer_profil(NOM, maison)
se.demonter_profils_si_libre(cfg)
print("ferme; monte encore :", os.path.ismount(maison), os.path.exists("/dev/mapper/" + se.mapper(NOM)))
print(subprocess.run(["lsblk", "-o", "NAME,SIZE,TYPE,PARTLABEL,FSTYPE,MOUNTPOINTS"],
                     capture_output=True, text=True).stdout)
