"""Zone Flatpak de la cle live (#26870, arbitre par Olivier le 2026-10-27).

Les applis Flatpak sont cuites dans l'ISO : sans rien de plus, une cle gardee des
mois garderait un Brave perime. Quand la cle a un espace des profils (partition
BFOS_PROFILS, ou dossier bfos-profils de Ventoy), on y tient une ZONE commune a
tous ses usagers, hors des profils :

  * apres l'ouverture (bfos-live-flatpak-maj.timer), a basse priorite, le fichier
    `bfos-flatpak.img` (ext4, taille fixe) y est cree et formate pour l'ISO de ce
    demarrage, puis marque pret ; une ISO differente (autres applis), une zone
    pleine ou abimee le refont de la meme facon ;
  * au demarrage suivant (bfos-live-flatpak.service, avant l'ecran), une zone prete
    est montee, puis une superposition sur /var/lib/flatpak : en bas les applis de
    l'ISO, en haut ce que les mises a jour ont change. Rien de lent avant l'ecran ;
  * apres l'ouverture (bfos-live-flatpak-maj.timer), `flatpak update` tourne en
    arriere-plan, a basse priorite, signatures de Flathub exigees ;
  * sans zone (cle seulement ephemere, ou trop petite) : les versions de l'ISO,
    et une bulle quand elles ont plus de 60 jours.

Rien ne bloque jamais l'ouverture : la moindre erreur laisse les applis de l'ISO.
"""
from __future__ import annotations

import configparser
import datetime
import errno
import json
import os
import re
import subprocess

from bfos_live import pam_helper as ph
from bfos_live import session as se
from bfos_live.langue import _

FICHIER = "bfos-flatpak.img"
TAILLE = 6 * 1024 * se.MIO
ETIQUETTE = "bfos-flatpak"
SOURCE = "bfos-flatpak"           # nom de la superposition (fstab : x-gvfs-hide)
CIBLE = "/var/lib/flatpak"
IMAGE = "/usr/share/bfos-live/flatpak-image.txt"   # ecrit a la construction
TAMPON = "image.txt"              # dans la zone : l'IMAGE pour laquelle elle a servi
A_REFAIRE = "bfos-flatpak.a-refaire"   # a cote du fichier : zone a remettre a neuf en arriere-plan
PRETE = "bfos-flatpak.prete"           # a cote du fichier : formatee pour cette empreinte d'ISO
CONTROLE = 30                     # secondes au plus pour un e2fsck avant l'ecran
AGE_AVIS = 60                     # jours
DEPOT = "/var/lib/flatpak/repo/config"


def points(cfg):
    base = os.path.join(cfg["state_dir"], "zone")
    return {"base": base, "espace": os.path.join(base, "espace"),
            "disque": os.path.join(base, "disque"), "active": os.path.join(base, "active")}


def lire_image(chemin=IMAGE):
    """{"date": date, "empreinte": str} de l'ISO, ou {}."""
    try:
        with open(chemin, encoding="ascii") as f:
            champs = dict(l.strip().split("=", 1) for l in f if "=" in l)
        return {"date": datetime.date.fromisoformat(champs["date"]),
                "empreinte": re.fullmatch(r"[0-9a-f]{8,64}", champs["empreinte"]).group(0)}
    except (OSError, KeyError, ValueError, AttributeError):
        return {}


