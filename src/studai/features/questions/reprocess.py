"""Reprocessar uma questão isolada pelo PDF sem perder a revisão humana.

Fluxo em duas etapas:

1. ``create_preview`` extrai o PDF inteiro numa área de trabalho isolada e guarda
   só a questão pedida (e o texto-base dela) em ``preview.json``. Nada muda na prova.
2. ``confirm_preview`` troca apenas os campos de extração (``REPROCESS_FIELDS``)
   da questão nos JSONs da prova, copia as imagens novas e mantém a revisão
   humana (status aprovado, matéria, conteúdo, observação). ``unusable``/``bad_crop``
   voltam a pendente e a camada ``edits`` é descartada, porque foi feita sobre a
   extração anterior.

A função de extração é injetada (``extract(source, outdir, progress) -> Path``),
assim como os diretórios; a API só traduz HTTP e jobs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Callable

from studai.features.questions import review as question_review
from studai.features.questions.source_pdf import stage_uploaded_pdf

ExtractFn = Callable[..., Path]

REPROCESS_ISSUES = {"alternativas_incompletas", "alternativa_vazia", "enunciado_vazio",
                    "glifo_estranho", "formula_matematica"}

# Campos que vêm da extração e são trocados ao confirmar a reextração de uma questão.
REPROCESS_FIELDS = ("pagina", "texto_id", "enunciado", "nota", "alternativas", "figuras_enunciado",
                    "imagens_completas", "blocos", "revisar")

PREVIEW_ID_RE = re.compile(r"[a-f0-9]{32}")
PREVIEW_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class ReprocessCancelled(Exception):
    """O pedido de reprocessamento foi cancelado durante a extração."""


def _question_hash(question: dict) -> str:
    return hashlib.sha256(json.dumps(question, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def find_question(data: dict, number: int) -> dict | None:
    return next((q for q in data.get("questoes", []) if int(q.get("numero", -1)) == int(number)), None)


def reprocess_recommended(question: dict, review: dict | None, corrupt_glyph_re: re.Pattern | None = None) -> bool:
    """Questão inutilizável/recorte ruim, com falha de extração sinalizada, ou cujo texto salvo
    ainda tem glifos que a extração atual reconhece. Qualquer questão pode ser reprocessada;
    estas ganham o destaque na revisão."""
    if (review or {}).get("status") in ("unusable", "bad_crop"):
        return True
    if REPROCESS_ISSUES.intersection(question_review.review_reasons(question)):
        return True
    if corrupt_glyph_re is None:
        return False
    options = question.get("alternativas") or {}
    texts = [question.get("enunciado") or ""] + [
        option if isinstance(option, str) else str((option or {}).get("texto") or "") for option in options.values()]
    return bool(corrupt_glyph_re.search("\n".join(texts)))


def create_preview(*, exam_id: str, exam_json: Path, number: int, workspace_root: Path,
                   extract: ExtractFn, source_pdf: Path | Callable[[], Path] | None = None,
                   uploaded: bytes | None = None, filename: str | None = None,
                   progress: Callable[[dict], None] | None = None,
                   cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Extrai a questão de novo numa área isolada e devolve a prévia (sem alterar a prova).

    ``uploaded`` (PDF enviado pelo usuário) tem prioridade sobre ``source_pdf`` (o PDF
    já associado à prova, ou uma função que o localiza, chamada só depois de conferir
    que a questão existe). Erros deixam a prova e a revisão como estavam.
    """
    exam_json = Path(exam_json)
    workspace_root = Path(workspace_root)
    original = json.loads(exam_json.read_text(encoding="utf-8"))
    old_question = find_question(original, number)
    if old_question is None:
        raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
    review = question_review.read_reviews(exam_json).get("questions", {}).get(str(number), {})
    if uploaded is not None:
        source = stage_uploaded_pdf(exam_json, uploaded, filename, workspace_root)
    elif source_pdf is not None:
        source = Path(source_pdf() if callable(source_pdf) else source_pdf)
    else:
        raise ValueError("informe o PDF da prova")

    workdir = workspace_root / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=False)
    try:
        # A cópia evita que a prévia dependa de um upload temporário externo.
        stable_source = workdir / "source.pdf"
        shutil.copyfile(source, stable_source)
        if uploaded is not None:
            source.unlink(missing_ok=True)

        def report(event):
            if cancelled():
                raise ReprocessCancelled()
            if progress:
                progress(event)

        extracted_path = extract(stable_source, workdir / "extracted", progress=report)
        if cancelled():
            raise ReprocessCancelled()
        extracted = json.loads(Path(extracted_path).read_text(encoding="utf-8"))
        replacement = find_question(extracted, number)
        if replacement is None:
            raise ValueError(f"a questão {number} não foi localizada no PDF enviado")
        if not replacement.get("enunciado", "").strip() and not replacement.get("imagens_completas"):
            raise ValueError(f"a questão {number} foi localizada, mas não gerou texto nem recorte legível")
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        if uploaded is not None:
            source.unlink(missing_ok=True)
        raise
    # Nota nova vem da reextração (caixa "Note e adote" que antes vazava para as
    # alternativas); sem nota nova, mantém a que já existia.
    replacement["nota"] = replacement.get("nota") or old_question.get("nota", "")
    shared_id = replacement.get("texto_id")
    shared_text = (extracted.get("textos_compartilhados") or {}).get(str(shared_id)) if shared_id is not None else None

    preview_id = workdir.name
    metadata = {"previewId": preview_id, "examId": exam_id, "number": number,
                "examJson": str(exam_json.resolve()),
                "sourceSha256": hashlib.sha256(stable_source.read_bytes()).hexdigest(),
                "originalQuestionSha256": _question_hash(old_question),
                "originalReviewStatus": review.get("status"),
                "question": replacement, "sharedText": shared_text}
    (workdir / "preview.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"previewId": preview_id, "examId": exam_id, "number": number,
            "question": replacement, "sharedText": shared_text}


