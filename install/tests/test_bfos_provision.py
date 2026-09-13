import io
import json
import os
import urllib.error

import pytest

import bfos_provision as bp

DEVICE_URL = "https://auth.example.com/application/o/device/"
TOKEN_URL = "https://auth.example.com/application/o/token/"
POLICY_URL = "https://example.com/api/v1/policy/me"

# Ces tests decrivent un DISQUE VIERGE. Depuis #22419 le defaut n'est plus
# celui-la : sur une machine qui porte deja un systeme, le plan devient
# « interactif » et rien n'est ni tire ni depose. Le dire explicitement plutot
# que de dependre des disques de la machine qui fait tourner les tests.
PLAN_VIERGE = {"mode": "disque_entier", "disque": "/dev/sda"}

# Le prealable ci-dessous remplace bp.plan_par_defaut. Les tests qui veulent
# eprouver la VRAIE fonction passent par cette reference, prise a l'import.
PLAN_PAR_DEFAUT_REEL = bp.plan_par_defaut


@pytest.fixture(autouse=True)
def _disque_vierge(monkeypatch):
    """Sans ce prealable, l'inspection tournerait sur les VRAIS disques de la
    machine qui execute les tests : le resultat dependrait du poste, et sur un
    portable qui porte un systeme il deviendrait « interactif » — donc des
    tests d'escrow rouges pour une raison qui n'a rien a voir avec l'escrow.
    Un test qui a besoin d'un autre plan le passe explicitement."""
    monkeypatch.setattr(bp, "plan_par_defaut", lambda *a, **k: dict(PLAN_VIERGE))


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(body.encode()))


def _pending():
    raise _http_error(400, '{"error":"authorization_pending"}')


def _slow_down():
    raise _http_error(400, '{"error":"slow_down"}')


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


# ---------------------------------------------------------------- device_authorize
def test_device_authorize_ok():
    def post(url, data=None, timeout=30):
        assert url == DEVICE_URL
        assert data["client_id"] == "blue-fox-os"
        return 200, json.dumps({"device_code": "DC", "user_code": "WXYZ-ABCD",
                                "verification_uri": "https://auth/device",
                                "interval": 5, "expires_in": 300})
    d = bp.device_authorize(DEVICE_URL, "blue-fox-os", post=post)
    assert d["device_code"] == "DC"


def test_device_authorize_missing_code_raises():
    def post(url, data=None, timeout=30):
        return 200, json.dumps({"user_code": "X"})
    with pytest.raises(bp.ProvisionError):
        bp.device_authorize(DEVICE_URL, "c", post=post)


# ---------------------------------------------------------------- poll_token
def test_poll_token_success_after_pending():
    responses = [_pending, _pending,
                 (200, json.dumps({"access_token": "TOK"}))]
    it = iter(responses)

    def post(url, data=None, timeout=30):
        item = next(it)
        return item() if callable(item) else item

    clock = Clock()
    tok = bp.poll_token(TOKEN_URL, "c", "DC", interval=1, expires_in=300,
                        post=post, sleep=clock.sleep, now=clock.now)
    assert tok == "TOK"


def test_poll_token_slow_down_increases_wait():
    responses = [_slow_down, (200, json.dumps({"access_token": "TOK"}))]
    it = iter(responses)

    def post(url, data=None, timeout=30):
        item = next(it)
        return item() if callable(item) else item

    clock = Clock()
    bp.poll_token(TOKEN_URL, "c", "DC", interval=2, expires_in=1000,
                  post=post, sleep=clock.sleep, now=clock.now)
    # first sleep at base interval, second sleep bumped by slow_down (+5)
    assert clock.sleeps == [2, 7]


def test_poll_token_timeout():
    def post(url, data=None, timeout=30):
        _pending()

    clock = Clock()
    with pytest.raises(bp.ProvisionError):
        bp.poll_token(TOKEN_URL, "c", "DC", interval=5, expires_in=10,
                      post=post, sleep=clock.sleep, now=clock.now)


# ---------------------------------------------------------------- fetch_policy
def test_fetch_policy_ok():
    def get(url, token, timeout=30):
        assert token == "TOK"
        return 200, json.dumps({"schema": "bf-policy/v2",
                                "install": {}, "user": {"login": "x"}})
    assert bp.fetch_policy(POLICY_URL, "TOK", get=get)["schema"] == "bf-policy/v2"


def test_fetch_policy_non200_raises():
    def get(url, token, timeout=30):
        return 403, "nope"
    with pytest.raises(bp.ProvisionError):
        bp.fetch_policy(POLICY_URL, "TOK", get=get)


def test_fetch_policy_rejects_wrong_shape():
    def get(url, token, timeout=30):
        return 200, json.dumps({"schema": "bf-policy/v2"})  # no install/user
    with pytest.raises(bp.ProvisionError):
        bp.fetch_policy(POLICY_URL, "TOK", get=get)


# ---------------------------------------------------------------- run / main
def test_run_happy_path():
    def post(url, data=None, timeout=30):
        if url == DEVICE_URL:
            return 200, json.dumps({"device_code": "DC", "user_code": "WX",
                                    "verification_uri": "https://auth/device",
                                    "interval": 1, "expires_in": 300})
        if url == TOKEN_URL:
            return 200, json.dumps({"access_token": "TOK"})
        raise AssertionError(url)

    def get(url, token, timeout=30):
        assert (url, token) == (POLICY_URL, "TOK")
        return 200, json.dumps({"schema": "bf-policy/v2", "install": {},
                                "user": {"login": "olivier"}})

    env = {"BFOS_OIDC_DEVICE_URL": DEVICE_URL, "BFOS_OIDC_TOKEN_URL": TOKEN_URL,
           "BFOS_OIDC_CLIENT_ID": "blue-fox-os", "BFOS_POLICY_URL": POLICY_URL}
    policy = bp.run(env=env, post=post, get=get, sleep=lambda s: None,
                    out=lambda m: None)
    assert policy["user"]["login"] == "olivier"


def test_run_missing_config_raises():
    with pytest.raises(bp.ProvisionError):
        bp.run(env={}, out=lambda m: None)


def test_main_writes_fallback_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "STAGED_JSON", str(tmp_path / "p.json"))
    for var in ("BFOS_OIDC_DEVICE_URL", "BFOS_OIDC_TOKEN_URL",
                "BFOS_OIDC_CLIENT_ID", "BFOS_POLICY_URL"):
        monkeypatch.delenv(var, raising=False)
    assert bp.main([]) == 0
    data = json.loads((tmp_path / "p.json").read_text())
    assert data["fallback"] is True
    assert data["install"]["root"] == "locked"


def test_fallback_policy_uses_env():
    env = {"BFOS_FALLBACK_LANG": "en_CA.UTF-8", "BFOS_FALLBACK_KEYMAP": "us"}
    fb = bp.fallback_policy(env=env)
    assert fb["install"]["locale"] == "en_CA.UTF-8"
    assert fb["install"]["keymap"] == "us"


def test_main_chmods_staged_file(tmp_path, monkeypatch):
    staged = tmp_path / "p.json"
    monkeypatch.setattr(bp, "STAGED_JSON", str(staged))
    for var in ("BFOS_OIDC_DEVICE_URL", "BFOS_OIDC_TOKEN_URL",
                "BFOS_OIDC_CLIENT_ID", "BFOS_POLICY_URL"):
        monkeypatch.delenv(var, raising=False)
    assert bp.main([]) == 0
    assert oct(staged.stat().st_mode)[-3:] == "600"


# ------------------------------------------------------------------ enrolment
POLICY = {"schema": "bf-policy/v2", "install": {"hostname": "bf-olivier"},
          "user": {"login": "olivier"}}
ENROLL_URL = "https://example.com/api/v1/policy/enroll"


def _enrol_ok(url, payload, token, timeout=30):
    assert url == ENROLL_URL
    assert token == "TOK"
    assert payload["hostname"] == "bf-olivier"
    return 200, json.dumps({
        "machine_id": 7, "machine_uuid": payload["machine_uuid"],
        "token": "SECRET", "endpoint": "https://example.com/api/v1/policy/machine",
        "hostname": payload["hostname"], "user": "olivier"})


