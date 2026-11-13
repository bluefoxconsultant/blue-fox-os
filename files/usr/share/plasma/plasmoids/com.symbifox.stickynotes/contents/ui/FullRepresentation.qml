/*
 * L'aiguillage. Trois surfaces, et une seule à la fois :
 *
 *   pas apparié  → EcranAppairage : où est ton Symbifox
 *   un carré     → StickyNote : UNE note, l'allure des Pense-bêtes de Windows
 *   sinon        → NotesList : toutes les notes, comme la Notes list
 *
 * 🔴 Ce fichier ne dessine plus rien lui-même. Il portait à la fois l'écran
 * d'appairage, le champ d'écriture et la liste, et c'est ce qui faisait qu'un
 * carré « une note » ressemblait à la liste en plus petit : même composeur,
 * même ListView, même cadre. Un pense-bête n'est pas une liste d'un élément.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

PlasmaComponents.Page {
    id: page

    /* 🔴 Un carré doit pouvoir devenir PETIT. Le minimum d'avant (16 × 18
     * unités) interdisait un pense-bête de la taille d'un pense-bête, et
     * Plasma refuse en silence une taille sous le minimum : on tirait le coin
     * et rien ne bougeait. Chaque carré étant un applet distinct, il garde
     * ensuite SA taille, d'un démarrage à l'autre. */
    Layout.minimumWidth: root.pinnedMode
        ? Kirigami.Units.gridUnit * 7 : Kirigami.Units.gridUnit * 13
    Layout.minimumHeight: root.pinnedMode
        ? Kirigami.Units.gridUnit * 5 : Kirigami.Units.gridUnit * 14
    Layout.preferredWidth: root.pinnedMode
        ? Kirigami.Units.gridUnit * 14 : Kirigami.Units.gridUnit * 20
    Layout.preferredHeight: root.pinnedMode
        ? Kirigami.Units.gridUnit * 16 : Kirigami.Units.gridUnit * 24

    /* Le papier du carré va bord à bord : aucune marge de page au-dessus. */
    padding: 0

    /* 🔴 Des LOADERS, et pas trois surfaces masquées.
     *
     * Masquer ne suffit pas : une surface instanciée EXISTE. Le carré masqué
     * portait son menu « ⋯ » sans note, donc une entrée « Pin » que personne ne
     * peut voir — et qu'un essai trouvait, puisqu'un essai cherche dans
     * l'arbre et non à l'écran. C'est la même famille que « masquer n'est pas
     * protéger » : ce qui n'a pas à être là ne doit pas être construit.
     *
     * Et c'est gratuit par ailleurs : trois notes sur le bureau font trois
     * widgets, chacun n'ayant à construire QUE sa surface. */
    Loader {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing
        active: !root.paired
        sourceComponent: EcranAppairage { }
    }

    /* ── Le carré ────────────────────────────────────────────────────────── */
    Loader {
        anchors.fill: parent
        active: root.paired && root.pinnedMode
        sourceComponent: StickyNote { }
    }

    /* ── La liste ────────────────────────────────────────────────────────── */
    Loader {
        anchors.fill: parent
        active: root.paired && !root.pinnedMode
        sourceComponent: Item {

        /* Le liseré de marque : deux pixels en haut, au-dessus de tout le
         * reste. C'est ce qui se voit sans rien repeindre, et il reste lisible
         * dans les deux thèmes puisqu'il ne porte aucun texte.
         *
         * ⚠️ Pas sur le carré : là, c'est la couleur de la note qui parle, et
         * un liseré de marque par-dessus une pastel ferait deux marques. */
        Rectangle {
            id: lisere
            z: 10
            visible: root.brandAccent.length > 0
            color: root.brandAccent.length > 0
                ? root.brandAccent : Kirigami.Theme.highlightColor
            height: Math.max(2, Math.round(Kirigami.Units.smallSpacing / 2))
            anchors { top: parent.top; left: parent.left; right: parent.right }
        }

        NotesList {
            anchors.fill: parent
            anchors.topMargin: lisere.visible
                ? lisere.height + Kirigami.Units.smallSpacing
                : Kirigami.Units.smallSpacing
            anchors.leftMargin: Kirigami.Units.smallSpacing
            anchors.rightMargin: Kirigami.Units.smallSpacing
            anchors.bottomMargin: Kirigami.Units.smallSpacing
        }
        }
    }
}
