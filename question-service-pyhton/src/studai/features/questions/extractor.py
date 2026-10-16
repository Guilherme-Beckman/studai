##!/usr/bin/env python3
"""
Worker de ingestão de provas (FUVEST / ENEM) em PDF -> JSON + recortes de figuras.

Uso:
    python extract.py prova.pdf -o simulados/processados/prova/
    python processar_simulados.py simulados/

Saída:
    <pasta do simulado>/questoes.json  questões estruturadas
    <pasta do simulado>/img/*.png      figuras e recortes das questões

Ideia: o PDF tem camada de texto, então NÃO fazemos OCR. Lemos linhas e figuras
com coordenadas, ordenamos por coluna (esquerda inteira, depois direita) e rodamos
uma máquina de estados: número da questão abre uma nova questão, "(A)".."(E)" abrem
alternativas, "Texto para as questões X e Y" abre um texto compartilhado.

Dependência: pip install pymupdf
"""
from __future__ import annotations

import argparse
import colorsys
import json
import re
import unicodedata

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf  # pip install pymupdf

# ----------------------------------------------------------------- constantes
HEADER_Y = 35.0        # linhas acima disso são cabeçalho ("Simulado da Prova...")
FOOTER_Y = 805.0
SEP_MAX_H = 12.0       # imagens/desenhos mais baixos que isso e largos = separador colorido
TINY_W, TINY_H = 15.0, 18.0   # imagens minúsculas = glifos faltando (ex.: "ℓ")
MERGE_TOL = 4.0        # tolerância p/ juntar pedaços de uma mesma figura
CROP_PAD = 2.0

