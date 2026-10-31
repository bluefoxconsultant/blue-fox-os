"""Essais de l'aide de connexion de la cle USB live (#26870)."""
import base64
import io
import json
import os
import pwd
import stat
import urllib.error
import urllib.request

import pytest

from bfos_live import pam_helper as ph

ISSUER = "https://auth.exemple.test/application/o/blue-fox-os/"
CFG_BASE = {
    "issuer": ISSUER,
    "client_id": "blue-fox-os",
    "device_url": "https://auth.exemple.test/application/o/device/",
    "token_url": "https://auth.exemple.test/application/o/token/",
    "revoke_url": "https://auth.exemple.test/application/o/revoke/",
}
SUB = "a1b2c3d4e5f6"
MAINTENANT = 1_800_000_000


def cfg(tmp_path, **extra):
    c = dict(ph.DEFAUTS)
    c.update(CFG_BASE)
    c["state_dir"] = str(tmp_path / "run")
    c["allowed_groups"] = ["Employes"]
    c.update(extra)
    return c


def jeton(claims):
    def seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.c2lnbmF0dXJl"


def claims_ok(**extra):
    c = {"iss": ISSUER, "aud": "blue-fox-os", "sub": SUB, "exp": MAINTENANT + 300,
         "iat": MAINTENANT, "preferred_username": "Olivier", "name": "Olivier B",
         "groups": ["Employes"], "amr": ["pwd", "mfa"]}
    c.update(extra)
    return c


def pw(nom, uid, gid=None):
    return pwd.struct_passwd((nom, "x", uid, uid if gid is None else gid, "", f"/home/{nom}", "/bin/bash"))


# ------------------------------------------------------------------ protocole
@pytest.mark.parametrize("texte", ["simple", "deux\nlignes", "a\\b", "\\n litteral",
                                   "fin\\", "", "é → ✓"])
def test_echappement_aller_retour(texte):
    e = ph.echapper(texte)
    assert "\n" not in e
    assert ph.desechapper(e) == texte


def test_canal_question_lit_une_reponse_echappee():
    entree, sortie = io.StringIO("mot\\nde passe\n"), io.StringIO()
    canal = ph.Canal(entree, sortie)
    assert canal.demander("Code ?", secret=True) == "mot\nde passe"
    assert sortie.getvalue() == "SECRET Code ?\n"


def test_canal_question_sans_reponse_est_un_refus():
    canal = ph.Canal(io.StringIO(""), io.StringIO())
    with pytest.raises(ph.Refus):
        canal.demander("Code ?")


# ----------------------------------------------------------------- id_token
def test_id_token_valide(tmp_path):
    c = ph.verifier_id_token(jeton(claims_ok()), cfg(tmp_path), MAINTENANT)
    assert c["sub"] == SUB


@pytest.mark.parametrize("modif, raison", [
    ({"iss": "https://autre.test/"}, "iss"),
    ({"aud": "nextcloud"}, "aud"),
    ({"aud": ["blue-fox-os", "nextcloud"]}, "azp"),
    ({"exp": MAINTENANT - 1000}, "expire"),
    ({"iat": MAINTENANT + 1000}, "futur"),
    ({"sub": ""}, "sub"),
])
def test_id_token_refuse(tmp_path, modif, raison):
    with pytest.raises(ph.Refus) as exc:
        ph.verifier_id_token(jeton(claims_ok(**modif)), cfg(tmp_path), MAINTENANT)
    assert raison in exc.value.raison


def test_id_token_plusieurs_audiences_avec_azp(tmp_path):
    c = claims_ok(aud=["blue-fox-os", "nextcloud"], azp="blue-fox-os")
    assert ph.verifier_id_token(jeton(c), cfg(tmp_path), MAINTENANT)["sub"] == SUB


def test_id_token_sans_exp(tmp_path):
    c = claims_ok()
    del c["exp"]
    with pytest.raises(ph.Refus):
        ph.verifier_id_token(jeton(c), cfg(tmp_path), MAINTENANT)


@pytest.mark.parametrize("brut", ["pas-un-jeton", "a.b", "a.!!!.c", None])
def test_id_token_mal_forme(tmp_path, brut):
    with pytest.raises(ph.Refus):
        ph.verifier_id_token(brut, cfg(tmp_path), MAINTENANT)


def test_groupes_vides_personne_n_entre(tmp_path):
    with pytest.raises(ph.Refus):
        ph.verifier_groupes(claims_ok(), cfg(tmp_path, allowed_groups=[]))


def test_groupes_permis(tmp_path):
    ph.verifier_groupes(claims_ok(), cfg(tmp_path))


@pytest.mark.parametrize("groupes", [[], ["Invites"], "Employes", None])
def test_groupes_refuses(tmp_path, groupes):
    with pytest.raises(ph.Refus):
        ph.verifier_groupes(claims_ok(groups=groupes), cfg(tmp_path))


@pytest.mark.parametrize("amr", [None, [], ["pwd"], "mfa"])
def test_deux_facteurs_exiges(tmp_path, amr):
    with pytest.raises(ph.Refus) as exc:
        ph.verifier_id_token(jeton(claims_ok(amr=amr)), cfg(tmp_path), MAINTENANT)
    assert "mfa" in exc.value.raison


def test_deux_facteurs_desactivables(tmp_path):
    c = claims_ok()
    del c["amr"]
    assert ph.verifier_id_token(jeton(c), cfg(tmp_path, require_mfa=False), MAINTENANT)


# ----------------------------------------------------------------- noms
H6 = ph._empreinte(SUB)[:6]


@pytest.mark.parametrize("claims, prefixe", [
    ({"preferred_username": "Olivier"}, "olivier"),
    ({"preferred_username": "jace.martin@bf.test"}, "jace-martin"),
    ({"preferred_username": "Élodie Côté"}, "elodie-cote"),
    ({"email": "anne@bf.test", "email_verified": True}, "anne"),
])
def test_nom_toujours_suffixe_du_sub(claims, prefixe):
    assert ph.nom_de_base({"sub": SUB, **claims}) == f"{prefixe}-{H6}"


def test_meme_nom_affiche_deux_personnes_deux_comptes():
    """Un attaquant qui se nomme « Olivier » dans Authentik n'obtient pas le
    nom local d'Olivier."""
    a = ph.nom_de_base({"sub": SUB, "preferred_username": "Olivier"})
    b = ph.nom_de_base({"sub": "attaquant", "preferred_username": "Olivier"})
    assert a != b


def test_courriel_non_verifie_ignore():
    nom = ph.nom_de_base({"sub": SUB, "email": "olivier@bf.test", "email_verified": False})
    assert not nom.startswith("olivier")


@pytest.mark.parametrize("brut", ["123", "", "---", "日本"])
def test_nom_de_base_repli_sur_le_sub(brut):
    nom = ph.nom_de_base({"sub": SUB, "preferred_username": brut})
    assert ph.NOM_VALIDE.match(nom)
    assert nom.endswith(H6) or nom == "u" + ph._empreinte(SUB)[:10]


