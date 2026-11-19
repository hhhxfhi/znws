// A configured query period is not proof that a business value exists. Keep
// availability, missing data and measure definitions bound to actual receipts.
import {messageText} from './request-context.js';

const plain=value=>String(value||'').replaceAll('**','');
const numeric=value=>value!==null&&value!==''&&value!==undefined&&Number.isFinite(Number(value));
const negated=value=>/(?:不能|不可|不应|不得|不代表|并非|不是|不等于|未能|无法确认|尚未核实)/.test(value);
const EVENT_RANK=/^(?:请(?:帮我)?|帮我|想知道|本年度|今年|本年|上年度|去年|本月|上月|20\d{2}年|[\s，,])*(?:哪个|哪一类|哪类|哪种)(?:的)?[\p{Script=Han}]{2,16}?的(?<event>[\p{Script=Han}]{2,12}?)(?:案卷|记录|业务)?(?:数量|件数|次数|数)(?:最多|最少|最高|最低)[。？！?!]?$/u;
const EVENT_ACTION=/注销|撤销|吊销|作废|撤回|退回|报废|停用|终止|到期|变更|换发|办结|受理|登记|签发|批准/;
const HOLDING_RANK=/^(?:(?:请问|请(?:帮我)?(?:查(?:询)?|统计))\s*)?(?:哪(?:一)?个(?:地市|城市)|哪(?:一)?市)的[^，。！？；\n]{0,24}(?:作业证|从业证|执业证|操作证|上岗证)[^，。！？；\n]{0,10}(?:持有率|持证率)(?:最[高低]|排[在名]第[一1])[。！？!?]?$/u;

export function eventDimensionCatalogTool(user,current){
  const question=messageText(user).trim(),match=EVENT_RANK.exec(question);
  if(!user?.data?.id||!match||!EVENT_ACTION.test(match.groups.event)
      ||/或者|还是|分别|以及|另外|顺便|同时|、|先问|选择|确认/.test(question)
      ||current.some(event=>event.type==='tool/call'))return null;
  return {name:'data_catalog',args:{question}};
}

export function holdingRateCatalogTool(user,current){
  const question=messageText(user).trim();
  if(!user?.data?.id||!HOLDING_RANK.test(question)||current.some(event=>event.type==='tool/call'))return null;
  return {name:'data_catalog',args:{question}};
}

const rankClaim='(?:最高|最低|第一(?:名|位)?|第1(?:名|位)?|首位|居首|榜首|垫底|最后一(?:名|位))';
const provinceRank=new RegExp('(?:全省|省内|全域)[^。；;\n]{0,20}'+rankClaim);
const cityRank=new RegExp('[\\p{Script=Han}]{2,10}市(?:的)?[^。；;\n]{0,18}'+rankClaim
  +'|'+rankClaim+'(?:的(?:是|为)|为|是|：|:)\\s*[\\p{Script=Han}]{2,10}市'
  +'|(?:名列|位列|位居|排名)\\s*(?:全省|省内|各地市|各市)?\\s*'+rankClaim,'u');
const rankUncertain=/无法|不能|未能|尚未|不宜|是否|若|如果|不足以|暂不|有待|不具备|缺少|需要|未核实/;

function holdingProof(item){
  const proof=item?.validation?.published_holding_rate,lineage=item?.result_lineage;
  if(item?.status==='succeeded'&&!item.truncated&&item.values?.complete===true
      &&lineage?.purpose==='answer'&&item.receipt?.read_only===true&&proof?.schema_version===1
      &&proof.result_ref===item.result_ref&&proof.request_ref&&proof.request_ref===lineage.request_ref
      &&proof.source_id&&proof.source_id===lineage.source_id&&proof.source_id===item.receipt.source_id
      &&proof.project_id&&proof.project_id===item.business_project?.id&&proof.project_version===item.business_project.version
      &&Array.isArray(proof.cities)&&Array.isArray(item.values.rows)
      &&item.values.rows.length===proof.cities.length)return proof;
  return null;
}

