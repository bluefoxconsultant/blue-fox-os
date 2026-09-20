"""Photo de l'usager tiree d'Odoo (#25854)."""
import base64

from bluefox_welcome.apply.photo import appliquer_photo, photo_de_la_politique

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
JPEG = (b"\xff\xd8\xff" + b"\x00" * 64)


def _politique(avatar):
    return {"user": {"login": "olivier@bluefoxconsultant.com", "avatar": avatar}}


def test_une_photo_png_est_lue():
    assert photo_de_la_politique(_politique(base64.b64encode(PNG).decode())) == PNG


def test_une_photo_jpeg_aussi():
    assert photo_de_la_politique(_politique(base64.b64encode(JPEG).decode())) == JPEG


def test_sans_photo_on_ne_touche_a_rien(tmp_path):
    cible = tmp_path / ".face.icon"
    ok, msg = appliquer_photo({"user": {}}, visage=cible, poser=lambda p: True)
    assert not ok and "aucune photo" in msg
    assert not cible.exists()


def test_ce_qui_n_est_ni_png_ni_jpeg_est_refuse(tmp_path):
    """Un SVG ou du texte s'ecrirait sans bruit et ne s'afficherait nulle part."""
    cible = tmp_path / ".face.icon"
    ok, _ = appliquer_photo(_politique(base64.b64encode(b"<svg/>").decode()),
                            visage=cible, poser=lambda p: True)
    assert not ok and not cible.exists()


def test_du_base64_casse_ne_fait_pas_tomber_l_agent():
    assert photo_de_la_politique(_politique("pas du base64 !!")) is None


def test_la_photo_est_ecrite_et_donnee_a_accountsservice(tmp_path):
    cible = tmp_path / ".face.icon"
    vus = []
    ok, msg = appliquer_photo(_politique(base64.b64encode(PNG).decode()),
                              visage=cible, poser=lambda p: vus.append(p) or True)
    assert ok, msg
    assert cible.read_bytes() == PNG
    assert vus == [cible]
    assert "AccountsService:ok" in msg
    # 0644 : l'ecran de connexion la lit sans etre l'usager.
    assert oct(cible.stat().st_mode)[-3:] == "644"
