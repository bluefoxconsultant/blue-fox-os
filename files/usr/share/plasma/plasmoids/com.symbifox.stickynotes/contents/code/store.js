/*
 * Symbifox Sticky Notes — what the widget holds between two calls.
 *
 * Pure functions over plain objects: no QML, no network, no clock of its own.
 * That is what makes the synchronisation rules testable by reading them.
 *
 * The model is a map id → note plus a `since` watermark. Three operations
 * keep it true to the server:
 *
 *   applyDelta   what `?since=` returned, inserted or replaced
 *   pruneTo      ids the server still shows; everything else is forgotten
 *   sortedNotes  the display order, mine first, then what colleagues shared
 */
.pragma library

/* ── Delta ──────────────────────────────────────────────────────────────── */

/* Merge what the server just sent into `byId`, and move the watermark.
 *
 * ⚠️ The watermark is `next_since`, which the server computes for us, and
 * never anything else:
 *
 *   - not the newest note we happened to receive: a note written during the
 *     request would sit just after our mark and never be asked for again;
 *   - not the LOCAL clock: a workstation a minute ahead would skip a minute
 *     of other people's writes, in silence;
 *   - not even the server's own `server_time`: Odoo truncates write dates to
 *     the second, so a note written during that very second compares EQUAL to
 *     it and `write_date > since` misses it. `next_since` is that instant
 *     pushed back by a small overlap, which is the whole point.
 */
function applyDelta(byId, payload, derniere) {
    /* `derniere` dit si cette page est la dernière du delta.
     *
     * 🔴 La marque d'eau ne doit avancer qu'alors. Le serveur plafonne la
     * liste : avancer la marque après une page PLEINE abandonnait tout ce qui
     * suivait — une première synchronisation de plus de cent notes n'en
     * montrait que cent, et une passe de masse (réétiquetage, archivage en
     * lot) perdait le reste des changements pour toujours. */
    var notes = (payload && payload.notes) || [];
    for (var i = 0; i < notes.length; i++) {
        var note = notes[i];
        /* An archived note leaves the desktop: that is how archiving on the
         * phone or in Odoo reaches the widget. It still counts as « seen », so
         * it must not come back on the next delta. */
        if (note.active === false) {
            delete byId[note.id];
        } else {
            byId[note.id] = note;
        }
    }
    if (!derniere) {
        return null;
    }
    /* Fall back to `server_time` only for a tenant too old to send
     * `next_since`: better a watermark with a known hole than none at all. */
    return (payload && (payload.next_since || payload.server_time)) || null;
}

/* Une page est pleine : il en reste donc probablement d'autres. */
function pageComplete(payload, limite) {
    var notes = (payload && payload.notes) || [];
    return notes.length >= limite;
}

/* Drop what the server no longer shows.
 *
 * 🔴 The one case a delta cannot express. A note that leaves our scope —
 * a colleague stops sharing it, somebody deletes it, it is reassigned —
 * simply stops being readable, so no delta ever mentions it again. Without
 * this pass it would stay on the desktop for good, showing text the person
 * is no longer allowed to see.
 */
function pruneTo(byId, visibleIds) {
    /* 🔴 Rien n'est effacé sur autre chose qu'un véritable tableau. Une
     * réponse mal formée, ou une liste tronquée par le plafond du serveur, ne
     * dit PAS « ces notes n'existent plus » : purger là-dessus viderait le
     * bureau sans retour. */
    if (!Array.isArray(visibleIds)) {
        return -1;
    }
    var keep = {};
    for (var i = 0; i < visibleIds.length; i++) {
        keep[visibleIds[i]] = true;
    }
    var dropped = 0;
    for (var id in byId) {
        /* Object keys are strings; the ids from the server are numbers. */
        if (!keep[parseInt(id, 10)]) {
            delete byId[id];
            dropped++;
        }
    }
    return dropped;
}

/* ── Display ────────────────────────────────────────────────────────────── */

/* Mine first — pinned ahead, then most recently written — and the notes
 * colleagues shared in their own group after them. Olivier's arbitration:
 * shared notes are visible, read-only, and never mixed in with one's own.
 */
function sortedNotes(byId) {
    var mine = [];
    var shared = [];
    for (var id in byId) {
        var note = byId[id];
        (note.mine === false ? shared : mine).push(note);
    }
    var byPinThenDate = function (a, b) {
        if (!!a.pinned !== !!b.pinned) {
            return a.pinned ? -1 : 1;
        }
        var da = a.write_date || "";
        var db = b.write_date || "";
        if (da !== db) {
            return da < db ? 1 : -1;
        }
        return b.id - a.id;
    };
    mine.sort(byPinThenDate);
    shared.sort(byPinThenDate);
    return mine.concat(shared);
}

/* True when the delta is worth pulling.
 *
 * ⚠️ `changed` is the SERVER's answer to « anything newer than my watermark »,
 * not a comparison we make here. We only add the count, because a deletion
 * moves no write date at all and the server cannot report it as « newer ».
 *
 * A tenant too old to answer `changed` sends only `stamp`; comparing stamps
 * then is the best available, and it is why the field is still read. */
