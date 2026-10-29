#!/usr/bin/env python3
"""scripts/proxy-openimage.py — juge une image comme rpm-ostree la jugerait,
sans en tirer une seule couche (#27109).

POURQUOI
Sur un poste, rpm-ostree (ostree-ext) ne lance pas `skopeo copy` : il ouvre
l'image par le proxy `skopeo experimental-image-proxy`, et c'est OpenImage qui
applique /etc/containers/policy.json (IsRunningImageAllowed, sur le manifeste
et les signatures, avant la premiere couche). Ce client rejoue exactement cet
appel. Il sert a la porte canari de scripts/publish-image.sh, execute DANS
l'image publiee (son skopeo, sa politique, son registries.d, ses cles) :

    podman run -i --rm --entrypoint /usr/bin/python3 <image> - docker://REF \\
        < scripts/proxy-openimage.py

Le script passe par l'entree standard : aucun montage du depot dans le
conteneur, aucun relabel SELinux de l'arbre de travail.

SORTIE (une ligne sur stdout)
  0  ACCEPTE   la politique laisse passer REF
  1  REFUSE    la politique a JUGE et refuse : l'erreur porte un message de
               verdict de containers-image (VERDICTS) et aucun signe de panne
               passagere (PASSAGERS)
  2  INCONNU   tout le reste (registre injoignable, tag absent, delai depasse,
               proxy absent ou muet, cle illisible). ⚠️ Un « reading
               signatures: ... i/o timeout » n'est PAS un refus.

⚠️ « Source image rejected » est le prefixe de `skopeo copy` (copy/single.go
enveloppe TOUTE erreur de la politique, panne de lecture des signatures
comprise) ; le proxy rend l'erreur nue. Mesure du 2026-10-29, skopeo 1.22.3,
bf:latest sous une politique a la seule cle de secours :
  « cryptographic signature verification failed: invalid signature when
    validating ASN.1 encoded signature »   (aucun prefixe)
D'ou un jugement sur les messages de verdict eux-memes.

Adapte du client de 20 lignes de la relecture adverse 1 (2026-10-28) ; seul le
protocole public du proxy est employe (Initialize, OpenImage, CloseImage).
"""
import json
import os
import re
import socket
import subprocess
import sys

# Messages que containers-image rend quand la POLITIQUE a tranche
# (signature/policy_eval_*.go, internal/signature) ; en minuscules.
VERDICTS = (
    "a signature was required, but no signature exists",
    "cryptographic signature verification failed",
    "none of the signatures were accepted",
    "is rejected by policy",                # type reject
    "is not accepted",                      # identite, cle GPG
    "signature for identity",
    "invalid signature",
)
# Une panne de transport pendant le jugement (lecture des signatures, cle de
# Rekor...) remonte par le meme chemin : elle annule le verdict.
# (Mots entiers : un digest en hexadecimal contient volontiers « 502 ».)
PASSAGERS = re.compile(
    r"reading signature|timeout|dial tcp|connection refused|connection reset|"
    r"tls handshake|no such host|name resolution|unexpected eof|context deadline|"
    r"too ?many ?requests|service unavailable|bad gateway|\b50[234]\b")


def verdict(succes, erreur):
    """(code, mot) pour une reponse d'OpenImage."""
    if succes:
        return 0, "ACCEPTE"
    texte = (erreur or "").lower()
    if any(v in texte for v in VERDICTS) and not PASSAGERS.search(texte):
        return 1, "REFUSE"
    return 2, "INCONNU"


def _appel(sock, methode, args):
    sock.send(json.dumps({"method": methode, "args": args}).encode())
    msg, anc, _flags, _addr = sock.recvmsg(1 << 20, socket.CMSG_SPACE(16))
    # Un descripteur passe en donnee annexe (GetBlob) : on n'en veut aucun.
    for niveau, type_, donnees in anc:
        if niveau == socket.SOL_SOCKET and type_ == socket.SCM_RIGHTS:
            for i in range(0, len(donnees) - len(donnees) % 4, 4):
                os.close(int.from_bytes(donnees[i:i + 4], sys.byteorder))
    if not msg:
        raise ConnectionError(f"le proxy a ferme la connexion pendant {methode}")
    return json.loads(msg)


def juger(ref, skopeo="skopeo", delai=120.0):
    """Rend (code, ligne). Ne leve rien."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        proc = subprocess.Popen([skopeo, "experimental-image-proxy", "--sockfd", str(b.fileno())],
                                pass_fds=[b.fileno()], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL)
    except OSError as exc:
        a.close()
        b.close()
        return 2, f"OpenImage {ref} : INCONNU : skopeo introuvable ({exc})"
    b.close()
    a.settimeout(delai)
    try:
        init = _appel(a, "Initialize", [])
        if not init.get("success"):
            return 2, f"OpenImage {ref} : INCONNU : Initialize : {init.get('error')}"
        r = _appel(a, "OpenImage", [ref])
        code, mot = verdict(r.get("success"), r.get("error"))
        if code == 0:
            try:
                _appel(a, "CloseImage", [r.get("value")])
            except (OSError, ValueError):
                pass
            return 0, f"OpenImage {ref} : {mot} (protocole {init.get('value')})"
        return code, f"OpenImage {ref} : {mot} : {str(r.get('error'))[:600]}"
    except (OSError, ValueError) as exc:     # socket.timeout est un OSError
        return 2, f"OpenImage {ref} : INCONNU : {type(exc).__name__} : {exc}"
    finally:
        a.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0].startswith("docker://"):
        print("usage : proxy-openimage.py docker://REF", file=sys.stderr)
        return 2
    code, ligne = juger(argv[0], skopeo=os.environ.get("BF_SKOPEO", "skopeo"),
                        delai=float(os.environ.get("BF_PROXY_DELAI", "120")))
    print(ligne, flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
