/*
 * Symbifox Sticky Notes — the server side of the widget.
 *
 * Everything that talks to Odoo lives here, and nothing here knows about QML.
 * Two routes carry the whole synchronisation:
 *
 *   GET  notes/stamp?shared=1   the cheap probe: last change + count
 *   GET  notes?since=…&shared=1 the delta, archived notes included
 *   GET  notes/ids?shared=1     every id in scope, to drop what left it
 *
 * ⚠️ The widget NEVER decides what it may see. It asks, and the record rules
 * of the notepad module answer. A note that comes back with `mine: false` is
 * displayed read-only because the server would refuse the write anyway.
 */
.pragma library

/* La version du widget, annoncée au serveur sur chaque appel authentifié.
 *
 * 🔴 Pourquoi elle existe : `app_version` n'était écrit qu'à l'APPAIRAGE, donc
 * « Mes appareils » affichait pour toujours la version qui avait apparié le
 * poste — 1.0.5 chez Olivier alors que le disque portait 1.5.0. Un champ figé
 * qui a l'air vivant est pire qu'un champ absent : je m'en suis servi comme
 * preuve que mes poses n'avaient pas été chargées, ce qu'il ne pouvait pas
 * dire.
 *
 * ⚠️ Une variable de MODULE dans un fichier `.pragma library`, donc partagée
 * par toutes les instances du moteur QML. C'est sûr ici, et seulement ici :
 * toutes les instances viennent du même paquet, donc portent la même version.
 * Rien d'autre ne doit suivre ce chemin — un jeton ou une adresse diffèrent
 * d'une instance à l'autre. */
var APP_VERSION = "";

function setAppVersion(v) {
    APP_VERSION = String(v || "");
}

var PAIRING_PATH = "/bf_desktop_pairing/v1";
var NOTES_PATH = "/bf_bloc_notes/mobile/v1";

/* How long a single call may take before we call it dead.
 *
 * 🔴 `xhr.timeout` does NOT exist in QML's XMLHttpRequest: assigning it just
 * creates a JavaScript property nobody reads. A half-open connection — a
 * laptop leaving the wifi, a dropped VPN, a server that stopped answering —
 * would then never come back as `status === 0`, so the widget would never
 * switch to offline, would keep its 3-second cadence, and would pile up one
 * request every 3 seconds for ever. The caller must therefore arm its own
 * timer and call `abort()`; `giveUp` below is what it uses. */
var TIMEOUT_MS = 15000;

function trimServer(server) {
    return (server || "").trim().replace(/\/+$/, "");
}

/* One place that builds every request, so no caller can forget the bearer
 * token, the timeout or the no-store. */
function call(opts) {
    var server = trimServer(opts.server);
    if (!server) {
        opts.onError({ error: "no_server" });
        return null;
    }
    var xhr = new XMLHttpRequest();
    var url = server + opts.path;
    var fini = false;
    xhr.open(opts.method || "GET", url, true);
    if (opts.token) {
        xhr.setRequestHeader("Authorization", "Bearer " + opts.token);
        /* Avec le jeton, jamais sans : le serveur ne saurait pas de quel
         * appareil il s'agit, et l'en-tête ne servirait qu'à se décrire à un
         * inconnu. */
        if (APP_VERSION) {
            xhr.setRequestHeader("X-Symbifox-App-Version", APP_VERSION);
        }
    }
    if (opts.body !== undefined) {
        xhr.setRequestHeader("Content-Type", "application/json");
    }
    /* The caller arms a timer and calls this when it fires. Aborting makes
     * `onreadystatechange` fire with status 0, which is already handled as
     * « offline » — so there is nothing else to unwind. */
    xhr.giveUp = function () {
        if (!fini) {
            xhr.abort();
        }
    };

    xhr.onreadystatechange = function () {
        if (xhr.readyState !== XMLHttpRequest.DONE) {
            return;
        }
        fini = true;
        /* status 0 means the request never completed: no network, DNS, TLS,
         * or the timeout above. It is NOT an authentication problem, and
         * treating it as one would unpair a widget every time a laptop
         * sleeps. */
        if (xhr.status === 0) {
            opts.onError({ error: "offline" });
            return;
        }
        var data = null;
        var lisible = true;
        try {
            data = JSON.parse(xhr.responseText);
        } catch (e) {
            data = null;
            lisible = false;
        }
        /* 🔴 Un 200 ILLISIBLE est une erreur, jamais une réponse vide. Un
         * portail captif, une page d'erreur de proxy ou une réponse tronquée
         * arrivent en 200 avec du HTML : le prendre pour `{}` faisait passer
         * une liste d'identifiants vide, et la passe de ménage effaçait TOUT
         * le bureau — définitivement, la marque d'eau ayant déjà avancé. */
        if (xhr.status >= 200 && xhr.status < 300 && !lisible) {
            opts.onError({ error: "bad_payload", status: xhr.status });
            return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
            /* ⚠️ A 2xx that carries an `error` field is NOT a success. The
             * pairing poll answers 202 « authorization_pending » while it
             * waits, and reading that as a token would hand the widget an
             * undefined secret and call itself paired. */
            if (data && data.error) {
                opts.onError({ error: data.error, status: xhr.status,
                               interval: data.interval, detail: data.detail });
            } else {
                opts.onSuccess(data || {});
            }
        } else if (xhr.status === 401) {
            /* The token is gone: revoked in « Mes appareils », the account was
             * archived, or 90 days without a call. The widget must offer to
             * pair again, not retry forever. */
            opts.onError({ error: "unauthorized", status: 401 });
        } else {
            opts.onError({
                error: (data && data.error) || "http_" + xhr.status,
                status: xhr.status,
                detail: data && data.detail,
                /* 🔴 La copie du serveur, recopiée. Le refus de conflit rend
                 * `{"error": "conflict", "note": …}` et toute la sémantique
                 * annoncée de la v1 — « on refuse, le serveur renvoie sa
                 * copie, on l'affiche » — tenait à cette clé. Elle n'était pas
                 * reprise ici, donc la branche qui l'affiche était morte et la
                 * copie n'arrivait que par le delta suivant, par chance. */
                note: data && data.note
            });
        }
    };
    xhr.send(opts.body !== undefined ? JSON.stringify(opts.body) : null);
    return xhr;
}

