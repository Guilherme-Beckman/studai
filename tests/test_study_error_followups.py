import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.diagnostics import service as diagnostics
from studai.features.explanations import summaries
from studai.features.study import error_notebook
from studai.features.study import service as study
from studai.integrations.llm import DemoProvider
from test_study_scheduler import T0, entry, leaves


class ReasonTests(unittest.TestCase):
    def test_new_reasons_are_valid_and_mapped_to_actions(self):
        alternatives = {"A": {}, "B": {}}
        for reason in ("unknown_content", "concept_confusion", "calc_error", "misread", "trap", "time_pressure"):
            self.assertEqual(diagnostics.grade_answer("B", alternatives, "A", reason)[2], reason)
        with self.assertRaises(ValueError):
            diagnostics.grade_answer("B", alternatives, "A", "preguiça")
        self.assertEqual(set(diagnostics.ACTION_BY_REASON), diagnostics.REASONS)
        self.assertLessEqual(set(diagnostics.ACTION_BY_REASON.values()), diagnostics.ACTIONS)


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "historico.sqlite3")
        self.math = leaves("Matemática", 5)
        # Quatro questões no primeiro subconteúdo e uma em cada um dos outros.
        self.pool = [entry("prova-1", n, "Matemática", self.math[0]) for n in range(1, 5)]
        self.pool += [entry("prova-2", i + 1, "Matemática", c) for i, c in enumerate(self.math[1:])]
        self.config = {"version": 1, "cursos": {"curso": {"nome": "Curso", "pesos": {"Matemática": 1}}}}

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def session(self):
        return study.plan_today(self.db, self.pool, self.config, "curso", T0, new_per_day=5)

    def first_on_leaf(self, session):
        return next(item for item in session["items"] if item["contentId"] == self.math[0])

    def answer(self, session, item, reason):
        return study.record_study_answer(self.db, session["id"], item["id"], "B", reason, T0, self.pool)

    def test_unknown_content_inserts_practice_of_the_same_subcontent_right_after(self):
        session = self.session()
        item = self.first_on_leaf(session)
        result = self.answer(session, item, "unknown_content")
        self.assertEqual(len(result["followups"]), 2)
        self.assertEqual(result["summaryContentId"], self.math[0])
        items = study.session_payload(self.db, session["id"])["items"]
        position = [entry["id"] for entry in items].index(item["id"])
        inserted = items[position + 1:position + 3]
        self.assertEqual([entry["id"] for entry in inserted], result["followups"])
        self.assertTrue(all(entry["reason"] == "followup_practice" and entry["contentId"] == self.math[0]
                            for entry in inserted))
        self.assertNotIn((item["sourceId"], item["number"]), {(e["sourceId"], e["number"]) for e in inserted})

    def test_calc_error_inserts_one_question_three_positions_later(self):
        session = self.session()
        item = self.first_on_leaf(session)
        ids_before = [entry["id"] for entry in session["items"]]
        result = self.answer(session, item, "calc_error")
        self.assertEqual(len(result["followups"]), 1)
        ids = [entry["id"] for entry in study.session_payload(self.db, session["id"])["items"]]
        expected = min(ids_before.index(item["id"]) + 4, len(ids_before))
        self.assertEqual(ids.index(result["followups"][0]), expected)

    def test_reason_sets_when_the_question_comes_back(self):
        for reason, days in (("misread", 3), ("trap", 7), ("concept_confusion", 3)):
            with self.subTest(reason=reason):
                self.db.execute("DELETE FROM study_sessions")
                self.db.execute("DELETE FROM review_cards")
                self.db.commit()
                session = self.session()
                result = self.answer(session, self.first_on_leaf(session), reason)
                self.assertEqual(result["followups"], [])
                self.assertEqual(datetime.fromisoformat(result["questionReview"]), T0 + timedelta(days=days))

    def test_diagnostic_trap_is_delayed_on_sync(self):
        attempt = diagnostics.create_attempt(self.db, self.config, "curso", self.pool[:1], total=1, seed=1,
                                             provider=None, mode="static")
        diagnostics.record_response(self.db, attempt["id"], 1, "B", "trap")
        study.sync_diagnostic_answers(self.db)
        row = self.db.execute("SELECT due_at FROM review_cards WHERE kind='question'").fetchone()
        answered = self.db.execute("SELECT answered_at FROM attempt_questions").fetchone()[0]
        self.assertEqual(datetime.fromisoformat(row["due_at"]), datetime.fromisoformat(answered) + timedelta(days=7))

    def test_error_notebook_and_redo(self):
        session = self.session()
        item = self.first_on_leaf(session)
        study.record_study_answer(self.db, session["id"], item["id"], "B", "trap", T0, self.pool,
                                  note="confundi as unidades")
        other = next(entry for entry in session["items"] if entry["contentId"] == self.math[1])
        study.record_study_answer(self.db, session["id"], other["id"], "A", None, T0, self.pool, confidence="guess")
        notebook = error_notebook.list_errors(self.db, "curso")
        self.assertEqual(notebook["total"], 2)
        trap = next(row for row in notebook["items"] if row["reason"] == "trap")
        self.assertEqual((trap["note"], trap["misses"], trap["resolved"]), ("confundi as unidades", 1, False))
        self.assertEqual(trap["contentPath"], " → ".join(CONTENT_INDEX[self.math[0]]["path"]))
        self.assertIsNotNone(trap["dueAt"])
        self.assertEqual(notebook["byReason"], {"trap": 1, "guess": 1})
        self.assertEqual(error_notebook.list_errors(self.db, "curso", reason="trap")["total"], 1)
        # Refazer pelo caderno e acertar resolve a questão.
        redo = study.append_training_item(self.db, session["id"], self.pool, self.config, T0,
                                          question_refs=[(item["sourceId"], item["number"])])
        added = redo["items"][-1]
        self.assertEqual((added["reason"], added["sourceId"], added["number"]),
                         ("redo", item["sourceId"], item["number"]))
        study.record_study_answer(self.db, session["id"], added["id"], "A", None, T0, self.pool, confidence="sure")
        self.assertEqual(error_notebook.list_errors(self.db, "curso")["total"], 1)
        resolved = error_notebook.list_errors(self.db, "curso", status="resolved")["items"]
        self.assertEqual([(row["sourceId"], row["number"]) for row in resolved], [(item["sourceId"], item["number"])])
        with self.assertRaises(ValueError):
            error_notebook.list_errors(self.db, "curso", status="talvez")


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = study.connect_db(Path(self.temp.name) / "historico.sqlite3")
        self.leaf = leaves("Física", 1)[0]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_validation(self):
        ok = {"summary": "Texto.", "key_points": ["a", "b", "c"], "common_mistakes": []}
        self.assertEqual(summaries.validate_summary(ok)["key_points"], ["a", "b", "c"])
        for broken in ({"summary": ""}, {**ok, "key_points": ["a"]}, {**ok, "common_mistakes": ["x"] * 5}):
            with self.assertRaises(ValueError):
                summaries.validate_summary(broken)

    def test_generate_review_and_regenerate(self):
        self.assertTrue(summaries.needs_generation(summaries.get_summary(self.db, self.leaf)))
        record = summaries.generate_summary(self.db, self.leaf, DemoProvider(model="demo"))
        self.assertFalse(summaries.needs_generation(record))
        payload = summaries.public_payload(record)
        self.assertEqual(payload["name"], CONTENT_INDEX[self.leaf]["name"])
        self.assertEqual(len(payload["key_points"]), 3)
        rejected = summaries.review_summary(self.db, self.leaf, False, "faltou a fórmula")
        self.assertTrue(summaries.needs_generation(rejected))

        class Recorder(DemoProvider):
            prompts = []

            def complete(self, system, user, images=None):
                self.prompts.append(json.loads(user))
                return super().complete(system, user, images)

        recorder = Recorder(model="demo")
        summaries.generate_summary(self.db, self.leaf, recorder)
        self.assertEqual(recorder.prompts[0]["critica_do_aluno"], "faltou a fórmula")
        self.assertEqual(summaries.review_summary(self.db, self.leaf, True)["status"], "approved")


if __name__ == "__main__":
    unittest.main()