def test_enroll_url_derived_from_policy_url():
    assert bp.enroll_url_from(POLICY_URL) == ENROLL_URL
    assert bp.enroll_url_from(POLICY_URL + "/") == ENROLL_URL
    # Anything that isn't the /me route yields "" so the caller skips enrolment
    # rather than posting the operator's bearer at a guessed URL.
    assert bp.enroll_url_from("https://example.com/api/v1/policy") == ""
    assert bp.enroll_url_from("") == ""


def test_image_version_reads_os_release(tmp_path):
    p = tmp_path / "os-release"
    p.write_text('NAME="Blue Fox OS"\nVERSION_ID=43\nBUILD_ID="20260725"\n')
    assert bp.image_version(str(p)) == "43 (20260725)"
    p.write_text("VERSION_ID=43\n")
    assert bp.image_version(str(p)) == "43"
    assert bp.image_version(str(tmp_path / "absent")) == ""


def test_enrol_machine_happy_path():
    machine = bp.enrol_machine(ENROLL_URL, "TOK", POLICY, post=_enrol_ok,
                               new_uuid=lambda: "uuid-1234-5678", os_version="43")
    assert machine["token"] == "SECRET"
    assert machine["machine_uuid"] == "uuid-1234-5678"
    assert machine["endpoint"].endswith("/api/v1/policy/machine")
    assert machine["schema"] == "bf-machine/v1"
    assert machine["hostname"] == "bf-olivier"


def test_enrol_machine_draws_a_fresh_uuid_each_time():
    seen = set()
    for _ in range(3):
        seen.add(bp.enrol_machine(ENROLL_URL, "TOK", POLICY, post=_enrol_ok,
                                  os_version="")["machine_uuid"])
    assert len(seen) == 3


@pytest.mark.parametrize("status,body", [
    (403, '{"error":"machine revoked"}'),
    (500, "boom"),
])
def test_enrol_machine_non200_raises(status, body):
    with pytest.raises(bp.ProvisionError):
        bp.enrol_machine(ENROLL_URL, "TOK", POLICY,
                         post=lambda *a, **k: (status, body), os_version="")


@pytest.mark.parametrize("body", [
    "pas du json",
    '{"endpoint":"https://x/machine"}',   # jeton manquant
    '{"token":"SECRET"}',                  # endpoint manquant
])
def test_enrol_machine_rejects_bad_response(body):
    with pytest.raises(bp.ProvisionError):
        bp.enrol_machine(ENROLL_URL, "TOK", POLICY,
                         post=lambda *a, **k: (200, body), os_version="")


def test_stage_enrolment_writes_0600(tmp_path):
    path = tmp_path / "machine.json"
    env = {"BFOS_POLICY_URL": POLICY_URL}
    machine = bp.stage_enrolment("TOK", POLICY, env=env, post=_enrol_ok,
                                 path=str(path))
    assert machine and machine["token"] == "SECRET"
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert json.loads(path.read_text())["endpoint"].endswith("/policy/machine")


def test_stage_enrolment_skips_without_endpoint(tmp_path):
    path = tmp_path / "machine.json"
    said = []
    assert bp.stage_enrolment("TOK", POLICY, env={}, out=said.append,
                              post=_enrol_ok, path=str(path)) is None
    assert not path.exists()
    assert any("policy changes" in m for m in said)


def test_stage_enrolment_failure_leaves_no_file(tmp_path):
    path = tmp_path / "machine.json"
    def boom(*a, **k):
        raise OSError("réseau coupé")
    assert bp.stage_enrolment("TOK", POLICY, env={"BFOS_ENROLL_URL": ENROLL_URL},
                              post=boom, path=str(path)) is None
    assert not path.exists()


def test_run_hands_the_token_to_after_policy():
    def post(url, data=None, timeout=30):
        if url == DEVICE_URL:
            return 200, json.dumps({"device_code": "DC", "user_code": "WX",
                                    "verification_uri": "https://auth/device",
                                    "interval": 1, "expires_in": 300})
        return 200, json.dumps({"access_token": "TOK"})

    def get(url, token, timeout=30):
        return 200, json.dumps(POLICY)

    seen = {}
    env = {"BFOS_OIDC_DEVICE_URL": DEVICE_URL, "BFOS_OIDC_TOKEN_URL": TOKEN_URL,
           "BFOS_OIDC_CLIENT_ID": "blue-fox-os", "BFOS_POLICY_URL": POLICY_URL}
    policy = bp.run(env=env, post=post, get=get, sleep=lambda s: None,
                    out=lambda m: None,
                    after_policy=lambda tok, pol: seen.update(tok=tok, pol=pol))
    # L'enrolement ne peut avoir lieu qu'apres le fetch : c'est la politique qui
    # porte le nom d'hote, et le porteur est encore valide a ce moment-la.
    assert seen["tok"] == "TOK"
    assert seen["pol"] == policy


def test_main_does_not_enrol_when_provisioning_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "STAGED_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(bp, "MACHINE_JSON", str(tmp_path / "machine.json"))
    for var in ("BFOS_OIDC_DEVICE_URL", "BFOS_OIDC_TOKEN_URL",
                "BFOS_OIDC_CLIENT_ID", "BFOS_POLICY_URL"):
        monkeypatch.delenv(var, raising=False)
    assert bp.main([]) == 0
    # Sans device flow il n'y a aucun porteur : rien a enroler, et surtout pas
    # de fichier de secret vide qui ferait croire le contraire au service.
    assert not (tmp_path / "machine.json").exists()


# ------------------------------------- sequestre de la phrase de disque (#23940)
#
# Le test qui compte dans ce bloc n'est aucun des cas heureux : c'est
# `test_no_path_seals_a_disk_against_everyone`. Tout le reste decrit le
# mecanisme ; celui-la garde la propriete qui justifie de l'avoir ecrit.

POLICY_ESCROW = {
    "schema": "bf-policy/v2",
    "install": {"hostname": "bf-olivier"},
    "user": {"login": "olivier"},
    "policies": {"disk_escrow": {"enabled": True, "available": True}},
}


def _enrol_escrow_ok(url, payload, token, timeout=30):
    """Odoo accepte le depot. Renvoie disk_escrowed=True."""
    body = {
        "machine_id": 7, "machine_uuid": payload["machine_uuid"],
        "token": "SECRET", "endpoint": "https://example.com/api/v1/policy/machine",
        "hostname": payload["hostname"], "user": "olivier",
        "disk_escrowed": bool(payload.get("disk_passphrase")),
    }
    return 200, json.dumps(body)


def _enrol_escrow_refused(url, payload, token, timeout=30):
    """Enrolement valide, depot refuse : le cas ou il ne faut RIEN sceller."""
    return 200, json.dumps({
        "machine_id": 7, "machine_uuid": payload["machine_uuid"],
        "token": "SECRET", "endpoint": "https://example.com/api/v1/policy/machine",
        "hostname": payload["hostname"], "user": "olivier",
        "disk_escrowed": False,
        "disk_escrow_error": "aucune cle de sequestre configuree",
    })


def test_generate_disk_passphrase_shape():
    phrase = bp.generate_disk_passphrase()
    groups = phrase.split("-")
    assert len(groups) == 5
    assert all(len(g) == 5 for g in groups)
    # Aucune des formes qu'on confond en recopiant depuis un ecran.
    assert not (set(phrase) & set("IL01OU"))
    assert set(phrase) <= set(bp._PASSPHRASE_ALPHABET + "-")


def test_generate_disk_passphrase_is_not_a_constant():
    assert len({bp.generate_disk_passphrase() for _ in range(50)}) == 50


@pytest.mark.parametrize("policy,expected", [
    (POLICY_ESCROW, True),
    # Voulu mais pas branche cote serveur : on ne tire rien.
    ({"policies": {"disk_escrow": {"enabled": True, "available": False}}}, False),
    ({"policies": {"disk_escrow": {"enabled": False, "available": True}}}, False),
    ({"policies": {}}, False),
    ({}, False),
    ({"policies": {"disk_escrow": "oui"}}, False),
    (None, False),
])
def test_escrow_requested(policy, expected):
    assert bp.escrow_requested(policy) is expected


