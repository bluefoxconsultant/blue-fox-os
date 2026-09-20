"""Le secret ne doit à AUCUN instant être lisible au-delà de son propriétaire.

L'ancien test de rclone.conf assertait `600` sur l'état FINAL, et il était vert
sur du code qui créait le fichier en 0664 avant de le restreindre. Ces tests-ci
gardent le mécanisme : le mode que le NOYAU applique à la création, et le cas
du fichier déjà là qu'une machine ayant tourné avec la version fautive porte
encore.
"""
import os
from pathlib import Path

import pytest

from bluefox_welcome.secure_file import MODE_PRIVATE, write_private


def test_le_mode_est_pose_a_la_creation_pas_apres(tmp_path: Path, monkeypatch):
    """Le test qui distingue vraiment les deux implémentations.

    Un `write_text` + `chmod` finit lui aussi à 600 : lire le mode après coup
    ne prouve rien. Ce qu'on garde ici, c'est que le mode part avec l'appel
    système de création.
    """
    vus = []
    vrai_open = os.open

    def espion(path, flags, mode=0o777, **kw):
        vus.append((str(path), mode))
        return vrai_open(path, flags, mode, **kw)

    monkeypatch.setattr(os, "open", espion)
    cible = tmp_path / "secret.conf"
    write_private(cible, "pass = hunter2\n")

    assert vus, "le fichier n'a pas été créé par os.open — mode posé après coup ?"
    assert vus[-1][1] == MODE_PRIVATE


def test_umask_permissif_ne_relache_pas_le_fichier(tmp_path: Path):
    ancien = os.umask(0o000)
    try:
        cible = tmp_path / "secret.conf"
        write_private(cible, "pass = hunter2\n")
        assert oct(cible.stat().st_mode)[-3:] == "600"
    finally:
        os.umask(ancien)


def test_fichier_deja_la_en_0644_est_ramene_a_600(tmp_path: Path):
    """Le chemin qu'emprunte toute machine ayant tourné avec la version fautive.

    O_CREAT n'applique pas son mode à un fichier existant et O_TRUNC ne remet
    pas ses permissions : sans le fchmod, le 0644 hérité survivrait à la
    réécriture.
    """
    cible = tmp_path / "secret.conf"
    cible.write_text("vieux\n")
    os.chmod(cible, 0o644)

    write_private(cible, "pass = hunter2\n")

    assert oct(cible.stat().st_mode)[-3:] == "600"
    assert cible.read_text() == "pass = hunter2\n"


def test_un_secret_qu_on_ne_peut_pas_restreindre_leve(tmp_path: Path, monkeypatch):
    """Pas de `except OSError: pass` : échouer vaut mieux que poser en clair."""
    def refuse(*a, **kw):
        raise OSError(1, "operation not permitted")

    monkeypatch.setattr(os, "fchmod", refuse)
    with pytest.raises(OSError):
        write_private(tmp_path / "secret.conf", "pass = hunter2\n")


def test_le_contenu_est_bien_ecrit(tmp_path: Path):
    cible = tmp_path / "secret.conf"
    write_private(cible, "une ligne\net une autre\n")
    assert cible.read_text() == "une ligne\net une autre\n"
