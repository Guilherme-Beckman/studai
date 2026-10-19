import json
import tempfile
import unittest
from pathlib import Path

from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.diagnostics import service as core

MECHANICS = "fisica.area.mecanica"
AREAS = {
    "Matemática": ["matematica.area.numeros-medidas-e-proporcionalidade",
                   "matematica.area.algebra-funcoes-e-sequencias", "matematica.area.geometria-e-medidas"],
    "Física": [MECHANICS, "fisica.area.termologia-e-termodinamica", "fisica.area.ondas-e-optica"],
    "Química": ["quimica.area.materia-estrutura-e-propriedades"],
}
PER_AREA = 6


def leaves_under(area_id: str, count: int) -> list[str]:
    leaves = sorted(identity for identity, node in CONTENT_INDEX.items()
                    if area_id in node["path_ids"] and node["is_leaf"])
    return leaves[:count]


def build_pool() -> list[dict]:
    pool, number = [], 0
    for subject, areas in AREAS.items():
        for area in areas:
            for content_id in leaves_under(area, PER_AREA):
                number += 1
                question = {"numero": number, "enunciado": f"Q{number}",
                            "alternativas": {"A": {"texto": "a"}, "B": {"texto": "b"}},
                            "classificacao": {"materia": subject, "conteudo_principal_id": content_id,
                                              "conteudos_relacionados_ids": []}}
                pool.append({"source_id": "prova-sintetica", "source_file": "", "number": number,
                             "subject": subject, "topic": CONTENT_INDEX[content_id]["name"],
                             "question": question, "correct_answer": "A"})
    return pool


def knows(row: dict) -> bool:
    """Aluno simulado: domina Matemática e Química, erra toda a Mecânica, acerta o resto da Física."""
    content_id = json.loads(row["question_json"])["classificacao"]["conteudo_principal_id"]
    return MECHANICS not in CONTENT_INDEX[content_id]["path_ids"]


class AdaptiveDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = core.connect_db(Path(self.temp.name) / "diag.sqlite3")
        self.profile = {"nome": "Engenharia (teste)", "pesos": {"Matemática": 3, "Física": 3, "Química": 1}}
        self.config = {"version": 2, "cursos": {"eng": self.profile}}
        self.pool = build_pool()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def run_student(self, seed: int, total: int = 30, student=knows) -> tuple[str, list[dict], dict]:
        attempt = core.create_attempt(self.db, self.config, "eng", self.pool, total=total, seed=seed)
        result = {}
        guard = 0
        while True:
            pending = core.list_questions_for_attempt(self.db, attempt["id"], unanswered_only=True)
            if not pending:
                break
            self.assertEqual(len(pending), 1, "adaptativo mostra uma questão por vez")
            row = pending[0]
            answer = "A" if student(row) else "B"
            result = core.record_response(self.db, attempt["id"], row["ordinal"], answer, require_reason=False)
            guard += 1
            self.assertLess(guard, 60)
        return attempt["id"], core.list_questions_for_attempt(self.db, attempt["id"]), result

    def test_default_mode_is_adaptive_and_inserts_one_question(self):
        attempt = core.create_attempt(self.db, self.config, "eng", self.pool, seed=7)
        self.assertEqual(attempt["mode"], "adaptive")
        self.assertEqual(attempt["requested"], core.DEFAULT_ADAPTIVE_TOTAL)
        self.assertEqual(len(core.list_questions_for_attempt(self.db, attempt["id"])), 1)
        stored = core.get_attempt(self.db, attempt["id"])
        self.assertEqual(stored["mode"], "adaptive")
        self.assertEqual(stored["actual_count"], 1)
        self.assertEqual(stored["requested_count"], 30)
        plan = stored["plan"]
        for key in ("weights", "incidence", "budget", "candidates", "policy"):
            self.assertIn(key, plan)
        self.assertEqual(sum(plan["budget"]["by_subject"].values()), 30)
        self.assertEqual(plan["incidence"]["fisica.area.mecanica"], round(1 / 3, 4))

    def test_rejects_budget_above_maximum(self):
        with self.assertRaisesRegex(ValueError, "adaptativo"):
            core.create_attempt(self.db, self.config, "eng", self.pool, total=52, seed=1)

    def test_separated_student_stops_early_and_weak_area_is_a_priority(self):
        attempt_id, rows, last = self.run_student(seed=11)
        self.assertTrue(last["done"])
        self.assertIsNone(last["next_ordinal"])
        self.assertEqual(last["stop_reason"], "converged")
        self.assertLess(len(rows), 30)
        self.assertLess(len(rows), 51)
        stored = core.get_attempt(self.db, attempt_id)
        self.assertEqual(stored["actual_count"], len(rows))
        self.assertEqual(stored["plan"]["stop"]["reason"], "converged")
        self.assertNotIn("candidates", stored["plan"])
        # Cobertura primeiro: cada matéria com orçamento aparece antes de aprofundar.
        first_subjects = [row["subject"] for row in rows[:3]]
        self.assertEqual(sorted(first_subjects), ["Física", "Matemática", "Química"])
        metrics = core.calculate_metrics(self.db, attempt_id)
        nodes = metrics["nodes"]
        self.assertEqual(nodes[MECHANICS]["status"], "gap")
        self.assertGreaterEqual(nodes[MECHANICS]["attempts"], core.STOP_MIN_AREA_ANSWERS)
        self.assertGreater(nodes["matematica"]["mastery"], nodes["fisica"]["mastery"])
        report = metrics["report"]
        self.assertIn(MECHANICS, [node["id"] for node in report["gaps"]])
        self.assertEqual(report["priorities"][0]["subject"], "Física")
        self.assertIn(MECHANICS, report["priorities"][0]["path_ids"])
        self.assertIn("Mecânica", " ".join(item["name"] for item in report["priorities"]))
        # Matérias e conteúdos continuam no formato anterior.
        self.assertEqual(set(metrics["subjects"]), {"Matemática", "Física", "Química"})
        self.assertTrue(metrics["contents"])
        final = core.finalize_attempt(self.db, attempt_id, provider=None, include_analysis=False)
        self.assertEqual(final["analysis_status"], "completed")

    def test_same_seed_reproduces_the_sequence(self):
        _, first, _ = self.run_student(seed=5)
        _, second, _ = self.run_student(seed=5)
        key = lambda rows: [(row["source_id"], row["question_number"]) for row in rows]
        self.assertEqual(key(first), key(second))

    def test_mixed_student_runs_until_budget(self):
        flip = {"n": 0}

        def coin(row):
            flip["n"] += 1
            return flip["n"] % 2 == 0

        attempt_id, rows, last = self.run_student(seed=3, total=12, student=coin)
        self.assertEqual(len(rows), 12)
        self.assertEqual(last["stop_reason"], "budget")
        budget = core.get_attempt(self.db, attempt_id)["plan"]["budget"]["by_subject"]
        counts = {s: sum(1 for row in rows if row["subject"] == s) for s in budget}
        self.assertEqual(counts, budget)

    def test_next_question_is_pure_and_respects_subject_budget(self):
        plan = {"weights": {"weights": {"Matemática": 0.5, "Física": 0.5}},
                "incidence": {}, "budget": {"total": 2, "by_subject": {"Matemática": 1, "Física": 1}},
                "candidates": [core._candidate(item) for item in self.pool if item["subject"] != "Química"]}
        first, stop = core.next_question(plan, [], seed=1)
        self.assertIsNone(stop)
        again, _ = core.next_question(plan, [], seed=1)
        self.assertEqual((first["source_id"], first["number"]), (again["source_id"], again["number"]))
        row = {"source_id": first["source_id"], "question_number": first["number"], "subject": first["subject"],
               "question_json": json.dumps(first["question"]), "is_correct": 1, "answered_at": "2026-10-19T00:00:00+00:00"}
        second, _ = core.next_question(plan, [row], seed=1)
        self.assertNotEqual(second["subject"], first["subject"])
        done, reason = core.next_question(plan, [row, {**row, "subject": second["subject"],
                                                       "question_number": second["number"]}], seed=1)
        self.assertIsNone(done)
        self.assertEqual(reason, "budget")

    def test_static_mode_keeps_full_selection_and_reports_next_ordinal(self):
        attempt = core.create_attempt(self.db, self.config, "eng", self.pool, total=9, seed=2, mode="static")
        rows = core.list_questions_for_attempt(self.db, attempt["id"])
        self.assertEqual(len(rows), 9)
        self.assertEqual(core.get_attempt(self.db, attempt["id"])["mode"], "static")
        result = core.record_response(self.db, attempt["id"], 1, "A")
        self.assertEqual((result["next_ordinal"], result["done"]), (2, False))
        for row in rows[1:]:
            result = core.record_response(self.db, attempt["id"], row["ordinal"], None)
        self.assertEqual((result["next_ordinal"], result["done"]), (None, True))

    def test_metrics_fallback_for_attempts_without_plan_weights(self):
        attempt = core.create_attempt(self.db, self.config, "eng", self.pool, total=6, seed=2, mode="static")
        self.db.execute("UPDATE attempts SET plan_json=? WHERE id=?", (json.dumps({"topics": {}}), attempt["id"]))
        self.db.commit()
        for row in core.list_questions_for_attempt(self.db, attempt["id"]):
            core.record_response(self.db, attempt["id"], row["ordinal"], "A")
        metrics = core.calculate_metrics(self.db, attempt["id"])
        self.assertAlmostEqual(sum(metrics["course_weights"].values()), 1.0, places=3)
        self.assertTrue(any(node["level"] == "area" for node in metrics["nodes"].values()))
        self.assertEqual(metrics["report"]["non_assessable"], {})


if __name__ == "__main__":
    unittest.main()
