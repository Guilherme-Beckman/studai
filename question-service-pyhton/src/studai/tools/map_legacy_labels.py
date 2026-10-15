"""Relatório de correspondência entre rótulos legados e IDs canônicos da taxonomia.

Lê (somente leitura) o catálogo antigo ``topic_taxonomy.json``, o cache de
classificação (``STUDAI_CACHE_DIR``: SQLite e ``*.jsonl`` revisados) e as provas
processadas (``questoes.classificado.json`` e ``revisao.json``). Não reescreve
nenhum registro: o resultado é só ``docs/legacy-label-mapping.md`` + ``.json``.

    PYTHONPATH=src .venv/bin/python -m studai.tools.map_legacy_labels
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from studai.features.classification.legacy_mapping import (
    LEGACY_LABEL_MAP_PATH,
    LegacyLabelOccurrence,
    ManualLabelMap,
    build_mapping_report,
    render_markdown_report,
)
from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION
from studai.settings import DEFAULT_QUESTIONS_DIR, SERVICE_ROOT

LEGACY_CATALOG = SERVICE_ROOT / "src" / "studai" / "features" / "classification" / "topic_taxonomy.json"
DEFAULT_OUTPUT = SERVICE_ROOT / "docs" / "legacy-label-mapping.md"


def default_cache_dir() -> Path:
    return Path(os.environ.get("STUDAI_CACHE_DIR", str(SERVICE_ROOT / "simulados" / "cache"))).expanduser()


def _has_canonical_id(record: dict, *keys: str) -> bool:
    return any(isinstance(record.get(key), str) and record[key] in CONTENT_INDEX for key in keys)


def catalog_occurrences(path: Path) -> Iterator[LegacyLabelOccurrence]:
    if not path.exists():
        return
    catalog = json.loads(path.read_text(encoding="utf-8"))
    for subject, topics in catalog.items():
        for topic in topics or []:
            yield LegacyLabelOccurrence(subject, str(topic), "catalog:topic_taxonomy.json")


def cache_occurrences(cache_dir: Path, skipped: Counter) -> Iterator[LegacyLabelOccurrence]:
    database = cache_dir / "classification-cache.sqlite3"
    if database.exists():
        connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT result_json FROM classifications").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            connection.close()
        for (raw,) in rows:
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(result, dict):
                continue
            if _has_canonical_id(result, "conteudo_principal_id"):
                skipped["cache:classification-cache.sqlite3"] += 1
                continue
            yield LegacyLabelOccurrence(result.get("materia") or "", result.get("assunto") or "",
                                        "cache:classification-cache.sqlite3")
    for path in sorted(cache_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line) if line.strip() else None
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or not (row.get("subject") or row.get("topic")):
                continue
            if _has_canonical_id(row, "content_id"):
                skipped[f"cache:{path.name}"] += 1
                continue
            yield LegacyLabelOccurrence(row.get("subject") or "", row.get("topic") or "", f"cache:{path.name}")


def processed_occurrences(questions_dir: Path, skipped: Counter) -> Iterator[LegacyLabelOccurrence]:
    if not questions_dir.exists():
        return
    for path in sorted(questions_dir.rglob("questoes.classificado.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for question in data.get("questoes", []) if isinstance(data, dict) else []:
            classification = question.get("classificacao") if isinstance(question, dict) else None
            if not isinstance(classification, dict):
                continue
            if _has_canonical_id(classification, "conteudo_principal_id"):
                skipped["processed:questoes.classificado.json"] += 1
                continue
            yield LegacyLabelOccurrence(classification.get("materia") or "", classification.get("assunto") or "",
                                        "processed:questoes.classificado.json")
    for path in sorted(questions_dir.rglob("revisao.json")):
        try:
            reviews = json.loads(path.read_text(encoding="utf-8")).get("questions", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        for record in reviews.values() if isinstance(reviews, dict) else []:
            if not isinstance(record, dict) or not (record.get("subject") or record.get("topic")):
                continue
            if _has_canonical_id(record, "content_id"):
                skipped["processed:revisao.json"] += 1
                continue
            yield LegacyLabelOccurrence(record.get("subject") or "", record.get("topic") or "",
                                        "processed:revisao.json")


def run(*, catalog: Path, cache_dir: Path, questions_dir: Path, manual_map: Path | None,
        output: Path) -> dict:
    skipped: Counter = Counter()
    occurrences = [*catalog_occurrences(catalog), *cache_occurrences(cache_dir, skipped),
                   *processed_occurrences(questions_dir, skipped)]
    sources = Counter()
    for item in occurrences:
        sources[item.source] += item.count
    report = build_mapping_report(
        occurrences, manual=ManualLabelMap.load(manual_map),
        extra={"taxonomy_version": CONTENT_TAXONOMY_VERSION,
               "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "sources": dict(sorted(sources.items())),
               "skipped": dict(sorted(skipped.items()))})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown_report(report), encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, default=LEGACY_CATALOG)
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="padrão: STUDAI_CACHE_DIR ou simulados/cache")
    parser.add_argument("--questions-dir", type=Path, default=DEFAULT_QUESTIONS_DIR)
    parser.add_argument("--manual-map", type=Path, default=LEGACY_LABEL_MAP_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="relatório Markdown; o JSON é gravado ao lado com extensão .json")
    args = parser.parse_args(argv)
    report = run(catalog=args.catalog, cache_dir=args.cache_dir or default_cache_dir(),
                 questions_dir=args.questions_dir, manual_map=args.manual_map, output=args.output)
    summary = ", ".join(f"{kind}={value['labels']}" for kind, value in report["by_kind"].items())
    print(f"{report['total_labels']} rótulos ({report['total_occurrences']} ocorrências): {summary}")
    print(f"relatório: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
