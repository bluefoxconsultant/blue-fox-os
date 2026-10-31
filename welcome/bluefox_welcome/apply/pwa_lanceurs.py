"""PWA épinglées de la politique : un lanceur chacune, épinglé à la barre des
tâches de Plasma (#26870, demande d'Olivier du 2026-10-24).

Pour chaque PWA `pinned` de bf-policy/v2 (session.pwas[]) :
- ~/.local/share/applications/<id>.desktop ouvre l'adresse dans une fenêtre
  d'application de Brave (`--app=`), avec le profil Brave de la personne
  (mêmes cookies, donc la même connexion Authentik) ;
- son icône vient du manifeste web du site (ce qu'utilise Brave pour une PWA
  installée), à défaut d'une icône du thème ;
- les lanceurs s'ajoutent à la barre des tâches par le script de bureau de
  Plasma (D-Bus org.kde.PlasmaShell.evaluateScript), sans toucher aux
  lanceurs déjà épinglés.

Le fichier .desktop porte le nom de l'identifiant de fenêtre que Brave donne à
`--app=<url>` sous Wayland : c'est par ce nom que Plasma relie la fenêtre
ouverte à l'icône épinglée (sinon une 2e icône apparaît à côté).
"""
from __future__ import annotations

import html.parser
import json
import logging
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome.apply.pwa_lanceurs")

BRAVE = "com.brave.Browser"
_TAILLE_MAX = 512 * 1024
_DELAI = 6
# Icône du thème préférée à celle du site : le logo de Vaultwarden est noir sur
# fond transparent, presque invisible sur la barre des tâches sombre ; celui de
# Bitwarden (Flatpak de la clé) se lit.
_ICONES_PREFEREES = (
    ("vault", "com.bitwarden.desktop"),
)
# Icône du thème quand le site n'en donne pas : par mot dans l'adresse. Des noms
# qui EXISTENT dans l'image (Breeze, ou Flatpak de la clé) : « internet-chat »,
# absent de Breeze, donnait l'icône générique de Talk (essai du 2026-10-26).
_ICONES_THEME = (
    ("spreed", "internet-telephony"),
    ("talk", "internet-telephony"),
    ("vault", "com.bitwarden.desktop"),
    ("nextcloud", "com.nextcloud.desktopclient.nextcloud"),
    ("odoo", "applications-office"),
)


def identifiant_fenetre(url: str) -> str:
    """Identifiant de fenêtre de `brave --app=<url>` (profil Default).

    Chromium nomme l'application d'après l'URL (GenerateApplicationNameFromURL :
    hôte + "_" + chemin), remplace les caractères interdits dans un nom de
    fichier par « _ » et retire les « _ » de début et de fin
    (GetWMClassFromAppName)."""
    u = urllib.parse.urlsplit(url)
    nom = f"{u.hostname or ''}_{u.path or '/'}"
    nom = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", nom).strip("_")
    return f"brave-{nom}-Default"


