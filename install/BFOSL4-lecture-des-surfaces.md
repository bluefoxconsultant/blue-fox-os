# BFOSL4 : où la phrase de passe LUKS existe en clair

Matrice Blue Fox OS #13, élément 598 · tâche BF #23940 · état au 2026-08-30.

Le séquestre protège bien contre « un disque scellé sur une phrase que personne
ne détient » : l'ordre d'écriture du `%include` et les cinq modes de panne sont
testés. Ce document traite l'autre question, celle que la conception n'avait pas
énumérée : **les endroits où la phrase existe en clair**.

Cinq surfaces étaient à établir. **Trois sont fermées sans VM.** Les deux qui
restent demandent une vraie installation ; la procédure est en bas.

---

## Ce que la phrase touche, par construction

Elle a exactement deux destinations dans `bfos_provision.py` :

| Destination | Ligne | Nature |
|---|---|---|
| `write_autopart(passphrase)` → `/tmp/bfos-autopart.ks` | 542 | fichier 0600, tmpfs de l'installateur |
| `payload["disk_passphrase"]` → POST `/api/v1/policy/enroll` | 359 | corps HTTPS |

Aucune autre. `grep 'out(\|print(' install/bfos_provision.py | grep passphrase`
rend trois lignes, et les trois nomment la phrase sans jamais l'interpoler : ce
sont des messages à l'opérateur, pas la valeur.

---

## Surface 1 : `/root/anaconda-ks.cfg` · **FERMÉE** (2026-08-30, lecture de source)

C'était la surface la plus inquiétante, parce que `pykickstart` sérialise bien
la ligne autopart avec la phrase en clair : rejoué le 2026-08-01 avec
`str(handler)` :

```
autopart --encrypted --passphrase="ABCDE-FGHJK-MNPQR-STVWX-YZ234" --type=btrfs --nohome
```

**Mais Anaconda vide le champ avant de sérialiser.** Dans
`pyanaconda/modules/storage/partitioning/automatic/automatic_module.py`,
`setup_kickstart()` :

```python
# Don't generate sensitive information.
data.autopart.passphrase = ""
```

C'est le correctif du bogue Red Hat 868519 (« LUKS passphrase exposed in
/root/anaconda-ks.cfg », CLOSED ERRATA, anaconda-18.22-1, 2012). Chris Lumens,
2012-10-30 : *« We are just going to blank out the passphrases for
anaconda-ks.cfg. »* Vérifié le 2026-08-30 : le correctif a survécu à la
réécriture d'Anaconda en modules DBus, il vit maintenant dans le module de
stockage plutôt que dans `pyanaconda/kickstart.py`.

⚠️ **L'inférence de 2026-08-01 était bien posée mais fausse dans sa conclusion.**
Elle disait : pykickstart sérialise la phrase, or c'est le mécanisme dont
Anaconda se sert pour écrire `anaconda-ks.cfg`, donc la phrase y atterrit. Le
maillon manquant : Anaconda blanchit le champ *avant* de passer la main à
pykickstart. Tester la bibliothèque ne dit rien de ce que l'appelant lui donne.

`/root/original-ks.cfg` reste sûr, comme déjà établi : il garde le `%include`,
pas son contenu.

## Surface 4 : journaux Odoo côté serveur · **FERMÉE** (2026-08-30, sonde)

Établie par une sonde plutôt que par lecture de configuration : un POST sur
`/api/v1/policy/enroll` portant un marqueur unique comme `disk_passphrase`,
puis recherche du marqueur dans tous les journaux.

```bash
MARK="BFOSL4-PROBE-$(date +%s)"
curl -s -X POST https://bluefoxconsultant.com/api/v1/policy/enroll \
  -H 'Content-Type: application/json' \
  -d "{\"machine_uuid\":\"00000000-0000-4000-8000-000000000000\",\"disk_passphrase\":\"$MARK\"}"
docker logs blue-fox-inc-odoo --since 3m 2>&1 | grep -c "$MARK"   # attendu : 0
docker exec npm-app grep -rl "$MARK" /data/logs/                   # attendu : rien
```

Résultat : zéro partout. NPM journalise la taille du corps, pas son contenu :

