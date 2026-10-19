"""Formatos das provas FUVEST até 2021: "a)", número colado ao texto, página inteira, dígitos em imagem."""
import sys
import unittest
from pathlib import Path

import pymupdf

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.questions import extractor
from studai.features.questions.extractor import Item, Row, parse

ALTS = ("Brasília", "Recife", "Belém", "Natal", "Manaus")


def _alternatives(page, x, y, step=14):
    for k, (letter, text) in enumerate(zip("abcde", ALTS)):
        page.insert_text((x, y + k * step), f"{letter}) {text}")
    return y + 5 * step


def _texts(alts):
    return {k: a["texto"] if isinstance(a["texto"], str) else " ".join(a["texto"]) for k, a in alts.items()}


class LegacyNumberingTests(unittest.TestCase):
    def test_bold_inline_number_and_lowercase_alternatives(self):
        # 2001–2017: "08 Um funcionário..." com o número em negrito e "a) texto".
        doc = pymupdf.open()
        page = doc.new_page()
        y = 60
        for num in (1, 2):
            page.insert_text((40, y), f"{num:02d}", fontname="hebo")
            page.insert_text((56, y), "Qual cidade é a capital do país?")
            y = _alternatives(page, 40, y + 20) + 30
        questions, _ = parse(doc)
        self.assertEqual(sorted(questions), [1, 2])
        self.assertEqual(questions[2]["enunciado"], ["Qual cidade é a capital do país?"])
        self.assertEqual(_texts(questions[1]["alternativas"]), dict(zip("ABCDE", ALTS)))

    def test_day_prefixed_number(self):
        # 1998–2000: "V.01 - Em relação ao texto..." (V/T = dia da prova; a Courier base-14 não tem "–").
        doc = pymupdf.open()
        page = doc.new_page()
        y = 60
        for num in (1, 2):
            page.insert_text((40, y), f"V.{num:02d} - Em relação ao texto, assinale.", fontname="cour")
            y = _alternatives(page, 40, y + 20) + 30
        questions, _ = parse(doc)
        self.assertEqual(sorted(questions), [1, 2])
        self.assertEqual(questions[1]["enunciado"], ["Em relação ao texto, assinale."])

    def test_plain_number_inside_stem_does_not_open_question(self):
        # "02 frascos..." sem destaque, com a questão 1 ainda no enunciado, é texto.
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 60), "01", fontname="hebo")
        page.insert_text((56, 60), "Uma caixa tem")
        page.insert_text((40, 74), "02 frascos de detergente. Quantos sobram?")
        _alternatives(page, 40, 100)
        questions, _ = parse(doc)
        self.assertEqual(sorted(questions), [1])
        self.assertIn("02 frascos de detergente. Quantos sobram?", questions[1]["enunciado"])

    def test_hundred_questions(self):
        # Até 2006 a primeira fase tinha 100 questões.
        doc = pymupdf.open()
        y = 800
        for num in range(1, 101):
            if y > 700:
                page, y = doc.new_page(), 60
            page.insert_text((40, y), f"{num:02d}", fontname="hebo")
            page.insert_text((60, y), "Enunciado curto?")
            y = _alternatives(page, 40, y + 16, step=12) + 14
        questions, _ = parse(doc)
        self.assertEqual(len(questions), 100)
        self.assertEqual(max(questions), 100)


