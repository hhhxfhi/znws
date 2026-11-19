// Deterministic delivery for verified monthly results.
import {explicitChartType,isExploratory} from './presentation-intent.js';
import {metricDeliveryIdentity} from './answer-delivery.js';
import {monthlyReply} from './confirmed-monthly-answer.js';

const MONTH=/^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$/;
const MONTHLY=/按\s*月|逐月|每(?:个)?月|各月|月度/;
const COUNT_REQUEST=/件数|条数|笔数|次数|数量|办理量|记录数|有多少(?:件|条|笔|宗|次)/;
const TABLE=/(?:表格|数据表|统计表|汇总表|明细表)|(?:列表|列成表|列为表|以表呈现|用表展示)/;
const EXTRA_DELIVERY=/同比|环比|对比|比较|预测|预估|排名|排行|最多|最少|最高|最低|前[几一二三四五六七八九十\d]+(?:名|项|类)|占比|比例|均值|平均|最大|最小|中位|分位|方差|标准差|极差|按(?:照)?(?:地区|地市|城市|区县|单位|机构|类别|种类|类型|等级|专业)|分组|各(?:市|地|区|单位|类)|原因|建议|阈值|明细名单|导出|下载|拆分|分开|另外|顺便|[A-Za-z]+[_][A-Za-z_]+/;
const ADDITIONAL_ACTION=/(?:并且?|以及|同时|还要?|再|[。！？；;\n])\s*(?:请|帮我)?\s*(?=(?:给出|给我|列出|列成|列为|查[询看找出]|统计|计算|分析|说明|标注|注明|展示|提供|附上|显示|画|绘制|用|以))([^。！？；;，,\n]+)/g;
const PRESENTATION_ONLY=/^(?:(?:给出|给我|列出|列成|列为|说明|标注|注明|展示|提供|附上|显示|画|绘制|用|以|一份|一个|月度|逐月|各月|对应的?|\s))*(?:数据表|表格|统计表|汇总表|表|(?:竖向|横向)?柱状图|条形图|折线图|面积图|(?:哪些月份缺少观测|缺测月份|未观测月份)|(?:已观测月份合计))(?:(?:和|与|、)(?:数据表|表格|统计表|汇总表|(?:竖向|横向)?柱状图|条形图|折线图|面积图))?(?:\s*(?:展示|呈现))?$/;
const YEAR_TOTAL=/^(?:(?:并且?|以及|同时|再|还要?)\s*)?(?:请\s*)?(?:(?:给出|给我|计算|算出|求|统计|列出|附上|提供|显示)\s*)?(?:全年|整年|年度|(?:12|十二)\s*个?\s*月)\s*的?\s*(?:合计|总计|总量|总数)(?:是多少|多少)?$/;
const TOTAL_CLAUSE_BOUNDARY=/[。！？?!；;，,\n]+|(?:并且?|以及|同时)(?=\s*(?:请\s*)?(?:给出|给我|计算|算出|求|列出|附上|提供|显示|全年|整年|年度))/;
const COUNT_UNIT=new Set(['件','条','宗','项','个','家','户','人','本','张','份','笔','台','辆','套','批','单','次']);
const ALLOWED_CHART=new Set(['table','column','bar','line','area']);
const count=n=>Number.isSafeInteger(n)&&n>=0;
const sameSet=(a,b)=>Array.isArray(a)&&a.length===b.length&&new Set(a).size===a.length
  &&a.every(item=>b.includes(item));
const safeHeader=value=>typeof value==='string'&&/^[\p{Script=Han}a-zA-Z0-9（）()·、\s-]{1,70}$/u.test(value)
  &&!(/[\r\n]/.test(value))?value.trim():null;

