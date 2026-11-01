"""Mutations du lot du 2026-10-26 (fichier du bureau perdu : renommage tenu
par un client de synchronisation, envoi orphelin jamais repris) : chaque
defaut reintroduit doit faire echouer au moins un essai. A jouer dans le
conteneur d'essais : python3 /src/live/tests/mutations_1026.py"""
import shutil
import subprocess
import sys

I = ("live", ["tests/test_itinerant.py"])
MUTATIONS = [
    (I, "bfos_live/itinerant.py", "    recuperer_orphelin(nuage, cle, tmp, statvfs=statvfs)\n", "", "ouverture: orphelin jamais repris"),
    (I, "bfos_live/itinerant.py", 'statut, _h, _c = self.requete_reprise("MOVE",', 'statut, _h, _c = self.requete("MOVE",',
     "renommage: pas repris"),
    (I, "bfos_live/itinerant.py", 'verrouille = (423, 500) if methode in ("COPY", "MOVE", "DELETE") else (423,)',
     "verrouille = (423,)", "reprise: 500 de MOVE pas repris"),
    (I, "bfos_live/itinerant.py", 'verrouille = (423, 500) if methode in ("COPY", "MOVE", "DELETE") else (423,)',
     "verrouille = (423, 500)", "reprise: 500 d'une lecture repris"),
    (I, "bfos_live/itinerant.py", "            if statut not in verrouille or pause is None:",
     "            if statut not in verrouille:", "reprise: sans fin"),
    (I, "bfos_live/itinerant.py", "    if reference and reference[1] >= date:\n        return None\n", "",
     "orphelin: plus ancien que le bureau mis en place"),
    (I, "bfos_live/itinerant.py", "    reference = fichiers.get(PROFIL) or fichiers.get(PRECEDENT)",
     "    reference = fichiers.get(PROFIL)", "orphelin: vieux promu par-dessus un precedent recent"),
    (I, "bfos_live/itinerant.py", "        nuage.deplacer(envoi, PROFIL, taille=os.path.getsize(chiffre))\n",
     "        try:\n            nuage.deplacer(envoi, PROFIL, taille=os.path.getsize(chiffre))\n"
     "        except Echec:\n            nuage.supprimer(envoi)\n            raise\n",
     "sauvegarde: envoi efface apres un renommage refuse"),
    (I, "bfos_live/itinerant.py", "        rate = not neuf and nuage.existe(envoi) is not None", "        rate = False",
     "sauvegarde: son propre renommage rate pris pour une disparition"),
    (I, "bfos_live/itinerant.py", "            if src not in liste and liste.get(dst, (None,))[0] == taille:\n                return\n",
     "            pass\n", "renommage: fait mais rendu 500 pris pour un echec"),
    (I, "bfos_live/itinerant.py", "    if MARQUEUR not in fichiers:\n", "    if False:\n",
     "orphelin: marqueur jamais pose"),
    (I, "bfos_live/itinerant.py", 'statut, ent, _c = self.requete_reprise("PUT", self.adresse(VERROU)',
     'statut, ent, _c = self.requete("PUT", self.adresse(VERROU)', "verrou: 423 pas repris"),
    (I, "bfos_live/itinerant.py", "                dechiffrer(e, _Puits(), cle)\n", "                pass\n",
     "orphelin: mis en place sans verification"),
    (I, "bfos_live/itinerant.py", "    if PROFIL in fichiers:\n        statut = nuage.copier(PROFIL, PRECEDENT)",
     "    if False:\n        statut = nuage.copier(PROFIL, PRECEDENT)", "orphelin: ancien bureau pas garde"),
    (I, "bfos_live/itinerant.py", "                       reverse=True)", "                       reverse=False)",
     "orphelin: le plus ancien choisi"),
    (I, "bfos_live/itinerant.py", "        shutil.rmtree(dossier, ignore_errors=True)\n    if PROFIL in fichiers:",
     "        pass\n    if PROFIL in fichiers:", "orphelin: copie claire laissee dans tmp"),
    (I, "bfos_live/itinerant.py", "    if v.f_bavail * v.f_frsize < fichiers[nom][0] + (64 << 20):",
     "    if False:", "orphelin: memoire pas comptee"),
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
