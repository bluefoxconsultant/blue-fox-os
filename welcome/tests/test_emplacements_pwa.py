"""#26870 : montages dans Dolphin, PWA épinglées à la barre des tâches."""
import io
import json
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bluefox_welcome.apply import emplacements as em
from bluefox_welcome.apply import pwa_lanceurs as pl

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


# ------------------------------------------------------------- Dolphin
def _hrefs(home):
    racine = ET.parse(home / ".local/share/user-places.xbel").getroot()
    return [b.get("href") for b in racine.iter("bookmark")]


def test_fichier_absent_cree_sans_version(tmp_path):
    ok, msg = em.ajouter_emplacements([("Nextcloud", tmp_path / "Nextcloud")], home=tmp_path)
    assert ok, msg
    texte = (tmp_path / ".local/share/user-places.xbel").read_text()
    assert _hrefs(tmp_path) == [f"file://{tmp_path}/Nextcloud"]
    # Sans version, KIO ajoute lui-même ses emplacements par défaut.
    assert "kde_places_version" not in texte
    assert "<!DOCTYPE xbel>" in texte and "folder-cloud" in texte


def test_signets_existants_gardes_et_pas_de_doublon(tmp_path):
    f = tmp_path / ".local/share/user-places.xbel"
    f.parent.mkdir(parents=True)
    f.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n'
                 '<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks">'
                 '<info><metadata owner="http://www.kde.org"><kde_places_version>4'
                 '</kde_places_version></metadata></info>'
                 '<bookmark href="file:///home/u"><title>Home</title></bookmark></xbel>')
    for _ in range(2):
        assert em.ajouter_emplacements([("NC", Path("/home/u/Nextcloud"))], home=tmp_path)[0]
    assert _hrefs(tmp_path) == ["file:///home/u", "file:///home/u/Nextcloud"]
    assert "kde_places_version" in f.read_text()


def test_fichier_abime_pas_reecrit(tmp_path):
    f = tmp_path / ".local/share/user-places.xbel"
    f.parent.mkdir(parents=True)
    f.write_text("<xbel><bookmark")
    ok, _ = em.ajouter_emplacements([("NC", tmp_path / "NC")], home=tmp_path)
    assert not ok
    assert f.read_text() == "<xbel><bookmark"


# ------------------------------------------------------------- PWA
def test_identifiant_fenetre():
    assert pl.identifiant_fenetre("https://bluefoxconsultant.com/odoo") == \
        "brave-bluefoxconsultant.com__odoo-Default"
    assert pl.identifiant_fenetre("https://vault.bluefoxconsultant.com") == \
        "brave-vault.bluefoxconsultant.com-Default"
    assert pl.identifiant_fenetre("https://nc.test/index.php/apps/spreed") == \
        "brave-nc.test__index.php_apps_spreed-Default"


class Rep(io.BytesIO):
    def __init__(self, corps, url, ctype="text/html"):
        super().__init__(corps)
        self.headers = {"Content-Type": ctype}
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def faux_web(pages):
    vus = []

    def ouvrir(req, timeout=None):
        url = req.full_url
        vus.append(url)
        if url not in pages:
            raise OSError("404")
        corps, finale = pages[url] if isinstance(pages[url], tuple) else (pages[url], url)
        return Rep(corps, finale)
    ouvrir.vus = vus
    return ouvrir


def test_icone_la_plus_grande_du_manifeste(tmp_path):
    web = faux_web({
        "https://erp.test/odoo": (b'<link rel="manifest" href="/m.json"><link rel="icon" href="/f.ico">',
                                  "https://erp.test/web/login"),
        "https://erp.test/m.json": json.dumps({"icons": [
            {"src": "/i48.png", "sizes": "48x48"}, {"src": "/i512.png", "sizes": "512x512"},
            {"src": "https://ailleurs.test/i1024.png", "sizes": "1024x1024"}]}).encode(),
        "https://erp.test/i512.png": PNG,
        "https://erp.test/i48.png": PNG,
    })
    f = pl.chercher_icone("https://erp.test/odoo", tmp_path, "odoo", ouvrir=web)
    assert f == tmp_path / "odoo.png" and f.read_bytes() == PNG
    # Jamais d'icône prise sur un autre hôte.
    assert "https://ailleurs.test/i1024.png" not in web.vus


def test_icone_pas_une_image_ignoree(tmp_path):
    web = faux_web({"https://x.test/": b'<link rel="icon" href="/f.png">',
                    "https://x.test/f.png": b"<html>pas une image</html>"})
    assert pl.chercher_icone("https://x.test/", tmp_path, "x", ouvrir=web) is None


