"""Caso de uso de reprocessamento isolado, com função de extração simulada."""
import json
import tempfile
import unittest
from pathlib import Path

from studai.features.questions import reprocess, review

PNG = b"\x89PNG\r\n\x1a\nfake"


def fake_extract(text: str = "Enunciado novo", number: int = 1):
    calls = []

    def extract(source, outdir, progress=None):
        calls.append(Path(source))
        if progress:
            progress({"stage": "extracting", "message": "extraindo"})
        (outdir / "img").mkdir(parents=True, exist_ok=True)
        (outdir / "img" / "q01_completa1.png").write_bytes(PNG)
        (outdir / "img" / "q01_fig1.png").write_bytes(PNG)
        data = {"prova": "x", "textos_compartilhados": {}, "questoes": [{
            "numero": number, "pagina": 2, "texto_id": None, "enunciado": text, "nota": "",
            "alternativas": {letter: {"texto": letter.lower(), "figuras": []} for letter in "ABCDE"},
            "figuras_enunciado": ["img/q01_fig1.png"], "imagens_completas": ["img/q01_completa1.png"],
            "blocos": [], "revisar": []}]}
        path = outdir / "questoes.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    extract.calls = calls
    return extract


class ReprocessUseCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.processed = self.root / "processed"
        self.exam_dir = self.processed / "web" / "new"
        self.exam_dir.mkdir(parents=True)
        self.exam_json = self.exam_dir / "questoes.classificado.json"
        self.question = {"numero": 1, "pagina": 1, "enunciado": "", "nota": "", "alternativas": {},
                         "figuras_enunciado": [], "imagens_completas": [], "revisar": ["enunciado_vazio"],
                         "classificacao": {"materia": "Física"}}
        self.data = {"prova": "exam-id", "arquivo_origem": "prova.pdf", "questoes": [self.question]}
        self.exam_json.write_text(json.dumps(self.data), encoding="utf-8")
        self.pdf = self.root / "prova.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")
        self.workspace = self.root / "reprocess"
        self.uploads = self.root / "uploads"

    def tearDown(self):
        self.temp.cleanup()

    def preview(self, extract=None, **kwargs):
        return reprocess.create_preview(exam_id="exam-id", exam_json=self.exam_json, number=1,
                                        workspace_root=self.workspace, extract=extract or fake_extract(),
                                        **{"source_pdf": self.pdf, **kwargs})

    def confirm(self, preview, processed_root=None):
        return reprocess.confirm_preview(exam_id="exam-id", exam_json=self.exam_json, number=1,
                                         preview_id=preview["previewId"], workspace_root=self.workspace,
                                         uploads_root=self.uploads, processed_root=processed_root)

    def test_preview_uses_injected_extractor_and_changes_nothing(self):
        extract = fake_extract()
        events = []
        preview = self.preview(extract, progress=events.append)
        self.assertEqual(preview["question"]["enunciado"], "Enunciado novo")
        self.assertEqual(extract.calls[0].name, "source.pdf")
        self.assertEqual(events[0]["stage"], "extracting")
        self.assertEqual(json.loads(self.exam_json.read_text(encoding="utf-8")), self.data)
        self.assertFalse(review.review_file(self.exam_json).exists())

    def test_lazy_source_is_only_resolved_after_the_question_is_found(self):
        def missing():
            raise FileNotFoundError("sem PDF")
        with self.assertRaisesRegex(FileNotFoundError, "questão 9"):
            reprocess.create_preview(exam_id="exam-id", exam_json=self.exam_json, number=9,
                                     workspace_root=self.workspace, extract=fake_extract(), source_pdf=missing)
        with self.assertRaisesRegex(FileNotFoundError, "sem PDF"):
            self.preview(source_pdf=missing)

    def test_cancelled_and_failed_extractions_leave_no_workspace(self):
        with self.assertRaises(reprocess.ReprocessCancelled):
            self.preview(cancelled=lambda: True)
        with self.assertRaisesRegex(ValueError, "não foi localizada"):
            self.preview(fake_extract(number=2))
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_confirm_keeps_human_review_and_drops_edits(self):
        review.save_review(self.exam_json, 1, {"status": "reviewed", "subject": "Física", "topic": "Óptica",
                                               "content_id": "fisica.x", "note": "conferida",
                                               "edits": {"enunciado": "à mão"}})
        preview = self.preview()
        result = self.confirm(preview)
        saved = review.read_reviews(self.exam_json)["questions"]["1"]
        self.assertEqual((saved["status"], saved["subject"], saved["content_id"], saved["note"]),
                         ("reviewed", "Física", "fisica.x", "conferida"))
        self.assertNotIn("edits", saved)
        self.assertEqual(result["approved"], 1)
        question = json.loads(self.exam_json.read_text(encoding="utf-8"))["questoes"][0]
        self.assertEqual(question["enunciado"], "Enunciado novo")
        self.assertEqual(question["classificacao"], {"materia": "Física"})
        for path in question["figuras_enunciado"] + question["imagens_completas"]:
            self.assertTrue(path.startswith("img/reproc_"))
            self.assertTrue((self.exam_dir / path).is_file())
        self.assertTrue((self.uploads / "exam-id.pdf").is_file())
        self.assertFalse((self.workspace / preview["previewId"]).exists())

    def test_confirm_inherits_reviews_from_previous_extraction_dir(self):
        old_dir = self.processed / "web" / "old"
        old_dir.mkdir()
        old_json = old_dir / "questoes.json"
        old_json.write_text(json.dumps(self.data), encoding="utf-8")
        review.save_review(old_json, 1, {"status": "bad_crop", "note": "figura em outra página"})
        preview = self.preview(processed_root=self.processed)
        self.assertEqual(review.read_reviews(self.exam_json)["questions"]["1"]["status"], "bad_crop")
        self.confirm(preview, processed_root=self.processed)
        saved = review.read_reviews(self.exam_json)["questions"]["1"]
        self.assertNotIn("status", saved)  # bad_crop volta a pendente
        self.assertEqual(saved["note"], "figura em outra página")

    def test_confirm_rejects_stale_preview(self):
        preview = self.preview()
        changed = json.loads(self.exam_json.read_text(encoding="utf-8"))
        changed["questoes"][0]["enunciado"] = "mudou"
        self.exam_json.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mudou desde a criação"):
            self.confirm(preview)
        with self.assertRaisesRegex(ValueError, "prévia inválida"):
            reprocess.confirm_preview(exam_id="exam-id", exam_json=self.exam_json, number=1, preview_id="../x",
                                      workspace_root=self.workspace, uploads_root=self.uploads)

    def test_discard_and_assets_are_scoped_to_exam(self):
        preview = self.preview()
        preview_id = preview["previewId"]
        self.assertIsNotNone(reprocess.preview_asset(self.workspace, "exam-id", preview_id, "img/q01_fig1.png"))
        self.assertIsNone(reprocess.preview_asset(self.workspace, "outra", preview_id, "img/q01_fig1.png"))
        self.assertIsNone(reprocess.preview_asset(self.workspace, "exam-id", preview_id, "../preview.json"))
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            reprocess.discard_preview(self.workspace, "exam-id", 2, preview_id)
        reprocess.discard_preview(self.workspace, "exam-id", 1, preview_id)
        self.assertFalse((self.workspace / preview_id).exists())


if __name__ == "__main__":
    unittest.main()