/* ── Pairing (RFC 8628) ─────────────────────────────────────────────────── */

/* Qui répond à cette adresse, sous quel nom canonique, à quelles couleurs.
 *
 * Aucun jeton : c'est précisément ce qu'on demande AVANT d'apparier, pour que
 * la personne voie à qui elle s'apparie, et pour que le widget sache si
 * l'adresse qu'on lui a tapée est bien celle que l'instance annonce. */
function identity(server, onSuccess, onError) {
    return call({
        server: server,
        path: PAIRING_PATH + "/ping",
        onSuccess: onSuccess,
        onError: onError
    });
}

function startPairing(server, deviceName, version, onSuccess, onError) {
    return call({
        server: server,
        path: PAIRING_PATH + "/auth/device/start",
        method: "POST",
        body: { app: "stickynotes", name: deviceName, version: version },
        onSuccess: onSuccess,
        onError: onError
    });
}

function pollPairing(server, deviceCode, deviceSecret, onSuccess, onPending, onError) {
    return call({
        server: server,
        path: PAIRING_PATH + "/auth/device/poll",
        method: "POST",
        body: { device_code: deviceCode, device_secret: deviceSecret },
        onSuccess: onSuccess,
        onError: function (err) {
            /* 202 authorization_pending and 429 slow_down are both « keep
             * waiting », not failures. Anything else ends the attempt. */
            if (err.error === "authorization_pending" || err.error === "slow_down") {
                onPending(err);
            } else {
                onError(err);
            }
        }
    });
}

/* The notepad announces what this tenant can do. A widget pointed at an older
 * tenant must degrade instead of calling routes that are not there. */
function capabilities(server, token, onSuccess, onError) {
    return call({
        server: server,
        path: NOTES_PATH + "/ping",
        onSuccess: onSuccess,
        onError: onError
    });
}

/* ── Reading ────────────────────────────────────────────────────────────── */

/* The probe carries the watermark: the server, not the widget, decides
 * whether anything is newer. See store.js shouldPull. */
function stamp(server, token, shared, since, onSuccess, onError) {
    var q = [];
    if (shared) { q.push("shared=1"); }
    if (since) { q.push("since=" + encodeURIComponent(since)); }
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes/stamp" + (q.length ? "?" + q.join("&") : ""),
        onSuccess: onSuccess, onError: onError
    });
}

function list(server, token, opts, onSuccess, onError) {
    var q = [];
    if (opts.since) { q.push("since=" + encodeURIComponent(opts.since)); }
    if (opts.shared) { q.push("shared=1"); }
    if (opts.limit) { q.push("limit=" + opts.limit); }
    if (opts.offset) { q.push("offset=" + opts.offset); }
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes" + (q.length ? "?" + q.join("&") : ""),
        onSuccess: onSuccess, onError: onError
    });
}

