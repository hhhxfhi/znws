import {activeEvidence} from './result-lineage.js';
import {isExploratory} from './presentation-intent.js';

const businessLabel=/^[\p{Script=Han}\p{N} A-Za-z（）()·、，,。%％—–/-]{1,100}$/u;
const technical=/\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b/;
const hasChinese=/\p{Script=Han}/u;
const plainLabel=value=>typeof value==='string'&&businessLabel.test(value)&&!technical.test(value);
const missingWord=/未(?:能|完成|核实|查询|取得|获取|返回)|无法|不可计算|待(?:确认|核实)|缺少|尚未|暂无(?:可核实|统计|数据)/;

function formatTable(headers,rows){
  return ['| '+headers.join(' | ')+' |','| '+headers.map(()=>'---').join(' | ')+' |',
    ...rows.map(row=>'| '+row.join(' | ')+' |')].join('\n');
}

function literalFactRows(result){
  const contract=result.metric_contract,facts=result.facts;
  if(!contract?.id||!plainLabel(contract.name)||!hasChinese.test(contract.name)
    ||!plainLabel(contract.unit)||!Array.isArray(facts)||facts.length!==result.row_count
    ||!facts.length||facts.length>80)return null;
  const dimensions=Object.keys(facts[0]?.dimensions||{});
  const labels=dimensions.map(key=>key==='period'?'期间':key==='city'?'地市':key==='grade'?'等级'
    :key==='profession'?'专业':plainLabel(key)&&hasChinese.test(key)?key:'分类');
  if(new Set(labels).size!==labels.length||facts.some(fact=>fact.unit!==contract.unit
    ||!Number.isFinite(fact.value)||typeof fact.id!=='string'||!fact.id.startsWith(result.result_ref+':')
    ||Object.keys(fact.dimensions||{}).length!==dimensions.length
    ||dimensions.some(key=>!plainLabel(fact.dimensions?.[key]))))return null;
  const rows=facts.map(fact=>[...dimensions.map(key=>fact.dimensions[key]),String(fact.value)]);
  if(new Set(rows.map(row=>JSON.stringify(row.slice(0,-1)))).size!==rows.length)return null;
  return {title:contract.name,text:formatTable([...labels,'数值（'+contract.unit+'）'],rows)};
}

// A suffix like "_元" can identify a display alias but is not independent
// evidence of the storage unit. Only a reported column unit is displayed.
function businessColumn(column){
  return typeof column==='string'?column.replace(/[_＿](?:亿元|万元|元|家|件|条|项|笔|个|次|台|吨|人)$/, ''):column;
}

function literalSqlRows(result){
  const values=result.values,contract=result.answer_contract;
  const aggregate=contract?.kind==='sql_aggregate'&&contract.complete===true&&contract.result_ref===result.result_ref;
  const detail=!contract&&!result.metric_contract&&result.result_lineage?.purpose==='answer'
    &&Boolean(result.receipt?.query_hash)&&['data.sql_query','data.query'].includes(result.execution?.operation);
  if((!aggregate&&!detail)||values?.complete!==true||!Array.isArray(values.columns)||!values.columns.length
    ||values.columns.length>8||!Array.isArray(values.rows)||values.rows.length!==result.row_count
    ||values.rows.length!==values.row_count||!values.rows.length||values.rows.length>80)return null;
  const columns=values.columns;
  const proof=result.validation?.annual_forecast_history;
  const reference=proof?.reference_only===true&&proof.state==='checked'&&proof.result_ref===result.result_ref
    &&proof.request_ref===result.result_lineage?.request_ref&&proof.source_id===result.receipt?.source_id
    &&plainLabel(proof.measure_label)&&columns.includes(proof.value_column)?proof:null;
  const labels=columns.map(column=>column===reference?.value_column?reference.measure_label:businessColumn(column));
  if(new Set(labels).size!==labels.length||labels.some(label=>!plainLabel(label)||!hasChinese.test(label)))return null;
  const titles=labels.map((label,index)=>{
    const unit=result.analysis_options?.column_units?.[columns[index]];
    if(!plainLabel(unit)||/\(.*\)|（.*）|%|％/.test(label))return label;
    return label+'（'+unit+'）';
  });
  let hasValues=false;
  const rows=[];
  for(const row of values.rows){
    if(!Array.isArray(row)||row.length!==columns.length)return null;
    const formatted=[];
    for(const [index,cell] of row.entries()){
      if(cell===null){formatted.push('');continue;}
      if(Number.isFinite(cell)){
        hasValues=true;
        const unit=result.analysis_options?.column_units?.[columns[index]];
        formatted.push(String(cell)+(unit==='%'&&!/[％%]/.test(titles[index])?'%':''));
      }else if(plainLabel(cell)){
        if(detail)hasValues=true;
        formatted.push(cell);
      }else return null;
    }
    rows.push(formatted);
  }
  if(!hasValues)return null;
  const title=reference?reference.measure_label+'历史记录':String(result.title||'').replace(/（合成测试）$/,'');
  return {title:plainLabel(title)&&hasChinese.test(title)?title:'查询结果',text:formatTable(titles,rows),
    ...(reference?{reference:true}:{})};
}

function canonical(value){
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])]));
  return value;
}

export function metricDeliveryIdentity(result){
  const metric=result.metric_contract, scope=metric?.scope, proof=metric?.confirmation;
  const receipt=result.receipt, project=result.business_project, answer=result.answer_contract;
  const revision=result.source_provenance?.connection_revision;
  if(result.execution?.operation!=='data.metric_query'||result.status!=='succeeded'||result.truncated
    ||isExploratory(result)||receipt?.read_only!==true||result.validation?.state==='conflict'
    ||result.validation?.period_grain?.state==='auxiliary'||answer?.complete!==true
    ||answer.result_ref!==result.result_ref||answer.metric_id!==metric?.id||answer.unit!==metric?.unit
    ||result.analysis_options?.mode!=='metric_facts'||result.analysis_options.complete!==true
    ||!metric?.id||!metric.version||!metric.unit||!proof?.definition_fingerprint||!proof.state
    ||proof.basis!=='published_configuration'||proof.metric_id!==metric.id||proof.definition_version!==metric.version
    ||!receipt.source_id||receipt.source_id!==proof.source_id||!project?.id||!Number.isSafeInteger(project.version)
    ||project.version!==proof.project_version||!Number.isSafeInteger(revision)||!result.execution_scope
    ||!Array.isArray(scope?.periods)||!Array.isArray(scope.dimensions)||!Array.isArray(scope.filters)
    ||!Array.isArray(result.facts)||!result.facts.length||result.facts.length!==result.row_count
    ||result.facts.some(fact=>fact?.unit!==metric.unit||typeof fact.id!=='string'||!fact.id.startsWith(result.result_ref+':')))
    return null;
  return {
    source:receipt.source_id,project,revision,executionScope:result.execution_scope,metric:metric.id,
    version:metric.version,definition:proof.definition_fingerprint,state:proof.state,
    businessConfirmation:metric.business_confirmation,unit:metric.unit,scope,defaultFilters:metric.default_filters,
    limitations:answer.limitations,lineage:result.result_lineage?.population_fingerprint
  };
}

function metricDeliveryKey(result){
  const identity=metricDeliveryIdentity(result);
  if(!identity)return null;
  const facts=result.facts.map(({id,...fact})=>JSON.stringify(canonical(fact))).sort();
  return JSON.stringify(canonical({...identity,facts}));
}