def test_write_autopart_forms_and_mode(tmp_path):
    path = tmp_path / "autopart.ks"
    bp.write_autopart(path=str(path))
    assert path.read_text().strip() == bp.AUTOPART_BASE
    assert "--passphrase" not in path.read_text()

    bp.write_autopart("ABCDE-FGHJK-MNPQR-STUVW-XYZ23", path=str(path))
    line = path.read_text().strip()
    assert line.startswith(bp.AUTOPART_BASE)
    assert line.endswith("--passphrase=ABCDE-FGHJK-MNPQR-STUVW-XYZ23")
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_la_cle_du_disque_ne_transite_jamais_par_un_fichier_lisible(tmp_path,
                                                                   monkeypatch):
    """L'assertion ci-dessus lit le mode FINAL, et elle etait verte sur du code
    qui creait le fichier au umask (0644) avant de le restreindre. Ici on garde
    le mode que le noyau applique A LA CREATION : pour la duree de l'install,
    ce fichier EST la cle du disque."""
    vus = []
    vrai_open = os.open

    def espion(p, flags, mode=0o777, **kw):
        vus.append(mode)
        return vrai_open(p, flags, mode, **kw)

    monkeypatch.setattr(os, "open", espion)
    path = tmp_path / "autopart.ks"
    bp.write_autopart("ABCDE-FGHJK-MNPQR-STUVW-XYZ23", path=str(path))

    assert vus and vus[-1] == 0o600


def test_un_autopart_deja_la_en_0644_est_ramene_a_600(tmp_path):
    """Le %pre reecrit un include que la version fautive a pu laisser en 0644 :
    O_CREAT ignore le mode sur un fichier existant, O_TRUNC ne le remet pas."""
    path = tmp_path / "autopart.ks"
    path.write_text("vieux\n")
    os.chmod(path, 0o644)

    bp.write_autopart("ABCDE-FGHJK-MNPQR-STUVW-XYZ23", path=str(path))

    assert oct(path.stat().st_mode)[-3:] == "600"
    assert "--passphrase=" in path.read_text()


def test_enrol_machine_omits_the_field_when_there_is_no_passphrase():
    seen = {}

    def post(url, payload, token, timeout=30):
        seen.update(payload)
        return _enrol_escrow_ok(url, payload, token, timeout)

    bp.enrol_machine(ENROLL_URL, "TOK", POLICY, post=post, os_version="")
    assert "disk_passphrase" not in seen


def test_enrol_machine_never_returns_the_passphrase():
    """Le dict rendu finit dans /etc/bluefox/machine.json sur le systeme installe."""
    machine = bp.enrol_machine(ENROLL_URL, "TOK", POLICY_ESCROW,
                               post=_enrol_escrow_ok, os_version="",
                               disk_passphrase="ABCDE-FGHJK-MNPQR-STUVW-XYZ23")
    assert machine["disk_escrowed"] is True
    assert "ABCDE" not in json.dumps(machine)


def test_stage_enrolment_writes_the_passphrase_once_odoo_confirms(tmp_path):
    autopart = tmp_path / "autopart.ks"
    bp.write_autopart(path=str(autopart))          # ce que fait le %pre
    said = []
    machine = bp.stage_enrolment("TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
        post=_enrol_escrow_ok, path=str(tmp_path / "machine.json"),
        autopart_path=str(autopart), out=said.append)

    line = autopart.read_text().strip()
    assert "--passphrase=" in line
    phrase = line.split("--passphrase=")[1]
    assert len(phrase.split("-")) == 5
    assert machine["disk_escrowed"] is True
    # Le secret de la machine est stage ; la phrase du disque, jamais.
    assert phrase not in (tmp_path / "machine.json").read_text()
    assert any("deposited" in m for m in said)


def test_stage_enrolment_leaves_the_prompt_when_escrow_is_off(tmp_path):
    autopart = tmp_path / "autopart.ks"
    bp.write_autopart(path=str(autopart))
    seen = {}

    def post(url, payload, token, timeout=30):
        seen.update(payload)
        return _enrol_ok(url, payload, token, timeout)

    bp.stage_enrolment("TOK", POLICY, env={"BFOS_ENROLL_URL": ENROLL_URL},
                       post=post, path=str(tmp_path / "machine.json"),
                       autopart_path=str(autopart))
    assert "disk_passphrase" not in seen
    assert autopart.read_text().strip() == bp.AUTOPART_BASE


def test_no_path_seals_a_disk_against_everyone(tmp_path):
    """La propriete a garder : sans confirmation d'Odoo, aucune phrase generee
    ne doit atteindre autopart. Trois facons d'echouer, un seul resultat."""
    scenarios = {
        "depot refuse": _enrol_escrow_refused,
        "erreur serveur": lambda *a, **k: (500, "boom"),
        "reseau coupe": lambda *a, **k: (_ for _ in ()).throw(OSError("nope")),
    }
    for label, post in scenarios.items():
        autopart = tmp_path / f"autopart-{abs(hash(label))}.ks"
        bp.write_autopart(path=str(autopart))      # ce que fait le %pre
        bp.stage_enrolment("TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=post, path=str(tmp_path / f"machine-{abs(hash(label))}.json"),
            autopart_path=str(autopart))
        assert autopart.read_text().strip() == bp.AUTOPART_BASE, (
            f"{label} : une phrase a ete scellee sans confirmation d'Odoo")


def test_stage_enrolment_says_why_the_deposit_was_refused(tmp_path):
    autopart = tmp_path / "autopart.ks"
    bp.write_autopart(path=str(autopart))
    said = []
    bp.stage_enrolment("TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
        post=_enrol_escrow_refused, path=str(tmp_path / "machine.json"),
        autopart_path=str(autopart), out=said.append)
    joined = "".join(said)
    assert "NOT deposited" in joined
    assert "aucune cle de sequestre configuree" in joined


# --- Contrat « after_policy ne leve jamais » (revue du 2026-08-01) -----------
#
# run() documente que ce seam ne doit pas lever, et main() traite une exception
# comme un echec TOTAL : il jette la politique deja obtenue et ecrit le repli.
# Le sequestre y avait insere trois appels nus, hors try. Une machine dont le
# fetch avait pourtant reussi se serait installee sur les valeurs de repli de
# l'org, sans enrolement, sans que rien ne le signale.

