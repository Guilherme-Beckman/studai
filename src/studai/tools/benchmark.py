#!/usr/bin/env python3
"""Dashboard local para monitorar e comparar configurações do Ollama."""
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from studai.features.classification import service as clf
from studai.settings import SERVICE_ROOT

HERE = SERVICE_ROOT
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def endpoint(url: str, path: str, timeout: float = 4) -> dict:
    import urllib.request
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as response:
        return json.loads(response.read())


def host_status(url: str, model: str) -> dict:
    host = {"url": url, "online": False, "models": [], "loaded": [], "error": None,
            "active_requests": 0, "requests": 0, "tokens": 0, "tokens_per_second": 0}
    try:
        tags = endpoint(url, "/api/tags")
        ps = endpoint(url, "/api/ps")
        host["online"] = True
        host["models"] = [m.get("name") for m in tags.get("models", [])]
        host["loaded"] = [{"name": m.get("name"), "size_vram": m.get("size_vram", 0),
                            "size": m.get("size", 0)} for m in ps.get("models", [])]
        host["requested_model_available"] = any(n == model or n.startswith(model + ":") for n in host["models"])
    except Exception as exc:
        host["error"] = str(exc)
    with LOCK:
        active = [j for j in JOBS.values() if j.get("status") == "running"
                  for h in j.get("hosts", []) if h["url"] == url]
        host["active_requests"] = sum(h.get("active", 0) for h in active)
        host["requests"] = sum(h.get("requests", 0) for h in active)
        host["tokens"] = sum(h.get("tokens", 0) for h in active)
        started = [j.get("started_monotonic") for j in JOBS.values()
                   if j.get("status") == "running" and any(h["url"] == url for h in j.get("hosts", []))]
        elapsed = max(1, time.monotonic() - min(started)) if started else 0
        host["tokens_per_second"] = round(host["tokens"] / elapsed, 2) if elapsed else 0
    return host


def list_questions(path: Path, count: int) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    shared_all = data.get("textos_compartilhados", {})
    out = []
    for q in data.get("questoes", [])[:count]:
        shared = shared_all.get(q.get("texto_id") or "")
        out.append({"q": q, "shared": shared, "number": q.get("numero")})
    return out


