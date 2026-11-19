import {asksForMissingHistory,requestedUnavailableHistory} from './unavailable-delivery.js';
import {clarificationEvidence,humanRequests} from './host-evidence.js';
import {workMeasureQuestionRejection} from './answer-delivery.js';
import {extractCatalogSources} from './tool-normalization.js';
import {isVague,isDataOnly,isAmbiguousMeasure} from './vague.js';

// Normalize only an initial, action-free material list. Keep Harness's question
// UI and answer transport; no synthetic answer and no extra agent loop.
export const chineseQuestionLabel=label=>typeof label==='string'?label.replace(/\s*[（(]\s*Recommended\s*[）)]/gi,'（推荐）'):label;
const baseLabel=label=>String(chineseQuestionLabel(label)||'').replace(/\s*[（(]\s*推荐\s*[）)]\s*$/,'').trim();

const nonempty=value=>typeof value==='string'&&Boolean(value.trim());
function validDimensionChoice(choice,source,user){
  return choice?.request_ref===user.data.id&&nonempty(source.id)&&choice.source_id===source.id
    &&/^biz-dim-[0-9a-f]{16}$/.test(choice.question_id)
    &&nonempty(choice.dimension)&&nonempty(choice.question)&&nonempty(choice.header)
    &&Array.isArray(choice.options)&&choice.options.length>=2&&choice.options.length<=3
    &&choice.options.every(option=>nonempty(option?.label)&&nonempty(option.description))
    &&new Set(choice.options.map(option=>baseLabel(option.label))).size===choice.options.length;
}

function automaticDimensionChoice(catalog,source,choice){
  const publication=source.business_project??source.source_context?.business_project;
  const revision=source.connection_revision??source.source_context?.connection_revision;
  return catalog.status==='succeeded'&&catalog.execution?.operation==='data.catalog'
    &&nonempty(catalog.catalog_ref)&&nonempty(catalog.project)&&catalog.execution_scope===catalog.project
    &&catalog.sources.length===1&&catalog.catalog_view?.next_source_offset==null
    &&choice.schema_version===1&&choice.automatic===true
    &&nonempty(choice.publication?.id)&&Number.isSafeInteger(choice.publication.version)&&choice.publication.version>0
    &&(!publication||publication.id===choice.publication.id&&publication.version===choice.publication.version)
    &&Number.isSafeInteger(choice.connection_revision)&&choice.connection_revision>=0
    &&(revision==null||revision===choice.connection_revision)
    &&choice.options.every(option=>nonempty(option.table)&&nonempty(option.column))
    &&new Set(choice.options.map(option=>option.table)).size>=2
    &&new Set(choice.options.map(option=>JSON.stringify([option.table,option.column]))).size===choice.options.length;
}

export function currentDimensionChoice(events,user,{automatic=false}={}){
  if(!user?.data.id)return null;
  const calls=new Map();
  let current=null;
  for(const event of events){
    if(event.seq<=user.seq)continue;
    if(automatic&&(user.data.turn==null||event.data?.turn!==user.data.turn
      ||(event.surfaceOp&&event.surfaceOp!=='append')))continue;
    if(event.type==='tool/call'&&event.data.name==='data_catalog')calls.set(event.data.callId,event.seq);
    if(event.type!=='tool/result')continue;
    const message=event.data?.message;
    if(message?.isError||!calls.has(message?.source?.callId)||calls.get(message.source.callId)>=event.seq
      ||(automatic&&message.source.kind!=='tool'))continue;
    for(const block of message.content||[]){
      if(block.type!=='text')continue;
      try{
        const catalog=JSON.parse(block.text);
        if(!Array.isArray(catalog.sources))continue;
        if(automatic)current=null;
        for(const source of catalog.sources){
          const choice=source.dimension_business_choice;
          if(!validDimensionChoice(choice,source,user))continue;
          if(!automatic)return choice;
          if(automaticDimensionChoice(catalog,source,choice))current=choice;
        }
      }catch{/* Catalog commentary and invalid JSON provide no proof. */}
    }
  }
  return current;
}

function nativeDimensionQuestion(choice){
  const {question_id:id,header,question}=choice;
  return {id,header,question,options:choice.options.map(({label,description})=>({label,description}))};
}

export function dimensionBusinessQuestionTool(events,user,tools){
  if(!user?.data.id||!tools?.some(tool=>tool.name==='ask_user_question')||
    events.some(event=>event.seq>user.seq&&event.data?.turn===user.data.turn
      &&event.type==='tool/call'&&event.data.name==='ask_user_question'))return null;
  const choice=currentDimensionChoice(events,user,{automatic:true});
  return choice?{name:'ask_user_question',args:{questions:[nativeDimensionQuestion(choice)]}}:null;
}

export function confirmedApprovalGrouping(events,user,tools,sessionId){
  const choice=currentDimensionChoice(events,user,{automatic:true});
  if(!choice)return null;
  const current=events.filter(event=>event.seq>user.seq&&event.data?.turn===user.data.turn
    &&(!event.surfaceOp||event.surfaceOp==='append'));
  const [picked,...other]=clarificationEvidence(current).clarifications;
  if(other.length||picked?.question_id!==choice.question_id||picked.question!==choice.question
    ||picked.selected_labels.length>1)return null;
  const selected=choice.options.find(option=>option.label===baseLabel(picked.selected_labels[0]||picked.answer));
  const nativeResult=current.find(event=>event.type==='tool/result'&&event.data.message?.source?.kind==='tool'
    &&event.data.message.source.callId===picked.call_id&&!event.data.message.isError);
  if(!selected?.approval_query_available||!nativeResult)return null;
  const calls=current.filter(event=>event.seq>nativeResult.seq&&event.type==='tool/call');
  const allowed=name=>tools.some(tool=>tool.name===name);
  if(!calls.length)return allowed('data_catalog')?{tool:{name:'data_catalog',args:{source_id:choice.source_id}}}:null;
  if(calls[0].data.name!=='data_catalog'||calls.length>2)return null;
  const plan=choice.confirmed_approval_query,query=plan?.query_request;
  if(plan?.selected_label!==selected.label||!['最多','最少'].includes(plan.direction)
    ||!nonempty(plan.category_column)||!nonempty(plan.count_column)
    ||!/^\d{4}-\d{2}-\d{2}$/.test(plan.start_inclusive)||!/^\d{4}-\d{2}-\d{2}$/.test(plan.end_exclusive)
    ||plan.end_exclusive<=plan.start_inclusive||query?.source_id!==choice.source_id
    ||query.purpose!=='answer'||!nonempty(query.sql)||!nonempty(query.title))return null;
  if(calls.length===1)return allowed('data_sql_query')?{tool:{name:'data_sql_query',args:query}}:null;
  const call=calls[1];
  if(call.data.name!=='data_sql_query')return null;
  try {
    const raw=call.data.arguments;
    const args=typeof raw==='string'?JSON.parse(raw):raw;
    if(args?.sql!==query.sql||args.source_id!==query.source_id||args.purpose!=='answer'
      ||args.scope_proposal?.length||args.parameters?.length||args.task_id||args.replaces_result_ref)return null;
    const message=current.find(event=>event.seq>call.seq&&event.type==='tool/result'
      &&event.data.message?.source?.kind==='tool'&&event.data.message.source.callId===call.data.callId)?.data.message;
    if(!message||message.isError)return null;
    return approvalGroupingAnswer(JSON.parse(message.content.find(block=>block.type==='text')?.text),
      plan,choice,user.data.id,sessionId);
  } catch { return null; }
}