function holdingRateRoute(user,events){
  if(!user?.data.id||user.data.turn==null)return null;
  const calls=new Set();
  let catalog=null;
  for(const event of events){
    if(event.seq<=user.seq||event.data?.turn!==user.data.turn||(event.surfaceOp&&event.surfaceOp!=='append'))continue;
    if(event.type==='tool/call'&&event.data.name==='data_catalog')calls.add(event.data.callId);
    if(event.type!=='tool/result')continue;
    const message=event.data.message;
    if(message?.isError||message?.source?.kind!=='tool'||!calls.has(message.source.callId))continue;
    const content=message.content?.find(block=>block.type==='text');
    try{
      const value=JSON.parse(content?.text);
      if(value.status==='succeeded'&&value.execution?.operation==='data.catalog')catalog=value;
    }catch{}
  }
  if(catalog?.sources?.length!==1||!catalog.project||catalog.project!==catalog.execution_scope||
    catalog.catalog_view?.next_source_offset!=null)return null;
  const [source]=catalog.sources;
  const plan=source.qualification_scope?.holding_rate;
  const query=plan?.query_request;
  const publication=source.business_project??source.source_context?.business_project;
  const revision=source.connection_revision??source.source_context?.connection_revision;
  const snapshot=plan?.mode==='published_city_snapshot'&&plan.comparison_eligible===true&&plan.cities?.length>=2;
  const single=plan?.mode==='registered_single_city'&&plan.comparison_eligible===false&&plan.cities?.length===1;
  if(source.dimension_business_choice||source.independent_metric_comparison||!query||
    plan.schema_version!==1||!snapshot&&!single||
    plan.request_ref!==user.data.id||!source.id||plan.source_id!==source.id||query.source_id!==source.id||
    !plan.publication?.id||!Number.isSafeInteger(plan.publication.version)||plan.publication.version<1||
    (publication&&(publication.id!==plan.publication.id||publication.version!==plan.publication.version))||
    !Number.isSafeInteger(plan.connection_revision)||plan.connection_revision<0||
    (revision!=null&&revision!==plan.connection_revision)||
    !/^\d{4}-\d\d-\d\d$/.test(plan.snapshot_day)||!Array.isArray(plan.cities)||plan.cities.length>100||
    !plan.cities.every(city=>typeof city==='string'&&/^[\p{Script=Han}]{2,10}市$/u.test(city))||
    query.purpose!=='answer'||typeof query.title!=='string'||!query.title||typeof query.sql!=='string'||!query.sql)return null;
  const coverage=single&&plan.coverage_request;
  if(coverage&&(coverage.source_id!==source.id||coverage.purpose!=='answer'||coverage.chart_type!=='table'
    ||typeof coverage.sql!=='string'||!coverage.sql||typeof coverage.title!=='string'||!coverage.title))return null;
  return {plan,query,coverage,scope:catalog.execution_scope};
}

function holdingQueries(user,events){
  return events.filter(event=>event.seq>user.seq&&event.data?.turn===user.data.turn&&event.type==='tool/call'
    &&(!event.surfaceOp||event.surfaceOp==='append')
    &&/^(?:ask_user_question|data_(?:sql_query|query|metric_query|report|derive_aggregation))$/.test(event.data.name));
}

function sameHoldingQuery(call,query){
  if(!query||call?.data.name!=='data_sql_query')return false;
  let args=call.data.arguments??call.data.args;
  try{if(typeof args==='string')args=JSON.parse(args);}catch{return false;}
  return args?.source_id===query.source_id&&args?.sql===query.sql&&args.purpose==='answer';
}

