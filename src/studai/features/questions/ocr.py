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

Página em duas colunas é lida coluna por coluna (`find_gutter`): o Tesseract com a
página inteira intercalava as linhas das duas colunas (2021 p. 17, Q54 com o texto
em inglês da direita). O idioma é "por+eng" quando o inglês está instalado
(STUDAI_OCR_LANG troca).
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


# --------------------------------------------------------- TSV do Tesseract
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


# ------------------------------------------------------- calha entre colunas
GUTTER_DPI = 50            # render leve só para achar a calha
GUTTER_INK = 200           # cinza abaixo disso é tinta (no render leve o texto fino fica cinza)
GUTTER_SPAN = (0.4, 0.6)   # a calha fica no meio da página
GUTTER_MIN_H = 0.8         # fração da altura do corpo (primeira à última linha com tinta)
GUTTER_MIN_W = 5.0         # pt; um vão de 1 px entre palavras alinhadas não é calha
GUTTER_RULE = 3            # px de fio (traço vertical entre as colunas) dentro da calha


@dataclass
class Gutter:
    """Calha vertical em pontos: ``x0``–``x1`` branco de ``y0`` a ``y1``."""
    x0: float
    x1: float
    y0: float
    y1: float


def find_gutter(page: pymupdf.Page) -> Gutter | None:
    """
    Faixa branca vertical entre 40% e 60% da largura cobrindo pelo menos 80% da
    altura do corpo. Um fio entre as colunas (2021, 2002) fica dentro da calha: as
    faixas brancas dos dois lados dele contam juntas. Acima e abaixo da calha ficam
    as faixas de largura total (título, rodapé, questão de página inteira).
    """
    pix = page.get_pixmap(dpi=GUTTER_DPI, colorspace=pymupdf.csGRAY)
    W, H, stride, samples = pix.width, pix.height, pix.stride, pix.samples
    rows = [y for y in range(H) if min(samples[y * stride: y * stride + W], default=255) < GUTTER_INK]
    if not rows:
        return None
    top, bottom = rows[0], rows[-1]
    need = GUTTER_MIN_H * (bottom - top + 1)
    runs: dict[int, tuple[int, int]] = {}            # x -> maior faixa branca (y0, y1)
    for x in range(int(GUTTER_SPAN[0] * W), int(GUTTER_SPAN[1] * W) + 1):
        best, start = (0, -1), None
        for y in range(top, bottom + 2):
            white = y > bottom or samples[y * stride + x] >= GUTTER_INK
            if white and start is None and y <= bottom:
                start = y
            elif (not white or y > bottom) and start is not None:
                if y - start > best[1] - best[0]:
                    best = (start, y)
                start = None
        if best[1] - best[0] >= need:
            runs[x] = best
    if not runs:
        return None
    # Colunas brancas vizinhas (com até GUTTER_RULE px de fio no meio) formam a calha.
    groups: list[list[int]] = []
    for x in sorted(runs):
        if groups and x - groups[-1][-1] <= GUTTER_RULE + 1:
            groups[-1].append(x)
        else:
            groups.append([x])
    group = max(groups, key=lambda g: g[-1] - g[0])
    scale = 72.0 / GUTTER_DPI
    if (group[-1] - group[0] + 1) * scale < GUTTER_MIN_W:
        return None
    # A faixa vertical é a da coluna branca mais longa: nas bordas da calha um hífen ou
    # um índice encurta a faixa e cortaria as linhas das colunas na faixa de cima.
    y0, y1 = max((runs[x] for x in group), key=lambda r: r[1] - r[0])
    r = page.rect
    # Calha desde a primeira (ou até a última) linha da página: as colunas vão até a
    # borda; cortar ali rasparia o topo da primeira linha.
    return Gutter(r.x0 + group[0] * scale, r.x0 + (group[-1] + 1) * scale,
                  r.y0 if y0 <= top else r.y0 + y0 * scale, r.y1 if y1 > bottom else r.y0 + y1 * scale)


def page_regions(page: pymupdf.Page, gutter: Gutter | None) -> list[tuple[pymupdf.Rect, bool]]:
    """
    Regiões lidas em separado, na ordem de leitura: faixa de cima, coluna esquerda,
    coluna direita, faixa de baixo. O booleano diz se a região é uma coluna.
    """
    r = page.rect
    if gutter is None:
        return [(pymupdf.Rect(r), False)]
    out = []
    if gutter.y0 - r.y0 > 2:
        out.append((pymupdf.Rect(r.x0, r.y0, r.x1, gutter.y0), False))
    out.append((pymupdf.Rect(r.x0, gutter.y0, gutter.x0, gutter.y1), True))
    out.append((pymupdf.Rect(gutter.x1, gutter.y0, r.x1, gutter.y1), True))
    if r.y1 - gutter.y1 > 2:
        out.append((pymupdf.Rect(r.x0, gutter.y1, r.x1, r.y1), False))
    return out


# ------------------------------------------------- alternativas lado a lado
# "(A) 0,10    (D) 0,97" numa linha só: a letra do meio depois de um vão largo.
SIDE_ALT_RE = re.compile(r"^(?:[(\[{][A-Ea-e][)\]}JjI|1l]?|[A-Ea-e][)\]}Jj|])$")
SIDE_ALT_GAP = 2.0         # vão mínimo antes da letra do meio, em alturas de linha
WIDE_GAP = 3.0             # vão que separa duas coisas na mesma altura (alternativa e caixa "Note e adote")


