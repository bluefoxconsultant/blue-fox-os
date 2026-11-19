/*
 * La mise en forme, côté bureau : ce qu'il faut pour parler du HTML au
 * serveur sans embarquer un analyseur dans un widget.
 *
 * BF #27250, lot « barre de mise en forme ». Le serveur (bf_bloc_notes
 * 18.0.4.5.0) sert `html` TOUJOURS assaini dans un sous-ensemble nommé, plus
 * `rich_editable` qui dit si le client peut le RENVOYER sans rien perdre. La
 * barre écrit donc `html` et jamais `text` — les deux à la fois sont refusés
 * par le serveur, et il a raison : l'ordre des clés d'un objet JSON n'engage
 * personne.
 *
 * ── Pourquoi ce fichier est si court, et pourquoi c'est volontaire ────────
 *
 * 🔴 La tentation était d'écrire ici un miroir de l'assainisseur du serveur,
 * pour comparer « ce que je rendrais » à « ce qui est en base » et savoir si
 * la personne a modifié quelque chose. C'était un piège : deux analyseurs
 * écrits dans deux langages divergent au premier défaut corrigé d'un seul
 * côté, et le témoin qui surveille un outil ne doit jamais sortir du même
 * outil. Mesuré sur charizard (Qt 6.12, 2026-11-13) :
 *
 *   — deux `TextEdit` de même police normalisent un même corps en une chaîne
 *     IDENTIQUE au caractère près ;
 *   — `color` ne paraît PAS dans le document sérialisé, donc l'encre du carré
 *     ne salit pas la comparaison ;
 *   — Qt est un POINT FIXE sur son propre corps : re-nourrir le corps extrait
 *     rend exactement le même corps.
 *
 * Donc « a-t-elle modifié la note ? » se répond en comparant deux documents
 * de Qt, sans analyser une seule balise. Ce fichier ne fait plus que découper
 * l'enveloppe.
 */
.pragma library

/* Le plafond d'ÉCRITURE du serveur (MAX_HTML). Qt enfle un texte d'un facteur
 * 4 à 6 avec son enveloppe, et le serveur refuse au-delà par un message. On le
 * dit AVANT d'envoyer : un refus prévu se raconte mieux qu'un 400. */
var MAX_HTML = 400000;

/* Le corps d'un document Qt, sans l'enveloppe.
 *
 * Qt ne rend pas le HTML qu'on lui donne : un `TextEdit` en mode riche renvoie
 * un document COMPLET — `<!DOCTYPE>`, `<head>`, un `<style>` qui porte du CSS
 * en TEXTE, et un `<body>` dont le style déclare la police du contrôle.
 *
 * 🔴 Ce `<style>` est la raison d'être de cette fonction. L'assainisseur du
 * serveur range `style` parmi les MUETS, donc il le fait disparaître avec son
 * contenu — rien ne fuirait dans la note. Mais un assainisseur qui DÉPLIERAIT
 * les inconnues au lieu de les taire écrirait « p, li { white-space: pre-wrap;
 * } » dans la note de la personne. On n'envoie pas une enveloppe en pariant
 * sur la bonté de ce qui la lit.
 *
 * ⚠️ `lastIndexOf` pour la fermeture, et pas `indexOf` : un « </body> » TAPÉ
 * par la personne ressort échappé (`&lt;/body&gt;`) de la sérialisation de Qt,
 * donc la dernière occurrence est bien la vraie — mais la première le serait
 * aussi, et autant ne pas dépendre de l'échappement de Qt pour ça.
 */
function corpsDeQt(doc) {
    var texte = (doc === undefined || doc === null) ? "" : String(doc);
    var debut = texte.indexOf("<body");
    if (debut < 0) {
        /* Pas d'enveloppe : un éditeur en texte simple, ou un corps déjà nu
         * (ce que le serveur sert). On le rend tel quel. */
        return texte;
    }
    var ouvrante = texte.indexOf(">", debut);
    var fin = texte.lastIndexOf("</body>");
    if (ouvrante < 0 || fin < 0 || fin < ouvrante) {
        return texte;
    }
    return texte.substring(ouvrante + 1, fin).replace(/^\s+|\s+$/g, "");
}

