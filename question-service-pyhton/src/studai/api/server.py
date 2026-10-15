"""API HTTP local e servidor de arquivos estáticos para a interface do StudAI."""
from __future__ import annotations

import json
import hashlib
import csv
import mimetypes
import re
import secrets
import shutil
import threading
import uuid
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import urllib.error
import urllib.request

from studai.features.answer_keys.service import exam_variant, import_key_file
from studai.features.classification.cache import ClassificationCache
from studai.features.classification import service as classification
from studai.features.classification.rag import DEFAULT_EMBEDDING_MODEL, DEFAULT_EXAMPLES, index_examples
from studai.features.diagnostics import service as diagnostics
from studai.features.questions.extractor import extract_pdf
from studai.features.questions import review as question_review
from studai.integrations.llm import (
    GeminiProvider, OllamaProvider, RotatingPoolProvider, load_dotenv, make_provider,
)
from studai.tools.evaluate_classifier import evaluate as evaluate_classifier, score as score_classifier
from studai.settings import (
    DEFAULT_COURSE_CONFIG,
    DEFAULT_CLASSIFICATION_CACHE,
    DEFAULT_DIAGNOSTICS_DB,
    DEFAULT_QUESTIONS_DIR,
    SERVICE_ROOT,
)
from studai.features.classification.service import DEFAULT_TAXONOMY, DEFAULT_TOPIC_TAXONOMY

WEB_ROOT = SERVICE_ROOT / "frontend"
UPLOADS_ROOT = SERVICE_ROOT / "simulados" / "uploads"
PROCESSED_ROOT = DEFAULT_QUESTIONS_DIR
ANSWER_IMPORTS_ROOT = SERVICE_ROOT / "diagnostico" / "imports-web"
QUESTION_REPROCESS_ROOT = SERVICE_ROOT / "simulados" / "cache" / "question-reprocess"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="studai-job")
BENCHMARK_REPORT = SERVICE_ROOT / "simulados" / "cache" / "edubench-baseline-100.json"
BENCHMARK_DATASET = SERVICE_ROOT / "simulados" / "cache" / "edubench-reviewed-canonical.jsonl"
BENCHMARK_REVIEW_CSV = SERVICE_ROOT / "simulados" / "cache" / "edubench-review-canonical.csv"
RAG_STATUS_FILE = SERVICE_ROOT / "simulados" / "cache" / "fuvest-rag-status.json"
RAG_INDEX_LOCK = threading.Lock()
AI_OPTIONS_LOCK = threading.Lock()
AI_OPTIONS_CACHE: tuple[float, dict] | None = None


def _read_rag_status() -> dict:
    try:
        status = json.loads(RAG_STATUS_FILE.read_text(encoding="utf-8"))
        return status if isinstance(status, dict) else {"status": "empty"}
    except (OSError, json.JSONDecodeError):
        return {"status": "empty", "indexed": 0}


def _write_rag_status(status: dict) -> None:
    RAG_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = RAG_STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(RAG_STATUS_FILE)


def _run_review_index(base_urls: str) -> dict:
    """Rebuild the FUVEST manifest and update only changed local embeddings."""
    with RAG_INDEX_LOCK:
        _write_rag_status({"status": "indexing"})
        try:
            count = question_review.write_reviewed_dataset(PROCESSED_ROOT, DEFAULT_EXAMPLES)
            cache = ClassificationCache(DEFAULT_CLASSIFICATION_CACHE)
            try:
                source = "rag:" + str(DEFAULT_EXAMPLES.resolve())
                if not count:
                    removed = cache.delete_embeddings_by_source(source)
                    result = {"indexed": 0, "removed": removed, "version": "empty"}
                else:
                    result = index_examples(DEFAULT_EXAMPLES, cache, base_url=base_urls,
                                            model=DEFAULT_EMBEDDING_MODEL)
            finally:
                cache.close()
            status = {"status": "ready" if count else "empty", "indexed": result.get("indexed", 0),
                      "reused": result.get("reused", 0), "examples": count,
                      "version": result.get("version")}
            _write_rag_status(status)
            return status
        except Exception as exc:
            status = {"status": "pending", "error": str(exc)}
            _write_rag_status(status)
            return status


def _queue_review_index(exam_json: Path) -> str:
    meta_path = exam_json.parent / "processamento.meta.json"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        metadata = {}
    base_urls = ",".join(metadata.get("ollama_base_urls") or []) or os.environ.get(
        "OLLAMA_BASE_URL", "http://localhost:11434")
    _write_rag_status({"status": "queued"})
    return _submit_job("Indexar questões revisadas", lambda _cancelled, _job_id: _run_review_index(base_urls))


