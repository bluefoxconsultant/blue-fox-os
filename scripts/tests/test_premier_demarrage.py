"""Garde-fous sur l'assistant du PREMIER DEMARRAGE (releve le 2026-09-13).

Six symptomes rapportes sur l'essai en VM — accueil KDE, mode sombre qui ne
prend pas, fond d'ecran errone, applications absentes — ont ete attribues a
`plasma-welcome`, retire de l'image le 2026-09-11. Mesure sur l'image publiee
(rev. 9d81537, montee par `podman image mount`), le retrait avait parfaitement
pris : ni binaire, ni `.desktop`, ni `kded_plasma_welcome.so`, ni entree au
rpmdb.

L'assistant qui s'ouvrait est `plasma-setup`, un AUTRE paquet :

    Description=Plasma Setup - Out-of-Box / First-Run setup wizard
    Before=display-manager.service
    ConditionPathExists=!/etc/plasma-setup-done

Il tourne AVANT le gestionnaire de connexion, et rien dans le retrait de
plasma-welcome ne pouvait le toucher. Deux paquets voisins par le nom, opposes
par le moment ou ils tournent : c'est exactement le genre de confusion qui se
refait, d'ou des tests plutot qu'un commentaire.

Le drapeau est le mecanisme documente EN AMONT, donc verifiable — l'inverse du
raisonnement qui avait fait retirer un paquet faute de pouvoir deviner
l'identifiant d'un module KDED.

Stdlib seulement (lane `test-wizard`), comme test_provenance.py.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
DRAPEAU = REPO_ROOT / "files" / "etc" / "plasma-setup-done"

CHEMIN_DRAPEAU = "/etc/plasma-setup-done"
CONDITION = "ConditionPathExists=!/etc/plasma-setup-done"
UNITE = "/usr/lib/systemd/system/plasma-setup.service"


def _recipes_locataires() -> list[Path]:
    """Les recipes qui construisent vraiment une image.

    `_base.yml` est de la documentation a dupliquer (BlueBuild ne sait pas
    heriter) : il n'a pas de module `script`, donc pas d'assertion a porter.
    """
    trouvees = sorted(p for p in RECIPES_DIR.glob("*.yml")
                      if not p.name.startswith("_"))
    assert trouvees, "aucune recipe locataire trouvee"
    return trouvees


def test_le_drapeau_est_livre_par_le_module_files():
    assert DRAPEAU.is_file(), (
        "files/etc/plasma-setup-done manquant : sans lui, plasma-setup ouvre "
        "son assistant avant SDDM a chaque premier demarrage."
    )


def test_chaque_recipe_affirme_le_drapeau():
    for recipe in _recipes_locataires():
        texte = recipe.read_text()
        assert CHEMIN_DRAPEAU in texte, (
            f"{recipe.name} ne verifie pas {CHEMIN_DRAPEAU} au build : "
            "l'absence du drapeau ne couterait pas un build, elle couterait "
            "une installation."
        )


def test_chaque_recipe_verifie_aussi_la_condition_amont():
    """La moitie qui manque d'habitude.

    Affirmer que le drapeau existe ne prouve rien si l'unite cesse de le lire.
    `plasma-setup` vient de KDE : le jour ou la condition change de chemin, le
    drapeau reste en place, le build reste vert, et l'assistant revient. On
    affirme donc les deux moities.
    """
    for recipe in _recipes_locataires():
        texte = recipe.read_text()
        assert CONDITION in texte, (
            f"{recipe.name} n'affirme pas la condition amont ({CONDITION}) : "
            "une version future de plasma-setup pourrait deplacer son drapeau "
            "sans qu'aucun build ne rougisse."
        )
        assert UNITE in texte, (
            f"{recipe.name} : l'assertion doit nommer l'unite {UNITE}, sinon "
            "elle ne sait pas sur quoi tomber quand le paquet disparait."
        )


def test_plasma_welcome_reste_retire():
    """Le retrait du 2026-09-11 etait bon, il ne doit pas etre defait au
    passage sous pretexte qu'il n'expliquait pas les symptomes."""
    for recipe in _recipes_locataires():
        texte = recipe.read_text()
        assert "- plasma-welcome\n" in texte, (
            f"{recipe.name} ne retire plus plasma-welcome : il rouvrirait sa "
            "visite guidee dans la session et reecrirait le theme."
        )
