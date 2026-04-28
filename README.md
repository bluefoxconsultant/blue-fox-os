# Blue Fox OS

Custom Fedora Kinoite image for Blue Fox Inc. clients. Image-based, atomique, brandable per-tenant via BlueBuild.

> **v1 portée :** pilote interne Olivier + Pablo seulement. Premier client externe = v1.1.

## Stack

| Couche | Choix |
|---|---|
| Base | Fedora Kinoite (KDE Plasma 6, Wayland, btrfs+LUKS) |
| Build | [BlueBuild](https://blue-build.org/) + GitHub Actions |
| Registry | `ghcr.io/bluefoxconsultant/blue-fox-os-{slug}` |
| Signature image | cosign keypair (BlueBuild canonical) |
| Signature ISO | clé GPG BF dédiée |
| Versioning | CalVer YY.MM (v26.07 = juillet 2026) |
| Licence | MIT |

## Quickstart (développeur)

```sh
# Build local d'une image (nécessite docker/podman)
make image SLUG=bf

# Générer une ISO d'install dérivée
make iso SLUG=bf

# Vérifier la signature cosign d'une image publiée
make verify SLUG=bf TAG=v26.07
# = cosign verify --key cosign.pub ghcr.io/bluefoxconsultant/blue-fox-os-bf:v26.07
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
| `config/` | `schema.v1.json` + `bf.json` (manifeste tenant lead) ; clients = server-side |
| `branding/` | Assets `logo.png` / `wallpaper.jpg` / `splash.png` (BFOSD8 — TBD) |
| `install/` | `bf-os.ks` Kickstart Anaconda + sentinel `/var/lib/bluefox-welcome/needs-rebase` |
| `files/` | Overlay vers `/usr` dans l'image OCI : Lexend TTFs, `bluefox-rebase.service`, helper script |
| `welcome/` | Agent de premier démarrage PyQt6 (5-page wizard), packagé en RPM |
| `scripts/` | Helpers de build, audit, release |
| `.github/workflows/` | CI : validate-kickstart + validate-config + build matrix + verify-published cosign |

## Chaîne d'install

1. Boot ISO Kinoite avec `inst.ks=path/to/bf-os.ks`
2. Anaconda interactif : LUKS passphrase + user, install btrfs
3. Reboot → `bluefox-rebase.service` lit le sentinel et fait `rpm-ostree rebase` vers `blue-fox-os-bf:latest`
4. Reboot → image BF, welcome wizard se lance au premier login graphique

## Documentation projet

- Projet Odoo : https://bluefoxconsultant.com/odoo/project/1765
- Matrice de connaissances : matrice #13
- Décisions : `nextcloud.bluefoxconsultant.com/Blue Fox/Blue Fox OS/Décisions/`
- Runbook : `nextcloud.bluefoxconsultant.com/Blue Fox/Blue Fox OS/Runbook/`

## Ne pas commiter

- `*.json` de tenant client (vit côté serveur, pas dans le repo)
- Clés cosign privées (GitHub Secrets)
- Clé GPG BF privée
