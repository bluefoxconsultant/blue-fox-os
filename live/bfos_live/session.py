#!/usr/bin/python3 -I
"""Preparation et fermeture d'une session de la cle live (#26870), en root.

Lance par `bfos-live-session@<nom>.service` (installe dans l'image sous
/usr/libexec/bfos-live/session) :

    session demarrer <nom>   ExecStart, pendant que l'ecran attend encore
    session arreter <nom>    ExecStop : fermeture de la session, ou extinction
    session garder <nom>     filet, quelques minutes apres : sans session
                             ouverte pour cette personne, l'unite est arretee

POURQUOI UNE UNITE SYSTEMD ET PAS L'AIDE PAM ELLE-MEME
L'aide tourne pendant l'authentification, dans le processus de greetd. Un
profil chiffre monte depuis la doit etre demonte a l'extinction meme si
personne ne ferme sa session (PC qu'on eteint, batterie a plat) : c'est le role
d'une unite active (RemainAfterExit), que systemd arrete avant de demonter le
reste. Et le montage vit alors dans l'espace de noms de systemd, visible par
la session qui va s'ouvrir.

CE QUE FAIT `demarrer`, DANS L'ORDRE
  1. Lit la demande deposee par l'aide (/run/bfos-live/demandes/<nom>.json,
     root 0600) : mode choisi, jeton d'acces du QR. La demande est effacee
     des sa lecture.
  2. Tire la politique de l'organisation (/api/v1/policy/me) avec ce jeton et
     en ecrit la copie expurgee lue par l'agent d'accueil, comme un poste
     installe. Ecrit aussi les listes Flatpak de la politique et lance
     system-flatpak-setup si une appli manque a l'image.
  3. Mode persistant : demande a Odoo la cle du profil (sequestre
     bf_policy_live), cree au besoin la partition de donnees dans l'espace
     libre de la cle, puis le fichier LUKS2 de la personne, et le monte sur
     son dossier personnel.
  4. Frappe un mot de passe d'application Nextcloud (echange RFC 8693), sauf
     si le profil persistant a deja ete configure par l'agent d'accueil.
  5. Ecrit le resultat pour l'aide (/run/bfos-live/resultats/<nom>.json).

Seule l'etape 3 est bloquante : sans politique ou sans Nextcloud, la session
s'ouvre quand meme (l'agent retombe sur ses defauts). Un profil persistant qui
ne s'ouvre pas, lui, ne doit JAMAIS laisser croire a la personne que son
travail sera garde : l'aide lui propose alors une session ephemere, en le
disant.

La cle de dechiffrement ne touche jamais un disque : elle passe d'Odoo a
cryptsetup par un tube.
"""
from __future__ import annotations

import base64
import ctypes
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

_ICI = os.path.dirname(os.path.abspath(__file__))
for _chemin in (_ICI, os.path.join(_ICI, ".."), os.path.join(_ICI, "..", "..", "install")):
    if os.path.exists(os.path.join(_chemin, "bfos_provision.py")):
        sys.path.insert(0, os.path.abspath(_chemin))
for _chemin in (_ICI, os.path.join(_ICI, "..")):
    if os.path.isdir(os.path.join(_chemin, "bfos_live")):
        sys.path.insert(0, os.path.abspath(_chemin))
        break

from bfos_live import pam_helper as ph  # noqa: E402
from bfos_live import itinerant as it  # noqa: E402
from bfos_live import langue  # noqa: E402
from bfos_live import organisation  # noqa: E402
from bfos_live.langue import _  # noqa: E402

MODES = ("ephemere", "persistant", "itinerant")
UNITE_SAUVEGARDE = "bfos-live-sauvegarde@{nom}.timer"
UNITE_ZONE = "bfos-live-flatpak-maj.service"
PARTLABEL = "BFOS_PROFILS"
SOURCE_LIVE = "/run/initramfs/live"
ETAT_WELCOME = "/var/lib/bluefox-welcome"
POLITIQUE_PUBLIQUE = os.path.join(ETAT_WELCOME, "policy-public.json")
IDENTIFIANTS_NC = os.path.join(ETAT_WELCOME, "nc-credentials.json")
FLATPAK_DIR = "/etc/bluebuild/default-flatpaks/system"
FLATPAK_APPS = "/var/lib/flatpak/app"
# Mode « garder sur la cle » : caches en memoire vive (#26870, arbitre le 2026-10-27,
# « ne rien conserver de Brave, Firefox, etc. entre les sessions ») ; sur la cle,
# ils ralentissaient tout. Brave et Firefox (Flatpak) cachent sous ~/.var/app/<id>/cache.
# Plafonds (memoire vive) : ~/.cache, puis chaque appli ; un tmpfs ne prend que ce qui y est ecrit.
TAILLE_CACHE = "20%"
TAILLE_CACHE_APPLI = "10%"
# Fuseau et clavier de la personne (#26870, essai du 2026-10-26), comme a
# l'installation (install/bfos_apply.py).
ZONEINFO = "/usr/share/zoneinfo"
CLAVIER_SYSTEME = "/etc/X11/xorg.conf.d/00-keyboard.conf"
VCONSOLE = "/etc/vconsole.conf"
FUSEAU_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$")
# Langue de la session (#26870, tournage du 2026-10-27 : Plasma restait en anglais).
LOCALE_CONF = "/etc/locale.conf"
LOCALE_RE = re.compile(r"[a-z]{2,3}_[A-Z]{2}\.UTF-8")   # fullmatch ; pays exige (fr.UTF-8 n'existe pas)
# Etapes de l'ouverture, relayees a l'ecran de connexion par l'aide PAM.
ETAPES = 5
DRAPEAU_ACCUEIL = ".config/bluefox-welcome/done"
RCLONE_PROFIL = ".config/rclone/rclone.conf"
MIO = 1024 * 1024
MARGE_LIBRE = 256 * MIO
PROFIL_MINIMUM = 1024 * MIO
# Profil qui grandit au besoin (#26870, 2026-10-25) : cree a profile_size_mb,
# agrandi a l'ouverture quand il depasse ce seuil, d'au plus sa taille (il
# double) et de PAS_MAX, en laissant profile_reserve_mb a une autre personne.
# PAS_MAX borne l'attente : sur exFAT (Ventoy), la reservation ECRIT les octets
# (1,2 Gio en 13 s sur SSD, mesure au banc), soit ~1 min par 4 Gio sur une cle.
SEUIL_AGRANDIR = 0.80
PAS_MIN = 1024 * MIO
PAS_MAX = 4 * 1024 * MIO
# exFAT (Ventoy) : pas de journal, et le fichier du profil passe d'un bloc
# contigu a une chaine FAT des qu'il grandit. Petits pas, sync entre chacun :
# une cle retiree pendant la croissance perd au plus le dernier pas.
PAS_MAX_EXFAT = 1024 * MIO
PAS_PAR_PASSE = 8
UNITE_AGRANDIR = "bfos-live-agrandir@{nom}.service"
DRAPEAU_NC_REFAIT = ".config/bluefox-welcome/nc-refait"
# Deux croissances, ou une croissance et une creation, ne comptent pas la
# meme place libre : calcul et reservation sous ce verrou commun.
VERROU_PLACE = "place.lock"
MAGIC_EXFAT = "2011bab0"
# Cle Ventoy : le profil vit dans un dossier de la partition de donnees de
# Ventoy, a cote des ISO. Systemes de fichiers pris en charge, avec le pilote
# et les options de montage (tout reste a root). exFAT est le format par
# defaut de Ventoy ; NTFS n'y est pas : le pilote ntfs3 n'est pas dans le
# noyau de l'image (kernel-modules-extra), mesure a la construction du 10-24.
VENTOY_DM = "bfos-ventoy"
VENTOY_DOSSIER = "bfos-profils"
FS_VENTOY = {"exfat": ("exfat", "umask=077"), "ext4": ("ext4", ""), "xfs": ("xfs", "")}
CHEMIN = "/usr/sbin:/usr/bin"


class Echec(Exception):
    """Echec explique a la personne (message) et au journal (raison)."""

    def __init__(self, message, raison=None):
        super().__init__(message)
        self.message = message
        self.raison = raison or message


def journal(msg, prio=None):
    ph._journal(msg, prio if prio is not None else ph.syslog.LOG_INFO)


# ------------------------------------------------------------- les chemins
def dossiers(cfg):
    base = cfg["state_dir"]
    return {
        "demandes": os.path.join(base, "demandes"),
        "resultats": os.path.join(base, "resultats"),
        "etat": os.path.join(base, "etat"),
        "profils": os.path.join(base, "profils"),
        "ventoy": os.path.join(base, "ventoy"),
        "connexions": os.path.join(base, "connexions"),
        "itinerant": os.path.join(base, "itinerant"),
    }


def lire_json_root(chemin):
    """Lit un JSON qui doit appartenir a root et n'etre lisible que par lui.
    Rend None s'il manque. Un fichier d'un autre proprietaire est une attaque."""
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0
                or st.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            raise Echec("Erreur interne.", f"fichier suspect: {chemin}")
        return json.loads(os.read(fd, 1 << 20))
    finally:
        os.close(fd)


def ecrire_resultat(cfg, nom, ok, message="", **extra):
    d = dossiers(cfg)["resultats"]
    ph.dossier_sur(d, 0o700)
    ph._ecrire(os.path.join(d, nom + ".json"),
               dict(ok=bool(ok), message=message, **extra), mode=0o600)


# ------------------------------------------------------------- HTTP
class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, "redirection refusee", headers, fp)


_OUVREUR = urllib.request.build_opener(_SansRedirection())


def requete(url, methode="GET", jeton=None, corps=None, entetes=None, delai=30):
    """HTTPS seulement, sans redirection. Rend (statut, texte) ; une erreur
    HTTP rend son statut au lieu de lever."""
    if not str(url).startswith("https://"):
        raise OSError(f"adresse non HTTPS refusee: {url}")
    donnees = None
    req = urllib.request.Request(url, method=methode)
    if corps is not None:
        donnees = json.dumps(corps).encode()
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if jeton:
        req.add_header("Authorization", f"Bearer {jeton}")
    for k, v in (entetes or {}).items():
        req.add_header(k, v)
    try:
        with _OUVREUR.open(req, data=donnees, timeout=delai) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        try:
            texte = exc.read().decode()
        except Exception:  # noqa: BLE001
            texte = ""
        return exc.code, texte


# ------------------------------------------------------------- la politique
def tirer_politique(cfg, jeton, http=requete):
    """La politique de la personne, ou None (jamais bloquant)."""
    url = cfg.get("policy_url")
    if not url:
        return None
    try:
        statut, texte = http(url, jeton=jeton)
    except Exception as exc:  # noqa: BLE001
        journal(f"politique injoignable: {exc}", ph.syslog.LOG_WARNING)
        return None
    if statut != 200:
        journal(f"politique refusee: HTTP {statut}", ph.syslog.LOG_WARNING)
        return None
    try:
        politique = json.loads(texte)
    except ValueError:
        return None
    if not isinstance(politique, dict) or politique.get("schema") != "bf-policy/v2":
        journal("politique d'un schema inattendu, ignoree", ph.syslog.LOG_WARNING)
        return None
    return politique


def ecrire_politique(politique, apply_mod, chemin=None):
    """Copie EXPURGEE, 0644 : c'est celle que l'agent d'accueil lit."""
    chemin = chemin or POLITIQUE_PUBLIQUE
    os.makedirs(os.path.dirname(chemin), mode=0o755, exist_ok=True)
    ph._ecrire_texte(chemin, apply_mod.render_public_policy(politique), mode=0o644)


