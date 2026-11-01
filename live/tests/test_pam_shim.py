"""Essais de pam_bfos.so contre la vraie libpam (#26870).

Compiles a la volee ; exigent gcc, pam-devel et root (le module refuse une
aide qui n'appartient pas a root). Jouer dans le conteneur Fedora :

    docker run --rm -v $PWD:/src:ro bfos-live-essais:43 \
        bash -c 'cd /src/live && python3 -m pytest -q -p no:cacheprovider tests'
"""
import os
import shutil
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ICI = Path(__file__).resolve().parent
PAM_SUCCESS, PAM_AUTH_ERR, PAM_AUTHINFO_UNAVAIL, PAM_CONV_ERR = 0, 7, 9, 19

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or not shutil.which("gcc")
    or not Path("/usr/include/security/pam_modules.h").exists(),
    reason="exige root, gcc et pam-devel (conteneur bfos-live-essais)")


@pytest.fixture(scope="module", autouse=True)
def comptes():
    """Vrais comptes du conteneur : la cale verifie que le compte rendu existe
    et n'est pas un compte systeme."""
    for nom, uid in (("bob", 20001), ("alice", 20002)):
        subprocess.run(["useradd", "--uid", str(uid), nom], capture_output=True)
    yield


@pytest.fixture(scope="module")
def binaires(tmp_path_factory):
    d = tmp_path_factory.mktemp("bin")
    src = ICI.parent / "pam"
    # Le depot est monte en lecture seule : compiler hors de l'arbre.
    for cible, cmd in (
        ("pam_bfos.so", ["gcc", "-O2", "-fPIC", "-Wall", "-Wextra", "-Werror",
                         "-D_FORTIFY_SOURCE=3", "-shared", "-Wl,--no-undefined",
                         "-o", str(d / "pam_bfos.so"), str(src / "pam_bfos.c"), "-lpam"]),
        ("pam_harness", ["gcc", "-Wall", "-Wextra", "-Werror", "-o", str(d / "pam_harness"),
                         str(ICI / "pam_harness.c"), "-lpam"]),
    ):
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, f"{cible}: {r.stderr}"
    return d


def aide(tmp_path, corps, nom="aide"):
    p = tmp_path / nom
    p.write_text("#!/usr/bin/python3\nimport os, sys\n" + textwrap.dedent(corps))
    p.chmod(0o755)
    return p


def jouer(binaires, tmp_path, aide_p, reponses="", action="auth", user="placeholder",
          service="greetd", timeout=30, preexec_fn=None):
    conf = tmp_path / "pam.d"
    conf.mkdir(exist_ok=True)
    so = binaires / "pam_bfos.so"
    (conf / service).write_text(
        f"auth required {so} helper={aide_p}\n"
        f"session required {so} helper={aide_p}\n")
    r = subprocess.run([str(binaires / "pam_harness"), str(conf), service, user, action],
                       input=reponses, capture_output=True, text=True, timeout=timeout,
                       preexec_fn=preexec_fn)
    lignes = [tuple(x.split("\t")) for x in r.stdout.splitlines()]
    resultat = [x for x in lignes if x[0] == "RESULT"]
    assert resultat, f"pas de RESULT : {r.stdout!r} {r.stderr!r}"
    return lignes[:-1], int(resultat[-1][1]), resultat[-1][2]


def test_relaie_dialogue_et_pose_l_usager(binaires, tmp_path):
    a = aide(tmp_path, r'''
        print("INFO bfos:device {}\\nligne 2 avec \\\\ barre", flush=True)
        print("SECRET Code ?", flush=True)
        rep = sys.stdin.readline().rstrip("\n")
        print("INFO recu:" + rep, flush=True)
        print("USER bob", flush=True)
        print("OK", flush=True)
    ''')
    msgs, rc, user = jouer(binaires, tmp_path, a, reponses="1234\n")
    assert rc == PAM_SUCCESS and user == "bob"
    assert msgs[0] == ("INFO", "bfos:device {}\\nligne 2 avec \\\\ barre")
    assert msgs[1] == ("SECRET", "Code ?")
    assert msgs[2] == ("INFO", "recu:1234")


def test_reponse_avec_saut_de_ligne_et_barre(binaires, tmp_path):
    a = aide(tmp_path, r'''
        print("ASK Nom ?", flush=True)
        rep = sys.stdin.readline().rstrip("\n")
        print("INFO " + rep, flush=True)
        print("FAIL fin", flush=True)
    ''')
    msgs, rc, _ = jouer(binaires, tmp_path, a, reponses="a\\b\n")
    # La reponse « a\b » arrive echappee « a\\b » a l'aide, qui la renvoie
    # telle quelle : le module la desechappe une fois, le banc la reechappe.
    assert msgs[1] == ("INFO", "a\\\\b")
    assert rc == PAM_AUTH_ERR


