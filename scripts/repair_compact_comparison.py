"""Explicit targeted repair round; all attempts share the original question cap."""
import argparse
import json
import time

from compare_reader_versions import REPORT,get,snapshot


def combine(old,new):
    result=dict(new)
    for key in ('api_requests','input_tokens','output_tokens','total_tokens','cached_input_tokens',
                'cache_write_input_tokens','reasoning_tokens','reported_requests','unpriced_requests'):
        result[key]=old.get(key,0)+new.get(key,0)
    result['known_cost_usd']=round(old['known_cost_usd']+new['known_cost_usd'],6)
    result['estimated_cost_usd']=result['known_cost_usd'] if old.get('estimated_cost_usd') is not None and new.get('estimated_cost_usd') is not None else None
    result['usage_complete']=old.get('usage_complete',False) and new.get('usage_complete',False)
    return result


def run(ids,resume=False):
    report=json.loads(REPORT.read_text())
    assert get('/model')['harness']=='reader-compact-harness-v2.1.1'
    for row in report['cases']:
        if row['id'] not in ids or (row.get('repair') and not resume):continue
        if resume and (row.get('v2') or {}).get('harness',{}).get('outcome')!='budget_exhausted':continue
        old=row.get('v2_initial_attempt',row['v2']);usage=old['usage']
        if not usage.get('usage_complete'):raise RuntimeError('Unknown spend must be reserved before a repair')
        remaining=.5-usage['known_cost_usd']
        if remaining<=0:raise RuntimeError('Question budget exhausted; no new generation')
        prior_elapsed=(row.get('repair') or {}).get('elapsed_s',0)
        if resume:row['repair'].setdefault('stopped_attempts',[]).append(row['v2'])
        row['v2_initial_attempt']=old
        row['repair']={**row.get('repair',{}),'reason':'scope-aware cache keys, entity-state closure and bounded input projection',
            'remaining_budget_usd':remaining,'status':'running'}
        report.update(status='running',status_message='在原题剩余预算内修复：'+row['label']);snapshot(report)
        start=time.perf_counter()
        result=get('/chat',{'message':row['question'],'scope':row['scope'],'history':[],'document_id':'default','max_cost_usd':remaining})
        while result.get('status')=='running':
            row['v2']={**result,'usage':combine(usage,result.get('usage') or {'known_cost_usd':0}),'response':'使用原题剩余预算补全…'};snapshot(report)
            time.sleep(2);result=get('/chat/jobs/'+result['job_id'])
        extra=result.get('usage') or {'known_cost_usd':0}
        row['repair'].update(status=result.get('status'),additional_usage=extra,elapsed_s=round(prior_elapsed+time.perf_counter()-start,3))
        row['v2']={**result,'usage':combine(usage,extra),'elapsed_s':round((old.get('elapsed_s') or 0)+row['repair']['elapsed_s'],3)}
        assert row['v2']['usage']['known_cost_usd']<=.500001
        snapshot(report)
        print(json.dumps({'case':row['id'],'outcome':result.get('harness',{}).get('outcome'),'combined_usage':row['v2']['usage']},ensure_ascii=False),flush=True)
        if result.get('status')!='completed':raise RuntimeError('Repair failed; no further automatic retry')
    report.update(status='awaiting_source_review',status_message='答案已保留全部首次及补跑费用，正在核对原文。');snapshot(report)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cases',nargs='+',required=True)
    parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    run(args.cases,args.resume)
