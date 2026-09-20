from __future__ import annotations

import random
from pathlib import Path

import pytest

from bluefox_welcome.apply.kde_theme import (
    BRANDING_RUNTIME,
    _MAX_WALLPAPER_BYTES,
    _fetch_url,
    _pick_random_wallpaper,
    _resolve_font,
    _resolve_lookandfeel_pkg,
    _wallpaper_ext,
    apply_kde_theme,
)


class _FakeResp:
    """Minimal urlopen() context-manager whose read(n) honours the byte cap."""
    def __init__(self, data: bytes):
        self._data = data

    def read(self, n: int = -1) -> bytes:
        return self._data[:n] if n and n >= 0 else self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_url_rejects_oversized(monkeypatch, tmp_path: Path):
    big = b"x" * (_MAX_WALLPAPER_BYTES + 100)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(big))
    dest = tmp_path / "w.jpg"
    assert _fetch_url("https://x/w.jpg", dest) is False
    assert not dest.exists()


def test_fetch_url_accepts_small(monkeypatch, tmp_path: Path):
    small = b"PNGDATA"
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeResp(small))
    dest = tmp_path / "w.jpg"
    assert _fetch_url("https://x/w.jpg", dest) is True
    assert dest.read_bytes() == small


class _CompletedOK:
    returncode = 0
    stderr = ""
    stdout = ""


def test_resolve_lookandfeel_pkg_defaults_to_slug():
    assert _resolve_lookandfeel_pkg({"slug": "bf", "branding": {}}, "bf") == "bf"


def test_resolve_lookandfeel_pkg_respects_override():
    tenant = {"slug": "bf", "branding": {"plasma_theme": "blue-fox-dark"}}
    assert _resolve_lookandfeel_pkg(tenant, "bf") == "blue-fox-dark"


def test_resolve_font_default_lexend():
    assert _resolve_font({"slug": "bf", "branding": {}}) == "Lexend"


def test_resolve_font_override():
    assert _resolve_font({"slug": "bf", "branding": {"system_font": "Inter"}}) == "Inter"


