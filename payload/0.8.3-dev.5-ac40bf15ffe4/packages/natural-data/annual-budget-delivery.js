import {isExploratory} from './presentation-intent.js';
import {reportedSpendingObservation} from './forecast-evidence.js';

const year=/^(?:19|20)\d{2}$/;
const moneyScale={'元':1,'万元':1e4,'亿元':1e8};
const plain=value=>typeof value==='string'&&value.trim()&&value.length<=200&&!/[\r\n<>|]/.test(value);
const samePublication=(a,b)=>a?.id&&a.id===b?.id&&Number.isSafeInteger(a.version)&&a.version>0&&a.version===b?.version;

function reportCoverage(plan,catalog){
  const proof=plan?.report_coverage, args=proof?.query_request;
  return catalog.runtime_contract?.source==='active-engine'&&plain(catalog.runtime_contract.build_id)
    &&Array.isArray(proof?.columns)&&proof.columns.length===2&&proof.columns.every(plain)&&new Set(proof.columns).size===2
    &&args?.source_id===plan.source_id&&args.purpose==='explore'&&args.chart_type==='table'
    &&plain(args.title)&&typeof args.sql==='string'&&args.sql.trim()?proof:null;
}

function budgetPlan(catalogs,user,comparison){
  const plans=new Map();
  for(const catalog of catalogs){
    const [source,...other]=catalog.sources||[];
    const plan=comparison?source?.comparison_budget_read:source?.preferred_budget_read;
    const args=plan?.query_request, otherYear=comparison?plan?.compared_year:plan?.fallback_year;
    if(!plan||other.length||catalog.status!=='succeeded'||catalog.execution?.operation!=='data.catalog'
      ||!catalog.catalog_ref||!catalog.project||catalog.execution_scope!==catalog.project
      ||catalog.catalog_view?.next_source_offset!=null||plan.schema_version!==1||plan.request_ref!==user?.data.id
      ||!source.id||plan.source_id!==source.id||args?.source_id!==source.id
      ||!samePublication(plan.publication,source.business_project)
      ||!Number.isSafeInteger(plan.connection_revision)||plan.connection_revision<0
      ||plan.connection_revision!==source.connection_revision||args.purpose!=='answer'||args.chart_type!=='table'
      ||!plain(args.title)||typeof args.sql!=='string'||!args.sql.trim()
      ||!year.test(plan.year)||!year.test(otherYear)||plan.year===otherYear
      ||comparison&&plan.organization_count!==null&&(!Number.isSafeInteger(plan.organization_count)||plan.organization_count<1)
      ||!plain(plan.column)||!Object.hasOwn(moneyScale,plan.unit))continue;
    const key=JSON.stringify([catalog.execution_scope,plan.source_id,plan.publication,plan.connection_revision,
      plan.year,otherYear,args.sql,plan.other_questions]);
    const coverage=comparison&&reportCoverage(plan,catalog);
    const prior=plans.get(key);
    if(coverage&&prior?.report_coverage&&JSON.stringify(prior.report_coverage)!==JSON.stringify(coverage))return null;
    // Later views of the same catalog can trim the report plan from their JSON.
    const retained=prior?.catalog_ref===catalog.catalog_ref?prior.report_coverage:undefined;
    plans.set(key,{...plan,scope:catalog.execution_scope,catalog_ref:catalog.catalog_ref,report_coverage:coverage||retained});
  }
  return plans.size===1?[...plans.values()][0]:null;
}

export const preferredBudgetPlan=(catalogs,user)=>budgetPlan(catalogs,user,false);
export const comparisonBudgetPlan=(catalogs,user)=>budgetPlan(catalogs,user,true);

export function broadComparisonCatalog(args,sourceId){
  return args&&typeof args==='object'&&!Array.isArray(args)
    &&(!args.source_id||args.source_id===sourceId)
    &&Object.keys(args).every(key=>key==='source_id'||key==='question'||key==='view'&&args.view==='full');
}

