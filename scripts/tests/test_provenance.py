"""Garde-fous sur la provenance des images publiees (#23810, audit P5.2 B2).

Le trou d'origine a tenu deux mois sans qu'aucun run rouge ne le signale :
l'image publiee ne portait pas son commit source, et rien n'empechait de
publier depuis un checkout perime. Ces deux manques se re-creusent en silence —
il suffit d'une recipe ajoutee sans son bloc `labels:`, ou d'un controle
d'arbre retire « le temps d'un test ». D'ou des tests, et pas une consigne.

Stdlib seulement (lane `test-wizard`) : on lit les fichiers, on ne parse pas de
YAML.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
PUBLISH = REPO_ROOT / "scripts" / "publish-image.sh"

REVISION_LABEL = "org.opencontainers.image.revision"
EXPECTED_VALUE = '"${BF_GIT_REVISION}"'


def _recipes() -> list[Path]:
    found = sorted(RECIPES_DIR.glob("*.yml"))
    assert found, "aucune recipe trouvee"
    return found


def test_every_recipe_declares_the_revision_label():
    for recipe in _recipes():
        text = recipe.read_text()
        assert f"{REVISION_LABEL}:" in text, (
            f"{recipe.name} ne declare pas {REVISION_LABEL} : une image publiee "
            "depuis cette recipe n'aurait aucun lien verifiable vers son commit."
        )


def test_revision_label_is_injected_not_hardcoded():
    # Un sha en dur vieillit sans bruit : il resterait juste... faux.
    for recipe in _recipes():
        m = re.search(rf"^\s*{re.escape(REVISION_LABEL)}:\s*(.+)$", recipe.read_text(), re.MULTILINE)
        assert m, f"{recipe.name} : etiquette de revision introuvable"
        assert m.group(1).strip() == EXPECTED_VALUE, (
            f"{recipe.name} : la revision doit venir de l'environnement "
            f"({EXPECTED_VALUE}), pas d'une valeur figee"
        )


def test_publish_exports_the_revision():
    body = PUBLISH.read_text()
    assert "BF_GIT_REVISION=\"$(git -C \"$WORKDIR\" rev-parse HEAD)\"" in body
    assert "export BF_GIT_REVISION" in body, (
        "sans export, shellexpand laisse le litteral et l'image publie "
        "« ${BF_GIT_REVISION} » comme revision"
    )


def test_publish_refuses_a_dirty_or_stale_checkout():
    body = PUBLISH.read_text()
    assert "git status --porcelain" in body, "arbre sale non detecte"
    assert "rev-list --count \"HEAD..${UPSTREAM}\"" in body, "retard sur le remote non detecte"
    assert "rev-list --count \"${UPSTREAM}..HEAD\"" in body, "commits non pousses non detectes"
    assert "refus de publier" in body, "les violations doivent bloquer, pas avertir"


def test_unclean_publish_needs_an_explicit_opt_in():
    body = PUBLISH.read_text()
    assert "PUBLISH_ALLOW_UNCLEAN" in body
    # Le contournement doit etiqueter honnetement ce qu'il publie.
    assert '-dirty' in body


def test_publish_reads_back_the_published_label():
    body = PUBLISH.read_text()
    assert "REVISION_LABEL" in body
    assert '[ "$REVISION_LABEL" = "$BF_GIT_REVISION" ]' in body, (
        "l'etiquette doit etre relue apres push : une variable non exportee "
        "produit une image signee et attestee dont la provenance est un litteral"
    )
