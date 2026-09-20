"""Apply submodules — each integration is a best-effort apply_*(tenant, **kw).

Pattern: returns (ok: bool, message: str). Callers must catch nothing — internal
exceptions are caught and converted to (False, repr(e)) by each module.
"""
from .rclone_mount import apply_rclone_mount, apply_session_mounts
from .kaccounts import apply_kaccounts
from .bitwarden_prefs import apply_bitwarden_prefs
from .brave_policy import apply_brave_policy
from .kde_theme import apply_kde_theme
from .horloges import appliquer_horloges
from .photo import appliquer_photo

__all__ = [
    "appliquer_horloges",
    "appliquer_photo",
    "apply_rclone_mount",
    "apply_session_mounts",
    "apply_kaccounts",
    "apply_bitwarden_prefs",
    "apply_brave_policy",
    "apply_kde_theme",
]