function shouldPull(previous, fresh) {
    if (!previous) {
        return true;
    }
    if (fresh.changed !== undefined) {
        return fresh.changed || previous.count !== fresh.count;
    }
    return previous.stamp !== fresh.stamp || previous.count !== fresh.count;
}

/* ── Outbox ─────────────────────────────────────────────────────────────── */

/* Notes typed while the server was unreachable. Creation only: editing a note
 * offline would need a merge rule, and v1 deliberately has none (#27250).
 *
 * Each entry carries its own uuid, so replaying the queue twice — a reply lost
 * on the way back, a widget restarted mid-flush — cannot create duplicates:
 * the server is idempotent on `client_uuid`.
 */
function queueCreate(outbox, text, uuid, couleur) {
    var entree = { uuid: uuid, text: text, tries: 0 };
    /* La couleur voyage AVEC la note : une note tapée hors ligne doit arriver
     * de la même couleur que celle qu'on voyait sur le bureau en la tapant.
     * ⚠️ Absente plutôt que vide : une clé vide vaut « retire la couleur »
     * côté serveur, ce qui n'est pas la même chose que « n'en parle pas ». */
    if (couleur) {
        entree.couleur = couleur;
    }
    outbox.push(entree);
    return outbox;
}

/* La file se range dans la configuration du widget, et pas seulement en
 * mémoire.
 *
 * 🔴 Sans cela, une note tapée hors ligne disparaissait à la fermeture de la
 * session, au redémarrage de plasmashell ou au simple déplacement du widget
 * d'un panneau à l'autre — sans un mot. Du texte que la personne croyait
 * enregistré. */
/* Changer le texte d'une note qui attend encore de partir.
 *
 * 🔴 Pourquoi cela existe : dans un carré NEUF, on tape, la pause crée la note,
 * et on continue de taper avant que le serveur n'ait répondu. Sans ce chemin,
 * la seconde pause créait une SECONDE note — une phrase coupée en deux notes,
 * et le carré n'en adoptait qu'une. Tant que l'entrée n'est pas partie, la
 * corriger sur place est à la fois juste et gratuit.
 *
 * Rend vrai si l'entrée a été retrouvée : l'appelant doit savoir qu'il n'a PAS
 * à créer, sinon on retombe dans le défaut qu'on vient de fermer. */
function retexte(outbox, uuid, texte) {
    for (var i = 0; i < (outbox || []).length; i++) {
        if (outbox[i].uuid === uuid) {
            outbox[i].text = String(texte === undefined || texte === null ? "" : texte).trim();
            return true;
        }
    }
    return false;
}

function serialiseOutbox(outbox) {
    try {
        return JSON.stringify(outbox || []);
    } catch (e) {
        return "[]";
    }
}

function parseOutbox(raw) {
    try {
        var lu = JSON.parse(raw || "[]");
        return Array.isArray(lu) ? lu : [];
    } catch (e) {
        return [];
    }
}

/* Give up on an entry the server keeps refusing, and say so rather than
 * dropping the person's text in silence. */
var MAX_TRIES = 5;

function nextToFlush(outbox) {
    for (var i = 0; i < outbox.length; i++) {
        if (outbox[i].tries < MAX_TRIES) {
            return outbox[i];
        }
    }
    return null;
}

/* Un échec PASSAGER ne doit pas consommer un essai définitif : cinq erreurs
 * 500 pendant un redémarrage d'Odoo suffisaient à bloquer une note pour de
 * bon. Seul un refus que le serveur répètera (une charge qu'il rejette) compte.
 */
function countsAsRefusal(err) {
    if (!err) {
        return false;
    }
    if (err.error === "offline" || err.error === "no_server"
            || err.error === "bad_payload") {
        return false;
    }
    /* 5xx : le serveur a un problème, pas notre note. */
    return !(err.status >= 500);
}

function forget(outbox, uuid) {
    for (var i = 0; i < outbox.length; i++) {
        if (outbox[i].uuid === uuid) {
            outbox.splice(i, 1);
            return true;
        }
    }
    return false;
}

function stuck(outbox) {
    var out = [];
    for (var i = 0; i < outbox.length; i++) {
        if (outbox[i].tries >= MAX_TRIES) {
            out.push(outbox[i]);
        }
    }
    return out;
}

/* ── Polling cadence ────────────────────────────────────────────────────── */

/* How long to wait before probing again.
 *
 * The widget is open and someone is using it: answer quickly. Folded away or
 * untouched for a while: slow right down. This is what buys « instant enough »
 * without a push channel, and the one knob that decides what the server pays
 * for the whole feature (#27250).
 */
var FAST_MS = 3000;
var IDLE_MS = 30000;
var OFFLINE_MS = 60000;
/* Un carré posé sur le bureau n'affiche qu'une note, et il est « déplié » en
 * permanence : la cadence rapide s'appliquerait donc tout le temps, à chaque
 * carré. Cinq carrés feraient cent requêtes par minute pour lire cinq notes
 * que personne ne regarde. Un carré au repos se contente donc de ceci — et
 * repasse en FAST_MS dès qu'on y tape, parce que là on attend une réponse. */
var PINNED_MS = 15000;

function nextDelay(state) {
    if (state.offline) {
        return OFFLINE_MS;
    }
    if (state.active) {
        return FAST_MS;
    }
    return state.pinned ? PINNED_MS : IDLE_MS;
}
