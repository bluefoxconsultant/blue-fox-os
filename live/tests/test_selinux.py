"""Le module SELinux de la cle doit viser l'aide que la cale PAM execute.

Sans ce module, l'aide tourne dans le domaine de greetd (xdm_t), qui ne peut
pas ecrire /etc/shadow : useradd echoue apres un QR approuve (#26870,
premier essai sur vraie machine du 2026-10-22). Les essais du conteneur
tournent sans SELinux et ne le verraient pas.
"""
import pathlib
import re

LIVE = pathlib.Path(__file__).resolve().parent.parent


def _helper_par_defaut():
    c = (LIVE / "pam" / "pam_bfos.c").read_text()
    return re.search(r'#define BFOS_HELPER_DEFAUT "([^"]+)"', c).group(1)


def _lignes_fc():
    return [l.split() for l in (LIVE / "selinux" / "bfos_live.fc").read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def test_le_module_etiquette_l_aide_de_la_cale():
    aide = [l for l in _lignes_fc() if l[0] == _helper_par_defaut()]
    assert len(aide) == 1 and "bfos_live_helper_exec_t" in aide[0][-1]


def test_l_ecran_peut_deposer_sa_langue():
    """L'ecran (xdm_t) ecrit son choix de langue ; xdm_t gere xdm_var_run_t (#26870, 2026-10-27)."""
    ecran = [l for l in _lignes_fc() if l[0].startswith("/run/bfos-live-ecran")]
    assert len(ecran) == 1 and "xdm_var_run_t" in ecran[0][-1]
    tmp = (LIVE / "image" / "files" / "usr" / "lib" / "tmpfiles.d" / "bfos-live.conf").read_text()
    assert "d /run/bfos-live-ecran 0755 greetd greetd -" in tmp


def test_greetd_transite_vers_le_domaine_de_l_aide():
    te = (LIVE / "selinux" / "bfos_live.te").read_text()
    assert "domtrans_pattern(xdm_t, bfos_live_helper_exec_t, bfos_live_helper_t)" in te
    assert "domain_entry_file(bfos_live_helper_t, bfos_live_helper_exec_t)" in te


def test_l_image_pose_le_module_et_l_iso_verifie_l_etiquette():
    cf = (LIVE / "image" / "Containerfile").read_text()
    assert "semodule -i /usr/share/selinux/packages/bfos_live.pp" in cf
    assert "COPY live/selinux/" in cf
    iso = (LIVE / "image" / "build-iso.sh").read_text()
    assert f"{_helper_par_defaut()}:bfos_live_helper_exec_t" in iso
