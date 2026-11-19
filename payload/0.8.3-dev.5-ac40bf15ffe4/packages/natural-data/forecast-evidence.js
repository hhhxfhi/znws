// Only computed claims can establish a forecast; keep each value tied to its period.
import {isDeepStrictEqual} from 'node:util';
import {isExploratory} from './presentation-intent.js';
import {isContractConfirmed} from './metric-semantics.js';
import {forecastAmountLabel,forecastBasisPresentation,forecastErrorLabel,forecastMethodPresentation,isCurrencyForecastUnit} from './forecast-presentation.js';

const datePattern = /(?<![\d.−-])(?:\d{4}\s*年\s*\d{1,2}\s*月(?:份)?(?:\s*\d{1,2}\s*[日号])?|\d{4}-Q[1-4]|\d{4}[-/]\d{2}(?:[-/]\d{2})?|\d{4}\s*年(?:度)?|\d{4}(?=\s*(?:预测|预估|预计)(?:值|量|结果|结论|\s|$)))/gi;
const forecastWord = /预测|预估|预计|展望/;
const negated = /(?:无法|不能|尚未|未能|不作|不做|没有|不足以|不提供)[^，。；;]{0,16}(?:预测|预估|预计|展望)|例如|假设|用户所说|请提供.*版本/;
const historyWord = /历史|实际(?:训练|观测)|训练(?:从|覆盖|期间)?|截至|截止/;
const intervalWord = /(?:预测|预估|置信)(?:的)?(?:区间|范围)|区间[：:]|区间\s*\|/;
const methodDetails = /(?:预测|预估)(?:模型|方法|步数|期限)|回测|候选模型|训练窗口/;
const asserted = /(?:预测|预估|预计)(?:值|量|结果|为|区间|范围|路径|\s|[\d今年本明下])|(?:预测|预计|预估)[^，,]{0,30}(?:约为?|为|达到|等于|[：:])/;
const sampleSize = /(?:已完成预测[，,]\s*使用|实际训练(?:使用|包含)|共)\s*(\d+)\s*(?:个(?:连续)?(?:月份|月|观测|有效数据|历史期|完整年度)|个月|条有效数据|期)/g;
const missing = '预测缺少执行依据：历史月份的值、人工估算或方法名称不能作为预测结果。请补齐同口径连续序列并执行预测方法，采用默认自动选择；返回目标期间、回测与区间。取数或参数失败须如实列为未完成，不能等同数据库缺失，也不应让用户先选择算法。';
const wrongPeriod = '预测期间与计算回执不符；不得把其他期间的预测值改写为用户目标期。请按真实历史末期重新计算到目标期间。';
const wrongValue = '预测数值与实际计算回执不符；仅报告对应目标期间的预测值和区间，不能用历史值、任意范围或其他期间代替。';
const explicitActualAmount = /实际(?:的)?(?:财政(?:资金)?)?(?:投入|投资|拨付|支出|出资)|(?:财政|政府)(?:资金)?(?:实际)?(?:投入|拨付|支出)/;
const allSourcesAmount = /(?:全部来源|各来源|全口径|非财政(?:资金)?)(?:的)?(?:投资|投入)|(?:全部|总)(?:投资|投入)|(?:投资|投入)(?:金额|额)?[（(\s]*(?:含|包含|包括)非财政/;
const annualAmountNumber = new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2});

const requestVerb = /(?:预测|预估|预计|展望)(?!值|区间|范围|路径|曲线|模型|方法|表现|结果)/;
const outputVerb = /^(?:请\s*)?(?:并且?|同时)?\s*(?:报告|给出|给我|给|提供|说明|列出|输出|附上)\s*/;
const outputItem = /^(?:点?(?:预测|预估)(?:值|结果)|(?:95\s*%\s*)?(?:(?:预测|置信)\s*)?区间|(?:历史)?回测(?:表现|结果|误差|指标|情况)?|模型(?:选择)?|样本(?:限制|说明)|(?:目前|本次|现有)?(?:能|可|已)(?:经)?(?:核实|查到|查询)(?:到|的)?(?:哪些)?(?:历史)?(?:数据|金额|记录))$/;
const extraTask = /另外|顺便|分别|各自|同比|环比|对比|比较|排名|排行|明细|导出|下载|图表|折线|柱状图|条形图|绘图|表格|列表|逐(?:月|年|季)(?:的)?预测|预测(?:路径|曲线|序列|趋势)|每(?:个)?月(?:的)?预测|各月(?:的)?预测|(?:未来|后续)\s*[一二三四五六七八九十\d]+\s*(?:期|个月|年)|原因|建议|技术详情|因果|不要|不用|不必|无需|只要|仅需/;
const inputAction = /查(?:询|看|找)|列出|给出|统计|计算|说明|展示|分析/;

// 0.8.2: a request can announce the task first and carry the target later
// (“请你预测基于近三年的X情况，预测本年度12月份的批准量”), and the target year can
// be relative. Parse every forecast verb; the claim's own target period picks
// the reading, so no verb order or year style is hardcoded.
const relativeYears='本年度|本年|今年|明年|下年度|下一年度|下一年|下年';
const requestDate=new RegExp('^(?:一下\\s*)?((\\d{4})\\s*年\\s*(\\d{1,2})\\s*月(?:份)?'
 +'|('+relativeYears+')\\s*(\\d{1,2})\\s*月(?:份)?'
 +'|(\\d{4})[-/](\\d{2})(?![-/\\d])|(\\d{4})\\s*年(?:度)?'
 +'|('+relativeYears+')(?!\\s*(?:\\d{1,2}\\s*月|\\d|年|Q[1-4])))');

export function requestTarget(userText,now) {
 if (typeof userText !== 'string') return null;
 const text = userText.normalize('NFKC').trim();
 if (extraTask.test(text)) return null;
 // Drop negated forecast clauses ("…，不做2028年预测") so a cancelled target
 // neither blocks the remaining real target nor becomes one itself.
 const cleaned = text.replace(/(?:不做|无需|不要|不必|不作|不需|取消|不|无意)[^，,。；;\n]{0,12}(?:预测|预估|预计|展望)[^，,。；;\n]*/g, '');
 for(const intent of cleaned.matchAll(new RegExp(requestVerb.source,requestVerb.flags+'g'))){
  // Mirror the engine negation window: a short negated phrase before the verb
  // within the same comma/sentence clause cancels the forecast intent.
  const clause = cleaned.slice(0,intent.index).split(/[，,。；;\n]/).pop();
  if (/(?:不做|无需|不要|不必|不作|不需|取消|不|无意)[^，,。；;\n]{0,12}$/.test(clause)) continue;
  const parsed=parseRequestAt(cleaned,intent.index,intent.index+intent[0].length,now);
  if(parsed)return {...parsed,text};
 }
 return null;
}

function parseRequestAt(text,start,end,now) {
 const input = text.slice(0, start).trim().replace(/^(?:请(?:你)?|帮我|能否|可以帮我)\s*/, '');
 const inputBody=input.replace(/[，,]\s*$/, '');
 if (input && (/(?:无法|不能|未能|不作|不做|没有|暂缓)\s*$/.test(input)
     || !/^(?:预测|预估|预计|展望)?\s*(?:基于|根据|用|使用|依据|按|参照|结合|截至|截止到?|查询|查看|统计|列出|取)/.test(input)
       &&!/^以[^，,。]+(?:为|作为)(?:依据|基础|参考)$/.test(inputBody)
     || /[，,。；;!?\n]|(?:并|同时|也|还|再)\s*(?:请)?$/.test(inputBody))) return null;
 const tail = text.slice(end).trim();
 const date = tail.match(requestDate);
 if (!date) return null;
 const relative=date[4]||date[9];
 const year=relative?( /明|下/.test(relative)?(now??new Date()).getFullYear()+1:(now??new Date()).getFullYear() )
   :Number(date[2]||date[6]||date[8]);
 const month=date[3]||date[5]||(date[7]&&Number(date[7]));
 if(!Number.isInteger(year)||year<1||year>9999)return null;
 const period=month?`${year}-${String(month).padStart(2,'0')}`:String(year);
 const grain = month ? 'month' : 'year';
 if (calendarIndex(period, grain) === null) return null;
 const rest = tail.slice(date[0].length).replace(/[。!?]+$/, '').trim();
 if (/^\s*(?:\d|[一二三四五六七八九十]+月|至|到|[-—–~～])/.test(rest) || [...rest.matchAll(datePattern)].length) return null;
 const clauses = rest.split(/[，,。；;!?\n]/).map(clause => clause.trim()).filter(Boolean);
 let subject = clauses.shift() || '';
 if (outputVerb.test(subject)) {clauses.unshift(subject);subject='';}
 const combined = subject.match(/(?:\s*(?:并且?|同时)\s*)(?=报告|给出|给我|给|提供|说明|列出|输出|附上)/);
 if (combined) {clauses.unshift(subject.slice(combined.index));subject=subject.slice(0,combined.index).trim();}
 subject = subject.replace(/^的\s*/, '').replace(/是多少$|有多少$|为多少$/, '').trim();
 if (subject && (!/^[\p{Script=Han}A-Za-z\s（）()·]{1,60}$/u.test(subject)
      || /和|与|或|以及|还要|再查|并且?|同时|给出|列出|查询|统计|计算|分析|说明|提供|报告|各(?:地|市|类|项|单位|地区)/.test(subject))) return null;
 if (clauses.length) {
  const outputs = clauses.join('、').replace(outputVerb, '').split(/、|以及|和|及|与/);
  if (outputs.some(item => !outputItem.test(item.trim().replace(outputVerb, '')))) return null;
 }
 return {period,grain,subject,text,inputBody,explicitTrainingWindow:/\d{4}|(?:近|过去|最近)\s*[一二两三四五六七八九十\d]|截至|截止/.test(inputBody)};
}

export function annualForecastCatalogTool(question) {
 const text=String(question||'').normalize('NFKC').trim();
 const verbs=[...text.matchAll(new RegExp(requestVerb.source,'g'))];
 if(verbs.length===1&&verbs[0].index>0){
  const start=verbs[0].index,history=text.slice(0,start);
  const forecast=requestTarget(text.slice(start));
  if(forecast?.grain==='year'&&annualHistoryRequest.test(history)&&!extraTask.test(history)
    &&/(?:预算|投资|投入|拨付|支出)/.test(history)
    &&!referencedHistory.test(history))return {name:'data_catalog',args:{question}};
 }
 const request=requestTarget(question);
 if(request?.grain!=='year'||referencedHistory.test(request.inputBody))
  return null;
 const range=request.inputBody.match(/(?<!\d)(\d{4})\s*年?\s*(?:至|到|[-–—])\s*(\d{4})\s*年/);
 if(!range)return null;
 const first=calendarIndex(range[1],'year'),last=calendarIndex(range[2],'year');
 if(first===null||last===null||last-first<4||last>=calendarIndex(request.period,'year'))return null;
 return {name:'data_catalog',args:{question}};
}

const referencedHistory=/附件|文档|工作簿|上表|下表|如下|我(?:给|提供|上传)|刚才|前面|之前|本轮|上一轮|上述|前述|已有结果/;

function calendarIndex(period, grain, row = false) {
 const value = String(period ?? '');
 if (grain === 'year') return /^\d{4}$/.test(value) && +value > 0 ? +value : null;
 const date = row && /^(\d{4}-\d{2})-\d{2}$/.exec(value);
 if (date) {
  const stamp = Date.parse(value+'T00:00:00Z');
  if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0,10)!==value) return null;
 }
 const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(date ? date[1] : value);
 return match && +match[1] > 0 ? +match[1] * 12 + +match[2] : null;
}

function monthlyRequest(question,result,subject) {
 const target=result?.validation?.request_scope?.forecast_target;
 if (!target || target.grain!=='month' || target.evidence?.quote!==question || result.status!=='succeeded'
     || result.truncated || isExploratory(result) || result.receipt?.read_only!==true
     || result.validation?.state==='conflict' || result.values?.complete!==true
     || !target.evidence?.user_ref || result.result_lineage?.request_ref!==target.evidence.user_ref
     || (target.evidence?.clarifications||[]).length || calendarIndex(target.period,'month')===null) return null;
 if(target.horizon_start!==undefined){
  const start=calendarIndex(target.horizon_start,'month');
  if(start===null||calendarIndex(target.period,'month')-start!==11)return null;
  return {period:target.period,grain:'month',subject:'',horizonStart:target.horizon_start,text:question.normalize('NFKC').trim()};
 }
 const normalized=question.normalize('NFKC').trim()
  .replace(/^(请(?:你)?\s*)?(?:预测|预估)(?=\s*(?:基于|根据|用|按))/, '$1')
  .replace(/(?:本年度|本年|今年|明年|下一年)\s*\d{1,2}\s*月(?:份)?/,cnPeriod(target.period))
  .replace(/[。；;]\s*(?:如|如果)历史(?:数据|序列)?不连续请明确说明[，,]\s*(?:不能|不要|不得)把未记录月份补成零[。!！]?$/, '');
 const request=requestTarget(normalized);
 if(request?.period!==target.period||subject!==undefined&&(!subject
     ||request.subject&&request.subject!==subject))return null;
 return {...request,text:question.normalize('NFKC').trim()};
}

function observedMonths(result,timeColumn,valueColumn) {
 const {columns,rows}=result.values;
 if (!Array.isArray(columns)||!Array.isArray(rows)||rows.length!==result.row_count||rows.length!==result.values.row_count
     || rows.length>500) return null;
 const time=columns.indexOf(timeColumn), measure=columns.indexOf(valueColumn);
 if (time<0||measure<0||time===measure||columns.lastIndexOf(timeColumn)!==time||columns.lastIndexOf(valueColumn)!==measure) return null;
 const periods=[];
 for (const row of rows) {
  if (!Array.isArray(row)||!Number.isFinite(row[measure])) return null;
  const index=calendarIndex(row[time],'month',true);
  if (index===null) return null;
  periods.push(index);
 }
 periods.sort((a,b)=>a-b);
 return new Set(periods).size===periods.length?periods:null;
}

const monthFromIndex=index=>String(Math.floor((index-1)/12)).padStart(4,'0')+'-'+String((index-1)%12+1).padStart(2,'0');

function gapAnswer(target,coverage) {
 return [coverage,'缺少连续历史，暂无法预测'+cnPeriod(target)+'的数值。缺失月份保留为空。'].join('\n\n');
}

