"""Profil itinerant dans Nextcloud (#26870, 2026-10-24).

Troisieme facon d'ouvrir une session sur la cle live, a cote de l'ephemere et
du profil garde sur la cle USB : le dossier personnel vit, chiffre, dans le
Nextcloud de la personne (`.bfos-live/profil.bfosnc`) et se retrouve sur
n'importe quelle cle, n'importe quel PC.

- Ouverture : verrou, telechargement en memoire vive, dechiffrement COMPLET et
  verifie, puis `tar -x` en tant que la personne dans sa maison (RAM).
- Sauvegarde : `tar` en tant que la personne (caches exclus), zstd,
  chiffrement, envoi sous un nom provisoire, ancienne copie gardee en
  `profil.precedent.bfosnc`, puis renommage. Toutes les 15 minutes et a la
  fermeture.
- Chiffrement `BFOSNC1` : cle du sequestre d'Odoo derivee par HKDF (sel
  aleatoire par fichier), morceaux AES-256-GCM de 1 Mio dont les donnees
  associees portent l'en-tete, le numero et le drapeau « dernier » : un
  fichier tronque, reordonne ou retouche ne se dechiffre pas.
- Verrou `verrou.json` par PUT conditionnel (If-None-Match / If-Match) : une
  seule session a la fois par personne ; un verrou sans nouvelles depuis
  30 minutes se reprend.
- Registre `jetons.bfosnc` (chiffre comme le bureau) : le mot de passe
  d'application de root y est note AVANT de servir ; une fermeture sans reseau
  le laisse, l'ouverture suivante le revoque.
- Verrous de Nextcloud : un client de synchronisation qui lit un fichier de
  `.bfos-live` le tient (423, ou 500 sur MOVE) ; on reprend. Un envoi complet
  reste orphelin quand meme (`profil.envoi-*`) : s'il est plus recent que le
  bureau, l'ouverture suivante le verifie et le met en place (#26870, essai
  du 2026-10-26 : bureau perdu ainsi).
"""
from __future__ import annotations

import base64
import email.utils
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from bfos_live.langue import _

MAGIE = b"BFOSNC1\n"
MORCEAU = 1 << 20
ETIQUETTE = 16
PLAFOND = 512 << 20               # octets chiffres au plus (la RAM d'un PC de 8 Go)
VERROU_PERIME = 1800              # secondes sans nouvelles : verrou repris
DOSSIER_NC = ".bfos-live"
PROFIL = "profil.bfosnc"
PRECEDENT = "profil.precedent.bfosnc"
PROVISOIRE = "profil.envoi.bfosnc"  # pas de « .part » : Nextcloud le refuse (400 InvalidPath)
VERROU = "verrou.json"
PAR_MORCEAUX = 400 << 20          # au-dela, envoi par morceaux (413 sinon)
# Envoi complet jamais renomme (MOVE refuse, fermeture coupee) : voir recuperer_orphelin.
ORPHELIN = re.compile(r"profil\.envoi-[A-Za-z0-9_-]+\.bfosnc")
# Fichier tenu par un client de synchronisation qui le telecharge : Nextcloud
# rend 423, ou 500 sur MOVE (LockedException non rattrapee, mesure le
# 2026-10-26, « 2 shared locks »). Le verrou tombe a la fin du telechargement.
REPRISES = (2, 4, 8, 15, 30, 30)  # secondes entre deux essais, ~90 s en tout

# Jamais dans l'archive : jetable, regenere, ou lourd sans valeur. Relatif a la
# maison. `--one-file-system` ecarte en plus tout montage (Nextcloud rclone).
EXCLUSIONS = [
    "./.cache",
    "./.local/share/Trash",
    "./.local/share/baloo",
    "./.local/share/recently-used.xbel*",
    "./.local/state/wireplumber",
    "./.var/app/*/cache",
    "./.var/app/*/.ld.so",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/*/Cache",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/*/Code Cache",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/*/GPUCache",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/*/Service Worker/CacheStorage",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/*/Service Worker/ScriptCache",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/component_crx_cache",
    "./.var/app/*/config/BraveSoftware/Brave-Browser/Singleton*",
    "./.mozilla/firefox/*/cache2",
    "./.var/app/*/.mozilla/firefox/*/cache2",
    "./.local/share/flatpak",
    # Thunderbird (#26870, arbitre le 2026-10-27) : copie locale des dossiers IMAP
    # et index de recherche, retelecharges et refaits a chaque session. Les comptes
    # (prefs.js) et le carnet restent. Le `*` de tar traverse les « / » : l'arbre
    # du profil change d'une version de Thunderbird a l'autre.
    "./.var/app/org.mozilla.thunderbird/*/ImapMail",
    "./.var/app/org.mozilla.thunderbird/*/global-messages-db.sqlite*",
    "./.thunderbird/*/ImapMail",
    "./.thunderbird/*/global-messages-db.sqlite*",
]


