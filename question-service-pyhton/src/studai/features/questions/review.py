"""Persistência das revisões humanas e materialização dos exemplos FUVEST."""
from __future__ import annotations

import json
import os
from pathlib import Path


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
    return isinstance(review, dict) and review.get("status") in {"reviewed", "unusable"}


def review_stats(data: dict, reviews: dict) -> dict:
    states = reviews.get("questions") or {}
    required = [q for q in data.get("questoes", []) if review_reasons(q)]
    reviewed = sum(is_reviewed(states.get(str(q.get("numero")))) for q in required)
    total = len(required)
    return {"reviewRequired": total, "reviewedQuestions": reviewed,
            "pendingReview": total - reviewed,
            "reviewProgress": 100 if not total else round(reviewed * 100 / total)}


def save_review(exam_json: Path, question_number: int, value: dict) -> dict:
    data = json.loads(Path(exam_json).read_text(encoding="utf-8"))
    question = next((q for q in data.get("questoes", [])
                     if int(q.get("numero", -1)) == question_number), None)
    if question is None:
        raise FileNotFoundError(f"questão {question_number} não encontrada nesta prova")
    reviews = read_reviews(exam_json)
    record = {
        "status": value["status"],
        "subject": value.get("subject"),
        "topic": value.get("topic"),
        "content_id": value.get("content_id"),
        "taxonomy_version": value.get("taxonomy_version"),
        "content_path": value.get("content_path"),
        "related_content_ids": value.get("related_content_ids") or [],
        "note": str(value.get("note") or "").strip(),
    }
    states = reviews.setdefault("questions", {})
    states[str(question_number)] = record
    target = review_file(exam_json)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return {**record, **review_stats(data, reviews)}


def reviewed_examples(root: Path) -> list[dict]:
    """Return only usable, human-confirmed examples, without answer-key labels."""
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

    rows = []
    for exam_id, (path, data) in candidates.items():
        reviews = read_reviews(path).get("questions") or {}
        shared = data.get("textos_compartilhados") or {}
        for question in data.get("questoes", []):
            number = question.get("numero")
            review = reviews.get(str(number)) or {}
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
