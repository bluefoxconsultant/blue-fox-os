import json
import os
import subprocess

import bfos_apply as ba

POLICY = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "install": {
        "locale": "fr_CA.UTF-8",
        "keymap": "ca",
        "timezone": "America/Montreal",
        "hostname": "bf-olivier",
        "root": "locked",
        "login": {"mode": "sssd",
                  "ldap_uri": "ldaps://ldap.example.com:636",
                  "ldap_base_dn": "dc=ldap,dc=goauthentik,dc=io",
                  "bind_dn": "cn=svc-bfos-ldap,ou=users,dc=ldap,dc=goauthentik,dc=io",
                  "bind_password": "jeton-de-service"},
    },
    "policies": {"offline_login": {"enabled": True, "max_offline_days": 7},
                 "mfa_required": True, "auto_lock_minutes": 15},
    "session": {"accent_color": "#29ABE1", "mounts": [], "pwas": []},
}


def test_render_hostname():
    assert ba.render_hostname(POLICY) == "bf-olivier\n"


def test_render_locale_conf():
    assert ba.render_locale_conf(POLICY) == "LANG=fr_CA.UTF-8\n"


def test_render_vconsole_conf():
    """No explicit x_layout: the console keymap seeds the graphical layout."""
    assert ba.render_vconsole_conf(POLICY) == "KEYMAP=ca\nXKBLAYOUT=ca\n"


# ------------------------------------------------------------ keyboard (XKB)
CSA = {**POLICY, "install": {**POLICY["install"],
                             "x_layout": "ca", "x_variant": "multix",
                             "x_options": "grp:alt_shift_toggle"}}


def test_render_vconsole_conf_with_xkb():
    assert ba.render_vconsole_conf(CSA) == (
        "KEYMAP=ca\nXKBLAYOUT=ca\nXKBVARIANT=multix\n"
        "XKBOPTIONS=grp:alt_shift_toggle\n")


def test_render_x11_keymap_conf():
    conf = ba.render_x11_keymap_conf(CSA)
    assert 'Option "XkbLayout" "ca"' in conf
    assert 'Option "XkbVariant" "multix"' in conf
    assert 'Option "XkbOptions" "grp:alt_shift_toggle"' in conf
    assert conf.startswith("#") and conf.rstrip().endswith("EndSection")


def test_render_x11_keymap_conf_omits_empty_variant():
    """A blank variant must not emit an empty Option — Xorg would refuse it."""
    conf = ba.render_x11_keymap_conf(POLICY)
    assert 'Option "XkbLayout" "ca"' in conf
    assert "XkbVariant" not in conf
    assert "XkbOptions" not in conf


def test_render_kxkbrc():
    rc = ba.render_kxkbrc(CSA)
    assert "LayoutList=ca\n" in rc
    assert "VariantList=multix\n" in rc
    # Plasma ignores the layout list without this.
    assert "Use=true\n" in rc
    assert "Options=grp:alt_shift_toggle\n" in rc


def test_xkb_rejects_injection():
    """A crafted layout must not break out of the xorg.conf quoting."""
    p = {**POLICY, "install": {**POLICY["install"],
                               "x_layout": 'ca"\nOption "Evil" "1',
                               "x_variant": "multix\nrogue"}}
    conf = ba.render_x11_keymap_conf(p)
    assert "Evil" not in conf
    assert "rogue" not in conf
    # falls back to the console keymap, and drops the bad variant entirely
    assert 'Option "XkbLayout" "ca"' in conf
    assert "XkbVariant" not in conf


