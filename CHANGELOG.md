# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning CalVer `YY.MM`.

## [Unreleased]

### Security

**Poste partagé : plus de mot de passe Nextcloud de l'opérateur sur le disque** (#28055, trouvé à l'essai en VM de #26119) :
- Sur un poste partagé (bloc `seat`, ou politique sans login), le `%pre` frappait quand même un mot de passe d'application Nextcloud durable **au nom de qui autorise l'installation**. Le `%post` le déposait dans `/var/lib/bluefox-welcome/nc-credentials.json`, et `bluefox-seat-credentials` l'y laissait faute d'usager de siège, sans révocation. `bfos_provision.poste_sans_usager` arrête désormais la frappe avant tout appel réseau. Elle ne part pas non plus quand un poste partagé a été DEMANDÉ (`bfos.poste=`), même si un serveur antérieur a ignoré la demande.
- Postes déjà installés en v1.0.2 : au démarrage, quand la politique porte un bloc `seat`, `bluefox-seat-credentials` révoque d'abord le mot de passe auprès de Nextcloud (`DELETE /ocs/v2.php/core/apppassword`, https seulement, au mieux), puis efface le fichier. Une politique illisible ne décide rien. Sans réseau, le journal dit « NON révoqué » : il faut alors le retirer dans Nextcloud, appareil « Blue Fox OS (poste de siege) » de l'opérateur.

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

