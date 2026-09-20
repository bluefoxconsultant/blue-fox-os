"""Les icones de la barre suivent le theme sombre (#25854).

🔴 Mesure du 2026-09-20 dans l'image publiee : notre theme heritait de
`breeze`, la variante CLAIRE, alors que le schema de couleurs du locataire est
sombre. Les icones monochromes du systeme de notification sortaient donc
sombres sur un panneau sombre.
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def _emettre(tmp_path):
    import generate_kde_theme as g
    b = g.resolve_branding(json.loads((REPO / "config" / "bf.json").read_text()))
    return g, b, g.emit_icon_theme(b, tmp_path)


def test_le_theme_herite_de_breeze_dark_en_premier(tmp_path):
    _, _, base = _emettre(tmp_path)
    index = (base / "index.theme").read_text()
    herite = next(l for l in index.splitlines() if l.startswith("Inherits="))
    familles = herite.split("=", 1)[1].split(",")
    assert familles[0] == "breeze-dark", herite
    # La variante claire reste derriere : une icone absente de la sombre s'y
    # resout encore plutot que de tomber sur le carre de hicolor.
    assert "breeze" in familles and familles[-1] == "hicolor"


def test_le_schema_de_couleurs_est_bien_sombre(tmp_path):
    """L'heritage sombre n'a de sens que si le fond l'est : si le schema
    devenait clair un jour, ce test le dirait avant que les icones ne
    disparaissent a nouveau."""
    g, b, _ = _emettre(tmp_path)
    scheme = g.emit_color_scheme(b, tmp_path).read_text()
    fond = next(l for l in scheme.splitlines() if l.startswith("BackgroundNormal="))
    r, v, bl = (int(x) for x in fond.split("=", 1)[1].split(","))
    assert (r + v + bl) / 3 < 96, f"fond trop clair pour des icones claires : {fond}"
