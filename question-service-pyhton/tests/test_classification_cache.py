import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studai.features.classification.cache import ClassificationCache
from studai.features.classification.service import _cache_key
from studai.features.classification import service as classification
from studai.tools.prepare_edubench import candidate_rows


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


if __name__ == "__main__":
    unittest.main()
