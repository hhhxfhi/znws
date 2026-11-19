// A turn may fix two ordinary failures, try one exact catalog recipe, and
// make two read-only probes. Only the service executes and authorizes queries.
const SQL_TOOLS = new Set(['data_sql_query', 'data_plan_query', 'data_query']);
const FORMAL_QUERY_TOOLS = new Set(['data_sql_query', 'data_query', 'data_metric_query', 'data_report', 'data_derive_aggregation']);
const SQL_ERRORS = /ARGUMENT_SCHEMA:|SCHEMA_MISMATCH|SQL_SYNTAX|SQL_CORRELATION:|BUSINESS_SCOPE_CONFLICT:|DIMENSION_BUCKET_CONFLICT:|FORMULA_BASIS_MISMATCH|ENUM_VALUE_MISMATCH:|QUERY_EXECUTION|EXECUTION_FAILED|EXECUTION_ERROR|DUPLICATE_RESULT_COLUMNS|PUBLISHED_(?:GROUPING|DETAIL)_REQUIRED:|METRIC_SUBJECT:|HISTORICAL_SCOPE:|TIME_SCOPE_MISMATCH:|DATE_ROLE_MISMATCH:|EVENT_SCOPE_MISMATCH:|FORECAST_HISTORY_WINDOW:|FISCAL_HISTORY_UNOBSERVED:|NULL_VALUE_POLICY:|REPEATED_QUERY:/;
const BUDGET_ERRORS = /^(?:Error:\s*)?SQL_(?:REPAIR_LIMIT|REPAIR_DUPLICATE|PROBE_LIMIT):/;

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}

function argumentsOf(call) {
  const args = call?.arguments ?? call?.args;
  try { return typeof args === 'string' ? JSON.parse(args) : args; }
  catch { return null; }
}

function exploratory(name, args) {
  return name === 'data_sql_query' && args?.purpose === 'explore';
}

function signature(name, args) {
  if (!args) return null;
  const keys = name === 'data_query' ? ['task_id', 'task_revision', 'plan_ref']
    : ['source_id', 'sql', 'registered_query', 'parameters'];
  return JSON.stringify(canonical({name, ...Object.fromEntries(keys.map(key => [key, args[key]]))}));
}

function catalogQueries(source) {
  const grouping = source?.published_grouping_query;
  const detail = source?.detail_query;
  return [
    grouping?.kind === 'published_grouped_count' ? grouping.query_request : null,
    detail?.kind === 'published_detail_count' ? detail.query_request : null,
    source?.current_execution_guidance?.query_request,
    source?.preferred_budget_read?.query_request,
    source?.comparison_budget_read?.query_request,
    source?.annual_monthly_count_query?.query_request,
    source?.province_snapshot_count_query?.query_request,
    source?.service_completion_query?.query_request,
  ].filter(query => query?.source_id === source?.id && query?.purpose === 'answer'
    && typeof query.sql === 'string' && query.sql.trim());
}

function isCatalogQuery(name, args, recipes) {
  return name === 'data_sql_query' && args?.sql && !args.registered_query && !args.task_id
    && !args.replaces_result_ref && !args.scope_proposal?.length && !args.parameters?.length
    && (!args.purpose || args.purpose === 'answer')
    && recipes.get(args.source_id)?.has(args.sql);
}

