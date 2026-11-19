import json
from pathlib import Path
import sqlite3
import tempfile
import time
from unittest import TestCase
from unittest.mock import patch

from src.api import app as api
from src.core.pipelines.novel import chat_runtime, reader_harness
from src.core.pipelines.novel.usage import record_usage,request_started


class ChatRuntimeTest(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database=str(Path(self.temp.name)/"book.db")
        self.other=str(Path(self.temp.name)/"other.db")
        for path,text in [(self.database,"甲书原文"),(self.other,"乙书原文")]:
            with sqlite3.connect(path) as conn:
                conn.executescript("CREATE TABLE chapters(chapter_index INTEGER,title TEXT,content TEXT,summary TEXT,key_events TEXT);")
                conn.execute("INSERT INTO chapters VALUES (1,'第1章',?,NULL,'[]')",(text,))
        self.calls=0

    def fake(self,base,messages,*args,**kwargs):
        request_started()
        record_usage("gpt-6.1-sol",{"input_tokens":100,"output_tokens":20,"total_tokens":120})
        self.calls+=1
        payload=json.loads(messages[1]["content"])
        observed=payload["state"]["observations"]
        value=dict(goal="解释本章",plan=["读取原文"],coverage="targeted",required_chapter_ids=[],
                   context_dependencies=["current_chapter"],action="execute",answer="",citations=[],unresolved=[],
                   calls=[dict(id="source",tool="read",arguments={"chapter_ids":[1]})])
        if "source" in observed:
            value.update(action="answer",calls=[],answer=observed["source"]["passages"]["c1p0"]["text"],citations=["c1p0"])
        return json.dumps(value,ensure_ascii=False)

    def wait(self,job,database):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=chat_runtime.status(job,database)
            if result and result["status"]!="running":
                self.assertEqual(result["status"],"completed",result)
                return result
            time.sleep(.005)
        self.fail("offline job did not finish")

    def test_answer_cache_isolated_by_document_and_invalidated_on_edit(self):
        with patch.object(reader_harness,"completion",side_effect=self.fake):
            first=chat_runtime.start(self.database,"unused","解释本章",chapter=1)
            a=self.wait(first["job_id"],self.database)
            self.assertEqual(a["response"],"甲书原文")
            self.assertEqual(chat_runtime.Spending(self.database).snapshot()["total_tokens"],240)
            cached=chat_runtime.start(self.database,"unused","解释本章",chapter=1)
            self.assertTrue(cached["cache_hit"])
            self.assertEqual(cached["usage"]["api_requests"],0)
            self.assertEqual(self.calls,2)
            self.assertEqual(chat_runtime.Spending(self.database).snapshot()["total_tokens"],240)
            b=chat_runtime.start(self.other,"unused","解释本章",chapter=1)
            self.assertEqual(self.wait(b["job_id"],self.other)["response"],"乙书原文")
            self.assertIsNone(chat_runtime.status(first["job_id"],self.other))
            with sqlite3.connect(self.database) as conn:
                conn.execute("UPDATE chapters SET content='甲书新原文'")
            newer=chat_runtime.start(self.database,"unused","解释本章",chapter=1)
            self.assertNotEqual(newer["job_id"],first["job_id"])
            self.assertEqual(self.wait(newer["job_id"],self.database)["response"],"甲书新原文")

    def test_feedback_activates_then_disables_recipe_and_clears_answer_cache(self):
        with patch.object(reader_harness,"completion",side_effect=self.fake):
            job=chat_runtime.start(self.database,"unused","解释本章",chapter=1)
            result=self.wait(job["job_id"],self.database)
        recipe=result["harness"]["learned_workflow"]["id"]
        self.assertEqual(chat_runtime.feedback(job["job_id"],self.database,True)["workflow_status"],"active")
        self.assertEqual(chat_runtime.feedback(job["job_id"],self.database,False)["workflow_status"],"disabled")
        self.assertFalse((chat_runtime.directory(self.database)/(job["job_id"]+".json")).exists())
        self.assertEqual(chat_runtime.Spending(self.database).snapshot()["total_tokens"],240)
        store=reader_harness.WorkflowStore(chat_runtime.directory(self.database),[reader_harness.VERSION,*chat_runtime.model_identity()])
        self.assertEqual(store.list()[0]["id"],recipe)
        self.assertEqual(store.list()[0]["status"],"disabled")

    def test_chat_endpoint_uses_unified_runtime_for_all_question_wording(self):
        client=api.app.test_client()
        with patch.object(api,"DB_PATH",self.database),patch.object(api,"PROVIDER","openai"):
            with patch.object(chat_runtime,"start",return_value={"job_id":"a"*32,"status":"running"}) as start:
                for question in ["秦宇和欧阳明谁修为高？","回顾小石寨村情节","第1章有多少人？"]:
                    response=client.post("/api/chat",json={"message":question})
                    self.assertEqual(response.status_code,202)
                self.assertEqual(start.call_count,3)
            self.assertEqual(client.get("/api/chat/jobs/../../etc/passwd").status_code,404)
            self.assertEqual(client.post("/api/chat/feedback",json={"job_id":"a","helpful":"yes"}).status_code,400)