class Echec(Exception):
    """Refus montrable a la personne (message) et raison technique (journal)."""

    def __init__(self, message, raison=""):
        super().__init__(raison or message)
        self.message = message
        self.raison = raison or message


class TropGros(Echec):
    pass


# --------------------------------------------------------------- chiffrement
def deriver(cle, sel):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=sel,
                info=b"bfos-live/nc-profil/v1").derive(cle)


def _nonce(prefixe, n, dernier):
    return prefixe + struct.pack(">I", n) + (b"\x01" if dernier else b"\x00")


def _ad(entete, n, dernier):
    return entete + struct.pack(">I?", n, dernier)


def chiffrer(entree, sortie, cle, plafond=PLAFOND, aleatoire=os.urandom):
    """Flux clair -> flux BFOSNC1. Rend la taille ecrite ; TropGros au-dela du
    plafond (la sortie est alors a jeter)."""
    if len(cle) < 32:
        raise ValueError("cle trop courte")
    sel, prefixe = aleatoire(16), aleatoire(7)
    entete = MAGIE + sel + prefixe
    aead = AESGCM(deriver(cle, sel))
    sortie.write(entete)
    total, n = len(entete), 0
    courant = entree.read(MORCEAU)
    while True:
        suivant = entree.read(MORCEAU)
        dernier = not suivant
        bloc = aead.encrypt(_nonce(prefixe, n, dernier), courant, _ad(entete, n, dernier))
        total += len(bloc)
        if total > plafond:
            raise TropGros("Ton bureau dépasse la taille gardée dans Nextcloud.",
                           f"archive chiffree > {plafond} octets")
        sortie.write(bloc)
        if dernier:
            return total
        courant, n = suivant, n + 1


def dechiffrer(entree, sortie, cle):
    """Flux BFOSNC1 -> flux clair, tout verifie ; Echec sinon (la sortie est
    alors a jeter : un morceau peut deja y etre ecrit)."""
    entete = entree.read(len(MAGIE) + 16 + 7)
    if len(entete) != len(MAGIE) + 23 or not entete.startswith(MAGIE):
        raise Echec("Ton bureau gardé dans Nextcloud est illisible.", "en-tete BFOSNC1 absent")
    sel, prefixe = entete[len(MAGIE):len(MAGIE) + 16], entete[len(MAGIE) + 16:]
    aead = AESGCM(deriver(cle, sel))
    n = 0
    courant = entree.read(MORCEAU + ETIQUETTE)
    while True:
        suivant = entree.read(MORCEAU + ETIQUETTE)
        dernier = not suivant
        try:
            clair = aead.decrypt(_nonce(prefixe, n, dernier), courant, _ad(entete, n, dernier))
        except InvalidTag as exc:
            raise Echec("Ton bureau gardé dans Nextcloud ne s'ouvre pas avec la clé "
                        "de ton organisation.", f"morceau {n} refuse") from exc
        sortie.write(clair)
        if dernier:
            return
        courant, n = suivant, n + 1


# --------------------------------------------------------------- Nextcloud
class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OUVREUR = urllib.request.build_opener(_SansRedirection)


