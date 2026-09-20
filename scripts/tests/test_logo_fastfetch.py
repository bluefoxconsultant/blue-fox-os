"""Le renard en couleurs dans le terminal (#25854).

Trois choses doivent rester vraies : le fichier existe et garde sa forme, la
construction le depose dans l'image, et la config fastfetch le pointe avec sa
taille declaree — sans quoi le bloc d'information se decale.
"""
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

ANSI = REPO / "branding" / "bluefoxos.ansi"
SGR = re.compile(r"\x1b\[[0-9;]*m")


def _lignes():
    return ANSI.read_text(encoding="utf-8").rstrip("\n").split("\n")


def test_le_logo_existe_et_tient_dans_un_terminal():
    """20 lignes, pas plus : au-dela, le bloc d'information de fastfetch sort
    d'un terminal de 24 lignes et le haut du logo defile."""
    lignes = _lignes()
    assert len(lignes) <= 20
    nues = [SGR.sub("", l) for l in lignes]
    assert 20 <= max(len(l) for l in nues) <= 44
    # Des demi-blocs, rien d'autre : un glyphe absent de la police de console
    # s'afficherait en losange a point d'interrogation.
    assert set("".join(nues).replace(" ", "")) <= set("▀▄█")


def test_le_logo_est_en_couleurs_vraies():
    couleurs = set(re.findall(r"\x1b\[(?:38|48);2;(\d+;\d+;\d+)m", ANSI.read_text()))
    assert len(couleurs) >= 4
    assert len(couleurs) <= 8  # une palette, pas une photo


def test_la_construction_depose_le_logo_dans_l_image():
    corps = (REPO / "scripts" / "build_branded_iso.sh").read_text()
    assert 'branding/bluefoxos.ansi' in corps
    assert 'share/bluefox/branding/bluefoxos.ansi' in corps
    # Absent, la construction continue : un logo manquant ne casse pas une image.
    assert 'fastfetch gardera le logo de Fedora' in corps


def test_la_config_fastfetch_pointe_le_logo_avec_sa_taille(tmp_path):
    """⚠️ La taille est MESUREE sur le dessin, pas declaree : une constante en
    dur se desynchronise a la premiere retouche du logo, et fastfetch decale
    alors tout le bloc d'information sans rien dire."""
    import generate_kde_theme as g
    marque = tmp_path / "usr/share/bluefox/branding"
    marque.mkdir(parents=True)
    (marque / "bluefoxos.ansi").write_bytes(ANSI.read_bytes())

    b = g.resolve_branding(json.loads((REPO / "config" / "bf.json").read_text()))
    cible = g.emit_fastfetch_config(b, tmp_path)
    assert cible == tmp_path / "etc/skel/.config/fastfetch/config.jsonc"
    conf = json.loads(cible.read_text())
    assert conf["logo"]["source"] == "/usr/share/bluefox/branding/bluefoxos.ansi"
    assert conf["logo"]["type"] == "file-raw"
    lignes = _lignes()
    assert conf["logo"]["height"] == len(lignes)
    assert conf["logo"]["width"] == max(len(SGR.sub("", l)) for l in lignes)
    assert any(m.get("format") == b["name"] for m in conf["modules"]
               if isinstance(m, dict))


def test_fastfetch_est_installe_dans_chaque_image():
    """🔴 Mesure du 2026-09-20 dans l'image publiee : ni fastfetch ni neofetch
    n'etaient la. On emettait une config pour un programme absent.

    Lecture textuelle plutot que YAML : cette lane tourne sans dependances, et
    un paquet declare est une ligne de liste, pas une structure a interpreter.
    """
    for nom in ("bf.yml", "bf-surface.yml", "factice.yml"):
        lignes = (REPO / "recipes" / nom).read_text().splitlines()
        declare = [l.strip() for l in lignes if l.strip() == "- fastfetch"]
        assert declare, f"{nom} n'installe pas fastfetch"
