// Adjust the logged call config at Harness's supported request boundary.
// Never rewrite the model's frozen request or permissions. History changes go
// through logged surface replacements only (0.6.2 receipt folding).
import {enterClosing,withNotice,turnStillWorking} from './closing.js';
import {foldPreviousTurns,COMPACTION_INSTRUCTION_ZH} from './fold.js';
import {contextBudgetAnswer} from './answer-guard.js';
export const contextPolicy='natural-work-context-budget-v1';
// 0.6.2: past this share of the window, stop querying and answer.
export const CLOSING_SHARE=.78;
export function optionalService(ctx,name){return typeof ctx.get==='function'?ctx.get(name):ctx[name];}
const first=(text,patterns)=>{for(const p of patterns){const n=Number(text.match(p)?.[1]);if(n>0)return n;}return 0;};
export function overflowBudget(failure){
  // Thousands separators ("70,000") appear in some gateways' messages.
  const text=String(failure?.message||'').replace(/(\d),(?=\d{3}\b)/g,'$1');
  // A number must follow the window phrase directly, so "context window
  // exceeded: input 37233" is not read as a 37233-token window.
  const window=first(text,[/maximum context length is\s*(\d+)/i,/context (?:window|length)(?: size)?(?:\s+(?:of|is)|\s*[:=])?\s*(\d+)/i,/exceeds? (?:the )?(?:model'?s? )?context (?:window|length)(?: of)?\s*(\d+)/i,/context (?:window|length)\s*\((\d+)\s*tokens?\)/i]);
  const input=first(text,[/prompt contains(?: at least)?\s*(\d+)/i,/\((\d+) in the messages/i,/input(?: tokens)?(?:\s+(?:of|is)|\s*[:=])?\s*(\d+)\s*(?:tokens)?/i,/(\d+)\s*input tokens/i,/input\s*\((\d+)\s*tokens?\)/i]);
  return window>0&&input>0&&input<=window*4?{window,input}:null;
}
export const defaultContextLimit=70000;
export function outputBudget({window,input,requested=16384}){
  if(!Number.isFinite(requested)||requested<=0)requested=16384;
  if(!window)return Math.min(requested,8192);
  const margin=Math.max(1024,Math.ceil(window*.05));
  const target=Math.min(16384,Math.max(2048,Math.floor(window*.18)));
  // An unmeasurable input must never send maxTokens=NaN to the provider.
  if(!Number.isFinite(input)||input<0)return Math.min(requested,target,8192);
  const available=Math.floor(window-input-margin);
  return available<256?0:Math.max(256,Math.min(requested,target,available));
}
// Token estimate without a tokenizer. CJK text is about 0.6-1 token per
// character in common models; count 1 per character (a safe upper bound) and
// ~3 characters per token for everything else. Bytes/2 counted 1.5 tokens per
// Chinese character and blocked requests that would have fit.
export function estimateTokens(value){
  const text=typeof value==='string'?value:JSON.stringify(value)??'';
  const cjk=(text.match(/[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]/g)||[]).length;
  return cjk+Math.ceil((text.length-cjk)/3);
}
function isData(agent){return agent.session.header?.agentPreset==='natural-data';}
async function* budgetStream(agent,visible){
  if(visible){
    const text=contextBudgetAnswer(agent);
    yield {type:'block-start',index:0,blockType:'text'};
    yield {type:'text-delta',index:0,text};
    yield {type:'block-end',index:0,block:{type:'text',text}};
  }
  yield {type:'finish',reason:{kind:visible?'stop':'aborted'}};
}
export function installBudget(ctx,options={}){
  const settings=options;
  // closingShare is for acceptance tests (force closing early); production keeps 0.78.
  const closingShare=Number.isFinite(options.closingShare)&&options.closingShare>0&&options.closingShare<1?options.closingShare:CLOSING_SHARE;
  const limit=Number.isFinite(options.contextWindowLimit)&&options.contextWindowLimit>=4096?Math.min(defaultContextLimit,options.contextWindowLimit):defaultContextLimit;
  // 0.6.1: cap the output reservation. Real Qwen 27B answers stay under ~2k
  // tokens without thinking, so reserving 12.6k of a 70k window wastes input
  // room. Raise maxOutputTokens (e.g. 12000-16000) when thinking mode is on.
  const outputCap=Number.isFinite(options.maxOutputTokens)&&options.maxOutputTokens>=1024?options.maxOutputTokens:Infinity;
  // 0.6.2: thinking shares the output budget. Measured xhigh calls reached 7.3k,
  // so thinking requests keep the full 12.6k target.
  const thinkingCap=Number.isFinite(options.maxOutputTokensThinking)&&options.maxOutputTokensThinking>=1024?options.maxOutputTokensThinking:Infinity;
  // The host drops reasoningEffort from the config when it equals the adapter
  // default, so fall back to the model's default effort.
  const capFor=(config,info)=>{const effort=config?.reasoningEffort??info?.reasoning?.defaultEffort;return effort&&effort!=='off'?thinkingCap:outputCap;};
  // provider/model -> whether reasoning effort "off" is accepted.
  const offSupported=new Map();
  const pending=new WeakMap();
  const windows=new WeakMap();
  const exhausted=new WeakSet();
  const failedCompactions=new WeakMap();
  const owned=new Map();
  const rough=value=>estimateTokens(value);
  const service=(agent,name)=>optionalService(ctx,name)||optionalService(agent.ctx||{},name);
  const measured=(agent,header,config)=>{
    const meter=service(agent,'tokenMeter');
    const tools=service(agent,'tools')?.schemas?.(agent)||header?.tools||[];
    let input=meter?meter.measure(agent.session,{...header,config,tools}).totalTokens:rough(tools)+4096;
    const events=agent.session.snapshotEvents();
    const last=[...events].reverse().find(e=>e.type==='assistant/message'&&e.data.usage&&e.data.message?.source?.provider===config.provider&&e.data.message?.source?.model===config.model);
    // Folding (0.6.2) replaces receipts the last usage already counted: like a
    // compaction, it makes that usage stale, so measure the surface instead.
    const compacted=last&&events.some(e=>e.seq>last.seq&&((e.type==='compaction/end'&&!e.data.error)||e.type==='compaction/prune'));
    if(last&&!compacted){
      const u=last.data.usage;
      let observed=(u.inputTokens||0)+(u.cacheReadTokens||0)+(u.cacheWriteTokens||0)+(u.outputTokens||0);
      for(const e of events)if(e.seq>last.seq&&['tool/result','user/message','developer/message'].includes(e.type)&&!(e.surfaceOp&&e.surfaceOp!=='append'))observed+=rough(e.data);
      input=Math.max(input,observed);
    }
    const logged=new Set(events.filter(e=>e.type==='user/message').map(e=>e.data.id));
    for(const message of pending.get(agent)||[])if(!logged.has(message.id))input+=Math.max(meter?.estimateMessage?meter.estimateMessage(message)||0:0,rough(message));
    return input;
  };
  ctx.on('agent/pre-step',async(payload,next)=>{
    if(!isData(payload.agent)||payload.signal.aborted)return next();
    const {agent,signal}=payload;
    owned.set(agent.session.id,agent);
    // 0.6.2: at the first step of a turn, fold earlier turns' receipts.
    // The previous turn stays whole (follow-ups often need all its rows) unless
    // the context is already past half; a failed previous turn always stays whole
    // so "继续完成第N轮" can reuse its receipts.
    if(payload.step===1&&options.foldPreviousTurns!==false){
      try{
        const events=agent.session.snapshotEvents();
        const starts=events.filter(e=>e.type==='turn/start');
        const start=[...starts].reverse().find(e=>e.data.turn===payload.turn);
        const previous=[...starts].reverse().find(e=>e.data.turn===payload.turn-1);
        const previousEnd=previous&&events.find(e=>e.type==='turn/end'&&e.data.turn===previous.data.turn);
        const previousFailed=['error','max-tokens','aborted','cancelled','blocked'].includes(previousEnd?.data?.reason?.kind);
        const header=agent.session.requestHeader?.();
        const tight=header?measured(agent,header,header.config)>=(windows.get(agent)||limit)*.5:false;
        const cutoff=previous&&(previousFailed||!tight)?previous.seq:start?.seq;
        const meter=service(agent,'tokenMeter');
        if(cutoff!==undefined)foldPreviousTurns(agent.session,cutoff,{estimate:m=>meter?.estimateMessage?.(m)||rough(m)});
      }catch(error){ctx.logger?.warn?.('receipt folding skipped: %s',error?.message);}
    }
    pending.set(agent,payload.messages||[]);
    const decision=await next();
    if(decision.kind==='reject'||signal.aborted)return decision;
    pending.set(agent,decision.messages||payload.messages||[]);
    const header=agent.session.requestHeader?.();
    if(header){
      const config=header.config;let window=limit;
      try{
        const info=await service(agent,'llm')?.resolveModelInfo(config.provider,config.model,signal);window=Math.min(window,info?.context?.contextWindow||window);
        // Known before the first compaction of a resumed session (D3).
        if(info)offSupported.set(config.provider+'\u0000'+config.model,Boolean(info.reasoning?.efforts?.some(e=>e.id==='off')));
      }catch{if(signal.aborted)throw signal.reason;}
      windows.set(agent,window);
      const turn=agent.session.snapshotEvents().filter(e=>e.type==='turn/start').at(-1)?.data.turn;
      if(measured(agent,header,config)>=window*.65&&(!failedCompactions.has(agent)||failedCompactions.get(agent)!==turn)){
        const compaction=service(agent,'compaction');
        if(compaction?.compactIfNeeded){
          try{await compaction.compactIfNeeded(agent,'context-overflow',signal);}catch{if(signal.aborted)throw signal.reason;failedCompactions.set(agent,turn);}
        }
      }
      // 0.6.2: still near the limit after compaction -> answer now while there
      // is room for it, instead of failing a few calls later with no answer.
      if(measured(agent,header,config)>=window*closingShare&&turnStillWorking(agent.session.snapshotEvents(),turn)){
        return withNotice(decision,enterClosing(agent,turn,'上下文已接近 '+window+' Token 上限'));
      }
    }
    return decision;
  },{prepend:true});
  ctx.on('agent/request',async({agent,signal},next)=>{
    const config=await next();
    if(!isData(agent)||signal.aborted)return config;
    owned.set(agent.session.id,agent);
    exhausted.delete(agent);
    const llm=service(agent,'llm');
    let info;
    try{info=await llm?.resolveModelInfo(config.provider,config.model,signal);}catch{if(signal.aborted)throw signal.reason;}
    if(info)offSupported.set(config.provider+'\u0000'+config.model,Boolean(info.reasoning?.efforts?.some(e=>e.id==='off')));
    const events=agent.session.snapshotEvents();
    // Only the current turn's overflow retry is evidence for this request.
    const turn=events.filter(e=>e.type==='turn/start').at(-1)?.data.turn;
    const retry=[...events].reverse().find(e=>e.type==='llm/retry'&&e.data.policyKey===contextPolicy&&e.data.provider===config.provider&&e.data.failure?.code==='CONTEXT_WINDOW_EXCEEDED');
    const current=retry&&(turn===undefined||retry.data.turn===turn);
    // Retry belongs to this exact route; do not reuse another model's capacity.
    const routeEvent=events.filter(e=>e.type==='request/header'&&e.seq<(retry?.seq??-1)).at(-1);
    const route=routeEvent?.data.header?.config;
    // Recovery compacts before it appends llm/retry, so a compaction after the
    // failed request (not only after the retry record) makes the overflow's
    // input count stale. Reusing it would block every later request.
    const stale=current&&events.some(e=>e.seq>(routeEvent?.seq??retry.seq)&&((e.type==='assistant/message'&&e.data.usage)||(e.type==='compaction/end'&&!e.data.error)));
    const observed=current&&!stale&&route?.model===config.model?overflowBudget(retry.data.failure):null;
    const window=Math.min(limit,observed?.window||info?.context?.contextWindow||limit);
    windows.set(agent,window);
    const header=agent.session.requestHeader?.();
    let input=measured(agent,header,config);
    if(observed)input=Math.max(input,observed.input);
    const maxTokens=outputBudget({window,input,requested:Math.min(config.maxTokens||info?.defaultMaxTokens||16384,capFor(config,info))});
    if(!maxTokens)exhausted.add(agent);
    return {...config,maxTokens:maxTokens||256};
  },{prepend:true});
  // The native compactor calls llm.stream directly. Guard its auxiliary request
  // and the final fully assembled normal envelope without mutating either.
  ctx.on('llm/stream',(options,next)=>{
    const agent=owned.get(options.sessionId);
    if(!agent||!isData(agent))return next();
    options.signal?.throwIfAborted();
    const visible=!options.purpose||options.purpose==='normal';
    if(visible&&exhausted.delete(agent))return budgetStream(agent,true);
    // 0.6.2: the adapter reads this same options object, so these changes reach the request.
    if(options.purpose==='compaction'){
      // Thinking used the whole 2k summary budget ("summarization truncated").
      if(options.reasoningEffort!=='off'&&offSupported.get(options.provider+'\u0000'+options.model))options.reasoningEffort='off';
      const last=options.messages?.at(-1);
      if(last?.role==='user'&&settings.compactionInstruction!==false)options.messages=[...options.messages.slice(0,-1),{...last,content:[{type:'text',text:COMPACTION_INSTRUCTION_ZH}]}];
    }
    const estimate=rough({messages:options.messages,tools:options.tools});
    const calibrated=measured(agent,agent.session.requestHeader?.(),{provider:options.provider,model:options.model,maxTokens:options.maxTokens});
    const events=agent.session.snapshotEvents();
    const sample=[...events].reverse().find(e=>e.type==='assistant/message'&&e.data.usage&&e.data.message?.source?.provider===options.provider&&e.data.message?.source?.model===options.model);
    const calibratedRoute=sample&&!events.some(e=>e.seq>sample.seq&&e.type==='compaction/end'&&!e.data.error);
    // Provider usage calibrates retained history. Re-pricing all prior JSON at
    // bytes/2 would otherwise reject a valid resumed answer despite actual
    // receipts being well below70k. Auxiliary input is a history subset plus
    // the native summary directive; reserve2048 for that directive.
    // Other auxiliary calls (titles, summaries) send only their own messages, so
    // price them by what they actually send instead of the whole session.
    const auxiliary=options.purpose&&!['normal','compaction'].includes(options.purpose);
    const input=auxiliary?estimate:options.purpose==='compaction'?Math.min(estimate,calibrated+2048):calibratedRoute?calibrated:Math.max(estimate,calibrated);
    const reserved=options.maxTokens||16384;
    const window=windows.get(agent)||limit;
    if(input+reserved+Math.max(1024,Math.ceil(window*.05))>window){
      return budgetStream(agent,visible);
    }
    ctx.logger?.debug?.('context-budget purpose=%s inputEstimate=%d reservedOutput=%d contextLimit=%d',options.purpose||'normal',input,reserved,window);
    return next();
  },{prepend:true});
  ctx.on('agent/disposed',({agent})=>{if(owned.get(agent.session.id)===agent)owned.delete(agent.session.id);pending.delete(agent);});

}
