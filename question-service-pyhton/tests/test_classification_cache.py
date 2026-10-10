import json
import csv
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studai.features.classification.cache import ClassificationCache
from studai.features.classification.service import _cache_key
from studai.features.classification import service as classification
from studai.tools.prepare_edubench import candidate_rows
from studai.tools.review_edubench import export_review, import_review, sample_by_year
from studai.tools.evaluate_classifier import evaluate, load_jsonl


class ClassificationCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_jsonl_migration_is_repeatable_and_preserves_source(self):
        source = self.root / "old.jsonl"
        source.write_text(json.dumps({"key": "k", "resultado": {"materia": "Física"}, "modelo": "m"}) + "\n")
        cache = ClassificationCache(self.root / "cache.sqlite3")
        self.assertEqual(cache.import_jsonl(source), 1)
        self.assertEqual(cache.import_jsonl(source), 0)
        self.assertEqual(cache.get("k"), ({"materia": "Física"}, "m"))
        self.assertTrue(source.exists())
        cache.close()

    def test_identity_ignores_number_order_case_accents_and_whitespace(self):
        first = {"numero": 1, "enunciado": "  Ação   sobre   Átomos ", "alternativas": {}, "revisar": []}
        second = {"numero": 90, "enunciado": "acao sobre atomos", "alternativas": {}, "revisar": []}
        key1 = _cache_key(first, None, [], "provider:model", "tax", "nunca")
        key2 = _cache_key(second, None, [], "provider:model", "tax", "nunca")
        self.assertEqual(key1, key2)
        self.assertNotEqual(key1, _cache_key(first, None, [], "provider:other", "tax", "nunca"))
        self.assertNotEqual(key1, _cache_key(first, None, [], "provider:model", "other-tax", "nunca"))
        self.assertNotEqual(key1, _cache_key(first, None, [], "provider:model", "tax", "sempre"))

    def test_vector_repository_returns_nearest_reviewed_example(self):
        cache = ClassificationCache(self.root / "cache.sqlite3")
        cache.put_embedding("one", "vetor um", [1, 0], {"materia": "Física"})
        cache.put_embedding("two", "vetor dois", [0, 1], {"materia": "Química"})
        nearest = cache.nearest([0.95, 0.05], 1)
        self.assertEqual(nearest[0]["identity"], "one")
        self.assertEqual(nearest[0]["source"], "human")
        cache.close()

    def test_completed_classification_is_reused_after_reordering(self):
        questions = self.root / "questions.json"
        original = {"questoes": [{"numero": 1, "enunciado": "Água e calor", "alternativas": {}, "revisar": []}]}
        questions.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        with patch.object(classification, "DEFAULT_CLASSIFICATION_CACHE", self.root / "cache.sqlite3"), \
             patch.object(classification, "DEFAULT_QUESTIONS_DIR", self.root / "empty"), \
             patch.object(classification, "_CACHE_WARMED", True):
            first = classification.classificar(questions, classification.MockProvider(),
                                                classification.DEFAULT_TAXONOMY, workers=1,
                                                log=lambda *_: None)
            original["questoes"][0]["numero"] = 8
            original["questoes"][0]["enunciado"] = " agua   e CALOR "
            questions.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            second = classification.classificar(questions, classification.MockProvider(),
                                                 classification.DEFAULT_TAXONOMY, workers=1,
                                                 log=lambda *_: None)
        self.assertEqual(first["processamento_cache"]["questoes_reutilizadas"], 0)
        self.assertEqual(second["processamento_cache"]["questoes_reutilizadas"], 1)
        self.assertEqual(second["questoes"][0]["classificacao"]["origem"], "cache")

    def test_edubench_candidates_keep_source_label_as_suggestion(self):
        rows = list(candidate_rows([
            {"question_key": "usp-1", "exam": "USP", "subject": "Ciências Exatas",
             "question": "questão", "short_answer": "resposta"},
            {"question_key": "unesp-1", "exam": "UNESP", "subject": "Humanas"},
        ]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["suggested_subject"], "Ciências Exatas")
        self.assertEqual(rows[0]["subject"], "")
        self.assertEqual(rows[0]["topic"], "")
        self.assertEqual(rows[0]["review_status"], "pending")

    def test_topic_taxonomy_restricts_topic_to_selected_subject(self):
        taxonomy = {"Física": "física", "Química": "química"}
        topics = {"Física": ["Indução eletromagnética"], "Química": ["Estequiometria"]}
        result = classification.validate({"materia": "Física", "assunto": "Indução eletromagnética",
            "materias_secundarias": [], "confianca": 0.9}, taxonomy, topics)
        self.assertEqual(result["assunto"], "Indução eletromagnética")
        with self.assertRaisesRegex(ValueError, "tópico fora da taxonomia"):
            classification.validate({"materia": "Física", "assunto": "Estequiometria",
                "materias_secundarias": [], "confianca": 0.9}, taxonomy, topics)
        review = classification.validate({"materia": "Física", "assunto": "REVISAR",
            "materias_secundarias": [], "confianca": 0.9}, taxonomy, topics)
        self.assertEqual(review["confianca"], 0.25)

    def test_edubench_review_roundtrip_samples_years_and_requires_labels(self):
        candidates = self.root / "candidates.jsonl"
        candidates.write_text("\n".join(json.dumps({"id": f"{year}-{n}", "year": year,
            "year_version": "fase1", "question": f"questão {year}-{n}", "supporting_texts": "texto",
            "suggested_subject": "Ciências Exatas", "subject": "", "topic": ""})
            for year in (2022, 2023, 2024) for n in range(4)), encoding="utf-8")
        csv_path = self.root / "review.csv"
        self.assertEqual(export_review(candidates, csv_path, limit=6), 6)
        reviewed = csv_path.read_text(encoding="utf-8-sig")
        self.assertIn("suggested_subject", reviewed)
        with self.assertRaisesRegex(ValueError, "preencha subject e topic"):
            import_review(csv_path, self.root / "reviewed.jsonl")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows_csv = list(csv.DictReader(file))
            fields = list(rows_csv[0])
        for row in rows_csv:
            row["subject"] = "Matemática"
            row["topic"] = "Probabilidade"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows_csv)
        output = self.root / "reviewed.jsonl"
        self.assertEqual(import_review(csv_path, output), 6)
        imported = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(imported["subject"], "Matemática")
        self.assertEqual(imported["topic"], "Probabilidade")
        self.assertTrue(imported["reviewed"])
        self.assertEqual(imported["exam"], "USP")
        rows = [json.loads(line) for line in candidates.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(sample_by_year(rows, 6)), 6)

    def test_benchmark_limit_prints_question_progress_and_records_http_attempts(self):
        dataset = self.root / "reviewed.jsonl"
        row = {"id": "usp-1", "question": "Quanto é um mais um?", "subject": "Matemática",
               "topic": "Probabilidade", "supporting_texts": ""}
        dataset.write_text("\n".join(json.dumps({**row, "id": f"usp-{n}"}) for n in range(3)), encoding="utf-8")

        class Provider:
            label = "test:mock"
            def complete(self, system, user, images=None):
                return json.dumps({"materia": "Matemática", "materias_secundarias": [],
                                   "assunto": "Probabilidade", "confianca": 1})

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report = evaluate(dataset, Provider(), output=self.root / "report.json", source="test",
                              version="1", limit=1, request_attempts=1)
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["http_attempts_per_request"], 1)
        self.assertIn("[1/1] usp-0", output.getvalue())
        self.assertGreaterEqual(report["latency_seconds"]["p95"], 0)
        self.assertGreater(report["latency_seconds"]["questions_per_minute"], 0)

    def test_benchmark_can_measure_unlabeled_questions_and_score_labeled_subset(self):
        dataset = self.root / "mixed.jsonl"
        rows = [
            {"id": "gold-1", "question": "questão rotulada", "subject": "Matemática",
             "topic": "Probabilidade"},
            {"id": "raw-2", "question": "questão sem rótulo"},
        ]
        dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_jsonl(dataset)
        self.assertEqual(len(load_jsonl(dataset, require_labels=False)), 2)

        class Provider:
            label = "test:mock"
            def complete(self, system, user, images=None):
                topic = "REVISAR" if "sem rótulo" in user else "Probabilidade"
                return json.dumps({"materia": "Matemática", "materias_secundarias": [],
                                   "assunto": topic, "confianca": 1})
            def complete_json(self, system, user, schema, images=None):
                return self.complete(system, user, images)

        result = evaluate(dataset, Provider(), output=self.root / "mixed-report.json",
                          source="test", version="1", allow_unlabeled=True)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["labeled_count"], 1)
        self.assertEqual(result["subject"]["accuracy"], 1)
        self.assertEqual(result["topic"]["accuracy"], 1)
        self.assertEqual(result["review_count"], 1)
        self.assertEqual(result["review_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