function matchesComparisonCatalog(plan,catalog){
  return catalog?.status==='succeeded'&&catalog.catalog_ref
    &&catalog.execution?.operation==='data.catalog'&&catalog.execution_scope===plan.scope
    &&catalog.project===plan.scope&&catalog.sources?.length===1&&catalog.sources[0].id===plan.source_id
    &&catalog.catalog_view?.next_source_offset==null;
}

export function comparisonCatalogExhausted(plan,catalogs){
  // Preserve the initial lookup, one follow-up for the amount and one for
  // each other question. Paging, entry reads and targeted table reads stay available.
  const searches=catalogs.filter(catalog=>matchesComparisonCatalog(plan,catalog));
  const latest=searches.at(-1)?.catalog_ref;
  return searches.filter(catalog=>catalog.catalog_ref===latest).length>=2+(Array.isArray(plan.other_questions)?plan.other_questions.length:0);
}

const normalizeSearch=text=>plain(text)?text.normalize('NFKC').replace(/\s+/g,''):'';

function focusedQuantitySearch(text){
  return normalizeSearch(text).match(/^(?:请)?(?:另查|另问|另看|核实|查看|查询|查|统计)?((?:19|20)\d{2})?(?:年度?)?([\p{Script=Han}]+?)(?:是?多少)?[?？。]?$/u);
}

function comparisonSearches(plan,events){
  const calls=new Map(), searches=new Map();
  for(const event of events){
    if(event.type==='tool/call'&&event.data.name==='data_catalog'){
      let args=event.data.arguments??event.data.args;
      if(typeof args==='string'){try{args=JSON.parse(args);}catch{continue;}}
      // A newer catalog page is never suppressed by this budget.
      if(broadComparisonCatalog(args,args?.source_id))calls.set(event.data.callId,{args,turn:event.data.turn});
    }
    if(event.type!=='tool/result'||event.surfaceOp&&event.surfaceOp!=='append')continue;
    const message=event.data.message;
    const id=message?.source?.callId??message?.toolCallId;
    const call=calls.get(id);
    if(!call||message.isError||event.data.turn!==call.turn)continue;
    for(const block of message.content||[]){
      if(block.type!=='text')continue;
      try{
        const catalog=JSON.parse(block.text);
        if(broadComparisonCatalog(call.args,plan.source_id)&&matchesComparisonCatalog(plan,catalog)){
          searches.set(id,{args:call.args,catalog});
        }else if(catalog?.status==='succeeded'&&catalog.execution?.operation==='data.catalog'){
          searches.clear();
        }
      }catch{/* Non-catalog text proves nothing about the search. */}
    }
  }
  const completed=[...searches.values()];
  const latest=completed.at(-1)?.catalog.catalog_ref;
  return completed.filter(search=>search.catalog.catalog_ref===latest);
}

function comparisonTopics(plan,searches){
  const other=Array.isArray(plan.other_questions)?plan.other_questions:[];
  const topics=other.map(question=>normalizeSearch(question)
    .replace(/^(?:另查|另问|另看)?(?:19|20)\d{2}年度?/,'').replace(/(?:是)?多少$/,''));
  const dedicated=searches.filter(search=>search.args.source_id===plan.source_id)
    .map(search=>normalizeSearch(search.args.question));
  return {topics,dedicated,covered:topics.every(topic=>topic.length>=2&&dedicated.some(question=>question.includes(topic)))};
}

export function comparisonDiscoveryCovered(plan,events,buildId,{reportCoverageVerified=false}={}){
  if(!buildId)return false;
  const searches=comparisonSearches(plan,events);
  const {topics,dedicated}=comparisonTopics(plan,searches);
  // The compound original question is not a focused search for each part.
  const focused=dedicated.map(question=>question.replace(/^(?:请)?(?:另查|另问|另看|核实|查看|查询|查|统计)?(?:(?:19|20)\d{2}(?:年度?)?)?/,''));
  return searches.length>0&&(reportCoverageVerified||comparisonCatalogExhausted(plan,searches.map(search=>search.catalog)))
    &&searches.every(({catalog})=>catalog.runtime_contract?.source==='active-engine'&&catalog.runtime_contract.build_id===buildId)
    &&topics.every(topic=>topic.length>=2&&focused.some(question=>question.startsWith(topic)));
}

