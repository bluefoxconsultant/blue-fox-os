"""tenant.json doit etre lisible par l'usager dans l'image (#27109, campagne VM).

L'agent de bienvenue tourne en tant que l'usager. Un `cp` du fichier mktemp
(0600) rendait /usr/share/bluefox/tenant.json illisible : les adresses de
services retombaient sur les replis codes en dur.
"""
import pathlib
import re

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "build_branded_iso.sh"


def test_tenant_json_pose_en_0644():
    texte = SCRIPT.read_text(encoding="utf-8")
    lignes = [l for l in texte.splitlines()
              if "share/bluefox/tenant.json" in l and not l.lstrip().startswith("#")]
    assert lignes, "aucune pose de tenant.json trouvee"
    for l in lignes:
        assert re.search(r"\binstall\s+-m\s*0?644\b", l), l
        assert not re.match(r"\s*cp\b", l), l