def _slug(nom: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-") or "pwa"


class _Liens(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.manifeste = None
        self.icones: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        rel = (a.get("rel") or "").lower().split()
        href = a.get("href")
        if not href:
            return
        if "manifest" in rel and not self.manifeste:
            self.manifeste = href
        elif "apple-touch-icon" in rel:
            self.icones.append((180, href))
        elif "icon" in rel:
            m = re.match(r"(\d+)x\d+", a.get("sizes") or "")
            self.icones.append((int(m.group(1)) if m else 16, href))


def _lire(url: str, ouvrir=urllib.request.urlopen) -> tuple[bytes, str, str]:
    """(corps, type, url finale). HTTPS seulement, taille bornée."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError(f"adresse non HTTPS : {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "bluefox-welcome"})
    with ouvrir(req, timeout=_DELAI) as r:
        corps = r.read(_TAILLE_MAX + 1)
        if len(corps) > _TAILLE_MAX:
            raise ValueError("réponse trop grande")
        return corps, r.headers.get("Content-Type", ""), r.geturl()


def _extension_image(corps: bytes) -> str | None:
    if corps.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if corps[:4] == b"\x00\x00\x01\x00":
        return "ico"
    debut = corps[:512].lstrip().lower()
    if debut.startswith(b"<svg") or (debut.startswith(b"<?xml") and b"<svg" in corps[:2048].lower()):
        return "svg"
    return None


# Pictogramme d'une appli Nextcloud : refusé s'il porte autre chose qu'un dessin.
_SVG_INTERDIT = re.compile(rb"<!DOCTYPE|<!ENTITY|<script|<foreignObject|<image|<use|href\s*=|url\(|<style", re.I)
_COULEUR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_BLEU_NEXTCLOUD = "#0082C9"


def appli_nextcloud(url: str) -> str | None:
    """L'appli Nextcloud qu'ouvre l'adresse (`/apps/<appli>`, ou celle du
    `redirectUrl` d'une connexion user_oidc), ou None."""
    u = urllib.parse.urlsplit(url)
    chemin = u.path
    if "/apps/user_oidc/" in chemin:
        chemin = (urllib.parse.parse_qs(u.query).get("redirectUrl") or [""])[0]
    m = re.search(r"/apps/([a-z0-9_]+)(?:/|$)", chemin)
    return m.group(1) if m and m.group(1) not in ("user_oidc", "theming") else None


def icone_appli_nextcloud(url: str, dossier: Path, slug: str, ouvrir=urllib.request.urlopen) -> Path | None:
    """Nextcloud sert sans session le pictogramme de chaque appli
    (`/apps/<appli>/img/app.svg`, blanc) et la couleur de son thème : on les
    compose en une icône, distincte pour Fichiers et pour Talk. Sans cela, la page
    de l'appli (401, ou redirection vers Authentik, un autre hôte) ne donnait
    rien et l'icône restait générique (essais du 2026-10-26 et du 2026-10-27)."""
    appli = appli_nextcloud(url)
    if not appli:
        return None
    racine = f"https://{urllib.parse.urlsplit(url).netloc}"
    try:
        glyphe, _t, finale = _lire(f"{racine}/apps/{appli}/img/app.svg", ouvrir)
    except Exception as e:  # noqa: BLE001 — pas Nextcloud, ou appli absente
        LOG.info("pictogramme Nextcloud de %s : %s", url, e)
        return None
    if (urllib.parse.urlsplit(finale).netloc != urllib.parse.urlsplit(racine).netloc
            or _extension_image(glyphe) != "svg" or _SVG_INTERDIT.search(glyphe)):
        return None
    texte = glyphe.decode("utf-8", "replace")
    debut = re.search(r"<svg\b[^>]*>", texte)
    fin = texte.rfind("</svg>")
    if not debut or fin < debut.end():
        return None
    entete = debut.group(0)
    vb = re.search(r'viewBox="([0-9.\s-]+)"', entete)
    if vb:
        cadre = vb.group(1)
    else:
        l = re.search(r'width="([0-9.]+)', entete)
        h = re.search(r'height="([0-9.]+)', entete)
        cadre = f"0 0 {l.group(1) if l else 16} {h.group(1) if h else 16}"
    # Couleur du dessin : celle de la balise racine (retirée ici), blanc sinon.
    trait = re.search(r'\sfill="(#[0-9A-Fa-f]{3,6}|white|black|none)"', entete)
    trait = trait.group(1) if trait else "#fff"
    couleur = _BLEU_NEXTCLOUD
    try:
        brut, _t, _f = _lire(f"{racine}/apps/theming/manifest/core", ouvrir)
        c = str(json.loads(brut).get("theme_color") or "")
        if _COULEUR_RE.fullmatch(c):
            couleur = c
    except Exception:  # noqa: BLE001 — le bleu de Nextcloud
        pass
    # Un groupe mis a l'echelle, pas un <svg> imbrique : le moteur SVG de Qt
    # (profil Tiny, celui des icones de Plasma) ne dessine pas un <svg> dans un
    # autre, et l'icone restait un carre vide (vu en VM le 2026-10-27).
    try:
        x0, y0, larg, haut = (float(v) for v in cadre.split())
    except ValueError:
        return None
    if larg <= 0 or haut <= 0:
        return None
    k = 36 / max(larg, haut)
    tx = 14 + (36 - larg * k) / 2 - x0 * k
    ty = 14 + (36 - haut * k) / 2 - y0 * k
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
           f'<rect width="64" height="64" rx="14" fill="{couleur}"/>'
           f'<g transform="translate({tx:.3f} {ty:.3f}) scale({k:.5f})" fill="{trait}">'
           f'{texte[debut.end():fin]}</g></svg>\n')
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / f"{slug}.svg"
    cible.write_text(svg, encoding="utf-8")
    return cible


def chercher_icone(url: str, dossier: Path, slug: str, ouvrir=urllib.request.urlopen) -> Path | None:
    """Le pictogramme d'une appli Nextcloud, sinon l'icône la plus grande du
    manifeste web, sinon des <link rel=icon>. Même hôte que la PWA seulement.
    Rend le fichier écrit, ou None."""
    trouve = icone_appli_nextcloud(url, dossier, slug, ouvrir=ouvrir)
    if trouve:
        return trouve
    morceaux = urllib.parse.urlsplit(url)
    hote = morceaux.hostname
    racine = f"https://{morceaux.netloc}/"
    # La page d'une appli peut refuser sans session (Nextcloud /apps/files :
    # 401 sans redirection) : la racine du site porte les mêmes icônes.
    for adresse in dict.fromkeys([url, racine]):
        try:
            page, _t, finale = _lire(adresse, ouvrir)
        except Exception as e:  # noqa: BLE001 — l'icône du thème suffit
            LOG.info("icône de %s : page %s illisible (%s)", url, adresse, e)
            continue
        trouve = _icone_de_la_page(page, finale, hote, dossier, slug, ouvrir)
        if trouve:
            return trouve
    return None


def _icone_de_la_page(page: bytes, finale: str, hote: str, dossier: Path, slug: str,
                      ouvrir) -> Path | None:
    p = _Liens()
    try:
        p.feed(page.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        pass
    candidats: list[tuple[int, str]] = []
    if p.manifeste:
        try:
            brut, _t, fin_m = _lire(urllib.parse.urljoin(finale, p.manifeste), ouvrir)
            for ic in json.loads(brut).get("icons") or []:
                tailles = [int(m.group(1)) for m in re.finditer(r"(\d+)x\d+", ic.get("sizes") or "")]
                if ic.get("src"):
                    candidats.append((max(tailles or [0]), urllib.parse.urljoin(fin_m, ic["src"])))
        except Exception as e:  # noqa: BLE001
            LOG.info("manifeste de %s illisible (%s)", url, e)
    candidats += [(t, urllib.parse.urljoin(finale, h)) for t, h in p.icones]
    for _taille, src in sorted(candidats, key=lambda c: -c[0]):
        if urllib.parse.urlsplit(src).hostname != hote:
            continue
        try:
            corps, _t, _f = _lire(src, ouvrir)
        except Exception:  # noqa: BLE001
            continue
        ext = _extension_image(corps)
        if not ext:
            continue
        dossier.mkdir(parents=True, exist_ok=True)
        cible = dossier / f"{slug}.{ext}"
        cible.write_bytes(corps)
        return cible
    return None


def icone_du_theme(url: str) -> str:
    bas = url.lower()
    for mot, icone in _ICONES_THEME:
        if mot in bas:
            return icone
    return "applications-internet"


def ecrire_lanceur(pwa: dict, home: Path, icone: str) -> Path:
    url = pwa["url"]
    ident = identifiant_fenetre(url)
    nom = (pwa.get("name") or urllib.parse.urlsplit(url).hostname or "PWA").replace("\n", " ")
    corps = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={nom}\n"
        f"Comment={nom} (Blue Fox)\n"
        f"Exec=flatpak run --command=brave {BRAVE} --profile-directory=Default --app={url}\n"
        f"Icon={icone}\n"
        f"StartupWMClass={ident}\n"
        "StartupNotify=true\n"
        "Terminal=false\n"
        "Categories=Network;WebBrowser;\n"
        "X-BlueFox-PWA=true\n"
    )
    cible = home / ".local" / "share" / "applications" / f"{ident}.desktop"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(corps)
    return cible


# Lanceurs par défaut de la barre des tâches de Plasma 6 (main.xml du greffon,
# compilé dans la bibliothèque). Tant que personne n'y a touché, la clé
# « launchers » est ABSENTE du fichier et le script ne voit pas ce défaut :
# écrire nos seules PWA effaçait Réglages, Discover, Dolphin et Brave (vu en VM
# le 2026-10-24). On repart donc de ce défaut quand la clé est vide.
LANCEURS_PAR_DEFAUT = ["applications:systemsettings.desktop", "applications:org.kde.discover.desktop",
                       "preferred://filemanager", "preferred://browser"]

_SCRIPT_PLASMA = """
var ajouts = %s;
var defauts = %s;
var faits = 0;
panels().forEach(function (p) {
  p.widgets().forEach(function (w) {
    if (w.type !== "org.kde.plasma.icontasks" && w.type !== "org.kde.plasma.taskmanager") return;
    w.currentConfigGroup = ["General"];
    var l = w.readConfig("launchers", "");
    if (typeof l === "string") l = l ? l.split(",") : [];
    if (!l || l.length === 0) l = defauts.slice();
    ajouts.forEach(function (x) { if (l.indexOf(x) < 0) l.push(x); });
    w.writeConfig("launchers", l);
    w.reloadConfig();
    faits++;
  });
});
print("bfos-epingles:" + faits);
"""


def epingler(lanceurs: list[Path], run=subprocess.run, attendre=time.sleep,
             essais: int = 20) -> tuple[bool, str]:
    """Ajoute les lanceurs à toute barre des tâches de Plasma. Plasma démarre
    en même temps que l'agent : on réessaie quelques secondes."""
    if not lanceurs:
        return True, "aucune PWA à épingler"
    ajouts = [p.as_uri() for p in lanceurs]
    script = _SCRIPT_PLASMA % (json.dumps(ajouts), json.dumps(LANCEURS_PAR_DEFAUT))
    dernier = ""
    for _ in range(essais):
        try:
            r = run(["gdbus", "call", "--session", "--dest", "org.kde.plasmashell",
                     "--object-path", "/PlasmaShell", "--method",
                     "org.kde.PlasmaShell.evaluateScript", script],
                    capture_output=True, text=True, timeout=15)
            dernier = (r.stdout or "") + (r.stderr or "")
            m = re.search(r"bfos-epingles:(\d+)", dernier)
            if r.returncode == 0 and m and int(m.group(1)) > 0:
                return True, f"{len(ajouts)} PWA épinglée(s) à {m.group(1)} barre(s) des tâches"
        except Exception as e:  # noqa: BLE001
            dernier = str(e)
        attendre(3)
    return False, f"barre des tâches introuvable : {dernier.strip()[:200]}"


def appliquer_pwa_epinglees(pwas: list[dict], home: Path | None = None,
                            ouvrir=urllib.request.urlopen, run=subprocess.run,
                            attendre=time.sleep) -> tuple[bool, str]:
    home = home or Path.home()
    epinglees = [p for p in pwas or [] if isinstance(p, dict) and p.get("url") and p.get("pinned")
                 and urllib.parse.urlsplit(p["url"]).scheme == "https"]
    if not epinglees:
        return True, "aucune PWA épinglée dans la politique"
    dossier_icones = home / ".local" / "share" / "icons" / "bfos-pwa"
    lanceurs = []
    for pwa in epinglees:
        slug = _slug(pwa.get("name") or pwa["url"])
        imposee = next((i for mot, i in _ICONES_PREFEREES if mot in pwa["url"].lower()), None)
        fichier = None if imposee else chercher_icone(pwa["url"], dossier_icones, slug, ouvrir=ouvrir)
        icone = imposee or (str(fichier) if fichier else icone_du_theme(pwa["url"]))
        lanceurs.append(ecrire_lanceur(pwa, home, icone))
    try:
        run(["kbuildsycoca6"], capture_output=True, timeout=60)
    except Exception as e:  # noqa: BLE001 — le lien file:// de l'épinglage n'en dépend pas
        LOG.info("kbuildsycoca6 : %s", e)
    ok, msg = epingler(lanceurs, run=run, attendre=attendre)
    return ok, f"{len(lanceurs)} lanceur(s) PWA ; {msg}"
