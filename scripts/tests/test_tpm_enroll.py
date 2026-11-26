"""bluefox-tpm-enroll : le verrou GRUB qui suit l'enrolement TPM2 (#28058).

Les cas qui comptent :

- Enrolement neuf : la phrase vient d'ouvrir le volume, elle verrouille GRUB.
  Elle passe par stdin, jamais en argument.
- Jeton deja la (postes v1.0.2) et GRUB ouvert : on ne peut rien fermer d'ici
  (la phrase n'est qu'au sequestre), on le dit au journal.
- Enrolement refuse : GRUB n'est pas touche.

⚠️ Bash reel, commandes systeme remplacees par des faux dans le PATH.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-tpm-enroll"
UNITE = REPO_ROOT / "files" / "usr" / "lib" / "systemd" / "system" / "bluefox-tpm-enroll.service"
PHRASE = "K7QM2-WXD9P-RTHZ4-ENB6C-AJVX3"


def _faux(bin_dir, nom, corps):
    f = bin_dir / nom
    f.write_text("#!/bin/sh\n" + corps + "\n")
    f.chmod(0o755)


@pytest.fixture
def poste(tmp_path):
    etat = tmp_path / "etat"
    etat.mkdir()
    (etat / "provisioning.json").write_text(
        '{"policies": {"tpm_autounlock": {"enabled": true, "pcrs": "7"}}}')
    (tmp_path / "crypttab").write_text("luks-abc UUID=abc none discard\n")
    b = tmp_path / "bin"
    b.mkdir()
    journal = tmp_path / "appels"
    jeton = tmp_path / "jeton"
    verrou = tmp_path / "grub-verrouille"
    _faux(b, "id", "echo 0")
    _faux(b, "logger", ":")
    _faux(b, "systemd-analyze", "exit 0")
    _faux(b, "rpm-ostree", "exit 0")
    _faux(b, "cryptsetup", f'[ "$1" = luksDump ] && [ -f {jeton} ] && echo "  0: systemd-tpm2"; exit 0')
    _faux(b, "systemd-ask-password", f"echo {PHRASE}")
    _faux(b, "systemd-cryptenroll",
          f'echo "cryptenroll $*" >> {journal}; '
          f'[ -f {tmp_path}/refus ] && exit 1; touch {jeton}')
    _faux(b, "grub-mdp",
          f'if [ "$1" = --etat ]; then [ -f {verrou} ]; exit $?; fi; '
          f'echo "grub $*" >> {journal}; cat > {tmp_path}/grub-stdin; touch {verrou}')

    def jouer():
        env = dict(os.environ,
                   PATH=f"{b}{os.pathsep}{os.environ.get('PATH', '')}",
                   BFOS_PROV=str(etat / "provisioning.json"),
                   BFOS_STATE_DIR=str(etat),
                   BFOS_CRYPTTAB=str(tmp_path / "crypttab"),
                   BFOS_GRUB_MDP=str(b / "grub-mdp"),
                   BFOS_DEV_FACTICE="1")
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                           text=True, timeout=30, check=False)
        appels = journal.read_text().splitlines() if journal.exists() else []
        return r, appels

    return SimpleNamespace(jouer=jouer, tmp=tmp_path, jeton=jeton, verrou=verrou)


def test_enrolement_neuf_verrouille_grub(poste):
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels[0].startswith("cryptenroll ")
    assert appels[1] == "grub --deja-verifiee"
    assert (poste.tmp / "grub-stdin").read_text() == PHRASE + "\n"
    assert not any(PHRASE in a for a in appels)          # jamais en argument
    assert "edition de GRUB verrouillee" in r.stdout


def test_enrolement_refuse_ne_touche_pas_grub(poste):
    (poste.tmp / "refus").write_text("")
    r, appels = poste.jouer()
    assert r.returncode == 1
    assert not any(a.startswith("grub") for a in appels)


def test_jeton_deja_la_et_grub_ouvert_le_dit(poste):
    poste.jeton.write_text("")
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels == []                                   # rien a refaire
    assert "GRUB sans mot de passe" in r.stderr
    assert "sudo /usr/libexec/bluefox-grub-password" in r.stderr


def test_jeton_deja_la_et_grub_verrouille_se_tait(poste):
    poste.jeton.write_text("")
    poste.verrou.write_text("")
    r, _ = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert "GRUB" not in r.stderr


def test_grub_qui_echoue_n_annule_pas_l_enrolement(poste):
    _faux(poste.tmp / "bin", "grub-mdp", 'cat >/dev/null; exit 1')
    r, _ = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert "mot de passe GRUB NON pose" in r.stderr
    assert (poste.tmp / "etat" / "tpm-enrolled").exists()


def test_l_unite_passe_sur_un_poste_enrole():
    """Relecture adverse : le temoin tpm-enrolled existe sur tout poste enrole,
    et ConditionPathExists=! sur lui empechait l'unite de jamais signaler un
    GRUB ouvert."""
    actives = [l for l in UNITE.read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not any(re.match(r"\s*ConditionPathExists=!.*tpm-enrolled", l) for l in actives)


def test_temoin_sans_jeton_ne_redemande_pas_la_phrase(poste):
    """Le comportement d'avant la levee de la condition : temoin present, pas
    de jeton (TPM remis a zero...) : aucune invite a chaque demarrage."""
    (poste.tmp / "etat" / "tpm-enrolled").write_text("")
    _faux(poste.tmp / "bin", "systemd-ask-password", "echo INVITE >&2; echo X")
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels == []
    assert "INVITE" not in r.stderr


def test_les_messages_donnent_un_chemin_que_sudo_trouve():
    """secure_path de sudo n'a pas /usr/libexec."""
    for f in (SCRIPT, REPO_ROOT / "files/usr/libexec/bluefox-grub-password"):
        for l in f.read_text().splitlines():
            if "sudo " in l and "bluefox-grub-password" in l:
                assert "sudo /usr/libexec/bluefox-grub-password" in l, l
