import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile
from unittest import TestCase
from unittest.mock import patch

from src.core.pipelines.novel import compact_index as index
from src.core.pipelines.novel.compact_harness import CompactHarness, CompactTools
from src.core.pipelines.novel import compact_runtime as runtime
from src.core.pipelines.novel.reader_tools import ToolError
from src.core.pipelines.novel.usage import request_started,record_usage,UsageLimitError


class CompactHarnessTest(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/'book.db'
        self.conn=sqlite3.connect(self.path);self.conn.row_factory=sqlite3.Row;self.addCleanup(self.conn.close)
        self.conn.executescript('CREATE TABLE chapters(chapter_index INTEGER,title TEXT,content TEXT,summary TEXT,key_events TEXT);CREATE TABLE chapter_people(chapter_index INTEGER,name TEXT,aliases TEXT);')
        self.conn.executemany('INSERT INTO chapters VALUES (?,?,?,?,?)',[(1738,'境界','秦宇七品初期。聂明升接近七品后期。',None,'[]'),(1739,'幸存','念安获救，活了下来。',None,'[]')])
        self.conn.execute('INSERT INTO chapter_people VALUES (1738,\'秦宇\',\'[]\')');self.conn.commit()
        path=index.index_path(self.path);index.initialize(path)
        revision=hashlib.sha256(json.dumps(index.source_rows(self.path,1738,1739),ensure_ascii=False).encode()).hexdigest()
        meta={'status':'published','start':1738,'end':1739,'processed_chapters':[1738,1739],'protocol':'test-protocol','source_revision':revision}
        with index.connect(path) as db:
            db.execute('INSERT INTO meta VALUES (?,?)',('manifest',json.dumps(meta)))
            for ref,p in index.passages(index.source_rows(self.path,1738,1739)).items():db.execute('INSERT INTO passages VALUES (?,?,?,?,?)',(ref,p['chapter_index'],p['title'],p['text'],'hash'))
            index.put_record(db,'c1738_state1',1738,'state',{'entity':'秦宇','property':'rank','value':'七品初期','refs':['c1738p0']})
            index.put_record(db,'outcome_1',1738,'outcome',{'identity_key':'念安','name':'念安','final_state':'alive','count':1,'final_refs':['c1739p0'],'unresolved':[]})
            index.put_record(db,'outcome_coverage',1738,'coverage',{'unresolved':['某人身份尚不明确']})

    def engine(self):return CompactHarness(self.conn,'',self.root/'jobs','source-and-index','秦宇是什么境界？')

    def test_compact_capsules_allow_one_grounded_decision_without_full_read(self):
        engine=self.engine()
        def model(*args,**kwargs):
            request_started();record_usage('gpt-6.1-sol',{'input_tokens':100,'output_tokens':30})
            return json.dumps({'goal':'核对境界','plan':['查看证据包'],'coverage':'targeted','required_chapter_ids':[],
                'context_dependencies':[],'action':'answer','answer':'秦宇七品初期。','citations':['c1738p0'],'unresolved':[]})
        with patch('src.core.pipelines.novel.reader_harness.completion',side_effect=model) as paid:
            result=engine.run()
        self.assertEqual(paid.call_count,1)
        self.assertEqual(result['harness']['version'],index.VERSION)
        self.assertEqual(result['harness']['reading_depth'],'compact_evidence')
        self.assertEqual(engine.state['fully_read'],[])

    def test_tool_cache_rehydrates_source_evidence_in_new_run(self):
        first=self.engine();second=self.engine()
        self.assertIn('c1738p0',second.state['evidence'])
        self.assertTrue(second.state['steps'][1]['cache_hit'])

    def test_changed_source_cannot_be_used_with_stale_capsule(self):
        tool=CompactTools(self.conn,None,index.index_path(self.path))
        self.conn.execute("UPDATE chapters SET content='修改后的原文。' WHERE chapter_index=1738");self.conn.commit()
        with self.assertRaises(ToolError):tool.tool_get_evidence({'refs':['c1738p0']},{})

    def test_inherited_catalog_and_search_do_not_use_legacy_summaries(self):
        self.conn.execute("UPDATE chapters SET summary='旧版错误提示',key_events='旧版错误提示' WHERE chapter_index=1738")
        self.conn.commit()
        with index.connect(index.index_path(self.path)) as db:
            db.execute('INSERT INTO chapters VALUES (?,?)',(1738,json.dumps({'summary':'新索引境界核对','events':[]})))
        tool=CompactTools(self.conn,None,index.index_path(self.path))
        self.assertEqual(tool.tool_catalog({}, {})['chapters'][0]['summary_hint'],'新索引境界核对')
        self.assertEqual(tool.tool_search({'query':'旧版错误提示','mode':'index'}, {})['chapter_ids'],[])

    def test_complete_census_rejects_missing_or_undisclosed_coverage(self):
        engine=self.engine();engine.state['evidence']['c1739p0']={'chapter_index':1739,'title':'幸存','text':'念安获救，活了下来。'}
        decision={'action':'answer','answer':'确认名单','citations':['c1739p0'],'evidence_requirements':['complete_census'],'unresolved':[]}
        with self.assertRaises(ToolError):engine.validate_final(decision)
        engine.state['observations']['outcomes']=engine.tools.tool_get_outcomes({},engine.state)
        with self.assertRaises(ToolError):engine.validate_final(decision)
        self.assertEqual(engine.validate_final({**decision,'unresolved':['某人身份待核，当前只列已确认名单']}),['c1739p0'])

    def test_typed_computation_uses_all_requested_records_and_validates_values(self):
        tool=CompactTools(self.conn,None,index.index_path(self.path))
        result=tool.tool_typed_compute({'record_ids':['outcome_1'],'filters':{'final_state':'alive'},'unique_by':'identity_key','operator':'sum','field':'count'}, {})
        self.assertEqual(result['value'],1)
        with self.assertRaises(ToolError):tool.tool_typed_compute({'record_ids':['outcome_1'],'operator':'sum','field':'name'}, {})
        with self.assertRaises(ToolError):tool.tool_typed_compute({'record_ids':['outcome_1'],'operator':'eval'}, {})

    def test_source_based_gap_resolution_can_complete_answer_after_retrieval(self):
        engine=self.engine();engine.state['observations']['outcomes']=engine.tools.tool_get_outcomes({},engine.state)
        decision={'action':'answer','answer':'根据原文给出已裁定结论。','citations':['c1738p0'],'evidence_requirements':['complete_census'],'unresolved':[],
                  'gap_resolutions':[{'gap_id':'census_gap_0','conclusion':'原文有明确身份依据','citations':['c1738p0']}]}
        self.assertEqual(engine.validate_final(decision),['c1738p0'])
        decision['gap_resolutions'][0]['citations']=['c1739p99']
        with self.assertRaises(ToolError):engine.validate_final(decision)

    def test_unrelated_global_gap_does_not_block_a_local_event(self):
        engine=self.engine();engine.context['scope']={'start':1738,'end':1738}
        engine.state['observations']['outcomes']=engine.tools.tool_get_outcomes({},engine.state)
        self.assertEqual(engine.validate_final({'action':'answer','answer':'本场已确认的结论。','citations':['c1738p0'],
                    'evidence_requirements':['complete_census'],'unresolved':[]}),['c1738p0'])

    def test_gap_resolution_sources_are_included_in_final_citations(self):
        engine=self.engine()
        engine.state['observations']['outcomes']=engine.tools.tool_get_outcomes({},engine.state)
        engine.execute({'id':'survival_source','tool':'get_evidence','arguments':{'refs':['c1739p0']}})
        decision={'action':'answer','answer':'幸存者不计死亡。','citations':['c1738p0'],
                  'evidence_requirements':['complete_census'],'unresolved':[],
                  'gap_resolutions':[{'gap_id':'census_gap_0','conclusion':'原文明确获救', 'citations':['c1739p0']}]}
        self.assertEqual(engine.validate_final(decision),['c1738p0','c1739p0'])

    def test_global_map_publication_is_resumable_without_regenerating(self):
        value={'arcs':[{'label':'试点故事线','phase_ids':[],'event_ids':[],'chapter_ids':[1738,1739],
                        'summary':'有出处的导航','refs':['c1738p0'],'status':'open'}],
               'links':[],'threads':[],'unresolved':[]}
        with patch.object(index,'PaidJob') as factory:
            job=factory.return_value;job.data={};job.call.return_value=value
            job.meter.summary.return_value={'api_requests':1,'estimated_cost_usd':.01}
            first=index.finalize_map(self.path)
            second=index.finalize_map(self.path)
        self.assertEqual(first['global_map_status'],'published')
        self.assertEqual(second['global_map_status'],'published')
        self.assertEqual(job.call.call_count,1)

    def test_global_map_rejects_unavailable_sources_before_publication(self):
        with patch.object(index,'PaidJob') as factory:
            factory.return_value.call.return_value={'arcs':[{'refs':['c9999p0'],'phase_ids':[],'event_ids':[]}], 'links':[],'threads':[]}
            with self.assertRaises(ValueError):index.finalize_map(self.path)
        with index.connect(index.index_path(self.path)) as db:
            manifest=json.loads(db.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()[0])
            self.assertNotIn('global_map_status',manifest)

    def test_context_dependency_metadata_is_derived_without_paid_repair(self):
        engine=self.engine();engine.context['scope']={'start':1738,'end':1739}
        with patch.object(engine,'invoke',return_value={'context_dependencies':['unrecognized_metadata']}):
            self.assertEqual(engine.decide()['context_dependencies'],['scope'])

    def test_exact_input_count_preserves_question_cap_and_prior_reservations(self):
        engine=self.engine();engine.settled_cost=.42;engine.next_reservation=.2
        engine._reservation_messages=[{'role':'user','content':'test'}]
        with patch('src.core.pipelines.novel.compact_harness.model_identity',return_value=['openai','gpt-6.1-sol','medium']),patch('src.core.pipelines.novel.compact_harness.count_openai_input',return_value=100) as count:
            engine.reserve_request()
            self.assertLess(engine.settled_cost+sum(engine.reservations),.5)
            with self.assertRaises(UsageLimitError):engine.reserve_request()
            self.assertEqual(count.call_count,1)
            self.assertEqual(len(engine.reservations),1)

    def test_grounded_partial_answer_replays_without_another_model_job(self):
        identity=['openai','gpt-6.1-sol','medium'];question='原文是否确认？'
        key=runtime.digest([index.VERSION,identity,str(self.path.resolve()),'revision',question,[],None,None])[:32]
        target=runtime.directory(self.path);target.mkdir(parents=True)
        (target/(key+'.json')).write_text(json.dumps({'status':'completed','response':'原文尚不明确。','harness':{'outcome':'partial'},
            'usage':{'api_requests':2,'estimated_cost_usd':.02,'usage_complete':True}}))
        with patch.object(runtime,'revision',return_value='revision'),patch.object(runtime,'model_identity',return_value=identity),patch.object(runtime.POOL,'submit') as submit:
            result=runtime.start(self.path,'',question)
        submit.assert_not_called()
        self.assertTrue(result['cache_hit'])
        self.assertEqual(result['usage']['api_requests'],0)
        self.assertEqual(result['generation_usage']['estimated_cost_usd'],.02)

    def test_older_original_evidence_survives_the_observation_window(self):
        engine=self.engine()
        engine.state['evidence']['c1739p0']={'chapter_index':1739,'title':'幸存','text':'念安获救，活了下来。'}
        engine.state['observations']={'old_sources':{'text':'旧观察'*20000},'ledger1':{'all_records':True},'ledger2':{'all_records':True}}
        view=engine.view()
        self.assertIn('c1738p0',view['evidence_memory'])
        self.assertIn('c1739p0',view['evidence_memory'])
        self.assertEqual(view['evidence_chapter_ids'],[1738,1739])
        self.assertEqual(sum(v.get('all_records',False) for v in view['observations'].values()),1)
