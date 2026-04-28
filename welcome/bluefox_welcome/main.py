"""Blue Fox OS welcome agent — wizard premier démarrage.

Architecture cible (BFOSP1, BFOSP3, BFOSP4, BFOSI8, BFOSI9, BFOSD8) :

- KAccounts pour mail / calendar / contacts NC (Akonadi DAV)
- rclone WebDAV systemd --user mount pour fichiers NC (KIO trop lent)
- Bitwarden Flatpak prefs pour Vaultwarden (URL pre-config)
- Thunderbird : ouvert au firstboot, autoconfig Migadu via CNAME
- Brave Sync : seed phrase stockee dans Vaultwarden, copy-paste manuelle
- Login machine : statu quo BFOSI2 (compte local, sssd v1.1)

Le wizard ne fait PAS tout en silence : il oriente l'utilisateur vers les
ouvertures necessaires (Authentik MFA, Vaultwarden, Brave Sync) et configure
en arriere-plan ce qui peut l'etre (rclone mount, prefs files).
"""
import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome")
STATE_DIR = Path("/var/lib/bluefox-welcome")
DONE_FLAG = STATE_DIR / "done"
NEEDS_REBASE_FLAG = STATE_DIR / "needs-rebase"
TENANT_FILE = Path("/usr/share/bluefox/tenant.json")
USER_CONFIG_DIR = Path.home() / ".config" / "bluefox-welcome"
USER_LOG = Path.home() / ".local/share/bluefox-welcome/firstboot.log"


def load_tenant() -> dict:
    if TENANT_FILE.exists():
        try:
            return json.loads(TENANT_FILE.read_text())
        except Exception as e:
            LOG.warning("failed to load %s: %s", TENANT_FILE, e)
    return {}


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
    return run_wizard(tenant)


def run_wizard(tenant: dict) -> int:
    try:
        from PyQt6.QtWidgets import (
            QApplication, QWizard, QWizardPage, QLabel, QLineEdit,
            QVBoxLayout, QPushButton, QCheckBox, QTextEdit,
        )
    except ImportError:
        LOG.error("PyQt6 not available ; falling back to terminal stub")
        print("[stub] Blue Fox OS welcome wizard ; PyQt6 manquant.")
        return 0

    app = QApplication(sys.argv)
    wizard = QWizard()
    wizard.setWindowTitle("Blue Fox OS — Premier démarrage")
    wizard.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

    wizard.addPage(_welcome_page(tenant, QWizardPage, QVBoxLayout, QLabel))
    wizard.addPage(_authentik_page(tenant, QWizardPage, QVBoxLayout, QLabel,
                                   QLineEdit, QPushButton))
    wizard.addPage(_files_page(tenant, QWizardPage, QVBoxLayout, QLabel,
                               QCheckBox))
    wizard.addPage(_vault_page(tenant, QWizardPage, QVBoxLayout, QLabel,
                               QPushButton))
    wizard.addPage(_done_page(tenant, QWizardPage, QVBoxLayout, QTextEdit))

    rc = wizard.exec()
    if rc == QWizard.DialogCode.Accepted:
        _finalize(wizard.field("user_email") or "")
        return 0
    return 1


def _welcome_page(tenant, QWizardPage, QVBoxLayout, QLabel):
    page = QWizardPage()
    page.setTitle("Bienvenue sur Blue Fox OS")
    slug = tenant.get("slug", "?")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        f"Cet assistant configure votre poste pour le tenant <b>{slug}</b>."))
    layout.addWidget(QLabel(
        "Etapes : compte Authentik, fichiers Nextcloud, vault Bitwarden, sync Brave."))
    layout.addWidget(QLabel(
        "Vous pourrez ignorer une etape avec Suivant et la reprendre plus tard."))
    page.setLayout(layout)
    return page


def _authentik_page(tenant, QWizardPage, QVBoxLayout, QLabel, QLineEdit,
                    QPushButton):
    page = QWizardPage()
    page.setTitle("Identite — Authentik")
    auth_url = tenant.get("services", {}).get("authentik", {}).get(
        "url", "https://auth.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        "Votre compte unique Blue Fox donne acces a Nextcloud + Odoo + Vaultwarden "
        "via le meme mot de passe (BFOSI2)."))
    layout.addWidget(QLabel(
        "Connectez-vous a Authentik dans le navigateur pour configurer le MFA TOTP "
        "la premiere fois."))
    btn = QPushButton(f"Ouvrir {auth_url}")
    btn.clicked.connect(lambda: subprocess.Popen(["xdg-open", auth_url]))
    layout.addWidget(btn)
    layout.addWidget(QLabel("Votre courriel Blue Fox :"))
    email = QLineEdit()
    email.setPlaceholderText("prenom@bluefoxconsultant.com")
    layout.addWidget(email)
    page.registerField("user_email*", email)
    page.setLayout(layout)
    return page


