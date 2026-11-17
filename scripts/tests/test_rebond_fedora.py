"""La Fedora du rebond RPM suit l'image de base DE LA RECETTE BATIE (#27708).

Le RPM bluefox-welcome porte `Requires: python(abi) = <python du container qui
l'a bati>`, et c'est l'image de base qui doit le resoudre a l'install. Quand les
deux Fedora divergent, rpm-ostree refuse la transaction sur « nothing provides
python(abi) = X » — a l'etape 4 sur 7, apres le tirage de la base et seize
etapes de construction. Rien en amont ne compare les deux, donc une balise en
dur en face d'une balise flottante derive EN SILENCE : c'est ce qui est arrive
du 2026-11-09 au 2026-11-15, bf et factice tombees sans qu'aucun controle ne
distingue cette cause d'une autre.

Le piege de la correction, et la raison de ce fichier : deriver du socle PARTAGE
(recipes/_base.yml) parait juste et ne l'est pas. BlueBuild n'herite pas ce
fichier — il est « a dupliquer dans chaque recipe » — et bf-surface est epinglee
`image-version: "43"` pendant que bf et factice suivent `latest` (Fedora 45).
Deriver du socle aurait bati le RPM en fc45 pour bf-surface aussi : la panne
aurait change d'image au lieu d'etre corrigee, et elle serait tombee sur la
seule des trois qui publiait encore.

Stdlib seulement, comme le reste de scripts/tests/.
"""

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPES_DIR = REPO_ROOT / "recipes"
BUILD_RPM = REPO_ROOT / "scripts" / "build-welcome-rpm.sh"
PUBLISH = REPO_ROOT / "scripts" / "publish-image.sh"


def _champ(recipe: Path, cle: str) -> str:
    m = re.search(rf"^{re.escape(cle)}:\s*(\S+)", recipe.read_text(), re.MULTILINE)
    return m.group(1).strip("\"'") if m else ""


def _tenants() -> list[Path]:
    found = sorted(p for p in RECIPES_DIR.glob("*.yml") if not p.name.startswith("_"))
    assert found, "aucune recette tenant trouvee"
    return found


def _dire_socle(slug: str | None):
    """Joue la couture de build-welcome-rpm.sh : aucun reseau, aucun podman."""
    env = dict(os.environ)
    env.pop("BLUEFOX_RPM_RECETTE", None)
    env.pop("BLUEFOX_RPM_FEDORA_TAG", None)
    env["BLUEFOX_RPM_DIRE_SOCLE"] = "1"
    if slug is None:
        env.pop("SLUG", None)
    else:
        env["SLUG"] = slug
    return subprocess.run(
        ["bash", str(BUILD_RPM)], cwd=REPO_ROOT, env=env,
        capture_output=True, text=True,
    )


def _lire(sortie: str) -> dict:
    return dict(re.findall(r"(\w+)=(\S+)", sortie))


def test_la_balise_du_rebond_n_est_pas_codee_en_dur():
    corps = BUILD_RPM.read_text()
    assert 'fedora:${FEDORA_N}' in corps, (
        "le container du rebond doit porter une balise derivee ; une balise en "
        "dur en face d'`image-version: latest` ne peut que deriver"
    )
    assert not re.search(r"^\s+fedora:\d+\s*\\?$", corps, re.MULTILINE), (
        "une balise fedora:<N> en dur subsiste comme argument de podman run"
    )


def test_chaque_recette_derive_sa_propre_image_de_base():
    """L'invariant. C'est lui qui aurait arrete la premiere correction."""
    for recipe in _tenants():
        slug = recipe.stem
        r = _dire_socle(slug)
        assert r.returncode == 0, f"{slug} : refus inattendu\n{r.stderr}"
        vu = _lire(r.stdout)
        assert Path(vu["recette"]).name == recipe.name, (
            f"{slug} : la Fedora du rebond est derivee de {vu['recette']}, "
            f"pas de {recipe.name} — le RPM serait bati pour une autre image"
        )
        assert vu["image"] == _champ(recipe, "base-image"), f"{slug} : base-image divergente"
        assert vu["version"] == _champ(recipe, "image-version"), (
            f"{slug} : balise {vu['version']} alors que la recette demande "
            f"{_champ(recipe, 'image-version')}"
        )


def test_une_recette_epinglee_ne_retombe_pas_sur_le_socle_partage():
    """bf-surface aujourd'hui : epinglee 43 pendant que le socle dit latest.

    Si plus aucune recette ne diverge du socle, le test ne mesure plus rien et
    le dit, plutot que de passer pour la mauvaise raison.
    """
    socle = _champ(RECIPES_DIR / "_base.yml", "image-version")
    divergentes = [r for r in _tenants() if _champ(r, "image-version") != socle]
    assert divergentes, (
        "aucune recette ne diverge du socle : ce test ne discrimine plus rien. "
        "Le garder exige une recette epinglee (bf-surface l'etait en 2026-11)."
    )
    for recipe in divergentes:
        vu = _lire(_dire_socle(recipe.stem).stdout)
        assert vu["version"] == _champ(recipe, "image-version") != socle, (
            f"{recipe.stem} est epinglee mais le rebond demande {vu['version']}"
        )


def test_un_slug_sans_recette_refuse_au_lieu_de_deviner():
    r = _dire_socle("tenant-qui-n-existe-pas")
    assert r.returncode != 0, "un slug inconnu doit refuser, pas retomber sur le socle"
    assert "introuvable" in r.stderr


def test_sans_slug_le_repli_sur_le_socle_est_dit_fort():
    r = _dire_socle(None)
    assert r.returncode == 0
    assert Path(_lire(r.stdout)["recette"]).name == "_base.yml"
    assert "aucun SLUG" in r.stderr, (
        "le repli doit s'annoncer : un silence ici est exactement ce qui a "
        "laisse passer des publications fausses"
    )


def test_publish_passe_le_slug_au_build_du_rpm():
    """SLUG n'est exporte que si l'appelant l'a mis dans l'environnement.

    `publish-image.sh bf-surface` (forme positionnelle, documentee en tete du
    script) laisserait sinon build-welcome-rpm.sh deriver du socle partage.
    """
    corps = PUBLISH.read_text()
    assert 'SLUG="$SLUG" ./scripts/build-welcome-rpm.sh' in corps, (
        "le slug doit etre passe explicitement au build du RPM"
    )


def test_la_derivation_refuse_au_lieu_de_retomber_sur_un_defaut():
    corps = BUILD_RPM.read_text()
    # Quatre refus voulus : socle absent, champs absents, image illisible,
    # temoins qui se contredisent.
    assert corps.count("Refus.") >= 4, "les refus de la derivation ont ete relaches"
    assert "se contredit" in corps, "le desaccord des deux temoins doit refuser"
    assert not re.search(r"FEDORA_N=\$\{[A-Z_]+:-\d+\}", corps), (
        "un numero de Fedora par defaut reste vert et faux : c'est la panne meme"
    )
