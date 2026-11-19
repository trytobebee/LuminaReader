"""Reuse historical V1 outputs; generate only V2 answers after publication.

This runner is deliberately separate from the preprocessing $10 ledger.
Every V2 answer retains the existing $0.50 default limit. No V1 POST is made.
"""
from datetime import datetime,timezone
import json
import re
import hashlib
import sqlite3
from pathlib import Path
import sys
import time
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.core.pipelines.novel.compact_index import connect,index_path
from src.core.pipelines.novel.reader_harness import write_json

DATA=ROOT/'data/version-comparison/v2'
REPORT=DATA/'comparison.json'
DATABASE=DATA/'book.db'
BENCH=ROOT/'data/benchmarks/2026-11-19'
API='http://127.0.0.1:5002/api'
LABELS={'injury':'神女出手：伤、死与未知','temple':'祠堂事件：名单与幸存者','rank':'修炼境界与战斗胜负','full_census':'104章：完整伤亡与境界台账'}


def get(path,payload=None):
    raw=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    request=Request(API+path,data=raw,headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=20) as response:return json.load(response)


def baseline_rows():
    frozen=json.loads((BENCH/'cases.json').read_text())
    result=[]
    for case in frozen:
        if case['id'] not in LABELS:continue
        p=BENCH/(case['id']+'__gpt.json');old=json.loads(p.read_text())
        if old.get('status')!='completed' or old['input_sha256']!=case['input_sha256']:
            raise ValueError('Historical baseline is incomplete or its frozen input differs')
        original=json.loads(case['messages'][-1]['content'])
        ids=sorted({int(re.match(r'c(\d+)',key)[1]) for key in case['passages']})
        scope={'start':1738,'end':1841,'location':'小石寨104章完整试点'} if case['id']=='full_census' else {'start':min(ids),'end':max(ids),'location':'原有问题的证据时间范围'}
        result.append({'id':case['id'],'label':LABELS[case['id']],'question':original['question'],
            'scope':scope,
            'comparison_scope':f"V1为已保存的固定证据回答，输入来自{len(ids)}章、{len(case['passages'])}段；V2从104章索引自主检索。模型均为gpt-6.1-sol / medium。不是相同召回输入的单轮模型实验。",
            'baseline_protocol':'historical_fixed_evidence_generation',
            'source_path':str(p.relative_to(ROOT)), 'input_sha256':case['input_sha256'],
            'v1':{'status':'historical','model':'gpt-6.1-sol · medium','response':old.get('answer',''),
                'usage':old.get('usage'),'elapsed_s':old.get('elapsed_s'),'new_api_calls':0,'structured':old.get('parsed'),
                'coverage':f'历史输入覆盖{len(ids)}章、{len(case["passages"])}段。'},
            'v2':None,'review':None})
    # This exact historic input was recovered by reproducing the original
    # cache fingerprint, not by guessing a question from its answer.
    recovered=DATA.parent/'v1-snapshot/recovered-inputs.json'
    for item in json.loads(recovered.read_text()) if recovered.exists() else []:
        question=item['question'];scope=item['source_scope']
        original_db=ROOT/'data/reader_novel.db'
        with sqlite3.connect('file:'+str(original_db)+'?mode=ro',uri=True) as conn:
            rows=conn.execute('SELECT chapter_index,title,content,summary,key_events FROM chapters WHERE chapter_index BETWEEN ? AND ? ORDER BY chapter_index',(scope['start'],scope['end'])).fetchall()
        fingerprint=hashlib.sha256(json.dumps([item['cache_version'],item['model_identity'],item['original_database_path'],question,item['history'],rows],ensure_ascii=False).encode()).hexdigest()[:32]
        if fingerprint!=item['cache_key']:raise ValueError('Recovered historic input does not reproduce its original cache fingerprint')
        old_path=ROOT/'data/reader_qa'/(fingerprint+'.json')
        if not old_path.exists():raise FileNotFoundError('Recovered historical answer is missing')
        old=json.loads(old_path.read_text())
        result.append({'id':item['id'],'label':item['label'],'question':question,
            'scope':{'start':1738,'end':1841,'location':'小石寨104章试点；更早进入和后续转场未纳入预处理'},
            'comparison_scope':'V1来自真实旧聊天，完整问题与空历史已通过原缓存指纹复核。V1读取1735–1844共110章，V2索引1738–1841共104章；范围差异单列，不把缺少的首尾内容当成同条件准确率比较。',
            'baseline_protocol':'historical_interactive_story_arc_v5','source_path':str(old_path.relative_to(ROOT)),
            'input_sha256':fingerprint,'v1':{'status':'historical','model':'gpt-6.1-sol · medium','response':old['response'],
                'usage':old.get('usage'),'elapsed_s':None,'new_api_calls':0,'timeline':old.get('timeline',[]),
                'coverage':old.get('coverage','历史流程覆盖110章。')},'v2':None,'review':None})
    return result


