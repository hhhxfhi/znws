import {claimReceipts} from './answer-bindings.js';
import {bindingIssues} from './answer-bindings.js';
import {activeEvidence} from './result-lineage.js';
import {annualForecastCatalogTool,checkedAnnualFiscalTrend,checkedAnnualForecastGapAnswer,checkedAnnualForecastTool,checkedForecastHistoriesAnswer,checkedMonthlyForecastTool,forecastClaimDelivery,forecastDeliveryIssues,forecastIssues,reportedSpendingObservation,reportedSpendingText,unavailableForecastAnswer,verifiedAnnualAmountReference,verifiedAnnualBudgetPartial,verifiedAnnualHistoryOnly,verifiedClosedBudgetTrend,verifiedCurrentExecution,verifiedCurrentReportBudget,verifiedForecastAnswer} from './forecast-evidence.js';
import {annualTrendAfterForecastRejection,verifiedProjectSpending} from './forecast-evidence.js';
import {isPercentagePointValue,percentagePointValues} from './percentage-points.js';
import {snapshotForecastGapAnswer} from './forecast-evidence.js';
import {progressInterpretationIssues} from './interpretation-evidence.js';
import {installAnswerAdmission} from './answer-admission.js';
import {assessedRateAnswerIssues,assessedRateObservation,assessedRateText,matchesAssessmentClause} from './assessment-rate.js';
import {answerFromReceipts,candidateMissingDelivery,completionInventoryAnswer,formatReceiptDelivery,groupedComparisonSection,groupedComparisonSources,groupedCountShareAnswer,groupedCountShareTool,metricDeliveryIdentity,uniqueMetricDeliveries,verifiedGroupedCountShare,verifiedGroupedMeasureAnswer,verifiedMonthlyGroupShare,verifiedObservationDelivery,withDefinitionNotice,workInventoryTool,workMeasureQuestionRejection} from './answer-delivery.js';
import {broadComparisonCatalog,comparisonBudgetNeeded,comparisonBudgetParagraph,comparisonBudgetPartial,comparisonBudgetPlan,comparisonBudgetWording,comparisonCatalogExhausted,
  comparisonCoverageTools,comparisonDiscoveryCovered,comparisonReportCoverageCall,describesPreferredBudget,preferredBudgetCall,
  preferredBudgetObservation,preferredBudgetPartial,preferredBudgetPlan,redundantComparisonCoverageRead,
  redundantComparisonSearch,unobservedComparisonReports} from './annual-budget-delivery.js';
import {formulaAnswerEvidence,formulaBasisRejection,formulaQuestionRejection,formulaRecoveryEvidence,independentChoiceSelection,independentComparisonCatalogTool,independentComparisonPlan,independentComparisonStep,unsettledQuestionRepeat} from './mixed-delivery.js';
import {publishedMonthlyMetricPlan,verifiedPublishedMonthlyMetricAnswer,verifiedSqlAnnualRefinement,verifiedSqlMonthlyAnswer} from './monthly-answer.js';
import {verifiedPublishedCountAnswer} from './published-grouping-answer.js';
import {currentExecutionTool,freshRankingCatalogTool,freshRepeatedSnapshotTool,normalizeDataToolArgs,previousGroupingView,publishedGroupingTool,publishedReportBudgetTool,rawDimensionRankingTool} from './tool-normalization.js';
import {catalogRefinementPeriod,confirmedApprovalGrouping,dimensionBusinessQuestionTool,initialCatalogCoverageTool,unavailableCatalogCoverageTool,verifiedUnavailableCatalogAnswer} from './intent-questions.js';
import {coverageIssues,currentPeriodObservationIssues,groupedEmptyIssues,groupedEmptyScope,monthlyObservationIssues,periodCountCoverageIssues} from './coverage-evidence.js';
import {eventDimensionCatalogTool,evidenceBoundaryIssues,holdingRateBoundaryIssues,holdingRateCatalogTool,registeredHoldingRateAnswer,registeredHoldingRateTool,regroupingIssues,snapshotRegionCoverageIssues} from './answer-evidence-boundaries.js';
import {categoricalExtremaIssues} from './categorical-extrema.js';
import {rankingObservationIssues,verifiedCancellationAnswer,verifiedRankingAnswer} from './ranking-observation.js';
import {groupSummaryIssues} from './group-summary-guard.js';
import {prosePeriodTableIssues,repairProsePeriodTable,verifiedYearComparisonTable} from './prose-period-table.js';
import {asksForMissingHistory,polishUnavailableDelivery,requestedUnavailableHistory,unavailableDeliveryIssues} from './unavailable-delivery.js';
import {
  isFundExecutionMetric,
  hasCompletedStatusScope,
  isProjectAcceptanceMetric,
  hasProjectAcceptanceEvidence,
  isWeightedWorkCompletionMetric,
  isArithmeticMeanMetric,
  hasAcceptanceStageScope,
  hasOverlappingDimension,
  isOnTimeRateMetric,
  isInspectionPassRateMetric,
  isContractConfirmed
} from './metric-semantics.js';
export * from './metric-semantics.js';
export {coverageIssues} from './coverage-evidence.js';
// Review public answer/evidence at the Harness stop boundary; never inspect reasoning.
import {periodEvidence} from './period-evidence.js';
import {requestContext,requestText,continuation,messageText} from './request-context.js';
import {asksAboutCatalogRegistration,calculationText,catalogRegistrationText,hasConcreteDataGap,proseLines,publicAnswerProse,publicMetricTitles,removeUnquotedMatches} from './calculation-presentation.js';
import {clarificationEvidence} from './host-evidence.js';
import {isKnowledgeOnly,isDataOnly,isAmbiguousMeasure} from './vague.js';
import {periodDeliveryIssues} from './delivery-checks.js';
import {chartDeliveryIssues,chartOnlyKind,explicitChartMismatch,explicitChartType,isExploratory,missingCurrentChartIssues,alignResultChartDescription} from './presentation-intent.js';
import {createUserMessage} from '@deepseek-ai/dsh-llm';
import {closingState,resumeQueryRepair,withNotice} from './closing.js';
import {assertSqlRepairBudget,sqlRepairState,sqlTimeoutRetry} from './repair-budget.js';
import {mkdirSync,writeFileSync,readFileSync,existsSync,realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
// Default to an isolated OS temp directory so unconfigured callers (unit tests that
// install the guard without configureAnswerQualityDirectory) never write generated
// review records into the plugin maintenance source. Production always overrides
// this via configureAnswerQualityDirectory(join(dataRoot,'answer-quality')).
let directory=tmpdir().replace(/\/$/,'')+'/natural-work-answer-quality/';
export function configureAnswerQualityDirectory(path){directory=path.replace(/\/$/,'')+'/';}

export function readAnswerQuality(sessionId,turn,ownDirectory=directory){
  if(!/^session-[0-9a-f-]{36}$/i.test(sessionId||'')||!Number.isSafeInteger(turn)||turn<1)throw new Error('无效会话或轮次');
  const path=ownDirectory.replace(/\/$/,'')+'/'+sessionId+'-'+turn+'.json';
  if(!existsSync(path))return {status:'unverified'};
  const record=JSON.parse(readFileSync(path,'utf8'));
  const unverified=Array.isArray(record.unverified)?record.unverified.filter(x=>typeof x==='string').slice(0,8):[];
  return {status:record.status==='partial'?'partial':'checked',repair_attempted:Boolean(record.repair_attempted),
    ...(typeof record.has_result==='boolean'?{has_result:record.has_result}:{}),
    ...(record.status==='partial'&&unverified.length?{unverified}:{})};
}

// 0.6.2: numbers a basic-query answer may cite. Values themselves with unit
// conversions (x100 /100 for percent, 万 and 亿), row counts and any whole number up
// to them ("其中 5 个地市"), and within a column or a row: differences, sums, ratios,
// growth rates, shares of the column total and averages.
const SCALES=[1,100,.01,1e-4,1e-8,1e4];
function addScaled(out,v){if(Number.isFinite(v))for(const k of SCALES)out.add(v*k);}
// 0.6.3: some drivers (e.g. TBase connections) return every cell as text ("208",
// "10072.04"); plain numeric text counts as a number.
const NUMERIC_TEXT=/^\s*-?\d+(?:\.\d+)?\s*$/;
const cellNumber=c=>typeof c==='number'?(Number.isFinite(c)?c:null):typeof c==='string'&&NUMERIC_TEXT.test(c)?Number(c):null;
const numericRow=row=>row.map(cellNumber);
export function rawValueNumbers(evidence){
  const out=new Set();
  for(const e of evidence)for(const row of e?.values?.rows||[])if(Array.isArray(row))for(const c of numericRow(row))if(c!==null)out.add(c);
  return out;
}
export function valueNumbers(evidence,ratioOut=null){
  const out=new Set();
  const add=v=>addScaled(out,v);
  // Ratios can justify percentages or multiples, never an unrelated count.
  const addRatio=ratioOut?(v=>{if(Number.isFinite(v)){ratioOut.add(v);ratioOut.add(v*100);ratioOut.add(v*.01);}}):add;
  const pairs=list=>{const xs=list.slice(0,50);for(let i=0;i<xs.length;i++)for(let j=0;j<xs.length;j++){if(i===j)continue;const a=xs[i],b=xs[j];add(Math.abs(a-b));if(i<j)add(a+b);if(b){addRatio(a/b);addRatio((a-b)/b);}}};
  let maxRows=0;
  for(const e of evidence){
    const v=e?.values;if(!v||!Array.isArray(v.rows))continue;
    for(const n of [e.row_count,v.row_count])if(Number.isFinite(n)){add(n);maxRows=Math.max(maxRows,n);}
    const rawRows=v.rows.filter(Array.isArray);
    // Numbers (including numeric text) for arithmetic; labels stay as they are.
    const rows=rawRows.map(r=>r.map(c=>{const n=cellNumber(c);return n===null?c:n;}));
    const width=Math.max(0,...rows.map(r=>r.length));
    for(const row of rows){const nums=row.filter(c=>typeof c==='number'&&Number.isFinite(c));nums.forEach(add);pairs(nums);}
    for(let c=0;c<width;c++){
      const col=rows.map(r=>r[c]).filter(x=>typeof x==='number'&&Number.isFinite(x));
      if(!col.length)continue;
      pairs(col);const total=col.reduce((a,b)=>a+b,0);add(total);add(total/col.length);
      if(total)for(const x of col.slice(0,50))addRatio(x/total);
      // 0.6.3: top-N sums and their cumulative share ("前三合计占比", 80% Pareto).
      const sorted=[...col].sort((a,b)=>b-a).slice(0,50);let run=0;
      for(const x of sorted){run+=x;add(run);if(total)addRatio(run/total);}
    }
    // Subtotals by each text column (e.g. 甲级 total across professions) and their shares.
    for(let g=0;g<width;g++){
      if(!rows.some(r=>typeof r[g]==='string'))continue;
      for(let c=0;c<width;c++){
        if(c===g)continue;
        const groups=new Map();
        for(const r of rows)if(typeof r[c]==='number'&&Number.isFinite(r[c]))groups.set(r[g],(groups.get(r[g])||0)+r[c]);
        if(groups.size<2||groups.size===rows.length)continue;
        const sums=[...groups.values()],total=sums.reduce((a,b)=>a+b,0);
        sums.forEach(add);pairs(sums);if(total)for(const x of sums)addRatio(x/total);
      }
    }
  }
  for(let i=0;i<=Math.min(maxRows,500);i++)out.add(i);
  return out;
}
const DATE_PARTS=/(?<![\d.])(?:19|20)\d{2}(?:\s*年|[-/]\d{1,2}(?:[-/]\d{1,2})?(?![\d.]))|(?<![\d.])\d{1,2}\s*(?:月份?|日|号|季度)/g;
// Numbers from typed evidence (facts, statements, claims, summaries) and from text.
export function evidenceNumbers(evidence,texts=[]){
  const out=new Set();
  const visit=(value,depth=0)=>{
    if(depth>6||value==null)return;
    if(typeof value==='number'){addScaled(out,value);return;}
    if(typeof value==='string'){for(const m of value.matchAll(/-?\d[\d,]*(?:\.\d+)?/g))addScaled(out,Number(m[0].replaceAll(',','')));return;}
    if(Array.isArray(value)){for(const x of value.slice(0,500))visit(x,depth+1);return;}
    if(typeof value==='object')for(const [k,x] of Object.entries(value))if(!['values','sql','view_url','result_ref','task_id','plan_ref','artifact_ref'].includes(k))visit(x,depth+1);
  };
  for(const e of evidence){
    for(const key of ['facts','claims','answer_contract','detail_summary','metrics','operators','row_count','inspection_reconciliation'])visit(e?.[key]);
    for(const r of e?.inspection_reconciliation?.rows||[]){
      if(r.status!=='incomplete'&&Number.isFinite(r?.numerator)&&Number.isFinite(r?.denominator)&&r.denominator>0){
        addScaled(out,Math.round((r.numerator/r.denominator)*10000)/100);
      }
    }
  }
  // 0.7.1: dates in the question or earlier answers (2026年10月, 2025-12) are not values;
  // otherwise “10月” would vouch for an invented “10 个”.
  for(const t of texts)visit(String(t||'').replace(DATE_PARTS,' '));
  return out;
}
const VALUE_UNIT=/(?<![\d.])(-?\d[\d,]*(?:\.\d+)?)\s*(万元|亿元|元|万次|次|个百分点|百分点|%|％|个(?!月)|件|张|本|家|条|项|人|户|份|批)/g;
const ISO_DATE_CELL=/\|\s*((?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))(?=\s*\|)/g;
// 0.6.4: numbers that state data — values with a unit, bare numbers in table cells,
// and numbers with two or more decimals. Years and YYYYMM periods are not data.
export function dataNumbers(text){
  const plain=String(text).replaceAll('**','');
  const found=new Map();
  for(const m of plain.matchAll(VALUE_UNIT))found.set(m.index,{raw:m[1],text:m[0],index:m.index,length:m[0].length});
  let offset=0;
  for(const line of plain.split('\n')){
    const table=/^\s*\|/.test(line)&&!/^\s*\|[\s:|-]*$/.test(line);
    // Table cells can carry a calendar date alongside measured counts.
    const scalars=table?line.replace(ISO_DATE_CELL,(cell,day)=>{
      const parsed=Date.parse(day);
      return Number.isFinite(parsed)&&new Date(parsed).toISOString().slice(0,10)===day
        ?cell.replace(day,' '.repeat(day.length)):cell;
    }):line;
    for(const m of scalars.matchAll(/(?<![\d.\/:])(-?\d[\d,]*(?:\.\d+)?)(?![\d.\/:]|\s*(?:年|月|日|号|季度|周|时|分|秒|级|等|期|批次))/g)){
      const raw=m[1].replaceAll(',',''),value=Number(raw),decimals=(raw.split('.')[1]||'').length;
      if(!Number.isFinite(value)||/^-?(?:19|20)\d{2}(?:0[1-9]|1[0-2])?$/.test(raw))continue;
      const index=offset+m.index;
      if([...found.values()].some(f=>index>=f.index&&index<f.index+f.length))continue;
      if((table&&Math.abs(value)>=10)||decimals>=2)found.set(index,{raw:m[1],text:m[1],index,length:m[1].length});
    }
    offset+=line.length+1;
  }
  return [...found.values()].sort((a,b)=>a.index-b.index);
}
export function unsupportedValues(text,evidence,userText='',history=[]){
  const explored=evidence.some(e=>e.purpose==='explore');
  evidence=evidence.filter(e=>e.purpose!=='explore');
  if(explored&&!evidence.some(e=>e.values))evidence=[...evidence,{values:{rows:[],complete:true}}];
  const valued=evidence.filter(e=>e?.values&&Array.isArray(e.values.rows));
  // 0.6.4: partial previews are checked too. Totals over the full result come from tool
  // statistics (claims/facts), which are approved below; rows the model never saw are not.
  if(!valued.length)return [];
  const verified=periodEvidence(evidence,userText);
  const prior=historyEvidence(history);
  const ratioNumbers=new Set();
  const approved=[...valueNumbers([...valued,...prior],ratioNumbers),...verified.numbers,...evidenceNumbers([...evidence,...prior])];
  const bad=[];
  const plain=String(text).replaceAll('**','');
  for(const m of dataNumbers(plain)){
    const raw=m.raw.replaceAll(',',''),value=Number(raw);
    if(!Number.isFinite(value)||[0,100].includes(Math.abs(value)))continue;
    if(quotedUserValue(plain,m.index,m.length,value,userText)||userThresholdValue(plain,m.index,m.length,value,userText))continue;
    // A requested top-N is a selection bound, not an observed population count.
    if(requestedCount(plain,m.index,m.length,value,userText))continue;
    // Thresholds in comparisons ("超过 1000 件", "60% 以下") are not results.
    const before=plain.slice(Math.max(0,m.index-6),m.index),after=plain.slice(m.index+m.length,m.index+m.length+3);
    if(/(?:超过|高于|低于|不足|少于|多于|达到|大于|小于|不低于|不高于|阈值|门槛|[<>≥≤])\s*$/.test(before)||/^\s*(?:以上|以下|及以上|及以下)/.test(after))continue;
    const decimals=(raw.split('.')[1]||'').length,tolerance=.5*10**-decimals+.011;
    const ratioValue=/[%％]|百分点/.test(m.text)||/^\s*倍/.test(plain.slice(m.index+m.length))
      ||isPercentagePointValue(plain,m);
    const candidates=ratioValue?[...approved,...ratioNumbers,...verified.percentages]:approved;
    if(!candidates.some(v=>Math.abs(Math.abs(v)-Math.abs(value))<=tolerance))bad.push(m.text);
  }
  return [...new Set(bad)];
}
// 0.6.4: the model sometimes drifts into Russian or other languages. Cyrillic, Greek,
// Arabic, Thai, Hangul or Kana runs are not part of a Chinese answer.
const FOREIGN=/[\u0370-\u03FF\u0400-\u052F\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\uAC00-\uD7AF]+/g;
export function foreignText(text){
  const plain=String(text).replace(/`[^`]*`/g,'');
  const runs=plain.match(FOREIGN)||[];
  if(runs.join('').length<4)return '';
  const at=plain.search(FOREIGN);
  return plain.slice(at,at+30).trim();
}
// 0.7.0: a stated count must match the items listed with it ("有4个月：5月、7月和9月").
const CN_DIGIT={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};
function countValue(s){
  if(/^\d+$/.test(s))return Number(s);
  if(s==='十')return 10;
  const m=s.match(/^([一二两三四五六七八九])?十([一二三四五六七八九])?$/);
  if(m)return (m[1]?CN_DIGIT[m[1]]:1)*10+(m[2]?CN_DIGIT[m[2]]:0);
  return s.length===1?CN_DIGIT[s]||null:null;
}
const COUNT=/(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|家|条)(月份?|地市|城市|市|区县?|县|项目|单位|年份|年度|季度|指标|类型|类别|专业|地区)/g;
function enumeratedMonths(labels){
  const values=labels.map(s=>s.replaceAll('**','').split(/[：:]/)[0].trim());
  const years=[...new Set(values.flatMap(s=>[...s.matchAll(/(\d{4})年/g)].map(m=>Number(m[1]))))];
  const months=new Set();let year=years.length===1?years[0]:0;
  for(const label of values){
    const span=label.match(/^(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*[-—–至到~～]\s*(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月/);
    const single=label.match(/^(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月/);
    if(!single)return null;
    year=Number(single[1]||year);
    const first=Number(single[2]),last=Number(span?.[4]||first),endYear=Number(span?.[3]||year);
    const start=year*12+first,end=endYear*12+last;
    if(first<1||first>12||last<1||last>12||end<start||end-start>120)return null;
    for(let month=start;month<=end;month++)months.add(month);
    year=endYear;
  }
  return months.size;
}
export function countMismatches(text){
  const plain=String(text).replaceAll('**','').replace(/(\d),(?=\d{3}\b)/g,'$1');
  const out=[];
  const matches=[...plain.matchAll(COUNT)];
  // Counts whose sentence ends at the same colon: the list after it is shared and ambiguous.
  const colonOf=m=>{const rest=plain.slice(m.index+m[0].length);const end=rest.search(/[。；;\n]/);const sentence=end<0?rest:rest.slice(0,end);return /[：:]\s*$/.test(sentence)?m.index+m[0].length+sentence.length:-1;};
  const colons=matches.map(colonOf);
  matches.forEach((m,idx)=>{
    let n=countValue(m[1]);
    if(!n||n<2||n>50)return;
    const noun=m[2],rest=plain.slice(m.index+m[0].length,m.index+m[0].length+2000);
    const sentence=rest.split(/[。；;\n]/)[0];
    // A population followed by a summary is not an enumeration promise.
    if(/^\s*(?:中|里|内)\s*[：:]\s*$/.test(sentence))return;
    // "十个专业计次总数为69家次，因此占比为：" introduces an aggregate
    // calculation, not a promised ten-item list. Do not count its comma-separated
    // calculation clauses as professional names.
    if(/^[^，,：:\n]{0,24}(?:合计|总计|总数|数量|占比|比例|均值|平均值|同比|环比)(?:为|是|约|达)?\s*[+-]?\d/.test(sentence))return;
    // "十个专业中前两位为：" promises a two-row selection, not ten rows.
    // Still check that explicit selection count rather than exempting the list.
    const subset=sentence.match(/^\s*(?:[，,]\s*)?(?:其中|中(?:的)?|里|内)\s*(?:排名)?前\s*(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:名|位|项|个|(?=的|为|是|[：:]))/);
    if(subset){const selected=countValue(subset[1]);if(!selected||selected>n)return;n=selected;}
    else if(/^\s*[,，]?\s*(?:其中|部分|代表(?:性)?|举例|例如|比如)[^。\n]{0,24}(?:包括|例如|举例|有|[：:])/.test(sentence)
      && !/(?:全(?:部|量)|完整|所有|逐一)/.test(sentence))return;
    let items=null;
    // Inline list right after the count: "有3个月：5月、7月和9月", "3个地市分别是甲市、乙市、丙市".
    // Bare "是/为" must directly follow the counted noun. In "39个项目的
    // 验收字段全部为空，没有数值" the comma clauses describe an attribute;
    // they are not two project names. Explicit list markers may have a prefix.
    const intro=sentence.replace(/（[^）]*）|\([^)]*\)/g,'').match(/^(?:[^，,]{0,12}?(?:分别是|分别为|包括|依次是|依次为|即|[：:])|\s*(?:是(?!否)|为))\s*(\S.*)$/);
    if(intro){
      // A following aggregate is not another list item. Conjunctions can also be
      // part of a real name (界线与不动产测绘); only split them at clear noun boundaries.
      const list=intro[1].split(/[，,](?=\s*(?:合计|总计|共计|累计|总数|占比|比例)(?:为|是|约|达|[:：\s\d]))/)[0];
      const conjunction=/^月/.test(noun)?/(?<=月)(?:以及|和|与|及)(?=[\d一二三四五六七八九十])/:
        /^(?:地市|城市|市|区县?|县|地区)$/.test(noun)?/(?<=[市县区])(?:以及|和|与|及)(?=.{1,20}[市县区]$)/:null;
      const parts=list.split(/[、，,]/).flatMap(x=>x.split(/\s+(?:以及|和|与|及)\s+/)).flatMap(x=>conjunction?x.split(conjunction):[x]).map(x=>x.trim()).filter(Boolean);
      const label=x=>x.length<=24&&!/\d+\.\d+|\d{3,}|[万亿%％]/.test(x)&&(!/^月/.test(noun)||/(?:\d{1,2}|[一二三四五六七八九十]{1,3})月/.test(x));
      if(parts.length>=2&&parts.every(label))items=parts.length;
    }
    // A list or table after a colon that only this count leads into.
    const blockLead=sentence.replace(/（[^）]*）|\([^)]*\)/g,'').trim();
    // A predicate on the counted members ("以下2个项目超支：") promises
    // them. A lead that opens a new clause or names a property of the whole
    // population ("39个项目的验收完成量情况如下：") introduces attribute or
    // empty-value notes instead; only an explicit list marker overrides that.
    const marked=/名单|清单|列表|分别(?:是|为)|依次(?:是|为)|包括/.test(blockLead)||
      /^(?:(?:具体|分别|依次)?(?:是|为)|(?:具体)?如下)?\s*[：:]$/.test(blockLead);
    const attribute=/^[，,]/.test(blockLead)||/^的/.test(blockLead)||
      /情况|结果|数据|数值|字段|完成量|统计|说明|分析|核查|核实|结论|原因|建议|问题|进度|分布|概况|汇总|口径/.test(blockLead);
    const enumeration=Boolean(subset)||marked||!attribute;
    if(items===null&&(enumeration||/^月/.test(noun))&&colons[idx]>=0&&colons.filter(c=>c===colons[idx]).length===1){
      let bullets=0,rows=0,started=false,listIndent=null,listKind=null;const labels=[];
      for(const line of rest.slice(sentence.length+1).split('\n')){
        const item=line.match(/^(\s*)([-*•]|\d+[.、)])\s+\S/);
        if(item){
          const indent=item[1].replaceAll('\t','    ').length,kind=/^\d/.test(item[2])?'ordered':'bullet';
          if(listIndent===null){listIndent=indent;listKind=kind;}
          // A nested ranking ends when the parent summary resumes. Only
          // siblings in the promised list count; child notes are not members.
          if(indent<listIndent||(indent===listIndent&&kind!==listKind))break;
          if(indent===listIndent){bullets++;labels.push(line.replace(/^\s*(?:[-*•]|\d+[.、)])\s+/,''));}
          started=true;
        }
        else if(/^\s*\|/.test(line)){if(!/^\s*\|[\s:|-]*\|?\s*$/.test(line))rows++;started=true;}
        else if(!line.trim()&&!started)continue;
        else break;
      }
      if(bullets>=2&&!rows)items=/^月/.test(noun)?(enumeratedMonths(labels)??(enumeration?bullets:null)):bullets;
      else if(rows>=3&&!bullets)items=rows-1;
    }
    if(items!==null&&items!==n)out.push(m[0]+'，但列出了 '+items+' 个');
  });
  return [...new Set(out)];
}
// 0.7.0: English inside a Chinese line ("结果显示 the overall trend is clearly upward").
// Code, field names, SQL and table/code blocks are left alone.
export function englishRuns(text){
  const out=[];
  let code=false;
  for(const raw of String(text).split('\n')){
    if(/^\s*```/.test(raw)){code=!code;continue;}
    if(code||/^\s*\|/.test(raw))continue;
    const line=raw.replace(/`[^`]*`/g,' ').replace(/\b[A-Za-z]+_[A-Za-z0-9_]+\b/g,' ');
    if(/\b(?:SELECT|FROM|WHERE|GROUP BY|ORDER BY)\b/.test(line))continue;
    const m=line.match(/(?:\b[A-Za-z][a-z']*\b[ ,.;:]+){4,}\b[A-Za-z][a-z']*\b/);
    if(m&&/[a-z]{2,}/.test(m[0]))out.push(m[0].trim().slice(0,40));
  }
  return out;
}
// 0.7.0: a month called the year's lowest/highest must really be the extreme in this
// turn's monthly results ("8月为全年最低" while 2月 is lower). Numbers alone can all be right.
function monthOf(v){
  const t=String(v??'').trim();let m;
  if((m=t.match(/^(\d{4})[-\/.]?(\d{1,2})$/))||(m=t.match(/^(\d{4})年(\d{1,2})月$/)))return {year:m[1],month:Number(m[2])};
  if((m=t.match(/^(\d{1,2})月$/)))return {year:'',month:Number(m[1])};
  return null;
}
export function monthlySeries(evidence){
  const out=[];
  for(const e of evidence){
    const v=e?.values;
    if(v&&Array.isArray(v.columns)&&Array.isArray(v.rows)&&v.rows.length>=3){
      const pc=v.columns.findIndex((_,i)=>v.rows.every(r=>monthOf(r[i])));
      if(pc>=0){
        const years=new Set(v.rows.map(r=>monthOf(r[pc]).year));
        if(years.size===1)v.columns.forEach((name,i)=>{
          if(i===pc)return;
          const pts=v.rows.map(r=>({month:monthOf(r[pc]).month,value:cellNumber(r[i])})).filter(p=>p.value!==null);
          if(pts.length>=3)out.push({name:String(name),points:pts});
        });
      }
    }
    const facts=(e?.facts||[]).filter(f=>typeof f.value==='number'&&monthOf(f.dimensions?.period)&&Object.keys(f.dimensions||{}).length===1);
    if(facts.length>=3&&new Set(facts.map(f=>monthOf(f.dimensions.period).year)).size===1)
      out.push({name:e.metric_contract?.id||'value',points:facts.map(f=>({month:monthOf(f.dimensions.period).month,value:f.value}))});
  }
  return out;
}
// 0.8.0 (CF078 DATE_RANGE): a stated whole-series month count, or a "近N年" label,
// must match the actual period span of an unambiguous period-only series (cross-year
// included). countMismatches exempts "N个月合计X" (aggregate, not a promised list) and
// the quantitative check skips 个(?!月), so "36个月合计X" (actual 35) and "近三年"
// (actual 47 months) were unenforced. This is a narrow additive consistency check; it
// relaxes no guard, invents no months, and never rewrites the user's range to a fixed
// rolling window — it only flags a count/label that contradicts the authorized facts.
function periodOnlySeries(evidence){
  const out=[];
  for(const e of evidence){
    const v=e?.values,c=e?.answer_contract;
    if(e?.status==='succeeded'&&!e.truncated&&c?.kind==='sql_aggregate'&&c.complete===true
      &&c.result_ref===e.result_ref&&v?.complete===true&&v.columns?.length===2
      &&v.rows?.length===e.row_count&&v.rows.length===v.row_count&&v.rows.length>=2){
      const coverage=e.period_coverage?.filter(p=>/^\d{4}-\d{2}$/.test(p.from)&&/^\d{4}-\d{2}$/.test(p.to))||[];
      const bound=coverage.length===1?coverage[0]:null;
      const time=bound?v.columns.indexOf(bound.column):-1,measure=1-time;
      const declaredCount=time>=0&&/件数|(?:数量|计数|总数|数)[（(_\s]+(?:件|条|宗|次|项|个|张)|count/i.test(v.columns[measure]);
      if(declaredCount&&v.rows.every(row=>row.length===2&&/^\d{4}-(?:0[1-9]|1[0-2])$/.test(row[time])
        &&Number.isSafeInteger(row[measure])&&row[measure]>=0)){
        const periods=v.rows.map(row=>row[time]).sort(),index=periods.map(p=>Number(p.slice(0,4))*12+Number(p.slice(5)));
        if(periods[0]===bound.from&&periods.at(-1)===bound.to&&index.every((n,i)=>!i||n===index[i-1]+1)){
          const calculated=v.rows.reduce((sum,row)=>sum+row[measure],0);
          out.push({periods,count:periods.length,total:calculated,additive:Number.isSafeInteger(calculated)});
        }
      }
    }
    const f=e?.facts;
    if(!Array.isArray(f)||!f.length||e.truncated||f.length!==e.row_count)continue;
    const metric=e.metric_contract;if(!metric||metric.ratio)continue;
    if(!f.every(x=>Number.isFinite(x.value)&&x.dimensions&&Object.keys(x.dimensions).length===1&&/^\d{4}-(0[1-9]|1[0-2])$/.test(x.dimensions.period||'')))continue;
    const periods=[...new Set(f.map(x=>x.dimensions.period))].sort();
    if(periods.length!==f.length)continue;
    const idx=periods.map(p=>Number(p.slice(0,4))*12+Number(p.slice(5)));
    if(!idx.every((v,i)=>i===0||v===idx[i-1]+1))continue;
    out.push({periods,count:periods.length,total:f.reduce((n,x)=>n+x.value,0),additive:metric.additive_over_time===true});
  }
  return out;
}
function monthSpanIssues(text,evidence){
  const series=periodOnlySeries(evidence);
  if(!series.length)return [];
  const plain=String(text).replaceAll('**','').replace(/(\d),(?=\d{3}\b)/g,'$1');
  const issues=[];
  // "N个月合计/总计/累计/共/总数 X" tied to a full-series total: the count must be the
  // actual number of periods. Tying to the complete total avoids subset false positives
  // (a half-year subtotal does not equal the full-series total).
  for(const m of plain.matchAll(/(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*个月\s*[，,、]?\s*(?:合计|总计|累计|共计|共|总数)\s*(?:为|是|约|达)?\s*[：:]?\s*([+-]?\d[\d,]*(?:\.\d+)?)/g)){
    const stated=countValue(m[1]),total=Number(m[2].replaceAll(',',''));
    if(!stated||stated<2||!Number.isFinite(total))continue;
    const matching=series.filter(s=>s.additive&&Math.abs(s.total-total)<=0.011);
    if(matching.length&&!matching.some(s=>s.count===stated)){
      const s=matching[0];
      issues.push(`月份数与事实不一致：该结果实际为${s.count}个月（${s.periods[0]} 至 ${s.periods[s.periods.length-1]}），回答写${stated}个月；请按实际期间数改写，不改变数值。`);
    }
  }
  // A "近N年/最近N年" label must not understate a single unambiguous span. Year labels
  // only (a "近N个月" focus may legitimately name a subset); never invents a window.
  if(series.length===1){
    const s=series[0];
    for(const m of plain.matchAll(/(?:近|最近)\s*(\d{1,2}|[一二两三四五六七八九十]{1,3})\s*年/g)){
      const n=countValue(m[1]);if(!n)continue;
      if(s.count>n*12)issues.push(`期间范围与事实不一致：回答称“${m[0]}”，但实际数据为${s.periods[0]} 至 ${s.periods[s.periods.length-1]}共${s.count}个月，超出该范围；请按实际期间描述，不擅自缩短或改为固定滚动窗口。`);
    }
  }
  return issues;
}
const TREND_SPANS=[['前三季度',1,9],['上半年',1,6],['下半年',7,12],['(?:第)?一季度|[Qq]1',1,3],['(?:第)?二季度|[Qq]2',4,6],['(?:第)?三季度|[Qq]3',7,9],['(?:第)?四季度|[Qq]4',10,12],['全年|年内',1,12]];
const STRICT_TREND='(?:逐月|连续|持续|稳步|单调|一路|月月)(上行|抬升|上升|增长|攀升|回升|走高|下行|下降|回落|下滑|走低)';
const FALLING=/下行|下降|回落|下滑|走低/;
// Strict month-by-month claims over a named span or an explicit month range.
function strictTrendClaims(text){
  const out=[];
  for(const clause of String(text).split(/[。；;\n，,]/)){
    if(/不是|并非|没有|并未|未能|不能|不算|无法判断/.test(clause))continue;
    const range=clause.match(new RegExp('(\\d{1,2})\\s*月?\\s*[-—–至到]\\s*(\\d{1,2})\\s*月[^，,。；;\\n]{0,6}'+STRICT_TREND));
    if(range){out.push({label:range[1]+'—'+range[2]+'月',a:Number(range[1]),b:Number(range[2]),rising:!FALLING.test(range[3]),checked:true});continue;}
    for(const [name,a,b] of TREND_SPANS){
      const m=clause.match(new RegExp('(?:'+name+')[^，,。；;\\n]{0,12}?'+STRICT_TREND));
      if(m){out.push({label:m[0].match(new RegExp(name))[0],a,b,rising:!FALLING.test(m[1]),checked:b-a===11});break;}
    }
  }
  return out.filter(c=>c.a>=1&&c.a<c.b&&c.b<=12);
}
// The answer's own list of falling (or rising) months against its summary.
function listedMonths(text){
  const out={falling:new Set(),rising:new Set()};
  for(const m of String(text).matchAll(/(下降|回落|下滑|减少|上升|增长|回升)的月份(?:有|为|是|包括)?\s*[:：]?\s*((?:\d{1,2}\s*月\s*[、，,和及与]?\s*)+)/g))
    for(const n of m[2].matchAll(/(\d{1,2})\s*月/g))out[/下降|回落|下滑|减少/.test(m[1])?'falling':'rising'].add(Number(n[1]));
  return out;
}
function monthlyTrendIssues(text,evidence){
  text=String(text).replace(/爬升/g,'攀升');
  const series=monthlySeries(evidence.filter(e=>!e.truncated&&e.values?.complete!==false))
    .map(s=>[...s.points].sort((a,b)=>a.month-b.month))
    .filter(points=>points.length===12&&points.every((p,i)=>p.month===i+1));
  const issues=new Set();
  const listed=listedMonths(text);
  for(const claim of strictTrendClaims(text)){
    const against=[...listed[claim.rising?'falling':'rising']].filter(m=>m>claim.a&&m<=claim.b).sort((x,y)=>x-y);
    if(against.length)issues.add(`回答前后矛盾：称${claim.label}逐月${claim.rising?'上升':'下降'}，又列出${against.map(m=>m+'月').join('、')}${claim.rising?'下降':'上升'}。请按实际月份改写概括，可写“总体${claim.rising?'上升':'下降'}、期间有波动”，不改变数值。`);
    // Whole-year and explicit month ranges are checked against the data below.
    const reversed=series.length&&!claim.checked?[...new Set(series.flatMap(points=>points.slice(claim.a,claim.b).filter((p,i)=>claim.rising?p.value<=points[claim.a+i-1].value:p.value>=points[claim.a+i-1].value).map(p=>p.month)))].sort((x,y)=>x-y):[];
    if(reversed.length)issues.add(`${claim.label}并非逐月${claim.rising?'上升':'下降'}：${reversed.map(m=>m+'月').join('、')}较上月${claim.rising?'下降或持平':'上升或持平'}。请按实际月份描述波动，可写“总体${claim.rising?'上升':'下降'}、期间有回落”，不改变数值。`);
  }
  if(!series.length)return [...issues];
  // A full year's twelve values only supply eleven month-on-month pairs.
  // "The remaining months rose" must not silently count January as a rise
  // without a numeric observation for the preceding December.
  const datedPoints=[];
  for(const e of evidence){
    for(const f of e.facts||[])if(cellNumber(f.value)!==null&&monthOf(f.dimensions?.period))datedPoints.push(monthOf(f.dimensions.period));
    const v=e.values;
    if(v?.rows?.length&&Array.isArray(v.columns)){
      const pc=v.columns.findIndex((_,i)=>v.rows.every(r=>monthOf(r[i])));
      if(pc>=0)for(const row of v.rows)if(row.some((c,i)=>i!==pc&&cellNumber(c)!==null))datedPoints.push(monthOf(row[pc]));
    }
  }
  const hasJanuaryBase=datedPoints.some(p=>p.year&&p.month===1&&datedPoints.some(b=>Number(b.year)===Number(p.year)-1&&b.month===12));
  if(!hasJanuaryBase&&/(?:全年|12\s*个月)/.test(text)&&/(?:其余|其他|剩余)(?:的)?月份(?:均|都)?(?:较|比)上月(?:回升|上升|增长|下降|回落)/.test(text)
      &&!/(?:除(?:了)?\s*1\s*月|1\s*月[^。\n]{0,16}(?:无|缺少|没有)[^。\n]{0,10}基期|11\s*个(?:可比)?月份)/.test(text))
    issues.add('全年12个月只有11组年内环比；没有上年12月数值时，不能把1月算入“其余月份均较上月回升／下降”。请明确可比月份范围和实际升降月份，说明1月缺少基期。');
  // Compare the overview with its own description of the starting level only
  // for one observed series. Do not invent a threshold for "high" or "low",
  // or compare qualitative labels belonging to different measures.
  if(new Set(series.map(points=>JSON.stringify(points))).size===1){
    const claims=text.split(/[。；;\n]/).filter(line=>!/(?:并非|不是|不能|不应|无法判断)/.test(line));
    for(const [overview,start] of [['高','低'],['低','高']]){
      const summary=new RegExp('(?:整体|总体|全年)[^。\\n]{0,20}前'+overview);
      const opening=new RegExp('(?:偏|较)?'+start+'(?:位)?起点|(?:起点|起步水平)(?:偏|较|为)?'+start);
      if(claims.some(line=>summary.test(line))&&claims.some(line=>opening.test(line)))
        issues.add('趋势概括与起点描述前后矛盾：请明确比较的月份区间和依据，用实际阶段升降替代含糊的“前高／前低”，保留已核对的数值。');
    }
  }
  for(const line of text.split(/[。；;\n]/)){
    if(/不是|并非|不能|不应|无法判断|上半年|下半年|季度/.test(line)&&!/(?:全年|整体)[^。]{0,120}[MW]\s*[字型形]/i.test(line))continue;
    if(/不是|并非|不能|不应|无法判断/.test(line))continue;
    const shape=line.match(/\b([MW])\s*(?:字)?[型形](?:走势|变化|曲线)?/i)?.[1]?.toUpperCase();
    if(shape&&series.every(points=>{
      const values=points.map(p=>p.value),extreme=shape==='M'?Math.max(...values):Math.min(...values);
      return values[0]===extreme||values.at(-1)===extreme;
    }))issues.add('全年形态描述与序列不符：M形需要两端低于内部峰值，W形需要两端高于内部谷值；本轮端点就是全局极值。删除这个形态标签，按实际升降区间描述，不改变数值。');
  }
  const causeRecorded=evidence.some(e=>e.values?.columns?.some((c,i)=>
    /原因|影响因素|归因/.test(c)&&e.values.rows?.some(row=>/春节|假期|节假日/.test(String(row[i])))));
  for(const line of text.split(/[。；;\n]/)){
    if(!causeRecorded)for(const clause of line.split(/[，,]/)){
      if(/(?:春节|假期|节假日)(?:因素|影响|导致|所致)|(?:受|由于|因为|源于)(?:春节|假期|节假日)/.test(clause)
          && !/可能|或许|待验证|尚待|缺少|无法|不能|不足|未证实/.test(clause))
        issues.add('月度计数未提供节假日归因证据。保留已核对的月份和值，删去未经核实的原因。');
    }
    if(/无法判断|不能说|并非|并未|没有(?:连续|逐月)/.test(line))continue;
    const monotone=(a,b,rising)=>series.every(points=>points.slice(a,b).some((p,i)=>rising?p.value<=points[a+i-1].value:p.value>=points[a+i-1].value));
    const whole=line.match(/(?:全年|年内|整体|整个期间)[^，,。；;\n]{0,15}(?:逐月|连续|持续|稳步)(上行|抬升|上升|增长|攀升|回升|下行|下降|回落)/);
    if(whole&&monotone(1,12,!/下行|下降|回落/.test(whole[1])))
      issues.add('全年存在反向变化月份，不能描述为逐月抬升、稳步上行或持续下降；请区分总体净变化与每月变化。');
    for(const range of line.matchAll(/(\d{1,2})\s*月?\s*[-—–至到]\s*(\d{1,2})\s*月[^，,。；;\n]{0,6}(?:逐月|连续|持续|稳步)(上行|抬升|上升|增长|攀升|回升|下行|下降|回落)/g)){
      const a=Number(range[1]),b=Number(range[2]);
      if(a>=1&&a<b&&b<=12&&monotone(a,b,!/下行|下降|回落/.test(range[3])))
        issues.add('该月份区间包含回落或反向变化，不能称为稳步上行或逐月下降；按实际月份说明波动。');
    }
    // Two dated clauses can describe the same bounded interval without a dash:
    // “2月回落至38件后逐月抬升，6月升至55件”. Inspect the intervening months.
    for(const claim of line.matchAll(/(\d{1,2})\s*月[^月，,。；;\n]{0,24}后(?:逐月|连续|持续|稳步)(上行|抬升|上升|增长|攀升|回升|下行|下降|回落)\s*[，,]\s*(\d{1,2})\s*月/g)){
      const a=Number(claim[1]),b=Number(claim[3]);
      if(a>=1&&a<b&&b<=12&&monotone(a,b,!/下行|下降|回落/.test(claim[2])))
        issues.add('两个月份之间存在反向变化，不能把净上升写成“之后逐月抬升”或净下降写成持续下降；保留实际起止值并说明中间波动。');
    }
    for(const claim of line.matchAll(/(\d{1,2})\s*月(?:[（(][^）)]*[）)])?[、和与](\d{1,2})\s*月(?:[（(][^）)]*[）)])?(?:均|连续|先后|连)(?:刷新|创)(?:当年|年内|全年)?(?:新高|高点)/g)){
      const months=claim.slice(1).map(Number);
      if(months.every(m=>m>=1&&m<=12)&&series.every(points=>months.some(m=>m>1&&points[m-1].value<=Math.max(...points.slice(0,m-1).map(p=>p.value)))))
        issues.add('逐月记录显示，并非这些月份都创了年内新高。请按月值核对后修正。');
    }
    for(const claim of line.matchAll(/(\d{1,2})\s*月(?:为[^。；\n()（）]{0,16}(?:[（(][^）)]{0,18}[）)])?[，,]\s*随后|起|开始|以来)?(?:逐月|持续|连续)(回升|上升|增长|攀升|下降|回落|下滑)(?:至|到)?(?:(\d{1,2})\s*月)?/g)){
      const a=Number(claim[1]),b=Number(claim[3]||12),rising=!/下降|回落|下滑/.test(claim[2]);
      if(a<1||a>=b||b>12||(!claim[3]&&!/月(?:起|开始|以来)/.test(claim[0])))continue;
      if(series.every(points=>points.slice(a,b).some((p,i)=>rising?p.value<=points[a+i-1].value:p.value>=points[a+i-1].value)))
        issues.add('该区间未按所述方向持续变化，请区分净变化与逐月上升或下降，按回执说明中间的波动。');
    }
    for(const m of line.matchAll(/(\d{1,2})\s*月[^，,。；\n]{0,10}(?:年内|全年)?(?:次低|次高|第二低|第二高)/g)){
      const low=/低/.test(m[0]),i=Number(m[1])-1;
      if(i>=0&&i<12&&series.every(points=>[...new Set(points.map(p=>p.value))].sort((a,b)=>low?a-b:b-a)[1]!==points[i].value))
        issues.add('该月不是所称的全年第二高或第二低。请删除该排序，引用回执给出的全年最高与最低。');
    }
    const shared=line.replaceAll('**','').match(/(?:^|^\s*[-•]\s*)(?:全年)?(最高|最低)\s*[：:]\s*(\d{1,2})\s*月(?:[（(][^）)]*[）)])?\s*(?:与|和|、)\s*(\d{1,2})\s*月/);
    if(shared&&series.every(points=>[Number(shared[2]),Number(shared[3])].some(m=>
      points[m-1]?.value!==(shared[1]==='最高'?Math.max:Math.min)(...points.map(p=>p.value)))))issues.add('并列最高或最低须有相同极值；请只按回执标注对应月份。');
  }
  return [...issues];
}
const EXTREME=/(最低|最高|峰值|低谷|最少|最多)/g;
function extremeMonthRange(text,index){
  // Scope belongs to the local claim. A half-year maximum is not an annual
  // maximum; a later explicit "全年" starts a different comparison scope.
  const before=text.slice(0,index).split(/[。；;\n，,、]/).at(-1)||'';
  const after=text.slice(index).split(/[。；;\n，,、]/)[0]||'';
  const scope=[...(before+after).matchAll(/上半年|下半年|全年|年内|第([一二三四1-4])季度/g)].at(-1);
  if(!scope)return [1,12];
  if(scope[0]==='上半年')return [1,6];
  if(scope[0]==='下半年')return [7,12];
  if(scope[1]){const q=Number(scope[1])||'一二三四'.indexOf(scope[1])+1;return [q*3-2,q*3];}
  return [1,12];
}
export function extremeErrors(text,evidence){
  const series=monthlySeries(evidence);
  if(!series.length)return [];
  const plain=String(text).replaceAll('**','').replace(/(\d),(?=\d{3}\b)/g,'$1');
  const out=[];
  for(const segment of plain.split(/\n|[。；;]/)){
    const months=[...segment.matchAll(/(\d{1,2})\s*月/g)].filter(m=>Number(m[1])>=1&&Number(m[1])<=12);
    if(!months.length)continue;
    for(const claim of segment.matchAll(EXTREME)){
      const before=segment.slice(0,claim.index);
      if(/(?:不是|并非|不为|非)[^，,]{0,6}$/.test(before))continue;
      // The line's subject (bullet or first table cell) or the nearest month before the claim;
      // only a claim that fits neither is reported.
      const prior=months.filter(m=>m.index<claim.index).at(-1)||months.find(m=>m.index>claim.index);
      if(!prior)continue;
      const lead=segment.match(/^[\s|>*•\-\d.、]*?(\d{1,2})\s*月/);
      const after=segment.slice(claim.index).split(/[、，,]/)[0].match(/(\d{1,2})\s*月/);
      const candidates=[...new Set([Number(prior[1]),...(lead&&Number(lead[1])<=12?[Number(lead[1])]:[]),...(after&&Number(after[1])<=12?[Number(after[1])]:[])])];
      const month=candidates.at(-1),low=/最低|低谷|最少/.test(claim[0]);
      const [start,end]=extremeMonthRange(segment,claim.index);
      const scoped=series.map(s=>({...s,points:s.points.filter(p=>p.month>=start&&p.month<=end)})).filter(s=>s.points.length);
      if(!scoped.length)continue;
      const extreme=m=>scoped.some(s=>{const hit=s.points.find(p=>p.month===m);if(!hit)return false;const best=low?Math.min(...s.points.map(p=>p.value)):Math.max(...s.points.map(p=>p.value));return Math.abs(hit.value-best)<1e-9;});
      if(candidates.some(extreme)||!series.some(s=>s.points.some(p=>candidates.includes(p.month))))continue;
      const main=scoped.find(s=>!/比|率|%|％|百分/.test(s.name))||scoped[0];
      const best=main.points.reduce((a,b)=>(low?b.value<a.value:b.value>a.value)?b:a);
      out.push(month+'月不是'+(low?'最低':'最高')+'（'+(low?'最低':'最高')+'是'+best.month+'月 '+best.value+'）');
    }
  }
  return [...new Set(out)];
}
// The numbers named by value issues: "找不到依据：A、B。" and "数值（A、B）".
export function unverifiedValues(issues){
  const out=[];
  for(const i of issues){
    if(!/数值|找不到依据|没有来源/.test(i))continue;
    const m=i.match(/找不到依据：([^。]*)。/)||i.match(/数值（([^）]*)）/);
    if(m)out.push(...m[1].split('、').map(x=>x.trim()).filter(Boolean));
  }
  return [...new Set(out)];
}
export function userClaimCorrection(userText,unverified){
  const original=new Set(dataNumbers(userText).map(m=>m.text.replace(/\s/g,'')));
  const claims=unverified.filter(value=>original.has(value.replace(/\s/g,'')));
  return claims.length?'用户原始转述中的数值（'+claims.join('、')+'）可以作为待核实说法引用，引用时明确写成“用户所说的 X”，然后依据本轮查询结果判断；不能将用户转述直接当作已查得的事实。':'';
}
function historyEvidence(history){
  return history.slice(-40).flatMap(value=>{
    let r;try{r=typeof value==='string'?JSON.parse(value):value;}catch{return [];}
    return r?.status==='succeeded'&&r.result_ref&&r.purpose!=='explore'?[r]:[];
  });
}
export function historyOnlyIssues(text,userText,history){
  const receipts=historyEvidence(history);
  const evidence=receipts.map(r=>({...r,values:r.values||{rows:(r.facts||[]).map(f=>[f.value]),complete:!r.truncated}}));
  if(!evidence.length)evidence.push({values:{rows:[],complete:true}});
  const missing=unsupportedValues(text,evidence,userText);
  return missing.length?['本轮没有查询，回答中的数值（'+missing.slice(0,5).join('、')+'）在本会话之前的结果里找不到依据。请先调用查询工具取得这些数值再回答，或删除它们。']:[];
}
export function userThresholdValue(text,index,length,value,userText){
  const requested=[...String(userText).matchAll(/(\d+(?:\.\d+)?)\s*[%％]/g)].some(m=>{
    if(Number(m[1])!==value)return false;
    const before=userText.slice(Math.max(0,m.index-30),m.index),after=userText.slice(m.index+m[0].length,m.index+m[0].length+30);
    return /(?:阈值|门槛|目标)[^。；;\n]{0,15}$/.test(before)||/^[^。；;\n]{0,15}(?:阈值|门槛|目标)/.test(after)||/(?:低于|高于|超过|不足|达到|不低于|不高于)\s*$/.test(before);
  });
  if(!requested)return false;
  const before=text.slice(Math.max(0,index-35),index).replaceAll('**',''),after=text.slice(index+length,index+length+35).replaceAll('**','');
  // The exemption belongs to this threshold occurrence, never to a measured
  // value elsewhere in the same sentence. It also covers an explicit subtraction.
  return /(?:阈值|门槛|目标)\s*(?:为|是|设为|[:：])?\s*$/.test(before)||
    /^\s*(?:为|是)?\s*(?:本次(?:设定的|测试的)?|预警|的|设定的|测试的|给定的|用户指定的|本次设定的预警)*\s*(?:阈值|门槛|目标)/.test(after)||
    /^\s*[（(]?\s*(?:为|是)?\s*(?:用户|您|本次测试)[^\d。；;\n，,]{0,8}(?:设定|指定|给定)[^\d。；;\n，,]{0,12}(?:阈值|门槛|目标)/.test(after)||
    /(?:低于|高于|超过|不足|达到|不低于|不高于)\s*$/.test(before)||
    /(?:与|和|相比于|相较于)\s*$/.test(before)&&/^\s*(?:的)?(?:百分点)?(?:差|相比|比较)/.test(after)||
    /\d+(?:\.\d+)?\s*[%％]\s*[−-]\s*$/.test(before)&&/^\s*=/.test(after);
}
const CN_NUMERAL=['零','一','二','三','四','五','六','七','八','九'];
function numeralForms(value){
  const forms=[String(value)];
  if(value<10)forms.push(CN_NUMERAL[value]);
  if(value===2)forms.push('两');
  if(value>=10&&value<100)forms.push((value>=20?CN_NUMERAL[Math.floor(value/10)]:'')+'十'+(value%10?CN_NUMERAL[value%10]:''));
  return forms;
}
// A count the task itself sets: the requested top-N ("前三个专业", "前10名")
// or the stated size of the compared set ("按十个专业"). A business count
// with another value or noun still needs query support.
export function requestedCount(text,index,length,value,userText){
  if(!Number.isInteger(value)||value<1||value>99)return false;
  const before=String(text).slice(Math.max(0,index-8),index).replaceAll('**',''),after=String(text).slice(index+length,index+length+10).replaceAll('**','');
  const forms=numeralForms(value).join('|'),unit='(?:个|名|项|家|条|位|类|大)?';
  const noun=after.match(/^\s*(?:个|名|项|家|条|位|类)?\s*([\u4e00-\u9fa5]{2})/)?.[1];
  if(noun&&/(?:前|top\s*|头)\s*$/i.test(before)&&new RegExp('(?:前|top\\s*|头)\\s*(?:'+forms+')\\s*'+unit+'\\s*'+noun,'i').test(userText))return true;
  if((!noun||/^(?:如下|列出|名单|合计|总计)$/.test(noun))&&/(?:前|top\s*|头)\s*$/i.test(before)
      &&new RegExp('(?:前|top\\s*|头)\\s*(?:'+forms+')(?![\\d一二三四五六七八九十])\\s*'+unit+'(?=\\s*(?:$|[，,。；;？?]|如下|名单))','i').test(userText))return true;
  return Boolean(noun)&&new RegExp('(?<![\\d一二三四五六七八九十两])(?:'+forms+')\\s*'+unit+'\\s*'+noun).test(userText);
}
export function quotedUserValue(text,index,length,value,userText){
  if(![...String(userText).matchAll(/-?\d[\d,]*(?:\.\d+)?/g)].some(m=>Number(m[0].replaceAll(',',''))===value))return false;
  const before=text.slice(Math.max(0,index-60),index).replaceAll('**',''),after=text.slice(index+length,index+length+60).replaceAll('**','');
  const tail=after.replace(/^(?:%|％)?[\s*”」"']*/, '');
  // A quoted challenge is not a measured value. Keep the exemption local to
  // this occurrence; an earlier attribution must not bless a later assertion.
  const clauseBefore=before.split(/[。；;\n，,]/).at(-1)||'';
  // A quoted proposition can contain "合格率是X" without asserting that X is
  // measured. Require the quote's claim label and a rejection in this sentence;
  // a later affirmative verification must not gain this exemption.
  const claimTail=tail.split(/[。；;\n]/)[0];
  if(/(?:关于|对于)\s*[“「"'][^”」"'。；;\n]{0,55}$/.test(clauseBefore)&&
     /^(?:的)?(?:说法|声称|传言)/.test(claimTail)&&
     /不可信|不成立|不正确|无法确认|尚未核实/.test(claimTail)&&
     !/(?:已|经)?(?:核实|复核|确认|证实)[^，,。；;\n]{0,8}(?:正确|可信|属实)/.test(claimTail))return true;
  if(/(?:实际|实测|查得|查出|查询结果|复核结果|核算结果)[^。；;\n，,]{0,20}(?:为|是|等于|达到|得出)\s*[“「"']?\s*$/.test(clauseBefore)
    ||/(?:实际(?:复核|核算)?(?:的)?(?:合格率|结果|数值)|(?:实测|查得|查出|查询|复核|核算)(?:结果|合格率|数值))\s*[“「"']?\s*$/.test(clauseBefore))return false;
  if(/^(?:经(?:过)?(?:核实|复核)|实际|确实|是(?:实际|正确)|可信|正确)/.test(tail))return false;
  const denial='(?:不可信|不采信|不能采信|不成立|(?:无从|无法|不能)(?:成立|证实|确认|支持)|不正确|不合理|不可能(?:成立|超过)?|超出合理(?:范围|区间)|站不住|矛盾值|有误|错误|错的|没有(?:任何)?(?:数据)?(?:依据|来源|支持)|找不到(?:任何)?(?:依据|来源|支持)|缺乏(?:可靠)?依据)';
  // Classify this occurrence as a discussed allegation: a local claim noun,
  // topic heading, comparison, or proposed error source. This exemption is
  // separate from checking whether a stated cause is actually supported.
  const claimSubject='(?:(?:这个|这一|该|的)(?:数字|数值|说法|结果|结论))';
  if(new RegExp('^'+claimSubject+'(?:本身)?(?:就|也|都)?(?:是)?'+denial).test(tail))return true;
  if(/^\s*[#>*-]*\s*(?:对|针对|关于|对于)\s*[“「"']?\s*$/.test(clauseBefore)&&/^(?:的)?(?:判断|复核|核查|核验|说明|分析)\s*[：:]?\s*$/.test(tail.split('\n')[0]))return true;
  if(/(?:与|和|跟|比|较|对照)\s*[“「"']?\s*$/.test(before)&&/^(?:差(?:得)?(?:更|很|太)?远|差很多|不同|不符)/.test(tail))return true;
  if(/^(?:可能|或许|也许|一定|肯定)?(?:是|来自|源于|由于)(?:把)?[^。；;\n]{0,40}(?:写反|误用|错填|填错|混用|计算错误|口径错误)/.test(tail))return true;
  if(new RegExp('^(?:在(?:数学|逻辑)上)?(?:(?:就|也|都|本就|根本|显然|明显|完全|绝对|直接)(?:是)?)?'+denial).test(tail))return true;
  if(/^只能是(?:计算|核算|统计|口径)?(?:错误|有误)/.test(tail))return true;
  // A denial may follow a comparison within this sentence, e.g. an alleged
  // percentage exceeds a bound and is therefore impossible. Reject positive
  // measured assertions first, then recognize the local rebuttal as a whole.
  const attributed=/(?:用户|有人|对方|您|你|转述|声称|所说)/.test(clauseBefore);
  const asserted=/(?:合格率|指标值|实测值|核算值|查询值|结果)(?:为|是|等于|达到|：|:)\s*[“「"']?\s*$/.test(clauseBefore);
  if(asserted&&!attributed&&!/(?:不可能|不能|并非|不是)[^，,。；;\n]{0,12}$/.test(clauseBefore))return false;
  // Discussing the source of a rejected allegation is not reporting a measured
  // number. Unsupported certainty about its cause is checked independently.
  if(/^(?:(?:只|仅)可能|只能|仅能)(?:是|来自|源于)[^。；;\n]{0,50}(?:错误|取错|算反|写反|误用|混用)/.test(tail))return true;
  const localRebuttal=tail.split(/[。；;\n]/)[0];
  if(localRebuttal.length<=160&&/(?:不可信|不采信|不能采信|不成立|不正确|不合理|不可能成立|不符合|不相符|不一致|不能(?:按|作为|视为)[^。；;\n]{0,18}(?:成立|确认|认定|采用)|(?:与|和|跟)[^。；;\n]{0,24}矛盾|误传|误算)/.test(localRebuttal))return true;
  if(/^(?:不在|未出现在|并未出现在|没有出现在)(?:本轮|本次|当前)?(?:的)?(?:工具|查询|数据|结果)/.test(tail))return true;
  // These are negative evidence statements and topic/source references, not
  // positive observations. Never carry a qualifier across a sentence boundary.
  if(/(?:没有|未能?|无法|不能)(?:读到|读取到|查得|查到|查询到|取得|确认|核实)(?:为|是|等于)?\s*[“「"']?\s*$/.test(clauseBefore))return true;
  if(/(?:没有|未能?|无法|不能)(?:取得|获得|确认)[^。；;\n，,]{0,12}(?:替代|支持|证实)\s*[“「"']?\s*$/.test(clauseBefore)
    &&/^[”」"']?(?:的)?(?:正式|总体|结果|数值|合格率)/.test(after.trim()))return true;
  if(/^\s*[#>*-]*\s*(?:关于|对于)\s*[“「"']?\s*$/.test(clauseBefore)&&/^[\s”」"']*$/.test(after.split('\n')[0]))return true;
  if(/(?:转述|原称|听到的)\s*[“「"']?\s*$/.test(clauseBefore)&&/^(?:的人|者|的来源|不可信|不成立)/.test(tail))return true;
  if(/(?:提供|报出|给出)\s*[“「"']?\s*$/.test(clauseBefore)&&/^(?:的)?(?:对方|人员|来源|人)[^。；;\n]{0,20}(?:核实|核对|确认|查证)/.test(tail))return true;
  if(/^(?:为什么|为何|是否)(?:不对|不成立|不可信|可信|正确|站不住)/.test(tail))return true;
  if(new RegExp('^(?:的)?(?:数值|说法|结果)?(?:在逻辑上)?'+denial).test(tail))return true;
  if(/(?:包括|例如)\s*[“「"']?\s*$/.test(clauseBefore)&&new RegExp('^[）)]?[^。；;\\n]{0,16}'+denial).test(tail))return true;
  if(/(?:如果|假如|假设)\s*[“「"']?\s*$/.test(clauseBefore)&&/^(?:来自|是指|代表|意味着)/.test(tail))return true;
  const sentenceBefore=text.slice(0,index).split(/[。！？!?\n；;]/).at(-1)||'';
  const sentenceAfter=text.slice(index+length).split(/[。！？!?\n；;]/)[0]||'';
  if(/^(?:明显|显著|远)?(?:高于|低于|大于|小于)/.test(tail)&&new RegExp('(?:矛盾|'+denial+')').test(sentenceAfter))return true;
  // A percentage-to-per-100 explanation is hypothetical only when explicitly
  // labelled as such and rejected in this same sentence; it is not query data.
  if((/^(?:意味着|相当于)每\s*100\s*(?:家|个|条|人|件)/.test(tail)||/[%％][\s*”」"']*(?:意味着|相当于)每\s*100\s*(?:家|个|条|人|件)/.test(sentenceBefore))
    &&new RegExp('(?:逻辑上|数学上)'+denial).test(sentenceAfter))return true;
  if(/(?:远|明显)?(?:低于|高于)\s*[“「"']?\s*$/.test(before))return true;
  if(/^(?:(?:这个|这一|该)(?:具体)?(?:数字|数值|合格率|说法|结果|结论)|(?:总体)?合格率[”」"']?(?:这个|这一|该)?(?:说法|数值|数字)?)?\s*[，,：:]?\s*(?:在(?:本轮|本次|当前)?(?:查询)?结果(?:中|里)?)?(?:直接)?(?:不可信|不成立|不能成立|不正确|不可能|有误|错误|没有(?:任何)?(?:数据)?(?:依据|来源|支持)|找不到(?:任何)?(?:依据|来源|支持))/.test(tail))return true;
  if(/(?:所(?:提到|说|声称)的|而非|并非|不是)\s*$/.test(before))return true;
  if(/(?:对方|有人|他人|用户|您|你)[^。；\n]{0,24}(?:提供|声称|提到|给出|所说|说的)[^。；\n]{0,8}$/.test(before))return true;
  if(/(?:关于|对于)\s*[“「"']?\s*$/.test(before)&&/^(?:(?:这|该)(?:个|一)?)?(?:说法|数字|数值|原始值)/.test(tail))return true;
  if(/(?:与|和|较|比|对照)\s*[“「"']?\s*$/.test(before)&&/^(?:(?:有|存在)(?:着)?)?(?:明显|显著|重大|较大|很大)?(?:不同|不一致|不符|不相符|相差|差距|差异|高|低|接近)/.test(tail))return true;
  if(/^在(?:本轮|本次|当前)?(?:查询)?结果(?:中|里)?(?:找不到|没有|缺乏)(?:依据|来源|支持)/.test(tail))return true;
  if(/(?:(?:不可能|不能|不应(?:该|当)?|不会)(?:就是|会是|是|为|等于|达到)|不等于|未达到|达不到)\s*[“「"']?\s*$/.test(clauseBefore))return true;
  if(/(?:核对|未确认|未能确认|追溯|查明)[^。；\n]{0,16}$/.test(before)&&/^(?:的)?(?:具体|原始)?来源/.test(tail))return true;
  if(/^(?:%|％)?[\s*”」]*(?:(?:这个|这一|该|的)?(?:数字|数值|合格率|说法|结果|结论)\s*)?(?:是|并)?\s*(?:不可信|不成立|不能成立|不正确|不可能|有误|错误|在逻辑上就不成立)/.test(after))return true;
  if(/^(?:%|％)?[\s*”」]*(?:明显)?超出[^。；;\n]{0,24}(?:有误|错误|不可信)/.test(after))return true;
  const attribution=/(?:您|你|用户)[^。；\n]{0,18}(?:提到|所说|声称|给出|提供|说的|原称)|(?:待核实|原(?:来)?(?:的)?说法|声称|传言)[^。；\n]{0,18}$/.test(before);
  if(attribution&&!/(?:实际|核实|证实|查得|查询结果)[^。；\n]{0,12}$/.test(before)
    ||/[“「]\s*$/.test(before)&&/^(?:%|％)?[”」][^。；\n]{0,12}(?:不|有误|错误|待核实)/.test(after))return true;
  const sentence=(text.slice(0,index).split(/[。！？!?\n；;]/).at(-1)||'')+text.slice(index,index+length)+(text.slice(index+length).split(/[。！？!?\n；;]/)[0]||'');
  if(/(?:对方|有人|他人|用户|您|你|所(?:提到|说|声称)|传言|原(?:来)?(?:的)?说法)/.test(sentence)&&/(?:不可信|不成立|不能成立|不正确|不可能|有误|错误|不符|不一致|缺乏依据|没有依据|找不到依据|明显超出)/.test(sentence))return true;
  return false;
}
export function citedRangeErrors(text,evidence){
  const numbers=[...String(text).matchAll(/-?\d+(?:\.\d+)?/g)].map(m=>Number(m[0]));
  const out=[];
  for(const e of evidence)for(const w of e?.warnings||[]){
    const m=String(w).match(/出现(?:负数)?\s*(-?\d+(?:\.\d+)?)/);
    if(m&&numbers.some(n=>Math.abs(n-Number(m[1]))<.01))out.push(w.split('，')[0]);
  }
  return out;
}

const COMPARE_HEADING=/^\s*(?:[#>*-]+\s*)*(?:与|同|较)上年(?:同期)?(?:的)?对比(?:参考|情况|结果)?(?:\s*[（(]如需[）)])?\s*(?:[：:]|$)/m;
function hasPriorYear(receipt,others){
  return receipt.facts.some(f=>{
    const period=f.dimensions?.period||'';
    if(!/^\d{4}(?:-\d{2})?$/.test(period)||cellNumber(f.value)===null)return false;
    const prior=String(Number(period.slice(0,4))-1)+period.slice(4);
    return others.some(r=>r.result_ref&&r.source_id===receipt.source_id&&r.metric_contract?.id===receipt.metric_contract.id
      &&r.metric_contract.version===receipt.metric_contract.version&&JSON.stringify(r.metric_contract.scope)===JSON.stringify(receipt.metric_contract.scope)
      &&r.facts?.some(g=>g.dimensions?.period===prior&&cellNumber(g.value)!==null
        &&Object.keys(g.dimensions).length===Object.keys(f.dimensions).length
        &&Object.keys(f.dimensions).every(k=>k==='period'||f.dimensions[k]===g.dimensions[k])));
  });
}
function asksAboutSource(text){
  return /(?:什么|哪个|哪一|查看|给出|提供|核对|显示|列出|查询|确认|请问|是否|是不是|是用)[^。！？\n]{0,16}(?:文档|工作簿|口径包|数据源|数据来源|合成|模拟)|(?:来源|版本|文档|工作簿)[^。！？\n]{0,10}(?:是什么|是哪|从哪|从何|哪一版|版本号)|(?:合成|模拟|测试)(?:的)?(?:数据|库)?(?:吗|还是真实)/.test(text);
}

const SYNTHETIC_SOURCE='(?:合成(?:测试)?数据(?:库|源)?|合成(?:测试|练习)?库|模拟数据(?:库|源)?|测试数据(?:库|源)?)';
const SOURCE_SUBJECT='(?:(?:本次|本轮|本|当前)(?:查询|统计|结果|数据)?的?(?:结果|数据)?|结果|数据)?';
const SOURCE_CAVEAT='(?:仅?(?:用|适用)于(?:演示|验收|测试|验证)(?:口径链路|计算链路)?|仅供(?:验收|演示|测试)参考|非真实业务(?:实绩|数据)|不代表(?:客户)?真实(?:业务)?数据|不作为真实业务数据)';
const SOURCE_NOTICE=new RegExp('^(?:'+SOURCE_SUBJECT+'(?:来源(?:为|是|于)?|来自|使用的是|为|是)|数据来自)'
  +'[“「"]?'+SYNTHETIC_SOURCE+'(?:[（(][^）)\\n]{1,80}[）)])?[”」"]?(?:[，,]'+SOURCE_CAVEAT+')*\\.?$');
const SOURCE_METADATA=/^(?:数据快照|数据来源|数据源|来源)\s*(?:[：:]|为|是)\s*(.+)$/;
const SYNTHETIC_SUFFIX=new RegExp('[（(]'+SYNTHETIC_SOURCE+'[）)]$');
const DOCUMENT_QUALIFIER=/(?<![\w.])[ \t]*v\d+(?:\.\d+)+[ \t]*(?:[一二三四]级)?(?:指标(?:文档|口径)?|业务文档|口径包|工作簿)(?:下的|中的|里的|内的)[ \t]*/gi;
const SNAPSHOT_LABEL=/[（(]\s*(?:[\p{L}\p{N} _·-]{0,48}\bv\d+(?:\.\d+)+[ \t]*)?(?:(?:可见|合成|测试|验收|模拟)[ \t]*){1,4}(?:数据)?库\s*[）)]/giu;

function polishCatalogMetadata(text,question){
  const lines=[];
  const confirmedDataGap=hasConcreteDataGap(publicAnswerProse(text));
  for(const {line,protected:literal} of proseLines(text)){
    if(literal){lines.push(line);continue;}
    let prose=removeUnquotedMatches(catalogRegistrationText(line,question,confirmedDataGap),DOCUMENT_QUALIFIER);
    prose=removeUnquotedMatches(prose,SNAPSHOT_LABEL);
    lines.push(prose);
  }
  const visible=lines.join('\n').trim();
  return visible||text;
}

function matchesPublishedDefinition(result){
  const contract=result.answer_contract,definition=contract?.definition_evidence;
  return contract?.business_confirmation==='configured'&&definition?.state==='configured'
    &&definition.basis==='published_configuration'
    &&['validated_sql','validated_current_execution_sql','validated_monthly_count','validated_grouped_count','validated_report_budget_sql'].includes(definition.match)
    &&Boolean(definition.source_id&&definition.project_id&&definition.project_version&&definition.definition_fingerprint)
    &&(!result.result_lineage?.source_id||result.result_lineage.source_id===definition.source_id)
    &&(!result.business_project?.id||result.business_project.id===definition.project_id)
    &&(!result.business_project?.version||result.business_project.version===definition.project_version);
}

function polishResultNotices(text,events){
  const turn=events.findLast(event=>event.type==='turn/start');
  const current=events.filter(event=>event.seq>=turn?.seq);
  const receipts=activeEvidence(typedReceipts(current,formalQueryTools))
    .filter(result=>result.result_ref&&result.status==='succeeded'&&!isExploratory(result));
  const pendingNotice=receipts.length>0;
  const question=requestText(events);
  const wantsRegistration=asksAboutCatalogRegistration(question);
  if(pendingNotice&&!wantsRegistration)text=calculationText(text);
  text=alignResultChartDescription(text,receipts);
  if(asksAboutSource(question))return text;
  text=publicMetricTitles(text,receipts,question);
  const successfulCatalogs=window=>typedReceipts(window,catalogTools).filter(result=>result.status==='succeeded'
    &&result.execution?.operation==='data.catalog'&&result.sources?.length);
  let catalogs=successfulCatalogs(current);
  if(!catalogs.length&&!pendingNotice&&turn){
    const previous=events.findLast(event=>event.type==='turn/start'&&event.seq<turn.seq);
    if(previous)catalogs=successfulCatalogs(events.filter(event=>event.seq>=previous.seq&&event.seq<turn.seq));
  }
  const sourceNotice=receipts.length&&receipts.every(result=>result.source_provenance?.synthetic===true);
  if((catalogs.length||pendingNotice)&&!wantsRegistration)
    text=polishCatalogMetadata(text,question);
  if(!sourceNotice)return text;
  const sourceNames=new Set(receipts.map(result=>String(result.source_provenance?.name||'').normalize('NFKC')
    .replace(/\s+/g,'').replace(SYNTHETIC_SUFFIX,'')).filter(Boolean));
  const remove=clause=>{
    const body=clause.replaceAll('**','').trim().replace(/^(?:[-*>#]+\s*)?/,'')
      .replace(/[。；;！!？?]+$/,'');
    const metadata=SOURCE_METADATA.exec(body);
    if(sourceNotice&&metadata){
      const name=metadata[1].split(/[，,]/,1)[0].normalize('NFKC').replace(/\s+/g,'')
        .replace(/^[“「"]|[”」"]$/g,'').replace(/(?:当前)?(?:授权)?快照$/,'').replace(SYNTHETIC_SUFFIX,'');
      if(sourceNames.has(name))return true;
    }
    const plain=body.replace(/^(?:(?:说明|数据来源|口径说明)[：:]\s*)?(?:(?:此外|另外|同时)[，,]\s*)?/,'')
      .replace(/\s+/g,'');
    return SOURCE_NOTICE.test(plain);
  };
  let fence=null;
  return text.split('\n').map(line=>{
    const fm=/^\s*(`{3,}|~{3,})/.exec(line);
    if(!fence&&fm){fence={c:fm[1][0],n:fm[1].length};return line;}
    if(fence){if(new RegExp(`^\\s*\\${fence.c}{${fence.n},}\\s*$`).test(line))fence=null;return line;}
    if(/^\s*>/.test(line)||/^\s*\|.*\|\s*$/.test(line))return line;
    const clauses=line.match(/[^。；;！!？?]+[。；;！!？?]*|[。；;！!？?]+/g)||[];
    const kept=clauses.filter(clause=>!remove(clause));
    if(kept.length===clauses.length)return line;
    return kept.join('').trimEnd().replace(/[；;]$/,'。');
  }).join('\n').trim();
}

export function presentationIssues(text,evidence=[],userText=''){
  const issues=[],contracts=evidence.map(e=>e.metric_contract).filter(Boolean);
  const synthetic=evidence.some(e=>e.source_provenance?.synthetic===true||e.source_context?.synthetic===true
    ||e.sources?.length&&e.sources.every(s=>s.source_context?.synthetic===true));
  if(synthetic&&/^(?:[-*]\s*)?(?:数据(?:来源)?|结果|数值|本次(?:查询|统计)?(?:的)?(?:结果|数据)?)\s*(?:均|皆|全部|确实|均确|都)?\s*(?:为|是|来自|源于|使用(?:的)?|基于)\s*(?:真实(?:的)?(?:业务|生产|客户|现场)|(?:客户|生产|现场)(?:的)?真实)/m.test(text.replaceAll('**','')))
    issues.push('来源信息已经保留，请删除把合成查询结果表述为真实业务实绩的内容。');
  if(!asksAboutSource(userText)&&/(?:^|\s)唯一来源\s*[：:]|[\p{Script=Han}\w.-]+\.xlsx\b|(?:内置|业务口径|口径包|工作簿|指标文档)[^。！？\n]{0,14}(?:v\d+(?:\.\d+)+|\d+\.\d+版)|(?:v\d+(?:\.\d+)+)[^。！？\n]{0,8}(?:工作簿|业务文档|口径包|内置口径)/iu.test(text))
    issues.push('用业务指标名称、统计范围和计算方式作答。内部口径文件名、文档版本和导入说明保留在配置管理。');
  // Evidence retains immutable IDs and versions; business prose does not need them.
  const technicalRequested=userText.split(/[。！!？?\n]/).some(s=>!/(?:不要|不需要|无需|别|不必)[^。]{0,35}(?:技术|版本|编号|字段|编码|SQL|[iI][dD])/i.test(s)&&
    /(?:查看|显示|提供|输出|列出|给出|展示|核对|追溯|调试|解释)[^。]{0,40}(?:技术详情|技术信息|内部编号|SQL|字段名?|指标编码|(?:口径|方法|技能)\s*(?:[iI][dD]|编号|版本)|statement_id|metric_id|result_ref)|(?:(?:口径|方法|技能)\s*(?:[iI][dD]|编号|版本)|字段名?|指标编码|statement_id|metric_id)[^。]{0,10}(?:是什么|是多少|含义)/i.test(s));
  if(!technicalRequested){
    const versions=evidence.flatMap(e=>[e.metric_contract?.version,...(e.claims||[]).map(c=>c.method_version)]).filter(Boolean);
    const knownVersion=versions.some(v=>text.includes(v));
    const internalId=contracts.some(c=>typeof c.id==='string'&&c.id.includes('_')&&text.includes(c.id));
    // Terms come from the actual contract, not a list tied to one exam table.
    // Generic identifier shapes also cover absence receipts without a contract.
    const identifiers=contracts.flatMap(c=>[...(String(c.definition||'').matchAll(/\b[A-Za-z][A-Za-z0-9_]{2,}\b/g))].map(m=>m[0]));
    const technicalTerm=identifiers.some(id=>new RegExp('\\b'+id+'\\b').test(text))
      ||/\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b[A-Z][A-Z0-9]+_\d+(?:_\d+)+\b|(?:统计区域|地区编码|区域编码)\s*[:：=]?\s*\d{6}\b/.test(text);
    const v2Reconciliation=evidence.some(e=>e.inspection_reconciliation?.reconciliation_version>=2);
    const physicalLeak=/\bsynthetic\s*=\s*(?:true|false)\b/i.test(text)||(v2Reconciliation&&/状态为\s*\**\s*conflict\b|\b(?:jcnd|bjdw|pcgzlpd|zztjfhx|chaqscztzrlsqk)\s*=|[（(]\s*(?:jcnd|bjdw|pcgzlpd|zztjfhx|chaqscztzrlsqk)\s*[）)]/i.test(text));
    if(/操作数|主键对齐|事实投影|编译器|数据工序|工序回执/.test(text))issues.push('删除操作数、主键对齐、事实投影及工序等实现细节。只解释业务计算方法和实际结果。');
    if(knownVersion||internalId||physicalLeak||technicalTerm||/(?:statement_id|fact_id|result_ref|metric_id|metric_version|plan_ref|first_issuance_evidence(?:\.code)?)\s*[:：=]|\bFIRST_EVIDENCE_MISSING\b|口径版本|(?:口径|指标|方法|技能)[^。\n]{0,20}版本\s*\d/.test(text))
      issues.push('面向业务用户，仅展示指标名称、期间、单位、结果与必要解释；删除内部编号、字段名、口径版本和方法版本。内部追溯信息已经保留，无需在正文复述。');
    if(/(?:已按|根据)[^。\n]{0,12}一致性检查|(?:修正|修改)文案|(?:下面|以下|现将)[^。\n]{0,8}修正为|未重放查询|不重放查询|补充口径版本|结论原样引用已核算结果|未新增推断/.test(text))
      issues.push('直接给出业务结果，删除一致性检查、修正文案和重放查询等内部处理过程说明。');
  }
  return issues;
}

export function nullThresholdIssues(text,evidence,userText){
  if(!/(?:低于|高于|超过|不足|小于|大于)\s*\d/.test(userText))return [];
  const nullMean=evidence.some(e=>e.values?.columns?.some((c,i)=>/平均|均值/.test(c)&&e.values.rows?.some(row=>row[i]===null)));
  const admitsMissing=/(?:字段|数据|数值)[^。；;\n]{0,20}(?:均|全|都)[^。；;\n]{0,10}(?:为空|空值|缺失)|所有[^。；;\n]{0,35}(?:为空|空值|缺失)/.test(text);
  if(!nullMean||!admitsMissing)return [];
  const unsupported=text.replaceAll('**','').split(/[。；;\n]/).some(s=>{
    const claim=/(?:没有|不存在|无(?!法|从|需))(?:任何)?[^。；;\n]{0,25}(?:满足|符合|低于|高于|超过|不足)|(?:满足|符合|低于|高于)[^。；;\n]{0,25}(?:为0|为零|是0|是零)/.exec(s);
    if(!claim)return false;
    const prefix=s.slice(0,claim.index).split(/[，,]/).at(-1)||'';
    return !/(?:不能|无法|不可|不应|不宜|不代表|不等于|并非)[^，,]{0,12}$/.test(prefix);
  });
  return unsupported?['字段为空时，阈值筛选的空结果不能证明没有低于或高于阈值的项目。保留平均值不可计算，明确“无法判断哪些项目满足阈值”，不要推断未验收或实际数量为零。']:[];
}

export function catalogSelectionIssues(text,events,userText){
  if(isKnowledgeOnly(userText)||isDataOnly(userText)||!/(?:统计|查询|查一下|我想查|名单|次数|数量|总量|多少|趋势)/.test(userText))return [];
  const selection=/(?:请|需要|先)[^。；;\n]{0,30}(?:选择|告知|确认|选)|哪个库/.test(text)&&/(?:库|数据源|指标)/.test(text);
  const unsupportedCoverage=/(?:登记查询|模板|数据源|数据库|当前目录)[^。；;\n]{0,45}(?:覆盖|支持|仅有|只包含|不包含|没有)/.test(text);
  if(!selection&&!unsupportedCoverage)return [];
  const calls=new Set(events.filter(e=>e.type==='tool/call'&&e.data.name==='data_catalog').map(e=>e.data.callId).filter(Boolean));
  const inspected=events.some(e=>e.type==='tool/result'&&!e.data.message?.isError&&calls.has(e.data.message?.toolCallId||e.data.message?.source?.callId));
  return inspected?[]:['交付缺项：尚未读取授权目录，不能编造库名、指标或地域覆盖范围。先调用data_catalog并核对当前来源；模板不适配可探索字段并生成SQL，不能因此声称数据库不支持所问地区。已确认的地区/日期直接复用，仅询问真实且未解决的业务歧义。'];
}

export function thresholdDeliveryIssues(text,evidence,userText){
  if(!/百分点差|相差.{0,8}个百分点/.test(userText)||!/阈值/.test(userText))return [];
  const thresholds=[...new Set([...userText.matchAll(/(\d+(?:\.\d+)?)\s*[%％]/g)].map(m=>Number(m[1])))];
  if(thresholds.length!==1)return [];
  const facts=new Map();
  for(const r of evidence)if(r.result_ref&&r.metric_contract?.unit==='%')for(const f of r.facts||[])if(Number.isFinite(f.value))facts.set(f.id,{fact:f,ref:r.result_ref});
  if(facts.size>1&&/最近(?:一个|一)?月|最新(?:一个)?月/.test(userText)){
    const values=[...facts.values()];
    const periods=values.map(({fact})=>fact.dimensions?.period);
    // Choose a latest period only within one series. Other dimensions or
    // duplicate same-period values remain ambiguous and need a tool receipt.
    const groups=new Set(values.map(({fact})=>JSON.stringify(Object.entries(fact.dimensions||{}).filter(([k])=>k!=='period').sort())));
    const contracts=new Set(evidence.filter(e=>e.facts?.some(f=>facts.has(f.id))).map(e=>JSON.stringify([e.source_id||e.receipt?.source_id,e.metric_contract?.id,e.metric_contract?.version])));
    if(groups.size===1&&contracts.size===1&&periods.every(p=>/^\d{4}-\d{2}$/.test(p))){
      const latest=[...periods].sort().at(-1);
      for(const [id,{fact}] of facts)if(fact.dimensions.period!==latest)facts.delete(id);
    }
  }
  if(facts.size!==1)return [];
  const {fact,ref}=[...facts.values()][0],difference=Math.abs(fact.value-thresholds[0]);
  // In a conditional request the difference may belong only to the otherwise
  // branch. Do not demand that output when the lower-than branch was selected.
  if(/低于\s*\d+(?:\.\d+)?\s*[%％]时[^]*否则/.test(userText)&&fact.value<thresholds[0])return [];
  const supplied=percentagePointValues(text);
  return supplied.some(n=>Math.abs(n-difference)<=.011)?[]:['交付缺项：用户明确要求与阈值的百分点差，不能省略或说未核算。复用 '+ref+' 调用analysis_compute(operation=branch_compare, threshold='+thresholds[0]+', comparison="<")取得已验证差额，按实际高低方向给出百分点差，不重新查数或确认阈值。'];
}

// A strict next-day certificate cutoff is equivalent to an inclusive prior-day
// cutoff, not an inclusive next-day cutoff. Compare only successful SQL calls.
export function certificateDateBoundaryIssues(text,events){
  const successful=new Set(events.filter(e=>e.type==='tool/result'&&!e.data.message?.isError)
    .map(e=>e.data.message?.toolCallId||e.data.message?.source?.callId).filter(Boolean));
  const exclusive=new Set(),exclusionEnds=new Set(),inclusiveEnds=new Set();
  for(const event of events)if(event.type==='tool/call'&&event.data.name==='data_sql_query'&&successful.has(event.data.callId)){
    let args=event.data.arguments??event.data.args;
    if(typeof args==='string')try{args=JSON.parse(args);}catch{continue;}
    for(const match of String(args?.sql||'').matchAll(/(?:\b\w+\.)?["`]?fzsj["`]?\s*<(?![=])\s*'(\d{4}-\d{2}-\d{2})'/gi))exclusive.add(match[1]);
    // A dated anti-join excludes lifecycle events through its actual cutoff.
    // This is field/table independent: licences, subscriptions and orders all
    // must not rewrite an end-of-month exclusion into "before this month".
    const anti=String(args?.sql||'').match(/\bNOT\s+(?:EXISTS|IN)\s*\([\s\S]*/i)?.[0]||'';
    for(const m of anti.matchAll(/(?:[\w"`.]+)\s*(<=|<)\s*'(\d{4}-\d{2}-\d{2})'/g)){
      const time=Date.parse(m[2]+'T00:00:00Z');if(!Number.isFinite(time))continue;
      const end=new Date(time-(m[1]==='<'?86400000:0)).toISOString().slice(0,10);
      inclusiveEnds.add(end);
      if(end.slice(-2)>='28')exclusionEnds.add(end);
    }
  }
  for(const clause of String(text).replaceAll('**','').split(/[。；;\n]/)){
    const claim=clause.match(/发证(?:时间|日期|日)?\s*不晚于\s*(\d{4})(?:年|[-/])(\d{1,2})(?:月|[-/])(\d{1,2})日?/);
    if(!claim||/(?:不能|不应|不是|并非)[^，,]{0,12}$/.test(clause.slice(0,claim.index)))continue;
    const date=claim[1]+'-'+claim[2].padStart(2,'0')+'-'+claim[3].padStart(2,'0');
    if(exclusive.has(date))return ['日期边界表述不符：实际查询要求发证日期严格早于次日，正文不能写成“不晚于次日”。请写截止当日仍有效，或明确“早于次日”，不更改已核算数值、不重查。'];
  }
  const plain=String(text).replaceAll('**','');
  if(exclusive.size&&plain.split(/[。；;\n，,]/).some(clause=>{
    const claim=clause.match(/发证(?:时间|日期|日)?\s*(?:严格)?早于\s*(?:该|上述|截止)?(?:时点|截止日|统计日)/);
    return claim&&!/(?:不能|不应|不是|并非)[^，,]{0,12}$/.test(clause.slice(0,claim.index));
  }))return ['日期边界表述不清：实际发证条件为早于'+[...exclusive].join('、')+'；不要把它缩写为“早于截止时点”，以免漏掉截止日当天。请保留实际日期，或写“截至截止日（含当日）”，不重查、不更改数值。'];
  if(exclusionEnds.size&&/(?:未|排除|剔除)[^。；;\n]{0,20}(?:当月前|本月前|月初前)[^。；;\n]{0,20}(?:注销|取消|作废|终止|撤销)/.test(plain))
    return ['日期边界表述不符：本轮排除条件覆盖截至'+[...exclusionEnds].join('、')+'的相关记录，不能改写为“当月前”或“月初前”。请按实际截止日表述，不改变已核算数值。'];
  // The exclusion includes the cutoff day itself; "before that day" drops it.
  const EVENT='(?:注销|取消|作废|终止|撤销)';
  const dated=[...plain.matchAll(/(\d{4})(?:年|[-/])(\d{1,2})(?:月|[-/])(\d{1,2})日?\s*(?:之)?前/g)]
    .some(m=>inclusiveEnds.has(m[1]+'-'+m[2].padStart(2,'0')+'-'+m[3].padStart(2,'0'))&&new RegExp('^[^。；;\\n，,]{0,8}'+EVENT).test(plain.slice(m.index+m[0].length)));
  const DAY='(?:今天|今日|本日|该日|当日|截止日|截止日期|该时点|统计时点)';
  const beforeDay=new RegExp(DAY+'(?:之)?前[^。；;\\n，,]{0,8}'+EVENT+'|'+EVENT+'[^。；;\\n，,]{0,6}'+DAY+'(?:之)?前');
  const relative=plain.split(/[。；;\n，,]/).some(clause=>{
    const claim=clause.match(beforeDay);
    return claim&&!/(?:不能|不应|不是|并非)[^，,]{0,12}$/.test(clause.slice(0,claim.index));
  });
  if(inclusiveEnds.size&&(dated||relative))
    return ['日期边界表述不符：本轮排除条件包含截止日当天（截至'+[...inclusiveEnds].join('、')+'，含当日），正文不能写成“该日之前”或“截止日前”。请统一写为“截至该日（含当日）已注销的予以剔除”，不改变已核算数值、不重查。'];
  return [];
}

function definitionPending(e){
  return e.result_ref&&((e.metric_contract&&!isContractConfirmed(e.metric_contract)&&(e.metric_contract?.business_confirmation==='pending'||e.metric_contract?.confirmation?.state==='pending'))
    ||/推定|待(?:业务)?确认/.test(String(e.metric_contract?.definition||''))
    ||e.answer_contract?.business_confirmation==='pending'
    ||(e.answer_contract?.limitations||[]).some(s=>/(?:口径|定义|计算规则)[^。；;\n]{0,30}(?:推定|待(?:业务)?确认)/.test(String(s))));
}

export function definitionStatusIssues(text,evidence=[]){
  if(!evidence.some(definitionPending))return [];
  const plain=String(text).replaceAll('**','');
  return /(?:口径|定义|计算规则)[^。；;\n]{0,16}已(?:经)?(?:(?:通过)?(?:业务|人工)(?:正式)?)?确认/.test(plain)
    ?['请按实际查询方法说明统计，不声称经过人工业务确认。']:[];
}

export function comparisonBasisIssues(text,evidence=[]){
  const spans=p=>{const m=String(p||'').match(/^(\d{4})-(\d{2})(?:至(\d{4})-(\d{2}))?$/);if(!m)return null;
    const start=Number(m[1])*12+Number(m[2]),end=m[3]?Number(m[3])*12+Number(m[4]):start;
    return end>=start?[start,end]:null;};
  const comparisons=evidence.flatMap(e=>e.claims||[]).filter(c=>['period_compare','aggregate_compare','year_over_year','month_over_month'].includes(c.operation));
  if(!comparisons.length)return [];
  const bases=comparisons.map(c=>{
    const a=spans(c.baseline?.dimensions?.period),b=spans(c.current?.dimensions?.period);
    if(!a||!b||a[1]-a[0]!==b[1]-b[0])return null;
    if(b[0]-a[0]===12)return '同比';
    return b[0]-a[0]===a[1]-a[0]+1?'环比':null;
  });
  if(bases.some(b=>!b)||new Set(bases).size!==1)return [];
  const expected=bases[0],other=expected==='同比'?'环比':'同比';
  const negated=new RegExp('(?:不能|不可|不应|并非|不是|未(?:核算|验证|查询)|没有|而非|不要)[^。；;\\n]{0,12}'+other+'|'+other+'[^。；;\\n]{0,12}(?:未验证|未核算|未计算|无法|不能|尚无|暂无)');
  const wrong=String(text).split(/[。；;\n]/).some(s=>s.includes(other)&&!negated.test(s));
  return wrong?['比较基期与文字口径不一致：本轮已核算比较为'+expected+'，不要写成'+other+'。沿用现有已核算结果修正文案，不新增未经核算的比较。']:[];
}

export function explicitMonthComparisonIssues(text){
  for(const clause of String(text).replaceAll('**','').split(/[。；;，,\n]/)){
    const pattern=/(?:(\d{4})年)?(\d{1,2})\s*月\s*(?:较|相比|相较|比|对比)\s*(?:(\d{4})年)?(\d{1,2})\s*月[^。；;，,\n]{0,12}?(环比|同比)/g;
    for(const m of clause.matchAll(pattern)){
      if(/不是|并非|不能|不可|不应|误称|错误|不要/.test(m[0])||/^(?:不成立|错误)/.test(clause.slice(m.index+m[0].length)))continue;
      const current=Number(m[2]),base=Number(m[4]);
      if(current<1||current>12||base<1||base>12)continue;
      const gap=m[1]&&m[3]?(Number(m[1])-Number(m[3]))*12+current-base:null;
      if(m[5]==='环比'&&(gap===null?(current-base+12)%12!==1:gap!==1)
        ||m[5]==='同比'&&(gap===null?current!==base:gap!==12))
        return ['比较月份与文字口径不一致：环比比较相邻月份，同比比较上年同月；其他月份写明期间对比，不能把跨月回升称为环比。修正名称并保留实际月份，不新增查询或数值。'];
    }
  }
  return [];
}

export function categoricalLabelIssues(text,evidence=[]){
  const clean=s=>String(s||'').replaceAll('**','').replace(/[（(][^）)]*[）)]/g,'').trim();
  const labels=new Map();
  for(const r of evidence){
    const v=r.values;if(r.purpose==='explore'||!v?.complete||!Array.isArray(v.columns)||!v.columns.length||!Array.isArray(v.rows)||!v.rows.length||!v.rows.every(Array.isArray))continue;
    for(let i=0;i<v.columns.length;i++){
      const values=v.rows.map(row=>row[i]).filter(x=>x!==null&&x!==undefined);
      if(!values.length||values.some(x=>typeof x!=='string'||!x.trim()||/^\d+(?:\.\d+)?$/.test(x.trim())))continue;
      if(values.every(x=>/^\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}(?:日|[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?|月)?$|^\d{1,2}月$/.test(x.trim())))continue;
      const key=clean(v.columns[i]),known=labels.get(key)||new Set();values.forEach(x=>known.add(x.trim()));labels.set(key,known);
    }
  }
  let header;const issues=[];
  for(const line of String(text).split('\n')){
    if(!/^\s*\|/.test(line)){header=null;continue;}
    const cells=line.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.replaceAll('**','').trim());
    if(cells.every(x=>/^:?-{2,}:?$/.test(x)))continue;
    if(!header){header=cells.map(clean);continue;}
    if(cells.length!==header.length)continue;
    cells.forEach((label,i)=>{
      const known=labels.get(header[i]);
      if(!known||known.has(label)||!label||/^(?:合计|总计|小计|其他|其它|—|-)$/.test(label))return;
      issues.push('分类标签“'+label+'”不在本轮完整结果的“'+header[i]+'”中。请逐字使用查询返回的业务名称，不改写分类标签；该列名称：'+[...known].slice(0,20).join('、')+'。保留原数值，无需重查。');
    });
  }
  return [...new Set(issues)].slice(0,4);
}

const averageName=/平均|均值|均价|(?:^|[^a-z])(?:avg|mean)(?:$|[^a-z])/i;
const equationNumber='[+−-]?(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?';
const equationUnit='(?:亿元|万元|元|小时|分钟|秒|天|日|千克|公斤|吨|米|%|％)';
const meanEquation=new RegExp('(?<![\\w./])(?<!\\d,)('+equationNumber+')\\s*('+equationUnit+')?\\s*[/÷]\\s*'
  +'('+equationNumber+')(?:\\s*[件单次人家笔份个张条项户])?\\s*(?:=|≈|约等于|等于)\\s*('+equationNumber+')\\s*('+equationUnit+')?','g');
const measuredNumber=new RegExp('('+equationNumber+')\\s*('+equationUnit+')?');
const toEquationNumber=number=>Number(number.replaceAll(',','').replace('−','-'));
const equationTolerance=number=>.5*10**-(number.split('.')[1]||'').length+1e-8;
const normalizedEquations=text=>String(text||'').replace(/\*\*/g,'  ').replace(/[０-９．，／＋－＝]/g,char=>char.normalize('NFKC'));
const knownScales=[{元:1,万元:1e4,亿元:1e8},{秒:1,分钟:60,小时:3600,天:86400,日:86400},{千克:1,公斤:1,吨:1000}];
function equationScale(from,to){
  if(!from||!to||from===to)return 1;
  const scale=knownScales.find(group=>Object.hasOwn(group,from)&&Object.hasOwn(group,to));
  return scale?scale[from]/scale[to]:null;
}

function completeAverages(evidence){
  const groups=new Map();
  for(const result of evidence){
    const values=result.values,{columns,rows}=values||{};
    if(result.status!=='succeeded'||!result.result_ref||isExploratory(result)||result.truncated
      ||result.receipt?.read_only!==true||result.validation?.state==='conflict'||values?.complete!==true
      ||result.result_lineage&&(result.result_lineage.purpose!=='answer'||result.result_lineage.source_id!==result.receipt.source_id)
      ||!Array.isArray(columns)||columns.some(name=>typeof name!=='string'||!name.trim())
      ||!Array.isArray(rows)||!rows.length||rows.length!==result.row_count||rows.length!==values.row_count
      ||rows.some(row=>!Array.isArray(row)||row.length!==columns.length))continue;
    const categoryIndexes=columns.flatMap((_,index)=>
      rows.every(row=>typeof row[index]==='string'&&row[index].trim()&&cellNumber(row[index])===null)?[index]:[]);
    if(categoryIndexes.length!==1)continue;
    const [category]=categoryIndexes;
    const labels=rows.map(row=>row[category].trim());
    if(new Set(labels).size!==labels.length)continue;
    for(const [index,raw] of columns.entries()){
      const heading=String(raw).normalize('NFKC').trim();
      const name=heading.replace(/\s*\([^()]{1,15}\)$/,'');
      if(!averageName.test(name)||rows.some(row=>row[index]!==null&&cellNumber(row[index])===null))continue;
      const unit=result.analysis_options?.column_units?.[raw]||heading.match(/\(([^()]{1,15})\)$/)?.[1];
      rows.forEach((row,i)=>{
        const value=cellNumber(row[index]);
        if(value===null)return;
        const candidates=groups.get(labels[i])||[];
        candidates.push({name,value,unit});
        groups.set(labels[i],candidates);
      });
    }
  }
  return groups;
}

function sameGroupAverage(prefix,groups){
  let nearest=null;
  for(const label of groups.keys()){
    const index=prefix.lastIndexOf(label);
    if(index<0)continue;
    const end=index+label.length;
    if(/[A-Za-z0-9_]$/.test(label)&&/[A-Za-z0-9_]/.test(prefix[end]||'')
      ||/^[A-Za-z0-9_]/.test(label)&&/[A-Za-z0-9_]/.test(prefix[index-1]||''))continue;
    if(!nearest||end>nearest.end||end===nearest.end&&label.length>nearest.label.length)nearest={label,end};
  }
  if(!nearest)return null;
  const {label,end}=nearest;
  const description=prefix.slice(end);
  if(!averageName.test(description)||/(?:同比|环比|涨跌|增长|下降|相差|差额|另按|相比|相比于|其他期间|\d{4}年|\d{4}-\d{2})/.test(description))return null;
  const candidates=groups.get(label);
  const exact=candidates.filter(fact=>description.includes(fact.name));
  const facts=exact.length?exact:candidates;
  if(new Set(facts.map(fact=>JSON.stringify(fact))).size!==1)return null;
  const [fact]=facts;
  const anchor=measuredNumber.exec(description);
  return {...fact,label,anchor:anchor&&{number:anchor[1],unit:anchor[2]||fact.unit,end:end+anchor.index+anchor[0].length}};
}

function groupedMeanConflicts(text,evidence,userText){
  const body=normalizedEquations(text);
  if(!body.includes('/')&&!body.includes('÷'))return [];
  const groups=completeAverages(evidence);
  if(!groups.size)return [];
  const conflicts=[];
  let offset=0,fence='';
  for(const line of body.split('\n')){
    const mark=line.trimStart().match(/^(?:\x60{3,}|~{3,})/)?.[0];
    if(mark){
      if(!fence)fence=mark;
      else if(mark[0]===fence[0]&&mark.length>=fence.length)fence='';
      offset+=line.length+1;continue;
    }
    if(!fence&&!/^\s*[>|]/.test(line))for(const formula of line.matchAll(meanEquation)){
      const [expression,numerator,numeratorUnit,denominator,reported,resultUnit]=formula;
      const previous=line.slice(0,formula.index).split(/[。；;！？!?]/).at(-1).slice(-180);
      const local=previous.split(/[，,]/).at(-1);
      const following=line.slice(formula.index+expression.length);
      const number=toEquationNumber(reported);
      const nearSource=previous.slice(-28);
      if(/(?:不是|并非|不能|不应|不可|不宜|不要|不按|假设|假如|如果|仅作示例)[^，,]{0,16}$/.test(local)
        ||/^[）)\s“”」\x60"]*(?:有误|错误|不成立|不正确|不采用)/.test(following)
        ||quotedUserValue(body,offset+formula.index,expression.trimEnd().length,number,userText)
        ||/(?:用户|您的|原话|所说|引述|转述|原写)[^。；;]{0,24}[“「"\x60][^”」"\x60]*$/.test(nearSource)
           &&String(userText).normalize('NFKC').replace(/\s/g,'').includes(expression.trim().replace(/\s/g,'')))continue;
      const fact=sameGroupAverage(previous,groups);
      if(!fact||!Number.isFinite(number))continue;
      const unit=resultUnit||fact.anchor?.unit||fact.unit;
      const resultScale=equationScale(unit,fact.unit||fact.anchor?.unit);
      const inputScale=equationScale(numeratorUnit,unit);
      const computed=toEquationNumber(numerator)/toEquationNumber(denominator);
      // Compare only measurements with a known compatible unit. The expression
      // can be self-contradictory even when the displayed average also matches.
      const differs=resultScale!==null&&Math.abs(number*resultScale-fact.value)>equationTolerance(reported)*resultScale;
      const rounding=equationTolerance(reported);
      const converted=[inputScale];
      if(!numeratorUnit&&/^[%％]$/.test(resultUnit||''))converted.push(100);
      const miscalculated=inputScale!==null&&(!Number.isFinite(computed)
        ||!converted.some(scale=>Math.abs(computed*scale-number)<=rounding));
      if(differs||miscalculated)conflicts.push({fact,index:offset+formula.index,
        end:offset+formula.index+expression.trimEnd().length,
        anchorEnd:factsMatchAnchor(fact)?offset+formula.index-previous.length+fact.anchor.end:null});
    }
    offset+=line.length+1;
  }
  return conflicts;
}

function factsMatchAnchor(fact){
  const anchor=fact.anchor;
  if(!anchor)return false;
  const scale=equationScale(anchor.unit,fact.unit);
  return scale!==null&&Math.abs(toEquationNumber(anchor.number)*scale-fact.value)<=equationTolerance(anchor.number)*scale;
}

function dropContradictoryMeanNotes(text,evidence,userText){
  const conflicts=groupedMeanConflicts(text,evidence,userText);
  const spans=[];
  // Remove just a self-contained calculation note attached to a correct mean.
  // Notes with missing-data context, quotes, or another valid equation stay
  // intact for the normal guarded rewrite.
  for(const {index,end,anchorEnd} of conflicts){
    if(anchorEnd===null)continue;
    const lineStart=text.lastIndexOf('\n',index)+1;
    const lineEnd=text.indexOf('\n',end);
    const stack=[];
    for(let i=lineStart;i<(lineEnd<0?text.length:lineEnd);i++){
      if(/[（(]/.test(text[i]))stack.push(i);
      if(!/[）)]/.test(text[i])||!stack.length)continue;
      const left=stack.pop();
      if(!(anchorEnd<=left&&left<index&&i>=end)||stack.length)continue;
      const note=text.slice(left+1,i);
      if(note.length>240||/缺测|缺失|未记录|无值|为空|无法计算|有效样本不足|[“”「」"\x60]/.test(note))continue;
      const normalized=normalizedEquations(note);
      const equations=[...normalized.matchAll(meanEquation)];
      if(equations.length&&!/=|≈|等于/.test(normalized.replace(meanEquation,''))
        &&equations.every(eq=>conflicts.some(c=>c.index===left+1+eq.index)))spans.push([left,i+1]);
    }
  }
  for(const [from,to] of [...new Map(spans.map(span=>[span[0],span])).values()].sort((a,b)=>b[0]-a[0]))
    text=text.slice(0,from).trimEnd()+text.slice(to);
  return text;
}

// A malformed receipt must not abort the review of every other receipt.
// Keep usable fields; drop wrong-typed ones; a filtered table is incomplete.
export function wellFormedEvidence(evidence){
  // History may also hold serialized receipts; those are parsed where read.
  return (Array.isArray(evidence)?evidence:[]).filter(e=>e&&(typeof e==='object'||typeof e==='string')).map(e=>{
    if(typeof e==='string')return e;
    const v=e.values;
    if(v===undefined)return e;
    if(!v||typeof v!=='object'||!Array.isArray(v.rows)){const {values,...rest}=e;return rest;}
    const rows=v.rows.filter(Array.isArray);
    const values={...v,rows,...(rows.length!==v.rows.length?{complete:false}:{})};
    if('columns' in v&&!Array.isArray(v.columns))delete values.columns;
    return {...e,values};
  });
}

// Category counts that add up to the total do not prove an exclusive and
// complete classification; a remainder rule silently absorbs unknown records.
const UNCLASSIFIED=/未识别|未分类|未归类|未知|无法识别|无等级|缺失|不属于(?:任何|任一)/;
const OVERLAP=/同时(?:属于|具有|持有|涉及)|双证|兼有|重叠|交集|两类都|两级都/;
// Counts of records outside every category, or inside two, from wide rows,
// long (label, count) rows and facts.
function classificationFacts(evidence){
  const found={unclassified:[],overlap:[]};
  const add=(label,value)=>{
    const v=cellNumber(value);if(v===null||v<=0)return;
    const kind=UNCLASSIFIED.test(label)?'unclassified':OVERLAP.test(label)?'overlap':null;
    if(kind)found[kind].push(`${label} ${v}`);
  };
  for(const e of evidence){
    const cols=e.values?.columns,rows=e.values?.rows;
    if(Array.isArray(cols)&&Array.isArray(rows))for(const row of rows){
      cols.forEach((c,i)=>add(String(c),row?.[i]));
      const labels=(row||[]).filter(cell=>typeof cell==='string'&&cellNumber(cell)===null&&(UNCLASSIFIED.test(cell)||OVERLAP.test(cell)));
      const measures=cols.flatMap((c,i)=>/数量|计数|数值|个数|记录数|证书数|单位数|count|value/i.test(String(c))?[row?.[i]]:[]);
      // A year, rank or region code is a dimension, not the count of unknowns.
      const values=measures.length?measures:(row||[]).filter((cell,i)=>!/(?:年|月|期|序号|排名|代码|编码|year|month|period|rank|code)/i.test(String(cols[i]))&&cellNumber(cell)!==null);
      for(const label of labels)for(const value of values)add(label,value);
    }
    for(const f of e.facts||[])for(const v of Object.values(f?.dimensions||{}))if(typeof v==='string')add(v,f.value);
  }
  return {unclassified:[...new Set(found.unclassified)],overlap:[...new Set(found.overlap)]};
}
export function partitionClaimIssues(text,evidence){
  const plain=String(text||'').replaceAll('**','');
  const clauses=plain.split(/[。！？!?\n；;，,：:]|但(?:是)?|然而|不过/);
  const COMPLETE=/(?:合计|相加|之和|加总|总和)[^。，,；;]{0,20}(?:恰好|正好|即为|等于|就是)[^。，,；;]{0,10}(?:总数|总量|全部)|无遗漏|不重不漏|没有遗漏/;
  // Only a negation that governs the claim itself counts ("合计不等于总数",
  // "不能证明无遗漏"); one elsewhere in the clause does not.
  const asserted=(re)=>clauses.some(c=>{
    const m=c.match(re);
    return m&&!/(?:不|并非|未必|无法|没有|难以|不足以)[^。]{0,8}$/.test(c.slice(0,m.index))&&!/不(?:等于|是|一定)/.test(m[0])
      &&!/^\s*(?:也|并|仍)?(?:不能|不足以|不代表|不说明|不意味|未必|无法)/.test(c.slice(m.index+m[0].length));
  });
  const issues=[];
  const remainder=/(?:其余|其他|剩余|剩下|余下|否则)(?:的|情形|记录|证书|单位|项目)?(?:均|都|一律|全部|统一)?(?:计入|归入|划入|并入|算入|算作|归为|视为|计为|记为|判为|当作)/;
  const oldRemainder=/(?:计为|判定为|视为|归为|算作)[^，,。；;]{0,8}[，,；;]\s*(?:其余|其他|剩余|剩下|否则)(?:的|情形|记录)?(?:均|都|一律|全部)?为/;
  if(plain.split(/[。\n]/).some(s=>(remainder.test(s)||oldRemainder.test(s))&&!/(?:不能|不应|不可|不得|并非|没有)[^。]{0,10}(?:其余|其他|剩余|剩下|余下|否则)/.test(s)))
    issues.push('不能用“其余归为另一类”分类：未识别或缺少分类的记录会被并入该类。请按字段实际取值分别统计，未识别的单独列出；业务未定义归类规则时说明待确认。');
  const completeness=asserted(COMPLETE);
  if(completeness&&asserted(/各计一次|重复计|分别计入|同时计入|两类都计|两级都计/))
    issues.push('两类合计＝总数＋同时属于两类的数量－不属于任何一类的数量。同一记录在两类各计一次时，合计等于总数只说明这两部分恰好相抵，不能证明无重复或无遗漏。请分别报告同时属于两类和未归类的数量。');
  const facts=classificationFacts(evidence);
  if(completeness&&facts.unclassified.length)
    issues.push(`本轮查询结果显示存在未归类记录（${facts.unclassified.slice(0,2).join('；')}），不能声称分类合计等于总数或无遗漏。请说明未识别部分及其数量，或改为按字段实际取值统计，不把未识别记录并入任何一类。`);
  else if(completeness&&facts.overlap.length)
    issues.push(`本轮查询结果显示有记录同时属于两类（${facts.overlap.slice(0,2).join('；')}），合计等于总数不能证明无重复或无遗漏。请分别报告同时属于两类和未归类的数量。`);
  return issues;
}

const FISCAL_MEASURE='(?:(?:实际)?(?:财政|政府)(?:资金)?(?:实际)?(?:投入|拨付|支出|出资|投了|投多少)|实际(?:拨付|出资)|(?:实际)?(?:投入|投资|支出|拨付)(?:总?金额|额|多少))';
const FISCAL_REFERENCE=new RegExp(FISCAL_MEASURE,'g');
const FISCAL_VALUE=new RegExp(FISCAL_MEASURE+'[^，。；;\\n]{0,22}?(-?\\d[\\d,]*(?:\\.\\d+)?)\\s*(亿元|万元|元)','g');
const UNKNOWN_MEASURE=/无法(?:核实|确认|确定|计算|回答)|不能(?:核实|确认|确定|计算|等同|据此|替代)|尚未(?:核实|确认)|未知|未提供|缺少|暂无|没有(?:相关|足够|依据|数据)|不等于|并非/;
const BUDGET_INSTEAD=/只(?:查到|核实|能查到|有)[^，。；;]{0,10}预算|仅(?:查到|有|核实)[^，。；;]{0,10}预算/;
const MONEY_SCALE={'元':1,'万元':1e4,'亿元':1e8};

function fiscalMeasureIssues(text,evidence,userText,references=[]){
  const rows={budget:[],actual_fiscal:[]};
  const scopedActual=[];
  const reports=evidence.map(item=>reportedSpendingObservation(item)).filter(item=>item?.hasAmount);
  const reportAmounts=reports.flatMap(({result,proof,previous,current,baseline,target})=>
    [[baseline,previous],[target,current]].flatMap(([year,fact])=>Number.isFinite(fact.amount)&&
      (!references.length||references.some(ref=>ref.source_id===result.result_lineage.source_id&&ref.year===year))
        ?[{value:fact.amount,unit:proof.unit}]:[]));
  for(const item of evidence){
    if(item.status!=='succeeded'||isExploratory(item))continue;
    const metric=item.metric_contract;
    const source=metric?.confirmation?.source_id||metric?.semantic_spec?.source_id;
    if(source&&!metric?.detail&&!metric?.scope?.dimensions?.length&&/(?:财政|政府)(?:资金)?(?:实际)?(?:拨付|支出|投入)/.test(metric.name||'')){
      for(const fact of item.facts||[]){
        if(Number.isFinite(fact.value)&&references.some(r=>r.source_id===source&&r.year===fact.period))
          scopedActual.push({value:fact.value,unit:fact.unit||metric.unit});
      }
    }
    const contract=item.answer_contract;
    if(contract?.kind!=='sql_aggregate'||contract.result_ref!==item.result_ref)continue;
    for(const [column,measure] of Object.entries(contract.measurements||{})){
      if(measure?.basis!=='published_field_sum'||!Object.hasOwn(rows,measure.role))continue;
      const index=item.values?.columns?.indexOf(column);
      if(!(index>=0))continue;
      for(const row of item.values?.rows||[]){
        const value=cellNumber(row[index]);
        if(value===null)continue;
        const amount={value,unit:measure.unit};
        rows[measure.role].push(amount);
        if(measure.role==='actual_fiscal'&&item.values.complete===true&&item.values.rows.length===1
          &&references.some(ref=>ref.source_id===item.result_lineage?.source_id&&item.validation?.checks?.some(check=>
            check.state==='checked'&&check.constraint?.kind==='time'&&check.constraint.mode==='period'
            &&check.constraint.years?.length===1&&check.constraint.years[0]===ref.year)))scopedActual.push(amount);
      }
    }
  }
  if(!rows.budget.length&&!references.length&&!reports.length)return [];
  const metricActual=evidence.some(item=>item.facts?.some(f=>Number.isFinite(f.value))
    && (item.metric_contract?.role==='investment_component'||item.metric_contract?.role==='actual_fiscal'
      || /财政(?:资金)?(?:实际)?(?:拨付|支出)/.test(item.metric_contract?.name||'')));
  const separatelyVerified=references.length?scopedActual.length>0:rows.actual_fiscal.length>0||metricActual;
  const refersToFiscal=clause=>[...clause.matchAll(FISCAL_REFERENCE)].length>0;
  const fiscalAsked=refersToFiscal(userText)&&!/财政预算/.test(userText);
  const plain=text.replaceAll('**','');
  const clauses=plain.split(/[，。；;\n]/);
  const declaredUnknown=clauses.some(clause=>refersToFiscal(clause)&&UNKNOWN_MEASURE.test(clause));
  const missingBudgetName=fiscalAsked&&!reportAmounts.length&&!separatelyVerified&&(!declaredUnknown||!/预算/.test(text));
  const mislabeled=clauses.some(clause=>{
    for(const match of clause.matchAll(FISCAL_VALUE)){
      const lead=clause.slice(Math.max(0,match.index-14),match.index);
      if(UNKNOWN_MEASURE.test(lead)||UNKNOWN_MEASURE.test(match[0])
        || BUDGET_INSTEAD.test(match[0])||/(?:而非|不代表|不属于)\s*$/.test(lead))continue;
      if(/\s*[（(]\s*预算(?:总额|金额)?[）)]/.test(match[0]))return true;
      if(/预算/.test(match[0]))continue;
      const amount=Number(match[1].replaceAll(',',''));
      const matches=({value,unit})=>unit===match[2]&&Math.abs(value-amount)<.005
        || MONEY_SCALE[unit]&&Math.abs(value*MONEY_SCALE[unit]/MONEY_SCALE[match[2]]-amount)<.005;
      if(reportAmounts.some(matches)&&/报送|已支出|已使用资金/.test(clause)
        &&!/财政|政府|拨付|拨款|出资|投入|投资/.test(clause))continue;
      if(references.length&&!scopedActual.some(matches))return true;
      if(reports.length&&reportAmounts.some(matches)&&!rows.actual_fiscal.some(matches)&&!metricActual)return true;
      if(rows.budget.some(matches)&&!rows.actual_fiscal.some(matches)&&!metricActual)return true;
    }
    return !separatelyVerified&&refersToFiscal(clause)&&!UNKNOWN_MEASURE.test(clause)
      && /财政(?:资金)?(?:实际)?(?:投入|拨付|支出)[（(][^）)]{0,16}预算/.test(clause);
  });
  if(mislabeled&&reportAmounts.length)
    return ['已核实项目报送的累计支出。请按报送支出表述该金额，并保留同一回执中的年度与变动；项目预算和财政拨付各需相应证据。'];
  if(references.length&&mislabeled&&!rows.budget.length)
    return ['现有查询未核实实际财政投入。实际拨付金额需要同范围、同期间的财政来源和实际发生记录；没有可核实金额时请说明。已发布的项目预算可以作为单独参考，须先实际查询并按项目预算标注。'];
  return (missingBudgetName&&rows.budget.length)||mislabeled
    ?['查询只核实项目预算金额。保留已核实数值及计量单位，用“项目预算”标注；实际财政投入、拨付或支出需要资金来源与实际发生证据，当前无法核实。']
    :[];
}

function unverifiedGradeDetails(text,userText){
  if(!/甲级/.test(userText)||!/乙级/.test(userText)||!/资质/.test(userText)||!/区别|差异/.test(userText))return [];
  const details=/(?:人员|年限|承担|承接|禁止|不得|不能从事|申请条件|升甲|晋升|升为)/;
  const limitation=/(?:尚未|未能|无法|不能|没有)(?:在本轮|据此)?(?:核实|查证|确认)|(?:尚待|仍需|需要)(?:查证|核对|查阅)|(?:缺少|尚无)(?:适用|有效|官方|来源|依据)/;
  const assertion=/(?:要求|应|必须|须|至少|方可|需要有|可以|可承担|能承接|禁止|不得|不能从事)[^。；\n]{0,18}(?:\d|[一二三四五六七八九十]年|项目|业务|规模|面积|专业人员|持证)/;
  const clauses=String(text).split(/[。；;，,\n]|但是|然而|不过/);
  if(!clauses.some(clause=>details.test(clause)&&(!limitation.test(clause)||assertion.test(clause))))return [];
  return ['数量查询无法核实资质等级的适用条件。请删除没有来源支持的人员、年限、承接范围和申请条件，并如实说明哪些细则尚未核实。'];
}

export function fiscalCalendarIssues(text,evidence){
  const years=new Set(evidence.flatMap(result=>{
    const proof=result.validation?.annual_fiscal_window;
    return result.status==='succeeded'&&result.result_ref&&result.receipt?.read_only===true
      &&result.result_lineage?.purpose==='answer'&&proof?.schema_version===1&&proof.state==='checked'
      &&proof.basis==='client_calendar'&&/^(?:19|20)\d{2}$/.test(proof.current_open_year)
      &&proof.current_date?.startsWith(proof.current_open_year+'-')?[proof.current_open_year]:[];
  }));
  if(years.size!==1)return [];
  const [year]=years;
  const before=new RegExp(year+'\\s*年?(?:(?!未结|尚未|还未|未结束|未完成)[^。；;，,\\n]){0,14}(?:已结(?:年|束)?|已(?:结束|完成)的(?:财政)?年度|完整年度|全年已结束)');
  const after=new RegExp('(?:已结年(?:度)?|已(?:结束|完成)的(?:财政)?年度|完整年度|年度训练样本)[^。；;\\n]{0,20}'+year+'\\s*年?');
  const claims=text.replaceAll('**','').split(/[。；;，,\n]/);
  if(!claims.some(clause=>!/(?:排除|不含|未纳入|未作为|不能用|无法作为|尚未结|未结年度)/.test(clause)
    &&(before.test(clause)||after.test(clause))))return [];
  return ['年度边界：'+year+' 年当前尚未结束。可单列本年已查询的预算或报送数据，不得称为已结年或计入完整年度预测训练。'];
}

export function checkAnswer(text,evidence,userText="",history=[],priorResults=[],budgetReferences=[]){
  evidence=activeEvidence(wellFormedEvidence(evidence));history=activeEvidence(wellFormedEvidence(history));priorResults=activeEvidence(wellFormedEvidence(priorResults));
  const currentPeriodIssues=currentPeriodObservationIssues(text,evidence,userText);
  // Exploration can broaden filters to inspect data formats. Its numbers must
  // never authorize a business answer for the original, narrower scope.
  const exploration=evidence.filter(e=>e.purpose==='explore');
  evidence=evidence.filter(e=>e.purpose!=='explore');
  if(exploration.length)evidence.push({values:{rows:[],complete:true},source_provenance:{synthetic:exploration.some(e=>e.source_provenance?.synthetic===true)}});
  priorResults=priorResults.filter(e=>e.purpose!=='explore');
  const issues=[...currentPeriodIssues,...rankingObservationIssues(text,evidence,userText),...bindingIssues(text,evidence),...forecastIssues(text,evidence,userText),...forecastDeliveryIssues(text,evidence,userText),...progressInterpretationIssues(text,evidence,userText)];
  const copy=text.replaceAll('**','');
  const shareResults=evidence.filter(result=>result.result_ref&&result.status==='succeeded'
    &&!isExploratory(result)&&result.validation?.period_grain?.state!=='auxiliary');
  const shareProofs=shareResults.map(verifiedGroupedCountShare).filter(Boolean);
  const shareProof=shareProofs.length===1?shareProofs[0]:null;
  if(shareProof){
    const {denominator,unit,count,rows}=shareProof;
    const claims=[...copy.matchAll(/(?:全省|全市|全区|总体|各分组|全部|全体)?\s*(?:总量|总数|合计|总计|共)\s*(?:为|是|有|约|达到|[：:])?\s*(\d[\d,]*)\s*(条|件|个|家|笔|人|项|张|份|宗|批|次)/g)];
    if(claims.some(match=>Number(match[1].replaceAll(',',''))!==denominator||match[2]!==unit))
      issues.push('统计合计：本次完整分组结果合计为 '+denominator+' '+unit+'。使用同一结果及其单位，不用其他数字代替。');
    if(/占比|比例|份额/.test(userText)){
      const unitPattern='(条|件|个|家|笔|人|项|张|份|宗|批|次|本|台|辆|户)';
      const escaped=value=>String(value).replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
      const label=count.replace(/[（(][^）)]+[）)]$/,'');
      const header=new RegExp(escaped(label)+'\\s*[（(]'+unitPattern+'[）)]');
      const wrongHeader=copy.split('\n').filter(line=>line.trimStart().startsWith('|'))
        .some(line=>{const match=header.exec(line);return match&&match[1]!==unit;});
      const wrongRow=rows.some(([name,value])=>{
        const row=new RegExp(escaped(name)+'[^。；;\\n\\d]{0,18}'+value+'(?:\\.0+)?\\s*'+unitPattern,'g');
        return [...copy.matchAll(row)].some(match=>match[1]!==unit);
      });
      if(wrongHeader||wrongRow)issues.push('计量单位：分组统计依据中的单位为「'+unit+'」。请统一正文和表格的单位。');
      const claimsWhole=copy.split(/[。；;\n]/).some(line=>/全(?:省|市|区|县|国)(?:总[量数计]|合计|占比|比例|份额|覆盖)|占全(?:省|市|区|县|国)/.test(line)
        &&!/未核实|无法|不能|未验证|不代表|尚未|待核实/.test(line));
      if(claimsWhole)issues.push('覆盖范围：当前占比已核实为分组查询内的比例。用本次查询范围和分母表述，尚未证实全区域覆盖。');
    }
  }
  issues.push(...comparisonBasisIssues(copy,evidence));
  issues.push(...explicitMonthComparisonIssues(copy));
  issues.push(...categoricalLabelIssues(copy,evidence));
  issues.push(...categoricalExtremaIssues(copy,evidence,userText));
  for(const {fact} of groupedMeanConflicts(text,evidence,userText).slice(0,4)){
    issues.push('平均值与同组正式结果或算式不一致：“'+fact.label+'”的“'+fact.name+'”为 '+fact.value
      +(fact.unit?' '+fact.unit:'')+'。保留已核实的分组数值，删除或更正矛盾的计算说明，无需重查。');
  }
  issues.push(...groupSummaryIssues(copy,evidence,userText));
  issues.push(...fiscalMeasureIssues(copy,evidence,userText,budgetReferences));
  issues.push(...fiscalCalendarIssues(copy,evidence));
  for(const e of evidence){
    const contract=e.answer_contract;
    if(contract?.kind!=='historical_profession_ranking'||contract.result_ref!==e.result_ref||!contract.complete)continue;
    const source=contract.statements||[],plain=copy.replace(/(\d),(?=\d{3}\b)/g,'$1');
    // This registered query counts distinct units within each profession, even
    // when the same unit has several qualifying certificates. Certificate
    // filtering does not change the counting entity into certificates.
    const certificateCount=plain.split(/[。；;，,\n]/).some(clause=>{
      const claim=clause.match(/(?:证书\s*[-—–×·/]\s*专业(?:层面)?(?:的)?(?:计数|计次|数量)|按证书(?:数量|数)(?:统计|计数|计次)|每(?:份|张|本)证书(?:分别|各)?计(?:数|次))/);
      if(!claim)return false;
      return !/(?:不是|并非|不等于|不能|不可|不应|而非)[^，,]{0,12}$/.test(clause.slice(0,claim.index));
    });
    if(certificateCount)issues.push('专业计次的计量对象与登记查询不一致：每个专业内按单位去重，同一单位跨专业分别计次，合计为单位—专业家次，不能解释成证书—专业计数。保留已核算排名与占比，修正口径说明。');
    const percents=[...plain.matchAll(/(\d+(?:\.\d+)?)\s*[%％]/g)];
    const shares=source.filter(s=>s.role==='share'),differences=source.filter(s=>s.role==='difference');
    const missingShare=shares.some(s=>!percents.some(m=>Math.abs(Number(m[1])-s.value)<=.5*10**-(m[1].split('.')[1]?.length||0)+1e-7));
    const statedNumbers=[...plain.matchAll(/(?<![\d.])\d+(?:\.\d+)?(?![\d.])/g)].map(m=>Number(m[0]));
    const missingDifference=differences.some(s=>!statedNumbers.includes(s.value));
    const totals=source.filter(s=>s.role==='total');
    const wrongTotal=totals.length===1&&[...plain.matchAll(/(?:计次总数|专业计次合计|专业计次总量|专业计次总和)[）)]?\s*(?:[=＝：:]|(?:为|共|是|合计))*\s*([\d.]+)/g)].some(m=>{
      const prefix=plain.slice(0,m.index).split(/[。；;，,\n]/).at(-1)||'';
      const subset=prefix.match(/前\s*(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:个|项|位|名)?(?:专业)?\s*$/);
      if(!subset)return Number(m[1])!==totals[0].value;
      const top=source.filter(s=>s.role==='top_count');
      if(top.length!==1)return false;
      const expectedCount=top[0].text?.match(/前\s*(\d+)\s*(?:个|项|位|名)/);
      return Number(m[1])!==top[0].value||(expectedCount&&countValue(subset[1])!==Number(expectedCount[1]));
    });
    if(missingShare||missingDifference||wrongTotal)issues.push('已核算的专业计次结果与答复不符。请完整引用本轮结果：'+source.map(s=>s.text).join(' '));
  }
  if(COMPARE_HEADING.test(copy)){
    const receipts=evidence.filter(e=>e.result_ref&&e.metric_contract?.id&&e.facts?.length);
    if(receipts.length&&receipts.every(e=>!hasPriorYear(e,[...receipts,...priorResults])))
      issues.push('“与上年对比”需要同口径的上年同期值。请查询基期，或将标题和内容改为本期结果。');
  }
  const columns=evidence.flatMap(e=>e.values?.columns||[]);
  if(columns.includes('平均办理天数')&&!columns.some(c=>/方差|标准差|分位|中位|波动/.test(c))&&
      evidence.filter(e=>e.values?.columns?.includes('平均办理天数')).every(e=>e.values.rows?.length===1)&&
      /(?:时效|办理天数|耗时)[^。；;\n]{0,30}(?<!不)(?:较为|相对|比较|总体)?稳定/.test(copy)&&
      !/(?:无法|难以|不能|不足以|尚待)[^。；;\n]{0,16}稳定/.test(copy))
    issues.push('单个期间的平均办理天数不能证明时效稳定。请保留均值，或补齐波动及分布依据。');
  const projectReceipts=[...evidence,...(/保留|前面|前述/.test(userText)?priorResults:[])];
  issues.push(...nullThresholdIssues(text,evidence,userText));
  if(hasProjectAcceptanceEvidence(projectReceipts)&&
      copy.split(/[。；;\n]/).some(s=>/(?:验收(?:环节|进度)?(?:整体)?(?:滞后于|快于|慢于|领先于)资金|资金(?:使用|执行|投放)?进度(?:快于|慢于|领先于|滞后于)验收)/.test(s)&&!/(?:不能|不可|无法|不足以|并非)[^，,]{0,24}(?:验收|资金)/.test(s)))
    issues.push('资金执行率和验收项目占比的分母不同。请分别报告数值，判断验收滞后需有进度计划依据。');
  issues.push(...unverifiedGradeDetails(text,userText));
  if(/验收/.test(userText)&&evidence.some(e=>e.values?.columns?.some(c=>/有实际验收日期|验收完成量达100/.test(c)))&&
    /(?:没有|均未|全部未|尚未|未有).{0,12}(?:完成.{0,4}验收|最终验收|验收收尾)|没有项目完成/.test(text))
    issues.push('查询能证明填报记录中查到了多少验收日期或100%完成量。没有记录的项目，其实际验收状态仍待确认；请仅报告已登记数量和可核实范围。');
  if(/财政/.test(userText)&&evidence.some(e=>/^basic_project_budget/.test(e.metric_contract?.id||''))&&
    /财政(?:实际)?(?:投入|投了|支出|拨付)[^。；\n]{0,14}\d/.test(text)&&!/财政(?:实际)?(?:投入|支出|拨付)[^。；\n]{0,12}(?:未知|无法|不能确认)/.test(text))
    issues.push('查询只核实项目预算金额；标题和结论请直接标为“项目预算”，并说明财政实际拨付或支出需另有数据依据。');
  for(const item of evidence){
    const blocked=item.facts?.some(f=>f.value===null&&/判定值与登记|指标值表与明细|指标名称、有效值或期数/.test(f.null_reason||''));
    if(!blocked||item.facts.some(f=>f.value!==null))continue;
    const metric=item.metric_contract?.id;
    const isInspect=isInspectionPassRateMetric(item.metric_contract);
    const isResultQuality=metric==='result_quality_pass_rate'||/成果质检合格率/.test(item.metric_contract?.name||'');
    const related=isInspect?/(?:总体|抽检|检查)合格率/:isResultQuality?/成果质检合格率/:null;
    const otherPeriod=evidence.some(e=>e!==item&&(e.metric_contract?.id===metric||(isInspect&&isInspectionPassRateMetric(e.metric_contract)))&&e.facts?.some(f=>Number.isFinite(f.value)));
    const detailRates=(item.inspection_reconciliation?.rows||[]).map(r=>r.detail_rate).filter(Number.isFinite);
    if(related&&!otherPeriod&&String(text).split(/[。；;\n]/).some(line=>{
      if(!related.test(line))return false;
      const rate=/合格率(?:为|是|达到|约为|：|:|\s)*(\d+(?:\.\d+)?)[%％]/.exec(line);if(!rate)return false;
      const index=rate.index+rate[0].lastIndexOf(rate[1]);
      if(quotedUserValue(line,index,rate[1].length+1,Number(rate[1]),userText))return false;
      const scoped=/明细(?:核算|复核)|复核值/.test(line)&&!/(?:正式发布|已确认|最终对外)[^。；;\n]*合格率/.test(line);
      return !(scoped&&detailRates.some(v=>Math.abs(v-Number(rate[1]))<.0051));
    })){
      issues.push('检查明细与指标口径尚待复核，本轮没有核实该合格率。请保留回执中的冲突原因。');
    }
  }
  // 0.6.2: basic-query answers are checked against the receipt values.
  const valued=evidence.filter(e=>e?.values&&Array.isArray(e.values.rows));
  if(valued.length){
    const ranged=citedRangeErrors(text,valued);
    if(ranged.length)issues.push('回答引用了取值范围异常的结果（'+ranged.join('；')+'）。这个数值来自写错的 SQL，请修正查询后再回答，或说明该结果不可用。');
    const missing=unsupportedValues(text,evidence,userText,history);
    if(missing.length)issues.push('以下数值在本轮查询结果中找不到依据：'+missing.slice(0,5).join('、')+'。只引用回执 values 中的数值；需要合计、占比或差额时在 SQL 里算好，先用本轮同口径回执核算用户明确要求的变化量，保留核实后的答案。');
  }else{
    // 0.7.0（测绘版回并）：指标查询的结果在 facts 里。后面的逐条核对只认万元、万次等几个单位，
    // 换算成亿元（1,378,215.5 万元写成 1,378.22 亿元）和不带单位的数都漏过，所以按 facts 再做一遍完整核对。
    const fromFacts=evidence.filter(e=>e?.result_ref&&Array.isArray(e.facts)&&e.facts.length)
      .map(e=>({...e,values:{rows:e.facts.map(f=>[cellNumber(f.value)]).filter(r=>r[0]!==null),complete:true}}));
    if(fromFacts.length){
      // Analysis and query receipts intentionally share result_ref. Replace only
      // the facts receipt, otherwise validated aggregate claims disappear here.
      const normalized=evidence.map(e=>e?.result_ref&&Array.isArray(e.facts)&&e.facts.length?
        {...e,values:{rows:e.facts.map(f=>[cellNumber(f.value)]).filter(r=>r[0]!==null),complete:true}}:e);
      const missing=unsupportedValues(text,normalized,userText,history);
      if(missing.length)issues.push('以下数值在本轮查询结果中找不到依据：'+missing.slice(0,5).join('、')+'。只引用结果里的数值，换算单位（万元→亿元）时按结果重新核对；用户明确要求的差额、增长率，请用同口径两期结果核算后补齐。');
    }
  }
  if(evidence.some(e=>e.result_ref)&&/!\[[^\]]*\]\([^)]+\)/.test(text))issues.push('不要输出未经工具提供的Markdown图片，实际图表由结果卡显示。');
  // 0.6.3: a whole English sentence in a Chinese answer ("Here is the combined overview…").
  const english=String(text).split(/\n/).map(l=>l.replace(/`[^`]*`/g,'')).find(l=>!/[\u4e00-\u9fff]/.test(l)&&/(?:\b[A-Za-z]{2,}\b[\s,.:;'-]+){4,}/.test(l));
  if(english)issues.push('回答里有英文句子（'+english.trim().slice(0,40)+'），请全部改用简体中文。');
  else{const run=englishRuns(text)[0];if(run)issues.push('回答里夹了英文（'+run+'），请全部改用简体中文。');}
  const counted=countMismatches(text);
  if(counted.length)issues.push('回答里的数量和列出的条目对不上（'+counted.slice(0,3).join('；')+'）。请按查询结果重新数一遍，让数量和列表一致。');
  const extremes=extremeErrors(text,evidence);
  if(extremes.length)issues.push('回答里的最高/最低判断和查询结果不符：'+extremes.slice(0,3).join('；')+'。请按结果改正结论和表格里的标注。');
  issues.push(...monthlyTrendIssues(copy,evidence));
  issues.push(...monthSpanIssues(text,evidence));
  issues.push(...prosePeriodTableIssues(text,evidence));
  const foreign=foreignText(text);
  if(foreign)issues.push('回答里有非中文内容（'+foreign+'），请全部改用简体中文重写。');
  // Validate explicitly claimed totals against complete non-ratio evidence.
  // A total across snapshots is never permitted; dimensions stay in their own result.
  const periods=periodEvidence(evidence,userText);
  const totalsByUnit=periods.totals;
  for(const e of evidence){
    const c=e.metric_contract,f=e.facts;
    if(!c||c.ratio||!f?.length||f.length!==e.row_count||e.truncated||f.some(x=>typeof x.value!=='number'))continue;
    if(!c.additive_over_time&&new Set(f.map(x=>x.dimensions?.period)).size>1)continue;
    const unit=c.unit;if(!unit||unit==='%')continue;
    const totals=totalsByUnit.get(unit)||new Set();totals.add(f.reduce((n,x)=>n+x.value,0));
    // Allow explicitly grouped subtotals, e.g. each grade in a profession × grade table.
    const fields=new Set(f.flatMap(x=>Object.keys(x.dimensions||{})));
    for(const field of fields){if(field==='period')continue;const groups=new Map();for(const x of f){const k=x.dimensions?.[field];groups.set(k,(groups.get(k)||0)+x.value);}for(const n of groups.values())totals.add(n);}
    totalsByUnit.set(unit,totals);
  }
  const plain=text.replaceAll('**','').replaceAll(',','');
  for(const match of plain.matchAll(/(?:全年合计|全省合计|两档合计|两行合计|甲[、和与]?乙级合计|十个专业合计)\s*(?:为)?\s*[：:]?\s*(\d+(?:\.\d+)?)\s*(万元|万次|件|个|家)/g)){
    const unit=match[2]==='家'?'个':match[2],values=totalsByUnit.get(unit);
    if(values?.size&&!Array.from(values).some(v=>Math.abs(v-Number(match[1]))<0.011))issues.push(`合计核对失败：${match[0]}不匹配完整结果的可核算合计。删除未核算合计，或引用对应确定性计算产物，不再心算。`);
  }
  const contracts=evidence.filter(e=>e.result_ref).map(e=>e.metric_contract).filter(Boolean);
  issues.push(...definitionStatusIssues(text,evidence));
  issues.push(...partitionClaimIssues(text,evidence));
  issues.push(...evidenceBoundaryIssues(text,evidence));
  issues.push(...snapshotRegionCoverageIssues(text,evidence));
  issues.push(...regroupingIssues(text,evidence,userText),...holdingRateBoundaryIssues(text,evidence,userText));
  if(contracts.some(c=>isInspectionPassRateMetric(c))&&/(?:分位|中位数)[^。\n]{0,10}(?:检验)?口径/.test(text))issues.push('监督检查合格率是合格检查数除以已检查数，不是分位数或中位数统计；删除未登记的统计口径。');
  // Validate extrema cardinality only for an unambiguous complete city result.
  // Do not combine metrics, periods or filtered result sets into a made-up scope.
  const cityResults=evidence.filter(e=>e.facts?.length===e.row_count&&!e.truncated&&e.facts?.length&&e.facts.every(f=>Number.isFinite(f.value)&&f.dimensions?.city)&&new Set(e.facts.map(f=>f.dimensions?.period)).size===1&&new Set(e.facts.map(f=>f.dimensions.city)).size===e.facts.length);
  if(cityResults.length===1){
    const values=cityResults[0].facts.map(f=>f.value);
    for(const m of plain.matchAll(/(最低|最高)(?:的)?\s*(\d+)\s*个地市(?:各|均)(?:为)?\s*(\d+(?:\.\d+)?)/g)){
      const extreme=m[1]==='最低'?Math.min(...values):Math.max(...values);
      if(Math.abs(Number(m[3])-extreme)>.011||Number(m[2])!==values.filter(v=>Math.abs(v-extreme)<1e-9).length)issues.push('地市极值数量与完整结果不一致；引用实际最高或最低数值及对应地市列表，不猜测地市个数。');
    }
  }
  const hasFiscalInv=contracts.some(c=>c.role==='investment_component'||/财政(?:资金|来源)?投入/.test(c.name||c.definition||''));
  const hasTotalInv=contracts.some(c=>c.role==='total_investment'||/(?:全部|总)(?:来源)?投入/.test(c.name||c.definition||''));
  if(hasFiscalInv&&hasTotalInv&&/财政[^。\n]*(?:组成部分|全部来源)[^。\n]*(?:不能|不做)[^。\n]*(?:占比|差额)/.test(text))issues.push('财政来源投入属于全部来源投入的组成部分；同期间同范围下可以计算组成占比或非财政差额，不能把二者说成不可比较。用户没有要求时只分别报告金额。');
  const detailLists=evidence.filter(e=>e.detail_summary&&e.metric_contract?.detail);
  if(detailLists.length>1&&/(?:地市[^。\n]{0,20}(?:两个|两份|两项)[^。\n]{0,12}(?:均无|都无)|(?:两个|两份|两项)[^。\n]{0,20}(?:共同|同时|均无|都无)[^。\n]{0,12}地市)/.test(text))issues.push('多份名单分别覆盖的地市数不能推出联合覆盖或共同缺失的地市数；缺少集合运算证据时仅报告各名单已验证的覆盖数。');
  const sentences=plain.split(/[。\n]/);
  if(detailLists.length>1&&contracts.some(c=>hasOverlappingDimension(c,'profession'))&&/(?:两份|两项)[^。\n]{0,35}(?:互不重叠|没有重叠|无重叠)/.test(text))issues.push('独立执行两份名单不代表单位集合互不重叠；缺少集合运算证据时，仅分别报告数量，说明同一单位可持多个专业。');

  const positiveClaim=pattern=>sentences.some(s=>pattern.test(s)&&!/(不能说|不能认为|不应|误称|错误地|并非|并不)/.test(s));
  if(contracts.some(c=>hasOverlappingDimension(c,'profession'))&&positiveClaim(/跨专业[^。]{0,12}(?:只计一次|仅计一次|不重复计)/))issues.push('跨专业分别计次，同一单位在不同专业可重复出现；不要改写为跨专业只计一次。');
  if(contracts.some(c=>hasOverlappingDimension(c,'profession'))&&positiveClaim(/两份名单[^。]{0,60}不能相减[^。]{0,20}(?:数量|单位数|差)/))issues.push('不同名单的数量可以比较计数差；计数差不等于名单集合差。重叠会影响并集去重，不会禁止比较两个计数。');
  if(contracts.some(c=>isOnTimeRateMetric(c))&&positiveClaim(/超时[^。]{0,60}(?:可|建议|通过|使用|取|查)[^。]{0,12}(?:批准率|不予批准)/))issues.push('按时办结与是否批准是独立维度；不批准原因不能用来解释超时。仅报告时效结果，归因需超时过程证据。');
  for(const m of plain.matchAll(/(\d{4})年(\d{1,2})月[^。\n]{0,40}同比\s*(\d{1,2})月/g)){
    if(m[2]!==m[3])issues.push('同一年不同月份的比较不是同比；相邻月份用环比，其他月份明确写期间对比。');
  }
  // Check explicit paired month/value statements only when the evidence has
  // one metric and one year. Ambiguous multi-metric prose is left unverified.
  const series=evidence.filter(e=>e.metric_contract&&e.facts?.length>1);
  const metricIds=new Set(series.map(e=>e.metric_contract.id));
  const facts=series.flatMap(e=>e.facts).filter(f=>/^\d{4}-\d{2}$/.test(f.dimensions?.period||''));
  if(metricIds.size===1&&new Set(facts.map(f=>f.dimensions.period.slice(0,4))).size===1){
    for(const m of plain.matchAll(/(\d{1,2})月(?:与|和|、)(\d{1,2})月(?:同为|均为|都是)\s*(\d+(?:\.\d+)?)/g)){
      if([Number(m[1]),Number(m[2])].some(month=>{
        const values=facts.filter(f=>Number(f.dimensions.period.slice(5))===month).map(f=>f.value);
        return values.length&&values.every(v=>typeof v==='number'&&Math.abs(v-Number(m[3]))>.011);
      }))issues.push('月份与数值配对错误：请引用服务端趋势结论中的最高/最低观测及月份，不重新猜测月份。');
    }
    const monthly=new Map();let ambiguous=false;
    for(const f of facts){const p=f.dimensions.period;const old=monthly.get(p);if(!Number.isFinite(f.value)||old!==undefined&&old!==f.value)ambiguous=true;monthly.set(p,f.value);}
    if(!ambiguous&&monthly.size===12){
      for(const m of text.matchAll(/(\d{1,2})月[^。\n，,、]{0,8}?(最高|最低)/g)){
        const [start,end]=extremeMonthRange(text,m.index+m[0].lastIndexOf(m[2]));
        const scoped=[...monthly].filter(([p])=>Number(p.slice(5))>=start&&Number(p.slice(5))<=end).map(([,v])=>v);
        const expected=(m[2]==='最低'?Math.min:Math.max)(...scoped),value=[...monthly].find(([p])=>Number(p.slice(5))===Number(m[1]))?.[1];
        if(value!==undefined&&Math.abs(value-expected)>.011)issues.push('月度极值月份与完整12个月事实不一致：删除错误的最高/最低月份，或引用服务端排名/趋势结论；不用重新查询。');
      }
    }
  }
  const clauses=userText.split(/[。？?；;\n]+/),fiscal=clauses.some(c=>c.includes('财政')&&(c.includes('基础测绘')||c.includes('投入'))),total=clauses.some(c=>c.includes('基础测绘')&&c.includes('投入')&&(!c.includes('财政')||c.includes('全部')));
  if(fiscal&&total&&!checkedAnnualFiscalTrend(userText,evidence)
    &&!contracts.some(c=>c.role==='total_investment'||/(?:全部|总)(?:来源)?投入/.test(c.name||c.definition||'')))issues.push('交付缺项：用户独立要求基础测绘全部投入比较，现有财政数据不能替代。按已发布定义新建总投入任务补齐用户要求的期间及比较。');
  if(contracts.some(c=>c.additive===true&&c.time_mode!=='snapshot'&&/服务(?:次数|量)/.test(c.name||c.definition||''))&&text.split(/[。；;\n]/).some(sentence=>/服务(?:次数|量)[^。\n]{0,25}(?:不能|不可)[^。\n]{0,10}(?:相加|累加)/.test(sentence)&&!/(?:用户|存量)[^。\n]{0,12}(?:不能|不可)[^。\n]{0,10}(?:相加|累加)/.test(sentence)))issues.push('天地图服务次数是期间发生量，可以跨月累加；不要与月末用户存量混淆。');
  if(contracts.some(c=>isArithmeticMeanMetric(c))&&text.split(/[。\n]/).some(s=>/多数|大部分项目|主要滞后|签认.*慢/.test(s)&&!/(不能|不代表|无法|不等于|不足以|缺少|未提供)/.test(s)))issues.push('项目算术平均不能证明多数项目完成情况，也不能证明签认/结项原因；保留已验证均值和限制。');
  if(contracts.some(c=>isArithmeticMeanMetric(c))&&text.split(/[。\n]/).some(s=>/(?:说明|证明)[^。\n]{0,35}(?:均衡|大项目|拖累|拉高)/.test(s)&&!/(不能|无法|不足以|并不)/.test(s)))issues.push('算术平均与加权平均接近不能证明项目分布均衡或大项目没有影响，需要项目级分布及权重证据；仅并列报告两种均值。');
  if(contracts.some(c=>isArithmeticMeanMetric(c))&&/(?:算术平均|平均完成率)[^。\n]{0,20}分母(?:为|是)计划工作量/.test(text))issues.push('项目算术平均是各项目完成率之和除以项目数；整体加权完成率才是实际工作量合计除以计划工作量合计。');
  if(contracts.some(c=>isProjectAcceptanceMetric(c))&&text.split(/[。\n]/).some(s=>/(?:资金|预算|支出)[^。\n]{0,25}进度[^。\n]{0,20}(?:高于|低于|快于|慢于)[^。\n]{0,20}验收/.test(s)&&!/(不能|不可|无法|不应)/.test(s)))issues.push('资金支出/预算与验收项目数占比不能解释为进度高低或快慢；分别报告指标数值与分母，不做进度比较。');
  if(contracts.some(c=>isProjectAcceptanceMetric(c))&&contracts.some(c=>isWeightedWorkCompletionMetric(c))&&positiveClaim(/(?:高于|低于)[^。]{0,30}项目[^。]{0,15}(?:数量占比|个数占比)[^。]{0,30}(?:体量|规模|权重)差异/))issues.push('工作量完成率与已验收项目占比不同，不能由两比率高低推断项目体量差异；需要项目级权重和状态证据。');
  const hasUnscopedAcceptanceStage=contracts.some(c=>isArithmeticMeanMetric(c)&&!hasAcceptanceStageScope(c));
  if((contracts.some(c=>isProjectAcceptanceMetric(c))||hasUnscopedAcceptanceStage)&&/(资金执行[^。\n]{0,20}快于验收|不足半数项目尚未|已进入验收的项目[^。\n]*平均)/.test(text)){
    const isSpeedClaim=/(资金执行[^。\n]{0,20}快于验收)/.test(text);
    const meanContract=contracts.find(c=>isArithmeticMeanMetric(c));
    const isStageClaim=/(?:不足半数项目尚未|已进入验收的项目[^。\n]*平均)/.test(text);
    if(isSpeedClaim)issues.push('不同分母的比率不能比较进度快慢；请仅并列展示各自指标与分母。');
    if(isStageClaim&&(!meanContract||!hasAcceptanceStageScope(meanContract))){
      issues.push('该均值指标未登记进入验收阶段的范围筛选；按实际登记口径说明，不自行添加未登记的阶段限制。');
    }
  }
  const summaries=evidence.flatMap(e=>e.claims||[]).filter(c=>c.operation==='trend_summary');
  if(summaries.some(c=>c.increase_maximum>20)&&/(?:均(?:为)?|都是|全部(?:为)?)[^。\n]{0,8}(?:小幅|温和)(?:正)?(?:增长|上行|上升)/.test(text))issues.push('已核算增长范围包含明显较大的反弹，不能把全部增长组描述为小幅或温和；引用完整范围并分别列出反弹月份。');
  if(summaries.some(c=>c.increase_maximum>20)&&positiveClaim(/非回落月份[^。]{0,25}(?:集中|均|都)[^。]{0,12}(?:区间|%)/))issues.push('非回落月份包含反弹月份，不可把正常爬升子集的增速范围套到全部增长月份；请引用完整增长区间，明确排除反弹月后才能描述其余月份。');
  if(contracts.some(c=>isOnTimeRateMetric(c))&&positiveClaim(/超时[^。]{0,25}原因[^。]{0,20}不予批准/))issues.push('超时原因与不予批准原因是独立业务维度，不能互相替代；未登记超时过程原因时说明证据缺失。');
  if(contracts.some(c=>hasOverlappingDimension(c,'profession'))&&positiveClaim(/各专业内部[^。]{0,20}甲[^。]{0,8}乙[^。]{0,30}(?:不可|不能)相加/))issues.push('跨专业重叠仅在登记的专业维度生效，未登记等级维度重叠；按实际登记维度说明，不自行推断未登记维度的限制。');
  if(/\d[\d,]*(?:…|\.\.\.)[^。\n]{0,8}即\s*\d/.test(text))issues.push('数值表述包含截断数字和另一个精确数字，删除不完整数值，只引用工具核算的完整金额。');
  if(/超过\s*\d+(?:\.\d+)?%[^。\n]{0,25}(?:≤|<=)/.test(plain))issues.push('严格超过阈值不包含恰好相等；环比跌幅超过T对应变化率<-T，不能写成≤。');
  for(const e of evidence){for(const claim of e.claims||[]){
    const base=claim.baseline,current=claim.current;
    if(!base||!current||typeof base.value!=='number'||!base.value||typeof current.value!=='number')continue;
    const samePeriod=base.dimensions?.period===current.dimensions?.period;
    const labelKeys=Object.keys(base.dimensions||{}).filter(k=>k!=='period'&&base.dimensions[k]!==current.dimensions?.[k]);
    if(!samePeriod||labelKeys.length!==1)continue;
    const escape=s=>String(s).replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const baselineLabel=escape(base.dimensions[labelKeys[0]]),currentLabel=escape(current.dimensions[labelKeys[0]]);
    for(const [a,b,av,bv] of [[currentLabel,baselineLabel,current.value,base.value],[baselineLabel,currentLabel,base.value,current.value]]){
      if(!bv)continue;
      const re=new RegExp(a+'[^。\\n]{0,10}(?:高于|低于|比)'+b+'[^。\\n]{0,35}差异比例(?:为)?\\s*([+−-]?\\d+(?:\\.\\d+)?)%','g');
      for(const match of plain.matchAll(re))if(Math.abs(Number(match[1].replace('−','-'))-(av-bv)/bv*100)>.02)issues.push('比较方向与差异比例不一致：'+match[0]+'。对照对象必须与计算分母一致，请直接引用完整已核算结论。');
    }
    const ratioPattern=new RegExp('相对'+baselineLabel+'(?:为|是)\\s*(\\d+(?:\\.\\d+)?)%','g');
    for(const m of plain.matchAll(ratioPattern))if(Math.abs(Number(m[1])-current.value/base.value*100)>.02)issues.push('相对比例表达混淆：本值/对照值与差额/对照值相差100个百分点，请明确分别引用本值占比和差异比例。');
    const factors=[{label:baselineLabel,value:current.value/base.value},{label:currentLabel,value:current.value?base.value/current.value:null}];
    for(const f of factors){if(f.value===null)continue;const re=new RegExp('(?:相当于|为)'+f.label+'(?:的)?(?:约)?\\s*(\\d+(?:\\.\\d+)?)\\s*倍','g');
      for(const match of plain.matchAll(re))if(Math.abs(Number(match[1])-f.value)>Math.max(.05,Math.abs(f.value)*.05))issues.push('倍数核对失败：'+match[0]+'不匹配当前值/对照值；差异比例不是倍数，使用已核算结论，不自行换算。');
    }
  }}
  const cumulative=evidence.flatMap(e=>e.claims||[]).filter(c=>Number.isFinite(c.cumulative_value));
  for(const m of plain.matchAll(/前\s*(\d+)\s*(?:个|项|类)[^。\n，,；;]{0,35}合计\s*(\d+(?:\.\d+)?)\s*(?:项次|个|件|万元|万次)/g)){
    const matches=cumulative.filter(c=>c.prefix_count===Number(m[1]));
    if(matches.length&&matches.every(c=>Math.abs(c.cumulative_value-Number(m[2]))>.011))issues.push('累计值错误：前N项合计必须引用累计占比产物的cumulative_value，不能由模型心算。');
  }
  for(const m of plain.matchAll(/合计\s*(\d+(?:\.\d+)?)\s*(?:项次|个|件|万元|万次)[^。\n]{0,12}累计占比\s*(\d+(?:\.\d+)?)%/g)){
    const matches=cumulative.filter(c=>Math.abs(c.cumulative_percent-Number(m[2]))<.011);
    if(matches.length&&matches.every(c=>Math.abs(c.cumulative_value-Number(m[1]))>.011))issues.push('累计值与累计占比不一致；引用同一累计结论中的数值与占比。');
  }
  if(positiveClaim(/由降转升|由升转降/)){
    const temporalGroups=new Map();
    for(const e of evidence)for(const f of e.facts||[]){
      const dims=f.dimensions||{},p=dims.period;if(!p)continue;
      const key=JSON.stringify([e.metric_contract?.id,Object.entries(dims).filter(([k])=>k!=='period').sort()]);
      const periods=temporalGroups.get(key)||new Set();periods.add(p);temporalGroups.set(key,periods);
    }
    if(![...temporalGroups.values()].some(p=>p.size>=3))issues.push('趋势反转缺少证据：同口径至少需要三个时点才能判断由降转升或由升转降；两期只能描述本次增减。');
  }
  const comparisonClaims=evidence.flatMap(e=>e.claims||[]).filter(c=>['compare_values','period_compare','aggregate_compare','year_over_year','month_over_month'].includes(c.operation));
  if(comparisonClaims.length&&comparisonClaims.every(c=>c.unit!=='%'&&c.baseline?.unit!=='%')&&contracts.length&&contracts.every(c=>!c.ratio&&c.unit!=='%')&&/百分点/.test(text))issues.push('金额或件数的相对变化率单位为百分比，不是百分点；百分点仅用于两个百分比的差额。');
  if(/(?:合计|汇总|聚合)[^。\n]{0,30}(?:一致|相等)[^。\n]{0,30}(?:说明|证明)[^。\n]{0,30}(?:不存在|没有)(?:漏项|重复)/.test(text))issues.push('聚合合计一致只证明本次对账一致，不能单独证明没有漏项或重复；删去超出校验范围的结论。');
  if(summaries.length===1){
    const summary=summaries[0];
    for(const m of text.matchAll(/(?:增长|增加|上升)\s*(\d+)\s*(?:组|个月)/g))if(Number(m[1])!==summary.increases)issues.push('增长组数须使用已核算trend_summary.increases，不把缺基期的月份计入。');
  }
  if(/Traceback|MALFORMED_RESPONSE|invalid arguments|missing required property/.test(text))issues.push('用户回答中只保留简明异常说明，技术诊断保留在轨迹。');
  // Audit quantitative values against typed evidence. Dates/ordinals and policy
  // quotations are outside this check; do not invent a general prose verifier.
  const typed=evidence.flatMap(e=>e.answer_contract?.statements||[]);
  if(typed.length){
    const trusted=evidence.flatMap(e=>[...(e.facts||[]).map(f=>({value:f.value,unit:f.unit})),...(e.answer_contract?.statements||[]).filter(s=>Number.isFinite(s.value))]);
    const claimTexts=evidence.flatMap(e=>(e.claims||[]).map(c=>c.verified_text||''));
    const metadata=evidence.flatMap(e=>[e.row_count,...Object.values(e.detail_summary||{}).filter(Number.isFinite),...Object.values(e.detail_summary?.distinct_counts||{}).filter(Number.isFinite),...(totalsByUnit.get(e.metric_contract?.unit)||[])]).filter(Number.isFinite);
    // 0.6.3: also accept numbers from earlier turns (the user may ask to keep them),
    // and differences, ratios, shares and top-N sums across all facts of this turn,
    // including facts from different results (甲级 27 vs 乙级 152 → 相差 125).
    const factValues=trusted.map(f=>cellNumber(f.value)).filter(Number.isFinite);
    const asRows=list=>({values:{rows:list.slice(0,50).map(v=>[v]),complete:true}});
    // Totals and top-N shares within each result; differences and ratios across all.
    const perResult=evidence.map(e=>(e.facts||[]).map(f=>cellNumber(f.value)).filter(Number.isFinite)).filter(x=>x.length);
    // Subtotals by each dimension (e.g. each month's total over categories), so that
    // differences between them (272−266 = 6, +2.26%) are recognised too.
    const subtotals=[];
    for(const e of evidence){
      const facts=(e.facts||[]).filter(f=>cellNumber(f.value)!==null);
      const fields=new Set(facts.flatMap(f=>Object.keys(f.dimensions||{})));
      for(const field of fields){const groups=new Map();for(const f of facts){const k=f.dimensions?.[field];groups.set(k,(groups.get(k)||0)+Number(f.value));}if(groups.size>1&&groups.size<facts.length)subtotals.push(...groups.values());}
    }
    const derived=factValues.length?valueNumbers([...perResult.map(asRows),asRows([...factValues,...subtotals])]):new Set();
    const prior=historyEvidence(history);
    const approvedNumbers=[...periods.numbers,...metadata,...rawValueNumbers([...evidence,...prior]),...factValues,...derived,...evidenceNumbers(prior),...claimTexts.flatMap(t=>[...t.matchAll(/-?\d[\d,]*(?:\.\d+)?/g)].map(m=>Number(m[0].replaceAll(',',''))))];
    const userPercentages=[...userText.matchAll(/(\d+(?:\.\d+)?)\s*[%％]/g)].map(m=>Number(m[1]));
    for(const m of text.matchAll(/(?<![\d-])(-?\d[\d,]*(?:\.\d+)?)\s*(万元|万次|个百分点|百分点|%|％|个(?!月)|件|项次)/g)){
      const prefix=text.slice(Math.max(0,m.index-80),m.index);
      if(/(?:已删除|无法确认|没有对应|未核实|不再陈述|原先误写)[^。；;\n]{0,70}$/.test(prefix)&&!/(?:但是|但|然而|实际)[^。；;\n]*$/.test(prefix))continue;
      const value=Number(m[1].replaceAll(',',''));
      const before=text.slice(Math.max(0,m.index-30),m.index),after=text.slice(m.index+m[0].length,m.index+m[0].length+16);
      const thresholdContext=/(?:达到|不低于|至少|阈值|目标|份额条件|累计占比条件)\s*[:：]?\s*[（(]?\s*$/.test(before)||/^\s*[)）]?\s*(?:的)?(?:预警)?(?:阈值|目标)/.test(after)||/(?:您|用户)[^。\n]{0,16}(?:给定|指定)[^。\n]{0,8}[（(]?\s*$/.test(before)||/[<>≥≤]\s*$/.test(before);
      if(userPercentages.includes(value)&&(thresholdContext||quotedUserValue(text,m.index,m[0].length,value,userText)||userThresholdValue(text,m.index,m[0].length,value,userText)))continue;
      if(m[2].includes('百分点')&&[...periods.percentages].some(n=>Math.abs(n-Math.abs(value))<=.011))continue;
      if(/^个/.test(m[2])&&requestedCount(text,m.index,m[1].length,value,userText))continue;
      // 0.6.3: a decrease may be written with a minus sign (-609.48 万次).
      if(![0,100].includes(value)&&!approvedNumbers.some(v=>Math.abs(Math.abs(v)-Math.abs(value))<=.011))issues.push('定量值缺少本轮事实或结论支持：'+m[0]+'；请直接引用结果卡中带引用的数据回答，删除无依据的额外数值。');
    }
  }
  if(evidence.some(e=>e.reason_code==='UNKNOWN'&&e.metric_contract?.ratio)&&text.split(/[。\n]/).some(s=>/(本次|当前)[^。]{0,60}完整性校验未通过/.test(s)&&!/(可能|或|不能确定|未定位)/.test(s)))issues.push('UNKNOWN不能独断归因为完整性失败：也可能是零分母、缺失或重复记录。没有专门诊断证据时说明原因未定位，不把未知当零。');
  // 0.6.2: when every empty value carries an engine-computed null_reason, stating
  // that cause (e.g. "乙市预算无记录") is supported by evidence.
  const unknownRatios=evidence.filter(e=>e.reason_code==='UNKNOWN'&&e.metric_contract?.ratio&&!(e.facts||[]).filter(f=>f.value===null).every(f=>f.null_reason));
  issues.push(...presentationIssues(text,evidence,userText));
  const unknownLabels=unknownRatios.flatMap(e=>(e.facts||[]).filter(f=>f.value===null).flatMap(f=>Object.entries(f.dimensions||{}).filter(([key,value])=>key!=='period'&&typeof value==='string'&&value).map(([,value])=>value)));
  if(unknownRatios.length&&text.split(/[。\n；;]|(?:(?<!不)但(?:是)?|然而|不过)/).some(s=>{
    const subject=/(该(?:市|行|项|结果)|(?:未知|不可计算|无法计算)[^。]{0,30}(?:因为|原因|由于|源于|是因))/.test(s)||unknownLabels.some(label=>s.includes(label));
    if(!subject)return false;
    const cause=/(?:缺少|缺失|缺乏|欠缺|没有|不存在|未提供|未找到)[^。，；]{0,12}(?:预算(?:分母|操作数|记录)?|分母|操作数)|(?:缺|无)(?:有效|实际)?(?:预算(?:分母|操作数|记录)?|分母|操作数)|(?:预算|分母|操作数)[^。，；]{0,12}(?:缺失|欠缺|不存在|未提供|为零|是零|等于零|(?:为|是|等于)\s*0(?:\.0+)?(?![\d.]))/g;
    for(const match of s.matchAll(cause)){
      // Apply caveats to this cause, not the whole sentence: “原因未定位，但
      // 该市缺预算分母” still makes an unsupported definite assertion.
      const before=s.slice(0,match.index).split(/[，,：:]/).at(-1)||'';
      const after=s.slice(match.index+match[0].length).split(/[，,：:]/)[0]||'';
      // The engine's general policy names two possible conditions; it does not
      // diagnose this city. Exempt only this exact policy phrase, so a following
      // “该市缺预算分母” is still checked even under a “口径限制” label.
      if(/^缺失值(?:与|和|或|、)零分母$/.test(match[0])&&/^(?:均|都|皆)?返回未知/.test(after))continue;
      if(/(?:可能|或许|也许|不一定|不排除|(?:不能|不可|不应|无法)(?:说|认定|认为|证明|确定|确认|解释|归因|把|将)|并非|并不|不是|不等于|不代表|未证明|未证实|未确认|尚未核实|尚未确定|未定位|如果|假如|假设|只要|当|若|原先误写|已删除)[^，,:：]{0,40}$/.test(before))continue;
      if(/可能|并非|并不|不是|不为|不能确定|无法确认|如果|假如|若/.test(match[0]))continue;
      if(/^(?:的(?:说法|解释|结论|可能性))?(?:未经核实|未被证实|尚未核实|尚未确定|不成立|是错误|没有依据|仍不确定|可能)|^(?:的)?可能性|^(?:时|的情况下)|^(?:或者|或为|或是|或零|或分母|和零分母)/.test(after))continue;
      return true;
    }
    return false;
  }))issues.push('未知比率没有证明具体原因：不能将“零分母或缺失”改写为“该市缺预算分母”或“该市预算为零”。本轮没有实际诊断证据时保留两种可能并说明原因未定位；口头声称已诊断不是证据。');
  const fundReceipts=evidence.filter(e=>e.result_ref&&isFundExecutionMetric(e.metric_contract));
  if(fundReceipts.length){
    for(const sentence of sentences){
      const subclauses=sentence.split(/(?<=[，,])|(?=但是|但|然而|不过)/);
      for(const sub of subclauses){
        const match=sub.match(/(?:已完成|已验收)项目[^。]{0,12}(?:支出|预算)/);
        if(!match)continue;
        const before=sub.slice(0,match.index);
        if(/(?:不能说|不能认为|不应|误称|错误地|并非|并不|无法证明|不限定|不限于|不能|无法|不可|未经)[^，,]{0,15}$/.test(before))continue;

        const targets=claimReceipts(sub,fundReceipts);
        const unverified=targets.find(r=>!hasCompletedStatusScope(r.metric_contract));
        if(unverified){
          issues.push('当前执行证据不能证明该资金执行率仅统计已完成或已验收项目；请按已登记并执行的范围说明，不能自行添加状态限定。');
          break;
        }
      }
    }
    if(positiveClaim(/(?:涵盖|统计|针对|代表)(?:了)?(?:全部|所有)项目/)){
      const hasFiltersOrPending=fundReceipts.some(r=>{
        const c=r.metric_contract;
        const otherFilters=Object.keys(c?.filters||{}).length>0||(Array.isArray(c?.default_filters)&&c.default_filters.length>0);
        const unconfirmed=!isContractConfirmed(c)||/待确认/.test(c?.definition_status||'');
        return otherFilters||unconfirmed;
      });
      if(hasFiltersOrPending){
        issues.push('本轮执行结果未证明覆盖全部项目。请保留计算结果，说明实际覆盖的项目范围。');
      }
    }
  }
  const decisions=evidence.flatMap(e=>e.claims||[]).filter(c=>typeof c.matched==='boolean');
  if(decisions.length===1&&!decisions[0].matched&&/(?<!未)命中[^。\n]{0,12}分支[^。\n]{0,8}(?:时)?不触发/.test(text))issues.push('本轮是未命中分支所以不触发；不要写成命中分支时不触发。保持用户指定的条件规则。');
  issues.push(...periodDeliveryIssues(text,evidence,userText));
  issues.push(...monthlyObservationIssues(text,evidence));
  issues.push(...assessedRateAnswerIssues(text,evidence));
  issues.push(...periodCountCoverageIssues(text,evidence));
  issues.push(...thresholdDeliveryIssues(text,evidence,userText));
  issues.push(...chartDeliveryIssues(evidence,userText,text));
  issues.push(...explicitChartMismatch(evidence,userText));
  if(evidence.some(e=>e.inspection_reconciliation?.status==='conflict')&&!/待复核|不一致|冲突|尚未确认/.test(text))issues.push('明细复核值与指标值表不一致；须说明正式指标待复核，不能将明细复核率说成已确认的发布指标。');
  if(evidence.some(e=>e.inspection_reconciliation?.status==='conflict')){
    if(copy.split(/[。；;\n]/).some(s=>/(?<!不)(?:一定|肯定|确定|就是|只能(?:是|来自|源于)|只可能(?:是|来自|源于))[^。；;\n]{0,80}(?:写反|算反|误用|错填|填错|混用|取错|口径错误)/.test(s)&&!/(?:不能|无法|尚未|未能|不应|并非|不是|不一定)[^。；;，,:：\n]{0,12}(?:确定|认定|判断|一定|肯定|就是|只能|只可能)/.test(s)))
      issues.push('已核实的是明细与指标值冲突，未核实具体成因；不能断言公式写反、分母误用或口径混用。原因需另有证据，直接报告冲突及待复核状态。');
    if(copy.split(/[。；;，,\n]/).some(s=>/(?:参考(?:下限|上限)口径)|(?:复核率|复核值)(?:是|为|作为|用作)(?:正式指标的)?(?:下限|上限)/.test(s)&&!/(?:不是|并非|不能|不应)/.test(s)))
      issues.push('明细复核率只是本次登记条件下的核算结果，不是正式指标的上限或下限；删除没有区间依据的上下限解释。');
  }
  return [...new Set(issues)];
}

export function recoverableQueryIssues(events,answer=''){
  const queryNames=new Set(['data_sql_query','data_metric_query','data_query','data_report','data_plan_query','data_semantic_plan','analysis_method','analysis_compute']);
  const calls=new Map(events.filter(e=>e.type==='tool/call'&&queryNames.has(e.data?.name)).map(e=>[e.data.callId,e.data]));
  const question=requestText(events);
  const catalogOnly=/(?:只|仅)(?:想|需|要)?(?:看|查看|了解|核对)[^。！？\n]{0,18}(?:指标|登记|定义)|(?:是否|有没有)(?:已)?(?:登记|发布|配置)/.test(question);
  const authorized=new Set(question&&!catalogOnly&&!isKnowledgeOnly(question)&&!isDataOnly(question)
    ?typedReceipts(events,catalogTools).flatMap(catalog=>catalog.status==='succeeded'&&catalog.readiness?.basic_query===true
      &&Array.isArray(catalog.sources)?catalog.sources.filter(source=>typeof source?.id==='string'
        &&source.query_modes?.basic_query!==false
        &&(source.query_modes?.basic_query===true
          ||Array.isArray(source.tables)&&source.tables.some(table=>typeof table?.name==='string'&&table.name.trim())))
        .map(source=>source.id):[]):[]);
  const fieldFallbacks=new Set();
  const missingDefinition=/(?:没有|尚无|未找到|查不到)(?:匹配的?|相应的?|对应的?|相关的?|已发布的?|已登记的?|预先配置的?|业务)*(?:指标(?:定义)?|口径|定义)|(?:指标|统计口径|业务口径)(?:尚未|未|没有)(?:定义|登记|配置|发布)/;
  const refusedNumbers=/(?:无法|不能|暂不能|未能|没法|暂无法)[^。！？\n]{0,28}(?:查询|计算|统计|给数|给出|提供|回答|返回)|(?:没有|暂无)(?:可用|可核实)?(?:数值|数据|查询结果)/;
  if(authorized.size===1&&answer&&/(?:统计|计算|汇总|查询|查一下|多少|数量|金额|趋势|占比|排名)/.test(question)){
    const prose=publicAnswerProse(answer);
    const sentences=prose.split('\n').filter(sentence=>sentence.trim());
    if(!hasConcreteDataGap(prose)&&!requestedUnavailableHistory(events).length
      &&sentences.some((sentence,i)=>missingDefinition.test(sentence)
        &&(refusedNumbers.test(sentence)||refusedNumbers.test(sentences[i+1]||'')))){
      fieldFallbacks.add(authorized.values().next().value);
    }
  }
  let failures=[];
  for(const e of events){
    if(e.type!=='tool/result'||e.surfaceOp&&e.surfaceOp!=='append')continue;
    const m=e.data?.message,call=calls.get(m?.source?.callId||m?.toolCallId);if(!m||!call)continue;
    const text=(m.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
    const args=toolArguments({data:call});
    const basic=call.name==='data_sql_query'||call.name==='data_query';
    const formal=basic&&!['explore','probe'].includes(args.purpose);
    // The same error code can mean there are no authorized tables. Only the
    // engine's field-query branch plus same-source catalog evidence can retry.
    if(m.isError&&call.name==='data_metric_query'&&authorized.has(args.source_id)
      &&/^(?:Error:\s*)?METRIC_NOT_REGISTERED:[^\n]*\bdata_sql_query\b/.test(text)){
      fieldFallbacks.add(args.source_id);
    }
    if(m.isError&&formal&&args.source_id
      &&!/^(?:Error:\s*)?(?:CLOSING:|QUERY_PROGRESS_LIMIT:|SQL_REPAIR_(?:LIMIT|DUPLICATE):|ARGUMENT_SCHEMA:|invalid arguments:)/.test(text)){
      fieldFallbacks.delete(args.source_id);
    }
    const missingResult=call.name.startsWith('analysis_')&&/UNKNOWN_METHOD:|结果不存在或不属于此项目/.test(text);
    const recoverable=missingResult||formulaBasisRejection.test(text)||/ARGUMENT_SCHEMA:|QUERY_CHOICE:|METRIC_REQUIRED:|METRIC_SUBJECT:|CERTIFICATE_DATE_SCOPE:|ENUM_VALUE_MISMATCH:|COMPARISON_PERIODS:|COMPARISON_BASELINE_REQUIRED:|PERIOD_GRAIN_MISMATCH:|DATE_ROLE_MISMATCH:|FORECAST_HISTORY_WINDOW:|MONTHLY_COVERAGE_UNKNOWN:|PUBLISHED_GROUPING_REQUIRED:|DIMENSION_BUCKET_CONFLICT:|FISCAL_EXECUTION_CUTOFF:|FISCAL_HISTORY_UNOBSERVED:|TIME_SCOPE_MISMATCH:|NULL_VALUE_POLICY:|SQL_CORRELATION:/.test(text);
    if(call.name==='data_metric_query'&&!recoverable&&!/CLOSING:|QUERY_PROGRESS_LIMIT:/.test(text)){
      // A corrected registered-metric invocation resolves the routing error
      // even when it truthfully reports missing coverage, denial or timeout.
      // Match the exact source and metric, never an unrelated query.
      failures=failures.filter(f=>!f.route||f.route.source!==args?.source_id||f.route.metric!==args?.metric_id);
    }
    if(m.isError){if(recoverable){
      const route=text.includes('METRIC_REQUIRED:')?text.match(/data_metric_query\(source_id="([^"]+)",\s*metric_id="([^"]+)"\)/):null;
      failures.push({text:text.slice(0,900),route:route?{source:route[1],metric:route[2]}:null});
    }continue;}
    try{
      const r=JSON.parse(text);
      if(formal&&r.result_ref&&!isExploratory(r)&&['succeeded','failed'].includes(r.status))
        fieldFallbacks.delete(r.receipt?.source_id);
      if(r.status==='succeeded'&&r.result_ref&&r.purpose!=='explore')failures=[];
    }catch{}
  }
  const issues=failures.length?['交付缺项：只读查询尚因可修复的参数或查询入口错误而未执行。分析方法或结果引用不存在时，先data_catalog，再查询取得真实result_ref；不能猜方法编号或结果引用。比较查询显式传 periods 本期数组与 comparison.baseline_periods 基期数组；修正仍失败可先查完整两期原值，再分析比较。若要求已发布指标，保留该口径使用对应工具。不要求用户恢复工具或重复确认已明确需求。最近错误：'+failures.at(-1).text]:[];
  if(fieldFallbacks.size)issues.push('交付缺项：本轮目录有同一来源的授权表可查。沿原问对象、期间和筛选核对所需字段，必要时读取表详情，使用 data_sql_query 或 data_plan_query 与 data_query 完成正式只读查询。先交付已核实的数值，再说明确切的数据缺项。');
  return issues;
}

export function clarificationDeliveryIssues(events,answer=''){
  const queryNames=new Set(['data_sql_query','data_metric_query','data_query','data_report','data_plan_query','data_semantic_plan']);
  const calls=new Map(events.filter(e=>e.type==='tool/call').map(e=>[e.data.callId,e]));
  let pending;
  for(const e of events){
    if(e.type!=='tool/result'||e.surfaceOp&&e.surfaceOp!=='append')continue;
    const m=e.data?.message,call=calls.get(m?.source?.callId||m?.toolCallId);
    if(!m||!queryNames.has(call?.data.name)||!m.isError)continue;
    const text=(m.content||[]).filter(b=>b.type==='text').map(b=>b.text).join('\n');
    if(text.includes('NEEDS_CLARIFICATION:'))pending={seq:e.seq,text:text.slice(0,900)};
  }
  const unavailable=requestedUnavailableHistory(events).length>0;
  const proseChoice=answer.split(/[。；;\n]/).some(line=>/(?:请|需要您|需要你)(?:先)?(?:确认|选择)[^。；;\n]{0,35}(?:要查询|实际|统计|期间|范围|口径)/.test(line)
    &&!(unavailable&&asksForMissingHistory(line)));
  if(!pending&&[...calls.values()].some(e=>e.data.name==='data_catalog')&&proseChoice)
    pending={seq:events[0]?.seq??0,text:'本轮答复仍请求用户选择查询范围或口径。'};
  if(!pending)return [];
  const answered=m=>{try{const r=JSON.parse((m.content||[]).filter(b=>b.type==='text').map(b=>b.text).join(''));return r.answers?.some(a=>a.selected?.length||typeof a.custom==='string'&&a.custom.trim());}catch{return false;}};
  const clarified=events.findLast(e=>e.seq>pending.seq&&e.type==='tool/result'&&!e.data?.message?.isError&&calls.get(e.data?.message?.source?.callId||e.data?.message?.toolCallId)?.data.name==='ask_user_question'&&answered(e.data.message));
  if(!clarified){
    const choice=pendingUserChoice(events);
    if(choice)return answer.includes(choice)?[]:['交付缺项：原生选项已提出，尚无用户选择。交付已核实的内容，再说明「'+choice+'」无需重复发起。'];
    return ['交付缺项：查询明确需要用户澄清，尚未完成原生提问。请调用 ask_user_question，让用户选择所需口径或范围；不要自行选择，也不要只在正文列出待确认选项后结束。最近原因：'+pending.text];
  }
  const attempted=events.some(e=>e.seq>clarified.seq&&e.type==='tool/result'&&queryNames.has(calls.get(e.data?.message?.source?.callId||e.data?.message?.toolCallId)?.data.name));
  return attempted?[]:['交付缺项：用户已通过原生提问澄清范围，请按该选择完成相应查询后再答复；不要再次要求确认同一事项。'];
}

export function requestIntegrityIssues(answer,userText){
  if(!String(userText||'').trim())return [];
  const plain=String(answer||'').replaceAll('**','');
  if(/(?:当前|本次|这次|收到的|您(?:的|发来的)?|你(?:的|发来的)?|用户)(?:输入|内容|消息|问题)[^。\n]{0,12}(?:为空|是空|空白|没有内容)|(?:没有|未)(?:收到|看到|提供)[^。\n]{0,8}(?:问题|输入|消息)|(?:发来|发送)(?:了)?(?:一条)?空(?:白)?消息/.test(plain))
    return ['原始请求读取错误：宿主保存的本轮用户问题非空。请重新读取并完成该原问，不要求用户重发，不把运行时上下文或工具空结果当成用户输入。原问：'+userText];
  return [];
}

function exhaustedQueryRepair(agent,turn,events){
  const closing=closingState(agent,turn);
  if(closing&&closing.reasonCode!=='query-progress')return true;
  return events.some(event=>event.type==='tool/result'&&event.data.message?.isError&&
    (event.data.message.content||[]).some(block=>block.type==='text'&&/^(?:Error:\s*)?SQL_REPAIR_LIMIT:/.test(block.text)))
    ||sqlRepairState(events).attempts>=3;
}

function unsupportedNoResultReply(text,events,userText){
  if(isKnowledgeOnly(userText)||isDataOnly(userText))return [];
  if(!events.some(event=>event.type==='tool/call'&&formalQueryTools.has(event.data.name)))return [];
  const genericRetry=/(?:重新|再次|再)(?:发送|发起|提交|问)(?:同样|相同|一遍|这道|此|该|原)(?:的)?(?:问题|查询)|联系(?:系统)?管理员/;
  const genericFailure=/(?:未能|无法|暂不能|暂无法)(?:成功|提供|取得|完成|生成|返回|给出|确认|查询|核实|统计)[^。；;\n]{0,38}(?:结果|数据|结论|数值|数字)|当前任务为部分完成/;
  if(!genericRetry.test(text)&&!genericFailure.test(text))return [];
  const concrete=/(?:缺少|没有|未返回|未覆盖|不包含|仅覆盖|仅查到|已核实|尚未结|尚未到期|当前仅可|超时|权限不足|无权|未授权|暂无访问权限|暂不可用|字段.{0,10}对应|匹配|原生选项|选择.{0,16}口径|口径.{0,16}不一致|从.{0,20}至.{0,20}(?:日|月|年))/;
  const specific=text.split(/[。；;\n]/).some(line=>concrete.test(line)&&!genericRetry.test(line)
    &&!/无法核实(?:查询)?结果|未能(?:查询|核实|取得)(?:任何)?(?:数据|结果)/.test(line));
  return specific&&!genericRetry.test(text)?[]:['答复缺项：说明实际核实到的范围或具体数据限制。已取得的事实先交付；用对应的时间、口径或权限说明缺项，只在存在可执行业务选择时提出一次。'];
}

const FORMULA_READ_NOTICE='正式查询已定位的公式组成量';
const FORMULA_ANSWER_ISSUE='公式分母不符：';
const basisVerb='(?:作为?|用作|算作|当作|视为|充当|替代|代替)';
const basisAggregate='(?:合计|总额|总和|总计|汇总值?)?';
const qualifiedBasis=/(?:不能|不可(?:以)?|不应(?:当)?|不得|不宜|不是|无法|无需|无须|未曾|未用|禁止|拒绝|错误地?|误|若|如果|假如|即使|即便)(?:直接|简单|擅自|自动|先|再|仅用|以|用|拿|把|将|按|采用|改用)*$/;
const basisQualifier=/(?:不能|不可|不应|不得|无法|不是|不等于|不采用|只用于|若|假如|如果|即使|误用|作为对照)/;

function chosenDenominator(field,formula){
  const matching=new RegExp('^(?:同意|确认|改用|改以|选用|使用|采用|用|以)?'+field+basisAggregate+basisVerb+
    '(?:分母|'+formula.denominator+')(?:\\(推荐\\))?[。!]?$','u');
  return formula.choices.some(choice=>matching.test(choice));
}

function statedDenominator(answer,formula){
  const clauses=answer.normalize('NFKC').replace(/[\s"'“”「」『』\x60*]/g,'').split(/[。！!?；;，,\n]+/).filter(Boolean);
  const subject='(?:分母|'+formula.denominator+')';
  for(const field of formula.alternatives){
    if(!answer.includes(field)&&!clauses.some(clause=>clause.includes(field)))continue;
    if(chosenDenominator(field,formula))continue;
    const substitution=new RegExp(field+basisAggregate+'\\(?(?:直接)?'+basisVerb+subject,'gu');
    const assignment=new RegExp(subject+'(?::|为|是|采用|取|按|用|选用)([^():]{0,48}?)'+field+
      basisAggregate+'(?=$|\\((?:作为|用作|替代|单位|元|万元|人民币)|\\))','gu');
    for(const clause of clauses){
      const active=match=>!qualifiedBasis.test(clause.slice(0,match.index))&&
        !/^(?:属于|是)(?:另一个|另一项|独立的|不同的)(?:指标|口径)/.test(clause.slice(match.index+match[0].length));
      if([...clause.matchAll(substitution)].some(active)
        ||[...clause.matchAll(assignment)].some(match=>active(match)&&!basisQualifier.test(match[1])
          &&!match[1].includes(formula.denominator)&&!/[×*+÷/]|乘以|除以/.test(match[1])))return field;
    }
  }
  return null;
}

const formulaColumnName=column=>normalizedSearch(column).replace(/(?:\([^()]*\)|[_＿](?:元|万元|亿元|%))$/,'')
  .replace(/(?:合计|总和|汇总)$/,'');
function hasFormulaColumns(result,formula){
  if(!Array.isArray(result.values?.columns))return false;
  const names=result.values.columns.map(formulaColumnName);
  return names.includes(formula.denominator)&&names.includes(formula.numerator)&&names.some(name=>/率|占比|百分比/.test(name));
}

function publishedFormulaResult(result,formula,human,sessionId){
  const lineage=result.result_lineage,project=result.business_project;
  const contract=result.answer_contract,definition=contract?.definition_evidence;
  if(result.status!=='succeeded'||!result.result_ref||result.receipt?.read_only!==true||lineage?.purpose!=='answer'
    ||lineage.request_ref!==human.data.id||lineage.session_id!==sessionId||lineage.source_id!==result.receipt.source_id
    ||contract?.result_ref!==result.result_ref||definition?.match!=='validated_sql'
    ||project?.id!==definition.project_id||project?.version!==definition.project_version
    ||!matchesPublishedDefinition(result))return false;
  return hasFormulaColumns(result,formula);
}

function sameFormulaRequest(result,human,sessionId){
  const lineage=result.result_lineage;
  return result.status==='succeeded'&&result.result_ref&&result.receipt?.read_only===true&&!result.truncated
    &&!isExploratory(result)&&result.validation?.state!=='conflict'&&lineage?.purpose==='answer'
    &&lineage.request_ref===human.data.id&&lineage.session_id===sessionId&&lineage.source_id===result.receipt.source_id;
}

function missingFormulaDenominator(formula,human,evidence,sessionId){
  return formula.missingDenominatorSource
    &&!formula.alternatives.some(field=>chosenDenominator(field,formula))
    &&!evidence.some(result=>{
      if(!sameFormulaRequest(result,human,sessionId)||result.values?.complete!==true)return false;
      const names=result.values.columns?.map(formulaColumnName)||[];
      const column=names.indexOf(formula.denominator);
      return names.includes(formula.numerator)&&column>=0
        &&result.values.rows?.some(row=>Boolean(cellNumber(row[column])));
    });
}

function unsupportedFormulaPercentage(answer,formula,userText){
  const normalized=answer.normalize('NFKC');
  return [...normalized.matchAll(/[+-]?\d[\d,]*(?:\.\d+)?\s*%/g)].find(match=>{
    const value=Number(match[0].replace(/[,%\s]/g,''));
    const before=normalized.slice(0,match.index).split(/[。！？；;\n]/).at(-1);
    const expression=before.replace(/[\s`*]/g,'');
    const explainsFormula=value===100
      &&/(?:×|\*|乘以)\s*[`*\s]*$/.test(before)
      &&expression.includes(formula.numerator)
      &&expression.includes(formula.denominator)
      &&/[÷/]|除以/.test(expression);
    return !explainsFormula&&!userThresholdValue(normalized,match.index,match[0].length,value,userText)
      &&!quotedUserValue(normalized,match.index,match[0].length,value,userText);
  })?.[0];
}

function formulaAnswerIssue(answer,formula,human,evidence,sessionId){
  if(evidence.some(result=>publishedFormulaResult(result,formula,human,sessionId)))return null;
  const other=statedDenominator(answer,formula);
  const unverified=missingFormulaDenominator(formula,human,evidence,sessionId)
    &&unsupportedFormulaPercentage(answer,formula,messageText(human));
  if(!other&&!unverified)return null;
  const detail=other?'当前回答使用了「'+other+'」。':'本轮尚无该分母的有效数值，回答给出了「'+unverified+'」。';
  return FORMULA_ANSWER_ISSUE+'原式要求「'+formula.denominator+'」，'+detail+
    '请沿原式交付经正式查询核实的分子，说明缺少的依据。比率留空，勿附加替代分母的计算或选项。';
}

function earlierFormulaReview(events){
  return events.findLast(event=>event.type==='user/message'&&event.data.source?.kind==='natural-work-answer-review'
    &&event.data.content?.some(block=>block.type==='text'&&block.text.includes(FORMULA_ANSWER_ISSUE)));
}

function rejectedFormulaRefs(events,formula,human,sessionId){
  const rejected=earlierFormulaReview(events);
  if(!rejected)return new Set();
  return new Set(typedReceipts(events.filter(event=>event.seq<rejected.seq),formalQueryTools)
    .filter(result=>result.result_lineage?.request_ref===human.data.id&&result.result_lineage.session_id===sessionId
      &&hasFormulaColumns(result,formula)&&!publishedFormulaResult(result,formula,human,sessionId))
    .map(result=>result.result_ref).filter(Boolean));
}

function copiesProbeRows(answer,probes){
  const rowKey=cells=>cells.map(cell=>String(cell??'').normalize('NFKC').replaceAll('**','').trim()
    .replace(/(?<=\d),(?=\d{3}(?:\D|$))/g,'')).join('\0');
  const printed=new Set(answer.split('\n').filter(line=>/^\s*\|.*\|\s*$/.test(line))
    .map(line=>rowKey(line.trim().slice(1,-1).split('|'))));
  return printed.size>0&&probes.some(probe=>Array.isArray(probe.values?.rows)&&probe.values.rows.some(row=>
    Array.isArray(row)&&row.some(cell=>typeof cell==='number'&&Number.isFinite(cell))&&printed.has(rowKey(row))));
}

function pendingFormulaRead(formula,events){
  if(!formula||formula.attempted)return null;
  const reviews=events.filter(event=>event.type==='user/message'&&event.data.source?.kind==='natural-work-answer-review');
  if(reviews.length>=2||reviews.some(event=>event.data.source.summary===FORMULA_READ_NOTICE))return null;
  const rejected=formula.basisRejected?'前次比例查询因实际分母与原问不符而未执行。':
    formula.questionRejected?'原公式已明确，更换分母的询问未发出。沿原式交付可核实的分子。':'';
  return rejected+'本轮授权目录已定位分子「'+formula.numerator+'」：来源 '+formula.sourceId+'，表「'+formula.table+
    '」，字段「'+formula.column+'」。沿原问对象、期间、分组和筛选，核实实际日期字段和关联后，'+
    '直接执行一次正式只读查询。使用 SQL 时设 purpose=answer。先交付正式回执中的分子。沿原公式「'+
    formula.numerator+' ÷ '+formula.denominator+' × 100%」说明剩余缺项；证据足够才计算比率。'+
    '已有探查只用于核对格式和取值，其数据不能作为正式结果。原问未指定日期字段时，使用分子事实的业务日期并说明。'+
    '若确有影响可查结果的歧义，只单独提问该项。按回执和权限如实说清仍无法查证的部分。'+
    '最终直接交付业务数值和缺项，内部查询与审查过程留在工具记录。';
}

export async function reviewCandidate({agent,turn,signal,read,answerOverride,retainedRef}){
    const events=agent.session.snapshotEvents();
    const start=events.findLast(e=>e.type==='turn/start'&&e.data.turn===turn)?.seq??0;
    const current=events.filter(e=>e.seq>=start);
    const answers=current.filter(e=>e.type==='assistant/message').map(e=>e.data.message?.content?.filter(b=>b.type==='text').map(b=>b.text).join('')||'');
    const human=events.findLast(e=>e.type==='user/message'&&e.data.source?.kind==='user');
    const formulaBasis=formulaAnswerEvidence(events,human);
    const rejected=formulaBasis?rejectedFormulaRefs(current,formulaBasis,human,agent.id):new Set();
    const evidence=typedReceipts(current).filter(receipt=>!rejected.has(receipt.result_ref));
    const budgetReferences=fiscalBudgetReferences(current);
    const preferredBudget=currentPreferredBudget(current,human,agent.id);
    const comparisonBudget=currentComparisonBudget(current,requestContext(events).users.at(-1),agent.id);
    const userText=requestText(events);
    const submitted=answerOverride??answers.at(-1)??'';
    const pendingChoice=pendingUserChoice(current);
    const shown=(pendingChoice&&submitted===pendingChoice&&verifiedPartialSections(events,agent,current))||null;
    const previous=previouslyDeliveredSections(current,shown)?shown:independentChoicePreview(current);
    const priorResult=previous;
    const answer=priorResult&&!submitted.includes(priorResult)?previous+'\n\n'+submitted:submitted;
    const currentResult=evidence.some(e=>e.result_ref);
    if(!currentResult&&!/^natural-data/.test(agent.session.header?.agentPreset||''))return null;
    const messages=events.filter(e=>e.seq<start&&e.type==='tool/result'&&!e.data.message?.isError)
      .flatMap(e=>(e.data.message?.content||[]).filter(b=>b.type==='text').map(b=>b.text));
    const receipts=historyEvidence(messages),history=[];
    // Revalidation is needed only when an answer can actually reuse prior data.
    // A fresh query cannot borrow an unrelated old number to pass validation.
    // Never cache authorization between turns: revoked results must disappear.
    const reuseRequested=COMPARE_HEADING.test(answer.replaceAll('**',''))||/保留|前面|前述|上面|上述|刚才|沿用|上次|前一次|对比|比较|相比|相差|同比|环比/.test(userText);
    const needsHistory=dataNumbers(answer).length>0&&(!currentResult||reuseRequested);
    const unique=new Map((needsHistory?receipts:[]).filter(r=>!retainedRef||r.result_ref===retainedRef)
      .map(r=>[r.result_ref,r]));
    for(const receipt of unique.values()){
      if(!read){history.push(...wellFormedEvidence([receipt]));continue;}
      try{
        const full=await read('result',realpathSync(agent.session.header.cwd),{result_ref:receipt.result_ref},'',signal,agent.id);
        if(full.status==='succeeded'&&full.result_ref===receipt.result_ref){
          history.push({...receipt,...full,values:{columns:full.columns||[],rows:(full.rows||[]).map(row=>(full.columns||[]).map(c=>row[c])),complete:!full.truncated}});
        }
      }catch(error){if(signal.aborted)throw error;}
    }
    if(retainedRef&&!history.some(result=>result.result_ref===retainedRef))
      return {issues:['此前结果在当前授权范围内无法重新读取。']};
    let issues;
    if(evidence.some(e=>e.result_ref)){
      const priorResults=COMPARE_HEADING.test(answer.replaceAll('**',''))||/保留|前面|前述/.test(userText)?history:[];
      issues=checkAnswer(answer,evidence,userText,history,priorResults,budgetReferences);
    }
    else{
      // 0.7.0：本钩子对宿主所有会话都生效；没有查询结果的回答只在问数会话里检查，其他会话模式不受影响。
      if(!/^natural-data/.test(agent.session.header?.agentPreset||''))return null;
      // 0.6.3: a follow-up answered without any query may only use numbers from
      // earlier results of this session (and what follows from them).
      const queried=receipts.length>0;
      const resumed=Boolean(continuation(messageText(events.findLast(e=>e.type==='user/message'&&e.data.source?.kind==='user'))));
      if(queried){
        issues=resumed?checkAnswer(answer,history,userText,history):historyOnlyIssues(answer,userText,history);
        // An explicit continuation can reuse the previous answer's complete monthly
        // receipt. Verify the trend as well as the reused numbers.
        if(/为准|按上述|沿用|刚才|以上月份|上述期间/.test(userText)){
          const lastTurn=events.findLast(e=>e.seq<start&&e.type==='turn/start')?.seq??start;
          const priorRefs=new Set(events.filter(e=>e.seq>=lastTurn&&e.seq<start&&e.type==='tool/result'&&!e.data.message.isError)
            .flatMap(e=>(e.data.message.content||[]).flatMap(b=>{try{return b.type==='text'?[JSON.parse(b.text).result_ref]:[];}catch{return [];}})));
          const prior=history.filter(r=>priorRefs.has(r.result_ref));
          issues.push(...monthlyTrendIssues(answer,prior),...monthlyObservationIssues(answer,prior));
        }
      }
      else{
        // 0.6.4: no data was read in this session. Numbers stated as data must come from
        // this turn's tool output (e.g. a knowledge passage) or the user's own words.
        const toolText=current.filter(e=>e.type==='tool/result'&&!e.data.message?.isError).map(e=>(e.data.message?.content||[]).map(b=>b.text||'').join('')).join('\n');
        const pseudo=[{values:{rows:[...toolText.matchAll(/-?\d[\d,]*(?:\.\d+)?/g)].map(m=>[Number(m[0].replaceAll(',',''))]).filter(r=>Number.isFinite(r[0])).slice(0,2000),complete:true}}];
        const missing=unsupportedValues(answer,pseudo,userText,[]);
        issues=missing.length?['本会话还没有查询数据，回答里的数值（'+missing.slice(0,5).join('、')+'）没有来源。需要数据就先查询；查不到就如实说明，不要估算或凭印象给出数字。']:[];
      }
      const foreign=foreignText(answer);
      if(foreign)issues.push('回答里有非中文内容（'+foreign+'），请全部改用简体中文重写。');
      const run=englishRuns(answer)[0];
      if(run)issues.push('回答里夹了英文（'+run+'），请全部改用简体中文。');
      const counted=countMismatches(answer);
      if(counted.length)issues.push('回答里的数量和列出的条目对不上（'+counted.slice(0,3).join('；')+'）。请重新数一遍，让数量和列表一致。');
      issues.push(...presentationIssues(answer,queried?history:evidence,userText));
      issues.push(...unverifiedGradeDetails(answer,userText));
      if(!resumed)issues.push(...periodDeliveryIssues(answer,history,userText));
      issues.push(...catalogSelectionIssues(answer,events,userText));
      if(budgetReferences.length)issues.push(...fiscalMeasureIssues(answer.replaceAll('**',''),[],userText,budgetReferences));
      const negativeCorrespondence=/(?:没有|无|未|不能|无法)[^。；;\n]{0,10}(?:能)?对应/.test(answer);
      if(!negativeCorrespondence&&isAmbiguousMeasure(userText)&&/(?:多个|两个|两种|多种)[^。；;\n]{0,12}(?:指标|口径)|能对应/.test(answer)&&!current.some(e=>e.type==='tool/call'&&e.data.name==='ask_user_question'))
        issues.push('交付缺项：用户说法对应多个指标，本轮尚未取得实际选择。请用ask_user_question列出目录中相关指标的中文名称和单位，确认后按所选指标及年份作答，不用多个备选口径代替确认。');
      // A corrected continuation must clear an earlier partial record too.
      // Returning here left the obsolete warning visible after a valid answer.
    }
    issues.push(...requestIntegrityIssues(answer,userText));
    if(!pendingChoice&&preferredBudget&&!describesPreferredBudget(answer,preferredBudget))
      issues.push(MISSING_BUDGET_REFERENCE);
    if(!pendingChoice&&comparisonBudget){
      const wording=comparisonBudgetWording(answer,comparisonBudget);
      if(wording.overbroad)
        issues.push('交付范围：本轮项目预算只覆盖台账登记的范围。请删除超出台账的全域预算表述。');
      if(!wording.shown)
        issues.push('交付缺项：本轮已核实发布台账的项目预算。单列台账范围、年度和预算金额，保留实际投入及其他问项的核实情况。');
    }
    if(!currentResult)issues.push(...holdingRateBoundaryIssues(answer,[],userText));
    if(!currentResult)issues.push(...forecastIssues(answer,history));
    issues.push(...coverageIssues(answer,current));
    const empty = boundedGroupedEmpty(current,userText);
    if(empty)issues.push(...groupedEmptyIssues(answer,empty.scope));
    issues.push(...unavailableDeliveryIssues(answer,current));
    issues.push(...certificateDateBoundaryIssues(answer,current));
    const basisIssue=formulaBasis&&formulaAnswerIssue(answer,formulaBasis,human,evidence,agent.id);
    if(basisIssue)issues.push(basisIssue);
    const explicitReuse=chartOnlyKind(userText)||continuation(messageText(human));
    // Filter unbound view updates before merging by result_ref. A presentation
    // receipt can replace the operation field and hide the current query card.
    const currentCards=activeEvidence(evidence.filter(item=>item.execution?.operation!=='data.visualize'
      ||explicitReuse||Boolean(human?.data.id)&&item.result_lineage?.request_ref===human.data.id));
    const queryExhausted=exhaustedQueryRepair(agent,turn,current);
    const chartIssues=missingCurrentChartIssues(currentCards,userText,answer,{queryExhausted});
    const formula=formulaRecoveryEvidence(events,human,agent.id);
    const clarification=clarificationDeliveryIssues(current,formula&&!formula.attempted?'':answer);
    const formulaRead=!queryExhausted&&!pendingChoice&&!clarification.length?pendingFormulaRead(formula,current):null;
    if(formula&&!typedReceipts(current,formalQueryTools).some(receipt=>receipt.status==='succeeded'
      &&receipt.result_ref&&!isExploratory(receipt))&&copiesProbeRows(answer,formula.probes)){
      issues.push('交付缺项：表格中的数字只来自探查，不能作为本轮统计结果，请删除该表。');
    }
    const invocationIssues=[...(queryExhausted?[]:recoverableQueryIssues(current,answer)),
      ...(pendingChoice?[]:clarification),...(queryExhausted?[]:chartIssues),...(formulaRead?[formulaRead]:[])];
    if(pendingChoice)issues.push(...clarification);
    if(queryExhausted)issues.push(...chartIssues);
    const completionBoundary=human?.data.id&&!pendingChoice&&evidence.some(receipt=>receipt.answer_contract?.work_inventory)
      &&completionInventoryAnswer(userText,typedReceipts(current,formalQueryTools),human.data.id,agent.id);
    if(completionBoundary){
      const prose=publicAnswerProse(answer).replaceAll('**','');
      if(dataNumbers(prose).some(value=>/[%％]/.test(value.text)&&!quotedUserValue(prose,value.index,value.length,Number(value.raw),userText)
        &&!userThresholdValue(prose,value.index,value.length,Number(value.raw),userText)))
        issues.push(WORK_EVIDENCE_ISSUE+'已查到的项目清单可单独交付。发布完成百分比需要同一范围的实际与计划工作量，不能拿资金指标、记录数或阈值代替。');
    }
    if(human?.data.id&&candidateMissingDelivery(answer)){
      const formal=typedReceipts(current,formalQueryTools);
      const forecastBoundary=verifiedForecastGap(userText,activeEvidence(formal),current,human.data.id,agent.id);
      if(answer!==forecastBoundary&&(answerFromReceipts(formal,activeEvidence(evidence),human.data.id,agent.id)
        ||completionBoundary)){
        issues.push('交付缺项：本轮已有成功的正式查询。先按原有条件和已核实数据给出结果，再说明仍缺的部分。无需重复执行已有查询。');
      }else if(!forecastBoundary&&!formal.some(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result))
        &&!pendingUserChoice(current)){
        issues.push(...unsupportedNoResultReply(answer,current,userText));
      }
    }
    issues.push(...invocationIssues);
    const preserve='说明实际统计方法及具体数据限制，不追加配置登记或业务确认提示。';
    return {issues,evidence,current,userText,preserve,answer,invocationIssues,queryExhausted,pendingChoice,formulaRead};
}

function fiscalBudgetReferences(events){
  const calls=new Set(events.filter(event=>event.type==='tool/call'&&event.data.name==='data_catalog').map(event=>event.data.callId).filter(Boolean));
  const references=new Map();
  for(const event of events){
    if(event.type!=='tool/result'||event.data?.message?.isError)continue;
    const message=event.data?.message;
    if(!calls.has(message?.source?.callId||message?.toolCallId))continue;
    for(const block of message.content||[]){
      if(block.type!=='text')continue;
      let catalog;try{catalog=JSON.parse(block.text);}catch{continue;}
      if(catalog?.status!=='succeeded'||!Array.isArray(catalog.sources))continue;
      for(const source of catalog.sources){
        const reference=source?.budget_reference??source?.source_context?.budget_reference;
        if(reference?.schema_version===1&&reference.kind==='budget_reference'&&reference.basis==='published_comparison_baseline'
          &&reference.requested_measure==='actual_fiscal'&&reference.source_id&&reference.source_id===source.id
          &&!reference.requested_years&&/^\d{4}$/.test(reference.year))
          references.set(source.id,reference);
      }
    }
  }
  return [...references.values()];
}

const publishedCountTools=new Set(['data_sql_query','data_query','data_visualize']);
function typedReceipts(events,toolNames,selectCall=()=>true){
  const evidence=[];
  const calls=toolNames&&new Map(events.filter(event=>event.type==='tool/call'&&toolNames.has(event.data.name)&&selectCall(event))
    .map(event=>[event.data.callId,event.data.turn]));
  for(const event of events){
    if(event.type!=='tool/result'||event.data?.message?.isError)continue;
    const callId=event.data.message?.source?.callId;
    if(calls&&(!calls.has(callId)||calls.get(callId)!==event.data.turn||(event.surfaceOp&&event.surfaceOp!=='append')))continue;
    for(const block of event.data?.message?.content||[]){
      if(block.type!=='text')continue;
      try{evidence.push(...wellFormedEvidence([JSON.parse(block.text)]));}catch{}
    }
  }
  return evidence;
}

export function retainedAnnualMonths(events,current,user,sessionId){
  if(current.some(event=>event.type==='tool/call'&&event.data.name!=='data_catalog')
    ||user.data.content?.some(block=>block.type!=='text'))return null;
  const [previous]=requestContext(events).users.slice(-2,-1);
  const period=catalogRefinementPeriod(messageText(user));
  const year=period?.match(/^(20\d{2})年(?:1月至12月)?$/)?.[1];
  if(!year||!previous?.data.id||previous.data.turn+1!==user.data.turn)return null;
  const end=events.find(event=>event.type==='turn/end'&&event.data.turn===previous.data.turn
    &&event.seq<user.seq&&event.data.reason?.kind==='completed');
  if(!end)return null;
  const prior=events.filter(event=>event.seq>previous.seq&&event.seq<end.seq);
  const choices=clarificationEvidence(prior).clarifications;
  if(choices.length!==1)return null;
  const formal=activeEvidence(typedReceipts(prior,formalQueryTools)).filter(result=>
    result.status==='succeeded'&&result.result_ref&&!isExploratory(result));
  if(formal.length!==1)return null;
  const [result]=formal;
  const lineage=result.result_lineage;
  if(result.execution?.operation!=='data.sql_query'||lineage?.purpose!=='answer'
    ||lineage.session_id!==sessionId||lineage.request_ref!==previous.data.id
    ||!lineage.config_fingerprint||lineage.source_id!==result.receipt?.source_id)return null;
  const answer=verifiedSqlAnnualRefinement(year,formal);
  if(!answer)return null;
  let resultBuild=result.runtime_contract?.build_id;
  if(!resultBuild){
    // SQL results carry their scope and query lineage. The same turn's
    // catalogue supplies the build identity for its exact executed recipe.
    const state=monthlyObservationState(previous,prior);
    const {boundary,catalog}=state?.year===year&&scopedCatalogEvidence(previous,state.after,monthlyObservationField)||{};
    const query=boundary?.query_request;
    if(query&&catalog.runtime_contract?.source==='active-engine'&&catalog.execution_scope===result.execution_scope
      &&boundary.connection_revision===(result.source_provenance?.connection_revision??0)
      &&boundary.publication.id===result.business_project?.id&&boundary.publication.version===result.business_project?.version
      &&typedReceipts(state.after,formalQueryTools,call=>{
        const args=toolArguments(call);
        return args.sql===query.sql&&args.source_id===query.source_id&&args.purpose==='answer';
      }).some(receipt=>receipt.result_ref===result.result_ref))resultBuild=catalog.runtime_contract.build_id;
    if(!resultBuild)return null;
  }
  if(!current.some(event=>event.type==='tool/call'))
    return {tool:{name:'data_catalog',args:{source_id:lineage.source_id}}};
  const catalog=typedReceipts(current,catalogTools).findLast(value=>value.status==='succeeded'
    &&value.runtime_contract?.source==='active-engine'&&value.runtime_contract.build_id===resultBuild
    &&value.execution_scope===result.execution_scope);
  const source=catalog?.sources?.find(item=>item.id===lineage.source_id);
  const publication=source?.business_project??source?.source_context?.business_project;
  const revision=source?.connection_revision??source?.source_context?.connection_revision;
  if(!source||!result.business_project?.id||publication?.id!==result.business_project.id
    ||publication.version!==result.business_project.version
    ||(revision??0)!==(result.source_provenance?.connection_revision??0))return null;
  return {text:answer.text};
}

function repeatedForecastRead(events,current,user,sessionId){
  if(current.some(event=>event.type==='tool/call')||user.data.content?.some(block=>block.type!=='text'))return null;
  const {requests,users}=requestContext(events);
  if(requests.length!==1)return null;
  const normalized=text=>text.normalize('NFKC').trim().replace(/[。!?]+$/,'');
  const question=normalized(messageText(user));
  const prior=users.slice(-5,-1).findLast(item=>normalized(messageText(item))===question
    &&item.data.turn!==user.data.turn&&item.data.content?.every(block=>block.type==='text'));
  if(!prior?.data.id)return null;
  const end=events.find(event=>event.type==='turn/end'&&event.data.turn===prior.data.turn&&event.seq<user.seq);
  if(end?.data.reason?.kind!=='completed')return null;
  const later=events.filter(event=>event.seq>prior.seq&&event.seq<user.seq);
  if(later.some(event=>event.type==='tool/call'&&event.data.name==='ask_user_question'))return null;
  const original=later.filter(event=>event.seq<end.seq);
  const deliveries=typedReceipts(original,formalQueryTools).filter(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result));
  if(deliveries.length!==1)return null;
  const [training]=deliveries;
  const lineage=training.result_lineage;
  if(training.execution?.operation!=='data.sql_query'||lineage?.session_id!==sessionId||!lineage.config_fingerprint
    ||!checkedMonthlyForecastTool(messageText(prior),deliveries,prior.data.id))return null;
  const intervening=typedReceipts(later.filter(event=>event.seq>end.seq),formalQueryTools)
    .filter(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result));
  if(intervening.some(result=>result.result_lineage?.source_id!==lineage.source_id
    ||result.result_lineage?.config_fingerprint!==lineage.config_fingerprint||result.execution_scope!==training.execution_scope))return null;
  const matches=original.filter(event=>event.type==='tool/call'&&event.data.name==='data_sql_query'
    &&typedReceipts(original,historicalSqlTools,call=>call===event).some(result=>result.result_ref===training.result_ref));
  if(matches.length!==1)return null;
  const {source_id,sql,title}=toolArguments(matches[0]);
  if(!source_id||source_id!==lineage.source_id||typeof sql!=='string'||!sql.trim()||typeof title!=='string'||!title.trim())return null;
  return {name:'data_sql_query',args:{source_id,sql,title,purpose:'answer'}};
}

function verifiedForecastGap(question,evidence,events,requestRef,sessionId){
  if(events.some(event=>event.type==='tool/call'&&event.data.name==='ask_user_question')||
    typedReceipts(events).some(result=>result?.claims?.some?.(claim=>claim.statistics?.kind==='forecast')))return null;
  const results=evidence.filter(value=>value?.result_ref&&value.status==='succeeded'&&!isExploratory(value));
  if(results.length===1){
    const stock=snapshotForecastGapAnswer(question,results[0],requestRef);
    if(stock)return stock;
  }
  const annual=verifiedClosedBudgetTrend(question,results,requestRef,sessionId)
    ||verifiedAnnualBudgetPartial(question,results)||checkedAnnualForecastGapAnswer(question,results);
  if(annual)return annual;
  const catalogs=typedReceipts(events,catalogTools);
  const inventory=typedReceipts(events,catalogTools,call=>{
    const args=toolArguments(call);
    return !args.source_id&&!args.source_offset&&!args.table_name&&!args.entry_ref&&!args.metric_id;
  });
  const reference=verifiedAnnualAmountReference(question,results,catalogs,inventory);
  if(reference)return reference;
  const answer=checkedForecastHistoriesAnswer(question,results.filter(value=>value.validation?.forecast_history?.state==='checked'));
  if(answer)return answer;
  const errors=events.filter(event=>event.type==='tool/result'&&event.data.message?.isError
    &&(!event.surfaceOp||event.surfaceOp==='append'));
  for(const event of errors.reverse()){
    const message=event.data.message;
    const call=events.find(candidate=>candidate.type==='tool/call'&&candidate.data.turn===event.data.turn
      &&candidate.seq<event.seq&&candidate.data.callId===(message.source?.callId??message.toolCallId)&&candidate.data.name==='analysis_method');
    if(!call)continue;
    let args=call.data.arguments??call.data.args;
    if(typeof args==='string'){try{args=JSON.parse(args);}catch{continue;}}
    const method=String(args?.method_id||'').trim().toLowerCase().replace(/^matrix\./,'');
    if(!/^(?:42|forecast|prediction|预测|时间序列预测|时序预测|time_series_forecast)$/.test(method))continue;
    const error=message.content?.find(block=>block.type==='text')?.text;
    if(typeof error!=='string')continue;
    const answer=annualTrendAfterForecastRejection(question,results,args,error)
      ||(results.length===1&&unavailableForecastAnswer(question,results[0],args,error));
    if(answer)return answer;
  }
  return null;
}

function verifiedSqlShareReceipts(events){
  const tools=new Set(['analysis_compute','analysis_method']);
  return events.filter(event=>event.type==='tool/call'&&tools.has(event.data.name)).flatMap(call=>{
    const args=toolArguments(call);
    const column=call.data.name==='analysis_compute'
      ?args.operation==='share'&&args.column
      :Array.isArray(args.columns)&&args.columns.length===1&&args.columns[0];
    if(typeof column!=='string')return [];
    return typedReceipts(events,tools,candidate=>candidate===call)
      .filter(receipt=>receipt.result_ref===args.result_ref).map(receipt=>({column,receipt}));
  });
}

function attemptedCountShare(events,{result_ref,column}){
  return events.some(event=>{
    if(event.type!=='tool/call'||!['analysis_compute','analysis_method'].includes(event.data.name))return false;
    const args=toolArguments(event);
    if(args.result_ref!==result_ref)return false;
    return event.data.name==='analysis_compute'?args.operation==='share'&&args.column===column
      :Array.isArray(args.columns)&&args.columns.length===1&&args.columns[0]===column;
  });
}

const formalQueryTools=new Set(['data_sql_query','data_query','data_metric_query','data_report','data_derive_aggregation']);
const catalogTools=new Set(['data_catalog']);
const historicalSqlTools=new Set(['data_sql_query']);
const historicalQuestion=/(?:20\d{2}\s*(?:年\s*(?:\d{1,2}\s*月|底|末)|[-/]\d{1,2})).{0,65}资质.{0,24}(?:证书|单位)/s;
const historicalRefinement=/^\s*(?:(?:再)?只看|仅看|改查|取消)[^\n]{1,80}$/;
const historicalCertificateShareRefinement=/^\s*(?:其中|上述(?:结果)?中|刚才的结果中).{2,70}证书.{0,24}(?:百分比|比例|比重|占比)/s;
const qualificationForecast=/^\s*(?:未来|接下来)的一年|^\s*(?:未来|接下来)(?:一|1)年/;
const qualificationStock=/^.{4,55}(?:资质(?:的(?:持证)?)?单位|持证单位)(?:数量|数|数据量)(?:会|是否会|是否|预计会)?(?:增加|减少|增长|下降|增减|变化)(?:吗|多少|百分之几)?[。？！?!]?$/;

function historicalQualificationPlan(user,events){
  const catalog=typedReceipts(events.filter(event=>event.seq>user.seq&&event.data.turn===user.data.turn),catalogTools)
    .findLast(result=>result.status==='succeeded'&&result.sources?.length===1
      &&result.catalog_view?.next_source_offset==null);
  const source=catalog?.sources[0];
  const plan=source?.historical_qualification_query;
  const query=plan?.query_request;
  const publication=source?.business_project??source?.source_context?.business_project;
  if(plan?.schema_version!==1||plan.request_ref!==user.data.id||plan.source_id!==source.id
    ||!plan.publication?.id||!Number.isSafeInteger(plan.publication?.version)
    ||(publication&&(publication.id!==plan.publication.id||publication.version!==plan.publication.version))
    ||source.dimension_business_choice||!/^\d{4}-\d{2}-\d{2}$/.test(plan.cutoff)
    ||!['certificate','unit'].includes(plan.subject)||query?.source_id!==source.id||query.purpose!=='answer'
    ||typeof query.title!=='string'||!query.title||typeof query.sql!=='string'||!query.sql)return null;
  return plan;
}

export function historicalQualificationTool(user,events,current){
  const calls=current.filter(event=>event.type==='tool/call');
  const question=messageText(user);
  if(!calls.length){
    const previous=requestContext(events).users.at(-2);
    const priorPlan=previous&&(historicalRefinement.test(question)||historicalCertificateShareRefinement.test(question))
      &&historicalQualificationPlan(previous,events);
    const completed=priorPlan&&events.some(event=>event.seq<user.seq&&event.type==='turn/end'
      &&event.data.turn===previous.data.turn&&event.data.reason?.kind==='completed');
    return historicalQuestion.test(question)||completed?{name:'data_catalog',args:{question}}:null;
  }
  if(calls.length!==1||calls[0].data.name!=='data_catalog')return null;
  const plan=historicalQualificationPlan(user,current);
  if(!plan)return null;
  const {source_id,sql,title,purpose}=plan.query_request;
  const chart_type=explicitChartType(question);
  return {name:'data_sql_query',args:{source_id,sql,title,purpose,...(chart_type?{chart_type}:{})}};
}

function historicalQualificationAnswer(user,events,session){
  const plan=historicalQualificationPlan(user,events);
  if(!plan)return null;
  const query=plan.query_request;
  const formal=typedReceipts(events,historicalSqlTools,call=>{
    const args=toolArguments(call);
    return args.source_id===query.source_id&&args.sql===query.sql&&args.purpose==='answer';
  });
  const receipt=formal.find(result=>result.result_lineage?.request_ref===user.data.id
    &&result.result_lineage?.session_id===session&&result.receipt?.source_id===query.source_id
    &&result.business_project?.id===plan.publication.id&&result.business_project?.version===plan.publication.version);
  if(!receipt)return null;
  const sections=answerFromReceipts([receipt],[receipt],user.data.id,session);
  if(sections?.length!==1)return null;
  const missingGrade=plan.share===true&&receipt.values?.rows?.length===1
    &&receipt.values.columns?.some((column,index)=>column.endsWith('占全部有效证书（%）')&&receipt.values.rows[0][index]===null);
  const detail=missingGrade?'历史证书的等级缺失或存在冲突，当前可核实证书总数，无法核实等级占比。':'';
  return withDefinitionNotice(formatReceiptDelivery(sections,detail),sections[0]);
}

function scopedCatalogEvidence(user,events,field){
  if(events.some(event=>event.type==='tool/call'&&event.data.name==='ask_user_question'))return null;
  const catalog=typedReceipts(events,catalogTools).findLast(result=>result.status==='succeeded'&&Array.isArray(result.sources));
  if(catalog?.sources.length!==1)return null;
  const [source]=catalog.sources;
  const boundary=source[field];
  const publication=source.business_project??source.source_context?.business_project;
  if(boundary?.schema_version!==1
    ||boundary.request_ref!==user.data.id||boundary.source_id!==source.id
    ||!boundary.publication?.id||!boundary.publication?.version
    ||(publication&&(boundary.publication.id!==publication.id||boundary.publication.version!==publication.version))
    ||source.dimension_business_choice||catalog.catalog_view?.next_source_offset!=null)return null;
  return {boundary,catalog,source};
}

function scopedCatalogBoundary(user,events,field){
  if(typedReceipts(events,formalQueryTools).some(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result)))return null;
  return scopedCatalogEvidence(user,events,field)?.boundary;
}

function historicalCertificateBoundaryAnswer(user,events){
  const boundary=scopedCatalogBoundary(user,events,'historical_certificate_boundary');
  if(boundary?.state!=='unverified_historical_certificate'
    ||boundary.evidence?.kind!=='no_authorized_historical_certificate_path'
    ||boundary.evidence.current_snapshot_only!==true||boundary.evidence.inventory_checked!==true
    ||typeof boundary.subject_label!=='string'||!/^\p{Script=Han}{2,30}证书$/u.test(boundary.subject_label)
    ||typeof boundary.scope!=='string'||!/^\p{Script=Han}{2,16}$/u.test(boundary.scope)
    ||!/^20\d{2}-\d{2}-\d{2}$/.test(boundary.cutoff)||!Array.isArray(boundary.grades)
    ||boundary.grades.length>8||boundary.grades.some(label=>!/^\p{Script=Han}{1,8}$/u.test(label)))return null;
  const grades=boundary.grades.length?'及'+boundary.grades.join('、')+'的数量':'';
  return '截至 '+boundary.cutoff+'，当前授权数据无法核实'+boundary.scope+'有效'+boundary.subject_label+'总数'+grades+'。'
    +'当前快照可以核对现有单位。回溯证书数量需要该时点同口径的证书快照，'
    +'或同范围的证书标识、等级、发证日期、有效期和注销记录。';
}

function annualForecastBoundaryAnswer(user,events){
  const boundary=scopedCatalogBoundary(user,events,'annual_forecast_boundary');
  if(boundary?.kind!=='annual_forecast_boundary'||!Number.isSafeInteger(boundary.connection_revision))return null;
  const {target_period:target,start_year:first,end_year:last,current_year:current,min_years:minimum,
    verified_not_closed_years:open}=boundary;
  if(![first,last,current,minimum].every(Number.isSafeInteger)||first<1900||last<first||last-first>40
    ||current<1900||minimum<2||minimum>20||target!==String(last+1)||!Array.isArray(open))return null;
  const expected=Array.from({length:Math.max(0,last-Math.max(first,current)+1)},(_,index)=>Math.max(first,current)+index);
  if(open.length!==expected.length||open.some((year,index)=>year!==expected[index]))return null;
  const ended=Math.min(last,current-1),available=Math.max(0,ended-first+1);
  if(available>=minimum&&!open.length)return null;
  const range=available?first+' 年至 '+ended+' 年共 '+available+' 个自然年度已结束。':'所选期间没有已结束的自然年度。';
  const unfinished=open.length?(open.length===1?open[0]+' 年':open[0]+' 年至 '+open.at(-1)+' 年')+'尚未结束。':'';
  return range+unfinished+'年度预测需要 '+minimum+' 个连续、同口径的完整年度数据。'
    +(available<minimum?'所选范围最多能提供 '+available+' 个完整年度，暂无法给出 '+target+' 年全年预测值。'
      :'所选范围包含未结束的年度，暂无法给出 '+target+' 年全年预测值。');
}

function eventDimensionEvidence(user,events){
  const {boundary,catalog,source}=scopedCatalogEvidence(user,events,'event_dimension_boundary')||{};
  if(boundary?.state!=='unverified_event_dimension'||boundary.evidence?.kind!=='no_registered_event_dimension_path'
    ||boundary.evidence.same_table!==false||boundary.evidence.registered_relation!==false
    ||!Array.of(boundary.event,boundary.dimension).every(label=>typeof label==='string'&&/^[\p{Script=Han}]{2,20}$/u.test(label)))return null;
  const {query_request:query,count_column:column,unit,year}=boundary.event_total||{};
  const revision=source.connection_revision??source.source_context?.connection_revision??0;
  const usable=catalog.execution?.operation==='data.catalog'&&catalog.project&&catalog.project===catalog.execution_scope
    &&Number.isSafeInteger(boundary.connection_revision)&&boundary.connection_revision===revision
    &&Number.isSafeInteger(year)&&year>=1900&&year<9999&&/^(?:件|条|笔|次|宗|单)$/.test(unit||'')
    &&typeof column==='string'&&column.normalize('NFKC').endsWith('('+unit+')')
    &&query?.source_id===boundary.source_id&&query.purpose==='answer'
    &&typeof query.sql==='string'&&query.sql.trim()&&typeof query.title==='string'&&query.title.trim();
  return {boundary,query:usable?query:null,scope:catalog.execution_scope,column,unit,year};
}

function eventDimensionTool(user,events){
  if(events.some(event=>event.type==='tool/call'&&formalQueryTools.has(event.data.name)))return null;
  const query=eventDimensionEvidence(user,events)?.query;
  return query?{name:'data_sql_query',args:query}:null;
}

function eventDimensionAnswer(user,events,session){
  const evidence=eventDimensionEvidence(user,events);
  if(!evidence)return null;
  const {boundary,query,scope,column,unit,year}=evidence;
  const gap='缺少“'+boundary.event+'”记录与事件发生时“'+boundary.dimension+'”的对应关系，暂无法按该维度排行。';
  const completed=results=>results.filter(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result));
  const formal=completed(typedReceipts(events,formalQueryTools));
  if(!query)return formal.length?null:'当前数据包含“'+boundary.event+'”记录。'+gap;
  const totals=completed(typedReceipts(events,historicalSqlTools,call=>{
    const args=toolArguments(call);
    return args.source_id===query.source_id&&args.sql===query.sql&&args.purpose==='answer';
  }));
  if(formal.length!==1||totals.length!==1||formal[0].result_ref!==totals[0].result_ref)return null;
  const [result]=totals;
  const {values,result_lineage:lineage,business_project:published}=result;
  const count=cellNumber(values?.rows?.[0]?.[0]);
  if(result.execution?.operation!=='data.sql_query'||result.execution_scope!==scope
    ||!session||lineage?.session_id!==session||lineage.request_ref!==boundary.request_ref||lineage.purpose!=='answer'
    ||lineage.source_id!==boundary.source_id||result.receipt?.read_only!==true||result.receipt.source_id!==boundary.source_id
    ||published?.id!==boundary.publication.id||published.version!==boundary.publication.version
    ||(result.source_provenance?.connection_revision??0)!==boundary.connection_revision
    ||result.validation?.state==='conflict'||result.truncated||result.row_count!==1||values?.complete!==true
    ||values.row_count!==1||values.columns?.length!==1||values.columns[0]!==column
    ||values.rows?.length!==1||values.rows[0]?.length!==1||result.analysis_options?.column_units?.[column]!==unit
    ||!Number.isSafeInteger(count)||count<0)return null;
  return year+' 年共查到 **'+count+' '+unit+'**“'+boundary.event+'”记录。\n\n'+gap;
}

function snapshotForecastEvidence(user,events){
  const evidence=scopedCatalogEvidence(user,events,'snapshot_forecast_boundary');
  const {boundary,catalog,source}=evidence||{};
  if(boundary?.state!=='unverified_snapshot_history'||boundary.evidence?.kind!=='no_registered_comparable_snapshot_history'
    ||!/^[\p{Script=Han}]{2,36}$/u.test(boundary.subject||'')
    ||!/^[\p{Script=Han}]{2,12}$/u.test(boundary.current_snapshot?.scope||'')
    ||!/^(?:未来|接下来)的?(?:一|1)年(?:内)?$/.test(boundary.window?.text||''))return null;
  const {query_request:query,count_column:column,unit}=boundary.current_snapshot;
  const revision=source.connection_revision??source.source_context?.connection_revision??0;
  const usable=catalog.execution?.operation==='data.catalog'&&catalog.project&&catalog.project===catalog.execution_scope
    &&Number.isSafeInteger(boundary.connection_revision)&&boundary.connection_revision===revision
    &&query?.source_id===boundary.source_id&&query.purpose==='answer'
    &&typeof query.sql==='string'&&query.sql.trim()&&typeof query.title==='string'&&query.title.trim()
    &&/^(?:家|户|个)$/.test(unit||'')&&typeof column==='string'&&column.normalize('NFKC').endsWith('('+unit+')');
  return {boundary,query:usable?query:null,scope:catalog.execution_scope,column,unit};
}

function snapshotForecastTool(user,events){
  if(events.some(event=>event.type==='tool/call'&&formalQueryTools.has(event.data.name)))return null;
  const query=snapshotForecastEvidence(user,events)?.query;
  return query?{name:'data_sql_query',args:query}:null;
}

function snapshotHistoryTool(user,events){
  if(events.some(event=>event.type==='tool/call'&&event.data.name!=='data_catalog'))return null;
  const {boundary,catalog,source}=scopedCatalogEvidence(user,events,'snapshot_history_query')||{};
  const args=boundary?.query_request;
  if(!args||boundary.quote!==messageText(user)||catalog.execution?.operation!=='data.catalog'
    ||!catalog.execution_scope||catalog.project!==catalog.execution_scope||args.source_id!==source.id
    ||boundary.connection_revision!==(source.connection_revision??source.source_context?.connection_revision??0)
    ||typeof args.metric_id!=='string'||typeof args.metric_version!=='string'||typeof args.title!=='string'
    ||!Array.isArray(args.periods)||args.periods.length<2||args.periods.length>60
    ||args.periods.some(period=>!/^\d{4}-(?:0[1-9]|1[0-2])$/.test(period)))return null;
  return {name:'data_metric_query',args};
}

function publishedCatalogQueryTool(user,events,field){
  if(events.some(event=>event.type==='tool/call'&&formalQueryTools.has(event.data.name)))return null;
  const {boundary,catalog,source}=scopedCatalogEvidence(user,events,field)||{};
  const query=boundary?.query_request;
  if(!query||boundary.quote!==messageText(user)||catalog.execution?.operation!=='data.catalog'
    ||!catalog.execution_scope||catalog.project!==catalog.execution_scope||query.source_id!==source.id
    ||query.purpose!=='answer'||boundary.connection_revision!==(source.connection_revision??source.source_context?.connection_revision??0)
    ||typeof query.sql!=='string'||!query.sql.trim()||typeof query.title!=='string'||!query.title.trim())return null;
  return {name:'data_sql_query',args:query};
}

function annualHistoryProviderTools(user,events,tools){
  if(!tools?.some(tool=>tool.name==='data_catalog')||!tools.some(tool=>tool.name==='data_sql_query')
    ||events.some(event=>event.type==='tool/result'&&event.data.message?.isError))return null;
  const {boundary,catalog,source}=scopedCatalogEvidence(user,events,'annual_fiscal_history_query')||{};
  if(boundary?.scope!=='requires_subject_evidence'||boundary.inline_catalog_complete!==true
    ||boundary.quote!==messageText(user)||boundary.candidate_query?.source_id!==source.id
    ||boundary.connection_revision!==(source.connection_revision??source.source_context?.connection_revision??0)
    ||catalog.execution?.operation!=='data.catalog'||catalog.runtime_contract?.source!=='active-engine'
    ||!catalog.execution_scope||catalog.execution_scope!==catalog.project
    ||source.tables?.length!==1||source.tables[0].name!==boundary.table
    ||!source.tables[0].columns?.length||source.catalog_entries?.length!==0)return null;
  return tools.filter(tool=>tool.name!=='data_catalog');
}

const monthlyObservationField='monthly_observation_period';

function monthlyObservationState(user,events){
  const asked=events.filter(event=>event.type==='tool/call'&&event.data.name==='ask_user_question');
  if(asked.length>1)return null;
  const [call]=asked;
  const before=call?events.filter(event=>event.seq<call.seq):events;
  const {boundary,catalog}=scopedCatalogEvidence(user,before,monthlyObservationField)||{};
  const question=boundary?.question;
  if(catalog?.execution?.operation!=='data.catalog'||!catalog.execution_scope||catalog.project!==catalog.execution_scope
    ||boundary.quote!==messageText(user)||question?.id!=='monthly-observation-period'
    ||!Array.isArray(question.options)||question.options.length!==2
    ||!question.options.every(option=>/^20\d{2}年1月至12月$/.test(option.label)))return null;
  if(!call)return before.some(event=>event.type==='tool/call'&&formalQueryTools.has(event.data.name))?null:{question};
  const [choice]=clarificationEvidence(events,user.seq).clarifications;
  if(choice?.call_id!==call.data.callId||choice.question_id!==question.id||choice.question!==question.question
     ||choice.selected_labels.length!==1||choice.answer!==choice.selected_labels[0]
     ||!question.options.some(option=>option.label===choice.answer))return null;
  const settled=events.find(event=>event.type==='tool/result'&&event.data.message?.source?.callId===call.data.callId
    &&!event.data.message.isError);
  return settled?{after:events.filter(event=>event.seq>settled.seq),year:choice.answer.slice(0,4)}:null;
}

export function monthlyObservationPeriodTool(user,events,tools){
  const state=monthlyObservationState(user,events);
  if(!state)return null;
  if(state.question)return tools.some(tool=>tool.name==='ask_user_question')
    ?{name:'ask_user_question',args:{questions:[state.question]}}:null;
  if(!state.after.some(event=>event.type==='tool/call'))return tools.some(tool=>tool.name==='data_catalog')
    ?{name:'data_catalog',args:{question:messageText(user)}}:null;
  return tools.some(tool=>tool.name==='data_sql_query')?publishedCatalogQueryTool(user,state.after,monthlyObservationField):null;
}

function snapshotForecastAnswer(user,events,session){
  const evidence=snapshotForecastEvidence(user,events);
  if(!evidence)return null;
  const {boundary,query,scope,column,unit}=evidence;
  const completed=results=>results.filter(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result));
  const formal=completed(typedReceipts(events,formalQueryTools));
  const lookedUp=query?completed(typedReceipts(events,historicalSqlTools,call=>{
    const args=toolArguments(call);
    return args.source_id===query.source_id&&args.sql===query.sql&&args.purpose==='answer';
  })):[];
  if(formal.some(result=>!lookedUp.some(lookup=>lookup.result_ref===result.result_ref)))return null;
  const snapshot=boundary.current_snapshot.scope;
  const receipt=lookedUp.length===1?lookedUp[0]:null;
  const values=receipt?.values,lineage=receipt?.result_lineage,published=receipt?.business_project;
  const count=values?.rows?.[0]?.[0];
  if(receipt&&session&&receipt.execution?.operation==='data.sql_query'&&receipt.execution_scope===scope
    &&lineage?.session_id===session&&lineage?.request_ref===boundary.request_ref&&lineage.purpose==='answer'
    &&lineage.source_id===boundary.source_id&&receipt.receipt?.read_only===true&&receipt.receipt.source_id===boundary.source_id
    &&published?.id===boundary.publication.id&&published.version===boundary.publication.version
    &&(receipt.source_provenance?.connection_revision??0)===boundary.connection_revision
    &&receipt.validation?.state!=='conflict'&&!receipt.truncated
    &&receipt.row_count===1&&values?.complete===true&&values.row_count===1&&values.columns?.length===1&&values.columns[0]===column
    &&values.rows?.length===1&&values.rows[0]?.length===1&&receipt.analysis_options?.column_units?.[column]===unit
    &&Number.isSafeInteger(count)&&count>=0){
    return '暂无法判断'+boundary.subject+'在'+boundary.window.text+'是否会增减。'
      +'现有快照缺少同地区、等级和专业口径的连续历史存量，申请或发证日期无法还原各期存量。\n\n'
      +snapshot+'当前快照中，'+boundary.subject+'有 **'+count+' '+unit+'**。';
  }
  return '目前无法判断'+boundary.subject+'数量在'+boundary.window.text+'是否会增减。'
    +'当前数据可查询'+snapshot+'的现有持证单位，'
    +'但缺少同一地区、等级和专业口径的连续历史存量。补齐各期存量后才能判断变化趋势。';
}
const normalizedSearch=value=>String(value||'').normalize('NFKC').replace(/\s/g,'');
function toolArguments(call){
  const args=call?.data?.arguments??call?.data?.args;
  try{return (typeof args==='string'?JSON.parse(args):args)||{};}catch{return {};}
}

const dimensionSqlError=/^(?:Error:\s*)?NEEDS_CLARIFICATION:[^\n]*\bdimension_business_choice\b[^\n]*\bbiz-dim-[0-9a-f]{16}\b/;

function dimensionBusinessCatalogTool(user,events){
  const current=events.filter(event=>event.seq>user.seq&&event.data?.turn===user.data.turn
    &&(!event.surfaceOp||event.surfaceOp==='append'));
  const calls=current.filter(event=>event.type==='tool/call');
  if(calls.some(event=>['data_catalog','ask_user_question'].includes(event.data.name)))return null;
  const queries=new Map(calls.filter(event=>event.data.name==='data_sql_query'&&event.data.callId)
    .map(event=>[event.data.callId,event]));
  const sources=new Set();
  for(const event of current){
    if(event.type!=='tool/result')continue;
    const message=event.data.message;
    const query=queries.get(message?.source?.callId);
    if(!query||query.seq>=event.seq||message.source.kind!=='tool'||message.isError!==true
      ||!message.content?.some(block=>block.type==='text'&&dimensionSqlError.test(block.text)))continue;
    const {source_id,sql,purpose}=toolArguments(query);
    if((purpose==null||purpose==='answer')&&typeof source_id==='string'&&source_id.trim()
      &&typeof sql==='string'&&sql.trim())sources.add(source_id);
  }
  const question=messageText(user);
  const [source_id]=sources;
  return sources.size===1&&question.trim()?{name:'data_catalog',args:{source_id,question}}:null;
}

function pendingUserChoice(events){
  const questions=events.filter(event=>event.type==='tool/call'&&event.data.name==='ask_user_question')
    .flatMap(event=>Array.isArray(toolArguments(event).questions)?toolArguments(event).questions:[]);
  if(!questions.length)return null;
  for(const question of questions){
    const state=unsettledQuestionRepeat(events,[question]);
    if(!state)continue;
    const label=String(question.question||'').match(/[「“]([^」”]{2,35})[」”]/)?.[1];
    const topic=label&&/^[\p{Script=Han}\d年月至、（） ()]{2,35}$/u.test(label)?'“'+label+'”':'其余统计口径';
    if(state==='unavailable')return topic+'的选项暂时无法显示，仍需确认口径。';
    return topic+'尚未选定，待确认口径后可继续核算。';
  }
  return null;
}

function independentChoicePreview(events,selectedCallId){
  const nativeIds=new Set();
  if(selectedCallId)nativeIds.add(selectedCallId);
  else{
    const request=events.findLast(event=>event.type==='user/message'&&event.data.source?.kind==='user');
    const choice=independentComparisonPlan(typedReceipts(events,catalogTools),request?.data.id)?.choice;
    if(!choice)return null;
    for(const event of events)if(event.type==='tool/call'&&event.data.name==='ask_user_question'
      &&toolArguments(event).questions?.some?.(question=>question.id===choice.id&&question.question===choice.question)){
      nativeIds.add(event.data.callId);
    }
  }
  return events.findLast(event=>event.type==='assistant/message'&&event.data.message?.content?.some(block=>
    block.type==='tool-call'&&block.name==='ask_user_question'&&nativeIds.has(block.id)))
    ?.data.message.content.find(block=>block.type==='text')?.text;
}

function withReceiptScopeNotice(text,section){
  const unknownScope=section.result.validation?.checks?.some(check=>check.state==='unknown'&&check.area==='scope');
  return unknownScope&&!/(?:范围|覆盖|去重|总体).{0,18}(?:未知|未核实|无法核实|尚未确认)/.test(text)
    ?text+'\n\n以上是本次筛选范围内的结果，整体覆盖范围尚未核实。':text;
}

function receiptSectionText(section){
  return withReceiptScopeNotice(withDefinitionNotice(formatReceiptDelivery([section]),section),section);
}

function formulaComponentPlan(catalogs,user){
  const catalog=catalogs.findLast(result=>result.status==='succeeded'&&result.execution?.operation==='data.catalog'
    &&result.runtime_contract?.source==='active-engine'&&result.execution_scope&&result.execution_scope===result.project
    &&result.sources?.length===1&&result.catalog_view?.next_source_offset==null);
  const source=catalog?.sources[0], plan=source?.formula_component_query, query=plan?.query_request;
  const project=source?.business_project??source?.source_context?.business_project;
  if(plan?.schema_version!==1||plan.request_ref!==user.data.id||plan.source_id!==source.id
    ||!project||plan.publication?.id!==project.id||plan.publication?.version!==project.version
    ||source.dimension_business_choice||query?.source_id!==source.id||query.purpose!=='answer'
    ||typeof query.sql!=='string'||!query.sql||typeof query.title!=='string'||!query.title
    ||!Number.isInteger(plan.year)||!plan.date_label||!plan.numerator||!plan.denominator)return null;
  return {...plan,scope:catalog.execution_scope};
}

function formulaComponentDelivery(events,user,sessionId,receipts,evidence){
  const terms=formulaAnswerEvidence(events,user);
  if(!terms)return null;
  const current=events.filter(event=>event.seq>user.seq&&event.data.turn===user.data.turn);
  const plan=formulaComponentPlan(typedReceipts(current,catalogTools),user);
  const planned=new Set(plan?typedReceipts(current,historicalSqlTools,call=>{
    const args=toolArguments(call);
    return args.sql===plan.query_request.sql&&args.source_id===plan.source_id&&args.purpose==='answer';
  }).filter(result=>sameFormulaRequest(result,user,sessionId)&&result.execution_scope===plan.scope
    &&result.business_project?.id===plan.publication.id
    &&result.business_project?.version===plan.publication.version).map(result=>result.result_ref):[]);
  // The engine checks the full authorized inventory before issuing the plan.
  // The compact catalog sent to the model may omit its table descriptions.
  const boundPlan=planned.size===1&&plan.numerator===terms.numerator&&plan.denominator===terms.denominator;
  const formula=boundPlan?{...terms,missingDenominatorSource:plan.source_id}:terms;
  if(!missingFormulaDenominator(formula,user,evidence,sessionId))return null;
  const direct=activeEvidence(receipts).filter(result=>sameFormulaRequest(result,user,sessionId)
    &&result.receipt.source_id===formula.missingDenominatorSource&&result.values?.complete===true
    &&(!boundPlan||planned.has(result.result_ref))
    &&Array.isArray(result.values.columns)&&Array.isArray(result.values.rows)
    &&result.values.rows.length>0&&result.values.rows.length===result.row_count
    &&!result.values.columns.some(column=>formulaColumnName(column)===formula.denominator));
  if(direct.length!==1)return null;
  const [result]=direct;
  const {columns,rows}=result.values;
  const measures=columns.flatMap((column,index)=>formulaColumnName(column)===formula.numerator?[index]:[]);
  const [measure]=measures;
  if(measures.length!==1||!rows.some(row=>cellNumber(row[measure])!==null))return null;
  const groups=columns.flatMap((column,index)=>index!==measure&&rows.every(row=>
    typeof row[index]==='string'&&cellNumber(row[index])===null)?[index]:[]);
  if(new Set(rows.map(row=>JSON.stringify(groups.map(index=>row[index])))).size!==rows.length)return null;
  const indices=[...groups,measure];
  const name=columns[measure];
  const amountUnit=result.analysis_options?.column_units?.[name]||name.match(/[_＿（(](亿元|万元|元)[）)]?$/)?.[1];
  const selected={...result,title:formula.numerator,claims:[],verified_claims:[],
    values:{...result.values,columns:[...groups.map(index=>columns[index]),formula.numerator],rows:rows.map(row=>
      indices.map(index=>index===measure?cellNumber(row[index]):row[index]))},
    analysis_options:{...result.analysis_options,column_units:{...result.analysis_options?.column_units,[formula.numerator]:amountUnit}}};
  const sections=answerFromReceipts([selected],[selected],user.data.id,sessionId);
  if(!sections)return null;
  const basis=boundPlan
    ?'按'+plan.year+'年可识别的'+plan.date_label+'汇总已记录'+formula.numerator+'，未记录的金额未计入。\n\n':'';
  const missing=basis+'当前数据缺少同一统计范围内的'+formula.denominator+'，无法计算所问比率。计算式为 '+formula.numerator
    +' ÷ '+formula.denominator+' × 100%。';
  return {text:formatReceiptDelivery(sections,missing),unresolved:false};
}

const MISSING_BUDGET_REFERENCE='交付缺项：本轮已查到首选年份的项目预算。直接给出该预算金额，单独说明实际投入的核实情况。';

function currentPreferredBudget(current,user,session){
  const plan=preferredBudgetPlan(typedReceipts(current,catalogTools),user);
  return preferredBudgetObservation(plan,typedReceipts(current,formalQueryTools,
    call=>preferredBudgetCall(plan,call,user)),user?.data.id,session);
}

function currentComparisonBudget(current,user,session){
  const plan=comparisonBudgetPlan(typedReceipts(current,catalogTools),user);
  if(!comparisonBudgetNeeded(plan,typedReceipts(current,formalQueryTools),user?.data.id,session))return null;
  return preferredBudgetObservation(plan,typedReceipts(current,formalQueryTools,
    call=>preferredBudgetCall(plan,call,user)),user.data.id,session);
}

function comparisonCoverageBuild(observation,current,user){
  const {plan}=observation;
  const receipts=typedReceipts(current,formalQueryTools,call=>comparisonReportCoverageCall(plan,call,user));
  if(!unobservedComparisonReports(observation,receipts))return null;
  const catalog=typedReceipts(current,catalogTools).findLast(value=>value.catalog_ref===plan.catalog_ref
    &&value.runtime_contract?.source==='active-engine');
  const buildId=catalog?.runtime_contract.build_id;
  return comparisonDiscoveryCovered(plan,current,buildId,{reportCoverageVerified:true})?buildId:null;
}

function comparisonHistoryExhausted(events,sourceId){
  const requests=new Set(events.filter(event=>{
    if(event.type!=='tool/call'||event.data.name!=='data_sql_query')return false;
    const args=toolArguments(event);
    return args.source_id===sourceId&&(!args.purpose||args.purpose==='answer');
  }).map(event=>event.data.callId));
  const failures=new Set(events.filter(event=>{
    if(event.type!=='tool/result'||event.surfaceOp&&event.surfaceOp!=='append')return false;
    const reply=event.data.message;
    return reply?.isError&&requests.has(reply.source?.callId)&&reply.content?.some(block=>
      block.type==='text'&&/^(?:Error:\s*)?FISCAL_HISTORY_UNOBSERVED:/.test(block.text));
  }).map(event=>event.data.message.source.callId));
  return failures.size>=3;
}

function comparisonRepairsBlocked(calls,events,current,plan){
  const builds=new Set(typedReceipts(current).filter(receipt=>receipt.runtime_contract?.source==='active-engine')
    .map(receipt=>receipt.runtime_contract.build_id));
  const [buildId]=builds;
  if(builds.size!==1||typeof buildId!=='string'||!buildId||!comparisonDiscoveryCovered(plan,current,buildId))return false;
  return calls.every(call=>{
    const args=toolArguments({data:call});
    if(args.source_id!==plan.source_id||(args.purpose??'answer')!=='answer'
      ||typeof args.sql!=='string'||!args.sql.trim()||args.registered_query||args.replaces_result_ref
      ||args.task_id||args.task_revision!=null||args.scope_proposal!=null)return false;
    try{
      const normalized=normalizeDataToolArgs('sql_query',args,events);
      assertSqlRepairBudget('sql_query',normalized,events,{buildId,originalSourceId:args.source_id});
    }catch(error){
      return /^SQL_REPAIR_LIMIT:/.test(error?.message);
    }
    return false;
  });
}

const WORK_EVIDENCE_ISSUE='工作量证据缺项：';
function reportedSpendingDelivery(receipts,request,session) {
  const reports=receipts.map(result=>reportedSpendingObservation(result,request,session)).filter(item=>item?.hasAmount);
  if(reports.length!==1)return null;
  const [report]=reports;
  const rates=receipts.map(result=>assessedRateObservation(result,request,session)).filter(Boolean);
  const paragraphs=[reportedSpendingText(report)];
  let unresolved=!report.complete;
  for(const clause of report.proof.additional_questions){
    const matches=rates.filter(rate=>matchesAssessmentClause(rate,clause));
    if(matches.length===1)paragraphs.push(assessedRateText(matches[0]));
    else{
      paragraphs.push(`「${clause}」本轮未取得可计算的结果。`);
      unresolved=true;
    }
  }
  return {text:paragraphs.join('\n\n'),unresolved};
}

function receiptDelivery(events,agent,question,current,failure,candidate){
  const user=requestContext(events).users.at(-1);
  if(!user?.data.id)return null;
  const receipts=typedReceipts(current,formalQueryTools);
  const evidence=activeEvidence(typedReceipts(current));
  if(!pendingUserChoice(current)&&(failure?.issues?.length||candidateMissingDelivery(candidate))){
    const spending=reportedSpendingDelivery(activeEvidence(receipts),user.data.id,agent.id);
    if(spending&&checkAnswer(spending.text,evidence,question).every(issue=>/^交付缺项：/.test(issue)))
      return {...spending,verifiedObservation:true};
    const rates=evidence.map(result=>assessedRateObservation(result,user.data.id,agent.id))
      .filter(rate=>rate&&matchesAssessmentClause(rate,question));
    if(rates.length===1){
      const text=assessedRateText(rates[0]);
      if(!checkAnswer(text,evidence,question).length)return {text,unresolved:false,verifiedObservation:true};
    }
  }
  if(!pendingUserChoice(current)&&failure?.issues?.includes(MISSING_BUDGET_REFERENCE)){
    const preferred=currentPreferredBudget(current,user,agent.id);
    const text=preferred&&preferredBudgetPartial(preferred,receipts.filter(result=>!isExploratory(result)));
    if(text&&!checkAnswer(text,[preferred.result],question).length)return {text,unresolved:true};
  }
  if(!pendingUserChoice(current)&&failure?.issues?.length){
    const comparison=currentComparisonBudget(current,user,agent.id);
    const text=comparison&&comparisonBudgetPartial(comparison,receipts.filter(result=>!isExploratory(result)));
    if(text&&!checkAnswer(text,evidence,question).length)return {text,unresolved:true,verifiedObservation:true};
  }
  if(!pendingUserChoice(current)&&(failure?.issues?.length||candidateMissingDelivery(candidate))){
    const inventory=completionInventoryAnswer(question,receipts,user.data.id,agent.id);
    if(inventory&&(failure?.issues.some(issue=>issue.startsWith(WORK_EVIDENCE_ISSUE))
      ||candidateMissingDelivery(candidate)||checkAnswer(candidate,evidence,question).length))
      return {text:inventory,unresolved:false};
  }
  if(failure?.issues?.some(issue=>issue.startsWith(FORMULA_ANSWER_ISSUE))||earlierFormulaReview(current)){
    return formulaComponentDelivery(events,user,agent.id,receipts,evidence);
  }
  const forecastReceipts=typedReceipts(current).filter(result=>!isExploratory(result));
  if(!pendingUserChoice(current)&&(candidateMissingDelivery(candidate)||failure?.issues?.length
    ||forecastDeliveryIssues(candidate,forecastReceipts,question).length)){
    const canonical=verifiedForecastAnswer(question,typedReceipts(current),{requestRef:user.data.id,sessionId:agent.id})
      ||forecastClaimDelivery(forecastReceipts);
    if(canonical&&!checkAnswer(canonical,evidence,question).length)return {text:canonical,unresolved:false};
    const history=verifiedForecastGap(question,evidence,current,user.data.id,agent.id)
      ||verifiedAnnualHistoryOnly(question,typedReceipts(current),user.data.id,agent.id);
    if(history&&!checkAnswer(history,evidence,question).length)return {text:history,unresolved:true,verifiedObservation:true};
  }
  const sections=answerFromReceipts(receipts,evidence,user.data.id,agent.id,question);
  if(!sections)return null;
  const unsettled=pendingUserChoice(current);
  const omitted=unsettled||candidateMissingDelivery(candidate);
  if(!omitted&&!failure?.issues?.length)return null;
  if(!unsettled&&candidate.trim()&&!candidateMissingDelivery(candidate)
    &&checkAnswer(candidate,evidence,question).length===0)return null;
  const validated=sections.filter(section=>checkAnswer(receiptSectionText(section),[section.result],question)
    .every(issue=>/^交付缺项：/.test(issue)));
  if(!validated.length)return null;
  const partial=validated.map(receiptSectionText).join('\n\n');
  const remaining=checkAnswer(partial,evidence,question);
  if(remaining.some(issue=>!/^交付缺项：/.test(issue)))return null;
  const missing=unsettled||(remaining.length||validated.length<sections.length
    ||recoverableQueryIssues(current).length||clarificationDeliveryIssues(current,partial).length
    ?'其余所问内容本轮暂未核实。':'');
  const unresolved=Boolean(missing||validated.some(({result})=>result.validation?.checks?.some(check=>check.state==='unknown'&&check.area==='scope')));
  return {text:partial+(missing?'\n\n'+missing:''),unresolved};
}

function verifiedPartialSections(events,agent,current){
  if(earlierFormulaReview(current))return null;
  const user=requestContext(events).users.at(-1);
  if(!user?.data.id)return null;
  const evidence=activeEvidence(typedReceipts(current));
  const question=requestText(events);
  const sections=answerFromReceipts(typedReceipts(current,formalQueryTools),evidence,user.data.id,agent.id,question);
  if(!sections)return null;
  const text=sections.map(receiptSectionText).join('\n\n');
  return checkAnswer(text,evidence,question).every(issue=>/^交付缺项：/.test(issue))?text:null;
}

export function contextBudgetAnswer(agent){
  const events=agent.session.snapshotEvents(),start=events.findLast(event=>event.type==='turn/start');
  const current=start?events.filter(event=>event.seq>start.seq):[];
  const user=requestContext(events).users.at(-1);
  if(user?.data.id&&user.data.turn===start?.data.turn){
    const sessionId=agent.id??agent.session.id;
    const question=requestText(events);
    const evidence=typedReceipts(current);
    const forecast=verifiedForecastAnswer(question,evidence,{requestRef:user.data.id,sessionId});
    if(forecast)return forecast;
    const partial=verifiedPartialSections(events,{id:sessionId},current);
    if(partial)return partial+'\n\n本轮交付已核实的部分，其余分析尚未完成。';
    const formal=activeEvidence(typedReceipts(current,formalQueryTools)).some(result=>
      result.status==='succeeded'&&result.result_ref&&result.view_url&&result.row_count>0
      &&result.receipt?.read_only===true&&!isExploratory(result)&&result.validation?.state!=='conflict'
      &&result.result_lineage?.request_ref===user.data.id&&result.result_lineage.session_id===sessionId
      &&result.result_lineage.purpose==='answer');
    if(formal)return '本轮已查得的结果见结果卡，其余分析尚未完成。';
    if(evidence.some(result=>result.status==='succeeded'&&isExploratory(result)))
      return '本轮已核对相关数据，正式统计尚未完成。可在新会话继续查询同一范围。';
  }
  return '本轮统计尚未完成，会话已保留。可在新会话继续查询。';
}

export function retainedCompletionSummary(events,start,results,session) {
  const users=requestContext(events).users, query=messageText(users.at(-1));
  const prior=users.at(-2), current=users.at(-1);
  const retain=/(?:保留(?:前面|前述|之前|上轮|上一轮)(?:的)?(?:结果|数据|结论)|(?:前面|前述|之前|上轮|上一轮)(?:的)?(?:结果|数据|结论)\s*(?:也|仍|一并)?\s*保留)/;
  if(!prior||!current||current.data.turn!==start.data.turn||!retain.test(query)
    ||/(?:不用|无需|不必|不要|不再)\s*保留|排名|对比|同比|环比|平均|天数|原因|明细|按.{1,12}分组/.test(query))return null;
  const [result,...rest]=results.filter(r=>r.result_ref&&r.status==='succeeded'&&!isExploratory(r));
  const period=result?.validation?.completion_period, lineage=result?.result_lineage;
  if(rest.length||period?.state!=='checked'||period.role!=='completion'||result.truncated||result.validation?.state==='conflict'
    ||result.receipt?.read_only!==true||lineage?.request_ref!==current.data.id||lineage.session_id!==session
    ||lineage.purpose!=='answer'||lineage.source_id!==result.receipt.source_id
    ||result.values?.complete!==true||!Array.isArray(result.values.columns)
    ||result.row_count!==1||result.values.rows?.length!==1)return null;
  const supplied=events.find(e=>e.type==='turn/end'&&e.data.turn===prior.data.turn&&e.seq<start.seq);
  if(supplied?.data.reason?.kind!=='completed')return null;
  const earlier=activeEvidence(typedReceipts(events.filter(e=>e.seq>prior.seq&&e.seq<supplied.seq),formalQueryTools));
  const [previous,...other]=earlier.filter(r=>r.validation?.current_execution?.target_month+'-01'===period.start_inclusive);
  const project=result.business_project;
  if(!previous||other.length||!project?.id||previous.business_project?.id!==project.id
    ||previous.business_project?.version!==project.version)return null;
  const recap=verifiedCurrentExecution(messageText(prior),[previous],prior.data.id,session);
  if(!recap)return null;
  const {columns,rows:[row]}=result.values;
  const value=pattern=>{
    const indexes=columns.flatMap((column,index)=>pattern.test(String(column).normalize('NFKC'))?[index]:[]);
    return indexes.length===1?row[indexes[0]]:undefined;
  };
  const completed=value(/^已办结(?:事项)?(?:件)?数(?:\(.*\))?$/);
  const timely=value(/^(?:按时|准时)办结(?:事项)?(?:件)?数(?:\(.*\))?$/);
  const rate=value(/^(?:按时|准时)办结率(?:\(.*\))?$/);
  if(!Number.isSafeInteger(completed)||completed<=0||!Number.isSafeInteger(timely)||timely<0||timely>completed
    ||!Number.isFinite(rate)||Math.abs(rate-100*timely/completed)>.00501)return null;
  const [year,month]=period.start_inclusive.split('-');
  const late=value(/^逾期办结(?:事项)?(?:件)?数(?:\(.*\))?$/);
  const lateText=Number.isSafeInteger(late)&&late>=0&&timely+late<=completed?`，${late} 件逾期办结`:'';
  return {retainedRef:previous.result_ref,text:`${year}年${Number(month)}月实际办结 ${completed} 件，其中 ${timely} 件按时办结${lateText}，**按时办结率 ${rate}%**。\n\n${recap}`};
}

function requestedObservation(user,agent,current,question){
  if(!user?.data.id||pendingUserChoice(current)||typedReceipts(current).some(result=>
    result.claims?.some(claim=>claim.statistics?.kind==='forecast')))return null;
  return verifiedObservationDelivery(typedReceipts(current,formalQueryTools),user.data.id,agent.id,question);
}

function previouslyDeliveredSections(events,sections){
  return sections&&events.some(event=>event.type==='assistant/message'
    &&event.data.message?.content?.some(block=>block.type==='tool-call'&&block.name==='ask_user_question')
    &&event.data.message.content.some(block=>block.type==='text'&&block.text===sections));
}

const metricQueryTools=new Set(['data_metric_query']);
const comparisonAnalysisTools=new Set(['analysis_compute']);
export function independentMetricReceipt(events,plan,query,categories=[]){
  const dimensions=query.group_by||[];
  const sameDimensions=value=>Array.isArray(value)&&value.length===dimensions.length
    &&value.every((dimension,index)=>dimension===dimensions[index]);
  const samePeriods=periods=>Array.isArray(periods)&&periods.length===query.periods.length
    &&query.periods.every(period=>periods.includes(period));
  const receipts=activeEvidence(typedReceipts(events,metricQueryTools,call=>{
    const args=toolArguments(call);
    return args.source_id===query.source_id&&args.metric_id===query.metric_id&&args.metric_version===query.metric_version
      &&samePeriods(args.periods)&&sameDimensions(args.group_by||[])&&!args.filters?.length&&!args.comparison;
  })).filter(result=>{
    const identity=metricDeliveryIdentity(result);
    return identity&&result.inspection_reconciliation?.status!=='conflict'&&identity.source===query.source_id
      &&identity.project.id===plan.publication.id&&identity.project.version===plan.publication.version
      &&identity.metric===query.metric_id&&identity.version===query.metric_version
      &&samePeriods(identity.scope.periods)&&sameDimensions(identity.scope.dimensions)&&!identity.scope.filters.length
      &&result.facts.every(fact=>Object.keys(fact.dimensions||{}).length===1+dimensions.length&&Number.isFinite(fact.value))
      &&(categories.length?result.facts.length===query.periods.length*categories.length&&query.periods.every(period=>
        categories.every(category=>result.facts.some(fact=>fact.dimensions.period===period&&fact.dimensions[dimensions[0]]===category)))
        :samePeriods(result.facts.map(fact=>fact.dimensions.period)));
  });
  return receipts.length===1?receipts[0]:null;
}

function selectedMetricReceipt(plan,current,selection,comparison){
  const result=independentMetricReceipt(current.filter(event=>event.seq>selection.reply.seq),plan,selection.option.query_request);
  return result&&result.execution_scope===comparison.execution_scope
    &&result.source_provenance.connection_revision===comparison.source_provenance.connection_revision?result:null;
}

function selectedMetricSection(result,user,sessionId){
  const section=answerFromReceipts([result],[result],user.data.id,sessionId)?.[0];
  if(!section)return null;
  const [{value,unit,dimensions:{period}}]=result.facts;
  const formatted=unit==='%'?value.toFixed(2):String(value);
  const body=['**'+period+'年'+section.table.title+'**为 '+formatted+unit+'。',...section.claims.slice(0,10)].join('\n\n');
  return withReceiptScopeNotice(withDefinitionNotice(body,section),section);
}

function completedIndependentAnswer(preview,section){
  // The host folds tool-call messages into the completed turn's process.
  return preview+'\n\n'+section;
}

function verifiedIndependentChoiceAnswer(plan,current,user,sessionId){
  const selection=independentChoiceSelection(plan,current);
  if(!selection)return null;
  const prior=current.filter(event=>event.seq<selection.ask.seq);
  const budget=independentMetricReceipt(prior,plan,plan.comparison.query_request);
  const choice=budget&&selectedMetricReceipt(plan,current,selection,budget);
  if(!choice)return null;
  const budgetEvidence=typedReceipts(prior,comparisonAnalysisTools,call=>{
    const args=toolArguments(call);
    return args.operation==='period_compare'&&args.result_ref===budget.result_ref;
  });
  const baseline=budget.facts.find(fact=>fact.dimensions.period===plan.comparison.baseline);
  const currentFact=budget.facts.find(fact=>fact.dimensions.period===plan.comparison.current);
  const comparison=budgetEvidence.find(result=>result.status==='succeeded'&&result.result_ref===budget.result_ref
    &&result.execution?.operation==='analysis.compute'&&result.execution_scope===budget.execution_scope
    &&result.claims?.some(claim=>claim.operation==='period_compare'&&claim.metric_id===budget.metric_contract.id
      &&claim.baseline?.id===baseline.id&&claim.current?.id===currentFact.id
      &&typeof claim.verified_text==='string'));
  if(!comparison)return null;
  const budgetSection=answerFromReceipts([budget],[comparison],user.data.id,sessionId)?.[0];
  const preview=independentChoicePreview(prior,selection.ask.data.callId);
  if(!budgetSection||!preview||preview!==receiptSectionText(budgetSection))return null;
  const selected=selectedMetricSection(choice,user,sessionId);
  return selected?completedIndependentAnswer(preview,selected):null;
}

function verifiedIndependentGroupedChoiceAnswer(plan,current,user,sessionId,grouped,comparison){
  if(!grouped||!comparison)return null;
  const selection=independentChoiceSelection(plan,current);
  if(!selection)return null;
  const preview=independentChoicePreview(current.filter(event=>event.seq<selection.ask.seq),selection.ask.data.callId);
  if(!preview||preview!==comparison)return null;
  const choice=selectedMetricReceipt(plan,current,selection,grouped);
  const selected=choice&&selectedMetricSection(choice,user,sessionId);
  return selected?completedIndependentAnswer(preview,selected):null;
}

function pendingPeriodCalculation(events,question){
  const months=question.match(/((?:19|20)\d{2})年\s*(\d{1,2})月[^。！？?；;\n]{0,38}?比(?:(\d{4})年\s*)?(\d{1,2})月/);
  const years=!months&&question.match(/((?:19|20)\d{2})年[^。！？?；;\n]{0,38}?比(?:较|上)?(去年|上年|(?:19|20)\d{2}年)/);
  if(!(months||years)||!/变化|增减|增长|减少|高还是低|多还是少|相差|差额|降幅|涨幅/.test(question))return null;
  const period=(year,month)=>Number(month)>=1&&Number(month)<=12?year+'-'+month.padStart(2,'0'):null;
  const current=months?period(months[1],months[2]):years[1];
  const baseline=months?period(months[3]||months[1],months[4]):/去年|上年/.test(years[2])?String(Number(years[1])-1):years[2].slice(0,4);
  if(!current||!baseline||current===baseline)return null;
  const eligible=typedReceipts(events,new Set(['data_metric_query'])).filter(result=>{
    const metric=result.metric_contract,dimensions=metric?.scope?.dimensions;
    return result.status==='succeeded'&&result.result_ref&&result.receipt?.read_only===true&&!result.truncated
      &&result.validation?.state!=='conflict'&&result.answer_contract?.complete===true
      &&result.analysis_options?.mode==='metric_facts'&&result.analysis_options.complete===true
      &&metric?.additive===true&&metric.ratio===false&&metric.unit&&Array.isArray(dimensions)&&dimensions.length<=1
      &&metric.confirmation?.basis==='published_configuration'
      &&metric.confirmation?.source_id===result.receipt.source_id&&metric.confirmation?.definition_fingerprint
      &&!dimensions.some(dim=>metric.overlapping_dimensions?.includes(dim))
      &&metric.periods?.length===2&&metric.periods.includes(baseline)&&metric.periods.includes(current)
      &&Array.isArray(result.facts)&&result.facts.length===result.row_count&&result.facts.length>=2&&result.facts.length<=40
      &&result.facts.every(fact=>fact.id?.startsWith(result.result_ref+':')&&Number.isFinite(fact.value)&&fact.value>=0
        &&fact.unit===metric.unit&&[current,baseline].includes(fact.dimensions?.period)
        &&Object.keys(fact.dimensions).length===1+dimensions.length&&dimensions.every(dim=>fact.dimensions[dim]));
  });
  const evidence=activeEvidence(typedReceipts(events));
  const alternatives=activeEvidence(eligible);
  const distinct=uniqueMetricDeliveries(alternatives,evidence);
  const paired=groupedComparisonSources(distinct,question);
  const result=distinct.length===1?distinct[0]:paired?.grouped;
  if(!result)return null;
  const dims=result.metric_contract.scope.dimensions;
  const groups=period=>result.facts.filter(fact=>fact.dimensions.period===period).map(fact=>JSON.stringify(dims.map(dim=>fact.dimensions[dim])));
  const before=groups(baseline).sort(),after=groups(current).sort();
  if(!before.length||new Set(before).size!==before.length||JSON.stringify(before)!==JSON.stringify(after))return null;
  const resultRefs=new Set(alternatives.filter(value=>!paired||value.metric_contract.scope.dimensions.length===1)
    .map(value=>value.result_ref));
  const called=events.filter(event=>event.type==='tool/call'&&event.data.name==='analysis_compute')
    .map(toolArguments).filter(args=>resultRefs.has(args.result_ref));
  const compared=(evidence.find(value=>value.result_ref===result.result_ref)?.claims||[])
    .some(claim=>claim.operation==='period_compare'
      &&claim.baseline?.dimensions?.period===baseline&&claim.current?.dimensions?.period===current
      &&(before.length===1||claim.current.dimensions.aggregation==='分类合计'));
  if(!compared&&!called.some(args=>args.operation==='period_compare')){
    return {name:'analysis_compute',args:{result_ref:result.result_ref,operation:'period_compare',baseline_period:baseline,current_period:current}};
  }
  if(dims.length===1&&after.length>1&&/(?:各|分别|各自)占(?:总量|总数|比)/.test(question)
    &&!called.some(args=>args.operation==='share')&&result.facts.filter(fact=>fact.dimensions.period===current).some(fact=>fact.value>0)){
    return {name:'analysis_compute',args:{result_ref:result.result_ref,operation:'share'}};
  }
  return null;
}

function boundedGroupedEmpty(events,question){
  const user=requestContext(events).users.at(-1);
  if(!user?.data.id)return null;
  const results=activeEvidence(typedReceipts(events.filter(event=>event.seq>user.seq),formalQueryTools))
    .filter(result=>result.result_ref&&result.status==='succeeded'&&!isExploratory(result));
  // A distinct formal result may answer the other question or give valid
  // observations for another source. Leave both with the normal answer path.
  if(results.length!==1)return null;
  const result=results[0];
  const scope=groupedEmptyScope(result,question,user.data.id);
  return scope?{result,scope}:null;
}

function independentMetricLookup(events,label){
  const catalogNames=new Set(['data_catalog']);
  const catalogCalls=events.filter(event=>event.type==='tool/call'&&event.data.name==='data_catalog');
  const queries=new Map(catalogCalls.map(event=>[event.data.callId,normalizedSearch(toolArguments(event).question)]));
  const target=normalizedSearch(label), qualifier=target.match(/^(.{2,})合格率$/u)?.[1];
  const covers=searches=>searches.some(query=>query.includes(target))||Boolean(qualifier&&
    searches.some(query=>query.includes(qualifier))&&searches.some(query=>query.includes('合格率')));
  const directories=typedReceipts(events,catalogNames).filter(receipt=>receipt.status==='succeeded'&&Array.isArray(receipt.sources));
  const succeeded=token=>typedReceipts(events,catalogNames,call=>queries.get(call.data.callId).includes(token))
    .some(receipt=>receipt.status==='succeeded'&&Array.isArray(receipt.sources)&&receipt.sources.length);
  const searched=succeeded(target)||Boolean(qualifier&&succeeded(qualifier)&&succeeded('合格率'));
  const relevant=name=>typeof name==='string'&&(name.replace(/\s/g,'').includes(label)
    ||/合格率$/.test(label)&&/抽检|监督(?:检查|检验)|质量/.test(name)&&/合格率|通过率/.test(name));
  const candidate=directories.some(receipt=>(receipt.same_name_metrics?.metrics||[]).some(metric=>relevant(metric.concept))
    ||receipt.sources.some(source=>['semantic_metrics','metric_definitions','unavailable_metrics','caliber_matches','example_matches','catalog_entries']
      .some(key=>(source[key]||[]).some(item=>[item.name,item.label,item.title,item.question,...(item.aliases||[])].some(relevant)))
      ||(source.clarifications||[]).some(item=>relevant(item.term))
      ||(source.tables||[]).some(table=>relevant(table?.label)||relevant(table?.name)||
        (table?.columns||[]).some(column=>relevant(column?.label)||relevant(column?.name)))));
  return {attempted:covers([...queries.values()]),searched,candidate};
}

function groupedEmptyDelivery(question,events,tools){
  if(events.some(event=>event.type==='tool/call'&&(event.data.name==='ask_user_question'||/^(?:knowledge_|analysis_)/.test(event.data.name)))
     ||clarificationDeliveryIssues(events).length)return null;
  const empty=boundedGroupedEmpty(events,question);
  const independent=empty?.scope.independent;
  if(!independent)return null;
  const {result,scope}=empty;
  const lookup=independentMetricLookup(events,independent.label);
  if(lookup.candidate)return null;
  if(!lookup.attempted&&tools?.some(tool=>tool.name==='data_catalog')){
    return {tool:{name:'data_catalog',args:{question:independent.question}}};
  }
  if(!lookup.searched)return null;
  const months=scope.proof.query_periods.map((period,index,list)=>
    (index&&period.slice(0,4)===list[index-1].slice(0,4)?'':period.slice(0,4)+'年')+Number(period.slice(5))+'月').join('和');
  const comparison=['两月变化'];
  if(/占比|占(?:总|多少)|比例|份额/.test(scope.comparison))comparison.push('各类占比');
  if(/各(?:自)?增减|各(?:类|自)[^。？]{0,8}变化/.test(scope.comparison))comparison.push('各类增减');
  if(/主要来自|贡献|变化来源/.test(scope.comparison))comparison.push('变化来源');
  const requirements=/合格率$/.test(independent.label)?'计算需要同一统计口径下的合格数和检查总数。':'计算需要同一统计口径下的分子和分母。';
  return {text:'本次查询在当前数据源和筛选范围内，'+months+'未返回符合条件的'+(scope.object||scope.subject)+'记录。'+
    comparison.join('、')+'暂无法核实。\n\n'+independent.period+independent.label+
    '本轮尚未核实，无法给出数值。'+requirements};
}

const snapshotRejection=/^(?:Error: )?SNAPSHOT_HISTORY_REQUIRED:\s*已发布口径说明本次查询来源为当前有效快照。/;
const datedSnapshotQuestion=/(?<!\d)((?:19|20)\d{2})\s*年\s*(1[0-2]|0?[1-9])\s*月(?:(?:\s*(?:3[01]|[12]\d|0?[1-9])\s*日)|\s*(?:末|底))?/g;

function snapshotQuestion(question,boundary){
  const dates=[...new Set((question.match(datedSnapshotQuestion)||[]).map(normalizedSearch))];
  if(dates.length!==1||/(?:^|[，。！？!?；;\n])\s*(?:另外|此外|附带|同时再)|当前|现在|今天|本期|同比|环比|预测|重新选|(?:同时|另)(?:查询|查看)|画图|导出/.test(question))return null;
  const clauses=question.split(/[。！？!?；;\n]+/).map(clause=>clause.trim()).filter(Boolean);
  const definition=clauses.find(clause=>/区别|差异/.test(clause)&&!/数量|多少|占比|排名|排行榜|相差/.test(clause));
  if(definition&&(!definition.includes('甲级')||!definition.includes('乙级')))return null;
  const main=clauses.filter(clause=>clause!==definition);
  const first=main[0];
  const dated=first?.match(datedSnapshotQuestion)?.[0];
  if(!dated)return null;
  const region=first.match(/全(?:省|市|区|县|国)/)?.[0];
  if(!region)return null;
  const measure=String(boundary.published_measure||'').replace(/^[A-Za-z0-9_-]+\s*[:：]?\s*/,'').replace(/^(?:本|当|上)?(?:月末|年末|期末)/,'').replace(/(?:总数量|总数|数量|总量|数)$/,'');
  const requested=boundary.requested_subjects||[],published=boundary.published_subjects||[];
  if(requested.length===1&&boundary.subject_mismatch===true&&published.length===1&&first.includes(requested[0])){
    const subject=requested[0],afterDate=first.slice(first.indexOf(dated)+dated.length),at=afterDate.indexOf(subject);
    if(at<0)return null;
    const label=afterDate.slice(0,at+subject.length).trim().replace(/^[，,]\s*/,'');
    if(!/^[\p{Script=Han}]{2,30}$/u.test(label)||!label.startsWith(region))return null;
    const name=label.slice(region.length);
    if(main.some(clause=>!clause.includes(subject)||!/有多少|多少个|多少本|多少张|多少件|多少台|数量|总量|总数/.test(clause)))return null;
    const breakdown=main.slice(1).map(clause=>clause.match(new RegExp('^(?:有效|在册)?([甲乙丙丁一二三四特]级)'+subject+
      '(?:有多少[个本张件台]?|(?:的)?(?:数量|总量|总数)(?:为多少|是多少|多少)?)$','u'))?.[1]);
    if(breakdown.some(value=>!value))return null;
    return {date:dates[0],region,name,reports:['总量',...new Set(breakdown.map(grade=>grade+'数量'))],definition,published:published[0]};
  }
  if(measure.length<2||!first.includes(measure)||!/(?:数量|有多少|多少台|多少个|总数|总量|排列|排名|排行|最多)/.test(first))return null;
  const details=main.slice(1);
  if(details.some(clause=>!/^(?:前[一二三四五六七八九十\d]+(?:个|项)?[^，。]{0,24}(?:占|比例)|再(?:查询|查|看)[^，。]{0,24}(?:最多|最少|第一)[^，。]{0,24}(?:数量|多少)|(?:甲乙级|两级|两者|二者)[^，。]{0,10}(?:相差|差额|差值)|同一[^，。]{0,20}分别计次)/.test(clause)))return null;
  const reports=[];
  const grades=[...new Set((question.match(/[甲乙丙丁一二三四特]级/g)||[]))];
  if(/排列|排名|排行/.test(first))reports.push((first.match(/[甲乙丙丁一二三四特]级/)?.[0]||'')+(/专业/.test(first)?'专业':'')+'排名');
  else reports.push('数量');
  if(details.some(clause=>/占|比例/.test(clause)))reports.push(details.some(clause=>/^前[三3]/.test(clause))?'前三占比':'占比');
  const other=details.find(clause=>/再(?:查询|查|看)/.test(clause));
  if(other)reports.push('排名首位分组的'+(other.match(/[甲乙丙丁一二三四特]级/)?.[0]||'另一等级')+'数量');
  if(main.some(clause=>/相差|差额|差值/.test(clause)))reports.push((grades.length===2?grades.map(grade=>grade[0]).join('')+'级':'等级')+'差额');
  return {date:dates[0],region,name:measure,reports,definition,published:null};
}

function pendingSnapshotClarification(events){
  if(clarificationDeliveryIssues(events).length)return true;
  const clarifications=clarificationEvidence(events).clarifications;
  // A native choice can change the question without changing the original
  // text. Only source confirmation is harmless to this history fallback.
  if(clarifications.some(item=>!/^(?:请)?(?:使用|采用|选择)[^，。；;\n]{1,100}(?:数据库|数据源|数据仓库)[。]?$/u.test(item.answer)
      ||/(?:改为|改用|换成|当前快照|按单位|按证书|20\d{2}\s*年|其他|不要)/.test(item.answer)))return true;
  const answered=new Set(clarifications.map(item=>item.call_id+'\n'+item.question_id));
  const optional=new Set(events.filter(event=>event.type==='tool/result'&&(!event.surfaceOp||event.surfaceOp==='append')
    &&event.data.message?.isError&&event.data.message.content?.some(block=>block.type==='text'&&/^(?:Error: )?(?:OPTIONAL_FOLLOWUP|MISSING_DATA_NO_CLARIFICATION):/.test(block.text)))
    .map(event=>event.data.message.source?.callId||event.data.message.toolCallId));
  return events.some(event=>{
    if(event.type!=='tool/call'||event.data.name!=='ask_user_question'||optional.has(event.data.callId))return false;
    const questions=toolArguments(event).questions;
    return !Array.isArray(questions)||!questions.length||questions.some(question=>!answered.has(event.data.callId+'\n'+question.id));
  });
}

function snapshotBoundaryAnswer(question,events){
  if(!question.match(datedSnapshotQuestion))return null;
  const queries=events.filter(event=>event.type==='tool/call'&&event.data.name==='data_sql_query');
  const blocked=events.findLast(event=>event.type==='tool/result'&&(!event.surfaceOp||event.surfaceOp==='append')
    &&event.data.message?.source?.kind==='tool'&&event.data.message.isError
    &&event.data.message.content?.some(block=>block.type==='text'&&snapshotRejection.test(block.text)));
  if(!blocked)return null;
  const query=queries.find(event=>event.seq<blocked.seq&&event.data.turn===blocked.data.turn
    &&event.data.callId===blocked.data.message.source.callId);
  const {source_id:sourceId,sql,purpose}=toolArguments(query);
  if(!query||!sourceId||!sql||['explore','probe'].includes(purpose)||pendingSnapshotClarification(events))return null;
  const laterFormal=events.some(event=>event.seq>blocked.seq&&event.type==='tool/call'&&formalQueryTools.has(event.data.name)
    &&!['explore','probe'].includes(toolArguments(event).purpose));
  if(laterFormal)return null;
  if(typedReceipts(events).some(result=>result.result_ref&&result.status==='succeeded'&&!isExploratory(result))
    ||events.some(event=>event.type==='tool/call'&&/^(?:knowledge_|analysis_)/.test(event.data.name)))return null;
  const catalogs=typedReceipts(events,new Set(['data_catalog']));
  // A source-specific result or an incomplete source page cannot establish
  // whether there are other authorized routes for the original question.
  const inventory=typedReceipts(events.filter(event=>event.seq<blocked.seq),new Set(['data_catalog']),call=>{
    const args=toolArguments(call);
    return !args.source_id&&!args.entry_ref&&!args.table_name&&!args.metric_id&&!args.source_offset&&args.view!=='metrics';
  }).findLast(value=>value.status==='succeeded'&&value.sources?.length&&(!value.catalog_view?.paged
    ||value.catalog_view.next_source_offset===null&&value.catalog_view.source_count===value.sources.length));
  if(!inventory||inventory.sources.length!==1||inventory.sources[0].id!==sourceId
    ||catalogs.some(catalog=>catalog.sources?.some(source=>source.id!==sourceId))
    ||queries.some(call=>call!==query&&!['explore','probe'].includes(toolArguments(call).purpose)
      &&toolArguments(call).source_id&&toolArguments(call).source_id!==sourceId))return null;
  const source=inventory.sources[0];
  if(!source.business_project?.id||!source.business_project.version)return null;
  if(catalogs.some(catalog=>catalog.sources?.some(row=>row.business_project&&
    (row.business_project.id!==source.business_project.id||row.business_project.version!==source.business_project.version))))return null;
  const boundaries=(source.snapshot_boundaries||[]).filter(boundary=>boundary.state==='current_snapshot_only'&&boundary.applies_to==='listed_table'
    &&boundary.publication?.id===source.business_project.id&&boundary.publication.version===source.business_project.version
    &&boundary.read_request?.source_id===sourceId&&Array.isArray(boundary.published_subjects)&&Array.isArray(boundary.requested_subjects)
    &&[...boundary.published_subjects,...boundary.requested_subjects].every(subject=>typeof subject==='string'&&/^[\p{Script=Han}]{2,12}$/u.test(subject))
    &&typeof boundary.table==='string'&&source.tables?.some(table=>(table?.name||table)===boundary.table)
    &&sql.toLowerCase().replace(/["`\[\]]/g,'').split(/[^\w.]+/).some(name=>name===boundary.table.toLowerCase()));
  if(boundaries.length!==1)return null;
  const spec=snapshotQuestion(question,boundaries[0]);
  if(!spec)return null;
  const possibleHistory=/历史|历年|归档|存档|回溯|archive|history/i;
  const relevant=value=>typeof value==='string'&&possibleHistory.test(value);
  if(catalogs.some(catalog=>catalog.sources?.some(row=>row.registered_query
    ||(row.semantic_metrics||[]).length||(row.metric_definitions||[]).length
    ||['caliber_matches','example_matches','catalog_entries','tables'].some(key=>
      (row[key]||[]).some(item=>[item?.name,item?.label,item?.question,item?.summary,item?.business_description].some(relevant))))))return null;
  const scope=spec.date+spec.region+spec.name;
  const difference=spec.definition?'甲级、乙级的适用条件和区别本轮尚未核实。':'';
  const counted=spec.published?'已核对的相关表是按'+spec.published+'统计的当前有效快照。':'已核对的相关口径使用当前有效快照。';
  return scope+'的'+spec.reports.join('、')+'本轮暂无法核实。'+counted+
    '核实上述统计需要同一范围、同一统计对象在该时点的历史快照，或能够还原当时状态的历史记录。'+difference;
}

function queryBoundaryAnswer(agent,turn,question,events){
  const snapshot=snapshotBoundaryAnswer(question,events);
  if(snapshot)return snapshot;
  if(closingState(agent,turn)?.reasonCode!=='query-progress'
    ||events.some(event=>event.type==='tool/call'&&(
      event.data.name==='ask_user_question'||/^(?:knowledge_|work_|analysis_)/.test(event.data.name))))return null;
  const receipts=typedReceipts(events);
  if(receipts.some(result=>result.result_ref&&result.status==='succeeded'&&!isExploratory(result)))return null;
  if(receipts.some(result=>result.reason_code==='TIMEOUT'))return unsuccessfulQueryAnswer(events);
  const queryNames=new Set(['data_sql_query','data_query','data_metric_query']);
  const queries=new Map(events.filter(event=>event.type==='tool/call'&&queryNames.has(event.data.name))
    .map(event=>[event.data.callId,event.data.arguments??event.data.args??{}]));
  const errors=events.flatMap(event=>{
    const message=event.data.message;
    const args=queries.get(message?.source?.callId||message?.toolCallId);
    if(event.type!=='tool/result'||event.surfaceOp&&event.surfaceOp!=='append'||!message?.isError||args===undefined)return [];
    let params=args;
    if(typeof args==='string'){try{params=JSON.parse(args);}catch{return [];}}
    if(['explore','probe'].includes(params?.purpose))return [];
    return (message.content||[]).filter(block=>block.type==='text').map(block=>block.text);
  });
  // These objects come from the typed execution error. Only use them when
  // the human actually asked for that entity and there is no other formal data.
  for(const error of errors){
    const detail=error.match(/^(?:Error: )?BUSINESS_SCOPE_CONFLICT:\s*(\{[\s\S]+\})$/)?.[1];
    if(!detail)continue;
    let conflicts;
    try{conflicts=JSON.parse(detail).conflicts;}catch{continue;}
    if(!Array.isArray(conflicts))continue;
    for(const conflict of conflicts){
      if(conflict?.area!=='grain'||conflict.state!=='conflict')continue;
      const match=String(conflict.detail).match(/^用户明确统计“([一-鿿]{2,12})”，而当前表的已发布行计数口径为“([一-鿿]{2,12})”。/);
      if(!match||!question.includes(match[1])||!/(?:数量|总量|总数|有多少|共多少)/.test(question))continue;
      const segments=question.trim().split(/[。！!？?；;\n]+/).filter(segment=>segment.trim());
      const comparison=segments.length===2&&/(?:等级|级别|甲级|乙级)/.test(segments[1])
        &&/(?:区别|差异)/.test(segments[1])&&!/(?:排名|排行|数量|多少|比例|占比|趋势)/.test(segments[1]);
      if(!comparison&&(segments.length!==1||/(?:另外|顺便|此外|附带|比例|占比|排名|排行|趋势)/.test(question)))continue;
      const [,requested,counted]=match;
      const count='“'+requested+'”的数量本轮暂无法核实。已检查的来源按“'+counted+'”计数，需要同口径的“'+requested+'”数据才能计算。';
      const definition=comparison?'关于等级区别，具体适用范围和条件本轮尚未核实。':'';
      return count+definition;
    }
  }
  return null;
}

const unavailableQueryReasons=new Map([
  ['TIMEOUT','本轮数据查询超时，暂时无法核实所问数值。'],
  ['SCHEMA_MISMATCH','本轮未能把所问统计与当前数据源的字段对应起来，暂时无法给出数值。'],
  ['EXECUTION_ERROR','当前数据源未能完成本次查询，暂时无法核实所问数值。']
]);

function unsuccessfulQueryAnswer(events){
  const formal=typedReceipts(events,formalQueryTools,call=>!['explore','probe'].includes(toolArguments(call).purpose));
  if(formal.some(result=>result.status==='succeeded'&&result.result_ref&&!isExploratory(result)))return null;
  const failures=formal.filter(result=>result.status==='failed'&&unavailableQueryReasons.has(result.reason_code));
  const reasons=new Set(failures.map(result=>result.reason_code));
  if(reasons.size===1)return unavailableQueryReasons.get(failures[0].reason_code);
  const calls=new Set(events.filter(event=>event.type==='tool/call'&&formalQueryTools.has(event.data.name)
    &&!['explore','probe'].includes(toolArguments(event).purpose)).map(event=>event.data.callId));
  const errors=events.filter(event=>event.type==='tool/result'&&event.data.message?.isError
    &&calls.has(event.data.message.source?.callId)).flatMap(event=>event.data.message.content||[]);
  if(errors.some(block=>block.type==='text'&&/^(?:Error:\s*)?BUSINESS_SCOPE_CONFLICT:/.test(block.text))){
    return '本轮未能确认已查询的数据是否覆盖所问统计范围，暂时无法给出该范围的数值。';
  }
  return null;
}

export function installAnswerGuard(ctx,read,ownDirectory=directory){
  ctx.on('system-prompt/assemble',async(_assembly,{agent},next)=>{
    const assembly=await next();
    if(agent?.session.header?.agentPreset!=='natural-data')return assembly;
    const events=agent.session.snapshotEvents();
    const user=requestContext(events).users.at(-1);
    const turn=events.findLast(event=>event.type==='turn/start')?.data.turn;
    if(!user?.data.id||user.data.turn!==turn||closingState(agent,turn))return assembly;
    const current=events.filter(event=>event.seq>user.seq&&event.data?.turn===turn);
    const tools=annualHistoryProviderTools(user,current,assembly.tools);
    return tools?{...assembly,tools}:assembly;
  });
  const directory=ownDirectory.replace(/\/$/,'')+'/';
  const pending=new Map();
  const plannedInventory=new WeakMap();
  const plannedPreferredBudget=new WeakMap();
  const acknowledgedCoverage=new WeakMap();
  const deliveredObservations=new WeakMap();
  ctx.on('agent/pre-step',async({agent,turn,signal},next)=>{
    const decision=await next();
    if(decision.kind!=='enter'||signal.aborted||agent.session.header?.agentPreset!=='natural-data'
      ||closingState(agent,turn)||acknowledgedCoverage.get(agent)===turn)return decision;
    const events=agent.session.snapshotEvents(),user=requestContext(events).users.at(-1);
    if(user?.data.turn!==turn)return decision;
    const current=events.filter(event=>event.seq>user.seq);
    if(pendingUserChoice(current)||clarificationEvidence(current).clarifications.length)return decision;
    const budget=currentComparisonBudget(current,user,agent.id);
    if(!budget?.plan.report_coverage)return decision;
    const receipts=typedReceipts(current,formalQueryTools,call=>comparisonReportCoverageCall(budget.plan,call,user));
    if(!unobservedComparisonReports(budget,receipts))return decision;
    acknowledgedCoverage.set(agent,turn);
    const years=[budget.plan.year,budget.plan.compared_year].sort().join('、');
    const text=years+' 年的项目在已核对的资金报送范围内没有关联记录，无需再次查询这条报送路径。'
      +'这仅说明该来源的覆盖情况。另有授权来源和可核对的实际投入字段时继续查询，保留真实的零值。'
      +'已查到的项目预算可作参考，不能代替实际投入。其他问项各自核对，有证据的结果直接给出。';
    return withNotice(decision,createUserMessage({source:{kind:'natural-data-fiscal-coverage',form:'notice',summary:'已核对资金报送覆盖'},
      content:[{type:'text',text}]}));
  });
  installAnswerAdmission(ctx,{pending,polish:polishResultNotices,omitRedundantDiscovery(agent,events,start,calls){
    if(!calls.some(call=>call.name==='data_catalog')||closingState(agent,start.data.turn))return null;
    const user=requestContext(events).users.at(-1);
    if(!user?.data.id||start.data.turn!==user.data.turn)return null;
    const current=events.filter(event=>event.seq>user.seq);
    if(pendingUserChoice(current)||clarificationEvidence(current).clarifications.length)return null;
    const verified=currentComparisonBudget(current,user,agent.id);
    if(!verified)return null;
    const coverageBuild=comparisonCoverageBuild(verified,current,user);
    const omitted=calls.filter(call=>{
      if(call.name!=='data_catalog')return false;
      const args=toolArguments({data:call});
      return coverageBuild?redundantComparisonCoverageRead(verified.plan,current,args,coverageBuild)
        :redundantComparisonSearch(verified.plan,current,args);
    });
    // Do not turn an all-catalog batch into an empty tool turn. The existing
    // evidence-based settlement decides when that batch can finish the answer.
    return omitted.length<calls.length?new Set(omitted.map(call=>call.id)):null;
  },async beforeQuestion(agent,events,start,signal,calls){
    signal.throwIfAborted();
    if(calls.length===1){
      const call=calls[0];
      let args;
      try{args=JSON.parse(call.arguments);}catch{}
      const user=requestContext(events).users.at(-1);
      if(user?.data.turn===start.data.turn&&Array.isArray(args?.questions)
        &&(workMeasureQuestionRejection(messageText(user),args.questions)
          ||formulaQuestionRejection(events,user,args.questions,agent.id,call.id)))return {suppressProgress:true};
    }
    const current=events.filter(event=>event.seq>=start.seq);
    return verifiedPartialSections(events,agent,current);
  },async before(agent,events,start,options,signal){
    const {text,users}=requestContext(events);
    const user=users.at(-1);
    if(!user?.data.id||user.data.turn!==start.data.turn)return null;
    const current=events.filter(event=>event.seq>user.seq);
    if(!closingState(agent,start.data.turn)){
      const grouping=confirmedApprovalGrouping(current,user,options.tools??[],agent.id);
      if(grouping?.tool)return grouping;
      if(grouping?.text){
        const checked=await reviewCandidate({agent,turn:start.data.turn,signal,read,answerOverride:grouping.text});
        if(checked&&!checked.issues.length)return grouping;
      }
    }
    if(options.tools?.some(tool=>tool.name==='data_catalog')){
      const retainedMonths=retainedAnnualMonths(events,current,user,agent.id);
      if(retainedMonths)return retainedMonths;
    }
    if(options.tools?.some(tool=>tool.name==='data_catalog')
      &&!pendingUserChoice(current)&&!closingState(agent,start.data.turn)){
      const confirmedObservation=monthlyObservationPeriodTool(user,current,options.tools);
      if(confirmedObservation)return {tool:confirmedObservation};
      const missing=verifiedUnavailableCatalogAnswer(events,user);
      if(missing){
        const checked=await reviewCandidate({agent,turn:start.data.turn,signal,read,answerOverride:missing});
        if(checked&&!checked.issues.length)return {text:missing};
      }
      const coverage=unavailableCatalogCoverageTool(events,user)||initialCatalogCoverageTool(events,user);
      if(coverage)return {tool:coverage};
    }
    if(!closingState(agent,start.data.turn)&&options.tools?.some(tool=>tool.name==='data_sql_query')){
      const retry=sqlTimeoutRetry(events,{requestRef:user.data.id,sessionId:agent.id});
      if(retry)return {tool:{name:retry.name,args:retry.args}};
      const forecast=repeatedForecastRead(events,current,user,agent.id);
      if(forecast)return {tool:forecast};
      if(options.tools.some(tool=>tool.name==='data_catalog')){
        const ranking=rawDimensionRankingTool(events,current,agent.id);
        if(ranking)return {tool:ranking};
      }
    }
    const catalogEvidence=typedReceipts(current,catalogTools);
    const latestCatalog=catalogEvidence.findLast(catalog=>catalog.status==='succeeded');
    const comparisonSource=latestCatalog?.sources?.length===1&&latestCatalog.sources[0];
    const spending=comparisonSource?.comparison_report_amounts;
    if(spending&&options.tools?.some(tool=>tool.name==='data_sql_query')
      &&!pendingUserChoice(current)&&!closingState(agent,start.data.turn)){
      const plans=[spending,comparisonSource.comparison_assessment_query].filter(query=>
        query?.source_id===comparisonSource.id&&query.purpose==='answer'&&typeof query.sql==='string');
      const work=current.filter(event=>event.type==='tool/call'&&event.data.name!=='data_catalog');
      const matches=(query,call)=>{
        const args=toolArguments(call);
        return call.data.name==='data_sql_query'&&args.source_id===query.source_id&&args.sql===query.sql
          &&args.purpose==='answer';
      };
      if(work.every(call=>plans.some(query=>matches(query,call)))){
        const next=plans.find(query=>!work.some(call=>matches(query,call)));
        if(next)return {tool:{name:'data_sql_query',args:next}};
      }
    }
    const preferred=preferredBudgetPlan(catalogEvidence,user);
    const annualComparison=!preferred&&comparisonBudgetPlan(catalogEvidence,user);
    const budget=preferred||(annualComparison&&comparisonBudgetNeeded(annualComparison,
      typedReceipts(current,formalQueryTools),user.data.id,agent.id)?annualComparison:null);
    if(budget&&plannedPreferredBudget.get(agent)!==start.seq&&!closingState(agent,start.data.turn)
      &&options.tools?.some(tool=>tool.name==='data_sql_query')
      &&!pendingUserChoice(current)&&!clarificationEvidence(current).clarifications.length
      &&!current.some(event=>preferredBudgetCall(budget,event,user))){
      plannedPreferredBudget.set(agent,start.seq);
      const primary={name:'data_sql_query',args:budget.query_request};
      const early=budget===annualComparison&&!current.some(event=>comparisonReportCoverageCall(budget,event,user))
        ?comparisonCoverageTools(budget,current).filter(tool=>options.tools.some(schema=>schema.name===tool.name)):[];
      return early.length?{tools:[primary,...early]}:{tool:primary};
    }
    if(latestCatalog?.sources?.[0]?.work_inventory_query&&!closingState(agent,start.data.turn)
      &&plannedInventory.get(agent)!==start.seq&&options.tools?.some(tool=>tool.name==='data_sql_query')
      &&!pendingUserChoice(current)&&!clarificationEvidence(current).clarifications.length){
      const tool=workInventoryTool(latestCatalog,user,current,typedReceipts(current,formalQueryTools),agent.id);
      if(tool){
        plannedInventory.set(agent,start.seq);
        return {tool};
      }
    }
    const comparison=independentComparisonPlan(catalogEvidence,user.data.id);
    const monthlyMetric=publishedMonthlyMetricPlan(catalogEvidence,user.data.id);
    const view=previousGroupingView(events);
    if(view&&!current.some(event=>event.type==='tool/call')&&options.tools?.some(tool=>tool.name==='data_visualize')){
      return {tool:{name:'data_visualize',args:view}};
    }
    if(!isKnowledgeOnly(text)&&options.tools?.some(tool=>tool.name==='data_catalog')){
      let forecast=null;
      if(!current.some(event=>event.type==='tool/call')){
        if(qualificationForecast.test(text)&&qualificationStock.test(text))forecast={name:'data_catalog',args:{question:text}};
        else if(messageText(user)===text&&user.data.content?.every(block=>block.type==='text'))forecast=annualForecastCatalogTool(text);
        if(!forecast&&/^(?:请)?\s*(?:未来|今后|接下来(?:的)?)\s*(?:[一1]年|(?:12|十二)个?月)/.test(text.normalize('NFKC'))
          &&/趋势|走势|走向|增长|上升|下降|减少|增加|持平/.test(text))forecast={name:'data_catalog',args:{question:text}};
        if(!forecast&&/(?:基于|根据|依据|使用|用)\s*(?:最近|近|过去)\s*[\d一二两三四五六七八九十]+\s*(?:年|个?月)/.test(text)
          &&/(?:预测|预估|预计)\s*(?:本年度?|今年|明年|下一年|\d{4}\s*年)\s*\d{1,2}\s*月/.test(text))
          forecast={name:'data_catalog',args:{question:text}};
      }
      const dimension=!closingState(agent,start.data.turn)&&options.tools.some(tool=>tool.name==='ask_user_question')
        ?dimensionBusinessCatalogTool(user,current):null;
      const tool=dimension||freshRepeatedSnapshotTool(events,current)||freshRankingCatalogTool(events,current)
        ||eventDimensionCatalogTool(user,current)||holdingRateCatalogTool(user,current)
        ||independentComparisonCatalogTool(user,current)||forecast;
      if(tool)return {tool};
      const normalized=text.normalize('NFKC');
      if(!current.some(event=>event.type==='tool/call')
        &&(/按月|逐月|每个?月|各月|月度/.test(normalized)&&/本年度?|今年|当年度?|去年|(?:19|20)\d{2}\s*年/.test(normalized)
          &&/(?:件数|条数|数量|案卷数|记录数)/.test(normalized)
          ||/政务(?:服务)?事项|政务服务/.test(normalized)&&/(?:按时|准时)办结率|办结时效/.test(normalized)
          ||/全(?:[\p{Script=Han}]{2,4})?省|本省/u.test(normalized)&&/资质/.test(normalized)&&/单位(?:数|的数量)|几家|多少家/.test(normalized)))
        return {tool:{name:'data_catalog',args:{question:text}}};
      if(!current.some(event=>event.type==='tool/call')&&/按月|逐月|每月/.test(normalized)&&/比较|对比/.test(normalized)
        &&/并列|分列|两列/.test(normalized)&&new Set(normalized.match(/(?:19|20)\d{2}(?=\s*年)/g)).size===2)
        return {tool:{name:'data_catalog',args:{question:text}}};
      if(!current.some(event=>event.type==='tool/call')&&/^(?:本年度?|今年|当年|20\d{2}\s*年)/.test(normalized)
        &&/最多|最少/.test(normalized)&&/(?:数据|记录|观测)的?(?:截至|截止)/.test(normalized))
        return {tool:{name:'data_catalog',args:{question:text}}};
    }
    if(options.tools?.some(tool=>tool.name==='data_catalog')&&options.tools.some(tool=>tool.name==='data_sql_query')){
      const tool=historicalQualificationTool(user,events,current)||currentExecutionTool(events,current)
        ||publishedCatalogQueryTool(user,current,'annual_fiscal_history_query')
        ||publishedCatalogQueryTool(user,current,'project_spending_query')
        ||publishedReportBudgetTool(events,current,agent.id)||publishedGroupingTool(events,current)
        ||eventDimensionTool(user,current)||snapshotForecastTool(user,current)
        ||publishedCatalogQueryTool(user,current,'relative_monthly_count_query')
        ||publishedCatalogQueryTool(user,current,'annual_monthly_count_query')
        ||publishedCatalogQueryTool(user,current,'approval_monthly_count_query')
        ||publishedCatalogQueryTool(user,current,'province_snapshot_count_query')
        ||publishedCatalogQueryTool(user,current,'service_completion_query')
        ||publishedCatalogQueryTool(user,current,'published_observed_ranking_query')
        ||publishedCatalogQueryTool(user,current,'cancellation_ranking_query')
        ||publishedCatalogQueryTool(user,current,'monthly_count_comparison_query')
        ||publishedCatalogQueryTool(user,current,'monthly_group_share_query');
      if(tool)return {tool};
    }
    if(options.tools?.some(tool=>tool.name==='data_metric_query')){
      const stock=snapshotHistoryTool(user,current);
      if(stock)return {tool:stock};
    }
    if(!closingState(agent,start.data.turn)&&options.tools?.some(tool=>tool.name==='data_sql_query')
       &&!current.some(event=>event.type==='tool/call'&&event.data.name!=='data_catalog')){
      const plan=formulaComponentPlan(catalogEvidence,user);
      if(plan)return {tool:{name:'data_sql_query',args:plan.query_request}};
    }
    if(!comparison&&!closingState(agent,start.data.turn)){
      const tool=dimensionBusinessQuestionTool(current,user,options.tools)||registeredHoldingRateTool(user,current,options.tools);
      if(tool)return {tool};
      if(monthlyMetric&&options.tools?.some(tool=>tool.name==='data_metric_query')
        &&!current.some(event=>event.type==='tool/call'&&event.data.name!=='data_catalog')){
        return {tool:{name:'data_metric_query',args:monthlyMetric.query_request}};
      }
    }
    const independentStep=comparison&&independentComparisonStep(comparison,current,options.tools);
    if(independentStep&&!closingState(agent,start.data.turn))return {tool:independentStep};
    const grouped=comparison?.comparison.categories&&independentMetricReceipt(current,comparison,
      comparison.comparison.query_request,comparison.comparison.categories);
    if(options.tools?.some(tool=>tool.name==='analysis_compute')){
      const closing=closingState(agent,start.data.turn);
      const tool=comparison?.comparison.categories&&!grouped?null:pendingPeriodCalculation(current,text);
      // The host assembles this step's authorized tools before a progress stop.
      // Only the one outstanding comparison may resume that step.
      if(tool&&(!closing||tool.args.operation==='period_compare'&&resumeQueryRepair(agent,start.data.turn)))return {tool};
      if(!closing&&!comparison&&!current.some(event=>event.type==='tool/call'&&event.data.name==='ask_user_question')){
        const share=groupedCountShareTool(text,typedReceipts(current,formalQueryTools),user.data.id,agent.id);
        if(share&&!attemptedCountShare(current,share.args))return {tool:share};
      }
    }
    const evidenceForComparison=grouped&&activeEvidence(typedReceipts(current));
    const boundGrouped=evidenceForComparison&&evidenceForComparison.find(result=>result.result_ref===grouped.result_ref);
    const compared=boundGrouped&&groupedComparisonSection({...grouped,claims:boundGrouped.claims},text);
    const verifiedComparison=compared&&receiptSectionText(compared);
    const independentQuestion=comparison&&independentComparisonStep(comparison,current,options.tools,{offerChoice:true});
    if(independentQuestion?.name==='ask_user_question'&&!closingState(agent,start.data.turn)){
      const preview=comparison.comparison.categories
        ?verifiedComparison&&checkAnswer(verifiedComparison,evidenceForComparison,text).every(issue=>/^交付缺项：/.test(issue))
          ?verifiedComparison:null
        :verifiedPartialSections(events,agent,current);
      return {tool:independentQuestion,preview};
    }
    const selectedDelivery=comparison?.comparison.categories
      ?verifiedIndependentGroupedChoiceAnswer(comparison,current,user,agent.id,grouped,verifiedComparison)
      :verifiedIndependentChoiceAnswer(comparison,current,user,agent.id);
    if(selectedDelivery){
      const checked=await reviewCandidate({agent,turn:start.data.turn,signal,read,answerOverride:selectedDelivery});
      if(checked&&!checked.issues.length)return {text:selectedDelivery};
    }
    const evidence=activeEvidence(typedReceipts(current,publishedCountTools));
    const factualEvidence=activeEvidence(typedReceipts(current));
    const forecastClosing=closingState(agent,start.data.turn);
    if(options.tools?.some(tool=>tool.name==='analysis_method')
      &&(!forecastClosing||forecastClosing.reasonCode==='query-progress')&&!pendingUserChoice(current)
      &&!current.some(event=>event.type==='tool/call'&&event.data.name==='analysis_method')){
      const forecast=checkedMonthlyForecastTool(text,typedReceipts(current),user.data.id)
        ||checkedAnnualForecastTool(text,typedReceipts(current),user.data.id);
      // A progress stop may follow unrelated failed tools in the same step.
      // Resume exactly the proven calculation, using the already assembled tool.
      if(forecast&&(!forecastClosing||resumeQueryRepair(agent,start.data.turn)))return {tool:forecast};
    }
    const empty=groupedEmptyDelivery(text,[user,...current],options.tools);
    if(empty?.tool)return empty;
    const choice=pendingUserChoice(current);
    if(choice){
      const delivered=verifiedPartialSections(events,agent,current);
      if(previouslyDeliveredSections(current,delivered)&&!recoverableQueryIssues(current).length
        &&checkAnswer(delivered,activeEvidence(typedReceipts(current)),text).length===0){
        return {text:choice.endsWith('选项暂时无法显示，仍需确认口径。')?delivered+'\n\n'+choice:choice};
      }
    }
    const retained=!choice&&retainedCompletionSummary(events,start,evidence,agent.id);
    if(retained){
      const checked=await reviewCandidate({agent,turn:start.data.turn,signal,read,answerOverride:retained.text,
        retainedRef:retained.retainedRef});
      if(checked&&!checked.issues.length)return {text:retained.text};
    }
    const spendingAnswer=!choice&&reportedSpendingDelivery(activeEvidence(evidence),user.data.id,agent.id);
    const delivery=(spendingAnswer&&!spendingAnswer.unresolved&&spendingAnswer.text)
      ||historicalQualificationAnswer(user,current,agent.id)
      ||verifiedPublishedCountAnswer(text,evidence,{requestRef:user.data.id,presentation:view})
      ||verifiedForecastAnswer(text,typedReceipts(current),{requestRef:user.data.id,sessionId:agent.id})
      ||verifiedForecastGap(text,factualEvidence,current,user.data.id,agent.id)
      ||verifiedCurrentExecution(text,evidence,user.data.id,agent.id)
      ||verifiedProjectSpending(evidence,user.data.id,agent.id)
      ||verifiedCurrentReportBudget(evidence,user.data.id,agent.id)
      ||registeredHoldingRateAnswer(user,current,evidence,agent.id)
      ||(!choice&&verifiedCancellationAnswer(text,evidence,user.data.id,agent.id))
      ||verifiedRankingAnswer(text,evidence,user.data.id,agent.id)
      ||verifiedPublishedMonthlyMetricAnswer(monthlyMetric,factualEvidence)
      ||(!choice&&verifiedYearComparisonTable(text,activeEvidence(typedReceipts(current,formalQueryTools)),user.data.id,agent.id))
      ||(!choice&&verifiedMonthlyGroupShare(text,evidence,user.data.id,agent.id))
      ||verifiedSqlAnnualRefinement(monthlyObservationState(user,current)?.year,evidence)?.text
      ||verifiedSqlMonthlyAnswer(text,evidence)?.text
      ||(!choice&&verifiedGroupedMeasureAnswer(text,typedReceipts(current,formalQueryTools),user.data.id,agent.id))
      ||groupedCountShareAnswer(text,evidence,user.data.id,agent.id,verifiedSqlShareReceipts(current))
      ||(!choice&&formulaComponentDelivery(events,user,agent.id,typedReceipts(current,formalQueryTools),factualEvidence)?.text)
      ||empty?.text
      ||historicalCertificateBoundaryAnswer(user,current)
      ||annualForecastBoundaryAnswer(user,current)
      ||eventDimensionAnswer(user,current,agent.id)
      ||snapshotForecastAnswer(user,current,agent.id)
      ||queryBoundaryAnswer(agent,start.data.turn,text,current);
    if(delivery){
      const checked=await reviewCandidate({agent,turn:start.data.turn,signal,read,answerOverride:delivery});
      if(checked&&!checked.issues.length)return {text:delivery};
    }
    const observation=requestedObservation(user,agent,current,text);
    if(observation?.kind==='rank'||observation?.kind==='year'){
      deliveredObservations.set(agent,start.data.turn);
      return {text:observation.text};
    }
    if(!choice&&!closingState(agent,start.data.turn)&&!clarificationEvidence(current).clarifications.length
      &&exhaustedQueryRepair(agent,start.data.turn,current)){
      const budget=currentComparisonBudget(current,user,agent.id);
      if(budget&&comparisonHistoryExhausted(current,budget.plan.source_id)){
        const verified=comparisonBudgetPartial(budget,typedReceipts(current,formalQueryTools).filter(result=>!isExploratory(result)));
        if(verified&&!checkAnswer(verified,[budget.result],text).length){
          deliveredObservations.set(agent,start.data.turn);
          return {text:verified};
        }
      }
    }
    return null;
  },settleDiscovery(agent,events,start,calls){
    const catalogs=calls.every(call=>call.name==='data_catalog');
    const queries=calls.every(call=>call.name==='data_sql_query');
    if(!calls.length||!(catalogs||queries)||closingState(agent,start.data.turn))return null;
    const user=requestContext(events).users.at(-1);
    if(user?.data.turn!==start.data.turn)return null;
    const current=events.filter(event=>event.seq>user.seq);
    if(pendingUserChoice(current)||clarificationEvidence(current).clarifications.length)return null;
    const verified=currentComparisonBudget(current,user,agent.id);
    if(!verified)return null;
    const formal=typedReceipts(current,formalQueryTools).filter(result=>result.status==='succeeded'
      &&result.result_ref&&!isExploratory(result));
    if(catalogs){
      const coverageBuild=comparisonCoverageBuild(verified,current,user);
      const reads=calls.map(call=>toolArguments({data:call}));
      const repeatedAfterCoverage=coverageBuild
        &&reads.every(args=>redundantComparisonCoverageRead(verified.plan,current,args,coverageBuild));
      // A failed query does not settle another question. Repeating an already
      // completed discovery adds nothing after both scoped checks ran.
      if(!repeatedAfterCoverage){
        if(!reads.every(args=>broadComparisonCatalog(args,verified.plan.source_id)))return null;
        const broad=typedReceipts(current,catalogTools,call=>broadComparisonCatalog(toolArguments(call),verified.plan.source_id));
        const otherCompleted=formal.some(result=>result.result_ref!==verified.result.result_ref
          &&result.result_lineage?.request_ref===user.data.id&&result.result_lineage.session_id===agent.id
          &&result.result_lineage.source_id===verified.plan.source_id&&result.values?.complete===true);
        if(!(otherCompleted&&comparisonCatalogExhausted(verified.plan,broad)))return null;
      }
    }else if(!comparisonRepairsBlocked(calls,events,current,verified.plan))return null;
    const text=comparisonBudgetPartial(verified,formal);
    if(!text||checkAnswer(text,[verified.result],requestText(events)).length)return null;
    deliveredObservations.set(agent,start.data.turn);
    return text;
  },async compose(agent,events,start,signal){
    if(!start)return null;
    const current=events.filter(event=>event.seq>=start.seq);
    const evidence=typedReceipts(current),question=requestText(events);
    const user=requestContext(events).users.at(-1);
    const historical=user?.data.id&&user.data.turn===start.data.turn&&historicalQualificationAnswer(user,current,agent.id);
    const ranked=user?.data.id&&user.data.turn===start.data.turn&&verifiedRankingAnswer(question,evidence,user.data.id,agent.id);
    const cancellation=user?.data.id&&user.data.turn===start.data.turn&&!pendingUserChoice(current)
      &&verifiedCancellationAnswer(question,evidence,user.data.id,agent.id);
    const snapshot=user?.data.id&&user.data.turn===start.data.turn&&snapshotForecastAnswer(user,current,agent.id);
    const forecast=verifiedForecastAnswer(question,evidence,{requestRef:user?.data.id,sessionId:agent.id});
    const monthlyTotal=user?.data.id&&user.data.turn===start.data.turn&&!pendingUserChoice(current)
      &&verifiedPublishedMonthlyMetricAnswer(null,activeEvidence(typedReceipts(
        current.filter(event=>event.seq>user.seq),formalQueryTools)),question);
    const work=user?.data.id&&user.data.turn===start.data.turn&&!pendingUserChoice(current)
      &&!clarificationEvidence(current).clarifications.length&&evidence.some(receipt=>receipt.answer_contract?.work_inventory)
      &&completionInventoryAnswer(question,typedReceipts(current,formalQueryTools),user.data.id,agent.id);
    const monthlyShare=user?.data.id&&user.data.turn===start.data.turn&&!pendingUserChoice(current)
      &&verifiedMonthlyGroupShare(question,evidence,user.data.id,agent.id);
    const delivery=historical||cancellation||ranked||monthlyTotal||monthlyShare||verifiedSqlMonthlyAnswer(question,activeEvidence(evidence))?.text||forecast||snapshot||work
      ||(user?.data.id&&user.data.turn===start.data.turn&&!pendingUserChoice(current)
        &&verifiedGroupedMeasureAnswer(question,typedReceipts(current,formalQueryTools),user.data.id,agent.id));
    if(!delivery)return null;
    const verified=await reviewCandidate({agent,turn:start.data.turn,signal,read,answerOverride:delivery});
    return verified&&!verified.issues.length?delivery:null;
  },async composePartial(agent,events,start,signal,failure,candidate){
    signal.throwIfAborted();
    const current=events.filter(event=>event.seq>=start.seq);
    const question=requestText(events);
    const user=requestContext(events).users.at(-1);
    const partial=receiptDelivery(events,agent,question,current,failure,candidate);
    if(partial){
      if(partial.verifiedObservation)deliveredObservations.set(agent,start.data.turn);
      return partial;
    }
    const observation=requestedObservation(user,agent,current,question);
    if(observation){
      deliveredObservations.set(agent,start.data.turn);
      return {text:observation.text,unresolved:true,verifiedObservation:true};
    }
    // A rejected answer can cite several incompatible or incompletely bound
    // reads. Even when they cannot be reconciled, deliver the individually
    // verified budget reference and track the remaining parts as incomplete.
    const budget=failure?.issues?.length&&!pendingUserChoice(current)&&currentComparisonBudget(current,user,agent.id);
    if(budget){
      const text=comparisonBudgetPartial(budget);
      if(text&&!checkAnswer(text,[budget.result],question).length){
        deliveredObservations.set(agent,start.data.turn);
        return {text,unresolved:true,verifiedObservation:true};
      }
    }
    const text=unsuccessfulQueryAnswer(current);
    return text?{text,unresolved:true}:null;
  },async validate(agent,text,signal){
    const turn=agent.session.snapshotEvents().findLast(e=>e.type==='turn/start')?.data.turn;
    return (await reviewCandidate({agent,turn,signal,read,answerOverride:text}))?.issues||[];
  },async repair(agent,text,signal){
    const events=agent.session.snapshotEvents();
    const start=events.findLast(e=>e.type==='turn/start');
    const turn=start?.data.turn,userText=requestText(events);
    const polished=polishUnavailableDelivery(text,events,{removeTechnical:presentationIssues(text,[],userText).some(issue=>issue.includes('内部编号'))});
    const request=requestContext(events).users.at(-1);
    const current=events.filter(e=>e.seq>=start?.seq&&e.data?.turn===turn);
    const evidence=activeEvidence(typedReceipts(current,formalQueryTools)).filter(result=>!result.result_lineage
      ||result.result_lineage.session_id===agent.id&&result.result_lineage.request_ref===request?.data.id);
    let revised=repairProsePeriodTable(dropContradictoryMeanNotes(polished,evidence,userText),evidence);
    // 0.8.2: a forecast answer leads with the conclusion; the training series
    // lives in the result card. Replace a dumped listing with the canonical
    // delivery built from this turn's single forecast claim.
    const forecastReceipts=typedReceipts(current).filter(result=>!isExploratory(result));
    if(forecastDeliveryIssues(revised,forecastReceipts,userText).length){
      const canonical=forecastClaimDelivery(forecastReceipts);
      if(canonical)revised=canonical;
    }
    const comparison=revised.trim()&&!pendingUserChoice(current)&&currentComparisonBudget(current,request,agent.id);
    if(comparison){
      const wording=comparisonBudgetWording(revised,comparison);
      if(!wording.shown&&!wording.overbroad)revised=comparisonBudgetParagraph(comparison)+'\n\n'+revised.trimStart();
    }
    if(revised===text)return null;
    const checked=await reviewCandidate({agent,turn,signal,read,answerOverride:revised});
    return checked&&!checked.issues.length?revised:null;
  },record(session,value){
    mkdirSync(directory,{recursive:true,mode:0o700});
    writeFileSync(directory+session+'-'+value.turn+'-withheld-'+Date.now()+'.json',JSON.stringify(value),{mode:0o600});
  }});
  ctx.on('agent/turn-stopping',async({agent,turn,signal})=>{
    if(signal.aborted)return;
    if(deliveredObservations.get(agent)===turn){
      deliveredObservations.delete(agent);
      pending.delete(agent.id);
      return;
    }
    const withheld=pending.get(agent.id);
    const review=await reviewCandidate({agent,turn,signal,read,answerOverride:withheld?.turn===turn?withheld.text:undefined});
    if(!review)return;
    let {issues}=review;
    const {evidence,current,userText,preserve,answer,invocationIssues,queryExhausted,pendingChoice,formulaRead}=review;
    // 0.6.4: up to two corrections. The second one asks to remove what still cannot be
    // verified and say so plainly; whatever remains is listed under the answer.
    const attempts=current.filter(e=>e.type==='user/message'&&e.data.source?.kind==='natural-work-answer-review').length;
    if(issues.length&&invocationIssues.length&&attempts<2&&!queryExhausted)resumeQueryRepair(agent,turn);
    if(withheld?.turn===turn)issues=[...new Set([...issues,...withheld.issues])];
    const unverified=unverifiedValues(issues);
    const claimCorrection=userClaimCorrection(userText,unverified);
    mkdirSync(directory,{recursive:true});
    const recordPath=directory+agent.id+'-'+turn+'.json';
    let prior=null;try{prior=existsSync(recordPath)?JSON.parse(readFileSync(recordPath,'utf8')):null;}catch{prior=null;}// A damaged record must not stop the turn.
    const has_result=typedReceipts(current,formalQueryTools).some(result=>result.status==='succeeded'
      &&result.result_ref&&result.row_count>0&&!isExploratory(result)&&result.validation?.state!=='conflict');
    writeFileSync(recordPath,JSON.stringify({first_issues:prior?.first_issues||issues,session:agent.id,turn,issues,unverified,
      has_result,repair_attempted:attempts>0,attempts,status:issues.length||pendingChoice?'partial':'checked',checks:'bounded semantic rules; not full semantic certification'},null,2));
    if(formulaRead&&attempts<2&&!queryExhausted){
      agent.steer(createUserMessage({source:{kind:'natural-work-answer-review',form:'notice',summary:FORMULA_READ_NOTICE},
        content:[{type:'text',text:'已恢复本轮一次正式查询机会。'+formulaRead}]}));
      return;
    }
    const exhaustedInstruction=queryExhausted?'当前查询已进入收尾。基于现有回执写出可核实的结论，并说明具体缺项。无需重新执行原 SQL。':'';
    if(issues.length&&attempts===1){
      const correction=exhaustedInstruction||(invocationIssues.length?'这是尚未执行的查询，请按最近的参数错误修正调用，完成后基于结果回答；不要重复原来的错误参数。':unverified.length?(claimCorrection+'其他没有查询依据的数值请删除，如实说明未能取得，不要估算、补全或换一个数。'):'保留已核实的数字，只修正上述对应的表述、列表或多余技术信息；不要把文案问题改写成未取得数据。');
      agent.steer(createUserMessage({source:{kind:'natural-work-answer-review',form:'notice',summary:'修正仍未通过的回答项'},content:[{type:'text',text:'修正后仍有问题：'+issues.join('；')+'。'+correction+preserve+'只用正式答复回执中的数字重写回答；purpose=explore 仅用于核对取值，范围可能不同，不得把其记录数作为原问题的统计结果。全部用简体中文，直接回答业务结论，不复述修正过程。'}]}));
      return;
    }
    if(issues.length&&!attempts){
      const verified=evidence.filter(e=>e.purpose!=='explore').flatMap(e=>[e.answer_contract?.monthly_observations?.verified_text,...(e.claims||[]).map(c=>c.verified_text)].filter(Boolean));
      const follow=exhaustedInstruction||'若有可恢复的查询缺项，按工具说明修正参数或执行必要查询；其余只修正文案，不重放已完成的查询。';
      agent.steer(createUserMessage({source:{kind:'natural-work-answer-review',form:'notice',summary:'核对回答与已验证数据'},content:[{type:'text',text:'回答一致性检查发现：'+issues.join('；')+'。请修正：查询结论只能使用正式答复回执中的数字，读不到就如实说明。purpose=explore 仅用于核对取值，范围可能不同，不得把其记录数作为原问题的统计结果。'+claimCorrection+preserve+follow+'直接引用已核算结论，不能确认的解释删除并说明未完成。面向用户直接给出业务答案，不复述本检查、修正文案或重放查询等处理过程；除非用户明确要求技术追溯，不显示内部编号、口径版本或方法版本；不介绍本次未使用的方法。已有错误回答保留在记录中。\n'+verified.join('\n')}]}));
    }
  });
}

export {stitchContinuationMarkdown} from './continuation-markdown.js';
