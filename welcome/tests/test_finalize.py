"""Tests for _finalize_and_apply — the apply_* fan-out + DONE_FLAG bookkeeping
that runs once a flow is accepted. Both the manual wizard and the provisioned
flow funnel through this function. Qt-free: no wizard is constructed here.

Current contract (BFOSI10 / SSO Login Flow v2):
- The NC mount credential is a (login_name, app_password) *pair* obtained from
  the SSO Login Flow v2, not a typed password.
- The fan-out is five integrations: exactly one of apply_rclone_mount /
  apply_session_mounts (the latter when the staged policy carries
  session.mounts[]), plus bitwarden_prefs, brave_policy, kaccounts, kde_theme.
- Each apply_* is best-effort and returns (ok, msg); one returning a failure
  must not abort the others, and DONE_FLAG must still be written.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bluefox_welcome import main as main_mod

POLICY_WITH_SESSION = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "session": {
        "mounts": [{"name": "Partages", "remote": "nc:Partages",
                    "local": "~/Partages"}],
        "pwas": ["https://talk.bluefoxconsultant.com"],
    },
}

_APPLY_NAMES = (
    "apply_rclone_mount", "apply_session_mounts", "apply_bitwarden_prefs",
    "apply_brave_policy", "apply_kaccounts", "apply_kde_theme",
)


@pytest.fixture
def applies(monkeypatch):
    """Replace the six apply_* in main with mocks returning (True, '<name> ok')."""
    mocks = {n: MagicMock(return_value=(True, f"{n} ok")) for n in _APPLY_NAMES}
    for name, m in mocks.items():
        monkeypatch.setattr(main_mod, name, m)
    return mocks


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect the state / log / config paths into tmp_path."""
    state = tmp_path / "var-lib"
    monkeypatch.setattr(main_mod, "STATE_DIR", state)
    monkeypatch.setattr(main_mod, "DONE_FLAG", state / "done")
    monkeypatch.setattr(main_mod, "USER_LOG", tmp_path / "share" / "firstboot.log")
    monkeypatch.setattr(main_mod, "USER_CONFIG_DIR", tmp_path / "config")
    return state


def _finalize(**over):
    """Call _finalize_and_apply with sensible defaults (do_mount + valid creds,
    no staged policy), overridable per test."""
    kw = dict(
        tenant={"slug": "bf"},
        user_email="alice@bluefoxconsultant.com",
        do_mount=True,
        nc_login_name="alice",
        nc_app_password="app-pw-xyz",
        prov={},
    )
    kw.update(over)
    main_mod._finalize_and_apply(**kw)


def test_single_rclone_mount_when_no_policy_mounts(applies, state_dir):
    """do_mount + creds + no session.mounts -> the single ~/Nextcloud mount."""
    _finalize()
    assert applies["apply_rclone_mount"].call_count == 1
    assert applies["apply_session_mounts"].call_count == 0
    for name in ("apply_bitwarden_prefs", "apply_brave_policy",
                 "apply_kaccounts", "apply_kde_theme"):
        assert applies[name].call_count == 1, name
    # the SSO pair is threaded through to the mount
    kw = applies["apply_rclone_mount"].call_args.kwargs
    assert kw["user"] == "alice" and kw["password"] == "app-pw-xyz"


def test_session_mounts_when_policy_carries_them(applies, state_dir):
    """A staged policy with session.mounts[] -> multi-mount, not the default."""
    _finalize(prov=POLICY_WITH_SESSION)
    assert applies["apply_session_mounts"].call_count == 1
    assert applies["apply_rclone_mount"].call_count == 0
    assert applies["apply_session_mounts"].call_args.kwargs["mounts"] == \
        POLICY_WITH_SESSION["session"]["mounts"]


def test_pwas_forwarded_to_brave_policy(applies, state_dir):
    _finalize(prov=POLICY_WITH_SESSION)
    assert applies["apply_brave_policy"].call_args.kwargs["pwas"] == \
        POLICY_WITH_SESSION["session"]["pwas"]


def test_skips_mount_when_no_credentials(applies, state_dir):
    """do_mount True but SSO not completed (empty creds) -> no mount at all,
    the other four still run."""
    _finalize(nc_login_name="", nc_app_password="")
    assert applies["apply_rclone_mount"].call_count == 0
    assert applies["apply_session_mounts"].call_count == 0
    for name in ("apply_bitwarden_prefs", "apply_brave_policy",
                 "apply_kaccounts", "apply_kde_theme"):
        assert applies[name].call_count == 1, name


def test_skips_mount_when_unchecked(applies, state_dir):
    _finalize(do_mount=False)
    assert applies["apply_rclone_mount"].call_count == 0
    assert applies["apply_session_mounts"].call_count == 0


def test_writes_done_flag(applies, state_dir):
    _finalize(do_mount=False, nc_login_name="", nc_app_password="")
    assert main_mod.DONE_FLAG.exists()


def test_writes_user_email_mode_0600(applies, state_dir):
    _finalize(user_email="bob@bluefoxconsultant.com")
    f = main_mod.USER_CONFIG_DIR / "user_email"
    assert f.read_text().strip() == "bob@bluefoxconsultant.com"
    assert (f.stat().st_mode & 0o777) == 0o600


def test_writes_per_apply_log(applies, state_dir):
    _finalize()
    log = main_mod.USER_LOG.read_text()
    for tag in ("rclone_mount", "bitwarden_prefs", "brave_policy",
                "kaccounts", "kde_theme"):
        assert tag in log
    assert "OK" in log


def test_one_failing_apply_does_not_abort_the_rest(state_dir, monkeypatch):
    """A (False, msg) from one apply must not stop the others, and DONE_FLAG
    must still be written (best-effort contract)."""
    ok = {n: MagicMock(return_value=(True, "ok")) for n in (
        "apply_rclone_mount", "apply_bitwarden_prefs",
        "apply_kaccounts", "apply_kde_theme")}
    for name, m in ok.items():
        monkeypatch.setattr(main_mod, name, m)
    monkeypatch.setattr(main_mod, "apply_brave_policy",
                        MagicMock(return_value=(False, "boom")))
    _finalize()
    for name, m in ok.items():
        assert m.call_count == 1, name
    assert main_mod.DONE_FLAG.exists()
    assert "FAIL brave_policy" in main_mod.USER_LOG.read_text()
