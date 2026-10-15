"""Avalia classificação num conjunto JSONL revisado e grava métricas reproduzíveis."""
from __future__ import annotations

import argparse
import json
import statistics
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification import service as clf
from studai.features.classification.cache import ClassificationCache
from studai.features.classification.rag import DEFAULT_EMBEDDING_MODEL, LocalExampleRetriever
from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION
from studai.integrations.llm import make_provider
from studai.settings import DEFAULT_CLASSIFICATION_CACHE


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


def load_jsonl(path: Path, *, require_labels: bool = True) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if "question" not in row:
            raise ValueError(f"linha {line_no}: question obrigatória")
        if require_labels and (not row.get("subject") or not (row.get("content_id") or row.get("topic"))):
            raise ValueError(f"linha {line_no}: esperado question, subject e content_id (ou rótulo legado topic)")
        if row.get("content_id"):
            content = CONTENT_INDEX.get(str(row["content_id"]))
            if row.get("taxonomy_version") != CONTENT_TAXONOMY_VERSION:
                raise ValueError(f"linha {line_no}: taxonomy_version não corresponde à árvore ativa")
            if not content or not content["is_leaf"] or content["subject"] != row.get("subject"):
                raise ValueError(f"linha {line_no}: content_id inválido para subject")
        rows.append(row)
    return rows


