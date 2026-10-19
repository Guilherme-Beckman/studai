import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from _reports_support import COURSE_ID, EXAMS, build, config, load_pool
from studai.api import server
from studai.features.diagnostics import service as diagnostics
from studai.tools.simulate_student import simulate


class ReportsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.processed, config_path, self.db_path = build(root)
        web = root / "web"
        web.mkdir()
        (web / "reports.js").write_text("export const ok = true;\n", encoding="utf-8")
        db = diagnostics.connect_db(self.db_path)
        try:
            simulate(db, config(), COURSE_ID, load_pool(db, self.processed), sessions=4, exams=1, seed=3,
                     end=date(2026, 10, 19), per_diagnostic=12)
        finally:
            db.close()
        self.patches = [
            patch.object(server, "PROCESSED_ROOT", self.processed),
            patch.object(server, "DEFAULT_QUESTIONS_DIR", self.processed),
            patch.object(server, "DEFAULT_DIAGNOSTICS_DB", self.db_path),
            patch.object(server, "DEFAULT_COURSE_CONFIG", config_path),
            patch.object(server, "WEB_ROOT", web),
            patch.dict(server.POOL_CACHE, clear=True),
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

    def test_overview_defaults_to_course_and_is_cached(self):
        status, payload = self.request("/api/reports/overview")
        self.assertEqual(status, 200)
        self.assertEqual(payload["course"]["id"], COURSE_ID)
        self.assertEqual(len(payload["timeline"]), 4)
        self.assertEqual([p["kind"] for p in payload["timeline"]].count("exam"), 1)
        self.assertEqual(len(server.POOL_CACHE), 1)
        status, again = self.request(f"/api/reports/overview?courseId={COURSE_ID}")
        self.assertEqual((status, again["kpis"]), (200, payload["kpis"]))
        status, error = self.request("/api/reports/overview?courseId=nao-existe")
        self.assertEqual(status, 404)
        self.assertIn("error", error)

    def test_node_detail_found_and_missing(self):
        status, detail = self.request(f"/api/reports/nodes/matematica?courseId={COURSE_ID}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["node"]["id"], "matematica")
        self.assertEqual(len(detail["series"]), 4)
        self.assertTrue(detail["children"])
        self.assertTrue(detail["recent_answers"])
        status, error = self.request("/api/reports/nodes/matematica.nao-existe")
        self.assertEqual(status, 404)
        self.assertIn("error", error)

    def test_exam_session_start_answer_finish(self):
        status, session = self.request(f"/api/exams/{EXAMS[1]}/sessions", "POST", {"courseId": COURSE_ID})
        self.assertEqual(status, 201)
        self.assertEqual(len(session["questions"]), 20)
        self.assertEqual(session["questions"][0], {"ordinal": 1, "number": 1})
        db = diagnostics.connect_db(self.db_path)
        try:
            correct = diagnostics.list_questions_for_attempt(db, session["id"])[0]["correct_answer"]
        finally:
            db.close()
        wrong = next(letter for letter in "ABCDE" if letter != correct)
        status, answer = self.request(f"/api/exam-sessions/{session['id']}/answers", "POST",
                                      {"ordinal": 1, "answer": wrong})
        self.assertEqual((status, answer["correct"], answer["reason"]), (200, False, None))
        status, answer = self.request(f"/api/exam-sessions/{session['id']}/answers", "POST",
                                      {"ordinal": 2, "answer": "Z"})
        self.assertEqual(status, 400)
        status, finished = self.request(f"/api/exam-sessions/{session['id']}/finish", "POST", {})
        self.assertEqual((status, finished["status"], finished["metrics"]["answered"]), (200, "completed", 1))
        status, payload = self.request("/api/reports/overview")
        self.assertEqual(payload["timeline"][-1]["id"], session["id"])
        self.assertEqual(payload["timeline"][-1]["label"], "Prova FUVEST 2025 (V1)")

    def test_exam_session_errors(self):
        status, _ = self.request("/api/exams/prova-inexistente/sessions", "POST", {})
        self.assertEqual(status, 404)
        status, _ = self.request("/api/exam-sessions/inexistente/finish", "POST", {})
        self.assertEqual(status, 404)
        status, _ = self.request("/api/exam-sessions/inexistente/answers", "POST", {"answer": "A"})
        self.assertEqual(status, 400)

    def test_reports_js_is_served_as_javascript(self):
        with urllib.request.urlopen(self.base + "/reports.js") as response:
            self.assertIn("javascript", response.headers["Content-Type"])
            self.assertIn(b"export const ok", response.read())

    def test_configure_paths_overrides_defaults(self):
        other = Path(self.temp.name) / "other.sqlite3"
        server.configure_paths(diagnostics_db=other, questions_dir=self.processed)
        self.assertEqual(server.DEFAULT_DIAGNOSTICS_DB, other.resolve())
        self.assertEqual(server.DEFAULT_QUESTIONS_DIR, self.processed.resolve())
        self.assertEqual(server.PROCESSED_ROOT, self.processed.resolve())


if __name__ == "__main__":
    unittest.main()
