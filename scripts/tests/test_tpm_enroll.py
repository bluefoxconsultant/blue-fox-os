"""bluefox-tpm-enroll : l'option de demarrage qui rend le jeton TPM2 visible a
l'initramfs (#28056).

Mesure en VM le 2026-11-24 (v1.0.2, poste labo et amorce) : jeton enrole au
%post, crypttab a jour, et le disque redemandait sa phrase. Sur ostree,
l'initramfs est celui de l'image et ne lit pas /etc/crypttab ; il ne lit que
les arguments du noyau.

Les cas qui comptent :

- Jeton deja la, option absente : c'est l'etat de TOUTES les installations
  v1.0.2. Le service doit poser l'option, sans redemander la phrase.
- Option deja active : rien, et surtout pas un deploiement de plus.
- Option posee a CE demarrage-ci : pas un second `rpm-ostree kargs` (chacun
  cree un deploiement) ; au demarrage suivant, si elle n'est toujours pas
  active, on reessaie.
- rd.luks.uuid deja nomme par Anaconda : on ne le double pas.
- Echec de rpm-ostree : pas de marqueur, sortie en succes, nouvel essai.
- Enrolement neuf : la phrase est demandee, le jeton pose, puis l'option.

⚠️ Bash reel, commandes systeme remplacees par des faux dans le PATH.
"""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "files" / "usr" / "libexec" / "bluefox-tpm-enroll"
UNITE = REPO_ROOT / "files" / "usr" / "lib" / "systemd" / "system" / "bluefox-tpm-enroll.service"
UUID = "db5f5214-0e0f-4d4e-b0f0-fa39be654205"
OPT = f"rd.luks.options={UUID}=tpm2-device=auto"


def _faux(bin_dir, nom, corps):
    f = bin_dir / nom
    f.write_text("#!/bin/sh\n" + corps + "\n")
    f.chmod(0o755)


@pytest.fixture
def poste(tmp_path):
    """Un poste de papier : politique, crypttab, cmdline, boot_id, et des faux
    pour id, cryptsetup, rpm-ostree, systemd-*."""
    etat = tmp_path / "etat"
    etat.mkdir()
    (etat / "provisioning.json").write_text(
        '{"policies": {"tpm_autounlock": {"enabled": true, "pcrs": "7"}}}')
    (tmp_path / "crypttab").write_text(f"luks-{UUID} UUID={UUID} none discard\n")
    (tmp_path / "cmdline").write_text("BOOT_IMAGE=/vmlinuz root=UUID=x rhgb quiet\n")
    (tmp_path / "boot_id").write_text("boot-1\n")
    b = tmp_path / "bin"
    b.mkdir()
    journal = tmp_path / "appels"
    _faux(b, "id", 'echo 0')
    _faux(b, "logger", ':')
    _faux(b, "systemd-analyze", 'exit 0')
    jeton = tmp_path / "jeton"
    _faux(b, "cryptsetup",
          f'case "$1" in luksUUID) echo {UUID};; '
          f'luksDump) [ -f {jeton} ] && echo "  0: systemd-tpm2";; esac; exit 0')
    _faux(b, "rpm-ostree", f'echo "rpm-ostree $*" >> {journal}; '
                           f'[ -f {tmp_path}/rpm-ostree-echoue ] && exit 1; exit 0')
    _faux(b, "systemd-ask-password", 'echo PHRASE-DU-DISQUE')
    _faux(b, "systemd-cryptenroll", f'echo "cryptenroll $*" >> {journal}; touch {jeton}')

    def jouer():
        env = dict(os.environ,
                   PATH=f"{b}{os.pathsep}{os.environ.get('PATH', '')}",
                   BFOS_PROV=str(etat / "provisioning.json"),
                   BFOS_STATE_DIR=str(etat),
                   BFOS_CRYPTTAB=str(tmp_path / "crypttab"),
                   BFOS_CMDLINE=str(tmp_path / "cmdline"),
                   BFOS_BOOT_ID=str(tmp_path / "boot_id"),
                   BFOS_DEV_FACTICE="1")
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                           text=True, timeout=30, check=False)
        appels = journal.read_text().splitlines() if journal.exists() else []
        return r, appels

    return SimpleNamespace(jouer=jouer, tmp=tmp_path, etat=etat, jeton=jeton)


def test_v102_jeton_present_option_absente_pose_l_option(poste):
    poste.jeton.write_text("")
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels == [(f"rpm-ostree kargs --append-if-missing=rd.luks.uuid={UUID} "
                       f"--append-if-missing={OPT}")]
    assert not any("cryptenroll" in a for a in appels)   # pas de re-enrolement
    assert (poste.etat / "tpm-kargs-poses").read_text().strip() == "boot-1"
    assert (poste.etat / "tpm-enrolled").exists()


def test_option_active_ne_cree_pas_de_deploiement(poste):
    poste.jeton.write_text("")
    (poste.tmp / "cmdline").write_text(f"root=UUID=x rd.luks.uuid={UUID} {OPT} quiet\n")
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels == []


def test_option_globale_ou_combinee_compte(poste):
    poste.jeton.write_text("")
    (poste.tmp / "cmdline").write_text(
        f"rd.luks.uuid={UUID} rd.luks.options=discard,tpm2-device=auto\n")
    _, appels = poste.jouer()
    assert appels == []


