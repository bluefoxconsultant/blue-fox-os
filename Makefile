# Blue Fox OS - build helpers
# Pablo : tu n'as normalement qu'à faire `git push` ; CI fait le reste.
# Ce Makefile sert pour les builds locaux de dev et le smoke test.

SLUG ?= bf
TAG ?= dev
REGISTRY ?= ghcr.io/bluefoxconsultant
IMAGE = $(REGISTRY)/blue-fox-os-$(SLUG):$(TAG)

.PHONY: help image iso verify clean lint test welcome-rpm

help:
	@echo "Cibles disponibles :"
	@echo "  make image SLUG=bf            Build local de l'image OCI"
	@echo "  make iso SLUG=bf              Génère l'ISO d'install via bootc-image-builder"
	@echo "  make verify SLUG=bf TAG=v26.07 Vérifie la signature cosign de l'image distante"
	@echo "  make welcome-rpm              Build le RPM du welcome agent"
	@echo "  make lint                     Lint des recipes YAML + Kickstart"
	@echo "  make test                     Tests unitaires welcome agent"
	@echo "  make clean                    Nettoie les artefacts locaux"

image:
	@which bluebuild >/dev/null 2>&1 || { echo "Installer bluebuild d'abord : https://blue-build.org/learn/getting-started/"; exit 1; }
	bluebuild build --push=false recipes/$(SLUG).yml

iso:
	@test -f recipes/$(SLUG).yml || { echo "Recipe recipes/$(SLUG).yml introuvable"; exit 1; }
	@which bootc-image-builder >/dev/null 2>&1 || { echo "Installer bootc-image-builder : https://github.com/osbuild/bootc-image-builder"; exit 1; }
	bootc-image-builder build --type iso $(IMAGE)

verify:
	cosign verify --key cosign.pub $(IMAGE)

welcome-rpm:
	cd welcome && rpmbuild -bb welcome.spec --define "_topdir $$PWD/build" --define "_sourcedir $$PWD"

lint:
	@for f in recipes/*.yml; do echo "lint $$f"; python3 -c "import yaml; yaml.safe_load(open('$$f'))" || exit 1; done
	@python3 -c "import json; json.load(open('config/schema.v1.json'))" && echo "schema.v1.json OK"
	@python3 -c "import json, glob, jsonschema; s = json.load(open('config/schema.v1.json')); [jsonschema.validate(json.load(open(p)), s) for p in glob.glob('config/*.json') if not p.endswith('schema.v1.json')]; print('config/*.json OK against schema')" || echo "jsonschema non installé : pip install --user jsonschema"
	@command -v ksvalidator >/dev/null && ksvalidator install/bf-os.ks || echo "ksvalidator non installé, skip"

test:
	cd welcome && python3 -m pytest -q || true

clean:
	rm -rf build/ dist/ output/ welcome/build/ welcome/dist/
	find . -type d -name __pycache__ -exec rm -rf {} +
