"""bluefox-seat-credentials : remise des identifiants Nextcloud au premier
demarrage, et effacement sur un poste partage (#28055).

Les cas qui comptent :

- Poste personnel : le fichier est remis a l'usager du siege (comportement
  d'avant, inchange).
- 🔴 Poste partage (bloc « seat ») : le fichier porte le mot de passe
  d'application de l'OPERATEUR qui a autorise l'installation (v1.0.2). Il n'a
  pas de destinataire : on l'efface, au lieu de le laisser en 0600 root
  indefiniment (mesure en VM le 2026-11-24).
- Politique illisible : on ne decide rien, ni remise ni effacement.

⚠️ Stdlib + pytest uniquement.
"""

import base64
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.dont_write_bytecode = True   # voir test_policy_sync.py : pas de .pyc dans files/

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-seat-credentials"


def _load():
    loader = importlib.machinery.SourceFileLoader("bluefox_seat_credentials",
                                                  str(SERVICE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _poste(tmp_path, monkeypatch, politique):
    mod = _load()
    fichier = tmp_path / "nc-credentials.json"
    fichier.write_text(json.dumps({"login": "Olivier", "app_password": "P"}))
    fichier.chmod(0o600)
    pol = tmp_path / "provisioning.json"
    if politique is not None:
        pol.write_text(politique if isinstance(politique, str)
                       else json.dumps(politique))
    monkeypatch.setattr(mod, "FICHIER", str(fichier))
    monkeypatch.setattr(mod, "POLITIQUE", str(pol))
    return mod, fichier


def test_poste_partage_efface_les_identifiants_de_l_operateur(tmp_path, monkeypatch):
    mod, fichier = _poste(tmp_path, monkeypatch, {
        "user": {"login": ""}, "seat": {"profile": "labo", "kind": "lab"}})
    monkeypatch.setattr(mod.os, "chown",
                        lambda *a: (_ for _ in ()).throw(AssertionError("remis !")))
    assert mod.main() == 0
    assert not fichier.exists()


def test_poste_partage_meme_avec_un_login_ne_remet_rien(tmp_path, monkeypatch):
    """Le bloc « seat » decide, pas la presence d'un login."""
    mod, fichier = _poste(tmp_path, monkeypatch, {
        "user": {"login": "olivier@exemple.com"}, "seat": {"profile": "pret"}})
    monkeypatch.setattr(mod.os, "chown",
                        lambda *a: (_ for _ in ()).throw(AssertionError("remis !")))
    assert mod.main() == 0
    assert not fichier.exists()


def test_poste_personnel_remet_a_l_usager(tmp_path, monkeypatch):
    mod, fichier = _poste(tmp_path, monkeypatch,
                          {"user": {"login": "olivier@exemple.com"}})
    vu = {}
    monkeypatch.setattr(mod.pwd, "getpwnam",
                        lambda nom: SimpleNamespace(pw_uid=2004, pw_gid=2004)
                        if nom == "olivier" else (_ for _ in ()).throw(KeyError(nom)))
    monkeypatch.setattr(mod.os, "chown",
                        lambda chemin, uid, gid: vu.update(uid=uid, gid=gid))
    assert mod.main() == 0
    assert fichier.exists()
    assert vu == {"uid": 2004, "gid": 2004}


def test_politique_illisible_ne_decide_rien(tmp_path, monkeypatch):
    mod, fichier = _poste(tmp_path, monkeypatch, "{pas du json")
    monkeypatch.setattr(mod.os, "chown",
                        lambda *a: (_ for _ in ()).throw(AssertionError("remis !")))
    assert mod.main() == 0
    assert fichier.exists()          # ni remis, ni efface


def test_politique_absente_ne_decide_rien(tmp_path, monkeypatch):
    mod, fichier = _poste(tmp_path, monkeypatch, None)
    assert mod.main() == 0
    assert fichier.exists()


def test_sans_fichier_rien_a_faire(tmp_path, monkeypatch):
    mod, fichier = _poste(tmp_path, monkeypatch, {"seat": {"profile": "labo"}})
    fichier.unlink()
    assert mod.main() == 0


# ------------------------------------------------ revocation (relecture adverse)
class _Rep:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _poste_avec_ident(tmp_path, monkeypatch, url="https://nc.exemple.test"):
    mod, fichier = _poste(tmp_path, monkeypatch, {"seat": {"profile": "labo"}})
    fichier.write_text(json.dumps({"login": "Olivier", "app_password": "MDP-OPERATEUR",
                                   "url": url}))
    return mod, fichier


def test_poste_partage_revoque_puis_efface(tmp_path, monkeypatch, capsys):
    mod, fichier = _poste_avec_ident(tmp_path, monkeypatch)
    vu = []
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda req, timeout: vu.append(req) or _Rep(200))
    assert mod.main() == 0
    assert not fichier.exists()
    (req,) = vu
    assert req.get_method() == "DELETE"
    assert req.full_url == "https://nc.exemple.test/ocs/v2.php/core/apppassword"
    assert req.get_header("Ocs-apirequest") == "true"
    assert req.get_header("Authorization") == "Basic " + base64.b64encode(
        b"Olivier:MDP-OPERATEUR").decode()
    sortie = capsys.readouterr().out
    assert "revoque dans Nextcloud" in sortie
    assert "MDP-OPERATEUR" not in sortie


def test_sans_reseau_on_efface_quand_meme_et_on_le_dit(tmp_path, monkeypatch, capsys):
    mod, fichier = _poste_avec_ident(tmp_path, monkeypatch)

    def coupe(req, timeout):
        raise OSError("reseau injoignable")
    monkeypatch.setattr(mod.urllib.request, "urlopen", coupe)
    assert mod.main() == 0
    assert not fichier.exists()
    assert "NON revoque" in capsys.readouterr().out


def test_jamais_en_http(tmp_path, monkeypatch):
    mod, fichier = _poste_avec_ident(tmp_path, monkeypatch, url="http://nc.exemple.test")
    monkeypatch.setattr(mod.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("mot de passe envoye en clair"))
    assert mod.main() == 0
    assert not fichier.exists()
