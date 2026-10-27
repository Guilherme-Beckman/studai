"""Lógica determinística e persistência do diagnóstico por curso."""
from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

from studai.features.classification import service as clf
from studai.integrations.ai_cache import cached_ai_result
from studai.integrations.sqlite import connect as connect_sqlite
from studai.features.answer_keys.service import (
    associate_answer_key, detect_exam_variant, exam_key, exam_variant, import_answer_key,
    import_embedded_answer_key,
    parse_answer_key_pdf, seed_corpus_answer_keys, source_id_for,
)
from studai.features.diagnostics import courses, mastery
from studai.features.diagnostics.report import build_report
from studai.features.questions import review as question_review
from studai.features.classification.taxonomy import (
    CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, aggregate_content_ids,
)

SCHEMA_VERSION = 4
DEFAULT_STATIC_TOTAL = 51
DEFAULT_ADAPTIVE_TOTAL = 30
MAX_ADAPTIVE_TOTAL = 51
# Regra de parada adaptativa (documentada em ``adaptive_stop_reason``).
STOP_WIDTH = 0.4
STOP_MIN_INCIDENCE = 0.1
STOP_TOP_WEIGHT_SHARE = 0.8
STOP_MIN_AREA_ANSWERS = 2
STOP_GAP_UPPER = 0.55
STOP_STRONG_LOWER = 0.5
MODES = {"static", "adaptive", "exam"}
# Motivo do erro informado pelo aluno (v4 acrescentou concept_confusion, trap e time_pressure).
REASONS = {"unknown_content", "concept_confusion", "calc_error", "misread", "trap", "time_pressure", "blank"}
ACTIONS = {"theory", "concept_review", "calculation_practice", "interpretation_practice", "distractor_practice",
           "pace_practice", "review"}
ACTION_BY_REASON = {"unknown_content": "theory", "concept_confusion": "concept_review",
                    "calc_error": "calculation_practice", "misread": "interpretation_practice",
                    "trap": "distractor_practice", "time_pressure": "pace_practice", "blank": "review"}
CONFIDENCES = ("sure", "doubt", "guess")
MAX_ELAPSED_MS = 30 * 60 * 1000
MAX_HINTS = 3
MAX_NOTE_LENGTH = 1000
# Colunas extras de cada resposta (v4): confiança, tempo gasto, dicas usadas e anotação.
ANSWER_EXTRA_COLUMNS = (("confidence", "TEXT"), ("elapsed_ms", "INTEGER"),
                        ("hints_used", "INTEGER NOT NULL DEFAULT 0"), ("note", "TEXT"))
ALIASES = {
    "arte": "artes", "artes": "arte", "portugues": "lingua portuguesa",
    "lingua portuguesa": "lingua portuguesa", "educacao fisica": "educacao fisica",
}


def norm(value: str) -> str:
    return clf._norm(str(value))


def canonical_subject(value: str, subjects: list[str]) -> str | None:
    n = norm(value)
    aliases = {norm(k): norm(v) for k, v in ALIASES.items()}
    wanted = aliases.get(n, n)
    # Accept both the previous display label "Artes" and the current taxonomy label "Arte".
    return next((subject for subject in subjects if norm(subject) == wanted), None) or \
        next((subject for subject in subjects if norm(subject) == n), None)


def load_course_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("version"), int) or not isinstance(data.get("cursos"), dict):
        raise ValueError("configuração de cursos inválida")
    return data


def course_profile(config: dict, course_id: str) -> dict:
    try:
        profile = config["cursos"][course_id]
    except KeyError:
        raise ValueError(f"curso não configurado: {course_id}") from None
    weights = profile.get("pesos")
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"curso {course_id} não possui pesos")
    for subject, weight in weights.items():
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"peso inválido para {subject}: use um número positivo")
    return profile


