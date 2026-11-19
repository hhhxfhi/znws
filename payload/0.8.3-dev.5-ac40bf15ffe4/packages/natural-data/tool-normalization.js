// Session-catalog-aware tool argument normalization at the native agent boundary.
// Direct HTTP capability calls remain strict; within an active agent session that
// has already called data_catalog, unambiguous source/metric aliases and registered
// queries are normalized from the session's authorized catalog.
import {continuation, messageText, requestContext} from './request-context.js';
import {chartKindSupported, chartOnlyKind, explicitChartType, isExploratory} from './presentation-intent.js';
import {activeEvidence} from './result-lineage.js';
import {annualReportBudgetReference} from './forecast-evidence.js';
import {completedWithoutTools} from './host-evidence.js';
import {checkedPublishedDetail,checkedPublishedGrouping} from './published-grouping-answer.js';

const chartQueries=new Set(['metric_query','sql_query','plan','plan_query','semantic_plan']);
const queryReceipts=new Set(['data_query','data_sql_query','data_metric_query','data_report','data_derive_aggregation']);

export function normalizeAnalysisToolArgs(op, args) {
  const options = args?.options;
  if (op !== 'method' || !options || typeof options !== 'object' || Array.isArray(options)) return args;
  const misplaced = ['time_column', 'columns'].filter(name => Object.hasOwn(options, name));
  if (!misplaced.length || misplaced.some(name => Object.hasOwn(args, name))) return args;
  const next = {...args, options: {...options}};
  for (const name of misplaced) {
    next[name] = options[name];
    delete next.options[name];
  }
  return next;
}

function answerViews(events,{requests,users}){
  const ranges=[];
  for(const request of requests){
    const next=users.find(user=>user.seq>request.seq);
    ranges.push([request.seq,next?.seq??Infinity]);
    if(continuation(messageText(request))?.scopeChanged)break;
  }
  const calls=new Map(),results=new Map();
  for(const event of events){
    const range=ranges.find(([start,end])=>event.seq>start&&event.seq<end);
    if(!range)continue;
    if(event.type==='tool/call'){
      calls.set(event.data.callId,{name:event.data.name,range,turn:event.data.turn});
      continue;
    }
    if(event.type!=='tool/result'||(event.surfaceOp&&event.surfaceOp!=='append'))continue;
    const message=event.data?.message,call=calls.get(message?.source?.callId);
    if(!call||message.isError||call.range!==range||call.turn!==event.data.turn)continue;
    if(!queryReceipts.has(call.name)&&call.name!=='data_visualize')continue;
    let value;
    try{value=JSON.parse(message.content?.find(block=>block.type==='text')?.text||'{}');}
    catch{continue;}
    if(value?.status!=='succeeded'||!value.result_ref||isExploratory(value)
      ||value.validation?.period_grain?.state==='auxiliary')continue;
    const prior=results.get(value.result_ref);
    if(!prior&&call.name==='data_visualize')continue;
    results.set(value.result_ref,{...prior,...value});
  }
  return activeEvidence([...results.values()]);
}

export function currentAnswerRefs(events){
  const context=requestContext(events);
  if(!context.requests.length)return [];
  return answerViews(events,{...context,requests:context.requests.slice(0,1)})
    .filter(item=>item.validation?.period_grain?.state==='checked')
    .map(item=>item.result_ref).slice(-4);
}

export function currentExplorationRefs(events){
  const user=requestContext(events).users.at(-1);
  if(!user?.data.id)return [];
  const current=events.filter(event=>event.seq>user.seq&&event.data?.turn===user.data.turn);
  const calls=new Set(current.filter(event=>event.type==='tool/call'
    &&['data_sql_query','data_query'].includes(event.data.name)).map(event=>event.data.callId));
  const refs=new Set();
  for(const event of current){
    if(event.type!=='tool/result'||event.surfaceOp&&event.surfaceOp!=='append')continue;
    const message=event.data.message;
    if(message?.isError||!calls.has(message?.source?.callId))continue;
    try{
      const result=JSON.parse(message.content?.find(block=>block.type==='text')?.text||'{}');
      if(result.status==='succeeded'&&isExploratory(result)&&result.result_lineage?.request_ref===user.data.id
         &&/^[a-f0-9]{32}$/.test(result.result_ref||''))refs.add(result.result_ref);
    }catch{}
  }
  return [...refs].slice(-4);
}