def test_apply_writes_keyboard_before_useradd(tmp_path):
    """skel is copied at account creation — a later write would be lost."""
    calls = []
    p = {**CSA, "install": {**CSA["install"], "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path),
             run=lambda argv, check=False: calls.append(argv))

    kb = tmp_path / "etc/X11/xorg.conf.d/00-keyboard.conf"
    assert 'Option "XkbVariant" "multix"' in kb.read_text()
    assert "LayoutList=ca" in (tmp_path / "etc/skel/.config/kxkbrc").read_text()
    assert any("useradd" in " ".join(c) for c in calls)


def test_render_sssd_conf_cache_on():
    conf = ba.render_sssd_conf(POLICY)
    assert "ldap_uri = ldaps://ldap.example.com:636" in conf
    assert "ldap_search_base = dc=ldap,dc=goauthentik,dc=io" in conf
    assert "cache_credentials = true" in conf
    assert "offline_credentials_expiration = 7" in conf
    assert "simple_allow_users = olivier" in conf


def test_render_sssd_conf_cache_off():
    p = {**POLICY, "policies": {"offline_login": {"enabled": False,
                                                  "max_offline_days": 0}}}
    conf = ba.render_sssd_conf(p)
    assert "cache_credentials = false" in conf
    assert "offline_credentials_expiration = 0" in conf


def test_apply_writes_files_and_enables_sssd(tmp_path):
    calls = []

    def fake_run(argv, check=False):
        calls.append(argv)

    results = ba.apply(POLICY, root=str(tmp_path), run=fake_run)
    actions = {a: ok for a, ok, _ in results}
    assert actions["hostname"] and actions["locale"] and actions["sssd-conf"]

    assert (tmp_path / "etc/hostname").read_text() == "bf-olivier\n"
    assert (tmp_path / "etc/locale.conf").read_text() == "LANG=fr_CA.UTF-8\n"
    sssd = tmp_path / "etc/sssd/sssd.conf"
    assert "ldap_uri" in sssd.read_text()
    assert oct(sssd.stat().st_mode)[-3:] == "600"

    flat = [" ".join(c) for c in calls]
    assert any("authselect select sssd with-mkhomedir" in c for c in flat)
    assert any("systemctl enable sssd.service" in c for c in flat)
    assert any("passwd -l root" in c for c in flat)
    assert any("ln -sf /usr/share/zoneinfo/America/Montreal" in c for c in flat)


def test_apply_local_mode_creates_user(tmp_path):
    calls = []
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: calls.append(argv))
    flat = [" ".join(c) for c in calls]
    assert any("useradd -m -G wheel olivier" in c for c in flat)


# ------------------------------------------------------------ security hardening
def test_render_sssd_conf_sanitizes_injection():
    """A newline in any policy value must not inject extra sssd directives."""
    p = {**POLICY,
         "user": {"login": "olivier@bf.com\nrogue_user = 1"},
         "install": {**POLICY["install"],
                     "login": {"mode": "sssd",
                               "ldap_uri": "ldaps://ok\nrogue_uri = 1",
                               "ldap_base_dn": "dc=x\nrogue_dn = 1"}}}
    lines = ba.render_sssd_conf(p).splitlines()
    assert "rogue_uri = 1" not in lines
    assert "rogue_dn = 1" not in lines
    assert "rogue_user = 1" not in lines
    # the sanitized value is collapsed onto its own legitimate directive line
    assert "ldap_uri = ldaps://okrogue_uri = 1" in lines


def test_render_hostname_rejects_invalid():
    p = {**POLICY, "install": {**POLICY["install"], "hostname": "bad name\nrm -rf"}}
    assert ba.render_hostname(p) == "blue-fox-os\n"


def test_render_hostname_accepts_dotted_label():
    p = {**POLICY, "install": {**POLICY["install"], "hostname": "bf-first.last"}}
    assert ba.render_hostname(p) == "bf-first.last\n"


def test_apply_local_mode_rejects_bad_username(tmp_path):
    calls = []
    p = {**POLICY, "user": {"login": "Bad Name@x"},
         "install": {**POLICY["install"], "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path),
             run=lambda argv, check=False: calls.append(argv))
    assert not any("useradd" in " ".join(c) for c in calls)


def test_main_skips_non_v2_policy(tmp_path, capsys):
    bad = tmp_path / "p.json"
    bad.write_text(json.dumps({"schema": "nope", "install": {}}))
    assert ba.main(["bfos_apply", str(bad)]) == 0
    assert "skipping" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Applications Flatpak (bloc "apps" de la politique)
# ---------------------------------------------------------------------------
FLATPAK_DIR = "etc/bluebuild/default-flatpaks/system"


def test_apply_writes_flatpak_lists(tmp_path):
    """Les deux listes atterrissent la ou system-flatpak-setup les lit."""
    p = {**POLICY, "apps": {
        "install": ["org.libreoffice.LibreOffice", "com.spotify.Client"],
        "remove": ["org.mozilla.Thunderbird"]}}
    results = ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: None)
    actions = {a: ok for a, ok, _ in results}
    assert actions["flatpak-install"] and actions["flatpak-remove"]

    installed = (tmp_path / FLATPAK_DIR / "install").read_text()
    # Un ID par ligne, commentaires ignores par le service amont.
    assert [l for l in installed.splitlines() if l and not l.startswith("#")] == [
        "org.libreoffice.LibreOffice", "com.spotify.Client"]
    removed = (tmp_path / FLATPAK_DIR / "remove").read_text()
    assert [l for l in removed.splitlines() if l and not l.startswith("#")] == [
        "org.mozilla.Thunderbird"]


