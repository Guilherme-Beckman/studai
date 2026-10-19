"""Domínio estimado por nó da taxonomia (matéria → área → conteúdo → folha).

Contrato compartilhado entre o diagnóstico, os relatórios de evolução e o
frontend. Funções puras: recebem respostas já normalizadas e devolvem nós.

Resposta (entrada)::

    {"correct": bool, "subject": "Matemática", "principal_id": "matematica.content.x" | None,
     "related_ids": [...], "answered_at": "ISO-8601 UTC" | None}

Nó (saída)::

    {"id", "name", "level": "subject"|"area"|"content"|"leaf", "path": [nomes],
     "path_ids": [ids], "parent_id", "subject", "attempts", "correct",
     "evidence", "mastery", "lower", "upper", "status", "course_weight",
     "incidence", "priority"}

* ``evidence``: soma ponderada das respostas (principal 1.0, relacionado 0.5,
  multiplicada pelo decaimento por recência quando ``half_life_days`` é dado).
* ``mastery``: média da posterior Beta(1 + acertos, 1 + erros) ponderada.
* ``lower``/``upper``: intervalo de 80% (aproximação normal, limitado a [0, 1]).
* ``status``: ``strong`` | ``gap`` | ``uncertain`` | ``untested``.
* ``course_weight``: peso normalizado da matéria no curso (soma 1 entre matérias).
* ``incidence``: participação histórica do nó dentro da sua matéria (0–1).
* ``priority``: ``course_weight × incidence × (1 − mastery) × (1 + largura do IC)``.

A matéria usa como id o ``subject_id`` da taxonomia (ex.: ``matematica``),
pois matérias não fazem parte de ``CONTENT_INDEX``.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone

from studai.features.classification.taxonomy import CONTENT_INDEX, DEFAULT_SUBJECT_IDS

PRINCIPAL_WEIGHT = 1.0
RELATED_WEIGHT = 0.5
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0
Z80 = 1.2816
LEVELS = {1: "subject", 2: "area", 3: "content"}
SUBJECT_NAMES = {identity: name for name, identity in DEFAULT_SUBJECT_IDS.items()}


def node_info(node_id: str) -> dict | None:
    """Metadados estáveis de um nó (matérias incluídas)."""
    if node_id in SUBJECT_NAMES:
        name = SUBJECT_NAMES[node_id]
        return {"id": node_id, "name": name, "level": "subject", "path": [name],
                "path_ids": [node_id], "parent_id": None, "subject": name}
    node = CONTENT_INDEX.get(node_id)
    if not node:
        return None
    depth = len(node["path_ids"])
    level = "leaf" if depth >= 4 or (depth == 3 and node["is_leaf"]) else LEVELS.get(depth, "content")
    return {"id": node_id, "name": node["name"], "level": level, "path": list(node["path"]),
            "path_ids": list(node["path_ids"]), "parent_id": node["path_ids"][-2],
            "subject": node["subject"]}


def response_from_row(row: dict) -> dict:
    """Converte uma linha de ``attempt_questions`` (já respondida) em resposta."""
    question = json.loads(row["question_json"]) if isinstance(row.get("question_json"), str) else (row.get("question_json") or {})
    classification = question.get("classificacao") or {}
    return {"correct": bool(row.get("is_correct")), "subject": row["subject"],
            "principal_id": classification.get("conteudo_principal_id"),
            "related_ids": [value for value in (classification.get("conteudos_relacionados_ids") or []) if value],
            "answered_at": row.get("answered_at")}


def evidence_weights(response: dict) -> dict[str, float]:
    """Peso de evidência que uma resposta dá a cada nó (com ancestrais)."""
    weights: dict[str, float] = {}
    subject_id = DEFAULT_SUBJECT_IDS.get(response.get("subject"))
    if subject_id:
        weights[subject_id] = PRINCIPAL_WEIGHT
    for content_id, weight in ([(response.get("principal_id"), PRINCIPAL_WEIGHT)]
                               + [(value, RELATED_WEIGHT) for value in response.get("related_ids") or []]):
        node = CONTENT_INDEX.get(content_id or "")
        if not node:
            continue
        for identity in node["path_ids"]:
            weights[identity] = max(weights.get(identity, 0.0), weight)
    return weights


def recency_factor(answered_at: str | None, now: datetime, half_life_days: float | None) -> float:
    if not half_life_days or not answered_at:
        return 1.0
    try:
        moment = datetime.fromisoformat(answered_at)
    except ValueError:
        return 1.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - moment).total_seconds() / 86400)
    return 0.5 ** (age_days / half_life_days)


def beta_summary(successes: float, failures: float) -> dict:
    alpha, beta = PRIOR_ALPHA + successes, PRIOR_BETA + failures
    total = alpha + beta
    mean = alpha / total
    sd = math.sqrt(alpha * beta / (total * total * (total + 1)))
    return {"mastery": round(mean, 4), "lower": round(max(0.0, mean - Z80 * sd), 4),
            "upper": round(min(1.0, mean + Z80 * sd), 4)}


def classify(evidence: float, summary: dict) -> str:
    if evidence <= 0:
        return "untested"
    if evidence >= 3 and summary["mastery"] >= 0.7 and summary["lower"] >= 0.55:
        return "strong"
    if summary["upper"] <= 0.55 or (evidence >= 3 and summary["mastery"] < 0.5):
        return "gap"
    return "uncertain"


def normalize_weights(subject_weights: dict[str, float]) -> dict[str, float]:
    total = sum(float(v) for v in subject_weights.values()) or 1.0
    return {subject: float(v) / total for subject, v in subject_weights.items()}


def node_incidence(questions: list[dict]) -> dict[str, float]:
    """Participação de cada nó dentro da matéria, a partir de questões FUVEST revisadas.

    ``questions`` são itens do pool (``{"subject", "question": {"classificacao": ...}}``)
    ou respostas normalizadas. Matérias recebem 1.0.
    """
    per_node: Counter = Counter()
    per_subject: Counter = Counter()
    for item in questions:
        if "principal_id" in item:
            principal, subject = item.get("principal_id"), item.get("subject")
        else:
            classification = (item.get("question") or {}).get("classificacao") or {}
            principal, subject = classification.get("conteudo_principal_id"), item.get("subject")
        node = CONTENT_INDEX.get(principal or "")
        if not subject or not node:
            continue
        per_subject[subject] += 1
        for identity in node["path_ids"][1:]:
            per_node[identity] += 1
    incidence = {DEFAULT_SUBJECT_IDS[s]: 1.0 for s in per_subject if s in DEFAULT_SUBJECT_IDS}
    for identity, count in per_node.items():
        subject = CONTENT_INDEX[identity]["subject"]
        incidence[identity] = round(count / per_subject[subject], 4)
    return incidence


def compute_nodes(responses: list[dict], *, subject_weights: dict[str, float],
                  incidence: dict[str, float] | None = None, half_life_days: float | None = None,
                  now: datetime | None = None) -> dict[str, dict]:
    """Agrega respostas em nós com domínio, incerteza, status e prioridade.

    Nós sem resposta aparecem como ``untested`` quando têm incidência histórica
    (para que lacunas não testadas de alto peso fiquem visíveis).
    """
    incidence = incidence or {}
    now = now or datetime.now(timezone.utc)
    weights = normalize_weights(subject_weights)
    successes: Counter = Counter()
    failures: Counter = Counter()
    attempts: Counter = Counter()
    correct: Counter = Counter()
    for response in responses:
        factor = recency_factor(response.get("answered_at"), now, half_life_days)
        for identity, weight in evidence_weights(response).items():
            attempts[identity] += 1
            if response.get("correct"):
                correct[identity] += 1
                successes[identity] += weight * factor
            else:
                failures[identity] += weight * factor
    nodes: dict[str, dict] = {}
    for identity in set(attempts) | set(incidence):
        info = node_info(identity)
        if not info or info["subject"] not in weights:
            continue
        evidence = successes[identity] + failures[identity]
        summary = beta_summary(successes[identity], failures[identity])
        node_incidence_value = 1.0 if info["level"] == "subject" else float(incidence.get(identity, 0.0))
        course_weight = weights[info["subject"]]
        width = summary["upper"] - summary["lower"]
        priority = course_weight * node_incidence_value * (1 - summary["mastery"]) * (1 + width)
        nodes[identity] = {**info, "attempts": attempts[identity], "correct": correct[identity],
                           "evidence": round(evidence, 4), **summary,
                           "status": classify(evidence, summary),
                           "course_weight": round(course_weight, 4),
                           "incidence": round(node_incidence_value, 4),
                           "priority": round(priority, 6)}
    return nodes


def weighted_score(nodes: dict[str, dict], subject_weights: dict[str, float]) -> float:
    """Nota estimada (0–1) ponderada pelo curso, usando o domínio de cada matéria.

    Matérias sem resposta entram com a média da priori (0.5).
    """
    weights = normalize_weights(subject_weights)
    score = 0.0
    for subject, weight in weights.items():
        node = nodes.get(DEFAULT_SUBJECT_IDS.get(subject, ""))
        score += weight * (node["mastery"] if node else PRIOR_ALPHA / (PRIOR_ALPHA + PRIOR_BETA))
    return round(score, 4)