export function registeredHoldingRateTool(user,events,tools){
  if(!user?.data.id||!tools?.some(tool=>tool.name==='data_sql_query'))return null;
  const route=holdingRateRoute(user,events);
  if(!route)return null;
  const calls=holdingQueries(user,events);
  if(!calls.length)return {name:'data_sql_query',args:route.query};
  if(calls.length===1&&route.coverage&&sameHoldingQuery(calls[0],route.query))
    return {name:'data_sql_query',args:route.coverage};
  return null;
}

function holdingCoverage(result,route,session,requestRef){
  const proof=result.validation?.published_holding_rate, lineage=result.result_lineage;
  if(proof?.mode!=='registered_city_coverage'||proof.comparison_eligible!==false
    ||proof.result_ref!==result.result_ref||proof.request_ref!==requestRef||lineage?.request_ref!==requestRef
    ||lineage?.session_id!==session||lineage.purpose!=='answer'||lineage.source_id!==route.plan.source_id
    ||proof.source_id!==lineage.source_id||result.receipt?.source_id!==lineage.source_id
    ||proof.sample_city!==route.plan.cities[0]||proof.snapshot_day!==route.plan.snapshot_day
    ||proof.project_id!==route.plan.publication.id||proof.project_version!==route.plan.publication.version
    ||result.business_project?.id!==proof.project_id||result.business_project.version!==proof.project_version
    ||(result.source_provenance?.connection_revision??0)!==route.plan.connection_revision
    ||result.execution_scope!==route.scope||result.execution?.operation!=='data.sql_query'
    ||result.status!=='succeeded'||result.truncated||result.receipt.read_only!==true||result.values?.complete!==true
    ||JSON.stringify(result.values.columns)!==JSON.stringify(['有审核记录的地市'])
    ||!Array.isArray(proof.cities)||!proof.cities.length||proof.cities.length>100
    ||!proof.cities.every(city=>/^[\p{Script=Han}]{2,10}市$/u.test(city)
      &&result.values.rows?.some(row=>String(row[0]).trim()===city)))return null;
  return proof.cities;
}

export function registeredHoldingRateAnswer(user,events,evidence,session){
  const route=holdingRateRoute(user,events);
  if(!route||!session)return null;
  const {plan,query,coverage,scope}=route;
  const calls=holdingQueries(user,events);
  if(calls.length!==(coverage?2:1)||!sameHoldingQuery(calls[0],query)
    ||coverage&&!sameHoldingQuery(calls[1],coverage))return null;
  const receipts=evidence.filter(item=>{
    const proof=holdingProof(item);
    return item?.execution_scope===scope&&item.execution?.operation==='data.sql_query'&&
      item.result_lineage?.session_id===session&&proof?.request_ref===user.data.id&&proof.source_id===plan.source_id&&
      proof.project_id===plan.publication.id&&proof.project_version===plan.publication.version&&
      (item.source_provenance?.connection_revision??0)===plan.connection_revision&&
      proof.mode===plan.mode&&proof.snapshot_day===plan.snapshot_day&&holdingRateColumns(plan,proof,item.values);
  });
  if(receipts.length!==1)return null;
  if(plan.mode==='published_city_snapshot')return holdingSnapshotText(receipts[0],messageText(user));
  const row=receipts[0].values.rows[0],value=row.at(-1);
  if(!numeric(value)||Number(value)<0||Number(value)>100)return null;
  let components='';
  if(row.length===3){
    const [issued,people]=row;
    if(!Number.isSafeInteger(issued)||!Number.isSafeInteger(people)||people<=0||issued<0||issued>people
      ||Math.abs(Number(value)-issued*100/people)>0.005001)return null;
    components=` 计算用到 ${issued} 份有效作业证和 ${people} 名审核通过的技术人员。按人员去重的持证人数需要证书关联的人员标识。`;
  }
  const [year,month,day]=plan.snapshot_day.split('-').map(Number);
  const order=messageText(user).includes('最低')?'最低':'最高';
  const city=plan.cities[0];
  if(coverage){
    const seen=evidence.map(result=>holdingCoverage(result,route,session,user.data.id)).filter(Boolean);
    const coverageText=seen.length===1?`本轮查到${seen[0].join('、')}有审核人员记录。`:'本轮尝试核对其他地市的审核人员记录。';
    return `目前无法判断哪个地市${order}。${coverageText} 仍缺统一统计日的人员状态、同一人去重及地市归属证据。\n\n`
      +`按登记示例，${city}${year}年${month}月${day}日的证件记录数与审核人员记录数之比为 **${Number(value)}%**。${components}`;
  }
  return `按登记示例查询，${city}${year}年${month}月${day}日的作业证持有率为 **${Number(value)}%**。${components}\n\n`
    +`目前无法判断哪个地市${order}。还需其他地市同一统计日的持证分子、审核人员分母，以及一致的人员去重规则和地市归属。`;
}

