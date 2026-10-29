"""Identifiants Nextcloud deposes par l'installation (#22436)."""
import json
import logging

import pytest

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



class TestServeurAttendu:
    """#27109 : le mot de passe d'application est frappe pour le Nextcloud de
    la POLITIQUE. L'agent ne le rend que si c'est bien ce serveur-la qu'il va
    monter ; sinon il l'enverrait a un autre (celui de l'image)."""

    MDP = "A1OK" * 18

    def _depot(self, tmp_path, url="https://cloud.autre.org"):
        contenu = {"login": "Ana", "app_password": self.MDP}
        if url is not None:
            contenu["url"] = url
        return _ecrire(tmp_path, contenu)

    def test_meme_serveur_rend_le_couple(self, tmp_path):
        p = self._depot(tmp_path)
        assert sc.lire(p, attendu="https://cloud.autre.org") == ("Ana", self.MDP)

    @pytest.mark.parametrize("attendu", [
        "https://cloud.autre.org/",
        "https://Cloud.Autre.ORG",
        "HTTPS://cloud.autre.org",
        "https://cloud.autre.org:443",
        "https://cloud.autre.org//",
    ])
    def test_ecarts_de_forme_seulement(self, tmp_path, attendu):
        assert sc.lire(self._depot(tmp_path), attendu=attendu) == ("Ana", self.MDP)

    def test_depot_avec_barre_finale(self, tmp_path):
        p = self._depot(tmp_path, url="https://cloud.autre.org/")
        assert sc.lire(p, attendu="https://cloud.autre.org") == ("Ana", self.MDP)

    @pytest.mark.parametrize("attendu", [
        "https://nextcloud.bluefoxconsultant.com",   # l'image, pas la politique
        "https://cloud.autre.org.piege.net",
        "http://cloud.autre.org",
        "https://cloud.autre.org:8443",
        "https://cloud.autre.org/nextcloud",
        "",
        "pas une url",
    ])
    def test_autre_serveur_rend_none(self, tmp_path, attendu):
        assert sc.lire(self._depot(tmp_path), attendu=attendu) is None

    def test_le_refus_se_journalise_sans_le_mot_de_passe(self, tmp_path, caplog):
        p = self._depot(tmp_path)
        with caplog.at_level(logging.ERROR):
            assert sc.lire(p, attendu="https://nextcloud.bluefoxconsultant.com") is None
        assert "https://cloud.autre.org" in caplog.text
        assert "nextcloud.bluefoxconsultant.com" in caplog.text
        assert self.MDP not in caplog.text

    @pytest.mark.parametrize("url", [None, "", 42, ["https://cloud.autre.org"]])
    def test_depot_sans_url_utilisable_rend_none(self, tmp_path, url):
        p = _ecrire(tmp_path, {"login": "Ana", "app_password": self.MDP, "url": url})
        assert sc.lire(p, attendu="https://cloud.autre.org") is None
        assert sc.lire(self._depot(tmp_path, url=None),
                       attendu="https://cloud.autre.org") is None

    def test_sans_attendu_le_contrat_d_avant_reste(self, tmp_path):
        assert sc.lire(self._depot(tmp_path, url=None)) == ("Ana", self.MDP)


class TestEffacer:
    def test_efface_apres_emploi(self, tmp_path):
        p = _ecrire(tmp_path, {"login": "a", "app_password": "b"})
        sc.effacer(p)
        assert not p.exists()

    def test_absent_ne_leve_pas(self, tmp_path):
        sc.effacer(tmp_path / "rien.json")  # ne doit rien lever
