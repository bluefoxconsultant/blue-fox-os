#!/usr/bin/env python3
"""Amorce du zero-touch : demander le domaine de l'organisation, puis enchainer
sur le kickstart que cette organisation sert.

POURQUOI CE FICHIER EXISTE
--------------------------
C'est le domaine qui decide d'ou vient le kickstart, et c'est ce kickstart,
servi par l'Odoo de l'organisation (bf_zerotouch_install), qui affiche le code
QR. La question du domaine doit donc etre posee AVANT de le telecharger.

GRUB ne pouvait pas la poser : `read` n'existe pas dans le grub UEFI de l'ISO
(#23940), et le domaine a fini en dur dans le menu. Une autre organisation que
Blue Fox devait passer par « e » au menu, ce qu'on ne demande pas a un client.

L'entree zero-touch demarre donc sur un petit kickstart embarque dans l'ISO
(bfos-amorce.ks), dont le seul bloc pre lance ce script. Il :
  1. demande le domaine sur une console virtuelle a lui, pre-rempli ;
  2. le verifie POUR DE VRAI : https://<domaine>/blue-fox-install.ks doit
     repondre 200 avec l'en-tete x-bf-zerotouch-version ;
  3. affiche le nom de l'organisation lu dans ce kickstart et attend une
     confirmation ;
  4. execute lui-meme les blocs pre de ce kickstart, puis depose le reste en
     /tmp/bfos-locataire.ks, que l'amorce inclut.

⚠️ POURQUOI LES BLOCS PRE SONT EXECUTES ICI, ET RETIRES DU FICHIER INCLUS
-------------------------------------------------------------------------
Anaconda lit le kickstart en deux passes (pyanaconda/kickstart.py).
preScriptPass ne cherche QUE les blocs pre, ignore un %include encore absent
(missingIncludeIsFatal=False), puis les execute. parseKickstart relit ensuite
tout, fichiers inclus compris. Un bloc pre qui n'existe que dans le fichier
inclus arrive donc trop tard : la premiere passe est finie quand le fichier
apparait. D'ou l'execution ici, dans l'ordre et avec les options d'Anaconda.

Et on les RETIRE du fichier inclus plutot que de compter sur la seconde passe
pour ne pas les rejouer : un second flux d'appareil au milieu d'une
installation serait la pire surprise possible, et rien ne coute de l'exclure.

Parametres noyau (a ajouter par « e » au menu) :
    bfos.domaine=<domaine>  pas de question ; le domaine est verifie quand meme.
    bfos.ks=<url>           ni question ni en-tete exige : ce kickstart-la.
                            Pour les essais sur registre local et le depannage.

Tout ce qui touche la console, le reseau et les processus est injectable.
"""
import argparse
import fcntl
import os
import re
import shlex
import socket
import ssl
import struct
import subprocess
import sys
import termios
import textwrap
import time
import urllib.error
import urllib.request

KS_LOCATAIRE = "bfos-locataire.ks"
CHEMIN_KS = "/blue-fox-install.ks"
EN_TETE_VERSION = "x-bf-zerotouch-version"
CMDLINE = "/proc/cmdline"
CONSOLE = "/dev/console"
# Le kickstart servi pese ~130 Kio. Au-dela de 4 Mio, ce n'en est pas un.
TAILLE_MAX = 4 * 1024 * 1024
_DELAI = 20  # secondes par requete
# Sans question a poser (bfos.domaine, bfos.ks), personne n'est la pour
# relancer : on laisse au reseau le temps de venir, puis on insiste un peu.
_PATIENCE_RESEAU = 180
_REPRISES = 6
_PAUSE_REPRISE = 10


class ErreurAmorce(Exception):
    """Echec a dire tel quel a l'operateur."""


