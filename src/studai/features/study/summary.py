"""Resumo de um período de estudo: a semana (Relatórios) ou o dia (fechamento do Treinamento).

Compara o fim do período com o começo dele: quantas questões, acerto, tempo, dias de
estudo, conteúdos que passaram a consolidados, os que mais avançaram, a prontidão e os
acertos estimados na 1ª fase, os motivos de erro e as trilhas.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, time, timedelta

from studai.features.diagnostics import depth, mastery
from studai.features.reports.service import HALF_LIFE_DAYS, _course
from studai.features.study import calendar, ladder, projection
from studai.features.study import service as study

PERIODS = {"week": 7, "day": 1}
TOP_CONTENTS = 5
MIN_GAIN = 0.05  # avanço de profundidade que vale citar


def _period_start(period: str, now: datetime) -> datetime:
    """Semana: os últimos 7 dias; dia: desde a meia-noite local."""
    if period not in PERIODS:
        raise ValueError("período deve ser week ou day")
    if period == "day":
        return datetime.combine(study.local_date(now), time.min, tzinfo=study.LOCAL_TZ)
    return now - timedelta(days=PERIODS[period])


def _streak(days: set, today) -> int:
    """Dias seguidos com estudo até hoje (ou até ontem, se hoje ainda não estudou)."""
    current = today if today in days else today - timedelta(days=1)
    streak = 0
    while current in days:
        streak += 1
        current -= timedelta(days=1)
    return streak


def period_summary(db: sqlite3.Connection, pool: list[dict], config: dict, course_id: str, now: datetime,
                   period: str = "week") -> dict:
    start = _period_start(period, now)
    phase = study.target_phase(db, course_id, now)
    profile, resolved = _course(config, course_id, pool, phase)
    weights = resolved["weights"]
    course_pool = [item for item in pool if item.get("subject") in profile["pesos"]]
    incidence = mastery.node_incidence(course_pool)
    responses = study.course_responses(db, course_id)
    parsed = [(study._parse(r["answered_at"]), r) for r in responses if r.get("answered_at")]
    inside = [r for at, r in parsed if at >= start]
    before = [r for at, r in parsed if at < start]

    def nodes_at(items: list[dict], moment: datetime) -> dict:
        return depth.compute_depth(items, subject_weights=weights, incidence=incidence,
                                   half_life_days=HALF_LIFE_DAYS, now=moment)

    now_nodes, start_nodes = nodes_at([r for _, r in parsed], now), nodes_at(before, start)
    contents = [node for node in now_nodes.values() if len(node["path_ids"]) == 3 and node["incidence"] > 0]
    consolidated, gains = [], []
    for node in contents:
        previous = start_nodes.get(node["id"], {}).get("depth", 0.0)
        if node["depth"] >= depth.CONSOLIDATED_DEPTH > previous:
            consolidated.append({"id": node["id"], "name": node["name"], "subject": node["subject"]})
        if node["depth"] - previous >= MIN_GAIN:
            gains.append({"id": node["id"], "name": node["name"], "subject": node["subject"],
                          "from": round(previous, 3), "to": node["depth"]})
    gains.sort(key=lambda item: (-(item["to"] - item["from"]), item["id"]))

    subjects = calendar.exam_subjects(profile, phase or "fase1")
    scoped = calendar._scope_weights(weights, subjects)
    readiness_now = calendar.readiness(now_nodes, scoped)["overall"] if scoped else None
    readiness_start = calendar.readiness(start_nodes, scoped)["overall"] if scoped else None
    first_phase = _course(config, course_id, pool, "fase1")[1]["weights"]
    estimate_now = projection.estimate([r for _, r in parsed], first_phase, now)
    estimate_start = projection.estimate(before, first_phase, start)

    answered = len(inside)
    correct = sum(1 for r in inside if r.get("is_correct", r["correct"]))
    seconds = sum(int(r["elapsed_ms"]) for r in inside if r.get("elapsed_ms")) / 1000
    by_subject = Counter(r["subject"] for r in inside)
    subject_correct = Counter(r["subject"] for r in inside if r.get("is_correct", r["correct"]))
    reasons = Counter(r["reason"] for r in inside if r.get("reason") and r["reason"] != "blank")
    study_days = {study.local_date(at) for at, _ in parsed}
    tomorrow = (study.local_date(now) + timedelta(days=1)).isoformat()
    due_tomorrow = study.progress(db, now, days=2)["forecast"].get(tomorrow, 0)
    ladder.ensure_schema(db)
    ladders_done = [ladder.public(row) for row in db.execute(
        "SELECT * FROM ladders WHERE course_id=? AND status='done' AND updated_at>=?", (course_id, start.isoformat()))]
    return {
        "period": period, "start": start.isoformat(), "end": now.isoformat(),
        "answered": answered, "correct": correct, "accuracy": round(correct / answered, 4) if answered else None,
        "studySeconds": round(seconds), "activeDays": len({study.local_date(at) for at, r in parsed if at >= start}),
        "streak": _streak(study_days, study.local_date(now)),
        "byKind": dict(Counter(r.get("kind") or "diagnostic" for r in inside)),
        "subjects": [{"subject": subject, "answered": count, "correct": subject_correct[subject]}
                     for subject, count in by_subject.most_common()],
        "consolidated": consolidated, "improved": gains[:TOP_CONTENTS],
        "readiness": {"from": readiness_start, "to": readiness_now},
        "estimate": {"from": estimate_start[0] if estimate_start else None,
                     "to": estimate_now[0] if estimate_now else None, "questions": projection.QUESTIONS},
        "reasons": dict(reasons.most_common(3)),
        "ladders": {"active": ladder.active(db, course_id), "done": ladders_done},
        "dueTomorrow": due_tomorrow,
    }
