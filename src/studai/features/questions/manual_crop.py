"""Modo de extração alternativo: recorte manual de uma região do PDF na revisão.

Para as questões que o extrator recorta mal (imagem ao lado do texto, faixa de
página inteira entre colunas, figura em outra página, enunciado lido como figura),
a pessoa desenha um retângulo sobre a página renderizada. Aqui a região é recortada
em boa resolução, gravada em ``img/`` da prova e, opcionalmente, a camada de texto
dentro dela vira sugestão de enunciado e alternativas.

O recorte entra como camada de correção (``edits`` em ``revisao.json``), igual às
imagens enviadas à mão: a extração original fica intacta.
"""
# TODO(cross-agent): extractor.py deveria sinalizar ``enunciado_vazio`` mesmo com figura
# (FUVEST 2023 Q28, 2026 Q42), anexar imagens de coluna lateral (2025 Q1), recortar a faixa
# de página inteira com a largura da página e tirar o aviso de rascunho das alternativas
# (2024 Q90). Detalhes em docs/bad-crops-survey.md.
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pymupdf

from studai.features.questions import review as question_review

PREVIEW_DPI = 110
CROP_DPI = 220
MIN_DPI, MAX_DPI = 36, 300
MIN_REGION_POINTS = 8
MAX_CROP_PIXELS = 25_000_000

OPTION_RE = re.compile(r"^\s*\(?([A-Ea-e])\)\s*(.*)$")
NUMBER_LINE_RE = re.compile(r"^\s*(?:quest[aã]o\s*)?\d{1,3}\s*$", re.I)


def _open(pdf_path: Path) -> pymupdf.Document:
    try:
        return pymupdf.open(Path(pdf_path))
    except Exception as exc:  # pymupdf levanta tipos variados para arquivo inválido
        raise ValueError(f"não foi possível abrir o PDF da prova: {exc}") from None


def _page(doc: pymupdf.Document, page_number: int) -> pymupdf.Page:
    try:
        page_number = int(page_number)
    except (TypeError, ValueError):
        raise ValueError("página inválida") from None
    if not 1 <= page_number <= doc.page_count:
        raise ValueError(f"página {page_number} fora do PDF (1 a {doc.page_count})")
    return doc[page_number - 1]


def _dpi(value, default: int) -> int:
    try:
        value = int(value) if value is not None else default
    except (TypeError, ValueError):
        raise ValueError("dpi inválido") from None
    return max(MIN_DPI, min(MAX_DPI, value))


def page_sizes(pdf_path: Path) -> list[dict]:
    """Número e tamanho (em pontos) de cada página, para a tela de recorte."""
    with _open(pdf_path) as doc:
        return [{"number": index + 1, "width": round(page.rect.width, 2), "height": round(page.rect.height, 2)}
                for index, page in enumerate(doc)]


def render_page(pdf_path: Path, page_number: int, dpi: int | None = None) -> bytes:
    """PNG da página inteira, na resolução de visualização."""
    with _open(pdf_path) as doc:
        page = _page(doc, page_number)
        return page.get_pixmap(dpi=_dpi(dpi, PREVIEW_DPI), alpha=False).tobytes("png")


def region_rect(page: pymupdf.Page, region) -> pymupdf.Rect:
    """Converte a região normalizada ``[x0, y0, x1, y1]`` (0 a 1, origem no topo
    esquerdo da página) para pontos do PDF, limitada à página."""
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise ValueError("região deve ser [x0, y0, x1, y1] com frações da página")
    try:
        x0, y0, x1, y1 = (float(value) for value in region)
    except (TypeError, ValueError):
        raise ValueError("região deve conter apenas números") from None
    if any(value != value for value in (x0, y0, x1, y1)):  # NaN
        raise ValueError("região inválida")
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    bounds = page.rect
    rect = pymupdf.Rect(bounds.x0 + x0 * bounds.width, bounds.y0 + y0 * bounds.height,
                        bounds.x0 + x1 * bounds.width, bounds.y0 + y1 * bounds.height)
    if rect.width < MIN_REGION_POINTS or rect.height < MIN_REGION_POINTS:
        raise ValueError("a região selecionada é pequena demais")
    return rect


