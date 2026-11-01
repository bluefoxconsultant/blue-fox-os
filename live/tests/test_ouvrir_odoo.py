"""Ouverture d'Odoo connecté à l'ouverture du bureau (#26870)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bfos_live import ouvrir_odoo as oo  # noqa: E402

LIEN = "https://erp.test/odoo?bfos_login=JETON"


def deposer(tmp_path, texte=LIEN + "\n", age=0):
    f = tmp_path / "moi"
    if f.is_symlink() or f.exists():
        f.unlink()
    f.write_text(texte)
    t = time.time() - age
    os.utime(f, (t, t))
    return f


def test_lien_frais_lu(tmp_path):
    deposer(tmp_path)
    assert oo.lien_frais("moi", dossier=str(tmp_path)) == LIEN


def test_lien_trop_vieux_ignore(tmp_path):
    deposer(tmp_path, age=oo.VALIDITE + 5)
    assert oo.lien_frais("moi", dossier=str(tmp_path)) is None


def test_lien_suspect_ignore(tmp_path):
    for texte in ("http://erp.test/odoo", "https://erp.test/web", "https://erp.test/odoo x",
                  "file:///etc/passwd"):
        deposer(tmp_path, texte)
        assert oo.lien_frais("moi", dossier=str(tmp_path)) is None, texte


def test_lien_absent_ou_lien_symbolique(tmp_path):
    assert oo.lien_frais("moi", dossier=str(tmp_path)) is None
    cible = tmp_path / "ailleurs"
    cible.write_text(LIEN)
    os.symlink(cible, tmp_path / "moi")
    assert oo.lien_frais("moi", dossier=str(tmp_path)) is None


def test_ouverture_dans_la_fenetre_odoo(tmp_path, monkeypatch):
    monkeypatch.setattr(oo, "DOSSIER", str(tmp_path))
    monkeypatch.setattr(oo, "lien_frais", lambda nom: LIEN)
    lances = []
    assert oo.main(run=lambda argv, **kw: lances.append(argv)) == 0
    assert lances == [oo.BRAVE + [f"--app={LIEN}"]]
    assert "--profile-directory=Default" in oo.BRAVE