def _monter_espace(cfg, run, support, monte=os.path.ismount, controle=True):
    """Monte l'espace des profils de la cle en ecriture ; rend le dossier qui
    porte les profils, ou None (pas d'espace : la cle n'a jamais garde de bureau,
    ou sa partition n'est pas propre)."""
    p = points(cfg)
    if support["genre"] == "ventoy":
        dev, fs = se.projection_ventoy(support, run=run)
        pilote, options = se.FS_VENTOY[fs]
        sous = se.VENTOY_DOSSIER
    else:
        dev, fs = se.partition_profils(support["disque"], run=run)
        if dev is None or fs != "ext4":
            return None
        pilote, options, sous = "ext4", "", ""
        if controle and not monte(p["espace"]):
            # La zone la garde montee toute la session : c'est ici, avant, que la
            # partition se controle (celui de monter_profils la trouverait montee).
            # Borne : l'ecran de connexion attend. Pas propre : pas de zone ce coup-ci.
            try:
                r = run(["e2fsck", "-p", dev], capture_output=True, timeout=CONTROLE,
                        env={"PATH": se.CHEMIN})
            except subprocess.TimeoutExpired:
                se.journal("zone flatpak : controle de la partition trop long", ph.syslog.LOG_WARNING)
                return None
            if r.returncode >= 4:
                se.journal(f"zone flatpak : partition non propre (e2fsck {r.returncode})",
                           ph.syslog.LOG_WARNING)
                return None
    ph.dossier_sur(cfg["state_dir"], 0o755)
    ph.dossier_sur(p["base"], 0o700)
    ph.dossier_sur(p["espace"], 0o700)
    if not monte(p["espace"]):
        opts = "nosuid,nodev,noexec" + ("," + options if options else "")
        se.lancer(["mount", "-t", pilote, "-o", opts, dev, p["espace"]], run=run)
    rep = os.path.join(p["espace"], sous) if sous else p["espace"]
    if sous and (os.path.islink(rep) or not os.path.isdir(rep)):
        return None   # Ventoy sans dossier de profils : cle seulement ephemere
    return rep


def _reserve(cfg):
    reserve_mb = cfg.get("profile_reserve_mb")
    if reserve_mb is None:
        reserve_mb = cfg.get("profile_size_mb") or 8192
    return int(reserve_mb) * se.MIO


def _creer_fichier(cfg, rep, statvfs, exfat):
    """Cree la zone si la place le permet (une autre personne doit toujours
    pouvoir creer un profil complet). False sinon."""
    chemin = os.path.join(rep, FICHIER)
    with se.Verrou(cfg, fichier=se.VERROU_PLACE):
        if os.path.lexists(chemin):
            return True
        lp = statvfs(rep)
        libre = lp.f_bavail * lp.f_frsize
        if libre < TAILLE + _reserve(cfg) + se.MARGE_LIBRE:
            se.journal(f"zone flatpak : place insuffisante ({libre // se.MIO} Mio libres)")
            return False
        fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            # exFAT (Ventoy) : etendre la taille reserve les grappes sans ecrire ;
            # c'est mkfs (en arriere-plan) qui fera ecrire les zeros jusqu'au bout.
            # ext4 : reservation.
            if exfat:
                os.ftruncate(fd, TAILLE)
            else:
                os.posix_fallocate(fd, 0, TAILLE)
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            os.unlink(chemin)
            raise
        os.close(fd)
    return True


def _formater(chemin, run):
    # nodiscard : le discard de mkfs CREUSE le fichier (513 Mio reserves -> 1 Mio) ;
    # lazy_itable_init=0 : sinon le noyau le creuse ensuite par la boucle (relectures
    # adverses du 2026-10-27). Lent sur exFAT (6 Gio de zeros) : jamais avant l'ecran.
    se.lancer(["mkfs.ext4", "-q", "-F", "-L", ETIQUETTE, "-m", "0",
               "-E", "nodiscard,lazy_itable_init=0,lazy_journal_init=0", chemin],
              run=run, delai=3600)


def _combler(cfg, chemin, rep, statvfs):
    """ext4 : remet la reservation (un fstrim a pu creuser le fichier), sans toucher
    aux donnees. Ce qui manque est compte AVANT : posix_fallocate garderait ce qu'il
    a pris d'une partition qui n'a plus la place (pleine a 100 %, mesure)."""
    st = os.stat(chemin, follow_symlinks=False)
    manque = TAILLE - st.st_blocks * 512
    if manque > 0:
        lp = statvfs(rep)
        if lp.f_bavail * lp.f_frsize - se.MARGE_LIBRE < manque:
            raise OSError(errno.ENOSPC, f"zone creusee de {manque // se.MIO} Mio, place absente")
        fd = os.open(chemin, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            os.posix_fallocate(fd, 0, TAILLE)
        finally:
            os.close(fd)


def _lire(chemin, n=200):
    try:
        fd = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, encoding="ascii") as f:
            return f.read(n).strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _ecrire(chemin, texte):
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(texte + "\n")
        f.flush()
        os.fsync(f.fileno())


