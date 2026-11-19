// Error receipts remain errors. Only counts bound to their source/metric/period
// can support physical absence; they never publish an uncovered metric value.
import {extractCatalogSources} from './tool-normalization.js';
import {requestText} from './request-context.js';
import {isExploratory} from './presentation-intent.js';
const plain=s=>String(s||'').replaceAll('**','');
function argumentsOf(call){
 try{return typeof (call?.args||call?.arguments)==='string'?JSON.parse(call.args||call.arguments):(call?.args||call?.arguments||{});}catch{return {};}
}
function periodsIn(text,year){
 const values=[];
 for(const m of text.matchAll(/(20\d{2})\s*(?:年\s*(\d{1,2})\s*月|-\s*(\d{2})(?!\d)|年?)|(\d{1,2})\s*月/g)){
  if(m[1])year=m[1];
  const month=m[2]||m[3]||m[4];
  if(year)values.push({period:year+(month?'-'+month.padStart(2,'0'):''),index:m.index});
 }
 return values;
}
function scopedPeriods(sentence,index,year){
 const previous=periodsIn(sentence.slice(0,index),year);
 if(!previous.length)return periodsIn(sentence,year).map(p=>p.period);
 // A comma starts a new assertion, but a list of months before “均无记录”
 // belongs to that assertion together.
 const boundary=Math.max(sentence.lastIndexOf('，',index),sentence.lastIndexOf(',',index));
 const local=previous.filter(p=>p.index>boundary);
 return (local.length?local:[previous.at(-1)]).map(p=>p.period);
}
function negated(sentence,match){
 const full=sentence.slice(0,match.index);
 const before=full.split(/[，,]|但是|然而|不过|但|因此|所以/).at(-1)||'';
 const after=sentence.slice(match.index+match[0].length).replace(/^[”」"']\s*/, '');
 return /(?:不代表|不等于|不等同(?:于)?|不意味(?:着)?|并非|而非|不是|尚未核实|尚未确定|无法确认|不能确认|未能核实|不将|不把|不视为|不当成|(?:不能|不可|不应|不要|无法)(?:据此|直接|简单|就此|就)?(?:说|认为|认定|确认|判断|证明|断言|把|将|写成|视为|计为|算作|当成|当作|作为|等同于))[^，,]{0,24}$/.test(before)
  ||/(?:不能因为|不能由于|即便|就算|哪怕|无论是否)[^。；\n]{0,30}$/.test(full)
  ||/(?:如果|假如|假设|若|如)\s*$/.test(before)
  ||/^(?:的)?(?:说法|结论)[^，,。]{0,12}(?:未经核实|不成立|不确定|没有依据)/.test(after);
}

export function monthlyObservationIssues(text,evidence){
 const uncovered=new Set();
 const observedZero=new Set();
 for(const item of evidence){
  const coverage=item.answer_contract?.monthly_sql_coverage;
  if(item.status!=='succeeded'||!coverage)continue;
  if(coverage.coverage==='unknown')for(const month of coverage.elapsed_unreturned_months||[])uncovered.add(month);
  if(!item.truncated&&item.receipt?.read_only===true&&coverage.query_window?.state==='checked'){
   for(const {period,value} of coverage.observations||[])if(value===0)observedZero.add(period);
   for(const period of coverage.observed_zero_months||[])observedZero.add(period);
  }
 }
 if(!uncovered.size&&!observedZero.size)return [];
 const years=new Set([...uncovered,...observedZero].map(month=>month.slice(0,4)));
 const year=years.size===1?[...years][0]:undefined;
 let previousPeriods=[];
 for(const sentence of plain(text).split(/[。；;\n]/)){
  const explicit=periodsIn(sentence,year).map(value=>value.period);
  for(const claim of sentence.matchAll(/数据缺失|缺测|(?:业务|服务)中断|(?:缺少|缺失|没有|未返回|未查到|无)(?:任何|可计入的|可用的)?[^，,。；;\n]{0,12}(?:记录|数据|观测)/g)){
   if(negated(sentence,claim))continue;
   if(/^(?:数据缺失|缺测|业务中断|服务中断)$/.test(claim[0])
       &&/(?:无|没有|未发生)\s*$/.test(sentence.slice(0,claim.index)))continue;
   const related=explicit.length?scopedPeriods(sentence,claim.index,year)
    :/^(?:\s*[（(])?\s*(?:这|该|上述|前述)月/.test(sentence)?previousPeriods:[];
   if(related.some(month=>observedZero.has(month))&&!related.some(month=>uncovered.has(month)))
    return ['交付缺项：该月有观测记录，记录值为 0。请准确报告零值，缺测月份保留为空。'];
  }
  if(!/(?:覆盖|报送)(?:情况)?(?:仍|尚|还)?(?:未知|待确认|未确认|不明)|(?:无法|不能|尚不能|尚未)(?:据此)?(?:核实|确认|判定|证明|判断)[^。；]{0,14}(?:覆盖|实际|业务)|(?:缺测|空值|缺少观测)[^。；]{0,15}(?:不代表|不等于|不能作)/.test(sentence)){
   for(const match of sentence.matchAll(/无(?:任何|实际|业务)?[^，,。；;\n]{0,8}(?:记录|数据)|没有(?:任何|实际|业务)?[^，,。；;\n]{0,8}(?:记录|数据)|(?:均|都|为|是|按|计为)\s*0\s*(?:件|条|宗|项|个)?(?![\d.])/g)){
    const before=sentence.slice(0,match.index);
    if(negated(sentence,match)||/(?:查询|查询结果|回执|已查范围)[^，,。；;]{0,14}(?:未返回|未查到|未观察到)\s*$/.test(before))continue;
    const periods=explicit.length?scopedPeriods(sentence,match.index,year):
      /^(?:\s*[（(])?\s*(?:这|该|上述|前述)(?:[两二三四五六七八九十\d个]+)?月/.test(sentence)?previousPeriods:[];
    if(periods.some(month=>uncovered.has(month)))
     return ['缺测月份仅证明本次查询未观察到记录，数据覆盖仍待确认。正文、表格与图保持这些月份为空，合计只说明已观测期间，不用计数探查的 0 代替缺测。'];
   }
  }
  previousPeriods=explicit;
 }
 return [];
}

const zeroValue=/^(?:\*\*)?(?:0(?:\.0+)?|零)(?:\*\*)?\s*(?:件|条|个|张|本|家|次|项|宗|户|批|台|%|％)?$/;
const periodKey=/^\d{4}-(?:0[1-9]|1[0-2])$/;
function missingCountColumns(result){
 const contract=result?.answer_contract?.period_count_coverage,lineage=result?.result_lineage,pub=result?.business_project;
 const values=result?.values;
 if(result?.status!=='succeeded'||result.truncated||result.purpose==='explore'||result.receipt?.read_only!==true
    ||contract?.schema_version!==1||!result.result_ref||contract.result_ref!==result.result_ref||lineage?.purpose!=='answer'
    ||!contract.request_ref||contract.request_ref!==lineage.request_ref||!contract.source_id
    ||contract.source_id!==lineage.source_id||contract.source_id!==result.receipt.source_id
    ||!contract.publication?.id||contract.publication.id!==pub?.id||contract.publication.version!==pub.version
    ||values?.complete!==true||values.rows?.length!==1||!Array.isArray(values.columns)||!Array.isArray(contract.columns))return [];
 return contract.columns.filter(column=>column?.state==='unobserved'&&column.observed_rows===0
    &&periodKey.test(column.period)&&column.start_inclusive===column.period+'-01'
    &&values.columns.indexOf(column.column)>=0&&values.rows[0]?.[values.columns.indexOf(column.column)]===null);
}

export function checkedEmptyGroupedMonths(result){
 const proof=result?.validation?.empty_grouped_months,lineage=result?.result_lineage;
 const publication=result?.business_project,values=result?.values;
 return result?.status==='succeeded'&&result.reason_code==='NO_MATCH'&&!result.truncated
   &&!['explore','probe'].includes(result.purpose)&&result.receipt?.read_only===true&&lineage?.purpose==='answer'
   &&proof?.schema_version===1&&proof.state==='unobserved'&&proof.request_periods_match===true
   &&proof.result_ref&&proof.result_ref===result.result_ref
   &&proof.request_ref&&proof.request_ref===lineage.request_ref
   &&proof.source_id&&proof.source_id===lineage.source_id&&proof.source_id===result.receipt.source_id
   &&proof.publication?.id&&proof.publication.id===publication?.id
   &&proof.publication.version===publication.version
   &&values?.complete===true&&values.row_count===0&&Array.isArray(values.rows)&&values.rows.length===0
   &&Array.isArray(proof.query_periods)&&Array.isArray(proof.requested_periods)
   &&proof.query_periods.length>0&&proof.query_periods.length===proof.requested_periods.length
   &&proof.query_periods.every((period,index)=>periodKey.test(period)&&period===proof.requested_periods[index])
   ?proof:null;
}

const independentBoundary=/[。！？!?；;\n]+\s*(?:另外|此外|顺便|另请)\s*[，,：:]?\s*/;
const namedMonthComparison=/(?:20\d{2}\s*年\s*)?\d{1,2}\s*月\s*([\p{Script=Han}]{2,24}?(?:数量|件数|总数|量|数))\s*(?:相较于|对比|比较|相比|比|较|与|和)\s*(?:同年\s*|20\d{2}\s*年\s*)?\d{1,2}\s*月/u;
const extraMonthlyDeliverable=/预测|原因|驱动因素|预算|金额|合格率|通过率|风险|建议|列出|明细|名单|绘制|柱状图|折线图|饼图|可视化|导出|请再/;

export function groupedEmptyScope(result,question,requestRef){
 const proof=checkedEmptyGroupedMonths(result);
 if(!proof||!requestRef||proof.request_ref!==requestRef||result.validation.request_scope?.request?.quote!==question)return null;
 const [comparison,extra,...rest]=question.split(independentBoundary);
 const subject=namedMonthComparison.exec(comparison)?.[1];
 if(!subject)return null;
 const object=subject.replace(/(?:办理量|处理量|批准量|业务量|数量|件数|总数)$/,'');
 // Only a separately phrased, named metric can be looked up independently.
 const match=extra&&!rest.length&&/^(?:请(?:问|查)?\s*)?((?:19|20)\d{2}\s*年)\s*([\p{Script=Han}]{2,22}率)(?:是多少|为多少|多少)[。！？!?\s]*$/u.exec(extra);
 const independent=match&&!extraMonthlyDeliverable.test(comparison)&&proof.query_periods.length===2
   &&!/(?:本次|这个|该|此|上述|前述|其中)/.test(match[2])&&!(object.length>=3&&match[2].includes(object))
   ?{question:extra.trim(),label:match[2],period:match[1]}:null;
 return {proof,subject,object,comparison,independent};
}

export function groupedEmptyIssues(text,scope){
 if(!scope)return [];
 const {subject,object,proof,independent}=scope;
 const names=[subject,...(object.length>=3?[object]:[])];
 const months=new Set(proof.query_periods);
 const years=new Set(proof.query_periods.map(period=>period.slice(0,4)));
 const year=years.size===1?[...years][0]:undefined;
 const globalAbsence=/(?:所有|任何|各个|各)\s*(?:授权)?(?:数据源|数据库|来源|库)[^，。；\n]{0,32}(?:无|未|没有)|(?:全库|全系统|全部来源|整个数据库)[^，。；\n]{0,32}(?:无|未|没有)/g;
 const zero=/(?<![\d.])(?:0(?:\.0+)?|零)\s*(?:件|条|个|张|本|家|次|项|宗|户|批|台|%|％)|(?:均|都|为|是|计为|等于)\s*(?:0(?:\.0+)?|零)(?![\d.月])/g;
 const related=part=>names.some(name=>part.includes(name));
 const coversMonth=part=>{
  const periods=periodsIn(part,year).map(item=>item.period).filter(period=>period.length===7);
  return periods.length&&periods.every(period=>months.has(period));
 };
 let tableSubject=false;
 for(const sentence of plain(text).split(/[。；;\n]/)){
  if(/^\s*\|/.test(sentence)){
   if(related(sentence))tableSubject=true;
   if(tableSubject&&coversMonth(sentence)&&sentence.split('|').some(cell=>zeroValue.test(cell.trim())))
    return ['已核对的月份没有返回符合本次筛选的记录，表格缺项需留空，不能填为业务量或差额 0。'];
   continue;
  }
  tableSubject=false;
  // A separate metric can share the same date. Keep matching local to the
  // assertion that actually names this query's measure or object.
  let prior='';
  for(const clause of sentence.split(/[，,]|(?:另外|此外|然而|不过|但是)/)){
   const context=prior+'，'+clause;
   prior=clause;
   if(!related(context)||!coversMonth(context)||independent&&context.includes(independent.label))continue;
   if([...clause.matchAll(globalAbsence),...clause.matchAll(zero)].some(match=>!negated(clause,match)
      &&!/(?:不能|不可|不应)(?:据此)?(?:推广到|概括为)[^，。]{0,12}$/.test(clause.slice(0,match.index))))
    return ['只能说明本次来源和筛选范围未返回所问月份的记录。数据覆盖仍未核实，不能推广到所有来源或用 0 表示业务量、差额或占比。'];
  }
 }
 return [];
}

export function periodCountCoverageIssues(text,evidence){
 const missing=evidence.flatMap(missingCountColumns);
 if(!missing.length)return [];
 const missingMonths=new Set(missing.map(item=>item.period));
 const years=new Set([...missingMonths].map(period=>period.slice(0,4)));
 const year=years.size===1?[...years][0]:undefined;
 const related=(sentence,index)=>scopedPeriods(sentence,index,year).some(period=>missingMonths.has(period))
   ||missing.some(item=>sentence.slice(0,index).includes(item.column));
 let tableColumns=[];
 for(const sentence of plain(text).split(/[。；;\n]/)){
  if(/^\s*\|/.test(sentence)){
   const cells=sentence.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());
   if(!tableColumns.length)tableColumns=cells;
   if(cells.some((cell,i)=>zeroValue.test(cell)&&(
     cells.slice(0,i).some(label=>periodsIn(label,year).some(p=>missingMonths.has(p.period)))||
     missing.some(item=>tableColumns[i]===item.column))))return [periodCountIssue];
   continue;
  }
  tableColumns=[];
  for(const match of sentence.matchAll(/(?<!\d)(?:0(?:\.0+)?|零)\s*(?:件|条|个|张|本|家|次|项|宗|户|批|台|%|％)|(?:均|都|为|是|计为|等于)\s*(?:0(?:\.0+)?|零)(?![\d.])/g)){
   if(!negated(sentence,match)&&related(sentence,match.index))return [periodCountIssue];
  }
 }
 return [];
}

const periodCountIssue='该期间的同范围查询没有观测记录，计数值应留空。不要把缺期写成业务量 0 或据此计算比较值；有证据的期间仍可分别报告。';

const currentCalendarMonth=/(?:本月|当月|这个月|当前(?:的)?月份|当前月(?!份|度))(?![末底])/;
const simpleCurrentCount=question=>currentCalendarMonth.test(question)
 &&/(?:累计|累积|合计|总数|数量|件数|条数|多少)/.test(question)
 &&!/(?:金额|余额|同比|环比|占比|预测|另外|同时|分别|和上月|与上月)/.test(question);

export function currentPeriodObservationIssues(text,evidence,question){
 const results=evidence.filter(result=>result?.status==='succeeded'&&result.result_ref);
 const formal=results.filter(result=>!isExploratory(result));
 const [result]=formal;
 const proof=result?.validation?.current_period_observation;
 const lineage=result?.result_lineage,publication=result?.business_project;
 const scoped=formal.length===1&&result.receipt?.read_only===true&&result.values?.complete===true
  &&proof?.schema_version===1&&proof.result_ref===result.result_ref&&lineage?.purpose==='answer'
  &&proof.request_ref&&proof.request_ref===lineage.request_ref&&proof.source_id
  &&proof.source_id===lineage.source_id&&proof.source_id===result.receipt.source_id
  &&proof.publication?.id&&proof.publication.id===publication?.id&&proof.publication.version===publication.version
  &&periodKey.test(proof.calendar_month)&&result.validation.request_scope?.request?.quote===question;
 const probeOnly=results.length>0&&!formal.length&&simpleCurrentCount(question);
 if(!scoped&&!probeOnly)return [];

 const missing=scoped&&proof.state==='unobserved';
 const months=missing&&proof.kind==='current_month'?[proof.calendar_month]:[];
 const earlierAbsence=scoped&&proof.prior_month_absence?.state==='unobserved'
  &&proof.prior_month_absence.period===proof.calendar_month&&proof.prior_month_absence.result_ref;
 for(const sentence of plain(text).split(/[。；;\n]/)){
  if(missing||probeOnly){
   const claims=[...sentence.matchAll(/(?<![\d.])(?:0(?:\.0+)?|零)\s*(?:件|条|个(?!月)|张|本|家|次|项|宗|户|批|台)|(?:累计|合计|数量|总数|计数|件数)[^，,]{0,10}(?:为|是|等于|[:：])\s*(?:0(?:\.0+)?|零)(?![\d.])/g)];
   const cells=/^\s*\|/.test(sentence)&&sentence.split('|').some(cell=>zeroValue.test(cell.trim()));
   const stated=months.length?periodsIn(sentence,months[0].slice(0,4)):[];
   const scopedMonth=!stated.length||currentCalendarMonth.test(sentence)||stated.some(({period})=>months.includes(period));
   if(scopedMonth&&(cells||claims.some(match=>!negated(sentence,match))))
    return [probeOnly?'本轮只有探查结果。按原问时间和业务筛选执行正式查询，再区分无观测与实际为零。':periodCountIssue];
  }
  if(months.length||earlierAbsence){
   const period=proof.calendar_month;
   const counts=sentence.matchAll(/(?:累计|累积|合计|总数|总量|计数|件数|条数|办理量)[^，,：:\d]{0,15}(?:为|是|达|[：:])?\s*(?<![\d.])\d[\d,]*(?:\.\d+)?\s*(?:件|条|个(?!月)|张|本|家|次|项|宗|户|批|台)/g);
   for(const claim of counts){
    const before=sentence.slice(0,claim.index).split(/[，,]/).at(-1);
    if(!negated(sentence,claim)&&(currentCalendarMonth.test(before)||periodsIn(before,period.slice(0,4)).at(-1)?.period===period)){
     return ['该月未取得可用观测。只报告已经核实的期间及其数值，并说明当前月完整累计仍待核实。'];
    }
   }
  }
  if(scoped&&proof.state==='coverage_unknown'&&['annual','year_to_date'].includes(proof.kind)){
   const claim=/(?:数据|记录|观测|报送)(?:覆盖|更新)?\s*(?:已|目前|实际|最新)?\s*(?:截至|截止(?:到)?|覆盖(?:至|到)|更新(?:至|到))\s*([^，,]+)/.exec(sentence);
   if(!claim||negated(sentence,claim))continue;
   if(periodsIn(claim[1],proof.calendar_month.slice(0,4)).some(({period})=>period===proof.calendar_month
      &&(!proof.prior_observed||period>proof.prior_observed.to)))
    return ['查询时间上限不能证明最新观测月份。请分别说明本次筛选范围和已核实的覆盖区间。'];
  }
 }
 if(earlierAbsence&&proof.kind==='year_to_date'&&/累计|截至|最新/.test(question)){
  const observedBoundary=plain(text).split(/[。；;\n]/).some(sentence=>
   (currentCalendarMonth.test(sentence)||periodsIn(sentence,proof.calendar_month.slice(0,4)).some(({period})=>period===proof.calendar_month))
   &&/(?:尚未结月|未返回.{0,12}(?:数据|记录|观测)|无.{0,12}观测|(?:待|未|无法)核实|覆盖.{0,8}(?:未知|待确认))/.test(sentence));
  if(!observedBoundary)return ['当前月同口径查询未返回观测。请说明本次筛选日期与已核实数据的区别，并明确当前月完整累计仍待核实。'];
 }
 return [];
}

export function coverageIssues(text,events){
 const calls=new Map(events.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e.data]));
 const blocked=[];const empty=[];
 for(const e of events){
  if(e.type!=='tool/result'||e.surfaceOp&&e.surfaceOp!=='append')continue;
  const message=e.data.message;if(!message)continue;
  const body=(message.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
  const args=argumentsOf(calls.get(message.source?.callId||message.toolCallId));
  if(!message.isError){
   try{const r=JSON.parse(body);if(r.status==='succeeded'&&r.reason_code==='NO_MATCH')empty.push(...(r.metric_contract?.periods||r.execution_scope?.periods||[]));}catch{}
   continue;
  }
  if(!body.includes('OUT_OF_COVERAGE'))continue;
  let meta;try{meta=JSON.parse(body.match(/COVERAGE_EVIDENCE:\s*(\{[^\n]+\})/)?.[1]||'null');}catch{}
  const counts={};
  if(meta?.schema_version===1){
   for(const [period,count]of Object.entries(meta.period_row_counts||{}))
    if(Number.isSafeInteger(count)&&count>=0&&(meta.requested_periods||[]).includes(period))counts[period]=count;
  }else{
   // Compatibility for persisted pre-structured receipts: require the count
   // and its period in the same clause, never just “some zero in this error”.
   for(const m of body.matchAll(/期间\s*(20\d{2}(?:-\d{2})?)[^。；;\n]*?underlying_row_count=(\d+)/g))counts[m[1]]=Number(m[2]);
  }
  blocked.push({periods:Array.isArray(args.periods)?args.periods:meta?.requested_periods||Object.keys(counts),counts,
   source:meta?.source_name||args.source_id,sourceId:meta?.source_id||args.source_id,
   metricId:meta?.metric_id||args.metric_id,metric:meta?.metric_name||args.metric_id});
 }
 if(!blocked.length)return [];
 // Bind absence to the metric the human named, not a similarly named rate.
 // Use only catalog-confirmed names/aliases. Ambiguous names remain unresolved.
 const question=requestText(events).replace(/\s/g,'');
 const matches=extractCatalogSources(events).flatMap(s=>(s.semantic_metrics||[]).flatMap(m=>
  [m.name,...(m.aliases||[])].filter(n=>typeof n==='string'&&n.length>=4&&question.includes(n.replace(/\s/g,''))).map(n=>({source:s.id,id:m.id,name:n}))));
 const longest=matches.filter(m=>!matches.some(other=>other.name.length>m.name.length&&other.name.includes(m.name)));
 const targets=new Map(longest.map(m=>[m.source+':'+m.id,m]));
 const target=targets.size===1?[...targets.values()][0]:null;
 let absent=false,zero=false,wrongMetric=false,deniedAbsence=false,year;
 for(const sentence of plain(text).split(/[。\n；;]/)){
  const sourceNamed=blocked.filter(r=>r.source&&sentence.includes(r.source));
  const candidates=sourceNamed.length?sourceNamed:blocked;
  const metricNamed=candidates.filter(r=>r.metric&&sentence.includes(r.metric));
  const records=metricNamed.length?metricNamed:candidates;
  const relevant=index=>{
   const requested=scopedPeriods(sentence,index,year);
   if(requested.length&&requested.every(p=>empty.includes(p)&&!records.some(r=>r.periods.includes(p))))return [];
   return records.flatMap(r=>(requested.length?requested:r.periods.length?r.periods:['*']).flatMap(p=>{
    if(!r.periods.length||r.periods.includes(p))return [p];
    if(/^20\d{2}$/.test(p)){
     const months=[...new Set(r.periods.filter(month=>month.startsWith(p+'-')))];
     // A year-wide absence claim requires every month, not one empty month.
     return months.length===12?months:months.length?[p]:[];
    }
    return [];
   }).map(p=>({record:r,period:p})));
  };
  for(const m of sentence.matchAll(/(?:不是|并非|并不是|不属于)(?:因为|由于)?\s*(?:底表|物理|实际)?(?:数据|记录)(?:不存在|缺失|缺少|没有|为空)|(?:仅|只)(?:仅|是|需|要)?[^，,。；]{0,16}(?:发布|登记|扩展)[^，,。；]{0,16}(?:覆盖|口径)[^，,。；]{0,12}(?:即可|就能|就可)(?:正常)?(?:查询|得到|获取)/g)){
   if(negated(sentence,m))continue;
   const scope=relevant(m.index);
   if(scope.length&&scope.every(({record,period})=>record.counts[period]===0))deniedAbsence=true;
  }
  for(const m of sentence.matchAll(/(?:记录|数据)(?:数)?(?:为|是)\s*0(?![\d.])|没有(?:任何|匹配|对应|该期间)?记录|(?:查无|未找到|未查到)(?:任何|匹配)?(?:记录|数据)|无(?:对应期间)?记录/g)){
   if(negated(sentence,m))continue;
   const scope=relevant(m.index);
   if(scope.length&&scope.some(({record,period})=>record.counts[period]!==0))absent=true;
   if(target&&scope.some(({record,period})=>record.sourceId===target.source&&record.metricId!==target.id&&!blocked.some(r=>r.sourceId===target.source&&r.metricId===target.id&&r.counts[period]===0)))wrongMetric=true;
  }
  const names=[...new Set(['指标值','服务次数','合格率','投入金额','数值',...records.map(r=>r.metric).filter(Boolean)])];
  const escaped=names.map(s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
  for(const m of sentence.matchAll(new RegExp('(?:'+escaped+')(?:为|是|等于|[:：])\\s*(?:0(?:\\.0+)?(?![\\d.])|零)','g'))){
   if(!negated(sentence,m)&&relevant(m.index).length)zero=true;
  }
  year=periodsIn(sentence,year).at(-1)?.period.slice(0,4)||year;
 }
 return [
  ...(wrongMetric?['缺失证据对应另一指标，不能用环比、同比或其他指标的零行记录代替用户所问指标。请查询目录中对应原问的指标；若未查询，只能说明目标指标尚未核实。']:[]),
  ...(absent?['底表记录声明与该来源、指标和期间的实查证据不符；只有对应期间的实查行数为0时才能说明底表无记录，其他期间须说明尚未核实或存在记录。']:[]),
  ...(deniedAbsence?['对应来源、指标和期间已实查为零行，不能否认底表无记录或声称仅发布覆盖期即可得到数据。请区分底表缺失与口径未覆盖；需先补录或接入实际数据，发布覆盖不会创造数据。']:[]),
  ...(zero?['超出登记覆盖范围的期间即使底表无物理记录，也不能直接将已发布指标值计为0；请保留未覆盖、不可确认的结论。']:[])
 ];
}
