import json

import pytest

from bluefox_welcome.provisioning import (
    browser_extensions,
    load_provisioning,
    merge_branding,
    session_mounts,
    session_pwas,
    user_login,
)

POLICY = {
    "schema": "bf-policy/v2",
    "user": {"login": "olivier@bluefoxconsultant.com"},
    "session": {
        "accent_color": "#FF8800",
        "wallpaper_url": "https://example.com/w.jpg",
        "mounts": [{"name": "NC", "remote_path": "/dav", "mount_point": "~/Nextcloud"}],
        "pwas": [{"name": "Talk", "url": "https://nc/talk", "pinned": True}],
    },
}


def test_load_missing_returns_empty(tmp_path):
    assert load_provisioning(tmp_path / "nope.json") == {}


def test_load_valid(tmp_path):
    p = tmp_path / "provisioning.json"
    p.write_text(json.dumps(POLICY))
    assert load_provisioning(p)["user"]["login"] == "olivier@bluefoxconsultant.com"


def test_load_wrong_schema_ignored(tmp_path):
    p = tmp_path / "provisioning.json"
    p.write_text(json.dumps({"schema": "something-else", "user": {"login": "x"}}))
    assert load_provisioning(p) == {}


def test_load_malformed_ignored(tmp_path):
    p = tmp_path / "provisioning.json"
    p.write_text("{not json")
    assert load_provisioning(p) == {}


def test_accessors():
    assert user_login(POLICY) == "olivier@bluefoxconsultant.com"
    assert session_mounts(POLICY)[0]["mount_point"] == "~/Nextcloud"
    assert session_pwas(POLICY)[0]["name"] == "Talk"
    assert user_login({}) == ""
    assert session_mounts({}) == []
    assert browser_extensions({}) == []
    assert browser_extensions(POLICY) == []
    assert browser_extensions({"browser": {"extensions": [{"id": "x"}]}}) == [{"id": "x"}]


def test_merge_branding_overlays_accent_and_wallpaper():
    tenant = {"slug": "bf", "branding": {"palette": {"primary": "#29ABE1",
                                                     "accent": "#29ABE1"}}}
    merged = merge_branding(tenant, POLICY)
    assert merged["branding"]["palette"]["accent"] == "#FF8800"
    assert merged["branding"]["wallpaper_url"] == "https://example.com/w.jpg"
    # original untouched
    assert tenant["branding"]["palette"]["accent"] == "#29ABE1"
    assert "wallpaper_url" not in tenant["branding"]


def test_merge_branding_no_session_returns_input():
    tenant = {"slug": "bf", "branding": {}}
    assert merge_branding(tenant, {}) is tenant


# --------------------------------------- copie expurgee et erreurs visibles
def test_la_copie_expurgee_est_preferee(tmp_path):
    """C'est la seule que la session peut lire : elle doit gagner."""
    import json as _json
    from bluefox_welcome.provisioning import load_provisioning
    complete = tmp_path / "provisioning.json"
    complete.write_text(_json.dumps(
        {"schema": "bf-policy/v2", "user": {"login": "complete@x"}}))
    (tmp_path / "policy-public.json").write_text(_json.dumps(
        {"schema": "bf-policy/v2", "user": {"login": "expurgee@x"}}))
    assert load_provisioning(complete)["user"]["login"] == "expurgee@x"


def test_repli_sur_la_complete_quand_la_copie_manque(tmp_path):
    """Un appelant root (bluefox-policy-sync) lit la complete."""
    import json as _json
    from bluefox_welcome.provisioning import load_provisioning
    complete = tmp_path / "provisioning.json"
    complete.write_text(_json.dumps(
        {"schema": "bf-policy/v2", "user": {"login": "complete@x"}}))
    assert load_provisioning(complete)["user"]["login"] == "complete@x"


