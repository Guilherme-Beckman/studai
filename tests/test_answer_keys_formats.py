"""Formatos de gabarito do acervo FUVEST (1997–2026) e gabarito impresso na prova."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from studai.features.answer_keys.service import (
    answer_key_pages, answer_key_versions, detect_exam_variant, embedded_answer_key,
    import_answer_key, import_embedded_answer_key, parse_answer_key_pdf,
)
from studai.features.diagnostics.service import connect_db
from studai.features.questions import ocr

LETTERS = "ABCDE"


def _answer(n: int) -> str:
    return LETTERS[(n * 3) % 5]


def _write(page: pymupdf.Page, items: list[tuple[float, float, str]], size: float = 9) -> None:
    for x, y, text in items:
        page.insert_text((x, y), text, fontsize=size)


def _correspondence_rows(total: int, k_of, start_y: float, x0: float, step: float = 12,
                         star: set[int] = frozenset()) -> list[tuple[float, float, str]]:
    """Linhas "resposta | nº na V | nº na K" com colunas fixas."""
    items = []
    for i, n in enumerate(range(1, total + 1)):
        y = start_y + i * step
        letter = "*" if n in star else _answer(n)
        mark = "*" if n in star else ""
        items += [(x0 + 15, y, letter), (x0 + 72, y, f"{n:02d}{mark}"), (x0 + 122, y, f"{k_of(n):02d}{mark}")]
    return items


class CorrespondenceTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_blocks_side_by_side_with_group_on_same_line(self):
        # 1999–2006: "RESPOSTA GRUPO V GRUPO K" repetido em dois blocos.
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        reverse = lambda n: 21 - n
        for x0, first in ((30, 1), (300, 11)):
            _write(page, [(x0, 80, "RESPOSTA"), (x0 + 65, 80, "GRUPO V"), (x0 + 115, 80, "GRUPO K")])
            for i, n in enumerate(range(first, first + 10)):
                y = 100 + i * 12
                _write(page, [(x0 + 15, y, _answer(n)), (x0 + 72, y, f"{n:02d}"), (x0 + 122, y, f"{reverse(n):02d}")])
        doc.save(path)
        self.assertEqual(parse_answer_key_pdf(path, "V"), {n: _answer(n) for n in range(1, 21)})
        self.assertEqual(parse_answer_key_pdf(path, "K"), {reverse(n): _answer(n) for n in range(1, 21)})
        self.assertEqual(answer_key_versions(path), ["K", "V"])

    def test_group_letter_below_and_table_continues_on_next_page(self):
        # 2007: a sigla fica embaixo de "GRUPO"; 1999: a página 2 continua sem cabeçalho.
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        _write(page, [(30, 80, "RESPOSTA"), (95, 74, "GRUPO"), (145, 74, "GRUPO"), (104, 84, "T"), (154, 84, "M")])
        _write(page, _correspondence_rows(12, lambda n: 21 - n, 100, 30))
        page2 = doc.new_page()
        for i, n in enumerate(range(13, 21)):
            y = 60 + i * 12
            _write(page2, [(20, y, _answer(n)), (75, y, f"{n:02d}"), (120, y, f"{21 - n:02d}")])
        doc.save(path)
        self.assertEqual(parse_answer_key_pdf(path, "T"), {n: _answer(n) for n in range(1, 21)})
        self.assertEqual(parse_answer_key_pdf(path, "M"), {21 - n: _answer(n) for n in range(1, 21)})

    def test_prova_headers_and_starred_annulled_question(self):
        # 2022: "RESPOSTA PROVA V PROVA K" e anulada como "*  54*  90*".
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        _write(page, [(30, 80, "RESPOSTA"), (95, 80, "PROVA V"), (145, 80, "PROVA K")])
        _write(page, _correspondence_rows(10, lambda n: 11 - n, 100, 30, star={4}))
        doc.save(path)
        answers = parse_answer_key_pdf(path, "K")
        self.assertEqual(answers[7], "*")
        self.assertEqual(answers[11 - 1], _answer(1))

    def test_inconsistent_duplicate_is_rejected(self):
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        _write(page, [(30, 80, "RESPOSTA"), (95, 80, "GRUPO V"), (145, 80, "GRUPO K")])
        _write(page, [(45, 100, "A"), (102, 100, "01"), (152, 100, "02"),
                      (45, 112, "B"), (102, 112, "01"), (152, 112, "01")])
        doc.save(path)
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            parse_answer_key_pdf(path, "V")


class VersionGridTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_grid_without_prefix_on_second_page(self):
        # 2004/2005: "01 - D   51 - B" sob "PROVA V   PROVA K", na página 2.
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        _write(doc.new_page(), [(40, 80, "1a Fase - GABARITO E CORRESPONDÊNCIA ENTRE PROVAS")])
        page = doc.new_page()
        _write(page, [(60, 80, "PROVA V"), (300, 80, "PROVA K")])
        for i in range(10):
            y = 100 + i * 12
            v1, v2 = i + 1, i + 11
            _write(page, [(40, y, f"{v1:02d} - {_answer(v1)}"), (100, y, f"{v2:02d} - {_answer(v2)}"),
                          (280, y, f"{v1:02d} - A"), (340, y, f"{v2:02d} - B")])
        doc.save(path)
        self.assertEqual(parse_answer_key_pdf(path, "V"), {n: _answer(n) for n in range(1, 21)})

    def test_prefixed_grid_with_annulled_word_and_double_answer(self):
        # 2014/2016: "V 05–ANULADA"; 2024 (retificado): "48 D E" fica com a primeira letra.
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        _write(page, [(60, 80, "PROVA V"), (300, 80, "PROVA K")])
        for i in range(12):
            n = i + 1
            v = "ANULADA" if n == 5 else _answer(n) + (" E" if n == 7 else "")
            _write(page, [(40, 100 + i * 12, f"V {n:02d} - {v}"), (280, 100 + i * 12, f"K {n:02d} - C")])
        doc.save(path)
        answers = parse_answer_key_pdf(path, "V")
        self.assertEqual(answers[5], "*")
        self.assertEqual(answers[7], _answer(7))
        self.assertEqual(len(answers), 12)
        self.assertEqual(set(parse_answer_key_pdf(path, "K").values()), {"C"})

    def test_missing_version_lists_available_ones(self):
        path = self.root / "gab.pdf"
        doc = pymupdf.open()
        page = doc.new_page()
        _write(page, [(60, 80, "PROVA V"), (300, 80, "PROVA K"), (40, 100, "01 - A"), (280, 100, "01 - B")])
        doc.save(path)
        with self.assertRaisesRegex(ValueError, "disponíveis: K, V"):
            parse_answer_key_pdf(path)
        with self.assertRaisesRegex(ValueError, "versão T não encontrada"):
            parse_answer_key_pdf(path, "T")


def _exam_with_key(path: Path, version: str = "K", total: int = 24, key_title: str | None = None) -> None:
    """Prova com marcadores "K.01 –" e, na última página, o gabarito impresso (1997–2004)."""
    doc = pymupdf.open()
    page = doc.new_page()
    for n in range(1, 4):
        _write(page, [(40, 60 + n * 40, f"{version}.{n:02d} - Enunciado da questão {n}.")])
    key = doc.new_page()
    _write(key, [(40, 50, key_title or f"1a Fase, 1o Dia - Gabarito Prova {version}")])
    for n in range(1, total + 1):
        col, row = divmod(n - 1, 12)
        _write(key, [(40 + col * 120, 80 + row * 14, f"{version} {n:02d}-{_answer(n)}")])
    doc.save(path)


class EmbeddedKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_last_page_key_is_found_and_read(self):
        exam = self.root / "fuvest_1997_prova_primeira_fase_dia_1.pdf"
        _exam_with_key(exam)
        with pymupdf.open(exam) as doc:
            self.assertEqual(answer_key_pages(doc), [1])
        self.assertEqual(embedded_answer_key(exam), {n: _answer(n) for n in range(1, 25)})
        self.assertEqual(detect_exam_variant(exam), "K")

    def test_exam_without_key_page(self):
        exam = self.root / "prova.pdf"
        doc = pymupdf.open()
        _write(doc.new_page(), [(40, 80, "V.01 - Enunciado sem gabarito")])
        doc.save(exam)
        self.assertIsNone(embedded_answer_key(exam))

    def test_variant_from_ocr_footer_when_text_layer_is_useless(self):
        # 2021: fonte em bitmap; a versão só aparece no rodapé "PAG 05/28 V" lido por OCR.
        exam = self.root / "fuvest_2021_primeira_fase.pdf"
        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page()
        doc.save(exam)
        calls = []

        def engine(page, dpi):
            calls.append(page.number)
            return [ocr.OcrLine([ocr.OcrWord(w, pymupdf.Rect(0, 0, 10, 10)) for w in ("PAG", "02/28", "V")])]

        self.assertIsNone(detect_exam_variant(exam))
        self.assertEqual(detect_exam_variant(exam, ocr=engine), "V")
        self.assertEqual(calls, [1, 2])

    def test_variant_is_not_guessed_when_text_is_split(self):
        exam = self.root / "prova.pdf"
        doc = pymupdf.open()
        _write(doc.new_page(), [(40, 80, "GRUPO V"), (40, 100, "GRUPO K")])
        doc.save(exam)
        self.assertIsNone(detect_exam_variant(exam))

    def _questions(self, numbers, extra=None) -> Path:
        path = self.root / "questoes.json"
        path.write_text(json.dumps({"prova": "fuvest-1997-k", "questoes": [
            {"numero": n, "alternativas": {}, **(extra or {}).get(n, {})} for n in numbers]}), encoding="utf-8")
        return path

    def test_import_embedded_key_skips_questions_lost_by_ocr(self):
        exam = self.root / "prova.pdf"
        _exam_with_key(exam)
        questions = self._questions([n for n in range(1, 25) if n != 7])
        db = connect_db(self.root / "db.sqlite3")
        try:
            summary = import_embedded_answer_key(db, exam, questions)
            stored = dict(db.execute("SELECT question_number,answer FROM answer_keys").fetchall())
            again = import_embedded_answer_key(db, exam, questions)
            method = db.execute("SELECT DISTINCT association_method FROM answer_keys").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(summary["imported"], 23)
        self.assertEqual(summary["missing_questions"], [7])
        self.assertNotIn(7, stored)
        self.assertEqual(stored[8], _answer(8))
        self.assertIsNone(again)                 # já tem respostas: não sobrescreve
        self.assertEqual(method, "embedded")

    def test_printed_answers_are_used_without_key_page(self):
        # 1996: "Resposta Correta D" sob cada questão, gravado pelo extrator em gabarito_impresso.
        exam = self.root / "prova.pdf"
        doc = pymupdf.open()
        doc.new_page()
        doc.save(exam)
        questions = self._questions([9, 10], {9: {"gabarito_impresso": "D"}, 10: {"gabarito_impresso": "c"}})
        db = connect_db(self.root / "db.sqlite3")
        try:
            summary = import_embedded_answer_key(db, exam, questions)
            stored = dict(db.execute("SELECT question_number,answer FROM answer_keys").fetchall())
        finally:
            db.close()
        self.assertEqual(summary["association_method"], "printed")
        self.assertEqual(stored, {9: "D", 10: "C"})

    def test_import_answer_key_can_leave_out_questions_missing_from_extraction(self):
        exam = self.root / "prova.pdf"
        _exam_with_key(exam)
        questions = self._questions([n for n in range(1, 25) if n not in (3, 4)])
        db = connect_db(self.root / "db.sqlite3")
        try:
            with self.assertRaisesRegex(ValueError, "não corresponde"):
                import_answer_key(db, exam, questions, variant="K", pages=[1])
            summary = import_answer_key(db, exam, questions, variant="K", pages=[1],
                                        allow_missing_questions=True)
        finally:
            db.close()
        self.assertEqual(summary["missing_questions"], [3, 4])
        self.assertEqual(summary["imported"], 22)


class ApiAnswerKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_processing_imports_key_printed_in_the_exam(self):
        from studai.api import server as api
        source = self.root / "fuvest_1998_prova_primeira_fase_dia_1.pdf"
        doc = pymupdf.open()
        _write(doc.new_page(), [(40, 80, "ABRA ESTE CADERNO QUANDO AUTORIZADO."),
                                (40, 100, "VERIFIQUE SE SUA FOLHA PERTENCE AO GRUPO V.")])
        page = doc.new_page()
        for index, line in enumerate(("1", "Quanto é 1 + 1?", "(A) 1", "(B) 2", "(C) 3", "(D) 4", "(E) 5")):
            page.insert_text((72, 72 + index * 20), line)
        key = doc.new_page()
        _write(key, [(40, 50, "FUVEST 98 - Primeira Fase - Prova V"), (40, 62, "GABARITO-23/11/97")])
        for n in range(1, 25):
            col, row = divmod(n - 1, 12)
            _write(key, [(40 + col * 120, 80 + row * 14, f"V {n:02d}-{_answer(n)}")])
        doc.save(source)
        db_path = self.root / "db.sqlite3"
        with patch.object(api, "UPLOADS_ROOT", self.root / "uploads"), \
             patch.object(api, "PROCESSED_ROOT", self.root / "processed"), \
             patch.object(api, "SERVICE_ROOT", self.root), \
             patch.object(api, "DEFAULT_DIAGNOSTICS_DB", db_path), \
             patch.dict(os.environ, {"STUDAI_OCR": "0"}):
            result = api._process_exam(source.name, source.read_bytes(), {"provider": "demo", "dpi": 72})
            _, questions_path = api._find_exam(result["examId"])
            version = api._exam_version(result["examId"], questions_path, source.name)
        db = connect_db(db_path)
        try:
            rows = db.execute("SELECT question_number,answer,association_method FROM answer_keys").fetchall()
        finally:
            db.close()
        self.assertEqual([tuple(r) for r in rows], [(1, _answer(1), "embedded")])
        self.assertEqual(version, "V")


if __name__ == "__main__":
    unittest.main()