function approvalGroupingAnswer(result,plan,choice,requestRef,sessionId){
  const {result_lineage:lineage,answer_contract:contract,values}=result??{};
  if(result?.status!=='succeeded'||!result.result_ref||result.truncated||result.validation?.state==='conflict'
    ||result.receipt?.read_only!==true||result.receipt?.source_id!==choice.source_id
    ||result.execution?.operation!=='data.sql_query'||lineage?.purpose!=='answer'||lineage.source_id!==choice.source_id
    ||lineage.request_ref!==requestRef||lineage.session_id!==sessionId||!lineage.config_fingerprint
    ||result.business_project?.id!==choice.publication.id||result.business_project.version!==choice.publication.version
    ||contract?.result_ref!==result.result_ref||contract.kind!=='sql_aggregate'||contract.complete!==true
    ||values?.complete!==true||values.columns?.length!==2||values.columns[0]!==plan.category_column
    ||values.columns[1]!==plan.count_column||!Array.isArray(values.rows)||values.rows.length!==result.row_count
    ||values.rows.length!==values.row_count)return null;
  const rows=values.rows;
  const label=/^[\p{L}\p{N}（）()·、 \-—]{1,60}$/u;
  if(rows.some(row=>!Array.isArray(row)||row.length!==2||row[0]!==null&&typeof row[0]!=='string'
    ||typeof row[0]==='string'&&row[0].trim()&&!label.test(row[0])||!Number.isSafeInteger(row[1])||row[1]<0))return null;
  const groups=rows.filter(([name,count])=>name?.trim()&&count>0);
  const scope='按「'+plan.selected_label+'」的批准日期查询 '+plan.start_inclusive+' 至 '+plan.end_exclusive
    +'（不含），各组按案卷键去重。';
  if(!groups.length)return null;
  const value=(plan.direction==='最多'?Math.max:Math.min)(...groups.map(row=>row[1]));
  const leaders=groups.filter(row=>row[1]===value).map(row=>row[0]);
  const missing=rows.some(([name,count])=>!name?.trim()&&count>0)?'未填写'+plan.category_column+'的分组单列在图卡中。':'';
  return {text:scope+'\n\n已标明'+plan.category_column+'的案卷中，**'+leaders.join('、')+'**'
    +(leaders.length>1?'并列':'')+plan.direction+'，'+(leaders.length>1?'均':'')+'为 **'+value+' 件**。'+missing};
}

function dimensionQuestionRejection(events,user,questions){
  const choice=currentDimensionChoice(events,user);
  if(!choice)return null;
  const names=choice.options.map(option=>option.label.split('（')[0]);
  const matching=questions.filter(q=>q.id===choice.question_id||String(q.question).includes(choice.dimension)
    ||names.some(name=>String(q.question).includes(name)||q.options?.some(o=>String(o.label).includes(name))));
  if(!matching.length)return null;
  const expected=nativeDimensionQuestion(choice);
  const options=expected.options;
  const picked=clarificationEvidence(events,user.seq).clarifications.findLast(receipt=>
    receipt.question_id===choice.question_id&&receipt.question===choice.question);
  if(picked&&picked.selected_labels.length<=1&&options.some(o=>o.label===baseLabel(picked.selected_labels[0]||picked.answer)))
    return 'QUESTION_ALREADY_RESOLVED: 用户已选择已发布业务，按同一来源的确认选项查询。';
  const valid=matching.length===1&&matching[0].id===expected.id&&matching[0].question===expected.question
    &&matching[0].multi_select!==true
    &&matching[0].options?.length===options.length&&options.every(option=>matching[0].options.some(proposed=>
      option.label===baseLabel(proposed.label)&&option.description===proposed.description));
  return valid?null:'NEEDS_CLARIFICATION: 请用当前授权目录提供的全部选项，原样调用 ask_user_question。'+JSON.stringify(expected);
}

export function internalPlaceholderQuestion(question){
  const text=String(question?.question||'').replace(/[\s（）(),，。.!！?？:：;；]/g,'');
  return /^(?:仅供|仅用于|用于|仅)?(?:内部|系统|模型|调试|测试)(?:核对|占位|校验|检查|测试|用){1,3}(?:(?:请|可)?忽略|(?:暂时)?无需(?:回答|回复)|(?:请)?不(?:用|必)(?:回答|回复))$/.test(text)
    || /^(?:占位(?:问题)?|placeholder|ignorethis|internalcheckonly)$/i.test(text);
}
const KEEP_OPTION=/保持现状|保持原样|当前即可|就这样|不需要|无需|不用|不必/;
// After the answer, "what next" choices (another metric, waiting for the data,
// keeping the explanation) are suggestions for the text, not clarifications.
const NEXT_STEP_QUESTION=/怎么处理|如何处理|下一步|接下来|后续(?:怎么|如何|是否)|希望我|是否(?:继续|改用|换用|改为)/;
const NEXT_STEP_OPTION=/补齐|补充数据|数据完善|仅保留|保留(?:现有|当前)(?:说明|结果)?|暂不处理|先这样|换(?:一?个|用)?(?:口径|指标)|改用(?:其他|别的)?(?:口径|指标)|其他指标/;
const SNAPSHOT_PERIOD=/(?<!\d)(20\d{2})\s*(?:年\s*(1[0-2]|0?[1-9])\s*月|[-/](1[0-2]|0?[1-9])(?!\d))/g;
const CURRENT_SNAPSHOT=/(?:当前|最新|现时|现在|目前|当下)(?:时点)?(?:的)?(?:有效)?快照/;
const SNAPSHOT_OPTION=new RegExp('^(?:先|改为|改用|改查|只|仅)?(?:查询|查看|查|统计|使用|用)?'+
  CURRENT_SNAPSHOT.source+'(?:[（(]?(?:作为|作|仅供)?参考[）)]?)?$');
