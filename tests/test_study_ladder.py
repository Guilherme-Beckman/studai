import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from studai.features.diagnostics import service as diagnostics
from studai.features.study import ladder
from studai.features.study import service as study
from test_study_scheduler import T0, entry, leaves


def practice(source, number, content, difficulty, origin="ia"):
    item = entry(source, number, "Matemática", content)
    item.update(origin=origin, difficulty=difficulty)
    item["question"] = {**item["question"], "_origem": origin, "_dificuldade": difficulty}
    return item


class LadderRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "h.sqlite3")
        self.leaf = leaves("Matemática", 1)[0]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_climbs_falls_and_finishes(self):
        self.assertEqual(ladder.start(self.db, "c", self.leaf, T0)["level"], 1)
        step = lambda ok: ladder.record(self.db, "c", self.leaf, ok, T0)
        self.assertIsNone(step(True)["change"])
        self.assertEqual((step(True)["change"], ladder.get(self.db, "c", self.leaf)["level"]), ("up", 2))
        step(False)
        self.assertEqual(step(False)["change"], "down")
        for _ in range(2 * 3):
            step(True)
        self.assertEqual(ladder.get(self.db, "c", self.leaf)["level"], 4)
        step(True)
        self.assertEqual(step(True)["change"], "done")
        self.assertIsNone(step(True))  # trilha concluída não muda mais
        self.assertEqual(ladder.start(self.db, "c", self.leaf, T0)["level"], 1)  # novo erro reabre do zero
        self.assertEqual(ladder.close(self.db, "c", self.leaf, T0)["status"], "closed")

    def test_pick_by_level_with_fallback(self):
        other = leaves("Matemática", 2)[1]
        pool = [practice("ia", 1, self.leaf, 2), practice("ia", 2, self.leaf, 1), practice("ia", 3, other, 1),
                practice("fuvest", 1, self.leaf, 3, origin="fuvest"), practice("fuvest", 2, self.leaf, None, "fuvest")]
        chosen, missing = ladder.pick(self.db, pool, self.leaf, 1, set(), 2, T0)
        # Fácil do mesmo subconteúdo primeiro, depois fácil do conteúdo acima dele.
        self.assertEqual([(e["source_id"], e["number"]) for e, _ in chosen], [("ia", 2), ("ia", 3)])
        self.assertFalse(missing)
        chosen, missing = ladder.pick(self.db, pool, self.leaf, 1, set(), 3, T0)
        self.assertEqual((chosen[-1][0]["source_id"], chosen[-1][0]["number"]), ("ia", 1))
        self.assertTrue(missing)  # só duas fáceis: completou com a média mais próxima
        chosen, missing = ladder.pick(self.db, pool, self.leaf, 4, set(), 2, T0)
        self.assertEqual({e["source_id"] for e, _ in chosen}, {"fuvest"})
        self.assertFalse(missing)
        chosen, _ = ladder.pick(self.db, pool, self.leaf, 3, {("fuvest", 1)}, 1, T0)
        self.assertNotEqual((chosen[0][0]["source_id"], chosen[0][0]["number"]), ("fuvest", 1))


class LadderFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "h.sqlite3")
        self.leaf = leaves("Matemática", 1)[0]
        self.pool = [entry("fuvest", 1, "Matemática", self.leaf)]
        self.pool += [practice("ia", n, self.leaf, 1) for n in range(1, 7)]
        self.pool += [practice("ia", n, self.leaf, 2) for n in range(7, 11)]
        self.config = {"version": 1, "cursos": {"curso": {"nome": "x", "pesos": {"Matemática": 1}}}}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def answer(self, session_id, item_id, correct=True, reason=None, now=T0):
        return study.record_study_answer(self.db, session_id, item_id, "A" if correct else "B", reason, now,
                                         self.pool, confidence="sure")

    def test_error_opens_ladder_and_answers_climb_it_in_the_same_session(self):
        session = study.plan_today(self.db, self.pool, self.config, "curso", T0, new_per_day=1)
        first = session["items"][0]
        self.assertEqual(first["sourceId"], "fuvest")  # o plano regular prefere a FUVEST
        result = self.answer(session["id"], first["id"], correct=False, reason="unknown_content")
        self.assertFalse(result["ladder"]["generating"])
        payload = study.session_payload(self.db, session["id"])
        pending = [i for i in payload["items"] if not i["answered"] and i["reason"] == "ladder"]
        self.assertEqual([i["question"]["_dificuldade"] for i in pending], [1, 1])
        results = [self.answer(session["id"], item["id"]) for item in pending]
        self.assertEqual((results[-1]["ladder"]["change"], results[-1]["ladder"]["level"]), ("up", 2))
        payload = study.session_payload(self.db, session["id"])
        pending = [i for i in payload["items"] if not i["answered"]]
        self.assertTrue(pending and all(i["ladder"]["level"] == 2 and i["question"]["_dificuldade"] == 2
                                        for i in pending))
        # No dia seguinte a trilha tem lugar no plano, no nível em que parou.
        tomorrow = study.plan_today(self.db, self.pool, self.config, "curso", T0 + timedelta(days=1))
        ladder_items = [i for i in tomorrow["items"] if i["reason"] == "ladder"]
        self.assertEqual(len(ladder_items), ladder.PER_ROUND)
        self.assertTrue(all(i["ladder"]["contentId"] == self.leaf for i in ladder_items))
        extra = study.append_ladder_block(self.db, tomorrow["id"], self.pool, self.leaf, T0 + timedelta(days=1))
        self.assertGreater(len(extra["items"]), len(tomorrow["items"]))

    def test_missing_levels_and_diagnostic_errors(self):
        attempt = diagnostics.create_attempt(self.db, self.config, "curso", self.pool[:1], total=1, seed=1,
                                             provider=None, mode="static")
        diagnostics.record_response(self.db, attempt["id"], 1, "B", "unknown_content")
        study.sync_diagnostic_answers(self.db)
        self.assertEqual([state["contentId"] for state in ladder.active(self.db, "curso")], [self.leaf])
        self.assertEqual(ladder.missing_levels(self.db, self.pool[:1], "curso", T0), [(self.leaf, 1)])
        self.assertEqual(ladder.missing_levels(self.db, self.pool, "curso", T0), [])
        study.reset_learning_context(self.db)
        self.assertEqual(ladder.active(self.db, "curso"), [])


if __name__ == "__main__":
    unittest.main()
