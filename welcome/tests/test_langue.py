import importlib.util
"""Langues de l'agent d'accueil (#26870, 2026-10-27 : « la clé, toutes langues »)."""
import ast
import re
import sys
from pathlib import Path

import pytest

from bluefox_welcome import langue

PAQUET = Path(langue.__file__).resolve().parent
EN = PAQUET / "po" / "en.po"
GABARIT = PAQUET / "po" / "bluefox-welcome.pot"


@pytest.fixture(autouse=True)
def francais_apres():
    yield
    langue.choisir("fr")


def releves():
    """Les textes marqués dans le paquet, par AST (comme outils/extraire_po.py)."""
    vus = set()
    for fichier in PAQUET.rglob("*.py"):
        for n in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("_", "N_")
                    and len(n.args) == 1 and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)):
                vus.add(n.args[0].value)
    return vus


# ------------------------------------------------------------------ lecteur PO
def test_lire_po_lignes_multiples_echappements_et_flou(tmp_path):
    po = tmp_path / "xx.po"
    po.write_text('msgid ""\nmsgstr ""\n"Language: xx\\n"\n\n'
                  '#: a.py:1\nmsgid "Bonjour"\nmsgstr "Hello"\n\n'
                  'msgid ""\n"Deux "\n"lignes"\nmsgstr ""\n"Two "\n"lines"\n\n'
                  'msgid "Guillemets \\"x\\" et\\nsaut"\nmsgstr "Quotes \\"x\\" and\\nbreak"\n\n'
                  '#, fuzzy\nmsgid "Douteux"\nmsgstr "Doubtful"\n\n'
                  'msgid "Vide"\nmsgstr ""\n', encoding="utf-8")
    d = langue.lire_po(po)
    assert d == {"Bonjour": "Hello", "Deux lignes": "Two lines",
                 'Guillemets "x" et\nsaut': 'Quotes "x" and\nbreak'}


def test_texte_non_traduit_reste_en_francais():
    langue.choisir("en")
    assert langue._("Un texte que personne n'a traduit") == "Un texte que personne n'a traduit"
    assert langue.N_("Constante") == "Constante"


@pytest.mark.parametrize("code,attendu", [("en", "en"), ("en_CA.UTF-8", "en"), ("en-CA", "en"),
                                          ("fr_CA.UTF-8", "fr"), ("C.UTF-8", None), ("", None),
                                          ("xx_YY", None), ("../en", None)])
def test_normaliser(code, attendu):
    assert langue.normaliser(code) == attendu


def test_choisir_inconnu_retombe_en_francais():
    assert langue.choisir("klingon") == "fr" and langue._("Terminer") == "Terminer"


# ------------------------------------------------------------------ langue de la session
@pytest.mark.parametrize("env,attendu", [
    ({"LANGUAGE": "en_CA:en", "LANG": "fr_CA.UTF-8"}, "en"),     # LANGUAGE l'emporte (plasma-localerc)
    ({"LANGUAGE": "de:en"}, "en"),                                # premier code offert de la liste
    ({"LC_ALL": "en_US.UTF-8", "LANG": "fr_CA.UTF-8"}, "en"),
    ({"LANG": "en_CA.UTF-8"}, "en"),
    ({"LANG": "fr_CA.UTF-8"}, "fr"),
    ({"LANG": "C.UTF-8"}, "fr"),                                  # rien d'offert : français
    ({}, "fr"),
])
def test_choisir_depuis_env(env, attendu):
    assert langue.choisir_depuis_env(env) == attendu == langue.courante()


# ------------------------------------------------------------------ catalogues
def test_anglais_complet():
    """Chaque texte marqué dans le paquet a sa traduction anglaise."""
    en = langue.lire_po(EN)
    manquants = sorted(t for t in releves() if not en.get(t))
    assert not manquants, f"textes sans traduction anglaise (outils/extraire_po.py) : {manquants}"


def test_gabarit_a_jour():
    """Le gabarit des nouvelles langues porte exactement les textes du code."""
    texte = GABARIT.read_text(encoding="utf-8")
    ids = set(re.findall(r'^msgid "(.+)"$', texte, re.M))
    assert len(ids) == len(releves()), "relancer outils/extraire_po.py"


def test_traductions_gardent_leurs_champs():
    """Un {champ} oublié ou renommé ferait planter le .format() à l'écran."""
    champ = re.compile(r"\{(\w*)\}")
    for code in [p.stem for p in (PAQUET / "po").glob("*.po")]:
        for msgid, msgstr in langue.lire_po(PAQUET / "po" / f"{code}.po").items():
            assert sorted(champ.findall(msgid)) == sorted(champ.findall(msgstr)), (code, msgid)


def test_po_part_avec_le_paquet():
    pyproject = (PAQUET.parent / "pyproject.toml").read_text()
    assert '"po/*.po"' in pyproject


# ------------------------------------------------------------------ assistant en anglais
# Seuls les essais d'interface demandent PyQt6 : un saut au niveau du module emportait aussi
# les essais de catalogue (relecture adverse du 2026-10-27).
requiert_qt = pytest.mark.skipif(importlib.util.find_spec("PyQt6") is None, reason="PyQt6 absent")


@requiert_qt
def test_assistant_en_anglais(tenant_data, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QWizard
    from bluefox_welcome import main
    langue.choisir("en")
    _app, wizard = main.build_manual_wizard(tenant_data, prefill_email="camille@example.test")
    B = QWizard.WizardButton
    assert wizard.windowTitle() == "Blue Fox OS — First-time setup"
    assert [wizard.buttonText(b) for b in (B.FinishButton, B.CancelButton, B.NextButton, B.BackButton)] == \
        ["Finish", "Cancel", "Next", "Back"]
    titres = [wizard.page(i).title() for i in wizard.pageIds()]
    assert titres == ["Welcome to Blue Fox OS", "Identity — Authentik", "Nextcloud files",
                      "Bitwarden vault", "Summary"]
    wizard.setField("user_email", "camille@example.test")
    wizard.page(wizard.pageIds()[-1]).initializePage()
    recap = wizard.page(wizard.pageIds()[-1]).findChild(__import__("PyQt6.QtWidgets", fromlist=["QTextEdit"]).QTextEdit).toPlainText()
    assert "Ready to set up" in recap and "Click Finish to apply these settings." in recap
    assert "Organization: bf" in recap and "Configuration" not in recap


@requiert_qt
def test_cli_prend_la_langue_de_la_session(tmp_path, monkeypatch):
    from bluefox_welcome import main
    drapeau = tmp_path / "done"
    drapeau.write_text("")
    monkeypatch.setattr(main, "DONE_FLAG", drapeau)
    monkeypatch.setattr(main, "_euid", lambda: 1000)
    monkeypatch.setattr(main, "refresh_browser_policy", lambda: (False, "essai"))
    monkeypatch.setattr(sys, "argv", ["bluefox-welcome"])
    monkeypatch.setenv("LANGUAGE", "en_CA:en")
    assert main.cli() == 0
    assert langue.courante() == "en"
