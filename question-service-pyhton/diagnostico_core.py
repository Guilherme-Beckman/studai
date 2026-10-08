"""Lógica determinística e persistência do diagnóstico por curso."""
from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

import classifier as clf

SCHEMA_VERSION = 2
REASONS = {"unknown_content", "calc_error", "misread", "blank"}
ACTIONS = {"theory", "calculation_practice", "interpretation_practice", "review"}
ACTION_BY_REASON = {"unknown_content": "theory", "calc_error": "calculation_practice",
                    "misread": "interpretation_practice", "blank": "review"}
ALIASES = {
    "arte": "artes", "artes": "artes", "portugues": "lingua portuguesa",
    "lingua portuguesa": "lingua portuguesa", "educacao fisica": "educacao fisica",
}


def norm(value: str) -> str:
    return clf._norm(str(value))


def canonical_subject(value: str, subjects: list[str]) -> str | None:
    n = norm(value)
    aliases = {norm(k): norm(v) for k, v in ALIASES.items()}
    wanted = aliases.get(n, n)
    return next((subject for subject in subjects if norm(subject) == wanted), None)


def load_course_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("version"), int) or not isinstance(data.get("cursos"), dict):
        raise ValueError("configuração de cursos inválida")
    return data


def course_profile(config: dict, course_id: str) -> dict:
    try:
        profile = config["cursos"][course_id]
    except KeyError:
        raise ValueError(f"curso não configurado: {course_id}") from None
    weights = profile.get("pesos")
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"curso {course_id} não possui pesos")
    for subject, weight in weights.items():
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"peso inválido para {subject}: use um número positivo")
    return profile


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS answer_keys (
            source_id TEXT NOT NULL, question_number INTEGER NOT NULL, answer TEXT NOT NULL,
            source_pdf TEXT NOT NULL, imported_at TEXT NOT NULL,
            is_annulled INTEGER NOT NULL DEFAULT 0, variant TEXT, association_method TEXT,
            PRIMARY KEY(source_id, question_number)
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY, course_id TEXT NOT NULL, course_name TEXT NOT NULL,
            config_version INTEGER NOT NULL, profile_snapshot TEXT NOT NULL,
            seed INTEGER NOT NULL, requested_count INTEGER NOT NULL, actual_count INTEGER NOT NULL,
            status TEXT NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            metrics_json TEXT, analysis_json TEXT, analysis_status TEXT,
            analysis_error TEXT
        );
        CREATE TABLE IF NOT EXISTS attempt_questions (
            attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, source_id TEXT NOT NULL, question_number INTEGER NOT NULL,
            subject TEXT NOT NULL, topic TEXT NOT NULL, question_json TEXT NOT NULL,
            correct_answer TEXT NOT NULL, selected_answer TEXT, error_reason TEXT,
            is_correct INTEGER, answered_at TEXT,
            PRIMARY KEY(attempt_id, ordinal),
            UNIQUE(attempt_id, source_id, question_number)
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(answer_keys)")}
    for name, declaration in (("is_annulled", "INTEGER NOT NULL DEFAULT 0"),
                              ("variant", "TEXT"), ("association_method", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE answer_keys ADD COLUMN {name} {declaration}")
    old = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    if old and int(old[0]) > SCHEMA_VERSION:
        db.close()
        raise ValueError("banco de diagnóstico foi criado por uma versão mais nova")
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    db.commit()
    return db


def source_id_for(data: dict, path: Path) -> str:
    return str(data.get("prova") or f"{path.parent.name}/{path.stem}")


def exam_variant(path: Path) -> str | None:
    match = re.search(r"(?:prova|simulado)[-_ ]*([VS][1-4])\b", path.stem, re.I)
    return match.group(1).upper() if match else None


def exam_key(path: Path) -> str:
    value = path.stem.casefold().replace("_", "-")
    value = re.sub(r"(?:[- ]+)(?:gabarito|prova|respostas?)(?:[- ]+retificado)?(?:[- ]+[vs][1-4])?$", "", value)
    value = re.sub(r"[- ]+[vs][1-4]$", "", value)
    return norm(value)


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
    for correction in (None, "A resposta anterior não passou na validação. Escolha apenas um nome da lista ou null; JSON estrito."):
        try:
            raw = provider.complete("Você associa arquivos de provas e gabaritos com cautela.",
                                    prompt if correction is None else correction+"\n"+prompt)
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
                    return found, "llm"
        except Exception:
            continue
    return None, "ambiguous"


def parse_answer_key_pdf(path: Path, variant: str | None = None) -> dict[int, str]:
    """Lê apenas linhas estritamente compostas por pares questão-alternativa.

    Linhas que não seguem esse padrão são ignoradas; números duplicados com
    alternativas conflitantes invalidam o gabarito em vez de escolher uma delas.
    """
    doc = pymupdf.open(path)
    # Gabaritos FUVEST recentes usam tabela por versão: cada coluna tem
    # questão/resposta e itens anulados são marcados com '*'.
    variant = variant.upper() if variant else None
    variant_header = re.compile(r"(?:PROVA|GRUPO)\s+([VS][1-4])", re.I)
    try:
        page = doc[0]
        words = page.get_text("words")
        headers: dict[str, float] = {}
        for word in words:
            if word[4].upper() in {"V1", "V2", "V3", "V4", "S1", "S2", "S3", "S4"}:
                headers[word[4].upper()] = (word[0] + word[2]) / 2
        # Aceita também cabeçalhos quebrados em tokens PROVA + V1.
        for i, word in enumerate(words[:-1]):
            if word[4].upper() in {"PROVA", "GRUPO"} and words[i+1][4].upper() in {"V1","V2","V3","V4","S1","S2","S3","S4"}:
                headers[words[i+1][4].upper()] = (words[i+1][0] + words[i+1][2]) / 2
        if headers:
            if not variant:
                if len(headers) != 1:
                    raise ValueError("gabarito contém várias versões; informe a versão (ex.: V1 ou S1)")
                variant = next(iter(headers))
            if variant not in headers:
                raise ValueError(f"versão {variant} não encontrada no gabarito; disponíveis: {', '.join(sorted(headers))}")
            x = headers[variant]
            xs = sorted(headers.values())
            left = (xs[xs.index(x)-1] + x)/2 if xs.index(x) else -1
            right = (x + xs[xs.index(x)+1])/2 if xs.index(x)+1 < len(xs) else page.rect.width+1
            selected = [w for w in words if left <= (w[0]+w[2])/2 < right]
            rows: dict[float, list[tuple[float,str]]] = defaultdict(list)
            for w in selected:
                rows[round(w[1], 1)].append(((w[0]+w[2])/2, w[4]))
            result: dict[int,str] = {}
            for row in rows.values():
                tokens = [t for _,t in sorted(row)]
                for i in range(0, len(tokens)-1, 2):
                    if tokens[i].isdigit() and (tokens[i+1].upper() in "ABCDE" or tokens[i+1] == "*"):
                        result[int(tokens[i])] = tokens[i+1].upper()
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


def load_question_pool(root: Path, db: sqlite3.Connection, subjects: list[str]) -> tuple[list[dict], dict]:
    pool: list[dict] = []
    excluded = Counter()
    seen_questions: set[tuple[str, int]] = set()
    for path in sorted(root.rglob("questoes.classificado.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            excluded["json_invalid"] += 1
            continue
        source_id = source_id_for(data, path)
        shared_texts = data.get("textos_compartilhados", {})
        keys = {int(r[0]): r[1] for r in db.execute(
            "SELECT question_number,answer FROM answer_keys WHERE source_id=? AND is_annulled=0", (source_id,))}
        for question in data.get("questoes", []):
            try:
                number = int(question["numero"])
            except (KeyError, TypeError, ValueError):
                excluded["invalid_number"] += 1
                continue
            identity = (source_id, number)
            if identity in seen_questions:
                excluded["duplicate_question"] += 1
                continue
            seen_questions.add(identity)
            raw_subject = question.get("materia") or question.get("classificacao", {}).get("materia")
            subject = canonical_subject(raw_subject, subjects) if raw_subject else None
            answer = keys.get(number)
            alternatives = question.get("alternativas") or {}
            if not subject:
                excluded["unclassified"] += 1
                continue
            if answer not in alternatives:
                excluded["no_valid_answer_key"] += 1
                continue
            topic = str((question.get("classificacao") or {}).get("assunto") or "").strip()
            if not topic:
                topic = "Tópico não informado"
            question_copy = dict(question)
            question_copy["_source_dir"] = str(path.parent.resolve())
            shared = shared_texts.get(question.get("texto_id") or "")
            if shared and shared.get("texto"):
                question_copy["_texto_compartilhado"] = shared["texto"]
            pool.append({"source_id": source_id, "source_file": str(path), "number": number,
                         "subject": subject, "topic": topic, "question": question_copy,
                         "correct_answer": answer})
    return pool, dict(excluded)


def allocate_counts(available: dict[str, int], weights: dict[str, float], total: int) -> dict[str, int]:
    """Distribui cotas pelo método dos maiores restos, respeitando a capacidade."""
    if total < 1:
        raise ValueError("quantidade deve ser positiva")
    caps = {s: max(0, int(available.get(s, 0))) for s in weights}
    allocated = {s: 0 for s in weights}
    remaining = min(total, sum(caps.values()))
    while remaining:
        active = [s for s in weights if allocated[s] < caps[s]]
        if not active:
            break
        weight_sum = sum(float(weights[s]) for s in active)
        shares = {s: remaining * float(weights[s]) / weight_sum for s in active}
        floors = {s: min(caps[s] - allocated[s], int(shares[s])) for s in active}
        placed = sum(floors.values())
        for s, count in floors.items():
            allocated[s] += count
        remaining -= placed
        if not remaining:
            break
        ranked = sorted((s for s in active if allocated[s] < caps[s]),
                        key=lambda s: (-(shares[s] - int(shares[s])), s.casefold()))
        if not ranked:
            break
        one = min(remaining, len(ranked))
        for subject in ranked[:one]:
            allocated[subject] += 1
        remaining -= one
    return {s: n for s, n in allocated.items() if n}


def deterministic_topics(pool: list[dict], subjects: list[str]) -> dict[str, list[str]]:
    topics: dict[str, set[str]] = defaultdict(set)
    for item in pool:
        topics[item["subject"]].add(item["topic"])
    return {s: sorted(topics.get(s, set()), key=str.casefold) for s in subjects if topics.get(s)}


def validate_topic_plan(obj: dict, available: dict[str, list[str]]) -> dict[str, list[str]]:
    if not isinstance(obj, dict) or set(obj) != {"topicos"} or not isinstance(obj["topicos"], dict):
        raise ValueError("formato esperado: {\"topicos\": {matéria: [tópicos]}}")
    normalized = {norm(subject): (subject, topics) for subject, topics in available.items()}
    plan: dict[str, list[str]] = {}
    errors = []
    if set(norm(str(s)) for s in obj["topicos"]) != set(normalized):
        errors.append("inclua exatamente todas as matérias disponíveis, sem matérias extras")
    for raw_subject, raw_topics in obj["topicos"].items():
        entry = normalized.get(norm(str(raw_subject)))
        if not entry:
            continue
        subject, topics = entry
        if not isinstance(raw_topics, list) or not raw_topics:
            errors.append(f"{subject}: forneça uma lista não vazia de tópicos")
            continue
        known = {norm(topic): topic for topic in topics}
        selected: list[str] = []
        for raw_topic in raw_topics:
            topic = known.get(norm(str(raw_topic)))
            if not topic:
                errors.append(f"{subject}: tópico desconhecido {raw_topic!r}")
            elif topic not in selected:
                selected.append(topic)
        if selected:
            plan[subject] = selected
    if errors:
        raise ValueError("; ".join(errors))
    return plan


def plan_topics(provider, profile: dict, available: dict[str, list[str]]) -> tuple[dict, dict]:
    system = ("Você planeja cobertura temática para um diagnóstico de vestibular. "
              "Você não calcula quantidades, não escolhe questões e não gera IDs. "
              "Retorne somente JSON no formato {\"topicos\": {\"Matéria\": [\"tópico existente\"]}}.")
    user = json.dumps({"curso": profile["nome"], "disciplinas_especificas": profile.get("disciplinas_especificas", []),
                       "topicos_disponiveis": available,
                       "tarefa": "Escolha uma ordem de tópicos ampla para cada matéria, usando apenas tópicos disponíveis. "
                                 "Não informe quantidade de questões nem identificadores."}, ensure_ascii=False)
    error = None
    for attempt in range(2):
        prompt = user if attempt == 0 else user + "\n\nSua resposta anterior falhou: " + str(error) + ". Corrija o JSON."
        try:
            result = validate_topic_plan(clf.extract_json(provider.complete(system, prompt)), available)
            return result, {"status": "llm", "attempts": attempt + 1, "error": None}
        except Exception as exc:
            error = str(exc)
    return deterministic_topics_from_available(available), {
        "status": "deterministic_fallback", "attempts": 2, "error": error}


def deterministic_topics_from_available(available: dict[str, list[str]]) -> dict[str, list[str]]:
    return {s: list(topics) for s, topics in available.items() if topics}


def select_questions(pool: list[dict], weights: dict[str, float], total: int,
                     topics: dict[str, list[str]], seed: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for item in pool:
        if item["subject"] in weights:
            by_subject[item["subject"]].append(item)
    available = {s: len(by_subject.get(s, [])) for s in weights}
    targets = allocate_counts(available, weights, total)
    selected: list[dict] = []
    for subject in weights:
        count = targets.get(subject, 0)
        if not count:
            continue
        by_topic: dict[str, list[dict]] = defaultdict(list)
        for item in by_subject[subject]:
            by_topic[item["topic"]].append(item)
        for values in by_topic.values():
            rng.shuffle(values)
        preferred = [t for t in topics.get(subject, []) if t in by_topic]
        topic_order = preferred + [t for t in sorted(by_topic, key=str.casefold) if t not in preferred]
        taken = 0
        while taken < count:
            progress = False
            for topic in topic_order:
                if by_topic[topic] and taken < count:
                    selected.append(by_topic[topic].pop())
                    taken += 1
                    progress = True
            if not progress:
                break
    rng.shuffle(selected)
    return selected, {"requested": total, "selected": len(selected), "available_by_subject": available,
                      "selected_by_subject": dict(Counter(q["subject"] for q in selected)),
                      "excluded": {}}


def create_attempt(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict],
                   *, total: int, seed: int, provider, planner_topics: dict | None = None,
                   planner_meta: dict | None = None, excluded: dict | None = None) -> dict:
    profile = course_profile(config, course_id)
    weights = profile["pesos"]
    subjects = list(weights)
    available_topics = deterministic_topics(pool, subjects)
    if planner_topics is None:
        planner_topics, planner_meta = plan_topics(provider, profile, available_topics)
    selected, distribution = select_questions(pool, weights, total, planner_topics, seed)
    distribution["excluded"] = excluded or {}
    distribution["unavailable_subjects"] = [s for s in subjects if not distribution["available_by_subject"].get(s)]
    if not selected:
        raise ValueError("nenhuma questão elegível com classificação e gabarito; importe gabaritos e confira o banco")
    attempt_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    plan = {"topics": planner_topics, "planner": planner_meta or {"status": "provided"},
            "distribution": distribution}
    with db:
        db.execute("""INSERT INTO attempts(id,course_id,course_name,config_version,profile_snapshot,seed,
            requested_count,actual_count,status,plan_json,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                   (attempt_id, course_id, profile["nome"], config["version"],
                    json.dumps(profile, ensure_ascii=False), seed, total, len(selected), "in_progress",
                    json.dumps(plan, ensure_ascii=False), now))
        for ordinal, item in enumerate(selected, 1):
            db.execute("""INSERT INTO attempt_questions(attempt_id,ordinal,source_id,question_number,
                subject,topic,question_json,correct_answer) VALUES(?,?,?,?,?,?,?,?)""",
                       (attempt_id, ordinal, item["source_id"], item["number"], item["subject"], item["topic"],
                        json.dumps(item["question"], ensure_ascii=False), item["correct_answer"]))
    return {"id": attempt_id, "course": profile["nome"], "seed": seed, "requested": total,
            "selected": len(selected), "distribution": distribution, "planner": planner_meta}


def get_attempt(db: sqlite3.Connection, attempt_id: str) -> dict:
    row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if not row:
        raise ValueError(f"diagnóstico não encontrado: {attempt_id}")
    attempt = dict(row)
    for key in ("profile_snapshot", "plan_json", "metrics_json", "analysis_json"):
        attempt[key.removesuffix("_json")] = json.loads(attempt[key]) if attempt.get(key) else None
    return attempt


def list_questions_for_attempt(db: sqlite3.Connection, attempt_id: str, *, unanswered_only: bool = False):
    where = " AND answered_at IS NULL" if unanswered_only else ""
    return [dict(row) for row in db.execute(
        "SELECT * FROM attempt_questions WHERE attempt_id=?" + where + " ORDER BY ordinal", (attempt_id,))]


def record_response(db: sqlite3.Connection, attempt_id: str, ordinal: int,
                    answer: str | None, reason: str | None = None) -> dict:
    row = db.execute("SELECT * FROM attempt_questions WHERE attempt_id=? AND ordinal=?",
                     (attempt_id, ordinal)).fetchone()
    if not row:
        raise ValueError(f"questão {ordinal} não pertence ao diagnóstico")
    if row["answered_at"] is not None:
        raise ValueError(f"questão {ordinal} já respondida")
    alternatives = json.loads(row["question_json"]).get("alternativas", {})
    answer = (answer or "").strip().upper() or None
    if answer is not None and answer not in alternatives:
        raise ValueError("resposta deve ser uma das alternativas disponíveis")
    correct = answer is not None and answer == row["correct_answer"]
    if answer is None:
        reason = "blank"
    elif correct:
        if reason is not None:
            raise ValueError("resposta correta não recebe motivo de erro")
    elif reason not in REASONS - {"blank"}:
        raise ValueError("resposta incorreta exige motivo: unknown_content, calc_error ou misread")
    with db:
        db.execute("UPDATE attempt_questions SET selected_answer=?,error_reason=?,is_correct=?,answered_at=? "
                   "WHERE attempt_id=? AND ordinal=?",
                   (answer, reason, int(correct), datetime.now(timezone.utc).isoformat(), attempt_id, ordinal))
    return {"ordinal": ordinal, "answer": answer, "correct": bool(correct), "reason": reason}


def calculate_metrics(db: sqlite3.Connection, attempt_id: str) -> dict:
    attempt = get_attempt(db, attempt_id)
    weights = attempt["profile_snapshot"]["pesos"]
    rows = list_questions_for_attempt(db, attempt_id)
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["answered_at"] is not None:
            by_subject[row["subject"]].append(row)
    metrics = {}
    for subject in weights:
        answered = by_subject.get(subject, [])
        count = len(answered)
        correct = sum(int(r["is_correct"] or 0) for r in answered)
        reasons = Counter(r["error_reason"] for r in answered if r["error_reason"])
        metrics[subject] = {"attempts": count, "correct": correct,
                            "accuracy": round(correct / count, 4) if count else None,
                            "error_reasons": {r: reasons.get(r, 0) for r in sorted(REASONS)},
                            "status": "assessed" if count >= 3 else "insufficient_data"}
    answered_count = sum(x["attempts"] for x in metrics.values())
    correct_count = sum(x["correct"] for x in metrics.values())
    return {"total_questions": len(rows), "answered": answered_count,
            "correct": correct_count, "incorrect_or_blank": answered_count - correct_count,
            "accuracy": round(correct_count / answered_count, 4) if answered_count else None,
            "subjects": metrics}


def validate_analysis(obj: dict, metrics: dict, subjects: list[str]) -> dict:
    if not isinstance(obj, dict) or set(obj) != {"summary", "steps"}:
        raise ValueError("retorne somente os campos summary e steps")
    if not isinstance(obj["summary"], str) or not obj["summary"].strip():
        raise ValueError("summary deve ser texto não vazio")
    if not isinstance(obj["steps"], list):
        raise ValueError("steps deve ser uma lista")
    steps = []
    allowed = {norm(s): s for s in subjects}
    required = {"subject", "action", "reason_code", "rationale", "exit_criterion"}
    for idx, step in enumerate(obj["steps"], 1):
        if not isinstance(step, dict) or set(step) != required:
            raise ValueError(f"etapa {idx}: campos esperados {sorted(required)}")
        subject = allowed.get(norm(step["subject"]))
        if not subject:
            raise ValueError(f"etapa {idx}: matéria desconhecida")
        if metrics["subjects"][subject]["status"] != "assessed":
            raise ValueError(f"etapa {idx}: {subject} tem menos de 3 tentativas e não pode receber conclusão")
        if step["action"] not in ACTIONS:
            raise ValueError(f"etapa {idx}: ação inválida")
        if step["reason_code"] not in REASONS:
            raise ValueError(f"etapa {idx}: reason_code inválido")
        if step["action"] != ACTION_BY_REASON[step["reason_code"]]:
            raise ValueError(f"etapa {idx}: motivo {step['reason_code']} exige ação {ACTION_BY_REASON[step['reason_code']]}")
        if metrics["subjects"][subject]["error_reasons"].get(step["reason_code"], 0) < 1:
            raise ValueError(f"etapa {idx}: motivo {step['reason_code']} não aparece nos dados de {subject}")
        if not all(isinstance(step[k], str) and step[k].strip() for k in ("rationale", "exit_criterion")):
            raise ValueError(f"etapa {idx}: justificativa e critério são obrigatórios")
        if not re.search(r"\d", step["exit_criterion"]):
            raise ValueError(f"etapa {idx}: critério de saída precisa ser mensurável")
        low_data_subjects = [s for s, m in metrics["subjects"].items() if m["status"] == "insufficient_data"]
        text = norm(step["rationale"] + " " + step["exit_criterion"])
        for low_subject in low_data_subjects:
            tokens = {norm(low_subject)}
            tokens.update(norm(alias) for alias, target in ALIASES.items()
                          if norm(target) == norm(low_subject))
            if any(token and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text) for token in tokens):
                raise ValueError(f"etapa {idx}: não faça afirmações sobre {low_subject}, com menos de 3 tentativas")
        steps.append({**step, "subject": subject})
    seen = [step["subject"] for step in steps]
    if len(set(seen)) != len(seen):
        raise ValueError("não repita uma matéria em várias etapas")
    if not steps and any(m["status"] == "assessed" and any(m["error_reasons"].values())
                         for m in metrics["subjects"].values()):
        raise ValueError("há erros em matérias avaliadas; informe ao menos uma prioridade de estudo")
    enough = [s for s, m in metrics["subjects"].items() if m["status"] == "assessed"]
    insufficient = [s for s, m in metrics["subjects"].items() if m["status"] == "insufficient_data"]
    summary = ("Matérias com dados suficientes: " + (", ".join(enough) if enough else "nenhuma") + ". "
               + "Sem conclusão por falta de 3 tentativas: "
               + (", ".join(insufficient) if insufficient else "nenhuma") + ".")
    return {"summary": summary, "steps": steps}


def analyze(provider, metrics: dict, profile: dict) -> tuple[dict | None, str | None]:
    subjects = list(profile["pesos"])
    system = ("Você interpreta resultados de um diagnóstico FUVEST e propõe um caminho inicial de estudo. "
              "Os números recebidos já foram calculados pelo programa: não os recalcule, altere ou infira dados. "
              "Não conclua nada sobre matérias com status insufficient_data. "
              "Use ações apenas theory, calculation_practice, interpretation_practice ou review. "
              "Associe unknown_content a theory, calc_error a calculation_practice, "
              "misread a interpretation_practice e blank a review. "
              "Devolva JSON sem markdown: {\"summary\": texto, \"steps\": [{\"subject\": matéria, "
              "\"action\": ação, \"reason_code\": unknown_content|calc_error|misread|blank, "
              "\"rationale\": texto, \"exit_criterion\": critério mensurável com número}]}.")
    user = json.dumps({"course": profile["nome"], "configured_course_weights": profile["pesos"],
                       "course_specific_subjects": profile.get("disciplinas_especificas", []),
                       "metrics_calculated_by_code": metrics,
                       "task": "Ordene as prioridades por sequência no array steps. Recomende estudo apenas para matérias "
                              "com pelo menos três tentativas; conecte ação e reason_code aos dados. "
                              "Não acrescente estatísticas.",
                       "criterion_examples": ["Acertar pelo menos 8 de 10 exercícios inéditos do tópico.",
                                             "Explicar 4 de 5 conceitos-chave e acertar 8 de 10 questões."],
                       "valid_actions": sorted(ACTIONS), "valid_reasons": sorted(REASONS),
                       "valid_subjects": subjects}, ensure_ascii=False)
    error = None
    for attempt in range(2):
        prompt = user if attempt == 0 else user + "\n\nA resposta anterior é inválida: " + str(error) + ". Corrija-a."
        try:
            result = validate_analysis(clf.extract_json(provider.complete(system, prompt)), metrics, subjects)
            return result, None
        except Exception as exc:
            error = str(exc)
    return None, error


def finalize_attempt(db: sqlite3.Connection, attempt_id: str, provider) -> dict:
    attempt = get_attempt(db, attempt_id)
    pending = list_questions_for_attempt(db, attempt_id, unanswered_only=True)
    if pending:
        raise ValueError(f"ainda faltam {len(pending)} questões; responda todas antes de concluir")
    metrics = calculate_metrics(db, attempt_id)
    analysis, error = analyze(provider, metrics, attempt["profile_snapshot"])
    status = "completed" if analysis is not None else "analysis_failed"
    with db:
        db.execute("UPDATE attempts SET status=?,metrics_json=?,analysis_json=?,analysis_status=?,analysis_error=? WHERE id=?",
                   (status, json.dumps(metrics, ensure_ascii=False),
                    json.dumps(analysis, ensure_ascii=False) if analysis else None,
                    status, error, attempt_id))
    return {"metrics": metrics, "analysis": analysis, "analysis_status": status, "analysis_error": error}
