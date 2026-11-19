import {clarificationEvidence} from './host-evidence.js';
import {isDataOnly} from './vague.js';
import {messageText,requestText} from './request-context.js';
// Domain scheduling policy at the native tool boundary. No synthetic user reply.
const clearAliases=[['inspection_pass_rate',/抽检合格率|监督检查合格率/],['confidential_approvals',/涉密测绘成果.*(?:审批|办理)/],['map_reviews',/地图审核/],['tianditu_services',/天地图.*服务次数/],['certificates',/测绘资质.*(?:数量|多少)/]];
function values(events,name,start){const ids=new Set(events.filter(e=>e.seq>start&&e.type==='tool/call'&&e.data.name===name).map(e=>e.data.callId));return events.filter(e=>e.seq>start&&e.type==='tool/result'&&ids.has(e.data.message.source?.callId)&&!e.data.message.isError).flatMap(e=>{try{return [JSON.parse(e.data.message.content.find(x=>x.type==='text')?.text||'{}')];}catch{return [];}});}
function coverageIncludesYear(coverage,year){
 if(!coverage)return true;
 const y=Number(year);
 if(Array.isArray(coverage))return coverage.includes(String(year))||coverage.some(p=>String(p).startsWith(year+'-'));
 if(typeof coverage==='string'){
  if(coverage.includes(String(year)))return true;
  const range=coverage.match(/(20\d{2})(?:-\d{2})?\s*[~～-]\s*(20\d{2})/);
  if(range&&y>=Number(range[1])&&y<=Number(range[2]))return true;
  return false;
 }
 return true;
}
export function pendingClearDelivery(events){
 const user=events.filter(e=>e.type==='user/message'&&e.data.source?.kind==='user').at(-1);if(!user)return [];
 const text=user.data.content.filter(x=>x.type==='text').map(x=>x.text).join('\n');
 // Only independent clear clauses are gated. Source/period ambiguity still reaches Harness clarification.
 const clauses=text.split(/[。；;\n]|另外|再帮(?:忙|我)?|以及/).map(x=>x.trim()).filter(Boolean);
 const hasVagueClause=clauses.some(c=>(/基础测绘/.test(c)&&/情况|分析|了解/.test(c)&&!/(投入|资金|验收|完成率|全部指标)/.test(c))||(/整体情况|总体情况|发展情况|运营情况|业务情况/.test(c)&&/分析|了解|介绍|看看/.test(c)&&!/20\d{2}/.test(c)));
 if(clauses.length<2||!hasVagueClause)return [];
 const catalog=values(events,'data_catalog',user.seq).at(-1);if(!catalog)return [];
 const delivered=['data_report','data_query','data_metric_query','data_sql_query'].flatMap(name=>values(events,name,user.seq));
 const dynamicAliases=[];
 for(const s of catalog.sources||[]){
  for(const m of [...(s.metric_definitions||[]),...(s.semantic_metrics||[])]){
   if(!m?.id||m.also_in?.length||clearAliases.some(([cid])=>cid===m.id))continue;
   const terms=[m.name,...(m.aliases||[])].map(x=>String(x||'').replace(/[（(][^）)]*[）)]/g,'').trim()).filter(x=>x.length>=3);
   if(terms.length)dynamicAliases.push([m.id,{test:clause=>terms.some(t=>clause.includes(t))}]);
  }
 }
 const allAliases=[...clearAliases,...dynamicAliases];
 const pending=[];
 for(const clause of clauses){const years=[...clause.matchAll(/(20\d{2})年/g)].map(x=>x[1]);if(new Set(years).size!==1)continue;
  for(const [id,alias]of allAliases){if(!alias.test(clause))continue;
   const sources=(catalog.sources||[]).filter(s=>s.metric_definitions?.some(m=>m.id===id)||s.semantic_metrics?.some(m=>m.id===id));if(sources.length!==1)continue;
   if(sources[0].coverage?.complete_years&&!sources[0].coverage.complete_years.includes(Number(years[0])))continue;
   const semMetric=sources[0].semantic_metrics?.find(m=>m.id===id);
   if(semMetric?.coverage&&!coverageIncludesYear(semMetric.coverage,years[0]))continue;
   if(delivered.some(r=>['failed','cancelled','execution_unknown'].includes(r.status)&&r.metric_contract?.id===id))continue;
   const fulfilled=delivered.some(r=>r.status==='succeeded'&&r.metric_contract?.id===id&&(r.metric_contract.periods?.includes(years[0])||Array.from({length:12},(_,j)=>years[0]+'-'+String(j+1).padStart(2,'0')).every(p=>r.metric_contract.periods?.includes(p))));
   if(!fulfilled&&!pending.some(p=>p.metric===id&&p.year===years[0]))pending.push({metric:id,year:years[0]});
  }
 }
 return pending;
}
// 0.6.2: small models without thinking sometimes switch language in questions
// (Korean options were seen). Users read Chinese only.
const FOREIGN_SCRIPT=/[\u0370-\u052f\u0600-\u06ff\u0e00-\u0e7f\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\uac00-\ud7af]/;
export function foreignQuestion(args){
 try{if(typeof args==='string')args=JSON.parse(args);}catch{return false;}
 const han=/\p{Script=Han}/u;
 const foreignLabel=value=>{
  const text=String(value||'');
  if(FOREIGN_SCRIPT.test(text))return true;
  if(han.test(text))return false;
  // Keep catalog identifiers, dates and technical acronyms intact. Natural
  // language labels need a Chinese explanation; JSON keys/ids are not UI text.
  const words=text.match(/\p{L}+/gu)||[];
  return words.length>1&&!words.every(word=>/^[A-Z]+$/.test(word))&&!/^[\w.:-]+$/.test(text);
 };
 return (Array.isArray(args?.questions)?args.questions:[]).some(q=>{
  const question=String(q.question||'');
  if(question.trim()&&(!han.test(question)||FOREIGN_SCRIPT.test(question)))return true;
  if(foreignLabel(q.header))return true;
  return (Array.isArray(q.options)?q.options:[]).some(o=>foreignLabel(typeof o==='string'?o:[o.label,o.description].filter(Boolean).join(' ')));
 });
}
// 0.6.3: without thinking, the model once asked "医疗器械、食品还是药品抽检？" before
// looking at the catalog at all; the options were invented. A clarifying question
// about data needs the catalog first. Only data-only input (figures with no task)
// may be asked about straight away.
export function questionBeforeCatalog(events,args){
 if(events.some(e=>e.type==='tool/call'&&e.data?.name==='data_catalog'))return false;
 const user=events.filter(e=>e.type==='user/message'&&e.data?.source?.kind==='user').at(-1);
 const text=requestText(events);
 const questions=args?.questions;
 // Choosing a year the human explicitly supplied is not a catalog claim.
 // Business, region and metric options still require the authorized catalog.
 const years=new Set(text.match(/20\d{2}/g)||[]);
 if(Array.isArray(questions)&&questions.length&&years.size&&questions.every(q=>{
   if(!/年份|哪年|年度|截止|截至|日期|年末|年底/.test(q.question||''))return false;
   const options=q.options||[];
   if(!Array.isArray(options)||!options.length)return false;
   return options.every(o=>{
    const label=typeof o==='string'?o:o.label;
    const cleaned=String(label||'').replace(/\s+/g,'').replace(/[（(](?:按|截至|截止)?(20\d{2})(?:[-/]12[-/]31|年12月31日)(?:有效(?:口径)?)?[）)]/g,(all,year)=>years.has(year)&&/年末|年底/.test(text)?'':all);
    const match=/^\s*(20\d{2})(?:年(?:底|末|12月31日)?|[-/]12[-/]31)?(?:[（(](?:推荐|recommended)[）)])?\s*$/i.exec(cleaned);
    const dated=(label||'').match(/20\d{2}/g)||[];
    return match&&years.has(match[1])&&dated.every(y=>y===match[1]);
   })&&((q.question||'').match(/数据源|指标|口径|地市|地区|行业|专业|甲级|乙级/g)||[]).every(term=>text.includes(term));
 }))return false;
 return !isDataOnly(text);
}
// A completed, single-year query does not create a new year ambiguity. Keep
// genuine multi-year choices, relative dates and mixed requests available.
export function redundantYearQuestion(events,args){
 const lastUser=events.findLast(e=>e.type==='user/message'&&e.data?.source?.kind==='user');
 if(!lastUser)return false;
 const text=(lastUser.data.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
 const years=[...new Set(text.match(/20\d{2}/g)||[])];
 if(years.length!==1||/最新|今年|去年|上年|哪年|哪一年|或者|或是|另外|另查|再查/.test(text))return false;
 const questions=args?.questions;
 if(!Array.isArray(questions)||!questions.length||!questions.every(q=>/哪一?年|选择年份|选择年度/.test(q.question||'')&&!/口径|数据源|专业|地市|地区|行业/.test(q.question||'')))return false;
 return values(events,'data_metric_query',lastUser.seq).some(r=>r.status==='succeeded'&&r.facts?.length&&
  r.facts.every(f=>String(f.dimensions?.period||'').startsWith(years[0])));
}

const questionName=value=>String(value||'').normalize('NFKC').replace(/[\s？?。]/g,'');
const optionName=value=>String(typeof value==='string'?value:value?.label||'').normalize('NFKC')
 .replace(/\s*[(](?:推荐|recommended)[)]\s*$/i,'').replace(/\s+/g,'');
function callArguments(call){
 try{
  const args=call.args??call.arguments;
  return (typeof args==='string'?JSON.parse(args):args)||{};
 }catch{return {};}
}
function questionItems(call){
 const questions=callArguments(call).questions;
 return Array.isArray(questions)?questions:[];
}

export function independentComparisonCatalogTool(user, current){
 const question=messageText(user);
 if(current.some(event=>event.type==='tool/call')||question.length>200)return null;
 const text=question.normalize('NFKC');
 const annual=/20\d{2}年[^。!?;\n]{2,48}(?:比|与)(?:去年|上年|20\d{2}年)[^。!?;\n]{2,18}[。!?;\n]/;
 const monthly=/20\d{2}年\d{1,2}月[^。!?;\n]{2,38}比(?:20\d{2}年)?\d{1,2}月(?:变化|增减|相差)多少[。!?;\n]/;
 if(!(annual.test(text)||monthly.test(text))
   ||!/[。!?;\n][^。!?;\n]{0,10}20\d{2}年[^。!?;\n]{2,36}(?:多少|是多少)[。!?;\s]*$/.test(text))return null;
 return {name:'data_catalog',args:{question}};
}

export function independentComparisonPlan(catalogs, requestRef){
 for(const catalog of [...catalogs].reverse()){
  if(catalog.status!=='succeeded'||catalog.sources?.length!==1||catalog.catalog_view?.next_source_offset!=null)continue;
  const [source]=catalog.sources;
  const plan=source.independent_metric_comparison, publication=source.source_context?.business_project||source.business_project;
  if(!requestRef||!source.id||plan?.schema_version!==1||plan.request_ref!==requestRef||plan.source_id!==source.id
    ||!plan.publication?.id||!Number.isSafeInteger(plan.publication.version)
    ||(publication&&(publication.id!==plan.publication.id||publication.version!==plan.publication.version)))continue;
  const {comparison,choice}=plan;
  const year=value=>typeof value==='string'&&/^20\d{2}$/.test(value);
  const month=value=>typeof value==='string'&&/^20\d{2}-(?:0[1-9]|1[0-2])$/.test(value);
  const query=(value,periods,grouped=false)=>value?.source_id===source.id&&typeof value.metric_id==='string'
    &&typeof value.metric_version==='string'&&typeof value.title==='string'&&Array.isArray(value.periods)
    &&value.periods.length===periods.length&&value.periods.every((period,index)=>period===periods[index])
    &&(!grouped||Array.isArray(value.group_by)&&value.group_by.length===1&&/^[a-z][a-z0-9_]{0,63}$/i.test(value.group_by[0]))
    &&!Object.keys(value).some(key=>!['source_id','metric_id','metric_version','title','periods'].includes(key)
      &&!(grouped&&key==='group_by'));
  const grouped=month(comparison?.baseline)&&month(comparison?.current);
  if((!grouped&&(!year(comparison?.baseline)||!year(comparison?.current)))||comparison.baseline===comparison.current
    ||!query(comparison.query_request,[comparison.baseline,comparison.current],grouped))continue;
  if(grouped&&(!Array.isArray(comparison.categories)||comparison.categories.length<2||comparison.categories.length>6
    ||new Set(comparison.categories).size!==comparison.categories.length
    ||comparison.categories.some(label=>typeof label!=='string'||!label.trim()||label.length>100)))continue;
  if(!/^metric-choice-[a-f0-9]{16}$/.test(choice?.id)||typeof choice.question!=='string'||!choice.question
    ||typeof choice.header!=='string'||!Array.isArray(choice.options)||choice.options.length<2||choice.options.length>4)continue;
  const first=choice.options[0]?.query_request?.periods?.[0];
  if(!year(first)||choice.options.some(option=>typeof option?.label!=='string'||!option.label
    ||!query(option.query_request,[first])||option.query_request.metric_id===comparison.query_request.metric_id)
    ||new Set(choice.options.map(option=>option.label)).size!==choice.options.length
    ||new Set(choice.options.map(option=>option.query_request.metric_id)).size!==choice.options.length)continue;
  return plan;
 }
 return null;
}

function queriedFor(events,query){
 return events.filter(event=>{
  if(event.type!=='tool/call'||event.data.name!=='data_metric_query')return false;
  let args=event.data.arguments??event.data.args;
  try{if(typeof args==='string')args=JSON.parse(args);}catch{return false;}
  const groups=args?.group_by||[];
  return args?.source_id===query.source_id&&args.metric_id===query.metric_id
    &&(!args.metric_version||args.metric_version===query.metric_version)
    &&Array.isArray(args.periods)&&args.periods.length===query.periods.length&&query.periods.every(period=>args.periods.includes(period))
    &&Array.isArray(groups)&&groups.length===(query.group_by?.length||0)&&groups.every((group,i)=>group===query.group_by?.[i])
    &&!(args.filters?.length);
 });
}

function independentChoiceCalls(plan,events){
 return events.filter(event=>event.type==='tool/call'&&event.data.name==='ask_user_question'
   &&questionItems(event.data).some(item=>item.id===plan.choice.id&&item.question===plan.choice.question));
}

export function independentChoiceSelection(plan,events){
 if(!plan)return null;
 const {choice}=plan;
 const asked=independentChoiceCalls(plan,events);
 const replies=events.filter(event=>{
  if(event.type!=='tool/result'||event.data.message?.isError||event.surfaceOp&&event.surfaceOp!=='append')return false;
  const call=asked.find(candidate=>candidate.data.callId===event.data.message?.source?.callId);
  return call&&call.seq<event.seq&&call.data.turn===event.data.turn;
 });
 const reply=replies.at(-1);
 const ask=reply&&asked.find(event=>event.data.callId===reply.data.message.source.callId);
 if(!ask)return null;
 const selected=clarificationEvidence([ask,reply]).clarifications.find(item=>item.question_id===choice.id
   &&item.question===choice.question);
 const option=selected&&choice.options.find(({label})=>
   selected.selected_labels.length===1&&selected.selected_labels[0]===label
   ||!selected.selected_labels.length&&selected.answer.trim()===label);
 return option?{option,ask,reply}:null;
}

export function independentComparisonStep(plan, events, tools, {offerChoice=false}={}){
 if(!plan)return null;
 const {choice,comparison}=plan;
 const can=name=>tools?.some(tool=>tool.name===name);
 const asked=independentChoiceCalls(plan,events);
 const nativeChoice={id:choice.id,header:choice.header,question:choice.question,
   options:choice.options.map(({label})=>({label}))};
 const picked=independentChoiceSelection(plan,events)?.option;
 const query=comparison.query_request;
 const queried=queriedFor(events,query);
 if(!queried.length)return can('data_metric_query')?{name:'data_metric_query',args:query}:null;
 if(query.group_by&&!events.some(event=>event.type==='tool/result'&&queried.some(call=>
   call.seq<event.seq&&call.data.callId===event.data.message?.source?.callId)))return null;
 if(offerChoice&&!asked.length&&!events.some(event=>event.type==='tool/call'&&event.data.name==='ask_user_question')
   &&can('ask_user_question'))return {name:'ask_user_question',args:{questions:[nativeChoice]}};
 if(picked&&can('data_metric_query')&&!queriedFor(events,picked.query_request).length)return {name:'data_metric_query',args:picked.query_request};
 return null;
}

export function independentChoiceWasOffered(events, plan){
 return !!plan&&independentChoiceCalls(plan,events).length>0;
}

const simplePeriod=/^((?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?)/;
const quotedTopic=/^(?:请(?:您)?(?:确认|选择))?((?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?)?(?:「([^」]+)」|“([^”]+)”|"([^"]+)")(.*)$/;
const neutralChoice=/^(?:(?:有(?:两|三|四|几|多|\d+)个(?:已发布)?口径,)?请(?:您)?(?:选一个|选择(?:要查询)?(?:哪(?:一)?[种个项]|一个|(?:已发布)?口径))|(?:(?:您|你)?指(?:的)?是|采用|使用)哪(?:一)?[种个项](?:已发布)?口径):?$/;
const scopedAlias=/\d|仅|只|除|不含|排除|以上|以下|期间|年度|此前|之后|以内|以外|省内|省外|市内|市外|城区|郊区|[省市区县镇乡州村]$/;
function catalogTopic(question){
 const match=questionName(question).match(quotedTopic);
 if(!match||!neutralChoice.test(match[5]))return null;
 let label=match.slice(2,5).find(Boolean);
 const internal=label.match(simplePeriod)?.[1]||'';
 if(match[1]&&internal)return null;
 if(internal)label=label.slice(internal.length);
 return label.length>=3?{label,period:match[1]||internal}:null;
}
function optionDescription(option){
 return String(option?.description||'').normalize('NFKC').replace(/\s+/g,'').replace(/÷/g,'/')
  .replace(/([项类条个者])都(?=合格|满足|符合|达到|通过|计入)/g,'$1均').replace(/。$/,'');
}
function optionLabel(label){
 const match=label.match(/^([^()]+)((?:\([^()]+\))*)$/);
 return match?{base:match[1],aliases:[...match[2].matchAll(/\(([^()]+)\)/g)].map(item=>item[1])}:null;
}
function relatedAlias(alias,base,topic,definition){
 if(scopedAlias.test(alias))return false;
 if(alias===topic||definition.includes(alias))return true;
 // An alternative label can reorder the same wording. Require a common
 // measure suffix and forbid additions absent from the option's own name.
 if(alias.length<5||!base.endsWith(alias.slice(-3)))return false;
 const remaining=[...base];
 return [...alias].every(character=>{
  const index=remaining.indexOf(character);
  if(index<0)return false;
  remaining.splice(index,1);
  return true;
 });
}
function equivalentOption(left,right,topic){
 const a=optionName(left),b=optionName(right),definition=optionDescription(left);
 if(!a||!b||definition!==optionDescription(right))return false;
 if(a===b)return true;
 if(!definition||!topic)return false;
 const first=optionLabel(a),second=optionLabel(b);
 if(!first||!second||first.base!==second.base)return false;
 return first.aliases.every(alias=>second.aliases.includes(alias)||relatedAlias(alias,first.base,topic,definition))
  &&second.aliases.every(alias=>first.aliases.includes(alias)||relatedAlias(alias,first.base,topic,definition));
}
function sameQuestion(left,right){
 if(!left||!right)return false;
 if(Boolean(left.multi_select??left.multiSelect)!==Boolean(right.multi_select??right.multiSelect))return false;
 const header=value=>questionName(value).replace(/^(?:请(?:您)?)?选择/,'');
 const oldHeader=header(left.header),newHeader=header(right.header);
 if(oldHeader&&newHeader&&oldHeader!==newHeader)return false;
 const a=Array.isArray(left.options)?left.options:[],b=Array.isArray(right.options)?right.options:[];
 if(a.length!==b.length)return false;
 const first=questionName(left.question),second=questionName(right.question);
 const oldTopic=catalogTopic(first),newTopic=catalogTopic(second);
 const topic=oldTopic?.label===newTopic?.label&&oldTopic?.period===newTopic?.period?oldTopic?.label:null;
 if(!first||(first!==second&&(!topic||!a.length)))return false;
 // Legacy exact questions may omit their UI header, but an unmatched
 // qualifier there must not be lost when the model changes question IDs.
 if(oldHeader!==newHeader&&(!left.id||left.id!==right.id||first!==second
    ||/[()\d、，,]|仅|只|除|不含|排除|范围|地区|对象|来源/.test(oldHeader||newHeader)))return false;
 return a.every(option=>b.filter(other=>equivalentOption(option,other,topic)).length===1)
  &&b.every(option=>a.filter(other=>equivalentOption(option,other,topic)).length===1);
}

// Only a sent question's actual native outcome can suppress a repeat.
// Rejections from local validation remain eligible for a corrected call.
export function unsettledQuestionRepeat(events,questions){
 const start=events.findLast(event=>event.type==='user/message'&&event.data.source?.kind==='user')?.seq??0;
 const calls=new Map(),outcomes=new Map();
 for(const event of events){
  if(event.seq<=start||event.surfaceOp&&event.surfaceOp!=='append')continue;
  const data=event.data;
  if(event.type==='tool/call'&&data.name==='ask_user_question'){
   const asked=questionItems(data);
   if(asked.length&&typeof data.callId==='string')calls.set(data.callId,{questions:asked,turn:data.turn});
   continue;
  }
  const reply=event.type==='user/message'&&data.source?.kind==='user-question-reply'&&data.source.outcome==='answered';
  if(event.type!=='tool/result'&&!reply)continue;
  const message=reply?data:data.message;
  const callId=message?.source?.callId||message?.toolCallId,asked=calls.get(callId);
  if(!asked||!reply&&asked.turn!==undefined&&data.turn!==undefined&&asked.turn!==data.turn)continue;
  const text=(message.content||[]).filter(block=>block.type==='text').map(block=>block.text).join('');
  if(message.isError){
   const code=(text.match(/^(?:Error:\s*)?([A-Z_]+):/)||[])[1]||data.error?.code;
   if(['ASK_ABORTED','ASK_TIMED_OUT','UNRESOLVED_CLARIFICATION','TOOL_OUTCOME_UNKNOWN'].includes(code))
    outcomes.set(callId,{state:code==='ASK_ABORTED'?'cancelled':code==='UNRESOLVED_CLARIFICATION'?'unanswered':'pending',answers:[]});
   else if(text==='Error: ask_user_question was aborted before the user answered')
    outcomes.set(callId,{state:'cancelled',answers:[]});
   else if(/^(?:Error: )?(?:Cannot assign to read only property '(?:label|description)' of object|no user-questions answerer accepted the request)/.test(text))
    outcomes.set(callId,{state:'unavailable',answers:[]});
   continue;
  }
  let value;try{value=JSON.parse(text);}catch{continue;}
  if(value?.pending===true&&value.callId===callId)outcomes.set(callId,{state:'pending',answers:[]});
  else if(Array.isArray(value?.answers)&&(!reply||value.callId===callId))
   outcomes.set(callId,{state:'cancelled',answers:value.answers});
 }
 for(const [callId,{state,answers}] of outcomes){
  if(calls.get(callId).questions.some(prior=>{
   const answer=answers.find(item=>item?.id===prior.id);
   const hasAnswer=answer?.selected?.length||typeof answer?.custom==='string'&&answer.custom.trim();
   // Missing IDs in a non-empty batch may be a protocol error, not a skip.
   return !hasAnswer&&(!answers.length||answer)&&questions.some(question=>sameQuestion(question,prior));
  }))return state;
 }
 return null;
}

function explicitPercentage(user){
 const text=messageText(user).normalize('NFKC').trim();
 const match=text.match(/^([^().。?!;\n]{2,140})\(\s*([\p{L}\p{N}]{2,32}?)\s*(?:占|除以|÷)\s*([\p{L}\p{N}]{2,24}?)\s*(?:的(?:百分比|比例|比率|占比)|(?:×|乘以)\s*100\s*%)\s*\)[。?!]?$/u);
 if(!match||/或者|或是|分别|另外|同时|以及|并且|一并|顺便|再查|替代|改用/.test(match[1]))return null;
 const [, , numerator,denominator]=match;
 return numerator!==denominator?{numerator,denominator}:null;
}

const formulaTerm=value=>String(value||'').normalize('NFKC').replace(/[\s"'“”「」『』]/g,'');

function formulaEvents(events,user){
 const turn=events.findLast(event=>event.type==='turn/start'&&event.seq<user.seq)?.data.turn;
 if(!Number.isSafeInteger(turn))return [];
 return events.filter(event=>event.seq>user.seq&&event.data?.turn===turn
   &&(!event.surfaceOp||event.surfaceOp==='append'));
}

function formulaCatalogs(events,user){
 return values(events,'data_catalog',user.seq).filter(catalog=>catalog?.status==='succeeded'
   &&catalog.runtime_contract?.source==='active-engine'&&Array.isArray(catalog.sources));
}

function formulaFieldNames(column){
 return [column?.name,column?.label,...(Array.isArray(column?.aliases)?column.aliases:[])]
   .map(formulaTerm).filter(name=>/^[\p{L}\p{N}_]{2,48}$/u.test(name));
}

function formulaColumns(source,term){
 return (Array.isArray(source?.tables)?source.tables:[]).flatMap(table=>
   (Array.isArray(table?.columns)?table.columns:[]).filter(column=>
     formulaFieldNames(column).includes(term))
    .map(column=>({table:table.name,column:column.name})));
}

export function formulaAnswerEvidence(events,user){
 const formula=explicitPercentage(user);
 if(!formula)return null;
 const current=formulaEvents(events,user);
 const columns=formulaCatalogs(current,user).flatMap(catalog=>catalog.sources).flatMap(source=>
   (Array.isArray(source?.tables)?source.tables:[]).flatMap(table=>Array.isArray(table?.columns)?table.columns:[]));
 const names=columns.map(formulaFieldNames);
 const equivalent=new Set(names.filter(fields=>fields.includes(formula.denominator)).flat());
 const alternatives=[...new Set(names.flat())].filter(field=>!equivalent.has(field)&&field!==formula.denominator)
  .sort((left,right)=>right.length-left.length);
 const choices=clarificationEvidence(current.filter(event=>event.type!=='tool/result'
  ||event.data.message?.source?.kind==='tool'),user.seq).clarifications
  .filter(choice=>/分母|改用|替代/.test(choice.question)).flatMap(choice=>choice.selected_labels.length?
   choice.selected_labels:[choice.answer]).map(formulaTerm);
 const location=formulaLocation(events,user);
 const missingDenominatorSource=location&&!formulaColumns(location.source,formula.denominator).length
  ?location.source.id:null;
 return {...formula,alternatives,choices,missingDenominatorSource};
}

const formulaQueryTools=new Set(['data_sql_query','data_query','data_metric_query','data_report']);
const explorationPurpose=value=>value==='explore'||value==='probe';
export const formulaBasisRejection=/^(?:Error:\s*)?FORMULA_BASIS_MISMATCH:/;
const formulaComponentQuestionCode='FORMULA_COMPONENT_QUERY';
const formulaQuestionRejectionPattern=new RegExp('^(?:Error:\\s*)?'+formulaComponentQuestionCode+':');

function formulaCallRejected(events,call,rejection){
 return events.some(event=>{
  const message=event.data?.message;
  return event.type==='tool/result'&&event.seq>call.seq&&message?.source?.callId===call.data.callId
    &&message.source.kind==='tool'&&message.isError===true
    &&message.content?.some(block=>block.type==='text'&&rejection.test(block.text));
 });
}

function formulaLocation(events,user){
 if(!user?.data.id)return null;
 const formula=explicitPercentage(user);
 if(!formula)return null;
 const current=formulaEvents(events,user);
 const catalogs=formulaCatalogs(current,user);
 // A scoped table detail or a paged source list alone cannot establish uniqueness.
 const inventory=formulaCatalogs(current.filter(event=>{
  if(event.type!=='tool/call'||event.data.name!=='data_catalog')return true;
  const {source_id,table_name,metric_id,entry_ref,source_offset,view}=callArguments(event.data);
  return !source_id&&!table_name&&!metric_id&&!entry_ref&&!source_offset&&view!=='metrics';
 }),user).findLast(value=>value.execution?.operation==='data.catalog'&&value.execution_scope
  &&value.execution_scope===value.project&&value.catalog_view?.next_source_offset==null
  &&(!value.catalog_view?.paged||value.catalog_view.source_count===1)&&value.sources.length===1);
 const source=inventory?.sources[0];
 if(!source?.id||source.next_table_offset!=null||!Array.isArray(source.tables)||!source.tables.length
   ||source.tables.some(table=>!Array.isArray(table?.columns)||table.next_column_offset!=null)
   ||catalogs.some(catalog=>catalog.sources.some(item=>item?.id!==source.id)))return null;
 const [field,...other]=formulaColumns(source,formula.numerator);
 if(!field?.table||!field.column||other.length)return null;
 return {formula,current,source,field};
}

function formulaAttempts(current,sourceId,activeQuestionId){
 const calls=current.filter(event=>event.type==='tool/call');
 const formal=calls.filter(call=>formulaQueryTools.has(call.data.name)&&!explorationPurpose(callArguments(call.data).purpose));
 const first=formal[0];
 const basisRejected=formal.length===1&&first.data.name==='data_sql_query'
   &&callArguments(first.data).source_id===sourceId&&formulaCallRejected(current,first,formulaBasisRejection);
 const questions=calls.filter(call=>call.data.name==='ask_user_question'&&call.data.callId!==activeQuestionId);
 const blocked=questions.filter(call=>formulaCallRejected(current,call,formulaQuestionRejectionPattern));
 return {basisRejected,questionRejected:blocked.length>0,
  attempted:questions.length>blocked.length||formal.length>0&&!basisRejected};
}

export function formulaRecoveryEvidence(events,user,sessionId){
 const location=sessionId&&formulaLocation(events,user);
 if(!location)return null;
 const {formula,current,source,field}=location;
 const probeCalls=current.filter(event=>event.type==='tool/call'&&event.data.name==='data_sql_query'
   &&explorationPurpose(callArguments(event.data).purpose));
 const probes=values(current.filter(event=>event.type!=='tool/call'||probeCalls.includes(event)),'data_sql_query',user.seq)
   .filter(result=>result?.status==='succeeded'&&result.result_ref&&result.receipt?.read_only===true
     &&result.receipt.source_id===source.id&&explorationPurpose(result.purpose)
     &&result.result_lineage?.source_id===source.id&&result.result_lineage.request_ref===user.data.id
     &&result.result_lineage.session_id===sessionId&&explorationPurpose(result.result_lineage.purpose));
 const attempts=formulaAttempts(current,source.id);
 if(!probes.length&&!attempts.basisRejected&&!attempts.questionRejected)return null;
 return {...formula,...field,sourceId:source.id,probes,...attempts};
}

function postponesSpecifiedFormula(item,denominator){
 if(item?.intent||!Array.isArray(item?.options)||item.options.length<2)return false;
 const question=formulaTerm(item.question);
 const topic=question+formulaTerm(item.header);
 // Source coverage and the field to read are verified by the callers.
 // Option wording is variable. Keep a real scope choice or an offered
 // source for the original operand available.
 const namedBasis=topic.includes(denominator)&&/分母|口径|改用|替代|计算|处理/.test(topic);
 const alternatives=item.options.map(option=>typeof option==='string'?option:option?.label||'').join('、');
 const denominatorChoice=/分母(?:口径|字段|怎么|如何|取)/.test(topic)
   ||/分母/.test(topic)&&/替代|代替|近似|改用/.test(topic+alternatives);
 if(!(namedBasis||denominatorChoice)||!/选择|确认|改用|替代|口径|如何|哪里|哪个|怎么/.test(topic)
   ||/(?:选择|哪个|哪一个)(?:统计|核算|授权)?(?:日期|年份|期间|对象|地区|数据源)|按.+还是.+(?:日期|期间|分组)/.test(topic))return false;

 return !item.options.some(option=>{
  const label=formulaTerm(typeof option==='string'?option:option?.label);
  const sourceOfOriginalOperand=/^(?:使用|采用|沿用|查询|读取|取|用|以)/.test(label)
   &&label.includes(denominator)&&!/替代|代替|近似|待补|确认|暂无|缺少/.test(label);
  return sourceOfOriginalOperand||/^(?:按|选择)(?:[^。?!,]{0,16})?(?:日期|月份|年度|地区|对象|分组|筛选|数据源)$/.test(label);
 });
}

function pendingFormulaComponent(events,user,questions,activeQuestionId){
 const location=formulaLocation(events,user);
 if(!location)return null;
 const {formula,current,source,field}=location;
 if(!questions.some(question=>postponesSpecifiedFormula(question,formula.denominator))
   ||formulaColumns(source,formula.denominator).length
   ||formulaAttempts(current,source.id,activeQuestionId).attempted)return null;
 return {...formula,...field,sourceId:source.id};
}

function availableFormulaComponent(events,user,questions,sessionId){
 if(!user?.data.id||!sessionId)return null;
 const formula=explicitPercentage(user);
 if(!formula||!questions.some(question=>postponesSpecifiedFormula(question,formula.denominator)))return null;
 const {numerator,denominator}=formula;
 const current=formulaEvents(events,user);
 const sources=formulaCatalogs(current,user).flatMap(catalog=>catalog.sources);
 if(sources.some(source=>formulaColumns(source,denominator).length))return null;
 const hasNumerator=sourceId=>!!sourceId&&sources.some(source=>source?.id===sourceId&&formulaColumns(source,numerator).length);
 const receipts=[...formulaQueryTools].flatMap(name=>values(current,name,user.seq));
 const names=[numerator,numerator+'合计',numerator+'总和',numerator+'汇总','合计'+numerator];
 const matched=new Map();
 for(const receipt of receipts){
  if(receipt?.status!=='succeeded')continue;
  const lineage=receipt.result_lineage,values=receipt.values;
  const sourceId=receipt.receipt?.source_id;
  if(!receipt.result_ref||receipt.purpose!=='answer'
    ||receipt.receipt?.read_only!==true||receipt.truncated||receipt.validation?.state==='conflict'
    ||lineage?.purpose!=='answer'||lineage.request_ref!==user.data.id
    ||lineage.session_id!==sessionId||lineage.source_id!==sourceId||!hasNumerator(sourceId)
    ||values?.complete!==true||!Array.isArray(values.columns)||!Array.isArray(values.rows)
    ||!values.rows.length||values.rows.length!==receipt.row_count
    ||values.columns.some(column=>formulaTerm(column).includes(denominator)||/率|占比|百分比/.test(String(column))))continue;
  const index=values.columns.findIndex(column=>names.includes(formulaTerm(column)));
  if(index<0||!values.rows.some(row=>Array.isArray(row)&&Number.isFinite(row[index])))continue;
  matched.set(receipt.result_ref,{...formula,column:values.columns[index]});
 }
 return matched.size===1?matched.values().next().value:null;
}

export function formulaQuestionRejection(events,user,questions,sessionId,callId){
  const formula=availableFormulaComponent(events,user,questions,sessionId);
  if(formula)return 'FORMULA_COMPONENT_READY: 本轮正式回执已查得「'+formula.column+
   '」。请直接交付该回执中的分组金额或数量，说明实际统计对象、日期字段和筛选范围。沿用用户公式「'+
   formula.numerator+' ÷ '+formula.denominator+' × 100%」，说明当前证据缺项并保留不可计算的比率为空。更换分母属于新问题，等用户明确提出后再计算。本轮用已有回执完成答复。';
  const component=pendingFormulaComponent(events,user,questions,callId);
  if(!component)return null;
  return formulaComponentQuestionCode+': 用户已指定分母「'+component.denominator+
   '」。授权目录已定位分子「'+component.numerator+'」，来源 '+component.sourceId+'，表「'+component.table+
   '」，字段「'+component.column+'」。请核实同范围的日期和关联，沿原问对象、期间、分组及筛选正式读取一次'+
   '（使用 SQL 时设 purpose=answer）。原问未指定日期字段时，使用分子事实的业务日期并说明。若仍有影响可查结果且无法按原问和授权目录确定的口径，只单独询问该项。'+
   '交付分子并按原公式说明所缺数据。本询问未发出，也没有用户选择。';
}

export function installMixedDelivery(ctx){
 ctx.on('tools/pre-execute',async(exec,next)=>{
  if(exec.name!=='ask_user_question'||exec.signal.aborted)return next();
  if(typeof exec.arguments?.questions==='string')return {kind:'deny',reason:'QUESTION_FORMAT: questions 必须直接传数组，如 {"questions":[{"id":"scope","question":"请选择统计口径"}]}。不要把数组写在引号内。尚未向用户提问，也未获得确认。'};
  if(foreignQuestion(exec.arguments))return {kind:'deny',reason:'LANGUAGE: 问题和选项必须用简体中文。请用简体中文重新提出同一个问题。此反馈不是用户回答。'};
  const events=exec.agent.session.snapshotEvents();
  const lastUser=events.findLast(e=>e.type==='user/message'&&e.data.source?.kind==='user');
  const prior=clarificationEvidence(events,lastUser?.seq??0).clarifications;
  const questions=Array.isArray(exec.arguments?.questions)?exec.arguments.questions:[];
  const plan=independentComparisonPlan(values(events,'data_catalog',lastUser?.seq??0),lastUser?.data.id);
  if(independentChoiceWasOffered(events,plan)
    &&!questions.some(question=>question.id===plan.choice.id&&question.question===plan.choice.question)
    &&questions.length===1){
   const title=plan.choice.question.match(/“([^”]+)”/)?.[1]||'';
   const topic=title.replace(/^20\d{2}年/,'');
   const year=title.slice(0,4);
   const text=questionName(questions[0].question);
   const years=text.match(/20\d{2}/g)||[];
   if(topic&&text.includes(topic)&&(!years.length||years.every(value=>value===year))){
    return {kind:'deny',reason:'QUESTION_ALREADY_OFFERED: 该口径本轮已有正式选择项。请沿用实际选择或说明尚待确认。'};
   }
  }
  const repeated=prior.find(r=>{
   const call=events.find(event=>event.type==='tool/call'&&event.data.name==='ask_user_question'&&event.data.callId===r.call_id);
   const old=call&&questionItems(call.data).find(q=>q.id===r.question_id);
   return old&&questions.some(q=>sameQuestion(q,old));
  });
  if(repeated)return {kind:'deny',reason:'QUESTION_ALREADY_ANSWERED: 用户已回答「'+repeated.question+'」为「'+repeated.answer+'」。沿用这个真实选择继续查询；其他尚未回答的问题请单独提问。'};
  if(unsettledQuestionRepeat(events,questions))return {kind:'deny',reason:'QUESTION_ALREADY_OFFERED: 该澄清本轮已经提出，尚无用户选择，不再重复弹出。请先交付可核实的部分，注明待确认事项；不得替用户选择。'};
  if(redundantYearQuestion(events,exec.arguments))return {kind:'deny',reason:'YEAR_ALREADY_SPECIFIED: 当前用户问题已明确唯一年份，且已有该年份的查询结果。请按原题完成答案并结束，不再让用户重选年份，也不自行扩展为新问题。此反馈不是用户的新选择。'};
  if(questionBeforeCatalog(events,exec.arguments))return {kind:'deny',reason:'CATALOG_FIRST: 追问业务范围前先调用 data_catalog；用户已明确给出的年份选择可直接提问，其他选项只写授权目录中实际存在的指标、表或范围。此反馈不是用户回答。'};
  const formula=formulaQuestionRejection(events,lastUser,questions,exec.agent.id,exec.callId);
  if(formula)return {kind:'deny',reason:formula};
  const pending=pendingClearDelivery(exec.agent.session.snapshotEvents());
  if(!pending.length)return next();
  return {kind:'deny',reason:'CLEAR_DELIVERY_FIRST: 请先为独立且已明确的问题建立任务并交付查询结果：'+pending.map(p=>p.year+'年 '+p.metric).join('、')+'；随后仅澄清未明确的项目分析范围。如果查询被拒绝或无覆盖，保存blocked状态和真实原因，再询问。此反馈不是用户确认。'};
 },{prepend:true});
}