def evaluate(dataset: Path, provider, *, output: Path, source: str, version: str,
             image_mode: str = "nunca", limit: int | None = None,
             request_attempts: int = 1, workers: int = 1, progress=None, cancelled=None,
             rag: bool = False, embedding_model: str = DEFAULT_EMBEDDING_MODEL,
             rag_examples: int = 3, allow_unlabeled: bool = False) -> dict:
    data = load_jsonl(dataset, require_labels=not allow_unlabeled)
    if limit is not None:
        data = data[:max(1, limit)]
    workers = max(1, min(int(workers), len(data) or 1))
    cache = ClassificationCache(DEFAULT_CLASSIFICATION_CACHE) if rag else None
    base_url = ",".join(getattr(provider, "base_urls", ["http://localhost:11434"]))
    retriever = (LocalExampleRetriever(cache, base_url=base_url, model=embedding_model,
                                      limit=rag_examples) if cache else None)
    wall_started = time.monotonic()

    def classify(index, row):
        question = {"enunciado": row["question"], "numero": index,
                    "nota": row.get("supporting_texts", ""),
                    "alternativas": {str(k): {"texto": v} for k, v in (row.get("alternatives") or {}).items()},
                    "revisar": []}
        started = time.monotonic()
        usage_history = getattr(getattr(provider, "_thread_state", None), "usage_history", [])
        usage_history_start = len(usage_history)
        images = []
        if image_mode != "nunca" and row.get("images"):
            for image_path in row["images"][:4]:
                resolved = (dataset.parent / image_path).resolve()
                if dataset.parent.resolve() in resolved.parents and resolved.is_file():
                    images.append(resolved.read_bytes())
        try:
            examples = []
            if retriever:
                # Holdout por ano impede que a validação veja exemplos do mesmo ciclo de prova.
                examples = retriever.retrieve(clf.render_questao(question, None, bool(images)),
                                              exclude_ids={str(row.get("id", ""))},
                                              exclude_year=row.get("year"))
            candidate_topics = ({str(item["result"].get("content_id") or item["result"].get("topic")) for item in examples}
                                if examples else None)
            system = clf.build_system(clf.DEFAULT_TAXONOMY, clf.DEFAULT_TOPIC_TAXONOMY,
                                      candidate_topics)
            result = clf.classify_one(provider,
                                      system,
                                      question, None, clf.DEFAULT_TAXONOMY, images,
                                      clf.DEFAULT_TOPIC_TAXONOMY, examples, candidate_topics)
            error = None
        except Exception as exc:
            result, error = {}, str(exc)
        elapsed = time.monotonic() - started
        thread_state = getattr(provider, "_thread_state", None)
        usage_history = getattr(thread_state, "usage_history", [])
        request_usages = usage_history[usage_history_start:]
        if request_usages:
            usage = {key: sum(int(request.get(key, 0)) for request in request_usages)
                     for key in ("prompt_tokens", "completion_tokens", "eval_duration_ns")}
            usage["calls"] = len(request_usages)
            usage["by_host"] = {}
            for request in request_usages:
                host = request.get("host")
                if host:
                    host_row = usage["by_host"].setdefault(host, {"calls": 0, "prompt_tokens": 0,
                                                                   "completion_tokens": 0,
                                                                   "eval_duration_ns": 0})
                    host_row["calls"] += 1
                    for key in ("prompt_tokens", "completion_tokens", "eval_duration_ns"):
                        host_row[key] += int(request.get(key, 0))
        else:
            usage = dict(getattr(thread_state, "usage", {}) or {})
            usage["calls"] = int(bool(usage))
        outcome = {"gold_subject": row.get("subject"), "pred_subject": result.get("materia"),
                   "gold_topic": row.get("topic"), "pred_topic": result.get("assunto"),
                   "gold_content_id": row.get("content_id"),
                   "pred_content_id": result.get("conteudo_principal_id"),
                   "pred_content_path": result.get("caminho_conteudo"),
                   "seconds": elapsed, "usage": usage, "error": error,
                   "retrieved_examples": result.get("exemplos_referencia", [])}
        return index, row, result, elapsed, error, outcome

    outcomes = [None] * len(data)
    latencies = [0.0] * len(data)
    invalid = 0
    attempts = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="benchmark-question") as pool:
        futures = {pool.submit(classify, index, row): index for index, row in enumerate(data, 1)}
        for completed, future in enumerate(as_completed(futures), 1):
            if cancelled and cancelled():
                for pending in futures:
                    pending.cancel()
                raise InterruptedError("Benchmark cancelado pelo usuário")
            index, row, result, elapsed, error, outcome = future.result()
            outcomes[index - 1] = outcome
            latencies[index - 1] = elapsed
            invalid += int(error is not None)
            attempts += int(result.get("tentativas", 2))
            label = "falha" if error else f"{result.get('materia')} / {result.get('assunto')}"
            print(f"[{index}/{len(data)}] {row.get('id', index)} · {elapsed:.1f}s · {label}", flush=True)
            if progress:
                progress({"completed": completed, "total": len(data), "question": row.get("id", index),
                          "seconds": elapsed, "error": error})
    usage_totals = {key: sum(int(item.get("usage", {}).get(key, 0)) for item in outcomes)
                    for key in ("prompt_tokens", "completion_tokens", "eval_duration_ns", "calls")}
    hosts = {}
    for item in outcomes:
        per_host = item.get("usage", {}).get("by_host") or {}
        if not per_host and item.get("usage", {}).get("host"):
            host = item["usage"]["host"]
            per_host = {host: {**item["usage"], "calls": item["usage"].get("calls", 1)}}
        for host, host_usage in per_host.items():
            entry = hosts.setdefault(host, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                            "eval_duration_ns": 0})
            for key in ("calls", "prompt_tokens", "completion_tokens", "eval_duration_ns"):
                entry[key] += int(host_usage.get(key, 0))
    wall_seconds = max(0.000001, time.monotonic() - wall_started)
    ordered_latencies = sorted(latencies)
    p95 = ordered_latencies[max(0, min(len(ordered_latencies) - 1,
                                      int(0.95 * len(ordered_latencies) + 0.999999) - 1))] if ordered_latencies else 0
    labeled = [item for item in outcomes if item.get("gold_subject") and
               (item.get("gold_content_id") or item.get("gold_topic"))]
    canonical_labeled = [item for item in labeled if item.get("gold_content_id")]
    review_count = sum(not item.get("pred_content_id") for item in outcomes)
    result = {"dataset": str(dataset), "source": source, "dataset_version": version,
              "provider": provider.label, "created_at": datetime.now(timezone.utc).isoformat(),
              "image_mode": image_mode,
              "http_attempts_per_request": request_attempts,
              "count": len(data), "workers": workers, "rag": bool(retriever),
              "embedding_model": embedding_model if retriever else None,
              "invalid_json_or_classification": invalid,
              "attempts": attempts, "latency_seconds": {"mean": statistics.mean(latencies) if latencies else 0,
                                                            "median": statistics.median(latencies) if latencies else 0,
                                                            "p95": p95, "wall": wall_seconds,
                                                            "questions_per_minute": len(data) * 60 / wall_seconds,
                                                            "total": sum(latencies)},
              "labeled_count": len(labeled),
              "subject": score(labeled, "gold_subject", "pred_subject"),
              "topic": score(labeled, "gold_topic", "pred_topic"),
              "content": score(canonical_labeled, "gold_content_id", "pred_content_id"),
              "taxonomy_version": clf.CONTENT_TAXONOMY_VERSION,
              "review_rate": review_count / len(outcomes) if outcomes else 0.0,
              "review_count": review_count,
              "prompt_tokens_per_call": (usage_totals["prompt_tokens"] / usage_totals["calls"]
                                          if usage_totals["calls"] else 0),
              "items": outcomes, "usage": usage_totals, "hosts": hosts}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if cache:
        cache.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSONL de questões; labels opcionais somente para medir desempenho")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--output", type=Path, default=Path("benchmark/results/latest.json"))
    parser.add_argument("--source", default="BLUEX filtrado: USP/Fuvest")
    parser.add_argument("--version", default="unversioned")
    parser.add_argument("--images", choices=("nunca", "figuras", "sempre"), default="nunca",
                        help="figuras/sempre enviam imagens locais listadas no campo images do JSONL")
    parser.add_argument("--limit", type=int, help="avalia só as primeiras N questões, útil para smoke test")
    parser.add_argument("--timeout", type=float, default=60,
                        help="timeout por chamada ao modelo em segundos (padrão: 60)")
    parser.add_argument("--http-attempts", type=int, default=1,
                        help="tentativas HTTP por questão (padrão: 1 para evitar esperas longas)")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--rag", action="store_true", help="inclui exemplos indexados; exclui o mesmo ano no teste")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--rag-examples", type=int, default=3)
    parser.add_argument("--allow-unlabeled", action="store_true",
                        help="permite linhas sem subject/topic e calcula acurácia apenas nas rotuladas")
    parser.add_argument("--ollama-threads", type=int, default=0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.http_attempts < 1 or args.max_tokens < 1 or args.ollama_threads < 0:
        parser.error("timeout, http-attempts e max-tokens devem ser positivos; ollama-threads não pode ser negativo")
    provider = make_provider(args.provider, args.model, base_url=args.base_url, key_env=None, temperature=0,
                             max_tokens=args.max_tokens, json_mode=True, timeout=args.timeout,
                             request_attempts=args.http_attempts, num_threads=args.ollama_threads)
    result = evaluate(args.dataset, provider, output=args.output, source=args.source, version=args.version,
                      image_mode=args.images, limit=args.limit, request_attempts=args.http_attempts,
                      workers=args.workers, rag=args.rag, embedding_model=args.embedding_model,
                      rag_examples=args.rag_examples, allow_unlabeled=args.allow_unlabeled)
    print(json.dumps({key: result[key] for key in ("count", "subject", "topic", "latency_seconds",
                                                   "invalid_json_or_classification", "attempts", "hosts")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
