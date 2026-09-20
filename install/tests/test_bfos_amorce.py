"""Amorce du zero-touch : la question du domaine, sa verification, et la
remise du kickstart de l'organisation a Anaconda."""
import io
import os
import pathlib
import re
import socket
import ssl
import stat
import subprocess
import sys
import urllib.error

import pytest

import bfos_amorce as ba
import bfos_provision as bp

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import render_zerotouch_ks  # noqa: E402

KS_BF = render_zerotouch_ks.render("bf", generated_at="2026-01-01T00:00:00Z")
EN_TETES_BF = {"x-bf-zerotouch-version": "v2"}


def _sans_ansi(texte):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", texte)


# ---------------------------------------------------------------------------
# Domaine
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("brut, attendu", [
    ("bluefoxconsultant.com", "bluefoxconsultant.com"),
    ("  HTTPS://BlueFoxConsultant.com/odoo/action-1  ", "bluefoxconsultant.com"),
    ("http://odoo.client.ca/", "odoo.client.ca"),
    ("client.com.", "client.com"),
    ("béland.com", "béland.com".encode("idna").decode()),
])
def test_domaine_accepte_ce_quon_colle_depuis_un_navigateur(brut, attendu):
    assert ba.normaliser_domaine(brut) == attendu


@pytest.mark.parametrize("brut", [
    "", None, "   ", "localhost", "entreprise", "a..b.com", "-x.com", "x-.com",
    "1.2.3.4", "olivier@client.com", "client.com:8443", "client com",
    "a" * 64 + ".com",
])
def test_domaine_refuse_plutot_que_deviner(brut):
    assert ba.normaliser_domaine(brut) is None


def test_parametres_noyau_bfos_seulement():
    ligne = ("BOOT_IMAGE=/images/pxeboot/vmlinuz inst.stage2=hd:LABEL=X quiet "
             "bfos.domaine=client.com bfos.ks=http://10.0.2.2:8088/essai.ks\n")
    assert ba.lire_parametres(ligne) == {
        "bfos.domaine": "client.com",
        "bfos.ks": "http://10.0.2.2:8088/essai.ks",
    }


# ---------------------------------------------------------------------------
# Blocs pre du kickstart de l'organisation
# ---------------------------------------------------------------------------
def test_le_vrai_kickstart_perd_ses_blocs_pre_et_garde_tout_le_reste():
    blocs, reste = ba.separer_pre(KS_BF)
    lignes_pre = [l for l in KS_BF.splitlines() if re.match(r"^%pre(\s|$)", l)]
    assert len(blocs) == len(lignes_pre) >= 1
    assert "--interpreter=/bin/bash" in blocs[0][0]
    assert "bfos_provision.py" in blocs[0][1]
    # Plus aucun bloc pre : la seconde passe d'Anaconda ne peut rien rejouer.
    assert not re.search(r"^%pre(\s|$)", reste, re.M)
    # Tout le reste est intact : commandes, includes, blocs post.
    for motif in (r"^ostreecontainer ", r"^%include /tmp/bfos-autopart\.ks$",
                  r"^%include /tmp/bfos-compte\.ks$", r"^rootpw --lock$"):
        assert re.search(motif, reste, re.M), motif
    assert (len(re.findall(r"^%post", reste, re.M))
            == len(re.findall(r"^%post", KS_BF, re.M)) >= 1)
    assert (len(re.findall(r"^%end", reste, re.M))
            == len(re.findall(r"^%end", KS_BF, re.M)) - len(blocs))


def test_pre_install_nest_pas_un_bloc_pre():
    ks = "%pre-install\necho a\n%end\n%pre\necho b\n%end\n"
    blocs, reste = ba.separer_pre(ks)
    assert [b[1] for b in blocs] == ["echo b\n"]
    assert "%pre-install\necho a\n%end\n" in reste


@pytest.mark.parametrize("ks", [
    "%pre\necho a\n",
    "%pre\necho a\n%post\necho b\n%end\n",
    "%pre\necho a\n%pre\necho b\n%end\n",
])
def test_bloc_pre_jamais_ferme_refuse(ks):
    with pytest.raises(ba.ErreurAmorce, match="jamais ferme"):
        ba.separer_pre(ks)