def test_stage_enrolment_never_raises_even_if_the_autopart_write_fails(
        tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(bp, "write_autopart", boom)
    said = []
    got = bp.stage_enrolment("TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
        post=_enrol_escrow_ok, path=str(tmp_path / "machine.json"),
        autopart_path=str(tmp_path / "autopart.ks"), out=said.append)
    assert got is None
    assert "no space left on device" in "".join(said)


def test_stage_enrolment_never_raises_on_a_malformed_policy(tmp_path):
    """`policies` non-mapping : .get dessus levait, et emportait tout."""
    said = []
    got = bp.stage_enrolment("TOK", {"schema": "bf-policy/v2", "policies": ["disk_escrow"]},
        env={"BFOS_ENROLL_URL": ENROLL_URL}, post=_enrol_escrow_ok,
        path=str(tmp_path / "machine.json"),
        autopart_path=str(tmp_path / "autopart.ks"), out=said.append)
    # Pas de phrase tiree, mais l'enrolement lui-meme aboutit.
    assert got is not None


def test_run_keeps_the_fetched_policy_when_the_seam_explodes():
    """Le point qui compte : la politique deja tiree survit a l'incident."""
    def post(url, data=None, timeout=30):
        if url == DEVICE_URL:
            return 200, json.dumps({"device_code": "DC", "user_code": "WX",
                                    "verification_uri": "https://auth/device",
                                    "interval": 1, "expires_in": 300})
        if url == TOKEN_URL:
            return 200, json.dumps({"access_token": "TOK"})
        raise AssertionError(url)

    def get(url, token, timeout=30):
        return 200, json.dumps({"schema": "bf-policy/v2", "install": {},
                                "user": {"login": "olivier"}})

    def exploding_post(*a, **k):
        raise RuntimeError("boom")

    env = {"BFOS_OIDC_DEVICE_URL": DEVICE_URL, "BFOS_OIDC_TOKEN_URL": TOKEN_URL,
           "BFOS_OIDC_CLIENT_ID": "blue-fox-os", "BFOS_POLICY_URL": POLICY_URL}
    policy = bp.run(
        env=env, post=post, get=get, sleep=lambda s: None, out=lambda m: None,
        after_policy=lambda tok, pol: bp.stage_enrolment(tok, pol, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=exploding_post))
    assert policy["user"]["login"] == "olivier"


@pytest.mark.parametrize("policies", [[], "oui", 42, ["disk_escrow"], 0.5])
def test_escrow_requested_survives_a_non_mapping_policies_block(policies):
    """_valid_policy ne controle jamais `policies` : un serveur qui repond une
    liste ou une chaine arrive ici intact."""
    assert bp.escrow_requested({"policies": policies}) is False


def test_escrow_requested_survives_a_non_dict_policy():
    assert bp.escrow_requested("bf-policy/v2") is False


# --- L'aveu quand la jambe retour se perd (revue du 2026-08-01) --------------
#
# Odoo commet le depot AVANT de repondre. Une reponse perdue laisse donc
# peut-etre une fiche qui affirme detenir une phrase que le disque n'utilisera
# pas : l'operateur va taper la sienne. Dire « NOT deposited » a ce moment-la,
# c'est empecher la seule personne capable de le remarquer de regarder.

def test_lost_reply_does_not_claim_the_deposit_failed(tmp_path):
    def dead_socket(*a, **k):
        raise OSError("timed out")

    said = []
    bp.stage_enrolment("TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
        post=dead_socket, path=str(tmp_path / "machine.json"),
        autopart_path=str(tmp_path / "autopart.ks"), out=said.append)
    joined = "".join(said)
    assert "NOT deposited" not in joined
    assert "may or may not have been recorded" in joined
    assert "clear it" in joined


# --- La ligne autopart existe en deux exemplaires (revue du 2026-08-01) ------
#
# Le %pre du gabarit ecrit le filet, bfos_provision.py porte AUTOPART_BASE, et
# rien ne les reliait. La porte CI qui aurait attrape la derive ne la voit plus
# non plus : ksvalidator tourne sans --followincludes, donc la ligne autopart
# du parcours zero-touch n'est plus validee. Ces deux tests remplacent la porte
# perdue.

def _template_text():
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent
    return (here / "blue-fox-install.ks.template").read_text()


def test_le_filet_du_gabarit_ne_peut_rien_detruire():
    """⚠️ CE TEST A CHANGE DE SENS LE 2026-09-12.

    Il exigeait que le filet du %pre soit exactement AUTOPART_BASE, pour que
    les machines dont le depot reussit et celles qui retombent sur la saisie
    manuelle aient le meme format de disque. Cette exigence supposait un
    disque a nous : elle signifiait « efface le disque quoi qu'il arrive ».
    Sur une machine qui porte Windows, elle decrivait une perte de donnees.

    L'invariant est renverse : le filet ecrit AVANT que quoi que ce soit
    puisse echouer ne doit contenir AUCUNE directive de partitionnement.
    C'est bfos_provision.py, apres inspection des disques, qui a le droit d'y
    mettre un autopart — jamais le gabarit d'avance.
    """
    texte = _template_text()
    i = texte.index("> /tmp/bfos-autopart.ks")
    debut = texte.rindex("printf", 0, i)
    ligne = texte[debut:i]
    for interdit in ("autopart", "clearpart", "zerombr", "part ", "--resize"):
        assert interdit not in ligne, (
            f"le filet ecrit {interdit!r} avant toute inspection : sur une "
            "machine qui porte deja un systeme, c'est une perte de donnees")
    assert "#" in ligne, "le filet doit ecrire un commentaire, pas du vide"


def test_the_template_carries_no_bare_autopart_line():
    """La ligne doit venir du %include, sinon Anaconda lit celle du gabarit et
    le sequestre n'a aucun effet."""
    text = _template_text()
    for line in text.splitlines():
        if line.strip().startswith("autopart"):
            raise AssertionError(
                f"ligne autopart nue dans le gabarit : {line.strip()!r}")
    assert "%include /tmp/bfos-autopart.ks" in text


# ------------------------------------ chiffrement, TPM et repli (2026-09-11)
def test_tpm2_present_regarde_le_noeud(tmp_path):
    absent = tmp_path / "pas-de-tpm"
    present = tmp_path / "tpmrm0"
    present.write_text("")
    assert bp.tpm2_present((str(absent),)) is False
    assert bp.tpm2_present((str(absent), str(present))) is True


def test_avec_tpm_on_chiffre_et_on_enrole_sans_rien_demander():
    dit = []
    assert bp.decider_chiffrement(dit.append, tpm=True) == (True, True)
    assert dit == [], "aucune question ne doit etre posee quand le TPM est la"


def test_sans_tpm_le_refus_explicite_laisse_en_clair():
    dit = []
    assert bp.decider_chiffrement(dit.append, tpm=False,
                                  lire=lambda: "2\n") == (False, False)
    assert "NON chiffre" in "".join(dit)


def test_sans_tpm_le_defaut_est_de_chiffrer():
    """Se tromper vers le chiffrement ne coute que du confort ; l'inverse non."""
    for reponse in ("", "\n", "1", "oui", "n'importe quoi"):
        assert bp.decider_chiffrement(lambda _m: None, tpm=False,
                                      lire=lambda r=reponse: r) == (True, False)


def test_sans_tpm_une_entree_fermee_chiffre_quand_meme():
    """Une install pilotee sans console ne doit pas rester bloquee, ni finir
    en clair par accident."""
    def lire_qui_leve():
        raise OSError("stdin ferme")
    assert bp.decider_chiffrement(lambda _m: None, tpm=False,
                                  lire=lire_qui_leve) == (True, False)


def test_write_autopart_en_clair_ne_porte_aucune_phrase(tmp_path):
    ap = tmp_path / "autopart.ks"
    bp.write_autopart(path=str(ap), chiffrer=False)
    ligne = ap.read_text().strip()
    assert ligne == bp.AUTOPART_CLAIR
    assert "--encrypted" not in ligne and "--passphrase" not in ligne


def test_stage_enrolment_en_clair_ne_sequestre_rien(tmp_path):
    """⚠️ L'ordre compte : decider apres l'enrolement sequestrerait une phrase
    pour un disque laisse en clair, et la fiche machine mentirait."""
    ap = tmp_path / "autopart.ks"
    vus = []

    def post_espion(url, payload, token, timeout=30):
        vus.append(payload)
        return _enrol_escrow_ok(url, payload, token, timeout=timeout)

    bp.stage_enrolment("TOK", POLICY_ESCROW,
                       env={"BFOS_ENROLL_URL": ENROLL_URL}, post=post_espion,
                       path=str(tmp_path / "machine.json"),
                       autopart_path=str(ap), out=lambda _m: None,
                       decider=lambda _out: (False, False))
    assert ap.read_text().strip() == bp.AUTOPART_CLAIR
    assert vus and not vus[0].get("disk_passphrase")


def test_stage_enrolment_pose_le_marqueur_tpm(tmp_path):
    ap = tmp_path / "autopart.ks"
    marqueur = tmp_path / "tpm-enrol"
    bp.stage_enrolment("TOK", POLICY_ESCROW,
                       env={"BFOS_ENROLL_URL": ENROLL_URL},
                       post=_enrol_escrow_ok,
                       path=str(tmp_path / "machine.json"),
                       autopart_path=str(ap), out=lambda _m: None,
                       decider=lambda _out: (True, True),
                       marqueur_tpm=str(marqueur))
    assert marqueur.exists(), "le %post ne saurait pas qu'il doit enroler"
    assert "--passphrase=" in ap.read_text()


def test_pas_de_marqueur_tpm_quand_l_enrolement_n_est_pas_voulu(tmp_path):
    ap = tmp_path / "autopart.ks"
    marqueur = tmp_path / "tpm-enrol"
    bp.stage_enrolment("TOK", POLICY_ESCROW,
                       env={"BFOS_ENROLL_URL": ENROLL_URL},
                       post=_enrol_escrow_ok,
                       path=str(tmp_path / "machine.json"),
                       autopart_path=str(ap), out=lambda _m: None,
                       decider=lambda _out: (True, False),
                       marqueur_tpm=str(marqueur))
    assert not marqueur.exists()


# ------------------------------------- compte de secours local (2026-09-11)
def test_compte_verrouille_par_defaut(tmp_path):
    """Le filet : Anaconda est satisfait, mais rien n'est ouvert."""
    c = tmp_path / "compte.ks"
    bp.write_compte(path=str(c))
    ligne = c.read_text().strip()
    assert ligne == "user --name=bfos-secours --groups=wheel --lock"
    assert oct(c.stat().st_mode)[-3:] == "600"


def test_compte_ouvert_sur_la_phrase_du_disque(tmp_path):
    c = tmp_path / "compte.ks"
    bp.write_compte("PWSND-4KX7M-Q2RTB-9HJVC-ZE6YA", path=str(c))
    ligne = c.read_text().strip()
    assert "--plaintext --password=PWSND-4KX7M-Q2RTB-9HJVC-ZE6YA" in ligne
    assert "--lock" not in ligne


def test_une_phrase_de_forme_inattendue_laisse_le_compte_verrouille(tmp_path):
    """⚠️ Un kickstart invalide n'installe RIEN : c'est bien pire que l'absence
    de compte de secours. La garde prefere verrouiller et le dire."""
    c = tmp_path / "compte.ks"
    for mauvaise in ("avec espace", 'avec"guillemet', "saut\nligne", "point;virgule"):
        dit = []
        bp.write_compte(mauvaise, path=str(c), out=dit.append)
        assert c.read_text().strip().endswith("--lock"), mauvaise
        assert "verrouille" in "".join(dit)


def test_stage_enrolment_ouvre_le_compte_quand_odoo_confirme(tmp_path):
    ap = tmp_path / "autopart.ks"
    compte = tmp_path / "compte.ks"
    bp.write_compte(path=str(compte))          # le filet, comme le %pre
    bp.stage_enrolment("TOK", POLICY_ESCROW,
                       env={"BFOS_ENROLL_URL": ENROLL_URL},
                       post=_enrol_escrow_ok,
                       path=str(tmp_path / "machine.json"),
                       autopart_path=str(ap), compte_path=str(compte),
                       out=lambda _m: None,
                       decider=lambda _out: (True, False))
    ligne = compte.read_text().strip()
    assert "--plaintext --password=" in ligne
    # La MEME phrase que le disque : un seul secret, un seul bouton Reveler.
    phrase_disque = ap.read_text().split("--passphrase=")[1].strip()
    assert ligne.endswith(phrase_disque)


def test_le_compte_reste_verrouille_si_le_depot_echoue(tmp_path):
    """Un compte ouvert sur une phrase qu'Odoo ne detient pas serait une porte
    muree : personne ne pourrait la reveler."""
    ap = tmp_path / "autopart.ks"
    compte = tmp_path / "compte.ks"
    bp.write_compte(path=str(compte))
    bp.stage_enrolment("TOK", POLICY_ESCROW,
                       env={"BFOS_ENROLL_URL": ENROLL_URL},
                       post=_enrol_escrow_refused,
                       path=str(tmp_path / "machine.json"),
                       autopart_path=str(ap), compte_path=str(compte),
                       out=lambda _m: None,
                       decider=lambda _out: (True, False))
    assert compte.read_text().strip().endswith("--lock")


# ===========================================================================
# Code QR et ecran d'autorisation (#22419 — rendu de l'etape d'installation)
# ===========================================================================
#
# Pourquoi des vecteurs d'or plutot qu'un aller-retour : relire le QR avec le
# meme parcours que celui qui l'a ecrit serait circulaire — le test passerait
# au vert meme avec un encodeur faux. La validation reelle s'est faite contre
# zxing-cpp (decodeur independant) sur les 106 longueurs ; ces empreintes
# figent la sortie exacte que ce decodeur a relue correctement.
#
# Si un de ces tests casse apres une modification de l'encodeur, ne PAS
# regenerer l'empreinte : revalider d'abord contre un vrai decodeur.


class TestCodeQR:
    GOLDEN = {
        "https://auth.bluefoxconsultant.com/device?code=119271062":
            (33, "37bde5a745ae8ebcdbec4b34ca843f204479a3911418e40087b1d867c7a6e1f3"),
        "https://auth.bluefoxconsultant.com/device":
            (29, "3fa2f972083443e55fd41683ff52a766e78dfbb08c9bf281c195898ade9a24f4"),
        "BFOS":
            (21, "d6ff61017e6ea9ba7b8ebdde10a24ede93eeb68d9ef7589a0593ff2cad8a0daa"),
        "https://auth.bluefoxconsultant.com/device?code=000000001":
            (33, "ca31952ea3268017da40100089fa9623f50d50a0a100cfb671630c2538f06e64"),
    }

    def test_matrices_conformes_aux_vecteurs_valides_par_un_decodeur(self):
        import hashlib
        for texte, (taille, empreinte) in self.GOLDEN.items():
            m = bp._qr_encode(texte)
            assert len(m) == taille, texte
            plat = "".join("".join(str(v) for v in ligne) for ligne in m)
            assert hashlib.sha256(plat.encode()).hexdigest() == empreinte, texte

    def test_motifs_de_reperage_aux_trois_coins(self):
        m = bp._qr_encode("BFOS")
        n = len(m)
        for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):
            assert m[r0][c0] == 1
            assert m[r0 + 3][c0 + 3] == 1          # coeur plein
            assert m[r0 + 1][c0 + 1] == 0          # anneau clair
            assert m[r0 + 6][c0 + 6] == 1

    def test_module_sombre_toujours_present(self):
        for v in (1, 4):
            texte = "x" * (10 if v == 1 else 60)
            m = bp._qr_encode(texte)
            assert m[len(m) - 8][8] == 1

    def test_refuse_au_dela_de_la_capacite(self):
        with pytest.raises(ValueError):
            bp._qr_encode("x" * 107)

    def test_rendu_demi_blocs_moitie_moins_haut_que_large(self):
        lignes, largeur = bp.render_qr_lines("BFOS")
        assert all(len(l) == largeur for l in lignes)
        assert len(lignes) == (largeur + 1) // 2
        assert set("".join(lignes)) <= {" ", "▀", "▄", "█"}

    def test_zone_de_silence_presente(self):
        lignes, largeur = bp.render_qr_lines("BFOS", quiet=4)
        # les deux premieres lignes = 4 modules clairs = 2 lignes vides
        assert lignes[0].strip() == ""
        assert lignes[1].strip() == ""
        assert lignes[-1].strip() == ""


