# Blue Fox OS

Custom Fedora Kinoite image for Blue Fox Inc. clients. Image-based, atomique, brandable per-tenant via BlueBuild.

> **v1 portée :** pilote interne Olivier + Jace seulement. Premier client externe = v1.1.

## Stack

| Couche | Choix |
|---|---|
| Base | Fedora Kinoite (KDE Plasma 6, Wayland, btrfs+LUKS) |
| Build | [BlueBuild](https://blue-build.org/), en local (`make publish`) — la CI ne fait que valider |
| Registry | `ghcr.io/bluefoxconsultant/blue-fox-os-{slug}` |
| Signature image | cosign keypair (BlueBuild canonical) |
| Signature ISO | clé GPG BF dédiée |
| Versioning | CalVer YY.MM (v26.07 = juillet 2026) |
| Licence | MIT |

## Quickstart (développeur)

```sh
# Machine de build sous Garuda / Arch : installer les prérequis (une fois)
make bootstrap-garuda

# Build local d'une image, sans push (nécessite docker/podman)
make image SLUG=bf

# Publier : build + push + signature cosign + SBOM + attestation.
# Nécessite bluebuild, cosign, syft, podman, une auth ghcr.io et la clé cosign.
COSIGN_PRIVATE_KEY="$(cat /chemin/cosign.key)" make publish SLUG=bf

# Générer une ISO d'install dérivée
make iso SLUG=bf

# Vérifier la signature cosign d'une image publiée
make verify SLUG=bf TAG=v26.07
# = cosign verify --key cosign.pub ghcr.io/bluefoxconsultant/blue-fox-os-bf:v26.07
```

> **La CI ne construit pas les images.** GitHub valide (kickstart, schémas,
> tests, RPM) et vérifie chaque nuit que les images publiées portent toujours
> une signature valide ; il ne construit, ne pousse et ne signe rien. Corollaire
> assumé : les mises à jour upstream de Fedora n'atterrissent dans `:latest` que
> lorsque `make publish` est lancé.

### Machine de build hors Fedora

La chaîne tourne sur Garuda / Arch. Deux nuances :

- **`bluebuild` et `syft` ne sont packagés ni dans les dépôts Arch ni dans
  l'AUR** — ils passent par leurs installeurs upstream, ce que fait
  `make bootstrap-garuda`. `cosign`, `podman`, `buildah` et `skopeo` sont dans
  `extra`.
- **Le RPM du welcome agent se construit dans un container `fedora:43`.** Le
  `.spec` dépend de `pyproject-rpm-macros`, que le paquet `rpm-tools` d'Arch ne
  fournit pas : `scripts/build-welcome-rpm.sh` teste la macro (pas juste la
  présence de `rpmbuild`, qui donnerait un RPM faux sans erreur) et rebondit
  dans podman tout seul. Rien d'autre dans la chaîne n'est spécifique à Fedora.

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
| `.github/workflows/` | CI de validation : validate-kickstart + validate-config + tests + RPM, plus verify-published cosign (nocturne). Aucun build d'image. |

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
