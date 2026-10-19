import unittest
from datetime import datetime, timedelta, timezone

from studai.features.diagnostics import mastery

LEAF = "matematica.conjuntos-numericos-e-operacoes.operacoes-com-conjuntos"
SIBLING = "matematica.conjuntos-numericos-e-operacoes.irracionais-e-reais"
CONTENT = "matematica.content.conjuntos-numericos-e-operacoes"
AREA = "matematica.area.numeros-medidas-e-proporcionalidade"
PHYSICS = "fisica.atrito-e-plano-inclinado.atrito-estatico-e-cinetico"
WEIGHTS = {"Matemática": 3, "Física": 1}


def answer(correct, principal=LEAF, subject="Matemática", related=(), when=None):
    return {"correct": correct, "subject": subject, "principal_id": principal,
            "related_ids": list(related), "answered_at": when}


class MasteryTest(unittest.TestCase):
    def test_rolls_up_to_every_level(self):
        nodes = mastery.compute_nodes([answer(True), answer(False)], subject_weights=WEIGHTS)
        levels = {nodes[i]["level"] for i in ("matematica", AREA, CONTENT, LEAF)}
        self.assertEqual(levels, {"subject", "area", "content", "leaf"})
        self.assertEqual(nodes[LEAF]["parent_id"], CONTENT)
        self.assertEqual(nodes["matematica"]["attempts"], 2)
        self.assertEqual(nodes["matematica"]["mastery"], 0.5)

    def test_related_content_counts_half(self):
        nodes = mastery.compute_nodes([answer(False, related=[SIBLING])], subject_weights=WEIGHTS)
        self.assertEqual(nodes[LEAF]["evidence"], 1.0)
        self.assertEqual(nodes[SIBLING]["evidence"], 0.5)
        self.assertEqual(nodes[CONTENT]["evidence"], 1.0)

    def test_status_thresholds(self):
        strong = mastery.compute_nodes([answer(True)] * 3, subject_weights=WEIGHTS)[LEAF]
        gap = mastery.compute_nodes([answer(False)] * 2, subject_weights=WEIGHTS)[LEAF]
        unsure = mastery.compute_nodes([answer(True), answer(False)], subject_weights=WEIGHTS)[LEAF]
        self.assertEqual((strong["status"], gap["status"], unsure["status"]), ("strong", "gap", "uncertain"))

    def test_untested_nodes_with_incidence_are_listed(self):
        nodes = mastery.compute_nodes([], subject_weights=WEIGHTS, incidence={AREA: 0.4})
        self.assertEqual(nodes[AREA]["status"], "untested")
        self.assertGreater(nodes[AREA]["priority"], 0)

    def test_priority_follows_course_weight(self):
        responses = [answer(False), answer(False, principal=PHYSICS, subject="Física")]
        incidence = {LEAF: 0.1, PHYSICS: 0.1}
        nodes = mastery.compute_nodes(responses, subject_weights=WEIGHTS, incidence=incidence)
        self.assertGreater(nodes[LEAF]["priority"], nodes[PHYSICS]["priority"])

    def test_recency_decay_favours_recent_answers(self):
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        old = (now - timedelta(days=90)).isoformat()
        recent = now.isoformat()
        responses = [answer(False, when=old)] * 4 + [answer(True, when=recent)] * 2
        decayed = mastery.compute_nodes(responses, subject_weights=WEIGHTS, half_life_days=30, now=now)
        flat = mastery.compute_nodes(responses, subject_weights=WEIGHTS)
        self.assertGreater(decayed[LEAF]["mastery"], flat[LEAF]["mastery"])

    def test_incidence_from_pool(self):
        pool = [{"subject": "Matemática", "question": {"classificacao": {"conteudo_principal_id": LEAF}}},
                {"subject": "Matemática", "question": {"classificacao": {"conteudo_principal_id": SIBLING}}}]
        incidence = mastery.node_incidence(pool)
        self.assertEqual(incidence["matematica"], 1.0)
        self.assertEqual(incidence[CONTENT], 1.0)
        self.assertEqual(incidence[LEAF], 0.5)

    def test_weighted_score_uses_prior_for_missing_subjects(self):
        nodes = mastery.compute_nodes([answer(True)] * 8, subject_weights=WEIGHTS)
        score = mastery.weighted_score(nodes, WEIGHTS)
        self.assertAlmostEqual(score, 0.75 * nodes["matematica"]["mastery"] + 0.25 * 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
