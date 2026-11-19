import { marked } from 'marked';
import DOMPurify from 'dompurify';
import './comparison.css';

const API=import.meta.env.VITE_API_BASE || 'http://127.0.0.1:5002/api';
let selected=null,report=null,lastPayload='',detailSignature='';
const money=value=>typeof value==='number'?'$'+value.toFixed(4):'未知';
const number=value=>typeof value==='number'?value.toLocaleString('zh-CN'):'未知';
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
function markdown(text){const n=el('div','answer');n.innerHTML=DOMPurify.sanitize(marked.parse(String(text||'尚未生成')));return n;}
function metrics(side){
 const u=side?.generation_usage||side?.usage||{},box=el('div','metrics');
 for(const [label,value] of [['输入',number(u.input_tokens)],['输出',number(u.output_tokens)],['总 token',number(u.total_tokens)],['模型请求',number(u.api_requests)],['估算费用',money(u.estimated_cost_usd)],['耗时',typeof side?.elapsed_s==='number'?side.elapsed_s.toFixed(1)+'秒':'未记录']]){
  const cell=el('div');cell.append(el('span','metric-label',label),el('strong','',value));box.append(cell);
 }
 const details=el('details','usage-details');details.append(el('summary','','缓存与推理用量'));
 details.append(el('p','',`缓存读取 ${number(u.cached_input_tokens)} · 缓存写入 ${number(u.cache_write_input_tokens)} · 推理 ${number(u.reasoning_tokens)} token`));
 if(u.usage_complete===false)details.append(el('p','',`用量尚不完整：${typeof u.reported_requests==='number'?Math.max(0,(u.api_requests||0)-u.reported_requests)+'次请求未回传用量':'部分历史用量缺失'}，当前费用仅为已知部分。`));
 box.append(details);return box;
}
function structuredDetails(value){
 if(!Array.isArray(value?.ranks))return null;
 const d=el('details','sources');d.append(el('summary','',`V1原始结构化境界记录 · ${value.ranks.length}条`));
 const wrap=el('div','review-table'),table=el('table');const head=el('tr');
 for(const text of ['人物','原记录境界','数值品阶／阶段','时段','来源段落'])head.append(el('th','',text));table.append(head);
 for(const row of value.ranks){const tr=el('tr');for(const text of [row.name,row.level,`${row.tier} / ${row.stage}`,row.temporal,(row.evidence||[]).join('、')])tr.append(el('td','',text));table.append(tr);}
 wrap.append(table);d.append(wrap);return d;
}
function renderCase(row){
 const main=document.getElementById('case-detail');main.replaceChildren();
 main.append(el('div','case-label',row.label),el('h2','question',row.question),el('p','provenance',row.comparison_scope));
 const grid=el('div','answer-grid');
 for(const [key,title] of [['v1','V1 · 历史回答'],['v2','V2 · 索引与按需取证']]){
  const side=row[key],panel=el('article','answer-panel '+key),head=el('div','panel-head');
  const outcome=side?.harness?.outcome;
  const status=outcome==='budget_exhausted'?'预算中止 · 未交付答案':outcome==='partial'?'已交付 · 保留未知项':outcome==='answered'?'已完成取证回答':side?.status||'待运行';
  head.append(el('h3','',title),el('span','tag',key==='v1'?'本次新增调用 0 次':side?.cache_hit?'缓存复用 · 本次新增 0 次':status));panel.append(head);
  panel.append(el('p','model',side?.model||'gpt-6.1-sol · medium'),metrics(side),markdown(side?.response));
  if(key==='v2'&&row.v2_initial_attempt){const initial=el('details','sources');initial.append(el('summary','','首次中止与续跑记录'));
   initial.append(el('p','','以上费用和 token 包含首次调用及续跑；耗时为两段执行时间之和，不含中间修复停顿。未提高每题 $0.50 上限。'),metrics(row.v2_initial_attempt),el('p','',row.v2_initial_attempt.response));panel.append(initial);}
  if(side?.coverage)panel.append(el('p','coverage',side.coverage));
  const structured=structuredDetails(side?.structured);if(structured)panel.append(structured);
  if(side?.timeline?.length){const detail=el('details','sources');const label=key==='v1'&&!side.timeline.some(s=>s.quotes?.length)?'历史回答的章节索引':'原文依据';detail.append(el('summary','',`${label} · ${side.timeline.length}章`));
   for(const source of side.timeline){detail.append(el('h4','',`原稿 ${source.chapter_index} · ${source.title||''}`));if(source.event)detail.append(el('p','',source.event));for(const quote of source.quotes||[])detail.append(el('blockquote','',quote));}panel.append(detail);}
  grid.append(panel);
 }
 main.append(grid);
 const review=el('section','review');review.append(el('h3','','准确性 · 按原文逐项核对'));
 if(row.review){review.append(el('p','',row.review.method),el('p','',row.review.summary));
  const score=row.review.common_score;if(score)review.append(el('p','',`共同核对项：V1 ${score.v1} / ${score.total} · V2 ${score.v2} / ${score.total}`));
  const wrap=el('div','review-table');const t=el('table');const head=el('tr');for(const text of ['核对项','V1','V2','依据与说明'])head.append(el('th','',text));t.append(head);
  for(const item of row.review.items||[]){const tr=el('tr');for(const v of [item.criterion,item.v1_pass===null?'未评分':item.v1_pass?'通过':'有误',item.v2_pass===null?'未评分':item.v2_pass?'通过':'有误',item.note])tr.append(el('td','',v));t.append(tr);}wrap.append(t);review.append(wrap);
  if(row.review.sources?.length){const sources=el('details','sources');sources.append(el('summary','','核对所用原文'));
   for(const source of row.review.sources){sources.append(el('h4','',`${source.ref} · ${source.title}`),el('blockquote','',source.text));}review.append(sources);}
 }else review.append(el('p','','尚未完成原文核对；引用地址有效不能直接证明答案准确。'));
 main.append(review);
 const trace=row.v2?.harness?.steps;
 if(trace?.length){const d=el('details','trace');d.append(el('summary','',`V2 执行过程 · ${trace.length}步`));for(const s of trace)d.append(el('p','',`${s.tool} · ${s.label||s.id}${s.cache_hit?' · 工具缓存命中':''}`));main.append(d);}
}
function render(){
 const totals=document.getElementById('totals');totals.replaceChildren();
 const prep=report.preprocessing||{},counts=report.cases||[];
 for(const [label,value,detail] of [
  ['预处理已知费用',money(prep.settled_usd ?? prep.usage?.estimated_cost_usd),`预算上限 $10 · ${prep.completed_chapters||0} / 104章${prep.reserved_usd>0?' · '+(prep.status==='published'?'未决请求预留 ':'请求预留 ')+money(prep.reserved_usd):''}`],
  ['V2 本次问答',money(report.totals?.v2_cost_usd),`${counts.filter(c=>c.v2?.status==='completed').length} / ${counts.length}题已完成`],
  ['V1 历史生成',money(report.totals?.v1_historical_cost_usd),`${counts.length}题的历史费用，本次不再支付`],
  ['V1 本次新增','$0.0000','直接复用历史答案，不重跑']]){
  const card=el('div','total-card');card.append(el('span','metric-label',label),el('strong','',value),el('small','',detail));totals.append(card);
 }
 const scored=report.source_review;
 document.getElementById('status').textContent=(report.status_message||report.status)+(scored?` 共同要点：V1 ${scored.v1_passed} / ${scored.common_criteria}，V2 ${scored.v2_passed} / ${scored.common_criteria}。`:'' );
 const notes=document.getElementById('validation-notes');notes.replaceChildren();for(const note of report.validation_notes||[])notes.append(el('p','provenance',note));
 const list=document.getElementById('case-list');list.replaceChildren();
 if(!selected||!counts.some(c=>c.id===selected))selected=counts[0]?.id;
 for(const row of counts){const button=el('button',row.id===selected?'selected':'',row.label);button.type='button';button.setAttribute('aria-pressed',String(row.id===selected));button.addEventListener('click',()=>{selected=row.id;render();});list.append(button);}
 const row=counts.find(c=>c.id===selected);const nextSignature=JSON.stringify(row);if(row&&nextSignature!==detailSignature){detailSignature=nextSignature;renderCase(row);}
}
async function refresh(){try{const response=await fetch(API+'/comparison');if(!response.ok)throw Error(response.status);const next=await response.json();const stable=JSON.stringify({...next,updated_at:null});if(stable===lastPayload)return;lastPayload=stable;report=next;render();}catch(error){document.getElementById('status').textContent='暂时无法连接 V2 试点服务。';}}
refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
