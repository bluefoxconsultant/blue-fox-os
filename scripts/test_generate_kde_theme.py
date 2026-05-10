"""Unit tests for scripts/generate_kde_theme.py.

Run with: cd /home/livv/blue-fox-os && python3 -m pytest scripts/test_generate_kde_theme.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_kde_theme as gkt  # noqa: E402


def test_resolve_branding_applies_bf_defaults():
    b = gkt.resolve_branding({"slug": "client", "branding": {}})
    assert b["primary"] == gkt.BF_PRIMARY
    assert b["secondary"] == gkt.BF_SECONDARY
    assert b["accent"] == b["primary"]
    assert b["system_font"] == gkt.BF_FONT
    assert b["plasma_theme"] == "client"
    assert b["sddm_theme"] == "client"


def test_resolve_branding_respects_palette_override():
    b = gkt.resolve_branding({
        "slug": "purp",
        "branding": {"palette": {"primary": "#7B2CBF", "accent": "#C77DFF"}},
    })
    assert b["primary"] == "#7B2CBF"
    assert b["accent"] == "#C77DFF"
    assert b["secondary"] == gkt.BF_SECONDARY  # no override → BF default


def test_resolve_branding_respects_theme_overrides():
    b = gkt.resolve_branding({
        "slug": "bf",
        "branding": {"plasma_theme": "blue-fox-dark", "sddm_theme": "blue-fox"},
    })
    assert b["plasma_theme"] == "blue-fox-dark"
    assert b["sddm_theme"] == "blue-fox"


def test_hex_to_rgb_round_trip():
    assert gkt._hex_to_rgb("#29ABE1") == (41, 171, 225)
    assert gkt._hex_to_rgb("29abe1") == (41, 171, 225)


def test_emit_color_scheme_uses_palette(tmp_path: Path):
    tenant = {
        "slug": "purp",
        "branding": {"palette": {"primary": "#7B2CBF", "secondary": "#1A0E2E", "accent": "#C77DFF"}},
    }
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["color_scheme"].read_text()
    # Accent (Selection.BackgroundNormal) should be the C77DFF rgb tuple
    assert "BackgroundNormal=199,125,255" in text
    # Window background uses secondary #1A0E2E = 26,14,46
    assert "BackgroundNormal=26,14,46" in text
    # ColorScheme name = slug
    assert "ColorScheme=purp" in text


def test_emit_xdg_kdeglobals_references_lookandfeel(tmp_path: Path):
    tenant = {
        "slug": "bf",
        "branding": {"plasma_theme": "blue-fox-dark"},
    }
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["xdg_kdeglobals"].read_text()
    assert "ColorScheme=bf" in text
    assert "LookAndFeelPackage=blue-fox-dark" in text
    assert "font=Lexend," in text


def test_emit_look_and_feel_package_dir_uses_theme_name(tmp_path: Path):
    tenant = {"slug": "bf", "branding": {"plasma_theme": "blue-fox-dark"}}
    artifacts = gkt.emit_all(tenant, tmp_path)
    pkg_dir = artifacts["look_and_feel"]
    assert pkg_dir.name == "blue-fox-dark"
    assert (pkg_dir / "metadata.json").exists()
    assert (pkg_dir / "contents/defaults").exists()
    metadata = json.loads((pkg_dir / "metadata.json").read_text())
    assert metadata["KPlugin"]["Id"] == "blue-fox-dark"


def test_emit_wallpaper_symlinks_runtime_path(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    link = artifacts["wallpaper_pkg"] / "contents/images/wallpaper.jpg"
    assert link.is_symlink()
    assert str(link.readlink()) == "/usr/share/bluefox/branding/wallpaper.jpg"


def test_emit_sddm_config_steers_to_breeze(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["sddm_config"].read_text()
    assert "Current=breeze" in text
    assert "Font=Lexend" in text


def test_emit_skel_kdeglobals_mirrors_xdg(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    skel = artifacts["skel_kdeglobals"].read_text()
    xdg = artifacts["xdg_kdeglobals"].read_text()
    assert skel == xdg


def test_idempotent_re_run_preserves_symlinks(tmp_path: Path):
    """Re-running the generator must not blow up on existing symlinks."""
    tenant = {"slug": "bf"}
    gkt.emit_all(tenant, tmp_path)
    # Second run must not raise
    gkt.emit_all(tenant, tmp_path)
    link = tmp_path / "usr/share/wallpapers/bf/contents/images/wallpaper.jpg"
    assert link.is_symlink()


def test_emit_plymouth_theme_uses_palette(tmp_path: Path):
    tenant = {
        "slug": "purp",
        "branding": {"palette": {"primary": "#7B2CBF", "secondary": "#1A0E2E", "accent": "#C77DFF"}},
    }
    artifacts = gkt.emit_all(tenant, tmp_path)
    base = artifacts["plymouth_theme"]
    assert (base / "purp.plymouth").exists()
    assert (base / "purp.script").exists()
    script = (base / "purp.script").read_text()
    # Background = secondary 26,14,46 → 0.1020, 0.0549, 0.1804
    assert "0.1020, 0.0549, 0.1804" in script
    # Text color = accent 199,125,255 → 0.7804, 0.4902, 1.0000
    assert "0.7804, 0.4902, 1.0000" in script


def test_emit_plymouth_config_references_slug(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["plymouth_config"].read_text()
    assert "Theme=bf" in text


def test_emit_plymouth_uses_splash_symlink(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    link = artifacts["plymouth_theme"] / "splash.png"
    assert link.is_symlink()
    assert str(link.readlink()) == "/usr/share/bluefox/branding/splash.png"


def test_resolve_boot_bg_color_default_to_secondary():
    b = gkt.resolve_branding({"slug": "bf", "branding": {}})
    assert b["boot_bg_color"] == gkt.BF_SECONDARY


def test_resolve_boot_bg_color_override():
    b = gkt.resolve_branding({
        "slug": "bf",
        "branding": {"boot_bg_color": "#001533"},
    })
    assert b["boot_bg_color"] == "#001533"


def test_plymouth_uses_boot_bg_color(tmp_path: Path):
    tenant = {
        "slug": "bf",
        "branding": {"boot_bg_color": "#001533"},
    }
    artifacts = gkt.emit_all(tenant, tmp_path)
    script = (artifacts["plymouth_theme"] / "bf.script").read_text()
    # #001533 = 0, 21, 51 / 255 = 0.0000, 0.0824, 0.2000
    assert "0.0000, 0.0824, 0.2000" in script


def test_plymouth_loading_bar_uses_accent(tmp_path: Path):
    tenant = {
        "slug": "bf",
        "branding": {"palette": {"accent": "#29ABE1"}},
    }
    artifacts = gkt.emit_all(tenant, tmp_path)
    base = artifacts["plymouth_theme"]
    assert (base / "bar-track.png").exists()
    assert (base / "bar-fill.png").exists()
    # Confirm bar-fill.png pixel matches accent color
    from PIL import Image
    fill = Image.open(base / "bar-fill.png")
    assert fill.getpixel((0, 0))[:3] == (41, 171, 225)


def test_emit_icon_theme_inherits_breeze(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    base = artifacts["icon_theme"]
    assert base.name == "bluefox-bf"
    index = (base / "index.theme").read_text()
    assert "Inherits=breeze,hicolor" in index
    # All 3 start-here aliases symlink to app-icon.svg
    for name in ("start-here-kde.svg", "start-here.svg", "start-here-symbolic.svg"):
        link = base / "scalable/places" / name
        assert link.is_symlink()
        assert str(link.readlink()) == "/usr/share/bluefox/branding/app-icon.svg"


def test_kdeglobals_references_icon_theme(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["xdg_kdeglobals"].read_text()
    assert "[Icons]" in text
    assert "Theme=bluefox-bf" in text


def test_emit_neofetch_ships_config_and_ascii(tmp_path: Path):
    tenant = {"slug": "bf"}
    artifacts = gkt.emit_all(tenant, tmp_path)
    cfg = artifacts["neofetch"] / "config.conf"
    assert cfg.exists()
    text = cfg.read_text()
    assert 'image_source="ascii"' in text
    assert "/usr/share/bluefox/branding/neofetch.ascii" in text
    ascii_path = tmp_path / "usr/share/bluefox/branding/neofetch.ascii"
    assert ascii_path.exists()
    assert "Blue Fox OS" in ascii_path.read_text()


def _wcag_luminance(hex_color: str) -> float:
    """sRGB relative luminance per WCAG 2.x."""
    r, g, b = (c / 255 for c in gkt._hex_to_rgb(hex_color))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _wcag_contrast(fg_hex: str, bg_hex: str) -> float:
    l1, l2 = _wcag_luminance(fg_hex), _wcag_luminance(bg_hex)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def _rgb_tuple_from_section(text: str, section: str, key: str) -> tuple[int, int, int]:
    """Parse `[section]\\n...key=R,G,B...` from a generated .colors file."""
    lines = text.splitlines()
    in_section = False
    for line in lines:
        if line.startswith("[") and line.endswith("]"):
            in_section = line == f"[{section}]"
            continue
        if in_section and line.startswith(f"{key}="):
            r, g, b = (int(x) for x in line.split("=", 1)[1].split(","))
            return r, g, b
    raise AssertionError(f"{section}.{key} not found")


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def test_color_scheme_foreground_text_passes_wcag_on_dark_bg(tmp_path: Path):
    """BF brand canon interdit explicitement texte bleu-accent sur fond
    anthracite. ForegroundActive/Link/Visited doivent utiliser un accent
    claircie qui passe WCAG AAA (≥7:1) sur les sections text-heavy
    (Window/View/Header/Tooltip — Dolphin labels, content panes, tooltips,
    headers de listes), et au minimum WCAG AA (≥4.5:1) sur Button (BG
    naturellement plus clair, labels bouton sont grands et rarement en état
    Active/Link).
    """
    tenant = {"slug": "bf", "branding": {"palette": {"primary": "#29ABE1", "secondary": "#2D3031", "accent": "#29ABE1"}}}
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["color_scheme"].read_text()

    aaa_sections = ("Colors:Window", "Colors:View", "Colors:Header", "Colors:Tooltip")
    aa_sections = ("Colors:Button",)
    for section in aaa_sections + aa_sections:
        bg = _rgb_to_hex(_rgb_tuple_from_section(text, section, "BackgroundNormal"))
        threshold = 7.0 if section in aaa_sections else 4.5
        level = "AAA" if section in aaa_sections else "AA"
        for key in ("ForegroundActive", "ForegroundLink", "ForegroundVisited"):
            fg = _rgb_to_hex(_rgb_tuple_from_section(text, section, key))
            ratio = _wcag_contrast(fg, bg)
            assert ratio >= threshold, (
                f"{section}.{key}={fg} on {bg} contrast={ratio:.2f}:1, "
                f"fails WCAG {level} (≥{threshold}:1)"
            )


def test_color_scheme_foreground_normal_passes_aaa_everywhere(tmp_path: Path):
    """ForegroundNormal (texte principal blanc-cassé) DOIT passer AAA partout —
    c'est le texte le plus dense visuellement (corps des fenêtres, contenus de
    fichiers, étiquettes par défaut)."""
    tenant = {"slug": "bf", "branding": {"palette": {"primary": "#29ABE1", "secondary": "#2D3031", "accent": "#29ABE1"}}}
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["color_scheme"].read_text()
    for section in ("Colors:Window", "Colors:View", "Colors:Button", "Colors:Header", "Colors:Tooltip"):
        bg = _rgb_to_hex(_rgb_tuple_from_section(text, section, "BackgroundNormal"))
        fg = _rgb_to_hex(_rgb_tuple_from_section(text, section, "ForegroundNormal"))
        ratio = _wcag_contrast(fg, bg)
        assert ratio >= 7.0, f"{section}.ForegroundNormal={fg} on {bg} = {ratio:.2f}:1, fails AAA"


def test_color_scheme_decoration_keeps_brand_accent(tmp_path: Path):
    """L'accent BF brut reste utilisé pour DecorationFocus/Hover (borders),
    pas pour du texte. Permet de garder l'identité visuelle BF (#29ABE1)
    sur les focus rings et hover states.
    """
    tenant = {"slug": "bf", "branding": {"palette": {"accent": "#29ABE1"}}}
    artifacts = gkt.emit_all(tenant, tmp_path)
    text = artifacts["color_scheme"].read_text()
    for section in ("Colors:Window", "Colors:View", "Colors:Button", "Colors:Header", "Colors:Tooltip"):
        focus = _rgb_to_hex(_rgb_tuple_from_section(text, section, "DecorationFocus"))
        assert focus == "#29ABE1", f"{section}.DecorationFocus must keep raw BF accent (got {focus})"
