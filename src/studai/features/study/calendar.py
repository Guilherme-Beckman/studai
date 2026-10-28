"""Calendário de preparação: datas de prova, prontidão e o plano dia a dia até a prova.

O calendário não ensina conteúdo; ele aponta o que praticar com as questões do acervo.

* **Prontidão** vem da profundidade (``diagnostics.depth``): por matéria,
  ``min(1, depth / CONSOLIDATED_DEPTH)``; a geral é a média ponderada pelos pesos do curso.
* **Demanda**: cada conteúdo (3º nível) com incidência no acervo precisa de
  ``QUESTIONS_PER_CONTENT × (1 − depth/CONSOLIDATED_DEPTH)`` questões para consolidar.
  Lacunas vêm primeiro; depois peso da matéria × incidência × (1 − depth).
* **Carga**: questões por dia = demanda ÷ dias de treino restantes, dentro de
  ``[MIN_DAILY, MAX_DAILY]``. Se não cabe, os conteúdos de menor prioridade ficam de fora.
* **Agenda**: domingos são simulados (prova completa do acervo); nos últimos
  ``FINAL_STRETCH_DAYS`` dias há simulado a cada 3 dias, a véspera é revisão leve e o
  último dia é a prova. Os outros dias puxam a fila de demanda, alternando matérias por
  crédito de déficit (cada matéria ganha crédito proporcional ao que ainda falta).
* **Hoje fixo, futuro adapta**: o plano de hoje é gravado na primeira consulta do dia
  (``calendar_days``); os dias futuros são recalculados a cada consulta.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from studai.features.classification.taxonomy import CONTENT_INDEX, DEFAULT_SUBJECT_IDS
from studai.features.diagnostics import depth, mastery
from studai.features.reports.service import HALF_LIFE_DAYS, _course, exam_label
from studai.features.questions.origin import is_fuvest
from studai.features.study import projection
from studai.features.study import service as study

PHASES = {"fase1": "1ª fase", "fase2": "2ª fase", "outra": "Outra prova"}
QUESTIONS_PER_CONTENT = 6
MIN_DAILY = 10
MAX_DAILY = 60
FINAL_STRETCH_DAYS = 14
SIMULADO_WEEKDAY = 6  # domingo
MAX_SUBJECTS_PER_DAY = 2
REVIEW_CONTENTS = 3
MAX_HORIZON_DAYS = 730
SIMULADO_DONE_SHARE = 0.8
FIRST_PHASE_QUESTIONS = 90
# Disponibilidade por dia da semana: cada questão do plano conta ~4 min (3,3 de prova +
# conferir a resolução); simulado só em dia com pelo menos 3 h livres.
QUESTION_MINUTES = 4
MIN_SIMULADO_MINUTES = 180
MAX_DAY_MINUTES = 12 * 60


class ExamNotFound(FileNotFoundError):
    """Data de prova inexistente (a API traduz para 404)."""


# --------------------------------------------------------------------------- provas

def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError("data inválida: use o formato AAAA-MM-DD") from None


def _public_exam(row: sqlite3.Row | dict, today: date) -> dict:
    day = date.fromisoformat(row["exam_date"])
    return {"id": row["id"], "courseId": row["course_id"], "name": row["name"], "date": row["exam_date"],
            "phase": row["phase"], "phaseLabel": PHASES[row["phase"]], "daysLeft": (day - today).days,
            "cutoff": row["cutoff_correct"]}


def _cutoff(value: object, phase: str) -> int | None:
    """Nota de corte em acertos; na 1ª fase vai de 0 a 90."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("nota de corte inválida")
    try:
        cutoff = int(value)
    except (TypeError, ValueError):
        raise ValueError("a nota de corte deve ser um número inteiro de acertos") from None
    top = FIRST_PHASE_QUESTIONS if phase == "fase1" else 1000
    if not 0 <= cutoff <= top:
        raise ValueError(f"a nota de corte deve ficar entre 0 e {top} acertos")
    return cutoff