const RANKING_PERIOD=/(?:本年度?|今年|当年|本季度|本月|当月|上年度?|去年|上季度|上月|20\d{2}年(?:\d{1,2}月)?)/;
const CATEGORY_RANKING=/^(.{0,32}?)(?:哪(?:一)?(?:种|类)|什么)(?:类型|类别)?(?:的)?(.{0,32}?)(?:最多|最少|最高|最低)$/;
const CONTEXT_REFERENCE=/刚才|上一轮|上一题|上个问题|上面|上述|前面|原口径|原范围|同口径|同一(?:个|类|批|组)|这(?:些|个|一项|类|组|批)|那(?:些|个|一项|类|组|批)|沿用|继续|追加|再按|换成|改成/;
const GROUPING_SCOPE_CHANGE=/取消|排除|不再|去掉|只看|仅看|对比|同比|环比|预测|另查/;

export function freshRankingCatalogTool(events,current){
  const {text,requests,users}=requestContext(events);
  const user=users.at(-1),previous=users.at(-2);
  if(!previous?.data.id||!user?.data.id||requests.length!==1||user.data.turn===previous.data.turn
    ||current.some(event=>event.type==='tool/call')||chartOnlyKind(text)||CONTEXT_REFERENCE.test(text)
    ||!RANKING_PERIOD.test(text))return null;
  const ranking=text.trim().replace(RANKING_PERIOD,'').replace(/\s/g,'')
    .replace(/^(?:(?:请)?帮我|请)?(?:查询|统计)?/,'').replace(/[？?。！!]$/,'').match(CATEGORY_RANKING);
  if(!ranking)return null;
  const subject=(ranking[1]+ranking[2]).replace(/(?:的)?(?:办理量|销量|数量|总量|总数|件数|笔数|金额)$/,'');
  if(!/^[\p{L}\p{N}]{2,32}$/u.test(subject))return null;
  const prior=answerViews(events,{requests:[previous],users})
    .filter(result=>result.result_lineage?.request_ref===previous.data.id);
  const completed=events.findLast(event=>event.type==='turn/end'&&event.data.turn===previous.data.turn&&event.seq<user.seq);
  if(!prior.length||completed?.data.reason?.kind!=='completed'
    ||[messageText(previous),...prior.map(result=>result.title)].some(value=>
      String(value||'').replace(/\s/g,'').includes(subject)))return null;
  // Use the current question to search the authorized project before the model
  // sees any guessed fields from the preceding result. The native call records
  // its receipt, so subsequent steps use it without another preflight.
  return {name:'data_catalog',args:{question:text}};
}

const RANK_WINDOW=/(?:本年度?|今年|当年度?|上年度?|去年|前年|(?:19|20)\d{2}\s*年)/;
const FIXED_RANK_WINDOW=/(?<!\d)20\d{2}\s*(?:年|[-/])\s*\d{1,2}\s*月?\s*(?:至|到|[-–—~～])\s*(?:20\d{2}\s*(?:年|[-/])\s*)?\d{1,2}\s*月(?!\s*\d)/;
const ONE_COUNT_RANK=/哪(?:一)?(?:个|类|种|项|些)[^。！？?!；;\n]{1,65}?(?:最[多高少低]|(?:排(?:在|名)?|位列|位居)(?:第[一1]|首位)|居首)[。！？?!\s]*$/;
const RANKING_COUNT=/数量|数目|件数|记录数|案卷数|批准量|办理量|多少/;
const RANKING_OTHER=/数据库|数据源|数据集|数据表|表名|库[里中]|改查|另外|另查|同时|分别|以及|预测|同比|环比|对比|比较|季度|半年|每个?月|逐月|各月|按月|本月|当月|上月|\d{1,2}\s*月|截至|截止|去重|占比|比例|平均|金额/;