# =========================================================================
# Domaine
# =========================================================================
_ETIQUETTE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normaliser_domaine(brut):
    """Ce qu'on a tape -> un nom d'hote, ou None s'il n'en est pas un.

    Tolere ce qu'on colle depuis un navigateur (schema, chemin, point final,
    majuscules) et les domaines accentues. Refuse le reste plutot que de
    deviner : un domaine devine, c'est une machine enrolee ailleurs.
    """
    if not brut:
        return None
    s = brut.strip()
    for prefixe in ("https://", "http://"):
        if s.lower().startswith(prefixe):
            s = s[len(prefixe):]
            break
    s = s.split("/", 1)[0].rstrip(".")
    if not s or any(c in s for c in ":@ \t"):
        return None
    try:
        s = s.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if len(s) > 253:
        return None
    etiquettes = s.split(".")
    if len(etiquettes) < 2 or not all(_ETIQUETTE.match(e) for e in etiquettes):
        return None
    # Une adresse IP n'a pas de certificat a son nom : la verification https
    # echouerait de toute facon, autant le dire tout de suite.
    if etiquettes[-1].isdigit():
        return None
    return s


def lire_parametres(texte):
    """Les parametres bfos.* de la ligne de commande du noyau."""
    params = {}
    for jeton in texte.split():
        if jeton.startswith("bfos.") and "=" in jeton:
            cle, _, valeur = jeton.partition("=")
            params[cle] = valeur.strip('"')
    return params


# =========================================================================
# Telechargement et verification du kickstart de l'organisation
# =========================================================================
class _RedirectionHttps(urllib.request.HTTPRedirectHandler):
    """Ne suivre une redirection que vers https. Un portail captif qui
    renverrait vers http servirait sinon le kickstart de son choix, et ce
    kickstart s'execute en root avant le partitionnement."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise ErreurAmorce(f"Redirection refusee vers {newurl} (https exige).")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def telecharger(url, timeout=_DELAI):
    """GET -> (statut, en-tetes en minuscules, texte)."""
    ouvreur = urllib.request.build_opener(_RedirectionHttps())
    req = urllib.request.Request(url, headers={
        "Accept": "text/plain", "User-Agent": "blue-fox-os-amorce"})
    with ouvreur.open(req, timeout=timeout) as rep:
        brut = rep.read(TAILLE_MAX + 1)
        en_tetes = {k.lower(): v for k, v in rep.headers.items()}
        statut = rep.status
    if len(brut) > TAILLE_MAX:
        raise ErreurAmorce(f"La reponse de {url} depasse {TAILLE_MAX // 1024 // 1024} Mio : "
                           "ce n'est pas un kickstart.")
    return statut, en_tetes, brut.decode("utf-8")


_ORGANISATION = re.compile(r"^#\s*Tenant:\s*(.+?)\s*$", re.M)
_OSTREE = re.compile(r"^ostreecontainer\s", re.M)


def _lisible(texte, longueur=60):
    """Texte venu d'ailleurs, rendu sur si on l'affiche : sans caractere de
    controle (donc sans sequence d'echappement qui repeindrait l'ecran)."""
    propre = "".join(c for c in texte if c.isprintable())
    return propre[:longueur]


def verifier_kickstart(texte, en_tetes, exiger_en_tete=True, cible="?"):
    """Rend le nom de l'organisation ; leve ErreurAmorce si ce n'est pas un
    kickstart Blue Fox OS utilisable."""
    if exiger_en_tete and EN_TETE_VERSION not in en_tetes:
        raise ErreurAmorce(f"{cible} repond, mais ne sert pas Blue Fox OS.")
    if not _OSTREE.search(texte):
        raise ErreurAmorce(f"Le kickstart de {cible} n'installe aucune image.")
    separer_pre(texte)  # un kickstart mal forme doit echouer ICI, pas apres confirmation
    m = _ORGANISATION.search(texte)
    return _lisible(m.group(1)) if m else cible


def verifier_domaine(domaine, telecharger_=telecharger):
    """-> (organisation, texte du kickstart)."""
    statut, en_tetes, texte = telecharger_(f"https://{domaine}{CHEMIN_KS}")
    if statut != 200:
        raise ErreurAmorce(f"{domaine} a repondu {statut} au lieu du kickstart.")
    return verifier_kickstart(texte, en_tetes, exiger_en_tete=True, cible=domaine), texte


def expliquer(exc, cible):
    """Une panne -> une phrase pour l'operateur."""
    if isinstance(exc, ErreurAmorce):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return f"{cible} ne sert pas Blue Fox OS (reponse 404)."
        return f"{cible} a repondu par une erreur HTTP {exc.code}."
    if isinstance(exc, UnicodeDecodeError):
        return f"{cible} ne sert pas un kickstart lisible."
    raison = getattr(exc, "reason", exc)
    if isinstance(raison, ssl.SSLCertVerificationError):
        return f"Le certificat de {cible} n'est pas valide."
    if isinstance(raison, ssl.SSLError):
        return f"Connexion securisee impossible avec {cible}."
    if isinstance(raison, socket.gaierror):
        if raison.errno == socket.EAI_NONAME:
            return f"Domaine introuvable : {cible}. Verifiez l'orthographe."
        return "Aucun serveur DNS ne repond. Verifiez le branchement reseau."
    if isinstance(raison, TimeoutError):
        return f"Pas de reponse de {cible}."
    if isinstance(raison, ConnectionRefusedError):
        return f"{cible} refuse la connexion."
    return f"Verification de {cible} impossible : {_lisible(str(raison), 120)}"


