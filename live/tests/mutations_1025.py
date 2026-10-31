"""Mutations du lot du 2026-10-25 (registre des mots de passe de root du
bureau itinerant) : chaque defaut reintroduit doit faire echouer au moins un
essai. A jouer dans le conteneur d'essais : python3 /src/live/tests/mutations_1025.py"""
import shutil
import subprocess
import sys

I = ("live", ["tests/test_itinerant.py", "tests/test_session.py"])
MUTATIONS = [
    (I, "bfos_live/itinerant.py", 'cond = {"If-Match": etag} if etag else {"If-None-Match": "*"}\n    statut, _h',
     'cond = {}\n    statut, _h', "registre: PUT sans condition"),
    (I, "bfos_live/itinerant.py", 'if ecrire_jetons(nuage, cle, nouvelle, etag):',
     'if ecrire_jetons(nuage, cle, nouvelle, etag) or True:', "registre: mise a jour perdue"),
    (I, "bfos_live/itinerant.py", 'chiffrer(io.BytesIO(json.dumps(garder).encode()), sortie, cle)',
     'sortie.write(json.dumps(garder).encode())', "registre: en clair"),
    (I, "bfos_live/itinerant.py", '                        and str(d.get("url", "")).startswith("https://")\n', '',
     "registre: entree non HTTPS"),
    (I, "bfos_live/itinerant.py", 'for d in depots][-JETONS_MAX:]', 'for d in depots]', "registre: sans plafond"),
    (I, "bfos_live/session.py", 'if revoquer_nextcloud(d, http=http)}', 'if False}', "ouverture: orphelins pas revoques"),
    (I, "bfos_live/session.py", '        noter_mot_de_passe_root(nuage, cle, depot, http=http)\n', '',
     "ouverture: mot de passe pas note"),
    (I, "bfos_live/session.py", '        oublier_mot_de_passe_root(nuage, cle, depot)\n        try:\n            nuage.retirer_verrou(etag)',
     '        try:\n            nuage.retirer_verrou(etag)', "ouverture ratee: reste au registre"),
    (I, "bfos_live/session.py",
     '    oublie = oublier_mot_de_passe_root(nuage, cle, depot)\n    if revoquer_nextcloud(depot, http=http):\n        return True\n',
     '    if revoquer_nextcloud(depot, http=http):\n        oublier_mot_de_passe_root(nuage, cle, depot)\n        return True\n    oublie = True\n',
     "fermeture: revoque avant de retirer"),
    (I, "bfos_live/session.py", '    if oublie:\n        try:\n            it.modifier_jetons', '    if False:\n        try:\n            it.modifier_jetons',
     "fermeture: revocation ratee pas remise"),
    (I, "bfos_live/session.py", 'return statut in (200, 401)', 'return statut == 200', "revocation: 401 pas mort"),
    (I, "bfos_live/session.py", 'if info.get("pret") and info.get("cle"):', 'if False:', "fermeture: registre ignore"),
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
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *essais],
                       cwd=f"/tmp/m/{racine}", capture_output=True, text=True,
                       env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    tue = r.returncode != 0
    print(("TUE      " if tue else "SURVIT   ") + nom)
    if not tue:
        survivants.append(nom)
print(f"{len(MUTATIONS) - len(survivants)}/{len(MUTATIONS)} mutations tuees")
sys.exit(1 if survivants else 0)
