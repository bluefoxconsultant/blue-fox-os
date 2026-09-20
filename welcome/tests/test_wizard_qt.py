"""pytest-qt harness for the manual 5-page QWizard (#22234, BFOSP1).

Builds the real wizard via build_manual_wizard() under the offscreen Qt
platform (no X server) and exercises: page count, start page, required-field
gating, forward/back navigation, prefill wiring, and the accept path's
delegation to _finalize_and_apply.

Skipped automatically when PyQt6 (its native Qt libs) or pytest-qt are not
importable, so the pure-Python lane still runs on hosts without Qt.
"""
import os

import pytest

# The offscreen platform plugin must be selected before any QApplication is
# created — set it at import time, before pytest-qt builds its qapp.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Importing QtWidgets (and pytestqt, which imports QtGui) dlopens the native Qt
# stack (libGL/libEGL/libxkbcommon). On a host with the wheel but without those
# system libs the loader raises a plain ImportError (e.g. "libEGL.so.1: cannot
# open shared object file") — NOT ModuleNotFoundError — so exc_type=ImportError
# is required for importorskip to treat it as a skip rather than a collection
# error (pytest >=8.2 only skips on ModuleNotFoundError by default).
pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
pytest.importorskip("pytestqt", exc_type=ImportError)

from bluefox_welcome import main as main_mod  # noqa: E402
from bluefox_welcome.main import build_manual_wizard  # noqa: E402

EMAIL = "alice@bluefoxconsultant.com"


def test_wizard_has_five_pages(qtbot, tenant_data):
    _app, wizard = build_manual_wizard(tenant_data)
    qtbot.addWidget(wizard)
    assert len(wizard.pageIds()) == 5


def test_wizard_starts_on_welcome(qtbot, tenant_data):
    _app, wizard = build_manual_wizard(tenant_data)
    qtbot.addWidget(wizard)
    wizard.restart()
    assert "Bienvenue" in wizard.currentPage().title()


def test_required_email_gates_next(qtbot, tenant_data):
    """The Authentik page registers user_email* (mandatory). While it is empty
    the page is not complete — which is exactly what disables Next/Finish."""
    _app, wizard = build_manual_wizard(tenant_data)
    qtbot.addWidget(wizard)
    wizard.restart()
    wizard.next()                                    # welcome -> authentik
    page = wizard.currentPage()
    assert "Identité" in page.title()
    assert page.isComplete() is False                # empty required email
    wizard.setField("user_email", EMAIL)
    assert page.isComplete() is True                 # now Next is enabled


def test_forward_and_back_navigation(qtbot, tenant_data):
    _app, wizard = build_manual_wizard(tenant_data)
    qtbot.addWidget(wizard)
    wizard.restart()
    seen = [wizard.currentPage().title()]            # welcome
    wizard.next()                                    # -> authentik
    seen.append(wizard.currentPage().title())
    wizard.setField("user_email", EMAIL)
    for _ in range(3):                               # -> files -> vault -> done
        wizard.next()
        seen.append(wizard.currentPage().title())
    assert any("Bienvenue" in t for t in seen)
    assert any("Identité" in t for t in seen)
    assert any("Fichiers" in t for t in seen)
    assert any("Vault" in t or "Bitwarden" in t for t in seen)
    assert any("Récapitulatif" in t for t in seen)
    # back from the recap lands on the vault page
    wizard.back()
    assert "Vault" in wizard.currentPage().title() or \
        "Bitwarden" in wizard.currentPage().title()


def test_prefill_email_populates_field(qtbot, tenant_data):
    """A supplied prefill flows into the Authentik email field.

    (Defensive wiring: in production the manual wizard's prefill is effectively
    always empty — when the user identity IS known, select_flow() routes to the
    provisioned flow instead of this wizard.)

    We assert the populated value, not isComplete(): a mandatory field
    pre-populated before its page is added to the wizard has that text as its
    Qt 'initialValue', so isComplete() stays False until the value *changes* —
    a Qt quirk, not a wizard requirement.
    """
    _app, wizard = build_manual_wizard(tenant_data, prefill_email=EMAIL)
    qtbot.addWidget(wizard)
    wizard.restart()
    wizard.next()
    assert wizard.field("user_email") == EMAIL


def test_accept_path_delegates_to_finalize(qtbot, monkeypatch, tenant_data):
    """The accepted wizard funnels its field values into _finalize_and_apply
    via the same _finalize_from_wizard the firstboot path uses."""
    captured = {}
    monkeypatch.setattr(main_mod, "_finalize_and_apply",
                        lambda **kw: captured.update(kw))

    _app, wizard = build_manual_wizard(tenant_data)
    qtbot.addWidget(wizard)
    wizard.restart()
    wizard.setField("user_email", EMAIL)
    wizard.setField("do_mount", True)
    wizard.setField("nc_login_name", "alice")
    wizard.setField("nc_app_password", "app-pw-xyz")

    main_mod._finalize_from_wizard(wizard, tenant_data, prov={})

    assert captured["user_email"] == EMAIL
    assert captured["do_mount"] is True
    assert captured["nc_login_name"] == "alice"
    assert captured["nc_app_password"] == "app-pw-xyz"
    assert captured["tenant"] is tenant_data
    assert captured["prov"] == {}


