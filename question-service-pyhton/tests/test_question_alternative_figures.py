"""Alternativas que são imagens: grade, pilha colada, moldura e legenda "Figura X"."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions import extractor
from studai.features.questions.extractor import extract_pdf


def _image(page, rect, shade):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 24, 24), False)
    pix.clear_with(shade)
    page.insert_image(rect, pixmap=pix)


def _stem(page):
    page.insert_text((40, 60), "1")
    page.insert_text((40, 90), "Assinale a alternativa que representa a tabela correta.")


class AlternativeFigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, doc):
        pdf = self.root / "prova.pdf"
        doc.save(pdf)
        doc.close()
        out = extract_pdf(pdf, self.root / "out", dpi=40, full_crops=False)
        return json.loads(out.read_text(encoding="utf-8"))["questoes"][0]

    def test_grid_of_images_with_side_letters_and_captions(self):
        # Layout da FUVEST 2026 Q05: (A)/(D) lado a lado, letra no meio da altura
        # da imagem e "Figura X" logo abaixo; a leitura por linha daria "(A) (D)".
        doc = pymupdf.open()
        page = doc.new_page()
        _stem(page)
        for k, (left, right) in enumerate((("A", "D"), ("B", "E"), ("C", None))):
            top = 120 + k * 110
            for x, letter in ((40, left), (160, right)):
                if not letter:
                    continue
                page.insert_text((x, top + 45), f"({letter})")
                _image(page, pymupdf.Rect(x + 20, top, x + 110, top + 90), 40 * (k + 1))
                page.insert_text((x + 25, top + 102), f"Figura {letter}", fontsize=9)
        question = self.extract(doc)
        self.assertEqual(list(question["alternativas"]), list("ABCDE"))
        for letter, option in question["alternativas"].items():
            self.assertEqual(option["texto"], "", letter)
            self.assertEqual(len(option["figuras"]), 1, letter)
            # Só a imagem da própria letra (90 pt), sem a vizinha da direita.
            width = pymupdf.Pixmap(str(self.root / "out" / option["figuras"][0])).width * 72 / 40
            self.assertLess(width, 110, letter)
        self.assertEqual(question["figuras_enunciado"], [])
        self.assertNotIn("Figura", question["enunciado"])
        self.assertNotIn("alternativas_incompletas", question["revisar"])

    def test_stacked_images_touching_each_other_are_split_per_letter(self):
        # 2024 Q50: cinco imagens empilhadas a 4 pt viram uma figura só na fusão;
        # cada letra à esquerda leva a imagem que cobre a sua altura.
        doc = pymupdf.open()
        page = doc.new_page()
        _stem(page)
        for k, letter in enumerate("ABCDE"):
            top = 120 + k * 104
            page.insert_text((40, top + 10), f"({letter})")
            _image(page, pymupdf.Rect(110, top, 230, top + 100), 30 * (k + 1))
        question = self.extract(doc)
        self.assertEqual([len(o["figuras"]) for o in question["alternativas"].values()], [1] * 5)
        self.assertEqual(question["figuras_enunciado"], [])

    def test_row_of_two_images_per_letter_becomes_one_crop(self):
        # 2025 Q60 / 2020 Q08: tabela com duas imagens por alternativa.
        doc = pymupdf.open()
        page = doc.new_page()
        _stem(page)
        for k, letter in enumerate("ABCDE"):
            top = 120 + k * 60
            page.insert_text((40, top + 30), f"({letter})")
            _image(page, pymupdf.Rect(60, top, 150, top + 58), 30 * (k + 1))
            _image(page, pymupdf.Rect(170, top, 260, top + 58), 30 * (k + 1))
        question = self.extract(doc)
        self.assertEqual([len(o["figuras"]) for o in question["alternativas"].values()], [1] * 5)

    def test_image_touching_note_box_is_not_swallowed_by_the_note(self):
        # 2019 Q39: a imagem da (E) encosta na caixa "Note e adote".
        doc = pymupdf.open()
        page = doc.new_page()
        _stem(page)
        for k, letter in enumerate("ABCDE"):
            top = 120 + k * 70
            page.insert_text((40, top + 30), f"({letter})")
            _image(page, pymupdf.Rect(70, top, 160, top + 60), 30 * (k + 1))
        box = pymupdf.Rect(36, 471, 280, 540)
        page.draw_rect(box, color=(0, 0, 0))
        page.insert_text((40, 490), "Note e adote:")
        page.insert_text((40, 505), "considere g = 10 m/s2")
        question = self.extract(doc)
        self.assertEqual(len(question["alternativas"]["E"]["figuras"]), 1)
        self.assertIn("considere", question["nota"])


class FigureGeometryTests(unittest.TestCase):
    def test_merge_keeps_the_union_instead_of_dropping_neighbours(self):
        merged = extractor._merge_rects([pymupdf.Rect(0, 0, 10, 10), pymupdf.Rect(0, 12, 10, 20),
                                         pymupdf.Rect(50, 50, 60, 60)])
        self.assertEqual(sorted(tuple(r) for r in merged),
                         [(0.0, 0.0, 10.0, 20.0), (50.0, 50.0, 60.0, 60.0)])

    def test_figure_across_both_columns_belongs_to_the_left_column(self):
        self.assertEqual(extractor._fig_col(pymupdf.Rect(95, 100, 500, 400), 595.22), 0)
        self.assertEqual(extractor._fig_col(pymupdf.Rect(330, 100, 540, 400), 595.22), 1)

    def test_mathmagic_font_glyphs(self):
        self.assertEqual(extractor._decode_char(",", "MMEtc"), "ℓ")
        self.assertEqual(extractor._decode_char("c", "MMEtc"), "°")
        self.assertEqual(extractor._decode_char(",", "ArialMT"), ",")


if __name__ == "__main__":
    unittest.main()
