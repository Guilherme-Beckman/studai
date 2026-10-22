"""Gera histórico sintético de um estudante (diagnósticos + provas completas) para testar relatórios.

Uso (sempre em um banco descartável; ``--db`` é obrigatório)::

    cp diagnostico/historico.sqlite3 /tmp/studai-sim.sqlite3   # traz os gabaritos importados
    PYTHONPATH=src .venv/bin/python -m studai.tools.simulate_student \\
        --db /tmp/studai-sim.sqlite3 --questions-dir simulados/processados \\
        --config diagnostico/cursos.json --course eng-comp-sao-carlos \\
        --sessions 6 --profile mat-forte-fis-fraca --seed 7

As sessões ficam espaçadas (padrão: 14 dias) e terminam hoje; ``created_at`` e
``answered_at`` são retroagidos diretamente no SQLite. O domínio por matéria do
estudante melhora linearmente do início ao fim do período.
"""
from __future__ import annotations

import argparse
import inspect
import json
import random
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from studai.features.diagnostics import service as diagnostics
from studai.features.reports import service as reports
from studai.settings import DEFAULT_COURSE_CONFIG, DEFAULT_QUESTIONS_DIR

DEFAULT_SKILL = (0.45, 0.7)
# Matéria → (habilidade inicial, habilidade final): probabilidade de acerto.
PROFILES: dict[str, dict[str, tuple[float, float]]] = {
    "mat-forte-fis-fraca": {"Matemática": (0.7, 0.9), "Física": (0.25, 0.65)},
    "equilibrado": {},
    "iniciante": {subject: (0.25, 0.55) for subject in (
        "Matemática", "Física", "Química", "Biologia", "Língua Portuguesa", "Literatura")},
}
WRONG_REASONS = ("unknown_content", "calc_error", "misread")
BLANK_RATE = 0.04


def skill(profile: str, subject: str, progress: float) -> float:
    start, end = PROFILES[profile].get(subject, DEFAULT_SKILL)
    return start + (end - start) * max(0.0, min(1.0, progress))


def _answer(rng: random.Random, row: dict, probability: float) -> tuple[str | None, str | None]:
    """Resposta simulada: ``(alternativa | None, motivo do erro | None)``."""
    if rng.random() < probability:
        return row["correct_answer"], None
    if rng.random() < BLANK_RATE:
        return None, None
    alternatives = sorted(json.loads(row["question_json"]).get("alternativas") or {})
    wrong = [letter for letter in alternatives if letter != row["correct_answer"]]
    if not wrong:
        return row["correct_answer"], None
    return rng.choice(wrong), rng.choice(WRONG_REASONS)


def _exam_slots(sessions: int, exams: int) -> set[int]:
    exams = max(0, min(exams, sessions))
    return {round((k + 1) * sessions / (exams + 1)) - 1 for k in range(exams)} if exams else set()


def _create_diagnostic(db, config, course_id, pool, *, total: int, seed: int) -> str:
    # Diagnóstico estático: todas as questões sorteadas de uma vez (o adaptativo
    # insere uma por vez e depende das respostas).
    kwargs = {"total": total, "seed": seed, "provider": None}
    if "mode" in inspect.signature(diagnostics.create_attempt).parameters:
        kwargs["mode"] = "static"
    return diagnostics.create_attempt(db, config, course_id, pool, **kwargs)["id"]


def _finish_diagnostic(db, attempt_id: str) -> None:
    parameters = inspect.signature(diagnostics.finalize_attempt).parameters
    kwargs = {"include_analysis": False} if "include_analysis" in parameters else {}
    diagnostics.finalize_attempt(db, attempt_id, None, **kwargs)


