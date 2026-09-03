#!/usr/bin/env python3
"""Generate KDE Plasma 6 theme assets from a tenant.json into files/.

Invoked by scripts/build_branded_iso.sh after fetching/validating the
tenant config, before bluebuild build. Reads `branding.{palette,
system_font}` (with BF canonical defaults if absent) and produces:

  <files>/usr/share/color-schemes/<slug>.colors
  <files>/usr/share/wallpapers/<slug>/{metadata.json,contents/images/...}
  <files>/usr/share/plasma/look-and-feel/<slug>/{metadata.json,contents/defaults}
  <files>/usr/share/sddm/themes/<slug>/{theme.conf,Background.jpg,metadata.desktop}
  <files>/etc/xdg/kdeglobals
  <files>/etc/sddm.conf.d/blue-fox.conf
  <files>/etc/skel/.config/kdeglobals
  <files>/etc/skel/.config/plasma-org.kde.plasma.desktop-appletsrc

Logo stays symlinked at runtime via the icon theme (resolved by Plasma at
runtime); Plymouth splash + KDE wallpaper.jpg are **copied** (not symlinked)
because dracut bake-into-initramfs for Plymouth is fragile across symlinks,
and a duplicated MB of branding has negligible cost on an OCI image.

Usage: python3 generate_kde_theme.py <tenant.json> <files_root>
       (files_root is typically <repo>/files/usr — but we accept the
        repo files/ root and create both /usr/* and /etc/* under it)
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

# Single source of truth for the palette math lives in the welcome package
# (bluefox_welcome/branding/colors.py) so the build-time bake and the firstboot
# per-user accent override produce identical .colors files. The welcome/ tree
# is a sibling of scripts/ in the repo checkout that runs this script, so add
# it to sys.path; this is plain-python3 safe (colors.py is stdlib-only).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "welcome"))
from bluefox_welcome.branding.colors import (  # noqa: E402
    BF_FONT,
    BF_PRIMARY,
    BF_SECONDARY,
    _hex_to_rgb,
    _rgb,
    build_color_scheme_text,
    resolve_branding,
)

LOG = logging.getLogger("generate_kde_theme")

BRANDING_RUNTIME = "/usr/share/bluefox/branding"


def emit_color_scheme(b: dict, files_root: Path) -> Path:
    """Write /usr/share/color-schemes/<slug>.colors (KDE Plasma 6 dark scheme)."""
    target = files_root / "usr/share/color-schemes" / f"{b['slug']}.colors"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_color_scheme_text(b))
    LOG.info("emit color-scheme %s", target)
    return target



def emit_wallpaper_package(b: dict, files_root: Path) -> Path:
    """Ship a KDE wallpaper package symlinking the branding wallpaper."""
    base = files_root / "usr/share/wallpapers" / b["slug"]
    images = base / "contents/images"
    images.mkdir(parents=True, exist_ok=True)
    metadata = {
        "KPackageStructure": "Wallpaper/Images",
        "KPlugin": {
            "Id": b["slug"],
            "Name": b["name"],
            "Authors": [{"Name": "Blue Fox", "Email": "info@bluefoxconsultant.com"}],
            "Version": "1.0",
            "Website": "https://bluefoxconsultant.com",
        },
    }
    (base / "metadata.json").write_text(json.dumps(metadata, indent=2))

    # Copy (not symlink) wallpaper.jpg into the KDE wallpaper package so that
    # Plasma's wallpaper applet resolves a real file at config-write time. A
    # symlink works at runtime but Plasma sometimes caches the resolved path
    # in ~/.config/plasma-org.kde.plasma.desktop-appletsrc, which then points
    # at the canonical /usr/share/bluefox/branding/ path and bypasses our
    # wallpaper package — confusing for users picking the wallpaper later.
    wallpaper_src = files_root / "usr/share/bluefox/branding/wallpaper.jpg"
    wallpaper_dst = images / "wallpaper.jpg"
    if wallpaper_dst.is_symlink() or wallpaper_dst.exists():
        wallpaper_dst.unlink()
    if wallpaper_src.is_file():
        shutil.copy(wallpaper_src, wallpaper_dst)
    else:
        LOG.warning("wallpaper source %s missing", wallpaper_src)
    LOG.info("emit wallpaper pkg %s", wallpaper_dst)
    return base


def emit_look_and_feel(b: dict, files_root: Path) -> Path:
    """Ship a Plasma look-and-feel package wiring scheme + wallpaper.

    Directory name uses b['plasma_theme'] (defaults to slug) so that the
    referencing key in kdeglobals (LookAndFeelPackage=...) and on disk match.
    """
    pkg = b["plasma_theme"]
    base = files_root / "usr/share/plasma/look-and-feel" / pkg
    contents = base / "contents"
    contents.mkdir(parents=True, exist_ok=True)
    metadata = {
        "KPackageStructure": "Plasma/LookAndFeel",
        "KPlugin": {
            "Id": pkg,
            "Name": f"Blue Fox OS — {b['name']}",
            "Description": f"Look & Feel package for tenant {b['slug']}",
            "Authors": [{"Name": "Blue Fox", "Email": "info@bluefoxconsultant.com"}],
            "Version": "1.0",
            "Website": "https://bluefoxconsultant.com",
            "Category": "Plasma Look And Feel",
        },
    }
    (base / "metadata.json").write_text(json.dumps(metadata, indent=2))

    wallpaper_path = f"/usr/share/wallpapers/{b['slug']}/contents/images/wallpaper.jpg"
    defaults = (
        f"[kdeglobals][General]\n"
        f"ColorScheme={b['slug']}\n"
        f"AccentColor={b['accent']}\n"
        f"font={b['system_font']},10,-1,5,50,0,0,0,0,0\n"
        f"\n"
        f"[kdeglobals][KDE]\n"
        f"LookAndFeelPackage={b['plasma_theme']}\n"
        f"\n"
        f"[plasma-org.kde.plasma.desktop-appletsrc][Wallpaper][org.kde.image][General]\n"
        f"Image=file://{wallpaper_path}\n"
        f"FillMode=2\n"
        f"\n"
        # L'ecran de VERROUILLAGE est un reglage separe de celui du bureau :
        # kscreenlocker ne lit pas le fond du bureau. Sans cette section, une
        # machine brandee retombe sur le fond Plasma d'origine des qu'elle se
        # verrouille — l'ecran qu'on voit le plus souvent, en pratique.
        f"[kscreenlockerrc][Greeter][Wallpaper][org.kde.image][General]\n"
        f"Image=file://{wallpaper_path}\n"
        f"FillMode=2\n"
    )
    (contents / "defaults").write_text(defaults)
    LOG.info("emit look-and-feel %s", base)
    return base


def emit_sddm_theme(b: dict, files_root: Path) -> Path:
    """Minimal SDDM theme: Breeze-style override with brand background + accent.

    Directory name uses b['sddm_theme'] (defaults to slug). Currently SDDM
    is steered to the upstream `breeze` theme via emit_sddm_config(); this
    per-tenant theme dir is shipped as a forward-compatibility hook for v1.1
    when we ship a custom QML theme.
    """
    base = files_root / "usr/share/sddm/themes" / b["sddm_theme"]
    base.mkdir(parents=True, exist_ok=True)

    desktop = (
        f"[Desktop Entry]\n"
        f"Type=Application\n"
        f"Name=Blue Fox OS — {b['name']}\n"
        f"Comment=SDDM theme for tenant {b['slug']}\n"
        f"X-KDE-PluginInfo-Name={b['sddm_theme']}\n"
        f"X-KDE-PluginInfo-Version=1.0\n"
        f"X-KDE-PluginInfo-Author=Blue Fox\n"
    )
    (base / "metadata.desktop").write_text(desktop)

    theme_conf = (
        f"[General]\n"
        f"background={BRANDING_RUNTIME}/wallpaper.jpg\n"
        f"type=image\n"
        f"color={b['secondary']}\n"
        f"fontSize=10\n"
        f"font={b['system_font']}\n"
        f"accentColor={b['accent']}\n"
    )
    (base / "theme.conf").write_text(theme_conf)

    # Symlink Background.jpg for themes that look at base dir directly (e.g. Breeze fork).
    bg_link = base / "Background.jpg"
    if bg_link.is_symlink() or bg_link.exists():
        bg_link.unlink()
    bg_link.symlink_to(f"{BRANDING_RUNTIME}/wallpaper.jpg")

    # Minimal Main.qml so SDDM has something to load if the theme is selected
    # before Breeze theming is borrowed. Plasma 6 ships a usable Breeze theme
    # at /usr/share/sddm/themes/breeze; in v1 we just steer SDDM to use breeze
    # via /etc/sddm.conf.d/blue-fox.conf rather than ship a full QML tree here.
    # See emit_sddm_config() below.
    LOG.info("emit sddm theme %s", base)
    return base


def emit_plymouth_theme(b: dict, files_root: Path) -> Path:
    """Ship a Plymouth boot splash theme: bg + centered splash + loading bar.

    Plymouth scripting language reference:
    https://www.freedesktop.org/wiki/Software/Plymouth/Scripts/

    Layout:
      /usr/share/plymouth/themes/<slug>/
        <slug>.plymouth        (theme config)
        <slug>.script          (animation)
        splash.png             (symlink to branding/splash.png)
        bar-track.png          (1×4 grey track, generated at build time via PIL)
        bar-fill.png           (1×4 accent color, generated at build time, scaled wider over time)

    Caveat: pre-/ boot stages (LUKS unlock) need the theme in the initramfs.
    BlueBuild Kinoite atomic regenerates initramfs on the next ostree commit;
    the very first post-rebase boot may still show the upstream default
    theme during LUKS prompt. `sudo plymouth-set-default-theme -R <slug>`
    once on pilote machines to force.
    """
    slug = b["slug"]
    base = files_root / "usr/share/plymouth/themes" / slug
    base.mkdir(parents=True, exist_ok=True)

    bg_r, bg_g, bg_b = _hex_to_rgb(b["boot_bg_color"])
    accent_r, accent_g, accent_b = _hex_to_rgb(b["accent"])

    # Plymouth wants RGB as floats 0..1.
    bg_norm = (bg_r / 255, bg_g / 255, bg_b / 255)
    accent_norm = (accent_r / 255, accent_g / 255, accent_b / 255)

    # Generate the loading-bar seed images (1×4 px solid colors). Plymouth
    # scripts Scale() these horizontally to animate width. PIL preferred ;
    # ImageMagick `magick`/`convert` is a fallback for hosts where Pillow
    # isn't installed (Kinoite/Garuda/Arch dev machines without venv).
    try:
        from PIL import Image as PImage
        track = PImage.new("RGBA", (1, 4), (255, 255, 255, 60))
        track.save(base / "bar-track.png")
        fill = PImage.new("RGBA", (1, 4), (accent_r, accent_g, accent_b, 255))
        fill.save(base / "bar-fill.png")
    except ImportError:
        import subprocess
        magick = shutil.which("magick") or shutil.which("convert")
        if magick:
            subprocess.run(
                [magick, "-size", "1x4", "xc:rgba(255,255,255,0.235)",
                 str(base / "bar-track.png")],
                check=True,
            )
            accent_rgba = f"xc:rgba({accent_r},{accent_g},{accent_b},1)"
            subprocess.run(
                [magick, "-size", "1x4", accent_rgba,
                 str(base / "bar-fill.png")],
                check=True,
            )
            LOG.info("plymouth bar PNGs generated via %s (PIL absent)", magick)
        else:
            LOG.warning("Neither PIL nor ImageMagick available; Plymouth "
                        "loading bar will be invisible. Install python-pillow "
                        "or imagemagick.")

    plymouth_conf = (
        f"[Plymouth Theme]\n"
        f"Name=Blue Fox OS — {b['name']}\n"
        f"Description=Blue Fox OS boot splash for tenant {slug}\n"
        f"ModuleName=script\n"
        f"\n"
        f"[script]\n"
        f"ImageDir=/usr/share/plymouth/themes/{slug}\n"
        f"ScriptFile=/usr/share/plymouth/themes/{slug}/{slug}.script\n"
    )
    (base / f"{slug}.plymouth").write_text(plymouth_conf)

    # Sleek loading bar: 600 px wide, accent fill grows from 0 → 600 over
    # a boot cycle. Plymouth has no procedural drawing, so we ship 1×4 seed
    # images and Scale() them horizontally each refresh tick (~50 Hz).
    bar_width = 600
    bar_height = 4
    # Le splash source fait 1024x1024 : pose tel quel il mange l'ecran entier.
    # On le redimensionne a une hauteur fixe et on descend l'ensemble vers le
    # bas — un splash de demarrage doit rester discret.
    logo_height = 160
    logo_top_ratio = 0.58   # haut du logo, en fraction de la hauteur d'ecran
    plymouth_script = (
        f"# Generated for tenant {slug}\n"
        f"Window.SetBackgroundTopColor({bg_norm[0]:.4f}, {bg_norm[1]:.4f}, {bg_norm[2]:.4f});\n"
        f"Window.SetBackgroundBottomColor({bg_norm[0]:.4f}, {bg_norm[1]:.4f}, {bg_norm[2]:.4f});\n"
        f"\n"
        f"# Logo ('splash.png' est une vraie copie de branding/splash.png), mis a\n"
        f"# l'echelle a {logo_height} px de haut en preservant le rapport, et pose\n"
        f"# dans le tiers inferieur.\n"
        f"splash.raw = Image(\"splash.png\");\n"
        f"splash.ratio = {logo_height} / splash.raw.GetHeight();\n"
        f"splash.image = splash.raw.Scale(splash.raw.GetWidth() * splash.ratio, {logo_height});\n"
        f"splash.y = Window.GetHeight() * {logo_top_ratio};\n"
        f"splash.sprite = Sprite(splash.image);\n"
        f"splash.sprite.SetX(Window.GetWidth() / 2 - splash.image.GetWidth() / 2);\n"
        f"splash.sprite.SetY(splash.y);\n"
        f"\n"
        f"# Loading bar — track + fill, {bar_width}x{bar_height} px, sous le logo.\n"
        f"bar.x = Window.GetWidth() / 2 - {bar_width} / 2;\n"
        f"bar.y = splash.y + {logo_height} + 28;\n"
        f"\n"
        f"track.seed = Image(\"bar-track.png\");\n"
        f"track.image = track.seed.Scale({bar_width}, {bar_height});\n"
        f"track.sprite = Sprite(track.image);\n"
        f"track.sprite.SetX(bar.x);\n"
        f"track.sprite.SetY(bar.y);\n"
        f"\n"
        f"fill.seed = Image(\"bar-fill.png\");\n"
        f"fill.sprite = Sprite();\n"
        f"fill.sprite.SetX(bar.x);\n"
        f"fill.sprite.SetY(bar.y);\n"
        f"\n"
        f"progress = 0;\n"
        f"\n"
        f"fun refresh_callback() {{\n"
        f"    progress++;\n"
        f"    # Fill width grows 0 → {bar_width}. Loop after a short pause.\n"
        f"    width = progress * 4;\n"
        f"    if (width > {bar_width}) {{\n"
        f"        width = {bar_width};\n"
        f"        if (progress > {bar_width // 4} + 30) progress = 0;\n"
        f"    }}\n"
        f"    fill.scaled = fill.seed.Scale(width, {bar_height});\n"
        f"    fill.sprite.SetImage(fill.scaled);\n"
        f"}}\n"
        f"Plymouth.SetRefreshFunction(refresh_callback);\n"
        f"\n"
        f"# Zones de texte sous la barre.\n"
        f"prompt_y = bar.y + 28;\n"
        f"bullets_y = prompt_y + 26;\n"
        f"status_y = bullets_y + 26;\n"
        f"status_sprite = Sprite();\n"
        f"prompt_sprite = Sprite();\n"
        f"bullets_sprite = Sprite();\n"
        f"\n"
        f"fun update_status(msg) {{\n"
        f"    status_image = Image.Text(msg, "
        f"{accent_norm[0]:.4f}, {accent_norm[1]:.4f}, {accent_norm[2]:.4f});\n"
        f"    status_sprite.SetImage(status_image);\n"
        f"    status_sprite.SetX(Window.GetWidth() / 2 - status_image.GetWidth() / 2);\n"
        f"    status_sprite.SetY(status_y);\n"
        f"}}\n"
        f"Plymouth.SetUpdateStatusFunction(update_status);\n"
        f"Plymouth.SetMessageFunction(update_status);\n"
        f"\n"
        f"# ATTENTION - SANS CE BLOC, LA PHRASE DE PASSE LUKS EST INVISIBLE.\n"
        f"# Le plugin `script` ne dessine rien tout seul : un theme qui n'implemente\n"
        f"# pas SetDisplayPasswordFunction laisse l'ecran de demarrage fige pendant\n"
        f"# que cryptsetup attend une saisie. plymouthd accepte quand meme les\n"
        f"# touches, donc la machine demarre si on tape a l'aveugle — le defaut ne\n"
        f"# casse pas le boot, il le rend seulement incomprehensible. Vecu le\n"
        f"# 2026-07-20 : les themes Fedora (bgrt, spinner) utilisent le plugin C\n"
        f"# `two-step` qui gere la saisie nativement, d'ou l'absence de symptome\n"
        f"# tant que notre theme n'etait pas reellement charge.\n"
        f"fun display_password(prompt, bullets) {{\n"
        f"    # Masquer la barre : pendant une attente de saisie, une barre qui\n"
        f"    # progresse ment sur ce que fait la machine.\n"
        f"    track.sprite.SetOpacity(0);\n"
        f"    fill.sprite.SetOpacity(0);\n"
        f"\n"
        f"    prompt_image = Image.Text(prompt, "
        f"{accent_norm[0]:.4f}, {accent_norm[1]:.4f}, {accent_norm[2]:.4f});\n"
        f"    prompt_sprite.SetImage(prompt_image);\n"
        f"    prompt_sprite.SetX(Window.GetWidth() / 2 - prompt_image.GetWidth() / 2);\n"
        f"    prompt_sprite.SetY(prompt_y);\n"
        f"    prompt_sprite.SetOpacity(1);\n"
        f"\n"
        f"    # Un asterisque par caractere saisi. Image.Text sur une chaine vide\n"
        f"    # n'a rien a rendre, d'ou la garde sur bullets > 0.\n"
        f"    if (bullets > 0) {{\n"
        f"        dots = \"\";\n"
        f"        i = 0;\n"
        f"        while (i < bullets) {{\n"
        f"            dots = dots + \"*\";\n"
        f"            i++;\n"
        f"        }}\n"
        f"        bullets_image = Image.Text(dots, "
        f"{accent_norm[0]:.4f}, {accent_norm[1]:.4f}, {accent_norm[2]:.4f});\n"
        f"        bullets_sprite.SetImage(bullets_image);\n"
        f"        bullets_sprite.SetX(Window.GetWidth() / 2 - bullets_image.GetWidth() / 2);\n"
        f"        bullets_sprite.SetY(bullets_y);\n"
        f"        bullets_sprite.SetOpacity(1);\n"
        f"    }} else {{\n"
        f"        bullets_sprite.SetOpacity(0);\n"
        f"    }}\n"
        f"}}\n"
        f"Plymouth.SetDisplayPasswordFunction(display_password);\n"
        f"\n"
        f"# Retour a l'affichage courant : effacer la zone de saisie et rendre la barre.\n"
        f"fun display_normal() {{\n"
        f"    prompt_sprite.SetOpacity(0);\n"
        f"    bullets_sprite.SetOpacity(0);\n"
        f"    track.sprite.SetOpacity(1);\n"
        f"    fill.sprite.SetOpacity(1);\n"
        f"}}\n"
        f"Plymouth.SetDisplayNormalFunction(display_normal);\n"
        f"\n"
        f"# Passage de relais au gestionnaire de session : tout effacer.\n"
        f"fun quit_callback() {{\n"
        f"    splash.sprite.SetOpacity(0);\n"
        f"    track.sprite.SetOpacity(0);\n"
        f"    fill.sprite.SetOpacity(0);\n"
        f"    prompt_sprite.SetOpacity(0);\n"
        f"    bullets_sprite.SetOpacity(0);\n"
        f"    status_sprite.SetOpacity(0);\n"
        f"}}\n"
        f"Plymouth.SetQuitFunction(quit_callback);\n"
    )
    (base / f"{slug}.script").write_text(plymouth_script)

    # Copy (not symlink) the splash PNG into the theme dir. `plymouth-set-
    # default-theme -R` bakes the theme into initramfs via dracut, and dracut
    # handling of symlinks in /usr/share/plymouth/themes/ has been flaky enough
    # across Fedora versions that we just ship the bytes (~1 MB).
    splash_src = files_root / "usr/share/bluefox/branding/splash.png"
    splash_dst = base / "splash.png"
    if splash_dst.is_symlink() or splash_dst.exists():
        splash_dst.unlink()
    if splash_src.is_file():
        shutil.copy(splash_src, splash_dst)
    else:
        LOG.warning("splash source %s missing — Plymouth will show black bg", splash_src)

    LOG.info("emit plymouth theme %s", base)
    return base


def emit_plymouth_config(b: dict, files_root: Path) -> Path:
    """Wire Plymouth daemon to use the per-tenant theme."""
    target = files_root / "etc/plymouth/plymouthd.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    conf = (
        f"# Generated by scripts/generate_kde_theme.py — tenant {b['slug']}.\n"
        f"[Daemon]\n"
        f"Theme={b['slug']}\n"
        f"ShowDelay=0\n"
        f"DeviceTimeout=8\n"
    )
    target.write_text(conf)
    LOG.info("emit plymouth config %s", target)
    return target


def emit_sddm_config(b: dict, files_root: Path) -> Path:
    """Wire SDDM to use Breeze with our brand background + cursor."""
    target = files_root / "etc/sddm.conf.d/blue-fox.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    # We stick with Breeze (mature, ships in Plasma 6) and override its
    # background via the theme.conf symlink trick documented in the theme dir.
    # When BFOSL3 audit lands we may switch to a custom QML theme.
    conf = (
        f"[Theme]\n"
        f"Current=breeze\n"
        f"CursorTheme=breeze_cursors\n"
        f"Font={b['system_font']}\n"
        f"\n"
        f"[General]\n"
        f"# Tenant: {b['slug']}\n"
    )
    target.write_text(conf)
    LOG.info("emit sddm config %s", target)
    return target


def _kdeglobals_body(b: dict) -> str:
    icon_theme = f"bluefox-{b['slug']}"
    return (
        f"# Generated by scripts/generate_kde_theme.py — tenant {b['slug']}.\n"
        f"# Edit tenant.json + rebuild image rather than this file.\n"
        f"\n"
        f"[General]\n"
        f"ColorScheme={b['slug']}\n"
        f"AccentColor={b['accent']}\n"
        f"font={b['system_font']},10,-1,5,50,0,0,0,0,0\n"
        f"fixed=Hack,10,-1,5,50,0,0,0,0,0\n"
        f"menuFont={b['system_font']},10,-1,5,50,0,0,0,0,0\n"
        f"smallestReadableFont={b['system_font']},8,-1,5,50,0,0,0,0,0\n"
        f"toolBarFont={b['system_font']},10,-1,5,50,0,0,0,0,0\n"
        f"\n"
        f"[Icons]\n"
        f"Theme={icon_theme}\n"
        f"\n"
        f"[KDE]\n"
        f"LookAndFeelPackage={b['plasma_theme']}\n"
        f"contrast=4\n"
        f"widgetStyle=Breeze\n"
        f"\n"
        f"[WM]\n"
        f"activeFont={b['system_font']},10,-1,5,75,0,0,0,0,0\n"
    )


def emit_sddm_breeze_override(b: dict, files_root: Path) -> Path:
    """Donner au greeter SDDM le papier peint de la marque.

    emit_sddm_config() oriente SDDM vers le theme `breeze` d'origine, mature et
    livre avec Plasma 6. Le theme par tenant emis juste au-dessus pointe bien
    sur notre papier peint, mais il n'est PAS celui qui est charge : son propre
    commentaire annonce une bascule qui n'a jamais eu lieu. Resultat constate le
    2026-09-02 sur une machine fraichement installee : l'ecran de connexion
    affiche le fond Breeze d'origine.

    SDDM lit `theme.conf` PUIS `theme.conf.user` dans le repertoire du theme, et
    le second l'emporte. C'est le point de surcharge prevu en amont, et il
    survit a une mise a jour du paquet breeze, contrairement a une reecriture de
    theme.conf.
    """
    base = files_root / "usr/share/sddm/themes/breeze"
    base.mkdir(parents=True, exist_ok=True)
    conf = (
        f"# Genere par scripts/generate_kde_theme.py — tenant {b['slug']}.\n"
        f"# Surcharge de /usr/share/sddm/themes/breeze/theme.conf.\n"
        f"[General]\n"
        f"background={BRANDING_RUNTIME}/wallpaper.jpg\n"
        f"type=image\n"
    )
    target = base / "theme.conf.user"
    target.write_text(conf)
    LOG.info("emit sddm breeze override %s", target)
    return target


def emit_kscreenlocker_config(b: dict, files_root: Path) -> Path:
    """Papier peint de l'ecran de verrouillage, a l'echelle du systeme.

    Le `defaults` du look-and-feel ne s'applique qu'a un profil Plasma NEUF. Ce
    fichier-ci vaut pour toute session, y compris un profil deja cree, et c'est
    ce qui rend le reglage vrai sur une machine qui a deja servi.
    """
    target = files_root / "etc/xdg/kscreenlockerrc"
    target.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_path = f"/usr/share/wallpapers/{b['slug']}/contents/images/wallpaper.jpg"
    conf = (
        f"# Genere par scripts/generate_kde_theme.py — tenant {b['slug']}.\n"
        f"[Greeter][Wallpaper][org.kde.image][General]\n"
        f"Image=file://{wallpaper_path}\n"
        f"FillMode=2\n"
    )
    target.write_text(conf)
    LOG.info("emit kscreenlocker config %s", target)
    return target


def emit_icon_theme(b: dict, files_root: Path) -> Path:
    """Ship a minimal KDE icon theme inheriting Breeze, overriding only
    `start-here-kde` (the application menu button) with the tenant logo.

    KDE Plasma resolves icons via theme inheritance: requests for
    `start-here-kde` first hit our theme dir; everything else falls through
    to Breeze, so the rest of the desktop looks normal.
    """
    theme = f"bluefox-{b['slug']}"
    base = files_root / "usr/share/icons" / theme
    base.mkdir(parents=True, exist_ok=True)

    index = (
        f"[Icon Theme]\n"
        f"Name=Blue Fox OS — {b['name']}\n"
        f"Comment=Blue Fox icon overrides (inherits Breeze)\n"
        f"Inherits=breeze,hicolor\n"
        f"Directories=scalable/places\n"
        f"\n"
        f"[scalable/places]\n"
        f"Size=64\n"
        f"MinSize=16\n"
        f"MaxSize=512\n"
        f"Type=Scalable\n"
        f"Context=Places\n"
    )
    (base / "index.theme").write_text(index)

    places = base / "scalable/places"
    places.mkdir(parents=True, exist_ok=True)

    # Symlink the well-known Plasma launcher icon names to the brand SVG.
    # Multiple aliases ensure both Kicker, Kickoff, and modern launcher
    # widgets resolve our brand. fileutils handle existing symlinks.
    target = f"{BRANDING_RUNTIME}/app-icon.svg"
    for name in ("start-here-kde.svg", "start-here.svg", "start-here-symbolic.svg"):
        link = places / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)

    LOG.info("emit icon theme %s", base)
    return base


NEOFETCH_ASCII = """\
        .-+++=:.
     :+#@@@@@@@#+:
   -*@@@@@@@@@@@@@*-
  +%@@@@@@@@@@@@@@@%+
 +@@@@@%-     -%@@@@@+