export function redundantComparisonSearch(plan,events,nextArgs,{reportCoverageVerified=false}={}){
  if(!plan||!broadComparisonCatalog(nextArgs,plan.source_id))return false;
  const next=normalizeSearch(nextArgs.question);
  if(!next)return false;
  const recent=comparisonSearches(plan,events);
  if(!reportCoverageVerified&&!comparisonCatalogExhausted(plan,recent.map(search=>search.catalog)))return false;
  const {topics,dedicated,covered}=comparisonTopics(plan,recent);
  if(!covered)return false;
  if(recent.some(search=>normalizeSearch(search.args.question)===next))return true;
  const focus=focusedQuantitySearch(next);
  if(focus&&plan.other_questions?.some(question=>{
    const requested=focusedQuantitySearch(question);
    return requested?.[2]===focus[2]&&(!focus[1]||focus[1]===requested[1])
      &&dedicated.includes(normalizeSearch(question));
  }))return true;
  return topics.some(topic=>next.includes(topic))&&dedicated.some(question=>question.includes(next));
}

export function redundantComparisonCoverageRead(plan,events,args,buildId){
  if(!args||args.source_id&&args.source_id!==plan.source_id)return false;
  if(redundantComparisonSearch(plan,events,args,{reportCoverageVerified:true}))return true;
  const searches=comparisonSearches(plan,events).filter(({catalog})=>
    catalog.runtime_contract?.source==='active-engine'&&catalog.runtime_contract.build_id===buildId);
  const entry=plan.budget_entry;
  if(entry?.source_id===plan.source_id&&args.source_id===entry.source_id&&args.entry_ref
    &&args.entry_ref===entry.entry_ref&&args.entry_version===entry.entry_version
    &&Object.keys(args).every(key=>['source_id','entry_ref','entry_version'].includes(key)))return true;
  if(args.source_id===plan.source_id&&plain(args.entry_ref)&&plain(args.entry_version)
    &&Object.keys(args).every(key=>['source_id','entry_ref','entry_version','entry_offset','entry_limit'].includes(key))){
    // A complete receipt for this exact entry and catalog snapshot already
    // contains every page. An unseen version or incomplete read still runs.
    const calls=new Map(events.filter(event=>event.type==='tool/call'&&event.data.name==='data_catalog')
      .map(event=>[event.data.callId,event.data.turn]));
    return events.some(event=>{
      if(event.type!=='tool/result'||event.surfaceOp&&event.surfaceOp!=='append')return false;
      const message=event.data.message;
      if(message?.isError||calls.get(message?.source?.callId)!==event.data.turn)return false;
      try{
        const catalog=JSON.parse(message.content.find(block=>block.type==='text')?.text||'{}');
        const [selected,...other]=catalog.sources||[];
        const detail=selected?.entry;
        return catalog.status==='succeeded'&&catalog.execution?.operation==='data.catalog'
          &&catalog.runtime_contract?.source==='active-engine'&&catalog.runtime_contract.build_id===buildId
          &&catalog.catalog_ref===plan.catalog_ref&&catalog.execution_scope===plan.scope&&catalog.project===plan.scope
          &&!other.length&&selected?.id===args.source_id&&detail?.source_id===args.source_id
          &&detail.entry_ref===args.entry_ref&&detail.read_request?.entry_version===args.entry_version
          &&detail.complete===true&&detail.offset===0&&detail.next_offset==null;
      }catch{return false;}
    });
  }
  if(args.table_name&&args.source_id===plan.source_id
    &&Object.keys(args).every(key=>key==='source_id'||key==='table_name'||key==='view'&&args.view==='full')){
    return searches.some(({catalog})=>catalog.sources[0].tables?.some(table=>table.name===args.table_name
      &&Array.isArray(table.columns)&&table.columns.length>0&&table.next_column_offset==null
      &&(table.column_count==null||table.column_count===table.columns.length)));
  }
  const focus=plan.amount_lookup;
  const requested=normalizeSearch(args.question);
  if(!focus||!plain(focus.question)||!plain(focus.measure)||!broadComparisonCatalog(args,plan.source_id))return false;
  const comparable=text=>normalizeSearch(text).replace(/^实际/,'');
  return ((requested===normalizeSearch(focus.question))||comparable(requested)===comparable(focus.measure))
    &&searches.some(({args})=>args.source_id===plan.source_id&&normalizeSearch(args.question)===normalizeSearch(focus.question));
}

