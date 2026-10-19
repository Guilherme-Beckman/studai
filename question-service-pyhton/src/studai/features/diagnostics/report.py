"""Relatório determinístico do diagnóstico a partir dos nós de domínio.

Função pura: recebe os nós de ``mastery.compute_nodes``, o resultado de
pesos do curso (``courses.resolve_weights``) e o perfil, e devolve::

    {"course", "estimated_score", "coverage", "strengths", "gaps", "priorities",
     "untested_high_priority", "non_assessable"}

* ``estimated_score``: nota estimada 0–1 (``mastery.weighted_score``), só da
  parte avaliável por questões objetivas.
* ``coverage``: fração (0–1) da incidência ponderada das áreas que já tem
  alguma evidência.
* ``strengths``/``gaps``: áreas e conteúdos com status ``strong``/``gap``,
  ordenados por ``course_weight × incidence`` (até 10).
* ``priorities``: até 10 nós (exceto matérias) testados e não fortes, por
  ``priority``, com ``reason`` e ``exit_criterion`` em português.
* ``untested_high_priority``: até 5 áreas/conteúdos sem resposta, por ``priority``,
  também com ``reason`` e ``exit_criterion``.
* ``non_assessable``: ``{"Redação": peso na NF, "note": texto}`` quando o
  curso usa a fórmula FUVEST; ``{}`` caso contrário.

Itens de nó: ``{"id", "name", "level", "subject", "path" (lista de nomes),
"path_ids", "status", "mastery", "lower", "upper", "attempts", "correct", "course_weight",
"incidence", "priority"}``.
"""
from __future__ import annotations

from studai.features.diagnostics import mastery

REDACAO = "Redação"
REDACAO_NOTE = "Redação vale 1/6 da nota final e não é avaliada por questões de múltipla escolha."
NODE_FIELDS = ("id", "name", "level", "subject", "path", "path_ids", "status", "mastery", "lower", "upper",
               "attempts", "correct", "course_weight", "incidence", "priority")
MAX_PRIORITIES = 10
MAX_LISTED = 10
MAX_UNTESTED = 5


def _pct(value: float) -> str:
    return f"{round(float(value) * 100)}%"


def _relevance(node: dict) -> float:
    return node["course_weight"] * node["incidence"]


def _item(node: dict) -> dict:
    return {key: node.get(key) for key in NODE_FIELDS}


def _context(node: dict) -> str:
    return (f"O tema responde por {_pct(node['incidence'])} das questões de {node['subject']}, "
            f"matéria com peso {_pct(node['course_weight'])} na parte objetiva do curso.")


def reason_text(node: dict) -> str:
    """Justificativa curta, em português, para estudar o nó."""
    name = node["name"]
    if node["status"] == "untested":
        return f"{name} ainda não foi testado. {_context(node)}"
    noun = "questão" if node["attempts"] == 1 else "questões"
    result = f"{node['correct']} de {node['attempts']} {noun} envolvendo {name}"
    if node["status"] == "gap":
        head = f"Você acertou {result} (domínio estimado de {_pct(node['mastery'])})."
    else:
        head = (f"Resultado ainda inconclusivo: acertou {result} "
                f"(domínio entre {_pct(node['lower'])} e {_pct(node['upper'])}).")
    return f"{head} {_context(node)}"


def exit_criterion(node: dict) -> str:
    """Critério mensurável para considerar o nó resolvido."""
    if node["level"] == "area":
        return f"acertar pelo menos 8 de 10 questões de {node['name']}"
    return f"acertar pelo menos 4 de 5 questões de {node['name']}"


def _with_text(node: dict) -> dict:
    return {**_item(node), "reason": reason_text(node), "exit_criterion": exit_criterion(node)}


def coverage(nodes: dict[str, dict]) -> float:
    """Fração da incidência ponderada (peso × incidência) das áreas com alguma evidência."""
    areas = [node for node in nodes.values() if node["level"] == "area"]
    total = sum(_relevance(node) for node in areas)
    if total <= 0:
        return 0.0
    tested = sum(_relevance(node) for node in areas if node["evidence"] > 0)
    return round(tested / total, 4)


def build_report(nodes: dict[str, dict], weights_result: dict, profile: dict) -> dict:
    weights = weights_result.get("weights") or {}
    ranked = sorted(nodes.values(), key=lambda node: (-_relevance(node), node["id"]))
    mid_level = [node for node in ranked if node["level"] in ("area", "content")]
    tested = [node for node in nodes.values()
              if node["level"] != "subject" and node["evidence"] > 0 and node["status"] != "strong"]
    priorities = sorted(tested, key=lambda node: (-node["priority"], node["id"]))[:MAX_PRIORITIES]
    untested = sorted((node for node in mid_level if node["status"] == "untested"),
                      key=lambda node: (-node["priority"], node["id"]))[:MAX_UNTESTED]
    redacao = (weights_result.get("non_assessable") or {}).get(REDACAO)
    return {
        "course": profile.get("nome"),
        "estimated_score": mastery.weighted_score(nodes, weights) if weights else None,
        "coverage": coverage(nodes),
        "strengths": [_item(node) for node in mid_level if node["status"] == "strong"][:MAX_LISTED],
        "gaps": [_item(node) for node in mid_level if node["status"] == "gap"][:MAX_LISTED],
        "priorities": [_with_text(node) for node in priorities],
        "untested_high_priority": [_with_text(node) for node in untested],
        "non_assessable": {REDACAO: redacao, "note": REDACAO_NOTE} if redacao is not None else {},
    }