def test_fail_refuse_et_garde_l_usager(binaires, tmp_path):
    a = aide(tmp_path, 'print("ERROR Le code a expire.", flush=True); print("FAIL expire", flush=True)')
    msgs, rc, user = jouer(binaires, tmp_path, a)
    assert rc == PAM_AUTH_ERR and user == "placeholder"
    assert msgs == [("ERROR", "Le code a expire.")]


@pytest.mark.parametrize("corps", [
    'print("OK", flush=True)',                                  # OK sans USER
    'print("USER Root!", flush=True); print("OK", flush=True)',  # nom invalide
    'print("USER " + "a"*40, flush=True); print("OK", flush=True)',
    'print("USER a", flush=True); print("USER b", flush=True); print("OK", flush=True)',
    'print("USER bob", flush=True); print("OK", flush=True); sys.exit(3)',  # sortie non nulle
    'print("USER bob", flush=True); print("BIZARRE", flush=True); print("OK", flush=True)',
    'print("USER bob", flush=True)',                            # meurt sans OK
    'sys.exit(0)',
])
def test_jamais_de_succes_sans_ok_propre(binaires, tmp_path, corps):
    _msgs, rc, user = jouer(binaires, tmp_path, aide(tmp_path, corps))
    assert rc == PAM_AUTH_ERR and user == "placeholder"


