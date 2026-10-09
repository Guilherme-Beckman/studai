import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studai.api import server
from studai.features.classification.service import DEFAULT_TOPIC_TAXONOMY
from studai.features.questions import review


class QuestionReviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.exam_dir = self.root / "exam"
        self.exam_dir.mkdir()
        self.exam_json = self.exam_dir / "questoes.classificado.json"
        self.subject = "Biologia"
        self.topic = DEFAULT_TOPIC_TAXONOMY[self.subject][0]
        self.data = {"prova": "fuvest-2024-v", "textos_compartilhados": {"1-2": {"texto": "texto base"}},
                     "questoes": [
                         {"numero": 1, "texto_id": "1-2", "enunciado": "ecologia", "revisar": ["low_confidence"],
                          "classificacao": {"materia": self.subject, "assunto": self.topic, "confianca": 0.4},
                          "alternativas": {"A": {"texto": "mutualismo"}}},
                         {"numero": 2, "enunciado": "genética", "revisar": [],
                          "classificacao": {"materia": self.subject, "assunto": self.topic, "confianca": 0.95},
                          "alternativas": {"A": {"texto": "dominante"}}},
                     ]}
        self.exam_json.write_text(json.dumps(self.data), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_review_progress_persists_and_unusable_counts_as_resolved(self):
        result = review.save_review(self.exam_json, 1, {"status": "reviewed", "subject": self.subject,
                                   "topic": self.topic, "note": "conferida"})
        self.assertEqual(result["reviewRequired"], 1)
        self.assertEqual(result["reviewedQuestions"], 1)
        self.assertEqual(result["reviewProgress"], 100)
        stored = review.read_reviews(self.exam_json)["questions"]["1"]
        self.assertEqual(stored["note"], "conferida")

        review.save_review(self.exam_json, 1, {"status": "unusable", "note": "extração incompleta"})
        examples = review.reviewed_examples(self.root)
        self.assertEqual(examples, [])
        stats = review.review_stats(self.data, review.read_reviews(self.exam_json))
        self.assertEqual(stats["reviewProgress"], 100)

    def test_only_human_confirmed_usable_questions_enter_rag_dataset(self):
        review.save_review(self.exam_json, 1, {"status": "reviewed", "subject": self.subject,
                             "topic": self.topic, "note": "confirmada"})
        rows = review.reviewed_examples(self.root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "fuvest-2024-v:q1")
        self.assertIn("texto base", rows[0]["supporting_texts"])
        self.assertNotIn("answer", rows[0])
        target = self.root / "reviewed.jsonl"
        self.assertEqual(review.write_reviewed_dataset(self.root, target), 1)
        self.assertEqual(len(target.read_text(encoding="utf-8").splitlines()), 1)

    def test_human_decisions_carry_to_a_new_extraction_directory(self):
        review.save_review(self.exam_json, 1, {"status": "reviewed", "subject": self.subject,
                             "topic": self.topic, "note": "preservar"})
        new_exam_json = self.root / "reprocessed" / "questoes.json"
        new_exam_json.parent.mkdir()
        new_exam_json.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertTrue(review.preserve_reviews(self.root, "fuvest-2024-v", new_exam_json))
        self.assertEqual(review.read_reviews(new_exam_json)["questions"]["1"]["note"], "preservar")


class QuestionReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.processed = self.root / "processados"
        exam_dir = self.processed / "web" / "exam"
        exam_dir.mkdir(parents=True)
        subject = "Biologia"
        topic = DEFAULT_TOPIC_TAXONOMY[subject][0]
        data = {"prova": "exam-id", "arquivo_origem": "fuvest2024_prova_V.pdf",
                "questoes": [{"numero": 1, "enunciado": "texto", "alternativas": {"A": {"texto": "a"}},
                              "revisar": ["classificacao_baixa_confianca"],
                              "classificacao": {"materia": subject, "assunto": topic, "confianca": 0.4}}]}
        (exam_dir / "questoes.classificado.json").write_text(json.dumps(data), encoding="utf-8")
        self.patches = [
            patch.object(server, "PROCESSED_ROOT", self.processed),
            patch.object(server, "DEFAULT_DIAGNOSTICS_DB", self.root / "history.sqlite3"),
            patch.object(server, "_queue_review_index", return_value="job123"),
            patch.object(server, "_read_rag_status", return_value={"status": "empty"}),
        ]
        for active in self.patches:
            active.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        for active in reversed(self.patches):
            active.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def test_patch_validates_and_persists_labels_and_updates_exam_progress(self):
        detail = self.request("/api/exams/exam-id/questions")
        self.assertTrue(detail["questions"][0]["needsReview"])
        self.assertEqual(detail["reviewProgress"]["reviewProgress"], 0)
        saved = self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload={
            "status": "reviewed", "subject": "Biologia", "topic": DEFAULT_TOPIC_TAXONOMY["Biologia"][0],
            "note": "revisada"})
        self.assertEqual(saved["reviewProgress"], 100)
        self.assertEqual(saved["ragJobId"], "job123")
        refreshed = self.request("/api/exams/exam-id/questions")
        self.assertEqual(refreshed["questions"][0]["review"]["note"], "revisada")
        listed = self.request("/api/exams")
        self.assertEqual(listed[0]["reviewProgress"], 100)

    def test_patch_rejects_topic_outside_selected_subject(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload={
                "status": "reviewed", "subject": "Biologia", "topic": "not a topic"})
        self.assertEqual(error.exception.code, 400)

    def test_embedding_failure_keeps_review_and_marks_index_pending(self):
        exam_json = self.processed / "web" / "exam" / "questoes.classificado.json"
        saved = server.question_review.save_review(exam_json, 1, {
            "status": "reviewed", "subject": "Biologia",
            "topic": DEFAULT_TOPIC_TAXONOMY["Biologia"][0], "note": "persistir mesmo offline"})
        dataset = self.root / "cache" / "fuvest-reviewed.jsonl"
        cache_path = self.root / "cache" / "vectors.sqlite3"
        status_path = self.root / "cache" / "status.json"
        with patch.object(server, "PROCESSED_ROOT", self.processed), \
             patch.object(server, "DEFAULT_EXAMPLES", dataset), \
             patch.object(server, "DEFAULT_CLASSIFICATION_CACHE", cache_path), \
             patch.object(server, "RAG_STATUS_FILE", status_path), \
             patch("studai.features.classification.rag.ollama_embed", side_effect=RuntimeError("Ollama offline")):
            status = server._run_review_index("http://offline")
        self.assertEqual(saved["reviewProgress"], 100)
        self.assertEqual(status["status"], "pending")
        self.assertEqual(server.question_review.read_reviews(exam_json)["questions"]["1"]["status"], "reviewed")
        self.assertEqual(len(dataset.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