/* Les blocs de Qt, débarrassés du style qui ne porte AUCUN effet.
 *
 * 🔴 Mesuré de bout en bout le 2026-11-13, et c'est un défaut que ni les
 * essais node, ni le harnais QML, ni les 235 essais du module ne pouvaient
 * voir. Qt enveloppe chaque bloc de son propre style de mise en page :
 *
 *   <p style=" margin-top:12px; margin-bottom:12px; margin-left:0px;
 *      margin-right:0px; -qt-block-indent:0; text-indent:0px;">
 *
 * Aucune de ces déclarations n'est traduisible en balise, donc le serveur les
 * laisse tomber — à juste titre — et répond `flattened: true`. Le widget
 * aurait alors annoncé « de la mise en forme n'a pas pu être gardée » à CHAQUE
 * pause de frappe, sur une note dont rien n'avait été perdu. Une alerte qui
 * crie toujours ne veut plus rien dire, et c'est elle qui aurait masqué la
 * vraie perte (un lien collé, une couleur).
 *
 * ⚠️ CHIRURGICAL, et dans un seul sens. On ne retire un `style` que s'il ne
 * porte AUCUN des trois effets que le serveur sait traduire. Un `<li
 * style="font-weight:700">` — que `_item()` lit côté serveur — est donc
 * GARDÉ tel quel : perdre de la mise en forme en nettoyant serait pire que le
 * faux message qu'on corrige. Et les `<span>` ne sont jamais touchés : c'est
 * là que Qt met les effets.
 *
 * Effet de bord utile : la charge rétrécit d'un facteur 4 à 6, ce qui éloigne
 * d'autant le plafond de 400 Ko.
 */
var EFFETS_EN_STYLE = /font-weight|font-style|text-decoration/i;
/* 🔴 La balise OUVRANTE avec tous ses attributs, et CHAQUE `style` jugé pour
 * lui-même.
 *
 * La forme d'avant attrapait `…\sstyle="([^"]*)"([^>]*)` : sur une balise qui
 * porte DEUX attributs `style`, elle ne jugeait que le DERNIER et rendait la
 * balise entière telle quelle. Mesuré en node le 2026-11-13 :
 * `<li style=" -qt-block-indent:0; text-indent:0px;" style=" font-weight:700;">`
 * ressortait INTACT, donc les marges de Qt repartaient au serveur, qui répond
 * alors `flattened: true` sur une note dont rien n'est perdu. L'ordre inverse
 * donnait la bonne réponse, mais par accident. */
var BLOC_OUVRANT = /<(p|div|ul|ol|li)((?:\s[^>]*)?)>/gi;
var UN_STYLE_DE_BLOC = /\sstyle\s*=\s*"([^"]*)"/gi;

function sansStyleDeBloc(html) {
    var texte = (html === undefined || html === null) ? "" : String(html);
    return texte.replace(BLOC_OUVRANT, function (tout, balise, attributs) {
        var net = String(attributs || "").replace(UN_STYLE_DE_BLOC,
            function (declaration, style) {
                if (EFFETS_EN_STYLE.test(style)) {
                    /* Il porte un effet : on n'y touche pas. */
                    return declaration;
                }
                return "";
            });
        if (net === attributs) {
            return tout;
        }
        return "<" + balise + net + ">";
    });
}

/* Ce que le widget ENVOIE : le corps de Qt, sans son enveloppe ni ses styles
 * de mise en page. Un seul nom, pour que personne n'envoie `corpsDeQt` tout
 * nu par mégarde. */