// Same-request callers can present repeated complete results once. Keep the
// original receipt with the most analysis evidence under its own reference.
export function uniqueMetricDeliveries(results,evidence=[]){
  const selected=new Map(),unmatched=[];
  const claims=new Map(evidence.map(result=>[result.result_ref,result.claims?.length||0]));
  const rank=result=>Math.max(claims.get(result.result_ref)||0,result.claims?.length||0);
  for(const result of results){
    const key=metricDeliveryKey(result);
    if(!key){unmatched.push(result);continue;}
    const prior=selected.get(key);
    if(!prior||rank(result)>rank(prior))selected.set(key,result);
  }
  const retained=new Set([...unmatched,...selected.values()]);
  return results.filter(result=>retained.has(result));
}

const profileRoles=['group','average','zero','missing'];
const optionalProfileRoles=['total','positive'];

function groupedProfile(result){
  const contract=result.answer_contract, proof=contract?.grouped_measure_profile;
  const lineage=result.result_lineage, values=result.values;
  const project=result.business_project, receipt=result.receipt;
  if(result.status!=='succeeded'||!result.result_ref||!result.view_url||result.truncated||isExploratory(result)
    ||receipt?.read_only!==true||!receipt.source_id||!receipt.query_hash||!result.execution_scope
    ||lineage?.purpose!=='answer'||!lineage.request_ref||!lineage.session_id||!lineage.config_fingerprint
    ||lineage.source_id!==receipt.source_id||!project?.id||!Number.isSafeInteger(project.version)
    ||!['data.sql_query','data.query'].includes(result.execution?.operation)
    ||result.validation?.state==='conflict'||result.validation?.period_grain?.state==='auxiliary'
    ||contract?.kind!=='sql_aggregate'||contract.complete!==true||contract.result_ref!==result.result_ref
    ||proof?.schema_version!==1||!proof.signature||!proof.roles||values?.complete!==true
    ||!Array.isArray(values.rows)||!Array.isArray(values.columns)||values.rows.length!==result.row_count
    ||values.rows.length!==values.row_count||values.rows.length<1||values.rows.length>80)return null;
  const roles=Object.keys(proof.roles).sort(), indices=roles.map(role=>proof.roles[role]);
  if(!profileRoles.every(role=>roles.includes(role))||roles.some(role=>![...profileRoles,...optionalProfileRoles].includes(role))
    ||indices.some(index=>!Number.isSafeInteger(index)||index<0||index>=values.columns.length)
    ||roles.length!==values.columns.length||new Set(indices).size!==indices.length)return null;
  const counts=n=>Number.isSafeInteger(n)&&n>=0;
  const groups=new Set();
  const {group,average,zero,missing,total}=proof.roles;
  for(const row of values.rows){
    if(!Array.isArray(row)||row.length!==indices.length||!plainLabel(row[group])||groups.has(row[group])
      ||!Number.isFinite(row[average])&&row[average]!==null
      ||roles.some(role=>role!=='group'&&role!=='average'&&!counts(row[proof.roles[role]])))return null;
    if(total!==undefined&&(row[zero]+row[missing]>row[total]||(row[average]===null)!==(row[missing]===row[total])))return null;
    groups.add(row[group]);
  }
  const units=roles.map(role=>result.analysis_options?.column_units?.[values.columns[proof.roles[role]]]??null);
  const rows=values.rows.map(row=>JSON.stringify(indices.map(index=>row[index]))).sort();
  return {proof,values,key:JSON.stringify([receipt.source_id,result.execution_scope,project.id,project.version,lineage.config_fingerprint,
    lineage.session_id,lineage.request_ref,proof.signature,roles,units,rows])};
}

export function uniqueSqlProfileDeliveries(results,evidence=[]){
  const hasClaims=result=>Boolean(result.claims?.length||result.verified_claims?.length);
  const claims=new Set(evidence.filter(hasClaims).map(result=>result.result_ref));
  const selected=new Map(), kept=new Set();
  for(const result of results){
    const profile=groupedProfile(result);
    if(!profile||hasClaims(result)||claims.has(result.result_ref)){kept.add(result);continue;}
    const prior=selected.get(profile.key);
    if(!prior||!literalSqlRows(prior)&&literalSqlRows(result))selected.set(profile.key,result);
  }
  for(const result of selected.values())kept.add(result);
  return results.filter(result=>kept.has(result));
}

export function verifiedGroupedMeasureAnswer(question,formal,request,session){
  if(!request||!session||!/(?:平均|均值)/.test(question)||!/缺测|缺失|未记录|空值/.test(question))return null;
  // Restrict early delivery to a single grouping and the three requested
  // observations. Other analyses or a requested visualization stay on the
  // regular completion path.
  const text=question.normalize('NFKC').trim().replace(/[。！!？?]+$/,'');
  if(/[。！!？?；;\n]|排行|排名|最高|最低|阈值|比较|对比|环比|同比|占比|趋势|预测|另(?:查|外|算)|明细|图|导出|下载|筛选|限定|超过|小于|大于|在[^，、]{0,12}范围/.test(text))return null;
  const parts=text.split(/\s*(?:、|,|以及)\s*/);
  if(parts.length!==3)return null;
  const results=activeEvidence(formal).filter(result=>result?.status==='succeeded'&&result.result_ref&&!isExploratory(result)
    &&result.result_lineage?.request_ref===request&&result.result_lineage.session_id===session);
  const [result,...others]=uniqueSqlProfileDeliveries(results,[]);
  if(!result||others.length||result.claims?.length)return null;
  const profile=groupedProfile(result);
  if(!profile)return null;
  const {values,proof}=profile, cols=values.columns.map(businessColumn);
  const [group,average,zero,missing]=profileRoles.map(role=>cols[proof.roles[role]]);
  if(![group,average,zero,missing].every(label=>plainLabel(label)&&hasChinese.test(label)))return null;
  const measure=average.replace(/平均(?:值|金额|数值|数额)?|均值/g,'').trim();
  if(!/^\p{Script=Han}{2,24}$/u.test(measure)||![zero,missing].every(label=>label.includes(measure))
    ||!/平均|均值/.test(average)||!/(?:零|为0|等于0)/.test(zero)||!/缺测|缺失|未记录|空值/.test(missing)
    ||!parts.every(part=>part.includes(measure)))return null;
  if(!parts.some(part=>/(?:平均|均值)/.test(part)&&(part.includes('各'+group)||part.includes('按'+group)||part.includes('每个'+group)))
    ||!parts.some(part=>/(?:零|为0|等于0)/.test(part))||!parts.some(part=>/缺测|缺失|未记录|空值/.test(part)))return null;
  const indices=profileRoles.map(role=>proof.roles[role]);
  const selected={...result,values:{...values,columns:indices.map(index=>values.columns[index]),
    rows:values.rows.map(row=>indices.map(index=>row[index]))}};
  const table=literalSqlRows(selected);
  if(!table)return null;
  const blank=values.rows.some(row=>row[proof.roles.average]===null);
  return '**'+table.title+'**\n\n'+table.text+
    (blank?'\n\n平均值按已记录的数值计算。平均值为空的分组没有可计算的数值。':'');
}

