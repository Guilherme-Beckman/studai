"""Associação, leitura e importação de gabaritos de simulados."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from studai.integrations.ai_cache import cached_ai_result


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip().casefold()

def source_id_for(data: dict, path: Path) -> str:
    return str(data.get("prova") or f"{path.parent.name}/{path.stem}")

def exam_variant(path: Path) -> str | None:
    # Formas usadas no acervo FUVEST: prova_V, tipo_V, V1 e prova-V1.
    match = re.search(r"(?:prova|simulado|tipo)[-_ ]*([VKQXZ][1-4]?)\b", path.stem, re.I)
    return match.group(1).upper() if match else None

def exam_key(path: Path) -> str:
    value = path.stem.casefold().replace("_", "-")
    value = re.sub(r"(?:[- ]+)(?:gabarito|prova|respostas?)(?:[- ]+retificado)?(?:[- ]+[vs][1-4])?$", "", value)
    value = re.sub(r"[- ]+[vs][1-4]$", "", value)
    return _normalize(value)

def associate_answer_key(exam_pdf: Path, key_pdfs: list[Path], provider=None) -> tuple[Path | None, str]:
    """Match by normalized exam identity; consults configured LLM only if ambiguous."""
    key = exam_key(exam_pdf)
    exact = [p for p in key_pdfs if exam_key(p) == key]
    if len(exact) == 1:
        return exact[0], "filename"
    candidates = exact or key_pdfs
    if provider is None or not candidates:
        return None, "ambiguous"
    candidates = sorted(candidates)
    payload = {"prova": {"arquivo": exam_pdf.name, "identificador": key,
                           "versao": exam_variant(exam_pdf)},
               "gabaritos": [{"arquivo": p.name, "identificador": exam_key(p),
                               "versao": exam_variant(p)} for p in candidates]}
    prompt = ("Associe um PDF de prova ao gabarito correspondente usando somente identidade da prova, ano, edição, fase e versão. "
              "Não infira respostas. Responda somente JSON {\"arquivo\": nome_exato_ou_null, \"confianca\": 0..1}.\n"+
              json.dumps(payload, ensure_ascii=False))
    system = "Você associa arquivos de provas e gabaritos com cautela."

    def ask():
        failures = 0
        for correction in (None, "A resposta anterior não passou na validação. Escolha apenas um nome da lista ou null; JSON estrito."):
            try:
                raw = provider.complete(system, prompt if correction is None else correction+"\n"+prompt)
                obj = json.loads(raw)
                filename = obj.get("arquivo") if isinstance(obj, dict) else None
                confidence = float(obj.get("confianca", 0)) if isinstance(obj, dict) else 0
                found = next((p for p in candidates if p.name == filename), None)
                if found and confidence >= 0.8:
                    years_exam = set(re.findall(r"20\d{2}", exam_pdf.stem))
                    years_key = set(re.findall(r"20\d{2}", found.stem))
                    key_variant = exam_variant(found)
                    exam_version = exam_variant(exam_pdf)
                    generic = {"fuvest", "simulado", "prova", "gabarito", "retificado"}
                    exam_tokens = set(re.findall(r"[a-z]+|\d+", exam_key(exam_pdf))) - generic
                    key_tokens = set(re.findall(r"[a-z]+|\d+", exam_key(found))) - generic
                    identity_overlap = len(exam_tokens & key_tokens)
                    if (years_exam and years_exam == years_key and identity_overlap >= 2
                            and not (key_variant and exam_version and key_variant != exam_version)):
                        return {"arquivo": found.name}
            except Exception:
                failures += 1
                continue
        # Sem resposta aproveitável por falha de rede/JSON: não guarda, tenta de novo na próxima execução.
        return None if failures == 2 else {"arquivo": None}

    # O prompt traz só nomes de arquivos: rodar o lote de novo reaproveita a decisão anterior.
    decision = cached_ai_result("answer_key_match", provider, system, prompt, ask)
    found = next((p for p in candidates if decision and p.name == decision.get("arquivo")), None)
    if found:
        return found, "llm"
    return None, "ambiguous"

# Siglas de versão: V K Q X Z (1º exame e provas de um dia), T M J H P (2º exame,
# 1999–2002), V1–V4 (2025+) e S1–S4 (simulados).
GROUP_LETTERS = frozenset("VKQXZTMJHP")
VARIANTS = frozenset({*GROUP_LETTERS, *(f"{prefix}{n}" for prefix in "VSKQXZ" for n in range(1, 5))})
DASHES = str.maketrans({"‐": "-", "‑": "-", "–": "-", "—": "-", "−": "-"})
ANSWER = r"(ANULADA|[A-E*])"
# Gabarito retificado com duas respostas ("48 D E", "9 DE"): fica a primeira, como antes.
PAIR_RE = re.compile(r"(\d{1,3})\s*[-:]?\s*" + ANSWER + r"(?:\s?[A-E](?![A-Za-z]))*(?![A-Za-z])", re.I)
ROW_TOL = 3.0       # pt: palavras com o centro vertical a essa distância estão na mesma linha


def _answer(token: str) -> str:
    token = token.upper()
    return "*" if token == "ANULADA" else token


def _rows(words: list) -> list[list]:
    """Palavras agrupadas por linha (centro vertical), cada linha em ordem de x."""
    rows: list[list] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        mid = (w[1] + w[3]) / 2
        if rows and abs(mid - rows[-1][0]) <= ROW_TOL:
            rows[-1][1].append(w)
        else:
            rows.append([mid, [w]])
    return [sorted(row, key=lambda w: w[0]) for _, row in rows]


def _cx(w) -> float:
    return (w[0] + w[2]) / 2


def _cy(w) -> float:
    return (w[1] + w[3]) / 2


def _page_words(page: pymupdf.Page) -> list:
    return [(*w[:4], w[4].translate(DASHES)) for w in page.get_text("words")]


def _check_sequence(result: dict[int, str], label: str) -> dict[int, str]:
    nums = sorted(result)
    if nums != list(range(1, nums[-1] + 1)):
        raise ValueError(f"gabarito {label} possui numeração incompleta ou ambígua")
    return result


def _correspondence(words: list, carry: list[str] | None = None
                    ) -> tuple[dict[str, dict[int, str]] | None, list[str] | None]:
    """
    Tabela "RESPOSTA | GRUPO V | GRUPO K ..." (FUVEST 1999–2018; "PROVA V" em 2022):
    cada linha traz a resposta e o número da questão em cada versão, às vezes em
    dois blocos lado a lado. A sigla pode estar embaixo do "GRUPO" (2007). Página
    sem cabeçalho continua a tabela da anterior (``carry``, 1999 e 2000).
    Devolve ({versão: {número: resposta}}, ordem das versões).
    """
    heads = [w for w in words if w[4].upper() == "RESPOSTA"]
    order = carry
    header_bottom = float("-inf")
    if heads:
        top = min(w[1] for w in heads) - 20
        bottom = max(w[3] for w in heads) + 20
        titles = [w for w in words if w[4].upper() in {"GRUPO", "PROVA"} and top <= w[1] <= bottom]
        letters = [w for w in words if w[4] in VARIANTS and top <= w[1] <= bottom and any(
            # A sigla fica ao lado ("GRUPO V") ou embaixo do título (2007, 2016).
            abs(t[1] - w[1]) < 2 and 0 < w[0] - t[2] < 15 or _cy(w) - _cy(t) >= 5 and abs(_cx(t) - _cx(w)) < 20
            for t in titles)]
        first = min(w[0] for w in heads)
        second = min((w[0] for w in heads if w[0] > first + 50), default=float("inf"))
        order = [w[4] for w in sorted((w for w in letters if first <= _cx(w) < second), key=_cx)]
        if not order:
            return None, carry
        header_bottom = max(_cy(w) for w in [*heads, *letters]) + ROW_TOL
    if not order:
        return None, None
    size = len(order) + 1
    out: dict[str, dict[int, str]] = defaultdict(dict)
    for row in _rows([w for w in words if _cy(w) > header_bottom]):
        tokens = [w[4] for w in row]
        if not tokens or len(tokens) % size:
            continue
        chunks = [tokens[i:i + size] for i in range(0, len(tokens), size)]
        if not all(re.fullmatch(ANSWER, c[0], re.I) and all(re.fullmatch(r"\d{1,3}\*?", t) for t in c[1:])
                   for c in chunks):
            continue
        for chunk in chunks:
            letter = _answer(chunk[0])
            for version, num in zip(order, chunk[1:]):
                n = int(num.rstrip("*"))
                if out[version].get(n, letter) != letter:
                    raise ValueError(f"gabarito ambíguo: questão {n} da versão {version} aparece com respostas diferentes")
                out[version][n] = letter
    return ({k: v for k, v in out.items() if v} or None), order


def _version_grid(words: list, page_width: float) -> dict[str, dict[int, str]] | None:
    """
    Grade por versão sob os títulos "PROVA V  PROVA K ...": pares "V 01-D", "01 – D",
    "1‐C" ou "1  E". Quando os pares trazem a sigla ("V 01-D"), ela decide a versão;
    senão, a coluna mais próxima do título.
    """
    headers: dict[str, object] = {}
    for i, w in enumerate(words):
        prev = words[i - 1][4].upper() if i else ""
        if w[4] in VARIANTS and prev in {"PROVA", "GRUPO"}:
            headers.setdefault(w[4], words[i - 1])
        elif w[4].upper().startswith("PROVA") and w[4][5:].strip() in VARIANTS:
            headers.setdefault(w[4][5:].strip(), w)
    if not headers:
        return None
    start = min(h[1] for h in headers.values()) - 2
    rows = _rows([w for w in words if w[1] > start])
    prefixed = re.compile(r"\b([VKQXZTMJHP])\s*(\d{1,3})\s*-\s*" + ANSWER + r"(?![A-Za-z])", re.I)
    out: dict[str, dict[int, str]] = defaultdict(dict)

    def put(version: str, n: int, letter: str) -> None:
        if out[version].get(n, letter) != letter:
            raise ValueError(f"gabarito ambíguo: questão {n} da versão {version} aparece com respostas diferentes")
        out[version][n] = letter

    texts = [" ".join(w[4] for w in row) for row in rows]
    if sum(len(prefixed.findall(t)) for t in texts) >= 10:
        for text in texts:
            for m in prefixed.finditer(text):
                if m.group(1) in headers:
                    put(m.group(1), int(m.group(2)), _answer(m.group(3)))
        return {k: v for k, v in out.items() if v} or None
    xs = sorted((_cx(h), v) for v, h in headers.items())
    edges = [-1.0] + [(a[0] + b[0]) / 2 for a, b in zip(xs, xs[1:])] + [page_width + 1]
    header_bottom = max(_cy(h) for h in headers.values()) + ROW_TOL
    for row in rows:
        if _cy(row[0]) <= header_bottom:
            continue
        for k, (_, version) in enumerate(xs):
            text = " ".join(w[4] for w in row if edges[k] <= _cx(w) < edges[k + 1]).strip()
            pairs = list(PAIR_RE.finditer(text))
            # Só linhas compostas inteiramente de pares (com ou sem a sigla da versão):
            # texto livre não vira resposta.
            if not pairs or re.sub(rf"\b{re.escape(version)}\b", "", PAIR_RE.sub("", text)).strip(" |;,"):
                continue
            for m in pairs:
                put(version, int(m.group(1)), _answer(m.group(2)))
    return {k: v for k, v in out.items() if v} or None


def _line_pairs(doc: pymupdf.Document, pages: list[int]) -> dict[int, str]:
    """Compatível com linhas "1-A", "Questão 1: A" e tabelas "1 A 2 C ..."."""
    pair = re.compile(r"(?:Q(?:UEST[AÃ]O)?\s*)?(\d{1,3})\s*[-:.)]?\s*[\[(]?([A-E*])[\])]??", re.I)
    answers: dict[int, str] = {}
    prefix = re.compile(r"^\s*(?:(?:GABARITO|RESPOSTAS?)\s*[:\-]?\s*)?", re.I)
    allowed_between = re.compile(r"^[\s,;|/\\.\-:()\[\]]*$")
    for pno in pages:
        for raw in doc[pno].get_text("text").splitlines():
            line = prefix.sub("", raw).strip()
            if not line:
                continue
            matches = list(pair.finditer(line))
            if not matches:
                continue
            cursor = 0
            parsed: list[tuple[int, str]] = []
            for match in matches:
                if not allowed_between.fullmatch(line[cursor:match.start()]):
                    parsed = []
                    break
                parsed.append((int(match.group(1)), match.group(2).upper()))
                cursor = match.end()
            if not parsed or not allowed_between.fullmatch(line[cursor:]):
                continue
            # Evita interpretar páginas, datas ou títulos como sequência de respostas.
            if any(n < 1 or n > 90 for n, _ in parsed):
                continue
            for number, letter in parsed:
                if number in answers and answers[number] != letter:
                    raise ValueError(f"gabarito ambíguo: questão {number} aparece com respostas diferentes")
                answers[number] = letter
    return answers


def parse_answer_key_pdf(path: Path, variant: str | None = None,
                         pages: list[int] | None = None) -> dict[int, str]:
    """
    Gabarito de uma versão da prova. Lê os formatos do acervo FUVEST em todas as
    páginas (ou só em ``pages``): tabela de correspondência entre grupos
    (1999–2018), grade por versão ("V 01-D", "01 – D", "1 E") e, por último, linhas
    simples ("1-A", "Questão 1: A").

    Linhas fora desses padrões são ignoradas; números duplicados com alternativas
    conflitantes invalidam o gabarito em vez de escolher uma delas.
    """
    variant = variant.upper() if variant else None
    tables = _read_tables(path, pages)
    if tables:
        if not variant:
            if len(tables) != 1:
                raise ValueError("gabarito contém várias versões; informe a versão (ex.: V ou V1); "
                                 f"disponíveis: {', '.join(sorted(tables))}")
            variant = next(iter(tables))
        if variant not in tables:
            raise ValueError(f"versão {variant} não encontrada no gabarito; disponíveis: {', '.join(sorted(tables))}")
        return _check_sequence(tables[variant], variant)
    with pymupdf.open(path) as doc:
        answers = _line_pairs(doc, list(range(len(doc))) if pages is None else pages)
    if not answers:
        raise ValueError("nenhum par questão/alternativa reconhecido; revise o PDF e prepare um gabarito JSON")
    return answers


def _read_tables(path: Path, pages: list[int] | None = None) -> dict[str, dict[int, str]]:
    """{versão: {número: resposta}} das tabelas de correspondência e grades do PDF."""
    tables: dict[str, dict[int, str]] = defaultdict(dict)
    with pymupdf.open(path) as doc:
        carry: list[str] | None = None
        for pno in (range(len(doc)) if pages is None else pages):
            words = _page_words(doc[pno])
            found, carry = _correspondence(words, carry)
            found = found or _version_grid(words, doc[pno].rect.width) or {}
            for version, answers in found.items():
                for n, letter in answers.items():
                    if tables[version].get(n, letter) != letter:
                        raise ValueError(f"gabarito ambíguo: questão {n} da versão {version} "
                                         "aparece com respostas diferentes")
                    tables[version][n] = letter
    return dict(tables)


def answer_key_versions(path: Path, pages: list[int] | None = None) -> list[str]:
    """Versões presentes no gabarito (V, K, T, V1…), em ordem alfabética."""
    return sorted(_read_tables(path, pages))


KEY_PAGE_RE = re.compile(r"GABARITO", re.I)
KEY_PAIR_RE = re.compile(r"\b\d{1,3}\s*[-‐‑–]\s*(?:[A-E*]|ANULADA)\b", re.I)


def answer_key_pages(doc: pymupdf.Document) -> list[int]:
    """Páginas de gabarito dentro de um PDF de prova (fim das provas de 1997–2004)."""
    out = []
    for pno, page in enumerate(doc):
        text = page.get_text("text")
        if not KEY_PAGE_RE.search(text):
            continue
        if len(KEY_PAIR_RE.findall(text)) >= 20 or re.search(r"RESPOSTA\s+GRUPO", text, re.I):
            out.append(pno)
    return out


def embedded_answer_key(exam_pdf: Path, variant: str | None = None, ocr=None) -> dict[int, str] | None:
    """
    Gabarito impresso no próprio PDF da prova (última página em 1997–2004), ou None
    se não houver. Com uma só versão no gabarito, ela vale para a prova: a capa de
    2001 (2º exame) cita o grupo V, mas o gabarito impresso é o da prova T.
    """
    with pymupdf.open(exam_pdf) as doc:
        pages = answer_key_pages(doc)
    if not pages:
        return None
    if variant is None:
        try:
            return parse_answer_key_pdf(exam_pdf, None, pages=pages)
        except ValueError:
            variant = detect_exam_variant(exam_pdf, ocr=ocr)
    return parse_answer_key_pdf(exam_pdf, variant, pages=pages)


VARIANT_TEXT_RES = (
    # "GRUPO V", "PROVA \"K\"", "Prova T", "tipo V"
    re.compile(r"(?i:\b(?:prova|grupo|tipo))\s*[\"“”']?\s*([VKQXZTMJHP][1-4]?)\b(?![a-zà-ú])"),
    # número da questão com a sigla: "V.01 –", "T.77 –", "K.15 –" (1997–2002)
    re.compile(r"\b([VKQXZTMJHP])\.\s?\d{2}\s*[-–—]"),
    # rodapé "PAG 05/28 V" (2019–2021)
    re.compile(r"(?i:\bPAG)\.?\s*\d{1,2}\s*/\s*\d{1,2}\s+([VKQXZ][1-4]?)\b"),
)
OCR_VARIANT_PAGES = 3      # páginas lidas por OCR quando o texto do PDF não serve (2021)


def _variant_counts(texts) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for text in texts:
        for rx in VARIANT_TEXT_RES:
            for m in rx.finditer(text):
                counts[m.group(1)] += 1
    return counts


def detect_exam_variant(exam_pdf: Path, ocr=None) -> str | None:
    """
    Versão da prova pelo nome do arquivo e, se faltar, pelo texto das páginas que
    não são gabarito ("GRUPO V", "V.01 –", "PAG 05/28 V"). ``ocr`` é um motor
    ``(page, dpi) -> linhas`` (``questions.ocr``) usado só quando o texto do PDF
    não traz nenhuma pista (fonte em bitmap, 2021). None quando nada decide ou
    duas versões aparecem em proporção parecida: aí o gabarito não é chutado.
    """
    named = exam_variant(exam_pdf)
    if named:
        return named
    try:
        doc = pymupdf.open(exam_pdf)
    except Exception:
        return None
    with doc:
        skip = set(answer_key_pages(doc))
        content = [pno for pno in range(len(doc)) if pno not in skip]
        counts = _variant_counts(doc[pno].get_text("text") for pno in content)
        if not counts and ocr is not None:
            texts = []
            for pno in content[1:1 + OCR_VARIANT_PAGES]:
                try:
                    texts.append("\n".join(line.text for line in ocr(doc[pno], 200)))
                except Exception:
                    break
            counts = _variant_counts(texts)
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(ranked) > 1 and ranked[1][1] * 2 > ranked[0][1]:
        return None
    return ranked[0][0]


def import_answer_key(db: sqlite3.Connection, pdf_path: Path, questions_path: Path,
                      *, replace: bool = False, variant: str | None = None,
                      association_method: str = "manual", pages: list[int] | None = None,
                      allow_missing_questions: bool = False) -> dict:
    """
    Importa o gabarito de ``pdf_path`` para a prova. Por padrão, um número do
    gabarito sem questão extraída recusa tudo (sinal de desalinhamento). Com
    ``allow_missing_questions`` (provas com OCR que perdeu algum número, cuja
    numeração continua sendo a da prova), esses números ficam de fora e são
    listados em ``missing_questions``.
    """
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    source_id = source_id_for(data, questions_path)
    questions = {int(q["numero"]): q for q in data.get("questoes", []) if "numero" in q}
    parsed = parse_answer_key_pdf(pdf_path, variant, pages=pages)
    missing = sorted(set(parsed) - set(questions)) if allow_missing_questions else []
    parsed = {n: a for n, a in parsed.items() if n not in missing}
    invalid = []
    for number, letter in parsed.items():
        question = questions.get(number)
        if question is None or letter not in "ABCDE*":
            invalid.append((number, letter))
    if invalid:
        raise ValueError("gabarito não corresponde ao simulado para: " + ", ".join(f"Q{n:02d}={a}" for n, a in invalid))
    existing = _store_answers(db, source_id, parsed, str(pdf_path.resolve()), variant=variant,
                              association_method=association_method, replace=replace)
    return {"source_id": source_id, "imported": len(parsed), "existing": len(existing),
            "unkeyed_questions": sorted(set(questions) - set(parsed)), "missing_questions": missing}


def _store_answers(db: sqlite3.Connection, source_id: str, answers: dict[int, str], source: str, *,
                   variant: str | None, association_method: str, replace: bool) -> dict[int, str]:
    """Grava as respostas; recusa divergência com o que já está salvo sem ``replace``."""
    existing = {int(row[0]): row[1] for row in db.execute(
        "SELECT question_number,answer FROM answer_keys WHERE source_id=?", (source_id,))}
    conflicts = [(n, a, existing[n]) for n, a in answers.items() if n in existing and existing[n] != a]
    if conflicts and not replace:
        raise ValueError("há respostas diferentes já importadas; confira o PDF ou use --replace: " +
                         ", ".join(f"Q{n:02d} novo={a} salvo={old}" for n, a, old in conflicts))
    now = datetime.now(timezone.utc).isoformat()
    with db:
        if replace:
            db.execute("DELETE FROM answer_keys WHERE source_id=?", (source_id,))
        for number, letter in answers.items():
            db.execute("""INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,is_annulled,variant,association_method)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id,question_number) DO UPDATE SET
                answer=excluded.answer,source_pdf=excluded.source_pdf,imported_at=excluded.imported_at,
                is_annulled=excluded.is_annulled,variant=excluded.variant,association_method=excluded.association_method""",
                       (source_id, number, letter, source, now, int(letter == "*"), variant, association_method))
    return existing


def printed_answer_key(data: dict) -> dict[int, str]:
    """Respostas impressas junto das questões ("Resposta Correta D", FUVEST 1996)."""
    out = {}
    for q in data.get("questoes", []):
        letter = str(q.get("gabarito_impresso") or "").strip().upper()
        if "numero" in q and len(letter) == 1 and letter in "ABCDE*":
            out[int(q["numero"])] = letter
    return out


def import_embedded_answer_key(db: sqlite3.Connection, exam_pdf: Path, questions_path: Path, *,
                               variant: str | None = None, ocr=None, replace: bool = False) -> dict | None:
    """
    Importa o gabarito que vem dentro da própria prova: a página de gabarito do fim
    do PDF (1997–2004) ou as respostas impressas em cada questão (1996). None
    quando a prova não traz gabarito. Sem ``replace``, não mexe em prova que já
    tem respostas salvas. Questões que a extração não achou ficam de fora e são
    listadas em ``missing_questions`` (o número da questão é o mesmo da prova).
    """
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    source_id = source_id_for(data, questions_path)
    if not replace and db.execute("SELECT 1 FROM answer_keys WHERE source_id=? LIMIT 1", (source_id,)).fetchone():
        return None
    answers, method = embedded_answer_key(exam_pdf, variant, ocr=ocr), "embedded"
    if not answers:
        answers, method = printed_answer_key(data), "printed"
    if not answers:
        return None
    numbers = {int(q["numero"]) for q in data.get("questoes", []) if "numero" in q}
    kept = {n: a for n, a in answers.items() if n in numbers}
    if not kept:
        return None
    variant = variant or (detect_exam_variant(exam_pdf) if method == "embedded" else None)
    _store_answers(db, source_id, kept, str(Path(exam_pdf).resolve()), variant=variant,
                   association_method=method, replace=replace)
    return {"source_id": source_id, "imported": len(kept), "association_method": method,
            "missing_questions": sorted(set(answers) - numbers),
            "unkeyed_questions": sorted(numbers - set(kept))}


def set_reviewed_answer(db: sqlite3.Connection, source_id: str, number: int, answer: str) -> None:
    """Gabarito corrigido na revisão humana: letra A–E, "*" para anulada ou "" para remover."""
    answer = str(answer or "").strip().upper()
    if answer and (len(answer) != 1 or answer not in "ABCDE*"):
        raise ValueError("gabarito deve ser A, B, C, D, E ou * (anulada)")
    with db:
        if not answer:
            db.execute("DELETE FROM answer_keys WHERE source_id=? AND question_number=?", (source_id, number))
            return
        db.execute("""INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,is_annulled,variant,association_method)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id,question_number) DO UPDATE SET
            answer=excluded.answer,imported_at=excluded.imported_at,is_annulled=excluded.is_annulled,
            association_method=excluded.association_method""",
                   (source_id, number, answer, "revisao", datetime.now(timezone.utc).isoformat(),
                    int(answer == "*"), None, "review"))


CORPUS_KEY_FILENAME = "gabarito.json"


def seed_corpus_answer_keys(db: sqlite3.Connection, root: Path) -> dict[str, int]:
    """Importa o ``gabarito.json`` distribuído junto a cada prova do acervo.

    O banco de histórico não é versionado; sem isto, um clone novo tem as
    questões revisadas, mas nenhum gabarito. Só preenche provas sem nenhuma
    resposta no banco, para não sobrescrever correções locais. Retorna
    ``{source_id: respostas importadas}``.
    """
    seeded: dict[str, int] = {}
    for key_path in sorted(Path(root).rglob(CORPUS_KEY_FILENAME)):
        questions_path = key_path.with_name("questoes.classificado.json")
        if not questions_path.exists():
            continue
        try:
            data = json.loads(questions_path.read_text(encoding="utf-8"))
            raw = json.loads(key_path.read_text(encoding="utf-8"))
            answers = parse_answer_key(key_path)
        except (OSError, ValueError):
            continue
        source_id = source_id_for(data, questions_path)
        if db.execute("SELECT 1 FROM answer_keys WHERE source_id=? LIMIT 1", (source_id,)).fetchone():
            continue
        numbers = {int(q["numero"]) for q in data.get("questoes", []) if "numero" in q}
        answers = {n: a for n, a in answers.items() if n in numbers}
        variant = raw.get("variante") if isinstance(raw, dict) else None
        now = datetime.now(timezone.utc).isoformat()
        with db:
            db.executemany(
                "INSERT OR IGNORE INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,"
                "is_annulled,variant,association_method) VALUES(?,?,?,?,?,?,?,?)",
                [(source_id, n, a, CORPUS_KEY_FILENAME, now, int(a == "*"), variant, "corpus")
                 for n, a in sorted(answers.items())])
        seeded[source_id] = len(answers)
    return seeded


def parse_answer_key(path: Path, variant: str | None = None) -> dict[int, str]:
    """Lê gabarito PDF ou JSON no formato {número: alternativa}."""
    if path.suffix.lower() == ".pdf":
        return parse_answer_key_pdf(path, variant)
    if path.suffix.lower() != ".json":
        raise ValueError("gabarito deve ser PDF com texto selecionável ou JSON estruturado")
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("gabarito", data) if isinstance(data, dict) else None
    if not isinstance(values, dict):
        raise ValueError("JSON de gabarito deve ser um objeto {numero: alternativa} ou {gabarito: {...}}")
    output = {}
    for raw_num, raw_answer in values.items():
        answer = str(raw_answer).strip().upper()
        if not str(raw_num).isdigit() or len(answer) != 1 or answer not in "ABCDE*":
            raise ValueError(f"entrada inválida no gabarito: {raw_num!r}: {raw_answer!r}")
        output[int(raw_num)] = answer
    if not output:
        raise ValueError("gabarito JSON vazio")
    return output

def import_key_file(db, pdf_path: Path, questions_path: Path, replace: bool, variant: str | None = None):
    parsed = parse_answer_key(pdf_path, variant)
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    source_id = source_id_for(data, questions_path)
    questions = {int(q["numero"]): q for q in data.get("questoes", []) if "numero" in q}
    invalid = [(n, a) for n, a in parsed.items() if n not in questions or a not in "ABCDE*"]
    if invalid:
        raise ValueError("gabarito não corresponde ao simulado: " + ", ".join(f"Q{n:02d}={a}" for n, a in invalid))
    existing = {int(r[0]): r[1] for r in db.execute(
        "SELECT question_number,answer FROM answer_keys WHERE source_id=?", (source_id,))}
    conflicts = [(n, a, existing[n]) for n, a in parsed.items() if n in existing and a != existing[n]]
    if conflicts and not replace:
        raise ValueError("respostas conflitantes já importadas; confira a fonte ou use --replace")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with db:
        if replace:
            db.execute("DELETE FROM answer_keys WHERE source_id=?", (source_id,))
        for number, letter in parsed.items():
            db.execute("""INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,is_annulled,variant,association_method)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id,question_number) DO UPDATE SET
                answer=excluded.answer,source_pdf=excluded.source_pdf,imported_at=excluded.imported_at,
                is_annulled=excluded.is_annulled,variant=excluded.variant,association_method=excluded.association_method""",
                       (source_id, number, letter, str(pdf_path.resolve()), now, int(letter == "*"), variant, "manual"))
    return {"source_id": source_id, "imported": len(parsed), "previously_present": len(existing),
            "questions_without_key": sorted(set(questions) - set(parsed))}