/* 🔴 LA CASSE DU THÈME NE DOIT PAS ENTRER DANS LA NOTE.
 *
 * Mesuré sur charizard le 2026-11-13 : sur un thème dont la police est en
 * PETITES CAPITALES, la police lue par la barre porte `SmallCaps`, et la
 * réassigner fait écrire `font-variant:small-caps` par Qt dans le `<span>` de
 * l'effet. Mesuré ensuite contre le banc 27250 : le serveur rend le MÊME
 * canonique (`<b><u>lait</u></b>`) avec ou sans la déclaration, mais répond
 * `flattened: true` quand elle est là — donc « de la mise en forme n'a pas pu
 * être gardée » à CHAQUE clic, sur une note dont rien n'est perdu. Tant que ce
 * message n'était peint nulle part, c'était inoffensif ; maintenant qu'il est
 * peint dans le carré, ce serait l'alerte-qui-crie-toujours que ce lot a payé
 * cher pour tuer.
 *
 * ⚠️ Pourquoi ici, et pas en neutralisant la casse à la bascule : neutraliser
 * CHANGE LE RENDU (mesuré, 146,8 px contre 144,5 px pour la même ligne), donc
 * la barre abîmerait l'allure d'un mot que personne ne lui a demandé de
 * toucher — le pire défaut possible pour une barre.
 *
 * ⚠️ Et pourquoi `font-variant` SEUL, alors que les `<span>` ne sont jamais
 * touchés par ailleurs : il n'y a aucune façon de le DEMANDER depuis cette
 * barre, il ne sort que du thème. Un `color`, lui, ARRIVE au serveur, parce
 * que sa perte doit rester visible. Mesuré enfin contre le banc : un `<span>`
 * dont le style ne porte plus rien ne fait PAS crier le serveur.
 *
 * ⚠️ Dans la VALEUR d'un attribut `style`, et nulle part ailleurs : « font-
 * variant » TAPÉ dans la note est du texte, et Qt échappe les guillemets de
 * ce que la personne tape — le motif ne peut donc pas l'atteindre. */
var ATTRIBUT_STYLE = /\sstyle\s*=\s*"([^"]*)"/gi;
var CASSE_EN_STYLE = /\s*font-variant\s*:[^;]*;?/gi;

function sansCasseDuTheme(html) {
    var texte = (html === undefined || html === null) ? "" : String(html);
    return texte.replace(ATTRIBUT_STYLE, function (tout, style) {
        var net = style.replace(CASSE_EN_STYLE, "");
        if (net === style) {
            return tout;
        }
        return ' style="' + net + '"';
    });
}

function pourLeServeur(doc) {
    return sansCasseDuTheme(sansStyleDeBloc(corpsDeQt(doc)));
}

/* Trop gros pour la porte d'écriture du serveur ? */
function tropGros(html) {
    return String(html === undefined || html === null ? "" : html).length > MAX_HTML;
}

/* Ce que le widget ENVERRAIT pour ce document est-il déjà ce qui est STOCKÉ ?
 *
 * Une seule ligne, mais elle porte tout le contrat : les deux côtés doivent
 * sortir du MÊME `TextEdit` (ou de deux `TextEdit` de même police), sinon
 * `<body style=" font-family:…">` diffère et deux corps identiques se
 * déclarent différents. C'est pour cela que la comparaison vit ici, nommée,
 * plutôt qu'écrite deux fois à l'oeil dans du QML.
 *
 * 🔴 Elle compare ce qu'on ENVERRAIT, et non les deux documents bruts. La
 * version d'avant (`memeDocument`, deux `corpsDeQt`) répondait « modifiée »
 * dès que les deux côtés ne différaient que par ce que le widget RETIRE avant
 * d'envoyer. Un seul cas existe aujourd'hui, et il suffit : sur un thème en
 * petites capitales, le `font-variant` que Qt écrit dans le champ n'est pas
 * dans le corps du serveur, donc la note se déclarait modifiée à chaque pause
 * de frappe — écriture pour rien, `write_date` qui avance, et le champ
 * rechargé sous le curseur.
 *
 * La bonne question est « y a-t-il quelque chose à envoyer », pas « les deux
 * chaînes sont-elles identiques » : si deux documents donnent la même charge,
 * les envoyer ne changerait rien en base. Et un style de bloc qui PORTE un
 * effet, lui, reste dans la charge (voir `sansStyleDeBloc`), donc il compte
 * toujours. */
