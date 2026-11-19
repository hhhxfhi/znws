import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {foreignText} from './answer-guard.js';
import {withNotice} from './closing.js';
export function installProcessLanguage(ctx){
 ctx.on('agent/pre-step',async({agent,turn,signal},next)=>{
  const decision=await next();
  if(signal.aborted||!/^natural-data/.test(agent.session.header?.agentPreset||''))return decision;
  const events=agent.session.snapshotEvents(),start=events.findLast(e=>e.type==='turn/start'&&e.data.turn===turn)?.seq??Infinity;
  const current=events.filter(e=>e.seq>start);
  if(current.some(e=>e.type==='user/message'&&e.data.source?.kind==='natural-data-language'))return decision;
  const drift=current.some(e=>e.type==='assistant/message'&&foreignText((e.data.message?.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('')));
  if(!drift)return decision;
  // Reuse the next already-needed step; never start another request merely to
  // translate a process message or rewrite historical evidence.
  return withNotice(decision,createUserMessage({source:{kind:'natural-data-language',form:'notice',summary:'保持简体中文'},content:[{type:'text',text:'刚才面向用户的过程说明出现了其他语言。后续过程和最终回答全部使用简体中文；SQL、字段名和引用标识保持原样。继续已有任务，无需重查、翻译或重复历史过程。'}]}));
 });
}
