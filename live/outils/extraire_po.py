#!/usr/bin/env python3
"""Régénère le gabarit des traductions de la clé live et met les langues à jour (#26870).

  python3 outils/extraire_po.py          (depuis live/)

- Parcourt le paquet `bfos_live` par AST et relève chaque `_("…")` et `N_("…")` dont
  l'argument est une chaîne littérale (les concaténations implicites comptent comme une seule),
  ainsi que le message (1er argument littéral) de `Refus`, `Echec`, `TropGros` et `Retreci` :
  l'aide et l'unité de session le traduisent à l'affichage (comme xgettext `-kRefus:1`).
- Écrit `bfos_live/po/bfos-live.pot` (le gabarit d'une nouvelle langue : le copier en
  `<code>.po` et remplir les msgstr).
- Pour chaque `po/<code>.po` existant : garde les traductions des textes toujours présents,
  ajoute les nouveaux textes avec un msgstr vide, et range les textes disparus en fin de
  fichier, en commentaire `#~` (rien n'est perdu).
Sans dépendance : ni xgettext ni msgmerge.
"""
import ast
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent.parent
PAQUET = ICI / "bfos_live"
PO = PAQUET / "po"
GABARIT = PO / "bfos-live.pot"
MOTS = ("_", "N_")
MESSAGES = ("Refus", "Echec", "TropGros", "Retreci")
sys.path.insert(0, str(ICI))
from bfos_live.langue import lire_po  # noqa: E402


def relever(paquet=PAQUET):
    """[(msgid, [« fichier:ligne », …])] dans l'ordre de première apparition."""
    vus: dict[str, list[str]] = {}
    for fichier in sorted(paquet.rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
        for noeud in ast.walk(arbre):
            if (isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)
                    and ((noeud.func.id in MOTS and len(noeud.args) == 1)
                         or (noeud.func.id in MESSAGES and noeud.args))
                    and isinstance(noeud.args[0], ast.Constant)
                    and isinstance(noeud.args[0].value, str)):
                ref = f"{fichier.relative_to(ICI)}:{noeud.lineno}"
                vus.setdefault(noeud.args[0].value, []).append(ref)
    return sorted(vus.items(), key=lambda kv: (kv[1][0].split(":")[0], int(kv[1][0].split(":")[1])))


def citer(texte):
    t = texte.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    lignes = t.split("\n")
    if len(lignes) == 1:
        return f'"{lignes[0]}"'
    morceaux = [f'"{l}\\n"' for l in lignes[:-1]] + ([f'"{lignes[-1]}"'] if lignes[-1] else [])
    return '""\n' + "\n".join(morceaux)


ENTETE = ('msgid ""\nmsgstr ""\n"Project-Id-Version: bfos-live\\n"\n'
          '"Language: {langue}\\n"\n"MIME-Version: 1.0\\n"\n'
          '"Content-Type: text/plain; charset=UTF-8\\n"\n"Content-Transfer-Encoding: 8bit\\n"\n')


def ecrire(chemin, entrees, traductions, langue, disparus=()):
    sortie = [ENTETE.format(langue=langue)]
    for msgid, refs in entrees:
        sortie.append("#: " + " ".join(refs) + f"\nmsgid {citer(msgid)}\nmsgstr {citer(traductions.get(msgid, ''))}\n")
    for msgid, msgstr in disparus:
        sortie.append("#~ msgid " + citer(msgid).replace("\n", "\n#~ ") +
                      "\n#~ msgstr " + citer(msgstr).replace("\n", "\n#~ ") + "\n")
    chemin.write_text("\n".join(sortie), encoding="utf-8")


def main():
    entrees = relever()
    PO.mkdir(exist_ok=True)
    ecrire(GABARIT, entrees, {}, "")
    actuels = {m for m, _r in entrees}
    for po in sorted(PO.glob("*.po")):
        anciens = lire_po(po)
        disparus = [(m, s) for m, s in anciens.items() if m not in actuels]
        ecrire(po, entrees, anciens, po.stem, disparus)
        manquants = sum(1 for m in actuels if not anciens.get(m))
        print(f"{po.name} : {len(actuels) - manquants}/{len(actuels)} traduits"
              + (f", {len(disparus)} disparus en #~" if disparus else ""))
    print(f"{GABARIT.name} : {len(entrees)} textes")


if __name__ == "__main__":
    main()
