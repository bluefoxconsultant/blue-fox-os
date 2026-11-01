# Clé USB live : connexion par QR (#26870)

Blue Fox OS démarré depuis une clé USB jetable, partagée par les membres d'une
organisation. La session s'ouvre par le flux d'appareil Authentik : un QR à
l'écran, l'approbation (avec 2FA) sur le téléphone. La clé ne porte aucun
secret, et aucun mot de passe n'est tapé sur le PC.

Napkin et décisions : `notes/napkin-26870-cle-usb-live-jetable.md` (dépôt
tentaclaude), tâche BF #26870.

## Ce que contient ce dossier

| Fichier | Rôle |
|---|---|
| `pam/pam_bfos.c` | Cale PAM en C : lance l'aide en root, relaie le dialogue, pose `PAM_USER` |
| `bfos_live/pam_helper.py` | Aide : flux d'appareil, vérification de l'id_token, création de l'usager, jetons de session, choix « éphémère ou persistant » |
| `bfos_live/session.py` | Unité root `bfos-live-session@<nom>` : politique, applis, Nextcloud, profil chiffré, fermeture |
| `bfos_live/greeter.py`, `greeter_flow.py` | Écran de connexion maison (QR, choix de session, Wi-Fi) |
| `bfos_live/organisation.py` | Clé générique : domaine de l'organisation, lecture et validation de sa configuration publique |
| `image/` | Containerfile, fichiers de l'image, `applis/flatpaks.txt` (applis intégrées), construction de l'ISO, bancs VM |
| `config/live.json.example` | Configuration attendue dans `/etc/bfos-live/live.json` |
| `config/pam.d-greetd.example` | Pile PAM de greetd sur la clé |
| `tests/` | Essais de l'aide, essais de la cale contre la vraie libpam, banc `pam_harness.c` |

Le flux d'appareil est celui de l'installation (`install/bfos_provision.py`,
`device_authorize` et `poll_token_response`), copié à côté de l'aide dans
l'image.

## Les règles de sécurité

