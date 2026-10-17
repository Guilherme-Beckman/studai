"""Recuperação local de exemplos revisados para classificação de questões."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from studai.features.classification.cache import ClassificationCache
from studai.integrations.llm import LLMError, http_post
from studai.settings import SERVICE_ROOT

DEFAULT_EMBEDDING_MODEL = os.environ.get("STUDAI_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
_EXAMPLES_ENV = os.environ.get("STUDAI_CLASSIFICATION_EXAMPLES")
DEFAULT_EXAMPLES = (Path(_EXAMPLES_ENV).expanduser() if _EXAMPLES_ENV else
                    SERVICE_ROOT / "simulados" / "cache" / "fuvest-reviewed.jsonl")


def ollama_embed(texts: list[str], *, base_url: str = "http://localhost:11434",
                 model: str = DEFAULT_EMBEDDING_MODEL, timeout: float = 120) -> list[list[float]]:
    """Gera vetores localmente usando Ollama /api/embed (suporta entradas em lote)."""
    if not texts:
        return []
    response = http_post(base_url.rstrip("/") + "/api/embed", {},
                         {"model": model, "input": texts, "truncate": True}, timeout, attempts=1)
    vectors = response.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise LLMError("resposta inválida do Ollama /api/embed")
    try:
        return [[float(value) for value in vector] for vector in vectors]
    except (TypeError, ValueError):
        raise LLMError("embedding inválido retornado pelo Ollama") from None


def example_text(row: dict) -> str:
    parts = [str(row.get("question") or row.get("enunciado") or "").strip()]
    support = str(row.get("supporting_texts") or row.get("texto_base") or "").strip()
    if support:
        parts.append("Texto de apoio: " + support)
    alternatives = row.get("alternatives") or row.get("alternativas") or {}
    for letter, value in sorted(alternatives.items()):
        text = value.get("texto", "") if isinstance(value, dict) else value
        if text:
            parts.append(f"({letter}) {text}")
    return "\n".join(parts)


def _load_reviewed(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"linha {line_number}: JSON inválido: {exc}") from None
        if row.get("reviewed") is False or row.get("review_status") == "pending":
            continue
        if not row.get("id") or not example_text(row).strip():
            continue
        subject = str(row.get("subject") or "").strip()
        content_id = str(row.get("content_id") or "").strip()
        if not subject or not content_id:
            # Old topic-only rows stay readable but do not train the canonical classifier.
            continue
        from studai.features.classification.service import DEFAULT_TAXONOMY
        from studai.features.classification.taxonomy import CONTENT_INDEX, CONTENT_TAXONOMY_VERSION
        if subject not in DEFAULT_TAXONOMY:
            raise ValueError(f"linha {line_number}: matéria fora da taxonomia: {subject!r}")
        content = CONTENT_INDEX.get(content_id)
        if row.get("taxonomy_version") != CONTENT_TAXONOMY_VERSION:
            continue
        if (not content or not (content["is_leaf"] or content["refinable"])
                or content["subject"] != subject):
            raise ValueError(f"linha {line_number}: content_id inválido para {subject}: {content_id!r}")
        rows.append({**row, "subject": subject, "content_id": content_id, "topic": content["name"],
                     "content_path": " → ".join(content["path"])})
    if not rows:
        raise ValueError("o conjunto não contém rótulos de conteúdo canônico revisados")
    return rows


def index_examples(dataset: Path, cache: ClassificationCache, *, base_url: str,
                   model: str = DEFAULT_EMBEDDING_MODEL, batch_size: int = 32) -> dict:
    """Valida e indexa rótulos revisados; reindexar substitui só esta origem."""
    dataset = Path(dataset)
    rows = _load_reviewed(dataset)
    source = "rag:" + str(dataset.resolve())
    texts = [example_text(row) for row in rows]
    canonical = [{"id": row["id"], "year": row.get("year"), "exam": row.get("exam"),
                  "subject": row["subject"], "content_id": row["content_id"], "text": text}
                 for row, text in zip(rows, texts)]
    digest = hashlib.sha256(json.dumps({"model": model, "rows": canonical}, sort_keys=True,
                                      ensure_ascii=False).encode()).hexdigest()
    source_cache = cache.embeddings_by_source(source, model)
    identities = {str(row["id"]) for row in rows}
    prepared = []
    reused = 0
    batch_size = max(1, min(int(batch_size), 128))
    for row, text in zip(rows, texts):
        metadata = {key: row.get(key) for key in
                    ("subject", "topic", "content_id", "taxonomy_version", "content_path",
                     "id", "year", "year_version", "phase", "version", "exam",
                     "source", "reviewed", "images", "source_pdf", "answer_key", "is_annulled",
                     "reference_answer", "reference_short_answer", "grading_guidelines")}
        metadata["exam"] = metadata.get("exam") or "USP"
        metadata["embedding_model"] = model
        metadata["dataset_version"] = digest
        previous = source_cache.get(str(row["id"]))
        if previous and previous["text"] == text and all(
                previous["metadata"].get(key) == metadata.get(key)
                for key in ("subject", "topic", "content_id", "taxonomy_version", "exam")):
            prepared.append((str(row["id"]), text, previous["vector"], metadata))
            reused += 1
        else:
            prepared.append((str(row["id"]), text, None, metadata))

    pending = [entry for entry in prepared if entry[2] is None]
    vectors_by_id = {}
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        vectors = ollama_embed([entry[1] for entry in batch], base_url=base_url, model=model)
        vectors_by_id.update({entry[0]: vector for entry, vector in zip(batch, vectors)})
    for identity, text, vector, metadata in prepared:
        cache.put_embedding(identity, text, vectors_by_id.get(identity, vector), metadata, source=source)
    cache.delete_embeddings_not_in_source(source, identities)
    return {"indexed": len(pending), "reused": reused, "skipped": 0, "source": str(dataset),
            "embedding_model": model, "version": digest}


class LocalExampleRetriever:
    def __init__(self, cache: ClassificationCache, *, base_url: str,
                 model: str = DEFAULT_EMBEDDING_MODEL, limit: int = 3,
                 source_prefix: str = "rag:"):
        self.cache = cache
        self.base_urls = list(dict.fromkeys(url.strip().rstrip("/") for url in base_url.split(",") if url.strip()))
        if not self.base_urls:
            self.base_urls = ["http://localhost:11434"]
        self.model = model
        self.limit = max(1, min(int(limit), 8))
        self.source_prefix = source_prefix

    def retrieve(self, text: str, *, exclude_ids: set[str] | None = None,
                 exclude_year: str | int | None = None,
                 exclude_exam: str | None = None) -> list[dict]:
        last_error = None
        query = None
        for url in self.base_urls:
            try:
                query = ollama_embed([text], base_url=url, model=self.model)[0]
                break
            except Exception as exc:
                last_error = exc
        if query is None:
            raise last_error or LLMError("nenhum servidor Ollama disponível para embeddings")
        hits = self.cache.nearest(query, limit=self.limit * 4, exclude=exclude_ids,
                                  exclude_year=exclude_year, exclude_exam=exclude_exam,
                                  embedding_model=self.model, source_prefix=self.source_prefix)
        return hits[:self.limit]


def render_examples(examples: list[dict]) -> str:
    """Rótulos de exemplos são dados auxiliares, não instruções nem explicações."""
    if not examples:
        return ""
    lines = ["EXEMPLOS DE REFERÊNCIA REVISADOS (use como apoio; classifique a questão atual pelo próprio conteúdo):"]
    for index, item in enumerate(examples, 1):
        result = item["result"]
        excerpt = " ".join(item["text"].split())[:700]
        label = result.get("content_path") or result.get("topic") or result.get("content_id")
        lines.append(f"{index}. Questão semelhante: {excerpt}\n   Matéria: {result['subject']} · Conteúdo: {label}")
    return "\n\n".join(lines)
