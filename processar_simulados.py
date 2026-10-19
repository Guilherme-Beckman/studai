#!/usr/bin/env python3
"""Compatibilidade: ingestão de lote via pacote StudAI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.features.questions.batch import main

if __name__ == "__main__":
    raise SystemExit(main())