def _transitoire(exc):
    """Vaut-il la peine de reessayer sans rien changer ?"""
    if isinstance(exc, (ErreurAmorce, urllib.error.HTTPError, UnicodeDecodeError)):
        return False
    raison = getattr(exc, "reason", exc)
    if isinstance(raison, ssl.SSLCertVerificationError):
        return False
    if isinstance(raison, socket.gaierror) and raison.errno == socket.EAI_NONAME:
        return False
    return isinstance(exc, OSError)


def _avec_reprises(appel, cible, journal, sleep=time.sleep):
    for essai in range(1, _REPRISES + 1):
        try:
            return appel()
        except Exception as exc:  # noqa: BLE001
            if not _transitoire(exc) or essai == _REPRISES:
                raise ErreurAmorce(expliquer(exc, cible)) from exc
            journal(f"{expliquer(exc, cible)} Nouvel essai ({essai}/{_REPRISES})...")
            sleep(_PAUSE_REPRISE)
    raise AssertionError("inatteignable")


# =========================================================================
# Reseau
# =========================================================================
def reseau_pret(route4="/proc/net/route", route6="/proc/net/ipv6_route"):
    """Une route par defaut existe-t-elle ? C'est ce que DHCP pose en dernier."""
    try:
        with open(route4, encoding="ascii", errors="replace") as fh:
            next(fh, None)
            for ligne in fh:
                champs = ligne.split()
                if (len(champs) > 3 and champs[1] == "00000000"
                        and int(champs[3], 16) & 0x1):
                    return True
    except (OSError, ValueError):
        pass
    try:
        with open(route6, encoding="ascii", errors="replace") as fh:
            for ligne in fh:
                champs = ligne.split()
                # La route par defaut « unreachable » du noyau passe par lo.
                if (len(champs) == 10 and champs[0] == "0" * 32
                        and champs[1] == "00" and champs[9] != "lo"):
                    return True
    except OSError:
        pass
    return False


def attendre_reseau(annoncer, pret=reseau_pret, sleep=time.sleep,
                    now=time.monotonic, patience=None):
    """Attend une route par defaut. Rend False si la patience est epuisee."""
    debut = now()
    dit = set()
    while not pret():
        ecoule = now() - debut
        if patience is not None and ecoule >= patience:
            return False
        if ecoule >= 3 and "connexion" not in dit:
            annoncer("Connexion au reseau...")
            dit.add("connexion")
        if ecoule >= 60 and "cable" not in dit:
            annoncer("Toujours aucun reseau. Branchez un cable reseau : "
                     "l'installation continuera d'elle-meme.")
            dit.add("cable")
        sleep(1)
    return True


# =========================================================================
# Kickstart de l'organisation : blocs pre et fichier inclus
# =========================================================================
_DEBUT_PRE = re.compile(r"^%pre(\s|$)")
_FIN_SECTION = re.compile(r"^%end(\s|$)")
_AUTRE_SECTION = re.compile(
    r"^%(pre-install|post|packages|onerror|traceback|addon|certificate)(\s|$)")