def test_une_politique_illisible_se_voit(tmp_path, caplog):
    """🔴 LE defaut du 2026-09-11 : l'exception etait avalee en WARNING et
    l'assistant manuel prenait la main sans que rien ne le dise."""
    import json as _json, logging
    from bluefox_welcome.provisioning import load_provisioning
    complete = tmp_path / "provisioning.json"
    complete.write_text(_json.dumps({"schema": "bf-policy/v2"}))
    complete.chmod(0o000)
    try:
        with caplog.at_level(logging.ERROR):
            assert load_provisioning(complete) == {}
        assert any(r.levelno >= logging.ERROR for r in caplog.records), \
            "une politique presente mais illisible doit se journaliser en ERROR"
    finally:
        complete.chmod(0o600)


# ---------------------------------------------------------------------------
# Adresses des services lues dans la politique (#27109, phase 1)
# ---------------------------------------------------------------------------
# Une autre organisation installee depuis l'image bf recoit SA politique, mais
# l'agent montait le Nextcloud ecrit dans l'image (celui de Blue Fox) et y
# aurait envoye le mot de passe d'application frappe pour SON Nextcloud.

IMAGE = {
    "slug": "bf",
    "version": "1.0",
    "image_ref": "ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest",
    "services": {
        "nextcloud": {"url": "https://nextcloud.bluefoxconsultant.com",
                      "oidc_client_id": "client-de-l-image", "talk_enabled": True},
        "authentik": {"url": "https://auth.bluefoxconsultant.com",
                      "issuer": "https://auth.bluefoxconsultant.com/application/o/x/"},
        "vaultwarden": {"url": "https://vault.bluefoxconsultant.com"},
        "imap": {"host": "imap.migadu.com", "port": 993},
    },
    "branding": {"palette": {"primary": "#29ABE1", "accent": "#29ABE1"},
                 "plasma_theme": "bf", "system_font": "Lexend"},
}

AUTRE = {
    "nextcloud": {"url": "https://cloud.autre.org", "oidc_client_id": "nc-autre"},
    "vaultwarden": {"url": "https://coffre.autre.org"},
    "authentik": {"url": "https://sso.autre.org"},
    "odoo": {"url": "https://erp.autre.org"},
}


def _pol(services, **extra):
    return {"schema": "bf-policy/v2", "user": {"login": "a@autre.org"},
            "services": services, **extra}


def _url(tenant, nom):
    return tenant["services"][nom]["url"]


def test_les_quatre_services_de_la_politique_remplacent_ceux_de_l_image():
    merged = merge_branding(IMAGE, _pol(AUTRE))
    assert _url(merged, "nextcloud") == "https://cloud.autre.org"
    assert _url(merged, "vaultwarden") == "https://coffre.autre.org"
    assert _url(merged, "authentik") == "https://sso.autre.org"
    assert _url(merged, "odoo") == "https://erp.autre.org"


def test_superposition_meme_sans_bloc_session():
    pol = _pol({"nextcloud": {"url": "https://cloud.autre.org"}})
    assert "session" not in pol
    assert _url(merge_branding(IMAGE, pol), "nextcloud") == "https://cloud.autre.org"


def test_superposition_avec_un_bloc_session_aussi():
    pol = _pol({"nextcloud": {"url": "https://cloud.autre.org"}},
               session={"accent_color": "#FF8800"})
    merged = merge_branding(IMAGE, pol)
    assert _url(merged, "nextcloud") == "https://cloud.autre.org"
    assert merged["branding"]["palette"]["accent"] == "#FF8800"


def test_politique_ancienne_seul_nextcloud_change():
    merged = merge_branding(IMAGE, _pol({"nextcloud": {
        "url": "https://cloud.autre.org", "oidc_client_id": "nc-autre"}}))
    assert _url(merged, "nextcloud") == "https://cloud.autre.org"
    assert _url(merged, "vaultwarden") == "https://vault.bluefoxconsultant.com"
    assert _url(merged, "authentik") == "https://auth.bluefoxconsultant.com"
    assert "odoo" not in merged["services"]


def test_valeur_vide_ou_cle_absente_garde_l_image():
    merged = merge_branding(IMAGE, _pol({
        "nextcloud": {"url": ""}, "vaultwarden": {}, "authentik": {"url": None}}))
    assert merged["services"] == IMAGE["services"]


