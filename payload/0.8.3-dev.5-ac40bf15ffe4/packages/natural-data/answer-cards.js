// 0.6.2: which result cards go under the answer. Exploration queries, field
// statistics and superseded results of the same task move into a collapsed
// "查询过程" list, so the main answer stays visible.
import {uniqueCountShareDeliveries,uniqueSqlProfileDeliveries} from './answer-delivery.js';
import {mergeResultReceipt,replacedResultRefs} from './result-lineage.js';
import {explicitChartType,isExploratory} from './presentation-intent.js';
export const NO_CARD_TOOLS=new Set(['data_inspect']);

// Numbers written in the answer, e.g. "1,994 本", "46.4%", "26,600.74万元".
export function answerNumbers(text){
  const dates=/(?<![\d.,])(?:19|20)\d{2}\s*(?:年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?)?|[-/]\s*(?:0?[1-9]|1[0-2])(?!\d)(?:[-/]\s*\d{1,2}(?!\d))?)|(?<!\d)\d{1,2}\s*月/g;
  return [...String(text||'').replace(dates,' ').matchAll(/-?\d[\d,]*(?:\.\d+)?/g)].map(m=>Number(m[0].replaceAll(',',''))).filter(Number.isFinite);
}

const close=(n,v)=>Math.abs(n-v)<=Math.max(.011,Math.abs(v)*.0005)||Math.abs(n-v*100)<=.06;
const numeric=v=>typeof v==='number'&&Number.isFinite(v)||typeof v==='string'&&/^-?\d+(?:\.\d+)?$/.test(v);
function timeColumn(column,value){
  return /^(?:年|年份|年度|月|年月|月份|日期|期间|统计期间|period|stat_year|year|month|date)$/i.test(column||'')
    ||(Array.isArray(value?.period_coverage)&&value.period_coverage.some(p=>p?.column===column&&timeKey(p.from)&&timeKey(p.to)));
}
function timeKey(value){
  const match=/^([0-9]{4})(?:\s*(?:[-/]|年\s*)(0?[1-9]|1[0-2])\s*月?)?\s*年?$/.exec(String(value??'').trim());
  return match?match[1]+(match[2]?'-'+match[2].padStart(2,'0'):''):null;
}
const sourceOf=value=>value?.result_lineage?.source_id||value?.receipt?.source_id;
const purposeOf=value=>value?.result_lineage?.purpose||value?.purpose;
// The engine proves a raw-dimension ranking in this turn's own scope check.
const rawRanking=value=>Array.isArray(value?.validation?.request_scope?.constraints)
  &&value.validation.request_scope.constraints.some(item=>item?.kind==='raw_dimension_grouping');
// One delivered table has one identity: a population the engine proved to be
// the same, plus its own rows. Two equal tables over *unproven* different
// populations are an ambiguity the answer must keep visible, so a missing
// population fingerprint is never treated as a duplicate.
function tableIdentity(value){
  const rows=value?.values?.rows;
  if(!Array.isArray(rows)||!rows.length)return null;
  const lineage=value?.result_lineage;
  const population=lineage?.population_fingerprint;
  if(population)return JSON.stringify([sourceOf(value)||null,population,value.values?.columns||[],rows]);
  if(!sourceOf(value)||!lineage?.request_ref||!lineage.session_id||!lineage.config_fingerprint
    ||lineage.purpose!=='answer'||!value.query_identity||value.receipt?.read_only!==true
    ||value.values.complete!==true||value.validation?.state==='conflict')return null;
  return JSON.stringify([sourceOf(value),lineage.request_ref,lineage.session_id,lineage.config_fingerprint,
    value.query_identity,value.values.columns,rows]);
}

