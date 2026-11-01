"""Clé générique : organisation choisie par son domaine (#26870, 2026-10-27).

Ce qui est gardé ici :
- le domaine tapé est normalisé, jamais une adresse IP, un chemin ou des identifiants ;
- la configuration de l'organisation est lue SANS redirection, bornée, avec l'en-tête attendu ;
- seule une LISTE BLANCHE passe dans la configuration de la clé : jamais les groupes locaux,
  les plages d'uid, le compte d'attente ; la 2FA ne fait que se durcir ;
- les adresses Odoo hors du domaine tapé, ou en http, refusent toute la configuration ;
- l'ouverture demande le domaine, confirme le nom, puis passe au QR ; « Changer » à l'écran
  arrête le QR et redemande le domaine ; le domaine retenu sur la clé évite la question ;
- le domaine est retenu dans l'espace des profils et relu au démarrage, en lecture seule.
"""
import io
import json
import os
import urllib.error

import pytest

from bfos_live import organisation as org
from bfos_live import pam_helper as ph

from test_pam_helper import (CFG_BASE, ISSUER, MAINTENANT, FauxAuthentik, FauxSysteme,
                             FauxSystemctl, cfg, reponse_ok)

DOMAINE = "boreal.exemple.test"
DOC = {
    "schema": "bfos-live/v1",
    "organisation": "Boréal Services-conseils",
    "domaine": DOMAINE,
    "live": dict(CFG_BASE, allowed_groups=["Employes"], require_mfa=True,
                 policy_url=f"https://{DOMAINE}/api/v1/policy/me",
                 profile_key_url=f"https://{DOMAINE}/api/v1/policy/live/profile-key",
                 login_link_url=f"https://{DOMAINE}/api/v1/policy/live/login-link",
                 langue="fr", timezone="America/Toronto"),
    "tenant": {"slug": "boreal", "name": "Boréal",
               "branding": {"palette": {"accent": "#1F5D4A"}, "wallpaper_url": "https://boreal.example/f.jpg"},
               "services": {"nextcloud": {"url": "https://nuage.boreal.example/", "oidc_client_id": "nc"},
                            "vaultwarden": {"url": "https://coffre.boreal.example"}}},
}
racine = pytest.mark.skipif(os.geteuid() != 0, reason="fichiers deposes par root")


def socle(tmp_path, **extra):
    c = cfg(tmp_path, **extra)
    for cle in list(CFG_BASE) + ["allowed_groups"]:
        c.pop(cle, None)
    return c


# ------------------------------------------------------------------ domaine
@pytest.mark.parametrize("brut, attendu", [
    ("boreal.example", "boreal.example"),
    ("  Boreal.Example/ ", "boreal.example"),
    ("https://boreal.example/", "boreal.example"),
    ("banc.exemple.test:9443", "banc.exemple.test:9443"),
    ("bôréal.example", "xn--bral-cpa8d.example"),
    ("http://boreal.example", None),
    ("https://boreal.example/chemin", None),
    ("https://moi:mdp@boreal.example", None),
    ("192.168.1.1", None),
    ("localhost", None),
    ("boreal", None),
    ("boreal.example:0", None),
    ("boreal.example:99999", None),
    ("bor eal.example", None),
    ("-boreal.example", None),
    ("boreal.example\n", "boreal.example"),
    ("boreal.example\x00x", None),
    ("boreal.example:443", "boreal.example"),
    ("https://boreal.example:443/", "boreal.example"),
    ("straße.de", None),
    ("", None),
    (None, None),
])
def test_normaliser_domaine(brut, attendu):
    assert org.normaliser_domaine(brut) == attendu


# ------------------------------------------------------------------ lecture
class Rep(io.BytesIO):
    def __init__(self, corps, entetes=None, statut=200):
        super().__init__(corps)
        self.headers = entetes if entetes is not None else {"x-bf-live-version": "1"}
        self.status = statut

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ouvreur(corps=None, entetes=None, exc=None):
    vus = []

    def ouvrir(req, timeout=None):
        vus.append(req.full_url)
        if exc:
            raise exc
        return Rep(json.dumps(DOC).encode() if corps is None else corps, entetes)
    ouvrir.vus = vus
    return ouvrir


