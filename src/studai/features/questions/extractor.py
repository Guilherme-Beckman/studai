##!/usr/bin/env python3
"""
Worker de ingestão de provas (FUVEST / ENEM) em PDF -> JSON + recortes de figuras.

Uso:
    python extract.py prova.pdf -o simulados/processados/prova/
    python processar_simulados.py simulados/

Saída:
    <pasta do simulado>/questoes.json  questões estruturadas
    <pasta do simulado>/img/*.png      figuras e recortes das questões

Ideia: o PDF tem camada de texto, então em geral NÃO fazemos OCR. Lemos linhas e figuras
com coordenadas, ordenamos por coluna (esquerda inteira, depois direita) e rodamos
uma máquina de estados: número da questão abre uma nova questão, "(A)".."(E)" abrem
alternativas, "Texto para as questões X e Y" abre um texto compartilhado.

Páginas sem camada de texto legível (1997 escaneada, 2002 dia 2 com codificação
trocada, 2021 em bitmap) passam pelo OCR de ``ocr.py`` (Tesseract, opcional) e
seguem pela mesma máquina de estados.

Dependência: pip install pymupdf
"""
from __future__ import annotations

import argparse
import collections
import colorsys
import json
import logging
import re
import subprocess
import unicodedata

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf  # pip install pymupdf

from studai.features.questions import ocr as ocr_mod

log = logging.getLogger(__name__)

# ----------------------------------------------------------------- constantes
HEADER_Y = 35.0        # linhas acima disso são cabeçalho ("Simulado da Prova...")
FOOTER_Y = 805.0
A4_H = 842.0           # HEADER_Y/FOOTER_Y valem para A4; carta (2006) e páginas ampliadas (2013) se ajustam
SEP_MAX_H = 12.0       # imagens/desenhos mais baixos que isso e largos = separador colorido
TINY_W, TINY_H = 15.0, 18.0   # imagens minúsculas = glifos faltando (ex.: "ℓ")
BITMAP_FONT_SIZE = 0.5   # corpo nominal das fontes Type3 em bitmap (texto ilegível)
DIGIT_W, DIGIT_H = (3.0, 10.0), (6.0, 12.0)   # dígito do número da questão desenhado como imagem (2018)
MERGE_TOL = 4.0        # tolerância p/ juntar pedaços de uma mesma figura
CROP_PAD = 2.0
FRAGMENT_GAP = 6.0      # vão máximo entre pedaços de uma mesma linha (a calha entre colunas tem ~17 pt)
FULL_ROW_W = 0.75       # fração da largura da página: linha de questão em página inteira (2015 Q13)
SIDE_FIG_GAP = 90.0    # distância máxima entre "(A)" e a imagem da alternativa à direita (2024 Q50: 63)

MARKER_RE = re.compile(r"^\{?\s*(\d{1,3})\s*\}?$")   # até 2006 a prova tinha 100 questões
SHARED_RE = re.compile(
    r"^Texto para as quest(?:ões|ão)\s+(?:de\s+)?(\d+)\s*(?:e|a|à|-|–)\s*(\d+)", re.I
)
ALT_RE = re.compile(r"^\(([A-E])\)\s*(.*)$", re.S)
# Provas até 2017 usam "a) texto"; a linha é reescrita como "(A) texto" ao ser lida.
LOWER_ALT_RE = re.compile(r"^([a-e])\)\s*(.*)$", re.S)
# Outras provas e listas: "A) texto" ou "A. texto" (também reescritas como "(A) texto").
PAREN_ALT_RE = re.compile(r"^([A-E])\)\s*(.*)$", re.S)
DOT_ALT_RE = re.compile(r"^([A-E])\.\s+(.*)$", re.S)
ALT_STYLES = {"lower": LOWER_ALT_RE, "paren": PAREN_ALT_RE, "dot": DOT_ALT_RE}
# Marcador com a palavra "QUESTÃO 91" (ENEM, cursinhos): abre a questão mesmo quando a
# numeração da prova não começa em 1.
QUESTION_WORD_RE = re.compile(r"^QUEST[ÃA]O\s*(\d{1,3})\b\s*[.:–—-]?\s*(.*)$", re.I | re.S)
# Número colado ao começo do enunciado: "07 Na perspectiva..." (2001–2017) ou
# "V.01 – Em relação..." (1998–2000, com a letra do dia).
INLINE_MARKER_RE = re.compile(r"^\*?\s*(?:[A-Z]\s?\.\s?)?(\d{2,3})(?:\s*[–—-])?(?:\s+(.*)|$)", re.S)
# Legenda de alternativa desenhada ("Figura A" sob a imagem da alternativa A).
ALT_CAPTION_RE = re.compile(r"^(?:Figura|Fig\.|Gráfico|Imagem|Esquema)\s+([A-E])$", re.I)
NOTE_RE = re.compile(r"^(?:Note e adote|Dados\b)", re.I)   # "Dados:" é a caixa equivalente nas provas antigas
COVER_RE = re.compile(r"abra este caderno", re.I)    # capa com as instruções da prova
# Título da capa e da contracapa; em 2021 é o único texto legível delas (o resto está em bitmap).
COVER_TITLE_RE = re.compile(r"Fase\s*[−–-]\s*Conhecimentos Gerais", re.I)
# Página de rascunho ("O que for escrito no RASCUNHO NÃO SERÁ CONSIDERADO NA CORREÇÃO", 2024 p. 35):
# lida em colunas, a marca d'água grudava na alternativa E da última questão (2024 K Q90).
DRAFT_RE = re.compile(r"\bRASCUNHO\b", re.I)
KEY_ENTRY_RE = re.compile(r"\b[VKQXZ]\s?\d{1,3}\s?-\s?[A-E]\b")  # gabarito no fim da prova (2003): "V 45- A"
# Número de página ("3V", "4 V", "v") perto da margem e título de matéria entre questões.
PAGE_MARK_RE = re.compile(r"^(?:\d{1,2}\s?)?[VTvt]$")
SUBJECT_RE = re.compile(r"^(?:LÍNGUA PORTUGUESA|PORTUGUÊS|LITERATURA|INGLÊS|LÍNGUA INGLESA|MATEMÁTICA|"
                        r"FÍSICA|QUÍMICA|BIOLOGIA|HISTÓRIA|GEOGRAFIA|ARTES|FILOSOFIA|SOCIOLOGIA|"
                        r"HISTÓRIA GERAL|HISTÓRIA DO BRASIL|CIÊNCIAS)$")
BOLD_FONT_RE = re.compile(r"bold|black|heavy|,bd\b", re.I)
LEAD_NUM_RE = re.compile(r"^(?:[A-Z]\.)?(\d{2,3})[–—-]?$")
# Número lido pelo OCR num escaneado (1997): "K.15 - ...", com dígitos às vezes trocados
# ("K. lá -" = K.14, "K.5h -" = K.54). Só vale com a letra do dia e o travessão.
OCR_MARKER_RE = re.compile(r"^([A-Z])\s?[.,]\s?(\S{2})\s*[-–—~]+(?:\s+(.*)|$)", re.S)
OCR_INLINE_RE = re.compile(r"^\d{2,3}\s+[A-ZÀ-Ú“\"]")   # "02 Considere as afirmações"
OCR_LEAD_JUNK_RE = re.compile(r"^[(|\[]\s+(?=[A-Z])")    # "( Note e adote:" (borda da caixa lida como "(")
# Letra de alternativa mal lida: "(Ajc<", "(JV=1,6", "(Qe<", "(EJc<1", "A) 10 bilhões" e, nas
# provas "a)" escaneadas (1997), "aj) À e B", "bh) 30º", "co) 45º".
OCR_ALT_RE = re.compile(r"^(?:[(\[{]\s?([A-Za-z0-9ÇÉ])|([A-Ea-e])[a-z]?[)\]}jJ|](?=\s|$))\s?([)\]}J|]?)\s?(.*)$",
                        re.S)
OCR_DIGIT_LOOKALIKES = {"l": "1", "I": "1", "i": "1", "|": "1", "!": "1", "O": "0", "o": "0",
                        "S": "5", "s": "5", "B": "8", "G": "6", "b": "6", "T": "7", "Z": "2", "z": "2"}

MATH_FONTS = ("cambria", "math", "symbol", "mt-extra")
# Alguns PDFs FUVEST mapeiam glifos de Cambria Math para blocos Unicode
# sem relação com o texto visível (p. ex. numerais Oriya em índices).
# O que sobrar depois de _decode_glyphs ainda é sinalizado para revisão.
CORRUPT_GLYPH_RE = re.compile(r"[\ufffd\ue000-\uf8ff\u0700-\u07ff\u0b00-\u0dff\u1200-\u137f]")

# Cambria Math exportada pelo Word: o ToUnicode quebrado desloca o código do
# glifo para blocos indianos/etíopes. Cada faixa abaixo foi conferida no acervo
# (ex.: "ሺୡୟ୲ୟ୪୧ୱୟୢ୭୰ሻ" = "(catalisador)", "𝑏௫" = b^x, "logଶ" = log₂).
_CAMBRIA_RANGES = (
    (0x0B34, 0x0B3D, "0"),   # dígitos em corpo de índice
    (0x0B5F, 0x0B78, "a"),   # minúsculas retas em índice ("máx", "catalisador")
    (0x0BBA, 0x0BD3, "A"),   # maiúsculas itálicas em índice (F_T, F_E)
    (0x0BD4, 0x0BED, "a"),   # minúsculas itálicas em índice (P_i, b^x)
    (0x0C08, 0x0C20, "α"),   # gregas em índice de índice ("sen గ/2" = sen π/2)
    (0x0C2C, 0x0C35, "0"),   # dígitos em índice de índice (I₀ dentro de fração)
    (0x0C3A, 0x0C53, "A"),
    (0x0CB2, 0x0CCB, "A"),   # maiúsculas itálicas em índice de índice (F_W/F_T, P_A − P_B)
    (0x0723, 0x073C, "A"),   # provas 2012–2019: letras itálicas em corpo normal
    (0x073D, 0x0756, "a"),
    (0x07D9, 0x07F1, "α"),   # idem, gregas minúsculas (ߙ = α, ߠ = θ)
)
_CAMBRIA_CHARS = {
    0x0372: "0", 0x0373: "1", 0x02B9: "2", 0x0374: "2", 0x0375: "3", 0x0376: "4",
    0x0377: "5", 0x0378: "6", 0x0379: "7", 0x037A: "8", 0x037B: "9",
    0x0B3E: "+", 0x0B3F: "−", 0x0539: "ℝ", 0x05D0: "∈", 0x11F1: "′",
    0x0D45: "+", 0x0D46: "−", 0x0D4C: "=", 0x0D4D: "≠", 0x0D4E: "≈", 0x0D4F: "<",
    0x0D50: ">", 0x0D51: "≤", 0x0D52: "≥", 0x0D57: "/", 0x0D5C: "{", 0x0D5D: "{",
    0x0D60: "}", 0x0D6B: "(", 0x0D6C: "(", 0x0D6F: ")", 0x0D70: ")",
    0x0DA7: "√", 0x0DCD: "∑", 0x122E: "→",
    0x123A: "(", 0x123B: ")", 0x123C: "{", 0x123D: "}", 0x123E: "[", 0x123F: "]",
    0x1240: "(", 0x1241: ")", 0x1242: "[", 0x1243: "]",
}
# Fontes Symbol/Wingdings chegam na área privada (U+F0xx) com o código da fonte.
_SYMBOL_UPPER = "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ"
_SYMBOL_LOWER = "αβχδεφγηιϕκλμνοπθρστυϖωξψζ"
_SYMBOL_EXTRA = {
    0x28: "(", 0x29: ")", 0x2B: "+", 0x2D: "−", 0x3C: "<", 0x3D: "=", 0x3E: ">",
    0x40: "≅", 0x5E: "⊥", 0xA2: "′", 0xA3: "≤", 0xA5: "∞", 0xAC: "←", 0xAE: "→",
    0xB0: "°", 0xB1: "±", 0xB3: "≥", 0xB4: "×", 0xB6: "∂", 0xB7: "•", 0xB9: "≠",
    0xBB: "≈", 0xCE: "∈", 0xD6: "√", 0xD7: "·", 0xE5: "∑",
}
_MMETC = {",": "ℓ", "c": "°"}                     # conferido em todo o acervo 2026
_WINGDINGS = {0x6C: "●", 0x6D: "❍", 0x6E: "■", 0x6F: "□", 0x71: "❑", 0x75: "◆",
              0x9F: "•", 0xA7: "▪", 0xFB: "✗", 0xFC: "✓"}

