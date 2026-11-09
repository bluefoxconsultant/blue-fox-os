/*
 * Où poser les carrés sur le bureau, et dans quel ordre.
 *
 * Du JavaScript pur, exactement comme store.js, et pour la même raison : la
 * partie qui peut tapisser le bureau de quelqu'un ou perdre une note se lit et
 * s'éprouve sans Qt, sans poste et sans serveur.
 *
 * ⚠️ CE QUE L'API DE PLASMA PERMET, mesuré le 2026-11-03 sur un banc Plasma
 * isolé (voir la napkin #27250) :
 *
 *   — `addWidget(plugin, x, y, w, h)` place un carré À LA POSE. Le conteneur
 *     décale d'une constante (+14 px observés) et ramène la taille à ce que le
 *     plasmoïde accepte, mais l'ÉCART entre deux poses est exact : une grille
 *     tient. C'est pourquoi ce module rend des positions RELATIVES à une
 *     origine, et ne promet jamais une position absolue au pixel.
 *   — Déplacer un carré déjà posé est IMPOSSIBLE à chaud. Ranger veut donc dire
 *     retirer et reposer, en recopiant la configuration.
 */
.pragma library

/* La taille d'un carré, et l'air entre eux. Le conteneur ramènera la taille à
 * ce que le plasmoïde accepte ; ces valeurs décident de l'écart, pas du rendu. */
var LARGEUR = 288;
var HAUTEUR = 324;
var MARGE = 24;

/* 🔴 Le plafond, et pourquoi il est DUR.
 *
 * Le bloc-notes d'Olivier porte 170 notes, dont 3 actives. Le lot ne pose que
 * les actives, mais un défaut de filtre — il y en a eu — tapisserait son bureau
 * de 170 carrés qu'il faudrait retirer un par un. Un plafond qui refuse est
 * réparable en un geste ; un bureau enseveli, non. */
var PLAFOND = 24;

/* Les emplacements d'une grille, dans l'ordre de lecture : on remplit une
 * colonne vers le bas, puis la colonne suivante. Pourquoi en colonnes et non en
 * lignes : un bureau est plus large que haut, et les icônes vivent en haut à
 * gauche — commencer par descendre laisse la zone des icônes tranquille plus
 * longtemps.
 *
 * `zone` est la surface utile, en pixels : { largeur, hauteur }. L'origine est
 * relative, le conteneur appliquant son propre décalage. */
function emplacements(combien, zone, options) {
    var o = options || {};
    var larg = o.largeur || LARGEUR;
    var haut = o.hauteur || HAUTEUR;
    var marge = o.marge === undefined ? MARGE : o.marge;
    var parColonne = Math.max(1, Math.floor((zone.hauteur - marge) / (haut + marge)));
    var colonnes = Math.max(1, Math.floor((zone.largeur - marge) / (larg + marge)));
    var tiennent = Math.min(combien, parColonne * colonnes);
    var out = [];
    for (var i = 0; i < tiennent; i++) {
        var col = Math.floor(i / parColonne);
        var lig = i % parColonne;
        out.push({
            x: marge + col * (larg + marge),
            y: marge + lig * (haut + marge),
            largeur: larg,
            hauteur: haut
        });
    }
    return out;
}

/* Combien de carrés la zone peut porter. Sert à refuser AVANT de poser, plutôt
 * qu'à empiler des carrés hors de l'écran — un carré qu'on ne voit pas est un
 * carré qu'on ne peut pas retirer. */
function capacite(zone, options) {
    return emplacements(PLAFOND * 4, zone, options).length;
}

/* Le premier emplacement libre, pour une pose à l'unité.
 *
 * `occupes` : les géométries des carrés déjà là ({x, y}), telles que Plasma les
 * rend — donc décalées. ⚠️ On compare donc à une TOLÉRANCE, jamais à l'égalité :
 * le décalage du conteneur ferait passer chaque emplacement pour libre, et tous
 * les carrés s'empileraient au même endroit.
 *
 * Rend null quand la grille est pleine : l'appelant doit le DIRE, pas poser
 * quelque part au hasard. */
function premierLibre(occupes, zone, options) {
    var o = options || {};
    var tol = o.tolerance === undefined ? 40 : o.tolerance;
    var grille = emplacements(PLAFOND * 4, zone, options);
    for (var i = 0; i < grille.length; i++) {
        var place = grille[i];
        var pris = false;
        for (var j = 0; j < (occupes || []).length; j++) {
            var c = occupes[j];
            if (Math.abs((c.x || 0) - place.x) <= tol
                    && Math.abs((c.y || 0) - place.y) <= tol) {
                pris = true;
                break;
            }
        }
        if (!pris) {
            return place;
        }
    }
    return null;
}

/* Combien de VRAIS carrés le bureau porte.
 *
 * ⚠️ Ni l'instance liste (`noteId` 0) ni un carré neuf (`noteId` négatif) n'en
 * sont : le plafond comptait la liste, donc la vraie limite était 23 et non 24.
 * Sans conséquence grave, mais un plafond qui ment sur son propre chiffre finit
 * par refuser une pose que personne ne comprend. */
