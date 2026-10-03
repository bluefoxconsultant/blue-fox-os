"""Blue Fox OS welcome agent — wizard premier démarrage.

Architecture (BFOSP1, BFOSP3, BFOSI8, BFOSI9, BFOSD8) :

- KAccounts pour mail / calendar / contacts NC (Akonadi DAV) — kcmshell6 v1
- rclone WebDAV systemd --user mount pour fichiers NC (KIO trop lent)
- Bitwarden Flatpak prefs pour Vaultwarden (URL pre-config)
- Thunderbird : ouvert au firstboot, autoconfig Migadu via CNAME
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
import getpass
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import seat_credentials
from .apply import (
    appliquer_horloges,
    appliquer_photo,
    apply_bitwarden_prefs,
    apply_brave_policy,
    apply_kde_theme,
    apply_rclone_mount,
    apply_session_mounts,
)
from .auth.nc_login_flow import initiate as nc_login_initiate
from .auth.nc_login_flow import poll_once as nc_login_poll_once
from .provisioning import (
    browser_extensions,
    load_provisioning,
    merge_branding,
    session_mounts,
    session_pwas,
    user_login,
)
from .secure_file import write_private
from .tenant import get_service_url, get_slug, load_tenant

LOG = logging.getLogger("bluefox-welcome")
STATE_DIR = Path("/var/lib/bluefox-welcome")
# ⚠️ DEPLACE LE 2026-09-11 : le drapeau vivait dans STATE_DIR, un repertoire
# cree root par le %post. L'agent tourne en tant que l'usager (autostart XDG)
# et ne pouvait donc JAMAIS l'ecrire — la PermissionError etait avalee en
# WARNING, le drapeau n'existait pas, et l'assistant se relancait a chaque
# ouverture de session. Un parcours d'accueil est par usager sur un poste
# multi-usagers : son drapeau vit dans le profil, la ou l'agent ecrit deja
# user_email et son journal.
DONE_FLAG = Path.home() / ".config" / "bluefox-welcome" / "done"
NEEDS_REBASE_FLAG = STATE_DIR / "needs-rebase"
USER_CONFIG_DIR = Path.home() / ".config" / "bluefox-welcome"
USER_LOG = Path.home() / ".local/share/bluefox-welcome/firstboot.log"


BROWSER_FLATPAK_ID = "com.brave.Browser"


def browser_argv(
    url: str,
    flatpak_installed=None,
    desktop_exists=None,
) -> list[str] | None:
    """Return the argv that opens ``url`` in a real browser, or None.

    ⚠️ NE PAS revenir a un simple `xdg-open` (regression vecue le 2026-07-19 :
    « le bouton ouvre Kate »). Au premier demarrage, DEUX gestionnaires MIME
    pointent dans le vide :
      - /etc/xdg/mimeapps.list (le notre) designe com.brave.Browser.desktop,
        mais Brave est un flatpak installe par system-flatpak-setup.service —
        il n'est PAS encore la quand l'agent tourne ;
      - /usr/share/applications/mimeapps.list (Fedora) designe firefox, que la
        recette RETIRE de l'image.
    xdg-open descend alors jusqu'au premier programme qui revendique text/html
    et lance un editeur de texte. Ce n'est pas cosmetique : le meme chemin sert
    au bouton SSO Nextcloud, donc la connexion — et le montage des fichiers —
    echouait avec lui.

    Les deux sondes sont injectables pour les tests.
    """
    if flatpak_installed is None:
        def flatpak_installed(app_id: str) -> bool:
            if not shutil.which("flatpak"):
                return False
            try:
                return subprocess.run(
                    ["flatpak", "info", app_id],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10,
                ).returncode == 0
            except Exception:  # noqa: BLE001 — une sonde ne doit jamais lever
                return False

    if desktop_exists is None:
        def desktop_exists() -> bool:
            # xdg-open n'est fiable que si le gestionnaire declare existe
            # vraiment. On ne verifie pas QUEL est le gestionnaire : n'importe
            # quel .desktop de navigateur present fait l'affaire.
            roots = [
                Path("/var/lib/flatpak/exports/share/applications"),
                Path.home() / ".local/share/flatpak/exports/share/applications",
                Path("/usr/share/applications"),
            ]
            names = [f"{BROWSER_FLATPAK_ID}.desktop", "firefox.desktop",
                     "org.mozilla.firefox.desktop", "chromium-browser.desktop"]
            return any((r / n).exists() for r in roots for n in names)

    if flatpak_installed(BROWSER_FLATPAK_ID):
        return ["flatpak", "run", BROWSER_FLATPAK_ID, url]
    if desktop_exists():
        return ["xdg-open", url]
    return None


def _open_url_logged(url: str) -> bool:
    """Ouvre ``url`` dans un navigateur. Utilise par les boutons « Ouvrir <svc> »
    ET par le bouton SSO Nextcloud. Un echec est journalise mais ne remonte
    jamais a Qt (une exception dans un slot clicked ferait planter le wizard
    en silence). Retourne True si un processus a ete lance."""
    argv = browser_argv(url)
    if argv is None:
        LOG.error(
            "aucun navigateur disponible pour ouvrir %s — Brave (flatpak %s) "
            "n'est pas encore installe et aucun .desktop de navigateur n'est "
            "present. On n'appelle PAS xdg-open : il ouvrirait un editeur de "
            "texte (cf. browser_argv).", url, BROWSER_FLATPAK_ID)
        return False
    try:
        subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        LOG.info("ouverture de %s via %s", url, argv[0])
        return True
    except FileNotFoundError:
        LOG.error("%s introuvable pour ouvrir %s", argv[0], url)
    except Exception as exc:
        LOG.exception("ouverture de %s via %s echouee: %s", url, argv[0], exc)
    return False


def _wire_nc_login_poll(page, btn, status, nc_url, on_success):
    """Wire an NC Login Flow v2 poll loop onto a wizard page.

    Shared by the provisioned flow and the manual _files_page — the two used to
    carry byte-identical copies of this block. A click on ``btn`` initiates the
    browser SSO (Login Flow v2, no password typed) and starts a 2 s QTimer whose
    every tick is one quick poll, so the wizard stays responsive while the user
    authenticates. Polling stops on success, on a 300 s deadline, or on error.

    ``btn`` / ``status`` feedback is identical across both call sites; the ONLY
    per-flow difference is where the acquired credential goes, so
    ``on_success(login_name, app_password)`` is the sole callback — the
    provisioned flow stashes it in a dict, the manual page writes it into two
    hidden fields. PyQt6 is imported lazily so main.py stays importable on the
    pure-Python test lane. Returns the QTimer (parented to ``page``; the return
    lets callers / the pytest-qt harness hold and drive it)."""
    from PyQt6.QtCore import QDateTime, QTimer

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
            on_success(res[0], res[1])
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
    return timer


def _euid() -> int:
    """Isole pour les tests : on ne peut pas devenir root dans un test."""
    return os.geteuid()


def cli() -> int:
    parser = argparse.ArgumentParser(prog="bluefox-welcome")
    parser.add_argument("--service-mode", action="store_true",
                        help="Accepte pour compatibilite avec le lanceur de session ; "
                             "sans effet (firstboot.service est retire).")
    parser.add_argument("--reset", action="store_true",
                        help="Dev only : retire les flags d'etat et relance le wizard.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    # ⚠️ JAMAIS EN ROOT (mesure en VM le 2026-09-14).
    # firstboot.service lancait cet agent en root, au demarrage, avant toute
    # session. Root trouvait les identifiants Nextcloud deposes par
    # l'installation, appliquait theme, fond d'ecran, montages rclone, comptes
    # KDE et PWA dans SON profil — Path.home() vaut /root —, ecrivait
    # /root/.config/bluefox-welcome/done, puis EFFACAIT les identifiants.
    # Quand l'usager ouvrait sa session, il n'y avait plus rien a faire : fond
    # d'ecran Fedora, pas de mode sombre, pas de montage.
    #
    # L'unite est retiree des recettes, mais un garde dans l'agent vaut mieux
    # qu'une absence dans une recette : n'importe quel lanceur futur, une
    # commande tapee avec sudo, reproduirait le defaut. On refuse AVANT de lire
    # la politique et AVANT de toucher aux identifiants, qui ne se reposent pas.
    if _euid() == 0:
        LOG.error("bluefox-welcome ne tourne pas en root : tout ce qu'il "
                  "applique vit dans le profil de l'usager, et il consommerait "
                  "des identifiants deposes pour une seule session. Il demarre "
                  "de lui-meme a l'ouverture de session (/etc/xdg/autostart).")
        return 1

    if args.reset:
        for flag in (DONE_FLAG,):
            try:
                flag.unlink()
                LOG.info("removed %s", flag)
            except FileNotFoundError:
                pass
        return 0

    if DONE_FLAG.exists():
        LOG.info("welcome already completed (%s)", DONE_FLAG)
        refresh_browser_policy()
        return 0

    tenant = load_tenant()
    # Prefer the install-staged policy (bf-policy/v2) over the baked tenant.json:
    # overlay its session branding and pre-fill the wizard with the authenticated
    # user. Absent on built-in-defaults installs — degrades to tenant.json alone.
    prov = load_provisioning()
    tenant = merge_branding(tenant, prov)
    return run_wizard(tenant, prefill_email=user_login(prov), prov=prov)


def refresh_browser_policy(home: Path | None = None) -> tuple[bool, str]:
    """A chaque ouverture de session : reecrit la politique Brave depuis la
    politique courante (#25966).

    La synchronisation horaire (bluefox-policy-sync, root) tient a jour la
    copie expurgee, mais elle ne peut pas ecrire dans le profil de la personne :
    c'est ici, dans la session, que les extensions et les PWA choisies dans
    Odoo rejoignent Brave. Effet au prochain demarrage de Brave.
    Sans politique (installation autonome), on ne touche a rien : on ne
    remplace pas une politique Brave par une politique vide.
    """
    prov = load_provisioning()
    if not prov:
        return False, "pas de politique : Brave laisse tel quel"
    tenant = merge_branding(load_tenant(), prov)
    ok, msg = apply_brave_policy(tenant, home=home, pwas=session_pwas(prov),
                                 extensions=browser_extensions(prov))
    (LOG.info if ok else LOG.warning)("rafraichissement Brave : %s", msg)
    return ok, msg


def select_flow(prov: dict | None) -> str:
    """'provisioned' when a valid staged policy names the user (we know who they
    are + their session prefs -> minimal interaction) ; 'seat' on a shared seat
    (bf_policy 18.0.2.12.0: a lab or a lent computer, the policy names nobody on
    purpose) ; else 'manual' (the full 5-page wizard). A tiny pure function so
    the dispatch is unit-testable without Qt."""
    if prov and isinstance(prov.get("seat"), dict):
        return "seat"
    return "provisioned" if (prov and user_login(prov)) else "manual"


def run_wizard(tenant: dict, prefill_email: str = "", prov: dict | None = None) -> int:
    """Firstboot entry point (BFOSI10 #22436). A device-flow install stages a
    bf-policy/v2 policy -> the provisioned flow (1 screen: confirm identity + one
    NC SSO + auto-apply). A built-in-defaults install has no policy -> the manual
    5-page wizard."""
    prov = prov or {}
    flow = select_flow(prov)
    if flow == "seat":
        return _run_seat_flow(tenant, prov)
    if flow == "provisioned":
        return _run_provisioned_flow(tenant, prov)
    return _run_manual_wizard(tenant, prefill_email, prov)


def _run_seat_flow(tenant: dict, prov: dict) -> int:
    """Shared seat: apply the session silently, ask nothing.

    Thirty students open a session on a lab computer; none of them should meet
    a five-page wizard asking who they are, when the directory login already
    said it. No Nextcloud SSO either: on a seat whose home is wiped at logout
    it would come back at every session. The person is the Unix user sssd
    opened the session for.
    """
    _finalize_and_apply(tenant=tenant, user_email=getpass.getuser(),
                        do_mount=False, nc_login_name="", nc_app_password="",
                        prov=prov)
    return 0


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
    """Premier demarrage d'une install par flux d'appareil.

    ⚠️ CE COMMENTAIRE DISAIT LE CONTRAIRE JUSQU'AU 2026-09-12. Il affirmait que
    « le justificatif de montage ne peut pas venir du jeton du flux d'appareil,
    voir auth/nc_login_flow.py » — et cette phrase justifiait de redemander un
    SSO a la personne qui venait d'autoriser l'installation trente secondes
    plus tot. C'est faux : l'echange de jetons RFC 8693 permet exactement ca,
    et le %pre s'en sert desormais pour frapper le mot de passe d'application
    avant meme que la machine ait redemarre.

    Donc : si l'installation a depose des identifiants, il n'y a plus AUCUNE
    interaction — on applique et on sort. Sinon on garde le parcours SSO par
    navigateur, inchange, comme repli."""
    creds = seat_credentials.lire()
    if creds:
        LOG.info("identifiants Nextcloud deposes par l'installation : "
                 "aucune connexion a redemander")
        _apply_from_policy(tenant, prov, creds)
        seat_credentials.effacer()
        return 0
    return _run_provisioned_flow_sso(tenant, prov)


def _run_provisioned_flow_sso(tenant: dict, prov: dict) -> int:
    """Le repli : une page, un SSO Nextcloud par navigateur. C'etait le seul
    chemin avant le 2026-09-12 ; il reste celui des postes dont la politique
    ne porte pas services.nextcloud.oidc_client_id, et celui de tout echec de
    l'echange cote installation."""
    try:
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

    # NC Login Flow v2 on a QTimer — shared with the manual _files_page. The only
    # per-flow difference is the success sink: here the credential lands in the
    # local ``creds`` dict that the exec() path below reads.
    def _store(login_name, app_password):
        creds["value"] = (login_name, app_password)

    _wire_nc_login_poll(page, btn, status, nc_url, _store)

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
                               QCheckBox, QLineEdit, QPushButton))
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
                QPushButton):
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

    # NC Login Flow v2 on a QTimer — shared with the provisioned flow. The only
    # per-flow difference is the success sink: here the credential lands in the
    # two hidden fields that _finalize reads via wizard.field().
    def _store(login_name, app_password):
        nc_login_name.setText(login_name)
        nc_app_password.setText(app_password)

    _wire_nc_login_poll(page, btn, status, nc_url, _store)

    page.setLayout(layout)
    return page


