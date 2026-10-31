"""Essais de l'ecran de connexion de la cle live (#26870)."""
import io
import json
import os
import socket
import struct
import tarfile
import threading

import pytest

from bfos_live import greetd_ipc, greeter_flow, reseau

QR = {"uri": "https://auth.exemple.test/device",
      "uri_complete": "https://auth.exemple.test/device?code=222353898",
      "user_code": "222353898", "expires_in": 600}
MSG_QR = "bfos:device " + json.dumps(QR) + "\nOuvre … sur ton téléphone"


# ------------------------------------------------------------------ IPC
def test_trame_aller_retour():
    a, b = socket.socketpair()
    greetd_ipc.envoyer(a, {"type": "success", "é": "✓"})
    assert greetd_ipc.recevoir(b) == {"type": "success", "é": "✓"}
    # longueur en ordre natif, sur 4 octets
    greetd_ipc.envoyer(a, {"type": "x"})
    entete = b.recv(4)
    assert struct.unpack("=I", entete)[0] == len(json.dumps({"type": "x"}).encode())


def test_trame_trop_longue_refusee():
    a, b = socket.socketpair()
    a.sendall(struct.pack("=I", greetd_ipc.TAILLE_MAX + 1))
    with pytest.raises(greetd_ipc.ErreurIPC):
        greetd_ipc.recevoir(b)


@pytest.mark.parametrize("corps", [b"pas du json", b"[1, 2]", b'{"sans": "type"}'])
def test_trame_illisible_refusee(corps):
    a, b = socket.socketpair()
    a.sendall(struct.pack("=I", len(corps)) + corps)
    with pytest.raises(greetd_ipc.ErreurIPC):
        greetd_ipc.recevoir(b)


def test_connexion_fermee():
    a, b = socket.socketpair()
    a.close()
    with pytest.raises(greetd_ipc.ErreurIPC):
        greetd_ipc.recevoir(b)


def test_client_sans_greetd_sock(monkeypatch):
    monkeypatch.delenv("GREETD_SOCK", raising=False)
    with pytest.raises(greetd_ipc.ErreurIPC):
        greetd_ipc.Client()


class FauxGreetd(threading.Thread):
    """Un greetd minimal sur une vraie socket : rejoue un scenario."""

    def __init__(self, scenario):
        super().__init__(daemon=True)
        self.a, self.b = socket.socketpair()
        self.scenario = list(scenario)
        self.recus = []

    def run(self):
        for reponse in self.scenario:
            try:
                self.recus.append(greetd_ipc.recevoir(self.a))
            except greetd_ipc.ErreurIPC:
                return
            greetd_ipc.envoyer(self.a, reponse)


def test_client_parle_le_protocole_de_greetd():
    g = FauxGreetd([
        {"type": "auth_message", "auth_message_type": "info", "auth_message": MSG_QR},
        {"type": "success"},
        {"type": "success"},
    ])
    g.start()
    c = greetd_ipc.Client(sock=g.b)
    r = Rappels()
    assert greeter_flow.tentative(c, r, "bfos-connexion") is True
    g.join(2)
    assert g.recus == [
        {"type": "create_session", "username": "bfos-connexion"},
        {"type": "post_auth_message_response", "response": None},
        {"type": "start_session", "cmd": greeter_flow.SESSION_PLASMA, "env": greeter_flow.ENV_PLASMA},
    ]
    assert r.evenements[0][0] == "qr" and r.evenements[-1] == ("fin",)


# ----------------------------------------------------------------- flux
class Rappels(greeter_flow.Rappels):
    def __init__(self, choix=None):
        self.evenements = []
        self.choix = choix

    def sur_choix(self, donnees, humain):
        self.evenements.append(("choix", [o["id"] for o in donnees["options"]]))
        return self.choix

    def sur_qr(self, donnees, humain):
        self.evenements.append(("qr", donnees["user_code"], humain))

    def sur_info(self, texte):
        self.evenements.append(("info", texte))

    def sur_progres(self, donnees):
        self.evenements.append(("progres", donnees["etape"], donnees["texte"]))

    def sur_erreur(self, texte):
        self.evenements.append(("erreur", texte))

    def sur_fin(self):
        self.evenements.append(("fin",))


class FauxClient:
    def __init__(self, reponses, demarrage=None):
        self.reponses = list(reponses)
        self.demarrage = demarrage or {"type": "success"}
        self.appels = []

    def creer_session(self, u):
        self.appels.append(("creer", u))
        return self.reponses.pop(0)

    def repondre(self, r=None):
        self.appels.append(("repondre", r))
        return self.reponses.pop(0)

    def demarrer(self, cmd, env=None):
        self.appels.append(("demarrer", list(cmd)))
        return self.demarrage

    def annuler(self):
        self.appels.append(("annuler",))
        return {"type": "success"}