- **Ouverture** : l'écran greetd demande le compte `placeholder_user`
  (`bfos-connexion`, qui n'existe pas). L'aide fait le flux d'appareil, crée
  l'usager et rend son nom.
- **Réauthentification** (verrouillage) : l'application demande un compte qui
  existe. Le QR doit être approuvé par la même personne (même `sub`), et la
  cale refuse tout autre nom que celui demandé. Un passant qui scanne avec son
  propre compte ne déverrouille pas la session d'un autre.
- Tout autre compte demandé est refusé avant même d'afficher le QR.
- **Accès fermé par défaut** : `allowed_groups` vide = personne. La 2FA est
  exigée dans l'id_token (`amr` contient `mfa`).
- **Nom local** : préfixe lisible + empreinte du `sub` (`olivier-3fa2c1`).
  Changer son nom dans Authentik ne donne jamais le compte d'un autre.
- HTTPS seulement, sans suivre de redirection. Dossiers de `/run/bfos-live`
  vérifiés (root, pas de lien, pas inscriptibles). Jamais de compte système.
- La fermeture révoque les jetons en 15 s au plus, réseau ou pas.

## La session (2026-10-23)

Après le QR, la personne choisit :

- **Session éphémère** : rien n'est gardé sur la clé. Le mot de passe
  d'application Nextcloud frappé pour la session est révoqué à la fermeture.
- **Garder mon bureau sur cette clé** : tout le dossier personnel vit dans un
  fichier LUKS2 à son nom (empreinte du `sub`, pas son nom) sur la partition
  `BFOS_PROFILS`, créée au premier besoin dans l'espace libre du disque d'où la
  session a démarré (jamais sur un autre disque). La clé de déchiffrement est
  séquestrée dans Odoo (module `bf_policy_live`, `POST
  /api/v1/policy/live/profile-key`) et servie après le QR : rien n'est tapé et
  rien de secret n'est sur la clé. La révoquer dans Odoo rend le profil
  illisible (effacement à distance). Un profil qui ne s'ouvre pas n'ouvre
  JAMAIS une session éphémère en silence : la question est reposée.
- **Garder mon bureau dans Nextcloud** (`bfos_live/itinerant.py`) : le dossier
  personnel vit, chiffré, dans `.bfos-live/profil.bfosnc` du Nextcloud de la
  personne, et se retrouve sur n'importe quelle clé. Envoyé toutes les
  15 minutes et à la fermeture ; une seule session à la fois (`verrou.json`).

### Ce que porte le bureau Nextcloud

Tout le dossier personnel (bureau, documents, réglages de KDE et des applis,
profils de Brave et de Thunderbird), **sauf** ce qui est jetable ou se
régénère : `~/.cache`, la corbeille, l'index Baloo, les caches de Brave, de
Firefox et des Flatpak (`~/.var/app/*/cache`), `~/.local/share/flatpak`, et
tout ce qui est monté (le Nextcloud monté par rclone n'est pas recopié : il est
déjà dans Nextcloud). Liste exacte : `EXCLUSIONS` dans `itinerant.py`.
Plafond : 512 Mio chiffrés ; au-delà, rien n'est envoyé et l'ancien reste.

⚠️ Un client Nextcloud de bureau qui synchronise les fichiers cachés télécharge
`.bfos-live` à chaque envoi, et Nextcloud tient le fichier pendant la lecture
(423, ou 500 sur MOVE). Pire : un MOVE **efface la destination avant** de
renommer, donc un renommage refusé peut laisser l'envoi seul, sans bureau.
Essai du 2026-10-26 : renommage refusé, envoi resté orphelin, session suivante
repartie d'un bureau vide. Depuis :
- chaque COPY, MOVE et DELETE (et le verrou, et le registre) est repris
  pendant environ 90 s ;
- un envoi n'est jamais effacé après un renommage refusé, et la sauvegarde
  suivante de la même session reconnaît le sien ;
- à l'ouverture, l'envoi orphelin le plus récent, s'il l'est plus que le
  bureau (ou, sans bureau, que `profil.precedent.bfosnc`), est vérifié puis
  mis en place. Un orphelin plus ancien n'est jamais effacé.

Conseil : exclure `.bfos-live` du client de bureau (liste d'exclusion), qui
sinon télécharge le bureau chiffré toutes les 15 minutes sur chaque poste.

Dans les trois cas, l'unité root `bfos-live-session@<nom>` tire la politique de
l'organisation avec le jeton du QR, en dépose la copie expurgée et les
identifiants Nextcloud (échange RFC 8693) pour l'agent d'accueil, qui applique
thème, fond, montages Nextcloud et applis Odoo comme sur un poste installé.
Elle est arrêtée à la fermeture de session et à l'extinction (le profil est
alors démonté proprement).

Les applis de la politique sont intégrées à l'ISO (`image/applis/flatpaks.txt`). Une
appli ajoutée à la politique après le build s'installe à l'ouverture, en
mémoire vive. Le groupe local `bfos-live` installe des Flatpak sans mot de
passe (polkit) ; personne n'est admin.

Côté Authentik : le fournisseur Nextcloud doit fédérer `blue-fox-os-live`
(`jwt_federation_providers`, plan `nextcloud-bf-federation.yaml`), sinon
l'échange est refusé et l'agent retombe sur le SSO par navigateur.

## Langues

L'écran de connexion, les messages de la clé et l'agent d'accueil sont multilingues (fichiers PO
standard). Les textes restent écrits en français dans le code, marqués `_()` ou `N_()` ; une langue
= `bfos_live/po/<code>.po` (et `welcome/bluefox_welcome/po/<code>.po` pour l'agent), lu tel quel,
sans `.mo`. Ajouter une langue : copier le gabarit `bfos-live.pot` en `<code>.po`, remplir les
`msgstr`, puis `python3 outils/extraire_po.py` après chaque changement de texte.

- Avant la connexion : la langue de la clé (`live.json`, clé `langue`, défaut `fr`), puis le choix
  fait à l'écran (déposé dans `/run/bfos-live-ecran/langue`, relu par l'aide PAM).
- Après : la session Plasma et l'agent suivent la locale de la politique de la personne.
- Une traduction mal formée retombe sur le français ; l'image refuse de se construire avec un
  catalogue illisible ou aux champs différents (`verifier-langues`).

## Clé générique : l'organisation par son domaine (2026-10-27)

