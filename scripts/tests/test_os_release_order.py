"""L'ecriture d'os-release doit precedee le bake dracut, et la sentinelle de
rebase ne doit pas survivre dans l'ISO BIB.

Les deux defauts ont ete trouves en testant l'ISO du 2026-07-25, et ils sont du
meme genre : silencieux, invisibles au build, visibles seulement sur une machine
installee.

1. dracut copie os-release dans l'initramfs sous le nom `initrd-release` —
   c'est la que Plymouth lit le nom du systeme sur l'ecran de deverrouillage
   LUKS. Le sed qui renomme os-release passait APRES le bake : l'initramfs
   embarquait donc PRETTY_NAME="Fedora Linux 43…" et l'ecran LUKS affichait
   « Fedora ». Verifie en extrayant usr/lib/initrd-release de l'initramfs.

2. bf-os.ks pose /var/lib/bluefox-welcome/needs-rebase ; au premier demarrage,
   bluefox-rebase.service rebase vers l'image publiee sur GHCR et redemarre.
   Dans le chemin BIB, l'ISO a DEJA deploye notre image : la machine remplace
   donc l'image fraiche par celle du registre (un build du 2026-05-18 au moment
   du constat), et tout ce qu'on inspecte ensuite appartient a l'image perimee.
"""

import re
from pathlib import Path

# ⚠️ Stdlib uniquement : la lane `test-wizard` de la CI n'installe que pytest.
# Pas de PyYAML — d'ou une comparaison sur l'ordre des lignes du fichier, ce qui
# suffit : chacun des deux snippets n'apparait qu'une fois.
REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER = REPO_ROOT / "scripts" / "render_bib_config.py"
KS = REPO_ROOT / "install" / "bf-os.ks"
BIB_CONFIG = REPO_ROOT / "install" / "bib-config.toml"


def _snippet_line(text: str, prefix: str) -> int | None:
    for i, line in enumerate(text.splitlines()):
        if line.startswith(f"      - {prefix}"):
            return i
    return None


def test_os_release_is_written_before_the_initramfs_is_baked():
    for name in ("bf.yml", "bf-surface.yml", "factice.yml"):
        text = (REPO_ROOT / "recipes" / name).read_text()
        os_rel = _snippet_line(text, ". /usr/lib/os-release")
        dracut = _snippet_line(text, "PLYMOUTH_THEME=")
        if os_rel is None:
            continue  # factice ne rebrande pas os-release
        assert dracut is not None, f"{name} : bake dracut introuvable"
        assert os_rel < dracut, (
            f"{name} : os-release est ecrit APRES le bake dracut — l'initramfs "
            "embarquera « Fedora Linux » et l'ecran LUKS l'affichera"
        )


def test_kickstart_still_creates_the_sentinel_for_the_standalone_path():
    """Le chemin standalone (inst.ks= contre une ISO Kinoite officielle) en a
    besoin : c'est la seule facon d'arriver a l'image BF."""
    assert "touch /var/lib/bluefox-welcome/needs-rebase" in KS.read_text()


def test_rendered_bib_config_strips_the_sentinel():
    """…mais l'ISO BIB ne doit pas la porter."""
    assert BIB_CONFIG.is_file(), "install/bib-config.toml absent — lancer make bib-config"
    body = BIB_CONFIG.read_text()
    for line in body.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "touch /var/lib/bluefox-welcome/needs-rebase" not in line, (
            "la sentinelle survit dans l'ISO BIB : la machine rebasera vers "
            "l'image de GHCR au premier demarrage et perdra celle qu'on installe"
        )
    # Et le mkdir doit rester : le welcome agent ecrit dans ce dossier.
    assert "mkdir -p /var/lib/bluefox-welcome" in body


def test_render_script_fails_loudly_if_the_sentinel_moves():
    body = RENDER.read_text()
    assert "SENTINEL_RE" in body
    assert re.search(r"if n != 1:", body), "un strip silencieux ne prouverait rien"
