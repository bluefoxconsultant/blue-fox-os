from __future__ import annotations

import random
from pathlib import Path

import pytest

from bluefox_welcome.apply.kde_theme import (
    BRANDING_RUNTIME,
    _pick_random_wallpaper,
    _resolve_font,
    _resolve_lookandfeel_pkg,
    apply_kde_theme,
)


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