def split_side_by_side(lines: list[OcrLine]) -> list[OcrLine]:
    """
    O Tesseract junta numa linha o que está na mesma altura: alternativas em duas
    subcolunas ("(A) … (D) …") e a alternativa ao lado da caixa "Note e adote" (2021
    Q55). A linha é quebrada no vão largo, ou na letra de alternativa depois de um vão
    médio quando a linha começa com outra letra; cada pedaço vira uma linha. O
    extrator junta de novo os pedaços que eram uma linha só (tabela) e lê a subcoluna
    esquerda das alternativas antes da direita.
    """
    out: list[OcrLine] = []
    for ln in lines:
        words = ln.words
        if len(words) < 2:
            out.append(ln)
            continue
        height = max(1.0, statistics.median(w.bbox.height for w in words))
        alt = bool(SIDE_ALT_RE.match(words[0].text))

        def cut_here(k: int) -> bool:
            gap = words[k].bbox.x0 - words[k - 1].bbox.x1
            return gap >= WIDE_GAP * height or (alt and k >= 2 and gap >= SIDE_ALT_GAP * height
                                                and bool(SIDE_ALT_RE.match(words[k].text)))

        cut = next((k for k in range(1, len(words)) if cut_here(k)), None)
        if cut is None:
            out.append(ln)
            continue
        out += [OcrLine(words[:cut], block=ln.block), *split_side_by_side([OcrLine(words[cut:], block=ln.block)])]
    return out


def drop_duplicates(lines: list[OcrLine]) -> list[OcrLine]:
    """
    Numa tabela o Tesseract pode devolver a mesma linha duas vezes, uma delas truncada
    ("B) | Ligado ao X recessivo 25%…" e "(B) | Lig j", 2021 Q28); a segunda virava
    outra alternativa. Palavra que cai sobre outra de uma linha mais longa sai.
    """
    kept: list[pymupdf.Rect] = []
    out: dict[int, OcrLine] = {}
    for k in sorted(range(len(lines)), key=lambda k: -len(lines[k].words)):
        ln = lines[k]
        words = [w for w in ln.words
                 if not any((w.bbox & b).get_area() >= 0.5 * min(w.bbox.get_area(), b.get_area()) > 0 for b in kept)]
        kept += [w.bbox for w in words]
        if words:
            out[k] = ln if len(words) == len(ln.words) else OcrLine(words, block=ln.block)
    return [out[k] for k in sorted(out)]


# ----------------------------------------------------------------- Tesseract
def installed_langs(binary: str, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> set[str]:
    try:
        result = runner([binary, "--list-langs"], capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    out = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
    return {ln.strip() for ln in out.splitlines()[1:] if ln.strip()}


def default_lang(binary: str | None, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """
    "por+eng" quando o inglês está instalado (questões de inglês e citações), "por"
    caso contrário. STUDAI_OCR_LANG troca (ex.: "por").
    """
    env = os.environ.get("STUDAI_OCR_LANG", "").strip()
    if env:
        return env
    return "por+eng" if binary and "eng" in installed_langs(binary, runner) else "por"


class TesseractOcr:
    """
    Chama o binário `tesseract` (TSV com a posição de cada palavra). Com calha entre
    colunas, cada coluna é lida sozinha com ``column_psm`` (4: uma coluna de texto) e
    as faixas de largura total com ``psm``; sem calha, a página inteira com ``psm``.
    """

    def __init__(self, lang: str | None = None, binary: str | None = None, psm: int = 3,
                 timeout: float = 180.0, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 column_psm: int = 4, columns: bool = True):
        self.binary = binary or shutil.which("tesseract")
        self.runner = runner
        self.lang = lang or default_lang(self.binary, runner)
        self.psm = psm
        self.column_psm = column_psm
        self.columns = columns
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def __call__(self, page: pymupdf.Page, dpi: int) -> list[OcrLine]:
        if not self.binary:
            raise FileNotFoundError("tesseract")
        regions = page_regions(page, find_gutter(page) if self.columns else None)
        out: list[OcrLine] = []
        for k, (rect, column) in enumerate(regions):
            lines = self.read(page, dpi, rect, self.column_psm if column else self.psm)
            for ln in lines:
                ln.block += 1000 * k                    # blocos únicos entre as regiões
            out += lines
        return out

    def read(self, page: pymupdf.Page, dpi: int, rect: pymupdf.Rect, psm: int) -> list[OcrLine]:
        """OCR de um trecho da página (``rect`` em pontos)."""
        clip = rect & page.rect
        if clip.is_empty:
            return []
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, clip=clip)
        result = self.runner([self.binary, "stdin", "stdout", "-l", self.lang, "--psm", str(psm), "tsv"],
                             input=pix.tobytes("png"), capture_output=True, check=True, timeout=self.timeout)
        tsv = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
        return parse_tsv(tsv, 72.0 / dpi, (clip.x0, clip.y0))


def default_engine() -> OcrEngine | None:
    """Tesseract instalado e OCR não desligado (STUDAI_OCR=0); senão None."""
    if os.environ.get("STUDAI_OCR", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if not shutil.which("tesseract"):
        return None
    return TesseractOcr()


# --------------------------------------------- linhas no formato do extrator
def as_raw_lines(lines: list[OcrLine], size: float | None = None) -> list[dict]:
    """
    Converte as linhas do OCR para o formato de `extractor._raw_lines`: cada palavra
    vira caracteres espalhados na caixa da palavra e os vãos viram espaços. O corpo
    é o mesmo para a página toda (a altura da caixa do OCR varia com ascendentes e
    descendentes e marcaria parágrafos falsos).
    """
    lines = split_side_by_side(drop_duplicates(lines))
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
