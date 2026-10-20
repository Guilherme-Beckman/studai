"""Classificador leve de matéria: regressão logística sobre embeddings (ex.: bge-m3).

Serve para pular a 1ª chamada do roteamento hierárquico (a escolha da matéria) quando o
modelo leve está confiante. Fica desligado por padrão (``STUDAI_SUBJECT_MODEL=1`` liga).

- Treino: ``train_subject_model`` recebe vetores e rótulos e devolve um ``SubjectModel``.
  Usa numpy (já instalado como dependência do PyMuPDF); a inferência é Python puro.
- O modelo fica em JSON em ``STUDAI_CACHE_DIR`` (``subject-model-<embedding>.json``).
- ``SubjectClassifier`` junta o modelo com uma função de embedding injetada e um limiar de
  confiança; abaixo do limiar devolve ``None`` e a chamada de matéria ao LLM acontece normalmente.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from studai.features.classification.rag import example_text
from studai.settings import DEFAULT_CLASSIFICATION_CACHE

SUBJECT_MODEL_SCHEMA = 1
DEFAULT_SUBJECT_EMBEDDING_MODEL = os.environ.get("STUDAI_SUBJECT_MODEL_EMBEDDING", "bge-m3")
DEFAULT_MIN_CONFIDENCE = 0.9

Embed = Callable[[list[str]], list[list[float]]]


def subject_model_enabled() -> bool:
    return os.environ.get("STUDAI_SUBJECT_MODEL", "").strip().lower() in {"1", "true", "on", "sim"}


def default_model_path(embedding_model: str = DEFAULT_SUBJECT_EMBEDDING_MODEL) -> Path:
    safe = "".join(c if c.isalnum() or c in "-." else "_" for c in embedding_model)
    return DEFAULT_CLASSIFICATION_CACHE.parent / f"subject-model-{safe}.json"


def question_text(q: dict, shared: dict | None = None) -> str:
    """Texto da questão no mesmo formato usado no treino (``rag.example_text``)."""
    support = "\n".join(part for part in ((shared or {}).get("texto") or "", q.get("nota") or "") if part)
    return example_text({"question": q.get("enunciado") or "", "supporting_texts": support,
                         "alternatives": q.get("alternativas") or {}})


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [float(value) / norm for value in vector]


@dataclass
class SubjectModel:
    """Regressão logística multinomial já treinada (pesos por matéria + viés)."""
    labels: list[str]
    weights: list[list[float]]
    bias: list[float]
    embedding_model: str = DEFAULT_SUBJECT_EMBEDDING_MODEL
    meta: dict = field(default_factory=dict)
    # Média e desvio de cada dimensão no treino: os embeddings normalizados têm um componente
    # comum grande, e padronizar deixa a regressão separar as matérias com bem menos passos.
    center: list[float] | None = None
    scale: list[float] | None = None

    def features(self, vector: Sequence[float]) -> list[float]:
        x = _normalize(vector)
        if len(x) != len(self.weights[0]):
            raise ValueError(f"dimensão do embedding {len(x)} difere da do modelo {len(self.weights[0])}")
        if self.center is not None and self.scale is not None:
            x = [(value - mean) / std for value, mean, std in zip(x, self.center, self.scale)]
        return x

    def predict_proba(self, vector: Sequence[float]) -> dict[str, float]:
        x = self.features(vector)
        logits = [sum(w * v for w, v in zip(row, x)) + b for row, b in zip(self.weights, self.bias)]
        top = max(logits)
        exps = [math.exp(value - top) for value in logits]
        total = sum(exps)
        return {label: value / total for label, value in zip(self.labels, exps)}

    @property
    def signature(self) -> str:
        payload = json.dumps([self.labels, self.embedding_model, self.meta.get("trained_on")],
                             ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()[:10]

    def to_json(self) -> dict:
        return {"schema": SUBJECT_MODEL_SCHEMA, "labels": self.labels, "embedding_model": self.embedding_model,
                "weights": [[round(value, 6) for value in row] for row in self.weights],
                "bias": [round(value, 6) for value in self.bias], "meta": self.meta,
                "center": [round(value, 7) for value in self.center] if self.center is not None else None,
                "scale": [round(value, 7) for value in self.scale] if self.scale is not None else None}

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_json(), ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> "SubjectModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != SUBJECT_MODEL_SCHEMA:
            raise ValueError(f"esquema de modelo de matéria não suportado: {data.get('schema')!r}")
        return cls(labels=list(data["labels"]), weights=data["weights"], bias=data["bias"],
                   embedding_model=data.get("embedding_model") or DEFAULT_SUBJECT_EMBEDDING_MODEL,
                   meta=data.get("meta") or {}, center=data.get("center"), scale=data.get("scale"))


def train_subject_model(vectors: Sequence[Sequence[float]], labels: Sequence[str], *,
                        l2: float = 1e-2, epochs: int = 300, learning_rate: float = 0.5,
                        class_weight: bool = True, embedding_model: str = DEFAULT_SUBJECT_EMBEDDING_MODEL,
                        meta: dict | None = None, standardize: bool = True) -> SubjectModel:
    """Regressão logística multinomial (softmax) com L2, por gradiente em lote completo.

    Os vetores são normalizados (norma 1) e, com ``standardize``, padronizados por dimensão.
    ``class_weight`` equilibra matérias raras (ex.: Educação Física tem 1 exemplo).
    """
    import numpy as np

    if len(vectors) != len(labels) or not vectors:
        raise ValueError("vetores e rótulos devem ter o mesmo tamanho, maior que zero")
    classes = sorted(set(labels))
    if len(classes) < 2:
        raise ValueError("são necessárias pelo menos duas matérias para treinar")
    x = np.asarray([_normalize(vector) for vector in vectors], dtype=np.float64)
    center = scale = None
    if standardize:
        center = x.mean(axis=0)
        scale = x.std(axis=0) + 1e-6
        x = (x - center) / scale
    index = {label: position for position, label in enumerate(classes)}
    y = np.asarray([index[label] for label in labels])
    onehot = np.zeros((len(y), len(classes)))
    onehot[np.arange(len(y)), y] = 1.0
    counts = onehot.sum(axis=0)
    sample_weight = (len(y) / (len(classes) * counts[y])) if class_weight else np.ones(len(y))
    sample_weight = sample_weight / sample_weight.sum()
    weights = np.zeros((len(classes), x.shape[1]))
    bias = np.zeros(len(classes))
    for _ in range(max(1, int(epochs))):
        logits = x @ weights.T + bias
        logits -= logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits)
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        error = (probabilities - onehot) * sample_weight[:, None]
        weights -= learning_rate * (error.T @ x + l2 * weights)
        bias -= learning_rate * error.sum(axis=0)
    return SubjectModel(labels=classes, weights=weights.tolist(), bias=bias.tolist(),
                        embedding_model=embedding_model, meta=dict(meta or {}),
                        center=center.tolist() if center is not None else None,
                        scale=scale.tolist() if scale is not None else None)


def cross_validate(vectors: Sequence[Sequence[float]], labels: Sequence[str], *, folds: int = 5,
                   groups: Sequence[str] | None = None, thresholds: Sequence[float] = (0.5, 0.7, 0.8, 0.9, 0.95),
                   seed: int = 13, **train_kwargs) -> dict:
    """Validação cruzada; com ``groups`` (ex.: a prova), cada dobra deixa provas inteiras de fora.

    Devolve acurácia geral e, por limiar, a cobertura (fração em que o modelo responderia
    sozinho) e a acurácia nessa fração: é o que importa para pular a chamada ao LLM.
    """
    count = len(labels)
    if groups is not None:
        distinct = sorted(set(groups))
        fold_of_group = {group: position % min(folds, len(distinct)) for position, group in enumerate(distinct)}
        fold_of = [fold_of_group[group] for group in groups]
        folds = min(folds, len(distinct))
    else:
        order = list(range(count))
        random.Random(seed).shuffle(order)
        fold_of = [0] * count
        for position, item in enumerate(order):
            fold_of[item] = position % folds
    predictions: list[tuple[str, float, str]] = []
    for fold in range(folds):
        train = [i for i in range(count) if fold_of[i] != fold]
        test = [i for i in range(count) if fold_of[i] == fold]
        if not test or len({labels[i] for i in train}) < 2:
            continue
        model = train_subject_model([vectors[i] for i in train], [labels[i] for i in train], **train_kwargs)
        for i in test:
            probabilities = model.predict_proba(vectors[i])
            best = max(probabilities, key=probabilities.get)
            predictions.append((labels[i], probabilities[best], best))
    correct = sum(gold == pred for gold, _, pred in predictions)
    by_threshold = {}
    for threshold in thresholds:
        confident = [(gold, pred) for gold, conf, pred in predictions if conf >= threshold]
        by_threshold[f"{threshold:.2f}"] = {
            "coverage": len(confident) / len(predictions) if predictions else 0.0,
            "accuracy": (sum(gold == pred for gold, pred in confident) / len(confident)) if confident else 0.0,
            "count": len(confident)}
    per_subject: dict[str, dict] = {}
    for gold, _, pred in predictions:
        entry = per_subject.setdefault(gold, {"count": 0, "correct": 0})
        entry["count"] += 1
        entry["correct"] += int(gold == pred)
    confusions: dict[str, int] = {}
    for gold, _, pred in predictions:
        if gold != pred:
            key = f"{gold} → {pred}"
            confusions[key] = confusions.get(key, 0) + 1
    return {"count": len(predictions), "folds": folds, "grouped": groups is not None,
            "accuracy": correct / len(predictions) if predictions else 0.0,
            "by_threshold": by_threshold, "per_subject": per_subject,
            "confusions": dict(sorted(confusions.items(), key=lambda item: -item[1]))}


class SubjectClassifier:
    """Escolhe a matéria sem LLM quando o modelo leve passa do limiar de confiança."""

    def __init__(self, model: SubjectModel, embed: Embed, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                 secondary_min: float = 0.1):
        self.model = model
        self.embed = embed
        self.min_confidence = float(min_confidence)
        self.secondary_min = float(secondary_min)

    @property
    def signature(self) -> str:
        return f"{self.model.signature}@{self.min_confidence:.2f}"

    def __call__(self, q: dict, shared: dict | None = None,
                 subjects: Sequence[str] | None = None) -> tuple[str, list[str], float] | None:
        """(matéria, secundárias, confiança) ou ``None`` para deixar o LLM decidir."""
        text = question_text(q, shared)
        if not text.strip():
            return None
        try:
            vector = self.embed([text])[0]
            probabilities = self.model.predict_proba(vector)
        except Exception:
            # Sem embedding (Ollama fora do ar, dimensão errada…) o LLM escolhe a matéria.
            return None
        if subjects is not None:
            probabilities = {label: value for label, value in probabilities.items() if label in subjects}
        if not probabilities:
            return None
        ranked = sorted(probabilities.items(), key=lambda item: -item[1])
        subject, confidence = ranked[0]
        if confidence < self.min_confidence:
            return None
        secondary = [label for label, value in ranked[1:3] if value >= self.secondary_min]
        return subject, secondary, round(confidence, 4)


def load_default_subject_classifier(base_url: str = "http://localhost:11434") -> SubjectClassifier | None:
    """Classificador configurado por variáveis de ambiente, ou ``None`` se desligado/ausente."""
    if not subject_model_enabled():
        return None
    path = Path(os.environ.get("STUDAI_SUBJECT_MODEL_PATH") or default_model_path())
    if not path.is_file():
        return None
    model = SubjectModel.load(path)
    threshold = float(os.environ.get("STUDAI_SUBJECT_MODEL_MIN_CONF", DEFAULT_MIN_CONFIDENCE))
    from studai.features.classification.rag import ollama_embed

    first_url = base_url.split(",")[0].strip() or "http://localhost:11434"

    def embed(texts: list[str]) -> list[list[float]]:
        return ollama_embed(texts, base_url=first_url, model=model.embedding_model, timeout=60)

    return SubjectClassifier(model, embed, min_confidence=threshold)


__all__ = ["SubjectClassifier", "SubjectModel", "cross_validate",
           "default_model_path", "load_default_subject_classifier", "question_text",
           "subject_model_enabled", "train_subject_model"]