class TestEcranAutorisation:
    D = {
        "verification_uri": "https://auth.bluefoxconsultant.com/device",
        "verification_uri_complete":
            "https://auth.bluefoxconsultant.com/device?code=119271062",
        "user_code": "119271062",
    }

    def _plat(self, **kw):
        ecran = bp.composer_ecran(self.D, **kw)
        return [bp._sans_ansi(l)
                for l in ecran.rstrip("\n").split("\n")]

    def test_tient_dans_une_console_80x25(self):
        lignes = self._plat()
        assert len(lignes) <= 24, f"{len(lignes)} lignes"
        assert max(len(l) for l in lignes) <= 78

    def test_le_code_et_l_adresse_restent_lisibles_en_texte(self):
        # Le QR s'AJOUTE au code, il ne le remplace pas : sans appareil photo,
        # sans police adequate, l'installation doit rester terminable.
        texte = "\n".join(self._plat())
        assert "auth.bluefoxconsultant.com/device" in texte
        assert "1 1 9   2 7 1   0 6 2" in texte

    def test_le_qr_encode_l_adresse_avec_le_code(self):
        # et non l'adresse nue : balayer ne doit pas obliger a taper le code.
        lignes, _ = bp.render_qr_lines(self.D["verification_uri_complete"])
        attendu, _ = bp.render_qr_lines(
            "https://auth.bluefoxconsultant.com/device?code=119271062")
        assert lignes == attendu

    def test_sans_qr_le_code_survit(self):
        lignes = self._plat(qr=False)
        texte = "\n".join(lignes)
        assert "1 1 9   2 7 1   0 6 2" in texte
        assert self.D["verification_uri_complete"] in texte

    def test_encre_noire_sur_fond_blanc(self):
        # Une console est blanche sur noir : dessine tel quel, le QR serait
        # INVERSE et certains appareils photo le refusent.
        ecran = bp.composer_ecran(self.D)
        assert "\x1b[30;47m" in ecran
        assert "\x1b[0m" in ecran

    def test_groupement_du_code(self):
        assert bp._grouper_code("119271062") == "1 1 9   2 7 1   0 6 2"
        assert bp._grouper_code("ABCD-EFGH") == "A B C   D E F   G H"
        assert bp._grouper_code("") == ""

    def test_repli_en_pile_si_le_qr_est_trop_large(self, monkeypatch):
        # Une adresse plus longue donne un QR de version superieure ; plutot
        # que de deborder a 80 colonnes, on empile.
        monkeypatch.setattr(bp, "_LARGEUR", 40)
        lignes = self._plat()
        # empile = plus aucune ligne ne porte a la fois le dessin et le texte
        melangees = [l for l in lignes
                     if set(l) & {"▀", "▄", "█"} and "Balayez" in l]
        assert melangees == []
        joint = "\n".join(lignes)
        assert "1 1 9   2 7 1   0 6 2" in joint
        assert self.D["verification_uri_complete"] in joint


