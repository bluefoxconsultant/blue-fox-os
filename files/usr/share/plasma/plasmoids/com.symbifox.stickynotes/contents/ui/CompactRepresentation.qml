/*
 * The panel icon. It carries one piece of information beyond « click me »:
 * whether the widget is actually talking to Symbifox.
 */
import QtQuick

import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami

MouseArea {
    id: compact

    /* Plasma sizes a compact representation from its implicit size; without
     * these it collapses to nothing in a vertical panel. */
    implicitWidth: Kirigami.Units.iconSizes.medium
    implicitHeight: Kirigami.Units.iconSizes.medium

    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: i18n("Sticky Notes")
    Accessible.description: root.paired
        ? i18np("%1 note", "%1 notes", root.notes.length)
        : i18n("Not paired yet")

    onClicked: root.expanded = !root.expanded
    Keys.onReturnPressed: root.expanded = !root.expanded
    Keys.onSpacePressed: root.expanded = !root.expanded

    Kirigami.Icon {
        id: icon
        anchors.fill: parent
        source: "note"
        active: compact.containsMouse || compact.activeFocus
    }

    /* A small badge rather than a number: on a 22px panel icon a count is
     * unreadable, and the only thing worth seeing at that size is « this is
     * not showing you current notes ». */
    Rectangle {
        visible: root.paired && root.offline
        width: Math.max(6, Math.round(parent.width / 4))
        height: width
        radius: width / 2
        color: Kirigami.Theme.neutralTextColor
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        border.width: 1
        border.color: Kirigami.Theme.backgroundColor
    }

    Rectangle {
        visible: !root.paired
        width: Math.max(6, Math.round(parent.width / 4))
        height: width
        radius: width / 2
        color: Kirigami.Theme.negativeTextColor
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        border.width: 1
        border.color: Kirigami.Theme.backgroundColor
    }
}
