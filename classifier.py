#!/usr/bin/env python3
"""Compatibilidade: classificação de questões via pacote StudAI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.features.classification.service import *  # noqa: F401,F403
from studai.features.classification.service import main

if __name__ == "__main__":
    main()
