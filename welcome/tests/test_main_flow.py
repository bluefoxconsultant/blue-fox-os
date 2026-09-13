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


# ---------------------------------------------------------------------------
# Identifiants deposes par l'installation : plus aucun SSO a refaire (#22436)
# ---------------------------------------------------------------------------
# Jusqu'au 2026-09-12, le premier demarrage redemandait un SSO Nextcloud a la
# personne qui venait d'autoriser l'installation. Le commentaire qui justifiait
# cette etape affirmait que le justificatif de montage ne pouvait pas venir du
# jeton du flux d'appareil. C'etait faux : l'echange RFC 8693 le permet.


def test_identifiants_deposes_suppriment_toute_interaction():
    with mock.patch.object(main.seat_credentials, "lire",
                           return_value=("Olivier", "mdp-application")) as lire, \
         mock.patch.object(main.seat_credentials, "effacer") as effacer, \
         mock.patch.object(main, "_apply_from_policy") as applique, \
         mock.patch.object(main, "_run_provisioned_flow_sso") as sso:
        rc = main._run_provisioned_flow({"slug": "bf"}, POLICY)
    assert rc == 0
    lire.assert_called_once()
    sso.assert_not_called()          # aucune page, aucun navigateur
    applique.assert_called_once()
    assert applique.call_args.args[2] == ("Olivier", "mdp-application")
    effacer.assert_called_once()     # consomme, pas conserve


def test_sans_identifiants_le_parcours_sso_reste_le_repli():
    with mock.patch.object(main.seat_credentials, "lire", return_value=None), \
         mock.patch.object(main, "_apply_from_policy") as applique, \
         mock.patch.object(main, "_run_provisioned_flow_sso",
                           return_value=0) as sso:
        rc = main._run_provisioned_flow({"slug": "bf"}, POLICY)
    assert rc == 0
    sso.assert_called_once()
    applique.assert_not_called()


def test_le_depot_n_est_pas_efface_quand_il_n_a_pas_servi():
    with mock.patch.object(main.seat_credentials, "lire", return_value=None), \
         mock.patch.object(main.seat_credentials, "effacer") as effacer, \
         mock.patch.object(main, "_run_provisioned_flow_sso", return_value=0):
        main._run_provisioned_flow({"slug": "bf"}, POLICY)
    effacer.assert_not_called()
