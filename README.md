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
| Licence | MIT pour le code ; marque et `branding/` exclus (voir [TRADEMARKS.md](TRADEMARKS.md)) |

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
- **Verrouillage d'écran** (#27109) : `policies.auto_lock_minutes` (1 à 1440)
  devient un groupe `[Daemon][$i]` de `/etc/xdg/kscreenlockerrc`, régénéré
  depuis la base de l'image (`/usr/etc`, qui porte le fond) ; grisé dans les
  Paramètres, rétabli à la synchro suivante s'il est altéré. 0 rend la main à
  la personne ; clé absente ou invalide, rien ne change. L'unité usager
  `bluefox-lock-reload.path` le fait relire à chaud par la session. Imposé,
  pas inviolable : un sudoer peut le réécrire jusqu'à la synchro suivante, et
  une application peut inhiber le verrouillage.
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
  KDE → `bluebuild --push` (qui signe) → porte canari (4b, la politique de
  l'image publiée juge l'image et deux canaris ; code 3 et retour arrière
  automatique si elle gèlerait le parc) → SBOM syft → attestation cosign →
  relecture de l'étiquette publiée. Il refuse de publier depuis un arbre sale,
  en retard ou non poussé, et s'arrête avant le build si l'index publié (le
  retour arrière de la porte) ne se lit pas.

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

### Clé de secours cosign

Les postes acceptent une image signée par l'une de **deux** clés (`keyPaths`
de `/etc/containers/policy.json`, écrite par `files/scripts/politique-conteneurs.sh`) :

| Clé | Publique | Privée |
|---|---|---|
| publication | `cosign.pub` = `files/usr/share/bluefox/keys/cosign-images.pub` | machine de build, `~/.config/bluefox/keys/cosign.key` (sans phrase) |
| secours | `files/usr/share/bluefox/keys/cosign-images-secours.pub` (sha256 `448dc741d397…`) | coffre Vaultwarden d'Olivier : `cosign-secours.key` et sa phrase de passe, jamais sur la machine de build |

**Quand s'en servir** : uniquement si la clé privée de publication **fuit**
(copiée hors de la machine de build, machine compromise, clé vue dans un
journal ou un dépôt) ou se **perd** (disque, effacement). Une clé perdue,
sans secours, gèle le parc : plus aucune image que les postes sachent
vérifier. Une clé qui fuit laisse quiconque la détient publier une image
acceptée. Dans les deux cas il faut une image de **transition** que les
postes en service acceptent (signée par le secours) et dont la politique ne
croit plus l'ancienne clé.

**Procédure**, une image de transition par locataire (bf, bf-surface, factice) :

1. Arrêter la republication automatique sur la machine de build :
   `systemctl --user stop bfos-maj-images.timer`.