# ---- _wire_nc_login_poll: the NC Login Flow v2 loop shared by the provisioned
# flow and the manual files page (dedupe #22436). The seam under test is the
# on_success callback — the only per-flow difference — plus the common btn/
# status feedback and the stop-on-{success,error} timer lifecycle. ------------

NC_URL = "https://nextcloud.bluefoxconsultant.com"


def _stub_login_flow(monkeypatch, poll):
    """Point the module-level flow seams at fakes: initiate always succeeds,
    xdg-open is a no-op (never spawn a browser in tests), poll_once = ``poll``."""
    monkeypatch.setattr(main_mod, "nc_login_initiate",
                        lambda url: ("http://login", "http://poll", "tok"))
    monkeypatch.setattr(main_mod, "_open_url_logged", lambda url: None)
    monkeypatch.setattr(main_mod, "nc_login_poll_once", poll)


def _poll_widgets(qtbot):
    from PyQt6.QtWidgets import QWizardPage, QPushButton, QLabel
    page = QWizardPage()
    btn = QPushButton("Se connecter")
    status = QLabel("Non connecté.")
    for w in (page, btn, status):
        qtbot.addWidget(w)
    return page, btn, status


def test_wire_poll_success_dispatches_to_on_success(qtbot, monkeypatch):
    """A successful poll routes (login, app_password) through on_success and
    applies the shared feedback — this is the seam both flows reuse."""
    _stub_login_flow(monkeypatch, lambda endpoint, token: ("alice", "app-pw-xyz"))
    page, btn, status = _poll_widgets(qtbot)

    got = {}
    timer = main_mod._wire_nc_login_poll(
        page, btn, status, NC_URL,
        lambda login, pw: got.update(login=login, pw=pw))

    btn.click()                          # on_click -> initiate + timer.start()
    assert btn.isEnabled() is False      # disabled while the browser login runs
    assert timer.isActive()
    timer.timeout.emit()                 # one tick -> poll returns creds

    assert got == {"login": "alice", "pw": "app-pw-xyz"}
    assert "Connecté en tant que" in status.text()
    assert "alice" in status.text()
    assert btn.text() == "Reconnecter"
    assert btn.isEnabled() is True
    assert timer.isActive() is False     # stopped on success


def test_wire_poll_pending_keeps_polling(qtbot, monkeypatch):
    """A pending (404 -> None) poll keeps the loop running and does NOT fire
    on_success — the user has not finished authenticating yet."""
    _stub_login_flow(monkeypatch, lambda endpoint, token: None)
    page, btn, status = _poll_widgets(qtbot)

    called = []
    timer = main_mod._wire_nc_login_poll(
        page, btn, status, NC_URL, lambda login, pw: called.append((login, pw)))

    btn.click()
    timer.timeout.emit()                 # pending tick

    assert called == []                  # on_success not fired
    assert timer.isActive()              # still polling (deadline not reached)
    assert btn.isEnabled() is False      # still waiting on the browser


def test_wire_poll_error_surfaces_and_stops(qtbot, monkeypatch):
    """A poll that raises stops the loop, re-enables the button, surfaces the
    error, and never calls on_success."""
    def boom(endpoint, token):
        raise RuntimeError("nc down")
    _stub_login_flow(monkeypatch, boom)
    page, btn, status = _poll_widgets(qtbot)

    called = []
    timer = main_mod._wire_nc_login_poll(
        page, btn, status, NC_URL, lambda login, pw: called.append(1))

    btn.click()
    timer.timeout.emit()                 # tick raises

    assert called == []
    assert timer.isActive() is False     # stopped on error
    assert btn.isEnabled() is True
    assert "nc down" in status.text()


def test_wire_poll_initiate_failure_never_starts_timer(qtbot, monkeypatch):
    """If initiating the flow fails, the click surfaces the error and never
    starts the timer — there is nothing to poll."""
    def boom(url):
        raise RuntimeError("no route")
    monkeypatch.setattr(main_mod, "nc_login_initiate", boom)
    monkeypatch.setattr(main_mod, "_open_url_logged", lambda url: None)
    page, btn, status = _poll_widgets(qtbot)

    timer = main_mod._wire_nc_login_poll(page, btn, status, NC_URL,
                                         lambda login, pw: None)
    btn.click()

    assert timer.isActive() is False     # never started
    assert "no route" in status.text()
    assert btn.isEnabled() is True       # still clickable to retry


def test_files_page_populates_hidden_fields_on_success(qtbot, monkeypatch,
                                                       tenant_data):
    """Integration at the real manual call site: a successful SSO on the files
    page writes the credential into the hidden nc_login_name / nc_app_password
    fields that _finalize reads via wizard.field()."""
    from PyQt6.QtWidgets import QPushButton
    from PyQt6.QtCore import QTimer
    _stub_login_flow(monkeypatch, lambda endpoint, token: ("bob", "bob-app-pw"))

    _app, wizard = build_manual_wizard(tenant_data)
    qtbot.addWidget(wizard)

    files_page = next(wizard.page(pid) for pid in wizard.pageIds()
                      if "Fichiers" in wizard.page(pid).title())
    btn = next(b for b in files_page.findChildren(QPushButton)
               if "Nextcloud" in b.text())
    timer = files_page.findChild(QTimer)

    btn.click()
    timer.timeout.emit()

    assert wizard.field("nc_login_name") == "bob"
    assert wizard.field("nc_app_password") == "bob-app-pw"
