// Only an explicit retry may reuse a previous human request. A new question,
// added filter, or topic change starts its own scope.
import {humanRequests} from './host-evidence.js';
const RETRY=/^(?:请)?继续(?:完成)?(?:上一题|上个问题|原问题|未完成的回答|(?:(?:前面|之前|刚才)(?:的)?)?(?:全省统计|统计|回答|查询))?[。！!]?$/;
const NATIVE=/^继续完成第(\d+)轮未完成的回答。先复用本会话已成功的查询结果和工序回执；除非结果确实缺失或条件改变，不要重放查询。无法确认的解释请明确说明。$/;
export const messageText=e=>(e?.data?.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
export function continuation(text){const value=String(text||'').trim(),match=NATIVE.exec(value);return match?{turn:Number(match[1])}:RETRY.test(value)?{scopeChanged:value.includes('全省')}:null;}
export function requestContext(events){
 const users=humanRequests(events);
 let index=users.length-1;const refinements=[],requests=[];
 while(index>=0){
  const user=users[index],text=messageText(user),retry=continuation(text);
  requests.push(user);
  if(!retry)return {text:[text,...refinements.reverse()].join('\n'),requests,users};
  if(retry.scopeChanged)refinements.push(text);
  if(retry.turn){const target=users.findLastIndex((e,i)=>i<index&&e.data.turn===retry.turn);if(target<0)return {text,requests,users};index=target;}
  else index--;
 }
 return {text:'',requests,users};
}
export function requestText(events){return requestContext(events).text;}
