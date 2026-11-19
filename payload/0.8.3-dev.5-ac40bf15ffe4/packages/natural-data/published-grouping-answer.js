import {chartOnlyKind,explicitChartType,isExploratory} from './presentation-intent.js';

const day=/^\d{4}-(?:0[1-9]|1[0-2])-01$/;
const dateKey=/^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/;
const labelPattern=/^[\p{L}\p{N}（）()·、 \-—]{1,60}$/u;
const count=n=>Number.isSafeInteger(n)&&n>=0;
const supportedView=new Set(['table','bar','column','pie','donut']);
const rankingQuestion=/(?:哪(?:个|一[个类种组]|类|些|种|组|几[个类组])|什么)[^，。！？；\n,?!;]{0,36}?(最多|最少)|(最多|最少)(?:的)?(?:是)?(?:哪(?:个|一[个类种组]|类|些|种|组|几[个类组])|什么)/g;

function checkedPublishedCount(value,proof){
  const contract=value?.answer_contract,lineage=value?.result_lineage,values=value?.values;
  return value?.status==='succeeded'&&Boolean(value.result_ref)&&!isExploratory(value)&&!value.truncated
    &&value.validation?.state!=='conflict'&&value.receipt?.read_only===true&&lineage?.purpose==='answer'
    &&contract?.kind==='sql_aggregate'&&contract.result_ref===value.result_ref&&contract.complete===true
    &&proof?.schema_version===1&&Boolean(proof.request_ref)&&proof.request_ref===lineage.request_ref
    &&Boolean(proof.source_id)&&proof.source_id===lineage.source_id&&proof.source_id===value.receipt.source_id
    &&count(proof.source_revision)&&Boolean(proof.publication?.id)&&proof.publication.id===value.business_project?.id
    &&Number.isInteger(proof.publication.version)&&proof.publication.version>0&&proof.publication.version===value.business_project?.version
    &&day.test(proof.start_inclusive)&&dateKey.test(proof.end_exclusive)&&proof.start_inclusive<proof.end_exclusive
    &&labelPattern.test(proof.unit||'')&&values?.complete===true&&Array.isArray(values.columns)
    &&Array.isArray(values.rows)&&values.rows.length===value.row_count&&values.rows.length===values.row_count;
}

export function checkedPublishedDetail(value){
  return checkedPublishedCount(value,value?.validation?.published_detail);
}

// The engine binds classification, query scope and counts. Every positive
// count and the unknown group must also occur in the same returned rows.
export function checkedPublishedGrouping(value){
  const proof=value?.answer_contract?.published_grouping;
  if(!checkedPublishedCount(value,proof)||proof.result_ref!==value.result_ref||!count(proof.denominator)||!proof.denominator
    ||!count(proof.unclassified_count)||!labelPattern.test(proof.unclassified_label||'')
    ||!Array.isArray(proof.known_counts)||proof.known_counts.length<2||proof.known_counts.length>20)return null;
  const {columns,rows}=value.values;
  if(columns.length!==2||rows.length!==proof.case_count)return null;
  const labels=new Map();
  for(const entry of proof.known_counts){
    if(!labelPattern.test(entry?.label||'')||!count(entry.count)||labels.has(entry.label)||entry.label===proof.unclassified_label)return null;
    labels.set(entry.label,entry.count);
  }
  if(proof.unclassified_count)labels.set(proof.unclassified_label,proof.unclassified_count);
  if([...labels.values()].reduce((sum,n)=>sum+n,0)!==proof.denominator)return null;
  const categoryIndex=columns.indexOf(proof.category_column),countIndex=columns.indexOf(proof.count_column);
  if(categoryIndex<0||countIndex<0||categoryIndex===countIndex)return null;
  const seen=new Set();
  for(const row of rows){
    if(!Array.isArray(row)||row.length!==2)return null;
    const name=row[categoryIndex];
    if(seen.has(name)||!labels.has(name)||row[countIndex]!==labels.get(name))return null;
    seen.add(name);
  }
  if([...labels].some(([name,n])=>n>0&&!seen.has(name)))return null;
  return {proof,labels};
}