def _read_benchmark_review() -> dict:
    if not BENCHMARK_REPORT.is_file() or not BENCHMARK_DATASET.is_file():
        raise FileNotFoundError("Relatório ou conjunto do benchmark não encontrado em simulados/cache")
    report = json.loads(BENCHMARK_REPORT.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in BENCHMARK_DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    items = report.get("items") or []
    if len(items) != len(rows):
        raise ValueError("O relatório e o conjunto revisado têm quantidades diferentes; gere novamente o benchmark")
    questions = []
    scored_rows = []
    mismatches = 0
    reference_changed = 0
    for index, (gold, result) in enumerate(zip(rows, items)):
        mismatch = gold.get("subject") != result.get("pred_subject") or gold.get("topic") != result.get("pred_topic")
        mismatches += int(mismatch)
        reference_changed += int(gold.get("subject") != result.get("gold_subject") or
                                 gold.get("topic") != result.get("gold_topic"))
        scored_rows.append({"gold_subject": gold.get("subject"), "pred_subject": result.get("pred_subject"),
                            "gold_topic": gold.get("topic"), "pred_topic": result.get("pred_topic")})
        questions.append({"index": index, "id": gold.get("id", str(index + 1)),
                          "year": gold.get("year"), "question": gold.get("question", ""),
                          "supportingTexts": gold.get("supporting_texts", ""),
                          "subject": gold.get("subject", ""), "topic": gold.get("topic", ""),
                          "predictedSubject": result.get("pred_subject"),
                          "predictedTopic": result.get("pred_topic"),
                          "seconds": result.get("seconds"), "error": result.get("error"),
                          "mismatch": mismatch, "reviewed": bool(gold.get("reviewed", False))})
    report_view = {key: report.get(key) for key in (
        "source", "dataset_version", "provider", "created_at", "image_mode", "count",
        "invalid_json_or_classification", "attempts", "latency_seconds", "subject", "topic", "usage")}
    original_metrics = {"subject": report.get("subject"), "topic": report.get("topic")}
    report_view["subject"] = score_classifier(scored_rows, "gold_subject", "pred_subject")
    report_view["topic"] = score_classifier(scored_rows, "gold_topic", "pred_topic")
    return {"report": report_view, "originalMetrics": original_metrics,
        "questions": questions, "mismatchCount": mismatches,
        "referenceChangedCount": reference_changed,
        "subjects": list(DEFAULT_TAXONOMY), "topics": DEFAULT_TOPIC_TAXONOMY}


def _save_benchmark_label(body: dict) -> dict:
    index = int(body.get("index", -1))
    subject = str(body.get("subject") or "").strip()
    topic = str(body.get("topic") or "").strip()
    if subject not in DEFAULT_TAXONOMY:
        raise ValueError("Selecione uma matéria válida da taxonomia StudAI")
    if topic not in DEFAULT_TOPIC_TAXONOMY.get(subject, []):
        raise ValueError("Selecione um tópico válido para a matéria escolhida")
    rows = [json.loads(line) for line in BENCHMARK_DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not 0 <= index < len(rows):
        raise ValueError("Questão de revisão não encontrada")
    row = rows[index]
    if body.get("id") and body["id"] != row.get("id"):
        raise ValueError("O conjunto foi alterado desde que a revisão foi aberta; atualize a tela")
    row.update(subject=subject, topic=topic, reviewed=True)
    dataset_tmp = BENCHMARK_DATASET.with_suffix(".jsonl.tmp")
    dataset_tmp.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")
    dataset_tmp.replace(BENCHMARK_DATASET)

    # Keep the human-editable source sheet in sync with the benchmark JSONL.
    if BENCHMARK_REVIEW_CSV.is_file():
        with BENCHMARK_REVIEW_CSV.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = reader.fieldnames or []
            csv_rows = list(reader)
        if index < len(csv_rows) and csv_rows[index].get("id") == row.get("id"):
            csv_rows[index]["subject"] = subject
            csv_rows[index]["topic"] = topic
            if "reviewed" in fields:
                csv_rows[index]["reviewed"] = "true"
            if "topic_canonical" in fields:
                csv_rows[index]["topic_canonical"] = topic
            csv_tmp = BENCHMARK_REVIEW_CSV.with_suffix(".csv.tmp")
            with csv_tmp.open("w", encoding="utf-8-sig", newline="") as destination:
                writer = csv.DictWriter(destination, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(csv_rows)
            csv_tmp.replace(BENCHMARK_REVIEW_CSV)
    return {"ok": True, "subject": subject, "topic": topic,
            "requiresRerun": True, "reviewed": True,
            "message": "Revisão registrada. Execute novamente o benchmark para atualizar as métricas."}


class JobCancelled(Exception):
    pass


def _set_job(job_id: str, **values) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(values)


def _submit_job(label: str, task, job_id: str | None = None) -> str:
    job_id = job_id or uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        # A browser may retry an upload after a reload while the original request
        # is still being accepted. Reuse the same job instead of starting twice.
        if job_id in JOBS:
            return job_id
        JOBS[job_id] = {"id": job_id, "label": label, "status": "queued", "message": "Na fila",
                        "progress": 0, "stage": "queued", "cancelRequested": False}

    def cancelled():
        with JOBS_LOCK:
            return bool(JOBS.get(job_id, {}).get("cancelRequested"))

    def run():
        if cancelled():
            _set_job(job_id, status="cancelled", stage="cancelled", message="Cancelado antes de iniciar")
            return
        _set_job(job_id, status="running", message="Em andamento")
        try:
            result = task(cancelled, job_id)
            _set_job(job_id, status="completed", stage="completed", progress=100, message="Concluído", result=result)
        except JobCancelled:
            _set_job(job_id, status="cancelled", stage="cancelled", message="Processamento cancelado")
        except Exception as exc:
            _set_job(job_id, status="failed", stage="failed", message=str(exc), error=str(exc))

    JOB_POOL.submit(run)
    return job_id


def _provider(payload: dict, *, max_tokens: int = 1600):
    load_dotenv(SERVICE_ROOT / ".env")
    name = str(payload.get("provider") or "demo")
    model = str(payload.get("model") or "")
    if not model and name == "ollama":
        model = "qwen3.5:4b"
    if not model and name == "gemini":
        model = "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash"
    try:
        threads = int(payload.get("ollamaThreads") or 0)
        if not 0 <= threads <= 64:
            raise ValueError("ollamaThreads deve estar entre 0 e 64")
        return make_provider(
            name,
            model,
            base_url=payload.get("baseUrl") or payload.get("base_url"),
            key_env=payload.get("apiKeyEnv") or payload.get("api_key_env"),
            temperature=0,
            max_tokens=max_tokens,
            json_mode=True,
            num_threads=threads,
        )
    except SystemExit as exc:
        raise ValueError(str(exc)) from None


def _gemini_api_keys() -> list[str]:
    """Return configured Gemini keys without ever exposing them to the browser."""
    plural = os.environ.get("GEMINI_API_KEYS", "")
    raw_values = [plural] if plural.strip() else [os.environ.get("GEMINI_API_KEY", ""),
                                                  os.environ.get("GOOGLE_API_KEY", "")]
    values = [part.strip() for raw in raw_values for part in re.split(r"[,;\n]", raw) if part.strip()]
    return list(dict.fromkeys(values))


def _gemini_models_for_key(api_key: str) -> set[str]:
    url = f"{GeminiProvider.default_base_url}/models?pageSize=1000"
    request = urllib.request.Request(url, headers={"x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return set()
    models = set()
    for item in payload.get("models", []):
        name = str(item.get("name") or "").removeprefix("models/")
        methods = item.get("supportedGenerationMethods") or []
        lowered = name.casefold()
        if (name.startswith("gemini-") and "generateContent" in methods
                and not any(part in lowered for part in ("embedding", "tts", "live", "image", "veo", "lyria", "robotics", "transcribe"))):
            models.add(name)
    return models


def _available_gemini_models(keys: list[str]) -> list[str]:
    if not keys:
        return []
    available: set[str] = set()
    for key in keys:
        available.update(_gemini_models_for_key(key))
    return sorted(available)


def _ollama_models(base_urls: str | None = None) -> tuple[list[str], bool]:
    urls = list(dict.fromkeys(url.strip().rstrip("/") for url in
                              (base_urls or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")).split(",")
                              if url.strip()))
    found: set[str] = set()
    available = False
    for base_url in urls:
        request = urllib.request.Request(f"{base_url}/api/tags")
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.loads(response.read())
            available = True
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        for item in payload.get("models", []):
            name = str(item.get("name") or item.get("model") or "").strip()
            capabilities = {str(cap).casefold() for cap in item.get("capabilities", [])}
            generation_capabilities = {"completion", "generate", "tools", "vision"}
            embedding_only = "embedding" in capabilities and not (capabilities & generation_capabilities)
            if name and not embedding_only and "embed" not in name.casefold():
                found.add(name)
    return sorted(found), available


def _ai_options() -> dict:
    global AI_OPTIONS_CACHE
    with AI_OPTIONS_LOCK:
        now = time.monotonic()
        if AI_OPTIONS_CACHE and now - AI_OPTIONS_CACHE[0] < 60:
            return dict(AI_OPTIONS_CACHE[1])
        load_dotenv(SERVICE_ROOT / ".env")
        keys = _gemini_api_keys()
        ollama_models, ollama_available = _ollama_models()
        options = {
            "ollamaModels": ollama_models,
            "ollamaAvailable": ollama_available,
            "geminiModels": _available_gemini_models(keys),
            "geminiConfigured": bool(keys),
            "geminiKeyCount": len(keys),
        }
        AI_OPTIONS_CACHE = (now, options)
        return dict(options)


def _pool_provider(payload: dict, *, max_tokens: int = 1600) -> RotatingPoolProvider:
    load_dotenv(SERVICE_ROOT / ".env")
    split_models = lambda value: list(dict.fromkeys(
        part.strip() for part in re.split(r"[,;\n]", str(value or "")) if part.strip()))
    use_ollama = str(payload.get("useOllama", "true")).strip().casefold() not in {"false", "0", "no", "off"}
    selected_ollama = split_models(payload.get("ollamaModels")) if use_ollama else []
    selected_gemini = split_models(payload.get("geminiModels"))
    keys = _gemini_api_keys()
    if use_ollama:
        options = _ai_options()
    else:
        options = {"ollamaModels": [], "geminiModels": _available_gemini_models(keys)}
    if len(selected_ollama) > 2:
        raise ValueError("selecione no máximo dois modelos Ollama")
    if any(model not in options["ollamaModels"] for model in selected_ollama):
        raise ValueError("um dos modelos Ollama selecionados não está instalado ou disponível")
    if any(model not in options["geminiModels"] for model in selected_gemini):
        raise ValueError("um dos modelos Gemini selecionados não está disponível para as chaves configuradas")
    if selected_gemini and not keys:
        raise ValueError("configure GEMINI_API_KEYS no arquivo .env para usar Gemini")
    if not selected_ollama and not selected_gemini:
        raise ValueError("selecione ao menos um modelo Ollama ou Gemini")
    try:
        threads = int(payload.get("ollamaThreads") or 0)
    except (TypeError, ValueError):
        raise ValueError("ollamaThreads inválido") from None
    if not 0 <= threads <= 64:
        raise ValueError("ollamaThreads deve estar entre 0 e 64")
    lanes: list[tuple[str, object]] = []
    base_url = payload.get("baseUrl") or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    base_urls = list(dict.fromkeys(url.strip().rstrip("/") for url in base_url.split(",") if url.strip()))
    host_models: dict[str, set[str]] = {}
    if selected_ollama:
        for url in base_urls:
            models, available = _ollama_models(url)
            if available:
                host_models[url] = set(models)
        if not host_models:
            raise ValueError("nenhum servidor Ollama configurado respondeu à consulta de modelos")
    for model in selected_ollama:
        matching_hosts = [url for url, models in host_models.items() if model in models]
        if not matching_hosts:
            raise ValueError(f"o modelo Ollama {model} não está disponível em nenhum servidor configurado")
        for url in matching_hosts:
            local_provider = make_provider("ollama", model, base_url=url, key_env=None,
                                           temperature=0, max_tokens=max_tokens, json_mode=True,
                                           num_threads=threads)
            lanes.append((f"Ollama ({model} @ {url})", local_provider))
    for index, key in enumerate(keys if selected_gemini else [], 1):
        gemini_provider = GeminiProvider(model=",".join(selected_gemini), api_key=key,
                                         temperature=0, max_tokens=max_tokens, json_mode=True)
        lanes.append((f"Gemini chave {index} ({','.join(selected_gemini)})", gemini_provider))
    if len(lanes) > 64:
        raise ValueError("o pool pode ter no máximo 64 faixas; reduza as chaves ou os modelos locais")
    return RotatingPoolProvider(lanes, temperature=0, max_tokens=max_tokens, json_mode=True)


def _exam_files() -> list[tuple[dict, Path]]:
    exams = []
    for path in sorted(PROCESSED_ROOT.rglob("questoes*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("questoes"), list):
            continue
        source_id = diagnostics.source_id_for(data, path)
        has_errors = bool((data.get("classificacao") or {}).get("erros"))
        exam_name = Path(str(data.get("arquivo_origem") or path.parent.name)).stem
        exams.append(({"id": source_id, "name": exam_name,
                       "totalQuestions": len(data["questoes"]),
                       "classified": "classificado" in path.name and not has_errors,
                       "subjects": sorted({q.get("materia") or (q.get("classificacao") or {}).get("materia")
                                            for q in data["questoes"]
                                            if q.get("materia") or (q.get("classificacao") or {}).get("materia")})}, path))
    # Keep only the classified JSON when both extraction versions exist.
    by_id: dict[str, tuple[dict, Path]] = {}
    for summary, path in exams:
        previous = by_id.get(summary["id"])
        if previous is None or (summary["classified"] and not previous[0]["classified"]):
            by_id[summary["id"]] = (summary, path)
    return list(by_id.values())


def _find_exam(exam_id: str) -> tuple[dict, Path]:
    for summary, path in _exam_files():
        if summary["id"] == exam_id:
            return summary, path
    raise FileNotFoundError(f"simulado não encontrado: {exam_id}")


def _source_pdf(exam_id: str, exam_json: Path, uploaded: bytes | None = None,
                filename: str | None = None) -> Path:
    """Resolve the stored source PDF, or saves a user-provided replacement in an isolated workspace."""
    if uploaded is not None:
        if not uploaded.startswith(b"%PDF"):
            raise ValueError("envie um arquivo PDF válido para reprocessar a questão")
        if filename and not Path(filename).name.lower().endswith(".pdf"):
            raise ValueError("o arquivo enviado precisa ter extensão PDF")
        expected = Path(str(json.loads(exam_json.read_text(encoding="utf-8")).get("arquivo_origem") or "")).stem
        if expected and filename:
            normalize = lambda value: re.sub(r"[^a-z0-9]", "", Path(value).stem.casefold())
            if normalize(filename) != normalize(expected):
                raise ValueError(f"o arquivo enviado não corresponde à prova {expected}.pdf")
        QUESTION_REPROCESS_ROOT.mkdir(parents=True, exist_ok=True)
        replacement = QUESTION_REPROCESS_ROOT / f"source-{uuid.uuid4().hex}.pdf"
        replacement.write_bytes(uploaded)
        return replacement

    candidates = [UPLOADS_ROOT / f"{exam_id}.pdf"]
    try:
        data = json.loads(exam_json.read_text(encoding="utf-8"))
        digest = str(data.get("arquivo_sha256") or "")
    except (OSError, json.JSONDecodeError):
        digest = ""
    if digest and UPLOADS_ROOT.is_dir():
        candidates.extend(path for path in UPLOADS_ROOT.glob("*.pdf")
                          if path.name != f"{exam_id}.pdf" and digest in path.name)
    for candidate in candidates:
        if candidate.is_file():
            content = candidate.read_bytes()
            if content.startswith(b"%PDF") and (not digest or hashlib.sha256(content).hexdigest() == digest):
                return candidate.resolve()
    raise FileNotFoundError("PDF original não encontrado. Envie o PDF associado à prova para reprocessar.")


def _reprocess_question(exam_id: str, number: int, uploaded: bytes | None,
                        filename: str | None, cancelled, job_id: str) -> dict:
    _, exam_json = _find_exam(exam_id)
    original = json.loads(exam_json.read_text(encoding="utf-8"))
    old_question = next((q for q in original.get("questoes", [])
                         if int(q.get("numero", -1)) == number), None)
    if old_question is None:
        raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
    review = question_review.read_reviews(exam_json).get("questions", {}).get(str(number), {})
    extraction_issues = {"alternativas_incompletas", "alternativa_vazia", "enunciado_vazio",
                         "glifo_estranho", "formula_matematica"}
    eligible = review.get("status") == "unusable" or bool(
        extraction_issues.intersection(question_review.review_reasons(old_question)))
    if review.get("status") not in (None, "unusable") or not eligible:
        raise ValueError("esta questão não tem uma falha de extração elegível para reprocessamento")

    workdir = QUESTION_REPROCESS_ROOT / uuid.uuid4().hex
    workdir.mkdir(parents=True, exist_ok=False)
    try:
        source = _source_pdf(exam_id, exam_json, uploaded, filename)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    if not source.is_relative_to(workdir.resolve()):
        # Copiar evita que o fluxo de prévia dependa de um upload temporário externo.
        stable_source = workdir / "source.pdf"
        shutil.copyfile(source, stable_source)
        if source.parent.resolve() == QUESTION_REPROCESS_ROOT.resolve() and source.name.startswith("source-"):
            source.unlink(missing_ok=True)
        source = stable_source

    def progress(event):
        if cancelled():
            raise JobCancelled()
        _set_job(job_id, stage=event["stage"], message=event["message"],
                 progress=15 if event["stage"] == "extracting" else 65)

    try:
        extracted_path = extract_pdf(source, workdir / "extracted", dpi=200, full_crops=True,
                                     detailed_crops=True, progress=progress)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    if cancelled():
        shutil.rmtree(workdir, ignore_errors=True)
        raise JobCancelled()
    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
    replacement = next((q for q in extracted.get("questoes", [])
                        if int(q.get("numero", -1)) == number), None)
    if replacement is None:
        shutil.rmtree(workdir, ignore_errors=True)
        raise ValueError(f"a questão {number} não foi localizada no PDF enviado")
    if not replacement.get("enunciado", "").strip() and not replacement.get("imagens_completas"):
        shutil.rmtree(workdir, ignore_errors=True)
        raise ValueError(f"a questão {number} foi localizada, mas não gerou texto nem recorte legível")
    replacement["nota"] = old_question.get("nota", "")
    shared_id = replacement.get("texto_id")
    shared_text = (extracted.get("textos_compartilhados") or {}).get(str(shared_id)) if shared_id is not None else None

    preview_id = workdir.name
    question_hash = hashlib.sha256(json.dumps(old_question, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    metadata = {"previewId": preview_id, "examId": exam_id, "number": number,
                "examJson": str(exam_json.resolve()), "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "originalQuestionSha256": question_hash, "originalReviewStatus": review.get("status"),
                "question": replacement, "sharedText": shared_text}
    (workdir / "preview.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_job(job_id, stage="preview_ready", message=f"Prévia da questão {number} pronta", progress=100)
    return {"previewId": preview_id, "examId": exam_id, "number": number,
            "question": replacement, "sharedText": shared_text,
            "assetPrefix": f"/api/exams/{exam_id}/reprocess-previews/{preview_id}/assets"}


def _confirm_question_reprocess(exam_id: str, number: int, preview_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", preview_id):
        raise ValueError("prévia inválida ou expirada")
    workdir = (QUESTION_REPROCESS_ROOT / preview_id).resolve()
    if not workdir.is_relative_to(QUESTION_REPROCESS_ROOT.resolve()):
        raise ValueError("prévia inválida")
    try:
        preview = json.loads((workdir / "preview.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise FileNotFoundError("prévia não encontrada ou expirada") from None
    if preview.get("examId") != exam_id or int(preview.get("number", -1)) != number:
        raise ValueError("a prévia não corresponde a esta questão")
    _, selected_path = _find_exam(exam_id)
    if str(selected_path.resolve()) != preview.get("examJson"):
        raise ValueError("a prova mudou desde a criação da prévia; gere uma nova prévia")
    data = json.loads(selected_path.read_text(encoding="utf-8"))
    question = next((q for q in data.get("questoes", []) if int(q.get("numero", -1)) == number), None)
    if question is None:
        raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
    current_question_hash = hashlib.sha256(json.dumps(question, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if current_question_hash != preview.get("originalQuestionSha256"):
        raise ValueError("a questão mudou desde a criação da prévia; gere uma nova prévia")
    reviews = question_review.read_reviews(selected_path)
    review = reviews.get("questions", {}).get(str(number), {})
    if review.get("status") != preview.get("originalReviewStatus"):
        raise ValueError("o estado da questão mudou desde a criação da prévia")

    image_root = selected_path.parent / "img"
    image_root.mkdir(parents=True, exist_ok=True)
    new_question = json.loads(json.dumps(preview["question"], ensure_ascii=False))
    path_map = {}
    for old in (new_question.get("figuras_enunciado", []) + new_question.get("imagens_completas", [])):
        path_map[old] = None
    for option in (new_question.get("alternativas") or {}).values():
        for old in option.get("figuras", []):
            path_map[old] = None
    new_shared = json.loads(json.dumps(preview.get("sharedText"), ensure_ascii=False)) if preview.get("sharedText") else None
    if new_shared:
        for old in new_shared.get("figuras", []):
            path_map[old] = None
    for old in path_map:
        rel = Path(old)
        source_image = (workdir / "extracted" / rel).resolve()
        if not source_image.is_relative_to((workdir / "extracted").resolve()) or not source_image.is_file():
            raise ValueError("uma imagem da prévia não está disponível; gere uma nova prévia")
        destination = image_root / f"reproc_{preview_id[:8]}_{rel.name}"
        shutil.copyfile(source_image, destination)
        path_map[old] = f"img/{destination.name}"
    def remap(paths):
        return [path_map.get(path, path) for path in paths or []]
    new_question["figuras_enunciado"] = remap(new_question.get("figuras_enunciado"))
    new_question["imagens_completas"] = remap(new_question.get("imagens_completas"))
    for option in (new_question.get("alternativas") or {}).values():
        option["figuras"] = remap(option.get("figuras"))
    if new_shared:
        new_shared["figuras"] = remap(new_shared.get("figuras"))

    for field in ("pagina", "texto_id", "enunciado", "alternativas", "figuras_enunciado",
                  "imagens_completas", "revisar"):
        question[field] = new_question.get(field)
    data["arquivo_sha256"] = preview["sourceSha256"]
    # Sincroniza a extração base, se a prova também mantém esse JSON.
    siblings = [selected_path]
    base_path = selected_path.parent / "questoes.json"
    if selected_path.name != "questoes.json" and base_path.is_file():
        siblings.append(base_path)
    temp_paths = []
    for path in siblings:
        current = json.loads(path.read_text(encoding="utf-8"))
        current["arquivo_sha256"] = preview["sourceSha256"]
        if new_shared:
            current.setdefault("textos_compartilhados", {})[str(new_shared["id"])] = new_shared
            for linked_number in new_shared.get("questoes", []):
                linked = next((item for item in current.get("questoes", [])
                               if int(item.get("numero", -1)) == int(linked_number)), None)
                if linked is not None:
                    linked["texto_id"] = str(new_shared["id"])
        target = next((q for q in current.get("questoes", []) if int(q.get("numero", -1)) == number), None)
        if target is None:
            raise ValueError("a extração base não contém a questão para sincronização")
        for field in ("pagina", "texto_id", "enunciado", "alternativas", "figuras_enunciado",
                      "imagens_completas", "revisar"):
            target[field] = new_question.get(field)
        temporary = path.with_name(path.name + f".{preview_id}.tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_paths.append((temporary, path))

    # A revisão volta a ficar pendente se o classificador ou a nova extração ainda sinalizarem algo.
    record = dict(review)
    if record.get("status") == "unusable":
        record.pop("status", None)
    reviews.setdefault("questions", {})[str(number)] = record
    review_path = question_review.review_file(selected_path)
    review_temp = review_path.with_name(review_path.name + f".{preview_id}.tmp")
    review_temp.write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
        associated_pdf = UPLOADS_ROOT / f"{exam_id}.pdf"
        staged_pdf = UPLOADS_ROOT / f"{exam_id}.{preview_id}.pdf.tmp"
        shutil.copyfile(workdir / "source.pdf", staged_pdf)
        os.replace(staged_pdf, associated_pdf)
        for temporary, destination in temp_paths:
            os.replace(temporary, destination)
        os.replace(review_temp, review_path)
    except Exception:
        for temporary, _ in temp_paths:
            temporary.unlink(missing_ok=True)
        review_temp.unlink(missing_ok=True)
        if "staged_pdf" in locals():
            staged_pdf.unlink(missing_ok=True)
        raise
    shutil.rmtree(workdir, ignore_errors=True)
    return {"ok": True, "question": number,
            **question_review.review_stats(data, reviews)}


def _delete_exam(exam_id: str) -> dict:
    root = PROCESSED_ROOT.resolve()
    matches = []
    for path in PROCESSED_ROOT.rglob("questoes*.json"):
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                continue
            data = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and diagnostics.source_id_for(data, resolved) == exam_id:
            matches.append(resolved)
    if not matches:
        raise FileNotFoundError(f"simulado não encontrado: {exam_id}")

    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        answer_rows = db.execute("SELECT source_pdf FROM answer_keys WHERE source_id=?", (exam_id,)).fetchall()
        answer_count = db.execute("SELECT COUNT(*) FROM answer_keys WHERE source_id=?", (exam_id,)).fetchone()[0]
        historical_questions = db.execute(
            "SELECT COUNT(*) FROM attempt_questions WHERE source_id=?", (exam_id,)).fetchone()[0]
        with db:
            db.execute("DELETE FROM answer_keys WHERE source_id=?", (exam_id,))
    finally:
        db.close()

    removed_answer_files = 0
    answer_root = ANSWER_IMPORTS_ROOT.resolve()
    for row in answer_rows:
        try:
            source = Path(row["source_pdf"]).resolve()
            if source.is_relative_to(answer_root) and source.is_file():
                source.unlink()
                removed_answer_files += 1
        except (OSError, ValueError):
            continue

    removed_files = 0
    removed_dirs = set()
    for question_path in matches:
        exam_dir = question_path.parent
        for pattern in ("questoes*.json", "revisao.json", "processamento*.json", "*.classificacao.cache.jsonl"):
            for artifact in exam_dir.glob(pattern):
                try:
                    if artifact.is_file() or artifact.is_symlink():
                        artifact.unlink()
                        removed_files += 1
                except OSError:
                    continue
        image_dir = exam_dir / "img"
        if image_dir.is_dir() and not image_dir.is_symlink():
            shutil.rmtree(image_dir)
            removed_dirs.add(str(image_dir))
        try:
            exam_dir.rmdir()
            removed_dirs.add(str(exam_dir))
        except OSError:
            pass

    upload = (UPLOADS_ROOT / f"{exam_id}.pdf").resolve()
    try:
        if upload.is_relative_to(UPLOADS_ROOT.resolve()) and upload.is_file():
            upload.unlink()
            removed_files += 1
    except (OSError, ValueError):
        pass

    base_urls = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    _write_rag_status({"status": "queued"})
    rag_job_id = _submit_job("Atualizar índice de questões revisadas",
                             lambda _cancelled, _job_id: _run_review_index(base_urls))
    return {"ok": True, "removedQuestionFiles": removed_files, "removedDirectories": len(removed_dirs),
            "removedAnswerKeys": answer_count, "removedAnswerFiles": removed_answer_files,
            "preservedHistoricalQuestions": historical_questions, "ragJobId": rag_job_id}


def _list_exams() -> list[dict]:
    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        keys = {row[0]: row[1] for row in db.execute(
            "SELECT source_id,COUNT(*) FROM answer_keys GROUP BY source_id")}
    finally:
        db.close()
    result = []
    for summary, path in _exam_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"questoes": []}
        progress = question_review.review_stats(data, question_review.read_reviews(path))
        result.append({**summary, **progress, "importedAnswers": keys.get(summary["id"], 0),
                       "ragStatus": _read_rag_status().get("status", "empty")})
    return result


def _list_courses() -> list[dict]:
    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
    return [{"id": course_id, "name": profile.get("nome", course_id),
             "subjects": list(profile.get("pesos", {}))}
            for course_id, profile in config["cursos"].items()]


def _diagnostic_payload(db, attempt_id: str) -> dict:
    attempt = diagnostics.get_attempt(db, attempt_id)
    rows = diagnostics.list_questions_for_attempt(db, attempt_id)
    questions = []
    for row in rows:
        question = json.loads(row["question_json"])
        # Internal source paths are only needed by the CLI for local image display.
        question.pop("_source_dir", None)
        questions.append({"ordinal": row["ordinal"], "subject": row["subject"], "topic": row["topic"],
                          "question": question, "selectedAnswer": row["selected_answer"],
                          "errorReason": row["error_reason"], "answered": row["answered_at"] is not None})
    return {
        "id": attempt["id"], "course": attempt["course_name"], "status": attempt["status"],
        "requested": attempt["requested_count"], "total": attempt["actual_count"],
        "createdAt": attempt["created_at"], "metrics": attempt.get("metrics"),
        "analysis": attempt.get("analysis"), "analysisError": attempt.get("analysis_error"),
        "questions": questions,
    }


def _process_exam(filename: str, content: bytes, payload: dict, job_id: str | None = None, cancelled=lambda: False) -> dict:
    clean_name = Path(filename).name
    if not clean_name.lower().endswith(".pdf") or not content.startswith(b"%PDF"):
        raise ValueError("envie um arquivo PDF válido")
    provider = (_pool_provider(payload) if str(payload.get("provider") or "") == "pool"
                else _provider(payload))
    workers = provider.lane_count if isinstance(provider, RotatingPoolProvider) else int(payload.get("workers") or 2)
    if not 1 <= workers <= 64:
        raise ValueError("workers deve estar entre 1 e 64")
    if isinstance(provider, classification.OllamaProvider):
        parallel_limit = max(1, int(os.environ.get("OLLAMA_NUM_PARALLEL", "1")))
        per_host = (workers + len(provider.base_urls) - 1) // len(provider.base_urls)
        if per_host > parallel_limit:
            warnings.warn(f"{per_host} workers por servidor excedem OLLAMA_NUM_PARALLEL={parallel_limit}; "
                          "o Ollama pode enfileirar requisições.", RuntimeWarning)
        cpu_budget = max(0, int(os.environ.get("STUDAI_CPU_THREAD_BUDGET", "0")))
        if cpu_budget and provider.num_threads and workers * provider.num_threads > cpu_budget:
            warnings.warn("workers × ollamaThreads excede STUDAI_CPU_THREAD_BUDGET.", RuntimeWarning)
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    pdf_hash = hashlib.sha256(content).hexdigest()
    stem = Path(clean_name).stem
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "simulado"
    stable_exam_id = f"{safe_stem}-{pdf_hash[:12]}"
    dpi = int(payload.get("dpi") or 160)
    extraction_key = hashlib.sha256(f"{pdf_hash}:{dpi}".encode("utf-8")).hexdigest()
    source = UPLOADS_ROOT / f"{stable_exam_id}.pdf"
    output_dir = PROCESSED_ROOT / "web" / extraction_key
    output_dir.mkdir(parents=True, exist_ok=True)
    meta_path = output_dir / "processamento.meta.json"
    if isinstance(provider, classification.OllamaProvider):
        embedding_urls = provider.base_urls
    elif isinstance(provider, RotatingPoolProvider):
        embedding_urls = list(dict.fromkeys(
            url for _, lane in provider.lanes if isinstance(lane, classification.OllamaProvider)
            for url in lane.base_urls))
    else:
        embedding_urls = []
    meta_path.write_text(json.dumps({"exam_id": stable_exam_id,
                                     "ollama_base_urls": embedding_urls}, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    if not source.exists():
        source.write_bytes(content)
    def extraction_progress(event):
        if cancelled():
            raise JobCancelled()
        if job_id:
            _set_job(job_id, status="running", stage=event["stage"], message=event["message"], progress=5 if event["stage"] == "extracting" else 16)

    extraction_progress({"stage": "upload_saved", "message": "Arquivo recebido e salvo", "progress": 3})
    extracted_path = output_dir / "questoes.json"
    if extracted_path.exists():
        extraction_progress({"stage": "extracted", "message": "Extração da prova reutilizada do cache", "questions": len(json.loads(extracted_path.read_text(encoding="utf-8")).get("questoes", []))})
    else:
        extracted_path = extract_pdf(source, output_dir, dpi=dpi, progress=extraction_progress)
        extracted_data = json.loads(extracted_path.read_text(encoding="utf-8"))
        extracted_data["prova"] = stable_exam_id
        extracted_data["arquivo_origem"] = clean_name
        extracted_data["arquivo_sha256"] = pdf_hash
        extracted_path.write_text(json.dumps(extracted_data, ensure_ascii=False, indent=2), encoding="utf-8")
    question_review.preserve_reviews(PROCESSED_ROOT, stable_exam_id, extracted_path)
    classified_path = output_dir / "questoes.classificado.json"
    cache_meta_path = output_dir / "processamento.cache.json"
    try:
        if cancelled():
            raise JobCancelled()
        images_mode = str(payload.get("images") or "nunca")
        cache_signature = hashlib.sha256(json.dumps({
            "pdf": pdf_hash, "dpi": dpi, "provider": provider.label,
            "taxonomy": classification.DEFAULT_TAXONOMY,
            "promptVersion": classification.PROMPT_VERSION, "images": images_mode,
        }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        question_total = len(json.loads(extracted_path.read_text(encoding="utf-8")).get("questoes", []))
        try:
            cached_meta = json.loads(cache_meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_meta = {}
        if cached_meta.get("signature") == cache_signature and classified_path.is_file():
            cached_result = json.loads(classified_path.read_text(encoding="utf-8"))
            if job_id:
                _set_job(job_id, stage="classifying", message="Este simulado já foi classificado · reutilizando resultado", progress=96,
                         completed=question_total, total=question_total, cachedQuestions=question_total,
                         cacheMisses=0, estimatedSavedCalls=question_total, activeQuestions=[])
            return {"examId": str(cached_result.get("prova") or stable_exam_id), "name": clean_name,
                    "questions": question_total, "classified": True, "examCacheHit": True,
                    "cachedQuestions": question_total, "cacheMisses": 0,
                    "estimatedSavedCalls": question_total,
                    "output": str(classified_path.relative_to(SERVICE_ROOT))}
        if job_id:
            _set_job(job_id, stage="classifying", message=f"Classificando {question_total} questões", progress=20,
                     completed=0, total=question_total, activeQuestions=[])
        def classification_progress(event):
            if cancelled():
                raise JobCancelled()
            done, total = event["completed"], event["total"]
            percent = 20 + int(70 * done / max(total, 1))
            if job_id:
                _set_job(job_id, stage="classifying", message=event["message"], progress=percent,
                         completed=done, total=total, errors=event.get("errors", 0),
                         cachedQuestions=event.get("cached", 0), cacheMisses=event.get("cache_misses", 0),
                         estimatedSavedCalls=event.get("cached", 0), activeQuestions=event.get("active", []))
        result = classification.classificar(
            extracted_path,
            provider,
            classification.DEFAULT_TAXONOMY,
            workers=workers,
            imagens=images_mode,
            progress=classification_progress,
            cancel_check=cancelled,
            use_rag=not isinstance(provider, RotatingPoolProvider),
        )
        if cancelled():
            raise JobCancelled()
        classified_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        cache_info = result.get("processamento_cache", {})
        classification_errors = result.get("classificacao", {}).get("erros", {})
        if classification_errors:
            cache_meta_path.unlink(missing_ok=True)
        else:
            cache_meta_path.write_text(json.dumps({"signature": cache_signature, "pdf_sha256": pdf_hash,
                                                   "provider": provider.label, "prompt_version": classification.PROMPT_VERSION},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
        return {"examId": str(result.get("prova") or stable_exam_id),
                "name": clean_name, "questions": len(result.get("questoes", [])),
                "classified": not bool(classification_errors), "examCacheHit": False,
                "cachedQuestions": int(cache_info.get("questoes_reutilizadas", 0)),
                "cacheMisses": int(cache_info.get("cache_misses", 0)),
                "estimatedSavedCalls": int(cache_info.get("estimated_saved_calls", 0)),
                "classificationError": (f"{len(classification_errors)} questões tiveram falha na classificação" if classification_errors else None),
                "output": str(classified_path.relative_to(SERVICE_ROOT))}
    except JobCancelled:
        raise
    except Exception as exc:
        data = json.loads(extracted_path.read_text(encoding="utf-8"))
        return {"examId": str(data.get("prova") or stable_exam_id), "name": clean_name,
                "questions": len(data.get("questoes", [])), "classified": False,
                "classificationError": str(exc), "output": str(extracted_path.relative_to(SERVICE_ROOT))}


class Handler(BaseHTTPRequestHandler):
    server_version = "StudAI-Local/1.0"

    def log_message(self, fmt, *args):
        print("API:", fmt % args)

    def _json(self, status: int, value: dict | list):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("o corpo deve ser um objeto JSON")
        return value

    def _static(self, filename: str):
        path = WEB_ROOT / filename
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/":
                return self._static("index.html")
            if path in ("/app.js", "/styles.css"):
                return self._static(path.lstrip("/"))
            if path == "/api/health":
                return self._json(200, {"status": "ok", "mode": "local"})
            if path == "/api/ai/options":
                return self._json(200, _ai_options())
            if path == "/api/providers":
                return self._json(200, [{"id": "demo", "name": "Demonstração offline"},
                                        {"id": "ollama", "name": "Ollama local"},
                                        {"id": "mock", "name": "Classificação simulada"}])
            if path == "/api/exams":
                return self._json(200, _list_exams())
            if path == "/api/courses":
                return self._json(200, _list_courses())
            if path == "/api/benchmark/review":
                return self._json(200, _read_benchmark_review())
            if path == "/api/diagnostics":
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    rows = db.execute("SELECT id,course_name,status,requested_count,actual_count,created_at "
                                      "FROM attempts ORDER BY created_at DESC").fetchall()
                    return self._json(200, [dict(row) for row in rows])
                finally:
                    db.close()
            match = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
            if match:
                with JOBS_LOCK:
                    job = JOBS.get(match.group(1))
                    if job:
                        job = dict(job)
                return self._json(200, job) if job else self._json(404, {"error": "tarefa não encontrada"})
            match = re.fullmatch(r"/api/diagnostics/([^/]+)", path)
            if match:
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, _diagnostic_payload(db, match.group(1)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/exams/([^/]+)/questions", path)
            if match:
                summary, file_path = _find_exam(match.group(1))
                data = json.loads(file_path.read_text(encoding="utf-8"))
                human_reviews = question_review.read_reviews(file_path).get("questions") or {}
                questions = []
                for question in data["questoes"]:
                    number = str(question.get("numero"))
                    questions.append({**question, "review": human_reviews.get(number),
                                      "reviewReasons": question_review.review_reasons(question),
                                      "needsReview": bool(question_review.review_reasons(question))})
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    answer_keys = {str(row[0]): {"answer": row[1], "annulled": bool(row[2])}
                                   for row in db.execute(
                                       "SELECT question_number,answer,is_annulled FROM answer_keys WHERE source_id=?",
                                       (summary["id"],))}
                finally:
                    db.close()
                return self._json(200, {"exam": {**summary, "importedAnswers": len(answer_keys)}, "questions": questions,
                                        "sharedTexts": data.get("textos_compartilhados") or {},
                                        "answerKeys": answer_keys,
                                        "reviewProgress": question_review.review_stats(
                                            data, {"questions": human_reviews}),
                                        "subjects": list(DEFAULT_TAXONOMY),
                                        "topics": DEFAULT_TOPIC_TAXONOMY,
                                        "ragStatus": _read_rag_status()})
            match = re.fullmatch(r"/api/exams/([^/]+)/assets/(.+)", path)
            if match:
                _, file_path = _find_exam(match.group(1))
                asset_path = (file_path.parent / match.group(2)).resolve()
                asset_root = file_path.parent.resolve()
                if (not asset_path.is_relative_to(asset_root) or not asset_path.is_file()
                        or asset_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}):
                    self.send_error(404)
                    return
                body = asset_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
                return
            match = re.fullmatch(r"/api/exams/([^/]+)/reprocess-previews/([a-f0-9]{32})/assets/(.+)", path)
            if match:
                exam_id, preview_id, relative = match.groups()
                workdir = (QUESTION_REPROCESS_ROOT / preview_id).resolve()
                if not workdir.is_relative_to(QUESTION_REPROCESS_ROOT.resolve()):
                    self.send_error(404)
                    return
                try:
                    preview = json.loads((workdir / "preview.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self.send_error(404)
                    return
                asset_path = (workdir / "extracted" / relative).resolve()
                asset_root = (workdir / "extracted").resolve()
                if (preview.get("examId") != exam_id or not asset_path.is_relative_to(asset_root)
                        or not asset_path.is_file() or asset_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}):
                    self.send_error(404)
                    return
                body = asset_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(404, {"error": "rota não encontrada"})
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_DELETE(self):
        path = unquote(urlsplit(self.path).path)
        try:
            match = re.fullmatch(r"/api/exams/([^/]+)", path)
            if not match:
                return self._json(404, {"error": "rota não encontrada"})
            return self._json(200, _delete_exam(match.group(1)))
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_PATCH(self):
        path = unquote(urlsplit(self.path).path)
        try:
            match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/review", path)
            if not match:
                return self._json(404, {"error": "rota não encontrada"})
            exam_id, number = match.group(1), int(match.group(2))
            _, exam_json = _find_exam(exam_id)
            body = self._read_json()
            status = str(body.get("status") or "")
            if status not in {"reviewed", "unusable"}:
                raise ValueError("status deve ser reviewed ou unusable")
            data = json.loads(exam_json.read_text(encoding="utf-8"))
            question = next((q for q in data.get("questoes", [])
                             if int(q.get("numero", -1)) == number), None)
            if question is None:
                raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
            if not question_review.review_reasons(question):
                raise ValueError("esta questão não está marcada para revisão pela IA")
            values = {"status": status, "note": str(body.get("note") or "")}
            if len(values["note"]) > 2000:
                raise ValueError("a observação deve ter no máximo 2000 caracteres")
            if status == "reviewed":
                subject = str(body.get("subject") or "").strip()
                topic = str(body.get("topic") or "").strip()
                if subject not in DEFAULT_TAXONOMY:
                    raise ValueError("selecione uma matéria válida")
                if topic not in DEFAULT_TOPIC_TAXONOMY.get(subject, []):
                    raise ValueError("selecione um tópico válido para a matéria")
                values.update(subject=subject, topic=topic)
            else:
                values.update(subject=None, topic=None)
            saved = question_review.save_review(exam_json, number, values)
            job_id = _queue_review_index(exam_json)
            return self._json(200, {**saved, "ragStatus": "queued", "ragJobId": job_id})
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            reprocess_match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/reprocess", path)
            if reprocess_match:
                exam_id, number = reprocess_match.group(1), int(reprocess_match.group(2))
                query = parse_qs(parsed.query)
                filename = (query.get("filename") or [self.headers.get("X-Filename")])[0]
                length = int(self.headers.get("Content-Length", "0"))
                uploaded = self.rfile.read(length) if length else None
                job_id = _submit_job("Reprocessar questão", lambda cancelled, current_job:
                                      _reprocess_question(exam_id, number, uploaded, filename, cancelled, current_job))
                return self._json(202, {"jobId": job_id})
            confirm_match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/reprocess/([a-f0-9]{32})/confirm", path)
            if confirm_match:
                exam_id, number, preview_id = confirm_match.groups()
                return self._json(200, _confirm_question_reprocess(exam_id, int(number), preview_id))
            cancel_preview_match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/reprocess/([a-f0-9]{32})", path)
            if cancel_preview_match:
                exam_id, number, preview_id = cancel_preview_match.groups()
                workdir = (QUESTION_REPROCESS_ROOT / preview_id).resolve()
                if not workdir.is_relative_to(QUESTION_REPROCESS_ROOT.resolve()):
                    raise ValueError("prévia inválida")
                try:
                    preview = json.loads((workdir / "preview.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raise FileNotFoundError("prévia não encontrada ou expirada") from None
                if preview.get("examId") != exam_id or int(preview.get("number", -1)) != int(number):
                    raise ValueError("a prévia não corresponde a esta questão")
                shutil.rmtree(workdir, ignore_errors=True)
                return self._json(200, {"ok": True})
            if path == "/api/benchmark/run":
                body = self._read_json()
                provider_name = str(body.get("provider") or "ollama")
                model = str(body.get("model") or "qwen3.5:4b")
                base_url = str(body.get("baseUrl") or "http://localhost:11434")
                timeout = float(body.get("timeout") or 60)
                attempts = int(body.get("httpAttempts") or 1)
                image_mode = str(body.get("images") or "nunca")
                workers = int(body.get("workers") or 2)
                ollama_threads = int(body.get("ollamaThreads") or 0)
                if not 1 <= workers <= 64 or not 0 <= ollama_threads <= 64:
                    raise ValueError("workers deve estar entre 1 e 64 e threads entre 0 e 64")
                if timeout <= 0 or attempts < 1:
                    raise ValueError("timeout e tentativas HTTP devem ser positivos")
                if image_mode not in ("nunca", "figuras", "sempre"):
                    raise ValueError("modo de imagem inválido")
                if not BENCHMARK_DATASET.is_file():
                    raise FileNotFoundError("Conjunto revisado não encontrado em simulados/cache")

                def run_benchmark(cancelled, job_id):
                    load_dotenv(SERVICE_ROOT / ".env")
                    provider = make_provider(provider_name, model, base_url=base_url, key_env=None,
                                             temperature=0, max_tokens=1024, json_mode=True,
                                             timeout=timeout, request_attempts=attempts, num_threads=ollama_threads)
                    def progress(event):
                        if cancelled():
                            raise JobCancelled()
                        current, total = event["completed"], event["total"]
                        _set_job(job_id, stage="benchmark", stageLabel="Classificando conjunto revisado",
                                 message=f"Questão {current} de {total}", progress=10 + int(84 * current / max(total, 1)),
                                 completed=current, total=total, activeQuestions=[],
                                 lastQuestion={"question": event["question"], "seconds": round(event["seconds"], 1)})
                    try:
                        result = evaluate_classifier(
                            BENCHMARK_DATASET, provider, output=BENCHMARK_REPORT,
                            source="EduBench USP revisado e tópicos canônicos",
                            version="review-ui", image_mode=image_mode,
                            request_attempts=attempts, workers=workers,
                            progress=progress, cancelled=cancelled)
                    except InterruptedError:
                        raise JobCancelled() from None
                    return {"count": result["count"], "subject": result["subject"],
                            "topic": result["topic"], "invalid": result["invalid_json_or_classification"],
                            "output": str(BENCHMARK_REPORT.relative_to(SERVICE_ROOT))}

                job_id = _submit_job("Executar benchmark", run_benchmark)
                return self._json(202, {"jobId": job_id})
            if path == "/api/benchmark/review":
                return self._json(200, _save_benchmark_label(self._read_json()))
            if path == "/api/rag/reindex":
                body = self._read_json()
                exam_id = str(body.get("examId") or "")
                if not exam_id:
                    raise ValueError("informe examId")
                _, exam_json = _find_exam(exam_id)
                job_id = _queue_review_index(exam_json)
                return self._json(202, {"jobId": job_id})
            cancel_match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/cancel", path)
            if cancel_match:
                with JOBS_LOCK:
                    job = JOBS.get(cancel_match.group(1))
                    if not job:
                        return self._json(404, {"error": "tarefa não encontrada"})
                    if job["status"] in ("completed", "failed", "cancelled"):
                        return self._json(409, {"error": "a tarefa já terminou"})
                    job["cancelRequested"] = True
                    job["status"] = "cancelling"
                    job["message"] = "Cancelamento solicitado · aguardando a etapa atual parar"
                return self._json(202, {"status": "cancelling"})
            if path == "/api/exams/process":
                query = parse_qs(parsed.query)
                client_job_id = (query.get("clientJobId") or [None])[0]
                if client_job_id and not re.fullmatch(r"[a-f0-9]{32}", client_job_id):
                    return self._json(400, {"error": "clientJobId inválido"})
                filename = (query.get("filename") or [self.headers.get("X-Filename", "prova.pdf")])[0]
                payload = {key: values[0] for key, values in query.items()}
                length = int(self.headers.get("Content-Length", "0"))
                content = self.rfile.read(length)
                job_id = _submit_job("Processar prova", lambda cancelled, current_job: _process_exam(filename, content, payload, current_job, cancelled), client_job_id)
                return self._json(202, {"jobId": job_id})
            key_match = re.fullmatch(r"/api/exams/([^/]+)/answer-key", path)
            if key_match:
                exam_id = key_match.group(1)
                filename = (parse_qs(parsed.query).get("filename") or ["gabarito.pdf"])[0]
                length = int(self.headers.get("Content-Length", "0"))
                content = self.rfile.read(length)
                if not Path(filename).name.lower().endswith(".pdf") or not content.startswith(b"%PDF"):
                    raise ValueError("envie um PDF de gabarito válido")
                summary, questions_path = _find_exam(exam_id)
                variant = exam_variant(Path(summary["name"]))
                ANSWER_IMPORTS_ROOT.mkdir(parents=True, exist_ok=True)
                answer_file = ANSWER_IMPORTS_ROOT / f"{uuid.uuid4().hex}.pdf"
                answer_file.write_bytes(content)
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    result = import_key_file(db, answer_file, questions_path, replace=True, variant=variant)
                finally:
                    db.close()
                return self._json(201, result)
            if path == "/api/answer-keys/import":
                body = self._read_json()
                exam_id = str(body.get("examId") or "")
                answers = body.get("answers")
                if not exam_id or not isinstance(answers, dict):
                    raise ValueError("informe examId e answers como objeto JSON")
                _, questions_path = _find_exam(exam_id)
                ANSWER_IMPORTS_ROOT.mkdir(parents=True, exist_ok=True)
                answer_file = ANSWER_IMPORTS_ROOT / f"{uuid.uuid4().hex}.json"
                answer_file.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    result = import_key_file(db, answer_file, questions_path,
                                             replace=bool(body.get("replace", False)))
                finally:
                    db.close()
                return self._json(201, result)
            if path == "/api/diagnostics":
                body = self._read_json()
                course_id = str(body.get("courseId") or "eng-comp-sao-carlos")
                total = int(body.get("total") or 51)
                if not 1 <= total <= 100:
                    raise ValueError("total deve estar entre 1 e 100 questões")
                seed = int(body["seed"]) if body.get("seed") is not None else secrets.randbits(63)

                def create(cancelled, current_job):
                    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                    profile = diagnostics.course_profile(config, course_id)
                    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                    try:
                        pool, excluded = diagnostics.load_question_pool(DEFAULT_QUESTIONS_DIR, db,
                                                                         list(profile["pesos"]))
                        if not pool:
                            raise ValueError("não há questões classificadas com gabarito para este curso")
                        result = diagnostics.create_attempt(
                            db, config, course_id, pool, total=total, seed=seed,
                            provider=_provider(body), excluded=excluded)
                        result["seed"] = seed
                        return result
                    finally:
                        db.close()

                job_id = _submit_job("Gerar diagnóstico", create)
                return self._json(202, {"jobId": job_id})
            match = re.fullmatch(r"/api/diagnostics/([^/]+)/answers", path)
            if match:
                body = self._read_json()
                attempt_id = match.group(1)
                ordinal = int(body.get("ordinal", 0))
                answer = body.get("answer")
                reason = body.get("reason")
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    row = db.execute("SELECT correct_answer FROM attempt_questions "
                                     "WHERE attempt_id=? AND ordinal=?", (attempt_id, ordinal)).fetchone()
                    if row is None:
                        raise ValueError(f"questão {ordinal} não pertence ao diagnóstico")
                    if answer and str(answer).upper() != row["correct_answer"] and not reason:
                        return self._json(200, {"requiresReason": True})
                    return self._json(200, diagnostics.record_response(db, attempt_id, ordinal, answer, reason))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/diagnostics/([^/]+)/finalize", path)
            if match:
                body = self._read_json()
                attempt_id = match.group(1)

                def finalize(cancelled, current_job):
                    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                    try:
                        return diagnostics.finalize_attempt(db, attempt_id, _provider(body),
                                                            include_analysis=bool(body.get("includeAnalysis", True)))
                    finally:
                        db.close()

                job_id = _submit_job("Analisar diagnóstico", finalize)
                return self._json(202, {"jobId": job_id})
            self._json(404, {"error": "rota não encontrada"})
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="endereço local de desenvolvimento")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    load_dotenv(SERVICE_ROOT / ".env")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"StudAI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        server.server_close()
        JOB_POOL.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
