import {humanRequests} from './host-evidence.js';
import {continuation,messageText} from './request-context.js';

const bullet=/^\s*[-*•]\s+/;
const bare=/^\s*[-*•]\s*(?:\*\*|__)?\s*$/;
const unfinished=line=>bare.test(line)||/^\s*[-*•]\s+(?:\*\*[^*\n]+|__[^_\n]+)$/.test(line);

export function stitchContinuationMarkdown(priorText,continuationText){
  const prev=String(priorText||'').trimEnd(),next=String(continuationText||'').trimStart();
  if(!prev||!next)return prev||next;
  const lines=prev.split('\n'),last=lines.at(-1);
  if(bullet.test(next)&&(bare.test(last)||unfinished(last)&&next.startsWith(last.trimStart()))){
    lines.pop();return [lines.join('\n').trimEnd(),next].filter(Boolean).join('\n\n');
  }
  // A continuation in the middle of a word/emphasis keeps every existing
  // character. Never delete a partial label just because it is short.
  return prev+(unfinished(last)&&!bullet.test(next)?'':'\n\n')+next;
}

function interruptedPrefix(events){
  const users=humanRequests(events),last=users.at(-1),retry=continuation(messageText(last));
  if(!last||!retry||retry.scopeChanged)return;
  const target=retry.turn?users.findLast(u=>u.seq<last.seq&&u.data.turn===retry.turn):users.at(-2);
  if(!target)return;
  const ended=events.findLast(e=>e.seq<last.seq&&e.type==='turn/end'&&e.data.turn===target.data.turn);
  if(!ended||!['error','max-tokens','aborted','canceled','interrupted'].includes(ended.data.reason?.kind))return;
  const visible=e=>e.type==='assistant/message'&&(!e.surfaceOp||e.surfaceOp==='append')&&
    (e.data.message?.content||[]).some(b=>b.type==='text'&&b.text.trim());
  if(events.some(e=>e.seq>last.seq&&visible(e)))return;
  const previous=events.findLast(e=>e.seq<ended.seq&&e.data.turn===target.data.turn&&visible(e));
  const text=(previous?.data.message.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
  if(unfinished(text.trimEnd().split('\n').at(-1)||''))return text;
}

// The transformed native stream is what Harness assembles, displays and saves.
// Previously saved events remain immutable; no synthetic assistant event or
// second conversation renderer is introduced.
export function installContinuationMarkdown(ctx){
  const sessions=new Map();
  const remember=agent=>{if(agent.session.header?.agentPreset==='natural-data')sessions.set(agent.id,agent);};
  ctx.on('agent/request',async({agent},next)=>{remember(agent);return next();});
  ctx.on('agent/created',({agent})=>remember(agent));
  ctx.on('agent/disposed',({agent})=>sessions.delete(agent.id));
  ctx.on('llm/stream',async function*(options,next){
    const agent=sessions.get(options.sessionId);
    const prior=!options.purpose&&agent&&interruptedPrefix(agent.session.snapshotEvents());
    if(!prior){yield*next();return;}
    const chunks=[];let bytes=0,passthrough=false;
    try{
      for await(const chunk of next()){
        if(passthrough){yield chunk;continue;}
        chunks.push(chunk);bytes+=JSON.stringify(chunk).length;
        if(bytes>512*1024){yield*chunks;chunks.length=0;passthrough=true;}
      }
    }catch(error){yield*chunks;throw error;}
    if(passthrough)return;
    const starts=chunks.filter(c=>c.type==='block-start');
    const end=chunks.find(c=>c.type==='block-end'&&c.block.type==='text');
    // Reasoning blocks pass through without inspection or modification. Tool
    // calls and incomplete attempts retain their exact provider stream.
    const textStarts=starts.filter(c=>c.blockType==='text');
    const last=prior.trimEnd().split('\n').at(-1);
    const nextText=end?.block.text.trimStart()||'';
    const completesEmphasis=/\*\*|__/.test(last)&&/^[^*_\n。!?！？]{1,100}(?:\*\*|__)(?:[:：，,\s]|$)/.test(nextText);
    const joins=bullet.test(nextText)?bare.test(last)||nextText.startsWith(last.trimStart()):completesEmphasis;
    if(textStarts.length!==1||starts.some(c=>!['text','reasoning'].includes(c.blockType))||!end||
       !joins||
       chunks.at(-1)?.type!=='finish'||chunks.at(-1).reason.kind!=='stop'){
      yield*chunks;return;
    }
    const text=stitchContinuationMarkdown(prior,end.block.text);
    let emitted=false;
    for(const chunk of chunks){
      if(chunk.type==='text-delta'||chunk===end){
        if(!emitted){yield {type:'text-delta',index:end.index,text};emitted=true;}
        if(chunk===end)yield {...end,block:{...end.block,text}};
      }else if(chunk.type==='finish'){
        const {replayState,...finish}=chunk;yield finish;
      }else yield chunk;
    }
  },{prepend:true});
}