def a_refaire(rep, raison):
    """Marque la zone a remettre a neuf (en arriere-plan). A cote du fichier, dans
    l'espace des profils : la zone, elle, est peut-etre pleine. Jamais bloquant."""
    try:
        _ecrire(os.path.join(rep, A_REFAIRE), raison)
    except OSError as exc:
        se.journal(f"zone flatpak : marque a-refaire non posee ({exc})", ph.syslog.LOG_WARNING)


def _fichier_valide(chemin):
    st = os.lstat(chemin)
    if not os.path.isfile(chemin) or os.path.islink(chemin) or st.st_size != TAILLE:
        raise se.Echec("", f"{chemin} inattendu (taille {st.st_size})")


def preparer(cfg, run=subprocess.run, statvfs=os.statvfs, support=None, image=None,
             monte=os.path.ismount):
    """Au demarrage, avant l'ecran : active une zone DEJA PRETE (creee et formatee
    en arriere-plan pour l'ISO de ce demarrage, voir preparer_en_fond). Ne cree ni
    ne formate jamais rien : sur exFAT, mkfs ecrit 6 Gio (1 min 44 s sur SSD,
    relecture du 2026-10-27). Rend True si /var/lib/flatpak est servi par la zone."""
    image = image if image is not None else lire_image()
    if not image:
        se.journal("zone flatpak : image sans empreinte, applis de l'ISO", ph.syslog.LOG_WARNING)
        return False
    p = points(cfg)
    if monte(CIBLE):
        return True
    disque_monte = False
    try:
        support = support or se.support_de_la_cle(run=run)
        rep = _monter_espace(cfg, run, support, monte=monte)
        if rep is None:
            return False
        chemin = os.path.join(rep, FICHIER)
        if (not os.path.lexists(chemin) or _lire(os.path.join(rep, PRETE)) != image["empreinte"]
                or os.path.lexists(os.path.join(rep, A_REFAIRE))):
            se.journal("zone flatpak : pas prete pour cette ISO, preparee en arriere-plan")
            return False
        _fichier_valide(chemin)
        if not se.est_exfat(rep, run=run):
            _combler(cfg, chemin, rep, statvfs)
        try:
            run(["e2fsck", "-p", chemin], capture_output=True, timeout=CONTROLE,
                env={"PATH": se.CHEMIN})
        except subprocess.TimeoutExpired:
            raise se.Echec("", "controle de la zone trop long")
        ph.dossier_sur(p["disque"], 0o755)
        try:
            se.lancer(["mount", "-t", "ext4", "-o", "loop,nosuid,nodev,noatime", chemin,
                       p["disque"]], run=run)
        except se.Echec:
            # Zone abimee (cle arrachee pendant une mise a jour) : reparee ou refaite
            # en arriere-plan ; ce demarrage garde les applis de l'ISO.
            a_refaire(rep, "illisible")
            raise
        disque_monte = True
        if _lire(os.path.join(p["disque"], TAMPON)) != image["empreinte"]:
            a_refaire(rep, "tampon")
            raise se.Echec("", "tampon de la zone different de l'ISO")
        for d in ("haut", "travail"):
            ph.dossier_sur(os.path.join(p["disque"], d), 0o755)
        # La racine de la superposition prend l'etiquette SELinux de `haut`, cree
        # sous une racine ext4 neuve sans etiquette : on lui donne celle de l'ISO.
        run(["chcon", f"--reference={CIBLE}", os.path.join(p["disque"], "haut")],
            capture_output=True, timeout=30, env={"PATH": se.CHEMIN})
        # nosuid,nodev SUR la superposition : elle n'herite pas de ceux de la couche
        # du haut (setuid execute avec euid 0, mesure a la relecture du 2026-10-27).
        se.lancer(["mount", "-t", "overlay", SOURCE, "-o",
                   f"nosuid,nodev,lowerdir={CIBLE},upperdir={p['disque']}/haut,"
                   f"workdir={p['disque']}/travail", CIBLE], run=run)
        ph._ecrire_texte(p["active"], json.dumps({"tampon": image["empreinte"], "rep": rep}),
                         mode=0o600)
        se.journal(f"zone flatpak active ({support['genre']})")
        return True
    except (se.Echec, ph.Refus, OSError, ValueError, KeyError) as exc:
        raison = exc.raison if isinstance(exc, (se.Echec, ph.Refus)) else repr(exc)
        se.journal(f"zone flatpak : applis de l'ISO ({raison})", ph.syslog.LOG_WARNING)
        if disque_monte:
            run(["umount", p["disque"]], capture_output=True, timeout=60, env={"PATH": se.CHEMIN})
        return False