function availableFiscalTitle(value){
  const proof=value.validation?.published_report_budget;
  const spending=value.validation?.published_report_spending;
  const {columns,rows}=value.values||{};
  if(!Array.isArray(columns)||!Array.isArray(rows)||rows.length!==1||!Array.isArray(rows[0]))return value;
  const present=column=>columns.includes(column)&&numeric(rows[0][columns.indexOf(column)]);
  if(spending?.period_kind==='current_project_spending'&&present(spending.columns?.projects)&&!present(spending.columns?.amount)){
    return {...value,title:spending.as_of_date?.slice(0,4)+'年度项目数量与支出报送覆盖'};
  }
  if(proof?.period==='current_year'&&present(proof.count_column)
    &&Object.keys(proof.measurements||{}).length>0&&!Object.keys(proof.measurements).some(present)){
    return {...value,title:proof.as_of_date?.slice(0,4)+'年度项目数量与预算报送覆盖'};
  }
  return value;
}
function sameColumns(a,b){
  return JSON.stringify(a?.values?.columns||[])===JSON.stringify(b?.values?.columns||[])
    &&(sourceOf(a)||null)===(sourceOf(b)||null);
}
function prefixOf(short,long){
  const rows=short?.values?.rows||[],full=long?.values?.rows||[];
  return rows.length>0&&rows.length<full.length
    &&rows.every((row,index)=>JSON.stringify(row)===JSON.stringify(full[index]));
}

function measureNumbers(value){
  return (value?.values?.rows||[]).flatMap(row=>row.flatMap((v,i)=>numeric(v)&&!timeColumn(value.values.columns?.[i],value)?[Number(v)]:[])).concat((value?.facts||[]).filter(f=>typeof f.value==='number').map(f=>f.value));
}

function citedSmallScalars(cards,answer){
  const matches=new Map();
  const claims=new Set([...answer.replaceAll('**','').matchAll(/(?<![\d.\-+])(?:0|[1-9]|1[0-2])\s*(?:条|件|家|本|张|笔|宗|单|台次|次|人|户|个(?!月))(?![\p{N}])/gu)]
    .map(match=>match[0].replace(/\s/g,'')));
  if(!claims.size)return new Set();
  for(const card of cards){
    const {value}=card,{columns,rows}=value.values||{};
    if(value.receipt?.read_only!==true||purposeOf(value)!=='answer'||value.validation?.state==='conflict'
      ||columns?.length!==1||rows?.length!==1||timeColumn(columns[0],value)||!numeric(rows[0][0]))continue;
    const unit=value.analysis_options?.column_units?.[columns[0]]||value.column_unit_evidence?.[columns[0]]?.unit;
    const key=String(Number(rows[0][0]))+unit;
    if(!claims.has(key))continue;
    matches.set(key,[...(matches.get(key)||[]),card]);
  }
  return new Set([...matches.values()].filter(items=>items.length===1).flat());
}

function cardRows(value){
  const facts=value.facts||[];
  if(facts.length&&facts.some(f=>Object.keys(f.dimensions||{}).length)){
    return facts.map(f=>{
      const {period,...dimensions}=f.dimensions||{};
      return {period:timeKey(period),labels:Object.values(dimensions),measures:numeric(f.value)?[Number(f.value)]:[]};
    });
  }
  const {rows=[],columns=[]}=value.values||{};
  const dates=columns.map((c,i)=>timeColumn(c,value)&&rows.some(row=>timeKey(row[i]))?i:-1).filter(i=>i>=0);
  const dimensions=new Set(rows.flatMap(row=>row.flatMap((v,i)=>
    typeof v==='string'&&!numeric(v)&&!dates.includes(i)?[i]:[])));
  return rows.map(row=>({
    period:dates.length===1?timeKey(row[dates[0]]):null,
    labels:[...dimensions].map(i=>row[i]),
    measures:row.flatMap((v,i)=>!dimensions.has(i)&&!dates.includes(i)&&numeric(v)?[Number(v)]:[])
  }));
}

function answerClauses(answer){
  return answer.replaceAll('**','').split(/[\n。；;]/).filter(Boolean).map(text=>({
    text,numbers:answerNumbers(text),
    periods:new Set([...text.matchAll(/([0-9]{4})\s*(?:[-/]\s*(0?[1-9]|1[0-2])(?!\d)|年(?:\s*(0?[1-9]|1[0-2])\s*月)?|(?=\s*\|))/g)]
      .map(m=>timeKey(m[1]+((m[2]||m[3])?'-'+(m[2]||m[3]):''))).filter(Boolean)),
    months:new Set([...text.matchAll(/(?:^|[^\d])(0?[1-9]|1[0-2])\s*月/g)].map(m=>m[1].padStart(2,'0')))
  }));
}

