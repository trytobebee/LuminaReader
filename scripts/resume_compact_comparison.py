"""Continue budget-stopped V2 jobs once, retaining initial failures and spend.

No V1 generation and no increased per-question limit. Run after fixing a
checkpoint/protocol issue; previously delivered answers are never rerun.
"""
import json
import time

from compare_reader_versions import DATA, REPORT, get, snapshot


def run():
    report=json.loads(REPORT.read_text())
    for row in report['cases']:
        original=row.get('v2') or {}
        if original.get('harness',{}).get('outcome')!='budget_exhausted' or row.get('recovery'):
            continue
        row['v2_initial_attempt']=original
        row['recovery']={'status':'running','policy':'resume existing checkpoint; same $0.50 cumulative cap; no V1 call',
            'reason':'exact input reservations and deterministic context metadata; source evidence retained'}
        report['status']='running';report['status_message']='沿用证据和原预算续跑：'+row['label'];snapshot(report)
        started=time.perf_counter()
        result=get('/chat',{'message':row['question'],'scope':row['scope'],'history':[],'document_id':'default'})
        while result.get('status')=='running':
            row['v2']={**result,'response':'沿用已保存证据，在剩余预算内完成回答…'};snapshot(report)
            time.sleep(5);result=get('/chat/jobs/'+result['job_id'])
        elapsed=time.perf_counter()-started
        row['v2']={**result,'elapsed_s':round((original.get('elapsed_s') or 0)+elapsed,3)}
        old_cost=(original.get('usage') or {}).get('known_cost_usd',0)
        new_cost=(result.get('usage') or {}).get('known_cost_usd',0)
        row['recovery'].update(status=result.get('status'),elapsed_s=round(elapsed,3),
            additional_known_cost_usd=round(new_cost-old_cost,6),
            elapsed_policy='sum of original and resumed execution including polling; excludes repair downtime')
        snapshot(report)
        print(json.dumps({'case':row['id'],'outcome':result.get('harness',{}).get('outcome'),
            'usage':result.get('usage'),'recovery':row['recovery']},ensure_ascii=False),flush=True)
        if result.get('status')!='completed':
            report.update(status='needs_attention',status_message='续跑未完成，已保留所有调用与证据。');snapshot(report)
            raise RuntimeError('V2 continuation did not complete')
    report.update(status='awaiting_source_review',status_message='V2回答已保留首次记录与续跑用量，正在逐项核对原文。')
    snapshot(report)


if __name__=='__main__':run()
