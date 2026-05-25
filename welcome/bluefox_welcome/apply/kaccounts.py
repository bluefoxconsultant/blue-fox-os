"""Open System Settings -> Online Accounts so user can add Nextcloud.

V1 approach: launch `kcmshell6 kcm_kaccounts` non-blocking. The user adds
the Nextcloud account manually (NC URL + credentials).

Why not full DBus auto-pairing yet? Since the SSO Login Flow v2 landed
(auth/nc_login_flow.py), an NC app-password *is* now available at firstboot — so
the old "circular: needs the password first" blocker is gone. What remains is
the KAccounts side: createAccount() needs a custom Authentik/NC kaccounts
provider (~1 day of work) to inject that app-password into Akonadi DAV. Deferred
to a follow-up; manual add stays the acceptable v1 trade-off. The rclone mount
(apply/rclone_mount.py) already consumes the Login Flow v2 app-password directly.
"""
import logging
import shutil
import subprocess

LOG = logging.getLogger("bluefox-welcome.apply.kaccounts")


def apply_kaccounts(
    tenant: dict,
    runner: callable = subprocess.Popen,
) -> tuple[bool, str]:
    binary = shutil.which("kcmshell6")
    if not binary:
        return False, "kcmshell6 introuvable (Plasma 6 manquant ?)"
    try:
        runner([binary, "kcm_kaccounts"])
        return True, "Page comptes KDE ouverte — ajoute Nextcloud manuellement"
    except Exception as e:
        LOG.exception("apply_kaccounts failed")
        return False, f"erreur kcmshell6 : {e}"