def test_telecharger_lit_la_bonne_adresse():
    o = ouvreur()
    assert org.telecharger(DOMAINE, ouvrir=o)["organisation"] == "Boréal Services-conseils"
    assert o.vus == [f"https://{DOMAINE}/bfos-live.json"]


@pytest.mark.parametrize("kw", [
    {"entetes": {}},
    {"entetes": {"x-bf-live-version": "2"}},
    {"corps": b"x" * (org.TAILLE_MAX + 1)},
    {"corps": b"pas du json"},
    {"exc": urllib.error.HTTPError("u", 404, "x", {}, io.BytesIO(b""))},
    {"exc": urllib.error.HTTPError("u", 302, "x", {}, io.BytesIO(b""))},
    {"exc": urllib.error.URLError("hors ligne")},
])
def test_telecharger_refuse(kw):
    with pytest.raises(org.Erreur):
        org.telecharger(DOMAINE, ouvrir=ouvreur(**kw))


def test_aucune_redirection_suivie():
    assert org._OUVREUR.handlers and any(isinstance(h, org._SansRedirection) for h in org._OUVREUR.handlers)


# ------------------------------------------------------------------ validation
def test_valider_garde_la_liste_blanche():
    doc = json.loads(json.dumps(DOC))
    doc["live"].update(local_groups=["wheel"], uid_min=0, placeholder_user="root",
                       rich_services=["login"], state_dir="/tmp", shell="/bin/sh")
    o = org.valider(doc, DOMAINE)
    assert set(o["live"]) <= set(org.CLES_LIVE)
    assert o["live"]["issuer"] == ISSUER and o["organisation"] == "Boréal Services-conseils"
    t = o["tenant"]
    assert t["services"]["nextcloud"] == {"url": "https://nuage.boreal.example", "oidc_client_id": "nc"}
    assert t["services"]["vaultwarden"]["url"] == "https://coffre.boreal.example"
    assert t["branding"]["palette"]["accent"] == "#1F5D4A" and t["slug"] == "boreal"


@pytest.mark.parametrize("modif", [
    {"policy_url": "https://ailleurs.example/api/v1/policy/me"},
    {"profile_key_url": "https://boreal.example.ailleurs.example/x"},
    {"login_link_url": "http://boreal.example/x"},
    {"issuer": "http://auth.example/"},
    {"token_url": "javascript:alert(1)"},
    {"device_url": "https://moi:mdp@auth.example/device/"},
    {"allowed_groups": []},
    {"allowed_groups": "Employes"},
    {"allowed_groups": [""]},
    {"require_mfa": "non"},
    {"client_id": ""},
])
def test_valider_refuse_toute_la_configuration(modif):
    doc = json.loads(json.dumps(DOC))
    doc["live"].update(modif)
    with pytest.raises(org.Erreur):
        org.valider(doc, DOMAINE)


@pytest.mark.parametrize("modif", [{"schema": "autre"}, {"organisation": ""},
                                   {"organisation": "a\nb"}, {"live": []}])
def test_valider_refuse_le_document(modif):
    doc = dict(json.loads(json.dumps(DOC)), **modif)
    with pytest.raises(org.Erreur):
        org.valider(doc, DOMAINE)


def test_tenant_douteux_omis():
    doc = json.loads(json.dumps(DOC))
    doc["tenant"] = {"slug": "../x", "branding": {"palette": {"accent": "red"}, "wallpaper_url": "http://x"},
                     "services": {"nextcloud": {"url": "http://nuage"}, "autre": {"url": "https://x"}}}
    t = org.valider(doc, DOMAINE)["tenant"]
    assert t["slug"] == "org" and t["branding"] == {} and t["services"] == {}


def test_fusionner_mfa_ne_fait_que_se_durcir(tmp_path):
    s = socle(tmp_path, require_mfa=True)
    assert org.fusionner(s, {"require_mfa": False})["require_mfa"] is True
    s = socle(tmp_path, require_mfa=False)
    assert org.fusionner(s, {"require_mfa": True})["require_mfa"] is True
    f = org.fusionner(socle(tmp_path, local_groups=["bfos-live"]), {"local_groups": ["wheel"]})
    assert f["local_groups"] == ["bfos-live"]