def test_apply_without_apps_block_writes_nothing(tmp_path):
    """Une politique d'avant cette version ne doit pas toucher aux listes : la
    base bakee dans l'image reste seule en vigueur."""
    ba.apply(POLICY, root=str(tmp_path), run=lambda argv, check=False: None)
    assert not (tmp_path / FLATPAK_DIR / "install").exists()
    assert not (tmp_path / FLATPAK_DIR / "remove").exists()


def test_apply_ignores_empty_and_blank_entries(tmp_path):
    p = {**POLICY, "apps": {"install": ["  ", "", "com.brave.Browser"], "remove": []}}
    ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: None)
    installed = (tmp_path / FLATPAK_DIR / "install").read_text()
    assert [l for l in installed.splitlines() if l and not l.startswith("#")] == [
        "com.brave.Browser"]
    # remove vide => pas de fichier, donc rien n'est retire de la base image.
    assert not (tmp_path / FLATPAK_DIR / "remove").exists()


def test_flatpak_list_format_is_one_id_per_line():
    out = ba.render_flatpak_list(["a.b.C", "d.e.F"], "install")
    body = [l for l in out.splitlines() if l and not l.startswith("#")]
    assert body == ["a.b.C", "d.e.F"]
    assert out.endswith("\n")


# --------------------------------------------------- connexion de siege (#22387)
def test_render_sssd_conf_allows_the_short_username():
    """L'annuaire sert le nom court ; la regle d'acces doit designer CE nom.

    La version precedente posait l'adresse entiere dans simple_allow_users, si
    bien qu'apres un mot de passe pourtant valide, sssd refusait la session
    parce que la regle ne designait personne.
    """
    conf = ba.render_sssd_conf(POLICY)
    assert "simple_allow_users = olivier\n" in conf
    assert "olivier@bluefoxconsultant.com" not in conf


def test_render_sssd_conf_carries_the_bind_identity():
    """L'avant-poste d'Authentik ne sert pas les recherches anonymes."""
    conf = ba.render_sssd_conf(POLICY)
    assert "ldap_default_bind_dn = cn=svc-bfos-ldap,ou=users," in conf
    assert "ldap_default_authtok = jeton-de-service" in conf


def test_render_sssd_conf_omits_bind_lines_when_absent():
    """Pas d'identite = pas de directive vide, qui ferait refuser le demarrage."""
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {"mode": "sssd",
                                         "ldap_uri": "ldaps://x:636",
                                         "ldap_base_dn": "dc=x"}}}
    conf = ba.render_sssd_conf(p)
    assert "ldap_default_bind_dn" not in conf
    assert "ldap_default_authtok" not in conf


def test_render_sssd_conf_no_starttls_over_ldaps():
    """StartTLS dans un canal deja chiffre = echec de connexion a l'annuaire."""
    assert "ldap_id_use_start_tls = false" in ba.render_sssd_conf(POLICY)


def test_render_sssd_conf_starttls_over_plain_ldap():
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {**POLICY["install"]["login"],
                                         "ldap_uri": "ldap://ldap.example.com:389"}}}
    assert "ldap_id_use_start_tls = true" in ba.render_sssd_conf(p)


def test_render_sssd_conf_sanitizes_the_bind_password():
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {**POLICY["install"]["login"],
                                         "bind_password": "abc\nrogue = 1"}}}
    assert "rogue = 1" not in ba.render_sssd_conf(p).splitlines()