```
[31/Aug/2026:01:20:14] - 401 401 - POST https bluefoxconsultant.com
"/api/v1/policy/enroll" [Client 10.200.63.1] [Length 36] [Sent-to blue-fox-inc-odoo]
```

Côté Odoo, werkzeug journalise la ligne de requête seule, et aucun chemin de
`bf_policy` ne touche à la valeur : le contrôleur ne journalise que
`hostname`, `login`, `ip` et un oui/non de séquestre ; `_escrow_disk_passphrase`
rend des motifs fixes ; `_logger.exception` n'emporte pas les variables locales.

⚠️ **Conditionné à `log_level = info`**, ce qu'est la prod aujourd'hui. Relire
cette surface si quelqu'un passe la prod en `debug`.

## Surface 2 : journaux recopiés dans `/var/log/anaconda/` · **FERMÉE aux deux tiers**

La question la plus dure était : *blivet passe-t-il la phrase en argument de
`cryptsetup`, donc visible dans `ps` et dans `program.log` ?* **Non.**
`blivet/formats/luks.py` passe par la bibliothèque, pas par un binaire :

```python
blockdev.crypto.luks_format(self.device, context=context, ...)
# context = blockdev.CryptoKeyslotContext(passphrase=self.__passphrase)
```

Un `CryptoKeyslotContext` ne devient jamais un `argv`. Donc rien dans `ps`, et
rien dans `program.log`, qui journalise les commandes externes de blivet.

Côté BFOS, le `%pre` est en `/bin/bash` **sans `set -x`** et son journal
(`/tmp/bfos-provision-pre.log`, recopié dans `/var/log/anaconda/`) ne reçoit que
les messages de `out()`, dont on a vérifié plus haut qu'aucun ne porte la valeur.

**Ce qui reste à voir sur la VM** : `anaconda.log` et les `ks-script-*.log`
générés par Anaconda lui-même, qu'on n'écrit pas et dont on ne contrôle pas le
verbiage.

## Surface 3 : `/tmp/bfos-autopart.ks` · **À ÉTABLIR SUR VM**

Le fichier porte la phrase, c'est son rôle. Il est créé en 0600 **à la création**
et non après coup (`os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)`, correctif de
cfc0f0b), donc il n'est jamais lisible même une fraction de seconde. Il vit dans
le tmpfs de l'installateur, qui meurt au redémarrage.

La question ouverte : **une copie survit-elle sur le système installé ?**
Anaconda recopie `/tmp/*.log` et les scripts `%pre`/`%post` dans
`/var/log/anaconda/`. `bfos-autopart.ks` n'est ni l'un ni l'autre, mais ça se
constate, ça ne se déduit pas.

## Surface 5 : mémoire du processus `%pre` · **À ÉTABLIR SUR VM**

Le processus meurt au `%end`. Le risque résiduel n'est pas la mémoire vive mais
le **swap** : une page contenant la phrase écrite sur un disque qui n'est pas
encore chiffré serait durable. Au moment du `%pre`, le partitionnement n'a pas
eu lieu et l'installateur tourne en mémoire : à confirmer par `swapon --show`.

---

## Procédure d'essai en VM

⚠️ **Corrigé le 2026-09-01.** La version précédente disait « cette station n'a
ni `virsh`, ni `qemu`, ni `virt-install` — l'essai se joue ailleurs ». C'est vrai
du poste, et faux de **charizard**, qui a `qemu-system-x86_64`, `/dev/kvm` et
`xorriso`. L'essai s'y joue, et il s'y est joué : voir `~/qemu-smoke/LISEZ-MOI.md`
(harnais `bfos-smoke.sh` — vTPM, journal série, partage 9p portant les contrôles).

Faire l'installation complète avec le séquestre actif (`disk_escrow` est déjà à
vrai sur l'organisation BF), puis, **sur le système installé, connecté en
root** :