def preparer_en_fond(cfg, run=subprocess.run, statvfs=os.statvfs, support=None, image=None,
                     monte=os.path.ismount):
    """Apres l'ouverture, a basse priorite, quand la zone n'est pas active : cree la
    zone si la place le permet, la formate pour l'ISO de ce demarrage (ou la remet
    a neuf), la marque prete. Active au demarrage suivant. Rend un mot d'etat ou
    None (pas d'espace, pas de place)."""
    image = image if image is not None else lire_image()
    if not image:
        return None
    p = points(cfg)
    try:
        support = support or se.support_de_la_cle(run=run)
        rep = _monter_espace(cfg, run, support, monte=monte, controle=False)
        if rep is None:
            return None
        chemin = os.path.join(rep, FICHIER)
        refaire = os.path.join(rep, A_REFAIRE)
        exfat = se.est_exfat(rep, run=run)
        if not os.path.lexists(chemin) and not _creer_fichier(cfg, rep, statvfs, exfat):
            return None
        _fichier_valide(chemin)
        if _lire(os.path.join(rep, PRETE)) == image["empreinte"] and not os.path.lexists(refaire):
            return "prete"
        try:
            os.unlink(os.path.join(rep, PRETE))
        except FileNotFoundError:
            pass
        if not exfat:
            _combler(cfg, chemin, rep, statvfs)
        _formater(chemin, run)
        prep = os.path.join(p["base"], "preparation")
        ph.dossier_sur(prep, 0o700)
        se.lancer(["mount", "-t", "ext4", "-o", "loop,nosuid,nodev,noatime", chemin, prep], run=run)
        try:
            _ecrire(os.path.join(prep, TAMPON), image["empreinte"])
            for d in ("haut", "travail"):
                ph.dossier_sur(os.path.join(prep, d), 0o755)
        finally:
            run(["umount", prep], capture_output=True, timeout=120, env={"PATH": se.CHEMIN})
        try:
            os.unlink(refaire)
        except FileNotFoundError:
            pass
        _ecrire(os.path.join(rep, PRETE), image["empreinte"])
        run(["sync"], capture_output=True, timeout=600, env={"PATH": se.CHEMIN})
        se.journal("zone flatpak preparee : active au prochain demarrage")
        return "preparee"
    except (se.Echec, ph.Refus, OSError, ValueError, KeyError) as exc:
        raison = exc.raison if isinstance(exc, (se.Echec, ph.Refus)) else repr(exc)
        se.journal(f"zone flatpak non preparee ({raison})", ph.syslog.LOG_WARNING)
        return None


