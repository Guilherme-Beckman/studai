"""Relatórios de evolução por curso e sessões persistidas de "Responder prova".

Implementa ``features/reports/CONTRACT.md``. Casos de uso puros sobre uma conexão
SQLite já aberta (``diagnostics.connect_db``), a configuração de cursos e o pool de
questões elegíveis (``diagnostics.load_question_pool``); não conhecem HTTP.

O domínio por nó vem de ``diagnostics.mastery``: em cada ponto da linha do tempo
(uma tentativa com ao menos uma resposta) os nós são recalculados com todas as
respostas acumuladas até ali, com decaimento por recência relativo ao instante
do ponto.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from studai.features.answer_keys.service import exam_variant
from studai.features.diagnostics import mastery
from studai.features.diagnostics import service as diagnostics
from studai.features.diagnostics.courses import resolve_weights

DEFAULT_COURSE_ID = "eng-comp-sao-carlos"
HALF_LIFE_DAYS = 30
MAX_PRIORITIES = 10
MAX_RECENT_ANSWERS = 20
FINISHED_STATUSES = ("completed", "analysis_failed")


class NotFound(FileNotFoundError):
    """Curso, nó ou sessão inexistente (a API traduz para 404)."""


# --------------------------------------------------------------------------- utilidades

def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _course(config: dict, course_id: str, pool: list[dict], phase: str | None = None) -> tuple[dict, dict]:
    """Perfil do curso e pesos efetivos (``resolve_weights``) para a fase da prova-alvo."""
    if course_id not in (config.get("cursos") or {}):
        raise NotFound(f"curso não configurado: {course_id}")
    profile = diagnostics.course_profile(config, course_id)
    course_pool = [item for item in pool if item.get("subject") in profile["pesos"]]
    try:
        resolved = resolve_weights(profile, course_pool, phase)
    except ValueError:
        # Sem questões revisadas para estimar a 1ª fase: usa os pesos manuais.
        resolved = resolve_weights({**profile, "formula": None}, course_pool, phase)
    return profile, resolved


def exam_label(source_id: str | None, name: str | None = None) -> str:
    """Rótulo pt-BR de uma prova completa, ex.: ``Prova FUVEST 2024 (K)``."""
    for text in (name, source_id):
        if not text:
            continue
        year = re.search(r"fuvest[-_ ]*(\d{4})", text, re.I) or re.search(r"(?<!\d)(20\d{2})(?!\d)", text)
        if not year:
            continue
        variant = exam_variant(Path(str(text).replace("/", "_")))
        return f"Prova FUVEST {year.group(1)}" + (f" ({variant})" if variant else "")
    return "Prova completa"


def _attempt_label(db: sqlite3.Connection, attempt_id: str, mode: str) -> str:
    if mode == "exam":
        # Só o plano das provas é lido: o de diagnósticos adaptativos pode ser grande.
        row = db.execute("SELECT plan_json FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        try:
            plan = json.loads(row[0] or "{}") if row else {}
        except json.JSONDecodeError:
            plan = {}
        plan = plan if isinstance(plan, dict) else {}
        return plan.get("label") or exam_label(plan.get("exam"), plan.get("exam_name"))
    if mode == "adaptive":
        return "Diagnóstico adaptativo"
    return "Diagnóstico"


def _kind(mode: str | None) -> str:
    return "exam" if mode == "exam" else "diagnostic"


# --------------------------------------------------------------------------- histórico

def _history(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict]) -> dict:
    """Respostas do curso agrupadas por tentativa, com os nós cumulativos por ponto."""
    profile, resolved = _course(config, course_id, pool)
    weights = resolved["weights"]
    incidence = mastery.node_incidence([item for item in pool if item.get("subject") in profile["pesos"]])
    rows = [dict(row) for row in db.execute(
        """SELECT aq.*, a.mode AS attempt_mode
           FROM attempt_questions aq JOIN attempts a ON a.id = aq.attempt_id
           WHERE a.course_id = ? AND aq.answered_at IS NOT NULL
           ORDER BY a.created_at, a.id, aq.answered_at, aq.ordinal""", (course_id,))]
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["attempt_id"], []).append(row)
    points: list[dict] = []
    cumulative: list[dict] = []
    answers: list[dict] = []
    for attempt_id, attempt_rows in groups.items():
        mode = attempt_rows[0].get("attempt_mode") or "static"
        kind = _kind(mode)
        at = max(row["answered_at"] for row in attempt_rows)
        for row in attempt_rows:
            response = mastery.response_from_row(row)
            cumulative.append(response)
            answers.append({"row": row, "response": response, "kind": kind})
        nodes = mastery.compute_nodes(cumulative, subject_weights=weights, incidence=incidence,
                                      half_life_days=HALF_LIFE_DAYS, now=_parse_time(at))
        correct = sum(1 for row in attempt_rows if row.get("is_correct"))
        points.append({"id": attempt_id, "kind": kind, "label": _attempt_label(db, attempt_id, mode), "at": at,
                       "answered": len(attempt_rows), "accuracy": round(correct / len(attempt_rows), 4),
                       "estimated_score": mastery.weighted_score(nodes, weights), "nodes": nodes})
    final = points[-1]["nodes"] if points else mastery.compute_nodes(
        [], subject_weights=weights, incidence=incidence, half_life_days=HALF_LIFE_DAYS)
    return {"profile": profile, "resolved": resolved, "weights": weights, "points": points,
            "nodes": final, "answers": answers}


def _children_index(nodes: dict[str, dict]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for node in nodes.values():
        if node.get("parent_id") in nodes:
            children.setdefault(node["parent_id"], []).append(node["id"])
    for ids in children.values():
        ids.sort(key=lambda identity: (-nodes[identity]["priority"], identity))
    return children


def _series(node_id: str, points: list[dict]) -> list[float | None]:
    values = []
    for point in points:
        node = point["nodes"].get(node_id)
        values.append(node["mastery"] if node and node["attempts"] > 0 else None)
    return values


def _public_node(node: dict, points: list[dict], children: dict[str, list[str]]) -> dict:
    series = _series(node["id"], points)
    delta = None
    if len(series) >= 2 and series[-1] is not None and series[-2] is not None:
        delta = round(series[-1] - series[-2], 4)
    return {**node, "delta": delta, "series": series, "children": list(children.get(node["id"], []))}


def _latest_report(db: sqlite3.Connection, course_id: str):
    row = db.execute(
        "SELECT metrics_json FROM attempts WHERE course_id=? AND mode!='exam' AND metrics_json IS NOT NULL "
        f"AND status IN ({','.join('?' * len(FINISHED_STATUSES))}) ORDER BY created_at DESC LIMIT 1",
        (course_id, *FINISHED_STATUSES)).fetchone()
    if not row:
        return None
    try:
        metrics = json.loads(row[0]) or {}
    except json.JSONDecodeError:
        return None
    return metrics.get("report") if isinstance(metrics, dict) else None


# --------------------------------------------------------------------------- casos de uso

def overview(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict]) -> dict:
    """Painel de evolução do curso (``GET /api/reports/overview``)."""
    history = _history(db, config, course_id, pool)
    points, nodes, weights = history["points"], history["nodes"], history["weights"]
    resolved, profile = history["resolved"], history["profile"]
    children = _children_index(nodes)
    public_nodes = {identity: _public_node(node, points, children) for identity, node in nodes.items()}
    roots = sorted((identity for identity, node in nodes.items() if node["level"] == "subject"),
                   key=lambda identity: (-weights.get(nodes[identity]["subject"], 0.0), identity))
    priorities = [node["id"] for node in sorted(
        (node for node in nodes.values() if node["level"] != "subject"),
        key=lambda node: (-node["priority"], node["id"]))[:MAX_PRIORITIES]]
    areas = [node for node in nodes.values() if node["level"] == "area"]
    area_total = sum(node["incidence"] * node["course_weight"] for node in areas)
    area_covered = sum(node["incidence"] * node["course_weight"] for node in areas if node["attempts"] > 0)
    statuses = Counter(node["status"] for node in nodes.values())
    if points:
        kpis = {"estimated_score": points[-1]["estimated_score"],
                "estimated_score_delta": (round(points[-1]["estimated_score"] - points[-2]["estimated_score"], 4)
                                          if len(points) >= 2 else None),
                "answered": sum(point["answered"] for point in points), "sessions": len(points),
                "coverage": round(area_covered / area_total, 4) if area_total else 0.0,
                "strong": statuses.get("strong", 0), "gaps": statuses.get("gap", 0),
                "last_activity": points[-1]["at"]}
    else:
        kpis = {"estimated_score": 0.0, "estimated_score_delta": None, "answered": 0, "sessions": 0,
                "coverage": 0.0, "strong": 0, "gaps": 0, "last_activity": None}
    course = {"id": course_id, "name": profile.get("nome", course_id), "weights": weights,
              "non_assessable": resolved.get("non_assessable") or {},
              "components": resolved.get("components") or {}, "fase2": profile.get("fase2") or {}}
    timeline = [{key: point[key] for key in ("id", "kind", "label", "at", "answered", "accuracy", "estimated_score")}
                for point in points]
    return {"course": course, "kpis": kpis, "timeline": timeline, "roots": roots, "priorities": priorities,
            "nodes": public_nodes, "latest_report": _latest_report(db, course_id)}


def node_detail(db: sqlite3.Connection, config: dict, course_id: str, node_id: str, pool: list[dict]) -> dict:
    """Detalhe de um nó (``GET /api/reports/nodes/<id>``)."""
    history = _history(db, config, course_id, pool)
    points, nodes = history["points"], history["nodes"]
    if node_id not in nodes:
        raise NotFound(f"nó não encontrado: {node_id}")
    children = _children_index(nodes)
    series = []
    for point in points:
        node = point["nodes"].get(node_id)
        series.append({"t": point["at"], "attempt_id": point["id"], "kind": point["kind"],
                       "mastery": node["mastery"] if node else None,
                       "lower": node["lower"] if node else None,
                       "upper": node["upper"] if node else None,
                       "attempts": node["attempts"] if node else 0})
    recent = []
    for answer in history["answers"]:
        if node_id not in mastery.evidence_weights(answer["response"]):
            continue
        row = answer["row"]
        recent.append({"at": row["answered_at"], "attempt_id": row["attempt_id"], "kind": answer["kind"],
                       "source_id": row["source_id"], "number": row["question_number"],
                       "correct": bool(row["is_correct"]), "selected": row["selected_answer"],
                       "correct_answer": row["correct_answer"], "reason": row["error_reason"],
                       "topic": row["topic"]})
    recent.sort(key=lambda item: item["at"] or "")
    return {"node": _public_node(nodes[node_id], points, children), "series": series,
            "children": [_public_node(nodes[child], points, children) for child in children.get(node_id, [])],
            "recent_answers": recent[-MAX_RECENT_ANSWERS:]}


# --------------------------------------------------------------------------- sessões de prova

def start_exam_session(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict],
                       exam_source_id: str, *, exam_name: str | None = None) -> dict:
    """Cria uma tentativa ``mode='exam'`` com as questões elegíveis da prova."""
    profile, resolved = _course(config, course_id, pool)
    subjects = set(profile["pesos"])
    items = sorted((item for item in pool if item["source_id"] == exam_source_id and item["subject"] in subjects),
                   key=lambda item: item["number"])
    if not items:
        raise ValueError("a prova não tem questões elegíveis (classificadas, revisadas e com gabarito) para este curso")
    attempt_id = uuid.uuid4().hex[:12]
    label = exam_label(exam_source_id, exam_name)
    # ``weights``/``incidence`` seguem o formato do plano do diagnóstico para que
    # ``calculate_metrics`` use os mesmos pesos do curso ao finalizar.
    plan = {"mode": "exam", "exam": exam_source_id, "exam_name": exam_name, "label": label,
            "weights": resolved,
            "incidence": mastery.node_incidence([item for item in pool if item["subject"] in subjects]),
            "distribution": {"selected": len(items),
                             "selected_by_subject": dict(Counter(item["subject"] for item in items))}}
    with db:
        db.execute("""INSERT INTO attempts(id,course_id,course_name,config_version,profile_snapshot,seed,
            requested_count,actual_count,status,plan_json,created_at,mode)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (attempt_id, course_id, profile["nome"], config["version"],
                    json.dumps(profile, ensure_ascii=False), 0, len(items), len(items), "in_progress",
                    json.dumps(plan, ensure_ascii=False), _now_iso(), "exam"))
        for ordinal, item in enumerate(items, 1):
            db.execute("""INSERT INTO attempt_questions(attempt_id,ordinal,source_id,question_number,
                subject,topic,question_json,correct_answer) VALUES(?,?,?,?,?,?,?,?)""",
                       (attempt_id, ordinal, item["source_id"], item["number"], item["subject"], item["topic"],
                        json.dumps(item["question"], ensure_ascii=False), item["correct_answer"]))
    return {"id": attempt_id, "questions": [{"ordinal": ordinal, "number": item["number"]}
                                            for ordinal, item in enumerate(items, 1)]}


