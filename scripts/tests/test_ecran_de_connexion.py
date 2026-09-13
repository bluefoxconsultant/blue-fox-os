"""Garde-fous sur l'ecran de connexion (releve le 2026-09-13).

Onze jours durant, la marque de l'ecran de connexion a ete reglee dans un
systeme qui n'existe pas sur la machine. Mesure sur l'image publiee :

    /etc/systemd/system/display-manager.service -> plasmalogin.service
    /usr/bin/sddm                                  ABSENT
    paquets sddm                                   AUCUN

Fedora 44 / Plasma 6.7 ont remplace SDDM par plasma-login-manager. Tout ce que
`generate_kde_theme.py` produisait pour SDDM — un fichier dans
`/etc/sddm.conf.d/`, un theme par locataire, une surcharge `theme.conf.user` —
n'etait lu par personne. Pire : `/usr/share/sddm/themes/` n'appartenait a AUCUN
paquet, il existait parce que notre propre script le creait. Un arbre entier qui
donnait l'illusion d'un ecran de connexion brande.

⚠️ La surcharge `breeze/theme.conf.user` avait ete ecrite le 2026-09-02 pour
corriger « l'ecran de connexion affiche le fond Breeze d'origine ». Le fond
n'etait pas celui de Breeze, c'etait celui de FEDORA, servi par
`/usr/lib/plasmalogin/defaults.conf`. Un diagnostic qui nomme le mauvais
coupable produit un correctif qui ne corrige rien, et qui a l'air d'un
correctif.

Stdlib seulement (lane `test-wizard`), comme test_provenance.py.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
GENERATEUR = REPO_ROOT / "scripts" / "generate_kde_theme.py"

DROP_IN = "/etc/plasmalogin.conf.d/10-bluefox.conf"
GREETER = "plasmalogin"


def _recipes_locataires() -> list[Path]:
    trouvees = sorted(p for p in RECIPES_DIR.glob("*.yml")
                      if not p.name.startswith("_"))
    assert trouvees, "aucune recipe locataire trouvee"
    return trouvees


def _generateur() -> str:
    return GENERATEUR.read_text()


def _code_seul(corps: str) -> str:
    """Le CODE seul, commentaires et docstrings retires.

    ⚠️ Ce fichier-ci et le generateur CITENT tous deux SDDM, longuement, pour
    dire pourquoi il n'y est plus. Une recherche naive prend la documentation
    pour du code et echoue sur l'explication elle-meme — c'est arrive a la
    premiere version de ce test.

    ⚠️ Et on ne peut pas simplement jeter toutes les chaines : d'autres tests
    ici verifient des litteraux du generateur. On retire donc precisement les
    docstrings, par l'arbre syntaxique, et les lignes de commentaire.
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


def test_le_generateur_n_ecrit_plus_rien_pour_sddm():
    code = _code_seul(_generateur())
    for mort in ("emit_sddm_theme", "emit_sddm_config", "emit_sddm_breeze_override",
                 "etc/sddm.conf.d", "usr/share/sddm"):
        assert mort not in code, (
            f"{mort} est encore produit : ces fichiers ne sont lus par personne, "
            "et ils font croire que l'ecran de connexion est brande"
        )


def test_le_generateur_ecrit_le_drop_in_du_vrai_greeter():
    code = _code_seul(_generateur())
    assert "etc/plasmalogin.conf.d" in code, (
        "aucun reglage n'est produit pour plasmalogin : l'ecran de connexion "
        "sert alors le papier peint de Fedora"
    )


def test_le_drop_in_copie_la_forme_des_cles_amont():
    """Ne pas inventer des cles plausibles — c'est ce qui a coute onze jours.

    `/usr/lib/plasmalogin/defaults.conf` donne la forme exacte :

        [Greeter]
        WallpaperPlugin=org.kde.image

        [Greeter][Wallpaper][org.kde.image][General]
        Image=file:///usr/share/wallpapers/Fedora/
    """
    code = _code_seul(_generateur())
    for cle in ("WallpaperPlugin=org.kde.image",
                "[Greeter][Wallpaper][org.kde.image][General]",
                "Image={paquet}",
                "PreviewImage={paquet}"):
        assert cle in code, (
            f"la cle {cle!r} manque : le drop-in doit reprendre la forme de "
            "defaults.conf, pas une forme plausible"
        )


def test_le_papier_peint_vise_un_paquet_kde_pas_un_fichier_nu():
    """Fedora pointe sur un REPERTOIRE de paquet de papier peint. On emet le
    notre dans emit_wallpaper_package() ; viser le .jpg nu marcherait
    peut-etre, mais s'ecarterait de la forme qu'on sait bonne."""
    code = _code_seul(_generateur())
    assert 'f"file:///usr/share/wallpapers/{b[\'slug\']}/"' in code, (
        "le papier peint du greeter doit designer le paquet KDE du locataire"
    )


def test_chaque_recipe_affirme_le_greeter_ET_le_drop_in():
    """Les deux moities, comme pour plasma-setup.

    Poser le drop-in sans verifier QUI lit ne prouve rien : c'est exactement
    l'etat d'avant, ou un fichier parfaitement forme attendait un lecteur qui
    n'existait pas.
    """
    for recipe in _recipes_locataires():
        texte = recipe.read_text()
        assert DROP_IN in texte, (
            f"{recipe.name} ne verifie pas la presence de {DROP_IN}"
        )
        assert "display-manager.service" in texte, (
            f"{recipe.name} ne verifie pas QUI rend l'ecran de connexion : un "
            "changement de greeter chez Fedora repasserait au fond Fedora sans "
            "un mot"
        )
        assert GREETER in texte, (
            f"{recipe.name} : l'assertion doit nommer le gestionnaire attendu"
        )