function checkedMonthlyHistory(question,result) {
 const proof=result?.validation?.forecast_history,lineage=result?.result_lineage,pub=result?.business_project;
 const request=monthlyRequest(question,result,proof?.kind==='observed_span'?(proof.subject??''):undefined);
 if(!request||proof?.schema_version!==1||proof.state!=='checked'||proof.target_period!==request.period
   ||!proof.result_ref||proof.result_ref!==result.result_ref||lineage.purpose!=='answer'||proof.request_ref!==lineage.request_ref
   ||!proof.source_id||proof.source_id!==lineage.source_id||proof.source_id!==result.receipt.source_id
   ||!proof.publication?.id||proof.publication.id!==pub?.id||!Number.isSafeInteger(pub.version)
   ||pub.version<1||proof.publication.version!==pub.version)return null;
 const span=proof.kind==='observed_span';
 const {observed_periods:observed,missing_periods:missing}=proof;
 const requested=span?proof.span_periods:proof.requested_periods;
 if(!Array.isArray(requested)||requested.length<2||requested.length>120
   ||!Array.isArray(observed)||!Array.isArray(missing)
   ||proof.kind!==undefined&&!span
   ||span&&(!Number.isSafeInteger(proof.connection_revision)||proof.connection_revision<1
     ||proof.connection_revision!==result.source_provenance?.connection_revision
     ||observed.length<2||observed[0]!==requested[0]||observed.at(-1)!==requested.at(-1)))return null;
 const first=calendarIndex(requested[0],'month'),last=calendarIndex(requested.at(-1),'month');
 const availableFirst=proof.available_start===undefined?first:calendarIndex(proof.available_start,'month');
 if(first===null||last===null||last>=calendarIndex(request.period,'month')||requested.length!==last-first+1
   ||availableFirst===null||availableFirst>first||span&&availableFirst!==first
   ||requested.some((period,i)=>calendarIndex(period,'month')!==first+i)
   ||proof.start_inclusive!==requested[0]+'-01'||proof.end_exclusive!==monthFromIndex(last+1)+'-01')return null;
 const indices=observedMonths(result,proof.time_column,proof.value_column);
 if(!indices||indices.length!==observed.length||indices.some((i,j)=>i<availableFirst||i>last||observed[j]!==monthFromIndex(i)))return null;
 const valueIndex=result.values.columns.indexOf(proof.value_column);
 if(!span&&result.values.rows.some(row=>!Number.isSafeInteger(row[valueIndex])||row[valueIndex]<=0))return null;
 const seen=new Set(observed),gaps=requested.filter(period=>!seen.has(period));
 if(gaps.length!==missing.length||gaps.some((period,i)=>missing[i]!==period))return null;
 return {request,proof,requested,observed,missing,span};
}

export function checkedForecastHistoryAnswer(question,result) {
 const history=checkedMonthlyHistory(question,result);
 if(!history?.missing.length)return null;
 const {request,requested,observed,missing,span,proof}=history;
 const available=!span&&typeof proof.available_start==='string'?proof.available_start:null;
 const within=available?observed.filter(period=>period>=requested[0]).length:observed.length;
 const coverage=span?'本次查到 '+observed.length+' 个月的历史记录，实际观测范围为 '+requested[0]+' 至 '+requested.at(-1)+'。'
   +'其中 '+missing.slice(0,12).join('、')+(missing.length>12?' 等月份':'')+' 缺少记录。':
   '近'+requested.length+'个月（'+requested[0]+' 至 '+requested.at(-1)+'）查到 '+(requested.length-missing.length)
   +' 个有记录的月份，缺 '+missing.length+' 个月。缺少记录的月份为 '+missing.slice(0,12).join('、')
   +(missing.length>12?' 等':'')+'。'
   +(available?'授权历史自 '+available+' 起，另含前置记录 '+(observed.length-within)+' 个月。':'');
 return gapAnswer(request.period,coverage);
}

function snapshotHistory(question,result,requestRef) {
 const proof=result?.validation?.snapshot_forecast_history, publication=result?.business_project;
 const metric=result?.metric_contract, facts=result?.facts, months=proof?.months;
 if(!requestRef||proof?.schema_version!==1||proof.quote!==question||proof.request_ref!==requestRef
   ||!result.result_ref||proof.result_ref!==result.result_ref||result.status!=='succeeded'||
   result.truncated||isExploratory(result)||result.receipt?.read_only!==true||result.validation?.state==='conflict'
   ||proof.source_id!==result.receipt.source_id||!publication?.id||publication.id!==proof.publication?.id
   ||publication.version!==proof.publication?.version||!metric||metric.additive_over_time!==false
   ||!Array.isArray(facts)||facts.length!==result.row_count||!facts.length
   ||!Array.isArray(months)||months.length<2||months.length>120)return null;
 const first=calendarIndex(months[0],'month'), start=calendarIndex(proof.horizon_start,'month');
 if(first===null||start===null||first+months.length!==start
   ||calendarIndex(proof.target_period,'month')!==start+11
   ||months.some((month,i)=>calendarIndex(month,'month')!==first+i))return null;
 const observations=new Map(), seen=new Set();
 for(const {dimensions,value,unit} of facts){
  const period=dimensions?.period;
  if(!months.includes(period)||Object.keys(dimensions).length!==1||seen.has(period)||unit!==metric.unit
    ||(value!==null&&!Number.isFinite(value)))return null;
  seen.add(period);
  if(value!==null)observations.set(period,value);
 }
 const observed=months.filter(month=>observations.has(month));
 return {proof,observed,observations,missing:months.filter(month=>!observations.has(month)),unit:metric.unit};
}

export function snapshotForecastGapAnswer(question,result,requestRef) {
 const history=snapshotHistory(question,result,requestRef);
 if(!history?.missing.length||!history.observed.length)return null;
 const {proof,observed,observations,missing,unit}=history;
 if(typeof unit!=='string'||!unit||/[\r\n|<>]/.test(unit))return null;
 const first=observed[0], last=observed.at(-1);
 return '已查到 '+observed.length+' 个月的月末存量。'+first+' 为 **'+observations.get(first)+' '+unit
  +'**，'+last+' 为 **'+observations.get(last)+' '+unit+'**。\n\n'
  +'同一序列缺少 '+missing.slice(0,12).join('、')+(missing.length>12?' 等月份':'')
  +'，这段历史不足以预测至 '+proof.target_period+' 的走势。';
}

export function checkedMonthlyForecastTool(question,results,requestRef) {
 // Leave specific algorithms and settings to the usual method selection.
 if(/ARIMA|ETS|naive|线性(?:趋势|回归)|移动平均|季节平滑|指数平滑|参数|模型|算法|阶数/i.test(question))return null;
 const formal=results.filter(result=>result?.result_ref&&result.status==='succeeded'&&!isExploratory(result));
 if(new Set(formal.map(result=>result.result_ref)).size!==1)return null;
 const stock=formal[0]&&snapshotHistory(question,formal[0],requestRef);
 if(stock&&!stock.missing.length&&stock.observed.length>=12)return {name:'analysis_method',args:{
   result_ref:formal[0].result_ref,method_id:'matrix.42',time_column:'period',columns:['value'],
   options:{forecast_grain:'month',forecast_model:'auto',forecast_until:stock.proof.target_period}}};
 const source=formal.find(result=>result.validation?.forecast_history);
 const history=source&&checkedMonthlyHistory(question,source);
 if(history&&!history.span&&history.proof.request_ref===requestRef&&!history.missing.length&&history.requested.length>=12
   &&history.observed.every((period,i,all)=>i===0||calendarIndex(period,'month')-calendarIndex(all[i-1],'month')===1)){
  const {request,proof}=history;
  return {name:'analysis_method',args:{result_ref:source.result_ref,method_id:'matrix.42',
    time_column:proof.time_column,columns:[proof.value_column],
    options:{forecast_grain:'month',forecast_model:'auto',forecast_until:request.period}}};
 }
 const result=formal.find(item=>item.values?.columns?.length===2&&item.result_lineage?.request_ref===requestRef);
 const request=monthlyRequest(question,result);
 if(!request?.horizonStart)return null;
 const [timeColumn,valueColumn]=result.values.columns;
 const observed=observedMonths(result,timeColumn,valueColumn);
 if(!observed||observed.length<12||observed.at(-1)!==calendarIndex(request.horizonStart,'month')-1
   ||observed.at(-1)-observed[0]+1!==observed.length)return null;
 return {name:'analysis_method',args:{result_ref:result.result_ref,method_id:'matrix.42',
   time_column:timeColumn,columns:[valueColumn],options:{forecast_grain:'month',forecast_model:'auto',forecast_until:request.period}}};
}

function annualTrendRequest(question,source,proof) {
 if(typeof question!=='string')return null;
 const text=question.normalize('NFKC').trim(), clock=source?.validation?.annual_fiscal_window;
 if(!['actual_fiscal','budget'].includes(proof?.measure_role)||!clock?.current_open_year
   ||!annualHistoryRequest.test(text)||allSourcesAmount.test(text))return null;
 const verbs=[...text.matchAll(new RegExp(requestVerb.source,'g'))];
 if(verbs.length!==1||verbs[0].index===0)return null;
 const start=verbs[0].index;
 const inputBody=text.slice(0,start).replace(/(?:[?。;,，]|\s|并且?|同时|然后|接着|再|请(?:你)?|帮我)+$/u,'');
 if(!annualHistoryRequest.test(inputBody)||extraTask.test(inputBody)||/[?。;！!]/.test(inputBody)
   ||!/(?:预算|投资|投入|拨付|支出)/.test(inputBody)
   ||(/预算/.test(inputBody)&&proof.measure_role!=='budget')
   ||(explicitActualAmount.test(inputBody)&&proof.measure_role!=='actual_fiscal'))return null;
 const request=requestTarget(text.slice(start),new Date(Number(clock.current_open_year),0,2));
 if(request?.grain!=='year'||request.period!==proof.target_period
   ||!/(?:预算|投资|投入|拨付|支出)/.test(request.subject))return null;
 return {...request,text,inputBody,historyRequested:true,explicitTrainingWindow:true};
}

function sameAnnualHistoryWithOpenNull(other,source,proof,observed){
 const {annual_fiscal_window:clock}=source.validation;
 const earlierClock=other.validation?.annual_fiscal_window, lineage=source.result_lineage;
 if(!clock?.current_open_year||!isDeepStrictEqual(clock,earlierClock)
   ||!sameForecastRequest(other,source,{requestRef:lineage.request_ref,sessionId:lineage.session_id})
   ||other.source_provenance?.connection_revision!==proof.connection_revision
   ||other.values?.complete!==true||!Array.isArray(other.values.columns)||!Array.isArray(other.values.rows)
   ||other.values.rows.length!==other.row_count||other.row_count!==observed.size+1)return false;
 const time=other.values.columns.indexOf(proof.time_column),value=other.values.columns.indexOf(proof.value_column);
 if(time<0||value<0||time===value||other.values.columns.lastIndexOf(proof.time_column)!==time
   ||other.values.columns.lastIndexOf(proof.value_column)!==value)return false;
 const remaining=new Map(observed);
 remaining.set(Number(clock.current_open_year),null);
 for(const row of other.values.rows){
  if(!Array.isArray(row)||row.length!==other.values.columns.length)return false;
  const year=calendarIndex(row[time],'year',true);
  if(!remaining.has(year)||remaining.get(year)!==row[value])return false;
  remaining.delete(year);
 }
 return remaining.size===0;
}

function isAnnualSupportQuery(result,source,proof,binding){
 const window=result.validation?.annual_fiscal_window;
 const trainingWindow=source.validation.annual_fiscal_window;
 return result.execution?.operation==='data.sql_query'&&window?.state==='checked'
   &&!window.time_column&&!result.validation.annual_forecast_history
   &&result.values?.complete===true&&Array.isArray(result.values.columns)
   &&!result.values.columns.includes(proof.time_column)
   &&window.basis==='client_calendar'&&window.current_date===trainingWindow?.current_date
   &&result.source_provenance?.connection_revision===proof.connection_revision
   &&sameForecastRequest(result,source,binding);
}

function sameClosedAnnualSeries(other, source, proof, observations, question, allowReference) {
 const lineage=source.result_lineage;
 if(!lineage.population_fingerprint||other.result_lineage?.population_fingerprint!==lineage.population_fingerprint
   ||!sameForecastRequest(other,source,{requestRef:lineage.request_ref,sessionId:lineage.session_id})
   ||!other.validation?.annual_forecast_history)return false;
 const checked=checkedAnnualHistory(question,[other],lineage.request_ref,{allowReference});
 return Boolean(checked&&!checked.missing.length
   &&['measure_label','measure_role','measure_label_basis','unit','value_kind','connection_revision','reference_only']
     .every(key=>checked.proof[key]===proof[key])
   &&isDeepStrictEqual(checked.observations,observations));
}

