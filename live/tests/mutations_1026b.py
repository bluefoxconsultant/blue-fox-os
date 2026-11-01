"""Mutations du lot B du 2026-10-26 (ecran de connexion : progression a la
place du carre blanc, textes qui reviennent a la ligne, echelle, fuseau et
clavier de la personne) : chaque defaut reintroduit doit faire echouer au
moins un essai. A jouer dans le conteneur d'essais avec PyQt6 :
QT_QPA_PLATFORM=offscreen python3 /src/live/tests/mutations_1026b.py"""
import shutil
import subprocess
import sys

G = ("live", ["tests/test_greeter.py"])
P = ("live", ["tests/test_pam_helper.py"])
S = ("live", ["tests/test_session.py"])
MUTATIONS = [
    (G, "bfos_live/greeter.py", "        self.setVisible(self.matrice is not None)\n", "", "ecran: carre blanc garde"),
    (G, "bfos_live/greeter.py", "        self.barre.hide()\n        self.qr.montrer(donnees[\"uri_complete\"])",
     "        self.qr.montrer(donnees[\"uri_complete\"])", "ecran: barre sous le QR"),
    (G, "bfos_live/greeter.py", "        self.occupe(donnees[\"total\"], donnees[\"etape\"])", "        self.occupe()",
     "ecran: etapes ignorees"),
    (G, "bfos_live/greeter.py", "        self.etat.setWordWrap(True)\n", "", "ecran: etat deborde"),
    (G, "bfos_live/greeter.py", "            etiquette.setWordWrap(True)\n", "", "choix: texte deborde"),
    (G, "bfos_live/greeter.py", "        f = min(f, hauteur_px / 900)", "        pass", "echelle: carte trop haute"),
    (G, "bfos_live/greeter.py", "    if largeur_mm and largeur_mm >= 100 and largeur_px:", "    if False:",
     "echelle: densite ignoree"),
    (G, "bfos_live/greeter.py", "            and os.path.isfile(os.path.join(zoneinfo, fuseau))):", "            ):",
     "fuseau: inexistant accepte"),
    (G, "bfos_live/greeter.py", "        if self.barre.maximum() == 0 or self.barre.isHidden():\n            self.occupe()\n",
     "        self.occupe()\n", "ecran: info remet la barre a zero"),
    (G, "bfos_live/greeter_flow.py", "or not 1 <= etape <= total <= 20):", "):", "flux: progression hors bornes"),
    (G, "bfos_live/greeter_flow.py", "                elif progres is not None:\n                    rappels.sur_progres(progres)\n", "",
     "flux: progression jamais transmise"),
    (P, "bfos_live/pam_helper.py", "        res = (_attendre_en_relayant(redemarrer, fichier_progres, progres) if progres\n               else redemarrer())",
     "        res = redemarrer()", "aide: rien relaye"),
    (P, "bfos_live/pam_helper.py", "    for vieux in (resultat, fichier_progres):", "    for vieux in (resultat,):",
     "aide: vieille progression relayee"),
    (P, "bfos_live/pam_helper.py", "    return {\"etape\": d[\"etape\"], \"total\": d[\"total\"], \"texte\": d[\"texte\"][:200]}",
     "    return {\"etape\": d[\"etape\"], \"total\": d[\"total\"], \"texte\": d[\"texte\"]}", "aide: texte non borne"),
    (P, "bfos_live/pam_helper.py", "        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)\n        with os.fdopen(fd, encoding=\"utf-8\") as f:\n            d = json.load(f)",
     "        with open(chemin, encoding=\"utf-8\") as f:\n            d = json.load(f)", "aide: progression suit un lien"),
    (P, "bfos_live/pam_helper.py", "    if \"exc\" in boite:\n        raise boite[\"exc\"]\n", "", "aide: exception avalee"),
    (S, "bfos_live/session.py", "    if FUSEAU_RE.match(fuseau) and os.path.isfile(os.path.join(ZONEINFO, fuseau)):",
     "    if fuseau:", "session: fuseau douteux applique"),
    (S, "bfos_live/session.py", "        f = os.open(parties[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW",
     "        f = os.open(parties[-1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC", "session: kxkbrc ecrase ou suit un lien"),
    (S, "bfos_live/session.py", "    etapes = [(lambda: ecrire_sous(maison, \".config/kxkbrc\", apply_mod.render_kxkbrc(politique),\n                                   compte), \"kxkbrc\")]",
     "    etapes = []", "session: kxkbrc jamais seme"),
    (S, "bfos_live/session.py", "                appliquer_preferences(politique, apply_mod, compte, etat[\"maison\"], run=run)", "                pass",
     "session: preferences jamais appliquees"),
    (S, "bfos_live/session.py", "        annoncer(cfg, nom, 4, _(\"Connexion à Odoo…\"))\n", "", "session: etape sautee"),
    (S, "bfos_live/session.py", "            os.makedirs(os.path.dirname(chemin), mode=0o755, exist_ok=True)\n", "",
     "session: dossier du clavier absent"),
    (S, "bfos_live/session.py", "        os.fchown(f, compte.pw_uid, compte.pw_gid)\n", "", "session: kxkbrc a root"),
    (S, "bfos_live/session.py", "    if not (install.get(\"keymap\") or install.get(\"x_layout\")):",
     "    if not (install.get(\"keymap\") and install.get(\"x_layout\")):", "session: clavier exige les deux"),
    (G, "bfos_live/greeter.py", "                os.environ[\"QT_SCALE_FACTOR\"] = f\"{f:g}\"\n", "", "ecran: relance sans fin"),
    (G, "bfos_live/greeter.py", "[sys.executable, \"-I\", os.path.abspath(__file__)]", "[sys.executable, os.path.abspath(__file__)]",
     "ecran: relance sans -I"),
    (G, "bfos_live/greeter.py", "        except Exception:  # noqa: BLE001\n            os.environ.pop(\"QT_SCALE_FACTOR\", None)",
     "        except ZeroDivisionError:\n            pass", "ecran: plante sans ecran"),
    (P, "bfos_live/pam_helper.py", "or not 1 <= d[\"etape\"] <= d[\"total\"] <= 20):", "):", "aide: progression hors bornes"),
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