def run_benchmark(job_id: str, payload: dict) -> None:
    with LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
    try:
        path = (HERE / payload["file"]).resolve()
        if HERE not in path.parents or not path.is_file() or path.suffix != ".json":
            raise ValueError("Selecione um JSON de questões dentro da pasta do serviço.")
        urls = list(dict.fromkeys(u.strip().rstrip("/") for u in payload["urls"] if u.strip()))
        if not urls:
            raise ValueError("Informe ao menos um servidor Ollama.")
        model = payload["model"].strip()
        questions = list_questions(path, max(1, min(20, int(payload.get("questions", 3)))))
        if not questions:
            raise ValueError("O JSON não contém questões.")
        counts = sorted(set(max(1, min(len(urls) * 4, int(v))) for v in payload.get("workers", [1])))
        thread_values = sorted(set(max(0, min(64, int(v))) for v in payload.get("threads", [0])))
        configurations = [{"workers": w, "threads": t} for w in counts for t in thread_values]
        job.update(total=len(configurations), current=0, question_count=len(questions), results=[],
                   started_monotonic=time.monotonic(),
                   hosts=[{"url": u, "active": 0, "requests": 0, "tokens": 0} for u in urls])

        for config in configurations:
            if job.get("cancel_requested"):
                break
            started = time.monotonic()
            cursor_lock = threading.Lock()
            cursor = 0
            def classify(item):
                nonlocal cursor
                with cursor_lock:
                    host_index = cursor % len(urls)
                    cursor += 1
                url = urls[host_index]
                host_data = job["hosts"][host_index]
                with LOCK:
                    host_data["active"] += 1
                    host_data["requests"] += 1
                    job["active_question"] = item["number"]
                system = clf.build_system(clf.DEFAULT_TAXONOMY)
                prompt = clf.render_questao(item["q"], item["shared"], False)
                body = {"model": model,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                        "stream": False, "think": False, "format": "json",
                        "options": {"num_predict": 512}}
                if config["threads"]:
                    body["options"]["num_thread"] = config["threads"]
                before = time.monotonic()
                try:
                    response = clf.http_post(url + "/api/chat", {}, body, 300, attempts=1)
                    elapsed = time.monotonic() - before
                    msg = response.get("message", {}).get("content", "")
                    valid = True
                    try:
                        clf.validate(clf.extract_json(msg), clf.DEFAULT_TAXONOMY,
                                     clf.DEFAULT_TOPIC_TAXONOMY)
                    except ValueError:
                        valid = False
                    tokens = int(response.get("eval_count", 0))
                    rate = tokens / (response.get("eval_duration", 0) / 1e9) if response.get("eval_duration") else 0
                    with LOCK:
                        host_data["tokens"] += tokens
                    return {"question": item["number"], "host": url, "seconds": round(elapsed, 2),
                            "tokens": tokens, "tokens_per_second": round(rate, 2), "valid": valid}
                finally:
                    with LOCK:
                        host_data["active"] -= 1

            rows = []
            try:
                with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
                    futures = [pool.submit(classify, item) for item in questions]
                    for future in as_completed(futures):
                        if job.get("cancel_requested"):
                            break
                        rows.append(future.result())
                        job["completed_questions"] = job.get("completed_questions", 0) + 1
            except Exception as exc:
                rows.append({"error": str(exc)})
            duration = time.monotonic() - started
            successful = [r for r in rows if "seconds" in r]
            result = {**config, "seconds": round(duration, 2), "questions": len(successful),
                      "tokens_per_second": round(sum(r["tokens_per_second"] for r in successful), 2),
                      "valid": sum(bool(r["valid"]) for r in successful), "details": rows}
            with LOCK:
                job["results"].append(result)
                job["current"] += 1
                job["active_question"] = None
        with LOCK:
            job["status"] = "cancelled" if job.get("cancel_requested") else "completed"
            good = [r for r in job["results"] if r["questions"]]
            job["recommended"] = min(good, key=lambda r: r["seconds"]) if good else None
    except Exception as exc:
        with LOCK:
            job["status"] = "error"
            job["error"] = str(exc)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, data: dict, code: int = 200):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            raw = (HERE / "benchmark.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif parsed.path == "/api/files":
            files = sorted(p.relative_to(HERE).as_posix() for p in HERE.rglob("*.json")
                           if ".venv" not in p.parts and "node_modules" not in p.parts)
            self.send_json({"files": files})
        elif parsed.path == "/api/status":
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            urls = [u.strip().rstrip("/") for u in qs.get("urls", ["http://localhost:11434"])[0].split(",") if u.strip()]
            model = qs.get("model", ["qwen3.5:4b"])[0]
            with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
                hosts = list(pool.map(lambda u: host_status(u, model), urls))
            self.send_json({"hosts": hosts, "jobs": list(JOBS.values())})
        elif parsed.path.startswith("/api/jobs/"):
            jid = parsed.path.rsplit("/", 1)[-1]
            self.send_json(JOBS.get(jid, {"error": "job não encontrado"}))
        else:
            self.send_json({"error": "não encontrado"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/benchmark":
            jid = uuid.uuid4().hex[:10]
            JOBS[jid] = {"id": jid, "status": "queued", "created": time.time()}
            threading.Thread(target=run_benchmark, args=(jid, body), daemon=True).start()
            self.send_json({"id": jid})
        elif self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
            jid = self.path.split("/")[-2]
            if jid in JOBS:
                JOBS[jid]["cancel_requested"] = True
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "não encontrado"}, 404)


def main(argv: list[str] | None = None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["ai-calls"]:
        # Contagem offline de chamadas de IA (sem Ollama): ver studai.tools.benchmark_ai_calls.
        from studai.tools.benchmark_ai_calls import main as ai_calls_main
        ai_calls_main(argv[1:])
        return
    if argv[:1] == ["legacy-exams"]:
        # Extração e gabarito das provas antigas do acervo: ver studai.tools.benchmark_legacy_exams.
        from studai.tools.benchmark_legacy_exams import main as legacy_main
        legacy_main(argv[1:])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="padrão seguro: apenas localhost")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    clf.load_dotenv(HERE / ".env")
    print(f"Benchmark Ollama em http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
