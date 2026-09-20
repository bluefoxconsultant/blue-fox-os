#!/usr/bin/env python3
"""Rend install/bfos-amorce.ks.template : le kickstart d'amorce que
scripts/brand-iso.sh depose dans l'ISO sous /bfos-amorce.ks.

    python3 scripts/render_amorce_ks.py --domaine-defaut bluefoxconsultant.com

Le domaine par defaut est celui qui s'affiche pre-rempli a la question ;
Entree seule le prend. Vide = aucune proposition.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "install"))

import bfos_amorce  # noqa: E402
from render_zerotouch_ks import PLACEHOLDER_RE, _embed_script  # noqa: E402

TEMPLATE_PATH = REPO / "install" / "bfos-amorce.ks.template"
SCRIPT_PATH = REPO / "install" / "bfos_amorce.py"
# Le delimiteur du heredoc qui porte le script : une ligne du script qui le
# reproduirait fermerait le heredoc au milieu du code.
DELIMITEUR = "BFOSAMORCEEOF"


def render(domaine_defaut: str = "") -> str:
    domaine = ""
    if domaine_defaut:
        domaine = bfos_amorce.normaliser_domaine(domaine_defaut) or ""
        if not domaine:
            raise SystemExit(f"[render_amorce_ks] domaine par defaut invalide : {domaine_defaut!r}")
    script = _embed_script(SCRIPT_PATH)
    if any(ligne.strip() == DELIMITEUR for ligne in script.splitlines()):
        raise SystemExit(f"[render_amorce_ks] {SCRIPT_PATH.name} contient une ligne {DELIMITEUR}")
    vars_ = {"AMORCE_SCRIPT": script, "DOMAINE_DEFAUT": domaine}

    def remplacer(m):
        cle = m.group(1)
        if cle not in vars_:
            raise SystemExit(f"[render_amorce_ks] variable inconnue {{{{{cle}}}}}")
        return vars_[cle]

    rendu = PLACEHOLDER_RE.sub(remplacer, TEMPLATE_PATH.read_text(encoding="utf-8"))
    # Le script embarque ne porte aucun {{...}} : ceux qui restent viendraient
    # du gabarit, donc d'une variable oubliee.
    reste = PLACEHOLDER_RE.findall(rendu.replace(script, ""))
    if reste:
        raise SystemExit(f"[render_amorce_ks] variables non resolues : {reste}")
    return rendu


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domaine-defaut", default="")
    args = parser.parse_args()
    sys.stdout.write(render(args.domaine_defaut))
    return 0


if __name__ == "__main__":
    sys.exit(main())
