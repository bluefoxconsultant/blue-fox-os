"""CLI entry point for the Blue Fox OS welcome agent.

P4.1 — implémentation complète à venir. Squelette pour valider l'empaquetage RPM.
"""
import argparse
import logging
import sys
from pathlib import Path

LOG = logging.getLogger("bluefox-welcome")
STATE_DIR = Path("/var/lib/bluefox-welcome")
DONE_FLAG = STATE_DIR / "done"
NEEDS_REBASE_FLAG = STATE_DIR / "needs-rebase"
USER_LOG = Path.home() / ".local/share/bluefox-welcome/firstboot.log"


def cli() -> int:
    parser = argparse.ArgumentParser(prog="bluefox-welcome")
    parser.add_argument(
        "--service-mode",
        action="store_true",
        help="Lancement par firstboot.service ; vérifie l'état et déclenche le wizard si nécessaire.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Pour le dev : retire les flags d'état et relance le wizard au prochain démarrage.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.reset:
        for flag in (DONE_FLAG,):
            try:
                flag.unlink()
                LOG.info("removed %s", flag)
            except FileNotFoundError:
                pass
        return 0

    if DONE_FLAG.exists():
        LOG.info("welcome already completed (%s) ; nothing to do", DONE_FLAG)
        return 0

    return run_wizard()


def run_wizard() -> int:
    """Lance le wizard PyQt6. À implémenter en P4.1."""
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox  # type: ignore
    except ImportError:
        LOG.error("PyQt6 not available ; falling back to terminal stub")
        print("[stub] Blue Fox OS welcome wizard ; full UI ships in v0.2 (P4.1).")
        return 0

    app = QApplication(sys.argv)
    QMessageBox.information(
        None,
        "Blue Fox OS",
        "Bienvenue sur Blue Fox OS.\n\nLe wizard complet arrive avec P4.1.\n\nClique OK pour terminer.",
    )

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DONE_FLAG.touch()
    LOG.info("wizard stub finished ; flagged done at %s", DONE_FLAG)
    return 0


if __name__ == "__main__":
    sys.exit(cli())
