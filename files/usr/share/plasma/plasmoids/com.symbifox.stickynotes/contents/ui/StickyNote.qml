/*
 * UN pense-bête : un carré, une note, l'allure de l'application de Windows.
 *
 * Ce que cette surface emprunte aux Pense-bêtes de Windows, et pourquoi :
 *   — le papier occupe TOUT le carré, sans fond de widget ni cadre de thème ;
 *     c'est ce qui distingue un pense-bête d'une carte posée dans un panneau,
 *     et c'est exactement ce qui manquait (« je ne vois qu'un rectangle avec
 *     les notes dedans », 2026-11-08) ;
 *   — un bandeau de la même couleur en plus foncé, avec « + » à gauche,
 *     « ⋯ » et « × » à droite ;
 *   — on tape DIRECTEMENT dans le corps, et ça s'enregistre tout seul ;
 *   — « × » ferme le carré sans toucher la note ; c'est « Supprimer » du menu
 *     qui supprime, et lui demande confirmation.
 *
 * 🔴 Ce que Plasma ne permet pas, et qui n'est donc pas promis ici : déplacer
 * un carré depuis son propre contenu. Un applet ne choisit pas sa géométrie
 * (mesuré le 2026-11-03) — c'est le bureau qui déplace et redimensionne, par
 * son propre geste. Chaque carré étant un applet distinct, il garde SA taille.
 */
import QtQuick
import QtQuick.Layouts

import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

import "../code/couleurs.js" as Couleurs
import "../code/papier.js" as Papier
import "../code/riche.js" as Riche

