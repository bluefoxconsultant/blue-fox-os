"""Mutations des corrections du 2026-10-23 : chaque defaut reintroduit doit faire
echouer au moins un essai. A jouer dans le conteneur d'essais."""
import shutil
import subprocess
import sys

MUTATIONS = [
    ("bfos_live/session.py", "        verifier_entete(fichier, run=run)\n", "\n", "en-tete non verifie"),
    ("bfos_live/session.py", "        os.rename(partiel, fichier)\n", "        os.rename(partiel, fichier) if False else None\n        shutil.copyfile(partiel, fichier)\n", "nom final avant la fin"),
    ("bfos_live/session.py", "        purger_depots()\n        etat = {", "        etat = {", "pas de purge au demarrage"),
    ("bfos_live/session.py", "    (finir or finir_processus)(nom, run=run, attendre=attendre)\n", "", "processus non finis"),
    ("bfos_live/session.py", "    maison = os.path.realpath(compte.pw_dir)\n", "    maison = compte.pw_dir\n", "chemin non reel"),
    ("bfos_live/session.py", 'return lancer(argv, run=run, accepter="operation has completed successfully")', "return lancer(argv, run=run)", "sgdisk strict"),
    ("bfos_live/session.py", "    if any(s.get(\"user\") == nom for s in sessions if isinstance(s, dict)):\n        return False\n", "", "filet aveugle"),
    ("bfos_live/pam_helper.py", "    try:\n        _revoquer_jetons(cfg, nom, post)\n    finally:\n        # Meme sans jetons a revoquer (fichier absent ou illisible) : le profil\n        # se referme toujours.\n        if os.geteuid() == 0:\n            arreter_session(cfg, nom, run=run)\n",
     "    if os.geteuid() == 0:\n        arreter_session(cfg, nom, run=run)\n    _revoquer_jetons(cfg, nom, post)\n", "arret avant revocation"),
    ("bfos_live/pam_helper.py", '["systemctl", "stop", "--no-block", _unite(cfg, nom)]', '["systemctl", "stop", _unite(cfg, nom)]', "arret bloquant"),
    ("bfos_live/pam_helper.py", "        poser_filet(cfg, nom, run=run)\n        return mode", "        return mode", "pas de filet"),
    ("bfos_live/pam_helper.py", "    except subprocess.TimeoutExpired:\n", "    except ZeroDivisionError:\n", "delai non rattrape"),
]

survivants = []
for fichier, avant, apres, nom in MUTATIONS:
    shutil.rmtree("/tmp/m", ignore_errors=True)
    shutil.copytree("/src/live", "/tmp/m/live")
    shutil.copytree("/src/install", "/tmp/m/install")
    chemin = f"/tmp/m/live/{fichier}"
    texte = open(chemin).read()
    assert texte.count(avant) == 1, (nom, texte.count(avant))
    open(chemin, "w").write(texte.replace(avant, apres))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                        "tests/test_session.py", "tests/test_pam_helper.py"],
                       cwd="/tmp/m/live", capture_output=True, text=True)
    tue = r.returncode != 0
    print(("TUE      " if tue else "SURVIT   ") + nom)
    if not tue:
        survivants.append(nom)
print(f"{len(MUTATIONS) - len(survivants)}/{len(MUTATIONS)} mutations tuees")
sys.exit(1 if survivants else 0)
