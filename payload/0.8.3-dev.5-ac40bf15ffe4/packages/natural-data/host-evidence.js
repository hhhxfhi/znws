// Only successful native question results after the latest human message count.
// Questions and labels retain their relation; model tool args are never consent.
export function humanRequests(events){
 let turn;const out=[];
 for(const event of events){
  if(event.type==='turn/start')turn=event.data.turn;
  if(event.type==='user/message'&&event.data?.source?.kind==='user')out.push({...event,data:{...event.data,turn:event.data.turn??turn}});
 }
 return out;
}
export function clarificationEvidence(events, after=0){
  const calls=new Map(events.filter(e=>e.seq>after&&e.type==='tool/call'&&e.data.name==='ask_user_question').map(e=>[e.data.callId,e.data]));
  const clarifications=[];
  for(const e of events){
    const message=e.data?.message,call=calls.get(message?.source?.callId);
    if(e.type!=='tool/result'||e.seq<=after||!call||message.isError)continue;
    for(const block of message.content||[]){
      if(block.type!=='text')continue;
      try{for(const answer of JSON.parse(block.text).answers||[]){
        const args=call.args||(typeof call.arguments==='string'?JSON.parse(call.arguments):call.arguments);
        const q=args?.questions?.find(q=>q.id===answer.id);
        if(!q)continue;
        const selected=(answer.selected||[]).filter(x=>typeof x==='string');
        const text=typeof answer.custom==='string'&&answer.custom.trim()?answer.custom:selected.join(' ');
        if(text)clarifications.push({call_id:message.source.callId,question_id:q.id,question:q.question,answer:text,selected_labels:selected});
      }}catch{/* malformed results never become confirmation */}
    }
  }
  return {clarifications,confirmation:clarifications.map(r=>r.answer).join('\n')};
}

// History-dependent snapshots can keep the original scope through repeated
// identical requests without increasing other tools' raw-history window.
export function repeatedSnapshotRequests(events, users, limit=16) {
 const latest=users.at(-1), previous=users.at(-2);
 const exact=user=>{
  const blocks=user?.data?.content;
  if(!blocks?.length||blocks.some(block=>block.type!=='text'||typeof block.text!=='string'))return '';
  return blocks.map(block=>block.text).join('\n');
 };
 const text=exact(latest);
 if(users.length<=limit+1||!text.trim()||text.length>2000||text!==exact(previous))return null;
 const completed=new Set(), questioned=new Set();
 let turn;
 for(const event of events){
  if(event.seq>=latest.seq||event.surfaceOp&&event.surfaceOp!=='append')continue;
  if(event.type==='turn/start')turn=event.data.turn;
  if(event.type==='turn/end'&&event.data.reason?.kind==='completed')completed.add(event.data.turn);
  if(event.type==='tool/call'&&event.data.name==='ask_user_question')questioned.add(event.data.turn??turn);
 }
 let index=users.length-2, next=latest;
 for(;index>=0;index--){
  const user=users[index], {turn,id}=user.data;
  if(!id||turn!==next.data.turn-1||!completed.has(turn)||questioned.has(turn)||exact(user)!==text)break;
  next=user;
 }
 if(index<0||index>=users.length-3)return null;
 return [...users.slice(Math.max(0,index-limit+2),index+1),previous];
}

export function completedWithoutTools(events, user, nextUser) {
  const turn=user?.data?.turn;
  if(!Number.isInteger(turn)||!user.data.id||!nextUser?.data?.id
    ||nextUser.data.id===user.data.id||nextUser.data.turn!==turn+1||user.seq>=nextUser.seq)return false;
  let answer=false, ended=false;
  for(const event of events){
    if(event.seq<=user.seq||event.seq>=nextUser.seq||event.surfaceOp&&event.surfaceOp!=='append')continue;
    if(event.type==='tool/call'||event.type==='tool/result'||event.type==='user/message')return false;
    if(event.data?.turn!==turn)continue;
    if(event.type==='assistant/message'&&!ended){
      const blocks=event.data.message?.content||[];
      if(blocks.some(block=>block.type==='tool-call'))return false;
      if(blocks.some(block=>block.type==='text'&&typeof block.text==='string'&&block.text.trim()))answer=true;
    }
    if(event.type==='turn/end'){
      if(ended||event.data.reason?.kind!=='completed')return false;
      ended=true;
    }
  }
  return answer&&ended;
}