def test_un_seul_essai_par_demarrage(poste):
    poste.jeton.write_text("")
    poste.jouer()
    _, appels = poste.jouer()                  # meme boot_id
    assert len(appels) == 1, appels
    (poste.tmp / "boot_id").write_text("boot-2\n")
    _, appels = poste.jouer()                  # redemarre, option toujours absente
    assert len(appels) == 2, appels


def test_rd_luks_uuid_d_anaconda_n_est_pas_double(poste):
    poste.jeton.write_text("")
    (poste.tmp / "cmdline").write_text(f"root=UUID=x rd.luks.uuid=luks-{UUID} quiet\n")
    _, appels = poste.jouer()
    assert appels == [f"rpm-ostree kargs --append-if-missing={OPT}"]


def test_echec_de_rpm_ostree_reessaie_plus_tard(poste):
    poste.jeton.write_text("")
    (poste.tmp / "rpm-ostree-echoue").write_text("")
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert len(appels) == 1
    assert not (poste.etat / "tpm-kargs-poses").exists()


def test_enrolement_neuf_puis_option(poste):
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels[0].startswith("cryptenroll ")
    assert appels[1] == (f"rpm-ostree kargs --append-if-missing=rd.luks.uuid={UUID} "
                         f"--append-if-missing={OPT}")
    assert (poste.tmp / "crypttab").read_text().split()[3] == "discard,tpm2-device=auto"
    assert not any("initramfs-etc" in a for a in appels)


def test_politique_desactivee_ne_touche_a_rien(poste):
    poste.jeton.write_text("")
    (poste.etat / "provisioning.json").write_text(
        '{"policies": {"tpm_autounlock": {"enabled": false}}}')
    r, appels = poste.jouer()
    assert r.returncode == 0
    assert appels == []


def test_l_unite_passe_meme_apres_le_marqueur_du_post():
    """Le %post pose tpm-enrolled : une condition sur ce marqueur empechait
    l'unite de jamais passer, donc de reparer quoi que ce soit."""
    texte = "\n".join(l for l in UNITE.read_text().splitlines()
                      if not l.lstrip().startswith("#"))
    assert "ConditionPathExists=!/var/lib/bluefox-welcome/tpm-enrolled" not in texte


# ------------------------------------------------ option_tpm (relecture adverse)
def _fonction(texte):
    """Le corps de option_tpm, sans l'indentation du kickstart."""
    import textwrap
    i = texte.index("option_tpm() {")
    debut = texte.rfind("\n", 0, i) + 1
    retrait = texte[debut:i]
    fin = texte.index("\n" + retrait + "}\n", i) + len(retrait) + 2
    return textwrap.dedent(texte[debut:fin])


KS = REPO_ROOT / "install" / "blue-fox-install.ks.template"


def test_les_deux_copies_d_option_tpm_sont_identiques():
    """Le %post ne peut pas executer le script de l'image : la fonction y est
    recopiee. Une copie qui deriverait poserait au %post autre chose que ce que
    le service repare ensuite."""
    assert _fonction(SCRIPT.read_text()) == _fonction(KS.read_text())


@pytest.mark.parametrize("cmdline,attendu", [
    ("root=UUID=x quiet", f"AJOUT {OPT}"),
    (f"rd.luks.options={UUID}=tpm2-device=auto", "ACTIVE"),
    (f"rd.luks.options={UUID}=discard,tpm2-device=auto", "ACTIVE"),
    ("rd.luks.options=tpm2-device=auto", "ACTIVE"),
    # relecture adverse : l'option d'un AUTRE volume ne compte pas
    ("rd.luks.options=99999999-ffff-4fff-8fff-ffffffffffff=discard,tpm2-device=auto",
     f"AJOUT {OPT}"),
    # relecture adverse : une option propre au volume remplace la generique
    (f"rd.luks.options=tpm2-device=auto rd.luks.options={UUID}=discard", "CONFLIT"),
    # generique sans tpm2 : on la reprend, sinon l'option propre la ferait perdre
    ("rd.luks.options=discard", f"AJOUT rd.luks.options={UUID}=discard,tpm2-device=auto"),
    # un mot qui contient seulement la chaine ne compte pas
    ("xrd.luks.options=tpm2-device=auto", f"AJOUT {OPT}"),
    ("rd.luks.options=tpm2-device=autox",
     f"AJOUT rd.luks.options={UUID}=tpm2-device=autox,tpm2-device=auto"),
])
def test_option_tpm(cmdline, attendu):
    corps = _fonction(SCRIPT.read_text())
    r = subprocess.run(["bash", "-c", corps + '\noption_tpm "$1" "$2"', "_", cmdline, UUID],
                       capture_output=True, text=True, timeout=10, check=False)
    assert r.stdout.strip() == attendu, r.stderr


def test_conflit_ne_pose_rien(poste):
    poste.jeton.write_text("")
    (poste.tmp / "cmdline").write_text(f"rd.luks.uuid={UUID} rd.luks.options={UUID}=discard\n")
    r, appels = poste.jouer()
    assert r.returncode == 0, r.stderr
    assert appels == []


def test_generique_reprise_dans_l_option_posee(poste):
    poste.jeton.write_text("")
    (poste.tmp / "cmdline").write_text(f"rd.luks.uuid={UUID} rd.luks.options=discard\n")
    _, appels = poste.jouer()
    assert appels == [f"rpm-ostree kargs --append-if-missing=rd.luks.options={UUID}=discard,tpm2-device=auto"]
