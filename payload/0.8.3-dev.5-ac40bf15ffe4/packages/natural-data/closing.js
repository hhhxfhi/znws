// 0.6.2 closing mode. When a turn stops making progress or the context is
// nearly full, stop the data tools and ask the model to answer from what it
// already has, instead of ending the turn with an error and no answer.
import {createUserMessage} from '@deepseek-ai/dsh-llm';

export const CLOSING_SOURCE='natural-data-closing';
// Extra steps allowed after closing starts. Past this the original error ends the turn.
export const CLOSING_STEPS=3;
const KEEP=new Set(['ask_user_question']);
const states=new WeakMap();
const bySession=new Map();

export function closingText(reason){
  return '【本轮进入收尾】'+reason+'。问数工具已暂停，请不要再调用工具，直接用中文回答用户：\n'+
    '1. 已经查到的结论：只用本轮工具回执里的数值，写清期间和单位；\n'+
    '2. 还没完成的部分，以及卡在哪一步（例如字段为空、找不到对应的表、条件在数据里无法表达）；\n'+
    '3. 只有缺少会改变结果的业务条件时，才指出需要确认的具体选项。执行故障用一句业务语言说明，保留可核实的结论。\n'+
    '不要编造数值，不要用其他年份、其他对象或全表合计代替用户要的结果。';
}

export function closingState(agent,turn){
  const state=states.get(agent);
  return state&&state.turn===turn?state:null;
}

// Restrict one name at a time: the host rejects a whole list when any name is
// not restrictable, and a silent no-op would leave every tool visible.
function restrict(agent){
  const tools=agent.ctx?.tools;
  if(!tools?.restrict||!tools.schemas)return ()=>{};
  let names=[];try{names=tools.schemas(agent).map(t=>t.name).filter(name=>!KEEP.has(name));}catch{return ()=>{};}
  const undo=[];
  for(const name of names){try{undo.push(tools.restrict({deny:[name]}));}catch{}}
  return ()=>{for(const fn of undo.splice(0))try{fn();}catch{}};
}

// Enter closing for this turn. Returns the notice to add to this step's input,
// or null when closing is already active for the turn.
export function enterClosing(agent,turn,reason,reasonCode){
  const old=states.get(agent);
  if(old&&old.turn===turn)return null;
  old?.dispose?.();
  const text=closingText(reason);
  states.set(agent,{turn,reason,reasonCode,text,steps:0,dispose:restrict(agent)});
  if(agent.session?.id)bySession.set(agent.session.id,agent);
  return createUserMessage({source:{kind:CLOSING_SOURCE,form:'notice',summary:'本轮进入收尾'},content:[{type:'text',text}]});
}

export function withNotice(decision,notice){
  if(!notice||!decision||decision.kind==='reject')return decision;
  return {...decision,messages:[...(decision.messages||[]),notice]};
}

export function leaveClosing(agent){
  states.get(agent)?.dispose?.();
  states.delete(agent);
  if(agent?.session?.id&&bySession.get(agent.session.id)===agent)bySession.delete(agent.session.id);
}

// Only the answer guard's existing two-attempt repair budget may reopen a
// progress stop. Context-window stops and normal permission checks remain intact.
export function resumeQueryRepair(agent,turn){
  if(closingState(agent,turn)?.reasonCode!=='query-progress')return false;
  leaveClosing(agent);return true;
}

// Whether the turn's latest model output asked for tools, i.e. the turn is
// still running. Closing is pointless after the model already answered.
export function turnStillWorking(events,turn){
  const start=[...events].reverse().find(e=>e.type==='turn/start'&&e.data.turn===turn);
  const last=events.filter(e=>e.seq>(start?.seq??-1)&&e.type==='assistant/message').at(-1);
  // No model output yet: working only if tool results exist (step 1 of a turn
  // has none, so it has nothing to answer from and is never closed).
  if(!last)return events.some(e=>e.seq>(start?.seq??-1)&&e.type==='tool/result');
  return (last.data.message?.content||[]).some(b=>b.type==='tool-call');
}

export function installClosing(ctx){
  // Tools are assembled before agent/pre-step, so the restriction only takes
  // effect on the next step. Deny calls made in the step that started closing.
  ctx.on('tools/pre-execute',async(exec,next)=>{
    const state=exec.agent&&states.get(exec.agent);
    if(!state||KEEP.has(exec.name))return next();
    const turn=exec.agent.session.snapshotEvents().filter(e=>e.type==='turn/start').at(-1)?.data.turn;
    if(state.turn!==turn)return next();
    return {kind:'deny',reason:'CLOSING: '+state.text};
  },{prepend:true});
  // The host assembles a step's tools before agent/pre-step, so the restriction
  // must be lifted when the turn ends, not at the next turn's first pre-step.
  ctx.on('session/event',(session,event)=>{
    if(event?.type!=='turn/end')return;
    const agent=bySession.get(session?.id);
    if(agent&&states.get(agent)?.turn===event.data?.turn)leaveClosing(agent);
  });
  ctx.on('agent/disposed',({agent})=>leaveClosing(agent));
}
