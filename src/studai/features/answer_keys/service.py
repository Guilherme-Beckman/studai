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

def parse_answer_key_pdf(path: Path, variant: str | None = None) -> dict[int, str]:
    """Lê apenas linhas estritamente compostas por pares questão-alternativa.

    Linhas que não seguem esse padrão são ignoradas; números duplicados com
    alternativas conflitantes invalidam o gabarito em vez de escolher uma delas.
    """
    doc = pymupdf.open(path)
    # Gabaritos FUVEST recentes usam colunas por versão. Em versões antigas,
    # cada grupo contém dois pares questão/resposta por linha e repete a sigla.
    variant = variant.upper() if variant else None
    try:
        page = doc[0]
        words = page.get_text("words")
        accepted = {"V", "K", "Q", "X", "Z", *(f"{prefix}{n}" for prefix in "VSKQXZ" for n in range(1, 5))}
        headers: dict[str, float] = {}
        for i, word in enumerate(words):
            token = word[4].upper()
            if token not in accepted:
                continue
            previous = words[i-1][4].upper() if i else ""
            if previous in {"PROVA", "GRUPO"}:
                headers[token] = (word[0] + word[2]) / 2
        # Alguns PDFs extraem o título PROVA V como um único token ou deslocam
        # a posição de PROVA; títulos em cabeçalho ainda identificam a versão.
        if len(headers) < 2:
            for word in words:
                token = word[4].upper()
                if token in accepted and word[1] < 240:
                    headers.setdefault(token, (word[0] + word[2]) / 2)
        if headers:
            if not variant:
                if len(headers) != 1:
                    raise ValueError("gabarito contém várias versões; informe a versão (ex.: V ou V1)")
                variant = next(iter(headers))
            if variant not in headers:
                raise ValueError(f"versão {variant} não encontrada no gabarito; disponíveis: {', '.join(sorted(headers))}")
            x = headers[variant]
            xs = sorted(headers.values())
            left = (xs[xs.index(x)-1] + x)/2 if xs.index(x) else -1
            right = (x + xs[xs.index(x)+1])/2 if xs.index(x)+1 < len(xs) else page.rect.width+1
            selected = [w for w in words if w[1] > min(word[1] for word in words if word[4].upper() == variant)
                        and left <= (w[0]+w[2])/2 < right]
            rows: dict[float, list[tuple[float,str]]] = defaultdict(list)
            for w in selected:
                rows[round(w[1], 1)].append(((w[0]+w[2])/2, w[4].upper().replace("‐", "-").replace("‑", "-")))
            result: dict[int,str] = {}
            for row in rows.values():
                tokens = [t for _,t in sorted(row)]
                i = 0
                while i < len(tokens):
                    token = tokens[i]
                    if token in headers:
                        i += 1
                        continue
                    match = re.fullmatch(r"(\d{1,2})-?", token)
                    if match and i + 1 < len(tokens) and tokens[i+1] in {*"ABCDE", "*"}:
                        result[int(match.group(1))] = tokens[i+1]
                        i += 2
                        continue
                    pair = re.fullmatch(r"(\d{1,2})[-:]([A-E*])", token)
                    if pair:
                        result[int(pair.group(1))] = pair.group(2)
                    i += 1
            if result:
                nums = sorted(result)
                if nums != list(range(nums[0], nums[-1]+1)):
                    raise ValueError(f"gabarito {variant} possui numeração incompleta ou ambígua")
                doc.close()
                return result
    except Exception:
        doc.close()
        raise
    # Compatível com linhas "1-A", "Questão 1: A" e tabelas "1 A 2 C ...".
    pair = re.compile(r"(?:Q(?:UEST[AÃ]O)?\s*)?(\d{1,3})\s*[-:.)]?\s*[\[(]?([A-E*])[\])]??", re.I)
    answers: dict[int, str] = {}
    prefix = re.compile(r"^\s*(?:(?:GABARITO|RESPOSTAS?)\s*[:\-]?\s*)?", re.I)
    allowed_between = re.compile(r"^[\s,;|/\\.\-:()\[\]]*$")
    try:
        for page in doc:
            for raw in page.get_text("text").splitlines():
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
    finally:
        doc.close()
    if not answers:
        raise ValueError("nenhum par questão/alternativa reconhecido; revise o PDF e prepare um gabarito JSON")
    return answers

def import_answer_key(db: sqlite3.Connection, pdf_path: Path, questions_path: Path,
                      *, replace: bool = False, variant: str | None = None,
                      association_method: str = "manual") -> dict:
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    source_id = source_id_for(data, questions_path)
    questions = {int(q["numero"]): q for q in data.get("questoes", []) if "numero" in q}
    parsed = parse_answer_key_pdf(pdf_path, variant)
    invalid = []
    for number, letter in parsed.items():
        question = questions.get(number)
        if question is None or letter not in "ABCDE*":
            invalid.append((number, letter))
    if invalid:
        raise ValueError("gabarito não corresponde ao simulado para: " + ", ".join(f"Q{n:02d}={a}" for n, a in invalid))
    existing = {int(row[0]): row[1] for row in db.execute(
        "SELECT question_number,answer FROM answer_keys WHERE source_id=?", (source_id,))}
    conflicts = [(n, a, existing[n]) for n, a in parsed.items() if n in existing and existing[n] != a]
    if conflicts and not replace:
        raise ValueError("há respostas diferentes já importadas; confira o PDF ou use --replace: " +
                         ", ".join(f"Q{n:02d} novo={a} salvo={old}" for n, a, old in conflicts))
    now = datetime.now(timezone.utc).isoformat()
    with db:
        if replace:
            db.execute("DELETE FROM answer_keys WHERE source_id=?", (source_id,))
        for number, letter in parsed.items():
            db.execute("""INSERT INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,is_annulled,variant,association_method)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id,question_number) DO UPDATE SET
                answer=excluded.answer,source_pdf=excluded.source_pdf,imported_at=excluded.imported_at,
                is_annulled=excluded.is_annulled,variant=excluded.variant,association_method=excluded.association_method""",
                       (source_id, number, letter, str(pdf_path.resolve()), now, int(letter == "*"), variant, association_method))
    return {"source_id": source_id, "imported": len(parsed), "existing": len(existing),
            "unkeyed_questions": sorted(set(questions) - set(parsed))}


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
