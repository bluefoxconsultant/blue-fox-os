/*
 * UN pense-bête : un carré, une note, l'allure de l'application de Windows.
 *
 * Ce que cette surface emprunte aux Pense-bêtes de Windows, et pourquoi :
 *   — le papier occupe TOUT le carré, sans fond de widget ni cadre de thème ;
 *     c'est ce qui distingue un pense-bête d'une carte posée dans un panneau,
 *     et c'est exactement ce qui manquait (« je ne vois qu'un rectangle avec
 *     les notes dedans », 2026-11-08) ;
 *   — un bandeau de la même couleur en plus foncé, avec « + » à gauche,
 *     « ⋯ » et « × » à droite ;
 *   — on tape DIRECTEMENT dans le corps, et ça s'enregistre tout seul ;
 *   — « × » ferme le carré sans toucher la note ; c'est « Supprimer » du menu
 *     qui supprime, et lui demande confirmation.
 *
 * 🔴 Ce que Plasma ne permet pas, et qui n'est donc pas promis ici : déplacer
 * un carré depuis son propre contenu. Un applet ne choisit pas sa géométrie
 * (mesuré le 2026-11-03) — c'est le bureau qui déplace et redimensionne, par
 * son propre geste. Chaque carré étant un applet distinct, il garde SA taille.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

import "../code/couleurs.js" as Couleurs
import "../code/papier.js" as Papier

Item {
    id: carre

    /* En mode carré, le cache ne porte qu'une note : c'est `probeOne` qui le
     * remplit, pas le cycle complet. */
    property alias menuDeLaNote: menu
    property alias menuDesProjets: projets

    readonly property var note: root.notes.length > 0 ? root.notes[0] : null
    readonly property bool mine: !!carre.note && carre.note.mine !== false
    /* Un carré NEUF n'a pas encore de note : il s'écrit quand même, et c'est
     * sa première frappe qui crée la note. Sans cela, le « + » de Windows
     * poserait un carré où l'on ne peut rien taper. */
    readonly property bool canEdit: root.carreNeuf
        || (carre.mine && !!carre.note && carre.note.editable !== false)

    /* ── Les couleurs, toutes mesurées ───────────────────────────────────── */

    /* 🔴 Sans couleur venue du serveur, on prend celle du THÈME et non un jaune
     * inventé : le mobile et le web montrent la même note sans couleur, et une
     * couleur née ici partirait en base au premier enregistrement. Ce qui fait
     * le pense-bête alors, c'est la forme — bandeau, coins, ombre. */
    readonly property color papier: {
        var p = Papier.papier(carre.note);
        return p.length > 0 ? p : Kirigami.Theme.backgroundColor;
    }
    readonly property bool colore: Papier.papier(carre.note).length > 0
    /* 🔴 L'ENCRE D'ABORD, puis le bandeau CONTRE ELLE.
     *
     * `bandeau()` sans argument mesure contre `texteSur(papier)` — le meilleur
     * des deux extrêmes — alors que ce qui est peint est `texteLisible(papier,
     * text_color)`, c'est-à-dire la couleur du SERVEUR quand elle atteint la
     * norme. Les deux diffèrent, et le commentaire d'avant affirmait le
     * contraire. Pire cas mesuré par la relecture adverse : papier `#EEEEAA`,
     * `text_color` `#776666` → 4,50:1 sur le papier, 3,60:1 sur le bandeau,
     * qui porte « + », « ⋯ » et « × ». */
    readonly property string encreHex: carre.colore
        ? Couleurs.texteLisible(Papier.papier(carre.note),
                                carre.note ? carre.note.text_color : null)
        : ""
    readonly property color encre: carre.colore
        ? carre.encreHex : Kirigami.Theme.textColor
    readonly property color bandeau: carre.colore
        ? Papier.bandeauLisible(Papier.papier(carre.note), carre.encreHex)
        : Kirigami.Theme.alternateBackgroundColor
    readonly property color trait: carre.colore
        ? Papier.trait(Papier.papier(carre.note), carre.encreHex)
        : Qt.alpha(Kirigami.Theme.textColor, 0.15)

    /* ── L'enregistrement automatique ────────────────────────────────────── */

    /* Le geste de Windows : on tape, ça part. Pas de bouton « enregistrer »,
     * pas de carte à replier.
     *
     * ⚠️ Après une PAUSE, jamais à chaque touche : une requête par frappe
     * noierait le serveur et perdrait la course contre la passe suivante. Et
     * `enTrainDEcrire` ralentit déjà la cadence pendant ce temps-là.
     *
     * 🔴 Le texte en cours vit dans la RACINE (`brouillons`), pas ici : cette
     * surface est détruite et recréée au moindre changement du modèle, et le
     * texte tapé disparaissait sous les doigts avant que ce détour n'existe. */
    property int pauseAvantEnvoi: 1200
    property bool modifie: false

    Timer {
        id: minuteurEnvoi
        interval: carre.pauseAvantEnvoi
        onTriggered: carre.enregistre()
    }

    function enregistre() {
        if (!carre.canEdit || !carre.modifie) {
            return;
        }
        /* Le carré neuf : sa première phrase CRÉE la note. Hors ligne, elle
         * part en file comme n'importe quelle création — c'est une création,
         * pas une modification, et la file les porte. */
        if (root.carreNeuf) {
            var neuf = root.noteBrouillon(root.pinnedNoteId);
            if (neuf === undefined || neuf === null || !neuf.trim()) {
                return;
            }
            carre.modifie = false;
            root.creerDansCeCarre(neuf);
            return;
        }
        if (!carre.note) {
            return;
        }
        var texte = root.noteBrouillon(carre.note.id);
        if (texte === undefined || texte === null) {
            return;
        }
        if (texte === (carre.note.text || "")) {
            carre.modifie = false;
            /* 🔴 ET ON L'EFFACE. Un brouillon identique au texte de la note est
             * un brouillon mort : il n'y a rien à enregistrer. Le laisser
             * laissait `enTrainDEcrire` vrai POUR TOUJOURS — donc le « × » du
             * bandeau refusait de fermer avec un message faux (« it will be
             * sent in a moment » pour une note déjà enregistrée), et le carré
             * restait à la cadence rapide, vingt requêtes la minute, ce que
             * `PINNED_MS` existe précisément pour empêcher. C'est le chemin le
             * plus courant de cette version : « + », on tape, on s'arrête. */
            root.poseBrouillon(carre.note.id, null);
            return;
        }
        /* Hors ligne, une MODIFICATION n'est pas mise en file (la v1 l'assume)
         * — mais elle doit le DIRE, sinon le texte ne vit plus qu'en mémoire. */
        if (root.offline) {
            root.refuseEcritureHorsLigne();
            return;
        }
        carre.modifie = false;
        root.saveNote(carre.note, texte);
    }

    /* Partir en emportant ce qui n'est pas encore enregistré. Sans cela, fermer
     * le carré ou changer de bureau pendant la pause perdait la dernière
     * phrase tapée. */
    Component.onDestruction: carre.enregistre()

    /* ── Le papier ───────────────────────────────────────────────────────── */

    Kirigami.ShadowedRectangle {
        id: feuille
        anchors.fill: parent
        anchors.margins: Math.round(Kirigami.Units.smallSpacing / 2)
        color: carre.papier
        radius: Kirigami.Units.cornerRadius
        border.width: 1
        border.color: carre.trait
        shadow.size: Kirigami.Units.gridUnit
        shadow.yOffset: 2
        shadow.color: Qt.rgba(0, 0, 0, 0.35)

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            /* ── Le bandeau ──────────────────────────────────────────────── */
            Rectangle {
                id: entete
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(Kirigami.Units.gridUnit * 1.7)
                color: carre.bandeau
                /* Les coins du HAUT seulement : un rectangle arrondi sur ses
                 * quatre coins laisserait voir le papier au-dessus du bandeau. */
                radius: feuille.radius
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: parent.radius
                    color: parent.color
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Kirigami.Units.smallSpacing
                    anchors.rightMargin: Kirigami.Units.smallSpacing
                    spacing: 0

                    BoutonBandeau {
                        icone: "list-add"
                        infobulle: i18n("New note")
                        encre: carre.encre
                        visible: root.paired
                        onActive: root.nouveauCarre()
                    }

                    Item { Layout.fillWidth: true }

                    /* Ce qui n'est pas encore parti, dit sur le bandeau même :
                     * dans un carré il n'y a pas de place pour une phrase. */
                    Kirigami.Icon {
                        source: "cloud-upload"
                        visible: root.offline || minuteurEnvoi.running
                        opacity: 0.7
                        color: carre.encre
                        implicitWidth: Kirigami.Units.iconSizes.small
                        implicitHeight: Kirigami.Units.iconSizes.small
                        /* ⚠️ Le libellé est calculé au-dessus et l'affectation
                         * tient sur UNE ligne. Coupée en trois, ses deux
                         * dernières lignes survivaient au `sed` du harnais et
                         * se greffaient sur `implicitHeight`, qui devenait un
                         * ternaire parfaitement valide : le harnais éprouvait
                         * une autre hauteur que celle du code, sans une erreur.
                         * Un essai garde maintenant la forme (voir
                         * `test_aucune_affectation_attachee_coupee_en_deux`). */
                        readonly property string infobulle: root.offline ? i18n("Offline: this note is not saved yet.") : i18n("Saving…")
                        PlasmaComponents.ToolTip.text: infobulle
                        PlasmaComponents.ToolTip.visible: survolEnvoi.hovered
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                        HoverHandler { id: survolEnvoi }
                    }

                    BoutonBandeau {
                        id: boutonMenu
                        icone: "overflow-menu"
                        infobulle: i18n("Note options")
                        encre: carre.encre
                        visible: !!carre.note
                        onActive: menu.ouvrePour(carre.note)
                    }

                    BoutonBandeau {
                        icone: "window-close"
                        infobulle: i18n("Close this note (it stays in Symbifox)")
                        encre: carre.encre
                        onActive: root.fermerLeCarre()
                    }
                }
            }

            /* ── Le corps ────────────────────────────────────────────────── */

            /* Mien et modifiable : on tape dedans, point. */
            PlasmaComponents.TextArea {
                id: corps
                Layout.fillWidth: true
                Layout.fillHeight: true
                /* L'air autour du texte fait autant que la couleur : collé au
                 * bandeau et au bord, le même texte a l'air d'un champ de
                 * formulaire. Windows laisse respirer, et c'est gratuit. */
                Layout.margins: Kirigami.Units.largeSpacing
                visible: carre.canEdit
                wrapMode: TextEdit.Wrap
                color: carre.encre
                /* 🔴 Le fond du champ est retiré : un champ de saisie dessiné
                 * par-dessus le papier ferait une boîte dans la note, ce qui
                 * est précisément l'allure qu'on quitte. */
                background: null
                /* 🔴 PAS `placeholderText` : mesuré le 2026-11-08, retirer le
                 * fond du champ (ce qu'il faut faire pour qu'un pense-bête ne
                 * ressemble pas à un formulaire) EFFACE aussi le substitut de
                 * texte — l'item existe, il se déclare visible, et rien n'est
                 * peint. Un carré neuf était donc une surface parfaitement
                 * vide, sans un mot pour dire qu'on peut y écrire. On le peint
                 * donc soi-même, juste en dessous. */
                selectByMouse: true

                /* 🔴 Le brouillon est rangé sous l'identifiant du CARRÉ quand
                 * la note n'existe pas encore (donc sous le sentinelle négatif)
                 * et sous celui de la note ensuite. Deux magasins auraient
                 * perdu la phrase au moment précis de l'adoption. */
                /* ⚠️ Défensif des DEUX côtés. `carre.note.id` manque d'une
                 * charge partielle, et `Plasmoid.configuration.noteId` rend
                 * `undefined` le temps d'un `reloadConfig()` — ce qui arrive
                 * chaque fois qu'on repointe le widget. Relevé dans le JOURNAL
                 * du bureau d'Olivier le 2026-11-08, onze fois en trois
                 * minutes, après la pose en production. */
                readonly property int cle: (carre.note && carre.note.id)
                    ? carre.note.id
                    : (root.pinnedNoteId || 0)

                text: {
                    var garde = root.noteBrouillon(corps.cle);
                    if (garde !== undefined && garde !== null) {
                        return garde;
                    }
                    return carre.note ? (carre.note.text || "") : "";
                }

                onTextChanged: {
                    if (!activeFocus) {
                        return;
                    }
                    root.poseBrouillon(corps.cle, corps.text);
                    carre.modifie = true;
                    minuteurEnvoi.restart();
                }

                /* Quitter le champ enregistre : attendre la pause alors qu'on
                 * est déjà parti ailleurs n'a aucun intérêt. */
                onActiveFocusChanged: {
                    if (!activeFocus && carre.modifie) {
                        minuteurEnvoi.stop();
                        carre.enregistre();
                    }
                }

                /* Le substitut, peint par nous. Il reste tant que rien n'est
                 * tapé, curseur dedans ou non — comme Windows. */
                PlasmaComponents.Label {
                    /* ⚠️ `corps`, par son identifiant, et NON `parent` : un
                     * enfant déclaré dans un `TextArea` n'est pas parenté au
                     * contrôle mais à sa zone de défilement, qui n'a ni
                     * `leftPadding` ni `topPadding`. D'où « Unable to assign
                     * [undefined] to int » toutes les quinze secondes dans le
                     * journal du bureau — invisible pour le harnais bouchonné,
                     * dont le TextArea est un QQC2 nu. */
                    anchors.left: corps.left
                    anchors.top: corps.top
                    anchors.leftMargin: corps.leftPadding
                    anchors.topMargin: corps.topPadding
                    visible: corps.text.length === 0
                    text: i18n("Write here…")
                    color: Qt.alpha(carre.encre, 0.45)
                }
            }

            /* Pas mien, ou mis en forme : on lit. */
            PlasmaComponents.Label {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: Kirigami.Units.largeSpacing
                visible: !!carre.note && !carre.canEdit
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                color: carre.encre
                verticalAlignment: Text.AlignTop
                text: carre.note ? (carre.note.text || "") : ""
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing
                visible: !!carre.note && !carre.canEdit
                wrapMode: Text.Wrap
                font: Kirigami.Theme.smallFont
                color: Qt.alpha(carre.encre, 0.7)
                text: carre.mine
                    ? i18n("This note has formatting the desktop cannot keep. Edit it in Symbifox.")
                    : i18n("Shared with you. Only its author can change it.")
            }

            /* 🔴 Ce que le serveur a refusé pour de bon. Un carré a sa PROPRE
             * file : si la sienne est bloquée, c'est ici qu'il faut le dire, et
             * nulle part ailleurs. Sans cela, le « × » refusait de fermer au
             * nom d'une note qui ne partirait jamais. */
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                visible: root.notesBloquees.length > 0
                spacing: 2

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.negativeTextColor
                    text: i18n("Symbifox refused this note. It will not be sent on its own.")
                }

                PlasmaComponents.Button {
                    text: i18n("Try again")
                    icon.name: "view-refresh"
                    flat: true
                    Layout.alignment: Qt.AlignRight
                    onClicked: root.reessayerLesBloquees()
                }
            }

            /* ── Le pied : ce que la note porte en plus de son texte ─────── */
            MetaNote {
                note: carre.note
                encre: carre.encre
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                Layout.bottomMargin: Kirigami.Units.smallSpacing
            }
        }

        /* La note a disparu : le dire, et offrir la sortie. Un carré vide
         * laisserait croire à une note vide. */
        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 2
            /* ⚠️ Pas sur un carré NEUF : il n'a pas de note parce qu'on ne
             * l'a pas encore écrite, et « chargement… » par-dessus un champ
             * vide serait un mensonge poli. */
            visible: !carre.note && !root.carreNeuf
            iconName: root.pinnedGone ? "dialog-information" : "note"
            text: root.pinnedGone ? i18n("This note is gone") : i18n("Loading…")
            explanation: root.pinnedGone
                ? i18n("It was deleted, or a colleague stopped sharing it.")
                : ""

            PlasmaComponents.Button {
                visible: root.pinnedGone
                text: i18n("Close this note")
                icon.name: "window-close"
                Layout.alignment: Qt.AlignHCenter
                onClicked: root.fermerLeCarre()
            }
        }
    }

    /* Le menu des gestes, partagé avec la liste : un seul endroit où les neuf
     * gestes du serveur sont câblés.
     *
     * ⚠️ Son parent visuel est NOMMÉ : sans cela Plasma évalue `parent.width`
     * sur un parent nul au montage et le journal se remplit d'une TypeError
     * par carré. */
    MenuNote {
        id: menu
        parent: feuille
        compact: true
        onDemandeTache: function (n) { projets.demande(n); }
    }

    MenuProjets { id: projets; parent: feuille }

    /* ── Un bouton du bandeau ────────────────────────────────────────────── */
    component BoutonBandeau: PlasmaComponents.ToolButton {
        property string icone: ""
        property string infobulle: ""
        property color encre: Kirigami.Theme.textColor
        signal active()

        icon.name: icone
        flat: true
        display: PlasmaComponents.AbstractButton.IconOnly
        implicitWidth: Math.round(Kirigami.Units.gridUnit * 1.6)
        implicitHeight: Math.round(Kirigami.Units.gridUnit * 1.5)
        /* 🔴 Le libellé ET son icône se peignent avec l'encre du papier, sinon
         * Plasma peint une icône de thème sombre sur un papier sombre. Même
         * défaut que le blanc sur bleu du 3 novembre, à un autre endroit. */
        icon.color: encre
        onClicked: active()
        PlasmaComponents.ToolTip.text: infobulle
        PlasmaComponents.ToolTip.visible: hovered && infobulle.length > 0
        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
    }
}