function plannedSqlCall(request,event,user){
  if(!request||event.type!=='tool/call'||event.data.name!=='data_sql_query'
    ||event.data.turn!==user?.data.turn||event.seq<=user.seq)return false;
  let args=event.data.arguments??event.data.args;
  if(typeof args==='string'){try{args=JSON.parse(args);}catch{return false;}}
  return args?.source_id===request.source_id&&args.sql===request.sql
    &&(args.purpose||'answer')===request.purpose&&!args.parameters?.length&&!args.replaces_result_ref;
}

export const preferredBudgetCall=(plan,event,user)=>plannedSqlCall(plan?.query_request,event,user);
export const comparisonReportCoverageCall=(plan,event,user)=>plannedSqlCall(plan?.report_coverage?.query_request,event,user);

export function comparisonCoverageTools(plan,events){
  if(!plan.report_coverage)return [];
  const focused=comparisonTopics(plan,comparisonSearches(plan,events)).dedicated;
  const other=Array.isArray(plan.other_questions)?plan.other_questions:[];
  const requests=[...other.slice(0,2),plan.amount_lookup?.question]
    .filter(question=>plain(question)&&!focused.includes(normalizeSearch(question)));
  return [{name:'data_sql_query',args:plan.report_coverage.query_request},...requests.map(question=>({
    name:'data_catalog',args:{source_id:plan.source_id,question},
  }))];
}

export function unobservedComparisonReports({plan,result},receipts){
  const proof=plan.report_coverage;
  if(!proof)return false;
  const original=result.result_lineage;
  const years=[plan.year,plan.compared_year];
  return receipts.some(candidate=>{
    const lineage=candidate.result_lineage, values=candidate.values;
    return candidate.status==='succeeded'&&candidate.result_ref&&candidate.execution?.operation==='data.sql_query'
      &&candidate.execution_scope===plan.scope&&candidate.purpose==='explore'&&lineage?.purpose==='explore'
      &&lineage.config_fingerprint===original.config_fingerprint&&lineage.request_ref===original.request_ref
      &&lineage.session_id===original.session_id&&lineage.source_id===original.source_id
      &&samePublication(candidate.business_project??lineage.publication,plan.publication)
      &&candidate.receipt?.read_only===true&&candidate.receipt.source_id===plan.source_id&&candidate.receipt.query_hash
      &&candidate.source_provenance?.connection_revision===plan.connection_revision&&!candidate.truncated
      &&candidate.validation?.state!=='conflict'&&candidate.row_count===years.length&&values?.complete===true
      &&values.columns?.length===proof.columns.length&&proof.columns.every((column,index)=>values.columns[index]===column)
      &&values.rows?.length===years.length&&years.every(year=>values.rows.some(row=>Array.isArray(row)&&row.length===2
        &&row[0]===year&&row[1]===0));
  });
}

