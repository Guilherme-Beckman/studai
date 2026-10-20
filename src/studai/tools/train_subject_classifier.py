"""Treina o classificador leve de matéria (regressão logística sobre embeddings) e mede por validação cruzada.

Exemplos:
    # gera o conjunto de revisões aprovadas e treina com bge-m3 (modelo vai para STUDAI_CACHE_DIR)
    PYTHONPATH=src python -m studai.tools.train_subject_classifier simulados/cache/fuvest-reviewed.jsonl

    # só mede (validação cruzada deixando provas inteiras de fora), sem gravar o modelo
    PYTHONPATH=src python -m studai.tools.train_subject_classifier simulados/cache/fuvest-reviewed.jsonl \\
        --no-save --report docs/benchmarks/subject-model-cv.json

Cada linha do JSONL precisa de ``question`` e ``subject`` (``supporting_texts`` e ``alternatives``
são opcionais). Linhas com ``reviewed: false`` ou ``review_status: pending`` ficam de fora; outras
fontes (ex.: BLUEX convertido) podem ser passadas como arquivos extras.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from studai.features.classification.rag import example_text, ollama_embed
from studai.features.classification.service import DEFAULT_TAXONOMY
from studai.features.classification.subject_model import (
    DEFAULT_SUBJECT_EMBEDDING_MODEL, Embed, cross_validate, default_model_path, train_subject_model,
)


def load_training_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("reviewed") is False or row.get("review_status") == "pending":
                continue
            subject = str(row.get("subject") or "").strip()
            if subject not in DEFAULT_TAXONOMY:
                raise ValueError(f"{path}:{line_no}: matéria fora da taxonomia: {subject!r}")
            text = example_text(row)
            if not text.strip():
                continue
            rows.append({"id": row.get("id") or f"{Path(path).name}:{line_no}", "subject": subject,
                         "group": str(row.get("exam") or row.get("year") or Path(path).name), "text": text})
    if not rows:
        raise ValueError("nenhuma questão rotulada para treinar")
    return rows


def embed_rows(rows: list[dict], embed: Embed, *, batch: int = 16, cache_path: Path | None = None,
               model: str = "") -> list[list[float]]:
    """Embeddings das questões; ``cache_path`` (JSON) evita recalcular entre rodadas."""
    cache: dict[str, list[float]] = {}
    if cache_path and cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    keys = [hashlib.sha1(f"{model}\n{row['text']}".encode()).hexdigest() for row in rows]
    missing = [index for index, key in enumerate(keys) if key not in cache]
    for start in range(0, len(missing), max(1, batch)):
        chunk = missing[start:start + batch]
        for index, vector in zip(chunk, embed([rows[i]["text"] for i in chunk])):
            cache[keys[index]] = vector
    if cache_path and missing:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return [cache[key] for key in keys]


def train(rows: list[dict], vectors: list[list[float]], *, folds: int = 5, by_group: bool = True,
          embedding_model: str = DEFAULT_SUBJECT_EMBEDDING_MODEL, l2: float = 1e-3, epochs: int = 400) -> tuple:
    labels = [row["subject"] for row in rows]
    groups = [row["group"] for row in rows] if by_group else None
    report = cross_validate(vectors, labels, folds=folds, groups=groups, l2=l2, epochs=epochs,
                            embedding_model=embedding_model)
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    model = train_subject_model(vectors, labels, l2=l2, epochs=epochs, embedding_model=embedding_model,
                                meta={"trained_on": len(rows), "per_subject": counts,
                                      "cv_accuracy": round(report["accuracy"], 4),
                                      "cv_by_threshold": report["by_threshold"]})
    return model, report, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("datasets", type=Path, nargs="+", help="JSONL com question/subject (revisões aprovadas, BLUEX…)")
    parser.add_argument("--embedding-model", default=DEFAULT_SUBJECT_EMBEDDING_MODEL)
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--output", type=Path, help="padrão: STUDAI_CACHE_DIR/subject-model-<embedding>.json")
    parser.add_argument("--no-save", action="store_true", help="só valida, sem gravar o modelo")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--random-folds", action="store_true",
                        help="dobras aleatórias em vez de deixar provas inteiras de fora")
    parser.add_argument("--l2", type=float, default=1e-3)
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--embeddings-cache", type=Path, help="JSON para reaproveitar embeddings entre rodadas")
    parser.add_argument("--report", type=Path, help="grava o relatório da validação cruzada em JSON")
    args = parser.parse_args()
    rows = load_training_rows(args.datasets)
    started = time.monotonic()
    vectors = embed_rows(rows, lambda texts: ollama_embed(texts, base_url=args.base_url,
                                                          model=args.embedding_model, timeout=300),
                         cache_path=args.embeddings_cache, model=args.embedding_model)
    embed_seconds = time.monotonic() - started
    model, report, counts = train(rows, vectors, folds=args.folds, by_group=not args.random_folds,
                                  embedding_model=args.embedding_model, l2=args.l2, epochs=args.epochs)
    summary = {"questions": len(rows), "per_subject": counts, "embedding_model": args.embedding_model,
               "embedding_seconds": round(embed_seconds, 1), "cross_validation": report}
    if not args.no_save:
        output = args.output or default_model_path(args.embedding_model)
        model.save(output)
        summary["model_path"] = str(output)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
