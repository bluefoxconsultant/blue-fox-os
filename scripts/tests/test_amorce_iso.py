"""L'entree zero-touch de l'ISO demarre sur l'amorce (/bfos-amorce.ks).

Deux choses a garder vraies : le kickstart d'amorce rendu est celui qu'Anaconda
peut lire en deux passes, et brand-iso.sh construit une entree qui le cite sur
le bon volume, sans reste de l'ancienne entree a domaine en dur.
"""
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "install"))

import bfos_amorce  # noqa: E402
import render_amorce_ks  # noqa: E402

BRAND_ISO = REPO_ROOT / "scripts" / "brand-iso.sh"
TEMPLATE = REPO_ROOT / "install" / "bfos-amorce.ks.template"
LABEL = "Fedora-S-dvd-x86_64-44"

# Extrait du menu BIOS reel de l'ISO du 2026-09-11, brandee AVANT l'amorce :
# l'entree zero-touch a domaine en dur est en tete, les entrees d'origine
# suivent. Rebrander cette ISO doit remplacer l'entree, pas en empiler une.
MENU_AVANT_AMORCE = f"""menuentry 'Installer Blue Fox OS (zero-touch)' --class fedora --class gnu-linux {{
    # Domaine en dur : `read` n' existe pas dans le grub UEFI.
    set bf_domain="bluefoxconsultant.com"
\tlinux /images/pxeboot/vmlinuz inst.stage2=hd:LABEL={LABEL} inst.ks=https://${{bf_domain}}/blue-fox-install.ks quiet ip=dhcp
\tinitrd /images/pxeboot/initrd.img
}}

set timeout=60
search --no-floppy --set=root -l '{LABEL}'

menuentry 'Install Blue Fox OS 44' --class fedora --class gnu-linux --class gnu --class os {{
\tlinux /images/pxeboot/vmlinuz inst.stage2=hd:LABEL={LABEL} inst.ks=hd:LABEL={LABEL}:/osbuild.ks quiet
\tinitrd /images/pxeboot/initrd.img
}}
"""


def _fonction(nom):
    corps = BRAND_ISO.read_text()
    m = re.search(rf"(?ms)^{nom}\(\) \{{.*?^\}}", corps)
    assert m, f"{nom} introuvable dans brand-iso.sh"
    return m.group(0)


def _prepend(cfg: pathlib.Path):
    script = "set -euo pipefail\nlog() { :; }\n" + _fonction("prepend_zerotouch_entry") \
        + f'\nprepend_zerotouch_entry "{cfg}"\n'
    subprocess.run(["bash", "-c", script], check=True)
    return cfg.read_text()


def _entrees_zero_touch(menu):
    return re.findall(r"(?ms)^menuentry 'Installer Blue Fox OS \(zero-touch\)'.*?^\}", menu)


def test_entree_zero_touch_demarre_sur_lamorce_du_meme_volume(tmp_path):
    cfg = tmp_path / "grub.cfg"
    cfg.write_text(MENU_AVANT_AMORCE)
    menu = _prepend(cfg)

    entrees = _entrees_zero_touch(menu)
    assert len(entrees) == 1
    assert menu.startswith("menuentry 'Installer Blue Fox OS (zero-touch)'")
    linux = next(l for l in entrees[0].splitlines() if l.strip().startswith("linux "))
    assert re.findall(r"inst\.ks=\S+", linux) == [f"inst.ks=hd:LABEL={LABEL}:/bfos-amorce.ks"]
    assert f"inst.stage2=hd:LABEL={LABEL}" in linux
    assert " ip=dhcp" in linux and " rd.neednet=1" in linux
    # Plus rien de l'ancienne entree, ni de ce que le grub UEFI ne sait pas faire.
    assert "bf_domain" not in menu
    assert not re.search(r"^\s*read\s", menu, re.M)
    # L'entree d'origine garde son kickstart embarque.
    assert f"inst.ks=hd:LABEL={LABEL}:/osbuild.ks" in menu


def test_rebrander_ne_cumule_rien(tmp_path):
    cfg = tmp_path / "grub.cfg"
    cfg.write_text(MENU_AVANT_AMORCE)
    une_fois = _prepend(cfg)
    deux_fois = _prepend(cfg)
    assert une_fois == deux_fois
    assert len(_entrees_zero_touch(deux_fois)) == 1


def test_etiquette_echappee_reprise_telle_quelle(tmp_path):
    cfg = tmp_path / "grub.cfg"
    cfg.write_text("menuentry 'Install' {\n\tlinux /images/pxeboot/vmlinuz "
                   "inst.stage2=hd:LABEL=Fedora\\x2044 quiet\n\tinitrd /images/pxeboot/initrd.img\n}\n")
    linux = next(l for l in _entrees_zero_touch(_prepend(cfg))[0].splitlines()
                 if l.strip().startswith("linux "))
    assert "inst.ks=hd:LABEL=Fedora\\x2044:/bfos-amorce.ks" in linux