function periodCited(clause,period,singleYear){
  if(clause.periods.has(period))return true;
  if(period.length!==7||!singleYear||!clause.months.has(period.slice(5)))return false;
  return [...clause.periods].every(p=>p.length===4&&p===period.slice(0,4));
}

function matchingRows(rows,clauses,{singleYear=false,allowYearless=false}={}){
  return rows.map(row=>clauses.some(clause=>
    row.labels.every(label=>label!==null&&label!==undefined&&String(label).trim()&&clause.text.includes(String(label)))
    && (!row.period||periodCited(clause,row.period,singleYear)||(allowYearless&&row.period.length===4&&!clause.periods.size&&!clause.months.size))
    && row.measures.some(v=>clause.numbers.some(n=>close(n,v)))));
}

// A monthly row needs a month, category and matching value in the same
// statement. A yearly category table can inherit its single year as context.
function groupedCitation(rows,clauses){
  const dates=new Set(rows.map(row=>row.period).filter(Boolean));
  const categorized=rows.some(row=>row.labels.length);
  if(!categorized&&!dates.size)return null;
  const years=new Set([...dates].map(p=>p.slice(0,4)));
  const matching=matchingRows(rows,clauses,{singleYear:years.size===1,allowYearless:dates.size===1}).some(Boolean);
  // Date-only cards have legacy prose that sometimes reports several values
  // without restating every date. A categorized series always needs both.
  return matching||(!categorized?null:false);
}

function cited(value,answer,numbers,rows,clauses){
  const title=String(value?.title||'').trim();
  if(!numbers.length&&title.length>=4&&answer.includes(title))return true;
  const grouped=groupedCitation(rows,clauses);
  if(grouped!==null)return grouped;
  const columns=value?.values?.columns||[],values=value?.values?.rows||[];
  const rate=c=>/率|比例|占比|百分比|%|percent|ratio|rate/i.test(String(c));
  const time=c=>timeColumn(c,value)||/年|月|日|期间|period|date|year|month/i.test(String(c));
  // Equal percentages do not establish equal populations. A mixed count/rate
  // card needs a matching non-rate measure too; otherwise it would assert an
  // unreferenced denominator merely because both sources happen to say 100%.
  if(columns.some(rate)){
    const counts=values.flatMap(row=>columns.flatMap((c,i)=>!rate(c)&&!time(c)&&typeof row[i]==='number'?[row[i]]:[]));
    if(counts.length&&!counts.some(v=>numbers.some(n=>Math.abs(n-v)<=Math.max(.011,Math.abs(v)*.0005))))return false;
  }
  return measureNumbers(value).some(v=>!(Number.isInteger(v)&&v>=0&&v<=12)&&numbers.some(n=>close(n,v)));
}

// Which answer numbers a card supports, and whether it also holds numbers the
// answer does not use (an intermediate breakdown sharing only the total).
function support(value,numbers){
  const hit=new Set();let miss=0;
  // Zero and small counts are real conflicting measures too. Exclude periods
  // by shape, not every integer below thirteen (which hid failed enum splits).
  for(const v of measureNumbers(value)){
    const found=numbers.map((n,i)=>close(n,v)?i:-1).filter(i=>i>=0);
    if(found.length)found.forEach(i=>hit.add(i));else miss++;
  }
  return {hit,miss};
}

