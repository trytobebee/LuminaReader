import json
import pathlib
import sqlite3
import tempfile
from unittest import TestCase,skipUnless
from unittest.mock import patch

from src.core.pipelines.novel.compact_context import intersects, observation, memory
from src.core.pipelines.novel.compact_harness import CompactHarness
from src.core.pipelines.novel.reader_tools import ToolError


class ProjectionTest(TestCase):
    def test_aggregate_range_uses_provenance_instead_of_anchor(self):
        record={'chapter_index':1,'refs':['c9p0'],'chapter_ids':[8,9]}
        self.assertTrue(intersects(record,{'start':8,'end':10}))
        self.assertFalse(intersects(record,{'start':2,'end':7}))

    def test_large_read_is_projected_before_size_filter(self):
        original={'passages':{f'c9p{i}':{'chapter_index':9,'text':'原文'*100} for i in range(500)},'passage_ids':[f'c9p{i}' for i in range(500)]}
        result=observation(original)
        self.assertNotIn('passages',result)
        self.assertEqual(len(result['passage_ids']),500)
        self.assertLess(len(json.dumps(result)),10000)

    def test_memory_keeps_chapter_coverage_and_priority_closure(self):
        evidence={f'c{chapter}p{n}':{'chapter_index':chapter,'text':'正文'*30} for chapter in range(1,6) for n in range(100)}
        result=memory(evidence,'正文',1000,['c5p99'])
        self.assertEqual({p['chapter_index'] for p in result.values()},set(range(1,6)))
        self.assertIn('c5p99',result)


ROOT=pathlib.Path(__file__).resolve().parents[1]
PILOT=ROOT/'data/version-comparison/v2'


@skipUnless((PILOT/'compact-index.sqlite3').exists(),'private pilot fixtures unavailable')
class SavedPilotReplayTest(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.conn=sqlite3.connect('file:'+str(PILOT/'book.db')+'?mode=ro',uri=True)
        self.conn.row_factory=sqlite3.Row;self.addCleanup(self.conn.close)
        self.report=json.loads((PILOT/'comparison.json').read_text())

    def engine(self,case_id):
        row=next(r for r in self.report['cases'] if r['id']==case_id)
        return CompactHarness(self.conn,'',pathlib.Path(self.temp.name),'saved-source',row['question'],scope=row['scope'])

    def test_boundary_and_graph_are_ready_without_a_planning_request(self):
        with patch('src.core.pipelines.novel.reader_harness.completion',side_effect=AssertionError('offline replay must not call model')):
            engine=self.engine('story_arc_cached');view=engine.view()
        self.assertIn('c1841p0',view['evidence_memory'])
        self.assertIn('c1841p1',view['evidence_memory'])
        self.assertEqual(len(engine.state['observations']['boot_context']['arcs']),11)
        self.assertTrue(any(s['tool']=='get_records' for s in engine.state['steps']))
        self.assertLess(len(json.dumps(view,ensure_ascii=False)),50000)

    def test_canonical_final_states_and_raw_proof_are_retained(self):
        engine=self.engine('full_census')
        engine.execute({'id':'ledger','tool':'get_outcomes','arguments':{}})
        view=engine.view();by_name={r.get('name'):r for r in view['known_facts']}
        self.assertEqual(by_name['廖沧']['final_state'],'historical_dead')
        self.assertEqual(by_name['明前寺老和尚']['final_state'],'alive')
        self.assertIn('c1752p37',view['evidence_memory'])
        self.assertIn('c1777p46',view['evidence_memory'])
        self.assertNotIn('records',view['observations']['ledger'])
        self.assertEqual(len(view['known_facts']),40)
        self.assertLess(len(json.dumps(view,ensure_ascii=False,separators=(',',':'))),50000)
        with self.assertRaises(ToolError):engine.validate_final({'action':'answer','answer':'未知','citations':['c1752p37'],
            'state_claims':[{'record_id':by_name['廖沧']['id'],'final_state':'unknown'}]})

    def test_partial_disclaimer_cannot_hide_available_ending(self):
        engine=self.engine('story_arc_cached');engine.state['coverage_plan']={'must_include_end':True}
        early=next(ref for ref in engine.state['evidence'] if ref.startswith('c1738'))
        with self.assertRaises(ToolError):engine.validate_final({'action':'answer','answer':'只有前半段','citations':[early],'unresolved':['结尾材料不足']})

    def test_scoped_ledger_cache_cannot_leak_into_whole_range(self):
        temple=self.engine('temple');temple.execute({'id':'ledger','tool':'get_outcomes','arguments':{}})
        full=self.engine('full_census');full.execute({'id':'ledger','tool':'get_outcomes','arguments':{}})
        self.assertLess(len(temple.state['observations']['ledger']['records']),40)
        self.assertEqual(len(full.state['observations']['ledger']['records']),40)
        self.assertFalse(next(s for s in full.state['steps'] if s['id']=='ledger')['cache_hit'])

    def test_rank_retrieval_closes_later_entity_states(self):
        engine=self.engine('rank');view=engine.view()
        self.assertIn('c1753p1',view['evidence_memory'])
        self.assertIn('终生无妄八品',view['evidence_memory']['c1753p1']['text'])