@pytest.mark.parametrize("en_tete, attendu", [
    ("%pre", {"interp": "/bin/sh", "journal": None, "erroronfail": False}),
    ("%pre --interpreter=/bin/bash --log=/tmp/x.log",
     {"interp": "/bin/bash", "journal": "/tmp/x.log", "erroronfail": False}),
    ("%pre --interpreter /usr/bin/python3 --logfile /tmp/y --erroronfail",
     {"interp": "/usr/bin/python3", "journal": "/tmp/y", "erroronfail": True}),
])
def test_en_tete_comme_pykickstart(en_tete, attendu):
    assert ba.analyser_en_tete(en_tete) == attendu


@pytest.mark.parametrize("en_tete", ["%pre --nochroot", "%pre --log", "%pre --interpreter='/bin/bash"])
def test_en_tete_incompris_refuse(en_tete):
    with pytest.raises(ba.ErreurAmorce):
        ba.analyser_en_tete(en_tete)


def test_blocs_executes_dans_lordre_avec_leur_journal(tmp_path):
    trace = tmp_path / "trace"
    journal1 = tmp_path / "un.log"
    ks = (f"%pre --log={journal1}\necho un >> {trace}\necho sortie-un\n%end\n"
          f"ostreecontainer --url=x\n"
          f"%pre --interpreter=/bin/bash\necho deux >> {trace}\nexit 3\n%end\n"
          f"%pre\necho trois >> {trace}\n%end\n")
    blocs, _ = ba.separer_pre(ks)
    messages = []
    ba.executer_pre(blocs, messages.append, dossier=str(tmp_path))
    # Un echec SANS --erroronfail n'arrete rien, comme chez Anaconda.
    assert trace.read_text().split() == ["un", "deux", "trois"]
    assert journal1.read_text().strip() == "sortie-un"
    assert (tmp_path / "bfos-locataire-pre-2.log").exists()
    assert any("code 3" in m for m in messages)
    assert stat.S_IMODE(os.stat(tmp_path / "bfos-locataire-pre-1").st_mode) == 0o600


def test_erroronfail_arrete_tout(tmp_path):
    trace = tmp_path / "trace"
    ks = (f"%pre --erroronfail\nexit 1\n%end\n"
          f"%pre\necho jamais >> {trace}\n%end\n")
    blocs, _ = ba.separer_pre(ks)
    with pytest.raises(ba.ErreurAmorce, match="code 1"):
        ba.executer_pre(blocs, lambda m: None, dossier=str(tmp_path))
    assert not trace.exists()


# ---------------------------------------------------------------------------
# Verification du kickstart servi
# ---------------------------------------------------------------------------
def test_kickstart_bf_reconnu_avec_son_organisation():
    assert ba.verifier_kickstart(KS_BF, EN_TETES_BF, cible="bluefoxconsultant.com") == "Blue Fox Inc."


def test_sans_en_tete_ce_nest_pas_blue_fox_os():
    with pytest.raises(ba.ErreurAmorce, match="ne sert pas Blue Fox OS"):
        ba.verifier_kickstart(KS_BF, {}, cible="client.com")
    # bfos.ks= (essais, depannage) ne l'exige pas.
    assert ba.verifier_kickstart(KS_BF, {}, exiger_en_tete=False) == "Blue Fox Inc."


def test_une_page_qui_nest_pas_un_kickstart_est_refusee():
    with pytest.raises(ba.ErreurAmorce, match="aucune image"):
        ba.verifier_kickstart("<html>Odoo</html>", EN_TETES_BF, cible="client.com")


def test_nom_dorganisation_sans_sequence_dechappement():
    ks = "# Tenant: Acme\x1b[2J Inc.\nostreecontainer --url=x\n"
    assert ba.verifier_kickstart(ks, EN_TETES_BF) == "Acme[2J Inc."


