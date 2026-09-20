# Blue Fox OS - build helpers
#
# Depuis le 2026-07-16, la CI ne construit plus d'images : elle ne fait que
# valider (lint, tests, RPM). La publication d'une image OCI — build, push,
# signature cosign, SBOM, attestation — se fait ici, en local :
#
#     make publish SLUG=bf
#
# Le reste du Makefile sert aux builds de dev, à l'ISO et au smoke test.

SLUG ?= bf
TAG ?= dev
REGISTRY ?= ghcr.io/bluefoxconsultant
IMAGE = $(REGISTRY)/blue-fox-os-$(SLUG):$(TAG)

.PHONY: help image publish iso verify verify-all clean lint test welcome-rpm rpm-signing-key bib-config brand-iso zerotouch-render zerotouch-sync bootstrap-garuda

help:
	@echo "Cibles disponibles :"
	@echo "  make bootstrap-garuda        Installe les prérequis de build (Garuda/Arch)"
	@echo "  make image SLUG=bf            Build local de l'image OCI (sans push)"
	@echo "  make publish SLUG=bf          Build + push + signe + SBOM + atteste (remplace la CI)"
	@echo "  make iso SLUG=bf              Génère l'ISO d'install via bootc-image-builder"
	@echo "  make bib-config               Régénère install/bib-config.toml depuis bf-os.ks"
	@echo "  make zerotouch-render SLUG=bf Rend le KS zero-touch pour validation"
	@echo "  make zerotouch-sync           Sync install/blue-fox-install.ks.template -> bf_zerotouch_install addon"
	@echo "  make brand-iso ISO=path/to.iso Brande l'ISO (boot menu, GRUB theme, splash)"
	@echo "  make verify SLUG=bf TAG=v26.07 5 contrôles sur l'image publiée (signature, attestation, SBOM, provenance, source)"
	@echo "  make verify-all               Les mêmes 5 contrôles sur les 3 tenants"
	@echo "  make welcome-rpm              Build le RPM du welcome agent"
	@echo "  make rpm-signing-key          Crée la clé GPG de signature des RPM (une fois, machine de build)"
	@echo "  make lint                     Lint des recipes YAML + Kickstart"
	@echo "  make test                     Tests unitaires welcome agent"
	@echo "  make clean                    Nettoie les artefacts locaux"

# Build local complet, sans push. Délègue à publish-image.sh en DRY_RUN :
# appeler `bluebuild build` directement (ce que faisait cette cible) saute le
# build + staging du RPM welcome et du branding, et la recipe casse ensuite sur
# `rpm-ostree install .../rpm-staging/bluefox-welcome.noarch.rpm`. Ça ne
# marchait qu'en CI, où un job séparé faisait le staging au préalable.
image:
	SLUG=$(SLUG) DRY_RUN=1 ./scripts/publish-image.sh

bootstrap-garuda:
	./scripts/bootstrap-garuda.sh

# TAG n'est volontairement pas passé : publish-image.sh cible :latest, qui est
# ce que bluebuild pousse. Voir l'en-tête du script.
publish:
	SLUG=$(SLUG) ./scripts/publish-image.sh

iso:
	@test -f recipes/$(SLUG).yml || { echo "Recipe recipes/$(SLUG).yml introuvable"; exit 1; }
	@which bootc-image-builder >/dev/null 2>&1 || { echo "Installer bootc-image-builder : https://github.com/osbuild/bootc-image-builder"; exit 1; }
	bootc-image-builder build --type iso $(IMAGE)

# ⚠️ Ne faisait qu'un `cosign verify` jusqu'au 2026-07-26. Les 3 images de
# l'audit P5.2 passaient ce contrôle en étant pourtant sans attestation SBOM et
# sans lien vers leur commit source. Vérifier un seul des trois liens de la
# chaîne donne un feu vert qui ne vaut rien (#21873).
verify:
	./scripts/verify-image.sh $(or $(SLUG),bf)

verify-all:
	./scripts/verify-image.sh --all

welcome-rpm:
	./scripts/build-welcome-rpm.sh

# Une seule fois, sur la machine de build. La clé privée n'a pas à vivre
# ailleurs (#23815) ; la publique est commitée et vérifiée dans l'image.
rpm-signing-key:
	./scripts/generate-rpm-signing-key.sh

bib-config:
	python3 scripts/render_bib_config.py

zerotouch-render:
	python3 scripts/render_zerotouch_ks.py --slug $(or $(SLUG),bf)

zerotouch-sync:
	./scripts/sync-zerotouch-mirror.sh

brand-iso:
	@test -n "$(ISO)" || { echo "usage: make brand-iso ISO=path/to/install.iso"; exit 1; }
	./scripts/brand-iso.sh $(ISO)

lint:
	@for f in recipes/*.yml; do echo "lint $$f"; python3 -c "import yaml; yaml.safe_load(open('$$f'))" || exit 1; done
	@python3 -c "import json; json.load(open('config/schema.v1.json'))" && echo "schema.v1.json OK"
	@python3 -c "import json, glob, jsonschema; s = json.load(open('config/schema.v1.json')); [jsonschema.validate(json.load(open(p)), s) for p in glob.glob('config/*.json') if not p.endswith('schema.v1.json')]; print('config/*.json OK against schema')" || echo "jsonschema non installé : pip install --user jsonschema"
	@command -v ksvalidator >/dev/null && ksvalidator install/bf-os.ks || echo "ksvalidator non installé, skip"
	@python3 scripts/render_bib_config.py >/dev/null && git diff --quiet install/bib-config.toml && echo "bib-config.toml OK" || { echo "bib-config.toml stale ; run make bib-config"; exit 1; }

# Les tests de scripts/tests/ ne sont PAS en `|| true` : ils gardent les
# correctifs d'audit (provenance, dépôt surface durci, source du manifeste) et
# un échec doit se voir.
test:
	cd welcome && python3 -m pytest -q || true
	python3 -m pytest -q scripts/tests

clean:
	rm -rf build/ dist/ output/ welcome/build/ welcome/dist/
	find . -type d -name __pycache__ -exec rm -rf {} +
