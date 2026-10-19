import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _reports_support import COURSE_ID, EXAMS, build, config, load_pool
from studai.features.diagnostics import service as diagnostics
from studai.features.reports import service as reports
from studai.tools.simulate_student import simulate

FIXTURES = Path(__file__).parent / "fixtures"


class ReportsEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = diagnostics.connect_db(db_path)
        self.config = config()
        self.pool = load_pool(self.db, self.processed)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def simulate(self, **kwargs):
        options = {"sessions": 6, "exams": 2, "profile": "mat-forte-fis-fraca", "seed": 7,
                   "end": date(2026, 10, 19), "per_diagnostic": 16}
        return simulate(self.db, self.config, COURSE_ID, self.pool, **{**options, **kwargs})

    def test_pool_fixture_is_reviewed_and_keyed(self):
        self.assertEqual(len(self.pool), 40)
        self.assertTrue(all(item["question"]["classificacao"].get("conteudo_principal_id") for item in self.pool))

    def test_improving_student_series_and_timeline_kinds(self):
        created = self.simulate(sessions=8, profile="iniciante", per_diagnostic=20)
        payload = reports.overview(self.db, self.config, COURSE_ID, self.pool)
        timeline = payload["timeline"]
        self.assertEqual([point["id"] for point in timeline], [item["id"] for item in created])
        self.assertEqual(sum(point["kind"] == "exam" for point in timeline), 2)
        self.assertEqual(sum(point["kind"] == "diagnostic" for point in timeline), 6)
        self.assertEqual({point["label"] for point in timeline if point["kind"] == "exam"},
                         {"Prova FUVEST 2024 (K)", "Prova FUVEST 2025 (V1)"})
        self.assertEqual({point["label"] for point in timeline if point["kind"] == "diagnostic"}, {"Diagnóstico"})
        self.assertEqual([point["at"] for point in timeline], sorted(point["at"] for point in timeline))
        for subject in ("matematica", "fisica"):
            series = payload["nodes"][subject]["series"]
            self.assertEqual(len(series), len(timeline))
            self.assertTrue(all(value is not None for value in series))
            self.assertGreater(series[-1], series[0], subject)
            drops = [b - a for a, b in zip(series, series[1:]) if b < a]
            self.assertTrue(all(drop > -0.12 for drop in drops), (subject, series))
        self.assertGreater(timeline[-1]["estimated_score"], timeline[0]["estimated_score"])
        kpis = payload["kpis"]
        self.assertEqual(kpis["sessions"], 8)
        self.assertEqual(kpis["answered"], sum(point["answered"] for point in timeline))
        self.assertEqual(kpis["estimated_score"], timeline[-1]["estimated_score"])
        self.assertAlmostEqual(kpis["estimated_score_delta"],
                               timeline[-1]["estimated_score"] - timeline[-2]["estimated_score"], places=3)
        self.assertEqual(kpis["last_activity"], timeline[-1]["at"])
        self.assertGreater(kpis["coverage"], 0.9)

    def test_empty_history_has_untested_nodes_and_zero_kpis(self):
        payload = reports.overview(self.db, self.config, COURSE_ID, self.pool)
        self.assertEqual(payload["timeline"], [])
        self.assertEqual(payload["kpis"], {"estimated_score": 0.0, "estimated_score_delta": None, "answered": 0,
                                           "sessions": 0, "coverage": 0.0, "strong": 0, "gaps": 0,
                                           "last_activity": None})
        self.assertEqual(payload["roots"], ["matematica", "fisica"])
        self.assertTrue(payload["nodes"])
        self.assertTrue(all(node["status"] == "untested" and node["series"] == [] and node["delta"] is None
                            for node in payload["nodes"].values()))
        self.assertIsNone(payload["latest_report"])
        self.assertLessEqual(len(payload["priorities"]), 10)

    def test_payload_keys_match_contract_fixtures(self):
        self.simulate()
        payload = reports.overview(self.db, self.config, COURSE_ID, self.pool)
        fixture = json.loads((FIXTURES / "reports_overview.json").read_text(encoding="utf-8"))
        self.assertEqual(set(payload), set(fixture))
        self.assertEqual(set(payload["kpis"]), set(fixture["kpis"]))
        self.assertEqual(set(payload["course"]), set(fixture["course"]))
        self.assertEqual(set(payload["timeline"][0]), set(fixture["timeline"][0]))
        reference_node = next(iter(fixture["nodes"].values()))
        for node in payload["nodes"].values():
            self.assertEqual(set(node), set(reference_node))
            for child in node["children"]:
                self.assertEqual(payload["nodes"][child]["parent_id"], node["id"])
        for identity in payload["priorities"]:
            self.assertNotEqual(payload["nodes"][identity]["level"], "subject")
        priorities = [payload["nodes"][identity]["priority"] for identity in payload["priorities"]]
        self.assertEqual(priorities, sorted(priorities, reverse=True))
        node_fixture = json.loads((FIXTURES / "reports_node.json").read_text(encoding="utf-8"))
        detail = reports.node_detail(self.db, self.config, COURSE_ID, "fisica.area.mecanica", self.pool)
        self.assertEqual(set(detail), set(node_fixture))
        self.assertEqual(set(detail["node"]), set(node_fixture["node"]))
        self.assertEqual(set(detail["series"][0]), set(node_fixture["series"][0]))
        self.assertEqual(set(detail["recent_answers"][0]), set(node_fixture["recent_answers"][0]))
        self.assertEqual(len(detail["series"]), len(payload["timeline"]))
        self.assertLessEqual(len(detail["recent_answers"]), 20)
        times = [answer["at"] for answer in detail["recent_answers"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual([child["id"] for child in detail["children"]],
                         payload["nodes"]["fisica.area.mecanica"]["children"])

    def test_unknown_node_and_course_are_not_found(self):
        with self.assertRaises(FileNotFoundError):
            reports.node_detail(self.db, self.config, COURSE_ID, "nao.existe", self.pool)
        with self.assertRaises(FileNotFoundError):
            reports.overview(self.db, self.config, "curso-inexistente", self.pool)

    def test_exam_session_lifecycle_and_reason_is_optional(self):
        session = reports.start_exam_session(self.db, self.config, COURSE_ID, self.pool, EXAMS[0])
        self.assertEqual([q["number"] for q in session["questions"]], list(range(1, 21)))
        self.assertEqual([q["ordinal"] for q in session["questions"]], list(range(1, 21)))
        attempt = diagnostics.get_attempt(self.db, session["id"])
        self.assertEqual((attempt["mode"], attempt["status"], attempt["plan"]["exam"]),
                         ("exam", "in_progress", EXAMS[0]))
        rows = diagnostics.list_questions_for_attempt(self.db, session["id"])
        wrong = next(letter for letter in "ABCDE" if letter != rows[0]["correct_answer"])
        result = reports.answer_exam_question(self.db, session["id"], 1, wrong)
        self.assertEqual((result["correct"], result["reason"]), (False, None))
        reports.answer_exam_question(self.db, session["id"], 2, rows[1]["correct_answer"])
        with self.assertRaises(ValueError):
            reports.answer_exam_question(self.db, session["id"], 2, rows[1]["correct_answer"])
        finished = reports.finish_exam_session(self.db, session["id"])
        self.assertEqual(finished["metrics"]["answered"], 2)
        self.assertEqual(diagnostics.get_attempt(self.db, session["id"])["status"], "completed")
        with self.assertRaises(ValueError):
            reports.answer_exam_question(self.db, session["id"], 3, "A")
        payload = reports.overview(self.db, self.config, COURSE_ID, self.pool)
        self.assertEqual([(p["kind"], p["answered"], p["accuracy"]) for p in payload["timeline"]],
                         [("exam", 2, 0.5)])
        self.assertIsNone(payload["latest_report"])

    def test_exam_endpoints_reject_diagnostic_attempts(self):
        attempt = diagnostics.create_attempt(self.db, self.config, COURSE_ID, self.pool, total=4, seed=1,
                                             provider=None, mode="static")
        with self.assertRaises(ValueError):
            reports.answer_exam_question(self.db, attempt["id"], 1, "A")
        with self.assertRaises(ValueError):
            reports.finish_exam_session(self.db, attempt["id"])
        with self.assertRaises(FileNotFoundError):
            reports.finish_exam_session(self.db, "inexistente")
        with self.assertRaises(ValueError):
            reports.start_exam_session(self.db, self.config, COURSE_ID, self.pool, "prova-sem-questoes")

    def test_latest_report_comes_from_last_finished_diagnostic(self):
        self.simulate(sessions=3, exams=1)
        last = self.db.execute("SELECT id, metrics_json FROM attempts WHERE mode!='exam' "
                               "ORDER BY created_at DESC LIMIT 1").fetchone()
        metrics = json.loads(last["metrics_json"])
        metrics["report"] = {"summary": "ok"}
        with self.db:
            self.db.execute("UPDATE attempts SET metrics_json=? WHERE id=?", (json.dumps(metrics), last["id"]))
        self.assertEqual(reports.overview(self.db, self.config, COURSE_ID, self.pool)["latest_report"],
                         {"summary": "ok"})

    def test_exam_label(self):
        self.assertEqual(reports.exam_label("fuvest2026-fase1-prova-V1-93b417ad6ea7"), "Prova FUVEST 2026 (V1)")
        self.assertEqual(reports.exam_label("x", "fuvest2024_primeira_fase_prova_K"), "Prova FUVEST 2024 (K)")
        self.assertEqual(reports.exam_label("simulado-local"), "Prova completa")


if __name__ == "__main__":
    unittest.main()