function memeEnvoi(docA, docB) {
    return pourLeServeur(docA) === pourLeServeur(docB);
}

/* ── Ce qu'une SÉLECTION dit de sa mise en forme ─────────────────────────
 *
 * 🔴 Mesuré sur charizard (Qt 6.12) le 2026-11-13, et c'est ce qui décide de
 * tout ce qui suit :
 *
 *   1. `cursorSelection.font` ne rend PAS « la police de la sélection » : il
 *      rend celle du caractère qui précède la FIN de la sélection. Sur
 *      « Acheter du <b>lait</b> et du jus » sélectionné en entier, il rend
 *      donc `bold: false` ; sur « ␣lait », `bold: true`. Une police lue là et
 *      réécrite sur TOUTE la sélection enlève donc le gras dans le premier
 *      cas, et l'ajoute à l'espace dans le second.
 *   2. Mesuré jusqu'en base, contre le banc 27250 : le serveur rangeait
 *      `<p><u>Acheter du </u><u>lait</u><u> et du jus</u></p>` — une note dont
 *      le gras a disparu — et répondait `flattened: false`. Rien n'était dit,
 *      rien n'était rattrapable.
 *   3. Dans une plage UNIFORME, la même lecture est juste.
 *   4. Une police PARTIELLE assignée à `cursorSelection` efface les autres
 *      attributs : `Qt.font({bold: true})` sur un mot italique lui retire
 *      l'italique et lui pose « Sans Serif 9 ».
 *   5. Une police RECOPIÉE attribut par attribut n'est pas plus sûre : elle
 *      n'emporte que ce qu'elle ÉNUMÈRE. Sur un thème en `pixelSize`, recopier
 *      `pointSize` écrit `font-size:10pt` dans la note — le serveur ne sait
 *      pas traduire une taille, donc « perte » annoncée à chaque clic sur une
 *      note dont rien n'est perdu. Sur un thème en `font.weight: 500`, elle
 *      écrit `font-weight:400` et ÉCLAIRCIT le texte pour de bon.
 *   6. La police LUE puis MUTÉE sur un seul attribut, elle, n'écrit ni taille
 *      ni famille, sur les deux sortes de thème : elle ne diffère de la police
 *      du contrôle que par l'effet demandé.
 *
 * D'où la règle : la sélection se découpe en plages UNIFORMES, et dans chaque
 * plage on MUTE la police lue. Ce fichier porte le découpage et la décision,
 * qui sont du calcul ; `StickyNote.qml` porte la lecture et la mutation, qui
 * sont du Qt.
 *
 * ⚠️ `lis(i)` est un LECTEUR : il rend le descripteur du caractère `i`, ou
 * `null`. Rien ici ne connaît donc un type de Qt, et un essai node éprouve
 * tout cela avec un tableau.
 */
var EFFETS = ["b", "i", "u", "s"];

function estUnEffet(effet) {
    return EFFETS.indexOf(effet) >= 0;
}

/* 🔴 LES CARACTÈRES QUI NE PORTENT AUCUNE ENCRE, et pourquoi ils décident.
 *
 * Mesuré sur charizard le 2026-11-13, sur « a / (ligne vide) / b » dont toute
 * l'encre est grasse (`<p><b>a</b></p><p><br /></p><p><b>b</b></p>`) :
 *
 *     0:B---/u61   1:----/u2029   2:----/u2028   3:B---/u2029   4:B---/u62
 *
 * Le séparateur de paragraphe (U+2029) et le saut de ligne (U+2028) de la
 * ligne VIDE ne portent rien. `toutPorteLEffet` répondait donc faux sur une
 * note dont toute l'encre est grasse : le bouton « B » se RELEVAIT, et le
 * premier clic AJOUTAIT le gras au lieu de l'enlever. À l'écran rien ne
 * changeait, mais la note repartait au serveur habillée de
 * `<p><span style=" font-weight:700;"><br /></span></p>` (mesuré), et il
 * fallait cliquer deux fois pour dégraisser. Les lignes vides sont la norme
 * dans un pense-bête.
 *
 * ⚠️ Les séparateurs d'une ligne NON vide portent l'effet de leur ligne
 * (mesuré : « a/b » tout gras rend `0:B---  1:B---/u2029  2:B---`), donc les
 * sauter ne change rien là. Et une ESPACE compte, elle : la rendre grasse est
 * un effet AJOUTÉ, et c'est précisément ce que le tour précédent a corrigé.
 *
 * ⚠️ Un lecteur qui ne dit pas le caractère — un essai node qui ne passe que
 * des polices — est cru sur parole : il décrit des caractères ordinaires. Un
 * descripteur NUL, lui, est une lecture ratée : il n'a pas d'encre ici (donc
 * il ne sera pas peint), mais les deux décideurs le traitent à part — voir
 * `toutPorteLEffet`. */