function checkedAnnualHistory(question,results,requestRef,{allowReference=false}={}) {
 const formal=results.filter(result=>result?.result_ref&&result.status==='succeeded'&&!isExploratory(result)
   &&(result.execution?.operation!=='analysis.method'||result.values?.complete===true));
 const histories=formal.filter(result=>result.validation?.annual_forecast_history);
 const source=histories.find(result=>results.some(method=>method?.result_ref===result.result_ref
   &&method.execution?.operation==='analysis.method'&&Array.isArray(method.claims)&&method.claims.some(claim=>
     claim?.statistics?.kind==='forecast'&&claim.statistics.grain==='year'
     &&claim.statistics.value_column===result.validation.annual_forecast_history.value_column)))||histories[0];
 const proof=source?.validation?.annual_forecast_history,lineage=source?.result_lineage,pub=source?.business_project;
 const request=requestTarget(question)||annualTrendRequest(question,source,proof);
 if(request?.grain!=='year'||!proof||proof.kind!=='annual_closed_years'||proof.schema_version!==1||proof.state!=='checked'
   ||proof.target_period!==request.period||!proof.result_ref||proof.result_ref!==source.result_ref
   ||!proof.request_ref||requestRef&&proof.request_ref!==requestRef||lineage?.purpose!=='answer'||proof.request_ref!==lineage?.request_ref
   ||!proof.source_id||proof.source_id!==lineage?.source_id||proof.source_id!==source.receipt?.source_id
   ||source.receipt?.read_only!==true||source.truncated||source.values?.complete!==true
   ||source.validation?.state==='conflict'
   ||!proof.publication?.id||proof.publication.id!==pub?.id||!Number.isSafeInteger(pub?.version)
   ||pub.version<1||proof.publication.version!==pub.version
   ||!Number.isSafeInteger(proof.connection_revision)||proof.connection_revision<1
   ||proof.connection_revision!==source.source_provenance?.connection_revision)return null;
 const target=source.validation?.request_scope?.forecast_target;
 if(target?.period!==request.period||target?.grain!=='year'
   ||typeof target?.evidence?.quote!=='string'||target.evidence.quote.normalize('NFKC').trim()!==request.text
   ||target.evidence?.user_ref!==proof.request_ref)return null;
 const declaredRole=source.answer_contract?.result_ref===source.result_ref
   ?source.answer_contract.measurements?.[proof.value_column]?.role:null;
 const wantsBudget=/预算/.test(request.subject);
 const actual=explicitActualAmount.test(request.subject)||!wantsBudget&&explicitActualAmount.test(request.inputBody);
 const role=declaredRole??proof.measure_role;
 const reference=actual&&role!=='actual_fiscal';
 if(declaredRole&&proof.measure_role&&declaredRole!==proof.measure_role
   ||reference!==Boolean(proof.reference_only)||reference&&!allowReference
   ||wantsBudget&&role==='actual_fiscal')return null;
 const unit=source.analysis_options?.column_units?.[proof.value_column]||source.column_unit_evidence?.[proof.value_column]?.unit;
 if(printableUnit(unit)&&unit!==proof.unit)return null;
 const years=proof.observed_years;
 const missing=proof.missing_years??[], gap=missing.length>0;
 if(!Array.isArray(missing)||!Array.isArray(years)||years.length<(gap?1:5)||years.length>500)return null;
 const indices=years.map(year=>calendarIndex(year,'year')),targetIndex=calendarIndex(request.period,'year');
 if(targetIndex===null||indices.some((index,i)=>index===null||i&&index<=indices[i-1]))return null;
 const span=gap&&Array.isArray(proof.requested_years)?proof.requested_years.map(year=>calendarIndex(year,'year')):indices;
 const last=span.at(-1), clock=source.validation?.annual_fiscal_window;
 const crossesOpenYear=targetIndex===last+2&&clock?.schema_version===1&&clock.state==='checked'
   &&clock.basis==='client_calendar'&&clock.current_open_year===String(last+1)
   &&clock.current_date?.startsWith(clock.current_open_year+'-')&&clock.last_closed_year===String(last)
   &&clock.target_period===request.period&&clock.request_ref===proof.request_ref&&clock.source_id===proof.source_id
   &&clock.publication?.id===pub.id&&clock.publication.version===pub.version;
 if(span.length<5||span.length>500||span.some((index,i)=>index===null||i&&index!==span[i-1]+1)
   ||last+1!==targetIndex&&!crossesOpenYear||indices.some(index=>index<span[0]||index>last)
   ||gap&&!isDeepStrictEqual(missing,span.filter(index=>!indices.includes(index)).map(String)))return null;
 const {columns,rows}=source.values;
 if(!Array.isArray(columns)||!Array.isArray(rows))return null;
 const time=columns.indexOf(proof.time_column),measure=columns.indexOf(proof.value_column);
 if(time<0||measure<0||time===measure||columns.lastIndexOf(proof.time_column)!==time
   ||columns.lastIndexOf(proof.value_column)!==measure)return null;
 if(rows.length!==(source.row_count??source.values.row_count))return null;
 const seen=new Set(),observed=new Map();
 for(const row of rows){
  if(!Array.isArray(row)||row.length!==columns.length||!(Number.isFinite(row[measure])||gap&&row[measure]===null))return null;
  const index=calendarIndex(row[time],'year',true);
  if(index===null||index<span[0]||index>span.at(-1)||seen.has(index))return null;
  seen.add(index);
  if(row[measure]!==null)observed.set(index,row[measure]);
 }
 if(observed.size!==indices.length||indices.some(index=>!observed.has(index)))return null;
 const observations=indices.map(index=>[index,observed.get(index)]);
 const sameRequest={requestRef:lineage.request_ref,sessionId:lineage.session_id};
 if(formal.some(other=>other.result_ref!==source.result_ref
    &&!(other.reason_code==='NO_MATCH'&&other.row_count===0&&other.values?.complete===true
       &&other.values.rows?.length===0&&sameForecastRequest(other,source,sameRequest))
    &&(gap||!sameClosedAnnualSeries(other,source,proof,observations,question,allowReference))
    &&(!request.historyRequested||gap||!(isAnnualSupportQuery(other,source,proof,sameRequest)
      ||sameAnnualHistoryWithOpenNull(other,source,proof,observed)))))return null;
 return {source,proof,request,missing,observations};
}

export function checkedAnnualForecastTool(question,results,requestRef) {
 // Leave specific algorithms and settings to the usual method selection.
 if(/ARIMA|ETS|naive|线性(?:趋势|回归)|移动平均|季节平滑|指数平滑|参数|模型|算法|阶数/i.test(question))return null;
 const history=checkedAnnualHistory(question,results,requestRef);
 if(!history||history.missing.length)return null;
 const {source,proof,request}=history;
 return {name:'analysis_method',args:{result_ref:source.result_ref,method_id:'matrix.42',
   time_column:proof.time_column,columns:[proof.value_column],
   options:{forecast_grain:'year',forecast_model:'auto',forecast_until:request.period}}};
}

export function checkedAnnualForecastGapAnswer(question,results) {
 const history=checkedAnnualHistory(question,results);
 if(!history?.missing.length||typeof history.proof.measure_label!=='string'
    ||!/^[\p{Script=Han}A-Za-z\d ()（）·]{1,60}$/u.test(history.proof.measure_label))return null;
 const {request,missing,observations,proof}=history;
 const unit=printableUnit(proof.unit)?proof.unit:'';
 const figures=observations.length<=8?observations.map(([year,amount])=>`${year} 年 ${forecastNumber.format(amount)}${unit}`).join('、')
   :`从 ${observations[0][0]} 年到 ${observations.at(-1)[0]} 年共查到 ${observations.length} 个年度值，详见图表`;
 const absent=missing.slice(0,12).join('、')+(missing.length>12?' 等年份':' 年');
 return `已核实的${proof.measure_label}为 ${figures}。\n\n${absent}缺少同口径观测，暂无法预测 ${request.period} 年。`;
}

export function verifiedAnnualHistoryOnly(question,results,requestRef,sessionId){
 const history=checkedAnnualHistory(question,results,requestRef);
 if(!history?.request.historyRequested||history.missing.length
   ||history.source.result_lineage?.session_id!==sessionId)return null;
 return annualTrendText(history)+'\n\n'+history.request.period+' 年预测本轮尚未完成计算。';
}

export function checkedAnnualFiscalTrend(question,results){
 return checkedAnnualHistory(question,results)?.request.historyRequested===true;
}

export function verifiedAnnualAmountReference(question,results,catalogs,inventory) {
 const history=checkedAnnualHistory(question,results,undefined,{allowReference:true});
 if(!history?.proof.reference_only||history.missing.length)return null;
 const {source,request,observations,proof}=history;
 if(!/^[\p{Script=Han}A-Za-z\d ()（）·]{1,60}$/u.test(proof.measure_label||'')
  ||!Object.hasOwn(moneyUnits,proof.unit?.replace(/^人民币/,'')))return null;
 // Early delivery is limited to a complete, single-source catalog without
 // another monetary field or a published metric to inspect for actual spend.
 const latest=catalogs.findLast(result=>result.status==='succeeded'&&result.execution?.operation==='data.catalog'
   &&result.runtime_contract?.source==='active-engine'&&(!result.catalog_view?.view||result.catalog_view.view==='full')
   &&result.sources?.some(item=>Array.isArray(item.tables)&&!item.entry));
 const listed=inventory.findLast(result=>result.status==='succeeded'&&result.execution?.operation==='data.catalog'
   &&result.runtime_contract?.source==='active-engine');
 const [catalog,...others]=latest?.sources||[];
 const table=catalog?.tables?.[0];
 if(!catalog||others.length||listed?.sources?.length!==1||listed.sources[0].id!==proof.source_id
   ||listed.execution_scope!==source.execution_scope||listed.catalog_view?.next_source_offset!=null
   ||listed.runtime_contract.build_id!==latest.runtime_contract.build_id
   ||latest.execution_scope!==source.execution_scope||catalog.id!==proof.source_id
   ||catalog.business_project?.id!==proof.publication.id||catalog.business_project?.version!==proof.publication.version
   ||catalog.connection_revision!==proof.connection_revision||catalog.tables?.length!==1||catalog.next_table_offset!=null
   ||table?.next_column_offset!=null||latest.catalog_view?.next_source_offset!=null
   ||catalog.semantic_metrics?.length!==0||catalog.metric_definitions?.length!==0)return null;
 const candidates=(table?.columns||[]).filter(column=>!column.time_semantics
   &&(/\b(?:numeric|decimal|real|float|double|int(?:eger)?|bigint|smallint|number)\b/i.test(column.type||'')
      ||/金额|支出|拨款|(?:^|_)amount(?:_|$)/i.test(`${column.name||''} ${column.label||''}`)));
 if(candidates.length!==1||candidates[0].label!==proof.measure_label)return null;
 const amounts=observations.length<=8?observations.map(([year,amount])=>`${year} 年 ${forecastNumber.format(amount)} ${proof.unit}`).join('、')
   :`从 ${observations[0][0]} 年到 ${observations.at(-1)[0]} 年共查到 ${observations.length} 个年度金额，详见图表`;
 return `台账记录的${proof.measure_label}为 ${amounts}。\n\n现有记录无法核实实际投入金额，暂无法预测 ${request.period} 年的实际投入。`;
}

function historyExecutionIdentity(result) {
 const proof=result.validation.forecast_history,lineage=result.result_lineage;
 // receipt.query_hash includes the unique plan ID. Query shape and the
 // parameter-sensitive population identity bind a repeated execution.
 const fingerprints=[result.query_identity,lineage.config_fingerprint,lineage.population_fingerprint];
 if(!lineage.session_id||fingerprints.some(value=>typeof value!=='string'||!value))return null;
 return JSON.stringify([
  lineage.session_id,lineage.request_ref,lineage.source_id,...fingerprints,
  proof.publication.id,proof.publication.version,proof.connection_revision,proof.kind,proof.subject,proof.target_period,
  proof.start_inclusive,proof.end_exclusive,proof.span_periods??proof.requested_periods,
  proof.observed_periods,proof.missing_periods,proof.time_column,proof.value_column,
  result.values.columns,result.values.rows,result.answer_contract?.business_confirmation
 ]);
}

// The same verified formal query can be emitted twice in one model response.
// Fresh result references do not change the evidence, but differing queries,
// populations or rows still require a decision about which result to use.
export function checkedForecastHistoriesAnswer(question,histories) {
 if(!Array.isArray(histories)||!histories.length)return null;
 const [first,...duplicates]=histories;
 const answer=checkedForecastHistoryAnswer(question,first);
 if(!answer||!duplicates.length)return answer;
 const identity=historyExecutionIdentity(first);
 return identity&&duplicates.every(result=>checkedForecastHistoryAnswer(question,result)
  &&historyExecutionIdentity(result)===identity)?answer:null;
}

export function unavailableForecastAnswer(question, result, args, error) {
 const target=monthlyRequest(question,result)?.period;
 if (!target||args?.result_ref!==result.result_ref||!Array.isArray(args.columns)||args.columns.length!==1) return null;
 const failure=/^Error: FORECAST_PREREQUISITE: 历史序列缺期或间隔不一致，缺期包括 ((?:\d{4}-\d{2})(?:、\d{4}-\d{2})*)。/.exec(error);
 if (!failure) return null;
 const periods=observedMonths(result,args.time_column,args.columns[0]);
 if (!periods||periods.length<2) return null;
 const gaps=[];
 // Collect every gap: the engine message lists up to three missing periods per
 // gap across all gaps, so a six-item cap here could drop a listed period and
 // lose the honest coverage answer. Display stays capped, verification does not.
 for(let i=1;i<periods.length;i++){
  for(let missing=periods[i-1]+1;missing<periods[i];missing++)gaps.push(missing);
 }
 if(!failure[1].split('、').every(p=>gaps.includes(calendarIndex(p,'month'))))return null;
 const [start,end]=[periods[0],periods.at(-1)].map(monthFromIndex);
 const valueColumn=args.columns[0],unit=result.analysis_options?.column_units?.[valueColumn];
 let observations='';
 if(/^(?:个|家|户|件|条|次|张|项|元|万元|亿元)$/.test(unit||'')){
  const [time,measure]=[args.time_column,valueColumn].map(column=>result.values.columns.indexOf(column));
  const values=new Map(result.values.rows.map(row=>[calendarIndex(row[time],'month',true),row[measure]]));
  observations=' '+start+' 为 '+forecastNumber.format(values.get(periods[0]))+' '+unit+'，'
   +end+' 为 '+forecastNumber.format(values.get(periods.at(-1)))+' '+unit+'。';
 }
 const coverage='查到 '+periods.length+' 个月的历史记录（'+start+' 至 '+end+'）。'+observations
  +' '+gaps.slice(0,6).map(monthFromIndex).join('、')+(gaps.length>6?' 等月份':'')+' 缺少记录。';
 return gapAnswer(target,coverage);
}

