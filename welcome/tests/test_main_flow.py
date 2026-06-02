"""Firstboot dispatch tests (#22436): provisioned vs manual flow + apply.

main.py imports PyQt6 only inside the flow functions, so these stay Qt-free:
the dispatch tests mock the two flow functions, and the apply tests mock
_finalize_and_apply.
"""
from unittest import mock

from bluefox_welcome import main

POLICY = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "session": {"mounts": [], "pwas": []},
}


def test_select_flow_provisioned_when_user_known():
    assert main.select_flow(POLICY) == "provisioned"


def test_select_flow_manual_when_absent():
    assert main.select_flow({}) == "manual"
    assert main.select_flow(None) == "manual"


def test_select_flow_manual_when_no_user_login():
    # A staged policy without a usable login can't pre-fill identity -> manual.
    assert main.select_flow({"schema": "bf-policy/v2", "user": {}}) == "manual"


def test_run_wizard_dispatches_to_provisioned():
    with mock.patch.object(main, "_run_provisioned_flow", return_value=0) as prov, \
         mock.patch.object(main, "_run_manual_wizard", return_value=0) as man:
        rc = main.run_wizard({"slug": "bf"}, prov=POLICY)
    assert rc == 0
    prov.assert_called_once()
    man.assert_not_called()
    # tenant + policy threaded through unchanged
    assert prov.call_args.args[1] == POLICY


def test_run_wizard_dispatches_to_manual():
    with mock.patch.object(main, "_run_provisioned_flow", return_value=0) as prov, \
         mock.patch.object(main, "_run_manual_wizard", return_value=0) as man:
        main.run_wizard({"slug": "bf"}, prefill_email="x@y.z", prov={})
    man.assert_called_once()
    prov.assert_not_called()


def test_apply_from_policy_with_creds_mounts():
    with mock.patch.object(main, "_finalize_and_apply") as fin:
        main._apply_from_policy({"slug": "bf"}, POLICY, ("loginname", "apppw"))
    kw = fin.call_args.kwargs
    assert kw["do_mount"] is True
    assert kw["nc_login_name"] == "loginname"
    assert kw["nc_app_password"] == "apppw"
    assert kw["user_email"] == "olivier@bluefoxconsultant.com"
    assert kw["prov"] is POLICY


def test_apply_from_policy_skip_sso_no_mount():
    # Decision A: SSO skipped -> apply everything except the NC mounts.
    with mock.patch.object(main, "_finalize_and_apply") as fin:
        main._apply_from_policy({"slug": "bf"}, POLICY, None)
    kw = fin.call_args.kwargs
    assert kw["do_mount"] is False
    assert kw["nc_login_name"] == ""
    assert kw["nc_app_password"] == ""
    # identity still applied from the policy
    assert kw["user_email"] == "olivier@bluefoxconsultant.com"


def test_provisioned_flow_headless_applies_without_mount():
    # No PyQt6 -> headless apply, never blocks the firstboot (decision A path).
    with mock.patch.dict("sys.modules", {"PyQt6": None, "PyQt6.QtWidgets": None,
                                         "PyQt6.QtCore": None}), \
         mock.patch.object(main, "_apply_from_policy") as apply_:
        rc = main._run_provisioned_flow({"slug": "bf"}, POLICY)
    assert rc == 0
    apply_.assert_called_once()
    assert apply_.call_args.kwargs.get("nc_creds", "missing") is None
