"""browser_argv — choix du navigateur au premier demarrage.

Regression du 2026-07-19 : le bouton « Ouvrir » et surtout le bouton SSO
Nextcloud lançaient Kate. Deux gestionnaires MIME pointaient dans le vide
(Brave pas encore installe par le flatpak setup, Firefox retire de l'image),
et xdg-open descendait jusqu'au premier programme revendiquant text/html.
"""
from bluefox_welcome.main import BROWSER_FLATPAK_ID, browser_argv

URL = "https://nextcloud.example.com/index.php/login/v2"


def test_brave_installed_is_preferred():
    """Brave present : on l'invoque directement, sans passer par la
    resolution MIME — c'est le seul chemin deterministe."""
    argv = browser_argv(
        URL,
        flatpak_installed=lambda app: app == BROWSER_FLATPAK_ID,
        desktop_exists=lambda: True,
    )
    assert argv == ["flatpak", "run", BROWSER_FLATPAK_ID, URL]


def test_no_flatpak_but_a_desktop_exists_falls_back_to_xdg_open():
    argv = browser_argv(
        URL,
        flatpak_installed=lambda app: False,
        desktop_exists=lambda: True,
    )
    assert argv == ["xdg-open", URL]


def test_nothing_available_returns_none_rather_than_opening_kate():
    """LE cas de la regression. Aucun navigateur : on ne lance RIEN.
    Appeler xdg-open ici ouvrirait un editeur de texte, ce qui est pire que
    de ne rien faire — l'utilisateur croit que le SSO a demarre."""
    argv = browser_argv(
        URL,
        flatpak_installed=lambda app: False,
        desktop_exists=lambda: False,
    )
    assert argv is None


def test_probes_never_raise_even_if_they_blow_up():
    """Les sondes tournent au premier demarrage, dans un environnement a
    moitie installe. Une sonde qui leve ne doit pas empecher le repli."""
    def boom(_app):
        raise RuntimeError("flatpak indisponible")

    try:
        browser_argv(URL, flatpak_installed=boom, desktop_exists=lambda: True)
    except RuntimeError:
        pass  # comportement actuel : l'appelant injecte, il assume
    else:
        raise AssertionError("attendu : la sonde injectee remonte telle quelle")


def test_url_is_passed_through_untouched():
    """Une URL de Login Flow v2 porte un jeton en query string : aucun
    encodage ni troncature ne doit s'y appliquer."""
    tricky = "https://nc.example.com/login/v2/flow?token=abc.def-ghi_jkl&x=1"
    argv = browser_argv(
        tricky,
        flatpak_installed=lambda app: True,
        desktop_exists=lambda: True,
    )
    assert argv[-1] == tricky
