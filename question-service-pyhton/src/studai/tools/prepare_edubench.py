"""Prepara candidatos EduBench USP para revisão local, sem rótulo de tópico presumido."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Iterable


def candidate_rows(rows: Iterable[dict], *, exam: str = "USP") -> Iterable[dict]:
    """Mapeia campos EduBench sem tratar área ampla como tópico gold."""
    for row in rows:
        if row.get("exam") != exam:
            continue
        yield {
            "id": row.get("question_key"),
            "year": row.get("year"),
            "year_version": row.get("year_version"),
            "exam": row.get("exam"),
            "question": row.get("question", ""),
            "supporting_texts": row.get("supporting_texts", ""),
            "alternatives": {},
            "images": row.get("image_paths", []) or [],
            "image_descriptions": row.get("image_descriptions", []) or [],
            "reference_answer": row.get("answer", ""),
            "reference_short_answer": row.get("short_answer", ""),
            "grading_guidelines": row.get("guidelines", ""),
            "suggested_subject": row.get("subject", ""),
            "subject": "",
            "topic": "",
            "review_status": "pending",
            "source": "recogna-nlp/EduBench",
        }


def prepare(output: Path, *, split: str = "test", exam: str = "USP") -> int:
    os.environ.setdefault("HF_HOME", str(Path(os.environ.get("STUDAI_CACHE_DIR", "simulados/cache")) /
                                           "huggingface"))
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("instale a dependência com: pip install -e '.[benchmark]'") from exc
    dataset = load_dataset("recogna-nlp/EduBench", split=split)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as file:
        for row in candidate_rows(dataset, exam=exam):
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(os.environ.get("STUDAI_CACHE_DIR", "simulados/cache")) /
                        "edubench-usp-review-candidates.jsonl")
    parser.add_argument("--split", default="test")
    parser.add_argument("--exam", default="USP")
    args = parser.parse_args()
    count = prepare(args.output, split=args.split, exam=args.exam)
    print(f"{count} candidatos preparados em {args.output}")
    print("Preencha subject/topic após revisão; suggested_subject é apenas uma sugestão de rótulo amplo.")


if __name__ == "__main__":
    main()