def _preview_dir(workspace_root: Path, preview_id: str) -> Path:
    if not PREVIEW_ID_RE.fullmatch(str(preview_id)):
        raise ValueError("prévia inválida ou expirada")
    root = Path(workspace_root).resolve()
    workdir = (root / preview_id).resolve()
    if not workdir.is_relative_to(root):
        raise ValueError("prévia inválida")
    return workdir


def load_preview(workspace_root: Path, preview_id: str, exam_id: str, number: int | None = None) -> tuple[Path, dict]:
    workdir = _preview_dir(workspace_root, preview_id)
    try:
        preview = json.loads((workdir / "preview.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise FileNotFoundError("prévia não encontrada ou expirada") from None
    if preview.get("examId") != exam_id or (number is not None and int(preview.get("number", -1)) != int(number)):
        raise ValueError("a prévia não corresponde a esta questão")
    return workdir, preview


def preview_asset(workspace_root: Path, exam_id: str, preview_id: str, relative: str) -> Path | None:
    """Imagem de uma prévia, ou ``None`` se o caminho não for válido para esta prova."""
    try:
        workdir, _ = load_preview(workspace_root, preview_id, exam_id)
    except (ValueError, FileNotFoundError):
        return None
    root = (workdir / "extracted").resolve()
    asset = (root / relative).resolve()
    if not asset.is_relative_to(root) or not asset.is_file() or asset.suffix.lower() not in PREVIEW_IMAGE_SUFFIXES:
        return None
    return asset


def discard_preview(workspace_root: Path, exam_id: str, number: int, preview_id: str) -> None:
    workdir, _ = load_preview(workspace_root, preview_id, exam_id, number)
    shutil.rmtree(workdir, ignore_errors=True)


def _remap_images(new_question: dict, new_shared: dict | None, workdir: Path, image_root: Path,
                  preview_id: str) -> None:
    """Copia as imagens da prévia para ``img/`` da prova e troca os caminhos."""
    path_map: dict[str, str | None] = {}
    for old in (new_question.get("figuras_enunciado", []) + new_question.get("imagens_completas", [])):
        path_map[old] = None
    for block in new_question.get("blocos") or []:
        if block.get("imagem"):
            path_map[block["imagem"]] = None
    for option in (new_question.get("alternativas") or {}).values():
        for old in option.get("figuras", []):
            path_map[old] = None
    if new_shared:
        for old in new_shared.get("figuras", []):
            path_map[old] = None
    extracted_root = (workdir / "extracted").resolve()
    for old in path_map:
        rel = Path(old)
        source_image = (extracted_root / rel).resolve()
        if not source_image.is_relative_to(extracted_root) or not source_image.is_file():
            raise ValueError("uma imagem da prévia não está disponível; gere uma nova prévia")
        destination = image_root / f"reproc_{preview_id[:8]}_{rel.name}"
        shutil.copyfile(source_image, destination)
        path_map[old] = f"img/{destination.name}"

    def remap(paths):
        return [path_map.get(path, path) for path in paths or []]

    new_question["figuras_enunciado"] = remap(new_question.get("figuras_enunciado"))
    new_question["imagens_completas"] = remap(new_question.get("imagens_completas"))
    for block in new_question.get("blocos") or []:
        if block.get("imagem"):
            block["imagem"] = path_map.get(block["imagem"], block["imagem"])
    for option in (new_question.get("alternativas") or {}).values():
        option["figuras"] = remap(option.get("figuras"))
    if new_shared:
        new_shared["figuras"] = remap(new_shared.get("figuras"))


def merged_review_after_reprocess(review: dict | None) -> dict:
    """Revisão humana mantida depois de confirmar a reextração.

    Mantém aprovação, matéria, conteúdo, conteúdos relacionados e observação. ``unusable``
    e ``bad_crop`` voltam a pendente (a nova extração precisa ser revista) e a camada
    ``edits`` sai, porque foi feita sobre a extração anterior.
    """
    record = dict(review or {})
    if record.get("status") in ("unusable", "bad_crop"):
        record.pop("status", None)
    record.pop("edits", None)
    record.pop("edited_at", None)
    return record


def confirm_preview(*, exam_id: str, exam_json: Path, number: int, preview_id: str,
                    workspace_root: Path, uploads_root: Path, processed_root: Path | None = None) -> dict:
    """Aplica a prévia à prova atual mantendo a revisão humana; tudo ou nada."""
    exam_json = Path(exam_json)
    workdir, preview = load_preview(workspace_root, preview_id, exam_id, number)
    if str(exam_json.resolve()) != preview.get("examJson"):
        raise ValueError("a prova mudou desde a criação da prévia; gere uma nova prévia")
    data = json.loads(exam_json.read_text(encoding="utf-8"))
    question = find_question(data, number)
    if question is None:
        raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
    if _question_hash(question) != preview.get("originalQuestionSha256"):
        raise ValueError("a questão mudou desde a criação da prévia; gere uma nova prévia")
    if processed_root is not None:
        # Sem revisao.json ao lado desta extração (p. ex. a prova foi processada de novo
        # noutro diretório), herda as decisões humanas da extração anterior da mesma prova.
        question_review.preserve_reviews(processed_root, str(data.get("prova") or exam_id), exam_json)
    reviews = question_review.read_reviews(exam_json)
    review = reviews.get("questions", {}).get(str(number), {})
    if review.get("status") != preview.get("originalReviewStatus"):
        raise ValueError("o estado da questão mudou desde a criação da prévia")

    image_root = exam_json.parent / "img"
    image_root.mkdir(parents=True, exist_ok=True)
    new_question = copy.deepcopy(preview["question"])
    new_shared = copy.deepcopy(preview.get("sharedText")) if preview.get("sharedText") else None
    _remap_images(new_question, new_shared, workdir, image_root, preview_id)

    for field in REPROCESS_FIELDS:
        question[field] = new_question.get(field)
    data["arquivo_sha256"] = preview["sourceSha256"]
    # Sincroniza a extração base, se a prova também mantém esse JSON.
    siblings = [exam_json]
    base_path = exam_json.parent / "questoes.json"
    if exam_json.name != "questoes.json" and base_path.is_file():
        siblings.append(base_path)
    temp_paths = []
    for path in siblings:
        current = json.loads(path.read_text(encoding="utf-8"))
        current["arquivo_sha256"] = preview["sourceSha256"]
        if new_shared:
            current.setdefault("textos_compartilhados", {})[str(new_shared["id"])] = new_shared
            for linked_number in new_shared.get("questoes", []):
                linked = find_question(current, int(linked_number))
                if linked is not None:
                    linked["texto_id"] = str(new_shared["id"])
        target = find_question(current, number)
        if target is None:
            for temporary, _ in temp_paths:
                temporary.unlink(missing_ok=True)
            raise ValueError("a extração base não contém a questão para sincronização")
        for field in REPROCESS_FIELDS:
            target[field] = new_question.get(field)
        temporary = path.with_name(path.name + f".{preview_id}.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_paths.append((temporary, path))

    reviews.setdefault("questions", {})[str(number)] = merged_review_after_reprocess(review)
    review_path = question_review.review_file(exam_json)
    review_temp = review_path.with_name(review_path.name + f".{preview_id}.tmp")
    review_temp.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    uploads_root = Path(uploads_root)
    staged_pdf = uploads_root / f"{exam_id}.{preview_id}.pdf.tmp"
    try:
        uploads_root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(workdir / "source.pdf", staged_pdf)
        os.replace(staged_pdf, uploads_root / f"{exam_id}.pdf")
        for temporary, destination in temp_paths:
            os.replace(temporary, destination)
        os.replace(review_temp, review_path)
    except Exception:
        for temporary, _ in temp_paths:
            temporary.unlink(missing_ok=True)
        review_temp.unlink(missing_ok=True)
        staged_pdf.unlink(missing_ok=True)
        raise
    shutil.rmtree(workdir, ignore_errors=True)
    return {"ok": True, "question": number, **question_review.review_stats(data, reviews)}
