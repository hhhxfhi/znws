// 0.6.2: fold earlier turns' tool receipts and give compaction a data-question
// summary format. Folding uses the same surface replacement as the native
// tool-result pruner: the original event stays in the log, the model sees the
// short version from the next request on.
export const FOLD_MARK='【前几轮回执已折叠】';
export const FOLD_MIN_CHARS=600;
const KEEP_ROWS=5;
const KEEP=['status','title','result_ref','task_id','row_count','reason_code','purpose','warnings','period_coverage'];

function clip(text,limit){text=String(text??'');return text.length>limit?text.slice(0,limit)+'…':text;}

// Return the folded text for one receipt, or null when it is already short.
export function foldReceiptText(text,isError=false){
  if(typeof text!=='string'||text.length<=FOLD_MIN_CHARS||text.includes(FOLD_MARK))return null;
  let value;try{value=JSON.parse(text);}catch{value=null;}
  if(isError||!value||typeof value!=='object'||Array.isArray(value)){
    // Split on Chinese sentence ends and newlines only: "table.column" and decimals stay whole.
    const first=text.split(/(?<=[。！？])|\n/)[0];
    return FOLD_MARK+clip(first,300);
  }
  const out={folded:true};
  for(const key of KEEP)if(value[key]!==undefined&&value[key]!==null)out[key]=value[key];
  if(value.values&&Array.isArray(value.values.columns)){
    const rows=Array.isArray(value.values.rows)?value.values.rows:[];
    out.values={columns:value.values.columns,rows:rows.slice(0,KEEP_ROWS),row_count:value.values.row_count??rows.length,complete:Boolean(value.values.complete)&&rows.length<=KEEP_ROWS};
  }
  if(Array.isArray(value.facts))out.facts=value.facts.slice(0,KEEP_ROWS).map(f=>({dimensions:f.dimensions,value:f.value,unit:f.unit}));
  if(Array.isArray(value.sources)){
    // Catalog: keep where data lives, drop columns and metric bodies.
    out.sources=value.sources.map(s=>({id:s.id,name:s.name,dialect:s.dialect,tables:Array.isArray(s.tables)?s.tables.map(t=>t.label?t.name+'（'+clip(t.label,20)+'）':t.name):undefined,
      ...(Array.isArray(s.semantic_metrics)&&s.semantic_metrics.length?{semantic_metrics:s.semantic_metrics.map(m=>({id:m.id,version:m.version,name:m.name||m.label}))}:{}),
      ...(Array.isArray(s.metric_definitions)&&s.metric_definitions.length?{metric_definitions:s.metric_definitions.map(m=>({id:m.id}))}:{})}));
  }
  if(Array.isArray(value.tasks))out.tasks=value.tasks.map(t=>({task_id:t.task_id,title:t.title,state:t.state}));
  if(Array.isArray(value.claims))out.claims=value.claims.slice(0,KEEP_ROWS).map(c=>c.verified_text||c.operation).filter(Boolean);
  out.note='前几轮回执已折叠，只保留前几行；完整结果在结果卡中。只用这几行能回答就直接引用，需要更多行时按原条件重新查询。';
  const folded=FOLD_MARK+JSON.stringify(out);
  return folded.length<text.length?folded:null;
}

function frozenText(text){return Object.freeze([Object.freeze({type:'text',text})]);}

// Fold tool receipts from turns before `turnStartSeq`. Returns how many were folded.
export function foldPreviousTurns(session,turnStartSeq,{estimate}={}){
  const nodes=session?.surface?.nodes;
  if(!nodes||typeof session.eventAt!=='function'||typeof session.deriveEventMessage!=='function')return 0;
  let count=0;
  for(const seq of [...nodes]){
    if(seq>=turnStartSeq)continue;
    const event=session.eventAt(seq);
    if(event?.type!=='tool/result')continue;
    const original=session.deriveEventMessage(event);
    const blocks=original?.content||[];
    if(blocks.some(b=>b.type!=='text'))continue;
    const text=blocks.map(b=>b.text).join('');
    const folded=foldReceiptText(text,Boolean(original.isError));
    if(folded===null)continue;
    const message=Object.freeze({...original,content:frozenText(folded)});
    session.append('compaction/prune',{shadowedRange:{start:seq,end:seq},shadowedSeqs:[seq],shadowedTokenCount:estimate?estimate(original):Math.ceil(text.length/2)});
    session.append('tool/result',{...event.data,message},{surfaceOp:{op:'replace',startSeq:seq,endSeq:seq},sourceEventSeqs:[seq]});
    count++;
  }
  return count;
}

export const COMPACTION_INSTRUCTION_ZH=[
  '请把以上对话压缩成一份中文检查点，供下一次请求接着回答用户的数据问题。不要调用工具，不要输出思考过程，只输出下面的 Markdown 结构，每节用简短条目；某节没有内容写“（无）”，不要删节。',
  '',
  '## 用户问题与最新要求',
  '- 用户的原问题和后续追问，期间、范围、条件按原话保留；用户撤销或替换过的条件写明。',
  '',
  '## 已确认的数据源、表和字段',
  '- source_id、表名、字段名及其业务含义；用户确认过的口径。',
  '',
  '## 已成功的查询',
  '- 每条一行：标题；result_ref；task_id；所用表和条件；关键数值（带单位和期间）。',
  '',
  '## 失败或受阻的尝试',
  '- 做了什么、错误码或原因；已经证明不可行的方向，避免重复。',
  '',
  '## 尚未完成的事项',
  '- 还没回答的子问题和下一步。',
  '',
  '## 已向用户说明或澄清的内容',
  '- 已给出的结论和澄清问答。'
].join('\n');
