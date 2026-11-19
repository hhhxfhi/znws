// Use Harness's request recovery boundary. No second model or agent loop.
import {randomUUID} from 'node:crypto';
import {installBudget,overflowBudget,contextPolicy,optionalService} from './budget.js';
const codes=new Set(['MALFORMED_RESPONSE','NETWORK_ERROR','TIMEOUT','RATE_LIMITED','SERVER_ERROR','TRANSPORT','RATE_LIMIT','SERVER','EMPTY_RESPONSE']);
export function installRecovery(ctx,options={}){
  installBudget(ctx,options);
  ctx.on('agent/request-error',async({agent,turn,step,provider,failure,signal},next)=>{
    if(agent.session.header?.agentPreset!=='natural-data')return next();
    if(failure.code==='CONTEXT_BUDGET_EXHAUSTED')return;
    if(failure.code==='CONTEXT_WINDOW_EXCEEDED'&&agent.session.header?.agentPreset==='natural-data'){
      if(signal.aborted)return;
      const events=agent.session.snapshotEvents();
      if(events.some(e=>e.type==='llm/retry'&&e.data.turn===turn&&e.data.policyKey===contextPolicy))return;
      const budget=overflowBudget(failure);
      let changed=Boolean(budget&&budget.input+Math.max(1024,budget.window*.05)+256<budget.window);
      const compaction=optionalService(ctx,'compaction');
      if(!changed&&compaction?.compactIfNeeded){
        try{changed=Boolean(await compaction.compactIfNeeded(agent,'context-overflow',signal));}catch{if(signal.aborted)return;}
      }
      // Presets may isolate the compaction service inside a sibling group.
      // Its native recovery listener can still operate in that exact scope.
      if(!changed&&!compaction){
        const generation=agent.session.surface?.replaceGeneration;
        const action=await next();
        changed=action?.kind==='retry'&&generation!==undefined&&agent.session.surface?.replaceGeneration>generation;
      }
      if(!changed)return; // No identical request retry or extra downstream loop.
      const retryId=randomUUID();
      agent.session.append('llm/retry',{retryId,turn,step,provider,mode:'normal',policyKey:contextPolicy,retry:1,maxRetries:1,delayMs:0,failure});
      agent.session.append('llm/retry-started',{retryId,turn,step,retry:1});
      return {kind:'retry'};
    }
    if(!codes.has(failure.code))return next();
    if(signal.aborted)return;
    const events=agent.session.snapshotEvents();
    const key='natural-work-bounded-recovery-v2';
    // A single retry per turn, durable across process restart. Owning this code
    // path also prevents downstream provider policies from adding extra retries.
    if(events.some(e=>e.type==='llm/retry'&&e.data.turn===turn&&e.data.policyKey===key))return;
    const retryId=randomUUID();
    agent.session.append('llm/retry',{retryId,turn,step,provider,mode:'normal',policyKey:key,retry:1,maxRetries:1,delayMs:0,failure});
    agent.session.append('llm/retry-started',{retryId,turn,step,retry:1});
    return {kind:'retry'};
  },{prepend:true});
}
