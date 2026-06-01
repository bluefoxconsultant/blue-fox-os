import io
import json
import urllib.error

import pytest

import bfos_provision as bp

DEVICE_URL = "https://auth.example.com/application/o/device/"
TOKEN_URL = "https://auth.example.com/application/o/token/"
POLICY_URL = "https://example.com/api/v1/policy/me"


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(body.encode()))


def _pending():
    raise _http_error(400, '{"error":"authorization_pending"}')


def _slow_down():
    raise _http_error(400, '{"error":"slow_down"}')


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


# ---------------------------------------------------------------- device_authorize
def test_device_authorize_ok():
    def post(url, data=None, timeout=30):
        assert url == DEVICE_URL
        assert data["client_id"] == "blue-fox-os"
        return 200, json.dumps({"device_code": "DC", "user_code": "WXYZ-ABCD",
                                "verification_uri": "https://auth/device",
                                "interval": 5, "expires_in": 300})
    d = bp.device_authorize(DEVICE_URL, "blue-fox-os", post=post)
    assert d["device_code"] == "DC"


def test_device_authorize_missing_code_raises():
    def post(url, data=None, timeout=30):
        return 200, json.dumps({"user_code": "X"})
    with pytest.raises(bp.ProvisionError):
        bp.device_authorize(DEVICE_URL, "c", post=post)


# ---------------------------------------------------------------- poll_token
def test_poll_token_success_after_pending():
    responses = [_pending, _pending,
                 (200, json.dumps({"access_token": "TOK"}))]
    it = iter(responses)

    def post(url, data=None, timeout=30):
        item = next(it)
        return item() if callable(item) else item

    clock = Clock()
    tok = bp.poll_token(TOKEN_URL, "c", "DC", interval=1, expires_in=300,
                        post=post, sleep=clock.sleep, now=clock.now)
    assert tok == "TOK"


def test_poll_token_slow_down_increases_wait():
    responses = [_slow_down, (200, json.dumps({"access_token": "TOK"}))]
    it = iter(responses)

    def post(url, data=None, timeout=30):
        item = next(it)
        return item() if callable(item) else item

    clock = Clock()
    bp.poll_token(TOKEN_URL, "c", "DC", interval=2, expires_in=1000,
                  post=post, sleep=clock.sleep, now=clock.now)
    # first sleep at base interval, second sleep bumped by slow_down (+5)
    assert clock.sleeps == [2, 7]


def test_poll_token_timeout():
    def post(url, data=None, timeout=30):
        _pending()

    clock = Clock()
    with pytest.raises(bp.ProvisionError):
        bp.poll_token(TOKEN_URL, "c", "DC", interval=5, expires_in=10,
                      post=post, sleep=clock.sleep, now=clock.now)


# ---------------------------------------------------------------- fetch_policy
def test_fetch_policy_ok():
    def get(url, token, timeout=30):
        assert token == "TOK"
        return 200, json.dumps({"schema": "bf-policy/v2",
                                "install": {}, "user": {"login": "x"}})
    assert bp.fetch_policy(POLICY_URL, "TOK", get=get)["schema"] == "bf-policy/v2"


def test_fetch_policy_non200_raises():
    def get(url, token, timeout=30):
        return 403, "nope"
    with pytest.raises(bp.ProvisionError):
        bp.fetch_policy(POLICY_URL, "TOK", get=get)


def test_fetch_policy_rejects_wrong_shape():
    def get(url, token, timeout=30):
        return 200, json.dumps({"schema": "bf-policy/v2"})  # no install/user
    with pytest.raises(bp.ProvisionError):
        bp.fetch_policy(POLICY_URL, "TOK", get=get)


# ---------------------------------------------------------------- run / main
def test_run_happy_path():
    def post(url, data=None, timeout=30):
        if url == DEVICE_URL:
            return 200, json.dumps({"device_code": "DC", "user_code": "WX",
                                    "verification_uri": "https://auth/device",
                                    "interval": 1, "expires_in": 300})
        if url == TOKEN_URL:
            return 200, json.dumps({"access_token": "TOK"})
        raise AssertionError(url)

    def get(url, token, timeout=30):
        assert (url, token) == (POLICY_URL, "TOK")
        return 200, json.dumps({"schema": "bf-policy/v2", "install": {},
                                "user": {"login": "olivier"}})

    env = {"BFOS_OIDC_DEVICE_URL": DEVICE_URL, "BFOS_OIDC_TOKEN_URL": TOKEN_URL,
           "BFOS_OIDC_CLIENT_ID": "blue-fox-os", "BFOS_POLICY_URL": POLICY_URL}
    policy = bp.run(env=env, post=post, get=get, sleep=lambda s: None,
                    out=lambda m: None)
    assert policy["user"]["login"] == "olivier"


def test_run_missing_config_raises():
    with pytest.raises(bp.ProvisionError):
        bp.run(env={}, out=lambda m: None)


def test_main_writes_fallback_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "STAGED_JSON", str(tmp_path / "p.json"))
    for var in ("BFOS_OIDC_DEVICE_URL", "BFOS_OIDC_TOKEN_URL",
                "BFOS_OIDC_CLIENT_ID", "BFOS_POLICY_URL"):
        monkeypatch.delenv(var, raising=False)
    assert bp.main([]) == 0
    data = json.loads((tmp_path / "p.json").read_text())
    assert data["fallback"] is True
    assert data["install"]["root"] == "locked"


def test_fallback_policy_uses_env():
    env = {"BFOS_FALLBACK_LANG": "en_CA.UTF-8", "BFOS_FALLBACK_KEYMAP": "us"}
    fb = bp.fallback_policy(env=env)
    assert fb["install"]["locale"] == "en_CA.UTF-8"
    assert fb["install"]["keymap"] == "us"


def test_main_chmods_staged_file(tmp_path, monkeypatch):
    staged = tmp_path / "p.json"
    monkeypatch.setattr(bp, "STAGED_JSON", str(staged))
    for var in ("BFOS_OIDC_DEVICE_URL", "BFOS_OIDC_TOKEN_URL",
                "BFOS_OIDC_CLIENT_ID", "BFOS_POLICY_URL"):
        monkeypatch.delenv(var, raising=False)
    assert bp.main([]) == 0
    assert oct(staged.stat().st_mode)[-3:] == "600"
