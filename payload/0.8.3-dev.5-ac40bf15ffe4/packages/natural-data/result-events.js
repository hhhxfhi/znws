import {continuation,messageText} from './request-context.js';
import {isExploratory} from './presentation-intent.js';
import {formatReceiptDelivery,groupedComparisonSection,withDefinitionNotice} from './answer-delivery.js';
import {mergeResultReceipt} from './result-lineage.js';
const RESULT_TOOLS=new Set(['data_query','data_report','data_sql_query','data_metric_query','data_derive_aggregation',
  'data_visualize','data_analyze','data_inspect','analysis_compute','analysis_run','analysis_method','data_make_report','data_edit_report']);
const DATA_TURN_TOOLS=new Set([...RESULT_TOOLS,'data_catalog']);
export const resultRequestEvents={
 kind:'natural-data-request',
 match:event=>event.type==='user/message'&&event.data?.source?.kind==='user'?{id:event.data.id||String(event.seq),role:'start'}:null,
 start:(_context,{event})=>({text:messageText(event),id:event.data.id}),
 update:(_context,{event})=>({text:messageText(event),id:event.data.id})
};

function comparisonPreviewRef(results,text,userText,requestId){
 const matched=new Set();
 for(const query of results){
  const result=query.value,lineage=result.result_lineage;
  if(query.name!=='data_metric_query'||query.reused||!result.execution_scope
    ||lineage&&(lineage.purpose!=='answer'||lineage.request_ref!==requestId||lineage.source_id!==result.receipt?.source_id))continue;
  const analysis=results.filter(item=>item.seq>query.seq&&item.name==='analysis_compute'&&!item.reused
    &&item.value.result_ref===result.result_ref&&item.value.execution_scope===result.execution_scope
    &&item.value.execution?.operation==='analysis.compute'&&Array.isArray(item.value.claims));
  if(!analysis.length)continue;
  const bound=analysis.reduce((value,item)=>mergeResultReceipt(value,{claims:item.value.claims}),result);
  const section=groupedComparisonSection(bound,userText||'');
  // Use the same checked presentation as the guard, then retain only the
  // exact formal receipt that produced the delivered text.
  if(section&&text===withDefinitionNotice(formatReceiptDelivery([section]),section))matched.add(result.result_ref);
 }
 return matched.size===1?matched.values().next().value:null;
}

export function forecastClaimsByResult(items){
  const byRef=new Map();
  for(const item of items){
    const value=item.value;
    if(item.name!=='analysis_method'||value?.status!=='succeeded'||!value.result_ref||isExploratory(value))continue;
    for(const claim of value.claims||[]){
      const stats=claim?.statistics;
      if(claim?.operation!=='matrix_method'||!/^claim:[0-9a-f]{20}$/.test(claim.claim_id)||
         (claim.method_id!=='matrix.42'&&!(typeof claim.method_id==='string'&&claim.method_id.startsWith('custom.')&&stats?.kind==='forecast'))||
         (stats?.source_result_ref&&stats.source_result_ref!==value.result_ref))continue;
      const ids=byRef.get(value.result_ref)||new Set();
      ids.add(claim.claim_id);
      byRef.set(value.result_ref,ids);
    }
  }
  return byRef;
}

