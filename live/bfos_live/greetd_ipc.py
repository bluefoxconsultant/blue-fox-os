"""Client du protocole de greetd (#26870).

Chaque message : une longueur sur 4 octets (ordre natif de la machine), puis
le JSON. Une requete, une reponse, sur la meme connexion.

Reponses : {"type": "success"}, {"type": "error", "error_type": ..., "description": ...},
{"type": "auth_message", "auth_message_type": "visible|secret|info|error", "auth_message": ...}.
"""
from __future__ import annotations

import json
import os
import socket
import struct

_LONGUEUR = struct.Struct("=I")
TAILLE_MAX = 1 << 20


class ErreurIPC(Exception):
    pass


def _lire_exact(sock, n):
    morceaux, reste = [], n
    while reste:
        bloc = sock.recv(reste)
        if not bloc:
            raise ErreurIPC("greetd a ferme la connexion")
        morceaux.append(bloc)
        reste -= len(bloc)
    return b"".join(morceaux)


def envoyer(sock, message: dict):
    corps = json.dumps(message).encode()
    sock.sendall(_LONGUEUR.pack(len(corps)) + corps)


def recevoir(sock) -> dict:
    (n,) = _LONGUEUR.unpack(_lire_exact(sock, _LONGUEUR.size))
    if n > TAILLE_MAX:
        raise ErreurIPC(f"message de greetd trop long ({n} octets)")
    try:
        d = json.loads(_lire_exact(sock, n))
    except ValueError as exc:
        raise ErreurIPC(f"JSON illisible de greetd: {exc}") from exc
    if not isinstance(d, dict) or "type" not in d:
        raise ErreurIPC("reponse de greetd sans type")
    return d


class Client:
    def __init__(self, sock=None, chemin=None):
        if sock is None:
            chemin = chemin or os.environ.get("GREETD_SOCK")
            if not chemin:
                raise ErreurIPC("GREETD_SOCK absent : pas lance par greetd")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(chemin)
        self.sock = sock

    def requete(self, message: dict) -> dict:
        envoyer(self.sock, message)
        return recevoir(self.sock)

    def creer_session(self, usager):
        return self.requete({"type": "create_session", "username": usager})

    def repondre(self, reponse=None):
        return self.requete({"type": "post_auth_message_response", "response": reponse})

    def demarrer(self, cmd, env=None):
        return self.requete({"type": "start_session", "cmd": list(cmd), "env": list(env or [])})

    def annuler(self):
        return self.requete({"type": "cancel_session"})

    def fermer(self):
        try:
            self.sock.close()
        except OSError:
            pass
