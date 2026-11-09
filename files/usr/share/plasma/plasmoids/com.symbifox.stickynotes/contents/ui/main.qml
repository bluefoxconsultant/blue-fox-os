/*
 * Symbifox Sticky Notes — the widget itself.
 *
 * This file holds the state machine and nothing else: pairing, the polling
 * cadence, the delta, the prune pass and the outbox. What it looks like lives
 * in FullRepresentation.qml and NoteCard.qml.
 *
 * ⚠️ Three states, and the widget must never blur them:
 *   unpaired  no token  → offer to pair, ask nothing of the server
 *   offline   a call came back with status 0 → keep showing the cache, slow down
 *   paired    talk to the server on the cadence in store.js
 *
 * An offline widget that behaved like an unpaired one would throw away a
 * working token every time a laptop lid closed.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.kirigami as Kirigami

import "../code/api.js" as Api
import "../code/store.js" as Store
import "../code/grille.js" as Grille
import "../code/papier.js" as Papier

PlasmoidItem {
    id: root

    /* ── Configuration ─────────────────────────────────────────────────── */

    /* ⚠️ Doit suivre la "Version" de metadata.json : c'est ce que le serveur
     * garde dans app_version et affiche dans « Mes appareils ». Deux
     * littéraux avaient déjà divergé. */
    readonly property string widgetVersion: "2.2.2"

    /* 🔴 NORMALISÉE à la lecture, et pas seulement quand `setServer` écrit.
     * La page de réglages expose `cfg_server` comme un alias nu sur son champ
     * de texte : ce qu'on y tape n'a jamais traversé `serverPattern`, donc
     * toute la garde de forme du lot 5a ne couvrait que l'appairage. Une
     * valeur irrecevable devient "" ici — donc `paired` tombe et l'écran
     * demande, au lieu de partir dans une requête. */
    readonly property string server: root.normalizeServer(Plasmoid.configuration.server)
    readonly property bool showShared: Plasmoid.configuration.showShared
    /* « Un carré, une note » : 0 = la liste. Voir config/main.xml. */
    readonly property int pinnedNoteId: Plasmoid.configuration.noteId
    /* ⚠️ `!== 0` et non `> 0` : un carré NEUF porte le sentinelle négatif
     * (voir `CARRE_NEUF`). Il est bien un carré — il ne doit surtout pas
     * afficher la liste — mais il n'a pas encore de note à demander. */
    readonly property bool pinnedMode: root.pinnedNoteId !== 0
    readonly property bool carreNeuf: root.pinnedNoteId < 0
    /* La note du carré a disparu de la portée : supprimée, ou plus partagée.
     * Le carré doit le dire au lieu d'afficher une note périmée pour
     * toujours. */
    property bool pinnedGone: false
    /* Le jeton de porteur. Il vit dans KWallet quand le poste en a un, partagé
     * par toutes les instances du widget : c'est ce qui permet de poser
     * plusieurs carrés sans apparier chacun, et ce qui sort le jeton du profil
     * en clair. Le repli — configuration de l'instance — reste pour un poste
     * sans portefeuille, et le widget le DIT alors à l'écran.
     * Voir contents/code/bfsn_wallet.py pour le pourquoi du détour. */
    property string token: ""

    /* ── State ─────────────────────────────────────────────────────────── */

    property var notesById: ({})
    property var outbox: []
    property string since: ""
    property var lastStamp: null
    property bool offline: false
    property string lastError: ""
    /* 🔴 Vrai quand l'échec vient de l'ADRESSE et non du reste. Posé le
     * 2026-11-02 : le widget d'Olivier portait encore
     * `server=https://bluefoxconsultant.com`, écrit dans son appletsrc par une
     * version dont le défaut était codé en dur. Vider ce défaut en 1.0.5 n'a
     * rien effacé — un défaut ne s'applique qu'à ce qui n'a pas de valeur. Le
     * message nommait bien l'adresse fautive, mais ne disait pas qu'on pouvait
     * la corriger juste au-dessus, et le champ était prérempli avec elle : on
     * reclique sans la relire. Dire ce qui ne va pas ne suffit pas s'il n'est
     * pas dit quoi faire. */
    property bool addressAtFault: false
    /* L'adresse que le COFFRE porte, telle que la dernière lecture l'a rendue.
     * Vide sur un poste apparié AVANT le correctif du 2026-11-02 : c'est ce
     * cas-là que `rangeServeurAuCoffre` rattrape. */
    property string coffreServeur: ""
    /* 🔴 Vrai dès qu'une réponse 2xx est revenue d'un appel PORTANT le jeton.
     *
     * Il existe parce que j'ai cru qu'un succès d'`askCapabilities` prouvait le
     * couple (jeton, adresse) : il ne prouve rien. Cette route est
     * `/bf_bloc_notes/mobile/v1/ping`, `auth="public"`, qui ne regarde JAMAIS
     * l'en-tête `Authorization` — un 200 dit seulement « cette adresse parle
     * Symbifox ». Graver l'adresse au coffre sur cette foi scellait le jeton
     * à une instance où il ne vaut peut-être rien, et le coffre étant partagé,
     * TOUTE instance neuve héritait ensuite de l'adresse fautive.
     *
     * ⚠️ Pourquoi pas un rappel global dans `api.js`, qui saurait pourtant si
     * l'appel portait un jeton : ce fichier est `.pragma library`, donc son
     * contexte est PARTAGÉ par toutes les instances du moteur QML. Un carré
     * écraserait le rappel d'un autre. */
    property bool jetonEprouve: false
    /* 🔴 Le coffre n'a pas pu être LU — ce qui n'est pas « aucune adresse
     * rangée ». Les confondre rendait la garde inopérante : un hoquet de
     * `kwallet-query` passait pour un coffre vide, ce qui désactivait la
     * comparaison de divergence ET rouvrait le rangement. Un hoquet du
     * portefeuille re-associait donc le jeton à l'adresse que portait
     * l'instance, quelle qu'elle soit. Quand c'est vrai : on ne juge pas. */
    property bool coffreServeurInconnu: false
    /* 🔴 m2 de la relecture finale : l'appairage demandé alors que la sonde
     * d'identité n'a pas encore répondu.
     *
     * `onEditingFinished` lance la sonde et `onClicked` suit dans le même tour :
     * `startPairing` trouvait `probedServer` vide, ne reconnaissait donc pas
     * l'alias, et s'appariait sur l'adresse TAPÉE — qui part ensuite au coffre
     * comme si c'était le canonique. Le commentaire affirmait le contraire.
     * On garde donc l'intention et on la rejoue quand la sonde a répondu. */
    property string appairageEnAttente: ""
    /* Quelqu'un est-il en train d'écrire quelque part ? */
    readonly property bool enTrainDEcrire:
        Object.keys(root.brouillons).length > 0

    /* Quick while somebody is looking, lazy once the widget is put away.
     *
     * 🔴 Valait `root.expanded` seul, ce qui rendait `PINNED_MS`
     * INATTEIGNABLE : un carré posé sur le bureau est déplié en permanence —
     * c'est la prémisse du mode — donc `nextDelay` voyait `active` vrai et
     * rendait la cadence rapide avant même de regarder `pinned`. Cinq carrés
     * faisaient vingt requêtes la minute chacun, exactement ce que `PINNED_MS`
     * devait empêcher. Un carré n'est « actif » que si on y écrit. */
    property bool interacting:
        root.expanded && (!root.pinnedMode || root.enTrainDEcrire)

    /* 🔴 Un jeton ET une adresse. Pas l'un des deux.
     *
     * Le jeton vit au coffre, partagé par toutes les instances — il a fallu
     * l'y mettre pour que « un carré par note » n'exige pas un appairage par
     * carré. L'adresse était restée par instance, défaut vide. Donc toute
     * instance neuve — une fenêtre `plasmawindowed`, un carré qu'on vient de
     * poser — démarrait avec un jeton valide et aucune adresse : appariée en
     * apparence, elle ne demandait rien, lançait son minuteur, et chaque passe
     * mourait dans `Api.call` sur `no_server` sans qu'un appel ne sorte.
     * C'est le défaut du 2026-11-02, « pas de synchro bien faite ».
     *
     * L'adresse part donc au coffre elle aussi (`bfsn_wallet.py`), et ceci
     * ferme la porte : sans endroit où appeler, on n'est pas apparié, et
     * l'écran demande. */
    readonly property bool paired: root.token.length > 0 && root.server.length > 0
    /* Les notes que le serveur refuse obstinément : la vue les annonce, pour
     * que personne ne croie son texte enregistré. */
    readonly property int stuckCount: Store.stuck(root.outbox).length
    /* Ce qui peut encore partir, par opposition à ce qui est bloqué pour de
     * bon. Les gardes doivent regarder CELUI-CI : une entrée refusée cinq fois
     * ne partira jamais, et attendre qu'elle parte est un conseil impossible. */
    readonly property int enAttente: root.outbox.length - root.stuckCount

    /* Y a-t-il, pour CETTE note, un texte tapé que rien n'a enregistré ?
     *
     * ⚠️ `enTrainDEcrire` répond à une autre question — « y a-t-il un brouillon
     * quelque part » — et il sert à régler la cadence. S'en servir comme garde
     * de fermeture faisait refuser un carré à cause du brouillon d'un autre. */
    /* Les notes que le serveur a refusées cinq fois : elles ne partiront plus
     * seules. Le commentaire de `stuckCount` promettait depuis le début que
     * « la vue les annonce » — elle ne les annonçait nulle part, et leur texte
     * n'était visible sur aucune surface. */
    readonly property var notesBloquees: Store.stuck(root.outbox)

    /* Remettre les compteurs à zéro et réessayer. Sans ce geste, une note
     * refusée est un cul-de-sac : elle bloque la fermeture d'un carré et le
     * rangement, et il faut éditer la configuration à la main pour s'en
     * sortir. */
    function reessayerLesBloquees() {
        var liste = root.outbox;
        for (var i = 0; i < liste.length; i++) {
            liste[i].tries = 0;
        }
        root.lastError = "";
        root.rangeOutbox();
        root.flush();
    }

    function brouillonNonEnregistre(noteId) {
        var garde = root.brouillons[noteId];
        if (garde === undefined || garde === null) {
            return false;
        }
        var note = root.notesById[noteId];
        return !note || garde !== (note.text || "");
    }
    readonly property var notes: Store.sortedNotes(root.notesById)

    /* 🔴 Le jeton de passe. Rien n'empêchait deux cycles de se chevaucher :
     * la réponse tardive d'un `notes/ids` arrivait après qu'une note soit
     * entrée dans le cache, l'effaçait du bureau, et la marque d'eau l'ayant
     * dépassée entre-temps, elle ne revenait jamais. Chaque passe porte donc
     * son numéro, et toute réponse d'une passe qui n'est plus la courante est
     * jetée. */
    property int passe: 0
    property bool enVol: false

    /* Les brouillons en cours d'édition, par identifiant de note.
     *
     * 🔴 Ils vivent ICI et pas dans la carte : le modèle de la liste est un
     * tableau JavaScript, donc toute réaffectation DÉTRUIT et recrée les
     * délégués. Le texte tapé dans une carte ouverte disparaissait au premier
     * changement venu d'ailleurs, en moins de trois secondes. */
    property var brouillons: ({})

    /* Pairing in flight */
    property string pairingCode: ""
    property string pairingSecret: ""
    property string pairingUrl: ""
    /* 🔴 Le nombre à deux chiffres que la personne doit RETROUVER parmi trois
     * sur la page de consentement. Sans lui à l'écran du widget, la
     * concordance de nombre ne vaut rien : elle n'existait que côté serveur,
     * la page proposait trois nombres et il n'y avait rien à comparer. C'est
     * tout le hameçonnage inverse que la relecture adverse avait relevé, laissé
     * ouvert par un câblage à moitié fait. Relevé le 2026-11-01. */
    property string matchNumber: ""
    property bool pairing: false

    /* 🔴 Sur le BUREAU, la surface pleine TOUJOURS — jamais l'icône.
     *
     * `switchWidth/Height` repliait le widget en icône sous 14 unités de
     * grille, soit environ 250 pixels. Un pense-bête de la taille d'un
     * pense-bête passait donc sous le seuil et devenait une icône : tirer le
     * coin pour faire un petit carré le faisait disparaître. Dans un PANNEAU,
     * au contraire, l'icône reste la bonne réponse — d'où le facteur de forme
     * plutôt qu'un seuil en pixels. */
    /* Posé en propriété NOMMÉE et pas lu deux fois : c'est la même question —
     * « sommes-nous sur le bureau ou dans un panneau ? » — et elle décide de
     * deux choses, la surface montrée et le fond peint. */
    readonly property bool surLeBureau: Plasmoid.formFactor === PlasmaCore.Types.Planar

    preferredRepresentation: root.surLeBureau ? fullRepresentation : compactRepresentation
    switchWidth: Kirigami.Units.gridUnit * 14
    switchHeight: Kirigami.Units.gridUnit * 14

    /* 🔴 Le fond de Plasma est retiré POUR UN CARRÉ, et pour lui seul.
     *
     * C'est ce fond-là, le « rectangle avec les notes dedans » : un pense-bête
     * doit peindre son propre papier, bord à bord, avec ses coins et son ombre
     * (voir StickyNote.qml).
     *
     * ⚠️ Mais la LISTE, elle, le garde — et ce n'est pas un oubli. La capture
     * du 2026-11-08 l'a montré : le thème peint ses textes en NOIR, donc une
     * liste sans fond serait du texte noir posé directement sur le fond
     * d'écran. Lisible sur un fond clair uni, illisible sur une photo. Le
     * premier jet retirait le fond à toute instance posée sur le bureau.
     *
     * Dans un panneau, même raison, en plus fort : une fenêtre surgissante
     * sans fond se lirait par-dessus n'importe quelle fenêtre. */
    /* ⚠️ SUR UNE SEULE LIGNE, et c'est la troisième fois que cette règle se
     * paie. Le harnais retire les affectations `Plasmoid.<prop>:` d'une copie
     * par un `sed` ligne à ligne : coupée en deux, l'affectation laisse sa
     * continuation orpheline, « Non-existent attached object », et les
     * quatre-vingt-seize essais tombent d'un coup. Même règle que les
     * affectations `Kirigami.Theme.<prop>:`, qui l'avaient déjà coûté le
     * 2026-11-03. */
    Plasmoid.backgroundHints: (root.surLeBureau && root.pinnedMode) ? PlasmaCore.Types.NoBackground : PlasmaCore.Types.DefaultBackground

    Plasmoid.icon: "note"
    toolTipMainText: i18n("Sticky Notes")
    toolTipSubText: root.paired
        ? (root.offline ? i18n("Offline — showing the last notes received")
                        : i18np("%1 note", "%1 notes", root.notes.length))
        : i18n("Not paired yet")

    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}

    /* ── Synchronisation ───────────────────────────────────────────────── */

    /* One timer, whose interval is recomputed after every pass. A fixed-rate
     * timer would either hammer the server while the widget sits folded in a
     * panel, or feel dead when someone is actually typing. */
    Timer {
        id: pulse
        interval: Store.nextDelay({ offline: root.offline, active: root.interacting,
                                    pinned: root.pinnedMode })
        repeat: true
        running: root.paired
        triggeredOnStart: true
        onTriggered: root.probe()
    }

    /* The cheap probe. It is the only call made on the fast cadence; the delta
     * follows only when the server says something is newer. */
    function probe() {
        if (!root.paired || root.pairing || root.enVol) {
            return;
        }
        if (!root.capabilitiesKnown) {
            root.askCapabilities();
            return;
        }
        /* Les deux se gardent elles-mêmes et ne partent qu'une fois : la
         * palette tant qu'elle n'est pas connue, le semis tant qu'il n'a pas
         * eu lieu. Les appeler ici plutôt qu'au premier dessin, c'est les
         * appeler quand il y a un jeton ET une adresse. */
        root.demandePrefs();
        root.semerLesCarres();
        if (root.carreNeuf) {
            /* Un carré neuf n'a rien à demander : sa note n'existe pas encore.
             * 🔴 Sans ce retour, `Api.one(-1)` partait à chaque battement, le
             * serveur rendait 404, et le carré annonçait « cette note a
             * disparu » avant même qu'on ait tapé la première lettre. */
            root.flush();
            return;
        }
        if (root.pinnedMode) {
            /* Un carré ne fait ni delta, ni sonde, ni ménage : une requête,
             * une note. Voir Api.one. */
            root.probeOne();
            return;
        }
        root.enVol = true;
        root.passe += 1;
        var maPasse = root.passe;
        /* 🔴 Le repli, qui était MORT. `hasStamp` était déclaré, posé par le
         * ping, et lu nulle part : chez un locataire dont le bloc-notes est
         * antérieur à 18.0.4.4.0 — BSI, PMEC, et la PRODUCTION Blue Fox
         * aujourd'hui, en 18.0.4.3.0 — `notes/stamp` n'existe pas. Le widget
         * y prenait un 404 à chaque battement, affichait « corrige l'adresse »
         * sous une adresse parfaitement correcte, et le delta nu (qui existe
         * depuis 18.0.3.0.0) n'était jamais tenté. Même défaut que « `/ping`
         * n'était jamais appelé », une couche plus bas. */
        if (!root.hasStamp) {
            root.pull(maPasse, 0);
            return;
        }
        root.suivi(Api.stamp(root.server, root.token,
            root.showShared && root.hasSharedNotes, root.since,
            function (data) {
                if (maPasse !== root.passe) { return; }
                root.offline = false;
                root.lastError = "";
                root.marqueJetonEprouve();
                if (Store.shouldPull(root.lastStamp, data)) {
                    root.lastStamp = { stamp: data.stamp, count: data.count };
                    root.pull(maPasse, 0);
                } else {
                    root.lastStamp = { stamp: data.stamp, count: data.count };
                    root.enVol = false;
                    root.flush();
                }
            },
            function (err) { root.enVol = false; root.onCallFailed(err); }));
    }

    /* Le délai : QML n'en a pas, donc le voici (voir api.js). */
    /* 🔴 Une LISTE, pas une référence. m3 de la relecture finale.
     *
     * `appelEnCours` ne gardait qu'un appel, écrasé par le suivant — et
     * `afterToken` en lance DEUX (la sonde d'identité puis la sonde de notes).
     * Le premier n'était donc ni suivi, ni annulable, ni abandonné à la
     * destruction : son rappel tirait sur un `root` détruit (« Value is null »
     * observé dans une passe). C'est précisément le défaut qu'on avait déclaré
     * corrigé le 11-01, à moitié. */
    property var appelsEnVol: []

    /* 🔴 UN DÉLAI PAR APPEL, et non un minuteur partagé.
     *
     * `chrono.restart()` à chaque inscription faisait repousser l'échéance par
     * le trafic SAIN : la sonde bat toutes les 3 à 30 secondes, donc un appel
     * vraiment bloqué n'atteignait jamais ses quinze secondes et n'était jamais
     * abandonné — exactement le défaut qu'on croyait refermer en faisant passer
     * les écritures par ici. Et quand le minuteur finissait par tirer, il
     * abandonnait aussi les appels en bonne santé, ce qui rendait un « hors
     * ligne » sur une écriture qui se portait bien.
     *
     * Chaque appel porte donc sa propre échéance, et le minuteur repasse. */
    readonly property int delaiAppel: 15000

    function suivi(xhr) {
        if (!xhr) {
            return xhr;
        }
        var liste = root.appelsEnVol.slice();
        liste.push({ xhr: xhr, du: Date.now() + root.delaiAppel });
        /* ⚠️ Bornée : `api.js` ne nous dit pas qu'un appel s'est terminé, donc
         * rien ne se retire tout seul. On garde les derniers, ce qui suffit :
         * `giveUp()` sur un appel déjà fini est sans effet — api.js tient son
         * propre drapeau. */
        while (liste.length > 16) {
            liste.shift();
        }
        root.appelsEnVol = liste;
        if (!chrono.running) {
            chrono.start();
        }
        return xhr;
    }

    /* Abandonner les SEULS appels dont l'échéance est passée. */
    function abandonneLesEnRetard() {
        var maintenant = Date.now();
        var reste = [];
        for (var i = 0; i < root.appelsEnVol.length; i++) {
            var e = root.appelsEnVol[i];
            if (!e || !e.xhr) {
                continue;
            }
            if (maintenant >= e.du) {
                if (e.xhr.giveUp) {
                    e.xhr.giveUp();
                }
            } else {
                reste.push(e);
            }
        }
        root.appelsEnVol = reste;
        if (reste.length === 0) {
            chrono.stop();
        }
    }

    /* Tout abandonner : au délai d'attente, et à la destruction du widget. */
    function abandonneTout() {
        var liste = root.appelsEnVol;
        for (var i = 0; i < liste.length; i++) {
            if (liste[i] && liste[i].xhr && liste[i].xhr.giveUp) {
                liste[i].xhr.giveUp();
            }
        }
        root.appelsEnVol = [];
        chrono.stop();
    }

    /* ⚠️ RÉPÉTITIF, et il ne juge plus en bloc : il repasse et n'abandonne que
     * ce qui a dépassé SON échéance. Deux secondes de grain suffisent pour un
     * délai de quinze. */
    Timer {
        id: chrono
        interval: 2000
        repeat: true
        onTriggered: {
            var avant = root.appelsEnVol.length;
            root.abandonneLesEnRetard();
            if (root.appelsEnVol.length < avant) {
                root.enVol = false;
            }
        }
    }

    /* M7 : ce que ce locataire sait faire. Sans cette question, un widget
     * posé chez un client dont le bloc-notes est antérieur à 18.0.4.4.0
     * appelait des routes absentes et n'affichait qu'« http_404 ». */
    property bool capabilitiesKnown: false
    property bool hasStamp: true
    property bool hasSharedNotes: true
    property bool hasVisibleIds: true

    /* ── Qui répond, et à quelles couleurs ─────────────────────────────── */

    /* Le nom du locataire, tel qu'il s'annonce. Affiché AVANT qu'on approuve :
     * on voit à qui on s'apparie, ce qui ferme la dernière porte du
     * hameçonnage inverse que la concordance de nombre laissait entrouverte. */
    property string tenantName: ""
    /* L'adresse que l'instance annonce (`web.base.url`). 🔴 C'est elle que la
     * page de consentement ouvrira : si elle diffère de ce qu'on a tapé,
     * l'appairage casse au milieu. Vécu sur le banc 27250. */
    property string canonicalUrl: ""
    /* L'adresse réellement sondée. ⚠️ Sans ce témoin, la réponse d'une sonde
     * lancée sur une ancienne adresse arrive après qu'on en a tapé une autre,
     * et le widget affiche le nom du mauvais locataire. */
    property string probedServer: ""
    property bool probingServer: false
    /* Ce qu'on dit quand l'adresse tapée et l'adresse canonique diffèrent.
     * Ce n'est pas une erreur : c'est un fait à annoncer. */
    property string aliasNotice: ""

    /* La marque du locataire. Vide = on garde le thème de la personne, ce qui
     * est le bon défaut : un widget Plasma appartient d'abord au bureau où il
     * vit. */
    property string brandAccent: ""
    property string brandDark: ""

    /* ⚠️ Une couleur venue du réseau ne se peint pas sans être relue. Le
     * serveur valide déjà la forme, et c'est exactement pour cela qu'il faut
     * la valider ici aussi : une garde posée d'un seul côté n'est pas une
     * garde, et c'est ce widget qui peint. */
    readonly property var hexPattern: /^#[0-9A-Fa-f]{6}$/

    function safeHex(valeur) {
        var v = String(valeur === undefined || valeur === null ? "" : valeur).trim();
        return root.hexPattern.test(v) ? v : "";
    }

    /* Ce que l'identité rapporte, posé en un seul endroit. */
    function applyIdentity(data) {
        var brand = (data && data.brand) || {};
        root.tenantName = String((data && data.tenant) || "");
        root.canonicalUrl = Api.trimServer((data && data.canonical_url) || "");
        root.brandAccent = root.safeHex(brand.accent);
        root.brandDark = root.safeHex(brand.dark);
    }

    function forgetIdentity() {
        root.tenantName = "";
        root.canonicalUrl = "";
        root.brandAccent = "";
        root.brandDark = "";
    }

    /* Vrai quand l'instance répond sous une AUTRE adresse que celle qu'on lui
     * parle. Le domaine convivial d'un client et son canonique peuvent
     * différer, et c'est le canonique qui gouverne la page de consentement. */
    readonly property bool serverIsAlias:
        root.canonicalUrl.length > 0 && root.probedServer.length > 0
        && root.canonicalUrl !== root.probedServer

    /* Sonde une adresse sans rien engager : pas de jeton, aucune écriture.
     * Appelée quand la personne quitte le champ d'adresse, pour qu'elle voie à
     * qui elle parle AVANT de cliquer. */
    function probeServerIdentity(adresse, silencieux) {
        var cible = root.normalizeServer(adresse);
        if (!cible) {
            root.forgetIdentity();
            root.probedServer = "";
            return;
        }
        if (cible === root.probedServer && root.tenantName.length > 0) {
            return;     // déjà connue : ne pas repayer l'aller-retour
        }
        root.probingServer = true;
        root.forgetIdentity();
        root.suivi(Api.identity(cible,
            function (data) {
                root.probingServer = false;
                /* ⚠️ La réponse d'une adresse qu'on ne demande plus est jetée. */
                if (root.normalizeServer(adresse) !== cible) {
                    return;
                }
                root.probedServer = cible;
                root.applyIdentity(data);
                root.rejoueAppairageEnAttente();
            },
            function (err) {
                root.probingServer = false;
                root.probedServer = "";
                root.forgetIdentity();
                /* ⚠️ Aussi sur un ÉCHEC : sinon un clic resterait sans effet,
                 * et personne ne saurait que le widget attend quelque chose. */
                root.rejoueAppairageEnAttente();
                /* Pour un widget DÉJÀ apparié, l'identité est un agrément, pas
                 * une étape : son échec ne doit pas peindre une erreur en
                 * travers de notes qui s'affichent très bien. */
                if (!silencieux) {
                    root.lastError = root.humanError(err || { error: "offline" });
                }
            }));
    }

    /* L'appairage qu'on a mis en attente le temps que la sonde réponde. */
    function rejoueAppairageEnAttente() {
        var cible = root.appairageEnAttente;
        if (!cible) {
            return;
        }
        root.appairageEnAttente = "";
        root.startPairing(cible);
    }

    function askCapabilities() {
        /* 🔴 Suivi comme les autres : sans cela cet appel-ci n'était pas
         * annulable, et son rappel tirait après la destruction du widget. Le
         * harnais l'a montré — « root is null » au retour du ping, une fois le
         * plasmoïde retiré. Sans conséquence pour la personne, mais une
         * erreur qui salit le journal de plasmashell en masque d'autres. */
        root.suivi(Api.capabilities(root.server, root.token,
            function (data) {
                var f = (data && data.features) || [];
                root.hasStamp = f.indexOf("stamp") >= 0;
                root.hasSharedNotes = f.indexOf("shared_notes") >= 0;
                root.hasVisibleIds = f.indexOf("visible_ids") >= 0;
                root.capabilitiesKnown = true;
                root.offline = false;
                /* ⚠️ PAS de `rangeServeurAuCoffre()` ici : cette réponse vient
                 * d'une route publique et ne dit rien du jeton. Voir
                 * `jetonEprouve`. */
                root.probe();
            },
            function (err) {
                err = err || { error: "offline" };
                /* Un locataire qui ne répond même pas au ping : on suppose le
                 * minimum (le delta seul, qui existe depuis 18.0.3.0.0) plutôt
                 * que de rester muet. */
                if (err.error === "offline" || err.error === "no_server") {
                    root.offline = true;
                    return;
                }
                root.hasStamp = false;
                root.hasVisibleIds = false;
                root.hasSharedNotes = false;
                root.capabilitiesKnown = true;
                root.probe();
            }));
    }

    /* Ce jeton ne vaut pas pour cette instance : elle l'oublie en mémoire et le
     * dit. Elle NE touche PAS au coffre — les autres instances vont bien.
     *
     * Tout ce qui décrit une synchronisation en cours part avec lui : garder
     * `since`, `lastStamp` ou `capabilitiesKnown` ferait reprendre un cycle sur
     * un autre serveur avec une marque d'eau qui ne veut rien dire. */
    function dissocieJeton(message) {
        root.token = "";
        root.jetonEprouve = false;
        root.capabilitiesKnown = false;
        root.since = "";
        root.lastStamp = null;
        root.enVol = false;
        root.passe += 1;      // toute réponse encore en vol est périmée
        root.lastError = message;
    }

    /* 🔴 B2 : le changement d'adresse, PENDANT que le widget tourne.
     *
     * Le constat au démarrage ne suffisait pas, et ma justification était
     * fausse : pour un processus déjà en marche, constater au démarrage est
     * strictement plus faible qu'intercepter le geste. La page de réglages
     * promet « changer l'adresse oblige à réapparier » ; sans ceci, `paired`
     * restait vrai, `capabilitiesKnown` aussi, et le battement suivant — trois
     * à trente secondes plus tard — faisait sortir le jeton en en-tête
     * `Authorization` vers l'adresse fraîchement tapée.
     *
     * Ceinture ET bretelles : le constat au démarrage reste, celui-ci le
     * double. ⚠️ `coffreServeur` vide (poste sans portefeuille, ou coffre
     * illisible) : on ne sait pas à quoi comparer, donc on ne juge pas — le
     * repli sans portefeuille garde jeton et adresse dans la même
     * configuration, où ils changent ensemble. */
    onServerChanged: {
        if (root.coffreServeurInconnu || !root.coffreServeur) {
            return;
        }
        if (root.coffreServeur === root.server) {
            /* 🔴 L'adresse REJOINT celle du coffre. Trouvé le 2026-11-03 sur le
             * poste d'Olivier : son applet portait encore
             * `https://bluefoxconsultant.com/` (écrit par une version dont le
             * défaut était en dur), le coffre portait le jeton du banc, donc la
             * garde de divergence a dissocié le jeton — correctement, c'est
             * elle qui a empêché le jeton de partir vers la production.
             *
             * Mais le message disait « remets cette adresse », et remettre
             * l'adresse ne produisait RIEN : le coffre n'est lu qu'une fois, au
             * chargement. Il fallait redémarrer plasmashell pour que le widget
             * retrouve son jeton. Un conseil qu'on suit et qui n'a pas d'effet
             * est pire qu'un silence. */
            if (!root.token) {
                root.lastError = "";
                root.walletProbe();
            }
            return;
        }
        if (!root.token) {
            return;
        }
        root.dissocieJeton(
            i18n("The address changed, so this widget is no longer paired. Pair it again."));
    }

    /* Le serveur a répondu 2xx à un appel qui portait le jeton : c'est la SEULE
     * preuve que le couple (jeton, adresse) tient. Appelée depuis les trois
     * lectures authentifiées — la sonde, le delta et la note d'un carré. */
    function marqueJetonEprouve() {
        if (root.jetonEprouve) {
            return;
        }
        root.jetonEprouve = true;
        root.rangeServeurAuCoffre();
    }

    /* 🔴 Le rattrapage des postes appariés AVANT le correctif du 2026-11-02.
     *
     * Leur jeton est déjà au coffre, donc ils ne repassent jamais par
     * l'appairage : sans ceci, leur adresse n'entrerait jamais au coffre et
     * chaque carré posé demain resterait muet — le correctif ne vaudrait que
     * pour les appairages à venir.
     *
     * ⚠️ APRÈS une réponse réussie du serveur avec ce jeton, jamais avant :
     * c'est la preuve que le couple (jeton, adresse) fonctionne. Graver une
     * adresse non vérifiée associerait le jeton à un endroit où il ne vaut
     * rien, et le prochain carré hériterait de l'erreur.
     *
     * Silencieux : il n'y a rien à annoncer: rien ne change pour la personne,
     * et un échec se rejouera à la prochaine passe réussie. */
    function rangeServeurAuCoffre() {
        /* 🔴 `jetonEprouve` d'abord : sans lui, l'adresse était gravée sur la
         * foi d'une route publique. Voir le commentaire du drapeau. */
        if (!root.jetonEprouve || !root.walletReady || !root.paired
                || root.coffreServeur || root.coffreServeurInconnu) {
            return;
        }
        var cible = root.server;
        root.walletRun("set-server", [cible], function (reponse) {
            if (reponse && reponse.ok) {
                root.coffreServeur = cible;
            }
        });
    }

    /* ── « Un carré, une note » ────────────────────────────────────────── */

    /* Le chemin court. Le cycle complet — sonde, delta paginé, passe de ménage,
     * marque d'eau — existe pour tenir un bureau entier à jour. Un carré n'a
     * qu'une note : tout cela serait payé cinq fois pour cinq carrés. */
    function probeOne() {
        root.enVol = true;
        root.passe += 1;
        var maPasse = root.passe;
        root.suivi(Api.one(root.server, root.token, root.pinnedNoteId,
            function (data) {
                if (maPasse !== root.passe) { return; }
                root.enVol = false;
                root.offline = false;
                root.lastError = "";
                root.marqueJetonEprouve();
                root.pinnedGone = false;
                var carte = {};
                carte[data.id] = data;
                root.notesById = carte;
                root.notesByIdChanged();
                root.flush();
            },
            function (err) {
                if (maPasse !== root.passe) { return; }
                root.enVol = false;
                err = err || { error: "offline" };
                /* ⚠️ Un 404 n'est PAS une panne : la note est supprimée, ou un
                 * collègue a cessé de la partager. Le dire, et vider la carte —
                 * sinon le carré montrerait une note qui n'existe plus, et la
                 * personne la croirait encore là. */
                if (err.error === "not_found" || err.status === 404) {
                    root.noteDisparue();
                    return;
                }
                root.onCallFailed(err);
            }));
    }

    /* La note de ce carré n'existe plus : ni supprimée par erreur, ni panne —
     * le serveur a répondu 404.
     *
     * ⚠️ Fonction NOMMÉE et non un bloc dans un rappel réseau : le rappel n'est
     * pas atteignable depuis le harnais, donc un essai ne pouvait que rejouer
     * la logique à la main — et une mutation y a survécu. Ce qui n'est pas
     * appelable n'est pas éprouvable. */
    function noteDisparue() {
        root.pinnedGone = true;
        root.notesById = ({});
        root.notesByIdChanged();
        /* 🔴 Son brouillon part avec elle. Sans cela, le bouton « fermer cette
         * note » du carré orphelin REFUSAIT — au nom d'un texte non enregistré
         * appartenant à une note qui n'existe plus — et le carré n'était plus
         * retirable depuis le widget. `prune` ne peut pas le faire : un carré
         * n'en fait jamais, et les brouillons sont par instance. */
        root.oublieLesBrouillonsMorts();
    }

    /* Cette instance n'affichera plus que cette note. Les autres instances ne
     * bougent pas : chacune a sa propre configuration. */
    function pinNote(id) {
        Plasmoid.configuration.noteId = id;
        Plasmoid.configuration.writeConfig();
        /* Ce qui est en cache vient du cycle complet : le jeter, la prochaine
         * passe ne rendra qu'une note. */
        root.notesById = ({});
        root.notesByIdChanged();
        root.since = "";
        root.lastStamp = null;
        root.pinnedGone = false;
        root.passe += 1;
        root.enVol = false;
        root.probe();
    }

    function unpinNote() {
        Plasmoid.configuration.noteId = 0;
        Plasmoid.configuration.writeConfig();
        root.notesById = ({});
        root.notesByIdChanged();
        root.since = "";
        root.lastStamp = null;
        root.pinnedGone = false;
        root.passe += 1;
        root.enVol = false;
        root.probe();
    }

    /* Le widget part du bureau : abandonner ce qui est en vol, sinon le rappel
     * tire sur un objet détruit. */
    Component.onDestruction: {
        root.abandonneTout();
    }

    /* The delta, then the prune pass.
     *
     * ⚠️ The prune runs after EVERY delta rather than on a timer of its own:
     * it is a list of integers, and the one thing it catches — a note that
     * left our scope — is exactly the case the delta cannot report. Cheap
     * enough to do every time, too important to do rarely.
     */
    /* La même page que Symbifox Mobile, et pas davantage.
     *
     * ⚠️ `_mobile_payload` fait UNE recherche `ir.attachment` par note : une
     * page de cent notes coûte donc cent requêtes au serveur, pour des pièces
     * que ce widget n'affiche même pas. Cinquante est ce que le téléphone
     * demande déjà ; la pagination fait le reste. Grouper ces lectures côté
     * serveur reste un dossier ouvert, qui toucherait le contrat du mobile. */
    readonly property int pageSize: 50

    /* Le delta, page par page. La marque d'eau n'avance qu'après la dernière :
     * voir `Store.applyDelta`. */
    function pull(maPasse, offset) {
        root.suivi(Api.list(root.server, root.token,
            { since: root.since, shared: root.showShared && root.hasSharedNotes,
              limit: root.pageSize, offset: offset },
            function (data) {
                if (maPasse !== root.passe) { return; }
                root.marqueJetonEprouve();
                var pleine = Store.pageComplete(data, root.pageSize);
                var fresh = root.notesById;
                var watermark = Store.applyDelta(fresh, data, !pleine);
                root.notesById = fresh;
                /* Réaffecter pour que QML voie le changement : muter la carte
                 * en place ne réveille aucune liaison. */
                root.notesByIdChanged();
                if (pleine) {
                    /* Il en reste : on continue AVANT de toucher la marque. */
                    root.pull(maPasse, offset + root.pageSize);
                    return;
                }
                if (watermark) {
                    root.since = watermark;
                }
                root.prune(maPasse);
            },
            /* ⚠️ La garde de passe ICI AUSSI, comme au succès. Sans elle, la
             * réponse tardive d'un tirage périmé remettait `enVol` à faux
             * pendant qu'un tirage vivant paginait — et le rangement, qui
             * refuse « pendant un tirage », se croyait autorisé sur un cache
             * incomplet. */
            function (err) {
                if (maPasse !== root.passe) { return; }
                root.enVol = false;
                root.onCallFailed(err);
            }));
    }

    /* Les brouillons dont la note n'est plus là.
     *
     * ⚠️ `enTrainDEcrire` ne regarde que « y a-t-il un brouillon », et il règle
     * la CADENCE d'un carré : une clé morte — une note archivée au téléphone,
     * supprimée, ou le -1 d'un carré neuf déjà adopté — laissait le widget en
     * cadence rapide pour toujours, soit vingt requêtes la minute que
     * `PINNED_MS` devait justement éviter. */
    function oublieLesBrouillonsMorts() {
        var b = root.brouillons;
        var change = false;
        for (var cle in b) {
            var id = parseInt(cle, 10);
            /* Le carré neuf garde le sien : sa note n'existe pas ENCORE. */
            if (id === root.idCarreNeuf && root.carreNeuf) {
                continue;
            }
            if (!root.notesById[id]) {
                delete b[cle];
                change = true;
            }
        }
        if (change) {
            root.brouillons = b;
            root.brouillonsChanged();
        }
    }

    function prune(maPasse) {
        if (!root.hasVisibleIds) {
            root.enVol = false;
            root.flush();
            return;
        }
        root.suivi(Api.visibleIds(root.server, root.token,
            root.showShared && root.hasSharedNotes,
            function (data) {
                if (maPasse !== root.passe) { return; }
                root.enVol = false;
                /* 🔴 Jamais de ménage sur une liste tronquée : elle ne dit pas
                 * « ces notes n'existent plus », elle dit « en voici une
                 * partie ». Purger là-dessus viderait le bureau. */
                if (!data.truncated) {
                    var fresh = root.notesById;
                    var retirees = Store.pruneTo(fresh, data.ids);
                    if (retirees > 0) {
                        root.notesById = fresh;
                        root.notesByIdChanged();
                        /* Les notes qui viennent de quitter la portée peuvent
                         * laisser un brouillon derrière elles, et un brouillon
                         * mort garde la cadence rapide pour toujours. C'est
                         * ICI qu'on le sait : nulle part ailleurs le widget
                         * n'apprend qu'une note a disparu. */
                        root.oublieLesBrouillonsMorts();
                    }
                }
                root.flush();
            },
            function (err) { root.enVol = false; root.onCallFailed(err); }));
    }

    /* 🔴 Sans ceci, `lastError` portait le code brut du transport : une
     * personne lisait « http_404 » et n'avait aucun moyen de savoir que son
     * locataire n'a simplement pas encore le module. Relevé le 2026-11-01 au
     * premier essai dans un vrai Plasma. */
    function humanError(err) {
        /* `reason` est accepté en plus de `error` : ceinture, alors que
         * `walletRun` fait déjà la traduction. Une fonction qui FABRIQUE le
         * message d'erreur ne doit pas, elle, échouer sur la forme de son
         * argument. */
        var code = (err && (err.error || err.reason)) || "";
        /* humanError est le passage obligé de TOUT code d'erreur : c'est donc
         * ici, et nulle part ailleurs, que se décide si l'adresse est en
         * cause. `isAddressFault` reste pure, pour que l'essai puisse la lire
         * sans monter une panne. */
        root.addressAtFault = root.isAddressFault(code);
        if (err && err.detail) {
            return err.detail;
        }
        switch (code) {
        case "offline":
        case "no_server":
            return i18n("Cannot reach %1. Is the address right, and the network up?",
                        root.server);
        case "absent":
            /* Pas d'erreur : il n'y a simplement pas encore de jeton. */
            return "";
        case "unknown_action":
        case "usage":
            /* Ne peut arriver que si le widget et son utilitaire ne sont pas
             * de la même version — une pose à moitié faite. */
            return i18n("This widget and its helper do not match. Install it again.");
        case "http_404":
            /* ⚠️ Nommait « the desktop pairing module » pour TOUT 404, y
             * compris celui du bloc-notes — donc un conseil faux sur une
             * adresse correcte. */
            return i18n("%1 answered, but it does not have the Symbifox module this widget needs yet.",
                        root.server);
        case "bad_payload":
            return i18n("%1 answered something that is not Symbifox. A captive portal, maybe?",
                        root.server);
        case "slow_down":
            return i18n("Too many pairing requests from here. Wait a moment, then try again.");
        case "unknown_app":
            return i18n("This Symbifox does not know this widget.");
        case "expired_token":
            return i18n("The pairing request expired. Start it again.");
        case "access_denied":
            return i18n("The pairing was refused. If you did not pick the right number, start again.");
        case "unauthorized":
            return i18n("This widget is no longer paired. Pair it again.");
        /* Le portefeuille. Ces cas disent tous « la faute n'est pas au
         * serveur », ce qui change entièrement ce qu'il y a à faire. */
        case "wallet_unavailable":
        case "crash":
            return i18n("The wallet helper did not answer. Your notes are safe; pairing is not possible right now.");
        case "wallet_timeout":
            return i18n("KWallet did not answer. Is your wallet unlocked?");
        case "no_wallet_tool":
            return i18n("This workstation has no KWallet. The token will be kept in this widget's settings instead.");
        case "wallet_refused":
        case "wallet_write_unverified":
        /* `stored_failed` est un ÉTAT, normalement accompagné de sa raison.
         * Le traiter quand même : un état qui arriverait seul ne doit pas
         * devenir « something went wrong ». */
        case "stored_failed":
            return i18n("The token could not be stored in KWallet, so it was not kept. Nothing is paired.");
        case "refused":
            return i18n("The pairing was refused. Start again from this widget if it was you.");
        case "no_state_dir":
            return i18n("No place to keep the pairing while it runs. Pairing cancelled.");
        case "no_pairing":
            return i18n("There is no pairing under way any more. Start again.");
        case "malformed":
            return i18n("The wallet returned something that is not a token.");
        }
        if (code.indexOf("http_") === 0) {
            return i18n("%1 answered %2.", root.server, code.substring(5));
        }
        return code || i18n("Something went wrong");
    }

    function onCallFailed(err) {
        /* ⚠️ Posé ICI et pas seulement dans `humanError` : les trois cas
         * ci-dessous sortent AVANT de l'appeler. Sans cette ligne, le drapeau
         * gardait la valeur d'un échec précédent, et « corrige l'adresse »
         * s'affichait sous « ce widget n'est plus apparié » — un conseil faux,
         * qui envoie retoucher une adresse correcte. Un drapeau ne se pose pas
         * à un seul des endroits qui le lisent. */
        root.addressAtFault = root.isAddressFault(err && (err.error || err.reason));
        if (err.error === "unauthorized") {
            /* The server says this token is finished. Forget it — and keep the
             * cached notes on screen until the person pairs again, rather than
             * blanking the widget on a revocation they may not have made. */
            root.forgetToken();
            root.lastError = i18n("This widget is no longer paired. Pair it again.");
            return;
        }
        if (err.error === "offline" || err.error === "no_server") {
            root.offline = true;
            return;
        }
        root.lastError = root.humanError(err);
    }

    /* ── Writing ───────────────────────────────────────────────────────── */

    /* A new note shows up immediately with a negative, local id, and the real
     * one replaces it when the server answers. Waiting for the round trip
     * would make typing feel like filling a form. */
    Component.onCompleted: {
        /* La version annoncée au serveur sur chaque appel authentifié, pour que
         * « Mes appareils » ne montre plus la version qui a apparié le poste.
         * Posée une fois, avant tout appel. */
        Api.setAppVersion(root.widgetVersion);
        /* La file survit à la session : voir `Store.parseOutbox`. */
        root.outbox = Store.parseOutbox(Plasmoid.configuration.outbox);
        /* ⚠️ Le jeton n'est pas encore connu : il vient du portefeuille, donc
         * d'un processus, donc plus tard. Vider la file demande un jeton, et
         * `flush` part de `root.paired` — c'est `afterToken` qui l'appelle une
         * fois la réponse arrivée. Vider ici enverrait des requêtes sans
         * en-tête, qui reviendraient en 401 et dépaireraient le widget. */
        root.walletProbe();
    }

    function rangeOutbox() {
        Plasmoid.configuration.outbox = Store.serialiseOutbox(root.outbox);
        Plasmoid.configuration.writeConfig();
        root.outboxChanged();
    }

    function addNote(text) {
        if (!text || !text.trim()) {
            return;
        }
        var uuid = root.makeUuid();
        root.outbox = Store.queueCreate(root.outbox, text.trim(), uuid,
                                        root.couleurNeuve);
        root.rangeOutbox();
        root.flush();
    }

    function flush() {
        if (!root.paired || root.offline) {
            return;
        }
        var entry = Store.nextToFlush(root.outbox);
        if (!entry) {
            return;
        }
        root.suivi(Api.create(root.server, root.token, entry.text, entry.uuid,
            function (data) {
                Store.forget(root.outbox, entry.uuid);
                root.rangeOutbox();
                if (data.note) {
                    var fresh = root.notesById;
                    fresh[data.note.id] = data.note;
                    root.notesById = fresh;
                    root.notesByIdChanged();
                    /* 🔴 Le carré neuf adopte SA note, et seulement la sienne :
                     * la comparaison porte sur l'identifiant d'envoi, pas sur
                     * « la dernière créée ». Une note tapée ailleurs pendant
                     * que celle-ci était hors ligne arriverait sinon dans ce
                     * carré-ci. */
                    if (root.carreNeuf
                            && Plasmoid.configuration.adopteUuid === entry.uuid) {
                        root.adopteLaNote(data.note);
                    }
                }
                /* On continue : plusieurs notes ont pu être tapées hors ligne. */
                root.flush();
            },
            function (err) {
                /* Un échec passager ne consomme PAS un essai : cinq erreurs
                 * 500 pendant un redémarrage d'Odoo bloquaient une note pour
                 * de bon. */
                if (Store.countsAsRefusal(err)) {
                    entry.tries += 1;
                }
                root.rangeOutbox();
                root.onCallFailed(err);
            },
            entry.couleur));
    }

    /* La note que porte un identifiant, ou rien. Le panneau de renvoi en a
     * besoin : il vit dans la LISTE (il lui faut un champ de recherche, et un
     * carré de 288 pixels n'a pas la place), donc il ne reçoit pas la note par
     * un délégué. */
    function noteParId(id) {
        return root.notesById[id] || null;
    }

    function noteBrouillon(id) {
        return root.brouillons[id];
    }

    function poseBrouillon(id, texte) {
        var b = root.brouillons;
        if (texte === undefined || texte === null) {
            delete b[id];
        } else {
            b[id] = texte;
        }
        root.brouillons = b;
        /* 🔴 Réaffecter la MÊME référence ne réveille aucune liaison en QML :
         * le moteur compare les références, et elles sont identiques. Le
         * défaut était resté latent parce que rien ne se liait à `brouillons`
         * — tout y accédait par la fonction `noteBrouillon`. La première
         * liaison écrite (`enTrainDEcrire`, qui règle la cadence d'un carré)
         * l'a révélé : elle ne bougeait jamais. Même remède que `notesById`
         * juste au-dessus. */
        root.brouillonsChanged();
    }

    function saveNote(note, text, title) {
        var vals = {};
        if (text !== undefined) { vals.text = text; }
        if (title !== undefined) { vals.title = title; }
        /* 🔴 `suivi`, comme toute LECTURE. Les écritures ne l'avaient pas, et
         * `api.js` dit lui-même qu'il n'existe pas de délai en QML : c'est à
         * l'appelant d'armer la minuterie. Une connexion à moitié ouverte
         * pendant un enregistrement — portable qui quitte le wifi, VPN tombé,
         * Odoo qui ne répond plus sans fermer la socket — ne revenait jamais :
         * aucun rappel, `lastError` vide, témoin éteint, et rien d'enregistré.
         * Relecture adverse du 11-08, qui a aussi relevé les TypeError de
         * rappels tirant sur un `root` déjà détruit, faute d'abandon. */
        root.suivi(Api.update(root.server, root.token, note.id, vals, note.write_date,
            function (data) {
                var fresh = root.notesById;
                fresh[data.note.id] = data.note;
                root.notesById = fresh;
                root.notesByIdChanged();
                root.poseBrouillon(data.note.id, null);
            },
            function (conflict) {
                /* Somebody changed this note elsewhere while it was open.
                 * v1 refuses rather than merges (#27250): show the server's
                 * version and say so. */
                if (conflict.note) {
                    var fresh = root.notesById;
                    fresh[conflict.note.id] = conflict.note;
                    root.notesById = fresh;
                    root.notesByIdChanged();
                }
                root.lastError = i18n("This note changed elsewhere. Your copy was not saved.");
            },
            root.echecEcriture));
    }

    /* 🔴 L'échec d'une ÉCRITURE n'est pas celui d'une lecture.
     *
     * `onCallFailed` sort en silence sur `offline`/`no_server` — c'est juste
     * pour une sonde (garder le cache à l'écran et ralentir), et faux pour un
     * enregistrement : la personne tapait, cliquait « Save », la carte se
     * refermait, AUCUN message, et son texte ne vivait plus que dans
     * `brouillons` — en mémoire seule, contrairement à la file d'envoi qui est
     * rangée dans la configuration. Un redémarrage de plasmashell, un
     * déplacement du widget, une fin de session : le texte était perdu sans un
     * mot. La v1 assume de ne pas mettre les MODIFICATIONS en file ; elle
     * n'assume nulle part de ne pas le dire. */
    /* Le refus AVANT l'envoi, quand on sait déjà que ça ne partira pas. */
    function refuseEcritureHorsLigne() {
        root.lastError = i18n("Not saved: the server is unreachable. Keep this note open until it is back.");
    }

    function echecEcriture(err) {
        err = err || { error: "offline" };
        if (err.error === "offline" || err.error === "no_server") {
            root.offline = true;
            root.addressAtFault = root.isAddressFault(err.error);
            root.lastError = i18n("Not saved: the server is unreachable. Keep this note open until it is back.");
            return;
        }
        root.onCallFailed(err);
    }

    /* ── « En faire une tâche » ──────────────────────────────────────────── */

    /* Les projets où poser la tâche, et la note pour laquelle on demande.
     *
     * ⚠️ La liste vit ICI et non dans la carte : le modèle de la liste est un
     * tableau JavaScript, donc tout délégué est détruit et recréé au moindre
     * changement venu d'ailleurs — une liste rangée dans la carte
     * disparaîtrait sous les doigts, comme le texte en cours de frappe avant
     * elle. */
    property var projetsOfferts: []
    property int projetsPourNote: 0
    property bool projetsEnVol: false

    function demandeProjets(noteId) {
        if (root.projetsEnVol) {
            return;
        }
        root.projetsEnVol = true;
        root.projetsPourNote = noteId;
        root.projetsOfferts = [];
        root.suivi(Api.projets(root.server, root.token, "",
            function (data) {
                root.projetsEnVol = false;
                var liste = (data && data.projets) || [];
                root.projetsOfferts = liste;
                if (!liste.length) {
                    /* Le serveur rend les projets où la personne a eu une tâche
                     * récemment : une liste vide veut dire « aucun », pas « ça
                     * n'a pas marché ». Le dire, sinon un menu vide passe pour
                     * une panne. */
                    root.lastError = i18n("No project to put a task in. Open the note in Symbifox to choose one.");
                }
            },
            function (err) {
                root.projetsEnVol = false;
                root.echecEcriture(err);
            }));
    }

    function enFaireUneTache(note, projectId) {
        /* ⚠️ `archive` n'est PAS demandé : le serveur sait archiver la note en
         * même temps, et le faire sans qu'on l'ait demandé ferait disparaître
         * du bureau une note dont on voulait seulement une tâche. */
        root.runAction(note, "task", { project_id: projectId });
        root.projetsOfferts = [];
        root.projetsPourNote = 0;
    }

    /* ── « Relier à une fiche » ──────────────────────────────────────────── */

    /* Les cibles offertes, APLATIES depuis les groupes du serveur.
     *
     * Le serveur groupe par modèle (« Tâches », « Rencontres », …) et le
     * premier groupe est la référence exacte tirée de la saisie quand il y en a
     * une. Un widget étroit ne peut pas rendre des groupes repliables : on
     * aplatit en gardant l'étiquette du modèle sur chaque ligne, ce qui préserve
     * l'ORDRE — donc la référence exacte reste en tête. */
    property var ciblesOffertes: []
    property int ciblesPourNote: 0
    property bool ciblesEnVol: false
    /* 🔴 Le texte tapé vit ICI, pas dans la carte. Le modèle de la liste est un
     * tableau JavaScript : tout délégué est détruit et recréé au moindre
     * changement venu d'ailleurs — le texte d'une note en cours de frappe avait
     * déjà disparu comme ça, en moins de trois secondes. Une recherche tapée
     * dans la carte subirait exactement le même sort. */
    property string rechercheCible: ""

    function aplatitCibles(groupes) {
        var out = [];
        for (var i = 0; i < (groupes || []).length; i++) {
            var g = groupes[i];
            var res = (g && g.results) || [];
            for (var j = 0; j < res.length; j++) {
                out.push({
                    model: g.model,
                    model_label: g.model_label || g.model,
                    id: res[j].id,
                    name: res[j].name || "",
                    detail: res[j].detail || "",
                    closed: !!res[j].closed
                });
            }
        }
        return out;
    }

    function chercheCibles(noteId, texte) {
        root.ciblesPourNote = noteId;
        /* ⚠️ `Api.cibles` rend une réponse vide SANS appeler sous deux
         * caractères : le serveur refuse une requête plus courte, et un
         * aller-retour par frappe pour une liste connue d'avance est un
         * aller-retour de trop. */
        root.ciblesEnVol = true;
        root.suivi(Api.cibles(root.server, root.token, texte,
            function (data) {
                root.ciblesEnVol = false;
                if (root.ciblesPourNote !== noteId) {
                    return;     // la réponse d'une recherche qu'on ne veut plus
                }
                root.ciblesOffertes = root.aplatitCibles(data && data.groupes);
            },
            function (err) {
                root.ciblesEnVol = false;
                root.ciblesOffertes = [];
                root.echecEcriture(err);
            }));
    }

    function renvoyer(note, model, id) {
        /* ⚠️ La note peut avoir quitté la portée pendant que le panneau était
         * ouvert (archivée au téléphone, supprimée, plus partagée). Sans cette
         * garde, cliquer un résultat levait « Cannot read property 'id' of
         * null » : aucune requête, aucun message, un bouton mort. */
        if (!note) {
            root.lastError = i18n("That note is no longer here. It was archived, deleted, or unshared.");
            root.ciblesPourNote = 0;
            root.ciblesOffertes = [];
            return;
        }
        /* `mode: "replace"` : le serveur accepte aussi d'ajouter, mais un
         * pense-bête n'a qu'un lien à l'écran — en garder deux sans pouvoir les
         * montrer serait mentir sur ce qu'on a fait. */
        root.runAction(note, "reroute", { model: model, id: id, mode: "replace" });
        root.ciblesOffertes = [];
        root.ciblesPourNote = 0;
    }

    function runAction(note, name, params, onDone) {
        /* ⚠️ `onDone` n'est appelé qu'APRÈS la réponse du serveur. Le menu
         * « supprimer » fermait le carré dans le même geste que l'archivage :
         * l'action est asynchrone, donc le carré disparaissait avant, et hors
         * ligne le message d'échec se peignait sur une surface déjà détruite —
         * carré fermé, note intacte, personne de prévenu. */
        root.suivi(Api.action(root.server, root.token, note.id, name, params || {},
            function () {
                root.lastStamp = null;
                root.probe();
                if (onDone) { onDone(); }
            },
            root.echecEcriture));
    }

    /* ── Pairing ───────────────────────────────────────────────────────── */

    /* 🔴 Une adresse tapée par une personne finit dans la ligne de commande de
     * l'utilitaire de portefeuille, donc dans un shell. Sans ce filtre,
     * « x.example; rm -rf ~ » serait exécuté. Deux gardes, pas une : ce motif,
     * et l'échappement de `shellQuote`.
     *
     * Le motif n'accepte qu'un schéma http(s), un hôte, un port, un chemin
     * sobre. Pas d'identifiant avant l'hôte (`user:mdp@`), pas de requête, pas
     * de fragment : rien de tout cela n'a de sens pour la racine d'un Odoo, et
     * chacun est un moyen de faire dire autre chose à une URL. */
    readonly property var serverPattern:
        /^https?:\/\/[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(:[0-9]{1,5})?(\/[A-Za-z0-9._~/-]*)?$/

    /* The address, as a person types it. Nobody types a scheme, and
     * « mon-locataire.com » must not become a relative path. A scheme that IS
     * typed is kept: the bench is served over http on a loopback port.
     * Rend "" si l'adresse est vide OU irrecevable — `serverRejected` sépare
     * les deux cas pour que le message soit juste. */
    function normalizeServer(texte) {
        var v = (texte || "").trim().replace(/\/+$/, "");
        if (!v) {
            return "";
        }
        if (v.indexOf("://") === -1) {
            v = "https://" + v;
        }
        return root.serverPattern.test(v) ? v : "";
    }

    function serverRejected(texte) {
        return (texte || "").trim().length > 0 && root.normalizeServer(texte) === "";
    }

    /* Les seuls codes où changer l'adresse est le geste utile. `unknown_app`
     * en fait partie : l'adresse répond, c'est un Symbifox, mais il ne connaît
     * pas ce widget — donc ce n'est pas le bon. Tout le reste (portefeuille,
     * jeton périmé, refus à la page de consentement) se règle ailleurs, et
     * inviter à retoucher l'adresse enverrait sur une fausse piste. */
    function isAddressFault(code) {
        switch (code) {
        case "offline":
        case "no_server":
        case "http_404":
        case "bad_payload":
        case "unknown_app":
            return true;
        default:
            return false;
        }
    }

    /* Un argument pour /bin/sh. Les guillemets simples ferment tout sauf
     * l'apostrophe, qui se recolle par '\''. Appliqué à TOUT argument, même
     * ceux qui viennent du système : une garde qu'on applique par exception
     * n'est pas une garde. */
    function shellQuote(valeur) {
        return "'" + String(valeur === undefined ? "" : valeur).replace(/'/g, "'\\''") + "'";
    }

    function setServer(texte) {
        var v = root.normalizeServer(texte);
        if (v !== Plasmoid.configuration.server) {
            Plasmoid.configuration.server = v;
        }
        return v;
    }

    function startPairing(adresse) {
        /* The pairing screen hands over what is in its field, so that the
         * address is asked for rather than assumed. */
        if (adresse !== undefined && root.serverRejected(adresse)) {
            root.lastError = i18n("That does not look like an address. Expected something like symbifox.mon-organisation.com.");
            root.addressAtFault = true;
            return;
        }
        var cible = adresse === undefined ? root.server : root.setServer(adresse);
        if (!cible) {
            root.lastError = i18n("Type the address of your Symbifox first.");
            root.addressAtFault = true;
            return;
        }
        /* 🔴 La sonde répond-elle encore ? Si oui, on ATTEND : sans cela, la
         * bascule vers le canonique ne se faisait pas et l'adresse tapée
         * partait au coffre à sa place. Voir `appairageEnAttente`. */
        if (root.probingServer) {
            root.appairageEnAttente = cible;
            return;
        }
        /* 🔴 On s'apparie sur l'adresse que l'instance ANNONCE, pas sur celle
         * qu'on a tapée. C'est le canonique (`web.base.url`) que la page de
         * consentement ouvrira dans le navigateur, et le jeton n'est utile que
         * là. Un client dont le domaine convivial diffère de son canonique
         * peut donc taper le convivial — ce que demandait #27250 — sans que
         * l'appairage casse au milieu.
         *
         * ⚠️ Et on le DIT : une bascule muette fait croire qu'on s'est
         * trompé de serveur. La leçon du jour s'applique ici aussi. */
        if (root.probedServer === cible && root.serverIsAlias) {
            root.aliasNotice = i18n("Pairing with %1, the address this Symbifox announces.",
                                    root.canonicalUrl);
            cible = root.setServer(root.canonicalUrl);
            if (!cible) {
                root.lastError = i18n("%1 announces an address this widget cannot use.",
                                      root.probedServer);
                root.addressAtFault = true;
                root.aliasNotice = "";
                return;
            }
        } else {
            root.aliasNotice = "";
        }
        root.pairing = true;
        root.lastError = "";
        root.addressAtFault = false;

        /* Ce qui suit l'ouverture d'un appairage est le même dans les deux
         * chemins : afficher le nombre, lancer le sondage, ouvrir la page. */
        function demarre(data) {
            root.pairingUrl = data.verification_uri_complete || "";
            root.matchNumber = data.match_number || "";
            pairPoll.interval = (data.interval || 2) * 1000;
            pairTimeout.interval = (data.expires_in || 300) * 1000;
            pairPoll.start();
            pairTimeout.restart();
            /* ⚠️ May quietly do nothing: a session with no default browser,
             * a locked-down profile. « Open the page again » is the way
             * out, and the address is shown so it can be read. */
            Qt.openUrlExternally(root.pairingUrl);
        }

        if (root.walletReady) {
            /* 🔴 Chemin du portefeuille : c'est l'utilitaire qui ouvre
             * l'appairage, garde le code et le secret dans un tmpfs à 0600, et
             * rangera le jeton lui-même. Ni le code, ni le secret, ni le jeton
             * ne passent par QML — donc rien à voler dans /proc. */
            root.walletRun("pair-start", [cible, root.deviceName(), root.widgetVersion],
                function (reponse) {
                    if (!reponse) {
                        root.pairing = false;
                        root.lastError = root.humanError({ error: "wallet_unavailable" });
                        return;
                    }
                    if (!reponse.ok) {
                        root.pairing = false;
                        root.lastError = root.humanError(reponse);
                        return;
                    }
                    demarre(reponse);
                });
            return;
        }

        /* Repli sans portefeuille : le jeton finira dans la configuration de
         * cette instance, et l'écran le dit. */
        /* `cible`, not `root.server`: the binding on Plasmoid.configuration
         * has not necessarily settled within this same function call. */
        Api.startPairing(cible, root.deviceName(), root.widgetVersion,
            function (data) {
                root.pairingCode = data.device_code;
                root.pairingSecret = data.device_secret;
                demarre(data);
            },
            function (err) {
                root.pairing = false;
                root.lastError = root.humanError(err);
            });
    }

    function cancelPairing() {
        pairPoll.stop();
        pairTimeout.stop();
        if (root.walletReady) {
            /* L'état vit dans un tmpfs, pas en mémoire : l'oublier aussi. */
            root.walletRun("pair-cancel", [], function () {});
        }
        root.pairing = false;
        root.pairingCode = "";
        root.pairingSecret = "";
        root.pairingUrl = "";
        root.matchNumber = "";
    }

    Timer {
        id: pairPoll
        repeat: true
        onTriggered: root.pollPairingOnce()
    }

    /* Un jeton vient d'arriver : plus rien n'est su des notes de ce compte. */
    function onPaired() {
        root.cancelPairing();
        root.notesById = ({});
        root.since = "";
        root.lastStamp = null;
        root.probe();
    }

    function pollPairingOnce() {
        if (root.walletReady) {
            /* 🔴 L'utilitaire range le jeton lui-même et ne le rend JAMAIS sur
             * sa sortie : il le relit du portefeuille juste après, pour que le
             * widget ne se croie pas apparié sur une écriture qui n'a pas eu
             * lieu. */
            root.walletRun("pair-poll", [], function (reponse) {
                if (!reponse) {
                    return;   /* un appel raté ne doit pas tuer l'appairage */
                }
                if (reponse.state === "pending") {
                    return;
                }
                if (reponse.state === "slow_down") {
                    pairPoll.interval = (reponse.interval || 5) * 1000;
                    return;
                }
                if (reponse.state === "stored") {
                    /* ⚠️ Le jeton est rangé, l'adresse peut-être pas — et une
                     * adresse non rangée n'annule PAS l'appairage : cette
                     * instance-ci porte la sienne dans sa configuration et
                     * fonctionnera. Ce qui se perd, c'est l'héritage : les
                     * carrés qu'on posera demain ne sauront pas où appeler et
                     * redemanderont un appairage. Donc on le dit, au lieu de
                     * défaire un appairage qui a abouti. */
                    if (reponse.server_stored === false) {
                        root.lastError = i18n("Paired, but the address could not be saved for other widgets: each new one will ask again. (%1)",
                                              String(reponse.server_reason || ""));
                    }
                    root.walletRun("read", [], function (lu) {
                        if (lu && lu.ok && lu.token) {
                            root.token = lu.token;
                            root.onPaired();
                        } else {
                            root.cancelPairing();
                            root.lastError = root.humanError({ error: "wallet_write_unverified" });
                        }
                    });
                    return;
                }
                root.cancelPairing();
                root.lastError = root.humanError(reponse);
            });
            return;
        }

        Api.pollPairing(root.server, root.pairingCode, root.pairingSecret,
            function (data) {
                root.keepToken(data.token);
                root.onPaired();
            },
            function () { /* still waiting, and that is fine */ },
            function (err) {
                root.cancelPairing();
                root.lastError = root.humanError(err);
            });
    }

    Timer {
        id: pairTimeout
        onTriggered: {
            root.cancelPairing();
            root.lastError = i18n("Pairing timed out. Try again.");
        }
    }

    /* ── Token ─────────────────────────────────────────────────────────── */

    /* Changer la portée remet la synchronisation à zéro : le delta ne rend
     * que ce qui a changé depuis la marque, donc cocher « aussi les partagées »
     * n'aurait rien ramené avant qu'un collègue ne touche une note. */
    onShowSharedChanged: {
        root.notesById = ({});
        root.since = "";
        root.lastStamp = null;
        root.passe += 1;
        root.enVol = false;
        root.probe();
    }

    function keepToken(token) {
        root.token = token;
        if (root.walletReady) {
            /* Déjà rangé par l'utilitaire, qui l'a reçu du serveur et l'a
             * écrit dans le portefeuille sans qu'il traverse QML ni une ligne
             * de commande. Rien à écrire ici — et surtout pas dans la
             * configuration, qu'on vient de vider. */
            return;
        }
        Plasmoid.configuration.token = token;
        Plasmoid.configuration.writeConfig();
    }

    function forgetToken() {
        root.token = "";
        if (root.walletReady) {
            /* ⚠️ Le rappel était VIDE. `forget` peut rendre `ok=False` —
             * portefeuille verrouillé, dossier refusé — en laissant le jeton
             * au coffre, alors que le code annonçait « oublié et purgé ». La
             * garde se rejoue au démarrage suivant, donc la conséquence est
             * bornée ; le silence, non. */
            root.walletRun("forget", [], function (reponse) {
                if (reponse && reponse.ok === false) {
                    root.lastError = i18n("This widget is no longer paired, but the token could not be removed from the wallet. Revoke it in « Mes appareils ».");
                }
            });
        }
        /* Vidé dans les deux cas : un jeton oublié ne doit pas survivre dans
         * la configuration parce que le portefeuille a pris le relais. */
        if (Plasmoid.configuration.token) {
            Plasmoid.configuration.token = "";
            Plasmoid.configuration.writeConfig();
        }
    }

    /* ── Le portefeuille ───────────────────────────────────────────────── */

    /* KWallet n'a pas d'API QML en Plasma 6 : il faut un processus. Tout le
     * pourquoi, et la raison pour laquelle aucun secret ne traverse une ligne
     * de commande, est en tête de contents/code/bfsn_wallet.py. */
    property bool walletReady: false
    property bool walletChecked: false
    property string walletReason: ""
    /* Vrai le temps d'un message, après que le passage au portefeuille a fait
     * perdre un jeton qui vivait dans la configuration. */
    property bool walletMigrated: false

    readonly property string walletScript:
        Qt.resolvedUrl("../code/bfsn_wallet.py").toString().replace(/^file:\/\//, "")

    property int shellSeq: 0

    Plasma5Support.DataSource {
        id: shell
        engine: "executable"
        connectedSources: []

        /* Les rappels en attente, par commande. */
        property var attentes: ({})

        onNewData: function (source, data) {
            /* Déconnecter d'abord : le moteur garde la source vivante sinon,
             * et une commande identique ne se relancerait jamais. */
            disconnectSource(source);
            var rappel = shell.attentes[source];
            delete shell.attentes[source];
            if (!rappel) {
                return;
            }
            var brut = String(data["stdout"] || "").trim();
            var reponse = null;
            if (brut.length > 0) {
                /* L'utilitaire ne rend qu'une ligne, mais prendre la dernière
                 * protège d'un avertissement qu'un outil tiers aurait écrit
                 * devant. */
                try {
                    reponse = JSON.parse(brut.split("\n").pop());
                } catch (e) {
                    reponse = null;
                }
            }
            /* 🔴 L'utilitaire parle en `reason`, le widget en `error`. Sans
             * cette traduction À LA FRONTIÈRE, chaque refus venu du
             * portefeuille arrivait dans `humanError` sans clé reconnue et
             * s'affichait « Something went wrong » : visible, et vide de sens.
             * Même classe de défaut que `matchNumber` — un contrat entre deux
             * composants, cassé d'un côté, et chaque côté éprouvé seul.
             * Un seul vocabulaire circule désormais dans le widget. */
            if (reponse && reponse.reason !== undefined && reponse.error === undefined) {
                reponse.error = reponse.reason;
            }
            rappel(reponse, data["exit code"]);
        }
    }

    function walletRun(action, args, onDone) {
        var cmd = "python3 " + root.shellQuote(root.walletScript)
                + " " + root.shellQuote(action);
        for (var i = 0; i < (args || []).length; i++) {
            cmd += " " + root.shellQuote(args[i]);
        }
        /* 🔴 Le moteur « executable » indexe ses sources par la COMMANDE :
         * reconnecter une commande identique ne relance RIEN. Le sondage
         * d'appairage, qui rejoue la même commande toutes les deux secondes,
         * tombe exactement là. Un commentaire shell rend chaque appel unique
         * sans changer ce qui est exécuté. */
        root.shellSeq += 1;
        cmd += " # " + root.shellSeq;
        shell.attentes[cmd] = onDone;
        shell.connectSource(cmd);
    }

    /* ── Les carrés sur le bureau ───────────────────────────────────────── */

    /* Piloter le bureau, c'est demander à plasmashell d'exécuter un script.
     *
     * ⚠️ Le script part en LIGNE DE COMMANDE, donc tout ce qu'il porte est
     * lisible dans /proc par n'importe quel compte du poste. C'est pourquoi ni
     * le jeton ni la file d'envoi ne traversent jamais ce chemin : voir
     * `CLES_JAMAIS_EN_LIGNE_DE_COMMANDE` dans grille.js.
     *
     * 🔴 Et plasmashell ne CALCULE rien : il lit l'état, ou il applique un plan.
     * Tout le placement vit dans grille.js, qui s'éprouve sous node. Dupliquer
     * la logique dans un script passé par DBus, c'est la mettre hors d'atteinte
     * de tout essai. */
    function bureauRun(script, onDone) {
        var cmd = "qdbus6 org.kde.plasmashell /PlasmaShell"
                + " org.kde.PlasmaShell.evaluateScript "
                + root.shellQuote(script);
        root.shellSeq += 1;
        cmd += " # " + root.shellSeq;
        shell.attentes[cmd] = onDone;
        shell.connectSource(cmd);
    }

    /* Ce que le bureau porte déjà : un carré par entrée, avec sa note, sa
     * position telle que Plasma la rend (donc décalée — grille.js compare à une
     * tolérance pour cette raison) et SA TAILLE.
     *
     * 🔴 La taille se relève parce que chaque carré est redimensionnable à la
     * main, et que ranger veut dire retirer puis reposer : sans elle, le
     * rangement rendait à tous les carrés la taille par défaut, et le geste de
     * la personne était effacé par un geste de ménage. */
    function bureauLit(onDone) {
        root.bureauRun(
            'var out = [];'
          + 'var t = desktops();'
          + 'for (var i = 0; i < t.length; i++) {'
          + '  var ids = t[i].widgetIds;'
          + '  for (var j = 0; j < ids.length; j++) {'
          + '    var w = t[i].widgetById(ids[j]);'
          + '    if (!w || String(w.type).indexOf("com.symbifox.stickynotes") < 0) { continue; }'
          + '    w.currentConfigGroup = ["General"];'
          + '    out.push({ id: w.id, bureau: i,'
          + '               noteId: parseInt(w.readConfig("noteId") || 0, 10) || 0,'
          + '               x: w.geometry.x, y: w.geometry.y,'
          + '               largeur: w.geometry.width, hauteur: w.geometry.height });'
          + '  }'
          + '}'
          + 'print(JSON.stringify({ ok: true, carres: out }));',
            onDone);
    }

    /* Poser UN carré sur une note, à une place calculée ici.
     *
     * ⚠️ `addWidget(plugin, x, y, w, h)` est le SEUL moyen de placer : la
     * géométrie d'un carré déjà posé ne s'écrit pas (mesuré le 2026-11-03). */
    function bureauPose(noteId, place, onDone) {
        root.bureauRun(
            /* 🔴 `desktops()` peut être VIDE, et `desktops()[0].addWidget`
             * levait alors une TypeError que le widget rapportait en « le
             * bureau a refusé » — une cause fausse. Trouvé le 2026-11-03 en
             * rejouant ce script même dans un plasmashell qui venait de
             * démarrer : ses conteneurs de bureau n'existaient pas encore.
             *
             * ⚠️ Et on pose sur le PREMIER conteneur : avec plusieurs écrans ou
             * activités, ce n'est pas forcément celui qu'on regarde. L'API ne
             * dit pas lequel est courant ; c'est une limite assumée, pas un
             * choix. */
            'var t = desktops();'
          + 'if (!t.length) {'
          + '  print(JSON.stringify({ ok: false, reason: "aucun_bureau" }));'
          + '} else {'
          + 'var d = t[0];'
          + 'var w = d.addWidget("com.symbifox.stickynotes", '
          + place.x + ', ' + place.y + ', '
          + place.largeur + ', ' + place.hauteur + ');'
          + 'if (!w) { print(JSON.stringify({ ok: false, reason: "refuse" })); }'
          + 'else {'
          + '  w.currentConfigGroup = ["General"];'
          + '  w.writeConfig("noteId", ' + parseInt(noteId, 10) + ');'
          + '  w.writeConfig("server", ' + JSON.stringify(root.server) + ');'
          + '  w.writeConfig("showShared", ' + (root.showShared ? "true" : "false") + ');'
          + '  w.reloadConfig();'
          + '  print(JSON.stringify({ ok: true, id: w.id }));'
          + '}}',
            onDone);
    }

    /* Ranger : retirer et reposer, puisque déplacer est impossible.
     *
     * Le plan vient de `Grille.rangement`, donc d'un code éprouvé. Ce script
     * n'applique que ce qu'on lui donne, et recopie la LISTE de clés que
     * grille.js nomme — jamais le jeton, jamais la file. */
    function bureauRange(plan, aRetirer, onDone) {
        var gestes = [];
        for (var i = 0; i < plan.length; i++) {
            var place = plan[i].place;
            var carre = plan[i].carre;
            /* La taille du carré l'emporte sur celle de la grille : la grille
             * décide des PLACES, la personne décide des tailles. */
            gestes.push({ id: carre.id, place: {
                x: place.x, y: place.y,
                largeur: carre.largeur > 0 ? carre.largeur : place.largeur,
                hauteur: carre.hauteur > 0 ? carre.hauteur : place.hauteur
            } });
        }
        var ids = [];
        for (var j = 0; j < aRetirer.length; j++) {
            ids.push(aRetirer[j].id);
        }
        root.bureauRun(
            'var PLAN = ' + JSON.stringify(gestes) + ';'
          + 'var RETIRER = ' + JSON.stringify(ids) + ';'
          + 'var CLES = ' + JSON.stringify(Grille.CLES_A_EMPORTER) + ';'
          + 'var t = desktops();'
          + 'function trouve(id) {'
          + '  for (var i = 0; i < t.length; i++) {'
          + '    var ids = t[i].widgetIds;'
          + '    for (var j = 0; j < ids.length; j++) {'
          + '      if (ids[j] === id) { return { c: t[i], w: t[i].widgetById(id) }; }'
          + '    }'
          + '  }'
          + '  return null;'
          + '}'
          + 'var ranges = 0, retires = 0, perdus = 0;'
          /* 🔴 Le conteneur de bureau peut ne pas exister encore — plasmashell
           * vient de démarrer. La pose le sait déjà et le dit ; le rangement,
           * lui, rendait `ok: true` avec zéro partout, donc le bouton ne
           * faisait rien et ne disait rien. */
          + 'if (!t.length) {'
          + '  print(JSON.stringify({ ok: false, reason: "aucun_bureau" }));'
          + '} else {'
          + 'for (var k = 0; k < RETIRER.length; k++) {'
          + '  var o = trouve(RETIRER[k]);'
          + '  if (o && o.w) { o.w.remove(); retires += 1; }'
          + '}'
          + 'for (var m = 0; m < PLAN.length; m++) {'
          + '  var g = PLAN[m];'
          + '  var f = trouve(g.id);'
          + '  if (!f || !f.w) { continue; }'
          + '  f.w.currentConfigGroup = ["General"];'
          + '  var garde = {};'
          + '  for (var n = 0; n < CLES.length; n++) { garde[CLES[n]] = f.w.readConfig(CLES[n]); }'
          + '  f.w.remove();'
          + '  var neuf = f.c.addWidget("com.symbifox.stickynotes",'
          + '    g.place.x, g.place.y, g.place.largeur, g.place.hauteur);'
          + '  if (!neuf) { perdus += 1; continue; }'
          + '  neuf.currentConfigGroup = ["General"];'
          + '  for (var p = 0; p < CLES.length; p++) { neuf.writeConfig(CLES[p], garde[CLES[p]]); }'
          + '  neuf.reloadConfig();'
          + '  ranges += 1;'
          + '}'
          + 'print(JSON.stringify({ ok: true, ranges: ranges, retires: retires,'
          + '                        perdus: perdus, prevus: PLAN.length }));'
          + '}',
            onDone);
    }

    /* La zone utile, pour la grille. `Screen` peut rendre 0 hors d'un vrai
     * écran (harnais, premier dessin) : un repli vaut mieux qu'une grille à une
     * seule place, qui ferait refuser toute pose sans que personne ne comprenne
     * pourquoi. */
    readonly property var zoneBureau: ({
        largeur: Screen.width > 0 ? Screen.width : 1920,
        hauteur: Screen.height > 0 ? Screen.height : 1080
    })

    /* Vrai quand un carré au moins garde une note non envoyée. Le rangement
     * recrée les carrés et ne peut pas emporter leur file (elle porte le texte
     * des notes, et le chemin est une ligne de commande) : il attend. */
    readonly property bool filesEnAttente: root.enAttente > 0

    property bool bureauEnCours: false

    /* « Poser cette note sur le bureau » : un carré de plus, à la première
     * place libre. */
    function poserSurLeBureau(noteId) {
        if (root.bureauEnCours) {
            return;
        }
        root.bureauEnCours = true;
        root.bureauLit(function (etat) {
            var carres = (etat && etat.carres) || [];
            /* Déjà posée ? Le dire, au lieu d'empiler un second carré sur la
             * même note sans rien expliquer. */
            for (var i = 0; i < carres.length; i++) {
                if (carres[i].noteId === noteId) {
                    root.bureauEnCours = false;
                    root.lastError = i18n("This note is already on the desktop.");
                    return;
                }
            }
            if (Grille.compteCarres(carres) >= Grille.PLAFOND) {
                root.bureauEnCours = false;
                root.lastError = i18np("There is already %1 note on the desktop, which is the limit.",
                                       "There are already %1 notes on the desktop, which is the limit.",
                                       Grille.compteCarres(carres));
                return;
            }
            var place = Grille.premierLibre(carres, root.zoneBureau);
            if (!place) {
                root.bureauEnCours = false;
                root.lastError = i18n("There is no room left on the desktop for another note.");
                return;
            }
            root.bureauPose(noteId, place, function (reponse) {
                root.bureauEnCours = false;
                if (!reponse || !reponse.ok) {
                    /* Deux causes, deux phrases : « refusé » envoie chercher le
                     * verrou des widgets, ce qui est une fausse piste quand le
                     * bureau n'existe simplement pas encore. */
                    root.lastError = reponse && reponse.reason === "aucun_bureau"
                        ? i18n("The desktop is not ready yet. Try again in a moment.")
                        : i18n("The desktop refused the new note. Are widgets locked?");
                }
            });
        });
    }

    /* « Ranger les carrés en grille ». Déplacer étant impossible, chaque carré
     * est retiré puis reposé — d'où les deux refus ci-dessous, qui valent mieux
     * qu'un bureau bien rangé et dépairé. */
    function rangerLesCarres() {
        if (root.bureauEnCours) {
            return;
        }
        var empechement = Grille.rangementPossible({
            coffrePret: root.walletReady,
            filesEnAttente: root.filesEnAttente,
            horsLigne: root.offline,
            combienDeNotes: root.notes.length,
            tirageEnCours: root.enVol
        });
        if (empechement === "sans_coffre") {
            root.lastError = i18n("Tidying would unpair the notes on this computer, which has no wallet. Nothing was moved.");
            return;
        }
        if (empechement === "file_en_attente") {
            root.lastError = i18n("A note has not been sent yet. Wait for it to go, then tidy again.");
            return;
        }
        /* 🔴 Les trois refus qui protègent les carrés eux-mêmes. Tidier sur un
         * cache qu'on ne peut pas croire, c'est retirer des carrés dont on
         * ignore si leur note existe encore. */
        if (empechement === "hors_ligne") {
            root.lastError = i18n("Not while offline: the desktop notes cannot be checked against Symbifox. Nothing was moved.");
            return;
        }
        if (empechement === "cache_vide") {
            root.lastError = i18n("No notes loaded yet. Wait for Symbifox to answer, then tidy again.");
            return;
        }
        if (empechement === "tirage_en_cours") {
            root.lastError = i18n("Still loading your notes. Try again in a moment.");
            return;
        }
        root.bureauEnCours = true;
        root.bureauLit(function (etat) {
            var carres = (etat && etat.carres) || [];
            var ordre = [];
            var vues = root.notes;
            for (var i = 0; i < vues.length; i++) {
                ordre.push(vues[i].id);
            }
            var r = Grille.rangement(carres, ordre, root.zoneBureau);
            if (!r.plan.length && !r.aRetirer.length) {
                root.bureauEnCours = false;
                root.lastError = i18n("There is nothing on the desktop to tidy.");
                return;
            }
            root.bureauRange(r.plan, r.aRetirer, function (reponse) {
                root.bureauEnCours = false;
                if (!reponse || !reponse.ok) {
                    /* Deux causes, deux phrases — comme pour la pose : « refusé »
                     * envoie chercher le verrou des widgets, ce qui est une
                     * fausse piste quand le bureau n'existe pas encore. */
                    root.lastError = reponse && reponse.reason === "aucun_bureau"
                        ? i18n("The desktop is not ready yet. Try again in a moment.")
                        : i18n("The desktop could not be tidied. Are widgets locked?");
                    return;
                }
                /* 🔴 Un plan non vide qui ne range RIEN est un échec, pas un
                 * succès : les carrés planifiés ont disparu entre la lecture et
                 * l'application, et le script sautait chacun en silence. */
                if (reponse.prevus > 0 && reponse.ranges === 0
                        && reponse.perdus === 0) {
                    root.lastError = i18n("Nothing could be tidied. The desktop notes may have moved or been removed.");
                    return;
                }
                /* 🔴 Ranger, c'est retirer PUIS reposer. Quand la repose est
                 * refusée — widgets verrouillés, conteneur qui dit non — le
                 * carré est déjà parti, et le script rendait `ok` quand même :
                 * des pense-bêtes disparaissaient du bureau sans un mot. */
                if (reponse.perdus > 0) {
                    root.lastError = i18np("One note could not be put back on the desktop. Open it again from the list.",
                                           "%1 notes could not be put back on the desktop. Open them again from the list.",
                                           reponse.perdus);
                }
            });
        });
    }

    /* ── Les couleurs du locataire ───────────────────────────────────────
     *
     * 🔴 La palette du menu « ⋯ » vient de `/prefs`, jamais du code. Les sept
     * pastels de Windows écrites ici seraient la marque de personne — et elles
     * partiraient EN BASE au premier choix, donc sur le mobile et sur le web
     * d'un locataire qui ne les a jamais voulues. Mesuré le 2026-11-08 : la
     * route rend `swatches` (les nuanciers de la personne puis ceux de sa
     * société) et `palette` (les douze d'Odoo). */
    property var prefs: ({})
    /* ⚠️ `nuancier` et non `palette` : `palette` EXISTE DÉJÀ sur tout `Item` en
     * Qt 6, et la déclarer de nouveau la masque — « Member palette … overrides
     * a member of the base object », relevé par la première capture du
     * 2026-11-08. Un nom emprunté à la classe de base est un défaut qui dort
     * jusqu'au jour où quelqu'un lit l'autre. */
    readonly property var nuancier: Papier.palette(root.prefs)
    /* La couleur d'une note neuve : celle du nuancier du locataire, et rien si
     * le locataire n'en a pas déclaré. Voir papier.js. */
    readonly property string couleurNeuve: Papier.couleurParDefaut(root.prefs)
    property bool prefsDemandees: false

    function demandePrefs() {
        if (root.prefsDemandees || !root.paired || root.pairing) {
            return;
        }
        root.prefsDemandees = true;
        root.suivi(Api.prefs(root.server, root.token,
            function (data) {
                root.prefs = data || ({});
            },
            function (err) {
                /* Rouvrir la porte : un échec passager ne doit pas priver le
                 * menu de couleurs pour le reste de la session. ⚠️ Et SURTOUT
                 * pas de `lastError` ici — la palette est un agrément, pas une
                 * panne, et un message rouge pour un menu incomplet
                 * masquerait les vraies pannes. */
                root.prefsDemandees = false;
            }));
    }

    /* Choisir la couleur d'une note. Elle part au SERVEUR : c'est ce qui fait
     * qu'un pense-bête jaune sur le bureau est jaune aussi sur le téléphone.
     * Une chaîne vide retire la couleur (le serveur remet `color_hex` à faux
     * et l'index à zéro). */
    function choisirCouleur(note, hex) {
        if (!note || !root.paired) {
            return;
        }
        if (root.offline) {
            root.refuseEcritureHorsLigne();
            return;
        }
        root.suivi(Api.update(root.server, root.token, note.id, { color_hex: hex || "" },
            note.write_date,
            function (data) {
                var fresh = root.notesById;
                fresh[data.note.id] = data.note;
                root.notesById = fresh;
                root.notesByIdChanged();
            },
            function (conflict) {
                if (conflict.note) {
                    var fresh = root.notesById;
                    fresh[conflict.note.id] = conflict.note;
                    root.notesById = fresh;
                    root.notesByIdChanged();
                }
                root.lastError = i18n("This note changed elsewhere. Its colour was not changed.");
            },
            root.echecEcriture));
    }

    /* ── Fermer un carré ─────────────────────────────────────────────────
     *
     * Le « × » du bandeau. Il ferme le CARRÉ ; la note reste dans Symbifox,
     * sur le téléphone et dans la liste. C'est « Supprimer » du menu qui
     * archive.
     *
     * 🔴 Garde capitale : JAMAIS sur l'instance liste. Le 2026-11-03, le
     * bouton « ranger » a supprimé le widget depuis lequel on venait de le
     * commander, parce que rien ne distinguait la liste d'un carré. Ici la
     * garde est explicite, et un essai la tient.
     */
    function fermerLeCarre() {
        if (!root.pinnedMode) {
            return;
        }
        /* 🔴 Fermer un carré RETIRE l'applet, et sa configuration part avec
         * lui — c'est ce même lot qui l'écrit, dans grille.js. Or la file
         * d'envoi vit DANS cette configuration : hors ligne, « + », on tape,
         * on ferme, et la note n'a jamais existé nulle part. Le refus vaut
         * mieux qu'un carré bien rangé et une phrase perdue. */
        /* ⚠️ `enAttente` et non `outbox.length` : une entrée que le serveur
         * refuse obstinément ne partira JAMAIS (cinq essais, puis `nextToFlush`
         * rend null). Compter celle-là, c'était faire d'une garde un
         * interrupteur : le conseil « attends qu'elle parte » devenait
         * impossible à suivre, et le carré n'était plus fermable autrement
         * qu'en éditant la configuration à la main. */
        if (root.enAttente > 0) {
            root.lastError = i18np("This note has not reached Symbifox yet. Wait for it to go, then close.",
                                   "%1 notes have not reached Symbifox yet. Wait for them to go, then close.",
                                   root.enAttente);
            return;
        }
        /* Ce qui est tapé mais pas encore enregistré — et SEULEMENT pour la
         * note de CE carré. `enTrainDEcrire` répond « y a-t-il un brouillon
         * quelque part », ce qui n'est pas la même question. */
        if (root.brouillonNonEnregistre(root.pinnedNoteId)) {
            root.lastError = i18n("This note is not saved yet. It will be sent in a moment — then close it.");
            return;
        }
        var geste = Plasmoid.internalAction ? Plasmoid.internalAction("remove") : null;
        if (geste) {
            geste.trigger();
            return;
        }
        /* Plasma n'a pas offert le geste : le dire plutôt que de laisser un
         * bouton mort. Retirer le widget par un script de bureau serait
         * possible, mais il faudrait le retrouver par sa note — et deux carrés
         * sur la même note partiraient ensemble. */
        root.lastError = i18n("This desktop would not close the note. Remove the widget from the desktop instead.");
    }

    /* ── Un carré neuf, vide ─────────────────────────────────────────────
     *
     * Le « + » des Pense-bêtes de Windows : un carré paraît, on tape dedans.
     *
     * 🔴 Il n'a pas encore de note, donc pas d'identifiant — et `noteId = 0`
     * est DÉJÀ pris : c'est la liste. D'où le sentinelle NÉGATIF. Un carré
     * neuf est un carré (il n'affiche pas la liste), mais le rangement ne le
     * déplace pas : son texte ne vit qu'en mémoire tant qu'il n'est pas parti,
     * et ranger veut dire retirer puis reposer.
     */
    /* ⚠️ En minuscule : un nom de PROPRIÉTÉ QML ne peut pas commencer par une
     * majuscule, et `CARRE_NEUF` faisait échouer le chargement de tout le
     * fichier — avec « Property names cannot begin with an upper case letter »
     * pour seul message, et un widget entièrement mort. La capture d'allure ne
     * pouvait pas le voir : elle charge les vues, pas la racine. */
    readonly property int idCarreNeuf: -1

    function nouveauCarre() {
        if (root.bureauEnCours || !root.paired) {
            return;
        }
        root.bureauEnCours = true;
        root.bureauLit(function (etat) {
            var carres = (etat && etat.carres) || [];
            if (Grille.compteCarres(carres) >= Grille.PLAFOND) {
                root.bureauEnCours = false;
                root.lastError = i18np("There is already %1 note on the desktop, which is the limit.",
                                       "There are already %1 notes on the desktop, which is the limit.",
                                       Grille.compteCarres(carres));
                return;
            }
            var place = Grille.premierLibre(carres, root.zoneBureau);
            if (!place) {
                root.bureauEnCours = false;
                root.lastError = i18n("There is no room left on the desktop for another note.");
                return;
            }
            root.bureauPose(root.idCarreNeuf, place, function (reponse) {
                root.bureauEnCours = false;
                if (!reponse || !reponse.ok) {
                    root.lastError = reponse && reponse.reason === "aucun_bureau"
                        ? i18n("The desktop is not ready yet. Try again in a moment.")
                        : i18n("The desktop refused the new note. Are widgets locked?");
                }
            });
        });
    }

    /* Le carré neuf reçoit l'identifiant de SA note.
     *
     * 🔴 LE BROUILLON DOIT SUIVRE, et c'est tout sauf un détail. Le texte en
     * cours est rangé sous la clé du CARRÉ (-1) tant que la note n'existe pas ;
     * une fois adoptée, la surface lit sous la clé de la NOTE. Tout ce qui a
     * été tapé entre l'envoi de la création et la réponse du serveur restait
     * donc sous l'ancienne clé, que plus personne ne lirait jamais :
     * `enregistre()` trouvait `undefined` et sortait EN SILENCE, à chaque
     * pause, pour toujours — et le mot ne vivait qu'en mémoire. Trouvé et
     * mesuré par la relecture adverse du 2026-11-08 ; la note de conception le
     * disait pourtant fermé.
     *
     * ⚠️ Fonction NOMMÉE et non un bloc dans `flush` : le rappel d'un envoi
     * réel n'est pas atteignable depuis le harnais, donc un essai ne pouvait
     * que rejouer la logique à la main — ce qu'il a fait, et une mutation y a
     * survécu. Ce qui n'est pas appelable n'est pas éprouvable. */
    function adopteLaNote(note) {
        if (!note || !note.id) {
            return;
        }
        var enCours = root.noteBrouillon(root.idCarreNeuf);
        Plasmoid.configuration.noteId = note.id;
        Plasmoid.configuration.adopteUuid = "";
        Plasmoid.configuration.writeConfig();
        /* ⚠️ Et SEULEMENT s'il reste quelque chose à enregistrer. Le cas
         * courant est que la pause a créé la note avec exactement ce texte :
         * recopier le brouillon sous la nouvelle clé fabriquait alors un
         * brouillon mort, que plus rien n'effaçait. */
        if (enCours !== undefined && enCours !== null
                && enCours !== (note.text || "")) {
            root.poseBrouillon(note.id, enCours);
        } else {
            /* ⚠️ Et on EFFACE la clé d'arrivée. Elle peut porter un brouillon
             * mort d'une vie antérieure de ce carré ; le laisser rouvrirait
             * exactement le défaut qu'on vient de fermer. */
            root.poseBrouillon(note.id, null);
        }
        root.poseBrouillon(root.idCarreNeuf, null);
    }

    /* Ce carré neuf vient de recevoir son premier texte : on crée la note, et
     * on retiendra son identifiant quand le serveur l'aura donné. */
    function creerDansCeCarre(texte) {
        if (!texte || !texte.trim()) {
            return;
        }
        /* 🔴 Une création est peut-être DÉJÀ en file pour ce carré : on tape,
         * la pause crée la note, et on continue de taper avant que le serveur
         * n'ait répondu. Créer une seconde fois couperait la phrase en deux
         * notes — et le carré n'en adopterait qu'une. Tant que l'entrée n'est
         * pas partie, on la corrige sur place. */
        var enCours = Plasmoid.configuration.adopteUuid;
        if (enCours && Store.retexte(root.outbox, enCours, texte)) {
            root.rangeOutbox();
            root.flush();
            return;
        }
        var uuid = root.makeUuid();
        root.outbox = Store.queueCreate(root.outbox, texte.trim(), uuid,
                                        root.couleurNeuve);
        /* ⚠️ Rangé dans la CONFIGURATION et pas en mémoire : si plasmashell
         * redémarre entre la frappe et l'envoi, la file survit (elle est dans
         * la configuration) — l'intention d'adopter doit survivre avec elle,
         * sinon la note serait créée et le carré resterait vide pour toujours. */
        Plasmoid.configuration.adopteUuid = uuid;
        Plasmoid.configuration.writeConfig();
        root.rangeOutbox();
        root.flush();
    }

    /* Ouvrir une note depuis la liste : c'est le geste de Windows, où cliquer
     * une ligne ouvre sa fenêtre. */
    function ouvrirLaNote(noteId) {
        root.poserSurLeBureau(noteId);
    }

    /* ── Semer les carrés, une fois ──────────────────────────────────────
     *
     * Windows repose au démarrage les notes qui étaient ouvertes. Chez nous,
     * c'est Plasma qui le fait : un carré est un applet, et le bureau les
     * restaure lui-même. Il ne reste donc qu'à SEMER, une seule fois, quand on
     * vient d'apparier.
     *
     * 🔴 « Une seule fois » n'est pas une économie, c'est une garde. Semer à
     * chaque démarrage ferait revenir les carrés que la personne a fermés, et
     * fermer un carré ne voudrait plus rien dire.
     */
    function semerLesCarres() {
        /* ⚠️ `surLeBureau` AUSSI. `autoPoseFait` est une clé PAR INSTANCE :
         * ajouter le widget au panneau — ce à quoi sert la représentation
         * compacte — faisait semer une seconde fois, et les carrés qu'on avait
         * fermés revenaient. Mesuré par la relecture adverse : facteur de forme
         * horizontal, deux `addWidget`. Un panneau ne sème pas ; il montre. */
        if (Plasmoid.configuration.autoPoseFait || root.pinnedMode
                || !root.surLeBureau
                || !root.paired || root.bureauEnCours || root.notes.length === 0) {
            return;
        }
        /* ⚠️ Pas pendant qu'une passe est en vol. Le cache se remplit page par
         * page (cinquante notes à la fois) : semer au milieu d'un tirage
         * poserait les notes de la première page, marquerait le semis comme
         * fait, et les autres n'auraient jamais leur carré. Mesuré sans
         * conséquence chez Olivier — il n'a qu'une note active — mais c'est
         * une chance, pas une garde. */
        if (root.enVol) {
            return;
        }
        root.bureauEnCours = true;
        root.bureauLit(function (etat) {
            var carres = (etat && etat.carres) || [];
            var deja = {};
            for (var i = 0; i < carres.length; i++) {
                deja[carres[i].noteId] = true;
            }
            var aSemer = [];
            for (var j = 0; j < root.notes.length; j++) {
                var n = root.notes[j];
                /* Ses notes à elle seulement : les carrés d'un collègue
                 * tapisseraient le bureau de notes qu'on ne peut pas écrire. */
                if (n.mine === false || deja[n.id]) {
                    continue;
                }
                aSemer.push(n.id);
            }
            /* Quoi qu'il arrive ensuite, on ne recommence pas : un semis qui
             * échoue à mi-chemin et rejouerait au prochain démarrage finirait
             * par poser deux fois ce qui a marché.
             *
             * ⚠️ Mais les échecs se DISENT. Si plasmashell n'est pas prêt juste
             * après l'appairage, le semis se marquait fait, rien n'était posé,
             * et rien ne le disait : la personne cherchait ses pense-bêtes sur
             * un bureau vide. */
            Plasmoid.configuration.autoPoseFait = true;
            Plasmoid.configuration.writeConfig();
            root.semisVoulus = aSemer.length;
            root.semisPoses = 0;
            root.semeSuivant(aSemer, carres);
        });
    }

    property int semisVoulus: 0
    property int semisPoses: 0

    function semeSuivant(restants, carres) {
        if (!restants.length || Grille.compteCarres(carres) >= Grille.PLAFOND) {
            root.bureauEnCours = false;
            if (root.semisVoulus > 0 && root.semisPoses === 0) {
                root.lastError = i18n("Your notes could not be put on the desktop. Open them from this list, or tidy once the desktop is ready.");
            }
            return;
        }
        var place = Grille.premierLibre(carres, root.zoneBureau);
        if (!place) {
            root.bureauEnCours = false;
            return;
        }
        var noteId = restants.shift();
        root.bureauPose(noteId, place, function (reponse) {
            if (reponse && reponse.ok) {
                root.semisPoses += 1;
            }
            /* La place est maintenant prise, que la pose ait réussi ou non :
             * sans cela, un échec ferait réessayer la même case en boucle. */
            carres.push({ id: (reponse && reponse.id) || 0, noteId: noteId,
                          x: place.x, y: place.y,
                          largeur: place.largeur, hauteur: place.hauteur });
            root.semeSuivant(restants, carres);
        });
    }

    /* Au démarrage : le portefeuille est-il utilisable, et porte-t-il déjà un
     * jeton ? Tant que la réponse n'est pas là, le widget ne demande rien au
     * serveur — sinon il se croirait non apparié et proposerait d'apparier
     * par-dessus un jeton valide. */
    function walletProbe() {
        root.walletRun("probe", [], function (reponse) {
            root.walletChecked = true;
            root.walletReady = !!(reponse && reponse.wallet);
            root.walletReason = (reponse && reponse.reason) || "";
            if (!root.walletReady) {
                /* Repli : la configuration de l'instance, comme avant. */
                root.token = Plasmoid.configuration.token || "";
                root.afterToken();
                return;
            }
            /* 🔴 Le portefeuille prend le relais : un jeton qui traînait dans
             * la configuration est effacé SANS être recopié, parce que le
             * recopier voudrait dire le passer en argument d'un processus,
             * donc l'exposer dans /proc à tout le poste. Le prix est un
             * appairage de plus, une fois, et le widget le dit. */
            if (Plasmoid.configuration.token) {
                Plasmoid.configuration.token = "";
                Plasmoid.configuration.writeConfig();
                root.walletMigrated = true;
            }
            if (!reponse.has_token) {
                root.afterToken();
                return;
            }
            root.walletRun("read", [], function (lu) {
                if (lu && lu.ok && lu.token) {
                    /* 🔴 Le jeton ne vaut QUE sur l'adresse qui l'accompagne.
                     *
                     * Deux cas, et le coffre tranche les deux :
                     *
                     * — l'instance n'a pas d'adresse (neuve, ou un carré qu'on
                     *   vient de poser) : elle adopte celle du coffre et
                     *   synchronise tout de suite, sans rien redemander. C'est
                     *   la raison d'être du coffre partagé.
                     *
                     * — l'instance en a une AUTRE : quelqu'un a changé
                     *   l'adresse dans la page de réglages, qui promet qu'il
                     *   faudra réapparier — sauf que rien ne l'appliquait, et
                     *   le jeton serait parti en en-tête `Authorization` vers
                     *   l'adresse fraîchement tapée. On oublie donc le jeton et
                     *   l'écran redemande un appairage. ⚠️ Constaté au
                     *   démarrage plutôt que branché sur le geste : une garde
                     *   qui dépend d'avoir intercepté chaque chemin de
                     *   changement n'en est pas une.
                     *
                     * Aucun appel porteur du jeton ne peut être parti d'ici :
                     * `pulse` ne tourne que sur `paired`, et la sonde
                     * d'identité est une route publique, sans jeton. */
                    root.coffreServeurInconnu = !!lu.server_unknown;
                    var duCoffre = root.normalizeServer(lu.server || "");
                    /* 🔴 Posé ICI, avant les deux sorties ci-dessous.
                     * Il l'était après, donc une divergence le laissait vide —
                     * et `onServerChanged`, qui sort quand il est vide, ne
                     * pouvait rien faire : remettre l'adresse, le geste que le
                     * message demande, restait sans effet. Une valeur qu'on
                     * pose après les `return` est une valeur que les cas
                     * d'erreur n'ont jamais. */
                    root.coffreServeur = duCoffre;
                    if (root.coffreServeurInconnu) {
                        /* ⚠️ État du coffre inconnu : ni adopter, ni graver, ni
                         * dissocier. Le jeton sert, l'instance garde son
                         * adresse, et la garde se rejouera quand le
                         * portefeuille répondra. */
                        root.token = lu.token;
                        root.afterToken();
                        return;
                    }
                    /* 🔴 `root.server` est DÉJÀ normalisée (voir sa
                     * déclaration) : comparer un côté normalisé à un côté brut
                     * faisait d'une simple barre oblique finale une
                     * « divergence ». Et l'ensemencement de l'image écrit
                     * l'adresse verbatim depuis `tenant.json`, donc le cas
                     * n'était pas théorique. */
                    if (duCoffre && root.server && duCoffre !== root.server) {
                        /* ⚠️ CETTE instance se tait ; le coffre reste INTACT.
                         *
                         * J'y purgeais l'entrée commune, ce qui dépairait
                         * toutes les autres instances — l'inverse exact de la
                         * raison d'être du coffre partagé, et pour le geste
                         * d'un seul carré. Le coffre fait autorité : c'est
                         * l'instance qui est à côté, et c'est elle qui se
                         * tait. Réapparier depuis l'écran écrira la nouvelle
                         * adresse pour tout le monde — un geste explicite. */
                        /* ⚠️ UNE seule substitution : `i18n` à trois
                         * arguments casse dans le harnais, où il devient
                         * `qsTr` (dont le troisième argument doit être un
                         * nombre). Piège déjà payé le 11-02. */
                        root.dissocieJeton(
                            i18n("This computer is paired with another Symbifox (%1). Pair this widget again, or put that address back.",
                                 duCoffre));
                        root.afterToken();
                        return;
                    }
                    if (duCoffre && !root.server) {
                        root.setServer(duCoffre);
                    }
                    root.token = lu.token;
                } else {
                    root.token = "";
                    if (lu && lu.reason === "malformed") {
                        /* Ce que le portefeuille rend n'est pas un jeton. Ne
                         * pas s'en servir, et le dire. */
                        root.lastError = i18n("The wallet returned something that is not a token. Pair this widget again.");
                    }
                }
                root.afterToken();
            });
        });
    }

    /* Le jeton est connu (ou son absence l'est) : la machine peut démarrer. */
    function afterToken() {
        if (!root.paired) {
            return;
        }
        /* Silencieuse : c'est elle qui rapporte le nom du locataire et ses
         * couleurs pour un widget déjà en service. */
        root.probeServerIdentity(root.server, true);
        root.probe();
        if (root.outbox.length > 0) {
            root.flush();
        }
    }

    /* ── Odds and ends ─────────────────────────────────────────────────── */

    function deviceName() {
        /* Named after the machine, because that is what the person will look
         * for in « Mes appareils » when they want to revoke one of several. */
        var host = Qt.application.name;
        return i18n("Blue Fox OS desktop (%1)", host || "Plasma");
    }

    function makeUuid() {
        /* 🔴 Un UUID, au format exact. Le serveur le passe à `uuid.UUID()`
         * (`_mobile_clean_uuid`), qui exige 32 chiffres hexadécimaux en
         * 8-4-4-4-12. La première version rendait 27 chiffres : TOUTE création
         * partait en 400 « The device identifier is not a valid UUID », et le
         * texte restait bloqué dans la file sans que rien ne le dise.
         *
         * Il n'a pas besoin d'être imprévisible — c'est la clé d'idempotence
         * du serveur, pas un secret — mais il doit être unique et bien formé.
         */
        function quartets(n) {
            var out = "";
            for (var i = 0; i < n; i++) {
                out += Math.floor(Math.random() * 16).toString(16);
            }
            return out;
        }
        /* Version 4, variante RFC 4122 : le « 4 » et le chiffre de variante
         * sont posés à la main, le reste est tiré au hasard. */
        return quartets(8) + "-" + quartets(4) + "-4" + quartets(3) + "-"
            + "89ab".charAt(Math.floor(Math.random() * 4)) + quartets(3) + "-"
            + quartets(12);
    }
}