def separer_pre(texte):
    """-> (blocs, reste).

    blocs : [(ligne d'en-tete, corps, numero de ligne)], dans l'ordre.
    reste : le kickstart sans ses blocs pre, chacun remplace par un
    commentaire qui dit ou il etait.
    """
    blocs, reste, courant = [], [], None
    for numero, ligne in enumerate(texte.splitlines(keepends=True), 1):
        brute = ligne.rstrip("\r\n")
        if courant is None:
            if _DEBUT_PRE.match(brute):
                courant = (brute, [], numero)
                reste.append(f"# bloc pre de la ligne {numero} : execute par l'amorce\n")
            else:
                reste.append(ligne)
            continue
        if _FIN_SECTION.match(brute):
            blocs.append((courant[0], "".join(courant[1]), courant[2]))
            courant = None
        elif _DEBUT_PRE.match(brute) or _AUTRE_SECTION.match(brute):
            raise ErreurAmorce(f"Kickstart mal forme : le bloc pre de la ligne "
                               f"{courant[2]} n'est jamais ferme.")
        else:
            courant[1].append(ligne)
    if courant is not None:
        raise ErreurAmorce(f"Kickstart mal forme : le bloc pre de la ligne "
                           f"{courant[2]} n'est jamais ferme.")
    return blocs, "".join(reste)


def analyser_en_tete(en_tete):
    """Options d'un bloc pre, avec les valeurs par defaut de pykickstart.

    Une option inconnue est REFUSEE : l'executer sans la comprendre, ce serait
    faire autre chose que ce qu'Anaconda aurait fait, en silence.
    """
    try:
        jetons = shlex.split(en_tete)[1:]
    except ValueError as exc:
        raise ErreurAmorce(f"En-tete de bloc pre illisible : {en_tete!r}") from exc
    options = {"interp": "/bin/sh", "journal": None, "erroronfail": False}
    i = 0
    while i < len(jetons):
        jeton = jetons[i]
        cle, egal, valeur = jeton.partition("=")
        if cle in ("--interpreter", "--log", "--logfile"):
            if not egal:
                i += 1
                if i >= len(jetons):
                    raise ErreurAmorce(f"Option {cle} sans valeur dans {en_tete!r}")
                valeur = jetons[i]
            options["interp" if cle == "--interpreter" else "journal"] = valeur
        elif jeton == "--erroronfail":
            options["erroronfail"] = True
        else:
            raise ErreurAmorce(f"Option inconnue dans l'en-tete {en_tete!r} : {jeton}")
        i += 1
    return options


def ecrire_prive(chemin, contenu, mode=0o600):
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        fh = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        fh.write(contenu)


def executer_pre(blocs, journal, lancer=subprocess.run, dossier="/tmp"):
    """Execute les blocs pre comme Anaconda : interpreteur, journal, et arret
    seulement si le bloc porte --erroronfail."""
    for rang, (en_tete, corps, ligne) in enumerate(blocs, 1):
        options = analyser_en_tete(en_tete)
        script = os.path.join(dossier, f"bfos-locataire-pre-{rang}")
        ecrire_prive(script, corps)
        chemin_journal = options["journal"] or os.path.join(
            dossier, f"bfos-locataire-pre-{rang}.log")
        journal(f"bloc pre de la ligne {ligne} ({options['interp']})")
        with open(chemin_journal, "w", encoding="utf-8") as fh:
            rc = lancer([options["interp"], script], stdout=fh,
                        stderr=subprocess.STDOUT, cwd="/").returncode
        journal(f"bloc pre de la ligne {ligne} : code {rc}")
        if rc != 0 and options["erroronfail"]:
            raise ErreurAmorce(f"Le bloc pre de la ligne {ligne} a echoue "
                               f"(code {rc}) ; journal : {chemin_journal}")


# =========================================================================
# Console
# =========================================================================
VT_GETSTATE = 0x5603
VT_ACTIVATE = 0x5606
# Pas VT_WAITACTIVE : il attend sans limite, et une console tenue par un
# serveur graphique qui n'acquitte pas gelerait l'installation sans un mot.
# Avant le bloc pre, Anaconda n'a lance aucun affichage (anaconda.py), mais
# on ne parie pas une machine figee la-dessus.
_ATTENTE_BASCULE = 5.0


