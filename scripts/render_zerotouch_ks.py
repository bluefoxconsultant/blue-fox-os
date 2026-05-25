#!/usr/bin/env python3
"""Render install/blue-fox-install.ks.template for a given tenant.

Two production consumers:
    1. CI ksvalidator gate — renders for slug=bf and pipes into ksvalidator
       to ensure the template stays valid.
    2. Drift check — compares the rendered output to the embedded copy in
       the bf_zerotouch_install Odoo module's QWeb template, fails CI if
       they diverge.

The Odoo controller does its OWN rendering at request time using QWeb. This
script exists for offline validation + drift detection ; it is NOT the
runtime renderer for production traffic.

Usage:
    # Render to stdout for slug=bf
    python3 scripts/render_zerotouch_ks.py --slug bf

    # Drift check against odoo-clients embed
    python3 scripts/render_zerotouch_ks.py --check-odoo-mirror
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO / "install" / "blue-fox-install.ks.template"
PROVISION_SCRIPT_PATH = REPO / "install" / "bfos_provision.py"
APPLY_SCRIPT_PATH = REPO / "install" / "bfos_apply.py"

# Tenant configs known to the offline renderer. v0.0.3 only supports BF ;
# external tenants ship their own Odoo module + config + endpoint.
#
# OIDC device-flow + policy endpoints (BFOSI10 / bf_policy):
#   AUTHENTIK_DEVICE_URL/TOKEN_URL  global Authentik OAuth2 endpoints on the
#                                   org's auth.<domain> host
#   OIDC_CLIENT_ID                  the public device-flow client ('blue-fox-os')
#   POLICY_URL                      <domain>/api/v1/policy/me (bf_policy module)
TENANTS = {
    "bf": {
        "TENANT_SLUG": "bf",
        "TENANT_NAME": "Blue Fox Inc.",
        "TENANT_DOMAIN": "bluefoxconsultant.com",
        "OCI_IMAGE_REF": "ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest",
        "LANG": "fr_CA.UTF-8",
        "ADDSUPPORT": "en_CA.UTF-8",
        "KEYBOARD_VC": "ca",
        "KEYBOARD_X": "'ca','us'",
        "TIMEZONE": "America/Montreal",
        "AUTHENTIK_DEVICE_URL": "https://auth.bluefoxconsultant.com/application/o/device/",
        "AUTHENTIK_TOKEN_URL": "https://auth.bluefoxconsultant.com/application/o/token/",
        "OIDC_CLIENT_ID": "blue-fox-os",
        "POLICY_URL": "https://bluefoxconsultant.com/api/v1/policy/me",
    },
    "bf-surface": {
        "TENANT_SLUG": "bf-surface",
        "TENANT_NAME": "Blue Fox Inc. (Surface)",
        "TENANT_DOMAIN": "bluefoxconsultant.com",
        "OCI_IMAGE_REF": "ghcr.io/bluefoxconsultant/blue-fox-os-bf-surface:latest",
        "LANG": "fr_CA.UTF-8",
        "ADDSUPPORT": "en_CA.UTF-8",
        "KEYBOARD_VC": "ca",
        "KEYBOARD_X": "'ca','us'",
        "TIMEZONE": "America/Montreal",
        "AUTHENTIK_DEVICE_URL": "https://auth.bluefoxconsultant.com/application/o/device/",
        "AUTHENTIK_TOKEN_URL": "https://auth.bluefoxconsultant.com/application/o/token/",
        "OIDC_CLIENT_ID": "blue-fox-os",
        "POLICY_URL": "https://bluefoxconsultant.com/api/v1/policy/me",
    },
}

# {{PLACEHOLDER}} — only ASCII letters, digits, underscores between braces.
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")

# A line starting with a kickstart section keyword would be read as a section
# delimiter by pykickstart even inside a heredoc, corrupting the install. Guard
# the embedded scripts against it.
_KS_SECTION_RE = re.compile(r"^%(pre|post|end|packages|onerror|traceback|addon)\b")


def _embed_script(path: pathlib.Path) -> str:
    """Read a script for verbatim embedding into a kickstart %pre/%post heredoc,
    failing loudly if any line would be mistaken for a kickstart section."""
    text = path.read_text(encoding="utf-8").rstrip("\n")
    for i, line in enumerate(text.splitlines(), 1):
        if _KS_SECTION_RE.match(line):
            raise SystemExit(
                f"[render_zerotouch_ks] {path.name}:{i} starts with a kickstart "
                f"section keyword ({line[:20]!r}); reword so no embedded line "
                f"begins with %pre/%post/%end/etc."
            )
    return text


def render(slug: str, generated_at: str | None = None) -> str:
    if slug not in TENANTS:
        raise SystemExit(
            f"[render_zerotouch_ks] unknown slug {slug!r} ; known: {sorted(TENANTS)}"
        )
    if not TEMPLATE_PATH.is_file():
        raise SystemExit(f"[render_zerotouch_ks] template not found: {TEMPLATE_PATH}")

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    vars_ = dict(TENANTS[slug])
    vars_["GENERATED_AT"] = generated_at or dt.datetime.now(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    # Embed the install-time scripts verbatim into the %pre/%post heredocs so the
    # rendered kickstart is self-contained (no second fetch at install time).
    vars_["PROVISION_SCRIPT"] = _embed_script(PROVISION_SCRIPT_PATH)
    vars_["APPLY_SCRIPT"] = _embed_script(APPLY_SCRIPT_PATH)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in vars_:
            raise SystemExit(
                f"[render_zerotouch_ks] template references unknown placeholder "
                f"{{{{ {key} }}}} for slug={slug}"
            )
        return vars_[key]

    rendered = PLACEHOLDER_RE.sub(replace, template)

    # Catch leftover placeholders the regex missed (defensive).
    leftover = PLACEHOLDER_RE.findall(rendered)
    if leftover:
        raise SystemExit(
            f"[render_zerotouch_ks] unresolved placeholders after render: {leftover}"
        )
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slug",
        default="bf",
        help="Tenant slug (default: bf). One of: " + ", ".join(sorted(TENANTS)),
    )
    parser.add_argument(
        "--check-odoo-mirror",
        action="store_true",
        help="Compare rendered output to the embedded copy in the bf_zerotouch_install Odoo module ; exit 1 on drift.",
    )
    parser.add_argument(
        "--generated-at",
        default="<RENDER_TIME>",
        help="Override the {{GENERATED_AT}} placeholder. Use a stable string for drift checks.",
    )
    args = parser.parse_args()

    rendered = render(args.slug, generated_at=args.generated_at)

    if args.check_odoo_mirror:
        # Path to the Odoo module's embedded template. Lives outside this repo
        # in ~/odoo-clients/bf-prod/addons/ ; provide via env or expect default.
        import os

        odoo_addon = pathlib.Path(
            os.environ.get(
                "BF_ZEROTOUCH_ADDON",
                str(pathlib.Path.home() / "odoo-clients" / "bf-prod" / "addons" / "bf_zerotouch_install"),
            )
        )
        embed = odoo_addon / "data" / "blue-fox-install.ks.template"
        if not embed.is_file():
            print(
                f"[render_zerotouch_ks] Odoo embed not found: {embed}\n"
                f"  Set BF_ZEROTOUCH_ADDON=/path/to/bf_zerotouch_install or skip "
                f"--check-odoo-mirror in environments without the Odoo addon checkout.",
                file=sys.stderr,
            )
            return 2  # Soft fail — distinct from drift exit 1.
        embedded = embed.read_text(encoding="utf-8")
        canonical = TEMPLATE_PATH.read_text(encoding="utf-8")
        if canonical != embedded:
            print(
                f"[render_zerotouch_ks] DRIFT: {embed} differs from {TEMPLATE_PATH}.\n"
                f"  Sync them: cp {TEMPLATE_PATH} {embed}",
                file=sys.stderr,
            )
            return 1
        print(f"[render_zerotouch_ks] OK : Odoo embed matches canonical template")
        return 0

    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