function comparisonMonths(question){
  const segments=question.split(/[。？?；;\n]/);
  const pairs=segments.flatMap(segment=>[...segment.matchAll(/((?:19|20)\d{2})年\s*(\d{1,2})月[^，,]{0,38}?比(?:较)?(?:(\d{4})年\s*)?(\d{1,2})月/g)]);
  const share=/各(?:自)?占(?:总量|总数|比)|分别占(?:总量|总数|比)|(?:各类|每类|分别).{0,8}占比/;
  const shares=segments.flatMap(segment=>{
    const match=share.exec(segment);
    if(!match)return [];
    const clause=segment.slice(0,match.index).split(/[，,]/).at(-1);
    return [...clause.matchAll(/(?:(\d{4})年\s*)?(\d{1,2})月/g)];
  });
  const shareMentions=segments.flatMap(segment=>[...segment.matchAll(/占比|占总量|占总数|份额/g)]);
  if(pairs.length!==1||shares.length!==1||shareMentions.length!==1)return null;
  const [,year,month,previousYear,previousMonth]=pairs[0], [,shareYear,shareMonth]=shares[0];
  const period=(y,m)=>Number(m)>=1&&Number(m)<=12?y+'-'+m.padStart(2,'0'):null;
  const current=period(year,month), baseline=period(previousYear||year,previousMonth);
  return current&&baseline&&current!==baseline&&period(shareYear||year,shareMonth)===current?{current,baseline}:null;
}

const sameEvidence=(left,right)=>JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
const sameIds=(actual,expected)=>Array.isArray(actual)&&sameEvidence([...actual].sort(),[...expected].sort());
const countValue=value=>Number.isSafeInteger(value)&&value>=0;
const closePercent=(value,expected)=>expected===null?value===null:Number.isFinite(value)&&Math.abs(value-expected)<1e-7;
const signedCount=value=>(value>0?'+':'')+value;
const shareClaim=claim=>(claim.operation===undefined||claim.operation==='share')&&Object.hasOwn(claim,'share_percent');

export function groupedComparisonSources(results,question){
  const periods=comparisonMonths(question);
  if(!periods||results.length!==2)return null;
  const grouped=results.find(result=>result.metric_contract?.scope?.dimensions?.length===1);
  const total=results.find(result=>result.metric_contract?.scope?.dimensions?.length===0);
  if(!grouped||!total||grouped.result_ref===total.result_ref)return null;
  const primary=metricDeliveryIdentity(grouped),auxiliary=metricDeliveryIdentity(total);
  if(!primary||!auxiliary||grouped.report||total.report)return null;
  const {dimensions:[dim],...scope}=primary.scope;
  const interval=[periods.baseline,periods.current];
  if(!sameEvidence({...primary,scope:{...scope,dimensions:[]}},auxiliary)
    ||!sameIds(scope.periods,interval)||![grouped,total].every(result=>result.metric_contract.additive===true
      &&result.metric_contract.ratio===false&&sameIds(result.metric_contract.periods,interval))
    ||grouped.metric_contract.overlapping_dimensions?.includes(dim)||total.facts.length!==2
    ||grouped.facts.length<4||grouped.facts.length>40
    ||new Set(grouped.facts.map(fact=>fact.id)).size!==grouped.facts.length
    ||new Set(total.facts.map(fact=>fact.id)).size!==total.facts.length)return null;
  const labels=period=>grouped.facts.filter(fact=>fact.dimensions?.period===period)
    .map(fact=>String(fact.dimensions[dim]).normalize('NFKC').trim().toLowerCase());
  if(!grouped.facts.every(fact=>interval.includes(fact.dimensions?.period)&&plainLabel(fact.dimensions[dim])
      &&fact.dimensions[dim].trim()&&!/^(?:合计|总计|全部|未分类|未知|未注明)$/.test(fact.dimensions[dim].trim())
      &&Object.keys(fact.dimensions).length===2&&countValue(fact.value)))return null;
  const before=labels(periods.baseline),after=labels(periods.current);
  if(before.length<2||new Set(before).size!==before.length||!sameIds(before,after))return null;
  for(const period of interval){
    const entries=total.facts.filter(fact=>fact.dimensions?.period===period);
    const sum=grouped.facts.filter(fact=>fact.dimensions.period===period).reduce((n,fact)=>n+fact.value,0);
    if(entries.length!==1||Object.keys(entries[0].dimensions).length!==1||!countValue(sum)||entries[0].value!==sum)return null;
  }
  return {grouped,total};
}

function comparisonMatches(claim,before,after,unit){
  const delta=after-before;
  return claim.baseline?.value===before&&claim.current?.value===after&&claim.baseline.unit===unit
    &&claim.current.unit===unit&&claim.difference===delta&&claim.difference_unit===unit
    &&closePercent(claim.change_percent,before?delta*100/before:null);
}

export function groupedComparisonSection(result,question){
  const periods=comparisonMonths(question),metric=result.metric_contract;
  const dim=metric?.scope?.dimensions?.[0],unit=metric?.unit;
  if(!periods||!metricDeliveryKey(result)||metric.additive!==true||metric.ratio!==false
    ||!plainLabel(metric.name)||!hasChinese.test(metric.name)||!plainLabel(unit)||/[%％]/.test(unit)
    ||metric.scope.dimensions.length!==1||metric.overlapping_dimensions?.includes(dim)
    ||!sameIds(metric.periods,[periods.baseline,periods.current])||result.row_count<4||result.row_count>40)return null;
  const {baseline,current}=periods;
  const groups=new Map();
  for(const fact of result.facts){
    const dims=fact.dimensions||{},label=dims[dim],period=dims.period;
    if(!plainLabel(label)||!label.trim()||!hasChinese.test(label)||/^(?:合计|总计|全部|未分类|未知|未注明)$/.test(label.trim())
      ||Object.keys(dims).length!==2||![baseline,current].includes(period)||!countValue(fact.value))return null;
    const key=label.normalize('NFKC').trim().toLowerCase(),group=groups.get(key)||{label};
    if(group.label!==label||group[period])return null;
    group[period]=fact;
    groups.set(key,group);
  }
  const rows=[...groups.values()];
  if(rows.length<2||rows.some(group=>!group[baseline]||!group[current]))return null;
  const before=rows.reduce((sum,row)=>sum+row[baseline].value,0),after=rows.reduce((sum,row)=>sum+row[current].value,0);
  if(!countValue(before)||!countValue(after))return null;
  const claims=result.claims||[];
  // This section consumes one complete comparison and one set of shares.
  // Any other verified analytical operation still needs its own presentation.
  if(claims.some(claim=>!claim||claim.metric_id!==metric.id||!sameEvidence(claim.scope,metric.scope)
    ||!['period_compare','aggregate_compare'].includes(claim.operation)&&!shareClaim(claim)))return null;
  const comparisons=claims.filter(claim=>claim.operation==='period_compare');
  const beforeIds=rows.map(row=>row[baseline].id),afterIds=rows.map(row=>row[current].id);
  if(new Set([...beforeIds,...afterIds]).size!==result.row_count)return null;
  const totals=comparisons.filter(claim=>sameIds(claim.baseline?.derived_from,beforeIds)&&sameIds(claim.current?.derived_from,afterIds));
  if(!totals.length||!totals.every(claim=>comparisonMatches(claim,before,after,unit)
    &&claim.baseline.dimensions?.period===baseline&&claim.current.dimensions?.period===current
    &&sameIds(claim.fact_refs,[...beforeIds,...afterIds])))return null;
  const table=[];
  const usedClaims=new Set(totals);
  for(const row of rows){
    const previous=row[baseline],latest=row[current],ids=[previous.id,latest.id];
    const compared=comparisons.filter(claim=>sameIds(claim.fact_refs,ids));
    if(!compared.length||!compared.every(claim=>comparisonMatches(claim,previous.value,latest.value,unit)
      &&claim.baseline.id===previous.id&&claim.current.id===latest.id
      &&sameEvidence(claim.baseline.dimensions,previous.dimensions)&&sameEvidence(claim.current.dimensions,latest.dimensions)))return null;
    const duplicates=claims.filter(claim=>claim.operation==='aggregate_compare'&&sameIds(claim.fact_refs,ids));
    if(duplicates.some(claim=>!comparisonMatches(claim,previous.value,latest.value,unit)
      ||claim.change_threshold!==undefined||!sameIds(claim.baseline?.derived_from,[previous.id])
      ||!sameIds(claim.current?.derived_from,[latest.id])))return null;
    const shares=claims.filter(claim=>claim.fact?.id===latest.id&&shareClaim(claim));
    const percentage=after?100*latest.value/after:null;
    if(!shares.length||!shares.every(claim=>sameIds(claim.fact_refs,[latest.id])&&claim.fact.value===latest.value
      &&claim.fact.unit===unit&&sameEvidence(claim.fact.dimensions,latest.dimensions)&&claim.denominator===after
      &&closePercent(claim.share_percent,percentage)))return null;
    for(const claim of [...compared,...duplicates,...shares])usedClaims.add(claim);
    const delta=compared[0].difference;
    table.push({label:row.label,delta,values:[row.label,String(previous.value),String(latest.value),signedCount(delta),
      percentage===null?'无法计算（分母为零）':shares[0].share_percent.toFixed(2)+'%']});
  }
  if(claims.some(claim=>!usedClaims.has(claim)
    &&!(shareClaim(claim)&&beforeIds.includes(claim.fact?.id))))return null;
  const {difference:delta,change_percent:change}=totals[0];
  const summary=change===null?'基期合计为零，无法计算整体变化率。':change===0?'整体持平。'
    :'整体'+(delta>0?'增幅':'降幅')+' '+Math.abs(change).toFixed(2)+'%。';
  const notes=[summary];
  const mainContribution=/主要来自|主要贡献|变化来源/.test(question);
  if(delta!==0&&mainContribution){
    const direction=Math.sign(delta);
    const highest=Math.max(...table.map(row=>row.delta*direction));
    const leaders=table.filter(row=>row.delta*direction===highest).map(row=>row.label);
    notes.push('总量'+(delta>0?'增加':'减少')+'主要来自'+leaders.join('、')+(leaders.length>1?'（并列）':'')+'。');
  }else if(delta===0&&mainContribution&&table.some(row=>row.delta!==0)){
    notes.push('各类增减相互抵消，总量没有净变化。');
  }
  const headers=['分类',baseline+'（'+unit+'）',current+'（'+unit+'）','增减（'+unit+'）',current+'占比'];
  const values=[...table.map(row=>row.values),['合计',String(before),String(after),signedCount(delta),after?'100.00%':'无法计算（分母为零）']];
  return {result,table:{title:metric.name,text:formatTable(headers,values)},claims:notes};
}