def _en_crlf(texte):
    """La console de l'installateur n'a pas toujours ONLCR (mesure le
    2026-09-12, voir bfos_provision) : un \\n seul y fait un escalier."""
    return texte.replace("\r\n", "\n").replace("\n", "\r\n")


class ConsoleVt:
    """Une console virtuelle a nous, en mode ligne, avec echo.

    ⚠️ Pas celle d'Anaconda : tty1 porte son tmux, qui lit le clavier. Lire au
    meme endroit partagerait les frappes entre lui et nous. On prend une
    console libre a partir de la 8 (logind reserve les six premieres a des
    getty a la demande), on l'active, et on rend la main a la fin.
    """

    def __init__(self, premiere=8):
        self._fd0 = self._fd = self._retour = None
        try:
            self._fd0 = os.open("/dev/tty0", os.O_RDWR | os.O_NOCTTY)
            etat = fcntl.ioctl(self._fd0, VT_GETSTATE, struct.pack("HHH", 0, 0, 0))
            active, _, occupees = struct.unpack("HHH", etat)
            # VT_GETSTATE ne couvre que les consoles 1 a 15 (bit n = console n).
            libre = next((n for n in range(premiere, 16)
                          if not occupees & (1 << n)), None)
            if libre is None:
                raise ErreurAmorce("Aucune console virtuelle libre.")
            self._fd = os.open(f"/dev/tty{libre}", os.O_RDWR | os.O_NOCTTY)
            attrs = termios.tcgetattr(self._fd)
            attrs[0] |= termios.ICRNL
            attrs[1] |= termios.OPOST | termios.ONLCR
            attrs[3] |= termios.ICANON | termios.ECHO | termios.ECHOE
            attrs[3] &= ~termios.ISIG
            attrs[6][termios.VERASE] = b"\x7f"
            termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
            self._retour = active
            if not self._basculer(libre):
                raise ErreurAmorce(f"La console {libre} ne s'est pas affichee.")
        except BaseException:
            self.fermer()
            raise

    def _active(self):
        etat = fcntl.ioctl(self._fd0, VT_GETSTATE, struct.pack("HHH", 0, 0, 0))
        return struct.unpack("HHH", etat)[0]

    def _basculer(self, numero):
        fcntl.ioctl(self._fd0, VT_ACTIVATE, numero)
        limite = time.monotonic() + _ATTENTE_BASCULE
        while time.monotonic() < limite:
            if self._active() == numero:
                return True
            time.sleep(0.05)
        return False

    def ecrire(self, texte):
        os.write(self._fd, _en_crlf(texte).encode("utf-8", "replace"))

    def effacer(self):
        self.ecrire("\x1b[2J\x1b[H")

    def lire_ligne(self):
        termios.tcflush(self._fd, termios.TCIFLUSH)
        lu = b""
        while not lu.endswith(b"\n"):
            morceau = os.read(self._fd, 256)
            if not morceau:
                raise ErreurAmorce("La console s'est fermee pendant la saisie.")
            lu += morceau
        return lu.decode("utf-8", "replace").strip()

    def fermer(self):
        """Rend la console d'Anaconda. Le code QR s'affiche la-bas, comme sans
        l'amorce : ce chemin-la est eprouve, on n'en change rien."""
        if self._retour and self._fd0 is not None:
            try:
                self._basculer(self._retour)
            except OSError:
                pass
            self._retour = None
        for nom in ("_fd", "_fd0"):
            fd = getattr(self, nom)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, nom, None)


# =========================================================================
# Ecrans
# =========================================================================
# Memes codes et meme renard que l'ecran d'autorisation de bfos_provision
# (un test garde la parite) : l'operateur doit reconnaitre la suite.
_LARGEUR = 78
_MARQUE = "\x1b[1;36m"
_GRAS = "\x1b[1m"
_ALERTE = "\x1b[1;31m"
_FIN = "\x1b[0m"
_RENARD = (
    r" /\     /\ ",
    r"/  \___/  \ ",
    r"\  o   o  / ",
    r" \___v___/ ",
)
INVITE_CONFIRMATION = "  Entree pour continuer, ou tapez un autre domaine : "
_RETOUR = ("n", "non", "no")


