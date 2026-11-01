"""Mutations du lot du 2026-10-24 (Ventoy, lien Odoo, agent d'accueil) :
chaque defaut reintroduit doit faire echouer au moins un essai. A jouer dans
le conteneur d'essais : python3 /src/live/tests/mutations_1024.py"""
import shutil
import subprocess
import sys

L = ("live", ["tests/test_session.py", "tests/test_ouvrir_odoo.py"])
W = ("welcome", ["tests"])
P = ("live", ["tests/test_pam_helper.py"])
I = ("live", ["tests/test_itinerant.py", "tests/test_session.py"])
MUTATIONS = [
    (L, "bfos_live/session.py", 'if len(cibles) != 1:', 'if len(cibles) < 1:', "ventoy: plusieurs cibles"),
    (L, "bfos_live/session.py", 'champs[2] != "linear"', 'champs[2] == "xx"', "ventoy: non lineaire"),
    (L, "bfos_live/session.py", "        if decalage < debut or decalage + longueur > debut + taille:\n",
     "        if False:\n", "ventoy: segment hors partition"),
    (L, "bfos_live/session.py", 'if "VTOYEFI" not in noms:', 'if False:', "ventoy: pas de VTOYEFI"),
    (L, "bfos_live/session.py", 'if os.path.islink(rep) or', 'if False and os.path.islink(rep) or', "ventoy: lien symbolique"),
    (L, "bfos_live/session.py", 'if fs not in FS_VENTOY:', 'if fs not in FS_VENTOY and False:', "ventoy: fs inconnu"),
    (L, "bfos_live/session.py", '        run(["dmsetup", "remove", VENTOY_DM]', '        (lambda *a, **k: None)(["dmsetup", "remove", VENTOY_DM]', "ventoy: projection restee"),
    (L, "bfos_live/session.py", 'not lien.startswith(f"https://{hote}/odoo")', 'not lien.startswith("https://")', "lien: autre hote"),
    (L, "bfos_live/session.py", 'any(c in lien for c in "\\r\\n\\x00 ")', 'False', "lien: saut de ligne"),
    (L, "bfos_live/session.py", "os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)", "os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)", "lien: lisible par tous"),
    (L, "bfos_live/session.py", "    retirer_lien_odoo(cfg, nom)\n    refait = etat.get", "    refait = etat.get", "lien: pas retire a l'arret"),
    (L, "bfos_live/ouvrir_odoo.py", "> VALIDITE:", "> 10 ** 9:", "ouvrir: lien perime"),
    (L, "bfos_live/ouvrir_odoo.py", "os.O_RDONLY | os.O_NOFOLLOW", "os.O_RDONLY", "ouvrir: suit un lien"),
    (W, "bluefox_welcome/apply/rclone_mount.py", 'remote_path = chemin_distant(m.get("remote_path") or "", user)', 'remote_path = m.get("remote_path") or ""', "rclone: chemin serveur"),
    (W, "bluefox_welcome/apply/rclone_mount.py", "                if premier and urllib.parse.unquote(premier) == user:", "                if premier and premier == user:", "rclone: login encode"),
    (W, "bluefox_welcome/apply/brave_policy.py", 'and not p.get("pinned")', '', "brave: epinglee installee 2 fois"),
    (W, "bluefox_welcome/apply/pwa_lanceurs.py", "        if urllib.parse.urlsplit(src).hostname != hote:\n            continue\n", "", "pwa: icone d'un autre hote"),
    (W, "bluefox_welcome/apply/pwa_lanceurs.py", "        if not ext:\n            continue\n", "        ext = ext or 'png'\n", "pwa: pas une image"),
    (W, "bluefox_welcome/apply/pwa_lanceurs.py", 'if (l.indexOf(x) < 0) l.push(x);', 'l.push(x);', "pwa: doublon de lanceur"),
    (W, "bluefox_welcome/apply/pwa_lanceurs.py", 'and int(m.group(1)) > 0:', ':', "pwa: aucune barre = succes"),
    (W, "bluefox_welcome/apply/emplacements.py", "        if href in presents:\n            continue\n", "", "dolphin: doublon"),
    (W, "bluefox_welcome/apply/emplacements.py", '        return False, f"emplacements illisibles : {e}"', '        texte = _VIDE; racine = ET.fromstring(texte.encode())', "dolphin: fichier abime ecrase"),
    # 2026-10-24, apres le banc Ventoy reel et le bureau en VM.
    (L, "bfos_live/session.py", 'os.path.isdir(os.path.join(sysfs, "dev", "block", majmin, "dm"))', 'False',
     "ventoy: projection vue par lsblk seulement"),
    (L, "bfos_live/session.py", '["dmsetup", "table", "-j", maj, "-m", mineur]', '["dmsetup", "table", "/dev/mapper/ventoy"]',
     "ventoy: table lue par le nom"),
    (W, "bluefox_welcome/apply/pwa_lanceurs.py", "    if (!l || l.length === 0) l = defauts.slice();\n", "",
     "pwa: lanceurs par defaut effaces"),
    (W, "bluefox_welcome/apply/brave_policy.py", '"BraveP3AEnabled": False,', '', "brave: bandeau P3A"),
    (P, "bfos_live/pam_helper.py", 'if service in (cfg.get("lock_services") or ()):', 'if False:',
     "verrou: message long"),
    (P, "bfos_live/pam_helper.py", 'and len(code) % 3 == 0:', ':', "verrou: code mal groupe"),
    # 2026-10-24 soir : bureau itinerant dans Nextcloud (apres relecture adverse).
    (I, "bfos_live/session.py", '    if not info.get("pret"):\n        raise Echec(', '    if False:\n        raise Echec(',
     "itinerant: sauvegarde d'un bureau non restaure"),
    (I, "bfos_live/session.py", '        info["verrou"] = it.renouveler_verrou(nuage, info["session"], info["verrou"])\n', '',
     "itinerant: envoi sans renouveler le verrou"),
    (I, "bfos_live/itinerant.py", 'cond = {"If-Match": etag} if etag else {"If-None-Match": "*"}\n        statut, ent', 'cond = {}\n        statut, ent',
     "itinerant: verrou sans PUT conditionnel"),
    (I, "bfos_live/itinerant.py", 'if actuel.get("session") != session_id and age < VERROU_PERIME:', 'if False:',
     "itinerant: verrou d'autrui ignore"),
    (I, "bfos_live/itinerant.py", '        age = actuel.get("_age_serveur")\n', '        age = None\n',
     "itinerant: age du verrou a l'horloge locale"),
    (I, "bfos_live/itinerant.py", '            if statut == 404 and not neuf and not rate:\n', '            if False:\n',
     "itinerant: bureau disparu remplace"),
    (I, "bfos_live/itinerant.py", '        if traces:\n            raise Echec(', '        if False:\n            raise Echec(',
     "itinerant: 404 pris pour un bureau neuf"),
    (I, "bfos_live/itinerant.py", 'if rz != 0 or rt not in (0, 1) or (rt == 1 and not _changements_seulement(erreurs)):',
     'if rz != 0 or rt not in (0, 1):', "itinerant: tar en echec accepte"),
    (I, "bfos_live/itinerant.py", '        if taille_ouverte > RETRECI_MIN and taille < taille_ouverte * RETRECI:\n',
     '        if False:\n', "itinerant: bureau fondu envoye"),
    (I, "bfos_live/itinerant.py", '        vider_maison(maison, run=run)\n        # Clair', '        # Clair',
     "itinerant: maison pas videe avant extraction"),
    (I, "bfos_live/itinerant.py", '    "./.cache",\n', '', "itinerant: caches archives"),
    (I, "bfos_live/itinerant.py", 'v.f_bavail * v.f_frsize < taille + (64 << 20)', 'False',
     "itinerant: memoire libre ignoree"),
    (I, "bfos_live/session.py", '        try:\n            nuage.retirer_verrou(etag)\n        except Exception:  # noqa: BLE001\n            pass\n', '',
     "itinerant: verrou garde apres echec"),
    (I, "bfos_live/itinerant.py", 'stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,',
     'stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,', "itinerant: tube d'erreurs qui fige"),
    (I, "bfos_live/itinerant.py", '        mettre_de_cote(maison, compte, run=run)\n', '',
     "itinerant: travail non envoye efface"),
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
