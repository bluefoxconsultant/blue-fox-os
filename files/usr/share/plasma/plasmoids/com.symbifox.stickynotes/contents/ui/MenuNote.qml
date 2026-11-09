/*
 * Le menu « ⋯ » d'une note : la couleur, puis les gestes que le serveur sait
 * faire. UN seul fichier pour le carré et pour la liste — deux copies auraient
 * divergé au premier geste ajouté, et c'est déjà arrivé dans cette tâche.
 *
 * ⚠️ Les gestes sont ceux du serveur, pas une invention du bureau : `pin`,
 * `unpin`, `activity` (quatre délais et pas un de plus, c'est ce que la route
 * accepte), `gen_exclude`, `gen_include`, `archive`. Offrir autre chose, ce
 * serait offrir un échec.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

import "../code/couleurs.js" as Couleurs

PlasmaComponents.Menu {
    id: menu

    property var note: null
    /* Dans un carré : pas de « poser sur le bureau » (on y est), et pas de
     * recherche de fiche (il faudrait un champ, et la place manque). */
    property bool compact: false

    readonly property bool mine: !!menu.note && menu.note.mine !== false
    readonly property bool canEdit: menu.mine && !!menu.note
                                    && menu.note.editable !== false

    /* L'hôte branche ceci sur son propre menu de projets. */
    signal demandeTache(var note)

    /* 🔴 Supprimer se demande DEUX FOIS. Le fichier du carré promettait une
     * confirmation que rien n'implémentait (relevé par la relecture adverse du
     * 11-08) : un clic de travers dans un menu archivait la note. Un menu se
     * ferme au clic, donc la confirmation est le MÊME élément, relibellé, et le
     * menu se rouvre. */
    property bool confirmeSuppression: false

    function ouvrePour(n) {
        menu.note = n;
        menu.confirmeSuppression = false;
        menu.popup();
    }

    /* ── Les couleurs ────────────────────────────────────────────────────── */

    /* 🔴 La palette vient du serveur (`/prefs`), jamais d'ici. Les sept pastels
     * de Windows écrites en dur seraient la marque de personne, et elles
     * partiraient en base dès le premier choix — visibles ensuite sur le
     * mobile et sur le web d'un locataire qui ne les a jamais choisies. */
    Row {
        spacing: Kirigami.Units.smallSpacing
        leftPadding: Kirigami.Units.smallSpacing
        rightPadding: Kirigami.Units.smallSpacing
        topPadding: Kirigami.Units.smallSpacing
        bottomPadding: Kirigami.Units.smallSpacing
        visible: menu.canEdit && root.nuancier.length > 0

        Repeater {
            model: menu.canEdit ? root.nuancier : []

            delegate: Rectangle {
                /* ⚠️ Un `id`, et pas `parent` : la pastille est l'enfant d'un
                 * `Row`, donc `parent.faible` désignait le Row et rendait
                 * `undefined` — une infobulle vide et un avertissement QML. */
                id: pastille
                required property var modelData
                width: Kirigami.Units.gridUnit
                height: Kirigami.Units.gridUnit
                radius: width / 2
                color: modelData.hex
                /* Celle qui est posée se reconnaît : sans ce trait, on ne sait
                 * pas quelle couleur la note porte déjà. */
                border.width: menu.note && menu.note.color_hex
                              && String(menu.note.color_hex).toUpperCase()
                                 === String(modelData.hex).toUpperCase() ? 3 : 1
                border.color: Couleurs.texteSur(modelData.hex)

                /* 🔴 `paireFaible` était déclarée, éprouvée… et appelée de
                 * NULLE PART : une couleur de locataire où ni le noir ni le
                 * blanc n'atteignent la norme se peignait sous la norme sans un
                 * mot, l'inverse de ce que son commentaire promettait. On ne
                 * l'interdit pas — ce n'est pas notre couleur — on la SIGNALE,
                 * et le choix reste à la personne. */
                readonly property bool faible: Couleurs.paireFaible(modelData.hex)

                /* ⚠️ Le libellé est calculé ICI et l'affectation attachée tient
                 * sur UNE ligne : le harnais retire les lignes
                 * `PlasmaComponents.ToolTip.*` une à une, et un ternaire sur
                 * trois lignes laisse ses deux dernières orphelines —
                 * « Unexpected token ? », fichier entier indisponible. C'est la
                 * quatrième fois que cette règle se paie dans cette tâche. */
                readonly property string infobulle: pastille.faible
                    ? i18n("%1 — no text colour reaches the readability standard on it",
                           modelData.name || modelData.hex)
                    : (modelData.name || modelData.hex)

                Kirigami.Icon {
                    anchors.centerIn: parent
                    visible: pastille.faible
                    source: "dialog-warning"
                    color: Couleurs.texteSur(modelData.hex)
                    implicitWidth: Math.round(Kirigami.Units.iconSizes.small * 0.75)
                    implicitHeight: Math.round(Kirigami.Units.iconSizes.small * 0.75)
                }

                TapHandler {
                    onTapped: {
                        root.choisirCouleur(menu.note, modelData.hex);
                        menu.close();
                    }
                }

                PlasmaComponents.ToolTip.text: pastille.infobulle
                PlasmaComponents.ToolTip.visible: survol.hovered
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                HoverHandler { id: survol }
            }
        }
    }

    /* Retirer la couleur : la note redevient celle du thème, partout. */
    PlasmaComponents.MenuItem {
        text: i18n("No colour")
        enabled: menu.canEdit && !!menu.note && !!menu.note.color_hex
        visible: menu.canEdit
        onTriggered: root.choisirCouleur(menu.note, "")
    }

    /* ── Les gestes ──────────────────────────────────────────────────────── */

    /* 🔴 `enabled` AUTANT que `visible` : masquer n'est pas protéger — un
     * signal atteint un élément invisible, et un essai l'a montré. */
    PlasmaComponents.MenuItem {
        text: menu.note && menu.note.pinned ? i18n("Unpin") : i18n("Pin")
        visible: menu.canEdit
        enabled: menu.canEdit
        onTriggered: root.runAction(menu.note, menu.note.pinned ? "unpin" : "pin", {})
    }

    PlasmaComponents.MenuItem {
        text: menu.note && menu.note.gen_excluded
            ? i18n("Let Gen read it") : i18n("Hide from Gen")
        visible: menu.canEdit
        enabled: menu.canEdit
        onTriggered: root.runAction(
            menu.note, menu.note.gen_excluded ? "gen_include" : "gen_exclude", {})
    }

    /* Le rappel. Le serveur n'accepte QUE ces quatre délais et refuse le
     * reste : le menu les reprend tels quels plutôt que d'offrir un choix qui
     * serait rejeté. À plat plutôt qu'en sous-menu — un sous-menu dans un carré
     * de 288 pixels sort de l'écran. */
    PlasmaComponents.MenuItem {
        text: i18n("Remind me today")
        visible: menu.canEdit
        enabled: menu.canEdit
        onTriggered: root.runAction(menu.note, "activity", { days: 0 })
    }
    PlasmaComponents.MenuItem {
        text: i18n("Remind me tomorrow")
        visible: menu.canEdit
        enabled: menu.canEdit
        onTriggered: root.runAction(menu.note, "activity", { days: 1 })
    }
    PlasmaComponents.MenuItem {
        text: i18n("Remind me in two days")
        visible: menu.canEdit
        enabled: menu.canEdit
        onTriggered: root.runAction(menu.note, "activity", { days: 2 })
    }
    PlasmaComponents.MenuItem {
        text: i18n("Remind me in a week")
        visible: menu.canEdit
        enabled: menu.canEdit
        onTriggered: root.runAction(menu.note, "activity", { days: 7 })
    }

    /* « En faire une tâche ». Le menu des projets est un VOISIN, pas un
     * sous-menu : voir MenuProjets.qml, qui dit pourquoi. */
    PlasmaComponents.MenuItem {
        text: i18n("Make a task")
        visible: menu.canEdit
        enabled: menu.canEdit && !root.projetsEnVol
        onTriggered: menu.demandeTache(menu.note)
    }

    /* « Relier à une fiche ». Un PANNEAU, pas un menu : le serveur refuse une
     * recherche de moins de deux caractères, donc il faut un champ — et un
     * champ ne se met pas dans un menu. Le panneau vit dans la liste ; cet
     * élément ne fait que le demander.
     *
     * ⚠️ Absent d'un carré : la place manque, et c'est dit plutôt que masqué
     * en silence. */
    PlasmaComponents.MenuItem {
        text: i18n("Link to a record")
        visible: menu.canEdit && !menu.compact
        enabled: menu.canEdit && !menu.compact
        onTriggered: {
            if (root.ciblesPourNote === menu.note.id) {
                root.ciblesPourNote = 0;
                root.ciblesOffertes = [];
            } else {
                root.ciblesPourNote = menu.note.id;
                root.ciblesOffertes = [];
                root.rechercheCible = "";
            }
        }
    }

    /* 🔴 `enabled` AUTANT que `visible`, pour ces deux-là comme pour les
     * autres : `visible` est EFFECTIF en QML, donc un essai ne peut pas
     * distinguer « masqué parce qu'il n'a pas sa place » de « masqué parce que
     * le menu est fermé ». La règle qui compte se lit sur `enabled`. */
    PlasmaComponents.MenuItem {
        text: i18n("Put on desktop")
        visible: !menu.compact
        enabled: !menu.compact && !root.bureauEnCours
        onTriggered: root.poserSurLeBureau(menu.note.id)
    }

    PlasmaComponents.MenuItem {
        text: i18n("All my notes")
        visible: menu.compact
        enabled: menu.compact
        onTriggered: root.unpinNote()
    }

    PlasmaComponents.MenuItem {
        text: i18n("Open in Symbifox")
        /* ⚠️ `url` peut manquer d'une charge : sans ce contrôle, on ouvrait
         * « …/undefined » dans le navigateur de la personne. */
        enabled: !!menu.note && !!menu.note.url
        onTriggered: Qt.openUrlExternally(root.server + menu.note.url)
    }

    /* Supprimer, c'est archiver côté serveur : la note quitte le bureau et le
     * téléphone, elle ne quitte pas Symbifox. Le libellé dit ce que la personne
     * croit faire, l'infobulle dit ce qui arrive vraiment. */
    PlasmaComponents.MenuItem {
        objectName: "entreeSupprimer"
        text: menu.confirmeSuppression
            ? i18n("Really delete this note?") : i18n("Delete note")
        visible: menu.mine
        enabled: menu.mine
        onTriggered: {
            if (!menu.confirmeSuppression) {
                menu.confirmeSuppression = true;
                menu.popup();
                return;
            }
            menu.confirmeSuppression = false;
            var note = menu.note;
            /* 🔴 Le carré ne se ferme QU'APRÈS la réponse du serveur. Les deux
             * gestes étaient dans le même souffle : l'archivage est asynchrone,
             * donc le carré disparaissait d'abord, et hors ligne le message
             * d'échec se peignait sur une surface détruite. Carré fermé, note
             * intacte, personne de prévenu. */
            root.runAction(note, "archive", {}, function () {
                if (menu.compact) {
                    root.fermerLeCarre();
                }
            });
        }
    }
}
