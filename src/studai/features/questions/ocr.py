"""
OCR das páginas sem camada de texto legível (FUVEST 1997, 2002 dia 2, 2021).

O extrator lê a camada de texto do PDF. Algumas provas do acervo não a têm em
condições de uso:

- 1997: páginas escaneadas; o texto é imagem e só o rodapé é texto de verdade.
- 2002 dia 2: fontes com codificação trocada ("CpqÇåuruâçq" no lugar das palavras).
- 2021: tudo em fonte Type3 de bitmap, sem Unicode (códigos de controle, corpo ~0,1 pt).

Este módulo decide quais páginas precisam de OCR (`text_layer_status`), roda o
Tesseract (`TesseractOcr`) e devolve linhas no mesmo formato de
`extractor._raw_lines`, para que o restante da montagem (colunas, números,
alternativas, figuras) seja o mesmo das provas com texto.

O motor é uma dependência injetável: qualquer função `(page, dpi) -> list[OcrLine]`.
Os testes usam motores falsos; sem o binário `tesseract` (ou com STUDAI_OCR=0) o
OCR é simplesmente pulado e a prova sai como antes, com as questões em revisão.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Protocol

import pymupdf

log = logging.getLogger(__name__)

# ----------------------------------------------------------------- modelos
OK, GARBAGE, MISSING = "ok", "garbage", "missing"


@dataclass
class OcrWord:
    text: str
    bbox: pymupdf.Rect
    conf: float = 0.0


@dataclass
class OcrLine:
    words: list[OcrWord]
    block: int = 0
    bbox: pymupdf.Rect = field(default_factory=pymupdf.Rect)

    def __post_init__(self) -> None:
        if self.words and self.bbox.is_empty:
            box = pymupdf.Rect(self.words[0].bbox)
            for w in self.words[1:]:
                box |= w.bbox
            self.bbox = box

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def conf(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words) if self.words else 0.0


class OcrEngine(Protocol):
    def __call__(self, page: pymupdf.Page, dpi: int) -> list[OcrLine]: ...


# ------------------------------------------------- diagnóstico da camada de texto
# Palavras mais comuns em português e inglês (a prova tem questões de inglês).
# Num texto normal elas são ~30% das palavras; na fonte com codificação trocada
# (2002 dia 2) não aparecem.
STOPWORDS = frozenset(
    "a o as os de do da dos das e é em no na nos nas um uma que se por para com não ao "
    "à pelo pela mais como seu sua ou foi ser são entre "
    "the of and to in is that for it on with as are by be this an or from".split()
)
BITMAP_FONT_SIZE = 0.5     # mesmo limite do extrator: corpo nominal das fontes Type3 em bitmap
MIN_CHARS = 80             # menos que isso no corpo da página: não dá para julgar como lixo
SCAN_COVER = 0.3           # fração do corpo coberta por imagens para considerar a página escaneada
WORD_RE = re.compile(r"[^\W\d_]+")


def _bad_char(ch: dict) -> bool:
    c = ch["c"]
    cp = ord(c)
    if (ch.get("size") or 1) < BITMAP_FONT_SIZE:
        return True
    # \x00/\x02/\x03 dentro de palavra são hífen/espaço das fontes de 2014–2017 (ver extrator._clean).
    return (cp < 32 and c not in "\x00\x02\x03\t\n") or 0xE000 <= cp <= 0xF8FF or c == "�"


def text_layer_status(page: pymupdf.Page) -> str:
    """
    "ok" quando a camada de texto serve; "garbage" quando existe mas é ilegível
    (bitmap sem Unicode, codificação trocada); "missing" quando a página é uma
    imagem escaneada com pouco ou nenhum texto.
    """
    h = page.rect.height
    top, bottom = page.rect.y0 + 0.05 * h, page.rect.y1 - 0.05 * h
    chars: list[dict] = []
    text_parts: list[str] = []
    for blk in page.get_text("rawdict")["blocks"]:
        if blk["type"] != 0:
            continue
        for ln in blk["lines"]:
            if not top <= ln["bbox"][1] <= bottom:
                continue
            for sp in ln["spans"]:
                for c in sp["chars"]:
                    if c["c"].strip():
                        chars.append({"c": c["c"], "size": sp["size"]})
                text_parts.append("".join(c["c"] for c in sp["chars"]))
            text_parts.append(" ")
    if len(chars) >= MIN_CHARS:
        if sum(_bad_char(ch) for ch in chars) >= 0.3 * len(chars):
            return GARBAGE
        # Só palavras de 2+ letras: "é"/"à" sobrevivem à codificação trocada e páginas de
        # matemática têm muita letra solta (2002 dia 2, p. 12).
        words = [w.lower() for w in WORD_RE.findall("".join(text_parts)) if len(w) >= 2]
        if len(words) >= 15 and sum(w in STOPWORDS for w in words) < 0.04 * len(words):
            return GARBAGE
        return OK
    body = pymupdf.Rect(page.rect.x0, top, page.rect.x1, bottom)
    covered = 0.0
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & body
        if not r.is_empty:
            covered += r.width * r.height
    return MISSING if covered >= SCAN_COVER * body.width * body.height else OK


def scan_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Imagens grandes da página (as faixas escaneadas de 1997)."""
    area = page.rect.width * page.rect.height
    return [pymupdf.Rect(im["bbox"]) for im in page.get_image_info()
            if (im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1]) >= 0.02 * area]


