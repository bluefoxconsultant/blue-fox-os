"""KDE Plasma 6 color-scheme math, shared between build time and runtime.

Single source of truth for:
  - the BF canonical palette defaults,
  - the hex/rgb helpers,
  - resolve_branding() (apply BF defaults to a tenant dict),
  - build_color_scheme_text() (the body of a <slug>.colors file),
  - generate_user_colorscheme() (write a per-user .colors at firstboot).

scripts/generate_kde_theme.py imports the first four at build time (plain
python3, no PyQt6) to bake /usr/share/color-schemes/<slug>.colors. The
firstboot welcome agent imports generate_user_colorscheme() to honor a
per-user accent override carried in the bf-policy/v2 session block
(BFOSI10) — same math, so the live scheme matches the baked one.
"""
from __future__ import annotations

import logging
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome.branding.colors")

BF_PRIMARY = "#29ABE1"
BF_SECONDARY = "#2D3031"
BF_FONT = "Lexend"


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb(h: str) -> str:
    return ",".join(str(c) for c in _hex_to_rgb(h))


def _mix(h1: str, h2: str, t: float) -> str:
    a = _hex_to_rgb(h1)
    b = _hex_to_rgb(h2)
    return "#{:02X}{:02X}{:02X}".format(
        *(round(a[i] * (1 - t) + b[i] * t) for i in range(3))
    )


def _darker(h: str, t: float = 0.15) -> str:
    return _mix(h, "#000000", t)


def _lighter(h: str, t: float = 0.15) -> str:
    return _mix(h, "#FFFFFF", t)


def resolve_branding(tenant: dict) -> dict:
    """Return a fully-populated branding dict, applying BF defaults."""
    slug = tenant.get("slug", "bf")
    branding = tenant.get("branding", {}) or {}
    palette = branding.get("palette", {}) or {}
    primary = palette.get("primary", BF_PRIMARY)
    secondary = palette.get("secondary", BF_SECONDARY)
    accent = palette.get("accent", primary)
    return {
        "slug": slug,
        "name": tenant.get("name") or slug.upper(),
        "primary": primary,
        "secondary": secondary,
        "accent": accent,
        "boot_bg_color": branding.get("boot_bg_color") or secondary,
        "system_font": branding.get("system_font") or BF_FONT,
        "plasma_theme": branding.get("plasma_theme") or slug,
        "sddm_theme": branding.get("sddm_theme") or slug,
    }


