"""Cache de respostas de IA e de vetores de embeddings, fora do pacote (``STUDAI_CACHE_DIR``).

Duas peças, ambas em SQLite e independentes do cache de classificação:

- ``cached_ai_result``: guarda o resultado *já validado* de uma chamada de texto, identificado
  pelo provedor (rótulo, temperatura, limite de tokens), pelo tipo da chamada e pelos prompts
  exatos. Respostas inválidas nunca são guardadas, então uma falha volta a ser tentada.
- ``EmbeddingCache``/``embed_with_cache``: guarda vetores por (modelo, texto). O mesmo texto com o
  mesmo modelo de embeddings não é enviado de novo ao Ollama, em nenhum host.

Provedores offline (``cacheable = False``, como o de demonstração) e objetos que não declaram
``cacheable`` passam direto, sem tocar no disco. ``STUDAI_AI_CACHE=off`` desliga as duas peças.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Iterable

from studai.settings import cache_dir

AI_RESPONSES_FILE = "ai-responses.sqlite3"
_OFF = {"0", "off", "false", "no", "none"}

_STATS: collections.Counter = collections.Counter()
_STATS_LOCK = threading.Lock()


def ai_cache_enabled() -> bool:
    return os.environ.get("STUDAI_AI_CACHE", "on").strip().casefold() not in _OFF


def default_ai_cache_path() -> Path:
    return cache_dir() / AI_RESPONSES_FILE


def _count(name: str, amount: int = 1) -> None:
    if amount:
        with _STATS_LOCK:
            _STATS[name] += amount


def cache_stats() -> dict[str, int]:
    """Acertos e faltas do processo atual, por tipo (ex.: ``diagnostic_analysis.hit``)."""
    with _STATS_LOCK:
        return dict(_STATS)


def reset_cache_stats() -> None:
    with _STATS_LOCK:
        _STATS.clear()


def provider_identity(provider) -> str | None:
    """Identidade estável do provedor para o cache, ou None quando ele não deve ser cacheado."""
    if provider is None or not getattr(provider, "cacheable", False):
        return None
    label = getattr(provider, "label", None)
    if not label:
        return None
    return json.dumps({"label": str(label), "temperature": getattr(provider, "temperature", None),
                       "max_tokens": getattr(provider, "max_tokens", None),
                       "json_mode": getattr(provider, "json_mode", None)}, sort_keys=True)


def response_key(kind: str, identity: str, system: str, user: str) -> str:
    material = json.dumps({"kind": kind, "provider": identity, "system": system, "user": user},
                          sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class _SQLiteStore:
    def __init__(self, path: Path, schema: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript(schema)
        self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class AIResponseCache(_SQLiteStore):
    """Resultados validados de chamadas de texto (análise diagnóstica, associação de gabarito...)."""

    def __init__(self, path: Path | None = None):
        super().__init__(path or default_ai_cache_path(), """
            CREATE TABLE IF NOT EXISTS ai_responses (
                cache_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                value_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def get(self, key: str):
        with self._lock:
            row = self.db.execute("SELECT value_json FROM ai_responses WHERE cache_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, kind: str, value, model: str = "") -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO ai_responses(cache_key,kind,model,value_json) VALUES(?,?,?,?)",
                            (key, kind, model, json.dumps(value, ensure_ascii=False)))

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self.db.execute("SELECT kind, COUNT(*) FROM ai_responses GROUP BY kind").fetchall()
        return {kind: count for kind, count in rows}


def cached_ai_result(kind: str, provider, system: str, user: str, compute: Callable[[], object], *,
                     cache_path: Path | None = None):
    """Devolve ``compute()``, reaproveitando o resultado de uma chamada idêntica anterior.

    ``compute`` faz a chamada e a validação; um retorno ``None`` significa falha e não é guardado.
    O resultado precisa ser serializável em JSON.
    """
    identity = provider_identity(provider) if ai_cache_enabled() else None
    if identity is None:
        return compute()
    key = response_key(kind, identity, system, user)
    try:
        with AIResponseCache(cache_path) as cache:
            stored = cache.get(key)
    except (sqlite3.Error, OSError, ValueError):
        stored = None
    if stored is not None:
        _count(f"{kind}.hit")
        return stored
    _count(f"{kind}.miss")
    value = compute()
    if value is not None:
        try:
            with AIResponseCache(cache_path) as cache:
                cache.put(key, kind, value, str(getattr(provider, "last_used_model", "")
                                                or getattr(provider, "label", "")))
        except (sqlite3.Error, OSError, TypeError, ValueError):
            pass
    return value


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache(_SQLiteStore):
    """Vetores por (modelo de embeddings, texto). Pode dividir o arquivo SQLite com outro cache."""

    def __init__(self, path: Path | None = None):
        super().__init__(path or default_ai_cache_path(), """
            CREATE TABLE IF NOT EXISTS embedding_vectors (
                model TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (model, text_hash)
            );
        """)

    def get_many(self, model: str, texts: Iterable[str]) -> dict[str, list[float]]:
        hashes = {_text_hash(text): text for text in texts}
        found: dict[str, list[float]] = {}
        with self._lock:
            for digest, text in hashes.items():
                row = self.db.execute("SELECT vector_json FROM embedding_vectors WHERE model=? AND text_hash=?",
                                      (model, digest)).fetchone()
                if row:
                    found[text] = json.loads(row[0])
        return found

    def put_many(self, model: str, vectors: dict[str, list[float]]) -> None:
        with self._lock, self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO embedding_vectors(model,text_hash,vector_json) VALUES(?,?,?)",
                [(model, _text_hash(text), json.dumps(list(vector))) for text, vector in vectors.items()])

    def count(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM embedding_vectors").fetchone()[0]


def embed_with_cache(texts: list[str], *, model: str, embed: Callable[..., list[list[float]]],
                     cache: EmbeddingCache | None, **embed_kwargs) -> list[list[float]]:
    """Chama ``embed(textos, model=..., **embed_kwargs)`` só para os textos ainda sem vetor."""
    if cache is None or not ai_cache_enabled():
        return embed(texts, model=model, **embed_kwargs)
    known = cache.get_many(model, texts)
    missing = list(dict.fromkeys(text for text in texts if text not in known))
    _count("embedding.hit", len(texts) - sum(1 for text in texts if text in missing))
    _count("embedding.miss", len(missing))
    if missing:
        vectors = embed(missing, model=model, **embed_kwargs)
        if len(vectors) != len(missing):
            raise ValueError("o modelo devolveu uma quantidade inválida de embeddings")
        fresh = dict(zip(missing, vectors))
        cache.put_many(model, fresh)
        known.update(fresh)
    return [known[text] for text in texts]