export function preferredBudgetObservation(plan,results,request,session){
  if(!plan)return null;
  const records=new Map();
  for(const result of results){
    const lineage=result.result_lineage, publication=result.business_project??lineage?.publication;
    const measure=result.answer_contract?.measurements?.[plan.column];
    const rows=result.values?.rows, columns=result.values?.columns;
    if(result.status!=='succeeded'||!result.result_ref||isExploratory(result)||!result.view_url
      ||result.validation?.state==='conflict'||result.validation?.period_grain?.state==='auxiliary'
      ||result.truncated||result.execution_scope!==plan.scope||!samePublication(publication,plan.publication)
      ||lineage?.purpose!=='answer'||lineage.request_ref!==request||lineage.session_id!==session
      ||lineage.source_id!==plan.source_id||!lineage.config_fingerprint||!lineage.population_fingerprint
      ||result.receipt?.read_only!==true||result.receipt.source_id!==plan.source_id||!result.receipt.query_hash
      ||result.source_provenance?.connection_revision!==plan.connection_revision
      ||result.answer_contract?.result_ref!==result.result_ref||measure?.role!=='budget'
      ||measure.basis!=='published_field_sum'||measure.unit!==plan.unit
      ||result.values?.complete!==true||result.row_count!==1||columns?.length!==1||columns[0]!==plan.column
      ||rows?.length!==1||rows[0]?.length!==1)continue;
    const raw=rows[0][0];
    if(typeof raw!=='number'&&!(typeof raw==='string'&&/^[+-]?\d+(?:\.\d+)?$/.test(raw)))continue;
    const amount=Number(raw);
    if(Number.isFinite(amount))records.set(result.result_ref,{plan,result,amount});
  }
  return records.size===1?[...records.values()][0]:null;
}

const requestedAmountLabel=/(?:投入|投资|支出|拨付)(?:总?金额|额)[^\r\n]*[（(](?:人民币)?(?:万|亿)?元[）)]$/;

export function comparisonBudgetNeeded(plan,results,request,session){
  if(!plan)return false;
  for(const result of results){
    const lineage=result.result_lineage;
    if(result.status!=='succeeded'||!result.result_ref||isExploratory(result)||result.truncated
      ||result.validation?.state==='conflict'||result.validation?.period_grain?.state==='auxiliary'
      ||result.execution_scope!==plan.scope||lineage?.purpose!=='answer'||lineage.request_ref!==request
      ||lineage.session_id!==session||lineage.source_id!==plan.source_id||result.receipt?.source_id!==plan.source_id
      ||result.receipt.read_only!==true||result.source_provenance?.connection_revision!==plan.connection_revision
      ||!samePublication(result.business_project??lineage.publication,plan.publication)
      ||result.values?.complete!==true||!Array.isArray(result.values.columns)
      ||!Array.isArray(result.values.rows)||result.values.rows.length!==result.row_count)continue;
    if(reportedSpendingObservation(result,request,session)?.complete)return false;
    for(const [index,column] of result.values.columns.entries()){
      const measure=result.answer_contract?.measurements?.[column];
      if(measure?.role!=='actual_fiscal'&&(measure||!requestedAmountLabel.test(column)))continue;
      // No positive financial claim is inferred from a model-chosen column
      // name. A returned amount, including a real zero, keeps normal delivery.
      if(result.values.rows.some(row=>row?.[index]!==null))return false;
    }
  }
  // The catalog has a separately bound budget read. It can supply context
  // while the requested actual-amount comparison remains unresolved.
  return true;
}

export function describesPreferredBudget(text,{plan,amount}){
  if(typeof text!=='string'||!text.includes(plan.year)||!/预算/.test(text))return false;
  const prose=text.replaceAll('**','');
  return [...prose.matchAll(/(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|元)/g)]
    .some(match=>Math.abs(Number(match[1].replaceAll(',',''))*moneyScale[match[2]]-amount*moneyScale[plan.unit])<.005)
    ||prose.split('\n').some(line=>/万元|亿元|元/.test(prose)&&line.includes('|')&&line.includes(plan.year)
      &&line.split('|').some(cell=>/^-?\d[\d,]*(?:\.\d+)?$/.test(cell.trim())
        &&Number(cell.trim().replaceAll(',',''))===amount));
}

const budgetScope=/台账所列|台账(?:登记|范围)|登记[^。；\n]{0,22}(?:单位|范围)/;
const broaderBudget=/全省|全市|全区|全县|全国|全部项目|所有项目|行业总额/;

export function comparisonBudgetWording(text,observation){
  const paragraphs=text.split(/[。；;\n]/).filter(clause=>describesPreferredBudget(clause,observation));
  return {shown:paragraphs.some(clause=>budgetScope.test(clause)&&!broaderBudget.test(clause)),
    overbroad:paragraphs.some(clause=>broaderBudget.test(clause)&&!budgetScope.test(clause))};
}