def info(t):
    return {"type": "auth_message", "auth_message_type": "info", "auth_message": t}


def erreur_pam(t):
    return {"type": "auth_message", "auth_message_type": "error", "auth_message": t}


def test_flux_qr_puis_succes():
    c, r = FauxClient([info(MSG_QR), {"type": "success"}]), Rappels()
    assert greeter_flow.tentative(c, r) is True
    assert r.evenements[0][:2] == ("qr", "222353898")
    assert ("demarrer", greeter_flow.SESSION_PLASMA) in c.appels
    assert ("annuler",) not in c.appels


def test_flux_code_expire_annule_et_rend_false():
    c = FauxClient([info(MSG_QR), erreur_pam("Le code a expiré. Un nouveau code arrive."),
                    {"type": "error", "error_type": "auth_error", "description": "auth failed"}])
    r = Rappels()
    assert greeter_flow.tentative(c, r) is False
    assert ("erreur", "Le code a expiré. Un nouveau code arrive.") in r.evenements
    assert c.appels[-1] == ("annuler",)
    # L'echec PAM brut (« auth failed ») n'est pas affiche par-dessus le message clair.
    assert ("erreur", "auth failed") not in r.evenements
    assert not any(a[0] == "demarrer" for a in c.appels)


def test_flux_erreur_greetd_affichee():
    c, r = FauxClient([{"type": "error", "error_type": "error", "description": "a session is already being configured"}]), Rappels()
    assert greeter_flow.tentative(c, r) is False
    assert r.evenements == [("erreur", "a session is already being configured")]


@pytest.mark.parametrize("style", ["visible", "secret"])
def test_flux_question_inattendue_annule(style):
    c = FauxClient([{"type": "auth_message", "auth_message_type": style, "auth_message": "Password:"}])
    r = Rappels()
    assert greeter_flow.tentative(c, r) is False
    assert c.appels[-1] == ("annuler",)
    assert not any(a[0] == "repondre" and a[1] for a in c.appels)


def test_flux_info_ordinaire():
    c, r = FauxClient([info("Bonjour"), {"type": "success"}]), Rappels()
    assert greeter_flow.tentative(c, r)
    assert ("info", "Bonjour") in r.evenements


def test_flux_demarrage_refuse():
    c = FauxClient([{"type": "success"}], demarrage={"type": "error", "description": "exec failed"})
    r = Rappels()
    assert greeter_flow.tentative(c, r) is False
    assert ("erreur", "exec failed") in r.evenements and c.appels[-1] == ("annuler",)


CHOIX = {"titre": "Comment veux-tu ouvrir ta session ?", "erreur": "",
         "options": [{"id": "ephemere", "titre": "Session éphémère", "texte": "…"},
                     {"id": "persistant", "titre": "Garder mon bureau", "texte": "…"}]}
MSG_CHOIX = "bfos:choix " + json.dumps(CHOIX, ensure_ascii=False) + "\n1) … Réponds 1 ou 2."


def question(t, style="visible"):
    return {"type": "auth_message", "auth_message_type": style, "auth_message": t}


def test_flux_choix_repond_l_option_cliquee():
    c = FauxClient([info(MSG_QR), question(MSG_CHOIX), info("Ouverture de ton profil chiffré…"),
                    {"type": "success"}])
    r = Rappels(choix="persistant")
    assert greeter_flow.tentative(c, r) is True
    assert ("repondre", "persistant") in c.appels
    assert ("choix", ["ephemere", "persistant"]) in r.evenements


@pytest.mark.parametrize("choix", [None, "autre-chose"])
def test_flux_choix_sans_reponse_valide_annule(choix):
    c = FauxClient([question(MSG_CHOIX)])
    assert greeter_flow.tentative(c, Rappels(choix=choix)) is False
    assert c.appels[-1] == ("annuler",)
    assert not any(a[0] == "repondre" and a[1] for a in c.appels)


def test_flux_choix_en_question_secrete_refuse():
    # Le choix n'arrive jamais masque : un SECRET qui s'en reclame est suspect.
    c = FauxClient([question(MSG_CHOIX, style="secret")])
    assert greeter_flow.tentative(c, Rappels(choix="ephemere")) is False


@pytest.mark.parametrize("texte", [
    "bfos:choix pas-du-json",
    "bfos:choix " + json.dumps({"options": []}),
    "bfos:choix " + json.dumps({"options": [{"titre": "sans id"}]}),
    "bfos:choix " + json.dumps([1]),
])
def test_ligne_choix_invalide_ignoree(texte):
    assert greeter_flow.lire_message_choix(texte) is None