def test_nom_tient_dans_32():
    nom = ph.nom_de_base({"sub": SUB, "preferred_username": "x" * 80})
    assert len(nom) <= 32 and ph.NOM_VALIDE.match(nom) and nom.endswith(H6)


def test_uid_stable_et_dans_la_plage(tmp_path):
    c = cfg(tmp_path)
    a, b = ph.uid_de_base(SUB, c), ph.uid_de_base(SUB, c)
    assert a == b and c["uid_min"] <= a <= c["uid_max"]
    assert ph.uid_de_base("autre", c) != a


# ----------------------------------------------------------------- annuaire
class FauxSysteme:
    """passwd et groupes en memoire + useradd simule."""

    def __init__(self, comptes=None, groupes=("root", "wheel", "users")):
        self.comptes = {c.pw_name: c for c in (comptes or [pw("root", 0), pw("bin", 1)])}
        self.groupes = set(groupes)
        self.appels = []

    def getpwnam(self, nom):
        return self.comptes[nom]

    def getpwuid(self, uid):
        for c in self.comptes.values():
            if c.pw_uid == uid:
                return c
        raise KeyError(uid)

    def getgrnam(self, nom):
        if nom in self.groupes:
            return nom
        raise KeyError(nom)

    def run(self, argv, **kw):
        self.appels.append((argv, kw))
        nom, uid = argv[-1], int(argv[argv.index("--uid") + 1])
        self.comptes[nom] = pw(nom, uid)
        self.groupes.add(nom)

        class R:
            returncode = 0
            stderr = ""
        return R()

    def annuaire(self, c):
        return ph.Annuaire(c, getpwnam=self.getpwnam, getpwuid=self.getpwuid,
                           getgrnam=self.getgrnam, run=self.run)


def test_choisir_nouvel_usager(tmp_path):
    nom, uid, creer = FauxSysteme().annuaire(cfg(tmp_path)).choisir(claims_ok())
    assert (nom, creer) == (f"olivier-{H6}", True)
    assert uid == ph.uid_de_base(SUB, cfg(tmp_path))


def test_choisir_root_reste_suffixe(tmp_path):
    nom, uid, creer = FauxSysteme().annuaire(cfg(tmp_path)).choisir(claims_ok(preferred_username="root"))
    assert nom == f"root-{H6}" and creer and uid >= 10000


def test_choisir_reprend_son_propre_compte(tmp_path):
    c = cfg(tmp_path)
    a = FauxSysteme().annuaire(c)
    nom, uid, _ = a.choisir(claims_ok())
    a.creer(nom, uid, claims_ok())
    assert a.choisir(claims_ok()) == (nom, uid, False)


def test_choisir_nom_pris_par_un_compte_etranger(tmp_path):
    sysm = FauxSysteme()
    sysm.comptes[f"olivier-{H6}"] = pw(f"olivier-{H6}", 20000)
    with pytest.raises(ph.Refus):
        sysm.annuaire(cfg(tmp_path)).choisir(claims_ok())


def test_choisir_nom_egal_a_un_groupe(tmp_path):
    sysm = FauxSysteme()
    sysm.groupes.add(f"olivier-{H6}")
    with pytest.raises(ph.Refus):
        sysm.annuaire(cfg(tmp_path)).choisir(claims_ok())


def test_choisir_jamais_le_nom_d_attente(tmp_path):
    c = cfg(tmp_path, placeholder_user=f"olivier-{H6}")
    nom, _uid, _ = FauxSysteme().annuaire(c).choisir(claims_ok())
    assert nom != c["placeholder_user"]


def test_registre_non_root_ignore(tmp_path):
    """Un registre forge par un usager (fichier qui ne lui appartient pas a root)
    ne fait reprendre aucun compte."""
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    a = sysm.annuaire(c)
    nom, uid, _ = a.choisir(claims_ok())
    a.creer(nom, uid, claims_ok())
    reg = os.path.join(c["state_dir"], "users", nom + ".json")
    os.chown(reg, 1000, 1000)
    assert a.proprietaire(nom) is None
    assert a.compte_de(nom, SUB) is None


