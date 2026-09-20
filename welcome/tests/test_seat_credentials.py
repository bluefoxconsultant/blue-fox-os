"""Identifiants Nextcloud deposes par l'installation (#22436)."""
import json
import logging

from bluefox_welcome import seat_credentials as sc


def _ecrire(tmp_path, contenu):
    p = tmp_path / "nc-credentials.json"
    p.write_text(json.dumps(contenu), encoding="utf-8")
    return p


class TestLire:
    def test_lit_le_couple_depose(self, tmp_path):
        p = _ecrire(tmp_path, {"login": "Olivier", "app_password": "A1OK" * 18,
                               "url": "https://nc.exemple.com"})
        assert sc.lire(p) == ("Olivier", "A1OK" * 18)

    def test_absent_rend_none(self, tmp_path):
        assert sc.lire(tmp_path / "rien.json") is None

    def test_incomplet_rend_none(self, tmp_path):
        assert sc.lire(_ecrire(tmp_path, {"login": "Olivier"})) is None
        assert sc.lire(_ecrire(tmp_path, {"app_password": "x"})) is None

    def test_json_casse_rend_none(self, tmp_path):
        p = tmp_path / "nc-credentials.json"
        p.write_text("{ pas du json", encoding="utf-8")
        assert sc.lire(p) is None

    def test_illisible_le_dit_fort(self, tmp_path, monkeypatch, caplog):
        """⚠️ Le cas qui RESSEMBLE a « pas d'identifiants » sans en etre un.

        Si bluefox-seat-credentials.service n'a pas remis le fichier a cet
        usager, retomber en silence sur le SSO ferait passer une panne de
        remise pour une absence de preparation.
        """
        p = _ecrire(tmp_path, {"login": "Olivier", "app_password": "x"})
        vrai = type(p).read_text

        def refuse(self, *a, **k):
            if self == p:
                raise PermissionError(13, "Permission denied")
            return vrai(self, *a, **k)

        monkeypatch.setattr(type(p), "read_text", refuse)
        with caplog.at_level(logging.ERROR):
            assert sc.lire(p) is None
        assert "NE SERONT PAS utilises" in caplog.text


class TestEffacer:
    def test_efface_apres_emploi(self, tmp_path):
        p = _ecrire(tmp_path, {"login": "a", "app_password": "b"})
        sc.effacer(p)
        assert not p.exists()

    def test_absent_ne_leve_pas(self, tmp_path):
        sc.effacer(tmp_path / "rien.json")  # ne doit rien lever
