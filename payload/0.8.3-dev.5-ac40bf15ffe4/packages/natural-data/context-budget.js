// Lives alongside the preset's isolated native compaction service.
import {installRecovery} from './recovery.js';
import {installVagueRequest} from './vague.js';
import {installProcessLanguage} from './process-language.js';
import {clarificationEvidence} from './host-evidence.js';
import {checkedEmptyGroupedMonths} from './coverage-evidence.js';
import {installClosing,enterClosing,closingState,leaveClosing,withNotice,turnStillWorking,CLOSING_STEPS} from './closing.js';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
export const name='natural-data-context-budget';
export const inject=['compaction','llm'];
const TOOL_NAMES={data_sql_query:'基础查询',data_metric_query:'业务指标查询',data_catalog:'查看目录',data_inspect:'字段统计',analysis_field_statistics:'字段统计',analysis_compute:'计算',data_visualize:'换图表'};
const dataQueries=new Set(['data_sql_query','data_metric_query','data_query','data_report']);
const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;

function discoveryEvidence(name,receipt,args){
  const scope=receipt?.execution_scope??receipt?.project;
  if(name==='data_catalog'&&Array.isArray(receipt?.sources)){
    // Catalog references and execution IDs are fresh on every read. Retain
    // the actual evidence and paging boundary when checking for repetition.
    const {question,source_id,...address}=args||{};
    const stable=Object.fromEntries(Object.entries(receipt.temporal_context||{}).filter(([k])=>k!=='current_time'));
    const sources=receipt.sources.map(s=>{
      const {starter_questions,catalog_entries,caliber_matches,example_matches,...fields}=s;
      // Suggested prompts and empty search results add no evidence of their own.
      const nonempty=items=>items==null||Array.isArray(items)&&!items.length?undefined:items;
      const withoutScore=items=>nonempty(Array.isArray(items)?items.map(({relevance,...item})=>item):items);
      return {...fields,catalog_entries:nonempty(catalog_entries),
        caliber_matches:withoutScore(caliber_matches),example_matches:withoutScore(example_matches)};
    });
    return JSON.stringify(canonical({scope,address,sources,temporal:stable,view:receipt.catalog_view,readiness:receipt.readiness}));
  }
  if(name==='data_sql_query'&&receipt?.purpose==='explore'&&receipt.values?.complete===true){
    // Equal samples from different predicates are not duplicate evidence.
    if(!receipt.query_identity)return null;
    return JSON.stringify(canonical({scope,source:receipt.receipt?.source_id,config:receipt.result_lineage?.config_fingerprint,
      query:receipt.query_identity,columns:receipt.values.columns,rows:receipt.values.rows,warnings:receipt.warnings}));
  }
  return null;
}

