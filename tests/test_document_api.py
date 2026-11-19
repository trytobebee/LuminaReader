from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from src.api import app as module
from src.api.documents import import_document


class DocumentAPITest(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / "default.db")
        self.patch = patch.object(module, "DB_PATH", self.database)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = module.app.test_client()
        with redirect_stdout(io.StringIO()):
            self.first = import_document(self.database, "甲.txt", "第1章 甲章\n甲书原文".encode())
            self.second = import_document(self.database, "乙.txt", "第1章 乙章\n乙书原文".encode())

    def test_chapter_reads_and_edits_are_isolated_by_document(self):
        a, b = self.first["id"], self.second["id"]
        self.assertEqual(self.client.get(f"/api/chapters/1/content?document_id={a}").get_json()["content"], "甲书原文")
        self.assertEqual(self.client.get(f"/api/chapters/1/content?document_id={b}").get_json()["content"], "乙书原文")
        self.client.post(f"/api/chapters/1/summary?document_id={a}", json={"summary": "甲书概要", "key_events": "[]"})
        self.assertIsNone(self.client.get(f"/api/chapters?document_id={b}").get_json()[0]["summary"])

    def test_manually_added_person_belongs_to_selected_chapter(self):
        document_id = self.first["id"]
        self.client.post(f"/api/entities?document_id={document_id}", json={
            "name": "阿林", "type": "person", "description": "本章人物", "chapter_index": 1,
        })
        entries = self.client.get(f"/api/entities?document_id={document_id}&chapter_index=1").get_json()
        self.assertEqual(entries[0]["name"], "阿林")
        self.assertEqual(self.client.get(f"/api/entities?document_id={self.second['id']}&chapter_index=1").get_json(), [])

    def test_upload_endpoint_performs_indexing_without_model_calls(self):
        with patch("src.core.pipelines.novel.model_client.openai_completion", side_effect=AssertionError("Unexpected API call")):
            response = self.client.post("/api/documents", data={
                "file": (io.BytesIO("第1章 新内容\n文档正文".encode()), "新文档.txt"),
            })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["analyzed_count"], 0)
