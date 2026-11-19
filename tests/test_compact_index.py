import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from src.core.pipelines.novel import compact_index as m
from src.core.pipelines.novel.usage import UsageLimitError, request_started


class CompactIndexTest(TestCase):
    def test_complexity_gives_more_budget_to_long_eventful_source(self):
        short=m.complexity({'content':'村民散去。'*80})
        busy=m.complexity({'content':('然而宗师元神突破，身份原来另有真相。“你是谁？”'*120)})
        self.assertGreater(busy['visible_token_budget'],short['visible_token_budget'])
        self.assertLessEqual(busy['visible_token_budget'],2600)

    def test_required_fields_cannot_be_dropped_and_unknown_refs_fail(self):
        row={'chapter_index':1738,'title':'测试','content':'原文。'}
        base={'chapter_index':1738,'summary':'摘要','characters':[], 'events':[], 'states':[], 'clues':[], 'phase':{},'unresolved':[]}
        self.assertEqual(len(m.validate_chapters({'chapters':[base]},[row],m.passages([row]))),1)
        with self.assertRaises(ValueError):m.validate_chapters({'chapters':[{k:v for k,v in base.items() if k!='clues'}]},[row],m.passages([row]))
        bad={**base,'states':[{'entity':'甲','refs':['c2000p0']}]}
        with self.assertRaises(ValueError):m.validate_chapters({'chapters':[bad]},[row],m.passages([row]))

    def test_physical_retries_and_unknown_requests_reserve_shared_cap(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m,'model_identity',return_value=['openai','gpt-6.1-sol','medium']):
            job=m.PaidJob(directory,str(Path(directory)/'book.db'),cap=.1)
            job.next_cost=.06; job.label='first-attempt';job.reserve()
            with self.assertRaises(UsageLimitError):job.reserve()
            # Unknown completion remains reserved after restarting the worker.
            resumed=m.PaidJob(directory,str(Path(directory)/'book.db'),cap=.1)
            resumed.next_cost=.06
            with self.assertRaises(UsageLimitError):resumed.reserve()
            with self.assertRaises(ValueError):m.PaidJob(directory,str(Path(directory)/'book.db'),cap=.2)

    def test_local_state_can_retain_earlier_cross_chapter_evidence(self):
        rows=[{'chapter_index':1738,'title':'先前','content':'甲修为七品。'}, {'chapter_index':1739,'title':'当前','content':'甲继续前行。'}]
        item={'chapter_index':1739,'summary':'甲前行','characters':[], 'events':[], 'clues':[], 'phase':{},'unresolved':[],
              'states':[{'entity':'甲','property':'rank','value':'七品','refs':['c1738p0','c1739p0']}]}
        self.assertEqual(len(m.validate_chapters({'chapters':[item]},[rows[1]],m.passages(rows))),1)

    def test_returned_usage_settles_only_latest_attempt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m,'model_identity',return_value=['openai','gpt-6.1-sol','medium']):
            job=m.PaidJob(directory,str(Path(directory)/'book.db'),cap=1)
            job.next_cost=.1;job.reserve();job.reserve()
            job.meter.record('gpt-6.1-sol',{'input_tokens':1000,'output_tokens':100})
            self.assertEqual(job.data['reservations'][0]['amount'],.1)
            self.assertEqual(job.data['reservations'][1]['amount'],0)
            self.assertAlmostEqual(job.data['settled_usd'],.003)

    def test_confirmed_death_does_not_require_a_known_cultivation_rank(self):
        item={'count':1,'final_state':'confirmed_dead','rank':'未知','rank_refs':[],
              'final_refs':['c1738p1'],'identity_refs':['c1738p0']}
        self.assertFalse(m.validate_outcome(item)['rank_evidence_available'])
        with self.assertRaises(ValueError):m.validate_outcome({**item,'final_refs':[]})

    def test_exact_count_can_reduce_overconservative_hold_without_raising_cap(self):
        def generate(*args,**kwargs):request_started();return '{}'
        with tempfile.TemporaryDirectory() as directory, patch.object(m,'model_identity',return_value=['openai','gpt-6.1-sol','medium']), patch.object(m,'count_openai_input',return_value=100) as count, patch.object(m,'completion',side_effect=generate):
            job=m.PaidJob(directory,str(Path(directory)/'book.db'),cap=.1)
            job.next_cost=.08;job.label='missing-receipt';job.reserve()
            job.call('large-input','正文',{'text':'重复'*20000},100)
            self.assertEqual(count.call_count,1)
            self.assertLess(job.next_cost,.1)
            self.assertEqual(job.data['cap_usd'],.1)
            self.assertEqual(job.data['token_count_requests'],1)
            self.assertEqual(job.data['reservations'][0]['amount'],.08)
            self.assertEqual(job.meter.summary()['api_requests'],1)
            # The count endpoint does not make unknown prior spend disappear.
            with self.assertRaises(UsageLimitError):job.call('still-too-large','正文',{'text':'重复'*20000},2000)
            self.assertEqual(job.meter.summary()['api_requests'],1)
