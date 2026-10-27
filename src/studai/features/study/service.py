"""Revisão espaçada e montagem do "O que estudar hoje"."""
from __future__ import annotations

import json
import random
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from studai.features.classification.taxonomy import CONTENT_INDEX, aggregate_content_ids, live_id
from studai.features.diagnostics import depth, mastery
from studai.features.diagnostics import service as diagnostics
from studai.features.diagnostics.courses import resolve_weights
from studai.features.study import scheduler

STUDY_SCHEMA_VERSION = 3
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
GAP_ACCURACY = 0.7
HALF_LIFE_DAYS = 30
REASONS = ("overdue_question", "due_content", "new_gap", "new_content")


def connect_db(path: Path) -> sqlite3.Connection:
    """Abre o banco do diagnóstico e garante as tabelas do estudo diário."""
    db = diagnostics.connect_db(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS review_cards (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('content','question')),
            content_id TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '',
            question_number INTEGER NOT NULL DEFAULT 0, subject TEXT NOT NULL,
            card_json TEXT NOT NULL, due_at TEXT NOT NULL, last_review_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(kind, content_id, source_id, question_number)
        );
        CREATE INDEX IF NOT EXISTS review_cards_due ON review_cards(due_at);
        CREATE TABLE IF NOT EXISTS review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL REFERENCES review_cards(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL, reviewed_at TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin IN ('diagnostic','daily')),
            source_ref TEXT NOT NULL UNIQUE, log_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS study_sessions (
            id TEXT PRIMARY KEY, local_date TEXT NOT NULL, course_id TEXT NOT NULL,
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(local_date, course_id)
        );
        CREATE TABLE IF NOT EXISTS study_answers (
            session_id TEXT NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL, source_id TEXT NOT NULL, question_number INTEGER NOT NULL,
            subject TEXT NOT NULL, content_id TEXT NOT NULL, selected_answer TEXT,
            correct_answer TEXT NOT NULL, is_correct INTEGER NOT NULL, error_reason TEXT,
            answered_at TEXT NOT NULL,
            PRIMARY KEY(session_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS exam_dates (
            id TEXT PRIMARY KEY, course_id TEXT NOT NULL, name TEXT NOT NULL,
            exam_date TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('fase1','fase2','outra')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calendar_days (
            local_date TEXT NOT NULL, course_id TEXT NOT NULL, exam_id TEXT NOT NULL,
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(local_date, course_id)
        );
    """)
    diagnostics.add_missing_columns(db, "study_answers", diagnostics.ANSWER_EXTRA_COLUMNS)
    old = db.execute("SELECT value FROM metadata WHERE key='study_schema_version'").fetchone()
    if old and int(old[0]) > STUDY_SCHEMA_VERSION:
        db.close()
        raise ValueError("tabelas de estudo foram criadas por uma versão mais nova")
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('study_schema_version',?)",
               (str(STUDY_SCHEMA_VERSION),))
    db.commit()
    return db


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_date(now: datetime) -> date:
    return now.astimezone(LOCAL_TZ).date()


def end_of_local_day(now: datetime) -> datetime:
    """Último instante do dia local; o que vence ainda hoje entra na sessão."""
    day = local_date(now)
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=LOCAL_TZ) - timedelta(microseconds=1)


def content_id_of(question: dict) -> str | None:
    """Nó da taxonomia onde fica o cartão da questão (conteúdo principal, sem os ancestrais)."""
    raw = (question.get("classificacao") or {}).get("conteudo_principal_id")
    content_id = live_id(str(raw)) if raw else None
    return content_id if content_id in CONTENT_INDEX else None


def _card(db: sqlite3.Connection, kind: str, content_id: str, subject: str, now: datetime,
          source_id: str = "", number: int = 0) -> sqlite3.Row:
    row = db.execute("SELECT * FROM review_cards WHERE kind=? AND content_id=? AND source_id=? "
                     "AND question_number=?", (kind, content_id, source_id, number)).fetchone()
    if row:
        return row
    card = scheduler.new_card(now)
    db.execute("""INSERT INTO review_cards(id,kind,content_id,source_id,question_number,subject,
        card_json,due_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
               (uuid.uuid4().hex[:16], kind, content_id, source_id, number, subject,
                json.dumps(card), _iso(now), _iso(now)))
    return db.execute("SELECT * FROM review_cards WHERE kind=? AND content_id=? AND source_id=? "
                      "AND question_number=?", (kind, content_id, source_id, number)).fetchone()


def _review(db: sqlite3.Connection, row: sqlite3.Row, correct: bool, at: datetime,
            origin: str, source_ref: str, **grade) -> bool:
    """Aplica uma revisão uma única vez por `source_ref`; devolve False se já existia."""
    if db.execute("SELECT 1 FROM review_logs WHERE source_ref=?", (source_ref,)).fetchone():
        return False
    if row["last_review_at"] and at < _parse(row["last_review_at"]):
        at = _parse(row["last_review_at"])
    card, log = scheduler.review(json.loads(row["card_json"]), correct, at, **grade)
    db.execute("UPDATE review_cards SET card_json=?,due_at=?,last_review_at=? WHERE id=?",
               (json.dumps(card), _iso(scheduler.due_at(card)), _iso(at), row["id"]))
    db.execute("INSERT INTO review_logs(card_id,rating,reviewed_at,origin,source_ref,log_json) "
               "VALUES(?,?,?,?,?,?)", (row["id"], int(log["rating"]), _iso(at), origin, source_ref,
                                       json.dumps(log)))
    return True


def _apply_answer(db: sqlite3.Connection, *, content_id: str, subject: str, source_id: str,
                  number: int, correct: bool, at: datetime, origin: str, ref: str,
                  confidence: str | None = None, elapsed_ms: int | None = None,
                  hints_used: int = 0) -> sqlite3.Row:
    """Revisa o cartão do conteúdo; erro ou chute cria (ou revisa) também o cartão da questão."""
    grade = {"confidence": confidence, "elapsed_ms": elapsed_ms, "hints_used": hints_used or 0}
    content_card = _card(db, "content", content_id, subject, at)
    _review(db, content_card, correct, at, origin, ref, **grade)
    question_card = db.execute("SELECT * FROM review_cards WHERE kind='question' AND content_id=? "
                               "AND source_id=? AND question_number=?",
                               (content_id, source_id, number)).fetchone()
    if question_card is None and (not correct or confidence == "guess"):
        question_card = _card(db, "question", content_id, subject, at, source_id, number)
    if question_card is not None:
        _review(db, question_card, correct, at, origin, ref + ":q", **grade)
    return db.execute("SELECT * FROM review_cards WHERE id=?", (content_card["id"],)).fetchone()


def sync_diagnostic_answers(db: sqlite3.Connection) -> int:
    """Importa as respostas dos diagnósticos para o agendamento; seguro para repetir."""
    rows = db.execute("""SELECT attempt_id,ordinal,source_id,question_number,subject,question_json,
        is_correct,answered_at,confidence,elapsed_ms,hints_used FROM attempt_questions WHERE answered_at IS NOT NULL
        ORDER BY answered_at, attempt_id, ordinal""").fetchall()
    imported = 0
    with db:
        for row in rows:
            ref = f"attempt:{row['attempt_id']}:{row['ordinal']}"
            if db.execute("SELECT 1 FROM review_logs WHERE source_ref=?", (ref,)).fetchone():
                continue
            content_id = content_id_of(json.loads(row["question_json"]))
            if not content_id:
                continue
            _apply_answer(db, content_id=content_id, subject=row["subject"], source_id=row["source_id"],
                          number=int(row["question_number"]), correct=bool(row["is_correct"]),
                          at=_parse(row["answered_at"]), origin="diagnostic", ref=ref,
                          confidence=row["confidence"], elapsed_ms=row["elapsed_ms"],
                          hints_used=row["hints_used"] or 0)
            imported += 1
    return imported


def _recently_answered(db: sqlite3.Connection, since: datetime) -> set[tuple[str, int]]:
    cutoff = _iso(since)
    rows = db.execute("SELECT source_id,question_number FROM study_answers WHERE answered_at>=? "
                      "UNION SELECT source_id,question_number FROM attempt_questions WHERE answered_at>=?",
                      (cutoff, cutoff)).fetchall()
    return {(row[0], int(row[1])) for row in rows}


def _gap_nodes(db: sqlite3.Connection, course_id: str) -> set[str]:
    """Nós com desempenho baixo ou poucos dados no último diagnóstico concluído do curso."""
    row = db.execute("SELECT metrics_json FROM attempts WHERE course_id=? AND metrics_json IS NOT NULL "
                     "ORDER BY created_at DESC LIMIT 1", (course_id,)).fetchone()
    if not row:
        return set()
    metrics = json.loads(row["metrics_json"]) or {}
    contents = metrics.get("contents") or {}
    gaps = {content_id for content_id, metric in contents.items()
            if metric.get("status") == "insufficient_data"
            or (metric.get("accuracy") is not None and metric["accuracy"] < GAP_ACCURACY)}
    # Fase 5: nós com lacuna ou ainda incertos pelo modelo de domínio também contam.
    gaps.update(node_id for node_id, node in (metrics.get("nodes") or {}).items()
                if node.get("status") in ("gap", "uncertain"))
    return gaps


def course_responses(db: sqlite3.Connection, course_id: str) -> list[dict]:
    """Respostas do curso no formato de ``mastery``: diagnósticos, provas e estudo diário.

    Cada resposta leva também ``kind`` (``diagnostic``/``exam``/``daily``) e os dados da
    questão (``source_id``, ``number``, ``selected``, ``correct_answer``, ``reason``).
    """
    responses = []
    for row in db.execute("""SELECT aq.*, a.mode AS attempt_mode FROM attempt_questions aq
            JOIN attempts a ON a.id = aq.attempt_id
            WHERE a.course_id = ? AND aq.answered_at IS NOT NULL""", (course_id,)):
        row = dict(row)
        responses.append({**mastery.response_from_row(row),
                          "kind": "exam" if row["attempt_mode"] == "exam" else "diagnostic",
                          "source_id": row["source_id"], "number": int(row["question_number"]),
                          "selected": row["selected_answer"], "correct_answer": row["correct_answer"],
                          "reason": row["error_reason"], "note": row["note"]})
    for row in db.execute("""SELECT sa.* FROM study_answers sa JOIN study_sessions s ON s.id = sa.session_id
            WHERE s.course_id = ?""", (course_id,)):
        # Como em ``mastery.response_from_row``: acerto no chute não conta como domínio.
        responses.append({"correct": bool(row["is_correct"]) and row["confidence"] != "guess",
                          "is_correct": bool(row["is_correct"]), "subject": row["subject"],
                          "principal_id": live_id(row["content_id"]), "related_ids": [],
                          "answered_at": row["answered_at"], "kind": "daily",
                          "source_id": row["source_id"], "number": int(row["question_number"]),
                          "selected": row["selected_answer"], "correct_answer": row["correct_answer"],
                          "reason": row["error_reason"], "confidence": row["confidence"],
                          "elapsed_ms": row["elapsed_ms"], "note": row["note"]})
    responses.sort(key=lambda response: response["answered_at"] or "")
    return responses


def _new_leaves(db: sqlite3.Connection, pool: list[dict], fresh: list[str], weights: dict[str, float],
                course_id: str, now: datetime, budget: int, rng: random.Random) -> list[tuple[str, str]]:
    """Folhas novas do dia, escolhidas de cima para baixo.

    A unidade é o conteúdo (3º nível): lacunas primeiro, depois conteúdos já começados
    e ainda não consolidados, depois conteúdos nunca vistos e, por último, os
    consolidados. Em cada rodada entra uma folha por conteúdo — a irmã ainda não
    praticada de maior incidência —, então o conteúdo é coberto por inteiro antes de
    se abrir outro, em vez de sortear subconteúdos soltos.
    """
    incidence = mastery.node_incidence(pool)
    nodes = depth.compute_depth(course_responses(db, course_id), subject_weights=weights, incidence=incidence,
                                half_life_days=HALF_LIFE_DAYS, now=now)
    gaps = _gap_nodes(db, course_id) | {identity for identity, node in nodes.items()
                                        if node["status"] == "gap" and node["level"] not in ("subject", "area")}
    groups: dict[str, list[str]] = defaultdict(list)
    for content_id in fresh:
        groups[depth.content_ancestor(content_id)].append(content_id)

    def tier(content: str) -> int:
        # Matéria e área são amplas demais para marcar lacuna: só conta do conteúdo para baixo.
        if any(gaps & set(aggregate_content_ids([leaf])[2:]) for leaf in groups[content]):
            return 0
        node = nodes.get(content)
        if not node or node["coverage"] == 0:
            return 2
        return 1 if node["depth"] < depth.CONSOLIDATED_DEPTH else 3

    def score(content: str) -> float:
        node = nodes.get(content) or {"depth": 0.0}
        subject = CONTENT_INDEX[content]["subject"]
        return weights.get(subject, 0.0) * (incidence.get(content, 0.0) + 1e-3) * (1 - node["depth"])

    contents = sorted(groups)
    rng.shuffle(contents)  # desempate estável e determinístico no dia
    contents.sort(key=lambda content: (tier(content), -score(content)))
    for content in contents:
        rng.shuffle(groups[content])
        groups[content].sort(key=lambda leaf: (nodes.get(leaf, {}).get("evidence", 0) > 0,
                                               -incidence.get(leaf, 0.0)))
    sequence = []  # rodadas: a n-ésima folha de cada conteúdo antes da (n+1)-ésima de qualquer um
    for depth_index in range(max(len(leaves) for leaves in groups.values())):
        sequence += [(tier(content), groups[content][depth_index]) for content in contents
                     if depth_index < len(groups[content])]
    subject_of = {leaf: CONTENT_INDEX[leaf]["subject"] for _, leaf in sequence}
    chosen = [("new_gap", leaf) for level, leaf in sequence if level == 0][:budget]
    remaining = budget - len(chosen)
    others = [leaf for level, leaf in sequence if level > 0 and subject_of[leaf] in weights]
    if remaining > 0 and others:
        quotas = diagnostics.allocate_counts(Counter(subject_of[leaf] for leaf in others), weights, remaining)
        for leaf in others:
            if quotas.get(subject_of[leaf], 0) > 0:
                quotas[subject_of[leaf]] -= 1
                chosen.append(("new_content", leaf))
    return chosen


def _item(reason: str, entry: dict, content_id: str) -> dict:
    node = CONTENT_INDEX[content_id]
    return {"reason": reason, "sourceId": entry["source_id"], "number": entry["number"],
            "subject": entry["subject"], "contentId": content_id, "contentPath": " → ".join(node["path"]),
            "question": entry["question"], "correctAnswer": entry["correct_answer"]}


def target_phase(db: sqlite3.Connection, course_id: str, now: datetime) -> str | None:
    """Fase da próxima prova cadastrada do curso (hoje conta), ou ``None`` sem prova."""
    row = db.execute("SELECT phase FROM exam_dates WHERE course_id=? AND exam_date>=? "
                     "ORDER BY exam_date, created_at LIMIT 1", (course_id, local_date(now).isoformat())).fetchone()
    return row["phase"] if row else None


def build_plan(db: sqlite3.Connection, pool: list[dict], profile: dict, course_id: str, now: datetime,
               *, size: int = 15, new_per_day: int = 5, recent_days: int = 7) -> list[dict]:
    """Monta as faixas do dia: questões erradas vencidas, conteúdos vencidos e conteúdos novos."""
    # Pesos efetivos do curso para a fase da próxima prova (fórmula FUVEST quando
    # configurada; senão os pesos manuais).
    try:
        effective = resolve_weights(profile, pool, target_phase(db, course_id, now))["weights"]
    except ValueError:  # pool vazio: sem incidência para aplicar a fórmula
        effective = profile["pesos"]
    weights = {subject: float(weight) for subject, weight in effective.items() if weight > 0}
    rng = random.Random(f"{local_date(now).isoformat()}:{course_id}")
    by_content: dict[str, list[dict]] = defaultdict(list)
    by_identity: dict[tuple[str, int], dict] = {}
    for entry in pool:
        content_id = content_id_of(entry["question"])
        if content_id and entry["subject"] in weights:
            by_content[content_id].append(entry)
            by_identity[(entry["source_id"], entry["number"])] = entry
    recent = _recently_answered(db, now - timedelta(days=recent_days))
    cutoff = _iso(end_of_local_day(now))
    used: set[tuple[str, int]] = set()
    items: list[dict] = []

    def pick(content_id: str) -> dict | None:
        candidates = [e for e in by_content.get(content_id, []) if (e["source_id"], e["number"]) not in used]
        fresh = [e for e in candidates if (e["source_id"], e["number"]) not in recent]
        options = fresh or candidates
        return rng.choice(options) if options else None

    # 1. Questões erradas cujo cartão venceu: refazer a mesma questão.
    for row in db.execute("SELECT * FROM review_cards WHERE kind='question' AND due_at<=? "
                          "ORDER BY due_at, id", (cutoff,)):
        entry = by_identity.get((row["source_id"], int(row["question_number"])))
        if len(items) < size and entry and (entry["source_id"], entry["number"]) not in used:
            used.add((entry["source_id"], entry["number"]))
            items.append(_item("overdue_question", entry, row["content_id"]))

    # 2. Conteúdos vencidos: menor retrievability e maior peso da matéria primeiro.
    due_contents = [row for row in db.execute(
        "SELECT * FROM review_cards WHERE kind='content' AND due_at<=?", (cutoff,)) if row["subject"] in weights]
    due_contents.sort(key=lambda row: (scheduler.retrievability(json.loads(row["card_json"]), now)
                                       / weights[row["subject"]], row["content_id"]))
    for row in due_contents:
        if len(items) >= size:
            break
        entry = pick(row["content_id"])
        if entry:
            used.add((entry["source_id"], entry["number"]))
            items.append(_item("due_content", entry, row["content_id"]))

    # 3. Conteúdos novos, de cima para baixo (veja ``_new_leaves``).
    known = {row[0] for row in db.execute("SELECT content_id FROM review_cards WHERE kind='content'")}
    fresh = sorted(content_id for content_id in by_content if content_id not in known)
    budget = min(new_per_day, size - len(items))
    chosen = _new_leaves(db, pool, fresh, weights, course_id, now, budget, rng) if fresh and budget > 0 else []
    for reason, content_id in chosen:
        entry = pick(content_id)
        if entry:
            used.add((entry["source_id"], entry["number"]))
            items.append(_item(reason, entry, content_id))
    for index, item in enumerate(items, 1):
        item["id"] = f"i{index}"
    return items


def plan_today(db: sqlite3.Connection, pool: list[dict], config: dict, course_id: str, now: datetime,
               *, size: int = 15, new_per_day: int = 5) -> dict:
    """Devolve a sessão do dia; a primeira chamada do dia a monta e grava."""
    profile = diagnostics.course_profile(config, course_id)
    day = local_date(now).isoformat()
    row = db.execute("SELECT * FROM study_sessions WHERE local_date=? AND course_id=?", (day, course_id)).fetchone()
    if row is None:
        items = build_plan(db, pool, profile, course_id, now, size=size, new_per_day=new_per_day)
        session_id = uuid.uuid4().hex[:12]
        with db:
            db.execute("INSERT INTO study_sessions(id,local_date,course_id,plan_json,created_at) VALUES(?,?,?,?,?)",
                       (session_id, day, course_id, json.dumps({"items": items}, ensure_ascii=False), _iso(now)))
        row = db.execute("SELECT * FROM study_sessions WHERE id=?", (session_id,)).fetchone()
    return session_payload(db, row["id"], pool)


def append_training_item(db: sqlite3.Connection, session_id: str, pool: list[dict], config: dict,
                        now: datetime, *, subjects: list[str] | None = None, count: int = 1,
                        content_ids: list[str] | None = None) -> dict:
    """Acrescenta um bloco de questões recomendadas à sessão diária.

    ``content_ids`` (do calendário) restringe o bloco às questões desses nós da
    taxonomia; sem questões neles, o bloco usa só o filtro por matéria.
    """
    if not 1 <= count <= 100:
        raise ValueError("a quantidade deve ficar entre 1 e 100 questões")
    row, existing = _session(db, session_id, pool)
    profile = diagnostics.course_profile(config, row["course_id"])
    selected = set(profile.get("pesos", {})) if subjects is None else set(subjects)
    allowed = set(profile.get("pesos", {}))
    if not selected or not selected <= allowed:
        raise ValueError("selecione ao menos uma matéria válida para este curso")
    filtered_pool = [entry for entry in pool if entry["subject"] in selected]
    if content_ids:
        targets = {live_id(content_id) for content_id in content_ids}
        focused = [entry for entry in filtered_pool
                   if targets & set(CONTENT_INDEX.get(content_id_of(entry["question"]) or "", {}).get("path_ids", []))]
        filtered_pool = focused or filtered_pool
    # Primeiro usa a fila priorizada pelo mesmo algoritmo da tarefa diária, sem limite diário.
    ranked = build_plan(db, filtered_pool, profile, row["course_id"], now,
                        size=max(1, len(filtered_pool)), new_per_day=max(1, len(filtered_pool)))
    used = {(item["sourceId"], int(item["number"])) for item in existing}
    plan = json.loads(row["plan_json"])
    eligible = []
    for entry in filtered_pool:
        content_id = content_id_of(entry["question"])
        if content_id:
            eligible.append(((entry["source_id"], int(entry["number"])), entry, content_id))
    if not eligible:
        raise ValueError("não há questões classificadas e com gabarito para as matérias selecionadas")
    for offset in range(count):
        candidate = next((item for item in ranked
                          if (item["sourceId"], int(item["number"])) not in used), None)
        if candidate is None:
            # Quando a fila priorizada se esgota, mantém treino livre sem repetir até necessário.
            unused = [item for item in eligible if item[0] not in used]
            choices = unused or eligible
            _, entry, content_id = random.Random(
                f"{session_id}:{len(existing) + offset}:{local_date(now)}").choice(choices)
            candidate = _item("practice", entry, content_id)
        identity = (candidate["sourceId"], int(candidate["number"]))
        candidate["id"] = f"t{len(existing) + offset + 1}"
        used.add(identity)
        plan["items"].append(candidate)
    with db:
        db.execute("UPDATE study_sessions SET plan_json=? WHERE id=?",
                   (json.dumps(plan, ensure_ascii=False), session_id))
    return session_payload(db, session_id, pool)


def _session(db: sqlite3.Connection, session_id: str,
             pool: list[dict] | None = None) -> tuple[sqlite3.Row, list[dict]]:
    """
    O plano gravado guarda uma cópia da questão. Com ``pool``, cada item passa a usar a
    versão atual do acervo (questão corrigida, gabarito revisto); a cópia só vale se a
    questão saiu do acervo.
    """
    row = db.execute("SELECT * FROM study_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError(f"sessão de estudo não encontrada: {session_id}")
    items = json.loads(row["plan_json"])["items"]
    current = {(e["source_id"], int(e["number"])): e for e in pool or []}
    for item in items:
        entry = current.get((item["sourceId"], int(item["number"])))
        if entry:
            item["question"], item["correctAnswer"] = entry["question"], entry["correct_answer"]
    return row, items


def session_payload(db: sqlite3.Connection, session_id: str, pool: list[dict] | None = None) -> dict:
    """Sessão para a interface: o gabarito só aparece depois da resposta."""
    row, items = _session(db, session_id, pool)
    answers = {a["item_id"]: dict(a) for a in db.execute(
        "SELECT * FROM study_answers WHERE session_id=?", (session_id,))}
    payload_items = []
    for item in items:
        question = dict(item["question"])
        question.pop("_source_dir", None)
        answer = answers.get(item["id"])
        entry = {key: item[key] for key in ("id", "reason", "sourceId", "number", "subject",
                                             "contentId", "contentPath")}
        entry.update(question=question, answered=answer is not None)
        if answer:
            entry.update(selectedAnswer=answer["selected_answer"], correct=bool(answer["is_correct"]),
                         correctAnswer=answer["correct_answer"], errorReason=answer["error_reason"],
                         confidence=answer["confidence"], elapsedMs=answer["elapsed_ms"],
                         hintsUsed=answer["hints_used"], note=answer["note"])
        payload_items.append(entry)
    counts = Counter(item["reason"] for item in items)
    return {"id": row["id"], "date": row["local_date"], "courseId": row["course_id"],
            "counts": {reason: counts.get(reason, 0) for reason in REASONS},
            "answered": len(answers), "correct": sum(int(a["is_correct"]) for a in answers.values()),
            "items": payload_items}


def record_study_answer(db: sqlite3.Connection, session_id: str, item_id: str, answer: str | None,
                        reason: str | None, now: datetime, pool: list[dict] | None = None, *,
                        confidence: str | None = None, elapsed_ms: object = None, hints_used: object = 0,
                        note: str | None = None) -> dict:
    """Registra a resposta do estudo diário e realimenta os cartões.

    ``confidence``/``elapsed_ms``/``hints_used`` definem a nota do FSRS (``scheduler.rating_for``).
    """
    _, items = _session(db, session_id, pool)
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise ValueError(f"item {item_id} não pertence à sessão")
    if db.execute("SELECT 1 FROM study_answers WHERE session_id=? AND item_id=?", (session_id, item_id)).fetchone():
        raise ValueError(f"item {item_id} já respondido")
    alternatives = item["question"].get("alternativas") or {}
    normalized = (answer or "").strip().upper() or None
    if normalized and normalized != item["correctAnswer"] and not reason:
        return {"requiresReason": True}
    answer, correct, reason = diagnostics.grade_answer(answer, alternatives, item["correctAnswer"], reason)
    extras = diagnostics.answer_extras(confidence if answer else None, elapsed_ms, hints_used, note)
    with db:
        db.execute("""INSERT INTO study_answers(session_id,item_id,source_id,question_number,subject,content_id,
            selected_answer,correct_answer,is_correct,error_reason,answered_at,confidence,elapsed_ms,hints_used,note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (session_id, item_id, item["sourceId"], item["number"], item["subject"], item["contentId"],
                    answer, item["correctAnswer"], int(correct), reason, _iso(now), extras["confidence"],
                    extras["elapsed_ms"], extras["hints_used"], extras["note"]))
        card = _apply_answer(db, content_id=item["contentId"], subject=item["subject"],
                             source_id=item["sourceId"], number=int(item["number"]), correct=correct,
                             at=now, origin="daily", ref=f"daily:{session_id}:{item_id}",
                             confidence=extras["confidence"], elapsed_ms=extras["elapsed_ms"],
                             hints_used=extras["hints_used"])
    rating = scheduler.rating_for(correct, extras["confidence"], extras["elapsed_ms"], extras["hints_used"])
    return {"itemId": item_id, "answer": answer, "correct": correct, "reason": reason,
            "correctAnswer": item["correctAnswer"], "nextReview": card["due_at"],
            "confidence": extras["confidence"], "rating": scheduler.rating_label(rating)}


def progress(db: sqlite3.Connection, now: datetime, *, days: int = 7) -> dict:
    """Domínio por conteúdo, revisões vencidas hoje e previsão dos próximos dias."""
    logs = defaultdict(list)
    for row in db.execute("SELECT card_id,rating FROM review_logs"):
        logs[row["card_id"]].append(int(row["rating"]))
    contents = []
    for row in db.execute("SELECT * FROM review_cards WHERE kind='content'"):
        node = CONTENT_INDEX.get(row["content_id"])
        ratings = logs.get(row["id"], [])
        contents.append({
            "contentId": row["content_id"], "subject": row["subject"],
            "path": " → ".join(node["path"]) if node else row["content_id"],
            "reviews": len(ratings),
            "accuracy": round(sum(r > 1 for r in ratings) / len(ratings), 4) if ratings else None,
            "retrievability": round(scheduler.retrievability(json.loads(row["card_json"]), now), 4),
            "dueAt": row["due_at"],
        })
    contents.sort(key=lambda c: (c["retrievability"], c["path"]))
    today = local_date(now)
    forecast = {(today + timedelta(days=offset)).isoformat(): 0 for offset in range(days)}
    due_today = 0
    cutoff = end_of_local_day(now)
    for row in db.execute("SELECT due_at FROM review_cards"):
        due = _parse(row["due_at"])
        if due <= cutoff:
            due_today += 1
            continue
        key = due.astimezone(LOCAL_TZ).date().isoformat()
        if key in forecast:
            forecast[key] += 1
    return {"dueToday": due_today, "forecast": forecast, "contents": contents}


def reset_learning_context(db: sqlite3.Connection) -> dict:
    """Limpa respostas e sinais de aprendizagem, preservando provas e gabaritos."""
    counts = {
        "studySessions": db.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0],
        "studyAnswers": db.execute("SELECT COUNT(*) FROM study_answers").fetchone()[0],
        "diagnostics": db.execute("SELECT COUNT(*) FROM attempts WHERE mode!='exam'").fetchone()[0],
        "examAttempts": db.execute("SELECT COUNT(*) FROM attempts WHERE mode='exam'").fetchone()[0],
        "reviewCards": db.execute("SELECT COUNT(*) FROM review_cards").fetchone()[0],
    }
    with db:
        db.execute("DELETE FROM study_sessions")
        db.execute("DELETE FROM review_cards")
        # O dia congelado do calendário reflete o progresso apagado; as datas das provas ficam.
        db.execute("DELETE FROM calendar_days")
        db.execute("DELETE FROM attempts WHERE mode!='exam'")
        # Mantém a tentativa e as questões da prova, removendo apenas respostas e métricas pessoais.
        db.execute("""UPDATE attempt_questions SET selected_answer=NULL,error_reason=NULL,
            is_correct=NULL,answered_at=NULL,confidence=NULL,elapsed_ms=NULL,hints_used=0,note=NULL
            WHERE attempt_id IN
            (SELECT id FROM attempts WHERE mode='exam')""")
        db.execute("""UPDATE attempts SET status='in_progress',metrics_json=NULL,analysis_json=NULL,
            analysis_status=NULL,analysis_error=NULL WHERE mode='exam'""")
    return {"ok": True, **counts}