def set_exam_cutoff(db: sqlite3.Connection, exam_id: str, cutoff: object, today: date) -> dict:
    row = db.execute("SELECT * FROM exam_dates WHERE id=?", (exam_id,)).fetchone()
    if not row:
        raise ExamNotFound(f"prova não encontrada: {exam_id}")
    with db:
        db.execute("UPDATE exam_dates SET cutoff_correct=? WHERE id=?", (_cutoff(cutoff, row["phase"]), exam_id))
    return _public_exam(db.execute("SELECT * FROM exam_dates WHERE id=?", (exam_id,)).fetchone(), today)


def add_exam(db: sqlite3.Connection, course_id: str, name: str, exam_date: str, phase: str,
             now: datetime, cutoff: object = None) -> dict:
    """Cadastra a data de uma prova do curso; a data não pode estar no passado."""
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("informe o nome da prova")
    if phase not in PHASES:
        raise ValueError(f"fase inválida: use {', '.join(PHASES)}")
    day = _parse_day(exam_date)
    today = study.local_date(now)
    if day < today:
        raise ValueError("a data da prova já passou")
    if (day - today).days > MAX_HORIZON_DAYS:
        raise ValueError("a prova precisa estar nos próximos dois anos")
    cutoff = _cutoff(cutoff, phase)
    exam_id = uuid.uuid4().hex[:12]
    with db:
        db.execute("INSERT INTO exam_dates(id,course_id,name,exam_date,phase,created_at,cutoff_correct) "
                   "VALUES(?,?,?,?,?,?,?)", (exam_id, course_id, name, day.isoformat(), phase, study._iso(now), cutoff))
    return _public_exam(db.execute("SELECT * FROM exam_dates WHERE id=?", (exam_id,)).fetchone(), today)


def list_exams(db: sqlite3.Connection, course_id: str, today: date) -> list[dict]:
    """Provas do curso em ordem de data (passadas incluídas, com ``daysLeft`` negativo)."""
    return [_public_exam(row, today) for row in db.execute(
        "SELECT * FROM exam_dates WHERE course_id=? ORDER BY exam_date, created_at", (course_id,))]


def delete_exam(db: sqlite3.Connection, exam_id: str) -> dict:
    if not db.execute("SELECT 1 FROM exam_dates WHERE id=?", (exam_id,)).fetchone():
        raise ExamNotFound(f"prova não encontrada: {exam_id}")
    with db:
        db.execute("DELETE FROM exam_dates WHERE id=?", (exam_id,))
        db.execute("DELETE FROM calendar_days WHERE exam_id=?", (exam_id,))
    return {"ok": True, "id": exam_id}


def next_exam(exams: list[dict]) -> dict | None:
    """A prova mais próxima que ainda não passou (hoje conta)."""
    return next((exam for exam in exams if exam["daysLeft"] >= 0), None)


def exam_subjects(profile: dict, phase: str) -> list[str]:
    """Matérias cobradas: a 2ª fase usa ``fase2`` do curso; as demais, todas as matérias do curso."""
    subjects = list(profile.get("pesos") or {})
    if phase == "fase2":
        fase2 = profile.get("fase2") or {}
        specific = [subject for subject in (fase2.get("dia1") or []) + (fase2.get("dia2") or [])
                    if subject in subjects]
        if specific:
            return list(dict.fromkeys(specific))
    return subjects


# --------------------------------------------------------------------------- prontidão e demanda

def _scope_weights(weights: dict[str, float], subjects: list[str]) -> dict[str, float]:
    scoped = {subject: float(weights.get(subject, 0.0)) for subject in subjects if weights.get(subject, 0.0) > 0}
    total = sum(scoped.values())
    return {subject: weight / total for subject, weight in scoped.items()} if total else {}


def _is_content(node: dict) -> bool:
    return len(node["path_ids"]) == 3


def readiness(nodes: dict[str, dict], weights: dict[str, float]) -> dict:
    """Prontidão geral e por matéria (0–1), a partir da profundidade de ``depth.compute_depth``."""
    subjects = []
    for subject, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
        node = nodes.get(DEFAULT_SUBJECT_IDS.get(subject, ""))
        contents = [n for n in nodes.values() if n["subject"] == subject and _is_content(n) and n["incidence"] > 0]
        value = min(1.0, node["depth"] / depth.CONSOLIDATED_DEPTH) if node else 0.0
        subjects.append({"subject": subject, "weight": round(weight, 4), "readiness": round(value, 4),
                         "depth": node["depth"] if node else 0.0,
                         "coverage": node["coverage"] if node else 0.0,
                         "consolidated": sum(1 for n in contents if n["depth"] >= depth.CONSOLIDATED_DEPTH),
                         "contents": len(contents)})
    overall = sum(item["weight"] * item["readiness"] for item in subjects)
    return {"overall": round(overall, 4), "subjects": subjects}


