"""Simulado cronometrado: uma prova completa do acervo nas condições da 1ª fase.

É uma sessão de prova (``attempts.mode = 'exam'``) com ``plan.timed``: o relógio começa
na criação, a prova termina sozinha quando o tempo acaba, a resposta pode ser trocada
até a entrega e nada de gabarito aparece antes da correção. Os motivos dos erros entram
na correção.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from studai.features.diagnostics import service as diagnostics
from studai.features.reports import service as reports

DEFAULT_DURATION_S = 5 * 60 * 60  # 1ª fase: 90 questões em 5 h
MIN_DURATION_S = 5 * 60
MAX_DURATION_S = 6 * 60 * 60
PACE_SECONDS = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _attempt(db: sqlite3.Connection, attempt_id: str) -> tuple[sqlite3.Row, dict]:
    row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if not row:
        raise reports.NotFound(f"sessão de prova não encontrada: {attempt_id}")
    plan = json.loads(row["plan_json"] or "{}") or {}
    if row["mode"] != "exam" or not plan.get("timed"):
        raise ValueError("a sessão não é um simulado cronometrado")
    return row, plan


def is_timed(db: sqlite3.Connection, attempt_id: str) -> bool:
    row = db.execute("SELECT mode, plan_json FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    return bool(row and row["mode"] == "exam" and (json.loads(row["plan_json"] or "{}") or {}).get("timed"))


def start_simulado(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict], exam_source_id: str,
                   *, exam_name: str | None = None, duration_s: int = DEFAULT_DURATION_S) -> dict:
    """Abre o simulado com as questões elegíveis da prova (revisadas e com gabarito)."""
    if isinstance(duration_s, bool) or not isinstance(duration_s, int) \
            or not MIN_DURATION_S <= duration_s <= MAX_DURATION_S:
        raise ValueError("a duração do simulado deve ficar entre 5 minutos e 6 horas")
    session = reports.start_exam_session(db, config, course_id, pool, exam_source_id, exam_name=exam_name)
    row = db.execute("SELECT plan_json FROM attempts WHERE id=?", (session["id"],)).fetchone()
    plan = json.loads(row["plan_json"])
    plan.update(timed=True, duration_s=duration_s)
    with db:
        db.execute("UPDATE attempts SET plan_json=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), session["id"]))
    return detail(db, session["id"])


def _deadline(row: sqlite3.Row, plan: dict) -> datetime:
    return _parse(row["created_at"]) + timedelta(seconds=int(plan.get("duration_s") or DEFAULT_DURATION_S))


def _close_if_expired(db: sqlite3.Connection, row: sqlite3.Row, plan: dict, now: datetime) -> bool:
    """Entrega a prova sozinha quando o tempo acaba; devolve True se ela está encerrada."""
    if row["status"] != "in_progress":
        return True
    if now < _deadline(row, plan):
        return False
    reports.finish_exam_session(db, row["id"])
    return True


def answer(db: sqlite3.Connection, attempt_id: str, ordinal: int, letter: str | None, *,
           confidence: str | None = None, elapsed_ms: object = None, now: datetime | None = None) -> dict:
    """Marca (ou troca, ou apaga com ``None``) a alternativa de uma questão do simulado."""
    now = now or _now()
    row, plan = _attempt(db, attempt_id)
    if _close_if_expired(db, row, plan, now):
        raise ValueError("o simulado já foi entregue ou o tempo acabou")
    question = db.execute("SELECT * FROM attempt_questions WHERE attempt_id=? AND ordinal=?",
                          (attempt_id, int(ordinal))).fetchone()
    if question is None:
        raise ValueError(f"questão {ordinal} não pertence ao simulado")
    letter = (letter or "").strip().upper() or None
    if letter is not None and letter not in (json.loads(question["question_json"]).get("alternativas") or {}):
        raise ValueError("resposta deve ser uma das alternativas disponíveis")
    extras = diagnostics.answer_extras(confidence if letter else None, elapsed_ms)
    # O tempo soma as visitas à questão (o aluno pode ir e voltar).
    spent = (question["elapsed_ms"] or 0) + (extras["elapsed_ms"] or 0)
    with db:
        if letter is None:
            db.execute("""UPDATE attempt_questions SET selected_answer=NULL,is_correct=NULL,answered_at=NULL,
                confidence=NULL,elapsed_ms=? WHERE attempt_id=? AND ordinal=?""", (spent, attempt_id, int(ordinal)))
        else:
            db.execute("""UPDATE attempt_questions SET selected_answer=?,is_correct=?,answered_at=?,confidence=?,
                elapsed_ms=?,error_reason=NULL WHERE attempt_id=? AND ordinal=?""",
                       (letter, int(letter == question["correct_answer"]), now.isoformat(), extras["confidence"],
                        spent, attempt_id, int(ordinal)))
    return {"ordinal": int(ordinal), "answer": letter, "confidence": extras["confidence"]}


def flag(db: sqlite3.Connection, attempt_id: str, ordinal: int, flagged: bool) -> dict:
    """Marca a questão para voltar depois (só enquanto o simulado está aberto)."""
    row, plan = _attempt(db, attempt_id)
    if _close_if_expired(db, row, plan, _now()):
        raise ValueError("o simulado já foi entregue ou o tempo acabou")
    with db:
        updated = db.execute("UPDATE attempt_questions SET flagged=? WHERE attempt_id=? AND ordinal=?",
                             (int(bool(flagged)), attempt_id, int(ordinal))).rowcount
    if not updated:
        raise ValueError(f"questão {ordinal} não pertence ao simulado")
    return {"ordinal": int(ordinal), "flagged": bool(flagged)}


def finish(db: sqlite3.Connection, attempt_id: str) -> dict:
    row, _ = _attempt(db, attempt_id)
    if row["status"] == "in_progress":
        reports.finish_exam_session(db, attempt_id)
    return detail(db, attempt_id)


def set_reason(db: sqlite3.Connection, attempt_id: str, ordinal: int, reason: str, note: str | None = None) -> dict:
    """Motivo (e anotação) de um erro, informado na correção."""
    row, _ = _attempt(db, attempt_id)
    if row["status"] == "in_progress":
        raise ValueError("entregue o simulado antes de informar os motivos dos erros")
    question = db.execute("SELECT * FROM attempt_questions WHERE attempt_id=? AND ordinal=?",
                          (attempt_id, int(ordinal))).fetchone()
    if question is None or question["answered_at"] is None or question["is_correct"]:
        raise ValueError("só questões respondidas e erradas recebem motivo")
    if reason not in diagnostics.REASONS - {"blank"}:
        raise ValueError("motivo de erro inválido")
    extras = diagnostics.answer_extras(note=note)
    with db:
        db.execute("UPDATE attempt_questions SET error_reason=?,note=? WHERE attempt_id=? AND ordinal=?",
                   (reason, extras["note"], attempt_id, int(ordinal)))
    return {"ordinal": int(ordinal), "reason": reason, "note": extras["note"]}


def detail(db: sqlite3.Connection, attempt_id: str, now: datetime | None = None) -> dict:
    """Estado do simulado; o gabarito e a correção só aparecem depois da entrega."""
    now = now or _now()
    row, plan = _attempt(db, attempt_id)
    finished = _close_if_expired(db, row, plan, now)
    rows = [dict(r) for r in db.execute("SELECT * FROM attempt_questions WHERE attempt_id=? ORDER BY ordinal",
                                        (attempt_id,))]
    deadline = _deadline(row, plan)
    questions = []
    for item in rows:
        entry = {"ordinal": item["ordinal"], "number": item["question_number"], "subject": item["subject"],
                 "selected": item["selected_answer"], "flagged": bool(item.get("flagged")),
                 "elapsedMs": item["elapsed_ms"]}
        if finished:
            entry.update(correctAnswer=item["correct_answer"], correct=bool(item["is_correct"]),
                         reason=item["error_reason"], note=item["note"], confidence=item["confidence"])
        questions.append(entry)
    payload = {"id": attempt_id, "exam": plan.get("exam"), "label": plan.get("label"), "timed": True,
               "status": "completed" if finished else "in_progress", "startedAt": row["created_at"],
               "deadline": deadline.isoformat(), "durationS": int(plan.get("duration_s") or DEFAULT_DURATION_S),
               "remainingS": 0 if finished else max(0, int((deadline - now).total_seconds())),
               "questions": questions}
    if finished:
        payload["score"] = score(rows)
    return payload


def score(rows: list[dict]) -> dict:
    """Acertos, por matéria, e as questões que passaram do ritmo da 1ª fase."""
    by_subject: dict[str, Counter] = defaultdict(Counter)
    for item in rows:
        counts = by_subject[item["subject"]]
        counts["total"] += 1
        counts["answered"] += int(item["answered_at"] is not None)
        counts["correct"] += int(bool(item["is_correct"]))
    total = len(rows)
    correct = sum(int(bool(item["is_correct"])) for item in rows)
    return {"total": total, "correct": correct,
            "answered": sum(int(item["answered_at"] is not None) for item in rows),
            # Prova com questões fora do acervo revisado: projeta os acertos para 90 questões.
            "scaledTo90": round(correct / total * 90) if total else 0,
            "subjects": [{"subject": subject, **dict(counts)} for subject, counts in
                         sorted(by_subject.items(), key=lambda pair: (-pair[1]["total"], pair[0]))],
            "overPace": [item["ordinal"] for item in rows if (item["elapsed_ms"] or 0) > PACE_SECONDS * 1000]}