function namedMeasures(value,clauses){
  const columns=value.values?.columns||[],row=value.values?.rows?.[0]||[];
  const hits=new Set();
  for(let i=0;i<columns.length;i++){
    if(!numeric(row[i])||timeColumn(columns[i],value))continue;
    const label=String(columns[i]).replace(/[（(].*?[）)]$/,'').replace(/(?:数量|数)$/,'');
    if(label.length<2)continue;
    if(clauses.some(({text})=>{
      const at=text.indexOf(label);
      if(at<0)return false;
      const phrase=text.slice(at+label.length).split(/[，、；;|]|,(?!\d{3}\b)/,1)[0].slice(0,26);
      const number=answerNumbers(phrase)[0];
      return number!==undefined&&close(number,Number(row[i]));
    }))hits.add(label);
  }
  return hits;
}

// What a card counts besides its numbers: metric, periods, filters, grouping
// and the dimension values of its facts.
function scopeOf(value){
  const c=value?.metric_contract||{},periods=new Set([...(c.periods||[]),...(c.scope?.periods||[])]),dims=new Set();
  for(const f of value?.facts||[])for(const [k,v] of Object.entries(f?.dimensions||{}))k==='period'?periods.add(String(v)):dims.add(k+'='+v);
  for(const p of value?.period_coverage||[])periods.add(`${p.column}:${p.from}~${p.to}`);
  return JSON.stringify([c.id||null,Boolean(c.detail),[...periods].sort(),c.scope?.filters||[],c.default_filters||[],c.scope?.dimensions||[],[...dims].sort()]);
}

// Earlier scalar diagnostics can share only the total with the final answer.
// This is evidence selection, not a claim that either dataset replaces the other.
function dropCovered(main,numbers,clauses){
  const profile=new Map(main.map(r=>[r,{...support(r.value,numbers),named:namedMeasures(r.value,clauses)}]));
  const scalar=r=>r.value.values?.rows?.length===1&&r.value.values.rows[0].every(numeric)
    && !(r.value.values?.columns||[]).some(timeColumn);
  const related=(a,b)=>{
    const x=a.value,y=b.value;
    if(!scalar(a)||!scalar(b)||scopeOf(x)!==scopeOf(y))return false;
    if(sourceOf(x)!==sourceOf(y)||(x.result_lineage?.config_fingerprint||null)!==(y.result_lineage?.config_fingerprint||null))return false;
    if(x.result_lineage&&y.result_lineage&&x.result_lineage.population_fingerprint!==y.result_lineage.population_fingerprint)return false;
    if(x.query_identity&&x.query_identity===y.query_identity)return true;
    // Legacy same-task scalar receipts predate population fingerprints.
    return !sourceOf(x)&&!sourceOf(y)&&!x.result_lineage&&!y.result_lineage&&Boolean(x.task_id)
      &&x.task_id===y.task_id&&Boolean(x.title)&&x.title===y.title;
  };
  return main.filter(a=>{
    const pa=profile.get(a);
    return !main.some(b=>{
      if(b.seq<=a.seq||!related(a,b))return false;
      const pb=profile.get(b);
      if(pa.miss&&pb.hit.size>pa.hit.size&&[...pa.hit].every(i=>pb.hit.has(i)))return true;
      return pb.named.size>pa.named.size&&[...pa.named].every(name=>pb.named.has(name));
    });
  });
}

const FORECAST=/预测|预估|预报|外推|forecast/i;
const HISTORICAL_DELIVERY=/(?:同时|并且?|另外|还要|再)(?:请|帮我)?(?:展示|给出|列出|提供|画|绘制|统计|分析)[^，。；\n]{0,28}(?:历史|历年|往年|过去|实际|原始|逐月|趋势)|(?:历史|历年|往年|过去|实际)[^，。；\n]{0,20}(?:趋势图|图表|明细表)|(?:近|过去)[^，。；\n]{0,7}年[^，。；\n]{0,22}(?:趋势|走势)[^，。；\n]{0,20}(?:和|与|及|并)[^，。；\n]{0,14}预测/;
const TWO_CHARTS=/(?:两|二|2)\s*(?:张|幅|个|份)\s*(?:[\p{Script=Han}]{0,10}?)图(?:表)?|双图/u;
const CHART_COMPARISON=/对比|比较|对照|并排|分别(?:展示|给出|绘制|画)|分开(?:展示|画)|(?:都|同时)保留|保留.{0,10}(?:两|二|2)\s*(?:张|幅|个|份)/;
const CHANGE_CHART_COUNT=/(?:不要|不用|无须|无需|不需要|不必|不做|不留)\s*(?:画|绘制|展示|做|保留|对照|对比|比较)?\s*(?:两|二|2)\s*(?:张|幅|个|份)|(?:改成|改为|只(?:要|留|看|画|给|用|展示)|仅(?:要|留|看|画|给|用|展示))\s*(?:一|1)\s*(?:张|幅|个|份)/;
const COMPARE_WINDOW=/(?<!\d)((?:19|20)\d{2})(?:\s*年\s*(0?[1-9]|1[0-2])\s*月|[-/](0?[1-9]|1[0-2])(?!\d)|\s*年)?\s*(?:至|到|[-–—~])\s*((?:19|20)\d{2})(?:\s*年\s*(0?[1-9]|1[0-2])\s*月|[-/](0?[1-9]|1[0-2])(?!\d)|\s*年)?/g;