class Nuage:
    """WebDAV du Nextcloud de la personne, avec SON mot de passe d'application
    (depot de bfos_provision : url, login, app_password). HTTPS seulement."""

    def __init__(self, depot, ouvreur=None, delai_max=None, attendre=time.sleep):
        url = (depot or {}).get("url", "").rstrip("/")
        if not url.startswith("https://") or not depot.get("login") or not depot.get("app_password"):
            raise Echec("Nextcloud est injoignable : bureau Nextcloud indisponible.",
                        "identifiants Nextcloud absents ou non HTTPS")
        self.url, self.login = url, depot["login"]
        self._auth = "Basic " + base64.b64encode(
            f"{depot['login']}:{depot['app_password']}".encode()).decode()
        self.base = f"{url}/remote.php/dav/files/{urllib.parse.quote(self.login)}/{DOSSIER_NC}"
        self._ouvreur = ouvreur or _OUVREUR
        self.delai_max = delai_max
        self._attendre = attendre
        self.repris = False

    def adresse(self, nom):
        return f"{self.base}/{urllib.parse.quote(nom)}"

    def requete(self, methode, url, donnees=None, entetes=None, delai=120, vers=None):
        """(statut, entetes, corps). `vers` : fichier ou ecrire le corps."""
        if self.delai_max:
            delai = min(delai, self.delai_max)
        req = urllib.request.Request(url, method=methode, data=donnees)
        req.add_header("Authorization", self._auth)
        for k, v in (entetes or {}).items():
            req.add_header(k, v)
        try:
            with self._ouvreur.open(req, timeout=delai) as resp:
                if vers is not None:
                    shutil.copyfileobj(resp, vers, 1 << 20)
                    return resp.status, dict(resp.headers), b""
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), b""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Reseau coupe, delai depasse, disque plein en ecrivant `vers` :
            # toujours un refus montrable, jamais une pile.
            raise Echec("Nextcloud est injoignable : ton bureau n'a pas pu être échangé.",
                        f"{methode} {url.rsplit('/', 1)[-1]}: {exc!r}") from exc

    def requete_reprise(self, methode, url, **kw):
        """`requete`, reprise tant que Nextcloud dit le fichier verrouille
        (423 ; 500 sur COPY, MOVE et DELETE). Rend le dernier statut."""
        verrouille = (423, 500) if methode in ("COPY", "MOVE", "DELETE") else (423,)
        self.repris = False
        for pause in REPRISES + (None,):
            statut, ent, corps = self.requete(methode, url, **kw)
            if statut not in verrouille or pause is None:
                return statut, ent, corps
            self.repris = True
            self._attendre(pause)
        return statut, ent, corps  # pragma: no cover - la boucle rend toujours

    def lister(self):
        """{nom: (taille, date de modification en secondes)} du dossier."""
        corps = (b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                 b'<d:getcontentlength/><d:getlastmodified/><d:resourcetype/>'
                 b'</d:prop></d:propfind>')
        statut, _h, rendu = self.requete("PROPFIND", self.base + "/", donnees=corps, delai=60,
                                         entetes={"Depth": "1", "Content-Type": "application/xml"})
        if statut == 404:
            return {}
        if statut != 207:
            raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                        f"PROPFIND {DOSSIER_NC}: HTTP {statut}")
        ns = {"d": "DAV:"}
        fichiers = {}
        try:
            racine = ET.fromstring(rendu)
        except ET.ParseError as exc:
            raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                        f"PROPFIND {DOSSIER_NC}: XML illisible") from exc
        for r in racine.findall("d:response", ns):
            if r.find(".//d:resourcetype/d:collection", ns) is not None:
                continue
            nom = urllib.parse.unquote((r.findtext("d:href", "", ns) or "").rstrip("/").rsplit("/", 1)[-1])
            try:
                taille = int(r.findtext(".//d:getcontentlength", "0", ns) or 0)
                date = email.utils.parsedate_to_datetime(r.findtext(".//d:getlastmodified", "", ns))
            except (TypeError, ValueError):
                continue
            if nom:
                fichiers[nom] = (taille, date.timestamp())
        return fichiers

    def supprimer(self, nom, delai=30):
        """Jamais bloquant : rend le statut."""
        try:
            return self.requete_reprise("DELETE", self.adresse(nom), delai=delai)[0]
        except Echec:
            return None

    def existe(self, nom):
        statut, ent, _c = self.requete("HEAD", self.adresse(nom), delai=30)
        if statut == 404:
            return None
        if statut != 200:
            raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                        f"HEAD {nom}: HTTP {statut}")
        try:
            return int(_entete(ent, "content-length") or 0)
        except ValueError:
            return 0

    def assurer_dossier(self):
        statut, _h, _c = self.requete("MKCOL", self.base + "/")
        if statut not in (201, 405):
            raise Echec("Nextcloud refuse de garder ton bureau.", f"MKCOL {DOSSIER_NC}: HTTP {statut}")

    def telecharger(self, nom, chemin):
        """True si le fichier existe et a ete ecrit dans `chemin`."""
        with open(chemin, "wb") as f:
            statut, _h, _c = self.requete("GET", self.adresse(nom), vers=f, delai=600)
        if statut == 404:
            return False
        if statut != 200:
            raise Echec("Ton bureau gardé dans Nextcloud n'a pas pu être récupéré.",
                        f"GET {nom}: HTTP {statut}")
        return True

    def envoyer(self, chemin, nom):
        taille = os.path.getsize(chemin)
        if taille > PAR_MORCEAUX:
            return self._envoyer_par_morceaux(chemin, nom, taille)
        with open(chemin, "rb") as f:
            statut, _h, _c = self.requete("PUT", self.adresse(nom), donnees=f, delai=1800,
                                          entetes={"Content-Length": str(taille),
                                                   "Content-Type": "application/octet-stream"})
        if statut not in (200, 201, 204):
            raise Echec("Ton bureau n'a pas pu être envoyé dans Nextcloud.", f"PUT {nom}: HTTP {statut}")

    def _envoyer_par_morceaux(self, chemin, nom, taille, morceau=100 << 20):
        # Envoi par morceaux v2 : chaque PUT de morceau rend 200 sur un
        # Nextcloud adosse a S3 (reference_nc_bf_put_413_envoi_par_morceaux).
        dossier = (f"{self.url}/remote.php/dav/uploads/{urllib.parse.quote(self.login)}/"
                   f"bfos-live-{uuid.uuid4().hex}")
        ent = {"Destination": self.adresse(nom), "OC-Total-Length": str(taille)}
        statut, _h, _c = self.requete("MKCOL", dossier, entetes=ent)
        if statut != 201:
            raise Echec("Ton bureau n'a pas pu être envoyé dans Nextcloud.", f"MKCOL envoi: HTTP {statut}")
        with open(chemin, "rb") as f:
            n = 0
            while bloc := f.read(morceau):
                n += 1
                statut, _h, _c = self.requete("PUT", f"{dossier}/{n:05d}", donnees=bloc,
                                              entetes=ent, delai=900)
                if statut not in (200, 201, 204):
                    raise Echec("Ton bureau n'a pas pu être envoyé dans Nextcloud.",
                                f"morceau {n}: HTTP {statut}")
        statut, _h, _c = self.requete("MOVE", dossier + "/.file", entetes=ent, delai=1800)
        if statut not in (201, 204):
            raise Echec("Ton bureau n'a pas pu être envoyé dans Nextcloud.", f"MOVE envoi: HTTP {statut}")

    def copier(self, src, dst):
        statut, _h, _c = self.requete_reprise("COPY", self.adresse(src), delai=1800,
                                      entetes={"Destination": self.adresse(dst), "Overwrite": "T"})
        return statut

    def deplacer(self, src, dst, taille=None):
        """⚠️ Nextcloud (Sabre httpMove) EFFACE la destination AVANT de renommer :
        un MOVE refuse (fichier tenu) peut laisser `dst` absent et `src` seul."""
        statut, _h, _c = self.requete_reprise("MOVE", self.adresse(src), delai=1800,
                                              entetes={"Destination": self.adresse(dst), "Overwrite": "T"})
        if statut == 404 and self.repris and taille is not None:
            # Un 500 qui avait quand meme renomme : la reprise ne trouve plus la source.
            liste = self.lister()
            if src not in liste and liste.get(dst, (None,))[0] == taille:
                return
        if statut not in (201, 204):
            raise Echec("Ton bureau n'a pas pu être envoyé dans Nextcloud.", f"MOVE {src}: HTTP {statut}")

    # ---- verrou
    def lire_verrou(self):
        """(etag, contenu) ou (None, None). `contenu["_age_serveur"]` : secondes
        depuis sa derniere ecriture, a l'horloge du SERVEUR (Date moins
        Last-Modified), si Nextcloud les donne."""
        statut, ent, corps = self.requete("GET", self.adresse(VERROU), delai=30)
        if statut == 404:
            return None, None
        if statut != 200:
            raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                        f"GET verrou: HTTP {statut}")
        try:
            contenu = json.loads(corps.decode())
        except ValueError:
            contenu = {}
        contenu = contenu if isinstance(contenu, dict) else {}
        try:
            date = email.utils.parsedate_to_datetime(_entete(ent, "date"))
            modif = email.utils.parsedate_to_datetime(_entete(ent, "last-modified"))
            contenu["_age_serveur"] = max(0.0, (date - modif).total_seconds())
        except (TypeError, ValueError):
            pass
        return _etag(ent), contenu

    def poser_verrou(self, contenu, etag=None):
        """Pose (etag None : seulement s'il n'existe pas) ou remplace (seulement
        s'il n'a pas change). Rend le nouvel etag, ou None si un autre a gagne."""
        cond = {"If-Match": etag} if etag else {"If-None-Match": "*"}
        statut, ent, _c = self.requete_reprise("PUT", self.adresse(VERROU), delai=30,
                                       donnees=json.dumps(contenu).encode(),
                                       entetes=dict(cond, **{"Content-Type": "application/json"}))
        if statut == 412:
            return None
        if statut not in (200, 201, 204):
            raise Echec("Nextcloud refuse de garder ton bureau.", f"PUT verrou: HTTP {statut}")
        return _etag(ent) or self.lire_verrou()[0]

    def retirer_verrou(self, etag):
        self.requete_reprise("DELETE", self.adresse(VERROU), entetes={"If-Match": etag} if etag else {},
                     delai=15)


