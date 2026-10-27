"""Acertos estimados na 1ª fase e para onde eles vão até a prova.

Estimativa de hoje: 90 × Σ (peso F1 da matéria × domínio da matéria), com o domínio do
``mastery`` (respostas mais antigas valem menos). A tendência combina essa estimativa,
recalculada no fim de cada uma das últimas semanas, com os resultados reais dos
simulados; uma reta pelos pontos das últimas 6 semanas dá o valor na data da prova.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta

from studai.features.diagnostics import mastery
from studai.features.study import service as study

QUESTIONS = 90
MIN_RESPONSES = 30  # menos que isso, a estimativa ainda é quase só a priori
HISTORY_WEEKS = 6
MIN_SPAN_DAYS = 14
MIN_SIMULADO_QUESTIONS = 30


def _parse(value: str) -> datetime:
    return study._parse(value)


def estimate_correct(responses: list[dict], weights_f1: dict[str, float], now: datetime) -> float | None:
    """Acertos esperados em 90 questões; ``None`` com poucas respostas."""
    if len(responses) < MIN_RESPONSES:
        return None
    nodes = mastery.compute_nodes(responses, subject_weights=weights_f1, half_life_days=study.HALF_LIFE_DAYS,
                                  now=now)
    return round(QUESTIONS * mastery.weighted_score(nodes, weights_f1), 1)


def simulado_points(db: sqlite3.Connection, course_id: str) -> list[dict]:
    """Provas e simulados FUVEST concluídos com pelo menos 30 questões, em acertos de 90."""
    points = []
    for row in db.execute("""SELECT a.id, a.created_at, a.plan_json, COUNT(*) AS total,
            SUM(COALESCE(aq.is_correct, 0)) AS correct, MAX(aq.answered_at) AS last_answer
            FROM attempts a JOIN attempt_questions aq ON aq.attempt_id = a.id
            WHERE a.course_id=? AND a.mode='exam' AND a.status='completed'
            GROUP BY a.id ORDER BY a.created_at""", (course_id,)):
        if row["total"] < MIN_SIMULADO_QUESTIONS:
            continue
        plan = json.loads(row["plan_json"] or "{}") or {}
        if (plan.get("origin") or "fuvest") != "fuvest":
            continue
        points.append({"at": row["last_answer"] or row["created_at"], "kind": "simulado" if plan.get("timed") else "exam",
                       "label": plan.get("label"), "correct": int(row["correct"]), "total": int(row["total"]),
                       "value": round(int(row["correct"]) / int(row["total"]) * QUESTIONS, 1)})
    return points


def _trend(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Reta dos mínimos quadrados (intercepto em x = 0, inclinação por dia)."""
    if len(points) < 2:
        return None
    xs, ys = [x for x, _ in points], [y for _, y in points]
    if max(xs) - min(xs) < MIN_SPAN_DAYS:
        return None
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / spread
    return mean_y - slope * mean_x, slope


def project(db: sqlite3.Connection, course_id: str, weights_f1: dict[str, float], now: datetime,
            exam_day: date | None = None, cutoff: int | None = None) -> dict:
    """Estimativa de hoje, histórico semanal, simulados e valor projetado na prova."""
    responses = study.course_responses(db, course_id)
    today = study.local_date(now)
    current = estimate_correct(responses, weights_f1, now)
    weekly = []
    for weeks in range(HISTORY_WEEKS, 0, -1):
        day = today - timedelta(days=7 * weeks)
        end = datetime.combine(day, time.max, tzinfo=study.LOCAL_TZ)
        value = estimate_correct([r for r in responses if r["answered_at"] and _parse(r["answered_at"]) <= end],
                                 weights_f1, end)
        if value is not None:
            weekly.append({"date": day.isoformat(), "value": value})
    if current is not None:
        weekly.append({"date": today.isoformat(), "value": current})
    simulados = simulado_points(db, course_id)
    since = today - timedelta(days=7 * HISTORY_WEEKS)
    series = [((date.fromisoformat(point["date"]) - today).days, point["value"]) for point in weekly]
    series += [((study.local_date(_parse(point["at"])) - today).days, point["value"]) for point in simulados
               if study.local_date(_parse(point["at"])) >= since]
    trend = _trend(series)
    at_exam = None
    if trend and exam_day:
        intercept, slope = trend
        at_exam = round(min(QUESTIONS, max(0.0, intercept + slope * (exam_day - today).days)), 1)
    return {"questions": QUESTIONS, "current": current, "cutoff": cutoff, "atExam": at_exam,
            "slopePerWeek": round(trend[1] * 7, 2) if trend else None, "weekly": weekly,
            "simulados": simulados, "responses": len(responses), "minResponses": MIN_RESPONSES}