function trainingMatches(result, stats, request) {
 const {grain, history_count:count, history_start:start, origin, time_column:time, value_column:value} = stats;
 const {values,validation} = result;
 const reuse = stats.training_window;
 const available = reuse ? reuse.available_periods : count;
 if (result.truncated || result.report || result.receipt?.read_only !== true || validation?.state === 'conflict'
     || values?.complete !== true
     || !Array.isArray(values.columns) || !Array.isArray(values.rows)
     || values.rows.length !== (result.row_count ?? values.row_count) || !Number.isSafeInteger(count)
     || count < (grain === 'year' ? 5 : 12) || available > 500 || values.rows.length > 500 || time === value) return false;
 const target = validation?.request_scope?.forecast_target;
 if (request.explicitTrainingWindow && !target) return false;
 if (target && (target.period !== request.period || target.grain !== request.grain
     || typeof target.evidence?.quote !== 'string' || target.evidence.quote.normalize('NFKC').trim() !== request.text)) return false;
 // An older public receipt can warn that the future target is absent from training.
 // Preserve all other warnings, including missing historical years and value conflicts.
 const missingTarget = new RegExp('^结果的日期列（[^。]+）都不包含问题中的 '+request.period.slice(0,4)+' 年。');
 if (Array.isArray(result.warnings) && result.warnings.some(warning => !target || !missingTarget.test(warning))) return false;
 const timeIndex=values.columns.indexOf(time), valueIndex=values.columns.indexOf(value);
 const first=calendarIndex(start,grain), last=calendarIndex(origin,grain);
 const availableFirst=reuse?calendarIndex(reuse.available_start,grain):first;
 if (timeIndex<0 || valueIndex<0 || values.columns.lastIndexOf(time)!==timeIndex || values.columns.lastIndexOf(value)!==valueIndex
     || first===null || last===null || last-first+1!==count || availableFirst===null) return false;
 if (reuse&&(grain!=='month'||reuse.schema_version!==1||reuse.kind!=='monthly_training_window'
     ||reuse.selection!=='trailing_requested_periods'||reuse.result_ref!==result.result_ref
     ||reuse.requested_periods!==count||reuse.history_start!==start||reuse.origin!==origin
     ||available<=count||values.rows.length!==available||last-availableFirst+1!==available||!target?.evidence?.user_ref
     ||result.result_lineage?.request_ref!==target.evidence.user_ref)) return false;
 // A checked wider history may carry leading observations before the training
 // window; only the declared, checked prefix is excluded from the row binding.
 // The trailing-reuse proof above already binds the full receipt, so this
 // exception applies only to the non-reuse forecast_history path.
 let trainRows=values.rows;
 const historyProof=validation?.forecast_history;
 if(!reuse&&grain==='month'&&historyProof?.available_start!==undefined){
  if(historyProof.state!=='checked'||historyProof.result_ref!==result.result_ref
     ||historyProof.request_ref!==result.result_lineage?.request_ref)return false;
  const avail=calendarIndex(historyProof.available_start,'month');
  if(avail===null||avail>=first)return false;
  const leading=[];
  trainRows=values.rows.filter(row=>{
   const index=Array.isArray(row)?calendarIndex(row[timeIndex],grain,true):null;
   if(index!==null&&index<first){leading.push(index);return false;}
   return true;
  });
  if(leading.some(index=>index<avail))return false;
  if(leading.length&&monthFromIndex(Math.min(...leading))!==historyProof.available_start)return false;
  if(trainRows.length!==count)return false;
 } else if(values.rows.length!==available) return false;
 const observed=new Set();
 for (const row of trainRows) {
  if (!Array.isArray(row) || !Number.isFinite(row[valueIndex])
      || stats.value_kind==='nonnegative_discrete_count' && (!Number.isSafeInteger(row[valueIndex]) || row[valueIndex]<0)) return false;
  const index=calendarIndex(row[timeIndex],grain,true);
  if (index===null || index<availableFirst || index>last || observed.has(index)) return false;
  observed.add(index);
 }
 return true;
}

function forecastSummary(stats, request) {
 const {grain, origin, steps, target_period:target, forecast_periods:periods, backtest, sample_limits:limits} = stats;
 const predicted=stats.display?.predicted, intervals=stats.display?.interval_95;
 const raw=stats.predicted, rawIntervals=stats.interval_95;
 const originIndex=calendarIndex(origin,grain);
 const isCount=stats.value_kind==='nonnegative_discrete_count';
 const atInteger=value=>Math.abs(value-Math.round(value))<=4*Number.EPSILON*Math.max(1,Math.abs(value))?Math.round(value):value;
 const model=forecastMethodPresentation(stats.model);
 if (grain!==request.grain || target!==request.period || originIndex===null || !model
     || (!isCount && stats.value_kind!=='numeric')
     || !Number.isSafeInteger(steps) || steps<1 || steps>24
     || ![periods,predicted,intervals,raw,rawIntervals].every(v=>Array.isArray(v)&&v.length===steps) || periods.at(-1)!==target
     || !periods.every((p,i)=>calendarIndex(p,grain)===originIndex+i+1)
     || !predicted.every(Number.isFinite) || !intervals.every((pair,i)=>Array.isArray(pair)&&pair.length===2
       &&pair.every(Number.isFinite)&&pair[0]<=predicted[i]&&predicted[i]<=pair[1])
     || backtest?.same_model_specification!==true || backtest.model!==stats.model
     || ![backtest.mae,backtest.rmse].every(n=>Number.isFinite(n)&&n>=0)
     || !Number.isSafeInteger(backtest.rolling_origins) || backtest.rolling_origins<1
     || backtest.rolling_origins>=stats.history_count || backtest.rolling_origins!==limits?.holdout_points
     || !Number.isSafeInteger(limits.minimum_history) || limits.minimum_history<1 || stats.history_count<limits.minimum_history) return null;
 for (const [i,n] of raw.entries()) {
  const pair=rawIntervals[i];
  if (!Number.isFinite(n) || !Array.isArray(pair) || pair.length!==2 || !pair.every(Number.isFinite) || !(pair[0]<=n&&n<=pair[1])) return null;
  const [lo,hi]=pair;
  if (isCount) {
   if (![predicted[i],...intervals[i]].every(v=>Number.isSafeInteger(v)&&v>=0)
       || hi<0 || predicted[i]!==Math.max(0,Math.floor(n+.5)) || intervals[i][0]!==Math.max(0,Math.floor(atInteger(lo)))
       || intervals[i][1]!==Math.ceil(atInteger(hi))) return null;
  } else if (predicted[i]!==n || intervals[i][0]!==lo || intervals[i][1]!==hi) return null;
 }
 return {point:predicted.at(-1), interval:intervals.at(-1), backtest, model};
}

const forecastNumber = new Intl.NumberFormat('zh-CN', {useGrouping:false,maximumFractionDigits:8});
const printableUnit = unit => typeof unit==='string' && /^[\p{L}%％°µμ²³^/·]{1,16}$/u.test(unit)
  && !/原字段单位|见单位|各指标单位|混合/.test(unit);
const cnPeriod = period => period.length===4 ? period+'年' : period.slice(0,4)+'年'+Number(period.slice(5))+'月';
const selectionReasons={insufficient_history_for_same_model_backtest:'同模型回测历史不足',fit_failed:'拟合失败',
 dependency_unavailable:'运行依赖缺失',infeasible_count_interval:'计数区间不可行'};
// Mirror methods.selection_note so a canonical replacement answer keeps the
// same disclosure; unknown models or reasons fail closed to no sentence.
function selectionNote(stats){
 if(stats?.requested_model!=='auto')return '';
 const excluded=stats?.selection?.excluded;
 if(!Array.isArray(excluded)||!excluded.length)return '';
 const chosen=forecastMethodPresentation(stats.model);
 if(!chosen)return '';
 const groups=new Map();
 for(const item of excluded){
  const model=forecastMethodPresentation(item?.model),reason=selectionReasons[item?.reason];
  if(!model||!reason)return '';
  groups.set(reason,[...(groups.get(reason)||[]),model.name]);
 }
 if(!groups.size)return '';
 return '自动选择本次采用'+chosen.name+'；'
  +[...groups].map(([reason,names])=>names.join('、')+'因'+reason+'排除').join('；')+'。';
}
// Mirror the engine's horizon disclosure: a horizon-matched backtest of the
// chosen model, or an explicit statement that the history cannot support one.
function horizonNote(stats){
 const steps=stats?.steps;
 if(!Number.isSafeInteger(steps)||steps<2||!Object.hasOwn(stats,'horizon_backtest'))return '';
 const hb=stats.horizon_backtest;
 if(hb===null||hb===undefined)
  return '历史不足以做同模型'+steps+'步回测，选模依据单步回测，区间受模型假设约束。';
 if(hb.horizon_steps!==steps||!Number.isSafeInteger(hb.rolling_origins)||hb.rolling_origins<1
    ||!Number.isFinite(hb.mae)||hb.mae<0)return '';
 return '多步回测（'+steps+'步）平均绝对误差'+forecastErrorLabel(hb.mae)+'。';
}
// Mirror the engine's publication-grade, signed-zero and interval-basis
// disclosures so a canonical replacement answer keeps them verbatim.
function disclosureNotes(stats){
 let out='';
 if(stats?.publication_level==='reference_only')
  out+='回测仅1个留出点，本结果为试算参考，不作为可引用结论。';
 const unit=stats?.unit;
 if(stats?.value_kind==='numeric'&&typeof unit==='string'&&/^(?:人民币)?(?:万|亿)?元$/.test(unit)
    &&Array.isArray(stats.display?.interval_95)
    &&stats.display.interval_95.some(pair=>Array.isArray(pair)&&pair[0]<0))
  out+='预测区间包含负值，反映模型估算范围。';
 const basis=forecastBasisPresentation(stats?.interval_basis);
 if(basis)out+='区间依据：'+basis+'。';
 return out;
}

function sameCountSubject(name, subject, unit) {
 if (!/^(?:件|条|笔|次|宗|单|个|家|户|台次)$/.test(unit)) return false;
 const noun = label => label.normalize('NFKC').replace(new RegExp('\\s*[（(]'+unit+'[）)]$'), '').trim()
  .replace(new RegExp('(?:案卷|记录|单据|事件)?(?:'+unit+'数|数量|数目|计数|数|量)$'), '');
 const expected=noun(subject);
 return expected.length>=2 && noun(name)===expected;
}

function sameForecastRequest(result, source, binding) {
 const lineage=result.result_lineage, base=source.result_lineage;
 const target=result.validation?.request_scope?.forecast_target;
 const planned=source.validation?.request_scope?.forecast_target;
 return Boolean(binding.requestRef&&binding.sessionId&&source.execution_scope
  &&result.execution_scope===source.execution_scope&&result.receipt?.read_only===true&&!result.truncated
  &&result.validation?.state!=='conflict'&&lineage?.purpose==='answer'
  &&lineage.request_ref===binding.requestRef&&lineage.session_id===binding.sessionId
  &&target?.evidence?.user_ref===binding.requestRef&&target.period===planned?.period&&target.grain===planned?.grain
  &&lineage.source_id===result.receipt.source_id
  &&Boolean(base?.config_fingerprint)&&lineage.config_fingerprint===base.config_fingerprint
  &&lineage.source_id===base.source_id&&source.business_project?.id
  &&result.business_project?.id===source.business_project.id
  &&result.business_project?.version===source.business_project.version);
}

function equivalentAnnualClaim(previous,current){
 if(previous?.length!==1||current?.length!==1)return false;
 const [left]=previous,[right]=current;
 if(left.operation!==right.operation||left.method_id!==right.method_id
   ||!/^claim:[0-9a-f]{20}$/.test(right.claim_id)||!left.statistics||!right.statistics)return false;
 const {requested_model:_leftModel,selection:_leftSelection,...leftStats}=left.statistics;
 const {requested_model:_rightModel,selection:_rightSelection,...rightStats}=right.statistics;
 return isDeepStrictEqual(leftStats,rightStats);
}

// The caller must run the complete answer guard before publishing this projection.
export function verifiedForecastAnswer(userText, evidence, binding={}) {
 if (!Array.isArray(evidence)) return null;
 const results=evidence.filter(result=>result?.result_ref&&result.status==='succeeded'&&!isExploratory(result));
 const annualHistory=checkedAnnualHistory(userText,results,binding.requestRef);
 const annual=annualHistory?.request.historyRequested&&!annualHistory.missing.length?annualHistory:null;
 const matchesAnnual=result=>result.result_ref===annual.source.result_ref&&result.claims?.some(claim=>{
  const stats=claim.statistics;
  return stats?.kind==='forecast'&&stats.grain==='year'&&stats.target_period===annual.request.period
   &&stats.time_column===annual.proof.time_column&&stats.value_column===annual.proof.value_column;
 });
 const methods=results.filter(result=>result.execution?.operation==='analysis.method'&&(!annual||matchesAnnual(result)));
 const [method,...repeated]=methods;
 const source=annual?.source||results.find(result=>result.result_ref===method?.result_ref
   &&(result.values?.complete===true||result!==method&&result.validation?.snapshot_forecast_history));
 const horizon=source?.validation?.request_scope?.forecast_target?.horizon_start;
 const stock=snapshotHistory(userText,source,binding.requestRef);
 const stockRequest=stock&&!stock.missing.length?{period:stock.proof.target_period,grain:'month',
   horizonStart:stock.proof.horizon_start,text:userText.normalize('NFKC').trim()}:null;
 const request=annual?.request||stockRequest||(horizon?monthlyRequest(userText,source):requestTarget(userText)||checkedMonthlyHistory(userText,source)?.request);
 if(!request||!method?.artifact_ref||!source)return null;
 const lineage=source.result_lineage;
 if(binding.requestRef&&lineage?.request_ref&&binding.requestRef!==lineage.request_ref
   ||binding.sessionId&&lineage?.session_id&&binding.sessionId!==lineage.session_id)return null;
 const histories=results.filter(result=>result.execution?.operation!=='analysis.method');
 if(histories.some(result=>result.result_ref!==source.result_ref)
    &&![source,...histories].every(result=>sameForecastRequest(result,source,binding)))return null;
 const claims=new Map();
 for (const result of annual?[method]:results) {
  if (!Array.isArray(result.claims)) continue;
  for (const claim of result.claims) claims.set(claim?.claim_id,claim);
 }
 if (claims.size!==1 || !Array.isArray(method.claims) || method.claims.length!==1
     || (method.execution_scope && source.execution_scope && method.execution_scope!==source.execution_scope)
     || (method!==source && method.artifact_ref===source.artifact_ref)
     || repeated.some(result=>result.result_ref!==method.result_ref||!result.artifact_ref
       ||result.execution_scope!==method.execution_scope||!(annual?equivalentAnnualClaim(method.claims,result.claims)
         :isDeepStrictEqual(result.claims,method.claims)))) return null;
 const claim=method.claims[0], stats=claim?.statistics;
 const input=stockRequest?{...source,values:{complete:true,columns:['period','value'],
   rows:stock.observed.map(month=>[month,stock.observations.get(month)])}}:source;
 if (claim?.operation!=='matrix_method' || !/^claim:[0-9a-f]{20}$/.test(claim.claim_id)
     || (claim.method_id!=='matrix.42' && !(typeof claim.method_id==='string'&&claim.method_id.startsWith('custom.')))
     || stats?.kind!=='forecast' || stats.schema_version!==1 || stats.source_result_ref!==method.result_ref
     || annual&&stats.unit!==annual.proof.unit || !trainingMatches(input,stats,request)) return null;
 const summary=forecastSummary(stats,request);
 if (!summary) return null;
 if(request.horizonStart&&(stats.steps!==12||stats.forecast_periods[0]!==request.horizonStart))return null;
 const {point,interval,backtest,model}=summary;
 if ([point,...interval].some(n=>n!==0 && +forecastNumber.format(n)===0)) return null;
 const declared=source.analysis_options?.column_units?.[stats.value_column]
   || source.column_unit_evidence?.[stats.value_column]?.unit
   || (stats.value_column==='value' ? source.metric_contract?.unit : null);
 if (printableUnit(declared) && declared!==stats.unit) return null;
 const unit=printableUnit(stats.unit) ? stats.unit : '';
 const number=n=>(isCurrencyForecastUnit(unit)?forecastAmountLabel(n):forecastNumber.format(n))+unit;
 const error=n=>forecastErrorLabel(n)+unit;
 let displaySubject=annual?.proof.measure_label||request.subject;
 if (request.subject&&!annual) {
  const published=stats.measure_label_basis==='published_field'&&typeof stats.measure_label==='string'
    ? stats.measure_label.normalize('NFKC').trim() : '';
  if(published&&published!==request.subject){
   const annualSubject=request.grain==='year'&&/^全年(?:的)?(.+)$/.exec(request.subject);
   const matched=annualSubject&&request.inputBody.endsWith(published)&&published.endsWith(annualSubject[1]);
   if(!matched)return null;
   displaySubject=published;
  }else if(!published&&![source.metric_contract?.name,stats.value_column].some(name=>
      typeof name==='string' && (name.normalize('NFKC').includes(request.subject)
      ||stats.value_kind==='nonnegative_discrete_count'&&declared===stats.unit&&sameCountSubject(name,request.subject,stats.unit)))) return null;
 }
 const label=displaySubject ? displaySubject+'的' : '';
 const first=stats.display.predicted[0];
 const direction=point===first?'首末月的预测值持平':point>first?'末月的预测值高于首月':'末月的预测值低于首月';
 const trend=annual?annualTrendText(annual):stockRequest?
   `历史月末存量从 ${cnPeriod(stock.observed[0])}的${number(stock.observations.get(stock.observed[0]))}，变化到${cnPeriod(stats.origin)}的${number(stock.observations.get(stats.origin))}。`:'';
 const openYear=stats.grain==='year'&&/^\d{4}$/.test(stats.open_year_skipped||'')&&stats.forecast_periods.includes(stats.open_year_skipped)
   ?`${stats.open_year_skipped}年尚未结年，图中的当年数据为模型推算，训练截至${stats.origin}年。`:'';
 return [
  request.horizonStart?'**'+cnPeriod(request.horizonStart)+'至'+cnPeriod(request.period)+'，'+direction+'。**':'',
  `**${cnPeriod(request.period)}${label}预测值约为${number(point)}**，95%预测区间为${number(interval[0])}至${number(interval[1])}。`,
  trend,
  openYear,
  `实际训练覆盖${cnPeriod(stats.history_start)}至${cnPeriod(stats.origin)}，使用${stats.history_count}期连续历史观测。采用${model.name}，${model.explanation}。`,
  `历史回测留出${backtest.rolling_origins}期，平均绝对误差${error(backtest.mae)}，均方根误差${error(backtest.rmse)}。`
    +(backtest.rolling_origins===1&&stats.publication_level!=='reference_only'?'回测样本较少，预测区间仅供参考。':'')
    +selectionNote(stats)+horizonNote(stats)+disclosureNotes(stats),
  request.horizonStart&&/按月|逐月/.test(userText)?'展开图卡的「查看各期预测和回测」可看每月预测值和区间。':'',
 ].filter(Boolean).join('\n\n');
}