export function rawDimensionRankingTool(events,current,sessionId){
  const {text,requests,users}=requestContext(events);
  const user=users.at(-1), normalized=text.normalize('NFKC').trim();
  const fixed=FIXED_RANK_WINDOW.test(normalized);
  if(!user?.data.id||!sessionId||requests.length!==1||!(fixed||RANK_WINDOW.test(normalized))
    ||!ONE_COUNT_RANK.test(normalized)||!RANKING_COUNT.test(normalized)
    ||RANKING_OTHER.test(fixed?normalized.replace(FIXED_RANK_WINDOW,''):normalized))return null;
  const calls=current.filter(event=>event.type==='tool/call'&&event.seq>user.seq);
  if(!calls.length)return {name:'data_catalog',args:{question:text}};
  if(calls.length!==1||calls[0].data.name!=='data_catalog')return null;
  const [source,...other]=extractCatalogSources(current,user.seq);
  const recipe=source?.raw_dimension_ranking_query, args=recipe?.query_request;
  const publication=source?.business_project;
  if(other.length||recipe?.schema_version!==1||recipe.request_ref!==user.data.id||recipe.quote!==text
    ||recipe.session_id!==sessionId||recipe.source_id!==source.id||args?.source_id!==source.id
    ||!publication?.id||publication.id!==recipe.publication?.id||publication.version!==recipe.publication.version
    ||args.purpose!=='answer'||!args.title||!args.sql)return null;
  const chart=explicitChartType(text);
  return {name:'data_sql_query',args:{...args,...(chart?{chart_type:chart}:{})}};
}

export function freshRepeatedSnapshotTool(events,current){
  const {text,requests,users}=requestContext(events);
  const [previous,user]=users.slice(-2);
  const normalized=value=>value.normalize('NFKC').replace(/[\s。！？!?]+$/,'');
  if(!previous?.data.id||!user?.data.id||requests.length!==1||user.data.turn===previous.data.turn
    ||current.some(event=>event.type==='tool/call')||CONTEXT_REFERENCE.test(text)
    ||!/(?:当前|现有|目前|现在|最新|截至(?:今日|今天))/.test(text)
    ||!/(?:统计|查询|有多少|有几)/.test(text)
    ||normalized(text)!==normalized(messageText(previous)))return null;
  const complete=events.some(event=>event.type==='turn/end'&&event.data.turn===previous.data.turn
    &&event.seq<user.seq&&event.data.reason?.kind==='completed');
  return complete?{name:'data_catalog',args:{question:text}}:null;
}

export function previousGroupingView(events){
  const {text,requests,users}=requestContext(events);
  const kind=chartOnlyKind(text),current=users.at(-1),previous=users.at(-2);
  if(!kind||requests.length!==1||!previous?.data.id||!current?.data.id)return null;
  const [view,...other]=answerViews(events,{requests:[previous],users});
  if(other.length||!checkedPublishedGrouping(view)||view.result_lineage.request_ref!==previous.data.id
    ||!Number.isInteger(view.view_revision)||view.view_revision<0||!chartKindSupported(view,kind))return null;
  const completed=events.findLast(event=>event.type==='turn/end'&&event.data.turn===previous.data.turn&&event.seq<current.seq);
  if(completed?.data.reason?.kind!=='completed')return null;
  return {result_ref:view.result_ref,kind,expected_revision:view.view_revision};
}

function repeatedGroupingClause(text,previous){
  const prefix=messageText(previous).normalize('NFKC').trim().replace(/[。!?]+$/,'').trimEnd();
  const current=text.normalize('NFKC').trim();
  if(!prefix||!current.startsWith(prefix))return false;
  return /^\s*[,。!?;:]\s*(?:按|按照|依照)\s*[\p{L}\p{N}]{2,28}?(?:的)?\s*(?:大类|分类|分组)\s*(?:重新)?\s*(?:分|分组|统计|汇总|归类)?\s*[。!?]?$/u.test(current.slice(prefix.length));
}

const MONTH_COUNT_REFINEMENT=/^(?:请\s*)?(?:帮我\s*)?(?:(?:只|仅)\s*)?(?:(?:计算|算|统计|汇总|查(?:询)?|看(?:一下)?|取)\s*)?(?:当前(?:的)?月(?:份)?|本月|当月|这个月)\s*(?:的|至今|到(?:目前|现在)为止)?\s*累[计积](?:值|量|数|件数|数量|记录数)?\s*(?:有?多少|即可|就行|呢)?[。!?]?$/;
const CUMULATIVE_COUNT_RANKING=/(?:当前|目前|现在|截至现在)(?:的)?\s*累[计积][^。!?;,，\n]{2,70}(?:里面|中|里)[，,]?\s*哪(?:一)?(?:个|类|种|项)[^。!?;,，\n]{2,40}(?:数量|件数|记录数|数目|数)\s*(?:最多|最高|最少|最低)(?:$|[。!?;,，])/;