function holdingRateColumns(plan,proof,values){
  if(plan.mode==='published_city_snapshot'){
    const expected=[...plan.cities].sort();
    if(JSON.stringify(expected)!==JSON.stringify(proof.expected_cities)
      ||JSON.stringify(proof.missing_cities)!==JSON.stringify(expected.filter(city=>!proof.cities.includes(city)))
      ||proof.comparison_eligible!==(proof.cities.length>=2))return false;
    return JSON.stringify(values.columns)===JSON.stringify(['地市','审核通过人数','持证人数',proof.rate_column])
      &&new Set(values.rows.map(row=>row[0])).size===proof.cities.length
      &&values.rows.every(([city,people,held,rate])=>proof.cities.includes(city)&&Number.isSafeInteger(people)&&people>0
        &&Number.isSafeInteger(held)&&held>=0&&held<=people&&typeof rate==='number'&&Math.abs(rate-held*100/people)<.005001);
  }
  if(proof.comparison_eligible!==false||proof.cities[0]!==plan.cities[0])return false;
  const components=plan.component_columns??[];
  if(!Array.isArray(components)||components.length!==0&&components.length!==2)return false;
  const columns=[...components,proof.rate_column];
  return components.every((label,index)=>proof.component_columns?.[index]===label)
    &&values.columns?.length===columns.length&&columns.every((column,index)=>values.columns[index]===column)
    &&values.rows[0]?.length===columns.length;
}

function holdingSnapshotText(result,question){
  const proof=holdingProof(result);
  const lowest=/最低/.test(question),label=lowest?'最低':'最高';
  const sign=lowest?1:-1;
  const rows=[...result.values.rows].sort((a,b)=>sign*(a[3]-b[3])||a[0].localeCompare(b[0],'zh-CN'));
  const best=rows[0][3],leaders=rows.filter(row=>row[3]===best).map(row=>row[0]);
  const [year,month,day]=proof.snapshot_day.split('-').map(Number);
  const intro=`${year}年${month}月${day}日核实了 ${rows.length} 个地市的作业证持有率。`;
  const rank=rows.length>=2?`在上述可比地市中，${leaders.join('、')}持有率${label}，为 **${best}%**。`
    :`当前只有 ${leaders[0]} 可核实，跨市${label}暂无法判断。`;
  const table=['| 地市 | 审核通过人数 | 持证人数 | 持有率 |','| --- | ---: | ---: | ---: |',
    ...rows.map(([city,people,held,rate])=>`| ${city} | ${people} | ${held} | ${rate}% |`)].join('\n');
  const missing=proof.missing_cities.length?`\n\n${proof.missing_cities.join('、')}缺少可核实的同日人员或持证状态。`:'';
  return intro+rank+'\n\n'+table+missing;
}