/* Une seule note, par son identifiant.
 *
 * C'est le chemin du mode « un carré, une note » : un carré posé sur le bureau
 * n'a besoin ni du delta, ni de la sonde, ni de la passe de ménage. Avec cinq
 * carrés, faire tourner le cycle complet dans chacun voudrait dire cinq
 * synchronisations entières du bureau, toutes les trois secondes. Ici, une
 * requête rend une note.
 *
 * ⚠️ Un 404 n'est pas une panne : c'est une note supprimée, ou qu'un collègue
 * a cessé de partager. Le carré doit le dire, pas afficher une note périmée
 * pour toujours.
 */
function one(server, token, noteId, onSuccess, onError) {
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes/" + encodeURIComponent(noteId),
        onSuccess: onSuccess, onError: onError
    });
}

function visibleIds(server, token, shared, onSuccess, onError) {
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes/ids" + (shared ? "?shared=1" : ""),
        onSuccess: onSuccess, onError: onError
    });
}

/* ── Writing ────────────────────────────────────────────────────────────── */

/* `clientUuid` makes creation idempotent: a reply lost on the way back does
 * not create a second note when the widget retries. */
/* ⚠️ `couleur` est en DERNIER, après les rappels, et ce n'est pas de
 * l'élégance : la glisser avant aurait décalé tous les appels existants, y
 * compris ceux des essais, en silence. Un paramètre ajouté à la fin ne casse
 * personne. */
function create(server, token, text, clientUuid, onSuccess, onError, couleur) {
    var corps = { text: text, client_uuid: clientUuid };
    if (couleur) {
        corps.color_hex = couleur;
    }
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes",
        method: "POST",
        body: corps,
        onSuccess: onSuccess, onError: onError
    });
}

/* `writeDate` is what the server compares to detect a conflict: pass the one
 * the widget had when the person started typing, never a fresher one. */
function update(server, token, noteId, vals, writeDate, onSuccess, onConflict, onError) {
    var body = {};
    for (var k in vals) { body[k] = vals[k]; }
    body.write_date = writeDate;
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes/" + noteId,
        method: "POST",
        body: body,
        onSuccess: onSuccess,
        onError: function (err) {
            if (err.status === 409) {
                onConflict(err);
            } else {
                onError(err);
            }
        }
    });
}

function action(server, token, noteId, name, params, onSuccess, onError) {
    var body = { action: name };
    for (var k in (params || {})) { body[k] = params[k]; }
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/notes/" + noteId + "/action",
        method: "POST",
        body: body,
        onSuccess: onSuccess, onError: onError
    });
}

/* ── « En faire une tâche » et « Relier à une fiche » ──────────────────────
 *
 * Les deux gestes du bloc-notes qui exigent de CHOISIR quelque chose, et donc
 * une liste venue du serveur : les 9 actions existaient côté serveur depuis le
 * début, ces deux-là attendaient seulement leur sélecteur (BF #27250).
 */

/* Les projets où poser la tâche. Sans recherche, le serveur rend ceux où la
 * personne a eu une tâche récemment, puis ses favoris — jamais la liste
 * alphabétique complète, qui n'aide personne. */
/* Les préférences de la personne, et avec elles les nuanciers du locataire.
 *
 * ⚠️ La route sert à la fois la mise en page du bloc-notes et les couleurs. Le
 * widget n'en lit que les couleurs ; demander moins n'est pas possible, et
 * c'est un seul aller-retour par session. */
function prefs(server, token, onSuccess, onError) {
    return call({
        server: server, token: token, path: NOTES_PATH + "/prefs",
        onSuccess: onSuccess, onError: onError
    });
}

function projets(server, token, q, onSuccess, onError) {
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/projets" + (q ? "?q=" + encodeURIComponent(q) : ""),
        onSuccess: onSuccess, onError: onError
    });
}

/* Les fiches auxquelles renvoyer la note, groupées par modèle.
 *
 * ⚠️ Le serveur rend une liste VIDE sous deux caractères de recherche, et ce
 * n'est pas une panne : `search_targets` refuse une requête trop courte. Mesuré
 * le 2026-11-03 — un `/cibles` sans `q` rend `{"groupes": []}`, ce qui peut
 * passer pour un serveur muet. L'appelant ne doit donc pas demander avant
 * d'avoir deux caractères.
 */
var CIBLES_MIN = 2;

function cibles(server, token, q, onSuccess, onError) {
    var texte = String(q || "").trim();
    if (texte.length < CIBLES_MIN) {
        /* Rendre la réponse vide SANS appeler : la question n'a pas de sens
         * encore, et un aller-retour pour une liste vide est un aller-retour de
         * trop sur une frappe. */
        onSuccess({ ok: true, groupes: [] });
        return null;
    }
    return call({
        server: server, token: token,
        path: NOTES_PATH + "/cibles?q=" + encodeURIComponent(texte),
        onSuccess: onSuccess, onError: onError
    });
}