def _exam_attempt(db: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
    row = db.execute("SELECT id, mode, status FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if not row:
        raise NotFound(f"sessão de prova não encontrada: {attempt_id}")
    if row["mode"] != "exam":
        raise ValueError("a tentativa não é uma sessão de prova")
    return row


def answer_exam_question(db: sqlite3.Connection, attempt_id: str, ordinal: int,
                         answer: str | None, reason: str | None = None, **extras) -> dict:
    """Registra uma resposta da prova; respostas incorretas exigem motivo.

    ``extras``: ``confidence``, ``elapsed_ms``, ``hints_used`` e ``note`` (``diagnostics.answer_extras``).
    """
    attempt = _exam_attempt(db, attempt_id)
    if attempt["status"] != "in_progress":
        raise ValueError("sessão de prova já finalizada")
    return diagnostics.record_response(db, attempt_id, int(ordinal), answer, reason or None, **extras)


def finish_exam_session(db: sqlite3.Connection, attempt_id: str) -> dict:
    """Finaliza a sessão (questões não respondidas ficam fora das métricas); sem LLM."""
    _exam_attempt(db, attempt_id)
    metrics = diagnostics.calculate_metrics(db, attempt_id)
    with db:
        db.execute("UPDATE attempts SET status='completed', metrics_json=? WHERE id=?",
                   (json.dumps(metrics, ensure_ascii=False), attempt_id))
    return {"id": attempt_id, "status": "completed", "metrics": metrics}