def test_verification_exige_lamorce_dans_les_menus_et_dans_liso():
    corps = BRAND_ISO.read_text()
    verif = _fonction("verify_menu")
    assert ":/bfos-amorce\\.ks" in verif
    assert "bf_domain" in verif  # refuse un reste d'avant l'amorce
    assert "-extract /bfos-amorce.ks" in corps
    assert 'MAPS+=(-map "${AMORCE_KS}" /bfos-amorce.ks)' in corps


# ---------------------------------------------------------------------------
# Kickstart d'amorce rendu
# ---------------------------------------------------------------------------
def test_rendu_embarque_le_script_et_le_domaine_propose():
    rendu = render_amorce_ks.render("BlueFoxConsultant.com")
    assert (REPO_ROOT / "install" / "bfos_amorce.py").read_text().rstrip("\n") in rendu
    assert "--domaine-defaut 'bluefoxconsultant.com'" in rendu
    assert "{{" not in rendu.replace((REPO_ROOT / "install" / "bfos_amorce.py").read_text(), "")


def test_rendu_refuse_un_domaine_propose_invalide():
    with pytest.raises(SystemExit):
        render_amorce_ks.render("pas un domaine")


def test_amorce_sans_aucune_commande():
    """Hors du bloc pre, seulement des commentaires et l'include : une commande
    ici serait lue une seconde fois depuis le kickstart de l'organisation."""
    blocs, reste = bfos_amorce.separer_pre(render_amorce_ks.render("bluefoxconsultant.com"))
    assert len(blocs) == 1
    assert "--erroronfail" in blocs[0][0]
    utiles = [l for l in reste.splitlines() if l.strip() and not l.startswith("#")]
    assert utiles == [f"%include /tmp/{bfos_amorce.KS_LOCATAIRE}"]


def test_ksvalidator_en_ci_lit_lamorce():
    workflow = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "render_amorce_ks.py" in workflow
    assert "ksvalidator /tmp/amorce-rendered.ks" in workflow


def test_deux_passes_danaconda_avec_pykickstart(tmp_path, monkeypatch):
    """La semantique dont l'amorce depend, rejouee avec le vrai parseur :
    premiere passe = blocs pre seuls, include absent ignore ; seconde passe =
    les blocs post du fichier inclus sont bien lus, et aucun bloc pre de
    l'organisation n'y reapparait."""
    pytest.importorskip("pykickstart")
    from pykickstart.constants import KS_SCRIPT_POST, KS_SCRIPT_PRE
    from pykickstart.parser import KickstartParser, NullSection, PreScriptSection, Script
    from pykickstart.version import makeVersion
    import render_zerotouch_ks

    inclus = tmp_path / "bfos-locataire.ks"
    amorce = render_amorce_ks.render("bluefoxconsultant.com").replace(
        f"/tmp/{bfos_amorce.KS_LOCATAIRE}", str(inclus))
    ks_amorce = tmp_path / "bfos-amorce.ks"
    ks_amorce.write_text(amorce)

    class PreParser(KickstartParser):  # copie d'AnacondaPreParser
        def handleCommand(self, lineno, args):
            pass

        def setupSections(self):
            # Anaconda passe dataObj=AnacondaKSScript ; sans dataObj, la section
            # lit le bloc et n'enregistre rien.
            self.registerSection(PreScriptSection(self.handler, dataObj=Script))
            for s in ("%pre-install", "%post", "%onerror", "%traceback", "%packages",
                      "%addon", "%certificate"):
                self.registerSection(NullSection(self.handler, sectionOpen=s))

    pre = PreParser(makeVersion(), missingIncludeIsFatal=False)
    pre.readKickstart(str(ks_amorce))
    assert [s.type for s in pre.handler.scripts] == [KS_SCRIPT_PRE]

    ks_bf = render_zerotouch_ks.render("bf", generated_at="2026-01-01T00:00:00Z")
    blocs, reste = bfos_amorce.separer_pre(ks_bf)
    inclus.write_text(reste)
    # Les includes du locataire (/tmp/bfos-autopart.ks...) viennent de ses
    # blocs pre : ici on les tolere absents, comme l'ISO les trouverait ecrits.
    complet = KickstartParser(makeVersion(), missingIncludeIsFatal=False)
    complet.readKickstart(str(ks_amorce))
    types = [s.type for s in complet.handler.scripts]
    assert types.count(KS_SCRIPT_PRE) == 1  # celui de l'amorce, pas celui du locataire
    assert types.count(KS_SCRIPT_POST) == len(re.findall(r"^%post", ks_bf, re.M))
    assert complet.handler.ostreecontainer.url.startswith("ghcr.io/bluefoxconsultant/")
