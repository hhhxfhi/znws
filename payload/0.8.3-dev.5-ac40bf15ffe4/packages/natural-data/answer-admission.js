import {randomUUID} from 'node:crypto';
import {requestText} from './request-context.js';
import {proseLines,removeUnquotedMatches} from './calculation-presentation.js';

const MAX_DISCOVERY_CALLS_PER_STEP=4;
const canonicalize=value=>Array.isArray(value)?value.map(canonicalize):value&&typeof value==='object'
  ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonicalize(value[key])])):value;

function boundedToolCalls(chunks, omitted){
 const seen=new Set(),removed=new Set();
 let count=0;
 for(const chunk of chunks){
  if(chunk.type!=='block-end'||chunk.block.type!=='tool-call')continue;
  const {id,name,arguments:serialized}=chunk.block;
  if(omitted?.has(id)){removed.add(chunk.index);continue;}
  const queryTool=name==='data_sql_query'||name==='data_metric_query';
  const analysisTool=name==='analysis_method'||name==='analysis_compute';
  const viewTool=name==='data_visualize';
  if(!queryTool&&!analysisTool&&!viewTool&&name!=='data_catalog'&&name!=='analysis_list')continue;
  let args;
  try{args=JSON.parse(serialized);}catch{if(queryTool||analysisTool||viewTool)continue;args=serialized;}
  const formalSql=name==='data_sql_query'&&!['explore','probe'].includes(args?.purpose);
  const formal=formalSql||name==='data_metric_query'||analysisTool||viewTool;
  // Keep every metric and analysis argument, including the view and method options.
  const {title,...query}=formalSql&&args&&typeof args==='object'?args:{};
  const identity=name+':'+JSON.stringify(canonicalize(formalSql?query:args));
  if(seen.has(identity)||(!formal&&count>=MAX_DISCOVERY_CALLS_PER_STEP)){
   removed.add(chunk.index);
   continue;
  }
  seen.add(identity);
  if(!formal)count++;
 }
 if(!removed.size)return {chunks,count:0};
 // Provider replay state can contain the discarded tool calls. Keep the
 // native finish reason and usage, and rebuild subsequent context from events.
 const kept=chunks.filter(chunk=>!removed.has(chunk.index)).map(chunk=>
  chunk.type==='finish'?{type:'finish',reason:chunk.reason}:chunk);
 return {chunks:kept,count:removed.size};
}

