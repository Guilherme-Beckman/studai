import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _reports_support import COURSE_ID, build, load_pool
from studai.api import server
from studai.features.explanations import service as explanations
from studai.features.study import service as study
from studai.integrations.llm import DemoProvider

ALTERNATIVES = {letter: {"texto": letter.lower()} for letter in "ABCDE"}


def valid(correct="C", **changes):
    obj = {"answer": correct, "key_concept": "Proporcionalidade", "steps": ["Monte a regra de três.", "Resolva."],
           "distractors": {letter: f"{letter} troca a razão." for letter in "ABCDE" if letter != correct},
           "hints": ["Olhe as unidades.", "Monte uma proporção.", "Compare as grandezas."], "difficulty": 2}
    obj.update(changes)
    return obj


class ScriptedProvider:
    """Sem ``cacheable``: o cache de IA é ignorado."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, system, user, images=None):
        self.prompts.append(user)
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


class ValidationTests(unittest.TestCase):
    def test_accepts_a_complete_resolution(self):
        result = explanations.validate_explanation(valid(answer="(c)"), ALTERNATIVES, "C")
        self.assertEqual(result["answer"], "C")
        self.assertEqual(sorted(result["distractors"]), ["A", "B", "D", "E"])

    def test_rejects_wrong_answer_missing_distractor_leaky_hint_and_bad_difficulty(self):
        broken = [valid(answer="B"), valid(distractors={"A": "x"}), valid(steps=[]),
                  valid(hints=["Uma.", "Duas."]), valid(hints=["Olhe.", "A resposta é C.", "Fim."]),
                  valid(hints=["Olhe.", "Fica com a alternativa (C).", "Fim."]), valid(difficulty=5),
                  valid(difficulty=True), {"answer": "C"}]
        for obj in broken:
            with self.assertRaises(ValueError, msg=obj):
                explanations.validate_explanation(obj, ALTERNATIVES, "C")

    def test_model_numbering_is_removed(self):
        obj = valid(steps=["1. Monte a regra.", "Passo 2: resolva."], hints=["1) Olhe.", "Dica 2 - Monte.", "3. Compare."])
        result = explanations.validate_explanation(obj, ALTERNATIVES, "C")
        self.assertEqual(result["steps"], ["Monte a regra.", "resolva."])
        self.assertEqual(result["hints"], ["Olhe.", "Monte.", "Compare."])

    def test_lowercase_letter_in_a_hint_is_not_a_leak(self):
        obj = valid("A", hints=["Pense na resposta a partir das unidades.", "Monte a conta.", "Compare."])
        self.assertEqual(explanations.validate_explanation(obj, ALTERNATIVES, "A")["answer"], "A")


class UseCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.pool = load_pool(self.db, self.processed)
        self.entry = self.pool[0]
        self.correct = self.entry["correct_answer"]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_retry_after_invalid_answer_then_store(self):
        provider = ScriptedProvider("sem json", valid(self.correct))
        record = explanations.generate_explanation(self.db, self.entry, provider)
        self.assertEqual((record["status"], record["content"]["answer"]), ("generated", self.correct))
        self.assertIn("inválida", provider.prompts[1])
        self.assertEqual(explanations.get_explanation(self.db, self.entry["source_id"], self.entry["number"]),
                         record)

    def test_two_invalid_answers_raise(self):
        with self.assertRaises(explanations.ExplanationError):
            explanations.generate_explanation(self.db, self.entry, ScriptedProvider("x", "y"))
        self.assertIsNone(explanations.get_explanation(self.db, self.entry["source_id"], self.entry["number"]))

    def test_review_and_regeneration_with_feedback(self):
        source, number = self.entry["source_id"], self.entry["number"]
        explanations.generate_explanation(self.db, self.entry, ScriptedProvider(valid(self.correct)))
        with self.assertRaises(ValueError):
            explanations.review_explanation(self.db, source, number, False)
        rejected = explanations.review_explanation(self.db, source, number, False, "o passo 2 erra o sinal")
        self.assertTrue(explanations.needs_generation(rejected))
        provider = ScriptedProvider(valid(self.correct, key_concept="Sinais"))
        regenerated = explanations.generate_explanation(self.db, self.entry, provider, feedback=rejected["feedback"])
        prompt = json.loads(provider.prompts[0])
        self.assertEqual(prompt["critica_do_aluno"], "o passo 2 erra o sinal")
        self.assertEqual(prompt["resolucao_anterior"]["key_concept"], "Proporcionalidade")
        self.assertEqual((regenerated["status"], regenerated["content"]["key_concept"]), ("generated", "Sinais"))
        approved = explanations.review_explanation(self.db, source, number, True)
        self.assertEqual(approved["status"], "approved")
        self.assertFalse(explanations.needs_generation(approved))

    def test_hints_before_answering_and_everything_after(self):
        record = explanations.generate_explanation(self.db, self.entry, DemoProvider(model="demo"))
        before = explanations.public_payload(record, answered=False, hints=2)
        self.assertEqual(len(before["hints"]), 2)
        self.assertNotIn("answer", before)
        self.assertNotIn("steps", before)
        after = explanations.public_payload(record, answered=True)
        self.assertEqual(after["answer"], self.correct)
        self.assertEqual(len(after["hints"]), 3)

    def test_question_answered_by_context(self):
        source, number = self.entry["source_id"], self.entry["number"]
        self.assertFalse(explanations.question_answered(self.db, source, number))
        config = {"version": 1, "cursos": {COURSE_ID: {"nome": "x", "pesos": {"Matemática": 1}}}}
        now = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
        session = study.plan_today(self.db, [self.entry], config, COURSE_ID, now)
        item = session["items"][0]
        context = {"session_id": session["id"], "item_id": item["id"]}
        self.assertFalse(explanations.question_answered(self.db, source, number, **context))
        study.record_study_answer(self.db, session["id"], item["id"], self.correct, None, now)
        self.assertTrue(explanations.question_answered(self.db, source, number, **context))
        self.assertTrue(explanations.question_answered(self.db, source, number))


class ExplanationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        processed, config_path, db_path = build(Path(self.temp.name))
        self.db_path = db_path
        self.patches = [
            patch.object(server, "DEFAULT_QUESTIONS_DIR", processed),
            patch.object(server, "DEFAULT_DIAGNOSTICS_DB", db_path),
            patch.object(server, "DEFAULT_COURSE_CONFIG", config_path),
            patch.dict(server.POOL_CACHE, clear=True),
            patch.dict(server.EXPLANATION_JOBS, clear=True),
            patch.dict(server.EXPLANATION_PROVIDERS, clear=True),
            patch.dict(os.environ, {"STUDAI_EXPLANATIONS_PROVIDER": "demo", "STUDAI_AI_CACHE": "off"}),
        ]
        for active in self.patches:
            active.start()
        db = study.connect_db(db_path)
        try:
            self.entry = load_pool(db, processed)[0]
        finally:
            db.close()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.http.server_port}"
        self.path = f"/api/questions/{self.entry['source_id']}/{self.entry['number']}/explanation"

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
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def wait(self, job_id):
        for _ in range(100):
            _, job = self.request(f"/api/jobs/{job_id}")
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.05)
        self.fail("tarefa não terminou")

    def test_generate_in_background_then_hints_only_until_answered(self):
        status, body = self.request(self.path)
        self.assertEqual((status, body["status"]), (202, "generating"))
        again_status, again = self.request(self.path)
        if again_status == 202:  # ainda gerando: a mesma tarefa é reaproveitada
            self.assertEqual(again["jobId"], body["jobId"])
        self.assertEqual(self.wait(body["jobId"])["status"], "completed")
        status, hints = self.request(self.path + "?hints=1&sessionId=x&itemId=y")
        self.assertEqual((status, len(hints["hints"])), (200, 1))
        self.assertNotIn("answer", hints)
        status, error = self.request(self.path + "/review", "POST", {"approved": True})
        self.assertEqual(status, 400)  # ainda não respondida

    def test_review_rejection_regenerates(self):
        self.wait(self.request(self.path)[1]["jobId"])
        db = study.connect_db(self.db_path)
        try:
            db.execute("""INSERT INTO study_sessions(id,local_date,course_id,plan_json,created_at)
                VALUES('s','2026-10-22',?,'{"items": []}','2026-10-22')""", (COURSE_ID,))
            db.execute("""INSERT INTO study_answers(session_id,item_id,source_id,question_number,subject,content_id,
                selected_answer,correct_answer,is_correct,answered_at) VALUES('s','i1',?,?,?,?,'A',?,0,'2026-10-22')""",
                       (self.entry["source_id"], self.entry["number"], self.entry["subject"], "x",
                        self.entry["correct_answer"]))
            db.commit()
        finally:
            db.close()
        status, full = self.request(self.path + "?sessionId=s&itemId=i1")
        self.assertEqual((status, full["answer"]), (200, self.entry["correct_answer"]))
        status, body = self.request(self.path + "/review", "POST", {"approved": False, "feedback": "passo errado",
                                                                    "courseId": COURSE_ID})
        self.assertEqual((status, body["status"]), (200, "generating"))
        self.assertEqual(self.wait(body["jobId"])["status"], "completed")
        self.assertEqual(self.request(self.path + "?sessionId=s&itemId=i1")[1]["status"], "generated")
        self.assertEqual(self.request(self.path + "/review", "POST", {"approved": True})[1]["status"], "approved")

    def test_unknown_question_fails_the_job(self):
        status, body = self.request("/api/questions/nao-existe/1/explanation")
        self.assertEqual(status, 202)
        self.assertEqual(self.wait(body["jobId"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