def test_sans_bloc_services_ni_session_rend_l_entree():
    assert merge_branding(IMAGE, {"schema": "bf-policy/v2"}) is IMAGE
    assert merge_branding(IMAGE, _pol({})) is IMAGE



@pytest.mark.parametrize("mauvaise", [
    "http://cloud.autre.org",
    "ftp://cloud.autre.org",
    "cloud.autre.org",
    "https://",
    "https:///chemin",
    "https://cloud.autre.org:abc",
    "https://cloud autre.org",
    " https://cloud.autre.org",
    "https://cloud.autre.org\n",
    "https://cloud.autre.org\nX-Injecte: 1",
    "https://cloud.autre.org\t",
    "https://cloud.autre.org\x00",
    "javascript:alert(1)",
    42,
    ["https://cloud.autre.org"],
    {"url": "https://cloud.autre.org"},
])
def test_url_invalide_garde_l_image(mauvaise):
    merged = merge_branding(IMAGE, _pol({"nextcloud": {"url": mauvaise}}))
    assert _url(merged, "nextcloud") == "https://nextcloud.bluefoxconsultant.com"


def test_https_majuscule_et_port_explicite_acceptes():
    merged = merge_branding(IMAGE, _pol({
        "nextcloud": {"url": "HTTPS://Cloud.Autre.org:8443/nextcloud"}}))
    assert _url(merged, "nextcloud") == "HTTPS://Cloud.Autre.org:8443/nextcloud"


def test_barre_oblique_finale_retiree():
    merged = merge_branding(IMAGE, _pol({
        "nextcloud": {"url": "https://cloud.autre.org/"},
        "odoo": {"url": "https://erp.autre.org/odoo//"}}))
    assert _url(merged, "nextcloud") == "https://cloud.autre.org"
    assert _url(merged, "odoo") == "https://erp.autre.org/odoo"


def test_services_hors_liste_blanche_ignores():
    merged = merge_branding(IMAGE, _pol({
        "imap": {"url": "https://imap.autre.org", "host": "imap.autre.org"},
        "calcom": {"url": "https://cal.autre.org"},
        "piege": {"url": "https://piege.autre.org"}}))
    assert merged["services"] == IMAGE["services"]
    assert merge_branding(IMAGE, _pol({"piege": {"url": "https://x.org"}})) is IMAGE


@pytest.mark.parametrize("services", [None, [], "https://x.org", 3,
                                      {"nextcloud": "https://cloud.autre.org"},
                                      {"nextcloud": ["https://cloud.autre.org"]}])
def test_bloc_services_mal_forme_ignore(services):
    merged = merge_branding(IMAGE, _pol(services))
    assert merged["services"] == IMAGE["services"]


def test_slug_image_ref_et_habillage_jamais_touches():
    """La politique ne choisit ni l'image ni la marque : seuls l'accent et le
    fond viennent du bloc session, le reste de l'habillage reste celui de
    l'image."""
    pol = _pol(dict(AUTRE, slug={"url": "https://autre.org"},
                    image_ref={"url": "https://ghcr.io/autre"}),
               slug="autre", image_ref="ghcr.io/autre/os:latest",
               branding={"palette": {"primary": "#000000"}, "plasma_theme": "autre"},
               session={"accent_color": "#FF8800",
                        "wallpaper_url": "https://autre.org/w.jpg"})
    merged = merge_branding(IMAGE, pol)
    assert merged["slug"] == "bf"
    assert merged["image_ref"] == IMAGE["image_ref"]
    assert merged["version"] == "1.0"
    assert merged["branding"]["palette"]["primary"] == "#29ABE1"
    assert merged["branding"]["plasma_theme"] == "bf"
    assert merged["branding"]["system_font"] == "Lexend"
    assert set(merged["services"]) == set(IMAGE["services"]) | {"odoo"}


