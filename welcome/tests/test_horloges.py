"""Format de l'heure et seconde horloge (#25854)."""
import configparser

import pytest

from bluefox_welcome.apply.horloges import (
    appliquer_horloges, horloges_de_la_politique,
)

PANNEAU = """[Containments][1]
plugin=org.kde.plasma.folder

[Containments][2]
formfactor=2
plugin=org.kde.panel

[Containments][2][General]
AppletOrder=3;7;21

[Containments][2][Applets][7]
plugin=org.kde.plasma.kickoff

[Containments][2][Applets][21]
plugin=org.kde.plasma.digitalclock

[Containments][2][Applets][21][Configuration][Appearance]
showDate=true
"""


def _panneau(tmp_path, corps=PANNEAU):
    f = tmp_path / "plasma-org.kde.plasma.desktop-appletsrc"
    f.write_text(corps)
    return f


def _lire(f):
    c = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    c.optionxform = str
    c.read(f, encoding="utf-8")
    return c


def test_sans_politique_c_est_24_heures():
    assert horloges_de_la_politique(None) == {"format": "24h", "second_timezone": None}
    assert horloges_de_la_politique({"session": {}})["format"] == "24h"
    assert horloges_de_la_politique({"session": {"clock": {"format": "n'importe quoi"}}})["format"] == "24h"


def test_douze_heures_quand_la_politique_le_demande():
    p = {"session": {"clock": {"format": "12h"}}}
    assert horloges_de_la_politique(p)["format"] == "12h"


def test_le_format_est_un_nombre_pas_un_booleen(tmp_path):
    """🔴 use24hFormat vaut 0, 1 ou 2 chez Plasma : ecrire « true » laisse la
    locale decider, et l'heure reste comme avant sans que rien ne le dise."""
    f = _panneau(tmp_path)
    ok, msg = appliquer_horloges({"session": {"clock": {"format": "24h"}}},
                                 appletsrc=f, relancer=lambda: True)
    assert ok, msg
    conf = _lire(f)
    assert conf.get("Containments][2][Applets][21][Configuration][Appearance",
                    "use24hFormat") == "1"

    ok, _ = appliquer_horloges({"session": {"clock": {"format": "12h"}}},
                               appletsrc=f, relancer=lambda: True)
    assert _lire(f).get("Containments][2][Applets][21][Configuration][Appearance",
                        "use24hFormat") == "0"


def test_la_seconde_horloge_rejoint_le_panneau(tmp_path):
    f = _panneau(tmp_path)
    ok, msg = appliquer_horloges(
        {"session": {"clock": {"format": "24h", "second_timezone": "America/Montreal"}}},
        appletsrc=f, relancer=lambda: True)
    assert ok, msg
    conf = _lire(f)
    # Un applet neuf, sur le fuseau demande, et le panneau le sait.
    neuf = "Containments][2][Applets][22"
    assert conf.get(neuf, "plugin") == "org.kde.plasma.digitalclock"
    app = f"{neuf}][Configuration][Appearance"
    assert conf.get(app, "selectedTimeZones") == "America/Montreal"
    assert conf.get(app, "displayTimezoneFormat") == "Code"
    assert conf.get("Containments][2][General", "AppletOrder") == "3;7;21;22"


def test_la_seconde_horloge_ne_se_pose_pas_deux_fois(tmp_path):
    f = _panneau(tmp_path)
    politique = {"session": {"clock": {"second_timezone": "America/Montreal"}}}
    appliquer_horloges(politique, appletsrc=f, relancer=lambda: True)
    ok, msg = appliquer_horloges(politique, appletsrc=f, relancer=lambda: True)
    assert ok and "deja la" in msg
    assert _lire(f).get("Containments][2][General", "AppletOrder") == "3;7;21;22"


def test_sans_horloge_dans_le_panneau_on_ne_fabrique_rien(tmp_path):
    """Un applet pose dans un panneau qu'on n'a pas compris donnerait un
    panneau ampute : on prefere l'heure au mauvais format."""
    f = _panneau(tmp_path, corps="[Containments][1]\nplugin=org.kde.plasma.folder\n")
    ok, msg = appliquer_horloges({"session": {"clock": {"format": "12h"}}},
                                 appletsrc=f, relancer=lambda: True)
    assert not ok and "aucune horloge" in msg
    assert "digitalclock" not in f.read_text()


def test_sans_fichier_de_panneau_on_le_dit(tmp_path):
    ok, msg = appliquer_horloges({}, appletsrc=tmp_path / "absent", relancer=lambda: True)
    assert not ok and "absent" in msg