class LegacyPageTests(unittest.TestCase):
    def test_full_width_question_after_two_columns(self):
        # 2015 Q13: questões 1 e 2 em colunas e a 3 em página inteira embaixo.
        doc = pymupdf.open()
        page = doc.new_page()
        for num, x in ((1, 40), (2, 310)):
            page.insert_text((x, 60), str(num), fontname="hebo")
            page.insert_text((x, 80), "Enunciado em coluna?")
            _alternatives(page, x, 100)
        page.insert_text((40, 300), "3", fontname="hebo")
        wide = "Um tema recorrente no debate contemporâneo é a migração global e seus efeitos"
        page.insert_text((40, 320), wide)
        page.insert_text((40, 334), wide)
        for k, (letter, text) in enumerate(zip("abcde", ALTS)):
            page.insert_text((40, 360 + k * 14), f"{letter}) {text} " + "e os efeitos da migração global" * 2)
        questions, _ = parse(doc)
        self.assertEqual(sorted(questions), [1, 2, 3])
        self.assertEqual(questions[3]["enunciado"], [wide, wide])
        self.assertEqual(sorted(questions[3]["alternativas"]), list("ABCDE"))

    def test_question_blocks_follow_marker_order(self):
        # 2005 Q45: na leitura por coluna a 46 (embaixo à esquerda) vinha antes da 45.
        def row(text, col, y, lead=None):
            return Item("text", 0, col, y, row=Row(text, pymupdf.Rect(0, y, 10, y + 5), col, lead_num=lead))
        items = [row("Mapa", 0, 50), row("46 Leia", 0, 200, 46), row("texto 46", 0, 220),
                 row("45", 1, 60), row("texto 45", 1, 80), row("47 Veja", 1, 300, 47)]
        ordered = [it.row.text for it in extractor._order_by_markers(items)]
        self.assertEqual(ordered, ["Mapa", "45", "texto 45", "46 Leia", "texto 46", "47 Veja"])

    def test_letter_page_footer_and_cover_are_skipped(self):
        # 2006: página carta (792 pt) com "PAG 03/18 V" a 25 pt da base; a capa traz um "1".
        doc = pymupdf.open()
        cover = doc.new_page(width=612, height=792)
        cover.insert_text((40, 120), "1", fontname="hebo")
        cover.insert_text((40, 160), "Só abra este caderno quando o fiscal autorizar.")
        page = doc.new_page(width=612, height=792)
        page.insert_text((40, 60), "01", fontname="hebo")
        page.insert_text((56, 60), "Qual é a capital?")
        y = _alternatives(page, 40, 80)
        page.insert_text((40, y + 6), "MATEMÁTICA", fontname="hebo")
        page.insert_text((280, 770), "PAG 03/18 V")
        page.insert_text((300, 760), "3V")
        questions, _ = parse(doc)
        self.assertEqual(sorted(questions), [1])
        self.assertEqual(questions[1]["pagina"], 2)
        self.assertEqual(_texts(questions[1]["alternativas"])["E"], "Manaus")

    def test_data_box_is_a_note(self):
        # 2013 Q09: caixa "Dados:" ao lado das alternativas não entra no texto delas.
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((40, 60), "1", fontname="hebo")
        page.insert_text((40, 80), "Qual a porcentagem?")
        _alternatives(page, 40, 110)
        page.draw_rect(pymupdf.Rect(150, 98, 280, 150), color=(0, 0, 0))
        page.insert_text((156, 112), "Dados:")
        page.insert_text((156, 126), "O = 16,0")
        questions, _ = parse(doc)
        self.assertEqual(_texts(questions[1]["alternativas"])["B"], "Recife")
        self.assertIn("O = 16,0", questions[1]["nota"])


class LegacyGlyphTests(unittest.TestCase):
    def test_control_codes_become_space_and_hyphen(self):
        # 2014–2017: fontes sem ToUnicode trazem \x03 no espaço e \x00/\x02 no hífen.
        self.assertEqual(extractor._clean("põe\x00se\x03em"), "põe-se em")
        self.assertEqual(extractor._clean("Lembra\x02me"), "Lembra-me")

    def test_bitmap_type3_line_is_unreadable(self):
        # 2021: glifos em bitmap chegam com corpo nominal de 0,1 pt e códigos sem sentido.
        bitmap = [{"c": c, "size": 0.1} for c in "&'()*+"]
        normal = [{"c": c, "size": 10.0} for c in "Texto"]
        self.assertTrue(extractor._unreadable(bitmap))
        self.assertFalse(extractor._unreadable(normal))

    def test_question_numbers_drawn_as_digit_images(self):
        # 2018: cada número é um par de imagens ("0" e "5"); o dígito sai da própria sequência.
        def digit(page, x, y, d):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 12), False)
            pix.clear_with(20 * d + 5)
            page.insert_image(pymupdf.Rect(x, y, x + 6, y + 9), pixmap=pix)

        doc = pymupdf.open()
        y = 800
        for num in range(1, 13):
            if y > 650:
                page, y = doc.new_page(), 50
            digit(page, 40, y, num // 10)
            digit(page, 47, y, num % 10)
            page.insert_text((40, y + 30), f"Enunciado da questão {num}?")
            y = _alternatives(page, 40, y + 46, step=12) + 20
        questions, _ = parse(doc)
        self.assertEqual(sorted(questions), list(range(1, 13)))
        self.assertEqual(questions[12]["enunciado"], ["Enunciado da questão 12?"])


if __name__ == "__main__":
    unittest.main()
