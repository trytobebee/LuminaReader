"""Generate one new V2.1 round; freeze historical V1 and V2 answers read-only."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from compare_reader_versions import DATA,DATABASE,REPORT,get,snapshot

ROUND='2026-11-25-v2.1'


def prepare():
    archive=DATA/'comparison-2026-11-19.json'
    if not archive.exists():archive.write_bytes(REPORT.read_bytes())
    current=json.loads(REPORT.read_text())
    if current.get('evaluation_round')==ROUND:return current
    previous=json.loads(archive.read_text())
    current=json.loads(archive.read_text())
    current.update(evaluation_round=ROUND,status='ready',status_message='V2.1离线验收通过，等待新一轮问答。',
        source_review=None,validation_notes=['V1与旧V2直接复用存档，只有新V2生成答案。',
        '复用原104章索引，无新增通读费用；新问答每题累计上限0.50美元。',
        '新增返京原文核对项，纠正上轮漏检；新旧按同一24项共同要点比较。'])
    current.pop('cache_verification',None)
    current['baseline_archive_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    for row,old in zip(current['cases'],previous['cases']):
        row['v2_previous']=old['v2'];row['v2']=None
        for field in ['recovery','v2_initial_attempt']:row.pop(field,None)
        for item in row['review']['items']:
            item['previous_v2_pass']=item['v2_pass'];item['v2_pass']=None
        row['review'].pop('common_score',None);row['review']['summary']='新一轮答案生成后，按冻结核对项重新检查。'
        if row['id']=='story_arc_cached':
            row['previous_scope']=row['scope']
            row['scope']={**row['scope'],'location':'小石寨104章，完整起止与转场以原文为准'}
            row['scope_correction']='起止1738–1841不变，旧location误写后续转场未纳入；1841实际已有赴京航班。'
            row['review']['items'].append({'criterion':'识别范围内已明确乘机赴京，不能称启程未覆盖',
                'v1_pass':False,'previous_v2_pass':False,'v2_pass':None,
                'note':'c1841p0、p1、p9明确长水至京城航班及起飞；上轮验收漏检，新增共同核对项。'})
            with sqlite3.connect(DATA/'compact-index.sqlite3') as db:
                for ref in ['c1841p0','c1841p1','c1841p9']:
                    source=db.execute('select ref,title,text from passages where ref=?',(ref,)).fetchone()
                    row['review']['sources'].append(dict(zip(['ref','title','text'],source)))
    snapshot(current)
    return current


def run():
    report=prepare()
    model=get('/model')
    if not model.get('harness','').startswith('reader-compact-harness-v2.1'):raise RuntimeError('Start the repaired V2 API before this paid comparison')
    for row in report['cases']:
        if row.get('v2',{} ) and row['v2'].get('status')=='completed':continue
        report.update(status='running',status_message='新V2正在回答：'+row['label']);snapshot(report)
        start=time.perf_counter()
        result=get('/chat',{'message':row['question'],'scope':row['scope'],'history':[],'document_id':'default'})
        while result.get('status')=='running':
            row['v2']={**result,'response':'正在使用修复后的证据与召回流程…'};snapshot(report)
            time.sleep(2);result=get('/chat/jobs/'+result['job_id'])
        row['v2']={**result,'elapsed_s':round(time.perf_counter()-start,3)};snapshot(report)
        print(json.dumps({'case':row['id'],'outcome':result.get('harness',{}).get('outcome'),'usage':result.get('usage')},ensure_ascii=False),flush=True)
        if result.get('status')!='completed':
            report.update(status='needs_attention',status_message='已保存本次异常与用量，停止后续调用。');snapshot(report)
            raise RuntimeError(result.get('error','V2.1 comparison failed'))
    report.update(status='awaiting_source_review',status_message='新V2五题已完成，正在按相同原文要点核对。');snapshot(report)


if __name__=='__main__':run()