*@@@@%-         -%@@@@*
@@@@%             %@@@@
@@@@%   .-=+=-.   %@@@@
@@@@@-+%@@@@@@%+-=@@@@@
*@@@@@@@@@@@@@@@@@@@@@*
 +@@@@@@%-     -%@@@@+
  +%@@@@%       %@@@%+
   -*@@@@%     %@@@*-
     :+#@@%   %@@#+:
        .-+= =+-.

  Blue Fox OS — {name}
"""


def emit_neofetch_config(b: dict, files_root: Path) -> Path:
    """Ship neofetch system-wide ASCII art + config in /etc/skel.

    Konsole/most terminals render the ASCII directly. Image_source pointing
    at our SVG would need chafa/jp2a to render — left to v1.1.
    """
    skel_dir = files_root / "etc/skel/.config/neofetch"
    skel_dir.mkdir(parents=True, exist_ok=True)

    ascii_path = files_root / "usr/share/bluefox/branding/neofetch.ascii"
    ascii_path.parent.mkdir(parents=True, exist_ok=True)
    ascii_path.write_text(NEOFETCH_ASCII.format(name=b["name"]))

    config = (
        f'# Generated by scripts/generate_kde_theme.py — tenant {b["slug"]}\n'
        f'print_info() {{\n'
        f'    info title\n'
        f'    info underline\n'
        f'    info "OS" distro\n'
        f'    info "Tenant" model\n'
        f'    info "Kernel" kernel\n'
        f'    info "Uptime" uptime\n'
        f'    info "Packages" packages\n'
        f'    info "Shell" shell\n'
        f'    info "Resolution" resolution\n'
        f'    info "DE" de\n'
        f'    info "WM" wm\n'
        f'    info "Theme" theme\n'
        f'    info "Icons" icons\n'
        f'    info "Terminal" term\n'
        f'    info "CPU" cpu\n'
        f'    info "Memory" memory\n'
        f'    info cols\n'
        f'}}\n'
        f'\n'
        f'image_source="ascii"\n'
        f'ascii_distro="off"\n'
        f'ascii_colors=(4 7)  # accent + text\n'
        f'ascii_bold="on"\n'
        f'image_backend="ascii"\n'
        f'\n'
        f'# Read ASCII from a file (system-wide, not per-user).\n'
        f'ascii="/usr/share/bluefox/branding/neofetch.ascii"\n'
    )
    (skel_dir / "config.conf").write_text(config)

    LOG.info("emit neofetch %s", skel_dir / "config.conf")
    return skel_dir


def emit_xdg_kdeglobals(b: dict, files_root: Path) -> Path:
    """System-wide defaults inherited by every new KDE session."""
    target = files_root / "etc/xdg/kdeglobals"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_kdeglobals_body(b))
    LOG.info("emit xdg kdeglobals %s", target)
    return target


def emit_skel_kdeglobals(b: dict, files_root: Path) -> Path:
    """Mirror in /etc/skel/.config so users created post-install also inherit."""
    target = files_root / "etc/skel/.config/kdeglobals"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_kdeglobals_body(b))
    LOG.info("emit skel kdeglobals %s", target)
    return target


def emit_skel_plasma_appletsrc(b: dict, files_root: Path) -> Path:
    """Pre-seed plasma-org.kde.plasma.desktop-appletsrc with the BF wallpaper.

    Why: setting LookAndFeelPackage in kdeglobals is not enough to make the
    wallpaper apply at first login — Plasma only consults the LnF `defaults`
    file when `plasma-apply-lookandfeel` is invoked (typically by the welcome
    wizard at the end of its flow). For accounts that log in BEFORE the wizard
    fires (or if the user closes it without finishing), Plasma writes its own
    appletsrc using its built-in defaults, which means the upstream Kinoite
    wallpaper. By shipping this file in /etc/skel/.config/, we ensure every
    new account starts its first session with the BF wallpaper already wired.

    Plasma will augment Containment 1 with the standard panel + widgets at
    first startup ; only the Wallpaper section is pre-seeded here.
    """
    target = files_root / "etc/skel/.config/plasma-org.kde.plasma.desktop-appletsrc"
    target.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_path = f"/usr/share/wallpapers/{b['slug']}/contents/images/wallpaper.jpg"
    body = (
        f"# Generated by scripts/generate_kde_theme.py — tenant {b['slug']}.\n"
        f"# Pre-seeds the desktop Containment so the very first session uses\n"
        f"# our wallpaper. Plasma adds taskbar / panels on top of this.\n"
        f"\n"
        f"[Containments][1]\n"
        f"activityId=\n"
        f"formfactor=0\n"
        f"immutability=1\n"
        f"location=0\n"
        f"plugin=org.kde.plasma.folder\n"
        f"wallpaperplugin=org.kde.image\n"
        f"\n"
        f"[Containments][1][Wallpaper][org.kde.image][General]\n"
        f"Image=file://{wallpaper_path}\n"
        f"PreviewImage=file://{wallpaper_path}\n"
        f"FillMode=2\n"
    )
    target.write_text(body)
    LOG.info("emit skel plasma appletsrc %s", target)
    return target


def emit_all(tenant: dict, files_root: Path) -> dict:
    b = resolve_branding(tenant)
    LOG.info(
        "tenant=%s primary=%s secondary=%s accent=%s font=%s",
        b["slug"], b["primary"], b["secondary"], b["accent"], b["system_font"],
    )
    return {
        "color_scheme": emit_color_scheme(b, files_root),
        "wallpaper_pkg": emit_wallpaper_package(b, files_root),
        "look_and_feel": emit_look_and_feel(b, files_root),
        "sddm_theme": emit_sddm_theme(b, files_root),
        "sddm_config": emit_sddm_config(b, files_root),
        "sddm_breeze_override": emit_sddm_breeze_override(b, files_root),
        "kscreenlocker": emit_kscreenlocker_config(b, files_root),
        "plymouth_theme": emit_plymouth_theme(b, files_root),
        "plymouth_config": emit_plymouth_config(b, files_root),
        "xdg_kdeglobals": emit_xdg_kdeglobals(b, files_root),
        "skel_kdeglobals": emit_skel_kdeglobals(b, files_root),
        "skel_appletsrc": emit_skel_plasma_appletsrc(b, files_root),
        "icon_theme": emit_icon_theme(b, files_root),
        "neofetch": emit_neofetch_config(b, files_root),
        "branding": b,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="generate_kde_theme")
    parser.add_argument("tenant_json", type=Path,
                        help="Path to validated tenant.json")
    parser.add_argument("files_root", type=Path,
                        help="Root of the BlueBuild files/ tree (creates /usr/* and /etc/* under it)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    tenant = json.loads(args.tenant_json.read_text())
    emit_all(tenant, args.files_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
