"""Persistência das revisões humanas e materialização dos exemplos FUVEST."""
from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

# "reviewed" é a questão aprovada; "bad_crop" vai para a fila de recortes ruins (Fase 3).
STATUSES = ("reviewed", "unusable", "bad_crop")


def review_file(exam_json: Path) -> Path:
    return Path(exam_json).parent / "revisao.json"


def read_reviews(exam_json: Path) -> dict:
    try:
        value = json.loads(review_file(exam_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "questions": {}}
    if not isinstance(value, dict) or not isinstance(value.get("questions"), dict):
        return {"schema_version": 1, "questions": {}}
    return value


def preserve_reviews(root: Path, exam_id: str, destination_json: Path) -> bool:
    """Carry human decisions to a new extraction directory for the same exam ID."""
    destination = review_file(destination_json)
    if destination.is_file():
        return True
    candidates = []
    for path in Path(root).rglob("questoes*.json"):
        if path.parent.resolve() == Path(destination_json).parent.resolve():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("prova") or "") != exam_id:
            continue
        reviews = read_reviews(path)
        questions = reviews.get("questions") or {}
        if questions:
            candidates.append((len(questions), reviews))
    if not candidates:
        return False
    _, reviews = max(candidates, key=lambda item: item[0])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return True


def review_reasons(question: dict) -> list[str]:
    reasons = list(question.get("revisar") or [])
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    if question.get("classificacao_erro"):
        reasons.append("erro_classificacao")
    if not isinstance(question.get("classificacao"), dict):
        reasons.append("classificacao_ausente")
    elif question["classificacao"].get("subconteudo_pendente"):
        reasons.append("subconteudo_pendente")
    return list(dict.fromkeys(str(reason) for reason in reasons if reason))


def is_reviewed(review: dict | None) -> bool:
    return isinstance(review, dict) and review.get("status") in STATUSES


def apply_edits(question: dict, review: dict | None) -> dict:
    """Questão com as correções humanas aplicadas por cima da extração (que fica intacta)."""
    edits = (review or {}).get("edits") if isinstance(review, dict) else None
    result = copy.deepcopy(question)
    if not isinstance(edits, dict):
        return result
    if "enunciado" in edits:
        result["enunciado"] = edits["enunciado"]
    if "figuras_enunciado" in edits:
        result["figuras_enunciado"] = list(edits["figuras_enunciado"] or [])
    if isinstance(edits.get("alternativas"), dict):
        options = result.get("alternativas") or {}
        merged = {}
        for letter, value in edits["alternativas"].items():
            current = options.get(letter)
            current = dict(current) if isinstance(current, dict) else {"texto": str(current or "")}
            if "texto" in value:
                current["texto"] = value["texto"]
            if "figuras" in value:
                current["figuras"] = list(value["figuras"] or [])
            current.setdefault("figuras", [])
            merged[letter] = current
        result["alternativas"] = dict(sorted(merged.items()))
    return result


MAX_STEM_CHARS = 20_000
MAX_OPTION_CHARS = 5_000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"))


def _figures(exam_dir: Path, value) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("figuras devem ser uma lista de caminhos")
    root = Path(exam_dir).resolve()
    paths = []
    for item in value:
        rel = str(item or "")
        target = (root / rel).resolve()
        if not rel.startswith("img/") or not target.is_relative_to(root / "img") or not target.is_file():
            raise ValueError(f"imagem inválida: {rel}")
        paths.append(rel)
    return paths


