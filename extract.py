#!/usr/bin/env python3
"""Compatibilidade: extração de provas via pacote StudAI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.features.questions.extractor import main

if __name__ == "__main__":
    main()
