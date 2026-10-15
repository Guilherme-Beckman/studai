"""Provedores e transporte para serviços de modelos de linguagem."""
from __future__ import annotations

import base64
import contextlib
import contextvars
import importlib
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

OPENAI_COMPAT_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    # Roteador do Hugging Face (Inference Providers); o modelo aceita sufixos como ":fastest".
    "huggingface": ("https://router.huggingface.co/v1", "HF_TOKEN"),
}

_DAILY_QUOTA_MARKERS = ("per day", "perday", "per_day", "daily", "rpd", "requests per day", "tokens per day")


def is_daily_quota_error(error: Exception) -> bool:
    """429 whose message points to a daily cap (retrying on the same day would only fail again)."""
    if getattr(error, "status_code", None) != 429:
        return False
    text = str(error).casefold()
    return any(marker in text for marker in _DAILY_QUOTA_MARKERS)


def _next_local_midnight(now: float | None = None) -> float:
    current = time.localtime(now if now is not None else time.time())
    tomorrow = time.mktime((current.tm_year, current.tm_mon, current.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    return tomorrow

def load_dotenv(path: Path) -> None:
    """Carrega pares simples KEY=VALUE sem sobrescrever variáveis já exportadas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in ("\"", "'"):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)

# Etapa da classificação em andamento nesta thread (ex.: "materia", "conteudo"),
# registrada no uso de tokens para medir o custo de cada etapa.
REQUEST_STAGE: contextvars.ContextVar[str] = contextvars.ContextVar("studai_request_stage", default="")


class LLMError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

def http_post(url: str, headers: dict, body: dict, timeout: float, attempts: int = 4) -> dict:
    data = json.dumps(body).encode("utf-8")
    for i in range(attempts):
        # User-Agent próprio: o Cloudflare de alguns provedores (ex.: Groq) bloqueia o do urllib.
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "studai/1.0", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            payload = e.read().decode("utf-8", "replace")[:500]
            if e.code in (408, 409, 429, 500, 502, 503, 504, 529) and i < attempts - 1:
                ra = e.headers.get("Retry-After") if e.headers else None
                delay = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2 ** i + random.random()
                time.sleep(min(delay, 60))
                continue
            raise LLMError(f"HTTP {e.code}: {payload}", status_code=e.code) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i < attempts - 1:
                time.sleep(2 ** i + random.random())
                continue
            raise LLMError(f"falha de rede: {e}") from None
    raise LLMError("tentativas esgotadas")

class Provider(ABC):
    """Interface mínima. Para usar outro modelo/empresa, herde e implemente complete()."""

    default_key_env: str | None = None
    default_base_url: str | None = None

    def __init__(self, model: str = "", api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 120, temperature: float | None = None, max_tokens: int = 1024,
                 json_mode: bool = True, num_threads: int = 0, request_attempts: int = 4):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url or "").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self.num_threads = max(0, num_threads)
        self.request_attempts = max(1, int(request_attempts))

    @property
    def label(self) -> str:
        return f"{type(self).__name__}:{self.model}"

    @abstractmethod
    def complete(self, system: str, user: str, images: list[bytes] | None = None) -> str:
        """Devolve o texto da resposta do modelo. images = PNGs (bytes) opcionais."""

    def complete_json(self, system: str, user: str, schema: dict,
                      images: list[bytes] | None = None) -> str:
        """Saída JSON estruturada quando disponível; provedores legados usam JSON mode."""
        return self.complete(system, user, images)

class AnthropicProvider(Provider):
    default_key_env = "ANTHROPIC_API_KEY"
    default_base_url = "https://api.anthropic.com/v1"

    def complete(self, system, user, images=None):
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(im).decode()}}
            for im in images or []
        ]
        content.append({"type": "text", "text": user})
        body = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                "messages": [{"role": "user", "content": content}]}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        out = http_post(f"{self.base_url}/messages",
                        {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"},
                        body, self.timeout)
        return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")

_ROTATION_RETRYABLE = (None, 404, 408, 409, 429, 500, 502, 503, 504, 529)


class ModelRotation:
    """Rodízio entre modelos separados por vírgula: cada pedido começa no próximo
    modelo e, em erro de cota, indisponibilidade ou modelo inexistente, tenta o seguinte."""

    rotation_family = "modelos"

    def _init_rotation(self) -> None:
        self.models = list(dict.fromkeys(m.strip() for m in self.model.split(",") if m.strip()))
        if not self.models:
            raise ValueError(f"informe pelo menos um modelo ({self.rotation_family})")
        self._rotation_lock = threading.Lock()
        self._rotation_index = 0
        self._thread_state = threading.local()

    @property
    def label(self) -> str:
        return f"{type(self).__name__}:{','.join(self.models)}"

    @property
    def last_used_model(self) -> str:
        return getattr(self._thread_state, "model", self.models[0])

    def _next_model_index(self) -> int:
        with self._rotation_lock:
            index = self._rotation_index
            self._rotation_index = (self._rotation_index + 1) % len(self.models)
            return index

    def _rotation_attempts(self, single: int) -> int:
        # Com vários modelos, trocar de modelo substitui as novas tentativas no mesmo.
        return 1 if len(self.models) > 1 else single

    def _rotate(self, call) -> str:
        first = self._next_model_index()
        failures: list[tuple[str, LLMError]] = []
        for offset in range(len(self.models)):
            model = self.models[(first + offset) % len(self.models)]
            try:
                answer = call(model)
                self._thread_state.model = model
                return answer
            except LLMError as e:
                if e.status_code not in _ROTATION_RETRYABLE:
                    raise
                failures.append((model, e))
        codes = {error.status_code for _, error in failures}
        status_code = next(iter(codes)) if len(codes) == 1 else None
        detail = " | ".join(f"{model}: {error}" for model, error in failures)
        raise LLMError(f"todos os {self.rotation_family} da rotação falharam: " + detail,
                       status_code=status_code)


class OpenAICompatProvider(ModelRotation, Provider):
    """OpenAI e qualquer servidor que fale /chat/completions; aceita vários modelos
    separados por vírgula, em rodízio como no Gemini."""
    default_key_env = "OPENAI_API_KEY"
    default_base_url = "https://api.openai.com/v1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_rotation()

    def complete(self, system, user, images=None):
        if images:
            content: str | list = [{"type": "text", "text": user}] + [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + base64.b64encode(im).decode()}}
                for im in images
            ]
        else:
            content = user
        body: dict = {"messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": content}]}
        # a API oficial usa max_completion_tokens; servidores compatíveis costumam usar max_tokens
        tok = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
        body[tok] = self.max_tokens
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        def call(model: str) -> str:
            out = http_post(f"{self.base_url}/chat/completions", headers, {"model": model, **body},
                            self.timeout, attempts=self._rotation_attempts(self.request_attempts))
            try:
                return out["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"resposta inesperada: {str(out)[:300]}") from None

        return self._rotate(call)

class GeminiProvider(ModelRotation, Provider):
    default_key_env = "GEMINI_API_KEY"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    rotation_family = "modelos Gemini"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_rotation()

    def complete(self, system, user, images=None):
        parts: list[dict] = [{"text": user}] + [
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(im).decode()}}
            for im in images or []
        ]
        gen: dict = {"maxOutputTokens": self.max_tokens}
        if self.temperature is not None:
            gen["temperature"] = self.temperature
        if self.json_mode:
            gen["responseMimeType"] = "application/json"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": gen}

        def call(model: str) -> str:
            out = http_post(f"{self.base_url}/models/{model}:generateContent",
                            {"x-goog-api-key": self.api_key or ""}, body, self.timeout,
                            attempts=self._rotation_attempts(4))
            try:
                return "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError, TypeError):
                raise LLMError(f"resposta inesperada: {str(out)[:300]}") from None

        return self._rotate(call)


class RotatingPoolProvider(Provider):
    """Distributes each request to one free provider lane and retries on another lane."""

    def __init__(self, lanes: list[tuple], *, clock=time.monotonic, wall_clock=time.time, **kwargs):
        """``lanes`` items are (name, provider[, capacity[, rpm]]); rpm=0 means unlimited."""
        if not lanes:
            raise ValueError("selecione ao menos um modelo local ou Gemini configurado")
        super().__init__(model="pool", **kwargs)
        self.lanes = [(str(lane[0]), lane[1]) for lane in lanes]
        self._lane_capacities = [max(1, int(lane[2])) if len(lane) > 2 else 1 for lane in lanes]
        self._lane_rpm = [max(0, int(lane[3])) if len(lane) > 3 and lane[3] else 0 for lane in lanes]
        self._condition = threading.Condition()
        self._busy: dict[int, int] = {}
        self._disabled: set[int] = set()
        # Faixas sem cota diária ficam fora até a meia-noite local (horário de parede).
        self._quota_until: dict[int, float] = {}
        self._recent_starts: dict[int, list[float]] = {}
        self._clock = clock
        self._wall_clock = wall_clock
        self._next_lane = 0
        self._thread_state = threading.local()

    @property
    def lane_count(self) -> int:
        return len(self.lanes)

    @property
    def worker_count(self) -> int:
        return sum(self._lane_capacities)

    @property
    def label(self) -> str:
        return "RotatingPool:" + ",".join(name for name, _ in self.lanes)

    @property
    def last_used_model(self) -> str:
        return getattr(self._thread_state, "model", self.label)

    @contextlib.contextmanager
    def request_context(self, question: int, on_route=None):
        previous = getattr(self._thread_state, "context", None)
        self._thread_state.context = (question, on_route)
        try:
            yield
        finally:
            self._thread_state.context = previous

    def _out_of_quota(self, index: int) -> bool:
        until = self._quota_until.get(index)
        if until is None:
            return False
        if self._wall_clock() >= until:
            self._quota_until.pop(index, None)
            return False
        return True

    def _rate_wait(self, index: int, now: float) -> float:
        """Seconds until the lane may start another request under its RPM limit (0 = now)."""
        rpm = self._lane_rpm[index]
        if not rpm:
            return 0.0
        starts = [moment for moment in self._recent_starts.get(index, []) if now - moment < 60]
        self._recent_starts[index] = starts
        return 0.0 if len(starts) < rpm else 60 - (now - starts[0])

    def _acquire_lane(self, excluded: set[int]) -> int | None:
        with self._condition:
            while True:
                candidates = [i for i in range(len(self.lanes))
                              if i not in self._disabled and i not in excluded and not self._out_of_quota(i)]
                if not candidates:
                    return None
                now = self._clock()
                free = [i for i in candidates if self._busy.get(i, 0) < self._lane_capacities[i]]
                waits = {i: self._rate_wait(i, now) for i in free}
                available = [i for i in free if waits[i] <= 0]
                if available:
                    index = next((i for i in available if i >= self._next_lane), available[0])
                    self._next_lane = (index + 1) % len(self.lanes)
                    self._busy[index] = self._busy.get(index, 0) + 1
                    if self._lane_rpm[index]:
                        self._recent_starts.setdefault(index, []).append(now)
                    return index
                # Sem faixa livre: espera uma liberar ou o limite de RPM da mais próxima abrir.
                self._condition.wait(timeout=min(waits.values()) if waits else None)

    @property
    def quota_exhausted_lanes(self) -> list[str]:
        with self._condition:
            return [self.lanes[i][0] for i in range(len(self.lanes)) if self._out_of_quota(i)]

    def complete(self, system, user, images=None):
        excluded: set[int] = set()
        failures: list[str] = []
        while len(excluded | self._disabled) < len(self.lanes):
            index = self._acquire_lane(excluded)
            if index is None:
                break
            name, provider = self.lanes[index]
            context = getattr(self._thread_state, "context", None)
            try:
                if context and context[1]:
                    context[1](name)
                answer = provider.complete(system, user, images)
                model = getattr(provider, "last_used_model", provider.label)
                self._thread_state.model = f"{name} · {model}"
                return answer
            except LLMError as error:
                failures.append(f"{name}: {error}")
                excluded.add(index)
                if context and context[1]:
                    context[1](f"Falha em {name}; buscando faixa alternativa")
                if error.status_code in (401, 403, 404):
                    with self._condition:
                        self._disabled.add(index)
                elif is_daily_quota_error(error):
                    with self._condition:
                        self._quota_until[index] = _next_local_midnight(self._wall_clock())
                if (error.status_code is not None
                        and error.status_code not in (401, 403, 404, 408, 409, 429)
                        and not 500 <= error.status_code < 600):
                    raise
            except Exception as error:
                failures.append(f"{name}: {error}")
                excluded.add(index)
            finally:
                with self._condition:
                    remaining = self._busy.get(index, 1) - 1
                    if remaining:
                        self._busy[index] = remaining
                    else:
                        self._busy.pop(index, None)
                    self._condition.notify_all()
        raise LLMError("todas as faixas de IA falharam: " + " | ".join(failures))

    def complete_json(self, system, user, schema, images=None):
        return self.complete(system, user, images)

class OllamaProvider(Provider):
    """API do Ollama; aceita vários hosts e desativa pensamento para respostas mais rápidas."""
    default_base_url = "http://localhost:11434"

    @property
    def label(self) -> str:
        return f"{type(self).__name__}:{self.model}:nothink"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_urls = list(dict.fromkeys(url.strip().rstrip("/")
                                            for url in self.base_url.split(",") if url.strip()))
        if not self.base_urls:
            self.base_urls = [self.default_base_url]
        self._server_lock = threading.Lock()
        self._server_index = 0
        self._thread_state = threading.local()
        self.keep_alive = os.environ.get("STUDAI_OLLAMA_KEEP_ALIVE", "10m")
        try:
            # The default Ollama context (often 4096) is too small for the
            # full classification taxonomy and long exam questions.
            self.num_ctx = max(0, int(os.environ.get("STUDAI_OLLAMA_NUM_CTX", "16384")))
        except ValueError:
            self.num_ctx = 0

    @property
    def last_used_model(self) -> str:
        model = getattr(self._thread_state, "model", self.model)
        server = getattr(self._thread_state, "server", self.base_urls[0])
        return f"{model} @ {server}"

    def _assigned_server_index(self) -> int:
        index = getattr(self._thread_state, "server_index", None)
        if index is None:
            with self._server_lock:
                index = self._server_index % len(self.base_urls)
                self._server_index += 1
            self._thread_state.server_index = index
        return index

    def complete(self, system, user, images=None):
        return self._complete(system, user, images, schema=None)

    def complete_json(self, system, user, schema, images=None):
        return self._complete(system, user, images, schema=schema)

    def _complete(self, system, user, images=None, schema=None):
        user_message: dict = {"role": "user", "content": user}
        if images:
            user_message["images"] = [base64.b64encode(im).decode() for im in images]
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, user_message],
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"num_predict": self.max_tokens},
        }
        if self.num_ctx:
            body["options"]["num_ctx"] = self.num_ctx
        if self.num_threads:
            body["options"]["num_thread"] = self.num_threads
        if self.temperature is not None:
            body["options"]["temperature"] = self.temperature
        if self.json_mode:
            body["format"] = schema or "json"
        # Cada worker fica associado a um host para que o host mais rápido possa
        # continuar recebendo trabalho enquanto o outro ainda está inferindo.
        first = self._assigned_server_index()
        failures = []
        for offset in range(len(self.base_urls)):
            server = self.base_urls[(first + offset) % len(self.base_urls)]
            try:
                out = http_post(f"{server}/api/chat", {}, body, self.timeout,
                                attempts=1 if len(self.base_urls) > 1 else self.request_attempts)
            except LLMError as e:
                if e.status_code not in (None, 404, 429, 500, 502, 503, 504, 529):
                    raise
                failures.append(f"{server}: {e}")
                continue
            try:
                answer = out["message"]["content"] or ""
            except (KeyError, TypeError):
                raise LLMError(f"resposta inesperada do Ollama: {str(out)[:300]}") from None
            self._thread_state.model = self.model
            self._thread_state.server = server
            usage = {"prompt_tokens": int(out.get("prompt_eval_count", 0)),
                     "completion_tokens": int(out.get("eval_count", 0)),
                     "eval_duration_ns": int(out.get("eval_duration", 0)),
                     # Na CPU, ler o prompt (prefill) costuma dominar o tempo da chamada.
                     "prompt_eval_duration_ns": int(out.get("prompt_eval_duration", 0)),
                     "load_duration_ns": int(out.get("load_duration", 0)),
                     "stage": REQUEST_STAGE.get(),
                     "host": server}
            self._thread_state.usage = usage
            history = getattr(self._thread_state, "usage_history", None)
            if history is None:
                history = self._thread_state.usage_history = []
            history.append(dict(usage))
            return answer
        raise LLMError("todos os servidores Ollama falharam: " + " | ".join(failures))

class MockProvider(Provider):
    """Heurística por palavras-chave. Serve só para testar o pipeline sem API."""
    KW = {
        "Matemática": ["probabilidade", "função", "triângulo", "cosseno", "progressão", "pa de", "prisma"],
        "Física": ["velocidade", "energia cinética", "campo elétrico", "laser", "bateria", "força"],
        "Química": ["mol", "ph ", "reação", "ligação", "solubilidade", "polímero", "ácido"],
        "Biologia": ["mitocond", "hormônio", "espécie", "bactéria", "genét", "ecológic", "mutação"],
        "Inglês": [" the ", " and ", " of "],
        "Geografia": ["bioma", "domínio", "clima", "cartografia", "ferrovia", "território"],
        "História": ["regime", "revolução", "império", "nazis", "feudalismo", "século"],
        "Literatura": ["romance", "poema", "narrativa", "autora", "lispector", "balada"],
        "Educação Física": ["atividade física", "prática corporal", "esporte", "exercício físico"],
    }

    def complete(self, system, user, images=None):
        low = " " + user.lower() + " "
        scores = {m: sum(low.count(k) for k in ks) for m, ks in self.KW.items()}
        best = max(scores, key=scores.get)
        mat = best if scores[best] else "Língua Portuguesa"
        topic_map = {}
        for line in system.splitlines():
            if line.startswith("TAXONOMIA_TOPICOS_JSON:"):
                try:
                    topic_map = json.loads(line.split(":", 1)[1].strip())
                except json.JSONDecodeError:
                    pass
                break
        topic = (topic_map.get(mat) or ["REVISAR"])[0]
        return json.dumps({"materia": mat, "materias_secundarias": [], "assunto": topic,
                           "confianca": 0.5}, ensure_ascii=False)


class DemoProvider(MockProvider):
    """Provider offline para exercitar o fluxo completo pela interface local."""

    @property
    def label(self) -> str:
        return "DemoProvider:offline"

    def complete(self, system, user, images=None):
        if "planeja cobertura temática" in system:
            payload = json.loads(user)
            return json.dumps({"topicos": payload.get("topicos_disponiveis", {})}, ensure_ascii=False)
        if "interpreta resultados de um diagnóstico" in system:
            payload = json.loads(user)
            metrics = payload["metrics_calculated_by_code"]["subjects"]
            actions = {
                "unknown_content": "theory",
                "calc_error": "calculation_practice",
                "misread": "interpretation_practice",
                "blank": "review",
            }
            steps = []
            for subject, row in metrics.items():
                if row["status"] != "assessed":
                    continue
                reason = next((name for name in actions if row["error_reasons"].get(name, 0)), None)
                if reason:
                    steps.append({"subject": subject, "action": actions[reason], "reason_code": reason,
                                  "rationale": "Prioridade gerada pelo provedor de demonstração local.",
                                  "exit_criterion": "Acertar pelo menos 8 de 10 questões inéditas."})
            return json.dumps({"summary": "Análise de demonstração concluída com métricas calculadas pelo sistema.",
                               "steps": steps}, ensure_ascii=False)
        return super().complete(system, user, images)


def make_provider(name: str, model: str, *, base_url: str | None, key_env: str | None, **kw) -> Provider:
    key_name = key_env
    if name in OPENAI_COMPAT_PRESETS:
        preset_url, preset_key = OPENAI_COMPAT_PRESETS[name]
        cls: type[Provider] = OpenAICompatProvider
        base_url = base_url or preset_url
        key_name = key_name or preset_key
    elif name == "ollama":
        cls = OllamaProvider
        base_url = base_url or cls.default_base_url
        key_name = None
    elif name == "openai-compat":
        cls = OpenAICompatProvider
        if not base_url:
            sys.exit("--provider openai-compat exige --base-url")
        key_name = key_name or "OPENAI_COMPAT_API_KEY"
    elif name in ("anthropic", "openai", "gemini", "mock", "demo"):
        cls = {"anthropic": AnthropicProvider, "openai": OpenAICompatProvider,
               "gemini": GeminiProvider, "mock": MockProvider, "demo": DemoProvider}[name]
        key_name = key_name or cls.default_key_env
    elif ":" in name:                      # "pacote.modulo:Classe"
        mod, _, attr = name.partition(":")
        cls = getattr(importlib.import_module(mod), attr)
        if not (isinstance(cls, type) and issubclass(cls, Provider)):
            sys.exit(f"{name} precisa ser subclasse de Provider")
        key_name = key_name or cls.default_key_env
    else:
        sys.exit(f"provider desconhecido: {name}")

    api_key = os.environ.get(key_name) if key_name else None
    if key_name and not api_key and name != "mock":
        sys.exit(f"defina a variável de ambiente {key_name} com a chave de API")
    return cls(model=model, api_key=api_key, base_url=base_url, **kw)
