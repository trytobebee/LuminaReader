from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from unittest import TestCase

from src.core.pipelines.novel.usage import CURRENT, UsageMeter, cached_result, record_usage, request_started


class UsageTest(TestCase):
    def test_cached_and_written_inputs_are_not_double_charged(self):
        meter = UsageMeter()
        meter.request()
        meter.record("gpt-6.1-sol", {
            "input_tokens": 1000, "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 300},
            "output_tokens_details": {"reasoning_tokens": 100},
        })
        usage = meter.summary()
        self.assertEqual(usage["total_tokens"], 1200)
        self.assertEqual(usage["reasoning_tokens"], 100)
        self.assertAlmostEqual(usage["estimated_cost_usd"], 0.00339)

    def test_nested_workers_and_truncated_retries_share_meter(self):
        meter = UsageMeter()
        token = CURRENT.set(meter)
        try:
            def call():
                request_started()
                record_usage("gpt-6.1-sol", {"input_tokens": 10, "output_tokens": 20})
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(copy_context().run, call) for _ in range(3)]
                for future in futures:
                    future.result()
        finally:
            CURRENT.reset(token)
        self.assertEqual(meter.summary()["api_requests"], 3)
        self.assertEqual(meter.summary()["total_tokens"], 90)

    def test_missing_usage_is_marked_incomplete(self):
        meter = UsageMeter()
        meter.request()
        self.assertFalse(meter.summary()["usage_complete"])

    def test_local_cache_costs_zero_and_keeps_original_usage(self):
        result = {"response": "回答", "usage": {"total_tokens": 100}}
        cached = cached_result(result)
        self.assertEqual(cached["usage"]["total_tokens"], 0)
        self.assertEqual(cached["usage"]["estimated_cost_usd"], 0)
        self.assertEqual(cached["generation_usage"]["total_tokens"], 100)
        self.assertEqual(result["usage"]["total_tokens"], 100)

    def test_resumed_analysis_includes_previous_stage_usage_once(self):
        previous = UsageMeter()
        previous.request()
        previous.record("gpt-6.1-sol", {"input_tokens": 100, "output_tokens": 10})
        meter = UsageMeter()
        meter.carry(previous.summary())
        meter.carry(previous.summary())
        meter.request()
        meter.record("gpt-6.1-sol", {"input_tokens": 200, "output_tokens": 20})
        result = meter.summary()
        self.assertEqual(result["api_requests"], 2)
        self.assertEqual(result["total_tokens"], 330)
        self.assertTrue(result["resumed"])
