import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from _reports_support import COURSE_ID, EXAMS, build, config, load_pool
from studai.features.diagnostics import courses, mastery
from studai.features.diagnostics import service as diagnostics
from studai.features.questions import origin
from studai.features.questions import review
from studai.features.reports import service as reports
from studai.features.study import calendar
from studai.features.study import service as study

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)
LIST_ID = "lista-trigonometria-cursinho"


def add_list(processed: Path, db, kind="lista", name="Lista de trigonometria", difficulty=1) -> Path:
    """Copia a primeira prova FUVEST como uma lista de outra origem, com dificuldade na revisão."""
    source = next(processed.rglob("questoes.classificado.json")).parent
    target = processed / "web" / "lista"
    shutil.copytree(source, target)
    exam = target / "questoes.classificado.json"
    data = json.loads(exam.read_text(encoding="utf-8"))
    data.update(prova=LIST_ID, origem={"tipo": kind, "nome": name})
    exam.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    reviews = json.loads((target / "revisao.json").read_text(encoding="utf-8"))
    for record in reviews["questions"].values():
        record["difficulty"] = difficulty
    (target / "revisao.json").write_text(json.dumps(reviews), encoding="utf-8")
    for question in data["questoes"]:
        db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at) VALUES(?,?,?,?,?)",
                   (LIST_ID, question["numero"], "A", "lista.pdf", NOW.isoformat()))
    db.commit()
    return exam


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.processed, _, db_path = build(Path(self.temp.name))
        self.db = study.connect_db(db_path)
        self.fuvest = load_pool(self.db, self.processed)
        self.list_json = add_list(self.processed, self.db)
        self.pool = load_pool(self.db, self.processed)
        self.listed = [item for item in self.pool if item["source_id"] == LIST_ID]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_pool_items_carry_origin_and_difficulty(self):
        self.assertEqual(len(self.listed), 20)
        item = self.listed[0]
        self.assertEqual((item["origin"], item["origin_name"], item["difficulty"]), ("lista", "Lista de trigonometria", 1))
        self.assertEqual((item["question"]["_origem"], item["question"]["_dificuldade"]), ("lista", 1))
        old = next(item for item in self.pool if item["source_id"] == EXAMS[0])
        self.assertEqual((old["origin"], old["difficulty"]), ("fuvest", None))

    def test_fuvest_alone_defines_incidence_weights_simulados_and_diagnostic(self):
        self.assertEqual(mastery.node_incidence(self.pool), mastery.node_incidence(self.fuvest))
        self.assertEqual(courses.subject_counts(self.pool), courses.subject_counts(self.fuvest))
        suggestions = calendar.simulado_suggestions(self.db, self.pool, COURSE_ID, ["Matemática", "Física"])
        self.assertNotIn(LIST_ID, [item["sourceId"] for item in suggestions])
        with self.assertRaises(ValueError):
            reports.start_exam_session(self.db, config(), COURSE_ID, self.pool, LIST_ID)
        attempt = diagnostics.create_attempt(self.db, config(), COURSE_ID, self.pool, total=40, seed=3,
                                             provider=None, mode="static")
        sources = {row[0] for row in self.db.execute("SELECT source_id FROM attempt_questions WHERE attempt_id=?",
                                                     (attempt["id"],))}
        self.assertNotIn(LIST_ID, sources)

    def test_other_vestibular_can_be_a_full_exam_with_its_own_label(self):
        origin.write_exam_origin(self.list_json, {"tipo": "vestibular", "nome": "ENEM 2023 dia 2"})
        pool = load_pool(self.db, self.processed)
        session = reports.start_exam_session(self.db, config(), COURSE_ID, pool, LIST_ID)
        plan = json.loads(self.db.execute("SELECT plan_json FROM attempts WHERE id=?", (session["id"],)).fetchone()[0])
        self.assertEqual((plan["label"], plan["origin"]), ("ENEM 2023 dia 2", "vestibular"))

    def test_answers_outside_fuvest_weigh_less(self):
        def response(item, correct=True):
            question = item["question"]
            return mastery.response_from_row({"question_json": json.dumps(question), "subject": item["subject"],
                                              "is_correct": int(correct), "answered_at": NOW.isoformat()})
        listed, fuvest = response(self.listed[0]), response(self.fuvest[0])
        self.assertEqual((listed["evidence_factor"], fuvest["evidence_factor"]), (0.3, 1.0))
        weights = {"Matemática": 1.0}
        one = mastery.compute_nodes([listed], subject_weights=weights, now=NOW)
        other = mastery.compute_nodes([fuvest], subject_weights=weights, now=NOW)
        subject = "matematica"
        self.assertLess(one[subject]["evidence"], other[subject]["evidence"])
        # Treinamento: origem e dificuldade ficam na resposta e chegam ao domínio.
        cfg = {"version": 1, "cursos": {"curso": {"nome": "x", "pesos": {"Matemática": 1}}}}
        session = study.plan_today(self.db, self.listed[:1], cfg, "curso", NOW)
        study.record_study_answer(self.db, session["id"], session["items"][0]["id"], "A", None, NOW)
        daily = study.course_responses(self.db, "curso")[0]
        self.assertEqual((daily["origin"], daily["difficulty"], daily["evidence_factor"]), ("lista", 1, 0.3))

    def test_review_difficulty_is_kept_when_not_sent(self):
        saved = review.save_review(self.list_json, 1, {"status": "reviewed", "difficulty": 3})
        self.assertEqual(saved["difficulty"], 3)
        self.assertEqual(review.save_review(self.list_json, 1, {"status": "reviewed"})["difficulty"], 3)
        self.assertNotIn("difficulty", review.save_review(self.list_json, 1, {"status": "reviewed", "difficulty": None}))

    def test_origin_and_difficulty_validation(self):
        self.assertEqual(origin.validate_origin({"tipo": "fuvest"}), {"tipo": "fuvest", "nome": None})
        for value in ({"tipo": "lista"}, {"tipo": "outro", "nome": "x"}, "fuvest", {"tipo": "lista", "nome": "x" * 121}):
            with self.assertRaises(ValueError):
                origin.validate_origin(value)
        self.assertIsNone(origin.validate_difficulty(""))
        for value in (0, 4, "x", True):
            with self.assertRaises(ValueError):
                origin.validate_difficulty(value)
        self.assertEqual(origin.exam_origin({}), {"tipo": "fuvest", "nome": None})
        self.assertEqual(origin.evidence_factor(None, 1), 1.0)
        self.assertEqual(origin.evidence_factor("ia", None), 0.6)


if __name__ == "__main__":
    unittest.main()
