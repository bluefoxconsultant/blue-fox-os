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