const SKIP_SNAPSHOT=/^(?:不查(?:了|询)?|(?:先|暂)不(?:查(?:询)?|处理)|取消(?:查询)?|保留(?:原|现有|当前)说明|无需(?:查询|查)|不用(?:查(?:询)?|了))$/;
const HISTORICAL_ROUTE=/(?:查询|查看|读取|改查|改用|使用|选择)[^，,。；;\n]{0,16}(?:历史(?:快照|库|表|档案|数据源)|归档|档案库)/;

function optionalSnapshotQuestion(events,user,questions){
  if(!user?.data.id||questions.length!==1||user.data.content?.some(block=>block.type!=='text'))return false;
  const [question]=questions,options=question.options;
  const label=String(question.question||'');
  if(question.multi_select||!CURRENT_SNAPSHOT.test(label)||!/(?:是否|要不要|需不需要|需要我|希望我)/.test(label)
    ||!Array.isArray(options)||options.length!==2||!options.some(option=>SNAPSHOT_OPTION.test(baseLabel(option?.label)))
    ||!options.some(option=>SKIP_SNAPSHOT.test(baseLabel(option?.label)))
    ||[label,...options.map(option=>String(option?.description||''))].some(text=>HISTORICAL_ROUTE.test(text)))return false;

  const text=user.data.content.map(block=>block.text).join('\n').normalize('NFKC').trim();
  const dates=[...text.matchAll(SNAPSHOT_PERIOD)];
  if(dates.length!==1||/当前|现在|最新|现时|目前|当下|本月|本期|今天|同时|另查|另外|以及|或|对比|比较|同比|环比/.test(text)
    ||text.split(/[。！？!?；;\n]+/).filter(Boolean).length!==1)return false;
  const start=events.findLast(event=>event.type==='turn/start');
  if(!start||start.seq>=user.seq||clarificationEvidence(events,user.seq).clarifications.length)return false;
  const current=events.filter(event=>event.seq>user.seq&&event.data?.turn===start.data.turn
    &&(!event.surfaceOp||event.surfaceOp==='append'));
  if(current.some(event=>event.type==='tool/result'&&event.data.message?.isError
    &&event.data.message.content?.some(block=>block.type==='text'&&/NEEDS_CLARIFICATION:/.test(block.text))))return false;

  const calls=new Map(current.filter(event=>event.type==='tool/call'&&event.data.name==='data_catalog').filter(event=>{
    const raw=event.data.arguments??event.data.args;
    try{
      const args=typeof raw==='string'?JSON.parse(raw):raw;
      return args?.question?.normalize('NFKC').trim()===text&&!args.source_id&&!args.table_name&&!args.entry_ref
        &&!args.metric_id&&!args.source_offset&&!args.view;
    }catch{return false;}
  }).map(event=>[event.data.callId,event.seq]));
  const [,year,month,iso]=dates[0];
  const requestedMonth=year+'-'+String(month||iso).padStart(2,'0');
  // A declaration about a listed table only proves that a current snapshot
  // cannot answer this historical request. Other authorized sources may help.
  return current.some(event=>{
    const message=event.data?.message;
    if(event.type!=='tool/result'||message?.isError||message?.source?.kind!=='tool'
      ||!(calls.get(message.source.callId)<event.seq))return false;
    let catalog;try{catalog=JSON.parse(message.content?.find(block=>block.type==='text')?.text);}catch{return false;}
    const clock=catalog?.temporal_context;
    if(catalog?.status!=='succeeded'||catalog.execution?.operation!=='data.catalog'||!catalog.catalog_ref||!catalog.project
      ||catalog.execution_scope!==catalog.project||catalog.sources?.length!==1
      ||catalog.catalog_view&&(catalog.catalog_view.source_count!==1||catalog.catalog_view.next_source_offset!==null)
      ||clock?.zone_fallback||!/^20\d{2}-\d{2}$/.test(clock?.current_month||'')||requestedMonth>=clock.current_month)return false;
    const [source]=catalog.sources,publication=source?.business_project;
    if(!source?.id||!publication?.id||!publication.version||source.dimension_business_choice
      ||source.historical_qualification_query||source.registered_query||source.same_name_metrics?.length)return false;
    return Array.isArray(source.snapshot_boundaries)&&source.snapshot_boundaries.some(boundary=>boundary?.state==='current_snapshot_only'
      &&boundary.applies_to==='listed_table'&&boundary.read_request?.source_id===source.id
      &&boundary.publication?.id===publication.id&&boundary.publication?.version===publication.version
      &&Array.isArray(boundary.requested_subjects)&&boundary.requested_subjects.length===1
      &&Array.isArray(boundary.published_subjects)&&boundary.published_subjects.includes(boundary.requested_subjects[0])
      &&Array.isArray(source.tables)&&source.tables.some(table=>table?.name===boundary.table));
  });
}

export function exhaustedOptionalFollowup(events){
  const start=events.findLast(e=>e.type==='turn/start')?.seq??-1;
  const current=events.filter(e=>e.seq>start);
  if(!requestedUnavailableHistory(current).length)return false;
  const calls=new Map(current.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e.data.name]));
  let denied=false;
  for(const e of current){
    if(e.type!=='tool/result')continue;
    const m=e.data?.message;
    const name=calls.get(m?.source?.callId||m?.toolCallId);
    if(!m?.isError||!name)continue;
    const text=(m.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
    if(/^data_(?:sql_query|metric_query|query|report|plan_query|semantic_plan)$/.test(name)&&text.includes('NEEDS_CLARIFICATION:'))return false;
    if(name==='ask_user_question'&&text.includes('OPTIONAL_FOLLOWUP:'))denied=true;
  }
  return denied;
}