export function holdingRateBoundaryIssues(text,evidence,question){
  const rate=/(?:作业证|从业证|执业证|操作证|上岗证)[^，。！？；\n]{0,10}(?:持有率|持证率)/;
  if(!rate.test(question)||!/地市|城市|各市|分市|跨市|全省|排行|排名|最高|最低|比较|对比/.test(question))return [];
  const body=plain(text);
  const unknownZero=/(?:地市|地区)(?:未注明|未知|不详|未填|未归属)|未知(?:地市|地区)/;
  const zero=/(?:^|[^0-9.])0(?:\.0+)?\s*[％%]|(?:率|为|是)\s*零/;
  if(body.split(/[。；;\n]/).some(line=>unknownZero.test(line)&&zero.test(line)
      &&!/不能|不可|不应|留空|未知状态/.test(line)))
    return ['未注明地区或缺失的分子无法核实，请保留未知，不要填为 0% 或纳入排行。'];
  const proofs=evidence.map(holdingProof).filter(Boolean);
  const matching=proofs.filter(proof=>proof.mode==='published_city_snapshot'&&proof.comparison_eligible===true&&proof.cities.length>=2);
  const knownCities=[...new Set(proofs.flatMap(proof=>proof.cities)
    .filter(city=>typeof city==='string'&&/^[\p{Script=Han}]{2,10}市$/u.test(city))
    .map(city=>city.slice(0,-1)))];
  const shortCityRank=knownCities.length?new RegExp('(?:'+knownCities.join('|')+')\\s*(?:的)?'
    +'(?:(?:持证率|持有率)\\s*)?(?:为|是|排名)?\\s*'+rankClaim+'|'+rankClaim
    +'(?:的(?:是|为)|为|是|：|:)\\s*(?:'+knownCities.join('|')+')(?!市)'):null;
  const clauses=body.split(/[。；;\n，,]|但是?|然而|不过/);
  const claims=clauses.filter(clause=>!rankUncertain.test(clause));
  const provinceClaim=claims.some(clause=>provinceRank.test(clause));
  const definite=provinceClaim||claims.some(clause=>cityRank.test(clause)||shortCityRank?.test(clause));
  if(definite&&!matching.length)return ['本轮没有可比地市的同一时点持证率回执。可以交付已核实的单城结果与统计日，跨市最高或最低暂无法核实。'];
  if(provinceClaim&&!matching.some(proof=>proof.province_completeness==='verified'))
    return ['已核实的排行仅适用于本轮可比地市，请写明范围，避免扩展为全省。'];
  if(!matching.length&&proofs.some(proof=>proof.mode==='registered_single_city'&&proof.comparison_eligible===false)
      &&/最高|最低|排行|排名/.test(question)&&body.trim()
      &&!clauses.some(clause=>rankUncertain.test(clause)&&/最高|最低|第一|排行|排名|比较|对比/.test(clause)))
    return ['交付缺项：先给已核实的单城持证率和统计日，再明确说明缺少同一时点、同口径的各地市数据，目前无法判断排名。'];
  return [];
}

function observedIn(receipt,year){
  const periods=[...(receipt.metric_contract?.periods||[]),...(receipt.execution_scope?.periods||[])];
  for(const fact of receipt.facts||[]){
    if(numeric(fact.value)&&(!year||String(fact.dimensions?.period||periods[0]||'').startsWith(year)))return true;
  }
  if(year&&!periods.some(p=>String(p).startsWith(year)))return false;
  return receipt.values?.rows?.some(row=>row.some(numeric))||false;
}