def _entete(entetes, nom):
    for k, v in (entetes or {}).items():
        if k.lower() == nom:
            return v
    return None


def _etag(entetes):
    for k, v in (entetes or {}).items():
        if k.lower() in ("oc-etag", "etag") and v:
            return v if v.startswith('"') else f'"{v}"'
    return None


# --------------------------------------------------------------- verrou de session
def prendre_verrou(nuage, session_id, maintenant=time.time, hote=None):
    """Etag du verrou pris pour cette session ; Echec si une autre session le
    tient et a donne des nouvelles depuis moins de 30 minutes."""
    nuage.assurer_dossier()
    contenu = {"session": session_id, "machine": hote or socket.gethostname(),
               "depuis": int(maintenant()), "vu": int(maintenant())}
    for _n in range(3):
        etag = nuage.poser_verrou(contenu)
        if etag:
            return etag
        actuel_etag, actuel = nuage.lire_verrou()
        if actuel_etag is None:
            continue
        # L'heure du serveur d'abord : un PC a l'horloge fausse (Windows a
        # l'heure locale, NTP bloque) reprendrait sinon un verrou bien vivant.
        age = actuel.get("_age_serveur")
        if age is None:
            age = maintenant() - float(actuel.get("vu") or 0)
        if actuel.get("session") != session_id and age < VERROU_PERIME:
            depuis = time.strftime("%H:%M", time.localtime(float(actuel.get("depuis") or 0)))
            # Traduit ici : le gabarit porte une heure, que le catalogue ne connaitrait pas.
            raise Echec(_("Ton bureau Nextcloud est déjà ouvert sur un autre poste (depuis {depuis}). "
                          "Ferme cette session, ou choisis une session éphémère.").format(depuis=depuis),
                        f"verrou tenu par {actuel.get('session')} ({int(age)} s)")
        etag = nuage.poser_verrou(contenu, etag=actuel_etag)
        if etag:
            return etag
    raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.", "verrou dispute")