export function publishedMonthlyMetricPlan(catalogs,requestRef) {
  const catalog=catalogs.at(-1);
  if(!requestRef||catalog?.status!=='succeeded'||catalog.sources?.length!==1)return null;
  const [source]=catalog.sources;
  const plan=source.published_monthly_metric_query,query=plan?.query_request;
  const publication=source.source_context?.business_project??source.business_project;
  if(plan?.schema_version!==1||plan.request_ref!==requestRef||plan.source_id!==source.id||query?.source_id!==source.id
    ||!publication?.id||publication.id!==plan.publication?.id||publication.version!==plan.publication?.version
    ||!query.metric_id||!query.metric_version||!query.title||!Array.isArray(query.periods)||query.periods.length!==12
    ||!query.periods.every((month,i)=>MONTH.test(month)&&month===query.periods[0].slice(0,4)+'-'+String(i+1).padStart(2,'0'))
    ||query.chart_type&&!ALLOWED_CHART.has(query.chart_type))return null;
  return plan;
}

export function verifiedPublishedMonthlyMetricAnswer(plan,evidence,userText) {
  const requestedYear=explicitAnnualTotalYear(userText);
  if(!plan&&!requestedYear)return null;
  const results=evidence.filter(result=>result?.status==='succeeded'&&result.result_ref&&!isExploratory(result));
  if(results.length!==1)return null;
  const [receipt]=results;
  const identity=metricDeliveryIdentity(receipt),query=plan?.query_request;
  const chart=query?.chart_type||explicitChartType(userText||'');
  if(!identity||identity.scope.dimensions.length||identity.scope.filters.length
    ||receipt.visualization?.fallback||chart&&receipt.visualization?.actual!==chart)return null;
  if(plan&&(identity.source!==query.source_id||identity.project.id!==plan.publication.id
    ||identity.project.version!==plan.publication.version||identity.metric!==query.metric_id||identity.version!==query.metric_version
    ||!sameSet(identity.scope.periods,query.periods)))return null;
  if(plan?.delivery==='annual_total')return publishedAnnualTotalReply(receipt,identity,query.periods[0].slice(0,4),true);
  if(requestedYear)return publishedAnnualTotalReply(receipt,identity,requestedYear);
  return monthlyReply({periods:query.periods,ref:receipt.result_ref,receipt},receipt);
}

function explicitAnnualTotalYear(userText){
  if(typeof userText!=='string')return null;
  const parts=userText.normalize('NFKC').split(TOTAL_CLAUSE_BOUNDARY).map(part=>part.trim()).filter(Boolean);
  if(parts.filter(part=>YEAR_TOTAL.test(part)).length!==1)return null;
  const series=parts.filter(part=>MONTHLY.test(part)&&!PRESENTATION_ONLY.test(part));
  if(series.length!==1||/和|与|及|、|分别|各自/.test(series[0]))return null;
  const monthlyRequest=parts.filter(part=>!YEAR_TOTAL.test(part)).join('。');
  if(/累计|截至|截止|期末|前[一二三四五六七八九十\d]+个月|\d{4}\s*年\s*\d{1,2}\s*月/.test(monthlyRequest))return null;
  const years=requestedYears(monthlyRequest,true);
  return Array.isArray(years)&&years.length===1?years[0]:null;
}

function decimalTotal(values){
  // Add the decimal forms of the typed cells so the display avoids binary rounding artifacts.
  const terms=values.map(value=>{
    const [decimal,exponent='0']=String(value).split('e');
    const fraction=decimal.split('.')[1]||'';
    return {digits:BigInt(decimal.replace('.','')),scale:fraction.length-Number(exponent)};
  });
  const scale=Math.max(0,...terms.map(term=>term.scale));
  const total=terms.reduce((sum,term)=>sum+term.digits*10n**BigInt(scale-term.scale),0n);
  const sign=total<0n?'-':'';
  const digits=String(total<0n?-total:total).padStart(scale+1,'0');
  if(!scale)return sign+digits;
  const fraction=digits.slice(-scale).replace(/0+$/,'');
  return sign+digits.slice(0,-scale)+(fraction?'.'+fraction:'');
}