@pytest.mark.parametrize("texte", [
    "bfos:device pas-du-json\nx",
    "bfos:device " + json.dumps({"uri_complete": "http://pas-https/"}),
    "bfos:device " + json.dumps({"autre": 1}),
    "bfos:device " + json.dumps([1]),
    "Bonjour",
])
def test_ligne_qr_invalide_ignoree(texte):
    assert greeter_flow.lire_message_qr(texte) is None


def test_ligne_qr_valide():
    d, humain = greeter_flow.lire_message_qr(MSG_QR)
    assert d["user_code"] == "222353898" and humain.startswith("Ouvre")


# ----------------------------------------------------------------- reseau
class FauxRun:
    def __init__(self, sorties):
        self.sorties = sorties
        self.appels = []

    def __call__(self, argv, **kw):
        self.appels.append((argv, kw))
        code, out, err = self.sorties.pop(0)

        class R:
            pass
        r = R()
        r.returncode, r.stdout, r.stderr = code, out, err
        return r


@pytest.mark.parametrize("out, attendu", [("full\n", "full"), ("portal\n", "portal"),
                                          ("", "unknown"), ("bizarre\n", "unknown")])
def test_connectivite(out, attendu):
    assert reseau.connectivite(run=FauxRun([(0, out, "")])) == attendu


def test_champs_nmcli_echappes():
    assert reseau._champs_terse(r"*:Café\: le 2e:WPA2:80") == ["*", "Café: le 2e", "WPA2", "80"]
    assert reseau._champs_terse(r" :a\\b:--:5") == [" ", "a\\b", "--", "5"]


def test_reseaux_wifi_dedoublonnes_et_tries():
    out = "\n".join([
        " :Voisin:WPA2:40",
        "*:Bureau:WPA2:55",
        " :Bureau:WPA2:70",
        " :Ouvert:--:90",
        " ::WPA2:99",          # SSID cache : ignore
    ])
    lst = reseau.reseaux_wifi(run=FauxRun([(0, out, "")]))
    assert [r[0] for r in lst] == ["Bureau", "Ouvert", "Voisin"]
    bureau = lst[0]
    assert bureau == ("Bureau", True, 70, True)
    assert lst[1][1] is False


def test_connecter_wifi_mot_de_passe_refuse():
    run = FauxRun([(4, "", "Error: Connection activation failed: Secrets were required, but not provided.")])
    ok, msg = reseau.connecter_wifi("Bureau", "faux", run=run)
    assert not ok and "Mot de passe" in msg
    argv = run.appels[0][0]
    assert argv[-2:] == ["password", "faux"] and argv[0] == reseau.NMCLI


def test_connecter_wifi_ouvert_sans_mot_de_passe():
    run = FauxRun([(0, "", "")])
    ok, _msg = reseau.connecter_wifi("Ouvert", None, run=run)
    assert ok and "password" not in run.appels[0][0]


# ----------------------------------------------------------------- etiquettes
def test_etiqueter_ajoute_la_cle_selinux(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "etiqueter", os.path.join(os.path.dirname(__file__), "..", "image", "etiqueter.py"))
    try:
        et = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(et)
    except ImportError:
        pytest.skip("python3-libselinux absent")
    assert et.chemin_absolu("./usr/bin/x") == "/usr/bin/x"
    assert et.chemin_absolu("etc/") == "/etc"
    assert et.chemin_absolu("./") == "/"
    assert et.chemin_absolu(".") == "/"
    vus = []

    def rechercher(chemin, mode):
        vus.append((chemin, mode))
        return 0, "system_u:object_r:bin_t:s0"
    info = tarfile.TarInfo("usr/bin/x")
    info.mode = 0o755
    assert et.etiquette("/usr/bin/x", info, rechercher) == "system_u:object_r:bin_t:s0"
    assert vus[0][1] & 0o170000 == 0o100000
    d = tarfile.TarInfo("etc")
    d.type = tarfile.DIRTYPE
    assert et.etiquette("/etc", d, lambda c, m: (0, "<<none>>")) is None
    assert et.etiquette("/etc", d, lambda c, m: (-1, None)) is None


# ----------------------------------------------------------------- interface
qt = pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class FauxReseau:
    def __init__(self, liste=(), resultat=(True, "Connecté à Bureau.")):
        self.liste = list(liste)
        self.resultat = resultat
        self.connexions = []

    def reseaux_wifi(self):
        return self.liste

    def connecter_wifi(self, ssid, mdp):
        self.connexions.append((ssid, mdp))
        return self.resultat


