#!/usr/bin/env python3
"""Blue Fox OS install-time provisioning — runs in the Anaconda %pre stage.

BEFORE the install is underway, authenticate the operator to the org's Authentik
via the OAuth2 Device Authorization Grant (RFC 8628): no browser on the machine,
2FA preserved (the operator authorizes on a phone / second device). Then pull the
merged policy JSON from <org>/api/v1/policy/me and stage it for the %post applier
(bfos_apply.py) and the firstboot welcome agent.

Enrolment (#23909): while the operator's bearer is still in hand — the only
moment an authorized person is demonstrably in front of this machine — we also
POST to <org>/api/v1/policy/enroll and stage the per-machine secret it returns.
That secret is what lets the installed system re-fetch its policy on a timer,
with nobody present, so a policy edited in Odoo reaches machines that are
already installed. It reads that one policy and nothing else; see bf.policy.machine.

Config comes from the environment, exported by the kickstart %pre block from the
rendered template:
    BFOS_OIDC_DEVICE_URL   Authentik device authorization endpoint
    BFOS_OIDC_TOKEN_URL    Authentik token endpoint
    BFOS_OIDC_CLIENT_ID    public client id (e.g. 'blue-fox-os')
    BFOS_POLICY_URL        e.g. https://<domain>/api/v1/policy/me
    BFOS_ENROLL_URL        optional; defaults to the policy URL with /me → /enroll
    BFOS_FALLBACK_LANG / BFOS_FALLBACK_KEYMAP / BFOS_FALLBACK_TIMEZONE
                           org defaults used if the flow fails

Disk passphrase escrow (#23940): when the org asks for it AND its server says it
can store it, we draw the LUKS passphrase here rather than making someone type
one nobody records, deposit it in the same authenticated call as the enrolment,
and only then write it into the autopart line Anaconda includes. Odoo says no,
or never gets asked, and that include file keeps the prompting form the %pre
wrote before any of this ran. See stage_enrolment for why that order is the
whole safety argument.

Output: /tmp/bfos-provision.json (the policy, or a minimal fallback), when the
enrolment succeeded /tmp/bfos-machine.json (endpoint + machine secret, no disk
key), and /tmp/bfos-autopart.ks (the autopart line, with or without a generated
passphrase). The passphrase itself is never written anywhere else and never
reaches the installed system.

This NEVER aborts the install: any failure writes the fallback and exits 0, so
Anaconda proceeds with org defaults and the user can finish at firstboot. A
failed *enrolment* is milder still — the machine installs exactly as before,
it simply won't follow later policy changes on its own. The network is already
up (dracut fetched the kickstart over it).

stdlib-only by design — the Anaconda installer environment has no extra packages.
All I/O (post/get/sleep/console) is injectable so the logic is unit-testable.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MODE_PRIVATE = 0o600


def write_private(path, content):
    """Écrire un secret, propriétaire seul, dès le premier octet.

    `open(path, "w")` puis `os.chmod(path, 0o600)` laisse le fichier lisible
    par tout le monde entre les deux instructions — au umask standard 0022,
    0644. Ici ça vise la ligne autopart, qui porte la passphrase LUKS : elle
    n'a pas à transiter par un mode qu'on corrige ensuite. `os.open` fait
    appliquer le mode par le noyau à la création ; le fchmod ne couvre que le
    fichier déjà présent, dont O_CREAT ignore le mode et dont O_TRUNC ne
    remet pas les permissions.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, MODE_PRIVATE)
    try:
        os.fchmod(fd, MODE_PRIVATE)
        fh = os.fdopen(fd, "w")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        fh.write(content)


STAGED_JSON = "/tmp/bfos-provision.json"
MACHINE_JSON = "/tmp/bfos-machine.json"
SCOPE = "openid profile email"
CONSOLE = "/dev/console"
_MAX_WAIT = 900  # cap the device-flow wait at 15 min regardless of expires_in