function publishedAnnualTotalReply(receipt,identity,year,totalOnly=false){
  const metric=receipt.metric_contract;
  const periods=Array.from({length:12},(_,i)=>year+'-'+String(i+1).padStart(2,'0'));
  const contract=receipt.answer_contract;
  const label=safeHeader(metric.name),unit=safeHeader(identity.unit);
  if(!label||!unit||metric.additive!==true||metric.additive_over_time!==true||metric.ratio!==false
    ||!sameSet(identity.scope.periods,periods)||!sameSet(contract.periods,periods)
    ||receipt.facts.length!==periods.length)return null;
  const observations=new Map();
  for(const {dimensions,value} of receipt.facts){
    const period=dimensions?.period;
    if(!periods.includes(period)||Object.keys(dimensions).length!==1||observations.has(period)||!Number.isFinite(value)
      ||COUNT_UNIT.has(unit)&&!count(value))return null;
    observations.set(period,value);
  }
  const values=periods.map(period=>observations.get(period));
  const total=decimalTotal(values);
  if(COUNT_UNIT.has(unit)&&!count(Number(total)))return null;
  if(totalOnly){
    const display=total.replace(/^-?\d+/,integer=>integer.replace(/\B(?=(\d{3})+(?!\d))/g,','));
    return '**'+year+' 年全年合计 '+display+' '+unit+'**，按 1–12 月的记录加总。';
  }
  const table=['| 月份 | '+label+'（'+unit+'） |','|---|---:|',
    ...periods.map((period,index)=>'| '+period+' | '+values[index]+' |')].join('\n');
  return '**'+year+' 年全年合计 '+total+' '+unit+'**。\n\n'+table;
}

function requestedYears(userText,annualTotal=false){
  if(typeof userText!=='string')return null;
  const text=userText.normalize('NFKC');
  if(!MONTHLY.test(text)||EXTRA_DELIVERY.test(text))return null;
  if([...text.matchAll(ADDITIONAL_ACTION)].some(([,action])=>!PRESENTATION_ONLY.test(action.trim())))return null;
  if(/(?:只|仅)(?:要|用|给)[^。；;\n]{0,12}图|不要|不用|不必|无需|无须/.test(text.replace(/(?:不要|不用|不必|无需|无须)\s*(?:补零|补成\s*0)/g,'')))return null;
  const deliverable=annualTotal||TABLE.test(text)||explicitChartType(text)||COUNT_REQUEST.test(text);
  if(/(?:月|日)\s*(?:至|到|—|–|~)|\d+\s*(?:至|到|—|–|~|-)\s*\d+\s*月|(?:上|下)半年|第?[一二三四1234]季/.test(text))return null;
  const years=[...new Set(text.match(/(?<!\d)(?:19|20)\d{2}(?!\d)/g)||[])].sort();
  if(years.length===1&&deliverable)return years;
  if(years.length===2&&Number(years[1])===Number(years[0])+1)return years;
  if(!years.length&&deliverable&&/本(?:年度?|年)|今年|当年/.test(text))return true;
  return null;
}

function equivalentMonthlyCountReceipt(result,extra,requested){
  const current=monthlyCounts(result,requested),prior=monthlyCounts(extra,requested);
  if(!current||!prior||current.unit!==prior.unit||current.total!==prior.total)return false;
  const a=current.coverage,b=prior.coverage;
  if(Boolean(a.counts_rows)!==Boolean(b.counts_rows))return false;
  if(a.query_window?.state!=='checked'||b.query_window?.state!=='checked'
    ||a.query_window.start_inclusive!==b.query_window.start_inclusive
    ||a.query_window.end_exclusive!==b.query_window.end_exclusive)return false;
  if(!sameSet(a.requested_months,b.requested_months)
    ||!sameSet(a.returned_months,b.returned_months)
    ||!sameSet(a.unreturned_months,b.unreturned_months))return false;
  if((a.current_month_unclosed||null)!==(b.current_month_unclosed||null)
    ||!sameSet(a.future_months||[],b.future_months||[]))return false;
  if(current.observed.size!==prior.observed.size)return false;
  for(const [period,value] of current.observed)if(prior.observed.get(period)!==value)return false;
  return true;
}