class TestEcritureConsole:
    def test_le_dessin_ne_casse_pas_un_stderr_ascii(self, tmp_path, monkeypatch):
        """Le piege qui transformait un ecran plus joli en echec d'installation.

        En %pre la locale est souvent ASCII. Avant correction, le `print` vers
        stderr etait HORS du try : un seul caractere de dessin levait
        UnicodeEncodeError, qui remontait jusqu'au garde-fou general et faisait
        basculer toute l'installation sur la politique de repli.
        """
        import io
        import sys

        class StderrAscii(io.TextIOBase):
            def __init__(self):
                self.buffer = io.BytesIO()

            def write(self, s):
                s.encode("ascii")  # leve comme le ferait une locale ASCII
                return len(s)

        faux = StderrAscii()
        monkeypatch.setattr(bp, "CONSOLE", str(tmp_path / "console"))
        monkeypatch.setattr(sys, "stderr", faux)
        ecrire = bp._console_writer()
        ecrire("█▀▄ essai")  # ne doit rien lever
        assert "essai" in faux.buffer.getvalue().decode("utf-8")

    def test_le_journal_ne_recoit_pas_les_sequences_ansi(self):
        assert bp._sans_ansi("\x1b[30;47mx\x1b[0m") == "x"


# ===========================================================================
# Identifiants Nextcloud obtenus en arriere-plan (#22436)
# ===========================================================================

POLITIQUE_NC = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "services": {"nextcloud": {
        "url": "https://nextcloud.exemple.com/",
        "oidc_client_id": "uK6cwJwhy",
    }},
}
ENV_NC = {
    "BFOS_OIDC_TOKEN_URL": "https://auth.exemple.com/application/o/token/",
    "BFOS_OIDC_CLIENT_ID": "blue-fox-os",
}


def _ocs(data, statuscode=200):
    return json.dumps({"ocs": {"meta": {"status": "ok",
                                        "statuscode": statuscode,
                                        "message": "OK"},
                               "data": data}})


class TestEchangeDeJeton:
    def test_envoie_bien_un_echange_rfc8693(self):
        vu = {}

        def post(url, data, timeout=30):
            vu["url"] = url
            vu["data"] = data
            return 200, json.dumps({"access_token": "JETON-NC"})

        jeton = bp.echanger_jeton(ENV_NC["BFOS_OIDC_TOKEN_URL"], "blue-fox-os",
                                  "JETON-INSTALL", "uK6cwJwhy", post=post)
        assert jeton == "JETON-NC"
        assert vu["data"]["grant_type"] == \
            "urn:ietf:params:oauth:grant-type:token-exchange"
        assert vu["data"]["subject_token"] == "JETON-INSTALL"
        assert vu["data"]["audience"] == "uK6cwJwhy"

    def test_refus_http(self):
        with pytest.raises(bp.ProvisionError):
            bp.echanger_jeton("u", "c", "t", "a",
                              post=lambda *a, **k: (400, '{"error":"invalid_target"}'))

    def test_reponse_sans_jeton(self):
        with pytest.raises(bp.ProvisionError):
            bp.echanger_jeton("u", "c", "t", "a",
                              post=lambda *a, **k: (200, "{}"))


class TestChargeOCS:
    def test_un_refus_rendu_en_200_reste_un_refus(self):
        """⚠️ Le piege. OCS rend un echec dans le CORPS avec un 200 HTTP tout
        autant qu'avec un 401 : lire le code d'etat seul ferait prendre un
        refus pour un succes, et frapper un mot de passe inexistant."""
        raw = json.dumps({"ocs": {"meta": {"status": "failure",
                                           "statuscode": 997,
                                           "message": "Current user is not logged in"},
                                  "data": []}})
        with pytest.raises(bp.ProvisionError) as e:
            bp._charge_ocs(raw)
        assert "997" in str(e.value)

    def test_100_et_200_passent(self):
        assert bp._charge_ocs(_ocs({"id": "Olivier"}, 100)) == {"id": "Olivier"}
        assert bp._charge_ocs(_ocs({"id": "Olivier"}, 200)) == {"id": "Olivier"}


class TestIdentifiantsNextcloud:
    def _get(self, reponses):
        appels = []

        def get(url, token, timeout=30):
            appels.append((url, token))
            for motif, rep in reponses.items():
                if motif in url:
                    return rep
            raise AssertionError(f"URL inattendue : {url}")

        get.appels = appels
        return get

    def test_parcours_complet(self, tmp_path):
        cible = tmp_path / "nc.json"
        get = self._get({
            "cloud/user": (200, _ocs({"id": "Olivier"})),
            "getapppassword": (200, _ocs({"apppassword": "MDP-APPLICATION"})),
        })
        stage = bp.obtenir_identifiants_nextcloud(
            "JETON-INSTALL", POLITIQUE_NC, env=ENV_NC,
            post=lambda *a, **k: (200, json.dumps({"access_token": "JETON-NC"})),
            get=get, path=str(cible))
        assert stage == {"login": "Olivier",
                         "app_password": "MDP-APPLICATION",
                         "url": "https://nextcloud.exemple.com"}
        assert json.loads(cible.read_text())["app_password"] == "MDP-APPLICATION"
        # un mot de passe d'application ne se depose pas en lisible par tous
        assert oct(os.stat(cible).st_mode & 0o777) == "0o600"
        # les deux appels portent le jeton ECHANGE, pas celui de l'installation
        assert {t for _, t in get.appels} == {"JETON-NC"}

    def test_le_login_vient_de_nextcloud_pas_de_la_politique(self, tmp_path):
        """⚠️ L'annuaire sert « Olivier », la politique porte « olivier@... ».
        Deviner la casse suffirait a ecrire un rclone.conf qui ne monte rien."""
        cible = tmp_path / "nc.json"
        stage = bp.obtenir_identifiants_nextcloud(
            "T", POLITIQUE_NC, env=ENV_NC,
            post=lambda *a, **k: (200, json.dumps({"access_token": "J"})),
            get=self._get({"cloud/user": (200, _ocs({"id": "Olivier"})),
                           "getapppassword": (200, _ocs({"apppassword": "P"}))}),
            path=str(cible))
        assert stage["login"] == "Olivier"          # et non "olivier"

    def test_sans_client_id_on_ne_demande_rien(self, tmp_path):
        cible = tmp_path / "nc.json"
        politique = json.loads(json.dumps(POLITIQUE_NC))
        del politique["services"]["nextcloud"]["oidc_client_id"]
        messages = []
        stage = bp.obtenir_identifiants_nextcloud(
            "T", politique, env=ENV_NC, out=messages.append,
            post=lambda *a, **k: pytest.fail("aucun appel ne doit partir"),
            path=str(cible))
        assert stage is None
        assert not cible.exists()
        assert "oidc_client_id" in " ".join(messages)

    def test_ne_leve_jamais(self, tmp_path):
        """C'est un after_policy : run() documente que rien n'a le droit d'en
        remonter. Une exception ici jetterait une politique deja en main."""
        messages = []
        def post_qui_explose(*a, **k):
            raise RuntimeError("reseau coupe")
        stage = bp.obtenir_identifiants_nextcloud(
            "T", POLITIQUE_NC, env=ENV_NC, out=messages.append,
            post=post_qui_explose, path=str(tmp_path / "nc.json"))
        assert stage is None
        assert "SSO" in " ".join(messages)

    def test_un_refus_de_nextcloud_ne_stage_rien(self, tmp_path):
        cible = tmp_path / "nc.json"
        stage = bp.obtenir_identifiants_nextcloud(
            "T", POLITIQUE_NC, env=ENV_NC,
            post=lambda *a, **k: (200, json.dumps({"access_token": "J"})),
            get=self._get({"cloud/user": (401, _ocs([], 997))}),
            path=str(cible))
        assert stage is None
        assert not cible.exists()


