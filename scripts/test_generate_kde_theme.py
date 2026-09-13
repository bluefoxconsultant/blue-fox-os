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
    assert "sddm_theme" not in b  # SDDM n'existe plus dans l'image (2026-09-13)


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
        "branding": {"plasma_theme": "blue-fox-dark"},
    })
    assert b["plasma_theme"] == "blue-fox-dark"
    assert "sddm_theme" not in b


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


def test_emit_plasmalogin_config_sert_le_papier_peint_du_locataire(tmp_path: Path):
    """Le greeter est plasmalogin, pas SDDM (mesure du 2026-09-13).

    Son defaut Fedora sert `file:///usr/share/wallpapers/Fedora/`. On reprend la
    forme exacte de ses cles, et on vise NOTRE paquet de papier peint.
    """
    artifacts = gkt.emit_all({"slug": "bf"}, tmp_path)
    cible = artifacts["plasmalogin_config"]
    assert cible == tmp_path / "etc/plasmalogin.conf.d/10-bluefox.conf"
    text = cible.read_text()
    assert "WallpaperPlugin=org.kde.image" in text
    assert "[Greeter][Wallpaper][org.kde.image][General]" in text
    assert "Image=file:///usr/share/wallpapers/bf/" in text
    assert "PreviewImage=file:///usr/share/wallpapers/bf/" in text


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


def test_plymouth_theme_can_display_a_password(tmp_path: Path):
    """Regression: a script theme draws everything itself, so without this
    callback the LUKS passphrase prompt is invisible and the machine looks
    hung. Vecu le 2026-07-20 — Olivier a du taper sa phrase de passe a
    l'aveugle. Ne jamais retirer cette assertion."""
    artifacts = gkt.emit_all({"slug": "bf", "branding": {}}, tmp_path)
    script = (artifacts["plymouth_theme"] / "bf.script").read_text()
    assert "Plymouth.SetDisplayPasswordFunction(" in script
    # Le retour a l'affichage courant doit exister aussi, sinon la zone de
    # saisie reste a l'ecran une fois le disque ouvert.
    assert "Plymouth.SetDisplayNormalFunction(" in script
    # Un asterisque par caractere saisi : sans retour visuel on ne sait pas
    # si le clavier repond.
    assert "bullets" in script


def test_plymouth_logo_is_scaled_down_and_low(tmp_path: Path):
    """Le splash source fait 1024x1024 : pose tel quel il occupe tout l'ecran.
    Il doit etre remis a l'echelle et descendu dans le tiers inferieur."""
    artifacts = gkt.emit_all({"slug": "bf", "branding": {}}, tmp_path)
    script = (artifacts["plymouth_theme"] / "bf.script").read_text()
    assert ".Scale(" in script.split("# Loading bar")[0], "logo non redimensionne"
    assert "Window.GetHeight() * 0.58" in script, "logo non descendu"
    # L'ancienne mise en page centrait sur la moitie de l'ecran.
    assert "Window.GetHeight() / 2 - splash.image.GetHeight() / 2" not in script


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


def test_plus_aucun_artefact_sddm_n_est_emis(tmp_path: Path):
    """Onze jours de reglages ecrits pour un systeme absent de la machine.

    Le correctif du 2026-09-02 visait `breeze/theme.conf.user` pour reparer
    « l'ecran de connexion affiche le fond Breeze d'origine ». Le fond n'etait
    pas celui de Breeze mais celui de FEDORA, et SDDM n'etait pas installe :
    /usr/bin/sddm absent, aucun paquet sddm, et `/usr/share/sddm/themes/`
    n'appartenant a AUCUN paquet — il n'existait que parce que ce script le
    creait.
    """
    gkt.emit_all({"slug": "bf", "branding": {}}, tmp_path)
    restes = [c for c in tmp_path.rglob("*") if "sddm" in str(c).lower()]
    assert not restes, f"artefacts SDDM encore emis : {restes}"


def test_kscreenlocker_config_uses_tenant_wallpaper(tmp_path: Path):
    b = gkt.resolve_branding({"slug": "bf", "branding": {}})
    target = gkt.emit_kscreenlocker_config(b, tmp_path)
    assert target == tmp_path / "etc/xdg/kscreenlockerrc"
    body = target.read_text()
    assert "[Greeter][Wallpaper][org.kde.image][General]" in body
    assert "Image=file:///usr/share/wallpapers/bf/contents/images/wallpaper.jpg" in body


def test_look_and_feel_defaults_cover_the_lock_screen(tmp_path: Path):
    """kscreenlocker ne lit pas le fond du bureau : il lui faut sa section."""
    b = gkt.resolve_branding({"slug": "bf", "branding": {}})
    base = gkt.emit_look_and_feel(b, tmp_path)
    body = (base / "contents" / "defaults").read_text()
    assert "[kscreenlockerrc][Greeter][Wallpaper][org.kde.image][General]" in body
    assert body.count("Image=file:///usr/share/wallpapers/bf/contents/images/wallpaper.jpg") == 2


def test_emit_all_ships_both_wallpaper_surfaces(tmp_path: Path):
    out = gkt.emit_all({"slug": "bf", "branding": {}}, tmp_path)
    assert out["plasmalogin_config"].exists()
    assert out["kscreenlocker"].exists()


def test_aucun_artefact_genere_ne_salit_git_status(tmp_path: Path):
    """Tout ce que ce script ecrit sous files/ doit etre ignore par git.

    🔴 Vecu le 2026-09-13, et ca a coute une passe de publication. L'emetteur
    plasmalogin a remplace les emetteurs SDDM, mais `.gitignore` couvrait
    `files/etc/sddm.conf.d/` et pas `files/etc/plasmalogin.conf.d/`. Le premier
    locataire de la passe a donc laisse un repertoire non suivi derriere lui,
    et `publish-image.sh` — qui refuse a juste titre de publier depuis un arbre
    sale — a rejete les DEUX locataires suivants.

    ⚠️ Le premier locataire, lui, a REUSSI. L'echec ne ressemblait donc pas a
    une regression de code mais a un caprice de machine, et c'est ce qui rend
    ce defaut cher : il se declare loin de sa cause.

    Le garde-fou vaut pour tout emetteur futur : on ne verifie pas une ligne
    connue de .gitignore, on verifie CE QUI EST REELLEMENT ECRIT.
    """
    import fnmatch

    gkt.emit_all({"slug": "bf", "branding": {}}, tmp_path)

    racine = Path(__file__).resolve().parents[1]
    regles = [
        l.strip().lstrip("/")
        for l in (racine / ".gitignore").read_text().splitlines()
        if l.strip() and not l.startswith("#") and not l.startswith("!")
    ]

    def couvert(rel: str) -> bool:
        """`rel` est ignore si une regle le designe, ou designe un de ses
        repertoires parents — c'est la semantique que git applique."""
        prefixes = [rel]
        parent = Path(rel).parent
        while str(parent) not in (".", "/"):
            prefixes.append(str(parent))
            parent = parent.parent
        for regle in regles:
            nu = regle.rstrip("/")
            for chemin in prefixes:
                if chemin == nu or fnmatch.fnmatch(chemin, nu):
                    return True
        return False

    nus = [
        "files/" + str(c.relative_to(tmp_path))
        for c in sorted(tmp_path.rglob("*"))
        if (c.is_file() or c.is_symlink()) and not couvert(
            "files/" + str(c.relative_to(tmp_path)))
    ]

    assert not nus, (
        "artefacts generes que .gitignore ne couvre pas — ils saliront "
        f"`git status` et feront refuser la publication : {nus}"
    )
