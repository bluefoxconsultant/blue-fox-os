#!/usr/bin/env python3
"""Le jeton du widget, rangé dans KWallet au lieu du profil en clair.

Pourquoi ce fichier existe, et pourquoi il n'est pas en QML.

BF #27250, lot 5. Le jeton vivait dans la configuration Plasma du widget, un
fichier en clair dans le profil : une sauvegarde ou une synchronisation du
profil l'emportait. Et comme la configuration Plasma est **par instance**,
poser plusieurs widgets voulait dire un appairage et un jeton par carré.
KWallet règle les deux : un seul coffre, partagé par toutes les instances.

🔴 Il n'existe AUCUN module QML pour KWallet en Plasma 6 (vérifié sur
charizard-garuda, Plasma 6.8.2 : rien sous /usr/lib/qt6/qml/org/kde/kwallet*).
Il faut donc un processus. Et un processus impose une contrainte dure :

    /proc/<pid>/cmdline est lisible par TOUT LE MONDE  (-r--r--r--)
    /proc/<pid>/environ ne l'est que par le propriétaire (-r--------)

Mesuré sur la machine, /proc monté sans `hidepid`. Un secret passé en argument
serait donc lisible par n'importe quel compte du poste : remplacer un fichier
lisible par son seul propriétaire par cela aurait été un RECUL.

D'où la forme de cet utilitaire : **aucun secret ne traverse jamais une ligne
de commande.** Ni le jeton, ni le code d'appairage, ni le secret d'appareil.

    probe        dit si KWallet est utilisable ici, sans rien écrire
    pair-start   démarre l'appairage, range code+secret dans $XDG_RUNTIME_DIR
                 (tmpfs, 0600, effacé à la déconnexion, jamais sauvegardé), et
                 n'écrit sur stdout que ce qui est public : l'adresse à ouvrir
                 et le nombre à afficher
    pair-poll    relit cet état, interroge le serveur, et si un jeton revient
                 l'écrit dans KWallet par le STDIN de kwallet-query.
                 Rend « stored » — jamais le jeton
    pair-cancel  oublie l'appairage en cours
    read         écrit le jeton sur stdout, pour le widget qui doit le mettre
                 dans un en-tête HTTP. C'est le seul endroit où il sort, et
                 c'est par un tube, pas par /proc
    forget       retire l'entrée du portefeuille

🔴 Ce que ce fichier NE croit PAS : la sortie des outils KWallet. Sur cette
machine, ~/.local/share/kwalletd/ contient deux fichiers nommés « Did not
receive a reply. Possible causes include;3A ….kwl » : un message d'erreur
D-Bus a déjà servi de NOM DE PORTEFEUILLE. Donc le nom du portefeuille n'est
jamais déduit d'une sortie de commande, et un jeton relu est vérifié de forme
avant d'être rendu. Ce qui ne colle pas au motif est une erreur, pas un secret.

Sortie : TOUJOURS une seule ligne de JSON sur stdout et rien d'autre, parce que
l'appelant est un DataSource de Plasma qui ne lit que cela. Le code de sortie
du processus est 0 même sur un refus : c'est le JSON qui porte le verdict.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

# Le coffre : un dossier à nous dans le portefeuille local de la personne.
#
# Surchargeables par l'environnement pour pouvoir éprouver un appairage complet
# sur une machine réelle SANS toucher l'entrée dont le widget de la personne se
# sert. Ce ne sont pas des secrets, seulement des noms de tiroir.
DOSSIER = os.environ.get("BFSN_FOLDER") or "Symbifox"
ENTREE = os.environ.get("BFSN_ENTRY") or "stickynotes"
# 🔴 L'ADRESSE, rangée à côté du jeton et dans le même coffre partagé.
#
# Pourquoi elle ne peut pas rester dans la configuration de l'instance : le
# jeton est partagé pour que « un carré par note » n'exige pas un appairage par
# carré, mais l'adresse, elle, l'était par instance. Toute instance neuve
# démarrait donc avec un jeton valide et aucune adresse — appariée en apparence,
# muette en pratique, chaque passe mourant sur `no_server` sans qu'un appel ne
# sorte. C'est le défaut du 2026-11-02 : « pas de synchro bien faite ».
#
# ⚠️ Une entrée DISTINCTE, et non un JSON dans l'entrée du jeton : le jeton
# garde ainsi sa validation stricte, et une entrée ancienne (jeton nu) reste
# lisible telle quelle — rien à migrer.
ENTREE_SERVEUR = os.environ.get("BFSN_ENTRY_SERVER") or (ENTREE + "-server")
# 🔴 Jamais déduit d'une sortie de commande. Voir l'en-tête.
PORTEFEUILLE = "kdewallet"

# La forme du jeton livré par bf_desktop_pairing : secrets.token_urlsafe(32),
# soit 43 caractères de base64url. La fourchette laisse de l'air si la longueur
# change côté serveur, sans accepter n'importe quoi.
MOTIF_JETON = re.compile(r"\A[A-Za-z0-9_-]{32,64}\Z")
# ⚠️ La même forme que `serverPattern` dans main.qml, et pour la même raison :
# ce qui sort d'un coffre est une donnée, pas une vérité. Un portefeuille
# altéré pourrait sinon pointer le widget — et son jeton — ailleurs. Garde des
# deux côtés, comme pour les couleurs de marque.
MOTIF_SERVEUR = re.compile(
    r"\Ahttps?://[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(:[0-9]{1,5})?"
    r"(/[A-Za-z0-9._~/-]*)?\Z")

BASE = "/bf_desktop_pairing/v1"
ETAT = "bfsn-pairing.json"


def _delai() -> int:
    """Le délai d'abandon, en secondes. Réglable pour les essais, et borné :
    0 pendrait le widget, une valeur énorme aussi."""
    try:
        valeur = int(os.environ.get("BFSN_TIMEOUT") or 20)
    except ValueError:
        return 20
    return min(max(valeur, 1), 120)


DELAI = _delai()


def _rendre(**donnees) -> None:
    sys.stdout.write(json.dumps(donnees, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ── L'appairage en cours, hors du profil ──────────────────────────────────

def _chemin_etat() -> tuple[str, bool]:
    """(chemin, dans_un_tmpfs). XDG_RUNTIME_DIR est /run/user/<uid>, en 0700 :
    ni sauvegardé, ni synchronisé, vidé à la déconnexion. C'est exactement la
    durée de vie d'un code d'appairage."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and os.path.isdir(base):
        return os.path.join(base, ETAT), True
    return os.path.join("/tmp", ETAT), False