def _vault_page(tenant, QWizardPage, QVBoxLayout, QLabel, QPushButton):
    page = QWizardPage()
    page.setTitle("Vault Bitwarden")
    vault_url = get_service_url(tenant, "vaultwarden",
                                "https://vault.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        f"Bitwarden Desktop sera pré-configuré pour : <code>{vault_url}</code>"))
    layout.addWidget(QLabel(
        "Lance Bitwarden depuis le menu après l'assistant et connecte-toi "
        "— l'URL self-hosted sera déjà remplie."))
    btn_v = QPushButton(f"Ouvrir {vault_url}")
    btn_v.clicked.connect(lambda: _open_url_logged(vault_url))
    layout.addWidget(btn_v)
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
            f"<li>Brave : policy + extensions de l'organisation poussées au clic suivant</li>"
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

    ok, msg = apply_brave_policy(tenant, pwas=pwas,
                                 extensions=browser_extensions(prov))
    results.append(("brave_policy", ok, msg))

    # ⚠️ Plus de page « Comptes en ligne » de KDE ici (#25854). Elle s'ouvrait
    # par-dessus notre propre fenetre au premier demarrage, pour demander a la
    # main un compte Nextcloud que le SSO (Login Flow v2) vient deja de relier.

    ok, msg = apply_kde_theme(tenant)
    results.append(("kde_theme", ok, msg))

    # L'heure et la photo viennent de la politique de la personne, pas du
    # locataire : elles passent donc `prov`, et se taisent quand il manque.
    ok, msg = appliquer_horloges(prov)
    results.append(("horloges", ok, msg))

    ok, msg = appliquer_photo(prov)
    results.append(("photo", ok, msg))

    try:
        USER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USER_LOG.open("a") as fh:
            for name, ok, msg in results:
                fh.write(f"{'OK' if ok else 'FAIL'} {name}: {msg}\n")
    except Exception as e:
        LOG.warning("could not write %s: %s", USER_LOG, e)

    for name, ok, msg in results:
        LOG.info("apply %s: ok=%s msg=%s", name, ok, msg)

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Dans le profil, donc toujours inscriptible : plus d'exception avalee, et
    # un echec ici doit se VOIR — c'est lui qui decide si l'assistant revient.
    # Le parent du DRAPEAU, pas USER_CONFIG_DIR : les tests les separent.
    DONE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    DONE_FLAG.touch()
    email_file = USER_CONFIG_DIR / "user_email"
    write_private(email_file, user_email + "\n")
    LOG.info("wizard finished ; flagged done at %s", DONE_FLAG)


if __name__ == "__main__":
    sys.exit(cli())
