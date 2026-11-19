from unittest import TestCase
from src.core.pipelines.novel.fact_audit import format_ledger, merge_records, validate


class FactAuditTest(TestCase):
    def test_injured_and_historical_are_not_counted_as_current_deaths(self):
        passages = {"c1p0": {"source": "原稿顺序 1 · 第一章", "text": "原文证据"}}
        def casualty(name, state, temporal="current"):
            return dict(name=name, state=state, master=True, temporal=temporal, evidence=["c1p0"])
        ledger = {
            "ranks": [],
            "casualties": [
                casualty("甲", "confirmed_dead"), casualty("乙", "injured"),
                casualty("丙", "uncertain"), casualty("丁", "confirmed_dead", "historical"),
            ],
        }
        result = format_ledger(ledger, passages)
        self.assertIn("可确认 1 位", result["response"])
        self.assertIn("乙：重伤", result["response"])
        self.assertIn("丁：历史回忆", result["response"])

    def test_repeated_person_is_merged_but_temporal_context_is_preserved(self):
        records = [
            dict(name="甲", state="injured", temporal="current", evidence=["a"]),
            dict(name="甲", state="confirmed_dead", temporal="current", evidence=["b"]),
            dict(name="甲", state="confirmed_dead", temporal="historical", evidence=["c"]),
        ]
        merged = merge_records(records, "casualties")
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["state"], "confirmed_dead")
        self.assertEqual(merged[0]["evidence"], ["a", "b"])

    def test_unavailable_citation_is_rejected(self):
        with self.assertRaises(ValueError):
            validate({"ranks": [], "casualties": [{
                "name": "甲", "evidence": ["invented"],
            }]}, {})