def test_registre_ne_revendique_pas_un_compte_systeme(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    os.makedirs(os.path.join(c["state_dir"], "users"))
    with open(os.path.join(c["state_dir"], "users", "bin.json"), "w") as f:
        json.dump({"sub": SUB}, f)
    assert sysm.annuaire(c).compte_de("bin", SUB) is None


def test_uid_occupe_on_sonde_le_suivant(tmp_path):
    c = cfg(tmp_path)
    attendu = ph.uid_de_base(SUB, c)
    sysm = FauxSysteme([pw("root", 0), pw("occupant", attendu)])
    _nom, uid, _ = sysm.annuaire(c).choisir(claims_ok())
    assert uid != attendu and c["uid_min"] <= uid <= c["uid_max"]


def test_creer_appelle_useradd_sans_coquille(tmp_path):
    c = cfg(tmp_path, local_groups=["users", "mauvais groupe;rm"])
    sysm = FauxSysteme()
    sysm.annuaire(c).creer("olivier", 23456, claims_ok(name="Olivier: B,\nroot"))
    argv, kw = sysm.appels[0]
    assert argv[0] == "useradd" and argv[-1] == "olivier"
    gecos = argv[argv.index("--comment") + 1]
    assert ":" not in gecos and "\n" not in gecos and "," not in gecos
    assert argv[argv.index("--groups") + 1] == "users"
    assert kw["env"] == {"PATH": "/usr/sbin:/usr/bin"}
    reg = os.path.join(c["state_dir"], "users", "olivier.json")
    assert json.load(open(reg)) == {"sub": SUB, "uid": 23456, "identifiant": "Olivier"}
    st = os.stat(reg)
    assert stat.S_IMODE(st.st_mode) == 0o644 and st.st_uid == 0


def test_creer_echec_useradd(tmp_path):
    class R:
        returncode = 9
        stderr = "useradd: group exists"
    a = ph.Annuaire(cfg(tmp_path), run=lambda *a, **k: R())
    with pytest.raises(ph.Refus) as exc:
        a.creer("olivier", 23456, claims_ok())
    assert "useradd" in exc.value.raison


# ----------------------------------------------------------------- dossiers
def test_dossier_piege_par_un_lien(tmp_path):
    c = cfg(tmp_path)
    os.makedirs(c["state_dir"], mode=0o755)
    cible = tmp_path / "ailleurs"
    cible.mkdir()
    os.symlink(cible, os.path.join(c["state_dir"], "users"))
    with pytest.raises(ph.Refus) as exc:
        FauxSysteme().annuaire(c).creer("olivier", 23456, claims_ok())
    assert "suspect" in exc.value.raison
    assert list(cible.iterdir()) == []


@pytest.mark.parametrize("mode, uid", [(0o777, 0), (0o775, 0), (0o755, 1000)])
def test_dossier_d_etat_suspect(tmp_path, mode, uid):
    c = cfg(tmp_path)
    os.makedirs(c["state_dir"])
    os.chmod(c["state_dir"], mode)
    os.chown(c["state_dir"], uid, uid)
    with pytest.raises(ph.Refus):
        ph.dossier_sur(c["state_dir"], 0o755)


# ----------------------------------------------------------------- transport
def test_post_refuse_http(tmp_path):
    with pytest.raises(OSError):
        ph.post_https("http://auth.exemple.test/token", {})


def test_redirection_refusee():
    with pytest.raises(urllib.error.HTTPError):
        ph._SansRedirection().redirect_request(
            urllib.request.Request("https://a.test/"), io.BytesIO(b""), 302, "Found",
            {}, "https://ailleurs.test/")


# ----------------------------------------------------------------- ecrans
DEVICE = {"device_code": "DC", "user_code": "ABCD-EFGH", "interval": 1, "expires_in": 600,
          "verification_uri": "https://auth.exemple.test/device",
          "verification_uri_complete": "https://auth.exemple.test/device?code=ABCD-EFGH"}


def test_message_riche_pour_greetd(tmp_path):
    m = ph.message_appareil(DEVICE, "greetd", cfg(tmp_path))
    premiere, reste = m.split("\n", 1)
    assert premiere.startswith("bfos:device ")
    d = json.loads(premiere[len("bfos:device "):])
    assert d["uri_complete"].endswith("ABCD-EFGH") and d["user_code"] == "ABCD-EFGH"
    assert "ABCD-EFGH" in reste


def test_message_console_porte_un_qr(tmp_path):
    m = ph.message_appareil(DEVICE, "login", cfg(tmp_path))
    assert "▀" in m or "█" in m or "▄" in m
    assert "ABCD-EFGH" in m and "bfos:device" not in m


def test_message_texte_seul_ailleurs(tmp_path):
    m = ph.message_appareil(DEVICE, "sudo", cfg(tmp_path))
    assert "bfos:device" not in m and "▀" not in m and "ABCD-EFGH" in m


def test_message_court_a_l_ecran_de_verrouillage(tmp_path):
    d = dict(DEVICE, user_code="748240472")
    m = ph.message_appareil(d, "kde", cfg(tmp_path))
    assert m == ("Pour déverrouiller, va sur auth.exemple.test/device avec ton téléphone\n"
                 "et entre le code 748 240 472.")
    assert "bfos:device" not in m and "?code=" not in m


@pytest.mark.parametrize("code, lu", [("748240472", "748 240 472"), ("123456", "123 456"),
                                       ("ABCD-EFGH", "ABCD-EFGH"), ("1234567", "1234567"), ("", "")])
def test_code_lisible(code, lu):
    assert ph.code_lisible(code) == lu


def test_pile_de_l_ecran_de_verrouillage():
    """L'image branche pam_bfos.so EN TETE de la pile kde, en `sufficient`, et
    jamais dans la pile sans saisie (kde-fingerprint)."""
    racine = os.path.join(os.path.dirname(__file__), "..", "image", "files", "etc", "pam.d")
    auth = [l.split() for l in open(os.path.join(racine, "kde")) if l.startswith("auth")]
    assert auth[0] == ["auth", "sufficient", "pam_bfos.so"]
    assert not os.path.exists(os.path.join(racine, "kde-fingerprint"))


# ----------------------------------------------------------------- auth
class FauxAuthentik:
    def __init__(self, reponses_jeton):
        self.reponses = list(reponses_jeton)
        self.appels = []

    def post(self, url, data):
        self.appels.append((url, data))
        if url == CFG_BASE["device_url"]:
            return 200, json.dumps(DEVICE)
        if url == CFG_BASE["token_url"]:
            r = self.reponses.pop(0)
            if isinstance(r, Exception):
                raise r
            return 200, json.dumps(r)
        if url == CFG_BASE["revoke_url"]:
            return 200, ""
        raise AssertionError(url)


def http_err(code, err):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps({"error": err}).encode()))


def reponse_ok(**claims):
    return {"access_token": "AT", "refresh_token": "RT", "expires_in": 300,
            "id_token": jeton(claims_ok(**claims))}


class FauxSystemctl:
    """systemctl simule : l'unite de session rend le resultat demande pour
    chaque mode, et on garde la demande qu'elle aurait lue."""

    def __init__(self, c, resultats=None):
        self.c = c
        self.resultats = dict(resultats or {})
        self.appels = []
        self.demandes = []

    def __call__(self, argv, **kw):
        self.appels.append(argv)

        class R:
            returncode = 0
            stdout = stderr = ""
        if argv[:2] == ["systemctl", "restart"]:
            nom = argv[2].split("@", 1)[1].rsplit(".service", 1)[0]
            chemin = os.path.join(self.c["state_dir"], "demandes", nom + ".json")
            st = os.stat(chemin)
            assert stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == 0
            d = json.load(open(chemin))
            os.unlink(chemin)
            self.demandes.append(d)
            ok, message = self.resultats.get(d["mode"], (True, ""))
            ph._ecrire(os.path.join(self.c["state_dir"], "resultats", nom + ".json"),
                       {"ok": ok, "message": message})
            R.returncode = 0 if ok else 1
        return R()


def jouer_auth(c, sysm, reponses, service="greetd", demande=None, choix="ephemere\n",
               systemctl=None, verifier=None, sleep=lambda s: None, compteur=None):
    ak = FauxAuthentik(reponses)
    sortie = io.StringIO()
    env = {"PAM_SERVICE": service,
           "PAM_USER": c["placeholder_user"] if demande is None else demande}
    systemctl = systemctl or FauxSystemctl(c)
    nom = ph.auth(ph.Canal(io.StringIO(choix), sortie), c, env=env, post=ak.post,
                  sleep=sleep, annuaire=sysm.annuaire(c), maintenant=MAINTENANT,
                  run=systemctl, verifier=verifier,
                  compteur=compteur or os.path.join(c["state_dir"], "echecs"))
    jouer_auth.systemctl = systemctl
    return nom, sortie.getvalue().splitlines(), ak


def lancer_auth(tmp_path, reponses, service="greetd", **cfg_extra):
    c = cfg(tmp_path, **cfg_extra)
    sysm = FauxSysteme()
    nom, lignes, ak = jouer_auth(c, sysm, reponses, service)
    return nom, lignes, c, sysm, ak


def test_auth_bout_en_bout(tmp_path):
    nom, lignes, c, sysm, ak = lancer_auth(
        tmp_path, [http_err(400, "authorization_pending"), reponse_ok()])
    assert nom == f"olivier-{H6}"
    assert lignes[0].startswith("INFO bfos:device ")
    assert lignes[-2:] == [f"USER {nom}", "OK"]
    assert len(sysm.appels) == 1
    jetons_f = os.path.join(c["state_dir"], "sessions", nom + ".json")
    j = json.load(open(jetons_f))
    assert j["access_token"] == "AT" and j["refresh_token"] == "RT" and j["sub"] == SUB
    st = os.stat(jetons_f)
    assert stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == sysm.comptes[nom].pw_uid
    assert "openid" in ak.appels[0][1]["scope"]


