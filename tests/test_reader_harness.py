"""Offline behavioral regression tests. Every model response is simulated."""
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch

from src.core.pipelines.novel import reader_harness as module
from src.core.pipelines.novel.reader_harness import Harness, Limits
from src.core.pipelines.novel.reader_tools import ToolError
from src.core.pipelines.novel.usage import record_usage, request_started


def decision(action="execute",calls=None,answer="",citations=None,**extra):
    return dict(goal="核对问题所需证据",plan=["检索并读取原文"],coverage="targeted",
                context_dependencies=[],
                required_chapter_ids=[],action=action,calls=calls or [],answer=answer,
                citations=citations or [],unresolved=[],**extra)


def lookup(name):
    return [dict(id="find",tool="search",arguments={"query":name,"mode":"original"}),
            dict(id="source",tool="read",arguments={"chapter_ids":"$find.chapter_ids"})]


class HarnessTest(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.conn=sqlite3.connect(":memory:")
        self.conn.row_factory=sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE chapters(chapter_index INTEGER,title TEXT,content TEXT,summary TEXT,key_events TEXT);
            CREATE TABLE chapter_people(chapter_index INTEGER,name TEXT,aliases TEXT,role TEXT);
        """)
        self.conn.executemany("INSERT INTO chapters VALUES (?,?,?,?,?)",[
            (1,"第1章 境界","秦宇七品初期。聂明升接近七品后期。",None,"[]"),
            (2,"第2章 断臂","三位宗师断臂后退走，仍能交谈。",None,"[]"),
            (3,"第3章 来客","欧阳明六品。",None,"[]"),
            (4,"第4章 幸存","念安和尚被秦宇救下，幸存。",None,"[]"),
        ])
        self.conn.executemany("INSERT INTO chapter_people VALUES (?,?,?,?)",[
            (1,"秦宇","[]",""),(1,"聂明升","[]",""),(3,"欧阳明","[]","")])

    def engine(self,question="秦宇和聂明升谁境界高？",**kwargs):
        return Harness(self.conn,"never-called",self.root,"book-v1",question,**kwargs)

    def run_script(self,harness,script):
        iterator=iter(script)
        def fake(*args,**kwargs):
            request_started()
            value=next(iterator)
            if isinstance(value,Exception):
                raise value
            record_usage("gpt-6.1-sol",{"input_tokens":100,"output_tokens":30,"total_tokens":130})
            return json.dumps(value,ensure_ascii=False)
        with patch.object(module,"completion",side_effect=fake) as model:
            result=harness.run()
        return result,model.call_count

    def test_two_names_do_not_trigger_full_book_relationship_work(self):
        result,calls=self.run_script(self.engine(),[
            decision(calls=lookup("秦宇 聂明升")),
            decision("answer",answer="第1章：聂明升境界较高。",citations=["c1p0"]),
        ])
        self.assertEqual(calls,2)
        self.assertEqual([row["tool"] for row in result["harness"]["steps"]],["search","read"])
        self.assertEqual(result["usage"]["api_requests"],2)
        self.assertEqual(result["harness"]["mode"],"dynamic")

    def test_plan_can_change_after_reading_and_old_answer_is_not_evidence(self):
        result,calls=self.run_script(self.engine("断臂者后来死了吗？"),[
            decision(calls=[dict(id="a",tool="read",arguments={"chapter_ids":[2]})]),
            decision(calls=[dict(id="b",tool="read",arguments={"chapter_ids":[4]})]),
            decision("answer",answer="目前只能确认断臂者仍能交谈。",citations=["c2p0"]),
        ])
        self.assertEqual(calls,3)
        self.assertEqual(len(result["harness"]["steps"]),2)

    def test_invalid_citation_is_rejected_and_replanned(self):
        result,calls=self.run_script(self.engine(),[
            decision(calls=lookup("秦宇")),
            decision("answer",answer="无据结论",citations=["c900p0"]),
            decision("answer",answer="根据第1章原文回答。",citations=["c1p0"]),
        ])
        self.assertEqual(calls,3)
        self.assertNotIn("无据",result["response"])
        self.assertIsNone(result["harness"]["learned_workflow"])

    def test_whole_book_coverage_cannot_silently_shrink(self):
        first=decision(calls=[dict(id="read",tool="read",arguments={"chapter_ids":[1]})])
        first["coverage"]="whole_book"
        last=decision("answer",answer="仅核实了第1章，全文尚未完成。",citations=["c1p0"])
        last["unresolved"]=["其余3章未覆盖"]
        result,calls=self.run_script(self.engine("请按全书统计"),[
            first,decision("answer",answer="全书均如此",citations=["c1p0"]),last])
        self.assertEqual(calls,3)
        self.assertEqual(result["harness"]["outcome"],"partial")
        self.assertIn("其余3章",result["response"])

    def test_call_budget_stops_before_another_paid_request(self):
        result,calls=self.run_script(self.engine(limits=Limits(max_calls=1)),[
            decision(calls=lookup("秦宇")),
        ])
        self.assertEqual(calls,1)
        self.assertEqual(result["harness"]["outcome"],"budget_exhausted")
        self.assertEqual(result["usage"]["api_requests"],1)

    def test_tools_and_source_cache_do_not_fake_full_coverage(self):
        harness=self.engine()
        harness.execute(dict(id="one",tool="read",arguments={"chapter_ids":[1]}))
        again=self.engine()
        again.execute(dict(id="two",tool="read",arguments={"chapter_ids":[1]}))
        self.assertTrue(again.state["steps"][0]["cache_hit"])
        self.assertIn(1,again.state["fully_read"])
        self.assertIn("c1p0",again.state["evidence"])
        self.assertNotIn(2,again.state["fully_read"])

    def test_recipe_learns_two_bindings_then_saves_the_planning_call(self):
        for name in ("秦宇","聂明升"):
            result,calls=self.run_script(self.engine("请解释"+name+"的境界"),[
                decision(calls=lookup(name)),
                decision("answer",answer="第1章的原文境界描述。",citations=["c1p0"]),
            ])
            self.assertEqual(calls,2)
        active=self.engine().store.list(active_only=True)
        self.assertEqual(len(active),1)
        result,calls=self.run_script(self.engine("请解释欧阳明的境界"),[
            decision("answer",answer="欧阳明六品（第3章）。",citations=["c3p0"]),
        ])
        self.assertEqual(calls,1)
        self.assertEqual(result["harness"]["mode"],"workflow")
        self.assertEqual([row["chapter_index"] for row in result["timeline"]],[3])
        # Changed intent must not inherit the learned steps merely due to a name.
        self.assertIsNone(self.engine().store.match("欧阳明的动机是什么",self.engine().context_key,self.engine().tools.names))

    def test_hardcoded_source_ids_cannot_become_parameterized_recipe(self):
        result,_=self.run_script(self.engine("请解释秦宇的境界"),[
            decision(calls=[dict(id="read",tool="read",arguments={"chapter_ids":[1]})]),
            decision("answer",answer="第1章：秦宇七品初期。",citations=["c1p0"]),
        ])
        self.assertIsNone(result["harness"]["learned_workflow"])

    def test_disabled_recipe_cannot_be_auto_promoted_again(self):
        store=self.engine().store
        steps=lookup("秦宇")
        info=store.learn("请解释秦宇的境界","ctx",self.engine().tools.names,steps,"targeted",[])
        store.feedback(info["id"],False)
        store.learn("请解释聂明升的境界","ctx",self.engine().tools.names,lookup("聂明升"),"targeted",[])
        self.assertEqual(store.list()[0]["status"],"disabled")

    def test_checkpoint_restores_read_evidence_without_repeating_tools(self):
        checkpoint=self.root/"checkpoint.json"
        with self.assertRaises(OSError):
            self.run_script(self.engine(checkpoint=checkpoint),[
                decision(calls=lookup("秦宇")),OSError("offline test disconnect")])
        result,calls=self.run_script(self.engine(checkpoint=checkpoint),[
            decision("answer",answer="依据已保存原文回答。",citations=["c1p0"])])
        self.assertEqual(calls,1)
        self.assertEqual(len(result["harness"]["steps"]),2)
        self.assertEqual(result["usage"]["api_requests"],3)
        self.assertFalse(result["usage"]["usage_complete"])

    def test_aggregate_is_deterministic_and_preserves_evidence(self):
        harness=self.engine()
        rows=[{"values":{"person":"甲","state":"dead"},"evidence_ids":["c1p0"]},
              {"values":{"person":"甲","state":"dead"},"evidence_ids":["c2p0"]},
              {"values":{"person":"乙","state":"alive"},"evidence_ids":["c4p0"]}]
        harness.state["observations"]["extracted"]={"records":rows}
        result=harness.tools.run("aggregate",{"records":[rows[:1],rows[1:]],"filters":{"state":"dead"},"unique_by":["person"]},harness.state)
        self.assertEqual(result["count"],1)
        self.assertEqual(result["evidence_ids"],["c1p0","c2p0"])

    def test_fabricated_aggregate_input_is_rejected(self):
        h=self.engine()
        with self.assertRaisesRegex(ToolError,"编造统计输入"):
            h.tools.run("aggregate",{"records":[{"values":{"person":"虚构宗师"},"evidence_ids":["c1p0"]}]},h.state)

    def test_new_explicit_scope_does_not_inherit_unrelated_previous_scope(self):
        plan=decision(calls=[dict(id="r",tool="read",arguments={"chapter_ids":[1]})])
        plan.update(coverage="range",required_chapter_ids=[1])
        final=decision("answer",answer="第1章原文。",citations=["c1p0"])
        final.update(coverage="range",required_chapter_ids=[1])
        result,_=self.run_script(self.engine("只分析第1章",scope={"start":2,"end":4}),[plan,final])
        self.assertEqual(result["harness"]["outcome"],"answered")
        self.assertEqual(result["scope"]["start"],1)

    def test_unknown_execution_tool_never_runs_generated_code(self):
        with self.assertRaisesRegex(ToolError,"未知工具"):
            self.engine().tools.run("exec",{"code":"raise Exception()"},self.engine().state)

    def test_model_can_choose_reusable_workflow_for_different_wording(self):
        for name in ("秦宇","聂明升"):
            self.run_script(self.engine("请解释"+name+"的境界"),[
                decision(calls=lookup(name)),decision("answer",answer="第1章原文。",citations=["c1p0"])])
        recipe=self.engine().store.list(active_only=True)[0]
        result,calls=self.run_script(self.engine("欧阳明是什么修为？",history=[{"role":"user","content":"之前讨论过其他话题"}]),[
            decision(calls=[dict(id="reuse",tool="workflow",arguments={"workflow_id":recipe["id"],"bindings":{"entity_0":"欧阳明"}})]),
            decision("answer",answer="第3章，欧阳明六品。",citations=["c3p0"]),
        ])
        self.assertEqual(calls,2)
        self.assertEqual(result["harness"]["mode"],"workflow")
        self.assertIn(3,{row["chapter_index"] for row in result["timeline"]})

    def test_extract_then_count_reuses_record_references(self):
        records=[{"values":{"person":"断臂者","state":"alive"},"evidence_ids":["c2p0"]}]
        result,calls=self.run_script(self.engine("断臂者死亡人数？"),[
            decision(calls=[dict(id="source",tool="read",arguments={"chapter_ids":[2]})]),
            decision(calls=[dict(id="rows",tool="extract",arguments={"passage_ids":"$source.passage_ids","fields":["person","state"],"task":"按原文判断最终结局"}),
                            dict(id="count",tool="aggregate",arguments={"records":"$rows.records","filters":{"state":"dead"},"unique_by":["person"]})]),
            {"records":records},
            decision("answer",answer="没有明确死亡者（第2章）。",citations=["c2p0"]),
        ])
        self.assertEqual(calls,4)
        self.assertIn("没有明确死亡",result["response"])

    def test_scanning_range_tracks_unmatched_chapters_without_claiming_full_read(self):
        h=self.engine()
        h.execute(dict(id="scan",tool="scan",arguments={"start":1,"end":4,"terms":["断臂"]}))
        self.assertEqual(set(h.state["scanned"]),{"1","2","3","4"})
        self.assertEqual(h.state["fully_read"],[2])
        self.assertIn("c2p0",h.state["evidence"])

    def test_paged_scan_cache_cannot_mark_unread_matching_passages_as_scanned(self):
        self.conn.execute("UPDATE chapters SET content=? WHERE chapter_index=1",("宗师"+"甲"*150+"\n宗师"+"乙"*150,))
        h=self.engine()
        first={"start":1,"end":1,"terms":["宗师"],"max_chars":200}
        h.execute(dict(id="first",tool="scan",arguments=first))
        self.assertNotIn("1",h.state["scanned"])
        h.execute(dict(id="last",tool="scan",arguments={**first,"cursor":1}))
        self.assertIn("1",h.state["scanned"])
        fresh=self.engine()
        fresh.execute(dict(id="last",tool="scan",arguments={**first,"cursor":1}))
        self.assertTrue(fresh.state["steps"][0]["cache_hit"])
        self.assertNotIn("1",fresh.state["scanned"])
        self.assertNotIn(1,fresh.state["fully_read"])

    def test_long_single_paragraph_is_read_in_stable_chunks(self):
        self.conn.execute("UPDATE chapters SET content=? WHERE chapter_index=1",("甲"*21000,))
        h=self.engine()
        cursor=0
        for number in range(10):
            h.execute(dict(id="page"+str(number),tool="read",arguments={"chapter_ids":[1],"cursor":cursor,"max_chars":5000}))
            cursor=h.state["observations"]["page"+str(number)]["next_cursor"]
            if cursor is None:
                break
        self.assertIn(1,h.state["fully_read"])
        self.assertEqual(sum(len(row["text"]) for row in h.state["evidence"].values()),21000)

    def test_currency_budget_reserves_before_the_request(self):
        result,calls=self.run_script(self.engine(limits=Limits(max_cost_usd=0.000001)),[])
        # Fake completion is entered, but request_started blocks the outbound call.
        self.assertEqual(calls,1)
        self.assertEqual(result["usage"]["api_requests"],0)
        self.assertEqual(result["harness"]["outcome"],"budget_exhausted")

    def test_known_source_ids_bind_recipe_to_book_revision(self):
        h=self.engine("解释本章")
        h.state["recipe_steps"]=[dict(id="a",tool="read",arguments={"chapter_ids":[1]})]
        info=h.store.learn("解释本章",h.context_key,[],h.state["recipe_steps"],"targeted",[])
        h.store.feedback(info["id"],True)
        newer=Harness(self.conn,"never-called",self.root,"book-v2","解释本章")
        self.assertIsNone(newer.store.match("解释本章",newer.context_key,newer.tools.names))
