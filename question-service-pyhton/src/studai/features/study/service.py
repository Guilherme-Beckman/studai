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
from studai.features.diagnostics import service as diagnostics
from studai.features.study import scheduler

STUDY_SCHEMA_VERSION = 1
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
GAP_ACCURACY = 0.7
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
    """)
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
            origin: str, source_ref: str) -> bool:
    """Aplica uma revisão uma única vez por `source_ref`; devolve False se já existia."""
    if db.execute("SELECT 1 FROM review_logs WHERE source_ref=?", (source_ref,)).fetchone():
        return False
    if row["last_review_at"] and at < _parse(row["last_review_at"]):
        at = _parse(row["last_review_at"])
    card, log = scheduler.review(json.loads(row["card_json"]), correct, at)
    db.execute("UPDATE review_cards SET card_json=?,due_at=?,last_review_at=? WHERE id=?",
               (json.dumps(card), _iso(scheduler.due_at(card)), _iso(at), row["id"]))
    db.execute("INSERT INTO review_logs(card_id,rating,reviewed_at,origin,source_ref,log_json) "
               "VALUES(?,?,?,?,?,?)", (row["id"], int(log["rating"]), _iso(at), origin, source_ref,
                                       json.dumps(log)))
    return True


def _apply_answer(db: sqlite3.Connection, *, content_id: str, subject: str, source_id: str,
                  number: int, correct: bool, at: datetime, origin: str, ref: str) -> sqlite3.Row:
    """Revisa o cartão do conteúdo; erro cria (ou revisa) também o cartão da questão."""
    content_card = _card(db, "content", content_id, subject, at)
    _review(db, content_card, correct, at, origin, ref)
    question_card = db.execute("SELECT * FROM review_cards WHERE kind='question' AND content_id=? "
                               "AND source_id=? AND question_number=?",
                               (content_id, source_id, number)).fetchone()
    if question_card is None and not correct:
        question_card = _card(db, "question", content_id, subject, at, source_id, number)
    if question_card is not None:
        _review(db, question_card, correct, at, origin, ref + ":q")
    return db.execute("SELECT * FROM review_cards WHERE id=?", (content_card["id"],)).fetchone()


def sync_diagnostic_answers(db: sqlite3.Connection) -> int:
    """Importa as respostas dos diagnósticos para o agendamento; seguro para repetir."""
    rows = db.execute("""SELECT attempt_id,ordinal,source_id,question_number,subject,question_json,
        is_correct,answered_at FROM attempt_questions WHERE answered_at IS NOT NULL
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
                          at=_parse(row["answered_at"]), origin="diagnostic", ref=ref)
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
    contents = (json.loads(row["metrics_json"]) or {}).get("contents") or {}
    return {content_id for content_id, metric in contents.items()
            if metric.get("status") == "insufficient_data"
            or (metric.get("accuracy") is not None and metric["accuracy"] < GAP_ACCURACY)}


def _item(reason: str, entry: dict, content_id: str) -> dict:
    node = CONTENT_INDEX[content_id]
    return {"reason": reason, "sourceId": entry["source_id"], "number": entry["number"],
            "subject": entry["subject"], "contentId": content_id, "contentPath": " → ".join(node["path"]),
            "question": entry["question"], "correctAnswer": entry["correct_answer"]}


def build_plan(db: sqlite3.Connection, pool: list[dict], profile: dict, course_id: str, now: datetime,
               *, size: int = 15, new_per_day: int = 5, recent_days: int = 7) -> list[dict]:
    """Monta as faixas do dia: questões erradas vencidas, conteúdos vencidos e conteúdos novos."""
    weights = {subject: float(weight) for subject, weight in profile["pesos"].items()}
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

    # 3. Conteúdos novos: lacunas do último diagnóstico primeiro, depois pelos pesos do curso.
    known = {row[0] for row in db.execute("SELECT content_id FROM review_cards WHERE kind='content'")}
    gaps = _gap_nodes(db, course_id)
    fresh_contents = sorted(content_id for content_id in by_content if content_id not in known)
    rng.shuffle(fresh_contents)
    # Matéria e área são amplas demais para marcar lacuna: só conta do conteúdo para baixo.
    is_gap = {c: bool(gaps & set(aggregate_content_ids([c])[2:])) for c in fresh_contents}
    subject_of = {c: CONTENT_INDEX[c]["subject"] for c in fresh_contents}
    budget = min(new_per_day, size - len(items))
    gap_first = sorted((c for c in fresh_contents if is_gap[c]), key=lambda c: -weights.get(subject_of[c], 0))
    chosen = [("new_gap", c) for c in gap_first[:max(budget, 0)]]
    remaining = budget - len(chosen)
    if remaining > 0:
        others = [c for c in fresh_contents if not is_gap[c] and subject_of[c] in weights]
        available = Counter(subject_of[c] for c in others)
        quotas = diagnostics.allocate_counts(available, weights, remaining) if others else {}
        for content_id in others:
            if quotas.get(subject_of[content_id], 0) > 0:
                quotas[subject_of[content_id]] -= 1
                chosen.append(("new_content", content_id))
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
                         correctAnswer=answer["correct_answer"], errorReason=answer["error_reason"])
        payload_items.append(entry)
    counts = Counter(item["reason"] for item in items)
    return {"id": row["id"], "date": row["local_date"], "courseId": row["course_id"],
            "counts": {reason: counts.get(reason, 0) for reason in REASONS},
            "answered": len(answers), "correct": sum(int(a["is_correct"]) for a in answers.values()),
            "items": payload_items}


def record_study_answer(db: sqlite3.Connection, session_id: str, item_id: str, answer: str | None,
                        reason: str | None, now: datetime, pool: list[dict] | None = None) -> dict:
    """Registra a resposta do estudo diário e realimenta os cartões."""
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
    with db:
        db.execute("""INSERT INTO study_answers(session_id,item_id,source_id,question_number,subject,content_id,
            selected_answer,correct_answer,is_correct,error_reason,answered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                   (session_id, item_id, item["sourceId"], item["number"], item["subject"], item["contentId"],
                    answer, item["correctAnswer"], int(correct), reason, _iso(now)))
        card = _apply_answer(db, content_id=item["contentId"], subject=item["subject"],
                             source_id=item["sourceId"], number=int(item["number"]), correct=correct,
                             at=now, origin="daily", ref=f"daily:{session_id}:{item_id}")
    return {"itemId": item_id, "answer": answer, "correct": correct, "reason": reason,
            "correctAnswer": item["correctAnswer"], "nextReview": card["due_at"]}


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
