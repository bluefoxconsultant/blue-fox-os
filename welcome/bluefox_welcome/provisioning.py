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


def merge_branding(tenant: dict, prov: dict) -> dict:
    """Overlay the policy session branding onto a copy of the tenant dict.

    accent_color -> branding.palette.accent ; wallpaper_url -> branding.wallpaper_url.
    The input tenant is left unchanged ; a new dict is returned.
    """
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