export function publishedGroupingTool(events,current){
  const {text,requests,users}=requestContext(events);
  const [previous,user]=users.slice(-2);
  if(requests.length!==1)return null;
  const calls=current.filter(event=>event.type==='tool/call');
  if(!calls.length){
    const completed=previous?.data.id&&previous.data.turn+1===user?.data.turn&&events.some(event=>event.type==='turn/end'
      &&event.data.turn===previous.data.turn&&event.seq<user.seq&&event.data.reason?.kind==='completed');
    const normalized=text.normalize('NFKC').trim();
    const activeRank=CUMULATIVE_COUNT_RANKING.test(normalized)
      ||/^(?:请\s*)?(?:帮我\s*)?(?:按|按照|依照)\s*[\p{L}\p{N}]{2,28}?(?:的)?(?:大类|分类|分组)/u.test(normalized)
        &&CUMULATIVE_COUNT_RANKING.test(messageText(previous).normalize('NFKC').trim());
    if(completed&&(MONTH_COUNT_REFINEMENT.test(normalized)||activeRank&&!GROUPING_SCOPE_CHANGE.test(normalized))){
      return {name:'data_catalog',args:{question:text}};
    }
    if(!/(?:大类|分类|分组)/.test(text))return null;
    const listed=/[^、,，]{1,30}[、,，][^、,，]{1,30}[、,，]/u.test(text);
    const count=/统计|计算|数量|件数|条数|累计|(?:各|分别)(?:有)?多少/.test(text);
    if(listed&&count&&!GROUPING_SCOPE_CHANGE.test(text)){
      return {name:'data_catalog',args:{question:text}};
    }
    if(!previous?.data.id)return null;
    const regroup=/^\s*(?:请\s*)?(?:帮我\s*)?(?:按|按照|依照)\s*[\p{L}\p{N}]{2,28}?(?:的)?(?:大类|分类|分组)\s*重新(?:统计|汇总|归类|分组)\s*[。！!？?]?\s*$/u.test(text);
    const inheritedRegroup=/^(?:请\s*)?(?:帮我\s*)?(?:沿用|保持|保留)(?:刚才|上(?:一)?轮|原先|原来|原有)[^，,。]{0,60}[，,。]\s*(?:改|重新)?按/.test(normalized);
    if(completed&&(regroup||inheritedRegroup&&!GROUPING_SCOPE_CHANGE.test(text)||repeatedGroupingClause(text,previous))){
      // The engine checks the previous result and current published definition
      // even when the transcript compacted its earlier query receipt.
      return {name:'data_catalog',args:{question:text}};
    }
    const regrouping=/^(?:请\s*)?(?:帮我\s*)?(?:按|按照|依照)\s*[\p{L}\p{N}]{2,28}?(?:的)?(?:大类|分类|分组)\s*(?:重新)?(?:统计|汇总|归类|分组)\s*[。!?]?$/u;
    const candidate=text.normalize('NFKC');
    const clause=candidate.split(/[,。!?;:](?=\s*按)/).at(-1)?.trim();
    if(!GROUPING_SCOPE_CHANGE.test(text)&&regrouping.test(clause||'')
      &&/(?:当前|目前|现在)(?:的)?\s*累[计积].*(?:最多|最高|最少|最低)/.test(messageText(previous))
      &&completedWithoutTools(events,previous,user))return {name:'data_catalog',args:{question:text}};
    if(!/[、,，]/.test(text))return null;
    const [detail,...other]=answerViews(events,{requests:[previous],users});
    if(other.length||!checkedPublishedDetail(detail)||detail.result_lineage.request_ref!==previous.data.id)return null;
    return {name:'data_catalog',args:{source_id:detail.result_lineage.source_id}};
  }
  if(calls.length!==1||calls[0].data.name!=='data_catalog')return null;
  const [source,...others]=extractCatalogSources(current).filter(item=>item.published_grouping_query);
  if(!source||others.length)return null;
  const recipe=source.published_grouping_query;
  const args=recipe?.query_request;
  if(recipe?.kind!=='published_grouped_count'||args?.source_id!==source.id||args.purpose!=='answer'||!args.title||!args.sql)return null;
  const chart=explicitChartType(text);
  return {name:'data_sql_query',args:{source_id:source.id,sql:args.sql,title:args.title,purpose:'answer',...(chart?{chart_type:chart}:{})}};
}

