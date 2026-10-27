import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from _reports_support import COURSE_ID, CONTENTS, EXAMS, build, config, load_pool
from studai.features.explanations import service as explanations
from studai.features.reports import simulado
from studai.features.study import calendar, projection
from studai.features.study import service as study

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


class SimuladoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.pool = load_pool(self.db, self.processed)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def start(self, minutes=300):
        return simulado.start_simulado(self.db, config(), COURSE_ID, self.pool, EXAMS[0], duration_s=minutes * 60)

    def key(self, ordinal):
        return self.db.execute("SELECT correct_answer FROM attempt_questions WHERE ordinal=?", (ordinal,)).fetchone()[0]

    def test_no_answer_key_until_delivery_and_answers_can_change(self):
        session = self.start()
        self.assertEqual((session["status"], len(session["questions"])), ("in_progress", 20))
        self.assertNotIn("correctAnswer", session["questions"][0])
        self.assertGreater(session["remainingS"], 0)
        wrong = next(letter for letter in "ABCDE" if letter != self.key(1))
        simulado.answer(self.db, session["id"], 1, wrong, elapsed_ms=60_000)
        simulado.answer(self.db, session["id"], 1, self.key(1), confidence="doubt", elapsed_ms=30_000)  # trocou
        simulado.answer(self.db, session["id"], 2, wrong if wrong != self.key(2) else "E", elapsed_ms=250_000)
        simulado.answer(self.db, session["id"], 3, "A")
        simulado.answer(self.db, session["id"], 3, None)  # apagou
        simulado.flag(self.db, session["id"], 4, True)
        detail = simulado.detail(self.db, session["id"])
        self.assertTrue(detail["questions"][3]["flagged"])
        self.assertEqual(detail["questions"][0]["elapsedMs"], 90_000)
        self.assertFalse(explanations.question_answered(self.db, self.pool[0]["source_id"], 1,
                                                        attempt_id=session["id"], ordinal=1))
        with self.assertRaises(ValueError):
            simulado.set_reason(self.db, session["id"], 2, "trap")  # antes da entrega
        result = simulado.finish(self.db, session["id"])
        score = result["score"]
        self.assertEqual((score["total"], score["answered"]), (20, 2))
        self.assertEqual(score["correct"], 1 + int(self.key(2) == "E" and wrong == self.key(2)))
        self.assertEqual(score["scaledTo90"], round(score["correct"] / 20 * 90))
        self.assertEqual(score["overPace"], [2])
        self.assertEqual(result["questions"][0]["correctAnswer"], self.key(1))
        self.assertTrue(explanations.question_answered(self.db, self.pool[0]["source_id"], 1,
                                                       attempt_id=session["id"], ordinal=1))
        self.assertEqual(simulado.set_reason(self.db, session["id"], 2, "trap", "unidade")["note"], "unidade")
        for ordinal, reason in ((1, "trap"), (3, "trap"), (2, "blank")):
            with self.assertRaises(ValueError):
                simulado.set_reason(self.db, session["id"], ordinal, reason)
        with self.assertRaises(ValueError):
            simulado.answer(self.db, session["id"], 5, "A")

    def test_time_up_delivers_the_exam(self):
        session = self.start(minutes=5)
        started = datetime.fromisoformat(session["startedAt"])
        with self.assertRaises(ValueError):
            simulado.answer(self.db, session["id"], 1, "A", now=started + timedelta(minutes=6))
        self.assertEqual(simulado.detail(self.db, session["id"])["status"], "completed")

    def test_duration_limits(self):
        for minutes in (1, 400):
            with self.assertRaises(ValueError):
                self.start(minutes=minutes)

    def test_cutoff_on_exam_dates(self):
        exam = calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW, cutoff="62")
        self.assertEqual(exam["cutoff"], 62)
        for value in (91, -1, "x", True):
            with self.assertRaises(ValueError):
                calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2026-11-22", "fase1", NOW, cutoff=value)
        self.assertEqual(calendar.set_exam_cutoff(self.db, exam["id"], 70, NOW.date())["cutoff"], 70)
        self.assertIsNone(calendar.set_exam_cutoff(self.db, exam["id"], "", NOW.date())["cutoff"])
        with self.assertRaises(calendar.ExamNotFound):
            calendar.set_exam_cutoff(self.db, "nao-existe", 50, NOW.date())


class ProjectionTests(unittest.TestCase):
    def test_trend_needs_two_points_spread_over_two_weeks(self):
        self.assertIsNone(projection._trend([(0, 40)]))
        self.assertIsNone(projection._trend([(-7, 40), (0, 42)]))
        intercept, slope = projection._trend([(-28, 40), (-14, 44), (0, 48)])
        self.assertAlmostEqual(intercept, 48)
        self.assertAlmostEqual(slope, 2 / 7)

    def test_projection_from_weekly_estimates(self):
        leaf = CONTENTS["Matemática"][0]
        responses = []
        # Seis semanas de respostas em Matemática, acertando cada vez mais.
        for week in range(6):
            day = NOW - timedelta(days=7 * (5 - week) + 1)
            for index in range(20):
                responses.append({"subject": "Matemática", "principal_id": leaf, "related_ids": [],
                                  "correct": index < 8 + 2 * week, "answered_at": day.isoformat()})
        weights = {"Matemática": 1.0}
        with tempfile.TemporaryDirectory() as temp:
            db = study.connect_db(Path(temp) / "h.sqlite3")
            try:
                with patch.object(study, "course_responses", return_value=responses):
                    result = projection.project(db, COURSE_ID, weights, NOW, date(2026, 12, 3), cutoff=60)
            finally:
                db.close()
        self.assertIsNotNone(result["current"])
        self.assertGreater(result["slopePerWeek"], 0)
        self.assertGreater(result["atExam"], result["current"])
        self.assertLessEqual(result["atExam"], 90)
        self.assertEqual(result["cutoff"], 60)
        self.assertEqual(result["weekly"][-1]["value"], result["current"])

    def test_few_responses_give_no_estimate(self):
        self.assertIsNone(projection.estimate_correct([], {"Matemática": 1.0}, NOW))


if __name__ == "__main__":
    unittest.main()