def test_annulation_tue_l_aide(binaires, tmp_path):
    pidf = tmp_path / "pid"
    a = aide(tmp_path, f'''
        open("{pidf}", "w").write(str(os.getpid()))
        print("ASK Continuer ?", flush=True)
        sys.stdin.readline()
        import time; time.sleep(60)
    ''')
    t = time.monotonic()
    _msgs, rc, _ = jouer(binaires, tmp_path, a, reponses="CANCEL\n")
    assert rc == PAM_CONV_ERR
    assert time.monotonic() - t < 10
    pid = int(pidf.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_aide_tuee_si_l_application_meurt(binaires, tmp_path):
    """greetd tue le processus de session a l'annulation : l'aide ne doit pas
    rester a interroger Authentik en orpheline (PR_SET_PDEATHSIG)."""
    pidf = tmp_path / "pid"
    a = aide(tmp_path, f'''
        open("{pidf}", "w").write(str(os.getpid()))
        print("INFO attente", flush=True)
        import time; time.sleep(60)
    ''')
    conf = tmp_path / "pam.d"
    conf.mkdir()
    (conf / "greetd").write_text(f"auth required {binaires / 'pam_bfos.so'} helper={a}\n")
    p = subprocess.Popen([str(binaires / "pam_harness"), str(conf), "greetd", "x", "auth"],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().startswith("INFO")
    pid = int(pidf.read_text())
    p.send_signal(signal.SIGKILL)
    p.wait()
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("l'aide a survecu a l'application")


def test_environnement_propre(binaires, tmp_path):
    a = aide(tmp_path, r'''
        cles = sorted(k for k in os.environ)
        print("INFO " + ",".join(cles), flush=True)
        print("INFO " + os.environ["PAM_SERVICE"] + "/" + os.environ["PAM_USER"], flush=True)
        fds = sorted(int(f) for f in os.listdir("/proc/self/fd"))
        print("INFO " + ",".join(map(str, fds)), flush=True)
        print("FAIL fin", flush=True)
    ''')
    env = dict(os.environ, SECRET_DE_L_APPELANT="fuite", LD_PRELOAD="")
    conf = tmp_path / "pam.d"
    conf.mkdir()
    (conf / "login").write_text(f"auth required {binaires / 'pam_bfos.so'} helper={a}\n")
    r = subprocess.run([str(binaires / "pam_harness"), str(conf), "login", "alice", "auth"],
                       capture_output=True, text=True, env=env, timeout=20)
    infos = [x.split("\t")[1] for x in r.stdout.splitlines() if x.startswith("INFO")]
    assert set(infos[0].split(",")) <= {"PATH", "LANG", "PAM_SERVICE", "PAM_USER", "PAM_TTY",
                                        "PAM_RHOST", "LC_CTYPE"}
    assert infos[1] == "login/alice"
    # stdin, stdout, stderr et le descripteur que listdir ouvre lui-meme.
    assert len(infos[2].split(",")) <= 4


@pytest.mark.parametrize("mode", [0o775, 0o757])
def test_aide_inscriptible_refusee(binaires, tmp_path, mode):
    a = aide(tmp_path, 'print("USER bob"); print("OK")')
    a.chmod(mode)
    _msgs, rc, _ = jouer(binaires, tmp_path, a)
    assert rc == PAM_AUTHINFO_UNAVAIL


def test_aide_d_un_autre_proprietaire_refusee(binaires, tmp_path):
    a = aide(tmp_path, 'print("USER bob"); print("OK")')
    os.chown(a, 1000, 1000)
    _msgs, rc, _ = jouer(binaires, tmp_path, a)
    assert rc == PAM_AUTHINFO_UNAVAIL


def test_aide_absente(binaires, tmp_path):
    _msgs, rc, _ = jouer(binaires, tmp_path, tmp_path / "absente")
    assert rc == PAM_AUTHINFO_UNAVAIL


def test_fermeture_lance_close_avec_l_usager(binaires, tmp_path):
    marque = tmp_path / "marque"
    a = aide(tmp_path, f'''
        open("{marque}", "w").write(sys.argv[1] + ":" + os.environ.get("PAM_USER", ""))
    ''')
    _msgs, rc, _ = jouer(binaires, tmp_path, a, action="close", user="olivier")
    assert rc == PAM_SUCCESS
    assert marque.read_text() == "close:olivier"


def test_fermeture_ne_bloque_jamais(binaires, tmp_path):
    a = aide(tmp_path, 'sys.exit(5)')
    _msgs, rc, _ = jouer(binaires, tmp_path, a, action="close", user="olivier")
    assert rc == PAM_SUCCESS


def test_vraie_aide_sans_configuration(binaires, tmp_path):
    """La vraie aide, lancee par le vrai module : sans live.json elle refuse
    proprement, avec un message lisible et sans pile Python."""
    dest = tmp_path / "libexec"
    dest.mkdir()
    shutil.copy(ICI.parent / "bfos_live" / "pam_helper.py", dest / "pam-helper")
    shutil.copy(ICI.parent.parent / "install" / "bfos_provision.py", dest / "bfos_provision.py")
    # Comme dans l'image : le paquet bfos_live (langues comprises) a cote de l'aide.
    shutil.copytree(ICI.parent / "bfos_live", dest / "bfos_live",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (dest / "pam-helper").chmod(0o755)
    if Path("/etc/bfos-live/live.json").exists():
        pytest.skip("une vraie configuration est presente")
    msgs, rc, user = jouer(binaires, tmp_path, dest / "pam-helper")
    assert rc == PAM_AUTH_ERR and user == "placeholder"
    assert msgs == [("ERROR", "Cette clé n'est pas configurée.")]
    assert stat.S_ISREG((dest / "pam-helper").stat().st_mode)


# ----------------------------------------------------------- verrouillage
def test_verrouillage_un_autre_compte_ne_sort_pas(binaires, tmp_path):
    """Session d'alice verrouillee : meme si l'aide rend bob, refus."""
    a = aide(tmp_path, 'print("USER bob", flush=True); print("OK", flush=True)')
    _m, rc, user = jouer(binaires, tmp_path, a, user="alice", service="kde")
    assert rc == PAM_AUTH_ERR and user == "alice"


def test_verrouillage_le_meme_compte_deverrouille(binaires, tmp_path):
    a = aide(tmp_path, 'print("USER alice", flush=True); print("OK", flush=True)')
    _m, rc, user = jouer(binaires, tmp_path, a, user="alice", service="kde")
    assert rc == PAM_SUCCESS and user == "alice"


@pytest.mark.parametrize("nom", ["root", "bin", "personne-inconnue"])
def test_jamais_un_compte_systeme_ou_absent(binaires, tmp_path, nom):
    a = aide(tmp_path, f'print("USER {nom}", flush=True); print("OK", flush=True)')
    _m, rc, user = jouer(binaires, tmp_path, a)
    assert rc == PAM_AUTH_ERR and user == "placeholder"


def test_fermeture_bornee_sans_reseau(binaires, tmp_path):
    a = aide(tmp_path, 'import time; time.sleep(120)')
    t = time.monotonic()
    _m, rc, _ = jouer(binaires, tmp_path, a, action="close", user="olivier", timeout=40)
    assert rc == PAM_SUCCESS
    assert time.monotonic() - t < 25


def test_annulation_meme_si_l_application_ignore_sigterm(binaires, tmp_path):
    pidf = tmp_path / "pid"
    a = aide(tmp_path, f'''
        open("{pidf}", "w").write(str(os.getpid()))
        print("ASK Continuer ?", flush=True)
        sys.stdin.readline()
        import time; time.sleep(60)
    ''')

    def ignorer():
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    t = time.monotonic()
    _m, rc, _ = jouer(binaires, tmp_path, a, reponses="CANCEL\n", preexec_fn=ignorer)
    assert rc == PAM_CONV_ERR and time.monotonic() - t < 10
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidf.read_text()), 0)
