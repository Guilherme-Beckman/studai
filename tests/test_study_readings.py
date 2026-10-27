import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.study import readings
from studai.features.study import service as study
from test_study_scheduler import entry

WORK = "literatura.content.clarice-lispector-a-paixao-segundo-gh"
NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "historico.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_works_are_the_current_list_without_the_overview(self):
        ids = [node["id"] for node in readings.works()]
        self.assertIn(WORK, ids)
        self.assertNotIn(readings.OVERVIEW_ID, ids)
        self.assertEqual(len(ids), 11)

    def test_status_note_and_questions_per_work(self):
        sub = next(node["id"] for node in CONTENT_INDEX.values() if node["path_ids"][:3] == CONTENT_INDEX[WORK]["path_ids"]
                   and len(node["path_ids"]) == 4)
        pool = [entry("prova-1", 1, "Literatura", WORK), entry("prova-1", 2, "Literatura", sub)]
        config = {"version": 1, "cursos": {"curso": {"nome": "x", "pesos": {"Literatura": 1}}}}
        session = study.plan_today(self.db, pool, config, "curso", NOW, new_per_day=2)
        item = session["items"][0]
        study.record_study_answer(self.db, session["id"], item["id"], "A", None, NOW)
        readings.set_reading(self.db, WORK, "reading", "  narradora G.H.  ")
        readings.set_reading(self.db, WORK, "read")  # a anotação continua
        result = readings.list_readings(self.db, pool, "curso")
        work = next(row for row in result["works"] if row["contentId"] == WORK)
        self.assertEqual((work["status"], work["note"]), ("read", "narradora G.H."))
        self.assertEqual((work["questions"], work["answered"], work["correct"]), (2, 1, 1))
        self.assertEqual((result["read"], result["total"]), (1, 11))
        other = next(row for row in result["works"] if row["contentId"] != WORK)
        self.assertEqual((other["status"], other["questions"]), ("not_started", 0))
        for args in ((WORK, "talvez"), ("matematica", "read"), (WORK, None, "x" * 2001)):
            with self.assertRaises(ValueError):
                readings.set_reading(self.db, *args)


if __name__ == "__main__":
    unittest.main()
