#!/usr/bin/python3 -I
"""Aide de connexion de la cle USB live Blue Fox OS (#26870).

Lancee par `pam_bfos.so`, une fois par tentative d'authentification. Elle
parle au module par ses entree et sortie standard, une ligne par message :

    aide -> module   INFO <texte>     message d'information (PAM_TEXT_INFO)
                     ERROR <texte>    message d'erreur (PAM_ERROR_MSG)
                     ASK <texte>      question visible, attend une reponse
                     SECRET <texte>   question masquee, attend une reponse
                     USER <nom>       l'usager local a ouvrir (une fois)
                     OK               authentification reussie
                     FAIL <raison>    authentification refusee
    module -> aide   <reponse>        une ligne par ASK/SECRET

Dans les deux sens, `\\` s'ecrit `\\\\` et un saut de ligne `\\n`.

Deux situations, decidees par le PAM_USER que l'application a demande :

  * OUVERTURE (PAM_USER = `placeholder_user`, ce que passe l'ecran greetd) :
    flux d'appareil, verification de l'id_token, nom et uid derives du `sub`,
    creation de l'usager a la volee (`/etc` est volatil sur la cle), jetons
    deposes dans /run pour la session. Exige root.
  * REAUTHENTIFICATION (PAM_USER = un compte que cette cle a cree, par exemple
    a l'ecran de verrouillage) : le QR doit etre approuve par LA MEME personne
    (meme `sub`), sinon refus. Un passant qui scanne avec son propre compte ne
    deverrouille pas la session d'un autre. Marche aussi sans root : le
    registre est lisible par tous (`sub` et nom d'usager Authentik, rien de
    secret). A l'ecran de verrouillage, le mot de passe Authentik de la
    personne suffit (arbitre le 2026-10-27) ; un champ vide donne le QR.

Tout autre PAM_USER est refuse.

`close` revoque les jetons de la session et les efface, avec des delais courts :
une deconnexion ne se fige pas faute de reseau.

Pourquoi pas de verification de signature : l'id_token arrive par HTTPS, sans
redirection, en reponse directe du point de jeton, a la demande de ce
processus. OpenID Connect Core 1.0, 3.1.3.7 (6) admet alors la validation du
serveur TLS a la place de la signature. On verifie quand meme iss, aud, azp,
exp, iat et la presence de la 2FA (`amr`).
"""
from __future__ import annotations

import base64
import grp
import hashlib
import http.cookiejar
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import syslog
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# bfos_provision voyage a cote de cette aide dans l'image (meme dossier) ; dans
# le depot, il vit dans install/. C'est le flux d'appareil de l'installation,
# eprouve sur machine reelle : on le reutilise tel quel.
_ICI = os.path.dirname(os.path.abspath(__file__))
for _chemin in (_ICI, os.path.join(_ICI, "..", "..", "install")):
    if os.path.exists(os.path.join(_chemin, "bfos_provision.py")):
        sys.path.insert(0, os.path.abspath(_chemin))
        break
if not os.path.isdir(os.path.join(_ICI, "bfos_live")):
    sys.path.insert(0, os.path.abspath(os.path.join(_ICI, "..")))
import bfos_provision as bp  # noqa: E402
from bfos_live import langue  # noqa: E402
from bfos_live import organisation  # noqa: E402
from bfos_live.langue import N_, _  # noqa: E402

CONFIG = "/etc/bfos-live/live.json"

DEFAUTS = {
    # Groupes Authentik admis. VIDE = PERSONNE : une instance Authentik sert
    # souvent plusieurs organisations, et l'acces ne s'ouvre jamais par defaut.
    "allowed_groups": [],
    # La 2FA est exigee dans l'id_token (`amr` contient "mfa"). Le flux par
    # defaut d'Authentik saute l'etape MFA pour un compte sans appareil.
    "require_mfa": True,
    "uid_min": 10000,
    "uid_max": 59999,
    "shell": "/bin/bash",
    "local_groups": [],
    "state_dir": "/run/bfos-live",
    # Nom que l'ecran greetd passe pour une OUVERTURE ; ne doit pas exister.
    "placeholder_user": "bfos-connexion",
    # Services PAM dont le client sait dessiner un QR a partir de la ligne
    # machine `bfos:device {...}` : l'ecran de connexion maison sous greetd.
    "rich_services": ["greetd"],
    # Services affiches sur une console texte : QR en demi-blocs.
    "ascii_qr_services": ["login"],
    # Ecran de verrouillage de Plasma (kscreenlocker) : quelques lignes sous le
    # nom, pas de QR possible. Message court, adresse sans https://.
    "lock_services": ["kde"],
    # Session (#26870, 2026-10-23). Politique, cle du profil et Nextcloud
    # sont prepares par l'unite root bfos-live-session@<nom>.service.
    "policy_url": "",
    "profile_key_url": "",
    "login_link_url": "",
    # Offrir le profil persistant chiffre sur la cle. Faux = ephemere seulement.
    "persistence": True,
    # Offrir le profil itinerant chiffre dans Nextcloud (#26870, 2026-10-24).
    "roaming": True,
    "profile_size_mb": 8192,
    # Le profil grandit au besoin, en laissant de quoi creer un profil complet
    # a une autre personne ; plafond par personne (0 = la moitie de la partition).
    "profile_reserve_mb": None,  # None : profile_size_mb
    "profile_max_mb": 0,
    "session_unit": "bfos-live-session@{nom}.service",
    "session_timeout": 900,
    # Filet : sans session ouverte pour la personne apres ce delai, l'unite
    # est arretee (profil referme, mot de passe Nextcloud revoque).
    "session_guard_seconds": 180,
    # Un jeton d'acces plus vieux que ceci est rafraichi avant de servir.
    "token_refresh_after": 300,
}

PREFIXE_CHOIX = "bfos:choix "
CHOIX_SESSION = [
    {"id": "ephemere", "titre": N_("Session éphémère"),
     "texte": N_("Rien n'est gardé sur la clé. Tes fichiers Nextcloud restent "
                 "sur Nextcloud ; tout le reste disparaît à l'extinction.")},
    {"id": "persistant", "titre": N_("Garder mon bureau sur cette clé"),
     "texte": N_("Ton dossier personnel est gardé, chiffré, sur la clé : "
                 "réglages, navigateur, applis que tu installes. Seul ton "
                 "compte peut l'ouvrir.")},
    {"id": "itinerant", "titre": N_("Garder mon bureau dans Nextcloud"),
     "texte": N_("Ton dossier personnel est gardé, chiffré, dans ton Nextcloud : "
                 "tu le retrouves sur n'importe quelle clé, sans les caches. "
                 "L'ouverture et la fermeture prennent un peu plus de temps.")},
]

# Nom d'usager local : ce que shadow-utils accepte sans option, en minuscules.
NOM_VALIDE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SKEW = 120  # secondes de derive d'horloge tolerees sur exp/iat
DELAI_HTTP = 30
DELAI_REVOCATION = 5
# Ecran de verrouillage : mot de passe Authentik (#26870, arbitre le 2026-10-27).
DELAI_MOT_DE_PASSE = 15
ATTENTE_REFUS = 2                 # secondes avant de rendre un refus : ralentit les essais
ECHEANCE_MOT_DE_PASSE = 25        # secondes en tout pour une verification
ECHECS_MAX = 5                    # refus de suite, puis le QR seulement
COMPTEUR_ECHECS = "/run/user/{uid}/bfos-live-echecs"   # tmpfs de la personne : un passant n'y touche pas
TAILLE_REPONSE_MAX = 1 << 20
FLUX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}")


