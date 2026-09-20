# bluefox-welcome

Agent de premier démarrage Blue Fox OS. PyQt6, packagé en RPM, intégré dans la recipe BlueBuild.

## État

Squelette v0.1.0. Implémentation complète prévue en P4.1 (échéance 2026-07-03).

## Build local

```sh
cd welcome
python3 -m pip install --user -e .
bluefox-welcome --reset       # nettoie l'état dev
bluefox-welcome               # lance le wizard stub
```

## Build RPM

```sh
make welcome-rpm   # depuis la racine du repo
```

## Architecture (à étoffer en P4.1)

- `main.py` : CLI + dispatcher (réinitialisation, mode service)
- `wizard.py` : 4-5 écrans PyQt6 (à créer)
- `tenant.py` : lit `/usr/share/bluefox/tenant.json`, déposé dans l'image au
  build depuis `config/{slug}.json` (source unique, en dépôt). ⚠️ Cette ligne
  annonçait un fetch de `config.bluefoxconsultant.com/{slug}.json` (#23813) :
  l'endpoint n'a jamais été déployé et ne le sera pas — décision du 2026-07-24.
  Aucun appel réseau ici.
- `apply/` : modules d'application des configs (KAccounts, Thunderbird, NC Talk, CalDAV, CardDAV, imprimante) (à créer)
- `firstboot.service` : unité systemd qui déclenche le wizard
