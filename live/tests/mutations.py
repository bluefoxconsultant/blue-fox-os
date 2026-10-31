"""Mutations de la cale PAM et de l'aide (#26870) : chacune doit faire rougir les essais.

A jouer dans le conteneur : docker run --rm -v $PWD:/src:ro bfos-live-essais:43 python3 /src/live/tests/mutations.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

C = "live/pam/pam_bfos.c"
H = "live/bfos_live/pam_helper.py"
MUTATIONS = [
    ("sortie non nulle acceptee", C, "if (!ok || sortie == 0 || !nom[0])", "if (!ok || !nom[0])"),
    ("nom non valide", C, "if (nom[0] || !nom_valide(arg)) {", "if (nom[0]) {"),
    ("pas de PDEATHSIG", C, "prctl(PR_SET_PDEATHSIG, SIGTERM);", ";"),
    ("aide inscriptible groupe", C, "(st.st_mode & (S_IWGRP | S_IWOTH))", "(st.st_mode & S_IWOTH)"),
    ("aide d'un autre", C, "st.st_uid != 0 ||", ""),
    ("OK sans USER", C, "if (!ok || sortie == 0 || !nom[0])", "if (!ok || sortie == 0)"),
    ("desechappement casse", C, "*w++ = (r[1] == 'n') ? '\\n' : r[1];", "*w++ = r[1];"),
    ("environnement herite", C, "execve(chemin, args, envp);", "execve(chemin, args, environ);"),
    ("annulation = refus", C, "if (conv_rompue)\n        return PAM_CONV_ERR;",
     "if (conv_rompue)\n        return PAM_AUTH_ERR;"),
    ("aide pas tuee a l'annulation", C, "int sortie = terminer(&a, !ok, 0);", "int sortie = terminer(&a, 0, 0);"),
    ("verrouillage : autre compte admis", C, "if (demande_existe && strcmp(nom, demande) != 0) {", "if (0) {"),
    ("compte systeme admis", C, "if (!pw || pw->pw_uid < 1000) {", "if (!pw) {"),
    ("signaux non remis a zero", C, "            signal(sigs[i], SIG_DFL);", "            (void)sigs[i];"),
    ("fermeture non bornee", C, "terminer(&a, 0, 15);", "terminer(&a, 0, 0);"),
    ("reauth sans verifier le sub", H, '        compte = annuaire.compte_de(demande, claims["sub"])',
     "        compte = annuaire.getpwnam(demande)"),
    ("groupes vides = tout le monde", H, "    if not permis or not isinstance", "    if permis and not isinstance"),
    ("config sans groupes admise", H, '    if not cfg.get("allowed_groups"):', "    if False:"),
    ("nom sans suffixe", H, 'nom = f"{prefixe}-{suffixe}" if prefixe', "nom = prefixe if prefixe"),
    ("2FA non verifiee", H, '    if cfg.get("require_mfa", True):', "    if False:"),
    ("dossier non controle", H,
     "    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != proprietaire\n"
     "            or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):",
     "    if False:"),
    ("registre non root admis", H,
     "            if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):\n"
     "                return None",
     "            pass"),
    ("http admis en config", H, '        if cfg.get(cle) and not str(cfg[cle]).startswith("https://"):', "        if False:"),
    ("courriel non verifie utilise", H, '    if not brut and claims.get("email_verified") is True:', "    if not brut:"),
    ("collision de groupe ignoree", H, "        if self._existe(nom) is None and not self._groupe_existe(nom):",
     "        if self._existe(nom) is None:"),
    ("registre trafique admis", H, '        if (existant is not None and existant.pw_uid >= int(self.cfg["uid_min"])',
     "        if (existant is not None"),
    ("aud non verifiee", H, '    if cfg["client_id"] not in auds:', "    if False:"),
    ("jetons lisibles par tous", H, "    }, mode=0o600, uid=uid, gid=gid)", "    }, mode=0o644, uid=uid, gid=gid)"),
    ("accents non retires", H,
     'brut = "".join(c for c in brut if not unicodedata.combining(c)).lower()', "brut = brut.lower()"),
    ("PAM_USER inattendu admis", H,
     '    else:\n        raise Refus("Connexion impossible pour ce compte.",',
     '    else:\n        situation = "ouverture"\n        if False: raise Refus("Connexion impossible pour ce compte.",'),
    ("redirections suivies", H, "_OUVREUR = urllib.request.build_opener(_SansRedirection())",
     "_OUVREUR = urllib.request.build_opener()\n_SansRedirection = urllib.request.HTTPRedirectHandler"),
]
racine = Path("/work")
tues, survivants = 0, []
for nom, fichier, avant, apres in MUTATIONS:
    if racine.exists():
        shutil.rmtree(racine)
    shutil.copytree("/src/live", racine / "live")
    shutil.copytree("/src/install", racine / "install")
    f = racine / fichier
    s = f.read_text()
    if s.count(avant) != 1:
        print(f"!! motif introuvable ou multiple : {nom}")
        survivants.append(nom)
        continue
    f.write_text(s.replace(avant, apres))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                        "live/tests"], cwd=racine, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        tues += 1
        print(f"tuee      : {nom}")
    else:
        survivants.append(nom)
        print(f"SURVIVANTE: {nom}")
print(f"\n{tues}/{len(MUTATIONS)} tuees")
sys.exit(1 if survivants else 0)