def test_code_lisible_et_rebours():
    from bfos_live import greeter as g
    assert g.code_lisible("222353898") == "222 353 898"
    assert g.code_lisible("ABCD-EFGH") == "ABCD-EFGH"
    assert g.rebours(599.7) == "9:59" and g.rebours(-3) == "0:00"


def test_ecran_dessine_le_qr(app):
    from bfos_live import greeter as g
    temps = [1000.0]
    e = g.Ecran(reseau_mod=FauxReseau(), horloge=lambda: temps[0])
    e.montrer_qr(QR, "")
    assert e.qr.matrice and len(e.qr.matrice) >= 21
    assert e.code.text() == "222 353 898"
    assert "auth.exemple.test/device" in e.aide.text() and "https://" not in e.aide.text()
    assert e.etat.text() == "Code valide encore 10:00"
    temps[0] += 601
    e.battre()
    assert "Nouveau code" in e.etat.text()
    # Le QR se rend vraiment (pas d'exception au dessin).
    e.qr.resize(300, 300)
    e.qr.grab()


def test_ecran_sans_reseau_ouvre_le_wifi(app):
    from bfos_live import greeter as g
    r = FauxReseau([("Bureau", True, 70, False), ("Ouvert", False, 50, False)])
    e = g.Ecran(reseau_mod=r)
    e.montrer_qr(QR, "")
    e.montrer_attente_reseau("none")
    assert e.qr.matrice is None and e.code.text() == ""
    assert not e.panneau.isHidden() and e.liste.count() == 2


def test_wifi_securise_exige_un_mot_de_passe(app):
    from bfos_live import greeter as g
    r = FauxReseau([("Bureau", True, 70, False)])
    e = g.Ecran(reseau_mod=r)
    e.basculer_wifi()
    e.liste.setCurrentRow(0)
    e.connecter_wifi()
    assert r.connexions == [] and "mot de passe" in e.message_wifi.text()
    e.mdp.setText("secret")
    e.connecter_wifi()
    assert r.connexions == [("Bureau", "secret")]
    assert e.mdp.text() == "" and e.panneau.isHidden()


def test_travail_attend_le_reseau_puis_tente(app):
    from bfos_live import greeter as g
    s = g.Signaux()
    attentes, qrs = [], []
    s.attente_reseau.connect(attentes.append)
    s.qr.connect(lambda d, h: qrs.append(d["user_code"]))
    etats = iter(["none", "none", "full"])
    client = FauxClient([info(MSG_QR), {"type": "success"}])
    t = g.Travail(s, "bfos-connexion", client_usine=lambda: client,
                  connectivite=lambda: next(etats), dormir=lambda s: None)
    t.run()  # dans ce fil : les signaux arrivent directement
    assert attentes == ["none", "none"] and qrs == ["222353898"]
    assert ("creer", "bfos-connexion") in client.appels


def test_travail_reprend_apres_une_panne_de_greetd(app):
    from bfos_live import greeter as g
    s = g.Signaux()
    erreurs = []
    s.erreur.connect(erreurs.append)
    usines = iter([greetd_ipc.ErreurIPC("ferme"), None])
    client = FauxClient([{"type": "success"}])

    def usine():
        x = next(usines)
        if isinstance(x, Exception):
            raise x
        return client
    t = g.Travail(s, "bfos-connexion", client_usine=usine, connectivite=lambda: "full",
                  dormir=lambda s: None)
    t.run()
    assert any("greetd" in e for e in erreurs)
    assert ("demarrer", greeter_flow.SESSION_PLASMA) in client.appels


