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
from datetime import datetime, timezone
import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
import urllib.error
import urllib.request

from studai.features.answer_keys.service import (
    detect_exam_variant, exam_variant, import_embedded_answer_key, import_key_file,
    seed_corpus_answer_keys, set_reviewed_answer,
)
from studai.features.classification.cache import ClassificationCache
from studai.features.classification import service as classification
from studai.features.classification.rag import DEFAULT_EMBEDDING_MODEL, DEFAULT_EXAMPLES, index_examples
from studai.features.diagnostics import service as diagnostics
from studai.features.explanations import service as explanations
from studai.features.explanations import summaries as content_summaries
from studai.features.reports import knowledge_map
from studai.features.reports import service as reports
from studai.features.study import calendar as study_calendar
from studai.features.study import error_notebook
from studai.features.study import proficiency as study_proficiency
from studai.features.study import service as study
from studai.features.questions.extractor import CORRUPT_GLYPH_RE, extract_pdf
from studai.features.questions import manual_crop, source_pdf
from studai.features.questions import ocr as question_ocr
from studai.features.questions import reprocess as question_reprocess
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
from studai.features.classification.taxonomy import (
    CONTENT_INDEX, CONTENT_TAXONOMY_VERSION, canonical_content_id,
    public_content_options, public_content_taxonomy,
)

