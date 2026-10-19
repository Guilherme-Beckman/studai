import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studai.features.classification.cache import ClassificationCache
from studai.features.classification.rag import LocalExampleRetriever, index_examples, render_examples
from studai.features.classification.service import DEFAULT_TOPIC_TAXONOMY, _cache_key
from studai.features.classification.taxonomy import (
    CONTENT_TAXONOMY_VERSION, DEFAULT_CONTENTS_BY_SUBJECT,
)
from studai.features.classification import service as classification
from studai.integrations.llm import OllamaProvider
from studai.tools import benchmark_classifier
from studai.tools.review_edubench import processed_exam_rows


class ClassificationRagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = ClassificationCache(self.root / "cache.sqlite3")

    def tearDown(self):
        self.cache.close()
        self.temp.cleanup()

    def test_index_uses_reviewed_labels_and_holdout_retrieval_excludes_same_year(self):
        content = DEFAULT_CONTENTS_BY_SUBJECT["Física"][0]
        topic = content["name"]
        dataset = self.root / "reviewed.jsonl"
        rows = [
            {"id": "fuvest-2023-q1", "exam": "Fuvest", "year": 2023,
             "question": "movimento e velocidade", "subject": "Física", "topic": topic,
             "content_id": content["id"], "taxonomy_version": CONTENT_TAXONOMY_VERSION, "reviewed": True},
            {"id": "fuvest-2024-q1", "exam": "Fuvest", "year": 2024,
             "question": "força e movimento", "subject": "Física", "topic": topic,
             "content_id": content["id"], "taxonomy_version": CONTENT_TAXONOMY_VERSION, "reviewed": True},
            {"id": "pending-q", "year": 2025, "question": "sem rótulo revisado",
             "subject": "Física", "topic": topic, "review_status": "pending"},
        ]
        dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

        def fake_embed(texts, **_):
            return [[1.0, 0.0] if "2023" in text or "velocidade" in text or "nova" in text
                    else [0.0, 1.0] for text in texts]

        with patch("studai.features.classification.rag.ollama_embed", side_effect=fake_embed):
            indexed = index_examples(dataset, self.cache, base_url="http://local", model="embed-test")
            retriever = LocalExampleRetriever(self.cache, base_url="http://local", model="embed-test", limit=2)
            hits = retriever.retrieve("nova questão Fuvest", exclude_year=2023)

        self.assertEqual(indexed["indexed"], 2)
        self.assertEqual(indexed["skipped"], 0)
        self.assertEqual([hit["identity"] for hit in hits], ["fuvest-2024-q1"])
        prior_signature = self.cache.embedding_signature("embed-test")
        dataset.write_text(json.dumps(rows[1], ensure_ascii=False), encoding="utf-8")
        with patch("studai.features.classification.rag.ollama_embed", side_effect=fake_embed):
            index_examples(dataset, self.cache, base_url="http://local", model="embed-test")
        self.assertEqual(self.cache.counts()["embeddings"], 1)
        self.assertNotEqual(self.cache.embedding_signature("embed-test"), prior_signature)

    def test_retrieved_context_contains_labels_without_answer_key(self):
        rendered = render_examples([{"text": "texto da questão", "result": {
            "subject": "Física", "topic": "Mecânica", "reference_answer": "GABARITO_SECRETO_X9"}}])
        self.assertIn("Matéria: Física", rendered)
        self.assertIn("Conteúdo: Mecânica", rendered)
        self.assertNotIn("GABARITO_SECRETO_X9", rendered)

    def test_cache_identity_changes_when_rag_corpus_changes(self):
        question = {"numero": 1, "enunciado": "movimento uniforme", "alternativas": {}, "revisar": []}
        key = lambda version: _cache_key(question, None, [], "provider:model", "taxonomy", "nunca", version)
        self.assertNotEqual(key("corpus-a"), key("corpus-b"))
        self.assertEqual(key("corpus-a"), key("corpus-a"))

    def test_batch_classifier_includes_retrieved_examples_by_default(self):
        content = DEFAULT_CONTENTS_BY_SUBJECT["Física"][0]
        topic = content["name"]
        examples = self.root / "reviewed.jsonl"
        examples.write_text("\n".join(json.dumps(row) for row in [
            {"id": "same-exam-q1", "year": 2024, "exam": "current-exam",
             "question": "movimento e aceleração no mesmo caderno", "subject": "Física", "topic": topic,
             "content_id": content["id"], "taxonomy_version": CONTENT_TAXONOMY_VERSION,
             "reviewed": True},
            {"id": "other-exam-q1", "year": 2023, "exam": "prior-exam",
             "question": "movimento e aceleração em outro caderno", "subject": "Física", "topic": topic,
             "content_id": content["id"], "taxonomy_version": CONTENT_TAXONOMY_VERSION,
             "reviewed": True},
        ]), encoding="utf-8")
        questions = self.root / "questions.json"
        questions.write_text(json.dumps({"prova": "current-exam", "questoes": [{"id": "current-1", "numero": 1,
            "enunciado": "movimento com aceleração", "alternativas": {}, "revisar": []}]}), encoding="utf-8")
        cache_path = self.root / "classification-cache.sqlite3"
        cache = ClassificationCache(cache_path)

        class FakeOllama(OllamaProvider):
            def complete_json(self, system, user, schema, images=None):
                self.last_prompt = user
                return json.dumps({"materia": "Física", "materias_secundarias": [],
                                   "assunto": topic, "confianca": 0.9})

        provider = FakeOllama(model="fake", base_url="http://ollama.test")
        try:
            with patch("studai.features.classification.rag.ollama_embed",
                       side_effect=lambda texts, **_: [[1.0, 0.0] for _ in texts]), \
                 patch.object(classification, "DEFAULT_CLASSIFICATION_CACHE", cache_path), \
                 patch.object(classification, "DEFAULT_EXAMPLES", examples), \
                 patch.object(classification, "DEFAULT_QUESTIONS_DIR", self.root / "empty"), \
                 patch.object(classification, "_CACHE_WARMED", True):
                index_examples(examples, cache, base_url="http://ollama.test", model="embed-test")
                result = classification.classificar(questions, provider, classification.DEFAULT_TAXONOMY,
                                                     workers=1, log=lambda *_: None,
                                                     embedding_model="embed-test")
        finally:
            cache.close()
        classified = result["questoes"][0]["classificacao"]
        self.assertEqual(classified["exemplos_referencia"][0]["id"], "other-exam-q1")
        self.assertNotIn("same-exam-q1", provider.last_prompt)
        self.assertIn("EXEMPLOS DE REFERÊNCIA REVISADOS", provider.last_prompt)

    def test_sweep_marks_rag_eligible_only_when_all_quality_metrics_hold(self):
        dataset = self.root / "reviewed.jsonl"
        dataset.write_text("{}\n", encoding="utf-8")

        def fake_evaluate(_dataset, _provider, *, output, **kwargs):
            uses_rag = "run-1" in str(output)
            accuracy = 0.8 if uses_rag else 0.7
            topic_accuracy = 0.2 if uses_rag else 0.3
            return {"provider": "test", "subject": {"accuracy": accuracy, "macro_f1": 0.5},
                    "topic": {"accuracy": topic_accuracy, "macro_f1": 0.2},
                    "invalid_json_or_classification": 0, "attempts": 1,
                    "latency_seconds": {"questions_per_minute": 1 if uses_rag else 2},
                    "hosts": {}, "rag": kwargs["rag"]}

        with patch.object(benchmark_classifier, "evaluate", side_effect=fake_evaluate):
            result = benchmark_classifier.benchmark(
                dataset, urls="http://localhost:11434", model="test-model", workers=[1],
                threads=[0], rag_modes=[False, True], embedding_model="embed-test",
                limit=1, output=self.root / "sweep.json", timeout=10, max_tokens=10)
        self.assertFalse(result["runs"][1]["preserves_quality"])
        self.assertEqual(result["fastest_quality_preserving"]["rag"], False)

    def test_local_exam_candidates_keep_version_and_answer_key_for_review(self):
        root = self.root / "processados" / "fuvest2026-fase1-prova-V1"
        root.mkdir(parents=True)
        exam_json = root / "questoes.classificado.json"
        exam_json.write_text(json.dumps({
            "prova": "fuvest2026-fase1-prova-V1", "arquivo_origem": "fuvest2026-fase1-prova-V1.pdf",
            "textos_compartilhados": {},
            "questoes": [{"numero": 1, "enunciado": "Questão teste", "alternativas": {"A": {"texto": "A"}},
                          "classificacao": {"materia": "Biologia", "assunto": "Ecologia e ecossistemas"}}],
        }), encoding="utf-8")
        db_path = self.root / "diagnostico.sqlite3"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE answer_keys(source_id TEXT,question_number INTEGER,answer TEXT,is_annulled INTEGER)")
        db.execute("INSERT INTO answer_keys VALUES(?,?,?,?)",
                   ("fuvest2026-fase1-prova-V1", 1, "A", 0))
        db.commit()
        db.close()

        rows = processed_exam_rows(self.root / "processados", db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["version"], "V1")
        self.assertEqual(rows[0]["year"], "2026")
        self.assertEqual(rows[0]["answer_key"], "A")
        self.assertFalse(rows[0]["is_annulled"])
        self.assertEqual(rows[0]["suggested_subject"], "Biologia")
        self.assertEqual(rows[0]["subject"], "")


if __name__ == "__main__":
    unittest.main()
