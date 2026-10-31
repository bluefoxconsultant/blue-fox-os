"""Langues de la clé live (#26870, 2026-10-27 : « la clé, toutes langues »)."""
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from bfos_live import langue
from bfos_live import pam_helper as ph

ICI = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def francais():
    langue.choisir("fr")
    yield
    langue.choisir("fr")


def extracteur():
    spec = importlib.util.spec_from_file_location("extraire_po", ICI / "outils" / "extraire_po.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ lecteur PO
def test_lire_po_lignes_echappements_et_flous(tmp_path):
    po = tmp_path / "xx.po"
    po.write_text('msgid ""\nmsgstr "Language: xx\\n"\n\n'
                  'msgid "Un"\nmsgstr "One"\n\n'
                  'msgid ""\n"Deux\\n"\n"lignes"\nmsgstr ""\n"Two\\n"\n"lines"\n\n'
                  '#, fuzzy\nmsgid "Flou"\nmsgstr "Fuzzy"\n\n'
                  'msgid "Vide"\nmsgstr ""\n\n'
                  'msgid "Guillemets \\"x\\""\nmsgstr "Quotes \\"x\\""\n', encoding="utf-8")
    assert langue.lire_po(po) == {"Un": "One", "Deux\nlignes": "Two\nlines",
                                  'Guillemets "x"': 'Quotes "x"'}


def test_normaliser_et_repli_en_francais():
    assert langue.normaliser("en_CA.UTF-8") == "en"
    assert langue.normaliser("en-US") == "en"
    assert langue.normaliser("fr_CA") == "fr"
    assert langue.normaliser("xx") is None and langue.normaliser("") is None
    assert langue.choisir("klingon") == "fr"
    assert langue._("Connexion annulée.") == "Connexion annulée."


def test_choisir_l_anglais_traduit_et_garde_le_reste():
    assert langue.choisir("en_CA.UTF-8") == "en"
    assert langue._("Connexion annulée.") == "Sign-in cancelled."
    assert langue._("Texte jamais traduit") == "Texte jamais traduit"


def test_disponibles_francais_en_tete():
    assert langue.disponibles()[0] == "fr" and "en" in langue.disponibles()


# ------------------------------------------------------------------ fichier de l'écran
def test_langue_de_l_ecran_lue_proprement(tmp_path):
    f = tmp_path / "langue"
    f.write_text("en\n")
    assert langue.lire_langue_ecran(str(f), proprietaire=os.getuid()) == "en"
    assert langue.lire_langue_ecran(str(f), proprietaire=os.getuid() + 1) is None, "autre proprietaire"
    f.write_text("../../etc\n")
    assert langue.lire_langue_ecran(str(f)) is None
    f.write_text("xx\n")
    assert langue.lire_langue_ecran(str(f)) is None, "langue sans catalogue"
    lien = tmp_path / "lien"
    f.write_text("en\n")
    lien.symlink_to(f)
    assert langue.lire_langue_ecran(str(lien)) is None, "lien refuse"
    assert langue.lire_langue_ecran(str(tmp_path / "absent")) is None


def test_aide_prend_l_ecran_sinon_la_cle(tmp_path, monkeypatch):
    f = tmp_path / "langue"
    f.write_text("en\n")
    monkeypatch.setattr(ph.pwd, "getpwnam", lambda n: type("P", (), {"pw_uid": os.getuid()}))
    assert ph.langue_de_l_ecran({"langue": "fr"}, chemin=str(f)) == "en"
    f.unlink()
    assert ph.langue_de_l_ecran({"langue": "en_CA"}, chemin=str(f)) == "en"
    assert ph.langue_de_l_ecran({}, chemin=str(f)) == "fr"


# ------------------------------------------------------------------ catalogue
def test_chaque_texte_marque_a_sa_traduction_anglaise():
    en = langue.lire_po(langue.DOSSIER_PO + "/en.po")
    manquants = [m for m, _r in extracteur().relever() if not en.get(m)]
    assert not manquants, manquants


def test_memes_champs_dans_la_traduction():
    en = langue.lire_po(langue.DOSSIER_PO + "/en.po")
    for m, t in en.items():
        assert sorted(re.findall(r"\{\w+\}", m)) == sorted(re.findall(r"\{\w+\}", t)), m


def test_gabarit_a_jour(tmp_path):
    """Le gabarit livre est exactement celui que l'outil regenererait depuis le code."""
    x = extracteur()
    x.ecrire(tmp_path / "frais.pot", x.relever(), {}, "")
    assert (tmp_path / "frais.pot").read_text(encoding="utf-8") == \
        (Path(langue.DOSSIER_PO) / "bfos-live.pot").read_text(encoding="utf-8")


def test_refus_traduit_par_la_vraie_aide(tmp_path, monkeypatch, capsys):
    """main() de l'aide, sans configuration, en anglais : le refus sort traduit vers la cale."""
    monkeypatch.setattr(ph, "CONFIG", str(tmp_path / "absent.json"))
    # L'ecran a choisi l'anglais ; la config manque : l'aide relit quand meme la langue de l'ecran.
    monkeypatch.setattr(ph, "langue_de_l_ecran", lambda cfg: "en")
    assert langue.courante() == "fr"
    assert ph.main(["auth"]) == 1
    sortie = capsys.readouterr().out
    assert "ERROR This key isn't set up." in sortie and "Cette clé" not in sortie


def test_choix_de_session_dans_la_langue_de_l_ecran():
    langue.choisir("en")
    texte = ph.message_choix("greetd", {"rich_services": ["greetd"]})
    d = json.loads(texte.split("\n", 1)[0][len(ph.PREFIXE_CHOIX):])
    assert d["titre"] == "How would you like to start your session?"
    assert [o["titre"] for o in d["options"]][:2] == ["Temporary session", "Keep my desktop on this key"]
    assert [o["id"] for o in d["options"]] == ["ephemere", "persistant", "itinerant"]


# ------------------------------------------------------------------ écran
# Seuls les essais d'interface demandent PyQt6 : un saut au niveau du module emportait aussi
# les essais de catalogue (relecture adverse du 2026-10-27).
requiert_qt = pytest.mark.skipif(importlib.util.find_spec("PyQt6") is None, reason="PyQt6 absent")


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Reseau:
    def reseaux_wifi(self):
        return []

    def connecter_wifi(self, *a):
        return True, ""


@requiert_qt
def test_ecran_bascule_en_anglais_et_rejoue_le_qr(app, tmp_path, monkeypatch):
    from bfos_live import greeter as g
    deposes = []
    monkeypatch.setattr(g, "deposer_langue", lambda code: deposes.append(code) or True)
    e = g.Ecran(reseau_mod=Reseau())
    assert e.choix_langue.count() == len(langue.disponibles()) >= 2
    e.montrer_qr({"uri": "https://auth.exemple.test/device", "uri_complete": "https://auth.exemple.test/device?c=1",
                  "user_code": "123456789", "expires_in": 600}, "")
    assert "Scanne" in e.consigne.text()
    e.choix_langue.setCurrentIndex(e.choix_langue.findData("en"))
    assert e.consigne.text() == "Scan this code with your phone to sign in."
    assert e.aide.text() == "Or go to auth.exemple.test/device and enter the code above."
    assert e.etat.text().startswith("Code valid for")
    assert e.mdp.placeholderText() == "Wi-Fi password"
    assert deposes == ["en"], "choix depose pour l'aide PAM"
    e.montrer_progres({"etape": 2, "total": 5, "texte": "Opening your encrypted profile…"})
    assert e.etat.text() == "Opening your encrypted profile… (2 of 5)"
    e.choix_langue.setCurrentIndex(e.choix_langue.findData("fr"))
    assert e.consigne.text() == "Ouverture de ta session" and "2 sur 5" in e.etat.text()
    assert deposes == ["en", "fr"]


@requiert_qt
def test_deposer_langue_ecrit_puis_renomme(tmp_path):
    from bfos_live import greeter as g
    cible = tmp_path / "langue"
    assert g.deposer_langue("en", str(cible)) and cible.read_text() == "en\n"
    assert not (tmp_path / "langue.tmp").exists()
    assert g.deposer_langue("en", str(tmp_path / "absent" / "langue")) is False


@requiert_qt
def test_choix_d_un_autre_que_greetd_ignore(tmp_path, monkeypatch):
    """Le fichier n'est pas a l'usager de l'ecran : l'aide (root) l'ignore et prend la cle."""
    f = tmp_path / "langue"
    f.write_text("en\n")
    monkeypatch.setattr(ph.pwd, "getpwnam", lambda n: type("P", (), {"pw_uid": os.getuid() + 1}))
    assert ph.langue_de_l_ecran({"langue": "fr"}, chemin=str(f)) == "fr"


# ------------------------------------------------------------------ catalogues abîmés (relecture du 2026-10-27)
def _champs(texte):
    import string
    return sorted((nom, conv, spec) for _l, nom, spec, conv in string.Formatter().parse(texte) if nom is not None)


@pytest.mark.parametrize("po", sorted(Path(langue.DOSSIER_PO).glob("*.po"))
                         + sorted((ICI.parent / "welcome" / "bluefox_welcome" / "po").glob("*.po")),
                         ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_chaque_catalogue_lisible_et_memes_champs(po):
    for m, t in langue.lire_po(po).items():
        assert _champs(m) == _champs(t), (po.name, m, t)


def test_traduction_piegee_retombe_sur_le_francais(tmp_path, monkeypatch):
    (tmp_path / "zz.po").write_text('msgid "Code valide encore {duree}"\nmsgstr "Valid {duree} {x.y} {"\n',
                                    encoding="utf-8")
    monkeypatch.setattr(langue, "DOSSIER_PO", str(tmp_path))
    monkeypatch.setitem(langue._catalogues, "zz", langue.lire_po(tmp_path / "zz.po"))
    assert langue.choisir("zz") == "zz"
    assert langue._("Code valide encore {duree}").format(duree="9:59") == "Code valide encore 9:59"


@pytest.mark.parametrize("contenu", [b"msgid Annuler\nmsgstr \"x\"\n", "msgid \"Annuler\"\nmsgstr \"\xe9\"\n".encode("latin-1")])
def test_catalogue_abime_vaut_francais(tmp_path, monkeypatch, contenu):
    (tmp_path / "zz.po").write_bytes(contenu)
    monkeypatch.setattr(langue, "DOSSIER_PO", str(tmp_path))
    langue._catalogues.pop("zz", None)
    assert langue.choisir("zz") == "zz"
    assert langue._("Annuler") == "Annuler"
    langue._catalogues.pop("zz", None)


@requiert_qt
def test_rejeu_apres_le_choix_garde_la_barre(app, monkeypatch):
    from bfos_live import greeter as g
    monkeypatch.setattr(g, "deposer_langue", lambda code: True)
    e = g.Ecran(reseau_mod=Reseau())
    e.show()
    e.montrer_choix({"options": [{"id": "ephemere", "titre": "Session éphémère", "texte": ""}]}, "")
    e.repondre_choix("ephemere")
    e.montrer_info("Préparation de ta session éphémère…")
    e.montrer_erreur("Le code a expiré.")
    e.choix_langue.setCurrentIndex(e.choix_langue.findData("en"))
    assert e.zone_choix.isHidden() and not e.barre.isHidden(), "pas de retour aux cartes du choix"
    assert e.erreur.text() == "Le code a expiré.", "l'erreur affichee reste"
    e.close()
