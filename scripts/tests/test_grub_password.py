"""bluefox-grub-password : verrou de l'edition GRUB par la phrase du disque (#28058).

Les cas qui comptent :

- L'empreinte a la forme de grub2-mkpasswd-pbkdf2 (verifie le 2026-11-25 contre
  l'outil de l'image : une empreinte de mkpasswd passe verifier()).
- Poste deja installe : la phrase est ESSAYEE sur le volume avant d'etre posee.
  Une phrase qui n'ouvre pas le disque ne pose rien : sinon GRUB serait
  verrouille par un secret que le sequestre ne connait pas.
- La phrase n'est jamais en argument d'une commande.
- /boot en lecture seule : remonte en ecriture, puis rendu en lecture seule.

⚠️ Stdlib + pytest uniquement.
"""

import errno
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTIL = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-grub-password"
PHRASE = "K7QM-2WXD-9PRT-HZ4E-NB6C-1AJV"


def _load():
    loader = importlib.machinery.SourceFileLoader("bluefox_grub_password", str(OUTIL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def poste(tmp_path, monkeypatch):
    mod = _load()
    grub = tmp_path / "grub2"
    grub.mkdir()
    crypttab = tmp_path / "crypttab"
    crypttab.write_text("luks-abc UUID=abc none discard\n")
    monkeypatch.setattr(mod, "GRUB_DIR", str(grub))
    monkeypatch.setattr(mod, "CRYPTTAB", str(crypttab))
    monkeypatch.setenv("BFOS_GRUB_DIR", str(grub))
    return mod, grub


def _stdin(monkeypatch, mod, texte):
    flux = io.StringIO(texte)
    flux.isatty = lambda: False
    monkeypatch.setattr(mod.sys, "stdin", flux)


def test_empreinte_verifiee():
    mod = _load()
    e = mod.empreinte(PHRASE)
    assert [len(x) for x in e.split(".")] == [4, 6, 6, 5, 128, 128]
    assert e == e.upper().replace("GRUB.PBKDF2.SHA512", "grub.pbkdf2.sha512")
    assert mod.verifier(PHRASE, e)
    assert not mod.verifier(PHRASE.lower(), e)
    assert not mod.verifier(PHRASE, "grub.pbkdf2.sha256.10000.AA.BB")
    assert not mod.verifier(PHRASE, "n'importe quoi")


def test_sel_neuf_a_chaque_pose():
    mod = _load()
    assert mod.empreinte(PHRASE) != mod.empreinte(PHRASE)


def test_deja_verifiee_pose_le_fichier(poste, monkeypatch):
    mod, grub = poste
    _stdin(monkeypatch, mod, PHRASE + "\n")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("aucune commande attendue"))
    assert mod.main(["--deja-verifiee"]) == 0
    cfg = grub / "user.cfg"
    (ligne,) = cfg.read_text().splitlines()
    cle, valeur = ligne.split("=", 1)
    assert cle == "GRUB2_PASSWORD"
    assert mod.verifier(PHRASE, valeur)
    assert PHRASE not in cfg.read_text()
    assert oct(cfg.stat().st_mode)[-3:] == "600"
    assert mod.etat()


def test_poste_installe_essaie_la_phrase_sur_le_disque(poste, monkeypatch):
    mod, grub = poste
    _stdin(monkeypatch, mod, PHRASE + "\n")
    vu = []

    def run(cmd, input=None, **k):
        vu.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod.main([]) == 0
    ((cmd, entree),) = vu
    assert cmd[:3] == ["cryptsetup", "open", "--test-passphrase"]
    assert cmd[-1] == "/dev/disk/by-uuid/abc"
    assert entree == PHRASE.encode()                # par stdin...
    assert not any(PHRASE in c for c in cmd)        # ...jamais en argument
    assert (grub / "user.cfg").exists()


def test_phrase_qui_n_ouvre_pas_le_disque_ne_pose_rien(poste, monkeypatch):
    mod, grub = poste
    _stdin(monkeypatch, mod, "MAUVAISE\n")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 2))
    assert mod.main([]) == 1
    assert not (grub / "user.cfg").exists()
    assert not mod.etat()


def test_sans_phrase_rien(poste, monkeypatch):
    mod, grub = poste
    _stdin(monkeypatch, mod, "\n")
    assert mod.main(["--deja-verifiee"]) == 1
    assert not (grub / "user.cfg").exists()


def test_sans_dossier_grub_rien(poste, monkeypatch, tmp_path):
    mod, _grub = poste
    monkeypatch.setattr(mod, "GRUB_DIR", str(tmp_path / "absent"))
    _stdin(monkeypatch, mod, PHRASE + "\n")
    assert mod.main(["--deja-verifiee"]) == 1


def test_boot_en_lecture_seule_remonte_puis_rend(poste, monkeypatch):
    mod, _grub = poste
    _stdin(monkeypatch, mod, PHRASE + "\n")
    vrai = mod._ecrire
    essais = []

    def ecrire(contenu):
        essais.append(1)
        if len(essais) == 1:
            raise OSError(errno.EROFS, "Read-only file system")
        vrai(contenu)
    monkeypatch.setattr(mod, "_ecrire", ecrire)
    vu = []
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **k: vu.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert mod.main(["--deja-verifiee"]) == 0
    assert vu == [["mount", "-o", "remount,rw", "/boot"], ["mount", "-o", "remount,ro", "/boot"]]
    assert mod.etat()


def test_etat_lit_le_fichier(poste, capsys):
    mod, grub = poste
    assert mod.main(["--etat"]) == 1
    assert capsys.readouterr().out.strip() == "ouvert"
    (grub / "user.cfg").write_text("GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.AA.BB\n")
    assert mod.main(["--etat"]) == 0
    assert capsys.readouterr().out.strip() == "verrouille"


def test_executable():
    assert os.access(OUTIL, os.X_OK)
    assert OUTIL.read_text().startswith("#!/usr/bin/env python3\n")
