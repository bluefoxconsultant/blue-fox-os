"""Écrire un fichier qui porte un secret, sans fenêtre de lisibilité.

Le motif `write_text(...)` puis `chmod(0o600)` a un défaut que son état final
masque : entre les deux instructions, le secret est sur le disque au mode que
le umask concède — 0664 dans la session de l'assistant, 0644 au umask standard
0022. Le résultat final est correct, donc un test qui lit le mode APRÈS coup
est vert, et l'a toujours été.

Deux façons de perdre : le processus meurt entre les deux instructions, ou le
chmod échoue. Dans les deux cas il reste un fichier lisible au-delà de son
propriétaire, et rien ne le signale.

`os.open(..., 0o600)` ferme la fenêtre : c'est le noyau qui applique le mode à
la création, il n'y a plus d'instant où le fichier existe autrement. Le fchmod
qui suit ne sert qu'au cas du fichier DÉJÀ là — O_CREAT ignore le mode sur un
fichier existant, et O_TRUNC ne remet pas ses permissions. C'est le chemin que
prend toute machine qui a déjà tourné avec la version fautive.
"""
import os

MODE_PRIVATE = 0o600


def write_private(path, content: str) -> None:
    """Écrire `content` dans `path`, propriétaire seul, dès le premier octet.

    Aucun `except` : un secret qu'on n'arrive pas à restreindre doit faire
    échouer l'appelant, pas se poser en clair en silence.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, MODE_PRIVATE)
    try:
        # Le fichier préexistait : O_CREAT n'a pas appliqué le mode.
        os.fchmod(fd, MODE_PRIVATE)
        fh = os.fdopen(fd, "w")
    except BaseException:
        os.close(fd)
        raise
    with fh:
        fh.write(content)
