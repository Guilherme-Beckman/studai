import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.answer_keys.service import associate_answer_key
from studai.features.classification.cache import ClassificationCache
from studai.features.classification.rag import LocalExampleRetriever
from studai.features.diagnostics import service as diagnostics
from studai.integrations import ai_cache
from studai.integrations.llm import DemoProvider, LLMError, MockProvider, Provider


class CountingDemo(DemoProvider):
    """Demo provider that behaves like a paid one: cacheable and counted."""
    cacheable = True

    def __init__(self):
        super().__init__(model="counting")
        self.calls = 0

    @property
    def label(self):
        return "CountingDemo:test"

    def complete(self, system, user, images=None):
        self.calls += 1
        return super().complete(system, user, images)


class ScriptedProvider(Provider):
    def __init__(self, *answers):
        super().__init__(model="scripted")
        self.answers = list(answers)
        self.calls = 0

    def complete(self, system, user, images=None):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


METRICS = {"subjects": {"Matemática": {"attempts": 3, "correct": 1, "accuracy": 1 / 3,
           "error_reasons": {"unknown_content": 1, "calc_error": 1, "misread": 0, "blank": 0},
           "status": "assessed"}}}
PROFILE = {"nome": "Curso de teste", "pesos": {"Matemática": 1}}


class AICacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"STUDAI_CACHE_DIR": str(self.root / "cache"), "STUDAI_AI_CACHE": "on"})
        self.env.start()
        ai_cache.reset_cache_stats()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_validated_result_is_reused_and_failures_are_not_stored(self):
        provider = CountingDemo()
        calls = []
        compute = lambda: calls.append(1) or {"ok": True}
        self.assertEqual(ai_cache.cached_ai_result("k", provider, "s", "u", compute), {"ok": True})
        self.assertEqual(ai_cache.cached_ai_result("k", provider, "s", "u", compute), {"ok": True})
        self.assertEqual(len(calls), 1)
        self.assertTrue((self.root / "cache" / ai_cache.AI_RESPONSES_FILE).is_file())

        failed = []
        for _ in range(2):
            self.assertIsNone(ai_cache.cached_ai_result("k", provider, "s", "other", lambda: failed.append(1)))
        self.assertEqual(len(failed), 2)
        self.assertEqual(ai_cache.cache_stats(), {"k.miss": 3, "k.hit": 1})

    def test_offline_and_undeclared_providers_bypass_the_cache(self):
        for provider in (MockProvider(model=""), object(), None):
            calls = []
            for _ in range(2):
                ai_cache.cached_ai_result("k", provider, "s", "u", lambda: calls.append(1) or {"v": 1})
            self.assertEqual(len(calls), 2)
        self.assertFalse((self.root / "cache" / ai_cache.AI_RESPONSES_FILE).exists())

    def test_cache_can_be_disabled_by_environment(self):
        provider = CountingDemo()
        calls = []
        with patch.dict(os.environ, {"STUDAI_AI_CACHE": "off"}):
            for _ in range(2):
                ai_cache.cached_ai_result("k", provider, "s", "u", lambda: calls.append(1) or {"v": 1})
        self.assertEqual(len(calls), 2)

    def test_identity_includes_model_and_generation_settings(self):
        first, second = CountingDemo(), CountingDemo()
        second.temperature = 0.7
        self.assertNotEqual(ai_cache.provider_identity(first), ai_cache.provider_identity(second))

    def test_embeddings_are_requested_once_per_model_and_text(self):
        requested = []

        def embed(texts, *, model):
            requested.append(list(texts))
            return [[float(len(text)), 1.0] for text in texts]

        with ai_cache.EmbeddingCache(self.root / "vectors.sqlite3") as cache:
            first = ai_cache.embed_with_cache(["a", "bb", "a"], model="m", embed=embed, cache=cache)
            second = ai_cache.embed_with_cache(["bb", "ccc"], model="m", embed=embed, cache=cache)
            ai_cache.embed_with_cache(["a"], model="other-model", embed=embed, cache=cache)
        self.assertEqual(first, [[1.0, 1.0], [2.0, 1.0], [1.0, 1.0]])
        self.assertEqual(second, [[2.0, 1.0], [3.0, 1.0]])
        self.assertEqual(requested, [["a", "bb"], ["ccc"], ["a"]])

    def test_rag_query_embedding_is_cached_across_retrievals(self):
        cache = ClassificationCache(self.root / "classification.sqlite3")
        try:
            retriever = LocalExampleRetriever(cache, base_url="http://a,http://b", model="embed-test")
            with patch("studai.features.classification.rag.ollama_embed",
                       side_effect=lambda texts, **_: [[1.0, 0.0] for _ in texts]) as embed:
                retriever.retrieve("mesma questão")
                retriever.retrieve("mesma questão")
                retriever.retrieve("outra questão")
        finally:
            cache.close()
        self.assertEqual(embed.call_count, 2)

    def test_rag_query_falls_back_to_next_host_before_caching(self):
        cache = ClassificationCache(self.root / "classification.sqlite3")
        try:
            retriever = LocalExampleRetriever(cache, base_url="http://down,http://up", model="embed-test")

            def embed(texts, *, base_url, **_):
                if "down" in base_url:
                    raise LLMError("offline")
                return [[0.0, 1.0] for _ in texts]
            with patch("studai.features.classification.rag.ollama_embed", side_effect=embed) as mocked:
                retriever.retrieve("q")
                retriever.retrieve("q")
        finally:
            cache.close()
        self.assertEqual(mocked.call_count, 2)  # down + up once; second retrieval hits the cache

    def test_diagnostic_analysis_is_not_requested_twice_for_the_same_metrics(self):
        provider = CountingDemo()
        first, error = diagnostics.analyze(provider, METRICS, PROFILE)
        second, second_error = diagnostics.analyze(provider, METRICS, PROFILE)
        self.assertIsNone(error)
        self.assertIsNone(second_error)
        self.assertEqual(first, second)
        self.assertEqual(provider.calls, 1)

    def test_topic_plan_is_reused(self):
        provider = CountingDemo()
        plans = [diagnostics.plan_topics(provider, PROFILE, {"Matemática": ["Funções"]}) for _ in range(2)]
        self.assertEqual(plans[0], plans[1])
        self.assertEqual(plans[0][1]["status"], "llm")
        self.assertEqual(provider.calls, 1)

    def test_answer_key_association_decision_is_reused_but_network_failures_are_not(self):
        exam = Path("fuvest-2024-primeira-fase-V1.pdf")
        keys = [Path("gabarito-fuvest-2024-primeira-fase-v1-oficial.pdf"), Path("gabarito-outro-2023.pdf")]
        answer = json.dumps({"arquivo": keys[0].name, "confianca": 0.95})
        provider = ScriptedProvider(answer)
        self.assertEqual(associate_answer_key(exam, keys, provider), (keys[0], "llm"))
        self.assertEqual(associate_answer_key(exam, keys, provider), (keys[0], "llm"))
        self.assertEqual(provider.calls, 1)

        offline = ScriptedProvider(LLMError("rede"), LLMError("rede"), json.dumps({"arquivo": None}), "{}")
        other_exam = Path("simulado-xyz.pdf")
        self.assertEqual(associate_answer_key(other_exam, keys, offline), (None, "ambiguous"))
        self.assertEqual(associate_answer_key(other_exam, keys, offline), (None, "ambiguous"))
        self.assertEqual(associate_answer_key(other_exam, keys, offline), (None, "ambiguous"))
        self.assertEqual(offline.calls, 4)  # failed run retried; explicit "no match" stored


class AICallsBenchmarkTests(unittest.TestCase):
    def test_offline_benchmark_counts_calls_and_cache_reuse(self):
        import contextlib
        import io
        from studai.tools import benchmark_ai_calls
        with contextlib.redirect_stdout(io.StringIO()):
            report = benchmark_ai_calls.main(["--questions", "4", "--examples", "3"])
        rows = {row["scenario"]: row for row in report["scenarios"]}
        self.assertEqual(rows["classificacao_inicial"]["llm_calls"], 12)
        self.assertEqual(rows["reprocessar_igual"]["llm_calls"], 0)
        self.assertEqual(rows["reprocessar_apos_revisao"]["embedding_texts"], 0)
        self.assertEqual(rows["gabarito_lote_2x"]["llm_calls"], 1)
        self.assertEqual(rows["analise_diagnostica_2x"]["llm_calls"], 1)


if __name__ == "__main__":
    unittest.main()
