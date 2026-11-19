import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from src.api import app as api
from src.core.pipelines.novel.spending import Spending, root_for
from src.core.pipelines.novel.usage import UsageMeter


def usage(tokens=110, cost=.001, calls=1, reported=1):
    return dict(input_tokens=tokens-10, output_tokens=10, total_tokens=tokens,
                estimated_cost_usd=cost, api_requests=calls, reported_requests=reported,
                usage_complete=calls == reported)


class SpendingTest(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "book.db"
        self.ledger = Spending(self.database)

    def test_history_checkpoint_pair_imports_once_and_ignores_benchmarks(self):
        history = Path(self.temp.name) / "reader_qa"
        history.mkdir()
        key = "a" * 32
        (history / (key + ".checkpoint.json")).write_text(json.dumps({"usage": usage()}))
        (history / (key + ".json")).write_text(json.dumps({"usage": usage(220, .002, 2, 2)}))
        (history / ("ledger-" + key + ".json")).write_text(json.dumps({"usage": usage(9999, 99)}))
        benchmarks = Path(self.temp.name) / "benchmarks"
        benchmarks.mkdir()
        (benchmarks / ("b" * 32 + ".json")).write_text(json.dumps({"usage": usage(9999, 99)}))
        first = self.ledger.snapshot()
        self.assertEqual(first["total_tokens"], 220)
        self.assertEqual(first["known_cost_usd"], .002)
        self.assertEqual(first["historical_records"], 1)
        self.assertEqual(self.ledger.snapshot()["known_cost_usd"], .002)
        (history / (key + ".json")).unlink()
        self.assertEqual(self.ledger.snapshot()["total_tokens"], 220)

    def test_cumulative_snapshots_and_new_attempts_are_distinct(self):
        self.ledger.update("run1", self.database, "job", usage())
        self.ledger.update("run1", self.database, "job", usage(220, .002, 2, 2))
        self.ledger.update("run1", self.database, "job", usage())
        self.assertEqual(self.ledger.snapshot()["total_tokens"], 220)
        self.ledger.update("run2", self.database, "job", usage(), status="completed")
        final = Spending(self.database).snapshot()
        self.assertEqual(final["total_tokens"], 330)
        self.assertEqual(final["known_cost_usd"], .003)
        self.assertEqual(sum(event["tokens"] for event in final["events"]), 330)

    def test_resumed_carry_is_not_charged_twice(self):
        self.ledger.update("old", self.database, "job", usage(), status="failed")
        meter = UsageMeter()
        meter.carry(usage())
        meter.on_change = lambda: self.ledger.update("retry", self.database, "job", meter.summary(include_carried=False))
        meter.request()
        meter.record("gpt-6.1-sol", {"input_tokens":200,"output_tokens":20})
        self.assertEqual(meter.summary()["total_tokens"], 330)
        self.assertEqual(self.ledger.snapshot()["total_tokens"], 330)

    def test_live_result_backfill_does_not_reimport_full_carried_usage(self):
        directory = Path(self.temp.name) / "reader_harness" / "book"
        directory.mkdir(parents=True)
        job = "c" * 32
        self.ledger.update("run", self.database, job, usage(), status="completed")
        (directory / (job + ".json")).write_text(json.dumps({"usage": usage(990, .009)}))
        self.assertEqual(self.ledger.snapshot()["total_tokens"], 110)

    def test_mixed_priced_and_unpriced_usage_keeps_known_cost(self):
        meter = UsageMeter()
        meter.request()
        meter.record("gpt-6.1-sol", {"input_tokens":1000,"output_tokens":100})
        meter.request()
        meter.record("qwen-local", {"prompt_tokens":2000,"completion_tokens":200})
        result = meter.summary()
        self.assertIsNone(result["estimated_cost_usd"])
        self.assertEqual(result["known_cost_usd"], .003)
        self.ledger.update("mixed", self.database, "job", result, status="completed")
        total = self.ledger.snapshot()
        self.assertEqual(total["total_tokens"], 3300)
        self.assertEqual(total["known_cost_usd"], .003)
        self.assertEqual(total["unpriced_requests"], 1)

    def test_inflight_usage_and_missing_usage_do_not_invent_token_counts(self):
        self.ledger.update("r", self.database, "job", usage(10, None, 1, 0))
        active = self.ledger.snapshot()
        self.assertEqual(active["active_requests"], 1)
        self.assertEqual(active["known_cost_usd"], 0)
        self.ledger.update("r", self.database, "job", usage(10, None, 1, 0), status="failed")
        self.assertEqual(self.ledger.snapshot()["missing_usage_requests"], 1)

    def test_library_books_share_one_ledger_and_endpoint_is_read_only(self):
        imported = Path(self.temp.name) / "library" / ("d" * 24) / "book.db"
        self.assertEqual(root_for(imported), root_for(self.database))
        self.ledger.update("r", self.database, "job", usage(), status="completed")
        with patch.object(api, "DB_PATH", str(self.database)):
            client = api.app.test_client()
            for query in ("", "?document_id=unrelated"):
                response = client.get("/api/usage" + query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["total_tokens"], 110)
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_partial_legacy_record_does_not_invent_missing_request_count(self):
        directory = Path(self.temp.name) / "reader_qa"
        directory.mkdir()
        old = usage(110, .001, 5, 3)
        del old["reported_requests"]
        (directory / ("f" * 32 + ".json")).write_text(json.dumps({"usage": old}))
        result = self.ledger.snapshot()
        self.assertEqual(result["known_cost_usd"], .001)
        self.assertEqual(result["incomplete_history_records"], 1)
        self.assertEqual(result["missing_usage_requests"], 0)
