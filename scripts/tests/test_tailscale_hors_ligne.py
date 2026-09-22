"""Le paquet Tailscale ne se telecharge plus PENDANT la construction (#25854).

🔴 « Curl error (56): Failure when receiving data from the peer » : le miroir
coupe en plein transfert depuis la machine de build, le paquet fait 39 Mio, et
rpm-ostree NE REESSAIE PAS. Le 2026-09-21, trois tours de suite ont tue bf et
bf-surface pendant que factice passait — le hasard, pas le code.

⚠️ Le correctif du 2026-09-14 versionnait le FICHIER .repo et annoncait qu'« une
coupure TLS ne tue plus la construction ». Vrai du fichier, faux du paquet.

⚠️ Et DNF5 n'a pas d'option `retries` (verifie dans sa documentation) : la
reprise ne peut pas vivre dans le .repo, elle vit cote hote.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
RECETTES = ("bf.yml", "bf-surface.yml", "factice.yml")


def test_aucune_recette_n_installe_tailscale_depuis_le_depot():
    for nom in RECETTES:
        lignes = (REPO / "recipes" / nom).read_text().splitlines()
        declare = [l for l in lignes if l.strip() == "- tailscale"]
        assert not declare, (
            f"{nom} tire encore tailscale du depot pendant la construction : "
            "une coupure TLS y tuera le locataire")


def test_chaque_recette_installe_le_rpm_depose():
    for nom in RECETTES:
        corps = (REPO / "recipes" / nom).read_text()
        assert "install-tailscale-rpm.sh" in corps, nom
        # Le fichier .repo reste : il porte l'URL que le script de depot lit.
        assert "tailscale.repo" in corps, nom


def test_le_telechargement_rejoue_l_erreur_56():
    """--retry seul NE rejoue PAS une erreur TLS ; il faut --retry-all-errors."""
    corps = (REPO / "scripts" / "stage_tailscale_rpm.py").read_text()
    assert "--retry-all-errors" in corps
    assert "--retry" in corps


def test_le_depot_est_verifie_avant_d_etre_cru():
    """Somme annoncee par le depot cote hote, signature dans l'image."""
    stage = (REPO / "scripts" / "stage_tailscale_rpm.py").read_text()
    assert "sha256" in stage and "somme SHA-256 differente" in stage
    script = (REPO / "files" / "scripts" / "install-tailscale-rpm.sh").read_text()
    assert "rpm -Kv" in script and ": OK" in script
    # L'identifiant de cle se LIT, il ne se code pas en dur (rpm 4/5/6 different).
    assert "gpg-pubkey" in script
    assert not re.search(r"KEY_RPM_ID=['\"][0-9a-f]{8,}", script)


def test_la_publication_depose_le_rpm_avant_de_construire():
    corps = (REPO / "scripts" / "publish-image.sh").read_text()
    assert "stage_tailscale_rpm.py" in corps
    i_stage = corps.index("stage_tailscale_rpm.py")
    i_build = corps.index("4/7")
    assert i_stage < i_build, "le depot doit preceder la construction"


# --- Reutilisation et rafales (2026-09-22) ------------------------------------
# bf puis bf-surface sont tombes a deux minutes d'intervalle, curl sorti en 35
# apres ses six reprises de trois secondes : la rafale du miroir durait plus
# longtemps. Et chaque locataire retelechargeait les 39 Mio que le precedent
# venait de verifier.
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402


def _module():
    spec = importlib.util.spec_from_file_location(
        "stage_tailscale_rpm", REPO / "scripts" / "stage_tailscale_rpm.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_un_paquet_deja_verifie_n_est_pas_retelecharge(tmp_path):
    mod = _module()
    cible = tmp_path / "tailscale.rpm"
    cible.write_bytes(b"paquet")
    bonne = hashlib.sha256(b"paquet").hexdigest()
    assert mod.deja_depose(cible, bonne)
    # Somme differente, fichier absent ou somme inconnue : on retelecharge.
    assert not mod.deja_depose(cible, "0" * 64)
    assert not mod.deja_depose(tmp_path / "absent.rpm", bonne)
    assert not mod.deja_depose(cible, "")


def test_curl_est_rejoue_apres_une_rafale(tmp_path, monkeypatch):
    mod = _module()
    appels, pauses = [], []

    def faux_run(args, check):
        appels.append(args)
        if len(appels) < 3:
            raise subprocess.CalledProcessError(35, args)

    monkeypatch.setattr(mod.subprocess, "run", faux_run)
    mod._telecharger("https://exemple/t.rpm", tmp_path / "t.rpm",
                     dormir=pauses.append)
    assert len(appels) == 3 and len(pauses) == 2


def test_curl_finit_par_echouer_bruyamment(tmp_path, monkeypatch):
    mod = _module()

    def toujours_35(args, check):
        raise subprocess.CalledProcessError(35, args)

    monkeypatch.setattr(mod.subprocess, "run", toujours_35)
    with pytest.raises(subprocess.CalledProcessError):
        mod._telecharger("https://exemple/t.rpm", tmp_path / "t.rpm",
                         dormir=lambda s: None)


def test_aucun_script_d_installation_ne_vide_le_staging_partage():
    """Premiere vraie construction depuis 301aca0 (2026-09-22) : factice meurt
    sur « [tailscale-rpm] ERREUR: RPM absent ». Le paquet etait bien depose ;
    install-welcome-rpm.sh, joue JUSTE AVANT, faisait `rm -rf` du dossier de
    staging entier. Chaque script retire son paquet, jamais le dossier."""
    for nom in ("install-welcome-rpm.sh", "install-tailscale-rpm.sh"):
        corps = (REPO / "files" / "scripts" / nom).read_text()
        code = "\n".join(l for l in corps.splitlines()
                         if not l.lstrip().startswith("#"))
        assert not re.search(r"rm\s+-[a-z]*r[a-z]*\s+\S*rpm-staging", code), nom
        assert 'rm -f "$RPM"' in code, nom