export function publishedReportBudgetTool(events,current,session){
  const context=requestContext(events);
  const {requests,text}=context;
  if(requests.length!==1)return null;
  const calls=current.filter(event=>event.type==='tool/call');
  if(!calls.length&&/(?:投资|投入|预算)(?:总)?(?:金额|额)|(?:已支出|已使用)(?:资金|金额)/.test(text)){
    const currentYear=/本年度?|今年|当年度?/.test(text)&&/项目数量|项目数/.test(text);
    const annualTrend=/近\s*(?:五|5)年/.test(text)&&/趋势/.test(text)&&/预测/.test(text);
    if(currentYear||annualTrend)return {name:'data_catalog',args:{question:text}};
  }
  const [source,...others]=extractCatalogSources(current);
  const recipe=source?.published_budget_guidance;
  if(source?.annual_fiscal_history_query)return null;
  const args=recipe?.closed_budget_trend??recipe?.query_request;
  if(others.length||recipe?.kind!=='published_report_budget'||args?.source_id!==source.id
    ||args?.purpose!=='answer'||!args.title||!args.sql)return null;
  if(calls.length===1&&calls[0].data.name==='data_catalog')return {name:'data_sql_query',args};
  if(recipe.closed_budget_trend||!recipe.historical_reference)return null;
  const [primary,...additional]=answerViews(events,context);
  const reference=primary&&!additional.length&&annualReportBudgetReference(primary,recipe,text,requests[0].data.id,session);
  if(!reference||calls.some(call=>{
    if(call.data.name!=='data_sql_query')return false;
    let payload=call.data.arguments??call.data.args;
    if(typeof payload==='string'){try{payload=JSON.parse(payload);}catch{return false;}}
    return payload?.source_id===reference.source_id&&payload?.sql===reference.sql;
  }))return null;
  return {name:'data_sql_query',args:reference};
}

export function currentExecutionTool(events,current){
  const {text,requests}=requestContext(events);
  const acceptanceFollowup=/(?:该月|同月|这个月|上述月份)/.test(text)
    &&/已验收(?:项目)?(?:占比|比例)|项目验收率/.test(text);
  if(requests.length!==1||!text.includes('执行率')
    ||!acceptanceFollowup&&!/(?:本年度?|今年|当年|当前|最新|(?:19|20)\d{2}\s*年)/.test(text))return null;
  const calls=current.filter(event=>event.type==='tool/call');
  if(!calls.length)return {name:'data_catalog',args:{question:text}};
  if(calls.length!==1||calls[0].data.name!=='data_catalog')return null;
  const [source,...others]=extractCatalogSources(current);
  const recipe=source?.current_execution_guidance?.query_request;
  if(others.length||!recipe||recipe.source_id!==source.id||recipe.purpose!=='answer'||!recipe.title||!recipe.sql)return null;
  return {name:'data_sql_query',args:recipe};
}

function stripUnit(name) {
  return String(name || '').replace(/[（(][^）)]*[）)]/g, '').trim();
}

export function extractCatalogSources(events = [], sinceSeq = -1) {
  const callIds = new Set(
    events
      .filter(e => e.seq > sinceSeq && e.type === 'tool/call' && e.data?.name === 'data_catalog')
      .map(e => String(e.data.callId))
  );
  const byId = new Map();
  for (const e of events) {
    if (
      e.seq <= sinceSeq ||
      e.type !== 'tool/result' ||
      (e.surfaceOp && e.surfaceOp !== 'append') ||
      e.data?.message?.isError ||
      !callIds.has(String(e.data?.message?.source?.callId))
    ) {
      continue;
    }
    let parsed;
    try {
      parsed = JSON.parse(e.data.message.content?.find(x => x.type === 'text')?.text || '{}');
    } catch {
      continue;
    }
    for (const s of parsed.sources || []) {
      if (!s?.id) continue;
      const prev = byId.get(s.id) || { id: s.id, semantic_metrics: [], metric_definitions: [] };
      const mergedMetrics = new Map((prev.semantic_metrics || []).map(m => [m.id, m]));
      for (const m of s.semantic_metrics || []) {
        if (m?.id) mergedMetrics.set(m.id, { ...(mergedMetrics.get(m.id) || {}), ...m });
      }
      const mergedDefs = new Map((prev.metric_definitions || []).map(m => [m.id, m]));
      for (const m of s.metric_definitions || []) {
        if (m?.id) mergedDefs.set(m.id, { ...(mergedDefs.get(m.id) || {}), ...m });
      }
      byId.set(s.id, {
        ...prev,
        ...s,
        semantic_metrics: [...mergedMetrics.values()],
        metric_definitions: [...mergedDefs.values()],
        reconciliation_query: s.reconciliation_query || prev.reconciliation_query,
        registered_query: s.registered_query || prev.registered_query,
        confirmed_aliases: { ...(prev.confirmed_aliases || {}), ...(s.confirmed_aliases || {}) },
      });
    }
  }
  return [...byId.values()];
}

