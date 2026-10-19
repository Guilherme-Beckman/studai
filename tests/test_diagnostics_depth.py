import unittest
from datetime import datetime, timedelta, timezone

from studai.features.classification.taxonomy import CONTENT_INDEX
from studai.features.diagnostics import depth

AREA = "matematica.area.trigonometria"
TRIANGLE = "matematica.content.trigonometria-no-triangulo"
CYCLE = "matematica.content.ciclo-trigonometrico-e-funcoes-trigonometricas"
SINES = "matematica.trigonometria-no-triangulo.lei-dos-senos"
COSINES = "matematica.trigonometria-no-triangulo.lei-dos-cossenos"
RADIANS = "matematica.ciclo-trigonometrico-e-funcoes-trigonometricas.radianos-e-arcos-no-ciclo"
WEIGHTS = {"Matemática": 3, "Física": 1}
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def answer(principal, correct=True, when=None):
    return {"correct": correct, "subject": "Matemática", "principal_id": principal,
            "related_ids": [], "answered_at": when}


def run(responses, **kwargs):
    return depth.compute_depth(responses, subject_weights=WEIGHTS, now=NOW, **kwargs)


class DepthTest(unittest.TestCase):
    def test_lists_the_whole_live_tree_untested(self):
        nodes = run([])
        live = [i for i, n in CONTENT_INDEX.items() if n["subject"] in WEIGHTS and i in depth.LIVE_CHILDREN]
        self.assertTrue(set(live) <= set(nodes))
        self.assertIn("matematica", nodes)
        self.assertTrue(all(n["depth"] == 0 and n["coverage"] == 0 for n in nodes.values()))
        self.assertTrue(all(n["status"] == "untested" for n in nodes.values()))

    def test_leaf_depth_is_mastery_times_confidence(self):
        leaf = run([answer(SINES)] * 2)[SINES]
        self.assertAlmostEqual(leaf["depth"], round(leaf["mastery"] * 2 / (2 + depth.EVIDENCE_HALF_CREDIT), 4))
        self.assertEqual(leaf["coverage"], 1.0)

    def test_unpracticed_children_dilute_the_parent(self):
        nodes = run([answer(SINES)] * 4)
        content = nodes[TRIANGLE]
        children = depth.LIVE_CHILDREN[TRIANGLE]
        self.assertEqual(content["children_total"], len(children))
        self.assertEqual(content["children_practiced"], 1)
        self.assertAlmostEqual(content["coverage"], 1 / len(children), places=3)
        self.assertAlmostEqual(content["depth"], nodes[SINES]["depth"] / len(children), places=3)
        # Beta do conteúdo já é alto; a profundidade mostra que só um subconteúdo foi estudado.
        self.assertGreater(content["mastery"], 0.8)
        self.assertLess(content["depth"], 0.2)

    def test_breadth_reinforces_the_area_above(self):
        """Trigonometria → X → Y e Trigonometria → Z → W reforçam Trigonometria."""
        narrow = run([answer(SINES)] * 4)
        broad = run([answer(SINES)] * 2 + [answer(RADIANS)] * 2)
        self.assertEqual(broad[AREA]["attempts"], narrow[AREA]["attempts"])
        self.assertGreater(broad[AREA]["depth"], narrow[AREA]["depth"])
        self.assertGreater(broad[AREA]["coverage"], narrow[AREA]["coverage"])
        self.assertEqual(broad[AREA]["children_practiced"], 2)
        self.assertGreater(broad["matematica"]["depth"], 0)

    def test_siblings_deepen_their_content(self):
        one = run([answer(SINES)] * 4)[TRIANGLE]
        two = run([answer(SINES)] * 2 + [answer(COSINES)] * 2)[TRIANGLE]
        self.assertGreater(two["depth"], one["depth"])

    def test_wrong_answers_give_less_depth(self):
        right = run([answer(SINES)] * 3)[TRIANGLE]
        wrong = run([answer(SINES, correct=False)] * 3)[TRIANGLE]
        self.assertGreater(right["depth"], wrong["depth"])

    def test_incidence_weights_children_with_floor(self):
        incidence = {SINES: 0.3}  # os outros filhos nunca caíram
        frequent = run([answer(SINES)] * 3, incidence=incidence)[TRIANGLE]
        rare = run([answer(COSINES)] * 3, incidence=incidence)[TRIANGLE]
        self.assertGreater(frequent["coverage"], rare["coverage"])
        self.assertGreater(rare["coverage"], 0)

    def test_answers_labelled_on_the_content_itself_count(self):
        nodes = run([answer(TRIANGLE)] * 3)
        self.assertGreater(nodes[TRIANGLE]["depth"], 0)
        self.assertEqual(nodes[TRIANGLE]["coverage"], 0)

    def test_old_answers_weigh_less(self):
        old = (NOW - timedelta(days=120)).isoformat()
        aged = run([answer(SINES, when=old)] * 3, half_life_days=30)[SINES]
        fresh = run([answer(SINES, when=NOW.isoformat())] * 3, half_life_days=30)[SINES]
        self.assertGreater(fresh["depth"], aged["depth"])

    def test_content_ancestor(self):
        self.assertEqual(depth.content_ancestor(SINES), TRIANGLE)
        self.assertEqual(depth.content_ancestor(TRIANGLE), TRIANGLE)
        self.assertEqual(depth.content_ancestor(AREA), AREA)


if __name__ == "__main__":
    unittest.main()
