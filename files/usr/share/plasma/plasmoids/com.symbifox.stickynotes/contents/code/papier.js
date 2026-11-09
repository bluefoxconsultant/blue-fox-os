/*
 * Le PAPIER d'un pense-bête : la couleur du corps, celle du bandeau, celle du
 * trait. Du JavaScript pur, comme store.js, grille.js et couleurs.js, et pour
 * la même raison : ce qui décide de ce qu'on voit doit s'éprouver sans Qt, sans
 * poste et sans serveur.
 *
 * 🔴 Aucune couleur n'est écrite ici. Le papier vient de la note
 * (`color_hex`, résolu par le serveur pour cette personne) ou, à défaut, de la
 * palette que le locataire déclare (`/prefs` → `swatches`). Un jaune de
 * Windows codé en dur serait la marque de personne, et il remonterait au
 * serveur dès qu'on l'enregistre.
 *
 * ⚠️ Le bandeau n'est pas « le papier en plus foncé » : sur un papier déjà
 * sombre, assombrir ne se voit pas. On mesure la clarté et on va dans la
 * direction qui laisse un écart VISIBLE, puis on vérifie que l'écart y est.
 */
.pragma library
/* 🔴 La lisibilité ne se duplique pas. Le bandeau est un FOND : le choisir sans
 * vérifier le texte qui va dessus, c'est exactement le défaut du 2026-11-03,
 * déplacé d'un cran. On emprunte donc le calcul de contraste déjà éprouvé. */
.import "couleurs.js" as Couleurs

/* Combien le bandeau s'écarte du papier. Mesuré à l'œil sur les captures du
 * 2026-11-08 : 0,10 se voit sans trancher, 0,04 ne se voit pas, 0,20 fait deux
 * couleurs au lieu d'une. */
var ECART_BANDEAU = 0.10;
var ECART_TRAIT = 0.18;

/* Au-dessus de cette clarté on assombrit, en dessous on éclaircit. 0,35 est le
 * point où assombrir cesse de se voir. */
var SEUIL_CLARTE = 0.35;

var MOTIF_HEX = /^#[0-9A-Fa-f]{6}$/;

function valide(hex) {
    return typeof hex === "string" && MOTIF_HEX.test(hex);
}

function composantes(hex) {
    if (!valide(hex)) {
        return null;
    }
    return {
        r: parseInt(hex.substr(1, 2), 16),
        v: parseInt(hex.substr(3, 2), 16),
        b: parseInt(hex.substr(5, 2), 16)
    };
}

function deuxChiffres(n) {
    var v = Math.max(0, Math.min(255, Math.round(n)));
    return (v < 16 ? "0" : "") + v.toString(16);
}

function enHex(c) {
    return "#" + deuxChiffres(c.r) + deuxChiffres(c.v) + deuxChiffres(c.b);
}

/* La clarté perçue, 0 à 1. La même pondération que la luminance de WCAG sans
 * la correction gamma : on ne juge pas un contraste ici, on choisit un sens. */
function clarte(hex) {
    var c = composantes(hex);
    if (!c) {
        return 0;
    }
    return (0.2126 * c.r + 0.7152 * c.v + 0.0722 * c.b) / 255;
}

function assombrir(hex, part) {
    var c = composantes(hex);
    if (!c) {
        return hex;
    }
    var f = 1 - Math.max(0, Math.min(1, part));
    return enHex({ r: c.r * f, v: c.v * f, b: c.b * f });
}

function eclaircir(hex, part) {
    var c = composantes(hex);
    if (!c) {
        return hex;
    }
    var f = Math.max(0, Math.min(1, part));
    return enHex({
        r: c.r + (255 - c.r) * f,
        v: c.v + (255 - c.v) * f,
        b: c.b + (255 - c.b) * f
    });
}

/* Le voisin d'une couleur, dans la direction qui se voit. */
function voisin(hex, ecart) {
    if (!valide(hex)) {
        return hex;
    }
    return clarte(hex) > SEUIL_CLARTE
        ? assombrir(hex, ecart)
        : eclaircir(hex, ecart);
}

/* Le bandeau du pense-bête.
 *
 * 🔴 MESURÉ, pas choisi. L'écart par défaut va dans la direction qui se voit —
 * mais sur le rouge d'Odoo `#EE2D2D`, qui est sombre de justesse, éclaircir
 * rapproche le bandeau du BLANC que le texte avait choisi : 3,77:1, sous la
 * norme. Un essai l'a attrapé le 2026-11-08, à la première passe.
 *
 * La règle est donc : proposer, puis VÉRIFIER le texte dessus. Dans l'ordre —
 * l'écart normal, le même écart dans l'autre sens, puis des écarts de plus en
 * plus petits. Faute de quoi on rend le papier lui-même : pas de bandeau vaut
 * mieux qu'un bandeau illisible, et la forme du pense-bête tient par ses coins,
 * son ombre et son trait.
 */
