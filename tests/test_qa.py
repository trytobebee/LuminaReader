import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.core.pipelines.novel import agent, qa
from src.core.pipelines.novel.batch import CompletionLengthError


class ReaderQATest(unittest.TestCase):
    def test_history_is_bounded_and_keeps_latest_turn(self):
        turns = [{"role": "user", "content": str(index) * 2000} for index in range(10)]
        compact = qa.compact_history(turns)
        self.assertLessEqual(sum(len(turn["content"]) for turn in compact), 4000)
        self.assertTrue(compact[-1]["content"].startswith("9"))

    def test_reduction_keeps_every_chapter_in_order(self):
        entries = [{"source": str(index), "summary": "文" * 3000} for index in range(30)]

        def compress(endpoint, messages, tokens, **kwargs):
            chapters = json.loads(messages[-1]["content"])["chapters"]
            return json.dumps({chapter["source"]: "摘要" for chapter in chapters})

        with patch.object(qa, "completion", side_effect=compress):
            compact = qa.reduce_context("unused", "回顾", entries)
        self.assertEqual([row["source"] for row in compact], [row["source"] for row in entries])

    def test_reduction_rejects_missing_chapters(self):
        with patch.object(qa, "completion", return_value="{}"):
            with self.assertRaisesRegex(ValueError, "不完整"):
                qa.reduce_context("unused", "回顾", [{"source": "1", "summary": "文" * 61000}])

    def test_output_limit_retries_once_with_larger_budget(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE chapters(status TEXT)")
        chapter = {"chapter_index": 1, "title": "第一章", "summary": "情节", "key_events": "[]"}
        with patch.object(qa, "retrieve", return_value=[(chapter, [])]):
            with patch.object(qa, "completion", side_effect=[CompletionLengthError(), "完整回答"]) as model:
                self.assertEqual(qa.answer(conn, "unused", "情节是什么", [], None), "完整回答")
        self.assertEqual(model.call_count, 2)
        self.assertEqual(model.call_args.args[2], 8000)

    def test_location_recall_includes_gaps_arrival_and_departure(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE chapters(chapter_index INTEGER,title TEXT,summary TEXT,key_events TEXT,content TEXT)")
        conn.executemany("INSERT INTO chapters VALUES (?,?,?,?,?)", [
            (1, "石寨村", "前往村庄", "[]", "初到小石寨村"),
            (2, "大战", "与世家交战", "[]", "中间情节没有地名"),
            (3, "善后", "离开村庄", "[]", "离开小石寨村"),
            (4, "出发", "飞往京城", "[]", "前往京城"),
        ])
        name, chapters = qa.location_arc(conn, "回顾从秦宇初到小石寨村到离开去京城")
        self.assertEqual(name, "小石寨村")
        self.assertEqual([row["chapter_index"] for row in chapters], [1, 2, 3, 4])

    def test_departure_is_kept_when_generation_omits_it(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE chapters(status TEXT)")
        chapter = {
            "chapter_index": 1841, "title": "第1839章 你没这想法？",
            "summary": "秦宇乘坐航班前往京城。途中遇到气流。",
            "key_events": "[]", "content": "秦宇乘飞机前往京城。",
        }
        with patch.object(qa, "location_arc", return_value=("小石寨村", [chapter])):
            with patch.object(qa, "completion", return_value="村中大战结束后处理善后。"):
                result = qa.answer(conn, "unused", "回顾小石寨村到最后去往京城", [], None)
        self.assertIn("第1839章", result)
        self.assertIn("前往京城", result)

    def test_completed_job_survives_process_state_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "book.db"
            jobs = Path(directory) / "reader_qa"
            jobs.mkdir()
            job_id = "a" * 32
            (jobs / (job_id + ".json")).write_text(json.dumps({"response": "已完成"}))
            with patch.dict(agent.JOBS, clear=True):
                result = agent.status(job_id, database)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["response"], "已完成")
                self.assertIsNone(agent.status("../book", database))


if __name__ == "__main__":
    unittest.main()
