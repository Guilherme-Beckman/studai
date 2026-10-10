"""Benchmark atual do classificador contra as questões processadas localmente."""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from studai.features.classification import service as clf
from studai.features.questions.review import read_reviews
from studai.integrations.llm import make_provider
from studai.tools.evaluate_classifier import evaluate


def collect_questions(root: Path) -> list[dict]:
    """Combina questões extraídas e rótulos humanos, sem usar previsões como gabarito."""
    selected: dict[str, tuple[Path, dict]] = {}
    for path in sorted(Path(root).rglob("questoes.classificado.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        exam_id = str(data.get("prova") or path.parent.name)
        selected[exam_id] = (path, data)

    rows = []
    for exam_id, (path, data) in sorted(selected.items()):
        reviews = read_reviews(path).get("questions") or {}
        shared_texts = data.get("textos_compartilhados") or {}
        exam_name = str(data.get("arquivo_origem") or path.parent.name)
        year_match = re.search(r"20\d{2}", exam_name + " " + exam_id)
        year = int(year_match.group()) if year_match else None
        for question in data.get("questoes", []):
            review = reviews.get(str(question.get("numero")), {})
            if review.get("status") == "unusable":
                continue
            supporting = str(question.get("nota") or "")
            shared = shared_texts.get(str(question.get("texto_id") or ""))
            if isinstance(shared, dict):
                supporting = "\n\n".join(part for part in (str(shared.get("texto") or ""), supporting) if part)
            elif shared:
                supporting = "\n\n".join(part for part in (str(shared), supporting) if part)
            alternatives = {}
            for letter, value in (question.get("alternativas") or {}).items():
                alternatives[str(letter)] = value.get("texto", "") if isinstance(value, dict) else str(value)
            row = {"id": f"{exam_id}:q{question.get('numero')}", "exam": exam_name,
                   "year": year, "question": str(question.get("enunciado") or ""),
                   "supporting_texts": supporting, "alternatives": alternatives}
            if review.get("status") == "reviewed" and review.get("subject") and review.get("topic"):
                row.update(subject=review["subject"], topic=review["topic"], reviewed=True)
            rows.append(row)
    return rows


def write_report(report: dict, output: Path, *, dataset_count: int, gold_count: int,
                 endpoints: list[str], workers: int, model: str) -> None:
    lat = report["latency_seconds"]
    metrics = report["usage"]
    subject_acc = report["subject"]["accuracy"]
    topic_acc = report["topic"]["accuracy"]
    lines = [
        "# Baseline de classificação",
        "",
        f"Medição registrada em {report['created_at']}.",
        "",
        "## Configuração",
        "",
        f"- Modelo: `{model}`; temperatura 0; saída JSON; contexto Ollama configurado pelo ambiente.",
        f"- Endpoints: {', '.join(f'`{url}`' for url in endpoints)}; workers={workers}, no máximo uma chamada por worker.",
        "- Pipeline atual: taxonomia completa no prompt, sem pré-filtro de tópicos, RAG desativado (índice local vazio), texto sem imagens.",
        f"- Questões extraídas: {dataset_count}; questões com rótulo humano utilizável: {gold_count}.",
        "- As métricas de acerto usam apenas rótulos humanos. Questões sem esses rótulos medem somente desempenho operacional.",
        "",
        "## Resultados",
        "",
        "| Métrica | Resultado |",
        "|---|---:|",
        f"| Acurácia de matéria (n={gold_count}) | {subject_acc:.1%} |",
        f"| Acurácia de tópico (n={gold_count}) | {topic_acc:.1%} |",
        f"| REVISAR | {report['review_count']}/{dataset_count} ({report['review_rate']:.1%}) |",
        f"| Latência média por questão | {lat['mean']:.2f} s |",
        f"| Latência p50 por questão | {lat['median']:.2f} s |",
        f"| Latência p95 por questão | {lat['p95']:.2f} s |",
        f"| Tokens de entrada por chamada (média informada pelo Ollama) | {report['prompt_tokens_per_call']:.0f} |",
        f"| Tokens de entrada no total | {metrics['prompt_tokens']} |",
        f"| Tempo total de parede | {lat['wall']:.2f} s |",
        f"| Chamadas com falha de classificação/JSON | {report['invalid_json_or_classification']} |",
        "",
        "### Distribuição por servidor",
        "",
        "| Endpoint | Chamadas | Tokens de entrada | Tokens de saída |",
        "|---|---:|---:|---:|",
    ]
    for host, usage in sorted(report.get("hosts", {}).items()):
        lines.append(f"| `{host}` | {usage.get('calls', usage.get('questions', 0))} | "
                     f"{usage.get('prompt_tokens', 0)} | {usage.get('completion_tokens', 0)} |")
    failures = [item for item in report.get("items", []) if item.get("error")]
    if failures:
        lines.extend(["", "### Falhas registradas", ""])
        lines.extend(f"- {item.get('error')}" for item in failures)
    lines.extend([
        "",
        "## Limitações e arquivos",
        "",
        "Há 180 questões extraídas no acervo local, mas somente 41 têm rótulos de matéria e tópico confirmados por revisão humana. Portanto, a medição de acerto ainda é preliminar e não representa todas as matérias/tópicos da taxonomia.",
        "As previsões e métricas completas por questão, incluindo latência, tokens e endpoint, estão em `simulados/cache/baseline-qwen3.5-4b.json` (arquivo local não versionado).",
        "",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-root", type=Path, default=Path("simulados/processados"))
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--output", type=Path, default=Path("simulados/cache/baseline-qwen3.5-4b.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/benchmarks/baseline.md"))
    args = parser.parse_args()
    if args.workers < 1 or args.threads < 0 or args.timeout <= 0 or args.max_tokens < 1:
        parser.error("workers, timeout e max-tokens devem ser positivos; threads não pode ser negativo")
    rows = collect_questions(args.processed_root)
    if not rows:
        parser.error(f"nenhuma questão classificada encontrada em {args.processed_root}")
    gold_count = sum(bool(row.get("subject") and row.get("topic")) for row in rows)
    endpoints = list(dict.fromkeys(url.strip().rstrip("/") for url in args.base_url.split(",") if url.strip()))
    with tempfile.TemporaryDirectory(prefix="studai-processed-benchmark-") as temporary:
        dataset = Path(temporary) / "processed-questions.jsonl"
        dataset.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                           encoding="utf-8")
        provider = make_provider("ollama", args.model, base_url=args.base_url, key_env=None,
                                 temperature=0, max_tokens=args.max_tokens, json_mode=True,
                                 timeout=args.timeout, request_attempts=1, num_threads=args.threads)
        result = evaluate(dataset, provider, output=args.output,
                          source="Questões processadas locais", version="baseline-stage-1",
                          image_mode="nunca", request_attempts=1, workers=args.workers,
                          rag=False, allow_unlabeled=True)
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    result["baseline_config"] = {"model": args.model, "base_urls": endpoints,
                                 "workers": args.workers, "threads": args.threads,
                                 "temperature": 0, "max_tokens": args.max_tokens,
                                 "images": "nunca", "rag": False, "topic_prefilter": False}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(result, args.report, dataset_count=len(rows), gold_count=gold_count,
                 endpoints=endpoints, workers=args.workers, model=args.model)
    print(json.dumps({"questions": len(rows), "human_labeled": gold_count,
                      "report": str(args.report), "results": str(args.output),
                      "latency_seconds": result["latency_seconds"],
                      "review_rate": result["review_rate"], "subject": result["subject"],
                      "topic": result["topic"], "usage": result["usage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