def _ecrire_etat(etat: dict) -> None:
    chemin, _ = _chemin_etat()
    # Créé à 0600 AVANT d'écrire : jamais une fenêtre où le fichier est
    # lisible par d'autres.
    drapeaux = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(chemin, drapeaux, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(etat, f)


def _lire_etat() -> dict | None:
    chemin, _ = _chemin_etat()
    try:
        with open(chemin, encoding="utf-8") as f:
            etat = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(etat, dict) or not etat.get("code") or not etat.get("secret"):
        return None
    return etat


def _oublier_etat() -> None:
    chemin, _ = _chemin_etat()
    try:
        os.unlink(chemin)
    except OSError:
        pass


# ── Le serveur ────────────────────────────────────────────────────────────

def _appel(serveur: str, route: str, corps: dict | None) -> tuple[int, dict]:
    url = serveur.rstrip("/") + BASE + route
    donnees = None
    entetes = {"Accept": "application/json"}
    if corps is not None:
        donnees = json.dumps(corps).encode("utf-8")
        # ⚠️ Exigé par le serveur : sans ce type, un POST serait une « requête
        # simple » au sens CORS, et n'importe quelle page piégée pourrait
        # consommer un créneau d'appairage depuis l'adresse d'un visiteur.
        entetes["Content-Type"] = "application/json"
    requete = urllib.request.Request(
        url, data=donnees, headers=entetes,
        method="POST" if corps is not None else "GET")
    try:
        with urllib.request.urlopen(requete, timeout=DELAI) as reponse:
            brut = reponse.read().decode("utf-8", "replace")
            try:
                return reponse.status, json.loads(brut)
            except ValueError:
                # Un 2xx illisible est une erreur, jamais une réponse vide :
                # un portail captif répond 200 avec du HTML.
                return reponse.status, {"error": "bad_payload"}
    except urllib.error.HTTPError as erreur:
        brut = erreur.read().decode("utf-8", "replace")
        try:
            return erreur.code, json.loads(brut)
        except ValueError:
            return erreur.code, {"error": "http_%d" % erreur.code}
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {"error": "offline"}


# ── Le portefeuille ───────────────────────────────────────────────────────

def _kwallet(args: list[str], entree: bytes | None = None) -> tuple[int, bytes]:
    try:
        fini = subprocess.run(
            ["kwallet-query", "-f", DOSSIER] + args + [PORTEFEUILLE],
            input=entree, capture_output=True, timeout=DELAI)
    except FileNotFoundError:
        return 127, b""
    except subprocess.TimeoutExpired:
        # ⚠️ Un portefeuille fermé peut faire surgir une invite : si personne
        # ne répond, on abandonne plutôt que de bloquer le widget.
        return 124, b""
    return fini.returncode, fini.stdout


def _lire_jeton() -> tuple[str, str]:
    """(jeton, raison). Le jeton n'est rendu que s'il a la bonne forme."""
    code, sortie = _kwallet(["-r", ENTREE])
    if code == 127:
        return "", "no_wallet_tool"
    if code == 124:
        return "", "wallet_timeout"
    if code != 0:
        return "", "absent"
    valeur = sortie.decode("utf-8", "replace").strip()
    if not valeur:
        return "", "absent"
    if not MOTIF_JETON.match(valeur):
        # 🔴 Ici se ferme l'incident des portefeuilles parasites : une sortie
        # qui n'est pas un jeton n'est pas un jeton.
        return "", "malformed"
    return valeur, ""


def _lire_serveur() -> tuple[str, str]:
    """(adresse, raison). L'adresse est "" si elle manque, ne tient pas la
    forme, ou n'a pas pu être lue.

    🔴 Les trois cas ne se valent pas, et les confondre rendait la garde
    inopérante : un hoquet de `kwallet-query` (verrou, délai, dossier refusé)
    devenait « pas d'adresse au coffre », ce qui DÉSACTIVE la comparaison de
    divergence et ROUVRE le rangement — donc un hoquet du portefeuille
    re-associait silencieusement le jeton à l'adresse que portait l'instance,
    quelle qu'elle soit. L'absence n'est pas une panne ; un code d'erreur l'est.
    """
    code, sortie = _kwallet(["-r", ENTREE_SERVEUR])
    if code == 127:
        return "", "no_wallet_tool"
    if code == 124:
        return "", "wallet_timeout"
    if code != 0:
        # kwallet-query rend 1 pour « entrée absente » comme pour « dossier
        # refusé » : indiscernables, donc traités comme une absence — mais
        # l'appelant doit savoir qu'il n'a pas lu, pas qu'il n'y a rien.
        return "", "absent"
    valeur = sortie.decode("utf-8", "replace").strip().rstrip("/")
    if not valeur:
        return "", "absent"
    return (valeur, "") if MOTIF_SERVEUR.match(valeur) else ("", "malformed")


def _ecrire_serveur(serveur: str) -> str:
    """"" si c'est écrit, sinon la raison. Même vérification par relecture que
    le jeton : kwallet-query rend 0 sans rien écrire quand le dossier est
    refusé."""
    valeur = (serveur or "").strip().rstrip("/")
    if not MOTIF_SERVEUR.match(valeur):
        return "bad_server"
    code, _ = _kwallet(["-w", ENTREE_SERVEUR], entree=valeur.encode("utf-8"))
    if code == 127:
        return "no_wallet_tool"
    if code == 124:
        return "wallet_timeout"
    if code != 0:
        return "wallet_refused"
    relu, _raison = _lire_serveur()
    if relu != valeur:
        return "wallet_write_unverified"
    return ""


def _ecrire_jeton(jeton: str) -> str:
    """"" si c'est écrit, sinon la raison. Le jeton passe par STDIN."""
    code, _ = _kwallet(["-w", ENTREE], entree=jeton.encode("utf-8"))
    if code == 127:
        return "no_wallet_tool"
    if code == 124:
        return "wallet_timeout"
    if code != 0:
        return "wallet_refused"
    # ⚠️ Vérifier en relisant : kwallet-query rend 0 sans rien écrire quand le
    # portefeuille est ouvert mais le dossier refusé. Une écriture annoncée et
    # non faite déconnecterait le widget au prochain démarrage, sans un mot.
    relu, _raison = _lire_jeton()
    if relu != jeton:
        return "wallet_write_unverified"
    return ""


# ── Les sous-commandes ────────────────────────────────────────────────────

def cmd_probe() -> None:
    """Utilisable ici ? Ne touche à rien : une lecture, c'est tout."""
    jeton, raison = _lire_jeton()
    _, tmpfs = _chemin_etat()
    utilisable = raison not in ("no_wallet_tool", "wallet_timeout")
    # ⚠️ Pas de `has_server` ici : `read` porte déjà l'adresse, et une clé que
    # le widget ne lit pas est une clé qui divergera en silence. C'est
    # `test_le_contrat_du_coffre_est_lu_par_le_widget` qui l'a refusée.
    _rendre(ok=utilisable, wallet=utilisable,
            has_token=bool(jeton), reason=raison, runtime_tmpfs=tmpfs)


def cmd_read() -> None:
    """Le seul endroit où le jeton sort, par un tube."""
    jeton, raison = _lire_jeton()
    if not jeton:
        _rendre(ok=False, reason=raison or "absent")
        return
    # ⚠️ L'adresse part AVEC le jeton : un jeton sans son serveur n'est bon à
    # rien, et c'est précisément l'état qui rendait le widget muet.
    adresse, raison_serveur = _lire_serveur()
    # ⚠️ `server_unknown` : le coffre n'a pas pu être LU, ce qui n'est pas la
    # même chose qu'« aucune adresse rangée ». Le widget doit alors s'abstenir
    # de juger — ni adopter, ni graver, ni dissocier.
    _rendre(ok=True, token=jeton, server=adresse,
            server_unknown=raison_serveur in ("no_wallet_tool", "wallet_timeout"))


def cmd_forget() -> None:
    # kwallet-query n'a pas de « supprimer » : écraser par du vide retire la
    # valeur utile, et la forme invalide fait rejeter ce qui reste.
    _kwallet(["-w", ENTREE], entree=b"")
    # ⚠️ L'adresse aussi : la laisser ferait croire au prochain démarrage que
    # le poste est à moitié apparié, et l'écran ne demanderait pas ce qu'il
    # faut. Les deux entrent ensemble, les deux sortent ensemble.
    _kwallet(["-w", ENTREE_SERVEUR], entree=b"")
    _oublier_etat()
    jeton, _ = _lire_jeton()
    adresse, _raison = _lire_serveur()
    _rendre(ok=not jeton and not adresse)


def cmd_set_server(serveur: str) -> None:
    """Ranger l'adresse au coffre pour un poste DÉJÀ apparié.

    🔴 Sans cette action, le correctif du 2026-11-02 ne vaudrait que pour les
    appairages à venir : un poste dont le jeton est déjà au coffre ne repasse
    jamais par `pair-poll`, donc son adresse n'y entrerait pas et chaque carré
    neuf resterait muet. C'est le cas du poste qui a trouvé le défaut.

    ⚠️ L'adresse n'est pas un secret — `pair-start` la prend déjà en argument —
    et le widget ne l'appelle qu'APRÈS une réponse réussie du serveur avec ce
    jeton : c'est la preuve que le couple fonctionne. Graver une adresse non
    vérifiée associerait le jeton à un endroit où il ne vaut rien."""
    jeton, _raison = _lire_jeton()
    if not jeton:
        # Pas de jeton, pas d'adresse à associer : les deux vont ensemble.
        _rendre(ok=False, reason="absent")
        return
    raison = _ecrire_serveur(serveur)
    _rendre(ok=not raison, reason=raison)


def cmd_pair_start(serveur: str, nom: str, version: str) -> None:
    statut, donnees = _appel(serveur, "/auth/device/start", {
        "app": "stickynotes", "name": nom, "version": version})
    if statut not in (200, 201) or donnees.get("error"):
        _rendre(ok=False, reason=donnees.get("error") or "http_%d" % statut,
                detail=donnees.get("detail") or "")
        return
    code = donnees.get("device_code") or ""
    secret = donnees.get("device_secret") or ""
    if not code or not secret:
        _rendre(ok=False, reason="bad_payload")
        return
    try:
        _ecrire_etat({"server": serveur, "code": code, "secret": secret})
    except OSError:
        _rendre(ok=False, reason="no_state_dir")
        return
    # Sur stdout, RIEN que du public. Le code et le secret restent dans le
    # fichier d'état : ce sont eux qui, ensemble, échangent contre un jeton.
    _rendre(ok=True,
            verification_uri_complete=donnees.get("verification_uri_complete") or "",
            match_number=donnees.get("match_number") or "",
            interval=donnees.get("interval") or 2,
            expires_in=donnees.get("expires_in") or 300)


def cmd_pair_poll() -> None:
    etat = _lire_etat()
    if not etat:
        _rendre(ok=False, state="no_pairing")
        return
    statut, donnees = _appel(etat["server"], "/auth/device/poll", {
        "device_code": etat["code"], "device_secret": etat["secret"]})
    erreur = donnees.get("error")
    if erreur == "authorization_pending" or statut == 202:
        _rendre(ok=True, state="pending")
        return
    if erreur == "slow_down":
        _rendre(ok=True, state="slow_down", interval=donnees.get("interval") or 5)
        return
    if erreur or statut not in (200, 201):
        # Le serveur détruit la demande sur un refus : inutile de garder l'état.
        _oublier_etat()
        _rendre(ok=False, state="refused",
                reason=erreur or "http_%d" % statut,
                detail=donnees.get("detail") or "")
        return
    jeton = donnees.get("token") or ""
    if not MOTIF_JETON.match(jeton):
        _oublier_etat()
        _rendre(ok=False, state="refused", reason="bad_payload")
        return
    raison = _ecrire_jeton(jeton)
    # 🔴 L'adresse part au coffre avec le jeton, et c'est `etat["server"]` —
    # le CANONIQUE sur lequel l'appairage a réellement abouti, pas ce qui a été
    # tapé. C'est elle que les instances neuves doivent adopter : un carré posé
    # demain hérite du jeton ET de l'endroit où s'en servir.
    raison_serveur = "" if raison else _ecrire_serveur(etat.get("server") or "")
    # L'état part dans tous les cas : le jeton n'est livré qu'une fois, donc
    # repoller ne redonnerait rien.
    _oublier_etat()
    if raison:
        # 🔴 Le jeton existe côté serveur mais n'a pas pu être rangé. Le dire :
        # sans cela le widget se croirait apparié et l'appareil resterait
        # listé dans « Mes appareils » sans que rien ne s'en serve.
        _rendre(ok=False, state="stored_failed", reason=raison)
        return
    # ⚠️ Une adresse non rangée n'annule PAS l'appairage : le jeton est livré,
    # l'appareil est listé côté serveur, et l'instance qui vient d'apparier
    # porte son adresse dans sa propre configuration. Ce qui se perd, c'est
    # l'héritage par les instances neuves — donc on le dit au lieu de défaire
    # un appairage qui a abouti.
    _rendre(ok=True, state="stored", server_stored=not raison_serveur,
            server_reason=raison_serveur)


def cmd_pair_cancel() -> None:
    _oublier_etat()
    _rendre(ok=True)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _rendre(ok=False, reason="usage")
        return 0
    action = argv[1]
    try:
        if action == "probe":
            cmd_probe()
        elif action == "read":
            cmd_read()
        elif action == "set-server":
            cmd_set_server(argv[2] if len(argv) > 2 else "")
        elif action == "forget":
            cmd_forget()
        elif action == "pair-start":
            # L'adresse et le nom du poste ne sont pas des secrets.
            serveur = argv[2] if len(argv) > 2 else ""
            nom = argv[3] if len(argv) > 3 else "Blue Fox OS workstation"
            version = argv[4] if len(argv) > 4 else ""
            if not serveur:
                _rendre(ok=False, reason="no_server")
            else:
                cmd_pair_start(serveur, nom, version)
        elif action == "pair-poll":
            cmd_pair_poll()
        elif action == "pair-cancel":
            cmd_pair_cancel()
        else:
            _rendre(ok=False, reason="unknown_action")
    except Exception as erreur:  # noqa: BLE001
        # Une trace sur stdout casserait l'appelant, qui n'attend qu'une ligne
        # de JSON. Le type suffit à diagnostiquer sans divulguer un chemin.
        _rendre(ok=False, reason="crash", detail=type(erreur).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