function monthlyReceipt(evidence,requested){
  const results=evidence.filter(r=>r?.status==='succeeded'&&r.result_ref&&!isExploratory(r));
  const primary=results.filter(r=>r.validation?.period_grain?.state!=='auxiliary'
    &&r.execution?.operation!=='analysis.field_statistics');
  if(!primary.length)return null;
  const result=primary.at(-1);
  const lineage=result.result_lineage,proof=result.validation?.period_grain;
  // Other successful results are safe to omit only when the server bound them
  // as diagnostics of this exact primary result in the current native request.
  for(const extra of results){
    if(extra===result)continue;
    if(extra.execution?.operation==='analysis.field_statistics'&&extra.result_ref===result.result_ref
      &&result.execution_scope&&extra.execution_scope===result.execution_scope)continue;
    if(extra.validation?.period_grain?.state!=='auxiliary'){
      if(['request_ref','session_id','source_id','config_fingerprint','population_fingerprint','purpose'].some(k=>
          !lineage?.[k]||lineage[k]!==extra.result_lineage?.[k])
        ||extra.receipt?.read_only!==true||extra.validation?.state==='conflict'||extra.truncated
        ||extra.claims?.length||extra.verified_claims?.length
        ||extra.answer_contract?.business_confirmation!==result.answer_contract?.business_confirmation)return null;
      const exactMatch=Boolean(result.query_identity&&result.query_identity===extra.query_identity
        &&JSON.stringify(extra.values)===JSON.stringify(result.values)
        &&JSON.stringify(extra.visualization)===JSON.stringify(result.visualization)
        &&JSON.stringify(extra.answer_contract?.monthly_sql_coverage)===JSON.stringify(result.answer_contract?.monthly_sql_coverage));
      if(!exactMatch&&!equivalentMonthlyCountReceipt(result,extra,requested))return null;
      continue;
    }
    const auxiliary=extra.validation?.period_grain;
    if(auxiliary?.source_result_ref!==result.result_ref||auxiliary.requested!=='month'
      ||proof?.state!=='checked'||proof.actual!=='month'
      ||['request_ref','session_id','source_id','config_fingerprint'].some(k=>!lineage?.[k]
        ||lineage[k]!==extra.result_lineage?.[k]))return null;
  }
  return result;
}

function monthlyCounts(result,requested){
  const contract=result.answer_contract,values=result.values,coverage=contract?.monthly_sql_coverage;
  if(result.truncated||result.metric_contract||result.report||result.receipt?.read_only!==true
    ||result.validation?.state==='conflict'||contract?.schema_version!==1||contract.kind!=='sql_aggregate'
    ||contract.result_ref!==result.result_ref||!contract.complete||contract.grouped_distinct
    ||values?.complete!==true||!coverage||coverage.query_window?.state!=='checked'
    ||coverage.outside_requested_months?.length||!Array.isArray(values.columns)||values.columns.length!==2
    ||!Array.isArray(values.rows)||values.rows.length!==result.row_count||values.rows.length!==values.row_count
    ||!Array.isArray(coverage.requested_months))return null;

  const year=String(coverage.requested_months[0]).slice(0,4);
  const years=requested===true?[year]:requested;
  if(years[0]!==year||coverage.requested_months.length!==years.length*12)return null;
  const periods=years.flatMap(value=>Array.from({length:12},(_,i)=>value+'-'+String(i+1).padStart(2,'0')));
  if(!periods.every(p=>MONTH.test(p))||!sameSet(coverage.requested_months,periods)
    ||coverage.query_window.start_inclusive!==periods[0]+'-01'
    ||coverage.query_window.end_exclusive!==String(Number(years.at(-1))+1)+'-01-01')return null;

  const {count_column:column,month_column:month,unit,observations,observed_total:total}=coverage;
  if(!COUNT_UNIT.has(unit)||result.analysis_options?.column_units?.[column]!==unit||!count(total)
    ||!Array.isArray(observations)||!observations.length||observations.length>periods.length)return null;
  const monthIndex=values.columns.indexOf(month),countIndex=values.columns.indexOf(column);
  if(monthIndex<0||countIndex<0||monthIndex===countIndex)return null;
  const cells=new Map();
  for(const row of values.rows){
    if(!Array.isArray(row)||row.length!==2||!periods.includes(row[monthIndex])||cells.has(row[monthIndex])
      ||row[countIndex]!==null&&!count(row[countIndex]))return null;
    cells.set(row[monthIndex],row[countIndex]);
  }
  const observed=new Map();
  for(const {period,value} of observations){
    if(!periods.includes(period)||!count(value)||observed.has(period)||cells.get(period)!==value)return null;
    observed.set(period,value);
  }
  if([...cells].some(([period,value])=>value!==null&&!observed.has(period)))return null;
  const missing=periods.filter(period=>!observed.has(period));
  if(!sameSet(coverage.returned_months,[...observed.keys()])||!sameSet(coverage.unreturned_months,missing))return null;
  if([...observed.values()].reduce((sum,value)=>sum+value,0)!==total)return null;
  return {years,periods,observed,missing,coverage,total,unit,column};
}

