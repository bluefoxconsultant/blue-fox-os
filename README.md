# Blue Fox OS

Poste de travail Linux image-based pour les équipes Blue Fox et leurs clients :
un Fedora Kinoite reconstruit par BlueBuild, brandé par tenant, qui s'installe
en s'authentifiant à l'Authentik de l'organisation et se configure à partir
d'une politique servie par Odoo.

Le pari du projet : le poste n'est pas un objet qu'on configure à la main, c'est
le rendu d'une fiche. L'image est immuable et signée, la configuration vient du
serveur, et une modification dans Odoo redescend toute seule sur les machines
déjà en service.

## État

Dernier tag : `v0.1.0-rc.1` (2026-05-22). Le travail courant vit sur
`feat/local-oci-builds` (**PR #39**, ouverte) ; `main` est resté au 2026-05-18.

À savoir avant de conclure quoi que ce soit :

- **`main` n'est pas la référence.** Tant que la PR #39 n'est pas fusionnée,
  `main` ne porte ni la sortie des builds hors CI, ni la re-synchronisation de
  politique, ni les correctifs de provenance et de signature.
- **Les images publiées sur GHCR datent d'avant la bascule.** `make publish`
  n'a pas encore tourné depuis ; `:latest` vient donc du dernier build CI
  (2026-05-18). Une image « vieille », c'est ça, pas une panne.
- **Le workflow GitHub de validation est désactivé** depuis le 2026-07-22
  (`gh workflow enable 267420271` pour le réactiver). Aucune vérification
  automatique ne tourne sur les PR en attendant.
- **La politique ne s'applique que sur le chemin zero-touch.** Le kickstart
  autonome `install/bf-os.ks` n'appelle ni `bfos_provision.py` ni
  `bfos_apply.py` : une install par ce chemin donne une machine brandée mais
  non provisionnée.
- **Le mode de connexion en production est `local`.** L'outpost LDAP Authentik
  n'est pas déployé, donc pas encore de compte de session central.

Reste avant la fusion : l'essai firstboot sur VM réelle (#22436).

## Stack

| Couche | Choix |
|---|---|
| Base | Fedora Kinoite via `ghcr.io/ublue-os/kinoite-main` (KDE Plasma 6, Wayland, btrfs + LUKS) |
| Build image | [BlueBuild](https://blue-build.org/), **en local** (`make publish`) |
| Registre | `ghcr.io/bluefoxconsultant/blue-fox-os-{slug}` |
| Identité | Authentik (OIDC device flow, 2FA sur un second appareil) |
| Politique | Module Odoo `bf_policy` — schéma `bf-policy/v2` |
| Signature image | cosign (keypair BlueBuild) + attestation SBOM SPDX (syft) |
| Signature RPM | clé GPG dédiée ; la publique est en dépôt et vérifiée au build |
| Versionnement | CalVer `YY.MM` (v26.07 = juillet 2026) |
| Licence | MIT |

Tenants : `bf` (interne), `bf-surface` (même image + noyau linux-surface),
`factice` (bac à sable de validation).

## Comment une machine arrive à Blue Fox OS

### Chemin zero-touch (celui qui provisionne)

1. Démarrage sur l'ISO, entrée GRUB « zero-touch ». Elle démarre sur l'**amorce**
   embarquée dans l'ISO (`/bfos-amorce.ks`, script `install/bfos_amorce.py`),
   qui demande à l'écran le domaine de l'organisation (pré-rempli à
   `bluefoxconsultant.com`), le **vérifie** (`https://<domaine>/blue-fox-install.ks`
   doit répondre avec l'en-tête `x-bf-zerotouch-version`), affiche le nom de
   l'organisation et attend une confirmation. Elle exécute ensuite les `%pre` du
   kickstart de l'organisation et inclut le reste. GRUB ne peut pas poser la
   question : `read` n'existe pas dans son binaire UEFI.
   Sans question : `bfos.domaine=<domaine>` sur la ligne `linux` (« e » au menu).
   Essais sur registre local : `bfos.ks=<url>`.
2. Le kickstart est **servi par Odoo** (`bf_zerotouch_install`), rendu à partir
   de la fiche Policy du tenant. Un hôte inconnu obtient un 404 plutôt que la
   politique de quelqu'un d'autre.
3. `%pre` (`install/bfos_provision.py`) : OAuth2 Device Grant contre Authentik,
   2FA sur un second appareil, **avant le partitionnement**. Le porteur obtenu
   sert à tirer `GET /api/v1/policy/me`, puis à enrôler la machine via
   `POST /api/v1/policy/enroll`. La politique part en
   `/var/lib/bluefox-welcome/provisioning.json`, le secret propre à la machine
   en `/etc/bluefox/machine.json` (0600, root). Le script **n'interrompt jamais
   l'installation** : sans réseau ni compte, on retombe sur les défauts baked.
4. `ostreecontainer` tire l'image du registre. Ce qui est installé vient donc du
   **réseau au moment de l'install**, pas de l'ISO.
5. `%post` (`install/bfos_apply.py`) applique le bloc `install` : nom d'hôte,
   locale, clavier console **et** XKB, fuseau, verrouillage du compte root,
   `sssd.conf` si le mode de connexion l'exige.
6. Au premier login graphique, l'agent welcome finit le travail côté session.

### Chemin autonome

`install/bf-os.ks` est baked dans l'ISO : Anaconda interactif, LUKS + compte
local, install depuis le dépôt ostree de l'ISO, puis `bluefox-rebase.service`
rebase vers l'image du tenant au premier démarrage. Utile pour installer sans
Odoo joignable ; ne provisionne pas.

## Le plan de politique

`bf_policy` (module Odoo, dépôt `odoo-clients`) est la source unique. Une fiche
Policy décrit un tenant de bout en bout : défauts de l'organisation + surcharges
par personne, fusionnés et validés en JSON `bf-policy/v2`.

Ce que la politique porte :

| Bloc | Contenu |
|---|---|
| `install` | locale, clavier (vconsole + XKB), fuseau, nom d'hôte, root, mode de connexion (`local` / `sssd` + LDAP) |
| `policies` | login hors ligne + péremption, MFA exigée, verrouillage auto, auto-déverrouillage TPM2 et ses PCR |
| `session` | couleur d'accent, fond d'écran, montages Nextcloud, PWA épinglées |
| `apps` | applications Flatpak à installer et à retirer |
| Autorisation | qui a le droit de provisionner : tout le monde, un groupe, ou une liste |

Cycle de vie d'une machine :

- **Enrôlement** à l'installation, tant que le porteur de l'opérateur est en
  main. Odoo ne garde que le sha256 du secret, jamais le secret.
- **Re-synchronisation** par `bluefox-policy-sync.timer` (au démarrage puis une
  fois par jour, étalée sur une heure) : la machine relit
  `GET /api/v1/policy/machine` avec son propre secret et réécrit
  `provisioning.json` + les deux listes Flatpak. Une politique modifiée dans
  Odoo atteint donc les postes déjà en service.
- **Révocation** depuis Policy > Machines : coupée à la synchro suivante, et
  aucune ré-inscription possible sous la même identité. L'autorisation est
  rejouée à chaque synchro, donc sortir quelqu'un d'un groupe coupe ses postes.
- **Tolérance aux pannes** : hors ligne, serveur muet ou machine révoquée, la
  dernière politique connue reste en place et le service sort en succès. Seules
  les pannes locales sortent en échec.

Frontière assumée : la synchro à chaud ne rejoue **pas** le bloc `install`
(nom d'hôte, locale, clavier, mode de connexion). L'auto-déverrouillage TPM2,
lui, suit gratuitement — son unité relit `provisioning.json` à chaque démarrage.

## Ce que l'image embarque

- **Branding système** piloté par `config/{slug}.json` :
  `scripts/generate_kde_theme.py` matérialise le thème Plasma, le schéma de
  couleurs, SDDM, Plymouth, les icônes et `/etc/skel`. Lexend en police système
  (TTF shippés, pas de paquet Fedora), thème Plymouth baked dans l'initramfs.
- **Agent welcome** (`welcome/`, PyQt6, packagé en RPM signé) : au premier
  démarrage, il présente **un seul écran** quand la machine a été provisionnée
  (identité déjà connue + une connexion Nextcloud), et retombe sur un assistant
  en 5 pages sinon. Il applique le thème KDE, le montage Nextcloud rclone, la
  politique Brave (page d'accueil, Floccus, PWA), l'URL Vaultwarden dans
  Bitwarden Desktop, et ouvre les Comptes en ligne KDE.
- **Credential Nextcloud par SSO** : l'agent obtient le mot de passe
  d'application via **Login Flow v2** (le protocole du client de bureau NC), pas
  via un porteur OIDC. Aucun mot de passe tapé dans l'assistant, et aucune
  configuration NC ou Authentik à changer.
- **Unités systemd** : `bluefox-rebase.service` (premier rebase),
  `firstboot.service` (agent welcome), `bluefox-policy-sync.timer`,
  `bluefox-tpm-enroll.service` (LUKS + TPM2), `rpm-ostreed-automatic.timer`.
  `tailscaled` est **masqué** : à activer consciemment.
- **Applications** : Brave, Thunderbird, client Nextcloud et Bitwarden en
  Flatpak Flathub ; Firefox retiré de l'image.

## Chaîne de build

Tout se construit **sur la machine d'Olivier** (Garuda / Arch), pas sur GitHub.

```sh
make bootstrap-garuda                  # prérequis, une fois
make image SLUG=bf                     # build local complet, sans push
make iso SLUG=bf                       # ISO d'install via bootc-image-builder
make brand-iso ISO=output/bootiso/install.iso
make verify SLUG=bf TAG=v26.07         # cosign verify d'une image publiée
make zerotouch-sync                    # pousse le gabarit KS vers bf_zerotouch_install
make lint && make test
```

Publier (build + push + signature + SBOM + attestation) exige **trois**
variables :

```sh
COSIGN_PRIVATE_KEY="$(cat ~/.config/bluefox/keys/cosign.key)" \
COSIGN_PASSWORD= \
BLUEFOX_RPM_GPG_NAME="Blue Fox OS Package Signing <info@bluefoxconsultant.com>" \
  make publish SLUG=bf
```

- `COSIGN_PASSWORD` doit être **définie**, même vide : la clé BlueBuild est un
  `ENCRYPTED SIGSTORE PRIVATE KEY` sans passphrase, et cosign qui lit un
  terminal absent meurt en `inappropriate ioctl for device`.
- `BLUEFOX_RPM_GPG_NAME` est exigée aussi par `make image` : sans elle le RPM
  welcome sort non signé et la vérification en-image fait échouer le build.
- `scripts/publish-image.sh` fait 7 étapes : RPM welcome → staging → branding
  KDE → `bluebuild --push` (qui signe) → SBOM syft → attestation cosign →
  relecture de l'étiquette publiée. Il refuse de publier depuis un arbre sale,
  en retard ou non poussé.

### Provenance et signatures

Les recipes émettent `org.opencontainers.image.revision` et `.source`, et
l'étape 7 **relit l'étiquette publiée** au lieu de faire confiance à l'export :
BlueBuild résout ces valeurs sans broncher sur une variable absente, un build
lancé à la main publierait sinon le littéral `${BF_GIT_REVISION}`.

Le RPM `bluefox-welcome` est signé sur l'hôte de build (la clé privée ne
descend jamais dans le container `fedora:43`) et sa signature est **vérifiée
avant installation** dans l'image. La clé publique vit en dépôt
(`files/usr/share/bluefox/keys/`) : sans elle, tout build échoue, et c'est
voulu.

### Machine de build hors Fedora

- `bluebuild` et `syft` ne sont packagés ni dans Arch ni dans l'AUR :
  `make bootstrap-garuda` passe par les installeurs upstream. `cosign`,
  `podman`, `buildah`, `skopeo` sont dans `extra`.
- Le RPM welcome se construit dans un container `fedora:43` :
  `pyproject-rpm-macros` n'existe pas côté Arch, et un `rpmbuild` présent mais
  sans la macro produirait un RPM faux **sans erreur**.
  `scripts/build-welcome-rpm.sh` teste la macro, pas le binaire, et rebondit
  tout seul.
- `syft` est le point fragile : il s'est fait OOM-killer à 13 Go de RSS, puis a
  rempli un `/tmp` en tmpfs, puis a cassé son flux HTTP/2 sur ~10 Go. Les
  garde-fous (`GOMEMLIMIT`, `TMPDIR` sur disque réel) sont dans le script.
  ⚠️ Ces échecs arrivent **après** l'étape 4 : l'image est poussée et signée
  avant que syft ne démarre. Un `make publish` en échec ne veut pas dire
  « rien n'est en ligne » — vérifier avec `cosign verify`.

### Où atterrit un correctif

| Correctif dans | Atteint une machine quand |
|---|---|
| `recipes/`, `files/`, RPM welcome | `make publish` a tourné (l'install tire l'image du registre) |
| `install/` (gabarit KS, `bfos_apply.py`) | `make zerotouch-sync` a tourné — ni image ni ISO nécessaires |
| Fiche Policy dans Odoo | à la synchro suivante, sur les machines déjà installées |

Reconstruire l'ISO ne change rien à la première ligne.

## CI

GitHub **ne construit aucune image**. Le workflow `Validate Blue Fox OS` valide
seulement : kickstart (`validate-kickstart`), configs contre le schéma
(`validate-config`), tests (`test-wizard`, plus la lane non bloquante
`test-wizard-ui` avec le stack Qt), RPM (`build-rpm`), et vérifie chaque nuit
que les images publiées portent toujours une signature valide
(`verify-published`).

Corollaire assumé : les mises à jour upstream de Fedora n'atterrissent dans
`:latest` que lorsque `make publish` tourne.

Le gate de release n'est donc pas la CI, c'est : image cosignée sur GHCR + tag
git + smoke QEMU vert.

## Tests

```sh
welcome/.venv/bin/python -m pytest -q welcome/tests install/tests scripts/tests
# 237 passed, 1 skipped
```

Le fichier `test_wizard_qt.py` se saute tout seul quand PyQt6 ou `libEGL` sont
absents ; avec le stack Qt installé, la lane UI le fait passer.

`scripts/tests/` n'est délibérément pas en `|| true` : ces tests gardent les
correctifs d'audit (provenance, dépôt Surface durci, ordre du renommage
`os-release`, source du manifeste tenant) et un échec doit se voir.

## Structure

| Dossier | Rôle |
|---|---|
| `recipes/` | `_base.yml` (référence commune) + `{slug}.yml` par tenant |
| `config/` | `schema.v1.json` + manifeste par tenant (branding, services, favoris) |
| `branding/` | Sources visuelles : logo, wallpapers, splash, thème GRUB, chrome Anaconda |
| `install/` | Kickstart autonome, gabarit zero-touch, `bfos_provision.py` / `bfos_apply.py`, tests |
| `files/` | Overlay `/usr` et `/etc` de l'image : unités systemd, helpers, gabarits, polices, clé publique RPM |
| `welcome/` | Agent de premier démarrage PyQt6, packagé en RPM |
| `scripts/` | Build, ISO, branding KDE, publication, synchro zero-touch, tests d'audit |
| `.github/workflows/` | Validation seule — aucun build d'image |

## Ne pas commiter

- Clé cosign privée — elle vit sur la machine de build
  (`~/.config/bluefox/keys/cosign.key`), plus en secret GitHub.
- Clé GPG privée de signature RPM — même endroit. La publique, elle, **doit**
  rester en dépôt.
- Manifestes de tenants clients : ils vivent côté serveur. Les exceptions
  autorisées (`bf`, `bf-surface`, `factice`, le schéma et l'exemple) sont
  listées dans `.gitignore`.
- Les assets KDE générés, `files/usr/share/bluefox/tenant.json` et
  `files/usr/lib/bluefox/` : matérialisés à chaque build. ⚠️ Un artefact laissé
  en place rend l'arbre sale et fait **refuser** la publication suivante.

## Documentation projet

- Projet Odoo : https://bluefoxconsultant.com/odoo/project/1765
- Matrice de connaissances : matrice #13
- Décisions et runbooks :
  `nextcloud.bluefoxconsultant.com/Blue Fox/Blue Fox OS/`
