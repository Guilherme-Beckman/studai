"""Provas e listas de outras origens: "QUESTÃO 91", numeração no meio e alternativas "A)"/"A."."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from studai.api import server
from studai.features.questions.extractor import extract_pdf
from studai.features.reports.service import exam_label

ALTS = ("Brasília", "Recife", "Belém", "Natal", "Manaus")


def _question(page, label, y, letter_format, stem="Qual cidade é a capital do país?"):
    page.insert_text((40, y), label, fontname="hebo", fontsize=12)
    page.insert_text((40, y + 20), stem)
    for k, (letter, city) in enumerate(zip("ABCDE", ALTS)):
        page.insert_text((40, y + 38 + 13 * k), letter_format.format(letter=letter, text=city))
    return y + 38 + 13 * 5 + 30


class OtherSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def extract(self, labels, letter_format):
        doc = pymupdf.open()
        page = doc.new_page()
        y = 60
        for label in labels:
            y = _question(page, label, y, letter_format)
        pdf = self.root / "prova.pdf"
        doc.save(pdf)
        doc.close()
        out = extract_pdf(pdf, self.root / "out", dpi=40, ocr=None)
        return json.loads(out.read_text(encoding="utf-8"))["questoes"]

    def assert_alternatives(self, question):
        self.assertEqual({k: a["texto"] for k, a in question["alternativas"].items()}, dict(zip("ABCDE", ALTS)))
        self.assertEqual(question["enunciado"], "Qual cidade é a capital do país?")

    def test_question_word_marker_starting_mid_exam(self):
        questions = self.extract(["QUESTÃO 91", "QUESTÃO 92", "QUESTÃO 93"], "{letter}) {text}")
        self.assertEqual([q["numero"] for q in questions], [91, 92, 93])
        for question in questions:
            self.assert_alternatives(question)

    def test_dot_alternatives_and_lowercase_word(self):
        questions = self.extract(["Questão 1.", "Questão 2."], "{letter}. {text}")
        self.assertEqual([q["numero"] for q in questions], [1, 2])
        self.assert_alternatives(questions[1])

    def test_fuvest_format_still_wins_ties(self):
        questions = self.extract(["01", "02"], "({letter}) {text}")
        self.assertEqual([q["numero"] for q in questions], [1, 2])
        self.assert_alternatives(questions[0])

    def test_upload_with_origin_marks_the_exam(self):
        doc = pymupdf.open()
        page = doc.new_page()
        y = 60
        for label in ("QUESTÃO 91", "QUESTÃO 92"):
            y = _question(page, label, y, "{letter}) {text}")
        pdf = self.root / "enem.pdf"
        doc.save(pdf)
        doc.close()
        with patch.object(server, "UPLOADS_ROOT", self.root / "uploads"), \
             patch.object(server, "PROCESSED_ROOT", self.root / "processed"), \
             patch.object(server, "SERVICE_ROOT", self.root), \
             patch.object(server, "DEFAULT_DIAGNOSTICS_DB", self.root / "db.sqlite3"), \
             patch.dict(os.environ, {"STUDAI_OCR": "0"}):
            with self.assertRaises(ValueError):
                server._process_exam(pdf.name, pdf.read_bytes(), {"provider": "demo", "dpi": 72, "originType": "lista"})
            result = server._process_exam(pdf.name, pdf.read_bytes(), {
                "provider": "demo", "dpi": 72, "originType": "vestibular", "originName": "ENEM 2023 dia 2"})
            summary, path = server._find_exam(result["examId"])
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(summary["origin"], {"tipo": "vestibular", "nome": "ENEM 2023 dia 2"})
        self.assertEqual(data["origem"]["nome"], "ENEM 2023 dia 2")
        self.assertEqual([q["numero"] for q in data["questoes"]], [91, 92])

    def test_label_uses_the_origin_name(self):
        self.assertEqual(exam_label("enem-2023-dia2-abc", origin_name="ENEM 2023 dia 2"), "ENEM 2023 dia 2")
        self.assertEqual(exam_label("fuvest2024_primeira_fase_prova_K"), "Prova FUVEST 2024 (K)")


if __name__ == "__main__":
    unittest.main()