def renouveler_verrou(nuage, session_id, etag, maintenant=time.time):
    """Nouvel etag ; Echec si le verrou n'est plus a cette session."""
    actuel_etag, actuel = nuage.lire_verrou()
    if actuel_etag is None or (actuel or {}).get("session") != session_id:
        raise Echec("Ton bureau Nextcloud a été ouvert ailleurs : il n'est plus sauvegardé ici.",
                    "verrou perdu")
    actuel = {k: v for k, v in actuel.items() if not k.startswith("_")}
    actuel["vu"] = int(maintenant())
    nouveau = nuage.poser_verrou(actuel, etag=actuel_etag)
    if not nouveau:
        raise Echec("Ton bureau Nextcloud a été ouvert ailleurs : il n'est plus sauvegardé ici.",
                    "verrou perdu (course)")
    return nouveau


# --------------------------------------------------------------- mots de passe de root
JETONS = "jetons.bfosnc"          # mots de passe de root pas encore revoques (chiffres)
JETONS_MAX = 50


def lire_jetons(nuage, cle):
    """(etag, depots notes par les sessions precedentes), (None, []) s'il n'y
    en a pas. Illisible (autre cle) : liste vide, rien n'est bloque."""
    statut, ent, corps = nuage.requete("GET", nuage.adresse(JETONS), delai=30)
    if statut == 404:
        return None, []
    if statut != 200:
        raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                    f"GET jetons: HTTP {statut}")
    clair = io.BytesIO()
    try:
        dechiffrer(io.BytesIO(corps), clair, cle)
        liste = json.loads(clair.getvalue().decode())
    except (Echec, ValueError):
        liste = []
    if not isinstance(liste, list):
        liste = []
    return _etag(ent), [d for d in liste if isinstance(d, dict)
                        and str(d.get("url", "")).startswith("https://")
                        and d.get("login") and d.get("app_password")]


def ecrire_jetons(nuage, cle, depots, etag):
    """Remplace le registre s'il n'a pas change depuis sa lecture (etag None :
    s'il n'existe pas). False si un autre ecrivain est passe entre-temps."""
    sortie = io.BytesIO()
    garder = [{k: d[k] for k in ("url", "login", "app_password")} for d in depots][-JETONS_MAX:]
    chiffrer(io.BytesIO(json.dumps(garder).encode()), sortie, cle)
    cond = {"If-Match": etag} if etag else {"If-None-Match": "*"}
    statut, _h, _c = nuage.requete_reprise("PUT", nuage.adresse(JETONS), donnees=sortie.getvalue(), delai=30,
                                   entetes=dict(cond, **{"Content-Type": "application/octet-stream"}))
    if statut == 412:
        return False
    if statut not in (200, 201, 204):
        raise Echec("Nextcloud refuse de garder ton bureau.", f"PUT jetons: HTTP {statut}")
    return True


