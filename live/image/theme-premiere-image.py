"""Theme Blue Fox des la premiere image du bureau (#26870, 2026-10-25).

Joue a la construction de l'image live, sur l'image de base publiee. Mesure en
VM : un profil neuf restait en Breeze clair, au fond de Plasma, jusqu'a ce que
l'agent d'accueil applique le theme (apres le montage Nextcloud et les PWA,
d'ou le « theme qui arrive apres un moment ») :
  - `ColorScheme=` ne fait que NOMMER le schema : les groupes [Colors:*] doivent
    etre dans kdeglobals ;
  - le fond d'un profil neuf vient de `[Wallpaper] Image=` du look-and-feel,
    pas du conteneur pre-seme dans /etc/skel (ignore, sans activite).
scripts/generate_kde_theme.py emet les deux depuis le 2026-10-25 : sur une
image de base qui les porte deja, ce script ne change rien.
"""
import json
import os
import re
import sys

XDG = "/etc/xdg/kdeglobals"
SKEL = "/etc/skel/.config/kdeglobals"


def valeur(texte, section, cle):
    m = re.search(rf"(?ms)^\[{re.escape(section)}\]\n(.*?)(?=^\[|\Z)", texte)
    v = m and re.search(rf"(?m)^{re.escape(cle)}=(.*)$", m.group(1))
    return v.group(1).strip() if v else None


def couleurs(nom):
    sections = re.split(r"(?m)^(?=\[)", open(f"/usr/share/color-schemes/{nom}.colors").read())
    groupes = [s.rstrip() + "\n" for s in sections if s.startswith(("[Colors:", "[ColorEffects:"))]
    if not any(g.startswith("[Colors:Window]") for g in groupes):
        sys.exit(f"{nom}.colors sans [Colors:Window]")
    return groupes


def main():
    texte = open(XDG).read()
    schema, paquet = valeur(texte, "General", "ColorScheme"), valeur(texte, "KDE", "LookAndFeelPackage")
    if not schema or not paquet:
        sys.exit(f"{XDG} sans ColorScheme ou LookAndFeelPackage")
    groupes = couleurs(schema)
    for f in (XDG, SKEL):
        actuel = open(f).read()
        if "[Colors:Window]" not in actuel:
            with open(f, "a") as s:
                s.write("\n" + "\n".join(groupes))
            print(f"{f} : {len(groupes)} groupes de couleurs de {schema}")

    defaults = f"/usr/share/plasma/look-and-feel/{paquet}/contents/defaults"
    texte = open(defaults).read()
    if valeur(texte, "Wallpaper", "Image") is None:
        image = re.search(r"file:///usr/share/wallpapers/([^/]+)/contents/", texte)
        if not image:
            sys.exit(f"{defaults} : fond introuvable")
        fond = image.group(1)
        json.load(open(f"/usr/share/wallpapers/{fond}/metadata.json"))
        with open(defaults, "a") as s:
            s.write(f"\n[Wallpaper]\nImage={fond}\n")
        print(f"{defaults} : fond {fond}")
    os.sync()


if __name__ == "__main__":
    main()
