from __future__ import annotations

from pathlib import Path

import pytest

from bluefox_welcome.apply.kde_theme import (
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