def test_auth_deuxieme_ouverture_ne_recree_pas(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    for _ in range(2):
        jouer_auth(c, sysm, [reponse_ok()])
    assert len(sysm.appels) == 1


def test_deverrouillage_par_la_meme_personne(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    nom2, lignes, _ak = jouer_auth(c, sysm, [reponse_ok()], service="kde", demande=nom,
                                   choix="\n")
    assert nom2 == nom and lignes[-2:] == [f"USER {nom}", "OK"]
    assert len(sysm.appels) == 1


def test_deverrouillage_par_un_passant_refuse(tmp_path):
    """A verrouille sa session ; B scanne le QR et approuve avec SON compte."""
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom_a, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    with pytest.raises(ph.Refus) as exc:
        jouer_auth(c, sysm, [reponse_ok(sub="passant-b", preferred_username="b")],
                   service="kde", demande=nom_a, choix="\n")
    assert "quelqu'un d'autre" in exc.value.message
    assert len(sysm.appels) == 1


def test_reauthentification_d_un_compte_systeme_refusee(tmp_path):
    c = cfg(tmp_path)
    with pytest.raises(ph.Refus):
        jouer_auth(c, FauxSysteme(), [reponse_ok()], service="kde", demande="root")


@pytest.mark.parametrize("demande", ["", "inconnu", "../x", "Root"])
def test_pam_user_inattendu_refuse_avant_le_qr(tmp_path, demande):
    c = cfg(tmp_path)
    ak = FauxAuthentik([])
    with pytest.raises(ph.Refus):
        ph.auth(ph.Canal(io.StringIO(), io.StringIO()), c,
                env={"PAM_SERVICE": "greetd", "PAM_USER": demande}, post=ak.post,
                sleep=lambda s: None, annuaire=FauxSysteme().annuaire(c))
    assert ak.appels == []


@pytest.mark.parametrize("reponse, attendu", [
    (http_err(400, "access_denied"), "refusée"),
    (http_err(400, "expired_token"), "expiré"),
    (http_err(400, "invalid_client"), "ne répond plus"),
])
def test_auth_refus_lisibles(tmp_path, reponse, attendu):
    with pytest.raises(ph.Refus) as exc:
        lancer_auth(tmp_path, [reponse])
    assert attendu in exc.value.message


def test_auth_sans_id_token(tmp_path):
    with pytest.raises(ph.Refus) as exc:
        lancer_auth(tmp_path, [{"access_token": "AT"}])
    assert "id_token" in exc.value.raison


def test_auth_groupe_refuse_ne_cree_rien(tmp_path):
    c = cfg(tmp_path, allowed_groups=["Admins"])
    sysm = FauxSysteme()
    with pytest.raises(ph.Refus):
        jouer_auth(c, sysm, [reponse_ok()])
    assert sysm.appels == []
    assert not os.path.exists(os.path.join(c["state_dir"], "sessions"))


def test_auth_sans_mfa_ne_cree_rien(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    with pytest.raises(ph.Refus):
        jouer_auth(c, sysm, [reponse_ok(amr=["pwd"])])
    assert sysm.appels == []


def test_auth_jeton_d_une_autre_application(tmp_path):
    with pytest.raises(ph.Refus):
        lancer_auth(tmp_path, [reponse_ok(aud="nextcloud")])


def test_auth_reseau_absent_au_depart(tmp_path):
    c = cfg(tmp_path)

    def post(url, data):
        raise OSError("Network is unreachable")
    with pytest.raises(ph.Refus) as exc:
        ph.auth(ph.Canal(io.StringIO(), io.StringIO()), c,
                env={"PAM_USER": c["placeholder_user"]}, post=post,
                sleep=lambda s: None, annuaire=FauxSysteme().annuaire(c))
    assert "réseau" in exc.value.message


# ----------------------------------------------------------------- close
def test_close_revoque_puis_efface(tmp_path):
    nom, _l, c, _s, _ak = lancer_auth(tmp_path, [reponse_ok()])
    ak = FauxAuthentik([])
    systemctl = FauxSystemctl(c)
    ordre = []

    def post(url, data):
        ordre.append("revocation")
        return ak.post(url, data)

    def run(argv, **kw):
        ordre.append("stop")
        return systemctl(argv, **kw)
    ph.close(c, nom, post=post, run=run)
    # Revocation D'ABORD (la cale tue l'aide a 15 s), puis arret sans attendre.
    assert ordre == ["revocation", "revocation", "stop"]
    assert systemctl.appels == [["systemctl", "stop", "--no-block", f"bfos-live-session@{nom}.service"]]
    assert [d["token_type_hint"] for _u, d in ak.appels] == ["refresh_token", "access_token"]
    assert [d["token"] for _u, d in ak.appels] == ["RT", "AT"]
    assert all(d["client_id"] == "blue-fox-os" for _u, d in ak.appels)
    assert not os.path.exists(os.path.join(c["state_dir"], "sessions", nom + ".json"))


def test_close_efface_meme_si_la_revocation_echoue(tmp_path):
    nom, _l, c, _s, _ak = lancer_auth(tmp_path, [reponse_ok()])

    def post(url, data):
        raise OSError("hors ligne")
    ph.close(c, nom, post=post, run=FauxSystemctl(c))
    assert not os.path.exists(os.path.join(c["state_dir"], "sessions", nom + ".json"))


@pytest.mark.parametrize("nom", ["", "../etc/shadow", "Root", None])
def test_close_nom_invalide_ne_touche_a_rien(tmp_path, nom):
    ph.close(cfg(tmp_path), nom, post=lambda *a: (_ for _ in ()).throw(AssertionError))


# ----------------------------------------------------------------- main
def test_main_sans_configuration_refuse_proprement(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ph, "CONFIG", str(tmp_path / "absent.json"))
    assert ph.main(["auth"]) == 1
    lignes = capsys.readouterr().out.splitlines()
    assert lignes[0].startswith("ERROR ") and lignes[1].startswith("FAIL ")
    assert "configuration" in lignes[1]


CFG_COMPLETE = {**CFG_BASE, "allowed_groups": ["Employes"]}


def test_main_erreur_interne_sans_pile(tmp_path, monkeypatch, capsys):
    f = tmp_path / "live.json"
    f.write_text(json.dumps(CFG_COMPLETE))
    monkeypatch.setattr(ph, "CONFIG", str(f))
    monkeypatch.setattr(ph, "auth", lambda canal, cfg: 1 / 0)
    assert ph.main(["auth"]) == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out and "FAIL erreur interne" in out


@pytest.mark.parametrize("brut, raison", [
    # Sans fournisseur : cle generique, refusee tant qu'aucune organisation n'est choisie.
    ({}, "sans organisation"),
    ({**CFG_BASE, "issuer": "", "allowed_groups": ["x"]}, "sans organisation"),
    ({**CFG_COMPLETE, "uid_min": 500}, "1000"),
    ({**CFG_COMPLETE, "uid_min": 9000, "uid_max": 100}, "plage"),
    ({**CFG_BASE}, "allowed_groups"),
    ({**CFG_COMPLETE, "token_url": "http://auth.exemple.test/token/"}, "https"),
    ({**CFG_COMPLETE, "placeholder_user": "Pas Valide"}, "placeholder"),
])
def test_configuration_refusee(tmp_path, brut, raison):
    f = tmp_path / "live.json"
    f.write_text(json.dumps(brut))
    with pytest.raises(ph.Refus) as exc:
        ph.charger_config(str(f))
    assert raison in exc.value.raison


def test_mode_inconnu(tmp_path, monkeypatch):
    f = tmp_path / "live.json"
    f.write_text(json.dumps(CFG_COMPLETE))
    monkeypatch.setattr(ph, "CONFIG", str(f))
    assert ph.main(["n-importe"]) == 1


# ----------------------------------------------------------- choix de session
def test_choix_riche_pour_l_ecran_maison(tmp_path):
    nom, lignes, c, _s, _ak = lancer_auth(tmp_path, [reponse_ok()])
    ask = [l for l in lignes if l.startswith("ASK ")]
    assert len(ask) == 1 and ask[0].startswith("ASK bfos:choix ")
    d = json.loads(ph.desechapper(ask[0][4:]).split("\n", 1)[0][len("bfos:choix "):])
    assert [o["id"] for o in d["options"]] == ["ephemere", "persistant", "itinerant"]
    assert jouer_auth.systemctl.demandes == [{"mode": "ephemere", "sub": SUB, "access_token": "AT",
                                             "langue": "fr"}]
    assert lignes[-2:] == [f"USER {nom}", "OK"]


def test_choix_numerote_sur_console(tmp_path):
    c = cfg(tmp_path)
    nom, lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()], service="login",
                                  choix="2\n")
    ask = [ph.desechapper(l[4:]) for l in lignes if l.startswith("ASK ")]
    assert "bfos:choix" not in ask[0] and "1) Session éphémère" in ask[0]
    assert jouer_auth.systemctl.demandes[0]["mode"] == "persistant"


def test_persistance_et_itinerance_coupees_ne_demandent_rien(tmp_path):
    c = cfg(tmp_path, persistence=False, roaming=False)
    _nom, lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()], choix="")
    assert not [l for l in lignes if l.startswith("ASK ")]
    assert jouer_auth.systemctl.demandes[0]["mode"] == "ephemere"