export function optionalPresentationQuestion(events,questions){
  const presentation=q=>/^(?:您|你)?(?:是否(?:需要|希望|要)|需要我|要不要)/.test(q.question||'')&&/展示|表格|图表|切换|拆分|进一步/.test(q.question)
    &&(q.options||[]).some(o=>KEEP_OPTION.test(o.label||''));
  const nextStep=q=>NEXT_STEP_QUESTION.test(q.question||'')||(q.options||[]).some(o=>NEXT_STEP_OPTION.test(o.label||''));
  if(!questions.length)return false;
  const last=events.findLastIndex(e=>e.type==='user/message'&&e.data?.source?.kind==='user');
  const current=events.slice(last+1);
  const answer=current.some(e=>e.type==='assistant/message'&&(e.data?.message?.content||[]).some(b=>b.type==='text'&&b.text?.trim()));
  const receipts=current.filter(e=>e.type==='tool/result').flatMap(e=>(e.data?.message?.content||[]).map(b=>({error:Boolean(e.data.message.isError),text:String(b.text||'')})));
  // An open object, caliber or scope ambiguity the engine raised still needs the user.
  if(receipts.some(r=>/NEEDS_CLARIFICATION/.test(r.text)))return false;
  if(questions.every(q=>asksForMissingHistory(q.question||''))&&requestedUnavailableHistory(events).length)return true;
  if(!questions.every(q=>presentation(q)||nextStep(q)))return false;
  const result=receipts.some(r=>{
    if(r.error)return false;
    try{const v=JSON.parse(r.text);return v.status==='succeeded'&&v.result_ref&&(v.purpose!=='explore'||!questions.every(presentation));}catch{return false;}
  });
  return answer&&result;
}
export function materialNeedsPurpose(events) {
  const users=events.filter(e=>e.type==='user/message'&&e.data.source?.kind==='user');
  if(users.length!==1)return false;
  const text=(users[0].data.content||[]).filter(x=>x.type==='text').map(x=>x.text).join('\n').trim();
  return Boolean(text)&&!/[?？查问看统算分析对比比较展示多少如何怎么请帮解释整理写]/.test(text)&&text.split('\n').filter(x=>x.trim()).length>1;
}
const CHOICE_CANNOT_HELP=/(?:即便|即使|哪怕)(?:确认|选择|选定)[^。；;\n]{0,65}(?:仍|也)(?:无|没有|查不到|不可查|无法查询)/;
const LOOKS_FOR_UNRELATED_METRIC=/(?:没有|未登记)[^。；;\n]{2,65}已发布指标[。；;\n]\s*(?:现有|其他)指标/;
const TIME_ONLY_OPTION=/^(?:20\d{2}\s*年|20\d{2}[-/]\d{1,2})/;
const PERIOD_LABEL=/^20\d{2}年(?:\d{1,2}月(?:[至到—](?:20\d{2}年)?\d{1,2}月)?)?(?:全年|按月|逐月)?$/;
const ENGINE_YEAR_CONFLICT=/^(?:Error: )?NEEDS_CLARIFICATION:\s*统计年份条件冲突/;
const GENERIC_SUBJECT=/^(?:今年|去年|前年|本年|当年|上年|本年度|上年度|全省|全市|全区|全国|本省|本市|各市|各地市|20\d{2}年?)$/;
// Extract the business object a question measures, from the question's own words.
// No fixed question text, metric id, year or table name participates in the decision.
const MEASURE_WORDS='服务次数|调用次数|访问次数|办件量|办理量|业务量|案卷数|件数|次数|数量|用户数|人数|金额|投入|经费|支出|收入|合格率|不合格率|通过率|比率|率|总量|总数|规模|体量';
const SUBJECT_PHRASE=new RegExp('([\\p{Script=Han}A-Za-z0-9]{2,24}?)('+MEASURE_WORDS+')','gu');
const SUBJECT_STOP=/^(?:服务|数据|指标|业务|情况|统计|管理|相关|其他|全部|所有)$/;
const INTENT_PREFIX=/^(?:只看|仅看|只|仅|请|帮我|帮忙|统计|查询|查一下|查|查看|计算|分析|对比|比较|展示|列出|核对|汇总|请问|我想|我要|需要)+/;
const SCOPE_PREFIX=/^(?:20\d{2}年(?:\d{1,2}月)?|今年|去年|前年|本年|当年|上年|本年度|上年度|全省|全市|全区|全国|本省|本市|各市|各地市|省级|市级|县级|区级)+/;
function cleanSubject(raw){
  let value=raw;
  for(let i=0;i<6;i++){
    const next=value.replace(INTENT_PREFIX,'').replace(SCOPE_PREFIX,'').replace(/^的+/,'').replace(/的+$/,'');
    if(next===value)break;
    value=next;
  }
  return value.trim();
}
function requestedSubjects(text){
  const value=String(text||'').normalize('NFKC');
  const out=[];let lastEnd=0;
  for(const m of value.matchAll(SUBJECT_PHRASE)){
    if(m.index<lastEnd)continue;
    lastEnd=m.index+m[0].length;
    const subject=cleanSubject(m[1]);
    if(subject.length>=2&&!GENERIC_SUBJECT.test(subject)&&!SUBJECT_STOP.test(subject)&&!out.includes(subject))out.push(subject);
  }
  return out;
}
// A period question may carry an escape option ("其他范围"); it is still a pure
// period choice and cannot resolve a missing business object.
const ESCAPE_OPTION=/^(?:其他|其它)(?:范围|期间|时间|年份)?(?:范围)?$|^以上都不是|^都不是|^自定义|^手动输入/;
const PERIOD_QUESTION=/年份|期间|月份|时间范围|哪一年/;
function periodDominated(q){
  const options=(Array.isArray(q?.options)?q.options:[]).map(o=>baseLabel(o?.label).replace(/\s+/g,''));
  if(options.length<2)return false;
  const isPeriod=label=>TIME_ONLY_OPTION.test(label)||PERIOD_LABEL.test(label);
  return options.filter(isPeriod).length>=2&&options.every(label=>isPeriod(label)||ESCAPE_OPTION.test(label));
}
// A completed, targeted catalog search over one fully enumerated source is the
// only structural proof that a requested object has no authorized field, caliber
// or example. Pagination, clipping, errors or unsearched subjects prove nothing.
function catalogSearchSettled(events,user,subjects){
  const current=events.filter(e=>e.seq>user.seq&&(user.data.turn==null||e.data?.turn===user.data.turn));
  const calls=new Map(current.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e.data]));
  const searched=new Set();let sawCatalog=false;
  for(const e of current){
    const message=e.data?.message;
    if(e.type!=='tool/result'||!message)continue;
    const call=calls.get(message.source?.callId||message.toolCallId);
    if(!call)continue;
    if(message.isError){
      // A denied ask_user_question is guard feedback, not a failed data lookup.
      if(/^data_/.test(call.name))return false;
      continue;
    }
    if(call.name!=='data_catalog')continue;
    let catalog;
    try{catalog=JSON.parse((message.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n'));}catch{return false;}
    if(catalog?.status!=='succeeded'||catalog.execution?.operation!=='data.catalog'||!Array.isArray(catalog.sources))return false;
    sawCatalog=true;
    const raw=call.arguments??call.args;
    let args;try{args=typeof raw==='string'?JSON.parse(raw):raw;}catch{args={};}
    const question=String(args?.question||'').normalize('NFKC');
    for(const subject of subjects)if(question.includes(subject))searched.add(subject);
    if(catalog.catalog_view?.next_source_offset!=null)return false;
    const count=catalog.catalog_view?.source_count;
    if(catalog.catalog_view?.paged&&count!=null&&catalog.sources.length!==count)return false;
    for(const s of catalog.sources){
      if(s.next_table_offset!=null||s.semantic_metrics_next_offset!=null||s.metric_definitions_next_offset!=null
        ||s.unavailable_metrics_next_offset!=null||s.source_context_clipped)return false;
      if((s.tables||[]).some(t=>t.next_column_offset!=null))return false;
    }
  }
  return sawCatalog&&subjects.every(subject=>searched.has(subject));
}

function uncoveredCatalogTerms(sources){
  const terms=[];
  for(const s of sources){
    const note=String(s.source_context?.scope_note||'');
    for(const m of note.matchAll(/(?:未覆盖|不包括|暂不支持)([^。；;\n]+)/g)){
      for(const part of m[1].split(/[、，,/和与及\s]+/)){
        const cleaned=part.replace(/^[A-Z0-9_]+/,'').trim();
        if(cleaned.length>=2)terms.push(cleaned);
      }
    }
    for(const u of s.unavailable_metrics||[]){
      for(const v of [u.name,...(u.aliases||[])]){
        if(typeof v==='string'&&v.trim().length>=2)terms.push(v.trim());
      }
    }
  }
  return [...new Set(terms)];
}

function catalogLabelsMatch(sources,matches){
  const names=item=>[item.name,item.label,item.description,item.business_description,item.question,...(item.aliases||[])]
    .some(text=>typeof text==='string'&&matches(text.trim()));
  const entry=item=>typeof item==='string'?matches(item.trim()):names(item);
  return sources.some(s=>{
    const context=s.source_context||{};
    const metrics=[...(s.semantic_metrics||[]),...(s.metric_definitions||[]),...(s.caliber_matches||[]),...(s.metric_names||[]),
      ...(context.caliber_matches||[]),...(context.example_matches||[]),...(context.catalog_entries||[]),...(context.metric_names||[])];
    return metrics.some(entry)||(s.tables||[]).some(t=>names(t)||(t.columns||[]).some(names));
  });
}

function catalogHasSubject(sources,subject){
  return Boolean(subject)&&catalogLabelsMatch(sources,text=>text.includes(subject));
}

const ONLY_PERIOD_REFINEMENT=/^(?:请)?(?:以|按|改按|改为|改成)(20\d{2})年(?:度)?(?:为准)?(?:[,，]?按?(?:(20\d{2})年)?(\d{1,2})月(?:[至到—–-](?:(20\d{2})年)?(\d{1,2})月)?)?(?:统计|查询)?[。！？!?]?$/;
const ADDITIONAL_CATALOG_INTENT=/另外|另(?:查|再)|同时|分别|顺便|还有|以及|并(?:查|看|列|解释|说明|比较)|对比|比较|预测|同比|环比|配置|如何|怎么|定义|口径|[和与及]/;
const PERIOD_OR_PRESENTATION=/^(?:按(?:月|季|年|日|周|20\d{2}年)|逐[月季年日周]|变化趋势|趋势|(?:用|给出|展示)(?:折线图|柱状图|表格|数据表))[\d年月日至到—–\-和与、的统查计询变化走势趋势图表展示列数据折线柱状\s]*$/;

function catalogMeasure(text){
  const value=text.normalize('NFKC');
  const matches=[...value.matchAll(SUBJECT_PHRASE)];
  if(matches.length!==1)return null;
  const [match]=matches;
  const [subject]=requestedSubjects(value);
  const tail=value.slice(match.index+match[0].length);
  if(!subject||ADDITIONAL_CATALOG_INTENT.test(value)
    ||tail.split(/[，,。；;！？!?\n]/).map(part=>part.trim())
      .some(part=>part&&!PERIOD_OR_PRESENTATION.test(part)))return null;
  return {subject,label:subject+match[2],measure:match[2]};
}

function catalogMeasureRequest(events,user){
  if(user.data.content?.some(block=>block.type!=='text'))return null;
  const text=(user.data.content||[]).map(block=>block.text||'').join('\n').trim();
  const measure=catalogMeasure(text);
  if(measure)return {measure,question:text};
  const period=catalogRefinementPeriod(text);
  if(!period)return null;
  const users=humanRequests(events);
  if(users.at(-1)?.data.id!==user.data.id)return null;
  for(let i=users.length-2;i>=0;i--){
    const prior=users[i];
    if(prior.data.content?.some(block=>block.type!=='text'))return null;
    const question=(prior.data.content||[]).map(block=>block.text||'').join('\n').trim();
    const inherited=catalogMeasure(question);
    if(inherited)return {measure:inherited,question:inherited.label+'，'+period,period};
    if(!catalogRefinementPeriod(question))break;
  }
  return null;
}

function catalogGapDeclaration(source,args,measure){
  const note=source.source_context?.scope_note;
  if(typeof note==='string'&&/(?:授权数据(?:源|库|表)?|当前(?:数据源|数据库))(?:中|内)?\s*(?:未覆盖|不包括)/.test(note)){
    const terms=uncoveredCatalogTerms([{source_context:{scope_note:note}}]);
    if(terms.includes(measure.subject)||terms.includes(measure.label))return true;
  }
  const entry=source.entry;
  if(entry?.kind!=='term'||entry.source_id!==source.id||entry.complete!==true||entry.next_offset!=null
    ||entry.entry_ref!==args.entry_ref||entry.read_request?.entry_version!==args.entry_version)return false;
  const definition=String(entry.content?.definition||'');
  const gap=definition.match(/(?:^|\n)\s*(?:(?:[一二三四五六七八九十]+|\d+)[、.]\s*)?(?:未覆盖|不包括|暂不支持)[^\n]{0,80}\n([^\n]+)/)?.[1];
  if(!gap)return false;
  return [...gap.matchAll(/[（(]([^）)]+)[）)]/g)].some(([,item])=>{
    const [names,...detail]=item.split(/[,，]/);
    // Being left out of a published metric list says nothing about SQL queryability.
    // A source statement or explicit absence of physical records is required.
    const dataScope=detail.some(part=>{
      const detail=part.trim(), origin=detail.match(/^来源(?:为|是|于|[:：])?\s*(.{2,})$/)?.[1];
      return Boolean(origin&&!/^(?:本库|本数据|当前|已接入|已有)/.test(origin))
        ||/^(?:未(?:接入|保存|采集)|没有|无)[^。；;]{0,12}(?:数据|日志|记录|明细表)/.test(detail);
    });
    return dataScope&&names.split(/[、／/]/).map(name=>name.trim()).includes(measure.subject);
  });
}

function catalogGapContext(events,user){
  const request=catalogMeasureRequest(events,user);
  if(!request||isVague(request.measure.label))return null;
  const {measure}=request;
  const current=events.filter(event=>event.seq>user.seq&&event.data?.turn===user.data.turn);
  const calls=current.filter(event=>event.type==='tool/call');
  if(!calls.length||calls.some(call=>call.data.name!=='data_catalog')
    ||currentDimensionChoice(events,user)||!catalogSearchSettled(events,user,[measure.subject]))return null;
  const reads=[];
  for(const call of calls){
    const matches=current.filter(event=>event.type==='tool/result'
      &&event.seq>call.seq&&event.data.message?.source?.callId===call.data.callId&&event.surfaceOp==='append');
    if(matches.length!==1||matches[0].data.message.source.kind!=='tool')return null;
    const [reply]=matches;
    const raw=call.data.arguments??call.data.args;
    let args,catalog;
    try{
      args=typeof raw==='string'?JSON.parse(raw):raw;
      catalog=JSON.parse(reply.data.message.content.filter(block=>block.type==='text').map(block=>block.text).join('\n'));
    }catch{return null;}
    if(reply.data.message.isError||catalog?.status!=='succeeded'||catalog.execution?.operation!=='data.catalog'
      ||!catalog.catalog_ref||!catalog.project||catalog.execution_scope!==catalog.project
      ||!Array.isArray(catalog.sources)||catalog.sources.length!==1||!catalog.sources[0]?.id
      ||catalog.same_name_metrics?.length||catalog.catalog_view?.paged
      ||args?.source_id&&args.source_id!==catalog.sources[0].id)return null;
    reads.push({catalog,source:catalog.sources[0],args:args||{}});
  }
  const broad=reads.find(({source,args})=>!args.source_id&&!args.entry_ref&&!args.table_name&&!args.metric_id
    &&Array.isArray(source.tables)&&source.tables.length>0
    &&(source.table_count==null||source.table_count===source.tables.length)
    &&source.tables.every(table=>Array.isArray(table.columns)
      &&(table.column_count==null||table.column_count===table.columns.length)));
  if(!broad||reads.some(({catalog,source})=>catalog.project!==broad.catalog.project
    ||catalog.catalog_ref!==broad.catalog.catalog_ref||source.id!==broad.source.id||source.same_name_metrics?.length))return null;
  const possibleMeasurement=measure.measure.endsWith('次数')?'次数':measure.measure;
  const isCandidate=({source})=>catalogHasSubject([source],measure.subject)
    ||catalogHasSubject([{source_context:{caliber_matches:source.example_matches,catalog_entries:source.catalog_entries}}],measure.subject)
    ||catalogHasSubject([{tables:(source.tables||[]).map(table=>({columns:table.columns}))}],possibleMeasurement);
  return reads.some(isCandidate)?null:{...request,reads,broad};
}

function missingCatalogEvidence(events,user){
  const context=catalogGapContext(events,user);
  if(!context)return null;
  const {measure,reads,period}=context;
  if(!reads.some(({source,args})=>catalogGapDeclaration(source,args,measure)))return null;
  return {measure,period};
}

export function initialCatalogCoverageTool(events,user){
  if(!user?.data.id||user.data.turn==null)return null;
  const request=catalogMeasureRequest(events,user);
  if(!request||isVague(request.measure.label)
    ||events.some(event=>event.seq>user.seq&&event.data?.turn===user.data.turn&&event.type==='tool/call'))return null;
  return {name:'data_catalog',args:{question:request.question}};
}

// A search preview can point to the source's authoritative coverage description.
// Fetch that exact entry once so that incomplete prose is never used as proof.
export function unavailableCatalogCoverageTool(events,user){
  if(!user?.data.id||user.data.turn==null)return null;
  const context=catalogGapContext(events,user);
  if(!context||context.reads.some(({args,source})=>args.entry_ref||catalogGapDeclaration(source,args,context.measure)))return null;
  const entries=new Map();
  for(const {source} of context.reads){
    const nested=source.source_context||{};
    const previews=[...(source.caliber_matches||[]),...(nested.caliber_matches||[])];
    const references=[...(source.catalog_entries||[]),...(nested.catalog_entries||[])].filter(entry=>entry.kind==='term');
    for(const entry of [...previews,...references]){
      const request=entry.read_request;
      if(request?.source_id!==source.id||request?.entry_ref!==entry.entry_ref
        ||!request?.entry_ref?.startsWith('term:')||!nonempty(request.entry_version)
        ||!/(?:业务|数据|统计)(?:覆盖)?范围|数据来源|数据覆盖/.test(entry.name||entry.summary||'')
        ||entry.kind!=='term'&&!/(?:覆盖范围|数据来源|数据覆盖|未覆盖|不包括)/.test(entry.definition||''))continue;
      const {source_id,entry_ref,entry_version}=request;
      entries.set(JSON.stringify([source_id,entry_ref,entry_version]),{source_id,entry_ref,entry_version});
    }
  }
  if(entries.size===1)return {name:'data_catalog',args:[...entries.values()][0]};
  const {source}=context.broad;
  // A differently phrased full-source lookup can expose the coverage
  // declaration that did not rank in the first view.
  return entries.size===0&&context.reads.length===1&&context.broad.args.question!==context.measure.label
    ?{name:'data_catalog',args:{source_id:source.id,question:context.measure.label}}:null;
}

export function catalogRefinementPeriod(text){
  const match=text.normalize('NFKC').replace(/\s+/g,'').match(ONLY_PERIOD_REFINEMENT);
  if(!match)return null;
  const [,year,monthYear,month,nextYear,nextMonth]=match;
  if(monthYear&&monthYear!==year||nextYear&&nextYear!==year||month&&!(Number(month)>=1&&Number(month)<=12)
    ||nextMonth&&!(Number(nextMonth)>=Number(month)&&Number(nextMonth)<=12))return null;
  return year+'年'+(month?Number(month)+'月':'')+(nextMonth&&Number(nextMonth)!==Number(month)?'至'+Number(nextMonth)+'月':'');
}

// Only a positive statement of noncoverage from the authorized catalog, alongside
// a complete source read without a candidate, qualifies for direct delivery.
export function verifiedUnavailableCatalogAnswer(events,user){
  if(!user?.data.id||user.data.turn==null)return null;
  const verified=missingCatalogEvidence(events,user);
  if(!verified)return null;
  const {measure,period}=verified;
  return period
    ?period+'的'+measure.label+'暂无法统计，当前授权数据中缺少所需记录。'
    :'当前授权数据中缺少用于统计'+measure.label+'的记录。';
}

function periodProbe(call,sourceId){
  if(call.name!=='data_sql_query')return false;
  const raw=call.arguments??call.args;
  try{
    const args=typeof raw==='string'?JSON.parse(raw):raw;
    return args?.purpose==='explore'&&args.source_id===sourceId;
  }catch{return false;}
}

function supportedMonthlyObservationQuestion(current,user,questions,calls){
  const [proposed]=questions;
  if(questions.length!==1||proposed.id!=='monthly-observation-period'||proposed.multi_select
    ||proposed.options?.length!==2)return false;
  for(const event of current.slice().reverse()){
    const message=event.data?.message;
    if(event.type!=='tool/result'||message?.isError||message?.source?.kind!=='tool'
      ||calls.get(message.source.callId)?.name!=='data_catalog'
      ||(event.surfaceOp&&event.surfaceOp!=='append'))continue;
    for(const block of message.content||[]){
      if(block.type!=='text')continue;
      let catalog;
      try{catalog=JSON.parse(block.text);}catch{continue;}
      if(catalog?.status!=='succeeded'||catalog.execution?.operation!=='data.catalog'
        ||!catalog.project||catalog.execution_scope!==catalog.project||catalog.sources?.length!==1
        ||catalog.catalog_view?.next_source_offset!=null)continue;
      const [source]=catalog.sources;
      const plan=source?.monthly_observation_period;
      if(!plan)return false;
      const published=source.business_project??source.source_context?.business_project;
      const revision=source.connection_revision??source.source_context?.connection_revision??0;
      const expected=plan?.question;
      if(plan?.schema_version!==1||plan.request_ref!==user.data.id||plan.source_id!==source.id
        ||plan.quote!==(user.data.content||[]).map(b=>b.text).join('\n')||plan.connection_revision!==revision
        ||!plan.publication?.id||!plan.publication?.version
        ||published&&(plan.publication.id!==published.id||plan.publication.version!==published.version)
        ||expected?.id!==proposed.id||expected.question!==proposed.question||expected.header!==proposed.header
        ||expected.options?.length!==2)return false;
      return expected.options.every(({label,description},i)=>
        /^20\d{2}年1月至12月$/.test(label)&&proposed.options[i].label===label&&proposed.options[i].description===description);
    }
  }
  return false;
}

export function missingDataClarificationRejection(events,user,questions){
  if(!user?.data?.id||!questions.length)return null;
  const userText=(user.data.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n').trim();
  if(!userText||isVague(userText)||isDataOnly(userText)||materialNeedsPurpose(events))return null;
  if(currentDimensionChoice(events,user))return null;
  // The current request's declaration or native selection can settle its period.
  const declared=/以\s*20\d{2}\s*年(?:度)?\s*为准/.test(userText.normalize('NFKC'));
  const selected=clarificationEvidence(events,user.seq).clarifications.some(r=>PERIOD_QUESTION.test(r.question||'')
    &&(r.selected_labels.some(l=>TIME_ONLY_OPTION.test(baseLabel(l).replace(/\s+/g,'')))||/20\d{2}\s*年/.test(r.answer||'')));
  if((declared||selected)&&questions.every(q=>Array.isArray(q.options)&&q.options.length>=2
    &&q.options.every(o=>/\b(?:BETWEEN|IN|WHERE|GROUP BY|DATE_TRUNC)\b/i.test(o.label||'')))){
    return 'QUESTION_ALREADY_RESOLVED: 统计期间已明确。根据数据字段选择等价的查询条件，直接查询并交付已核实结果。';
  }
  if(questions.every(q=>periodDominated(q)&&PERIOD_QUESTION.test(q.question||''))){
    if(declared||selected)
      return 'QUESTION_ALREADY_RESOLVED: 用户已明确统计期间。按所选期间与原业务对象、筛选继续查询或直接说明缺项，不再重复询问年份。此反馈不是用户选择。';
  }
  const current=events.filter(e=>e.seq>user.seq&&(user.data.turn==null||e.data?.turn===user.data.turn));
  const calls=new Map(current.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e.data]));
  if(questions.some(question=>question.id==='monthly-observation-period'))
    return supportedMonthlyObservationQuestion(current,user,questions,calls)?null
      :'MISSING_DATA_NO_CLARIFICATION: 请使用本轮授权目录给出的年份选项。确认有可查的记录范围后再提问。';
  const queryTools=/^data_(?:sql_query|metric_query|query|report|plan_query|semantic_plan|derive_aggregation)$/;
  const engineClarifications=current.flatMap(e=>{
    const message=e.data?.message;
    if(e.type!=='tool/result'||!message?.isError||(e.surfaceOp&&e.surfaceOp!=='append'))return [];
    const call=calls.get(message.source?.callId||message.toolCallId);
    if(!queryTools.test(call?.name||''))return [];
    return (message.content||[]).filter(b=>b.type==='text'&&b.text.includes('NEEDS_CLARIFICATION:')).map(b=>({call,text:b.text}));
  });
  const sources=extractCatalogSources(events,user.seq);
  if(!sources.length)return null;
  if(sources.some(s=>Array.isArray(s.same_name_metrics)&&s.same_name_metrics.length))return null;

  // Refuse an ineffectual choice. The model's own text cannot prove a data gap.
  const reason='MISSING_DATA_NO_CLARIFICATION: 这些选项无法解决当前业务问题。继续用授权字段和业务定义核对。'
    +'先交付可核实的部分，仅说明已证实的具体缺项。确有可查且影响查询结果的业务选择时，按对应字段或口径提问。';
  const hasOptions=q=>Array.isArray(q.options)&&q.options.length>=2;
  const onlyPeriod=q=>hasOptions(q)&&q.options.every(o=>TIME_ONLY_OPTION.test(String(o.label||'').trim()));
  const uncovered=uncoveredCatalogTerms(sources).filter(term=>userText.includes(term)&&!catalogHasSubject(sources,term));
  if(engineClarifications.length){
    if(sources.length!==1||!questions.every(q=>hasOptions(q)&&q.options.every(o=>PERIOD_LABEL.test(baseLabel(o.label).replace(/\s+/g,''))))
      ||engineClarifications.some(({text})=>!ENGINE_YEAR_CONFLICT.test(text)))return null;
    // A date conflict on a sample read does not prove the business query can run.
    if(engineClarifications.every(({call})=>periodProbe(call,sources[0].id))
      &&!catalogLabelsMatch(sources,text=>text.length>=3&&userText.includes(text)))
      return uncovered.length?reason:'MISSING_DATA_NO_CLARIFICATION: 取值探查尚未证明统计所需记录可查。先在该授权来源核对业务表、编码和字段。只有可按同一口径查询且年份会影响结果时，再请用户选择期间。此反馈不是用户选择。';
    return null;
  }
  if(questions.every(q=>onlyPeriod(q)&&CHOICE_CANNOT_HELP.test(q.question||'')))return reason;

  if(uncovered.length){
    const allPeriodOrUnrelated=questions.every(q=>hasOptions(q)
      &&(onlyPeriod(q)
        ||q.options.every(o=>uncovered.every(term=>!String(o.label||'').includes(term)&&!String(o.description||'').includes(term)))));
    if(allPeriodOrUnrelated)return reason;
  }

  // Judge queryability before clarifying: when a completed, targeted search of
  // the single fully enumerated source shows no authorized field, caliber or
  // example for the measured object, a period choice cannot make it queryable.
  const subjects=requestedSubjects(userText);
  if(subjects.length&&sources.length===1&&questions.every(q=>periodDominated(q)&&PERIOD_QUESTION.test(q.question||''))
    &&!subjects.some(subject=>catalogHasSubject(sources,subject))
    &&catalogSearchSettled(events,user,subjects))return reason;

  if(isAmbiguousMeasure(userText)){
    const match=userText.match(/([\p{Script=Han}A-Za-z0-9]{2,16})的[「“"']?[^「“"'\n]{0,6}(?:规模|体量|水平|表现|热度|活跃度)/u);
    const subject=match?.[1]?.replace(/^(?:20\d{2}年(?:\d{1,2}月)?|今年|去年|前年|本年|当年|上年|本年度|上年度|全省|全市|全区|全国)+/,'');
    if(subject&&subject.length>=2&&!GENERIC_SUBJECT.test(subject)&&!catalogHasSubject(sources,subject)){
      const optionsMentionSubject=questions.some(q=>(q.options||[]).some(o=>String(o.label||'').includes(subject)||String(o.description||'').includes(subject)));
      const optionsNameOtherMetrics=questions.every(q=>hasOptions(q)&&q.options.every(option=>sources.some(s=>
        [...(s.semantic_metrics||[]),...(s.metric_definitions||[])].some(metric=>baseLabel(metric.name)===baseLabel(option.label)))));
      const proposesDifferentMetrics=questions.every(q=>hasOptions(q)&&LOOKS_FOR_UNRELATED_METRIC.test(q.question||''));
      if(!optionsMentionSubject&&(optionsNameOtherMetrics||proposesDifferentMetrics))return reason;
    }
  }
  return null;
}

export function installPurposeQuestions(ctx) {
  ctx.on('system-prompt/assemble',async(_assembly,{agent},next)=>{
    const assembly=await next();
    if(agent?.session.header?.agentPreset!=='natural-data'||!exhaustedOptionalFollowup(agent.session.snapshotEvents()))return assembly;
    // A terminal history gap was already explained. Keep the next request on
    // delivery. A later typed ambiguity or a new turn restores the question tool.
    return {...assembly,tools:assembly.tools.filter(tool=>tool.name!=='ask_user_question')};
  });
  ctx.on('tools/pre-execute',async(exec,next)=>{
    if(exec.name!=='ask_user_question'||!exec.agent||exec.agent.session.header?.agentPreset!=='natural-data')return next();
    const events=exec.agent.session.snapshotEvents();
    const last=events.filter(e=>e.type==='user/message'&&e.data.source?.kind==='user').at(-1);
    const text=(last?.data.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
    const questions=Array.isArray(exec.arguments?.questions)?exec.arguments.questions:[];
    if(questions.some(internalPlaceholderQuestion))
      return {kind:'deny',reason:'NON_USER_QUESTION: 原生澄清只能询问完成业务问题所缺少、需要用户决定的信息。内部核对、占位或要求忽略的内容不能弹给用户；请继续核对已有目录和结果，确有业务歧义时提出具体问题。此反馈不是用户回答。'};
    const workRejection=workMeasureQuestionRejection(text,questions);
    if(workRejection)return {kind:'deny',reason:workRejection};
    const dimensionRejection=dimensionQuestionRejection(events,last,questions);
    if(dimensionRejection)return {kind:'deny',reason:dimensionRejection};
    const missingRejection=missingDataClarificationRejection(events,last,questions);
    if(missingRejection)return {kind:'deny',reason:missingRejection};
    if(optionalSnapshotQuestion(events,last,questions))
      return {kind:'deny',reason:'OPTIONAL_FOLLOWUP: 授权目录已确认该表仅提供当前有效快照。保留原问的历史时点、统计对象和地区，核对可用的同口径历史证据。若本轮无法核实，直接说明缺少的历史快照或可还原当时状态的记录。改查当前快照属于另一个问题。本询问未发出，也没有用户选择。'};
    if(optionalPresentationQuestion(events,questions))
      return {kind:'deny',reason:'OPTIONAL_FOLLOWUP: 当前应直接交付已核对结果和数据边界。后续展示、拆分或补数据可简述于正文；请结束本轮提问，不重复询问或替用户改查其他指标。此反馈不是用户选择。'};
    if(/首次.{0,8}(获证|发证)/.test(text)&&/(?:不要|不采用|不接受|拒绝).{0,15}(?:发证|替代)/.test(text)
       &&questions.some(q=>/口径|(?:本年|当年|今年|20\d{2}年).*发证/.test(q.question)))
      return {kind:'deny',reason:'QUESTION_ALREADY_RESOLVED: 用户已要求统计首次获证且拒绝替代口径。查看授权目录；缺少依据时直接说明所需数据，不再询问同一选择。'};
    return next();
  },{prepend:true});
  ctx.on('user-questions/request',async(request,next)=>{
    if(request.agent?.session?.header?.agentPreset==='natural-data'&&Array.isArray(request.questions)){
      // The native composer localizes recommendation badges and submits the
      // original label. Keep that identity for both active and resumed questions.
      const questions=request.questions.map(q=>{
        if(q.intent||!Array.isArray(q.options))return q;
        const options=q.options.map(option=>{
          const description=chineseQuestionLabel(option.description);
          return description===option.description?option:{...option,description};
        });
        return options.every((option,index)=>option===q.options[index])?q:{...q,options};
      });
      if(questions.some((question,index)=>question!==request.questions[index]))request.questions=questions;
    }
    const events=request.agent?.session.snapshotEvents?.();
    if(!events||!materialNeedsPurpose(events)||request.questions.some(q=>q.intent))return next();
    if(request.questions.some(q=>/用途|做什么|如何使用/.test(q.question)))return next();
    const first=request.questions[0];
    if(!first)return next();
    request.questions=[{
      id:first.id,header:'确认材料用途',
      question:'已收到这些指标材料。你希望如何使用它们？',
      options:[{label:'解释指标口径',description:'说明指标含义和计算口径。'},{label:'整理为指标清单',description:'整理材料，不执行数据查询。'},{label:'查询或分析数据',description:'确认查询用途后再补充所需期间与筛选条件。'}]
    }];
    return next();
  },{prepend:true});
}
