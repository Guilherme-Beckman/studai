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

from studai.features.classification import service as clf
from studai.integrations.sqlite import connect as connect_sqlite
from studai.features.answer_keys.service import (
    associate_answer_key, exam_key, exam_variant, import_answer_key,
    parse_answer_key_pdf, source_id_for,
)

SCHEMA_VERSION = 2
REASONS = {"unknown_content", "calc_error", "misread", "blank"}
ACTIONS = {"theory", "calculation_practice", "interpretation_practice", "review"}
ACTION_BY_REASON = {"unknown_content": "theory", "calc_error": "calculation_practice",
                    "misread": "interpretation_practice", "blank": "review"}
ALIASES = {
    "arte": "artes", "artes": "arte", "portugues": "lingua portuguesa",
    "lingua portuguesa": "lingua portuguesa", "educacao fisica": "educacao fisica",
}


def norm(value: str) -> str:
    return clf._norm(str(value))


def canonical_subject(value: str, subjects: list[str]) -> str | None:
    n = norm(value)
    aliases = {norm(k): norm(v) for k, v in ALIASES.items()}
    wanted = aliases.get(n, n)
    # Accept both the previous display label "Artes" and the current taxonomy label "Arte".
    return next((subject for subject in subjects if norm(subject) == wanted), None) or \
        next((subject for subject in subjects if norm(subject) == n), None)


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
    db = connect_sqlite(path)
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
        # A composição do diagnóstico é determinística; o LLM não escolhe a
        # cobertura de tópicos nem pode tornar a criação dependente de rede.
        planner_topics = deterministic_topics_from_available(available_topics)
        planner_meta = {"status": "deterministic", "attempts": 0, "error": None}
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


def finalize_attempt(db: sqlite3.Connection, attempt_id: str, provider, *, include_analysis: bool = True) -> dict:
    attempt = get_attempt(db, attempt_id)
    pending = list_questions_for_attempt(db, attempt_id, unanswered_only=True)
    if pending:
        raise ValueError(f"ainda faltam {len(pending)} questões; responda todas antes de concluir")
    metrics = calculate_metrics(db, attempt_id)
    analysis, error = analyze(provider, metrics, attempt["profile_snapshot"]) if include_analysis else (None, None)
    status = "completed" if analysis is not None or not include_analysis else "analysis_failed"
    with db:
        db.execute("UPDATE attempts SET status=?,metrics_json=?,analysis_json=?,analysis_status=?,analysis_error=? WHERE id=?",
                   (status, json.dumps(metrics, ensure_ascii=False),
                    json.dumps(analysis, ensure_ascii=False) if analysis else None,
                    status, error, attempt_id))
    return {"metrics": metrics, "analysis": analysis, "analysis_status": status,
            "analysis_error": error, "analysis_included": include_analysis}