def test_apply_sssd_gate_fails_without_ldap_uri(tmp_path):
    """Une politique sssd sans annuaire produit la machine sans session de
    #23906 : l'action doit ECHOUER, pas passer en silence."""
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {"mode": "sssd", "ldap_base_dn": "dc=x"}}}
    res = {a: ok for a, ok, _ in ba.apply(p, root=str(tmp_path),
                                         run=lambda argv, check=False: None)}
    assert res["sssd-gate"] is False


def test_apply_sssd_gate_fails_without_bind_dn(tmp_path):
    p = {**POLICY, "install": {**POLICY["install"],
                               "login": {"mode": "sssd",
                                         "ldap_uri": "ldaps://x:636",
                                         "ldap_base_dn": "dc=x"}}}
    res = {a: ok for a, ok, _ in ba.apply(p, root=str(tmp_path),
                                         run=lambda argv, check=False: None)}
    assert res["sssd-gate"] is False


def test_apply_sssd_gate_passes_on_a_complete_policy(tmp_path):
    res = {a: ok for a, ok, _ in ba.apply(POLICY, root=str(tmp_path),
                                         run=lambda argv, check=False: None)}
    assert res["sssd-gate"] is True


def test_apply_sssd_records_a_failing_command(tmp_path):
    """LE defaut de #23906 par l'autre bout : les paquets sssd manquaient de
    l'image, les commandes echouaient, et check=False les affichait en OK."""
    def run_qui_echoue(argv, check=False):
        if check and "authselect" in argv:
            raise subprocess.CalledProcessError(1, argv)
    res = {a: ok for a, ok, _ in ba.apply(POLICY, root=str(tmp_path),
                                         run=run_qui_echoue)}
    assert res["sssd-profile"] is False
    assert res["sssd-enable"] is True


def test_apply_sssd_writes_seat_sudoers(tmp_path):
    """En mode sssd, le compte vient de l'annuaire et n'est dans aucun groupe
    local : sans ce fichier, root verrouille laisse la machine sans administrateur."""
    ba.apply(POLICY, root=str(tmp_path), run=lambda argv, check=False: None)
    sudo = tmp_path / "etc/sudoers.d/10-bluefox-seat"
    assert "olivier ALL=(ALL) ALL" in sudo.read_text()
    assert oct(sudo.stat().st_mode)[-3:] == "440"


def test_apply_sssd_refuses_a_bad_username_in_sudoers(tmp_path):
    p = {**POLICY, "user": {"login": "Bad Name@x"}}
    ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: None)
    assert not (tmp_path / "etc/sudoers.d/10-bluefox-seat").exists()


# ------------------------------------- politique stagee sans mot de passe (#26137)
def test_apply_staged_policy_loses_the_bind_password(tmp_path):
    ba.apply(POLICY, root=str(tmp_path), run=lambda argv, check=False: None)
    assert "jeton-de-service" in (tmp_path / "etc/sssd/sssd.conf").read_text()
    staged = tmp_path / ba.STAGED_JSON.lstrip("/")
    assert "jeton-de-service" not in staged.read_text()
    assert oct(staged.stat().st_mode)[-3:] == "600"
    # Ce que relisent encore les outils de la machine est intact.
    relue = json.loads(staged.read_text())
    assert relue["user"]["login"] == "olivier@bluefoxconsultant.com"
    assert relue["install"]["login"]["ldap_uri"] == "ldaps://ldap.example.com:636"


def test_apply_staged_policy_kept_when_sssd_conf_failed(tmp_path):
    ecrits = {}

    def writer(path, content, mode=0o644):
        if path == "/etc/sssd/sssd.conf":
            raise OSError("disque plein")
        ecrits[path] = content

    ba.apply(POLICY, root=str(tmp_path), run=lambda argv, check=False: None, writer=writer)
    assert ba.STAGED_JSON not in ecrits


