import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from studai.integrations.llm import (GeminiProvider, LLMError, ModelQuota, Provider, RotatingPoolProvider,
                                     gemini_limits_from_env, next_pacific_midnight)

OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
DAILY_429 = ('HTTP 429: {"error": {"code": 429, "message": "You exceeded your current quota. Please retry in 41s.",'
             ' "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}')
MINUTE_429 = ('HTTP 429: {"error": {"code": 429, "message": "You exceeded your current quota. Please retry in 12s.",'
              ' "details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}')


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.wall = time.mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))

    def sleep(self, seconds):
        self.now += seconds
        self.wall += seconds


class GeminiQuotaTests(unittest.TestCase):
    def make(self, models, limits, clock=None):
        clock = clock or FakeClock()
        quota = ModelQuota(limits, clock=lambda: clock.now, wall_clock=lambda: clock.wall)
        provider = GeminiProvider(model=models, api_key="k", quota=quota)
        provider._sleep = clock.sleep
        return provider, quota, clock

    def called_models(self, request):
        return [call.args[0].split("/models/")[1].split(":")[0] for call in request.call_args_list]

    def test_model_at_daily_limit_is_skipped_without_calling_it(self):
        provider, quota, _ = self.make("flash,lite", {"flash": (5, 2), "lite": (15, 500)})
        with patch("studai.integrations.llm.http_post", return_value=OK) as request:
            for _ in range(6):
                provider.complete("s", "u")
        used = self.called_models(request)
        self.assertEqual(used.count("flash"), 2)
        self.assertEqual(used.count("lite"), 4)
        self.assertEqual(quota.remaining_today("flash"), 0)

    def test_model_at_minute_limit_yields_to_the_next_one(self):
        provider, _, clock = self.make("flash,lite", {"flash": (1, 20), "lite": (15, 500)})
        with patch("studai.integrations.llm.http_post", return_value=OK) as request:
            for _ in range(4):
                provider.complete("s", "u")
        self.assertEqual(self.called_models(request), ["flash", "lite", "lite", "lite"])
        self.assertEqual(clock.now, 1000.0)

    def test_waits_for_minute_limit_when_every_model_is_busy(self):
        provider, _, clock = self.make("flash", {"flash": (2, 20)})
        with patch("studai.integrations.llm.http_post", return_value=OK) as request:
            for _ in range(3):
                provider.complete("s", "u")
        self.assertEqual(request.call_count, 3)
        self.assertAlmostEqual(clock.now, 1060.0)

    def test_daily_429_exhausts_model_until_reset(self):
        provider, quota, clock = self.make("flash,lite", {})
        responses = [LLMError(DAILY_429, status_code=429), OK, OK]
        with patch("studai.integrations.llm.http_post", side_effect=responses) as request:
            provider.complete("s", "u")
            provider.complete("s", "u")
        self.assertEqual(self.called_models(request), ["flash", "lite", "lite"])
        self.assertIsNone(quota.wait_time("flash"))
        clock.wall = quota.day_ends + 1
        self.assertEqual(quota.wait_time("flash"), 0)

    def test_minute_429_cools_model_and_retries_it_after_the_delay(self):
        provider, _, clock = self.make("flash", {})
        responses = [LLMError(MINUTE_429, status_code=429), OK]
        with patch("studai.integrations.llm.http_post", side_effect=responses) as request:
            self.assertEqual(provider.complete("s", "u"), "ok")
        self.assertEqual(request.call_count, 2)
        self.assertAlmostEqual(clock.now, 1012.0)

    def test_all_models_out_for_the_day_pause_pool_lane_until_pacific_midnight(self):
        provider, quota, clock = self.make("flash,lite", {"flash": (5, 1), "lite": (5, 1)})
        pool = RotatingPoolProvider([("Gemini", provider)], wall_clock=lambda: clock.wall)
        with patch("studai.integrations.llm.http_post", return_value=OK) as request:
            pool.complete("s", "u")
            pool.complete("s", "u")
            with self.assertRaises(LLMError):
                pool.complete("s", "u")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(pool.quota_exhausted_lanes, ["Gemini"])
        self.assertEqual(pool._quota_until[0], quota.day_ends)

    def test_pacific_midnight_is_reset_time(self):
        # 2026-10-17 23:30 UTC = 16:30 PDT -> próximo reset 2026-10-18 07:00 UTC.
        now = 1792279800.0
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M", time.gmtime(next_pacific_midnight(now))),
                         "2026-10-18 07:00")

    def test_env_overrides_and_disables_limits(self):
        with patch.dict("os.environ", {"STUDAI_GEMINI_LIMITS": "gemini-x=10/1000"}):
            limits = gemini_limits_from_env()
        self.assertEqual(limits["gemini-x"], (10, 1000))
        self.assertEqual(limits["gemini-3.6-flash"], (5, 20))
        with patch.dict("os.environ", {"STUDAI_GEMINI_LIMITS": "off"}):
            self.assertEqual(gemini_limits_from_env(), {})


if __name__ == "__main__":
    unittest.main()