def test_verifier_domaine_frappe_le_bon_chemin():
    vus = []

    def telecharger(url):
        vus.append(url)
        return 200, EN_TETES_BF, KS_BF

    assert ba.verifier_domaine("client.com", telecharger) == ("Blue Fox Inc.", KS_BF)
    assert vus == ["https://client.com/blue-fox-install.ks"]


def test_redirection_vers_http_refusee():
    import urllib.request
    gestion = ba._RedirectionHttps()
    req = urllib.request.Request("https://client.com/blue-fox-install.ks")
    with pytest.raises(ba.ErreurAmorce, match="https exige"):
        gestion.redirect_request(req, None, 302, "Found", {}, "http://portail.local/ks")
    assert gestion.redirect_request(req, None, 302, "Found", {},
                                    "https://www.client.com/blue-fox-install.ks") is not None


def _url_error(raison):
    return urllib.error.URLError(raison)


@pytest.mark.parametrize("exc, attendu", [
    (urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO()), "ne sert pas Blue Fox OS"),
    (urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO()), "HTTP 500"),
    (_url_error(socket.gaierror(socket.EAI_NONAME, "Name or service not known")), "introuvable"),
    (_url_error(socket.gaierror(socket.EAI_AGAIN, "Temporary failure")), "DNS"),
    (_url_error(ssl.SSLCertVerificationError("bad cert")), "certificat"),
    (_url_error(TimeoutError()), "Pas de reponse"),
    (_url_error(ConnectionRefusedError()), "refuse la connexion"),
])
def test_chaque_panne_a_sa_phrase(exc, attendu):
    assert attendu in ba.expliquer(exc, "client.com")


def test_reprises_sur_panne_passagere_seulement():
    pauses = []
    essais = iter([_url_error(TimeoutError()), _url_error(TimeoutError()), "ok"])

    def appel():
        r = next(essais)
        if isinstance(r, Exception):
            raise r
        return r

    assert ba._avec_reprises(appel, "c", lambda m: None, sleep=pauses.append) == "ok"
    assert len(pauses) == 2

    faute = _url_error(socket.gaierror(socket.EAI_NONAME, "nope"))
    appels = []

    def faute_de_frappe():
        appels.append(1)
        raise faute

    with pytest.raises(ba.ErreurAmorce, match="introuvable"):
        ba._avec_reprises(faute_de_frappe, "c", lambda m: None, sleep=pauses.append)
    assert len(appels) == 1


# ---------------------------------------------------------------------------
# Reseau
# ---------------------------------------------------------------------------
_ROUTE_ENTETE = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"


def test_route_par_defaut_ipv4(tmp_path):
    r4, r6 = tmp_path / "route", tmp_path / "ipv6_route"
    r6.write_text("")
    r4.write_text(_ROUTE_ENTETE + "enp1s0\t0002000A\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n")
    assert not ba.reseau_pret(r4, r6)
    r4.write_text(_ROUTE_ENTETE + "enp1s0\t00000000\t0202000A\t0003\t0\t0\t100\t00000000\t0\t0\t0\n")
    assert ba.reseau_pret(r4, r6)


def test_route_par_defaut_ipv6_hors_lo(tmp_path):
    r4, r6 = tmp_path / "route", tmp_path / "ipv6_route"
    r4.write_text(_ROUTE_ENTETE)
    zero = "0" * 32
    r6.write_text(f"{zero} 00 {zero} 00 {zero} ffffffff 00000001 00000000 00200200 lo\n")
    assert not ba.reseau_pret(r4, r6)
    r6.write_text(f"{zero} 00 {zero} 00 fe800000000000000000000000000001 00000400 00000001 00000000 00000003 enp1s0\n")
    assert ba.reseau_pret(r4, r6)


class Horloge:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_attente_reseau_annonce_puis_abandonne_si_patience():
    h, dits = Horloge(), []
    assert ba.attendre_reseau(dits.append, pret=lambda: False, sleep=h.sleep,
                              now=h.now, patience=90) is False
    assert dits[0].startswith("Connexion au reseau")
    assert any("cable" in d for d in dits)


