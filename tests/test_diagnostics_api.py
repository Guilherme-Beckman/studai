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
from studai.features.classification.taxonomy import CONTENT_TAXONOMY_VERSION
from studai.features.diagnostics import service as diagnostics
from studai.features.questions import review

CONFIG = Path(__file__).parent.parent / "diagnostico" / "cursos.json"
LEAVES = {"Matemática": "matematica.conjuntos-numericos-e-operacoes.operacoes-com-conjuntos",
          "Física": "fisica.atrito-e-plano-inclinado.atrito-estatico-e-cinetico"}


class DiagnosticsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.processed = root / "processados"
        exam = self.processed / "web" / "prova" / "questoes.classificado.json"
        exam.parent.mkdir(parents=True)
        subjects = ["Matemática"] * 4 + ["Física"] * 4
        exam.write_text(json.dumps({"prova": "prova-api", "questoes": [
            {"numero": n, "enunciado": f"Q{n}", "alternativas": {"A": {"texto": "a"}, "B": {"texto": "b"}},
             "classificacao": {"materia": subject}} for n, subject in enumerate(subjects, 1)]}), encoding="utf-8")
        review.save_reviews_bulk(exam, {n: {"status": "reviewed", "subject": subject, "content_id": LEAVES[subject],
                                            "taxonomy_version": CONTENT_TAXONOMY_VERSION}
                                        for n, subject in enumerate(subjects, 1)})
        self.db_path = root / "diag.sqlite3"
        db = diagnostics.connect_db(self.db_path)
        try:
            for number in range(1, len(subjects) + 1):
                db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at) "
                           "VALUES(?,?,?,?,?)", ("prova-api", number, "A", "g.pdf", "now"))
            db.commit()
            config = diagnostics.load_course_config(CONFIG)
            pool, _ = diagnostics.load_question_pool(self.processed, db, list(config["cursos"]["eng-comp-sao-carlos"]["pesos"]))
            self.adaptive = diagnostics.create_attempt(db, config, "eng-comp-sao-carlos", pool, total=4, seed=1)
            self.exam = diagnostics.create_attempt(db, config, "eng-comp-sao-carlos", pool, total=4, seed=1,
                                                   mode="static")
            db.execute("UPDATE attempts SET mode='exam' WHERE id=?", (self.exam["id"],))
            db.commit()
        finally:
            db.close()
        self.patches = [patch.object(server, "PROCESSED_ROOT", self.processed),
                        patch.object(server, "DEFAULT_QUESTIONS_DIR", self.processed),
                        patch.object(server, "DEFAULT_DIAGNOSTICS_DB", self.db_path),
                        patch.object(server, "DEFAULT_COURSE_CONFIG", CONFIG),
                        patch.dict(server.POOL_CACHE, clear=True)]
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
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def test_courses_include_derived_weights_and_redacao(self):
        status, courses = self.request("/api/courses")
        self.assertEqual(status, 200)
        course = next(item for item in courses if item["id"] == "eng-comp-sao-carlos")
        self.assertEqual(course["formula"], "fuvest-2027")
        self.assertAlmostEqual(sum(course["weights"].values()), 1.0, places=2)
        self.assertAlmostEqual(course["nonAssessable"]["Redação"], 1 / 6, places=3)

    def test_history_hides_exam_attempts(self):
        status, rows = self.request("/api/diagnostics")
        self.assertEqual(status, 200)
        ids = [row["id"] for row in rows]
        self.assertIn(self.adaptive["id"], ids)
        self.assertNotIn(self.exam["id"], ids)

    def test_exam_attempts_are_rejected_by_diagnostic_routes(self):
        status, body = self.request(f"/api/diagnostics/{self.exam['id']}/answers", "POST", {"ordinal": 1, "answer": "A"})
        self.assertEqual(status, 400)
        self.assertIn(f"/api/exam-sessions/{self.exam['id']}/answers", body["error"])
        status, body = self.request(f"/api/diagnostics/{self.exam['id']}/finalize", "POST", {})
        self.assertEqual(status, 400)
        self.assertIn("/api/exam-sessions/", body["error"])

    def test_adaptive_answer_returns_next_question(self):
        status, payload = self.request(f"/api/diagnostics/{self.adaptive['id']}")
        self.assertEqual((status, payload["mode"], payload["done"], len(payload["questions"])),
                         (200, "adaptive", False, 1))
        status, result = self.request(f"/api/diagnostics/{self.adaptive['id']}/answers", "POST",
                                      {"ordinal": 1, "answer": "A"})
        self.assertEqual(status, 200)
        self.assertEqual((result["next_ordinal"], result["done"]), (2, False))
        status, payload = self.request(f"/api/diagnostics/{self.adaptive['id']}")
        self.assertEqual(len(payload["questions"]), 2)


    def test_answer_key_only_after_the_diagnostic_is_finished(self):
        status, payload = self.request(f"/api/diagnostics/{self.adaptive['id']}")
        self.assertNotIn("correctAnswer", payload["questions"][0])
        self.assertEqual(payload["questions"][0]["number"], payload["questions"][0]["question"]["numero"])
        ordinal, done = 1, False
        while not done:  # o adaptativo acrescenta a próxima questão a cada resposta
            _, result = self.request(f"/api/diagnostics/{self.adaptive['id']}/answers", "POST",
                                     {"ordinal": ordinal, "answer": "B", "reason": "calc_error"})
            ordinal, done = result["next_ordinal"], result["done"]
        db = diagnostics.connect_db(self.db_path)
        try:
            diagnostics.finalize_attempt(db, self.adaptive["id"], None, include_analysis=False)
        finally:
            db.close()
        status, payload = self.request(f"/api/diagnostics/{self.adaptive['id']}")
        first = payload["questions"][0]
        self.assertEqual((first["correctAnswer"], first["correct"]), ("A", False))


if __name__ == "__main__":
    unittest.main()
