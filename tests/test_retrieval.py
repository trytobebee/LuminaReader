import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from src.core.pipelines.novel import qa, retrieval


class AdaptiveRetrievalTest(TestCase):
    def book(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE chapters(chapter_index INTEGER,title TEXT,summary TEXT,key_events TEXT,content TEXT)")
        conn.executemany("INSERT INTO chapters VALUES (?,?,?,?,?)", [
            (1, "石寨村", "到村", "[]", "秦宇来到小石寨村。"),
            (2, "大战", "有人断臂", "[]", "三位宗师断臂后仍在交谈，没有死亡。"),
            (3, "离开", "离村", "[]", "秦宇离开小石寨村去京城。"),
        ])
        return conn

    def plan(self):
        return dict(needs_original=True, missing_points=["断臂是否死亡"],
                    search_terms=["宗师", "断臂"], chapter_indices=[2])

    def test_exact_followup_inherits_story_scope(self):
        conn = self.book()
        arc = qa.resolve_arc(conn, "在这个过程中有多少宗师或者传奇宗师殒命呢？",
                             [{"role": "user", "content": "回顾小石寨村到离开去京城的情节"}])
        self.assertIsNotNone(arc)
        self.assertEqual([row["chapter_index"] for row in arc[1]], [1, 2, 3])

    def test_explicit_scope_survives_shortened_history(self):
        conn = self.book()
        arc = qa.resolve_arc(conn, "这个过程中还有谁？", [],
                             {"start": 1, "end": 3, "location": "小石寨村"})
        self.assertEqual(len(arc[1]), 3)

    def test_incomplete_initial_answer_is_replaced_by_source_audit(self):
        conn = self.book()
        arc = qa.location_arc(conn, "回顾小石寨村")
        with patch.object(qa, "answer", return_value="至少5位，缺少原文"):
            with patch.object(retrieval, "assess", return_value=self.plan()):
                with patch("src.core.pipelines.novel.fact_audit.answer_from_source",
                           return_value={"response": "原文核验后的清单", "timeline": []}) as audit:
                    result = retrieval.refine_answer(
                        conn, "unused", "在这个过程中有多少宗师殒命？", [], None, arc
                    )
        audit.assert_called_once()
        self.assertEqual(result["response"], "原文核验后的清单")
        self.assertTrue(result["retrieval"]["performed"])

    def test_general_uncertainty_triggers_original_search_and_new_generation(self):
        conn = self.book()
        passages = {"c2p0": {"chapter_index": 2, "title": "大战", "text": "三位宗师断臂后仍在交谈，没有死亡。"}}
        final = dict(answer="原文只写断臂，三人仍存活。", cited_paragraphs=["c2p0"],
                     unresolved=[], needs_more=False, search_terms=[])
        with patch.object(qa, "answer", return_value="不确定三人是否死亡"):
            with patch.object(retrieval, "assess", return_value=self.plan()):
                with patch.object(qa, "retrieve", return_value=[]):
                    with patch.object(retrieval, "search_original", return_value=passages) as search:
                        with patch.object(retrieval, "completion", return_value=json.dumps(final)):
                            result = retrieval.refine_answer(conn, "unused", "三人结局是什么", [], None)
        search.assert_called_once()
        self.assertIn("仍存活", result["response"])
        self.assertEqual(result["timeline"][0]["quotes"], [passages["c2p0"]["text"]])

    def test_unknown_reference_is_rejected(self):
        conn = self.book()
        final = dict(answer="捏造结论", cited_paragraphs=["missing"], unresolved=[], needs_more=False, search_terms=[])
        with patch.object(qa, "answer", return_value="不确定"):
            with patch.object(retrieval, "assess", return_value=self.plan()):
                with patch.object(qa, "retrieve", return_value=[]):
                    with patch.object(retrieval, "search_original", return_value={
                        "c2p0": {"chapter_index": 2, "title": "大战", "text": "原文"},
                    }):
                        with patch.object(retrieval, "completion", return_value=json.dumps(final)):
                            with self.assertRaisesRegex(ValueError, "引用"):
                                retrieval.refine_answer(conn, "unused", "三人的结局", [], None)

    def test_failed_original_search_resumes_saved_draft_and_plan(self):
        conn = self.book()
        arc = qa.location_arc(conn, "回顾小石寨村")
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            with patch.object(qa, "answer", return_value="至少5位，需核对"):
                with patch.object(retrieval, "assess", return_value=self.plan()):
                    with patch("src.core.pipelines.novel.fact_audit.answer_from_source", side_effect=OSError("断连")):
                        with self.assertRaises(OSError):
                            retrieval.refine_answer(conn, "unused", "多少宗师殒命", [], None, arc, checkpoint=checkpoint)
            with patch.object(qa, "answer", side_effect=AssertionError("重复生成初稿")):
                with patch.object(retrieval, "assess", side_effect=AssertionError("重复检查")):
                    with patch("src.core.pipelines.novel.fact_audit.answer_from_source",
                               return_value={"response": "已恢复核验", "timeline": []}):
                        result = retrieval.refine_answer(conn, "unused", "多少宗师殒命", [], None, arc, checkpoint=checkpoint)
            self.assertEqual(result["response"], "已恢复核验")

    def test_unresolved_first_retrieval_can_search_again(self):
        conn = self.book()
        first = {
            "answer": "尚不能确定结局", "cited_paragraphs": ["c2p0"],
            "unresolved": ["后续是否死亡"], "needs_more": True, "search_terms": ["后续", "死亡"],
        }
        second = {
            "answer": "后续原文说明仍然存活", "cited_paragraphs": ["c3p0"],
            "unresolved": [], "needs_more": False, "search_terms": [],
        }
        batches = [
            {"c2p0": {"chapter_index": 2, "title": "大战", "text": "三人断臂"}},
            {"c3p0": {"chapter_index": 3, "title": "离开", "text": "三人仍然存活"}},
        ]
        with patch.object(qa, "answer", return_value="资料不足"):
            with patch.object(retrieval, "assess", return_value=self.plan()):
                with patch.object(qa, "retrieve", return_value=[]):
                    with patch.object(retrieval, "search_original", side_effect=batches) as search:
                        with patch.object(retrieval, "completion", side_effect=[json.dumps(first), json.dumps(second)]):
                            result = retrieval.refine_answer(conn, "unused", "三人后来的结局", [], None)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(result["retrieval"]["rounds"], 2)
        self.assertIn("仍然存活", result["response"])
        self.assertEqual(result["timeline"][0]["chapter_index"], 3)

    def test_missing_conversation_scope_does_not_turn_into_global_count(self):
        conn = self.book()
        plan = self.plan()
        plan["search_terms"] = ["宗师"]
        with patch.object(qa, "answer", return_value="范围不明"):
            with patch.object(retrieval, "assess", return_value=plan):
                with patch.object(retrieval, "search_original", side_effect=AssertionError("全书盲目统计")):
                    result = retrieval.refine_answer(conn, "unused", "在这个过程中多少宗师殒命", [], None)
        self.assertIn("补充地点或起止章节", result["response"])
