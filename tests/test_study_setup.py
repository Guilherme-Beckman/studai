"""Primeiros passos: o checklist acompanha o que o aluno já configurou."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from _reports_support import COURSE_ID, build, config, load_pool
from studai.features.diagnostics import service as diagnostics
from studai.features.study import calendar, readings, setup
from studai.features.study import service as study

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.pool = load_pool(self.db, processed)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def done(self):
        return {step["id"]: step["done"] for step in setup.setup_steps(self.db, COURSE_ID, NOW)["steps"]}

    def test_steps_follow_what_was_configured(self):
        self.assertFalse(any(self.done().values()))
        exam = calendar.add_exam(self.db, COURSE_ID, "FUVEST", "2027-11-01", "fase1", NOW)
        calendar.set_exam_cutoff(self.db, exam["id"], 60, NOW.date())
        calendar.set_availability(self.db, COURSE_ID, {"0": 60})
        attempt = diagnostics.create_attempt(self.db, config(), COURSE_ID, self.pool, total=2, seed=1,
                                             provider=None, mode="static")
        for ordinal in (1, 2):
            diagnostics.record_response(self.db, attempt["id"], ordinal, "A", "calc_error")
        diagnostics.finalize_attempt(self.db, attempt["id"], None, include_analysis=False)
        readings.set_reading(self.db, readings.works()[0]["id"], "reading")
        done = self.done()
        self.assertEqual({key for key, value in done.items() if value},
                         {"exam", "cutoff", "availability", "diagnostic", "readings"})
        answers = next(step for step in setup.setup_steps(self.db, COURSE_ID, NOW)["steps"] if step["id"] == "answers")
        self.assertEqual((answers["progress"], answers["goal"]), (2, 30))


if __name__ == "__main__":
    unittest.main()