def _entete():
    titre, queue = " BLUE FOX OS ", " organisation "
    return _MARQUE + titre + _FIN + "-" * (_LARGEUR - len(titre) - len(queue)) + queue


def _renard(sous_titre):
    lignes = [_MARQUE + r + _FIN for r in _RENARD]
    lignes[1] += _GRAS + "   BLUE FOX OS" + _FIN
    lignes[2] += "   " + sous_titre
    return ["  " + x for x in lignes]


def _paragraphe(texte, couleur=""):
    return ["  " + couleur + x + (_FIN if couleur else "")
            for x in textwrap.wrap(texte, _LARGEUR - 4)]


def ecran_domaine(erreur=None):
    lignes = [_entete(), ""] + _renard("installation") + [""]
    lignes += _paragraphe("A quelle organisation cette machine appartient-elle ?")
    lignes.append("")
    lignes += _paragraphe("Entrez le domaine de votre organisation : celui de "
                          "l'adresse de votre Odoo, par exemple entreprise.com.")
    lignes.append("")
    if erreur:
        lignes += _paragraphe(erreur, _ALERTE)
        lignes.append("")
    lignes += _paragraphe("Sans organisation : redemarrez et choisissez "
                          "'Installer Blue Fox OS' au menu.")
    return "\n".join(lignes) + "\n\n"


def invite_domaine(defaut):
    return f"  Domaine [{defaut}] : " if defaut else "  Domaine : "


def ecran_confirmation(organisation, domaine):
    lignes = [_entete(), ""] + _renard("organisation trouvee") + [""]
    lignes += [
        "  Organisation : " + _GRAS + organisation + _FIN,
        "  Domaine      : " + _MARQUE + domaine + _FIN,
        "",
    ]
    lignes += _paragraphe("L'autorisation de cette machine sera demandee a "
                          "cette organisation, a l'ecran suivant.")
    return "\n".join(lignes) + "\n\n"


def demander(console, defaut, verifier, annoncer_reseau=None,
             pret=reseau_pret, sleep=time.sleep, now=time.monotonic):
    """Boucle de saisie -> (domaine, organisation, texte du kickstart)."""
    erreur, a_verifier = None, None
    while True:
        if a_verifier is None:
            console.effacer()
            console.ecrire(ecran_domaine(erreur))
            console.ecrire(invite_domaine(defaut))
            saisie = console.lire_ligne() or defaut or ""
            domaine = normaliser_domaine(saisie)
            if domaine is None:
                erreur = (f"'{_lisible(saisie)}' n'est pas un domaine."
                          if saisie else "Entrez le domaine de votre organisation.")
                continue
        else:
            domaine, a_verifier = a_verifier, None

        console.ecrire(f"\n  Verification de {domaine}...\n")
        attendre_reseau(lambda m: console.ecrire(f"  {m}\n"), pret, sleep, now)
        try:
            organisation, texte = verifier(domaine)
        except Exception as exc:  # noqa: BLE001 — tout se dit, tout se reessaie
            erreur = expliquer(exc, domaine)
            # Entree seule retente le meme domaine : une faute de frappe se
            # corrige, une panne passagere se relance sans tout retaper.
            defaut = domaine
            continue

        console.effacer()
        console.ecrire(ecran_confirmation(organisation, domaine))
        console.ecrire(INVITE_CONFIRMATION)
        saisie = console.lire_ligne()
        if not saisie:
            return domaine, organisation, texte
        if saisie.lower() in _RETOUR:
            erreur, defaut = None, domaine
            continue
        autre = normaliser_domaine(saisie)
        if autre is None:
            erreur, defaut = f"'{_lisible(saisie)}' n'est pas un domaine.", domaine
            continue
        a_verifier = autre


# =========================================================================
# Orchestration
# =========================================================================
def _trace_stderr(msg):
    print(f"[bfos-amorce] {msg}", file=sys.stderr, flush=True)