class TestApresPolitique:
    """Les deux etapes du crochet sont independantes : un enrolement rate ne
    doit pas couter les identifiants Nextcloud, ni l'inverse."""

    def test_les_deux_etapes_partent(self):
        vu = []
        import unittest.mock as mock
        with mock.patch.object(bp, "stage_enrolment",
                               side_effect=lambda *a, **k: vu.append("enrol")), \
             mock.patch.object(bp, "obtenir_identifiants_nextcloud",
                               side_effect=lambda *a, **k: vu.append("nc")):
            bp._apres_politique("T", POLITIQUE_NC, out=lambda m: None)
        assert vu == ["enrol", "nc"]

    def test_un_enrolement_rate_ne_coute_pas_les_identifiants(self):
        vu = []
        import unittest.mock as mock
        # stage_enrolment ne leve pas par contrat ; on verifie qu'un retour
        # None (echec) laisse bien passer l'etape suivante.
        with mock.patch.object(bp, "stage_enrolment", return_value=None), \
             mock.patch.object(bp, "obtenir_identifiants_nextcloud",
                               side_effect=lambda *a, **k: vu.append("nc")):
            bp._apres_politique("T", POLITIQUE_NC, out=lambda m: None)
        assert vu == ["nc"]


# ===========================================================================
# Cohabitation avec un systeme deja present (#22419)
# ===========================================================================
# Regle de lecture de ces tests : le seul resultat SUR est « interactif ».
# Chaque cas ou l'on ne sait pas doit y retomber. Un test qui verrait
# « cohabitation » la ou l'information manque decrit une perte de donnees.

GIO = 1024 ** 3


def _lsblk(disques):
    return json.dumps({"blockdevices": disques})


def _disque(path="/dev/sda", taille=512 * GIO, rm=False, parts=()):
    return {"path": path, "name": path.split("/")[-1], "type": "disk",
            "size": taille, "rm": rm,
            "children": [dict(c, type="part") for c in parts]}


def _part(path, taille, fstype="", parttypename=""):
    return {"path": path, "name": path.split("/")[-1], "size": taille,
            "fstype": fstype, "parttypename": parttypename}


ESP = _part("/dev/sda1", 512 * 1024 * 1024, "vfat", "EFI System")


class TestInspecterDisques:
    def test_normalise_lsblk(self):
        d = bp.inspecter_disques(_lsblk([_disque(parts=[ESP])]))
        assert len(d) == 1
        assert d[0]["path"] == "/dev/sda"
        assert d[0]["partitions"][0]["fstype"] == "vfat"

    def test_json_casse_leve(self):
        with pytest.raises(bp.ProvisionError):
            bp.inspecter_disques("{ pas du json")