// The original receipt proves which request and result the rows came from.
// Formatting a verified value never supplies another filter or infers totals.
export function answerFromReceipts(formal,other,requestRef,sessionId,question=''){
  const complete=activeEvidence(formal).filter(result=>result.status==='succeeded'&&result.result_ref
    &&!isExploratory(result)&&!result.truncated&&result.receipt?.read_only===true
    &&result.validation?.state!=='conflict'&&result.validation?.period_grain?.state!=='auxiliary'
    &&result.view_url&&(!result.result_lineage||(
      result.result_lineage.request_ref===requestRef&&result.result_lineage.session_id===sessionId
      &&result.result_lineage.purpose==='answer'&&result.result_lineage.source_id===result.receipt.source_id)));
  const originals=uniqueSqlProfileDeliveries(uniqueMetricDeliveries(complete,other),other);
  if(!originals.length||originals.length>3)return null;
  const bound=activeEvidence([...originals,...other.filter(value=>value.claims?.length
    &&originals.some(result=>result.result_ref===value.result_ref
      &&(!value.execution_scope||!result.execution_scope||value.execution_scope===result.execution_scope))).map(value=>({
      result_ref:value.result_ref,claims:value.claims
    }))]);
  const paired=groupedComparisonSources(bound,question);
  const comparisonSource=bound.length===1?bound[0]:paired&&!paired.total.claims?.length&&!paired.total.verified_claims?.length
    ?paired.grouped:null;
  if(comparisonSource){
    const compared=groupedComparisonSection(comparisonSource,question);
    if(compared)return [compared];
  }
  const sections=bound.map(result=>{
    const table=result.facts?.length?literalFactRows(result):literalSqlRows(result);
    if(!table)return null;
    const claims=[...new Set((result.claims||[]).map(claim=>{
      const text=claim.public_text||claim.verified_text;
      return typeof text==='string'&&claim.fact?.id&&Object.hasOwn(claim,'share_percent')
        ?text.replace('。占比','，占比'):text;
    }).filter(text=>
      typeof text==='string'&&text.length<=160&&hasChinese.test(text)&&!technical.test(text)
      &&!/\b(?:trend|snapshot|pct|derive|base|current)\b|样本合成|合成(?:数据|测试)|来源于模拟/.test(text)))];
    if(table.reference)claims.unshift('这些历史记录无法核实实际投入金额。');
    return {result,table,claims};
  }).filter(Boolean);
  return sections.length?sections:null;
}

function observationSeries(result){
  const {columns,rows}=result.values;
  const axis=columns.findIndex(column=>/^(?:年|年份|年度|月份|年月|统计年|统计期|期间|period)$/i.test(column));
  if(axis<0)return null;
  const periods=rows.map(row=>String(row[axis]));
  const grain=periods.every(period=>/^\d{4}-\d{2}$/.test(period)&&+period.slice(-2)>=1&&+period.slice(-2)<=12)?'month'
    :periods.every(period=>/^\d{4}$/.test(period))?'year':null;
  if(!grain||new Set(periods).size!==periods.length)return null;
  const measures=columns.flatMap((name,index)=>{
    const unit=result.analysis_options?.column_units?.[name];
    return index!==axis&&/^(?:家|户|人|个|件|笔|次|万元|亿元|元)$/.test(unit)
      &&rows.every(row=>row[index]===null||Number.isFinite(row[index]))?[{index,unit}]:[];
  });
  const monetary=measures.filter(({unit})=>/元$/.test(unit));
  const [measure,...multiple]=monetary.length?monetary:measures;
  if(!measure||multiple.length)return null;
  const observations=rows.flatMap((row,index)=>Number.isFinite(row[measure.index])?[[periods[index],row[measure.index]]]:[]);
  if(observations.length<2)return null;
  return {grain,observations:observations.sort(([a],[b])=>a.localeCompare(b)),unit:measure.unit,result};
}

