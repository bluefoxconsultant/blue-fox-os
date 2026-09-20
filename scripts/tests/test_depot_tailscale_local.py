"""Le depot Tailscale est versionne, pas telecharge a la construction.

2026-09-14 : pkgs.tailscale.com coupait ~1 connexion TLS sur 4 depuis la
machine de construction. Le module rpm-ostree tirait le .repo par
`curl --retry 5`, mais --retry ne reessaie pas une erreur TLS (code 35).
Une coupure tuait la construction entiere — bf, bf-surface et factice tour a
tour. Ces tests empechent de revenir a l'URL.
"""
import configparser
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[2]
RECETTES = ("bf.yml", "bf-surface.yml", "factice.yml")
FICHIER = RACINE / "files" / "rpm-ostree" / "tailscale.repo"


def test_aucune_recette_ne_telecharge_le_repo_tailscale():
    for nom in RECETTES:
        texte = (RACINE / "recipes" / nom).read_text()
        assert "pkgs.tailscale.com/stable/fedora/tailscale.repo" not in texte, \
            f"{nom} retelecharge le .repo : une coupure TLS tuera la construction"


def test_chaque_recette_utilise_le_fichier_local():
    for nom in RECETTES:
        lignes = (RACINE / "recipes" / nom).read_text().splitlines()
        assert any(l.strip() == "- tailscale.repo" for l in lignes), nom


def test_le_fichier_existe_la_ou_le_module_le_cherche():
    """Le module copie depuis $CONFIG_DIRECTORY/rpm-ostree/, qui vaut
    /tmp/files/rpm-ostree/ dans cette version de BlueBuild, soit
    files/rpm-ostree/ dans le depot. Absent, rpm-ostree ne trouverait pas le
    paquet tailscale : l'echec serait bruyant, mais autant l'attraper ici."""
    assert FICHIER.is_file()


def test_la_verification_gpg_reste_entiere():
    """Versionner le .repo ne doit pas affaiblir la verification des paquets."""
    cp = configparser.ConfigParser()
    cp.read(FICHIER)
    section = cp["tailscale-stable"]
    assert section["gpgcheck"] == "1"
    assert section["repo_gpgcheck"] == "1"
    assert section["gpgkey"].startswith("https://pkgs.tailscale.com/")
    assert section["baseurl"].startswith("https://")
