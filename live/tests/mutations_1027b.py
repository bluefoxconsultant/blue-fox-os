"""Mutations de la cle generique et des correctifs de la relecture n° 1 (2026-10-27) :
chaque defaut reintroduit doit faire echouer au moins un essai. Dans le conteneur d'essais :
QT_QPA_PLATFORM=offscreen python3 /src/live/tests/mutations_1027b.py"""
import shutil
import subprocess
import sys

L = ("live", ["tests/test_organisation.py", "tests/test_pam_helper.py", "tests/test_session.py", "tests/test_greeter.py"])
W = ("welcome", ["tests/test_tenant_organisation.py", "tests/test_emplacements_pwa.py"])
MUTATIONS = [
    (L, 'bfos_live/organisation.py', '        if cle in CLES_ODOO and urllib.parse.urlsplit(url).netloc.lower() != domaine:', '        if False:', 'odoo hors du domaine tape accepte'),
    (L, 'bfos_live/organisation.py', '        if cle in live and _hote(live[cle]) != hote_emetteur:', '        if False:', "point de jeton hors de l'emetteur accepte"),
    (L, 'bfos_live/organisation.py', '    if domaine_enregistre(hote_emetteur) != domaine_enregistre(domaine):', '    if False:', 'domaine sosie accepte'),
    (L, 'bfos_live/organisation.py', '        if cle in live_org and cle != "require_mfa":', '        if cle != "require_mfa":', 'liste blanche ignoree'),
    (L, 'bfos_live/organisation.py', '    cfg["require_mfa"] = bool(socle.get("require_mfa", True)) or bool(live_org.get("require_mfa"))', '    cfg["require_mfa"] = bool(live_org.get("require_mfa", True))', "2fa abaissee par l'organisation"),
    (L, 'bfos_live/organisation.py', '        if (unicode_ != hote) if not hote.isascii() else (unicode_.encode("idna").decode("ascii") != ascii_):\n            return None', '        pass', 'idna 2003 accepte'),
    (L, 'bfos_live/organisation.py', '    if port == "443":\n        port = ""', '    pass', 'port 443 garde'),
    (L, 'bfos_live/organisation.py', '        if not (st.st_mode & 0o170000 == 0o100000) or st.st_uid != 0 or st.st_size > TAILLE_MAX:', '        if False:', "depot d'un autre proprietaire lu"),
    (L, 'bfos_live/organisation.py', '    if not (st.st_mode & 0o170000 == 0o100000) or (proprietaire is not None and st.st_uid != proprietaire):\n        return False', '    pass', "drapeau d'un autre usager accepte"),
    (L, 'bfos_live/pam_helper.py', '    if cfg.get("organisation"):\n        return f"{claims[\'iss\']}\\0{claims[\'sub\']}"', '    pass', 'identite sans emetteur'),
    (L, 'bfos_live/pam_helper.py', '    except Exception as exc:  # noqa: BLE001\n        raise organisation.Erreur(N_("Ce domaine ne répond pas correctement. Vérifie-le."),', '    except organisation.Erreur as exc:  # noqa: BLE001\n        raise organisation.Erreur(N_("Ce domaine ne répond pas correctement. Vérifie-le."),', 'panne inattendue non rattrapee'),
    (L, 'bfos_live/pam_helper.py', '        if choix == "continuer":', '        if True:', 'confirmation ignoree'),
    (L, 'bfos_live/pam_helper.py', '    if actuelle and not changer:\n        return actuelle', '    if actuelle:\n        return actuelle', 'changer ignore'),
    (L, 'bfos_live/pam_helper.py', '            raise Refus(N_("Changement d\'organisation."), "changement demande a l\'ecran")', '            pass', 'qr pas interrompu'),
    (L, 'bfos_live/session.py', '        lancer(["cryptsetup", "open", "--test-passphrase", "--type", "luks2", "--key-file", "-",\n                ancien], run=run, entree=cle)', '        pass', "profil d'avant repris sans la cle"),
    (L, 'bfos_live/session.py', '    retirer = [] if generique else [a for a in retirer', '    retirer = [a for a in retirer', 'cle generique retire des applis'),
    (L, 'bfos_live/session.py', '            pilote, options, sous = "ext4", "noload", ""', '            pilote, options, sous = "ext4", "", ""', 'rejeu du journal a la lecture'),
    (L, 'bfos_live/greeter.py', '        if not texte or self.zone_domaine.isHidden():', '        if not texte:', 'domaine repondu deux fois'),
    (W, 'bluefox_welcome/tenant.py', '    return path.is_file() and not path.is_symlink() and st.st_uid == 0', '    return path.is_file()', "tenant d'organisation non verifie"),
    (W, 'bluefox_welcome/apply/pwa_lanceurs.py', '            or _extension_image(glyphe) != "svg" or _SVG_INTERDIT.search(glyphe)):', '            or _extension_image(glyphe) != "svg"):', 'svg piege insere'),
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
