"""Bureau itinerant dans Nextcloud (#26870, 2026-10-24)."""
import email.utils
import io
import json
import os
import shutil
import subprocess
import types
import urllib.error
import urllib.parse

import pytest

from bfos_live import itinerant as it

CLE = bytes(range(32))


# ------------------------------------------------------------------ chiffrement
def aller(donnees, cle=CLE, **kw):
    e = io.BytesIO()
    it.chiffrer(io.BytesIO(donnees), e, cle, **kw)
    return e.getvalue()


def retour(chiffre, cle=CLE):
    s = io.BytesIO()
    it.dechiffrer(io.BytesIO(chiffre), s, cle)
    return s.getvalue()


@pytest.mark.parametrize("n", [0, 1, it.MORCEAU - 1, it.MORCEAU, it.MORCEAU + 1, 3 * it.MORCEAU + 7])
def test_aller_retour(n):
    d = os.urandom(n)
    assert retour(aller(d)) == d


def test_deux_chiffrements_differents():
    assert aller(b"x" * 100) != aller(b"x" * 100), "sel et nonce aleatoires"


def _blocs(c):
    entete = len(it.MAGIE) + 23
    taille = it.MORCEAU + it.ETIQUETTE
    corps = c[entete:]
    return c[:entete], [corps[i:i + taille] for i in range(0, len(corps), taille)]


@pytest.mark.parametrize("abime", ["octet", "tronque", "reordonne", "cle", "magie", "vide", "queue"])
def test_fichier_abime_refuse(abime):
    c = aller(os.urandom(3 * it.MORCEAU + 10))
    entete, blocs = _blocs(c)
    if abime == "octet":
        c = c[:500] + bytes([c[500] ^ 1]) + c[501:]
    elif abime == "tronque":                      # coupe a une frontiere de morceau
        c = entete + b"".join(blocs[:2])
    elif abime == "reordonne":
        c = entete + blocs[1] + blocs[0] + b"".join(blocs[2:])
    elif abime == "magie":
        c = b"BFOSNC2\n" + c[8:]
    elif abime == "vide":
        c = b""
    elif abime == "queue":
        c = c + b"\x00" * 40
    with pytest.raises(it.Echec):
        retour(c, cle=bytes(32) if abime == "cle" else CLE)


def test_plafond():
    with pytest.raises(it.TropGros):
        aller(os.urandom(3 * it.MORCEAU), plafond=2 * it.MORCEAU)


# ------------------------------------------------------------------ faux Nextcloud
class Reponse(io.BytesIO):
    def __init__(self, statut, corps=b"", entetes=None):
        super().__init__(corps)
        self.status = statut
        self.headers = entetes or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FauxDAV:
    """WebDAV en memoire : fichiers, etags, PUT conditionnels, COPY, MOVE,
    envoi par morceaux, PROPFIND. `pannes` : {methode+nom: statut} pour
    simuler un refus ; `passageres` : {methode+nom: [statuts]}, consommes un a
    un (un client de synchronisation qui tient le fichier un moment)."""

    def __init__(self, base="https://nc.exemple.test"):
        self.base, self.fichiers, self.etags, self.n = base, {}, {}, 0
        self.journal, self.pannes, self.morceaux = [], {}, {}
        self.passageres, self.dates, self.attentes = {}, {}, []
        self.mentent = set()   # methode+nom : l'operation est FAITE puis rendue 500

    def _chemin(self, url):
        return urllib.parse.unquote(urllib.parse.urlsplit(url).path)

    def _etag(self, chemin):
        self.n += 1
        self.etags[chemin] = f'"e{self.n}"'
        self.dates[chemin] = 1_800_000_000 + self.n   # chaque ecriture plus recente
        return self.etags[chemin]

    def open(self, req, timeout=None):
        m, chemin = req.get_method(), self._chemin(req.full_url)
        ent = {k.lower(): v for k, v in req.header_items()}
        self.journal.append((m, chemin))
        cle = m + " " + chemin.rsplit("/", 1)[-1]
        if m == "MOVE" and (cle in self.pannes or self.passageres.get(cle)) \
                and ent.get("overwrite", "T") != "F" and "destination" in ent:
            # Comme Nextcloud (Sabre httpMove) : la destination est effacee AVANT
            # le renommage, que le verrou de la source fait ensuite echouer.
            self.fichiers.pop(self._chemin(ent["destination"]), None)
        if cle in self.pannes:
            raise urllib.error.HTTPError(req.full_url, self.pannes[cle], "panne", {}, None)
        if self.passageres.get(cle):
            raise urllib.error.HTTPError(req.full_url, self.passageres[cle].pop(0), "tenu", {}, None)
        donnees = req.data.read() if hasattr(req.data, "read") else (req.data or b"")
        dest = self._chemin(ent["destination"]) if "destination" in ent else None
        if m == "MKCOL":
            if "/uploads/" in chemin:
                self.morceaux[chemin] = {}
                return Reponse(201)
            if chemin.rstrip("/") in self.fichiers:
                raise urllib.error.HTTPError(req.full_url, 405, "existe", {}, None)
            self.fichiers[chemin.rstrip("/")] = None
            return Reponse(201)
        if m == "HEAD":
            if chemin not in self.fichiers or self.fichiers[chemin] is None:
                raise urllib.error.HTTPError(req.full_url, 404, "absent", {}, None)
            return Reponse(200, b"", {"Content-Length": str(len(self.fichiers[chemin])),
                                      "ETag": self.etags.get(chemin, '"?"')})
        if m == "GET":
            if chemin not in self.fichiers or self.fichiers[chemin] is None:
                raise urllib.error.HTTPError(req.full_url, 404, "absent", {}, None)
            return Reponse(200, self.fichiers[chemin], {"ETag": self.etags[chemin]})
        if m == "PUT":
            if "/uploads/" in chemin:
                d, n = chemin.rsplit("/", 1)
                self.morceaux[d][n] = donnees
                return Reponse(200)
            if chemin.endswith(".part"):   # comme le vrai : « ".part" is a forbidden file type »
                raise urllib.error.HTTPError(req.full_url, 400, "InvalidPath", {}, None)
            existe = chemin in self.fichiers
            if ent.get("if-none-match") == "*" and existe:
                raise urllib.error.HTTPError(req.full_url, 412, "existe", {}, None)
            if "if-match" in ent and (not existe or self.etags.get(chemin) != ent["if-match"]):
                raise urllib.error.HTTPError(req.full_url, 412, "change", {}, None)
            self.fichiers[chemin] = donnees
            return Reponse(201 if not existe else 204, entetes={"ETag": self._etag(chemin)})
        if m in ("COPY", "MOVE"):
            if "/uploads/" in chemin:
                d = chemin.rsplit("/", 1)[0]
                self.fichiers[dest] = b"".join(v for _k, v in sorted(self.morceaux.pop(d).items()))
                self._etag(dest)
                return Reponse(201)
            if chemin not in self.fichiers:
                raise urllib.error.HTTPError(req.full_url, 404, "absent", {}, None)
            self.fichiers[dest] = self.fichiers[chemin]
            self._etag(dest)
            # COPY et MOVE gardent la date de la source (mesure sur Nextcloud le 2026-10-26).
            self.dates[dest] = self.dates.get(chemin, self.dates[dest])
            if m == "MOVE":
                del self.fichiers[chemin]
                self.dates.pop(chemin, None)
            if cle in self.mentent:
                self.mentent.discard(cle)
                raise urllib.error.HTTPError(req.full_url, 500, "fait mais rendu 500", {}, None)
            return Reponse(201)
        if m == "PROPFIND":
            dossier = chemin.rstrip("/")
            if dossier not in self.fichiers and not any(c.startswith(dossier + "/") for c in self.fichiers):
                raise urllib.error.HTTPError(req.full_url, 404, "absent", {}, None)
            rep = [f"<d:response><d:href>{urllib.parse.quote(dossier)}/</d:href><d:propstat><d:prop>"
                   "<d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>"]
            for c, v in self.fichiers.items():
                if v is None or c.rsplit("/", 1)[0] != dossier:
                    continue
                date = email.utils.formatdate(self.dates.get(c, 0), usegmt=True)
                rep.append(f"<d:response><d:href>{urllib.parse.quote(c)}</d:href><d:propstat><d:prop>"
                           f"<d:getcontentlength>{len(v)}</d:getcontentlength>"
                           f"<d:getlastmodified>{date}</d:getlastmodified><d:resourcetype/>"
                           "</d:prop></d:propstat></d:response>")
            corps = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(rep)
                     + "</d:multistatus>").encode()
            return Reponse(207, corps)
        if m == "DELETE":
            if "if-match" in ent and self.etags.get(chemin) != ent["if-match"]:
                raise urllib.error.HTTPError(req.full_url, 412, "change", {}, None)
            self.fichiers.pop(chemin, None)
            return Reponse(204)
        raise AssertionError(m)