class TestChoisirPlan:
    def _mesure(self, valeur):
        return lambda chemin, fstype: valeur

    def test_disque_vierge_reste_zero_touche(self):
        d = bp.inspecter_disques(_lsblk([_disque()]))
        assert bp.choisir_plan(d, self._mesure(None))["mode"] == "disque_entier"

    def test_deux_disques_fixes_on_demande(self):
        """Se tromper de disque efface le mauvais. Pas d'heuristique ici."""
        d = bp.inspecter_disques(_lsblk([_disque("/dev/sda"), _disque("/dev/sdb")]))
        p = bp.choisir_plan(d, self._mesure(None))
        assert p["mode"] == "interactif" and "2 disques" in p["raison"]

    def test_la_cle_usb_d_installation_ne_compte_pas(self):
        d = bp.inspecter_disques(_lsblk([
            _disque("/dev/sda"), _disque("/dev/sdb", 32 * GIO, rm=True)]))
        assert bp.choisir_plan(d, self._mesure(None))["mode"] == "disque_entier"

    def test_disque_trop_petit(self):
        d = bp.inspecter_disques(_lsblk([_disque(taille=16 * GIO)]))
        p = bp.choisir_plan(d, self._mesure(None))
        assert p["mode"] == "interactif" and "minimum" in p["raison"]

    def test_windows_avec_de_la_place(self):
        parts = [ESP, _part("/dev/sda2", 400 * GIO, "ntfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        p = bp.choisir_plan(d, self._mesure(100 * GIO))
        assert p["mode"] == "cohabitation"
        assert p["partition"] == "/dev/sda2"
        assert p["nouvelle_taille"] == 100 * GIO + bp.VOISIN_MARGE
        assert p["libere"] == 400 * GIO - (100 * GIO + bp.VOISIN_MARGE)
        assert p["esp"] == "/dev/sda1"       # on REUTILISE l'ESP existante

    def test_windows_presque_plein_on_demande(self):
        parts = [ESP, _part("/dev/sda2", 400 * GIO, "ntfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        p = bp.choisir_plan(d, self._mesure(390 * GIO))
        assert p["mode"] == "interactif"

    def test_mesure_impossible_on_demande(self):
        """⚠️ Ne pas savoir vaut REFUS. Une estimation ici mord dans les
        donnees de quelqu'un."""
        parts = [ESP, _part("/dev/sda2", 400 * GIO, "ntfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        p = bp.choisir_plan(d, self._mesure(None))
        assert p["mode"] == "interactif" and "indeterminable" in p["raison"]

    def test_mesure_incoherente_on_demande(self):
        parts = [ESP, _part("/dev/sda2", 400 * GIO, "ntfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        for absurde in (0, -1, 500 * GIO):
            p = bp.choisir_plan(d, self._mesure(absurde))
            assert p["mode"] == "interactif", absurde

    def test_xfs_ne_retrecit_pas(self):
        parts = [ESP, _part("/dev/sda2", 400 * GIO, "xfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        p = bp.choisir_plan(d, self._mesure(100 * GIO))
        assert p["mode"] == "interactif" and "retrecissable" in p["raison"]

    def test_btrfs_traite_comme_non_retrecissable(self):
        """btrfs SAIT retrecir, mais pas par ce chemin. Tenter a moitie serait
        pire que refuser."""
        parts = [ESP, _part("/dev/sda2", 400 * GIO, "btrfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        assert bp.choisir_plan(d, self._mesure(100 * GIO))["mode"] == "interactif"

    def test_choisit_la_plus_grosse_retrecissable(self):
        parts = [ESP, _part("/dev/sda2", 80 * GIO, "ext4"),
                 _part("/dev/sda3", 400 * GIO, "ntfs")]
        d = bp.inspecter_disques(_lsblk([_disque(parts=parts)]))
        assert bp.choisir_plan(d, self._mesure(50 * GIO))["partition"] == "/dev/sda3"


class TestRenderPartitionnement:
    DESTRUCTEURS = ("autopart", "clearpart --all", "clearpart --linux",
                    "part ", "--resize", "zerombr")

    def test_interactif_ne_peut_rien_detruire(self):
        """Le test qui compte le plus de tout ce fichier."""
        rendu = bp.render_partitionnement(
            {"mode": "interactif", "raison": "deux disques"})
        for ligne in rendu.splitlines():
            assert ligne.startswith("#"), f"ligne active en mode sur : {ligne!r}"
        for mot in self.DESTRUCTEURS:
            assert mot not in rendu.replace("#", "")
        assert "deux disques" in rendu      # la raison est dite

    def test_disque_entier_identique_a_avant(self):
        rendu = bp.render_partitionnement({"mode": "disque_entier",
                                           "disque": "/dev/sda"}, "SECRET")
        assert rendu.strip() == \
            "autopart --type=btrfs --encrypted --nohome --passphrase=SECRET"

    def test_disque_entier_sans_chiffrement(self):
        rendu = bp.render_partitionnement({"mode": "disque_entier",
                                           "disque": "/dev/sda"}, chiffrer=False)
        assert "--encrypted" not in rendu and "--passphrase" not in rendu

    def _plan(self, esp="/dev/sda1"):
        return {"mode": "cohabitation", "disque": "/dev/sda",
                "partition": "/dev/sda2", "taille_actuelle": 400 * GIO,
                "nouvelle_taille": 120 * GIO, "libere": 280 * GIO, "esp": esp}

    def test_cohabitation_n_efface_rien(self):
        rendu = bp.render_partitionnement(self._plan(), "SECRET")
        assert "clearpart --none" in rendu
        assert "clearpart --all" not in rendu and "zerombr" not in rendu
        assert f"--onpart=/dev/sda2 --resize --size={120 * 1024}" in rendu

    def test_l_esp_du_voisin_n_est_pas_reformatee(self):
        """⚠️ Formater l'ESP existante effacerait l'amorceur du voisin : sa
        partition serait intacte et son systeme indemarrable."""
        rendu = bp.render_partitionnement(self._plan(), "SECRET")
        assert "part /boot/efi --onpart=/dev/sda1 --noformat" in rendu

    def test_sans_esp_on_en_cree_une(self):
        rendu = bp.render_partitionnement(self._plan(esp=""), "SECRET")
        assert "part /boot/efi --fstype=efi --size=600" in rendu
        assert "--noformat" not in rendu

    def test_la_phrase_ne_va_que_sur_le_volume_chiffre(self):
        rendu = bp.render_partitionnement(self._plan(), "SECRET")
        porteuses = [l for l in rendu.splitlines() if "--passphrase=SECRET" in l]
        assert len(porteuses) == 1
        assert porteuses[0].startswith("part btrfs.bfos --grow --encrypted")

    def test_plan_inconnu_leve(self):
        with pytest.raises(bp.ProvisionError):
            bp.render_partitionnement({"mode": "n_importe_quoi"})


class TestTailleMinimaleFs:
    def test_ext4_convertit_les_blocs_en_octets(self):
        def run(cmd):
            class R: pass
            r = R(); r.returncode = 0
            if cmd[0] == "resize2fs":
                r.stdout = "Estimated minimum size of the filesystem: 1250000\n"
            else:
                r.stdout = "Block size:               4096\n"
            return r
        assert bp.taille_minimale_fs("/dev/sda2", "ext4", run=run) == 1250000 * 4096

    def test_ntfs_lit_les_octets(self):
        def run(cmd):
            class R: pass
            r = R(); r.returncode = 0
            r.stdout = "You might resize at 107374182400 bytes or 107374 MB\n"
            return r
        assert bp.taille_minimale_fs("/dev/sda2", "ntfs", run=run) == 107374182400

    def test_outil_en_echec_rend_none(self):
        def run(cmd):
            class R: pass
            r = R(); r.returncode = 1; r.stdout = ""
            return r
        assert bp.taille_minimale_fs("/dev/sda2", "ntfs", run=run) is None

    def test_outil_absent_rend_none_sans_lever(self):
        def run(cmd):
            raise FileNotFoundError("ntfsresize")
        assert bp.taille_minimale_fs("/dev/sda2", "ntfs", run=run) is None

    def test_fs_inconnu_rend_none(self):
        assert bp.taille_minimale_fs("/dev/sda2", "zfs", run=lambda c: None) is None


class TestSequestreEtCohabitation:
    """⚠️ La règle née de #22419 : en partitionnement interactif, c'est ANACONDA
    qui demandera la phrase à l'opérateur. Celle qu'on tirerait n'ouvrirait
    rien. Déposer quand même donnerait à Odoo une clé qui n'est pas celle du
    disque — pire que pas de clé, parce qu'on la croirait bonne le jour où
    elle servirait."""

    PLAN_INTERACTIF = {"mode": "interactif", "raison": "2 disques fixes"}

    def _capture_post(self, vu):
        def post(url, payload, token, timeout=30):
            vu.update(payload)
            return _enrol_escrow_ok(url, payload, token, timeout)
        return post

    def test_plan_interactif_ne_depose_aucune_phrase(self, tmp_path):
        autopart = tmp_path / "autopart.ks"
        vu, dit = {}, []
        bp.stage_enrolment(
            "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=self._capture_post(vu), path=str(tmp_path / "machine.json"),
            autopart_path=str(autopart), out=dit.append,
            plan=dict(self.PLAN_INTERACTIF))
        assert "disk_passphrase" not in vu, "une phrase a ete deposee a tort"
        assert any("aucune phrase" in m for m in dit)
        assert any("2 disques fixes" in m for m in dit)   # la raison est dite

    def test_plan_interactif_n_ecrit_rien_de_destructif(self, tmp_path):
        autopart = tmp_path / "autopart.ks"
        bp.stage_enrolment(
            "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=_enrol_escrow_ok, path=str(tmp_path / "machine.json"),
            autopart_path=str(autopart), plan=dict(self.PLAN_INTERACTIF))
        for ligne in autopart.read_text().splitlines():
            assert ligne.startswith("#"), ligne

    def test_cohabitation_sequestre_normalement(self, tmp_path):
        """Le voisin est préservé ET le disque BFOS reste séquestré : les deux
        garanties tiennent ensemble, elles ne s'excluent pas."""
        autopart = tmp_path / "autopart.ks"
        plan = {"mode": "cohabitation", "disque": "/dev/sda",
                "partition": "/dev/sda2", "taille_actuelle": 400 * GIO,
                "nouvelle_taille": 120 * GIO, "libere": 280 * GIO,
                "esp": "/dev/sda1"}
        vu = {}
        machine = bp.stage_enrolment(
            "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=self._capture_post(vu), path=str(tmp_path / "machine.json"),
            autopart_path=str(autopart), plan=plan)
        rendu = autopart.read_text()
        assert machine["disk_escrowed"] is True
        assert "disk_passphrase" in vu
        assert "clearpart --none" in rendu          # rien n'est efface
        assert "--noformat" in rendu                # l'amorceur du voisin survit
        phrase = [l for l in rendu.splitlines() if "--passphrase=" in l]
        assert len(phrase) == 1 and "--encrypted" in phrase[0]

    def test_le_garde_transmet_bien_le_plan(self, tmp_path):
        """⚠️ stage_enrolment est un garde mince qui delegue. Ajouter un
        parametre au garde sans le passer au corps donne un UnboundLocalError
        avale par le « ne jamais lever » : tout parait marcher, et le plan est
        silencieusement ignore."""
        autopart = tmp_path / "autopart.ks"
        bp.stage_enrolment(
            "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=_enrol_escrow_ok, path=str(tmp_path / "machine.json"),
            autopart_path=str(autopart), plan=dict(self.PLAN_INTERACTIF))
        # si le plan n'etait pas transmis, le prealable rendrait un disque
        # vierge et on trouverait un autopart ici.
        assert "autopart" not in autopart.read_text()


class TestInterrupteurCohabitation:
    """Les deux moitiés du changement sont livrées séparément : ne plus
    effacer à l'aveugle est vrai tout de suite ; rétrécir le voisin attend
    d'avoir touché un vrai disque."""

    def _lsblk_windows(self):
        return _lsblk([_disque(parts=[ESP, _part("/dev/sda2", 400 * GIO, "ntfs")])])

    def _run(self, sortie):
        def run(cmd):
            class R: pass
            r = R(); r.returncode = 0; r.stdout = sortie
            return r
        return run

    def test_par_defaut_la_cohabitation_demande(self, monkeypatch):
        monkeypatch.delenv("BFOS_COHABITATION", raising=False)
        monkeypatch.setattr(bp, "taille_minimale_fs",
                            lambda c, f, **k: 100 * GIO)
        p = PLAN_PAR_DEFAUT_REEL(run=self._run(self._lsblk_windows()))
        assert p["mode"] == "interactif"
        assert "BFOS_COHABITATION" in p["raison"]
        assert "280 Gio liberables" in p["raison"]   # on DIT ce qu'on refuse

    def test_activee_elle_cohabite(self, monkeypatch):
        monkeypatch.setenv("BFOS_COHABITATION", "1")
        monkeypatch.setattr(bp, "taille_minimale_fs",
                            lambda c, f, **k: 100 * GIO)
        p = PLAN_PAR_DEFAUT_REEL(run=self._run(self._lsblk_windows()))
        assert p["mode"] == "cohabitation" and p["partition"] == "/dev/sda2"

    def test_le_disque_vierge_reste_zero_touche_dans_les_deux_cas(self, monkeypatch):
        monkeypatch.delenv("BFOS_COHABITATION", raising=False)
        p = PLAN_PAR_DEFAUT_REEL(run=self._run(_lsblk([_disque()])))
        assert p["mode"] == "disque_entier"

    def test_lsblk_absent_ne_leve_pas(self):
        def run(cmd):
            raise FileNotFoundError("lsblk")
        assert PLAN_PAR_DEFAUT_REEL(run=run)["mode"] == "interactif"
