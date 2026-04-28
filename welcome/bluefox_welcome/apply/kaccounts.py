"""Open System Settings -> Online Accounts so user can add Nextcloud.

V1 approach: launch `kcmshell6 kcm_kaccounts` non-blocking. The user adds
the Nextcloud account manually (NC URL + credentials).

Why not full DBus auto-pairing? KAccounts.createAccount() needs an app-password,
which Authentik OIDC flow doesn't produce. Auto-pairing requires either a
custom Authentik kaccounts-provider (1+ days of work) or fetching an NC OCS
app-password (circular: needs the password first). Acceptable v1 trade-off.
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