function requestedChartPair(userText){
  const question=String(userText||'').normalize('NFKC');
  const clause=question.split(/[。；;！？!?\n]/).find(part=>TWO_CHARTS.test(part)&&CHART_COMPARISON.test(part));
  if(!clause||CHANGE_CHART_COUNT.test(question))return null;
  const periods=[...clause.matchAll(COMPARE_WINDOW)].flatMap(([,year,month,iso,endYear,endMonth,endIso])=>{
    const from=timeKey(year+((month||iso)?'-'+(month||iso):''));
    const to=timeKey(endYear+((endMonth||endIso)?'-'+(endMonth||endIso):''));
    return from?.length===to?.length&&from<to?[{from,to}]:[];
  });
  const counts=[...clause.replace(/(?:19|20)\d{2}\s*年\s*(?:\d{1,2}\s*月)?/g,' ')
    .matchAll(/(?<![\d.])(\d+)\s*(个?月|年)(?![度份])/g)];
  if(periods.length===2&&JSON.stringify(periods[0])!==JSON.stringify(periods[1]))return {periods,kind:explicitChartType(clause)};
  if(periods.length||counts.length!==2||counts[0][2].endsWith('月')!==counts[1][2].endsWith('月')
    ||Number(counts[0][1])===Number(counts[1][1]))return null;
  const width=counts[0][2].endsWith('月')?7:4;
  return {periods:counts.map(match=>({count:Number(match[1]),width})),kind:explicitChartType(clause)};
}

function matchedComparisonCards(cards,rows,userText,forecast){
  const requested=requestedChartPair(userText);
  if(!requested)return [];
  const matches=requested.periods.map(period=>cards.filter(card=>{
    const value=card.value,actual=value.visualization?.actual;
    if(value.status!=='succeeded'||value.truncated||value.validation?.state==='conflict'
      ||value.receipt?.read_only!==true||purposeOf(value)!=='answer'||forecast.includes(card)
      ||!actual||actual==='table'||value.visualization.fallback||requested.kind&&requested.kind!==actual)return false;
    const observed=rows.get(card);
    if(!observed.length||observed.some(row=>!row.period||row.labels.length||!row.measures.length)
      ||new Set(observed.map(row=>row.period)).size!==observed.length)return false;
    const [cover,...others]=value.period_coverage||[];
    const ordered=observed.map(row=>row.period).sort();
    const from=cover&&!others.length&&timeColumn(cover.column,value)?timeKey(cover.from):ordered[0];
    const to=cover&&!others.length&&timeColumn(cover.column,value)?timeKey(cover.to):ordered.at(-1);
    if(!from||!to||from.length!==to.length||from>ordered[0]||to<ordered.at(-1))return false;
    if(period.count!==undefined)return Number.isSafeInteger(value.row_count)&&value.row_count>=observed.length
      &&(value.row_count===observed.length||Boolean(cover&&!others.length&&timeColumn(cover.column,value)))
      &&value.row_count===period.count&&from.length===period.width;
    return from===period.from&&to===period.to;
  }));
  if(!matches.every(candidates=>candidates.length===1))return [];
  const [[a],[b]]=matches;
  const source=sourceOf(a.value);
  if(a===b||!source||source!==sourceOf(b.value)||a.reused!==b.reused
    ||(a.value.business_project?.id||null)!==(b.value.business_project?.id||null)
    ||(a.value.business_project?.version??null)!==(b.value.business_project?.version??null))return [];
  // New receipts must agree on their native request. Legacy receipts arrive
  // from this turn's projection and carry only source and publication.
  if((a.value.result_lineage||b.value.result_lineage)&&!sharesRequestAndSource(a,b))return [];
  return [a,b];
}

