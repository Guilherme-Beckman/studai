"""Provedores e transporte para serviços de modelos de linguagem."""
from __future__ import annotations

import base64
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
}

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

class LLMError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

def http_post(url: str, headers: dict, body: dict, timeout: float, attempts: int = 4) -> dict:
    data = json.dumps(body).encode("utf-8")
    for i in range(attempts):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
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
                 json_mode: bool = True, num_threads: int = 0):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url or "").rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode
        self.num_threads = max(0, num_threads)

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

class OpenAICompatProvider(Provider):
    """OpenAI e qualquer servidor que fale /chat/completions."""
    default_key_env = "OPENAI_API_KEY"
    default_base_url = "https://api.openai.com/v1"

    def complete(self, system, user, images=None):
        if images:
            content: str | list = [{"type": "text", "text": user}] + [
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + base64.b64encode(im).decode()}}
                for im in images
            ]
        else:
            content = user
        body: dict = {"model": self.model,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": content}]}
        # a API oficial usa max_completion_tokens; servidores compatíveis costumam usar max_tokens
        tok = "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"
        body[tok] = self.max_tokens
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        out = http_post(f"{self.base_url}/chat/completions", headers, body, self.timeout)
        try:
            return out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"resposta inesperada: {str(out)[:300]}") from None

class GeminiProvider(Provider):
    default_key_env = "GEMINI_API_KEY"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.models = list(dict.fromkeys(m.strip() for m in self.model.split(",") if m.strip()))
        if not self.models:
            raise ValueError("informe pelo menos um modelo Gemini")
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
        first = self._next_model_index()
        failures = []
        for offset in range(len(self.models)):
            model = self.models[(first + offset) % len(self.models)]
            try:
                out = http_post(f"{self.base_url}/models/{model}:generateContent",
                                {"x-goog-api-key": self.api_key or ""}, body, self.timeout,
                                attempts=1 if len(self.models) > 1 else 4)
                try:
                    answer = "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"])
                except (KeyError, IndexError, TypeError):
                    raise LLMError(f"resposta inesperada: {str(out)[:300]}") from None
                self._thread_state.model = model
                return answer
            except LLMError as e:
                if e.status_code not in (404, 429, 500, 502, 503, 504, 529):
                    raise
                failures.append(f"{model}: {e}")
        raise LLMError("todos os modelos Gemini da rotação falharam: " + " | ".join(failures))

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
            self.num_ctx = max(0, int(os.environ.get("STUDAI_OLLAMA_NUM_CTX", "0")))
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
                                attempts=1 if len(self.base_urls) > 1 else 4)
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
            self._thread_state.usage = {"prompt_tokens": int(out.get("prompt_eval_count", 0)),
                                        "completion_tokens": int(out.get("eval_count", 0)),
                                        "eval_duration_ns": int(out.get("eval_duration", 0))}
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
        return json.dumps({"materia": mat, "materias_secundarias": [], "assunto": "(mock)",
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
