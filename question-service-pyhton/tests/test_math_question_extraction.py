import tempfile
import unittest
from pathlib import Path
import sys

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions.extractor import finalize, parse


class MathQuestionExtractionTests(unittest.TestCase):
    def test_fuvest_2026_math_question_has_clean_text_and_source_crop(self):
        source = (APP / "fuvest_acervo/2026/1a_fase/provas/fuvest2026-fase1-prova-V1.pdf")
        if not source.is_file():
            self.skipTest("PDF de regressão FUVEST 2026 não está disponível")

        doc = pymupdf.open(source)
        try:
            questions, _ = parse(doc)
            q3, q4 = questions[3], questions[4]

            self.assertEqual(q3["enunciado"], [])
            self.assertIn("CambriaMath", q4["_fonts"])
            self.assertNotIn("¬", " ".join(q4["enunciado"]))

            with tempfile.TemporaryDirectory() as directory:
                outdir = Path(directory)
                finalize(doc, {3: q3, 4: q4}, {}, outdir, dpi=120, full_crops=True)

                self.assertIn("formula_matematica", q4["revisar"])
                self.assertIn("glifo_estranho", q4["revisar"])
                for number in (3, 4):
                    crops = questions[number]["imagens_completas"]
                    self.assertTrue(crops, f"Questão {number} deve ter recorte fonte")
                    self.assertTrue((outdir / crops[0]).is_file())
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
