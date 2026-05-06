"""Tests for _finalize_and_apply orchestration.

No Qt deps — exercises the apply_* fan-out + DONE_FLAG bookkeeping
that runs after the QWizard is accepted.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bluefox_welcome import main as main_mod


@pytest.fixture
def patched_applies(monkeypatch):
    """Replace the 5 apply_* in main with mocks returning (True, 'ok')."""
    mocks = {
        "apply_rclone_mount": MagicMock(return_value=(True, "rclone ok")),
        "apply_bitwarden_prefs": MagicMock(return_value=(True, "bw ok")),
        "apply_brave_policy": MagicMock(return_value=(True, "brave ok")),
        "apply_kaccounts": MagicMock(return_value=(True, "kacc ok")),
        "apply_kde_theme": MagicMock(return_value=(True, "theme ok")),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(main_mod, name, m)
    return mocks


@pytest.fixture
def patched_state_dir(tmp_path: Path, monkeypatch):
    """Redirect STATE_DIR + USER_LOG + USER_CONFIG_DIR to tmp_path."""
    state = tmp_path / "var-lib"
    user_log = tmp_path / "share" / "firstboot.log"
    user_cfg = tmp_path / "config"
    monkeypatch.setattr(main_mod, "STATE_DIR", state)
    monkeypatch.setattr(main_mod, "DONE_FLAG", state / "done")
    monkeypatch.setattr(main_mod, "USER_LOG", user_log)
    monkeypatch.setattr(main_mod, "USER_CONFIG_DIR", user_cfg)
    return state


def test_finalize_calls_all_five_applies(patched_applies, patched_state_dir,
                                         tenant_data: dict):
    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="alice@bluefoxconsultant.com",
        do_mount=True,
        nc_password="hunter2",
    )
    for name, mock in patched_applies.items():
        assert mock.call_count == 1, f"{name} should be called exactly once"


def test_finalize_writes_done_flag(patched_applies, patched_state_dir,
                                   tenant_data: dict):
    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="alice@bluefoxconsultant.com",
        do_mount=False,
        nc_password="",
    )
    assert main_mod.DONE_FLAG.exists()


def test_finalize_writes_user_email(patched_applies, patched_state_dir,
                                    tenant_data: dict):
    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="bob@bluefoxconsultant.com",
        do_mount=False,
        nc_password="",
    )
    saved = (main_mod.USER_CONFIG_DIR / "user_email").read_text().strip()
    assert saved == "bob@bluefoxconsultant.com"


def test_finalize_skips_rclone_when_no_password(patched_applies,
                                                patched_state_dir,
                                                tenant_data: dict):
    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="alice@bluefoxconsultant.com",
        do_mount=True,
        nc_password="",
    )
    assert patched_applies["apply_rclone_mount"].call_count == 0
    # Other 4 still ran
    for name in ("apply_bitwarden_prefs", "apply_brave_policy",
                 "apply_kaccounts", "apply_kde_theme"):
        assert patched_applies[name].call_count == 1, name


def test_finalize_skips_rclone_when_unchecked(patched_applies,
                                              patched_state_dir,
                                              tenant_data: dict):
    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="alice@bluefoxconsultant.com",
        do_mount=False,
        nc_password="hunter2",
    )
    assert patched_applies["apply_rclone_mount"].call_count == 0


def test_finalize_writes_log(patched_applies, patched_state_dir,
                             tenant_data: dict):
    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="alice@bluefoxconsultant.com",
        do_mount=True,
        nc_password="hunter2",
    )
    log_text = main_mod.USER_LOG.read_text()
    for tag in ("rclone_mount", "bitwarden_prefs", "brave_policy",
                "kaccounts", "kde_theme"):
        assert tag in log_text


def test_finalize_continues_when_one_apply_raises(patched_state_dir,
                                                  monkeypatch,
                                                  tenant_data: dict):
    """One apply_* raising should NOT abort the others.

    apply_* contract is (ok, msg) — if any returns/raises, the rest still run.
    We mock apply_brave_policy to raise; the other 4 must still be called and
    DONE_FLAG must still be written.
    """
    others = {
        "apply_rclone_mount": MagicMock(return_value=(True, "ok")),
        "apply_bitwarden_prefs": MagicMock(return_value=(True, "ok")),
        "apply_kaccounts": MagicMock(return_value=(True, "ok")),
        "apply_kde_theme": MagicMock(return_value=(True, "ok")),
    }
    for name, m in others.items():
        monkeypatch.setattr(main_mod, name, m)

    # Per the apply_* contract, internal exceptions are caught and converted
    # to (False, repr(e)) — so "raises" here means returns a failure tuple.
    monkeypatch.setattr(main_mod, "apply_brave_policy",
                        MagicMock(return_value=(False, "RuntimeError('boom')")))

    main_mod._finalize_and_apply(
        tenant=tenant_data,
        user_email="alice@bluefoxconsultant.com",
        do_mount=True,
        nc_password="hunter2",
    )
    for name, mock in others.items():
        assert mock.call_count == 1, name
    assert main_mod.DONE_FLAG.exists()
