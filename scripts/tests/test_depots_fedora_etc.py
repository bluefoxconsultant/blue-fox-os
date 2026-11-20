"""Depots Fedora visibles a bootc-image-builder (#23941, 2026-11-20).

Fedora 45 range ses depots dans /usr/share/dnf5/repos.d ; bootc-image-builder ne
lit que /etc/yum.repos.d et n'y trouvait plus [fedora] : aucune ISO ne se
batissait. files/scripts/depots-fedora-dans-etc.sh les recopie ; ces essais
gardent le script ET son appel dans chaque recipe tenant.
"""
import pathlib
import subprocess

import pytest

RACINE = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = RACINE / "files" / "scripts" / "depots-fedora-dans-etc.sh"
RECIPES_TENANT = sorted(p for p in (RACINE / "recipes").glob("*.yml")
                        if not p.name.startswith("_"))

DEPOT_FEDORA = "[fedora]\nname=Fedora $releasever\nenabled=1\n"
DEPOT_MAJ = "[updates]\nname=Fedora $releasever - Updates\nenabled=1\n"


def _lancer(source, cible):
    env = {"BFOS_DEPOTS_SOURCE": str(source), "BFOS_DEPOTS_CIBLE": str(cible),
           "PATH": "/usr/bin:/bin"}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True)


def test_il_y_a_des_recipes_tenant():
    # Un glob vide rendrait le test suivant vert sans rien verifier.
    assert len(RECIPES_TENANT) >= 3, RECIPES_TENANT


@pytest.mark.parametrize("recipe", RECIPES_TENANT, ids=lambda p: p.name)
def test_chaque_recipe_appelle_le_script(recipe):
    # Bibliotheque standard seulement : le job CI « stdlib-only » n'a pas yaml.
    # On exige une ligne de snippet (hors commentaire) qui EXECUTE le script.
    # ⚠️ La commande exacte, pas le nom seul : la ligne nomme aussi le script
    # dans son test d'existence et dans son message d'erreur, et un premier
    # jet qui cherchait le nom « quelque part sur une ligne avec exec bash »
    # restait vert quand on remplacait la cible de l'exec (temoin du 2026-11-20).
    attendu = 'exec bash "$D/depots-fedora-dans-etc.sh"'
    lignes = [l for l in recipe.read_text().splitlines()
              if not l.lstrip().startswith("#")]
    assert any(attendu in l for l in lignes), recipe.name


def test_fedora_45_copie_les_depots_fournisseur(tmp_path):
    source, cible = tmp_path / "fournisseur", tmp_path / "etc"
    source.mkdir()
    (source / "fedora.repo").write_text(DEPOT_FEDORA)
    (source / "fedora-updates.repo").write_text(DEPOT_MAJ)
    r = _lancer(source, cible)
    assert r.returncode == 0, r.stderr
    assert (cible / "fedora.repo").read_text() == DEPOT_FEDORA
    assert (cible / "fedora-updates.repo").read_text() == DEPOT_MAJ
    assert oct((cible / "fedora.repo").stat().st_mode & 0o777) == "0o644"
    assert "2 depot(s) copie(s)" in r.stdout


def test_fedora_43_sans_repertoire_fournisseur_passe(tmp_path):
    cible = tmp_path / "etc"
    cible.mkdir()
    (cible / "fedora.repo").write_text(DEPOT_FEDORA)
    r = _lancer(tmp_path / "absent", cible)
    assert r.returncode == 0, r.stderr
    assert "rien a copier" in r.stdout


def test_un_fichier_deja_dans_etc_n_est_jamais_ecrase(tmp_path):
    source, cible = tmp_path / "fournisseur", tmp_path / "etc"
    source.mkdir()
    cible.mkdir()
    (source / "fedora.repo").write_text(DEPOT_FEDORA)
    (cible / "fedora.repo").write_text(DEPOT_FEDORA + "# local\n")
    r = _lancer(source, cible)
    assert r.returncode == 0, r.stderr
    assert (cible / "fedora.repo").read_text().endswith("# local\n")
    assert "garde tel quel" in r.stdout


def test_aucun_depot_fedora_fait_echouer_la_construction(tmp_path):
    # Le cas mesure le 2026-11-20 : /etc ne porte que des depots tiers.
    source, cible = tmp_path / "fournisseur", tmp_path / "etc"
    source.mkdir()
    cible.mkdir()
    (cible / "tailscale.repo").write_text("[tailscale-stable]\nenabled=1\n")
    r = _lancer(source, cible)
    assert r.returncode == 1
    assert "aucun depot [fedora]" in r.stderr
