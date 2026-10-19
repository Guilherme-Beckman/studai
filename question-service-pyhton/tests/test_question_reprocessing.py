import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from studai.api import server
from studai.features.diagnostics.service import connect_db, load_question_pool
from studai.features.questions.extractor import extract_pdf
from studai.features.questions import review
from studai.features.classification.taxonomy import CONTENT_TAXONOMY_VERSION

REVIEWED_CONTENT = "matematica.conjuntos-numericos-e-operacoes.operacoes-com-conjuntos"


class QuestionReprocessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.processed = self.root / "processed"
        self.exam_dir = self.processed / "web" / "exam"
        self.exam_dir.mkdir(parents=True)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.reprocess = self.root / "reprocess"
        self.classified = self.exam_dir / "questoes.classificado.json"
        self.base = self.exam_dir / "questoes.json"
        question = {"numero": 1, "pagina": 1, "enunciado": "Texto antigo", "nota": "nota mantida",
                    "alternativas": {"A": {"texto": "antiga"}}, "figuras_enunciado": [],
                    "imagens_completas": [], "revisar": ["enunciado_vazio"],
                    "classificacao": {"materia": "Matemática", "assunto": "Probabilidade", "confianca": 0.7}}
        other = {"numero": 2, "enunciado": "Outra questão intacta", "alternativas": {"A": {"texto": "b"}}}
        data = {"prova": "exam-id", "arquivo_origem": "prova.pdf", "questoes": [question, other]}
        self.classified.write_text(json.dumps(data), encoding="utf-8")
        self.base.write_text(json.dumps(data), encoding="utf-8")
        review.save_review(self.classified, 1, {"status": "unusable", "note": "revisar depois"})
        review.save_review(self.base, 1, {"status": "unusable", "note": "revisar depois"})
        self.pdf = self.uploads / "exam-id.pdf"
        self.make_pdf(self.pdf, include_question=True)
        self.patches = [patch.object(server, "PROCESSED_ROOT", self.processed),
                        patch.object(server, "UPLOADS_ROOT", self.uploads),
                        patch.object(server, "QUESTION_REPROCESS_ROOT", self.reprocess),
                        patch.object(server, "_set_job", return_value=None)]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.temp.cleanup()

    @staticmethod
    def make_pdf(path, include_question):
        doc = pymupdf.open()
        page = doc.new_page()
        if include_question:
            for y, line in ((60, "1"), (90, "Calcule a probabilidade de sair azul."),
                            (130, "(A) um terço"), (160, "(B) metade"), (190, "(C) dois terços"),
                            (220, "(D) um quarto"), (250, "(E) três quartos")):
                page.insert_text((72, y), line)
        doc.save(path)
        doc.close()

    def test_confirmation_saves_note_found_by_the_new_extraction(self):
        # FUVEST 2024 Q49: a nota vazava para as alternativas e ficava vazia; reextraída,
        # a caixa "Note e adote" precisa chegar à prova em vez de manter a nota antiga.
        for path in (self.classified, self.base):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["questoes"][0]["nota"] = ""
            path.write_text(json.dumps(data), encoding="utf-8")
        doc = pymupdf.open(self.pdf)
        page = doc[0]
        page.draw_rect(pymupdf.Rect(300, 115, 520, 175))
        page.insert_text((305, 130), "Note e adote:", fontsize=10)
        page.insert_text((305, 145), "Despreze a massa do fio.", fontsize=10)
        doc.saveIncr()
        doc.close()
        preview = self.run_preview()
        self.assertEqual(preview["question"]["nota"], "Note e adote: Despreze a massa do fio.")
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        for path in (self.classified, self.base):
            question = json.loads(path.read_text(encoding="utf-8"))["questoes"][0]
            self.assertEqual(question["nota"], "Note e adote: Despreze a massa do fio.", path.name)
            self.assertEqual(question["alternativas"]["A"]["texto"], "um terço")

    def run_preview(self, uploaded=None, filename=None):
        return server._reprocess_question("exam-id", 1, uploaded, filename, lambda: False, "preview-job")

    def test_preview_does_not_change_question_and_confirmation_replaces_only_extraction_fields(self):
        before = json.loads(self.classified.read_text(encoding="utf-8"))
        preview = self.run_preview()
        still_unusable = review.read_reviews(self.classified)["questions"]["1"]
        after_preview = json.loads(self.classified.read_text(encoding="utf-8"))
        self.assertEqual(before, after_preview)
        self.assertEqual(still_unusable["status"], "unusable")
        self.assertTrue(preview["question"]["enunciado"])
        self.assertTrue(preview["question"]["imagens_completas"])

        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        result = json.loads(self.classified.read_text(encoding="utf-8"))
        self.assertIn("probabilidade", result["questoes"][0]["enunciado"].lower())
        self.assertEqual(result["questoes"][0]["classificacao"], before["questoes"][0]["classificacao"])
        self.assertEqual(result["questoes"][0]["nota"], "nota mantida")
        self.assertEqual(result["questoes"][1], before["questoes"][1])
        self.assertNotIn("status", review.read_reviews(self.classified)["questions"]["1"])
        self.assertEqual(review.read_reviews(self.classified)["questions"]["1"]["note"], "revisar depois")
        for path in result["questoes"][0]["imagens_completas"]:
            self.assertTrue((self.exam_dir / path).is_file())

    def test_extraction_flagged_question_can_be_reprocessed_without_unusable_status(self):
        reviews = review.read_reviews(self.classified)
        reviews["questions"]["1"].pop("status")
        review.review_file(self.classified).write_text(json.dumps(reviews), encoding="utf-8")
        preview = self.run_preview()
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        refreshed = review.read_reviews(self.classified)["questions"]["1"]
        self.assertNotIn("status", refreshed)
        current = json.loads(self.classified.read_text(encoding="utf-8"))["questoes"][0]
        self.assertEqual(sorted(current["alternativas"]), list("ABCDE"))

    def test_reviewed_question_can_be_reprocessed_and_keeps_its_review(self):
        review.save_review(self.classified, 1, {"status": "reviewed", "subject": "Matemática",
                                                "topic": "Probabilidade"})
        preview = self.run_preview()
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        refreshed = review.read_reviews(self.classified)["questions"]["1"]
        self.assertEqual(refreshed["status"], "reviewed")
        self.assertIn("probabilidade", json.loads(self.classified.read_text(encoding="utf-8"))
                      ["questoes"][0]["enunciado"].lower())

    def test_any_question_can_be_reprocessed_even_without_flags(self):
        # Correções no extrator (ex.: alternativas em imagem) valem para questões que a
        # extração antiga não sinalizou e que já foram revisadas.
        data = json.loads(self.classified.read_text(encoding="utf-8"))
        data["questoes"][0]["revisar"] = []
        self.classified.write_text(json.dumps(data), encoding="utf-8")
        review.save_review(self.classified, 1, {"status": "reviewed", "subject": "Matemática",
                                                "topic": "Probabilidade"})
        self.assertFalse(server._reprocess_recommended(data["questoes"][0], {"status": "reviewed"}))
        preview = self.run_preview()
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        self.assertEqual(review.read_reviews(self.classified)["questions"]["1"]["status"], "reviewed")

    def test_stale_glyphs_make_unflagged_question_reprocessable(self):
        clean = {"numero": 3, "enunciado": "Calcule f(x)", "alternativas": {"A": {"texto": "2"}}, "revisar": []}
        self.assertFalse(server._reprocess_recommended(clean, None))
        # "ܣ" (U+0723) vinha de provas 2012–2019 e só passou a ser reconhecido depois.
        stale = {**clean, "alternativas": {"A": {"texto": "\u0723 = 2"}}}
        self.assertTrue(server._reprocess_recommended(stale, {"status": "reviewed"}))
        self.assertTrue(server._reprocess_recommended(clean, {"status": "unusable"}))

    def test_missing_pdf_and_wrong_question_leave_original_unchanged(self):
        self.pdf.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "Envie o PDF"):
            self.run_preview()
        self.assertEqual(review.read_reviews(self.classified)["questions"]["1"]["status"], "unusable")

        replacement = self.root / "replacement.pdf"
        self.make_pdf(replacement, include_question=False)
        with self.assertRaisesRegex(ValueError, "não foi localizada"):
            self.run_preview(replacement.read_bytes(), "prova.pdf")
        data = json.loads(self.classified.read_text(encoding="utf-8"))
        self.assertEqual(data["questoes"][0]["enunciado"], "Texto antigo")
        self.assertEqual(review.read_reviews(self.classified)["questions"]["1"]["status"], "unusable")

    def test_uploaded_pdf_filename_must_match_exam(self):
        with self.assertRaisesRegex(ValueError, "não corresponde à prova"):
            self.run_preview(self.pdf.read_bytes(), "outra-prova.pdf")

    def test_uploaded_matching_pdf_becomes_associated_after_confirmation(self):
        self.pdf.unlink()
        uploaded = self.root / "prova.pdf"
        self.make_pdf(uploaded, include_question=True)
        preview = self.run_preview(uploaded.read_bytes(), uploaded.name)
        self.assertFalse(self.pdf.exists())
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        self.assertTrue(self.pdf.is_file())
        data = json.loads(self.classified.read_text(encoding="utf-8"))
        self.assertEqual(data["arquivo_sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())

    def test_reprocessing_refreshes_shared_passage_and_links_related_questions(self):
        shared_pdf = self.root / "shared.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        lines = ((60, "Texto para as questões 1 e 2"), (88, "Base compartilhada com informação útil."),
                 (135, "1"), (160, "Enunciado da primeira questão."), (195, "(A) uma"),
                 (220, "(B) duas"), (245, "(C) três"), (270, "(D) quatro"), (295, "(E) cinco"),
                 (350, "2"), (375, "Enunciado da segunda questão."), (410, "(A) uma"),
                 (435, "(B) duas"), (460, "(C) três"), (485, "(D) quatro"), (510, "(E) cinco"))
        for y, line in lines:
            page.insert_text((72, y), line)
        doc.save(shared_pdf)
        doc.close()
        preview = self.run_preview(shared_pdf.read_bytes(), "prova.pdf")
        self.assertEqual(preview["question"]["texto_id"], "1-2")
        self.assertIn("Base compartilhada", preview["sharedText"]["texto"])
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        updated = json.loads(self.classified.read_text(encoding="utf-8"))
        question_two = next(q for q in updated["questoes"] if q["numero"] == 2)
        self.assertEqual(question_two["texto_id"], "1-2")
        self.assertIn("Base compartilhada", updated["textos_compartilhados"]["1-2"]["texto"])

    def test_unusable_question_is_excluded_from_diagnostic_pool(self):
        db = connect_db(self.root / "diagnostics.sqlite3")
        try:
            db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,is_annulled) VALUES(?,?,?,?,?,?)",
                       ("exam-id", 1, "A", "exam.pdf", "now", 0))
            db.commit()
            pool, excluded = load_question_pool(self.processed, db, ["Matemática"])
        finally:
            db.close()
        self.assertFalse(any(row["number"] == 1 for row in pool))
        self.assertEqual(excluded["unusable"], 1)

    def test_bad_crop_is_excluded_and_edits_reach_the_diagnostic_pool(self):
        data = json.loads(self.classified.read_text(encoding="utf-8"))
        data["questoes"][1]["classificacao"] = {"materia": "Matemática", "assunto": "Probabilidade"}
        self.classified.write_text(json.dumps(data), encoding="utf-8")
        review.save_review(self.classified, 1, {"status": "bad_crop"})
        review.save_review(self.classified, 2, {
            "status": "reviewed", "subject": "Matemática", "content_id": REVIEWED_CONTENT,
            "taxonomy_version": CONTENT_TAXONOMY_VERSION,
            "edits": {"alternativas": {"A": {"texto": "b"}, "B": {"texto": "corrigida"}}}})
        db = connect_db(self.root / "diagnostics.sqlite3")
        try:
            for number, answer in ((1, "A"), (2, "B")):
                db.execute("INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,is_annulled) VALUES(?,?,?,?,?,?)",
                           ("exam-id", number, answer, "exam.pdf", "now", 0))
            db.commit()
            pool, excluded = load_question_pool(self.processed, db, ["Matemática"])
        finally:
            db.close()
        self.assertEqual(excluded["bad_crop"], 1)
        # A alternativa B só existe na correção; sem ela a 2 ficaria sem gabarito válido.
        self.assertEqual([row["number"] for row in pool], [2])
        self.assertEqual(pool[0]["question"]["alternativas"]["B"]["texto"], "corrigida")

    def test_confirmed_reprocess_replaces_the_previous_text_corrections(self):
        review.save_review(self.classified, 1, {"status": "unusable", "edits": {"enunciado": "corrigido à mão"}})
        preview = self.run_preview()
        server._confirm_question_reprocess("exam-id", 1, preview["previewId"])
        refreshed = review.read_reviews(self.classified)["questions"]["1"]
        self.assertNotIn("edits", refreshed)
        self.assertNotIn("status", refreshed)

    def test_fuvest_2025_questions_1_and_26_recover_all_choices_and_full_region_crops(self):
        pdf = Path(__file__).resolve().parents[1] / "fuvest_acervo/2025/1a_fase/provas/fuvest2025_primeira_fase_prova_V1.pdf"
        if not pdf.is_file():
            self.skipTest("PDF local da FUVEST 2025 não está disponível")
        outdir = self.root / "detailed"
        result_path = extract_pdf(pdf, outdir, dpi=96, detailed_crops=True)
        data = json.loads(result_path.read_text(encoding="utf-8"))
        questions = {q["numero"]: q for q in data["questoes"]}
        for number in (1, 10, 26):
            question = questions[number]
            self.assertEqual(sorted(question["alternativas"]), list("ABCDE"))
            self.assertNotIn("alternativas_incompletas", question["revisar"])
            crop_path = outdir / question["imagens_completas"][0]
            self.assertIn("detalhada", crop_path.name)
            with pymupdf.open(crop_path) as crop_doc:
                # 1 e 26 têm conteúdo que cruza a página; a 10 cabe numa coluna e
                # o recorte não pode trazer a questão 12, que está ao lado.
                if number == 10:
                    self.assertLess(crop_doc[0].rect.width, 300)
                else:
                    self.assertGreater(crop_doc[0].rect.width, 400)
        self.assertEqual(questions[10]["texto_id"], "10-11")
        self.assertIn("cultura do", data["textos_compartilhados"]["10-11"]["texto"])


if __name__ == "__main__":
    unittest.main()