export function evidenceBoundaryIssues(text,evidence){
  const body=plain(text),issues=[];
  // A total immediately introducing a complete table is an enumeration claim,
  // including "共18家" without repeating the noun. Do not treat an explicitly
  // partial/top-N list as the entire population.
  const table=/((?:^|\n)[^\n]*(?:共|总计|合计|查到|找到)\s*(\d+)\s*(?:家|个|条|人)[^\n]*\n)\s*(\|[^\n]+\|\n\|[ :|\-]+\|\n(?:\|[^\n]+\|(?:\n|$))+)/g;
  for(const m of body.matchAll(table)){
    if(/其中|部分|前\s*\d|节选|示例|抽样|仅列/.test(m[1]))continue;
    const listed=m[3].trim().split('\n').slice(2).filter(row=>{
      const label=row.split('|')[1]?.trim();
      return !/^(?:合计|小计|总计|汇总|总数)$/.test(label);
    }).length;
    if(listed!==Number(m[2]))issues.push(`正文声明共${m[2]}项，但完整表格列出${listed}项。请按完整查询结果核对数量与名单；确为部分展示时明确写出范围，不补造记录。`);
  }
  const receipts=evidence.filter(e=>e.result_ref&&e.purpose!=='explore');
  const nullMeasure=receipts.some(e=>(e.facts||[]).some(f=>f.value===null)||
    e.metric_contract&&e.values?.rows?.some(row=>row.some(v=>v===null)));
  const missing=/(?:字段|数据|数值)[^。；;\n]{0,20}(?:均|全|都)[^。；;\n]{0,10}(?:为空|空值|缺失)|所有[^。；;\n]{0,35}(?:为空|空值|缺失)/.test(body);
  if(nullMeasure&&missing){
    for(const line of body.split(/[。；;\n]/)){
      if(/发布|映射|配置|登记/.test(line)&&/(?:即可|就能|就可|便可)[^。]{0,14}(?:查到|得到|获得|计算)/.test(line)
          &&!negated(line)&&!/(?:补齐|补录|接入|修复)[^。]{0,14}(?:数据|数值|字段|记录)/.test(line)){
        issues.push('数据已实查为空，修改映射或重新发布不能保证产生数值。请说明需核实数据来源并补齐实际数据；仅完成配置不代表指标已可计算。');break;
      }
    }
  }
  for(const line of body.split(/[。；;\n，,]|但(?:是)?|然而|不过/)){
    const match=line.match(/(?:年度值|月度值|指标值|业务数据|业务数值)(?:理论上|应当|应该|必然|肯定)?(?:存在|有值)/);
    if(!match||negated(line))continue;
    const year=[...line.slice(0,match.index).matchAll(/20\d{2}/g)].at(-1)?.[0];
    if(!receipts.some(e=>observedIn(e,year))){
      issues.push('目录覆盖期只表示登记的查询范围，不能证明该业务期间存在数值。本轮没有对应的非空查询证据，请删除“年度值存在”等推断，说明尚未核实；保留已查到的空结果和实际粒度限制。');break;
    }
  }
  const additions=receipts.filter(e=>/新增/.test(e.metric_contract?.definition||''));
  const netDefined=additions.some(e=>/新增[^，。；]{0,16}(?:定义为|等于|采用)[^，。；]{0,12}净增|新增[^，。；]{0,16}(?:减去|扣除)/.test(e.metric_contract.definition));
  if(additions.length&&!netDefined&&body.split(/[。；;\n]/).some(line=>
      /新增[^。；;\n]{0,30}(?:是|为|指|即|等于)[^。；;\n]{0,15}净增/.test(line)&&!negated(line)))
    issues.push('登记口径是新增数量，未定义为净增。不要把新增改写为扣除减少后的净增，也不能用两期累计差替代；按原指标名称和已核算数值交付。');
  return issues;
}