function bandeauLisible(papierHex, texteHex, ecart) {
    if (!valide(papierHex)) {
        return papierHex;
    }
    var texte = valide(texteHex) ? texteHex : Couleurs.texteSur(papierHex);
    var base = ecart === undefined ? ECART_BANDEAU : ecart;
    var essais = [];
    var sens = clarte(papierHex) > SEUIL_CLARTE ? -1 : 1;
    for (var i = 0; i < 4; i++) {
        var pas = base / Math.pow(2, i);
        essais.push(sens < 0 ? assombrir(papierHex, pas) : eclaircir(papierHex, pas));
        if (i === 0) {
            essais.push(sens < 0 ? eclaircir(papierHex, pas) : assombrir(papierHex, pas));
        }
    }
    for (var j = 0; j < essais.length; j++) {
        if (Couleurs.contraste(texte, essais[j]) >= Couleurs.SEUIL_AA) {
            return essais[j];
        }
    }
    return papierHex;
}

function bandeau(papierHex) {
    return bandeauLisible(papierHex, null, ECART_BANDEAU);
}

/* Le trait : le même écart, plus marqué, et surtout dans le MÊME SENS que le
 * bandeau. Sans cette contrainte, un papier dont le bandeau a dû changer de
 * direction pour rester lisible se retrouve cerné d'un trait qui part dans
 * l'autre sens — deux couleurs qui se contredisent autour du même carré. */
function trait(papierHex, texteHex) {
    if (!valide(papierHex)) {
        return papierHex;
    }
    var band = bandeauLisible(papierHex, texteHex, ECART_BANDEAU);
    if (band === papierHex) {
        return voisin(papierHex, ECART_TRAIT);
    }
    return clarte(band) >= clarte(papierHex)
        ? eclaircir(papierHex, ECART_TRAIT)
        : assombrir(papierHex, ECART_TRAIT);
}

/* Le papier d'une note : ce que le SERVEUR a résolu pour elle, ou rien.
 *
 * 🔴 « Ou rien » est un choix, pas un oubli. La tentation était de rendre la
 * première couleur de la palette pour qu'une note sans couleur ait quand même
 * l'air d'un pense-bête. Mais le mobile et le web, eux, la montreraient grise :
 * trois surfaces qui ne s'accordent pas sur la couleur d'une même note. Et le
 * jour où quelqu'un enregistre, la couleur inventée ici part en base.
 *
 * Sans couleur, c'est donc à la vue de retomber sur le thème de la personne.
 * Ce qui fait un pense-bête sans couleur, c'est sa forme : le bandeau, les
 * coins, l'ombre. */
function papier(note) {
    var n = note || {};
    return valide(n.color_hex) ? n.color_hex : "";
}

/* La palette que le menu « ⋯ » propose.
 *
 * ⚠️ MESURÉ sur le serveur (2026-11-08), pas supposé : `/prefs` rend
 * `swatches` = une liste de NUANCIERS `{id, name, shared, colors: [hex…]}` —
 * et non une liste de couleurs — puis `palette` = les douze couleurs d'Odoo,
 * saturées. Ma première version lisait `swatch.hex`, qui n'existe nulle part :
 * elle aurait rendu une palette vide chez tout le monde, sans une erreur.
 *
 * L'ordre a du sens : les nuanciers du locataire d'abord (les siens, puis ceux
 * de la société, le serveur les trie déjà), la palette d'Odoo ensuite comme
 * repli. Un locataire qui s'est donné des pastels les voit en premier. */
var MAX_ECHANTILLONS = 12;

function palette(prefs) {
    var out = [], vus = {};

    function ajoute(hex, nom) {
        if (!valide(hex) || out.length >= MAX_ECHANTILLONS) {
            return;
        }
        var cle = hex.toUpperCase();
        if (vus[cle]) {
            return;
        }
        vus[cle] = true;
        out.push({ hex: hex, name: nom || "" });
    }

    var nuanciers = (prefs && prefs.swatches) || [];
    for (var i = 0; i < nuanciers.length; i++) {
        var n = nuanciers[i] || {};
        var couleurs = n.colors || [];
        for (var j = 0; j < couleurs.length; j++) {
            ajoute(couleurs[j], n.name);
        }
    }
    var odoo = (prefs && prefs.palette) || [];
    for (var k = 0; k < odoo.length; k++) {
        ajoute(odoo[k], "");
    }
    return out;
}

/* La couleur d'une note NEUVE.
 *
 * Windows donne une couleur à toute note qu'on crée. Nous aussi — mais
 * seulement si le locataire a DÉCLARÉ un nuancier.
 *
 * 🔴 Surtout pas « la première du nuancier aplati » : faute de nuancier, celui-ci
 * retombe sur la palette d'Odoo, dont la première couleur est le gris `#A2A2A2`.
 * Toute note neuve serait donc grise chez qui n'a rien déclaré — une couleur
 * que personne n'a choisie, écrite en base, et visible ensuite sur le mobile.
 * Sans nuancier, pas de couleur : la note prend le thème de la personne, et un
 * clic dans « ⋯ » suffit à lui en donner une.
 */
function couleurParDefaut(prefs) {
    var nuanciers = (prefs && prefs.swatches) || [];
    for (var i = 0; i < nuanciers.length; i++) {
        var couleurs = (nuanciers[i] && nuanciers[i].colors) || [];
        for (var j = 0; j < couleurs.length; j++) {
            if (valide(couleurs[j])) {
                return couleurs[j];
            }
        }
    }
    return "";
}