# ------------------------------------------------------------------ depot
@racine
def test_depot_aller_retour(tmp_path):
    d = str(tmp_path / "org")
    o = org.valider(DOC, DOMAINE)
    org.deposer(o, d)
    assert org.deposee(d) == {"organisation": o["organisation"], "domaine": DOMAINE, "live": o["live"]}
    for f in ("live.json", "tenant.json", "organisation.json"):
        assert os.stat(os.path.join(d, f)).st_mode & 0o777 == 0o644
    os.chown(os.path.join(d, "live.json"), 1000, 1000)
    assert org.deposee(d) is None, "fichier d'un autre proprietaire ignore"


def test_depot_dans_un_lien_refuse(tmp_path):
    os.symlink(str(tmp_path), str(tmp_path / "lien"))
    with pytest.raises(org.Erreur):
        org.deposer(org.valider(DOC, DOMAINE), str(tmp_path / "lien"))


def test_drapeau_de_changement(tmp_path):
    f = tmp_path / "changer"
    assert not org.changement_demande(str(f))
    f.write_text("")
    assert not org.changement_demande(str(f), proprietaire=os.getuid() + 1)
    assert org.changement_demande(str(f), proprietaire=os.getuid())
    assert f.exists()
    assert org.changement_demande(str(f), proprietaire=os.getuid(), consommer=True)
    assert not f.exists()
    os.symlink("/etc/passwd", str(f))
    assert not org.changement_demande(str(f))


# ------------------------------------------------------------------ dialogue
def fausse_lecture(doc=DOC, erreurs=()):
    vus = []

    def telecharger(domaine):
        vus.append(domaine)
        if domaine in erreurs:
            raise org.Erreur("Ce domaine ne sert pas de clé Blue Fox OS.", "404")
        # Les adresses Odoo d'une organisation sont sur SON domaine.
        return dict(json.loads(json.dumps(doc).replace(DOMAINE, domaine)), domaine=domaine)
    telecharger.vus = vus
    return telecharger


def choisir(tmp_path, reponses, s=None, **kw):
    sortie = io.StringIO()
    canal = ph.Canal(io.StringIO("".join(r + "\n" for r in reponses)), sortie)
    s = s or socle(tmp_path)
    kw.setdefault("telecharger", fausse_lecture())
    kw.setdefault("dossier", str(tmp_path / "org"))
    kw.setdefault("retenu", str(tmp_path / "retenu"))
    kw.setdefault("drapeau", str(tmp_path / "changer"))
    o = ph.choisir_organisation(canal, s, "greetd", **kw)
    return o, sortie.getvalue().splitlines()


@racine
def test_premiere_ouverture_demande_puis_confirme(tmp_path):
    o, lignes = choisir(tmp_path, ["https://Boreal.Exemple.test/", "continuer"])
    assert o["domaine"] == DOMAINE
    assert lignes[0].startswith("ASK " + ph.PREFIXE_DOMAINE)
    assert any(ph.PREFIXE_CHOIX in l and "Boréal Services-conseils" in l for l in lignes)
    assert org.deposee(str(tmp_path / "org"))["domaine"] == DOMAINE


@racine
def test_domaine_invalide_puis_inconnu_puis_bon(tmp_path):
    lecture = fausse_lecture(erreurs={"inconnu.exemple.test"})
    o, lignes = choisir(tmp_path, ["pas un domaine", "inconnu.exemple.test", "changer-pas-ici",
                                   DOMAINE, "continuer"], telecharger=lecture)
    assert o["domaine"] == DOMAINE
    questions = [json.loads(ph.desechapper(l)[len("ASK " + ph.PREFIXE_DOMAINE):].split("\n")[0])
                 for l in lignes if l.startswith("ASK " + ph.PREFIXE_DOMAINE)]
    assert "domaine" in questions[1]["erreur"].lower() and questions[1]["valeur"] == "pas un domaine"
    assert questions[2]["erreur"] and questions[2]["valeur"] == "inconnu.exemple.test"


@racine
def test_langue_de_l_ecran_relue_avant_chaque_question(tmp_path, monkeypatch):
    """Tournage du 2026-10-28 : passee a l'anglais, la cle gardait le dialogue du
    domaine en francais. Relue avant la question et avant la confirmation."""
    vues = iter(["fr", "en", "en"])
    monkeypatch.setattr(ph, "langue_de_l_ecran", lambda cfg: next(vues, "en"))
    try:
        o, lignes = choisir(tmp_path, [DOMAINE, "continuer"])
        assert "Quel est le domaine" in ph.desechapper(lignes[0])
        choix = [ph.desechapper(l) for l in lignes if ph.PREFIXE_CHOIX in l]
        assert choix and "Is this your organization?" in choix[0]
    finally:
        ph.langue.choisir("fr")