def add_missing_columns(db: sqlite3.Connection, table: str, declarations: tuple[tuple[str, str], ...]) -> None:
    """Migração aditiva: acrescenta à tabela as colunas que ainda não existem."""
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, declaration in declarations:
        if name not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def connect_db(path: Path) -> sqlite3.Connection:
    db = connect_sqlite(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS answer_keys (
            source_id TEXT NOT NULL, question_number INTEGER NOT NULL, answer TEXT NOT NULL,
            source_pdf TEXT NOT NULL, imported_at TEXT NOT NULL,
            is_annulled INTEGER NOT NULL DEFAULT 0, variant TEXT, association_method TEXT,
            PRIMARY KEY(source_id, question_number)
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY, course_id TEXT NOT NULL, course_name TEXT NOT NULL,
            config_version INTEGER NOT NULL, profile_snapshot TEXT NOT NULL,
            seed INTEGER NOT NULL, requested_count INTEGER NOT NULL, actual_count INTEGER NOT NULL,
            status TEXT NOT NULL, plan_json TEXT NOT NULL, created_at TEXT NOT NULL,
            metrics_json TEXT, analysis_json TEXT, analysis_status TEXT,
            analysis_error TEXT
        );
        CREATE TABLE IF NOT EXISTS attempt_questions (
            attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, source_id TEXT NOT NULL, question_number INTEGER NOT NULL,
            subject TEXT NOT NULL, topic TEXT NOT NULL, question_json TEXT NOT NULL,
            correct_answer TEXT NOT NULL, selected_answer TEXT, error_reason TEXT,
            is_correct INTEGER, answered_at TEXT,
            PRIMARY KEY(attempt_id, ordinal),
            UNIQUE(attempt_id, source_id, question_number)
        );
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(answer_keys)")}
    for name, declaration in (("is_annulled", "INTEGER NOT NULL DEFAULT 0"),
                              ("variant", "TEXT"), ("association_method", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE answer_keys ADD COLUMN {name} {declaration}")
    attempt_columns = {row[1] for row in db.execute("PRAGMA table_info(attempts)")}
    if "mode" not in attempt_columns:
        # v3: origem da tentativa (diagnóstico estático/adaptativo ou prova completa).
        db.execute("ALTER TABLE attempts ADD COLUMN mode TEXT NOT NULL DEFAULT 'static'")
    add_missing_columns(db, "attempt_questions", ANSWER_EXTRA_COLUMNS)
    old = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    if old and int(old[0]) > SCHEMA_VERSION:
        db.close()
        raise ValueError("banco de diagnóstico foi criado por uma versão mais nova")
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    db.commit()
    return db


def load_question_pool(root: Path, db: sqlite3.Connection, subjects: list[str]) -> tuple[list[dict], dict]:
    """Questões elegíveis ao diagnóstico: revisadas por humano, com conteúdo válido e gabarito.

    Usa o mesmo critério de ``questions.review.diagnostic_ready`` (o dos
    exemplos revisados). Exclusões são contadas por motivo (``pending_review``,
    ``invalid_content``, ``missing_subject``, ``unusable``, ``bad_crop``,
    ``unclassified``, ``no_valid_answer_key``, ...). Provas do acervo sem
    gabarito no banco recebem antes o ``gabarito.json`` distribuído com elas.
    """
    seed_corpus_answer_keys(db, root)
    pool: list[dict] = []
    excluded = Counter()
    seen_questions: set[tuple[str, int]] = set()
    for path in sorted(root.rglob("questoes.classificado.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            excluded["json_invalid"] += 1
            continue
        source_id = source_id_for(data, path)
        reviewed_states = question_review.read_reviews(path).get("questions") or {}
        shared_texts = data.get("textos_compartilhados", {})
        keys = {int(r[0]): r[1] for r in db.execute(
            "SELECT question_number,answer FROM answer_keys WHERE source_id=? AND is_annulled=0", (source_id,))}
        for question in data.get("questoes", []):
            try:
                number = int(question["numero"])
            except (KeyError, TypeError, ValueError):
                excluded["invalid_number"] += 1
                continue
            identity = (source_id, number)
            human_review = reviewed_states.get(str(number)) or {}
            status = human_review.get("status")
            if status in ("unusable", "bad_crop"):
                excluded[status] += 1
                continue
            question = question_review.apply_edits(question, human_review)
            if identity in seen_questions:
                excluded["duplicate_question"] += 1
                continue
            seen_questions.add(identity)
            raw_subject = human_review.get("subject")
            subject = canonical_subject(raw_subject, subjects) if raw_subject else None
            reason = question_review.diagnostic_ready(human_review, subject or raw_subject or None)
            if reason:
                excluded[reason] += 1
                continue
            if not subject:
                excluded["unclassified"] += 1
                continue
            answer = keys.get(number)
            alternatives = question.get("alternativas") or {}
            if answer not in alternatives:
                excluded["no_valid_answer_key"] += 1
                continue
            reviewed_content = CONTENT_INDEX[str(human_review["content_id"])]
            classification = dict(question.get("classificacao") or {})
            classification.update(materia=subject, assunto=reviewed_content["name"],
                                  conteudo_principal_id=reviewed_content["id"],
                                  conteudos_relacionados_ids=human_review.get("related_content_ids", []),
                                  caminho_conteudo=" → ".join(reviewed_content["path"]))
            topic = str(classification.get("caminho_conteudo") or classification.get("assunto") or "").strip()
            if not topic:
                topic = "Tópico não informado"
            question_copy = dict(question)
            question_copy["classificacao"] = classification
            question_copy["_source_dir"] = str(path.parent.resolve())
            shared = shared_texts.get(question.get("texto_id") or "")
            if shared and shared.get("texto"):
                question_copy["_texto_compartilhado"] = shared["texto"]
                question_copy["_figuras_compartilhadas"] = list(shared.get("figuras") or [])
            pool.append({"source_id": source_id, "source_file": str(path), "number": number,
                         "subject": subject, "topic": topic, "question": question_copy,
                         "correct_answer": answer})
    return pool, dict(excluded)


EXCLUSION_LABELS = {
    "no_valid_answer_key": "sem gabarito importado",
    "pending_review": "sem revisão humana",
    "invalid_content": "com conteúdo revisado inválido",
    "missing_subject": "sem matéria revisada",
    "unclassified": "de matérias fora do curso",
    "unusable": "marcadas como inutilizáveis",
    "bad_crop": "com recorte ruim",
    "duplicate_question": "duplicadas",
    "invalid_number": "sem número válido",
    "json_invalid": "arquivos de prova ilegíveis",
}


def empty_pool_message(excluded: dict) -> str:
    """Explica por que nenhuma questão entrou no sorteio, maior motivo primeiro."""
    message = "não há questões classificadas com gabarito para este curso"
    if not excluded:
        return message + " (nenhuma prova processada encontrada)"
    parts = [f"{count} {EXCLUSION_LABELS.get(reason, reason)}"
             for reason, count in sorted(excluded.items(), key=lambda item: (-item[1], item[0]))]
    hint = ""
    if "no_valid_answer_key" in excluded:
        hint = "; importe o gabarito de cada prova na tela de provas"
    return f"{message} ({', '.join(parts)}{hint})"


def allocate_counts(available: dict[str, int], weights: dict[str, float], total: int) -> dict[str, int]:
    """Distribui cotas pelo método dos maiores restos, respeitando a capacidade."""
    if total < 1:
        raise ValueError("quantidade deve ser positiva")
    caps = {s: max(0, int(available.get(s, 0))) for s in weights}
    allocated = {s: 0 for s in weights}
    remaining = min(total, sum(caps.values()))
    while remaining:
        active = [s for s in weights if allocated[s] < caps[s]]
        if not active:
            break
        weight_sum = sum(float(weights[s]) for s in active)
        shares = {s: remaining * float(weights[s]) / weight_sum for s in active}
        floors = {s: min(caps[s] - allocated[s], int(shares[s])) for s in active}
        placed = sum(floors.values())
        for s, count in floors.items():
            allocated[s] += count
        remaining -= placed
        if not remaining:
            break
        ranked = sorted((s for s in active if allocated[s] < caps[s]),
                        key=lambda s: (-(shares[s] - int(shares[s])), s.casefold()))
        if not ranked:
            break
        one = min(remaining, len(ranked))
        for subject in ranked[:one]:
            allocated[subject] += 1
        remaining -= one
    return {s: n for s, n in allocated.items() if n}


def deterministic_topics(pool: list[dict], subjects: list[str]) -> dict[str, list[str]]:
    topics: dict[str, set[str]] = defaultdict(set)
    for item in pool:
        topics[item["subject"]].add(item["topic"])
    return {s: sorted(topics.get(s, set()), key=str.casefold) for s in subjects if topics.get(s)}


def validate_topic_plan(obj: dict, available: dict[str, list[str]]) -> dict[str, list[str]]:
    if not isinstance(obj, dict) or set(obj) != {"topicos"} or not isinstance(obj["topicos"], dict):
        raise ValueError("formato esperado: {\"topicos\": {matéria: [tópicos]}}")
    normalized = {norm(subject): (subject, topics) for subject, topics in available.items()}
    plan: dict[str, list[str]] = {}
    errors = []
    if set(norm(str(s)) for s in obj["topicos"]) != set(normalized):
        errors.append("inclua exatamente todas as matérias disponíveis, sem matérias extras")
    for raw_subject, raw_topics in obj["topicos"].items():
        entry = normalized.get(norm(str(raw_subject)))
        if not entry:
            continue
        subject, topics = entry
        if not isinstance(raw_topics, list) or not raw_topics:
            errors.append(f"{subject}: forneça uma lista não vazia de tópicos")
            continue
        known = {norm(topic): topic for topic in topics}
        selected: list[str] = []
        for raw_topic in raw_topics:
            topic = known.get(norm(str(raw_topic)))
            if not topic:
                errors.append(f"{subject}: tópico desconhecido {raw_topic!r}")
            elif topic not in selected:
                selected.append(topic)
        if selected:
            plan[subject] = selected
    if errors:
        raise ValueError("; ".join(errors))
    return plan


def plan_topics(provider, profile: dict, available: dict[str, list[str]]) -> tuple[dict, dict]:
    system = ("Você planeja cobertura temática para um diagnóstico de vestibular. "
              "Você não calcula quantidades, não escolhe questões e não gera IDs. "
              "Retorne somente JSON no formato {\"topicos\": {\"Matéria\": [\"tópico existente\"]}}.")
    user = json.dumps({"curso": profile["nome"], "disciplinas_especificas": profile.get("disciplinas_especificas", []),
                       "topicos_disponiveis": available,
                       "tarefa": "Escolha uma ordem de tópicos ampla para cada matéria, usando apenas tópicos disponíveis. "
                                 "Não informe quantidade de questões nem identificadores."}, ensure_ascii=False)
    error = None

    def ask():
        nonlocal error
        for attempt in range(2):
            prompt = user if attempt == 0 else user + "\n\nSua resposta anterior falhou: " + str(error) + ". Corrija o JSON."
            try:
                result = validate_topic_plan(clf.extract_json(provider.complete(system, prompt)), available)
                return {"topics": result, "attempts": attempt + 1}
            except Exception as exc:
                error = str(exc)
        return None

    # Mesmo curso e mesmos tópicos disponíveis: o plano validado é reaproveitado.
    planned = cached_ai_result("diagnostic_topic_plan", provider, system, user, ask)
    if planned is not None:
        return planned["topics"], {"status": "llm", "attempts": planned["attempts"], "error": None}
    return deterministic_topics_from_available(available), {
        "status": "deterministic_fallback", "attempts": 2, "error": error}


def deterministic_topics_from_available(available: dict[str, list[str]]) -> dict[str, list[str]]:
    return {s: list(topics) for s, topics in available.items() if topics}


def select_questions(pool: list[dict], weights: dict[str, float], total: int,
                     topics: dict[str, list[str]], seed: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for item in pool:
        if item["subject"] in weights:
            by_subject[item["subject"]].append(item)
    available = {s: len(by_subject.get(s, [])) for s in weights}
    targets = allocate_counts(available, weights, total)
    selected: list[dict] = []
    for subject in weights:
        count = targets.get(subject, 0)
        if not count:
            continue
        by_topic: dict[str, list[dict]] = defaultdict(list)
        for item in by_subject[subject]:
            by_topic[item["topic"]].append(item)
        for values in by_topic.values():
            rng.shuffle(values)
        preferred = [t for t in topics.get(subject, []) if t in by_topic]
        topic_order = preferred + [t for t in sorted(by_topic, key=str.casefold) if t not in preferred]
        taken = 0
        while taken < count:
            progress = False
            for topic in topic_order:
                if by_topic[topic] and taken < count:
                    selected.append(by_topic[topic].pop())
                    taken += 1
                    progress = True
            if not progress:
                break
    rng.shuffle(selected)
    return selected, {"requested": total, "selected": len(selected), "available_by_subject": available,
                      "selected_by_subject": dict(Counter(q["subject"] for q in selected)),
                      "excluded": {}}


def effective_weights(profile: dict, pool: list[dict]) -> dict:
    """Pesos efetivos do curso (``courses.resolve_weights``): fórmula FUVEST ou ``pesos`` manuais."""
    return courses.resolve_weights(profile, pool)


def selection_weights(profile: dict, weights_result: dict) -> dict[str, float]:
    """Pesos usados para sortear/alocar: derivados quando o perfil tem ``formula``."""
    return dict(weights_result["weights"]) if profile.get("formula") else dict(profile["pesos"])


def course_weight_summary(profile: dict, pool: list[dict]) -> dict:
    """Pesos efetivos de um curso para exibição: ``{"weights", "non_assessable", "formula", "error"}``."""
    course_pool = [item for item in pool if item.get("subject") in profile.get("pesos", {})]
    try:
        result = effective_weights(profile, course_pool)
    except (ValueError, KeyError, ZeroDivisionError) as exc:
        return {"weights": None, "non_assessable": {}, "formula": profile.get("formula"), "error": str(exc)}
    return {"weights": result["weights"], "non_assessable": result["non_assessable"],
            "formula": result["formula"], "error": None}


def _candidate(item: dict) -> dict:
    return {"source_id": item["source_id"], "number": item["number"], "subject": item["subject"],
            "topic": item["topic"], "question": item["question"], "correct_answer": item["correct_answer"]}


def _content_ids(question: dict) -> tuple[str | None, list[str]]:
    classification = question.get("classificacao") or {}
    return (classification.get("conteudo_principal_id"),
            [value for value in (classification.get("conteudos_relacionados_ids") or []) if value])


def _candidate_response(candidate: dict) -> dict:
    principal, related = _content_ids(candidate["question"])
    return {"correct": False, "subject": candidate["subject"], "principal_id": principal,
            "related_ids": related, "answered_at": None}


def _node_score(node: dict) -> float:
    """Valor de perguntar sobre o nó: peso do curso × incidência × incerteza (largura do IC)."""
    return node["course_weight"] * node["incidence"] * (node["upper"] - node["lower"])


def _target_node(nodes: dict[str, dict], reachable: set[str]) -> str | None:
    """Área de maior valor; desce para conteúdos/folhas quando a área já tem evidência e não está resolvida.

    Áreas ainda não resolvidas (``_area_settled``) vêm antes das resolvidas, para
    que uma lacuna já confirmada não consuma o orçamento da matéria inteira.
    """
    areas = [node for node in nodes.values() if node["level"] == "area" and node["id"] in reachable]
    if not areas:
        return None
    target = max(areas, key=lambda node: (not _area_settled(node), _node_score(node), node["id"]))
    while target["attempts"] >= 1 and target["status"] in ("gap", "uncertain"):
        children = [node for node in nodes.values()
                    if node["parent_id"] == target["id"] and node["id"] in reachable]
        if not children:
            break
        target = max(children, key=lambda node: (_node_score(node), node["id"]))
    return target["id"]


def _area_settled(node: dict) -> bool:
    """Área com evidência suficiente para uma conclusão (ver ``adaptive_stop_reason``)."""
    if node["attempts"] < STOP_MIN_AREA_ANSWERS:
        return False
    return (node["upper"] - node["lower"] < STOP_WIDTH or node["upper"] <= STOP_GAP_UPPER
            or node["lower"] >= STOP_STRONG_LOWER)


def adaptive_stop_reason(nodes: dict[str, dict], plan: dict, rows: list[dict]) -> str | None:
    """Regra de parada do diagnóstico adaptativo.

    * ``budget``: o orçamento total de questões foi atingido.
    * ``converged``: toda matéria com orçamento já tem ao menos uma resposta e
      toda área *relevante* está resolvida. Relevantes são as áreas com
      incidência ≥ ``STOP_MIN_INCIDENCE`` nas matérias de maior peso (peso ≥
      ``STOP_TOP_WEIGHT_SHARE`` × maior peso do curso). Uma área está resolvida
      quando tem ≥ ``STOP_MIN_AREA_ANSWERS`` respostas e o intervalo é estreito
      (``upper − lower < STOP_WIDTH``) ou já separado — claramente lacuna
      (``upper ≤ STOP_GAP_UPPER``) ou claramente dominada (``lower ≥
      STOP_STRONG_LOWER``) — ou quando a matéria já gastou seu orçamento (não
      há como obter mais evidência).
    """
    budget = plan["budget"]
    if len(rows) >= budget["total"]:
        return "budget"
    by_subject = budget["by_subject"]
    answered = Counter(row["subject"] for row in rows if row.get("answered_at") is not None)
    if any(answered[subject] < 1 for subject, count in by_subject.items() if count > 0):
        return None
    asked = Counter(row["subject"] for row in rows)
    weights = mastery.normalize_weights(plan["weights"]["weights"])
    top = max(weights.values(), default=0.0)
    top_subjects = {subject for subject, weight in weights.items()
                    if weight >= STOP_TOP_WEIGHT_SHARE * top and by_subject.get(subject)}
    relevant = [node for node in nodes.values()
                if node["level"] == "area" and node["subject"] in top_subjects
                and node["incidence"] >= STOP_MIN_INCIDENCE]
    if all(_area_settled(node) or asked[node["subject"]] >= by_subject[node["subject"]]
           for node in relevant):
        return "converged"
    return None


def next_question(plan: dict, rows: list[dict], *, seed: int) -> tuple[dict | None, str | None]:
    """Escolhe a próxima questão do diagnóstico adaptativo (função pura).

    ``plan`` traz ``weights``, ``incidence``, ``budget`` e ``candidates``;
    ``rows`` são as linhas de ``attempt_questions`` já inseridas. Devolve
    ``(candidato, None)`` ou ``(None, motivo_de_parada)`` — ``budget``,
    ``converged`` ou ``exhausted``.

    Política: cobertura primeiro (toda matéria com orçamento recebe uma
    questão, na ordem do peso); depois o nó-alvo é a área de maior
    ``peso × incidência × (upper − lower)`` entre matérias com orçamento,
    descendo para conteúdo e folha quando a área já foi testada e segue
    lacuna/incerta; áreas já resolvidas só voltam a ser escolhidas quando não há
    outra. Entre as questões sob o alvo, prefere a que toca mais nós
    incertos; empates são resolvidos pela semente (determinístico).
    """
    weights = plan["weights"]["weights"]
    responses = [mastery.response_from_row(row) for row in rows if row.get("answered_at") is not None]
    nodes = mastery.compute_nodes(responses, subject_weights=weights, incidence=plan.get("incidence") or {})
    stop = adaptive_stop_reason(nodes, plan, rows)
    if stop:
        return None, stop
    asked = Counter(row["subject"] for row in rows)
    used = {(row["source_id"], int(row["question_number"])) for row in rows}
    remaining = {subject for subject, count in plan["budget"]["by_subject"].items() if asked[subject] < count}
    open_items = [item for item in plan.get("candidates") or []
                  if item["subject"] in remaining and (item["source_id"], int(item["number"])) not in used]
    if not open_items:
        return None, "exhausted"
    uncovered = sorted({item["subject"] for item in open_items if asked[item["subject"]] == 0},
                       key=lambda subject: (-float(weights.get(subject, 0)), subject.casefold()))
    if uncovered:
        open_items = [item for item in open_items if item["subject"] == uncovered[0]]
    paths: list[set[str]] = []
    for item in open_items:
        principal, _ = _content_ids(item["question"])
        node = CONTENT_INDEX.get(principal or "")
        paths.append(set(node["path_ids"]) if node else set())
    target = _target_node(nodes, set().union(*paths))
    indexes = [i for i, path in enumerate(paths) if target in path] if target else []
    indexes = indexes or list(range(len(open_items)))

    def touched(index: int) -> int:
        return sum(1 for identity in mastery.evidence_weights(_candidate_response(open_items[index]))
                   if nodes.get(identity, {}).get("status", "untested") in ("uncertain", "untested"))

    best = max(touched(i) for i in indexes)
    ties = sorted((open_items[i] for i in indexes if touched(i) == best),
                  key=lambda item: (item["source_id"], int(item["number"])))
    rng = random.Random(f"{seed}:{len(rows) + 1}")
    return rng.choice(ties), None


def _insert_question(db: sqlite3.Connection, attempt_id: str, ordinal: int, item: dict) -> None:
    db.execute("""INSERT INTO attempt_questions(attempt_id,ordinal,source_id,question_number,
        subject,topic,question_json,correct_answer) VALUES(?,?,?,?,?,?,?,?)""",
               (attempt_id, ordinal, item["source_id"], item["number"], item["subject"], item["topic"],
                json.dumps(item["question"], ensure_ascii=False), item["correct_answer"]))


def create_attempt(db: sqlite3.Connection, config: dict, course_id: str, pool: list[dict],
                   *, total: int | None = None, seed: int, provider=None, planner_topics: dict | None = None,
                   planner_meta: dict | None = None, excluded: dict | None = None,
                   mode: str = "adaptive") -> dict:
    """Cria uma tentativa de diagnóstico.

    ``mode="static"`` sorteia todas as questões de uma vez (comportamento
    original). ``mode="adaptive"`` (padrão) reserva um orçamento por matéria
    e insere uma questão por vez: a primeira aqui, as seguintes em
    ``record_response`` via ``next_question``.
    """
    if mode not in ("static", "adaptive"):
        raise ValueError("modo de diagnóstico inválido: use adaptive ou static")
    total = total if total is not None else (DEFAULT_ADAPTIVE_TOTAL if mode == "adaptive" else DEFAULT_STATIC_TOTAL)
    if mode == "adaptive" and not 1 <= total <= MAX_ADAPTIVE_TOTAL:
        raise ValueError(f"diagnóstico adaptativo aceita de 1 a {MAX_ADAPTIVE_TOTAL} questões")
    profile = course_profile(config, course_id)
    subjects = list(profile["pesos"])
    course_pool = [item for item in pool if item["subject"] in profile["pesos"]]
    weights_result = effective_weights(profile, course_pool)
    weights = selection_weights(profile, weights_result)
    incidence = mastery.node_incidence(course_pool)
    if mode == "adaptive":
        available = dict(Counter(item["subject"] for item in course_pool if item["subject"] in weights))
        by_subject = allocate_counts(available, weights, total) if available else {}
        budget = {"total": sum(by_subject.values()), "requested": total, "by_subject": by_subject}
        candidates = [_candidate(item) for item in course_pool if by_subject.get(item["subject"])]
        plan = {"mode": "adaptive", "weights": weights_result, "incidence": incidence,
                "budget": budget, "candidates": candidates,
                "planner": {"status": "adaptive", "attempts": 0, "error": None},
                "policy": {"stop_width": STOP_WIDTH, "stop_min_incidence": STOP_MIN_INCIDENCE,
                           "stop_top_weight_share": STOP_TOP_WEIGHT_SHARE,
                           "stop_min_area_answers": STOP_MIN_AREA_ANSWERS,
                           "stop_gap_upper": STOP_GAP_UPPER, "stop_strong_lower": STOP_STRONG_LOWER}}
        first, _ = next_question(plan, [], seed=seed) if candidates else (None, "exhausted")
        selected = [first] if first else []
        distribution = {"requested": total, "selected": len(selected), "available_by_subject": available,
                        "selected_by_subject": dict(Counter(q["subject"] for q in selected)),
                        "budget_by_subject": by_subject}
        planner_meta = plan["planner"]
    else:
        available_topics = deterministic_topics(course_pool, subjects)
        if planner_topics is None:
            # A composição do diagnóstico é determinística; o LLM não escolhe a
            # cobertura de tópicos nem pode tornar a criação dependente de rede.
            planner_topics = deterministic_topics_from_available(available_topics)
            planner_meta = {"status": "deterministic", "attempts": 0, "error": None}
        selected, distribution = select_questions(course_pool, weights, total, planner_topics, seed)
        plan = {"mode": "static", "topics": planner_topics, "planner": planner_meta or {"status": "provided"},
                "weights": weights_result, "incidence": incidence}
    distribution["excluded"] = excluded or {}
    distribution["unavailable_subjects"] = [s for s in subjects if not distribution["available_by_subject"].get(s)]
    plan["distribution"] = distribution
    if not selected:
        raise ValueError("nenhuma questão elegível com classificação e gabarito; importe gabaritos e confira o banco")
    attempt_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("""INSERT INTO attempts(id,course_id,course_name,config_version,profile_snapshot,seed,
            requested_count,actual_count,status,plan_json,created_at,mode)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (attempt_id, course_id, profile["nome"], config["version"],
                    json.dumps(profile, ensure_ascii=False), seed,
                    plan["budget"]["total"] if mode == "adaptive" else total, len(selected), "in_progress",
                    json.dumps(plan, ensure_ascii=False), now, mode))
        for ordinal, item in enumerate(selected, 1):
            _insert_question(db, attempt_id, ordinal, item)
    result = {"id": attempt_id, "course": profile["nome"], "seed": seed, "requested": total,
              "selected": len(selected), "distribution": distribution, "planner": planner_meta,
              "mode": mode, "weights": weights_result["weights"]}
    if mode == "adaptive":
        result["budget"] = plan["budget"]
    return result


def get_attempt(db: sqlite3.Connection, attempt_id: str) -> dict:
    row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if not row:
        raise ValueError(f"diagnóstico não encontrado: {attempt_id}")
    attempt = dict(row)
    for key in ("profile_snapshot", "plan_json", "metrics_json", "analysis_json"):
        attempt[key.removesuffix("_json")] = json.loads(attempt[key]) if attempt.get(key) else None
    return attempt


def list_questions_for_attempt(db: sqlite3.Connection, attempt_id: str, *, unanswered_only: bool = False):
    where = " AND answered_at IS NULL" if unanswered_only else ""
    return [dict(row) for row in db.execute(
        "SELECT * FROM attempt_questions WHERE attempt_id=?" + where + " ORDER BY ordinal", (attempt_id,))]


def _advance_adaptive(db: sqlite3.Connection, attempt_id: str) -> dict:
    """Acrescenta a próxima questão adaptativa (dentro da transação corrente)."""
    attempt = db.execute("SELECT seed,plan_json FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    plan = json.loads(attempt["plan_json"])
    rows = list_questions_for_attempt(db, attempt_id)
    if any(row["answered_at"] is None for row in rows):
        pending = min(row["ordinal"] for row in rows if row["answered_at"] is None)
        return {"next_ordinal": pending, "done": False}
    if plan.get("stop"):
        return {"next_ordinal": None, "done": True, "stop_reason": plan["stop"]["reason"]}
    item, stop = next_question(plan, rows, seed=int(attempt["seed"]))
    if item is None:
        # Ao parar, o banco de candidatos deixa de ser necessário (reduz o plano salvo).
        plan["stop"] = {"reason": stop, "questions": len(rows),
                        "at": datetime.now(timezone.utc).isoformat()}
        plan["candidate_count"] = len(plan.pop("candidates", None) or [])
        db.execute("UPDATE attempts SET plan_json=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), attempt_id))
        return {"next_ordinal": None, "done": True, "stop_reason": stop}
    ordinal = max((row["ordinal"] for row in rows), default=0) + 1
    _insert_question(db, attempt_id, ordinal, item)
    db.execute("UPDATE attempts SET actual_count=? WHERE id=?", (ordinal, attempt_id))
    return {"next_ordinal": ordinal, "done": False}


def grade_answer(answer: str | None, alternatives: dict, correct_answer: str,
                 reason: str | None, *, require_reason: bool = True) -> tuple[str | None, bool, str | None]:
    """Valida a alternativa e o motivo do erro; devolve (resposta, acertou, motivo)."""
    answer = (answer or "").strip().upper() or None
    if answer is not None and answer not in alternatives:
        raise ValueError("resposta deve ser uma das alternativas disponíveis")
    correct = answer is not None and answer == correct_answer
    if answer is None:
        reason = "blank"
    elif correct:
        if reason is not None:
            raise ValueError("resposta correta não recebe motivo de erro")
    elif reason is None and not require_reason:
        pass
    elif reason not in REASONS - {"blank"}:
        raise ValueError("resposta incorreta exige motivo: " + ", ".join(sorted(REASONS - {"blank"})))
    return answer, correct, reason


def answer_extras(confidence: str | None = None, elapsed_ms: object = None, hints_used: object = 0,
                  note: str | None = None) -> dict:
    """Valida os dados extras de uma resposta.

    ``confidence`` é ``sure``/``doubt``/``guess`` (ou vazio); ``elapsed_ms`` é limitado a
    30 min, para não contar uma aba esquecida aberta; ``note`` é a anotação livre do aluno.
    """
    confidence = (confidence or "").strip().lower() or None
    if confidence is not None and confidence not in CONFIDENCES:
        raise ValueError("confiança deve ser sure, doubt ou guess")
    if elapsed_ms is not None:
        if isinstance(elapsed_ms, bool):
            raise ValueError("tempo da resposta inválido")
        try:
            elapsed_ms = int(float(elapsed_ms))
        except (TypeError, ValueError):
            raise ValueError("tempo da resposta inválido") from None
        if elapsed_ms < 0:
            raise ValueError("tempo da resposta não pode ser negativo")
        elapsed_ms = min(elapsed_ms, MAX_ELAPSED_MS)
    if isinstance(hints_used, bool) or not isinstance(hints_used, (int, float)) or not 0 <= hints_used <= MAX_HINTS:
        raise ValueError(f"dicas usadas devem ficar entre 0 e {MAX_HINTS}")
    note = (note or "").strip() or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"a anotação deve ter até {MAX_NOTE_LENGTH} caracteres")
    return {"confidence": confidence, "elapsed_ms": elapsed_ms, "hints_used": int(hints_used), "note": note}


def record_response(db: sqlite3.Connection, attempt_id: str, ordinal: int,
                    answer: str | None, reason: str | None = None, *, require_reason: bool = True,
                    confidence: str | None = None, elapsed_ms: object = None, hints_used: object = 0,
                    note: str | None = None) -> dict:
    """Registra a resposta e informa a próxima questão.

    Resultado: ``{"ordinal", "answer", "correct", "reason", "next_ordinal",
    "done"}`` (+ ``"stop_reason"`` quando um adaptativo termina). Em tentativas
    adaptativas, a próxima questão é inserida aqui.
    """
    row = db.execute("SELECT * FROM attempt_questions WHERE attempt_id=? AND ordinal=?",
                     (attempt_id, ordinal)).fetchone()
    if not row:
        raise ValueError(f"questão {ordinal} não pertence ao diagnóstico")
    if row["answered_at"] is not None:
        raise ValueError(f"questão {ordinal} já respondida")
    alternatives = json.loads(row["question_json"]).get("alternativas", {})
    answer, correct, reason = grade_answer(answer, alternatives, row["correct_answer"], reason,
                                           require_reason=require_reason)
    extras = answer_extras(confidence if answer else None, elapsed_ms, hints_used, note)
    mode_row = db.execute("SELECT mode FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    with db:
        db.execute("UPDATE attempt_questions SET selected_answer=?,error_reason=?,is_correct=?,answered_at=?,"
                   "confidence=?,elapsed_ms=?,hints_used=?,note=? WHERE attempt_id=? AND ordinal=?",
                   (answer, reason, int(correct), datetime.now(timezone.utc).isoformat(), extras["confidence"],
                    extras["elapsed_ms"], extras["hints_used"], extras["note"], attempt_id, ordinal))
        if mode_row and mode_row["mode"] == "adaptive":
            progress = _advance_adaptive(db, attempt_id)
        else:
            pending = db.execute("SELECT MIN(ordinal) FROM attempt_questions WHERE attempt_id=? "
                                 "AND answered_at IS NULL", (attempt_id,)).fetchone()[0]
            progress = {"next_ordinal": pending, "done": pending is None}
    return {"ordinal": ordinal, "answer": answer, "correct": bool(correct), "reason": reason,
            "confidence": extras["confidence"], **progress}


def attempt_weights(attempt: dict) -> dict:
    """Resultado de pesos da tentativa (``plan.weights``) ou fallback para tentativas antigas."""
    plan = attempt.get("plan") or {}
    if isinstance(plan.get("weights"), dict) and plan["weights"].get("weights"):
        return plan["weights"]
    profile = attempt["profile_snapshot"]
    manual = {s: float(v) for s, v in profile["pesos"].items()}
    total = sum(manual.values()) or 1.0
    non_assessable = {courses.REDACAO: round(0.5 / 3, 4)} if profile.get("formula") else {}
    return {"weights": {s: round(v / total, 4) for s, v in manual.items()}, "raw": manual, "components": {},
            "non_assessable": non_assessable, "formula": None, "first_phase_sample": None}


def calculate_metrics(db: sqlite3.Connection, attempt_id: str) -> dict:
    attempt = get_attempt(db, attempt_id)
    profile = attempt["profile_snapshot"]
    rows = list_questions_for_attempt(db, attempt_id)
    by_subject: dict[str, list[dict]] = defaultdict(list)
    by_content: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["answered_at"] is not None:
            by_subject[row["subject"]].append(row)
            question = json.loads(row["question_json"])
            classification = question.get("classificacao") or {}
            assigned = [classification.get("conteudo_principal_id"),
                       *(classification.get("conteudos_relacionados_ids") or [])]
            for content_id in aggregate_content_ids([value for value in assigned if value]):
                by_content[content_id].append(row)
    metrics = {}
    for subject in profile["pesos"]:
        answered = by_subject.get(subject, [])
        count = len(answered)
        correct = sum(int(r["is_correct"] or 0) for r in answered)
        reasons = Counter(r["error_reason"] for r in answered if r["error_reason"])
        metrics[subject] = {"attempts": count, "correct": correct,
                            "accuracy": round(correct / count, 4) if count else None,
                            "error_reasons": {r: reasons.get(r, 0) for r in sorted(REASONS)},
                            "status": "assessed" if count >= 3 else "insufficient_data"}
    answered_count = sum(x["attempts"] for x in metrics.values())
    correct_count = sum(x["correct"] for x in metrics.values())
    content_metrics = {}
    for content_id, content_rows in by_content.items():
        node = CONTENT_INDEX.get(content_id)
        if not node:
            continue
        count = len(content_rows)
        correct = sum(int(row["is_correct"] or 0) for row in content_rows)
        reasons = Counter(row["error_reason"] for row in content_rows if row["error_reason"])
        content_metrics[content_id] = {
            "name": node["name"], "path": " → ".join(node["path"]),
            "depth": len(node["path_ids"]), "attempts": count, "correct": correct,
            "accuracy": round(correct / count, 4) if count else None,
            "error_reasons": {reason: reasons.get(reason, 0) for reason in sorted(REASONS)},
            "status": "assessed" if count >= 3 else "insufficient_data",
        }
    weights_result = attempt_weights(attempt)
    incidence = (attempt.get("plan") or {}).get("incidence") or \
        mastery.node_incidence([mastery.response_from_row(row) for row in rows])
    responses = [mastery.response_from_row(row) for row in rows if row["answered_at"] is not None]
    nodes = mastery.compute_nodes(responses, subject_weights=weights_result["weights"], incidence=incidence)
    return {"total_questions": len(rows), "answered": answered_count,
            "correct": correct_count, "incorrect_or_blank": answered_count - correct_count,
            "accuracy": round(correct_count / answered_count, 4) if answered_count else None,
            "subjects": metrics, "contents": content_metrics,
            "taxonomy_version": CONTENT_TAXONOMY_VERSION,
            "mode": attempt.get("mode") or "static",
            "course_weights": weights_result["weights"],
            "nodes": nodes,
            "report": build_report(nodes, weights_result, profile)}


def validate_analysis(obj: dict, metrics: dict, subjects: list[str]) -> dict:
    if not isinstance(obj, dict) or set(obj) != {"summary", "steps"}:
        raise ValueError("retorne somente os campos summary e steps")
    if not isinstance(obj["summary"], str) or not obj["summary"].strip():
        raise ValueError("summary deve ser texto não vazio")
    if not isinstance(obj["steps"], list):
        raise ValueError("steps deve ser uma lista")
    steps = []
    allowed = {norm(s): s for s in subjects}
    required = {"subject", "action", "reason_code", "rationale", "exit_criterion"}
    for idx, step in enumerate(obj["steps"], 1):
        if not isinstance(step, dict) or set(step) != required:
            raise ValueError(f"etapa {idx}: campos esperados {sorted(required)}")
        subject = allowed.get(norm(step["subject"]))
        if not subject:
            raise ValueError(f"etapa {idx}: matéria desconhecida")
        if metrics["subjects"][subject]["status"] != "assessed":
            raise ValueError(f"etapa {idx}: {subject} tem menos de 3 tentativas e não pode receber conclusão")
        if step["action"] not in ACTIONS:
            raise ValueError(f"etapa {idx}: ação inválida")
        if step["reason_code"] not in REASONS:
            raise ValueError(f"etapa {idx}: reason_code inválido")
        if step["action"] != ACTION_BY_REASON[step["reason_code"]]:
            raise ValueError(f"etapa {idx}: motivo {step['reason_code']} exige ação {ACTION_BY_REASON[step['reason_code']]}")
        if metrics["subjects"][subject]["error_reasons"].get(step["reason_code"], 0) < 1:
            raise ValueError(f"etapa {idx}: motivo {step['reason_code']} não aparece nos dados de {subject}")
        if not all(isinstance(step[k], str) and step[k].strip() for k in ("rationale", "exit_criterion")):
            raise ValueError(f"etapa {idx}: justificativa e critério são obrigatórios")
        if not re.search(r"\d", step["exit_criterion"]):
            raise ValueError(f"etapa {idx}: critério de saída precisa ser mensurável")
        low_data_subjects = [s for s, m in metrics["subjects"].items() if m["status"] == "insufficient_data"]
        text = norm(step["rationale"] + " " + step["exit_criterion"])
        for low_subject in low_data_subjects:
            tokens = {norm(low_subject)}
            tokens.update(norm(alias) for alias, target in ALIASES.items()
                          if norm(target) == norm(low_subject))
            if any(token and re.search(rf"(?<!\w){re.escape(token)}(?!\w)", text) for token in tokens):
                raise ValueError(f"etapa {idx}: não faça afirmações sobre {low_subject}, com menos de 3 tentativas")
        steps.append({**step, "subject": subject})
    seen = [step["subject"] for step in steps]
    if len(set(seen)) != len(seen):
        raise ValueError("não repita uma matéria em várias etapas")
    if not steps and any(m["status"] == "assessed" and any(m["error_reasons"].values())
                         for m in metrics["subjects"].values()):
        raise ValueError("há erros em matérias avaliadas; informe ao menos uma prioridade de estudo")
    enough = [s for s, m in metrics["subjects"].items() if m["status"] == "assessed"]
    insufficient = [s for s, m in metrics["subjects"].items() if m["status"] == "insufficient_data"]
    summary = ("Matérias com dados suficientes: " + (", ".join(enough) if enough else "nenhuma") + ". "
               + "Sem conclusão por falta de 3 tentativas: "
               + (", ".join(insufficient) if insufficient else "nenhuma") + ".")
    return {"summary": summary, "steps": steps}


def analyze(provider, metrics: dict, profile: dict) -> tuple[dict | None, str | None]:
    subjects = list(profile["pesos"])
    system = ("Você interpreta resultados de um diagnóstico FUVEST e propõe um caminho inicial de estudo. "
              "Os números recebidos já foram calculados pelo programa: não os recalcule, altere ou infira dados. "
              "Não conclua nada sobre matérias com status insufficient_data. "
              "Use ações apenas " + ", ".join(sorted(ACTIONS)) + ". "
              "Associe cada motivo a uma ação: "
              + ", ".join(f"{reason} a {action}" for reason, action in sorted(ACTION_BY_REASON.items())) + ". "
              "Devolva JSON sem markdown: {\"summary\": texto, \"steps\": [{\"subject\": matéria, "
              "\"action\": ação, \"reason_code\": " + "|".join(sorted(REASONS)) + ", "
              "\"rationale\": texto, \"exit_criterion\": critério mensurável com número}]}.")
    user = json.dumps({"course": profile["nome"], "configured_course_weights": profile["pesos"],
                       "course_specific_subjects": profile.get("disciplinas_especificas", []),
                       "metrics_calculated_by_code": metrics,
                       "task": "Ordene as prioridades por sequência no array steps. Recomende estudo apenas para matérias "
                              "com pelo menos três tentativas; conecte ação e reason_code aos dados. "
                              "Não acrescente estatísticas.",
                       "criterion_examples": ["Acertar pelo menos 8 de 10 exercícios inéditos do tópico.",
                                             "Explicar 4 de 5 conceitos-chave e acertar 8 de 10 questões."],
                       "valid_actions": sorted(ACTIONS), "valid_reasons": sorted(REASONS),
                       "valid_subjects": subjects}, ensure_ascii=False)
    error = None

    def ask():
        nonlocal error
        for attempt in range(2):
            prompt = user if attempt == 0 else user + "\n\nA resposta anterior é inválida: " + str(error) + ". Corrija-a."
            try:
                return validate_analysis(clf.extract_json(provider.complete(system, prompt)), metrics, subjects)
            except Exception as exc:
                error = str(exc)
        return None

    # Concluir de novo a mesma tentativa (ou métricas idênticas) não chama a IA outra vez.
    result = cached_ai_result("diagnostic_analysis", provider, system, user, ask)
    return (result, None) if result is not None else (None, error)


def finalize_attempt(db: sqlite3.Connection, attempt_id: str, provider, *, include_analysis: bool = True) -> dict:
    attempt = get_attempt(db, attempt_id)
    pending = list_questions_for_attempt(db, attempt_id, unanswered_only=True)
    if pending:
        raise ValueError(f"ainda faltam {len(pending)} questões; responda todas antes de concluir")
    metrics = calculate_metrics(db, attempt_id)
    analysis, error = analyze(provider, metrics, attempt["profile_snapshot"]) if include_analysis else (None, None)
    status = "completed" if analysis is not None or not include_analysis else "analysis_failed"
    with db:
        db.execute("UPDATE attempts SET status=?,metrics_json=?,analysis_json=?,analysis_status=?,analysis_error=? WHERE id=?",
                   (status, json.dumps(metrics, ensure_ascii=False),
                    json.dumps(analysis, ensure_ascii=False) if analysis else None,
                    status, error, attempt_id))
    return {"metrics": metrics, "analysis": analysis, "analysis_status": status,
            "analysis_error": error, "analysis_included": include_analysis}
