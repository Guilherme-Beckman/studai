"""Resolução comentada de uma questão: passo a passo, por que cada alternativa errada
está errada e três dicas graduais.

A IA gera uma vez por questão (com o gabarito no prompt) e a resposta só é aceita
se confirmar o gabarito, comentar todas as alternativas erradas e não entregar a
letra nas dicas. O aluno aprova ou aponta um erro; com a crítica, a resolução é
gerada de novo. O resultado fica na tabela ``question_explanations`` do banco de
histórico, por ``(source_id, question_number)``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification import service as clf
from studai.integrations.ai_cache import cached_ai_result

PROMPT_VERSION = "explanation-v1"
STATUSES = ("generated", "approved", "rejected")
MAX_STEPS = 8
HINTS = 3
MAX_FEEDBACK_LENGTH = 1000

SYSTEM = """Você é professor de cursinho e escreve a resolução comentada de uma questão de múltipla escolha da 1ª fase da FUVEST para um aluno que acabou de respondê-la.
Recebe a questão e a alternativa correta (o gabarito oficial, que não deve ser contestado).
Escreva em português do Brasil, com frases curtas e diretas, como numa conversa com o aluno.
Fórmulas em texto simples com símbolos (√, ², ³, π, °, ·, ≤, ≥, ≠, ×, ÷), sem LaTeX e sem $: a tela não renderiza LaTeX.
- key_concept: o conceito ou a habilidade que a questão cobra, em uma frase.
- steps: os passos da resolução, em ordem, sem numerar (a tela numera). Cada passo traz conteúdo da questão: um dado, uma conta, um conceito aplicado. Não encha: nada de "marque a alternativa" ou "revise a resposta"; 2 a 5 passos costumam bastar, no máximo 8. Nas contas, mostre a conta.
- distractors: para cada alternativa errada, por que ela está errada (o erro que leva a ela, quando houver).
- hints: exatamente 3 dicas graduais, sem numerar, para quem ainda não respondeu: a 1ª só orienta o olhar, a 2ª aponta o caminho, a 3ª quase resolve. Nenhuma dica pode dizer a letra correta.
- Se a questão citar figura ou gráfico que não veio em texto, resolva com o que o enunciado e as alternativas permitem e diga no passo o que se lê na figura.
- difficulty: 1 (fácil), 2 (média) ou 3 (difícil) para um aluno de cursinho.
Devolva JSON sem markdown: {"answer": letra correta, "key_concept": texto, "steps": [textos], "distractors": {"<letra>": texto}, "hints": [3 textos], "difficulty": 1|2|3}"""


class ExplanationError(Exception):
    """A IA não devolveu uma resolução válida depois das tentativas."""


def ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS question_explanations (
        source_id TEXT NOT NULL, question_number INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('generated','approved','rejected')),
        content_json TEXT NOT NULL, model TEXT, prompt_version TEXT NOT NULL, feedback TEXT,
        created_at TEXT NOT NULL, reviewed_at TEXT,
        PRIMARY KEY(source_id, question_number))""")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shared(question: dict) -> dict | None:
    if not question.get("_texto_compartilhado"):
        return None
    return {"texto": question["_texto_compartilhado"], "figuras": question.get("_figuras_compartilhadas") or []}


def _hint_leaks(hint: str, letter: str) -> bool:
    """A dica cita a letra correta ("alternativa C", "letra (C)", "resposta é C")."""
    # A letra precisa estar em maiúscula: "resposta a partir de..." não é vazamento.
    linking = r"(?:\W+(?i:é|e|seria|fica|correta|certa|está|esta|na|em))*"
    return bool(re.search(rf"(?i:alternativa|letra|op[cç][aã]o|resposta|gabarito){linking}\W{{0,3}}\(?{letter}\)?(?!\w)",
                          hint))