# --- Disk passphrase escrow (#23940) ---------------------------------------
# The kickstart %include Anaconda reads its autopart line from. The %pre writes
# the prompting version FIRST, before anything can fail; we only ever overwrite
# it once Odoo has confirmed it holds the passphrase. Getting that order wrong
# is the one way to produce the failure that matters: a disk sealed with a
# passphrase nobody on earth possesses.
AUTOPART_INCLUDE = "/tmp/bfos-autopart.ks"
AUTOPART_BASE = "autopart --type=btrfs --encrypted --nohome"
# La variante EN CLAIR. Elle n'existe que pour le repli decide le 2026-09-11 :
# sans TPM, l'operateur choisit entre une phrase a taper a chaque demarrage et
# pas de chiffrement du tout. Ce second choix se prend les yeux ouverts.
AUTOPART_CLAIR = "autopart --type=btrfs --nohome"
# Marqueur lu par le %post --nochroot : « enrole le TPM avec la phrase que je
# viens de tirer ». Explicite, parce que deduire l'intention de la presence
# d'un TPM confondrait le cas ou l'operateur l'a refuse.
TPM_MARKER = "/tmp/bfos-tpm-enrol"
# Crockford's base32 alphabet: digits and uppercase letters minus I, L, O and U.
# The first three are the shapes people mistype reading a key off a screen
# (I/1, L/1, O/0); U goes because excluding it is what keeps a random string
# from spelling something someone has to read out loud. 30 symbols, 25 of them
# = ~122 bits.
# Layout-safe on purpose: A-Z, 0-9 and the hyphen sit identically on the
# Canadian CSA layout we ship and on US QWERTY, so the passphrase is typeable
# at the LUKS prompt whatever keymap the policy set.
_PASSPHRASE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_PASSPHRASE_GROUPS = 5
_PASSPHRASE_GROUP_LEN = 5


class ProvisionError(Exception):
    pass


def _post_form(url, data, timeout=30):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def _get(url, token, timeout=30):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def device_authorize(device_url, client_id, post=_post_form):
    """Start the device flow; returns the device authorization response dict."""
    try:
        status, raw = post(device_url, {"client_id": client_id, "scope": SCOPE})
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"device authorization request failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"device authorization HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"device authorization bad JSON: {exc}") from exc
    if "device_code" not in d:
        raise ProvisionError("device authorization missing device_code")
    return d


def announce(d, out):
    """Print the verification URL + user code to the install console."""
    uri = d.get("verification_uri", "")
    uri_complete = d.get("verification_uri_complete") or uri
    out(
        "\n==================== Blue Fox OS ====================\n"
        " Authentifiez cette installation :\n"
        f"   1. Sur un autre appareil, ouvrez : {uri}\n"
        f"   2. Entrez le code : {d.get('user_code', '?')}\n"
        f"   (ou directement : {uri_complete})\n"
        " En attente d'autorisation (2FA incluse)...\n"
        "=====================================================\n"
    )


def poll_token(token_url, client_id, device_code, interval, expires_in,
               post=_post_form, sleep=time.sleep, now=time.monotonic):
    """Poll the token endpoint until the operator authorizes, or time out."""
    deadline = now() + min(int(expires_in), _MAX_WAIT)
    wait = max(int(interval), 1)
    grant = "urn:ietf:params:oauth:grant-type:device_code"
    while now() < deadline:
        sleep(wait)
        try:
            status, raw = post(token_url, {
                "grant_type": grant,
                "device_code": device_code,
                "client_id": client_id,
            })
        except urllib.error.HTTPError as exc:
            err = _error_code(exc)
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                wait += 5
                continue
            raise ProvisionError(f"token error: {err or exc.code}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ProvisionError(f"token request failed: {exc}") from exc
        try:
            d = json.loads(raw)
        except ValueError as exc:
            raise ProvisionError(f"token response bad JSON: {exc}") from exc
        if d.get("access_token"):
            return d["access_token"]
        err = d.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            wait += 5
            continue
        if err:
            raise ProvisionError(f"token error: {err}")
    raise ProvisionError("device authorization timed out")


def _error_code(http_error):
    try:
        return json.loads(http_error.read().decode()).get("error")
    except Exception:  # noqa: BLE001
        return None


def _valid_policy(d) -> bool:
    """Structural sanity check on a policy payload (no jsonschema in Anaconda).
    A wrong-shaped response is rejected so the caller falls back to org defaults
    rather than staging a malformed policy for the root-level %post applier."""
    return (isinstance(d, dict) and d.get("schema") == "bf-policy/v2"
            and isinstance(d.get("install"), dict)
            and isinstance(d.get("user"), dict))


def fetch_policy(policy_url, token, get=_get):
    try:
        status, raw = get(policy_url, token)
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"policy fetch failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"policy fetch HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"policy response bad JSON: {exc}") from exc
    if not _valid_policy(d):
        raise ProvisionError("policy response failed bf-policy/v2 schema check")
    return d