export function snapshotRegionCoverageIssues(text,evidence){
  const scoped=evidence.filter(item=>{
    const proof=item?.validation?.snapshot_region_coverage,lineage=item?.result_lineage;
    return item?.status==='succeeded'&&!item.truncated&&item.purpose!=='explore'&&item.purpose!=='probe'
      &&proof?.schema_version===1&&proof.result_ref===item.result_ref&&lineage?.purpose==='answer'
      &&proof.request_ref&&proof.request_ref===lineage.request_ref&&proof.source_id===lineage.source_id
      &&proof.population==='authorized_snapshot'&&proof.province_completeness==='unverified';
  });
  if(scoped.length!==1)return [];
  const proof=scoped[0].validation.snapshot_region_coverage,body=plain(text);
  const lines=body.split(/[。；;\n]/);
  const issues=[];
  const assertsProvince=lines.some(line=>/全省(?:合计|共有|总计|总数|范围内共有|全部)|覆盖全省|全省(?:仅|只)(?:有|包含)/.test(line)
    &&!/快照|已查询|已核实|授权|当前数据|无法|尚未/.test(line));
  if(assertsProvince)issues.push('本轮仅核实了授权资质快照，请写清统计范围，省域总体完整性尚未核实。');
  if(Number.isSafeInteger(proof.unlocated_count)&&proof.unlocated_count>0){
    const regionBreakdown=(proof.regions||[]).some(region=>body.includes(region))&&/\d/.test(body);
    if(regionBreakdown&&!/(?:地区|地市|城市)(?:未注明|未知|为空|缺失)|未填写(?:地区|地市)/.test(body))
      issues.push('已核实的地区分布包含 '+proof.unlocated_count+' 家地区未注明的单位，列出地区分布时请单列。');
    if(lines.some(line=>/(?:地区|地市)(?:未注明|未知|为空|缺失)[^。，,\n]{0,15}(?:归入|并入|归为|属于)[^。，,\n]{0,14}市/.test(line)
      &&!/不能|不得|不应|未归|未并/.test(line)))
      issues.push('地区未注明的单位应单列，请删除无依据的城市归属。');
  }
  return issues;
}

// The existence of fine labels with a shared prefix is not an approved mapping.
// A parent count requires a verified grouped result or a confirmed derivation.
export function regroupingIssues(text,evidence,question){
  if(!/大类|重新归类|合并分组|重分组|粗分/.test(question))return [];
  const receipts=evidence.filter(e=>e?.result_ref&&e.status==='succeeded'&&e.purpose!=='explore'
    &&e.result_lineage?.purpose!=='probe'&&!e.truncated);
  const raw=receipts.filter(e=>e.metric_contract?.additive===true&&Array.isArray(e.facts)
    &&e.facts.length===e.row_count&&e.facts.length>1);
  if(raw.length!==1)return [];
  const [fine]=raw,labels=new Map(),known=new Set();
  for(const fact of fine.facts){
    for(const label of Object.values(fact.dimensions||{})){
      if(typeof label!=='string')continue;
      known.add(label);
      const match=/^([\p{L}]{2,28})\s*[-—–－]\s*(\S.{0,54})$/u.exec(label);
      if(!match)continue;
      const members=labels.get(match[1])||new Set();
      members.add(match[2]);labels.set(match[1],members);
    }
  }
  const proven=receipts.filter(e=>e!==fine&&(
    e.answer_contract?.published_grouping?.result_ref===e.result_ref||
    e.metric_contract?.derived===true&&e.result_lineage?.kind==='derived_aggregation'&&e.result_lineage.source_result_ref===fine.result_ref));
  const lines=plain(text).split(/[。；;\n]/);
  for(const [label,members] of labels){
    if(members.size<2||known.has(label)||proven.some(e=>e.values?.rows?.some(row=>row.includes(label))
      ||e.facts?.some(f=>Object.values(f.dimensions||{}).includes(label))))continue;
    const escaped=label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const claim=new RegExp('(?:^|[|、，,：:\\s])'+escaped+'(?:\\s*\\|\\s*\\d+|\\s*(?:为|共|合计|数量|：|:)?\\s*\\d+\\s*(?:件|条|项|个|家|次|台|笔|宗|张))');
    const mention=lines.some(line=>!negated(line)&&!/待(?:用户)?确认|建议归类|拟归并/.test(line)&&claim.test(line));
    if(mention)return ['原查询返回细分类，尚无对应的大类统计回执。已发布的分类用同范围正式查询；自定义分组先核对并取得用户确认，再用派生汇总结果交付数量和图表。'];
  }
  return [];
}
