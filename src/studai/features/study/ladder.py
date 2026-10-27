"""Trilha por nível: do zero ao estável num subconteúdo.

Começa quando o aluno erra por não saber o conteúdo. Níveis: 1 fácil, 2 média,
3 difícil (questões de qualquer origem com essa dificuldade) e 4 FUVEST. Dois acertos
seguidos sem chute sobem um nível; dois erros seguidos descem um; dois acertos no
nível 4 concluem a trilha. Faltando questão do nível, quem chama pede à IA mais
questões (``missing``) e, enquanto isso, usa a de dificuldade mais próxima.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from studai.features.classification.taxonomy import CONTENT_INDEX, live_id
from studai.features.diagnostics import depth
from studai.features.questions.origin import DIFFICULTIES, is_fuvest

LEVELS = {1: "fácil", 2: "média", 3: "difícil", 4: "FUVEST"}
TOP_LEVEL = 4
STEP_UP = 2       # acertos seguidos (sem chute) para subir
STEP_DOWN = 2     # erros seguidos para descer
PER_ROUND = 2     # questões da trilha inseridas de cada vez
MAX_PER_SESSION = 10
MAX_DAILY_LADDERS = 3
RECENT_DAYS = 7


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS ladders (
        course_id TEXT NOT NULL, content_id TEXT NOT NULL,
        level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 4), streak INTEGER NOT NULL DEFAULT 0,
        misses INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL CHECK(status IN ('active','done','closed')),
        started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(course_id, content_id))""")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def public(row: sqlite3.Row | dict) -> dict:
    node = CONTENT_INDEX.get(row["content_id"]) or {}
    return {"contentId": row["content_id"], "name": node.get("name", row["content_id"]),
            "path": " → ".join(node.get("path", [])), "subject": node.get("subject"), "level": row["level"],
            "levelLabel": LEVELS[row["level"]], "streak": row["streak"], "status": row["status"],
            "startedAt": row["started_at"], "updatedAt": row["updated_at"]}


def get(db: sqlite3.Connection, course_id: str, content_id: str) -> sqlite3.Row | None:
    ensure_schema(db)
    return db.execute("SELECT * FROM ladders WHERE course_id=? AND content_id=?", (course_id, content_id)).fetchone()


def start(db: sqlite3.Connection, course_id: str, content_id: str, now: datetime) -> dict:
    """Abre a trilha no nível 1; uma trilha ativa continua de onde estava."""
    content_id = live_id(content_id)
    row = get(db, course_id, content_id)
    if row is None or row["status"] != "active":
        db.execute("""INSERT INTO ladders(course_id,content_id,level,streak,misses,status,started_at,updated_at)
            VALUES(?,?,1,0,0,'active',?,?) ON CONFLICT(course_id,content_id) DO UPDATE SET level=1, streak=0,
            misses=0, status='active', started_at=excluded.started_at, updated_at=excluded.updated_at""",
                   (course_id, content_id, _iso(now), _iso(now)))
        row = get(db, course_id, content_id)
    return public(row)


def close(db: sqlite3.Connection, course_id: str, content_id: str, now: datetime) -> dict:
    row = get(db, course_id, content_id)
    if row is None:
        raise ValueError("trilha não encontrada")
    db.execute("UPDATE ladders SET status='closed', updated_at=? WHERE course_id=? AND content_id=?",
               (_iso(now), course_id, content_id))
    return public(get(db, course_id, content_id))


def record(db: sqlite3.Connection, course_id: str, content_id: str, success: bool, now: datetime) -> dict | None:
    """Aplica uma resposta à trilha ativa do subconteúdo; devolve o que mudou."""
    row = get(db, course_id, content_id)
    if row is None or row["status"] != "active":
        return None
    level, streak, misses, status = row["level"], row["streak"], row["misses"], "active"
    change = None
    if success:
        streak, misses = streak + 1, 0
        if streak >= STEP_UP:
            streak = 0
            if level == TOP_LEVEL:
                status, change = "done", "done"
            else:
                level, change = level + 1, "up"
    else:
        streak, misses = 0, misses + 1
        if misses >= STEP_DOWN:
            misses = 0
            if level > 1:
                level, change = level - 1, "down"
    db.execute("UPDATE ladders SET level=?,streak=?,misses=?,status=?,updated_at=? WHERE course_id=? AND content_id=?",
               (level, streak, misses, status, _iso(now), course_id, content_id))
    return {**public(get(db, course_id, content_id)), "change": change, "previousLevel": row["level"]}


