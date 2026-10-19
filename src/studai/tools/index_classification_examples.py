"""Cria ou atualiza o índice local de exemplos de classificação revisados."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from studai.features.classification.cache import ClassificationCache
from studai.features.classification.rag import DEFAULT_EMBEDDING_MODEL, index_examples
from studai.settings import DEFAULT_CLASSIFICATION_CACHE


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, nargs="?", default=None,
                        help="JSONL revisado; padrão definido por STUDAI_CLASSIFICATION_EXAMPLES")
    parser.add_argument("--base-url", default="http://localhost:11434",
                        help="endereço do Ollama que hospeda o modelo de embeddings")
    parser.add_argument("--model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CLASSIFICATION_CACHE)
    args = parser.parse_args()
    from studai.features.classification.rag import DEFAULT_EXAMPLES
    dataset = (args.dataset or DEFAULT_EXAMPLES).resolve()
    if not dataset.is_file():
        parser.error(f"conjunto JSONL não encontrado: {dataset}")
    if args.batch_size < 1:
        parser.error("--batch-size deve ser maior que zero")
    cache = ClassificationCache(args.cache)
    try:
        result = index_examples(dataset, cache, base_url=args.base_url, model=args.model,
                                batch_size=args.batch_size)
        result["cache"] = str(args.cache.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        cache.close()


if __name__ == "__main__":
    main()
