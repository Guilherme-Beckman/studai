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
from studai.features.classification.taxonomy import (
    CONTENT_TAXONOMY_VERSION, DEFAULT_CONTENTS_BY_SUBJECT,
)
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
        self.content = DEFAULT_CONTENTS_BY_SUBJECT[self.subject][0]
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
                                   "topic": self.topic, "content_id": self.content["id"],
                                   "taxonomy_version": CONTENT_TAXONOMY_VERSION, "note": "conferida"})
        self.assertEqual(result["reviewRequired"], 1)
        self.assertEqual(result["reviewedQuestions"], 1)
        # O progresso conta todas as questões: a 2 ainda não foi decidida.
        self.assertEqual(result["reviewProgress"], 50)
        self.assertEqual((result["approved"], result["pending"], result["total"]), (1, 1, 2))
        stored = review.read_reviews(self.exam_json)["questions"]["1"]
        self.assertEqual(stored["note"], "conferida")

        review.save_review(self.exam_json, 1, {"status": "unusable", "note": "extração incompleta"})
        examples = review.reviewed_examples(self.root)
        self.assertEqual(examples, [])
        review.save_review(self.exam_json, 2, {"status": "bad_crop"})
        stats = review.review_stats(self.data, review.read_reviews(self.exam_json))
        self.assertEqual(stats["reviewProgress"], 100)
        self.assertEqual((stats["unusable"], stats["badCrop"], stats["pending"]), (1, 1, 0))

    def approve(self, number, **extra):
        return review.save_review(self.exam_json, number, {
            "status": "reviewed", "subject": self.subject, "topic": self.topic,
            "content_id": self.content["id"], "taxonomy_version": CONTENT_TAXONOMY_VERSION, **extra})

    def test_edits_are_a_layer_over_the_extraction_and_feed_the_rag(self):
        (self.exam_dir / "img").mkdir()
        (self.exam_dir / "img" / "fig.png").write_bytes(b"png")
        question = self.data["questoes"][0]
        edits = review.validate_edits(question, {
            "enunciado": "ecologia corrigida", "figuras_enunciado": ["img/fig.png"],
            "alternativas": {"A": {"texto": "mutualismo"}, "B": {"texto": "comensalismo"}}}, self.exam_dir)
        self.approve(1, edits=edits)
        stored = review.read_reviews(self.exam_json)["questions"]["1"]
        self.assertIn("edited_at", stored)
        # Sem "edits" no pedido, a correção anterior continua; a extração não muda.
        self.approve(1, note="só a nota")
        stored = review.read_reviews(self.exam_json)["questions"]["1"]
        self.assertEqual(stored["edits"]["enunciado"], "ecologia corrigida")
        self.assertEqual(json.loads(self.exam_json.read_text(encoding="utf-8"))["questoes"][0]["enunciado"], "ecologia")
        shown = review.apply_edits(question, stored)
        self.assertEqual(shown["figuras_enunciado"], ["img/fig.png"])
        self.assertEqual(sorted(shown["alternativas"]), ["A", "B"])
        self.assertEqual(review.reviewed_examples(self.root)[0]["question"], "ecologia corrigida")
        self.approve(1, edits=None)
        self.assertNotIn("edits", review.read_reviews(self.exam_json)["questions"]["1"])

    def test_invalid_edits_are_rejected(self):
        question = self.data["questoes"][0]
        for edits in ({"alternativas": {"F": {"texto": "x"}}}, {"figuras_enunciado": ["../fora.png"]},
                      {"figuras_enunciado": ["img/inexistente.png"]}, {"gabarito": "A"},
                      {"enunciado": "x" * (review.MAX_STEM_CHARS + 1)}):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                review.validate_edits(question, edits, self.exam_dir)

    def test_saved_image_must_be_png_jpeg_or_webp(self):
        path = review.save_image(self.exam_dir, b"\x89PNG\r\n\x1a\n" + b"0" * 10)
        self.assertTrue(path.startswith("img/edit_") and path.endswith(".png"))
        self.assertTrue((self.exam_dir / path).is_file())
        with self.assertRaises(ValueError):
            review.save_image(self.exam_dir, b"<svg></svg>")

    def test_only_approved_questions_enter_rag_and_bad_crops_form_a_queue(self):
        self.approve(1)
        review.save_review(self.exam_json, 2, {"status": "bad_crop", "note": "figura cortada"})
        self.assertEqual([row["id"] for row in review.reviewed_examples(self.root)], ["fuvest-2024-v:q1"])
        review.save_review(self.exam_json, 1, {"status": "pending", "subject": self.subject})
        self.assertEqual(review.reviewed_examples(self.root), [])
        queue = review.bad_crop_queue(self.root)
        self.assertEqual([(row["exam"], row["numero"], row["note"]) for row in queue],
                         [("fuvest-2024-v", 2, "figura cortada")])

    def test_bulk_save_writes_every_decision_at_once(self):
        result = review.save_reviews_bulk(self.exam_json, {
            1: {"status": "unusable"}, 2: {"status": "bad_crop"}})
        self.assertEqual(sorted(result["questions"]), ["1", "2"])
        self.assertEqual(result["reviewProgress"], 100)
        with self.assertRaises(FileNotFoundError):
            review.save_reviews_bulk(self.exam_json, {9: {"status": "unusable"}})

    def test_only_human_confirmed_usable_questions_enter_rag_dataset(self):
        review.save_review(self.exam_json, 1, {"status": "reviewed", "subject": self.subject,
                             "topic": self.topic, "content_id": self.content["id"],
                             "taxonomy_version": CONTENT_TAXONOMY_VERSION, "note": "confirmada"})
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
                             "topic": self.topic, "content_id": self.content["id"],
                             "taxonomy_version": CONTENT_TAXONOMY_VERSION, "note": "preservar"})
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
        content = DEFAULT_CONTENTS_BY_SUBJECT[subject][0]
        data = {"prova": "exam-id", "arquivo_origem": "fuvest2024_prova_V.pdf",
                "questoes": [{"numero": 1, "enunciado": "texto", "alternativas": {"A": {"texto": "a"}},
                              "revisar": ["classificacao_baixa_confianca"],
                              "classificacao": {"materia": subject, "assunto": topic, "confianca": 0.4}},
                             {"numero": 2, "enunciado": "sem sinalização", "alternativas": {"A": {"texto": "a"}},
                              "revisar": [], "classificacao": {"materia": subject, "assunto": content["name"],
                                                               "conteudo_principal_id": content["id"],
                                                               "confianca": 0.95}}]}
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

    def raw(self, path, body, content_type):
        request = urllib.request.Request(self.base + path, data=body, method="POST",
                                         headers={"Content-Type": content_type})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def test_unflagged_question_can_be_approved_marked_bad_crop_and_reset(self):
        saved = self.request("/api/exams/exam-id/questions/2/review", method="PATCH", payload={"status": "bad_crop"})
        self.assertEqual(saved["status"], "bad_crop")
        self.assertEqual(saved["badCrop"], 1)
        self.assertEqual(self.request("/api/review/bad-crops")[0]["numero"], 2)
        reset = self.request("/api/exams/exam-id/questions/2/review", method="PATCH", payload={"status": "pending"})
        self.assertNotIn("status", reset)
        self.assertEqual(self.request("/api/review/bad-crops"), [])

    def test_edits_answer_and_uploaded_image_show_up_in_the_review_queue(self):
        uploaded = self.raw("/api/exams/exam-id/questions/1/images", b"\x89PNG\r\n\x1a\n0000", "image/png")
        content_id = DEFAULT_CONTENTS_BY_SUBJECT["Biologia"][0]["id"]
        self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload={
            "status": "reviewed", "subject": "Biologia", "content_id": content_id, "answer": "C",
            "edits": {"enunciado": "texto corrigido", "figuras_enunciado": [uploaded["path"]],
                      "alternativas": {letter: {"texto": letter.lower()} for letter in "ABCDE"}}})
        detail = self.request("/api/exams/exam-id/questions")
        question = detail["questions"][0]
        self.assertEqual(question["enunciado"], "texto corrigido")
        self.assertEqual(question["figuras_enunciado"], [uploaded["path"]])
        self.assertEqual(sorted(question["alternativas"]), list("ABCDE"))
        self.assertEqual(detail["answerKeys"]["1"], {"answer": "C", "annulled": False})
        self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload={
            "status": "reviewed", "subject": "Biologia", "content_id": content_id, "answer": "*"})
        self.assertTrue(self.request("/api/exams/exam-id/questions")["answerKeys"]["1"]["annulled"])

    def test_invalid_edits_answer_and_image_are_rejected(self):
        for payload in ({"status": "pending", "edits": {"alternativas": {"Z": "x"}}},
                        {"status": "pending", "answer": "F"}, {"status": "aprovada"}):
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload=payload)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.raw("/api/exams/exam-id/questions/1/images", b"GIF89a", "image/gif")
        self.assertEqual(error.exception.code, 400)

    def test_bulk_approval_uses_ai_content_and_reports_skipped_questions(self):
        with patch.object(server, "_queue_review_index", return_value="job-bulk") as queued:
            result = self.request("/api/exams/exam-id/reviews/bulk", method="POST", payload={"numbers": [1, 2, 7]})
        self.assertEqual(result["approved"], [2])
        self.assertEqual({item["numero"] for item in result["skipped"]}, {1, 7})
        self.assertEqual(result["ragJobId"], "job-bulk")
        queued.assert_called_once()
        reviews = self.request("/api/exams/exam-id/questions")["questions"]
        self.assertEqual(reviews[1]["review"]["status"], "reviewed")
        self.assertEqual(reviews[1]["review"]["content_id"], DEFAULT_CONTENTS_BY_SUBJECT["Biologia"][0]["id"])
        self.assertIsNone(reviews[0]["review"])

    def test_patch_validates_and_persists_labels_and_updates_exam_progress(self):
        detail = self.request("/api/exams/exam-id/questions")
        self.assertTrue(detail["questions"][0]["needsReview"])
        self.assertEqual(detail["reviewProgress"]["reviewProgress"], 0)
        saved = self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload={
            "status": "reviewed", "subject": "Biologia", "content_id": DEFAULT_CONTENTS_BY_SUBJECT["Biologia"][0]["id"],
            "note": "revisada"})
        self.assertEqual(saved["reviewProgress"], 50)
        self.assertEqual(saved["ragJobId"], "job123")
        refreshed = self.request("/api/exams/exam-id/questions")
        self.assertEqual(refreshed["questions"][0]["review"]["note"], "revisada")
        self.assertEqual(refreshed["questions"][0]["review"]["content_id"],
                         DEFAULT_CONTENTS_BY_SUBJECT["Biologia"][0]["id"])
        listed = self.request("/api/exams")
        self.assertEqual(listed[0]["reviewProgress"], 50)

    def test_patch_rejects_topic_outside_selected_subject(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/exams/exam-id/questions/1/review", method="PATCH", payload={
                "status": "reviewed", "subject": "Biologia", "topic": "not a topic"})
        self.assertEqual(error.exception.code, 400)

    def test_embedding_failure_keeps_review_and_marks_index_pending(self):
        exam_json = self.processed / "web" / "exam" / "questoes.classificado.json"
        saved = server.question_review.save_review(exam_json, 1, {
            "status": "reviewed", "subject": "Biologia",
            "topic": DEFAULT_TOPIC_TAXONOMY["Biologia"][0], "content_id": DEFAULT_CONTENTS_BY_SUBJECT["Biologia"][0]["id"],
            "taxonomy_version": CONTENT_TAXONOMY_VERSION, "note": "persistir mesmo offline"})
        dataset = self.root / "cache" / "fuvest-reviewed.jsonl"
        cache_path = self.root / "cache" / "vectors.sqlite3"
        status_path = self.root / "cache" / "status.json"
        with patch.object(server, "PROCESSED_ROOT", self.processed), \
             patch.object(server, "DEFAULT_EXAMPLES", dataset), \
             patch.object(server, "DEFAULT_CLASSIFICATION_CACHE", cache_path), \
             patch.object(server, "RAG_STATUS_FILE", status_path), \
             patch("studai.features.classification.rag.ollama_embed", side_effect=RuntimeError("Ollama offline")):
            status = server._run_review_index("http://offline")
        self.assertEqual(saved["reviewProgress"], 50)
        self.assertEqual(status["status"], "pending")
        self.assertEqual(server.question_review.read_reviews(exam_json)["questions"]["1"]["status"], "reviewed")
        self.assertEqual(len(dataset.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
