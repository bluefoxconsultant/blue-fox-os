"""Organisation de la clé générique (#26870, 2026-10-27).

La clé publiée sur le site de Blue Fox OS ne connaît aucune organisation : son
`live.json` n'est qu'un SOCLE (plages d'uid, groupes locaux, compte d'attente,
tailles de profil). Au premier démarrage, la personne tape le domaine de son
organisation ; l'aide PAM (root) lit alors https://<domaine>/bfos-live.json,
servi par l'Odoo de l'organisation (bf_policy_live 18.0.1.4.0), le valide et le
dépose dans DOSSIER. Verrouillage, fermeture, unité de session et agent
d'accueil relisent ce dépôt.

Ce que l'organisation peut fixer est une LISTE BLANCHE (CLES_LIVE) : son
fournisseur d'identité, ses groupes admis, ses adresses Odoo, sa langue, son
fuseau. Jamais les groupes locaux, les plages d'uid, le compte d'attente, les
services PAM ni les dossiers : un domaine tapé par erreur (ou malveillant) ne
doit rien obtenir de plus qu'une session ordinaire. La double authentification
ne peut que se durcir (`require_mfa` du socle OU de l'organisation).

Le domaine (seul) est retenu sur la clé, dans l'espace des profils, par l'unité
de session ; l'unité de démarrage bfos-live-organisation le relit dans RETENU.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from bfos_live.langue import N_

DOSSIER = "/run/bfos-live-organisation"
TENANT = os.path.join(DOSSIER, "tenant.json")
# Domaine retenu sur la clé, relu au démarrage (root seulement).
RETENU = "/run/bfos-live-retenu/domaine"
# Déposé par l'écran de connexion (usager greetd) : « Changer d'organisation ».
DRAPEAU_CHANGER = "/run/bfos-live-ecran/changer-organisation"
# Nom du fichier retenu dans l'espace des profils de la clé.
FICHIER_RETENU = "organisation"

SCHEMA = "bfos-live/v1"
VERSION = "1"
TAILLE_MAX = 64 * 1024
DELAI = 15
DELAI_TOTAL = 30
# Suffixes publics a plusieurs etiquettes (approximation de la Public Suffix List,
# absente de l'image) : « boreal.qc.ca » et « autre.qc.ca » sont deux organisations.
SUFFIXES_COMPOSES = frozenset({
    "qc.ca", "on.ca", "bc.ca", "ab.ca", "mb.ca", "nb.ca", "nl.ca", "ns.ca", "pe.ca", "sk.ca",
    "nt.ca", "nu.ca", "yk.ca", "gouv.qc.ca", "gc.ca", "co.uk", "org.uk", "ac.uk", "gov.uk",
    "ltd.uk", "plc.uk", "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz", "org.nz",
    "net.nz", "govt.nz", "ac.nz", "school.nz", "co.jp", "or.jp", "ne.jp", "com.br", "com.mx",
    "co.za", "com.cn", "com.hk", "co.in", "gouv.fr", "asso.fr", "com.fr", "co.il", "com.sg",
    "github.io", "gitlab.io", "pages.dev", "netlify.app", "vercel.app", "herokuapp.com",
    "azurewebsites.net", "cloudfront.net", "ts.net", "ngrok.io", "ngrok-free.app",
    "odoo.com", "blogspot.com", "onrender.com", "fly.dev", "web.app", "firebaseapp.com",
    "appspot.com", "myshopify.com", "duckdns.org", "amazonaws.com", "pythonanywhere.com",
    "wixsite.com", "workers.dev", "glitch.me", "repl.co", "dyndns.org", "no-ip.org",
})

CLES_LIVE = ("issuer", "client_id", "device_url", "token_url", "revoke_url", "allowed_groups",
             "require_mfa", "policy_url", "profile_key_url", "login_link_url", "langue", "timezone")
# Adresses servies par l'Odoo de l'organisation : sur le domaine tapé, rien d'autre.
CLES_ODOO = ("policy_url", "profile_key_url", "login_link_url")
CLES_AUTHENTIK = ("issuer", "device_url", "token_url", "revoke_url")

_ETIQUETTE = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
DOMAINE_RE = re.compile(rf"(?:{_ETIQUETTE}\.)+[a-z]{{2,63}}(?::[0-9]{{1,5}})?")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
_COULEUR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
_FUSEAU_RE = re.compile(r"[A-Za-z_]+(?:/[A-Za-z0-9_+-]+){0,2}")
_LANGUE_RE = re.compile(r"[a-z]{2,3}")
_CONTROLE_RE = re.compile(r"[\x00-\x1f\x7f]")


class Erreur(Exception):
    """`message` pour l'écran (marqué N_), `raison` pour le journal."""

    def __init__(self, message, raison):
        super().__init__(raison)
        self.message = message
        self.raison = raison


