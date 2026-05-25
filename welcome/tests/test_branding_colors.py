from __future__ import annotations

from pathlib import Path

from bluefox_welcome.branding.colors import (
    _rgb,
    build_color_scheme_text,
    generate_user_colorscheme,
    resolve_branding,
)


def test_build_color_scheme_text_keeps_raw_bf_accent_in_decoration():
    """Default BF accent appears raw in DecorationFocus (borders/focus rings)."""
    b = resolve_branding({"slug": "bf", "branding": {}})
    text = build_color_scheme_text(b)
    assert f"DecorationFocus={_rgb('#29ABE1')}" in text
    assert "ColorScheme=bf" in text


def test_generate_user_colorscheme_uses_override_accent(tmp_path: Path):
    tenant = {"slug": "bf", "branding": {"palette": {"accent": "#A020F0"}}}
    target, name = generate_user_colorscheme(tenant, dest_dir=tmp_path)
    assert name == "bf-custom"
    assert target.name == "bf-custom.colors"
    text = target.read_text()
    assert "ColorScheme=bf-custom" in text
    assert "Name=bf-custom" in text
    assert "DecorationFocus=160,32,240" in text  # #A020F0


def test_generate_user_colorscheme_custom_name(tmp_path: Path):
    target, name = generate_user_colorscheme(
        {"slug": "x"}, dest_dir=tmp_path, scheme_name="zzz")
    assert name == "zzz"
    assert target.name == "zzz.colors"


def test_generate_user_colorscheme_creates_dest_dir(tmp_path: Path):
    dest = tmp_path / "nested" / "color-schemes"
    target, _ = generate_user_colorscheme({"slug": "bf"}, dest_dir=dest)
    assert target.exists()
    assert target.parent == dest
