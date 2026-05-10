/*
 * SPDX-FileCopyrightText: 2026 Blue Fox Consultant Inc.
 * SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
 *
 * Page Plasma Welcome extra-page Blue Fox OS — point d'entrée vers le wizard
 * bluefox-welcome (PyQt6) qui configure Nextcloud + Vaultwarden + Brave Sync
 * + KAccounts. Lance bluefox-welcome via Controller.runCommand depuis un
 * bouton, puis l'utilisateur poursuit Plasma Welcome après config.
 *
 * Format documenté upstream : https://invent.kde.org/plasma/plasma-welcome
 * § « Extending Welcome Center with custom pages ». Doit hériter de
 * Kirigami.Page (ou GenericPage/ScrollablePage). On utilise GenericPage pour
 * cohérence visuelle avec les pages standard de Plasma Welcome.
 *
 * Discovery : posé dans /usr/share/plasma/plasma-welcome/extra-pages/ et
 * scanné au lancement (préfixe NN- pour ordre alphabétique).
 *
 * v0.1.1 — page minimale qui délègue au wizard PyQt6 existant.
 * v0.1.2 (#22424 followup) — migration des 5 pages PyQt6 en pages QML
 * natives qui appelleraient les apply_* helpers via runCommand.
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts

import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard

import org.kde.plasma.welcome as Welcome

Welcome.GenericPage {
    id: root

    heading: i18nc("@title", "Configurer votre identité Blue Fox")
    description: i18nc("@info:usagetip",
        "Le wizard Blue Fox configure votre compte Authentik (mail, calendrier, " +
        "contacts via Nextcloud), votre Vaultwarden, votre Brave Sync, et monte " +
        "votre Nextcloud Files. Ce sera complété en quelques minutes ; vous pouvez " +
        "aussi le relancer plus tard depuis le menu d'applications.")

    Kirigami.Icon {
        Layout.alignment: Qt.AlignHCenter
        Layout.preferredWidth: Kirigami.Units.gridUnit * 6
        Layout.preferredHeight: Kirigami.Units.gridUnit * 6
        source: "/usr/share/bluefox/branding/logo.png"
        fallback: "preferences-desktop-personal"
    }

    QQC2.Button {
        Layout.alignment: Qt.AlignHCenter
        Layout.topMargin: Kirigami.Units.largeSpacing
        text: i18nc("@action:button", "Lancer la configuration Blue Fox")
        icon.name: "system-run"
        highlighted: true
        // Lance le wizard PyQt6 en arrière-plan. Plasma Welcome reste actif
        // pendant que le wizard tourne ; l'utilisateur revient à Plasma
        // Welcome après finalize. Le `--service-mode` skip le wizard si
        // /var/lib/bluefox-welcome/done existe déjà.
        onClicked: Controller.runCommand("bluefox-welcome")
    }

    QQC2.Label {
        Layout.alignment: Qt.AlignHCenter
        Layout.topMargin: Kirigami.Units.smallSpacing
        text: i18nc("@info:tooltip",
            "Ouvre une nouvelle fenêtre. Revenez ici après avoir terminé.")
        opacity: 0.7
        font.italic: true
    }
}
