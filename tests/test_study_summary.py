"""Resumo da semana e fechamento do dia."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _reports_support import COURSE_ID, build, config, load_pool
from studai.features.study import ladder, summary
from studai.features.study import service as study

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.pool = load_pool(self.db, self.processed)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def train(self, now, correct=True, reason=None):
        session = study.plan_today(self.db, self.pool, config(), COURSE_ID, now, size=6, new_per_day=6)
        # Um bloco de treino livre garante questões mesmo quando o plano do dia está vazio.
        session = study.append_training_item(self.db, session["id"], self.pool, config(), now, count=6)
        answered = 0
        for item in session["items"]:
            if item["answered"]:
                continue
            raw = next(e for e in self.pool if (e["source_id"], e["number"]) == (item["sourceId"], item["number"]))
            answer = raw["correct_answer"] if correct else next(l for l in "ABCDE" if l != raw["correct_answer"])
            study.record_study_answer(self.db, session["id"], item["id"], answer,
                                      None if correct else (reason or "calc_error"), now, self.pool,
                                      confidence="sure", elapsed_ms=60_000)
            answered += 1
        return answered

    def test_week_compares_with_a_week_ago(self):
        self.train(NOW - timedelta(days=9), correct=False)
        answered = self.train(NOW - timedelta(days=1)) + self.train(NOW)
        result = summary.period_summary(self.db, self.pool, config(), COURSE_ID, NOW, "week")
        self.assertEqual(result["answered"], answered)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["studySeconds"], answered * 60)
        self.assertEqual((result["activeDays"], result["streak"]), (2, 2))
        self.assertGreater(result["readiness"]["to"], result["readiness"]["from"])
        self.assertTrue(result["improved"])
        self.assertEqual(result["byKind"], {"daily": answered})
        self.assertEqual({item["subject"] for item in result["subjects"]}, {"Matemática", "Física"})

    def test_day_counts_only_today_and_reports_reasons_and_ladders(self):
        self.train(NOW - timedelta(days=1))
        self.train(NOW, correct=False, reason="unknown_content")
        result = summary.period_summary(self.db, self.pool, config(), COURSE_ID, NOW, "day")
        self.assertEqual(result["correct"], 0)
        self.assertEqual(list(result["reasons"]), ["unknown_content"])
        self.assertTrue(result["ladders"]["active"])
        self.assertEqual(result["streak"], 2)
        self.assertGreaterEqual(result["dueTomorrow"], 0)
        with self.assertRaises(ValueError):
            summary.period_summary(self.db, self.pool, config(), COURSE_ID, NOW, "year")

    def test_empty_history(self):
        result = summary.period_summary(self.db, self.pool, config(), COURSE_ID, NOW, "week")
        self.assertEqual((result["answered"], result["accuracy"], result["streak"]), (0, None, 0))
        self.assertEqual(result["consolidated"], [])


if __name__ == "__main__":
    unittest.main()
