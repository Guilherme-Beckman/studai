"""Cache compartilhado e repositório vetorial simples baseado em SQLite."""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import threading
from pathlib import Path
from typing import Iterable


class ClassificationCache:
    """Cache de resultados e vetores; não compartilha o banco de histórico."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS classifications (
                cache_key TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                identity TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'human',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cache_migrations (
                source TEXT PRIMARY KEY,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                imported_rows INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.db.commit()

    def get(self, key: str) -> tuple[dict, str] | None:
        with self._lock:
            row = self.db.execute("SELECT result_json,model FROM classifications WHERE cache_key=?", (key,)).fetchone()
        return (json.loads(row["result_json"]), row["model"]) if row else None

    def put(self, key: str, result: dict, model: str) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO classifications(cache_key,result_json,model) VALUES(?,?,?)",
                            (key, json.dumps(result, ensure_ascii=False), model))

    def import_jsonl(self, source: Path) -> int:
        """Importa JSONL apenas uma vez; deixa o arquivo de origem intacto."""
        source = Path(source)
        if not source.is_file():
            return 0
        marker = str(source.resolve())
        with self._lock:
            if self.db.execute("SELECT 1 FROM cache_migrations WHERE source=?", (marker,)).fetchone():
                return 0
        rows = []
        for line in source.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                key, result = item["key"], item["resultado"]
                if isinstance(result, dict):
                    rows.append((key, json.dumps(result, ensure_ascii=False), str(item.get("modelo", ""))))
            except (ValueError, KeyError, TypeError):
                continue
        with self._lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO classifications(cache_key,result_json,model) VALUES(?,?,?)", rows)
            self.db.execute("INSERT OR REPLACE INTO cache_migrations(source,imported_rows) VALUES(?,?)",
                            (marker, len(rows)))
        return len(rows)

    def counts(self) -> dict[str, int]:
        return {"classifications": self.db.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
                "embeddings": self.db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]}

    def put_embedding(self, identity: str, text: str, vector: Iterable[float], result: dict,
                      source: str = "human") -> None:
        with self._lock, self.db:
            self.db.execute("""INSERT OR REPLACE INTO embeddings(identity,text,vector_json,result_json,source)
                VALUES(?,?,?,?,?)""", (identity, text, json.dumps(list(vector)),
                                         json.dumps(result, ensure_ascii=False), source))

    def embeddings_by_source(self, source: str, embedding_model: str) -> dict[str, dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT identity,text,vector_json,result_json FROM embeddings WHERE source=?", (source,)
            ).fetchall()
        result = {}
        for row in rows:
            try:
                metadata = json.loads(row["result_json"])
                if metadata.get("embedding_model") == embedding_model:
                    result[row["identity"]] = {"text": row["text"],
                        "vector": json.loads(row["vector_json"]), "metadata": metadata}
            except (ValueError, TypeError):
                continue
        return result

    def delete_embeddings_not_in_source(self, source: str, identities: set[str]) -> int:
        with self._lock, self.db:
            rows = self.db.execute("SELECT identity FROM embeddings WHERE source=?", (source,)).fetchall()
            stale = [row[0] for row in rows if row[0] not in identities]
            self.db.executemany("DELETE FROM embeddings WHERE identity=? AND source=?",
                                [(identity, source) for identity in stale])
        return len(stale)

    def nearest(self, vector: Iterable[float], limit: int = 5, *,
                exclude: set[str] | None = None, exclude_year: str | int | None = None,
                exclude_exam: str | None = None, embedding_model: str | None = None,
                source_prefix: str | None = None) -> list[dict]:
        """Busca cosseno em vetores locais; adequado ao cache pequeno/médio inicial."""
        query = list(vector)
        qnorm = math.sqrt(sum(x * x for x in query)) or 1.0
        hits = []
        exclude = exclude or set()
        with self._lock:
            rows = self.db.execute("SELECT identity,text,vector_json,result_json,source FROM embeddings").fetchall()
        for row in rows:
            try:
                if row["identity"] in exclude:
                    continue
                metadata = json.loads(row["result_json"])
                if source_prefix and not str(row["source"]).startswith(source_prefix):
                    continue
                if embedding_model and metadata.get("embedding_model") != embedding_model:
                    continue
                if exclude_year is not None and str(metadata.get("year", "")) == str(exclude_year):
                    continue
                if exclude_exam and str(metadata.get("exam", "")).casefold() == exclude_exam.casefold():
                    continue
                candidate = json.loads(row["vector_json"])
                if len(candidate) != len(query):
                    continue
                norm = math.sqrt(sum(x * x for x in candidate)) or 1.0
                score = sum(a * b for a, b in zip(query, candidate)) / (qnorm * norm)
                hits.append({"identity": row["identity"], "text": row["text"], "score": score,
                             "result": metadata, "source": row["source"]})
            except (ValueError, TypeError):
                continue
        return sorted(hits, key=lambda item: item["score"], reverse=True)[:max(0, limit)]

    def embedding_signature(self, embedding_model: str) -> str:
        """Fingerprinta corpus, vetores e rótulos usados pelo RAG."""
        with self._lock:
            rows = self.db.execute(
            "SELECT identity,text,vector_json,result_json,source FROM embeddings ORDER BY identity"
            ).fetchall()
        digest = hashlib.sha256()
        count = 0
        for row in rows:
            try:
                metadata = json.loads(row["result_json"])
            except (ValueError, TypeError):
                continue
            if metadata.get("embedding_model") != embedding_model or not str(row["source"]).startswith("rag:"):
                continue
            count += 1
            for value in (row["identity"], row["text"], row["vector_json"], row["result_json"]):
                digest.update(str(value).encode("utf-8"))
                digest.update(b"\0")
        return digest.hexdigest() if count else "empty"

    def delete_embeddings_by_source(self, source: str) -> int:
        """Remove um índice anterior antes de reindexar o mesmo conjunto."""
        with self._lock, self.db:
            cursor = self.db.execute("DELETE FROM embeddings WHERE source=?", (source,))
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self.db.close()
