#!/usr/bin/env python3
"""Compatibilidade: comandos de diagnóstico via pacote StudAI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.features.diagnostics.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