def _post_json(url, payload, token, timeout=30):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def enroll_url_from(policy_url) -> str:
    """Derive the enrolment endpoint from the policy endpoint.

    They are two routes of the same controller, so deriving beats carrying a
    second placeholder through both kickstart renderers — one less value that
    can drift. An unrecognised policy URL yields "" and the caller skips
    enrolment rather than posting the operator's bearer somewhere unintended.
    """
    derived = re.sub(r"/me/?$", "/enroll", (policy_url or "").strip())
    return derived if derived.endswith("/enroll") else ""


def image_version(path="/etc/os-release") -> str:
    """Best-effort label for what this machine was installed from.

    Cosmetic — it only ever lands in the machine's Odoo record, to tell apart a
    fleet installed from different ISOs. Unreadable file → empty string.
    """
    fields = {}
    try:
        with open(path) as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if _:
                    fields[key.strip()] = value.strip().strip('"')
    except OSError:
        return ""
    version = fields.get("VERSION_ID", "")
    build = fields.get("BUILD_ID") or fields.get("IMAGE_VERSION") or ""
    return f"{version} ({build})".strip() if build else version


def generate_disk_passphrase(rng=None):
    """Draw the LUKS passphrase this machine will be sealed with.

    Grouped like a recovery key rather than run together: the one moment this
    string is read out loud or copied off a screen is a bad moment, and groups
    of five are what make that survivable.
    """
    choice = (rng or secrets.choice)
    groups = [
        "".join(choice(_PASSPHRASE_ALPHABET)
                for _ in range(_PASSPHRASE_GROUP_LEN))
        for _ in range(_PASSPHRASE_GROUPS)
    ]
    return "-".join(groups)


def escrow_requested(policy) -> bool:
    """True when the org wants the installer to draw and deposit the passphrase.

    Two conditions, both from the server: the org turned escrow on, AND that
    server can actually store it (a key is configured). Without the second we
    must not draw a passphrase at all — asking Odoo to keep something it has no
    way to keep is exactly how a disk ends up sealed against everyone.

    Every level is type-checked rather than assumed. `_valid_policy` gates
    `schema`, `install` and `user` — it never looks at `policies`, so a server
    that answers with a list or a string there reaches this function intact,
    and `.get` on a non-mapping raises. Answering False to a malformed policy
    is the safe reading: no passphrase drawn, Anaconda prompts.
    """
    if not isinstance(policy, dict):
        return False
    policies = policy.get("policies")
    if not isinstance(policies, dict):
        return False
    block = policies.get("disk_escrow")
    if not isinstance(block, dict):
        return False
    return bool(block.get("enabled")) and bool(block.get("available"))


def tpm2_present(chemins=("/dev/tpmrm0", "/dev/tpm0")) -> bool:
    """Y a-t-il un TPM2 utilisable dans cette machine ?

    On regarde le noeud de peripherique plutot que `systemd-analyze has-tpm2` :
    l'environnement de l'installateur est reduit, et un outil absent rendrait
    « pas de TPM » pour une mauvaise raison. Un noeud present et illisible
    serait un cas tordu ; l'enrolement echouerait alors bruyamment au %post,
    ce qui est le bon endroit pour l'apprendre.
    """
    return any(os.path.exists(c) for c in chemins)


