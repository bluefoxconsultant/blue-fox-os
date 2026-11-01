"""Boutons de l'assistant dans la langue de l'assistant (#26870, 2026-10-27)."""
import os

import pytest

from bluefox_welcome import langue

pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)


@pytest.mark.parametrize("code,attendu", [("fr", ["Terminer", "Annuler", "Suivant", "Précédent"]),
                                          ("en", ["Finish", "Cancel", "Next", "Back"])])
def test_boutons_de_l_assistant_dans_sa_langue(code, attendu):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QWizard
    from bluefox_welcome.main import franciser_boutons, traduire_boutons
    _app = QApplication.instance() or QApplication([])
    assert franciser_boutons is traduire_boutons
    langue.choisir(code)
    try:
        w = QWizard()
        traduire_boutons(w, QWizard)
        B = QWizard.WizardButton
        assert [w.buttonText(b) for b in (B.FinishButton, B.CancelButton, B.NextButton, B.BackButton)] == attendu
    finally:
        langue.choisir("fr")
