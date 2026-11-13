/*
 * Une note DANS LA LISTE : une tuile de sa propre couleur, son titre, un
 * aperçu. C'est la Notes list des Pense-bêtes de Windows.
 *
 * 🔴 On n'écrit PLUS ici. Cliquer une tuile ouvre la note sur le bureau, et
 * c'est là qu'on tape — exactement comme Windows ouvre sa fenêtre. L'éditeur
 * qu'il y avait dans cette carte était la raison pour laquelle la liste avait
 * l'air d'« un rectangle avec les notes dedans » : une carte qui s'ouvre et se
 * referme n'est pas un pense-bête, c'est un accordéon.
 *
 * ⚠️ `editable` vient du SERVEUR, pas d'une supposition d'ici : une note qui
 * porte un tableau ou un lien se montre, elle ne se mutile pas.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

import "../code/couleurs.js" as Couleurs
import "../code/papier.js" as Papier

ColumnLayout {
    id: card

    /* 🔴 `modelData` et `index` doivent être DÉCLARÉS requis pour être reçus.
     * En Qt 6, un délégué défini dans son propre fichier ne les voit pas par
     * magie : la vue ne les fournit qu'aux propriétés requises qu'il annonce.
     * Sans cela, « modelData is not defined » dans le fichier de la VUE, et la
     * liste reste vide. Éprouvé sous qmltestrunner. */
    required property var modelData
    required property int index
    readonly property var note: card.modelData

    /* Le menu de cette tuile, nommé : c'est par lui que passent désormais tous
     * les gestes, donc c'est par lui que les essais les atteignent. */
    property alias menuDeLaNote: menu
    property alias menuDesProjets: projets

    readonly property bool mine: card.note.mine !== false
    readonly property bool canEdit: card.mine && card.note.editable !== false

    /* Le libellé du groupe, quand cette tuile en ouvre un. Calculé ICI, à
     * partir de la liste complète, parce que c'est ici qu'on connaît son
     * rang — `index` n'existe pas dans le fichier de la vue. */
    readonly property string sectionLabel: {
        if (card.mine) {
            return "";
        }
        if (card.index === 0) {
            return i18n("Shared with you");
        }
        var precedente = root.notes[card.index - 1];
        return (precedente && precedente.mine !== false)
            ? i18n("Shared with you") : "";
    }

    /* ── Les couleurs, mesurées (voir papier.js) ─────────────────────────── */
    readonly property string couleur: Papier.papier(card.note)
    readonly property bool colore: card.couleur.length > 0
    readonly property color papier: card.colore
        ? card.couleur : Kirigami.Theme.alternateBackgroundColor
    readonly property string encreHex: card.colore
        ? Couleurs.texteLisible(card.couleur, card.note.text_color) : ""
    readonly property color trait: card.colore
        ? Papier.trait(card.couleur, card.encreHex)
        : Qt.alpha(Kirigami.Theme.textColor, 0.15)
    /* ⚠️ Sans couleur de note, le fond est celui du THÈME — mais `text_color`
     * vient quand même du serveur, et rien ne le mesurait contre ce fond-là :
     * c'était la seule branche du lot où une couleur du serveur était peinte
     * sans mesure. Le carré, lui, faisait déjà le bon geste. */
    readonly property color encre: card.colore
        ? card.encreHex
        : Couleurs.texteLisible(String(Kirigami.Theme.alternateBackgroundColor),
                                card.note.text_color)

    spacing: 0

    /* Le titre du groupe, quand cette note en ouvre un. */
    PlasmaComponents.Label {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.largeSpacing
        Layout.bottomMargin: Kirigami.Units.smallSpacing
        visible: card.sectionLabel.length > 0
        text: card.sectionLabel
        font: Kirigami.Theme.smallFont
        opacity: 0.7
    }

    Rectangle {
        id: tuile
        Layout.fillWidth: true
        implicitHeight: contenu.implicitHeight + Kirigami.Units.smallSpacing * 2
        radius: Kirigami.Units.cornerRadius
        color: card.papier
        border.width: 1
        border.color: card.trait

        /* Le survol se voit : sans cela, rien ne dit qu'une tuile se clique. */
        HoverHandler { id: survol }
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: Couleurs.texteSur(String(card.papier))
            opacity: survol.hovered ? 0.07 : 0
            Behavior on opacity { NumberAnimation { duration: 80 } }
        }

        /* Ouvrir la note sur le bureau. ⚠️ Posé SOUS les contrôles (`z: -1`) :
         * le menu et ses boutons prennent leurs propres clics. */
        MouseArea {
            anchors.fill: parent
            z: -1
            acceptedButtons: Qt.LeftButton
            onClicked: root.ouvrirLaNote(card.note.id)
        }

        RowLayout {
            id: contenu
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    Kirigami.Icon {
                        source: "pin"
                        /* ⚠️ `!!` : une charge où le champ manque rend
                         * `undefined`, et QML se contente d'un avertissement —
                         * du bruit qui masque les vrais messages. */
                        visible: !!card.note.pinned
                        color: card.encre
                        implicitWidth: Kirigami.Units.iconSizes.small
                        implicitHeight: Kirigami.Units.iconSizes.small
                    }

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        text: card.note.title || i18n("Untitled")
                        elide: Text.ElideRight
                        font.bold: true
                        color: card.encre
                    }

                    /* « Hors Gen » : montré parce que cela change ce que les
                     * passes font de cette note. La BASCULE est dans le menu —
                     * une icône qui ne paraît que lorsque le drapeau est posé
                     * ne peut pas servir à le retirer. */
                    Kirigami.Icon {
                        source: "view-hidden"
                        visible: !!card.note.gen_excluded
                        color: card.encre
                        opacity: 0.6
                        implicitWidth: Kirigami.Units.iconSizes.small
                        implicitHeight: Kirigami.Units.iconSizes.small

                        HoverHandler { id: survolHorsGen }
                        PlasmaComponents.ToolTip.text: i18n("Not for Gen")
                        PlasmaComponents.ToolTip.visible: survolHorsGen.hovered
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                    }

                    /* 🔴 TOUJOURS visible, jamais seulement au survol. Le
                     * 2026-11-03, cinq gestes neufs sont restés invisibles
                     * parce qu'ils vivaient dans une rangée qui ne paraissait
                     * qu'une fois la carte dépliée : « je ne vois pas de
                     * nouveaux boutons ». Un geste qu'on ne voit pas n'existe
                     * pas. */
                    PlasmaComponents.ToolButton {
                        icon.name: "overflow-menu"
                        icon.color: card.encre
                        flat: true
                        display: PlasmaComponents.AbstractButton.IconOnly
                        implicitWidth: Math.round(Kirigami.Units.gridUnit * 1.5)
                        implicitHeight: Math.round(Kirigami.Units.gridUnit * 1.4)
                        onClicked: menu.ouvrePour(card.note)
                        PlasmaComponents.ToolTip.text: i18n("Note options")
                        PlasmaComponents.ToolTip.visible: hovered
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                    }
                }

                /* L'aperçu. Le texte est déjà brut — le serveur l'a aplati —
                 * donc aucune lecture de mise en forme n'a lieu ici. */
                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    text: card.note.text || ""
                    textFormat: Text.PlainText
                    maximumLineCount: 3
                    wrapMode: Text.Wrap
                    elide: Text.ElideRight
                    opacity: 0.85
                    color: card.encre
                }

                MetaNote {
                    Layout.fillWidth: true
                    note: card.note
                    encre: card.encre
                }
            }
        }
    }

    /* ⚠️ Le parent visuel est NOMMÉ. Un menu déclaré nu dans une disposition
     * n'en a pas au moment où il se construit, et Plasma évalue alors
     * `parent.width` dans son propre délégué : « TypeError: Cannot read
     * property 'width' of null », une fois par carte. La version précédente
     * n'avait pas ce bruit parce que ses menus vivaient DANS un bouton. */
    MenuNote {
        id: menu
        parent: tuile
        compact: false
        onDemandeTache: function (n) { projets.demande(n); }
    }

    MenuProjets { id: projets; parent: tuile }
}
