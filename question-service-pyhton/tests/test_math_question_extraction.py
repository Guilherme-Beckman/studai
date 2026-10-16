import tempfile
import unittest
from pathlib import Path
import sys

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions import extractor
from studai.features.questions.extractor import finalize, parse


def _ch(c, x0, y0, x1, y1, oy, size=9.0, color=0, font="CambriaMath"):
    return {"c": c, "bbox": pymupdf.Rect(x0, y0, x1, y1), "oy": oy,
            "size": size, "color": color, "font": font}


def _line(chars):
    rect = pymupdf.Rect(chars[0]["bbox"])
    for ch in chars[1:]:
        rect |= ch["bbox"]
    return {"chars": chars, "bbox": rect, "fonts": {ch["font"] for ch in chars}}


class FormulaReconstructionTests(unittest.TestCase):
    def test_decodes_cambria_math_glyphs_only_in_math_fonts(self):
        self.assertEqual(extractor._decode_char("\u0b36", "CambriaMath"), "2")
        self.assertEqual(extractor._decode_char("\u0bdc", "CambriaMath"), "i")
        self.assertEqual(extractor._decode_char("\u0dcd", "CambriaMath"), "∑")
        self.assertEqual(extractor._decode_char("\U0001d443", "CambriaMath"), "P")
        self.assertEqual(extractor._decode_char("\uf072", "SymbolMT"), "ρ")
        self.assertEqual(extractor._decode_char("\u0b36", "ArialMT"), "\u0b36")

    def test_stacked_fraction_becomes_inline(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(60, 104.5, 65, 105.1), color=None, fill=(0, 0, 0))
        host = _line([_ch("é", 50, 100, 55, 110, 108, font="ArialMT"),
                      _ch(" ", 55, 100, 58, 110, 108, font="ArialMT"),
                      _ch(".", 66, 100, 68, 110, 108, font="ArialMT")])
        lines = [host,
                 _line([_ch("\u0b35", 60, 96, 65, 104, 102.5, size=6.5)]),
                 _line([_ch("\u0b36", 60, 106, 65, 114, 112, size=6.5)])]
        extractor._decode_glyphs(lines)
        extractor._rebuild_fractions(lines, page)
        self.assertEqual("".join(ch["c"] for ch in host["chars"]), "é 1/2.")
        self.assertEqual(lines, [host])

    def test_table_rule_is_not_a_fraction(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(10, 104.5, 60, 105.1), color=None, fill=(0, 0, 0))
        lines = [_line([_ch("A", 30, 96, 36, 104, 102.5, font="ArialMT")]),
                 _line([_ch("B", 30, 106, 36, 114, 112, font="ArialMT")])]
        extractor._rebuild_fractions(lines, page)
        self.assertEqual([ln["chars"][0]["c"] for ln in lines], ["A", "B"])

    def test_scripts_and_colors_in_line_text(self):
        line = _line([_ch("l", 0, 0, 3, 10, 8), _ch("o", 3, 0, 6, 10, 8), _ch("g", 6, 0, 9, 10, 8),
                      _ch("\u0b36", 9, 3, 12, 11, 10, size=6.5), _ch(" ", 12, 0, 14, 10, 8, font="ArialMT"),
                      _ch("A", 14, 0, 20, 10, 8, color=0x33A9E0, font="ArialMT"),
                      _ch("A", 20, 0, 26, 10, 8, color=0xE12C27, font="ArialMT")])
        extractor._decode_glyphs([line])
        extractor._mark_scripts([line])
        self.assertEqual(extractor._line_text(line), "log₂ [azul: A][vermelho: A]")


class MathQuestionExtractionTests(unittest.TestCase):
    def test_fuvest_2026_math_question_has_clean_text_and_source_crop(self):
        source = (APP / "fuvest_acervo/2026/1a_fase/provas/fuvest2026-fase1-prova-V1.pdf")
        if not source.is_file():
            self.skipTest("PDF de regressão FUVEST 2026 não está disponível")

        doc = pymupdf.open(source)
        try:
            questions, _ = parse(doc)
            q3, q4 = questions[3], questions[4]

            self.assertIn("eleição para um conselho", " ".join(q3["enunciado"]))
            self.assertIn("CambriaMath", q4["_fonts"])
            self.assertNotIn("¬", " ".join(q4["enunciado"]))

            with tempfile.TemporaryDirectory() as directory:
                outdir = Path(directory)
                finalize(doc, {3: q3, 4: q4}, {}, outdir, dpi=120, full_crops=True)

                self.assertIn("formula_matematica", q4["revisar"])
                self.assertNotIn("glifo_estranho", q4["revisar"])
                self.assertIn("∑ᵢPᵢ(x)log₂Pᵢ(x)", q4["enunciado"])
                self.assertIn("−([azul: 1/2 log₂ 1/2] + [vermelho: 1/2 log₂ 1/2] )= 1", q4["enunciado"])
                self.assertIn("([azul: AAA][vermelho: A])", q4["enunciado"])
                self.assertEqual({k: a["texto"] for k, a in q4["alternativas"].items()},
                                 {"A": "5 − 9/4 log₂3", "B": "1/2 − 5/2 log₂3", "C": "3/2",
                                  "D": "9/4 log₂3", "E": "3"})
                for number in (3, 4):
                    crops = questions[number]["imagens_completas"]
                    self.assertTrue(crops, f"Questão {number} deve ter recorte fonte")
                    self.assertTrue((outdir / crops[0]).is_file())
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