function availableForecastHistory(results,question){
  if(!/(?:预测|预估|未来.{0,12}(?:年|月).{0,80}(?:趋势|增加|减少|增长|下降|增减))/.test(question))return null;
  const series=results.map(observationSeries).filter(Boolean);
  if(!series.length)return null;
  const [latest]=series.slice().reverse().sort((a,b)=>b.observations.length-a.observations.length);
  const {grain,observations,unit,result}=latest;
  const scoped=result.result_lineage;
  const known=new Map(observations);
  if(series.some(item=>item.grain!==grain||item.unit!==unit
    ||item.result.result_lineage.source_id!==scoped.source_id
    ||item.observations.some(([period,value])=>known.has(period)&&known.get(period)!==value)))return null;
  const format=value=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:8}).format(value)+' '+unit;
  if(grain==='month'){
    if(observations.length>=12)return null;
    const [start,first]=observations[0], [end,last]=observations.at(-1);
    return {kind:'month',text:'已查到 '+start+' 至 '+end+' 的 '+observations.length+' 个月度观测，期初为 **'+format(first)
      +'**，期末为 **'+format(last)+'**。\n\n现有月度历史不足以生成可靠预测，未来增减方向暂不能判断。'};
  }
  const window=result.validation?.annual_fiscal_window;
  if(window?.state!=='checked'||window.basis!=='client_calendar'||!/^\d{4}$/.test(window.current_open_year)
    ||window.target_period!==String(Number(window.current_open_year)+1))return null;
  const closed=observations.filter(([year])=>year<window.current_open_year);
  if(closed.length<2||closed.length>=5)return null;
  const changes=closed.slice(1).map(([,value],index)=>Math.sign(value-closed[index][1]));
  const trough=changes.indexOf(1);
  const trend=changes.every(delta=>delta<=0)?'总体下降'
    :changes.every(delta=>delta>=0)?'总体上升'
    :trough>0&&changes.slice(0,trough).every(delta=>delta<=0)&&changes.slice(trough).every(delta=>delta>=0)?'先降后升':'有升有降';
  const open=observations.find(([year])=>year===window.current_open_year);
  return {kind:'year',text:'已查到的已结年度金额'+(changes.every(delta=>delta===0)?'持平':trend)+'。'
    +closed.map(([year,value])=>year+' 年 '+format(value)).join('，')+'。'
    +(open?'\n\n'+open[0]+' 年尚未结年，当前查询金额为 **'+format(open[1])+'**。':'')
    +'\n\n现有完整年度数据不足以生成 '+window.target_period+' 年预测。预测须使用连续、可比的完整年度金额。'};
}

function availableGroupedLeader(results,question){
  if(!/(?:哪个|哪一类|哪类|哪种|哪一项|哪些).{0,45}(?:最多|最高)/.test(question)
    ||/(?:前\s*[2-9]|top\s*[2-9]|分别|每年|每月|同比|环比|占比|最低|最少)/i.test(question))return null;
  const matching=results.flatMap(result=>{
    const {columns,rows}=result.values;
    if(result.answer_contract?.kind!=='sql_aggregate'||result.answer_contract?.complete!==true
      ||result.answer_contract.result_ref!==result.result_ref||columns.length!==2||rows.length<2
      ||!rows.every(row=>typeof row[0]==='string'&&row[0]&&!/^\d{4}(?:[-/]\d{1,2})?$/.test(row[0])
        &&Number.isSafeInteger(row[1])&&row[1]>=0)
      ||new Set(rows.map(row=>row[0])).size!==rows.length)return [];
    const unit=result.analysis_options?.column_units?.[columns[1]];
    if(!/^(?:条|件|人|家|个|次|项|笔|宗)$/.test(unit)||!plainLabel(result.title))return [];
    return [{rows,unit,title:result.title,scope:result.result_lineage.source_id}];
  });
  const [first]=matching;
  if(!first||matching.some(item=>JSON.stringify(canonical(item))!==JSON.stringify(canonical(first))))return null;
  const count=Math.max(...first.rows.map(row=>row[1]));
  const names=first.rows.filter(row=>row[1]===count).map(row=>row[0]);
  if(!names.every(plainLabel))return null;
  return {kind:'rank',text:'在「'+first.title+'」的查询结果中，**'+names.join('、')+(names.length>1?'并列':'')+'最多，为 '
    +count+' '+first.unit+'**。'};
}

// Preserve narrowly verified observations when a broader question is still
// unresolved. Every value comes from a complete, same-request SQL result.
export function verifiedObservationDelivery(formal,requestRef,sessionId,question){
  const results=activeEvidence(formal).filter(result=>{
    const lineage=result.result_lineage,values=result.values;
    return result.status==='succeeded'&&result.result_ref&&result.view_url&&!result.truncated&&!isExploratory(result)
      &&result.receipt?.read_only===true&&result.receipt.query_hash&&result.validation?.state!=='conflict'
      &&result.validation?.period_grain?.state!=='auxiliary'&&['data.sql_query','data.query'].includes(result.execution?.operation)
      &&lineage?.request_ref===requestRef&&lineage.session_id===sessionId&&lineage.purpose==='answer'
      &&lineage.source_id===result.receipt.source_id&&values?.complete===true&&Array.isArray(values.columns)
      &&Array.isArray(values.rows)&&values.rows.length===values.row_count&&values.row_count===result.row_count
      &&values.rows.length<=80&&values.rows.every(row=>Array.isArray(row)&&row.length===values.columns.length);
  });
  return availableGroupedLeader(results,question)||availableForecastHistory(results,question);
}

export function formatReceiptDelivery(sections,missing){
  const blocks=[];
  for(const {table,claims} of sections)blocks.push('**'+table.title+'**',table.text,...claims.slice(0,10));
  if(missing)blocks.push(missing);
  return blocks.join('\n\n');
}

export function withDefinitionNotice(text,section){
  // Audit state does not require registration or confirmation of an ad hoc answer.
  return text;
}

export function candidateMissingDelivery(candidate){
  return !candidate.trim()||missingWord.test(candidate)&&!/[+\-]?\d+(?:\.\d+)?\s*(?:件|条|张|家|项|笔|人|户|万?元|%|％)/.test(candidate)
    &&!candidate.split('\n').some(line=>line.trim().startsWith('|')&&/\d/.test(line));
}

const WORK_MEASURE=/(?:验收|核验|交付)(?:实际)?完成(?:工作)?(?:量|率|度|百分比)|(?:实际|计划)(?:完成|验收|交付)(?:的)?(?:工作)?量|工作量完成(?:率|度|百分比)/;
const FUNDS_MEASURE=/(?:资金|预算|经费)(?:的)?(?:执行|使用|支出|消耗)(?:率|度|比例|百分比)/;
const STAGE_FRACTION=/(?:已(?:验收|核验|完成|交付))[^。！？\n]{0,20}(?:数|数量)[^。！？\n]{0,22}(?:总数|总数量|总(?:项目|工单|订单|任务|事项)数|全部(?:项目|工单|订单|任务|事项))/;
const SUBSTITUTE_WORK_QUESTION='WORK_MEASURE_SUBSTITUTION: 请按原问核对同一对象的实际工作量和计划工作量。'
  +'资金使用率和已验收件数占比是其他测量。先用已有授权目录和可用事实作答，查不到具体工作量时说明本次查询的限制。';
const WORK_TERMS={
  验收:'实际验收工作量',
  核验:'实际核验工作量',
  交付:'实际交付工作量'
};

export function workMeasureQuestionRejection(question,questions){
  if(!WORK_MEASURE.test(question)||FUNDS_MEASURE.test(question)||!questions.length)return null;
  const substitute=option=>[option?.label,option?.description].some(text=>FUNDS_MEASURE.test(text)||STAGE_FRACTION.test(text));
  return questions.every(item=>Array.isArray(item?.options)&&item.options.length>=2&&item.options.every(substitute)
    &&item.options.some(option=>STAGE_FRACTION.test(option?.label)||STAGE_FRACTION.test(option?.description)))
    ?SUBSTITUTE_WORK_QUESTION:null;
}