2. Nouvelle clé de publication : `cosign generate-key-pair` dans
   `~/.config/bluefox/keys/` (effacer l'ancienne `cosign.key` si elle a fui).
   Copier la nouvelle `cosign.pub` à la racine **et** dans
   `files/usr/share/bluefox/keys/cosign-images.pub` (un test exige qu'elles
   soient identiques), et dans `CLE_EMBARQUEE` de `install/bfos_provision.py`
   et de sa copie du module Odoo `bf_zerotouch_install` (`make
   zerotouch-sync`), sinon les installations neuves refuseront les images
   suivantes. La clé de secours ne change pas. Commit, push sur main : la
   politique de l'image de transition ne croit plus que [nouvelle, secours].
3. Re-signer le canari signé avec la nouvelle clé, sinon la porte canari
   (étape 4b) le refuse sous la nouvelle politique et remet l'index précédent :

   ```sh
   D="$(skopeo inspect --format '{{.Digest}}' docker://ghcr.io/bluefoxconsultant/blue-fox-os-factice:canari-signe)"
   cosign sign --new-bundle-format=false --use-signing-config=false \
     --key ~/.config/bluefox/keys/cosign.key "ghcr.io/bluefoxconsultant/blue-fox-os-factice@${D}"
   ```

   L'ancien format (`--new-bundle-format=false --use-signing-config=false`)
   est celui que lit le skopeo des postes.
4. Publier avec la nouvelle clé (`make publish SLUG=<slug>`, `COSIGN_PRIVATE_KEY`
   = la nouvelle). Relever au journal l'« index publié avant la poussée »
   (N, l'image des postes) et l'index publié (N+1).
5. Contre-signer N+1 avec la clé de secours, index **et** manifestes enfants
   (`--recursive`). Sortir `cosign-secours.key` et sa phrase du coffre dans un
   dossier 0700 hors du dépôt, puis :

   ```sh
   COSIGN_PASSWORD="<phrase>" cosign sign --new-bundle-format=false \
     --use-signing-config=false --recursive \
     --key <dossier>/cosign-secours.key \
     "ghcr.io/bluefoxconsultant/blue-fox-os-<slug>@<index N+1>"
   ```

   Effacer le dossier ensuite. Entre 4 et 5, les postes refusent N+1 (mise à
   jour en échec, ils gardent N, pas de gel) : enchaîner sans attendre.
6. Juger N+1 avec la politique **des postes** (celle de N), pas avec la
   sienne (voir le piège ci-dessous) :

   ```sh
   podman run -i --rm --entrypoint /usr/bin/python3 \
     ghcr.io/bluefoxconsultant/blue-fox-os-<slug>@<index N> \
     - docker://ghcr.io/bluefoxconsultant/blue-fox-os-<slug>@<index N+1> \
     < scripts/proxy-openimage.py
   ```

   Attendu : `ACCEPTE`. Sinon, remettre N sous le tag sans attendre :
   `skopeo copy --all --preserve-digests docker://ghcr.io/bluefoxconsultant/blue-fox-os-<slug>@<index N> docker://ghcr.io/bluefoxconsultant/blue-fox-os-<slug>:latest`.
7. Vérifier : `./scripts/verify-image.sh <slug>` (nouvelle clé) et
   `cosign verify --key files/usr/share/bluefox/keys/cosign-images-secours.pub ghcr.io/bluefoxconsultant/blue-fox-os-<slug>@<index N+1>`.
8. Remettre la minuterie (`systemctl --user start bfos-maj-images.timer`).
   La clé de secours est sortie du coffre : la remplacer à froid à l'image
   suivante (nouvelle paire, `.pub` dans `files/`, empreinte du test
   `test_politique_conteneurs.py`, `CLE_SECOURS_EMBARQUEE` de l'installateur).

**Clé de vérification configurable.** L'étape 7 de `publish-image.sh` et
`scripts/verify-image.sh` vérifient avec `COSIGN_PUB` (chemin d'une clé
publique ; défaut : `cosign.pub` du dépôt). Une image dont la signature **et**
l'attestation viennent d'une autre clé (par exemple une republication signée
par la seule clé de secours, `COSIGN_PRIVATE_KEY` = clé de secours, le temps
de générer la nouvelle clé) se publie et se vérifie avec
`COSIGN_PUB=files/usr/share/bluefox/keys/cosign-images-secours.pub`.

🔴 **Piège : la porte canari juge l'image N avec la politique de N, pas avec
celle des postes (N-1).** Elle tire l'image qu'elle vient de pousser et la
juge avec ses propres clés. Une image signée par une clé que N connaît et que
N-1 ignore (nouvelle clé de publication, étape 4 seule) passe la porte, puis
se fait refuser par tout le parc. Pas de gel (les postes gardent N-1 et
réessaient), mais aucune mise à jour ne passe tant que l'image n'est pas
contre-signée par une clé que N-1 connaît : d'où les étapes 5 et 6.

### Limites connues

- **Porte canari** : jugement par la politique de l'image publiée (N), pas
  par celle des postes (N-1) ; voir le piège ci-dessus. Le jugement par N-1
  (second tirage) est reporté après le 3 novembre 2026 (#27109), comme la
  publication d'un candidat avant sa promotion en `:latest`.
- **Sonde de signature de l'installateur** (`install/bfos_provision.py`,
  `sonder_signature_image`) : elle passe par `skopeo copy`, qui enveloppe
  toute erreur de la politique dans « Source image rejected », panne de
  lecture des signatures comprise. Un registre lent au moment de la sonde
  affiche donc « l'image n'est pas encore signée » à tort. Ce n'est qu'un
  avertissement : le verdict qui compte reste celui du téléchargement par
  Anaconda.
- **Verdict inconnu sans motif passager** (proxy muet, client qui plante) :
  la porte remet l'index précédent après trois jugements. Asymétrie assumée :
  une semaine de retard vaut mieux qu'un parc gelé ; le cas se lit dans
  `dernier.json` (`porte_cause=inconnu`).

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

## Licence et marque

Le code est publié sous licence MIT (voir [LICENSE](LICENSE)). Les noms
« Blue Fox » et « Blue Fox OS », les logos, le mot-symbole, les fonds d'écran
et le contenu de `branding/` ne sont pas concédés sous cette licence : ils
restent la propriété de Les services de consultation Blue Fox, Inc. On peut
les citer pour désigner le projet, sans laisser croire à une approbation ; un
dérivé redistribué doit retirer la marque. Détails dans
[TRADEMARKS.md](TRADEMARKS.md). La police Lexend est sous SIL Open Font
License 1.1 (`files/usr/share/fonts/lexend/OFL.txt`).

## Documentation projet

- Projet Odoo : https://bluefoxconsultant.com/odoo/project/1765
- Matrice de connaissances : matrice #13
- Décisions et runbooks :
  `nextcloud.bluefoxconsultant.com/Blue Fox/Blue Fox OS/`
