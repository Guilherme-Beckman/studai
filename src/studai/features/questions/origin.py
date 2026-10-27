"""Origem e dificuldade das questões do acervo.

As provas da FUVEST são a referência: só elas definem a incidência dos conteúdos, os
pesos da 1ª fase, os simulados, o diagnóstico e a projeção. Questões de outras provas,
de listas ou geradas pela IA servem para treinar e contam no domínio com peso menor,
conforme a dificuldade. Prova sem ``"origem"`` no JSON é FUVEST (acervo antigo).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FUVEST = "fuvest"
TYPES = {"fuvest": "FUVEST", "vestibular": "Outro vestibular", "lista": "Lista de exercícios",
         "ia": "Gerada por IA"}
# Provas completas que podem virar sessão de prova; listas e o banco da IA não.
FULL_EXAM_TYPES = {"fuvest", "vestibular"}
DIFFICULTIES = {1: "fácil", 2: "média", 3: "difícil"}
# Peso de uma resposta fora da FUVEST no domínio, pela dificuldade da questão.
EVIDENCE_FACTORS = {1: 0.3, 2: 0.6, 3: 0.8}
MAX_NAME_LENGTH = 120


def exam_origin(data: dict) -> dict:
    """Origem gravada no JSON da prova, normalizada (``{"tipo", "nome"}``)."""
    raw = data.get("origem") if isinstance(data.get("origem"), dict) else {}
    kind = raw.get("tipo") if raw.get("tipo") in TYPES else FUVEST
    return {"tipo": kind, "nome": str(raw.get("nome") or "").strip() or None}


def validate_origin(value: object) -> dict:
    """Origem enviada pela interface; o nome é obrigatório fora da FUVEST."""
    if not isinstance(value, dict):
        raise ValueError("origem deve ser um objeto {tipo, nome}")
    kind = str(value.get("tipo") or FUVEST)
    if kind not in TYPES:
        raise ValueError(f"tipo de origem inválido: use {', '.join(TYPES)}")
    name = " ".join(str(value.get("nome") or "").split()) or None
    if kind != FUVEST and not name:
        raise ValueError("informe o nome da prova ou lista (ex.: ENEM 2023 dia 2)")
    if name and len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"o nome da origem deve ter até {MAX_NAME_LENGTH} caracteres")
    return {"tipo": kind, "nome": name}


def write_exam_origin(exam_json: Path, origin: dict) -> dict:
    """Grava a origem (já validada) no JSON da prova, numa escrita atômica."""
    exam_json = Path(exam_json)
    data = json.loads(exam_json.read_text(encoding="utf-8"))
    data["origem"] = origin
    temporary = exam_json.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, exam_json)
    return exam_origin(data)


def validate_difficulty(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("dificuldade inválida")
    try:
        level = int(value)
    except (TypeError, ValueError):
        raise ValueError("dificuldade deve ser 1 (fácil), 2 (média) ou 3 (difícil)") from None
    if level not in DIFFICULTIES:
        raise ValueError("dificuldade deve ser 1 (fácil), 2 (média) ou 3 (difícil)")
    return level


def is_fuvest(item: dict) -> bool:
    """Item do pool ou resposta; sem origem conta como FUVEST."""
    return (item.get("origin") or FUVEST) == FUVEST


def evidence_factor(origin: str | None, difficulty: int | None) -> float:
    """Quanto uma resposta pesa no domínio: 1 na FUVEST, menos fora dela."""
    if (origin or FUVEST) == FUVEST:
        return 1.0
    return EVIDENCE_FACTORS.get(difficulty or 2, EVIDENCE_FACTORS[2])
