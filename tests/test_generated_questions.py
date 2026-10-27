import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _reports_support import build
from studai.api import server
from studai.features.diagnostics import service as diagnostics
from studai.features.explanations import service as explanations
from studai.features.questions import generated
from studai.integrations.llm import DemoProvider
from test_study_scheduler import leaves


def question(**changes):
    obj = {"enunciado": "Quanto vale o seno de 30 graus no ciclo trigonométrico?",
           "alternativas": {"A": "0", "B": "1/2", "C": "√2/2", "D": "√3/2", "E": "1"}, "answer": "B",
           "key_concept": "Seno de ângulos notáveis", "steps": ["No triângulo 30-60-90, o seno de 30° é 1/2."],
           "distractors": {"A": "seno de 0°", "C": "seno de 45°", "D": "seno de 60°", "E": "seno de 90°"},
           "hints": ["Pense no triângulo equilátero.", "Divida-o ao meio.", "Compare o cateto oposto e a hipotenusa."],
           "difficulty": 1}
    obj.update(changes)
    return obj


class ScriptedProvider:
    def __init__(self, *answers):
        self.answers = list(answers)

    def complete(self, system, user, images=None):
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


class GeneratedQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "processados"
        self.root.mkdir()
        self.db = diagnostics.connect_db(Path(self.temp.name) / "h.sqlite3")
        self.leaf = leaves("Matemática", 1)[0]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def pool(self):
        return [item for item in diagnostics.load_question_pool(self.root, self.db, ["Matemática"])[0]
                if item["source_id"] == generated.SOURCE_ID]

    def test_validation(self):
        self.assertEqual(generated.validate_question(question(), 1)["answer"], "B")
        broken = [question(alternativas={"A": "0", "B": "1/2", "C": "1", "D": "2"}),
                  question(alternativas={"A": "0", "B": "1/2", "C": "1/2", "D": "2", "E": "3"}),
                  question(answer="F"), question(enunciado="curta"),
                  question(hints=["A resposta é B.", "Duas.", "Três."]), question(distractors={"A": "x"})]
        for obj in broken:
            with self.assertRaises(ValueError, msg=obj):
                generated.validate_question(obj, 1)

    def test_generate_enters_the_pool_with_key_resolution_and_level(self):
        numbers = generated.generate_questions(self.root, self.db, self.leaf, 1, 3, DemoProvider(model="demo"))
        self.assertEqual(numbers, [1, 2, 3])
        pool = self.pool()
        self.assertEqual(len(pool), 3)
        self.assertEqual({(item["origin"], item["difficulty"], item["question"]["classificacao"]["conteudo_principal_id"])
                          for item in pool}, {("ia", 1, self.leaf)})
        self.assertEqual(pool[0]["correct_answer"], "C")
        record = explanations.get_explanation(self.db, generated.SOURCE_ID, 1)
        self.assertEqual((record["status"], record["content"]["difficulty"]), ("generated", 1))
        key = json.loads(generated.bank_file(self.root).with_name("gabarito.json").read_text(encoding="utf-8"))
        self.assertEqual(key["gabarito"], {"1": "C", "2": "C", "3": "C"})
        # A próxima geração continua a numeração e manda os enunciados existentes no prompt.
        self.assertEqual(generated.generate_questions(self.root, self.db, self.leaf, 2, 1, DemoProvider(model="demo")),
                         [4])

    def test_repeated_questions_are_skipped_and_partial_answers_kept(self):
        provider = ScriptedProvider({"questions": [question(), question(answer="Z"),
                                                   question(enunciado="Quanto vale o cosseno de 60 graus no ciclo?")]})
        self.assertEqual(generated.generate_questions(self.root, self.db, self.leaf, 1, 3, provider), [1, 2])
        again = ScriptedProvider({"questions": [question()]})
        self.assertEqual(generated.generate_questions(self.root, self.db, self.leaf, 1, 1, again), [])

    def test_invalid_answers_raise(self):
        with self.assertRaises(generated.GenerationError):
            generated.generate_questions(self.root, self.db, self.leaf, 1, 1,
                                         ScriptedProvider({"questions": []}, {"questions": [question(answer="Z")]}))
        for args in ((self.leaf, 4, 1), (self.leaf, 1, 9), ("matematica", 1, 1)):
            with self.assertRaises(ValueError):
                generated.generate_questions(self.root, self.db, *args, DemoProvider(model="demo"))

    def test_report_takes_the_question_out(self):
        generated.generate_questions(self.root, self.db, self.leaf, 1, 2, DemoProvider(model="demo"))
        self.assertEqual(generated.report_question(self.root, 1, "alternativa C também serve")["status"], "unusable")
        self.assertEqual([item["number"] for item in self.pool()], [2])


class GeneratedApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        processed, config_path, db_path = build(Path(self.temp.name))
        self.processed = processed
        self.patches = [
            patch.object(server, "PROCESSED_ROOT", processed),
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
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_generate_route_and_report(self):
        leaf = leaves("Matemática", 1)[0]
        status, body = self.request(f"/api/contents/{leaf}/generate", "POST", {"level": 1, "count": 2})
        self.assertEqual(status, 202)
        for _ in range(100):
            job = self.request(f"/api/jobs/{body['jobId']}")[1]
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        self.assertEqual((job["status"], job["result"]["numbers"]), ("completed", [1, 2]))
        exams = self.request("/api/exams")[1]
        bank = next(exam for exam in exams if exam["id"] == generated.SOURCE_ID)
        self.assertEqual(bank["origin"]["tipo"], "ia")
        self.assertEqual(self.request(f"/api/questions/{generated.SOURCE_ID}/1/report", "POST", {"note": "x"})[1],
                         {"status": "unusable"})
        self.assertEqual(self.request("/api/questions/fuvest2024/1/report", "POST", {})[0], 400)
        self.assertEqual(self.request(f"/api/contents/{leaf}/generate", "POST", {"level": 7})[0], 400)


if __name__ == "__main__":
    unittest.main()