```bash
# Le marqueur : la phrase réellement séquestrée pour cette machine.
# La lire dans Odoo (bouton « Révéler » sur la fiche machine) et la poser ici.
read -rs PHRASE

# 1. anaconda-ks.cfg et son voisin : attendu : aucune occurrence
grep -c "$PHRASE" /root/anaconda-ks.cfg /root/original-ks.cfg

# 2. tous les journaux d'installation : attendu : aucune occurrence
grep -rc "$PHRASE" /var/log/anaconda/ 2>/dev/null | grep -v ':0$'

# 3. une copie de l'include a-t-elle survécu quelque part ?
find / -xdev -name '*autopart*' 2>/dev/null
grep -rl "$PHRASE" / --exclude-dir=/proc --exclude-dir=/sys 2>/dev/null

# 5. y avait-il du swap pendant l'installation ?
grep -i swap /var/log/anaconda/program.log | head
swapon --show
```

Et **dans l'environnement de l'installateur**, avant de redémarrer (Ctrl-Alt-F2
donne un shell) :

```bash
ls -l /tmp/bfos-autopart.ks        # attendu : -rw------- root root
swapon --show                       # attendu : vide
ps auxww | grep -i cryptsetup       # attendu : rien avec la phrase en argument
```

**Ce que l'essai ne doit PAS faire** : écrire le `%post` qui gomme la ligne dans
`/root/anaconda-ks.cfg`. La surface 1 est fermée : un nettoyage posé sur un
fichier qui ne contient rien donne une fausse assurance et masque la vraie
question. Ne l'écrire que si l'essai contredit la lecture de source ci-dessus.

## Ce que l'essai couvre en même temps

L'arbitrage du 2026-08-29 (séquestre activé AVANT l'essai, contre la note du 1er
août) tenait sur ceci : les cinq modes de panne laissent tous Anaconda redemander
la phrase, donc une installation aboutit même si le séquestre déraille. Un seul
essai couvre donc les cinq défauts ouverts #23906 à #23910 **et** le séquestre.
Le `%include` alimenté par le `%pre` n'a jamais tourné sur une vraie machine.

---

## État au 2026-09-01 : l'essai a eu lieu, et il bute sur le prompt LUKS

Deux installations complètes en VM sur charizard (UEFI, vTPM, zero-touch depuis
le kickstart servi). **Le séquestre s'est déclenché pour de vrai, deux fois** —
c'était l'inconnue qui restait depuis le 26 juillet :

| fiche | créée (UTC) | `disk_escrowed_on` | révélée |
|---|---|---|---|
| 2 | 2026-09-01 03:28:22 | même instant | 03:54, Olivier |
| 3 | 2026-09-01 04:22:37 | même instant | 04:40, Olivier |

Le dépôt passe par `_valid_passphrase()` : une phrase de longueur ou d'alphabet
non conformes serait refusée. Les deux dépôts ont donc la bonne forme.

**Ce qui bloque** : au prompt LUKS de la VM, la phrase révélée par Odoo a été
refusée. Deux causes se ressemblent à l'écran et ne se valent pas du tout :

- **(a)** la phrase est bonne, le prompt ne rend pas ce qu'on tape (disposition
  de clavier de l'initramfs, saisie à l'aveugle). Ennuyeux, sans gravité.
- **(b)** le disque est scellé sur autre chose que ce qui est séquestré — et
  c'est la garantie centrale de #23940 qui tombe.

`~/bfos-tester-phrase.sh` sur charizard tranche : `cryptsetup
--test-passphrase` contre le qcow2 via `qemu-nbd`, saisie masquée, rien monté,
rien déverrouillé, rien en argument de commande. **Pas encore joué.**

Tant que le disque ne s'ouvre pas, les surfaces 3 et 5 restent fermées à double
tour au sens propre : leurs réponses sont sur ce disque. Le `%post` du kickstart
grave désormais les deux lectures dans
`/var/log/anaconda/bfosl4-installateur.log` — swap actif pendant le `%pre`,
droits de `/tmp/bfos-autopart.ks`, et **présence** (jamais la valeur) du motif
`--passphrase=`. Vérifié : le gabarit synchronisé côté Odoo date du 04:19:45 UTC,
soit trois minutes avant l'enrôlement de la fiche 3 — cette installation-là porte
donc bien la gravure.

Ce même fichier tranchera aussi la cause (b) sans révéler quoi que ce soit : si
la ligne autopart **ne portait pas** de phrase, c'est qu'Anaconda est retombé sur
la saisie à l'écran, et le disque est scellé sur ce qui a été tapé là.
