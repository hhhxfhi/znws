import {calculationNotes} from '../calculation-presentation.js';
import {forecastMethodPresentation} from '../forecast-presentation.js';
type Grain = 'year' | 'quarter' | 'month' | 'week' | 'day';
type Interval = [number, number];
type ForecastPoint = {period: string; value: number; interval: Interval};

export type ForecastView = {
  id: string;
  model: string;
  methodExplanation: string;
  grain: Grain;
  history: {period: string; value: number}[];
  predictions: ForecastPoint[];
  unit: string | null;
  openYear?: string;
  measureLabel?: string;
  minimumHistory: number;
  backtestPoints: number;
  mae: number;
  rmse: number;
  sampleNote: string;
  steps: number;
  publicationLevel: 'formal' | 'reference_only';
  basis: string;
  candidates: {model: string; mae: number}[];
  excluded: {model: string; reason: string}[];
  horizon: {horizon_steps: number; mae: number; rolling_origins: number} | null;
};

const grains = new Set(['year', 'quarter', 'month', 'week', 'day']);
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const dayMs = 86_400_000;

function periodIndex(period: unknown, grain: Grain, fromRow = false): number | undefined {
  if (fromRow && grain === 'year' && Number.isInteger(period)) period = String(period);
  if (typeof period !== 'string') return undefined;
  const day = /^([0-9]{4})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;
  if (grain === 'week' || grain === 'day' || (grain === 'month' && fromRow && day.test(period))) {
    if (!day.test(period)) return undefined;
    const stamp = Date.parse(period + 'T00:00:00Z');
    if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 10) !== period) return undefined;
    if (grain !== 'month') return stamp / dayMs;
    period = period.slice(0, 7);
  }
  if (grain === 'year') return /^\d{4}$/.test(period) ? Number(period) : undefined;
  if (grain === 'quarter') {
    const part = /^(\d{4})-Q([1-4])$/.exec(period);
    return part ? Number(part[1]) * 4 + Number(part[2]) - 1 : undefined;
  }
  const part = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(period);
  return part ? Number(part[1]) * 12 + Number(part[2]) - 1 : undefined;
}

export function isForecastClaim(claim: any): boolean {
  return claim?.operation === 'matrix_method' && (
    claim.method_id === 'matrix.42' ||
    (typeof claim.method_id === 'string' && claim.method_id.startsWith('custom.')
      && claim.statistics?.kind === 'forecast')
  );
}

export function scopedResultClaims(result: any, embedded: boolean) {
  const claims = Array.isArray(result.verified_claims)
    ? result.verified_claims.filter((claim: unknown) => claim && typeof claim === 'object' && !Array.isArray(claim))
    : [];
  if (!embedded) return claims;
  const allowed = new Set(Array.isArray(result.forecast_claim_ids) ? result.forecast_claim_ids : []);
  return claims.filter((claim: any) => !isForecastClaim(claim) || allowed.has(claim.claim_id));
}

const standardForecastNotes = [
  '仅描述当前完整结果，不证明因果或总体覆盖。',
  '预测区间受模型假设约束，不是保证区间；回测表现不能保证未来准确。',
  '自动选择只比较本次可用模型，回测和最终拟合采用相同设定。',
  '计数显示值取约数，预测区间向外取整并限制为非负；原拟合值及回测保留。',
];

export function scopedResultLimitations(result: any, claims: any[], embedded: boolean, forecasts: ForecastView[] = []) {
  const allClaimNotes: string[] = calculationNotes(result.claim_limitations);
  const allAnswerNotes: string[] = calculationNotes(result.answer_contract?.limitations);
  const visible = new Set(claims.map(claim => claim.claim_id));
  const hiddenForecast = embedded && Array.isArray(result.verified_claims)
    && result.verified_claims.some((claim: any) => isForecastClaim(claim) && !visible.has(claim.claim_id));
  const detailNotes = new Set([
    ...(hiddenForecast ? allClaimNotes : []),
    ...(forecasts.length ? [...standardForecastNotes, ...forecasts.map(forecast => forecast.sampleNote)] : []),
  ]);
  return {
    claimLimitations: allClaimNotes.filter(text => !detailNotes.has(text)),
    answerLimitations: allAnswerNotes.filter(text => !detailNotes.has(text)),
  };
}

// 0.8.2: a forecast card leads with the forecast section — the history is
// already plotted there. The raw training chart stays available but collapsed,
// unless a chart type was explicitly chosen for this view.
export function trainingChartCollapsed(result: any, forecastCount: number): boolean {
  if (forecastCount <= 0 || result?.report) return false;
  return (result?.view_revision ?? 0) <= 0 && !result?.visualization?.requested;
}

// 0.8.2: long verified-claim lists stay readable — the first few conclusions
// stay visible, the rest fold behind a summary line (customer feedback Q4:
// “已核算结论部分内容全部展示，内容过长”).
export function splitClaimNotes(texts: unknown, visibleLimit = 3): {visible: string[]; folded: string[]} {
  const list = (Array.isArray(texts) ? texts : []).filter((text): text is string =>
    typeof text === 'string' && text.trim().length > 0);
  return {visible: list.slice(0, visibleLimit), folded: list.slice(visibleLimit)};
}