def validate_edits(question: dict, edits, exam_dir: Path) -> dict | None:
    """Normaliza a camada de correções enviada pela revisão; None ou {} remove a camada."""
    if not edits:
        return None
    if not isinstance(edits, dict):
        raise ValueError("edits deve ser um objeto")
    clean: dict = {}
    if "enunciado" in edits:
        stem = str(edits["enunciado"] or "")
        if len(stem) > MAX_STEM_CHARS:
            raise ValueError(f"o enunciado deve ter no máximo {MAX_STEM_CHARS} caracteres")
        clean["enunciado"] = stem
    if "figuras_enunciado" in edits:
        clean["figuras_enunciado"] = _figures(exam_dir, edits["figuras_enunciado"])
    if "alternativas" in edits:
        options = edits["alternativas"]
        if not isinstance(options, dict) or not options:
            raise ValueError("alternativas devem ser um objeto {letra: {texto, figuras}}")
        clean["alternativas"] = {}
        for letter, value in options.items():
            if letter not in "ABCDE" or len(letter) != 1:
                raise ValueError(f"alternativa inválida: {letter}")
            value = value if isinstance(value, dict) else {"texto": value}
            option = {"texto": str(value.get("texto") or "")}
            if len(option["texto"]) > MAX_OPTION_CHARS:
                raise ValueError(f"a alternativa {letter} deve ter no máximo {MAX_OPTION_CHARS} caracteres")
            option["figuras"] = _figures(exam_dir, value.get("figuras") or [])
            clean["alternativas"][letter] = option
    unknown = set(edits) - {"enunciado", "figuras_enunciado", "alternativas"}
    if unknown:
        raise ValueError(f"campo de edição desconhecido: {', '.join(sorted(unknown))}")
    return clean or None