function annualTrendText({observations,proof}){
 const [firstYear,first]=observations[0], [lastYear,last]=observations.at(-1);
 const [peakYear,peak]=observations.reduce((best,row)=>row[1]>best[1]?row:best);
 const format=amount=>(Math.abs(amount)>=0.01?annualAmountNumber:forecastNumber).format(amount)+proof.unit;
 const startEnd=first===last?`首末年均为${format(last)}`:`从${format(first)}${last>first?'升至':'降至'}${format(last)}`;
 const peakNote=peak===first||peak===last?'':`，期间${peakYear}年最高，为${format(peak)}`;
 return `${firstYear}年至${lastYear}年，${proof.measure_label}${startEnd}${peakNote}。`;
}

// ---- 0.8.2: forecast answer structure -----------------------------------------
// A forecast answer leads with the conclusion. The training series and the
// per-period values live in the result card, not in the answer body.
const DETAIL_DISPLAY_REQUEST=/(?:列出|展示|显示|给出|提供|附上|罗列|导出|下载|画|绘制|看)[^。！？!?;；\n]{0,24}(?:历史|历年|往年|逐月|每个月|每月|各个月|各月|各期|训练|样本|明细|序列|原始数据|数据表|趋势图|走势图|情况)|(?:历史|历年|往年|逐月|每个月|每月|各个月|各月|各期|训练|样本|刚才|上面|上述)[^。！？!?;；\n]{0,16}(?:明细|数据|数值|序列|列表|表格|趋势图|走势图|情况|分布)|逐(?:月|期|年)(?:的)?(?:预测|数值)|每(?:个)?月(?:的)?(?:预测|数值)|各(?:个)?月(?:的)?(?:预测|数值)|预测(?:路径|曲线|序列|趋势)|(?:未来|后续)\s*[一二三四五六七八九十\d]+\s*(?:期|个月|年)/;

function periodFromIndex(index,grain){
 if(grain==='year')return String(index);
 if(grain==='quarter')return Math.floor(index/4)+'-Q'+(index%4+1);
 if(grain==='month')return monthFromIndex(index);
 return null;
}

function trainingPeriods(evidence){
 const periods=new Set();
 const list=Array.isArray(evidence)?evidence:[];
 for(const receipt of list)for(const claim of receipt?.claims||[]){
  const stats=claim?.statistics;
  if(stats?.kind!=='forecast')continue;
  if(Array.isArray(stats.history?.periods)){
   for(const period of stats.history.periods)if(typeof period==='string'&&period.trim())periods.add(period.trim());
  }
  const source=list.find(item=>item?.result_ref&&item.result_ref===stats.source_result_ref);
  const timeIdx=Array.isArray(source?.values?.columns)?source.values.columns.indexOf(stats.time_column):-1;
  if(timeIdx>=0&&Array.isArray(source?.values?.rows)){
   for(const row of source.values.rows){
    const cell=Array.isArray(row)?row[timeIdx]:row?.[stats.time_column];
    if(typeof cell==='string'&&cell.trim())periods.add(cell.trim());
   }
  }
  const start=periodIndexValue(stats.history_start,stats.grain),end=periodIndexValue(stats.origin,stats.grain);
  const step=grainSteps[stats.grain];
  if(start!==null&&end!==null&&step&&end>=start&&(end-start)/step<=500){
   for(let idx=start;idx<=end;idx+=step){
    const label=periodFromIndex(idx,stats.grain);
    if(label)periods.add(label);
   }
  }
 }
 return periods;
}

function trainingCellPattern(period){
 let match=/^(\d{4})-(\d{2})$/.exec(period);
 if(match){const month=Number(match[2]);
  return new RegExp('^'+match[1]+'(?:\\s*[-/.]\\s*0?'+month+'\\s*$|\\s*年\\s*0?'+month+'\\s*月\\s*$)');}
 match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(period);
 if(match)return new RegExp('^'+match[1]+'[-/.]'+match[2]+'[-/.]'+match[3]+'$');
 match=/^(\d{4})-Q([1-4])$/.exec(period);
 if(match)return new RegExp('^'+match[1]+'(?:\\s*Q'+match[2]+'\\s*$|\\s*年\\s*(?:第\\s*)?Q?'+match[2]+'\\s*季度\\s*$)');
 if(/^\d{4}$/.test(period))return new RegExp('^'+period+'\\s*年?\\s*$');
 return null;
}

function periodMentionPattern(period){
 let match=/^(\d{4})-(\d{2})$/.exec(period);
 if(match){const month=Number(match[2]);
  return new RegExp(match[1]+'\\s*(?:年\\s*0?'+month+'\\s*月|[-/.]\\s*0?'+month+')(?!\\d)');}
 match=/^(\d{4})$/.exec(period);
 if(match)return new RegExp(match[1]+'\\s*年(?!\\s*\\d{1,2}\\s*月)');
 match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(period);
 if(match)return new RegExp(match[1]+'\\s*[-/.]\\s*0?'+Number(match[2])+'\\s*月\\s*0?'+Number(match[3])+'\\s*[日号]?');
 match=/^(\d{4})-Q([1-4])$/.exec(period);
 if(match)return new RegExp(match[1]+'\\s*年?\\s*[-\\s]?\\s*Q?'+match[2]+'\\s*季度');
 return null;
}

function markdownTables(text){
 const tables=[];let current=null,offset=0;
 for(const line of text.split('\n')){
  const trimmed=line.trim();
  if(/^\|.*\|$/.test(trimmed)){
   const cells=trimmed.slice(1,-1).split('|').map(cell=>cell.trim());
   // Header separators are not data rows.
   if(!cells.every(cell=>/^:?-+:?$/.test(cell))){current??={offset,rows:[]};current.rows.push(cells);}
  }else if(current){tables.push(current);current=null;}
  offset+=line.length+1;
 }
 if(current)tables.push(current);
 return tables;
}

function trainingTables(text,training){
 const patterns=[...training].map(trainingCellPattern).filter(Boolean);
 let matched=0,firstOffset=null;
 if(patterns.length)for(const table of markdownTables(text)){
  let hits=0;
  for(const cells of table.rows)if(cells.length&&patterns.some(pattern=>pattern.test(cells[0])))hits++;
  matched+=hits;
  if(hits>=4&&firstOffset===null)firstOffset=table.offset;
 }
 return {matched,firstOffset};
}

function firstClaimOffset(plain){
 for(const match of plain.matchAll(/[^。；;\n]+/g)){
  const section=match[0];
  if(negated.test(section))continue;
  if(!(asserted.test(section)||intervalWord.test(section)
       ||/^(?:点?预测|预计|预估)\s*[：:]/.test(section.trim())||section.trim().startsWith('预测')))continue;
  if(!numbers(section.replace(sampleSize,'')).length)continue;
  return match.index;
 }
 return null;
}

// Fires only when this turn computed a forecast and the user did not explicitly
// ask for the series itself; then an answer that buries the conclusion under
// training tables or per-period listings is a structural defect.
export function forecastDeliveryIssues(text,evidence,userText=''){
 const forecasts=forecastRows(evidence);
 if(!forecasts.length)return [];
 const question=typeof userText==='string'?userText.normalize('NFKC'):'';
 if(DETAIL_DISPLAY_REQUEST.test(question))return [];
 const plain=String(text||'').replaceAll('**','');
 const claimAt=firstClaimOffset(plain);
 if(claimAt===null&&!forecastWord.test(question))return [];
 const issues=[];
 const {matched,firstOffset}=trainingTables(plain,trainingPeriods(evidence));
 if(firstOffset!==null&&(claimAt===null||firstOffset<claimAt))
  issues.push('回答结构：预测结论要放在回答开头。请先用一句话给出目标期间的预测值和95%预测区间，再说明训练窗口、模型与回测；数据明细保留在结果卡中。');
 if(matched>=8)
  issues.push('回答结构：正文逐期罗列了 '+matched+' 行训练序列明细，结果卡已完整展示该序列。请删除正文中的逐期明细表，保留结论与口径说明。');
 const future=new Set(forecasts.map(row=>row.period));
 let enumerated=0;
 for(const period of future){const pattern=periodMentionPattern(period);if(pattern&&pattern.test(plain))enumerated++;}
 if(enumerated>=6)
  issues.push('回答结构：正文逐期罗列了 '+enumerated+' 个预测期间。请只报告目标期间的结论，各期预测值已在结果卡中查看。');
 return issues;
}

const grainSteps={day:1,week:7,month:1,quarter:1,year:1};
function periodIndexValue(period,grain){
 if(grain==='quarter'){
  const match=/^(\d{4})-Q([1-4])$/.exec(period);
  return match?Number(match[1])*4+Number(match[2])-1:null;
 }
 if(grain==='week'||grain==='day'){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(period))return null;
  const stamp=Date.parse(period+'T00:00:00Z');
  if(!Number.isFinite(stamp)||new Date(stamp).toISOString().slice(0,10)!==period)return null;
  return stamp/86400000;
 }
 return calendarIndex(period,grain);
}

const periodLabel=period=>/^\d{4}$/.test(period)?period+'年'
 :/^\d{4}-\d{2}$/.test(period)?period.slice(0,4)+'年'+Number(period.slice(5))+'月'
 :/^\d{4}-Q[1-4]$/.test(period)?period.slice(0,4)+'年第'+Number(period.slice(6))+'季度'
 :period;

function claimForecastSummary(stats){
 if(!stats||stats.schema_version!==1||stats.kind!=='forecast')return null;
 const grain=stats.grain;
 if(!['year','quarter','month','week','day'].includes(grain))return null;
 const model=forecastMethodPresentation(stats.model);
 if(!model||!['numeric','nonnegative_discrete_count'].includes(stats.value_kind))return null;
 const count=stats.history_count,steps=stats.steps;
 const periods=stats.forecast_periods,raw=stats.predicted,rawIntervals=stats.interval_95;
 const predicted=stats.display?.predicted,intervals=stats.display?.interval_95;
 if(!Number.isSafeInteger(count)||count<(grain==='year'?5:12)
   ||!Number.isSafeInteger(steps)||steps<1||steps>24
   ||![periods,raw,rawIntervals,predicted,intervals].every(list=>Array.isArray(list)&&list.length===steps)
   ||stats.target_period!==periods.at(-1))return null;
 const startIndex=periodIndexValue(stats.history_start,grain);
 const lastIndex=periodIndexValue(stats.origin,grain);
 if(startIndex===null||lastIndex===null
   ||Math.round((lastIndex-startIndex)/grainSteps[grain])+1!==count
   ||periods.some((period,i)=>periodIndexValue(period,grain)!==lastIndex+grainSteps[grain]*(i+1)))return null;
 if(stats.history!==undefined){
  const history=stats.history?.periods,values=stats.history?.values;
  if(!Array.isArray(history)||!Array.isArray(values)||history.length!==count||values.length!==count
    ||stats.history_start!==history[0]||stats.origin!==history.at(-1)
    ||!values.every(value=>Number.isFinite(value)))return null;
  const indexes=history.map(period=>periodIndexValue(period,grain));
  if(indexes.some(index=>index===null)
    ||indexes.some((index,i)=>i&&index!==indexes[i-1]+grainSteps[grain]))return null;
 }
 if(!predicted.every((value,i)=>
   Number.isFinite(value)&&Number.isFinite(raw[i])&&Array.isArray(intervals[i])&&Array.isArray(rawIntervals[i])
   &&intervals[i].length===2&&rawIntervals[i].length===2
   &&intervals[i].every(Number.isFinite)&&rawIntervals[i].every(Number.isFinite)
   &&intervals[i][0]<=value&&value<=intervals[i][1]&&rawIntervals[i][0]<=raw[i]&&raw[i]<=rawIntervals[i][1]))return null;
 const backtest=stats.backtest,limits=stats.sample_limits;
 if(!backtest||backtest.model!==stats.model||backtest.same_model_specification!==true
   ||![backtest.mae,backtest.rmse].every(n=>Number.isFinite(n)&&n>=0)
   ||!Number.isSafeInteger(backtest.rolling_origins)||backtest.rolling_origins<1||backtest.rolling_origins>=count
   ||!Number.isSafeInteger(limits?.minimum_history)||limits.minimum_history<1||count<limits.minimum_history)return null;
 return {model,count,start:stats.history_start,origin:stats.origin,target:stats.target_period,
   point:predicted.at(-1),interval:intervals.at(-1),mae:backtest.mae,rmse:backtest.rmse,rolling:backtest.rolling_origins};
}

