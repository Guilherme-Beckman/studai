"""Compara qualidade e desempenho do classificador em vários hosts/configurações locais."""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification.rag import DEFAULT_EMBEDDING_MODEL
from studai.integrations.llm import make_provider
from studai.tools.evaluate_classifier import evaluate


def csv_values(raw: str, cast):
    return sorted(set(cast(value.strip()) for value in raw.split(",") if value.strip()))


def benchmark(dataset: Path, *, urls: str, model: str, workers: list[int], threads: list[int],
              rag_modes: list[bool], embedding_model: str, limit: int | None,
              output: Path, timeout: float, max_tokens: int) -> dict:
    rows = []
    with tempfile.TemporaryDirectory(prefix="studai-benchmark-") as temp_dir:
        for use_rag in rag_modes:
            for worker_count in workers:
                for thread_count in threads:
                    provider = make_provider("ollama", model, base_url=urls, key_env=None,
                                             temperature=0, max_tokens=max_tokens, json_mode=True,
                                             timeout=timeout, request_attempts=1,
                                             num_threads=thread_count)
                    report = evaluate(
                        dataset, provider,
                        output=Path(temp_dir) / f"run-{len(rows)}.json",
                        source="local Fuvest benchmark", version=dataset.stem,
                        limit=limit, workers=worker_count, rag=use_rag,
                        embedding_model=embedding_model)
                    rows.append({"rag": use_rag, "workers": worker_count,
                                 "ollama_threads": thread_count, "provider": report["provider"],
                                 "subject": report["subject"], "topic": report["topic"],
                                 "invalid": report["invalid_json_or_classification"],
                                 "attempts": report["attempts"],
                                 "latency_seconds": report["latency_seconds"],
                                 "hosts": report["hosts"]})
    by_config = {(row["workers"], row["ollama_threads"], row["rag"]): row for row in rows}
    quality_fields = ("accuracy", "macro_f1")
    eligible = []
    for row in rows:
        if not row["rag"]:
            row["preserves_quality"] = True
            eligible.append(row)
            continue
        baseline = by_config.get((row["workers"], row["ollama_threads"], False))
        if baseline is None:
            row["preserves_quality"] = None
            continue
        comparisons = []
        for area in ("subject", "topic"):
            for field in quality_fields:
                comparisons.append(row[area][field] >= baseline[area][field])
        row["preserves_quality"] = all(comparisons)
        if row["preserves_quality"]:
            eligible.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {"dataset": str(dataset), "created_at": datetime.now(timezone.utc).isoformat(),
              "hosts_requested": [url.strip() for url in urls.split(",") if url.strip()],
              "embedding_model": embedding_model, "runs": rows,
              "fastest_quality_preserving": max(
                  eligible, key=lambda row: row["latency_seconds"].get("questions_per_minute", 0),
                  default=None)}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSONL de questões com subject/topic revisados")
    parser.add_argument("--base-url", default="http://localhost:11434",
                        help="um ou mais Ollama separados por vírgula")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--workers", default="1,2")
    parser.add_argument("--threads", default="0,4,8")
    parser.add_argument("--rag", choices=("off", "on", "both"), default="both")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--output", type=Path, default=Path("simulados/cache/classifier-sweep.json"))
    args = parser.parse_args()
    if not args.dataset.is_file():
        parser.error(f"dataset não encontrado: {args.dataset}")
    if args.timeout <= 0 or args.max_tokens <= 0 or (args.limit is not None and args.limit < 1):
        parser.error("timeout, max-tokens e limit devem ser positivos")
    try:
        workers, threads = csv_values(args.workers, int), csv_values(args.threads, int)
    except ValueError:
        parser.error("workers e threads devem ser listas de inteiros separados por vírgula")
    if not workers or min(workers) < 1 or not threads or min(threads) < 0:
        parser.error("workers deve ser >=1 e threads >=0")
    rag_modes = {"off": [False], "on": [True], "both": [False, True]}[args.rag]
    result = benchmark(args.dataset, urls=args.base_url, model=args.model, workers=workers,
                       threads=threads, rag_modes=rag_modes, embedding_model=args.embedding_model,
                       limit=args.limit, output=args.output, timeout=args.timeout,
                       max_tokens=args.max_tokens)
    print(json.dumps({"output": str(args.output), "runs": len(result["runs"]),
                      "fastest_quality_preserving": result["fastest_quality_preserving"],
                      "results": result["runs"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
