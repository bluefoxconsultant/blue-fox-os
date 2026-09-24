"""bluefox-flatpaks-verify : la liste voulue, et le code de sortie (#25854)."""
import importlib.machinery
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PATH = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-flatpaks-verify"
loader = importlib.machinery.SourceFileLoader("bluefox_flatpaks_verify", str(PATH))
spec = importlib.util.spec_from_loader("bluefox_flatpaks_verify", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


class _R:
    def __init__(self, stdout=""):
        self.stdout = stdout


class _Run(list):
    """Faux subprocess.run : `flatpak list` rend `installees`, et un
    `systemctl start` peut en installer d'autres (reparation)."""

    def __init__(self, installees, apres_reparation=None):
        super().__init__()
        self.installees = set(installees)
        self.apres = apres_reparation

    def __call__(self, argv, **kw):
        self.append(argv)
        if argv[:2] == ["systemctl", "start"] and self.apres is not None:
            self.installees = set(self.apres)
        return _R("\n".join(sorted(self.installees)) + "\n")


def _listes(tmp_path, image=(), image_remove=(), etc_install=(), etc_remove=()):
    img, etc = tmp_path / "img", tmp_path / "etc"
    img.mkdir()
    etc.mkdir()
    (img / "install").write_text("# commentaire\n" + "\n".join(image) + "\n\n")
    (img / "remove").write_text("\n".join(image_remove))
    (etc / "install").write_text("\n".join(etc_install))
    (etc / "remove").write_text("\n".join(etc_remove))
    return str(img), str(etc)


def test_liste_voulue_comme_l_amont(tmp_path):
    img, etc = _listes(tmp_path, image=["a.A", "b.B", "c.C"],
                       etc_install=["d.D", "a.A"], etc_remove=["b.B"])
    assert mod.voulues(img, etc) == ["a.A", "c.C", "d.D"]


def test_fichiers_absents_liste_vide(tmp_path):
    assert mod.voulues(str(tmp_path / "x"), str(tmp_path / "y")) == []


def test_tout_present_sort_en_succes_sans_rien_reveiller(tmp_path, monkeypatch):
    img, etc = _listes(tmp_path, image=["a.A"])
    monkeypatch.setattr(mod, "IMAGE_DIR", img)
    monkeypatch.setattr(mod, "ETC_DIR", etc)
    monkeypatch.setattr(mod, "manquantes",
                        lambda run: [a for a in mod.voulues(img, etc) if a not in mod.installees(run)])
    run = _Run(["a.A"])
    assert mod.main(["--repair"], run=run) == 0
    assert not any(a[:2] == ["systemctl", "start"] for a in run)


def test_manquante_sort_en_echec(tmp_path, monkeypatch):
    img, etc = _listes(tmp_path, image=["a.A", "b.B"])
    monkeypatch.setattr(mod, "manquantes",
                        lambda run: [a for a in mod.voulues(img, etc) if a not in mod.installees(run)])
    assert mod.main([], run=_Run(["a.A"])) == 1


def test_reparation_relance_puis_reverifie(tmp_path, monkeypatch):
    img, etc = _listes(tmp_path, image=["a.A", "b.B"])
    monkeypatch.setattr(mod, "manquantes",
                        lambda run: [a for a in mod.voulues(img, etc) if a not in mod.installees(run)])
    run = _Run(["a.A"], apres_reparation=["a.A", "b.B"])
    assert mod.main(["--repair"], run=run) == 0
    assert ["systemctl", "start", "system-flatpak-setup.service"] in run


def test_reparation_ratee_reste_en_echec(tmp_path, monkeypatch):
    img, etc = _listes(tmp_path, image=["a.A", "b.B"])
    monkeypatch.setattr(mod, "manquantes",
                        lambda run: [a for a in mod.voulues(img, etc) if a not in mod.installees(run)])
    assert mod.main(["--repair"], run=_Run(["a.A"], apres_reparation=["a.A"])) == 1