def normaliser_domaine(texte) -> str | None:
    """« https://Boreal.example/ » -> « boreal.example ». Un port est admis (bancs
    d'essai) ; ni chemin, ni identifiants, ni adresse IP. None si invalide."""
    texte = str(texte or "").strip()
    if not texte or len(texte) > 253 or _CONTROLE_RE.search(texte) or " " in texte:
        return None
    if "://" in texte:
        u = urllib.parse.urlsplit(texte)
        if u.scheme != "https" or u.username or u.password or u.query or u.fragment \
                or u.path not in ("", "/"):
            return None
        texte = u.netloc
    texte = texte.rstrip("/").lower()
    hote, _deux, port = texte.partition(":")
    try:
        ascii_ = hote.encode("idna").decode("ascii")
        # IDNA 2003 (codec de Python) change « ß » en « ss » : un autre domaine que
        # celui des navigateurs. Un nom accentue doit revenir a l'identique ; sa forme
        # ASCII (xn--, deposee puis retenue) doit se reencoder a l'identique.
        unicode_ = ascii_.encode("ascii").decode("idna")
        if (unicode_ != hote) if not hote.isascii() else (unicode_.encode("idna").decode("ascii") != ascii_):
            return None
    except UnicodeError:
        return None
    hote = ascii_
    if port == "443":
        port = ""
    domaine = f"{hote}:{port}" if port else hote
    if not DOMAINE_RE.fullmatch(domaine):
        return None
    if port and not 0 < int(port) < 65536:
        return None
    return domaine


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OUVREUR = urllib.request.build_opener(_SansRedirection())


def telecharger(domaine, ouvrir=_OUVREUR.open, delai_total=None) -> dict:
    """La configuration publiée par l'organisation, lue telle quelle (sans
    suivre de redirection : la réponse doit venir du domaine tapé), en
    DELAI_TOTAL secondes au plus, en-têtes compris : le délai de la socket vaut
    par lecture, et un serveur qui goutte retiendrait l'écran indéfiniment."""
    resultat = {}

    def lire():
        try:
            resultat["doc"] = _telecharger(domaine, ouvrir)
        except BaseException as exc:  # noqa: BLE001 - rendu au fil appelant
            resultat["exc"] = exc
    fil = threading.Thread(target=lire, daemon=True)
    fil.start()
    fil.join(DELAI_TOTAL if delai_total is None else delai_total)
    if fil.is_alive():
        raise Erreur(N_("Impossible de joindre ce domaine. Vérifie-le, et le réseau."),
                     f"https://{domaine}/bfos-live.json : délai total dépassé")
    if "exc" in resultat:
        raise resultat["exc"]
    return resultat["doc"]