def _journal_console():
    """Journal des chemins sans question : console d'installation + stderr,
    que le bloc pre de l'amorce verse dans /tmp/bfos-amorce.log."""
    try:
        fh = open(CONSOLE, "w", encoding="utf-8", errors="replace")
    except OSError:
        fh = None

    def journal(msg):
        ligne = f"[bfos-amorce] {msg}"
        if fh is not None:
            try:
                fh.write(_en_crlf(ligne + "\n"))
                fh.flush()
            except OSError:
                pass
        print(ligne, file=sys.stderr, flush=True)

    return journal


def run(parametres, domaine_defaut="", journal=None, console_factory=ConsoleVt,
        telecharger_=telecharger, pret=reseau_pret, sleep=time.sleep,
        now=time.monotonic, lancer=subprocess.run, dossier="/tmp"):
    """Obtient le kickstart de l'organisation, execute ses blocs pre et depose
    le reste. Rend le nom de l'organisation."""
    # Une fois l'organisation choisie, l'ecran appartient a ses blocs pre :
    # l'ecran d'autorisation est calibre sur 24 lignes, et chaque ligne a nous
    # posee avant lui le pousserait vers le haut. La suite ne va qu'au journal.
    trace = journal or _trace_stderr
    journal = journal or _journal_console()

    if parametres.get("bfos.ks"):
        url = parametres["bfos.ks"]
        journal(f"kickstart impose par bfos.ks : {url}")
        attendre_reseau(journal, pret, sleep, now, patience=_PATIENCE_RESEAU)
        statut, en_tetes, texte = _avec_reprises(
            lambda: telecharger_(url), url, journal, sleep)
        if statut != 200:
            raise ErreurAmorce(f"{url} a repondu {statut} au lieu du kickstart.")
        organisation = verifier_kickstart(texte, en_tetes, exiger_en_tete=False, cible=url)
    elif parametres.get("bfos.domaine"):
        domaine = normaliser_domaine(parametres["bfos.domaine"])
        if domaine is None:
            raise ErreurAmorce(f"bfos.domaine={_lisible(parametres['bfos.domaine'])} "
                               "n'est pas un domaine.")
        journal(f"domaine impose par bfos.domaine : {domaine}")
        attendre_reseau(journal, pret, sleep, now, patience=_PATIENCE_RESEAU)
        organisation, texte = _avec_reprises(
            lambda: verifier_domaine(domaine, telecharger_), domaine, journal, sleep)
    else:
        try:
            console = console_factory()
        except Exception as exc:  # noqa: BLE001
            raise ErreurAmorce(
                f"Aucune console pour demander le domaine ({exc}). Au menu de "
                "demarrage, tapez 'e' et ajoutez bfos.domaine=<domaine> a la "
                "ligne linux.") from exc
        try:
            domaine, organisation, texte = demander(
                console, domaine_defaut,
                lambda d: verifier_domaine(d, telecharger_),
                pret=pret, sleep=sleep, now=now)
        finally:
            console.fermer()

    trace(f"organisation : {organisation}")
    blocs, reste = separer_pre(texte)
    # Depose AVANT d'executer : le fichier ne depend pas des blocs pre, et
    # Anaconda doit le trouver quoi que ces blocs deviennent.
    ecrire_prive(os.path.join(dossier, KS_LOCATAIRE), reste)
    executer_pre(blocs, trace, lancer=lancer, dossier=dossier)
    return organisation


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--domaine-defaut", default="")
    parser.add_argument("--cmdline", default=CMDLINE)
    args = parser.parse_args(argv)
    try:
        with open(args.cmdline, encoding="utf-8", errors="replace") as fh:
            parametres = lire_parametres(fh.read())
    except OSError:
        parametres = {}
    try:
        run(parametres, domaine_defaut=normaliser_domaine(args.domaine_defaut) or "")
    except ErreurAmorce as exc:
        # Le bloc pre de l'amorce porte --erroronfail : Anaconda s'arrete et
        # affiche ce journal. Mieux qu'une installation sans organisation.
        print(f"[bfos-amorce] ERREUR : {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