def ocr_dpi(page: pymupdf.Page, status: str, default: int = 300) -> int:
    """
    Resolução do render para o OCR. Texto vetorial (2002, 2021) sai nítido em 300 dpi.
    Num escaneado de ~100 dpi (1997), ampliar muito só borra: o Tesseract acerta mais
    os números das questões em ~1,5× a resolução original (medido no acervo).
    """
    if status != MISSING:
        return default
    native = []
    for im in page.get_image_info():
        w = im["bbox"][2] - im["bbox"][0]
        if w >= 50 and im.get("width"):
            native.append(im["width"] / w * 72)
    if not native:
        return default
    return int(min(default, max(150, 1.5 * statistics.median(native))))


# ----------------------------------------------------------------- Tesseract
def parse_tsv(tsv: str, scale: float, origin: tuple[float, float] = (0.0, 0.0)) -> list[OcrLine]:
    """
    Saída `tsv` do Tesseract -> linhas com palavras em coordenadas do PDF.
    ``scale`` converte pixel do render em ponto (72/dpi).
    """
    lines: dict[tuple[int, int, int], OcrLine] = {}
    ox, oy = origin
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
            left, top, width, height = (int(v) for v in cols[6:10])
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        rect = pymupdf.Rect(ox + left * scale, oy + top * scale,
                            ox + (left + width) * scale, oy + (top + height) * scale)
        key = (int(cols[2]), int(cols[3]), int(cols[4]))
        line = lines.setdefault(key, OcrLine([], block=key[0]))
        line.words.append(OcrWord(text, rect, conf))
    out = []
    for line in lines.values():
        line.__post_init__()
        out.append(line)
    return out