DEPOT = {"url": "https://nc.exemple.test", "login": "jane doe", "app_password": "AP"}
RACINE = "/remote.php/dav/files/jane doe/.bfos-live"


def nuage(dav=None):
    dav = dav or FauxDAV()
    return it.Nuage(DEPOT, ouvreur=dav, attendre=dav.attentes.append), dav


def test_nuage_refuse_http_et_identifiants_vides():
    for d in ({"url": "http://nc.exemple.test", "login": "a", "app_password": "b"},
              {"url": "https://nc.exemple.test", "login": "", "app_password": "b"}, None):
        with pytest.raises(it.Echec):
            it.Nuage(d)


# ------------------------------------------------------------------ verrou
def test_verrou_libre_puis_tenu_par_une_autre_session():
    n, dav = nuage()
    t = [1000.0]
    etag = it.prendre_verrou(n, "A", maintenant=lambda: t[0], hote="pc-a")
    assert etag and json.loads(dav.fichiers[RACINE + "/verrou.json"])["session"] == "A"
    t[0] += 600
    with pytest.raises(it.Echec) as exc:
        it.prendre_verrou(n, "B", maintenant=lambda: t[0], hote="pc-b")
    assert "déjà ouvert sur un autre poste" in exc.value.message
    assert json.loads(dav.fichiers[RACINE + "/verrou.json"])["session"] == "A"


def test_verrou_perime_repris_et_ancienne_session_ecartee():
    n, dav = nuage()
    t = [1000.0]
    etag_a = it.prendre_verrou(n, "A", maintenant=lambda: t[0])
    t[0] += it.VERROU_PERIME + 1
    it.prendre_verrou(n, "B", maintenant=lambda: t[0])
    assert json.loads(dav.fichiers[RACINE + "/verrou.json"])["session"] == "B"
    with pytest.raises(it.Echec) as exc:
        it.renouveler_verrou(n, "A", etag_a, maintenant=lambda: t[0])
    assert "ouvert ailleurs" in exc.value.message


def test_verrou_renouvele_change_d_etag():
    n, dav = nuage()
    t = [1000.0]
    e1 = it.prendre_verrou(n, "A", maintenant=lambda: t[0])
    t[0] += 900
    e2 = it.renouveler_verrou(n, "A", e1, maintenant=lambda: t[0])
    assert e2 != e1 and json.loads(dav.fichiers[RACINE + "/verrou.json"])["vu"] == 1900


