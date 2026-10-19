#!/usr/bin/env python3
"""Compatibilidade: scraper do acervo FUVEST via pacote StudAI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from studai.tools.fuvest_scraper import main

if __name__ == "__main__":
    main()