// A concise delivery built from this turn's single
// forecast claim. Used to replace a structurally defective model answer; the
// numbers come from the same claim rows forecastIssues validates against.
export function forecastClaimDelivery(evidence){
 if(!Array.isArray(evidence))return null;
 const claims=new Map();
 for(const receipt of evidence){
  if(!receipt||typeof receipt!=='object')continue;
  for(const claim of receipt.claims||[]){
   if(claim?.operation!=='matrix_method'||typeof claim.claim_id!=='string')continue;
   if(claim.statistics?.kind==='forecast')claims.set(claim.claim_id,claim.statistics);
  }
 }
 if(claims.size!==1)return null;
 const stats=claims.values().next().value;
 const summary=claimForecastSummary(stats);
 if(!summary)return null;
 if([summary.point,...summary.interval].some(n=>n!==0&&+forecastNumber.format(n)===0))return null;
 const unit=printableUnit(stats.unit)?stats.unit:'';
 const number=n=>(isCurrencyForecastUnit(unit)?forecastAmountLabel(n):forecastNumber.format(n))+unit;
 const error=n=>forecastErrorLabel(n)+unit;
 return ['**'+periodLabel(summary.target)+'预测值约为'+number(summary.point)+'**，95%预测区间为'+number(summary.interval[0])+'至'+number(summary.interval[1])+'。',
  '实际训练覆盖'+periodLabel(summary.start)+'至'+periodLabel(summary.origin)+'，使用'+summary.count+'期连续历史观测。采用'
    +summary.model.name+'，'+summary.model.explanation+'。',
  '历史回测留出'+summary.rolling+'期，平均绝对误差'+error(summary.mae)+'，均方根误差'+error(summary.rmse)+'。'
    +(summary.rolling===1&&stats.publication_level!=='reference_only'?'回测样本较少，预测区间仅供参考。':'')
    +selectionNote(stats)+horizonNote(stats)+disclosureNotes(stats),
  stats.grain==='year'&&/^\d{4}$/.test(stats.open_year_skipped||'')&&stats.forecast_periods.includes(stats.open_year_skipped)
    ?`${stats.open_year_skipped}年尚未结年，图中的当年数据为模型推算，训练截至${summary.origin}年。`:'',
 ].filter(Boolean).join('\n\n');
}

function periodOf(text) {
 const parts = text.match(/\d{1,4}/g);
 if (/Q/i.test(text)) return parts[0] + '-Q' + parts[1];
 return parts.map((part, index) => index ? part.padStart(2, '0') : part).join('-');
}

function numbers(text) {
 const cleaned = text.replace(datePattern, '').replace(/版本\s*\d+(?:\.\d+)+|第\d+期|未来\d+期|\d{1,2}月/g, '')
  .replace(/\[\s*([-−]?\d+(?:\.\d+)?)\s*,\s*([-−]?\d+(?:\.\d+)?)\s*\]/g, '$1 至 $2')
  .replace(/(?<=\d)-(?=-?\d)/g, ' 至 ');
 return [...cleaned.matchAll(/[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?/g)].map(match => ({
  value: Number(match[0].replaceAll(',', '').replace('−', '-')),
  tolerance: .5 * 10 ** -(match[0].split('.')[1]?.length || 0) + 1e-8,
 }));
}

function forecastRows(evidence) {
 const forecasts = [];
 for (const receipt of evidence) for (const claim of receipt.claims || []) {
  const stats = claim.statistics;
  if (claim.operation !== 'matrix_method' || stats?.schema_version !== 1
      || stats.kind && stats.kind !== 'forecast' || !['year','quarter','month','week','day'].includes(stats.grain)
      || stats.source_result_ref && stats.source_result_ref !== receipt.result_ref
      || stats.backtest?.same_model_specification !== true) continue;
  const {forecast_periods: periods} = stats;
  const {predicted, interval_95: intervals} = stats.display ?? stats;
  if (!Array.isArray(periods) || !periods.length || !Array.isArray(predicted) || !Array.isArray(intervals)
      || periods.length !== predicted.length || periods.length !== intervals.length
      || periods.some(p => typeof p !== 'string') || new Set(periods).size !== periods.length
      || !predicted.every(Number.isFinite) || intervals.some(v => !Array.isArray(v) || v.length !== 2 || !v.every(Number.isFinite))) continue;
  for (let i = 0; i < periods.length; i++) forecasts.push({period: periods[i],
   point: predicted[i], interval: intervals[i],
   rawPoint: stats.predicted?.[i], rawInterval: stats.interval_95?.[i],
   historyCount: stats.history_count});
 }
 return forecasts;
}

function isHistoryDate(text, match) {
 const before = text.slice(0, match.index).split(/[，,；;。]/).at(-1) || '';
 const after = text.slice(match.index + match[0].length).trim();
 const afterBeforeForecast = after.split(forecastWord)[0];
 return /^(?:为|作为)?(?:起点|历史|基期)/.test(after)
  || /(?:基于|根据)[^）)]*[（(][^）)]*$/.test(before)
  || historyWord.test(before.split(forecastWord).at(-1))
  || (!forecastWord.test(before) && /^(?:至|到|[-—–~～]|、|和|\d{4}[-/]\d{2}|\d{4}\s*年(?:\s*\d{1,2}\s*月)?|\s|共\s*\d+\s*个?(?:连续)?(?:月|期|年|条)){0,36}(?:历史|实际(?:观测|数据|记录)|训练|样本|序列)/.test(afterBeforeForecast));
}

const moneyUnits = {元:1,万元:1e4,亿元:1e8};
const moneyValue = /(?<![\d.])([-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?:人民币)?(亿元|万元|元)?/g;
const annualClauses = /[。；;\n]/;
const annualHistoryRequest = /趋势|(?:逐年|历年|各年|过去|最近|近\s*[一二两三四五六七八九十\d]+\s*年)[^。；;\n]{0,28}(?:是多少|有多少|如何|怎么样|多少|统计|查询|对比|比较|变化)|(?:查看|展示|列出|统计|查询)[^。；;\n]{0,24}(?:逐年|历年|各年|历史金额|年度金额)/;

const executionColumns = ['承担单位','最新报送日期','资金执行率（%）'];

function boundPublishedReceipt(result, proof, match, request, session) {
 const {result_lineage:lineage,business_project:project,answer_contract:contract,values}=result;
 const definition=contract?.definition_evidence;
 return Boolean(result.result_ref&&result.status==='succeeded'&&!isExploratory(result)&&!result.truncated
   &&result.receipt?.read_only===true&&result.validation?.state!=='conflict'
   &&request&&session&&lineage?.request_ref===request&&lineage.session_id===session&&lineage.purpose==='answer'
   &&proof?.state==='configured'&&proof.basis==='published_configuration'&&proof.match===match
   &&proof.source_id&&proof.source_id===lineage.source_id&&proof.source_id===result.receipt.source_id
   &&proof.project_id&&proof.project_id===project?.id&&proof.project_version===project.version
   &&contract?.kind==='sql_aggregate'&&contract.result_ref===result.result_ref&&contract.complete===true
   &&contract.business_confirmation==='configured'&&definition?.match===match
   &&proof.query_fingerprint&&definition.query_fingerprint===proof.query_fingerprint
   &&proof.definition_fingerprint&&definition.definition_fingerprint===proof.definition_fingerprint
   &&values?.complete===true&&Array.isArray(values.columns)&&Array.isArray(values.rows)
   &&values.rows.length===result.row_count&&values.row_count===result.row_count);
}

export function reportedSpendingObservation(result, request=result?.result_lineage?.request_ref,
 session=result?.result_lineage?.session_id) {
 const proof=result?.validation?.published_report_spending;
 if(!proof||proof.request_ref!==request||proof.role!=='reported_expenditure'
   ||!boundPublishedReceipt(result,proof,'validated_report_spending_sql',request,session)
   ||!Object.hasOwn(moneyUnits,proof.unit)||proof.periods?.length!==2
   ||!Array.isArray(proof.additional_questions))return null;
 const [baseline,target]=proof.periods;
 const {columns,rows}=result.values;
 const field=['period','projects','covered','amount','change','rate'];
 const indices=field.map(key=>columns.indexOf(proof.columns?.[key]));
 if(baseline===target||[baseline,target].some(period=>calendarIndex(period,'year')===null)
   ||indices.includes(-1)||new Set(indices).size!==indices.length||rows.length!==2)return null;
 const facts=new Map();
 for(const row of rows){
   if(!Array.isArray(row)||row.length!==columns.length)return null;
   const [rawPeriod,projects,covered,amount,change,rate]=indices.map(index=>row[index]);
   const period=String(rawPeriod);
   if(!proof.periods.includes(period)||facts.has(period)
     ||(projects!==null&&(!Number.isSafeInteger(projects)||projects<0))
     ||(covered!==null&&(!Number.isSafeInteger(covered)||covered<0||covered>projects))
     ||(amount!==null&&(!Number.isFinite(amount)||!(projects>0&&covered===projects)))
     ||[change,rate].some(value=>value!==null&&!Number.isFinite(value)))return null;
   facts.set(period,{amount,change,rate});
 }
 const previous=facts.get(baseline), current=facts.get(target);
 const hasAmount=previous.amount!==null||current.amount!==null;
 const complete=previous.amount!==null&&current.amount!==null;
 if(complete&&(!Number.isFinite(current.change)||Math.abs(current.change-current.amount+previous.amount)>.0051
   ||(previous.amount===0?current.rate!==null:
     !Number.isFinite(current.rate)||Math.abs(current.rate-current.change/previous.amount*100)>.0051)))return null;
 return {result,proof,previous,current,baseline,target,hasAmount,complete};
}

export function reportedSpendingText({proof,previous,current,baseline,target,complete}) {
 const number=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(value);
 const money=value=>number(value)+' '+proof.unit;
 if(!complete){
   const [period,amount,missing]=current.amount===null?[baseline,previous.amount,target]:[target,current.amount,baseline];
   return `按各项目年内最新资金报送汇总，${period} 年累计已支出 **${money(amount)}**。`
     +`${missing} 年缺少覆盖所问项目范围的可比支出记录，暂无法计算两年变化。`;
 }
 const direction=current.change>0?'增加':current.change<0?'减少':'持平';
 const difference=direction==='持平'?'持平':direction+' **'+money(Math.abs(current.change))+'**';
 const change=Number.isFinite(current.rate)?'，变动幅度 **'+number(current.rate)+'%**'
   :previous.amount===0?'，基期金额为 0，增幅不适用':'';
 return `按各项目年内最新资金报送汇总，${target} 年累计已支出 **${money(current.amount)}**，`
   +`较 ${baseline} 年的 **${money(previous.amount)}** ${difference}${change}。`;
}

export function verifiedProjectSpending(results,request,session){
 const facts=results.flatMap(result=>{
  const proof=result?.validation?.published_report_spending;
  if(proof?.period_kind!=='current_project_spending'||proof.role!=='reported_expenditure'||proof.request_ref!==request
   ||!boundPublishedReceipt(result,proof,'validated_report_spending_sql',request,session)
   ||!Object.hasOwn(moneyUnits,proof.unit)||!/^\d{4}-\d\d-\d\d$/.test(proof.as_of_date))return [];
  const {columns,rows}=result.values;
  const indices=['projects','covered','amount','latest'].map(key=>columns.indexOf(proof.columns?.[key]));
  if(rows.length!==1||columns.length!==4||rows[0]?.length!==4||indices.includes(-1)||new Set(indices).size!==4)return [];
  const [projects,covered,amount,reported]=indices.map(index=>rows[0][index]);
  const latest=typeof reported==='string'?reported.slice(0,10):reported;
  if(!Number.isSafeInteger(projects)||projects<0||!Number.isSafeInteger(covered)||covered<0||covered>projects
   ||(amount===null?covered!==0:!Number.isFinite(amount)||covered===0)
   ||(latest!==null&&(!/^\d{4}-\d\d-\d\d$/.test(latest)||latest>proof.as_of_date)))return [];
  return [{result,proof,projects,covered,amount,latest}];
 });
 if(!facts.length)return null;
 const [first]=facts;
 if(!facts.every(item=>item.proof.query_fingerprint===first.proof.query_fingerprint&&item.proof.source_id===first.proof.source_id
  &&item.result.result_lineage.config_fingerprint===first.result.result_lineage.config_fingerprint
  &&JSON.stringify(item.result.values)===JSON.stringify(first.result.values)))return null;
 const {proof,projects,covered,amount,latest}=first;
 const report=latest?` 数据中的最新报送日期为 ${latest}。`:'';
 const amountText=amount===null?'当前范围缺少可核实的支出报送金额。'
  :`按各项目在截止日前的最新报送，累计已支出 **${forecastNumber.format(amount)} ${proof.unit}**，覆盖 **${covered}/${projects} 个项目**。`;
 const coverage=amount!==null&&covered<projects?` 另有 ${projects-covered} 个项目的支出金额待核实。`:'';
 return `${proof.as_of_date.slice(0,4)} 年截至 ${proof.as_of_date}，共 **${projects} 个项目**。${amountText}${coverage}${report}`;
}

function currentExecutionFact(result, request, session, question) {
 const proof=result?.validation?.current_execution;
 if(!proof||!boundPublishedReceipt(result,proof,'validated_current_execution_sql',request,session)
   ||result.validation?.request_scope?.request?.user_ref!==request
   ||result.validation.request_scope.request.quote!==question) return null;
 const {values}=result;
 const [name,date,rate]=executionColumns.map(column=>values.columns.indexOf(column));
 if([name,date,rate].includes(-1)||values.rows.some(row=>!Array.isArray(row)||row.length!==values.columns.length))return null;
 const totals=values.rows.filter(row=>row[name]==='整体');
 if(totals.length!==1)return null;
 const raw=totals[0][date];
 const targetMonth=proof.target_month;
 const [projects,covered]=['项目数量','可核算项目数'].map(column=>totals[0][values.columns.indexOf(column)]);
 const coverage=Number.isSafeInteger(projects)&&Number.isSafeInteger(covered)&&projects>0&&covered>=0&&covered<=projects;
 if(targetMonth){
   if(!/^\d{4}-(?:0[1-9]|1[0-2])$/.test(targetMonth)||!Number.isSafeInteger(projects)
     ||!Number.isSafeInteger(covered)||projects<=0||covered<0||covered>projects)return null;
 }
 if(raw===null&&coverage&&covered===0&&totals[0][rate]===null)
   return {values,name,rate,total:totals[0],day:null,targetMonth,projects,covered};
 const day=typeof raw==='string'&&raw.match(/^(\d{4}-\d{2}-\d{2})(?:$|[T ]\d{2}:\d{2}:\d{2})/)?.[1];
 if(!day||!/^\d{4}-\d{2}-\d{2}$/.test(proof.start_inclusive)||!/^\d{4}-\d{2}-\d{2}$/.test(proof.end_exclusive)
    ||day<proof.start_inclusive||day>=proof.end_exclusive)return null;
 const time=Date.parse(day+'T00:00:00Z');
 if(!Number.isFinite(time)||new Date(time).toISOString().slice(0,10)!==day)return null;
 return {values,name,rate,total:totals[0],day,targetMonth,includesAcceptance:proof.includes_acceptance===true};
}

export function verifiedCurrentExecution(question,evidence,request,session) {
 const results=evidence.filter(result=>result?.result_ref&&result.status==='succeeded'&&!isExploratory(result));
 if(results.length!==1)return null;
 const fact=currentExecutionFact(results[0],request,session,question);
 if(!fact)return null;
 const {values,name,rate,total,day}=fact;
 if(day===null){
   const period=fact.targetMonth?cnPeriod(fact.targetMonth):'本年度';
   const cutoff=fact.targetMonth?'截至月末':'截至当前日期';
   return period+'查到 **'+fact.projects+' 个项目**。'+cutoff+'没有对应的资金报送，整体资金执行率暂无法计算。';
 }
 if(fact.targetMonth){
   const [year,month]=fact.targetMonth.split('-').map(Number),period=year+'年'+month+'月';
   if(fact.includesAcceptance){
     const [projects,known,accepted,archived,share]=['项目数量','验收状态已知项目数','已验收项目数','已归档项目数','已验收项目占比（%）']
       .map(column=>total[values.columns.indexOf(column)]);
     if(Number.isSafeInteger(projects)&&projects>0&&known===projects&&Number.isSafeInteger(accepted)&&accepted>=0
       &&Number.isSafeInteger(archived)&&archived>=0&&archived<=accepted&&accepted<=projects
       &&Number.isFinite(share)&&Math.abs(share-100*accepted/projects)<.00501){
       const funding=Number.isFinite(total[rate])?'整体资金执行率 **'+total[rate]+'%**。'
         :'资金报送覆盖不足，整体资金执行率暂无法计算。';
       return period+'，'+projects+' 个项目中已验收 **'+accepted+' 个（'+share+'%）**，其中已归档 '+archived
         +' 个，未验收 '+(projects-accepted)+' 个。'+funding+'两项均按截至 '+day+' 的各项目最新报送汇总。';
     }
     if(Number.isFinite(total[rate])&&Number.isSafeInteger(known)&&Number.isSafeInteger(projects)&&known>=0&&known<projects)
       return period+'整体资金执行率 **'+total[rate]+'%**。'+projects+' 个项目中 '+known
         +' 个有可核对的验收状态，整体已验收占比暂无法计算。';
     return null;
   }
   if(!Number.isFinite(total[rate]))return null;
   return period+'整体资金执行率为 **'+new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2}).format(total[rate])
     +'%**，按截至 '+day+' 的各项目最新报送汇总。';
 }
 const units=values.rows.filter(row=>row!==total);
 const eligible=units.filter(row=>Number.isFinite(row[rate]));
 if(units.some(row=>typeof row[name]!=='string'||!/^[\p{L}\p{N} ·（）()—-]{1,60}$/u.test(row[name])))return null;
 const partialIndex=values.columns.indexOf('可核算项目资金执行率（%）');
 const [projects,covered]=['项目数量','可核算项目数'].map(column=>total[values.columns.indexOf(column)]);
 const subsetRate=partialIndex>=0&&Number.isSafeInteger(covered)&&Number.isSafeInteger(projects)
   &&covered>0&&covered<projects&&Number.isFinite(total[partialIndex]) ? total[partialIndex] : null;
 const zeroBudget=Number.isSafeInteger(projects)&&projects>0&&covered===projects
   &&total[values.columns.indexOf('项目预算总额')]===0;
 if(!Number.isFinite(total[rate])&&!eligible.length&&subsetRate===null&&!zeroBudget)return null;
 const highest=Math.max(...eligible.map(row=>row[rate]));
 const names=eligible.filter(row=>row[rate]===highest).map(row=>row[name]);
 const format=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2});
 const [year,month,date]=day.split('-').map(Number);
 const cutoff='按截至'+year+'年'+month+'月'+date+'日的最新报送汇总，';
 const overall=Number.isFinite(total[rate])?'整体资金执行率为 **'+format.format(total[rate])+'%**。'
  :subsetRate!==null?'**'+covered+' 个可核算项目的资金执行率为 '+format.format(subsetRate)+'%**（共 '+projects+' 个项目）。'
  :zeroBudget?'已核算 **'+projects+' 个项目**，预算合计为 0，执行率暂无法计算。'
  :'部分项目缺少同份报送金额，整体资金执行率暂无法计算。';
 return cutoff+overall+(names.length?(eligible.length<units.length?'在可比较的单位中，':'')
  +'执行率最高的单位'+(names.length>1?'按展示精度并列为':'为')+'**'+names.join('、')+'（'+format.format(highest)+'%）**。':'');
}