def _telecharger(domaine, ouvrir) -> dict:
    url = f"https://{domaine}/bfos-live.json"
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                "User-Agent": "bfos-live"})
    try:
        with ouvrir(req, timeout=DELAI) as r:
            statut = getattr(r, "status", 200)
            version = r.headers.get("x-bf-live-version")
            morceaux, lu = [], 0
            while lu <= TAILLE_MAX:
                bloc = r.read(min(8192, TAILLE_MAX + 1 - lu))
                if not bloc:
                    break
                morceaux.append(bloc)
                lu += len(bloc)
            corps = b"".join(morceaux)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Erreur(N_("Ce domaine ne sert pas de clé Blue Fox OS."),
                         f"{url}: 404") from exc
        raise Erreur(N_("Ce domaine ne répond pas correctement. Vérifie-le."),
                     f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        raise Erreur(N_("Impossible de joindre ce domaine. Vérifie-le, et le réseau."),
                     f"{url}: {exc!r}") from exc
    if statut != 200 or version != VERSION:
        raise Erreur(N_("Ce domaine ne sert pas de clé Blue Fox OS."),
                     f"{url}: statut {statut}, x-bf-live-version={version!r}")
    if len(corps) > TAILLE_MAX:
        raise Erreur(N_("Ce domaine ne répond pas correctement. Vérifie-le."),
                     f"{url}: réponse de plus de {TAILLE_MAX} octets")
    try:
        doc = json.loads(corps.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise Erreur(N_("Ce domaine ne répond pas correctement. Vérifie-le."),
                     f"{url}: JSON illisible ({exc})") from exc
    return doc


def _texte(v, maxi=100) -> str | None:
    v = str(v).strip() if isinstance(v, str) else ""
    return v if v and len(v) <= maxi and not _CONTROLE_RE.search(v) else None


def _https(v, maxi=512) -> str | None:
    v = _texte(v, maxi)
    if not v:
        return None
    try:
        u = urllib.parse.urlsplit(v)
        hote, _port = u.hostname, u.port
    except ValueError:
        return None
    if u.scheme != "https" or not hote or u.username or u.password or u.fragment:
        return None
    return v


def domaine_enregistre(hote) -> str:
    """« auth.boreal.qc.ca » -> « boreal.qc.ca » : le domaine que l'organisation
    a enregistre (sans port)."""
    etiquettes = (hote or "").split(":")[0].lower().strip(".").split(".")
    for n in (3, 2):
        if len(etiquettes) > n and ".".join(etiquettes[-n:]) in SUFFIXES_COMPOSES:
            return ".".join(etiquettes[-n - 1:])
    return ".".join(etiquettes[-2:])


def _hote(url) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def valider(doc, domaine) -> dict:
    try:
        return _valider(doc, domaine)
    except Erreur:
        raise
    except Exception as exc:  # noqa: BLE001 - un document piege ne doit rien casser
        raise Erreur(N_("Ce domaine ne sert pas une configuration valide."),
                     f"configuration illisible : {exc!r}") from exc


def _valider(doc, domaine) -> dict:
    """{"organisation", "domaine", "live", "tenant"} épurés, ou Erreur.

    Rien de ce qui n'est pas dans la liste blanche ne passe ; une adresse Odoo
    hors du domaine tapé, une adresse en http, un champ du mauvais type
    refusent TOUTE la configuration (pas de configuration à moitié)."""
    def refus(raison):
        return Erreur(N_("Ce domaine ne sert pas une configuration valide."), raison)

    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise refus(f"schéma {doc.get('schema') if isinstance(doc, dict) else type(doc)!r}")
    nom = _texte(doc.get("organisation"))
    if not nom:
        raise refus("nom d'organisation absent ou invalide")
    live_brut = doc.get("live")
    if not isinstance(live_brut, dict):
        raise refus("bloc live absent")
    live = {}
    for cle in CLES_AUTHENTIK + CLES_ODOO:
        if cle not in live_brut:
            continue
        url = _https(live_brut[cle])
        if not url:
            raise refus(f"{cle} n'est pas une adresse https")
        if cle in CLES_ODOO and urllib.parse.urlsplit(url).netloc.lower() != domaine:
            raise refus(f"{cle} hors du domaine tapé : {url}")
        live[cle] = url
    for cle in ("issuer", "device_url", "token_url", "policy_url"):
        if cle not in live:
            raise refus(f"{cle} manquant")
    # L'aide ne verifie pas la signature de l'id_token (il vient du point de jeton
    # en TLS) : les points d'Authentik doivent donc etre sur l'hote MEME de
    # l'emetteur, sinon un point de jeton tiers forgerait des revendications au nom
    # d'un vrai emetteur.
    hote_emetteur = _hote(live["issuer"])
    for cle in ("device_url", "token_url", "revoke_url"):
        if cle in live and _hote(live[cle]) != hote_emetteur:
            raise refus(f"{cle} hors de l'hote de l'emetteur : {live[cle]}")
    # Un domaine sosie (« bluefoxconsulant.com ») qui servirait le VRAI emetteur
    # avec SES adresses Odoo recolterait les jetons de la personne : l'emetteur doit
    # etre sous le meme domaine enregistre que le domaine tape.
    if domaine_enregistre(hote_emetteur) != domaine_enregistre(domaine):
        raise refus(f"emetteur {hote_emetteur} hors du domaine {domaine}")
    client = _texte(live_brut.get("client_id"), 200)
    if not client:
        raise refus("client_id manquant")
    live["client_id"] = client
    groupes = live_brut.get("allowed_groups")
    if (not isinstance(groupes, list) or not 0 < len(groupes) <= 50
            or not all(_texte(g, 200) for g in groupes)):
        raise refus("allowed_groups invalide")
    live["allowed_groups"] = [g.strip() for g in groupes]
    if "require_mfa" in live_brut:
        if not isinstance(live_brut["require_mfa"], bool):
            raise refus("require_mfa invalide")
        live["require_mfa"] = live_brut["require_mfa"]
    langue_org = live_brut.get("langue")
    if isinstance(langue_org, str) and _LANGUE_RE.fullmatch(langue_org):
        live["langue"] = langue_org
    fuseau = live_brut.get("timezone")
    if isinstance(fuseau, str) and len(fuseau) <= 64 and _FUSEAU_RE.fullmatch(fuseau):
        live["timezone"] = fuseau
    return {"organisation": nom, "domaine": domaine, "live": live,
            "emetteur": hote_emetteur,
            "tenant": valider_tenant(doc.get("tenant"), nom, domaine)}


def valider_tenant(brut, nom, domaine) -> dict:
    """Ce que l'agent d'accueil lit (tenant.json) : nom, habillage, services.
    Tout champ douteux est simplement omis (l'agent a ses replis)."""
    brut = brut if isinstance(brut, dict) else {}
    slug = brut.get("slug")
    tenant = {"slug": slug if isinstance(slug, str) and _SLUG_RE.fullmatch(slug) else "org",
              "name": nom, "tenant_name": nom, "tenant_domain": domaine,
              "branding": {}, "services": {}}
    branding = brut.get("branding") if isinstance(brut.get("branding"), dict) else {}
    accent = (branding.get("palette") or {}).get("accent") if isinstance(branding.get("palette"), dict) else None
    if isinstance(accent, str) and _COULEUR_RE.fullmatch(accent):
        tenant["branding"]["palette"] = {"accent": accent}
    fond = _https(branding.get("wallpaper_url"))
    if fond:
        tenant["branding"]["wallpaper_url"] = fond
    services = brut.get("services") if isinstance(brut.get("services"), dict) else {}
    for nom_service in ("nextcloud", "vaultwarden", "odoo"):
        s = services.get(nom_service)
        url = _https(s.get("url")) if isinstance(s, dict) else None
        if not url:
            continue
        tenant["services"][nom_service] = {"url": url.rstrip("/")}
        client = _texte(s.get("oidc_client_id"), 200) if nom_service == "nextcloud" else None
        if client:
            tenant["services"][nom_service]["oidc_client_id"] = client
    return tenant


def _ecrire(chemin, donnees):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(chemin), prefix=".org-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(donnees, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _chemins(dossier):
    return (os.path.join(dossier, "live.json"), os.path.join(dossier, "tenant.json"),
            os.path.join(dossier, "organisation.json"))


def deposer(org, dossier=DOSSIER):
    """Dépose la configuration validée pour le reste du démarrage (root, 0644 :
    rien de secret). Le choix (organisation.json) est écrit en DERNIER : tant
    qu'il n'y est pas, `deposee()` ne rend rien, jamais un mélange de deux
    organisations."""
    if os.path.islink(dossier):
        raise Erreur(N_("Erreur interne."), f"{dossier} est un lien")
    os.makedirs(dossier, mode=0o755, exist_ok=True)
    os.chmod(dossier, 0o755)
    live, tenant, choix = _chemins(dossier)
    try:
        os.unlink(choix)
    except FileNotFoundError:
        pass
    _ecrire(live, org["live"])
    _ecrire(tenant, org["tenant"])
    _ecrire(choix, {"organisation": org["organisation"], "domaine": org["domaine"]})


def lire_json_root(chemin):
    """Un JSON déposé par root : fichier ordinaire, pas un lien, à root."""
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not (st.st_mode & 0o170000 == 0o100000) or st.st_uid != 0 or st.st_size > TAILLE_MAX:
            return None
        with os.fdopen(fd, encoding="utf-8") as f:
            fd = None
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def deposee(dossier=DOSSIER):
    """{"organisation", "domaine", "live"} déjà déposés pendant ce démarrage, ou None."""
    live_f, _tenant_f, choix_f = _chemins(dossier)
    choix = lire_json_root(choix_f)
    live = lire_json_root(live_f)
    if not choix or not live:
        return None
    return {"organisation": choix.get("organisation"), "domaine": choix.get("domaine"), "live": live}


def fusionner(socle: dict, live_org: dict) -> dict:
    """Le socle de l'image, plus ce que l'organisation peut fixer (liste blanche).
    La double authentification ne peut que se durcir."""
    cfg = dict(socle)
    for cle in CLES_LIVE:
        if cle in live_org and cle != "require_mfa":
            cfg[cle] = live_org[cle]
    cfg["require_mfa"] = bool(socle.get("require_mfa", True)) or bool(live_org.get("require_mfa"))
    return cfg


def socle_generique(socle: dict) -> bool:
    """Vrai pour une clé générique : son live.json ne nomme aucun fournisseur."""
    return not socle.get("issuer")


def domaine_retenu(chemin=RETENU) -> str | None:
    try:
        with open(chemin, encoding="ascii") as f:
            return normaliser_domaine(f.read(300))
    except (OSError, UnicodeDecodeError):
        return None


def changement_demande(chemin=DRAPEAU_CHANGER, proprietaire=None, consommer=False) -> bool:
    """L'écran a demandé « Changer d'organisation » (fichier de l'usager greetd)."""
    try:
        st = os.lstat(chemin)
    except OSError:
        return False
    if not (st.st_mode & 0o170000 == 0o100000) or (proprietaire is not None and st.st_uid != proprietaire):
        return False
    if consommer:
        try:
            os.unlink(chemin)
        except OSError:
            pass
    return True
