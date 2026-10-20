"""Localiza, valida e associa o PDF original de uma prova já extraída.

Usado pelo reprocessamento de uma questão e pelo recorte manual por região. Os
diretórios (uploads, área temporária, acervo) são injetados por quem chama.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

MISSING_PDF_MESSAGE = "PDF original não encontrado. Envie o PDF associado à prova para reprocessar."


def _exam_meta(exam_json: Path) -> tuple[str, str]:
    """(nome do arquivo de origem, sha256) gravados na extração."""
    try:
        data = json.loads(Path(exam_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    return str(data.get("arquivo_origem") or ""), str(data.get("arquivo_sha256") or "")


def _normalized_stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", Path(value).stem.casefold())


def validate_uploaded_pdf(exam_json: Path, content: bytes, filename: str | None) -> None:
    """Confere se o PDF enviado parece ser o da prova (assinatura, extensão e nome)."""
    if not content or not content.startswith(b"%PDF"):
        raise ValueError("envie um arquivo PDF válido para reprocessar a questão")
    if filename and not Path(filename).name.lower().endswith(".pdf"):
        raise ValueError("o arquivo enviado precisa ter extensão PDF")
    origin, _ = _exam_meta(exam_json)
    expected = Path(origin).stem
    if expected and filename and _normalized_stem(filename) != _normalized_stem(expected):
        raise ValueError(f"o arquivo enviado não corresponde à prova {expected}.pdf")


def stage_uploaded_pdf(exam_json: Path, content: bytes, filename: str | None, staging_dir: Path) -> Path:
    """Valida o PDF enviado e o grava numa área temporária isolada (``source-<uuid>.pdf``)."""
    validate_uploaded_pdf(exam_json, content, filename)
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged = staging_dir / f"source-{uuid.uuid4().hex}.pdf"
    staged.write_bytes(content)
    return staged


def resolve_source_pdf(exam_json: Path, exam_id: str, uploads_root: Path,
                       search_roots: tuple[Path, ...] | list[Path] = ()) -> Path:
    """PDF original da prova.

    Ordem: ``uploads/<exam_id>.pdf``, outro upload cujo nome contém o sha256 da
    extração e, por fim, ``search_roots`` (p. ex. o acervo FUVEST), onde só vale o
    arquivo com o mesmo nome **e** o mesmo sha256 gravado na extração.
    """
    origin, digest = _exam_meta(exam_json)
    uploads_root = Path(uploads_root)
    candidates = [uploads_root / f"{exam_id}.pdf"]
    if digest and uploads_root.is_dir():
        candidates.extend(path for path in uploads_root.glob("*.pdf")
                          if path.name != f"{exam_id}.pdf" and digest in path.name)
    for candidate in candidates:
        if candidate.is_file():
            content = candidate.read_bytes()
            if content.startswith(b"%PDF") and (not digest or hashlib.sha256(content).hexdigest() == digest):
                return candidate.resolve()
    if digest and origin:
        name = Path(origin).name
        for root in search_roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for candidate in root.rglob(name):
                if candidate.is_file() and hashlib.sha256(candidate.read_bytes()).hexdigest() == digest:
                    return candidate.resolve()
    raise FileNotFoundError(MISSING_PDF_MESSAGE)


def attach_source_pdf(exam_json: Path, exam_id: str, content: bytes, filename: str | None,
                      uploads_root: Path) -> Path:
    """Associa um PDF enviado à prova (``uploads/<exam_id>.pdf``) sem reprocessar nada.

    Se a extração gravou o sha256 do PDF, o arquivo precisa ser idêntico: assim um
    recorte manual nunca sai de outro caderno (outra versão, outra cor de prova).
    """
    validate_uploaded_pdf(exam_json, content, filename)
    _, digest = _exam_meta(exam_json)
    if digest and hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("o PDF enviado não é o mesmo arquivo usado na extração desta prova")
    uploads_root = Path(uploads_root)
    uploads_root.mkdir(parents=True, exist_ok=True)
    target = uploads_root / f"{exam_id}.pdf"
    temporary = uploads_root / f"{exam_id}.{uuid.uuid4().hex}.pdf.tmp"
    temporary.write_bytes(content)
    os.replace(temporary, target)
    return target
