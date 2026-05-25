import pytest

from bluefox_welcome.auth.nc_login_flow import (
    LoginFlowError,
    acquire_nc_credentials,
    initiate,
    poll_once,
)

NC = "https://nc.example.com"

INIT_OK = (
    '{"poll":{"token":"T","endpoint":"https://nc.example.com/login/v2/poll"},'
    '"login":"https://nc.example.com/login/flow/abc"}'
)
POLL_OK = (
    '{"server":"https://nc.example.com","loginName":"olivier","appPassword":"APPPW"}'
)


def test_initiate_parses_response():
    def fake_post(url, data=None, timeout=15):
        assert url == NC + "/index.php/login/v2"
        return 200, INIT_OK

    login, endpoint, token = initiate(NC, post=fake_post)
    assert login.endswith("/login/flow/abc")
    assert endpoint.endswith("/login/v2/poll")
    assert token == "T"


def test_initiate_transport_error_raises():
    def fake_post(url, data=None, timeout=15):
        raise OSError("connection refused")

    with pytest.raises(LoginFlowError):
        initiate(NC, post=fake_post)


def test_initiate_bad_json_raises():
    def fake_post(url, data=None, timeout=15):
        return 200, "not json"

    with pytest.raises(LoginFlowError):
        initiate(NC, post=fake_post)


def test_poll_once_pending_returns_none_on_404_status():
    def fake_post(url, data=None, timeout=15):
        return 404, ""

    assert poll_once("ep", "tok", post=fake_post) is None


def test_poll_once_success_returns_creds():
    def fake_post(url, data=None, timeout=15):
        assert data == {"token": "tok"}
        return 200, POLL_OK

    assert poll_once("ep", "tok", post=fake_post) == ("olivier", "APPPW")


def test_poll_once_bad_json_raises():
    def fake_post(url, data=None, timeout=15):
        return 200, "{}"

    with pytest.raises(LoginFlowError):
        poll_once("ep", "tok", post=fake_post)


def test_acquire_polls_until_success():
    calls = {"poll": 0}

    def fake_post(url, data=None, timeout=15):
        if url.endswith("/index.php/login/v2"):
            return 200, INIT_OK
        calls["poll"] += 1
        if calls["poll"] < 3:
            return 404, ""
        return 200, POLL_OK

    opened = []
    name, pw = acquire_nc_credentials(
        NC, opener=opened.append, post=fake_post, sleep=lambda s: None,
        timeout=60, interval=0,
    )
    assert (name, pw) == ("olivier", "APPPW")
    assert opened == ["https://nc.example.com/login/flow/abc"]
    assert calls["poll"] == 3


def test_acquire_times_out():
    def fake_post(url, data=None, timeout=15):
        if url.endswith("/index.php/login/v2"):
            return 200, INIT_OK
        return 404, ""

    with pytest.raises(LoginFlowError):
        acquire_nc_credentials(
            NC, opener=lambda u: None, post=fake_post, sleep=lambda s: None,
            timeout=0, interval=0,
        )
