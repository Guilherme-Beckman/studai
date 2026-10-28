"""Relatórios: os dias de Treinamento entram na linha do tempo e no domínio."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _reports_support import COURSE_ID, build, config, load_pool
from studai.features.diagnostics import service as diagnostics
from studai.features.reports import service as reports
from studai.features.study import calendar
from studai.features.study import service as study

NOW = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.pool = load_pool(self.db, self.processed)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def train(self, now, count):
        session = study.plan_today(self.db, self.pool, config(), COURSE_ID, now, new_per_day=count)
        for item in session["items"][:count]:
            raw = next(entry for entry in self.pool
                       if (entry["source_id"], entry["number"]) == (item["sourceId"], item["number"]))
            study.record_study_answer(self.db, session["id"], item["id"], raw["correct_answer"], None, now,
                                      self.pool, confidence="sure")
        return session

    def test_training_days_are_timeline_points(self):
        self.assertEqual(reports.overview(self.db, config(), COURSE_ID, self.pool)["timeline"], [])
        first = self.train(NOW, 3)
        self.train(NOW + timedelta(days=1), 2)
        overview = reports.overview(self.db, config(), COURSE_ID, self.pool)
        timeline = overview["timeline"]
        self.assertEqual([(p["kind"], p["label"], p["answered"]) for p in timeline],
                         [("daily", "Treinamento 20/10", 3), ("daily", "Treinamento 21/10", 2)])
        self.assertEqual(timeline[0]["accuracy"], 1.0)
        self.assertEqual(overview["kpis"]["answered"], 5)
        node_id = study.content_id_of(next(e for e in self.pool if e["source_id"] == first["items"][0]["sourceId"]
                                           and e["number"] == first["items"][0]["number"])["question"])
        detail = reports.node_detail(self.db, config(), COURSE_ID, node_id, self.pool)
        self.assertTrue(any(answer["kind"] == "daily" for answer in detail["recent_answers"]))

    def test_diagnostics_and_training_are_in_time_order(self):
        self.train(NOW, 2)
        attempt = diagnostics.create_attempt(self.db, config(), COURSE_ID, self.pool, total=2, seed=1,
                                             provider=None, mode="static")
        for ordinal in (1, 2):
            diagnostics.record_response(self.db, attempt["id"], ordinal, "A", "calc_error")
        kinds = [point["kind"] for point in reports.overview(self.db, config(), COURSE_ID, self.pool)["timeline"]]
        self.assertEqual(kinds, ["daily", "diagnostic"])

    def test_first_phase_target_sets_report_and_diagnostic_weights(self):
        cfg = config()
        cfg["cursos"][COURSE_ID].update(formula="fuvest-2027", fase2={"dia1": [], "dia2": ["Matemática"]})
        self.assertEqual(reports.overview(self.db, cfg, COURSE_ID, self.pool)["course"]["weights"],
                         {"Matemática": 0.75, "Física": 0.25})
        calendar.add_exam(self.db, COURSE_ID, "FUVEST", (NOW + timedelta(days=60)).date().isoformat(), "fase1",
                          datetime.now(timezone.utc))
        course = reports.overview(self.db, cfg, COURSE_ID, self.pool)["course"]
        self.assertEqual((course["phase"], course["weights"]), ("fase1", {"Matemática": 0.5, "Física": 0.5}))
        attempt = diagnostics.create_attempt(self.db, cfg, COURSE_ID, self.pool, total=4, seed=1, provider=None,
                                             mode="static", phase="fase1")
        plan = diagnostics.get_attempt(self.db, attempt["id"])["plan"]
        self.assertEqual(plan["weights"]["weights"], {"Matemática": 0.5, "Física": 0.5})

    def test_reports_still_work_without_study_tables(self):
        db = diagnostics.connect_db(Path(self.temp.name) / "only-diagnostics.sqlite3")
        try:
            self.assertEqual(reports.overview(db, config(), COURSE_ID, self.pool)["timeline"], [])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
