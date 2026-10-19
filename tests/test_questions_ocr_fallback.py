"""OCR só nas páginas sem texto legível (2021 em bitmap, 1997 escaneada, 2002 dia 2), com provedor simulado."""
import sys
import unittest
from pathlib import Path

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions import extractor
from studai.features.questions.extractor import parse
from studai.features.questions.ocr import OcrLine, OcrWord

ALTS = ("Brasília", "São Paulo", "Belém", "Maceió", "Manaus")


def _line(x: float, y: float, text: str, h: float = 10.0, w: float = 6.0) -> OcrLine:
    words, cx = [], x
    for word in text.split():
        words.append(OcrWord(word, (cx, y, cx + w * len(word), y + h)))
        cx += w * len(word) + w
    return OcrLine(words)


class FakeOcr:
    """Devolve sempre as mesmas linhas e anota quais páginas foram lidas."""

    def __init__(self, lines: list[OcrLine]):
        self.lines = lines
        self.pages: list[int] = []

    def read_page(self, page, ignore, clip=None):
        self.pages.append(page.number)
        return self.lines


def _question_lines(num: int, y: float, x: float = 40) -> list[OcrLine]:
    lines = [_line(x, y, f"{num:02d}", h=14), _line(x, y + 20, "Qual cidade é a capital do país?")]
    lines += [_line(x, y + 40 + 14 * k, f"({letter}) {text}") for k, (letter, text) in enumerate(zip("ABCDE", ALTS))]
    return lines


def _bitmap_page(doc):
    """Página como a de 2021: códigos sem relação com o glifo num corpo de ~0,1 pt."""
    page = doc.new_page()
    for k in range(12):
        page.insert_text((40, 80 + 10 * k), "DCB=EDFGHIJKL" * 2, fontsize=0.1)
    return page


class OcrFallbackTests(unittest.TestCase):
    def test_bitmap_page_is_read_by_ocr(self):
        doc = pymupdf.open()
        _bitmap_page(doc)
        ocr = FakeOcr(_question_lines(1, 60) + _question_lines(2, 200))
        questions, _ = parse(doc, ocr)
        self.assertEqual(ocr.pages, [0])
        self.assertEqual(sorted(questions), [1, 2])
        self.assertEqual(questions[2]["enunciado"], ["Qual cidade é a capital do país?"])
        alts = {k: " ".join(a["texto"]) for k, a in questions[1]["alternativas"].items()}
        self.assertEqual(alts, dict(zip("ABCDE", ALTS)))

    def test_without_provider_bitmap_text_is_dropped(self):
        doc = pymupdf.open()
        _bitmap_page(doc)
        questions, _ = parse(doc)
        self.assertEqual(questions, {})

    def test_readable_page_never_calls_ocr(self):
        doc = pymupdf.open()
        page = doc.new_page()
        for k, line in enumerate(("1", "Qual cidade é a capital do país?",
                                  *(f"({letter}) {text}" for letter, text in zip("ABCDE", ALTS)))):
            page.insert_text((40, 60 + 16 * k), line)
        ocr = FakeOcr([])
        questions, _ = parse(doc, ocr)
        self.assertEqual(ocr.pages, [])
        self.assertEqual(sorted(questions), [1])

    def test_scanned_page_triggers_ocr(self):
        # 1997: o texto é imagem; a camada de texto só tem o cabeçalho.
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 30), "FUVEST 1997")
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 40, 60), False)
        pix.clear_with(255)
        page.insert_image(pymupdf.Rect(30, 50, 290, 800), pixmap=pix, keep_proportion=False)
        self.assertEqual(extractor._needs_ocr(page, extractor._raw_lines(page)), "scan")

    def test_garbled_encoding_triggers_ocr(self):
        # 2002 dia 2: fonte com codificação deslocada ("RTQEGUUQU" = "PROCESSOS").
        garbled = pymupdf.open().new_page()
        readable = pymupdf.open().new_page()
        for k in range(20):
            garbled.insert_text((40, 60 + 12 * k), "UGOK KPVGPUKXC FQU RTQEGUUQU GTQUKXQU")
            readable.insert_text((40, 60 + 12 * k), "Um dos processos de erosão que ocorre na região")
        self.assertEqual(extractor._needs_ocr(garbled, extractor._raw_lines(garbled)), "garbled")
        self.assertIsNone(extractor._needs_ocr(readable, extractor._raw_lines(readable)))

    def test_ocr_line_across_columns_is_split(self):
        # O OCR pode juntar numa linha só as duas colunas na mesma altura.
        left, right = _line(40, 100, "(A) Brasília"), _line(320, 100, "Texto da outra coluna")
        lines = extractor._ocr_lines([OcrLine(left.words + right.words)], 595)
        texts = ["".join(ch["c"] for ch in ln["chars"]) for ln in lines]
        self.assertEqual(texts, ["(A) Brasília", "Texto da outra coluna"])


if __name__ == "__main__":
    unittest.main()
