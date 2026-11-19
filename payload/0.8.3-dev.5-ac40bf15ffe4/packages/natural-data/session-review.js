// Read a selected real turn through the host's public SessionController API.
// Browser-submitted question/answer/result objects are never used as evidence.
export function sessionTurnReview(inspection, sessionId, selectedTurn) {
  if(!inspection?.meta||!Array.isArray(inspection.events))throw Error('宿主未返回完整会话记录。');
  const events=inspection.events;
  const starts=events.filter(e=>e.type==='turn/start');
  const start=starts.find(e=>e.data.turn===Number(selectedTurn))||(!selectedTurn&&starts.at(-1));
  if(!start)throw Error('所选会话轮次不存在。');
  const next=starts.find(e=>e.seq>start.seq);
  const current=events.filter(e=>e.seq>=start.seq&&(!next||e.seq<next.seq));
  const text=e=>(e?.data?.content||e?.data?.message?.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
  const user=current.find(e=>e.type==='user/message'&&e.data.source?.kind==='user');
  if(!user||!text(user).trim())throw Error('该轮次没有可核对的原始用户问题。');
  const end=current.findLast(e=>e.type==='turn/end');
  const refs=[];
  for(const e of current)if(e.type==='tool/result'&&!e.data.message?.isError){
    for(const b of e.data.message?.content||[])if(b.type==='text')try{
      const v=JSON.parse(b.text);
      if(typeof v.result_ref==='string'&&v.purpose!=='explore')refs.push(v.result_ref);
    }catch{}
  }
  return {session_id:sessionId,workspace:inspection.meta.cwd,turn:start.data.turn,
    user_ref:user.seq,question:text(user),answer:text(current.findLast(e=>e.type==='assistant/message'&&text(e))),
    result_refs:[...new Set(refs)],termination:end?.data?.reason?.kind||'in_progress'};
}