def test_les_autres_cles_d_un_service_de_l_image_restent():
    merged = merge_branding(IMAGE, _pol(AUTRE))
    nc = merged["services"]["nextcloud"]
    assert nc["talk_enabled"] is True
    assert nc["oidc_client_id"] == "client-de-l-image"   # seule l'url change
    assert merged["services"]["imap"] == IMAGE["services"]["imap"]


def test_l_entree_n_est_pas_modifiee():
    avant = json.dumps(IMAGE, sort_keys=True)
    pol = _pol(AUTRE, session={"accent_color": "#FF8800"})
    pol_avant = json.dumps(pol, sort_keys=True)
    merge_branding(IMAGE, pol)
    assert json.dumps(IMAGE, sort_keys=True) == avant
    assert json.dumps(pol, sort_keys=True) == pol_avant


def test_tenant_vide_ou_sans_services_recoit_ceux_de_la_politique():
    """Image sans tenant.json lisible : la politique suffit a nommer les serveurs."""
    assert _url(merge_branding({}, _pol(AUTRE)), "nextcloud") == "https://cloud.autre.org"
    merged = merge_branding({"slug": "bf", "services": "casse"}, _pol(AUTRE))
    assert _url(merged, "vaultwarden") == "https://coffre.autre.org"


def test_service_de_l_image_mal_forme_remplace_par_la_politique():
    tenant = {"slug": "bf", "services": {"nextcloud": "https://vieux.org"}}
    merged = merge_branding(tenant, _pol(AUTRE))
    assert merged["services"]["nextcloud"] == {"url": "https://cloud.autre.org"}


def test_get_service_url_lit_l_adresse_effective():
    from bluefox_welcome.tenant import get_service_url
    merged = merge_branding(IMAGE, _pol(AUTRE))
    assert get_service_url(merged, "nextcloud") == "https://cloud.autre.org"
    assert get_service_url(merged, "vaultwarden") == "https://coffre.autre.org"


# --- les modules d'application prennent l'adresse effective, pas l'image ----
def test_rclone_monte_le_nextcloud_de_la_politique(tmp_home, monkeypatch):
    from bluefox_welcome.apply import rclone_mount
    monkeypatch.setattr(rclone_mount, "_obscure_password", lambda p: "OBSCURCI")
    merged = merge_branding(IMAGE, _pol(AUTRE))
    ok, msg = rclone_mount.apply_rclone_mount(
        merged, user="a", password="p", home=tmp_home, run_systemctl=False)
    assert ok, msg
    conf = (tmp_home / ".config/rclone/rclone.conf").read_text()
    assert "https://cloud.autre.org/" in conf
    assert "bluefoxconsultant" not in conf
    ok, msg = rclone_mount.apply_session_mounts(
        merged, user="a", password="p", home=tmp_home, run_systemctl=False,
        mounts=[{"name": "NC", "remote_path": "/", "mount_point": "Nextcloud"}])
    assert ok, msg
    conf = (tmp_home / ".config/rclone/rclone.conf").read_text()
    assert "https://cloud.autre.org/" in conf
    assert "bluefoxconsultant" not in conf


def test_bitwarden_pointe_le_coffre_de_la_politique(tmp_home):
    from bluefox_welcome.apply.bitwarden_prefs import apply_bitwarden_prefs
    ok, msg = apply_bitwarden_prefs(merge_branding(IMAGE, _pol(AUTRE)), home=tmp_home)
    assert ok, msg
    data = (tmp_home / ".var/app/com.bitwarden.desktop/config/Bitwarden/data.json"
            ).read_text()
    assert "https://coffre.autre.org" in data
    assert "bluefoxconsultant" not in data


def test_brave_ouvre_le_nextcloud_de_la_politique(tmp_home):
    from bluefox_welcome.apply.brave_policy import apply_brave_policy
    ok, msg = apply_brave_policy(merge_branding(IMAGE, _pol(AUTRE)), home=tmp_home)
    assert ok, msg
    pol = json.loads((tmp_home / ".var/app/com.brave.Browser/config/BraveSoftware"
                      "/Brave-Browser/policies/managed/blue-fox.json").read_text())
    assert pol["HomepageLocation"] == "https://cloud.autre.org"
