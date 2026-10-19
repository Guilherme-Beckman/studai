import json
import tempfile
import unittest
from pathlib import Path

from studai.features.classification.taxonomy import CONTENT_TAXONOMY_VERSION
from studai.features.diagnostics import service as core
from studai.features.questions import review

MATH_LEAF = "matematica.conjuntos-numericos-e-operacoes.operacoes-com-conjuntos"
PHYSICS_LEAF = "fisica.atrito-e-plano-inclinado.atrito-estatico-e-cinetico"


def question(number: int) -> dict:
    return {"numero": number, "enunciado": f"Enunciado {number}",
            "alternativas": {"A": {"texto": "a"}, "B": {"texto": "b"}},
            "classificacao": {"materia": "Matemática", "assunto": "IA disse outra coisa"}}


def reviewed(subject="Matemática", content_id=MATH_LEAF, version=CONTENT_TAXONOMY_VERSION, **extra) -> dict:
    return {"status": "reviewed", "subject": subject, "content_id": content_id,
            "taxonomy_version": version, **extra}


class ReviewedPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "processados"
        exam_dir = self.root / "web" / "prova"
        exam_dir.mkdir(parents=True)
        self.exam = exam_dir / "questoes.classificado.json"
        self.exam.write_text(json.dumps({"prova": "prova-x", "questoes": [question(n) for n in range(1, 8)]}),
                             encoding="utf-8")
        review.save_reviews_bulk(self.exam, {
            1: reviewed(related_content_ids=["matematica.conjuntos-numericos-e-operacoes.irracionais-e-reais"]),
            # 2: sem revisão (pendente)
            3: reviewed(version="versao-antiga"),
            4: reviewed(content_id=PHYSICS_LEAF),  # conteúdo de outra matéria
            5: {"status": "unusable"},
            6: {"status": "bad_crop"},
            7: reviewed(subject="Física", content_id=PHYSICS_LEAF),
        })
        self.db = core.connect_db(Path(self.temp.name) / "diag.sqlite3")
        for number in range(1, 8):
            self.db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at) "
                            "VALUES(?,?,?,?,?)", ("prova-x", number, "A", "g.pdf", "now"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_only_reviewed_questions_with_valid_content_enter_the_pool(self):
        pool, excluded = core.load_question_pool(self.root, self.db, ["Matemática", "Física"])
        self.assertEqual(sorted(item["number"] for item in pool), [1, 7])
        self.assertEqual(excluded, {"pending_review": 1, "invalid_content": 2, "unusable": 1, "bad_crop": 1})
        first = next(item for item in pool if item["number"] == 1)
        classification = first["question"]["classificacao"]
        self.assertEqual(classification["conteudo_principal_id"], MATH_LEAF)
        self.assertEqual(classification["conteudos_relacionados_ids"],
                         ["matematica.conjuntos-numericos-e-operacoes.irracionais-e-reais"])
        self.assertIn("→", first["topic"])

    def test_subject_outside_course_is_unclassified(self):
        pool, excluded = core.load_question_pool(self.root, self.db, ["Matemática"])
        self.assertEqual([item["number"] for item in pool], [1])
        self.assertEqual(excluded["unclassified"], 1)

    def test_pool_and_reviewed_examples_share_the_same_criterion(self):
        pool, _ = core.load_question_pool(self.root, self.db, ["Matemática", "Física"])
        examples = review.reviewed_examples(self.root)
        self.assertEqual(sorted(int(row["id"].rsplit("q", 1)[1]) for row in examples),
                         sorted(item["number"] for item in pool))

    def test_diagnostic_ready_reasons(self):
        self.assertIsNone(review.diagnostic_ready(reviewed()))
        self.assertEqual(review.diagnostic_ready(None), "pending_review")
        self.assertEqual(review.diagnostic_ready({"status": "unusable"}), "unusable")
        self.assertEqual(review.diagnostic_ready(reviewed(subject="")), "missing_subject")
        self.assertEqual(review.diagnostic_ready(reviewed(content_id=None)), "invalid_content")
        self.assertEqual(review.diagnostic_ready(reviewed(), "Física"), "invalid_content")
        intermediate = "matematica.area.numeros-medidas-e-proporcionalidade"
        self.assertEqual(review.diagnostic_ready(reviewed(content_id=intermediate)), "invalid_content")

    def test_missing_answer_key_is_still_excluded(self):
        self.db.execute("DELETE FROM answer_keys WHERE question_number=1")
        self.db.commit()
        pool, excluded = core.load_question_pool(self.root, self.db, ["Matemática", "Física"])
        self.assertEqual([item["number"] for item in pool], [7])
        self.assertEqual(excluded["no_valid_answer_key"], 1)


if __name__ == "__main__":
    unittest.main()