export function normalizeDataToolArgs(op, args, events = []) {
  if (!args || typeof args !== 'object') return args;
  const next = { ...args };
  // Native entry reads use the engine's bounded page size. A model-proposed
  // tiny slice splits even a short example over many extra model turns.
  if(op==='catalog'&&typeof next.entry_ref==='string'&&next.entry_ref.trim()
    &&Number.isInteger(next.entry_limit)&&next.entry_limit>0&&next.entry_limit<6000){
    delete next.entry_limit;
  }
  if(chartQueries.has(op)||op==='visualize'){
    const context=requestContext(events);
    const chart=explicitChartType(context.text);
    if(chart&&chartQueries.has(op)&&!isExploratory(next))next.chart_type=chart;
    if(chart&&op==='visualize'&&next.kind!==chart){
      const [target,...others]=answerViews(events,context);
      if(!others.length&&target?.result_ref===next.result_ref&&
         (target.view_revision===undefined||target.view_revision===next.expected_revision)&&chartKindSupported(target,chart)){
        next.kind=chart;
      }else if(chart==='table'&&context.requests[0]&&!continuation(messageText(context.requests[0]))){
        next.kind='table';
      }
    }
  }
  if (typeof next.source_id !== 'string' || !next.source_id.trim()) return next;
  const sessionSources = extractCatalogSources(events, -1);
  if (!sessionSources.length) return next;

  // 1. Normalize source_id when the model passes a non-canonical alias after data_catalog
  if (typeof next.source_id === 'string' && next.source_id.trim()) {
    const rawSid = next.source_id.trim();
    const knownIds = new Set(sessionSources.map(s => s.id));
    if (knownIds.has(rawSid)) {
      next.source_id = rawSid;
    } else {
      // Catalog pages are partial. An unseen ID may be another authorized source;
      // neither a metric match nor a one-source page permits substituting it.
      const matches = sessionSources.filter(s => [s.name, stripUnit(s.name), ...(s.aliases || [])]
        .some(alias => typeof alias === 'string' && alias.trim() === rawSid));
      if (matches.length === 1) next.source_id = matches[0].id;
    }
  }

  const targetSource = sessionSources.find(s => s.id === next.source_id);

  // 2. Normalize metric_id on metric_query / semantic_plan when given a unique Chinese name/alias/code
  if ((op === 'metric_query' || op === 'semantic_plan') && targetSource && typeof next.metric_id === 'string' && next.metric_id.trim()) {
    const rawMid = next.metric_id.trim();
    const metrics = targetSource.semantic_metrics || [];
    if (!metrics.some(m => m.id === rawMid)) {
      if (targetSource.confirmed_aliases?.[rawMid]) {
        next.metric_id = targetSource.confirmed_aliases[rawMid];
      } else {
        const exactName = metrics.filter(m =>
          String(m.name || '').trim() === rawMid ||
          stripUnit(m.name) === rawMid ||
          String(m.code || '').trim() === rawMid
        );
        if (exactName.length === 1) {
          next.metric_id = exactName[0].id;
        } else if (exactName.length === 0) {
          const byAlias = metrics.filter(m => Array.isArray(m.aliases) && m.aliases.some(a => String(a || '').trim() === rawMid));
          if (byAlias.length === 1) {
            next.metric_id = byAlias[0].id;
          }
        }
      }
    }
  }

  // A template is an explicit alternative, never a replacement for model SQL.
  // Rewriting here discarded fixes learned from real field values and changed
  // the requested population before the server could validate the query.

  return next;
}
