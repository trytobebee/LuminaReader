from contextlib import redirect_stdout
import io
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase

from src.api.documents import import_document, list_documents, resolve_database, split_sections
from src.core.pipelines.novel import qa, search


class DocumentImportTest(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.default = str(Path(self.directory.name) / "existing.db")

    def load(self, title, text):
        with redirect_stdout(io.StringIO()):
            return import_document(self.default, title, text.encode("utf-8"))

    def test_import_indexes_chapters_and_allows_raw_text_retrieval(self):
        doc = self.load("阅读测试.txt", "第1章 初遇\n阿林在桥边遇见小雨。\n第2章 告别\n阿林在车站告别小雨。")
        self.assertEqual(doc["chapter_count"], 2)
        self.assertEqual(doc["analyzed_count"], 0)
        database = resolve_database(self.default, doc["id"])
        with sqlite3.connect(database) as conn:
            conn.row_factory = sqlite3.Row
            hits = qa.retrieve(conn, "阿林在哪里告别小雨？", [], None)
            self.assertTrue(hits)
            self.assertEqual(hits[0][0]["chapter_index"], 2)
            self.assertTrue(search.search(conn, "阿林", "people"))
            self.assertTrue(search.search(conn, "车站", "plot"))

    def test_content_isolation_and_duplicate_import(self):
        first = self.load("甲.txt", "第1章 甲章\n只有甲书的内容。")
        duplicate = self.load("甲.txt", "第1章 甲章\n只有甲书的内容。")
        second = self.load("乙.md", "# 乙章\n只有乙书的内容。")
        self.assertEqual(first["id"], duplicate["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(list_documents(self.default)), 2)
        with sqlite3.connect(resolve_database(self.default, second["id"])) as conn:
            text = conn.execute("SELECT content FROM chapters").fetchone()[0]
            self.assertNotIn("甲书", text)

    def test_long_sections_preserve_text(self):
        original = "山" * 14500
        sections = split_sections(original, "正文.txt")
        self.assertTrue(all(len(section[2]) <= 6000 for section in sections))
        self.assertEqual("".join(section[2] for section in sections), original)

    def test_invalid_format_empty_and_unknown_document(self):
        with self.assertRaises(ValueError):
            import_document(self.default, "bad.pdf", b"text")
        with self.assertRaises(ValueError):
            self.load("empty.txt", " ")
        with self.assertRaises(ValueError):
            resolve_database(self.default, "../other")