function compteCarres(carres) {
    var n = 0;
    for (var i = 0; i < (carres || []).length; i++) {
        if (carres[i] && carres[i].noteId > 0) {
            n += 1;
        }
    }
    return n;
}

/* Le rangement : à quelle place va chaque carré existant.
 *
 * L'ordre est STABLE et vient des notes, pas de l'ordre de pose : deux
 * rangements de suite donnent le même damier, sinon chaque rangement
 * mélangerait le bureau. Les carrés dont la note a disparu de la portée sont
 * rendus à part, pour que l'appelant les retire au lieu de leur garder une
 * place.
 *
 * `carres` : [{ id, noteId }]. `ordreNotes` : les identifiants de notes dans
 * l'ordre où le widget les affiche.
 */
function rangement(carres, ordreNotes, zone, options) {
    var rang = {};
    for (var i = 0; i < (ordreNotes || []).length; i++) {
        rang[ordreNotes[i]] = i;
    }
    /* `orphelins` ne se remplit plus que d'un cas : les carrés qui ne tiennent
     * pas dans la zone utile, et pour lesquels l'appelant ne fait rien de plus
     * que les laisser où ils sont. Voir plus bas. */
    var vivants = [], orphelins = [], intouchables = [];
    for (var j = 0; j < (carres || []).length; j++) {
        var c = carres[j];
        /* 🔴 UN WIDGET EN MODE LISTE N'EST PAS UN CARRÉ.
         *
         * Son `noteId` vaut 0 : il n'est lié à aucune note, donc il tombait
         * dans « orphelins » et le rangement le RETIRAIT. Le 2026-11-03, le
         * widget d'Olivier a ainsi été détruit au moment même où il cliquait
         * « ranger » — la liste depuis laquelle on commande le rangement était
         * la première victime, et sa configuration est partie avec elle.
         *
         * On ne range ni ne retire que ce qui est un carré. Le reste est
         * INTOUCHABLE : il garde sa place, qu'on ne lui a pas donnée. */
        if (!c.noteId) {
            intouchables.push(c);
            continue;
        }
        /* 🔴 Et un carré NEUF non plus n'est pas rangeable.
         *
         * Son `noteId` est négatif : sa note n'existe pas encore, donc le texte
         * qu'on vient d'y taper ne vit qu'en mémoire — ni dans la note, ni dans
         * la file d'envoi tant qu'on n'a pas marqué de pause. Or ranger veut
         * dire RETIRER puis reposer. Le classer « orphelin » (son identifiant
         * n'est dans aucun ordre) l'aurait fait supprimer avec sa phrase, et
         * c'est exactement l'incident du 2026-11-03 sous un autre visage. */
        if (c.noteId < 0) {
            intouchables.push(c);
            continue;
        }
        /* 🔴 UN CARRÉ DONT LA NOTE N'EST PAS DANS LA LISTE N'EST PLUS RETIRÉ.
         *
         * « Pas dans la liste » ne veut pas dire « n'existe plus ». Ça veut
         * dire « pas dans ce que CETTE instance sait », et trois chemins y
         * mènent sans que la note ait bougé : une page de pagination qui
         * échoue (le cache ne porte que les cinquante premières), le réglage
         * « montrer les notes partagées » qu'on décoche alors qu'un carré porte
         * la note d'un collègue, et une réponse tardive qui remet le drapeau de
         * tirage à faux. Dans ces trois cas, le rangement DÉTRUISAIT un carré
         * vivant avec sa configuration, en silence.
         *
         * Trois incidents de cette tâche viennent du même geste — retirer un
         * applet qu'on croit mort. On ne le fait plus : un carré inconnu garde
         * sa place, et c'est la personne qui le ferme par le « × » quand son
         * carré lui dit « cette note a disparu ». Le bureau est à elle. */
        if (rang[c.noteId] === undefined) {
            intouchables.push(c);
        } else {
            vivants.push(c);
        }
    }
    vivants.sort(function (a, b) {
        var d = rang[a.noteId] - rang[b.noteId];
        /* À rang égal — deux carrés sur la même note — l'identifiant tranche,
         * pour que l'ordre ne dépende pas de l'algorithme de tri. */
        return d !== 0 ? d : (a.id - b.id);
    });
    /* ⚠️ Les intouchables occupent des places : les carrés rangés doivent les
     * éviter, sinon le rangement empilerait un carré sur la liste. On réserve
     * donc leurs places et on range dans celles qui restent. */
    var toutes = emplacements(PLAFOND * 4, zone, options);
    var libres = [];
    for (var p = 0; p < toutes.length; p++) {
        var prise = false;
        for (var q = 0; q < intouchables.length; q++) {
            var it = intouchables[q];
            var tol = (options || {}).tolerance === undefined
                ? 40 : (options || {}).tolerance;
            if (Math.abs((it.x || 0) - toutes[p].x) <= tol
                    && Math.abs((it.y || 0) - toutes[p].y) <= tol) {
                prise = true;
                break;
            }
        }
        if (!prise) {
            libres.push(toutes[p]);
        }
    }
    var places = libres.slice(0, vivants.length);
    var plan = [];
    for (var k = 0; k < vivants.length; k++) {
        if (k >= places.length) {
            /* Au-delà de ce que la zone porte : ne pas poser hors de l'écran.
             * ⚠️ On ne les RETIRE pas non plus — on les laisse où ils sont. */
            orphelins.push(vivants[k]);
            continue;
        }
        plan.push({ carre: vivants[k], place: places[k] });
    }
    /* 🔴 `aRetirer` est VIDE, et le restera. Le nom survit parce que
     * l'appelant le lit encore, mais plus rien ne le remplit : le rangement
     * déplace, il ne détruit pas. Ce qui ne tient pas dans la zone est rendu
     * dans `horsZone`, pour être DIT et non retiré. */
    return { plan: plan, aRetirer: [], horsZone: orphelins,
             intouchables: intouchables };
}