@racine
def test_erreur_du_domaine_porte_sa_forme_source(tmp_path):
    o, lignes = choisir(tmp_path, ["pas un domaine", DOMAINE, "continuer"])
    q = json.loads(ph.desechapper(lignes[1])[len("ASK " + ph.PREFIXE_DOMAINE):].split("\n")[0])
    assert q["erreur_source"] == "Ce n'est pas un nom de domaine. Exemple : exemple.com"


@racine
def test_changer_de_domaine_a_la_confirmation(tmp_path):
    o, _l = choisir(tmp_path, [DOMAINE, "changer", "autre.exemple.test", "continuer"])
    assert o["domaine"] == "autre.exemple.test"


@racine
def test_domaine_retenu_sans_question(tmp_path):
    (tmp_path / "retenu").write_text("boreal.exemple.test\n")
    lecture = fausse_lecture()
    o, lignes = choisir(tmp_path, [], telecharger=lecture)
    assert o["domaine"] == DOMAINE and lignes == [] and lecture.vus == [DOMAINE]


@racine
def test_domaine_retenu_injoignable_demande_avec_erreur(tmp_path):
    (tmp_path / "retenu").write_text("inconnu.exemple.test\n")
    o, lignes = choisir(tmp_path, [DOMAINE, "continuer"],
                        telecharger=fausse_lecture(erreurs={"inconnu.exemple.test"}))
    assert o["domaine"] == DOMAINE
    q = json.loads(ph.desechapper(lignes[0])[len("ASK " + ph.PREFIXE_DOMAINE):].split("\n")[0])
    assert q["erreur"] and q["valeur"] == "inconnu.exemple.test"


@racine
def test_changer_a_l_ecran_ignore_le_depot_et_le_retenu(tmp_path, monkeypatch):
    monkeypatch.setattr(ph, "uid_de_l_ecran", lambda c: os.getuid())
    choisir(tmp_path, [DOMAINE, "continuer"])
    (tmp_path / "retenu").write_text("boreal.exemple.test\n")
    (tmp_path / "changer").write_text("")
    o, lignes = choisir(tmp_path, ["autre.exemple.test", "continuer"])
    assert o["domaine"] == "autre.exemple.test"
    assert not (tmp_path / "changer").exists(), "drapeau consomme"
    q = json.loads(ph.desechapper(lignes[0])[len("ASK " + ph.PREFIXE_DOMAINE):].split("\n")[0])
    assert q["valeur"] == DOMAINE, "pre-rempli avec l'organisation actuelle"


@racine
def test_deja_choisie_pendant_ce_demarrage(tmp_path):
    choisir(tmp_path, [DOMAINE, "continuer"])
    lecture = fausse_lecture()
    o, lignes = choisir(tmp_path, [], telecharger=lecture)
    assert o["domaine"] == DOMAINE and lignes == [] and lecture.vus == []


def test_sans_reponse_la_connexion_est_annulee(tmp_path):
    with pytest.raises(ph.Refus):
        choisir(tmp_path, ["x"] * 6)


# ------------------------------------------------------------------ auth de bout en bout
@racine
def test_auth_cle_generique(tmp_path, monkeypatch):
    s = socle(tmp_path)
    sysm = FauxSysteme()
    ak = FauxAuthentik([reponse_ok()])
    sortie = io.StringIO()
    canal = ph.Canal(io.StringIO(f"{DOMAINE}\ncontinuer\nephemere\n"), sortie)
    env = {"PAM_SERVICE": "greetd", "PAM_USER": s["placeholder_user"]}
    sysm_cfg = dict(s, **DOC["live"])
    nom = ph.auth(canal, s, env=env, post=ak.post, sleep=lambda x: None,
                  annuaire=sysm.annuaire(sysm_cfg), maintenant=MAINTENANT, run=FauxSystemctl(s),
                  telecharger=fausse_lecture(), dossier_org=str(tmp_path / "org"))
    lignes = sortie.getvalue().splitlines()
    qr = next(l for l in lignes if l.startswith("INFO bfos:device "))
    d = json.loads(ph.desechapper(qr)[len("INFO bfos:device "):].split("\n")[0])
    assert d["organisation"] == "Boréal Services-conseils" and d["domaine"] == DOMAINE
    assert lignes[-1] == "OK" and nom
    # Fermeture et verrouillage relisent le depot.
    c = ph.charger_config(_ecrire_socle(tmp_path, s), dossier_org=str(tmp_path / "org"))
    assert c["issuer"] == ISSUER and c["organisation"] == "Boréal Services-conseils"
    assert c["local_groups"] == s["local_groups"]


