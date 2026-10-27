"""Caderno de erros: as questões que o aluno errou (ou acertou no chute), com o motivo,
a anotação dele, quando volta para revisão e se já foi resolvida depois."""
from __future__ import annotations

import sqlite3

from studai.features.classification.taxonomy import CONTENT_INDEX, live_id
from studai.features.explanations import service as explanations
from studai.features.study import service as study

STATUSES = ("pending", "resolved", "all")


def _missed(response: dict) -> bool:
    return not response.get("is_correct", response["correct"]) or response.get("confidence") == "guess"


def list_errors(db: sqlite3.Connection, course_id: str, *, subject: str | None = None,
                reason: str | None = None, status: str = "pending") -> dict:
    """Uma linha por questão errada, da mais recente para a mais antiga.

    ``resolved``: a última resposta da questão foi um acerto sem chute. ``status``
    filtra entre pendentes (padrão), resolvidas ou todas.
    """
    if status not in STATUSES:
        raise ValueError(f"status deve ser um de {', '.join(STATUSES)}")
    entries: dict[tuple[str, int], dict] = {}
    for response in study.course_responses(db, course_id):
        key = (response["source_id"], int(response["number"]))
        entry = entries.get(key)
        if _missed(response):
            content_id = live_id(response.get("principal_id") or "")
            node = CONTENT_INDEX.get(content_id)
            entry = entries.setdefault(key, {"sourceId": key[0], "number": key[1], "misses": 0, "note": None})
            entry.update(subject=response["subject"], contentId=content_id if node else None,
                         contentPath=" → ".join(node["path"]) if node else None,
                         reason=response.get("reason"), selected=response.get("selected"),
                         correctAnswer=response.get("correct_answer"), missedAt=response["answered_at"],
                         guessed=bool(response.get("is_correct")), kind=response.get("kind"), resolved=False)
            entry["misses"] += 1
            entry["note"] = response.get("note") or entry["note"]
        elif entry:
            entry["resolved"] = True
            entry["resolvedAt"] = response["answered_at"]
    explanations.ensure_schema(db)
    due = {(row["source_id"], int(row["question_number"])): row["due_at"] for row in db.execute(
        "SELECT source_id,question_number,due_at FROM review_cards WHERE kind='question'")}
    approved = {(row[0], int(row[1])): row[2] for row in db.execute(
        "SELECT source_id,question_number,status FROM question_explanations")}
    reasons, subjects = {}, {}
    rows = []
    for key, entry in entries.items():
        entry["dueAt"] = due.get(key)
        entry["explanation"] = approved.get(key)
        if status != "all" and entry["resolved"] != (status == "resolved"):
            continue
        subjects[entry["subject"]] = subjects.get(entry["subject"], 0) + 1
        if subject and entry["subject"] != subject:
            continue
        reasons[entry["reason"] or "guess"] = reasons.get(entry["reason"] or "guess", 0) + 1
        if reason and (entry["reason"] or "guess") != reason:
            continue
        rows.append(entry)
    rows.sort(key=lambda entry: entry["missedAt"] or "", reverse=True)
    return {"items": rows, "total": len(rows), "bySubject": subjects, "byReason": reasons}