def test_etiqueter_suit_les_liens_physiques_d_ostree():
    """L'objet ostree passe en premier : il doit recevoir l'etiquette du chemin
    /usr qui le designe, sinon /usr/bin/bash herite de default_t."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "etiqueter", os.path.join(os.path.dirname(__file__), "..", "image", "etiqueter.py"))
    try:
        et = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(et)
    except ImportError:
        pytest.skip("python3-libselinux absent")
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode="w", format=tarfile.PAX_FORMAT) as t:
        obj = tarfile.TarInfo("sysroot/ostree/repo/objects/7c/abc.file")
        obj.size = 4
        obj.mode = 0o755
        t.addfile(obj, io.BytesIO(b"bash"))
        lien = tarfile.TarInfo("usr/bin/bash")
        lien.type = tarfile.LNKTYPE
        lien.linkname = obj.name
        t.addfile(lien)
    tampon.seek(0)
    liens = et.releve_liens(tarfile.open(fileobj=tampon, mode="r|"))
    assert liens == {"sysroot/ostree/repo/objects/7c/abc.file": "usr/bin/bash"}

    def rechercher(chemin, mode):
        return 0, ("system_u:object_r:shell_exec_t:s0" if chemin == "/usr/bin/bash"
                   else "system_u:object_r:default_t:s0")
    tampon.seek(0)
    sortie = io.BytesIO()
    with tarfile.open(fileobj=sortie, mode="w|", format=tarfile.PAX_FORMAT) as s:
        et.etiqueter_flux(tarfile.open(fileobj=tampon, mode="r|"), s, rechercher, liens)
    sortie.seek(0)
    vus = {m.name: m.pax_headers.get(et.CLE) for m in tarfile.open(fileobj=sortie, mode="r:")}
    assert vus["sysroot/ostree/repo/objects/7c/abc.file"] == "system_u:object_r:shell_exec_t:s0"
    assert vus["usr/bin/bash"] == "system_u:object_r:shell_exec_t:s0"


def test_ecran_choix_affiche_puis_repond(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau())
    e.montrer_qr(QR, "")
    e.montrer_choix(dict(CHOIX, erreur="Ton profil persistant a été révoqué."), "")
    assert not e.zone_choix.isHidden() and e.code.text() == "" and e.qr.matrice is None
    assert "révoqué" in e.erreur.text()
    boutons = [e.lignes_choix.itemAt(i).widget() for i in range(e.lignes_choix.count())]
    assert [b.titre.text() for b in boutons] == ["Session éphémère", "Garder mon bureau"]
    assert [b.accessibleName() for b in boutons] == ["Session éphémère", "Garder mon bureau"]
    boutons[1].click()
    assert e.reponses.get_nowait() == "persistant"
    assert e.zone_choix.isHidden()


def test_rappels_qt_attendent_le_clic(app):
    import queue
    from bfos_live import greeter as g
    s = g.Signaux()
    rep = queue.Queue()
    recus = []
    s.choix.connect(lambda d, h: (recus.append(d), rep.put("ephemere")))
    assert g.RappelsQt(s, rep, delai=1).sur_choix(CHOIX, "") == "ephemere"
    assert recus == [CHOIX]


def test_rappels_qt_sans_clic_annulent(app):
    import queue
    from bfos_live import greeter as g
    assert g.RappelsQt(g.Signaux(), queue.Queue(), delai=0.05).sur_choix(CHOIX, "") is None


# ------------------------------------------------------------ lot B (essai du 2026-10-26)
def progres(etape, texte="Connexion à Odoo…", total=5):
    return info(greeter_flow.PREFIXE_PROGRES + json.dumps({"etape": etape, "total": total, "texte": texte}))


def test_flux_progression_puis_succes():
    c, r = FauxClient([info(MSG_QR), progres(1), progres(4), {"type": "success"}]), Rappels()
    assert greeter_flow.tentative(c, r) is True
    assert [e for e in r.evenements if e[0] == "progres"] == [("progres", 1, "Connexion à Odoo…"),
                                                             ("progres", 4, "Connexion à Odoo…")]


@pytest.mark.parametrize("ligne", ['{"etape": 0, "total": 5, "texte": "x"}', '{"etape": 6, "total": 5, "texte": "x"}',
                                   '{"etape": 1, "total": 99, "texte": "x"}', '{"etape": 1, "total": 5}',
                                   "pas du json", "[1]"])
def test_ligne_progression_invalide_devient_un_texte(ligne):
    assert greeter_flow.lire_message_progres(greeter_flow.PREFIXE_PROGRES + ligne) is None
    c, r = FauxClient([info(greeter_flow.PREFIXE_PROGRES + ligne), {"type": "success"}]), Rappels()
    greeter_flow.tentative(c, r)
    assert r.evenements[0][0] == "info"


def test_pas_de_carre_blanc_hors_du_qr(app):
    """Le carre blanc restait a l'ecran pendant toute l'ouverture."""
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau())
    e.show()
    assert e.qr.isHidden() and not e.barre.isHidden(), "attente : barre, pas de carre"
    e.montrer_qr(QR, "")
    assert not e.qr.isHidden() and e.barre.isHidden()
    e.montrer_choix(CHOIX, "")
    assert e.qr.isHidden() and e.barre.isHidden()
    e.repondre_choix("persistant")
    assert e.qr.isHidden() and not e.barre.isHidden() and e.barre.maximum() == 0
    e.montrer_info("Ouverture de ton profil chiffré…")
    assert e.qr.isHidden() and not e.barre.isHidden()
    e.montrer_progres({"etape": 3, "total": 5, "texte": "Connexion à Nextcloud…"})
    assert e.barre.maximum() == 5 and e.barre.value() == 3
    assert "Connexion à Nextcloud" in e.etat.text() and "3 sur 5" in e.etat.text()
    e.montrer_info("Premier bureau gardé dans ton Nextcloud.")
    assert e.barre.maximum() == 5, "une info ne remet pas la barre a zero"
    e.close()


def test_textes_longs_reviennent_a_la_ligne(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau())
    e.resize(1280, 800)
    e.show()
    long = "Ton bureau Nextcloud est introuvable pour le moment. Réessaie plus tard, ou choisis une session éphémère."
    e.montrer_info(long)
    assert e.etat.wordWrap() and e.code.wordWrap()
    e.montrer_choix({"options": [{"id": "a", "titre": "Garder mon bureau dans Nextcloud",
                                  "texte": long + " " + long}]}, "")
    app.processEvents()
    b = e.lignes_choix.itemAt(0).widget()
    assert b.texte.wordWrap() and b.hasHeightForWidth()
    assert b.heightForWidth(400) > b.heightForWidth(1200), "plus etroit = plus haut, pas de debordement"
    assert b.width() <= e.carte.width()
    e.close()


@pytest.mark.parametrize("px,mm,attendu", [((1920, 1080), 0, 1.0), ((1920, 1080), 600, 1.0),
                                             ((1920, 1080), 310, 1.25), ((2736, 1824), 260, 2.0),
                                             ((3840, 2160), 597, 1.5), ((1366, 768), 0, 1.0),
                                             ((2560, 1600), 0, 1.5)])
def test_echelle(px, mm, attendu):
    from bfos_live import greeter as g
    assert g.echelle(px[0], px[1], mm) == attendu


def test_fuseau_de_la_cle(tmp_path):
    from bfos_live import greeter as g
    zi = tmp_path / "zi"
    (zi / "America").mkdir(parents=True)
    (zi / "America" / "Toronto").write_text("x")
    env = {}
    assert g.regler_fuseau({"timezone": "America/Toronto"}, environ=env, zoneinfo=str(zi))
    assert env["TZ"] == "America/Toronto"
    for mauvais in ("", "../../etc/passwd", "America/Nulle_part", "America/../America/Toronto"):
        env = {}
        assert not g.regler_fuseau({"timezone": mauvais}, environ=env, zoneinfo=str(zi))
        assert "TZ" not in env


def test_horloge_cachee_sans_fuseau(app):
    from bfos_live import greeter as g
    assert g.Ecran({"horloge": False}, reseau_mod=FauxReseau()).heure.isHidden()
    e = g.Ecran({}, reseau_mod=FauxReseau())
    e.show()
    assert not e.heure.isHidden()
    e.close()



class _FauxEcranQt:
    def __init__(self, w, h, mm):
        from PyQt6.QtCore import QSize, QSizeF
        self._s, self._mm = QSize(w, h), QSizeF(mm, mm * h / w if w else 0)

    def size(self):
        return self._s

    def devicePixelRatio(self):  # noqa: N802
        return 1

    def physicalSize(self):  # noqa: N802
        return self._mm


def _jouer_main(monkeypatch, ecran, env):
    from bfos_live import greeter as g
    relances = []

    class Arret(BaseException):  # un vrai execv ne revient jamais
        pass

    class App:
        def __init__(self, argv):
            pass

        def primaryScreen(self):  # noqa: N802
            if isinstance(ecran, Exception):
                raise ecran
            return ecran

    def ecran_cree(*a, **k):
        raise Arret()
    monkeypatch.setattr(g, "QApplication", App)
    monkeypatch.setattr(g, "Ecran", ecran_cree)
    monkeypatch.setattr(g, "lire_json", lambda *a, **k: {})
    monkeypatch.setattr(g.os, "environ", env)
    monkeypatch.setattr(g.os, "execv", lambda exe, argv: relances.append(argv) or (_ for _ in ()).throw(Arret()))
    with pytest.raises(Arret):
        g.main()
    return relances


def test_main_se_relance_une_fois_a_l_echelle(monkeypatch):
    env = {}
    relances = _jouer_main(monkeypatch, _FauxEcranQt(2736, 1824, 260), env)
    assert env["QT_SCALE_FACTOR"] == "2" and len(relances) == 1 and relances[0][1] == "-I"
    # Seconde passe (variable posee) : pas de nouvelle relance.
    assert _jouer_main(monkeypatch, _FauxEcranQt(2736, 1824, 260), env) == []


def test_main_sans_relance_a_l_echelle_1(monkeypatch):
    env = {}
    assert _jouer_main(monkeypatch, _FauxEcranQt(1920, 1080, 0), env) == []
    assert "QT_SCALE_FACTOR" not in env


def test_main_ecran_introuvable_reste_a_1(monkeypatch):
    env = {}
    assert _jouer_main(monkeypatch, None, env) == []
    assert "QT_SCALE_FACTOR" not in env


# ------------------------------------------------------------------ cle generique (2026-10-27)
DOMAINE_Q = {"titre": "Quel est le domaine de ton organisation ?", "texte": "Celui de son site",
             "erreur": "", "valeur": "boreal.example"}


def question_visible(texte):
    return {"type": "auth_message", "auth_message_type": "visible", "auth_message": texte}


class RappelsDomaine(Rappels):
    def __init__(self, domaine=None):
        super().__init__()
        self.domaine = domaine

    def sur_domaine(self, donnees, humain):
        self.evenements.append(("domaine", donnees["valeur"]))
        return self.domaine


def test_flux_domaine_repond_le_texte_tape():
    msg = greeter_flow.PREFIXE_DOMAINE + json.dumps(DOMAINE_Q) + "\nQuel est…"
    c = FauxClient([question_visible(msg), info(MSG_QR), {"type": "success"}])
    r = RappelsDomaine("  boreal.example ")
    assert greeter_flow.tentative(c, r) is True
    assert ("repondre", "boreal.example") in c.appels
    assert r.evenements[0] == ("domaine", "boreal.example")


@pytest.mark.parametrize("reponse", [None, "", "   "])
def test_flux_domaine_sans_reponse_annule(reponse):
    msg = greeter_flow.PREFIXE_DOMAINE + json.dumps(DOMAINE_Q)
    c = FauxClient([question_visible(msg)])
    assert greeter_flow.tentative(c, RappelsDomaine(reponse)) is False
    assert ("annuler",) in c.appels


@pytest.mark.parametrize("texte", ["bfos:domaine pas-du-json", "bfos:domaine []",
                                   'bfos:domaine {"titre": 3}', 'bfos:domaine {"titre": "t", "valeur": 1}'])
def test_ligne_domaine_invalide_ignoree(texte):
    assert greeter_flow.lire_message_domaine(texte) is None


def test_ecran_domaine_puis_qr_avec_organisation(app, monkeypatch):
    from bfos_live import greeter as g
    deposes = []
    monkeypatch.setattr(g, "deposer_changement", lambda: deposes.append(1) or True)
    e = g.Ecran(reseau_mod=FauxReseau())
    e.montrer_domaine(dict(DOMAINE_Q, erreur="Ce domaine ne sert pas de clé Blue Fox OS."), "")
    assert not e.zone_domaine.isHidden() and e.champ_domaine.text() == "boreal.example"
    assert "ne sert pas" in e.erreur.text() and e.qr.matrice is None
    e.champ_domaine.setText(" autre.example ")
    e.bouton_domaine.click()
    assert e.reponses.get_nowait() == "autre.example"
    assert e.zone_domaine.isHidden()
    e.bouton_domaine.click()
    assert e.reponses.empty(), "pas de seconde reponse une fois la zone fermee"
    e.montrer_qr(dict(QR, organisation="Boréal Services-conseils", domaine="boreal.example"), "")
    assert not e.ligne_org.isHidden() and "Boréal Services-conseils" in e.nom_org.text()
    e.bouton_changer.click()
    assert deposes == [1] and not e.bouton_changer.isEnabled()
    e.montrer_qr(QR, "")
    assert e.ligne_org.isHidden(), "cle configuree : pas de « Changer »"


def test_question_du_domaine_retraduite_au_changement_de_langue(app, monkeypatch):
    """Tournage du 2026-10-28 : la question deja affichee restait en francais."""
    from bfos_live import greeter as g
    monkeypatch.setattr(g, "deposer_langue", lambda code: None)
    e = g.Ecran(reseau_mod=FauxReseau())
    try:
        e.montrer_domaine(dict(DOMAINE_Q, erreur="Ce n'est pas un nom de domaine. Exemple : exemple.com",
                               erreur_source="Ce n'est pas un nom de domaine. Exemple : exemple.com"), "")
        assert "domaine" in e.consigne.text()
        e.changer_langue("en")
        assert "domaine" not in e.consigne.text() and "domain" in e.consigne.text().lower()
        assert "domaine" not in e.erreur.text() and e.erreur.text()
    finally:
        g.langue.choisir("fr")


def test_deposer_changement(tmp_path):
    from bfos_live import greeter as g
    assert g.deposer_changement(str(tmp_path / "changer")) and (tmp_path / "changer").exists()
    assert g.deposer_changement(str(tmp_path / "absent" / "changer")) is False


# --- reseau arrive apres l'attente (#27223) ---------------------------------
# VM filaire, demo Boreal : « Pas de reseau » et le panneau Wi-Fi restaient a
# l'ecran pendant tout le parcours, alors que la configuration etait bien lue.

def test_travail_signale_le_retour_du_reseau_une_seule_fois(app):
    from bfos_live import greeter as g
    s = g.Signaux()
    retours = []
    s.reseau_retabli.connect(lambda: retours.append(1))
    etats = iter(["unknown", "none", "full"])
    client = FauxClient([info(MSG_QR), {"type": "success"}])
    g.Travail(s, "bfos-connexion", client_usine=lambda: client,
              connectivite=lambda: next(etats), dormir=lambda s: None).run()
    assert retours == [1]


def test_travail_sans_attente_ne_signale_rien(app):
    from bfos_live import greeter as g
    s = g.Signaux()
    retours = []
    s.reseau_retabli.connect(lambda: retours.append(1))
    client = FauxClient([{"type": "success"}])
    g.Travail(s, "bfos-connexion", client_usine=lambda: client,
              connectivite=lambda: "full", dormir=lambda s: None).run()
    assert retours == []


def test_ecran_retire_le_bandeau_et_referme_son_wifi(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau([("Bureau", True, 70, False)]))
    e.montrer_attente_reseau("none")
    assert "Pas de réseau" in e.etiquette_reseau.text() and not e.panneau.isHidden()
    e.reseau_retabli()
    assert e.etiquette_reseau.text() == "" and e.panneau.isHidden()


def test_ecran_garde_le_wifi_ouvert_par_la_personne(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau([("Bureau", True, 70, False)]))
    e.basculer_wifi_a_la_main()
    e.reseau_retabli()
    assert not e.panneau.isHidden()


def test_ecran_retire_l_avis_de_portail(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau())
    e.montrer_attente_reseau("portal")
    assert "portail" in e.etiquette_reseau.text()
    e.reseau_retabli()
    assert e.etiquette_reseau.text() == ""


def test_travail_portail_puis_reseau_complet(app):
    """none -> portal (la tentative echoue) -> full : l'avis de portail se retire."""
    from bfos_live import greeter as g
    s = g.Signaux()
    vus = []
    s.attente_reseau.connect(lambda e: vus.append(e))
    s.reseau_retabli.connect(lambda: vus.append("retabli"))
    etats = iter(["none", "portal", "full"])
    client = FauxClient([{"type": "success"}])
    usines = iter([greetd_ipc.ErreurIPC("ferme"), client])

    def usine():
        x = next(usines)
        if isinstance(x, Exception):
            raise x
        return x
    g.Travail(s, "bfos-connexion", client_usine=usine,
              connectivite=lambda: next(etats), dormir=lambda s: None).run()
    assert vus == ["none", "portal", "retabli"]


