"""Leituras obrigatórias da FUVEST: uma lista de obras com o progresso de leitura e o
desempenho nas questões de cada obra.

As obras são os conteúdos da área ``Leituras obrigatórias FUVEST`` da taxonomia (a
visão de conjunto da lista fica de fora). Questões e respostas contam para a obra
quando o conteúdo principal da questão está nela ou num subconteúdo dela.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from studai.features.classification.taxonomy import CONTENT_INDEX, live_id
from studai.features.study import service as study

AREA_ID = "literatura.area.leituras-obrigatorias-fuvest"
OVERVIEW_ID = "literatura.content.conteudo-de-leitura-obrigatoria"
STATUSES = {"not_started": "Não comecei", "reading": "Lendo", "read": "Li", "reviewed": "Revisei"}
MAX_NOTE_LENGTH = 2000
# Plano de leitura: as obras ainda não lidas se espalham até TRAIN_DAYS antes da prova,
# para sobrar tempo de treinar as questões sobre elas (prova perto: até a véspera de 2 semanas).
TRAIN_DAYS = 60
MIN_TRAIN_DAYS = 14
DONE = ("read", "reviewed")


def reading_plan(works: list[dict], today: date, exam_day: date | None) -> dict | None:
    """Prazo sugerido para cada obra ainda não lida, em ordem: as que estão em leitura primeiro.

    Sem prova cadastrada (ou prova já passada), não há plano.
    """
    if exam_day is None or exam_day <= today:
        return None
    left = (exam_day - today).days
    until = exam_day - timedelta(days=TRAIN_DAYS if left > TRAIN_DAYS + 30 else min(MIN_TRAIN_DAYS, left // 2))
    pending = [work for work in works if work["status"] not in DONE]
    pending.sort(key=lambda work: (work["status"] != "reading", works.index(work)))
    if not pending:
        return {"until": until.isoformat(), "daysPerWork": None, "deadlines": {}}
    span = max(1, (until - today).days)
    step = span / len(pending)
    deadlines = {work["contentId"]: (today + timedelta(days=round(step * (index + 1)))).isoformat()
                 for index, work in enumerate(pending)}
    return {"until": until.isoformat(), "daysPerWork": round(step), "deadlines": deadlines}


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS reading_progress (
        content_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK(status IN ('not_started','reading','read','reviewed')),
        note TEXT, updated_at TEXT NOT NULL)""")


def works() -> list[dict]:
    """Obras da lista atual, na ordem da taxonomia."""
    return [node for node in CONTENT_INDEX.values()
            if len(node["path_ids"]) == 3 and node["path_ids"][1] == AREA_ID and node["id"] != OVERVIEW_ID
            and not node.get("deprecated")]


def _work_of(content_id: str | None, ids: set[str]) -> str | None:
    node = CONTENT_INDEX.get(live_id(content_id or ""))
    return next((identity for identity in (node or {}).get("path_ids", []) if identity in ids), None)


def list_readings(db: sqlite3.Connection, pool: list[dict], course_id: str, today: date | None = None,
                  exam_day: date | None = None) -> dict:
    """Cada obra com status de leitura, anotação, questões no acervo, respostas e prazo sugerido."""
    ensure_schema(db)
    items = works()
    ids = {node["id"] for node in items}
    progress = {row["content_id"]: row for row in db.execute("SELECT * FROM reading_progress")}
    questions = Counter(_work_of((entry["question"].get("classificacao") or {}).get("conteudo_principal_id"), ids)
                        for entry in pool)
    answered, correct = Counter(), Counter()
    for response in study.course_responses(db, course_id):
        work = _work_of(response.get("principal_id"), ids)
        if work:
            answered[work] += 1
            correct[work] += int(bool(response.get("correct")))
    rows = []
    for node in items:
        row = progress.get(node["id"])
        rows.append({"contentId": node["id"], "name": node["name"], "description": node.get("description"),
                     "status": row["status"] if row else "not_started", "note": row["note"] if row else None,
                     "updatedAt": row["updated_at"] if row else None, "questions": questions.get(node["id"], 0),
                     "answered": answered[node["id"]], "correct": correct[node["id"]]})
    done = sum(1 for row in rows if row["status"] in DONE)
    plan = reading_plan(rows, today or datetime.now(timezone.utc).date(), exam_day)
    for row in rows:
        row["deadline"] = (plan or {}).get("deadlines", {}).get(row["contentId"])
    following = min((row for row in rows if row["deadline"]), key=lambda row: row["deadline"], default=None)
    return {"works": rows, "read": done, "total": len(rows), "statuses": STATUSES,
            "plan": {**plan, "next": {"name": following["name"], "deadline": following["deadline"]} if following else None}
            if plan else None}


def set_reading(db: sqlite3.Connection, content_id: str, status: str | None = None, note: str | None = None) -> dict:
    """Atualiza o status de leitura e/ou a anotação de uma obra."""
    ensure_schema(db)
    if content_id not in {node["id"] for node in works()}:
        raise ValueError(f"obra fora da lista de leituras obrigatórias: {content_id}")
    current = db.execute("SELECT * FROM reading_progress WHERE content_id=?", (content_id,)).fetchone()
    status = status or (current["status"] if current else "not_started")
    if status not in STATUSES:
        raise ValueError(f"status deve ser um de {', '.join(STATUSES)}")
    if note is None:
        note = current["note"] if current else None
    else:
        note = str(note).strip() or None
        if note and len(note) > MAX_NOTE_LENGTH:
            raise ValueError(f"a anotação deve ter até {MAX_NOTE_LENGTH} caracteres")
    with db:
        db.execute("""INSERT INTO reading_progress(content_id,status,note,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(content_id) DO UPDATE SET status=excluded.status, note=excluded.note,
            updated_at=excluded.updated_at""", (content_id, status, note, datetime.now(timezone.utc).isoformat()))
    return {"contentId": content_id, "status": status, "note": note}