var SANS_ENCRE = ["\u2029", "\u2028", "\u000a", "\u000d", ""];

function porteDeLEncre(descripteur) {
    if (!descripteur) {
        return false;
    }
    if (descripteur.texte === undefined || descripteur.texte === null) {
        return true;
    }
    return SANS_ENCRE.indexOf(String(descripteur.texte)) < 0;
}

/* Deux caractères sont-ils dans la même plage ?
 *
 * ⚠️ La clé porte les quatre effets ET la police. Deux caractères qui ne
 * diffèrent que par la taille doivent rester dans deux plages : les mettre
 * ensemble imposerait à l'un la taille de l'autre, puisque la plage est
 * réécrite avec UNE police — celle que Qt rend pour elle. */
function cleDePolice(police) {
    if (!police) { return "\u0000"; }
    return [police.family, police.pointSize, police.pixelSize, police.weight,
            !!police.bold, !!police.italic, !!police.underline,
            !!police.strikeout].join("\u0001");
}

/* Le découpage de [debut, fin) en plages uniformes.
 *
 * Chaque plage dit aussi si elle porte de l'ENCRE : `basculeEffet` ne peint
 * que celles-là.
 *
 * ⚠️ L'encre entre dans la clé de découpage, et pas seulement dans la plage :
 * sans cela un séparateur de ligne vide se fondrait dans la plage voisine
 * quand leurs polices concordent, et la bascule l'emporterait avec elle — le
 * `<span><br /></span>` qu'on vient justement de fermer.
 *
 * ⚠️ `lis(i)` est appelé UNE fois par position : chaque appel déplace la
 * sélection d'un éditeur de Qt et coûte un aller-retour, donc le lire deux
 * fois doublerait le prix du clic. */
function plagesUniformes(lis, debut, fin) {
    var plages = [];
    if (!(fin > debut)) {
        return plages;
    }
    var lu = lis(debut);
    var ouverte = debut;
    var encre = porteDeLEncre(lu);
    var cle = cleDePolice(lu) + "\u0002" + (encre ? "1" : "0");
    for (var i = debut + 1; i <= fin; i++) {
        var suivante = null;
        var encreSuivante = false;
        if (i < fin) {
            lu = lis(i);
            encreSuivante = porteDeLEncre(lu);
            suivante = cleDePolice(lu) + "\u0002" + (encreSuivante ? "1" : "0");
        }
        if (i === fin || suivante !== cle) {
            plages.push({ debut: ouverte, fin: i, encre: encre });
            ouverte = i;
            cle = suivante;
            encre = encreSuivante;
        }
    }
    return plages;
}

/* TOUS les caractères portent-ils l'effet ?
 *
 * ⚠️ Sortie dès le premier qui ne le porte pas : c'est ce qui rend la question
 * gratuite sur du texte nu, où le premier caractère répond pour tous. */
