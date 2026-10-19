"""Map legacy (free-text) subject/topic labels to canonical taxonomy IDs.

Deterministic only: exact ID/name/alias match through the taxonomy helpers,
then an explicit manual override table, then an accent/case/punctuation
insensitive match inside the same subject. Nothing fuzzy, nothing generated.
This module never rewrites stored records; it only reports correspondences.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from studai.features.classification.taxonomy import (
    CONTENT_INDEX,
    DEFAULT_SUBJECT_IDS,
    canonical_content_id,
)

LEGACY_LABEL_MAP_PATH = Path(__file__).with_name("legacy_label_map.json")
MATCH_KINDS = ("exact", "alias", "manual", "normalized", "unmatched")


def normalize_label(value: str) -> str:
    """Accent/case-folded, punctuation-insensitive form of a label."""
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


@dataclass(frozen=True)
class LegacyMatch:
    subject: str
    topic: str
    canonical_subject: str | None
    content_id: str | None
    kind: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        node = CONTENT_INDEX.get(self.content_id or "")
        return {"subject": self.subject, "topic": self.topic,
                "canonical_subject": self.canonical_subject,
                "content_id": self.content_id, "kind": self.kind,
                "content_path": " → ".join(node["path"]) if node else None,
                "reason": self.reason}


@dataclass
class ManualLabelMap:
    """Manual overrides: ``{subject: {legacy topic: canonical id}}`` keyed by normalized labels."""
    entries: dict[tuple[str, str], str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ManualLabelMap":
        mappings = payload.get("mappings", payload)
        entries: dict[tuple[str, str], str] = {}
        if not isinstance(mappings, Mapping):
            raise ValueError("legacy_label_map: 'mappings' deve ser um objeto matéria → {tópico: id}")
        for subject, topics in mappings.items():
            if not isinstance(topics, Mapping):
                raise ValueError(f"legacy_label_map: matéria {subject!r} deve mapear tópico → id")
            for topic, content_id in topics.items():
                if not isinstance(content_id, str) or not content_id:
                    raise ValueError(f"legacy_label_map: id inválido para {subject!r}/{topic!r}")
                entries[(normalize_label(subject), normalize_label(topic))] = content_id
        return cls(entries)

    @classmethod
    def load(cls, path: Path | None = LEGACY_LABEL_MAP_PATH) -> "ManualLabelMap":
        if path is None or not Path(path).exists():
            return cls()
        return cls.from_payload(json.loads(Path(path).read_text(encoding="utf-8")))

    def get(self, subject: str, topic: str) -> str | None:
        return self.entries.get((normalize_label(subject), normalize_label(topic)))


def _canonical_subject(subject: str) -> str | None:
    if subject in DEFAULT_SUBJECT_IDS:
        return subject
    folded = normalize_label(subject)
    matches = [name for name, identity in DEFAULT_SUBJECT_IDS.items()
               if normalize_label(name) == folded or identity == subject]
    return matches[0] if len(matches) == 1 else None


def _accepted(node: dict[str, Any], subject: str) -> bool:
    # Same nodes canonical_content_id(..., allow_refinable=True) accepts.
    return node["subject"] == subject and (node["is_leaf"] or node["refinable"])


def map_legacy_label(subject: str, topic: str, *,
                     manual: ManualLabelMap | None = None) -> LegacyMatch:
    """Resolve one legacy (subject, topic) pair to a canonical content ID."""
    subject_text, topic_text = str(subject or "").strip(), str(topic or "").strip()
    canonical_subject = _canonical_subject(subject_text) if subject_text else None

    def result(content_id: str | None, kind: str, reason: str = "") -> LegacyMatch:
        return LegacyMatch(subject_text, topic_text, canonical_subject, content_id, kind, reason)

    if not canonical_subject:
        return result(None, "unmatched", "matéria desconhecida")
    if not topic_text or topic_text.upper() == "REVISAR":
        return result(None, "unmatched", "tópico vazio ou REVISAR")

    identity = canonical_content_id(topic_text, subject=canonical_subject, allow_refinable=True)
    if identity:
        node = CONTENT_INDEX[identity]
        exact = identity == topic_text or node["name"].strip().casefold() == topic_text.casefold()
        return result(identity, "exact" if exact else "alias")

    manual_id = (manual or ManualLabelMap()).get(subject_text, topic_text)
    if manual_id:
        node = CONTENT_INDEX.get(manual_id)
        if node and node["subject"] == canonical_subject:
            return result(manual_id, "manual")
        reason = f"mapeamento manual aponta para id inexistente ou de outra matéria: {manual_id}"
    else:
        reason = ""

    folded = normalize_label(topic_text)
    candidates = sorted({node["id"] for node in CONTENT_INDEX.values()
                         if _accepted(node, canonical_subject)
                         and folded in {normalize_label(node["name"]), normalize_label(node["id"]),
                                        *(normalize_label(alias) for alias in node.get("aliases", []))}})
    if len(candidates) == 1:
        return result(candidates[0], "normalized")
    if len(candidates) > 1:
        return result(None, "unmatched", "ambíguo: " + ", ".join(candidates))
    return result(None, "unmatched", reason or "sem correspondência determinística")


@dataclass(frozen=True)
class LegacyLabelOccurrence:
    subject: str
    topic: str
    source: str
    count: int = 1


def build_mapping_report(occurrences: Iterable[LegacyLabelOccurrence], *,
                         manual: ManualLabelMap | None = None,
                         extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate occurrences by label, map each once and summarize the results."""
    counts: Counter[tuple[str, str]] = Counter()
    sources: dict[tuple[str, str], Counter[str]] = {}
    for item in occurrences:
        key = (str(item.subject or "").strip(), str(item.topic or "").strip())
        counts[key] += item.count
        sources.setdefault(key, Counter())[item.source] += item.count

    labels = []
    for (subject, topic), count in sorted(counts.items()):
        match = map_legacy_label(subject, topic, manual=manual)
        labels.append({**match.as_dict(), "occurrences": count,
                       "sources": dict(sorted(sources[(subject, topic)].items()))})

    by_kind = {kind: {"labels": 0, "occurrences": 0} for kind in MATCH_KINDS}
    by_subject: dict[str, dict[str, int]] = {}
    for label in labels:
        by_kind[label["kind"]]["labels"] += 1
        by_kind[label["kind"]]["occurrences"] += label["occurrences"]
        row = by_subject.setdefault(label["subject"] or "(vazio)", {kind: 0 for kind in MATCH_KINDS})
        row[label["kind"]] += 1
    unmatched = sorted((label for label in labels if label["kind"] == "unmatched"),
                       key=lambda label: (-label["occurrences"], label["subject"], label["topic"]))
    return {"total_labels": len(labels),
            "total_occurrences": sum(counts.values()),
            "by_kind": by_kind,
            "by_subject": dict(sorted(by_subject.items())),
            "unmatched": unmatched,
            "labels": labels,
            **(dict(extra) if extra else {})}


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = ["# Mapeamento de rótulos legados → IDs canônicos", ""]
    if report.get("taxonomy_version"):
        lines.append(f"Taxonomia: `{report['taxonomy_version']}`  ")
    if report.get("generated_at"):
        lines.append(f"Gerado em: {report['generated_at']}  ")
    lines += ["Gerado por `python -m studai.tools.map_legacy_labels`. Nenhum registro armazenado foi "
              "alterado: este relatório só descreve a correspondência.", ""]
    if report.get("sources"):
        lines += ["## Fontes lidas", "", "| Fonte | Ocorrências |", "|---|---:|"]
        lines += [f"| {name} | {count} |" for name, count in report["sources"].items()]
        lines.append("")
    if report.get("skipped"):
        lines += ["Registros ignorados (já possuem ID canônico válido): "
                  + ", ".join(f"{k}: {v}" for k, v in report["skipped"].items()), ""]
    lines += [f"Rótulos distintos: **{report['total_labels']}** · ocorrências: **{report['total_occurrences']}**", "",
              "## Por tipo de correspondência", "", "| Tipo | Rótulos | Ocorrências |", "|---|---:|---:|"]
    lines += [f"| {kind} | {value['labels']} | {value['occurrences']} |"
              for kind, value in report["by_kind"].items()]
    lines += ["", "## Por matéria (rótulos distintos)", "",
              "| Matéria | " + " | ".join(MATCH_KINDS) + " |",
              "|---|" + "---:|" * len(MATCH_KINDS)]
    lines += [f"| {subject} | " + " | ".join(str(row[kind]) for kind in MATCH_KINDS) + " |"
              for subject, row in report["by_subject"].items()]
    lines += ["", f"## Sem correspondência ({len(report['unmatched'])})", "",
              "| Matéria | Rótulo legado | Ocorrências | Fontes | Motivo |", "|---|---|---:|---|---|"]
    for label in report["unmatched"]:
        sources = ", ".join(f"{k}: {v}" for k, v in label["sources"].items())
        topic = label["topic"].replace("|", "\\|")
        lines.append(f"| {label['subject']} | {topic} | {label['occurrences']} | {sources} | {label['reason']} |")
    lines.append("")
    return "\n".join(lines)