def test_verrou_rendu_seulement_le_sien():
    n, dav = nuage()
    e = it.prendre_verrou(n, "A")
    n.retirer_verrou('"autre"')
    assert RACINE + "/verrou.json" in dav.fichiers
    n.retirer_verrou(e)
    assert RACINE + "/verrou.json" not in dav.fichiers


# ------------------------------------------------------------------ archive reelle
needs_tar = pytest.mark.skipif(not (shutil.which("tar") and shutil.which("zstd")),
                               reason="tar et zstd requis")
SOI = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())


def popen_local(argv, **kw):
    """Les essais tournent sous un seul compte : on retire le changement d'uid."""
    for k in ("user", "group", "extra_groups"):
        kw.pop(k, None)
    return subprocess.Popen(argv, **kw)


def run_local(argv, **kw):
    if argv and argv[0] == "restorecon":
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return subprocess.run(argv, **kw)


def statvfs_large(_p):
    return types.SimpleNamespace(f_bavail=1 << 30, f_frsize=4096)


def maison_type(racine):
    m = racine / "maison"
    for chemin, contenu in {
        ".config/kdeglobals": "[General]\nColorScheme=BlueFox\n",
        ".local/share/notes.txt": "à garder\n",
        ".cache/gros.bin": "x" * 5000,
        ".local/share/Trash/files/vieux.txt": "corbeille",
        ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Preferences": "{}",
        ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Cache/data_0": "c",
        ".var/app/com.brave.Browser/cache/x": "c",
        "Nextcloud/enregistre-montage-tombe.txt": "local, a garder",
        "Documents/rapport.odt": "rapport",
    }.items():
        f = m / chemin
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(contenu)
    return m


def sauver(n, m, tmp, **kw):
    (tmp).mkdir(exist_ok=True)
    kw.setdefault("session_id", "S1")
    return it.sauvegarder(n, SOI, str(m), CLE, str(tmp), kw.pop("session_id"), popen=popen_local, **kw)


def ouvrir(n, maison, tmp):
    tmp.mkdir(exist_ok=True)
    return it.restaurer(n, SOI, str(maison), CLE, str(tmp), popen=popen_local, run=run_local,
                        statvfs=statvfs_large)