class Refus(Exception):
    """Refus explique a la personne (message) et au journal (raison)."""

    def __init__(self, message, raison=None):
        super().__init__(message)
        self.message = message
        self.raison = raison or message


# --------------------------------------------------------------- transport
class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise urllib.error.HTTPError(req.full_url, code, "redirection refusee", headers, fp)


_OUVREUR = urllib.request.build_opener(_SansRedirection())


def post_https(url, data, timeout=DELAI_HTTP):
    """POST de formulaire, HTTPS seulement, sans suivre de redirection."""
    if not str(url).startswith("https://"):
        raise OSError(f"adresse non HTTPS refusee: {url}")
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    with _OUVREUR.open(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


# --------------------------------------------------------------- protocole
def echapper(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace("\n", "\\n")


def desechapper(ligne: str) -> str:
    out, i = [], 0
    while i < len(ligne):
        c = ligne[i]
        if c == "\\" and i + 1 < len(ligne):
            nxt = ligne[i + 1]
            out.append("\n" if nxt == "n" else nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


class Canal:
    """Le dialogue avec pam_bfos.so. Injectable pour les essais."""

    def __init__(self, entree=None, sortie=None):
        self.entree = entree or sys.stdin
        self.sortie = sortie or sys.stdout

    def _envoyer(self, verbe, texte=None):
        ligne = verbe if texte is None else f"{verbe} {echapper(texte)}"
        self.sortie.write(ligne + "\n")
        self.sortie.flush()

    def info(self, texte):
        self._envoyer("INFO", texte)

    def erreur(self, texte):
        self._envoyer("ERROR", texte)

    def demander(self, texte, secret=False):
        self._envoyer("SECRET" if secret else "ASK", texte)
        ligne = self.entree.readline()
        if not ligne:
            raise Refus(N_("Connexion annulée."), "le module a ferme le dialogue")
        return desechapper(ligne.rstrip("\n"))

    def usager(self, nom):
        self._envoyer("USER", nom)

    def ok(self):
        self._envoyer("OK")

    def echec(self, raison):
        self._envoyer("FAIL", raison)


# ------------------------------------------------------------ configuration
def charger_socle(chemin=CONFIG):
    """Le live.json de l'image, complete des defauts. Sur une cle generique, il
    ne nomme aucun fournisseur d'identite : l'organisation vient ensuite."""
    try:
        with open(chemin, encoding="utf-8") as f:
            brut = json.load(f)
    except (OSError, ValueError) as exc:
        raise Refus(N_("Cette clé n'est pas configurée."),
                    f"configuration illisible {chemin}: {exc}") from exc
    if not isinstance(brut, dict):
        raise Refus(N_("Cette clé n'est pas configurée."), f"configuration {chemin} : pas un objet")
    cfg = dict(DEFAUTS)
    cfg.update(brut)
    return cfg


def avec_organisation(socle, org):
    """Le socle, plus la configuration de l'organisation choisie (cle generique)."""
    cfg = organisation.fusionner(socle, org["live"])
    cfg["organisation"] = org["organisation"]
    cfg["domaine"] = org["domaine"]
    return valider_config(cfg)


def valider_config(cfg):
    pas_configuree = N_("Cette clé n'est pas configurée.")
    for cle in ("issuer", "client_id", "device_url", "token_url"):
        if not cfg.get(cle):
            raise Refus(pas_configuree, f"configuration incomplete : {cle} manquant")
    for cle in ("issuer", "device_url", "token_url", "revoke_url"):
        if cfg.get(cle) and not str(cfg[cle]).startswith("https://"):
            raise Refus(pas_configuree, f"configuration : {cle} doit etre en https")
    if not (0 < int(cfg["uid_min"]) < int(cfg["uid_max"])):
        raise Refus(pas_configuree, "plage d'uid invalide")
    if int(cfg["uid_min"]) < 1000:
        # Jamais dans la plage des comptes systeme.
        raise Refus(pas_configuree, "uid_min sous 1000")
    if not cfg.get("allowed_groups"):
        raise Refus(pas_configuree, "allowed_groups vide : personne n'est admis")
    if not NOM_VALIDE.match(str(cfg["placeholder_user"])):
        raise Refus(pas_configuree, "placeholder_user invalide")
    return cfg


def charger_config(chemin=CONFIG, dossier_org=organisation.DOSSIER):
    """La configuration complete : celle de l'image, ou, sur une cle generique,
    le socle plus l'organisation deposee pendant ce demarrage (verrouillage,
    fermeture, unite de session)."""
    socle = charger_socle(chemin)
    if organisation.socle_generique(socle):
        org = organisation.deposee(dossier_org)
        if not org:
            raise Refus(N_("Aucune organisation choisie. Redémarre la clé."),
                        "cle generique sans organisation deposee")
        return avec_organisation(socle, org)
    return valider_config(socle)


# ------------------------------------------------------------ l'id_token
def _b64url(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def verifier_id_token(id_token, cfg, maintenant=None):
    """Rend les revendications d'un id_token recu directement du point de
    jeton, apres verification de iss, aud, azp, exp, iat et de la 2FA."""
    maintenant = time.time() if maintenant is None else maintenant
    try:
        _entete, charge, _sig = id_token.split(".")
        claims = json.loads(_b64url(charge))
    except (ValueError, AttributeError) as exc:
        raise Refus("Réponse d'identité illisible.", f"id_token mal forme: {exc}") from exc
    if not isinstance(claims, dict):
        raise Refus("Réponse d'identité illisible.", "id_token sans objet")

    if claims.get("iss") != cfg["issuer"]:
        raise Refus("Réponse d'un autre fournisseur d'identité.",
                    f"iss inattendu: {claims.get('iss')!r}")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if cfg["client_id"] not in auds:
        raise Refus("Réponse destinée à une autre application.",
                    f"aud inattendu: {aud!r}")
    if len(auds) > 1 and claims.get("azp") != cfg["client_id"]:
        raise Refus("Réponse destinée à une autre application.",
                    f"azp inattendu: {claims.get('azp')!r}")
    try:
        exp = float(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Refus("Réponse d'identité incomplète.", "exp absent") from exc
    if exp + SKEW < maintenant:
        raise Refus("Autorisation expirée, recommence.", "id_token expire")
    iat = claims.get("iat")
    if iat is not None and float(iat) - SKEW > maintenant:
        raise Refus("L'horloge de ce PC est déréglée.", "iat dans le futur")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise Refus("Réponse d'identité incomplète.", "sub absent")
    if cfg.get("require_mfa", True):
        amr = claims.get("amr")
        if not isinstance(amr, list) or "mfa" not in amr:
            raise Refus("La double authentification est obligatoire pour ces clés.",
                        f"amr sans mfa: {amr!r} sub={sub}")
    return claims


def verifier_groupes(claims, cfg):
    permis = set(cfg.get("allowed_groups") or [])
    groupes = claims.get("groups") or []
    if not permis or not isinstance(groupes, list) or not permis.intersection(groupes):
        raise Refus("Ton compte n'a pas accès aux clés Blue Fox OS.",
                    f"aucun groupe permis pour sub={claims['sub']}")


# --------------------------------------------------------- nom et uid locaux
def _empreinte(sub: str) -> str:
    return hashlib.sha256(sub.encode()).hexdigest()


def identite(claims, cfg) -> str:
    """La cle locale d'une personne : son `sub` sur une cle configuree, et
    l'EMETTEUR avec son `sub` sur une cle generique. La, c'est le domaine tape
    qui choisit le fournisseur d'identite : un fournisseur malveillant emet le
    `sub` qu'il veut, et sans l'emetteur il reprendrait le compte local (ou le
    profil) d'une personne d'une autre organisation (relecture du 2026-10-27)."""
    if cfg.get("organisation"):
        return f"{claims['iss']}\0{claims['sub']}"
    return claims["sub"]


def nom_de_base(claims, ident=None) -> str:
    """Nom local : un prefixe lisible TOUJOURS suffixe de l'empreinte du `sub`.

    Le suffixe n'est pas decoratif. Sans lui, le premier qui met « olivier »
    dans son profil Authentik devient `olivier` sur la cle, et tout ce qui se
    fie a $USER en aval (profil, journaux) le prend pour Olivier. Le nom
    humain, lui, va dans le champ GECOS.
    """
    brut = claims.get("preferred_username") or ""
    if not brut and claims.get("email_verified") is True:
        brut = claims.get("email") or ""
    brut = str(brut).split("@", 1)[0]
    # « Élodie » doit donner « elodie », pas « -lodie » : on retire les accents
    # avant de remplacer ce qui reste d'illisible pour shadow-utils.
    brut = unicodedata.normalize("NFKD", brut)
    brut = "".join(c for c in brut if not unicodedata.combining(c)).lower()
    prefixe = re.sub(r"[^a-z0-9_-]", "-", brut).strip("-")[:24].strip("-")
    ident = ident or claims["sub"]
    suffixe = _empreinte(ident)[:6]
    nom = f"{prefixe}-{suffixe}" if prefixe else f"u{_empreinte(ident)[:10]}"
    if not NOM_VALIDE.match(nom):
        nom = "u" + _empreinte(ident)[:10]
    return nom


def uid_de_base(sub: str, cfg) -> int:
    lo, hi = int(cfg["uid_min"]), int(cfg["uid_max"])
    return lo + int(_empreinte(sub), 16) % (hi - lo + 1)


def dossier_sur(chemin, mode, proprietaire=0):
    """Cree `chemin` s'il manque, puis verifie que ce n'est pas un lien, qu'il
    appartient a root et qu'il n'est inscriptible ni par le groupe ni par les
    autres. Un dossier prepare par quelqu'un d'autre ferait forger le registre."""
    parent = os.path.dirname(chemin.rstrip("/"))
    if parent and parent != chemin and not os.path.isdir(parent):
        raise Refus("Impossible d'ouvrir ta session sur cette clé.",
                    f"dossier parent absent: {parent}")
    try:
        os.mkdir(chemin, mode)
    except FileExistsError:
        pass
    st = os.lstat(chemin)
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != proprietaire
            or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise Refus("Impossible d'ouvrir ta session sur cette clé.",
                    f"dossier suspect: {chemin} (uid={st.st_uid} mode={oct(st.st_mode)})")
    os.chmod(chemin, mode, follow_symlinks=False)


class Annuaire:
    """Comptes locaux et registre de la cle. Injectable pour les essais."""

    def __init__(self, cfg, getpwnam=pwd.getpwnam, getpwuid=pwd.getpwuid,
                 getgrnam=grp.getgrnam, run=subprocess.run):
        self.cfg = cfg
        self.getpwnam = getpwnam
        self.getpwuid = getpwuid
        self.getgrnam = getgrnam
        self.run = run
        self.registre = os.path.join(cfg["state_dir"], "users")

    def _existe(self, nom):
        try:
            return self.getpwnam(nom)
        except KeyError:
            return None

    def _groupe_existe(self, nom):
        try:
            self.getgrnam(nom)
            return True
        except KeyError:
            return False

    def _uid_pris(self, uid):
        try:
            self.getpwuid(uid)
            return True
        except KeyError:
            return False

    def _registre(self, nom):
        """L'entree du registre pour `nom`, ou {}. Le fichier doit appartenir a
        root (meme lu par un usager, a l'ecran de verrouillage)."""
        chemin = os.path.join(self.registre, nom + ".json")
        try:
            fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return {}
        try:
            st = os.fstat(fd)
            if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return {}
            d = json.loads(os.read(fd, 65536))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}
        finally:
            os.close(fd)

    def proprietaire(self, nom):
        """Le `sub` pour lequel CETTE cle a cree `nom`, ou None."""
        return self._registre(nom).get("sub")

    def identifiant(self, nom):
        """Le nom d'usager Authentik de la personne a qui CETTE cle a cree `nom`,
        ou None (compte ouvert par une version qui ne le retenait pas)."""
        valeur = self._registre(nom).get("identifiant")
        return valeur if isinstance(valeur, str) and 0 < len(valeur) <= 150 else None

    def compte_de(self, nom, sub):
        """Le compte `nom`, s'il a ete cree par cette cle pour `sub`."""
        existant = self._existe(nom)
        if (existant is not None and existant.pw_uid >= int(self.cfg["uid_min"])
                and self.proprietaire(nom) == sub):
            return existant
        return None

    def choisir(self, claims):
        """(nom, uid, a_creer) pour une OUVERTURE. Ne reprend JAMAIS un compte
        qui n'est pas a cette personne."""
        sub = identite(claims, self.cfg)
        nom = nom_de_base(claims, sub)
        if nom == self.cfg["placeholder_user"]:
            nom = "u" + _empreinte(sub)[:10]
        existant = self.compte_de(nom, sub)
        if existant is not None:
            return nom, existant.pw_uid, False
        if self._existe(nom) is None and not self._groupe_existe(nom):
            return nom, self._uid_libre(sub), True
        raise Refus("Impossible d'ouvrir ta session sur cette clé.",
                    f"nom local {nom} deja pris pour sub={sub}")

    def _uid_libre(self, sub):
        lo, hi = int(self.cfg["uid_min"]), int(self.cfg["uid_max"])
        uid = uid_de_base(sub, self.cfg)
        for _n in range(256):
            if not self._uid_pris(uid):
                return uid
            uid = lo + (uid - lo + 1) % (hi - lo + 1)
        raise Refus("Impossible d'ouvrir ta session sur cette clé.", "aucun uid libre")

    def creer(self, nom, uid, claims):
        gecos = re.sub(r"[:,=\\\n\r\x00]", " ", str(claims.get("name") or nom))[:64]
        argv = ["useradd", "--uid", str(uid), "--user-group", "--create-home",
                "--comment", gecos, "--shell", self.cfg["shell"]]
        groupes = [g for g in self.cfg.get("local_groups") or []
                   if re.match(r"^[a-z_][a-z0-9_-]*$", g)]
        if groupes:
            argv += ["--groups", ",".join(groupes)]
        argv.append(nom)
        res = self.run(argv, capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/sbin:/usr/bin"})
        if res.returncode != 0:
            raise Refus("Impossible de créer ta session sur cette clé.",
                        f"useradd {nom} a echoue ({res.returncode}): {res.stderr.strip()}")
        dossier_sur(self.cfg["state_dir"], 0o755)
        dossier_sur(self.registre, 0o755)
        # Lisible par tous : l'ecran de verrouillage tourne sous l'usager et
        # doit retrouver le `sub` du compte. Un `sub` n'est pas un secret.
        entree = {"sub": identite(claims, self.cfg), "uid": uid}
        ident = identifiant_authentik(claims)
        if ident:
            # Ce que l'etape d'identification d'Authentik reconnait : sert a
            # verifier le mot de passe a l'ecran de verrouillage. Pas un secret.
            entree["identifiant"] = ident
        _ecrire(os.path.join(self.registre, nom + ".json"), entree, mode=0o644, uid=0, gid=0)


def _ecrire(chemin, donnees, mode=0o600, uid=0, gid=0):
    """Ecrit un JSON sans fenetre ou il aurait un autre mode ou proprietaire."""
    _ecrire_texte(chemin, json.dumps(donnees), mode=mode, uid=uid, gid=gid)


def _ecrire_texte(chemin, texte, mode=0o600, uid=0, gid=0):
    """Ecrit un fichier sans fenetre ou il aurait un autre mode ou proprietaire."""
    tmp = chemin + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        if os.geteuid() == 0:
            os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.write(fd, texte.encode())
    finally:
        os.close(fd)
    os.replace(tmp, chemin)


def deposer_jetons(cfg, nom, uid, gid, reponse, claims):
    """Jetons de la session, lisibles par la personne seule, en memoire vive."""
    dossier_sur(cfg["state_dir"], 0o755)
    dossier = os.path.join(cfg["state_dir"], "sessions")
    dossier_sur(dossier, 0o711)
    expire = int(time.time()) + int(reponse.get("expires_in") or 300)
    _ecrire(os.path.join(dossier, nom + ".json"), {
        "sub": claims["sub"],
        "access_token": reponse.get("access_token"),
        "refresh_token": reponse.get("refresh_token"),
        "id_token": reponse.get("id_token"),
        "expires_at": expire,
    }, mode=0o600, uid=uid, gid=gid)


# ------------------------------------------------- mot de passe de verrouillage
def identifiant_authentik(claims):
    """Le nom d'usager d'Authentik (`preferred_username`), ou None. Pas de repli sur
    le courriel : deux comptes peuvent le partager (relecture du 2026-10-27)."""
    valeur = claims.get("preferred_username")
    if isinstance(valeur, str) and 0 < len(valeur.strip()) <= 150 \
            and not re.search(r"[\x00-\x1f\x7f]", valeur):
        return valeur.strip()
    return None


class Navigation:
    """Un passage dans le flux de connexion d'Authentik : temoins en memoire,
    HTTPS sur l'hote de l'emetteur seulement, aucune redirection suivie."""

    def __init__(self, base, ouvreur=None, echeance=ECHEANCE_MOT_DE_PASSE, horloge=time.monotonic):
        self.base = base
        self.ouvreur = ouvreur or urllib.request.build_opener(
            _SansRedirection(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.horloge = horloge
        self.fin = horloge() + echeance

    def json(self, chemin, corps=None):
        url = self.base + chemin
        for _n in range(4):
            req = urllib.request.Request(
                url, method="GET" if corps is None else "POST",
                data=None if corps is None else json.dumps(corps).encode())
            req.add_header("Accept", "application/json")
            if corps is not None:
                req.add_header("Content-Type", "application/json")
            reste = self.fin - self.horloge()
            if reste <= 0:
                raise ValueError("delai depasse")
            try:
                with self.ouvreur.open(req, timeout=min(DELAI_MOT_DE_PASSE, reste)) as resp:
                    brut = resp.read(TAILLE_REPONSE_MAX + 1)
                break
            except urllib.error.HTTPError as exc:
                # L'executeur rend 302 vers lui-meme apres chaque reponse (mesure au
                # banc) : suivie en GET, sur le meme hote et dans l'executeur seulement.
                if exc.code not in (302, 303):
                    raise
                vers = urllib.parse.urljoin(url, exc.headers.get("Location") or "")
                v, b = urllib.parse.urlsplit(vers), urllib.parse.urlsplit(self.base)
                if (v.scheme, v.netloc) != (b.scheme, b.netloc) \
                        or not v.path.startswith("/api/v3/flows/executor/"):
                    raise ValueError(f"redirection refusee: {vers!r}") from exc
                url, corps = vers, None
        else:
            raise ValueError("trop de redirections")
        if len(brut) > TAILLE_REPONSE_MAX:
            raise ValueError("reponse trop longue")
        d = json.loads(brut.decode())
        if not isinstance(d, dict):
            raise ValueError("reponse inattendue")
        return d


INDISPONIBLE = N_("Mot de passe impossible à vérifier pour l'instant. "
                  "Laisse le champ vide pour recevoir un code.")


def verifier_mot_de_passe(cfg, identifiant, mot_de_passe, navigation=None):
    """True si l'Authentik de l'emetteur accepte le mot de passe d'`identifiant`,
    False s'il le refuse ; Refus quand on ne peut pas savoir (reseau, flux
    inattendu). On joue le flux de connexion par defaut de la marque jusqu'a
    l'etape qui SUIT le mot de passe (MFA en general), sans aller plus loin. Un
    compte sans MFA va jusqu'au bout du flux : la session Authentik ouverte vit
    dans des temoins jetes aussitot. Mesure le 2026-10-27 (Authentik du banc) : bon
    mot de passe -> ak-stage-authenticator-validate ; mauvais, ou compte inconnu ->
    ak-stage-password avec « Invalid password ». `pending_user` (l'usager que le
    flux a reconnu) doit etre `identifiant`, sinon refus sans envoyer le mot de
    passe. Limite assumee : les groupes admis ne sont verifies qu'au QR."""
    u = urllib.parse.urlsplit(str(cfg.get("issuer") or ""))
    if u.scheme != "https" or not u.netloc or u.username or u.password:
        raise Refus(INDISPONIBLE, f"emetteur inattendu: {cfg.get('issuer')!r}")
    nav = navigation or Navigation(f"https://{u.netloc}")
    try:
        flux = nav.json("/api/v3/core/brands/current/").get("flow_authentication")
        if not isinstance(flux, str) or not FLUX_RE.fullmatch(flux):
            raise ValueError(f"flux de connexion inattendu: {flux!r}")
        racine = f"/api/v3/flows/executor/{flux}/"
        chemin = racine + "?query=" + urllib.parse.quote("next=/", safe="")

        def suivre(d, apres_mot=False):
            # Apres chaque reponse, l'executeur renvoie vers lui-meme : on y va,
            # sans jamais sortir de ce flux. Une sortie APRES le mot de passe est la
            # fin du flux (compte sans MFA).
            for _n in range(5):
                if d.get("component") != "xak-flow-redirect" or d.get("final_redirect"):
                    return d
                vers = d.get("to")
                if not isinstance(vers, str) or not vers.startswith(racine):
                    if apres_mot and isinstance(vers, str):
                        return {"component": "xak-flow-redirect", "final_redirect": True}
                    raise ValueError(f"redirection hors du flux: {vers!r}")
                d = nav.json(vers)
            raise ValueError("trop de redirections")

        def autre_usager(d, exige=False):
            vu = d.get("pending_user")
            if not isinstance(vu, str):
                return exige
            return vu.casefold() != identifiant.casefold()

        d = suivre(nav.json(chemin))
        envoye = False
        for _n in range(6):
            etape, erreurs = d.get("component"), d.get("response_errors")
            if etape == "ak-stage-identification":
                if envoye or erreurs:
                    return False
                if d.get("captcha_stage"):
                    raise ValueError("captcha dans l'identification")
                corps = {"component": etape, "uid_field": identifiant}
                if d.get("password_fields"):
                    corps["password"] = mot_de_passe
                    envoye = True
                d = suivre(nav.json(chemin, corps), apres_mot=envoye)
            elif etape == "ak-stage-password":
                # Exige a l'etape du mot de passe : sans usager reconnu, on n'envoie rien.
                if envoye or autre_usager(d, exige=True):
                    return False
                envoye = True
                d = suivre(nav.json(chemin, {"component": etape, "password": mot_de_passe}),
                           apres_mot=True)
            elif etape == "ak-stage-access-denied":
                if envoye:
                    return False
                raise ValueError("acces refuse avant le mot de passe")
            elif not envoye:
                raise ValueError(f"etape {etape!r} avant le mot de passe")
            else:
                # MFA, consentement ou fin du flux : le mot de passe est passe.
                return not erreurs and not autre_usager(d)
        raise ValueError("flux trop long")
    except (OSError, ValueError) as exc:
        raise Refus(INDISPONIBLE, f"verification du mot de passe: {exc!r}") from exc


def lire_echecs(chemin):
    """Refus de suite, ou None si le compteur est illisible (on ferme alors le mot de passe)."""
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return 0 if os.path.isdir(os.path.dirname(chemin)) else None
    except OSError:
        return None
    try:
        texte = os.read(fd, 32).decode().strip()
        return int(texte) if texte.isdigit() else None   # vide ou abime : ferme
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def ecrire_echecs(chemin, n):
    """Par fichier provisoire puis rename : jamais de compteur a moitie ecrit (lu vide,
    il fermerait le mot de passe ; lu 0, il l'ouvrirait)."""
    tmp = f"{chemin}.{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, f"{n}\n".encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, chemin)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def par_mot_de_passe(canal, cfg, annuaire, nom, sleep=time.sleep, verifier=verifier_mot_de_passe,
                     compteur=None):
    """Ecran de verrouillage : le mot de passe Authentik de la personne, ou un
    champ vide pour le QR. True si accepte, False pour passer au QR, Refus si
    refuse. Seulement pour un compte que CETTE cle a cree avec son identifiant.
    Apres ECHECS_MAX refus de suite, le QR seulement jusqu'a un deverrouillage
    reussi (relecture du 2026-10-27 : un passant devinait sans fin)."""
    ident = annuaire.identifiant(nom)
    compte = annuaire.compte_de(nom, annuaire.proprietaire(nom))
    if not ident or compte is None:
        return False
    compteur = compteur or COMPTEUR_ECHECS.format(uid=compte.pw_uid)
    echecs = lire_echecs(compteur)
    if echecs is None or echecs >= ECHECS_MAX:
        if echecs is not None:
            canal.info(_("Trop d'essais de mot de passe : approuve le code sur ton téléphone."))
        return False
    mot = canal.demander(_("Mot de passe (vide : code sur ton téléphone)"), secret=True)
    if not mot:
        return False
    try:
        accepte = verifier(cfg, ident, mot)
    finally:
        mot = None
    if not accepte:
        if not ecrire_echecs(compteur, echecs + 1):
            # Refus non compte : le prochain essai trouvera un compteur illisible
            # ou ancien ; on ferme plutot le mot de passe jusqu'au QR.
            ecrire_echecs(compteur, ECHECS_MAX)
        sleep(ATTENTE_REFUS)
        raise Refus(N_("Mot de passe refusé."), f"mot de passe refuse pour {nom} ({echecs + 1})")
    ecrire_echecs(compteur, 0)
    return True


# ------------------------------------------------------------ les ecrans
def code_lisible(code):
    """Un code tout en chiffres, par groupes de trois (« 748 240 472 »), comme
    l'ecran de connexion ; tout autre code tel quel."""
    if re.fullmatch(r"\d{6,12}", code or "") and len(code) % 3 == 0:
        return " ".join(code[i:i + 3] for i in range(0, len(code), 3))
    return code


def message_appareil(d, service, cfg):
    """Le texte qui accompagne le QR, adapte au client PAM."""
    uri = d.get("verification_uri") or ""
    complet = d.get("verification_uri_complete") or uri
    code = d.get("user_code") or "?"
    humain = _("Ouvre {complet} sur ton téléphone pour te connecter,\n"
               "ou va sur {uri} et entre le code {code}.").format(complet=complet, uri=uri, code=code)
    if service in cfg["rich_services"]:
        champs = {"uri": uri, "uri_complete": complet, "user_code": code,
                  "expires_in": d.get("expires_in")}
        if cfg.get("organisation"):
            # Cle generique : l'ecran montre l'organisation et « Changer ».
            champs.update(organisation=cfg["organisation"], domaine=cfg.get("domaine") or "")
        machine = json.dumps(champs, separators=(",", ":"), ensure_ascii=False)
        return f"bfos:device {machine}\n{humain}"
    if service in (cfg.get("lock_services") or ()):
        court = uri.split("://", 1)[-1]
        return _("Pour déverrouiller, va sur {court} avec ton téléphone\n"
                 "et entre le code {code}.").format(court=court, code=code_lisible(code))
    if service in cfg["ascii_qr_services"] and complet:
        try:
            lignes, _l = bp.render_qr_lines(complet, quiet=2)
            return "\n".join(lignes) + "\n" + humain
        except Exception:  # noqa: BLE001 - un QR rate ne coute jamais le code
            pass
    return humain


# ------------------------------------------------------------ la session
def message_choix(service, cfg, erreur=None, options=None, titre=None):
    """La question « ephemere ou persistant » (ou une autre, `titre`), lisible par
    l'ecran maison (ligne machine) et par une console (texte numerote)."""
    options = [dict(o, titre=_(o["titre"]), texte=_(o.get("texte", "")))
               for o in (options or CHOIX_SESSION)]
    titre = titre or _("Comment veux-tu ouvrir ta session ?")
    erreur = _(erreur) if erreur else erreur
    lignes = [f"{i}) {o['titre']} : {o['texte']}" for i, o in enumerate(options, 1)]
    humain = "\n".join(([erreur] if erreur else []) + [titre] + lignes
                        + [_("Réponds {choix}.").format(
                            choix=_(" ou ").join(str(i) for i in range(1, len(options) + 1)))])
    if service in cfg["rich_services"]:
        machine = json.dumps({"titre": titre, "erreur": erreur or "",
                              "options": options}, separators=(",", ":"),
                             ensure_ascii=False)
        return f"{PREFIXE_CHOIX}{machine}\n{humain}"
    return humain


def lire_choix(reponse, options):
    reponse = (reponse or "").strip().lower()
    for i, o in enumerate(options, 1):
        if reponse in (str(i), o["id"]):
            return o["id"]
    return None


def choix_offerts(cfg):
    """Les modes que cette cle propose, l'ephemere toujours en premier."""
    retires = set()
    if not cfg.get("persistence", True):
        retires.add("persistant")
    if not cfg.get("roaming", True):
        retires.add("itinerant")
    return [o for o in CHOIX_SESSION if o["id"] not in retires]


def demander_mode(canal, cfg, service, erreur=None, options=None):
    options = options or choix_offerts(cfg)
    if len(options) == 1:
        return options[0]["id"]
    for _n in range(3):
        choix = lire_choix(canal.demander(message_choix(service, cfg, erreur, options)), options)
        if choix:
            return choix
        erreur = N_("Réponse non comprise.")
    raise Refus("Connexion annulée.", "choix de session non compris")


PREFIXE_DOMAINE = "bfos:domaine "


def message_domaine(service, cfg, valeur="", erreur=None):
    """La question du domaine de l'organisation (cle generique)."""
    titre = _("Quel est le domaine de ton organisation ?")
    texte = _("Celui de son site Web ou de son Odoo, par exemple exemple.com.")
    source = str(erreur or "")
    erreur = _(erreur) if erreur else ""
    humain = "\n".join(([erreur] if erreur else []) + [titre, texte])
    if service in cfg["rich_services"]:
        # erreur_source : le texte francais d'origine, que l'ecran retraduit lui-meme
        # quand on change de langue pendant la question (vu au tournage du 2026-10-28).
        machine = json.dumps({"titre": titre, "texte": texte, "erreur": erreur,
                              "erreur_source": source, "valeur": str(valeur or "")[:253]},
                             separators=(",", ":"), ensure_ascii=False)
        return f"{PREFIXE_DOMAINE}{machine}\n{humain}"
    return humain


def uid_de_l_ecran(cfg):
    try:
        return pwd.getpwnam(cfg.get("greeter_user") or "greetd").pw_uid
    except KeyError:
        return None


def _organisation_en_ligne(domaine, telecharger):
    """Lue et validee ; toute panne inattendue devient une `organisation.Erreur` :
    une exception qui remonterait bouclerait l'ecran sur « Erreur interne » a
    chaque demarrage d'une cle qui a retenu ce domaine (relecture du 2026-10-27)."""
    try:
        return organisation.valider(telecharger(domaine), domaine)
    except organisation.Erreur:
        raise
    except Exception as exc:  # noqa: BLE001
        raise organisation.Erreur(N_("Ce domaine ne répond pas correctement. Vérifie-le."),
                                  f"{domaine}: {exc!r}") from exc


def choisir_organisation(canal, socle, service, telecharger=organisation.telecharger,
                         dossier=organisation.DOSSIER, retenu=organisation.RETENU,
                         drapeau=organisation.DRAPEAU_CHANGER):
    """L'organisation de cette ouverture (cle generique) : celle deja choisie
    pendant ce demarrage, sinon celle retenue sur la cle, sinon la personne tape
    son domaine et confirme le nom affiche. « Changer » a l'ecran saute les deux
    premieres. Rend {"organisation", "domaine", "live"} et la depose."""
    changer = organisation.changement_demande(drapeau, proprietaire=uid_de_l_ecran(socle),
                                              consommer=True)
    actuelle = organisation.deposee(dossier)
    if actuelle and not changer:
        return actuelle
    valeur = (actuelle or {}).get("domaine") or organisation.domaine_retenu(retenu) or ""
    erreur = None
    if not changer and valeur:
        try:
            org = _organisation_en_ligne(valeur, telecharger)
            organisation.deposer(org, dossier)
            return org
        except organisation.Erreur as exc:
            _journal(f"organisation retenue {valeur}: {exc.raison}", syslog.LOG_NOTICE)
            erreur = exc.message
    for _n in range(6):
        # La langue de l'ecran, relue avant chaque question : elle a pu changer.
        langue.choisir(langue_de_l_ecran(socle))
        reponse = canal.demander(message_domaine(service, socle, valeur, erreur))
        domaine = organisation.normaliser_domaine(reponse)
        if not domaine:
            erreur = N_("Ce n'est pas un nom de domaine. Exemple : exemple.com")
            valeur = str(reponse or "").strip()[:253]
            continue
        valeur = domaine
        try:
            org = _organisation_en_ligne(domaine, telecharger)
        except organisation.Erreur as exc:
            _journal(f"organisation {domaine}: {exc.raison}", syslog.LOG_NOTICE)
            erreur = exc.message
            continue
        langue.choisir(langue_de_l_ecran(socle))
        options = [{"id": "continuer", "titre": _("Continuer avec {nom}").format(nom=org["organisation"]),
                    "texte": _("{domaine} · connexion par {emetteur}").format(
                        domaine=domaine, emetteur=org["emetteur"])},
                   {"id": "changer", "titre": _("Changer de domaine"), "texte": ""}]
        choix = lire_choix(canal.demander(message_choix(
            service, socle, options=options, titre=_("Est-ce bien ton organisation ?"))), options)
        if choix == "continuer":
            organisation.deposer(org, dossier)
            _journal(f"organisation choisie: {domaine}")
            return org
        erreur = None
    raise Refus("Connexion annulée.", "domaine de l'organisation non obtenu")


def _dormir_sauf_changement(socle, sleep, drapeau=organisation.DRAPEAU_CHANGER):
    """Le sommeil de l'attente du QR, interrompu quand l'ecran demande « Changer
    d'organisation » : le prochain essai redemande le domaine."""
    proprietaire = uid_de_l_ecran(socle)

    def dormir(secondes):
        if organisation.changement_demande(drapeau, proprietaire=proprietaire):
            raise Refus(N_("Changement d'organisation."), "changement demande a l'ecran")
        sleep(secondes)
    return dormir


def langue_de_l_ecran(cfg, chemin=langue.LANGUE_ECRAN):
    """La langue choisie a l'ecran de connexion (fichier depose par l'usager greetd), sinon
    celle de la cle (live.json « langue »), sinon le francais."""
    try:
        proprietaire = pwd.getpwnam(cfg.get("greeter_user") or "greetd").pw_uid
    except KeyError:
        proprietaire = None
    if proprietaire is not None:
        choix = langue.lire_langue_ecran(chemin, proprietaire=proprietaire)
        if choix:
            return choix
    return langue.normaliser(cfg.get("langue")) or langue.SOURCE


def _unite(cfg, nom):
    return cfg["session_unit"].format(nom=nom)


PREFIXE_PROGRES = "bfos:progres "


def lire_progres(chemin):
    """{etape, total, texte} publie par l'unite de session, ou None."""
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if (not isinstance(d, dict) or not all(isinstance(d.get(k), int) for k in ("etape", "total"))
            or not isinstance(d.get("texte"), str) or not 1 <= d["etape"] <= d["total"] <= 20):
        return None
    return {"etape": d["etape"], "total": d["total"], "texte": d["texte"][:200]}


def _attendre_en_relayant(appel, chemin, progres, pas=0.5):
    """Lance `appel` dans un fil et, tant qu'il tourne, relaie chaque nouvelle
    etape publiee dans `chemin`. Rend son resultat ou relance son exception."""
    boite = {}

    def lancer():
        try:
            boite["res"] = appel()
        except BaseException as exc:  # noqa: BLE001 - relancee dans le fil appelant
            boite["exc"] = exc

    fil = threading.Thread(target=lancer, daemon=True)
    fil.start()
    vu = None
    while fil.is_alive():
        fil.join(pas)
        d = lire_progres(chemin)
        if d is not None and d != vu:
            vu = d
            try:
                progres(d)
            except Exception as exc:  # noqa: BLE001 - l'ecran est accessoire
                _journal(f"progression non relayee: {exc}", syslog.LOG_WARNING)
    d = lire_progres(chemin)   # une etape publiee juste avant la fin
    if d is not None and d != vu:
        try:
            progres(d)
        except Exception as exc:  # noqa: BLE001
            _journal(f"progression non relayee: {exc}", syslog.LOG_WARNING)
    if "exc" in boite:
        raise boite["exc"]
    return boite["res"]


def lancer_session(cfg, nom, sub, mode, jeton, run=subprocess.run, progres=None):
    """Depose la demande et demarre l'unite root ; rend (ok, message, resultat).
    `progres(d)` recoit chaque etape publiee par l'unite pendant l'attente."""
    base = cfg["state_dir"]
    dossier_sur(base, 0o755)
    for d in ("demandes", "resultats"):
        dossier_sur(os.path.join(base, d), 0o700)
    resultat = os.path.join(base, "resultats", nom + ".json")
    fichier_progres = os.path.join(base, "progres", nom + ".json")
    for vieux in (resultat, fichier_progres):
        try:
            os.unlink(vieux)
        except FileNotFoundError:
            pass
    _ecrire(os.path.join(base, "demandes", nom + ".json"),
            {"mode": mode, "sub": sub, "access_token": jeton, "langue": langue.courante()},
            mode=0o600)
    env = {"PATH": "/usr/sbin:/usr/bin"}
    unite = _unite(cfg, nom)
    run(["systemctl", "reset-failed", unite], capture_output=True, timeout=30, env=env)
    # restart et pas start : une unite restee active (session precedente mal
    # fermee) serait sinon un « succes » qui n'a rien prepare.
    def redemarrer():
        return run(["systemctl", "restart", unite], capture_output=True, text=True,
                   timeout=int(cfg["session_timeout"]) + 30, env=env)

    try:
        res = (_attendre_en_relayant(redemarrer, fichier_progres, progres) if progres
               else redemarrer())
    except subprocess.TimeoutExpired:
        run(["systemctl", "stop", "--no-block", unite], capture_output=True, timeout=30, env=env)
        return False, N_("La préparation de ta session a pris trop de temps."), {}
    try:
        fd = os.open(resultat, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as f:
            lu = json.load(f)
        os.unlink(resultat)
    except (OSError, ValueError):
        lu = {}
    if res.returncode == 0 and lu.get("ok"):
        return True, "", lu
    return False, lu.get("message") or N_("Ta session n'a pas pu être préparée."), lu


def poser_filet(cfg, nom, run=subprocess.run):
    """Si l'ecran ne demarre jamais la session (refus de greetd, annulation),
    aucun close_session n'arrive : un minuteur arrete alors l'unite."""
    delai = int(cfg.get("session_guard_seconds") or 0)
    if delai <= 0:
        return
    try:
        run(["systemd-run", "--quiet", "--collect", f"--on-active={delai}",
             f"--unit=bfos-live-garde-{nom}-{int(time.time())}",
             "/usr/libexec/bfos-live/session", "garder", nom],
            capture_output=True, timeout=30, env={"PATH": "/usr/sbin:/usr/bin"})
    except Exception as exc:  # noqa: BLE001 - le filet ne coute jamais la session
        _journal(f"filet de {nom} non pose: {exc}", syslog.LOG_WARNING)


def rafraichir(cfg, reponse, obtenu_a, post=None, maintenant=None):
    """Le jeton d'acces du QR, rafraichi s'il a attendu trop longtemps (la
    personne a pu rester devant la question). Rend la reponse a utiliser."""
    maintenant = time.time() if maintenant is None else maintenant
    if (maintenant - obtenu_a < int(cfg.get("token_refresh_after") or 300)
            or not reponse.get("refresh_token")):
        return reponse
    try:
        statut, brut = (post or post_https)(cfg["token_url"], {
            "grant_type": "refresh_token", "refresh_token": reponse["refresh_token"],
            "client_id": cfg["client_id"]})
        neuve = json.loads(brut) if statut == 200 else {}
    except Exception as exc:  # noqa: BLE001 - on garde l'ancien, qui vaut peut-etre encore
        _journal(f"rafraichissement du jeton echoue: {exc}", syslog.LOG_WARNING)
        return reponse
    if not neuve.get("access_token"):
        return reponse
    fusion = dict(reponse)
    fusion.update({k: v for k, v in neuve.items() if v})
    return fusion


def preparer_session(canal, cfg, service, nom, sub, jeton, run=subprocess.run,
                     rafraichi=None):
    """Choix du mode puis preparation. Un profil persistant qui echoue n'ouvre
    JAMAIS une session ephemere en silence : la personne le choisit.
    `rafraichi()` rend un jeton d'acces frais, appele apres chaque question."""
    rafraichi = rafraichi or (lambda: jeton)
    # La personne a pu changer de langue a l'ecran pendant le QR : la question la suit.
    langue.choisir(langue_de_l_ecran(cfg))
    mode = demander_mode(canal, cfg, service)
    canal.info({"persistant": _("Ouverture de ton profil chiffré… La première fois, sa création "
                                "sur la clé peut prendre quelques minutes."),
                "itinerant": _("Récupération de ton bureau dans Nextcloud… Selon sa taille et "
                               "le réseau, cela peut prendre une minute ou deux.")}
               .get(mode, _("Préparation de ta session…")))
    def progres(d):
        canal.info(PREFIXE_PROGRES + json.dumps(d, ensure_ascii=False))

    ok, message, lu = lancer_session(cfg, nom, sub, mode, rafraichi(), run=run, progres=progres)
    if ok:
        if lu.get("profil_neuf") and mode == "itinerant":
            canal.info(_("Premier bureau gardé dans ton Nextcloud : il y sera envoyé "
                       "toutes les 15 minutes et à la fermeture."))
        elif lu.get("profil_neuf"):
            canal.info(_("Nouveau profil créé sur cette clé. S'il devait déjà en "
                       "exister un, préviens ton organisation."))
        poser_filet(cfg, nom, run=run)
        return mode
    if mode in ("persistant", "itinerant"):
        repli = [CHOIX_SESSION[0], {"id": "annuler", "titre": N_("Annuler"),
                                    "texte": N_("Revenir à l'écran de connexion.")}]
        choix = demander_mode(canal, cfg, service, erreur=message, options=repli)
        if choix == "ephemere":
            canal.info(_("Préparation de ta session éphémère…"))
            ok, message, _lu = lancer_session(cfg, nom, sub, "ephemere", rafraichi(), run=run,
                                              progres=progres)
            if ok:
                poser_filet(cfg, nom, run=run)
                return "ephemere"
        else:
            raise Refus("Connexion annulée.", f"profil {mode} refuse, session annulee")
    raise Refus(message, f"preparation de la session {mode} de {nom} echouee")


def arreter_session(cfg, nom, run=subprocess.run):
    """Sans attendre : la cale PAM tue l'aide au bout de 15 s, et l'arret
    (demontage, revocation Nextcloud) se poursuit dans systemd."""
    try:
        run(["systemctl", "stop", "--no-block", _unite(cfg, nom)], capture_output=True,
            timeout=10, env={"PATH": "/usr/sbin:/usr/bin"})
    except Exception as exc:  # noqa: BLE001 - la revocation passe quand meme
        _journal(f"arret de la session {nom} echoue: {exc}", syslog.LOG_WARNING)


# ------------------------------------------------------------ les commandes
def _journal(msg, prio=syslog.LOG_INFO):
    try:
        syslog.openlog("bfos-live", syslog.LOG_PID, syslog.LOG_AUTHPRIV)
        # L'identite d'une cle generique porte un \0 (« emetteur\0sub ») :
        # syslog refuserait toute la ligne.
        syslog.syslog(prio, msg.replace("\0", " "))
    except Exception:  # noqa: BLE001
        pass


def _identite_par_qr(canal, cfg, service, post, sleep, maintenant):
    """Flux d'appareil complet ; rend (reponse du point de jeton, claims)."""
    try:
        d = bp.device_authorize(cfg["device_url"], cfg["client_id"], post=post)
    except bp.ProvisionError as exc:
        raise Refus("Impossible de joindre le service d'identité. Vérifie le réseau.",
                    f"autorisation d'appareil: {exc}") from exc
    canal.info(message_appareil(d, service, cfg))
    try:
        reponse = bp.poll_token_response(
            cfg["token_url"], cfg["client_id"], d["device_code"],
            d.get("interval", 5), d.get("expires_in", 600), post=post, sleep=sleep)
    except bp.ProvisionError as exc:
        texte = str(exc)
        if "timed out" in texte or "expired_token" in texte:
            raise Refus("Le code a expiré. Un nouveau code arrive.", texte) from exc
        if "access_denied" in texte:
            raise Refus("Connexion refusée sur le téléphone.", texte) from exc
        raise Refus("Le service d'identité ne répond plus. Recommence.", texte) from exc
    if not reponse.get("id_token"):
        raise Refus("Réponse d'identité incomplète.", "pas d'id_token (scope openid ?)")
    claims = verifier_id_token(reponse["id_token"], cfg, maintenant)
    verifier_groupes(claims, cfg)
    return reponse, claims


def auth(canal, socle, env=os.environ, post=post_https, sleep=time.sleep,
         annuaire=None, maintenant=None, run=subprocess.run,
         telecharger=organisation.telecharger, dossier_org=organisation.DOSSIER,
         verifier=None, compteur=None):
    """`socle` : le live.json de l'image (charger_socle). Sur une cle generique,
    l'organisation est choisie a l'ouverture, reprise du depot sinon."""
    service = env.get("PAM_SERVICE", "")
    demande = env.get("PAM_USER", "")
    cfg = socle

    if demande == socle["placeholder_user"]:
        situation = "ouverture"
        if os.geteuid() != 0:
            raise Refus("Erreur interne.", "ouverture sans root")
    elif NOM_VALIDE.match(demande or "") and (annuaire or Annuaire(socle))._existe(demande) is not None:
        situation = "reauthentification"
    else:
        raise Refus("Connexion impossible pour ce compte.",
                    f"PAM_USER inattendu: {demande!r} (service {service})")

    if organisation.socle_generique(socle):
        if situation == "ouverture":
            org = choisir_organisation(canal, socle, service, telecharger=telecharger,
                                       dossier=dossier_org)
            sleep = _dormir_sauf_changement(socle, sleep)
        else:
            org = organisation.deposee(dossier_org)
            if not org:
                raise Refus("Aucune organisation choisie. Redémarre la clé.",
                            "reauthentification sans organisation deposee")
        cfg = avec_organisation(socle, org)
    else:
        cfg = valider_config(socle)
    annuaire = annuaire or Annuaire(cfg)

    if (situation == "reauthentification" and service in (cfg.get("lock_services") or ())
            and par_mot_de_passe(canal, cfg, annuaire, demande, sleep=sleep,
                                 verifier=verifier or verifier_mot_de_passe, compteur=compteur)):
        _journal(f"reauthentification accordee par mot de passe: {demande} service={service}")
        canal.usager(demande)
        canal.ok()
        return demande

    reponse, claims = _identite_par_qr(canal, cfg, service, post, sleep, maintenant)

    if situation == "reauthentification":
        compte = annuaire.compte_de(demande, identite(claims, cfg))
        if compte is None:
            raise Refus("Cette session appartient à quelqu'un d'autre.",
                        f"reauthentification de {demande} par sub={claims['sub']}")
        nom = demande
        if service in (cfg.get("lock_services") or ()):
            # La personne elle-meme a approuve le QR : le mot de passe revient.
            ecrire_echecs(compteur or COMPTEUR_ECHECS.format(uid=compte.pw_uid), 0)
        try:
            # Rafraichir les jetons si on le peut (session de la personne).
            deposer_jetons(cfg, nom, compte.pw_uid, compte.pw_gid, reponse, claims)
        except (OSError, Refus):
            pass
    else:
        nom, uid, a_creer = annuaire.choisir(claims)
        if a_creer:
            annuaire.creer(nom, uid, claims)
        compte = annuaire.getpwnam(nom)
        deposer_jetons(cfg, nom, compte.pw_uid, compte.pw_gid, reponse, claims)
        obtenu_a = time.time()
        frais = {"reponse": reponse}

        def rafraichi():
            frais["reponse"] = rafraichir(cfg, frais["reponse"], obtenu_a, post=post)
            return frais["reponse"].get("access_token")
        mode = preparer_session(canal, cfg, service, nom, identite(claims, cfg),
                                reponse.get("access_token"), run=run, rafraichi=rafraichi)
        if frais["reponse"] is not reponse:
            deposer_jetons(cfg, nom, compte.pw_uid, compte.pw_gid, frais["reponse"], claims)
        situation = f"ouverture {mode}"
        if cfg.get("organisation"):
            # « Changer » clique trop tard (QR deja approuve) : sans effet, a oublier.
            organisation.changement_demande(proprietaire=uid_de_l_ecran(cfg), consommer=True)

    _journal(f"{situation} accordee: {nom} iss={claims.get('iss')} sub={claims['sub']} service={service}")
    canal.usager(nom)
    canal.ok()
    return nom


def close(cfg, nom, post=None, run=subprocess.run):
    """Revoque les jetons de la session (RFC 7009) et les efface, PUIS demande
    l'arret de l'unite root (profil, Nextcloud). Dans cet ordre : la cale PAM
    tue l'aide au bout de 15 s, et la revocation doit y tenir ; l'arret se
    poursuit dans systemd. Delais courts : sans reseau, la deconnexion ne doit
    pas se figer."""
    if not nom or not NOM_VALIDE.match(nom):
        return
    try:
        _revoquer_jetons(cfg, nom, post)
    finally:
        # Meme sans jetons a revoquer (fichier absent ou illisible) : le profil
        # se referme toujours.
        if os.geteuid() == 0:
            arreter_session(cfg, nom, run=run)
    _journal(f"session fermee: {nom}")


def _revoquer_jetons(cfg, nom, post=None):
    if post is None:
        socket.setdefaulttimeout(DELAI_REVOCATION)  # la resolution DNS aussi

        def post(url, data):
            return post_https(url, data, timeout=DELAI_REVOCATION)
    chemin = os.path.join(cfg["state_dir"], "sessions", nom + ".json")
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="utf-8") as f:
            jetons = json.load(f)
    except (OSError, ValueError):
        return
    if cfg.get("revoke_url"):
        for type_ in ("refresh_token", "access_token"):
            if jetons.get(type_):
                try:
                    post(cfg["revoke_url"], {"token": jetons[type_], "token_type_hint": type_,
                                             "client_id": cfg["client_id"]})
                except Exception as exc:  # noqa: BLE001 - l'effacement passe quand meme
                    _journal(f"revocation {type_} de {nom} echouee: {exc}", syslog.LOG_WARNING)
    try:
        os.unlink(chemin)
    except OSError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ""
    canal = Canal()
    cfg = {}
    try:
        cfg = charger_socle(CONFIG)
        langue.choisir(langue_de_l_ecran(cfg))
        if mode == "auth":
            auth(canal, cfg)
            return 0
        if mode == "close":
            close(charger_config(CONFIG), os.environ.get("PAM_USER", ""))
            return 0
        raise Refus("Erreur interne.", f"mode inconnu {mode!r}")
    except Refus as exc:
        _journal(f"refus: {exc.raison}", syslog.LOG_NOTICE)
        if mode == "auth":
            # La langue de l'ecran a pu changer pendant le QR (ou la config manque) : relue ici.
            langue.choisir(langue_de_l_ecran(cfg))
            canal.erreur(_(exc.message))
            canal.echec(exc.raison)
        return 1
    except Exception as exc:  # noqa: BLE001 - jamais de pile dans l'ecran de connexion
        _journal(f"erreur interne: {exc!r}", syslog.LOG_ERR)
        if mode == "auth":
            langue.choisir(langue_de_l_ecran(cfg))
            canal.erreur(_("Erreur interne. Recommence."))
            canal.echec(f"erreur interne: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