def decider_chiffrement(out, tpm=None, lire=None) -> tuple:
    """Rend (chiffrer, enroler_tpm). Decision prise AVANT l'enrolement.

    ⚠️ L'ordre compte : decider apres coup reviendrait a sequestrer une phrase
    pour un disque qu'on s'apprete a laisser en clair, et la fiche machine
    mentirait.

    Avec TPM : on chiffre et on enrole a l'installation, sans rien demander.
    C'est possible depuis le sequestre (#23940) et ca ne l'etait pas avant —
    `systemd-cryptenroll` exige la phrase existante, et l'installateur la tire
    desormais lui-meme.

    Sans TPM : on demande. Le defaut, sur toute reponse inattendue comme sur
    une entree fermee, est de CHIFFRER : c'est la seule direction ou se tromper
    ne coute que du confort.
    """
    lire = lire or (lambda: sys.stdin.readline())
    if tpm is None:
        tpm = tpm2_present()
    if tpm:
        return True, True
    out("\n[bfos] Aucun TPM2 sur cette machine.\n"
        "       1) chiffrer le disque — la phrase est tiree et sequestree dans\n"
        "          Odoo, et il faudra la TAPER A CHAQUE DEMARRAGE ;\n"
        "       2) ne pas chiffrer — rien a taper, et un disque vole se lit.\n"
        "       Choix [1] : ")
    try:
        reponse = (lire() or "").strip()
    except Exception:  # noqa: BLE001 — entree fermee, install pilotee
        reponse = ""
    if reponse == "2":
        out("[bfos] disque NON chiffre, a la demande de l'operateur\n")
        return False, False
    out("[bfos] disque chiffre, sans deverrouillage automatique\n")
    return True, False


def write_autopart(passphrase=None, path=None, chiffrer=True):
    """Write the autopart line Anaconda includes, and lock it down.

    No passphrase: the prompting form, byte-identical to what the template
    carried before this existed. With one: the same line plus --passphrase.
    0600 because for the length of the install that file IS the disk key.
    """
    path = path or AUTOPART_INCLUDE
    if not chiffrer:
        # Aucune phrase ici, et il ne faut surtout pas en accepter une : un
        # appelant qui passerait les deux se contredit.
        write_private(path, AUTOPART_CLAIR + "\n")
        return path
    line = AUTOPART_BASE
    if passphrase:
        line = f"{AUTOPART_BASE} --passphrase={passphrase}"
    # Pas de `except OSError: pass` ici : un chmod avalé en silence laissait la
    # clé du disque en 0644 sans que rien ne le dise.
    write_private(path, line + "\n")
    return path


def enrol_machine(enroll_url, token, policy, post=_post_json,
                  new_uuid=None, os_version=None, disk_passphrase=""):
    """Register this machine and return the staged dict, or raise ProvisionError.

    The UUID is drawn here, not by the server: it is the machine's own handle on
    its record, and drawing it locally means a re-run of the same install (same
    staged file) rotates that record's token instead of piling up a second one.
    A reinstall draws a fresh one — the two records coexist and `last_seen` is
    what tells them apart.
    """
    machine_uuid = str((new_uuid or uuid.uuid4)())
    hostname = (policy.get("install", {}) or {}).get("hostname", "") or ""
    payload = {
        "machine_uuid": machine_uuid,
        "hostname": hostname,
        "os_version": image_version() if os_version is None else os_version,
    }
    if disk_passphrase:
        payload["disk_passphrase"] = disk_passphrase
    try:
        status, raw = post(enroll_url, payload, token)
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"enrolment request failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"enrolment HTTP {status}")
    try:
        d = json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"enrolment bad JSON: {exc}") from exc
    if not isinstance(d, dict) or not d.get("token") or not d.get("endpoint"):
        raise ProvisionError("enrolment response missing token/endpoint")
    return {
        "schema": "bf-machine/v1",
        "machine_uuid": d.get("machine_uuid") or machine_uuid,
        "machine_id": d.get("machine_id"),
        # Endpoint served by the org itself, so a tenant can move its policy
        # plane without us guessing the URL from the install-time one.
        "endpoint": d["endpoint"],
        "token": d["token"],
        "hostname": d.get("hostname") or hostname,
        "user": d.get("user", ""),
        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Odoo's answer to "do you hold this passphrase?". ⚠️ The flag, never
        # the passphrase: this dict is what gets written to /etc/bluefox/
        # machine.json on the installed system, and the disk key has no
        # business surviving the install.
        "disk_escrowed": bool(d.get("disk_escrowed")),
        "disk_escrow_error": str(d.get("disk_escrow_error") or "")[:200],
    }