@pytest.mark.parametrize("reglages, attendus", [
    ({}, ["ephemere", "persistant", "itinerant"]),
    ({"persistence": False}, ["ephemere", "itinerant"]),
    ({"roaming": False}, ["ephemere", "persistant"]),
    ({"persistence": False, "roaming": False}, ["ephemere"]),
])
def test_choix_offerts(tmp_path, reglages, attendus):
    assert [o["id"] for o in ph.choix_offerts(cfg(tmp_path, **reglages))] == attendus


def test_itinerant_choisi_sur_console(tmp_path):
    c = cfg(tmp_path)
    _nom, _lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()], service="login", choix="3\n")
    assert jouer_auth.systemctl.demandes[0]["mode"] == "itinerant"


def test_choix_incompris_trois_fois_annule(tmp_path):
    c = cfg(tmp_path)
    with pytest.raises(ph.Refus):
        jouer_auth(c, FauxSysteme(), [reponse_ok()], choix="x\ny\nz\n")


def test_profil_en_echec_propose_l_ephemere_en_le_disant(tmp_path):
    c = cfg(tmp_path)
    systemctl = FauxSystemctl(c, {"persistant": (False, "Ton profil persistant a été révoqué.")})
    nom, lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()],
                                  choix="persistant\nephemere\n", systemctl=systemctl)
    assert [d["mode"] for d in systemctl.demandes] == ["persistant", "ephemere"]
    second = ph.desechapper([l for l in lignes if l.startswith("ASK ")][1][4:])
    assert "révoqué" in second and "annuler" in second
    assert lignes[-1] == "OK"


def test_profil_en_echec_puis_annuler(tmp_path):
    c = cfg(tmp_path)
    systemctl = FauxSystemctl(c, {"persistant": (False, "Plus de place.")})
    with pytest.raises(ph.Refus):
        jouer_auth(c, FauxSysteme(), [reponse_ok()], choix="persistant\nannuler\n",
                   systemctl=systemctl)
    assert [d["mode"] for d in systemctl.demandes] == ["persistant"]


def test_session_ephemere_en_echec_refuse_avec_le_message(tmp_path):
    c = cfg(tmp_path)
    systemctl = FauxSystemctl(c, {"ephemere": (False, "Erreur interne en préparant ta session.")})
    with pytest.raises(ph.Refus) as exc:
        jouer_auth(c, FauxSysteme(), [reponse_ok()], systemctl=systemctl)
    assert "préparant" in exc.value.message


def test_reauthentification_ne_relance_pas_la_session(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    systemctl = FauxSystemctl(c)
    jouer_auth(c, sysm, [reponse_ok()], demande=nom, choix="", systemctl=systemctl)
    assert systemctl.appels == []


def test_resultat_absent_vaut_echec(tmp_path):
    c = cfg(tmp_path)

    def run(argv, **kw):
        class R:
            returncode = 0
        return R()
    ok, message, _lu = ph.lancer_session(c, "olivier-abc123", SUB, "ephemere", "AT", run=run)
    assert not ok and message


def test_close_sans_jetons_arrete_quand_meme_l_unite(tmp_path):
    c = cfg(tmp_path)
    systemctl = FauxSystemctl(c)
    ph.close(c, "olivier-abc123", post=lambda *a: (_ for _ in ()).throw(AssertionError),
             run=systemctl)
    assert systemctl.appels == [["systemctl", "stop", "--no-block",
                                 "bfos-live-session@olivier-abc123.service"]]


def test_delai_depasse_rattrape(tmp_path):
    import subprocess
    c = cfg(tmp_path)
    appels = []

    def run(argv, **kw):
        appels.append(argv)
        if argv[:2] == ["systemctl", "restart"]:
            raise subprocess.TimeoutExpired(argv, 1)

        class R:
            returncode = 0
        return R()
    ok, message, _lu = ph.lancer_session(c, "olivier-abc123", SUB, "persistant", "AT", run=run)
    assert not ok and "trop de temps" in message
    assert ["systemctl", "stop", "--no-block", "bfos-live-session@olivier-abc123.service"] in appels


def test_filet_pose_apres_une_preparation_reussie(tmp_path):
    c = cfg(tmp_path)
    jouer_auth(c, FauxSysteme(), [reponse_ok()])
    filets = [a for a in jouer_auth.systemctl.appels if a[0] == "systemd-run"]
    assert len(filets) == 1 and "--on-active=180" in filets[0]
    assert filets[0][-3:] == ["/usr/libexec/bfos-live/session", "garder", f"olivier-{H6}"]


def test_profil_neuf_annonce(tmp_path):
    c = cfg(tmp_path)

    class Neuf(FauxSystemctl):
        def __call__(self, argv, **kw):
            r = super().__call__(argv, **kw)
            if argv[:2] == ["systemctl", "restart"]:
                nom = argv[2].split("@", 1)[1].rsplit(".service", 1)[0]
                ph._ecrire(os.path.join(c["state_dir"], "resultats", nom + ".json"),
                           {"ok": True, "profil_neuf": True})
            return r
    _nom, lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()], choix="persistant\n",
                                   systemctl=Neuf(c))
    assert any("Nouveau profil" in l for l in lignes)