def test_lanceur(tmp_path):
    f = pl.ecrire_lanceur({"name": "Odoo", "url": "https://erp.test/odoo"}, tmp_path, "/i.png")
    assert f.name == "brave-erp.test__odoo-Default.desktop"
    t = f.read_text()
    assert "Exec=flatpak run --command=brave com.brave.Browser --profile-directory=Default " \
           "--app=https://erp.test/odoo\n" in t
    assert "StartupWMClass=brave-erp.test__odoo-Default\n" in t and "Icon=/i.png\n" in t


def faux_gdbus(sorties):
    appels = []

    def run(argv, **kw):
        appels.append(argv)
        if argv[0] != "gdbus":
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        rc, out = sorties.pop(0) if sorties else (0, "('bfos-epingles:1',)")
        return types.SimpleNamespace(returncode=rc, stdout=out, stderr="")
    run.appels = appels
    return run


def test_epingler_attend_plasma(tmp_path):
    run = faux_gdbus([(1, "Error: org.freedesktop.DBus.Error.ServiceUnknown"), (0, "('bfos-epingles:0',)")])
    ok, msg = pl.epingler([tmp_path / "a.desktop"], run=run, attendre=lambda s: None)
    assert ok and "1 barre" in msg
    gdbus = [a for a in run.appels if a[0] == "gdbus"]
    assert len(gdbus) == 3
    script = gdbus[-1][-1]
    assert json.dumps([(tmp_path / "a.desktop").as_uri()]) in script
    assert "l.indexOf(x) < 0" in script  # n'enlève ni ne double un lanceur


def test_epingler_abandonne_sans_barre(tmp_path):
    run = faux_gdbus([(0, "('bfos-epingles:0',)")] * 5)
    ok, _ = pl.epingler([tmp_path / "a.desktop"], run=run, attendre=lambda s: None, essais=5)
    assert not ok


def test_seules_les_pwa_epinglees_https(tmp_path):
    run = faux_gdbus([])
    pwas = [{"name": "Odoo", "url": "https://erp.test/odoo", "pinned": True},
            {"name": "Talk", "url": "https://nc.test/talk", "pinned": False},
            {"name": "Clair", "url": "http://nc.test/", "pinned": True},
            "https://ancien-format.test"]
    ok, msg = pl.appliquer_pwa_epinglees(pwas, home=tmp_path, ouvrir=faux_web({}),
                                         run=run, attendre=lambda s: None)
    assert ok, msg
    apps = sorted(p.name for p in (tmp_path / ".local/share/applications").iterdir())
    assert apps == ["brave-erp.test__odoo-Default.desktop"]
    # Sans icône du site : celle du thème.
    assert "Icon=applications-office" in (tmp_path / ".local/share/applications" / apps[0]).read_text()


def test_icone_par_la_racine_si_la_page_refuse(tmp_path):
    web = faux_web({"https://nc.test/": b'<link rel="apple-touch-icon" href="/t.png">',
                    "https://nc.test/t.png": PNG})
    f = pl.chercher_icone("https://nc.test/apps/files", tmp_path, "nc", ouvrir=web)
    assert f == tmp_path / "nc.png"
    # Pictogramme de l'appli d'abord (absent ici), puis la page, puis la racine.
    assert web.vus[:2] == ["https://nc.test/apps/files/img/app.svg", "https://nc.test/apps/files"]


def test_vaultwarden_icone_bitwarden_sans_telechargement(tmp_path):
    web = faux_web({})
    pl.appliquer_pwa_epinglees([{"name": "Vault", "url": "https://vault.test", "pinned": True}],
                               home=tmp_path, ouvrir=web, run=faux_gdbus([]), attendre=lambda s: None)
    t = next((tmp_path / ".local/share/applications").iterdir()).read_text()
    assert "Icon=com.bitwarden.desktop\n" in t and web.vus == []


def test_epingler_garde_les_lanceurs_par_defaut():
    """Clé « launchers » absente = défaut du greffon, invisible au script : on
    repart du défaut de Plasma 6 au lieu de n'écrire que les PWA (vu en VM)."""
    scripts = []

    def run(argv, **kw):
        scripts.append(argv[-1])
        return types.SimpleNamespace(returncode=0, stdout="('bfos-epingles:1',)", stderr="")
    ok, _ = pl.epingler([Path("/h/a.desktop")], run=run, attendre=lambda s: None)
    assert ok
    js = scripts[0]
    assert json.dumps(pl.LANCEURS_PAR_DEFAUT) in js
    assert "preferred://filemanager" in pl.LANCEURS_PAR_DEFAUT
    assert "if (!l || l.length === 0) l = defauts.slice();" in js
    assert js.index("defauts.slice()") < js.index("ajouts.forEach")


