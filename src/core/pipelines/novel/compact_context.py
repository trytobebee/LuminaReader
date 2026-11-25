"""Deterministic projections and provenance selection; no model calls."""
import math
import re
from collections import Counter

from .compact_index import all_refs
from .qa import terms

TEXT_FIELDS=('name','entity','label','summary','action','result','condition','rank','reason','description','value')


def chapter_of(ref):
    found=re.match(r'c(\d+)p',ref)
    return int(found[1]) if found else None


def chapters(record):
    ids=set(record.get('chapter_ids',[]))
    ids.update(chapter_of(ref) for ref in all_refs(record))
    ids.discard(None)
    return ids or {record['chapter_index']}


def intersects(record,scope):
    return not scope or any(scope['start']<=chapter<=scope['end'] for chapter in chapters(record))


def card(record):
    keys={'id','kind','chapter_index','name','entity','property','value','label','summary','action','result',
          'epistemic','story_time','participants','refs','status','chapter_ids','event_ids','phase_ids',
          'identity_key','record_type','count','rank','rank_refs','final_state','final_refs','identity_refs',
          'event_refs','rank_evidence_available'}
    result={key:value for key,value in record.items() if key in keys}
    if record.get('reason'):result['reason']=record['reason'][:150]
    if record.get('unresolved'):result['unresolved']=record['unresolved']
    return result


def rank_records(records,query,names,scope=None):
    candidates=[r for r in records if intersects(r,scope)]
    texts={r['id']:' '.join(str(r.get(k,'')) for k in TEXT_FIELDS)+' '+ ' '.join(r.get('participants',[])) for r in candidates}
    words={key:terms(text) for key,text in texts.items()}
    frequencies=Counter(word for tokens in words.values() for word in tokens)
    query_words=terms(query)
    anchors=sorted((name for name in names if 2<=len(name)<=16 and name in query),key=lambda n:(-len(n),n))
    ranked=[]
    for record in candidates:
        text=texts[record['id']]
        score=sum(math.log(1+len(candidates)/(1+frequencies[w])) for w in query_words&words[record['id']])
        score+=sum(12 for name in anchors if name in text)
        if score:ranked.append((score,record))
    ranked.sort(key=lambda pair:(-pair[0],pair[1]['chapter_index'],pair[1]['id']))
    # First cover named anchors, then select diverse facts before repeated
    # occurrences of the same person. Query-independent record schema only.
    selected=[];seen=set()
    for anchor in anchors:
        match=next((r for _,r in ranked if anchor in texts[r['id']] and r['kind'] in {'state','outcome','event'} and r['id'] not in seen),None)
        if match:selected.append(match);seen.add(match['id'])
    for _,record in ranked:
        if record['id'] not in seen:selected.append(record);seen.add(record['id'])
    return selected


def memory(evidence,query,max_chars=12000,priority_refs=()):
    """Keep raw text across chapters; prioritize claim refs without dropping pages."""
    query_words=terms(query);groups={};priority=set(priority_refs)
    for ref,p in evidence.items():groups.setdefault(p['chapter_index'],[]).append((ref,p))
    for group in groups.values():
        group.sort(key=lambda item:(item[0] not in priority,-len(query_words&terms(item[1]['text'])),item[0]))
    selected={};size=0
    for ref in dict.fromkeys(priority_refs):
        if ref in evidence and size+len(evidence[ref]['text'])<=max_chars:
            p=evidence[ref];selected[ref]={'chapter_index':p['chapter_index'],'text':p['text']};size+=len(p['text'])
    for offset in range(max((len(group) for group in groups.values()),default=0)):
        for _,group in sorted(groups.items()):
            if offset<len(group):
                ref,p=group[offset]
                if ref not in selected and size+len(p['text'])<=max_chars:
                    selected[ref]={'chapter_index':p['chapter_index'],'text':p['text']}
                    size+=len(p['text'])
    return selected


def observation(value):
    """Originals have a separate durable channel; don't resend copies here."""
    if 'passages' in value:
        return {'passage_ids':value.get('passage_ids',[]),'next_cursor':value.get('next_cursor'),
                'fully_read':value.get('fully_read',[]),'total_passages':value.get('total_passages'),
                'note':'正文已进入evidence_memory，其余可按refs再次取回'}
    if 'records' in value:
        result={k:v for k,v in value.items() if k!='records' and not k.startswith('_')}
        if not value.get('all_records'):result['records']=[card(record) for record in value['records']]
        if 'operator' in value:result.pop('records',None)
        if 'records' in result:
            preview=[];size=0
            for row in result['records']:
                cost=len(str(row))
                if size+cost>8500:break
                preview.append(row);size+=cost
            if len(preview)<len(result['records']):result['omitted_records']=len(result['records'])-len(preview)
            result['records']=preview
        return result
    if 'arcs' in value:return {**value,'arcs':[r['id'] for r in value['arcs']],'note':'故事线正文持久保存在story_map，未展示记录可按ID展开'}
    return {k:v for k,v in value.items() if not k.startswith('_')}