const YEARLY_RANK=/(?:本年|今年|全年|年度|年内|[0-9]{4}\s*年).{0,75}(?:哪个|哪类|排名|排行|最多|最少|最高|最低|第一|top)/i;
const TIME_BREAKDOWN=/每(?:个)?月|逐月|按月|月度|各月|当月|分月|各季度|按季度|逐季|分季|同比|环比|对比|比较/;
const PERIOD_REQUESTS=[
  /按(?:年份|年度|年)(?![底末龄级])|逐年|每(?:个)?年(?:份|度)?|各年|年报/,
  /按季度|逐季|每(?:个)?季度|各季度|季报/,
  /每(?:个)?月|逐月|按月|月度|各月|分月/,
  /按日|逐日|每天|每日/,
];
const TOTAL_REQUEST=/全年|年度|全期|本年|整体/;
const TOTAL_MEASURE=/合计|总计|总数|总量|总额|累计|汇总/;
const ADD_DELIVERY=/^(?:另|也|还|再|同时|请|给出|提供|列出|展示|统计|算出|计算|并|和|与|及)/;
const COUNT_COLUMN=/数量|计数|件数|条数|笔数|记录数|案卷数|办理量|[（(]\s*(?:件|条|笔|份|个|家|人)\s*[）)]$|(?:^|_)count(?:$|_)/i;

function separatePeriodDelivery(text){
  if(PERIOD_REQUESTS.filter(pattern=>pattern.test(text)).length>1)return true;
  return text.split(/[，、。；;\n]|(?=(?:以及|同时|并|另外|和|与|及)(?:还|再|给|列|算|统计|全|本|年度|总|合|汇))/).some(clause=>
    !PERIOD_REQUESTS.some(pattern=>pattern.test(clause))&&(
      TOTAL_REQUEST.test(clause)&&TOTAL_MEASURE.test(clause)
      || ADD_DELIVERY.test(clause.trim())&&TOTAL_MEASURE.test(clause)
      || /(?:对比|比较).{0,14}(?:全期|全年|上年|其他期间)/.test(clause)));
}

const periodProof=card=>card?.value.validation?.period_grain;

function outsideRequest(card){
  const value=card.value,proof=value.validation?.result_period_scope,ref=value.result_lineage?.request_ref;
  return !card.reused&&proof?.state==='outside_request'&&proof.result_ref===value.result_ref
    &&Boolean(ref)&&proof.request_ref===ref;
}

function monthlyPrimary(rows,card){
  if(card.value.status!=='succeeded'||card.value.truncated)return false;
  const months=new Set(rows.filter(row=>row.period?.length===7&&row.measures.length).map(row=>row.period));
  const coverage=card.value.answer_contract?.monthly_sql_coverage;
  return months.size>=2||Boolean(months.size&&coverage?.month_column&&coverage.requested_months?.length>=2);
}

function sharesRequestAndSource(a,b){
  const left=a.value.result_lineage,right=b.value.result_lineage;
  return Boolean(left?.request_ref&&left?.source_id&&left?.session_id&&left?.config_fingerprint
    && left.source_id===right?.source_id&&left.session_id===right.session_id
    && left.config_fingerprint===right.config_fingerprint
    && JSON.stringify(left.request_ref)===JSON.stringify(right.request_ref));
}