const TECHNICAL_TOPIC=/(?:\bSQL\b|字段名|物理字段|技术(?:追溯|详情|细节)|工具(?:调用|参数|日志)|内部编号|错误码|(?:查询|原始|调试)日志)/i;
const TECHNICAL_REQUEST=/(?:查看|给出|提供|展示|显示|解释|列出|输出|追溯|核对|排查|想看|要看|保留|附上)/;
const TECHNICAL_NEGATION=/(?:不要|不需要|不用|不必|无需|无须|别)/;
const HAN=/\p{Script=Han}/u;
const FOREIGN_PROSE=/[\u0370-\u03FF\u0400-\u052F\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\uAC00-\uD7AF]{2,}/;
const ENGLISH_PROSE=/(?:\b[A-Za-z][A-Za-z’'-]*\b[ \t,;:"]+){4,}\b[A-Za-z][A-Za-z’'-]*\b/;
const ENGLISH_LEAD=/^\s*["“(]*(?:I(?:\b|['’])|we\b|let\s+me\b|(?:the\s+)?(?:scope checker|query engine)\b)/i;
const INTERNAL_DIAGNOSTIC=/\b(?:[A-Z][A-Z0-9]+_)+(?:CONFLICT|MISMATCH|PREREQUISITE|LIMIT|ERROR|CLARIFICATION)\b|\bSELECT\b[^\n]{1,240}\bFROM\b|(?:查询引擎|口径校验器|SQL校验器|工具参数)(?:[^。！？\n]{0,24})(?:拦截|报错|失败|冲突|不匹配|语法)|(?:SQL|查询|工具)\s*(?:修复|重试)\s*(?:预算|上限|次数)|追踪堆栈|堆栈跟踪/;
const INTERNAL_PROGRESS=/^(?:让我(?:再|先|重新)?(?:想|确认|核对|理解)|(?:我已)?完成核实[，,]\s*(?:预测|查询|计算)(?:方法|工具)[^。！？\n]{0,28}(?:前置条件|校验)|根据系统(?:的)?校验[，,]\s*用户)|<\/?tool_call\b[^>]*>/i;
const FORECAST_REQUEST=/(?:预测|预估|推算|(?:未来|接下来).{0,12}(?:月|季|年))/;
const TOOL_PROTOCOL_PROGRESS=/<\/?(?:invoke|function_calls|function_results)\b[^<>\n]*>/ig;

function requestsTechnicalDetails(events){
 return requestText(events).split(/[。！!？?，,；;\n]/).some(part=>
  TECHNICAL_TOPIC.test(part)&&!TECHNICAL_NEGATION.test(part)
  &&(TECHNICAL_REQUEST.test(part)||/是什么|怎么写|如何生成|为什么报错/.test(part)));
}

function businessProgress(text,chineseRequest){
 const visible=[];
 for(const {line,protected:literal} of proseLines(text)){
  if(literal){visible.push(line);continue;}
  const prose=removeUnquotedMatches(line,TOOL_PROTOCOL_PROGRESS);
  const parts=prose.split(/(?<=[。！？；])|(?<=[.!?])(?=\s)/);
  const kept=parts.filter(part=>{
   const plain=part.replace(/`[^`]*`/g,' ').trim();
   if(INTERNAL_DIAGNOSTIC.test(part)||INTERNAL_PROGRESS.test(plain))return false;
   if(chineseRequest&&!HAN.test(plain)&&FOREIGN_PROSE.test(plain))return false;
   const startsEnglish=ENGLISH_LEAD.test(plain),englishSentence=ENGLISH_PROSE.test(plain);
   return HAN.test(plain)?!startsEnglish||!englishSentence:!startsEnglish&&!englishSentence;
  });
  const filtered=kept.join('');
  if(!line.trim()||filtered.trim())visible.push(filtered);
 }
 const result=visible.join('\n');
 return result===text?text:result.trim();
}

function filterToolProgress(chunks,chineseRequest,{forecast=false}={}){
 const texts=new Map(),closed=new Set();
 for(const chunk of chunks){
  const {index}=chunk;
  if(closed.has(index))continue;
  if(chunk.type==='text-delta')texts.set(index,(texts.get(index)||'')+chunk.text);
  if(chunk.type==='block-end'&&chunk.block.type==='text'){
   texts.set(index,chunk.block.text);
   closed.add(index);
  }
 }
 const edited=new Map();
 for(const [index,text] of texts){
  const visible=forecast?'':businessProgress(text,chineseRequest);
  if(visible!==text)edited.set(index,visible);
 }
 if(!edited.size)return chunks;
 const indexes=[...new Set(chunks.flatMap(chunk=>'index' in chunk?[chunk.index]:[]))];
 const emitted=new Set();
 return chunks.flatMap(chunk=>{
  if(chunk.type==='finish'){
   const replay=chunk.replayState;
   // Keep metadata on untouched blocks, including provider tool signatures.
   const aligned=Array.isArray(replay?.blocks)&&replay.blocks.length===indexes.length
    &&indexes.every((index,position)=>!edited.has(index)||replay.blocks[position]?.type==='text');
   return [{type:'finish',reason:chunk.reason,...(aligned?{
    replayState:{response:replay.response,blocks:replay.blocks.flatMap((block,position)=>{
     const index=indexes[position];
     return edited.has(index)?edited.get(index)?[{type:'text'}]:[]:[block];
    })}
   }:{})}];
  }
  if(!edited.has(chunk.index))return [chunk];
  const visible=edited.get(chunk.index);
  if(!visible||emitted.has(chunk.index))return [];
  emitted.add(chunk.index);
  return [...textBlock(visible,chunk.index)];
 });
}


// Buffer a final candidate until evidence checks run. Tool calls still execute
// through the host. Invalid text is retained privately, never published as fact.
export function installAnswerAdmission(ctx,{before,beforeQuestion,omitRedundantDiscovery,settleDiscovery,compose,composePartial,polish,validate,repair,pending,record}){
 const sessions=new Map();
 const shownBeforeQuestion=new WeakMap();
 ctx.on('agent/request',async({agent},next)=>{if(agent.session.header?.agentPreset==='natural-data')sessions.set(agent.id,agent);return next();});
 ctx.on('agent/disposed',({agent})=>{sessions.delete(agent.id);pending.delete(agent.id);});
 ctx.on('llm/stream',async function*(options,next){
  const agent=sessions.get(options.sessionId);
  if(!agent||options.purpose){yield* next();return;}
  const events=agent.session.snapshotEvents(),start=events.findLast(e=>e.type==='turn/start');
  const signal=options.signal||new AbortController().signal;
  signal.throwIfAborted();
  const ready=before&&start?await before(agent,events,start,options,signal):null;
  signal.throwIfAborted();
  const planned=ready?.tools??(ready?.tool?[ready.tool]:[]);
  if(planned.length){
   const showPreview=ready.preview&&shownBeforeQuestion.get(agent)!==start.seq;
   if(showPreview){
    shownBeforeQuestion.set(agent,start.seq);
    yield* textBlock(ready.preview);
   }
   for(const [position,{name,args}] of planned.entries()){
    const index=position+Number(Boolean(showPreview));
    const block={type:'tool-call',id:'call_'+randomUUID().replaceAll('-',''),name,arguments:JSON.stringify(args)};
    yield {type:'block-start',index,blockType:'tool-call'};
    yield {type:'tool-call-delta',index,id:block.id,name,argumentsDelta:block.arguments};
    yield {type:'block-end',index,block};
   }
   yield {type:'finish',reason:{kind:'tool-calls'}};
   return;
  }
  if(ready?.text){
   pending.delete(agent.id);
   yield* textBlock(polish?.(ready.text,events)??ready.text);
   yield {type:'finish',reason:{kind:'stop'}};
   return;
  }
  const chunks=[];let text='',tools=false,bytes=0;
  for await(const chunk of next()){
   options.signal?.throwIfAborted();bytes+=JSON.stringify(chunk).length;
   if(bytes>8*1024*1024)throw Error('ANSWER_BUFFER_LIMIT: 回答超过可核对长度，已停止本次生成。');
   chunks.push(chunk);
   if(chunk.type==='text-delta')text+=chunk.text;
   if(chunk.type==='tool-call-delta'||chunk.type==='block-start'&&chunk.blockType==='tool-call')tools=true;
  }
  const finish=chunks.findLast(c=>c.type==='finish');
  if(tools&&finish?.reason?.kind==='tool-calls'){
   const latest=agent.session.snapshotEvents();
   const proposals=chunks.filter(chunk=>chunk.type==='block-end'&&chunk.block.type==='tool-call').map(chunk=>chunk.block);
   const omitted=proposals.length>1&&start&&latest.findLast(event=>event.type==='turn/start')?.seq===start.seq
     ?omitRedundantDiscovery?.(agent,latest,start,proposals):null;
   const bounded=boundedToolCalls(chunks,omitted);
   if(bounded.count)ctx.logger?.warn?.('问数：同一步省略 %d 个重复调用或超量探查',bounded.count);
   const question=requestText(events),technical=requestsTechnicalDetails(events);
   const filtered=technical?bounded.chunks:filterToolProgress(bounded.chunks,HAN.test(question),
     {forecast:FORECAST_REQUEST.test(question)});
   const blocks=filtered.filter(chunk=>chunk.type==='block-end');
   const calls=blocks.filter(chunk=>chunk.block.type==='tool-call');
   if(settleDiscovery&&calls.length&&start&&latest.findLast(event=>event.type==='turn/start')?.seq===start.seq){
    const answer=await settleDiscovery(agent,latest,start,calls.map(chunk=>chunk.block));
    signal.throwIfAborted();
    if(answer){
     pending.delete(agent.id);
     yield* textBlock(polish?.(answer,latest)??answer);
     for(const chunk of filtered)if(chunk.type==='usage')yield chunk;
     yield {type:'finish',reason:{kind:'stop'}};
     return;
    }
   }
   const questionOnly=calls.length>0&&calls.every(chunk=>chunk.block.name==='ask_user_question');
   const partial=beforeQuestion&&questionOnly&&start&&latest.findLast(event=>event.type==='turn/start')?.seq===start.seq
     ?await beforeQuestion(agent,latest,start,signal,calls.map(chunk=>chunk.block)):null;
   signal.throwIfAborted();
   const preview=typeof partial==='string'&&partial&&shownBeforeQuestion.get(agent)!==start.seq;
   if(preview||partial?.suppressProgress){
    if(preview){
     shownBeforeQuestion.set(agent,start.seq);
     yield* textBlock(partial);
    }
    const keep=blocks.filter(chunk=>chunk.block.type!=='text');
    for(const chunk of filtered){
     if(chunk.type==='usage')yield chunk;
     else if('index' in chunk&&keep.some(block=>block.index===chunk.index))yield {...chunk,index:chunk.index+Number(Boolean(preview))};
    }
    const replay=filtered.findLast(chunk=>chunk.type==='finish')?.replayState;
    const replayState=Array.isArray(replay?.blocks)&&replay.blocks.length===blocks.length
      ?{response:replay.response,blocks:[...(preview?[{type:'text'}]:[]),...replay.blocks.filter((_,i)=>blocks[i].block.type!=='text')]}
      :undefined;
    yield {type:'finish',reason:finish.reason,...(replayState?{replayState}:{})};
   }else yield* filtered;
   return;
  }
  if(tools||finish?.reason?.kind!=='stop'){yield* chunks;return;}
  const latest=agent.session.snapshotEvents();
  const latestStart=latest.findLast(e=>e.type==='turn/start');
  if(!text.trim()&&(!composePartial||!start||latestStart?.seq!==start.seq)){yield* chunks;return;}
  const composed=compose?await compose(agent,latest,latestStart,signal):null;
  const candidate=polish?.(text,latest)??text;
  const issues=await validate(agent,candidate,signal);
  if(!candidate.trim()&&!composed&&!issues.length&&pending.get(agent.id)?.turn!==start?.data.turn){yield* chunks;return;}
  if(issues.length){
   const failure={turn:start?.data.turn,issues,text};
   pending.set(agent.id,failure);
   record(agent.id,failure);
  }
  let revised=composed||(!issues.length?candidate:await repair?.(agent,candidate,signal));
  let unresolved=false, silence=false;
  const held=pending.get(agent.id);
  if(!composed&&composePartial&&start&&latestStart?.seq===start.seq&&held?.turn===start.data.turn){
   const attempts=latest.filter(event=>event.seq>start.seq&&event.type==='user/message'
    &&event.data.source?.kind==='natural-work-answer-review').length;
   // A later candidate can pass numeric checks simply by omitting an answer.
   // Retain the same-turn failure and let the evidence callback check delivery.
   if(!revised||(attempts>0&&!issues.length)){
    const partial=await composePartial(agent,latest,start,signal,held,revised||candidate);
    if(typeof partial?.text==='string'&&partial.text.trim()&&typeof partial.unresolved==='boolean'){
     if(partial.unresolved&&(attempts>=2||partial.verifiedObservation===true)){
      unresolved=true;
      revised=partial.text;
     }else if(!partial.unresolved&&!(await validate(agent,partial.text,signal)).length){
      revised=partial.text;
     }
    }
   }
   silence=!revised&&attempts<2;
  }
  signal.throwIfAborted();
  if(revised)revised=polish?.(revised,latest)??revised;
  if(revised===text&&!unresolved&&(!issues.length||composed===text)){
   pending.delete(agent.id);
   yield* chunks;
  }else{
   if(revised&&!unresolved)pending.delete(agent.id);
   if(!silence)yield* textBlock(revised||'暂时无法核实所问结论。');
   for(const chunk of chunks)if(chunk.type==='usage')yield chunk;
   // A rewritten visible answer must not carry the provider's old replay.
   yield {type:'finish',reason:{kind:'stop'}};
  }
 },{prepend:true});
}

function* textBlock(text,index=0){
 yield {type:'block-start',index,blockType:'text'};
 yield {type:'text-delta',index,text};
 yield {type:'block-end',index,block:{type:'text',text}};
}