function currentExecutionIssues(text,evidence,question) {
 if(!/执行率/.test(text))return [];
 const facts=evidence.map(item=>currentExecutionFact(item,item?.result_lineage?.request_ref,item?.result_lineage?.session_id,question))
   .filter(Boolean);
 if(facts.length!==1||!facts[0].day)return [];
 const {day}=facts[0], [year,month,date]=day.split('-');
 const chinese=year+'\\s*年\\s*0?'+Number(month)+'\\s*月\\s*0?'+Number(date)+'\\s*[日号]';
 const iso=year+'[-/]'+month+'[-/]'+date;
 const dateMention=new RegExp('(?<!\\d)(?:'+iso+'|'+chinese+')(?!\\d)');
 return text.split(annualClauses).some(clause=>dateMention.test(clause)&&/最新|报送|截至|截止/.test(clause))?[]:
   ['交付缺项：已核实的最新报送日期为 '+day+'。请将该实际时点与同一查询的执行率一起报告。'];
}

export function verifiedCurrentReportBudget(evidence,request,session){
 const formal=evidence.filter(result=>result?.result_ref&&result.status==='succeeded'&&!isExploratory(result));
 if(formal.length!==1||!request||!session)return null;
 const [result]=formal;
 const proof=result.validation?.published_report_budget, contract=result.answer_contract;
 const lineage=result.result_lineage, project=result.business_project, values=result.values;
 if(proof?.period!=='current_year'||proof.match!=='validated_report_budget_sql'||proof.state!=='configured'
    ||proof.basis!=='published_configuration'||proof.request_ref!==request||lineage?.request_ref!==request
    ||lineage.session_id!==session||lineage.purpose!=='answer'||!proof.project_id||proof.project_id!==project?.id
    ||proof.project_version!==project.version||!proof.source_id||proof.source_id!==lineage.source_id
    ||proof.source_id!==result.receipt?.source_id||result.receipt?.read_only!==true||result.truncated
    ||result.validation?.state==='conflict'||contract?.kind!=='sql_aggregate'||contract.result_ref!==result.result_ref
    ||contract.business_confirmation!=='configured'||contract.complete!==true
    ||!proof.definition_fingerprint||contract.definition_evidence?.definition_fingerprint!==proof.definition_fingerprint
    ||!proof.query_fingerprint||contract.definition_evidence?.query_fingerprint!==proof.query_fingerprint
    ||values?.complete!==true||values.rows?.length!==1||values.row_count!==1||result.row_count!==1)return null;
 const measures=Object.entries(proof.measurements||{});
 if(measures.length!==1)return null;
 const [[column,measure]]=measures;
 const {columns,rows}=values;
 const indices=[proof.time_column,proof.count_column,proof.covered_column,column].map(name=>columns.indexOf(name));
 if(columns.length!==4||new Set(indices).size!==4||indices.includes(-1)||rows[0].length!==4
    ||measure?.role!=='budget'||!/^\p{Script=Han}{2,32}$/u.test(measure.label)
    ||!Object.hasOwn(moneyUnits,measure.unit))return null;
 const [year,count,covered,amount]=indices.map(index=>rows[0][index]),period=String(year);
 if(!/^\d{4}$/.test(period)||!proof.as_of_date?.startsWith(period+'-')
    ||!Number.isSafeInteger(count)||count<0||!Number.isSafeInteger(covered)||covered<0||covered>count
    ||(amount===null?covered===count&&count>0:!Number.isFinite(amount)||covered===0))return null;
 const format=n=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:8}).format(n);
 const unit=proof.count_unit;
 if(!/^(?:个|项|件)$/.test(unit)||!proof.count_column.endsWith('（'+unit+'）')
    ||!proof.covered_column.endsWith('（'+unit+'）'))return null;
 const budget=amount===null?`本轮核对到 ${format(covered)}/${format(count)} ${unit}项目有可核算预算报送，完整预算总额尚待核实。`
    : (covered<count?'其中 '+format(covered)+' '+unit+'项目有可核实的最新报送，':'')+measure.label+'为 **'+format(amount)+' '+measure.unit+'**。';
 return period+' 年截至 '+proof.as_of_date+'，共 **'+format(count)+' '+unit+'项目**。'+budget+'实际投入金额尚未核实。';
}

function annualObservation(result, question) {
 const proof=result?.validation?.annual_fiscal_window, scope=result?.validation?.request_scope?.forecast_target;
 const lineage=result?.result_lineage, project=result?.business_project, values=result?.values;
 if (proof?.schema_version!==1||proof.state!=='checked'||proof.basis!=='client_calendar'
     || !proof.current_date?.startsWith(proof.current_open_year+'-')||scope?.grain!=='year'
     || !proof.request_ref||proof.request_ref!==lineage?.request_ref||scope.evidence?.user_ref!==proof.request_ref
     || scope.evidence?.quote!==question||scope.period!==proof.target_period
     || !proof.source_id||proof.source_id!==lineage.source_id||proof.source_id!==result.receipt?.source_id
     || !proof.publication?.id||proof.publication.id!==project?.id||proof.publication.version!==project.version
     || !result.result_ref||result.status!=='succeeded'||result.receipt?.read_only!==true||lineage.purpose!=='answer'
     || isExploratory(result)||result.truncated||result.validation?.state==='conflict'||values?.complete!==true
     || !Array.isArray(values.columns)||!Array.isArray(values.rows)||values.rows.length!==result.row_count
     || values.rows.length!==values.row_count||!values.rows.length||values.rows.length>100) return null;
 const entries=Object.entries(proof.measurements||{});
 if (entries.length!==1) return null;
 const [[column,measure]]=entries, time=values.columns.indexOf(proof.time_column), value=values.columns.indexOf(column);
 if (time<0||value<0||time===value||values.columns.lastIndexOf(proof.time_column)!==time
     ||values.columns.lastIndexOf(column)!==value||!['budget','actual_fiscal'].includes(measure?.role)
     ||typeof measure.label!=='string'||typeof measure.unit!=='string'
     ||!Object.hasOwn(moneyUnits,measure.unit.replace(/^人民币/,''))) return null;
 const yearSet=new Set(), observed=[], unobserved=[];
 for(const row of values.rows){
  const year=String(row[time]);
  if (calendarIndex(year,'year')===null||yearSet.has(year)) return null;
  yearSet.add(year);
  if(Number.isFinite(row[value])&&year<=proof.current_open_year) observed.push([year,row[value]]);
  else if(row[value]===null&&year<=proof.last_closed_year) unobserved.push(year);
 }
 observed.sort(([a],[b])=>b.localeCompare(a));
 return observed.length?{result,proof,column,measure,latest:observed[0],observed,unobserved}:null;
}

function closedAnnualObservation(result,question){
 const observation=annualObservation(result,question);
 if(!observation)return null;
 const {proof,measure,observed}=observation, years=proof.requested_years;
 if(!Array.isArray(years)||years.length<2||years.length>36
  ||!years.every((year,i)=>/^\d{4}$/.test(year)&&year<=proof.last_closed_year&&(!i||Number(year)===Number(years[i-1])+1))
  ||!observed.every(([year])=>years.includes(year))
  ||!/^[\p{Script=Han}A-Za-z\d ()（）·]{1,60}$/u.test(measure.label))return null;
 const axis=result.values.columns.indexOf(proof.time_column);
 if(result.values.rows.some(row=>!years.includes(String(row[axis]))))return null;
 return {...observation,years};
}

function closedAnnualTrendText({proof,measure,observed,years},question){
 const known=new Map(observed);
 const table=['| 年度 | '+measure.label+'（'+measure.unit+'） |','| --- | ---: |',
  ...years.map(year=>`| ${year} | ${known.has(year)?forecastNumber.format(known.get(year)):'缺少观测'} |`)].join('\n');
 const missing=years.filter(year=>!known.has(year));
 const changes=years.slice(1).flatMap((year,index)=>known.has(year)&&known.has(years[index])
   ?[Math.sign(known.get(year)-known.get(years[index]))]:[]);
 const direction=changes.includes(1)&&changes.includes(-1)?'有升有降'
   :changes.includes(1)?'上升':changes.includes(-1)?'回落':changes.length?'持平':'';
 const basis=measure.role==='budget'&&!/预算/.test(question)
   ?'实际投入金额尚未核实，缺少同范围、同口径的已结年支出记录。已查得的项目预算趋势如下。'
   :'现有回执尚未证明各年度金额的来源、单位和统计范围完整可比。';
 const trend=direction?`相邻有数年度的${measure.label}${direction}。`:'';
 const gaps=missing.length?`${missing.join('、')} 年缺少同口径金额。`:'';
 return [`暂无法给出 ${proof.target_period} 年的预测。${basis}`,trend+gaps,table].filter(Boolean).join('\n\n');
}