La clé publiée sur le site de Blue Fox OS ne connaît aucune organisation : son `live.json` n'est
qu'un socle (plages d'uid, groupes locaux, compte d'attente, tailles de profil), sans `issuer`.
Une clé dont le `live.json` nomme un fournisseur (banc de tournage, clé dédiée) reste configurée
comme avant et ne demande rien.

- **Au premier démarrage**, l'aide PAM demande le domaine (`bfos:domaine`), lit
  `https://<domaine>/bfos-live.json` (servi par `bf_policy_live` 18.0.1.4.0 dans l'Odoo de
  l'organisation : sans redirection, 64 Kio au plus, en-tête `x-bf-live-version: 1`), affiche le
  nom de l'organisation pour confirmation, puis passe au QR.
- **Liste blanche** (`organisation.CLES_LIVE`) : émetteur, client, adresses Authentik, groupes
  admis, adresses Odoo, langue, fuseau. Les adresses Odoo doivent être sur le domaine tapé ; tout
  champ douteux refuse TOUTE la configuration. Jamais les groupes locaux, les uid, le compte
  d'attente, les services PAM ni les dossiers. La 2FA ne fait que se durcir.
- **Dépôt** : `/run/bfos-live-organisation/` (live.json, tenant.json, organisation.json ; root,
  0644, rien de secret), relu par le verrouillage, la fermeture, l'unité de session et l'agent
  d'accueil (dont le `tenant.json` remplace celui de l'image : services et habillage de
  l'organisation).
- **Retenu sur la clé** : le domaine seul, dans l'espace des profils (`organisation`), écrit par
  l'unité de session ; relu au démarrage, en lecture seule et sans rien créer, par
  `bfos-live-organisation.service`. Une clé qui n'a jamais gardé de session n'a pas d'espace de
  profils : elle redemande le domaine.
- **Changer d'organisation** : le lien sous le QR dépose `/run/bfos-live-ecran/changer-organisation` ;
  l'aide arrête ce QR à sa prochaine attente et redemande le domaine (pré-rempli).

Côté Odoo, renseigner sur la fiche Policy : émetteur OIDC de la clé live, groupes admis, coffre.

## À régler hors de ce code

- Authentik : `sub` en mode « hashed user id » sur le fournisseur
  `blue-fox-os` ; vérifier que l'id_token du flux d'appareil porte bien `amr`
  avec `mfa` (sinon la clé refusera tout le monde) ; écran de consentement qui
  nomme l'appareil (hameçonnage par code d'appareil, le client est public).
- Image : `KillUserProcesses=yes` dans logind, pour qu'aucun processus d'une
  session ne survive dans la suivante.

## Pourquoi greetd et pas l'écran de KDE

plasma-login-manager affiche les messages PAM sur une seule ligne de texte : pas
de QR possible. greetd transmet chaque message PAM à l'écran de connexion (qu'on
écrit nous-mêmes) et relit `PAM_USER` après l'authentification, ce qui permet de
créer l'usager à la volée. Les postes installés gardent l'écran de KDE.

L'écran maison reconnaît la première ligne `bfos:device {json}` d'un message
d'information et dessine le QR à partir de `uri_complete`. Les autres clients
PAM (console, verrouillage) reçoivent le texte lisible, avec un QR en
demi-blocs sur la console.

## Essais

Ils exigent root, gcc et pam-devel. Les jouer dans le conteneur Fedora 43 :

```
docker run --rm --init -v $PWD:/src:ro -e QT_QPA_PLATFORM=offscreen bfos-live-essais:43-qt \
    bash -c 'cd /src/live && python3 -m pytest -q -p no:cacheprovider tests'
```

(Image : `FROM fedora:43` + `gcc make pam-devel python3-pytest shadow-utils`,
puis `python3-pyqt6` pour la variante `43-qt`. ⚠️ Sans PyQt6, TOUT
`test_greeter.py` est sauté, flux greetd compris : jusqu'au 2026-10-23, ces
essais ne tournaient pas.)

## Pas encore fait

- le paquet RPM et l'intégration dans une recette ;
- l'écran de verrouillage ;
- la question du domaine de l'organisation (la clé vise l'Authentik de BF).
