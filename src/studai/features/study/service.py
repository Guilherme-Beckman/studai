"""Revisão espaçada e montagem do "O que estudar hoje"."""
from __future__ import annotations

import json
import random
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from studai.features.classification.taxonomy import CONTENT_INDEX, aggregate_content_ids, live_id
from studai.features.diagnostics import depth, mastery
from studai.features.diagnostics import service as diagnostics
from studai.features.diagnostics.courses import resolve_weights
from studai.features.explanations import service as explanations
from studai.features.questions.origin import evidence_factor, is_fuvest
from studai.features.study import ladder, scheduler

STUDY_SCHEMA_VERSION = 4
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
GAP_ACCURACY = 0.7
HALF_LIFE_DAYS = 30
REASONS = ("overdue_question", "ladder", "due_content", "new_gap", "new_content", "followup_practice")
# Cada questão da sessão pertence a uma frente do Treinamento, respondida em separado:
# a tarefa do dia (plano e blocos do calendário), as trilhas e o treino livre.
GROUPS = ("daily", "ladder", "free")


def item_group(item: dict) -> str:
    """Grupo da questão; sessões antigas, sem o campo, deduzem pelo motivo e pelo id."""
    if item.get("group") in GROUPS:
        return item["group"]
    if item.get("reason") == "ladder":
        return "ladder"
    return "daily" if str(item.get("id", "")).startswith("i") else "free"
# O motivo do erro decide o próximo passo: em quantos dias a questão volta...
REVIEW_DELAY_DAYS = {"concept_confusion": 3, "misread": 3, "trap": 7}
# ...e quantas questões do mesmo subconteúdo entram na sessão, e onde (posições depois da atual).
# "Não sabia o conteúdo" abre a trilha por nível (``ladder``) em vez disso.
FOLLOWUPS = {"calc_error": (1, 3)}