/* 🔴 Les clés de configuration qu'un carré emporte quand on le retire et le
 * repose. Ranger un carré le RECRÉE : toute clé absente de cette liste serait
 * perdue en silence, à chaque rangement — `outbox` en premier, qui porte les
 * notes tapées hors ligne.
 *
 * C'est le motif qui s'est reposé cinq fois dans cette tâche : une liste d'un
 * côté, une déclaration de l'autre. `test_grille.js` compare donc cette liste
 * au vrai `config/main.xml`, et rougit sur toute entrée ajoutée là-bas. */
var CLES_A_EMPORTER = ["server", "showShared", "noteId",
                       "autoPoseFait", "adopteUuid"];

/* 🔴 DEUX clés ne voyagent JAMAIS, et ce ne sont pas des oublis.
 *
 * Le rangement passe par `qdbus … evaluateScript "<script>"`, donc par une
 * LIGNE DE COMMANDE : tout ce que le script porte est lisible dans
 * /proc/<pid>/cmdline par n'importe quel compte du poste.
 *
 *   `token`  — le recopier par cette voie annulerait d'un coup le lot 5a, qui
 *              l'avait justement sorti de la configuration pour cette raison.
 *   `outbox` — elle porte le TEXTE des notes tapées hors ligne. Ce n'est pas un
 *              secret de sécurité, c'est quelque chose de plus simple : les
 *              notes de quelqu'un n'ont rien à faire dans la table des
 *              processus d'un poste partagé.
 *
 * Conséquences, toutes deux assumées et dites à l'écran :
 *
 *   — le jeton : sur un poste AVEC portefeuille, il vient du coffre et le carré
 *     reposé le retrouve seul. Sans portefeuille, la configuration est son seul
 *     magasin et un rangement le perdrait : le rangement s'y REFUSE.
 *   — la file : un rangement ne doit pas emporter une note qui n'est pas
 *     partie, donc il ATTEND qu'elle soit partie. Le widget vide sa file en
 *     continu dès que le serveur répond : il suffit de réessayer. */
var CLES_JAMAIS_EN_LIGNE_DE_COMMANDE = ["token", "outbox"];

/* Peut-on ranger sans rien perdre ? Rend "" si oui, sinon la raison.
 *
 * Refuser est le seul comportement honnête : un rangement qui dépairerait les
 * carrés ou perdrait une note non envoyée serait bien pire que des carrés mal
 * placés. Et c'est réparable tout seul — une file se vide. */
function rangementPossible(etat) {
    var e = etat || {};
    if (!e.coffrePret) {
        return "sans_coffre";
    }
    if (e.filesEnAttente) {
        return "file_en_attente";
    }
    /* 🔴 LE CACHE DOIT ÊTRE DIGNE DE FOI, et ce n'est pas une précaution de
     * confort : le rangement RETIRE les carrés dont la note n'est pas dans la
     * liste qu'on lui donne. Or `notesById` ne vit qu'en mémoire — rien n'en
     * est rangé dans la configuration. Au redémarrage de plasmashell avec le
     * serveur injoignable, la liste est VIDE, donc tout carré est « orphelin »,
     * donc tous les carrés du bureau partent avec leur configuration, d'un seul
     * clic. C'est l'incident du 2026-11-03 sous un troisième visage, trouvé par
     * la relecture adverse du 11-08 et reproduit : RETIRER = [7,8], PLAN = [].
     *
     * Trois refus, et chacun a sa phrase :
     *   hors ligne  — on ne sait pas ce qui existe encore ;
     *   cache vide  — on ne sait rien du tout ;
     *   en vol      — on ne sait que la première page (le tirage pagine à 50).
     */
    if (e.horsLigne) {
        return "hors_ligne";
    }
    if (!e.combienDeNotes) {
        return "cache_vide";
    }
    if (e.tirageEnCours) {
        return "tirage_en_cours";
    }
    return "";
}

function configAEmporter(lire) {
    var out = {};
    for (var i = 0; i < CLES_A_EMPORTER.length; i++) {
        out[CLES_A_EMPORTER[i]] = lire(CLES_A_EMPORTER[i]);
    }
    return out;
}
