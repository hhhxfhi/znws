// 0.6.2: a vague request ("了解一下…情况，帮我看看") with no measure and no period
// should be clarified first. The prompt rule alone was followed only 1 time in 3
// without thinking, so the first step of such a turn gets a short reminder. It is
// a reminder, not a block: clear questions are never stopped.
import {createUserMessage} from '@deepseek-ai/dsh-llm';

export const VAGUE_SOURCE='natural-data-vague';
const BROAD=/了解一下|了解下|看看|看一下|帮我分析|分析一下|什么情况|情况怎么样|情况如何|的情况|概况|总体情况|整体情况/;
// Any measure, object list, comparison or period makes the request concrete enough.
const CONCRETE=/多少|几个|几家|几本|数量|总量|总数|金额|比例|占比|率|排名|趋势|对比|同比|环比|名单|哪些|列出|明细|分布|合计|平均|最高|最低|\d{4}\s*年|\d{1,2}\s*月|今年|去年|本月|上月|季度|年度/;

export function isVague(text){
  const value=String(text||'').trim();
  return value.length>0&&value.length<=60&&BROAD.test(value)&&!CONCRETE.test(value);
}

export function vagueNotice(){
  return createUserMessage({source:{kind:VAGUE_SOURCE,form:'notice',summary:'需求较笼统'},content:[{type:'text',text:
    '用户这句话没有说明要看的指标和期间，属于笼统需求。可以先看目录确认有哪些数据，然后用 ask_user_question 给出 2–4 个具体方向（选项用简体中文，例如数量规模、等级与专业分布、到期情况、办理情况）请用户选择，再按选择查询；不要直接输出全表概览。'}]});
}

// 0.6.3: two more first-step reminders, same approach (remind, never block).
// A pure concept question ("甲级和乙级有什么区别？") was answered with database
// statistics; a message that only supplies figures or metric names was queried and
// compared without being asked to.
const KNOWLEDGE=/区别|差别|不同|什么是|是什么意思|含义|定义|概念|怎么申请|如何申请|申请条件|需要什么条件|有什么要求|怎么办理|如何办理|流程是什么|依据是什么|解释一下/;
const DATA_WORD=/多少|几个|几家|几本|数量|总量|总数|金额|比例|占比|率|排名|趋势|对比|同比|环比|名单|哪些单位|列出|明细|分布|合计|平均|最高|最低|统计|查一下|查询|查下|数据/;
const REQUEST=/[？?]|吗|呢|多少|查|看|列|分析|比较|对比|统计|计算|帮|请|给我|展示|画|生成|怎么|如何|是否|哪些|核对|解释|总结|说明/;
const GIVEN_VALUE=/\d+(?:\.\d+)?\s*(?:件|个|家|本|项|人|次|万次|万元|亿元|元|%|％)/;
const METRIC_LINE=/[（(][^）)]{1,6}[）)]\s*$/;
// Words that can stand for several catalog measures (服务次数 or 用户数, …).
const AMBIGUOUS=/规模|体量|水平|表现|热度|活跃度/;
const MEASURE=/次数|数量|量|金额|率|人数|用户|个数|件数|总数|投入|收入/;

export function isKnowledgeOnly(text){
  const value=String(text||'').trim();
  return value.length>0&&value.length<=80&&KNOWLEDGE.test(value)&&!DATA_WORD.test(value);
}
export function isDataOnly(text){
  const value=String(text||'').trim();
  if(!value||REQUEST.test(value))return false;
  const lines=value.split(/\n+/).map(x=>x.trim()).filter(Boolean);
  // A threshold describes which records to query; it is not a supplied result.
  // Keep genuine supplied values (e.g. "合格率60%") on the purpose-confirmation path.
  const supplied=value.replace(/(?:低于|高于|大于|小于|不少于|不高于|不超过|超过|不足)\s*\d+(?:\.\d+)?\s*(?:万次|万元|亿元|件|个|家|本|项|人|次|元|%|％)/g,'');
  return GIVEN_VALUE.test(supplied)||(lines.length>=2&&lines.every(x=>METRIC_LINE.test(x)));
}
export function isAmbiguousMeasure(text){
  const value=String(text||'').trim();
  const m=value.match(AMBIGUOUS);
  if(!m)return false;
  // Any concrete measure in the message (数量、占比、次数…) already says what to count.
  return !MEASURE.test(value.replace(m[0],''))&&!/占比|比例|名单|排名|分布/.test(value);
}
function notice(kind,summary,text){return createUserMessage({source:{kind,form:'notice',summary},content:[{type:'text',text}]});}
const SHARED_CONCEPT=/合格率|财政投入|投入|支出|经费|服务次数|调用次数/;
export function intentNotices(text){
  if(isKnowledgeOnly(text))return [notice(VAGUE_SOURCE,'概念咨询','用户问的是概念、政策或资质区别这类知识问题，没有要求统计数据。直接用简体中文简要解答，说明以现行规定为准；不要调用问数工具，也不要用库里的统计数据代替解释。')];
  if(isDataOnly(text))return [notice(VAGUE_SOURCE,'只提供了数据','用户只提供了数据或指标名称，没有提出要做什么。不要查询、比对或下结论；先用 ask_user_question 询问希望怎么处理（例如：与系统数据核对、计算占比或差额、生成图表、只是记录），选项用简体中文。')];
  if(isVague(text))return [vagueNotice()];
  if(isAmbiguousMeasure(text))return [notice(VAGUE_SOURCE,'说法有多种含义','用户用了「规模、水平」这类笼统说法。先看目录和授权表字段：如果所问业务对象在多个可查指标或字段中有对应（例如同一对象的服务次数和用户数、金额和数量），才用 ask_user_question 让用户选择，选项写明各指标的含义和单位；只有一个对应指标或授权字段可支持时直接查询，并在回答中说明按哪个口径理解；若目录和授权表均无该业务对象或所需数据（或各候选均缺所需数据），不要把其他无关业务对象的指标放进 ask_user_question，直接说明具体缺少哪个业务对象、期间或来源。')];
  // 0.7.0: concepts that several databases hold as different metrics (抽检合格率, 财政投入).
  if(SHARED_CONCEPT.test(text)&&!/库|数据源/.test(text))return [notice(VAGUE_SOURCE,'核对授权目录','先调用data_catalog；未成功读取目录前，不提供任何库名或指标选项。只有返回的same_name_metrics明确列出「'+text.match(SHARED_CONCEPT)[0]+'」的多个来源，而用户没说明来源时，才用ask_user_question列出真实的「库名：指标名」让用户选择。仅一个来源则直接使用，不编造其他库。若目录及授权表均无该业务对象或所需字段，直接说明缺少的业务对象与来源，不要再就年份、期间或无关指标调用 ask_user_question。')];
  return [];
}

export function installVagueRequest(ctx){
  ctx.on('agent/pre-step',async(payload,next)=>{
    const decision=await next();
    if(payload.step!==1||payload.signal?.aborted||payload.agent?.session?.header?.agentPreset!=='natural-data'||decision?.kind==='reject')return decision;
    const user=(decision.messages||payload.messages||[]).filter(m=>m?.role==='user'&&m.source?.kind==='user').at(-1);
    const text=(user?.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
    const notices=intentNotices(text);
    if(!notices.length)return decision;
    return {...decision,messages:[...(decision.messages||[]),...notices]};
  });
}