def test_jeton_rafraichi_s_il_a_attendu(tmp_path):
    c = cfg(tmp_path)
    vieux = {"access_token": "AT", "refresh_token": "RT", "id_token": "x"}
    appels = []

    def post(url, data):
        appels.append(data)
        return 200, json.dumps({"access_token": "AT2", "refresh_token": "RT2"})
    assert ph.rafraichir(c, vieux, obtenu_a=1000, post=post, maintenant=1100) is vieux
    neuf = ph.rafraichir(c, vieux, obtenu_a=1000, post=post, maintenant=2000)
    assert neuf["access_token"] == "AT2" and neuf["id_token"] == "x"
    assert appels == [{"grant_type": "refresh_token", "refresh_token": "RT",
                       "client_id": "blue-fox-os"}]
    assert ph.rafraichir(c, vieux, 1000, post=lambda u, d: (400, "{}"), maintenant=2000) is vieux


# ------------------------------------------------------------ progression (lot B, 2026-10-26)
def test_etapes_de_l_unite_relayees_a_l_ecran(tmp_path):
    import time as _t
    c = cfg(tmp_path)

    class Etapes(FauxSystemctl):
        def __call__(self, argv, **kw):
            if argv[:2] == ["systemctl", "restart"]:
                nom = argv[2].split("@", 1)[1].rsplit(".service", 1)[0]
                d = os.path.join(c["state_dir"], "progres")
                os.makedirs(d, mode=0o700, exist_ok=True)
                for n, texte in ((1, "Lecture"), (3, "Connexion à Nextcloud…")):
                    ph._ecrire(os.path.join(d, nom + ".json"), {"etape": n, "total": 5, "texte": texte})
                    _t.sleep(0.7)
            return super().__call__(argv, **kw)
    _nom, lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()], systemctl=Etapes(c))
    relayees = [l for l in lignes if "bfos:progres " in l]
    assert len(relayees) == 2, lignes
    assert '"etape": 3' in relayees[-1] and "Nextcloud" in relayees[-1]


def test_vieille_progression_effacee_avant_l_ouverture(tmp_path):
    c = cfg(tmp_path)
    d = os.path.join(c["state_dir"], "progres")
    os.makedirs(d, mode=0o700)
    ph._ecrire(os.path.join(d, f"olivier-{H6}.json"), {"etape": 5, "total": 5, "texte": "vieux"})
    _nom, lignes, _ak = jouer_auth(c, FauxSysteme(), [reponse_ok()])
    assert not [l for l in lignes if "vieux" in l]


@pytest.mark.parametrize("contenu", ['{"etape": "1", "total": 5, "texte": "x"}', "pas du json",
                                     '{"etape": 1, "total": 5}', "[]",
                                     '{"etape": 0, "total": 5, "texte": "x"}', '{"etape": 6, "total": 5, "texte": "x"}',
                                     '{"etape": 1, "total": 99, "texte": "x"}'])
def test_progression_illisible_ignoree(tmp_path, contenu):
    f = tmp_path / "p.json"
    f.write_text(contenu)
    assert ph.lire_progres(str(f)) is None


def test_progression_tronquee_et_lien_refuse(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"etape": 1, "total": 5, "texte": "x" * 500}))
    assert len(ph.lire_progres(str(f))["texte"]) == 200
    (tmp_path / "lien.json").symlink_to(f)
    assert ph.lire_progres(str(tmp_path / "lien.json")) is None


def test_exception_du_lancement_relancee(tmp_path):
    import subprocess
    c = cfg(tmp_path)

    def run(argv, **kw):
        if argv[:2] == ["systemctl", "restart"]:
            raise subprocess.TimeoutExpired(argv, 1)

        class R:
            returncode = 0
        return R()
    ok, message, _lu = ph.lancer_session(c, "olivier-abc123", SUB, "persistant", "AT", run=run,
                                         progres=lambda d: None)
    assert not ok and "trop de temps" in message