function boundPeriodPrimary(card,byRef,candidates){
  const auxiliary=periodProof(card);
  if(auxiliary?.state!=='auxiliary'||card.reused||!auxiliary.source_result_ref)return null;
  const primary=byRef.get(auxiliary.source_result_ref),proof=periodProof(primary);
  if(!primary||primary===card||primary.reused||!candidates.has(primary)||!sharesRequestAndSource(card,primary)
    ||primary.value.status!=='succeeded'||primary.value.truncated||proof?.state!=='checked'
    ||proof.actual!==auxiliary.requested)return null;
  return primary;
}

function monthlyCheckCard(card,primary,rows){
  const value=card.value,columns=value.values?.columns||[];
  const shape=rows.get(card);
  return !value.metric_contract?.detail&&!value.report&&columns.length>0&&columns.every(column=>COUNT_COLUMN.test(column))
    && shape.length===1&&!shape[0].period&&!shape[0].labels.length&&shape[0].measures.length===columns.length
    && primary.some(main=>sharesRequestAndSource(card,main)
      && (main.value.values?.columns||[]).some(column=>COUNT_COLUMN.test(column)));
}

function forecastCard(card){
  if(card.value.status!=='succeeded'||purposeOf(card.value)==='explore')return false;
  return card.currentClaims.some(claim=>{
    const stats=claim.statistics;
    return claim.operation==='matrix_method'&&(stats?.kind==='forecast'||claim.method_id==='matrix.42')
      && Boolean(timeKey(stats?.origin)||stats?.history?.periods?.length||stats?.forecast_periods?.length)
      && (!stats?.source_result_ref||stats.source_result_ref===card.value.result_ref)
      && Array.isArray(stats?.predicted)&&stats.predicted.length>0&&stats.predicted.every(Number.isFinite)
      && Array.isArray(stats.interval_95)&&stats.interval_95.length===stats.predicted.length
      && stats.interval_95.every(interval=>Array.isArray(interval)&&interval.length===2&&interval.every(Number.isFinite));
  });
}

function latestOfTask(cards){
  const latest=new Map();
  for(const card of cards){
    const task=card.value.task_id;
    if(!task)continue;
    const previous=latest.get(task);
    if(!previous||card.querySeq>=previous.querySeq)latest.set(task,card);
  }
  return card=>!card.value.task_id||latest.get(card.value.task_id)===card;
}

