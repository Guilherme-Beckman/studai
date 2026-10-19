import unittest
from pathlib import Path

from studai.features.diagnostics import courses, mastery, report
from studai.features.diagnostics import service as core

MATH_LEAF = "matematica.conjuntos-numericos-e-operacoes.operacoes-com-conjuntos"
MATH_AREA = "matematica.area.numeros-medidas-e-proporcionalidade"
MATH_CONTENT = "matematica.content.conjuntos-numericos-e-operacoes"
PHYSICS_LEAF = "fisica.atrito-e-plano-inclinado.atrito-estatico-e-cinetico"
PHYSICS_AREA = "fisica.area.mecanica"
PHYSICS_THERMO = "fisica.area.termologia-e-termodinamica"
WEIGHTS = {"weights": {"Matemática": 0.5, "Física": 0.5}, "non_assessable": {"Redação": 0.1667}}
PROFILE = {"nome": "Curso teste", "pesos": {"Matemática": 1, "Física": 1}}
CONFIG = Path(__file__).parent.parent / "diagnostico" / "cursos.json"


def answer(correct, principal, subject):
    return {"correct": correct, "subject": subject, "principal_id": principal,
            "related_ids": [], "answered_at": None}


class ReportTests(unittest.TestCase):
    def setUp(self):
        responses = ([answer(True, MATH_LEAF, "Matemática")] * 5
                     + [answer(False, PHYSICS_LEAF, "Física")] * 4)
        incidence = {MATH_AREA: 1.0, MATH_CONTENT: 1.0, MATH_LEAF: 0.5,
                     PHYSICS_AREA: 0.6, PHYSICS_THERMO: 0.4, PHYSICS_LEAF: 0.3}
        self.nodes = mastery.compute_nodes(responses, subject_weights=WEIGHTS["weights"], incidence=incidence)
        self.report = report.build_report(self.nodes, WEIGHTS, PROFILE)

    def test_score_and_coverage(self):
        self.assertEqual(self.report["estimated_score"],
                         mastery.weighted_score(self.nodes, WEIGHTS["weights"]))
        # Termologia (0.5 × 0.4) não tem evidência: cobertura = 1 − 0.2 / 1.0.
        self.assertAlmostEqual(self.report["coverage"], 0.8, places=3)

    def test_strengths_gaps_and_priorities(self):
        self.assertIn(MATH_AREA, [item["id"] for item in self.report["strengths"]])
        self.assertIn(PHYSICS_AREA, [item["id"] for item in self.report["gaps"]])
        self.assertTrue(all(item["level"] in ("area", "content") for item in
                            self.report["strengths"] + self.report["gaps"]))
        priorities = self.report["priorities"]
        self.assertLessEqual(len(priorities), 10)
        self.assertEqual(priorities[0]["id"], PHYSICS_AREA)
        self.assertNotIn("subject", {item["level"] for item in priorities})
        self.assertNotIn(MATH_AREA, [item["id"] for item in priorities])
        top = priorities[0]
        self.assertIn("Você acertou 0 de 4 questões envolvendo Mecânica", top["reason"])
        self.assertIn("60% das questões de Física", top["reason"])
        self.assertEqual(top["exit_criterion"], "acertar pelo menos 8 de 10 questões de Mecânica")
        leaf = next(item for item in priorities if item["level"] == "leaf")
        self.assertRegex(leaf["exit_criterion"], r"^acertar pelo menos 4 de 5 questões de ")

    def test_untested_high_priority_and_non_assessable(self):
        untested = self.report["untested_high_priority"]
        self.assertEqual([item["id"] for item in untested], [PHYSICS_THERMO])
        self.assertIn("ainda não foi testado", untested[0]["reason"])
        self.assertEqual(self.report["non_assessable"]["Redação"], 0.1667)
        self.assertIn("1/6", self.report["non_assessable"]["note"])

    def test_manual_weights_have_no_non_assessable_block(self):
        manual = courses.resolve_weights(PROFILE, [])
        self.assertEqual(report.build_report(self.nodes, manual, PROFILE)["non_assessable"], {})

    def test_empty_attempt(self):
        result = report.build_report({}, WEIGHTS, PROFILE)
        self.assertEqual(result["coverage"], 0.0)
        self.assertEqual(result["priorities"], [])
        self.assertEqual(result["estimated_score"], 0.5)

    def test_course_weight_summary_for_course_listing(self):
        config = core.load_course_config(CONFIG)
        profile = core.course_profile(config, "eng-comp-sao-carlos")
        pool = [{"subject": subject} for subject, count in
                {"Matemática": 45, "Física": 41, "Língua Portuguesa": 26, "Literatura": 32}.items()
                for _ in range(count)]
        summary = core.course_weight_summary(profile, pool)
        self.assertIsNone(summary["error"])
        self.assertEqual(max(summary["weights"], key=summary["weights"].get), "Matemática")
        self.assertAlmostEqual(summary["non_assessable"]["Redação"], 1 / 6, places=3)
        self.assertIsNotNone(core.course_weight_summary(profile, [])["error"])


if __name__ == "__main__":
    unittest.main()
