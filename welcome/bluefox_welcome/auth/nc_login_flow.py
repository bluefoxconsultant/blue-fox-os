"""Nextcloud Login Flow v2 — SSO-compatible app-password acquisition.

The official desktop-client login protocol: POST /index.php/login/v2 to start a
flow, open the returned login URL in the browser (where the user authenticates
via the existing Authentik SSO button — *no password typed into the wizard*),
then poll until Nextcloud returns a durable app-password.

This is the v1 mechanism for obtaining the rclone / KAccounts credential. It
breaks the chicken-and-egg flagged in apply/kaccounts.py: the browser SSO login
is what authorizes minting the app-password, so OIDC users never type a password
here, and we avoid the user_oidc bearer-token audience pitfalls entirely.

Ref: https://docs.nextcloud.com/server/latest/developer_manual/client_apis/LoginFlow/index.html#login-flow-v2

All network I/O goes through the injectable `post` / `opener` / `sleep` seams so
the flow is unit-testable without a live Nextcloud or a browser.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

LOG = logging.getLogger("bluefox-welcome.auth.nc_login_flow")

USER_AGENT = "Blue Fox OS Welcome"
DEFAULT_POLL_TIMEOUT = 300  # seconds the user has to complete the browser login
DEFAULT_POLL_INTERVAL = 2.0


class LoginFlowError(Exception):
    """Raised when the login flow cannot be initiated or completed."""


def _post(url: str, data: dict | None = None, timeout: int = 15) -> tuple[int, str]:
    """POST helper returning (status, body). Raises urllib HTTPError on 4xx/5xx."""
    body = urllib.parse.urlencode(data).encode() if data else b""
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def initiate(nc_url: str, post=_post) -> tuple[str, str, str]:
    """Start a Login Flow v2. Returns (login_url, poll_endpoint, poll_token)."""
    endpoint = nc_url.rstrip("/") + "/index.php/login/v2"
    try:
        status, raw = post(endpoint)
    except Exception as e:  # noqa: BLE001 — surface any transport error uniformly
        raise LoginFlowError(f"initiation impossible ({endpoint}): {e}") from e
    if status != 200:
        raise LoginFlowError(f"initiation HTTP {status}")
    try:
        d = json.loads(raw)
        return d["login"], d["poll"]["endpoint"], d["poll"]["token"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise LoginFlowError(f"réponse d'initiation invalide: {e}") from e


def poll_once(poll_endpoint: str, poll_token: str, post=_post):
    """One poll. Returns (login_name, app_password) on success, None if pending.

    Nextcloud answers 404 while the user has not finished authenticating; that is
    a normal "keep waiting" signal, not an error.
    """
    try:
        status, raw = post(poll_endpoint, data={"token": poll_token})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise LoginFlowError(f"poll HTTP {e.code}") from e
    except LoginFlowError:
        raise
    except Exception as e:  # noqa: BLE001
        raise LoginFlowError(f"poll échoué: {e}") from e
    if status == 404:
        return None
    if status != 200:
        raise LoginFlowError(f"poll HTTP {status}")
    try:
        d = json.loads(raw)
        return d["loginName"], d["appPassword"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise LoginFlowError(f"réponse de poll invalide: {e}") from e


def acquire_nc_credentials(
    nc_url: str,
    opener=webbrowser.open,
    post=_post,
    sleep=time.sleep,
    timeout: float = DEFAULT_POLL_TIMEOUT,
    interval: float = DEFAULT_POLL_INTERVAL,
) -> tuple[str, str]:
    """Full blocking flow: initiate → open browser → poll until creds or timeout.

    Returns (login_name, app_password). Raises LoginFlowError on failure/timeout.
    Used by the CLI / headless path ; the Qt wizard drives initiate()+poll_once()
    on a timer instead so the UI stays responsive.
    """
    login_url, poll_endpoint, poll_token = initiate(nc_url, post=post)
    LOG.info("login flow started ; opening browser")
    try:
        opener(login_url)
    except Exception as e:  # noqa: BLE001 — browser launch is best-effort
        LOG.warning("could not open browser automatically: %s", e)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = poll_once(poll_endpoint, poll_token, post=post)
        if result is not None:
            LOG.info("login flow completed for %s", result[0])
            return result
        sleep(interval)
    raise LoginFlowError("délai dépassé en attente de la connexion SSO")