def build_color_scheme_text(b: dict) -> str:
    """Return the body of a KDE Plasma 6 dark color-scheme (.colors) file.

    `b` is a resolved branding dict (see resolve_branding). Output is the same
    bytes whether baked at build time or generated per-user at firstboot.
    """
    bg = b["secondary"]
    bg_alt = _lighter(bg, 0.07)
    fg = "#EFF0F1"
    fg_inactive = _darker(fg, 0.20)
    accent = b["accent"]
    accent_hover = _lighter(accent, 0.15)
    # accent_text : variante claircie de l'accent, sûre pour du texte sur fond
    # foncé. Le brand canon BF interdit explicitement l'accent #29ABE1 en
    # foreground sur l'anthracite #2D3031 (contraste ~5.5:1, visuellement
    # muddled). _lighter(accent, 0.40) → #7FCDED sur #2D3031 = 8.1:1 (WCAG AAA),
    # passe ≥7:1 sur tous les BackgroundNormal de toutes les sections (Window,
    # View légèrement éclaircie, Button éclairci, Header/Tooltip assombris).
    # Réservé aux ForegroundActive / ForegroundLink / ForegroundVisited ;
    # l'accent raw reste uniquement dans DecorationFocus / DecorationHover
    # (borders/focus rings, pas du texte).
    accent_text = _lighter(accent, 0.40)
    accent_visited = _lighter(accent, 0.50)
    selection_fg = "#FFFFFF"

    sections = {
        "ColorEffects:Disabled": {
            "Color": "56,56,56",
            "ColorAmount": "0",
            "ColorEffect": "0",
            "ContrastAmount": "0.65",
            "ContrastEffect": "1",
            "IntensityAmount": "0.10",
            "IntensityEffect": "2",
        },
        "ColorEffects:Inactive": {
            "ChangeSelectionColor": "true",
            "Color": "112,111,110",
            "ColorAmount": "0.025",
            "ColorEffect": "2",
            "ContrastAmount": "0.1",
            "ContrastEffect": "2",
            "Enable": "false",
            "IntensityAmount": "0",
            "IntensityEffect": "0",
        },
        "Colors:Button": {
            "BackgroundAlternate": _rgb(_lighter(bg, 0.15)),
            "BackgroundNormal": _rgb(_lighter(bg, 0.10)),
            "DecorationFocus": _rgb(accent),
            "DecorationHover": _rgb(accent_hover),
            "ForegroundActive": _rgb(accent_text),
            "ForegroundInactive": _rgb(fg_inactive),
            "ForegroundLink": _rgb(accent_text),
            "ForegroundNegative": "218,68,83",
            "ForegroundNeutral": "246,116,0",
            "ForegroundNormal": _rgb(fg),
            "ForegroundPositive": "39,174,96",
            "ForegroundVisited": _rgb(accent_visited),
        },
        "Colors:Selection": {
            "BackgroundAlternate": _rgb(_darker(accent, 0.10)),
            "BackgroundNormal": _rgb(accent),
            "DecorationFocus": _rgb(accent),
            "DecorationHover": _rgb(accent_hover),
            "ForegroundActive": _rgb(selection_fg),
            "ForegroundInactive": _rgb(selection_fg),
            "ForegroundLink": _rgb(selection_fg),
            "ForegroundNegative": "176,55,69",
            "ForegroundNeutral": "198,92,0",
            "ForegroundNormal": _rgb(selection_fg),
            "ForegroundPositive": "23,104,57",
            "ForegroundVisited": _rgb(selection_fg),
        },
        "Colors:Tooltip": {
            "BackgroundAlternate": _rgb(_lighter(bg, 0.15)),
            "BackgroundNormal": _rgb(_darker(bg, 0.05)),
            "DecorationFocus": _rgb(accent),
            "DecorationHover": _rgb(accent_hover),
            "ForegroundActive": _rgb(accent_text),
            "ForegroundInactive": _rgb(fg_inactive),
            "ForegroundLink": _rgb(accent_text),
            "ForegroundNegative": "218,68,83",
            "ForegroundNeutral": "246,116,0",
            "ForegroundNormal": _rgb(fg),
            "ForegroundPositive": "39,174,96",
            "ForegroundVisited": _rgb(accent_visited),
        },
        "Colors:View": {
            "BackgroundAlternate": _rgb(_darker(bg, 0.05)),
            # Pas d'éclaircissement de bg : préserve le contraste AAA du
            # ForegroundActive/Link (accent_text) sur ce BG. Différenciation
            # Window/View laissée à BackgroundAlternate qui assombrit pour les
            # rows alternées (Dolphin, listes, content panes).
            "BackgroundNormal": _rgb(bg),
            "DecorationFocus": _rgb(accent),
            "DecorationHover": _rgb(accent_hover),
            "ForegroundActive": _rgb(accent_text),
            "ForegroundInactive": _rgb(fg_inactive),
            "ForegroundLink": _rgb(accent_text),
            "ForegroundNegative": "218,68,83",
            "ForegroundNeutral": "246,116,0",
            "ForegroundNormal": _rgb(fg),
            "ForegroundPositive": "39,174,96",
            "ForegroundVisited": _rgb(accent_visited),
        },
        "Colors:Window": {
            "BackgroundAlternate": _rgb(bg_alt),
            "BackgroundNormal": _rgb(bg),
            "DecorationFocus": _rgb(accent),
            "DecorationHover": _rgb(accent_hover),
            "ForegroundActive": _rgb(accent_text),
            "ForegroundInactive": _rgb(fg_inactive),
            "ForegroundLink": _rgb(accent_text),
            "ForegroundNegative": "218,68,83",
            "ForegroundNeutral": "246,116,0",
            "ForegroundNormal": _rgb(fg),
            "ForegroundPositive": "39,174,96",
            "ForegroundVisited": _rgb(accent_visited),
        },
        "Colors:Header": {
            "BackgroundAlternate": _rgb(_darker(bg, 0.10)),
            "BackgroundNormal": _rgb(_darker(bg, 0.05)),
            "DecorationFocus": _rgb(accent),
            "DecorationHover": _rgb(accent_hover),
            "ForegroundActive": _rgb(accent_text),
            "ForegroundInactive": _rgb(fg_inactive),
            "ForegroundLink": _rgb(accent_text),
            "ForegroundNegative": "218,68,83",
            "ForegroundNeutral": "246,116,0",
            "ForegroundNormal": _rgb(fg),
            "ForegroundPositive": "39,174,96",
            "ForegroundVisited": _rgb(accent_visited),
        },
        "General": {
            "ColorScheme": b["slug"],
            "Name": b["name"],
            "shadeSortColumn": "true",
            "accentColor": _rgb(accent),
        },
        "KDE": {
            "contrast": "4",
        },
        "WM": {
            "activeBackground": _rgb(_darker(bg, 0.05)),
            "activeBlend": _rgb(accent),
            "activeForeground": _rgb(fg),
            "inactiveBackground": _rgb(_darker(bg, 0.10)),
            "inactiveBlend": _rgb(fg_inactive),
            "inactiveForeground": _rgb(fg_inactive),
        },
    }

    lines: list[str] = []
    for sect, kvs in sections.items():
        lines.append(f"[{sect}]")
        for k, v in kvs.items():
            lines.append(f"{k}={v}")
        lines.append("")
    return "\n".join(lines)


# Where Plasma resolves per-user color schemes (overrides /usr/share).
USER_COLOR_SCHEMES_DIR = Path.home() / ".local/share/color-schemes"


def generate_user_colorscheme(
    tenant: dict,
    dest_dir: Path | None = None,
    scheme_name: str | None = None,
) -> tuple[Path, str]:
    """Write a per-user .colors built from the tenant's (effective) accent.

    Used at firstboot when a bf-policy/v2 session carries an accent_color
    override (merged into tenant.branding.palette.accent upstream). Writes to
    ~/.local/share/color-schemes/<scheme_name>.colors and returns
    (path, scheme_name). `scheme_name` defaults to "<slug>-custom" so it never
    collides with the baked /usr/share/color-schemes/<slug>.colors.
    """
    b = resolve_branding(tenant)
    name = scheme_name or f"{b['slug']}-custom"
    # Override the [General] ColorScheme/Name so plasma-apply-colorscheme can
    # target this scheme unambiguously.
    b = dict(b, slug=name, name=name)
    dest_dir = dest_dir or USER_COLOR_SCHEMES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{name}.colors"
    target.write_text(build_color_scheme_text(b))
    LOG.info("generated user color-scheme %s", target)
    return target, name