def save_image(exam_dir: Path, content: bytes) -> str:
    """Guarda uma imagem enviada na revisão e devolve o caminho relativo à prova."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("a imagem deve ter até 5 MB")
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        suffix = ".webp"
    else:
        suffix = next((ext for signature, ext in IMAGE_SIGNATURES if content.startswith(signature)), None)
    if suffix is None:
        raise ValueError("envie uma imagem PNG, JPEG ou WebP")
    folder = Path(exam_dir) / "img"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"edit_{uuid.uuid4().hex[:12]}{suffix}"
    (folder / name).write_bytes(content)
    return f"img/{name}"


def review_stats(data: dict, reviews: dict) -> dict:
    """Progresso sobre todas as questões; as chaves "reviewRequired"/"reviewedQuestions"/
    "pendingReview" continuam contando só as sinalizadas pela IA."""
    states = reviews.get("questions") or {}
    questions = data.get("questoes", [])
    status_of = [(states.get(str(q.get("numero"))) or {}).get("status") for q in questions]
    required = [q for q in questions if review_reasons(q)]
    flagged_done = sum(is_reviewed(states.get(str(q.get("numero")))) for q in required)
    total = len(questions)
    decided = sum(status in STATUSES for status in status_of)
    return {"total": total, "approved": status_of.count("reviewed"), "unusable": status_of.count("unusable"),
            "badCrop": status_of.count("bad_crop"), "pending": total - decided,
            "reviewRequired": len(required), "reviewedQuestions": flagged_done,
            "pendingReview": len(required) - flagged_done,
            "reviewProgress": 100 if not total else round(decided * 100 / total)}


def _record(value: dict, previous: dict | None) -> dict:
    status = value.get("status")
    record = {
        "status": status if status in STATUSES else None,
        "subject": value.get("subject"),
        "topic": value.get("topic"),
        "content_id": value.get("content_id"),
        "taxonomy_version": value.get("taxonomy_version"),
        "content_path": value.get("content_path"),
        "related_content_ids": value.get("related_content_ids") or [],
        "note": str(value.get("note") or "").strip(),
    }
    if record["status"] is None:
        record.pop("status")
    # Sem "edits" no pedido a correção anterior continua valendo; None remove a camada.
    if "edits" in value:
        edits, edited_at = value["edits"], datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        edits, edited_at = (previous or {}).get("edits"), (previous or {}).get("edited_at")
    if edits:
        record.update(edits=edits, edited_at=edited_at)
    return record


def _write_reviews(exam_json: Path, reviews: dict) -> None:
    target = review_file(exam_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def save_reviews_bulk(exam_json: Path, values: dict[int, dict]) -> dict:
    """Grava várias decisões numa única escrita atômica."""
    data = json.loads(Path(exam_json).read_text(encoding="utf-8"))
    numbers = {int(q.get("numero", -1)) for q in data.get("questoes", [])}
    missing = sorted(set(values) - numbers)
    if missing:
        raise FileNotFoundError(f"questão {missing[0]} não encontrada nesta prova")
    reviews = read_reviews(exam_json)
    states = reviews.setdefault("questions", {})
    for number, value in values.items():
        states[str(number)] = _record(value, states.get(str(number)))
    _write_reviews(exam_json, reviews)
    return {"questions": {str(n): states[str(n)] for n in values}, **review_stats(data, reviews)}


def save_review(exam_json: Path, question_number: int, value: dict) -> dict:
    saved = save_reviews_bulk(exam_json, {question_number: value})
    record = saved.pop("questions")[str(question_number)]
    return {**record, **saved}


def _exam_files(root: Path) -> dict[str, tuple[Path, dict]]:
    """Um JSON por prova, preferindo o classificado."""
    candidates: dict[str, tuple[Path, dict]] = {}
    for path in Path(root).rglob("questoes*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("questoes"), list):
            continue
        exam_id = str(data.get("prova") or f"{path.parent.name}/questoes")
        previous = candidates.get(exam_id)
        if previous is None or ("classificado" in path.name and "classificado" not in previous[0].name):
            candidates[exam_id] = (path, data)
    return candidates


def bad_crop_queue(root: Path) -> list[dict]:
    """Questões marcadas como "recorte ruim", para a fila de recorte da Fase 3."""
    rows = []
    for exam_id, (path, data) in sorted(_exam_files(root).items()):
        reviews = read_reviews(path).get("questions") or {}
        for question in data.get("questoes", []):
            review = reviews.get(str(question.get("numero"))) or {}
            if review.get("status") == "bad_crop":
                rows.append({"exam": exam_id, "numero": question.get("numero"),
                             "pagina": question.get("pagina"), "note": review.get("note") or "",
                             "examJson": str(path)})
    return rows


def reviewed_examples(root: Path) -> list[dict]:
    """Return only usable, human-confirmed examples, without answer-key labels."""
    rows = []
    for exam_id, (path, data) in _exam_files(root).items():
        reviews = read_reviews(path).get("questions") or {}
        shared = data.get("textos_compartilhados") or {}
        for question in data.get("questoes", []):
            number = question.get("numero")
            review = reviews.get(str(number)) or {}
            question = apply_edits(question, review)
            if (review.get("status") != "reviewed" or not review.get("subject")
                    or not review.get("content_id")):
                continue
            from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION
            content = CONTENT_INDEX.get(str(review["content_id"]))
            if (review.get("taxonomy_version") != CONTENT_TAXONOMY_VERSION or not content
                    or not content["is_leaf"] or content["subject"] != review["subject"]):
                continue
            support = shared.get(str(question.get("texto_id") or ""), {})
            if isinstance(support, str):
                support = {"texto": support}
            alternatives = question.get("alternativas") or {}
            text_content = [str(question.get("enunciado") or ""), str(support.get("texto") or "")]
            text_content.extend(str(value.get("texto") or "") if isinstance(value, dict) else str(value)
                                for value in alternatives.values())
            if not any(part.strip() for part in text_content):
                continue
            rows.append({
                "id": f"{exam_id}:q{number}", "exam": exam_id,
                "question": question.get("enunciado") or "",
                "supporting_texts": support.get("texto") or "",
                "alternatives": alternatives,
                "subject": review["subject"], "topic": content["name"],
                "content_id": content["id"], "content_path": " → ".join(content["path"]),
                "taxonomy_version": CONTENT_TAXONOMY_VERSION,
                "reviewed": True, "review_status": "reviewed",
                "source": "fuvest_local", "note": review.get("note") or "",
            })
    return rows


def write_reviewed_dataset(root: Path, destination: Path) -> int:
    rows = reviewed_examples(root)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                         encoding="utf-8")
    os.replace(temporary, destination)
    return len(rows)