_SUB = str.maketrans("0123456789+-−=()aehijklmnoprstuvx", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ")
_SUP = str.maketrans("0123456789+-−=()in", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁱⁿ")
FRACTION_BAR_MAX_H = 1.5
COLOR_TAG_RE = re.compile(r"\[(\w+): ([^\[\]]*)\](\s*)\[\1: ")


# ------------------------------------------------------------------- modelos
@dataclass
class Row:
    """Uma linha (já fundida com linhas na mesma altura, p/ tabelas)."""
    text: str
    bbox: pymupdf.Rect
    col: int
    fonts: set[str] = field(default_factory=set)
    parts: list[tuple[pymupdf.Rect, str]] = field(default_factory=list)
    n_math: int = 0                 # glifos de fonte matemática (para achar fórmulas em destaque)
    n_chars: int = 0
    display: bool = False           # fórmula em destaque (recuada e quase só matemática)
    short: bool = False             # termina antes da margem direita: a próxima linha abre parágrafo
    indent: bool = False            # começa recuada: abre parágrafo
    size: float = 0.0               # corpo dominante (fonte/legenda costumam ser menores)
    ink: pymupdf.Rect | None = None # área desenhada, com índices soltos (recorte de fórmula)
    lead_num: int | None = None     # número em destaque (negrito/maior) abrindo a linha: "07 Na..."


@dataclass
class Fig:
    bbox: pymupdf.Rect
    col: int
    alt: str | None = None          # alternativa à qual pertence (A..E), se aplicável
    texto: list[str] = field(default_factory=list)  # texto vetorial dentro da figura


@dataclass
class Item:
    kind: str                       # "text" | "fig" | "note" | "end"
    page: int
    col: int
    y0: float
    row: Row | None = None
    fig: Fig | None = None


# ------------------------------------------------------------ extração (página)
def _margins(page: pymupdf.Page) -> tuple[float, float]:
    """
    Limites de cabeçalho e rodapé. O rodapé fica à mesma distância da base que no A4
    (carta, 2006: "PAG 03/18 V" a 25 pt da base); páginas ampliadas (2013, 1191 pt)
    escalam as duas margens, e páginas menores nunca as reduzem.
    """
    k = max(1.0, page.rect.height / A4_H)
    return HEADER_Y * k, page.rect.height - (A4_H - FOOTER_Y) * k


def _overlap_ratio(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    top, bot = max(a.y0, b.y0), min(a.y1, b.y1)
    if bot <= top:
        return 0.0
    return (bot - top) / max(1e-6, min(a.height, b.height))


def _col_of(rect: pymupdf.Rect, page_w: float) -> int:
    return 0 if (rect.x0 + rect.x1) / 2 < page_w / 2 else 1


def _fig_col(rect: pymupdf.Rect, page_w: float) -> int:
    """Figura que atravessa as duas colunas segue a regra das linhas largas: coluna 0.
    Pelo centro, um mapa de 95 a 500 pt (2026 Q52) caía na coluna da direita."""
    return 0 if rect.width >= page_w * 0.55 else _col_of(rect, page_w)


def _raw_lines(page: pymupdf.Page):
    """Linhas com caracteres individuais (rawdict) -> lista de dicts mutáveis."""
    out = []
    for blk in page.get_text("rawdict")["blocks"]:
        if blk["type"] != 0:
            continue
        for ln in blk["lines"]:
            chars, fonts = [], set()
            for sp in ln["spans"]:
                fonts.add(sp["font"])
                bold = bool(sp["flags"] & 16) or bool(BOLD_FONT_RE.search(sp["font"]))
                chars.extend({"c": c["c"], "bbox": pymupdf.Rect(c["bbox"]), "oy": c["origin"][1],
                              "size": sp["size"], "color": sp["color"], "font": sp["font"], "bold": bold}
                             for c in sp["chars"])
            # Em linhas com texto e fórmula, o PDF pode devolver cada fonte como
            # um span separado e concatená-los por fonte. Ordenar pela posição
            # horizontal recompõe a leitura e evita intercalar partes da equação.
            chars.sort(key=lambda ch: (round(ch["bbox"].x0, 2), round(ch["bbox"].y0, 2)))
            out.append({"chars": chars, "bbox": pymupdf.Rect(ln["bbox"]), "fonts": fonts})
    return out


def _patch_missing_glyphs(lines, page: pymupdf.Page):
    """
    O PDF desenha alguns glifos (ex.: 'ℓ' de H2O(ℓ), HCℓ, Tℓ) como mini-imagens e deixa um
    placeholder (espaço ou vírgula) na camada de texto, às vezes no começo da linha seguinte.
    Aqui o placeholder sob a imagem vira 'ℓ'; se não houver, insere o 'ℓ' na posição da imagem.
    """
    tiny = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()
            if (i["bbox"][2] - i["bbox"][0]) <= TINY_W and (i["bbox"][3] - i["bbox"][1]) <= TINY_H]
    for img in tiny:
        # 1) placeholder sob a imagem, em qualquer linha
        done = False
        for ln in lines:
            lb = ln["bbox"]
            if not (lb.y0 - 3 <= img.y0 and img.y1 <= lb.y1 + 3):
                continue
            cand = [k for k, ch in enumerate(ln["chars"])
                    if ch["c"] in (" ", ",") and img.x0 - 0.5 <= (ch["bbox"].x0 + ch["bbox"].x1) / 2 <= img.x1 + 0.5]
            # o placeholder costuma ser uma vírgula de largura zero; prefere ela ao espaço
            cand.sort(key=lambda k: ln["chars"][k]["c"] != ",")
            if cand:
                k = cand[0]
                ln["chars"][k] = {"c": "ℓ", "bbox": ln["chars"][k]["bbox"]}
                done = True
            if done:
                break
        if done:
            continue
        # 2) sem placeholder: insere no fim da linha que termina logo antes da imagem
        for ln in lines:
            lb = ln["bbox"]
            if lb.y0 - 3 <= img.y0 and img.y1 <= lb.y1 + 3 and lb.x0 - 6 <= img.x0 <= lb.x1 + 6:
                chars = ln["chars"]
                pos = next((k for k, ch in enumerate(chars) if ch["bbox"].x0 >= img.x0 - 1), len(chars))
                chars.insert(pos, {"c": "ℓ", "bbox": img})
                break


# ------------------------------------------------------------ fórmulas
def _is_math(ch: dict) -> bool:
    return any(m in str(ch.get("font", "")).lower() for m in MATH_FONTS)


def _decode_char(c: str, font: str) -> str:
    cp, font = ord(c), font.lower()
    if 0x1D400 <= cp <= 0x1D7FF or cp == 0x210E:      # 𝐸, 𝑥, 𝟏, 𝐥𝐨𝐠, ℎ -> E, x, 1, log, h
        return unicodedata.normalize("NFKC", c)
    if "cambria" in font:
        for lo, hi, base in _CAMBRIA_RANGES:
            if lo <= cp <= hi:
                return chr(ord(base) + cp - lo)
        return _CAMBRIA_CHARS.get(cp, c)
    if 0xF020 <= cp <= 0xF0FF:
        code = cp - 0xF000
        if "symbol" in font:
            if 0x41 <= code <= 0x5A:
                return _SYMBOL_UPPER[code - 0x41]
            if 0x61 <= code <= 0x7A:
                return _SYMBOL_LOWER[code - 0x61]
            return _SYMBOL_EXTRA.get(code, c)
        if "wingdings" in font:
            return _WINGDINGS.get(code, c)
    if "mmetc" in font:                                # MathMagic: "HC," = HCℓ, "20 cC" = 20 °C
        return _MMETC.get(c, c)
    return c


def _decode_glyphs(lines) -> None:
    for ln in lines:
        for ch in ln["chars"]:
            decoded = _decode_char(ch["c"], str(ch.get("font", "")))
            if decoded != ch["c"]:
                ch["raw"], ch["c"] = ch["c"], decoded


def _refresh_bbox(ln) -> None:
    boxes = [ch["bbox"] for ch in ln["chars"] if ch["c"].strip()]
    if boxes:
        r = pymupdf.Rect(boxes[0])
        for b in boxes[1:]:
            r |= b
        ln["bbox"] = r


def _insert_token(ln, token: dict) -> None:
    """Insere um pedaço sintético (fração) na linha, com espaços só onde a leitura pede."""
    chars = ln["chars"]
    pos = next((k for k, ch in enumerate(chars) if ch["bbox"].x0 >= token["bbox"].x0 - 0.5), len(chars))
    prev = chars[pos - 1]["c"] if pos > 0 else ""
    nxt = chars[pos]["c"] if pos < len(chars) else ""
    text = token["c"]
    if prev and not prev.isspace() and prev not in "([{":
        text = " " + text
    if nxt and not nxt.isspace() and nxt not in ".,;:)]}":
        text += " "
    chars.insert(pos, {**token, "c": text})


def _rebuild_fractions(lines, page: pymupdf.Page) -> None:
    """
    Frações empilhadas (Cambria Math) viram "num/den": o PDF desenha a barra como um
    retângulo fino e deixa numerador e denominador como linhas de texto separadas.
    """
    bars = []
    for dr in page.get_drawings():
        r = pymupdf.Rect(dr["rect"])
        if dr.get("fill") is not None and r.height <= FRACTION_BAR_MAX_H and 2 <= r.width <= 150:
            bars.append(r)
    for bar in sorted(bars, key=lambda r: (r.y0, r.x0)):
        num, den, misplaced = [], [], []
        for li, ln in enumerate(lines):
            for ci, ch in enumerate(ln["chars"]):
                b, oy, size = ch["bbox"], ch.get("oy"), ch.get("size") or 0
                if (oy is None or ch.get("frac") or not ch["c"].strip()
                        or not bar.x0 - 0.8 <= (b.x0 + b.x1) / 2 <= bar.x1 + 0.8):
                    continue
                # Pela linha de base: a caixa da fonte é alta demais e, com alternativas
                # empilhadas, alcançaria o denominador da fração de cima.
                if 0 <= bar.y0 - oy <= 0.55 * size:
                    num.append((li, ci))
                elif 0.6 * size <= oy - bar.y1 <= 1.4 * size:
                    den.append((li, ci))
                else:
                    continue
                if b.width < 0.1:
                    misplaced.append(ch)
        if misplaced:
            # Glifo mapeado para marca combinante (ex.: "଼" = 8) vem sem largura e
            # na altura errada: não dá para saber se é numerador ou denominador.
            # Mantém o código original para a questão cair em glifo_estranho.
            for ch in misplaced:
                ch["c"] = ch.get("raw", ch["c"])
            continue
        picked = num + den
        # Barras de tabela/sublinhado também são retângulos finos; só vira fração
        # quando os dois lados existem e são glifos de fonte matemática.
        if not num or not den or not all(_is_math(lines[li]["chars"][ci]) for li, ci in picked):
            continue

        def part(idx):
            chs = sorted((lines[li]["chars"][ci] for li, ci in idx), key=lambda ch: ch["bbox"].x0)
            s = "".join(ch["c"] for ch in chs).strip()
            return f"({s})" if re.search(r"[\s+\-−·×]", s) else s

        first = lines[num[0][0]]["chars"][num[0][1]]
        area = pymupdf.Rect(bar)
        for li, ci in picked:
            area |= lines[li]["chars"][ci]["bbox"]
        token = {"c": f"{part(num)}/{part(den)}", "bbox": area, "oy": None, "frac": True,
                 "size": max(lines[li]["chars"][ci]["size"] for li, ci in picked),
                 "color": first.get("color", 0), "font": first.get("font", "")}
        touched = {li for li, _ in picked}
        for li in touched:
            drop = {ci for l2, ci in picked if l2 == li}
            lines[li]["chars"] = [ch for ci, ch in enumerate(lines[li]["chars"]) if ci not in drop]
            _refresh_bbox(lines[li])
        mid = (bar.y0 + bar.y1) / 2
        hosts = [ln for ln in lines if any(ch["c"].strip() for ch in ln["chars"])
                 and ln["bbox"].y0 - 1 <= mid <= ln["bbox"].y1 + 1
                 and ln["bbox"].x0 - 12 <= bar.x1 and bar.x0 <= ln["bbox"].x1 + 12]
        if hosts:
            host = min(hosts, key=lambda ln: (max(0.0, ln["bbox"].x0 - bar.x1, bar.x0 - ln["bbox"].x1),
                                              abs((ln["bbox"].y0 + ln["bbox"].y1) / 2 - mid)))
            _insert_token(host, token)
        else:
            lines.append({"chars": [token], "bbox": pymupdf.Rect(area), "fonts": {token["font"]}})
    lines[:] = [ln for ln in lines if ln["chars"]]


def _ref_size(chars) -> float:
    sizes = [round(ch.get("size") or 0, 1) for ch in chars if ch["c"].strip()]
    return max(set(sizes), key=sizes.count) if sizes else 0.0


def _merge_script_lines(lines) -> None:
    """Índices soltos (limite do ∑, expoente alto) voltam para a linha da fórmula."""
    for ln in list(lines):
        chs = [ch for ch in ln["chars"] if ch["c"].strip()]
        if not chs or not all(_is_math(ch) for ch in chs):
            continue
        size = max(ch.get("size") or 0 for ch in chs)
        b = ln["bbox"]
        cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
        hosts = []
        for other in lines:
            ob = other["bbox"]
            if other is ln or not any(_is_math(ch) for ch in other["chars"]):
                continue
            if size > 0.85 * _ref_size(other["chars"]) or not ob.x0 <= cx <= ob.x1:
                continue
            gap = abs((ob.y0 + ob.y1) / 2 - cy)
            if gap <= ob.height * 1.3:
                hosts.append((gap, other))
        if hosts:
            host = min(hosts, key=lambda h: h[0])[1]
            host["chars"] = sorted(host["chars"] + ln["chars"], key=lambda ch: ch["bbox"].x0)
            host["fonts"] |= ln["fonts"]
            # A caixa da linha fica como está (mudá-la funde linhas vizinhas); o
            # recorte da fórmula usa "ink" para incluir o limite do ∑.
            host["ink"] = pymupdf.Rect(host.get("ink", host["bbox"])) | ln["bbox"]
            lines.remove(ln)


def _mark_scripts(lines) -> None:
    """Glifo matemático menor e fora da linha de base = índice ("_") ou expoente ("^")."""
    for ln in lines:
        ref = _ref_size(ln["chars"])
        base = sorted(ch["oy"] for ch in ln["chars"]
                      if ch.get("oy") is not None and ch["c"].strip() and abs((ch.get("size") or 0) - ref) < 0.6)
        if not ref or not base:
            continue
        baseline = base[len(base) // 2]
        for ch in ln["chars"]:
            # Fora das fontes matemáticas só números e sinais ("10³⁰", "CaCO₃", "Ca²⁺"):
            # letras menores em fonte de texto costumam ser versalete/rodapé.
            if ch.get("oy") is None or (ch.get("size") or ref) > 0.85 * ref:
                continue
            if not _is_math(ch) and (ch["c"] not in "0123456789+-−" or ch.get("color") == 0xFFFFFF):
                continue
            dy = ch["oy"] - baseline
            if dy > 0.1 * ref:
                ch["script"] = "sub"
            elif dy < -0.2 * ref:
                ch["script"] = "sup"


def _render_script(text: str, kind: str) -> str:
    text = text.strip()
    if kind == "sup" and text == "o":
        return "°"                                    # "45୭" = 45°
    if kind == "sup" and text in ("′", "″"):
        return text
    table = _SUB if kind == "sub" else _SUP
    if text and all(c in table for c in map(ord, text)):
        return text.translate(table)
    return ("_{%s}" if kind == "sub" else "^{%s}") % text


def _color_name(rgb: int) -> str | None:
    r, g, b = ((rgb >> 16) & 255) / 255, ((rgb >> 8) & 255) / 255, (rgb & 255) / 255
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.35 or v < 0.3:
        return None                                   # preto, cinza, branco (texto oculto)
    hue = h * 360
    for limit, name in ((15, "vermelho"), (40, "laranja"), (70, "amarelo"), (165, "verde"),
                        (255, "azul"), (290, "roxo"), (345, "rosa"), (361, "vermelho")):
        if hue < limit:
            return name
    return None


def _line_text(ln) -> str:
    """
    Texto da linha com índices compostos e, quando a cor carrega significado
    (trecho colorido no meio de texto preto, ou fórmula colorida), "[azul: ...]".
    """
    chars = ln["chars"]
    names = [_color_name(ch.get("color") or 0) if ch["c"].strip() else None for ch in chars]
    solid = [n for n, ch in zip(names, chars) if ch["c"].strip()]
    tag = any(solid) and (None in solid or all(_is_math(ch) for ch in chars if ch["c"].strip()))
    if not tag:
        names = [None] * len(chars)
    else:
        # espaço herda a cor quando está entre dois trechos da mesma cor
        for k, ch in enumerate(chars):
            if not ch["c"].strip():
                prev = next((names[j] for j in range(k - 1, -1, -1) if chars[j]["c"].strip()), None)
                nxt = next((names[j] for j in range(k + 1, len(chars)) if chars[j]["c"].strip()), None)
                names[k] = prev if prev == nxt else None
        # trecho colorido sem letra/número (vírgula, parêntese) não carrega sentido
        k = 0
        while k < len(chars):
            j = k
            while j < len(chars) and names[j] == names[k]:
                j += 1
            if names[k] and not any(c.isalnum() for ch in chars[k:j] for c in ch["c"]):
                names[k:j] = [None] * (j - k)
            k = j

    out, open_color, k = [], None, 0
    while k < len(chars):
        kind = chars[k].get("script")
        j = k + 1
        if kind:
            while j < len(chars) and chars[j].get("script") == kind:
                j += 1
            piece = _render_script("".join(ch["c"] for ch in chars[k:j]), kind)
        else:
            piece = chars[k]["c"]
        color = names[k]
        if color != open_color:
            if open_color:
                out.append("]")
            if color:
                out.append(f"[{color}: ")
            open_color = color
        out.append(piece)
        k = j
    if open_color:
        out.append("]")
    return "".join(out)


def _clean(s: str) -> str:
    s = re.sub(r"#{3,}", " ", s)          # "#####" escondido na camada de texto, marca fim de questão
    s = s.replace("\u00ac", " ")          # glifo usado pelo PDF no lugar de espaços
    s = s.replace("\u2011", "-").replace("\u00ad", "").replace("\u00a0", " ")
    # Fontes sem ToUnicode (2014–2017) trazem o código do glifo: \x03 é o espaço e
    # \x00/\x02 entre letras é o hífen ("põe\x00se", "Lembra\x02me").
    s = re.sub(r"(?<=[^\W\d_])[\x00\x02](?=[^\W\d_])", "-", s).replace("\x03", " ")
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = re.sub(r"ℓ \)", "ℓ)", s)
    s = re.sub(r"\[(\w+): \s*", r"[\1: ", s)
    s = re.sub(r"\s+\]", "]", s)
    while COLOR_TAG_RE.search(s):                     # "[azul: 1/2][azul: log₂]" -> um trecho só
        s = COLOR_TAG_RE.sub(r"[\1: \2\3", s)
    return re.sub(r"ℓ ([.,;])", r"ℓ\1", s)


def _lead_num(chars) -> int | None:
    """
    Número da questão no começo da linha, destacado do resto: em negrito ("08 Um
    funcionário", 2013) ou em corpo maior ("07 Na perspectiva", 2006). Uma linha
    só com o número em negrito ("V.04", 1998) também conta.
    """
    solid = [ch for ch in chars if ch["c"].strip() and ch["c"] != "\x03"]
    k = 0
    while k < len(solid) and k < 6 and not (k and solid[k]["bbox"].x0 - solid[k - 1]["bbox"].x1 > 1.5):
        k += 1
    lead, rest = solid[:k], solid[k:]
    m = LEAD_NUM_RE.match("".join(ch["c"] for ch in lead))
    if not m:
        return None
    if rest and rest[0]["c"] in "–—-":                 # "V.01 – Em relação..."
        rest = rest[1:]
    lead_size = min(ch.get("size") or 0 for ch in lead)
    # Título em negrito na mesma linha ("47      Indicadores Sociais", 2005): o vão largo separa.
    apart = not rest or rest[0]["bbox"].x0 - lead[-1]["bbox"].x1 >= 2 * lead_size
    bold = all(ch.get("bold") for ch in lead) and (apart or not all(ch.get("bold") for ch in rest))
    big = bool(rest) and lead_size >= 1.12 * _ref_size(rest)
    return int(m.group(1)) if bold or big else None


def _join_fragments(cands: list[Row], pw: float) -> list[Row]:
    """
    Uma linha de página inteira pode vir picada em pedaços colados (2017 Q46); pela
    posição, o pedaço à direita do meio cairia na outra coluna. Pedaços na mesma
    altura e separados só por um espaço viram uma linha, e a coluna é recalculada.
    Entre as colunas há ~17 pt de calha, então colunas vizinhas não se juntam.
    """
    out: list[Row] = []
    for r in sorted(cands, key=lambda r: r.bbox.x0):
        # Na mesma coluna a fusão de _build_rows já cuida (e sabe lidar com índices soltos).
        prev = next((o for o in out if o.col != r.col and _overlap_ratio(o.bbox, r.bbox) >= 0.6
                     and -1 <= r.bbox.x0 - o.bbox.x1 <= FRAGMENT_GAP), None)
        if prev is None:
            out.append(r)
            continue
        prev.text = _clean(prev.text + ("" if r.bbox.x0 - prev.bbox.x1 < 1.5 else " ") + r.text)
        prev.bbox |= r.bbox
        prev.ink |= r.ink
        prev.fonts |= r.fonts
        prev.n_math += r.n_math
        prev.n_chars += r.n_chars
        prev.col = 0 if prev.bbox.width >= pw * 0.55 else _col_of(prev.bbox, pw)
    return out


def _unreadable(chars) -> bool:
    """
    Fonte Type3 em bitmap sem ToUnicode (prova de 2021 inteira, cabeçalhos de 2017–2018):
    a camada de texto traz códigos sem relação com o glifo, num corpo nominal de ~0,1 pt.
    A linha é descartada; a questão fica com o recorte da página e cai em
    enunciado_vazio/alternativas_incompletas para revisão.
    """
    solid = [ch for ch in chars if ch["c"].strip()]
    return bool(solid) and sum((ch.get("size") or 1) < BITMAP_FONT_SIZE for ch in solid) >= 0.5 * len(solid)


def _ocr_fix_marker(txt: str) -> str:
    """
    "K. lá - Duas esferas" -> "K.1á - ..." -> número com sósias trocados ("l" = 1, "O" = 0).
    Só os dois caracteres do número mudam; o que não vira dígito fica como está
    e a linha só abre questão se bater com o número esperado (ver _ocr_marker).
    """
    m = OCR_MARKER_RE.match(txt)
    if not m:
        return txt
    num = "".join(OCR_DIGIT_LOOKALIKES.get(c, c) for c in m.group(2))
    return f"{m.group(1)}.{num} – {m.group(3) or ''}".strip()


def _normalize_alt(txt: str, alt_style: str) -> str:
    """Alternativas "a)", "A)" ou "A." passam a ser "(A)", o formato do resto do código."""
    pattern = ALT_STYLES.get(alt_style)
    m = pattern.match(txt) if pattern else None
    return f"({m.group(1).upper()}) {m.group(2)}".strip() if m else txt


def _ocr_merge(lines: list[dict], ocr: dict) -> list[dict]:
    """
    Junta as linhas do OCR às da camada de texto. Página ilegível ("garbage"): do PDF
    ficam só os números de questão legíveis (em 2021 eles estão em fonte de verdade,
    e o Tesseract às vezes pula um número sozinho). Página escaneada ("missing"): o
    texto de verdade (rodapé, título) fica e o OCR entra onde não há texto.
    """
    native = [ln for ln in lines if not _unreadable(ln["chars"])]
    if ocr["status"] == ocr_mod.GARBAGE:
        native = [ln for ln in native if MARKER_RE.match("".join(c["c"] for c in ln["chars"]).strip())]
    extra = [o for o in ocr["lines"]
             if not any(_overlap_ratio(o["bbox"], n["bbox"]) > 0.5 and o["bbox"].intersects(n["bbox"])
                        for n in native)]
    return native + extra


def _build_rows(page: pymupdf.Page, parts: list[pymupdf.Rect] | None = None,
                alt_style: str = "upper", ocr: dict | None = None) -> tuple[list[Row], list[tuple[int, float]]]:
    pw = page.rect.width
    header_y, footer_y = _margins(page)
    lines = _raw_lines(page)
    _patch_missing_glyphs(lines, page)
    _decode_glyphs(lines)
    _rebuild_fractions(lines, page)
    _merge_script_lines(lines)
    _mark_scripts(lines)
    if ocr:
        lines = _ocr_merge(lines, ocr)

    cands = []
    ends: list[tuple[int, float]] = []     # "#####" = marca de fim de questão (texto oculto no PDF)
    for ln in lines:
        raw = "".join(c["c"] for c in ln["chars"])
        txt = _normalize_alt(_clean(_line_text(ln)), alt_style)
        b = ln["bbox"]
        if "###" in raw and header_y <= b.y0 <= footer_y:
            ends.append((_col_of(b, pw), b.y1))
        if not txt or b.y0 < header_y or b.y0 > footer_y or SUBJECT_RE.match(txt) or _unreadable(ln["chars"]):
            continue
        if PAGE_MARK_RE.match(txt) and not 0.06 < b.y0 / page.rect.height < 0.9:
            continue
        # Linhas longas atravessam a fronteira visual da página ao redor de
        # imagens laterais; classificá-las pelo centro alterna de coluna e
        # desordena enunciados e alternativas que continuam na linha seguinte.
        row_col = 0 if b.width >= pw * 0.55 else _col_of(b, pw)
        solid = [c for c in ln["chars"] if c["c"].strip()]
        if ln.get("ocr"):
            txt = OCR_LEAD_JUNK_RE.sub("", _ocr_fix_marker(txt))
        cands.append(Row(txt, b, row_col, set(ln["fonts"]),
                         n_math=sum(_is_math(c) for c in solid), n_chars=len(solid),
                         size=_ref_size(ln["chars"]), ink=pymupdf.Rect(ln.get("ink", b)),
                         lead_num=_lead_num(ln["chars"])))
    cands = _join_fragments(cands, pw)

    # Caixa "Note e adote" ao lado das alternativas (2024 Q48/Q49): cada linha da caixa
    # está na altura de uma alternativa e seria fundida a ela ("(B) 28°C Note e adote:").
    note_boxes = [p for p in parts or []
                  if any(NOTE_RE.match(c.text) and p.contains(pymupdf.Point(c.bbox.x0 + 1, c.bbox.y0 + 1))
                         for c in cands)]

    def note_box(b: pymupdf.Rect) -> int:
        center = pymupdf.Point((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
        return next((k for k, nb in enumerate(note_boxes) if nb.contains(center)), -1)

    # funde linhas na mesma altura e mesma coluna (linhas de tabela, "(A)" + célula etc.)
    cands.sort(key=lambda r: (r.col, (r.bbox.y0 + r.bbox.y1) / 2, r.bbox.x0))
    rows: list[Row] = []
    for r in cands:
        best, best_ov = None, 0.0
        for prev in rows[-6:]:
            if prev.col != r.col:
                continue
            ov = _overlap_ratio(prev.bbox, r.bbox)
            # Compara com cada pedaço já fundido: fórmulas chegam picadas em
            # várias linhas e a caixa acumulada engoliria os pedaços do meio.
            side_by_side = all(r.bbox.x0 >= b.x1 - 1 or b.x0 >= r.bbox.x1 - 1 for b, _ in prev.parts)
            # Alternativas em grade ("(A) img  (D) img") e suas legendas ("Figura A  Figura D")
            # ficam na mesma altura, mas cada uma é uma linha própria.
            label = _alt_label(r.text, r.fonts)
            if label and any(_alt_label(t, prev.fonts) for _, t in prev.parts):
                continue
            if note_boxes and note_box(prev.bbox) != note_box(r.bbox):
                continue
            if ov >= 0.5 and side_by_side and ov > best_ov:
                best, best_ov = prev, ov
        if best:
            if r.bbox.x0 < best.parts[0][0].x0:
                best.lead_num = r.lead_num
            best.parts.append((r.bbox, r.text))
            best.parts.sort(key=lambda p: p[0].x0)
            text = best.parts[0][1]
            for (lb, _), (rb, t) in zip(best.parts, best.parts[1:]):
                text += ("" if rb.x0 - lb.x1 < 1.5 else " ") + t
            best.text = _clean(text)
            best.bbox |= r.bbox
            best.fonts |= r.fonts
            best.n_math += r.n_math
            best.ink |= r.ink
            best.n_chars += r.n_chars
        else:
            r.parts = [(pymupdf.Rect(r.bbox), r.text)]
            rows.append(r)
    return rows, ends


def _alt_label(text: str, fonts: set[str]) -> bool:
    """Letra de alternativa ou legenda "Figura A"; no OCR vale a letra borrada ("D)", "(D")."""
    return bool(ALT_RE.match(text) or ALT_CAPTION_RE.match(text)
                or fonts == {"OCR"} and ocr_mod.SIDE_ALT_RE.match(text.split(" ", 1)[0]))


def _merge_rects(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[pymupdf.Rect] = []
        for r in rects:
            for k, o in enumerate(out):
                grown = pymupdf.Rect(o.x0 - MERGE_TOL, o.y0 - MERGE_TOL, o.x1 + MERGE_TOL, o.y1 + MERGE_TOL)
                if grown.intersects(r):
                    # Rect não tem |= no lugar: "o |= r" só trocava a variável e o
                    # retângulo r sumia (ex.: alternativas B e D da 2022 K Q48).
                    out[k] = o | r
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def _invisible(drawing: dict) -> bool:
    """Preenchimento branco sem contorno: some contra o papel."""
    fill = drawing.get("fill")
    return (drawing.get("type") == "f" and fill is not None and len(fill) >= 3
            and min(fill[:3]) >= 0.98)


def _figure_parts(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Imagens e grupos de desenho que podem ser figura, antes de juntar os vizinhos."""
    rects: list[pymupdf.Rect] = []
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"])
        if r.width <= TINY_W and r.height <= TINY_H:
            continue                                  # glifo faltando, tratado em _patch
        if r.height < SEP_MAX_H and r.width > 60:
            continue                                  # separador colorido entre questões
        rects.append(r)
    # Fundo branco atrás do texto (2023 K Q28: um retângulo por linha) não desenha nada
    # e transformava o enunciado em figura.
    drawings = [d for d in page.get_drawings() if not _invisible(d)]
    for r in page.cluster_drawings(drawings=drawings) if drawings else ():
        r = pymupdf.Rect(r)
        if r.width < 4 or r.height < 4:
            continue                                  # linha divisória de coluna / filete
        if r.width >= page.rect.width * 0.9 and r.height >= page.rect.height * 0.85:
            continue                                  # moldura/contorno de página, não uma figura da questão
        if r.height >= page.rect.height * 0.6:
            continue                                  # moldura/fio de coluna inteira (ex.: 2019 Q32)
        if r.height < SEP_MAX_H and r.width > 40:
            continue                                  # bolinhas do separador
        rects.append(r)
    return [r for r in rects if r.y0 >= _margins(page)[0] - 5]


def _figure_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    return _merge_rects(_figure_parts(page))


def _mark_layout(rows: list[Row], pw: float) -> None:
    """Marca fórmulas em destaque e fins de parágrafo, relativos às margens da coluna."""
    for col in (0, 1):
        narrow = [r for r in rows if r.col == col and r.bbox.width < pw * 0.55]
        if len(narrow) < 3:
            continue
        lefts = sorted(r.bbox.x0 for r in narrow)
        rights = sorted(r.bbox.x1 for r in narrow)
        left, right = lefts[len(lefts) // 10], rights[len(rights) * 9 // 10]
        # Coluna sem justificação (provas antigas): quase toda linha termina antes da
        # margem e o fim curto deixa de indicar parágrafo.
        prose = [r for r in narrow if r.n_chars >= 25 and r.n_math < 0.3 * r.n_chars
                 and r.bbox.x0 - left <= 12]
        ragged = len(prose) >= 5 and sum(r.bbox.x1 < right - 20 for r in prose) > 0.4 * len(prose)
        for r in narrow:
            r.indent = r.bbox.x0 - left > 12
            # Linha centralizada ou alinhada à direita (fonte, legenda) é um bloco à parte.
            r.short = ((not ragged and r.bbox.x1 < right - 20)
                       or r.bbox.x0 - left > 0.25 * (right - left))
            r.display = (r.n_chars > 0 and r.n_math >= 0.6 * r.n_chars and r.bbox.x0 - left > 12
                         and not ALT_RE.match(r.text) and not MARKER_RE.match(r.text))


def _split_by_markers(fig: Fig, markers: list[tuple[str, pymupdf.Rect]],
                      parts: list[pymupdf.Rect]) -> list[Fig]:
    """
    Fatia uma figura apontada por várias letras. Usa as peças originais (cada imagem
    ou desenho antes da fusão) que cobrem a altura de cada letra; se não der para
    separar assim, corta pela altura das letras.
    """
    inner = [r for r in parts if fig.bbox.contains(r)]
    owned: dict[str, list[pymupdf.Rect]] = {}
    for letter, mb in markers:
        cy = (mb.y0 + mb.y1) / 2
        owned[letter] = [r for r in inner if r.y0 - 2 <= cy <= r.y1 + 2]
    claimed = [id(r) for rs in owned.values() for r in rs]
    out: list[Fig] = []
    if all(owned.values()) and len(claimed) == len(set(claimed)):
        for letter, rs in owned.items():
            box = pymupdf.Rect(rs[0])
            for r in rs[1:]:
                box |= r
            out.append(Fig(box, fig.col, alt=letter))
        first = min(r.y0 for rs in owned.values() for r in rs)
        above = [r for r in inner if id(r) not in set(claimed) and r.y1 <= first + 2]
        if above:                                     # figura do enunciado colada às alternativas
            box = pymupdf.Rect(above[0])
            for r in above[1:]:
                box |= r
            out.append(Fig(box, fig.col))
        return out
    for k, (letter, mb) in enumerate(markers):
        y0 = fig.bbox.y0 if k == 0 else mb.y0 - 4
        y1 = markers[k + 1][1].y0 - 4 if k + 1 < len(markers) else fig.bbox.y1
        out.append(Fig(pymupdf.Rect(fig.bbox.x0, y0, fig.bbox.x1, y1), fig.col, alt=letter))
    return out


def _attach_side_figures(rows: list[Row], figs: list[Fig], parts: list[pymupdf.Rect]) -> set[int]:
    """
    Alternativas desenhadas (FUVEST 2026 Q05, 2024 Q50, 2020 Q08): "(A)" sozinho à
    esquerda da imagem, às vezes com "Figura A" logo abaixo. Na ordem de leitura a
    imagem vem antes ou junto da letra, então a ligação é feita aqui pela posição.
    Altera ``figs`` no lugar e devolve os ids das legendas, que não entram no texto.
    """
    hits: dict[int, list[tuple[str, pymupdf.Rect]]] = {}
    letters = [r for r in rows if (m := ALT_RE.match(r.text)) and not m.group(2).strip()]
    for r in rows:
        m = ALT_RE.match(r.text)
        if not m or m.group(2).strip():
            continue
        cy = (r.bbox.y0 + r.bbox.y1) / 2
        # Em grade, a imagem da letra vai só até a próxima letra da mesma linha ("(A) img (D) img").
        limit = min((o.bbox.x0 for o in letters if o is not r and o.col == r.col and o.bbox.x0 > r.bbox.x1
                     and abs((o.bbox.y0 + o.bbox.y1) / 2 - cy) <= 10), default=float("inf"))
        center = pymupdf.Point((r.bbox.x0 + r.bbox.x1) / 2, (r.bbox.y0 + r.bbox.y1) / 2)
        host = next((f for f in figs if f.bbox.contains(center)), None)
        if host:
            # Várias letras dentro da figura já foram fatiadas; uma só, no canto de cima
            # da moldura da própria alternativa (2023 K Q44), é a dona da figura.
            if host.alt is None and r.bbox.y0 - host.bbox.y0 < 20 and r.bbox.x0 - host.bbox.x0 < 20:
                host.alt = m.group(1)
            continue
        side = [f for f in figs if f.alt is None and f.col == r.col
                and f.bbox.y0 - 2 <= center.y <= f.bbox.y1 + 2 and f.bbox.x0 >= r.bbox.x1 - 2
                and f.bbox.x1 <= limit + 2]
        # A imagem mais próxima precisa estar colada à letra; as demais da mesma faixa
        # (tabela de alternativas com duas imagens por linha) vêm junto.
        if not side or min(f.bbox.x0 for f in side) - r.bbox.x1 > SIDE_FIG_GAP:
            continue
        for f in side:
            hits.setdefault(id(f), []).append((m.group(1), pymupdf.Rect(r.bbox)))

    result: list[Fig] = []
    for f in figs:
        markers = sorted(hits.get(id(f), []), key=lambda lm: lm[1].y0)
        if not markers:
            result.append(f)
        elif len(markers) == 1:
            f.alt = markers[0][0]
            result.append(f)
        else:
            result.extend(_split_by_markers(f, markers, parts))
    # Uma letra com pedaços em mais de uma imagem da mesma faixa vira um recorte só.
    # Só junta na mesma coluna e altura: a outra coluna (ou outra questão) tem seu próprio "(A)".
    kept: list[Fig] = []
    for f in result:
        twin = next((k for k in kept if f.alt and k.alt == f.alt and k.col == f.col
                     and _overlap_ratio(k.bbox, f.bbox) > 0.5), None)
        if twin:
            twin.bbox = twin.bbox | f.bbox
        else:
            kept.append(f)
    figs[:] = kept

    # Moldura (tabela, caixa) da alternativa que começa na linha do "(B) texto", com a letra
    # logo à esquerda (2019 Q40): na leitura ela vem antes da letra e ficaria com a anterior.
    for r in rows:
        m = ALT_RE.match(r.text)
        if not m:
            continue
        for f in figs:
            if (f.alt is None and f.col == r.col and -3 <= r.bbox.y0 - f.bbox.y0 <= 12
                    and 0 < f.bbox.x0 - r.bbox.x0 <= SIDE_FIG_GAP):
                f.alt = m.group(1)

    captions: set[int] = set()
    for f in figs:
        if not f.alt:
            continue
        for c in rows:
            cm = ALT_CAPTION_RE.match(c.text)
            if (cm and cm.group(1).upper() == f.alt and 0 <= c.bbox.y0 - f.bbox.y1 <= 15
                    and c.bbox.x1 >= f.bbox.x0 and c.bbox.x0 <= f.bbox.x1):
                captions.add(id(c))
    return captions


def _digit_groups(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, tuple[bytes, ...]]]:
    """Imagens do tamanho de um dígito, lado a lado e fora do texto: candidatas a número de questão."""
    ims = sorted(((pymupdf.Rect(i["bbox"]), i["digest"]) for i in page.get_image_info(hashes=True)
                  if DIGIT_W[0] <= i["bbox"][2] - i["bbox"][0] <= DIGIT_W[1]
                  and DIGIT_H[0] <= i["bbox"][3] - i["bbox"][1] <= DIGIT_H[1]),
                 key=lambda im: (im[0].y0, im[0].x0))
    groups: list[tuple[pymupdf.Rect, list[tuple[float, bytes]]]] = []
    for r, digest in ims:
        g = next((g for g in groups if abs(g[0].y0 - r.y0) <= 1.5
                  and -1 <= max(r.x0 - g[0].x1, g[0].x0 - r.x1) <= 3), None)
        if g:
            g[0].include_rect(r)
            g[1].append((r.x0, digest))
        else:
            groups.append((pymupdf.Rect(r), [(r.x0, digest)]))
    words = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    return [(r, tuple(d for _, d in sorted(ds, key=lambda xd: xd[0]))) for r, ds in groups
            if len(ds) <= 3 and not any(r.intersects(w) for w in words)]


def _learn_digits(doc: pymupdf.Document, pages: list[int]) -> dict[bytes, str]:
    """
    Prova de 2018: o número de cada questão é um par de imagens ("0" e "5"), sem texto.
    Como as questões vêm em ordem, o k-ésimo grupo de imagens é o número k; cada imagem
    recebe o dígito em que mais votos caem. Sem sequência coerente, devolve {}.
    """
    seq: list[tuple[bytes, ...]] = []
    for pno in pages:
        pw = doc[pno].rect.width
        seq += [ds for _, ds in sorted(_digit_groups(doc[pno]), key=lambda g: (_col_of(g[0], pw), g[0].y0))]
    if len(seq) < 9:
        return {}
    votes: dict[bytes, collections.Counter] = collections.defaultdict(collections.Counter)
    for k, ds in enumerate(seq, 1):
        label = str(k).zfill(len(ds))
        if len(label) == len(ds):
            for digest, ch in zip(ds, label):
                votes[digest][ch] += 1
    digits = {d: c.most_common(1)[0][0] for d, c in votes.items()}
    decoded = ["".join(digits.get(d, "?") for d in ds) for ds in seq]
    ordered = sum(a.isdigit() and b.isdigit() and int(b) == int(a) + 1 for a, b in zip(decoded, decoded[1:]))
    if len(set(digits.values())) < 5 or ordered < 0.8 * (len(decoded) - 1):
        return {}
    return digits


def extract_page(page: pymupdf.Page, pno: int, alt_style: str = "upper",
                 digits: dict[bytes, str] | None = None, ocr: dict | None = None) -> list[Item]:
    """``ocr``: {"status": "garbage"|"missing", "lines": linhas de ocr.as_raw_lines} para página sem texto legível."""
    pw = page.rect.width
    parts = _figure_parts(page)
    if ocr and ocr["status"] == ocr_mod.MISSING:
        # As faixas escaneadas são a página, não figuras; as figuras de verdade são
        # a tinta dentro delas que o OCR não leu como texto.
        scans = ocr_mod.scan_rects(page)
        parts = [r for r in parts if not any(_overlap_ratio(r, s) > 0.9 and (r & s).width > 0.9 * r.width
                                             for s in scans)]
        header_y, footer_y = _margins(page)
        text_boxes = [ln["bbox"] for ln in ocr["lines"] if _ocr_prose(ln)]
        parts += [r for r in ocr_mod.scan_figures(page, text_boxes, scans)
                  if r.y0 >= header_y - 5 and r.y1 <= footer_y + 5]
    rows, ends = _build_rows(page, parts, alt_style, ocr)
    for r, ds in _digit_groups(page) if digits else ():
        if all(d in digits for d in ds):
            text = "".join(digits[d] for d in ds)
            rows.append(Row(text, r, _col_of(r, pw), parts=[(pymupdf.Rect(r), text)], size=r.height))
    _mark_layout(rows, pw)

    # caixas "Note e adote": retângulo que contém uma linha começando assim. Separadas
    # antes da fusão: uma imagem encostada na caixa (2019 Q39) não pode virar nota.
    def is_note(rect: pymupdf.Rect) -> bool:
        return any(NOTE_RE.match(r.text) and rect.contains(pymupdf.Point(r.bbox.x0 + 1, r.bbox.y0 + 1))
                   for r in rows)

    note_parts = [r for r in parts if is_note(r)]
    merged = _merge_rects([r for r in parts if not is_note(r)])
    note_rects = _merge_rects(note_parts)
    fig_rects = [fr for fr in merged if not is_note(fr)]
    note_rects += [fr for fr in merged if is_note(fr)]

    figs: list[Fig] = []
    for fr in fig_rects:
        inside = [r for r in rows if fr.contains(pymupdf.Point((r.bbox.x0 + r.bbox.x1) / 2,
                                                              (r.bbox.y0 + r.bbox.y1) / 2))]
        bare = sorted((r for r in inside if (m := ALT_RE.match(r.text)) and not m.group(2).strip()),
                      key=lambda r: r.bbox.y0)
        if len(bare) >= 2:
            # figura única contendo várias alternativas desenhadas (ex.: questão 14):
            # fatia por alternativa
            first_y = bare[0].bbox.y0 - 4
            if first_y - fr.y0 > 20:   # sobra algo acima do "(A)" => figura do enunciado
                figs.append(Fig(pymupdf.Rect(fr.x0, fr.y0, fr.x1, first_y), _fig_col(fr, pw)))
            for k, mk in enumerate(bare):
                y0 = mk.bbox.y0 - 4
                y1 = (bare[k + 1].bbox.y0 - 4) if k + 1 < len(bare) else fr.y1
                letter = ALT_RE.match(mk.text).group(1)
                figs.append(Fig(pymupdf.Rect(fr.x0, y0, fr.x1, y1), _fig_col(fr, pw), alt=letter))
        else:
            figs.append(Fig(fr, _fig_col(fr, pw)))

    captions = _attach_side_figures(rows, figs, parts)

    items: list[Item] = []
    for f in figs:
        items.append(Item("fig", pno, f.col, f.bbox.y0, fig=f))

    for r in rows:
        center = pymupdf.Point((r.bbox.x0 + r.bbox.x1) / 2, (r.bbox.y0 + r.bbox.y1) / 2)
        # Em alguns PDFs oficiais o marcador numérico fica dentro da caixa de
        # uma imagem; ele continua sendo texto estrutural e não pode sumir.
        # Vale também para o número em negrito colado ao texto (2013 Q61).
        if (MARKER_RE.match(r.text) or QUESTION_WORD_RE.match(r.text) or r.lead_num is not None
                or (r.fonts == {"OCR"} and OCR_INLINE_RE.match(r.text))):
            items.append(Item("text", pno, r.col, r.bbox.y0, row=r))
            continue
        if id(r) in captions:
            continue
        if _alt_label(r.text, r.fonts) and not ALT_CAPTION_RE.match(r.text) and not any(
                nr.contains(center) for nr in note_rects):
            # linha de alternativa nunca é "texto da figura" (cobre tabelas de alternativas)
            items.append(Item("text", pno, r.col, r.bbox.y0, row=r))
            continue
        if any(nr.contains(center) for nr in note_rects):
            items.append(Item("note", pno, r.col, r.bbox.y0, row=r))
            continue
        host = next((f for f in figs if f.bbox.contains(center)), None)
        if host:
            host.texto.append(r.text)
            continue
        items.append(Item("text", pno, r.col, r.bbox.y0, row=r))

    for col, y in ends:
        items.append(Item("end", pno, col, y + 0.5))

    items.sort(key=_reading_order(items, pw))
    return _order_alt_grid(items)


def _order_alt_grid(items: list[Item]) -> list[Item]:
    """
    Alternativas do OCR em duas subcolunas ("(A) … (D) …" / "(B) … (E) …" / "(C) …"):
    a leitura por altura dá A, D, B, E, C e o OCR, que confia na ordem para corrigir
    letra borrada, renomearia D para B. No trecho da grade, a subcoluna esquerda vem
    inteira antes da direita.
    """
    def is_alt(it: Item) -> bool:
        return it.kind == "text" and it.row.fonts == {"OCR"} and _alt_label(it.row.text, it.row.fonts)

    def box(it: Item) -> pymupdf.Rect:
        return it.row.bbox if it.row else it.fig.bbox

    out = list(items)
    for col in {it.col for it in items}:
        idx = [k for k, it in enumerate(out) if it.col == col and it.kind in ("text", "fig")]
        alts = sorted((k for k in idx if is_alt(out[k])), key=lambda k: out[k].row.bbox.y0)
        pairs = [(a, b) for a in alts for b in alts
                 if out[b].row.bbox.x0 > out[a].row.bbox.x1 + 2
                 and _overlap_ratio(out[a].row.bbox, out[b].row.bbox) >= 0.5]
        # Uma grade por questão: pares separados por mais de 3 linhas são grades diferentes.
        grids: list[list[tuple[int, int]]] = []
        for a, b in sorted(pairs, key=lambda ab: out[ab[0]].row.bbox.y0):
            ra = out[a].row.bbox
            if grids and ra.y0 - out[grids[-1][-1][0]].row.bbox.y1 <= 3 * ra.height:
                grids[-1].append((a, b))
            else:
                grids.append([(a, b)])
        for grid in grids:
            lefts = [a for a, _ in grid]
            split = min(out[b].row.bbox.x0 for _, b in grid) - 1
            x_left = min(out[a].row.bbox.x0 for a in lefts)
            height = max(4.0, min(out[a].row.bbox.height for a in lefts))
            # Dos pares para baixo, enquanto a próxima letra da subcoluna esquerda vem
            # logo abaixo ("(C)" sozinha na última linha).
            top = min(out[a].row.bbox.y0 for a in lefts) - 1
            bottom = max(out[a].row.bbox.y1 for a in lefts)
            for k in alts:
                b = out[k].row.bbox
                if b.y0 > bottom and b.y0 - bottom <= 2.5 * height and abs(b.x0 - x_left) <= 2 * height:
                    bottom = b.y1
            inside = [k for k in idx if top <= box(out[k]).y0 <= bottom]
            if not inside or inside != list(range(inside[0], inside[-1] + 1)):
                continue                              # outra coisa no meio: deixa como está
            ordered = sorted(inside, key=lambda k: (box(out[k]).x0 >= split, box(out[k]).y0))
            out[inside[0]:inside[-1] + 1] = [out[k] for k in ordered]
    return out


def _ocr_prose(ln: dict) -> bool:
    """Linha do OCR que é texto mesmo (não rótulo de figura lido como letras soltas)."""
    words = "".join(ch["c"] for ch in ln["chars"]).split()
    return ln.get("conf", 0) >= 55 and (len(words) >= 3 or any(len(w) >= 4 for w in words))


def _reading_order(items: list[Item], pw: float):
    """
    Chave de ordenação: coluna esquerda inteira, depois a direita — exceto nas faixas
    de largura total (2015 Q13: duas colunas em cima e uma questão de página inteira
    embaixo). Cada faixa é lida de cima para baixo depois das colunas acima dela.
    """
    def box(it: Item) -> pymupdf.Rect | None:
        return it.row.bbox if it.row else it.fig.bbox if it.fig else None

    right = [b for it in items if it.col == 1 and (b := box(it))]
    full = sorted((it.row.bbox for it in items if it.row and it.row.bbox.width >= FULL_ROW_W * pw),
                  key=lambda b: b.y0)
    groups: list[list[pymupdf.Rect]] = []
    for b in full:
        # Duas linhas largas ficam na mesma faixa se nada da coluna direita aparece entre elas.
        if groups and not any(groups[-1][-1].y1 - 2 < (r.y0 + r.y1) / 2 < b.y0 + 2 for r in right):
            groups[-1].append(b)
        else:
            groups.append([b])
    bands: list[tuple[float, float]] = []
    prev = 0.0
    for g in groups:
        if len(g) < 2:
            continue
        y0, y1 = g[0].y0, max(b.y1 for b in g)
        if any(r.y0 < y1 - 2 and r.y1 > y0 + 2 for r in right):
            continue                                  # coluna direita ao lado: não é página inteira
        # A faixa começa onde termina a coluna direita acima dela (o número da questão
        # e a figura de abertura ficam na esquerda, logo acima da primeira linha larga).
        top = max((r.y1 for r in right if prev <= r.y1 <= y0 + 2), default=prev)
        bands.append((top, y1))
        prev = y1

    def key(it: Item):
        for k, (top, bot) in enumerate(bands):
            if it.y0 < top:
                return (2 * k, it.col, it.y0)
            if it.y0 <= bot + 1:
                return (2 * k + 1, 0, it.y0)
        return (2 * len(bands), it.col, it.y0)
    return key


# --------------------------------------------------------------- montagem
def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not out:
            out = p
        elif out.endswith("-"):          # "tornando-" + "a" -> "tornando-a"
            out += p
        else:
            out += " " + p
    return out.strip()


def new_question(num: int, page: int) -> dict:
    return {
        "numero": num, "pagina": page + 1, "texto_id": None,
        "enunciado": [], "nota": [], "alternativas": {},
        "figuras_enunciado": [], "imagens_completas": [], "revisar": [],
        "_segs": {}, "_fonts": set(), "_stem": [], "_short": False, "_size": 0.0,
    }


def _alt_style(doc: pymupdf.Document, texts: dict[int, str] | None = None) -> str:
    """Como a prova marca as alternativas: "upper" para "(A)" (FUVEST), "lower" para "a)"
    (FUVEST até 2017), "paren" para "A)" e "dot" para "A." (outras provas e listas)."""
    counts = dict.fromkeys(("upper", *ALT_STYLES), 0)
    for pno, page in enumerate(doc):
        text = texts[pno] if texts and pno in texts else page.get_text("text")
        for line in text.splitlines():
            line = line.strip()
            counts["upper"] += bool(ALT_RE.match(line))
            for style, pattern in ALT_STYLES.items():
                counts[style] += bool(pattern.match(line))
    # Empate fica com o formato FUVEST; os outros só valem com maioria.
    best = max(ALT_STYLES, key=lambda style: counts[style])
    return best if counts[best] > counts["upper"] else "upper"


def _inline_marker(t: str, expected: int) -> str | None:
    """Resto da linha quando ela abre a questão ``expected`` com o número colado ao texto."""
    m = INLINE_MARKER_RE.match(t)
    if not m or int(m.group(1)) != expected:
        return None
    return (m.group(2) or "").strip()


OCR_SKIP = 3     # números que o OCR pode ter perdido antes de um número lido certo


def _ocr_marker(row: Row, expected: int) -> tuple[int, str] | None:
    """
    Número de questão lido pelo OCR ("K.1? – texto"): com a letra do dia e o travessão
    a linha só pode abrir questão, então aceita até um caractere errado no número.
    Se o OCR perdeu a linha de uma questão, um número lido certo logo adiante
    (até ``OCR_SKIP``) retoma a sequência em vez de travar a prova ali (1997 K.71).
    """
    if row.fonts != {"OCR"}:
        return None
    m = OCR_MARKER_RE.match(row.text)
    want = f"{expected:02d}"
    if not m or len(want) != 2:
        return None
    rest, token = (m.group(3) or "").strip(), m.group(2)
    close = sum(a != b for a, b in zip(token, want)) <= 1
    if token.isdigit():
        n = int(token)
        # Número lido inteiro à frente: o OCR perdeu os do meio. Atrás e quase igual
        # ("15" depois da 17): dígito trocado.
        if expected < n <= expected + OCR_SKIP:
            return n, rest
        return (expected, rest) if n == expected or (n < expected and close) else None
    return (expected, rest) if close else None


def _ocr_alt(row: Row, cur: dict, mode: str) -> tuple[str, str] | None:
    """
    Alternativa numa linha do OCR com a letra borrada. As alternativas vêm em ordem,
    então a letra é a próxima esperada. Antes da primeira alternativa vale só a forma
    com fecho ("(A)", "A)") ou com o que segue sem cara de palavra ("(Adaptado)" é texto).
    """
    if row.fonts != {"OCR"} or NOTE_RE.match(row.text):
        return None
    m = OCR_ALT_RE.match(row.text)
    if not m:
        return None
    letters = sorted(cur["alternativas"])
    want = chr(ord(letters[-1]) + 1) if letters else "A"
    if want > "E":
        return None
    read, closed, rest = (m.group(1) or m.group(2)).upper(), bool(m.group(3)) or bool(m.group(2)), m.group(4)
    if mode != "alts":
        if read != want or not (closed or (rest[:1] and not rest[:1].islower())):
            return None
    elif not closed and read.isalpha() and rest[:1].islower() and (m.group(1) or m.group(2)) not in "ABCDEQJGO":
        return None
    return want, rest.strip()


def _order_by_markers(items: list[Item]) -> list[Item]:
    """
    Questão no alto da coluna direita ao lado de uma figura larga da esquerda (2005
    Q45): a leitura por coluna traz a 46, que está embaixo à esquerda, antes da 45.
    Quando os números da página formam uma sequência sem buracos e estão fora de
    ordem, cada bloco (do número até o próximo) é reposicionado pelo número.
    """
    nums: list[int | None] = []
    for it in items:
        m = (MARKER_RE.match(it.row.text) or QUESTION_WORD_RE.match(it.row.text)) if it.kind == "text" else None
        nums.append(int(m.group(1)) if m else it.row.lead_num if it.kind == "text" else None)
    found = [n for n in nums if n is not None]
    if (len(found) < 2 or found == sorted(found) or len(set(found)) != len(found)
            or max(found) - min(found) != len(found) - 1):
        return items
    blocks: list[tuple[int, list[Item]]] = [(-1, [])]
    for it, n in zip(items, nums):
        if n is not None:
            blocks.append((n, []))
        blocks[-1][1].append(it)
    return [it for _, block in sorted(blocks, key=lambda b: b[0]) for it in block]


def _ocr_pages(doc: pymupdf.Document, engine, progress=None) -> dict[int, dict]:
    """
    OCR das páginas sem texto legível. Sem motor (Tesseract ausente, STUDAI_OCR=0)
    ou se o motor falhar, a página segue só com a camada de texto, como antes.
    """
    if engine is None:
        return {}
    todo = [(pno, st) for pno in range(len(doc))
            if (st := ocr_mod.text_layer_status(doc[pno])) != ocr_mod.OK]
    out: dict[int, dict] = {}
    for k, (pno, status) in enumerate(todo, 1):
        if progress:
            progress({"stage": "extracting", "message": f"OCR da página {pno + 1} ({k}/{len(todo)})",
                      "page": pno + 1, "completed": k - 1, "total": len(todo), "unit": "páginas"})
        page = doc[pno]
        dpi = ocr_mod.ocr_dpi(page, status)
        try:
            lines = engine(page, dpi)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("OCR indisponível (%s); páginas sem texto ficam para revisão", exc)
            return out
        out[pno] = {"status": status, "dpi": dpi, "ocr_lines": lines}
        _ocr_refresh(out[pno])
    return out


def _ocr_refresh(page_ocr: dict) -> None:
    raw = ocr_mod.as_raw_lines(page_ocr["ocr_lines"])
    page_ocr["lines"] = raw
    page_ocr["text"] = "\n".join("".join(ch["c"] for ch in ln["chars"]) for ln in raw)


OCR_RETRY_PSM = 11       # texto esparso: acha a letra de alternativa ao lado de fração (2021 Q46)


def _alt_letter(word: str) -> str | None:
    """Letra de "(D)", "D)", "(d" etc.; None se a palavra não é letra de alternativa."""
    if not ocr_mod.SIDE_ALT_RE.match(word):
        return None
    return (word[1] if word[0] in "([{" else word[0]).upper()


def _recover_alternatives(doc: pymupdf.Document, engine, ocr_pages: dict[int, dict],
                          questions: dict[int, dict]) -> bool:
    """
    Segunda passada nas questões do OCR que ficaram sem alguma alternativa. Lida em
    coluna (--psm 4), a letra ao lado de uma fração empilhada some ou vira outra coisa
    ("5)" no lugar de "(C)", 2021 Q47, e a D virava C); com --psm 11 no trecho da
    questão ela aparece. Cada letra que falta, lida assim, entra no lugar das palavras
    que cobre, a não ser que ali já houvesse uma letra; sem nada embaixo, vira uma
    linha nova com as palavras que o OCR ainda não tinha. Devolve True se mudou algo.
    """
    read = getattr(engine, "read", None)
    if read is None:
        return False
    changed = False
    for q in questions.values():
        missing = set("ABCDE") - set(q["alternativas"])
        if "OCR" not in q["_fonts"] or not missing:
            continue
        for (pno, _col), (y0, y1, x0, x1) in sorted(q["_segs"].items()):
            page_ocr = ocr_pages.get(pno)
            if not page_ocr:
                continue
            try:
                # Embaixo, uma linha a mais: a última letra pode ter ficado fora do trecho (2021 Q46 E).
                found = read(doc[pno], page_ocr["dpi"], pymupdf.Rect(x0 - 4, y0 - 4, x1 + 4, y1 + 14), OCR_RETRY_PSM)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("Segunda passada do OCR falhou (%s)", exc)
                return changed
            lines = page_ocr["ocr_lines"]
            for ln in found:
                letter = _alt_letter(ln.words[0].text) if ln.words else None
                if not letter:
                    continue
                marker = ln.words[0]

                def covered(w: ocr_mod.OcrWord) -> bool:
                    return (w.bbox & marker.bbox).get_area() >= 0.3 * max(1e-6, w.bbox.get_area())

                hits = [(h, w) for h in lines for w in h.words if covered(w)]
                if hits:
                    # Uma letra já lida ali fica (mesmo outra: trocá-la apagaria uma alternativa).
                    host, old = max(hits, key=lambda hw: (hw[1].bbox & marker.bbox).get_area())
                    if _alt_letter(old.text) or letter not in missing:
                        continue
                    host.words = sorted([w for w in host.words if not covered(w)] + [marker], key=lambda w: w.bbox.x0)
                    host.bbox = pymupdf.Rect()
                    host.__post_init__()
                elif letter in missing:
                    known = [w.bbox for h in lines for w in h.words]
                    extra = [w for w in ln.words[1:] if not any(w.bbox.intersects(b) for b in known)]
                    lines.append(ocr_mod.OcrLine([marker, *extra]))
                else:
                    continue
                missing.discard(letter)
                changed = True
            _ocr_refresh(page_ocr)
    return changed


def parse(doc: pymupdf.Document, ocr="auto", progress=None):
    """
    Questões e textos compartilhados da prova. ``ocr``: motor de OCR
    (``ocr.OcrEngine``), "auto" para o Tesseract instalado, ou None para não usar.
    """
    engine = ocr_mod.default_engine() if ocr == "auto" else ocr
    ocr_pages = _ocr_pages(doc, engine, progress)
    questions, shared = _parse_pages(doc, ocr_pages, progress)
    if ocr_pages and _recover_alternatives(doc, engine, ocr_pages, questions):
        questions, shared = _parse_pages(doc, ocr_pages)
    return questions, shared


def _parse_pages(doc: pymupdf.Document, ocr_pages: dict[int, dict], progress=None):
    """Máquina de estados sobre as páginas (com as linhas do OCR já lidas)."""
    questions: dict[int, dict] = {}
    shared: dict[str, dict] = {}
    cur: dict | None = None
    cur_alt: str | None = None
    mode = "none"                      # none | stem | alts | shared
    active_shared: str | None = None
    expected = 1
    texts = {pno: o["text"] for pno, o in ocr_pages.items()}
    alt_style = _alt_style(doc, texts)
    pages = []
    for pno in range(len(doc)):
        text = texts.get(pno) or doc[pno].get_text()
        cover = COVER_RE.search(text) or (pno in (0, len(doc) - 1) and COVER_TITLE_RE.search(text))
        draft = DRAFT_RE.search(text) and len(text.split()) < 40
        if not (cover or draft or len(KEY_ENTRY_RE.findall(text)) >= 20):
            pages.append(pno)
    digits = _learn_digits(doc, pages)

    def touch(q, it):
        seg = q["_segs"].setdefault((it.page, it.col), [1e9, -1e9, 1e9, -1e9])
        b = it.row.bbox if it.row else it.fig.bbox
        seg[0], seg[1] = min(seg[0], b.y0), max(seg[1], b.y1)
        seg[2], seg[3] = min(seg[2], b.x0), max(seg[3], b.x1)

    for page_index, pno in enumerate(pages, 1):
        if progress:
            progress({"stage": "extracting", "message": f"Lendo página {page_index} de {len(pages)} · OCR e identificação das questões",
                      "completed": page_index - 1, "total": len(pages), "unit": "páginas"})
        for it in _order_by_markers(extract_page(doc[pno], pno, alt_style, digits, ocr_pages.get(pno))):
            if it.kind == "end":
                if mode != "shared":       # fim da questão: ignora o que vier até o próximo marcador
                    cur, cur_alt, mode = None, None, "none"
                continue
            if it.kind == "text":
                t = it.row.text

                m = SHARED_RE.match(t)
                if m:
                    a, b = int(m.group(1)), int(m.group(2))
                    key = f"{a}-{b}"
                    shared[key] = {"id": key, "questoes": list(range(a, b + 1)),
                                   "texto": [], "figuras": [], "_figrects": []}
                    active_shared, mode, cur, cur_alt = key, "shared", None, None
                    continue

                mk = MARKER_RE.match(t)
                rest = None
                qm = QUESTION_WORD_RE.match(t)
                word_num = int(qm.group(1)) if qm else None
                # Número colado ao texto abre questão quando vem destacado (negrito ou
                # corpo maior) ou quando a anterior já chegou à alternativa E: "12 frascos..."
                # no meio de um enunciado não é marcador.
                if qm and (word_num == expected or not questions or expected < word_num <= expected + OCR_SKIP):
                    # "QUESTÃO 91": a primeira questão pode ter qualquer número (ENEM dia 2, listas).
                    mk, rest, expected = None, qm.group(2).strip(), word_num
                elif not (mk and int(mk.group(1)) == expected) and (
                        it.row.lead_num == expected
                        or cur is None or mode != "stem" and "E" in cur["alternativas"]
                        # OCR não sabe o negrito: "02 Considere..." (2002 dia 2) abre questão
                        # quando o número esperado vem seguido de palavra em maiúscula.
                        or it.row.fonts == {"OCR"} and OCR_INLINE_RE.match(t)):
                    rest = _inline_marker(t, expected)
                num = expected
                if not (mk and int(mk.group(1)) == expected) and rest is None:
                    if found := _ocr_marker(it.row, expected):
                        num, rest = found
                    elif it.row.fonts == {"OCR"} and OCR_INLINE_RE.match(t):
                        # "09 A construção..." com a 08 perdida pelo OCR (dentro de figura):
                        # um número logo adiante retoma a sequência.
                        n = int(INLINE_MARKER_RE.match(t).group(1))
                        if expected < n <= expected + OCR_SKIP:
                            num, rest = n, _inline_marker(t, n)
                if (mk and int(mk.group(1)) == expected) or rest is not None:
                    expected = num + 1
                    cur = questions[num] = new_question(num, pno)
                    cur_alt, mode = None, "stem"
                    if active_shared and num in shared[active_shared]["questoes"]:
                        cur["texto_id"] = active_shared
                    else:
                        active_shared = None
                    touch(cur, it)
                    if rest:
                        cur["_fonts"] |= it.row.fonts
                        cur["enunciado"].append(rest)
                        cur["_stem"].append({"text": rest, "page": it.page, "bbox": it.row.ink or it.row.bbox,
                                             "display": False, "para": True})
                        cur["_short"], cur["_size"] = it.row.short, it.row.size
                    continue

                if mode == "shared":
                    shared[active_shared]["texto"].append(t)
                elif cur is not None:
                    touch(cur, it)
                    cur["_fonts"] |= it.row.fonts
                    am = ALT_RE.match(t)
                    alt = (am.group(1), am.group(2)) if am else _ocr_alt(it.row, cur, mode)
                    if am and it.row.fonts == {"OCR"} and am.group(1) in cur["alternativas"]:
                        alt = _ocr_alt(it.row, cur, mode) or alt    # "(B)" lido no lugar de "(E)"
                    if alt:
                        cur_alt, mode = alt[0], "alts"
                        cur["alternativas"][cur_alt] = {"texto": [alt[1]] if alt[1] else [],
                                                        "figuras": []}
                    elif mode == "alts" and cur_alt:
                        cur["alternativas"][cur_alt]["texto"].append(t)
                    else:
                        cur["enunciado"].append(t)
                        cur["_stem"].append({"text": t, "page": it.page, "bbox": it.row.ink or it.row.bbox,
                                             "display": it.row.display,
                                             "para": (cur["_short"] or it.row.indent
                                                      or abs(it.row.size - cur["_size"]) > 0.6)})
                        cur["_short"], cur["_size"] = it.row.short, it.row.size

            elif it.kind == "note" and cur is not None:
                touch(cur, it)
                cur["nota"].append(it.row.text)

            elif it.kind == "fig":
                if mode == "shared":
                    shared[active_shared]["_figrects"].append((it.page, it.fig.bbox))
                elif cur is not None:
                    touch(cur, it)
                    letter = it.fig.alt or (cur_alt if mode == "alts" else None)
                    cur["_short"] = True             # texto depois de figura começa parágrafo novo
                    cur.setdefault("_figs", []).append((it.page, it.fig.bbox, letter))
        if progress:
            progress({"stage": "extracting", "message": f"Página {page_index} de {len(pages)} lida",
                      "completed": page_index, "total": len(pages), "unit": "páginas"})
    return questions, shared


# ------------------------------------------------------------- recortes / saída
def crop(doc, pno: int, rect: pymupdf.Rect, path: Path, dpi: int):
    r = pymupdf.Rect(rect.x0 - CROP_PAD, rect.y0 - CROP_PAD, rect.x1 + CROP_PAD, rect.y1 + CROP_PAD)
    r &= doc[pno].rect
    doc[pno].get_pixmap(dpi=dpi, clip=r).save(str(path))


def _stem_blocks(doc, stem: list[dict], imgdir: Path, n: str, dpi: int) -> list[dict]:
    """
    Mantém a estrutura do enunciado: parágrafos e fórmulas em destaque viram blocos
    próprios. A fórmula leva o texto reconstruído e o recorte dela no PDF.
    """
    blocks: list[dict] = []
    for e in stem:
        last = blocks[-1] if blocks else None
        if e["display"]:
            if (last and last["tipo"] == "formula" and last["_page"] == e["page"]
                    and e["bbox"].y0 - last["_rect"].y1 < 1.6 * e["bbox"].height):
                last["_parts"].append(e["text"])
                last["_rect"] |= e["bbox"]
            else:
                blocks.append({"tipo": "formula", "_parts": [e["text"]], "_page": e["page"],
                               "_rect": pymupdf.Rect(e["bbox"])})
        elif last and last["tipo"] == "texto" and (not e["para"] or last["_parts"][-1].endswith("-")):
            last["_parts"].append(e["text"])
        else:
            blocks.append({"tipo": "texto", "_parts": [e["text"]]})
    out, k = [], 0
    for b in blocks:
        if b["tipo"] == "texto":
            out.append({"tipo": "texto", "texto": _join(b["_parts"])})
            continue
        k += 1
        name = f"q{n}_formula{k}.png"
        r = b["_rect"]
        crop(doc, b["_page"], pymupdf.Rect(r.x0 - 4, r.y0 - 2, r.x1 + 4, r.y1 + 2), imgdir / name, dpi)
        out.append({"tipo": "formula", "texto": "\n".join(b["_parts"]), "imagem": f"img/{name}"})
    return out


def finalize(doc, questions, shared, outdir: Path, dpi: int, full_crops: bool,
             detailed_crops: bool = False, progress=None):
    imgdir = outdir / "img"
    imgdir.mkdir(parents=True, exist_ok=True)
    pw = doc[0].rect.width
    colx = {0: (30.0, pw / 2 - 4), 1: (pw / 2 + 10, pw - 28.0)}

    for key, s in shared.items():
        for k, (p, r) in enumerate(s.pop("_figrects"), 1):
            name = f"texto_{key}_fig{k}.png"
            crop(doc, p, r, imgdir / name, dpi)
            s["figuras"].append(f"img/{name}")
        s["texto"] = _join(s["texto"])

    for question_index, (num, q) in enumerate(questions.items(), 1):
        if progress:
            progress({"stage": "rendering", "message": f"Preparando recortes da questão {num} · {question_index} de {len(questions)}",
                      "completed": question_index - 1, "total": len(questions), "unit": "questões"})
        n = f"{num:02d}"
        kE = 0
        for p, r, letter in q.pop("_figs", []):
            if letter and letter in q["alternativas"]:
                alt = q["alternativas"][letter]
                name = f"q{n}_alt{letter}{len(alt['figuras']) + 1 if alt['figuras'] else ''}.png"
                crop(doc, p, r, imgdir / name, dpi)
                alt["figuras"].append(f"img/{name}")
            else:
                kE += 1
                name = f"q{n}_fig{kE}.png"
                crop(doc, p, r, imgdir / name, dpi)
                q["figuras_enunciado"].append(f"img/{name}")

        if full_crops:
            if detailed_crops:
                # A revisão detalhada preserva toda a geometria da questão:
                # parte da coluna e só alarga onde o próprio conteúdo é mais
                # largo (imagem, legenda ou alternativas que cruzam a página).
                # A página inteira traria junto a questão da outra coluna.
                for k, ((p, col), (y0, y1, x0, x1)) in enumerate(sorted(q["_segs"].items()), 1):
                    name = f"q{n}_completa_detalhada{k}.png"
                    cx0, cx1 = colx[col]
                    rect = pymupdf.Rect(max(22, min(cx0, x0 - 3)), y0 - 3,
                                        min(doc[p].rect.width - 22, max(cx1, x1 + 3)), y1 + 3)
                    crop(doc, p, rect, imgdir / name, dpi)
                    q["imagens_completas"].append(f"img/{name}")
            else:
                for k, ((p, col), (y0, y1, sx0, sx1)) in enumerate(sorted(q["_segs"].items()), 1):
                    x0, x1 = colx[col]
                    # Faixa de página inteira (comando e alternativas de 2025 Q1): o recorte
                    # da coluna cortava o fim das linhas; alarga até o conteúdo.
                    x0 = max(22.0, min(x0, sx0 - 3))
                    x1 = min(doc[p].rect.width - 22, max(x1, sx1 + 3))
                    name = f"q{n}_completa{k}.png"
                    crop(doc, p, pymupdf.Rect(x0, y0 - 3, x1, y1 + 3), imgdir / name, dpi)
                    q["imagens_completas"].append(f"img/{name}")

        q["blocos"] = _stem_blocks(doc, q.pop("_stem"), imgdir, n, dpi)
        q.pop("_short")
        q.pop("_size")
        q["enunciado"] = "\n".join(b["texto"] for b in q["blocos"]) if q["blocos"] else _join(q["enunciado"])
        q["nota"] = _join(q["nota"])
        # Em grade, a leitura por linha dá A, D, B, E, C.
        q["alternativas"] = dict(sorted(q["alternativas"].items()))
        for a in q["alternativas"].values():
            a["texto"] = _join(a["texto"])

        # ---- sinalizadores p/ revisão humana
        if any(any(m in f.lower() for m in MATH_FONTS) for f in q["_fonts"]):
            q["revisar"].append("formula_matematica")
        blob = q["enunciado"] + "".join(a["texto"] for a in q["alternativas"].values())
        if CORRUPT_GLYPH_RE.search(blob):
            q["revisar"].append("glifo_estranho")
        if sorted(q["alternativas"]) != list("ABCDE"):
            q["revisar"].append("alternativas_incompletas")
        # Também com figura: enunciado lido como figura (2023 K Q28, 2026 Q42) passava sem aviso.
        if not q["enunciado"].strip():
            q["revisar"].append("enunciado_vazio")
        if any(not a["texto"] and not a["figuras"] for a in q["alternativas"].values()):
            q["revisar"].append("alternativa_vazia")

        q.pop("_segs")
        q.pop("_fonts")
        q["resposta"] = None  # o caderno não traz gabarito; preencher depois
        if progress:
            progress({"stage": "rendering", "message": f"Recortes prontos · questão {num} de {len(questions)}",
                      "completed": question_index, "total": len(questions), "unit": "questões"})


def extract_pdf(input_pdf: Path, outdir: Path, *, dpi: int = 200, full_crops: bool = True,
                detailed_crops: bool = False, progress=None, ocr="auto") -> Path:
    """Extrai um PDF e grava questoes.json e imagens dentro de outdir."""
    input_pdf = input_pdf.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(input_pdf)
    try:
        if progress:
            progress({"stage": "extracting", "message": f"Lendo PDF · {len(doc)} páginas", "pages": len(doc)})
        questions, shared = parse(doc, ocr=ocr, progress=progress)
        if progress:
            progress({"stage": "rendering", "message": f"Extraindo imagens de {len(questions)} questões", "questions": len(questions)})
        finalize(doc, questions, shared, outdir, dpi, full_crops, detailed_crops, progress)
        data = {
            "prova": input_pdf.stem,
            "arquivo_origem": input_pdf.name,
            "total_questoes": len(questions),
            "textos_compartilhados": shared,
            "questoes": list(questions.values()),
        }
        output = outdir / "questoes.json"
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress:
            progress({"stage": "extracted", "message": f"Extração concluída · {len(questions)} questões", "questions": len(questions)})
        return output
    finally:
        doc.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", type=Path, help="arquivo PDF da prova")
    ap.add_argument("-o", "--outdir", type=Path, help="pasta de saída (default: <pasta do PDF>/<nome do PDF>)")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--no-full-crops", action="store_true")
    args = ap.parse_args()
    if not args.pdf.is_file():
        ap.error(f"PDF não encontrado: {args.pdf}")
    outdir = args.outdir or args.pdf.parent / args.pdf.stem
    output = extract_pdf(args.pdf, outdir, dpi=args.dpi, full_crops=not args.no_full_crops)
    print(f"Extração concluída: {output} ({json.loads(output.read_text(encoding='utf-8'))['total_questoes']} questões)")


if __name__ == "__main__":
    main()
