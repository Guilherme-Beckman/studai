import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.answer_keys.service import exam_variant, import_answer_key, parse_answer_key_pdf
from studai.features.classification.service import DEFAULT_TAXONOMY, MockProvider, classificar
from studai.features.diagnostics.service import connect_db
from studai.features.questions.extractor import extract_pdf
from studai.features.diagnostics import service as diagnostics
from studai.integrations.llm import make_provider
from studai.api import server as api


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

    def test_pdf_extraction_reports_page_and_question_progress(self):
        source = self.root / "prova.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        for index, line in enumerate(("1", "Enunciado da questão", "(A) opção A", "(B) opção B",
                                      "(C) opção C", "(D) opção D", "(E) opção E")):
            page.insert_text((72, 72 + index * 20), line)
        doc.save(source)
        doc.close()

        events = []
        extract_pdf(source, self.root / "saida", dpi=40, full_crops=False, progress=events.append)
        page_events = [event for event in events if event.get("unit") == "páginas"]
        question_events = [event for event in events if event.get("unit") == "questões"]
        self.assertEqual(page_events[-1]["completed"], page_events[-1]["total"])
        self.assertEqual(question_events[-1]["completed"], question_events[-1]["total"])

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

    def test_fuvest_answer_key_reads_multiple_variant_columns_and_annulled_items(self):
        source = self.root / "gabarito.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 60), "PROVA V")
        page.insert_text((250, 60), "PROVA K")
        page.insert_text((30, 80), "V 01-E V 02-*")
        page.insert_text((30, 100), "V 03-A V 04-D")
        page.insert_text((240, 80), "K 01-B K 02-C")
        page.insert_text((240, 100), "K 03-C K 04-E")
        doc.save(source)
        doc.close()
        self.assertEqual(parse_answer_key_pdf(source, "V"), {1: "E", 2: "*", 3: "A", 4: "D"})

    def test_exam_variant_recognizes_fuvest_type_suffix(self):
        self.assertEqual(exam_variant(Path("fuvest_2022_primeira_fase_tipo_V.pdf")), "V")
        self.assertEqual(exam_variant(Path("fuvest2026-fase1-prova-V1.pdf")), "V1")

    def test_api_processes_pdf_offline_without_external_model(self):
        source = self.root / "prova.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        for index, line in enumerate(("1", "Quanto é 1 + 1?", "(A) 1", "(B) 2", "(C) 3", "(D) 4", "(E) 5")):
            page.insert_text((72, 72 + index * 20), line)
        doc.save(source)
        doc.close()
        processed = self.root / "processed"
        with patch.object(api, "UPLOADS_ROOT", self.root / "uploads"), \
             patch.object(api, "PROCESSED_ROOT", processed), \
             patch.object(api, "SERVICE_ROOT", self.root), \
             patch.object(api.classification, "classificar", wraps=api.classification.classificar) as classify:
            result = api._process_exam("prova.pdf", source.read_bytes(), {
                "provider": "demo", "dpi": 72, "workers": "2", "ollamaThreads": "12"})
            exams = api._exam_files()
        self.assertTrue(result["classified"])
        self.assertEqual(result["questions"], 1)
        self.assertTrue(exams[0][0]["classified"])
        self.assertEqual(classify.call_args.kwargs["workers"], 2)
        self.assertEqual(classify.call_args.args[1].num_threads, 12)

    def test_api_provider_accepts_multiple_ollama_servers(self):
        provider = api._provider({"provider": "ollama", "model": "qwen3.5:4b",
                                  "baseUrl": "http://localhost:11434,http://192.168.100.167:11434",
                                  "ollamaThreads": "12"})
        self.assertEqual(provider.base_urls, ["http://localhost:11434", "http://192.168.100.167:11434"])
        self.assertEqual(provider.num_threads, 12)

    def test_demo_provider_supports_diagnostic_planning_and_analysis(self):
        provider = make_provider("demo", "", base_url=None, key_env=None)
        profile = {"nome": "Curso de teste", "pesos": {"Matemática": 1}}
        plan, plan_info = diagnostics.plan_topics(provider, profile, {"Matemática": ["Funções"]})
        self.assertEqual(plan, {"Matemática": ["Funções"]})
        self.assertEqual(plan_info["status"], "llm")

        metrics = {"subjects": {"Matemática": {"attempts": 3, "correct": 1, "accuracy": 1 / 3,
                    "error_reasons": {"unknown_content": 1, "calc_error": 1, "misread": 0, "blank": 0},
                    "status": "assessed"}}}
        analysis, error = diagnostics.analyze(provider, metrics, profile)
        self.assertIsNone(error)
        self.assertEqual(analysis["steps"][0]["subject"], "Matemática")


if __name__ == "__main__":
    unittest.main()
