"""Garde-fous sur la construction de l'ISO (panne DNS du 2026-07-25).

bootc-image-builder tourne en ROOTFUL et depsolve l'environnement Anaconda
depuis le reseau. Sur une machine dont le seul resolveur est Tailscale MagicDNS
(100.100.100.100), un pont podman rootful n'y a pas acces : tailscaled ne sert
cette adresse qu'aux processus locaux. Le build d'IMAGE (rootless, via le
namespace de l'hote) marche pourtant — d'ou une machine ou l'image se construit
et l'ISO echoue, apres 4 minutes de pull et sans que la cause soit nommee.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_ISO = REPO_ROOT / "scripts" / "build-iso.sh"


def _body() -> str:
    return BUILD_ISO.read_text()


def test_bib_runs_in_the_host_network_by_default():
    assert 'BIB_NETWORK="${BIB_NETWORK:-host}"' in _body(), (
        "le namespace de l'hote est le seul chemin DNS qui fonctionne quand le "
        "resolveur est Tailscale"
    )


def test_network_mode_is_passed_to_the_bib_run():
    body = _body()
    run = body[body.index('"${ENGINE}" run'):]
    assert '--network="${BIB_NETWORK}"' in run, "le mode reseau n'atteint pas le run BIB"


def test_dns_override_is_available():
    """Un resolveur explicite reste possible si l'hote change de configuration."""
    body = _body()
    assert "BIB_DNS" in body
    assert "--dns=" in body


def test_dns_is_checked_before_the_long_build():
    """La panne ne s'est manifestee qu'apres le pull de BIB : le controle doit
    venir AVANT, et dans le meme mode reseau que le run reel, sinon il ne prouve
    rien."""
    body = _body()
    pre = body.index("preflight DNS")
    run = body.index('"${ENGINE}" run')
    assert pre < run, "le controle DNS doit precedent le run BIB"
    preflight = body[pre:run]
    assert '--network="${BIB_NETWORK}"' in preflight, (
        "un controle dans un autre mode reseau ne prouve rien"
    )
    assert "getent hosts" in preflight


def test_failure_message_names_the_cause_and_the_workarounds():
    body = _body()
    assert "Tailscale" in body and "100.100.100.100" in body
    assert re.search(r"BIB_NETWORK=host", body)
    assert re.search(r"BIB_DNS=", body)
