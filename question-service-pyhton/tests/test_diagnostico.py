import json
import tempfile
import unittest
from pathlib import Path

import pymupdf

import diagnostico_core as core


class ScriptedProvider:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, system, user, images=None):
        if not self.responses:
            raise AssertionError("resposta inesperada solicitada ao provider")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def question(number, subject, correct="A"):
    return {"numero": number, "enunciado": f"Enunciado {number}", "nota": "",
            "alternativas": {"A": {"texto": "opção A"}, "B": {"texto": "opção B"}},
            "resposta": None, "classificacao": {"materia": subject, "assunto": "Tópico 1"},
            "imagens_completas": [], "revisar": []}


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = core.connect_db(self.root / "diagnostico.sqlite3")
        self.profile = {"nome": "Curso de teste", "pesos": {"Matemática": 1, "Física": 1},
                       "disciplinas_especificas": ["Matemática"]}
        self.config = {"version": 1, "cursos": {"curso-teste": self.profile}}
        self.pool = []
        for subject, start in (("Matemática", 1), ("Física", 11)):
            for i in range(3):
                q = question(start + i, subject)
                self.pool.append({"source_id": "simulado-teste", "source_file": "", "number": start + i,
                                  "subject": subject, "topic": "Tópico 1", "question": q,
                                  "correct_answer": "A"})

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def make_attempt(self, total=6, provider=None):
        provider = provider or ScriptedProvider(json.dumps({"topicos": {
            "Matemática": ["Tópico 1"], "Física": ["Tópico 1"]}}))
        return core.create_attempt(self.db, self.config, "curso-teste", self.pool,
                                   total=total, seed=42, provider=provider)

    def test_weighted_allocation_respects_caps_and_redistributes(self):
        allocated = core.allocate_counts({"Base": 80, "Física": 20, "Matemática": 20},
                                         {"Base": 1, "Física": 3, "Matemática": 3}, 70)
        self.assertEqual(sum(allocated.values()), 70)
        self.assertLessEqual(allocated["Base"], 80)
        self.assertLessEqual(allocated["Física"], 20)
        self.assertLessEqual(allocated["Matemática"], 20)
        self.assertEqual(core.allocate_counts({"A": 1, "B": 20}, {"A": 1, "B": 1}, 10),
                         {"A": 1, "B": 9})

    def test_aliases_and_course_profile_weights(self):
        self.assertEqual(core.canonical_subject("Arte", ["Artes"]), "Artes")
        self.assertEqual(core.canonical_subject("Português", ["Língua Portuguesa"]), "Língua Portuguesa")
        self.assertIn("Educação Física", __import__("classifier").DEFAULT_TAXONOMY)

    def test_default_course_profile_has_expected_weighted_coverage(self):
        config = core.load_course_config(Path(__file__).parent.parent / "diagnostico" / "cursos.json")
        profile = core.course_profile(config, "eng-comp-sao-carlos")
        self.assertEqual(profile["pesos"]["Física"], 3)
        self.assertEqual(profile["pesos"]["Matemática"], 3)
        self.assertEqual(len(profile["pesos"]), 13)
        counts = core.allocate_counts({s: 100 for s in profile["pesos"]}, profile["pesos"], 51)
        self.assertEqual(counts["Física"], 9)
        self.assertEqual(counts["Matemática"], 9)
        self.assertTrue(all(v == 3 for s, v in counts.items() if s not in ("Física", "Matemática")))

    def test_seed_reproduces_question_selection(self):
        provider = ScriptedProvider(json.dumps({"topicos": {
            "Matemática": ["Tópico 1"], "Física": ["Tópico 1"]}}))
        first = self.make_attempt(provider=provider)
        selected_first = [(r["source_id"], r["question_number"]) for r in core.list_questions_for_attempt(self.db, first["id"])]
        provider2 = ScriptedProvider(json.dumps({"topicos": {
            "Matemática": ["Tópico 1"], "Física": ["Tópico 1"]}}))
        second = self.make_attempt(provider=provider2)
        selected_second = [(r["source_id"], r["question_number"]) for r in core.list_questions_for_attempt(self.db, second["id"])]
        self.assertEqual(selected_first, selected_second)

    def test_planner_retries_invalid_reply_then_uses_valid_correction(self):
        topics = {"Matemática": ["Tópico 1"], "Física": ["Tópico 1"]}
        corrected = json.dumps({"topicos": topics})
        provider = ScriptedProvider("not json", corrected)
        selected, metadata = core.plan_topics(provider, self.profile, topics)
        self.assertEqual(selected, topics)
        self.assertEqual(metadata["status"], "llm")
        self.assertEqual(metadata["attempts"], 2)

    def test_planner_invalid_after_correction_falls_back_without_question_ids(self):
        topics = {"Matemática": ["Tópico 1"], "Física": ["Tópico 1"]}
        provider = ScriptedProvider("bad", "also bad")
        selected, metadata = core.plan_topics(provider, self.profile, topics)
        self.assertEqual(selected, topics)
        self.assertEqual(metadata["status"], "deterministic_fallback")

    def test_interactive_responses_metrics_analysis_and_blank_persistence(self):
        planner = json.dumps({"topicos": {"Matemática": ["Tópico 1"], "Física": ["Tópico 1"]}})
        analysis = json.dumps({"summary": "Comece pelos erros de conteúdo.", "steps": [
            {"subject": "Matemática", "action": "calculation_practice", "reason_code": "calc_error",
             "rationale": "O motivo informado foi erro de cálculo.",
             "exit_criterion": "Acertar pelo menos 8 de 10 questões inéditas."}]})
        provider = ScriptedProvider(planner, analysis)
        attempt = self.make_attempt(provider=provider)
        subject_seen = {"Matemática": 0, "Física": 0}
        for row in core.list_questions_for_attempt(self.db, attempt["id"]):
            subject = row["subject"]
            subject_seen[subject] += 1
            index = subject_seen[subject]
            if index == 1:
                core.record_response(self.db, attempt["id"], row["ordinal"], "A")
            elif index == 2:
                core.record_response(self.db, attempt["id"], row["ordinal"], "B", "calc_error")
            else:
                core.record_response(self.db, attempt["id"], row["ordinal"], None)
        self.assertEqual(core.list_questions_for_attempt(self.db, attempt["id"], unanswered_only=True), [])
        result = core.finalize_attempt(self.db, attempt["id"], provider)
        self.assertEqual(result["metrics"]["answered"], 6)
        self.assertEqual(result["metrics"]["subjects"]["Matemática"]["attempts"], 3)
        self.assertEqual(result["metrics"]["subjects"]["Física"]["error_reasons"]["blank"], 1)
        self.assertEqual(result["analysis_status"], "completed")
        self.assertEqual(core.get_attempt(self.db, attempt["id"])["analysis"]["steps"][0]["action"], "calculation_practice")
        self.db.close()
        self.db = core.connect_db(self.root / "diagnostico.sqlite3")
        self.assertEqual(core.get_attempt(self.db, attempt["id"])["metrics"]["correct"], 2)

    def test_less_than_three_attempts_is_forced_to_insufficient_data(self):
        attempt = self.make_attempt(total=5)
        rows = core.list_questions_for_attempt(self.db, attempt["id"])
        for row in rows:
            core.record_response(self.db, attempt["id"], row["ordinal"], None)
        metrics = core.calculate_metrics(self.db, attempt["id"])
        counts = [r["attempts"] for r in metrics["subjects"].values()]
        self.assertIn(2, counts)
        self.assertIn("insufficient_data", [r["status"] for r in metrics["subjects"].values()])
        with self.assertRaisesRegex(ValueError, "menos de 3 tentativas"):
            core.validate_analysis({"summary": "ok", "steps": [{
                "subject": next(s for s, row in metrics["subjects"].items() if row["attempts"] == 2),
                "action": "review", "reason_code": "misread", "rationale": "r",
                "exit_criterion": "8 de 10"}]}, metrics, list(self.profile["pesos"]))

    def test_llm_summary_cannot_override_low_data_status(self):
        metrics = {"subjects": {
            "Física": {"attempts": 3, "correct": 1, "accuracy": 1/3,
                       "error_reasons": {"unknown_content": 1, "calc_error": 0, "misread": 0, "blank": 1},
                       "status": "assessed"},
            "Matemática": {"attempts": 2, "correct": 0, "accuracy": 0,
                           "error_reasons": {"unknown_content": 0, "calc_error": 0, "misread": 0, "blank": 2},
                           "status": "insufficient_data"}}}
        result = core.validate_analysis({"summary": "Matemática está fraca.", "steps": [{
            "subject": "Física", "action": "theory", "reason_code": "unknown_content",
            "rationale": "Há erro de conteúdo registrado em Física.",
            "exit_criterion": "Acertar 8 de 10 questões."}]}, metrics, ["Física", "Matemática"])
        self.assertNotIn("está fraca", result["summary"])
        self.assertIn("Matemática", result["summary"])

    def test_analysis_failure_keeps_calculated_numbers(self):
        attempt = self.make_attempt()
        for row in core.list_questions_for_attempt(self.db, attempt["id"]):
            core.record_response(self.db, attempt["id"], row["ordinal"], None)
        provider = ScriptedProvider("not json", "still not json")
        result = core.finalize_attempt(self.db, attempt["id"], provider)
        stored = core.get_attempt(self.db, attempt["id"])
        self.assertIsNone(result["analysis"])
        self.assertEqual(result["analysis_status"], "analysis_failed")
        self.assertEqual(stored["metrics"]["answered"], 6)
        self.assertTrue(stored["analysis_error"])

    def test_import_text_pdf_gabarito_is_associated_with_selected_simulado(self):
        source = self.root / "questoes.classificado.json"
        questions = [question(1, "Matemática"), question(2, "Física")]
        source.write_text(json.dumps({"prova": "prova-2027-s1", "questoes": questions}), encoding="utf-8")
        pdf_path = self.root / "gabarito.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "GABARITO\n1 - A  2 - B")
        doc.save(pdf_path)
        doc.close()
        summary = core.import_answer_key(self.db, pdf_path, source)
        self.assertEqual(summary["source_id"], "prova-2027-s1")
        self.assertEqual(summary["imported"], 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM answer_keys").fetchone()[0], 2)

    def test_conflicting_pdf_gabarito_is_rejected(self):
        path = self.root / "conflict.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "1 A\n1 B")
        doc.save(path)
        doc.close()
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            core.parse_answer_key_pdf(path)


if __name__ == "__main__":
    unittest.main()