def fallback_policy(env=None):
    env = env if env is not None else os.environ
    return {
        "schema": "bf-policy/v2",
        "org": {"company": "", "domain": ""},
        "user": {"login": ""},
        "install": {
            "locale": env.get("BFOS_FALLBACK_LANG", "fr_CA.UTF-8"),
            "keymap": env.get("BFOS_FALLBACK_KEYMAP", "ca"),
            "timezone": env.get("BFOS_FALLBACK_TIMEZONE", "America/Montreal"),
            "hostname": "blue-fox-os",
            "root": "locked",
            "login": {"mode": "local", "ldap_uri": "", "ldap_base_dn": ""},
        },
        "policies": {
            "offline_login": {"enabled": True, "max_offline_days": 7},
            "mfa_required": True,
            "auto_lock_minutes": 15,
        },
        "session": {"accent_color": "#29ABE1", "wallpaper_url": "",
                    "mounts": [], "pwas": []},
        "fallback": True,
    }


def _console_writer():
    try:
        fh = open(CONSOLE, "w")
    except Exception:  # noqa: BLE001
        return lambda msg: print(msg, file=sys.stderr)

    def write(msg):
        try:
            fh.write(msg)
            fh.flush()
        except Exception:  # noqa: BLE001
            pass
        print(msg, file=sys.stderr)

    return write


def run(env=None, post=_post_form, get=_get, sleep=time.sleep, out=None,
        after_policy=None):
    """Full device flow → policy fetch. Returns the policy dict or raises.

    `after_policy(access_token, policy)` runs while the operator's bearer is
    still valid — that is the enrolment window, and the only one. It must not
    raise: the policy is already in hand at that point, and nothing about it
    depends on the enrolment succeeding.
    """
    env = env if env is not None else os.environ
    out = out or _console_writer()
    device_url = env.get("BFOS_OIDC_DEVICE_URL", "")
    token_url = env.get("BFOS_OIDC_TOKEN_URL", "")
    client_id = env.get("BFOS_OIDC_CLIENT_ID", "")
    policy_url = env.get("BFOS_POLICY_URL", "")
    if not all([device_url, token_url, client_id, policy_url]):
        raise ProvisionError("missing BFOS_OIDC_* / BFOS_POLICY_URL config")
    d = device_authorize(device_url, client_id, post=post)
    announce(d, out)
    token = poll_token(
        token_url, client_id, d["device_code"],
        d.get("interval", 5), d.get("expires_in", 300),
        post=post, sleep=sleep)
    policy = fetch_policy(policy_url, token, get=get)
    if after_policy is not None:
        after_policy(token, policy)
    return policy


def stage_enrolment(token, policy, env=None, out=None, post=_post_json,
                    path=None, autopart_path=None, gen=None, decider=None,
                    marqueur_tpm=None):
    """Enrol this machine, stage the secret, and settle the disk passphrase.

    ⚠️ This is the `after_policy` seam, and `run()` documents that it must not
    raise. Before #23940 that held by construction: every statement lived under
    a try. The escrow added three bare calls, and any of them raising would
    have travelled up through `run()` to `main()`, which treats one exception
    as total failure and throws away a policy that was already in hand — the
    machine would then install on org fallbacks, with no enrolment, over a
    successful fetch. The guard below restores the contract rather than trusting
    each new statement to be safe.

    Returns the staged dict, or None when enrolment was skipped or failed. A
    machine that fails to enrol installs exactly as before — it just won't
    follow later policy changes on its own, which is a degradation, not a
    breakage, and the console says so.

    The disk passphrase rides the same authenticated window (#23940), and the
    ORDER here is the whole safety argument: we draw a passphrase, we ask Odoo
    to keep it, and we only tell Anaconda to use it once Odoo has said yes.
    Every other outcome — escrow off, no key on the server, enrolment failed,
    deposit refused — leaves the include file exactly as the %pre wrote it, so
    Anaconda prompts and a human ends up holding the key, which is where we
    started. There is no path that seals a disk against everyone.
    """
    out = out or (lambda msg: None)
    try:
        return _stage_enrolment(token, policy, env=env, out=out, post=post,
                                path=path, autopart_path=autopart_path,
                                gen=gen, decider=decider,
                                marqueur_tpm=marqueur_tpm)
    except Exception as exc:  # noqa: BLE001 — the contract is: never raise
        out(f"[bfos] the enrolment step failed ({exc}); the policy already "
            "fetched is kept and the install goes on. Anaconda will ask for "
            "a disk passphrase.\n")
        return None


