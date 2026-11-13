/*
 * L'écran d'appairage : où est ton Symbifox, et une fois seulement.
 *
 * Sorti de FullRepresentation.qml le 2026-11-08, quand celle-ci est devenue un
 * aiguillage entre la liste et le carré. Rien n'a changé dans ce qui suit : ces
 * lignes portent quatre défauts trouvés en conditions réelles, et les déplacer
 * ne les rejoue pas.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

import "../code/couleurs.js" as Couleurs

Item {
    id: ecran

    /* L'accent vient du serveur (`res.company.report_brand_primary`), jamais du
     * code : chez un client Symbifox, un bleu Blue Fox codé en dur serait la
     * marque de quelqu'un d'autre. Vide = le thème de la personne.
     *
     * 🔴 Pas d'auto-référence dans la surcharge : lire
     * `Kirigami.Theme.highlightColor` dans sa propre définition ferait une
     * boucle de liaison, et Qt la casse en silence. */
    readonly property color accent:
        root.brandAccent.length > 0 ? root.brandAccent : Kirigami.Theme.highlightColor
    readonly property bool branded: root.brandAccent.length > 0

    PlasmaExtras.PlaceholderMessage {
        id: pairing
        anchors.centerIn: parent
        width: parent.width - Kirigami.Units.gridUnit * 4
        visible: !root.paired
        iconName: "network-connect"
        text: i18n("Connect to Symbifox")
        explanation: root.pairing
            ? i18n("Approve the pairing in your browser, then come back here.")
            : i18n("Your notes live in Symbifox. Tell this widget where, and pair it once.")

        /* 🔴 The address is ASKED FOR here, not assumed. It used to default to
         * one tenant's domain and live only in the settings page: clicking
         * « pair » then failed against somebody else's Symbifox, with nothing
         * on screen to say so. Pre-filled when the image seeded it. */
        PlasmaComponents.TextField {
            id: adresse
            visible: !root.pairing
            Layout.fillWidth: true
            text: root.server
            placeholderText: i18n("symbifox.mon-organisation.com")
            inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoAutoUppercase
            onAccepted: if (text.trim().length > 0) root.startPairing(text)
            /* 🔴 Sonder dès qu'on quitte le champ, avant tout clic : la
             * personne voit à qui elle parle, et le widget apprend l'adresse
             * canonique de l'instance. Toute la classe de pannes du 2026-11-02
             * — un widget pointé sur un locataire sans le module, sans moyen
             * de le savoir avant d'avoir essayé — se referme ici.
             *
             * `onEditingFinished` et non `onTextChanged` : un aller-retour à
             * chaque touche frappée. */
            onEditingFinished: if (text.trim().length > 0) root.probeServerIdentity(text)
        }

        /* Qui répond. Vert : c'est une confirmation, pas un avertissement. */
        PlasmaComponents.Label {
            visible: !root.pairing && root.tenantName.length > 0
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            color: Kirigami.Theme.positiveTextColor
            text: i18n("✓ %1", root.tenantName)
        }

        PlasmaComponents.BusyIndicator {
            visible: root.probingServer
            running: visible
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: Kirigami.Units.gridUnit * 1.5
            implicitHeight: Kirigami.Units.gridUnit * 1.5
        }

        /* L'adresse tapée n'est pas celle que l'instance annonce. Un fait, pas
         * une erreur — mais un fait qui doit être lu, sinon la bascule a l'air
         * d'une erreur de serveur. */
        PlasmaComponents.Label {
            visible: !root.pairing
                && (root.aliasNotice.length > 0
                    || (root.tenantName.length > 0 && root.serverIsAlias))
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.neutralTextColor
            text: root.aliasNotice.length > 0
                ? root.aliasNotice
                : i18n("This Symbifox answers as %1. That is the address this widget will use.",
                       root.canonicalUrl)
        }

        PlasmaComponents.Label {
            visible: !root.pairing
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            opacity: 0.7
            text: i18n("The same address you use in your browser.")
        }

        /* Où le jeton va atterrir. Dit avant l'appairage, pas après : c'est au
         * moment de décider que cela compte. */
        PlasmaComponents.Label {
            visible: !root.pairing && root.walletChecked && !root.walletReady
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.neutralTextColor
            text: i18n("This workstation has no wallet, so the token will be kept in this widget's settings, in plain text in your profile.")
        }

        /* Le passage au portefeuille a coûté un appairage : le dire une fois,
         * sinon le widget a l'air de s'être dépairé tout seul. */
        PlasmaComponents.Label {
            visible: !root.pairing && root.walletMigrated
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.neutralTextColor
            text: i18n("This widget now keeps its token in KWallet instead of your profile. Pair it once more, and all your note widgets will share that one pairing.")
        }

        /* 🔴 Le nombre à RETROUVER parmi les trois que la page propose. Sans
         * lui ici, la concordance de nombre ne vaut rien : elle n'existait que
         * côté serveur, et il n'y avait rien à comparer. C'est la garde contre
         * le hameçonnage inverse — quelqu'un qui ouvre un appairage et vous
         * envoie le lien — et elle se joue entièrement sur ces deux chiffres. */
        PlasmaComponents.Label {
            visible: root.pairing && root.matchNumber.length > 0
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: root.matchNumber
            /* ⚠️ Pas `Kirigami.Theme.defaultFont.pointSize * 2.2` : `pointSize`
             * vaut -1 dès qu'un thème définit sa police en pixels, et la
             * taille demandée serait négative. `gridUnit` suit la police du
             * thème et ne peut pas valoir -1. */
            font.pixelSize: Math.round(Kirigami.Units.gridUnit * 1.7)
            font.bold: true
        }

        PlasmaComponents.Label {
            visible: root.pairing && root.matchNumber.length > 0
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            text: i18n("Click this number on the page that just opened. If it is not offered, close the page: the pairing is not yours.")
        }

        PlasmaComponents.BusyIndicator {
            visible: root.pairing
            running: visible
            Layout.alignment: Qt.AlignHCenter
        }

        PlasmaComponents.Button {
            visible: !root.pairing
            text: i18n("Pair this widget")
            icon.name: "network-connect"
            enabled: adresse.text.trim().length > 0
            Layout.alignment: Qt.AlignHCenter
            onClicked: root.startPairing(adresse.text)
            /* L'action principale porte l'accent du locataire. En Plasma 6 un
             * bouton se peint depuis `Kirigami.Theme`, d'où la surcharge
             * plutôt qu'un rectangle posé dessous : le bouton garde son relief,
             * ses états et sa police. */
            Kirigami.Theme.inherit: false
            Kirigami.Theme.highlightColor: ecran.accent
            /* 🔴 Et la couleur du TEXTE, mesurée.
             *
             * Poser `highlightColor` seul laisse Plasma peindre le libellé avec
             * `highlightedTextColor`, qui est BLANC. Sur l'accent de Blue Fox,
             * un bleu clair, cela rend environ 2,3:1 — Olivier l'a dit le
             * 2026-11-03 (« le blanc sur bleu est trop dur à lire ») et le canon
             * de marque l'interdisait déjà. Choisir un fond sans choisir son
             * texte, c'est laisser le contraste à la charge de personne.
             *
             * `Couleurs.texteSur` mesure et rend l'anthracite ou le blanc : sur
             * un accent foncé, c'est bien le blanc qui gagne. */
            /* ⚠️ Sur UNE ligne : le harnais retire les affectations
             * `Kirigami.Theme.<prop>:` d'une copie par un `sed` ligne à ligne,
             * et une affectation coupée en deux laissait sa continuation
             * orpheline — « Non-existent attached object », 91 essais tombés
             * d'un coup. */
            Kirigami.Theme.highlightedTextColor: Couleurs.texteSur(ecran.accent) || Kirigami.Theme.textColor
            highlighted: ecran.branded
        }

        /* 🔴 Without this, every pairing failure was invisible: the only label
         * bound to lastError sat inside the « paired » block, which is hidden
         * until there is a token. Clicking « pair » against a tenant without
         * the module blinked a spinner and went back to the button, with no
         * word anywhere. Found on 2026-11-01, first run in a real Plasma. */
        PlasmaComponents.Label {
            visible: root.lastError.length > 0
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.negativeTextColor
            text: root.lastError
        }

        /* 🔴 Dire ce qui ne va pas ne suffit pas : il faut dire quoi faire.
         * Le champ au-dessus est prérempli avec l'adresse configurée — qui
         * peut venir d'une version précédente du widget ou de l'image BFOS —
         * donc on reclique « Pair » sans la relire, et le même échec revient.
         * Relevé le 2026-11-02, sur un widget resté pointé sur la production
         * d'un locataire qui n'a pas encore le module. */
        PlasmaComponents.Label {
            visible: !root.pairing && root.lastError.length > 0 && root.addressAtFault
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            font: Kirigami.Theme.smallFont
            text: i18n("Correct the address above, then pair again.")
        }

        PlasmaComponents.Button {
            visible: root.pairing
            text: i18n("Cancel")
            flat: true
            Layout.alignment: Qt.AlignHCenter
            onClicked: root.cancelPairing()
        }

        /* The browser may not have opened — a Live session with no default
         * browser, a locked-down profile. The address is always reachable by
         * hand, and it is not a secret to the person it belongs to. */
        PlasmaComponents.Button {
            visible: root.pairing && root.pairingUrl
            text: i18n("Open the page again")
            flat: true
            Layout.alignment: Qt.AlignHCenter
            onClicked: Qt.openUrlExternally(root.pairingUrl)
        }
    }
}
