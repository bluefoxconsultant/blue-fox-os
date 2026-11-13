/*
 * Ce qu'une note porte en plus de son texte : l'échéance, les pièces jointes,
 * les étiquettes. Extrait de NoteCard pour que le carré et la liste le montrent
 * de la même façon — deux surfaces qui divergeraient sur l'échéance seraient
 * deux vérités.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

import "../code/couleurs.js" as Couleurs
import "../code/papier.js" as Papier

RowLayout {
    id: meta

    property var note: null
    /* L'encre du papier sur lequel cette rangée est posée : dans un carré, le
     * fond est une pastel, donc le gris de thème y serait illisible. */
    property color encre: Kirigami.Theme.textColor

    /* 🔴 NOMMÉE plutôt qu'écrite dans le `visible` de la rangée : `visible` est
     * EFFECTIF en QML — un enfant d'un parent non rendu est invisible quoi
     * qu'on déclare — donc aucun essai ne peut distinguer « masqué parce que
     * vide » de « masqué parce que le parent l'est ». Une mutation l'a montré
     * en survivant. */
    readonly property bool aDesMeta: !!meta.note && (
        !!meta.note.deadline
        || (!!meta.note.tags && meta.note.tags.length > 0)
        || meta.note.attachments > 0)

    /* L'échéance est-elle passée ? ⚠️ En CHAÎNES, jamais en dates construites :
     * `new Date("2026-11-03")` est interprétée en UTC puis rendue dans le
     * fuseau du poste, donc une échéance du jour paraissait dépassée dès la
     * veille au soir à l'ouest de Greenwich — et Olivier est dans le fuseau le
     * plus à l'est, là où l'erreur se voit. La note porte un JOUR ; on compare
     * donc des jours. */
    readonly property bool enRetard: {
        if (!meta.note || !meta.note.deadline) {
            return false;
        }
        var d = new Date();
        var aujourdhui = d.getFullYear() + "-"
            + ("0" + (d.getMonth() + 1)).slice(-2) + "-"
            + ("0" + d.getDate()).slice(-2);
        return String(meta.note.deadline) < aujourdhui;
    }

    visible: meta.aDesMeta
    spacing: Kirigami.Units.smallSpacing

    PlasmaComponents.Label {
        visible: !!meta.note && !!meta.note.deadline
        text: meta.note ? (meta.note.deadline || "") : ""
        font: Kirigami.Theme.smallFont
        color: meta.enRetard ? Kirigami.Theme.negativeTextColor
                             : Qt.alpha(meta.encre, 0.7)
    }

    Kirigami.Icon {
        source: "mail-attachment"
        visible: !!meta.note && meta.note.attachments > 0
        color: meta.encre
        opacity: 0.6
        implicitWidth: Kirigami.Units.iconSizes.small
        implicitHeight: Kirigami.Units.iconSizes.small
    }

    PlasmaComponents.Label {
        visible: !!meta.note && meta.note.attachments > 0
        /* ⚠️ `String()` et un contrôle : une charge où `attachments` manque
         * rend `undefined`, et QML crie « Unable to assign [undefined] to
         * QString » à chaque réévaluation. Relevé dans le JOURNAL du bureau
         * d'Olivier le 2026-11-08, pas dans un harnais — le bouchon de note
         * porte toujours le champ, le serveur pas forcément. */
        text: (meta.note && meta.note.attachments) ? String(meta.note.attachments) : ""
        font: Kirigami.Theme.smallFont
        color: Qt.alpha(meta.encre, 0.7)
    }

    /* Les étiquettes, avec LEUR couleur quand le locataire leur en a donné
     * une — même règle que la note : la couleur vient du serveur, jamais d'un
     * hexadécimal en dur. Et le texte se mesure contre elle. */
    Repeater {
        model: (meta.note && meta.note.tags) || []

        delegate: Rectangle {
            required property var modelData
            radius: height / 2
            implicitWidth: nom.implicitWidth + Kirigami.Units.smallSpacing * 2
            implicitHeight: nom.implicitHeight + Kirigami.Units.smallSpacing
            /* ⚠️ `Papier.valide` : c'était le seul hexadécimal du serveur
             * affecté à un `color` sans contrôle. Une valeur difforme donne un
             * avertissement QML et une étiquette d'une couleur arbitraire. */
            readonly property bool peinte: Papier.valide(modelData.color_hex)
            color: peinte ? modelData.color_hex : Qt.alpha(meta.encre, 0.12)

            PlasmaComponents.Label {
                id: nom
                anchors.centerIn: parent
                text: modelData.name || ""
                font: Kirigami.Theme.smallFont
                color: parent.peinte
                    ? Couleurs.texteLisible(modelData.color_hex, modelData.text_color)
                    : meta.encre
            }
        }
    }

    Item { Layout.fillWidth: true }
}