_LATEX_SYMBOLS = {r"\pi": "π", r"\cdot": "·", r"\times": "×", r"\div": "÷", r"\leq": "≤", r"\le": "≤",
                  r"\geq": "≥", r"\ge": "≥", r"\neq": "≠", r"\approx": "≈", r"\circ": "°", r"\Delta": "Δ",
                  r"\alpha": "α", r"\beta": "β", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ", r"\infty": "∞",
                  r"\left": "", r"\right": "", r"\%": "%", r"\,": " ", r"\;": " ", r"\!": ""}
_SUPERSCRIPTS = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def plain_math(text: str) -> str:
    """LaTeX comum em texto simples: a tela mostra texto puro (``$30\\sqrt{2}$`` → ``30√2``)."""
    if "\\" not in text and "$" not in text:
        return text
    text = re.sub(r"\\text(?:rm|bf)?\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\{([^{}]*)\}", lambda m: f"√{m.group(1)}" if re.fullmatch(r"[\w.,]+", m.group(1))
                  else f"√({m.group(1)})", text)
    text = re.sub(r"\^\{?\\circ\}?", "°", text)
    text = re.sub(r"\^\{([0-9+\-]+)\}|\^([0-9])", lambda m: (m.group(1) or m.group(2)).translate(_SUPERSCRIPTS), text)
    for command, symbol in _LATEX_SYMBOLS.items():
        # Comando inteiro: "\le" não pode pegar o começo de "\left".
        boundary = r"(?![A-Za-z])" if command[-1].isalpha() else ""
        text = re.sub(re.escape(command) + boundary, lambda _: symbol, text)
    text = text.replace("$", "").replace("{", "").replace("}", "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _clean(text: str) -> str:
    """Tira a numeração que o modelo põe por conta própria ("1. ", "Passo 2:", "Dica 3 -") e o LaTeX."""
    return plain_math(re.sub(r"^\s*(?:(?:passo|dica)\s*)?\d+\s*[.):\-–]\s*", "", text.strip(), flags=re.I).strip())


def validate_explanation(obj: dict, alternatives: dict, correct: str) -> dict:
    """Confere a resolução da IA contra o gabarito; devolve só os campos esperados."""
    if not isinstance(obj, dict):
        raise ValueError("a resposta deve ser um objeto JSON")
    missing = {"answer", "key_concept", "steps", "distractors", "hints", "difficulty"} - set(obj)
    if missing:
        raise ValueError(f"faltam os campos {sorted(missing)}")
    if str(obj["answer"]).strip().strip("()").upper() != correct:
        raise ValueError(f"a resolução deve chegar ao gabarito oficial ({correct})")
    if not isinstance(obj["key_concept"], str) or not obj["key_concept"].strip():
        raise ValueError("key_concept deve ser texto não vazio")
    steps = obj["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS \
            or not all(isinstance(step, str) and step.strip() for step in steps):
        raise ValueError(f"steps deve ter de 1 a {MAX_STEPS} textos")
    distractors = obj["distractors"]
    wrong = sorted(set(alternatives) - {correct})
    if not isinstance(distractors, dict):
        raise ValueError("distractors deve ser um objeto por letra")
    distractors = {str(letter).strip().strip("()").upper(): text for letter, text in distractors.items()}
    if sorted(distractors) != wrong or not all(isinstance(text, str) and text.strip() for text in distractors.values()):
        raise ValueError(f"distractors deve comentar exatamente as alternativas {wrong}")
    hints = obj["hints"]
    if not isinstance(hints, list) or len(hints) != HINTS or not all(isinstance(h, str) and h.strip() for h in hints):
        raise ValueError(f"hints deve ter exatamente {HINTS} textos")
    if any(_hint_leaks(hint, correct) for hint in hints):
        raise ValueError("as dicas não podem dizer a alternativa correta")
    difficulty = obj["difficulty"]
    if isinstance(difficulty, bool) or difficulty not in (1, 2, 3):
        raise ValueError("difficulty deve ser 1, 2 ou 3")
    return {"answer": correct, "key_concept": plain_math(obj["key_concept"].strip()),
            "steps": [_clean(step) for step in steps],
            "distractors": {letter: plain_math(distractors[letter].strip()) for letter in wrong},
            "hints": [_clean(hint) for hint in hints], "difficulty": difficulty}


def build_prompt(entry: dict, *, previous: dict | None = None, feedback: str | None = None,
                 has_images: bool = False) -> str:
    question = entry["question"]
    payload = {"questao": clf.render_questao(question, _shared(question), has_images),
               "materia": entry.get("subject"), "conteudo": entry.get("topic"),
               "alternativa_correta": entry["correct_answer"]}
    if feedback:
        payload["resolucao_anterior"] = previous
        payload["critica_do_aluno"] = feedback
        payload["task"] = "Reescreva a resolução corrigindo o problema apontado pelo aluno."
    return json.dumps(payload, ensure_ascii=False)


def question_images(entry: dict) -> list[bytes]:
    """Figuras da questão (e do texto-base), para um provedor que aceita imagem."""
    question = entry["question"]
    source_dir = question.get("_source_dir")
    if not source_dir:
        return []
    return clf.load_images(Path(source_dir), question, _shared(question), "figuras")


def _images(entry: dict) -> list[bytes]:
    """Recortes para o provedor padrão, só com ``STUDAI_EXPLANATIONS_IMAGES=1``: as faixas
    grátis de nuvem são modelos só de texto e recusam mensagens com imagem."""
    if os.environ.get("STUDAI_EXPLANATIONS_IMAGES", "0") != "1":
        return []
    return question_images(entry)


def _record(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {"sourceId": row["source_id"], "number": int(row["question_number"]), "status": row["status"],
            "content": json.loads(row["content_json"]), "model": row["model"], "feedback": row["feedback"],
            "promptVersion": row["prompt_version"], "createdAt": row["created_at"], "reviewedAt": row["reviewed_at"]}


def get_explanation(db: sqlite3.Connection, source_id: str, number: int) -> dict | None:
    ensure_schema(db)
    return _record(db.execute("SELECT * FROM question_explanations WHERE source_id=? AND question_number=?",
                              (source_id, int(number))).fetchone())


def generate_explanation(db: sqlite3.Connection, entry: dict, provider, *, feedback: str | None = None,
                         images: list[bytes] | None = None) -> dict:
    """Gera (ou regenera, com a crítica do aluno) e grava a resolução da questão do pool.

    ``images``: figuras para um provedor com visão; sem elas, vale ``STUDAI_EXPLANATIONS_IMAGES``.
    """
    ensure_schema(db)
    correct = entry["correct_answer"]
    alternatives = entry["question"].get("alternativas") or {}
    if correct not in alternatives:
        raise ValueError("a questão não tem gabarito válido para resolver")
    previous = get_explanation(db, entry["source_id"], entry["number"])
    images = _images(entry) if images is None else images
    user = build_prompt(entry, previous=previous["content"] if previous and feedback else None, feedback=feedback,
                        has_images=bool(images))
    errors: list[str] = []

    def ask():
        for attempt in range(2):
            prompt = user if attempt == 0 else user + f"\n\nA resposta anterior é inválida: {errors[-1]}. Corrija-a."
            try:
                raw = provider.complete(SYSTEM, prompt, images or None)
                return validate_explanation(clf.extract_json(raw), alternatives, correct)
            except (ValueError, TypeError) as error:
                errors.append(str(error))
        return None

    content = cached_ai_result("question_explanation", provider, SYSTEM, user, ask)
    if content is None:
        raise ExplanationError(f"a IA não gerou uma resolução válida: {errors[-1] if errors else 'sem resposta'}")
    model = str(getattr(provider, "last_used_model", "") or getattr(provider, "label", "") or "")
    with db:
        db.execute("""INSERT INTO question_explanations(source_id,question_number,status,content_json,model,
            prompt_version,feedback,created_at,reviewed_at) VALUES(?,?,?,?,?,?,?,?,NULL)
            ON CONFLICT(source_id,question_number) DO UPDATE SET status=excluded.status,
            content_json=excluded.content_json, model=excluded.model, prompt_version=excluded.prompt_version,
            feedback=excluded.feedback, created_at=excluded.created_at, reviewed_at=NULL""",
                   (entry["source_id"], int(entry["number"]), "generated", json.dumps(content, ensure_ascii=False),
                    model, PROMPT_VERSION, feedback, _now()))
    return get_explanation(db, entry["source_id"], entry["number"])


def review_explanation(db: sqlite3.Connection, source_id: str, number: int, approved: bool,
                       feedback: str | None = None) -> dict:
    """O aluno aprova a resolução ou aponta o erro (que vai para a próxima geração)."""
    record = get_explanation(db, source_id, number)
    if record is None:
        raise ValueError("a questão ainda não tem resolução para revisar")
    feedback = (feedback or "").strip() or None
    if not approved and not feedback:
        raise ValueError("descreva o erro da resolução para gerar outra")
    if feedback and len(feedback) > MAX_FEEDBACK_LENGTH:
        raise ValueError(f"a crítica deve ter até {MAX_FEEDBACK_LENGTH} caracteres")
    with db:
        db.execute("UPDATE question_explanations SET status=?,feedback=?,reviewed_at=? "
                   "WHERE source_id=? AND question_number=?",
                   ("approved" if approved else "rejected", feedback, _now(), source_id, int(number)))
    return get_explanation(db, source_id, number)


def needs_generation(record: dict | None) -> bool:
    """Sem resolução, ou rejeitada pelo aluno (e ainda não regerada)."""
    return record is None or record["status"] == "rejected"


def public_payload(record: dict | None, *, answered: bool, hints: int = 0) -> dict:
    """O que a tela pode mostrar: antes de responder, só as dicas pedidas; depois, tudo."""
    if record is None:
        return {"status": "missing"}
    # Resoluções gravadas antes da limpeza do LaTeX também saem em texto simples.
    content = {**record["content"],
               "key_concept": plain_math(record["content"]["key_concept"]),
               "steps": [plain_math(step) for step in record["content"]["steps"]],
               "hints": [plain_math(hint) for hint in record["content"]["hints"]],
               "distractors": {letter: plain_math(text) for letter, text in record["content"]["distractors"].items()}}
    if not answered:
        count = max(0, min(int(hints), HINTS))
        return {"status": record["status"], "hints": content["hints"][:count], "hintsAvailable": HINTS}
    return {"status": record["status"], "feedback": record["feedback"], "model": record["model"],
            "hintsAvailable": HINTS, **content}


def question_answered(db: sqlite3.Connection, source_id: str, number: int, *, session_id: str | None = None,
                      item_id: str | None = None, attempt_id: str | None = None,
                      ordinal: int | None = None) -> bool:
    """A questão já foi respondida no contexto em que a tela a mostra.

    Treinamento: ``session_id`` + ``item_id``; diagnóstico ou prova: ``attempt_id`` +
    ``ordinal``. Simulado cronometrado só libera a resolução depois de corrigido.
    Sem contexto, vale qualquer resposta já registrada dessa questão.
    """
    if session_id and item_id:
        return db.execute("SELECT 1 FROM study_answers WHERE session_id=? AND item_id=? AND source_id=? "
                          "AND question_number=?", (session_id, item_id, source_id, int(number))).fetchone() is not None
    if attempt_id and ordinal is not None:
        row = db.execute("""SELECT aq.answered_at, a.status, a.plan_json FROM attempt_questions aq
            JOIN attempts a ON a.id = aq.attempt_id WHERE aq.attempt_id=? AND aq.ordinal=? AND aq.source_id=?
            AND aq.question_number=?""", (attempt_id, int(ordinal), source_id, int(number))).fetchone()
        if row is None or row["answered_at"] is None:
            return False
        timed = (json.loads(row["plan_json"] or "{}") or {}).get("timed")
        return not timed or row["status"] == "completed"
    return db.execute("SELECT 1 FROM study_answers WHERE source_id=? AND question_number=? UNION "
                      "SELECT 1 FROM attempt_questions WHERE source_id=? AND question_number=? "
                      "AND answered_at IS NOT NULL", (source_id, int(number), source_id, int(number))).fetchone() is not None
