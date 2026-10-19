import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.features.classification import service
from studai.integrations.llm import LLMError, MockProvider, Provider, RotatingPoolProvider


class FlakyProvider(MockProvider):
    """Fails the first calls (like a lane under rate limit) and then recovers."""

    def __init__(self, failures):
        super().__init__(model="flaky")
        self.failures = failures
        self.calls = 0

    def complete(self, system, user, images=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise LLMError("HTTP 503: sobrecarregado", status_code=503)
        return super().complete(system, user, images)


class FailingProvider(Provider):
    def __init__(self, status_code=500):
        super().__init__(model="failing")
        self.status_code = status_code
        self.calls = 0

    def complete(self, system, user, images=None):
        self.calls += 1
        raise LLMError("HTTP {}: falhou".format(self.status_code), status_code=self.status_code)


class ClassificationRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.exam = Path(self.tmp.name) / "questoes.json"
        self.exam.write_text(json.dumps({"prova": "t", "questoes": [
            {"numero": 1, "enunciado": "Calcule a velocidade média do carro.", "alternativas": {}},
            {"numero": 2, "enunciado": "Qual a função da mitocondria?", "alternativas": {}},
        ]}), encoding="utf-8")

    def classify(self, provider, **kwargs):
        slept = []
        data = service.classificar(self.exam, provider, service.DEFAULT_TAXONOMY, workers=1, use_cache=False,
                                   use_rag=False, batch_size=1, log=lambda *_: None,
                                   sleep=slept.append, **kwargs)
        return data, slept

    def test_failed_questions_go_back_to_the_queue_until_they_succeed(self):
        dead = FailingProvider(status_code=503)
        flaky = FlakyProvider(failures=2)
        pool = RotatingPoolProvider([("Fora do ar", dead), ("Instável", flaky)])
        data, slept = self.classify(pool, retry_rounds=3, retry_delays=(5,))
        self.assertEqual(data["classificacao"]["erros"], {})
        self.assertTrue(all(q.get("materia") for q in data["questoes"]))
        self.assertEqual(data["processamento_cache"]["questoes_recuperadas"], 2)
        self.assertEqual(data["processamento_cache"]["rodadas_reprocessamento"], 1)
        for q in data["questoes"]:
            self.assertEqual(q["classificacao"]["tentativas"], 2)
            self.assertIn("Instável", q["classificacao"]["modelo"])
            self.assertNotIn("classificacao_erro", q)
        self.assertEqual(slept, [1.0] * 5)

    def test_question_is_kept_as_error_only_after_all_rounds(self):
        failing = FailingProvider(status_code=503)
        data, slept = self.classify(failing, retry_rounds=2, retry_delays=(0,))
        self.assertEqual(sorted(data["classificacao"]["erros"]), [1, 2])
        self.assertEqual(data["processamento_cache"]["rodadas_reprocessamento"], 2)
        self.assertTrue(all("classificacao_erro" in q for q in data["questoes"]))

    def test_retry_stops_when_pool_has_no_live_lanes(self):
        broke = FailingProvider(status_code=402)
        pool = RotatingPoolProvider([("Sem crédito", broke)])
        data, slept = self.classify(pool, retry_rounds=5, retry_delays=(30,))
        self.assertEqual(sorted(data["classificacao"]["erros"]), [1, 2])
        self.assertEqual(data["processamento_cache"]["rodadas_reprocessamento"], 0)
        self.assertEqual(slept, [])

    def test_cancel_during_wait_stops_retrying(self):
        cancelled = [False]
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            cancelled[0] = True

        service.classificar(self.exam, FailingProvider(), service.DEFAULT_TAXONOMY, workers=1, use_cache=False,
                            use_rag=False, batch_size=1, log=lambda *_: None, sleep=sleep,
                            cancel_check=lambda: cancelled[0], retry_rounds=5, retry_delays=(10,))
        self.assertEqual(len(slept), 1)


if __name__ == "__main__":
    unittest.main()
