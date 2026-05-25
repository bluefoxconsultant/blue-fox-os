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

Per-user policy (bf-policy/v2 session, BFOSI10) overrides two of these when
present in tenant.branding (overlaid upstream by provisioning.merge_branding):

  - branding.palette.accent  -> generate + apply a per-user color scheme
  - branding.wallpaper_url    -> fetch + apply that wallpaper

Returns best-effort; missing tools (e.g. inside a CI container) are reported
as soft failures, not exceptions.
"""
from __future__ import annotations

import logging
import random
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from ..branding.colors import generate_user_colorscheme
from ..tenant import get_slug

LOG = logging.getLogger("bluefox-welcome.apply.kde_theme")

BRANDING_RUNTIME = "/usr/share/bluefox/branding"
WALLPAPERS_DIR = Path(BRANDING_RUNTIME) / "wallpapers"
LOOK_AND_FEEL_ROOT = Path("/usr/share/plasma/look-and-feel")
WALLPAPER_CACHE_DIR = Path.home() / ".local/share/bluefox"
_WALLPAPER_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _pick_random_wallpaper(
    wallpapers_dir: Path = WALLPAPERS_DIR,
    fallback: str = f"{BRANDING_RUNTIME}/wallpaper.jpg",
    rng: random.Random | None = None,
) -> str:
    """Pick one random wallpaper from the pack, fallback to single default (#22433)."""
    if not wallpapers_dir.is_dir():
        return fallback
    pack = sorted(wallpapers_dir.glob("*.png")) + sorted(wallpapers_dir.glob("*.jpg"))
    if not pack:
        return fallback
    chooser = rng or random
    return str(chooser.choice(pack))


def _wallpaper_ext(url: str) -> str:
    """Derive a safe image extension from a wallpaper URL (default .jpg)."""
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in _WALLPAPER_EXTS else ".jpg"


def _fetch_url(url: str, dest: Path, timeout: int = 30) -> bool:
    """Download `url` to `dest`. Returns False on any failure (never raises)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "blue-fox-os"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            data = resp.read()
        dest.write_bytes(data)
        return True
    except Exception as e:  # noqa: BLE001
        LOG.warning("wallpaper fetch %s failed: %s", url, e)
        return False


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
    wallpapers_dir: Path = WALLPAPERS_DIR,
    color_schemes_dir: Path | None = None,
    wallpaper_cache_dir: Path | None = None,
    fetch=None,
) -> tuple[bool, str]:
    """Apply look-and-feel + wallpaper + font to the current user session.

    `look_and_feel_root`, `wallpapers_dir`, `color_schemes_dir`,
    `wallpaper_cache_dir` and `fetch` are seams for tests.
    """
    slug = get_slug(tenant)
    pkg = _resolve_lookandfeel_pkg(tenant, slug)
    font = _resolve_font(tenant)
    branding = tenant.get("branding", {}) or {}
    accent = (branding.get("palette", {}) or {}).get("accent")
    wallpaper_url = branding.get("wallpaper_url")

    notes: list[str] = []
    overall_ok = False

    # 1. Look-and-feel package (preferred — applies scheme + wallpaper + decoration in one go).
    if (look_and_feel_root / pkg).is_dir():
        ok, msg = _run(["plasma-apply-lookandfeel", "-a", pkg])
        notes.append(f"lookandfeel({pkg}):{'ok' if ok else msg}")
        overall_ok = overall_ok or ok
    else:
        notes.append(f"lookandfeel({pkg}):missing")

    # 2. Color scheme. A per-user accent override (bf-policy/v2 session) gets a
    #    freshly generated scheme so the live colors match the baked palette
    #    math; otherwise fall back to the baked /usr/share scheme named <slug>.
    scheme_name = slug
    if accent:
        try:
            _, scheme_name = generate_user_colorscheme(
                tenant, dest_dir=color_schemes_dir)
            notes.append(f"colorscheme-gen({scheme_name}):ok")
        except Exception as e:  # noqa: BLE001
            notes.append(f"colorscheme-gen:fail({type(e).__name__}: {e})")
            scheme_name = slug
    ok, msg = _run(["plasma-apply-colorscheme", scheme_name])
    notes.append(f"colorscheme({scheme_name}):{'ok' if ok else msg}")
    overall_ok = overall_ok or ok

    # 3. Wallpaper — a policy wallpaper_url is fetched + applied; otherwise pick
    # one from the baked pack. plasma-apply-wallpaperimage handles containments.
    wallpaper: str | None = None
    if wallpaper_url:
        cache_dir = wallpaper_cache_dir or WALLPAPER_CACHE_DIR
        fetcher = fetch or _fetch_url
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / f"wallpaper-policy{_wallpaper_ext(wallpaper_url)}"
            if fetcher(wallpaper_url, dest):
                wallpaper = str(dest)
                notes.append("wallpaper-fetch:ok")
            else:
                notes.append("wallpaper-fetch:fail")
        except Exception as e:  # noqa: BLE001
            notes.append(f"wallpaper-fetch:fail({type(e).__name__}: {e})")
    if not wallpaper:
        wallpaper = _pick_random_wallpaper(wallpapers_dir)
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