export function verifiedSqlMonthlyAnswer(userText,evidence){
  return formatSqlMonthlyAnswer(userText,evidence,requestedYears(userText));
}

export function verifiedSqlAnnualRefinement(year,evidence){
  return /^(?:19|20)\d{2}$/.test(year)&&formatSqlMonthlyAnswer('',evidence,[year])||null;
}

function formatSqlMonthlyAnswer(userText,evidence,requested){
  if(!requested||!Array.isArray(evidence))return null;
  const result=monthlyReceipt(evidence,requested);
  if(!result||result.claims?.length||result.verified_claims?.length)return null;
  const chart=explicitChartType(userText),actual=result.visualization?.actual;
  if(!ALLOWED_CHART.has(actual)||chart&&actual!==chart||result.visualization?.fallback)return null;
  const series=monthlyCounts(result,requested);
  if(!series)return null;
  const {years,periods,observed,missing,coverage,total,unit,column}=series;
  const suffix=['_'+unit,'（'+unit+'）','('+unit+')'].find(value=>column.endsWith(value))||'';
  const label=safeHeader(column.slice(0,column.length-suffix.length));
  if(!label)return null;
  const header=label+'（'+unit+'）';
  const table=TABLE.test(userText)||years.length>1||!chart?['| 月份 | '+header+' |','|---|---:|',
    ...periods.map(period=>'| '+period+' | '+(observed.get(period)??'')+' |')].join('\n'):'';
  const gapSuffix=table?'，表中保留为空。':'。';
  const notes=[];
  const current=coverage.current_month_unclosed;
  const isOpen=current&&periods.includes(current);
  if(missing.length){
    const future=(coverage.future_months||[]).filter(month=>missing.includes(month));
    const elapsed=missing.filter(month=>month!==current&&!future.includes(month));
    if(elapsed.length)notes.push(elapsed.join('、')+' 未返回观测，数据覆盖未知'+gapSuffix);
    if(isOpen&&!observed.has(current))notes.push(current+' 尚未结月，当前未返回观测'+gapSuffix);
    if(future.length)notes.push(future.join('、')+' 尚未到期'+gapSuffix);
  }
  if(isOpen&&observed.has(current))notes.push(current+' 尚未结月，该月数值以本次查询为准。');
  if(coverage.observed_sum){
    const zeroPeriods=periods.filter(period=>observed.get(period)===0);
    if(zeroPeriods.length)notes.push(zeroPeriods.join('、')+' 的记录值为 0 '+unit+'。');
  }
  const totals=years.length>1?years.map(year=>{
    const numbers=[...observed].filter(([period])=>period.startsWith(year+'-')).map(([,value])=>value);
    return year+' 年'+(numbers.length?'已观测 **'+numbers.reduce((sum,value)=>sum+value,0)+' '+unit+'**':'未返回观测');
  }).join('，')+'。':'已观测月份合计 **'+total+' '+unit+'**。';
  const text=['**'+years.join('、')+'年逐月'+label+'**',table,totals,...notes]
    .filter(Boolean).join('\n\n');
  return {text,resultRef:result.result_ref,observedTotal:total,unit};
}
