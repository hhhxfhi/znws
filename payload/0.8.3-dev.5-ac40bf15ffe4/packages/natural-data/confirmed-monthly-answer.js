// A confirmed single-series trend can be delivered directly from its authorized facts.
// Harness and the chosen model still own catalog selection, clarification and the query.
import {realpathSync} from 'node:fs';
import {clarificationEvidence} from './host-evidence.js';

const content=m=>(m?.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
const years=s=>[...new Set((s||'').match(/20\d{2}/g)||[])];
function exclusiveTrend(s){
  return years(s).length>1&&/只看|仅看|限定|只统计/.test(s)&&/按月|逐月/.test(s)&&/趋势|走势/.test(s)
    &&!/[、及和与]|以及|然后|另外|同时|并|原因|对比|同比|环比|预测|如果|分组|各市|各区|排名|累计|平均|阈值|超过|低于/.test(s);
}
const correction=s=>years(s).length===1&&/为准|按|确认/.test(s)&&/^[\s\d年月从到至以按为准统计只看就选确认请全的间期使用，,。.!！\-/–—~～]+$/.test(s);
// This shortcut only formats a complete year. Every narrower or unclear scope
// goes through the normal planning path, whose month contract checks the query.
function fullYear(s){
  const clauses=String(s||'').normalize('NFKC').split(/[,，]/).filter(x=>x.trim());
  const selected=clauses.map(clause=>clause.match(/^\s*(?:(?:以|按|确认|请|只看|就选|使用)\s*)*(20\d{2})年(?:\s*(?:全年|全年的|从?1月?\s*[-—–~～至到]\s*(?:\1年)?12月))?(?:\s|为准|统计|。|！|!)*$/)?.[1]);
  return selected.length&&selected.every(y=>y&&y===selected[0])?selected[0]:undefined;
}

function simpleMonthlyQuestion(text,name){
  if(!name||!text.includes(name)||!/按月|逐月|每月|月度/.test(text))return false;
  const rest=text.replace(name,'').normalize('NFKC')
    .replace(/按月|逐月|每月|月度|趋势|走势|变化|展示|统计|查询|请|帮我|给我|看看|看一下|的|全年|季度|半年|第|[一二三四上下]|从|到|至|年|月/g,'');
  // Only a single metric, time scope and presentation request; extra business
  // filters, comparisons and deliverables remain with the normal model path.
  return /^[\s\dQqHh，,。.!！?？:：()（）“”「」\-/–—~～]*$/.test(rest);
}

function currentMonthlyReceipt(events,last,current){
  const calls=new Map(events.filter(e=>e.seq>last.seq&&e.type==='tool/call').map(e=>[e.data.callId,e.data.name]));
  const results=[];
  for(const e of events){
    if(e.seq<=last.seq||e.type!=='tool/result'||e.data.message?.isError||e.surfaceOp&&e.surfaceOp!=='append')continue;
    if(!['data_metric_query','data_report','data_query','data_sql_query'].includes(calls.get(e.data.message.source?.callId)))continue;
    let r;try{r=JSON.parse(content(e.data.message));}catch{continue;}
    if(r.status==='succeeded'&&r.result_ref&&r.purpose!=='explore')results.push(r);
  }
  if(new Set(results.map(r=>r.result_ref)).size!==1)return;
  const receipt=results.at(-1),periods=receipt?.answer_contract?.requested_months;
  if(!periods?.length||!receipt.answer_contract.monthly_observations||!simpleMonthlyQuestion(current,receipt.metric_contract?.name))return;
  return {ref:receipt.result_ref,periods,receipt};
}

export function confirmedMonthlyReceipt(events){
  const users=events.filter(e=>e.type==='user/message'&&e.data.source?.kind==='user');
  const last=users.at(-1),current=content(last?.data);
  if(!last)return;
  const direct=currentMonthlyReceipt(events,last,current);
  if(direct)return direct;
  const basis=exclusiveTrend(current)?last:correction(current)&&exclusiveTrend(content(users.at(-2)?.data))?users.at(-2):null;
  if(!basis)return;
  const end=basis===last?Infinity:last.seq;
  const choices=clarificationEvidence(events.filter(e=>e.seq<end),basis.seq).clarifications;
  const timeChoices=choices.filter(c=>/年|时间|期间|月份/.test(c.question)&&years(c.answer).length===1);
  const selected=years(timeChoices.map(c=>c.answer).join(' '));
  if(selected.length!==1||basis!==last&&years(current)[0]!==selected[0])return;
  if(!timeChoices.length||!timeChoices.every(c=>fullYear(c.answer)===selected[0]))return;
  if(basis!==last&&fullYear(current)!==selected[0])return;
  const toolCalls=new Map(events.filter(e=>e.seq>basis.seq&&e.type==='tool/call').map(e=>[e.data.callId,e.data.name]));
  const candidates=[];
  for(const e of events){
    if(e.seq<=basis.seq||e.type!=='tool/result'||e.data.message.isError)continue;
    const name=toolCalls.get(e.data.message.source?.callId);
    if(!['data_metric_query','data_report','data_query','data_sql_query'].includes(name))continue;
    let r;try{r=JSON.parse(content(e.data.message));}catch{continue;}
    if(r.status!=='succeeded'||!r.result_ref||r.purpose==='explore')continue;
    // A second, different result indicates more than the one simple deliverable.
    if(!r.metric_contract?.name||!content(basis.data).includes(r.metric_contract.name)||!r.answer_contract?.monthly_observations)return;
    candidates.push(r);
  }
  const r=candidates.at(-1);
  if(!r||new Set(candidates.map(x=>[x.source_id||x.receipt?.source_id||x.metric_contract?.semantic_spec?.source_id,x.metric_contract.id].join(':'))).size!==1)return;
  if(!r.answer_contract.monthly_observations.periods.every(p=>p.startsWith(selected[0]+'-')))return;
  return {ref:r.result_ref,year:selected[0],receipt:r};
}

export function monthlyReply(selection,full){
  const {year,ref,receipt}=selection;
  const obs=receipt.answer_contract?.monthly_observations;
  if(typeof obs?.verified_text!=='string'||!obs.verified_text.trim()||!full.metric_contract?.id
    ||full.status!=='succeeded'||full.result_ref!==ref||full.truncated||full.metric_contract.id!==receipt.metric_contract?.id)return;
  const periods=selection.periods||Array.from({length:12},(_,i)=>year+'-'+String(i+1).padStart(2,'0'));
  if(periods.length<2||periods.length>120)return;
  const {unit}=full.metric_contract;
  const rows=(full.facts||[]).map(f=>[f.dimensions?.period,f.value]);
  if(!rows.length)for(const r of full.rows||[])rows.push([r.period,r.value]);
  rows.sort((a,b)=>String(a[0]).localeCompare(String(b[0])));
  if(rows.length!==periods.length||!rows.every(([p,n],i)=>p===periods[i]&&Number.isFinite(n)))return;
  // Both the current authorized result and the public receipt must agree cell for cell.
  const facts=new Map((receipt.facts||[]).map(f=>[f.dimensions?.period,f.value]));
  if(obs.result_ref!==ref||facts.size!==periods.length||JSON.stringify(obs.periods)!==JSON.stringify(periods)||!rows.every(([p,n])=>facts.get(p)===n))return;
  const safe=s=>String(s||'').replace(/[|\r\n*<>]/g,' ');
  const name=safe(full.metric_contract.name||receipt.metric_contract.name||'指标值');
  const table=['| 月份 | '+name+'（'+safe(unit)+'） |','|---|---:|',...rows.map(([p,n])=>'| '+p+' | '+n+' |')].join('\n');
  return '**'+periods[0]+' 至 '+periods.at(-1)+' · '+name+'**\n\n'+obs.verified_text+'\n\n'+table+'\n\n按所列期间和统计范围计算。';
}

export function installConfirmedMonthlyAnswer(ctx,read){
  const sessions=new Map();
  ctx.on('agent/request',async({agent},next)=>{if(agent.session.header?.agentPreset==='natural-data')sessions.set(agent.id,agent);return next();});
  ctx.on('agent/created',({agent})=>{if(agent.session.header?.agentPreset==='natural-data')sessions.set(agent.id,agent);});
  ctx.on('agent/disposed',({agent})=>sessions.delete(agent.id));
  ctx.on('llm/stream',async function*(options,next){
    const agent=sessions.get(options.sessionId);
    const selected=!options.purpose&&agent&&confirmedMonthlyReceipt(agent.session.snapshotEvents());
    if(!selected){yield* next();return;}
    let answer;
    try{
      const full=await read('result',realpathSync(agent.session.header.cwd),{result_ref:selected.ref},'',options.signal,agent.id);
      answer=monthlyReply(selected,full);
    }catch(error){
      if(options.signal?.aborted)throw error;
      answer='当前项目无法重新读取已查询的月度结果。请核对数据源授权和业务项目，再查询所选期间。';
    }
    if(!answer){yield* next();return;}
    yield {type:'block-start',index:0,blockType:'text'};
    yield {type:'text-delta',index:0,text:answer};
    yield {type:'block-end',index:0,block:{type:'text',text:answer}};
    yield {type:'finish',reason:{kind:'stop'}};
  },{prepend:true});
}