function toutPorteLEffet(lis, debut, fin, effet) {
    if (!estUnEffet(effet) || !(fin > debut)) {
        return false;
    }
    var vuDeLEncre = false;
    for (var i = debut; i < fin; i++) {
        var lu = lis(i);
        /* ⚠️ Un descripteur NUL est une lecture RATÉE, pas une ligne vide : on
         * ne sait RIEN de ce caractère, donc on ne promet rien pour la
         * sélection. `cursorSelection` peut rendre nul, et un nul qui vaudrait
         * « porte tout » ferait DÉGRASSER la sélection au premier clic. */
        if (!lu) {
            return false;
        }
        /* 🔴 Un caractère sans encre, lui, ne répond de rien : voir
         * `porteDeLEncre`. C'est cette ligne qui empêche une ligne vide
         * d'inverser le bouton. */
        if (!porteDeLEncre(lu)) {
            continue;
        }
        vuDeLEncre = true;
        if (!effetPose(lu, effet)) {
            return false;
        }
    }
    /* Une sélection sans une goutte d'encre ne porte pas l'effet — et
     * `basculeEffet` la refuse, il n'y a rien à mettre en forme. */
    return vuDeLEncre;
}

/* Ce que la bascule doit POSER sur toute la sélection.
 *
 * 🔴 C'est le geste des traitements de texte, et il n'y en a pas d'autre qui
 * tienne : une sélection où le gras est PARTIEL devient grasse en entier, et
 * seule une sélection TOUTE grasse se dégrasse. Basculer plage par plage
 * (chacune selon son propre état) rendrait le négatif de la sélection au
 * premier clic — personne ne demande ça. */
function cibleDeLaBascule(lis, debut, fin, effet) {
    return !toutPorteLEffet(lis, debut, fin, effet);
}

/* L'état enfoncé des quatre boutons, en une passe.
 *
 * 🔴 Un bouton enfoncé dit « TOUTE la sélection le porte », donc aussi « un
 * clic va le retirer ». Lu sur la seule police de `cursorSelection`, il
 * mentait : « B » paraissait enfoncé sur « ␣lait » — dont l'espace n'est pas
 * grasse — et un clic AJOUTAIT le gras, l'inverse de ce que le bouton
 * montrait. */
function etatsDesEffets(lis, debut, fin) {
    var etats = { b: false, i: false, u: false, s: false };
    if (!(fin > debut)) {
        return etats;
    }
    var k;
    for (k = 0; k < EFFETS.length; k++) {
        etats[EFFETS[k]] = true;
    }
    var vuDeLEncre = false;
    for (var i = debut; i < fin; i++) {
        var police = lis(i);
        /* ⚠️ Une lecture RATÉE : aucun bouton n'est enfoncé, et on arrête. Un
         * nul traité comme une ligne vide laisserait les boutons dire « toute
         * la sélection le porte » sur une sélection qu'on n'a pas su lire. */
        if (!police) {
            vuDeLEncre = false;
            break;
        }
        /* 🔴 La ligne VIDE ne vote pas : voir `porteDeLEncre`. Sans cette
         * ligne, les quatre boutons se relevaient sur une note dont toute
         * l'encre portait l'effet, et le clic suivant AJOUTAIT au lieu de
         * retirer — le bouton montrait donc l'inverse de ce qu'il allait
         * faire. */
        if (!porteDeLEncre(police)) {
            continue;
        }
        vuDeLEncre = true;
        var reste = false;
        for (k = 0; k < EFFETS.length; k++) {
            if (etats[EFFETS[k]] && !effetPose(police, EFFETS[k])) {
                etats[EFFETS[k]] = false;
            }
            if (etats[EFFETS[k]]) {
                reste = true;
            }
        }
        /* Plus rien à infirmer : le reste de la sélection ne changerait rien. */
        if (!reste) {
            break;
        }
    }
    /* Rien que des lignes vides : aucun bouton n'est enfoncé. */
    if (!vuDeLEncre) {
        for (k = 0; k < EFFETS.length; k++) {
            etats[EFFETS[k]] = false;
        }
    }
    return etats;
}

/* L'effet est-il posé sur CE caractère ? (une police, jamais une sélection) */
function effetPose(police, effet) {
    if (!police) { return false; }
    if (effet === "b") { return !!police.bold; }
    if (effet === "i") { return !!police.italic; }
    if (effet === "u") { return !!police.underline; }
    if (effet === "s") { return !!police.strikeout; }
    return false;
}
