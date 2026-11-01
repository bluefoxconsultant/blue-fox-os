"""Mutations du lot du 2026-10-25 au soir (profil qui grandit au besoin,
accueil refait quand Nextcloud n'a jamais ete configure) : chaque defaut
reintroduit doit faire echouer au moins un essai. A jouer dans le conteneur
d'essais : python3 /src/live/tests/mutations_1025b.py"""
import shutil
import subprocess
import sys

I = ("live", ["tests/test_session.py"])
MUTATIONS = [
    (I, "bfos_live/session.py", "(st.f_blocks - st.f_bfree) * st.f_frsize < total * SEUIL_AGRANDIR", "False",
     "croissance: sans seuil"),
    (I, "bfos_live/session.py", "(st.f_blocks - st.f_bfree) * st.f_frsize", "(st.f_blocks - st.f_bavail) * st.f_frsize",
     "croissance: seuil sur f_bavail"),
    (I, "bfos_live/session.py", "pas = min(actuelle, PAS_MAX_EXFAT", "pas = min(PAS_MAX_EXFAT",
     "croissance: ne double pas, prend tout"),
    (I, "bfos_live/session.py", "PAS_MAX_EXFAT if exfat else PAS_MAX", "PAS_MAX", "croissance: gros pas sur exFAT"),
    (I, "bfos_live/session.py", " - MARGE_LIBRE - reserve, ", " - MARGE_LIBRE, ", "croissance: reserve ignoree"),
    (I, "bfos_live/session.py", "        reserve_mb = cfg.get(\"profile_size_mb\") or 8192\n",
     "        reserve_mb = 0\n", "croissance: reserve par defaut nulle"),
    (I, "bfos_live/session.py", "or getattr(lp, \"f_blocks\", 0) * lp.f_frsize // 2)", "or 1 << 62)",
     "croissance: aucun plafond par defaut"),
    (I, "bfos_live/session.py", ", plafond - actuelle)", ")", "croissance: plafond ignore"),
    (I, "bfos_live/session.py", '        lancer(["losetup", "--set-capacity", boucle], run=run)\n', "",
     "croissance: boucle pas relue"),
    (I, "bfos_live/session.py", "        boucle = boucle_de(dev)\n        run([\"sync\"]",
     "        run([\"sync\"]", "croissance: boucle cherchee apres l'ecriture"),
    (I, "bfos_live/session.py", '"--key-file", "-", mappe], run=run, entree=cle)', '"--key-file", "-", mappe], run=run)',
     "croissance: cle absente du resize"),
    (I, "bfos_live/session.py", "    except (Echec, OSError, subprocess.SubprocessError) as exc:\n        journal(f\"profil non agrandi",
     "    except (Echec, OSError) as exc:\n        journal(f\"profil non agrandi", "croissance: delai depasse fatal"),
    (I, "bfos_live/session.py", 'if len(esclaves) != 1 or not esclaves[0].startswith("loop"):', "if not esclaves:",
     "boucle: deux boucles acceptees"),
    (I, "bfos_live/session.py", '            run(["resize2fs", dev], capture_output=True, timeout=600, env={"PATH": CHEMIN})\n        except subprocess',
     '            pass\n        except subprocess', "unite: reprise interrompue jamais finie"),
    (I, "bfos_live/session.py", "        for _n in range(PAS_PAR_PASSE):", "        while True:", "unite: pas illimites"),
    (I, "bfos_live/session.py", "                or not etat.get(\"profil_sain\") or not", "                or not",
     "unite: agrandit un ext4 abime"),
    (I, "bfos_live/session.py", "            etat[\"profil_sain\"] = controle < 4", "            etat[\"profil_sain\"] = True",
     "ouverture: e2fsck ignore"),
    (I, "bfos_live/session.py", "        if mode == \"persistant\" and not neuf and etat.get(\"profil_sain\"):",
     "        if mode == \"persistant\" and etat.get(\"profil_sain\"):", "demarrer: agrandit un profil neuf"),
    (I, "bfos_live/session.py", '            etat["cle_profil"] = base64.b64encode(cle).decode()\n', "",
     "demarrer: cle pas transmise a l'unite"),
    (I, "bfos_live/session.py", '            run(["systemctl", "stop", UNITE_AGRANDIR.format(nom=nom)], capture_output=True,\n                timeout=180, env={"PATH": CHEMIN})\n',
     "            pass\n", "arreter: agrandissement pas arrete"),
    (I, "bfos_live/session.py", "        if (deja_configure and nextcloud_a_refaire(",
     "        if (False and nextcloud_a_refaire(", "accueil: jamais refait"),
    (I, "bfos_live/session.py", "            if depot and poser_sous(", "            if poser_sous(", "accueil: refait sans identifiants"),
    (I, "bfos_live/session.py", "            elif depot:\n                revoquer_nextcloud(depot, http=http)\n", "",
     "accueil: identifiants inutiles pas revoques"),
    (I, "bfos_live/session.py", "        revoquer_nextcloud(refait, http=http)", "        pass",
     "arreter: refait non monte pas revoque"),
    (I, "bfos_live/session.py", "    if refait and not (etat.get(\"maison\") and os.path.lexists(", "    if refait and not (False and os.path.lexists(",
     "arreter: refait monte revoque"),
    (I, "bfos_live/session.py", "return bool(nc.get(\"url\")) and not os.path.lexists", "return not os.path.lexists",
     "accueil: refait sans Nextcloud dans la politique"),
    (I, "bfos_live/session.py", "suivant = os.open(partie, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)",
     "suivant = os.open(partie, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)", "accueil: suit un lien de la personne"),
    (I, "bfos_live/session.py", "        with Verrou(cfg, fichier=VERROU_PLACE):\n            actuelle, pas =",
     "        if True:\n            actuelle, pas =", "place: croissance hors verrou commun"),
    (I, "bfos_live/session.py", "        with Verrou(cfg, fichier=VERROU_PLACE):\n            taille = taille_profil",
     "        if True:\n            taille = taille_profil", "place: creation hors verrou commun"),
    (I, "bfos_live/session.py", 'return res.returncode != 0 or (res.stdout or "").strip().lower() == MAGIC_EXFAT',
     "return False", "exfat: jamais reconnu"),
    (I, "bfos_live/session.py", "                and not os.path.lexists(os.path.join(etat[\"maison\"], DRAPEAU_NC_REFAIT))):",
     "                and True):", "accueil: refait a chaque session"),
    (I, "bfos_live/session.py", "os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,", "os.O_WRONLY | os.O_CREAT,",
     "marque: suit un lien"),
    (I, "bfos_live/session.py", "        if etat.pop(\"cle_profil\", None):", "        if False:",
     "arreter: cle laissee dans l'etat"),
    (I, "bfos_live/session.py", "            except (OSError, subprocess.SubprocessError) as exc:\n                journal(f\"agrandissement non lance",
     "            except OSError as exc:\n                journal(f\"agrandissement non lance", "demarrer: delai fatal"),
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