# ------------------------------------------- mot de passe a l'ecran de verrouillage
def test_registre_retient_l_identifiant_authentik(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    e = json.load(open(os.path.join(c["state_dir"], "users", nom + ".json")))
    assert e["identifiant"] == "Olivier" and e["sub"] == SUB, "tel que dans Authentik"
    assert sysm.annuaire(c).identifiant(nom) == "Olivier"


@pytest.mark.parametrize("claims, attendu", [
    ({"preferred_username": "c.dorval", "email": "x@y.z", "email_verified": True}, "c.dorval"),
    ({"email": "c@boreal.example", "email_verified": True}, None),
    ({"email": "c@boreal.example", "email_verified": False}, None),
    ({"preferred_username": "a\nb"}, None),
    ({"preferred_username": "x" * 151}, None),
    ({}, None),
])
def test_identifiant_authentik(claims, attendu):
    assert ph.identifiant_authentik(claims) == attendu


def _verrouiller(tmp_path, mot, verifier):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    dodos = []
    vus = []

    def verif(cfg_, ident, mdp):
        vus.append((ident, mdp))
        return verifier(cfg_, ident, mdp)
    r = jouer_auth(c, sysm, [reponse_ok()], service="kde", demande=nom, choix=mot,
                   verifier=verif, sleep=dodos.append)
    return nom, r, vus, dodos


def test_deverrouillage_par_mot_de_passe_sans_qr(tmp_path):
    nom, (nom2, lignes, ak), vus, _d = _verrouiller(tmp_path, "s3cret\n", lambda *a: True)
    assert nom2 == nom and lignes[0].startswith("SECRET ") and lignes[-2:] == [f"USER {nom}", "OK"]
    assert vus == [("Olivier", "s3cret")]
    assert ak.appels == [], "aucun code d'appareil demande"


def test_mot_de_passe_refuse_attend_puis_refuse(tmp_path):
    with pytest.raises(ph.Refus) as exc:
        _verrouiller(tmp_path, "faux\n", lambda *a: False)
    assert exc.value.message == "Mot de passe refusé."


def test_mot_de_passe_refuse_ralentit(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    dodos = []
    with pytest.raises(ph.Refus):
        jouer_auth(c, sysm, [], service="kde", demande=nom, choix="faux\n",
                   verifier=lambda *a: False, sleep=dodos.append)
    assert dodos == [ph.ATTENTE_REFUS]


def test_champ_vide_donne_le_qr(tmp_path):
    nom, (nom2, lignes, ak), vus, _d = _verrouiller(
        tmp_path, "\n", lambda *a: (_ for _ in ()).throw(AssertionError("pas de verification")))
    assert nom2 == nom and vus == [] and any(u == CFG_BASE["device_url"] for u, _d2 in ak.appels)


def test_compte_sans_identifiant_passe_au_qr_sans_demander(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    f = os.path.join(c["state_dir"], "users", nom + ".json")
    e = json.load(open(f))
    del e["identifiant"]
    open(f, "w").write(json.dumps(e))
    nom2, lignes, _ak = jouer_auth(c, sysm, [reponse_ok()], service="kde", demande=nom, choix="",
                                   verifier=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert nom2 == nom and not [l for l in lignes if l.startswith("SECRET")]


def test_mot_de_passe_hors_ecran_de_verrouillage_jamais_demande(tmp_path):
    """sudo, polkit… : le QR seulement, comme avant."""
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    _n, lignes, _ak = jouer_auth(c, sysm, [reponse_ok()], service="sudo", demande=nom, choix="",
                                 verifier=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert not [l for l in lignes if l.startswith("SECRET")]


class FauxFlux:
    """L'executeur de flux d'Authentik, rejoue d'apres le banc du 2026-10-27."""
    EXEC = "/api/v3/flows/executor/default-authentication-flow/"

    def __init__(self, mdp="bon", identification=None, apres="ak-stage-authenticator-validate",
                 marque=None, vers=None):
        self.mdp, self.apres, self.vers = mdp, apres, vers
        self.identification = identification or {"component": "ak-stage-identification",
                                                 "user_fields": ["email", "username"],
                                                 "password_fields": False, "captcha_stage": None}
        self.marque = marque or {"flow_authentication": "default-authentication-flow"}
        self.etat = "identification"
        self.uid = None
        self.appels = []

    def _retour(self):
        return {"component": "xak-flow-redirect", "final_redirect": False,
                "to": self.vers or self.EXEC + "?query=next%3D%2F"}

    def json(self, chemin, corps=None):
        self.appels.append((chemin, corps))
        if chemin == "/api/v3/core/brands/current/":
            return self.marque
        assert chemin.startswith(self.EXEC), chemin
        if corps is None:
            if self.etat == "identification":
                return dict(self.identification)
            if self.etat == "mot":
                return {"component": "ak-stage-password", "pending_user": self.uid}
            if self.etat == "faux":
                return {"component": "ak-stage-password", "pending_user": self.uid,
                        "response_errors": {"password": [{"string": "Invalid password"}]}}
            if self.etat == "fini":
                return self.apres if isinstance(self.apres, dict) else {"component": self.apres}
        if corps["component"] == "ak-stage-identification":
            self.uid = corps["uid_field"]
            if "password" in corps:
                self.etat = "fini" if corps["password"] == self.mdp else "identification"
                if self.etat == "identification":
                    self.identification = dict(self.identification, response_errors={"x": 1})
            else:
                self.etat = "mot"
            return self._retour()
        if corps["component"] == "ak-stage-password":
            self.etat = "fini" if corps["password"] == self.mdp else "faux"
            return self._retour()
        raise AssertionError(corps)


CFG_MDP = {"issuer": ISSUER}


def test_flux_bon_mot_de_passe_s_arrete_a_la_mfa():
    f = FauxFlux()
    assert ph.verifier_mot_de_passe(CFG_MDP, "c.dorval", "bon", navigation=f) is True
    corps = [c for _ch, c in f.appels if c]
    assert corps == [{"component": "ak-stage-identification", "uid_field": "c.dorval"},
                     {"component": "ak-stage-password", "password": "bon"}]


def test_flux_mauvais_mot_de_passe():
    assert ph.verifier_mot_de_passe(CFG_MDP, "c.dorval", "faux", navigation=FauxFlux()) is False


def test_flux_mot_de_passe_dans_l_identification():
    ident = {"component": "ak-stage-identification", "password_fields": True, "captcha_stage": None}
    assert ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=FauxFlux(identification=ident))
    assert not ph.verifier_mot_de_passe(CFG_MDP, "c", "x", navigation=FauxFlux(identification=ident))


def test_flux_sans_mfa_fini_vaut_accepte():
    f = FauxFlux(apres={"component": "xak-flow-redirect", "final_redirect": True, "to": "/"})
    assert ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=f) is True


def test_flux_acces_refuse_apres_le_mot_de_passe():
    f = FauxFlux(apres="ak-stage-access-denied")
    assert ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=f) is False


def test_flux_mfa_en_erreur_ne_vaut_pas_accepte():
    f = FauxFlux(apres={"component": "ak-stage-authenticator-validate", "response_errors": {"x": 1}})
    assert ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=f) is False


@pytest.mark.parametrize("fabrique", [
    lambda: FauxFlux(identification={"component": "ak-stage-identification",
                                     "captcha_stage": {"js_url": "x"}}),
    lambda: FauxFlux(identification={"component": "ak-stage-prompt"}),
    lambda: FauxFlux(identification={"component": "ak-stage-access-denied"}),
    lambda: FauxFlux(marque={"flow_authentication": "../admin"}),
    lambda: FauxFlux(marque={"flow_authentication": None}),
    lambda: FauxFlux(vers="/api/v3/flows/executor/autre-flux/"),
    lambda: FauxFlux(vers="https://ailleurs.example/api/v3/flows/executor/default-authentication-flow/"),
])
def test_flux_inattendu_est_indisponible_jamais_accepte(fabrique):
    with pytest.raises(ph.Refus) as exc:
        ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=fabrique())
    assert exc.value.message == ph.INDISPONIBLE


@pytest.mark.parametrize("emetteur", ["http://auth.exemple.test/application/o/x/", "",
                                      "https://u:p@auth.exemple.test/x/"])
def test_flux_emetteur_non_https_refuse(emetteur):
    with pytest.raises(ph.Refus):
        ph.verifier_mot_de_passe({"issuer": emetteur}, "c", "bon", navigation=FauxFlux())


def test_flux_reseau_coupe_est_indisponible():
    class Coupe:
        def json(self, chemin, corps=None):
            raise OSError("reseau")
    with pytest.raises(ph.Refus) as exc:
        ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=Coupe())
    assert exc.value.message == ph.INDISPONIBLE


