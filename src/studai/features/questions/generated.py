"""Questões de treino geradas pela IA, por subconteúdo e nível de dificuldade.

Ficam numa "prova" virtual do acervo (``treino-ia/questoes.classificado.json``, origem
``ia``), com ``revisao.json`` e gabarito como as provas extraídas. Assim a descoberta
do acervo, o pool, a revisão, o gabarito e as resoluções funcionam sem caminho próprio.
Cada questão entra já aprovada (``status: reviewed``, ``generated: true``) e com a
resolução comentada; se o aluno apontar um erro, ela vira ``unusable`` e sai do pool.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from studai.features.answer_keys.service import CORPUS_KEY_FILENAME
from studai.features.classification import service as clf
from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, live_id
from studai.features.explanations import service as explanations
from studai.features.questions import review
from studai.features.questions.origin import DIFFICULTIES
from studai.integrations.ai_cache import cached_ai_result

SOURCE_ID = "treino-ia"
BANK_DIR = "treino-ia"
ORIGIN = {"tipo": "ia", "nome": "Questões geradas pela IA"}
PROMPT_VERSION = "generated-v1"
MAX_COUNT = 5
LETTERS = "ABCDE"
LEVEL_GUIDE = {
    1: "fácil: aplica diretamente uma definição, fórmula ou fato do assunto, em um ou dois passos",
    2: "média: dois ou três passos, no nível das questões medianas da FUVEST",
    3: "difícil: no nível das questões difíceis da FUVEST, integrando o assunto com outro conceito",
}
_LOCK = threading.Lock()

SYSTEM = """Você é professor de cursinho e cria questões inéditas de múltipla escolha para treino da 1ª fase da FUVEST.
Recebe um assunto da matéria, a descrição do edital, o nível pedido e enunciados que já existem (não os repita).
Cada questão tem enunciado autossuficiente (sem figura), 5 alternativas (A a E) plausíveis e diferentes entre si, uma só correta.
Escreva em português do Brasil. Para cada questão, inclua também a resolução comentada:
- key_concept: o conceito cobrado, em uma frase;
- steps: os passos da resolução, sem numerar, com as contas quando houver;
- distractors: para cada alternativa errada, por que está errada;
- hints: exatamente 3 dicas graduais, sem numerar, que não digam a letra correta;
- difficulty: o nível pedido (1, 2 ou 3).
Devolva JSON sem markdown: {"questions": [{"enunciado": texto, "alternativas": {"A": texto, ..., "E": texto}, "answer": letra, "key_concept": texto, "steps": [textos], "distractors": {"<letra>": texto}, "hints": [3 textos], "difficulty": 1|2|3}]}"""


class GenerationError(Exception):
    """A IA não devolveu nenhuma questão válida."""


def bank_file(root: Path) -> Path:
    return Path(root) / BANK_DIR / "questoes.classificado.json"


def _read_bank(root: Path) -> dict:
    path = bank_file(root)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"prova": SOURCE_ID, "arquivo_origem": "Questões geradas pela IA", "origem": ORIGIN,
            "textos_compartilhados": {}, "questoes": []}


def _write_bank(root: Path, data: dict) -> None:
    path = bank_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _key(text: str) -> str:
    """Enunciado normalizado (sem acentos, caixa e pontuação) para achar repetições."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"\W+", " ", text).strip()


def validate_question(obj: dict, level: int) -> dict:
    """Uma questão gerada: enunciado, 5 alternativas distintas, gabarito e resolução válida."""
    if not isinstance(obj, dict):
        raise ValueError("cada questão deve ser um objeto")
    stem = obj.get("enunciado")
    if not isinstance(stem, str) or len(stem.strip()) < 15:
        raise ValueError("enunciado ausente ou curto demais")
    raw = obj.get("alternativas")
    if not isinstance(raw, dict):
        raise ValueError("alternativas deve ser um objeto de A a E")
    alternatives = {str(letter).strip().strip("()").upper(): text for letter, text in raw.items()}
    if sorted(alternatives) != list(LETTERS) or not all(isinstance(t, str) and t.strip() for t in alternatives.values()):
        raise ValueError("a questão precisa de exatamente 5 alternativas (A a E) com texto")
    if len({_key(text) for text in alternatives.values()}) != 5:
        raise ValueError("as alternativas precisam ser diferentes entre si")
    answer = str(obj.get("answer") or "").strip().strip("()").upper()
    if answer not in LETTERS:
        raise ValueError("answer deve ser uma letra de A a E")
    explanation = explanations.validate_explanation({**obj, "answer": answer, "difficulty": level},
                                                    alternatives, answer)
    return {"enunciado": stem.strip(), "alternativas": {letter: alternatives[letter].strip() for letter in LETTERS},
            "answer": answer, "explanation": explanation}


def _node(content_id: str) -> dict:
    node = CONTENT_INDEX.get(live_id(content_id or ""))
    if node is None or len(node["path_ids"]) < 3:
        raise ValueError(f"escolha um conteúdo ou subconteúdo da taxonomia: {content_id}")
    return node


def existing_stems(root: Path, content_id: str) -> list[str]:
    """Enunciados já gerados para o nó (o prompt pede para não repetir)."""
    return [q["enunciado"] for q in _read_bank(root)["questoes"]
            if (q.get("classificacao") or {}).get("conteudo_principal_id") == content_id]