@needs_tar
def test_sauvegarde_puis_restauration_sans_les_caches(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    taille = sauver(n, m, tmp_path / "t", neuf=True)
    profil = dav.fichiers[RACINE + "/profil.bfosnc"]
    assert len(profil) == taille and profil.startswith(it.MAGIE)
    assert not [k for k in dav.fichiers if "envoi" in k]
    assert RACINE + "/bureau.json" in dav.fichiers, "marqueur pose au premier envoi"
    assert b"ColorScheme" not in profil, "chiffre"

    neuve = tmp_path / "neuve"
    neuve.mkdir()
    (neuve / "reste-d-une-autre-session.txt").write_text("vieux")
    assert ouvrir(n, neuve, tmp_path / "t2") == taille
    gardes = {str(p.relative_to(neuve)) for p in neuve.rglob("*") if p.is_file()}
    assert ".config/kdeglobals" in gardes and "Documents/rapport.odt" in gardes
    assert ".local/share/notes.txt" in gardes and "Nextcloud/enregistre-montage-tombe.txt" in gardes
    assert ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Preferences" in gardes
    assert "reste-d-une-autre-session.txt" not in gardes, "maison videe avant l'extraction"
    for jete in (".cache/gros.bin", ".local/share/Trash/files/vieux.txt",
                 ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser/Default/Cache/data_0",
                 ".var/app/com.brave.Browser/cache/x"):
        assert jete not in gardes, jete
    assert not os.listdir(tmp_path / "t2"), "rien ne traine"


@needs_tar
def test_thunderbird_sans_copie_imap_ni_index(tmp_path):
    """Arbitre le 2026-10-27 : le courriel se retelecharge, les comptes restent."""
    n, _dav = nuage()
    m = maison_type(tmp_path)
    tb = ".var/app/org.mozilla.thunderbird"
    for rel in (f"{tb}/.thunderbird/ab.default/prefs.js",
                f"{tb}/.thunderbird/ab.default/ImapMail/imap.example/INBOX",
                f"{tb}/.thunderbird/ab.default/global-messages-db.sqlite",
                f"{tb}/config/thunderbird/cd.default/abook.sqlite",
                f"{tb}/config/thunderbird/cd.default/ImapMail/imap.example/INBOX.msf"):
        (m / rel).parent.mkdir(parents=True, exist_ok=True)
        (m / rel).write_text("x")
    sauver(n, m, tmp_path / "t", neuf=True)
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    gardes = {str(p.relative_to(neuve)) for p in neuve.rglob("*") if p.is_file()}
    assert f"{tb}/.thunderbird/ab.default/prefs.js" in gardes
    assert f"{tb}/config/thunderbird/cd.default/abook.sqlite" in gardes
    assert not [g for g in gardes if "ImapMail" in g or "global-messages-db" in g], gardes


@needs_tar
def test_cachedir_tag_a_la_racine_ne_vide_pas_l_archive(tmp_path):
    n, _dav = nuage()
    m = maison_type(tmp_path)
    (m / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
    sauver(n, m, tmp_path / "t", neuf=True)
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "rapport.odt").exists()


@needs_tar
def test_precedent_est_le_bureau_tel_qu_ouvert(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    ouvert = dav.fichiers[RACINE + "/profil.bfosnc"]
    (m / "Documents" / "a.txt").write_text("a")
    sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    (m / "Documents" / "b.txt").write_text("b")
    sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=False)
    assert dav.fichiers[RACINE + "/profil.precedent.bfosnc"] == ouvert
    assert dav.fichiers[RACINE + "/profil.bfosnc"] != ouvert


@needs_tar
def test_envoi_rate_ne_touche_pas_au_bon_profil(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    bon = dav.fichiers[RACINE + "/profil.bfosnc"]
    dav.pannes["PUT profil.envoi-S9.bfosnc"] = 507
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", session_id="S9")
    assert dav.fichiers[RACINE + "/profil.bfosnc"] == bon


@needs_tar
def test_trop_gros_rien_n_est_envoye(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    (m / "Documents" / "gros.bin").write_bytes(os.urandom(3 * it.MORCEAU))
    with pytest.raises(it.TropGros):
        sauver(n, m, tmp_path / "t", neuf=True, plafond=it.MORCEAU)
    assert not [k for k in dav.fichiers if k.endswith(".bfosnc")]
    assert not os.listdir(tmp_path / "t")


@needs_tar
def test_bureau_qui_fond_n_est_pas_envoye(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    (m / "Documents" / "gros.bin").write_bytes(os.urandom(2 * it.MORCEAU))
    t = sauver(n, m, tmp_path / "t", neuf=True)
    bon = dav.fichiers[RACINE + "/profil.bfosnc"]
    shutil.rmtree(m / "Documents")
    with pytest.raises(it.Retreci):
        sauver(n, m, tmp_path / "t", session_id="S2", taille_ouverte=t)
    assert dav.fichiers[RACINE + "/profil.bfosnc"] == bon


@needs_tar
def test_erreur_de_tar_autre_que_fichier_change_refusee(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)

    def popen_faux(argv, **kw):
        if argv[0] == "tar":
            argv = ["sh", "-c", "echo 'tar: ./x: Cannot open: Permission denied' >&2; exit 1"]
        return popen_local(argv, **kw)
    with pytest.raises(it.Echec):
        it.sauvegarder(n, SOI, str(m), CLE, str(tmp_path), "S1", neuf=True, popen=popen_faux)
    assert not [k for k in dav.fichiers if k.endswith(".bfosnc")]


@needs_tar
def test_fichier_change_pendant_la_lecture_accepte(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)

    def popen_faux(argv, **kw):
        if argv[0] == "tar":
            argv = ["sh", "-c", "tar --create --file=- -C " + str(m) + " . ; "
                    "echo 'tar: ./.config/x: file changed as we read it' >&2; exit 1"]
        return popen_local(argv, **kw)
    it.sauvegarder(n, SOI, str(m), CLE, str(tmp_path), "S1", neuf=True, popen=popen_faux)
    assert RACINE + "/profil.bfosnc" in dav.fichiers


def test_pas_de_bureau_encore(tmp_path):
    n, _dav = nuage()
    assert ouvrir(n, tmp_path, tmp_path / "t") == 0


@pytest.mark.parametrize("trace", ["profil.precedent.bfosnc", "bureau.json"])
def test_bureau_disparu_mais_traces_refuse(tmp_path, trace):
    n, dav = nuage()
    n.assurer_dossier()
    dav.fichiers[RACINE + "/" + trace] = b"x"
    dav.etags[RACINE + "/" + trace] = '"t"'
    (tmp_path / "maison").mkdir()
    (tmp_path / "maison" / "garde.txt").write_text("x")
    with pytest.raises(it.Echec) as exc:
        ouvrir(n, tmp_path / "maison", tmp_path / "t")
    assert "introuvable" in exc.value.message
    assert (tmp_path / "maison" / "garde.txt").exists()


def test_bureau_d_une_autre_cle_refuse(tmp_path):
    n, dav = nuage()
    n.assurer_dossier()
    dav.fichiers[RACINE + "/profil.bfosnc"] = aller(b"ce n'est pas pour toi", cle=bytes(32))
    dav.etags[RACINE + "/profil.bfosnc"] = '"x"'
    (tmp_path / "m").mkdir()
    with pytest.raises(it.Echec) as exc:
        ouvrir(n, tmp_path / "m", tmp_path / "t")
    assert "ne s'ouvre pas" in exc.value.message
    assert not os.listdir(tmp_path / "t")


def test_pas_assez_de_memoire_refuse_sans_rien_toucher(tmp_path):
    n, dav = nuage()
    n.assurer_dossier()
    dav.fichiers[RACINE + "/profil.bfosnc"] = b"x" * 1000
    dav.etags[RACINE + "/profil.bfosnc"] = '"x"'
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "garde.txt").write_text("x")
    (tmp_path / "t").mkdir()
    with pytest.raises(it.Echec) as exc:
        it.restaurer(n, SOI, str(tmp_path / "m"), CLE, str(tmp_path / "t"), popen=popen_local,
                     run=run_local, statvfs=lambda p: types.SimpleNamespace(f_bavail=1, f_frsize=4096))
    assert "mémoire" in exc.value.message and (tmp_path / "m" / "garde.txt").exists()


def test_reseau_coupe_donne_un_refus_lisible():
    class Coupe:
        def open(self, req, timeout=None):
            raise urllib.error.URLError("connexion refusee")
    n = it.Nuage(DEPOT, ouvreur=Coupe())
    with pytest.raises(it.Echec) as exc:
        n.lire_verrou()
    assert "injoignable" in exc.value.message


def test_age_du_verrou_a_l_heure_du_serveur():
    n, dav = nuage()
    it.prendre_verrou(n, "A", maintenant=lambda: 0.0)   # horloge locale a 1970

    vrai_open = dav.open

    def avec_dates(req, timeout=None):
        r = vrai_open(req, timeout)
        if req.get_method() == "GET":
            r.headers = dict(r.headers, Date="Sat, 24 Oct 2026 22:00:00 GMT",
                             **{"Last-Modified": "Sat, 24 Oct 2026 21:58:00 GMT"})
        return r
    dav.open = avec_dates
    # Horloge locale tres en avance : sans l'heure du serveur, le verrou paraitrait perime.
    with pytest.raises(it.Echec) as exc:
        it.prendre_verrou(n, "B", maintenant=lambda: 10 ** 9)
    assert "déjà ouvert" in exc.value.message


def test_envoi_par_morceaux_au_dela_du_seuil(tmp_path, monkeypatch):
    n, dav = nuage()
    n.assurer_dossier()
    f = tmp_path / "gros"
    f.write_bytes(b"0123456789" * 30)
    n._envoyer_par_morceaux(str(f), "profil.envoi-S.bfosnc", 300, morceau=64)
    assert dav.fichiers[RACINE + "/profil.envoi-S.bfosnc"] == f.read_bytes()
    assert any("/uploads/" in c for _m, c in dav.journal)


@needs_tar
@pytest.mark.skipif(os.getuid() != 0 or not shutil.which("useradd"), reason="root et useradd requis")
def test_sous_un_vrai_compte_non_root(tmp_path):
    """La relecture : tar sous l'uid de la personne ne traversait pas le
    dossier root 0700 du fichier. On l'essaie pour de vrai."""
    import pwd
    nom = "bfos-essai-iti"
    subprocess.run(["useradd", "-M", "-d", str(tmp_path / "h"), nom], capture_output=True)
    cpt = pwd.getpwnam(nom)
    # Comme /var/home : la maison est atteignable par la personne. Les dossiers
    # de pytest (root, 0700) ne le sont pas ; on les ouvre en traversee.
    for d in [tmp_path, *tmp_path.parents]:
        if str(d).startswith("/tmp/pytest"):
            os.chmod(d, os.stat(d).st_mode | 0o011)
    try:
        n, _dav = nuage()
        m = maison_type(tmp_path)
        subprocess.run(["chown", "-R", f"{cpt.pw_uid}:{cpt.pw_gid}", str(m)], check=True)
        tmp = tmp_path / "t"
        tmp.mkdir(mode=0o700)          # dossier de root, comme sur la cle
        it.sauvegarder(n, cpt, str(m), CLE, str(tmp), "S1", neuf=True)
        neuve = tmp_path / "neuve"
        neuve.mkdir()
        os.chown(neuve, cpt.pw_uid, cpt.pw_gid)
        assert it.restaurer(n, cpt, str(neuve), CLE, str(tmp), run=run_local, statvfs=statvfs_large)
        f = neuve / "Documents" / "rapport.odt"
        assert f.read_text() == "rapport" and f.stat().st_uid == cpt.pw_uid
    finally:
        subprocess.run(["userdel", nom], capture_output=True)


@needs_tar
def test_bureau_ouvert_puis_disparu_n_est_pas_remplace(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    del dav.fichiers[RACINE + "/profil.bfosnc"]          # miroir de secours, renommage...
    with pytest.raises(it.Echec) as exc:
        sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    assert "disparu" in exc.value.message
    assert RACINE + "/profil.bfosnc" not in dav.fichiers
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=False)
    assert RACINE + "/profil.bfosnc" not in dav.fichiers


@needs_tar
def test_beaucoup_d_erreurs_de_tar_ne_figent_pas_la_restauration(tmp_path):
    n, _dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)

    def popen_bavard(argv, **kw):
        if argv[:2] == ["tar", "--extract"]:
            # 200 Kio d'erreurs AVANT de lire son entree : un tube se remplirait.
            argv = ["sh", "-c", "head -c 204800 /dev/zero | tr '\\\\0' x >&2; cat >/dev/null; exit 2"]
        return popen_local(argv, **kw)
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    (tmp_path / "t2").mkdir()
    import threading
    res = {}

    def go():
        try:
            it.restaurer(n, SOI, str(neuve), CLE, str(tmp_path / "t2"), popen=popen_bavard,
                         run=run_local, statvfs=statvfs_large)
        except it.Echec as exc:
            res["echec"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive(), "restauration figee"
    assert "remis en place" in res["echec"].message


def test_travail_non_envoye_mis_de_cote_pas_efface(tmp_path):
    m = tmp_path / "m"
    (m / "Documents").mkdir(parents=True)
    (m / "Documents" / "pas-envoye.txt").write_text("precieux")
    it.marquer_non_envoye(str(m), SOI)
    garde = it.mettre_de_cote(str(m), SOI, maintenant=lambda: 0)
    assert garde and (tmp_path / "m" / os.path.basename(garde) / "Documents" / "pas-envoye.txt").exists()
    it.vider_maison(str(m), run=run_local)
    assert (tmp_path / "m" / os.path.basename(garde) / "Documents" / "pas-envoye.txt").read_text() == "precieux"
    assert not (m / it.NON_ENVOYE).exists()
    assert it.mettre_de_cote(str(m), SOI) is None, "sans marqueur, rien n'est deplace"


def test_marqueur_ne_suit_pas_un_lien(tmp_path):
    m = tmp_path / "m"
    m.mkdir()
    cible = tmp_path / "ailleurs"
    os.symlink(cible, m / it.NON_ENVOYE)
    try:
        it.marquer_non_envoye(str(m), SOI)
    except OSError:
        pass
    assert not cible.exists()


def test_vider_maison_ne_traverse_pas(tmp_path):
    m = tmp_path / "m"
    (m / "a" / "b").mkdir(parents=True)
    (m / "a" / "b" / "f").write_text("x")
    (m / ".config").mkdir()
    it.vider_maison(str(m), run=run_local)
    assert os.listdir(m) == []


@needs_tar
def test_restauration_garde_le_travail_non_envoye(tmp_path):
    n, _dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    neuve = tmp_path / "neuve"
    (neuve / "Documents").mkdir(parents=True)
    (neuve / "Documents" / "pas-envoye.txt").write_text("precieux")
    it.marquer_non_envoye(str(neuve), SOI)
    ouvrir(n, neuve, tmp_path / "t2")
    gardes = [d for d in os.listdir(neuve) if d.startswith("Bureau non envoyé ")]
    assert len(gardes) == 1
    assert (neuve / gardes[0] / "Documents" / "pas-envoye.txt").read_text() == "precieux"
    assert (neuve / "Documents" / "rapport.odt").exists(), "le bureau restaure est la aussi"


# ------------------------------------------------------------- registre des mots de passe de root
AUTRE = {"url": "https://nc.exemple.test", "login": "jane doe", "app_password": "VIEUX"}


def test_registre_aller_retour_chiffre():
    n, dav = nuage()
    assert it.lire_jetons(n, CLE) == (None, [])
    assert it.ecrire_jetons(n, CLE, [DEPOT, AUTRE], None)
    brut = dav.fichiers[RACINE + "/" + it.JETONS]
    assert brut.startswith(it.MAGIE) and b"VIEUX" not in brut, "jamais en clair dans le nuage"
    etag, liste = it.lire_jetons(n, CLE)
    assert etag and liste == [DEPOT, AUTRE]


def test_registre_ecriture_conditionnelle_sans_mise_a_jour_perdue():
    n, _dav = nuage()
    it.ecrire_jetons(n, CLE, [AUTRE], None)
    etag, _l = it.lire_jetons(n, CLE)
    assert it.ecrire_jetons(n, CLE, [], None) is False, "existe deja : If-None-Match refuse"
    it.ecrire_jetons(n, CLE, [AUTRE, DEPOT], etag)
    assert it.ecrire_jetons(n, CLE, [], etag) is False, "etag perime : un autre est passe"
    assert it.lire_jetons(n, CLE)[1] == [AUTRE, DEPOT]


def test_modifier_recommence_si_un_autre_ecrit_entre_deux():
    n, dav = nuage()
    it.ecrire_jetons(n, CLE, [AUTRE], None)
    vu = []

    def changer(liste):
        vu.append(list(liste))
        if len(vu) == 1:   # une autre session ecrit pendant qu'on modifie
            it.modifier_jetons(n, CLE, lambda l: l + [dict(DEPOT, app_password="TIERS")])
        return liste + [DEPOT]

    it.modifier_jetons(n, CLE, changer)
    mots = [d["app_password"] for d in it.lire_jetons(n, CLE)[1]]
    assert mots == ["VIEUX", "TIERS", "AP"], "la mise a jour de l'autre n'est pas perdue"


def test_registre_d_une_autre_cle_ou_abime_vaut_vide():
    n, dav = nuage()
    it.ecrire_jetons(n, bytes(32), [AUTRE], None)
    assert it.lire_jetons(n, CLE)[1] == []
    dav.fichiers[RACINE + "/" + it.JETONS] = b"n'importe quoi"
    assert it.lire_jetons(n, CLE)[1] == []


def test_registre_ecarte_les_entrees_douteuses_et_plafonne():
    n, _dav = nuage()
    douteux = [dict(AUTRE, url="http://nc.exemple.test"), {"url": "https://x"}, "chaine"]
    sortie = io.BytesIO()
    it.chiffrer(io.BytesIO(json.dumps(douteux + [AUTRE]).encode()), sortie, CLE)
    n.requete("PUT", n.adresse(it.JETONS), donnees=sortie.getvalue())
    assert it.lire_jetons(n, CLE)[1] == [AUTRE]
    beaucoup = [dict(AUTRE, app_password=f"P{i}") for i in range(it.JETONS_MAX + 7)]
    it.ecrire_jetons(n, CLE, beaucoup, it.lire_jetons(n, CLE)[0])
    garde = it.lire_jetons(n, CLE)[1]
    assert len(garde) == it.JETONS_MAX and garde[-1]["app_password"] == f"P{it.JETONS_MAX + 6}"


def test_registre_injoignable_est_un_refus_lisible():
    n, dav = nuage()
    dav.pannes["GET " + it.JETONS] = 503
    with pytest.raises(it.Echec) as exc:
        it.lire_jetons(n, CLE)
    assert "Nextcloud" in exc.value.message


# ------------------------------------------------------------------ verrous de Nextcloud (essai du 2026-10-26)
def _envois(dav):
    return sorted(k.rsplit("/", 1)[-1] for k in dav.fichiers if "/profil.envoi-" in k)


@needs_tar
def test_renommage_tenu_par_un_client_de_synchro_repris(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    dav.passageres["MOVE profil.envoi-S1.bfosnc"] = [500, 423, 500]
    sauver(n, m, tmp_path / "t", neuf=True)
    assert RACINE + "/profil.bfosnc" in dav.fichiers and not _envois(dav)
    assert dav.attentes == list(it.REPRISES[:3])


@needs_tar
def test_renommage_refuse_en_session_l_envoi_reste_et_la_suivante_repare(tmp_path):
    """Relecture adverse du 2026-10-26 (bloquant 1) : Nextcloud efface le bureau
    AVANT de refuser le renommage ; effacer l'envoi ensuite perdait tout le
    travail depuis l'ouverture."""
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    ouvert = dav.fichiers[RACINE + "/profil.bfosnc"]
    (m / "Documents" / "apres.txt").write_text("travail de la session")
    dav.pannes["MOVE profil.envoi-S2.bfosnc"] = 500
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    assert RACINE + "/profil.bfosnc" not in dav.fichiers, "comme Nextcloud : efface avant le refus"
    assert _envois(dav) == ["profil.envoi-S2.bfosnc"], "l'envoi reste"
    assert not [j for j in dav.journal if j[0] == "DELETE" and "envoi" in j[1]]
    assert dav.attentes == list(it.REPRISES), "pas d'attente apres le dernier essai"
    del dav.pannes["MOVE profil.envoi-S2.bfosnc"]
    # La sauvegarde suivante (precedent pas encore note fait : l'exception l'a coupee)
    (m / "Documents" / "encore.txt").write_text("plus tard")
    sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    assert dav.fichiers[RACINE + "/profil.precedent.bfosnc"] == ouvert, "le bureau tel qu'ouvert"
    assert not _envois(dav)
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "encore.txt").exists()


@needs_tar
def test_renommage_refuse_puis_session_coupee_l_ouverture_reprend_l_envoi(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    (m / "Documents" / "apres.txt").write_text("x")
    sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    (m / "Documents" / "fermeture.txt").write_text("au moment de fermer")
    dav.pannes["MOVE profil.envoi-S2.bfosnc"] = 500
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=False)
    dav.pannes.clear()
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "fermeture.txt").exists()


@needs_tar
def test_bureau_vraiment_disparu_toujours_refuse(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    del dav.fichiers[RACINE + "/profil.bfosnc"]
    with pytest.raises(it.Echec) as exc:
        sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=False)
    assert "disparu" in exc.value.message and not _envois(dav)


def test_lecture_et_put_ne_sont_pas_repris_sur_500():
    n, dav = nuage()
    n.assurer_dossier()
    dav.pannes["GET x"] = 500
    assert n.requete_reprise("GET", n.adresse("x"))[0] == 500
    assert dav.attentes == []


@needs_tar
def test_premier_bureau_jamais_renomme_revient_a_l_ouverture_suivante(tmp_path):
    """Le cas du 2026-10-26 : 1re session itinerante, le client de bureau tient
    l'envoi, MOVE (500) et DELETE (423) refuses ; la session suivante ouvrait
    une maison vide et le fichier laisse sur le bureau etait perdu."""
    n, dav = nuage()
    m = maison_type(tmp_path)
    (m / "Bureau").mkdir()
    (m / "Bureau" / "laisse-la.txt").write_text("a retrouver")
    dav.pannes["MOVE profil.envoi-S1.bfosnc"] = 500
    dav.pannes["DELETE profil.envoi-S1.bfosnc"] = 423
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", neuf=True)
    assert RACINE + "/profil.bfosnc" not in dav.fichiers and _envois(dav) == ["profil.envoi-S1.bfosnc"]
    del dav.pannes["MOVE profil.envoi-S1.bfosnc"]

    neuve = tmp_path / "neuve"
    neuve.mkdir()
    assert ouvrir(n, neuve, tmp_path / "t2") > 0, "pas un bureau neuf"
    assert (neuve / "Bureau" / "laisse-la.txt").read_text() == "a retrouver"
    assert RACINE + "/profil.bfosnc" in dav.fichiers and not _envois(dav)
    assert RACINE + "/profil.precedent.bfosnc" not in dav.fichiers, "aucun ancien a garder"


@needs_tar
def test_orphelin_plus_recent_que_le_bureau_mis_en_place_l_ancien_garde(tmp_path):
    """Fermeture coupee entre l'envoi et le renommage : le bureau est intact,
    l'envoi plus recent attend."""
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    ancien = dav.fichiers[RACINE + "/profil.bfosnc"]
    (m / "Documents" / "derniere-heure.txt").write_text("fait apres")
    n2, dav2 = nuage()
    sauver(n2, m, tmp_path / "t", neuf=True)
    n.requete("PUT", n.adresse("profil.envoi-S2.bfosnc"), donnees=dav2.fichiers[RACINE + "/profil.bfosnc"])

    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "derniere-heure.txt").exists()
    assert dav.fichiers[RACINE + "/profil.precedent.bfosnc"] == ancien
    assert not _envois(dav)


@needs_tar
def test_orphelin_plus_ancien_que_le_bureau_ignore_et_garde(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    dav.pannes["MOVE profil.envoi-S1.bfosnc"] = 500
    dav.pannes["DELETE profil.envoi-S1.bfosnc"] = 423
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", neuf=True)
    dav.pannes.clear()
    (m / "Documents" / "plus-tard.txt").write_text("session suivante")
    sauver(n, m, tmp_path / "t", session_id="S2", neuf=True)
    recent = dav.fichiers[RACINE + "/profil.bfosnc"]

    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "plus-tard.txt").exists()
    assert dav.fichiers[RACINE + "/profil.bfosnc"] == recent
    assert _envois(dav) == ["profil.envoi-S1.bfosnc"], "jamais efface : peut tenir un travail"


@needs_tar
def test_orphelin_illisible_ignore_le_bureau_s_ouvre(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    sauver(n, m, tmp_path / "t", neuf=True)
    bon = dav.fichiers[RACINE + "/profil.bfosnc"]
    n.requete("PUT", n.adresse("profil.envoi-ZZ.bfosnc"), donnees=aller(b"x" * 10)[:-5])
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "rapport.odt").exists()
    assert dav.fichiers[RACINE + "/profil.bfosnc"] == bon
    assert _envois(dav) == ["profil.envoi-ZZ.bfosnc"]
    assert not os.listdir(tmp_path / "t2"), "rien ne traine"


def test_lister_lit_la_reponse_de_nextcloud():
    corps = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" xmlns:oc="http://owncloud.org/ns">
<d:response><d:href>/remote.php/dav/files/jane%20doe/.bfos-live/</d:href><d:propstat><d:prop>
<d:resourcetype><d:collection/></d:resourcetype><d:getlastmodified>Tue, 27 Oct 2026 00:35:43 GMT</d:getlastmodified>
</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat><d:propstat><d:prop><d:getcontentlength/></d:prop>
<d:status>HTTP/1.1 404 Not Found</d:status></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/files/jane%20doe/.bfos-live/profil.envoi-72d72cc8e2e14fcca0e18a676172a55b.bfosnc</d:href>
<d:propstat><d:prop><d:getcontentlength>37000664</d:getcontentlength>
<d:getlastmodified>Tue, 27 Oct 2026 00:25:20 GMT</d:getlastmodified><d:resourcetype/></d:prop>
<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/files/jane%20doe/.bfos-live/profil.bfosnc</d:href>
<d:propstat><d:prop><d:getcontentlength>32984457</d:getcontentlength>
<d:getlastmodified>Tue, 27 Oct 2026 00:35:36 GMT</d:getlastmodified><d:resourcetype/></d:prop>
<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""

    class Un:
        def open(self, req, timeout=None):
            assert req.get_method() == "PROPFIND" and req.get_header("Depth") == "1"
            return Reponse(207, corps)

    n = it.Nuage(DEPOT, ouvreur=Un())
    liste = n.lister()
    assert set(liste) == {"profil.bfosnc", "profil.envoi-72d72cc8e2e14fcca0e18a676172a55b.bfosnc"}
    assert liste["profil.bfosnc"][0] == 32984457
    assert liste["profil.bfosnc"][1] > liste["profil.envoi-72d72cc8e2e14fcca0e18a676172a55b.bfosnc"][1]
    assert it.ORPHELIN.fullmatch("profil.envoi-72d72cc8e2e14fcca0e18a676172a55b.bfosnc")


@needs_tar
def test_deux_orphelins_le_plus_recent_gagne(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    for session, mot in (("S1", "premier"), ("S2", "second")):
        (m / "Documents" / "etat.txt").write_text(mot)
        dav.pannes[f"MOVE profil.envoi-{session}.bfosnc"] = 500
        dav.pannes[f"DELETE profil.envoi-{session}.bfosnc"] = 423
        with pytest.raises(it.Echec):
            sauver(n, m, tmp_path / "t", session_id=session, neuf=True)
    dav.pannes.clear()
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert (neuve / "Documents" / "etat.txt").read_text() == "second"
    assert _envois(dav) == ["profil.envoi-S1.bfosnc"], "l'ancien orphelin reste"


def test_orphelin_sans_assez_de_memoire_refuse_sans_rien_toucher(tmp_path):
    n, dav = nuage()
    n.assurer_dossier()
    n.requete("PUT", n.adresse("profil.envoi-S1.bfosnc"), donnees=aller(b"x" * 1000))
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "garde.txt").write_text("x")
    (tmp_path / "t").mkdir()
    with pytest.raises(it.Echec) as exc:
        it.restaurer(n, SOI, str(tmp_path / "m"), CLE, str(tmp_path / "t"),
                     statvfs=lambda _p: types.SimpleNamespace(f_bavail=1, f_frsize=4096))
    assert "mémoire" in exc.value.message
    assert (tmp_path / "m" / "garde.txt").exists() and _envois(dav) == ["profil.envoi-S1.bfosnc"]
    assert not [j for j in dav.journal if j[0] == "GET"]


@needs_tar
def test_vieil_orphelin_ne_passe_pas_devant_un_precedent_plus_recent(tmp_path):
    """Relecture adverse (bloquant 2) : sans bureau, un orphelin de septembre
    etait promu par-dessus le precedent d'hier, puis l'ecrasait."""
    n, dav = nuage()
    m = maison_type(tmp_path)
    n.requete("PUT", n.adresse("profil.envoi-VIEUX.bfosnc"), donnees=aller(b"septembre"))
    sauver(n, m, tmp_path / "t", neuf=True)
    sauver(n, m, tmp_path / "t", session_id="S2", copier_precedent=True)
    hier = dav.fichiers[RACINE + "/profil.precedent.bfosnc"]
    del dav.fichiers[RACINE + "/profil.bfosnc"]
    (tmp_path / "m").mkdir()
    with pytest.raises(it.Echec) as exc:
        ouvrir(n, tmp_path / "m", tmp_path / "t2")
    assert "introuvable" in exc.value.message
    assert dav.fichiers[RACINE + "/profil.precedent.bfosnc"] == hier
    assert "profil.envoi-VIEUX.bfosnc" in _envois(dav)


@needs_tar
def test_renommage_fait_mais_rendu_500_puis_404_est_un_succes(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    dav.mentent.add("MOVE profil.envoi-S1.bfosnc")
    taille = sauver(n, m, tmp_path / "t", neuf=True)
    assert len(dav.fichiers[RACINE + "/profil.bfosnc"]) == taille and not _envois(dav)
    assert RACINE + "/bureau.json" in dav.fichiers


@needs_tar
def test_premier_bureau_par_un_orphelin_pose_le_marqueur(tmp_path):
    n, dav = nuage()
    m = maison_type(tmp_path)
    dav.pannes["MOVE profil.envoi-S1.bfosnc"] = 500
    with pytest.raises(it.Echec):
        sauver(n, m, tmp_path / "t", neuf=True)
    dav.pannes.clear()
    assert RACINE + "/bureau.json" not in dav.fichiers
    neuve = tmp_path / "neuve"
    neuve.mkdir()
    ouvrir(n, neuve, tmp_path / "t2")
    assert RACINE + "/bureau.json" in dav.fichiers


def test_verrou_tenu_par_un_client_de_synchro_repris():
    n, dav = nuage()
    n.assurer_dossier()
    dav.passageres["PUT verrou.json"] = [423]
    e = it.prendre_verrou(n, "A")
    assert e and dav.attentes == [it.REPRISES[0]]
    dav.passageres["DELETE verrou.json"] = [423, 423]
    n.retirer_verrou(e)
    assert RACINE + "/verrou.json" not in dav.fichiers
