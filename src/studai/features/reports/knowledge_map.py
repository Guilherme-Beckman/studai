"""Mapa do conhecimento (Fase 7): a taxonomia como grafo, com domínio e profundidade.

Implementa a seção "Mapa do conhecimento" de ``features/reports/CONTRACT.md``.
As respostas vêm de diagnósticos, provas e do estudo diário
(``study.course_responses``); a profundidade, de ``diagnostics.depth``.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone

from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, content_path
from studai.features.diagnostics import depth, mastery
from studai.features.reports.service import HALF_LIFE_DAYS, NotFound, _course, exam_label
from studai.features.study import service as study

MAX_RECENT_ANSWERS = 20
MAX_QUESTIONS = 10
PUBLIC_FIELDS = ("id", "name", "level", "parent_id", "subject", "attempts", "correct", "evidence", "mastery",
                 "lower", "upper", "status", "course_weight", "incidence", "priority", "coverage", "depth",
                 "children_total", "children_practiced")


def _state(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict], now: datetime | None) -> dict:
    profile, resolved = _course(config, course_id, pool, study.target_phase(db, course_id, datetime.now(timezone.utc)))
    course_pool = [item for item in pool if item.get("subject") in profile["pesos"]]
    responses = [depth.live_response(response) for response in study.course_responses(db, course_id)]
    nodes = depth.compute_depth(responses, subject_weights=resolved["weights"],
                                incidence=mastery.node_incidence(course_pool),
                                half_life_days=HALF_LIFE_DAYS, now=now or datetime.now(timezone.utc))
    questions: dict[str, list[dict]] = {}
    for item in course_pool:
        content_id = study.content_id_of(item["question"])
        for identity in CONTENT_INDEX[content_id]["path_ids"] if content_id else []:
            questions.setdefault(identity, []).append(item)
    return {"profile": profile, "weights": resolved["weights"], "nodes": nodes, "responses": responses,
            "questions": questions}


def _public(node: dict, questions: dict[str, list[dict]]) -> dict:
    return {**{key: node[key] for key in PUBLIC_FIELDS}, "questions": len(questions.get(node["id"], []))}


def graph(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict],
          now: datetime | None = None) -> dict:
    """Grafo do curso (``GET /api/knowledge-map``): nós da árvore viva e arestas pai → filho."""
    state = _state(db, config, course_id, pool, now)
    nodes, weights = state["nodes"], state["weights"]
    ordered = sorted(nodes.values(), key=lambda node: (len(node["path_ids"]), node["id"]))
    subjects = [node for node in ordered if node["level"] == "subject"]
    kpis = {"nodes": len(nodes),
            "practiced": sum(1 for node in nodes.values() if node["level"] != "subject" and node["evidence"] > 0),
            "depth": round(sum(weights.get(node["subject"], 0.0) * node["depth"] for node in subjects), 4),
            "depth_by_subject": {node["subject"]: node["depth"] for node in subjects}}
    return {"course": {"id": course_id, "name": state["profile"].get("nome", course_id), "weights": weights},
            "taxonomy_version": CONTENT_TAXONOMY_VERSION, "kpis": kpis,
            "nodes": [_public(node, state["questions"]) for node in ordered],
            "edges": [{"source": node["parent_id"], "target": node["id"]} for node in ordered
                      if node["parent_id"] in nodes]}


def node_detail(db: sqlite3.Connection, config: dict, course_id: str, node_id: str, pool: list[dict],
                now: datetime | None = None) -> dict:
    """Detalhe de um nó do mapa (``GET /api/knowledge-map/nodes/<id>``)."""
    state = _state(db, config, course_id, pool, now)
    nodes, questions = state["nodes"], state["questions"]
    if node_id not in nodes:
        raise NotFound(f"nó não encontrado: {node_id}")
    node = nodes[node_id]
    children = sorted((nodes[child] for child in depth.LIVE_CHILDREN.get(node_id, []) if child in nodes),
                      key=lambda child: (-child["incidence"], child["name"]))
    recent = []
    for response in state["responses"]:
        if node_id not in mastery.evidence_weights(response):
            continue
        recent.append({"at": response["answered_at"], "kind": response["kind"], "source_id": response["source_id"],
                       "number": response["number"], "exam_label": exam_label(response["source_id"]),
                       "correct": response["correct"], "selected": response["selected"],
                       "correct_answer": response["correct_answer"], "reason": response["reason"],
                       "content_path": content_path(response["principal_id"] or "")})
    recent.sort(key=lambda answer: answer["at"] or "")
    seen = Counter((answer["source_id"], answer["number"]) for answer in recent)
    under = sorted(questions.get(node_id, []), key=lambda item: ((item["source_id"], item["number"]) in seen,
                                                                item["source_id"], item["number"]))
    return {"node": {**_public(node, questions), "path": node["path"]},
            "ancestors": [identity for identity in node["path_ids"][:-1] if identity in nodes],
            "children": [_public(child, questions) for child in children],
            "recent_answers": recent[-MAX_RECENT_ANSWERS:],
            "questions": [{"source_id": item["source_id"], "number": item["number"],
                           "exam_label": exam_label(item["source_id"]), "subject": item["subject"],
                           "content_path": content_path(study.content_id_of(item["question"]) or ""),
                           "statement": " ".join(str(item["question"].get("enunciado") or "").split())[:180],
                           "answered": (item["source_id"], item["number"]) in seen}
                          for item in under[:MAX_QUESTIONS]]}
