from pathlib import Path

from bluefox_welcome.tenant import get_service_url, get_slug, load_tenant


def test_load_tenant_happy(tenant_file: Path):
    data = load_tenant(tenant_file)
    assert data["slug"] == "bf"
    assert data["services"]["nextcloud"]["url"].startswith("https://")


def test_load_tenant_missing_file(tmp_path: Path):
    assert load_tenant(tmp_path / "does-not-exist.json") == {}


def test_load_tenant_malformed(tmp_path: Path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    assert load_tenant(p) == {}


def test_get_service_url_present(tenant_data: dict):
    assert get_service_url(tenant_data, "nextcloud") == \
        "https://nextcloud.bluefoxconsultant.com"


def test_get_service_url_missing_returns_default():
    assert get_service_url({}, "nextcloud", "https://fallback") == "https://fallback"


def test_get_slug_default():
    assert get_slug({}) == "bf"


def test_tenant_illisible_dit_l_erreur_et_rend_vide(tmp_path, monkeypatch, caplog):
    """#26870 (2026-10-26) : tenant.json livre en 0600 root ; l'agent, qui tourne
    en tant que l'usager, ne montait ni Nextcloud ni Vaultwarden."""
    import logging
    from pathlib import Path
    from bluefox_welcome import tenant as t

    f = tmp_path / "tenant.json"
    f.write_text('{"services": {"nextcloud": {"url": "https://nc"}}}')

    def refuse(self, *a, **k):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", refuse)
    with caplog.at_level(logging.ERROR, logger="bluefox-welcome.tenant"):
        assert t.load_tenant(f) == {}
    assert any("pas lisible" in r.getMessage() for r in caplog.records)
