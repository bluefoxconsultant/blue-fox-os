"""L'agent d'accueil ne doit etre lance par AUCUNE unite systeme.

firstboot.service le lancait en root avant toute session (retire le
2026-09-14). Ce test lit les recettes et le RPM : si quelqu'un reactive une
unite qui appelle bluefox-welcome, il rougit avant la construction plutot
qu'apres une installation.
"""
import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[2]


def test_aucune_recette_n_active_firstboot_service():
    for recette in (RACINE / "recipes").glob("*.yml"):
        actives = [l for l in recette.read_text().splitlines()
                   if re.match(r"^\s*-\s*firstboot\.service\b", l)]
        assert actives == [], f"{recette.name} reactive firstboot.service"


def test_le_rpm_n_embarque_plus_l_unite():
    spec = (RACINE / "welcome" / "welcome.spec").read_text()
    assert "firstboot.service" not in spec
    assert not (RACINE / "welcome" / "firstboot.service").exists()


def test_aucune_unite_systeme_du_depot_n_appelle_l_agent():
    """On lit ExecStart, pas le fichier entier : la chaine « bluefox-welcome »
    apparait legitimement dans des CHEMINS (/var/lib/bluefox-welcome/) sans
    que l'unite lance l'agent. Un grep sur tout le fichier rougissait donc sur
    bluefox-seat-credentials.service, qui ne fait qu'un chown."""
    for unite in (RACINE / "files").rglob("*.service"):
        lancements = [l for l in unite.read_text().splitlines()
                      if l.strip().startswith(("ExecStart", "ExecStartPre",
                                               "ExecStartPost"))]
        for l in lancements:
            assert "/bluefox-welcome" not in l.split("=", 1)[1].split()[0], \
                f"{unite.relative_to(RACINE)} lance l'agent hors session : {l}"


def test_le_lanceur_de_session_existe_toujours():
    """Retirer l'unite systeme ne doit pas laisser l'agent sans lanceur."""
    trouves = list(RACINE.rglob("bluefox-welcome.desktop"))
    trouves = [t for t in trouves if "output" not in t.parts]
    assert trouves, "plus aucun lanceur /etc/xdg/autostart pour l'agent"