export function workInventoryTool(catalog,user,events,receipts,sessionId){
  const source=catalog?.sources?.[0];
  const plan=source?.work_inventory_query,args=plan?.query_request;
  const publication=source?.business_project??source?.source_context?.business_project;
  const revision=source?.connection_revision??source?.source_context?.connection_revision;
  if(catalog?.status!=='succeeded'||!Array.isArray(catalog.sources)||catalog.execution?.operation!=='data.catalog'
    ||catalog.sources.length!==1||catalog.catalog_view?.next_source_offset!=null||!catalog.catalog_ref
    ||!catalog.project||catalog.execution_scope!==catalog.project||plan?.schema_version!==1
    ||plan.request_ref!==user.data.id||!source.id||plan.source_id!==source.id||args?.source_id!==source.id
    ||!plan.publication?.id||!Number.isSafeInteger(plan.publication.version)||plan.publication.version<=0
    ||publication&&(publication.id!==plan.publication.id||publication.version!==plan.publication.version)
    ||!Number.isSafeInteger(plan.connection_revision)||plan.connection_revision<0||revision!=null&&revision!==plan.connection_revision
    ||args.purpose!=='answer'||args.chart_type!=='table'||typeof args.sql!=='string'||!args.sql.trim()||!plainLabel(args.title))return null;
  if(receipts.some(result=>currentWorkResult(result,user.data.id,sessionId)
    &&(result.answer_contract?.work_inventory||hasWorkMeasurements(result))))return null;
  for(const event of events){
    if(event.type!=='tool/call'||event.data.name!=='data_sql_query'
      ||event.seq<=user.seq||event.data.turn!==user.data.turn)continue;
    let prior=event.data.arguments??event.data.args;
    if(typeof prior==='string'){try{prior=JSON.parse(prior);}catch{continue;}}
    if(prior?.source_id===source.id&&prior.sql===args.sql&&!['explore','probe'].includes(prior.purpose))return null;
  }
  return {name:'data_sql_query',args};
}

function currentWorkResult(result,requestRef,sessionId){
  const lineage=result.result_lineage;
  return result.status==='succeeded'&&result.result_ref&&!isExploratory(result)
    &&result.validation?.state!=='conflict'&&result.validation?.period_grain?.state!=='auxiliary'
    &&result.receipt?.read_only===true&&result.view_url
    &&lineage?.purpose==='answer'&&lineage.request_ref===requestRef&&lineage.session_id===sessionId
    &&lineage.source_id===result.receipt.source_id&&lineage.config_fingerprint&&result.execution_scope;
}

