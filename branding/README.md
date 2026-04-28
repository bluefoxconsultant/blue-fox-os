# Brand assets — Blue Fox OS

Ce dossier contient les fichiers d'images shippes dans l'image OCI BF (et referencees par `config/bf.json`).

## Fichiers attendus

| Fichier | Format | Dimensions | Utilisation |
|---|---|---|---|
| `logo.png` | PNG transparent | 1024x1024 | Affiche dans le welcome agent, panneaux de session, KAccounts |
| `wallpaper.jpg` | JPEG | 1920x1080 minimum (4K idealement) | Fond d'ecran KDE Plasma par defaut |
| `splash.png` | PNG transparent ou opaque | 1024x768 | Splash Plymouth au demarrage |

## Identite visuelle (canonical, voir `reference_bf_brand_canonical.md`)

- Bleu BF : `#29ABE1`
- Anthracite : `#2D3031`
- Police systeme : Lexend (TTFs shippes via `files/usr/share/fonts/lexend/`)
- Logo : silhouette de renard, ligne fine, pas de texte

## Process de mise a jour

1. Editer ou remplacer le fichier ici.
2. Commit + push : la CI build une nouvelle image dans les minutes qui suivent.
3. Les machines deployees recuperent la mise a jour au prochain `rpm-ostree update` (timer quotidien).

## Statut au 2026-04-28

Aucun fichier d'asset present. Les URLs dans `config/bf.json` (raw.githubusercontent.com/...) retournent 404. A produire avant le 2026-05-15 (matrice BFOSD8).