export function comparisonBudgetParagraph({plan,amount}){
  const value=amount.toLocaleString('zh-CN',{maximumFractionDigits:10});
  const population=plan.organization_count===null?'台账登记范围':'台账所列单位范围';
  return `按${population}核实，${plan.year} 年项目预算为 **${value} ${plan.unit}**，可作预算参考。`;
}

function otherMeasuredResult(formal,result){
  return formal.some(item=>item.result_ref!==result.result_ref&&(item.facts?.some(fact=>Number.isFinite(fact.value))
    ||item.values?.rows?.some(row=>row.some(value=>typeof value==='number'
      ||typeof value==='string'&&/^[+-]?\d+(?:\.\d+)?$/.test(value)))));
}

function sameComparisonScope(candidate,{plan,result}){
  const lineage=candidate.result_lineage;
  const original=result.result_lineage;
  return candidate.status==='succeeded'&&lineage?.purpose==='answer'&&!!original?.config_fingerprint
    &&lineage.request_ref===original.request_ref&&lineage.session_id===original.session_id
    &&lineage.source_id===plan.source_id&&lineage.config_fingerprint===original.config_fingerprint
    &&candidate.execution_scope===plan.scope&&samePublication(candidate.business_project??lineage.publication,plan.publication)
    &&candidate.receipt?.read_only===true&&candidate.receipt.source_id===plan.source_id
    &&candidate.source_provenance?.connection_revision===plan.connection_revision&&!candidate.truncated
    &&candidate.validation?.state!=='conflict'&&candidate.validation?.period_grain?.state!=='auxiliary'
    &&candidate.values?.complete===true;
}

function sameComparisonRead(candidate,observation){
  return sameComparisonScope(candidate,observation)&&candidate.row_count===1
    &&candidate.answer_contract?.result_ref===candidate.result_ref;
}

function repeatsBudgetReference(candidate,observation){
  if(!sameComparisonRead(candidate,observation))return false;
  const {plan,result,amount}=observation;
  const {columns,rows}=candidate.values;
  if(!Array.isArray(columns)||columns.length<1||columns.length>2||rows?.length!==1||rows[0]?.length!==columns.length)return false;
  if(columns.length===1&&candidate.receipt.query_hash!==result.receipt?.query_hash)return false;
  const budgets=columns.flatMap((column,index)=>{
    const measure=candidate.answer_contract.measurements?.[column];
    return measure?.role==='budget'&&measure.basis==='published_field_sum'&&measure.unit===plan.unit?[index]:[];
  });
  if(budgets.length!==1)return false;
  const [index]=budgets;
  const cell=rows[0][index];
  if(typeof cell!=='number'&&!(typeof cell==='string'&&/^[+-]?\d+(?:\.\d+)?$/.test(cell)))return false;
  // The repeated reference supplies no actual investment or other requested
  // quantity. Only the original plan's independently proven scope is delivered.
  return Number(cell)===amount&&columns.every((column,i)=>i===index||
    /^(?:(?:自然|统计|业务|项目|财政|预算)\s*)?(?:年份|年度|年)$/.test(column)&&String(rows[0][i])===plan.year);
}

function countsRowsWithoutAmount(candidate,observation){
  if(!sameComparisonRead(candidate,observation)||candidate.facts?.length
    ||candidate.answer_contract?.kind!=='sql_aggregate')return false;
  const {amount_column:amount,count_column:count,schema_version:version}=candidate.answer_contract.scalar_sum_count||{};
  const {columns,rows}=candidate.values;
  if(version!==1||typeof amount!=='string'||typeof count!=='string'||!count.trim()||amount===count
    ||columns?.length!==2||rows?.length!==1||rows[0]?.length!==2
    ||!columns.includes(amount)||!columns.includes(count))return false;
  const unit=candidate.analysis_options?.column_units?.[amount];
  if(!Object.hasOwn(moneyScale,unit)||candidate.answer_contract.measurements?.[count]
    ||rows[0][columns.indexOf(amount)]!==null)return false;
  const coverage=rows[0][columns.indexOf(count)];
  if(!Number.isSafeInteger(coverage)||coverage<0)return false;
  // Only treat the scalar COUNT as supporting coverage when the request does
  // not ask for a separate count or for a calculation about those same rows.
  const normalized=count.normalize('NFKC').replace(/\s+/g,'');
  const features=[...normalized.matchAll(/(?=(\p{Script=Han}{2}))/gu)].map(match=>match[1]);
  const independent=observation.plan.other_questions||[];
  return Array.isArray(independent)&&independent.every(question=>plain(question)&&/(?:率|比例)(?:是)?多少$/.test(question)
    &&features.length>0&&!features.some(feature=>question.normalize('NFKC').includes(feature)));
}