# ---------------------------------------------------------------------------
# La question, a l'ecran
# ---------------------------------------------------------------------------
class FausseConsole:
    def __init__(self, saisies):
        self.saisies = list(saisies)
        self.ecrit = []
        self.fermee = False

    def ecrire(self, t):
        self.ecrit.append(t)

    def effacer(self):
        self.ecrit.append("<effacer>")

    def lire_ligne(self):
        if not self.saisies:
            raise AssertionError("saisie inattendue")
        return self.saisies.pop(0)

    def fermer(self):
        self.fermee = True

    @property
    def texte(self):
        return _sans_ansi("".join(self.ecrit))


def _verifier_selon(table):
    vus = []

    def verifier(domaine):
        vus.append(domaine)
        r = table[domaine]
        if isinstance(r, Exception):
            raise r
        return r, f"ks de {domaine}"

    return verifier, vus


def _demander(console, defaut, verifier):
    return ba.demander(console, defaut, verifier, pret=lambda: True,
                       sleep=lambda s: None, now=lambda: 0.0)


def test_entree_seule_prend_le_domaine_propose_puis_confirme():
    console = FausseConsole(["", ""])
    verifier, vus = _verifier_selon({"bluefoxconsultant.com": "Blue Fox Inc."})
    assert _demander(console, "bluefoxconsultant.com", verifier) == (
        "bluefoxconsultant.com", "Blue Fox Inc.", "ks de bluefoxconsultant.com")
    assert vus == ["bluefoxconsultant.com"]
    assert "Domaine [bluefoxconsultant.com] :" in console.texte
    assert "Organisation : Blue Fox Inc." in console.texte


def test_saisie_invalide_redemandee_sans_rien_verifier():
    console = FausseConsole(["pas un domaine", "client.com", ""])
    verifier, vus = _verifier_selon({"client.com": "Client inc."})
    assert _demander(console, "bluefoxconsultant.com", verifier)[0] == "client.com"
    assert vus == ["client.com"]
    assert "'pas un domaine' n'est pas un domaine." in console.texte


def test_echec_de_verification_dit_pourquoi_et_propose_le_meme_domaine():
    panne = _url_error(socket.gaierror(socket.EAI_NONAME, "nope"))
    console = FausseConsole(["clinet.com", "client.com", ""])
    verifier, vus = _verifier_selon({"clinet.com": panne, "client.com": "Client inc."})
    assert _demander(console, "bluefoxconsultant.com", verifier)[0] == "client.com"
    assert vus == ["clinet.com", "client.com"]
    assert "Domaine introuvable : clinet.com" in console.texte
    assert "Domaine [clinet.com] :" in console.texte


def test_un_autre_domaine_a_la_confirmation_est_verifie_directement():
    console = FausseConsole(["", "client.com", ""])
    verifier, vus = _verifier_selon({"bluefoxconsultant.com": "Blue Fox Inc.",
                                     "client.com": "Client inc."})
    assert _demander(console, "bluefoxconsultant.com", verifier)[1] == "Client inc."
    assert vus == ["bluefoxconsultant.com", "client.com"]


def test_non_a_la_confirmation_ramene_a_la_question():
    console = FausseConsole(["", "non", "client.com", ""])
    verifier, vus = _verifier_selon({"bluefoxconsultant.com": "Blue Fox Inc.",
                                     "client.com": "Client inc."})
    assert _demander(console, "bluefoxconsultant.com", verifier)[0] == "client.com"
    assert vus == ["bluefoxconsultant.com", "client.com"]


def test_sans_proposition_il_faut_taper_quelque_chose():
    console = FausseConsole(["", "client.com", ""])
    verifier, _ = _verifier_selon({"client.com": "Client inc."})
    assert _demander(console, "", verifier)[0] == "client.com"
    assert "Domaine : " in console.texte
    assert "Entrez le domaine de votre organisation." in console.texte


