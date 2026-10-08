#!/usr/bin/env python3
"""Compatibilidade: painel benchmark via pacote StudAI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.tools.benchmark import main

if __name__ == "__main__":
    main()