WEB_ROOT = SERVICE_ROOT / "frontend"
UPLOADS_ROOT = SERVICE_ROOT / "simulados" / "uploads"
PROCESSED_ROOT = DEFAULT_QUESTIONS_DIR
ANSWER_IMPORTS_ROOT = SERVICE_ROOT / "diagnostico" / "imports-web"
QUESTION_REPROCESS_ROOT = SERVICE_ROOT / "simulados" / "cache" / "question-reprocess"
# Onde mais procurar o PDF original (só vale arquivo com o mesmo nome e sha256 da extração).
SOURCE_PDF_SEARCH_ROOTS: tuple[Path, ...] = (SERVICE_ROOT / "fuvest_acervo",)
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
POOL_CACHE: dict[tuple, tuple[tuple, list[dict]]] = {}
POOL_CACHE_LOCK = threading.Lock()


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
        gold_content = gold.get("content_id")
        predicted_content = result.get("pred_content_id")
        mismatch = (gold.get("subject") != result.get("pred_subject") or
                    (gold_content != predicted_content if gold_content else gold.get("topic") != result.get("pred_topic")))
        mismatches += int(mismatch)
        reference_changed += int(gold.get("subject") != result.get("gold_subject") or
                                 gold.get("topic") != result.get("gold_topic"))
        scored_rows.append({"gold_subject": gold.get("subject"), "pred_subject": result.get("pred_subject"),
                            "gold_topic": gold.get("topic"), "pred_topic": result.get("pred_topic"),
                            "gold_content_id": gold_content, "pred_content_id": predicted_content})
        questions.append({"index": index, "id": gold.get("id", str(index + 1)),
                          "year": gold.get("year"), "question": gold.get("question", ""),
                          "supportingTexts": gold.get("supporting_texts", ""),
                          "subject": gold.get("subject", ""), "topic": gold.get("topic", ""),
                          "contentId": gold_content, "predictedContentId": predicted_content,
                          "contentPath": gold.get("content_path") or gold.get("topic", ""),
                          "predictedContentPath": result.get("pred_content_path") or result.get("pred_topic", ""),
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
    canonical_rows = [row for row in scored_rows if row.get("gold_content_id")]
    report_view["content"] = score_classifier(canonical_rows, "gold_content_id", "pred_content_id")
    return {"report": report_view, "originalMetrics": original_metrics,
        "questions": questions, "mismatchCount": mismatches,
        "referenceChangedCount": reference_changed,
        "subjects": list(DEFAULT_TAXONOMY), "topics": DEFAULT_TOPIC_TAXONOMY,
        "contentTree": public_content_taxonomy(), "contents": public_content_options(),
        "taxonomyVersion": CONTENT_TAXONOMY_VERSION}


def _save_benchmark_label(body: dict) -> dict:
    index = int(body.get("index", -1))
    subject = str(body.get("subject") or "").strip()
    topic = str(body.get("topic") or "").strip()
    content_id = canonical_content_id(str(body.get("content_id") or ""), subject=subject) if body.get("content_id") else None
    if subject not in DEFAULT_TAXONOMY:
        raise ValueError("Selecione uma matéria válida da taxonomia StudAI")
    if content_id:
        topic = CONTENT_INDEX[content_id]["name"]
    elif topic not in DEFAULT_TOPIC_TAXONOMY.get(subject, []):
        raise ValueError("Selecione um tópico válido para a matéria escolhida")
    rows = [json.loads(line) for line in BENCHMARK_DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not 0 <= index < len(rows):
        raise ValueError("Questão de revisão não encontrada")
    row = rows[index]
    if body.get("id") and body["id"] != row.get("id"):
        raise ValueError("O conjunto foi alterado desde que a revisão foi aberta; atualize a tela")
    row.update(subject=subject, topic=topic, reviewed=True)
    if content_id:
        row.update(content_id=content_id, taxonomy_version=CONTENT_TAXONOMY_VERSION,
                   content_path=" → ".join(CONTENT_INDEX[content_id]["path"]))
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
            if "content_id" in fields and content_id:
                csv_rows[index]["content_id"] = content_id
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
    return {"ok": True, "subject": subject, "topic": topic, "content_id": content_id,
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
    if not model and name in CLOUD_LANE_PRESETS:
        model = CLOUD_LANE_PRESETS[name]["default_models"]
    try:
        threads = int(payload.get("ollamaThreads") or 0)
        if not 0 <= threads <= 64:
            raise ValueError("ollamaThreads deve estar entre 0 e 64")
        key_env = payload.get("apiKeyEnv") or payload.get("api_key_env")
        if name == "gemini" and not key_env:
            # Mesmas chaves do processamento (GEMINI_API_KEYS, GEMINI_API_KEY ou GOOGLE_API_KEY).
            keys = _gemini_api_keys()
            if not keys:
                raise ValueError("configure GEMINI_API_KEYS no arquivo .env para usar Gemini")
            return GeminiProvider(model=model, api_key=keys[0],
                                  base_url=payload.get("baseUrl") or payload.get("base_url"),
                                  temperature=0, max_tokens=max_tokens, json_mode=True)
        return make_provider(
            name,
            model,
            base_url=payload.get("baseUrl") or payload.get("base_url"),
            key_env=key_env,
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
            "freeCloudLanes": [name for name, *_ in _cloud_lanes(max_tokens=1)],
            "freeCloud": _cloud_lane_status(),
        }
        AI_OPTIONS_CACHE = (now, options)
        return dict(options)


# Faixas grátis de nuvem compatíveis com a API da OpenAI, ativadas quando a chave está
# no .env. Cada chave vira uma faixa que alterna entre os modelos, como no Gemini; sem
# modelos no .env valem os padrões abaixo (catálogos grátis de 2026-10, mudam com frequência).
CLOUD_LANE_PRESETS = {
    "groq": {"label": "Groq", "models_env": "STUDAI_GROQ_MODELS", "rpm_env": "STUDAI_GROQ_RPM",
             "rpm_default": 30, "parallel_env": "STUDAI_GROQ_PARALLEL",
             "default_models": "openai/gpt-oss-120b,qwen/qwen3.8-27b,openai/gpt-oss-20b"},
    "huggingface": {"label": "Hugging Face", "models_env": "STUDAI_HF_MODELS", "rpm_env": "STUDAI_HF_RPM",
                    "rpm_default": 0, "parallel_env": "STUDAI_HF_PARALLEL",
                    # A cota grátis do roteador é mínima: só entra com modelos explícitos.
                    "default_models": ""},
    "openrouter": {"label": "OpenRouter", "models_env": "STUDAI_OPENROUTER_MODELS",
                   "rpm_env": "STUDAI_OPENROUTER_RPM", "rpm_default": 20,
                   "parallel_env": "STUDAI_OPENROUTER_PARALLEL",
                   "default_models": ("nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free,"
                                      "google/gemma-4-26b-a4b-it:free")},
}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise ValueError(f"{name} deve ser um inteiro entre {low} e {high}") from None
    if not low <= value <= high:
        raise ValueError(f"{name} deve ser um inteiro entre {low} e {high}")
    return value


def _cloud_models(config: dict) -> tuple[list[str], bool]:
    """Models of a free cloud preset and whether they came from the defaults."""
    raw = os.environ.get(config["models_env"], "").strip()
    models = list(dict.fromkeys(m.strip() for m in (raw or config["default_models"]).split(",") if m.strip()))
    return models, not raw


def _cloud_lane_status() -> list[dict]:
    """What the interface may show about each free cloud tier: never the key itself."""
    status = []
    for preset, config in CLOUD_LANE_PRESETS.items():
        key_env = classification.OPENAI_COMPAT_PRESETS[preset][1]
        models, default = _cloud_models(config)
        status.append({"id": preset, "label": config["label"], "keyEnv": key_env,
                       "modelsEnv": config["models_env"], "models": models, "defaultModels": default,
                       "configured": bool(os.environ.get(key_env, "").strip() and models)})
    return status


def _cloud_lanes(*, max_tokens: int, providers: set[str] | None = None) -> list[tuple]:
    """Lanes for free OpenAI-compatible tiers configured in the environment.

    ``providers`` limits the tiers used (None means every configured tier)."""
    lanes: list[tuple] = []
    for preset, config in CLOUD_LANE_PRESETS.items():
        if providers is not None and preset not in providers:
            continue
        base_url, key_env = classification.OPENAI_COMPAT_PRESETS[preset]
        key = os.environ.get(key_env, "").strip()
        models, _ = _cloud_models(config)
        if not key or not models:
            continue
        rpm = _env_int(config["rpm_env"], config["rpm_default"], 0, 10_000)
        parallel = _env_int(config["parallel_env"], 2, 1, 16)
        provider = make_provider(preset, ",".join(models), base_url=base_url, key_env=key_env,
                                 temperature=0, max_tokens=max_tokens, json_mode=True,
                                 request_attempts=1)
        lanes.append((f"{config['label']} ({','.join(models)})", provider, parallel, rpm))
    return lanes


SHARED_POOLS: dict[str, tuple[float, RotatingPoolProvider]] = {}
SHARED_POOLS_LOCK = threading.Lock()
SHARED_POOL_TTL_SECONDS = 15 * 60
POOL_PAYLOAD_KEYS = ("useOllama", "ollamaModels", "geminiModels", "useFreeCloud", "cloudProviders",
                     "ollamaThreads", "baseUrl")


def _shared_pool_provider(payload: dict) -> RotatingPoolProvider:
    """Reuse one pool per configuration so simultaneous jobs share lane capacity and quota state."""
    signature = json.dumps({key: str(payload.get(key) or "") for key in POOL_PAYLOAD_KEYS}, sort_keys=True)
    with SHARED_POOLS_LOCK:
        now = time.monotonic()
        for key, (created, _) in list(SHARED_POOLS.items()):
            if now - created > SHARED_POOL_TTL_SECONDS:
                SHARED_POOLS.pop(key, None)
        cached = SHARED_POOLS.get(signature)
        if cached:
            return cached[1]
        provider = _pool_provider(payload)
        SHARED_POOLS[signature] = (now, provider)
        return provider


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
    use_cloud = str(payload.get("useFreeCloud", "true")).strip().casefold() not in {"false", "0", "no", "off"}
    # Sem cloudProviders no pedido valem todas as faixas configuradas (compatível com clientes antigos).
    cloud_providers = (set(split_models(payload.get("cloudProviders")))
                       if payload.get("cloudProviders") is not None else None)
    unknown = (cloud_providers or set()) - set(CLOUD_LANE_PRESETS)
    if unknown:
        raise ValueError(f"faixa de nuvem desconhecida: {', '.join(sorted(unknown))}")
    cloud_lanes = _cloud_lanes(max_tokens=max_tokens, providers=cloud_providers) if use_cloud else []
    if not selected_ollama and not selected_gemini and not cloud_lanes:
        raise ValueError("selecione ao menos um modelo Ollama, Gemini ou uma faixa grátis de nuvem")
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
        try:
            gemini_parallelism = int(os.environ.get("STUDAI_GEMINI_PARALLEL_REQUESTS", "4"))
        except ValueError:
            raise ValueError("STUDAI_GEMINI_PARALLEL_REQUESTS deve ser um inteiro entre 1 e 16") from None
        if not 1 <= gemini_parallelism <= 16:
            raise ValueError("STUDAI_GEMINI_PARALLEL_REQUESTS deve ser um inteiro entre 1 e 16")
        lanes.append((f"Gemini chave {index} ({','.join(selected_gemini)})",
                      gemini_provider, gemini_parallelism,
                      _env_int("STUDAI_GEMINI_RPM", 0, 0, 10_000)))
    lanes.extend(cloud_lanes)
    if len(lanes) > 64:
        raise ValueError("o pool pode ter no máximo 64 faixas; reduza as chaves ou os modelos locais")
    provider = RotatingPoolProvider(lanes, temperature=0, max_tokens=max_tokens, json_mode=True)
    if provider.worker_count > 64:
        raise ValueError("o pool pode ter no máximo 64 chamadas simultâneas; reduza as faixas ou a concorrência Gemini")
    return provider


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


def _source_pdf(exam_id: str, exam_json: Path) -> Path:
    """PDF original associado à prova (uploads ou acervo FUVEST com o mesmo sha256)."""
    return source_pdf.resolve_source_pdf(exam_json, exam_id, UPLOADS_ROOT, SOURCE_PDF_SEARCH_ROOTS)


def _exam_version(exam_id: str, exam_json: Path, name: str) -> str | None:
    """Versão (V, K, T, V1…) pelo nome e, se faltar, pelo texto do PDF original."""
    named = exam_variant(Path(name))
    if named:
        return named
    try:
        source = _source_pdf(exam_id, exam_json)
    except (OSError, ValueError):
        return None
    return detect_exam_variant(source, ocr=question_ocr.default_engine())


def _import_embedded_key(source: Path, questions_path: Path) -> dict | None:
    """Gabarito impresso na própria prova (1996–2004); falha aqui não impede o processamento."""
    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        return import_embedded_answer_key(db, source, questions_path, ocr=question_ocr.default_engine())
    except (OSError, ValueError) as exc:
        warnings.warn(f"gabarito impresso na prova não importado: {exc}", RuntimeWarning)
        return None
    finally:
        db.close()


def _classification_label(subject: str, content_id: str | None, topic: str = "") -> dict:
    """Matéria e conteúdo validados contra a taxonomia, no formato gravado na revisão."""
    if subject not in DEFAULT_TAXONOMY:
        raise ValueError("selecione uma matéria válida")
    # Conteúdo intermediário (com subconteúdos) também é rótulo final válido.
    content_id = (canonical_content_id(str(content_id), subject=subject, allow_refinable=True)
                  if content_id else None)
    if content_id:
        return {"subject": subject, "topic": CONTENT_INDEX[content_id]["name"], "content_id": content_id,
                "taxonomy_version": CONTENT_TAXONOMY_VERSION,
                "content_path": " → ".join(CONTENT_INDEX[content_id]["path"])}
    if topic not in DEFAULT_TOPIC_TAXONOMY.get(subject, []):
        raise ValueError("selecione um tópico válido para a matéria")
    return {"subject": subject, "topic": topic}


def _review_values(question: dict, body: dict, exam_dir: Path) -> dict:
    status = str(body.get("status") or "")
    if status not in (*question_review.STATUSES, "pending"):
        raise ValueError("status deve ser reviewed, unusable, bad_crop ou pending")
    values = {"status": status, "note": str(body.get("note") or "")}
    if len(values["note"]) > 2000:
        raise ValueError("a observação deve ter no máximo 2000 caracteres")
    if status == "reviewed":
        values.update(_classification_label(str(body.get("subject") or "").strip(), body.get("content_id"),
                                             str(body.get("topic") or "").strip()))
    elif status == "pending" and body.get("subject"):
        # Correção salva sem aprovar ainda guarda a matéria/conteúdo escolhidos.
        values.update(_classification_label(str(body["subject"]).strip(), body.get("content_id"),
                                            str(body.get("topic") or "").strip()))
    else:
        values.update(subject=None, topic=None)
    if "edits" in body:
        values["edits"] = question_review.validate_edits(question, body["edits"], exam_dir)
    return values


def _bulk_approve(exam_id: str, numbers: list) -> dict:
    """Aprova as questões escolhidas com a matéria e o conteúdo sugeridos pela IA."""
    _, exam_json = _find_exam(exam_id)
    data = json.loads(exam_json.read_text(encoding="utf-8"))
    questions = {int(q.get("numero", -1)): q for q in data.get("questoes", [])}
    reviews = question_review.read_reviews(exam_json).get("questions") or {}
    values, skipped = {}, []
    for raw in numbers:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"número de questão inválido: {raw!r}") from None
        question = questions.get(number)
        if question is None:
            skipped.append({"numero": number, "motivo": "questão não encontrada"})
            continue
        previous = reviews.get(str(number)) or {}
        classification_data = question.get("classificacao") if isinstance(question.get("classificacao"), dict) else {}
        subject = previous.get("subject") or question.get("materia") or classification_data.get("materia")
        content_id = previous.get("content_id") or classification_data.get("conteudo_principal_id")
        try:
            if not content_id:
                raise ValueError("sem conteúdo da taxonomia")
            label = _classification_label(str(subject or ""), content_id)
        except ValueError as exc:
            skipped.append({"numero": number, "motivo": str(exc)})
            continue
        values[number] = {**label, "status": "reviewed", "note": previous.get("note") or "",
                          "related_content_ids": classification_data.get("conteudos_relacionados_ids") or []}
    result = question_review.save_reviews_bulk(exam_json, values) if values else {
        **question_review.review_stats(data, {"questions": reviews})}
    result.pop("questions", None)
    job_id = _queue_review_index(exam_json) if values else None
    return {"approved": sorted(values), "skipped": skipped, "reviewProgress": result,
            "ragStatus": "queued" if job_id else None, "ragJobId": job_id}


def _reprocess_recommended(question: dict, review: dict | None) -> bool:
    return question_reprocess.reprocess_recommended(question, review, CORRUPT_GLYPH_RE)


def _reprocess_question(exam_id: str, number: int, uploaded: bytes | None,
                        filename: str | None, cancelled, job_id: str) -> dict:
    """Job HTTP: prévia da reextração de uma questão (regra em ``questions.reprocess``)."""
    _, exam_json = _find_exam(exam_id)

    def progress(event):
        _set_job(job_id, stage=event["stage"], message=event["message"],
                 progress=15 if event["stage"] == "extracting" else 65)

    def extract(source, outdir, progress=None):
        return extract_pdf(source, outdir, dpi=200, full_crops=True, detailed_crops=True, progress=progress)

    try:
        preview = question_reprocess.create_preview(
            exam_id=exam_id, exam_json=exam_json, number=number, workspace_root=QUESTION_REPROCESS_ROOT,
            extract=extract, source_pdf=lambda: _source_pdf(exam_id, exam_json),
            uploaded=uploaded, filename=filename, progress=progress, cancelled=cancelled,
            processed_root=PROCESSED_ROOT)
    except question_reprocess.ReprocessCancelled:
        raise JobCancelled() from None
    _set_job(job_id, stage="preview_ready", message=f"Prévia da questão {number} pronta", progress=100)
    return {**preview, "assetPrefix": f"/api/exams/{exam_id}/reprocess-previews/{preview['previewId']}/assets"}


def _confirm_question_reprocess(exam_id: str, number: int, preview_id: str) -> dict:
    _, exam_json = _find_exam(exam_id)
    return question_reprocess.confirm_preview(
        exam_id=exam_id, exam_json=exam_json, number=number, preview_id=preview_id,
        workspace_root=QUESTION_REPROCESS_ROOT, uploads_root=UPLOADS_ROOT, processed_root=PROCESSED_ROOT)


def _manual_crop(exam_id: str, number: int, body: dict) -> dict:
    """Recorte manual de uma região do PDF; com ``save`` grava direto na revisão."""
    _, exam_json = _find_exam(exam_id)
    data = json.loads(exam_json.read_text(encoding="utf-8"))
    if question_reprocess.find_question(data, number) is None:
        raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
    crop = manual_crop.crop_region(_source_pdf(exam_id, exam_json), body.get("page"), body.get("region"),
                                   exam_json.parent, dpi=body.get("dpi"),
                                   extract_text=bool(body.get("extractText", True)))
    if body.get("save"):
        crop["review"] = manual_crop.apply_crop_to_review(
            exam_json, number, crop["path"], target=str(body.get("target") or "stem"),
            replace=bool(body.get("replace")),
            suggestion=crop.get("suggestion") if body.get("useText") else None)
    return crop


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
    courses = []
    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        pools = {}
        for course_id in config["cursos"]:
            try:
                # Reaproveita o cache do pool dos relatórios (invalidado quando as fontes mudam).
                pools[course_id] = _reports_pool(db, config, course_id)
            except (OSError, ValueError):
                pools[course_id] = []
    finally:
        db.close()
    for course_id, profile in config["cursos"].items():
        summary = diagnostics.course_weight_summary(profile, pools[course_id])
        courses.append({"id": course_id, "name": profile.get("nome", course_id),
                        "subjects": list(profile.get("pesos", {})),
                        "weights": summary["weights"], "nonAssessable": summary["non_assessable"],
                        "formula": summary["formula"]})
    return courses


def _reject_exam_attempt(db, attempt_id: str, action: str) -> None:
    """Provas completas (mode='exam') são tentativas, mas têm rotas próprias."""
    row = db.execute("SELECT mode FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if row is not None and row["mode"] == "exam":
        raise ValueError(f"esta tentativa é uma prova completa; use /api/exam-sessions/{attempt_id}/{action}")


def _diagnostic_payload(db, attempt_id: str) -> dict:
    attempt = diagnostics.get_attempt(db, attempt_id)
    rows = diagnostics.list_questions_for_attempt(db, attempt_id)
    questions = []
    for row in rows:
        question = json.loads(row["question_json"])
        # Internal source paths are only needed by the CLI for local image display.
        question.pop("_source_dir", None)
        questions.append({"ordinal": row["ordinal"], "source_id": row["source_id"],
                          "subject": row["subject"], "topic": row["topic"],
                          "question": question, "selectedAnswer": row["selected_answer"],
                          "errorReason": row["error_reason"], "answered": row["answered_at"] is not None})
    return {
        "id": attempt["id"], "course": attempt["course_name"], "status": attempt["status"],
        "mode": attempt.get("mode") or "static",
        "done": not any(item["answered"] is False for item in questions),
        "requested": attempt["requested_count"], "total": attempt["actual_count"],
        "createdAt": attempt["created_at"], "metrics": attempt.get("metrics"),
        "analysis": attempt.get("analysis"), "analysisError": attempt.get("analysis_error"),
        "questions": questions,
    }


def _study_pool(db, config: dict, course_id: str) -> list[dict]:
    profile = diagnostics.course_profile(config, course_id)
    pool, _ = diagnostics.load_question_pool(DEFAULT_QUESTIONS_DIR, db, list(profile["pesos"]))
    return pool


def _study_today(course_id: str) -> dict:
    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        study.sync_diagnostic_answers(db)
        pool = _study_pool(db, config, course_id)
        return study.plan_today(db, pool, config, course_id, datetime.now(timezone.utc))
    finally:
        db.close()


def _answer_extras(body: dict) -> dict:
    """Campos extras da resposta no corpo HTTP (``diagnostics.answer_extras`` valida)."""
    return {"confidence": body.get("confidence"), "elapsed_ms": body.get("elapsedMs"),
            "hints_used": body.get("hintsUsed") or 0, "note": body.get("note")}


def _study_answer(body: dict) -> dict:
    session_id = str(body.get("sessionId") or "")
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        # Corrige com o gabarito atual, o mesmo que a tela "Hoje" mostrou.
        row = db.execute("SELECT course_id FROM study_sessions WHERE id=?", (session_id,)).fetchone()
        pool = None
        if row:
            config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
            pool = _study_pool(db, config, row["course_id"])
        return study.record_study_answer(db, session_id, str(body.get("itemId") or ""), body.get("answer"),
                                         body.get("reason"), datetime.now(timezone.utc), pool,
                                         **_answer_extras(body))
    finally:
        db.close()


def _study_training_next(body: dict) -> dict:
    session_id = str(body.get("sessionId") or "")
    if not session_id:
        raise ValueError("sessão de estudo inválida")
    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        row = db.execute("SELECT course_id FROM study_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise ValueError(f"sessão de estudo não encontrada: {session_id}")
        pool = _study_pool(db, config, row["course_id"])
        subjects = body.get("subjects")
        if subjects is not None and (not isinstance(subjects, list)
                                     or any(not isinstance(subject, str) for subject in subjects)):
            raise ValueError("matérias deve ser uma lista")
        try:
            count = int(body.get("count", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("a quantidade de questões precisa ser um número inteiro") from exc
        content_ids = body.get("contentIds")
        if content_ids is not None and (not isinstance(content_ids, list)
                                        or any(not isinstance(value, str) for value in content_ids)):
            raise ValueError("conteúdos deve ser uma lista")
        refs = body.get("questionRefs")
        if refs is not None:
            try:
                refs = [(str(ref["sourceId"]), int(ref["number"])) for ref in refs]
            except (KeyError, TypeError, ValueError):
                raise ValueError("questionRefs deve ser uma lista de {sourceId, number}") from None
        return study.append_training_item(db, session_id, pool, config, datetime.now(timezone.utc),
                                          subjects=subjects, count=count, content_ids=content_ids,
                                          question_refs=refs)
    finally:
        db.close()


def _calendar(course_id: str, *, today_only: bool = False) -> dict:
    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        study.sync_diagnostic_answers(db)
        pool = _reports_pool(db, config, course_id)
        use_case = study_calendar.today_plan if today_only else study_calendar.calendar_overview
        return use_case(db, pool, config, course_id, datetime.now(timezone.utc))
    finally:
        db.close()


def _calendar_add_exam(body: dict) -> dict:
    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
    course_id = _report_course_id({}, body)
    if course_id not in config["cursos"]:
        raise reports.NotFound(f"curso não configurado: {course_id}")
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        return study_calendar.add_exam(db, course_id, body.get("name"), body.get("date"),
                                       str(body.get("phase") or "fase1"), datetime.now(timezone.utc))
    finally:
        db.close()


def _process_exam(filename: str, content: bytes, payload: dict, job_id: str | None = None, cancelled=lambda: False) -> dict:
    clean_name = Path(filename).name
    if not clean_name.lower().endswith(".pdf") or not content.startswith(b"%PDF"):
        raise ValueError("envie um arquivo PDF válido")
    provider = (_shared_pool_provider(payload) if str(payload.get("provider") or "") == "pool"
                else _provider(payload))
    workers = provider.worker_count if isinstance(provider, RotatingPoolProvider) else int(payload.get("workers") or 2)
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
            stage = event["stage"]
            done, total = int(event.get("completed", 0)), max(1, int(event.get("total", 1)))
            if stage == "extracting":
                percent = 5 + int(42 * done / total)
            elif stage == "rendering":
                percent = 48 + int(17 * done / total)
            elif stage == "extracted":
                percent = 65
            else:
                percent = int(event.get("progress", 3))
            _set_job(job_id, status="running", stage=stage, message=event["message"], progress=percent,
                     completed=done, total=event.get("total"), unit=event.get("unit"))

    extraction_progress({"stage": "upload_saved", "message": "Arquivo recebido e salvo", "progress": 3})
    extracted_path = output_dir / "questoes.json"
    if extracted_path.exists():
        cached_questions = len(json.loads(extracted_path.read_text(encoding="utf-8")).get("questoes", []))
        extraction_progress({"stage": "extracted", "message": f"Extração reutilizada do cache · {cached_questions} questões", "questions": cached_questions})
    else:
        extracted_path = extract_pdf(source, output_dir, dpi=dpi, progress=extraction_progress)
        extracted_data = json.loads(extracted_path.read_text(encoding="utf-8"))
        extracted_data["prova"] = stable_exam_id
        extracted_data["arquivo_origem"] = clean_name
        extracted_data["arquivo_sha256"] = pdf_hash
        extracted_path.write_text(json.dumps(extracted_data, ensure_ascii=False, indent=2), encoding="utf-8")
    question_review.preserve_reviews(PROCESSED_ROOT, stable_exam_id, extracted_path)
    _import_embedded_key(source, extracted_path)
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
                _set_job(job_id, stage="classifying", message="Este simulado já foi classificado · reutilizando resultado", progress=99,
                         completed=question_total, total=question_total, cachedQuestions=question_total,
                         cacheMisses=0, estimatedSavedCalls=question_total, activeQuestions=[])
            return {"examId": str(cached_result.get("prova") or stable_exam_id), "name": clean_name,
                    "questions": question_total, "classified": True, "examCacheHit": True,
                    "cachedQuestions": question_total, "cacheMisses": 0,
                    "estimatedSavedCalls": question_total,
                    "output": str(classified_path.relative_to(SERVICE_ROOT))}
        if job_id:
            _set_job(job_id, stage="classifying", message=f"Classificando {question_total} questões", progress=65,
                     completed=0, total=question_total, activeQuestions=[])
        def classification_progress(event):
            if cancelled():
                raise JobCancelled()
            done, total = event["completed"], event["total"]
            percent = 65 + int(33 * done / max(total, 1))
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


def _pool_signature(db) -> tuple:
    """Assinatura barata das fontes do pool: JSONs, revisões e gabaritos importados."""
    files = sorted(list(DEFAULT_QUESTIONS_DIR.rglob("questoes.classificado.json"))
                   + list(DEFAULT_QUESTIONS_DIR.rglob("revisao.json")))
    stamps = []
    for path in files:
        try:
            stamps.append((str(path), path.stat().st_mtime_ns))
        except OSError:
            continue
    keys = tuple(db.execute("SELECT COUNT(*), COALESCE(MAX(imported_at), ''), COALESCE(SUM(is_annulled), 0), "
                            "COALESCE(SUM(LENGTH(answer)), 0) FROM answer_keys").fetchone())
    return tuple(stamps) + keys


def _reports_pool(db, config: dict, course_id: str) -> list[dict]:
    """Pool elegível do curso para relatórios/provas; cache por processo enquanto as fontes não mudam."""
    if course_id not in (config.get("cursos") or {}):
        raise reports.NotFound(f"curso não configurado: {course_id}")
    subjects = list(diagnostics.course_profile(config, course_id)["pesos"])
    key = (str(DEFAULT_QUESTIONS_DIR), str(DEFAULT_DIAGNOSTICS_DB), tuple(subjects))
    signature = _pool_signature(db)
    with POOL_CACHE_LOCK:
        cached = POOL_CACHE.get(key)
        if cached and cached[0] == signature:
            return cached[1]
    pool, _ = diagnostics.load_question_pool(DEFAULT_QUESTIONS_DIR, db, subjects)
    with POOL_CACHE_LOCK:
        POOL_CACHE[key] = (signature, pool)
    return pool


# ---- Resoluções comentadas: geradas em segundo plano, uma tarefa por questão.
EXPLANATION_JOBS: dict[tuple, str] = {}
EXPLANATION_PROVIDERS: dict[str, tuple[float, object]] = {}
EXPLANATION_LOCK = threading.Lock()
EXPLANATION_MAX_TOKENS = 8000  # o gpt-oss gasta parte disso raciocinando antes do JSON


def _explanations_provider():
    """Provedor das resoluções (``STUDAI_EXPLANATIONS_PROVIDER``; padrão: faixas grátis de nuvem)."""
    load_dotenv(SERVICE_ROOT / ".env")
    name = os.environ.get("STUDAI_EXPLANATIONS_PROVIDER", "pool").strip() or "pool"
    with EXPLANATION_LOCK:
        cached = EXPLANATION_PROVIDERS.get(name)
        if cached and time.monotonic() - cached[0] < SHARED_POOL_TTL_SECONDS:
            return cached[1]
    if name == "pool":
        provider = _pool_provider({"useOllama": "false", "useFreeCloud": "true"}, max_tokens=EXPLANATION_MAX_TOKENS)
    else:
        provider = _provider({"provider": name}, max_tokens=EXPLANATION_MAX_TOKENS)
    with EXPLANATION_LOCK:
        EXPLANATION_PROVIDERS[name] = (time.monotonic(), provider)
    return provider


def _question_entry(db, config: dict, course_id: str, source_id: str, number: int) -> dict:
    entry = next((item for item in _reports_pool(db, config, course_id)
                  if item["source_id"] == source_id and int(item["number"]) == int(number)), None)
    if entry is None:
        raise reports.NotFound("questão fora do acervo revisado deste curso")
    return entry


def _explanation_job_running(key: tuple) -> str | None:
    job_id = EXPLANATION_JOBS.get(key)
    with JOBS_LOCK:
        job = JOBS.get(job_id or "")
        return job_id if job and job["status"] in ("queued", "running") else None


def _start_explanation_job(course_id: str, source_id: str, number: int) -> str:
    key = (source_id, int(number))
    with EXPLANATION_LOCK:
        running = _explanation_job_running(key)
        if running:
            return running

        def task(cancelled, job_id):
            config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
            db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
            try:
                entry = _question_entry(db, config, course_id, source_id, number)
                record = explanations.get_explanation(db, source_id, number)
                feedback = record["feedback"] if record and record["status"] == "rejected" else None
                _set_job(job_id, stage="generating", message="Gerando a resolução comentada")
                explanations.generate_explanation(db, entry, _explanations_provider(), feedback=feedback)
                return {"sourceId": source_id, "number": int(number)}
            finally:
                db.close()

        job_id = _submit_job(f"Resolução {source_id} Q{number}", task)
        EXPLANATION_JOBS[key] = job_id
        return job_id


def _explanation_context(query: dict) -> dict:
    value = lambda name: (query.get(name) or [None])[0]
    ordinal = value("ordinal")
    return {"session_id": value("sessionId"), "item_id": value("itemId"), "attempt_id": value("attemptId"),
            "ordinal": int(ordinal) if ordinal not in (None, "") else None}


def _explanation_get(source_id: str, number: int, query: dict) -> tuple[int, dict]:
    course_id = _report_course_id(query)
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        record = explanations.get_explanation(db, source_id, number)
        if explanations.needs_generation(record):
            return 202, {"status": "generating", "jobId": _start_explanation_job(course_id, source_id, number)}
        answered = explanations.question_answered(db, source_id, number, **_explanation_context(query))
        try:
            hints = int((query.get("hints") or ["0"])[0])
        except ValueError:
            raise ValueError("hints deve ser um número de 0 a 3") from None
        return 200, explanations.public_payload(record, answered=answered, hints=hints)
    finally:
        db.close()


def _explanation_review(source_id: str, number: int, body: dict) -> dict:
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        if not explanations.question_answered(db, source_id, number):
            raise ValueError("responda à questão antes de revisar a resolução")
        record = explanations.review_explanation(db, source_id, number, bool(body.get("approved")),
                                                 body.get("feedback"))
    finally:
        db.close()
    if record["status"] == "rejected":
        return {"status": "generating", "jobId": _start_explanation_job(
            _report_course_id({}, body), source_id, number)}
    return {"status": record["status"]}


def _prefetch_explanations(body: dict) -> dict:
    """Adianta, em segundo plano, as resoluções das questões ainda sem resposta na sessão do dia."""
    session_id = str(body.get("sessionId") or "")
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        row = db.execute("SELECT course_id FROM study_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("sessão de estudo não encontrada")
        course_id = row["course_id"]
        answered = {r[0] for r in db.execute("SELECT item_id FROM study_answers WHERE session_id=?", (session_id,))}
        items = json.loads(db.execute("SELECT plan_json FROM study_sessions WHERE id=?",
                                      (session_id,)).fetchone()[0])["items"]
        missing = [(item["sourceId"], int(item["number"])) for item in items if item["id"] not in answered
                   and explanations.needs_generation(explanations.get_explanation(db, item["sourceId"], item["number"]))]
    finally:
        db.close()
    jobs = [_start_explanation_job(course_id, source_id, number) for source_id, number in missing]
    return {"queued": len(jobs), "jobIds": jobs}


def _start_summary_job(content_id: str) -> str:
    key = ("summary", content_id)
    with EXPLANATION_LOCK:
        running = _explanation_job_running(key)
        if running:
            return running

        def task(cancelled, job_id):
            db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
            try:
                _set_job(job_id, stage="generating", message="Gerando o mini-resumo")
                content_summaries.generate_summary(db, content_id, _explanations_provider())
                return {"contentId": content_id}
            finally:
                db.close()

        job_id = _submit_job(f"Mini-resumo {content_id}", task)
        EXPLANATION_JOBS[key] = job_id
        return job_id


def _summary_get(content_id: str) -> tuple[int, dict]:
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        record = content_summaries.get_summary(db, content_id)
        if content_summaries.needs_generation(record):
            return 202, {"status": "generating", "jobId": _start_summary_job(content_id)}
        return 200, content_summaries.public_payload(record)
    finally:
        db.close()


def _summary_review(content_id: str, body: dict) -> dict:
    db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        record = content_summaries.review_summary(db, content_id, bool(body.get("approved")), body.get("feedback"))
    finally:
        db.close()
    if record["status"] == "rejected":
        return {"status": "generating", "jobId": _start_summary_job(content_id)}
    return {"status": record["status"]}


def _report_course_id(query: dict, body: dict | None = None) -> str:
    value = (body or {}).get("courseId") or (query.get("courseId") or [""])[0]
    return str(value or reports.DEFAULT_COURSE_ID)


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

    def _binary(self, body: bytes, content_type: str, cache_control: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
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
        # Aplicação local: o navegador revalida a cada carga para não ficar com JS/CSS antigos.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/":
                return self._static("index.html")
            if path in ("/app.js", "/focus-mode.js", "/reports.js", "/calendar.js", "/knowledge-map.js", "/crop-editor.js",
                        "/explanations.js", "/styles.css"):
                return self._static(path.lstrip("/"))
            if path == "/api/health":
                return self._json(200, {"status": "ok", "mode": "local"})
            if path == "/api/ai/options":
                return self._json(200, _ai_options())
            if path == "/api/providers":
                return self._json(200, [{"id": "demo", "name": "Demonstração offline"},
                                        {"id": "ollama", "name": "Ollama local"},
                                        {"id": "gemini", "name": "Gemini"},
                                        {"id": "groq", "name": "Groq"},
                                        {"id": "openrouter", "name": "OpenRouter"},
                                        {"id": "huggingface", "name": "Hugging Face"},
                                        {"id": "mock", "name": "Classificação simulada"}])
            if path == "/api/review/bad-crops":
                return self._json(200, question_review.bad_crop_queue(PROCESSED_ROOT))
            if path == "/api/exams":
                return self._json(200, _list_exams())
            if path == "/api/courses":
                return self._json(200, _list_courses())
            if path == "/api/benchmark/review":
                return self._json(200, _read_benchmark_review())
            if path == "/api/diagnostics":
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    rows = db.execute("SELECT id,course_name,status,requested_count,actual_count,created_at,mode "
                                      "FROM attempts WHERE mode!='exam' ORDER BY created_at DESC").fetchall()
                    return self._json(200, [dict(row) for row in rows])
                finally:
                    db.close()
            if path == "/api/reports/overview":
                config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                course_id = _report_course_id(parse_qs(parsed.query))
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, reports.overview(db, config, course_id,
                                                            _reports_pool(db, config, course_id)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/reports/nodes/([^/]+)", path)
            if match:
                config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                course_id = _report_course_id(parse_qs(parsed.query))
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, reports.node_detail(db, config, course_id, match.group(1),
                                                               _reports_pool(db, config, course_id)))
                finally:
                    db.close()
            if path == "/api/knowledge-map":
                config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                course_id = _report_course_id(parse_qs(parsed.query))
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, knowledge_map.graph(db, config, course_id,
                                                               _reports_pool(db, config, course_id)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/knowledge-map/nodes/([^/]+)", path)
            if match:
                config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                course_id = _report_course_id(parse_qs(parsed.query))
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, knowledge_map.node_detail(db, config, course_id, match.group(1),
                                                                     _reports_pool(db, config, course_id)))
                finally:
                    db.close()
            if path == "/api/study/today":
                course_id = (parse_qs(parsed.query).get("courseId") or ["eng-comp-sao-carlos"])[0]
                return self._json(200, _study_today(course_id))
            if path in ("/api/calendar", "/api/calendar/today"):
                return self._json(200, _calendar(_report_course_id(parse_qs(parsed.query)),
                                                 today_only=path.endswith("/today")))
            if path == "/api/study/progress":
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    study.sync_diagnostic_answers(db)
                    return self._json(200, study.progress(db, datetime.now(timezone.utc)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/contents/([^/]+)/summary", path)
            if match:
                status, payload = _summary_get(unquote(match.group(1)))
                return self._json(status, payload)
            if path == "/api/study/errors":
                query = parse_qs(parsed.query)
                value = lambda name: (query.get(name) or [None])[0] or None
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, error_notebook.list_errors(
                        db, _report_course_id(query), subject=value("subject"), reason=value("reason"),
                        status=value("status") or "pending"))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/questions/([^/]+)/(\d+)/explanation", path)
            if match:
                status, payload = _explanation_get(unquote(match.group(1)), int(match.group(2)),
                                                   parse_qs(parsed.query))
                return self._json(status, payload)
            if path == "/api/study/proficiency":
                course_id = _report_course_id(parse_qs(parsed.query))
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, study_proficiency.subject_proficiency(
                        study.course_responses(db, course_id)))
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
                    questions.append({**question_review.apply_edits(question, human_reviews.get(number)),
                                      "review": human_reviews.get(number),
                                      "reviewReasons": question_review.review_reasons(question),
                                      "needsReview": bool(question_review.review_reasons(question)),
                                      "reprocessRecommended": _reprocess_recommended(question, human_reviews.get(number))})
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
                                        "contentTree": public_content_taxonomy(),
                                        "contents": public_content_options(),
                                        "taxonomyVersion": CONTENT_TAXONOMY_VERSION,
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
                asset_path = question_reprocess.preview_asset(QUESTION_REPROCESS_ROOT, *match.groups())
                if asset_path is None:
                    self.send_error(404)
                    return
                return self._binary(asset_path.read_bytes(), mimetypes.guess_type(asset_path.name)[0]
                                    or "application/octet-stream", "no-store")
            match = re.fullmatch(r"/api/exams/([^/]+)/source/pages", path)
            if match:
                _, exam_json = _find_exam(match.group(1))
                return self._json(200, {"pages": manual_crop.page_sizes(_source_pdf(match.group(1), exam_json))})
            match = re.fullmatch(r"/api/exams/([^/]+)/source/pages/(\d+)\.png", path)
            if match:
                _, exam_json = _find_exam(match.group(1))
                dpi = (parse_qs(urlsplit(self.path).query).get("dpi") or [None])[0]
                body = manual_crop.render_page(_source_pdf(match.group(1), exam_json), int(match.group(2)), dpi)
                return self._binary(body, "image/png", "private, max-age=600")
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
            if path == "/api/learning-context":
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, study.reset_learning_context(db))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/calendar/exams/([a-f0-9]+)", path)
            if match:
                db = study.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, study_calendar.delete_exam(db, match.group(1)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/diagnostics/([^/]+)", path)
            if match:
                attempt_id = match.group(1)
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    row = db.execute("SELECT mode FROM attempts WHERE id=?", (attempt_id,)).fetchone()
                    if row is None:
                        raise FileNotFoundError(f"diagnóstico não encontrado: {attempt_id}")
                    if row["mode"] == "exam":
                        raise ValueError("esta tentativa é uma prova completa; não pode ser removida por esta rota")
                    with db:
                        db.execute("DELETE FROM attempts WHERE id=?", (attempt_id,))
                    return self._json(200, {"ok": True, "id": attempt_id})
                finally:
                    db.close()
            match = re.fullmatch(r"/api/exams/([^/]+)", path)
            if not match:
                return self._json(404, {"error": "rota não encontrada"})
            return self._json(200, _delete_exam(match.group(1)))
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_PATCH(self):
        path = unquote(urlsplit(self.path).path)
        try:
            match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/review", path)
            if not match:
                return self._json(404, {"error": "rota não encontrada"})
            exam_id, number = match.group(1), int(match.group(2))
            summary, exam_json = _find_exam(exam_id)
            body = self._read_json()
            data = json.loads(exam_json.read_text(encoding="utf-8"))
            question = next((q for q in data.get("questoes", [])
                             if int(q.get("numero", -1)) == number), None)
            if question is None:
                raise FileNotFoundError(f"questão {number} não encontrada nesta prova")
            values = _review_values(question, body, exam_json.parent)
            if "answer" in body:
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    set_reviewed_answer(db, summary["id"], number, str(body.get("answer") or ""))
                finally:
                    db.close()
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
            match = re.fullmatch(r"/api/exams/([^/]+)/sessions", path)
            if match:
                body = self._read_json()
                summary, _ = _find_exam(match.group(1))
                config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                course_id = _report_course_id({}, body)
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(201, reports.start_exam_session(
                        db, config, course_id, _reports_pool(db, config, course_id), summary["id"],
                        exam_name=summary.get("name")))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/exam-sessions/([^/]+)/answers", path)
            if match:
                body = self._read_json()
                try:
                    ordinal = int(body.get("ordinal"))
                except (TypeError, ValueError):
                    raise ValueError("informe ordinal como número inteiro") from None
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, reports.answer_exam_question(
                        db, match.group(1), ordinal, body.get("answer"), body.get("reason"),
                        **_answer_extras(body)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/exam-sessions/([^/]+)/finish", path)
            if match:
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    return self._json(200, reports.finish_exam_session(db, match.group(1)))
                finally:
                    db.close()
            bulk_match = re.fullmatch(r"/api/exams/([^/]+)/reviews/bulk", path)
            if bulk_match:
                numbers = self._read_json().get("numbers")
                if not isinstance(numbers, list) or not numbers:
                    raise ValueError("informe as questões em numbers")
                return self._json(200, _bulk_approve(bulk_match.group(1), numbers))
            image_match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/images", path)
            if image_match:
                _, exam_json = _find_exam(image_match.group(1))
                length = int(self.headers.get("Content-Length", "0"))
                if length > question_review.MAX_IMAGE_BYTES:
                    raise ValueError("a imagem deve ter até 5 MB")
                return self._json(201, {"path": question_review.save_image(
                    exam_json.parent, self.rfile.read(length) if length else b"")})
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
                question_reprocess.discard_preview(QUESTION_REPROCESS_ROOT, exam_id, int(number), preview_id)
                return self._json(200, {"ok": True})
            crop_match = re.fullmatch(r"/api/exams/([^/]+)/questions/(\d+)/manual-crop", path)
            if crop_match:
                return self._json(201, _manual_crop(crop_match.group(1), int(crop_match.group(2)), self._read_json()))
            source_match = re.fullmatch(r"/api/exams/([^/]+)/source", path)
            if source_match:
                exam_id = source_match.group(1)
                _, exam_json = _find_exam(exam_id)
                filename = (parse_qs(parsed.query).get("filename") or [self.headers.get("X-Filename")])[0]
                length = int(self.headers.get("Content-Length", "0"))
                source_pdf.attach_source_pdf(exam_json, exam_id, self.rfile.read(length) if length else b"",
                                             filename, UPLOADS_ROOT)
                return self._json(201, {"ok": True})
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
                query = parse_qs(parsed.query)
                filename = (query.get("filename") or ["gabarito.pdf"])[0]
                length = int(self.headers.get("Content-Length", "0"))
                content = self.rfile.read(length)
                if not Path(filename).name.lower().endswith(".pdf") or not content.startswith(b"%PDF"):
                    raise ValueError("envie um PDF de gabarito válido")
                summary, questions_path = _find_exam(exam_id)
                # ?variant=K escolhe a coluna do gabarito quando a prova não diz a versão.
                variant = ((query.get("variant") or [""])[0].strip().upper()
                           or _exam_version(exam_id, questions_path, summary["name"]))
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
                mode = str(body.get("mode") or "adaptive")
                if mode not in ("adaptive", "static"):
                    raise ValueError("mode deve ser adaptive ou static")
                total = int(body.get("total") or (diagnostics.DEFAULT_ADAPTIVE_TOTAL if mode == "adaptive"
                                                  else diagnostics.DEFAULT_STATIC_TOTAL))
                limit = diagnostics.MAX_ADAPTIVE_TOTAL if mode == "adaptive" else 100
                if not 1 <= total <= limit:
                    raise ValueError(f"total deve estar entre 1 e {limit} questões")
                seed = int(body["seed"]) if body.get("seed") is not None else secrets.randbits(63)

                def create(cancelled, current_job):
                    config = diagnostics.load_course_config(DEFAULT_COURSE_CONFIG)
                    profile = diagnostics.course_profile(config, course_id)
                    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                    try:
                        pool, excluded = diagnostics.load_question_pool(DEFAULT_QUESTIONS_DIR, db,
                                                                         list(profile["pesos"]))
                        if not pool:
                            raise ValueError(diagnostics.empty_pool_message(excluded))
                        result = diagnostics.create_attempt(
                            db, config, course_id, pool, total=total, seed=seed,
                            provider=_provider(body), excluded=excluded, mode=mode)
                        result["seed"] = seed
                        return result
                    finally:
                        db.close()

                job_id = _submit_job("Gerar diagnóstico", create)
                return self._json(202, {"jobId": job_id})
            if path == "/api/study/today/answers":
                return self._json(200, _study_answer(self._read_json()))
            if path == "/api/calendar/exams":
                return self._json(201, _calendar_add_exam(self._read_json()))
            if path == "/api/study/training/next":
                return self._json(200, _study_training_next(self._read_json()))
            if path == "/api/study/explanations/prefetch":
                return self._json(202, _prefetch_explanations(self._read_json()))
            match = re.fullmatch(r"/api/contents/([^/]+)/summary/review", path)
            if match:
                return self._json(200, _summary_review(unquote(match.group(1)), self._read_json()))
            match = re.fullmatch(r"/api/questions/([^/]+)/(\d+)/explanation/review", path)
            if match:
                return self._json(200, _explanation_review(unquote(match.group(1)), int(match.group(2)),
                                                           self._read_json()))
            match = re.fullmatch(r"/api/diagnostics/([^/]+)/answers", path)
            if match:
                body = self._read_json()
                attempt_id = match.group(1)
                ordinal = int(body.get("ordinal", 0))
                answer = body.get("answer")
                reason = body.get("reason")
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    _reject_exam_attempt(db, attempt_id, "answers")
                    row = db.execute("SELECT correct_answer FROM attempt_questions "
                                     "WHERE attempt_id=? AND ordinal=?", (attempt_id, ordinal)).fetchone()
                    if row is None:
                        raise ValueError(f"questão {ordinal} não pertence ao diagnóstico")
                    if answer and str(answer).upper() != row["correct_answer"] and not reason:
                        return self._json(200, {"requiresReason": True})
                    return self._json(200, diagnostics.record_response(db, attempt_id, ordinal, answer, reason,
                                                                       **_answer_extras(body)))
                finally:
                    db.close()
            match = re.fullmatch(r"/api/diagnostics/([^/]+)/finalize", path)
            if match:
                body = self._read_json()
                attempt_id = match.group(1)
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    _reject_exam_attempt(db, attempt_id, "finish")
                finally:
                    db.close()

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


def configure_paths(*, diagnostics_db: Path | None = None, questions_dir: Path | None = None,
                    course_config: Path | None = None) -> None:
    """Sobrescreve os caminhos padrão do processo (testes locais com bancos descartáveis)."""
    global DEFAULT_DIAGNOSTICS_DB, DEFAULT_QUESTIONS_DIR, PROCESSED_ROOT, DEFAULT_COURSE_CONFIG
    if diagnostics_db is not None:
        DEFAULT_DIAGNOSTICS_DB = Path(diagnostics_db).expanduser().resolve()
    if questions_dir is not None:
        DEFAULT_QUESTIONS_DIR = PROCESSED_ROOT = Path(questions_dir).expanduser().resolve()
    if course_config is not None:
        DEFAULT_COURSE_CONFIG = Path(course_config).expanduser().resolve()


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="endereço local de desenvolvimento")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--diagnostics-db", type=Path, default=None,
                        help=f"banco SQLite do diagnóstico (padrão: {DEFAULT_DIAGNOSTICS_DB})")
    parser.add_argument("--questions-dir", type=Path, default=None,
                        help=f"provas processadas (padrão: {DEFAULT_QUESTIONS_DIR})")
    parser.add_argument("--course-config", type=Path, default=None,
                        help=f"configuração de cursos (padrão: {DEFAULT_COURSE_CONFIG})")
    args = parser.parse_args(argv)
    configure_paths(diagnostics_db=args.diagnostics_db, questions_dir=args.questions_dir,
                    course_config=args.course_config)
    load_dotenv(SERVICE_ROOT / ".env")
    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        seeded = seed_corpus_answer_keys(db, DEFAULT_QUESTIONS_DIR)
    finally:
        db.close()
    if seeded:
        print(f"Gabaritos do acervo importados: {sum(seeded.values())} respostas em {len(seeded)} provas")
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