def connect_db(path: Path) -> sqlite3.Connection:
    """Abre o banco do diagnóstico e garante as tabelas do estudo diário."""
    db = diagnostics.connect_db(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS review_cards (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('content','question')),
            content_id TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '',
            question_number INTEGER NOT NULL DEFAULT 0, subject TEXT NOT NULL,
            card_json TEXT NOT NULL, due_at TEXT NOT NULL, last_review_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(kind, content_id, source_id, question_number)
        );
        CREATE INDEX IF NOT EXISTS review_cards_due ON review_cards(due_at);
        CREATE TABLE IF NOT EXISTS review_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL REFERENCES review_cards(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL, reviewed_at TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin IN ('diagnostic','daily')),
            source_ref TEXT NOT NULL UNIQUE, log_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS study_sessions (
            id TEXT PRIMARY KEY, local_date TEXT NOT NULL, course_id TEXT NOT NULL,
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(local_date, course_id)
        );
        CREATE TABLE IF NOT EXISTS study_answers (
            session_id TEXT NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
            item_id TEXT NOT NULL, source_id TEXT NOT NULL, question_number INTEGER NOT NULL,
            subject TEXT NOT NULL, content_id TEXT NOT NULL, selected_answer TEXT,
            correct_answer TEXT NOT NULL, is_correct INTEGER NOT NULL, error_reason TEXT,
            answered_at TEXT NOT NULL,
            PRIMARY KEY(session_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS exam_dates (
            id TEXT PRIMARY KEY, course_id TEXT NOT NULL, name TEXT NOT NULL,
            exam_date TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('fase1','fase2','outra')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calendar_days (
            local_date TEXT NOT NULL, course_id TEXT NOT NULL, exam_id TEXT NOT NULL,
            plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(local_date, course_id)
        );
    """)
    diagnostics.add_missing_columns(db, "study_answers", diagnostics.ANSWER_EXTRA_COLUMNS)
    # v4: origem e dificuldade da questão, para o peso da resposta no domínio.
    diagnostics.add_missing_columns(db, "study_answers", (("origin", "TEXT"), ("difficulty", "INTEGER")))
    # v3: nota de corte da prova, em número de acertos.
    diagnostics.add_missing_columns(db, "exam_dates", (("cutoff_correct", "INTEGER"),))
    ladder.ensure_schema(db)
    old = db.execute("SELECT value FROM metadata WHERE key='study_schema_version'").fetchone()
    if old and int(old[0]) > STUDY_SCHEMA_VERSION:
        db.close()
        raise ValueError("tabelas de estudo foram criadas por uma versão mais nova")
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('study_schema_version',?)",
               (str(STUDY_SCHEMA_VERSION),))
    db.commit()
    return db


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_date(now: datetime) -> date:
    return now.astimezone(LOCAL_TZ).date()


def end_of_local_day(now: datetime) -> datetime:
    """Último instante do dia local; o que vence ainda hoje entra na sessão."""
    day = local_date(now)
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=LOCAL_TZ) - timedelta(microseconds=1)


def content_id_of(question: dict) -> str | None:
    """Nó da taxonomia onde fica o cartão da questão (conteúdo principal, sem os ancestrais)."""
    raw = (question.get("classificacao") or {}).get("conteudo_principal_id")
    content_id = live_id(str(raw)) if raw else None
    return content_id if content_id in CONTENT_INDEX else None


def _card(db: sqlite3.Connection, kind: str, content_id: str, subject: str, now: datetime,
          source_id: str = "", number: int = 0) -> sqlite3.Row:
    row = db.execute("SELECT * FROM review_cards WHERE kind=? AND content_id=? AND source_id=? "
                     "AND question_number=?", (kind, content_id, source_id, number)).fetchone()
    if row:
        return row
    card = scheduler.new_card(now)
    db.execute("""INSERT INTO review_cards(id,kind,content_id,source_id,question_number,subject,
        card_json,due_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
               (uuid.uuid4().hex[:16], kind, content_id, source_id, number, subject,
                json.dumps(card), _iso(now), _iso(now)))
    return db.execute("SELECT * FROM review_cards WHERE kind=? AND content_id=? AND source_id=? "
                      "AND question_number=?", (kind, content_id, source_id, number)).fetchone()


def _review(db: sqlite3.Connection, row: sqlite3.Row, correct: bool, at: datetime,
            origin: str, source_ref: str, **grade) -> bool:
    """Aplica uma revisão uma única vez por `source_ref`; devolve False se já existia."""
    if db.execute("SELECT 1 FROM review_logs WHERE source_ref=?", (source_ref,)).fetchone():
        return False
    if row["last_review_at"] and at < _parse(row["last_review_at"]):
        at = _parse(row["last_review_at"])
    card, log = scheduler.review(json.loads(row["card_json"]), correct, at, **grade)
    db.execute("UPDATE review_cards SET card_json=?,due_at=?,last_review_at=? WHERE id=?",
               (json.dumps(card), _iso(scheduler.due_at(card)), _iso(at), row["id"]))
    db.execute("INSERT INTO review_logs(card_id,rating,reviewed_at,origin,source_ref,log_json) "
               "VALUES(?,?,?,?,?,?)", (row["id"], int(log["rating"]), _iso(at), origin, source_ref,
                                       json.dumps(log)))
    return True


def _delay_question(db: sqlite3.Connection, card_id: str, at: datetime, days: int) -> None:
    """Marca a volta da questão para daqui a ``days`` dias, por cima do que o FSRS decidiu."""
    row = db.execute("SELECT card_json FROM review_cards WHERE id=?", (card_id,)).fetchone()
    card = json.loads(row["card_json"])
    due = at + timedelta(days=days)
    card["due"] = due.astimezone(timezone.utc).isoformat()
    db.execute("UPDATE review_cards SET card_json=?,due_at=? WHERE id=?", (json.dumps(card), _iso(due), card_id))


def _apply_answer(db: sqlite3.Connection, *, content_id: str, subject: str, source_id: str,
                  number: int, correct: bool, at: datetime, origin: str, ref: str,
                  confidence: str | None = None, elapsed_ms: int | None = None,
                  hints_used: int = 0, reason: str | None = None) -> sqlite3.Row:
    """Revisa o cartão do conteúdo; erro ou chute cria (ou revisa) também o cartão da questão.

    Alguns motivos de erro (``REVIEW_DELAY_DAYS``) fixam em quantos dias a questão volta.
    """
    grade = {"confidence": confidence, "elapsed_ms": elapsed_ms, "hints_used": hints_used or 0}
    content_card = _card(db, "content", content_id, subject, at)
    _review(db, content_card, correct, at, origin, ref, **grade)
    question_card = db.execute("SELECT * FROM review_cards WHERE kind='question' AND content_id=? "
                               "AND source_id=? AND question_number=?",
                               (content_id, source_id, number)).fetchone()
    if question_card is None and (not correct or confidence == "guess"):
        question_card = _card(db, "question", content_id, subject, at, source_id, number)
    if question_card is not None:
        reviewed = _review(db, question_card, correct, at, origin, ref + ":q", **grade)
        if reviewed and not correct and reason in REVIEW_DELAY_DAYS:
            _delay_question(db, question_card["id"], at, REVIEW_DELAY_DAYS[reason])
    return db.execute("SELECT * FROM review_cards WHERE id=?", (content_card["id"],)).fetchone()


def sync_diagnostic_answers(db: sqlite3.Connection) -> int:
    """Importa as respostas dos diagnósticos para o agendamento; seguro para repetir."""
    rows = db.execute("""SELECT aq.attempt_id,aq.ordinal,aq.source_id,aq.question_number,aq.subject,
        aq.question_json,aq.is_correct,aq.answered_at,aq.confidence,aq.elapsed_ms,aq.hints_used,aq.error_reason,
        a.course_id FROM attempt_questions aq JOIN attempts a ON a.id = aq.attempt_id
        WHERE aq.answered_at IS NOT NULL
        ORDER BY aq.answered_at, aq.attempt_id, aq.ordinal""").fetchall()
    imported = 0
    with db:
        for row in rows:
            ref = f"attempt:{row['attempt_id']}:{row['ordinal']}"
            if db.execute("SELECT 1 FROM review_logs WHERE source_ref=?", (ref,)).fetchone():
                continue
            content_id = content_id_of(json.loads(row["question_json"]))
            if not content_id:
                continue
            _apply_answer(db, content_id=content_id, subject=row["subject"], source_id=row["source_id"],
                          number=int(row["question_number"]), correct=bool(row["is_correct"]),
                          at=_parse(row["answered_at"]), origin="diagnostic", ref=ref,
                          confidence=row["confidence"], elapsed_ms=row["elapsed_ms"],
                          hints_used=row["hints_used"] or 0, reason=row["error_reason"])
            if row["error_reason"] == "unknown_content":
                # Erro de conteúdo num diagnóstico ou prova também abre a trilha (entra no plano seguinte).
                ladder.start(db, row["course_id"], content_id, _parse(row["answered_at"]))
            imported += 1
    return imported


def _recently_answered(db: sqlite3.Connection, since: datetime) -> set[tuple[str, int]]:
    cutoff = _iso(since)
    rows = db.execute("SELECT source_id,question_number FROM study_answers WHERE answered_at>=? "
                      "UNION SELECT source_id,question_number FROM attempt_questions WHERE answered_at>=?",
                      (cutoff, cutoff)).fetchall()
    return {(row[0], int(row[1])) for row in rows}


def _gap_nodes(db: sqlite3.Connection, course_id: str) -> set[str]:
    """Nós com desempenho baixo ou poucos dados no último diagnóstico concluído do curso."""
    row = db.execute("SELECT metrics_json FROM attempts WHERE course_id=? AND metrics_json IS NOT NULL "
                     "ORDER BY created_at DESC LIMIT 1", (course_id,)).fetchone()
    if not row:
        return set()
    metrics = json.loads(row["metrics_json"]) or {}
    contents = metrics.get("contents") or {}
    gaps = {content_id for content_id, metric in contents.items()
            if metric.get("status") == "insufficient_data"
            or (metric.get("accuracy") is not None and metric["accuracy"] < GAP_ACCURACY)}
    # Fase 5: nós com lacuna ou ainda incertos pelo modelo de domínio também contam.
    gaps.update(node_id for node_id, node in (metrics.get("nodes") or {}).items()
                if node.get("status") in ("gap", "uncertain"))
    return gaps


def course_responses(db: sqlite3.Connection, course_id: str) -> list[dict]:
    """Respostas do curso no formato de ``mastery``: diagnósticos, provas e estudo diário.

    Cada resposta leva também ``kind`` (``diagnostic``/``exam``/``daily``) e os dados da
    questão (``source_id``, ``number``, ``selected``, ``correct_answer``, ``reason``).
    """
    responses = []
    for row in db.execute("""SELECT aq.*, a.mode AS attempt_mode FROM attempt_questions aq
            JOIN attempts a ON a.id = aq.attempt_id
            WHERE a.course_id = ? AND aq.answered_at IS NOT NULL""", (course_id,)):
        row = dict(row)
        responses.append({**mastery.response_from_row(row),
                          "kind": "exam" if row["attempt_mode"] == "exam" else "diagnostic",
                          "source_id": row["source_id"], "number": int(row["question_number"]),
                          "selected": row["selected_answer"], "correct_answer": row["correct_answer"],
                          "reason": row["error_reason"], "note": row["note"]})
    responses += [daily_response(row) for row in daily_rows(db, course_id)]
    responses.sort(key=lambda response: response["answered_at"] or "")
    return responses


def daily_rows(db: sqlite3.Connection, course_id: str) -> list[sqlite3.Row]:
    """Respostas do Treinamento do curso, na ordem em que foram dadas (com a data da sessão)."""
    return db.execute("""SELECT sa.*, s.local_date FROM study_answers sa JOIN study_sessions s ON s.id = sa.session_id
        WHERE s.course_id = ? ORDER BY sa.answered_at""", (course_id,)).fetchall()


def daily_response(row: sqlite3.Row) -> dict:
    """Resposta do Treinamento no formato de ``mastery``.

    Como em ``mastery.response_from_row``: acerto no chute não conta como domínio, e
    questões fora da FUVEST pesam menos.
    """
    return {"correct": bool(row["is_correct"]) and row["confidence"] != "guess",
            "origin": row["origin"], "difficulty": row["difficulty"],
            "evidence_factor": evidence_factor(row["origin"], row["difficulty"]),
            "is_correct": bool(row["is_correct"]), "subject": row["subject"],
            "principal_id": live_id(row["content_id"]), "related_ids": [],
            "answered_at": row["answered_at"], "kind": "daily",
            "source_id": row["source_id"], "number": int(row["question_number"]),
            "selected": row["selected_answer"], "correct_answer": row["correct_answer"],
            "reason": row["error_reason"], "confidence": row["confidence"],
            "elapsed_ms": row["elapsed_ms"], "note": row["note"]}


def _new_leaves(db: sqlite3.Connection, pool: list[dict], fresh: list[str], weights: dict[str, float],
                course_id: str, now: datetime, budget: int, rng: random.Random) -> list[tuple[str, str]]:
    """Folhas novas do dia, escolhidas de cima para baixo.

    A unidade é o conteúdo (3º nível): lacunas primeiro, depois conteúdos já começados
    e ainda não consolidados, depois conteúdos nunca vistos e, por último, os
    consolidados. Em cada rodada entra uma folha por conteúdo — a irmã ainda não
    praticada de maior incidência —, então o conteúdo é coberto por inteiro antes de
    se abrir outro, em vez de sortear subconteúdos soltos.
    """
    incidence = mastery.node_incidence(pool)
    nodes = depth.compute_depth(course_responses(db, course_id), subject_weights=weights, incidence=incidence,
                                half_life_days=HALF_LIFE_DAYS, now=now)
    gaps = _gap_nodes(db, course_id) | {identity for identity, node in nodes.items()
                                        if node["status"] == "gap" and node["level"] not in ("subject", "area")}
    groups: dict[str, list[str]] = defaultdict(list)
    for content_id in fresh:
        groups[depth.content_ancestor(content_id)].append(content_id)

    def tier(content: str) -> int:
        # Matéria e área são amplas demais para marcar lacuna: só conta do conteúdo para baixo.
        if any(gaps & set(aggregate_content_ids([leaf])[2:]) for leaf in groups[content]):
            return 0
        node = nodes.get(content)
        if not node or node["coverage"] == 0:
            return 2
        return 1 if node["depth"] < depth.CONSOLIDATED_DEPTH else 3

    def score(content: str) -> float:
        node = nodes.get(content) or {"depth": 0.0}
        subject = CONTENT_INDEX[content]["subject"]
        return weights.get(subject, 0.0) * (incidence.get(content, 0.0) + 1e-3) * (1 - node["depth"])

    contents = sorted(groups)
    rng.shuffle(contents)  # desempate estável e determinístico no dia
    contents.sort(key=lambda content: (tier(content), -score(content)))
    for content in contents:
        rng.shuffle(groups[content])
        groups[content].sort(key=lambda leaf: (nodes.get(leaf, {}).get("evidence", 0) > 0,
                                               -incidence.get(leaf, 0.0)))
    sequence = []  # rodadas: a n-ésima folha de cada conteúdo antes da (n+1)-ésima de qualquer um
    for depth_index in range(max(len(leaves) for leaves in groups.values())):
        sequence += [(tier(content), groups[content][depth_index]) for content in contents
                     if depth_index < len(groups[content])]
    subject_of = {leaf: CONTENT_INDEX[leaf]["subject"] for _, leaf in sequence}
    chosen = [("new_gap", leaf) for level, leaf in sequence if level == 0][:budget]
    remaining = budget - len(chosen)
    others = [leaf for level, leaf in sequence if level > 0 and subject_of[leaf] in weights]
    if remaining > 0 and others:
        quotas = diagnostics.allocate_counts(Counter(subject_of[leaf] for leaf in others), weights, remaining)
        for leaf in others:
            if quotas.get(subject_of[leaf], 0) > 0:
                quotas[subject_of[leaf]] -= 1
                chosen.append(("new_content", leaf))
    return chosen


def _item(reason: str, entry: dict, content_id: str) -> dict:
    node = CONTENT_INDEX[content_id]
    return {"reason": reason, "sourceId": entry["source_id"], "number": entry["number"],
            "subject": entry["subject"], "contentId": content_id, "contentPath": " → ".join(node["path"]),
            "question": entry["question"], "correctAnswer": entry["correct_answer"]}


def target_phase(db: sqlite3.Connection, course_id: str, now: datetime) -> str | None:
    """Fase da próxima prova cadastrada do curso (hoje conta), ou ``None`` sem prova.

    Banco aberto só com as tabelas do diagnóstico (sem ``exam_dates``) também dá ``None``.
    """
    try:
        row = db.execute("SELECT phase FROM exam_dates WHERE course_id=? AND exam_date>=? "
                         "ORDER BY exam_date, created_at LIMIT 1", (course_id, local_date(now).isoformat())).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["phase"] if row else None


def build_plan(db: sqlite3.Connection, pool: list[dict], profile: dict, course_id: str, now: datetime,
               *, size: int = 15, new_per_day: int = 5, recent_days: int = 7) -> list[dict]:
    """Monta as faixas do dia: questões erradas vencidas, conteúdos vencidos e conteúdos novos."""
    # Pesos efetivos do curso para a fase da próxima prova (fórmula FUVEST quando
    # configurada; senão os pesos manuais).
    try:
        effective = resolve_weights(profile, pool, target_phase(db, course_id, now))["weights"]
    except ValueError:  # pool vazio: sem incidência para aplicar a fórmula
        effective = profile["pesos"]
    weights = {subject: float(weight) for subject, weight in effective.items() if weight > 0}
    rng = random.Random(f"{local_date(now).isoformat()}:{course_id}")
    by_content: dict[str, list[dict]] = defaultdict(list)
    by_identity: dict[tuple[str, int], dict] = {}
    for entry in pool:
        content_id = content_id_of(entry["question"])
        if content_id and entry["subject"] in weights:
            by_content[content_id].append(entry)
            by_identity[(entry["source_id"], entry["number"])] = entry
    recent = _recently_answered(db, now - timedelta(days=recent_days))
    cutoff = _iso(end_of_local_day(now))
    used: set[tuple[str, int]] = set()
    items: list[dict] = []

    def pick(content_id: str) -> dict | None:
        candidates = [e for e in by_content.get(content_id, []) if (e["source_id"], e["number"]) not in used]
        fresh = [e for e in candidates if (e["source_id"], e["number"]) not in recent]
        options = fresh or candidates
        # O plano regular mira a FUVEST; questões de treino entram pelas trilhas e pela prática do erro.
        options = [e for e in options if is_fuvest(e)] or options
        return rng.choice(options) if options else None

    # 1. Questões erradas cujo cartão venceu: refazer a mesma questão.
    for row in db.execute("SELECT * FROM review_cards WHERE kind='question' AND due_at<=? "
                          "ORDER BY due_at, id", (cutoff,)):
        entry = by_identity.get((row["source_id"], int(row["question_number"])))
        if len(items) < size and entry and (entry["source_id"], entry["number"]) not in used:
            used.add((entry["source_id"], entry["number"]))
            items.append(_item("overdue_question", entry, row["content_id"]))

    # 1b. Trilhas ativas: questões do nível atual de cada uma.
    for state in ladder.active(db, course_id)[:ladder.MAX_DAILY_LADDERS]:
        if state["subject"] not in weights:
            continue
        chosen, _ = ladder.pick(db, pool, state["contentId"], state["level"], used, ladder.PER_ROUND, now)
        for entry, content_id in chosen:
            if len(items) >= size:
                break
            used.add((entry["source_id"], entry["number"]))
            items.append({**_item("ladder", entry, content_id), "ladder": state["contentId"]})

    # 2. Conteúdos vencidos: menor retrievability e maior peso da matéria primeiro.
    due_contents = [row for row in db.execute(
        "SELECT * FROM review_cards WHERE kind='content' AND due_at<=?", (cutoff,)) if row["subject"] in weights]
    due_contents.sort(key=lambda row: (scheduler.retrievability(json.loads(row["card_json"]), now)
                                       / weights[row["subject"]], row["content_id"]))
    for row in due_contents:
        if len(items) >= size:
            break
        entry = pick(row["content_id"])
        if entry:
            used.add((entry["source_id"], entry["number"]))
            items.append(_item("due_content", entry, row["content_id"]))

    # 3. Conteúdos novos, de cima para baixo (veja ``_new_leaves``).
    known = {row[0] for row in db.execute("SELECT content_id FROM review_cards WHERE kind='content'")}
    fresh = sorted(content_id for content_id in by_content if content_id not in known)
    budget = min(new_per_day, size - len(items))
    chosen = _new_leaves(db, pool, fresh, weights, course_id, now, budget, rng) if fresh and budget > 0 else []
    for reason, content_id in chosen:
        entry = pick(content_id)
        if entry:
            used.add((entry["source_id"], entry["number"]))
            items.append(_item(reason, entry, content_id))
    for index, item in enumerate(items, 1):
        item["id"] = f"i{index}"
        item["group"] = "ladder" if item["reason"] == "ladder" else "daily"
    return items


def plan_today(db: sqlite3.Connection, pool: list[dict], config: dict, course_id: str, now: datetime,
               *, size: int = 15, new_per_day: int = 5) -> dict:
    """Devolve a sessão do dia; a primeira chamada do dia a monta e grava."""
    profile = diagnostics.course_profile(config, course_id)
    day = local_date(now).isoformat()
    row = db.execute("SELECT * FROM study_sessions WHERE local_date=? AND course_id=?", (day, course_id)).fetchone()
    if row is None:
        items = build_plan(db, pool, profile, course_id, now, size=size, new_per_day=new_per_day)
        session_id = uuid.uuid4().hex[:12]
        with db:
            db.execute("INSERT INTO study_sessions(id,local_date,course_id,plan_json,created_at) VALUES(?,?,?,?,?)",
                       (session_id, day, course_id, json.dumps({"items": items}, ensure_ascii=False), _iso(now)))
        row = db.execute("SELECT * FROM study_sessions WHERE id=?", (session_id,)).fetchone()
    return session_payload(db, row["id"], pool)


def append_training_item(db: sqlite3.Connection, session_id: str, pool: list[dict], config: dict,
                        now: datetime, *, subjects: list[str] | None = None, count: int = 1,
                        content_ids: list[str] | None = None,
                        question_refs: list[tuple[str, int]] | None = None, group: str = "free") -> dict:
    """Acrescenta um bloco de questões recomendadas à sessão diária.

    ``content_ids`` (do calendário) restringe o bloco às questões desses nós da
    taxonomia; sem questões neles, o bloco usa só o filtro por matéria.
    ``question_refs`` (do caderno de erros) acrescenta exatamente essas questões.
    ``group``: ``free`` (treino livre, padrão) ou ``daily`` (bloco do calendário).
    """
    if group not in ("daily", "free"):
        raise ValueError("grupo do bloco deve ser daily ou free")
    if question_refs:
        return _append_questions(db, session_id, pool, question_refs)
    if not 1 <= count <= 100:
        raise ValueError("a quantidade deve ficar entre 1 e 100 questões")
    row, existing = _session(db, session_id, pool)
    profile = diagnostics.course_profile(config, row["course_id"])
    selected = set(profile.get("pesos", {})) if subjects is None else set(subjects)
    allowed = set(profile.get("pesos", {}))
    if not selected or not selected <= allowed:
        raise ValueError("selecione ao menos uma matéria válida para este curso")
    filtered_pool = [entry for entry in pool if entry["subject"] in selected]
    if content_ids:
        targets = {live_id(content_id) for content_id in content_ids}
        focused = [entry for entry in filtered_pool
                   if targets & set(CONTENT_INDEX.get(content_id_of(entry["question"]) or "", {}).get("path_ids", []))]
        filtered_pool = focused or filtered_pool
    # Primeiro usa a fila priorizada pelo mesmo algoritmo da tarefa diária, sem limite diário.
    ranked = build_plan(db, filtered_pool, profile, row["course_id"], now,
                        size=max(1, len(filtered_pool)), new_per_day=max(1, len(filtered_pool)))
    used = {(item["sourceId"], int(item["number"])) for item in existing}
    plan = json.loads(row["plan_json"])
    eligible = []
    for entry in filtered_pool:
        content_id = content_id_of(entry["question"])
        if content_id:
            eligible.append(((entry["source_id"], int(entry["number"])), entry, content_id))
    if not eligible:
        raise ValueError("não há questões classificadas e com gabarito para as matérias selecionadas")
    for offset in range(count):
        # As questões de trilha ficam na aba Trilhas; o bloco usa só o resto da fila.
        candidate = next((item for item in ranked if item["reason"] != "ladder"
                          and (item["sourceId"], int(item["number"])) not in used), None)
        if candidate is None:
            # Quando a fila priorizada se esgota, mantém treino livre sem repetir até necessário.
            unused = [item for item in eligible if item[0] not in used]
            choices = unused or eligible
            _, entry, content_id = random.Random(
                f"{session_id}:{len(existing) + offset}:{local_date(now)}").choice(choices)
            candidate = _item("practice", entry, content_id)
        identity = (candidate["sourceId"], int(candidate["number"]))
        candidate["id"] = f"t{len(existing) + offset + 1}"
        candidate["group"] = group
        used.add(identity)
        plan["items"].append(candidate)
    with db:
        db.execute("UPDATE study_sessions SET plan_json=? WHERE id=?",
                   (json.dumps(plan, ensure_ascii=False), session_id))
    return session_payload(db, session_id, pool)


def _append_questions(db: sqlite3.Connection, session_id: str, pool: list[dict],
                      refs: list[tuple[str, int]]) -> dict:
    """Refazer questões escolhidas (caderno de erros), na ordem pedida."""
    if not 1 <= len(refs) <= 100:
        raise ValueError("escolha de 1 a 100 questões para refazer")
    row, existing = _session(db, session_id, pool)
    by_ref = {(entry["source_id"], int(entry["number"])): entry for entry in pool}
    plan = json.loads(row["plan_json"])
    added = 0
    for source_id, number in dict.fromkeys((str(s), int(n)) for s, n in refs):
        entry = by_ref.get((source_id, number))
        content_id = content_id_of(entry["question"]) if entry else None
        if not content_id:
            continue
        candidate = {**_item("redo", entry, content_id), "group": "free"}
        candidate["id"] = f"t{len(existing) + added + 1}"
        plan["items"].append(candidate)
        added += 1
    if not added:
        raise ValueError("nenhuma das questões escolhidas está no acervo revisado deste curso")
    with db:
        db.execute("UPDATE study_sessions SET plan_json=? WHERE id=?",
                   (json.dumps(plan, ensure_ascii=False), session_id))
    return session_payload(db, session_id, pool)


def _session(db: sqlite3.Connection, session_id: str,
             pool: list[dict] | None = None) -> tuple[sqlite3.Row, list[dict]]:
    """
    O plano gravado guarda uma cópia da questão. Com ``pool``, cada item passa a usar a
    versão atual do acervo (questão corrigida, gabarito revisto); a cópia só vale se a
    questão saiu do acervo.
    """
    row = db.execute("SELECT * FROM study_sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError(f"sessão de estudo não encontrada: {session_id}")
    items = json.loads(row["plan_json"])["items"]
    current = {(e["source_id"], int(e["number"])): e for e in pool or []}
    for item in items:
        entry = current.get((item["sourceId"], int(item["number"])))
        if entry:
            item["question"], item["correctAnswer"] = entry["question"], entry["correct_answer"]
    return row, items


def session_payload(db: sqlite3.Connection, session_id: str, pool: list[dict] | None = None) -> dict:
    """Sessão para a interface: o gabarito só aparece depois da resposta."""
    row, items = _session(db, session_id, pool)
    answers = {a["item_id"]: dict(a) for a in db.execute(
        "SELECT * FROM study_answers WHERE session_id=?", (session_id,))}
    states = ladder.listing(db, row["course_id"])
    ladders = {state["contentId"]: state for state in states["active"] + states["done"]}
    payload_items = []
    for item in items:
        question = dict(item["question"])
        question.pop("_source_dir", None)
        answer = answers.get(item["id"])
        entry = {key: item[key] for key in ("id", "reason", "sourceId", "number", "subject",
                                             "contentId", "contentPath")}
        entry["group"] = item_group(item)
        if item.get("ladder"):
            entry["ladder"] = ladders.get(item["ladder"])
        entry.update(question=question, answered=answer is not None)
        if answer:
            entry.update(selectedAnswer=answer["selected_answer"], correct=bool(answer["is_correct"]),
                         correctAnswer=answer["correct_answer"], errorReason=answer["error_reason"],
                         confidence=answer["confidence"], elapsedMs=answer["elapsed_ms"],
                         hintsUsed=answer["hints_used"], note=answer["note"])
        payload_items.append(entry)
    counts = Counter(item["reason"] for item in items)
    groups = {group: {"total": 0, "answered": 0, "correct": 0} for group in GROUPS}
    for entry in payload_items:
        summary = groups[entry["group"]]
        summary["total"] += 1
        summary["answered"] += int(entry["answered"])
        summary["correct"] += int(bool(entry.get("correct")))
    return {"id": row["id"], "date": row["local_date"], "courseId": row["course_id"],
            "counts": {reason: counts.get(reason, 0) for reason in REASONS}, "groups": groups,
            "answered": len(answers), "correct": sum(int(a["is_correct"]) for a in answers.values()),
            "items": payload_items}


def record_study_answer(db: sqlite3.Connection, session_id: str, item_id: str, answer: str | None,
                        reason: str | None, now: datetime, pool: list[dict] | None = None, *,
                        confidence: str | None = None, elapsed_ms: object = None, hints_used: object = 0,
                        note: str | None = None) -> dict:
    """Registra a resposta do estudo diário e realimenta os cartões.

    ``confidence``/``elapsed_ms``/``hints_used`` definem a nota do FSRS (``scheduler.rating_for``).
    """
    session_row, items = _session(db, session_id, pool)
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise ValueError(f"item {item_id} não pertence à sessão")
    if db.execute("SELECT 1 FROM study_answers WHERE session_id=? AND item_id=?", (session_id, item_id)).fetchone():
        raise ValueError(f"item {item_id} já respondido")
    alternatives = item["question"].get("alternativas") or {}
    normalized = (answer or "").strip().upper() or None
    if normalized and normalized != item["correctAnswer"] and not reason:
        return {"requiresReason": True}
    answer, correct, reason = diagnostics.grade_answer(answer, alternatives, item["correctAnswer"], reason)
    extras = diagnostics.answer_extras(confidence if answer else None, elapsed_ms, hints_used, note)
    with db:
        db.execute("""INSERT INTO study_answers(session_id,item_id,source_id,question_number,subject,content_id,
            selected_answer,correct_answer,is_correct,error_reason,answered_at,confidence,elapsed_ms,hints_used,note,
            origin,difficulty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (session_id, item_id, item["sourceId"], item["number"], item["subject"], item["contentId"],
                    answer, item["correctAnswer"], int(correct), reason, _iso(now), extras["confidence"],
                    extras["elapsed_ms"], extras["hints_used"], extras["note"],
                    item["question"].get("_origem"), item["question"].get("_dificuldade")))
        card = _apply_answer(db, content_id=item["contentId"], subject=item["subject"],
                             source_id=item["sourceId"], number=int(item["number"]), correct=correct,
                             at=now, origin="daily", ref=f"daily:{session_id}:{item_id}",
                             confidence=extras["confidence"], elapsed_ms=extras["elapsed_ms"],
                             hints_used=extras["hints_used"], reason=reason)
        followups = _insert_followups(db, session_id, items, item, reason, pool, now) if not correct else []
        ladder_result = _update_ladder(db, session_row["course_id"], session_id, item, reason,
                                       correct and extras["confidence"] != "guess", pool, now)
    rating = scheduler.rating_for(correct, extras["confidence"], extras["elapsed_ms"], extras["hints_used"])
    question_card = db.execute("SELECT due_at FROM review_cards WHERE kind='question' AND content_id=? "
                               "AND source_id=? AND question_number=?",
                               (item["contentId"], item["sourceId"], int(item["number"]))).fetchone()
    return {"itemId": item_id, "answer": answer, "correct": correct, "reason": reason,
            "correctAnswer": item["correctAnswer"], "nextReview": card["due_at"],
            "questionReview": question_card["due_at"] if question_card else None,
            "confidence": extras["confidence"], "rating": scheduler.rating_label(rating),
            "followups": followups, "ladder": ladder_result,
            "summaryContentId": item["contentId"] if reason == "unknown_content" else None}


def _update_ladder(db: sqlite3.Connection, course_id: str, session_id: str, item: dict, reason: str | None,
                   success: bool, pool: list[dict] | None, now: datetime) -> dict | None:
    """Abre a trilha no erro de conteúdo, aplica a resposta à trilha ativa e põe as próximas questões.

    ``generating``: faltou questão no nível de treino atual; quem chama pede mais à IA.
    """
    ladder_id = item.get("ladder") or item["contentId"]
    if reason == "unknown_content":
        state = {**ladder.start(db, course_id, item["contentId"], now), "change": "started"}
        ladder_id = item["contentId"]
    else:
        state = ladder.record(db, course_id, ladder_id, success, now)
    if state is None:
        return None
    inserted, missing = [], False
    if state["status"] == "active":
        inserted, missing = _insert_ladder_items(db, session_id, item, ladder_id, state["level"], pool, now)
    return {**state, "inserted": inserted, "generating": missing and state["level"] < ladder.TOP_LEVEL}


def append_ladder_block(db: sqlite3.Connection, session_id: str, pool: list[dict], content_id: str,
                        now: datetime, count: int = 2 * ladder.PER_ROUND) -> dict:
    """"Treinar agora": acrescenta ao fim da sessão questões do nível atual da trilha."""
    row, _ = _session(db, session_id, pool)
    state = ladder.get(db, row["course_id"], content_id)
    if state is None or state["status"] != "active":
        raise ValueError("a trilha não está ativa")
    plan = json.loads(row["plan_json"])
    used = {(entry["sourceId"], int(entry["number"])) for entry in plan["items"]}
    chosen, _ = ladder.pick(db, pool, content_id, state["level"], used, count, now)
    if not chosen:
        raise ValueError("ainda não há questões novas para este nível; as da IA estão sendo geradas")
    taken = sum(1 for entry in plan["items"] if entry["id"].startswith("tl"))
    plan["items"] += [{**_item("ladder", entry, node_id), "ladder": content_id, "id": f"tl{taken + index}",
                       "group": "ladder"}
                      for index, (entry, node_id) in enumerate(chosen, 1)]
    with db:
        db.execute("UPDATE study_sessions SET plan_json=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), session_id))
    return session_payload(db, session_id, pool)


def _insert_ladder_items(db: sqlite3.Connection, session_id: str, item: dict, ladder_id: str, level: int,
                         pool: list[dict] | None, now: datetime) -> tuple[list[str], bool]:
    """Põe as próximas questões da trilha logo depois da atual, quando não há outras pendentes."""
    if not pool:
        return [], False
    plan = json.loads(db.execute("SELECT plan_json FROM study_sessions WHERE id=?", (session_id,)).fetchone()[0])
    answered = {row[0] for row in db.execute("SELECT item_id FROM study_answers WHERE session_id=?", (session_id,))}
    own = [entry for entry in plan["items"] if entry.get("ladder") == ladder_id]
    if any(entry["id"] not in answered for entry in own) or len(own) >= ladder.MAX_PER_SESSION:
        return [], False
    used = {(entry["sourceId"], int(entry["number"])) for entry in plan["items"]}
    chosen, missing = ladder.pick(db, pool, ladder_id, level, used, ladder.PER_ROUND, now)
    taken = sum(1 for entry in plan["items"] if entry["id"].startswith("tl"))
    # Prefixo "t": como os blocos de treino, não conta na tarefa diária.
    new_items = [{**_item("ladder", entry, content_id), "ladder": ladder_id, "id": f"tl{taken + index}", "group": "ladder"}
                 for index, (entry, content_id) in enumerate(chosen, 1)]
    if new_items:
        position = next(index for index, entry in enumerate(plan["items"]) if entry["id"] == item["id"]) + 1
        plan["items"][position:position] = new_items
        db.execute("UPDATE study_sessions SET plan_json=? WHERE id=?",
                   (json.dumps(plan, ensure_ascii=False), session_id))
    return [entry["id"] for entry in new_items], missing


def _followup_entries(db: sqlite3.Connection, pool: list[dict], item: dict, used: set[tuple[str, int]],
                      count: int, now: datetime) -> list[dict]:
    """Questões para praticar o mesmo assunto depois de um erro.

    Primeiro o mesmo subconteúdo, depois o conteúdo acima dele; entre elas, as mais
    fáceis segundo a resolução comentada (sem resolução, contam como médias). Questões
    respondidas na última semana ficam de fora.
    """
    leaf = item["contentId"]
    parent = depth.content_ancestor(leaf)
    recent = _recently_answered(db, now - timedelta(days=7))
    candidates = []
    for entry in pool:
        identity = (entry["source_id"], int(entry["number"]))
        content_id = content_id_of(entry["question"])
        if identity in used or identity in recent or not content_id:
            continue
        if content_id == leaf:
            tier = 0
        elif parent != leaf and parent in CONTENT_INDEX[content_id]["path_ids"]:
            tier = 1
        else:
            continue
        candidates.append((tier, entry.get("difficulty") or 2, identity, entry, content_id))
    candidates.sort(key=lambda candidate: candidate[:3])
    return [_item("followup_practice", entry, content_id) for *_, entry, content_id in candidates[:count]]


def _insert_followups(db: sqlite3.Connection, session_id: str, items: list[dict], item: dict,
                      reason: str | None, pool: list[dict] | None, now: datetime) -> list[str]:
    """Põe na sessão as questões de prática que o motivo do erro pede (``FOLLOWUPS``)."""
    if reason not in FOLLOWUPS or not pool:
        return []
    count, offset = FOLLOWUPS[reason]
    used = {(entry["sourceId"], int(entry["number"])) for entry in items}
    chosen = _followup_entries(db, pool, item, used, count, now)
    if not chosen:
        return []
    plan = json.loads(db.execute("SELECT plan_json FROM study_sessions WHERE id=?", (session_id,)).fetchone()[0])
    taken = sum(1 for entry in plan["items"] if entry["id"].startswith("tf"))
    # Prefixo "t": como os blocos de treino, não conta na tarefa diária.
    for index, followup in enumerate(chosen, 1):
        followup["id"] = f"tf{taken + index}"
        followup["group"] = item_group(item)  # a prática do erro fica na mesma frente da questão
    position = next(index for index, entry in enumerate(plan["items"]) if entry["id"] == item["id"]) + 1
    answered = {row[0] for row in db.execute("SELECT item_id FROM study_answers WHERE session_id=?", (session_id,))}
    # ``offset`` conta só as questões ainda por responder depois da atual.
    while offset and position < len(plan["items"]):
        if plan["items"][position]["id"] not in answered:
            offset -= 1
        position += 1
    plan["items"][position:position] = chosen
    db.execute("UPDATE study_sessions SET plan_json=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), session_id))
    return [followup["id"] for followup in chosen]


def progress(db: sqlite3.Connection, now: datetime, *, days: int = 7) -> dict:
    """Domínio por conteúdo, revisões vencidas hoje e previsão dos próximos dias."""
    logs = defaultdict(list)
    for row in db.execute("SELECT card_id,rating FROM review_logs"):
        logs[row["card_id"]].append(int(row["rating"]))
    contents = []
    for row in db.execute("SELECT * FROM review_cards WHERE kind='content'"):
        node = CONTENT_INDEX.get(row["content_id"])
        ratings = logs.get(row["id"], [])
        contents.append({
            "contentId": row["content_id"], "subject": row["subject"],
            "path": " → ".join(node["path"]) if node else row["content_id"],
            "reviews": len(ratings),
            "accuracy": round(sum(r > 1 for r in ratings) / len(ratings), 4) if ratings else None,
            "retrievability": round(scheduler.retrievability(json.loads(row["card_json"]), now), 4),
            "dueAt": row["due_at"],
        })
    contents.sort(key=lambda c: (c["retrievability"], c["path"]))
    today = local_date(now)
    forecast = {(today + timedelta(days=offset)).isoformat(): 0 for offset in range(days)}
    due_today = 0
    cutoff = end_of_local_day(now)
    for row in db.execute("SELECT due_at FROM review_cards"):
        due = _parse(row["due_at"])
        if due <= cutoff:
            due_today += 1
            continue
        key = due.astimezone(LOCAL_TZ).date().isoformat()
        if key in forecast:
            forecast[key] += 1
    return {"dueToday": due_today, "forecast": forecast, "contents": contents}


def reset_learning_context(db: sqlite3.Connection) -> dict:
    """Limpa respostas e sinais de aprendizagem, preservando provas e gabaritos."""
    counts = {
        "studySessions": db.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0],
        "studyAnswers": db.execute("SELECT COUNT(*) FROM study_answers").fetchone()[0],
        "diagnostics": db.execute("SELECT COUNT(*) FROM attempts WHERE mode!='exam'").fetchone()[0],
        "examAttempts": db.execute("SELECT COUNT(*) FROM attempts WHERE mode='exam'").fetchone()[0],
        "reviewCards": db.execute("SELECT COUNT(*) FROM review_cards").fetchone()[0],
    }
    with db:
        db.execute("DELETE FROM study_sessions")
        db.execute("DELETE FROM review_cards")
        db.execute("DELETE FROM ladders")
        # O dia congelado do calendário reflete o progresso apagado; as datas das provas ficam.
        db.execute("DELETE FROM calendar_days")
        db.execute("DELETE FROM attempts WHERE mode!='exam'")
        # Mantém a tentativa e as questões da prova, removendo apenas respostas e métricas pessoais.
        db.execute("""UPDATE attempt_questions SET selected_answer=NULL,error_reason=NULL,
            is_correct=NULL,answered_at=NULL,confidence=NULL,elapsed_ms=NULL,hints_used=0,note=NULL
            WHERE attempt_id IN
            (SELECT id FROM attempts WHERE mode='exam')""")
        db.execute("""UPDATE attempts SET status='in_progress',metrics_json=NULL,analysis_json=NULL,
            analysis_status=NULL,analysis_error=NULL WHERE mode='exam'""")
    return {"ok": True, **counts}
