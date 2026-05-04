"""Re-apply Blue Fox KDE theme to the current user's session.

System-wide defaults shipped via /etc/xdg/kdeglobals + look-and-feel package
work for new accounts, but a user that was created BEFORE the theme bake
(e.g. the Anaconda Kickstart user, or an existing account on rebase) keeps
its own ~/.config/kdeglobals and won't pick the new defaults up.

This module bridges that gap: read /usr/share/bluefox/tenant.json, derive
the look-and-feel package + color scheme name, and call:

  - plasma-apply-lookandfeel -a <pkg>     # if the package exists
  - plasma-apply-colorscheme <slug>       # color scheme fallback
  - kwriteconfig6 to set the wallpaper image and font

Returns best-effort; missing tools (e.g. inside a CI container) are reported
as soft failures, not exceptions.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from ..tenant import get_slug

LOG = logging.getLogger("bluefox-welcome.apply.kde_theme")

BRANDING_RUNTIME = "/usr/share/bluefox/branding"
LOOK_AND_FEEL_ROOT = Path("/usr/share/plasma/look-and-feel")


def _resolve_lookandfeel_pkg(tenant: dict, slug: str) -> str:
    """Mirror generate_kde_theme.resolve_branding for the LnF package name."""
    return (tenant.get("branding", {}) or {}).get("plasma_theme") or slug


def _resolve_font(tenant: dict) -> str:
    return (tenant.get("branding", {}) or {}).get("system_font") or "Lexend"


def _run(cmd: list[str]) -> tuple[bool, str]:
    if not shutil.which(cmd[0]):
        return False, f"{cmd[0]} introuvable"
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return False, f"{cmd[0]} rc={out.returncode}: {out.stderr.strip()[:200]}"
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, f"{cmd[0]} timeout"
    except Exception as e:
        return False, f"{cmd[0]} {type(e).__name__}: {e}"


def apply_kde_theme(
    tenant: dict,
    look_and_feel_root: Path = LOOK_AND_FEEL_ROOT,
) -> tuple[bool, str]:
    """Apply look-and-feel + wallpaper + font to the current user session."""
    slug = get_slug(tenant)
    pkg = _resolve_lookandfeel_pkg(tenant, slug)
    font = _resolve_font(tenant)
    wallpaper = f"{BRANDING_RUNTIME}/wallpaper.jpg"

    notes: list[str] = []
    overall_ok = False

    # 1. Look-and-feel package (preferred — applies scheme + wallpaper + decoration in one go).
    if (look_and_feel_root / pkg).is_dir():
        ok, msg = _run(["plasma-apply-lookandfeel", "-a", pkg])
        notes.append(f"lookandfeel({pkg}):{'ok' if ok else msg}")
        overall_ok = overall_ok or ok
    else:
        notes.append(f"lookandfeel({pkg}):missing")

    # 2. Color scheme fallback (in case LnF isn't honored or package missing).
    ok, msg = _run(["plasma-apply-colorscheme", slug])
    notes.append(f"colorscheme({slug}):{'ok' if ok else msg}")
    overall_ok = overall_ok or ok

    # 3. Wallpaper — kwriteconfig6 on plasma-org.kde.plasma.desktop-appletsrc
    # is brittle (containment IDs vary). plasma-apply-wallpaperimage handles it.
    ok, msg = _run(["plasma-apply-wallpaperimage", wallpaper])
    notes.append(f"wallpaper:{'ok' if ok else msg}")
    overall_ok = overall_ok or ok

    # 4. Font — kwriteconfig6 directly into ~/.config/kdeglobals.
    font_spec = f"{font},10,-1,5,50,0,0,0,0,0"
    ok, msg = _run([
        "kwriteconfig6",
        "--file", "kdeglobals",
        "--group", "General",
        "--key", "font", font_spec,
    ])
    notes.append(f"font({font}):{'ok' if ok else msg}")
    overall_ok = overall_ok or ok

    summary = "; ".join(notes)
    if overall_ok:
        return True, f"KDE thème : {summary}"
    return False, f"KDE thème (toutes les étapes ont échoué) : {summary}"
