"""
Contrato do OCR usado pelo extrator nas páginas sem camada de texto legível.

O extrator só conhece esta interface; a implementação (Tesseract, em
``studai.integrations.ocr``) é injetada por quem chama ``parse``/``extract_pdf``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pymupdf


@dataclass
class OcrWord:
    text: str
    bbox: tuple[float, float, float, float]     # coordenadas da página (pt), não do bitmap


@dataclass
class OcrLine:
    words: list[OcrWord] = field(default_factory=list)


class OcrProvider(Protocol):
    def read_page(self, page: pymupdf.Page, ignore: list[pymupdf.Rect],
                  clip: pymupdf.Rect | None = None) -> list[OcrLine]:
        """
        Linhas lidas da página (ou só de ``clip``), com as palavras em ordem de leitura e
        caixas em coordenadas da página. As áreas em ``ignore`` (fotos, mapas) ficam de
        fora: o OCR de uma tirinha só traz lixo e ainda engorda as linhas vizinhas.
        """
        ...