function hasWorkMeasurements(result){
  if(result.metric_contract&&WORK_MEASURE.test(result.metric_contract.name||'')
    &&result.facts?.some(fact=>Number.isFinite(fact.value)))return true;
  const {columns,rows}=result.values||{};
  const units=result.analysis_options?.column_units||{};
  return Array.isArray(columns)&&Array.isArray(rows)&&columns.some((column,index)=>{
    const measured=WORK_MEASURE.test(column)||/^(?:完成率|完成量|完成度|completion_(?:rate|ratio|amount))(?:[（(_%％ ]|$)/i.test(column)
      ||units[column]==='%'&&!/预算|资金|经费|支出|结算|占比|同比|环比/.test(column);
    return measured&&rows.some(row=>Number.isFinite(row[index])
      ||typeof row[index]==='string'&&/^[+-]?\d+(?:\.\d+)?\s*[%％]?$/.test(row[index]));
  });
}

export function completionInventoryAnswer(question,receipts,requestRef,sessionId){
  const match=question.match(WORK_MEASURE);
  if(!match||!requestRef||!sessionId)return null;
  const results=activeEvidence(receipts).filter(result=>currentWorkResult(result,requestRef,sessionId));
  if(results.some(hasWorkMeasurements))return null;
  const selection=new Map();
  for(const result of results){
    const proof=result.answer_contract?.work_inventory, lineage=result.result_lineage;
    if(proof?.schema_version!==1||proof.kind!=='published_annual_population'
      ||proof.result_ref!==result.result_ref||proof.request_ref!==requestRef||proof.session_id!==sessionId
      ||proof.source_id!==lineage.source_id||!lineage.population_fingerprint||!result.receipt.query_hash||result.truncated
      ||result.values?.complete!==true||result.values?.rows?.length!==result.row_count||proof.row_count!==result.row_count
      ||!Number.isSafeInteger(proof.row_count)||proof.row_count<1||!/^(?:19|20)\d{2}$/.test(proof.year)
      ||!plainLabel(proof.subject)||!question.includes(proof.subject)
      ||proof.organization_count!==undefined&&(!Number.isSafeInteger(proof.organization_count)
        ||proof.organization_count<1||proof.organization_count>proof.row_count))continue;
    const identity=[proof.source_id,lineage.config_fingerprint,lineage.population_fingerprint,result.execution_scope,
      proof.year,proof.subject,proof.row_count,proof.organization_count];
    selection.set(JSON.stringify(identity),proof);
  }
  if(selection.size!==1)return null;
  const [proof]=selection.values();
  const work=WORK_TERMS[match[0].slice(0,2)]||'实际完成工作量';
  const lead='在本次查询范围内，'+proof.year+' 年'+proof.subject+'清单查到 **'+proof.row_count+' 条**'
    +(proof.organization_count?'，涉及 **'+proof.organization_count+' 家单位**':'')+'。';
  const remaining=/平均/.test(question)&&/低于|低过|少于|不足|高于|超过|大于|达到/.test(question)
    ?'暂无法计算平均完成率，也无法按所给阈值筛选名单。'
    :/低于|低过|少于|不足|高于|超过|大于|达到/.test(question)?'暂无法按所给阈值筛选名单。'
    :/平均/.test(question)?'暂无法计算平均完成率。':'暂无法核算所问完成情况。';
  return lead+'\n\n本次查询尚未取得可与清单中同一条记录对应的'+work+'和计划工作量，'+remaining;
}

export function verifiedGroupedCountShare(result){
  const proof=result?.answer_contract?.grouped_count_share;
  const values=result?.values;
  if(result?.status!=='succeeded'||!result.result_ref||result.truncated||isExploratory(result)
    ||result.receipt?.read_only!==true||result.validation?.state==='conflict'
    ||result.answer_contract?.kind!=='sql_aggregate'||result.answer_contract?.complete!==true
    ||result.answer_contract.result_ref!==result.result_ref||proof?.schema_version!==1||proof.result_ref!==result.result_ref
    ||!Number.isSafeInteger(proof.denominator)||proof.denominator<=0||!plainLabel(proof.unit)
    ||!Number.isInteger(proof.rounding_digits)||proof.rounding_digits<0||proof.rounding_digits>6
    ||values?.complete!==true||!Array.isArray(values.columns)||values.columns.length!==3||!Array.isArray(values.rows)
    ||values.rows.length!==values.row_count||values.rows.length!==result.row_count
    ||values.rows.length<2||values.rows.length>80
    ||values.rows.some(row=>!Array.isArray(row)||row.length!==3))return null;
  const {category_column:category,count_column:count,share_column:share,denominator,unit}=proof;
  const columns=[category,count,share];
  // The percentage uses a fixed display heading. Its SQL alias only binds the verified column.
  if(new Set(columns).size!==3||typeof share!=='string'||!share.trim()
    ||[category,count].some(column=>!plainLabel(column)||!hasChinese.test(column)))return null;
  const [ci,ni,si]=columns.map(column=>values.columns.indexOf(column));
  const units=result.analysis_options?.column_units;
  if(Math.min(ci,ni,si)<0||units?.[count]!==unit||units?.[share]!=='%')return null;
  const rows=values.rows.map(row=>[row[ci],row[ni],row[si]]);
  const tolerance=0.5*10**-proof.rounding_digits+1e-7;
  if(rows.some(([label,n,p])=>!plainLabel(label)||!label.trim()||!Number.isSafeInteger(n)||n<0||!Number.isFinite(p)
    ||p<0||p>100||Math.abs(p-n*100/denominator)>tolerance)
    ||new Set(rows.map(([label])=>label.normalize('NFKC').trim())).size!==rows.length
    ||rows.reduce((sum,row)=>sum+row[1],0)!==denominator)return null;
  return {category,count,share,denominator,unit,rows,digits:proof.rounding_digits};
}

function verifiedCountTable(result){
  const {answer_contract:contract,values}=result;
  if(contract?.kind!=='sql_aggregate'||contract.result_ref!==result.result_ref||contract.complete!==true
    ||result.receipt?.read_only!==true||result.truncated||result.validation?.state==='conflict'
    ||values?.complete!==true||values.row_count!==result.row_count||!Array.isArray(values.columns)||values.columns.length!==2
    ||!Array.isArray(values.rows)||values.rows.length!==result.row_count||values.rows.length<2||values.rows.length>50)return null;
  const [category,count]=values.columns,unit=result.analysis_options?.column_units?.[count];
  if(![category,count].every(name=>plainLabel(name)&&hasChinese.test(name))||category===count
    ||!/^(?:条|件|个|家|笔|人|项|张|份|宗|批|次|本|台|辆|户)$/.test(unit||''))return null;
  const rows=values.rows;
  if(rows.some(row=>!Array.isArray(row)||row.length!==2||!plainLabel(row[0])||!row[0].trim()
    ||!countValue(row[1]))||new Set(rows.map(([label])=>label.normalize('NFKC').trim())).size!==rows.length)return null;
  return {category,count,unit,rows};
}

function computedCountShare(result,grouped,computed){
  if(!grouped||result.answer_contract.grouped_distinct||!result.execution_scope)return null;
  const {count,rows}=grouped;
  const denominator=rows.reduce((sum,[,value])=>sum+value,0);
  if(!Number.isSafeInteger(denominator)||denominator<=0)return null;
  const shares=computed.flatMap(({column,receipt})=>{
    if(column!==count||receipt?.status!=='succeeded'||receipt.result_ref!==result.result_ref
      ||receipt.execution_scope!==result.execution_scope
      ||!['analysis.compute','analysis.method'].includes(receipt.execution?.operation))return [];
    return (receipt.claims||[]).filter(claim=>claim.operation==='matrix_method'&&claim.method_id==='matrix.13'
      &&claim.sample_count===rows.length&&claim.statistics?.total===denominator).map(claim=>claim.statistics.shares_percent);
  });
  if(!shares.length||shares.some(values=>!Array.isArray(values)||values.length!==rows.length||values.some((value,index)=>
    !Number.isFinite(value)||Math.abs(value-rows[index][1]*100/denominator)>1e-8)))return null;
  return {...grouped,denominator,rows:rows.map(([label,value],i)=>[label,value,shares[0][i]]),digits:2};
}

const groupedDeliveryExcluded=/同比|环比|预测|变化|以及另|另外|另查|再查|[前后][几一二三四五六七八九十\d]+(?:名|位)|最多|最少|最高|最低|排名|排行|阈值|建议|明细|导出|下载/;
const groupShareRequested=/占比|比例|百分比|份额|(?:各|分别|各自)占/;
const formalGroups=evidence=>activeEvidence(evidence).filter(result=>result.result_ref&&result.status==='succeeded'
  &&!isExploratory(result)&&result.validation?.period_grain?.state!=='auxiliary');

function scopedCountReceipt(result,question,requestRef,sessionId){
  const lineage=result.result_lineage,scope=result.validation?.request_scope?.request;
  return ['data.sql_query','data.query'].includes(result.execution?.operation)&&result.receipt?.query_hash
    &&result.execution_scope&&lineage?.schema_version===1&&lineage.request_ref===requestRef
    &&lineage.session_id===sessionId&&lineage.purpose==='answer'&&lineage.source_id
    &&lineage.source_id===result.receipt.source_id&&scope?.user_ref===requestRef&&scope.quote===question;
}

export function verifiedMonthlyGroupShare(question,evidence,requestRef,sessionId){
  const matches=formalGroups(evidence).filter(result=>{
    const contract=result.answer_contract, proof=contract?.monthly_group_share;
    const units=result.analysis_options?.column_units;
    return contract?.complete===true&&scopedCountReceipt(result,question,requestRef,sessionId)&&result.receipt.read_only===true
      &&!result.truncated&&result.validation?.state!=='conflict'
      &&proof?.schema_version===1&&proof.result_ref===result.result_ref
      &&proof.basis==='complete_monthly_row_count_partition'
      &&plainLabel(proof.category_column)&&plainLabel(proof.unit)
      &&proof.unit===units?.[proof.count_column]&&units?.[proof.share_column]==='%'
      &&Array.isArray(proof.leaders)&&proof.leaders.length>=1&&proof.leaders.length<=60;
  });
  if(matches.length!==1)return null;
  const {title,answer_contract:{monthly_group_share:proof}}=matches[0];
  const periods=new Set(), rows=[];
  for(const item of proof.leaders){
    if(!/^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$/.test(item?.period||'')||periods.has(item.period)
      ||!countValue(item.total)||!countValue(item.count)||item.count>item.total
      ||!Array.isArray(item.categories)||item.categories.some(label=>!plainLabel(label)))return null;
    periods.add(item.period);
    if(item.total===0){
      if(item.categories.length||item.share!==null)return null;
      rows.push([item.period,'—','0','0','—']);
    }else{
      if(!item.count||!item.categories.length||!Number.isFinite(item.share)||item.share<0||item.share>100
        ||Math.abs(item.share-100*item.count/item.total)>.005000001)return null;
      rows.push([item.period,item.categories.join('、'),String(item.count),String(item.total),item.share+'%']);
    }
  }
  const heading=plainLabel(title)&&hasChinese.test(title)?title:'逐月统计';
  const missing=proof.unobserved||[];
  if(!Array.isArray(missing)||missing.some(month=>!/^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$/.test(month)||periods.has(month)))return null;
  const coverage=missing.length?'查询区间内 '+missing.join('、')+' 未返回记录。':'表中列出已观测月份。';
  return '**'+heading+'**\n\n各月占比按当月合计计算，并列第一全部列出。\n\n'
    +formatTable(['月份','占比最高的'+proof.category_column,'每类数量（'+proof.unit+'）', '当月合计（'+proof.unit+'）','占比'],rows)
    +'\n\n'+coverage;
}

function countPartitionReceipt(result,question,requestRef,sessionId){
  const contract=result.answer_contract, proof=contract?.row_count_partition, values=result.values;
  if(!scopedCountReceipt(result,question,requestRef,sessionId)||!result.result_lineage.config_fingerprint
    ||contract?.kind!=='sql_aggregate'||contract.complete!==true||contract.result_ref!==result.result_ref
    ||contract.grouped_distinct||proof?.schema_version!==1||proof.result_ref!==result.result_ref
    ||!proof.population_fingerprint||result.receipt.read_only!==true||result.truncated
    ||result.validation?.state==='conflict'||values?.complete!==true)return null;
  if(proof.group_fingerprint){
    const original=verifiedGroupedCountShare(result);
    const grouped=original||verifiedCountTable(result);
    if(!grouped||grouped.count!==proof.count_column||grouped.category!==proof.category_column)return null;
    const rows=JSON.stringify(grouped.rows.map(([label,value])=>[label.normalize('NFKC').trim(),value]).sort());
    return {result,proof,grouped,original,rows,unit:grouped.unit};
  }
  if(proof.category_column||values.row_count!==1||result.row_count!==1||values.columns?.length!==1
    ||values.columns[0]!==proof.count_column||values.rows?.length!==1||values.rows[0]?.length!==1
    ||!countValue(values.rows[0][0]))return null;
  return {result,proof,total:values.rows[0][0],unit:result.analysis_options?.column_units?.[proof.count_column]};
}

function sameCountPartition(entry,first,denominator){
  const left=entry.result.result_lineage,right=first.result.result_lineage;
  if(left.source_id!==right.source_id||left.config_fingerprint!==right.config_fingerprint
    ||entry.result.execution_scope!==first.result.execution_scope||entry.unit!==first.unit)return false;
  const samePopulation=entry.proof.population_fingerprint===first.proof.population_fingerprint;
  if(!entry.grouped)return samePopulation&&entry.total===denominator;
  // Both complete tables were validated to contain only nonempty labels.
  // In that case a predicate excluding NULL on this exact grouping column
  // preserves the partition, even across different SQL aliases or row order.
  const sameNonNull=entry.proof.non_null_category_population
    &&entry.proof.non_null_category_population===first.proof.non_null_category_population;
  return Boolean((samePopulation||sameNonNull)
    &&entry.proof.group_fingerprint===first.proof.group_fingerprint&&entry.rows===first.rows);
}

const partitionShares=entry=>JSON.stringify(entry.original.rows.map(([label,,value])=>[label.normalize('NFKC').trim(),value]).sort());

export function uniqueCountShareDeliveries(results,question){
  if(!groupShareRequested.test(question)||groupedDeliveryExcluded.test(question))return results;
  const entries=[];
  return results.filter(result=>{
    const lineage=result.result_lineage;
    if(!lineage?.request_ref||!lineage.session_id)return true;
    const entry=countPartitionReceipt(result,question,lineage.request_ref,lineage.session_id);
    if(!entry?.original||result.claims?.length||result.verified_claims?.length)return true;
    const prior=entries.find(other=>other.result.result_lineage.request_ref===lineage.request_ref
      &&other.result.result_lineage.session_id===lineage.session_id
      &&sameCountPartition(entry,other,other.original.denominator)
      &&entry.original.digits===other.original.digits&&partitionShares(entry)===partitionShares(other));
    if(prior)return false;
    entries.push(entry);
    return true;
  });
}

function compatibleCountShare(formal,question,requestRef,sessionId,computed=[]){
  const entries=formal.map(result=>countPartitionReceipt(result,question,requestRef,sessionId));
  if(entries.some(entry=>!entry))return null;
  const grouped=entries.filter(entry=>entry.grouped);
  const first=grouped[0];
  if(!first)return null;
  const denominator=first.grouped.rows.reduce((sum,[,value])=>sum+value,0);
  if(!Number.isSafeInteger(denominator)||denominator<=0
    ||entries.some(entry=>!sameCountPartition(entry,first,denominator)))return null;
  const originals=grouped.filter(entry=>entry.original);
  if(originals.length){
    const original=originals[0], expected=partitionShares(original);
    if(originals.some(entry=>entry.original.digits!==original.original.digits||partitionShares(entry)!==expected))return null;
    return original;
  }
  return grouped.find(entry=>computedCountShare(entry.result,entry.grouped,computed))||first;
}

export function groupedCountShareTool(question,evidence,requestRef,sessionId){
  if(!requestRef||!sessionId||groupedDeliveryExcluded.test(question)||!groupShareRequested.test(question))return null;
  const formal=formalGroups(evidence);
  const selected=formal.length>1?compatibleCountShare(formal,question,requestRef,sessionId):null;
  if(selected?.original)return null;
  const result=formal.length===1?formal[0]:selected?.result;
  if(!result||!scopedCountReceipt(result,question,requestRef,sessionId)||result.answer_contract?.grouped_distinct)return null;
  const grouped=selected?.grouped||verifiedCountTable(result);
  if(!grouped)return null;
  const denominator=grouped.rows.reduce((sum,[,value])=>sum+value,0);
  if(!Number.isSafeInteger(denominator)||denominator<=0)return null;
  return {name:'analysis_compute',args:{result_ref:result.result_ref,operation:'share',column:grouped.count}};
}

export function groupedCountShareAnswer(question,evidence,requestRef,sessionId,computed=[]){
  if(groupedDeliveryExcluded.test(question))return null;
  const asksShare=groupShareRequested.test(question);
  const asksCount=!asksShare&&/(?:各(?:地市|地区|城市|分类|类别|单位)|分(?:地区|类别|分类))[^。；\n]{0,24}(?:数量|数目|多少|件数|条数|户数|家数)/.test(question)
    &&!/合计|总计|总数|总量|及|和|与|另|[，,、；;\n]/.test(question);
  if(!asksShare&&!asksCount)return null;
  const formal=formalGroups(evidence);
  if(!requestRef||!sessionId)return null;
  const hasPartition=formal.some(item=>item.answer_contract?.row_count_partition);
  const selected=asksShare&&formal.length>1&&hasPartition
    ?compatibleCountShare(formal,question,requestRef,sessionId,computed):null;
  // A replay of older receipts can still deliver its already-proven shares.
  const legacyShares=asksShare&&formal.length===2&&!hasPartition?formal.filter(verifiedGroupedCountShare):[];
  const result=formal.length===1?formal[0]:selected?.result||(legacyShares.length===1?legacyShares[0]:null);
  if(!result)return null;
  const lineage=result.result_lineage;
  if(!requestRef||!sessionId||lineage?.request_ref!==requestRef||lineage?.session_id!==sessionId
    ||!lineage.source_id||lineage.source_id!==result.receipt?.source_id||lineage.purpose!=='answer')return null;
  const scope=result.validation?.request_scope?.request;
  const sameRequest=scope?.user_ref===requestRef&&scope.quote===question;
  if(legacyShares.length===1){
    const other=formal.find(item=>item!==result),otherLineage=other.result_lineage;
    if(!sameRequest||!result.execution_scope||other.execution_scope!==result.execution_scope
      ||other.row_count!==1||other.receipt?.read_only!==true||other.receipt.source_id!==lineage.source_id
      ||otherLineage&&(otherLineage.request_ref!==requestRef||otherLineage.session_id!==sessionId||otherLineage.purpose!=='answer'))return null;
  }
  const grouped=selected?.grouped||(sameRequest?verifiedCountTable(result):null);
  if(asksCount){
    if(!grouped)return null;
    const label=value=>String(value||'').normalize('NFKC').replace(/[\s。！？!?、，,]/g,'');
    if(label(result.title)!==label(question))return null;
    const table=literalSqlRows(result);
    if(!table)return null;
    const unknownScope=result.validation?.checks?.some(check=>check.state==='unknown'&&check.area==='scope');
    const title=unknownScope&&/全省|全市|全区|全国|全部/.test(table.title)?'查询结果':table.title;
    return withDefinitionNotice('**'+title+'**\n\n'+table.text,{result});
  }
  const original=selected?.original||verifiedGroupedCountShare(result);
  const verified=original||computedCountShare(result,grouped,computed);
  if(!verified)return null;
  const {category,count,denominator,unit,rows,digits}=verified;
  const label=count.replace(/[（(][^）)]+[）)]$/,'');
  const wideScope=/全省|全市|全区|全县|全国|总体/.test(question);
  const table=formatTable([category,label+'（'+unit+'）',original&&!wideScope?'占比':'本次查询占比'],rows.map(([name,n,share])=>
    [name,String(n),share.toFixed(digits)+'%']));
  const prefix=original?'本次查询覆盖 '+rows.length+' 个分组，数据合计为 ':'本次查询合计 ';
  const total=prefix+'**'+denominator.toLocaleString('zh-CN')+' '+unit+'**。';
  const text=withDefinitionNotice([total,table].join('\n\n'),{result});
  return wideScope||(!original&&/全部/.test(question))
    ||result.validation?.checks?.some(check=>check.state==='unknown'&&check.area==='scope')
    ?text+'\n\n占比按本次查询范围计算，整体覆盖范围尚未核实。':text;
}