def modifier_jetons(nuage, cle, changer):
    """Lecture, `changer(liste)`, ecriture conditionnelle ; recommence si un
    autre ecrivain est passe (aucune mise a jour perdue, verrou ou pas)."""
    for _n in range(5):
        etag, liste = lire_jetons(nuage, cle)
        nouvelle = changer(liste)
        if ecrire_jetons(nuage, cle, nouvelle, etag):
            return nouvelle
    raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.", "registre dispute")


# --------------------------------------------------------------- l'archive
MARQUEUR = "bureau.json"          # pose au premier envoi : un bureau existe
RETRECI = 0.10                    # archive < 10 % du bureau ouvert : pas envoyee
RETRECI_MIN = 1 << 20             # ... si le bureau ouvert depassait 1 Mio


class Retreci(Echec):
    pass


def _comme(compte):
    """Options de Popen pour lancer un outil SOUS le compte de la personne
    (root seulement), sans runuser : un echec de runuser rendrait 1, pris pour
    « un fichier a change »."""
    if compte.pw_uid == os.getuid():
        return {"cwd": "/"}
    return {"user": compte.pw_uid, "group": compte.pw_gid, "extra_groups": [], "cwd": "/"}


def _tar_creer(maison):
    # Ni --exclude-caches ni -all : un CACHEDIR.TAG pose a la racine de la
    # maison viderait l'archive. --one-file-system ecarte tout montage
    # (Nextcloud par rclone) ; un dossier « Nextcloud » LOCAL (montage tombe)
    # est garde, sans quoi ce qui y a ete enregistre serait perdu.
    argv = ["tar", "--create", "--file=-", "--one-file-system",
            "--warning=no-file-removed", "--warning=no-file-ignored", "-C", maison]
    argv += [f"--exclude={e}" for e in EXCLUSIONS]
    return argv + ["."]


NON_ENVOYE = ".bfos-live-non-envoye"   # pose (root) quand la derniere sauvegarde a echoue