// Exploration and earlier work remain available in the query process.
export function pickAnswerCards(results,answer='',failed=false,{userText='',comparisonPreviewRef}={}){
  const shown=results.filter(r=>!NO_CARD_TOOLS.has(r.name));
  // Analysis/visualization receipts may update the same immutable result while
  // omitting its facts and scope. Preserve those query fields when projecting
  // later partial receipts; otherwise a cited comparison card loses its data.
  const byRef=new Map();
  for(const card of shown){
    const ref=card.value.result_ref;
    const prior=byRef.get(ref);
    const currentClaims=card.name==='analysis_method'&&!card.reused&&card.value.status==='succeeded'
      ?[...(card.value.claims||[]),...(card.value.verified_claims||[])]:[];
    byRef.set(ref,{...card,querySeq:prior?.querySeq??card.seq,
      currentClaims:[...(prior?.currentClaims||[]),...currentClaims],
      value:mergeResultReceipt(prior?.value,card.value)});
  }
  const all=[...byRef.values()];
  const replaced=replacedResultRefs(all.map(r=>r.value));
  const candidates=all.filter(r=>!isExploratory(r.value)&&!outsideRequest(r)&&!replaced.has(r.value.result_ref));
  const current=latestOfTask(candidates);
  const numbers=answerNumbers(answer);
  const clauses=answerClauses(answer);
  const rows=new Map(candidates.map(card=>[card,cardRows(card.value)]));
  const forecast=candidates.filter(forecastCard);
  const separate=separatePeriodDelivery(userText);
  const annualRank=YEARLY_RANK.test(userText)&&!TIME_BREAKDOWN.test(userText)&&!separate;
  // The server proves a raw-dimension ranking in this turn's own scope check.
  // That proof must not depend on the host forwarding user text: with it, a
  // month-by-category crosstab is a supporting probe, never the answer.
  const rankingProven=candidates.some(card=>rawRanking(card.value));
  const monthlyBreakdown=card=>(annualRank||rankingProven)
    &&rows.get(card).some(row=>row.labels.length&&row.period?.length===7);
  const monthly=!separate&&PERIOD_REQUESTS[2].test(userText)
    ?candidates.filter(card=>monthlyPrimary(rows.get(card),card)):[];
  const scopedCards=new Set(candidates);
  const periodPrimaries=new Set(),periodAuxiliaries=new Set();
  if(!separate)for(const card of candidates){
    const primary=boundPeriodPrimary(card,byRef,scopedCards);
    if(primary){periodAuxiliaries.add(card);periodPrimaries.add(primary);}
  }
  const relevant=candidates.filter(card=>!monthlyBreakdown(card)&&!periodAuxiliaries.has(card)
    && (periodProof(card)||!monthlyCheckCard(card,monthly,rows)));
  const small=citedSmallScalars(relevant,answer);
  let main=failed?relevant.filter(current):dropCovered(relevant.filter(card=>
    small.has(card)||cited(card.value,answer,numbers,rows.get(card),clauses)),numbers,clauses);
  if(forecast.length){
    if(FORECAST.test(userText||answer)&&!HISTORICAL_DELIVERY.test(userText)){
      // The wider queries stay in the process. Only the verified source
      // is the training result for this turn's forecast.
      main=main.filter(card=>{
        if(card.value.metric_contract?.detail||!rows.get(card).some(row=>row.period))return true;
        return !forecast.some(training=>!sourceOf(card.value)||!sourceOf(training.value)
          ||sourceOf(card.value)===sourceOf(training.value));
      });
    }
    for(const card of forecast)if(!main.includes(card))main.push(card);
  }
  // A requested detail result remains visible when its receipt omits values.
  if(!failed){
    for(const card of candidates)if(card.value.status==='succeeded'&&card.value.metric_contract?.detail&&!main.includes(card))main.push(card);
    for(const card of matchedComparisonCards(relevant,rows,userText,forecast))if(!main.includes(card))main.push(card);
    const compared=relevant.find(card=>card.value.result_ref===comparisonPreviewRef&&!card.reused&&current(card));
    if(compared&&!main.includes(compared))main.push(compared);
    const lastAnswer=relevant.filter(card=>periodPrimaries.has(card)||monthly.includes(card)).at(-1)
      ??relevant.filter(current).at(-1);
    if(lastAnswer&&!main.length)main.push(lastAnswer);
  }
  // Equivalent formal summaries keep a single readable card. The native
  // receipts and earlier renderings remain available under the query process.
  const primary=new Set(uniqueCountShareDeliveries(uniqueSqlProfileDeliveries(main.map(card=>card.value)),userText));
  main=main.filter(card=>primary.has(card.value));
  // The identical table can arrive twice (a re-read, or the same delivery under
  // a second title). Keep the fullest rendering once; the other stays in the
  // query process instead of repeating the chart.
  const fullest=new Map();
  for(const card of main){
    const key=tableIdentity(card.value);
    if(!key)continue;
    const prior=fullest.get(key);
    // Equal rows: keep the untruncated receipt, else the first one.
    if(!prior||prior.value.truncated&&!card.value.truncated)fullest.set(key,card);
  }
  main=main.filter(card=>{
    const key=tableIdentity(card.value);
    if(key&&fullest.get(key)!==card)return false;
    // A truncated copy of a table that is also delivered in full adds nothing.
    return !(card.value.truncated&&main.some(other=>other!==card&&!other.value.truncated
      &&sameColumns(card.value,other.value)&&prefixOf(card.value,other.value)));
  });
  // Only exploration happened (e.g. "no data" answers): nothing is promoted.
  const process=all.filter(r=>!main.includes(r));
  return {main:main.sort((a,b)=>a.seq-b.seq).map(r=>availableFiscalTitle(r.value)),process:process.map(r=>availableFiscalTitle(r.value))};
}