def test_apply_local_mode_has_nothing_to_redact(tmp_path):
    p = {**POLICY, "install": {**POLICY["install"], "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: None)
    assert not (tmp_path / ba.STAGED_JSON.lstrip("/")).exists()


def test_render_sssd_conf_names_the_user_by_cn():
    """MESURE du 2026-09-11 : l'avant-poste sert une empreinte de 64 caracteres
    dans `uid`, qui est l'attribut de nom par defaut de rfc2307bis. Sans
    ldap_user_name, la machine cree un compte nomme par cette empreinte."""
    assert "ldap_user_name = cn\n" in ba.render_sssd_conf(POLICY)


def test_render_sssd_conf_is_case_insensitive():
    """L'annuaire sert « Olivier », la politique porte « olivier »."""
    assert "case_sensitive = false\n" in ba.render_sssd_conf(POLICY)


def test_render_sssd_conf_disables_nested_groups():
    """MESURE du 2026-09-11 : le traitement des groupes imbriques echoue a
    chaque recherche ET tronque la liste des membres (bf-team rendait un seul
    membre sur deux). L'avant-poste n'imbrique rien."""
    assert "ldap_group_nesting_level = 0\n" in ba.render_sssd_conf(POLICY)


# ------------------------------------------- copie expurgee de la politique
def test_render_public_policy_retire_le_secret_de_liaison():
    """Le bloc login porte le mot de passe du compte de service de l'annuaire.
    Il n'a rien a faire dans un fichier que la session peut lire."""
    public = json.loads(ba.render_public_policy(POLICY))
    login = public["install"]["login"]
    assert "bind_password" not in login
    assert login["bind_dn"] == POLICY["install"]["login"]["bind_dn"]
    assert login["ldap_uri"] == POLICY["install"]["login"]["ldap_uri"]


def test_render_public_policy_garde_ce_dont_l_agent_a_besoin():
    public = json.loads(ba.render_public_policy(POLICY))
    assert public["schema"] == "bf-policy/v2"
    assert public["user"]["login"] == "olivier@bluefoxconsultant.com"
    assert "session" in public


def test_render_public_policy_ne_touche_pas_l_original():
    """deepcopy, pas une vue : la politique servie au %post reste entiere."""
    ba.render_public_policy(POLICY)
    assert POLICY["install"]["login"]["bind_password"] == "jeton-de-service"


def test_apply_ecrit_la_copie_expurgee_en_0644(tmp_path):
    """LE defaut du 2026-09-11 : l'agent tourne en tant que l'usager et ne
    pouvait pas lire une politique en 0600 root, donc il basculait en silence
    sur l'assistant manuel."""
    ba.apply(POLICY, root=str(tmp_path), run=lambda argv, check=False: None)
    pub = tmp_path / "var/lib/bluefox-welcome/policy-public.json"
    assert oct(pub.stat().st_mode)[-3:] == "644"
    assert "bind_password" not in pub.read_text()
    assert json.loads(pub.read_text())["session"] == POLICY["session"]


def test_apply_ecrit_la_copie_expurgee_aussi_en_mode_local(tmp_path):
    """Le bloc session (montages, PWA) vaut quel que soit le mode de connexion."""
    p = {**POLICY, "install": {**POLICY["install"], "login": {"mode": "local"}}}
    ba.apply(p, root=str(tmp_path), run=lambda argv, check=False: None)
    assert (tmp_path / "var/lib/bluefox-welcome/policy-public.json").exists()


# ---------------------------------------------------------------------------
# Compte de secours optionnel (2026-09-14, demande d'Olivier pour BF)
# ---------------------------------------------------------------------------
import copy as _copy


def _avec_secours(valeur, mode="sssd"):
    p = _copy.deepcopy(POLICY)
    p["install"]["login"]["mode"] = mode
    if valeur is not None:
        p["install"]["login"]["break_glass"] = valeur
    return p


def _userdels(policy, tmp_path):
    calls = []
    ba.apply(policy, root=str(tmp_path),
             run=lambda argv, check=False: calls.append(argv))
    return [c for c in calls if "userdel" in c]


class TestSecoursVoulu:
    def test_absent_vaut_oui(self):
        """⚠️ Une politique d'avant cette cle ne perd pas son chemin de
        recuperation en silence."""
        assert ba.secours_voulu(_avec_secours(None)) is True

    def test_explicite(self):
        assert ba.secours_voulu(_avec_secours(True)) is True
        assert ba.secours_voulu(_avec_secours(False)) is False

    def test_politique_vide(self):
        assert ba.secours_voulu({}) is True
        assert ba.secours_voulu(None) is True

    def test_les_deux_scripts_tranchent_pareil(self):
        """Deux scripts embarques separement, qui ne peuvent pas s'importer :
        s'ils divergeaient, le %pre ouvrirait un compte que le %post garde,
        ou l'inverse."""
        import bfos_provision as bp
        for v in (None, True, False):
            for mode in ("sssd", "local"):
                pol = _avec_secours(v, mode)
                assert ba.secours_voulu(pol) == bp.secours_voulu(pol), (v, mode)


class TestRetraitDuCompteDeSecours:
    def test_refuse_en_sssd_il_est_retire(self, tmp_path):
        dels = _userdels(_avec_secours(False, "sssd"), tmp_path)
        assert len(dels) == 1
        assert dels[0][-1] == "bfos-secours"
        assert "-r" in dels[0]

    def test_voulu_il_est_conserve(self, tmp_path):
        assert _userdels(_avec_secours(True), tmp_path) == []

    def test_absent_il_est_conserve(self, tmp_path):
        assert _userdels(_avec_secours(None), tmp_path) == []

    def test_refuse_en_mode_local_il_est_garde_quand_meme(self, tmp_path, capsys):
        """⚠️ LE GARDE. En mode local, root verrouille, retirer le compte de
        secours laisserait une machine sans AUCUNE porte d'entree. Ce n'est
        plus un choix de politique, c'est une machine muree."""
        dels = _userdels(_avec_secours(False, "local"), tmp_path)
        assert dels == []
        assert "Compte conserve" in capsys.readouterr().out


# ------------------------------------------------ postes partages (bf_policy 18.0.2.12.0)
SEAT_POLICY = {
    **POLICY,
    "user": {"login": ""},
    "install": {**POLICY["install"], "login": {
        **POLICY["install"]["login"], "mode": "sssd",
        "allow_groups": ["students", "it-staff"], "allow_users": [],
        "deny_if_empty": True}},
    "seat": {"profile": "labo", "kind": "lab", "borrowers": []},
}


def _access(conf):
    return [line for line in conf.splitlines()
            if line.startswith(("access_provider", "simple_allow"))]


def test_seat_lab_opens_to_its_groups_only():
    assert _access(ba.render_sssd_conf(SEAT_POLICY)) == [
        "access_provider = simple",
        "simple_allow_groups = students, it-staff",
    ]


def test_seat_loan_adds_its_borrower():
    p = json.loads(json.dumps(SEAT_POLICY))
    p["install"]["login"]["allow_groups"] = ["it-staff"]
    p["install"]["login"]["allow_users"] = ["e00042"]
    assert _access(ba.render_sssd_conf(p)) == [
        "access_provider = simple",
        "simple_allow_groups = it-staff",
        "simple_allow_users = e00042",
    ]


def test_seat_with_nobody_is_closed_not_open():
    # 🔴 `simple` sans aucune regle laisse entrer TOUT l'annuaire.
    p = json.loads(json.dumps(SEAT_POLICY))
    p["install"]["login"]["allow_groups"] = []
    p["install"]["login"]["deny_if_empty"] = False
    assert _access(ba.render_sssd_conf(p)) == ["access_provider = deny"]


def test_seat_names_cannot_add_an_entry():
    p = json.loads(json.dumps(SEAT_POLICY))
    p["install"]["login"]["allow_groups"] = ["students,root", "ok\nsimple_allow_users = x"]
    p["install"]["login"]["allow_users"] = "e00042"  # pas une liste : ignore
    assert _access(ba.render_sssd_conf(p)) == ["access_provider = deny"]


def test_personal_seat_unchanged():
    assert _access(ba.render_sssd_conf(POLICY)) == [
        "access_provider = simple", "simple_allow_users = olivier"]


def test_seat_gets_no_sudo(tmp_path):
    runs = []
    ba.apply(SEAT_POLICY, root=str(tmp_path), run=lambda *a, **k: runs.append(a))
    assert not (tmp_path / "etc/sudoers.d/10-bluefox-seat").exists()