def applis_de_politique(politique):
    apps = (politique or {}).get("apps") or {}
    if not isinstance(apps, dict):
        return [], []
    install = [a for a in apps.get("install") or [] if isinstance(a, str)]
    retirer = [a for a in apps.get("remove") or [] if isinstance(a, str)]
    return install, retirer


def installe(app_id, run=subprocess.run):
    try:
        return run(["flatpak", "info", "--system", app_id], capture_output=True,
                   timeout=20, env={"PATH": CHEMIN}).returncode == 0
    except Exception:  # noqa: BLE001
        return False


FLATPAK_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_][A-Za-z0-9_-]*){2,}")


def appliquer_applis(politique, apply_mod, run=subprocess.run, dossier=None,
                     est_installe=None, generique=False):
    """Ecrit les listes de la politique et lance l'installation des applis
    absentes de l'image. Elles s'installent en memoire vive : la prochaine ISO
    les portera. Rend la liste des applis a installer.

    Seuls des identifiants Flatpak bien formes passent (le script root de
    l'image les recoit tels quels). Sur une cle generique, la politique vient
    d'une organisation choisie par la personne : elle ajoute, elle ne RETIRE
    jamais une appli de la cle, partagee (relecture du 2026-10-27)."""
    est_installe = est_installe or (lambda a: installe(a, run=run))
    dossier = dossier or FLATPAK_DIR
    install, retirer = applis_de_politique(politique)
    install = [a for a in install if isinstance(a, str) and FLATPAK_ID_RE.fullmatch(a)]
    retirer = [] if generique else [a for a in retirer
                                    if isinstance(a, str) and FLATPAK_ID_RE.fullmatch(a)]
    os.makedirs(dossier, mode=0o755, exist_ok=True)
    for cle, ids in (("install", install), ("remove", retirer)):
        ph._ecrire_texte(os.path.join(dossier, cle),
                         apply_mod.render_flatpak_list(ids, cle), mode=0o644)
    manquantes = [a for a in install if not est_installe(a)]
    if manquantes:
        journal(f"applis de la politique absentes de l'image: {', '.join(manquantes)}")
        run(["systemctl", "start", "--no-block", "system-flatpak-setup.service"],
            capture_output=True, timeout=30, env={"PATH": CHEMIN})
    return manquantes


# ------------------------------------------------------------- Nextcloud
def identifiants_nextcloud(cfg, jeton, politique, compte, bp, chemin=None):
    """Frappe un mot de passe d'application et le remet a la personne, 0600.
    Rend le depot, ou None (l'agent proposera alors le SSO par navigateur)."""
    if not politique:
        return None
    chemin = chemin or IDENTIFIANTS_NC
    env = {"BFOS_OIDC_TOKEN_URL": cfg["token_url"], "BFOS_OIDC_CLIENT_ID": cfg["client_id"]}
    tmp = chemin + ".root"
    depot = bp.obtenir_identifiants_nextcloud(
        jeton, politique, env=env, path=tmp,
        out=lambda m: journal(m.strip()))
    if not depot:
        return None
    os.chown(tmp, compte.pw_uid, compte.pw_gid)
    os.chmod(tmp, 0o600)
    os.replace(tmp, chemin)
    return depot


def annoncer(cfg, nom, etape, texte):
    """Ou en est l'ouverture : l'aide PAM le relaie a l'ecran de connexion, a
    la place du QR (#26870, essai du 2026-10-26 : un carre blanc pendant des
    minutes). Jamais bloquant."""
    try:
        d = os.path.join(cfg["state_dir"], "progres")
        ph.dossier_sur(d, 0o700)
        ph._ecrire(os.path.join(d, nom + ".json"),
                   {"etape": etape, "total": ETAPES, "texte": texte}, mode=0o600)
    except Exception as exc:  # noqa: BLE001
        journal(f"progression non publiee: {exc!r}", ph.syslog.LOG_WARNING)