def test_ecrans_tiennent_dans_78_colonnes():
    longue = "Domaine introuvable : " + "tres-long-sous-domaine." * 6 + "com."
    for ecran in (ba.ecran_domaine(longue), ba.ecran_confirmation("Blue Fox Inc.", "bluefoxconsultant.com")):
        for ligne in _sans_ansi(ecran).splitlines():
            assert len(ligne) <= 78, ligne


def test_meme_renard_et_memes_couleurs_que_lecran_dautorisation():
    assert ba._RENARD == bp._RENARD
    assert (ba._MARQUE, ba._GRAS, ba._FIN) == (bp._MARQUE, bp._GRAS, bp._FIN)


# ---------------------------------------------------------------------------
# Bout a bout
# ---------------------------------------------------------------------------
class Lanceur:
    def __init__(self):
        self.appels = []

    def __call__(self, argv, **kw):
        self.appels.append((argv, pathlib.Path(argv[1]).read_text()))
        return subprocess.CompletedProcess(argv, 0)


def _telecharger_bf(url):
    assert url == "https://bluefoxconsultant.com/blue-fox-install.ks"
    return 200, EN_TETES_BF, KS_BF


def test_parcours_complet_depose_le_kickstart_sans_blocs_pre(tmp_path):
    console, lanceur, messages = FausseConsole(["", ""]), Lanceur(), []
    org = ba.run({}, domaine_defaut="bluefoxconsultant.com", journal=messages.append,
                 console_factory=lambda: console, telecharger_=_telecharger_bf,
                 pret=lambda: True, lancer=lanceur, dossier=str(tmp_path))
    assert org == "Blue Fox Inc."
    assert console.fermee
    depose = tmp_path / "bfos-locataire.ks"
    assert stat.S_IMODE(os.stat(depose).st_mode) == 0o600
    assert depose.read_text() == ba.separer_pre(KS_BF)[1]
    assert [a[0][0] for a in lanceur.appels] == ["/bin/bash"]
    assert "bfos_provision.py" in lanceur.appels[0][1]


def test_la_console_est_rendue_meme_si_tout_casse(tmp_path):
    console = FausseConsole([])  # lire_ligne leve
    with pytest.raises(AssertionError):
        ba.run({}, journal=lambda m: None, console_factory=lambda: console,
               pret=lambda: True, dossier=str(tmp_path))
    assert console.fermee


def test_bfos_domaine_ne_pose_aucune_question(tmp_path):
    def pas_de_console():
        raise AssertionError("aucune question attendue")

    org = ba.run({"bfos.domaine": "BlueFoxConsultant.com"}, journal=lambda m: None,
                 console_factory=pas_de_console, telecharger_=_telecharger_bf,
                 pret=lambda: True, lancer=Lanceur(), dossier=str(tmp_path))
    assert org == "Blue Fox Inc."


def test_bfos_ks_prend_ce_kickstart_la_sans_en_tete(tmp_path):
    vus = []

    def telecharger(url):
        vus.append(url)
        return 200, {}, KS_BF

    ba.run({"bfos.ks": "http://10.0.2.2:8088/bfos-essai.ks"}, journal=lambda m: None,
           console_factory=None, telecharger_=telecharger, pret=lambda: True,
           lancer=Lanceur(), dossier=str(tmp_path))
    assert vus == ["http://10.0.2.2:8088/bfos-essai.ks"]
    assert (tmp_path / "bfos-locataire.ks").exists()


def test_sans_console_on_dit_comment_imposer_le_domaine(tmp_path):
    def sans_console():
        raise FileNotFoundError("/dev/tty0")

    with pytest.raises(ba.ErreurAmorce, match="bfos.domaine="):
        ba.run({}, journal=lambda m: None, console_factory=sans_console,
               dossier=str(tmp_path))
    assert not (tmp_path / "bfos-locataire.ks").exists()


def test_main_rend_1_et_explique(tmp_path, capsys):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("quiet bfos.domaine=pas_un_domaine\n")
    assert ba.main(["--cmdline", str(cmdline)]) == 1
    assert "ERREUR" in capsys.readouterr().err
