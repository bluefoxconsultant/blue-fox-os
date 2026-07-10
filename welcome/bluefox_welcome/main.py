"""Blue Fox OS welcome agent — wizard premier démarrage.

Architecture (BFOSP1, BFOSP3, BFOSP4, BFOSI8, BFOSI9, BFOSD8) :

- KAccounts pour mail / calendar / contacts NC (Akonadi DAV) — kcmshell6 v1
- rclone WebDAV systemd --user mount pour fichiers NC (KIO trop lent)
- Bitwarden Flatpak prefs pour Vaultwarden (URL pre-config)
- Thunderbird : ouvert au firstboot, autoconfig Migadu via CNAME
- Brave Sync : seed phrase stockee dans Vaultwarden, copy-paste manuelle
- Login machine : statu quo BFOSI2 (compte local, sssd v1.1)

Deux flux au firstboot (BFOSI10 #22436), dispatchés par select_flow() :
- policy stagée présente (install device-flow) -> flux provisionné : 1 écran
  (confirme l'identité connue + 1 SSO Nextcloud) puis apply auto de tout le reste.
- pas de policy (install built-in defaults) -> le wizard manuel 5 pages qui
  collecte les inputs (email + choix mount).
Dans les deux cas _finalize_and_apply() orchestre les apply_* en best-effort :
un échec d'une intégration ne bloque pas les autres ; tout résultat est journalisé.
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

from .apply import (
    apply_bitwarden_prefs,
    apply_brave_policy,
    apply_kaccounts,
    apply_kde_theme,
    apply_rclone_mount,
    apply_session_mounts,
)
from .auth.nc_login_flow import initiate as nc_login_initiate
from .auth.nc_login_flow import poll_once as nc_login_poll_once
from .provisioning import (
    load_provisioning,
    merge_branding,
    session_mounts,
    session_pwas,
    user_login,
)
from .tenant import get_service_url, get_slug, load_tenant

LOG = logging.getLogger("bluefox-welcome")
STATE_DIR = Path("/var/lib/bluefox-welcome")
DONE_FLAG = STATE_DIR / "done"
NEEDS_REBASE_FLAG = STATE_DIR / "needs-rebase"
USER_CONFIG_DIR = Path.home() / ".config" / "bluefox-welcome"
USER_LOG = Path.home() / ".local/share/bluefox-welcome/firstboot.log"


def _open_url_logged(url: str) -> None:
    """Spawn xdg-open on a URL with stderr captured. Used by 'Ouvrir <svc>'
    buttons in the wizard. Failures are logged but never propagate to Qt
    (a raise inside a clicked-slot lambda would crash the wizard silently)."""
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        LOG.info("xdg-open spawned for %s", url)
    except FileNotFoundError:
        LOG.error("xdg-open not found ; xdg-utils package missing from image")
    except Exception as exc:
        LOG.exception("xdg-open %s failed: %s", url, exc)


def cli() -> int:
    parser = argparse.ArgumentParser(prog="bluefox-welcome")
    parser.add_argument("--service-mode", action="store_true",
                        help="Lance par firstboot.service ; check etat avant wizard.")
    parser.add_argument("--reset", action="store_true",
                        help="Dev only : retire les flags d'etat et relance le wizard.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.reset:
        for flag in (DONE_FLAG,):
            try:
                flag.unlink()
                LOG.info("removed %s", flag)
            except FileNotFoundError:
                pass
        return 0

    if DONE_FLAG.exists():
        LOG.info("welcome already completed (%s) ; nothing to do", DONE_FLAG)
        return 0

    tenant = load_tenant()
    # Prefer the install-staged policy (bf-policy/v2) over the baked tenant.json:
    # overlay its session branding and pre-fill the wizard with the authenticated
    # user. Absent on built-in-defaults installs — degrades to tenant.json alone.
    prov = load_provisioning()
    tenant = merge_branding(tenant, prov)
    return run_wizard(tenant, prefill_email=user_login(prov), prov=prov)


def select_flow(prov: dict | None) -> str:
    """'provisioned' when a valid staged policy names the user (we know who they
    are + their session prefs -> minimal interaction) ; else 'manual' (the full
    5-page wizard). A tiny pure function so the dispatch is unit-testable
    without Qt."""
    return "provisioned" if (prov and user_login(prov)) else "manual"


def run_wizard(tenant: dict, prefill_email: str = "", prov: dict | None = None) -> int:
    """Firstboot entry point (BFOSI10 #22436). A device-flow install stages a
    bf-policy/v2 policy -> the provisioned flow (1 screen: confirm identity + one
    NC SSO + auto-apply). A built-in-defaults install has no policy -> the manual
    5-page wizard."""
    prov = prov or {}
    if select_flow(prov) == "provisioned":
        return _run_provisioned_flow(tenant, prov)
    return _run_manual_wizard(tenant, prefill_email, prov)


def _apply_from_policy(tenant: dict, prov: dict, nc_creds) -> None:
    """Qt-free orchestration for the provisioned flow. ``nc_creds`` is
    (login_name, app_password) on a completed Nextcloud SSO, or None when the
    user skipped it — decision A: apply everything except the NC mounts."""
    login, password = nc_creds if nc_creds else ("", "")
    _finalize_and_apply(
        tenant=tenant,
        user_email=user_login(prov),
        do_mount=bool(nc_creds),
        nc_login_name=login,
        nc_app_password=password,
        prov=prov,
    )


def _run_provisioned_flow(tenant: dict, prov: dict) -> int:
    """Minimal firstboot for a device-flow install: identity + session prefs are
    already known from the staged policy, so the only interaction is the one NC
    SSO (Login Flow v2 — the mount credential cannot come from the device-flow
    token, see auth/nc_login_flow.py). Everything else applies on Terminer."""
    try:
        from PyQt6.QtCore import QDateTime, QTimer
        from PyQt6.QtWidgets import (
            QApplication, QWizard, QWizardPage, QLabel, QPushButton, QVBoxLayout,
        )
    except ImportError:
        LOG.error("PyQt6 absent ; applying policy headless (no NC mount)")
        _apply_from_policy(tenant, prov, nc_creds=None)
        return 0

    app = QApplication(sys.argv)  # noqa: F841 — kept alive for Qt
    wizard = QWizard()
    wizard.setWindowTitle("Blue Fox OS — Premier démarrage")
    wizard.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

    login = user_login(prov)
    nc_url = get_service_url(tenant, "nextcloud",
                             "https://nextcloud.bluefoxconsultant.com")
    creds = {"value": None}

    page = QWizardPage()
    page.setTitle("Bienvenue sur Blue Fox OS")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        f"Configuration automatique de votre poste pour <b>{login}</b>."))
    layout.addWidget(QLabel(
        "Connectez-vous une fois à Nextcloud (SSO) pour activer vos fichiers. "
        "Le thème, le navigateur, le vault et les comptes mail sont configurés "
        "automatiquement à la fin."))
    btn = QPushButton("Se connecter à Nextcloud (SSO)")
    layout.addWidget(btn)
    status = QLabel("Non connecté — vous pouvez ignorer et le faire plus tard.")
    layout.addWidget(status)
    page.setLayout(layout)
    wizard.addPage(page)

    # NC Login Flow v2 on a QTimer (same mechanism as the manual _files_page ;
    # TODO dedupe into a shared helper once the manual path gets test coverage).
    state = {"poll_endpoint": None, "poll_token": None, "deadline": 0}
    timer = QTimer(page)
    timer.setInterval(2000)

    def on_tick():
        try:
            res = nc_login_poll_once(state["poll_endpoint"], state["poll_token"])
        except Exception as exc:  # noqa: BLE001 — surface to the user, never crash Qt
            timer.stop()
            btn.setEnabled(True)
            status.setText(f"<span style='color:#c0392b'>Échec : {exc}</span>")
            LOG.warning("nc login poll failed: %s", exc)
            return
        if res is not None:
            timer.stop()
            creds["value"] = (res[0], res[1])
            status.setText(f"<span style='color:#27ab63'>Connecté en tant que "
                           f"<b>{res[0]}</b> ✓</span>")
            btn.setText("Reconnecter")
            btn.setEnabled(True)
            return
        if QDateTime.currentSecsSinceEpoch() > state["deadline"]:
            timer.stop()
            btn.setEnabled(True)
            status.setText("<span style='color:#c0392b'>Délai dépassé — "
                           "réessayez.</span>")

    def on_click():
        try:
            login_url, endpoint, token = nc_login_initiate(nc_url)
        except Exception as exc:  # noqa: BLE001
            status.setText(f"<span style='color:#c0392b'>Impossible de démarrer "
                           f"la connexion : {exc}</span>")
            LOG.warning("nc login initiate failed: %s", exc)
            return
        state.update(poll_endpoint=endpoint, poll_token=token,
                     deadline=QDateTime.currentSecsSinceEpoch() + 300)
        _open_url_logged(login_url)
        btn.setEnabled(False)
        status.setText("Connectez-vous dans le navigateur…")
        timer.start()

    btn.clicked.connect(on_click)
    timer.timeout.connect(on_tick)

    rc = wizard.exec()
    if rc == QWizard.DialogCode.Accepted:
        _apply_from_policy(tenant, prov, creds["value"])
        return 0
    return 1


def build_manual_wizard(tenant: dict, prefill_email: str = "",
                        prov: dict | None = None):
    """Construct the manual 5-page QWizard *without* executing it.

    Returns ``(app, wizard)`` so both the firstboot entry point and the
    pytest-qt harness (#22234) build the exact same wizard, then drive
    navigation / inspect fields before (or instead of) ``exec()``. PyQt6 is
    imported here lazily so main.py stays importable on the pure-Python test
    lane; an already-running QApplication (pytest-qt owns one via its ``qapp``
    fixture) is reused rather than constructing a second."""
    from PyQt6.QtCore import QDateTime, QTimer
    from PyQt6.QtWidgets import (
        QApplication, QWizard, QWizardPage, QLabel, QLineEdit,
        QVBoxLayout, QPushButton, QCheckBox, QTextEdit,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    wizard = QWizard()
    wizard.setWindowTitle("Blue Fox OS — Premier démarrage")
    wizard.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

    wizard.addPage(_welcome_page(tenant, QWizardPage, QVBoxLayout, QLabel))
    wizard.addPage(_authentik_page(tenant, QWizardPage, QVBoxLayout, QLabel,
                                   QLineEdit, QPushButton, prefill_email))
    wizard.addPage(_files_page(tenant, QWizardPage, QVBoxLayout, QLabel,
                               QCheckBox, QLineEdit, QPushButton,
                               QTimer, QDateTime))
    wizard.addPage(_vault_page(tenant, QWizardPage, QVBoxLayout, QLabel,
                               QPushButton))
    wizard.addPage(_done_page(tenant, wizard, QWizardPage, QVBoxLayout,
                              QTextEdit))
    return app, wizard


def _finalize_from_wizard(wizard, tenant: dict, prov: dict) -> None:
    """Marshal the accepted manual wizard's fields into _finalize_and_apply.

    Split out of _run_manual_wizard so the accept path is exercised by the same
    code the tests call — no field-marshalling copy living in the harness."""
    _finalize_and_apply(
        tenant=tenant,
        user_email=wizard.field("user_email") or "",
        do_mount=bool(wizard.field("do_mount")),
        nc_login_name=wizard.field("nc_login_name") or "",
        nc_app_password=wizard.field("nc_app_password") or "",
        prov=prov,
    )


def _run_manual_wizard(tenant: dict, prefill_email: str = "",
                       prov: dict | None = None) -> int:
    """The full 5-page wizard for built-in-defaults installs (no staged policy):
    welcome, Authentik identity, NC files + SSO, vault, recap."""
    try:
        app, wizard = build_manual_wizard(tenant, prefill_email, prov)  # noqa: F841 — app kept alive for Qt
    except ImportError:
        LOG.error("PyQt6 not available ; falling back to terminal stub")
        print("[stub] Blue Fox OS welcome wizard ; PyQt6 manquant.")
        return 0

    from PyQt6.QtWidgets import QWizard  # safe: build_manual_wizard succeeded
    if wizard.exec() == QWizard.DialogCode.Accepted:
        _finalize_from_wizard(wizard, tenant, prov or {})
        return 0
    return 1


def _welcome_page(tenant, QWizardPage, QVBoxLayout, QLabel):
    page = QWizardPage()
    page.setTitle("Bienvenue sur Blue Fox OS")
    slug = get_slug(tenant)
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        f"Cet assistant configure votre poste pour le tenant <b>{slug}</b>."))
    layout.addWidget(QLabel(
        "Étapes : compte Authentik, fichiers Nextcloud, vault Bitwarden, sync Brave."))
    layout.addWidget(QLabel(
        "Vous pouvez ignorer une étape avec Suivant et la reprendre plus tard."))
    page.setLayout(layout)
    return page


def _authentik_page(tenant, QWizardPage, QVBoxLayout, QLabel, QLineEdit,
                    QPushButton, prefill_email=""):
    page = QWizardPage()
    page.setTitle("Identité — Authentik")
    auth_url = get_service_url(tenant, "authentik",
                               "https://auth.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        "Votre compte unique Blue Fox donne accès à Nextcloud + Odoo + Vaultwarden "
        "via le même mot de passe (BFOSI2)."))
    layout.addWidget(QLabel(
        "Connectez-vous à Authentik dans le navigateur pour configurer le MFA TOTP "
        "la première fois."))
    btn = QPushButton(f"Ouvrir {auth_url}")
    btn.clicked.connect(lambda: _open_url_logged(auth_url))
    layout.addWidget(btn)
    layout.addWidget(QLabel("Votre courriel Blue Fox :"))
    email = QLineEdit()
    email.setPlaceholderText("prenom@bluefoxconsultant.com")
    if prefill_email:
        email.setText(prefill_email)  # known from the install device-flow
    layout.addWidget(email)
    page.registerField("user_email*", email)
    page.setLayout(layout)
    return page


def _files_page(tenant, QWizardPage, QVBoxLayout, QLabel, QCheckBox, QLineEdit,
                QPushButton, QTimer, QDateTime):
    page = QWizardPage()
    page.setTitle("Fichiers Nextcloud")
    nc_url = get_service_url(tenant, "nextcloud",
                             "https://nextcloud.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel("Configuration du dossier Nextcloud (BFOSP3)."))
    layout.addWidget(QLabel(f"Serveur : <code>{nc_url}</code>"))
    layout.addWidget(QLabel("Cible : <code>~/Nextcloud/</code> — visible "
                            "dans Dolphin et toutes les apps."))

    do_mount = QCheckBox("Monter Nextcloud dans ~/Nextcloud après la connexion")
    do_mount.setChecked(True)
    layout.addWidget(do_mount)
    page.registerField("do_mount", do_mount)

    layout.addWidget(QLabel(
        "Connectez-vous une seule fois : l'authentification se fait dans le "
        "navigateur avec votre compte Blue Fox (SSO Authentik). Aucun mot de "
        "passe à saisir ici."))
    btn = QPushButton("Se connecter à Nextcloud (SSO)")
    layout.addWidget(btn)
    status = QLabel("Non connecté.")
    layout.addWidget(status)

    # Hidden fields carry the credentials acquired via Login Flow v2 to _finalize.
    nc_login_name = QLineEdit()
    nc_login_name.setVisible(False)
    nc_app_password = QLineEdit()
    nc_app_password.setVisible(False)
    layout.addWidget(nc_login_name)
    layout.addWidget(nc_app_password)
    page.registerField("nc_login_name", nc_login_name)
    page.registerField("nc_app_password", nc_app_password)

    layout.addWidget(QLabel("Mail / calendrier / contacts seront ajoutés "
                            "à KAccounts (System Settings → Online Accounts)."))

    # NC Login Flow v2 driven by a QTimer: each tick is one quick poll, so the
    # wizard stays responsive while the user authenticates in the browser.
    state = {"poll_endpoint": None, "poll_token": None, "deadline": 0}
    timer = QTimer(page)
    timer.setInterval(2000)

    def on_tick():
        try:
            res = nc_login_poll_once(state["poll_endpoint"], state["poll_token"])
        except Exception as exc:  # noqa: BLE001 — surface to the user, never crash Qt
            timer.stop()
            btn.setEnabled(True)
            status.setText(f"<span style='color:#c0392b'>Échec : {exc}</span>")
            LOG.warning("nc login poll failed: %s", exc)
            return
        if res is not None:
            timer.stop()
            nc_login_name.setText(res[0])
            nc_app_password.setText(res[1])
            status.setText(f"<span style='color:#27ab63'>Connecté en tant que "
                           f"<b>{res[0]}</b> ✓</span>")
            btn.setText("Reconnecter")
            btn.setEnabled(True)
            return
        if QDateTime.currentSecsSinceEpoch() > state["deadline"]:
            timer.stop()
            btn.setEnabled(True)
            status.setText("<span style='color:#c0392b'>Délai dépassé — "
                           "réessayez.</span>")

    def on_click():
        try:
            login_url, endpoint, token = nc_login_initiate(nc_url)
        except Exception as exc:  # noqa: BLE001
            status.setText(f"<span style='color:#c0392b'>Impossible de démarrer "
                           f"la connexion : {exc}</span>")
            LOG.warning("nc login initiate failed: %s", exc)
            return
        state.update(poll_endpoint=endpoint, poll_token=token,
                     deadline=QDateTime.currentSecsSinceEpoch() + 300)
        _open_url_logged(login_url)
        btn.setEnabled(False)
        status.setText("Connectez-vous dans le navigateur…")
        timer.start()

    btn.clicked.connect(on_click)
    timer.timeout.connect(on_tick)

    page.setLayout(layout)
    return page


def _vault_page(tenant, QWizardPage, QVBoxLayout, QLabel, QPushButton):
    page = QWizardPage()
    page.setTitle("Vault Bitwarden et Brave Sync")
    vault_url = get_service_url(tenant, "vaultwarden",
                                "https://vault.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        f"<b>1.</b> Bitwarden Desktop sera pré-configuré pour : <code>{vault_url}</code>"))
    layout.addWidget(QLabel(
        "Lance Bitwarden depuis le menu après l'assistant et connecte-toi "
        "— l'URL self-hosted sera déjà remplie."))
    btn_v = QPushButton(f"Ouvrir {vault_url}")
    btn_v.clicked.connect(lambda: _open_url_logged(vault_url))
    layout.addWidget(btn_v)
    layout.addWidget(QLabel("<br><b>2.</b> Brave Sync (BFOSP4) — seed phrase via Vaultwarden :"))
    layout.addWidget(QLabel(
        "<ol>"
        "<li>Première machine BF OS : ouvre Brave, Settings → Sync → "
        "« Start a new sync chain », sauvegarde la seed dans Vaultwarden "
        "(entrée « Brave Sync — prénom »).</li>"
        "<li>Machines suivantes : récupère la seed dans Vaultwarden et colle-la "
        "dans Brave Settings → Sync → « I have a sync code ».</li>"
        "</ol>"))
    page.setLayout(layout)
    return page


def _done_page(tenant, wizard, QWizardPage, QVBoxLayout, QTextEdit):
    page = QWizardPage()
    page.setTitle("Récapitulatif")
    layout = QVBoxLayout()
    summary = QTextEdit()
    summary.setReadOnly(True)
    layout.addWidget(summary)
    page.setLayout(layout)

    def init():
        slug = get_slug(tenant)
        email = wizard.field("user_email") or "?"
        do_mount = bool(wizard.field("do_mount"))
        nc_login = wizard.field("nc_login_name") or ""
        connected = bool(nc_login and wizard.field("nc_app_password"))
        if connected and do_mount:
            nc_state = f"monté au clic suivant (connecté : {nc_login})"
        elif connected:
            nc_state = f"connecté ({nc_login}) — mount décoché"
        else:
            nc_state = "reporté (connexion SSO non complétée)"
        text = (
            f"<h3>Configuration prête</h3>"
            f"<ul>"
            f"<li>Tenant : <b>{slug}</b></li>"
            f"<li>Compte BF : {email}</li>"
            f"<li>NC Files (rclone mount, SSO) : {nc_state}</li>"
            f"<li>Bitwarden Desktop : URL Vaultwarden injectée au clic suivant</li>"
            f"<li>Brave : policy + extension Floccus poussées au clic suivant</li>"
            f"<li>Mail/Calendar/Contacts : System Settings ouvert au clic suivant</li>"
            f"</ul>"
            f"<p>Cliquez <b>Terminer</b> pour exécuter les configurations.</p>")
        summary.setHtml(text)

    page.initializePage = init
    return page


def _finalize_and_apply(
    tenant: dict,
    user_email: str,
    do_mount: bool,
    nc_login_name: str,
    nc_app_password: str,
    prov: dict | None = None,
) -> None:
    """Run the apply_* integrations, then write the done flag.

    Best-effort: one failed apply does not abort the others. All results
    are logged and surfaced in ~/.local/share/bluefox-welcome/firstboot.log.

    The Nextcloud credential pair comes from the SSO Login Flow v2 (login name
    + app-password), not a typed password — see auth/nc_login_flow.py.

    `prov` is the staged bf-policy/v2 JSON: its session.mounts[] drive a
    multi-mount (one rclone unit each) instead of the single ~/Nextcloud
    default, and session.pwas[] are force-installed via the Brave policy.
    """
    prov = prov or {}
    mounts = session_mounts(prov)
    pwas = session_pwas(prov)
    results: list[tuple[str, bool, str]] = []

    if do_mount and nc_login_name and nc_app_password:
        if mounts:
            ok, msg = apply_session_mounts(
                tenant, user=nc_login_name, password=nc_app_password,
                mounts=mounts)
            results.append(("rclone_mounts", ok, msg))
        else:
            ok, msg = apply_rclone_mount(
                tenant, user=nc_login_name, password=nc_app_password)
            results.append(("rclone_mount", ok, msg))
    else:
        results.append(("rclone_mount", False,
                        "ignoré (connexion SSO non complétée ou mount décoché)"))

    ok, msg = apply_bitwarden_prefs(tenant)
    results.append(("bitwarden_prefs", ok, msg))

    ok, msg = apply_brave_policy(tenant, pwas=pwas)
    results.append(("brave_policy", ok, msg))

    ok, msg = apply_kaccounts(tenant)
    results.append(("kaccounts", ok, msg))

    ok, msg = apply_kde_theme(tenant)
    results.append(("kde_theme", ok, msg))

    try:
        USER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USER_LOG.open("a") as fh:
            for name, ok, msg in results:
                fh.write(f"{'OK' if ok else 'FAIL'} {name}: {msg}\n")
    except Exception as e:
        LOG.warning("could not write %s: %s", USER_LOG, e)

    for name, ok, msg in results:
        LOG.info("apply %s: ok=%s msg=%s", name, ok, msg)

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        DONE_FLAG.touch()
    except PermissionError:
        LOG.warning("cannot write %s as user ; firstboot.service should mkdir at /var/lib", STATE_DIR)
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    email_file = USER_CONFIG_DIR / "user_email"
    email_file.write_text(user_email + "\n")
    email_file.chmod(0o600)
    LOG.info("wizard finished ; flagged done at %s", DONE_FLAG)


if __name__ == "__main__":
    sys.exit(cli())