export function apply(ctx,config={}){
  installRecovery(ctx,config);
  installClosing(ctx);
  installVagueRequest(ctx);
  installProcessLanguage(ctx);
  ctx.on('agent/pre-step',async({agent,turn,signal},next)=>{
    if(signal.aborted||agent.session.header?.agentPreset!=='natural-data')return next();
    // 0.6.2: a turn in closing mode gets a few steps to write its answer; only
    // after that does the original progress error end it.
    const closing=closingState(agent,turn);
    if(closing){
      closing.steps++;
      if(closing.steps>CLOSING_STEPS)throw Object.assign(Error('QUERY_PROGRESS_LIMIT: 本轮已停止无进展的重复调用，避免继续消耗上下文。已成功的数据结果和历史保留；请检查最后一项数据范围或业务口径提示，修正后继续。'),{code:'QUERY_PROGRESS_LIMIT'});
      return next();
    }
    leaveClosing(agent);
    const events=agent.session.snapshotEvents();
    const start=[...events].reverse().find(e=>e.type==='turn/start'&&e.data.turn===turn);
    const current=events.filter(e=>e.seq>(start?.seq??-1));
    const calls=new Map(current.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e.data.name]));
    const parameters=new Map(current.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e.data.args??e.data.arguments]));
    const exactFailures=new Map();
    const failures=new Map(),seenDiscovery=new Set();let schemaFailures=0,unproductive=0,repeatedDiscovery=0,latestDiscoveryRepeat=false,progressTime=start?.time||Date.now();
    let blockedAt=0,toolsAfterBoundary=0;
    for(const e of current){
      if(e.type!=='tool/result')continue;
      const message=e.data.message,name=calls.get(message?.toolCallId||message?.source?.callId);
      if(!name)continue;
      const text=(message.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
      let receipt;
      try{receipt=JSON.parse(text);}catch{/* native tool errors are plain text */}
      const query=['data_sql_query','data_query'].includes(name);
      let args=parameters.get(message?.toolCallId||message?.source?.callId);
      if(typeof args==='string')try{args=JSON.parse(args);}catch{args=null;}
      const exact=args&&typeof args==='object'?name+':'+JSON.stringify(canonical(args)):null;
      const code=text.match(/(?:Error:\s*)?([A-Z][A-Z_]{3,})\s*:/)?.[1]||receipt?.reason_code||'error';
      const formal=dataQueries.has(name)&&!['explore','probe'].includes(args?.purpose);
      const boundUnavailable=formal&&message.isError&&(code==='SNAPSHOT_HISTORY_REQUIRED'||
        code==='BUSINESS_SCOPE_CONFLICT'&&text.includes('当前表的已发布行计数口径为'));
      const periodCoverage=receipt?.answer_contract?.period_count_coverage;
      const emptyWindow=formal&&receipt?.status==='succeeded'&&periodCoverage?.result_ref===receipt.result_ref
        &&receipt.result_lineage?.purpose==='answer'&&periodCoverage.request_ref===receipt.result_lineage.request_ref
        &&receipt.values?.complete===true&&receipt.values.rows?.length===1
        &&Array.isArray(receipt.values.rows[0])
        &&receipt.values.rows[0].length===periodCoverage.columns?.length&&periodCoverage.columns.length>0
        &&periodCoverage.columns.every(column=>column.state==='unobserved')
        &&receipt.values.rows[0].every(value=>value===null);
      if(boundUnavailable||emptyWindow||formal&&checkedEmptyGroupedMonths(receipt)){
        if(!blockedAt){blockedAt=e.seq;toolsAfterBoundary=0;}
        else toolsAfterBoundary++;
      }else if(formal&&receipt?.status==='succeeded'){
        blockedAt=0;toolsAfterBoundary=0;
      }else if(blockedAt&&e.seq>blockedAt&&/^(?:data_|analysis_|work_|knowledge_)/.test(name)){
        toolsAfterBoundary++;
      }
      const exactFailure=exact?exact+':'+code:null;
      const schemaFailure=query&&(receipt?.reason_code==='SCHEMA_MISMATCH'||(message.isError&&/SCHEMA_MISMATCH/.test(text)));
      const timedOut=formal&&receipt?.status==='failed'&&receipt.reason_code==='TIMEOUT';
      if(schemaFailure)schemaFailures++;
      else if(query&&receipt?.status==='succeeded'&&receipt.purpose!=='explore')schemaFailures=0;
      // Paging to different entries, a different SQL observation and genuine
      // human clarification are progress. Identical evidence is not.
      const answered=name==='ask_user_question'&&clarificationEvidence(current.filter(item=>item.seq<=e.seq)).clarifications.some(c=>c.call_id===(message.toolCallId||message.source?.callId));
      if(answered){blockedAt=0;toolsAfterBoundary=0;}
      latestDiscoveryRepeat=false;
      if(!message.isError&&!schemaFailure&&!timedOut&&(/^(data_|analysis_|work_|knowledge_)/.test(name)||answered)){
        const signature=discoveryEvidence(name,receipt,args);
        if(signature&&seenDiscovery.has(signature)){
          repeatedDiscovery++;
          latestDiscoveryRepeat=true;
          continue;
        }
        if(signature)seenDiscovery.add(signature);
        else if(answered||query&&receipt?.status==='succeeded'&&receipt.purpose!=='explore'){
          repeatedDiscovery=0;
          // A formal result advances the response; the same evidence remains
          // seen until the human changes the request scope.
          if(answered)seenDiscovery.clear();
        }
        unproductive=0;failures.clear();progressTime=e.time||progressTime;
        for(const key of exactFailures.keys())if(exact&&key.startsWith(exact+':')||answered&&key.endsWith(':NEEDS_CLARIFICATION'))exactFailures.delete(key);
        continue;
      }
      unproductive++;
      if(message.isError||schemaFailure||timedOut){
        // The same arguments can move from waiting for a human choice to a
        // different validation stage. Only the same typed failure is repeated.
        if(exactFailure)exactFailures.set(exactFailure,(exactFailures.get(exactFailure)||0)+1);
        // Keep the message detail with the code: three different SCHEMA_MISMATCH
        // errors are the model fixing SQL, not one error repeating.
        const normalized=text.replace(/[0-9a-f]{16,}|[0-9a-f-]{36}/gi,'[ref]').replace(/\s+/g,' ');
        const signature=name+':'+code+':'+normalized.slice(0,120);
        failures.set(signature,(failures.get(signature)||0)+1);
      }
    }
    const repeated=Math.max(0,...failures.values());
    const identical=Math.max(0,...exactFailures.values());
    // Only a turn still calling tools is stopped; a repair step after a final
    // answer (answer guard) must not get a conflicting closing notice.
    const isWorking=turnStillWorking(events,turn);
    const reachedBoundary=blockedAt&&toolsAfterBoundary>=2;
    if((reachedBoundary||identical>=2||schemaFailures>=4||repeated>=3||repeatedDiscovery>=4||unproductive>=24||(unproductive>=8&&start&&Date.now()-progressTime>180000))&&isWorking){
      // 0.6.2: answer from what is known instead of ending with an error.
      const worst=[...failures].sort((a,b)=>b[1]-a[1])[0];
      const [tool,...rest]=(worst?.[0]||'').split(':');
      const reason=reachedBoundary?'正式查询已明确数据边界，后续调用尚未获得可替代的正式结果。仅说明该次查询的来源和范围，其他独立问题分别说明已查到什么或尚未核实。缺观测不得填零或计算差额'
        :repeatedDiscovery>=4?'多次返回相同的目录或探查证据，请使用已核对结果并说明缺少的数据或定义':identical>=2?'相同参数已失败两次，请保留已有结果并说明未完成的计算或条件':schemaFailures>=4?'本轮已有四次查询字段或类型不匹配，请保留已有结果，列出还缺的字段和待核对的口径':repeated>=3?'同一操作已连续 '+repeated+' 次没有进展（'+(TOOL_NAMES[tool]||tool)+'：'+rest.join(':').slice(0,60)+'）':'本轮已有 '+unproductive+' 次调用没有新进展';
      const decision=await next();
      return withNotice(decision,enterClosing(agent,turn,reason,'query-progress'));
    }
    if(isWorking&&latestDiscoveryRepeat&&repeatedDiscovery>=2){
      const decision=await next();
      return withNotice(decision,createUserMessage({source:{kind:'natural-data-discovery-repeat',form:'notice',summary:'目录或探查未增加证据'},
        content:[{type:'text',text:'多次目录或探查返回同样的证据。若已有可用字段和口径，按原问完成一次查询；若缺少必要字段或定义，请直接指出当前授权资料的缺项，保留已核对的部分。只有确实有可选业务范围时才询问。不要换关键词重复查看同一资料，也不要把相近指标当成原问题。'}]}));
    }
    return next();
  },{prepend:true});
}
