"""Garde-fous sur le pense-bête Symbifox posé dans l'image (BF #27250).

Trois choses peuvent se défaire en silence, et chacune donne un widget inutile
sur tous les postes sans qu'une construction devienne rouge :

1. le paquet du plasmoïde disparaît de l'arbre (un `files/` réorganisé) ;
2. le widget cesse d'être semé sur le bureau du premier ouvrant ;
3. il est semé SANS adresse, parce que `resolve_branding()` ne garde que la
   marque et qu'une fonction qui lit `b["services"]` rend alors un dictionnaire
   vide. C'est le piège précis qui a été corrigé avant la pose.

D'où des tests, et pas une consigne.

Stdlib seulement (lane `test-wizard`) : on lit les fichiers, on ne parse pas de
YAML.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLASMOID = (REPO_ROOT / "files/usr/share/plasma/plasmoids"
            / "com.symbifox.stickynotes")
GENERATEUR = REPO_ROOT / "scripts" / "generate_kde_theme.py"
PLUGIN_ID = "com.symbifox.stickynotes"


# ── Le paquet est là, et complet ─────────────────────────────────────────────

def test_le_paquet_du_plasmoide_est_dans_l_image():
    assert PLASMOID.is_dir(), (
        f"{PLASMOID} manque : aucun poste n'aurait le pense-bête, et rien "
        "d'autre ne le signalerait.")


@pytest.mark.parametrize("relatif", [
    "metadata.json",
    "contents/ui/main.qml",
    "contents/ui/CompactRepresentation.qml",
    "contents/ui/FullRepresentation.qml",
    "contents/ui/NoteCard.qml",
    "contents/code/api.js",
    "contents/code/store.js",
    "contents/config/main.xml",
    "contents/config/config.qml",
    "contents/ui/ConfigGeneral.qml",
])
def test_chaque_fichier_du_plasmoide_est_present(relatif):
    """Plasma charge un plasmoïde incomplet SANS erreur : l'applet paraît
    vide, ou pas du tout. Nommer les fichiers un par un est donc la seule
    façon d'en voir la perte."""
    chemin = PLASMOID / relatif
    assert chemin.is_file(), f"{relatif} manque au paquet du plasmoïde"


def test_l_identite_du_plasmoide_correspond_a_ce_qui_est_seme():
    """🔴 L'identifiant du `metadata.json` et le `plugin=` semé dans
    l'appletsrc doivent être LE MÊME mot. S'ils divergent, Plasma cherche un
    plasmoïde qui n'existe pas : le bureau s'ouvre sans widget, sans un mot."""
    meta = json.loads((PLASMOID / "metadata.json").read_text())
    assert meta["KPlugin"]["Id"] == PLUGIN_ID
    assert f"plugin={PLUGIN_ID}" in GENERATEUR.read_text(), (
        "le générateur ne sème pas ce plasmoïde sous son propre identifiant")


def test_le_plasmoide_declare_plasma_6():
    meta = json.loads((PLASMOID / "metadata.json").read_text())
    assert meta.get("KPackageStructure") == "Plasma/Applet"
    assert meta.get("X-Plasma-API-Minimum-Version", "").startswith("6"), (
        "Kinoite porte Plasma 6 : un paquet qui ne le déclare pas est ignoré")


def test_le_jeton_n_est_jamais_seme_dans_l_image():
    """🔴 Le jeton d'appairage est PAR PERSONNE et PAR POSTE. Un jeton semé
    dans `/etc/skel` serait partagé par tous les comptes de tous les postes
    construits depuis cette image."""
    texte = GENERATEUR.read_text()
    assert "token=" not in texte, (
        "le générateur sème un jeton : l'appairage doit rester le seul chemin")


# ── Le widget est semé, avec l'adresse du locataire ──────────────────────────

def _genere(tenant: dict, tmp_path: Path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import generate_kde_theme as gen

    return gen.emit_all(tenant, tmp_path)


def _tenant(services: dict | None = None) -> dict:
    tenant = {
        "slug": "essai",
        "name": "Essai",
        "branding": {"palette": {"primary": "#29ABE1", "secondary": "#2D3031"}},
    }
    if services is not None:
        tenant["services"] = services
    return tenant


def test_le_widget_est_seme_avec_l_adresse_du_locataire(tmp_path):
    _genere(_tenant({"odoo": {"url": "https://exemple.invalid"}}), tmp_path)
    appletsrc = (tmp_path / "etc/skel/.config"
                 / "plasma-org.kde.plasma.desktop-appletsrc").read_text()
    assert f"plugin={PLUGIN_ID}" in appletsrc
    assert "server=https://exemple.invalid" in appletsrc, (
        "sans l'adresse, le pense-bête est muet sur chaque poste")


def test_le_fond_d_ecran_reste_seme(tmp_path):
    """Ce que le fichier faisait AVANT #27250 doit continuer : c'est la seule
    raison pour laquelle il existe."""
    _genere(_tenant({"odoo": {"url": "https://exemple.invalid"}}), tmp_path)
    appletsrc = (tmp_path / "etc/skel/.config"
                 / "plasma-org.kde.plasma.desktop-appletsrc").read_text()
    assert "[Containments][1][Wallpaper][org.kde.image][General]" in appletsrc
    assert "wallpaper.jpg" in appletsrc


def test_un_odoo_declare_sans_adresse_fait_echouer_la_generation(tmp_path):
    """🔴 Le piège exact à ne pas laisser revenir.

    `resolve_branding()` ne garde que la marque : une fonction qui lirait
    l'adresse dans son résultat obtiendrait une chaîne vide et sèmerait un
    widget sans serveur, sans un mot au journal de construction. Un locataire
    qui DÉCLARE un Odoo doit donc en donner l'adresse, ou la construction
    s'arrête.
    """
    with pytest.raises(ValueError, match="services.odoo"):
        _genere(_tenant({"odoo": {}}), tmp_path)

    with pytest.raises(ValueError, match="services.odoo"):
        _genere(_tenant({"odoo": {"url": ""}}), tmp_path)


def test_un_locataire_sans_odoo_du_tout_est_seme_sans_widget(tmp_path):
    """`factice` n'a pas d'Odoo, donc pas de bloc-notes : rien à semer.

    ⚠️ Et surtout, sa construction ne doit PAS échouer — `make verify-all`
    porte sur les trois locataires.
    """
    _genere(_tenant({"nextcloud": {"url": "https://nc.invalid"}}), tmp_path)
    appletsrc = (tmp_path / "etc/skel/.config"
                 / "plasma-org.kde.plasma.desktop-appletsrc").read_text()
    assert PLUGIN_ID not in appletsrc
    # Le reste du fichier tient toujours.
    assert "[Containments][1][Wallpaper][org.kde.image][General]" in appletsrc


def test_chaque_locataire_du_depot_se_genere(tmp_path):
    """Ce qui précède ne sert à rien si un locataire livré ne passe pas.

    Celui qui a un Odoo doit semer le widget sur son adresse ; celui qui n'en
    a pas doit se générer quand même.
    """
    for config in sorted((REPO_ROOT / "config").glob("*.json")):
        if config.name.startswith("_") or config.name == "schema.v1.json":
            continue
        tenant = json.loads(config.read_text())
        cible = tmp_path / config.stem
        _genere(tenant, cible)
        appletsrc = (cible / "etc/skel/.config"
                     / "plasma-org.kde.plasma.desktop-appletsrc").read_text()
        url = ((tenant.get("services") or {}).get("odoo") or {}).get("url")
        if url:
            assert f"server={url}" in appletsrc, config.name
        else:
            assert PLUGIN_ID not in appletsrc, config.name