def test_apply_kde_theme_reports_when_tools_missing(monkeypatch, tmp_path: Path,
                                                    tenant_data: dict):
    """In a CI container without Plasma binaries, every step should soft-fail
    gracefully and the function should still return without raising."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    ok, msg = apply_kde_theme(tenant_data, look_and_feel_root=tmp_path)
    assert ok is False
    # Surface what was tried so an operator can debug.
    assert "lookandfeel" in msg
    assert "colorscheme" in msg
    assert "wallpaper" in msg
    assert "font" in msg


def test_apply_kde_theme_partial_success(monkeypatch, tmp_path: Path,
                                         tenant_data: dict):
    """If at least one step succeeds, the overall result is True (best-effort)."""
    pkg_root = tmp_path / "look-and-feel"
    pkg_root.mkdir()
    (pkg_root / "bf").mkdir()  # tenant_data has slug=bf

    calls: list[list[str]] = []

    class _CompletedOK:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        # Succeed only for plasma-apply-lookandfeel
        if cmd[0] == "plasma-apply-lookandfeel":
            return _CompletedOK()
        # Fail others with non-zero
        out = _CompletedOK()
        out.returncode = 1
        out.stderr = "not implemented in test"
        return out

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    ok, msg = apply_kde_theme(tenant_data, look_and_feel_root=pkg_root)
    assert ok is True
    assert "lookandfeel(bf):ok" in msg


def test_apply_kde_theme_skips_lookandfeel_when_pkg_missing(monkeypatch,
                                                            tmp_path: Path,
                                                            tenant_data: dict):
    monkeypatch.setattr("shutil.which", lambda _: None)
    pkg_root = tmp_path / "look-and-feel"
    pkg_root.mkdir()
    # No bf/ subdir → should report missing
    ok, msg = apply_kde_theme(tenant_data, look_and_feel_root=pkg_root)
    assert "lookandfeel(bf):missing" in msg


def test_pick_random_wallpaper_falls_back_when_dir_missing(tmp_path: Path):
    """No wallpaper pack on disk → return the single default fallback (#22433)."""
    missing = tmp_path / "wallpapers"
    picked = _pick_random_wallpaper(missing, fallback="/usr/share/bluefox/branding/wallpaper.jpg")
    assert picked == "/usr/share/bluefox/branding/wallpaper.jpg"


def test_pick_random_wallpaper_falls_back_when_dir_empty(tmp_path: Path):
    """Empty pack dir → fallback to the static wallpaper, never an exception."""
    empty = tmp_path / "wallpapers"
    empty.mkdir()
    picked = _pick_random_wallpaper(empty, fallback="/static.jpg")
    assert picked == "/static.jpg"


def test_pick_random_wallpaper_picks_from_pack(tmp_path: Path):
    """With a pack present, return one of the PNGs deterministically via seeded rng."""
    pack = tmp_path / "wallpapers"
    pack.mkdir()
    for i in range(1, 13):
        (pack / f"blue-fox-os-wallpaper-{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    picked = _pick_random_wallpaper(pack, rng=random.Random(42))
    assert picked.startswith(str(pack))
    assert picked.endswith(".png")
    # Re-seed yields the same pick → deterministic for tests.
    again = _pick_random_wallpaper(pack, rng=random.Random(42))
    assert again == picked


def test_apply_kde_theme_uses_random_wallpaper_from_pack(monkeypatch,
                                                         tmp_path: Path,
                                                         tenant_data: dict):
    """apply_kde_theme must pass a wallpaper from the pack dir, not the static default."""
    pack = tmp_path / "wallpapers"
    pack.mkdir()
    (pack / "w1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (pack / "w2.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    seen_wallpaper: list[str] = []

    class _CompletedOK:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[0] == "plasma-apply-wallpaperimage":
            seen_wallpaper.append(cmd[1])
        return _CompletedOK()

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    apply_kde_theme(tenant_data, look_and_feel_root=tmp_path, wallpapers_dir=pack)
    assert len(seen_wallpaper) == 1
    assert seen_wallpaper[0].endswith((".png",))
    assert seen_wallpaper[0].startswith(str(pack))


# --- bf-policy/v2 session overrides (BFOSI10) -------------------------------

def test_apply_kde_theme_generates_user_colorscheme_on_accent(monkeypatch,
                                                              tmp_path: Path,
                                                              tenant_data: dict):
    """A session accent override → generate + apply a per-user scheme, not slug."""
    tenant = dict(tenant_data, branding={"palette": {"accent": "#A020F0"}})
    schemes = tmp_path / "color-schemes"
    applied: list[str] = []

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[0] == "plasma-apply-colorscheme":
            applied.append(cmd[1])
        return _CompletedOK()

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    ok, msg = apply_kde_theme(
        tenant, look_and_feel_root=tmp_path,
        wallpapers_dir=tmp_path / "wp", color_schemes_dir=schemes)
    files = list(schemes.glob("*.colors"))
    assert len(files) == 1
    assert files[0].name == "bf-custom.colors"
    assert applied == ["bf-custom"]  # generated scheme applied, not baked slug
    # The override accent #A020F0 shows up raw in DecorationFocus.
    assert "DecorationFocus=160,32,240" in files[0].read_text()
    assert "colorscheme-gen(bf-custom):ok" in msg


def test_apply_kde_theme_no_accent_uses_baked_slug(monkeypatch, tmp_path: Path,
                                                   tenant_data: dict):
    """No accent override → apply the baked <slug> scheme, generate nothing."""
    schemes = tmp_path / "color-schemes"
    applied: list[str] = []

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[0] == "plasma-apply-colorscheme":
            applied.append(cmd[1])
        return _CompletedOK()

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    apply_kde_theme(tenant_data, look_and_feel_root=tmp_path,
                    color_schemes_dir=schemes)
    assert applied == ["bf"]
    assert not list(schemes.glob("*.colors")) if schemes.exists() else True


def test_apply_kde_theme_fetches_wallpaper_url(monkeypatch, tmp_path: Path,
                                               tenant_data: dict):
    """A session wallpaper_url is fetched + applied, not the random pack."""
    tenant = dict(tenant_data, branding={"wallpaper_url": "https://nc.example/w.png"})
    cache = tmp_path / "cache"
    fetched: list[tuple[str, Path]] = []

    def fake_fetch(url, dest):
        fetched.append((url, dest))
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")
        return True

    seen: list[str] = []

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[0] == "plasma-apply-wallpaperimage":
            seen.append(cmd[1])
        return _CompletedOK()

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    ok, msg = apply_kde_theme(
        tenant, look_and_feel_root=tmp_path,
        wallpaper_cache_dir=cache, fetch=fake_fetch)
    assert len(fetched) == 1
    assert fetched[0][0] == "https://nc.example/w.png"
    assert seen and seen[0].endswith(".png")
    assert seen[0].startswith(str(cache))
    assert "wallpaper-fetch:ok" in msg


def test_apply_kde_theme_wallpaper_fetch_failure_falls_back_to_pack(
        monkeypatch, tmp_path: Path, tenant_data: dict):
    """Fetch failure → fall back to a wallpaper from the baked pack."""
    tenant = dict(tenant_data, branding={"wallpaper_url": "https://x/w.png"})
    pack = tmp_path / "wp"
    pack.mkdir()
    (pack / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    seen: list[str] = []

    def fake_run(cmd, capture_output, text, timeout):
        if cmd[0] == "plasma-apply-wallpaperimage":
            seen.append(cmd[1])
        return _CompletedOK()

    monkeypatch.setattr("shutil.which", lambda x: f"/usr/bin/{x}")
    monkeypatch.setattr("subprocess.run", fake_run)

    ok, msg = apply_kde_theme(
        tenant, look_and_feel_root=tmp_path, wallpapers_dir=pack,
        wallpaper_cache_dir=tmp_path / "c", fetch=lambda url, dest: False)
    assert seen and seen[0] == str(pack / "a.png")
    assert "wallpaper-fetch:fail" in msg


def test_wallpaper_ext_defaults_to_jpg():
    assert _wallpaper_ext("https://x/a.png") == ".png"
    assert _wallpaper_ext("https://x/a.webp") == ".webp"
    assert _wallpaper_ext("https://x/a") == ".jpg"
    assert _wallpaper_ext("https://x/a.gif") == ".jpg"
