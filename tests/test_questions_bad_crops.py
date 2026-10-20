"""Recortes ruins do levantamento da Fase 3 (docs/bad-crops-survey.md), em PDFs sintéticos."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions.extractor import extract_pdf, parse

ALTS = ("Brasília", "Recife", "Belém", "Natal", "Manaus")


def _question(page, num, x, y, stem="Qual cidade é a capital do país?"):
    page.insert_text((x, y), f"{num:02d}", fontname="hebo", fontsize=14)
    page.insert_text((x, y + 22), stem)
    for k, (letter, city) in enumerate(zip("ABCDE", ALTS)):
        page.insert_text((x, y + 40 + 13 * k), f"({letter}) {city}")
    return y + 40 + 13 * 5


class BadCropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, doc, **kw):
        pdf = self.root / "prova.pdf"
        doc.save(pdf)
        doc.close()
        out = extract_pdf(pdf, self.root / "out", dpi=40, ocr=None, **kw)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_white_background_behind_stem_is_not_a_figure(self):
        # 2023 K Q28 / 2026 Q42: um retângulo branco atrás de cada linha virava figura
        # e o enunciado sumia (com as alternativas A–C cortadas).
        doc = pymupdf.open()
        page = doc.new_page()
        for k in range(3):
            page.draw_rect(pymupdf.Rect(38, 70 + 12 * k, 300, 82 + 12 * k), color=None, fill=(1, 1, 1))
        _question(page, 1, 40, 60)
        q = self.extract(doc)["questoes"][0]
        self.assertEqual(q["enunciado"], "Qual cidade é a capital do país?")
        self.assertEqual(q["figuras_enunciado"], [])
        self.assertEqual({k: a["texto"] for k, a in q["alternativas"].items()}, dict(zip("ABCDE", ALTS)))

    def test_empty_stem_is_flagged_even_with_a_figure(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 60), "01", fontname="hebo", fontsize=14)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 24, 24), False)
        pix.clear_with(90)
        page.insert_image(pymupdf.Rect(40, 70, 200, 160), pixmap=pix)
        for k, (letter, city) in enumerate(zip("ABCDE", ALTS)):
            page.insert_text((40, 180 + 13 * k), f"({letter}) {city}")
        q = self.extract(doc)["questoes"][0]
        self.assertEqual(len(q["figuras_enunciado"]), 1)
        self.assertIn("enunciado_vazio", q["revisar"])

    def test_draft_page_is_not_glued_to_last_alternative(self):
        # 2024 K Q90: a página de rascunho, lida em colunas, ia para a alternativa E.
        doc = pymupdf.open()
        _question(doc.new_page(), 1, 40, 60)
        draft = doc.new_page()
        for k, word in enumerate(("O que for escrito no", "RASCUNHO", "NÃO SERÁ", "CONSIDERADO NA", "CORREÇÃO")):
            draft.insert_text((150 + 60 * (k % 2), 200 + 90 * k), word, fontsize=30)
        questions, _ = parse(doc, ocr=None)
        self.assertEqual(questions[1]["alternativas"]["E"]["texto"], ["Manaus"])

    def test_full_width_band_crop_is_not_cut_at_column(self):
        # 2025 Q1: textos em duas colunas e comando/alternativas na largura da página.
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 60), "01", fontname="hebo", fontsize=14)
        page.insert_text((40, 90), "Texto 1 na coluna da esquerda.")
        page.insert_text((320, 90), "Legenda da foto à direita.")
        page.insert_text((40, 130), "Com base nos textos e na imagem, assinale a alternativa correta sobre "
                                    "o tema apresentado.", fontsize=10)
        for k, (letter, city) in enumerate(zip("ABCDE", ALTS)):
            page.insert_text((40, 150 + 13 * k), f"({letter}) {city}")
        self.extract(doc, full_crops=True)
        crop = pymupdf.Pixmap(str(self.root / "out" / "img" / "q01_completa1.png"))
        # 40 dpi: coluna inteira ~ 4,5 pol.; a linha larga vai a ~ 480 pt (6,7 pol.)
        self.assertGreater(crop.width / 40 * 72, 420)


if __name__ == "__main__":
    unittest.main()