**Départ de la personne : verrouiller ou effacer le poste** (#23941, #27365 ; avec bf_policy 18.0.2.11.6) :
- `files/usr/libexec/bluefox-wipe` exécute un ordre servi dans le bloc `departure` de la politique. `lock` : purge du cache d'identifiants de sssd (`sss_cache -E`, arrêt, retrait des bases, relance) et verrouillage des sessions ouvertes, donc la fenêtre hors ligne se referme au lieu de durer encore `offline_max_days` ; le disque n'est pas touché. `wipe` : `cryptsetup luksErase --batch-mode` sur les volumes `crypto_LUKS` **de ce poste**, puis redémarrage.
- ⚠️ Les doubles d'essai sont injectés **à l'appel** (`run`, `opener`, `dormir`, et `main()` les prend aussi), jamais en valeur par défaut liée à l'import : un double installé par `monkeypatch.setattr(…, "run", …)` n'était alors jamais consulté, « aucune commande lancée » était vrai par construction, et la garde root du chemin destructeur n'était pas mesurée. Et la résolution se fait en `is not None`, **pas** en `or` : les doubles de la suite sont des sous-classes de `list`, donc faux tant qu'ils n'ont rien enregistré — avec `or`, la suite lançait pour de vrai `systemctl stop sssd`, `loginctl lock-sessions` et `sss_cache` sur la machine de build (seul polkit les a refusés). Même piège sur `argv or sys.argv[1:]`, où un `main([])` repartait sur les arguments du processus.
- L'ordre arrive sur l'**entrée standard**, jamais en argument : les arguments d'un processus se lisent dans /proc, et le compte rendu voyage avec le jeton de la machine. La garde est à côté du geste : l'ordre nomme sa machine, comparé à `/etc/bluefox/machine.json` avant de toucher à quoi que ce soit.
- ⚠️ **Les volumes effacés sont ceux du poste** : intersection de `lsblk` avec les entrées de `/etc/crypttab` et le volume qui porte la racine (`/` ou `/sysroot`, qui peut être absent de crypttab : clé TPM, déverrouillage par l'initramfs). Sans ce filtre, un SSD personnel chiffré, un disque client en récupération ou le disque de sauvegarde du labo branché au moment du passage horaire était détruit hors de tout mandat — et le compte rendu disait « done, 2/2 ».
- Les **autres** entrées de crypttab passent par une ceinture : écartées si leur 4e champ porte `noauto` ou `nofail` — la signature d'un disque chiffré **externe**, un disque de sauvegarde typiquement, que le poste sait ouvrir à la demande mais n'ouvre pas au démarrage. ⚠️ Le discriminant n'est **pas** `RM`/`HOTPLUG` : un NVMe interne se déclare très souvent `HOTPLUG=1` parce que le bus PCIe l'est, et le noyau dit vrai sur le bus, pas sur la propriété du disque. Sur un poste **bi-NVMe** dont les deux volumes sont dans crypttab, la ceinture `RM`/`HOTPLUG` écartait le second, le faisait disparaître des deux côtés de la fraction, et le poste rapportait « done, 1/1 volume(s) effacé(s) » avec un volume chiffré **intact** — dont l'emplacement de clé de la personne qui part, qui en a la phrase. `noauto`/`nofail` est un signal que le poste **possède**, écrit par qui l'a installé, et qui ne dépend d'aucun bus. `RM`/`HOTPLUG` restent lus, et seulement journalisés.
- Les trois formes de source de crypttab sont reconnues : `UUID=`, `PARTUUID=`, un chemin `/dev/...` et les liens `/dev/disk/by-uuid/` et `/dev/disk/by-partuuid/`. Seul `UUID=` l'était, et le reste était **jeté** : un poste dont crypttab nomme son volume autrement voyait son propre volume classé « hors de ce poste », donc laissé intact, et le compte rendu disait « done » quand même. (`bluefox-tpm-enroll` traite les deux formes depuis toujours.)
- **La fraction se compte sur les volumes chiffrés vus**, et les volumes écartés sont nommés avec leur motif dans le détail sur **toutes** les branches. Avec le nombre de volumes *retenus* au dénominateur, un écarté disparaissait des deux côtés : Odoo archivait, affichait « Effacé » et levait le marqueur de séquestre sur un poste dont un volume chiffré n'avait rien perdu. Un volume **de ce poste** (une entrée de crypttab) laissé intact fait commencer le détail par « INCOMPLET : n volume(s) non traité(s) », en tête pour survivre à la troncature à 255 caractères. On ne rapporte pas `failed` pour autant : écarter un disque de sauvegarde externe est le comportement **voulu**, et un échec sur tout poste qui en porte un serait une fonction morte, pas une garde.
- Les colonnes demandées à `lsblk` sont **graduées**, du jeu le plus riche au plus ancien : `lsblk` refuse *toute* la commande sur une colonne qu'il ne connaît pas (`MOUNTPOINTS` demande util-linux 2.37, `PARTUUID` 2.22), ce qui faisait de la sélection des volumes une fonction **morte** sur un hôte plus ancien — « aucun volume retenu », donc échec sur tous les postes de ce genre. Le repli rend aussi atteignable le `MOUNTPOINT` singulier, qui ne pouvait jamais servir.
- Un volume qui remonte **deux fois** (LUKS sur RAID : `md0` sous chaque disque membre ; multipath) n'a qu'un verdict : les indices de tous ses chemins se fusionnent, et la décision se prend après le parcours. Avant, il pouvait atterrir dans les deux tables — effacé **et** listé comme écarté.
- ⚠️ **Aucun volume retenu n'est pas un succès** : le poste rend compte d'un échec plutôt que de faire basculer la fiche en « effacé » sur un poste intact.
- Un effacement **partiel qui a emporté la racine** se rapporte « disque condamné », pas « échec » : le reste du départ (purge du cache, sessions coupées, compte rendu) a lieu, et le détail dit lequel des deux cas on est. Dire « échec » envoyait l'opérateur chercher un problème de réseau sur un poste qui ne redémarrera plus.
- **Rien ne se détruit avant d'avoir vérifié qu'on pourra le dire** : l'adresse du compte rendu est validée (non vide, https) avant le premier geste, et un marqueur d'**intention** est écrit avant le premier `luksErase`. Coupé en vol (SIGTERM du délai de l'unité, batterie retirée, exception inattendue), le poste garde de quoi dire « un effacement a commencé, résultat inconnu » au lieu de rester « effacé, sans marqueur, sans compte rendu ». Le chemin destructeur attrape les exceptions larges.
- Le compte rendu (`POST /api/v1/policy/departure-done`) passe **avant** le redémarrage, et s'il ne passe pas le poste **ne redémarre pas** : il verrouille ses sessions, garde un marqueur, et la synchronisation suivante réessaie de rendre compte avant de redémarrer. Un poste redémarré ne reparle plus, et « effacé » ne doit pas se confondre avec « jamais rallumé ».
- Un compte rendu **refusé** (409 : ordre annulé ou remplacé pendant l'exécution ; 400 et autres refus définitifs) ne jette plus le marqueur en silence : trace locale dans `departure-sans-suite.json` (0600), ligne `ALERTE` au journal, aucun redémarrage — cet état exige un humain. Et il ne se réessaie pas : un 400 brûlait 10 s à chaque passage horaire et empêchait le redémarrage pour toujours.
- Un ordre **refusé par la garde** est rapporté au serveur (`result="refused"`, avec l'`order_id` reçu tel quel) : sinon un poste réenrôlé laissait la fiche sur « ordre remis » indéfiniment.
- La **portée de la purge vient de l'ordre** (`seat_only`, `target_login`), plus de la politique, et le login est normalisé en forme courte (sssd nomme court). Un poste partagé sans usager visé ne purge **rien**. ⚠️ La version précédente la déduisait de `policy["user"]["login"]`, vide sur un poste partagé : la branche qui vide tout le cache de sssd se prenait précisément là où elle ne devait jamais se prendre, et tous les emprunteurs perdaient leur cache. Le login de la personne ne voyage plus dans le détail du compte rendu (Loi 25).
- Un `lock` **ciblé** (poste partagé) ne rend plus « done » sans avoir fermé la fenêtre hors ligne : (a) la cible est vérifiée (`getent passwd`), parce que `sss_cache -u` rend 0 **même quand elle ne désigne personne** — un ordre qui ne désigne personne n'est pas un succès ; (b) le mot de passe **haché** de la seule cible est retiré du cache (`ldbmodify` sur l'attribut `cachedPassword` de `/var/lib/sss/db/cache_<domaine>.ldb`, sssd arrêté le temps du geste parce qu'il sert sa copie en mémoire — aucune base n'est supprimée, les autres emprunteurs gardent tout) ; (c) si le haché n'a pas pu être retiré, le détail **le dit en tête** et la fiche l'affiche. ⚠️ `sss_cache` invalide le cache mais ne retire pas le haché : sans (b), la fenêtre de `offline_max_days` restait ouverte et `loginctl lock-sessions` ne faisait que verrouiller un écran que la personne visée déverrouille avec son mot de passe intact.
- Le verdict d'un `lock` ne tient plus au seul code de `sss_cache` : un `systemctl stop sssd` en échec ou des bases de cache toujours en place ne rendent plus « done ». Le marqueur naît déjà en 0600 (le mode est posé à la création, pas après), porte son `machine_uuid`, et un marqueur sans `result` utilisable ou venu d'un autre poste est refusé au lieu d'être rejoué en « done ».
- `bluefox-policy-sync` rejoue un compte rendu en attente en premier geste, puis passe l'ordre **tel qu'il arrive** avant les applications, l'accès et le verrou. La charge réduite (usager plus autorisé, mais ordre en attente) porte **son propre schéma** `bf-policy/order-v1` et aucune clé `install` : elle n'écrase pas `provisioning.json`, et un agent d'avant ce lot la refuse comme hors schéma au lieu de la prendre pour une politique. Assistant absent, non lançable, ou code de retour inattendu : `rc=1` et « ORDRE NON EXECUTE » dans `policy-sync.json`, plutôt que de laisser croire à un effacement. L'état « effacé, compte rendu en attente, pas de redémarrage » fait sortir l'unité en échec. Après un redémarrage demandé, la synchronisation **s'arrête là** (`systemctl reboot` est asynchrone : elle réécrivait Flatpak, sssd et le verrou d'écran pendant l'extinction).
- **Un seul canal pour un ordre** : Odoo sert la charge `bf-policy/order-v1` dès qu'un ordre attend, que l'usager du poste soit encore autorisé ou non, et un ordre ne voyage plus **jamais** dans une charge `bf-policy/v2`. La branche v2 ne cherche donc plus d'ordre ; un ordre qui y apparaîtrait quand même n'est **pas exécuté**, il est journalisé en `ALERTE` et l'unité sort en échec — le taire ferait afficher « Ordre remis au poste » pour toujours sans qu'aucun des deux bouts puisse le savoir.
- Le bloc `departure` est **expurgé** de la copie de session `policy-public.json` (0644), des deux côtés : dans `render_public_policy` (`install/bfos_apply.py`) et à l'écriture dans `bluefox-policy-sync`. Il portait le login de la personne **visée** et le mode de l'ordre, à chaque passage tant que l'ordre échouait : la personne visée y lisait qu'un effacement l'attend, et avait alors tout intérêt à ne plus rebrancher le poste — ce qui désarme la fonction — ou à sortir la clé maîtresse du noyau pendant que le volume est encore ouvert. La ceinture côté poste n'est pas redondante : l'image peut porter une copie de `bfos_apply.py` antérieure à ce lot.
- Le poste **annonce sa capacité de départ** à chaque synchronisation : en-tête HTTP `X-BFOS-Capabilities: departure`. bf_policy l'inscrit sur la fiche et refuse de poser un ordre sur un poste qui ne l'a pas annoncée. Sans cette annonce, un ordre pouvait être posé sur un poste dont l'image ne connaît pas le bloc `departure` : l'agent l'ignorait en sortant 0, unité verte, et la fiche disait « Ordre remis au poste » indéfiniment.
- Le code de `systemctl reboot` **se lit** : échec, alerte, nouvel essai en `--force`, et l'assistant sort en échec si rien n'a pris. Un redémarrage refusé laissait la fiche « Effacé » et archivée sur un poste qui tourne encore, volume ouvert et clé maîtresse dans le noyau — la fenêtre réversible, cette fois sans borne de temps.
- Un assistant **tué par un signal** (`-15` du `TimeoutStartSec=5min`, `-9` du tueur de mémoire) ne se rapporte plus « ORDRE NON EXECUTE » : `subprocess.run` rend un code négatif, qui tombait dans « code inattendu », sur un poste dont la racine peut très bien être déjà effacée. L'état dit « ordre peut-être agi, assistant interrompu par le signal n » et renvoie au marqueur.
- Le marqueur d'intention est **relu** après écriture, et l'ordre est **refusé** s'il n'est pas là (le refus remonte au serveur). L'`OSError` était avalée et l'effacement continuait : sur un `/var` plein ou remonté en lecture seule, on détruisait sans avoir pu écrire l'intention — le contraire du contrat « rien ne se détruit avant d'avoir vérifié qu'on pourra le dire ».
- Un marqueur **refusé** (résultat inutilisable, UUID d'un autre poste, JSON abîmé) est rangé en trace (`departure-marqueur-refuse.json`) et alerté **une fois**. Il restait en place, se rejouait à vide toutes les heures, et la synchronisation écrivait « compte rendu en attente rejoué (code 0) » avec `status: ok`. Le code du rejeu entre désormais dans celui de l'unité.
- La trace locale `departure-sans-suite.json` entre dans `policy-sync.json` **tant qu'elle existe** et y tient l'unité en échec : un ordre exécuté dont la fiche ne garde rien exige un humain, et c'est l'humain qui retire le fichier après l'avoir reporté. Rien ne la relisait, et l'unité redevenait verte au passage suivant.

Limites connues du départ :
- Un poste qui ne se rebranche jamais ne s'efface jamais. C'est vrai de toutes les suites de gestion de parc, y compris celles qui le vendent comme une garantie : le chiffrement au repos reste la réponse, l'effacement est une ceinture de plus.
- Latence : jusqu'à une heure (minuterie de synchronisation). Un poste éteint reçoit l'ordre à son retour.
- `luksErase` détruit les emplacements de clés, pas les données : un volume dont la clé maîtresse aurait fuité avant l'effacement reste théoriquement déchiffrable par qui la détient.
- ⚠️ **L'effacement n'est irréversible qu'au redémarrage.** Tant que le poste tourne, le volume est ouvert et sa clé maîtresse est dans le noyau : `dmsetup table --showkeys` la rend et `cryptsetup luksAddKey --master-key-file` reconstitue un emplacement de clé. La fenêtre d'attente du compte rendu, qui peut durer des heures, est donc une fenêtre où un root local peut défaire ce que l'ordre vient de faire. Limite assumée : l'alternative serait de redémarrer sans rendre compte, donc de perdre la différence entre « effacé » et « jamais rallumé » sur la fiche. À ne jamais présenter comme « il n'y a pas de retour ».
- 🔴 **Hypothèse NON LEVÉE : rien ne prouve que `cryptsetup luksErase --batch-mode` aboutit sur le volume de la racine, actif et monté.** `luksErase` ne réécrit pas les données, il écrase les emplacements de clés de l'en-tête — ce qui demande un accès exclusif au périphérique que le device-mapper tient peut-être déjà. S'il refuse (« device busy »), la racine échoue **systématiquement** et la fonction est morte sur tout le parc ; ce serait la troisième garde de cette classe dans ce chantier, après la ceinture des amovibles et le défaut de `seat_only`. Cela se mesure, cela ne se déduit pas : essai dédié dans `ops/bfos/essais-vm-23941.sh`, présenté comme la **levée de cette hypothèse** et non comme une preuve d'effacement. Rien ne doit être posé sur un poste réel avant.
- Un `lock` **ciblé** sur une image sans `ldb-tools` ne peut pas retirer le mot de passe haché de la cible : la fenêtre de `offline_max_days` reste alors **ouverte**, et le poste le dit en tête du détail du compte rendu (« fenêtre hors ligne NON fermée pour la cible : le mot de passe haché reste en cache »). Il rapporte quand même `done`, parce que le cache **est** invalidé et qu'écarter tout poste dont l'image n'a pas l'outil serait une fonction morte. À vérifier sur l'image en VM ; si l'outil manque, l'ajouter à la recette.
- Non éprouvé : la **réexécution d'un `wipe` sur un volume déjà effacé** (ordre rejoué, effacement partiel repris). `luksErase` sur un en-tête sans emplacement de clé est attendu idempotent, mais rien ne le mesure encore — à passer en VM sur charizard avec la campagne `ops/bfos/essais-vm-23941.sh`. À y vérifier aussi : ce que `lsblk` annonce réellement pour RM et HOTPLUG sur le disque système (virtio en VM, NVMe sur un vrai poste).

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
