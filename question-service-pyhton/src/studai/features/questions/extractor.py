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
import json
import re

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
CORRUPT_GLYPH_RE = re.compile(r"[\ufffd\ue000-\uf8ff\u0b80-\u0bff\u0d00-\u0d7f\u1200-\u137f]")


# ------------------------------------------------------------------- modelos
@dataclass
class Row:
    """Uma linha (já fundida com linhas na mesma altura, p/ tabelas)."""
    text: str
    bbox: pymupdf.Rect
    col: int
    fonts: set[str] = field(default_factory=set)


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
                chars.extend({"c": c["c"], "bbox": pymupdf.Rect(c["bbox"])} for c in sp["chars"])
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


def _clean(s: str) -> str:
    s = re.sub(r"#{3,}", " ", s)          # "#####" escondido na camada de texto, marca fim de questão
    s = s.replace("\u00ac", " ")          # glifo usado pelo PDF no lugar de espaços
    s = s.replace("\u2011", "-").replace("\u00ad", "").replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = re.sub(r"ℓ \)", "ℓ)", s)
    return re.sub(r"ℓ ([.,;])", r"ℓ\1", s)


def _build_rows(page: pymupdf.Page) -> tuple[list[Row], list[tuple[int, float]]]:
    pw = page.rect.width
    lines = _raw_lines(page)
    _patch_missing_glyphs(lines, page)

    cands = []
    ends: list[tuple[int, float]] = []     # "#####" = marca de fim de questão (texto oculto no PDF)
    for ln in lines:
        raw = "".join(c["c"] for c in ln["chars"])
        txt = _clean(raw)
        b = ln["bbox"]
        if "###" in raw and HEADER_Y <= b.y0 <= FOOTER_Y:
            ends.append((_col_of(b, pw), b.y1))
        if not txt or b.y0 < HEADER_Y or b.y0 > FOOTER_Y:
            continue
        cands.append(Row(txt, b, _col_of(b, pw), set(ln["fonts"])))

    # funde linhas na mesma altura e mesma coluna (linhas de tabela, "(A)" + célula etc.)
    cands.sort(key=lambda r: (r.col, (r.bbox.y0 + r.bbox.y1) / 2, r.bbox.x0))
    rows: list[Row] = []
    for r in cands:
        best, best_ov = None, 0.0
        for prev in rows[-6:]:
            if prev.col != r.col:
                continue
            ov = _overlap_ratio(prev.bbox, r.bbox)
            side_by_side = r.bbox.x0 >= prev.bbox.x1 - 1 or prev.bbox.x0 >= r.bbox.x1 - 1
            if ov >= 0.5 and side_by_side and ov > best_ov:
                best, best_ov = prev, ov
        if best:
            left, right = (best, r) if best.bbox.x0 <= r.bbox.x0 else (r, best)
            gap = right.bbox.x0 - left.bbox.x1
            best.text = _clean(left.text + ("" if gap < 1.5 else " ") + right.text)
            best.bbox |= r.bbox
            best.fonts |= r.fonts
        else:
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


def finalize(doc, questions, shared, outdir: Path, dpi: int, full_crops: bool):
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


def extract_pdf(input_pdf: Path, outdir: Path, *, dpi: int = 200, full_crops: bool = True, progress=None) -> Path:
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
        finalize(doc, questions, shared, outdir, dpi, full_crops)
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
