"""Profundidade por nó: o quanto um setor da taxonomia foi aprofundado.

O domínio Beta de ``mastery`` soma cada resposta em todos os ancestrais: vinte
acertos num único subconteúdo deixam a área inteira "forte". A profundidade
mede amplitude × qualidade, de baixo para cima. Funções puras.

Nó (saída): o Nó de ``mastery`` mais::

    {"coverage", "depth", "children_total", "children_practiced"}

* Confiança da evidência ``e``: ``conf(e) = e / (e + EVIDENCE_HALF_CREDIT)``.
* Folha: ``depth = mastery × conf(evidence)``; ``coverage`` é 1 com evidência, senão 0.
* Nó interno com filhos vivos ``C``, peso ``w_i = incidência_i + UNSEEN_CHILD_FLOOR ×
  média(incidência de C)`` (uniforme quando ninguém de ``C`` caiu na FUVEST):

  - ``coverage = Σ w_i·[evidence_i > 0] / Σ w_i``
  - ``depth = (Σ w_i·depth_i + w_self·q_self) / (Σ w_i + w_self)``

  ``q_self``/``w_self`` só existem quando há respostas rotuladas no próprio nó
  (rótulos anteriores ao refinamento): ``q_self = Beta(diretas) × conf(diretas)`` e
  ``w_self = média(w_i)``.

Todos os nós vivos das matérias do curso aparecem; sem resposta ficam ``untested``
com ``depth`` 0. IDs obsoletos nas respostas seguem ``replaced_by``.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from studai.features.classification.taxonomy import CONTENT_INDEX, DEFAULT_CONTENT_TAXONOMY, live_id
from studai.features.diagnostics import mastery

EVIDENCE_HALF_CREDIT = 2.0
UNSEEN_CHILD_FLOOR = 0.2
CONSOLIDATED_DEPTH = 0.6


def _live_children() -> dict[str, list[str]]:
    children: dict[str, list[str]] = {subject["id"]: [] for subject in DEFAULT_CONTENT_TAXONOMY["subjects"]}
    for identity, node in CONTENT_INDEX.items():
        if any(CONTENT_INDEX.get(part, {}).get("deprecated") for part in node["path_ids"][1:]):
            continue
        children.setdefault(identity, [])
        children.setdefault(node["path_ids"][-2], []).append(identity)
    for ids in children.values():
        ids.sort()
    return children


LIVE_CHILDREN = _live_children()


def content_ancestor(node_id: str) -> str:
    """Conteúdo (3º nível) que contém o nó; áreas e conteúdos devolvem a si mesmos."""
    node = CONTENT_INDEX.get(node_id)
    return node["path_ids"][2] if node and len(node["path_ids"]) > 3 else node_id


def confidence(evidence: float) -> float:
    return evidence / (evidence + EVIDENCE_HALF_CREDIT) if evidence > 0 else 0.0


def live_response(response: dict) -> dict:
    """Resposta com os ids obsoletos trocados pelo substituto (`replaced_by`)."""
    principal = response.get("principal_id")
    return {**response, "principal_id": live_id(principal) if principal else principal,
            "related_ids": [live_id(value) for value in response.get("related_ids") or [] if value]}


def direct_evidence(responses: list[dict], *, now: datetime,
                    half_life_days: float | None = None) -> dict[str, tuple[float, float]]:
    """Acertos/erros ponderados só no id exato rotulado (sem ancestrais)."""
    successes: Counter = Counter()
    failures: Counter = Counter()
    for response in responses:
        factor = (mastery.recency_factor(response.get("answered_at"), now, half_life_days)
                  * float(response.get("evidence_factor", 1.0)))
        labelled: dict[str, float] = {}
        for identity, weight in ([(response.get("principal_id"), mastery.PRINCIPAL_WEIGHT)]
                                 + [(value, mastery.RELATED_WEIGHT) for value in response.get("related_ids") or []]):
            if identity in CONTENT_INDEX:
                labelled[identity] = max(labelled.get(identity, 0.0), weight)
        for identity, weight in labelled.items():
            (successes if response.get("correct") else failures)[identity] += weight * factor
    return {identity: (successes[identity], failures[identity]) for identity in set(successes) | set(failures)}


def compute_depth(responses: list[dict], *, subject_weights: dict[str, float],
                  incidence: dict[str, float] | None = None, half_life_days: float | None = None,
                  now: datetime | None = None) -> dict[str, dict]:
    """Nós de ``mastery.compute_nodes`` para a árvore viva inteira, com profundidade."""
    incidence = incidence or {}
    now = now or datetime.now(timezone.utc)
    responses = [live_response(response) for response in responses]
    # Incidência 0 para todo nó vivo: ``compute_nodes`` passa a listar a árvore inteira.
    full_incidence = {identity: 0.0 for identity in LIVE_CHILDREN} | incidence
    nodes = mastery.compute_nodes(responses, subject_weights=subject_weights, incidence=full_incidence,
                                  half_life_days=half_life_days, now=now)
    direct = direct_evidence(responses, now=now, half_life_days=half_life_days)

    def visit(identity: str) -> None:
        node = nodes[identity]
        children = [child for child in LIVE_CHILDREN.get(identity, []) if child in nodes]
        for child in children:
            visit(child)
        if not children:
            node.update(coverage=1.0 if node["evidence"] > 0 else 0.0,
                        depth=round(node["mastery"] * confidence(node["evidence"]), 4),
                        children_total=0, children_practiced=0)
            return
        raw = [float(incidence.get(child, 0.0)) for child in children]
        mean = sum(raw) / len(raw)
        weights = [value + UNSEEN_CHILD_FLOOR * mean for value in raw] if mean > 0 else [1.0] * len(raw)
        practiced = [nodes[child]["evidence"] > 0 for child in children]
        total = sum(weights)
        weighted_depth = sum(w * nodes[child]["depth"] for w, child in zip(weights, children))
        successes, failures = direct.get(identity, (0.0, 0.0))
        if successes + failures > 0:
            self_weight = total / len(weights)
            quality = mastery.beta_summary(successes, failures)["mastery"] * confidence(successes + failures)
            weighted_depth += self_weight * quality
            total += self_weight
        node.update(coverage=round(sum(w for w, seen in zip(weights, practiced) if seen) / sum(weights), 4),
                    depth=round(weighted_depth / total, 4),
                    children_total=len(children), children_practiced=sum(practiced))

    for identity, node in nodes.items():
        if node["level"] == "subject":
            visit(identity)
    # Nós obsoletos que vieram das respostas não fazem parte da árvore viva.
    return {identity: node for identity, node in nodes.items() if "depth" in node}