def _ecrire_socle(tmp_path, s):
    chemin = tmp_path / "live.json"
    chemin.write_text(json.dumps({k: v for k, v in s.items() if k not in ph.DEFAUTS or v != ph.DEFAUTS[k]}))
    return str(chemin)


def test_cle_generique_sans_organisation_refuse_la_fermeture(tmp_path):
    with pytest.raises(ph.Refus):
        ph.charger_config(_ecrire_socle(tmp_path, socle(tmp_path)), dossier_org=str(tmp_path / "rien"))


def test_cle_configuree_ne_demande_rien(tmp_path):
    chemin = tmp_path / "live.json"
    chemin.write_text(json.dumps(dict(CFG_BASE, allowed_groups=["Employes"])))
    c = ph.charger_config(str(chemin), dossier_org=str(tmp_path / "rien"))
    assert c["issuer"] == ISSUER and "organisation" not in c


def test_le_qr_s_arrete_quand_l_ecran_demande_changer(tmp_path, monkeypatch):
    drapeau = tmp_path / "changer"
    monkeypatch.setattr(ph, "uid_de_l_ecran", lambda c: os.getuid())
    dormir = ph._dormir_sauf_changement(socle(tmp_path), lambda s: None, drapeau=str(drapeau))
    dormir(5)
    drapeau.write_text("")
    with pytest.raises(ph.Refus):
        dormir(5)
    assert drapeau.exists(), "le prochain essai le consomme"


# ------------------------------------------------------------------ relecture adverse n° 1 (2026-10-27)
@pytest.mark.parametrize("hote, attendu", [
    ("auth.boreal.example", "boreal.example"), ("boreal.example:9443", "boreal.example"),
    ("auth.boreal.qc.ca", "boreal.qc.ca"), ("x.y.gouv.qc.ca", "y.gouv.qc.ca"),
    ("a.exemple-reseau.ts.net", "exemple-reseau.ts.net"), ("exemple.co.uk", "exemple.co.uk"),
])
def test_domaine_enregistre(hote, attendu):
    assert org.domaine_enregistre(hote) == attendu


@pytest.mark.parametrize("modif", [
    {"token_url": "https://jetons.ailleurs.example/application/o/token/"},
    {"device_url": "https://autre.exemple.test/application/o/device/"},
    {"revoke_url": "https://autre.exemple.test/application/o/revoke/"},
])
def test_points_authentik_sur_l_hote_de_l_emetteur(modif):
    """Sans signature verifiee, un point de jeton tiers forgerait des revendications."""
    doc = json.loads(json.dumps(DOC))
    doc["live"].update(modif)
    with pytest.raises(org.Erreur):
        org.valider(doc, DOMAINE)


def test_domaine_sosie_avec_le_vrai_emetteur_refuse():
    """« bluefoxconsulant.com » qui sert le vrai Authentik et SES adresses Odoo."""
    doc = json.loads(json.dumps(DOC).replace(DOMAINE, "sosie.example"))
    with pytest.raises(org.Erreur):
        org.valider(doc, "sosie.example")


def test_emetteur_rendu_pour_la_confirmation():
    assert org.valider(DOC, DOMAINE)["emetteur"] == "auth.exemple.test"


def test_adresse_mal_formee_ne_casse_rien():
    doc = json.loads(json.dumps(DOC))
    doc["tenant"]["branding"]["wallpaper_url"] = "https://[x"
    doc["tenant"]["services"]["nextcloud"]["url"] = "https://[x"
    t = org.valider(doc, DOMAINE)["tenant"]
    assert "wallpaper_url" not in t["branding"] and "nextcloud" not in t["services"]
    doc["live"]["issuer"] = "https://[x"
    with pytest.raises(org.Erreur):
        org.valider(doc, DOMAINE)


