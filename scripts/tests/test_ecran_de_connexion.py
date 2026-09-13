"""Garde-fous sur l'ecran de connexion (releve et corrige le 2026-09-13).

NOTRE PARC STRADDLE DEUX GENERATIONS DE GREETER, et c'est le fait central :

    bf, factice   image-version: latest -> Fedora 44 -> plasmalogin
    bf-surface    image-version: "43"   -> Fedora 43 -> sddm

Mesure sur les images publiees, `/etc/systemd/system/display-manager.service` :

    bf-surface -> sddm.service      sddm + sddm-breeze installes, et
                                    /usr/share/sddm/themes/breeze appartient
                                    bien a sddm-breeze-6.7.5-1.fc43
    bf/factice -> plasmalogin.service   /usr/bin/sddm ABSENT, aucun paquet sddm,
                                    et /usr/share/sddm/themes/ n'appartenant a
                                    AUCUN paquet : notre propre script le creait

⚠️ LA PREMIERE LECTURE, LE MEME JOUR, ETAIT FAUSSE et a coute une passe de
publication. En ouvrant l'image `factice` on a conclu « le reglage SDDM est mort
depuis onze jours » et on a retire les emetteurs. C'etait vrai des deux images en
44 et FAUX de celle en 43, ou la surcharge `breeze/theme.conf.user` du
2026-09-02 faisait parfaitement son travail. Le build de bf-surface a rougi sur
l'assertion qui exigeait plasmalogin.

*Conclure « code mort » depuis une seule image, c'est conclure depuis un
echantillon de un.*

D'ou la regle que ces tests gardent : on emet les DEUX configurations, et
l'assertion de build exige celle que l'image cable vraiment, en LISANT
display-manager au lieu de la supposer.

Stdlib seulement (lane `test-wizard`), comme test_provenance.py.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
GENERATEUR = REPO_ROOT / "scripts" / "generate_kde_theme.py"

DROP_IN_PLASMALOGIN = "/etc/plasmalogin.conf.d/10-bluefox.conf"
CONF_SDDM = "/etc/sddm.conf.d/blue-fox.conf"


def _recipes_locataires() -> list[Path]:
    trouvees = sorted(p for p in RECIPES_DIR.glob("*.yml")
                      if not p.name.startswith("_"))
    assert trouvees, "aucune recipe locataire trouvee"
    return trouvees


def _code_seul(corps: str) -> str:
    """Le CODE seul, commentaires et docstrings retires.

    ⚠️ Ce fichier et le generateur CITENT tous deux abondamment SDDM et
    plasmalogin pour expliquer leur cohabitation. Une recherche naive prend la
    documentation pour du code — c'est arrive a la premiere version de ce test.
    On ne peut pas non plus jeter toutes les chaines : d'autres tests ici
    verifient des litteraux. On retire donc precisement les docstrings, par
    l'arbre syntaxique, et les lignes de commentaire.
    """
    arbre = ast.parse(corps)
    lignes = corps.splitlines()
    a_blanchir = set()
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, (ast.Module, ast.ClassDef,
                                  ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        premier = (noeud.body or [None])[0]
        if (isinstance(premier, ast.Expr)
                and isinstance(premier.value, ast.Constant)
                and isinstance(premier.value.value, str)):
            a_blanchir.update(range(premier.lineno, premier.end_lineno + 1))
    return "\n".join(
        "" if (i + 1) in a_blanchir or l.lstrip().startswith("#") else l
        for i, l in enumerate(lignes)
    )


def test_les_deux_generations_de_greeter_sont_servies():
    """Une image en 43 et une image en 44 ne lisent pas le meme fichier."""
    code = _code_seul(GENERATEUR.read_text())
    assert "etc/plasmalogin.conf.d" in code, (
        "rien n'est produit pour plasmalogin : sur Fedora 44 l'ecran de "
        "connexion servirait le papier peint de Fedora"
    )
    assert "etc/sddm.conf.d" in code, (
        "rien n'est produit pour sddm : sur bf-surface, epingle en 43, l'ecran "
        "de connexion perdrait la marque qu'il avait deja"
    )


def test_le_drop_in_plasmalogin_copie_la_forme_des_cles_amont():
    """Ne pas inventer des cles plausibles.

    `/usr/lib/plasmalogin/defaults.conf` donne la forme exacte :

        [Greeter]
        WallpaperPlugin=org.kde.image

        [Greeter][Wallpaper][org.kde.image][General]
        Image=file:///usr/share/wallpapers/Fedora/
    """
    code = _code_seul(GENERATEUR.read_text())
    for cle in ("WallpaperPlugin=org.kde.image",
                "[Greeter][Wallpaper][org.kde.image][General]",
                "Image={paquet}",
                "PreviewImage={paquet}"):
        assert cle in code, (
            f"la cle {cle!r} manque : le drop-in doit reprendre la forme de "
            "defaults.conf, pas une forme plausible"
        )


def test_le_papier_peint_vise_un_paquet_kde_pas_un_fichier_nu():
    """Fedora pointe sur un REPERTOIRE de paquet de papier peint, et on en emet
    un dans emit_wallpaper_package(). Viser le .jpg nu marcherait peut-etre,
    mais s'ecarterait de la forme qu'on sait bonne."""
    code = _code_seul(GENERATEUR.read_text())
    assert 'f"file:///usr/share/wallpapers/{b[\'slug\']}/"' in code, (
        "le papier peint du greeter doit designer le paquet KDE du locataire"
    )


def test_chaque_recipe_exige_la_config_DU_greeter_cable():
    """Le coeur de la correction : lire, pas supposer.

    Exiger plasmalogin partout a fait rougir bf-surface, qui tourne sur sddm.
    Exiger sddm partout laisserait bf et factice sans marque. L'assertion doit
    donc brancher sur ce que `display-manager.service` designe REELLEMENT.
    """
    for recipe in _recipes_locataires():
        texte = recipe.read_text()
        assert "display-manager.service" in texte, (
            f"{recipe.name} ne lit pas display-manager : il supposerait le "
            "greeter au lieu de le constater"
        )
        assert DROP_IN_PLASMALOGIN in texte, f"{recipe.name} : branche plasmalogin absente"
        assert CONF_SDDM in texte, f"{recipe.name} : branche sddm absente"


def test_un_greeter_inconnu_fait_rougir_le_build():
    """Le cas qui compte le jour ou Fedora change encore.

    Sans branche par defaut, un troisieme greeter passerait sans un mot et
    l'ecran de connexion reviendrait au fond de Fedora — exactement le defaut
    qu'on vient de corriger, mais sans personne pour le voir.
    """
    for recipe in _recipes_locataires():
        texte = recipe.read_text()
        assert "gestionnaire de connexion inconnu" in texte, (
            f"{recipe.name} : aucune branche par defaut, un greeter inattendu "
            "serait accepte en silence"
        )
