"""Cache and rank embeddings for the closed topic taxonomy."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from studai.features.classification.rag import ollama_embed
from studai.features.classification.taxonomy import DEFAULT_CONTENT_TAXONOMY
from studai.settings import DEFAULT_CLASSIFICATION_CACHE

DEFAULT_TOPIC_EMBEDDING_MODEL = os.environ.get("STUDAI_TOPIC_EMBEDDING_MODEL", "nomic-embed-text")
DEFAULT_TOPIC_EMBEDDING_CACHE = Path(os.environ.get(
    "STUDAI_TOPIC_EMBEDDING_CACHE",
    str(DEFAULT_CLASSIFICATION_CACHE.parent / "topic-embeddings.json"),
))


def taxonomy_rows(taxonomy: dict, level: str = "leaf") -> list[dict[str, str]]:
    """Return searchable content; accept the old flat format for callers in migration.

    ``level="leaf"`` indexes subcontents; ``level="stage1"`` indexes the contents the first
    classification step chooses from, with their subcontent names folded into the text.
    """
    if isinstance(taxonomy, dict) and isinstance(taxonomy.get("subjects"), list):
        index = taxonomy.get("index") or {}
        if not index:
            from studai.features.classification.taxonomy import validate_content_taxonomy
            index = validate_content_taxonomy(taxonomy)
        if level == "stage1":
            from studai.features.classification.taxonomy import is_stage_one_option
            nodes = [node for node in index.values() if is_stage_one_option(node)]
        elif level == "leaf":
            nodes = [node for node in index.values() if node["is_leaf"] and not node.get("deprecated")]
        else:
            raise ValueError("level deve ser 'leaf' ou 'stage1'")
        rows = []
        for node in nodes:
            aliases = list(node.get("aliases", []))
            if level == "stage1":
                aliases += [child["name"] for child in node.get("children") or []]
            rows.append({"content_id": node["id"], "subject": node["subject"], "topic": node["name"],
                         "path": " → ".join(node["path"]), "description": node.get("description", ""),
                         "aliases": " | ".join(aliases)})
        return rows
    return [{"subject": subject, "topic": topic}
            for subject, topics in taxonomy.items() for topic in topics]


def taxonomy_digest(taxonomy: dict) -> str:
    digest_payload = {key: value for key, value in taxonomy.items() if key != "index"}
    payload = json.dumps(digest_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def topic_embedding_text(row: dict[str, str]) -> str:
    return (f"Caminho: {row.get('path') or row['subject'] + ' → ' + row['topic']}\n"
            f"Descrição: {row.get('description', '')}\nTermos equivalentes: {row.get('aliases', '')}")


class TopicEmbeddingIndex:
    """Persist vectors by embedding model and taxonomy hash; rebuild on either change."""

    def __init__(self, path: Path = DEFAULT_TOPIC_EMBEDDING_CACHE, *, model: str = DEFAULT_TOPIC_EMBEDDING_MODEL,
                 base_url: str = "http://localhost:11434", embed=ollama_embed, level: str = "leaf"):
        self.path = Path(path)
        self.level = level
        self.model = model
        self.base_url = base_url
        self.embed = embed

    def build(self, topics_by_subject: dict = DEFAULT_CONTENT_TAXONOMY,
              *, batch_size: int = 64) -> dict:
        rows = taxonomy_rows(topics_by_subject, self.level)
        digest = taxonomy_digest(topics_by_subject)
        cached = self._load(digest, rows)
        cached_vectors = cached.get("vectors", []) if cached else []
        if cached and len(cached_vectors) == len(rows):
            return {"cached": True, "count": len(rows), "taxonomy_hash": digest,
                    "model": self.model, "path": str(self.path)}

        vectors = []
        for offset in range(0, len(rows), max(1, int(batch_size))):
            chunk = rows[offset:offset + max(1, int(batch_size))]
            vectors.extend(self.embed([topic_embedding_text(row) for row in chunk],
                                      base_url=self.base_url, model=self.model))
        if len(vectors) != len(rows) or any(not vector for vector in vectors):
            raise ValueError("O modelo retornou quantidade ou dimensão inválida de embeddings.")
        payload = {"version": 1, "model": self.model, "taxonomy_hash": digest,
                   "topics": rows, "vectors": vectors}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return {"cached": False, "count": len(rows), "taxonomy_hash": digest,
                "model": self.model, "path": str(self.path)}

    def rank(self, vector: list[float], *, limit: int = 10,
             topics_by_subject: dict = DEFAULT_CONTENT_TAXONOMY, subject: str | None = None) -> list[dict]:
        rows = taxonomy_rows(topics_by_subject, self.level)
        payload = self._load(taxonomy_digest(topics_by_subject), rows)
        if not payload:
            raise ValueError("Índice de embeddings ausente ou incompatível; execute build() primeiro.")
        query_norm = math.sqrt(sum(value * value for value in vector))
        if not query_norm:
            return []
        ranked = []
        for row, candidate in zip(payload["topics"], payload["vectors"]):
            if len(candidate) != len(vector) or (subject is not None and row.get("subject") != subject):
                continue
            candidate_norm = math.sqrt(sum(value * value for value in candidate))
            similarity = sum(a * b for a, b in zip(vector, candidate)) / (query_norm * candidate_norm) if candidate_norm else 0
            ranked.append({**row, "score": similarity})
        return sorted(ranked, key=lambda row: row["score"], reverse=True)[:max(0, int(limit))]

    def _load(self, digest: str, expected_topics: list[dict[str, str]]) -> dict | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("model") != self.model or payload.get("taxonomy_hash") != digest:
            return None
        vectors = payload.get("vectors")
        if payload.get("topics") != expected_topics or not isinstance(vectors, list) or len(vectors) != len(expected_topics):
            return None
        dimensions = set()
        for vector in vectors:
            if not isinstance(vector, list) or not vector or any(
                    not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
                return None
            dimensions.add(len(vector))
        if len(dimensions) != 1:
            return None
        return payload