export function verifiedClosedBudgetTrend(question,results,requestRef,sessionId){
 if(!annualHistoryRequest.test(question)||!requestRef||!sessionId)return null;
 const plans=results.filter(result=>{
  const proof=result.validation?.published_report_budget;
  return proof?.period==='closed_budget_trend'
   &&boundPublishedReceipt(result,proof,'validated_report_budget_sql',requestRef,sessionId);
 }).map(result=>closedAnnualObservation(result,question)).filter(Boolean);
 if(plans.length!==1||plans[0].measure.role!=='budget')return null;
 return closedAnnualTrendText(plans[0],question);
}

export function annualTrendAfterForecastRejection(question,results,args,error){
 if(!/^\s*(?:Error:\s*)?FORECAST_PREREQUISITE:/.test(error)||!annualHistoryRequest.test(question)
  ||!Array.isArray(args?.columns)||args.columns.length!==1)return null;
 const observation=closedAnnualObservation(results.find(item=>item.result_ref===args.result_ref),question);
 if(!observation)return null;
 const {result,proof,column,measure,observed}=observation;
 if(args.columns[0]!==column||args.time_column!==proof.time_column)return null;
 const competitors=results.filter(item=>item!==result).map(item=>closedAnnualObservation(item,question))
    .filter(item=>item&&item.measure.role===measure.role);
 const lineage=result.result_lineage;
 if(competitors.some(({result:other,observed:values})=>{
   const match=other.result_lineage;
   const samePopulation=result.query_identity&&result.query_identity===other.query_identity
     ||lineage.population_fingerprint&&lineage.population_fingerprint===match.population_fingerprint;
   return !samePopulation||!lineage.config_fingerprint||lineage.config_fingerprint!==match.config_fingerprint
     ||lineage.request_ref!==match.request_ref||lineage.session_id!==match.session_id||lineage.source_id!==match.source_id
     ||JSON.stringify(values)!==JSON.stringify(observed);
 }))return null;
 return closedAnnualTrendText(observation,question);
}

function annualReference(result, primary, {requireAmount=true}={}) {
 const proof=result.validation?.published_report_budget, lineage=primary.result_lineage;
 const published=primary.validation?.published_report_budget;
 const expected=published?.historical_reference;
 if(!expected||proof?.period!=='historical_reference'||proof.year!==expected.year
    ||proof.primary_definition_fingerprint!==published.definition_fingerprint
    ||proof.definition_fingerprint!==expected.definition_fingerprint||proof.query_fingerprint!==expected.query_fingerprint
    ||proof.source_id!==published.source_id||proof.project_id!==published.project_id||proof.project_version!==published.project_version
    ||!lineage?.config_fingerprint||lineage.config_fingerprint!==result.result_lineage?.config_fingerprint
    ||proof.request_ref!==lineage.request_ref||!boundPublishedReceipt(result,proof,'validated_report_budget_sql',lineage.request_ref,lineage.session_id))
   return null;
 const entries=Object.entries(proof.measurements||{}), values=result.values;
 if(entries.length!==1||values.columns.length!==1||values.rows.length!==1||values.rows[0]?.length!==1)return null;
 const [[column,measure]]=entries, amount=values.rows[0][0];
 if(values.columns[0]!==column||measure?.role!=='budget'||!/^[\p{Script=Han}]{2,32}$/u.test(measure.label)
    ||!Object.hasOwn(moneyUnits,measure.unit?.replace(/^人民币/,''))||!Number.isFinite(amount)&&(requireAmount||amount!==null))return null;
 return {year:proof.year,amount,measure};
}

export function annualReportBudgetReference(primary,recipe,question,request,session) {
 const proof=primary.validation?.published_report_budget, guide=recipe?.historical_reference, args=guide?.query_request;
 if(!boundPublishedReceipt(primary,proof,'validated_report_budget_sql',request,session)
   ||proof.period!=='annual_history'||proof.request_ref!==request||args?.source_id!==proof.source_id
   ||args.purpose!=='answer'||args.chart_type!=='table'||typeof args.sql!=='string'||typeof args.title!=='string'
   ||proof.historical_reference?.year!==guide.year||proof.historical_reference.definition_fingerprint!==guide.definition_fingerprint
   ||proof.historical_reference.query_fingerprint!==guide.query_fingerprint)return null;
 const observation=annualObservation(primary,question);
 if(!observation||observation.proof.last_closed_year!==guide.year
    ||observation.observed.some(([year])=>year===guide.year))return null;
 return args;
}

export function verifiedAnnualBudgetPartial(question,evidence){
 if(!annualHistoryRequest.test(question)||!/(?:投资|投入|支出)(?:金额|额|情况)/.test(question)
    ||/预算|另外|分别|排行|排名|导出|画图|图表|(?:按|分)单位|各(?:单位|地区)|占比/.test(question))return null;
 const results=evidence.filter(result=>result?.result_ref&&result.status==='succeeded'&&!isExploratory(result));
 const observations=results.map(result=>annualObservation(result,question)).filter(Boolean);
 if(observations.length!==1)return null;
 const {result,proof,measure,latest,observed,unobserved}=observations[0];
 const other=results.filter(receipt=>receipt!==result);
 if(other.length>1||other.length===1&&!annualReference(other[0],result,{requireAmount:false}))return null;
 const reference=other.length?annualReference(other[0],result):null;
 if(measure.role!=='budget'||latest[0]!==proof.current_open_year||observed.length!==1
    ||(!unobserved.includes(proof.last_closed_year)&&!reference)||!/^[\p{Script=Han}]{2,32}$/u.test(measure.label)
    ||proof.last_closed_year!==String(Number(proof.current_open_year)-1)
    ||proof.target_period!==String(Number(proof.current_open_year)+1))return null;
 const amount=new Intl.NumberFormat('zh-CN',{maximumFractionDigits:8}).format(latest[1]);
 return [
  `${latest[0]}年尚未结束，本次查询核实的${measure.label}为 **${amount} ${measure.unit}**。`,
  (reference?`${reference.year}年按独立历史预算台账核实的${reference.measure.label}为 **${new Intl.NumberFormat('zh-CN',{maximumFractionDigits:8}).format(reference.amount)} ${reference.measure.unit}**，可作预算参考。`
   :`${proof.last_closed_year}年在同一查询范围内未返回金额。`)
  +`实际投入金额尚未核实，缺少连续五个已结年度的同口径数据，暂无法给出${proof.target_period}年预测。`,
 ].filter(Boolean).join('\n\n');
}

function annualAmountShown(text, [year,amount], measure) {
 const kind=measure.role==='budget'?/预算/:/财政(?:资金)?(?:实际)?(?:拨付|投入|支出)/;
 const unrelated=measure.role==='budget'?/实际(?:财政)?(?:投入|支出|拨付)|投资(?:金额|额)|(?:执行|余额|剩余|差额|同比|环比)(?:率|金额|数额)?/:/预算/;
 const targetUnit=measure.unit.replace(/^人民币/,'');
 const matchesAmount=(text, headerUnit)=>[...text.matchAll(moneyValue)].some(match=>{
  const unit=match[2]||headerUnit;
  if(!unit)return false;
  const scale=moneyUnits[unit]/moneyUnits[targetUnit];
  const value=Number(match[1].replaceAll(',','').replace('−','-'));
  return Math.abs(value*scale-amount)<=.5*scale*10**-(match[1].split('.')[1]?.length||0)+1e-8;
 });
 const tableRow=new RegExp('^'+year+'\\s*年?(?:度)?$');
 let headers=[];
 for(const line of text.split('\n')){
  if(!line.includes('|')){headers=[];continue;}
  const cells=line.trim().replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim());
  if(cells.some(cell=>tableRow.test(cell))&&cells.some((cell,i)=>kind.test(headers[i]||'')&&!unrelated.test(headers[i])
      &&matchesAmount(cell,headers[i].match(/亿元|万元|元/)?.[0])))return true;
  if(cells.some(cell=>kind.test(cell)))headers=cells;
 }
 return text.split(annualClauses).some(clause=>{
  const mentions=[...clause.matchAll(/(?<!\d)(\d{4})\s*年(?:度)?/g)];
  return [...clause.matchAll(moneyValue)].some(match=>{
   if(!match[2]||!matchesAmount(match[0]))return false;
   const nearest=mentions.findLast(mention=>mention.index<match.index)||(mentions.length===1?mentions[0]:null);
   if(nearest?.[1]!==year)return false;
   const context=clause.slice(0,match.index).split(/,(?!\d{3}(?:[^\d]|$))/).at(-1).slice(-60);
   return kind.test(context)&&!unrelated.test(context);
  });
 });
}

function annualPartialIssues(text,evidence,question) {
 if(!annualHistoryRequest.test(question))return [];
 const observations=evidence.map(result=>annualObservation(result,question)).filter(Boolean);
 if(observations.length!==1)return [];
 const {result,proof,column,measure,latest}=observations[0];
 const validForecast=evidence.some(receipt=>receipt.result_ref===result.result_ref&&(receipt.claims||[]).some(claim=>{
  const stats=claim.statistics;
  return stats?.source_result_ref===result.result_ref&&stats.grain==='year'&&stats.kind==='forecast'
   &&stats.time_column===proof.time_column&&stats.value_column===column
   &&forecastRows([{...receipt,claims:[claim]}]).some(row=>row.period===proof.target_period);
 }));
 if(validForecast)return [];
 const open=latest[0]===proof.current_open_year;
 const missingOpen=open&&!text.split(/[。；;\n]/).some(clause=>
  clause.includes(latest[0])&&/未结|未结束|未完成|尚未届满|进行中|截至/.test(clause));
 const issues=annualAmountShown(text,latest,measure)&&!missingOpen?[]:[
  `年度查询已取得${latest[0]}年${measure.label}${forecastNumber.format(latest[1])}${measure.unit}，请用实际口径与单位交付。`
  +(open?'该年尚未结束，可作为当前参考。':'')+'对无法核实的历史数据或预测仅说明实际缺项。'];
 const references=evidence.map(receipt=>annualReference(receipt,result)).filter(Boolean);
 if(references.length===1){
  const {year,amount,measure:reference}=references[0];
  if(!annualAmountShown(text,[year,amount],reference)||!/(?:单独|独立|另|分别)[^。\n]{0,26}预算|预算[^。\n]{0,20}(?:参考|不同)/.test(text))
   issues.push(`已核实 ${year} 年单独的${reference.label} ${forecastNumber.format(amount)} ${reference.unit}，请作为预算参考交付。两个查询的年度范围及数据定义分别保留。`);
 }
 return issues;
}

export function forecastIssues(text, evidence, question) {
 const sections = String(text).replaceAll('**', '').split(/[。；;\n]/);
 const forecasts = forecastRows(evidence);
 let previousTarget;
 for (const section of sections) {
  if (negated.test(section) || !forecastWord.test(section) && !intervalWord.test(section)) continue;
  if (methodDetails.test(section) && !/预测值|预测路径/.test(section) && !intervalWord.test(section)) continue;
  const mentions = [...section.matchAll(datePattern)].filter(match => !isHistoryDate(section, match));
  const countClaim = [...section.matchAll(sampleSize)].map(match => Number(match[1]));
  if (countClaim.length && forecasts.some(row => Number.isFinite(row.historyCount))
      && !forecasts.some(row => countClaim.every(count => count === row.historyCount))) return [wrongValue];
  const hasNumericClaim = asserted.test(section) || intervalWord.test(section) || mentions.length && section.trim().startsWith('预测');
  if (!hasNumericClaim || !numbers(section.replace(sampleSize, '')).length) {
   if (mentions.length === 1) previousTarget = periodOf(mentions[0][0]);
   continue;
  }
  if (!forecasts.length) return [missing];
  if (!mentions.length) {
   const possible = previousTarget ? forecasts.filter(row => row.period === previousTarget) : forecasts;
   if (!possible.length) return [wrongPeriod];
   if (!supported(section, possible)) return [wrongValue];
   continue;
  }
  for (const [index, match] of mentions.entries()) {
   const target = periodOf(match[0]);
   const possible = forecasts.filter(row => row.period === target);
   if (!possible.length) return [wrongPeriod];
   const clause = section.slice(index ? match.index : 0, mentions[index + 1]?.index ?? section.length);
   if (!supported(clause, possible)) {
    const rawMatched = possible.some(r => Number.isFinite(r.rawPoint) && Array.isArray(r.rawInterval) && supported(clause, [{...r, point: r.rawPoint, interval: r.rawInterval}]));
    return [rawMatched ? wrongValue + `（当前为计数指标，请直接引用取整后的展示预测值 ${possible[0].point} 及区间 ${possible[0].interval.join(' 至 ')}）` : wrongValue];
   }
   previousTarget = target;
  }
 }
 const referenceOnly=evidence.some(receipt=>(receipt.claims||[]).some(claim=>
   claim.statistics?.kind==='forecast'&&claim.statistics.publication_level==='reference_only'));
 const gradeIssues=referenceOnly&&!/试算参考/.test(String(text))?
   ['发布级别：回测仅1个留出点，须标明试算参考，不可作为可引用结论。']:[];
 if(!question)return gradeIssues;
 const plain=String(text).normalize('NFKC').replaceAll('**','');
 return [...gradeIssues,...currentExecutionIssues(plain,evidence,question),...annualPartialIssues(plain,evidence,question)];
}

function matches(number, value) {
 return Math.abs(number.value - value) <= number.tolerance;
}

function supported(clause, forecasts) {
 const sizes = [...clause.matchAll(sampleSize)].map(match => Number(match[1]));
 clause = clause.replace(sampleSize, '');
 forecasts = forecasts.filter(row => !Number.isFinite(row.historyCount) || sizes.every(size => size === row.historyCount));
 const marker = clause.match(intervalWord);
 if (!marker) return forecasts.some(row => numbers(clause).every(number => matches(number, row.point)));
 const points = numbers(clause.slice(0, marker.index).replace(/95\s*%\s*$/, ''));
 const bounds = numbers(clause.slice(marker.index + marker[0].length).replace(/^\s*[（(]?\s*95\s*%\s*[)）]?/, '')
  .split(/[（(](?!\s*[-−]?\d)|[，,]\s*(?![\d\s])/)[0]);
 return forecasts.some(row => points.every(number => matches(number, row.point)) && (
  bounds.length === 0 || bounds.length === 1 && row.interval.every(value => matches(bounds[0], value))
  || bounds.length === 2 && bounds.every((number, index) => matches(number, row.interval[index]))));
}