def ecrire_sous(maison, relatif, contenu, compte):
    """Cree maison/relatif avec `contenu`, au nom de la personne, sans suivre
    aucun lien. Un fichier deja la (choix de la personne) est laisse tel quel.
    True s'il a ete ecrit. Le dossier parent doit exister."""
    parties = relatif.split("/")
    try:
        fd = _dossier_sous(maison, parties[:-1])
    except OSError as exc:
        journal(f"{relatif} non ecrit: {exc}", ph.syslog.LOG_WARNING)
        return False
    try:
        f = os.open(parties[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    | os.O_NONBLOCK, 0o644, dir_fd=fd)
    except FileExistsError:
        return False
    except OSError as exc:
        journal(f"{relatif} non ecrit: {exc}", ph.syslog.LOG_WARNING)
        return False
    finally:
        os.close(fd)
    try:
        os.fchown(f, compte.pw_uid, compte.pw_gid)
        os.write(f, contenu.encode())
    finally:
        os.close(f)
    return True


def completer_localerc(maison, contenu, compte):
    """Un plasma-localerc sans [Translations] LANGUAGE n'est pas un choix de la personne :
    startplasma l'ecrit lui-meme ([Formats] seul) au premier demarrage, et il figeait l'anglais
    (relecture adverse du 2026-10-27). On le complete, sans suivre de lien, seulement s'il est
    a elle, ordinaire et sans autre lien dur. True s'il a ete complete."""
    try:
        fd = _dossier_sous(maison, [".config"])
    except OSError:
        return False
    try:
        f = os.open("plasma-localerc", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError:
        return False
    finally:
        os.close(fd)
    try:
        st = os.fstat(f)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != compte.pw_uid or st.st_nlink != 1:
            journal("plasma-localerc laisse tel quel (proprietaire ou type inattendu)",
                    ph.syslog.LOG_WARNING)
            return False
        actuel = os.read(f, 65536).decode("utf-8", "replace")
        if re.search(r"^LANGUAGE=", actuel, re.M):
            return False
        os.lseek(f, 0, os.SEEK_END)
        os.write(f, ("\n" + contenu).encode())
        return True
    finally:
        os.close(f)


def appliquer_preferences(politique, apply_mod, compte, maison, run=subprocess.run):
    """Fuseau, langue et clavier de la politique, comme sur un poste installe : un
    kxkbrc seme dans la maison s'il n'y en a pas (KWin le lit avant le reglage
    du systeme ; un choix fait ensuite par la personne, dans un profil garde,
    reste le sien), puis le reglage du systeme."""
    install = (politique or {}).get("install") or {}
    fuseau = str(install.get("timezone") or "")
    if FUSEAU_RE.match(fuseau) and os.path.isfile(os.path.join(ZONEINFO, fuseau)):
        try:
            r = run(["timedatectl", "set-timezone", fuseau], capture_output=True, timeout=30,
                    env={"PATH": CHEMIN})
            if r.returncode != 0:
                journal(f"fuseau {fuseau} refuse: {r.stderr!r}", ph.syslog.LOG_WARNING)
        except Exception as exc:  # noqa: BLE001 - le clavier passe quand meme
            journal(f"fuseau {fuseau} non pose: {exc!r}", ph.syslog.LOG_WARNING)
    locale = str(install.get("locale") or "")
    if LOCALE_RE.fullmatch(locale):
        # plasma-localerc d'abord : startplasma le lit et en tire LANG et LANGUAGE ;
        # locale.conf pour le reste du systeme. Un choix fait ensuite reste le sien.
        langue = locale.split(".")[0]
        contenu = (f"[Formats]\nLANG={locale}\n\n[Translations]\n"
                   f"LANGUAGE={langue}:{langue.split('_')[0]}\n")
        for faire, quoi in ((lambda: ecrire_sous(maison, ".config/plasma-localerc", contenu, compte)
                             or completer_localerc(maison, contenu, compte), "plasma-localerc"),
                            (lambda: ph._ecrire_texte(LOCALE_CONF, f"LANG={locale}\n", mode=0o644),
                             LOCALE_CONF)):
            try:
                faire()
            except Exception as exc:  # noqa: BLE001
                journal(f"langue : {quoi} non ecrit: {exc!r}", ph.syslog.LOG_WARNING)
    if not (install.get("keymap") or install.get("x_layout")):
        return
    # kxkbrc D'ABORD (c'est le seul que KWin lit), puis chaque fichier du
    # systeme a part : /etc/X11/xorg.conf.d n'existe pas dans l'image (aucun
    # serveur Xorg), et son echec emportait tout le reste (relecture adverse
    # du 2026-10-26).
    etapes = [(lambda: ecrire_sous(maison, ".config/kxkbrc", apply_mod.render_kxkbrc(politique),
                                   compte), "kxkbrc")]
    for chemin, rendu in ((CLAVIER_SYSTEME, apply_mod.render_x11_keymap_conf),
                          (VCONSOLE, apply_mod.render_vconsole_conf)):
        def ecrire(chemin=chemin, rendu=rendu):
            os.makedirs(os.path.dirname(chemin), mode=0o755, exist_ok=True)
            ph._ecrire_texte(chemin, rendu(politique), mode=0o644)
        etapes.append((ecrire, chemin))
    for faire, quoi in etapes:
        try:
            faire()
        except Exception as exc:  # noqa: BLE001
            journal(f"clavier : {quoi} non ecrit: {exc!r}", ph.syslog.LOG_WARNING)


def nextcloud_a_refaire(maison, politique):
    """La politique sert un Nextcloud, mais ce profil n'a jamais pu s'y monter."""
    nc = ((politique or {}).get("services") or {}).get("nextcloud") or {}
    return bool(nc.get("url")) and not os.path.lexists(os.path.join(maison, RCLONE_PROFIL))


def _dossier_sous(maison, parties):
    """Descend dans maison/parties sans suivre aucun lien ; rend le fd."""
    fd = os.open(maison, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for partie in parties:
            suivant = os.open(partie, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = suivant
    except BaseException:
        os.close(fd)
        raise
    return fd


def _dossier_cree_sous(maison, parties, compte):
    """Comme _dossier_sous, en creant au nom de la personne (0700) ce qui manque."""
    fd = os.open(maison, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for partie in parties:
            try:
                os.mkdir(partie, 0o700, dir_fd=fd)
                cree = True
            except FileExistsError:
                cree = False
            suivant = os.open(partie, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = suivant
            if cree:
                os.fchown(fd, compte.pw_uid, compte.pw_gid)
    except BaseException:
        os.close(fd)
        raise
    return fd


MS_NOSUID, MS_NODEV = 2, 4


def vider_par_fd(fd):
    """Vide le dossier tenu par `fd` sans suivre aucun lien (un lien est retire, pas
    suivi). Sert au cache d'avant, reste sur la cle sous le tmpfs (relecture du
    2026-10-27 : il y serait reste cache, et garde, pour toujours)."""
    for _racine, dossiers, fichiers, rfd in os.fwalk(".", dir_fd=fd, topdown=False,
                                                     follow_symlinks=False):
        for nom in fichiers:
            os.unlink(nom, dir_fd=rfd)
        for nom in dossiers:
            try:
                os.rmdir(nom, dir_fd=rfd)
            except NotADirectoryError:
                os.unlink(nom, dir_fd=rfd)


def monter_tmpfs_sur(fd, donnees):
    """mount(2) d'un tmpfs SUR le dossier tenu par `fd` (/proc/self/fd/N, resolu par
    le noyau au moment de l'appel). mount(8) 2.41 refuse cette cible (« wrong fs
    type », il passe par la nouvelle API) : mesure le 2026-10-27, d'ou l'appel direct,
    comme systemd. Rend None, ou le texte de l'erreur."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.mount(b"bfos-cache", f"/proc/self/fd/{fd}".encode(), b"tmpfs",
                  ctypes.c_ulong(MS_NOSUID | MS_NODEV), donnees.encode()) == 0:
        return None
    return os.strerror(ctypes.get_errno())


def caches_en_memoire(maison, compte, run=subprocess.run, applis=None, monter=monter_tmpfs_sur):
    """Mode « garder sur la cle » : ~/.cache et le cache de chaque Flatpak dans
    un tmpfs a la personne, perdu a la fermeture. Monte PAR DESCRIPTEUR
    (mount(2) sur /proc/self/fd/N) : la maison est a la personne, un lien
    pose a la place d'un dossier ne dirige pas un montage de root ailleurs.
    Rend le nombre de montages. Jamais bloquant."""
    if applis is None:
        try:
            applis = sorted(a for a in os.listdir(FLATPAK_APPS) if FLATPAK_ID_RE.fullmatch(a))
        except OSError:
            applis = []
    faits = 0
    for parties in [[".cache"]] + [[".var", "app", a, "cache"] for a in applis]:
        relatif = "/".join(parties)
        try:
            fd = _dossier_cree_sous(maison, parties, compte)
        except OSError as exc:
            journal(f"cache {relatif} laisse sur la cle: {exc}", ph.syslog.LOG_WARNING)
            continue
        try:
            if os.fstat(fd).st_uid != compte.pw_uid:
                journal(f"cache {relatif} laisse sur la cle (proprietaire inattendu)",
                        ph.syslog.LOG_WARNING)
                continue
            try:
                vider_par_fd(fd)
            except OSError as exc:
                journal(f"cache {relatif} : ancien contenu non vide ({exc})", ph.syslog.LOG_WARNING)
            donnees = (f"mode=0700,uid={compte.pw_uid},gid={compte.pw_gid},"
                       f"size={TAILLE_CACHE if parties == ['.cache'] else TAILLE_CACHE_APPLI}")
            erreur = monter(fd, donnees)
            if erreur is None:
                faits += 1
            else:
                journal(f"cache {relatif} laisse sur la cle: {erreur}", ph.syslog.LOG_WARNING)
        finally:
            os.close(fd)
    return faits


def poser_sous(maison, relatif):
    """Cree maison/relatif (fichier vide, a root) sans suivre aucun lien.
    True s'il existe ensuite. Le dossier parent doit exister."""
    parties = relatif.split("/")
    try:
        fd = _dossier_sous(maison, parties[:-1])
    except OSError as exc:
        journal(f"{relatif} non pose: {exc}", ph.syslog.LOG_WARNING)
        return False
    try:
        os.close(os.open(parties[-1], os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o644, dir_fd=fd))
        return True
    except OSError as exc:
        journal(f"{relatif} non pose: {exc}", ph.syslog.LOG_WARNING)
        return False
    finally:
        os.close(fd)


def retirer_sous(maison, relatif):
    """Retire maison/relatif sans suivre aucun lien : la maison appartient a la
    personne, root ne se laisse pas diriger ailleurs. True si c'est fait."""
    parties = relatif.split("/")
    try:
        fd = _dossier_sous(maison, parties[:-1])
    except FileNotFoundError:
        return True
    except OSError as exc:
        journal(f"{relatif} non retire: {exc}", ph.syslog.LOG_WARNING)
        return False
    try:
        os.unlink(parties[-1], dir_fd=fd)
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        journal(f"{relatif} non retire: {exc}", ph.syslog.LOG_WARNING)
        return False
    finally:
        os.close(fd)


def revoquer_nextcloud(depot, http=requete):
    """Retire le mot de passe d'application. Delai court. True s'il ne vaut
    plus rien (revoque, ou deja inconnu : 401)."""
    if not depot or not depot.get("url"):
        return False
    jeton = base64.b64encode(f"{depot['login']}:{depot['app_password']}".encode()).decode()
    try:
        statut, _corps = http(f"{depot['url'].rstrip('/')}/ocs/v2.php/core/apppassword",
                         methode="DELETE", delai=5,
                         entetes={"Authorization": f"Basic {jeton}", "OCS-APIRequest": "true"})
        journal(f"mot de passe Nextcloud revoque (HTTP {statut})")
        return statut in (200, 401)
    except Exception as exc:  # noqa: BLE001
        journal(f"revocation Nextcloud echouee: {exc}", ph.syslog.LOG_WARNING)
        return False


# ------------------------------------------------------------- la cle du profil
def cle_du_profil(cfg, jeton, http=requete):
    """32 octets servis par le sequestre d'Odoo (bf_policy_live)."""
    url = cfg.get("profile_key_url")
    if not url:
        raise Echec("Le profil persistant n'est pas offert sur cette clé USB.",
                    "profile_key_url absent de live.json")
    try:
        statut, texte = http(url, methode="POST", jeton=jeton, corps={"create": True})
    except Exception as exc:  # noqa: BLE001
        raise Echec("Le serveur de ton organisation ne répond pas : profil persistant indisponible.",
                    f"sequestre injoignable: {exc}") from exc
    if statut == 403:
        raise Echec("Ton profil persistant a été révoqué par ton organisation.",
                    "sequestre: revoque")
    if statut != 200:
        raise Echec("Le serveur de ton organisation refuse de servir ton profil.",
                    f"sequestre: HTTP {statut} {texte[:200]}")
    try:
        cle = base64.b64decode(json.loads(texte)["key"], validate=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise Echec("Réponse illisible du serveur de ton organisation.",
                    f"sequestre: reponse illisible ({exc})") from exc
    if len(cle) < 32:
        raise Echec("Réponse illisible du serveur de ton organisation.", "cle trop courte")
    return cle


# ------------------------------------------------------------- Odoo sans retaper
def lien_odoo(cfg, jeton, http=requete):
    """Lien de connexion Odoo a usage unique (bf_policy_live), ou None.

    Le jeton du QR ne quitte jamais root : seul ce lien, valable 5 minutes et
    une seule fois, est remis a la session de la personne. Il garde le chemin
    /odoo de la PWA epinglee (le jeton est dans la requete) pour que Brave
    range la fenetre sous la meme icone."""
    url = cfg.get("login_link_url")
    if not url:
        return None
    try:
        statut, texte = http(url, methode="POST", jeton=jeton, corps={}, delai=15)
        lien = json.loads(texte).get("url") if statut == 200 else None
    except Exception as exc:  # noqa: BLE001 - jamais bloquant
        journal(f"lien Odoo non obtenu: {exc!r}", ph.syslog.LOG_WARNING)
        return None
    if statut != 200:
        journal(f"lien Odoo refuse: HTTP {statut}", ph.syslog.LOG_WARNING)
        return None
    hote = urllib.parse.urlsplit(url).netloc
    if (not isinstance(lien, str) or not lien.startswith(f"https://{hote}/odoo")
            or any(c in lien for c in "\r\n\x00 ")):
        journal("lien Odoo inattendu, ignore", ph.syslog.LOG_WARNING)
        return None
    # Session Authentik de Brave (flux bfos-live-navigateur) : Odoo passe ce
    # code au flux apres avoir consomme son lien. Une empreinte, jamais le jeton.
    sep = "&" if "?" in lien else "?"
    return f"{lien}{sep}bfos_auth={code_navigateur(jeton)}"


def code_navigateur(jeton):
    """Code a usage unique de la session Authentik du navigateur : empreinte du
    jeton d'acces de la cle, que seul Authentik sait rapprocher d'un jeton
    (live/authentik/bfos-live-navigateur.yaml). 43 caracteres base64url."""
    brut = hashlib.sha256(("bfos-live-nav:" + jeton).encode()).digest()
    return base64.urlsafe_b64encode(brut).rstrip(b"=").decode()


def deposer_lien_odoo(cfg, nom, compte, lien):
    """/run/bfos-live/connexions/<nom> : dossier de root (0711), fichier a la
    personne en 0400. Jamais dans son dossier personnel, ou root suivrait un
    lien symbolique qu'elle aurait pose."""
    d = dossiers(cfg)["connexions"]
    ph.dossier_sur(d, 0o711)
    chemin = os.path.join(d, nom)
    retirer_lien_odoo(cfg, nom)
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    try:
        os.write(fd, (lien + "\n").encode())
        os.fchown(fd, compte.pw_uid, compte.pw_gid)
    finally:
        os.close(fd)


def retirer_lien_odoo(cfg, nom):
    try:
        os.unlink(os.path.join(dossiers(cfg)["connexions"], nom))
    except FileNotFoundError:
        pass


# ------------------------------------------------------------- la cle USB
CHIFFREMENT = "aes-xts-plain64"


def lancer(argv, run=subprocess.run, entree=None, delai=120, accepter=None):
    """Lance une commande, leve Echec si elle echoue. `accepter` : texte de
    sortie qui vaut succes malgre un code non nul (sgdisk sur un disque en
    service rend un avertissement quand le noyau refuse de relire la table)."""
    res = run(argv, input=entree, capture_output=True, timeout=delai,
              env={"PATH": CHEMIN, "LC_ALL": "C"})
    out = res.stdout
    out = out.decode(errors="replace") if isinstance(out, bytes) else (out or "")
    if res.returncode != 0 and not (accepter and accepter in out):
        err = res.stderr.decode(errors="replace") if isinstance(res.stderr, bytes) else str(res.stderr)
        raise Echec("Ton profil n'a pas pu s'ouvrir sur cette clé USB.",
                    f"{' '.join(argv[:3])} a echoue ({res.returncode}): {err.strip()[:300]}")
    return out


def _sgdisk(argv, run):
    return lancer(argv, run=run, accepter="operation has completed successfully")


def support_de_la_cle(run=subprocess.run, sysfs="/sys"):
    """D'ou la session a demarre : {"genre": "gpt", "disque", "source"} pour
    une cle gravee (dd, Fedora Media Writer), {"genre": "ventoy", ...} pour une
    ISO lancee par Ventoy (projection device-mapper du fichier ISO).

    ⚠️ On ne prend JAMAIS une partition BFOS_PROFILS trouvee ailleurs que sur
    ce disque : une autre cle branchee a cote pourrait en porter une."""
    champs = lancer(["findmnt", "-n", "-o", "SOURCE,MAJ:MIN", SOURCE_LIVE], run=run).split()
    source = champs[0] if champs else ""
    majmin = champs[1] if len(champs) > 1 else ""
    if not source.startswith("/dev/") or source.startswith("/dev/loop"):
        raise Echec("Cette clé USB ne peut pas garder de profil (support en lecture seule).",
                    f"source live inattendue: {source!r}")
    # Ventoy cree sa projection (/dev/ventoy) dans l'initramfs, sans udev : lsblk
    # ne la liste pas (rien, code 32, vu sous Ventoy 1.1.18 le 2026-10-24). C'est
    # donc sysfs qui dit si la source est une projection device-mapper.
    if re.fullmatch(r"\d+:\d+", majmin) and os.path.isdir(os.path.join(sysfs, "dev", "block", majmin, "dm")):
        return dict(ventoy_sous_jacent(majmin, run=run, sysfs=sysfs), genre="ventoy", source=source)
    infos = json.loads(lancer(["lsblk", "-J", "-n", "-p", "-o", "NAME,TYPE,PKNAME", source],
                              run=run))
    dev = (infos.get("blockdevices") or [{}])[0]
    if dev.get("type") == "disk":
        return {"genre": "gpt", "disque": dev["name"], "source": source}
    if dev.get("type") == "part" and dev.get("pkname"):
        return {"genre": "gpt", "disque": dev["pkname"], "source": source}
    raise Echec("Cette clé USB ne peut pas garder de profil.", f"support inattendu: {dev!r}")


def disque_de_la_cle(run=subprocess.run):
    """(disque, source) d'une cle gravee ; Echec pour tout autre support."""
    s = support_de_la_cle(run=run)
    if s["genre"] != "gpt":
        raise Echec("Cette clé USB ne peut pas garder de profil.", f"support {s['genre']}")
    return s["disque"], s["source"]


def _lire_sysfs(chemin):
    with open(chemin) as f:
        return f.read().strip()


def ventoy_sous_jacent(dm_majmin, run=subprocess.run, sysfs="/sys"):
    """La partition de donnees de Ventoy sous la projection de l'ISO.

    Ventoy projette le fichier ISO par une table device-mapper « linear » sur
    le disque (ou sa 1re partition). On exige : une seule cible, des segments
    tous lineaires, tous DANS la partition 1, et une partition VTOYEFI sur le
    meme disque. Rend {"majmin", "debut", "taille", "disque"} : la partition
    de donnees, en secteurs de 512 octets, relative au peripherique majmin."""
    maj, mineur = dm_majmin.split(":")
    table = lancer(["dmsetup", "table", "-j", maj, "-m", mineur], run=run)
    segments = []
    for ligne in table.strip().splitlines():
        champs = ligne.split()
        if len(champs) != 5 or champs[2] != "linear" or not re.fullmatch(r"\d+:\d+", champs[3]):
            raise Echec("Cette clé USB ne peut pas garder de profil.", f"table inattendue: {ligne!r}")
        segments.append((int(champs[1]), champs[3], int(champs[4])))
    cibles = {c for _l, c, _o in segments}
    if len(cibles) != 1:
        raise Echec("Cette clé USB ne peut pas garder de profil.", f"cibles: {sorted(cibles)}")
    (majmin,) = cibles
    base = os.path.realpath(os.path.join(sysfs, "dev", "block", majmin))
    nom = os.path.basename(base)
    if os.path.exists(os.path.join(base, "partition")):
        # La table vise deja la partition de donnees.
        if _lire_sysfs(os.path.join(base, "partition")) != "1":
            raise Echec("Cette clé USB ne peut pas garder de profil.", f"{nom} n'est pas la partition 1")
        debut, taille = 0, int(_lire_sysfs(os.path.join(base, "size")))
        disque = os.path.basename(os.path.dirname(base))
    else:
        disque = nom
        parts = [e for e in sorted(os.listdir(base))
                 if os.path.exists(os.path.join(base, e, "partition"))
                 and _lire_sysfs(os.path.join(base, e, "partition")) == "1"]
        if len(parts) != 1:
            raise Echec("Cette clé USB ne peut pas garder de profil.", f"pas de partition 1 sur {nom}")
        debut = int(_lire_sysfs(os.path.join(base, parts[0], "start")))
        taille = int(_lire_sysfs(os.path.join(base, parts[0], "size")))
    for longueur, _c, decalage in segments:
        if decalage < debut or decalage + longueur > debut + taille:
            raise Echec("Cette clé USB ne peut pas garder de profil.",
                        f"segment hors de la partition de donnees: {decalage}+{longueur}")
    etiquettes = json.loads(lancer(["lsblk", "-J", "-p", "-o", "NAME,LABEL", f"/dev/{disque}"],
                                   run=run))
    noms = [e.get("label") for d in etiquettes.get("blockdevices") or []
            for e in d.get("children") or []]
    if "VTOYEFI" not in noms:
        raise Echec("Cette clé USB ne peut pas garder de profil.", f"pas de VTOYEFI sur {disque}")
    return {"majmin": majmin, "debut": debut, "taille": taille, "disque": f"/dev/{disque}"}


def partition_profils(disque, run=subprocess.run):
    infos = json.loads(lancer(["lsblk", "-J", "-p", "-o", "NAME,TYPE,PARTLABEL,FSTYPE", disque],
                              run=run))
    for d in infos.get("blockdevices") or []:
        for enfant in d.get("children") or []:
            if enfant.get("type") == "part" and enfant.get("partlabel") == PARTLABEL:
                return enfant["name"], enfant.get("fstype")
    return None, None


def _dernier_entier(texte):
    for ligne in reversed(texte.strip().splitlines()):
        if ligne.strip().isdigit():
            return int(ligne.strip())
    raise Echec("La partition de profils n'a pas pu être créée.", f"sortie sgdisk illisible: {texte!r}")


def creer_partition(disque, run=subprocess.run, attendre=time.sleep):
    """Ajoute la partition de donnees dans l'espace libre de la cle.

    L'ISO porte une table GPT (xorriso -appended_part_as_gpt) dont la copie de
    secours est a la fin de l'IMAGE, pas du disque : `sgdisk -e` la deplace a
    la fin de la cle avant d'ajouter quoi que ce soit. Le systeme de fichiers
    de l'ISO n'est pas touche (seule la zone systeme, ses 32 premiers Kio, et la
    fin du disque changent)."""
    _sgdisk(["sgdisk", "-e", disque], run)
    debut = _dernier_entier(lancer(["sgdisk", "-F", disque], run=run))
    fin = _dernier_entier(lancer(["sgdisk", "-E", disque], run=run))
    if (fin - debut) * 512 < 2 * PROFIL_MINIMUM:
        raise Echec("Il n'y a pas assez de place libre sur cette clé USB pour un profil.",
                    f"espace libre {(fin - debut) * 512 // MIO} Mio")
    _sgdisk(["sgdisk", "-n", "0:0:0", "-t", "0:8300", "-c", f"0:{PARTLABEL}", disque], run)
    # Le disque est en service (la racine en vient) : le noyau refuse de relire
    # toute la table, mais partx ajoute la seule partition neuve.
    run(["partx", "-a", disque], capture_output=True, timeout=30, env={"PATH": CHEMIN})
    run(["udevadm", "settle", "-t", "10"], capture_output=True, timeout=30, env={"PATH": CHEMIN})
    for _n in range(10):
        part, _fs = partition_profils(disque, run=run)
        if part:
            break
        attendre(1)
    else:
        raise Echec("La partition de profils n'a pas pu être créée.", "partition invisible apres sgdisk")
    lancer(["mkfs.ext4", "-q", "-F", "-L", PARTLABEL, "-m", "0", part], run=run, delai=300)
    journal(f"partition de profils creee: {part}")
    return part


def monter_profils(cfg, run=subprocess.run, attendre=time.sleep, monte=os.path.ismount,
                   support=None):
    """Monte (et cree au besoin) la partition de donnees ; rend son point de montage."""
    point = dossiers(cfg)["profils"]
    if monte(point):
        return point
    support = support or support_de_la_cle(run=run)
    if support["genre"] == "ventoy":
        return monter_profils_ventoy(cfg, support, run=run, monte=monte)
    disque = support["disque"]
    part, fs = partition_profils(disque, run=run)
    if part is None:
        part = creer_partition(disque, run=run, attendre=attendre)
    elif fs != "ext4":
        raise Echec("La partition de profils de cette clé USB est illisible.",
                    f"{part} en {fs!r}, ext4 attendu")
    ph.dossier_sur(point, 0o700)
    run(["e2fsck", "-p", part], capture_output=True, timeout=300, env={"PATH": CHEMIN})
    lancer(["mount", "-t", "ext4", "-o", "nosuid,nodev,noexec", part, point], run=run)
    return point


def projection_ventoy(support, run=subprocess.run, existe=os.path.exists):
    """(peripherique, type) de la projection device-mapper de la partition de
    donnees Ventoy, creee au besoin."""
    dev = f"/dev/mapper/{VENTOY_DM}"
    if not existe(dev):
        lancer(["dmsetup", "create", VENTOY_DM, "--table",
                f"0 {support['taille']} linear {support['majmin']} {support['debut']}"], run=run)
        run(["udevadm", "settle", "-t", "10"], capture_output=True, timeout=30, env={"PATH": CHEMIN})
    fs = lancer(["blkid", "-p", "-o", "value", "-s", "TYPE", dev], run=run).strip()
    if fs not in FS_VENTOY:
        raise Echec("La partition Ventoy de cette clé USB n'est pas dans un format pris en charge.",
                    f"{dev} en {fs!r}")
    return dev, fs


def monter_profils_ventoy(cfg, support, run=subprocess.run, monte=os.path.ismount,
                          existe=os.path.exists):
    """Cle Ventoy : la partition de donnees (exFAT le plus souvent) est deja
    tenue par la projection de l'ISO, donc un `mount /dev/sdX1` est refuse
    (occupe). Une seconde projection device-mapper de la partition entiere
    partage la meme reservation et se monte, comme l'option de remontage de
    Ventoy. On n'y ecrit que le dossier bfos-profils : les ISO ne bougent pas."""
    d = dossiers(cfg)
    dev, fs = projection_ventoy(support, run=run, existe=existe)
    pilote, options = FS_VENTOY[fs]
    ph.dossier_sur(d["ventoy"], 0o700)
    if not monte(d["ventoy"]):
        opts = "nosuid,nodev,noexec" + ("," + options if options else "")
        lancer(["mount", "-t", pilote, "-o", opts, dev, d["ventoy"]], run=run)
    rep = os.path.join(d["ventoy"], VENTOY_DOSSIER)
    if os.path.islink(rep) or (os.path.lexists(rep) and not os.path.isdir(rep)):
        raise Echec("Le dossier des profils sur cette clé USB a été modifié hors de Blue Fox OS.",
                    f"{rep} n'est pas un dossier")
    os.makedirs(rep, mode=0o700, exist_ok=True)
    point = d["profils"]
    ph.dossier_sur(point, 0o700)
    lancer(["mount", "--bind", "-o", "nosuid,nodev,noexec", rep, point], run=run)
    journal(f"profils sur la partition Ventoy ({fs}) de {support['disque']}")
    return point


def retenir_organisation(cfg, point):
    """Cle generique : le domaine de l'organisation (lui seul), retenu dans
    l'espace des profils pour le prochain demarrage. Jamais bloquant."""
    domaine = organisation.normaliser_domaine(cfg.get("domaine")) if cfg.get("organisation") else None
    if not domaine or not point:
        return False
    chemin = os.path.join(point, organisation.FICHIER_RETENU)
    try:
        if (os.path.isfile(chemin) and not os.path.islink(chemin)
                and organisation.domaine_retenu(chemin) == domaine):
            return True
        if os.path.islink(chemin) or (os.path.lexists(chemin) and not os.path.isfile(chemin)):
            raise OSError(f"{chemin} n'est pas un fichier ordinaire")
        tmp = chemin + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(domaine + "\n")
        os.replace(tmp, chemin)
        return True
    except OSError as exc:
        journal(f"organisation non retenue sur la cle : {exc}", ph.syslog.LOG_NOTICE)
        return False


def lire_organisation_retenue(cfg, run=subprocess.run, sortie=organisation.RETENU,
                              support=None):
    """Au demarrage (unite bfos-live-organisation, avant l'ecran) : relit le
    domaine retenu dans l'espace des profils, en LECTURE SEULE et sans rien
    creer, et le depose dans `sortie` pour l'aide PAM. Rend le domaine ou None."""
    try:
        support = support or support_de_la_cle(run=run)
        if support["genre"] == "ventoy":
            dev, fs = projection_ventoy(support, run=run)
            pilote, options = FS_VENTOY[fs]
            sans_rejeu = {"ext4": "noload", "xfs": "norecovery"}.get(fs)
            options = ",".join(o for o in (options, sans_rejeu) if o)
            sous = VENTOY_DOSSIER
        else:
            dev, fs = partition_profils(support["disque"], run=run)
            if dev is None or fs != "ext4":
                return None
            # « ro » seul rejoue le journal d'une partition arrachee : il ECRIT.
            pilote, options, sous = "ext4", "noload", ""
    except Echec as exc:
        journal(f"organisation retenue : {exc.raison}", ph.syslog.LOG_INFO)
        return None
    ph.dossier_sur(os.path.dirname(sortie), 0o700)  # dossier a lui : /run/bfos-live reste tel quel
    point = tempfile.mkdtemp(prefix="bfos-organisation-", dir=os.path.dirname(sortie))
    domaine = None
    try:
        opts = "ro,nosuid,nodev,noexec" + ("," + options if options else "")
        lancer(["mount", "-t", pilote, "-o", opts, dev, point], run=run)
        try:
            chemin = os.path.join(point, sous, organisation.FICHIER_RETENU)
            fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as f:
                domaine = organisation.normaliser_domaine(f.read(300).decode("ascii", "replace"))
        except OSError:
            domaine = None
        finally:
            run(["umount", point], capture_output=True, timeout=60, env={"PATH": CHEMIN})
    except Echec as exc:
        journal(f"organisation retenue : {exc.raison}", ph.syslog.LOG_NOTICE)
    finally:
        try:
            os.rmdir(point)
        except OSError:
            pass
    if domaine:
        ph._ecrire_texte(sortie, domaine + "\n", mode=0o600)
    return domaine


def reprendre_profil_d_avant(fichier, ident, cle, run=subprocess.run):
    """Cle generique : l'identite est « emetteur\\0sub », et le profil d'avant
    (nom tire du `sub` seul, cle configuree) n'est repris que s'il s'ouvre avec
    la cle sequestree de CETTE organisation : un autre emetteur qui annoncerait
    le meme `sub` n'a pas cette cle. Rend True si le fichier a ete renomme."""
    if "\0" not in ident or os.path.lexists(fichier):
        return False
    ancien = os.path.join(os.path.dirname(fichier), nom_fichier_profil(ident.split("\0", 1)[1]))
    if os.path.islink(ancien) or not os.path.isfile(ancien):
        return False
    try:
        verifier_entete(ancien, run=run)
        lancer(["cryptsetup", "open", "--test-passphrase", "--type", "luks2", "--key-file", "-",
                ancien], run=run, entree=cle)
    except Echec as exc:
        journal(f"profil d'avant non repris : {exc.raison}", ph.syslog.LOG_NOTICE)
        return False
    os.rename(ancien, fichier)
    journal(f"profil d'avant repris sous {os.path.basename(fichier)}")
    return True


def nom_fichier_profil(sub):
    # L'empreinte seule : le nom ne dit pas a qui appartient le profil.
    return f"profil-{ph._empreinte(sub)[:32]}.luks"


def taille_profil(cfg, point, statvfs=os.statvfs):
    voulue = int(cfg.get("profile_size_mb") or 8192) * MIO
    st = statvfs(point)
    libre = st.f_bavail * st.f_frsize - MARGE_LIBRE
    taille = min(voulue, libre)
    if taille < PROFIL_MINIMUM:
        raise Echec("Il n'y a plus assez de place sur cette clé USB pour un nouveau profil.",
                    f"libre {libre // MIO} Mio")
    return taille - taille % MIO


def mapper(nom):
    return f"bfos-profil-{nom}"


def verifier_entete(fichier, run=subprocess.run):
    """Refuse un conteneur dont l'en-tete n'est pas celui que cette cle cree.

    La partition de profils n'a aucune integrite : quiconque tient la cle peut
    remplacer un fichier. Un en-tete au chiffrement nul ou a plusieurs
    emplacements de cle pourrait s'ouvrir sans la vraie cle et faire monter a
    la personne un dossier personnel choisi par quelqu'un d'autre. On lit donc
    l'en-tete AVANT de donner la cle a cryptsetup."""
    brut = lancer(["cryptsetup", "luksDump", "--dump-json-metadata", fichier], run=run)
    try:
        meta = json.loads(brut)
        emplacements = meta["keyslots"]
        segments = meta["segments"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Echec("Le profil sur cette clé USB a été modifié hors de Blue Fox OS : il n'est pas ouvert.",
                    f"en-tete illisible: {exc}") from exc
    ok = (isinstance(emplacements, dict) and len(emplacements) == 1
          and isinstance(segments, dict) and len(segments) == 1)
    if ok:
        (slot,) = emplacements.values()
        (seg,) = segments.values()
        ok = (slot.get("type") == "luks2"
              and (slot.get("area") or {}).get("encryption") == CHIFFREMENT
              and seg.get("type") == "crypt" and seg.get("encryption") == CHIFFREMENT)
    if not ok:
        raise Echec("Le profil sur cette clé USB a été modifié hors de Blue Fox OS : il n'est pas ouvert.",
                    f"en-tete inattendu: {brut[:300]}")


def maison_reelle(cfg, compte):
    """Le dossier personnel, chemin reel (sur Fedora atomic, /home -> /var/home)."""
    base = os.path.realpath(cfg.get("home_base") or "/home")
    maison = os.path.realpath(compte.pw_dir)
    if (not maison.startswith(base.rstrip("/") + "/") or os.path.dirname(maison) != base.rstrip("/")
            or not os.path.isdir(maison)):
        raise Echec("Erreur interne.", f"dossier personnel inattendu: {compte.pw_dir!r} -> {maison!r}")
    return maison


def ouvrir_profil(cfg, nom, sub, compte, cle, run=subprocess.run, attendre=time.sleep,
                  statvfs=os.statvfs, squelette="/etc/skel", monte=os.path.ismount,
                  existe=os.path.exists, etat=None):
    """Ouvre (ou cree) le fichier LUKS de la personne et le monte sur son
    dossier personnel. Rend True si le profil vient d'etre cree."""
    maison = maison_reelle(cfg, compte)
    point = monter_profils(cfg, run=run, attendre=attendre, monte=monte)
    fichier = os.path.join(point, nom_fichier_profil(sub))
    mappe = mapper(nom)
    dev = f"/dev/mapper/{mappe}"
    if existe(dev) and not monte(maison):
        # Reste d'une session mal fermee : on repart d'un conteneur ferme.
        run(["cryptsetup", "close", mappe], capture_output=True, timeout=60, env={"PATH": CHEMIN})
    _retirer_orphelin(fichier + ".part", mappe, run)
    reprendre_profil_d_avant(fichier, sub, cle, run=run)
    neuf = not os.path.lexists(fichier)
    if neuf:
        _creer_conteneur(cfg, point, fichier, mappe, dev, cle, run=run, statvfs=statvfs)
    else:
        if os.path.islink(fichier) or not os.path.isfile(fichier):
            raise Echec("Le profil sur cette clé USB est illisible.", f"{fichier} n'est pas un fichier")
        verifier_entete(fichier, run=run)
        try:
            lancer(["cryptsetup", "open", "--type", "luks2", "--key-file", "-",
                    fichier, mappe], run=run, entree=cle)
        except Echec as exc:
            raise Echec("Ton profil sur cette clé USB ne s'ouvre pas avec la clé de "
                        "déchiffrement servie par ton organisation (elle a peut-être été "
                        "remplacée).",
                        exc.raison) from exc
        controle = run(["e2fsck", "-p", dev], capture_output=True, timeout=300,
                       env={"PATH": CHEMIN}).returncode
        if controle >= 4:
            # Pas repare : on l'ouvre quand meme, mais on n'agrandit pas un
            # systeme de fichiers abime.
            journal(f"profil de {nom} : e2fsck rend {controle}, pas d'agrandissement",
                    ph.syslog.LOG_WARNING)
        if etat is not None:
            etat["profil_sain"] = controle < 4
    if not monte(maison):
        # noatime : sur une cle USB, chaque lecture ecrivait sa date d'acces
        # (mode « garder sur la cle » tres lent, essai du 2026-10-26).
        lancer(["mount", "-t", "ext4", "-o", "nosuid,nodev,noatime", dev, maison], run=run)
    if etat is not None:
        etat["profil_fichier"] = fichier
        etat.setdefault("profil_sain", True)
    if neuf:
        for entree in os.listdir(squelette):
            src = os.path.join(squelette, entree)
            dst = os.path.join(maison, entree)
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
        lost = os.path.join(maison, "lost+found")
        if os.path.isdir(lost):
            os.rmdir(lost)
    st = os.lstat(maison)
    if neuf or st.st_uid != compte.pw_uid:
        # L'uid d'une personne est stable d'un demarrage a l'autre (derive du
        # sub), sauf collision : on remet alors tout a son nom.
        lancer(["chown", "-R", "-h", f"{compte.pw_uid}:{compte.pw_gid}", maison], run=run, delai=600)
        os.chmod(maison, 0o700)
        lancer(["restorecon", "-R", "-F", maison], run=run, delai=600)
    journal(f"profil {'cree' if neuf else 'ouvert'} pour {nom}")
    return neuf


def boucle_du_profil(dev, sysfs="/sys", stat=os.stat):
    """La boucle EXACTE sous le mappage du profil (pas une recherche par nom de
    fichier : une boucle orpheline d'une ouverture interrompue repondrait aussi)."""
    st = stat(dev)
    esclaves = os.listdir(f"{sysfs}/dev/block/{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}/slaves")
    if len(esclaves) != 1 or not esclaves[0].startswith("loop"):
        raise Echec("Erreur interne.", f"boucle du profil introuvable: {esclaves!r}")
    return "/dev/" + esclaves[0]


def _reserver_pas(cfg, point, fichier, statvfs, exfat):
    """Sous VERROU_PLACE : compte la place et reserve le pas d'un bloc. Rend
    (taille avant, octets ajoutes) ; rien d'ajoute sous PAS_MIN."""
    actuelle = os.lstat(fichier).st_size
    lp = statvfs(point)
    # Une autre personne doit toujours pouvoir creer un profil complet.
    reserve_mb = cfg.get("profile_reserve_mb")
    if reserve_mb is None:
        reserve_mb = cfg.get("profile_size_mb") or 8192
    reserve = int(reserve_mb) * MIO
    # Plafond par personne : la moitie de la partition, sauf reglage.
    plafond = (int(cfg.get("profile_max_mb") or 0) * MIO
               or getattr(lp, "f_blocks", 0) * lp.f_frsize // 2)
    pas = min(actuelle, PAS_MAX_EXFAT if exfat else PAS_MAX,
              lp.f_bavail * lp.f_frsize - MARGE_LIBRE - reserve, plafond - actuelle)
    pas -= pas % MIO
    if pas < PAS_MIN:
        return actuelle, 0
    fd = os.open(fichier, os.O_WRONLY | os.O_NOFOLLOW)
    try:
        os.posix_fallocate(fd, actuelle, pas)
        os.fsync(fd)
    finally:
        os.close(fd)
    return actuelle, pas


def agrandir_profil(cfg, point, fichier, mappe, dev, maison, cle, run=subprocess.run,
                    statvfs=os.statvfs, exfat=False, boucle_de=boucle_du_profil):
    """Agrandit a chaud, d'un pas, un profil presque plein (#26870, 2026-10-25).

    Le profil nait a profile_size_mb (8 Gio) : sans ceci, il le restait sur
    une cle de 256 Go. Chaine : boucle reperee, fichier reserve plus loin,
    boucle relue (losetup -c), LUKS etendu, ext4 etendu en ligne. Tourne dans
    sa propre unite, APRES l'ouverture de session (sur exFAT, la reservation
    ecrit vraiment les octets : des minutes sur une cle lente). Jamais
    bloquant. Rend le nombre d'octets ajoutes."""
    try:
        st = statvfs(maison)
        total = getattr(st, "f_blocks", 0) * st.f_frsize
        # Occupation reelle : f_bfree compte aussi les 5 % reserves a root.
        if not total or (st.f_blocks - st.f_bfree) * st.f_frsize < total * SEUIL_AGRANDIR:
            return 0
        boucle = boucle_de(dev)
        run(["sync"], capture_output=True, timeout=600, env={"PATH": CHEMIN})
        with Verrou(cfg, fichier=VERROU_PLACE):
            actuelle, pas = _reserver_pas(cfg, point, fichier, statvfs, exfat)
        if pas < PAS_MIN:
            journal(f"profil de {os.path.basename(maison)} presque plein, plus de place "
                    f"pour l'agrandir", ph.syslog.LOG_WARNING)
            return 0
        lancer(["losetup", "--set-capacity", boucle], run=run)
        lancer(["cryptsetup", "resize", "--key-file", "-", mappe], run=run, entree=cle)
        lancer(["resize2fs", dev], run=run, delai=600)
        run(["sync"], capture_output=True, timeout=600, env={"PATH": CHEMIN})
    except (Echec, OSError, subprocess.SubprocessError) as exc:
        journal(f"profil non agrandi: {getattr(exc, 'raison', exc)}", ph.syslog.LOG_WARNING)
        return 0
    journal(f"profil de {os.path.basename(maison)} agrandi de {pas // MIO} Mio "
            f"({(actuelle + pas) // MIO} Mio)")
    return pas


def est_exfat(point, run=subprocess.run):
    """Type du systeme de fichiers qui porte les profils (Ventoy : exFAT le
    plus souvent, mais aussi ext4 ou xfs), par son nombre magique."""
    try:
        res = run(["stat", "-f", "-c", "%t", point], capture_output=True, text=True, timeout=30,
                  env={"PATH": CHEMIN})
    except (OSError, subprocess.SubprocessError):
        return True  # dans le doute, les petits pas
    return res.returncode != 0 or (res.stdout or "").strip().lower() == MAGIC_EXFAT


def agrandir(cfg, nom, run=subprocess.run, statvfs=os.statvfs, monte=os.path.ismount, **kw):
    """Commande de l'unite bfos-live-agrandir@ : termine une croissance
    interrompue, puis agrandit par pas tant que le profil reste presque plein."""
    chemin_etat = os.path.join(dossiers(cfg)["etat"], nom + ".json")
    with Verrou(cfg, nom):
        etat = lire_json_root(chemin_etat) or {}
        if (etat.get("mode") != "persistant" or not etat.get("profil_monte")
                or not etat.get("profil_sain") or not etat.get("cle_profil")):
            return 0
        maison, fichier = etat["maison"], etat["profil_fichier"]
        if not monte(maison):
            return 0
        mappe = mapper(nom)
        dev = f"/dev/mapper/{mappe}"
        try:
            run(["resize2fs", dev], capture_output=True, timeout=600, env={"PATH": CHEMIN})
        except subprocess.SubprocessError as exc:
            journal(f"resize2fs de reprise: {exc}", ph.syslog.LOG_WARNING)
        d = dossiers(cfg)
        exfat = est_exfat(d["profils"], run=run)
        cle = base64.b64decode(etat["cle_profil"])
        total = 0
        for _n in range(PAS_PAR_PASSE):
            pas = agrandir_profil(cfg, d["profils"], fichier, mappe, dev, maison, cle, run=run,
                                  statvfs=statvfs, exfat=exfat, **kw)
            if not pas:
                break
            total += pas
        return total


def _retirer_orphelin(partiel, mappe, run):
    """Une creation interrompue (delai, coupure de courant) laisse un `.part` :
    jamais un conteneur au nom final sans systeme de fichiers."""
    if os.path.lexists(partiel):
        run(["cryptsetup", "close", mappe], capture_output=True, timeout=60, env={"PATH": CHEMIN})
        os.unlink(partiel)
        journal(f"creation interrompue retiree: {partiel}", ph.syslog.LOG_WARNING)


def _creer_conteneur(cfg, point, fichier, mappe, dev, cle, run=subprocess.run,
                     statvfs=os.statvfs):
    """Fichier reserve d'un bloc (pas de fichier creux : deux profils ne
    peuvent pas se promettre la meme place), LUKS2, ouvert, ext4, puis
    seulement renomme a son nom final : un conteneur au nom final est toujours
    complet."""
    partiel = fichier + ".part"
    try:
        # La place se compte et se reserve d'un bloc, face a une croissance.
        with Verrou(cfg, fichier=VERROU_PLACE):
            taille = taille_profil(cfg, point, statvfs=statvfs)
            fd = os.open(partiel, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.posix_fallocate(fd, 0, taille)
            finally:
                os.close(fd)
        # Cle aleatoire de 256 bits : une derivation lente (argon2) ne
        # protegerait rien de plus et couterait 1 Gio de memoire vive.
        lancer(["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode",
                "--cipher", CHIFFREMENT, "--key-size", "512",
                "--pbkdf", "pbkdf2", "--pbkdf-force-iterations", "1000",
                "--label", "bfos-profil", "--key-file", "-", partiel],
               run=run, entree=cle, delai=120)
        lancer(["cryptsetup", "open", "--type", "luks2", "--key-file", "-", partiel, mappe],
               run=run, entree=cle)
        lancer(["mkfs.ext4", "-q", "-L", "profil", dev], run=run, delai=300)
        os.rename(partiel, fichier)
    except BaseException:
        _retirer_orphelin(partiel, mappe, run)
        raise


def fermer_profil(nom, maison, run=subprocess.run, attendre=time.sleep, essais=15,
                  monte=os.path.ismount, existe=os.path.exists):
    """Demonte le dossier personnel et referme le LUKS. On attend un peu les
    processus de la session, puis on detache quand meme : la fermeture se fait
    des que le dernier s'en va."""
    run(["sync"], capture_output=True, timeout=60, env={"PATH": CHEMIN})
    if maison and monte(maison):
        # -R : les caches en memoire vive (et un montage Nextcloud reste) d'abord.
        for _n in range(essais):
            if run(["umount", "-R", maison], capture_output=True, timeout=30,
                   env={"PATH": CHEMIN}).returncode == 0:
                break
            attendre(1)
        else:
            run(["umount", "-R", "-l", maison], capture_output=True, timeout=30,
                env={"PATH": CHEMIN})
            journal(f"{maison} detache (occupe)", ph.syslog.LOG_WARNING)
    if existe(f"/dev/mapper/{mapper(nom)}"):
        if run(["cryptsetup", "close", mapper(nom)], capture_output=True, timeout=60,
               env={"PATH": CHEMIN}).returncode != 0:
            run(["cryptsetup", "close", "--deferred", mapper(nom)], capture_output=True,
                timeout=60, env={"PATH": CHEMIN})


def demonter_profils_si_libre(cfg, run=subprocess.run, lister=os.listdir, monte=os.path.ismount,
                              existe=os.path.exists):
    """Plus aucun profil ouvert : la partition de donnees est demontee."""
    point = dossiers(cfg)["profils"]
    try:
        ouverts = [m for m in lister("/dev/mapper") if m.startswith("bfos-profil-")]
    except OSError:
        return
    if ouverts:
        return
    if monte(point):
        run(["umount", point], capture_output=True, timeout=30, env={"PATH": CHEMIN})
    ventoy = dossiers(cfg)["ventoy"]
    if monte(ventoy):
        run(["umount", ventoy], capture_output=True, timeout=60, env={"PATH": CHEMIN})
    if existe(f"/dev/mapper/{VENTOY_DM}"):
        run(["dmsetup", "remove", VENTOY_DM], capture_output=True, timeout=30, env={"PATH": CHEMIN})


# ------------------------------------------------------------- bureau itinerant
def _echec_de(exc):
    return Echec(exc.message, exc.raison)


def identifiants_root(cfg, jeton, politique, bp, tmp):
    """Mot de passe d'application Nextcloud pour ROOT seulement (telechargement,
    envoi, verrou du bureau itinerant), revoque a la fermeture."""
    if not politique:
        raise Echec("Nextcloud n'est pas configuré pour ton organisation : bureau Nextcloud "
                    "indisponible.", "pas de politique")
    ph.dossier_sur(tmp, 0o700)
    chemin = os.path.join(tmp, "nc-root.json")
    env = {"BFOS_OIDC_TOKEN_URL": cfg["token_url"], "BFOS_OIDC_CLIENT_ID": cfg["client_id"]}
    try:
        depot = bp.obtenir_identifiants_nextcloud(jeton, politique, env=env, path=chemin,
                                                  out=lambda m: journal(m.strip()))
    finally:
        try:
            os.unlink(chemin)
        except FileNotFoundError:
            pass
    if not depot:
        raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                    "mot de passe d'application Nextcloud refuse")
    return depot


def _tmp_itinerant(cfg):
    # /tmp (50 % de la RAM) et pas /run : /run (20 %) porte deja toute la
    # racine en ecriture de la cle live, maison comprise.
    return cfg.get("roaming_tmp") or "/tmp"


def _autre_que(depot):
    return lambda liste: [d for d in liste if d["app_password"] != depot["app_password"]]


def noter_mot_de_passe_root(nuage, cle, depot, http=requete):
    """Note le mot de passe de root de cette session dans le registre AVANT
    qu'il serve, et revoque ceux qu'une fermeture sans reseau a laisses (avec
    leurs propres identifiants : aucun droit de plus)."""
    _e, anciens = it.lire_jetons(nuage, cle)
    morts = {d["app_password"] for d in _autre_que(depot)(anciens) if revoquer_nextcloud(d, http=http)}
    it.modifier_jetons(nuage, cle, lambda liste: [d for d in _autre_que(depot)(liste)
                                                  if d["app_password"] not in morts] + [depot])


def oublier_mot_de_passe_root(nuage, cle, depot):
    """Retire ce mot de passe du registre ; False si le registre est
    injoignable (il y reste alors, et l'ouverture suivante le revoque)."""
    try:
        it.modifier_jetons(nuage, cle, _autre_que(depot))
        return True
    except Exception as exc:  # noqa: BLE001
        journal(f"registre des mots de passe injoignable: {exc!r}", ph.syslog.LOG_WARNING)
        return False


def rendre_mot_de_passe_root(nuage, cle, depot, http=requete):
    """Fermeture : retire du registre PUIS revoque (revoque, il ne pourrait
    plus ecrire ; dans l'autre ordre, chaque ouverture suivante essaierait un
    mot de passe mort, un echec de connexion compte par la protection
    anti-force brute de Nextcloud). Revocation ratee : il y est remis."""
    oublie = oublier_mot_de_passe_root(nuage, cle, depot)
    if revoquer_nextcloud(depot, http=http):
        return True
    if oublie:
        try:
            it.modifier_jetons(nuage, cle, lambda liste: _autre_que(depot)(liste) + [depot])
        except Exception as exc:  # noqa: BLE001
            journal(f"mot de passe de root ni revoque ni note: {exc!r}", ph.syslog.LOG_ERR)
    return False


def ouvrir_itinerant(cfg, nom, compte, cle, depot, run=subprocess.run, nuage_de=it.Nuage,
                     popen=subprocess.Popen, statvfs=os.statvfs, http=requete):
    """Verrou, registre des mots de passe, puis restauration. Rend (info
    d'etat, restaure). Echec apres le verrou : ce mot de passe sort du registre
    (la fermeture le revoque) et le verrou est rendu."""
    try:
        nuage = nuage_de(depot)
        session_id = uuid.uuid4().hex
        etag = it.prendre_verrou(nuage, session_id)
    except it.Echec as exc:
        raise _echec_de(exc) from exc
    try:
        noter_mot_de_passe_root(nuage, cle, depot, http=http)
        # Chemin verifie avant que root n'y vide quoi que ce soit.
        maison = maison_reelle(cfg, compte)
        taille = it.restaurer(nuage, compte, maison, cle, _tmp_itinerant(cfg),
                              popen=popen, run=run, statvfs=statvfs)
    except BaseException as exc:
        oublier_mot_de_passe_root(nuage, cle, depot)
        try:
            nuage.retirer_verrou(etag)
        except Exception:  # noqa: BLE001
            pass
        if isinstance(exc, it.Echec):
            raise _echec_de(exc) from exc
        raise
    if taille:
        run(["restorecon", "-R", compte.pw_dir], capture_output=True, timeout=300,
            env={"PATH": CHEMIN})
    return {"depot": depot, "session": session_id, "verrou": etag,
            "cle": base64.b64encode(cle).decode(), "pret": True,
            "taille_ouverte": taille, "neuf": not taille, "precedent_fait": False}, bool(taille)


def notifier(nom, uid, texte, run=subprocess.run, titre=None):
    """Bulle sur le bureau de la personne ; jamais bloquant."""
    try:
        run(["runuser", "-u", nom, "--", "env", f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
             "notify-send", "-a", "Blue Fox OS", titre or _("Bureau Nextcloud"), texte],
            capture_output=True, timeout=15, env={"PATH": CHEMIN})
    except Exception:  # noqa: BLE001
        pass


def sauvegarder_itinerant(cfg, nom, etat, chemin_etat, nuage_de=it.Nuage, popen=subprocess.Popen,
                          getpwnam=pwd.getpwnam, delai_max=None):
    """Renouvelle le verrou PUIS envoie le bureau. Jamais si la restauration
    n'a pas abouti (on ecraserait le bon bureau par une maison vide)."""
    info = etat.get("itinerant") or {}
    if not info.get("pret"):
        raise Echec("Ton bureau n'est pas sauvegardé : il n'a pas été ouvert correctement.",
                    "sauvegarde refusee : bureau non restaure")
    try:
        nuage = nuage_de(info["depot"])
        if delai_max:
            nuage.delai_max = delai_max
        info["verrou"] = it.renouveler_verrou(nuage, info["session"], info["verrou"])
        ph._ecrire(chemin_etat, etat, mode=0o600)
        taille = it.sauvegarder(nuage, getpwnam(nom), etat["maison"], base64.b64decode(info["cle"]),
                                _tmp_itinerant(cfg), info["session"],
                                taille_ouverte=int(info.get("taille_ouverte") or 0),
                                neuf=bool(info.get("neuf")),
                                copier_precedent=not info.get("precedent_fait"), popen=popen)
    except it.Echec as exc:
        raise _echec_de(exc) from exc
    # Le bureau existe maintenant dans le nuage : les suivantes ne sont plus « neuves ».
    info.update(precedent_fait=True, neuf=False,
                taille_ouverte=max(int(info.get("taille_ouverte") or 0), taille))
    ph._ecrire(chemin_etat, etat, mode=0o600)
    journal(f"bureau itinerant de {nom} envoye ({taille} octets)")
    return taille


def sauvegarder(cfg, nom, run=subprocess.run, **kw):
    """Commande de la minuterie : une sauvegarde, sous le verrou de la personne."""
    chemin_etat = os.path.join(dossiers(cfg)["etat"], nom + ".json")
    with Verrou(cfg, nom):
        etat = lire_json_root(chemin_etat) or {}
        if etat.get("mode") != "itinerant":
            return False
        langue.choisir(etat.get("langue"))
        try:
            sauvegarder_itinerant(cfg, nom, etat, chemin_etat, **kw)
            return True
        except Echec as exc:
            journal(f"sauvegarde itinerante de {nom}: {exc.raison}", ph.syslog.LOG_WARNING)
            notifier(nom, etat.get("uid"), _(exc.message), run=run)
            return False
        except Exception as exc:  # noqa: BLE001 - la personne est prevenue quand meme
            journal(f"sauvegarde itinerante de {nom}: {exc!r}", ph.syslog.LOG_ERR)
            notifier(nom, etat.get("uid"), _("Ton bureau n'a pas pu être envoyé dans Nextcloud."), run=run)
            return False


def attendre_fin_des_processus(uid, run=subprocess.run, attendre=time.sleep, essais=15):
    """La derniere archive se prend quand plus RIEN ne tourne sous l'uid
    (Brave ecrit encore ses bases un instant apres terminate-user)."""
    for _n in range(essais):
        res = run(["pgrep", "-u", str(uid)], capture_output=True, text=True, timeout=10,
                  env={"PATH": CHEMIN})
        if res.returncode != 0 or not (res.stdout or "").strip():
            return True
        attendre(1)
    return False


def fermer_itinerant(cfg, nom, etat, chemin_etat, http=requete, run=subprocess.run,
                     nuage_de=it.Nuage, popen=subprocess.Popen, attendre=time.sleep,
                     getpwnam=pwd.getpwnam):
    """Derniere sauvegarde, puis TOUJOURS verrou rendu et mot de passe de root
    revoque ; la maison n'est videe qu'apres un envoi reussi."""
    info = etat.get("itinerant") or {}
    env = {"PATH": CHEMIN}
    # La minuterie ET une sauvegarde en cours : rien n'est touche avant le
    # MOVE final, on peut l'interrompre sans risque.
    for unite in (UNITE_SAUVEGARDE.format(nom=nom),
                  UNITE_SAUVEGARDE.format(nom=nom).replace(".timer", ".service")):
        run(["systemctl", "stop", unite], capture_output=True, timeout=60, env=env)
    envoye = False
    try:
        with Verrou(cfg, nom):
            if info.get("pret"):
                if etat.get("uid") is not None:
                    attendre_fin_des_processus(etat["uid"], run=run, attendre=attendre)
                try:
                    # 10 min d'arret au plus (TimeoutStopSec) : garder de quoi rendre.
                    sauvegarder_itinerant(cfg, nom, etat, chemin_etat, nuage_de=nuage_de,
                                          popen=popen, getpwnam=getpwnam, delai_max=420)
                    envoye = True
                except Exception as exc:  # noqa: BLE001
                    journal(f"derniere sauvegarde de {nom}: {getattr(exc, 'raison', exc)!r}",
                            ph.syslog.LOG_ERR)
    finally:
        if info.get("depot") and info.get("verrou"):
            try:
                n = nuage_de(info["depot"])
                n.delai_max = 15
                n.retirer_verrou(info["verrou"])
            except Exception as exc:  # noqa: BLE001
                journal(f"verrou de {nom} non rendu: {exc!r}", ph.syslog.LOG_WARNING)
        if info.get("depot"):
            try:
                if info.get("pret") and info.get("cle"):
                    # Le registre s'ecrit par PUT conditionnel : verrou rendu
                    # ou pas, aucune mise a jour d'une autre session n'est perdue.
                    n = nuage_de(info["depot"])
                    n.delai_max = 15
                    rendre_mot_de_passe_root(n, base64.b64decode(info["cle"]), info["depot"],
                                             http=http)
                else:
                    revoquer_nextcloud(info["depot"], http=http)
            except Exception as exc:  # noqa: BLE001
                journal(f"revocation Nextcloud de {nom}: {exc!r}", ph.syslog.LOG_WARNING)
    if etat.get("maison") and info.get("pret"):
        try:
            if envoye:
                # Pas de bureau dechiffre qui traine en RAM sur un PC emprunte.
                it.vider_maison(etat["maison"], run=run)
            else:
                # Rien d'envoye : ce travail ne vit plus que dans cette maison.
                # Une reouverture le mettra de cote au lieu de l'effacer.
                it.marquer_non_envoye(etat["maison"], getpwnam(nom))
        except Exception as exc:  # noqa: BLE001
            journal(f"maison de {nom} apres fermeture: {exc!r}", ph.syslog.LOG_WARNING)
    return envoye

def finir_processus(nom, run=subprocess.run, attendre=time.sleep):
    """Aucun processus d'une personne ne survit a sa session (linger compris) :
    il lirait les fichiers de la suivante et garderait le profil occupe."""
    env = {"PATH": CHEMIN}
    run(["loginctl", "disable-linger", nom], capture_output=True, timeout=30, env=env)
    run(["loginctl", "terminate-user", nom], capture_output=True, timeout=30, env=env)
    attendre(1)


# ------------------------------------------------------------- les commandes
def charger_modules():
    import bfos_provision as bp  # noqa: PLC0415
    import bfos_apply as apply_mod  # noqa: PLC0415
    return bp, apply_mod


def verifier_compte(cfg, nom, sub, getpwnam=pwd.getpwnam):
    if not ph.NOM_VALIDE.match(nom or ""):
        raise Echec("Erreur interne.", f"nom invalide: {nom!r}")
    annuaire = ph.Annuaire(cfg, getpwnam=getpwnam)
    compte = annuaire.compte_de(nom, sub)
    if compte is None:
        raise Echec("Erreur interne.", f"{nom} n'est pas un compte de cette cle pour ce sub")
    return compte


def purger_depots():
    """Rien de la personne precedente ne doit servir a la suivante : politique,
    identifiants, listes Flatpak propres a quelqu'un."""
    fichiers = [IDENTIFIANTS_NC, IDENTIFIANTS_NC + ".root", POLITIQUE_PUBLIQUE,
                os.path.join(FLATPAK_DIR, "install"), os.path.join(FLATPAK_DIR, "remove")]
    for f in fichiers:
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass


class Verrou:
    """Une seule preparation a la fois : deux unites ne creent pas la
    partition deux fois et ne la montent pas en double. Avec `nom` : le verrou
    du bureau itinerant de cette personne (sauvegarde contre fermeture)."""

    def __init__(self, cfg, nom=None, fichier=None):
        fichier = fichier or (f"itinerant-{nom}.lock" if nom else "session.lock")
        self.chemin = os.path.join(cfg["state_dir"], fichier)
        self.fd = None

    def __enter__(self):
        ph.dossier_sur(self.chemin.rsplit("/", 1)[0], 0o755)
        self.fd = os.open(self.chemin, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def demarrer(cfg, nom, http=requete, run=subprocess.run, modules=None,
             getpwnam=pwd.getpwnam, attendre=time.sleep, statvfs=os.statvfs,
             nuage_de=it.Nuage, popen=None, **kw):
    d = dossiers(cfg)
    chemin = os.path.join(d["demandes"], nom + ".json")
    demande = lire_json_root(chemin)
    if demande is None:
        raise Echec("Erreur interne.", f"aucune demande pour {nom}")
    os.unlink(chemin)
    # Les etapes et un echec s'affichent sur l'ecran de connexion : dans SA langue.
    langue.choisir(demande.get("langue"))
    mode = demande.get("mode")
    if mode not in MODES:
        raise Echec("Erreur interne.", f"mode inconnu {mode!r}")
    sub, jeton = demande.get("sub"), demande.get("access_token")
    compte = verifier_compte(cfg, nom, sub, getpwnam=getpwnam)
    bp, apply_mod = modules or charger_modules()

    with Verrou(cfg):
        purger_depots()
        etat = {"mode": mode, "maison": compte.pw_dir, "uid": compte.pw_uid}
        ph.dossier_sur(d["etat"], 0o700)
        chemin_etat = os.path.join(d["etat"], nom + ".json")
        ph._ecrire(chemin_etat, etat, mode=0o600)

        annoncer(cfg, nom, 1, _("Lecture des réglages de ton organisation…"))
        politique = tirer_politique(cfg, jeton, http=http)
        # Pour les bulles de la session (sauvegardes) : la langue de la personne, sinon l'ecran.
        etat["langue"] = (langue.normaliser(((politique or {}).get("install") or {}).get("locale"))
                          or langue.courante())

        annoncer(cfg, nom, 2, {"persistant": _("Ouverture de ton profil chiffré…"),
                               "itinerant": _("Récupération de ton bureau dans Nextcloud…")}
                 .get(mode, _("Préparation de ton bureau…")))
        neuf = False
        if mode == "persistant":
            # La cle d'abord : le jeton du QR est encore frais.
            cle = cle_du_profil(cfg, jeton, http=http)
            neuf = ouvrir_profil(cfg, nom, sub, compte, cle, run=run, attendre=attendre,
                                 statvfs=statvfs, etat=etat, **kw)
            caches_en_memoire(maison_reelle(cfg, compte), compte, run=run)
            # Pour l'unite d'agrandissement (cryptsetup resize redemande la
            # cle) ; etat 0600 root en memoire vive, efface a la fermeture,
            # comme la cle du bureau itinerant.
            etat["cle_profil"] = base64.b64encode(cle).decode()
            etat["maison"] = maison_reelle(cfg, compte)
            etat["profil_monte"] = True
            etat["profil_neuf"] = neuf
            ph._ecrire(chemin_etat, etat, mode=0o600)
            # Seul ce mode ecrit sur la cle : « Rien n'est garde sur la cle » vaut
            # aussi pour le domaine en ephemere et en itinerant.
            retenir_organisation(cfg, d["profils"])
            # La cle a maintenant un espace : la zone Flatpak s'y prepare tout de suite,
            # en arriere-plan (sinon : a la minuterie suivante, jusqu'a 6 h plus tard).
            try:
                run(["systemctl", "start", "--no-block", UNITE_ZONE], capture_output=True,
                    timeout=30, env={"PATH": CHEMIN})
            except Exception as exc:  # noqa: BLE001 - la minuterie le refera
                journal(f"zone flatpak : preparation non lancee: {exc!r}", ph.syslog.LOG_WARNING)

        if mode == "itinerant":
            cle = cle_du_profil(cfg, jeton, http=http)
            depot_root = identifiants_root(cfg, jeton, politique, bp, d["itinerant"])
            # Ecrit AVANT d'ouvrir : un echec plus loin revoque ce mot de passe.
            etat["itinerant"] = {"depot": depot_root, "pret": False}
            ph._ecrire(chemin_etat, etat, mode=0o600)
            info, restaure = ouvrir_itinerant(cfg, nom, compte, cle, depot_root, run=run,
                                              nuage_de=nuage_de, statvfs=statvfs, http=http,
                                              **({"popen": popen} if popen else {}))
            neuf = not restaure
            etat["itinerant"] = info
            etat["profil_neuf"] = neuf
            ph._ecrire(chemin_etat, etat, mode=0o600)
            run(["systemctl", "start", UNITE_SAUVEGARDE.format(nom=nom)], capture_output=True,
                timeout=60, env={"PATH": CHEMIN})

        if politique:
            try:
                appliquer_preferences(politique, apply_mod, compte, etat["maison"], run=run)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant
                journal(f"fuseau et clavier non appliques: {exc!r}", ph.syslog.LOG_WARNING)

        annoncer(cfg, nom, 3, _("Connexion à Nextcloud…"))
        deja_configure = (mode in ("persistant", "itinerant") and not neuf
                          and os.path.exists(os.path.join(etat["maison"], DRAPEAU_ACCUEIL)))
        refait = False
        if (deja_configure and nextcloud_a_refaire(etat["maison"], politique)
                and not os.path.lexists(os.path.join(etat["maison"], DRAPEAU_NC_REFAIT))):
            # Accueil fait SANS Nextcloud (echange refuse par Authentik jusqu'au
            # 2026-10-25) : sans ceci, ce profil ne l'aurait plus jamais. Les
            # identifiants D'ABORD : sans eux, refaire l'accueil ne servirait a
            # rien (et le referait a chaque session).
            try:
                depot = identifiants_nextcloud(cfg, jeton, politique, compte, bp)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant
                journal(f"identifiants Nextcloud non obtenus: {exc!r}", ph.syslog.LOG_WARNING)
                depot = None
            # Une seule fois : si l'agent ne monte toujours rien, on ne lui fait
            # pas reecrire theme et reglages de la personne a chaque session.
            if depot and poser_sous(etat["maison"], DRAPEAU_NC_REFAIT) \
                    and retirer_sous(etat["maison"], DRAPEAU_ACCUEIL):
                # Revoque a la fermeture si l'agent n'en a pas fait un rclone.conf.
                etat["nextcloud_refait"] = depot
                ph._ecrire(chemin_etat, etat, mode=0o600)
                deja_configure, refait = False, True
                journal(f"accueil de {nom} a refaire : Nextcloud jamais configure")
            elif depot:
                revoquer_nextcloud(depot, http=http)
        if not deja_configure and not refait:
            try:
                depot = identifiants_nextcloud(cfg, jeton, politique, compte, bp)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant
                journal(f"identifiants Nextcloud non obtenus: {exc!r}", ph.syslog.LOG_WARNING)
                depot = None
            # Ephemere : le mot de passe meurt avec la session. Persistant : il
            # vit dans rclone.conf, dans le profil chiffre, comme sur un poste
            # installe, et se revoque depuis Nextcloud.
            if depot and mode == "ephemere":
                etat["nextcloud"] = depot
                ph._ecrire(chemin_etat, etat, mode=0o600)

        annoncer(cfg, nom, 4, _("Connexion à Odoo…"))
        lien = lien_odoo(cfg, jeton, http=http)
        if lien:
            try:
                deposer_lien_odoo(cfg, nom, compte, lien)
            except Exception as exc:  # noqa: BLE001 - jamais bloquant
                journal(f"lien Odoo non depose: {exc!r}", ph.syslog.LOG_WARNING)

        annoncer(cfg, nom, 5, _("Préparation de tes applications…"))
        if politique:
            try:
                ecrire_politique(politique, apply_mod)
                appliquer_applis(politique, apply_mod, run=run,
                                 generique=bool(cfg.get("organisation")))
            except Exception as exc:  # noqa: BLE001 - jamais bloquant
                journal(f"politique non appliquee: {exc!r}", ph.syslog.LOG_WARNING)

        if mode == "persistant" and not neuf and etat.get("profil_sain"):
            # Apres tout le reste : la session s'ouvre sans attendre.
            try:
                run(["systemctl", "start", "--no-block", UNITE_AGRANDIR.format(nom=nom)],
                    capture_output=True, timeout=60, env={"PATH": CHEMIN})
            except (OSError, subprocess.SubprocessError) as exc:
                journal(f"agrandissement non lance: {exc}", ph.syslog.LOG_WARNING)
    return etat


def arreter(cfg, nom, http=requete, run=subprocess.run, attendre=time.sleep,
            fermer=None, finir=None, demonter=None, nuage_de=None, popen=None, getpwnam=None):
    if not ph.NOM_VALIDE.match(nom or ""):
        return
    chemin_etat = os.path.join(dossiers(cfg)["etat"], nom + ".json")
    try:
        etat = lire_json_root(chemin_etat) or {}
    except Echec:
        etat = {}
    if etat.get("mode") == "persistant":
        # Avant de refermer le profil (deja fait par PartOf= a l'arret normal).
        try:
            run(["systemctl", "stop", UNITE_AGRANDIR.format(nom=nom)], capture_output=True,
                timeout=180, env={"PATH": CHEMIN})
        except (OSError, subprocess.SubprocessError) as exc:
            journal(f"agrandissement non arrete: {exc}", ph.syslog.LOG_WARNING)
        if etat.pop("cle_profil", None):
            # Plus personne n'en a besoin : hors de l'etat meme si la suite echoue.
            try:
                ph._ecrire(chemin_etat, etat, mode=0o600)
            except OSError:
                pass
    (finir or finir_processus)(nom, run=run, attendre=attendre)
    retirer_lien_odoo(cfg, nom)
    refait = etat.get("nextcloud_refait")
    if refait and not (etat.get("maison") and os.path.lexists(
            os.path.join(etat["maison"], RCLONE_PROFIL))):
        # Accueil refait sans que l'agent en fasse un montage : ce mot de
        # passe ne servirait a personne, il ne doit pas s'accumuler.
        revoquer_nextcloud(refait, http=http)
    if etat.get("mode") == "itinerant":
        # Apres finir_processus : Brave et les applis sont fermes, l'archive
        # est coherente.
        try:
            fermer_itinerant(cfg, nom, etat, chemin_etat, http=http, run=run, attendre=attendre,
                             **({"nuage_de": nuage_de} if nuage_de else {}),
                             **({"popen": popen} if popen else {}),
                             **({"getpwnam": getpwnam} if getpwnam else {}))
        except Exception as exc:  # noqa: BLE001 - le reste de l'arret doit passer
            journal(f"fermeture itinerante de {nom}: {exc!r}", ph.syslog.LOG_ERR)
    if etat.get("nextcloud"):
        revoquer_nextcloud(etat["nextcloud"], http=http)
    purger_depots()
    if etat.get("mode") == "persistant":
        (fermer or fermer_profil)(nom, etat.get("maison"), run=run, attendre=attendre)
        (demonter or demonter_profils_si_libre)(cfg, run=run)
    try:
        os.unlink(chemin_etat)
    except FileNotFoundError:
        pass
    journal(f"session {nom} arretee")


def garder(cfg, nom, run=subprocess.run):
    """Filet : quelques minutes apres la preparation, si la personne n'a
    AUCUNE session ouverte (l'ecran n'a pas pu la demarrer, ou on a annule),
    l'unite est arretee : profil referme, mot de passe Nextcloud revoque.
    Sans ce filet, aucun close_session n'arriverait jamais."""
    env = {"PATH": CHEMIN}
    res = run(["loginctl", "list-sessions", "--no-legend", "-o", "json"],
              capture_output=True, text=True, timeout=30, env=env)
    try:
        sessions = json.loads(res.stdout or "[]")
    except ValueError:
        return False
    if any(s.get("user") == nom for s in sessions if isinstance(s, dict)):
        return False
    journal(f"aucune session pour {nom} : preparation defaite", ph.syslog.LOG_WARNING)
    run(["systemctl", "stop", "--no-block", ph._unite(cfg, nom)],
        capture_output=True, timeout=30, env=env)
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["zone-flatpak"], ["maj-flatpak"]):
        from bfos_live import zone_flatpak
        return zone_flatpak.main(argv)
    if argv == ["lire-organisation"]:
        try:
            socle = ph.charger_socle(ph.CONFIG)
        except ph.Refus as exc:
            print(exc.raison, file=sys.stderr)
            return 1
        if organisation.socle_generique(socle):
            lire_organisation_retenue(socle)
        return 0
    if len(argv) != 2 or argv[0] not in ("demarrer", "arreter", "garder", "sauvegarder",
                                         "agrandir"):
        print("usage: session demarrer|arreter|garder|sauvegarder|agrandir <nom>", file=sys.stderr)
        return 2
    commande, nom = argv
    if not ph.NOM_VALIDE.match(nom):
        return 2
    try:
        cfg = ph.charger_config(ph.CONFIG)
    except ph.Refus as exc:
        print(exc.raison, file=sys.stderr)
        return 1
    if commande == "arreter":
        arreter(cfg, nom)
        return 0
    if commande == "garder":
        garder(cfg, nom)
        return 0
    if commande == "sauvegarder":
        return 0 if sauvegarder(cfg, nom) else 1
    if commande == "agrandir":
        agrandir(cfg, nom)
        return 0
    try:
        etat = demarrer(cfg, nom)
        ecrire_resultat(cfg, nom, True, mode=etat["mode"],
                        profil_neuf=bool(etat.get("profil_neuf")))
        return 0
    except Echec as exc:
        journal(f"session {nom}: {exc.raison}", ph.syslog.LOG_ERR)
        ecrire_resultat(cfg, nom, False, _(exc.message))
    except Exception as exc:  # noqa: BLE001 - jamais de pile a l'ecran
        journal(f"session {nom}: erreur interne {exc!r}", ph.syslog.LOG_ERR)
        ecrire_resultat(cfg, nom, False, _("Erreur interne en préparant ta session."))
    # Une unite oneshot dont ExecStart echoue ne passe pas par ExecStop :
    # l'etat partiel (profil a moitie ouvert, fichiers deposes) se defait ici.
    arreter(cfg, nom)
    return 1


if __name__ == "__main__":
    sys.exit(main())