def content_demand(nodes: dict[str, dict], weights: dict[str, float], gaps: set[str] = frozenset()) -> list[dict]:
    """Conteúdos ainda não consolidados, em ordem de prioridade, com as questões que faltam."""
    demand = []
    for node in nodes.values():
        if not _is_content(node) or node["subject"] not in weights or node["incidence"] <= 0:
            continue
        missing = max(0.0, 1 - node["depth"] / depth.CONSOLIDATED_DEPTH)
        questions = math.ceil(QUESTIONS_PER_CONTENT * missing - 1e-9)
        if questions <= 0:
            continue
        gap = node["status"] == "gap" or node["id"] in gaps
        demand.append({"id": node["id"], "name": node["name"], "subject": node["subject"],
                       "questions": questions, "gap": gap, "depth": node["depth"],
                       "priority": round(weights[node["subject"]] * node["incidence"] * (1 - node["depth"]), 6)})
    demand.sort(key=lambda item: (not item["gap"], -item["priority"], item["id"]))
    return demand


def _ensure_availability(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS study_availability (
        course_id TEXT NOT NULL, weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
        minutes INTEGER NOT NULL CHECK(minutes BETWEEN 0 AND 720), PRIMARY KEY(course_id, weekday))""")


def get_availability(db: sqlite3.Connection, course_id: str) -> dict[int, int] | None:
    """Minutos livres por dia da semana (0 = segunda); ``None`` se o aluno não informou."""
    _ensure_availability(db)
    rows = db.execute("SELECT weekday, minutes FROM study_availability WHERE course_id=?", (course_id,)).fetchall()
    return {int(row[0]): int(row[1]) for row in rows} if rows else None


def set_availability(db: sqlite3.Connection, course_id: str, minutes: dict | None) -> dict[int, int] | None:
    """Grava os minutos por dia da semana (``{"0": 60, ..., "6": 0}``); ``None`` volta ao plano sem limite."""
    _ensure_availability(db)
    if minutes is None:
        with db:
            db.execute("DELETE FROM study_availability WHERE course_id=?", (course_id,))
        return None
    if not isinstance(minutes, dict):
        raise ValueError("informe os minutos por dia da semana")
    values = {}
    for weekday in range(7):
        raw = minutes.get(str(weekday), minutes.get(weekday, 0))
        if isinstance(raw, bool):
            raise ValueError("minutos inválidos")
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            raise ValueError("os minutos devem ser números inteiros") from None
        if not 0 <= value <= MAX_DAY_MINUTES:
            raise ValueError(f"cada dia aceita de 0 a {MAX_DAY_MINUTES} minutos")
        values[weekday] = value
    if not any(values.values()):
        raise ValueError("informe tempo em pelo menos um dia da semana")
    with db:
        db.executemany("INSERT OR REPLACE INTO study_availability(course_id,weekday,minutes) VALUES(?,?,?)",
                       [(course_id, weekday, value) for weekday, value in values.items()])
    return values


def daily_load(total_demand: int, study_days: int) -> dict:
    """Questões por dia para cumprir a demanda até a prova, dentro de ``[MIN_DAILY, MAX_DAILY]``."""
    if study_days <= 0:
        return {"questionsPerDay": 0, "demand": total_demand, "studyDays": 0, "feasible": total_demand == 0}
    needed = math.ceil(total_demand / study_days)
    return {"questionsPerDay": min(MAX_DAILY, max(MIN_DAILY, needed)), "demand": total_demand,
            "studyDays": study_days, "feasible": needed <= MAX_DAILY}


# --------------------------------------------------------------------------- agenda

def _day_kind(day: date, exam_day: date) -> tuple[str, str]:
    """(tipo, fase) do dia: ``exam``, ``eve`` (véspera), ``simulado`` ou ``study``."""
    left = (exam_day - day).days
    phase = "reta_final" if left <= FINAL_STRETCH_DAYS else "construcao"
    if left == 0:
        return "exam", phase
    if left == 1:
        return "eve", phase
    if phase == "reta_final":
        return ("simulado" if left % 3 == 1 else "study"), phase
    return ("simulado" if day.weekday() == SIMULADO_WEEKDAY else "study"), phase


def _block(subject: str, parts: list[tuple[dict, int]], kind: str) -> dict:
    merged: dict[str, list] = {}
    for item, count in parts:
        merged.setdefault(item["id"], [item, 0])[1] += count
    contents = [(item, count) for item, count in merged.values()]
    return {"subject": subject, "kind": kind, "questions": sum(count for _, count in contents),
            "contentIds": [item["id"] for item, _ in contents],
            "contents": [{"id": item["id"], "name": item["name"], "questions": count} for item, count in contents]}


def build_schedule(today: date, exam_day: date, demand: list[dict], weights: dict[str, float],
                   review_pool: dict[str, list[dict]], simulados: list[dict],
                   availability: dict[int, int] | None = None) -> tuple[list[dict], dict]:
    """Dias de ``today`` até ``exam_day`` e a carga diária usada.

    ``review_pool`` lista, por matéria, os conteúdos mais fracos (para revisão quando
    a fila de demanda acaba); ``simulados`` são as provas sugeridas, em ordem.
    ``availability`` (minutos por dia da semana): dia sem tempo vira folga, simulado só
    cabe em dia com 3 h, e a carga de cada dia segue o tempo livre dele.
    """
    days = [today + timedelta(days=offset) for offset in range((exam_day - today).days + 1)]
    kinds = {day: _day_kind(day, exam_day) for day in days}
    if availability:
        for day in days:
            kind, phase = kinds[day]
            minutes = availability.get(day.weekday(), 0)
            if kind in ("study", "simulado") and minutes <= 0:
                kinds[day] = ("rest", phase)
            elif kind == "simulado" and minutes < MIN_SIMULADO_MINUTES:
                kinds[day] = ("study", phase)
    study_list = [day for day in days if kinds[day][0] == "study"]
    total_demand = sum(item["questions"] for item in demand)
    if availability:
        fits = {day: max(1, availability.get(day.weekday(), 0) // QUESTION_MINUTES) for day in study_list}
        total_fit = sum(fits.values())
        share = min(1.0, total_demand / total_fit) if total_fit else 0.0
        # Cada dia recebe a sua parte da demanda, sem passar do que cabe no tempo dele.
        targets = {day: min(fits[day], max(min(fits[day], MIN_DAILY), math.ceil(fits[day] * share)))
                   for day in study_list}
        load = {"questionsPerDay": round(sum(targets.values()) / len(targets)) if targets else 0,
                "demand": total_demand, "studyDays": len(study_list), "feasible": total_demand <= total_fit,
                "byAvailability": True}
    else:
        load = daily_load(total_demand, len(study_list))
        targets = {day: load["questionsPerDay"] or MIN_DAILY for day in study_list}
    per_day = load["questionsPerDay"] or MIN_DAILY
    capacity = sum(targets.values())
    # Sem tempo para tudo: fica de fora o fim da fila (menor prioridade).
    queues: dict[str, list[list]] = defaultdict(list)
    kept = 0
    for item in demand:
        take = min(item["questions"], capacity - kept)
        if take <= 0:
            break
        queues[item["subject"]].append([item, take])
        kept += take
    load["planned"] = kept
    load["contentsPending"] = len(demand)
    load["contentsPlanned"] = sum(len(queue) for queue in queues.values())
    credit = {subject: 0.0 for subject in weights}
    review_credit = {subject: 0.0 for subject in weights}
    review_turn: Counter = Counter()
    simulado_index = 0
    schedule = []

    def review_block(amount: int) -> dict | None:
        candidates = [subject for subject in weights if review_pool.get(subject)]
        if not candidates or amount <= 0:
            return None
        for subject in candidates:
            review_credit[subject] += weights[subject]
        subject = max(candidates, key=lambda s: (review_credit[s], weights[s], s))
        review_credit[subject] -= 1
        pool = review_pool[subject]
        start = review_turn[subject] * REVIEW_CONTENTS % len(pool)
        review_turn[subject] += 1
        chosen = (pool[start:] + pool[:start])[:REVIEW_CONTENTS]
        shares = [amount // len(chosen) + (1 if index < amount % len(chosen) else 0) for index in range(len(chosen))]
        return _block(subject, [(item, count) for item, count in zip(chosen, shares) if count > 0], "review")

    for day in days:
        kind, phase = kinds[day]
        entry = {"date": day.isoformat(), "kind": kind, "phase": phase, "daysLeft": (exam_day - day).days,
                 "blocks": [], "simulado": None}
        if kind == "simulado":
            if simulados:
                entry["simulado"] = simulados[simulado_index % len(simulados)]
                simulado_index += 1
            else:  # sem prova no acervo: o dia vira treino misto
                block = review_block(per_day)
                entry["blocks"] = [block] if block else []
        elif kind == "eve":
            amount = max(MIN_DAILY, per_day // 2)
            if availability:
                amount = min(amount, max(1, availability.get(day.weekday(), 0) // QUESTION_MINUTES))
            block = review_block(amount)
            entry["blocks"] = [block] if block else []
        elif kind == "study":
            remaining_total = sum(count for queue in queues.values() for _, count in queue)
            day_target = targets[day]
            left = day_target
            # Carga grande se divide entre duas matérias em vez de um dia inteiro numa só.
            cap = max(MIN_DAILY, math.ceil(day_target / MAX_SUBJECTS_PER_DAY))
            taken: dict[str, list[tuple[dict, int]]] = {}

            def take(subject: str, amount: int) -> None:
                nonlocal left
                while queues[subject] and amount > 0 and left > 0:
                    item = queues[subject][0]
                    count = min(item[1], amount, left)
                    taken[subject].append((item[0], count))
                    item[1] -= count
                    amount -= count
                    left -= count
                    if item[1] == 0:
                        queues[subject].pop(0)

            for _ in range(MAX_SUBJECTS_PER_DAY):
                active = [subject for subject in weights if queues[subject] and subject not in taken]
                if not active or left <= 0:
                    break
                if not taken:
                    for subject in active:
                        credit[subject] += sum(count for _, count in queues[subject]) / remaining_total
                subject = max(active, key=lambda s: (credit[s], weights[s], s))
                credit[subject] -= 1
                taken[subject] = []
                take(subject, cap)
            for subject in taken:  # sobrou espaço: as matérias do dia seguem na fila antes da revisão
                take(subject, left)
            entry["blocks"] = [_block(subject, items, "focus") for subject, items in taken.items()]
            block = review_block(left)
            if block:
                entry["blocks"].append(block)
        entry["target"] = (entry["simulado"]["questions"] if entry["simulado"]
                           else sum(block["questions"] for block in entry["blocks"]))
        schedule.append(entry)
    return schedule, load


def simulado_suggestions(db: sqlite3.Connection, pool: list[dict], course_id: str, subjects: list[str]) -> list[dict]:
    """Provas completas da FUVEST no acervo, ainda não respondidas primeiro e as mais recentes antes."""
    counts = Counter(item["source_id"] for item in pool if item["subject"] in subjects and is_fuvest(item))
    answered = set()
    for row in db.execute("SELECT plan_json FROM attempts WHERE course_id=? AND mode='exam'", (course_id,)):
        try:
            answered.add((json.loads(row[0] or "{}") or {}).get("exam"))
        except (json.JSONDecodeError, AttributeError):
            continue

    def year(source_id: str) -> int:
        match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", source_id)
        return int(match.group(1)) if match else 0

    ordered = sorted(counts, key=lambda source: (source in answered, -year(source), source))
    return [{"sourceId": source, "label": exam_label(source), "questions": counts[source],
             "answered": source in answered} for source in ordered]


# --------------------------------------------------------------------------- progresso

def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=study.LOCAL_TZ)
    return study._iso(start), study._iso(start + timedelta(days=1))


def day_activity(db: sqlite3.Connection, course_id: str, day: date) -> dict:
    """Questões respondidas no dia local: por matéria (estudo e provas) e só em provas completas."""
    start, end = _day_bounds(day)
    subjects: Counter = Counter()
    for row in db.execute("""SELECT sa.subject FROM study_answers sa JOIN study_sessions s ON s.id = sa.session_id
            WHERE s.course_id=? AND sa.answered_at>=? AND sa.answered_at<?""", (course_id, start, end)):
        subjects[row[0]] += 1
    exam = 0
    for row in db.execute("""SELECT aq.subject, a.mode FROM attempt_questions aq JOIN attempts a ON a.id = aq.attempt_id
            WHERE a.course_id=? AND aq.answered_at>=? AND aq.answered_at<?""", (course_id, start, end)):
        subjects[row[0]] += 1
        exam += row[1] == "exam"
    return {"subjects": dict(subjects), "exam": exam, "total": sum(subjects.values())}


def attach_progress(entry: dict, activity: dict, today: date) -> dict:
    """Marca o quanto do dia foi cumprido: ``done``, ``partial``, ``missed`` ou ``pending``.

    Vale qualquer questão da matéria no dia (bloco do calendário, tarefa diária ou treino livre).
    """
    by_subject = dict(activity["subjects"])
    done = 0
    for block in entry["blocks"]:
        block["done"] = min(block["questions"], by_subject.get(block["subject"], 0))
        by_subject[block["subject"]] = by_subject.get(block["subject"], 0) - block["done"]
        done += block["done"]
    if entry.get("simulado"):
        done = min(entry["target"], activity["exam"])
    entry["done"] = done
    entry["answered"] = activity["total"]
    day = date.fromisoformat(entry["date"])
    target = entry.get("target") or 0
    if entry["kind"] == "exam":
        entry["status"] = "exam"
    elif entry["kind"] == "rest":  # folga: só conta se estudou mesmo assim
        entry["status"] = "active" if activity["total"] else "rest"
    elif entry["kind"] == "past":  # dia anterior ao calendário: só mostra se houve estudo
        entry["status"] = "active" if activity["total"] else "idle"
    elif target and done >= (math.ceil(target * SIMULADO_DONE_SHARE) if entry.get("simulado") else target):
        entry["status"] = "done"
    elif day > today:
        entry["status"] = "pending"
    elif done > 0:
        entry["status"] = "partial"
    else:
        entry["status"] = "missed" if day < today else "pending"
    return entry


# --------------------------------------------------------------------------- agregados

def _summaries(days: list[dict], key) -> list[dict]:
    groups: dict[str, dict] = {}
    for entry in days:
        label = key(date.fromisoformat(entry["date"]))
        group = groups.setdefault(label, {"key": label, "start": entry["date"], "end": entry["date"],
                                          "questions": 0, "simulados": 0, "subjects": Counter(),
                                          "contents": {}})
        group["end"] = entry["date"]
        group["questions"] += entry.get("target") or 0
        group["simulados"] += bool(entry.get("simulado"))
        for block in entry["blocks"]:
            group["subjects"][block["subject"]] += block["questions"]
            if block["kind"] == "focus":
                for content in block["contents"]:
                    group["contents"].setdefault(content["id"], {**content, "subject": block["subject"],
                                                                  "questions": 0})
                    group["contents"][content["id"]]["questions"] += content["questions"]
    result = []
    for group in groups.values():
        subjects = [{"subject": subject, "questions": count} for subject, count in group["subjects"].most_common()]
        contents = sorted(group["contents"].values(), key=lambda item: -item["questions"])
        result.append({**group, "subjects": subjects, "contents": contents})
    return result


def _week_key(day: date) -> str:
    return (day - timedelta(days=day.weekday())).isoformat()


# --------------------------------------------------------------------------- caso de uso principal

def calendar_overview(db: sqlite3.Connection, pool: list[dict], config: dict, course_id: str,
                      now: datetime) -> dict:
    """Calendário do curso: provas, prontidão, carga diária, dias, semanas e meses até a próxima prova."""
    today = study.local_date(now)
    exams = list_exams(db, course_id, today)
    target = next_exam(exams)
    profile, resolved = _course(config, course_id, pool, target["phase"] if target else None)
    subjects = exam_subjects(profile, target["phase"] if target else "fase1")
    weights = _scope_weights(resolved["weights"], subjects)
    course_pool = [item for item in pool if item.get("subject") in profile["pesos"]]
    nodes = depth.compute_depth(study.course_responses(db, course_id), subject_weights=resolved["weights"],
                                incidence=mastery.node_incidence(course_pool), half_life_days=HALF_LIFE_DAYS,
                                now=now)
    payload = {"course": {"id": course_id, "name": profile.get("nome", course_id)}, "today": today.isoformat(),
               "exams": exams, "target": target, "subjects": list(weights),
               "readiness": readiness(nodes, weights), "load": None, "days": [], "weeks": [], "months": [],
               "availability": get_availability(db, course_id)}
    if target:
        payload["target"] = {**target, "subjects": list(weights)}
    if not target or target["phase"] == "fase1":
        # Acertos estimados na 1ª fase (cada questão vale 1 ponto: pesos F1).
        first_phase = resolved["weights"] if target else _course(config, course_id, pool, "fase1")[1]["weights"]
        payload["projection"] = projection.project(
            db, course_id, first_phase, now, date.fromisoformat(target["date"]) if target else None,
            target.get("cutoff") if target else None)
    if not target or not weights:
        return payload

    gaps = {depth.content_ancestor(node_id) for node_id in study._gap_nodes(db, course_id)}
    demand = content_demand(nodes, weights, gaps)
    review_pool: dict[str, list[dict]] = defaultdict(list)
    for node in sorted(nodes.values(), key=lambda n: (n["depth"], -n["incidence"], n["id"])):
        if _is_content(node) and node["subject"] in weights and node["incidence"] > 0:
            review_pool[node["subject"]].append({"id": node["id"], "name": node["name"]})
    simulados = simulado_suggestions(db, pool, course_id, subjects)
    schedule, load = build_schedule(today, date.fromisoformat(target["date"]), demand, weights,
                                    review_pool, simulados, payload["availability"])

    # Hoje fixo: a primeira consulta do dia grava o plano; mudar de prova-alvo regrava.
    key = today.isoformat()
    row = db.execute("SELECT * FROM calendar_days WHERE local_date=? AND course_id=?", (key, course_id)).fetchone()
    if row is None or row["exam_id"] != target["id"]:
        with db:
            db.execute("INSERT OR REPLACE INTO calendar_days(local_date,course_id,exam_id,plan_json,created_at) "
                       "VALUES(?,?,?,?,?)", (key, course_id, target["id"],
                                             json.dumps(schedule[0], ensure_ascii=False), study._iso(now)))
    else:
        schedule[0] = json.loads(row["plan_json"])
    for entry in schedule:
        entry["frozen"] = entry["date"] == key

    # Dias já passados desde o início da semana/mês, com o plano congelado quando existir.
    first = min(today.replace(day=1), today - timedelta(days=today.weekday()))
    frozen = {r["local_date"]: json.loads(r["plan_json"]) for r in db.execute(
        "SELECT local_date, plan_json FROM calendar_days WHERE course_id=? AND local_date>=? AND local_date<?",
        (course_id, first.isoformat(), key))}
    past = []
    for offset in range((today - first).days):
        day = first + timedelta(days=offset)
        entry = frozen.get(day.isoformat()) or {"date": day.isoformat(), "kind": "past", "phase": None,
                                                 "daysLeft": None, "blocks": [], "simulado": None, "target": 0}
        past.append({**entry, "frozen": day.isoformat() in frozen})
    for entry in past + schedule[:1]:
        attach_progress(entry, day_activity(db, course_id, date.fromisoformat(entry["date"])), today)
    for entry in schedule[1:]:
        entry.update(done=0, answered=0, status="exam" if entry["kind"] == "exam" else "pending")

    payload.update(load=load, days=past + schedule,
                   weeks=_summaries(schedule, _week_key),
                   months=_summaries(schedule, lambda day: day.strftime("%Y-%m")))
    return payload


def today_plan(db: sqlite3.Connection, pool: list[dict], config: dict, course_id: str, now: datetime) -> dict:
    """Só o dia de hoje do calendário (faixa "Plano de hoje" do Treinamento)."""
    overview = calendar_overview(db, pool, config, course_id, now)
    today = next((entry for entry in overview["days"] if entry["date"] == overview["today"]), None)
    return {"target": overview["target"], "today": today, "load": overview["load"]}