Item {
    id: carre

    /* En mode carré, le cache ne porte qu'une note : c'est `probeOne` qui le
     * remplit, pas le cycle complet. */
    property alias menuDeLaNote: menu
    property alias menuDesProjets: projets

    readonly property var note: root.notes.length > 0 ? root.notes[0] : null
    readonly property bool mine: !!carre.note && carre.note.mine !== false
    /* Un carré NEUF n'a pas encore de note : il s'écrit quand même, et c'est
     * sa première frappe qui crée la note. Sans cela, le « + » de Windows
     * poserait un carré où l'on ne peut rien taper. */
    /* #27250, lot « mise en forme ».
     *
     * Trois états, pas deux. `editable` est le contrat du TÉLÉPHONE (une note
     * mise en forme y est affichée, pas modifiable) et il ne change pas ;
     * `rich_editable` dit si la BARRE peut renvoyer le corps sans rien perdre.
     * Une note mise en forme devient donc modifiable ICI sans le devenir là —
     * c'est tout l'objet du lot.
     *
     * ⚠️ `=== true` et non `!== false` : la clé n'existe pas sur un serveur
     * d'avant 18.0.4.5.0, et `undefined !== false` serait vrai. Le widget
     * aurait alors offert la barre contre un serveur qui ne sait pas la lire. */
    readonly property bool richeModifiable: root.hasRichText
        && !!carre.note && carre.note.rich_editable === true
    readonly property bool canEdit: root.carreNeuf
        || (carre.mine && !!carre.note
            && (carre.note.editable !== false || carre.richeModifiable))
    /* La barre paraît quand le serveur la porte ET que cette note se laisse
     * renvoyer. Sur un carré NEUF, la note n'existe pas encore : c'est le
     * serveur seul qui décide. */
    readonly property bool riche: root.hasRichText
        && (root.carreNeuf || carre.richeModifiable)

    /* 🔴 LE MODE DU CHAMP SUIT LE BROUILLON, et non la capacité.
     *
     * `riche` dit ce que le serveur et la note permettent MAINTENANT. Le champ,
     * lui, doit rester dans le mode où la personne a tapé — les deux divergent,
     * et les deux sens ont été mesurés :
     *
     *   — `hasRichText` est faux jusqu'à la réponse du premier `/ping`, et on
     *     peut taper avant (fenêtre courte en ligne, large au retour d'un hors
     *     ligne ou d'un redémarrage de plasmashell). Mesuré : le brouillon
     *     `a < b et <b>pas gras</b>`, relu en mode riche, devient
     *     `a pas gras` — « < b et » avalé comme une balise, « pas gras » passé
     *     en gras. Du texte mangé ET un effet ajouté, sans un mot ;
     *   — dans l'autre sens, un brouillon riche relu en texte simple affiche
     *     `<!DOCTYPE HTML PUBLIC …` à l'écran, puis la pause l'envoie dans la
     *     clé `text` : le source HTML de Qt DEVIENT le contenu de la note, et
     *     on le voit au téléphone.
     *
     * Un brouillon d'un mode ne doit donc jamais être relu dans l'autre. Tant
     * qu'il y en a un, c'est LUI qui dit le mode du champ ; la capacité ne
     * reprend la main qu'une fois le brouillon parti. */
    readonly property var modeDuBrouillon: root.brouillonRiche(corps.cle)
    readonly property bool champRiche: carre.modeDuBrouillon === undefined
        ? carre.riche : carre.modeDuBrouillon === true

    /* ── Les couleurs, toutes mesurées ───────────────────────────────────── */

    /* 🔴 Sans couleur venue du serveur, on prend celle du THÈME et non un jaune
     * inventé : le mobile et le web montrent la même note sans couleur, et une
     * couleur née ici partirait en base au premier enregistrement. Ce qui fait
     * le pense-bête alors, c'est la forme — bandeau, coins, ombre. */
    readonly property color papier: {
        var p = Papier.papier(carre.note);
        return p.length > 0 ? p : Kirigami.Theme.backgroundColor;
    }
    readonly property bool colore: Papier.papier(carre.note).length > 0
    /* 🔴 L'ENCRE D'ABORD, puis le bandeau CONTRE ELLE.
     *
     * `bandeau()` sans argument mesure contre `texteSur(papier)` — le meilleur
     * des deux extrêmes — alors que ce qui est peint est `texteLisible(papier,
     * text_color)`, c'est-à-dire la couleur du SERVEUR quand elle atteint la
     * norme. Les deux diffèrent, et le commentaire d'avant affirmait le
     * contraire. Pire cas mesuré par la relecture adverse : papier `#EEEEAA`,
     * `text_color` `#776666` → 4,50:1 sur le papier, 3,60:1 sur le bandeau,
     * qui porte « + », « ⋯ » et « × ». */
    readonly property string encreHex: carre.colore
        ? Couleurs.texteLisible(Papier.papier(carre.note),
                                carre.note ? carre.note.text_color : null)
        : ""
    readonly property color encre: carre.colore
        ? carre.encreHex : Kirigami.Theme.textColor
    readonly property color bandeau: carre.colore
        ? Papier.bandeauLisible(Papier.papier(carre.note), carre.encreHex)
        : Kirigami.Theme.alternateBackgroundColor
    readonly property color trait: carre.colore
        ? Papier.trait(Papier.papier(carre.note), carre.encreHex)
        : Qt.alpha(Kirigami.Theme.textColor, 0.15)

    /* ── L'enregistrement automatique ────────────────────────────────────── */

    /* Le geste de Windows : on tape, ça part. Pas de bouton « enregistrer »,
     * pas de carte à replier.
     *
     * ⚠️ Après une PAUSE, jamais à chaque touche : une requête par frappe
     * noierait le serveur et perdrait la course contre la passe suivante. Et
     * `enTrainDEcrire` ralentit déjà la cadence pendant ce temps-là.
     *
     * 🔴 Le texte en cours vit dans la RACINE (`brouillons`), pas ici : cette
     * surface est détruite et recréée au moindre changement du modèle, et le
     * texte tapé disparaissait sous les doigts avant que ce détour n'existe. */
    property int pauseAvantEnvoi: 1200
    property bool modifie: false

    /* 🔴 VRAI PENDANT QU'UNE BASCULE ÉCRIT, et ça rend le clic linéaire.
     *
     * `basculeEffet` réécrit la sélection plage par plage, et chaque plage
     * change `corps.text`. Tout ce qui écoutait le champ repassait donc une
     * fois par plage : la sonde ré-analysait TOUT le document, `onTextChanged`
     * reposait le brouillon, et la barre relisait l'état des quatre boutons.
     * Mesuré sur charizard : 400 plages / 2 690 caractères coûtaient 1 737 ms
     * avec la sonde contre 611 ms sans, et 200 plages 438 ms contre 158 — la
     * sonde payait les deux tiers du prix, et le prix lui-même est
     * QUADRATIQUE. Ça tourne dans le fil principal de plasmashell : panneau,
     * barre des tâches et tous les widgets figés pendant ce temps-là.
     *
     * Pendant une bascule, le champ n'a donc aucun auditeur, et la bascule
     * range le brouillon elle-même à la fin. */
    property bool enBascule: false

    /* 🔴 VRAI PENDANT QUE `chargeLeChamp` ÉCRIT, et CHARGER N'EST PAS TAPER.
     *
     * Mesuré au harnais : quand le mode du champ change — la note cesse d'être
     * renvoyable, le locataire est rétrogradé — `chargeLeChamp` repose le
     * texte, `onTextChanged` prenait ça pour une frappe et posait un brouillon
     * que personne n'avait tapé. Ce brouillon changeait `modeDuBrouillon`, donc
     * `champRiche`, donc `textFormat`, donc rappelait `chargeLeChamp` : Qt
     * criait « Binding loop detected for property "modeDuBrouillon" », et la
     * note repartait au serveur sans que personne n'ait touché à rien.
     *
     * ⚠️ C'est la même famille que la boucle du constat 1, prise par l'autre
     * bout : là une liaison réécrivait le champ, ici une écriture du champ
     * réveillait la liaison. Le harnais refuse maintenant toute « Binding
     * loop » (voir `tests/lancer-qml-natif.sh`), parce que c'est le seul
     * signal qui les nomme. */
    property bool enChargement: false

    Timer {
        id: minuteurEnvoi
        interval: carre.pauseAvantEnvoi
        onTriggered: carre.enregistre()
    }

    function enregistre() {
        if (!carre.canEdit || !carre.modifie) {
            return;
        }
        /* Le carré neuf : sa première phrase CRÉE la note. Hors ligne, elle
         * part en file comme n'importe quelle création — c'est une création,
         * pas une modification, et la file les porte. */
        if (root.carreNeuf) {
            var neuf = root.noteBrouillon(root.pinnedNoteId);
            if (neuf === undefined || neuf === null || !neuf.trim()) {
                return;
            }
            /* 🔴 Le VIDE ne se lit pas sur le document d'un éditeur riche.
             * Mesuré le 2026-11-13 : un `TextEdit` riche vide rend un document
             * qui porte quand même un paragraphe (`-qt-paragraph-type:empty`)
             * et un `<br />`, donc `.trim()` n'est JAMAIS vide et le premier
             * clic dans un carré neuf aurait créé une note vide. Le texte NU de
             * Qt, lui, est vide (`length === 0`). */
            if (carre.champRiche && corps.length === 0) {
                return;
            }
            /* ⚠️ Le plafond d'écriture vaut AUSSI à la création. Il n'était
             * vérifié que pour une modification : une note neuve trop grosse
             * partait en file, le serveur la refusait, et après cinq essais
             * elle s'y bloquait — du texte que le carré annonce comme parti et
             * qui ne partira jamais. */
            var charge = carre.champRiche ? Riche.pourLeServeur(neuf) : neuf;
            if (Riche.tropGros(charge)) {
                root.diteEcriture(i18n("This note is too large to save from the desktop. Edit it in Symbifox."));
                return;
            }
            carre.modifie = false;
            root.creerDansCeCarre(charge, carre.champRiche);
            return;
        }
        if (!carre.note) {
            return;
        }
        var texte = root.noteBrouillon(carre.note.id);
        if (texte === undefined || texte === null) {
            return;
        }
        /* 🔴 LE BROUILLON MORT, en mode riche : il ne se reconnaît pas par une
         * comparaison de chaînes avec le corps du serveur.
         *
         * Qt ne rend pas le HTML qu'on lui donne : il le renormalise (le gras
         * en `font-weight:700`, des marges partout). Comparer son document au
         * corps canonique du serveur aurait toujours dit « modifiée », donc :
         * un enregistrement à chaque pause de frappe même sans frappe, le
         * « × » du bandeau qui refuse de fermer POUR TOUJOURS avec un message
         * faux, et le carré gelé à la cadence rapide. Ce sont les trois portes
         * du défaut B-A du 2026-11-08, revenues par une autre route.
         *
         * 🔴 Et le témoin ne les fermait PAS toutes : il ne juge que ce qui se
         * passe ICI, à l'enregistrement. La LIAISON de `text` rouvrait les
         * trois en grand, par un chemin que ce témoin ne voit pas — elle
         * reposait le corps canonique dans un champ qui portait la
         * sérialisation de Qt, donc à chaque effacement de brouillon. Le
         * commentaire d'avant disait les trois portes fermées ; elles l'étaient
         * d'un seul côté. Voir le chargement du champ, plus bas.
         *
         * Le témoin (`temoinRiche`) fait passer le corps du SERVEUR par le
         * même Qt : les deux côtés sortent alors du même outil, et la
         * comparaison porte sur ce qu'on ENVERRAIT, pas sur l'enveloppe —
         * mesuré, deux `TextEdit` de polices différentes rendent le même
         * corps. Elle porte sur la CHARGE (`memeEnvoi`) et non sur les deux
         * documents bruts : sur un thème en petites capitales, Qt écrit dans
         * le champ un `font-variant` que le widget retire avant d'envoyer, et
         * comparer les documents bruts déclarait alors la note modifiée à
         * chaque pause de frappe. */
        if (carre.champRiche) {
            /* 🔴 Le brouillon est riche et la mise en forme n'est plus
             * permise : le locataire a été rétrogradé, ou la note porte
             * maintenant quelque chose que la barre ne sait pas renvoyer. On
             * REFUSE et on garde le brouillon. L'envoyer dans la clé `text`
             * mettrait le source HTML de Qt dans la note — le serveur
             * l'accepte sur une note restée simple (c'est alors au téléphone
             * qu'on le découvre) et rend 400 sur une note mise en forme, le
             * texte perdu dans les deux cas. */
            if (!carre.riche) {
                /* 🔴 `diteEcriture` et non `lastError` : ce chemin GARDE le
                 * brouillon, donc le texte ne vit plus qu'en mémoire jusqu'au
                 * prochain redémarrage de plasmashell. C'est précisément le
                 * message qu'il ne faut pas perdre, et il n'était peint sur
                 * aucune surface qu'un carré monte. */
                root.diteEcriture(i18n("Not saved: this note can no longer be formatted here. Copy your text into Symbifox."));
                return;
            }
            if (Riche.memeEnvoi(texte, temoinRiche.text)) {
                carre.modifie = false;
                root.poseBrouillon(carre.note.id, null);
                return;
            }
            if (Riche.tropGros(Riche.pourLeServeur(texte))) {
                root.diteEcriture(i18n("This note is too large to save from the desktop. Edit it in Symbifox."));
                return;
            }
            if (root.offline) {
                root.refuseEcritureHorsLigne();
                return;
            }
            carre.modifie = false;
            root.saveNote(carre.note, undefined, undefined,
                          Riche.pourLeServeur(texte));
            return;
        }
        if (texte === (carre.note.text || "")) {
            carre.modifie = false;
            /* 🔴 ET ON L'EFFACE. Un brouillon identique au texte de la note est
             * un brouillon mort : il n'y a rien à enregistrer. Le laisser
             * laissait `enTrainDEcrire` vrai POUR TOUJOURS — donc le « × » du
             * bandeau refusait de fermer avec un message faux (« it will be
             * sent in a moment » pour une note déjà enregistrée), et le carré
             * restait à la cadence rapide, vingt requêtes la minute, ce que
             * `PINNED_MS` existe précisément pour empêcher. C'est le chemin le
             * plus courant de cette version : « + », on tape, on s'arrête. */
            root.poseBrouillon(carre.note.id, null);
            return;
        }
        /* Hors ligne, une MODIFICATION n'est pas mise en file (la v1 l'assume)
         * — mais elle doit le DIRE, sinon le texte ne vit plus qu'en mémoire. */
        if (root.offline) {
            root.refuseEcritureHorsLigne();
            return;
        }
        carre.modifie = false;
        root.saveNote(carre.note, texte);
    }

    /* Partir en emportant ce qui n'est pas encore enregistré. Sans cela, fermer
     * le carré ou changer de bureau pendant la pause perdait la dernière
     * phrase tapée. */
    Component.onDestruction: carre.enregistre()

    /* ── Lire la police d'un caractère ───────────────────────────────────
     *
     * `riche.js` ne connaît aucun type de Qt : c'est ici que la police se lit,
     * un caractère à la fois, et là-bas qu'on en tire les plages.
     *
     * 🔴 LIRE DÉPLACE LA SÉLECTION, et il n'y a pas d'autre façon de lire en
     * QML pur. Mesuré sur charizard : après un `select(2, 3)` suivi du
     * `select(5, 8)` qui restitue la sélection, le curseur a changé de BOUT —
     * une extension à la souris ou au Maj+flèche part alors du mauvais côté et
     * la sélection s'inverse. D'où deux lecteurs :
     *   — à la bascule, on lit le CHAMP (c'est là qu'on va écrire) et on
     *     restitue le sens : mesuré, `select(fin, debut)` repose le curseur au
     *     début, et l'extension suivante repart du bon bout ;
     *   — pour l'état des boutons, on lit la SONDE. Une liaison qui déplacerait
     *     la sélection dont elle dépend se réévaluerait sans fin, et le champ
     *     de la personne n'a pas à être remué vingt fois par mouvement de
     *     souris.
     */
    /* ⚠️ Le CARACTÈRE part avec la police, et ce n'est pas du décor : c'est la
     * seule façon pour `riche.js` de distinguer de l'encre d'un séparateur de
     * ligne vide. Mesuré sur charizard sur « a / (ligne vide) / b » dont toute
     * l'encre est grasse : `0:B---/u61  1:----/u2029  2:----/u2028
     * 3:B---/u2029  4:B---/u62` — le paragraphe vide ne porte AUCUN effet,
     * donc les boutons s'inversaient et le premier clic AJOUTAIT l'effet. */
    function descripteur(selection, texte) {
        if (!selection || !selection.font) {
            return null;
        }
        var f = selection.font;
        return { family: f.family, pointSize: f.pointSize,
                 pixelSize: f.pixelSize, weight: f.weight,
                 bold: f.bold, italic: f.italic,
                 underline: f.underline, strikeout: f.strikeout,
                 texte: texte };
    }

    function litPolice(i) {
        corps.select(i, i + 1);
        return carre.descripteur(corps.cursorSelection, corps.getText(i, i + 1));
    }

    function litPoliceSonde(i) {
        sondeRiche.select(i, i + 1);
        return carre.descripteur(sondeRiche.cursorSelection,
                                 sondeRiche.getText(i, i + 1));
    }

    /* Basculer un effet sur la sélection.
     *
     * 🔴 Quatre choses dans l'ordre, et l'ordre compte :
     *   1. refuser s'il n'y a pas de sélection (voir la barre), et refuser un
     *      effet qui n'est pas des quatre ;
     *   2. décider UNE fois ce qu'on pose, pour toute la sélection : une
     *      sélection partiellement grasse devient grasse, seule une sélection
     *      toute grasse se dégrasse ;
     *   3. l'appliquer PLAGE PAR PLAGE, en mutant la police lue dans chaque
     *      plage. Une police lue sur la sélection entière est celle de son
     *      dernier caractère (mesuré) : la réécrire partout enlevait le gras
     *      d'un mot qu'on voulait seulement souligner, et l'ajoutait à l'espace
     *      qui le précède. Et une police RECOPIÉE attribut par attribut écrit
     *      dans la note la taille et le poids du thème — voir `riche.js` ;
     *   4. ranger le brouillon SOI-MÊME. `onTextChanged` ne le fait que si le
     *      champ a le focus actif, et rien ne garantit qu'il l'ait au retour
     *      d'un clic. Sans cette ligne, le gras était à l'écran et jamais
     *      enregistré — le défaut B2 du 11-08, à l'identique.
     *
     * ⚠️ Le prix d'un clic, mesuré sur charizard (Qt 6.12) : 15 ms sur une
     * sélection UNIFORME de 3 600 caractères — une plage — et, sur du texte
     * alternant un mot gras sur deux, 33 ms pour 50 plages, 110 ms pour 100,
     * 438 ms pour 200 et 1 737 ms pour 400. C'est SUPERLINÉAIRE : la même
     * mesure sans la sonde donne 11 / 38 / 158 / 611 ms, donc la sonde en
     * payait les deux tiers, et c'est elle que `enBascule` gèle. Le reste
     * (611 ms pour 400 plages) est le prix que Qt met à réécrire un document
     * une fois par plage, et il n'y a pas de façon de l'éviter en QML pur.
     */
    function basculeEffet(effet) {
        if (!carre.champRiche || !carre.canEdit) {
            return false;
        }
        if (corps.selectionEnd <= corps.selectionStart) {
            return false;
        }
        if (!Riche.estUnEffet(effet)) {
            return false;
        }
        var debut = corps.selectionStart;
        var fin = corps.selectionEnd;
        /* De quel bout vient la sélection : le curseur est à l'un des deux, et
         * c'est lui qu'une extension fera bouger. */
        var versLArriere = corps.cursorPosition === debut;
        var cible = Riche.cibleDeLaBascule(carre.litPolice, debut, fin, effet);
        var plages = Riche.plagesUniformes(carre.litPolice, debut, fin);
        if (plages.length === 0) {
            return false;
        }
        /* 🔴 ON NE PEINT QUE CE QUI PORTE DE L'ENCRE.
         *
         * Mesuré sur charizard : basculer le séparateur d'une ligne VIDE fait
         * écrire `<p><span style=" font-weight:700;"><br /></span></p>` par Qt
         * — à l'écran rien ne change, mais la note repart au serveur pour
         * rien. Et comme la ligne vide ne porte aucun effet, elle inversait
         * aussi le bouton : voir `porteDeLEncre` dans `riche.js`. */
        var aPeindre = 0;
        for (var p = 0; p < plages.length; p++) {
            if (plages[p].encre !== false) {
                aPeindre += 1;
            }
        }
        if (aPeindre === 0) {
            /* Rien que des lignes vides : il n'y a pas d'encre à mettre en
             * forme, et réécrire la note pour cela serait une modification que
             * personne n'a demandée. */
            return false;
        }
        carre.enBascule = true;
        try {
            for (var k = 0; k < plages.length; k++) {
                if (plages[k].encre === false) {
                    continue;
                }
                corps.select(plages[k].debut, plages[k].fin);
                var selection = corps.cursorSelection;
                if (!selection || !selection.font) {
                    continue;
                }
                /* 🔴 La police LUE, mutée sur le SEUL attribut demandé, puis
                 * réassignée. Ni `Qt.font({…})`, ni une recopie attribut par
                 * attribut : les deux n'emportent que ce qu'elles énumèrent, et
                 * écrivent dans la note ce qu'elles ont inventé. */
                var police = selection.font;
                if (effet === "b") { police.bold = cible; }
                else if (effet === "i") { police.italic = cible; }
                else if (effet === "u") { police.underline = cible; }
                else { police.strikeout = cible; }
                selection.font = police;
            }
            if (versLArriere) {
                corps.select(fin, debut);
            } else {
                corps.select(debut, fin);
            }
        } finally {
            /* ⚠️ Dans un `finally` : une exception qui laisserait `enBascule`
             * vrai gèlerait la barre et la sonde pour de bon. */
            carre.enBascule = false;
        }
        root.poseBrouillon(corps.cle, corps.text, true);
        carre.modifie = true;
        minuteurEnvoi.restart();
        return true;
    }

    /* ── Le témoin de la mise en forme ───────────────────────────────────── */

    /* Le corps du SERVEUR, renormalisé par le même moteur que l'éditeur.
     *
     * 🔴 Il n'affiche rien et ne doit rien afficher : `visible: false` ET une
     * taille nulle, pour qu'il ne prenne pas une ligne dans la disposition.
     * Son seul rôle est que `.text` rende ce que Qt ferait du corps canonique.
     *
     * ⚠️ Ce n'est PAS un deuxième analyseur : c'est le MÊME. Un témoin qui
     * sortirait d'un autre outil que ce qu'il surveille ne verrait rien — la
     * leçon du lot serveur, payée deux fois le 2026-11-09. */
    TextEdit {
        id: temoinRiche
        visible: false
        width: 0
        height: 0
        textFormat: TextEdit.RichText
        font: corps.font
        text: (carre.note && carre.note.html) ? carre.note.html : ""
    }

    /* La SONDE : le document du CHAMP, dans un éditeur que personne ne
     * sélectionne.
     *
     * 🔴 Elle existe parce que lire la police d'un caractère déplace la
     * sélection de l'éditeur qu'on lit. Lire le champ de la personne vingt
     * fois par mouvement de souris lui prendrait sa sélection ; le faire dans
     * une liaison la réévaluerait sans fin, puisqu'elle dépend de ce qu'elle
     * déplace.
     *
     * ⚠️ MÊME police que le champ, et c'est le contrat : mesuré sur charizard,
     * deux éditeurs de même police rendent la même longueur, la même table
     * caractère par caractère et le même corps sérialisé. Avec une autre
     * police, les positions resteraient bonnes mais le corps différerait.
     *
     * Coût mesuré : 2,5 ms pour analyser un document de 2 400 caractères, à
     * chaque frappe. Un pense-bête en porte quelques dizaines. */
    TextEdit {
        id: sondeRiche
        objectName: "sonde-riche"
        visible: false
        width: 0
        height: 0
        textFormat: TextEdit.RichText
        font: corps.font
        /* 🔴 GELÉE PENDANT UNE BASCULE, et c'est ce qui rend le clic
         * supportable. Sans cela, chaque plage réécrite changeait `corps.text`
         * et la sonde ré-analysait TOUT le document : mesuré sur charizard,
         * 400 plages coûtaient 1 737 ms avec elle contre 611 ms sans, dans le
         * fil principal de plasmashell.
         *
         * ⚠️ `carre.enBascule` est lu AVANT `corps.text`, et c'est voulu : QML
         * ne retient comme dépendances que ce qu'il a LU, donc pendant une
         * bascule cette liaison ne dépend plus du champ du tout — et elle se
         * réévalue une seule fois, au dégel. */
        text: carre.enBascule ? "" : (carre.champRiche ? corps.text : "")
    }

    /* ── Le papier ───────────────────────────────────────────────────────── */

    Kirigami.ShadowedRectangle {
        id: feuille
        anchors.fill: parent
        anchors.margins: Math.round(Kirigami.Units.smallSpacing / 2)
        color: carre.papier
        radius: Kirigami.Units.cornerRadius
        border.width: 1
        border.color: carre.trait
        shadow.size: Kirigami.Units.gridUnit
        shadow.yOffset: 2
        shadow.color: Qt.rgba(0, 0, 0, 0.35)

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            /* ── Le bandeau ──────────────────────────────────────────────── */
            Rectangle {
                id: entete
                Layout.fillWidth: true
                Layout.preferredHeight: Math.round(Kirigami.Units.gridUnit * 1.7)
                color: carre.bandeau
                /* Les coins du HAUT seulement : un rectangle arrondi sur ses
                 * quatre coins laisserait voir le papier au-dessus du bandeau. */
                radius: feuille.radius
                Rectangle {
                    anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                    height: parent.radius
                    color: parent.color
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Kirigami.Units.smallSpacing
                    anchors.rightMargin: Kirigami.Units.smallSpacing
                    spacing: 0

                    BoutonBandeau {
                        icone: "list-add"
                        infobulle: i18n("New note")
                        encre: carre.encre
                        visible: root.paired
                        onActive: root.nouveauCarre()
                    }

                    Item { Layout.fillWidth: true }

                    /* Ce qui n'est pas encore parti, dit sur le bandeau même :
                     * dans un carré il n'y a pas de place pour une phrase. */
                    Kirigami.Icon {
                        source: "cloud-upload"
                        visible: root.offline || minuteurEnvoi.running
                        opacity: 0.7
                        color: carre.encre
                        implicitWidth: Kirigami.Units.iconSizes.small
                        implicitHeight: Kirigami.Units.iconSizes.small
                        /* ⚠️ Le libellé est calculé au-dessus et l'affectation
                         * tient sur UNE ligne. Coupée en trois, ses deux
                         * dernières lignes survivaient au `sed` du harnais et
                         * se greffaient sur `implicitHeight`, qui devenait un
                         * ternaire parfaitement valide : le harnais éprouvait
                         * une autre hauteur que celle du code, sans une erreur.
                         * Un essai garde maintenant la forme (voir
                         * `test_aucune_affectation_attachee_coupee_en_deux`). */
                        readonly property string infobulle: root.offline ? i18n("Offline: this note is not saved yet.") : i18n("Saving…")
                        PlasmaComponents.ToolTip.text: infobulle
                        PlasmaComponents.ToolTip.visible: survolEnvoi.hovered
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                        HoverHandler { id: survolEnvoi }
                    }

                    BoutonBandeau {
                        id: boutonMenu
                        icone: "overflow-menu"
                        infobulle: i18n("Note options")
                        encre: carre.encre
                        visible: !!carre.note
                        onActive: menu.ouvrePour(carre.note)
                    }

                    BoutonBandeau {
                        icone: "window-close"
                        infobulle: i18n("Close this note (it stays in Symbifox)")
                        encre: carre.encre
                        onActive: root.fermerLeCarre()
                    }
                }
            }

            /* ── Le corps ────────────────────────────────────────────────── */

            /* Mien et modifiable : on tape dedans, point. */
            PlasmaComponents.TextArea {
                id: corps
                objectName: "corps"
                Layout.fillWidth: true
                Layout.fillHeight: true
                /* L'air autour du texte fait autant que la couleur : collé au
                 * bandeau et au bord, le même texte a l'air d'un champ de
                 * formulaire. Windows laisse respirer, et c'est gratuit. */
                Layout.margins: Kirigami.Units.largeSpacing
                visible: carre.canEdit
                wrapMode: TextEdit.Wrap
                /* #27250 : le MÊME champ, en deux modes. Pas deux surfaces —
                 * deux surfaces voudraient deux brouillons, deux cadences et
                 * deux chemins d'enregistrement, et c'est le genre de doublon
                 * qui divergeait au premier correctif. */
                textFormat: carre.champRiche ? TextEdit.RichText : TextEdit.PlainText
                /* 🔴 La sélection doit SURVIVRE au clic sur un bouton de la
                 * barre. Les boutons ne prennent pas le focus (`NoFocus`), donc
                 * en principe rien ne la perd ; ceci est la ceinture, et elle
                 * ne coûte rien. */
                persistentSelection: true
                color: carre.encre
                /* 🔴 Le fond du champ est retiré : un champ de saisie dessiné
                 * par-dessus le papier ferait une boîte dans la note, ce qui
                 * est précisément l'allure qu'on quitte. */
                background: null
                /* 🔴 PAS `placeholderText` : mesuré le 2026-11-08, retirer le
                 * fond du champ (ce qu'il faut faire pour qu'un pense-bête ne
                 * ressemble pas à un formulaire) EFFACE aussi le substitut de
                 * texte — l'item existe, il se déclare visible, et rien n'est
                 * peint. Un carré neuf était donc une surface parfaitement
                 * vide, sans un mot pour dire qu'on peut y écrire. On le peint
                 * donc soi-même, juste en dessous. */
                selectByMouse: true

                /* 🔴 Le brouillon est rangé sous l'identifiant du CARRÉ quand
                 * la note n'existe pas encore (donc sous le sentinelle négatif)
                 * et sous celui de la note ensuite. Deux magasins auraient
                 * perdu la phrase au moment précis de l'adoption. */
                /* ⚠️ Défensif des DEUX côtés. `carre.note.id` manque d'une
                 * charge partielle, et `Plasmoid.configuration.noteId` rend
                 * `undefined` le temps d'un `reloadConfig()` — ce qui arrive
                 * chaque fois qu'on repointe le widget. Relevé dans le JOURNAL
                 * du bureau d'Olivier le 2026-11-08, onze fois en trois
                 * minutes, après la pose en production. */
                readonly property int cle: (carre.note && carre.note.id)
                    ? carre.note.id
                    : (root.pinnedNoteId || 0)

                /* 🔴 AUCUNE LIAISON SUR `text`, et c'est le coeur du
                 * correctif du 2026-11-13.
                 *
                 * La liaison d'avant rendait `carre.note.html` — le corps
                 * CANONIQUE du serveur — alors que le champ porte la
                 * SÉRIALISATION de Qt. Les deux ne sont JAMAIS égaux : mesuré
                 * sur charizard, 38 caractères contre 657 pour la même note.
                 * Donc chaque effacement du brouillon réévaluait la liaison, et
                 * chaque réévaluation était un vrai `setText` — curseur ramené
                 * à ZÉRO, sélection perdue. `onTextChanged` reposait alors un
                 * brouillon, le minuteur repartait, 1,2 s plus tard
                 * `enregistre()` déclarait ce brouillon mort et l'effaçait, et
                 * ça recommençait. Mesuré : la boucle tournait indéfiniment, Qt
                 * criait « Binding loop detected for property "text" », la
                 * frappe suivante atterrissait en tête de note, `interacting`
                 * restait vrai (donc `FAST_MS` au lieu de `PINNED_MS`) et le
                 * « × » refusait de fermer un tour sur deux.
                 *
                 * ⚠️ En texte simple la liaison rendait exactement ce que le
                 * champ portait, donc rien ne bougeait : la boucle est née
                 * avec le mode riche, et la ligne 216 suffisait à l'amorcer,
                 * sans aucun aller-retour réseau.
                 *
                 * Le champ se CHARGE donc par une fonction, qui n'écrit que si
                 * le document affiché n'est pas déjà celui qu'on voulait
                 * poser. Les réveils sont nommés juste en dessous : il n'y en a
                 * pas d'autre, et c'est ce qui remplace la liaison. */
                function chargeLeChamp() {
                    var garde = root.noteBrouillon(corps.cle);
                    if (garde !== undefined && garde !== null) {
                        /* Le brouillon SORT déjà de Qt : il est son propre
                         * témoin. */
                        corps.poseLeTexte(garde, garde);
                        return;
                    }
                    if (!carre.note) {
                        corps.poseLeTexte("", "");
                        return;
                    }
                    /* ⚠️ En mode riche, c'est `html` qu'on charge — et `text`
                     * sinon. Charger `text` dans un champ riche afficherait le
                     * balisage que `html2plaintext` fabrique (« des *courses* »)
                     * ; charger `html` dans un champ en texte simple
                     * afficherait les balises. */
                    if (carre.champRiche) {
                        corps.poseLeTexte(carre.note.html || "", temoinRiche.text);
                        return;
                    }
                    corps.poseLeTexte(carre.note.text || "", "");
                }

                /* Écrire dans le champ SEULEMENT s'il ne porte pas déjà ça.
                 *
                 * `temoin` est ce que Qt rend de `cible` : un brouillon est son
                 * propre témoin (il sort de ce champ-ci), et le corps du
                 * serveur a `temoinRiche`. Sans ce détour il n'y a aucune façon
                 * de comparer — Qt ne rend pas le HTML qu'on lui donne. */
                function poseLeTexte(cible, temoin) {
                    if (!carre.champRiche) {
                        if (corps.text !== cible) {
                            corps.ecrit(cible);
                        }
                        return;
                    }
                    /* 🔴 Le VIDE se lit sur `length` (le texte NU) et jamais
                     * sur `text` : mesuré, le document d'un éditeur riche vide
                     * porte 622 caractères, et il les porte même sans qu'on
                     * lui ait rien assigné. */
                    if (!cible || cible.length === 0) {
                        if (corps.length !== 0) {
                            corps.ecrit("");
                        }
                        return;
                    }
                    if (!Riche.memeEnvoi(corps.text, temoin)) {
                        corps.ecrit(cible);
                    }
                }

                /* La SEULE écriture du champ qui ne vienne pas des doigts, et
                 * elle se déclare comme telle : voir `carre.enChargement`. */
                function ecrit(cible) {
                    carre.enChargement = true;
                    try {
                        corps.text = cible;
                    } finally {
                        carre.enChargement = false;
                    }
                }

                Component.onCompleted: corps.chargeLeChamp()

                /* Les réveils, et il n'y en a pas d'autre.
                 *
                 * ⚠️ Le TÉMOIN aussi : son document n'arrive qu'après le
                 * changement de note qui l'a demandé, et sans ce réveil-là le
                 * champ garderait la note d'avant. Et `textFormat` plutôt que
                 * `champRiche` : les deux changent ensemble, mais rien ne
                 * garantit l'ordre, et charger avant le changement de format
                 * prendrait la mauvaise branche. `poseLeTexte` étant
                 * idempotent, un réveil de trop ne coûte qu'une comparaison. */
                onTextFormatChanged: corps.chargeLeChamp()

                Connections {
                    target: root
                    function onBrouillonsChanged() { corps.chargeLeChamp(); }
                }

                Connections {
                    target: carre
                    function onNoteChanged() { corps.chargeLeChamp(); }
                }

                Connections {
                    target: temoinRiche
                    function onTextChanged() { corps.chargeLeChamp(); }
                }

                onTextChanged: {
                    /* ⚠️ Pas pendant une bascule : elle réécrit le document
                     * plage par plage et range le brouillon elle-même à la
                     * fin. Repasser ici une fois par plage rendait le clic
                     * quadratique, comme la sonde gelée juste à côté. */
                    if (carre.enBascule || !activeFocus) {
                        return;
                    }
                    /* ⚠️ Ni pendant un CHARGEMENT : `chargeLeChamp` repose le
                     * texte du serveur, ce qui n'est pas une frappe. Sans
                     * cette ligne, un changement de mode fabriquait un
                     * brouillon que personne n'avait tapé — et la liaison qui
                     * l'avait déclenché se réveillait elle-même. */
                    if (carre.enChargement) {
                        return;
                    }
                    /* 🔴 NI PENDANT UNE BASCULE DE MODE, et celle-là ne se
                     * voit pas avec un drapeau.
                     *
                     * Changer `textFormat` fait RÉÉCRIRE le document par Qt
                     * lui-même : de riche à simple, il repose le source HTML
                     * en texte brut. Mesuré sur charizard : cette réécriture
                     * passe ICI, et elle y passe AVANT que `textFormat` n'ait
                     * pris sa nouvelle valeur — donc hors de tout drapeau que
                     * `poseLeTexte` pourrait lever. Le brouillon posé là
                     * portait `<p>des <b>courses</b></p>` dans la clé `text`,
                     * et il réveillait la liaison qui avait déclenché la
                     * bascule : Qt criait « Binding loop detected for property
                     * "modeDuBrouillon" ».
                     *
                     * Le signe sûr est le DÉSACCORD entre le mode voulu et le
                     * format que le champ porte encore. */
                    var voulu = carre.champRiche ? TextEdit.RichText
                                                 : TextEdit.PlainText;
                    if (corps.textFormat !== voulu) {
                        return;
                    }
                    /* 🔴 EN MODE RICHE, UN DOCUMENT IDENTIQUE À CELUI DU
                     * SERVEUR N'EST PAS UN BROUILLON.
                     *
                     * Deux raisons, et les deux ont coûté un défaut. Le
                     * chargement d'abord : quand le serveur a aplati quelque
                     * chose, `chargeLeChamp` repose son corps dans le champ, ce
                     * qui passe ICI — poser un brouillon là ferait repartir le
                     * minuteur sur un texte que personne n'a tapé, et le
                     * prochain tour recommencerait. `brouillonNonEnregistre`
                     * ensuite (main.qml) : ce second juge du brouillon mort
                     * compare le brouillon à `note.text`, donc il répondait
                     * TOUJOURS « non enregistré » pour du HTML — et c'est lui
                     * qui faisait refuser le « × » avec un message faux. On ne
                     * pose donc jamais de brouillon riche qui n'a rien à dire,
                     * et les deux juges redeviennent d'accord. */
                    if (carre.champRiche && !!carre.note
                            && Riche.memeEnvoi(corps.text, temoinRiche.text)) {
                        carre.modifie = false;
                        minuteurEnvoi.stop();
                        var garde = root.noteBrouillon(corps.cle);
                        if (garde !== undefined && garde !== null) {
                            root.poseBrouillon(corps.cle, null);
                        }
                        return;
                    }
                    root.poseBrouillon(corps.cle, corps.text, carre.champRiche);
                    carre.modifie = true;
                    minuteurEnvoi.restart();
                }

                /* Quitter le champ enregistre : attendre la pause alors qu'on
                 * est déjà parti ailleurs n'a aucun intérêt. */
                onActiveFocusChanged: {
                    if (!activeFocus && carre.modifie) {
                        minuteurEnvoi.stop();
                        carre.enregistre();
                    }
                }

                /* Le substitut, peint par nous. Il reste tant que rien n'est
                 * tapé, curseur dedans ou non — comme Windows. */
                PlasmaComponents.Label {
                    /* ⚠️ `corps`, par son identifiant, et NON `parent` : un
                     * enfant déclaré dans un `TextArea` n'est pas parenté au
                     * contrôle mais à sa zone de défilement, qui n'a ni
                     * `leftPadding` ni `topPadding`. D'où « Unable to assign
                     * [undefined] to int » toutes les quinze secondes dans le
                     * journal du bureau — invisible pour le harnais bouchonné,
                     * dont le TextArea est un QQC2 nu. */
                    anchors.left: corps.left
                    anchors.top: corps.top
                    anchors.leftMargin: corps.leftPadding
                    anchors.topMargin: corps.topPadding
                    /* 🔴 `corps.length`, PAS `corps.text.length`. Mesuré le
                     * 2026-11-13 : le document d'un éditeur riche vide porte
                     * quand même un paragraphe et un `<br />` — plus de 600
                     * caractères — donc `text.length === 0` est FAUX dès que le
                     * mode riche s'allume, et le seul mot qui dit qu'on peut
                     * écrire ici disparaissait. `length` est le nombre de
                     * caractères du TEXTE, et il vaut bien zéro. */
                    visible: corps.length === 0
                    text: i18n("Write here…")
                    color: Qt.alpha(carre.encre, 0.45)
                }
            }

            /* ── La barre de mise en forme ────────────────────────────────
             *
             * Sous le texte, comme sur Windows, et seulement quand le serveur
             * la porte ET que cette note se laisse renvoyer.
             *
             * 🔴 LES BOUTONS SONT DÉSACTIVÉS SANS SÉLECTION, et ce n'est pas
             * du confort. Mesuré sur charizard le 2026-11-13 : avec le curseur
             * seul, assigner `cursorSelection.font` ne met PAS en forme ce
             * qu'on va taper — il réécrit la DERNIÈRE sélection, ailleurs dans
             * la note, et le caractère tapé ensuite sort nu. Pire, la police
             * assignée y laissait `font-family` et `font-size`, deux
             * déclarations que l'assainisseur du serveur ne sait pas traduire :
             * la note devenait « non modifiable à la barre », donc la barre se
             * verrouillait elle-même hors de la note qu'elle venait d'écrire.
             * Un bouton gris qui explique vaut mieux qu'un bouton qui abîme.
             */
            RowLayout {
                id: barre
                objectName: "barre-mise-en-forme"
                visible: carre.champRiche && carre.canEdit
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                Layout.bottomMargin: Kirigami.Units.smallSpacing
                spacing: 0

                /* La sélection du moment. ⚠️ Lue par une liaison et non au
                 * clic : c'est elle qui allume l'état enfoncé des boutons, et
                 * une fonction appelée au clic ne le réveillerait jamais. */
                readonly property bool aUneSelection:
                    corps.selectionEnd > corps.selectionStart

                /* L'état enfoncé des quatre boutons, pour TOUTE la sélection.
                 *
                 * 🔴 Un bouton enfoncé dit « tout ce qui est sélectionné le
                 * porte », donc aussi « un clic va le retirer ». Lu sur la
                 * seule police de `cursorSelection`, il mentait : « B »
                 * paraissait enfoncé sur « ␣lait » — dont l'espace n'est pas
                 * grasse — et le clic AJOUTAIT le gras, l'inverse de ce que le
                 * bouton montrait.
                 *
                 * ⚠️ Posé par une FONCTION et non par une liaison : lire la
                 * police déplace la sélection de l'éditeur lu, et une liaison
                 * qui bouge ce dont elle dépend se réévalue sans fin. On lit
                 * donc la sonde, et on repose l'état quand la sélection ou le
                 * document change. */
                property var etats: null

                function relitLesEtats() {
                    /* ⚠️ Pas pendant une bascule : la sonde est gelée (elle
                     * rend ""), et l'état se repose tout seul au dégel, quand
                     * la sonde reçoit le document final. Sans ce refus, la
                     * barre relisait les quatre effets une fois par plage. */
                    if (carre.enBascule) {
                        return;
                    }
                    barre.etats = (carre.champRiche && barre.aUneSelection
                                   && sondeRiche.length > 0)
                        ? Riche.etatsDesEffets(carre.litPoliceSonde,
                                               corps.selectionStart,
                                               corps.selectionEnd)
                        : null;
                }

                /* ⚠️ La sonde AUSSI : son document n'arrive qu'après le
                 * changement de sélection qui l'a demandé, et sans ce
                 * réveil-là les boutons resteraient relevés sur la première
                 * sélection d'un carré. */
                Connections {
                    target: corps
                    function onSelectionStartChanged() { barre.relitLesEtats(); }
                    function onSelectionEndChanged() { barre.relitLesEtats(); }
                }

                Connections {
                    target: sondeRiche
                    function onTextChanged() { barre.relitLesEtats(); }
                }

                Repeater {
                    model: [
                        /* ⚠️ `i18n` et pas `i18nc` : les deux harnais ne
                         * remplacent que `i18n(` et `i18np(` dans la copie
                         * qu'ils chargent, donc `i18nc` y est indéfini et le
                         * modèle de la barre ne se construit PAS — quatre
                         * boutons absents, trois essais rouges. Mesuré. */
                        { effet: "b", icone: "format-text-bold",
                          nom: i18n("Bold") },
                        { effet: "i", icone: "format-text-italic",
                          nom: i18n("Italic") },
                        { effet: "u", icone: "format-text-underline",
                          nom: i18n("Underline") },
                        { effet: "s", icone: "format-text-strikethrough",
                          nom: i18n("Strikethrough") }
                    ]

                    /* ⚠️ `modelData` et `index` DÉCLARÉS : sans eux le délégué
                     * ne reçoit rien et la barre est vide. Le piège a déjà coûté
                     * une liste entière dans cette tâche (2026-11-01). */
                    delegate: PlasmaComponents.ToolButton {
                        required property var modelData
                        required property int index

                        objectName: "barre-" + modelData.effet
                        icon.name: modelData.icone
                        display: PlasmaComponents.AbstractButton.IconOnly
                        /* 🔴 Le focus reste dans le texte : un bouton qui le
                         * prend emporte la sélection, et la bascule porterait
                         * alors sur rien. */
                        focusPolicy: Qt.NoFocus
                        checkable: true
                        enabled: barre.aUneSelection
                        checked: barre.aUneSelection && !!barre.etats
                            && barre.etats[modelData.effet] === true
                        /* 🔴 SUR UNE SEULE LIGNE, aussi longue soit-elle : le
                         * harnais retire les propriétés attachées ligne à
                         * ligne, et une continuation orpheline se greffe sur la
                         * propriété précédente en ternaire valide — le harnais
                         * mesure alors un autre code que le nôtre. La règle
                         * s'est payée quatre fois dans cette tâche, et un essai
                         * de la maison la garde. */
                        readonly property string infobulle: barre.aUneSelection ? modelData.nom : i18n("Select some text first")
                        PlasmaComponents.ToolTip.text: infobulle
                        PlasmaComponents.ToolTip.visible: hovered
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay

                        onClicked: carre.basculeEffet(modelData.effet)
                    }
                }

                Item { Layout.fillWidth: true }
            }

            /* Pas mien, ou mis en forme : on lit.
             *
             * #27250 : et on le lit MIS EN FORME quand le serveur a envoyé
             * `html`. Avant ce lot, une note en gras s'affichait ici avec le
             * balisage de `html2plaintext` (« des *courses* ») ; c'est le même
             * corps, enfin rendu.
             *
             * 🔴 `Text.RichText` sur un corps venu du réseau n'est sûr que
             * parce que le serveur l'assainit TOUJOURS, même pour une note
             * qu'il déclare non renvoyable : le sous-ensemble n'a ni `img`, ni
             * `a`, ni `style`, donc rien ici ne peut tirer une adresse
             * distante ni peindre hors de la note. Le jour où le serveur
             * servirait `html` brut, cette ligne deviendrait une fuite — c'est
             * pour cela que le lot serveur assainit à la LECTURE et pas
             * seulement à l'écriture. */
            PlasmaComponents.Label {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: Kirigami.Units.largeSpacing
                visible: !!carre.note && !carre.canEdit
                wrapMode: Text.Wrap
                textFormat: (carre.note && carre.note.html)
                    ? Text.RichText : Text.PlainText
                color: carre.encre
                verticalAlignment: Text.AlignTop
                text: {
                    if (!carre.note) {
                        return "";
                    }
                    return carre.note.html || carre.note.text || "";
                }
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing
                visible: !!carre.note && !carre.canEdit
                wrapMode: Text.Wrap
                font: Kirigami.Theme.smallFont
                color: Qt.alpha(carre.encre, 0.7)
                text: carre.mine
                    ? i18n("This note has formatting the desktop cannot keep. Edit it in Symbifox.")
                    : i18n("Shared with you. Only its author can change it.")
            }

            /* ── CE QUI A ÉTÉ DIT, DANS LA SURFACE QUI L'A DIT ────────────
             *
             * 🔴 Aucun message du chemin d'enregistrement n'était peint dans un
             * carré. `root.lastError` ne vivait que dans `NotesList.qml` et
             * dans `EcranAppairage.qml` ; or `saveNote` n'est appelé que d'ici,
             * et l'aiguillage monte l'écran d'appairage OU le carré OU la
             * liste, jamais deux (voir FullRepresentation). « Saved, but some
             * formatting could not be kept », le plafond d'écriture, le refus
             * hors ligne et le refus de renvoyer une note qui n'est plus
             * modifiable n'atteignaient donc PERSONNE — ce dernier garde même
             * le brouillon, donc le texte ne vivait plus qu'en mémoire jusqu'au
             * prochain redémarrage de plasmashell. Le refus hors ligne, lui,
             * était déjà muet dans un carré en 2.2.2 : ce lot-ci n'a fait
             * qu'ajouter des phrases au même silence.
             *
             * ⚠️ Deux lignes et pas une, parce que les deux messages n'ont pas
             * la même durée de vie. Celle du HAUT porte ce que l'ÉCRITURE a dit
             * et ne s'efface QUE sur un geste (`oublieLeMessage`) ou sur un
             * enregistrement réussi — un battement de sonde la laisserait
             * sinon trois à quinze secondes à l'écran, ce qui ne suffit pas
             * pour une perte de mise en forme. Celle du BAS porte le canal des
             * lectures, que la sonde vide et remplit : elle se tait quand elle
             * répéterait la première.
             *
             * ⚠️ Pas de cadre, pas de fond, pas de bouton large : un carré n'a
             * ni chrome ni cadre, et une boîte de dialogue en ferait un
             * formulaire. Un texte de la taille des petits caractères et le
             * même « × » plat que le bandeau, c'est tout. */
            ColumnLayout {
                objectName: "boite-des-messages"
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                visible: root.messageEcriture.length > 0
                    || root.lastError.length > 0
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    visible: root.messageEcriture.length > 0
                    spacing: Kirigami.Units.smallSpacing

                    PlasmaComponents.Label {
                        objectName: "message-de-l-ecriture"
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.negativeTextColor
                        text: root.messageEcriture
                    }

                    /* ⚠️ `NoFocus` : fermer la boîte ne doit pas emporter la
                     * sélection du champ, sinon la barre se verrouille juste
                     * après avoir parlé. */
                    PlasmaComponents.ToolButton {
                        objectName: "oublier-le-message"
                        icon.name: "window-close"
                        flat: true
                        focusPolicy: Qt.NoFocus
                        display: PlasmaComponents.AbstractButton.IconOnly
                        implicitWidth: Math.round(Kirigami.Units.gridUnit * 1.4)
                        implicitHeight: Math.round(Kirigami.Units.gridUnit * 1.4)
                        icon.color: Kirigami.Theme.negativeTextColor
                        onClicked: root.oublieLeMessage()
                        PlasmaComponents.ToolTip.text: i18n("Dismiss")
                        PlasmaComponents.ToolTip.visible: hovered
                        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                    }
                }

                PlasmaComponents.Label {
                    objectName: "message-de-la-lecture"
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.negativeTextColor
                    visible: root.lastError.length > 0
                        && root.lastError !== root.messageEcriture
                    text: root.lastError
                }
            }

            /* 🔴 Ce que le serveur a refusé pour de bon. Un carré a sa PROPRE
             * file : si la sienne est bloquée, c'est ici qu'il faut le dire, et
             * nulle part ailleurs. Sans cela, le « × » refusait de fermer au
             * nom d'une note qui ne partirait jamais. */
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                visible: root.notesBloquees.length > 0
                spacing: 2

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.negativeTextColor
                    text: i18n("Symbifox refused this note. It will not be sent on its own.")
                }

                PlasmaComponents.Button {
                    text: i18n("Try again")
                    icon.name: "view-refresh"
                    flat: true
                    Layout.alignment: Qt.AlignRight
                    onClicked: root.reessayerLesBloquees()
                }
            }

            /* ── Le pied : ce que la note porte en plus de son texte ─────── */
            MetaNote {
                note: carre.note
                encre: carre.encre
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                Layout.bottomMargin: Kirigami.Units.smallSpacing
            }
        }

        /* La note a disparu : le dire, et offrir la sortie. Un carré vide
         * laisserait croire à une note vide. */
        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 2
            /* ⚠️ Pas sur un carré NEUF : il n'a pas de note parce qu'on ne
             * l'a pas encore écrite, et « chargement… » par-dessus un champ
             * vide serait un mensonge poli. */
            visible: !carre.note && !root.carreNeuf
            iconName: root.pinnedGone ? "dialog-information" : "note"
            text: root.pinnedGone ? i18n("This note is gone") : i18n("Loading…")
            explanation: root.pinnedGone
                ? i18n("It was deleted, or a colleague stopped sharing it.")
                : ""

            PlasmaComponents.Button {
                visible: root.pinnedGone
                text: i18n("Close this note")
                icon.name: "window-close"
                Layout.alignment: Qt.AlignHCenter
                onClicked: root.fermerLeCarre()
            }
        }
    }

    /* Le menu des gestes, partagé avec la liste : un seul endroit où les neuf
     * gestes du serveur sont câblés.
     *
     * ⚠️ Son parent visuel est NOMMÉ : sans cela Plasma évalue `parent.width`
     * sur un parent nul au montage et le journal se remplit d'une TypeError
     * par carré. */
    MenuNote {
        id: menu
        parent: feuille
        compact: true
        onDemandeTache: function (n) { projets.demande(n); }
    }

    MenuProjets { id: projets; parent: feuille }

    /* ── Un bouton du bandeau ────────────────────────────────────────────── */
    component BoutonBandeau: PlasmaComponents.ToolButton {
        property string icone: ""
        property string infobulle: ""
        property color encre: Kirigami.Theme.textColor
        signal active()

        icon.name: icone
        flat: true
        display: PlasmaComponents.AbstractButton.IconOnly
        implicitWidth: Math.round(Kirigami.Units.gridUnit * 1.6)
        implicitHeight: Math.round(Kirigami.Units.gridUnit * 1.5)
        /* 🔴 Le libellé ET son icône se peignent avec l'encre du papier, sinon
         * Plasma peint une icône de thème sombre sur un papier sombre. Même
         * défaut que le blanc sur bleu du 3 novembre, à un autre endroit. */
        icon.color: encre
        onClicked: active()
        PlasmaComponents.ToolTip.text: infobulle
        PlasmaComponents.ToolTip.visible: hovered && infobulle.length > 0
        PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
    }
}
