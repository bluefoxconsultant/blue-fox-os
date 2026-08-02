import io
import json
import urllib.error

import pytest

import bfos_provision as bp

DEVICE_URL = "https://auth.example.com/application/o/device/"
TOKEN_URL = "https://auth.example.com/application/o/token/"
POLICY_URL = "https://example.com/api/v1/policy/me"


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
    machine = bp.stage_enrolment(
        "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
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
        bp.stage_enrolment(
            "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
            post=post, path=str(tmp_path / f"machine-{abs(hash(label))}.json"),
            autopart_path=str(autopart))
        assert autopart.read_text().strip() == bp.AUTOPART_BASE, (
            f"{label} : une phrase a ete scellee sans confirmation d'Odoo")


def test_stage_enrolment_says_why_the_deposit_was_refused(tmp_path):
    autopart = tmp_path / "autopart.ks"
    bp.write_autopart(path=str(autopart))
    said = []
    bp.stage_enrolment(
        "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
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
    got = bp.stage_enrolment(
        "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
        post=_enrol_escrow_ok, path=str(tmp_path / "machine.json"),
        autopart_path=str(tmp_path / "autopart.ks"), out=said.append)
    assert got is None
    assert "no space left on device" in "".join(said)


def test_stage_enrolment_never_raises_on_a_malformed_policy(tmp_path):
    """`policies` non-mapping : .get dessus levait, et emportait tout."""
    said = []
    got = bp.stage_enrolment(
        "TOK", {"schema": "bf-policy/v2", "policies": ["disk_escrow"]},
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
        after_policy=lambda tok, pol: bp.stage_enrolment(
            tok, pol, env={"BFOS_ENROLL_URL": ENROLL_URL},
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
    bp.stage_enrolment(
        "TOK", POLICY_ESCROW, env={"BFOS_ENROLL_URL": ENROLL_URL},
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


def test_the_template_net_matches_autopart_base_word_for_word():
    expected = f"printf '%s\\n' '{bp.AUTOPART_BASE}' > /tmp/bfos-autopart.ks"
    assert expected in _template_text(), (
        "le filet du %pre et AUTOPART_BASE ont diverge : les machines dont le "
        "depot reussit et celles qui retombent sur la saisie manuelle "
        "n'auraient plus le meme format de disque")


def test_the_template_carries_no_bare_autopart_line():
    """La ligne doit venir du %include, sinon Anaconda lit celle du gabarit et
    le sequestre n'a aucun effet."""
    text = _template_text()
    for line in text.splitlines():
        if line.strip().startswith("autopart"):
            raise AssertionError(
                f"ligne autopart nue dans le gabarit : {line.strip()!r}")
    assert "%include /tmp/bfos-autopart.ks" in text