const MUTATION_TOOLS=new Set(['data_visualize','data_analyze','data_make_report','data_edit_report','analysis_compute','analysis_method']);
// Per-session native event projection. Explicit retries reuse successful business
// receipts; ordinary follow-ups never inherit cards. Reload uses the same events.
export function resultEvents(publicUrls,hooks){
 const reuse=(state,text)=>{
  const retry=continuation(text);
  const prior=retry&&!retry.scopeChanged?(retry.turn?state.prior.find(p=>p.turn===retry.turn):state.prior.at(-1)):null;
  return {...state,userText:prior?.userText||text,
    reused:prior?(prior.results||[]).filter(r=>!isExploratory(r.value)).map(r=>({...r,failure:false,reused:true})):[]};
 };
 const definition={
  kind:'natural-data-results',
  match(event){
   // A native user/message has no turn field. Start at the first assistant or
   // turn end, after its user Context exists, using the documented reader.
   if(event.type==='assistant/message'||event.type==='turn/end')return {id:String(event.data.turn),role:'start'};
   if(event.type==='tool/call'||(event.type==='tool/result'&&event.surfaceOp==='append'))return {id:String(event.data.turn),role:'update'};
   return null;
  },
  start(context,match,reader){
   const prev=reader?.previous('natural-data-results')?.state;
   const prior=prev?[...(prev.prior||[]),{turn:prev.turn,userText:prev.userText,results:[...(prev.reused||[]),...prev.results]}].slice(-8):[];
   const state={turn:match.event.data.turn,calls:new Map(),results:[],reused:[],prior};
   const user=reader?.previous('natural-data-request')?.state;
   return definition.update({state:user?{...reuse(state,user.text),requestId:user.id}:state},match);
  },
  update(context,{event}){
   const state=context.state;
   if(event.type==='turn/end'){
    const failed=['error','max-tokens'].includes(event.data.reason?.kind);
    return {...state,failed,ended:true,results:state.results.map(item=>({...item,failure:failed}))};
   }
   if(event.type==='tool/call'){
    const calls=new Map(state.calls);calls.set(String(event.data.callId),event.data.name);
    const preview=state.pendingComparisonPreview;
    if(preview&&event.data.name==='ask_user_question'&&event.data.callId===preview.callId){
     return {...state,calls,pendingComparisonPreview:null,comparisonPreview:{ref:preview.ref,seq:event.seq}};
    }
    return {...state,calls,pendingComparisonPreview:null};
   }
   if(event.type==='assistant/message'){
    const blocks=event.data.message?.content||[];
    const text=blocks.filter(b=>b.type==='text').map(b=>b.text).join('');
    if(!text.trim())return state;
    const ask=blocks.find(block=>block.type==='tool-call'&&block.name==='ask_user_question');
    if(ask){
     const ref=ask.id&&comparisonPreviewRef(state.results,text,state.userText,state.requestId);
     return {...state,answer:text,preClarificationAnswer:text,
       pendingComparisonPreview:ref?{ref,callId:ask.id}:null};
    }
    const choiceOnly=/尚未选定，待确认口径后可继续核算。$/.test(text)&&!text.includes('\n');
    return {...state,answer:choiceOnly&&state.preClarificationAnswer?state.preClarificationAnswer+'\n\n'+text:text,
      pendingComparisonPreview:null};
   }
   if(event.type!=='tool/result'||event.data.message.isError)return state;
   const message=event.data.message,name=state.calls.get(String(message.source?.callId));
   if(!RESULT_TOOLS.has(name))return state;
   let value;try{value=JSON.parse(message.content.find(x=>x.type==='text')?.text||'{}');}catch{return state;}
   if(!value.result_ref||!value.view_url||value.status!=='succeeded'||!publicUrls.result(value.view_url))return state;
   if(MUTATION_TOOLS.has(name))hooks?.onMutation?.(value,name,event.seq);
   return {...state,results:[...state.results,{seq:event.seq,name,value}]};
  },
  buildLocationData(context,scope,previous){
   if(scope!=='turn'||!context.state)return null;
   const state=context.state;
   const attempted=[...state.calls.values()].some(name=>DATA_TURN_TOOLS.has(name));
   if(previous?.ownResults===state.results&&previous?.reused===state.reused&&previous?.value?.answer===(state.answer||'')
      &&previous?.value?.comparisonPreview===state.comparisonPreview
      &&previous?.value?.userText===(state.userText||'')
      &&previous?.value?.failed===state.failed&&previous?.value?.attempted===attempted&&previous?.value?.ended===state.ended)return previous;
   return {kind:'turn',turn:state.turn,key:'natural-data-results',ownResults:state.results,reused:state.reused,
    value:{results:[...state.reused,...state.results],answer:state.answer||'',comparisonPreview:state.comparisonPreview,
     failed:state.failed,userText:state.userText||'',
     attempted,ended:state.ended}};
  }
 };
 return definition;
}
