import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from studai.api import server
from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.diagnostics import service as diagnostics
from studai.features.study import scheduler
from studai.features.study import service as study

T0 = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)  # 10h em São Paulo


def leaves(subject: str, count: int) -> list[str]:
    nodes = sorted(node["id"] for node in CONTENT_INDEX.values()
                   if node["subject"] == subject and node["is_leaf"] and not node.get("deprecated"))
    return nodes[:count]


def entry(source: str, number: int, subject: str, content_id: str) -> dict:
    question = {"numero": number, "enunciado": f"Enunciado {number}",
                "alternativas": {"A": {"texto": "a"}, "B": {"texto": "b"}},
                "classificacao": {"materia": subject, "conteudo_principal_id": content_id}}
    return {"source_id": source, "source_file": "", "number": number, "subject": subject,
            "topic": content_id, "question": question, "correct_answer": "A"}


class SchedulerTests(unittest.TestCase):
    def test_consecutive_correct_answers_grow_the_interval(self):
        card, now, intervals = scheduler.new_card(T0), T0, []
        for _ in range(4):
            card, _ = scheduler.review(card, True, now)
            due = scheduler.due_at(card)
            intervals.append(due - now)
            now = due
        self.assertEqual(intervals, sorted(intervals))
        self.assertGreater(intervals[-1], intervals[0])

    def test_wrong_answer_comes_back_sooner_than_correct(self):
        base, _ = scheduler.review(scheduler.new_card(T0), True, T0)
        later = scheduler.due_at(base)
        right, _ = scheduler.review(base, True, later)
        wrong, _ = scheduler.review(base, False, later)
        self.assertLess(scheduler.due_at(wrong), scheduler.due_at(right))
        self.assertTrue(scheduler.is_due(wrong, scheduler.due_at(wrong)))


class StudyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "historico.sqlite3")
        self.math = leaves("Matemática", 4)
        self.physics = leaves("Física", 4)
        self.pool = [entry("prova-1", i + 1, "Matemática", c) for i, c in enumerate(self.math)]
        self.pool += [entry("prova-1", i + 11, "Física", c) for i, c in enumerate(self.physics)]
        self.pool += [entry("prova-2", i + 1, "Matemática", c) for i, c in enumerate(self.math)]
        self.profile = {"nome": "Curso", "pesos": {"Matemática": 3, "Física": 1}}
        self.config = {"version": 1, "cursos": {"curso": self.profile}}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def today(self, now, **kwargs):
        return study.plan_today(self.db, self.pool, self.config, "curso", now, **kwargs)

    def answer(self, session, item, answer, reason=None, now=T0):
        return study.record_study_answer(self.db, session["id"], item["id"], answer, reason, now)

    def test_first_day_brings_only_new_contents_weighted_by_course(self):
        session = self.today(T0, new_per_day=4)
        self.assertEqual(len(session["items"]), 4)
        self.assertEqual({item["reason"] for item in session["items"]}, {"new_content"})
        subjects = [item["subject"] for item in session["items"]]
        self.assertEqual(subjects.count("Matemática"), 3)
        self.assertNotIn("correctAnswer", session["items"][0])

    def test_session_is_stable_within_the_same_day(self):
        first = self.today(T0)
        second = self.today(T0 + timedelta(hours=8))
        self.assertEqual(first["id"], second["id"])
        third = self.today(T0 + timedelta(days=1))
        self.assertNotEqual(first["id"], third["id"])

    def test_size_limits_the_session(self):
        self.assertEqual(len(self.today(T0, size=2, new_per_day=5)["items"]), 2)

    def test_wrong_answer_requires_reason_and_returns_as_overdue_question(self):
        session = self.today(T0, new_per_day=1)
        item = session["items"][0]
        self.assertEqual(self.answer(session, item, "B"), {"requiresReason": True})
        result = self.answer(session, item, "B", "unknown_content")
        self.assertFalse(result["correct"])
        self.assertEqual(result["correctAnswer"], "A")
        with self.assertRaises(ValueError):
            self.answer(session, item, "A")
        next_day = self.today(T0 + timedelta(days=1), new_per_day=0)
        reasons = [(i["reason"], i["sourceId"], i["number"]) for i in next_day["items"]]
        self.assertEqual(reasons[0], ("overdue_question", item["sourceId"], item["number"]))

    def test_correct_answer_schedules_content_for_later(self):
        session = self.today(T0, new_per_day=1)
        result = self.answer(session, session["items"][0], "A")
        self.assertTrue(result["correct"])
        self.assertGreater(datetime.fromisoformat(result["nextReview"]), T0 + timedelta(days=1))
        cards = self.db.execute("SELECT kind FROM review_cards").fetchall()
        self.assertEqual([row["kind"] for row in cards], ["content"])
        due_day = datetime.fromisoformat(result["nextReview"])
        later = self.today(due_day, new_per_day=0)
        self.assertEqual([i["reason"] for i in later["items"]], ["due_content"])
        self.assertEqual(later["items"][0]["contentId"], session["items"][0]["contentId"])

    def test_diagnostic_answers_sync_once_and_mark_gaps(self):
        pool = [entry("prova-1", i + 1, "Matemática", self.math[0]) for i in range(3)]
        attempt = diagnostics.create_attempt(self.db, self.config, "curso", pool, total=3, seed=1, provider=None)
        for ordinal in (1, 2, 3):
            diagnostics.record_response(self.db, attempt["id"], ordinal, "B", "calc_error")
        diagnostics.finalize_attempt(self.db, attempt["id"], None, include_analysis=False)
        self.assertEqual(study.sync_diagnostic_answers(self.db), 3)
        self.assertEqual(study.sync_diagnostic_answers(self.db), 0)
        logs = self.db.execute("SELECT COUNT(*) FROM review_logs WHERE origin='diagnostic'").fetchone()[0]
        self.assertEqual(logs, 6)  # cartão do conteúdo + cartão de cada questão errada
        # Os vizinhos do conteúdo fraco (mesma área) entram como lacuna antes dos demais.
        node = CONTENT_INDEX[self.math[0]]
        sibling = next(c for c in self.math[1:] if CONTENT_INDEX[c]["path_ids"][:-1] == node["path_ids"][:-1])
        session = self.today(T0 + timedelta(days=30), new_per_day=1)
        gap = [i for i in session["items"] if i["reason"] == "new_gap"]
        self.assertEqual([i["contentId"] for i in gap], [sibling])

    def test_progress_reports_due_reviews_and_forecast(self):
        session = self.today(T0, new_per_day=2)
        self.answer(session, session["items"][0], "A")
        self.answer(session, session["items"][1], "B", "misread")
        report = study.progress(self.db, T0 + timedelta(days=1))
        self.assertEqual(len(report["contents"]), 2)
        self.assertEqual(len(report["forecast"]), 7)
        self.assertGreaterEqual(report["dueToday"], 1)
        weakest = report["contents"][0]
        self.assertEqual(weakest["accuracy"], 0.0)

    def test_grade_answer_keeps_diagnostic_rules(self):
        alternatives = {"A": {}, "B": {}}
        self.assertEqual(diagnostics.grade_answer(" a ", alternatives, "A", None), ("A", True, None))
        self.assertEqual(diagnostics.grade_answer(None, alternatives, "A", None), (None, False, "blank"))
        with self.assertRaises(ValueError):
            diagnostics.grade_answer("C", alternatives, "A", None)
        with self.assertRaises(ValueError):
            diagnostics.grade_answer("A", alternatives, "A", "misread")
        with self.assertRaises(ValueError):
            diagnostics.grade_answer("B", alternatives, "A", None)


class StudyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        content = leaves("Matemática", 1)[0]
        exam_dir = root / "provas" / "prova-1"
        exam_dir.mkdir(parents=True)
        data = {"prova": "prova-1", "questoes": [entry("prova-1", 1, "Matemática", content)["question"]]}
        (exam_dir / "questoes.classificado.json").write_text(json.dumps(data), encoding="utf-8")
        (root / "cursos.json").write_text(json.dumps({"version": 1, "cursos": {"curso": {
            "nome": "Curso", "pesos": {"Matemática": 1}}}}), encoding="utf-8")
        db_path = root / "historico.sqlite3"
        db = study.connect_db(db_path)
        with db:
            db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at) "
                       "VALUES('prova-1',1,'A','gabarito.pdf','2026-10-01')")
        db.close()
        self.patches = [patch.object(server, "DEFAULT_DIAGNOSTICS_DB", db_path),
                        patch.object(server, "DEFAULT_QUESTIONS_DIR", root / "provas"),
                        patch.object(server, "DEFAULT_COURSE_CONFIG", root / "cursos.json")]
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

    def request(self, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=body, method="POST" if body else "GET",
                                         headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def test_today_answer_and_progress_routes(self):
        session = self.request("/api/study/today?courseId=curso")
        self.assertEqual([item["reason"] for item in session["items"]], ["new_content"])
        item = session["items"][0]
        self.assertEqual(self.request("/api/study/today/answers", {
            "sessionId": session["id"], "itemId": item["id"], "answer": "B"}), {"requiresReason": True})
        result = self.request("/api/study/today/answers", {
            "sessionId": session["id"], "itemId": item["id"], "answer": "B", "reason": "misread"})
        self.assertFalse(result["correct"])
        again = self.request("/api/study/today?courseId=curso")
        self.assertEqual(again["id"], session["id"])
        self.assertTrue(again["items"][0]["answered"])
        progress = self.request("/api/study/progress")
        self.assertEqual(progress["contents"][0]["reviews"], 1)


if __name__ == "__main__":
    unittest.main()