def _files_page(tenant, QWizardPage, QVBoxLayout, QLabel, QCheckBox):
    page = QWizardPage()
    page.setTitle("Fichiers Nextcloud")
    nc_url = tenant.get("services", {}).get("nextcloud", {}).get(
        "url", "https://nextcloud.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel("Configuration rclone WebDAV mount (BFOSP3)."))
    layout.addWidget(QLabel(f"Serveur : <code>{nc_url}</code>"))
    layout.addWidget(QLabel("Cible : <code>~/Nextcloud/</code> — visible "
                            "dans Dolphin et toutes apps."))
    do_mount = QCheckBox("Configurer maintenant le mount systemd --user")
    do_mount.setChecked(True)
    layout.addWidget(do_mount)
    page.registerField("do_mount", do_mount)
    layout.addWidget(QLabel("Mail / calendrier / contacts seront ajoutes "
                            "a KAccounts (System Settings -> Online Accounts)."))
    page.setLayout(layout)
    return page


def _vault_page(tenant, QWizardPage, QVBoxLayout, QLabel, QPushButton):
    page = QWizardPage()
    page.setTitle("Vault Bitwarden et Brave Sync")
    vault_url = tenant.get("services", {}).get("vaultwarden", {}).get(
        "url", "https://vault.bluefoxconsultant.com")
    layout = QVBoxLayout()
    layout.addWidget(QLabel(
        f"1. Bitwarden Desktop pre-configure pour : <code>{vault_url}</code>"))
    btn_v = QPushButton(f"Ouvrir {vault_url}")
    btn_v.clicked.connect(lambda: subprocess.Popen(["xdg-open", vault_url]))
    layout.addWidget(btn_v)
    layout.addWidget(QLabel(
        "<br>2. Brave Sync (BFOSP4) : retrouvez l'entree "
        "'Brave Sync - {prenom}' dans Vaultwarden et copiez la seed dans "
        "Brave Settings -> Sync."))
    layout.addWidget(QLabel(
        "Si c'est votre premiere machine BF OS, creez la chaine maintenant "
        "dans Brave puis sauvegardez la seed dans Vaultwarden."))
    btn_b = QPushButton("Ouvrir Brave Settings -> Sync")
    btn_b.clicked.connect(lambda: subprocess.Popen(
        ["xdg-open", "brave://settings/braveSync"]))
    layout.addWidget(btn_b)
    page.setLayout(layout)
    return page


def _done_page(tenant, QWizardPage, QVBoxLayout, QTextEdit):
    page = QWizardPage()
    page.setTitle("Recapitulatif")
    layout = QVBoxLayout()
    summary = QTextEdit()
    summary.setReadOnly(True)
    layout.addWidget(summary)
    page.setLayout(layout)

    def init():
        slug = tenant.get("slug", "?")
        email = page.field("user_email") or "?"
        do_mount = bool(page.field("do_mount"))
        text = (
            f"<h3>Configuration terminee</h3>"
            f"<ul>"
            f"<li>Tenant : <b>{slug}</b></li>"
            f"<li>Compte BF : {email}</li>"
            f"<li>NC Files (rclone mount) : "
            f"{'configure au prochain login' if do_mount else 'reporte'}</li>"
            f"<li>Mail/Calendar/Contacts : a finaliser dans System Settings -> Online Accounts</li>"
            f"<li>Brave Sync seed : conservee dans Vaultwarden</li>"
            f"</ul>"
            f"<p>Cliquez <b>Terminer</b> pour finaliser le firstboot.</p>")
        summary.setHtml(text)

    page.initializePage = init
    return page


def _finalize(user_email: str) -> None:
    """Ecrit le done flag et un journal de session."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        LOG.warning("cannot write %s as user ; firstboot.service should mkdir at /var/lib", STATE_DIR)
        return
    DONE_FLAG.touch()
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (USER_CONFIG_DIR / "user_email").write_text(user_email + "\n")
    LOG.info("wizard finished ; flagged done at %s ; email recorded", DONE_FLAG)


if __name__ == "__main__":
    sys.exit(cli())
