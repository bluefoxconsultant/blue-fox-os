"""Rend bfos_live et bfos_provision importables pour les essais."""
import pathlib
import sys

ICI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))
sys.path.insert(0, str(ICI.parent.parent / "install"))
