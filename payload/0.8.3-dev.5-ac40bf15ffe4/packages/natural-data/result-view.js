// Apply only the presentation fields returned by an authorized view mutation.
export function mergeViewPatch(current, patch) {
  if (!current || !patch || patch.result_ref !== current.result_ref || current.report) return null;
  const base = current.view_revision ?? 0;
  if (!Number.isSafeInteger(base) || patch.base_view_revision !== base
      || patch.view_revision !== base + 1 || typeof patch.view_ref !== 'string') return null;
  const {chart, requested_chart, view, visualization, view_revision, view_ref} = patch;
  if (!chart || typeof chart.type !== 'string' || chart.type !== view || visualization?.actual !== view) return null;
  return {...current, chart, requested_chart, view, visualization, view_revision, view_ref};
}

export function resultFrameMessage(previous, next, baseRevision) {
  const {result, tab} = next;
  if (!result) return null;
  if (!previous) return {type: 'natural-data:state', result, tab};
  if (previous.result !== result) {
    if (result.result_ref === previous.result.result_ref && baseRevision === (previous.result.view_revision ?? 0)) {
      const {result_ref, chart, requested_chart, view, visualization, view_revision, view_ref} = result;
      const patch = {result_ref, base_view_revision:baseRevision, chart, requested_chart,
                     view, visualization, view_revision, view_ref};
      return {type: 'natural-data:view', patch, tab};
    }
    return {type: 'natural-data:state', result, tab};
  }
  return previous.tab === tab ? null : {type: 'natural-data:tab', result_ref: result.result_ref, tab};
}

// 0.8.2: beyond this height a chat result card scrolls internally instead of
// stretching the conversation (customer feedback: “考虑增加侧边滚动查看”).
// The standalone viewer page is not affected; it does not use this cap.
export const MAX_RESULT_FRAME_HEIGHT = 760;

export function capResultFrameHeight(height) {
  return Math.min(Math.round(height), MAX_RESULT_FRAME_HEIGHT);
}

export function resultFrameHeight(message) {
  if (message?.type !== 'natural-data:height' || !Number.isFinite(message.height) || message.height <= 0) return null;
  return capResultFrameHeight(Math.max(72, Math.ceil(message.height)));
}

const FOREIGN_TITLE = /[\u0370-\u03FF\u0400-\u052F\u0600-\u06FF\u0E00-\u0E7F\u3040-\u30FF\uAC00-\uD7AF]{2,}/;

export function resultDisplayTitle(title, provenance) {
  if (typeof title !== 'string') return title;
  const cleaned = provenance?.synthetic===true ? title.replace(/（合成测试）$/, '') : title;
  return FOREIGN_TITLE.test(cleaned) && !/[\u4e00-\u9fff]/.test(cleaned) ? '数据查询结果' : cleaned;
}


const MONTH=/^(?:19|20)\d{2}-(?:0[1-9]|1[0-2])$/;

function metricMonthCoverage(result) {
  const metric=result?.metric_contract;
  const months=metric?.periods;
  const rows=result?.rows;
  if (result?.truncated || result?.status!=='succeeded' || result?.receipt?.read_only!==true
      || !Array.isArray(months) || months.length<2 || months.length>60 || !months.every(m=>MONTH.test(m))
      || new Set(months).size!==months.length || metric.scope?.dimensions?.length
      || !Array.isArray(rows) || rows.length!==result.row_count || result.columns?.length!==2
      || !result.columns.includes('period') || !result.columns.includes('value')) return null;
  const observed=new Map();
  for (const row of rows) {
    if (!months.includes(row.period) || observed.has(row.period)
        || (row.value!==null&&!Number.isFinite(row.value))) return null;
    observed.set(row.period,row.value);
  }
  const span=months.map(m=>Number(m.slice(0,4))*12+Number(m.slice(5))-1).sort((a,b)=>a-b);
  const length=span.at(-1)-span[0]+1;
  if (length>120) return null;
  const requested=Array.from({length},(_,i)=>{
    const month=span[0]+i;
    return Math.floor(month/12)+'-'+String(month%12+1).padStart(2,'0');
  });
  return {query_window:{state:'checked'},requested_months:requested,month_column:'period',count_column:'value',
    unreturned_months:requested.filter(month=>observed.get(month)==null)};
}

const monthlyCoverage=result=>result?.answer_contract?.monthly_sql_coverage||metricMonthCoverage(result);

export function missingMonthNotes(result) {
  const coverage=monthlyCoverage(result);
  if (result?.truncated || coverage?.query_window?.state!=='checked' || !Array.isArray(coverage.unreturned_months)) return [];
  const missing=coverage.unreturned_months.filter(month=>MONTH.test(month));
  const current=coverage.current_month_unclosed;
  const future=new Set(Array.isArray(coverage.future_months)?coverage.future_months:[]);
  const elapsed=missing.filter(month=>month!==current&&!future.has(month));
  const upcoming=missing.filter(month=>future.has(month));
  return [elapsed.length?`${elapsed.join('、')} 未返回观测。`:'',
          missing.includes(current)?`${current} 尚未结月，当前未返回观测。`:'',
          upcoming.length?`${upcoming.join('、')} 尚未到期。`:''].filter(Boolean);
}

// Line and area charts use calendar slots to break the series at missing months.
// The data tab and other charts use the original result rows.
export function monthlyDisplayResult(result, kind=result?.chart?.type) {
  const coverage=monthlyCoverage(result);
  if(!coverage)return result;
  const {rows,columns}=result;
  if(coverage.query_window?.state!=='checked'||result.truncated
      ||!['line','area'].includes(kind)
      ||!Array.isArray(rows)||!Array.isArray(columns)||columns.length!==2) return result;
  const {requested_months:months,unreturned_months:gaps,month_column:month,count_column:count}=coverage;
  if(!Array.isArray(months)||months.length<2||months.length>120||new Set(months).size!==months.length
      ||!Array.isArray(gaps)||!gaps.length||!columns.includes(month)||!columns.includes(count)||month===count) return result;
  const byMonth=new Map();
  const period=/^((?:19|20)\d{2}-(?:0[1-9]|1[0-2]))(?:-01(?:[ T]00:00:00(?:\.0+)?(?:Z|[+-]00(?::?00)?)?)?)?$/;
  for(const row of rows){
    const key=period.exec(String(row?.[month]))?.[1];
    if(!key||!months.includes(key)||byMonth.has(key)||gaps.includes(key)&&row[count]!=null) return result;
    byMonth.set(key,row);
  }
  if(months.some(key=>!period.test(key)||!byMonth.has(key)&&!gaps.includes(key))) return result;
  return {...result,rows:months.map(key=>byMonth.get(key)??{[month]:key,[count]:null})};
}
