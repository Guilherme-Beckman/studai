"""Primeiros passos: o que ainda falta configurar para o app planejar bem o estudo."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from studai.features.study import calendar, projection, readings
from studai.features.study import service as study


def setup_steps(db: sqlite3.Connection, course_id: str, now: datetime) -> dict:
    """Passos em ordem, cada um com ``done`` e a aba (``view``) onde se resolve."""
    today = study.local_date(now)
    target = calendar.next_exam(calendar.list_exams(db, course_id, today))
    diagnosed = db.execute("SELECT 1 FROM attempts WHERE course_id=? AND mode!='exam' AND status!='in_progress' "
                           "LIMIT 1", (course_id,)).fetchone() is not None
    answered = len(study.course_responses(db, course_id))
    readings.ensure_schema(db)
    reading = db.execute("SELECT 1 FROM reading_progress WHERE status!='not_started' LIMIT 1").fetchone() is not None
    steps = [
        {"id": "exam", "title": "Cadastre a data da sua prova", "view": "calendar", "done": target is not None,
         "hint": "O plano dia a dia, a carga diária e os simulados miram a próxima prova."},
        {"id": "cutoff", "title": "Informe a nota de corte", "view": "calendar",
         "done": bool(target and target.get("cutoff") is not None),
         "hint": "Os simulados e a projeção comparam seus acertos com o corte do curso."},
        {"id": "availability", "title": "Diga quanto tempo você tem por dia", "view": "calendar",
         "done": calendar.get_availability(db, course_id) is not None,
         "hint": "Dias sem tempo viram folga e a carga de cada dia cabe no seu tempo."},
        {"id": "diagnostic", "title": "Faça um diagnóstico", "view": "diagnostics", "done": diagnosed,
         "hint": "Mede o que você já sabe e aponta as lacunas para o plano começar por elas."},
        {"id": "answers", "title": f"Responda {projection.MIN_RESPONSES} questões", "view": "training",
         "done": answered >= projection.MIN_RESPONSES, "progress": min(answered, projection.MIN_RESPONSES),
         "goal": projection.MIN_RESPONSES,
         "hint": "Com isso aparece a estimativa de acertos na 1ª fase."},
        {"id": "readings", "title": "Marque como está nas leituras obrigatórias", "view": "readings",
         "done": reading, "hint": "Cada obra ganha um prazo para ler até a prova."},
    ]
    return {"steps": steps, "done": sum(1 for step in steps if step["done"]), "total": len(steps)}