def test_document_piege_devient_une_erreur():
    with pytest.raises(org.Erreur):
        org.valider({"schema": "bfos-live/v1", "organisation": "X", "live": {"issuer": 3}}, DOMAINE)
    import http.client
    with pytest.raises(org.Erreur):
        org.telecharger(DOMAINE, ouvrir=ouvreur(exc=http.client.IncompleteRead(b"")))
    with pytest.raises(org.Erreur):
        org.telecharger(DOMAINE, ouvrir=ouvreur(corps=b"[" * 100000 + b"]" * 100000))


def test_lecture_bornee_dans_le_temps():
    """Un serveur qui goutte (en-tetes compris) ne retient pas l'ecran."""
    import time as _t

    def lent(req, timeout=None):
        _t.sleep(5)
        return Rep(json.dumps(DOC).encode())
    debut = _t.monotonic()
    with pytest.raises(org.Erreur):
        org.telecharger(DOMAINE, ouvrir=lent, delai_total=0.3)
    assert _t.monotonic() - debut < 2


@pytest.mark.parametrize("brut, attendu", [
    ("école.ca", "xn--cole-9oa.ca"), ("xn--cole-9oa.ca", "xn--cole-9oa.ca"),
    ("faß.de", None), ("xn--fa-hia.de", None),
])
def test_domaine_accentue_retenu_sous_sa_forme_ascii(brut, attendu):
    assert org.normaliser_domaine(brut) == attendu


def test_journal_avec_identite_generique(monkeypatch):
    vus = []
    monkeypatch.setattr(ph.syslog, "syslog", lambda prio, msg: vus.append(msg) if "\0" not in msg
                        else (_ for _ in ()).throw(ValueError("embedded null character")))
    ph._journal("refus pour sub=https://x\0s1")
    assert vus == ["refus pour sub=https://x s1"]


@racine
def test_panne_inattendue_du_domaine_retenu_redemande_le_domaine(tmp_path):
    (tmp_path / "retenu").write_text("boreal.exemple.test\n")
    appels = []

    def lecture(domaine):
        appels.append(domaine)
        if len(appels) == 1:
            raise ValueError("panne inattendue")
        return fausse_lecture()(domaine)
    o, lignes = choisir(tmp_path, [DOMAINE, "continuer"], telecharger=lecture)
    assert o["domaine"] == DOMAINE and lignes[0].startswith("ASK " + ph.PREFIXE_DOMAINE)


def test_identite_liee_a_l_emetteur(tmp_path):
    claims = {"iss": ISSUER, "sub": "s1"}
    assert ph.identite(claims, {}) == "s1"
    assert ph.identite(claims, {"organisation": "X"}) == f"{ISSUER}\0s1"
    autre = dict(claims, iss="https://idp.malveillant.example/")
    assert ph.identite(autre, {"organisation": "Y"}) != ph.identite(claims, {"organisation": "X"})
    assert ph.nom_de_base(dict(claims, preferred_username="alice"), ph.identite(claims, {"organisation": "X"})) \
        != ph.nom_de_base(dict(autre, preferred_username="alice"), ph.identite(autre, {"organisation": "Y"}))


def test_un_autre_emetteur_ne_reprend_pas_le_compte(tmp_path):
    """Scenario de la relecture : meme `sub`, meme nom affiche, autre emetteur."""
    sysm = FauxSysteme()
    c1 = dict(cfg(tmp_path), organisation="X")
    a = sysm.annuaire(c1)
    claims = {"iss": ISSUER, "sub": "sub-alice", "preferred_username": "alice"}
    nom, uid, a_creer = a.choisir(claims)
    a.creer(nom, uid, claims)
    c2 = dict(c1, organisation="M", issuer="https://idp.malveillant.example/")
    nom2, _uid2, a_creer2 = sysm.annuaire(c2).choisir(dict(claims, iss="https://idp.malveillant.example/"))
    assert a_creer2 and nom2 != nom
    assert sysm.annuaire(c2).compte_de(nom, ph.identite(dict(claims, iss="https://idp.malveillant.example/"), c2)) is None
