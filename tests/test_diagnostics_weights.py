import unittest
from pathlib import Path

from studai.features.diagnostics import courses
from studai.features.diagnostics import service as core

CONFIG = Path(__file__).parent.parent / "diagnostico" / "cursos.json"
COUNTS = {"Matemática": 45, "Física": 41, "Língua Portuguesa": 26, "Literatura": 32, "Geografia": 45,
          "Química": 39, "Biologia": 38, "História": 35, "Inglês": 24, "Sociologia": 16,
          "Filosofia": 10, "Arte": 7, "Educação Física": 1}


class CourseWeightsTest(unittest.TestCase):
    def setUp(self):
        self.config = core.load_course_config(CONFIG)

    def test_eng_comp_sao_carlos_prioritises_math_and_physics(self):
        profile = core.course_profile(self.config, "eng-comp-sao-carlos")
        result = courses.derive_subject_weights(profile, COUNTS)
        weights = result["weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=2)
        top_two = sorted(weights, key=weights.get, reverse=True)[:2]
        self.assertEqual(set(top_two), {"Matemática", "Física"})
        self.assertGreater(weights["Matemática"], 3 * weights["Química"])
        self.assertAlmostEqual(result["non_assessable"]["Redação"], 1 / 6, places=3)
        self.assertEqual(result["components"]["Física"]["d2"], 0.5)

    def test_poli_splits_day_two_between_three_subjects(self):
        profile = core.course_profile(self.config, "eng-comp-poli")
        result = courses.derive_subject_weights(profile, COUNTS)
        self.assertAlmostEqual(result["components"]["Química"]["d2"], 1 / 3, places=3)

    def test_manual_weights_without_formula_still_work(self):
        profile = {"nome": "Teste", "pesos": {"Matemática": 3, "Física": 1}}
        result = courses.resolve_weights(profile, [])
        self.assertEqual(result["weights"], {"Matemática": 0.75, "Física": 0.25})

    def test_requires_day_two_subjects(self):
        profile = {"nome": "x", "formula": "fuvest-2027", "pesos": {"Matemática": 1}, "fase2": {}}
        with self.assertRaises(ValueError):
            courses.derive_subject_weights(profile, {"Matemática": 1})


if __name__ == "__main__":
    unittest.main()
