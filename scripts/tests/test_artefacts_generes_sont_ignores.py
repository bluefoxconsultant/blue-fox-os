"""Tout ce que la generation ecrit sous files/ doit etre ignore par git.

🔴 DEUXIEME OCCURRENCE, 2026-09-21. Le meme defaut, une generation plus tard :
`emit_plasmalogin_config` s'est mis a ecrire AUSSI `files/etc/plasmalogin.conf`
(le drop-in etant ignore par le gestionnaire de connexion), `.gitignore` ne
couvrait pas ce chemin-la, et les TROIS locataires ont ete refuses par le
preflight de `publish-image.sh` — qui refuse a juste titre de publier depuis un
arbre sale, puisqu'il ne pourrait pas rattacher l'image a un commit relisible.

⚠️ Un garde-fou existait deja pour ce piege, dans `scripts/test_generate_kde_theme.py`.
Il n'a rien attrape : ce fichier n'est PAS dans la lane de CI (il porte trois
echecs connus, et la lane cible `scripts/tests/`). Un garde qu'on ne joue pas
n'est pas un garde — d'ou cette copie, ici, dans la lane.

On interroge `git check-ignore`, qui fait autorite, plutot que de reimplementer
les motifs de .gitignore : une reimplementation se trompe exactement la ou les
motifs sont subtils.
"""
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def test_chaque_fichier_emis_est_ignore(tmp_path):
    pytest.importorskip("PIL")
    import generate_kde_theme as g

    # Les sources de marque que la construction depose avant la generation.
    marque = tmp_path / "usr/share/bluefox/branding"
    marque.mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (1920, 1080), (20, 30, 40)).save(marque / "wallpaper.jpg")
    Image.new("RGB", (1672, 941), (10, 10, 12)).save(marque / "login-wallpaper.png")
    (marque / "bluefoxos.ansi").write_text("\x1b[38;2;0;47;104m█\x1b[0m\n")

    g.emit_all({"slug": "bf", "branding": {}}, tmp_path)

    emis = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert emis, "la generation n'a rien ecrit : le test ne prouverait rien"

    chemins = [f"files/{p}" for p in emis]
    r = subprocess.run(["git", "check-ignore", "--no-index", *chemins],
                       cwd=REPO, capture_output=True, text=True)
    ignores = set(r.stdout.split())
    oublies = [c for c in chemins if c not in ignores]
    assert not oublies, (
        "ces artefacts generes ne sont pas dans .gitignore ; ils saliront "
        f"l'arbre et feront refuser la publication : {oublies}"
    )
