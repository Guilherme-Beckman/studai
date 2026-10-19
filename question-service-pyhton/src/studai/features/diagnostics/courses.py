"""Pesos por curso/carreira FUVEST derivados da fórmula oficial da nota final.

Guia de Provas FUVEST 2027, p. 13: ``NF = (F1 + D1 + D2) / 3`` (carreiras sem
Competências Específicas), com cada parcela normalizada em base 100.

* F1 — 1ª fase, conhecimentos gerais; cada questão vale 1 ponto. A participação
  de cada matéria é estimada pela incidência histórica nas provas revisadas.
* D1 — 2º fase, dia 1: 50% questões discursivas de Português (divididas entre
  Língua Portuguesa e Literatura pela incidência) + 50% Redação.
* D2 — 2ª fase, dia 2: 12 questões das disciplinas específicas da carreira
  (Guia de Carreiras 2027), divididas igualmente.

A Redação entra no peso total mas não é avaliável pelo banco de questões de
múltipla escolha; ela é reportada separadamente em ``non_assessable``.
"""
from __future__ import annotations

from collections import Counter

FORMULAS = {"fuvest-2027"}
REDACAO = "Redação"
PORTUGUESE = ("Língua Portuguesa", "Literatura")


def subject_counts(pool: list[dict]) -> dict[str, int]:
    """Quantidade de questões revisadas por matéria (proxy da 1ª fase)."""
    return dict(Counter(item["subject"] for item in pool if item.get("subject")))


def derive_subject_weights(profile: dict, counts: dict[str, int]) -> dict:
    """Aplica a fórmula FUVEST ao perfil do curso.

    Retorna ``{"weights": {matéria: peso normalizado entre avaliáveis},
    "raw": {matéria ou Redação: participação na NF}, "components": {matéria:
    {"f1", "d1", "d2"}}, "non_assessable": {"Redação": participação}}``.
    """
    formula = profile.get("formula")
    if formula not in FORMULAS:
        raise ValueError(f"fórmula de pesos desconhecida: {formula!r}")
    subjects = list(profile.get("pesos") or {})
    if not subjects:
        raise ValueError("perfil sem lista de matérias (pesos)")
    second_phase = profile.get("fase2") or {}
    day_two = [s for s in second_phase.get("dia2", []) if s in subjects]
    if not day_two:
        raise ValueError("perfil precisa de fase2.dia2 com as disciplinas específicas")
    total = sum(counts.get(s, 0) for s in subjects)
    if total <= 0:
        raise ValueError("sem questões revisadas para estimar a 1ª fase")
    portuguese = [s for s in PORTUGUESE if s in subjects]
    portuguese_total = sum(counts.get(s, 0) for s in portuguese)
    components: dict[str, dict[str, float]] = {}
    for subject in subjects:
        f1 = counts.get(subject, 0) / total
        if subject in portuguese:
            share = counts.get(subject, 0) / portuguese_total if portuguese_total else 1 / len(portuguese)
            d1 = 0.5 * share
        else:
            d1 = 0.0
        d2 = 1 / len(day_two) if subject in day_two else 0.0
        components[subject] = {"f1": round(f1, 4), "d1": round(d1, 4), "d2": round(d2, 4)}
    raw = {s: (c["f1"] + c["d1"] + c["d2"]) / 3 for s, c in components.items()}
    raw[REDACAO] = 0.5 / 3
    assessable_total = sum(v for s, v in raw.items() if s != REDACAO)
    weights = {s: round(v / assessable_total, 4) for s, v in raw.items() if s != REDACAO and v > 0}
    return {"weights": weights, "raw": {s: round(v, 4) for s, v in raw.items()},
            "components": components, "non_assessable": {REDACAO: round(raw[REDACAO], 4)},
            "formula": formula, "first_phase_sample": total}


def resolve_weights(profile: dict, pool: list[dict]) -> dict:
    """Pesos efetivos do curso: fórmula FUVEST quando configurada, senão ``pesos`` manuais."""
    if profile.get("formula"):
        return derive_subject_weights(profile, subject_counts(pool))
    manual = {s: float(v) for s, v in profile["pesos"].items()}
    total = sum(manual.values())
    return {"weights": {s: round(v / total, 4) for s, v in manual.items()}, "raw": manual,
            "components": {}, "non_assessable": {}, "formula": None, "first_phase_sample": None}
