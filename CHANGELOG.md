# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning CalVer `YY.MM`.

## [Unreleased]

### Security

**Signature vérifiée des images, à l'installation et aux mises à jour** (#27109) :
- La politique de conteneurs publiée visait `localhost/blue-fox-os-<slug>` (le build se fait avec `IMAGE_REGISTRY=localhost`) : `ghcr.io/bluefoxconsultant/...` tombait sous `"": insecureAcceptAnything` et aucune mise à jour n'était vérifiée. `files/scripts/politique-conteneurs.sh`, lancé après le module `signing`, écrit une entrée `sigstoreSigned` + `matchRepository` par dépôt (bf, bf-surface, factice) avec deux clés (`keyPaths`) : `/usr/share/bluefox/keys/cosign-images.pub` (publication) et `cosign-images-secours.pub` (secours, clé privée hors ligne). Le build échoue si `default` n'est pas `reject`, si une des deux clés manque ou si elles sont identiques, si la politique relue sur le disque n'est pas celle attendue, si une portée `registries.d` est déclarée deux fois, ou si la portée qui décide ne demande pas les pièces jointes sigstore.
- `bluefox-rebase` passe en `ostree-image-signed:` quand la politique couvre l'image ; `bluefox-origine-signee.timer` bascule ensuite les postes existants (sans redémarrage, marqueur une fois démarré sur l'origine signée). Un déploiement en attente qui suit une autre image (rebase manuel, version épinglée) n'est jamais écrasé ; délai dépassé et transaction déjà en cours sont passagers.
- Kickstart zero-touch : la ligne `ostreecontainer` vient de `/tmp/bfos-image.ks` (filet non vérifié écrit par le `%pre`, remplacé par la forme vérifiée) ; la politique de l'installateur passe en `matchRepository` et reçoit son `registries.d`.
- `publish-image.sh`, étape 4b (juste après la poussée, avant SBOM et attestation) : porte canari. Dans l'image publiée, son propre skopeo juge par le proxy (`scripts/proxy-openimage.py`, OpenImage, le chemin de rpm-ostree) l'image elle-même, `factice:canari-signe` (doit passer) et `factice:canari-non-signe` (doit être refusé). Un refus de la politique remet l'index précédent sous le tag, relu au registre ; tout échec de la porte sort en code 3, qui arrête la republication des autres locataires (`ops/bfos/maj-images.sh`). Un verdict inconnu (registre lent, signature illisible) n'est jamais pris pour un refus.
- Relecture adverse 2 : chaque clé est **décodée** au build (base64 strict, clé ECDSA P-256, point sur la courbe, puis `openssl pkey -pubin` quand openssl est là) : un corps PEM abîmé passait le contrôle d'en-tête et aurait gelé le parc. Une clé illisible (« parsing public key », « PEM decoding failed », clé absente) est un refus de la porte ; un verdict se rejuge (refus confirmé une fois, inconnu rejoué deux fois, 60 s d'intervalle) et un inconnu **sans motif passager** remet aussi l'index précédent (un retard plutôt qu'un gel). Seule une ligne « : REFUSE : » vaut refus (un plantage du juge n'en est pas un). Le retour arrière précède tout message (disque plein) et distingue « remis », « copie faite mais NON CONFIRMÉ » et « IMPOSSIBLE ». L'index précédent illisible pour une autre raison que « manifest unknown » arrête avant le build. `set -E` : le trap ERR tient aussi dans les fonctions. La porte se résume en une ligne `PORTE_CANARI cause=… retour=…`, reprise par `maj-images.sh` dans `dernier.json`.
- `bluefox-origine-signee` : délais par commande (600 + 1500 + 1500 s) sous le `TimeoutStartSec=2h` de l'unité ; une origine épinglée par digest n'est plus sondée (son `upgrade --check` échouait chaque jour), seulement notée.
- Clé de vérification de l'étape 7 et de `verify-image.sh` configurable (`COSIGN_PUB`, `cosign.pub` par défaut) ; procédure de la clé de secours dans le README.

### Limites connues

- La porte canari juge l'image N avec la politique de N, pas avec celle des postes (N-1) : une image signée par une clé que N-1 ignore la passe, et le parc la refuse (pas de gel, mais plus aucune mise à jour). À vérifier à la main lors d'un changement de clé (README, « Clé de secours cosign », étape 6) ; jugement par N-1 reporté après le 3 novembre.

### Changed

**Verrouillage d'écran piloté par la politique** (#27109, lot 3) :
- `bluefox-policy-sync` applique `policies.auto_lock_minutes` (1 à 1440, défaut Odoo 15) : groupe `[Daemon][$i]` de `/etc/xdg/kscreenlockerrc` (`Autolock`, `Timeout`, `Lock`, `LockGrace=5`, `LockOnResume`, `RequirePassword`), immuable et grisé dans les Paramètres. 0 rend la main à la personne ; clé absente ou invalide, rien ne change.
- Le fichier est régénéré depuis `/usr/etc/xdg/kscreenlockerrc` (l'image y livre le fond) : une altération locale est défaite et notée « réglage local rétabli » dans `policy-sync.json`.
- À chaud : unité usager `bluefox-lock-reload.path` (`busctl`, Fedora 43 ne livre pas `qdbus6`), activée dans bf, bf-surface et factice. ⚠️ Effet flotte : 15 minutes imposées à tous les postes enrôlés dès l'image à jour. Imposé, pas inviolable (sudoer, inhibition par une application).

**Adresses des services lues dans la politique, phase 1** (#27109, lot 4 ; `bluefox-welcome` 0.3.0) :
- `merge_branding` superpose `services.{nextcloud,vaultwarden,authentik,odoo}.url` de la politique sur `tenant.json` (liste blanche, https avec un hôte ; vide ou invalide = l'adresse de l'image reste ; jamais `slug` ni `image_ref`). rclone, Bitwarden et Brave lisent le tenant fusionné.
- Les identifiants Nextcloud déposés par l'installation ne servent que si l'agent monte le Nextcloud pour lequel ils ont été frappés ; sinon le dépôt est effacé et le SSO prend le relais. Corrige le cas d'une autre organisation installée depuis l'image bf, dont le mot de passe d'application serait parti vers le Nextcloud de Blue Fox.
- Reste pour la phase 2 : replis en dur de l'agent, `%post` des services, libellés.

- `bluefox-policy-sync.timer` passe d'une fois par jour à **toutes les heures** (étalé sur 15 min, fixe par machine) : un prêt s'ouvre dans l'heure.
- La sonde de signature de l'installateur distingue un refus de signature d'une panne de lecture des signatures (« inconnue », avertissement seulement) ; un refus lisible fait sauter le flux d'appareil (porté de la copie servie par bf_zerotouch_install 18.0.4.0.7, #27109).

### Added

**Postes partagés : laboratoire et prêt** (#26119, avec bf_policy 18.0.2.12.0) :
- `bfos.poste=<code>` à l'installation : le poste appartient au profil, pas à la personne qui l'installe ; le serveur le nomme.
- sssd s'ouvre aux groupes du profil et aux emprunteurs inscrits ; un poste partagé sans personne passe en `deny` (sans règle, `simple` ouvrirait tout l'annuaire).
- `bluefox-policy-sync` réécrit l'accès des postes partagés quand un prêt commence ou finit ; l'agent d'accueil applique la session sans assistant.
- Le mot de passe de liaison de l'annuaire reste dans `sssd.conf` quand `/machine` ne le sert plus (bf_policy 18.0.2.11.2) : la synchro le reprend du fichier en place, et sans mot de passe nulle part elle garde le fichier au lieu d'écrire un poste sans session.
- Une fois `sssd.conf` écrit, l'installation réécrit la politique stagée sans le mot de passe de liaison (#26137, déjà dans la copie servie par bf_zerotouch_install 18.0.4.0.4) ; elle le garde si `sssd.conf` a échoué, pour qu'on puisse rejouer le script à la main.

**Re-synchronisation de la politique sur les machines déjà installées** (#23909) :
- Une machine reçoit désormais une identité à elle. Pendant le `%pre`, tant que le porteur OIDC de l'opérateur est en main, `bfos_provision.py` appelle `POST /api/v1/policy/enroll` et met le secret rendu dans `/etc/bluefox/machine.json` (0600, root). Odoo n'en conserve que le sha256.
- `bluefox-policy-sync.timer` (au démarrage + une fois par jour, étalée sur une heure) re-tire la politique depuis `GET /api/v1/policy/machine` et réécrit `provisioning.json` puis les deux listes Flatpak. Une politique modifiée dans Odoo atteint donc les postes en service, plus seulement les installations neuves.
- L'auto-déverrouillage TPM2 suit gratuitement : `bluefox-tpm-enroll.service` relit déjà `provisioning.json` à chaque démarrage.
- ⚠️ `system-flatpak-setup.timer` est en `OnBootSec=30` — elle ne repasse jamais sur une machine allumée. Quand les listes changent, le service déclenche donc lui-même `system-flatpak-setup.service` ; quand rien ne change, il ne réveille rien.
- Frontière assumée : le reste du bloc `install` (nom d'hôte, locale, clavier, mode de connexion) n'est pas ré-appliqué à chaud — ces réglages demandent `hostnamectl`/`localectl`/sssd et se testent en VM à part.
- Tolérance aux pannes : poste hors ligne, serveur muet ou machine révoquée → la dernière politique connue reste en place et le service sort en succès. Seules les pannes locales (écriture impossible, `bfos_apply.py` absent de l'image) sortent en échec.
- Côté Odoo : `bf_policy` 18.0.2.4.0 — modèle `bf.policy.machine`, endpoints `/api/v1/policy/enroll` et `/api/v1/policy/machine`, écran Policy > Machines avec bouton Révoquer. Une machine révoquée ne peut pas se ré-enrôler sous la même identité.

## [v0.1.0-rc.1] - 2026-05-22

Premier release candidate. Valide la chaîne install end-to-end (2 menuentries GRUB, zero-touch typed-domain + built-in defaults), le branding system-wide piloté par `tenant.json`, et le supply chain cosign + SBOM SPDX.

### Added

**Branding piloté par tenant.json** (BFOSD8) :
- `tenant.json` drives KDE Plasma, SDDM, Plymouth, icons, neofetch — un seul fichier de configuration par tenant pour tout l'aspect visuel.
- Lexend comme sans-serif système par défaut, fontconfig `95-bluefox-default-fonts.conf` + `fc-cache` post-install dans tous les recipes (#28, #22421).
- KDE Color Scheme dark BF avec foreground WCAG AAA sur fond sombre (Dolphin labels, champs wizard) (#27, #22422).
- Rotation aléatoire du wallpaper au firstboot parmi 16 originaux Upscayl 4x digital-art (#35, #37, #22433).

**Anaconda chrome overlay** (#22417/22418/22419) :
- Mécanisme d'overlay stage2 squashfs : `inst.profile=blue-fox-os` + assets sidebar/CSS/buildstamp pick-up automatique au build (#32).
- Sidebar gauche : Barracuda graphic 1540×6400 avec wordmark BF embarqué + glyphe renard, CSS `background-size: cover` (#34).
- `inst.keymap=ca` + `inst.lang=fr_CA.UTF-8` sur les 3 menuentries GRUB et 2 isolinux (#32).
- Neutralisation du slot `.product-logo` (le wordmark est dans la sidebar) (#36).

**Plymouth splash branded** (#22420) :
- Thème tenant baked dans l'initramfs au build pour éliminer l'écran noir au premier boot post-install (#31).
- `dracut --regenerate-all` au lieu de `-R` pour détecter correctement les kernels installés (commit 02518db).

**Plasma Welcome BFOS integration** (#22423/22424) :
- Intégration via extra-pages QML — la première session Plasma présente le wizard BFOS plutôt que le welcome KDE générique (#30).

**Default apps** (#22416) :
- Brave configuré comme handler par défaut pour `http/https/HTML` via mimeapps.list (#29).

**Zero-touch tenant provisioning** (PR #20, #21) :
- `bf-os.ks` baked dans l'ISO + chrome boot menu via `scripts/brand-iso.sh` (#20).
- Menuentry GRUB « zero-touch » avec `read` qui capture le domaine tenant, construit `inst.ks=https://<domain>/blue-fox-install.ks` à la volée (#21).
- Endpoint live `https://bluefoxconsultant.com/blue-fox-install.ks` servi par le module Odoo `bf_zerotouch_install`, header `x-bf-zerotouch-version: v1`.

**Supply chain — SBOM SPDX attestation** (P5.2 F7) :
- syft attaché en attestation cosign sur chaque image tenant (bf, bf-surface, factice) (#24).
- CI free-disk + force OCI registry auth pour syft attest-sbom (#25).
- `syft --scope squashed` + timeout CI 25 min sur le job attest-sbom (#26).

**Build infrastructure locale** :
- `scripts/build-iso.sh` pour produire l'ISO Anaconda en local (sans GH Actions) (commit 188a922).
- Pre-pull explicite de l'image BF (BIB ne pull plus automatiquement) (0b4c8ef).
- Mount `/var/lib/containers/storage` + option SELinux dans le wrapper (c737544).
- Support podman + docker, détection auth `$XDG_RUNTIME_DIR` (9fec221, 69c45c0).
- `SKIP_PULL=1` escape hatch pour itérer hors réseau (6644097).
- `build_branded_iso.sh` prep step avant BlueBuild dans CI (c8effee).
- Validation jsonschema rendue optionnelle quand le module Python est absent (5c3c4b8).

**Firstboot polish** :
- Wallpaper, splash + bouton Welcome appliqués correctement sur install fraîche (56cb33f).

**Recipes** :
- `bf-surface` pinné à `kinoite-main:43` (BFOSL2, linux-surface F44 pas encore publié) (#19).

### Caveats / Known issues

- **#22415** — Reboot inattendu Plasma mid-firstboot observé au smoke 2026-05-10. Contournement : laisser le système rebooter une fois. Investigation deadline 2026-06-14, ciblée v0.1.2.
- **#22424 Phase 2** — Wizard PyQt6 custom QML : différé v0.1.2 (Phase 1 plasma-welcome intégrée).
- **PR #23** — Tests pytest-qt du QWizard : merge déféré post-rc.1 pour ne pas perturber le tag.
- **PR #33** — SBOM CI timeout sur GitHub Actions : non-bloquant car les builds passent sur `vir` en local (cf. `feedback_bfos_builds_on_vir_not_gh.md`).
- **GH org `bluefoxconsultant` billing** bloqué depuis 2026-05-15 : pas d'impact release (pipeline local), affecte uniquement les checks PR automatiques.

### Supply chain verification

- Image OCI : `cosign verify --key cosign.pub ghcr.io/bluefoxconsultant/blue-fox-os-bf:rc.1`
- SBOM SPDX : attestation cosign attachée, vérifiable via `cosign verify-attestation`
- Packages GHCR publics depuis 2026-05-10 (vérification cosign anonyme possible)

## [v26.07] - 2026-07-30 (cible)

Première release publique. Contenu prévu :
- Image `ghcr.io/bluefoxconsultant/blue-fox-os-bf:v26.07` signée cosign
- ISO dérivée signée GPG BF
- Welcome agent PyQt6 fonctionnel (Thunderbird, Bitwarden, NC Talk, CalDAV, CardDAV)
- Authentik OIDC sur Nextcloud + Odoo BF
- Whitelabel paramétré par `tenant.json`
- Audit sécurité v1 passé
