# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning CalVer `YY.MM`.

## [Unreleased]

### Added
- Squelette initial du dépôt (recipes, welcome agent, install kickstart, GH Actions, Makefile).
- Pivot vers Fedora Kinoite + BlueBuild (vs plan initial Kubuntu+Cubic), 2026-04-27.

## [v26.07] - 2026-07-30 (cible)

Première release publique. Contenu prévu :
- Image `ghcr.io/bluefoxconsultant/blue-fox-os-bf:v26.07` signée cosign
- ISO dérivée signée GPG BF
- Welcome agent PyQt6 fonctionnel (Thunderbird, Bitwarden, NC Talk, CalDAV, CardDAV)
- Authentik OIDC sur Nextcloud + Odoo BF
- Whitelabel paramétré par `{slug}.json`
- Audit sécurité v1 passé