def active(db: sqlite3.Connection, course_id: str) -> list[dict]:
    ensure_schema(db)
    return [public(row) for row in db.execute(
        "SELECT * FROM ladders WHERE course_id=? AND status='active' ORDER BY updated_at DESC", (course_id,))]


def listing(db: sqlite3.Connection, course_id: str) -> dict:
    ensure_schema(db)
    rows = [public(row) for row in db.execute(
        "SELECT * FROM ladders WHERE course_id=? AND status!='closed' ORDER BY status, updated_at DESC", (course_id,))]
    return {"active": [row for row in rows if row["status"] == "active"],
            "done": [row for row in rows if row["status"] == "done"], "levels": LEVELS}


def _content_of(entry: dict) -> str | None:
    raw = (entry["question"].get("classificacao") or {}).get("conteudo_principal_id")
    content_id = live_id(str(raw)) if raw else None
    return content_id if content_id in CONTENT_INDEX else None


def pick(db: sqlite3.Connection, pool: list[dict], content_id: str, level: int, used: set[tuple[str, int]],
         count: int, now: datetime) -> tuple[list[tuple[dict, str]], bool]:
    """Questões para o nível da trilha e se faltou questão do nível exato.

    Primeiro o mesmo subconteúdo, depois o conteúdo acima dele. Níveis 1–3: a
    dificuldade igual ao nível vem antes (sem dificuldade conta como média); no nível 4,
    só FUVEST. Questões respondidas na última semana ficam de fora.
    """
    parent = depth.content_ancestor(content_id)
    cutoff = _iso(now - timedelta(days=RECENT_DAYS))
    recent = {(row[0], int(row[1])) for row in db.execute(
        "SELECT source_id,question_number FROM study_answers WHERE answered_at>=? UNION "
        "SELECT source_id,question_number FROM attempt_questions WHERE answered_at>=?", (cutoff, cutoff))}
    exact, near = [], []
    for entry in pool:
        identity = (entry["source_id"], int(entry["number"]))
        node_id = _content_of(entry)
        if identity in used or identity in recent or not node_id:
            continue
        if node_id == content_id:
            tier = 0
        elif parent != content_id and parent in CONTENT_INDEX[node_id]["path_ids"]:
            tier = 1
        else:
            continue
        difficulty = entry.get("difficulty") or 2
        if level == TOP_LEVEL:
            if is_fuvest(entry):
                exact.append((tier, False, identity, entry, node_id))
            continue
        # Nos níveis de treino as questões de treino vêm antes: as da FUVEST ficam para o
        # nível 4 e para o plano regular. FUVEST sem dificuldade estimada conta como média.
        if difficulty == level:
            exact.append((tier, is_fuvest(entry), identity, entry, node_id))
        else:
            near.append((abs(difficulty - level), tier, is_fuvest(entry), identity, entry, node_id))
    exact.sort(key=lambda item: item[:3])
    near.sort(key=lambda item: item[:4])
    chosen = [(entry, node_id) for *_, entry, node_id in exact[:count]]
    missing = len(chosen) < count
    if missing and level < TOP_LEVEL:
        chosen += [(entry, node_id) for *_, entry, node_id in near[:count - len(chosen)]]
    return chosen, missing


def missing_levels(db: sqlite3.Connection, pool: list[dict], course_id: str, now: datetime) -> list[tuple[str, int]]:
    """Trilhas ativas sem questões suficientes no nível de treino atual (para gerar com a IA)."""
    result = []
    for ladder in active(db, course_id):
        if ladder["level"] in DIFFICULTIES:
            _, missing = pick(db, pool, ladder["contentId"], ladder["level"], set(), PER_ROUND, now)
            if missing:
                result.append((ladder["contentId"], ladder["level"]))
    return result