GLYPHE = b'<svg xmlns="http://www.w3.org/2000/svg" height="32" width="32" viewBox="0 0 32 32"><path d="M1 1h30v30z"/></svg>'


def test_talk_et_fichiers_ont_chacun_leur_pictogramme(tmp_path):
    """Nextcloud : pictogramme de l'appli sur la couleur du thème, pour Talk comme
    pour Fichiers (y compris derrière une connexion user_oidc)."""
    web = faux_web({
        "https://nc.test/apps/spreed/img/app.svg": GLYPHE,
        "https://nc.test/apps/files/img/app.svg": GLYPHE.replace(b"M1 1", b"M2 2"),
        "https://nc.test/apps/theming/manifest/core": json.dumps({"theme_color": "#1F5D4A"}).encode(),
    })
    run = faux_gdbus([(0, "('bfos-epingles:1',)")])
    ok, _ = pl.appliquer_pwa_epinglees(
        [{"name": "Talk", "url": "https://nc.test/index.php/apps/spreed", "pinned": True},
         {"name": "Nextcloud", "url": "https://nc.test/apps/user_oidc/login/1?redirectUrl=%2Fapps%2Ffiles",
          "pinned": True}],
        home=tmp_path, ouvrir=web, run=run, attendre=lambda s: None)
    assert ok
    talk = (tmp_path / ".local/share/icons/bfos-pwa/talk.svg").read_text()
    nc = (tmp_path / ".local/share/icons/bfos-pwa/nextcloud.svg").read_text()
    assert 'fill="#1F5D4A"' in talk and "M1 1h30v30z" in talk
    assert '<g transform="translate(14.000 14.000) scale(1.12500)" fill="#fff">' in talk
    assert talk.count("<svg") == 1, "pas de <svg> imbrique : Qt ne le dessine pas"
    assert "M2 2h30v30z" in nc and talk != nc
    lanceurs = " ".join(f.read_text() for f in (tmp_path / ".local/share/applications").glob("*.desktop"))
    assert "Icon=" + str(tmp_path / ".local/share/icons/bfos-pwa/talk.svg") in lanceurs


@pytest.mark.parametrize("glyphe", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://ailleurs.test/x.png"/></svg>',
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>',
    b"<html>pas une image</html>",
])
def test_pictogramme_douteux_refuse(tmp_path, glyphe):
    web = faux_web({"https://nc.test/apps/spreed/img/app.svg": glyphe})
    assert pl.icone_appli_nextcloud("https://nc.test/apps/spreed", tmp_path, "talk", ouvrir=web) is None


def test_pictogramme_d_un_autre_hote_refuse(tmp_path):
    web = faux_web({"https://nc.test/apps/spreed/img/app.svg": (GLYPHE, "https://ailleurs.test/app.svg")})
    assert pl.icone_appli_nextcloud("https://nc.test/apps/spreed", tmp_path, "talk", ouvrir=web) is None


def test_talk_sans_icone_du_site_garde_une_icone_du_theme(tmp_path):
    """Repli sur un nom qui existe dans Breeze (« internet-chat » n'y est pas)."""
    run = faux_gdbus([(0, "('bfos-epingles:1',)")])
    ok, _ = pl.appliquer_pwa_epinglees(
        [{"name": "Talk", "url": "https://nc.exemple.test/index.php/apps/spreed", "pinned": True}],
        home=tmp_path, ouvrir=faux_web({}), run=run, attendre=lambda s: None)
    assert ok
    lanceur = next((tmp_path / ".local/share/applications").glob("*.desktop")).read_text()
    assert "Icon=internet-telephony" in lanceur


def test_appli_nextcloud():
    assert pl.appli_nextcloud("https://nc.test/index.php/apps/spreed/") == "spreed"
    assert pl.appli_nextcloud("https://nc.test/apps/user_oidc/login/1?redirectUrl=%2Fapps%2Ffiles") == "files"
    assert pl.appli_nextcloud("https://nc.test/apps/user_oidc/login/1") is None
    assert pl.appli_nextcloud("https://erp.test/odoo") is None
