"""Measure embedding candidate recall against reviewed question labels."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification.rag import ollama_embed
from studai.features.classification.taxonomy import (
    CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, DEFAULT_CONTENT_TAXONOMY,
)
from studai.features.classification.topic_embeddings import (
    DEFAULT_TOPIC_EMBEDDING_CACHE, DEFAULT_TOPIC_EMBEDDING_MODEL,
    TopicEmbeddingIndex,
)


def question_text(row: dict) -> str:
    parts = [str(row.get("question") or row.get("enunciado") or "").strip()]
    support = str(row.get("supporting_texts") or row.get("texto_base") or "").strip()
    if support:
        parts.append("Texto compartilhado/de apoio: " + support)
    alternatives = row.get("alternatives") or row.get("alternativas") or {}
    for letter, value in sorted(alternatives.items()):
        text = value.get("texto", "") if isinstance(value, dict) else value
        if text:
            parts.append(f"({letter}) {text}")
    return "\n".join(part for part in parts if part)


def measure(dataset: Path, *, base_url: str, model: str, cache_path: Path,
            output: Path, report_path: Path | None = None,
            ks: tuple[int, ...] = (5, 10, 15, 20)) -> dict:
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [row for row in rows if row.get("subject") and row.get("content_id")
            and row.get("taxonomy_version") == CONTENT_TAXONOMY_VERSION]
    if not rows:
        raise ValueError("O dataset não contém questões revisadas com content_id da taxonomia ativa.")
    index = TopicEmbeddingIndex(cache_path, model=model, base_url=base_url)
    build = index.build()
    vectors = ollama_embed([question_text(row) for row in rows], base_url=base_url, model=model)
    if len(vectors) != len(rows):
        raise ValueError("Quantidade inesperada de embeddings de questões.")
    hits = {k: 0 for k in ks}
    misses = []
    items = []
    for row, vector in zip(rows, vectors):
        ranked = index.rank(vector, limit=max(ks))
        gold = {"subject": row["subject"], "content_id": row["content_id"]}
        rank = next((number for number, candidate in enumerate(ranked, 1)
                     if candidate.get("content_id") == gold["content_id"]), None)
        for k in ks:
            hits[k] += int(rank is not None and rank <= k)
        item = {"id": row.get("id"), "subject": gold["subject"], "content_id": gold["content_id"],
                "content_path": " → ".join(CONTENT_INDEX[gold["content_id"]]["path"]),
                "gold_rank": rank, "top_candidates": ranked[:min(5, max(ks))]}
        items.append(item)
        if rank is None or rank > max(ks):
            misses.append(item)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "dataset": str(dataset),
              "labeled_questions": len(rows), "taxonomy_topics": sum(1 for node in DEFAULT_CONTENT_TAXONOMY["index"].values()
                                                                      if node["is_leaf"]),
              "embedding_model": model, "embedding_endpoint": base_url, "cache": build,
              "recall_at_k": {str(k): {"hits": hits[k], "total": len(rows), "recall": hits[k] / len(rows)}
                              for k in ks},
              "items": items, "misses_at_max_k": misses}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report_path:
        lines = ["# Recall do pré-filtro de tópicos por embeddings", "",
                 f"Medição: {report['created_at']}.", "",
                 "## Configuração", "",
                 f"- Modelo de embeddings: `{model}` em `{base_url}`.",
                 f"- Taxonomia: {report['taxonomy_topics']} tópicos; hash `{build['taxonomy_hash']}`.",
                 f"- Questões revisadas: {len(rows)}; dataset `{dataset}`.",
                 f"- Cache: `{cache_path}` ({'reutilizado' if build['cached'] else 'gerado nesta execução'}).",
                 "", "## Recall", "", "| k | Acertos no top-k | Recall@k |", "|---:|---:|---:|"]
        for k in ks:
            metric = report["recall_at_k"][str(k)]
            lines.append(f"| {k} | {metric['hits']}/{metric['total']} | {metric['recall']:.1%} |")
        lines.extend(["", "A decisão de ativar o filtro deve usar o menor k que mantenha o tópico correto entre os candidatos em todas as questões avaliadas. A etapa seguinte medirá a acurácia do classificador com essa lista reduzida.",
                      "", f"Resultados por questão e candidatos: `{output}`.", ""])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path,
                        default=Path("simulados/cache/fuvest-reviewed.jsonl"))
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", default=DEFAULT_TOPIC_EMBEDDING_MODEL)
    parser.add_argument("--cache", type=Path, default=DEFAULT_TOPIC_EMBEDDING_CACHE)
    parser.add_argument("--output", type=Path, default=Path("docs/benchmarks/topic-retrieval.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/benchmarks/topic-retrieval.md"))
    args = parser.parse_args()
    result = measure(args.dataset, base_url=args.base_url, model=args.model,
                     cache_path=args.cache, output=args.output, report_path=args.report)
    print(json.dumps({"labeled_questions": result["labeled_questions"],
                      "taxonomy_topics": result["taxonomy_topics"],
                      "recall_at_k": result["recall_at_k"], "cache": result["cache"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