def split_question_text(text: str) -> dict:
    """Separa o texto recortado em enunciado e alternativas ``(A)``/``A)``.

    É só uma sugestão para pré-preencher a edição; a pessoa confere antes de salvar.
    Linhas que continuam uma alternativa são juntadas a ela; o número da questão
    sozinho na primeira linha é descartado.
    """
    stem: list[str] = []
    options: dict[str, list[str]] = {}
    current: str | None = None
    lines = [line.strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    if lines and NUMBER_LINE_RE.match(lines[0]):
        lines = lines[1:]
    expected = "A"
    for line in lines:
        match = OPTION_RE.match(line)
        letter = match.group(1).upper() if match else None
        # Só aceita a próxima letra na ordem; evita confundir "a) " no meio do texto.
        if letter and letter == expected:
            current = letter
            options[current] = [match.group(2).strip()]
            expected = chr(ord(expected) + 1)
            continue
        if current:
            options[current].append(line)
        else:
            stem.append(line)

    return {"enunciado": join_paragraphs(stem),
            "alternativas": {letter: _join_lines(parts) for letter, parts in options.items()}}


def _join_lines(lines: list[str]) -> str:
    value = ""
    for line in lines:
        if not value:
            value = line
        elif value.endswith("-") and line[:1].islower():
            value = value[:-1] + line  # hifenização de fim de linha
        else:
            value += " " + line
    return value.strip()


def join_paragraphs(lines: list[str]) -> str:
    """Junta linhas quebradas pela diagramação mantendo parágrafos (linha terminada em
    ponto final, dois-pontos, interrogação ou exclamação fecha o parágrafo)."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        current.append(line)
        if re.search(r"[.:?!”\"]$", line):
            paragraphs.append(_join_lines(current))
            current = []
    if current:
        paragraphs.append(_join_lines(current))
    return "\n".join(paragraphs).strip()


def crop_region(pdf_path: Path, page_number: int, region, exam_dir: Path, *,
                dpi: int | None = None, extract_text: bool = True) -> dict:
    """Recorta a região da página e grava ``img/crop_<id>.png`` na pasta da prova.

    Devolve o caminho relativo à prova, a região em pontos e, se pedido, o texto da
    camada de texto dentro da região com a sugestão de enunciado/alternativas.
    """
    resolution = _dpi(dpi, CROP_DPI)
    with _open(pdf_path) as doc:
        page = _page(doc, page_number)
        rect = region_rect(page, region)
        scale = resolution / 72
        if rect.width * scale * rect.height * scale > MAX_CROP_PIXELS:
            raise ValueError("a região é grande demais para esta resolução")
        pixmap = page.get_pixmap(dpi=resolution, clip=rect, alpha=False)
        content = pixmap.tobytes("png")
        text = page.get_text("text", clip=rect, sort=True) if extract_text else ""
    folder = Path(exam_dir) / "img"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"crop_{uuid.uuid4().hex[:12]}.png"
    (folder / name).write_bytes(content)
    result = {"path": f"img/{name}", "page": int(page_number), "dpi": resolution,
              "rect": [round(value, 2) for value in (rect.x0, rect.y0, rect.x1, rect.y1)],
              "width": pixmap.width, "height": pixmap.height}
    if extract_text:
        text = text.strip()
        result["text"] = text
        result["suggestion"] = split_question_text(text) if text else {"enunciado": "", "alternativas": {}}
    return result


def _parse_target(target: str) -> str | None:
    """``"stem"`` → enunciado; ``"option:B"`` → alternativa B."""
    target = str(target or "stem")
    if target == "stem":
        return None
    match = re.fullmatch(r"option:([A-E])", target)
    if not match:
        raise ValueError("destino do recorte deve ser stem ou option:A…E")
    return match.group(1)


def crop_edits(question: dict, review: dict | None, image_path: str, *, target: str = "stem",
               replace: bool = False, suggestion: dict | None = None) -> dict:
    """Camada ``edits`` com o recorte, partindo do que a revisão mostra hoje.

    O recorte entra no enunciado ou na alternativa escolhida (somando às figuras
    existentes, ou trocando-as com ``replace``). Com ``suggestion``, os textos não
    vazios da camada de texto substituem enunciado/alternativas.
    """
    letter = _parse_target(target)
    current = question_review.apply_edits(question, review)
    edits = dict((review or {}).get("edits") or {})
    stem_figures = list(current.get("figuras_enunciado") or [])
    options = {}
    for key, value in (current.get("alternativas") or {}).items():
        value = value if isinstance(value, dict) else {"texto": str(value or "")}
        options[key] = {"texto": str(value.get("texto") or ""), "figuras": list(value.get("figuras") or [])}
    if letter is None:
        edits["figuras_enunciado"] = [image_path] if replace else [*stem_figures, image_path]
    else:
        option = options.setdefault(letter, {"texto": "", "figuras": []})
        option["figuras"] = [image_path] if replace else [*option["figuras"], image_path]
        edits["alternativas"] = options
    if suggestion:
        if str(suggestion.get("enunciado") or "").strip():
            edits["enunciado"] = suggestion["enunciado"]
        suggested_options = {key: value for key, value in (suggestion.get("alternativas") or {}).items()
                             if key in "ABCDE" and len(key) == 1 and str(value or "").strip()}
        if suggested_options:
            for key, value in suggested_options.items():
                options.setdefault(key, {"texto": "", "figuras": []})["texto"] = str(value)
            edits["alternativas"] = options
    if "alternativas" in edits:
        edits["alternativas"] = dict(sorted(edits["alternativas"].items()))
    return edits


def apply_crop_to_review(exam_json: Path, number: int, image_path: str, *, target: str = "stem",
                         replace: bool = False, suggestion: dict | None = None,
                         resolve_bad_crop: bool = True) -> dict:
    """Grava o recorte como correção da revisão da questão.

    Mantém status, matéria, conteúdo e observação. Uma questão marcada como
    ``bad_crop`` sai da fila de recortes ruins e volta a pendente
    (``resolve_bad_crop``), para ser conferida e aprovada com o recorte novo.
    """
    exam_json = Path(exam_json)
    data = json.loads(exam_json.read_text(encoding="utf-8"))
    question = next((q for q in data.get("questoes", []) if int(q.get("numero", -1)) == int(number)), None)
    if question is None:
        raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
    review = question_review.read_reviews(exam_json).get("questions", {}).get(str(number))
    edits = crop_edits(question, review, image_path, target=target, replace=replace, suggestion=suggestion)
    clean = question_review.validate_edits(question, edits, exam_json.parent)
    value = {key: item for key, item in (review or {}).items() if key not in ("edits", "edited_at")}
    if resolve_bad_crop and value.get("status") == "bad_crop":
        value.pop("status")
    value["edits"] = clean
    return question_review.save_review(exam_json, int(number), value)
