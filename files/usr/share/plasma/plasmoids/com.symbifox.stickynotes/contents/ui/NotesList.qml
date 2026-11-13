/*
 * « Toutes mes notes » : la liste, à la manière de la fenêtre Notes list des
 * Pense-bêtes de Windows — un en-tête, un filtre, et une tuile de la couleur de
 * chaque note. Cliquer une tuile ouvre la note SUR LE BUREAU, comme Windows
 * ouvre sa fenêtre.
 *
 * Ce qui reste de nous et que Windows n'a pas : le champ d'écriture rapide en
 * haut. C'est le geste le plus fréquent — la note qu'on n'a pas encore écrite
 * compte plus que celles déjà au mur — et c'est la même décision que la page
 * /notes du bloc-notes. Le « + » de l'en-tête, lui, pose un carré vide sur le
 * bureau : le geste de Windows, pour qui veut écrire dans le carré.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: liste

    property string filtre: ""

    /* Le filtre est LOCAL : il trie ce que le widget a déjà, sans un
     * aller-retour. La portée du widget est petite (ses notes actives et les
     * partagées) et une recherche serveur ferait une requête par frappe. */
    readonly property var notesVues: {
        var q = liste.filtre.trim().toLowerCase();
        if (q.length === 0) {
            return root.notes;
        }
        var out = [];
        for (var i = 0; i < root.notes.length; i++) {
            var n = root.notes[i];
            var foin = ((n.title || "") + " " + (n.text || "")).toLowerCase();
            if (foin.indexOf(q) >= 0) {
                out.push(n);
            }
        }
        return out;
    }

    spacing: Kirigami.Units.smallSpacing

    /* ── L'en-tête ───────────────────────────────────────────────────────── */
    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing

        PlasmaComponents.TextField {
            id: champFiltre
            Layout.fillWidth: true
            placeholderText: i18n("Search notes…")
            /* ⚠️ `onTextChanged` est acceptable ICI, et seulement ici : rien ne
             * part sur le réseau, on filtre un tableau déjà en mémoire. */
            onTextChanged: liste.filtre = text
        }

        PlasmaComponents.ToolButton {
            objectName: "boutonNouvelleNote"
            icon.name: "list-add"
            display: PlasmaComponents.AbstractButton.IconOnly
            enabled: root.paired && !root.bureauEnCours
            onClicked: root.nouveauCarre()
            PlasmaComponents.ToolTip.text: i18n("New note on the desktop")
            PlasmaComponents.ToolTip.visible: hovered
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
        }

        PlasmaComponents.ToolButton {
            objectName: "boutonRanger"
            icon.name: "view-grid"
            display: PlasmaComponents.AbstractButton.IconOnly
            enabled: !root.bureauEnCours
            onClicked: root.rangerLesCarres()
            PlasmaComponents.ToolTip.text: i18n("Tidy desktop notes")
            PlasmaComponents.ToolTip.visible: hovered
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
        }
    }

    /* ── L'écriture rapide ───────────────────────────────────────────────── */
    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing

        PlasmaComponents.TextArea {
            id: composer
            objectName: "composer"
            Layout.fillWidth: true
            /* Grandit avec ce qu'on tape, jusqu'au tiers de la liste, puis
             * défile : une note fait une ligne, parfois dix. */
            Layout.maximumHeight: liste.height / 3
            placeholderText: i18n("Write a note…")
            wrapMode: TextEdit.Wrap

            /* Ctrl+Entrée enregistre, Entrée fait un retour à la ligne.
             * L'inverse perdrait la moitié d'une pensée chaque fois que
             * quelqu'un appuie sur Entrée par habitude. */
            Keys.onPressed: function (event) {
                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                        && (event.modifiers & Qt.ControlModifier)) {
                    liste.ecrire();
                    event.accepted = true;
                }
            }
        }

        PlasmaComponents.Button {
            icon.name: "document-save"
            display: PlasmaComponents.AbstractButton.IconOnly
            enabled: composer.text.trim().length > 0
            Layout.alignment: Qt.AlignBottom
            onClicked: liste.ecrire()
            PlasmaComponents.ToolTip.text: i18n("Save (Ctrl+Return)")
            PlasmaComponents.ToolTip.visible: hovered
            PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
        }
    }

    /* ── Ce qui ne va pas, quand quelque chose ne va pas ─────────────────── */
    PlasmaComponents.Label {
        Layout.fillWidth: true
        visible: text.length > 0
        wrapMode: Text.Wrap
        font: Kirigami.Theme.smallFont
        color: Kirigami.Theme.negativeTextColor
        text: root.lastError
    }

    PlasmaComponents.Label {
        Layout.fillWidth: true
        visible: root.offline
        wrapMode: Text.Wrap
        font: Kirigami.Theme.smallFont
        color: Kirigami.Theme.neutralTextColor
        text: root.outbox.length > 0
            ? i18np("Offline. %1 note will be sent when Symbifox answers again.",
                    "Offline. %1 notes will be sent when Symbifox answers again.",
                    root.outbox.length)
            : i18n("Offline. Showing the last notes received.")
    }


    /* ── Ce que le serveur refuse obstinément ─────────────────────────────
     *
     * 🔴 Ces notes ne partiront plus seules, et leur TEXTE n'était visible nulle
     * part : il n'existait que dans une chaîne JSON de la configuration. On les
     * montre, et on offre de réessayer — sinon la garde qui empêche de fermer
     * un carré devient un piège dont on ne sort qu'en éditant un fichier. */
    ColumnLayout {
        Layout.fillWidth: true
        visible: root.notesBloquees.length > 0
        spacing: Kirigami.Units.smallSpacing

        PlasmaComponents.Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.negativeTextColor
            text: i18np("Symbifox refused this note. It will not be sent on its own.",
                        "Symbifox refused %1 notes. They will not be sent on their own.",
                        root.notesBloquees.length)
        }

        Repeater {
            model: root.notesBloquees

            delegate: PlasmaComponents.Label {
                required property var modelData
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                /* Sélectionnable : c'est le seul endroit où ces mots existent
                 * encore, et la personne doit pouvoir les reprendre. */
                text: "« " + (modelData.text || "") + " »"
            }
        }

        PlasmaComponents.Button {
            text: i18n("Try again")
            icon.name: "view-refresh"
            Layout.alignment: Qt.AlignRight
            onClicked: root.reessayerLesBloquees()
        }
    }

    /* ── Relier une note à une fiche ─────────────────────────────────────
     *
     * Ouvert pour UNE note à la fois, et son état vit dans la racine
     * (`root.ciblesPourNote`, `root.rechercheCible`) : ce qu'on tape ici ne
     * doit pas mourir avec un délégué détruit et recréé au premier changement
     * venu d'ailleurs — c'est ce qui faisait disparaître le texte des notes
     * sous les doigts avant que ce détour n'existe. */
    ColumnLayout {
        Layout.fillWidth: true
        visible: root.ciblesPourNote !== 0
        spacing: Kirigami.Units.smallSpacing

        PlasmaComponents.Label {
            Layout.fillWidth: true
            elide: Text.ElideRight
            font: Kirigami.Theme.smallFont
            opacity: 0.7
            text: {
                var n = root.noteParId(root.ciblesPourNote);
                return n ? i18n("Link « %1 » to:", n.title || i18n("Untitled")) : "";
            }
        }

        PlasmaComponents.TextField {
            objectName: "champCible"
            Layout.fillWidth: true
            placeholderText: i18n("Search a task, a meeting, an invoice…")
            text: root.rechercheCible
            /* ⚠️ `onEditingFinished` et non `onTextChanged` : un aller-retour
             * par frappe pour une liste de cinq résultats n'a pas de sens, et
             * le serveur refuse de toute façon sous deux caractères. */
            onEditingFinished: {
                root.rechercheCible = text;
                root.chercheCibles(root.ciblesPourNote, text);
            }
        }

        PlasmaComponents.Label {
            visible: !root.ciblesEnVol && root.rechercheCible.length >= 2
                     && root.ciblesOffertes.length === 0
            text: i18n("Nothing found.")
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }

        Repeater {
            model: root.ciblesOffertes

            delegate: PlasmaComponents.Button {
                required property var modelData
                Layout.fillWidth: true
                flat: true
                /* L'étiquette du modèle sur CHAQUE ligne : le widget est trop
                 * étroit pour des groupes repliables, et sans elle on ne sait
                 * pas si « 27250 » est une tâche ou une facture. */
                text: modelData.model_label + " · " + modelData.name
                      + (modelData.closed ? " " + i18n("(closed)") : "")
                onClicked: root.renvoyer(root.noteParId(root.ciblesPourNote),
                                         modelData.model, modelData.id)
            }
        }
    }

    /* ── Les tuiles ──────────────────────────────────────────────────────── */
    PlasmaComponents.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true

        ListView {
            id: vue
            /* 🔴 Un nom, parce qu'un essai ne peut plus reconnaître cette vue
             * à sa forme : un menu de Plasma porte LUI AUSSI une vue de liste
             * avec `count` et `delegate`, et le premier venu gagnait. */
            objectName: "listeDesNotes"
            model: liste.notesVues
            spacing: Kirigami.Units.smallSpacing
            clip: true
            /* Le recyclage est coupé : ces délégués portent du texte en cours
             * et un menu ouvert, et un délégué recyclé emporterait l'un sur
             * l'autre. */
            reuseItems: false

            /* ⚠️ Rien n'est passé au délégué : `modelData` et `index` sont
             * déclarés REQUIS dans NoteCard, et c'est la vue qui les lui
             * fournit. Les nommer d'ici lève « ReferenceError » et la liste
             * reste vide. */
            delegate: NoteCard {
                width: vue.width
            }

            PlasmaExtras.PlaceholderMessage {
                anchors.centerIn: parent
                width: parent.width - Kirigami.Units.gridUnit * 2
                visible: vue.count === 0
                iconName: liste.filtre.length > 0 ? "search" : "note"
                text: liste.filtre.length > 0
                    ? i18n("No note matches") : i18n("No notes yet")
                explanation: liste.filtre.length > 0
                    ? i18n("Only the notes on this desktop are searched.")
                    : i18n("Write one above. It shows up in Symbifox right away.")
            }
        }
    }

    function ecrire() {
        root.addNote(composer.text);
        composer.text = "";
    }

    /* Une frappe, et on écrit : à l'ouverture, le curseur est déjà dans la
     * note. Sans cela il faut viser le champ à la souris, ce qui annule
     * l'intérêt du raccourci clavier que Plasma sait attribuer au widget. */
    function focusComposer() {
        if (root.paired && composer.visible) {
            composer.forceActiveFocus();
        }
    }

    Component.onCompleted: liste.focusComposer()

    Connections {
        target: root
        function onExpandedChanged() {
            if (root.expanded) {
                liste.focusComposer();
            }
        }
    }
}
