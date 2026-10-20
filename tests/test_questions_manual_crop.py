import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from studai.api import server
from studai.features.questions import manual_crop, review, source_pdf


def make_pdf(path: Path) -> None:
    """Página com duas colunas: questão na esquerda, "figura" (retângulo) na direita."""
    doc = pymupdf.open()
    page = doc.new_page()
    for y, line in ((60, "01"), (90, "Observe a imagem ao lado e responda."), (105, "Qual a cor do quadrado?"),
                    (140, "(A) azul"), (160, "(B) verde"), (180, "(C) vermelho"),
                    (200, "(D) preto e"), (215, "branco"), (235, "(E) amarelo")):
        page.insert_text((60, y), line)
    page.draw_rect(pymupdf.Rect(340, 60, 520, 220), color=(0, 0, 1), fill=(0, 0, 1))
    doc.new_page()
    doc.save(path)
    doc.close()


class ManualCropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "prova.pdf"
        make_pdf(self.pdf)
        self.exam_dir = self.root / "processed" / "web" / "exam"
        (self.exam_dir / "img").mkdir(parents=True)
        (self.exam_dir / "img" / "q01_fig1.png").write_bytes(b"\x89PNG\r\n\x1a\nantiga")
        self.exam_json = self.exam_dir / "questoes.classificado.json"
        question = {"numero": 1, "pagina": 1, "enunciado": "Observe a imagem ao lado",
                    "alternativas": {"A": {"texto": "azul", "figuras": []}, "B": {"texto": "verde", "figuras": []}},
                    "figuras_enunciado": ["img/q01_fig1.png"], "imagens_completas": []}
        self.data = {"prova": "exam-id", "arquivo_origem": "prova.pdf",
                     "arquivo_sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest(), "questoes": [question]}
        self.exam_json.write_text(json.dumps(self.data), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_page_sizes_and_render(self):
        pages = manual_crop.page_sizes(self.pdf)
        self.assertEqual([page["number"] for page in pages], [1, 2])
        png = manual_crop.render_page(self.pdf, 1, dpi=40)
        self.assertTrue(png.startswith(b"\x89PNG"))
        with self.assertRaisesRegex(ValueError, "fora do PDF"):
            manual_crop.render_page(self.pdf, 3)

    def test_crop_region_saves_image_in_exam_dir_and_suggests_text(self):
        width, height = 595, 842
        region = [40 / width, 40 / height, 300 / width, 250 / height]
        crop = manual_crop.crop_region(self.pdf, 1, region, self.exam_dir, dpi=150)
        image = self.exam_dir / crop["path"]
        self.assertTrue(image.is_file())
        self.assertTrue(crop["path"].startswith("img/crop_"))
        self.assertAlmostEqual(crop["width"], 260 * 150 / 72, delta=3)
        self.assertEqual(pymupdf.Pixmap(str(image)).width, crop["width"])
        suggestion = crop["suggestion"]
        self.assertIn("Qual a cor do quadrado?", suggestion["enunciado"])
        self.assertNotIn("01", suggestion["enunciado"].split("\n")[0][:2])
        self.assertEqual(sorted(suggestion["alternativas"]), list("ABCDE"))
        self.assertEqual(suggestion["alternativas"]["D"], "preto e branco")

    def test_crop_region_rejects_invalid_regions(self):
        with self.assertRaisesRegex(ValueError, "pequena demais"):
            manual_crop.crop_region(self.pdf, 1, [0.1, 0.1, 0.1001, 0.5], self.exam_dir)
        with self.assertRaisesRegex(ValueError, "região"):
            manual_crop.crop_region(self.pdf, 1, [0, 0, 1], self.exam_dir)
        with self.assertRaisesRegex(ValueError, "página"):
            manual_crop.crop_region(self.pdf, 9, [0, 0, 1, 1], self.exam_dir)

    def test_split_question_text_only_accepts_letters_in_order(self):
        result = manual_crop.split_question_text("12\nTexto com a) no meio\ne continua-\nção.\n(A) um\n(C) fora\n(B) dois")
        self.assertEqual(result["enunciado"], "Texto com a) no meio e continuação.")
        self.assertEqual(result["alternativas"], {"A": "um (C) fora", "B": "dois"})

    def test_apply_crop_keeps_extraction_and_review_and_leaves_bad_crop_queue(self):
        review.save_review(self.exam_json, 1, {"status": "bad_crop", "note": "imagem ao lado ficou de fora"})
        crop = manual_crop.crop_region(self.pdf, 1, [0.55, 0.05, 0.9, 0.3], self.exam_dir, extract_text=False)
        self.assertNotIn("suggestion", crop)
        saved = manual_crop.apply_crop_to_review(self.exam_json, 1, crop["path"])
        self.assertNotIn("status", saved)
        self.assertEqual(saved["note"], "imagem ao lado ficou de fora")
        self.assertEqual(saved["edits"]["figuras_enunciado"], ["img/q01_fig1.png", crop["path"]])
        self.assertEqual(json.loads(self.exam_json.read_text(encoding="utf-8")), self.data)
        self.assertEqual(review.bad_crop_queue(self.root / "processed"), [])

    def test_apply_crop_to_option_with_text_suggestion_replaces_figures(self):
        review.save_review(self.exam_json, 1, {"status": "reviewed", "subject": "Matemática", "topic": "x",
                                               "edits": {"enunciado": "corrigido"}})
        crop = manual_crop.crop_region(self.pdf, 1, [0.05, 0.05, 0.5, 0.3], self.exam_dir)
        saved = manual_crop.apply_crop_to_review(self.exam_json, 1, crop["path"], target="option:B", replace=True,
                                                 suggestion=crop["suggestion"])
        self.assertEqual(saved["status"], "reviewed")
        edits = saved["edits"]
        self.assertEqual(edits["alternativas"]["B"]["figuras"], [crop["path"]])
        self.assertEqual(edits["alternativas"]["E"]["texto"], "amarelo")
        self.assertIn("Qual a cor", edits["enunciado"])
        shown = review.apply_edits(self.data["questoes"][0], review.read_reviews(self.exam_json)["questions"]["1"])
        self.assertEqual(sorted(shown["alternativas"]), list("ABCDE"))
        with self.assertRaisesRegex(ValueError, "destino"):
            manual_crop.apply_crop_to_review(self.exam_json, 1, crop["path"], target="option:Z")


class SourcePdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "acervo" / "2025" / "prova.pdf"
        self.pdf.parent.mkdir(parents=True)
        make_pdf(self.pdf)
        self.exam_json = self.root / "questoes.json"
        self.exam_json.write_text(json.dumps({"prova": "exam-id", "arquivo_origem": "prova.pdf",
                                              "arquivo_sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
                                              "questoes": []}), encoding="utf-8")
        self.uploads = self.root / "uploads"

    def tearDown(self):
        self.temp.cleanup()

    def test_resolve_falls_back_to_archive_only_with_same_sha(self):
        with self.assertRaisesRegex(FileNotFoundError, "Envie o PDF"):
            source_pdf.resolve_source_pdf(self.exam_json, "exam-id", self.uploads)
        found = source_pdf.resolve_source_pdf(self.exam_json, "exam-id", self.uploads, [self.root / "acervo"])
        self.assertEqual(found, self.pdf.resolve())
        other = self.root / "outro" / "prova.pdf"
        other.parent.mkdir()
        other.write_bytes(b"%PDF-1.4 diferente")
        with self.assertRaises(FileNotFoundError):
            source_pdf.resolve_source_pdf(self.exam_json, "exam-id", self.uploads, [self.root / "outro"])

    def test_attach_requires_identical_pdf(self):
        with self.assertRaisesRegex(ValueError, "não é o mesmo arquivo"):
            source_pdf.attach_source_pdf(self.exam_json, "exam-id", b"%PDF-1.4 outro", "prova.pdf", self.uploads)
        with self.assertRaisesRegex(ValueError, "não corresponde"):
            source_pdf.attach_source_pdf(self.exam_json, "exam-id", self.pdf.read_bytes(), "x.pdf", self.uploads)
        target = source_pdf.attach_source_pdf(self.exam_json, "exam-id", self.pdf.read_bytes(), "prova.pdf",
                                              self.uploads)
        self.assertEqual(target, self.uploads / "exam-id.pdf")
        self.assertEqual(source_pdf.resolve_source_pdf(self.exam_json, "exam-id", self.uploads),
                         target.resolve())


class ManualCropApiTests(unittest.TestCase):
    """A rota só traduz HTTP: recorta pelo PDF associado e, com save, grava a correção."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.processed = self.root / "processed"
        exam_dir = self.processed / "web" / "exam"
        exam_dir.mkdir(parents=True)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        make_pdf(self.uploads / "exam-id.pdf")
        self.exam_json = exam_dir / "questoes.json"
        self.exam_json.write_text(json.dumps({"prova": "exam-id", "arquivo_origem": "prova.pdf", "questoes": [
            {"numero": 1, "enunciado": "", "alternativas": {}, "figuras_enunciado": []}]}), encoding="utf-8")
        self.patches = [patch.object(server, "PROCESSED_ROOT", self.processed),
                        patch.object(server, "UPLOADS_ROOT", self.uploads),
                        patch.object(server, "SOURCE_PDF_SEARCH_ROOTS", ())]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.temp.cleanup()

    def test_manual_crop_saves_edits_with_text(self):
        result = server._manual_crop("exam-id", 1, {"page": 1, "region": [0.05, 0.05, 0.5, 0.3],
                                                    "save": True, "useText": True})
        edits = result["review"]["edits"]
        self.assertEqual(edits["figuras_enunciado"], [result["path"]])
        self.assertIn("Qual a cor", edits["enunciado"])
        self.assertEqual(sorted(edits["alternativas"]), list("ABCDE"))
        with self.assertRaises(FileNotFoundError):
            server._manual_crop("exam-id", 7, {"page": 1, "region": [0, 0, 1, 1]})


if __name__ == "__main__":
    unittest.main()
