"""Canonical hierarchical taxonomy for FUVEST content labels."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

TAXONOMY_PATH = Path(__file__).with_name("content_taxonomy.json")


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _walk(nodes: list[dict[str, Any]], *, ancestors: tuple[dict[str, Any], ...] = ()):
    for node in nodes:
        yield node, ancestors
        yield from _walk(node.get("children") or [], ancestors=(*ancestors, node))


def validate_content_taxonomy(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate tree structure and return an ID-indexed map with paths."""
    subjects = payload.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("taxonomia deve conter uma lista não vazia de matérias")
    index: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for subject in subjects:
        if not isinstance(subject, dict) or not subject.get("id") or not subject.get("name"):
            raise ValueError("cada matéria precisa de id e name")
        if subject["id"] in seen_ids:
            raise ValueError(f"id ausente ou duplicado na taxonomia: {subject['id']!r}")
        seen_ids.add(subject["id"])
        if not isinstance(subject.get("children"), list) or not subject["children"]:
            raise ValueError(f"matéria sem áreas/conteúdos: {subject.get('name')}")
        if not subject.get("description") or not subject.get("sources"):
            raise ValueError(f"matéria sem descrição ou fonte: {subject['name']}")
        for node, ancestors in _walk(subject.get("children") or [], ancestors=(subject,)):
            identity = node.get("id")
            if not isinstance(identity, str) or not identity or identity in seen_ids:
                raise ValueError(f"id ausente ou duplicado na taxonomia: {identity!r}")
            seen_ids.add(identity)
            if not isinstance(node.get("name"), str) or not node["name"].strip():
                raise ValueError(f"nó sem name: {identity}")
            if not isinstance(node.get("description"), str) or not node["description"].strip():
                raise ValueError(f"nó sem description: {identity}")
            children = node.get("children") or []
            if not isinstance(children, list):
                raise ValueError(f"children deve ser lista: {identity}")
            if node in subject.get("children", []) and not children:
                raise ValueError(f"área sem conteúdo folha: {identity}")
            aliases = node.get("aliases", [])
            sources = node.get("sources", [])
            if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
                raise ValueError(f"aliases inválidos: {identity}")
            if not isinstance(sources, list) or not all(isinstance(x, dict) for x in sources):
                raise ValueError(f"sources inválidos: {identity}")
            if not sources:
                raise ValueError(f"nó sem fonte: {identity}")
            path_nodes = (*ancestors, node)
            index[identity] = {
                **node,
                "subject_id": subject["id"],
                "subject": subject["name"],
                "path_ids": [part["id"] for part in path_nodes],
                "path": [part["name"] for part in path_nodes],
                "is_leaf": not children,
                # Conteúdo cujos filhos são todos subconteúdos: a 1ª etapa escolhe o
                # conteúdo e a 2ª etapa refina entre os filhos.
                "refinable": bool(children) and all(child.get("level") == "subcontent"
                                                    and not child.get("children") for child in children),
            }
    return index


def load_content_taxonomy(path: Path = TAXONOMY_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload.get("version"):
        raise ValueError("taxonomia hierárquica precisa de version")
    payload["index"] = validate_content_taxonomy(payload)
    return payload


DEFAULT_CONTENT_TAXONOMY = load_content_taxonomy()
CONTENT_TAXONOMY_VERSION = str(DEFAULT_CONTENT_TAXONOMY["version"])
CONTENT_INDEX: dict[str, dict[str, Any]] = DEFAULT_CONTENT_TAXONOMY["index"]
DEFAULT_SUBJECT_IDS = {subject["name"]: subject["id"]
                       for subject in DEFAULT_CONTENT_TAXONOMY["subjects"]}
DEFAULT_CONTENTS_BY_SUBJECT = {
    subject["name"]: [node for node in CONTENT_INDEX.values()
                      if node["subject_id"] == subject["id"] and node["is_leaf"]]
    for subject in DEFAULT_CONTENT_TAXONOMY["subjects"]
}


def is_stage_one_option(node: dict[str, Any]) -> bool:
    """Options listed in the first classification step: refinable contents and plain leaves."""
    return node["refinable"] or (node["is_leaf"] and node.get("level") != "subcontent")


STAGE_ONE_OPTIONS = [node for node in CONTENT_INDEX.values() if is_stage_one_option(node)]


def stage_one_id(content_id: str) -> str | None:
    """Map a leaf or stage-one option to the option the first step must choose."""
    node = CONTENT_INDEX.get(content_id)
    if not node:
        return None
    if is_stage_one_option(node):
        return node["id"]
    parent = CONTENT_INDEX.get(node["path_ids"][-2]) if len(node["path_ids"]) > 1 else None
    return parent["id"] if parent and parent["refinable"] else None


def refinement_options(content_id: str) -> list[dict[str, Any]]:
    """Subcontent leaves the second step chooses from; empty when no refinement exists."""
    node = CONTENT_INDEX.get(content_id)
    if not node or not node["refinable"]:
        return []
    return [CONTENT_INDEX[child["id"]] for child in node.get("children") or []]


def content_path(content_id: str) -> str | None:
    node = CONTENT_INDEX.get(content_id)
    return " → ".join(node["path"]) if node else None


def content_options_for_subject(subject: str) -> list[dict[str, Any]]:
    return DEFAULT_CONTENTS_BY_SUBJECT.get(subject, [])


def public_content_taxonomy() -> dict[str, Any]:
    """Return the source tree without the derived in-memory index."""
    return {key: value for key, value in DEFAULT_CONTENT_TAXONOMY.items() if key != "index"}


def public_content_options() -> dict[str, list[dict[str, str]]]:
    return {subject: [{"id": node["id"], "name": node["name"],
                       "path": " → ".join(node["path"])}
                      for node in nodes]
            for subject, nodes in DEFAULT_CONTENTS_BY_SUBJECT.items()}


def canonical_content_id(value: str, *, subject: str | None = None,
                         allow_refinable: bool = False) -> str | None:
    """Resolve an ID or an exact leaf name/alias without fuzzy guessing.

    ``allow_refinable`` also accepts contents whose subcontent was not chosen yet.
    """
    def accepted(node: dict[str, Any]) -> bool:
        return ((node["is_leaf"] or (allow_refinable and node["refinable"]))
                and (subject is None or node["subject"] == subject))

    if value in CONTENT_INDEX:
        return value if accepted(CONTENT_INDEX[value]) else None
    folded = value.strip().casefold()
    matches = [node["id"] for node in CONTENT_INDEX.values()
               if accepted(node)
               and (node["name"].casefold() == folded
                    or any(alias.casefold() == folded for alias in node.get("aliases", [])))]
    return matches[0] if len(matches) == 1 else None


def aggregate_content_ids(content_ids: list[str]) -> list[str]:
    """Return each selected node and its unique ancestor IDs, ordered by depth.

    Non-leaf contents are accepted so labels made before a content gained
    subcontents still count toward their area in diagnostics.
    """
    result = set()
    for content_id in content_ids:
        node = CONTENT_INDEX.get(content_id)
        if node:
            result.update(node["path_ids"])
    return sorted(result, key=lambda identity: (len(CONTENT_INDEX.get(identity, {}).get("path_ids", [])), identity))
