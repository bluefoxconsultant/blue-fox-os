"""Cle live generique (#26870) : le tenant.json de l'organisation choisie remplace celui de l'image."""
import json
import os

import pytest

from bluefox_welcome import tenant as t


def test_tenant_de_l_organisation_prefere(tmp_path, monkeypatch):
    image, org = tmp_path / "image.json", tmp_path / "org.json"
    image.write_text(json.dumps({"slug": "bf", "services": {"nextcloud": {"url": "https://nc.bf"}}}))
    org.write_text(json.dumps({"slug": "boreal", "services": {"nextcloud": {"url": "https://nuage.boreal"}}}))
    monkeypatch.setattr(t, "TENANT_FILE", image)
    monkeypatch.setattr(t, "ORGANISATION_FILE", org)
    monkeypatch.setattr(t, "_depose_par_root", lambda p: p == org)
    assert t.get_service_url(t.load_tenant(), "nextcloud") == "https://nuage.boreal"
    monkeypatch.setattr(t, "_depose_par_root", lambda p: False)
    assert t.get_service_url(t.load_tenant(), "nextcloud") == "https://nc.bf"


def test_depose_par_root_refuse_un_lien_ou_un_autre_proprietaire(tmp_path):
    f = tmp_path / "tenant.json"
    f.write_text("{}")
    assert t._depose_par_root(f) is (os.getuid() == 0)
    lien = tmp_path / "lien.json"
    lien.symlink_to(f)
    assert t._depose_par_root(lien) is False
    assert t._depose_par_root(tmp_path / "absent.json") is False
