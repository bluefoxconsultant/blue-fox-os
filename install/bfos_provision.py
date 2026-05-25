#!/usr/bin/env python3
"""Blue Fox OS install-time provisioning — runs in the Anaconda %pre stage.

BEFORE the install is underway, authenticate the operator to the org's Authentik
via the OAuth2 Device Authorization Grant (RFC 8628): no browser on the machine,
2FA preserved (the operator authorizes on a phone / second device). Then pull the
merged policy JSON from <org>/api/v1/policy/me and stage it for the %post applier
(bfos_apply.py) and the firstboot welcome agent.

Config comes from the environment, exported by the kickstart %pre block from the
rendered template:
    BFOS_OIDC_DEVICE_URL   Authentik device authorization endpoint
    BFOS_OIDC_TOKEN_URL    Authentik token endpoint
    BFOS_OIDC_CLIENT_ID    public client id (e.g. 'blue-fox-os')
    BFOS_POLICY_URL        e.g. https://<domain>/api/v1/policy/me
    BFOS_FALLBACK_LANG / BFOS_FALLBACK_KEYMAP / BFOS_FALLBACK_TIMEZONE
                           org defaults used if the flow fails

Output: /tmp/bfos-provision.json (the policy, or a minimal fallback).

This NEVER aborts the install: any failure writes the fallback and exits 0, so
Anaconda proceeds with org defaults and the user can finish at firstboot. The
network is already up (dracut fetched the kickstart over it).

stdlib-only by design — the Anaconda installer environment has no extra packages.
All I/O (post/get/sleep/console) is injectable so the logic is unit-testable.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STAGED_JSON = "/tmp/bfos-provision.json"
SCOPE = "openid profile email"
CONSOLE = "/dev/console"
_MAX_WAIT = 900  # cap the device-flow wait at 15 min regardless of expires_in


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


def fetch_policy(policy_url, token, get=_get):
    try:
        status, raw = get(policy_url, token)
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"policy fetch failed: {exc}") from exc
    if status != 200:
        raise ProvisionError(f"policy fetch HTTP {status}")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ProvisionError(f"policy response bad JSON: {exc}") from exc


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


def run(env=None, post=_post_form, get=_get, sleep=time.sleep, out=None):
    """Full device flow → policy fetch. Returns the policy dict or raises."""
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
    return fetch_policy(policy_url, token, get=get)


def main(argv=None):
    out = _console_writer()
    try:
        policy = run(out=out)
        out("[bfos] policy received for user "
            f"{policy.get('user', {}).get('login', '?')}\n")
    except Exception as exc:  # noqa: BLE001 — never abort the install
        out(f"[bfos] provisioning failed ({exc}); using org fallback defaults\n")
        policy = fallback_policy()
    with open(STAGED_JSON, "w") as fh:
        json.dump(policy, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
