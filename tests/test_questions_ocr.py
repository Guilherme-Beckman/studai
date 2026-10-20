"""OCR das provas sem camada de texto legível (1997, 2002 dia 2, 2021), com motor falso."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions import ocr
from studai.features.questions.extractor import parse

GARBLED = "CpqÇåuruâçq qÇåäq gà rüåüà ghguÉü gâçqxg âçq yqxtüä iüääqàéüÇpq gàéqiåüà"


def _line(text: str, x: float, y: float, size: float = 10.0, conf: float = 90.0) -> ocr.OcrLine:
    words, cx = [], x
    for w in text.split():
        width = 0.5 * size * len(w)
        words.append(ocr.OcrWord(w, pymupdf.Rect(cx, y, cx + width, y + size), conf))
        cx += width + 0.3 * size
    return ocr.OcrLine(words)


def _question_lines(num_text: str, y: float, x: float = 40) -> list[ocr.OcrLine]:
    lines = [_line(num_text, x, y), _line("Qual cidade é a capital do país?", x, y + 14)]
    for k, (letter, city) in enumerate(zip("ABCDE", ("Brasília", "Recife", "Belém", "Natal", "Manaus"))):
        lines.append(_line(f"({letter}) {city}", x, y + 30 + 13 * k))
    return lines


class FakeEngine:
    def __init__(self, pages: dict[int, list[ocr.OcrLine]]):
        self.pages = pages
        self.calls: list[tuple[int, int]] = []

    def __call__(self, page, dpi):
        self.calls.append((page.number, dpi))
        return self.pages.get(page.number, [])


def _garbled_doc() -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    for k in range(12):
        page.insert_text((40, 80 + 16 * k), GARBLED, fontsize=9)
    return doc


def _scan_doc() -> pymupdf.Document:
    """Página "escaneada": uma imagem grande (36 dpi), um quadrado preto (a figura, x 330–470, y 560–700) e nenhum texto."""
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 250, 350), False)
    pix.set_rect(pix.irect, (255,))
    pix.set_rect(pymupdf.IRect(150, 250, 220, 320), (0,))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(30, 60, 530, 760), pixmap=pix)
    return doc


class TextLayerStatusTests(unittest.TestCase):
    def test_normal_text_is_ok(self):
        doc = pymupdf.open()
        page = doc.new_page()
        for k in range(10):
            page.insert_text((40, 80 + 16 * k), "O estudo da cidade e a vida no campo são temas de uma questão.")
        self.assertEqual(ocr.text_layer_status(page), ocr.OK)

    def test_garbled_encoding_is_garbage(self):
        # 2002 dia 2: letras latinas, mas nenhuma palavra comum do português.
        self.assertEqual(ocr.text_layer_status(_garbled_doc()[0]), ocr.GARBAGE)

    def test_bitmap_font_is_garbage(self):
        # 2021: fonte Type3 com corpo nominal ~0,1 pt.
        doc = pymupdf.open()
        page = doc.new_page()
        for k in range(30):
            page.insert_text((40, 80 + 10 * k), "abcdefghij" * 3, fontsize=0.2)
        self.assertEqual(ocr.text_layer_status(page), ocr.GARBAGE)

    def test_scanned_page_is_missing(self):
        self.assertEqual(ocr.text_layer_status(_scan_doc()[0]), ocr.MISSING)

    def test_blank_page_is_ok(self):
        self.assertEqual(ocr.text_layer_status(pymupdf.open().new_page()), ocr.OK)


class TesseractTests(unittest.TestCase):
    TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "1\t1\t0\t0\t0\t0\t0\t0\t2480\t3508\t-1\t\n"
           "5\t1\t1\t1\t1\t1\t100\t200\t50\t40\t96.0\t11\n"
           "5\t1\t2\t1\t1\t1\t100\t300\t200\t40\t91.5\tUm\n"
           "5\t1\t2\t1\t1\t2\t320\t300\t300\t40\t90.0\tmarceneiro\n"
           "5\t1\t3\t1\t1\t1\t900\t900\t10\t10\t95.0\t \n")

    def test_parse_tsv_groups_words_in_lines_with_pdf_coordinates(self):
        lines = ocr.parse_tsv(self.TSV, 72 / 300)
        self.assertEqual([ln.text for ln in lines], ["11", "Um marceneiro"])
        self.assertAlmostEqual(lines[1].bbox.x0, 24.0)
        self.assertAlmostEqual(lines[1].bbox.y1, 81.6, places=3)
        self.assertAlmostEqual(lines[1].conf, 90.75)

    def test_engine_renders_page_and_calls_binary(self):
        calls = []

        def runner(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout=self.TSV.encode())

        engine = ocr.TesseractOcr(binary="/fake/tesseract", runner=runner)
        lines = engine(pymupdf.open().new_page(), 300)
        cmd, kw = calls[0]
        self.assertEqual(cmd[0], "/fake/tesseract")
        self.assertIn("por", cmd)
        self.assertTrue(kw["input"].startswith(b"\x89PNG"))
        self.assertEqual(lines[0].text, "11")

    def test_default_engine_can_be_disabled(self):
        with mock.patch.dict(os.environ, {"STUDAI_OCR": "0"}):
            self.assertIsNone(ocr.default_engine())
        with mock.patch.dict(os.environ, {"STUDAI_OCR": "1"}), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(ocr.default_engine())

    def test_dpi_follows_scan_resolution(self):
        doc = _scan_doc()      # 250 px em 500 pt = 36 dpi -> mínimo 150
        self.assertEqual(ocr.ocr_dpi(doc[0], ocr.MISSING), 150)
        self.assertEqual(ocr.ocr_dpi(doc[0], ocr.GARBAGE), 300)


class OcrParseTests(unittest.TestCase):
    def test_garbage_page_uses_ocr_lines(self):
        doc = _garbled_doc()
        engine = FakeEngine({0: [_line("Concurso Vestibular FUVEST — 2021", 300, 20)]
                             + _question_lines("01", 120) + _question_lines("02", 260)})
        questions, _ = parse(doc, ocr=engine)
        self.assertEqual(engine.calls, [(0, 300)])
        self.assertEqual(sorted(questions), [1, 2])
        self.assertEqual(questions[1]["enunciado"], ["Qual cidade é a capital do país?"])
        alts = {k: " ".join(v["texto"]) for k, v in questions[2]["alternativas"].items()}
        self.assertEqual(alts["A"], "Brasília")
        self.assertEqual(sorted(alts), list("ABCDE"))
        # nada do texto embaralhado entra na questão
        self.assertNotIn("CpqÇåuruâçq", str(questions))

    def test_without_engine_the_page_is_skipped(self):
        questions, _ = parse(_garbled_doc(), ocr=None)
        self.assertEqual(questions, {})

    def test_engine_failure_is_not_fatal(self):
        def broken(page, dpi):
            raise FileNotFoundError("tesseract")

        questions, _ = parse(_garbled_doc(), ocr=broken)
        self.assertEqual(questions, {})

    def test_ok_pages_never_call_the_engine(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 60), "01", fontname="hebo")
        page.insert_text((40, 80), "Qual cidade é a capital do país?")
        engine = FakeEngine({})
        parse(doc, ocr=engine)
        self.assertEqual(engine.calls, [])

    def test_scanned_page_with_misread_number_and_figure(self):
        # 1997: "K.01 - texto" com o OCR lendo "K.0l"; a figura é tinta fora do texto.
        doc = _scan_doc()
        lines = [_line("K.0l - Qual cidade aparece na figura ao lado?", 35, 80, size=8)]
        for k, (letter, city) in enumerate(zip("abcde", ("Brasília", "Recife", "Belém", "Natal", "Manaus"))):
            lines.append(_line(f"{letter}) {city}", 35, 100 + 12 * k, size=8))
        lines += [_line("K.02 - Outra pergunta sobre o mapa do país?", 35, 180, size=8)]
        lines += [_line(f"{letter}) {city}", 35, 200 + 12 * k, size=8)
                  for k, (letter, city) in enumerate(zip("abcde", ("Lima", "Quito", "Bogotá", "Caracas", "Assunção")))]
        questions, _ = parse(doc, ocr=FakeEngine({0: lines}))
        self.assertEqual(sorted(questions), [1, 2])
        self.assertEqual(questions[1]["enunciado"], ["Qual cidade aparece na figura ao lado?"])
        self.assertEqual(sorted(questions[1]["alternativas"]), list("ABCDE"))
        # o quadrado preto é figura da questão 2; a faixa escaneada não
        figs = [r for _, r, _ in questions[2].get("_figs", [])]
        self.assertEqual(len(figs), 1)
        self.assertTrue(figs[0].contains(pymupdf.Rect(335, 565, 465, 695)))
        self.assertLess(figs[0].width, 160)
        self.assertFalse(questions[1].get("_figs"))

    def test_ocr_marker_tolerates_misread_digit_and_skips_lost_number(self):
        from studai.features.questions.extractor import Row, _ocr_marker

        def row(text):
            return Row(text, pymupdf.Rect(0, 0, 10, 10), 0, {"OCR"})

        self.assertEqual(_ocr_marker(row("K. 1á - Duas esferas"), 14), (14, "Duas esferas"))
        self.assertEqual(_ocr_marker(row("K.73 – Em frente"), 71), (73, "Em frente"))
        self.assertIsNone(_ocr_marker(row("K.79 – longe demais"), 71))
        native = Row("K.15 - texto", pymupdf.Rect(0, 0, 10, 10), 0, {"Courier"})
        self.assertIsNone(_ocr_marker(native, 15))

    def test_garbled_lowercase_alternatives_follow_the_sequence(self):
        # 1997 "a)": o OCR lê "aj)", "bh)", "co)".
        doc = _scan_doc()
        lines = [_line("K.01 - Quanto vale o ângulo?", 35, 80, size=8)]
        for k, label in enumerate(("aj) 0º", "bh) 30º", "co) 45º", "d) 60º", "e) 90º")):
            lines.append(_line(label, 35, 100 + 12 * k, size=8))
        questions, _ = parse(doc, ocr=FakeEngine({0: lines}))
        alts = {k: " ".join(v["texto"]) for k, v in questions[1]["alternativas"].items()}
        self.assertEqual(alts, {"A": "0º", "B": "30º", "C": "45º", "D": "60º", "E": "90º"})

    def test_scan_figures_ignores_text_boxes(self):
        doc = _scan_doc()
        page = doc[0]
        region = [pymupdf.Rect(30, 60, 530, 760)]
        self.assertEqual(len(ocr.scan_figures(page, [], region)), 1)
        self.assertEqual(ocr.scan_figures(page, [pymupdf.Rect(320, 550, 480, 710)], region), [])


if __name__ == "__main__":
    unittest.main()