class TesseractOcr:
    """Chama o binário `tesseract` (TSV com a posição de cada palavra)."""

    def __init__(self, lang: str = "por", binary: str | None = None, psm: int = 3,
                 timeout: float = 180.0, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.lang = lang
        self.binary = binary or shutil.which("tesseract")
        self.psm = psm
        self.timeout = timeout
        self.runner = runner

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def __call__(self, page: pymupdf.Page, dpi: int) -> list[OcrLine]:
        if not self.binary:
            raise FileNotFoundError("tesseract")
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        result = self.runner([self.binary, "stdin", "stdout", "-l", self.lang, "--psm", str(self.psm), "tsv"],
                             input=pix.tobytes("png"), capture_output=True, check=True, timeout=self.timeout)
        tsv = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
        return parse_tsv(tsv, 72.0 / dpi, (page.rect.x0, page.rect.y0))


def default_engine() -> OcrEngine | None:
    """Tesseract instalado e OCR não desligado (STUDAI_OCR=0); senão None."""
    if os.environ.get("STUDAI_OCR", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    engine = TesseractOcr()
    return engine if engine.available else None


# --------------------------------------------- linhas no formato do extrator
def as_raw_lines(lines: list[OcrLine], size: float | None = None) -> list[dict]:
    """
    Converte as linhas do OCR para o formato de `extractor._raw_lines`: cada palavra
    vira caracteres espalhados na caixa da palavra e os vãos viram espaços. O corpo
    é o mesmo para a página toda (a altura da caixa do OCR varia com ascendentes e
    descendentes e marcaria parágrafos falsos).
    """
    if size is None:
        heights = [ln.bbox.height for ln in lines if ln.words]
        size = statistics.median(heights) * 0.8 if heights else 10.0
    out = []
    for ln in lines:
        chars: list[dict] = []
        for k, w in enumerate(ln.words):
            if k:
                prev = ln.words[k - 1].bbox
                gap = pymupdf.Rect(prev.x1, w.bbox.y0, max(prev.x1 + 0.1, w.bbox.x0), w.bbox.y1)
                chars.append(_char(" ", gap, size))
            step = w.bbox.width / max(1, len(w.text))
            for i, c in enumerate(w.text):
                x0 = w.bbox.x0 + i * step
                chars.append(_char(c, pymupdf.Rect(x0, w.bbox.y0, x0 + step, w.bbox.y1), size))
        if chars:
            out.append({"chars": chars, "bbox": pymupdf.Rect(ln.bbox), "fonts": {"OCR"}, "ocr": True,
                        "conf": ln.conf})
    return out


def _char(c: str, bbox: pymupdf.Rect, size: float) -> dict:
    return {"c": c, "bbox": bbox, "oy": None, "size": size, "color": 0, "font": "OCR", "bold": False}


# ---------------------------------------------- figuras em página escaneada
INK_CELL = 3.0             # lado da célula da grade (pt)
INK_DARK = 140             # cinza abaixo disso é tinta
FIG_MIN_W, FIG_MIN_H = 24.0, 18.0


def scan_figures(page: pymupdf.Page, text_boxes: list[pymupdf.Rect],
                 regions: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """
    Figuras dentro de uma página escaneada: o que tem tinta e não é texto lido pelo
    OCR. A página é reduzida a uma grade de células de ``INK_CELL`` pt, as caixas de
    texto são apagadas e as manchas conectadas (com 1 célula de folga) viram figuras.
    Só olha dentro de ``regions`` (as imagens escaneadas): moldura e fio de coluna
    são desenhos fora delas.
    """
    if not regions:
        return []
    pix = page.get_pixmap(dpi=int(72 / INK_CELL * 2), colorspace=pymupdf.csGRAY)   # 2 px por célula
    W, H = pix.width, pix.height
    sx, sy = W / page.rect.width, H / page.rect.height
    samples = pix.samples
    cw, ch = int(page.rect.width / INK_CELL) + 1, int(page.rect.height / INK_CELL) + 1
    ox, oy = page.rect.x0, page.rect.y0
    inside = bytearray(cw * ch)                    # células dentro das faixas escaneadas
    for r in regions:
        for cy in range(max(0, int((r.y0 - oy + 1.5) / INK_CELL) + 1), min(ch, int((r.y1 - oy - 1.5) / INK_CELL))):
            for cx in range(max(0, int((r.x0 - ox + 1.5) / INK_CELL) + 1), min(cw, int((r.x1 - ox - 1.5) / INK_CELL))):
                inside[cy * cw + cx] = 1
    ink = bytearray(cw * ch)
    col_cell = [min(cw - 1, int(px / sx / INK_CELL)) for px in range(W)]
    for py in range(H):
        base = min(ch - 1, int(py / sy / INK_CELL)) * cw
        row = samples[py * pix.stride: py * pix.stride + W]
        for px in range(W):
            if row[px] < INK_DARK:
                k = base + col_cell[px]
                if inside[k]:
                    ink[k] = 1
    for b in text_boxes:
        for cy in range(max(0, int((b.y0 - oy) / INK_CELL)), min(ch, int((b.y1 - oy) / INK_CELL) + 1)):
            for cx in range(max(0, int((b.x0 - ox) / INK_CELL)), min(cw, int((b.x1 - ox) / INK_CELL) + 1)):
                ink[cy * cw + cx] = 0
    seen = bytearray(cw * ch)
    out: list[pymupdf.Rect] = []
    for start in range(cw * ch):
        if not ink[start] or seen[start]:
            continue
        stack, seen[start] = [start], 1
        x0 = x1 = start % cw
        y0 = y1 = start // cw
        n = 0
        while stack:
            k = stack.pop()
            n += 1
            kx, ky = k % cw, k // cw
            x0, x1, y0, y1 = min(x0, kx), max(x1, kx), min(y0, ky), max(y1, ky)
            for dy in (-2, -1, 0, 1, 2):
                ny = ky + dy
                if not 0 <= ny < ch:
                    continue
                for dx in (-2, -1, 0, 1, 2):
                    nx = kx + dx
                    if 0 <= nx < cw:
                        j = ny * cw + nx
                        if ink[j] and not seen[j]:
                            seen[j] = 1
                            stack.append(j)
        rect = pymupdf.Rect(ox + x0 * INK_CELL, oy + y0 * INK_CELL, ox + (x1 + 1) * INK_CELL, oy + (y1 + 1) * INK_CELL)
        if rect.width >= FIG_MIN_W and rect.height >= FIG_MIN_H and n >= 12:
            out.append(rect)
    return out
