"""API HTTP local e servidor de arquivos estáticos para a interface do StudAI."""
from __future__ import annotations

import json
import hashlib
import mimetypes
import re
import secrets
import threading
import uuid
import os
import warnings
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from studai.features.answer_keys.service import import_key_file
from studai.features.classification import service as classification
from studai.features.diagnostics import service as diagnostics
from studai.features.questions.extractor import extract_pdf
from studai.integrations.llm import load_dotenv, make_provider
from studai.settings import (
    DEFAULT_COURSE_CONFIG,
    DEFAULT_DIAGNOSTICS_DB,
    DEFAULT_QUESTIONS_DIR,
    SERVICE_ROOT,
)

WEB_ROOT = SERVICE_ROOT / "frontend"
UPLOADS_ROOT = SERVICE_ROOT / "simulados" / "uploads"
PROCESSED_ROOT = DEFAULT_QUESTIONS_DIR
ANSWER_IMPORTS_ROOT = SERVICE_ROOT / "diagnostico" / "imports-web"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="studai-job")


class JobCancelled(Exception):
    pass


def _set_job(job_id: str, **values) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(values)


def _submit_job(label: str, task) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
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


def _list_exams() -> list[dict]:
    db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
    try:
        keys = {row[0]: row[1] for row in db.execute(
            "SELECT source_id,COUNT(*) FROM answer_keys GROUP BY source_id")}
    finally:
        db.close()
    result = []
    for summary, _ in _exam_files():
        result.append({**summary, "importedAnswers": keys.get(summary["id"], 0)})
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
    workers = int(payload.get("workers") or 2)
    if not 1 <= workers <= 64:
        raise ValueError("workers deve estar entre 1 e 64")
    provider = _provider(payload)
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
            if path == "/api/providers":
                return self._json(200, [{"id": "demo", "name": "Demonstração offline"},
                                        {"id": "ollama", "name": "Ollama local"},
                                        {"id": "mock", "name": "Classificação simulada"}])
            if path == "/api/exams":
                return self._json(200, _list_exams())
            if path == "/api/courses":
                return self._json(200, _list_courses())
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
                return self._json(200, {"exam": summary, "questions": data["questoes"]})
            self._json(404, {"error": "rota não encontrada"})
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        try:
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
                filename = (query.get("filename") or [self.headers.get("X-Filename", "prova.pdf")])[0]
                payload = {key: values[0] for key, values in query.items()}
                length = int(self.headers.get("Content-Length", "0"))
                content = self.rfile.read(length)
                job_id = _submit_job("Processar prova", lambda cancelled, current_job: _process_exam(filename, content, payload, current_job, cancelled))
                return self._json(202, {"jobId": job_id})
            key_match = re.fullmatch(r"/api/exams/([^/]+)/answer-key", path)
            if key_match:
                exam_id = key_match.group(1)
                filename = (parse_qs(parsed.query).get("filename") or ["gabarito.pdf"])[0]
                length = int(self.headers.get("Content-Length", "0"))
                content = self.rfile.read(length)
                if not Path(filename).name.lower().endswith(".pdf") or not content.startswith(b"%PDF"):
                    raise ValueError("envie um PDF de gabarito válido")
                _, questions_path = _find_exam(exam_id)
                ANSWER_IMPORTS_ROOT.mkdir(parents=True, exist_ok=True)
                answer_file = ANSWER_IMPORTS_ROOT / f"{uuid.uuid4().hex}.pdf"
                answer_file.write_bytes(content)
                db = diagnostics.connect_db(DEFAULT_DIAGNOSTICS_DB)
                try:
                    result = import_key_file(db, answer_file, questions_path, replace=True)
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