def generate_questions(root: Path, db: sqlite3.Connection, content_id: str, level: int, count: int,
                       provider) -> list[int]:
    """Gera ``count`` questões do nível pedido, grava no banco da IA e devolve os números."""
    if level not in DIFFICULTIES:
        raise ValueError("nível deve ser 1 (fácil), 2 (média) ou 3 (difícil)")
    if not 1 <= int(count) <= MAX_COUNT:
        raise ValueError(f"gere de 1 a {MAX_COUNT} questões por vez")
    node = _node(content_id)
    known = existing_stems(root, node["id"])
    user = json.dumps({"assunto": node["name"], "caminho": " → ".join(node["path"]), "materia": node["subject"],
                       "descricao": node.get("description"), "nivel": level, "nivel_explicado": LEVEL_GUIDE[level],
                       "quantidade": int(count), "enunciados_existentes": [stem[:160] for stem in known[-20:]]},
                      ensure_ascii=False)
    errors: list[str] = []

    def ask():
        for attempt in range(2):
            prompt = user if attempt == 0 else user + f"\n\nA resposta anterior é inválida: {errors[-1]}. Corrija-a."
            try:
                payload = clf.extract_json(provider.complete(SYSTEM, prompt))
                items = payload.get("questions") if isinstance(payload, dict) else None
                if not isinstance(items, list) or not items:
                    raise ValueError("questions deve ser uma lista não vazia")
                valid = []
                for index, item in enumerate(items, 1):
                    try:
                        valid.append(validate_question(item, level))
                    except ValueError as error:
                        errors.append(f"questão {index}: {error}")
                if valid:
                    return valid
                raise ValueError(errors[-1])
            except (ValueError, TypeError) as error:
                errors.append(str(error))
        return None

    generated = cached_ai_result("generated_questions", provider, SYSTEM, user, ask)
    if not generated:
        raise GenerationError(f"a IA não gerou questões válidas: {errors[-1] if errors else 'sem resposta'}")
    model = str(getattr(provider, "last_used_model", "") or getattr(provider, "label", "") or "")
    return _store(root, db, node, level, generated, model)


def _store(root: Path, db: sqlite3.Connection, node: dict, level: int, generated: list[dict], model: str) -> list[int]:
    now = datetime.now(timezone.utc).isoformat()
    explanations.ensure_schema(db)
    with _LOCK:
        data = _read_bank(root)
        seen = {_key(q["enunciado"]) for q in data["questoes"]}
        number = max((int(q["numero"]) for q in data["questoes"]), default=0)
        added: list[tuple[int, dict]] = []
        for item in generated:
            if _key(item["enunciado"]) in seen:
                continue  # repetida: a IA às vezes devolve a mesma questão de antes
            seen.add(_key(item["enunciado"]))
            number += 1
            data["questoes"].append({
                "numero": number, "pagina": None, "texto_id": None, "enunciado": item["enunciado"], "nota": "",
                "alternativas": {letter: {"texto": text, "figuras": []} for letter, text in item["alternativas"].items()},
                "figuras_enunciado": [], "imagens_completas": [], "revisar": [], "materia": node["subject"],
                "classificacao": {"materia": node["subject"], "assunto": node["name"],
                                  "conteudo_principal_id": node["id"], "caminho_conteudo": " → ".join(node["path"])},
                "gerada": {"nivel": level, "modelo": model, "criada_em": now, "prompt": PROMPT_VERSION}})
            added.append((number, item))
        if not added:
            return []
        _write_bank(root, data)
        # Gabarito também em arquivo, como no acervo: o banco de histórico não é versionado.
        key_path = bank_file(root).with_name(CORPUS_KEY_FILENAME)
        key = json.loads(key_path.read_text(encoding="utf-8")) if key_path.exists() else {"prova": SOURCE_ID, "gabarito": {}}
        key["gabarito"].update({str(n): item["answer"] for n, item in added})
        key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        review.save_reviews_bulk(bank_file(root), {n: {
            "status": "reviewed", "subject": node["subject"], "topic": node["name"], "content_id": node["id"],
            "taxonomy_version": CONTENT_TAXONOMY_VERSION, "content_path": " → ".join(node["path"]),
            "difficulty": level, "generated": True} for n, _ in added})
    with db:
        for n, item in added:
            db.execute("""INSERT OR REPLACE INTO answer_keys(source_id,question_number,answer,source_pdf,imported_at,
                is_annulled,association_method) VALUES(?,?,?,?,?,0,'ai')""", (SOURCE_ID, n, item["answer"], "ia", now))
            db.execute("""INSERT OR REPLACE INTO question_explanations(source_id,question_number,status,content_json,
                model,prompt_version,feedback,created_at,reviewed_at) VALUES(?,?,'generated',?,?,?,NULL,?,NULL)""",
                       (SOURCE_ID, n, json.dumps(item["explanation"], ensure_ascii=False), model,
                        explanations.PROMPT_VERSION, now))
    return [n for n, _ in added]


def report_question(root: Path, number: int, note: str | None) -> dict:
    """"Tem erro": a questão gerada sai do pool (``unusable``) com a crítica na observação."""
    note = (note or "").strip() or "Erro apontado no treino"
    if len(note) > 2000:
        raise ValueError("a observação deve ter até 2000 caracteres")
    with _LOCK:
        path = bank_file(root)
        if not path.exists():
            raise FileNotFoundError("ainda não há questões geradas pela IA")
        previous = (review.read_reviews(path).get("questions") or {}).get(str(int(number)), {})
        return review.save_review(path, int(number), {**previous, "status": "unusable", "note": note})