def test_navigation_https_sur_l_hote_de_l_emetteur():
    vus = []

    class Ouvreur:
        def open(self, req, timeout):
            vus.append((req.full_url, req.get_method(), timeout))

            class R:
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def read(s, n): return b'{"flow_authentication": "x"}'
            return R()
    nav = ph.Navigation("https://auth.exemple.test", ouvreur=Ouvreur())
    assert nav.json("/api/v3/core/brands/current/") == {"flow_authentication": "x"}
    assert vus == [("https://auth.exemple.test/api/v3/core/brands/current/", "GET",
                    ph.DELAI_MOT_DE_PASSE)]


def test_navigation_reponse_trop_longue():
    class Ouvreur:
        def open(self, req, timeout):
            class R:
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def read(s, n): return b"x" * n
            return R()
    with pytest.raises(ValueError):
        ph.Navigation("https://a.test", ouvreur=Ouvreur()).json("/")


def _ouvreur_302(location):
    import email.message
    vus = []

    class Ouvreur:
        def open(self, req, timeout):
            vus.append((req.full_url, req.get_method()))
            if req.get_method() == "POST":
                h = email.message.Message()
                h["Location"] = location
                raise urllib.error.HTTPError(req.full_url, 302, "Found", h, None)

            class R:
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def read(s, n): return b'{"component": "ak-stage-password"}'
            return R()
    return Ouvreur(), vus


def test_navigation_suit_le_302_de_l_executeur_en_get():
    ouvreur, vus = _ouvreur_302("/api/v3/flows/executor/f/?query=next%3D%2F")
    nav = ph.Navigation("https://auth.exemple.test", ouvreur=ouvreur)
    assert nav.json("/api/v3/flows/executor/f/?query=x", {"a": 1}) == {"component": "ak-stage-password"}
    assert vus == [("https://auth.exemple.test/api/v3/flows/executor/f/?query=x", "POST"),
                   ("https://auth.exemple.test/api/v3/flows/executor/f/?query=next%3D%2F", "GET")]


@pytest.mark.parametrize("location", ["https://ailleurs.example/api/v3/flows/executor/f/",
                                      "http://auth.exemple.test/api/v3/flows/executor/f/",
                                      "/if/flow/default-authentication-flow/", "/api/v3/core/users/"])
def test_navigation_refuse_toute_autre_redirection(location):
    ouvreur, _vus = _ouvreur_302(location)
    with pytest.raises(ValueError):
        ph.Navigation("https://auth.exemple.test", ouvreur=ouvreur).json(
            "/api/v3/flows/executor/f/", {"a": 1})


def test_cinq_refus_puis_le_qr_seulement(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    for _n in range(ph.ECHECS_MAX):
        with pytest.raises(ph.Refus):
            jouer_auth(c, sysm, [], service="kde", demande=nom, choix="faux\n",
                       verifier=lambda *a: False)
    vus = []
    nom2, lignes, ak = jouer_auth(c, sysm, [reponse_ok()], service="kde", demande=nom,
                                  choix="bon\n", verifier=lambda *a: vus.append(a) or True)
    assert vus == [], "plus de mot de passe apres cinq refus"
    assert any("Trop d'essais" in l for l in lignes) and nom2 == nom
    # Le QR approuve par la personne elle-meme remet le compteur a zero.
    assert open(os.path.join(c["state_dir"], "echecs")).read().strip() == "0"
    nom3, _l, _ak = jouer_auth(c, sysm, [], service="kde", demande=nom, choix="bon\n",
                               verifier=lambda *a: True)
    assert nom3 == nom


def test_compteur_illisible_ferme_le_mot_de_passe(tmp_path):
    c = cfg(tmp_path)
    sysm = FauxSysteme()
    nom, _l, _ak = jouer_auth(c, sysm, [reponse_ok()])
    _n, lignes, _ak = jouer_auth(c, sysm, [reponse_ok()], service="kde", demande=nom, choix="",
                                 verifier=lambda *a: (_ for _ in ()).throw(AssertionError),
                                 compteur=str(tmp_path / "absent" / "echecs"))
    assert not [l for l in lignes if l.startswith("SECRET")]


def test_flux_autre_usager_reconnu_refuse_sans_envoyer_le_mot():
    f = FauxFlux()
    orig = f.json

    def json_(chemin, corps=None):
        d = orig(chemin, corps)
        if d.get("component") == "ak-stage-password":
            d = dict(d, pending_user="quelqu.un.d.autre")
        return d
    f.json = json_
    assert ph.verifier_mot_de_passe(CFG_MDP, "c.dorval", "bon", navigation=f) is False
    assert not [c for _ch, c in f.appels if c and "password" in c]


def test_flux_meme_usager_casse_differente_accepte():
    f = FauxFlux(apres={"component": "ak-stage-authenticator-validate", "pending_user": "C.Dorval"})
    assert ph.verifier_mot_de_passe(CFG_MDP, "c.dorval", "bon", navigation=f) is True


def test_flux_sans_mfa_qui_sort_du_flux_vaut_accepte():
    f = FauxFlux(apres={"component": "xak-flow-redirect", "final_redirect": False, "to": "/"})
    assert ph.verifier_mot_de_passe(CFG_MDP, "c", "bon", navigation=f) is True


def test_navigation_echeance_globale():
    t = [0.0]

    class Ouvreur:
        def open(self, req, timeout):
            assert timeout <= ph.DELAI_MOT_DE_PASSE
            t[0] += 20

            class R:
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def read(s, n): return b"{}"
            return R()
    nav = ph.Navigation("https://a.test", ouvreur=Ouvreur(), horloge=lambda: t[0])
    nav.json("/")
    nav.json("/")
    with pytest.raises(ValueError):
        nav.json("/")


def test_flux_sans_pending_user_a_l_etape_du_mot_refuse_sans_envoyer():
    f = FauxFlux()
    orig = f.json

    def json_(chemin, corps=None):
        d = orig(chemin, corps)
        if d.get("component") == "ak-stage-password":
            d = {k: v for k, v in d.items() if k != "pending_user"}
        return d
    f.json = json_
    assert ph.verifier_mot_de_passe(CFG_MDP, "c.dorval", "bon", navigation=f) is False
    assert not [c for _ch, c in f.appels if c and "password" in c]


@pytest.mark.parametrize("contenu", ["", "abc", "-1"])
def test_compteur_vide_ou_abime_ferme_le_mot_de_passe(tmp_path, contenu):
    f = tmp_path / "echecs"
    f.write_text(contenu)
    assert ph.lire_echecs(str(f)) is None


def test_compteur_ecrit_par_rename(tmp_path):
    f = tmp_path / "echecs"
    assert ph.ecrire_echecs(str(f), 3) and ph.lire_echecs(str(f)) == 3
    assert sorted(os.listdir(tmp_path)) == ["echecs"]
    assert not ph.ecrire_echecs(str(tmp_path / "absent" / "echecs"), 1)