def marquer_non_envoye(maison, compte):
    """Pose le marqueur (root, sans suivre de lien) : la derniere sauvegarde a
    echoue, la maison en RAM tient du travail jamais envoye."""
    try:
        fd = os.open(os.path.join(maison, NON_ENVOYE),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    except FileExistsError:
        pass


def mettre_de_cote(maison, compte=None, run=subprocess.run, maintenant=time.time):
    """Travail d'une session precedente du meme demarrage, jamais envoye
    (derniere sauvegarde ratee) : deplace dans « Bureau non envoyé <date> »,
    hors de ce que la restauration efface. Rend le chemin, ou None."""
    marque = os.path.join(maison, NON_ENVOYE)
    if not os.path.lexists(marque):
        return None
    os.unlink(marque)
    garde = os.path.join(maison, time.strftime("Bureau non envoyé %Y-%m-%d %H%M",
                                               time.localtime(maintenant())))
    os.mkdir(garde, 0o700)
    if compte is not None:
        os.chown(garde, compte.pw_uid, compte.pw_gid)
    for entree in os.listdir(maison):
        chemin = os.path.join(maison, entree)
        if chemin == garde or os.path.ismount(chemin):
            continue
        os.rename(chemin, os.path.join(garde, entree))
    return garde


def vider_maison(maison, run=subprocess.run):
    """Vide la maison SANS traverser de montage (-xdev) : un `rm -r` dans un
    Nextcloud monte effacerait les fichiers du nuage. Garde « Bureau non
    envoyé … » (voir mettre_de_cote)."""
    run(["find", maison, "-xdev", "-mindepth", "1", "-maxdepth", "1",
         "!", "-name", "Bureau non envoyé *", "-exec", "rm", "-rf", "--one-file-system", "{}", "+"],
        capture_output=True,
        timeout=600, env={"PATH": "/usr/sbin:/usr/bin"})


class _Puits:
    def write(self, _octets):
        pass


def recuperer_orphelin(nuage, cle, tmp, statvfs=os.statvfs):
    """Un envoi complet reste orphelin quand son renommage a echoue (fichier
    tenu par un client de synchronisation, fermeture coupee). Le plus recent,
    s'il l'est plus que le bureau, EST le dernier etat de la personne : on le
    verifie (dechiffrement complet), on garde l'ancien bureau en `precedent`,
    puis on le met en place. Un orphelin plus ancien que le bureau, ou
    illisible, n'est jamais efface ici : il peut tenir le seul exemplaire d'un
    travail. Rend le nom recupere, ou None."""
    fichiers = nuage.lister()
    orphelins = sorted(((m, n) for n, (_t, m) in fichiers.items() if ORPHELIN.fullmatch(n)),
                       reverse=True)
    if not orphelins:
        return None
    date, nom = orphelins[0]
    # Sans bureau, le precedent fait foi : COPY garde la date de la source
    # (mesure sur Nextcloud le 2026-10-26), il date donc le bureau qu'il copie.
    # Un vieil orphelin ne remplace jamais un etat plus recent.
    reference = fichiers.get(PROFIL) or fichiers.get(PRECEDENT)
    if reference and reference[1] >= date:
        return None
    v = statvfs(tmp)
    if v.f_bavail * v.f_frsize < fichiers[nom][0] + (64 << 20):
        raise Echec("Pas assez de mémoire libre sur ce PC pour ouvrir ton bureau Nextcloud.",
                    f"{tmp}: {v.f_bavail * v.f_frsize} octets libres pour {nom}")
    dossier = tempfile.mkdtemp(prefix="bfos-live-itinerant-", dir=tmp)
    try:
        chemin = os.path.join(dossier, "orphelin.bfosnc")
        if not nuage.telecharger(nom, chemin):
            return None
        try:
            with open(chemin, "rb") as e:
                dechiffrer(e, _Puits(), cle)
        except Echec:
            return None
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
    if PROFIL in fichiers:
        statut = nuage.copier(PROFIL, PRECEDENT)
        if statut not in (201, 204):
            raise Echec("Nextcloud ne répond pas : bureau Nextcloud indisponible.",
                        f"COPY precedent avant {nom}: HTTP {statut}")
    nuage.deplacer(nom, PROFIL, taille=fichiers[nom][0])
    if MARQUEUR not in fichiers:
        # Premier bureau arrive par un orphelin : sans marqueur, un bureau qui
        # disparaitrait ensuite passerait pour un bureau neuf.
        nuage.requete_reprise("PUT", nuage.adresse(MARQUEUR), donnees=json.dumps(
            {"cree": int(time.time())}).encode(), entetes={"Content-Type": "application/json"},
            delai=30)
    return nom


def etat_du_nuage(nuage):
    """(taille du bureau ou None, traces) : traces = un precedent, un envoi en
    cours ou le marqueur d'un bureau deja garde."""
    taille = nuage.existe(PROFIL)
    traces = taille is None and any(nuage.existe(n) is not None for n in (PRECEDENT, MARQUEUR))
    return taille, traces


def restaurer(nuage, compte, maison, cle, tmp, popen=subprocess.Popen, run=subprocess.run,
              statvfs=os.statvfs):
    """Taille du bureau restaure, ou 0 s'il n'y en a pas encore. Echec si un
    bureau a existe mais qu'il manque (404 passager, miroir de secours) :
    on ne repart JAMAIS d'une maison vide qui l'ecraserait."""
    recuperer_orphelin(nuage, cle, tmp, statvfs=statvfs)
    taille, traces = etat_du_nuage(nuage)
    if taille is None:
        if traces:
            raise Echec("Ton bureau Nextcloud est introuvable pour le moment. Réessaie plus "
                        "tard, ou choisis une session éphémère.",
                        "profil absent mais precedent ou marqueur presents")
        return 0
    v = statvfs(tmp)
    if v.f_bavail * v.f_frsize < taille + (64 << 20):
        raise Echec("Pas assez de mémoire libre sur ce PC pour ouvrir ton bureau Nextcloud.",
                    f"{tmp}: {v.f_bavail * v.f_frsize} octets libres pour {taille}")
    dossier = tempfile.mkdtemp(prefix="bfos-live-itinerant-", dir=tmp)
    chiffre = os.path.join(dossier, PROFIL)
    try:
        if not nuage.telecharger(PROFIL, chiffre):
            raise Echec("Ton bureau Nextcloud est introuvable pour le moment. Réessaie plus "
                        "tard, ou choisis une session éphémère.", "profil disparu entre HEAD et GET")
        taille = os.path.getsize(chiffre) or taille   # HEAD sans Content-Length
        mettre_de_cote(maison, compte, run=run)
        vider_maison(maison, run=run)
        # Clair en flux, jamais sur disque : dechiffrement (root) -> tar
        # (la personne) qui lit son entree standard. Ses erreurs vont dans un
        # FICHIER : un tube de 64 Kio plein (« No space left » par fichier)
        # figerait tout.
        with open(os.path.join(dossier, "tar.err"), "wb") as err:
            tar = popen(["tar", "--extract", "--zstd", "--file=-", "--no-same-owner", "-C", maison],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err,
                        **_comme(compte))
            try:
                with open(chiffre, "rb") as e:
                    dechiffrer(e, tar.stdin, cle)
            except BaseException:
                tar.kill()
                raise
            finally:
                try:
                    tar.stdin.close()
                except OSError:
                    pass
                rc = tar.wait()
        if rc != 0:
            with open(os.path.join(dossier, "tar.err"), encoding="utf-8", errors="replace") as f:
                raise Echec("Ton bureau gardé dans Nextcloud n'a pas pu être remis en place.",
                            f"tar -x: {rc} {f.read().strip()[:300]}")
        return taille
    finally:
        shutil.rmtree(dossier, ignore_errors=True)


def _changements_seulement(err):
    lignes = [l for l in err.splitlines() if l.strip()]
    return all("file changed as we read it" in l for l in lignes)


def sauvegarder(nuage, compte, maison, cle, tmp, session_id, taille_ouverte=0, neuf=False,
                copier_precedent=True, popen=subprocess.Popen, plafond=PLAFOND):
    """Envoie le bureau ; rend la taille chiffree.
    - TropGros / Retreci : rien n'est envoye, le bureau dans Nextcloud reste ;
    - `copier_precedent` : une fois par session, le bureau TEL QU'OUVERT est
      garde en `precedent` (deux mauvaises sauvegardes ne le perdent pas) ;
    - un bureau ouvert (pas neuf) qui a disparu du nuage : refus."""
    dossier = tempfile.mkdtemp(prefix="bfos-live-itinerant-", dir=tmp)
    chiffre, journal_tar = os.path.join(dossier, "envoi.bfosnc"), os.path.join(dossier, "tar.err")
    envoi = f"profil.envoi-{session_id}.bfosnc"
    try:
        with open(journal_tar, "wb") as err:
            tar = popen(_tar_creer(maison), stdout=subprocess.PIPE, stderr=err, **_comme(compte))
            zstd = popen(["zstd", "-q", "-T0", "-3", "-c"], stdin=tar.stdout, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
            tar.stdout.close()
            try:
                with open(chiffre, "wb") as s:
                    taille = chiffrer(zstd.stdout, s, cle, plafond=plafond)
            finally:
                zstd.stdout.close()
                rz, rt = zstd.wait(), tar.wait()
        with open(journal_tar, encoding="utf-8", errors="replace") as f:
            erreurs = f.read()
        # tar : 1 = un fichier a change pendant la lecture (session ouverte),
        # et RIEN d'autre ; tout autre message est un vrai echec.
        if rz != 0 or rt not in (0, 1) or (rt == 1 and not _changements_seulement(erreurs)):
            raise Echec("Ton bureau n'a pas pu être préparé pour Nextcloud.",
                        f"tar={rt} zstd={rz} {erreurs.strip()[:300]}")
        if taille_ouverte > RETRECI_MIN and taille < taille_ouverte * RETRECI:
            raise Retreci("Ton bureau a fondu d'un coup : par prudence, il n'est pas envoyé dans "
                          "Nextcloud. Préviens ton organisation si ce n'est pas voulu.",
                          f"archive {taille} octets pour {taille_ouverte} a l'ouverture")
        # Notre envoi deja la : un renommage de CETTE session a echoue, et
        # Nextcloud a pu effacer le bureau avant de le refuser (voir deplacer).
        # Le bureau absent, c'est alors lui, pas une disparition. Une
        # disparition venue d'ailleurs apres une sauvegarde refusee est donc
        # remplacee aussi : sans perte (verrou renouvele avant chaque envoi,
        # aucune autre session n'a ecrit ; le precedent reste).
        rate = not neuf and nuage.existe(envoi) is not None
        nuage.envoyer(chiffre, envoi)
        if copier_precedent:
            statut = nuage.copier(PROFIL, PRECEDENT)
            if statut == 404 and not neuf and not rate:
                nuage.supprimer(envoi)
                raise Echec("Ton bureau Nextcloud a disparu du nuage : il n'est pas remplacé.",
                            "profil absent au moment de garder le precedent")
            if statut not in (201, 204, 404):
                raise Echec("Ton bureau n'a pas pu être envoyé dans Nextcloud.",
                            f"COPY precedent: HTTP {statut}")
        elif not neuf and not rate and nuage.existe(PROFIL) is None:
            nuage.supprimer(envoi)
            raise Echec("Ton bureau Nextcloud a disparu du nuage : il n'est pas remplacé.",
                        "profil absent avant le remplacement")
        # Renommage refuse : l'envoi RESTE, jamais efface. Le bureau a pu etre
        # efface par Nextcloud ; l'envoi chiffre est alors le seul dernier
        # etat, repris par la sauvegarde suivante ou a l'ouverture.
        nuage.deplacer(envoi, PROFIL, taille=os.path.getsize(chiffre))
        if neuf:
            nuage.requete("PUT", nuage.adresse(MARQUEUR), donnees=json.dumps(
                {"cree": int(time.time())}).encode(), entetes={"Content-Type": "application/json"},
                delai=30)
        return taille
    finally:
        shutil.rmtree(dossier, ignore_errors=True)