export function verifiedPublishedCountAnswer(question,evidence,{requestRef,presentation}={}){
  const results=evidence.filter(item=>item.result_ref&&item.status==='succeeded'&&!isExploratory(item));
  if(results.length!==1)return null;
  const value=results[0],verified=checkedPublishedGrouping(value);
  if(!verified)return presentation?null:detailAnswer(question,value,requestRef);
  const {proof,labels}=verified,desired=presentation?chartOnlyKind(question):explicitChartType(question),actual=value.visualization?.actual;
  if(presentation){
    if(presentation.result_ref!==value.result_ref||desired!==presentation.kind||actual!==desired||value.visualization?.fallback)return null;
  }else if(!requestRef||requestRef!==proof.request_ref||!supportedView.has(actual)
    ||desired&&desired!==actual||value.visualization?.fallback)return null;
  const boundary=groupedObservationBoundary(value,proof);
  const heading=boundary?'**分类统计**':'**'+monthRange(proof)+'分类统计**';
  const showShare=presentation||/占比|构成|份额|饼(?:状|形)?图|环形图/.test(question);
  const rows=['| 分类 | 数量（'+proof.unit+'） |'+(showShare?' 占比 |':''),'|---|---:|'+(showShare?'---:|':''),
    ...[...labels].map(([name,n])=>'| '+name+' | '+n+' |'+(showShare?' '+(100*n/proof.denominator).toFixed(2)+'% |':''))];
  const summary=(boundary?'本次查到的记录合计 **':'同一统计范围合计 **')+proof.denominator+' '+proof.unit+'**。';
  const unknown=proof.unclassified_count?'分类字段为空的记录单列为“'+proof.unclassified_label+'”，计入统计总量。':'';
  const ranking=groupedCountRanking(question,proof);
  return answerText(value,heading,boundary,summary,ranking,rows.join('\n'),unknown);
}

function groupedObservationBoundary(value,proof){
  const coverage=value.validation?.current_period_observation;
  if(coverage?.schema_version!==1||!['annual','year_to_date','fixed_range','current_month'].includes(coverage.kind)||coverage.state!=='coverage_unknown'
    ||['request_ref','result_ref','source_id','start_inclusive','end_exclusive'].some(key=>coverage[key]!==proof[key])
    ||coverage.publication?.id!==proof.publication.id||coverage.publication?.version!==proof.publication.version
    ||!day.test(coverage.calendar_month+'-01'))return '';
  const previous=coverage.prior_observed,month=coverage.calendar_month,absence=coverage.prior_month_absence;
  const unobserved=absence?.state==='unobserved'&&absence.period===month&&Boolean(absence.result_ref);
  const known=previous?.result_ref&&day.test(previous.from+'-01')&&day.test(previous.to+'-01')
    &&previous.from<=previous.to&&previous.to<=month&&!(unobserved&&previous.to===month)&&previous.unit===proof.unit
    &&previous.from+'-01'>=proof.start_inclusive&&previous.to+'-01'<proof.end_exclusive
    &&count(previous.observed_total)&&previous.observed_total===proof.denominator
    &&Array.isArray(previous.missing_closed_months)
    &&previous.missing_closed_months.every(item=>day.test(item+'-01')&&item<month
      &&item+'-01'>=proof.start_inclusive&&item+'-01'<proof.end_exclusive);
  const range='查询范围为 '+monthRange(proof).trim()+'。';
  const prior=known?'同口径月度结果的观测起止为 '+previous.from+' 至 '+previous.to+'。'
    +(previous.missing_closed_months.length?'查询范围内未返回观测的月份为 '+previous.missing_closed_months.join('、')+'。':''):'';
  if(coverage.kind==='fixed_range')return range+prior;
  if(coverage.kind==='current_month')return range+month+' 尚未结月，以上为该月已查到的记录。';
  if(coverage.kind==='annual'){
    const start=proof.start_inclusive.slice(0,7),end=proof.end_exclusive.slice(0,7);
    const [year,index]=month.split('-');
    if(month>=start&&month<end){
      const future=Array.from({length:12-Number(index)},(_,i)=>year+'-'+String(Number(index)+i+1).padStart(2,'0'))
        .filter(item=>item<end);
      return range+prior+month+' 尚未结月。'+(future.length?future.join('、')+' 尚未到期。':'')
        +'完整年度的观测范围尚待核实。';
    }
    return range+prior+'完整年度的观测范围尚待核实。';
  }
  const missing=unobserved
    ?'此前同口径单月查询在 '+(absence.start_inclusive&&absence.end_exclusive&&!day.test(absence.end_exclusive)
        ?absence.start_inclusive+' 至 '+absence.end_exclusive+'（不含）':month)+' 未返回观测，该月的完整累计仍待核实。'
    :month+' 尚未结月，完整累计仍待核实。';
  return range+prior+missing;
}

