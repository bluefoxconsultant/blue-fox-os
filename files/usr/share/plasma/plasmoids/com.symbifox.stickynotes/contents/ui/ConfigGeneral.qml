/*
 * The widget's settings: which Symbifox, and whether colleagues' shared notes
 * belong on this desktop.
 *
 * Pairing is NOT here. It belongs on the widget itself, where somebody who
 * just added it will look, and a « re-pair » button buried in a settings
 * dialog is a button nobody finds.
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kcmutils as KCM
import org.kde.kirigami as Kirigami

KCM.SimpleKCM {
    property alias cfg_server: server.text
    property alias cfg_showShared: showShared.checked

    Kirigami.FormLayout {

        QQC2.TextField {
            id: server
            Kirigami.FormData.label: i18n("Symbifox address:")
            placeholderText: "https://…"
            /* Changer l'adresse invalide le jeton : il a été émis par l'ancien
             * locataire et ne veut rien dire pour le nouveau. Le dire vaut
             * mieux que de laisser quelqu'un avec un widget qui répond 401.
             *
             * ⚠️ On compare à la valeur d'OUVERTURE, gardée ici. `plasmoid`
             * en minuscule n'existe plus en Plasma 6, et une page de réglages
             * ne reçoit de toute façon que ses `cfg_*` : s'y référer donnait
             * un ReferenceError à chaque frappe, et l'avertissement ne
             * paraissait jamais. */
            property string adresseDOuverture: ""
            Component.onCompleted: adresseDOuverture = server.text
            onTextChanged: hint.visible = (text !== server.adresseDOuverture)
        }

        QQC2.Label {
            id: hint
            visible: false
            Kirigami.FormData.label: ""
            wrapMode: Text.Wrap
            Layout.maximumWidth: Kirigami.Units.gridUnit * 20
            font: Kirigami.Theme.smallFont
            text: i18n("Changing the address means pairing this widget again.")
        }

        QQC2.CheckBox {
            id: showShared
            Kirigami.FormData.label: i18n("Shared notes:")
            text: i18n("Also show notes colleagues shared with me")
        }
    }
}
