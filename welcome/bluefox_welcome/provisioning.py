"""Read the install-staged policy JSON (bf-policy/v2) and overlay it on the
baked per-tenant config.

The Anaconda %pre device flow stages /var/lib/bluefox-welcome/provisioning.json
(see install/bfos_provision.py). It carries the authenticated user and the
per-user/org *session* block (accent, wallpaper, mounts, PWAs). When present it
is preferred over the baked /usr/share/bluefox/tenant.json defaults: we know who
the user is (pre-fill the wizard) and which branding/session prefs to apply.

Returns {} on a missing/malformed/foreign file so the wizard still runs from
tenant.json alone.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

LOG = logging.getLogger("bluefox-welcome.provisioning")
PROVISIONING_FILE = Path("/var/lib/bluefox-welcome/provisioning.json")
# Copie EXPURGEE, 0644, ecrite par bfos_apply.py. C'est celle que la session
# peut lire : la complete est en 0600 root parce qu'elle porte le mot de passe
# de liaison LDAP.
PUBLIC_FILE = Path("/var/lib/bluefox-welcome/policy-public.json")


def _lire(path: Path) -> dict:
    """Lit UN fichier de politique. Rend {} sinon, en nommant la raison.

    ⚠️ La permission refusee se journalise en ERROR, pas en WARNING, et elle ne
    se confond pas avec l'absence. Le 2026-09-11, l'agent tournant en tant que
    l'usager ne pouvait pas lire la politique en 0600 root : l'exception etait
    avalee, {} etait rendu, et l'assistant manuel en 5 pages prenait la main
    comme si aucune politique n'avait jamais ete deposee. Personne ne pouvait
    le savoir depuis l'ecran.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except PermissionError as e:
        LOG.error("%s existe mais n'est pas lisible par %s : %s — "
                  "la politique NE SERA PAS appliquee et l'assistant manuel "
                  "va prendre la main. Attendu : une copie expurgee en 0644.",
                  path, _qui(), e)
        return {}
    except Exception as e:  # noqa: BLE001
        LOG.warning("failed to load %s: %s", path, e)
        return {}
    if isinstance(data, dict) and data.get("schema") == "bf-policy/v2":
        return data
    LOG.warning("%s present but not bf-policy/v2 ; ignoring", path)
    return {}


def _qui() -> str:
    try:
        import getpass
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "?"


def load_provisioning(path: Path = PROVISIONING_FILE,
                      public: Path | None = None) -> dict:
    """Load the staged bf-policy/v2 JSON, or {} if absent/invalid.

    Ordre de lecture : la copie EXPURGEE d'abord, parce que c'est la seule que
    la session peut lire ; la complete ensuite, pour les appelants root
    (bluefox-policy-sync). Une politique presente des deux cotes donne le meme
    bloc `session` — le seul ecart est le secret de liaison, dont l'agent n'a
    aucun usage.
    """
    # La copie expurgee est SOLIDAIRE du chemin demande : un appelant qui
    # pointe ailleurs (un test, un banc) ne doit pas se faire servir le fichier
    # systeme a son insu.
    if public is None:
        public = (PUBLIC_FILE if path == PROVISIONING_FILE
                  else path.parent / PUBLIC_FILE.name)
    data = _lire(public)
    if data:
        return data
    return _lire(path)


def user_login(prov: dict) -> str:
    return (prov.get("user", {}) or {}).get("login", "") if prov else ""


def session(prov: dict) -> dict:
    return (prov.get("session", {}) or {}) if prov else {}


def session_mounts(prov: dict) -> list:
    return session(prov).get("mounts", []) or []


def session_pwas(prov: dict) -> list:
    return session(prov).get("pwas", []) or []


def browser_extensions(prov: dict) -> list:
    """Extensions imposees a Brave par bf_policy (bloc `browser`, #25966)."""
    if not prov:
        return []
    return ((prov.get("browser", {}) or {}).get("extensions", []) or [])


# Services dont la politique peut donner l'adresse (#27109, phase 1). Liste
# blanche : une cle inconnue du bloc `services` n'entre pas dans le tenant.
SERVICES_DE_POLITIQUE = ("nextcloud", "vaultwarden", "authentik", "odoo")


def _url_https(valeur) -> str:
    """L'url si elle est https, avec un hote, sans blanc ni caractere de
    controle ; `/` final retire. Sinon "" (garder celle de l'image)."""
    if not isinstance(valeur, str) or not valeur:
        return ""
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in valeur):
        return ""
    try:
        parts = urlsplit(valeur)
        parts.port  # noqa: B018 — leve ValueError sur un port invalide
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ""
    return valeur.rstrip("/")


def _services_de_politique(prov: dict) -> dict:
    """{service: url} des services que la politique designe, filtres.

    ⚠️ POURQUOI (#27109) : une autre organisation sur l'image bf recevait sa
    politique, mais l'agent montait le Nextcloud ecrit dans l'image — celui de
    Blue Fox — et y aurait envoye le mot de passe d'application frappe pour
    SON Nextcloud. Une politique ancienne n'a que services.nextcloud ; une
    valeur vide, absente ou invalide laisse l'adresse de l'image en place.
    """
    services = (prov or {}).get("services")
    if not isinstance(services, dict):
        return {}
    retenus = {}
    for nom in SERVICES_DE_POLITIQUE:
        entree = services.get(nom)
        url = _url_https(entree.get("url")) if isinstance(entree, dict) else ""
        if url:
            retenus[nom] = url
        elif isinstance(entree, dict) and entree.get("url"):
            LOG.warning("services.%s.url ignoree (https avec un hote attendu) : "
                        "l'adresse de l'image reste en place", nom)
    return retenus


def _superposer_services(tenant: dict, prov: dict) -> dict:
    """Copie du tenant ou les services de la politique remplacent les url de
    l'image. Rend le tenant lui-meme s'il n'y a rien a superposer."""
    services = _services_de_politique(prov)
    if not services:
        return tenant
    merged = dict(tenant)
    tous = merged.get("services")
    tous = dict(tous) if isinstance(tous, dict) else {}
    for nom, url in services.items():
        entree = tous.get(nom)
        entree = dict(entree) if isinstance(entree, dict) else {}
        entree["url"] = url
        tous[nom] = entree
    merged["services"] = tous
    return merged


def merge_branding(tenant: dict, prov: dict) -> dict:
    """Overlay the policy onto a copy of the tenant dict.

    services.<nextcloud|vaultwarden|authentik|odoo>.url -> services.<...>.url ;
    accent_color -> branding.palette.accent ; wallpaper_url -> branding.wallpaper_url.
    Les services passent AVANT la sortie sans bloc session : une politique
    sans session designe quand meme son Nextcloud.
    The input tenant is left unchanged ; a new dict is returned.
    """
    tenant = _superposer_services(tenant, prov)
    sess = session(prov)
    if not sess:
        return tenant
    merged = dict(tenant)
    branding = dict(merged.get("branding", {}) or {})
    if sess.get("accent_color"):
        palette = dict(branding.get("palette", {}) or {})
        palette["accent"] = sess["accent_color"]
        branding["palette"] = palette
    if sess.get("wallpaper_url"):
        branding["wallpaper_url"] = sess["wallpaper_url"]
    merged["branding"] = branding
    return merged
