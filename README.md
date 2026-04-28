# Blue Fox OS

Custom Fedora Kinoite image for Blue Fox Inc. clients. Image-based, atomique, brandable per-tenant via BlueBuild.

> **v1 portée :** pilote interne Olivier + Pablo seulement. Premier client externe = v1.1.

## Stack

| Couche | Choix |
|---|---|
| Base | Fedora Kinoite (KDE Plasma 6, Wayland, btrfs+LUKS) |
| Build | [BlueBuild](https://blue-build.org/) + GitHub Actions |
| Registry | `ghcr.io/bluefoxconsultant/blue-fox-os-{slug}` |
| Signature image | cosign keyless (Sigstore OIDC) |
| Signature ISO | clé GPG BF dédiée |
| Versioning | CalVer YY.MM (v26.07 = juillet 2026) |
| Licence | MIT |

## Quickstart (développeur)

```sh
# Build local d'une image (nécessite docker/podman)
make image SLUG=bf

# Générer une ISO d'install dérivée
make iso SLUG=bf

# Vérifier la signature cosign d'une image publiée (keyless OIDC)
make verify SLUG=bf TAG=v26.07
# = cosign verify ghcr.io/bluefoxconsultant/blue-fox-os-bf:v26.07 \
#     --certificate-identity-regexp "https://github.com/bluefoxconsultant/blue-fox-os" \
#     --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

## Rebase d'un poste vers Blue Fox OS

```sh
# Depuis un Kinoite stock
sudo rpm-ostree rebase ostree-image-signed:docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest
sudo systemctl reboot
```

## Structure

| Dossier | Rôle |
|---|---|
| `recipes/` | `_base.yml` commun + `{slug}.yml` par tenant (BlueBuild) |
| `config/` | `schema.v1.json` + exemples `{slug}.json` consommés par le welcome agent |
| `install/` | `bf-os.ks` Kickstart Anaconda (autoinstall) |
| `welcome/` | Agent de premier démarrage PyQt6, packagé en RPM |
| `scripts/` | Helpers de build, audit, release |
| `.github/workflows/` | CI : build matrix par tenant, sign cosign, publie ghcr.io |

## Documentation projet

- Projet Odoo : https://bluefoxconsultant.com/odoo/project/1765
- Matrice de connaissances : matrice #13
- Décisions : `nextcloud.bluefoxconsultant.com/Blue Fox/Blue Fox OS/Décisions/`
- Runbook : `nextcloud.bluefoxconsultant.com/Blue Fox/Blue Fox OS/Runbook/`

## Ne pas commiter

- `*.json` de tenant client (vit côté serveur, pas dans le repo)
- Clés cosign privées (GitHub Secrets)
- Clé GPG BF privée
