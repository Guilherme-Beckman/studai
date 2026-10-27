"""Mini-resumo de um subconteúdo da taxonomia, para quem errou por não saber o conteúdo.

Gerado pela IA a partir do nome, da descrição e do caminho do nó (``CONTENT_INDEX``),
uma vez por nó e versão da taxonomia; o aluno aprova ou aponta o erro, como nas
resoluções comentadas.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from studai.features.classification import service as clf
from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, live_id
from studai.integrations.ai_cache import cached_ai_result

PROMPT_VERSION = "summary-v1"

SYSTEM = """Você é professor de cursinho e escreve um mini-resumo de um assunto da FUVEST para um aluno que acabou de errar uma questão por não saber o conteúdo.
Recebe o assunto, o caminho dele na matéria e a descrição do edital.
Escreva em português do Brasil, direto ao ponto, só o que a 1ª fase cobra.
- summary: de 3 a 6 frases que explicam o assunto.
- key_points: de 3 a 5 pontos para lembrar (fórmulas, definições, datas, relações).
- common_mistakes: de 0 a 4 confusões comuns em questões desse assunto.
Devolva JSON sem markdown: {"summary": texto, "key_points": [textos], "common_mistakes": [textos]}"""


class SummaryError(Exception):
    """A IA não devolveu um mini-resumo válido."""


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS content_summaries (
        content_id TEXT PRIMARY KEY, taxonomy_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('generated','approved','rejected')),
        content_json TEXT NOT NULL, model TEXT, prompt_version TEXT NOT NULL, feedback TEXT,
        created_at TEXT NOT NULL, reviewed_at TEXT)""")


def _texts(value, low: int, high: int, name: str) -> list[str]:
    if not isinstance(value, list) or not low <= len(value) <= high \
            or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} deve ter de {low} a {high} textos")
    return [item.strip() for item in value]


def validate_summary(obj: dict) -> dict:
    if not isinstance(obj, dict) or not isinstance(obj.get("summary"), str) or not obj["summary"].strip():
        raise ValueError("summary deve ser texto não vazio")
    return {"summary": obj["summary"].strip(), "key_points": _texts(obj.get("key_points"), 3, 5, "key_points"),
            "common_mistakes": _texts(obj.get("common_mistakes", []), 0, 4, "common_mistakes")}


def _node(content_id: str) -> dict:
    node = CONTENT_INDEX.get(live_id(content_id))
    if node is None:
        raise ValueError(f"conteúdo desconhecido: {content_id}")
    return node


def get_summary(db: sqlite3.Connection, content_id: str) -> dict | None:
    ensure_schema(db)
    row = db.execute("SELECT * FROM content_summaries WHERE content_id=?", (_node(content_id)["id"],)).fetchone()
    if row is None:
        return None
    return {"contentId": row["content_id"], "status": row["status"], "content": json.loads(row["content_json"]),
            "model": row["model"], "feedback": row["feedback"], "taxonomyVersion": row["taxonomy_version"]}


def needs_generation(record: dict | None) -> bool:
    return record is None or record["status"] == "rejected" or record["taxonomyVersion"] != CONTENT_TAXONOMY_VERSION


def generate_summary(db: sqlite3.Connection, content_id: str, provider) -> dict:
    node = _node(content_id)
    previous = get_summary(db, node["id"])
    payload = {"assunto": node["name"], "caminho": " → ".join(node["path"]), "descricao": node.get("description"),
               "materia": node["subject"]}
    if previous and previous["status"] == "rejected" and previous["feedback"]:
        payload.update(resumo_anterior=previous["content"], critica_do_aluno=previous["feedback"],
                       task="Reescreva o resumo corrigindo o problema apontado pelo aluno.")
    user = json.dumps(payload, ensure_ascii=False)
    errors: list[str] = []

    def ask():
        for attempt in range(2):
            prompt = user if attempt == 0 else user + f"\n\nA resposta anterior é inválida: {errors[-1]}. Corrija-a."
            try:
                return validate_summary(clf.extract_json(provider.complete(SYSTEM, prompt)))
            except (ValueError, TypeError) as error:
                errors.append(str(error))
        return None

    content = cached_ai_result("content_summary", provider, SYSTEM, user, ask)
    if content is None:
        raise SummaryError(f"a IA não gerou um mini-resumo válido: {errors[-1] if errors else 'sem resposta'}")
    model = str(getattr(provider, "last_used_model", "") or getattr(provider, "label", "") or "")
    with db:
        db.execute("""INSERT INTO content_summaries(content_id,taxonomy_version,status,content_json,model,
            prompt_version,feedback,created_at,reviewed_at) VALUES(?,?,?,?,?,?,NULL,?,NULL)
            ON CONFLICT(content_id) DO UPDATE SET taxonomy_version=excluded.taxonomy_version,
            status=excluded.status, content_json=excluded.content_json, model=excluded.model,
            prompt_version=excluded.prompt_version, feedback=NULL, created_at=excluded.created_at,
            reviewed_at=NULL""",
                   (node["id"], CONTENT_TAXONOMY_VERSION, "generated", json.dumps(content, ensure_ascii=False),
                    model, PROMPT_VERSION, datetime.now(timezone.utc).isoformat()))
    return get_summary(db, node["id"])


def review_summary(db: sqlite3.Connection, content_id: str, approved: bool, feedback: str | None = None) -> dict:
    record = get_summary(db, content_id)
    if record is None:
        raise ValueError("o conteúdo ainda não tem mini-resumo para revisar")
    feedback = (feedback or "").strip() or None
    if not approved and not feedback:
        raise ValueError("descreva o erro do resumo para gerar outro")
    with db:
        db.execute("UPDATE content_summaries SET status=?,feedback=?,reviewed_at=? WHERE content_id=?",
                   ("approved" if approved else "rejected", feedback, datetime.now(timezone.utc).isoformat(),
                    record["contentId"]))
    return get_summary(db, content_id)


def public_payload(record: dict) -> dict:
    node = CONTENT_INDEX[record["contentId"]]
    return {"status": record["status"], "contentId": record["contentId"], "name": node["name"],
            "path": " → ".join(node["path"]), **record["content"]}