def _stage_enrolment(token, policy, env=None, out=None, post=_post_json,
                     path=None, autopart_path=None, gen=None, decider=None,
                     marqueur_tpm=None):
    """The body of stage_enrolment. Kept apart so the guard above is the only
    way in, and so nothing added here can quietly break the no-raise contract.
    """
    env = env if env is not None else os.environ
    out = out or (lambda msg: None)
    path = path or MACHINE_JSON
    url = env.get("BFOS_ENROLL_URL", "") or enroll_url_from(
        env.get("BFOS_POLICY_URL", ""))
    if not url:
        out("[bfos] no enrolment endpoint; this machine will not follow later "
            "policy changes\n")
        return None

    # ⚠️ La decision de chiffrer se prend AVANT l'enrolement : la prendre apres
    # reviendrait a sequestrer une phrase pour un disque laisse en clair, et la
    # fiche machine d'Odoo mentirait sur ce que porte la machine.
    decider = decider or decider_chiffrement
    chiffrer, enroler_tpm = decider(out)
    if not chiffrer:
        write_autopart(path=autopart_path, chiffrer=False)

    passphrase = ""
    if chiffrer and escrow_requested(policy):
        passphrase = generate_disk_passphrase(rng=gen)

    try:
        machine = enrol_machine(url, token, policy, post=post,
                                disk_passphrase=passphrase)
    except Exception as exc:  # noqa: BLE001 — never abort the install
        out(f"[bfos] enrolment failed ({exc}); this machine will not follow "
            "later policy changes\n")
        if passphrase:
            # ⚠️ Do NOT claim the deposit failed. We know we got no answer; we
            # do not know what the server did. Odoo commits the escrow before
            # it replies, so a lost return leg leaves a record saying this
            # machine is escrowed while the operator is about to seal the disk
            # with a passphrase of their own. The person reading this screen is
            # the only one placed to notice, and telling them "NOT deposited"
            # is precisely what would stop them looking.
            out("[bfos] no answer on the disk passphrase deposit: it may or "
                "may not have been recorded. Anaconda will ask for one, and "
                "that typed passphrase is the real one. If Odoo lists this "
                "machine as escrowed, that record is wrong — clear it.\n")
        return None

    if passphrase:
        if machine.get("disk_escrowed"):
            write_autopart(passphrase, path=autopart_path)
            out("[bfos] disk passphrase drawn and deposited in Odoo; this "
                "install will not ask for one\n")
            if enroler_tpm:
                # Le %post --nochroot lit ce marqueur ET la phrase dans
                # /tmp/bfos-autopart.ks. Un marqueur explicite plutot qu'une
                # deduction : « il y a un TPM et la ligne porte une phrase »
                # serait vrai aussi quand l'operateur a refuse l'enrolement.
                try:
                    write_private(marqueur_tpm or TPM_MARKER, "")
                    out("[bfos] TPM2 present : le disque sera enrole a "
                        "l'installation, sans invite au demarrage\n")
                except OSError as exc:
                    out(f"[bfos] marqueur TPM non ecrit ({exc}); le disque "
                        "demandera sa phrase a chaque demarrage\n")
        else:
            reason = machine.get("disk_escrow_error") or "reason unknown"
            out(f"[bfos] disk passphrase NOT deposited ({reason}); Anaconda "
                "will ask for one\n")
    try:
        # Le secret de la machine. 0600 AVANT tout, et jamais journalise —
        # ce que le commentaire promettait deja, mais que le chmod POSTERIEUR
        # ne rendait pas.
        write_private(path, json.dumps(machine))
    except OSError as exc:
        out(f"[bfos] could not stage the machine secret ({exc})\n")
        return None
    out(f"[bfos] machine enrolled as {machine['hostname']} "
        f"({machine['machine_uuid']})\n")
    return machine


def main(argv=None):
    out = _console_writer()
    try:
        policy = run(out=out,
                     after_policy=lambda tok, pol: stage_enrolment(
                         tok, pol, out=out))
        out("[bfos] policy received for user "
            f"{policy.get('user', {}).get('login', '?')}\n")
    except Exception as exc:  # noqa: BLE001 — never abort the install
        out(f"[bfos] provisioning failed ({exc}); using org fallback defaults\n")
        policy = fallback_policy()
    # The staged policy carries the operator login + LDAP endpoints (no token),
    # so keep it owner-only rather than the installer's default umask (0o644).
    write_private(STAGED_JSON, json.dumps(policy))
    return 0


if __name__ == "__main__":
    sys.exit(main())
