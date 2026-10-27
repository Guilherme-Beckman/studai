import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fsrs import Rating

from studai.features.diagnostics import mastery
from studai.features.diagnostics import service as diagnostics
from studai.features.study import proficiency, scheduler
from studai.features.study import service as study
from test_study_scheduler import T0, entry, leaves


class RatingTests(unittest.TestCase):
    def test_confidence_time_and_hints_choose_the_rating(self):
        cases = [
            ((False, "sure", 10_000, 0), Rating.Again),
            ((True, "guess", 10_000, 0), Rating.Again),
            ((True, "doubt", 10_000, 0), Rating.Hard),
            ((True, "sure", 10_000, 1), Rating.Hard),
            ((True, "sure", 250_000, 0), Rating.Good),
            ((True, "sure", None, 0), Rating.Good),
            ((True, "sure", 200_000, 0), Rating.Easy),
            ((True, None, None, 0), Rating.Good),  # respostas antigas, sem confiança
        ]
        for args, expected in cases:
            self.assertEqual(scheduler.rating_for(*args), expected, args)

    def test_easy_answer_comes_back_later_than_hard(self):
        card = scheduler.new_card(T0)
        hard, _ = scheduler.review(card, True, T0, confidence="doubt")
        easy, _ = scheduler.review(card, True, T0, confidence="sure", elapsed_ms=60_000)
        self.assertGreater(scheduler.due_at(easy), scheduler.due_at(hard))

    def test_answer_extras_validation(self):
        self.assertEqual(diagnostics.answer_extras("SURE", "1500.7", 2, "  sinal trocado  "),
                         {"confidence": "sure", "elapsed_ms": 1500, "hints_used": 2, "note": "sinal trocado"})
        self.assertEqual(diagnostics.answer_extras(elapsed_ms=10 ** 9)["elapsed_ms"], diagnostics.MAX_ELAPSED_MS)
        for kwargs in ({"confidence": "talvez"}, {"elapsed_ms": -1}, {"elapsed_ms": "x"}, {"elapsed_ms": True},
                       {"hints_used": 4}, {"note": "x" * 1001}):
            with self.assertRaises(ValueError, msg=kwargs):
                diagnostics.answer_extras(**kwargs)


class DailyConfidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "historico.sqlite3")
        self.math = leaves("Matemática", 3)
        self.pool = [entry("prova-1", i + 1, "Matemática", c) for i, c in enumerate(self.math)]
        self.config = {"version": 1, "cursos": {"curso": {"nome": "Curso", "pesos": {"Matemática": 1}}}}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def session(self, now=T0):
        return study.plan_today(self.db, self.pool, self.config, "curso", now, new_per_day=3)

    def test_guessed_hit_is_scored_but_not_counted_as_mastery(self):
        session = self.session()
        item = session["items"][0]
        result = study.record_study_answer(self.db, session["id"], item["id"], "A", None, T0,
                                           confidence="guess", elapsed_ms=90_000)
        self.assertTrue(result["correct"])
        self.assertEqual((result["confidence"], result["rating"]), ("guess", "again"))
        # Chute certo cria o cartão da questão, que volta como questão vencida.
        kinds = sorted(row["kind"] for row in self.db.execute("SELECT kind FROM review_cards"))
        self.assertEqual(kinds, ["content", "question"])
        response = study.course_responses(self.db, "curso")[0]
        self.assertEqual((response["correct"], response["is_correct"]), (False, True))
        payload = study.session_payload(self.db, session["id"])["items"][0]
        self.assertEqual((payload["confidence"], payload["elapsedMs"]), ("guess", 90_000))

    def test_sure_and_fast_hit_is_easy(self):
        session = self.session()
        item = session["items"][0]
        result = study.record_study_answer(self.db, session["id"], item["id"], "A", None, T0,
                                           confidence="sure", elapsed_ms=40_000, note="fácil")
        self.assertEqual(result["rating"], "easy")
        row = self.db.execute("SELECT confidence,elapsed_ms,hints_used,note FROM study_answers").fetchone()
        self.assertEqual(tuple(row), ("sure", 40_000, 0, "fácil"))

    def test_blank_answer_drops_the_confidence(self):
        session = self.session()
        item = session["items"][0]
        result = study.record_study_answer(self.db, session["id"], item["id"], None, None, T0, confidence="sure")
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["rating"], "again")

    def test_diagnostic_confidence_reaches_fsrs_and_mastery(self):
        pool = [entry("prova-1", 1, "Matemática", self.math[0])]
        attempt = diagnostics.create_attempt(self.db, self.config, "curso", pool, total=1, seed=1, provider=None,
                                             mode="static")
        diagnostics.record_response(self.db, attempt["id"], 1, "A", confidence="guess", elapsed_ms=5000)
        row = dict(self.db.execute("SELECT * FROM attempt_questions").fetchone())
        self.assertEqual((row["confidence"], row["elapsed_ms"]), ("guess", 5000))
        self.assertFalse(mastery.response_from_row(row)["correct"])
        study.sync_diagnostic_answers(self.db)
        ratings = {int(r["rating"]) for r in self.db.execute("SELECT rating FROM review_logs")}
        self.assertEqual(ratings, {int(Rating.Again)})


