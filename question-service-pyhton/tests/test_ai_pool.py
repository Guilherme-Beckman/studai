import json
import io
import os
import sys
import tempfile
import threading
import time
import unittest
import concurrent.futures
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.api import server
from studai.integrations.llm import GeminiProvider, LLMError, Provider, RotatingPoolProvider


class StubProvider(Provider):
    def __init__(self, model, *, fail=None, pause=0):
        super().__init__(model=model)
        self.fail = fail
        self.pause = pause
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def complete(self, system, user, images=None):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.pause:
                time.sleep(self.pause)
            if self.fail:
                raise self.fail
            return self.model
        finally:
            with self.lock:
                self.active -= 1


class AIPoolTests(unittest.TestCase):
    def test_gemini_rotates_models_after_model_not_found(self):
        responses = [LLMError("not found", status_code=404),
                     {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}]
        with patch("studai.integrations.llm.http_post", side_effect=responses) as request:
            provider = GeminiProvider(model="gemini-a,gemini-b", api_key="test-key")
            self.assertEqual(provider.complete("system", "user"), "ok")
        self.assertEqual(request.call_count, 2)
        self.assertIn("gemini-b:generateContent", request.call_args.args[0])

    def test_pool_falls_back_after_quota_error(self):
        primary = StubProvider("primary", fail=LLMError("quota", status_code=429))
        backup = StubProvider("backup")
        pool = RotatingPoolProvider([("Gemini chave 1", primary), ("Ollama 1", backup)])
        self.assertEqual(pool.complete("system", "user"), "backup")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(backup.calls, 1)
        self.assertIn("Ollama 1", pool.last_used_model)

    def test_daily_quota_pauses_lane_until_next_local_midnight(self):
        now = [time.mktime((2026, 10, 15, 22, 0, 0, 0, 0, -1))]
        limited = StubProvider("limited", fail=LLMError("HTTP 429: Rate limit reached: requests per day (RPD)",
                                                         status_code=429))
        backup = StubProvider("backup")
        pool = RotatingPoolProvider([("Groq", limited), ("Ollama", backup)], wall_clock=lambda: now[0])
        self.assertEqual(pool.complete("system", "first"), "backup")
        self.assertEqual(pool.complete("system", "second"), "backup")
        self.assertEqual(limited.calls, 1)
        self.assertEqual(pool.quota_exhausted_lanes, ["Groq"])
        limited.fail = None
        now[0] += 3 * 3600  # passou da meia-noite local
        self.assertEqual(pool.complete("system", "third"), "limited")
        self.assertEqual(pool.quota_exhausted_lanes, [])

    def test_per_minute_429_does_not_pause_lane_for_the_day(self):
        limited = StubProvider("limited", fail=LLMError("HTTP 429: too many requests", status_code=429))
        pool = RotatingPoolProvider([("Gemini", limited), ("Ollama", StubProvider("backup"))])
        pool.complete("system", "first")
        self.assertEqual(pool.quota_exhausted_lanes, [])

    def test_rpm_limit_moves_extra_requests_to_other_lane(self):
        clock = [100.0]
        cloud, local = StubProvider("cloud"), StubProvider("local")
        pool = RotatingPoolProvider([("Groq", cloud, 1, 2), ("Ollama", local)], clock=lambda: clock[0])
        answers = [pool.complete("system", str(number)) for number in range(4)]
        self.assertEqual(cloud.calls, 2)
        self.assertEqual(local.calls, 2)
        self.assertEqual(answers.count("cloud"), 2)
        clock[0] += 61
        pool.complete("system", "after a minute")
        pool.complete("system", "after a minute 2")
        self.assertEqual(cloud.calls, 3)

    def test_rpm_limit_waits_when_it_is_the_only_lane(self):
        clock = [0.0]
        cloud = StubProvider("cloud")
        pool = RotatingPoolProvider([("Groq", cloud, 1, 1)], clock=lambda: clock[0])
        pool.complete("system", "first")
        waited = []
        original_wait = pool._condition.wait

        def fake_wait(timeout=None):
            waited.append(timeout)
            clock[0] += timeout or 0
            return original_wait(timeout=0)

        pool._condition.wait = fake_wait
        self.assertEqual(pool.complete("system", "second"), "cloud")
        self.assertEqual(len(waited), 1)
        self.assertAlmostEqual(waited[0], 60.0)

    def test_pool_disables_key_after_authentication_failure(self):
        invalid_key = StubProvider("invalid", fail=LLMError("unauthorized", status_code=401))
        backup = StubProvider("backup")
        pool = RotatingPoolProvider([("Gemini chave 1", invalid_key), ("Ollama", backup)])
        self.assertEqual(pool.complete("system", "first"), "backup")
        self.assertEqual(pool.complete("system", "second"), "backup")
        self.assertEqual(invalid_key.calls, 1)
        self.assertEqual(backup.calls, 2)

    def test_pool_does_not_retry_invalid_request(self):
        invalid = StubProvider("invalid", fail=LLMError("bad request", status_code=400))
        backup = StubProvider("backup")
        pool = RotatingPoolProvider([("Gemini", invalid), ("Ollama", backup)])
        with self.assertRaises(LLMError):
            pool.complete("system", "user")
        self.assertEqual(backup.calls, 0)

    def test_pool_never_runs_two_questions_on_same_lane_at_once(self):
        first, second = StubProvider("first", pause=0.03), StubProvider("second", pause=0.03)
        pool = RotatingPoolProvider([("Ollama 1", first), ("Ollama 2", second)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda number: pool.complete("system", str(number)), range(4)))
        self.assertEqual(len(results), 4)
        self.assertEqual(first.calls + second.calls, 4)
        self.assertEqual(first.max_active, 1)
        self.assertEqual(second.max_active, 1)

    def test_discovery_reports_counts_without_returning_keys(self):
        original_cache = server.AI_OPTIONS_CACHE
        server.AI_OPTIONS_CACHE = None
        try:
            with patch.object(server, "_gemini_api_keys", return_value=["secret-one", "secret-two"]), \
                 patch.object(server, "_ollama_models", return_value=(["qwen3.5:4b"], True)), \
                 patch.object(server, "_gemini_models_for_key", side_effect=[{"gemini-3.8-flash"}, set()]):
                options = server._ai_options()
            encoded = json.dumps(options)
            self.assertEqual(options["geminiKeyCount"], 2)
            self.assertEqual(options["geminiModels"], ["gemini-3.8-flash"])
            self.assertNotIn("secret-one", encoded)
            self.assertNotIn("secret-two", encoded)
        finally:
            server.AI_OPTIONS_CACHE = original_cache

    def test_ollama_discovery_keeps_generation_models_and_excludes_embedding_only(self):
        payload = {"models": [
            {"name": "local-chat", "capabilities": ["completion"]},
            {"name": "local-hybrid", "capabilities": ["completion", "embedding"]},
            {"name": "qwen3-embedding:0.6b", "capabilities": ["embedding"]},
        ]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            models, available = server._ollama_models("http://ollama:11434")
        self.assertTrue(available)
        self.assertEqual(models, ["local-chat", "local-hybrid"])

    def test_gemini_catalog_filters_models_without_content_generation(self):
        payload = {"models": [
            {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-2", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-3.8-flash-tts", "supportedGenerationMethods": ["generateContent"]},
        ]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            models = server._gemini_models_for_key("test-key")
        self.assertEqual(models, {"gemini-3.8-flash"})

    def test_pool_config_creates_lanes_per_model_host_and_gemini_key(self):
        original_cache = server.AI_OPTIONS_CACHE
        server.AI_OPTIONS_CACHE = None
        with tempfile.TemporaryDirectory() as temp:
            original_root = server.SERVICE_ROOT
            server.SERVICE_ROOT = Path(temp)
            try:
                options = {"ollamaModels": ["local-a", "local-b"], "ollamaAvailable": True,
                           "geminiModels": ["gemini-3.8-flash"], "geminiConfigured": True,
                           "geminiKeyCount": 2}
                created = []
                def make_stub(name, model, **kwargs):
                    provider = StubProvider(model)
                    provider.base_url = kwargs.get("base_url")
                    created.append(provider)
                    return provider
                with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://ollama-a:11434,http://ollama-b:11434"}), \
                     patch.object(server, "_ai_options", return_value=options), \
                     patch.object(server, "_ollama_models", return_value=(['local-a', 'local-b'], True)), \
                     patch.object(server, "_gemini_api_keys", return_value=["secret-one", "secret-two"]), \
                     patch.object(server, "make_provider", side_effect=make_stub):
                    pool = server._pool_provider({"ollamaModels": "local-a,local-b",
                                                  "geminiModels": "gemini-3.8-flash"})
                self.assertEqual(pool.lane_count, 6)
                self.assertEqual([provider.base_url for provider in created], [
                    "http://ollama-a:11434", "http://ollama-b:11434",
                    "http://ollama-a:11434", "http://ollama-b:11434",
                ])
                self.assertTrue(all("@ http://ollama-" in name for name, _ in pool.lanes[:4]))
                self.assertNotIn("secret-one", pool.label)
                self.assertNotIn("secret-two", pool.label)
            finally:
                server.SERVICE_ROOT = original_root
                server.AI_OPTIONS_CACHE = original_cache

    def test_pool_can_disable_ollama_without_discovering_or_calling_it(self):
        original_root = server.SERVICE_ROOT
        with tempfile.TemporaryDirectory() as temp:
            server.SERVICE_ROOT = Path(temp)
            try:
                gemini = StubProvider("gemini-3.8-flash")
                with patch.object(server, "_ai_options", side_effect=AssertionError("Ollama discovery called")), \
                     patch.object(server, "_gemini_api_keys", return_value=["test-key"]), \
                     patch.object(server, "_available_gemini_models", return_value=["gemini-3.8-flash"]), \
                     patch.object(server, "_ollama_models", side_effect=AssertionError("Ollama queried")), \
                     patch.object(server, "make_provider", side_effect=AssertionError("Ollama provider created")), \
                     patch("studai.api.server.GeminiProvider", return_value=gemini):
                    pool = server._pool_provider({"useOllama": "false", "ollamaModels": "local-a",
                                                  "geminiModels": "gemini-3.8-flash"})
                self.assertEqual(pool.lane_count, 1)
                self.assertIn("Gemini", pool.label)
            finally:
                server.SERVICE_ROOT = original_root

    def test_pool_adds_free_cloud_lanes_configured_in_env_with_rpm(self):
        original_root = server.SERVICE_ROOT
        with tempfile.TemporaryDirectory() as temp:
            server.SERVICE_ROOT = Path(temp)
            try:
                created = []
                def make_stub(name, model, **kwargs):
                    provider = StubProvider(model)
                    created.append((name, model, kwargs))
                    return provider
                env = {"GROQ_API_KEY": "groq-secret", "STUDAI_GROQ_MODELS": "model-a, model-b",
                       "STUDAI_GROQ_RPM": "25", "HF_TOKEN": "", "STUDAI_HF_MODELS": "ignored-without-token"}
                with patch.dict(os.environ, env), \
                     patch.object(server, "_gemini_api_keys", return_value=[]), \
                     patch.object(server, "_available_gemini_models", return_value=[]), \
                     patch.object(server, "make_provider", side_effect=make_stub):
                    pool = server._pool_provider({"useOllama": "false"})
                self.assertEqual(pool.lane_count, 2)
                self.assertEqual([name for name, _ in pool.lanes], ["Groq (model-a)", "Groq (model-b)"])
                self.assertEqual(pool._lane_rpm, [25, 25])
                self.assertEqual(pool.worker_count, 4)
                self.assertTrue(all(kwargs["request_attempts"] == 1 for _, _, kwargs in created))
                self.assertNotIn("groq-secret", pool.label)
                with patch.dict(os.environ, env), \
                     patch.object(server, "_gemini_api_keys", return_value=[]), \
                     patch.object(server, "_available_gemini_models", return_value=[]), \
                     patch.object(server, "make_provider", side_effect=make_stub):
                    with self.assertRaises(ValueError):
                        server._pool_provider({"useOllama": "false", "useFreeCloud": "false"})
            finally:
                server.SERVICE_ROOT = original_root

    def test_simultaneous_jobs_with_same_config_share_one_pool(self):
        built = []
        def fake_pool(payload, **kwargs):
            pool = RotatingPoolProvider([("Ollama", StubProvider("local"))])
            built.append(pool)
            return pool
        server.SHARED_POOLS.clear()
        try:
            with patch.object(server, "_pool_provider", side_effect=fake_pool):
                first = server._shared_pool_provider({"provider": "pool", "ollamaModels": "a"})
                second = server._shared_pool_provider({"provider": "pool", "ollamaModels": "a"})
                other = server._shared_pool_provider({"provider": "pool", "ollamaModels": "b"})
            self.assertIs(first, second)
            self.assertIsNot(first, other)
            self.assertEqual(len(built), 2)
        finally:
            server.SHARED_POOLS.clear()


if __name__ == "__main__":
    unittest.main()
