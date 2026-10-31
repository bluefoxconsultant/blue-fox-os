"""Mutations du multilingue de la cle (2026-10-27, « la cle, toutes langues ») : chaque
defaut reintroduit doit faire echouer au moins un essai. A jouer dans le conteneur d'essais :
QT_QPA_PLATFORM=offscreen python3 /src/live/tests/mutations_1027.py"""
import shutil
import subprocess
import sys

L = ("live", ["tests/test_langue.py", "tests/test_pam_helper.py", "tests/test_session.py", "tests/test_greeter.py"])
MUTATIONS = [
    (L, "bfos_live/pam_helper.py", "            canal.erreur(_(exc.message))", "            canal.erreur(exc.message)",
     "aide: refus non traduit"),
    (L, "bfos_live/pam_helper.py", "    options = [dict(o, titre=_(o[\"titre\"]), texte=_(o.get(\"texte\", \"\")))\n               for o in (options or CHOIX_SESSION)]",
     "    options = options or CHOIX_SESSION", "aide: choix non traduit"),
    (L, "bfos_live/pam_helper.py", "        choix = langue.lire_langue_ecran(chemin, proprietaire=proprietaire)",
     "        choix = langue.lire_langue_ecran(chemin)", "aide: proprietaire du choix ignore"),
    (L, "bfos_live/langue.py", "        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)",
     "        fd = os.open(chemin, os.O_RDONLY | os.O_NONBLOCK)", "langue: suit un lien"),
    (L, "bfos_live/langue.py", "    return normaliser(brut) if CODE_RE.fullmatch(brut) else None", "    return brut or None",
     "langue: choix non controle"),
    (L, "bfos_live/langue.py", "        if msgid and msgstr and not flou:", "        if msgid and msgstr:", "po: entree floue gardee"),
    (L, "bfos_live/greeter.py", "        deposer_langue(langue.courante())\n", "", "ecran: choix jamais depose"),
    (L, "bfos_live/greeter.py", "            methode, args = self._dernier\n            methode(*args)", "            pass",
     "ecran: affichage pas rejoue"),
    (L, "bfos_live/session.py", "    langue.choisir(demande.get(\"langue\"))\n", "", "session: langue de l'ecran ignoree"),
]

survivants = []
for (racine, essais), fichier, avant, apres, nom in MUTATIONS:
    shutil.rmtree("/tmp/m", ignore_errors=True)
    shutil.copytree("/src/live", "/tmp/m/live")
    shutil.copytree("/src/install", "/tmp/m/install")
    shutil.copytree("/src/welcome", "/tmp/m/welcome")
    chemin = f"/tmp/m/{racine}/{fichier}"
    texte = open(chemin).read()
    assert texte.count(avant) == 1, (nom, texte.count(avant))
    open(chemin, "w").write(texte.replace(avant, apres))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *essais],
                           cwd=f"/tmp/m/{racine}", capture_output=True, text=True, timeout=300,
                           env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin", "HOME": "/tmp"})
        tue = r.returncode != 0
    except subprocess.TimeoutExpired:
        tue = True  # un essai bloque : le defaut se voit aussi
    print(("TUE      " if tue else "SURVIT   ") + nom, flush=True)
    if not tue:
        survivants.append(nom)
print(f"{len(MUTATIONS) - len(survivants)}/{len(MUTATIONS)} mutations tuees")
sys.exit(1 if survivants else 0)
