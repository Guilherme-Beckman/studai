import json
import tempfile
import unittest
from pathlib import Path
import sys

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.answer_keys.service import import_answer_key, parse_answer_key_pdf
from studai.features.classification.service import DEFAULT_TAXONOMY, MockProvider, classificar
from studai.features.diagnostics.service import connect_db
from studai.features.questions.extractor import extract_pdf


class ModularFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pdf_extraction_creates_question_json(self):
        source = self.root / "prova.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        for index, line in enumerate(("1", "Enunciado da questão", "(A) opção A", "(B) opção B",
                                      "(C) opção C", "(D) opção D", "(E) opção E")):
            page.insert_text((72, 72 + index * 20), line)
        doc.save(source)
        doc.close()

        output = extract_pdf(source, self.root / "saida")
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["total_questoes"], 1)
        self.assertEqual(result["questoes"][0]["alternativas"]["E"]["texto"], "opção E")

    def test_classification_uses_provider_interface_and_preserves_json_shape(self):
        questions = self.root / "questoes.json"
        questions.write_text(json.dumps({"questoes": [{"numero": 1, "enunciado": "Leia o texto.",
            "alternativas": {"A": {"texto": "resposta"}}, "revisar": []}]}), encoding="utf-8")

        result = classificar(questions, MockProvider(), DEFAULT_TAXONOMY, workers=1, use_cache=False,
                             log=lambda *_: None)
        self.assertEqual(result["questoes"][0]["materia"], "Língua Portuguesa")
        self.assertEqual(result["classificacao"]["erros"], {})

    def test_answer_key_pdf_imports_answers_to_sqlite(self):
        source = self.root / "prova.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "1 A 2 B")
        doc.save(source)
        doc.close()
        self.assertEqual(parse_answer_key_pdf(source), {1: "A", 2: "B"})

        questions = self.root / "questoes.json"
        questions.write_text(json.dumps({"prova": "prova", "questoes": [
            {"numero": n, "alternativas": {"A": {}, "B": {}}} for n in (1, 2)
        ]}), encoding="utf-8")
        db = connect_db(self.root / "diagnostico.sqlite3")
        try:
            summary = import_answer_key(db, source, questions)
            stored = db.execute("SELECT question_number,answer FROM answer_keys ORDER BY question_number").fetchall()
        finally:
            db.close()
        self.assertEqual(summary["imported"], 2)
        self.assertEqual([tuple(row) for row in stored], [(1, "A"), (2, "B")])


if __name__ == "__main__":
    unittest.main()
