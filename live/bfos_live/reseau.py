"""Reseau avant la session : connectivite et Wi-Fi par nmcli (#26870).

Sans reseau, pas de QR. L'ecran de connexion verifie la connectivite avant
chaque tentative et offre de choisir un Wi-Fi.
"""
from __future__ import annotations

import subprocess

from bfos_live.langue import _

NMCLI = "/usr/bin/nmcli"


def _nmcli(args, run=subprocess.run, timeout=30, secret=None):
    res = run([NMCLI, *args], capture_output=True, text=True, timeout=timeout,
              env={"PATH": "/usr/bin", "LANG": "C.UTF-8"})
    return res.returncode, res.stdout, res.stderr


def connectivite(run=subprocess.run):
    """full, limited, portal, none ou unknown."""
    try:
        code, out, _err = _nmcli(["-t", "-f", "CONNECTIVITY", "general"], run, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    etat = out.strip().splitlines()[0] if code == 0 and out.strip() else "unknown"
    return etat if etat in {"full", "limited", "portal", "none", "unknown"} else "unknown"


def _champs_terse(ligne):
    """Decoupe une ligne `nmcli -t` : ':' separe, '\\:' et '\\\\' echappes."""
    champs, cour, i = [], [], 0
    while i < len(ligne):
        c = ligne[i]
        if c == "\\" and i + 1 < len(ligne):
            cour.append(ligne[i + 1])
            i += 2
            continue
        if c == ":":
            champs.append("".join(cour))
            cour = []
        else:
            cour.append(c)
        i += 1
    champs.append("".join(cour))
    return champs


def reseaux_wifi(run=subprocess.run):
    """[(ssid, securise, signal, actif)], tries par signal, sans doublon."""
    try:
        code, out, _err = _nmcli(["-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL",
                                  "device", "wifi", "list", "--rescan", "auto"], run)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if code != 0:
        return []
    vus = {}
    for ligne in out.splitlines():
        champs = _champs_terse(ligne)
        if len(champs) < 4 or not champs[1]:
            continue
        actif, ssid, secu, signal = champs[0] == "*", champs[1], champs[2], champs[3]
        try:
            force = int(signal)
        except ValueError:
            force = 0
        securise = secu not in ("", "--")
        if ssid not in vus or force > vus[ssid][2]:
            vus[ssid] = (ssid, securise, force, actif or (vus.get(ssid, (0, 0, 0, False))[3]))
    return sorted(vus.values(), key=lambda r: (-r[3], -r[2], r[0].lower()))


def connecter_wifi(ssid, mot_de_passe=None, run=subprocess.run):
    """(ok, message). Le mot de passe passe en argument a nmcli : il n'est
    visible que de root et du greeter dans /proc, sur un systeme jetable."""
    args = ["--wait", "30", "device", "wifi", "connect", ssid]
    if mot_de_passe:
        args += ["password", mot_de_passe]
    try:
        code, _out, err = _nmcli(args, run, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, _("Échec de la connexion : {raison}").format(raison=exc)
    if code == 0:
        return True, _("Connecté à {ssid}.").format(ssid=ssid)
    msg = err.strip().splitlines()[-1] if err.strip() else _("raison inconnue")
    if "Secrets were required" in msg or "802-11-wireless-security" in msg:
        return False, _("Mot de passe Wi-Fi refusé.")
    return False, _("Échec de la connexion : {raison}").format(raison=msg)