export function forecastView(result: any, claim: any): ForecastView | null {
  if (!isForecastClaim(claim) || typeof claim.claim_id !== 'string'
      || !result.result_ref || result.status !== 'succeeded' || result.truncated) return null;
  const s = claim.statistics;
  if (!s || s.schema_version !== 1 || s.source_result_ref !== result.result_ref || !grains.has(s.grain)) return null;
  const model = forecastMethodPresentation(s.model);
  if (!model || !['numeric', 'nonnegative_discrete_count'].includes(s.value_kind)
      || (s.unit !== null && (typeof s.unit !== 'string' || !s.unit.trim()))) return null;
  const historyPeriods = s.history?.periods, historyValues = s.history?.values;
  const {forecast_periods: futurePeriods, steps, history_count: count} = s;
  const displayValues = s.display?.predicted, displayIntervals = s.display?.interval_95;
  const minimum = s.grain === 'year' ? 5 : 12;
  if (!Array.isArray(historyPeriods) || !Array.isArray(historyValues) || count < minimum
      || count !== historyPeriods.length || count !== historyValues.length
      || !Number.isInteger(steps) || steps < 1 || steps > 24
      || ![futurePeriods, displayValues, displayIntervals, s.predicted, s.interval_95]
        .every(list => Array.isArray(list) && list.length === steps)
      || s.history_start !== historyPeriods[0] || s.origin !== historyPeriods.at(-1)
      || s.target_period !== futurePeriods.at(-1)) return null;

  const periods = [...historyPeriods, ...futurePeriods];
  const step = s.grain === 'week' ? 7 : 1;
  const indexes = periods.map(period => periodIndex(period, s.grain));
  if (indexes.some((index, i) => index === undefined || (i > 0 && index !== indexes[i - 1]! + step))) return null;
  if (!Array.isArray(result.rows) || result.row_count !== count || result.rows.length !== count
      || !Array.isArray(result.columns) || typeof s.time_column !== 'string' || typeof s.value_column !== 'string'
      || s.time_column === s.value_column || !result.columns.includes(s.time_column)
      || !result.columns.includes(s.value_column)) return null;

  const sourceValues = new Map<number, number>();
  for (const row of result.rows) {
    const index = periodIndex(row?.[s.time_column], s.grain, true);
    const value = row?.[s.value_column];
    if (index === undefined || !finite(value) || sourceValues.has(index)) return null;
    sourceValues.set(index, value);
  }
  const countKind = s.value_kind === 'nonnegative_discrete_count';
  const isCount = (value: number) => Number.isInteger(value) && value >= 0;
  const validInterval = (interval: unknown, value: number): interval is Interval =>
    Array.isArray(interval) && interval.length === 2 && interval.every(finite)
      && interval[0] <= value && value <= interval[1];

  const history = [];
  for (let i = 0; i < count; i++) {
    const value = historyValues[i];
    if (!finite(value) || sourceValues.get(indexes[i]!) !== value || (countKind && !isCount(value))) return null;
    history.push({period: historyPeriods[i], value});
  }
  const predictions = [];
  for (let i = 0; i < steps; i++) {
    const value = displayValues[i], raw = s.predicted[i];
    const interval = displayIntervals[i], rawInterval = s.interval_95[i];
    if (!finite(value) || !finite(raw) || !validInterval(interval, value) || !validInterval(rawInterval, raw)) return null;
    if (countKind) {
      if (![value, ...interval].every(isCount)) return null;
    } else if (value !== raw || interval[0] !== rawInterval[0] || interval[1] !== rawInterval[1]) {
      return null;
    }
    predictions.push({period: futurePeriods[i], value, interval});
  }

  const backtest = s.backtest, limits = s.sample_limits;
  if (!backtest || backtest.model !== s.model || backtest.same_model_specification !== true
      || !finite(backtest.mae) || backtest.mae < 0 || !finite(backtest.rmse) || backtest.rmse < 0
      || !Number.isInteger(limits?.minimum_history) || limits.minimum_history < minimum || limits.minimum_history > count
      || !Number.isInteger(limits.holdout_points) || limits.holdout_points < 1 || limits.holdout_points >= count
      || limits.holdout_points !== backtest.rolling_origins) return null;
  const level = s.publication_level === undefined ? 'formal' : s.publication_level;
  if (level !== 'formal' && level !== 'reference_only') return null;
  const horizon = s.horizon_backtest ?? null;
  if (horizon !== null && (horizon.horizon_steps !== s.steps || !finite(horizon.mae) || horizon.mae < 0
      || !Number.isInteger(horizon.rolling_origins) || horizon.rolling_origins < 1)) return null;
  return {id: claim.claim_id, model: model.name, methodExplanation: model.explanation,
          grain: s.grain, history, predictions, unit: s.unit,
          openYear: s.grain === 'year' && futurePeriods.includes(s.open_year_skipped) ? s.open_year_skipped : undefined,
          measureLabel: typeof s.measure_label === 'string' ? s.measure_label : undefined,
          minimumHistory: limits.minimum_history, backtestPoints: limits.holdout_points,
          mae: backtest.mae, rmse: backtest.rmse, sampleNote: typeof limits.note === 'string' ? limits.note : '',
          steps, publicationLevel: level, basis: typeof s.interval_basis === 'string' ? s.interval_basis : '',
          candidates: Array.isArray(s.selection?.candidates) ? s.selection.candidates : [],
          excluded: Array.isArray(s.selection?.excluded) ? s.selection.excluded : [],
          horizon};
}