function groupedCountRanking(question,proof){
  const directions=new Set([...question.matchAll(rankingQuestion)].map(match=>match[1]||match[2]));
  if(!directions.size&&['最多','最少'].includes(proof.ranking_direction))directions.add(proof.ranking_direction);
  const counts=proof.known_counts.map(entry=>entry.count);
  return [...directions].map(direction=>{
    const value=(direction==='最多'?Math.max:Math.min)(...counts);
    const names=proof.known_counts.filter(entry=>entry.count===value).map(entry=>entry.label);
    const lead=names.length===1?'的数量'+direction+'，为 ':'的数量并列'+direction+'，均为 ';
    return (proof.unclassified_count?'已分类记录中，':'')+names.join('、')+lead+value+' '+proof.unit+'。';
  }).join('\n');
}

function detailAnswer(question,value,requestRef){
  const proof=value.validation?.published_detail,desired=explicitChartType(question);
  if(!checkedPublishedDetail(value)||requestRef!==proof.request_ref||value.visualization?.actual!=='table'
    ||desired&&desired!=='table'||value.visualization?.fallback||!labelPattern.test(proof.category_column||''))return null;
  const {columns,rows}=value.values;
  const indices=[proof.month_column,proof.category_column,proof.count_column].map(column=>columns.indexOf(column));
  if(!rows.length||columns.length!==3||indices.includes(-1)||new Set(indices).size!==3)return null;
  const [month,category,measure]=indices,seen=new Set();
  let total=0;
  for(const row of rows){
    const period=row[month],label=row[category],n=row[measure],key=JSON.stringify([period,label]);
    if(row.length!==3||!day.test(period+'-01')||period+'-01'<proof.start_inclusive||period+'-01'>=proof.end_exclusive
      ||label!==null&&typeof label!=='string'||!count(n)||n===0||seen.has(key))return null;
    seen.add(key);
    total+=n;
  }
  if(!count(total))return null;
  return answerText(value,'**'+monthRange(proof)+'按月、按'+proof.category_column+'统计**',
    '共返回 '+rows.length+' 组，合计 **'+total+' '+proof.unit+'**。下方数据表逐行列出了月份、'+proof.category_column+'和数量。');
}

function answerText(value,...parts){
  return parts.filter(Boolean).join('\n\n');
}

function monthRange(proof){
  if(!day.test(proof.end_exclusive))return proof.start_inclusive+' 至 '+proof.end_exclusive+'（不含）';
  const [year,month]=proof.end_exclusive.split('-').map(Number);
  const end=(month===1?year-1:year)+'-'+String(month===1?12:month-1).padStart(2,'0');
  return proof.start_inclusive.slice(0,7)+' 至 '+end+' ';
}
