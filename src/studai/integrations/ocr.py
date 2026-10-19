"""OCR com o Tesseract embutido no PyMuPDF (precisa só do tessdata: apt install tesseract-ocr-por)."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from studai.features.questions.ocr import OcrLine, OcrWord


class TesseractOcr:
    def __init__(self, language: str = "por", dpi: int = 300, tessdata: str | None = None):
        self.language = language
        self.dpi = dpi
        self.tessdata = tessdata or pymupdf.get_tessdata()   # RuntimeError se não houver tessdata

    def read_page(self, page: pymupdf.Page, ignore: list[pymupdf.Rect],
                  clip: pymupdf.Rect | None = None) -> list[OcrLine]:
        area = pymupdf.Rect(clip or page.rect)
        zoom = pymupdf.Matrix(self.dpi / 72, self.dpi / 72)
        pix = page.get_pixmap(matrix=zoom, clip=area, colorspace=pymupdf.csGRAY, alpha=False)
        for r in ignore:
            if not pymupdf.Rect(r).intersects(area):
                continue                       # interseção vazia viraria um retângulo que apaga tudo
            hole = (pymupdf.Rect(r) & area) - (area.x0, area.y0, area.x0, area.y0)
            pix.set_rect((hole * zoom).irect & pix.irect, (255,))
        # Página avulsa só com a imagem mascarada, no tamanho do recorte: as palavras já saem em pt.
        scratch = pymupdf.open()
        sp = scratch.new_page(width=area.width, height=area.height)
        sp.insert_image(sp.rect, pixmap=pix)
        tp = sp.get_textpage_ocr(language=self.language, dpi=self.dpi, full=True, tessdata=self.tessdata)
        dx, dy = area.x0, area.y0
        lines: dict[tuple[int, int], OcrLine] = {}
        for x0, y0, x1, y1, text, block, line, _ in sp.get_text("words", textpage=tp, sort=False):
            lines.setdefault((block, line), OcrLine()).words.append(OcrWord(text, (x0 + dx, y0 + dy, x1 + dx, y1 + dy)))
        return list(lines.values())


def default_ocr(language: str = "por") -> TesseractOcr | None:
    """Tesseract quando o tessdata do idioma está instalado; senão None (sem OCR)."""
    try:
        ocr = TesseractOcr(language)
    except RuntimeError:
        return None
    return ocr if (Path(ocr.tessdata) / f"{language}.traineddata").exists() else None