class MigrationTests(unittest.TestCase):
    def test_old_database_gets_the_new_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "historico.sqlite3"
            old = sqlite3.connect(path)
            old.executescript("""
                CREATE TABLE attempt_questions (attempt_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    source_id TEXT NOT NULL, question_number INTEGER NOT NULL, subject TEXT NOT NULL,
                    topic TEXT NOT NULL, question_json TEXT NOT NULL, correct_answer TEXT NOT NULL,
                    selected_answer TEXT, error_reason TEXT, is_correct INTEGER, answered_at TEXT,
                    PRIMARY KEY(attempt_id, ordinal));
                CREATE TABLE study_answers (session_id TEXT NOT NULL, item_id TEXT NOT NULL,
                    source_id TEXT NOT NULL, question_number INTEGER NOT NULL, subject TEXT NOT NULL,
                    content_id TEXT NOT NULL, selected_answer TEXT, correct_answer TEXT NOT NULL,
                    is_correct INTEGER NOT NULL, error_reason TEXT, answered_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, item_id));
                INSERT INTO study_answers VALUES ('s','i','p',1,'Matemática','c','A','A',1,NULL,'2026-10-01');
            """)
            old.commit()
            old.close()
            db = study.connect_db(path)
            try:
                for table in ("attempt_questions", "study_answers"):
                    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                    self.assertTrue({"confidence", "elapsed_ms", "hints_used", "note"} <= columns, table)
                row = db.execute("SELECT confidence,hints_used FROM study_answers").fetchone()
                self.assertEqual(tuple(row), (None, 0))
            finally:
                db.close()


class ProficiencyTests(unittest.TestCase):
    def test_buckets_calibration_and_pace(self):
        def response(subject, correct, confidence, seconds):
            return {"subject": subject, "is_correct": correct, "correct": correct and confidence != "guess",
                    "confidence": confidence, "elapsed_ms": None if seconds is None else seconds * 1000}
        responses = [response("Física", True, "sure", 100), response("Física", False, "sure", 300),
                     response("Física", True, "doubt", 250), response("Física", True, "guess", None),
                     response("História", True, None, None)]
        result = proficiency.subject_proficiency(responses)
        physics = next(item for item in result["subjects"] if item["subject"] == "Física")
        self.assertEqual(physics["counts"], {"sure": 1, "doubt": 1, "guess": 1, "wrong": 1})
        self.assertEqual(physics["calibration"], 0.5)
        self.assertEqual((physics["avgSeconds"], physics["overPace"], physics["timed"]), (216.7, 2, 3))
        history = next(item for item in result["subjects"] if item["subject"] == "História")
        self.assertEqual(sum(history["counts"].values()), 0)  # acerto antigo sem confiança não entra nas faixas
        self.assertIsNone(history["calibration"])
        self.assertEqual(result["paceSeconds"], scheduler.PACE_SECONDS)


if __name__ == "__main__":
    unittest.main()
