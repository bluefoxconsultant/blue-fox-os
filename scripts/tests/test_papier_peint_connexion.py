"""L'ecran de connexion montre le fond de Blue Fox, pas celui de Fedora (#25854).

🔴 Ce qui manquait n'etait pas la configuration : le drop-in
`/etc/plasmalogin.conf.d/10-bluefox.conf` existait dans l'image publiee et
pointait le bon paquet. C'est le NOM du fichier image qui clochait. Le greffon
`org.kde.image` n'enumere que les fichiers nommes par leur resolution —
`5120x2880.jpg` chez Volna, `1920x1080.png` ailleurs. Notre `wallpaper.jpg`
n'etait lu par personne, le paquet passait pour vide, et le greeter retombait
sur le papier peint de la distribution.
"""
import json
import pathlib
import re
import sys

import pytest

Image = pytest.importorskip("PIL.Image")

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

RESOLUTION = re.compile(r"^\d+x\d+\.(jpg|png)$")


def _branding(tmp_path, avec_connexion=True):
    import generate_kde_theme as g
    marque = tmp_path / "usr/share/bluefox/branding"
    marque.mkdir(parents=True)
    Image.new("RGB", (1920, 1080), (20, 30, 40)).save(marque / "wallpaper.jpg")
    if avec_connexion:
        Image.new("RGB", (1672, 941), (10, 10, 12)).save(marque / "login-wallpaper.png")
    return g, g.resolve_branding(json.loads((REPO / "config" / "bf.json").read_text()))


def test_le_paquet_du_bureau_porte_un_nom_que_kde_lit(tmp_path):
    g, b = _branding(tmp_path)
    base = g.emit_wallpaper_package(b, tmp_path)
    images = {p.name for p in (base / "contents/images").iterdir()}
    assert any(RESOLUTION.match(n) for n in images), images
    assert "1920x1080.jpg" in images
    # wallpaper.jpg reste : kscreenlockerrc le designe par son chemin complet.
    assert "wallpaper.jpg" in images


def test_l_ecran_de_connexion_a_son_propre_paquet(tmp_path):
    g, b = _branding(tmp_path)
    g.emit_wallpaper_package(b, tmp_path)
    base = g.emit_login_wallpaper_package(b, tmp_path)
    assert base == tmp_path / "usr/share/wallpapers/bf-login"
    images = {p.name for p in (base / "contents/images").iterdir()}
    assert "1672x941.png" in images
    metadata = json.loads((base / "metadata.json").read_text())
    assert metadata["KPlugin"]["Id"] == "bf-login"


def test_le_drop_in_vise_le_paquet_de_connexion(tmp_path):
    g, b = _branding(tmp_path)
    g.emit_wallpaper_package(b, tmp_path)
    g.emit_login_wallpaper_package(b, tmp_path)
    conf = g.emit_plasmalogin_config(b, tmp_path).read_text()
    assert "Image=file:///usr/share/wallpapers/bf-login/" in conf


def test_sans_fond_dedie_le_drop_in_garde_celui_du_bureau(tmp_path):
    g, b = _branding(tmp_path, avec_connexion=False)
    g.emit_wallpaper_package(b, tmp_path)
    assert g.emit_login_wallpaper_package(b, tmp_path) is None
    conf = g.emit_plasmalogin_config(b, tmp_path).read_text()
    assert "Image=file:///usr/share/wallpapers/bf/" in conf


def test_la_construction_depose_le_fond_de_connexion():
    corps = (REPO / "scripts" / "build_branded_iso.sh").read_text()
    assert "branding/login-wallpaper.png" in corps
    assert "share/bluefox/branding/login-wallpaper.png" in corps


def test_le_papier_peint_est_aussi_dans_le_fichier_principal(tmp_path):
    """🔴 Mesure du 2026-09-21 sur une machine installee : le fond de Fedora
    s'affichait encore, alors que le drop-in etait bien la et pointait le bon
    paquet. Defaut connu en amont — le gestionnaire de connexion ignore le
    papier peint place dans /etc/plasmalogin.conf.d/ et ne lit que le fichier
    principal. On ecrit aux deux endroits."""
    g, b = _branding(tmp_path)
    g.emit_wallpaper_package(b, tmp_path)
    g.emit_login_wallpaper_package(b, tmp_path)
    g.emit_plasmalogin_config(b, tmp_path)
    principal = (tmp_path / "etc/plasmalogin.conf").read_text()
    drop_in = (tmp_path / "etc/plasmalogin.conf.d/10-bluefox.conf").read_text()
    for fichier in (principal, drop_in):
        assert "[Greeter][Wallpaper][org.kde.image][General]" in fichier
        assert "Image=file:///usr/share/wallpapers/bf-login/" in fichier