def test_changer_de_langue_apres_le_retour_ne_rejoue_pas_l_attente(app):
    """Relecture du 2026-10-31 : _dernier rejouait montrer_attente_reseau."""
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau([("Bureau", True, 70, False)]))
    e.montrer_attente_reseau("none")
    e.reseau_retabli()
    assert e._dernier is None and e.consigne.text() == ""
    assert e.panneau.isHidden() and e.etiquette_reseau.text() == ""


def test_le_wifi_reste_ouvert_pendant_la_saisie(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau([("Bureau", True, 70, False)]))
    e.montrer_attente_reseau("none")
    e.mdp.setText("en cours")
    e.reseau_retabli()
    assert not e.panneau.isHidden()


def test_avis_retire_meme_apres_un_changement_de_langue(app):
    """Relecture n° 2 : l'avis de portail affiche en francais ne correspondait
    plus au texte anglais et restait a l'ecran."""
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau())
    e.montrer_attente_reseau("portal")
    e.etiquette_reseau.setText("Network behind a portal (autre langue)")
    e.reseau_retabli()
    assert e.etiquette_reseau.text() == ""


def test_le_wifi_reste_ouvert_si_le_champ_a_le_focus(app):
    from bfos_live import greeter as g
    e = g.Ecran(reseau_mod=FauxReseau([("Bureau", True, 70, False)]))
    e.montrer_attente_reseau("none")
    e.mdp.hasFocus = lambda: True
    e.reseau_retabli()
    assert not e.panneau.isHidden()