function listsUnmeasuredYears(candidate,observation){
  if(!sameComparisonScope(candidate,observation)||candidate.execution?.operation!=='data.sql_query'||!candidate.receipt.query_hash
    ||candidate.facts?.length||candidate.claims?.length||candidate.verified_claims?.length)return false;
  const contract=candidate.answer_contract;
  if(contract&&(contract.kind!=='sql_aggregate'||contract.result_ref!==candidate.result_ref||contract.complete!==true))return false;
  const {columns,rows}=candidate.values;
  const options=candidate.analysis_options;
  if(!Array.isArray(columns)||!Array.isArray(rows)||!rows.length||candidate.row_count!==rows.length
    ||!Array.isArray(options?.numeric_columns)||options.numeric_columns.length||!Array.isArray(candidate.period_coverage))return false;
  // A four-digit value remains a measure unless the engine identified this
  // exact column as a period and found no numeric measure in the typed read.
  const periods=new Map(candidate.period_coverage.filter(period=>year.test(period?.from)&&year.test(period?.to))
    .map(period=>[period.column,period]));
  if(!periods.size)return false;
  return rows.every(row=>Array.isArray(row)&&row.length===columns.length&&row.every((cell,index)=>{
    if(typeof cell==='number')return false;
    if(typeof cell!=='string'||!/^[+-]?\d+(?:\.\d+)?$/.test(cell))return true;
    const column=columns[index], period=periods.get(column);
    return period&&!options.column_units?.[column]&&year.test(cell)&&cell>=period.from&&cell<=period.to;
  }));
}

export function comparisonBudgetPartial(observation,formal=[]){
  const {plan,result}=observation;
  const comparableYears=[plan.year,plan.compared_year];
  const supporting=candidate=>{
    if(repeatsBudgetReference(candidate,observation)||countsRowsWithoutAmount(candidate,observation)
      ||listsUnmeasuredYears(candidate,observation))return true;
    if(!sameComparisonScope(candidate,observation))return false;
    const report=reportedSpendingObservation(candidate);
    return report&&!report.hasAmount&&[report.baseline,report.target].every(year=>comparableYears.includes(year));
  };
  if(otherMeasuredResult(formal.filter(candidate=>!supporting(candidate)),result))return null;
  const other=Array.isArray(plan.other_questions)?plan.other_questions.filter(plain).map(q=>q.replace(/(?:是)?多少$/,'')):[];
  const missing=plan.year+' 年和 '+plan.compared_year+' 年可比的实际投入金额本轮尚未核实，暂无法计算变化幅度。';
  return comparisonBudgetParagraph(observation)+'\n\n'+missing+(other.length?' '+other.join('、')+'本轮尚未核实。':'');
}

export function preferredBudgetPartial({plan,amount,result},formal){
  // Keep a distinct formal result on the normal delivery path.
  if(otherMeasuredResult(formal,result))return null;
  const formatted=amount.toLocaleString('zh-CN',{maximumFractionDigits:10});
  return plan.year+' 年按台账所列范围核实的**项目预算合计为 '+formatted+' '+plan.unit+'**。'
    +'\n\n'+plan.year+' 年和 '+plan.fallback_year+' 年的实际投入金额目前尚未核实，无法给出所问实际投入数值。';
}