def signatures_exigees(chemin=DEPOT):
    """Le depot flathub du systeme verifie ses signatures (sinon : pas de mise a jour)."""
    c = configparser.ConfigParser(interpolation=None)
    try:
        c.read(chemin, encoding="utf-8")
        s = c['remote "flathub"']
    except (KeyError, configparser.Error):
        return False
    return s.get("gpg-verify", "").lower() == "true" and s.get("url", "").startswith("https://")


def zone_active(run=subprocess.run):
    r = run(["findmnt", "-n", "-o", "SOURCE,FSTYPE", CIBLE], capture_output=True, text=True,
            timeout=15, env={"PATH": se.CHEMIN})
    return r.returncode == 0 and r.stdout.split() == [SOURCE, "overlay"]


def personnes_ouvertes(cfg, lister=os.listdir):
    """(nom, uid) des sessions ouvertes sur la cle (etat root de chaque session)."""
    d = se.dossiers(cfg)["etat"]
    try:
        noms = [n[:-5] for n in lister(d) if n.endswith(".json")]
    except OSError:
        return []
    out = []
    for nom in noms:
        etat = se.lire_json_root(os.path.join(d, nom + ".json")) or {}
        if ph.NOM_VALIDE.match(nom) and isinstance(etat.get("uid"), int):
            out.append((nom, etat["uid"]))
    return out


def mettre_a_jour(cfg, run=subprocess.run, image=None, aujourd_hui=None, actif=None,
                  signes=None, preparer_fond=True):
    """Minuterie, apres l'ouverture. Zone : mise a jour des Flatpak. Sans zone :
    bulle si les applis de l'ISO ont plus de AGE_AVIS jours. Rend un mot d'etat."""
    image = image if image is not None else lire_image()
    actif = zone_active(run=run) if actif is None else actif
    if not actif:
        etat = preparer_en_fond(cfg, run=run, image=image) if preparer_fond else None
        if etat in ("preparee", "prete"):
            return etat
        aujourd_hui = aujourd_hui or datetime.date.today()
        if image and (aujourd_hui - image["date"]).days > AGE_AVIS:
            texte = _("Les applications de cette clé datent du {date}. Pour les garder à jour, "
                      "garde ton bureau sur la clé une fois, ou installe une clé plus récente."
                      ).format(date=image["date"].isoformat())
            for nom, uid in personnes_ouvertes(cfg):
                se.notifier(nom, uid, texte, run=run, titre=_("Applications"))
            return "avis"
        return "iso"
    if not (signes if signes is not None else signatures_exigees()):
        se.journal("zone flatpak : depot flathub sans signatures exigees, pas de mise a jour",
                   ph.syslog.LOG_WARNING)
        return "refus"
    r = run(["flatpak", "update", "--system", "--noninteractive", "--assumeyes"],
            capture_output=True, timeout=7200, env={"PATH": se.CHEMIN, "LC_ALL": "C"})
    if r.returncode != 0:
        erreur = r.stderr or b""
        se.journal(f"zone flatpak : mise a jour en echec ({r.returncode}): {erreur[-300:]!r}",
                   ph.syslog.LOG_WARNING)
        if b"No space left" in erreur:
            try:
                rep = json.loads(_lire(points(cfg)["active"], 4096) or "{}").get("rep")
            except ValueError:
                rep = None
            if rep:
                a_refaire(rep, "pleine")
        return "echec"
    run(["flatpak", "uninstall", "--system", "--unused", "--noninteractive", "--assumeyes"],
        capture_output=True, timeout=1800, env={"PATH": se.CHEMIN, "LC_ALL": "C"})
    run(["sync"], capture_output=True, timeout=300, env={"PATH": se.CHEMIN})
    se.journal("zone flatpak : applis a jour")
    return "fait"


def main(argv):
    try:
        cfg = ph.charger_socle(ph.CONFIG)
    except ph.Refus as exc:
        print(exc.raison)
        return 1
    if argv == ["zone-flatpak"]:
        preparer(cfg)
        return 0
    if argv == ["maj-flatpak"]:
        print(json.dumps({"etat": mettre_a_jour(cfg)}))
        return 0
    return 2