def snapshot(report):
    report['updated_at']=datetime.now(timezone.utc).isoformat()
    if index_path(DATABASE).exists():
        with connect(index_path(DATABASE)) as db:
            row=db.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
        manifest=json.loads(row[0]) if row else {}
        ready=manifest.get('status')=='published' and manifest.get('global_map_status')=='published'
        prep={'completed_chapters':len(manifest.get('processed_chapters',[])),'status':'published' if ready else 'building','global_map_status':manifest.get('global_map_status','pending'),'budget_usd':10}
        paths=list((DATA/'preprocessing').rglob('budget.json'))
        if paths:
            budget=json.loads(max(paths,key=lambda p:p.stat().st_mtime).read_text())
            prep.update(usage=budget.get('usage'),settled_usd=budget.get('settled_usd'),
                        reserved_usd=sum(x['amount'] for x in budget.get('reservations',[])),
                        token_count_requests=budget.get('token_count_requests',0))
            operation=(budget.get('reservations') or [{}])[-1].get('operation','')
            prep['phase']=('跨阶段故事线与远处线索连接' if operation.startswith('global-map') else
                           '人物境界与最终状态核对' if operation.startswith('outcomes') else
                           '跨章事件与剧情阶段连接' if operation.startswith('stitch') else '逐章通读提取')
        report['preprocessing']=prep
    v2=[r['v2'] for r in report['cases'] if r.get('v2') and r['v2'].get('usage')]
    complete=all(r['usage'].get('estimated_cost_usd') is not None and r['usage'].get('usage_complete',False) for r in v2)
    known=round(sum((r.get('usage') or {}).get('known_cost_usd',(r.get('usage') or {}).get('estimated_cost_usd')) or 0 for r in v2),6)
    report['totals']={'v1_new_api_requests':0,'v1_historical_cost_usd':round(sum((r['v1'].get('usage') or {}).get('estimated_cost_usd',0) or 0 for r in report['cases']),6),
        'v2_cost_usd':known if complete else None,'v2_known_cost_usd':known,'v2_usage_complete':complete,
        'v2_api_requests':sum((r.get('usage') or {}).get('api_requests',0) for r in v2),
        'qa_cost_limit_per_question_usd':.5,'preprocessing_accounted_separately':True}
    write_json(REPORT,report)


def run():
    DATA.mkdir(parents=True,exist_ok=True)
    report=json.loads(REPORT.read_text()) if REPORT.exists() else {'status':'preprocessing','cases':baseline_rows(),
        'policy':{'no_v1_generation':True,'preprocessing_limit_usd':10,'qa_limit_per_question_usd':.5,
                  'quality_assessment':'pending source-by-source review, never a model self-score'}}
    deadline=time.monotonic()+7200
    while True:
        snapshot(report)
        if report.get('preprocessing',{}).get('status')=='published':break
        if time.monotonic()>deadline:raise TimeoutError('Preprocessing did not publish within two hours')
        prep=report.get('preprocessing',{})
        report['status_message']=f"{prep.get('phase','正在预处理')}：通读{prep.get('completed_chapters',0)} / 104章；V1未重新调用。"
        snapshot(report);time.sleep(15)
    for row in report['cases']:
        if row.get('v2',{} ) and row['v2'].get('status')=='completed':continue
        report['status']='running';report['status_message']='V2正在回答：'+row['label'];snapshot(report)
        start=time.perf_counter()
        result=get('/chat',{'message':row['question'],'scope':row['scope'],'history':[],'document_id':'default'})
        while result.get('status')=='running':
            row['v2']={'status':'running','usage':result.get('usage'),'response':'正在检索并核对原文…'};snapshot(report)
            time.sleep(5);result=get('/chat/jobs/'+result['job_id'])
        row['v2']={**result,'elapsed_s':round(time.perf_counter()-start,3)}
        if result.get('status')!='completed':
            report['status']='needs_attention';report['status_message']='V2问答未完成，已保留进度与实际用量。';snapshot(report)
            raise RuntimeError(result.get('error',result))
        snapshot(report)
        print(json.dumps({'case':row['id'],'usage':result.get('usage'),'outcome':result.get('harness',{}).get('outcome')}),flush=True)
    report['status']='awaiting_source_review';report['status_message']='V2回答完成；正在进行原文准确性核对。';snapshot(report)


if __name__=='__main__':run()
