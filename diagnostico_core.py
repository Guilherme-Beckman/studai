"""Compatibilidade de import; use studai.features.diagnostics.service."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.features.diagnostics.service import *  # noqa: F401,F403
