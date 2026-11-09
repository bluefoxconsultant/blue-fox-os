/*
 * Le menu des projets de « en faire une tâche ».
 *
 * 🔴 Il vit À CÔTÉ du menu « ⋯ » et non DEDANS, et ce n'est pas un détail de
 * rangement : un menu déclaré dans un menu devient un SOUS-MENU, et Plasma
 * fabrique alors son élément avec son propre délégué, qui lit `parent.width`
 * sur un parent encore nul — une TypeError par carte, à chaque montage. Mesuré
 * le 2026-11-08 : la version précédente ne l'avait pas, parce que ses menus
 * vivaient dans un bouton. Le bruit n'était pas le nôtre, mais c'est nous qui
 * le déclenchions.
 *
 * ⚠️ Et un sous-menu dans un carré de 288 pixels sortirait de l'écran de toute
 * façon.
 */
import QtQuick

import org.kde.plasma.components as PlasmaComponents

PlasmaComponents.Menu {
    id: projets

    property var note: null

    /* La liste vient d'un aller-retour : le menu s'ouvre APRÈS la réponse,
     * sinon on verrait un menu vide puis des lignes qui apparaissent. */
    function demande(n) {
        projets.note = n;
        root.demandeProjets(n.id);
    }

    Repeater {
        model: projets.note && root.projetsPourNote === projets.note.id
            ? root.projetsOfferts : []

        delegate: PlasmaComponents.MenuItem {
            required property var modelData
            /* Le client quand il y en a un : deux projets peuvent porter le
             * même nom chez deux clients. */
            text: modelData.client ? modelData.name + " — " + modelData.client
                                   : modelData.name
            onTriggered: root.enFaireUneTache(projets.note, modelData.id)
        }
    }

    /* 🔴 SEULEMENT pour la note qui a demandé : sans ce second test, tous les
     * menus ouverts s'ouvriraient d'un coup. */
    Connections {
        target: root
        function onProjetsOffertsChanged() {
            if (projets.note && root.projetsPourNote === projets.note.id
                    && root.projetsOfferts.length > 0) {
                projets.popup();
            }
        }
    }
}
