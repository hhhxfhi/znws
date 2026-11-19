import {defineTool} from '@deepseek-ai/dsh-tools';
import {readFileSync,realpathSync} from 'node:fs';
import {registerCapability} from '../capability-sdk/adapter.js';
import {installConfirmedMonthlyAnswer} from '../natural-data/confirmed-monthly-answer.js';
import {installAnswerGuard,configureAnswerQualityDirectory} from '../natural-data/answer-guard.js';
import {installContinuationMarkdown} from '../natural-data/continuation-markdown.js';
import {installMixedDelivery} from '../natural-data/mixed-delivery.js';
import {installPurposeQuestions} from '../natural-data/intent-questions.js';
import {clarificationEvidence,completedWithoutTools,humanRequests,repeatedSnapshotRequests} from '../natural-data/host-evidence.js';
import {currentAnswerRefs,currentExplorationRefs,normalizeAnalysisToolArgs,normalizeDataToolArgs} from '../natural-data/tool-normalization.js';
import {assertSqlRepairBudget,sqlTimeoutRetry} from '../natural-data/repair-budget.js';
export const name='natural-capabilities';
const runtimeKey='naturalDataRuntime_'+JSON.parse(readFileSync(new URL('../../build-info.json',import.meta.url),'utf8')).build_id;
export const inject=['tools','systemPrompt',runtimeKey];
// Static JSON remains a packaging artifact. Register the validators of the running
// engine so an old cached manifest cannot advertise an impossible invocation.
// 0.6.1 tool profiles. "compact" (default) is for local models: one tool per
// query (data_sql_query / data_metric_query), overlapping tools hidden.
// "full" keeps the original multi-step tools for comparison runs.
const COMPACT_HIDDEN=['data_plan','data_plan_query','data_semantic_plan','data_report','data_query','data_analyze','analysis_run','work_create','work_patch'];
const LEGACY_TOOLS=['data_plan','data_report','work_create'];
const FULL_HIDDEN=['data_sql_query','data_metric_query'];
// Legacy compatible metrics need the old plan/report pair. Show it only after
// this session's catalog actually listed metric_definitions.
export function catalogHasLegacyMetrics(events){
  const calls=new Set(events.filter(e=>e.type==='tool/call'&&e.data.name==='data_catalog').map(e=>String(e.data.callId)));
  for(const e of [...events].reverse()){
    // 0.6.2: folded receipts (surface replacements) are not catalog evidence; read the original.
    // A multi-source catalog page lists metric counts only (metric_definitions_count).
    if(e.type!=='tool/result'||(e.surfaceOp&&e.surfaceOp!=='append')||e.data.message?.isError||!calls.has(String(e.data.message?.source?.callId)))continue;
    try{const value=JSON.parse(e.data.message.content.find(x=>x.type==='text')?.text||'{}');return (value.sources||[]).some(s=>s.metric_definitions?.length||s.metric_definitions_count>0);}catch{continue;}
  }
  return false;
}
export function profileDeny(profile,events,toolSupport){
  if(profile==='full')return FULL_HIDDEN;
  const legacy=typeof toolSupport?.legacy_metrics==='boolean'
    ? toolSupport.legacy_metrics : catalogHasLegacyMetrics(events);
  return COMPACT_HIDDEN.filter(name=>!(legacy&&LEGACY_TOOLS.includes(name)));
}
export function apply(ctx,config={}){
  const call=(...args)=>ctx[runtimeKey].call(...args);
  configureAnswerQualityDirectory(ctx[runtimeKey].answerQualityDirectory);
  const contract=ctx[runtimeKey].contract;
  const expected=JSON.parse(readFileSync(new URL('../../build-info.json',import.meta.url),'utf8')).build_id;
  if(!contract||contract.build_id!==expected||!Array.isArray(contract.capabilities))throw Error('PLUGIN_CONTRACT_MISMATCH: 插件工具模块与运行引擎版本不一致，请重新加载插件。');
  const manifests=contract.capabilities;
  if(ctx.tools.schemas().some(t=>t.name==='working_directory'))ctx.effect(()=>ctx.tools.restrict({deny:['working_directory']}));
  const profile=config.toolProfile==='full'?'full':'compact';
  const selected=new Set(config.capabilities||manifests.map(m=>m.id));
  const active=manifests.filter(m=>selected.has(m.id));
  // Assemble current policy as request-owned data. Agent-owned registrations
  // outlive preset revisions and can otherwise collide on the next user turn.
  ctx.on('system-prompt/assemble',async(_assembly,{agent,signal},next)=>{
    const assembly=await next();
    if(!agent)return assembly;
    const project=realpathSync(agent.session.header.cwd);
    const policy=await call('policy',project,{},'',signal,agent.id);
    const deny=new Set(active.flatMap(m=>policy.states[m.id]==='enabled'?[]:m.operations.filter(o=>!(policy.states[m.id]==='history'&&['read','inspect','export'].includes(o.name))).map(o=>m.id+'_'+o.name)));
    if(agent.session.header?.agentPreset==='natural-data')deny.add('working_directory');
    for(const name of profileDeny(profile,agent.session.snapshotEvents(),policy.data_tool_support))deny.add(name);
    return {...assembly,tools:assembly.tools.filter(t=>!deny.has(t.name)),
      sections:[...assembly.sections.filter(s=>s.name!=='natural-work:enabled-capabilities'),
        {name:'natural-work:enabled-capabilities',interpolate:false,text:'宿主能力配置版本 '+policy.revision+'；当前状态：'+JSON.stringify(policy.states)+'。仅使用已启用的工具；能力关闭不是数据为零。'}]};
  });
  installAnswerGuard(ctx,call,ctx[runtimeKey].answerQualityDirectory);
  if(active.some(m=>m.id==='data')){
    installConfirmedMonthlyAnswer(ctx,call);
    installContinuationMarkdown(ctx);
  }
  installPurposeQuestions(ctx);
  installMixedDelivery(ctx);
  for(const manifest of active) registerCapability(ctx,{
    manifest, defineTool,
    normalizeArgs:manifest.id==='analysis'?normalizeAnalysisToolArgs:undefined,
    prompt:manifest.id==='data'?readFileSync(new URL(profile==='full'?'../natural-data/SKILL.md':'../natural-data/SKILL-compact.md',import.meta.url),'utf8'):'能力工序 '+manifest.id+'：复用同一Harness会话和权限，按输入与输出合约执行。',
    context(exec){
      const cwd=exec.agent?.session.header.cwd;
      if(!cwd)throw Error('问数工具必须在已选择工作区的 Harness 会话中调用。');
      const events=exec.agent.session.snapshotEvents();
      const users=humanRequests(events);
      const first=users.length===1?users[0].data:null;
      const text=first?.content?.filter(x=>x.type==='text').map(x=>x.text).join('\n')||'';
      const materialOnly=Boolean(first)&&(!text.trim()||(!/[?？查问看统算分析对比比较展示多少如何怎么请帮解释整理写]/.test(text)&&(text.split('\n').filter(x=>x.trim()).length>1||/^[\d\s.,，、；;]+$/.test(text))));
      const latest=users.at(-1);
      const user_text=latest?.data.content?.filter(x=>x.type==='text').map(x=>x.text).join('\n')||'';
      const clarification=clarificationEvidence(events,latest?.seq??0);
      const client_time_zone=latest?.data.source?.clientTimeZone||Intl.DateTimeFormat().resolvedOptions().timeZone;
      // Trusted user context is sent only to the local scope validator, not added
      // to the model prompt. Keep enough refinements to retain their root request.
      const bySequence=new Map();
      const request=e=>{
        if(!bySequence.has(e.seq)){
          const nextUser=users.find(u=>u.seq>e.seq);
          bySequence.set(e.seq,{user_ref:e.data.id,turn:e.data.turn,
            text:(e.data.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n').slice(0,2000),
            ...clarificationEvidence(events.filter(x=>x.seq<(nextUser?.seq??Infinity)),e.seq),
            ...(nextUser===latest&&completedWithoutTools(events,e,nextUser)?{completed_without_tools:true}:{})});
        }
        return bySequence.get(e.seq);
      };
      const recent_requests=users.slice(-17,-1).map(request);
      const recent_questions=recent_requests.map(item=>item.text);
      const historical=manifest.id==='data'&&!clarification.clarifications.length&&repeatedSnapshotRequests(events,users);
      const host_evidence={user_text,recent_questions,recent_requests,...clarification,user_ref:latest?.data.id,turn:latest?.data.turn,client_time_zone,
        ...(historical?{historical_snapshot_recent_requests:historical.map(request)}:{})};
      return {host_evidence,session:exec.agent.id,project:realpathSync(cwd),key:`${exec.agent.id}:${exec.callId}`,callId:exec.callId,materialOnly,events};
    },
    invoke:(op,args,scope,signal)=>{
      if(scope.materialOnly&&manifest.id==='data')throw Error('NEEDS_INTENT: 用户仅提供材料，尚未要求查数。请询问用途（解释指标、整理文档或分析数据），不要只询问查询期间。');
      const finalArgs=manifest.id==='data'?normalizeDataToolArgs(op,args,scope.events):args;
      if(manifest.id==='data')assertSqlRepairBudget(op,finalArgs,scope.events,{callId:scope.callId,buildId:contract.build_id,originalSourceId:args.source_id});
      const retry=manifest.id==='data'&&op==='sql_query'&&sqlTimeoutRetry(scope.events,{
        requestRef:scope.host_evidence.user_ref,sessionId:scope.session,callId:scope.callId,args});
      const hostEvidence=manifest.id==='data'&&['sql_query','query','plan_query'].includes(op)
        ?{...scope.host_evidence,current_answer_refs:currentAnswerRefs(scope.events),current_exploration_refs:currentExplorationRefs(scope.events),
          ...(retry?{sql_timeout_retry_ref:retry.resultRef}:{})}
        :scope.host_evidence;
      return call('capabilities/'+manifest.id+'/'+op,scope.project,finalArgs,scope.key,signal,scope.session,hostEvidence);
    },
    cancel(op,scope,args){if(manifest.id==='data'&&['query','report'].includes(op))void call('cancel',scope.project,{},args.plan_ref?'bound:'+args.plan_ref:scope.key,undefined,scope.session).catch(()=>{});},
    decorate(op,value,scope){
      if(op==='catalog')value.runtime_contract={build_id:contract.build_id,source:'active-engine'};
      if(value.result_ref){
        const query=new URLSearchParams({project:value.execution_scope||scope.project,result_ref:value.result_ref});
        value.view_url='/natural-data/view?'+query;
        if(op==='export')value.download_url='/natural-data/api/export?'+query;
      }
      return value;
    }
  });
}