def simulate(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict], *,
             sessions: int = 6, exams: int = 2, profile: str = "mat-forte-fis-fraca", seed: int = 7,
             end: date | None = None, spacing_days: int = 14, per_diagnostic: int = 30) -> list[dict]:
    """Cria ``sessions`` tentativas retroagidas (``exams`` delas provas completas)."""
    if profile not in PROFILES:
        raise ValueError(f"perfil desconhecido: {profile}; use {', '.join(sorted(PROFILES))}")
    if sessions < 1:
        raise ValueError("sessions deve ser positivo")
    if not pool:
        raise ValueError("pool vazio: use um banco com gabaritos importados e provas revisadas")
    rng = random.Random(seed)
    end = end or datetime.now(timezone.utc).date()
    exam_ids = sorted({item["source_id"] for item in pool})
    rng.shuffle(exam_ids)
    slots = _exam_slots(sessions, exams if exam_ids else 0)
    created = []
    for index in range(sessions):
        progress = index / (sessions - 1) if sessions > 1 else 1.0
        day = end - timedelta(days=spacing_days * (sessions - 1 - index))
        start = datetime.combine(day, time(13, 0), tzinfo=timezone.utc)
        if index in slots:
            exam_id = exam_ids[len([s for s in slots if s < index]) % len(exam_ids)]
            attempt_id = reports.start_exam_session(db, config, course_id, pool, exam_id)["id"]
        else:
            attempt_id = _create_diagnostic(db, config, course_id, pool, total=per_diagnostic,
                                            seed=rng.randrange(1 << 31))
        rows = diagnostics.list_questions_for_attempt(db, attempt_id)
        for row in rows:
            answer, reason = _answer(rng, row, skill(profile, row["subject"], progress))
            if index in slots:
                # Mantém o mesmo contrato dos fluxos reais: erros sempre têm motivo.
                reports.answer_exam_question(db, attempt_id, row["ordinal"], answer, reason)
            else:
                diagnostics.record_response(db, attempt_id, row["ordinal"], answer, reason)
        if index in slots:
            reports.finish_exam_session(db, attempt_id)
        else:
            _finish_diagnostic(db, attempt_id)
        with db:
            db.execute("UPDATE attempts SET created_at=? WHERE id=?", (start.isoformat(), attempt_id))
            for row in rows:
                moment = start + timedelta(minutes=2 * row["ordinal"])
                db.execute("UPDATE attempt_questions SET answered_at=? WHERE attempt_id=? AND ordinal=?",
                           (moment.isoformat(), attempt_id, row["ordinal"]))
        created.append({"id": attempt_id, "kind": "exam" if index in slots else "diagnostic",
                        "date": day.isoformat(), "questions": len(rows)})
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True,
                        help="banco SQLite de destino (use uma cópia; nunca o histórico real)")
    parser.add_argument("--questions-dir", type=Path, default=DEFAULT_QUESTIONS_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_COURSE_CONFIG)
    parser.add_argument("--course", default=reports.DEFAULT_COURSE_ID)
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--exams", type=int, default=2, help="quantas sessões são provas completas")
    parser.add_argument("--profile", default="mat-forte-fis-fraca", choices=sorted(PROFILES))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--spacing-days", type=int, default=14)
    parser.add_argument("--per-diagnostic", type=int, default=30)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None, help="padrão: hoje (UTC)")
    args = parser.parse_args(argv)
    config = diagnostics.load_course_config(args.config)
    profile = diagnostics.course_profile(config, args.course)
    db = diagnostics.connect_db(args.db)
    try:
        pool, excluded = diagnostics.load_question_pool(args.questions_dir, db, list(profile["pesos"]))
        created = simulate(db, config, args.course, pool, sessions=args.sessions, exams=args.exams,
                           profile=args.profile, seed=args.seed, end=args.end_date,
                           spacing_days=args.spacing_days, per_diagnostic=args.per_diagnostic)
    finally:
        db.close()
    print(f"pool: {len(pool)} questões elegíveis (excluídas: {excluded})")
    for item in created:
        print(f"{item['date']}  {item['kind']:<10}  {item['id']}  {item['questions']} questões")
    print(f"banco: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