function receiptOf(message) {
  const text = (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('\n');
  try { return {text, value: JSON.parse(text)}; }
  catch { return {text}; }
}

export function sqlTimeoutRetry(events, {requestRef, sessionId, callId, args} = {}) {
  const start = events.findLastIndex(event => event.type === 'turn/start');
  if (start < 0 || !requestRef || !sessionId) return null;
  const turn = events[start].data.turn;
  const end = callId ? events.findIndex((event, index) => index > start
    && event.type === 'tool/call' && event.data.callId === callId) : events.length;
  if (end < 0) return null;
  const current = events.slice(start + 1, end);
  const calls = current.filter(event => event.type === 'tool/call' && FORMAL_QUERY_TOOLS.has(event.data.name)
    && !['explore', 'probe'].includes(argumentsOf(event.data)?.purpose));
  const call = calls[0]?.data;
  if (calls.length !== 1 || call.name !== 'data_sql_query' || call.turn !== turn || !call.callId) return null;
  const original = argumentsOf(call);
  if (!original?.source_id || !(original.sql || original.registered_query)
      || original.task_id || original.replaces_result_ref || original.scope_proposal?.length) return null;
  if (args && JSON.stringify(canonical(original)) !== JSON.stringify(canonical(args))) return null;
  for (const event of current) {
    const message = event.data?.message;
    if (event.type !== 'tool/result' || event.data.turn !== turn
        || event.surfaceOp && event.surfaceOp !== 'append' || message?.isError
        || message?.source?.callId !== call.callId) continue;
    const {value} = receiptOf(message);
    const lineage = value?.result_lineage;
    if (value?.execution?.operation !== 'data.sql_query' || value.status !== 'failed' || value.reason_code !== 'TIMEOUT'
        || !/^[a-f0-9]{32}$/.test(value.result_ref || '') || lineage?.purpose !== 'answer'
        || lineage.request_ref !== requestRef || lineage.session_id !== sessionId
        || !lineage.config_fingerprint || lineage.source_id !== original.source_id) return null;
    if (current.some(later => later.seq > event.seq && later.type === 'tool/call'
        && ['ask_user_question', 'data_catalog'].includes(later.data.name))) return null;
    return {name: call.name, args: original, resultRef: value.result_ref};
  }
  return null;
}

export function sqlRepairState(events, {callId, buildId} = {}) {
  let start = events.findLastIndex(event => event.type === 'turn/start');
  if (start < 0) start = 0;
  const failures = [], calls = new Map(), recipes = new Map(), timeouts = new Set(), timeoutRetries = new Set();
  let attempts = 0, probes = 0, published = 0;
  for (const event of events.slice(start)) {
    if (event.type === 'tool/call') {
      const call = event.data;
      if (callId && call?.callId === callId) break;
      const args = argumentsOf(call);
      const probe = exploratory(call?.name, args);
      const reserve = attempts >= 3 && Boolean(isCatalogQuery(call?.name, args, recipes));
      if (call.name === 'data_sql_query' && !probe) {
        const key = signature(call.name, args);
        if (timeouts.has(key)) timeoutRetries.add(key);
      }
      calls.set(call?.callId, {name: call?.name, args, probe, reserve});
      if (probe) probes++;
      if (reserve) published++;
      continue;
    }
    if (event.type !== 'tool/result' || event.surfaceOp && event.surfaceOp !== 'append') continue;
    const message = event.data?.message;
    const call = calls.get(message?.source?.callId || message?.toolCallId);
    if (!call) continue;
    if (!SQL_TOOLS.has(call.name) && call.name !== 'data_catalog') continue;
    const {text, value} = receiptOf(message);
    if (call.name === 'data_catalog' && !message.isError && value?.catalog_ref
        && value.runtime_contract?.source === 'active-engine'
        && buildId && value.runtime_contract.build_id === buildId && Array.isArray(value.sources)) {
      for (const source of value.sources) {
        if (source?.id) recipes.set(source.id, new Set(catalogQueries(source).map(query => query.sql)));
      }
    }
    if (call.reserve && message.isError && BUDGET_ERRORS.test(text)) published--;
    if (call.name === 'data_sql_query' && !message.isError && value?.status === 'failed' && value.reason_code === 'TIMEOUT') {
      if (!call.probe) timeouts.add(signature(call.name, call.args));
      if (!call.probe && !call.reserve) attempts++;
      continue;
    }
    if (!SQL_TOOLS.has(call.name) || !SQL_ERRORS.test(text) || (!message.isError && value?.status !== 'failed')) continue;
    failures.push(signature(call.name, call.args));
    if (!call.probe && !call.reserve) attempts++;
  }
  return {attempts, failures, probes, published, recipes, timeoutRetries};
}

export function assertSqlRepairBudget(operation, args, events, scope) {
  const name = 'data_' + operation;
  if (!SQL_TOOLS.has(name)) return;
  const state = sqlRepairState(events, scope);
  const key = signature(name, args);
  if (state.timeoutRetries.has(key)) {
    throw Error('SQL_REPAIR_DUPLICATE: 相同查询已恢复重试过。有成功结果时直接沿用；否则保留已核实的事实并说明当前限制。');
  }
  if (state.failures.includes(key)) {
    throw Error('SQL_REPAIR_DUPLICATE: 相同 SQL 参数已经失败。可使用本轮目录中与原问匹配的正式查询，或交付已有事实并说明缺项。');
  }
  if (exploratory(name, args)) {
    if (state.probes < 2) return;
    throw Error('SQL_PROBE_LIMIT: 本轮已使用两次探索查询。可继续按授权目录执行正式只读查询，交付已可核实的结果并说明缺项。');
  }
  if (state.attempts >= 3) {
    if ((!scope?.originalSourceId || args?.source_id === scope.originalSourceId)
        && isCatalogQuery(name, args, state.recipes)) {
      if (state.published < 1) return;
      throw Error('SQL_REPAIR_LIMIT: 本轮已尝试一次已发布的查询。请交付已有事实并说明缺项。');
    }
    throw Error('SQL_REPAIR_LIMIT: 本轮普通 SQL 已尝试原查询及两次修正。可使用本轮目录中与原问匹配的正式查询，或交付已有事实并说明缺项。');
  }
}
