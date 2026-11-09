/*
 * La lisibilité, calculée — pas devinée.
 *
 * 🔴 Pourquoi ce fichier existe. Olivier, 2026-11-03 : « le blanc sur bleu est
 * trop dur à lire ». Il avait raison, et c'est MESURABLE : l'accent de Blue Fox
 * `#29ABE2` est un bleu clair, et du blanc dessus rend environ 2,3:1 — là où
 * WCAG AA demande 4,5:1 pour du texte. Le canon de marque interdit déjà
 * littéralement les combinaisons de ce genre, et la fiche
 * `feedback_bf_blue_never_as_foreground_text_on_dark` porte la mesure depuis
 * mai : « sur fond clair, #29ABE1 rend 2,62:1 ».
 *
 * ⚠️ Et le piège qui l'a produit : poser `Kirigami.Theme.highlightColor` sur un
 * bouton fait peindre son texte par Plasma avec `highlightedTextColor`, qui est
 * BLANC. On choisit donc la couleur de fond sans choisir celle du texte, et le
 * contraste n'est le problème de personne.
 *
 * Du JavaScript pur, comme store.js et grille.js, pour la même raison : une
 * règle qui décide de la lisibilité doit s'éprouver sans Qt ni poste.
 */
.pragma library

/* Le seuil de WCAG pour du texte normal. AAA (7:1) est hors de portée sur un
 * accent de marque qu'on ne choisit pas ; AA est le minimum défendable. */
var SEUIL_AA = 4.5;

var NOIR = "#2E3132";       // l'anthracite opérationnel de Blue Fox
var BLANC = "#FFFFFF";

/* Les composantes d'un hexadécimal, ou null. Accepte #RGB et #RRGGBB, et refuse
 * tout le reste — `color_hex` et `report_brand_primary` sont des Char libres. */
function composantes(hex) {
    var v = String(hex || "").trim();
    if (!/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(v)) {
        return null;
    }
    v = v.substring(1);
    if (v.length === 3) {
        v = v[0] + v[0] + v[1] + v[1] + v[2] + v[2];
    }
    return [parseInt(v.substring(0, 2), 16),
            parseInt(v.substring(2, 4), 16),
            parseInt(v.substring(4, 6), 16)];
}

/* La luminance relative de WCAG 2.x. */
function luminance(hex) {
    var c = composantes(hex);
    if (!c) {
        return null;
    }
    var canal = function (v) {
        var s = v / 255;
        return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * canal(c[0]) + 0.7152 * canal(c[1]) + 0.0722 * canal(c[2]);
}

/* Le rapport de contraste entre deux couleurs, ou null si l'une est illisible. */
function contraste(a, b) {
    var la = luminance(a), lb = luminance(b);
    if (la === null || lb === null) {
        return null;
    }
    var haut = Math.max(la, lb), bas = Math.min(la, lb);
    return (haut + 0.05) / (bas + 0.05);
}

/* La couleur de texte à employer SUR ce fond : celle des deux qui contraste le
 * mieux.
 *
 * ⚠️ On ne « préfère » pas le blanc ni le noir : on mesure. Sur un bleu clair
 * de marque, c'est l'anthracite qui gagne — et c'est exactement le cas qu'Olivier
 * a vu. Rend "" quand le fond n'est pas une couleur lisible, pour que l'appelant
 * garde son propre repli plutôt que de recevoir une couleur inventée.
 */
function texteSur(fond) {
    var cNoir = contraste(fond, NOIR);
    var cBlanc = contraste(fond, BLANC);
    if (cNoir === null || cBlanc === null) {
        return "";
    }
    return cNoir >= cBlanc ? NOIR : BLANC;
}

/* La couleur de texte DEMANDÉE est-elle lisible sur ce fond ? Sinon, laquelle
 * employer.
 *
 * 🔴 C'est la garde qui compte pour les étiquettes et les notes : leur couleur
 * de texte vient du SERVEUR, et un locataire peut y avoir mis du blanc sur un
 * fond clair. Le widget est celui qui peint : c'est à lui de refuser une paire
 * illisible, comme il refuse déjà un hexadécimal difforme.
 *
 * Rend la couleur demandée quand elle atteint AA, sinon la meilleure des deux.
 */
function texteLisible(fond, demandee) {
    var mesure = contraste(fond, demandee);
    if (mesure !== null && mesure >= SEUIL_AA) {
        return String(demandee);
    }
    var repli = texteSur(fond);
    if (!repli) {
        /* Fond illisible : on ne peut rien affirmer, l'appelant décide. */
        return demandee ? String(demandee) : "";
    }
    return repli;
}

/* Vrai quand la paire n'atteint même pas AA avec le MEILLEUR texte possible.
 * Un accent de marque trop pâle est dans ce cas : utile à dire plutôt qu'à
 * corriger en douce, puisque la couleur ne nous appartient pas. */
function paireFaible(fond) {
    var meilleur = texteSur(fond);
    if (!meilleur) {
        return false;
    }
    return contraste(fond, meilleur) < SEUIL_AA;
}