MARKER_RE = re.compile(r"^\{?\s*(\d{1,2})\s*\}?$")
SHARED_RE = re.compile(
    r"^Texto para as quest(?:ões|ão)\s+(?:de\s+)?(\d+)\s*(?:e|a|à|-|–)\s*(\d+)", re.I
)
ALT_RE = re.compile(r"^\(([A-E])\)\s*(.*)$", re.S)
NOTE_RE = re.compile(r"^Note e adote", re.I)

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
def _overlap_ratio(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    top, bot = max(a.y0, b.y0), min(a.y1, b.y1)
    if bot <= top:
        return 0.0
    return (bot - top) / max(1e-6, min(a.height, b.height))


def _col_of(rect: pymupdf.Rect, page_w: float) -> int:
    return 0 if (rect.x0 + rect.x1) / 2 < page_w / 2 else 1


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
                chars.extend({"c": c["c"], "bbox": pymupdf.Rect(c["bbox"]), "oy": c["origin"][1],
                              "size": sp["size"], "color": sp["color"], "font": sp["font"]}
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
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = re.sub(r"ℓ \)", "ℓ)", s)
    s = re.sub(r"\[(\w+): \s*", r"[\1: ", s)
    s = re.sub(r"\s+\]", "]", s)
    while COLOR_TAG_RE.search(s):                     # "[azul: 1/2][azul: log₂]" -> um trecho só
        s = COLOR_TAG_RE.sub(r"[\1: \2\3", s)
    return re.sub(r"ℓ ([.,;])", r"ℓ\1", s)


def _build_rows(page: pymupdf.Page) -> tuple[list[Row], list[tuple[int, float]]]:
    pw = page.rect.width
    lines = _raw_lines(page)
    _patch_missing_glyphs(lines, page)
    _decode_glyphs(lines)
    _rebuild_fractions(lines, page)
    _merge_script_lines(lines)
    _mark_scripts(lines)

    cands = []
    ends: list[tuple[int, float]] = []     # "#####" = marca de fim de questão (texto oculto no PDF)
    for ln in lines:
        raw = "".join(c["c"] for c in ln["chars"])
        txt = _clean(_line_text(ln))
        b = ln["bbox"]
        if "###" in raw and HEADER_Y <= b.y0 <= FOOTER_Y:
            ends.append((_col_of(b, pw), b.y1))
        if not txt or b.y0 < HEADER_Y or b.y0 > FOOTER_Y:
            continue
        # Linhas longas atravessam a fronteira visual da página ao redor de
        # imagens laterais; classificá-las pelo centro alterna de coluna e
        # desordena enunciados e alternativas que continuam na linha seguinte.
        row_col = 0 if b.width >= pw * 0.55 else _col_of(b, pw)
        cands.append(Row(txt, b, row_col, set(ln["fonts"])))

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
            if ov >= 0.5 and side_by_side and ov > best_ov:
                best, best_ov = prev, ov
        if best:
            best.parts.append((r.bbox, r.text))
            best.parts.sort(key=lambda p: p[0].x0)
            text = best.parts[0][1]
            for (lb, _), (rb, t) in zip(best.parts, best.parts[1:]):
                text += ("" if rb.x0 - lb.x1 < 1.5 else " ") + t
            best.text = _clean(text)
            best.bbox |= r.bbox
            best.fonts |= r.fonts
        else:
            r.parts = [(pymupdf.Rect(r.bbox), r.text)]
            rows.append(r)
    return rows, ends


def _merge_rects(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[pymupdf.Rect] = []
        for r in rects:
            for o in out:
                grown = pymupdf.Rect(o.x0 - MERGE_TOL, o.y0 - MERGE_TOL, o.x1 + MERGE_TOL, o.y1 + MERGE_TOL)
                if grown.intersects(r):
                    o |= r
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def _figure_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rects: list[pymupdf.Rect] = []
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"])
        if r.width <= TINY_W and r.height <= TINY_H:
            continue                                  # glifo faltando, tratado em _patch
        if r.height < SEP_MAX_H and r.width > 60:
            continue                                  # separador colorido entre questões
        rects.append(r)
    for r in page.cluster_drawings():
        r = pymupdf.Rect(r)
        if r.width < 4 or r.height < 4:
            continue                                  # linha divisória de coluna / filete
        if r.width >= page.rect.width * 0.9 and r.height >= page.rect.height * 0.85:
            continue                                  # moldura/contorno de página, não uma figura da questão
        if r.height < SEP_MAX_H and r.width > 40:
            continue                                  # bolinhas do separador
        rects.append(r)
    return [r for r in _merge_rects(rects) if r.y0 >= HEADER_Y - 5]


def extract_page(page: pymupdf.Page, pno: int) -> list[Item]:
    pw = page.rect.width
    rows, ends = _build_rows(page)
    frects = _figure_rects(page)

    # caixas "Note e adote": retângulo que contém uma linha começando assim
    note_rects, fig_rects = [], []
    for fr in frects:
        is_note = any(NOTE_RE.match(r.text) and fr.contains(pymupdf.Point(r.bbox.x0 + 1, r.bbox.y0 + 1))
                      for r in rows)
        (note_rects if is_note else fig_rects).append(fr)

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
                figs.append(Fig(pymupdf.Rect(fr.x0, fr.y0, fr.x1, first_y), _col_of(fr, pw)))
            for k, mk in enumerate(bare):
                y0 = mk.bbox.y0 - 4
                y1 = (bare[k + 1].bbox.y0 - 4) if k + 1 < len(bare) else fr.y1
                letter = ALT_RE.match(mk.text).group(1)
                figs.append(Fig(pymupdf.Rect(fr.x0, y0, fr.x1, y1), _col_of(fr, pw), alt=letter))
        else:
            figs.append(Fig(fr, _col_of(fr, pw)))

    items: list[Item] = []
    for f in figs:
        items.append(Item("fig", pno, f.col, f.bbox.y0, fig=f))

    for r in rows:
        center = pymupdf.Point((r.bbox.x0 + r.bbox.x1) / 2, (r.bbox.y0 + r.bbox.y1) / 2)
        # Em alguns PDFs oficiais o marcador numérico fica dentro da caixa de
        # uma imagem; ele continua sendo texto estrutural e não pode sumir.
        if MARKER_RE.match(r.text):
            items.append(Item("text", pno, r.col, r.bbox.y0, row=r))
            continue
        if ALT_RE.match(r.text) and not any(nr.contains(center) for nr in note_rects):
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

    items.sort(key=lambda it: (it.col, it.y0))
    return items


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
        "_segs": {}, "_fonts": set(),
    }


def parse(doc: pymupdf.Document):
    questions: dict[int, dict] = {}
    shared: dict[str, dict] = {}
    cur: dict | None = None
    cur_alt: str | None = None
    mode = "none"                      # none | stem | alts | shared
    active_shared: str | None = None
    expected = 1

    def touch(q, it):
        seg = q["_segs"].setdefault((it.page, it.col), [1e9, -1e9])
        b = it.row.bbox if it.row else it.fig.bbox
        seg[0], seg[1] = min(seg[0], b.y0), max(seg[1], b.y1)

    for pno in range(len(doc)):
        for it in extract_page(doc[pno], pno):
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
                if mk and int(mk.group(1)) == expected:
                    num = expected
                    expected += 1
                    cur = questions[num] = new_question(num, pno)
                    cur_alt, mode = None, "stem"
                    if active_shared and num in shared[active_shared]["questoes"]:
                        cur["texto_id"] = active_shared
                    else:
                        active_shared = None
                    touch(cur, it)
                    continue

                if mode == "shared":
                    shared[active_shared]["texto"].append(t)
                elif cur is not None:
                    touch(cur, it)
                    cur["_fonts"] |= it.row.fonts
                    am = ALT_RE.match(t)
                    if am:
                        cur_alt, mode = am.group(1), "alts"
                        cur["alternativas"][cur_alt] = {"texto": [am.group(2)] if am.group(2) else [],
                                                        "figuras": []}
                    elif mode == "alts" and cur_alt:
                        cur["alternativas"][cur_alt]["texto"].append(t)
                    else:
                        cur["enunciado"].append(t)

            elif it.kind == "note" and cur is not None:
                touch(cur, it)
                cur["nota"].append(it.row.text)

            elif it.kind == "fig":
                if mode == "shared":
                    shared[active_shared]["_figrects"].append((it.page, it.fig.bbox))
                elif cur is not None:
                    touch(cur, it)
                    letter = it.fig.alt or (cur_alt if mode == "alts" else None)
                    cur.setdefault("_figs", []).append((it.page, it.fig.bbox, letter))
    return questions, shared


# ------------------------------------------------------------- recortes / saída
def crop(doc, pno: int, rect: pymupdf.Rect, path: Path, dpi: int):
    r = pymupdf.Rect(rect.x0 - CROP_PAD, rect.y0 - CROP_PAD, rect.x1 + CROP_PAD, rect.y1 + CROP_PAD)
    r &= doc[pno].rect
    doc[pno].get_pixmap(dpi=dpi, clip=r).save(str(path))


def finalize(doc, questions, shared, outdir: Path, dpi: int, full_crops: bool,
             detailed_crops: bool = False):
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

    for num, q in questions.items():
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
                # A revisão detalhada preserva toda a geometria da questão em
                # cada página, inclusive imagem, legenda e alternativas largas.
                by_page: dict[int, list[tuple[float, float]]] = {}
                for (p, _col), (y0, y1) in q["_segs"].items():
                    by_page.setdefault(p, []).append((y0, y1))
                segments = [(p, (min(y0 for y0, _ in bounds), max(y1 for _, y1 in bounds)))
                            for p, bounds in sorted(by_page.items())]
                for k, (p, (y0, y1)) in enumerate(segments, 1):
                    name = f"q{n}_completa_detalhada{k}.png"
                    crop(doc, p, pymupdf.Rect(22, y0 - 3, doc[p].rect.width - 22, y1 + 3),
                         imgdir / name, dpi)
                    q["imagens_completas"].append(f"img/{name}")
            else:
                for k, ((p, col), (y0, y1)) in enumerate(sorted(q["_segs"].items()), 1):
                    x0, x1 = colx[col]
                    name = f"q{n}_completa{k}.png"
                    crop(doc, p, pymupdf.Rect(x0, y0 - 3, x1, y1 + 3), imgdir / name, dpi)
                    q["imagens_completas"].append(f"img/{name}")

        q["enunciado"] = _join(q["enunciado"])
        q["nota"] = _join(q["nota"])
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
        if not q["enunciado"] and not q["figuras_enunciado"]:
            q["revisar"].append("enunciado_vazio")
        if any(not a["texto"] and not a["figuras"] for a in q["alternativas"].values()):
            q["revisar"].append("alternativa_vazia")

        q.pop("_segs")
        q.pop("_fonts")
        q["resposta"] = None  # o caderno não traz gabarito; preencher depois


def extract_pdf(input_pdf: Path, outdir: Path, *, dpi: int = 200, full_crops: bool = True,
                detailed_crops: bool = False, progress=None) -> Path:
    """Extrai um PDF e grava questoes.json e imagens dentro de outdir."""
    input_pdf = input_pdf.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(input_pdf)
    try:
        if progress:
            progress({"stage": "extracting", "message": f"Lendo PDF · {len(doc)} páginas", "pages": len(doc)})
        questions, shared = parse(doc)
        if progress:
            progress({"stage": "rendering", "message": f"Extraindo imagens de {len(questions)} questões", "questions": len(questions)})
        finalize(doc, questions, shared, outdir, dpi, full_crops, detailed_crops)
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
