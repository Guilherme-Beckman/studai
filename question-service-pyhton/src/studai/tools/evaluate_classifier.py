"""Avalia classificação num conjunto JSONL revisado e grava métricas reproduzíveis."""
from __future__ import annotations

import argparse
import json
import statistics
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification import service as clf
from studai.integrations.llm import make_provider


def score(rows: list[dict], gold_key: str, pred_key: str) -> dict:
    def normalize(value):
        text = unicodedata.normalize("NFKD", str(value or "")).casefold()
        return "".join(c for c in text if unicodedata.category(c) != "Mn").strip()

    labels = sorted({str(row[gold_key]) for row in rows if row.get(gold_key) is not None} |
                    {str(row[pred_key]) for row in rows if row.get(pred_key) is not None})
    correct = sum(normalize(row.get(gold_key)) == normalize(row.get(pred_key)) for row in rows)
    per_label = {}
    for label in labels:
        tp = sum(normalize(row.get(gold_key)) == normalize(label) and normalize(row.get(pred_key)) == normalize(label) for row in rows)
        fp = sum(normalize(row.get(gold_key)) != normalize(label) and normalize(row.get(pred_key)) == normalize(label) for row in rows)
        fn = sum(normalize(row.get(gold_key)) == normalize(label) and normalize(row.get(pred_key)) != normalize(label) for row in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        per_label[label] = {"precision": precision, "recall": recall,
                            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}
    return {"accuracy": correct / len(rows) if rows else 0.0,
            "macro_f1": statistics.mean(item["f1"] for item in per_label.values()) if per_label else 0.0,
            "per_label": per_label}


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("question") or not row.get("subject") or not row.get("topic"):
            raise ValueError(f"linha {line_no}: esperado question, subject e topic revisados")
        rows.append(row)
    return rows


def evaluate(dataset: Path, provider, *, output: Path, source: str, version: str,
             image_mode: str = "nunca") -> dict:
    data = load_jsonl(dataset)
    outcomes = []
    latencies = []
    invalid = 0
    attempts = 0
    for index, row in enumerate(data, 1):
        question = {"enunciado": row["question"], "numero": index,
                    "alternativas": {str(k): {"texto": v} for k, v in (row.get("alternatives") or {}).items()},
                    "revisar": []}
        started = time.monotonic()
        images = []
        if image_mode != "nunca" and row.get("images"):
            for image_path in row["images"][:4]:
                resolved = (dataset.parent / image_path).resolve()
                if dataset.parent.resolve() in resolved.parents and resolved.is_file():
                    images.append(resolved.read_bytes())
        try:
            result = clf.classify_one(provider, clf.build_system(clf.DEFAULT_TAXONOMY), question,
                                      None, clf.DEFAULT_TAXONOMY, images)
            error = None
        except Exception as exc:
            result, error = {}, str(exc)
            invalid += 1
        elapsed = time.monotonic() - started
        latencies.append(elapsed)
        attempts += int(result.get("tentativas", 2))
        usage = getattr(getattr(provider, "_thread_state", None), "usage", {})
        outcomes.append({"gold_subject": row["subject"], "pred_subject": result.get("materia"),
                         "gold_topic": row["topic"], "pred_topic": result.get("assunto"),
                         "seconds": elapsed, "usage": usage, "error": error})
    usage_totals = {key: sum(int(item.get("usage", {}).get(key, 0)) for item in outcomes)
                    for key in ("prompt_tokens", "completion_tokens", "eval_duration_ns")}
    result = {"dataset": str(dataset), "source": source, "dataset_version": version,
              "provider": provider.label, "created_at": datetime.now(timezone.utc).isoformat(),
              "image_mode": image_mode,
              "count": len(data), "invalid_json_or_classification": invalid,
              "attempts": attempts, "latency_seconds": {"mean": statistics.mean(latencies) if latencies else 0,
                                                            "median": statistics.median(latencies) if latencies else 0,
                                                            "total": sum(latencies)},
              "subject": score(outcomes, "gold_subject", "pred_subject"),
              "topic": score(outcomes, "gold_topic", "pred_topic"), "items": outcomes,
              "usage": usage_totals}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSONL com question, subject e topic revisados por humano")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--output", type=Path, default=Path("benchmark/results/latest.json"))
    parser.add_argument("--source", default="BLUEX filtrado: USP/Fuvest")
    parser.add_argument("--version", default="unversioned")
    parser.add_argument("--images", choices=("nunca", "figuras", "sempre"), default="nunca",
                        help="figuras/sempre enviam imagens locais listadas no campo images do JSONL")
    args = parser.parse_args()
    provider = make_provider(args.provider, args.model, base_url=args.base_url, temperature=0,
                             max_tokens=1024, json_mode=True)
    result = evaluate(args.dataset, provider, output=args.output, source=args.source, version=args.version,
                      image_mode=args.images)
    print(json.dumps({key: result[key] for key in ("count", "subject", "topic", "latency_seconds",
                                                   "invalid_json_or_classification", "attempts")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
