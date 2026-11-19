import { IconDataOutlineMedium as BarChartOutlined, IconWarningTriangleOutlineMedium as WarningOutlined } from '@natural-work/harness-ui';
import {
  AlertRule,
  CanonicalResult,
  ChartSpec,
  FieldRef,
  PivotLayout,
  PivotTotalsConfig,
  columnLabelOf,
} from './contract';
import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { displayDimension, formatVerifiedPeriod, isPeriodGrain, selectAxisTicks, measureTextWidth } from './chart-axis';

const SERIES_COLORS = [
  '#2563eb',
  '#0f9f94',
  '#f59e0b',
  '#ef4444',
  '#8b5cf6',
  '#10b981',
  '#f43f5e',
  '#64748b',
  '#0891b2',
  '#d97706',
];

const fieldName = (ref?: FieldRef) => ref?.field ?? '';
const fieldLabel = (ref?: FieldRef) => ref?.label ?? ref?.field ?? '';
const yFields = (encoding: ChartSpec['encoding']) =>
  Array.isArray(encoding.y) ? encoding.y : encoding.y ? [encoding.y] : [];

const numericValue = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return undefined;
};

const formatNumber = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
const formatMeasure = (value: number, ref?: FieldRef) => formatNumber(value) + (ref?.unit ?? '');

/** Render all retained categories; the viewport scrolls instead of dropping rows. */
const MAX_CATEGORIES = Number.POSITIVE_INFINITY;
/** Keep every retained series; crowded results use a scrollable viewport. */
const MAX_SERIES = Number.POSITIVE_INFINITY;

/**
 * 维度值归一化。写入与读取必须共用这一个函数：热力图曾经写入时用原始值拼键
 * （null 变成字符串 "null"）、读取时用 String(v ?? '')，null 维度的格子永远读成 0。
 */
const dimensionLabel = (value: unknown) => String(value ?? '');

const truncateLabel = (label: string, limit = 12) =>
  label.length > limit ? `${label.slice(0, Math.max(limit - 1, 1))}…` : label;

// 时间/序数维度不能按数值重排：按月统计一旦降序就画成 5月/8月/1月，与下方 Data 表格顺序不一致。
const ORDERED_FIELD_HINT = /(date|time|day|week|month|quarter|year|period|dt|ym|时间|日期|日|周|月|季|年|期)/i;
const ORDERED_VALUE_HINT =
  /^(\d{4}([-/年]\d{1,2}([-/月]\d{1,2}日?)?)?|\d{1,2}\s*(月|日|周|季度?)|q[1-4]|第\s*\d+\s*(周|月|季度?|天))$/i;

const isOrderedDimension = (ref: FieldRef | undefined, labels: string[]) => {
  if (isPeriodGrain(ref?.grain)) return true;
  if (ORDERED_FIELD_HINT.test(`${ref?.field ?? ''} ${ref?.label ?? ''}`)) return true;
  const named = labels.map(label => label.trim()).filter(Boolean);
  return named.length > 0 && named.every(label => ORDERED_VALUE_HINT.test(label));
};

interface SignedScale {
  /** 数据里存在负值时以 0 为轴心双向绘制。 */
  signed: boolean;
  allZero: boolean;
  /** 0 轴在轨道上的位置（横向从左起算、竖向从下起算）。 */
  zeroPct: number;
  /** 条形起点（横向 left / 竖向 bottom）。 */
  startPct: (value: number) => number;
  /** 条形长度，0 值长度为 0，不再被兜底成 2%。 */
  lengthPct: (value: number) => number;
}

/**
 * 以 0 为轴心构造共享标尺。旧实现 `Math.max(...values, 1)` 在全负数据上恒等于 1，
 * 所有负值都被 `Math.max(pct, 2)` 压成 2%，方向和大小全部丢失。
 */
const buildSignedScale = (values: number[]): SignedScale => {
  const finite = values.filter(value => Number.isFinite(value));
  const maxPositive = Math.max(0, ...finite);
  const maxNegative = Math.max(0, ...finite.map(value => -value));
  const signed = maxNegative > 0;
  const allZero = maxPositive === 0 && maxNegative === 0;
  const span = maxPositive + maxNegative || 1;
  const zeroPct = (maxNegative / span) * 100;
  const lengthPct = (value: number) => {
    if (!Number.isFinite(value) || value === 0) return 0;
    return Math.max((Math.abs(value) / span) * 100, 0.8);
  };
  return {
    signed,
    allZero,
    zeroPct,
    lengthPct,
    startPct: (value: number) => (value < 0 ? Math.max(zeroPct - lengthPct(value), 0) : zeroPct),
  };
};

/**
 * Round a raw maximum up to a readable axis bound (1/2/2.5/5 x 10^n).
 * The gauge used to hard-code a 0-100 scale, so any metric outside that range -
 * revenue in yuan, area in square metres - pinned the needle at full deflection
 * and mislabelled the axis as a percentage.
 */
const niceCeiling = (value: number) => {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
};

// Only a genuine ratio should be drawn as a 0-100% gauge.
const isPercentageMetric = (metric: { field: string; label?: string; unit?: string }) => {
  const hint = `${metric.field} ${metric.label ?? ''} ${metric.unit ?? ''}`;
  return /(pct|percent|ratio|(?:^|_)rate(?:\b|_)|率|占比|百分比|完成度)/i.test(hint) || (metric.unit ?? '').includes('%');
};

// 0.6.2: an empty metric value says "无法计算" with the engine's reason, never a blank cell.
const nullReasonOf = (result: CanonicalResult, row: Record<string, unknown>): string | undefined =>
  ((result as any).null_reasons as Array<{ dimensions?: Record<string, unknown>; reason?: string }> | undefined)
    ?.find(item => Object.entries(item.dimensions || {}).every(([key, value]) => String(row[key]) === String(value)))?.reason;

const displayValue = (value: unknown) => {
  if(typeof value==='number'&&Number.isFinite(value))return formatNumber(value);
  // PG can return decimal values as strings. Preserve identifiers, leading zeros,
  // integer strings and full precision in CSV; format decimal display only.
  if(typeof value==='string'&&/^-?(?:0|[1-9]\d*)\.\d+$/.test(value)&&Number.isFinite(Number(value)))return formatNumber(Number(value));
  return String(value??'');
};

interface RowValue {
  label: string;
  value: number;
  values: number[];
}

function extractSeries(
  result: CanonicalResult,
  encoding: ChartSpec['encoding'],
): { rows: RowValue[]; metrics: FieldRef[] } {
  const source = result.rows ?? [];
  const metrics = yFields(encoding);
  const xField = fieldName(encoding.x);
  if (!metrics.length) return { rows: [], metrics };
  const rows: RowValue[] = source.map(row => ({
    label: xField ? dimensionLabel(row[xField]) : '',
    // Missing observations stay unknown, including pre-aggregated report cells.
    // Grouped bars already use NaN to represent a missing cell without drawing it.
    value: numericValue(row[metrics[0].field]) ?? Number.NaN,
    values: metrics.map(metric => numericValue(row[metric.field]) ?? Number.NaN),
  }));
  return { rows, metrics };
}

/** 长表 → 宽表：按 (x 分类 × series 维度 × 度量) 铺成矩阵，供分组/堆叠柱状图渲染。
 *
 *  `extractSeries` 只按**度量**分系列，`encoding.series` 此前无人消费——两个分类维
 *  的结果（区县 × 标志类型）会被画成 x 轴标签重复的一长串柱子，第二个维度整个丢掉
 *  （2026-09-17 真机）。这里把 series 维度提升成系列，兑现后端两分类维分支把它放进
 *  encoding.series 的契约：
 *
 *  - 单度量：系列 = series 维度的各个取值（最常见，区县 × 标志类型）。
 *  - 多度量：系列 = series 取值 × 度量，图例写作「取值 · 度量」。后端 4 分支会在
 *    两分类维且多度量时同时给 series 和多个 y，两边都必须在图上占一席。
 *
 *  没有 series 维度时保持既有行为（度量即系列、每个原始行一个分类），不改动
 *  单分类维图的既有观感。 */
function extractSeriesGroups(
  result: CanonicalResult,
  encoding: ChartSpec['encoding'],
): { rows: RowValue[]; metrics: FieldRef[]; hiddenSeries: number; seriesMetric: string[] } {
  const metrics = yFields(encoding);
  const xField = fieldName(encoding.x);
  const seriesField = fieldName(encoding.series);
  // 没有 series 维度（或无法按它分组）时完全保持既有行为。
  const asBefore = () => ({
    rows: extractSeries(result, encoding).rows,
    metrics,
    hiddenSeries: 0,
    // 无 series 维度时每个绘制系列就是它自己的度量。
    seriesMetric: metrics.map(metric => metric.field),
  });
  if (!xField || !seriesField || !metrics.length) return asBefore();

  // 单元格键把 series 取值与度量绑在一起，避免多度量下同名的两个度量互相覆盖。
  const cellKey = (seriesValue: string, metricField: string) =>
    `${seriesValue}\u0001${metricField}`;
  const categories: string[] = [];
  const seriesValues: string[] = [];
  const seenCategories = new Set<string>();
  const seenSeries = new Set<string>();
  const matrix = new Map<string, Map<string, number>>();
  for (const row of result.rows ?? []) {
    const category = dimensionLabel(row[xField]);
    const seriesValue = dimensionLabel(row[seriesField]);
    if (!seenCategories.has(category)) {
      seenCategories.add(category);
      categories.push(category);
    }
    if (!seenSeries.has(seriesValue)) {
      seenSeries.add(seriesValue);
      seriesValues.push(seriesValue);
    }
    const bucket = matrix.get(category) ?? new Map<string, number>();
    // 聚合组合必须唯一；重复时显示未知，避免擅自相加比率或重复统计。
    for (const metric of metrics) {
      const key = cellKey(seriesValue, metric.field);
      const value = numericValue(row[metric.field]);
      // Duplicate aggregate cells are ambiguous; never sum ratios or unknowns.
      bucket.set(key, bucket.has(key) ? Number.NaN : value ?? Number.NaN);
    }
    matrix.set(category, bucket);
  }
  if (!categories.length) return asBefore();

  const singleSeries = seriesValues.length === 1 && metrics.length === 1;
  // 每条绘制系列都记住它背后的**度量字段**：多度量同图时量纲不同，归一化分母必须
  // 按度量隔离（同一度量的不同 series 取值共用同一分母，跨分类才可比）。
  const allSeries = seriesValues.flatMap(seriesValue =>
    metrics.map(metric => ({
      ref: {
        field: cellKey(seriesValue, metric.field),
        // series 维度只有一个取值时它不构成分组，图例用度量名读起来更自然。
        label: singleSeries
          ? metric.label || metric.field
          : metrics.length === 1
            ? seriesValue
            : `${seriesValue} · ${metric.label || metric.field}`,
      } as FieldRef,
      metricField: metric.field,
    })),
  );
  const visibleSeries = allSeries.slice(0, MAX_SERIES);
  return {
    rows: categories.map(category => {
      const values = visibleSeries.map(
        series => matrix.get(category)?.get(series.ref.field) ?? Number.NaN,
      );
      return { label: category, value: values[0] ?? 0, values };
    }),
    metrics: visibleSeries.map(series => series.ref),
    hiddenSeries: allSeries.length - visibleSeries.length,
    seriesMetric: visibleSeries.map(series => series.metricField),
  };
}

function KpiView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const row = result.rows?.[0] ?? {};
  const metrics = yFields(encoding);
  const xField = fieldName(encoding.x);
  return (
    <div className='product-kpi-grid'>
      {metrics.map(metric => {
        const value = row[metric.field];
        const number = numericValue(value);
        return (
          <div className='product-kpi-card' key={metric.field}>
            <span className='product-kpi-label'>
              {xField ? `${String(row[xField] ?? '')} · ` : ''}
              {metric.label || metric.field}
            </span>
            <strong className='product-kpi-value'>
              {number === undefined ? displayValue(value) : formatNumber(number)}
              {metric.unit && <small>{metric.unit}</small>}
            </strong>
          </div>
        );
      })}
    </div>
  );
}

function BarView({
  result,
  encoding,
  vertical = false,
  scalar = false,
}: {
  result: CanonicalResult;
  encoding: ChartSpec['encoding'];
  vertical?: boolean;
  scalar?: boolean;
}) {
  const { rows: series, metrics } = extractSeries(result, encoding);
  const rows = series.filter(row => Number.isFinite(row.value));
  const metric = metrics[0];
  // 时间/序数维度保持原始顺序，其余类目才按数值降序排名。
  const ordered = isOrderedDimension(
    encoding.x,
    rows.map(row => row.label),
  );
  const arranged = ordered ? rows : rows.sort((a, b) => b.value - a.value);
  const visible = scalar
    ? arranged.map(row=>({...row,label:row.label||metric?.label||metric?.field||''}))
    : arranged.slice(0, MAX_CATEGORIES);
  const scale = buildSignedScale(visible.map(row => row.value));
  if (!visible.length || !metric) return <EmptyVisual note='没有可绘制的分类数据' />;

  const hasDimension = scalar||Boolean(fieldName(encoding.x));
  const notes: string[] = [];
  if (arranged.length > MAX_CATEGORIES) {
    notes.push(
      `共 ${arranged.length} 个分类，图中${ordered ? '按原始顺序' : '按数值降序'}展示前 ${MAX_CATEGORIES} 个，其余未绘制；完整数据请查看「查看数据」。`,
    );
  }
  if (scale.signed) notes.push('灰线表示 0，红色柱表示负值。');
  if (rows.length < series.length && (result as any).reason_code !== 'UNKNOWN'
    && !(result as any).answer_contract?.monthly_sql_coverage?.unreturned_months?.length) {
    notes.push('部分分类缺少数值，详见数据表。');
  }
  if (scale.allZero) notes.push('已绘制的数值均为 0。');

  // 竖向柱状图必须用 height 表达数值：旧实现只 inline 了 width，
  // 而 CSS 把竖向变体的 height 钉死成 100%，所有柱子都撑满轨道、完全无法比较。
  const barStyle = (value: number): React.CSSProperties => {
    const length = scale.lengthPct(value);
    const color = value < 0 ? '#ef4444' : SERIES_COLORS[0];
    if (vertical) {
      return scale.signed
        ? { bottom: `${scale.startPct(value)}%`, height: `${length}%`, background: color }
        : { height: `${length}%`, width: '100%', background: color };
    }
    return scale.signed
      ? { left: `${scale.startPct(value)}%`, width: `${length}%`, background: color }
      : { width: `${length}%`, background: color };
  };

  return (
    <div className='product-bar-wrap' style={{overflowX:'auto',maxHeight:520,overflowY:'auto'}}> 
      <div
        className={vertical ? 'product-column-chart' : 'product-bar-chart'}
        style={vertical?{minWidth:Math.max(300,visible.length*64)}:undefined}
        aria-label={`${metric.label || metric.field}${vertical ? '柱状图' : '条形图'}`}
      >
        {visible.map((row, index) => {
          const category = hasDimension ? displayDimension(row.label, encoding.x) : String(index + 1);
          const formatted = formatNumber(row.value);
          const label = <span className='product-series-label' title={hasDimension ? category : undefined}>{category}</span>;
          const value = <strong className='product-series-value'>
            {formatted}
            {metric.unit && <small>{metric.unit}</small>}
          </strong>;
          return (
            <div className={vertical ? 'product-column-row' : 'product-bar-row'} key={`${row.label}-${index}`}>
              {vertical ? value : label}
              <div className={`product-series-track${scale.signed ? ' product-series-track--signed' : ''}`}
                title={`${category}: ${formatted}${metric.unit || ''}`}>
                {scale.signed && (
                  <span
                    className='product-series-zero'
                    style={vertical ? { bottom: `${scale.zeroPct}%` } : { left: `${scale.zeroPct}%` }}
                    aria-hidden
                  />
                )}
                <i style={barStyle(row.value)} />
              </div>
              {vertical ? label : value}
            </div>
          );
        })}
      </div>
      {notes.map(note => (
        <p className='product-visual-note' key={note}>
          {note}
        </p>
      ))}
    </div>
  );
}

function GroupedBarView({
  result,
  encoding,
  stacked = false,
  normalized = false,
}: {
  result: CanonicalResult;
  encoding: ChartSpec['encoding'];
  stacked?: boolean;
  normalized?: boolean;
}) {
  const { rows, metrics, hiddenSeries, seriesMetric } = extractSeriesGroups(result, encoding);
  const limited = rows.slice(0, MAX_CATEGORIES);
  // 堆叠图按「最大行合计」归一化：各段之和就是整行长度，必须共用同一个合计分母。
  const stackMaximum = Math.max(
    ...limited.map(row => row.values.reduce((sum, value) => sum + (Number.isFinite(value) ? Math.max(value, 0) : 0), 0)),
    1,
  );
  // 分组图的分母按**度量**（y 字段）隔离，而不是按绘制系列逐条归一。
  // 多度量同图时各度量量纲不同（真机实测产出数量 ~200 件 vs 覆盖面积 ~1311 平方公里），
  // 共用一个全局最大值会把小量纲压成看不见的细条；但若按「绘制系列」逐条归一，同一
  // 度量的不同 series 取值（基础/专业）会各自撑到 100%，跨分类比较立刻失真。
  // 正确口径：**同一度量的所有系列共用该度量的最大值**，不同度量各用自己的
  // ——同度量内可比长短，跨度量不可比（图下披露）。单度量时退化为旧的全局最大值，
  // 观感完全不变。
  const metricFields = Array.from(new Set(seriesMetric));
  const metricMaxima = new Map<string, number>();
  metricFields.forEach(metricField => {
    let best = 0;
    seriesMetric.forEach((candidate, index) => {
      if (candidate !== metricField) return;
      for (const row of limited) if (Number.isFinite(row.values[index])) best = Math.max(best, row.values[index], 0);
    });
    metricMaxima.set(metricField, best || 1);
  });
  const denominatorOf = (index: number) => metricMaxima.get(seriesMetric[index]) ?? 1;
  const negatives = limited.reduce(
    (count, row) => count + row.values.filter(value => Number.isFinite(value) && value < 0).length,
    0,
  );
  if (!limited.length || !metrics.length) return <EmptyVisual note='没有可绘制的多系列数据' />;

  const hasDimension = Boolean(fieldName(encoding.x));
  // `.product-grouped-bars` 段间有 2px gap，总宽必须为它们留出位置。
  const gapAllowance = metrics.length > 1 ? (2 * (metrics.length - 1)) / metrics.length : 0;
  const notes: string[] = [];
  if (!stacked && !normalized && metricFields.length > 1) {
    notes.push('每个度量按各自最大值等比绘制：同一度量内可比长短，不同度量之间不可直接比较。');
  }
  if (rows.length > MAX_CATEGORIES) {
    notes.push(`共 ${rows.length} 个分类，图中仅展示前 ${MAX_CATEGORIES} 个，其余未绘制；完整数据请查看「查看数据」。`);
  }
  if (hiddenSeries > 0) {
    notes.push(
      `共 ${metrics.length + hiddenSeries} 个系列，图中仅展示前 ${MAX_SERIES} 个，其余未绘制；完整数据请查看「查看数据」。`,
    );
  }
  if (negatives > 0) {
    notes.push(
      normalized || stacked
        ? `已忽略 ${negatives} 个负值：堆叠图无法表达负数，请改用分组柱状图或折线图查看。`
        : `已忽略 ${negatives} 个负值：分组柱状图按 0 起点绘制，请改用条形图查看负向数据。`,
    );
  }

  return (
    <div className='product-grouped-chart' aria-label={normalized ? '100% 堆叠图' : stacked ? '堆叠图' : '分组柱状图'}>
      <div className='product-grouped-legend'>
        {metrics.map((metric, index) => (
          <span key={`${metric.field}-${index}`}>
            <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
            {metric.label || metric.field}
          </span>
        ))}
      </div>
      <div className='product-grouped-body'>
        {limited.map((row, rowIndex) => {
          // 100% 堆叠只在正值域内成立：负值一旦进分母就会算出 width:-37.5%（非法 CSS，整行空白）。
          const positives = row.values.map(value => (Number.isFinite(value) && value > 0 ? value : 0));
          const positiveTotal = positives.reduce((sum, value) => sum + value, 0);
          const incomplete = row.values.some(value => !Number.isFinite(value));
          const stackable = !((normalized || stacked) && incomplete) && (!normalized || positiveTotal > 0);
          return (
            <div className='product-grouped-row' key={`${row.label}-${rowIndex}`}>
              <span className='product-series-label' title={hasDimension ? displayDimension(row.label, encoding.x) : undefined}>
                {hasDimension ? displayDimension(row.label, encoding.x) : String(rowIndex + 1)}
              </span>
              <div className='product-grouped-bars'>
                {!stackable && <span className='product-grouped-void'>{incomplete ? '组合缺失、值未知或重复，无法计算完整堆叠' : '无正值，无法按 100% 堆叠'}</span>}
                {stackable &&
                  metrics.map((metric, metricIndex) => {
                    const value = row.values[metricIndex] ?? 0;
                    if (!Number.isFinite(value)) return <span key={metric.field} className='product-grouped-void' title={`${displayDimension(row.label, encoding.x)} · ${metric.label || metric.field}: 无数据或聚合组合重复`}>—</span>;
                    const positive = positives[metricIndex] ?? 0;
                    const color = SERIES_COLORS[metricIndex % SERIES_COLORS.length];
                    // 分母按图型分流：100% 堆叠用行合计、堆叠用最大行合计、分组用**本度量**
                    // 自己的最大值（多度量量纲不同，不能共用一个分母；同度量的各 series
                    // 取值共用该分母，跨分类才可比）。百分比落在等宽槽位内，所以每行总宽
                    // 恒定不超过 100%，flex-shrink 不会再按各行总和独立压缩。
                    const denominator = normalized
                      ? positiveTotal
                      : stacked
                        ? stackMaximum
                        : denominatorOf(metricIndex);
                    const width = (positive / denominator) * 100;
                    const share = normalized ? ` · ${((positive / positiveTotal) * 100).toFixed(1)}%` : '';
                    const hint = `${displayDimension(row.label, encoding.x)} · ${metric.label || metric.field}: ${formatNumber(value)}${share}${
                      value < 0 ? '（负值未绘制）' : ''
                    }`;
                    const segment = (
                      <div
                        className='product-grouped-segment'
                        style={{
                          // 堆叠模式下扣掉段间 gap 占用的像素，最长的一行才不会被 overflow:hidden 裁掉一截。
                          width:
                            stacked || normalized
                              ? `calc(${Math.max(width, 0)}% - ${gapAllowance}px)`
                              : `${Math.max(width, 0)}%`,
                          background: color,
                        }}
                        title={hint}
                      />
                    );
                    return stacked || normalized ? (
                      <Fragment key={`${metric.field}-${metricIndex}`}>{segment}</Fragment>
                    ) : (
                      <div className='product-grouped-slot' key={`${metric.field}-${metricIndex}`} title={hint}>
                        {segment}
                      </div>
                    );
                  })}
              </div>
              {normalized && (
                <span className='product-grouped-axis-end' aria-hidden>
                  100%
                </span>
              )}
            </div>
          );
        })}
      </div>
      {notes.map(note => (
        <p className='product-visual-note' key={note}>
          {note}
        </p>
      ))}
    </div>
  );
}

function LineAreaView({
  result,
  encoding,
  area = false,
  width: customWidth,
}: {
  result: CanonicalResult;
  encoding: ChartSpec['encoding'];
  area?: boolean;
  width?: number;
}) {
  const containerNodeRef = useRef<HTMLDivElement | null>(null);
  const roRef = useRef<ResizeObserver | null>(null);

  const [measuredWidth, setMeasuredWidth] = useState<number>(640);
  const [measureVersion, setMeasureVersion] = useState<number>(0);
  const [instanceFont, setInstanceFont] = useState<string>(
    '500 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  );

  const updateInstanceFont = useCallback((el: HTMLElement) => {
    if (typeof window !== 'undefined' && window.getComputedStyle) {
      try {
        const tickEl = el.querySelector<SVGTextElement>('text.axis-tick-x') || el;
        const cs = window.getComputedStyle(tickEl);
        const weight = cs.fontWeight || '500';
        const size = cs.fontSize || '11px';
        const family = cs.fontFamily || '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        const computedFont = `${weight} ${size} ${family}`;
        setInstanceFont(prev => (prev !== computedFont ? computedFont : prev));
      } catch {}
    }
  }, []);

  const containerRefCallback = useCallback((node: HTMLDivElement | null) => {
    containerNodeRef.current = node;
    if (roRef.current) {
      roRef.current.disconnect();
      roRef.current = null;
    }
    if (node) {
      const updateSize = () => {
        const rect = node.getBoundingClientRect();
        const w = Math.round(rect.width);
        if (w > 0) {
          setMeasuredWidth(prev => (prev !== w ? w : prev));
        }
      };

      updateSize();
      updateInstanceFont(node);

      if (typeof ResizeObserver !== 'undefined') {
        const ro = new ResizeObserver(() => {
          updateSize();
          updateInstanceFont(node);
        });
        ro.observe(node);
        roRef.current = ro;
      }
    }
  }, [updateInstanceFont]);

  useEffect(() => {
    return () => {
      if (roRef.current) {
        roRef.current.disconnect();
        roRef.current = null;
      }
    };
  }, []);

  // 监听 Web 字体加载完成事件：不改变容器宽度的情况下更新组件度量版本，触发重新排布
  useEffect(() => {
    if (typeof document === 'undefined' || !document.fonts) return;
    let active = true;

    const onFontsUpdated = () => {
      if (!active) return;
      setMeasureVersion(v => v + 1);
      const node = containerNodeRef.current;
      if (node) {
        updateInstanceFont(node);
      }
    };

    document.fonts.ready?.then(onFontsUpdated).catch(() => {});
    document.fonts.addEventListener?.('loadingdone', onFontsUpdated);

    return () => {
      active = false;
      document.fonts.removeEventListener?.('loadingdone', onFontsUpdated);
    };
  }, [updateInstanceFont]);

  const width = Math.max(customWidth ?? measuredWidth ?? 640, 240);
  const height = 360;
  const { rows, metrics, seriesMetric } = useMemo(() => extractSeriesGroups(result, encoding), [result, encoding]);
  const labels = useMemo(() => rows.map(row => row.label), [rows]);
  const series = useMemo(
    () => metrics.map((_, index) => rows.map(row => numericValue(row.values[index]))),
    [metrics, rows],
  );
  const allValues = useMemo(
    () => series.flat().filter((value): value is number => value !== undefined),
    [series],
  );

  const hasData = allValues.length > 0 && metrics.length > 0;

  const rawMin = hasData ? Math.min(...allValues) : 0;
  const rawMax = hasData ? Math.max(...allValues) : 1;
  // 全平序列（yMin == yMax）过去恒等于 0，被画成贴着底轴的直线，看起来像「值都是 0」。
  // 给一个对称留白，让平坦序列落在中线上。
  const flat = rawMax === rawMin;
  const pad = flat ? Math.abs(rawMax) * 0.5 || 1 : 0;
  const yMin = rawMin - pad;
  const yMax = rawMax + pad;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const tickValues = useMemo(() => ticks.map(tick => yMax - tick * (yMax - yMin)), [yMin, yMax]);
  // 轴留白随实际格式化刻度标签宽度自适应：纵轴标签右对齐锚在 left-8，
  // 故 left 至少要容纳最宽标签 + 8 间距 + 4 左缘安全余量，否则大数/小数标签
  // 首位会画出 viewBox 左缘被裁切（Q21 真机：41,513,700 显示成 1,513,700）。
  // 窄标签保留既有 62 下限，不改变已验收小数值图表的观感。
  const leftPadding = useMemo(() => {
    const widest = Math.max(
      ...tickValues.map(value => measureTextWidth(formatNumber(value), undefined, instanceFont)),
    );
    return Math.max(62, Math.ceil(widest) + 8 + 4);
  }, [tickValues, instanceFont, measureVersion]);
  const padding = useMemo(() => ({ left: leftPadding, right: 24, top: 20, bottom: 54 }), [leftPadding]);

  // 渲染出带有 text.axis-tick 的 SVG 后，从真实子元素中精确再次读取本实例字体
  useEffect(() => {
    const node = containerNodeRef.current;
    if (node) {
      updateInstanceFont(node);
    }
  }, [hasData, updateInstanceFont]);

  // 纯函数刻度选择：显式传入本实例测量的 instanceFont，并通过 measureVersion 响应字体就绪
  const axisTicks = useMemo(
    () =>
      selectAxisTicks({
        labels,
        encodingX: encoding?.x,
        width,
        padding,
        measureWidth: text => measureTextWidth(text, undefined, instanceFont),
      }),
    [labels, encoding?.x, width, padding, instanceFont, measureVersion],
  );

  if (!hasData) {
    return <EmptyVisual note='没有可绘制的趋势数据' />;
  }

  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const scaleX = (index: number) =>
    padding.left + (labels.length <= 1 ? plotW / 2 : (index / (labels.length - 1)) * plotW);
  const scaleY = (value: number) => height - padding.bottom - ((value - yMin) / (yMax - yMin || 1)) * plotH;
  const units = new Map(yFields(encoding).map(metric => [metric.field, metric.unit]));

  return (
    <div ref={containerRefCallback} className='product-line-chart' aria-label={area ? '面积图' : '折线图'}>
      <svg viewBox={`0 0 ${width} ${height}`} role='img'>
        {ticks.map((tick, tickIndex) => {
          const y = padding.top + tick * plotH;
          const value = tickValues[tickIndex];
          return (
            <g key={tick}>
              <line className='grid' x1={padding.left} x2={width - padding.right} y1={y} y2={y} />
              <text className='axis-tick' x={padding.left - 8} y={y + 3} textAnchor='end'>
                {formatNumber(value)}
              </text>
            </g>
          );
        })}
        {metrics.map((metric, metricIndex) => {
          const values = series[metricIndex] ?? [];
          const color = SERIES_COLORS[metricIndex % SERIES_COLORS.length];
          if (!values.some(value => value !== undefined)) return null;
          // 缺失值不再当 0：把序列切成连续的有值片段，面积图不会莫名塌到零轴。
          const chunks: Array<Array<{ index: number; value: number }>> = [];
          let current: Array<{ index: number; value: number }> = [];
          values.forEach((value, index) => {
            if (value === undefined) {
              if (current.length) chunks.push(current);
              current = [];
              return;
            }
            current.push({ index, value });
          });
          if (current.length) chunks.push(current);
          return (
            <g key={`${metric.field}-${metricIndex}`}>
              {chunks.map((chunk, chunkIndex) => {
                const key = `${metric.field}-${metricIndex}-${chunkIndex}`;
                // 孤立的单点连不成线，用点标出来才不会整段消失。
                if (chunk.length === 1) {
                  return (
                    <circle key={key} cx={scaleX(chunk[0].index)} cy={scaleY(chunk[0].value)} r='3' fill={color} />
                  );
                }
                const linePoints = chunk.map(point => `${scaleX(point.index)},${scaleY(point.value)}`).join(' ');
                const bottomY = height - padding.bottom;
                return (
                  <Fragment key={key}>
                    {area && (
                      <polygon
                        points={`${scaleX(chunk[0].index)},${bottomY} ${linePoints} ${scaleX(chunk[chunk.length - 1].index)},${bottomY}`}
                        fill={color}
                        opacity='0.16'
                      />
                    )}
                    <polyline points={linePoints} fill='none' stroke={color} strokeWidth='2.4' />
                  </Fragment>
                );
              })}
            </g>
          );
        })}
        {axisTicks.map(tick => (
          <text
            key={tick.index}
            className='axis-tick axis-tick-x'
            x={tick.x}
            y={height - padding.bottom + 18}
            textAnchor={tick.textAnchor}
          >
            {tick.displayLabel}
            <title>{tick.formattedLabel}</title>
          </text>
        ))}
        <text className='axis-label' x={(padding.left + width - padding.right) / 2} y={height - 6}>
          {fieldLabel(encoding.x)}
        </text>
      </svg>
      <div className='product-grouped-legend'>
        {metrics.map((metric, index) => {
          const label = metric.label || metric.field;
          const unit = units.get(seriesMetric[index]);
          const suffix = unit && !label.endsWith(`（${unit}）`) && !label.endsWith(`(${unit})`) ? `（${unit}）` : '';
          return (
            <span key={`${metric.field}-${index}`}>
              <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
              {label + suffix}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function PieDonutView({
  result,
  encoding,
  donut = false,
}: {
  result: CanonicalResult;
  encoding: ChartSpec['encoding'];
  donut?: boolean;
}) {
  const source = result.rows ?? [];
  const metric = yFields(encoding)[0];
  const xField = fieldName(encoding.x);
  // Keep every finite value: the old `value > 0` filter silently dropped zero and
  // negative rows, so a chart could show 100% for one category while the table
  // right below it showed three.
  const parsed = source
    .map(row => ({ label: dimensionLabel(row[xField]), value: numericValue(row[metric?.field ?? '']) }))
    .filter((entry): entry is { label: string; value: number } => entry.value !== null && Number.isFinite(entry.value));
  const negative = parsed.filter(entry => entry.value < 0);
  const entries = parsed.filter(entry => entry.value > 0);
  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  if (!entries.length || !total) return <EmptyVisual note='没有可绘制的占比数据' />;
  const size = 300;
  const radius = size / 2;
  const inner = donut ? radius * 0.6 : 0;
  let angle = -Math.PI / 2;
  const segments = entries.map((entry, index) => {
    const fraction = entry.value / total;
    const start = angle;
    const end = start + fraction * Math.PI * 2;
    angle = end;
    const largeArc = fraction > 0.5 ? 1 : 0;
    const color = SERIES_COLORS[index % SERIES_COLORS.length];
    // A 100% slice has identical start and end points, and SVG omits an arc whose
    // endpoints coincide - the whole chart rendered blank. Draw a full circle instead.
    if (fraction >= 0.9999) {
      return { ...entry, index, color, fraction, full: true as const };
    }
    const sx = radius + Math.cos(start) * radius;
    const sy = radius + Math.sin(start) * radius;
    const ex = radius + Math.cos(end) * radius;
    const ey = radius + Math.sin(end) * radius;
    const ix = radius + Math.cos(start) * inner;
    const iy = radius + Math.sin(start) * inner;
    const exi = radius + Math.cos(end) * inner;
    const eyi = radius + Math.sin(end) * inner;
    const path = donut
      ? `M ${sx} ${sy} A ${radius} ${radius} 0 ${largeArc} 1 ${ex} ${ey} L ${exi} ${eyi} A ${inner} ${inner} 0 ${largeArc} 0 ${ix} ${iy} Z`
      : `M ${radius} ${radius} L ${sx} ${sy} A ${radius} ${radius} 0 ${largeArc} 1 ${ex} ${ey} Z`;
    return { ...entry, index, path, color, fraction, full: false as const };
  });
  return (
    <div className='product-pie-chart' aria-label={donut ? '环图' : '饼图'}>
      <svg viewBox={`0 0 ${size} ${size}`} role='img'>
        {/* key 必须带 index：维度含 NULL 时 label 全退化成空串，纯 label 做 key 会让重复分类只画一个扇形。 */}
        {segments.map(segment =>
          segment.full ? (
            donut ? (
              <circle
                key={`${segment.label}-${segment.index}`}
                cx={radius}
                cy={radius}
                r={(radius + inner) / 2}
                fill='none'
                stroke={segment.color}
                strokeWidth={radius - inner}
              >
                <title>{`${displayDimension(segment.label)}: ${formatMeasure(segment.value, metric)} (100%)`}</title>
              </circle>
            ) : (
              <circle key={`${segment.label}-${segment.index}`} cx={radius} cy={radius} r={radius} fill={segment.color}>
                <title>{`${displayDimension(segment.label)}: ${formatMeasure(segment.value, metric)} (100%)`}</title>
              </circle>
            )
          ) : (
            <path
              key={`${segment.label}-${segment.index}`}
              d={segment.path}
              fill={segment.color}
              stroke='#fff'
              strokeWidth='1'
            >
              <title>{`${displayDimension(segment.label)}: ${formatMeasure(segment.value, metric)} (${(segment.fraction * 100).toFixed(1)}%)`}</title>
            </path>
          ),
        )}
        {donut && <circle cx={radius} cy={radius} r={inner} fill='var(--dsw-alias-bg-layer-1, #fff)' />}
      </svg>
      <div className='product-pie-legend'>
        {segments.map(segment => (
          <span key={`${segment.label}-${segment.index}`}>
            <i style={{ background: segment.color }} />
            {displayDimension(segment.label)} · {formatMeasure(segment.value, metric)} ({(segment.fraction * 100).toFixed(1)}%)
          </span>
        ))}
      </div>
      {negative.length > 0 && (
        <p className='product-visual-note'>
          已忽略 {negative.length} 个负值（{negative.map(entry => entry.label).join('、')}
          ）：占比图无法表达负数，请改用柱状图查看。
        </p>
      )}
    </div>
  );
}

function ScatterBubbleView({
  result,
  encoding,
  bubble = false,
}: {
  result: CanonicalResult;
  encoding: ChartSpec['encoding'];
  bubble?: boolean;
}) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const yRef = yFields(encoding)[0];
  const yField = yRef?.field ?? '';
  const sizeField = fieldName(encoding.size);
  const width = 600;
  const height = 360;
  const padding = { left: 90, right: 40, top: 30, bottom: 58 };
  const points = source
    .map((row, index) => ({
      label: String(row[fieldName(encoding.color)] ?? row[xField] ?? index + 1),
      x: numericValue(row[xField]),
      y: numericValue(row[yField]),
      size: sizeField ? numericValue(row[sizeField]) : undefined,
    }))
    .filter(point => point.x !== undefined && point.y !== undefined) as Array<{
    label: string;
    x: number;
    y: number;
    size?: number;
  }>;
  if (!points.length) return <EmptyVisual note='没有可配对的相关性数据' />;
  const xValues = points.map(point => point.x);
  const yValues = points.map(point => point.y);
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  const yMin = Math.min(...yValues);
  const yMax = Math.max(...yValues);
  const sizeMax = bubble ? Math.max(...points.map(point => point.size ?? 1), 1) : 1;
  const scaleX = (value: number) =>
    padding.left + ((value - xMin) / (xMax - xMin || 1)) * (width - padding.left - padding.right);
  const scaleY = (value: number) =>
    height - padding.bottom - ((value - yMin) / (yMax - yMin || 1)) * (height - padding.top - padding.bottom);
  return (
    <div className='product-scatter-wrap' aria-label={bubble ? '气泡图' : '散点图'}>
      <svg className='product-scatter-chart' viewBox={`0 0 ${width} ${height}`} role='img'>
        <line x1={padding.left} x2={padding.left} y1={padding.top} y2={height - padding.bottom} />
        <line x1={padding.left} x2={width - padding.right} y1={height - padding.bottom} y2={height - padding.bottom} />
        {[0, 0.25, 0.5, 0.75, 1].map(tick => {
          const y = padding.top + tick * (height - padding.top - padding.bottom);
          const x = padding.left + tick * (width - padding.left - padding.right);
          return <Fragment key={tick}><line className='grid' x1={padding.left} x2={width - padding.right} y1={y} y2={y} />
            <text className='axis-tick' textAnchor='end' x={padding.left-8} y={y+4}>{formatNumber(yMax-tick*(yMax-yMin))}</text>
            <text className='axis-tick' textAnchor='middle' x={x} y={height-padding.bottom+20}>{formatNumber(xMin+tick*(xMax-xMin))}</text></Fragment>;
        })}
        {points.map((point, index) => {
          const radius = bubble ? 18 * Math.sqrt(Math.max(0,point.size ?? 1) / sizeMax) : 6;
          return (
            <circle
              key={`${point.label}-${index}`}
              cx={scaleX(point.x)}
              cy={scaleY(point.y)}
              r={radius}
              fill={SERIES_COLORS[0]}
              fillOpacity={bubble ? 0.55 : 0.85}
            >
              <title>{`${point.label}: ${fieldLabel(encoding.x)} ${formatNumber(point.x)}, ${fieldLabel(yRef)} ${formatNumber(point.y)}${point.size !== undefined ? `, 规模 ${formatNumber(point.size)}` : ''}`}</title>
            </circle>
          );
        })}
        <text className='axis-label' x={(padding.left + width - padding.right) / 2} y={height - 14}>
          {fieldLabel(encoding.x)}
        </text>
        <text
          className='axis-label'
          transform={`translate(18 ${(padding.top + height - padding.bottom) / 2}) rotate(-90)`}
        >
          {fieldLabel(yRef)}
        </text>
      </svg>
    </div>
  );
}

function HeatmapView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const colField = fieldName(encoding.series);
  const valRef = yFields(encoding)[0];
  const valField = valRef?.field ?? '';
  if (!xField || !colField || !valField) return <EmptyVisual note='缺少行、列或数值字段，无法绘制交叉矩阵' />;
  const rowLabels = Array.from(new Set(source.map(row => dimensionLabel(row[xField]))));
  const colLabels = Array.from(new Set(source.map(row => dimensionLabel(row[colField]))));
  // 写入与读取共用同一个键函数与归一化标签；分隔符用不可能出现在维度值里的 \u0001，
  // 旧实现用 '|' 拼原始值，维度值本身含 '|' 或为 null 时都会错位。
  const cellKey = (rowValue: unknown, colValue: unknown) =>
    `${dimensionLabel(rowValue)}\u0001${dimensionLabel(colValue)}`;
  const cellMap = new Map<string, number>();
  for (const row of source) {
    const value = numericValue(row[valField]);
    if (value !== undefined) cellMap.set(cellKey(row[xField], row[colField]), value);
  }
  if (!rowLabels.length || !colLabels.length) return <EmptyVisual note='没有可绘制的交叉矩阵数据' />;
  const values = Array.from(cellMap.values());
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const colorFor = (value: number) => {
    const t = max === min ? 0.6 : (value - min) / (max - min);
    const r = Math.round(236 - t * 178);
    const g = Math.round(240 - t * 140);
    const b = Math.round(252 - t * 82);
    return `rgb(${r}, ${g}, ${b})`;
  };
  return (
    <div className='product-heatmap' aria-label='矩阵热力图'>
      <div
        className='product-heatmap-grid'
        style={{ gridTemplateColumns: `auto repeat(${colLabels.length}, minmax(46px, 1fr))` }}
      >
        <div className='product-heatmap-cell product-heatmap-head product-heatmap-corner' />
        {colLabels.map((col, colIndex) => (
          <div
            className='product-heatmap-cell product-heatmap-head'
            key={`col-${col}-${colIndex}`}
            title={displayDimension(col)}
          >
            {displayDimension(col)}
          </div>
        ))}
        {rowLabels.map((rowLabel, rowIndex) => (
          <Fragment key={`row-${rowLabel}-${rowIndex}`}>
            <div className='product-heatmap-cell product-heatmap-head' title={displayDimension(rowLabel)}>
              {displayDimension(rowLabel)}
            </div>
            {colLabels.map((col, colIndex) => {
              // 「无数据」和「值为 0」必须区分：旧实现 `value ? ... : ''` 让真实的 0 显示成空白。
              const key = cellKey(rowLabel, col);
              const filled = cellMap.has(key);
              const value = cellMap.get(key) ?? 0;
              return (
                <div
                  className={`product-heatmap-cell${filled ? '' : ' product-heatmap-cell--empty'}`}
                  key={`${rowLabel}-${col}-${rowIndex}-${colIndex}`}
                  style={filled ? { background: colorFor(value) } : undefined}
                  title={`${displayDimension(rowLabel)} × ${displayDimension(col)}: ${filled ? formatNumber(value) : '无数据'}`}
                >
                  {filled ? formatNumber(value) : '—'}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
      <div className='product-heatmap-scale'>
        <span>{formatNumber(min)}</span>
        <i style={{ background: `linear-gradient(90deg, ${colorFor(min)}, ${colorFor(max)})` }} />
        <span>{formatNumber(max)}</span>
      </div>
    </div>
  );
}

interface TreemapItem {
  label: string;
  value: number;
}

interface TreemapRect extends TreemapItem {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 当前行的最差长宽比，squarified 算法用它决定是否继续往这一行塞元素。 */
const worstAspect = (row: TreemapItem[], rowValue: number, total: number, area: number, short: number) => {
  if (rowValue <= 0 || short <= 0) return Infinity;
  const rowArea = (rowValue / total) * area;
  const thickness = rowArea / short;
  if (!(thickness > 0)) return Infinity;
  return Math.max(
    ...row.map(item => {
      const side = ((item.value / rowValue) * rowArea) / thickness;
      return side > 0 ? Math.max(side / thickness, thickness / side) : Infinity;
    }),
  );
};

/**
 * squarified treemap 布局。旧实现把所有矩形铺成单行等高切片，长尾项各占 1px、
 * 累计宽度超出 viewBox，最右侧的项直接落在画布外，既看不见也点不到。
 * 这里按行折叠，任何时刻矩形都被限制在剩余矩形内，因此所有项必然可见。
 */
function squarifyTreemap(items: TreemapItem[], width: number, height: number): TreemapRect[] {
  const out: TreemapRect[] = [];
  let rest = items.filter(item => item.value > 0);
  let x = 0;
  let y = 0;
  let w = width;
  let h = height;
  while (rest.length && w > 0 && h > 0) {
    const total = rest.reduce((sum, item) => sum + item.value, 0);
    if (total <= 0) break;
    const horizontal = w <= h;
    const short = horizontal ? w : h;
    const area = w * h;
    const row: TreemapItem[] = [rest[0]];
    let rowValue = rest[0].value;
    let aspect = worstAspect(row, rowValue, total, area, short);
    while (row.length < rest.length) {
      const next = rest[row.length];
      const nextValue = rowValue + next.value;
      const nextAspect = worstAspect([...row, next], nextValue, total, area, short);
      if (nextAspect > aspect) break;
      row.push(next);
      rowValue = nextValue;
      aspect = nextAspect;
    }
    const rowArea = (rowValue / total) * area;
    const thickness = Math.min(horizontal ? h : w, rowArea / short);
    let offset = horizontal ? x : y;
    row.forEach((item, index) => {
      const side = rowValue > 0 ? (item.value / rowValue) * short : short / row.length;
      out.push(
        horizontal
          ? { ...item, x: offset, y, w: side, h: thickness }
          : { ...item, x, y: offset, w: thickness, h: side },
      );
      offset += side;
      if (index === row.length - 1) {
        if (horizontal) {
          y += thickness;
          h -= thickness;
        } else {
          x += thickness;
          w -= thickness;
        }
      }
    });
    rest = rest.slice(row.length);
  }
  return out;
}

function TreemapView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const valRef = yFields(encoding)[0];
  const parsed = source
    .map(row => ({ label: dimensionLabel(row[xField]), value: numericValue(row[valRef?.field ?? '']) ?? 0 }))
    .filter(entry => entry.value > 0)
    .sort((a, b) => b.value - a.value);
  const grandTotal = parsed.reduce((sum, entry) => sum + entry.value, 0);
  if (!parsed.length || !grandTotal) return <EmptyVisual note='没有可绘制的占比数据' />;
  // Keep every category; small tiles retain full values in the scrollable legend.
  const entries: TreemapItem[] = parsed;
  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  const width = 640;
  const height = 360;
  const rects = squarifyTreemap(entries, width, height);
  return (
    <div className='product-treemap' aria-label='树图'>
      <svg className='product-treemap-svg' viewBox={`0 0 ${width} ${height}`} role='img'>
        {rects.map((rect, index) => (
          <g key={`${rect.label}-${index}`}>
            <rect
              x={rect.x}
              y={rect.y}
              width={Math.max(rect.w, 0)}
              height={Math.max(rect.h, 0)}
              fill={SERIES_COLORS[index % SERIES_COLORS.length]}
              stroke='#fff'
              strokeWidth='1'
            >
              <title>{`${displayDimension(rect.label)}: ${formatMeasure(rect.value, valRef)} (${((rect.value / total) * 100).toFixed(1)}%)`}</title>
            </rect>
            {rect.w > 56 && rect.h > 24 && (
              <text
                x={rect.x + rect.w / 2}
                y={rect.y + rect.h / 2}
                textAnchor='middle'
                dominantBaseline='middle'
                fill='#fff'
                fontSize='12'
              >
                {truncateLabel(displayDimension(rect.label), Math.max(Math.floor(rect.w / 9), 2))}
              </text>
            )}
          </g>
        ))}
      </svg>
      <div className='product-pie-legend'>
        {rects.map((rect, index) => (
          <span key={`${rect.label}-${index}`}>
            <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
            {displayDimension(rect.label)} · {formatMeasure(rect.value, valRef)} ({((rect.value / total) * 100).toFixed(1)}%)
          </span>
        ))}
      </div>
    </div>
  );
}

function HistogramView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const values = source.map(row => numericValue(row[xField])).filter((value): value is number => value !== undefined);
  if (values.length < 2) return <EmptyVisual note='没有可统计分布的数据' />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const binCount = Math.min(20, Math.max(5, Math.ceil(Math.log2(values.length) + 1)));
  const binWidth = (max - min) / binCount || 1;
  const bins = Array.from({ length: binCount }, () => 0);
  for (const value of values) {
    const index = Math.min(binCount - 1, Math.floor((value - min) / binWidth));
    bins[index] += 1;
  }
  const maxCount = Math.max(...bins, 1);
  const width = 640;
  const height = 340;
  const padding = { left: 56, right: 20, top: 20, bottom: 54 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const barW = plotW / binCount;
  return (
    <div className='product-histogram' aria-label='直方图'>
      <svg className='product-histogram-svg' viewBox={`0 0 ${width} ${height}`} role='img'>
        {[0, 0.25, 0.5, 0.75, 1].map(tick => {
          const y = padding.top + tick * plotH;
          return <line className='grid' key={tick} x1={padding.left} x2={width - padding.right} y1={y} y2={y} />;
        })}
        {bins.map((count, index) => {
          const barH = (count / maxCount) * plotH;
          return (
            <rect
              key={index}
              x={padding.left + index * barW + 1}
              y={height - padding.bottom - barH}
              width={Math.max(barW - 2, 1)}
              height={barH}
              fill={SERIES_COLORS[0]}
              fillOpacity='0.82'
            >
              <title>{`${formatNumber(min + index * binWidth)} – ${formatNumber(min + (index + 1) * binWidth)}: ${count} 项`}</title>
            </rect>
          );
        })}
        <text className='axis-label' x={padding.left} y={height - padding.bottom + 18} textAnchor='middle'>
          {formatNumber(min)}
        </text>
        <text className='axis-label' x={width - padding.right} y={height - padding.bottom + 18} textAnchor='middle'>
          {formatNumber(max)}
        </text>
        <text className='axis-label' x={(padding.left + width - padding.right) / 2} y={height - 6}>
          {fieldLabel(encoding.x)}（{binCount} 个区间）
        </text>
      </svg>
    </div>
  );
}

function RadarView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const metrics = yFields(encoding);
  const xField = fieldName(encoding.x);
  if (metrics.length < 3) return <EmptyVisual note='雷达图至少需要 3 个指标维度' />;
  const entities = source.map((row, index) => ({
    label: xField ? dimensionLabel(row[xField]) : String(index + 1),
    values: metrics.map(metric => numericValue(row[metric.field]) ?? 0),
  }));
  const maxes = metrics.map((_, metricIndex) => Math.max(...entities.map(entity => entity.values[metricIndex]), 1));
  const size = 360;
  const cx = size / 2;
  const cy = size / 2;
  const radius = 128;
  const count = metrics.length;
  const angleFor = (index: number) => -Math.PI / 2 + (index * 2 * Math.PI) / count;
  const pointFor = (index: number, ratio: number) => {
    const r = radius * ratio;
    return [cx + Math.cos(angleFor(index)) * r, cy + Math.sin(angleFor(index)) * r];
  };
  return (
    <div className='product-radar' aria-label='雷达图'>
      <svg viewBox={`0 0 ${size} ${size}`} role='img'>
        {[0.2, 0.4, 0.6, 0.8, 1].map(level => {
          const points = metrics
            .map((_, index) =>
              pointFor(index, level)
                .map(value => value.toFixed(1))
                .join(','),
            )
            .join(' ');
          return <polygon key={level} points={points} fill='none' stroke='#e5e7eb' strokeWidth='1' />;
        })}
        {metrics.map((metric, index) => {
          const [x, y] = pointFor(index, 1);
          return <line key={metric.field} x1={cx} y1={cy} x2={x} y2={y} stroke='#e5e7eb' strokeWidth='1' />;
        })}
        {entities.map((entity, entityIndex) => {
          const points = metrics
            .map((_, index) =>
              pointFor(index, entity.values[index] / maxes[index])
                .map(value => value.toFixed(1))
                .join(','),
            )
            .join(' ');
          return (
            <polygon
              key={`${entity.label}-${entityIndex}`}
              points={points}
              fill={SERIES_COLORS[entityIndex % SERIES_COLORS.length]}
              fillOpacity='0.24'
              stroke={SERIES_COLORS[entityIndex % SERIES_COLORS.length]}
              strokeWidth='2'
            >
              <title>{displayDimension(entity.label)}</title>
            </polygon>
          );
        })}
        {metrics.map((metric, index) => {
          const labelR = radius + 20;
          const lx = cx + Math.cos(angleFor(index)) * labelR;
          const ly = cy + Math.sin(angleFor(index)) * labelR;
          return (
            <text key={metric.field} className='axis-tick' x={lx} y={ly} textAnchor='middle' dominantBaseline='middle'>
              {metric.label || metric.field}
            </text>
          );
        })}
      </svg>
      <div className='product-grouped-legend'>
        {entities.map((entity, index) => (
          <span key={`${entity.label}-${index}`}>
            <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
            {displayDimension(entity.label)}
          </span>
        ))}
      </div>
    </div>
  );
}

function FunnelView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const valRef = yFields(encoding)[0];
  const stages = source
    .map(row => ({ label: dimensionLabel(row[xField]), value: numericValue(row[valRef?.field ?? '']) ?? 0 }))
    .filter(stage => stage.value > 0);
  if (!stages.length) return <EmptyVisual note='没有可绘制的漏斗数据' />;
  const max = Math.max(...stages.map(stage => stage.value), 1);
  const width = 560;
  const height = Math.max(360, stages.length * 44);
  const stageH = height / stages.length;
  return (
    <div className='product-funnel' aria-label='漏斗图'>
      <svg style={{minWidth:560,height}} className='product-funnel-svg' viewBox={`0 0 ${width} ${height}`} role='img'>
        {stages.map((stage, index) => {
          const ratio = Math.max(stage.value / max, 0.08);
          const halfW = width * 0.44 * ratio;
          const x1 = width / 2 - halfW;
          const x2 = width / 2 + halfW;
          const y1 = index * stageH + 6;
          const y2 = (index + 1) * stageH - 6;
          return (
            <g key={`${stage.label}-${index}`}>
              <path
                d={`M ${x1} ${y1} L ${x2} ${y1} L ${x2} ${y2} L ${x1} ${y2} Z`}
                fill={SERIES_COLORS[index % SERIES_COLORS.length]}
                fillOpacity='0.82'
              >
                <title>{`${displayDimension(stage.label)}: ${formatNumber(stage.value)}`}</title>
              </path>
              <text
                x={width / 2}
                y={(y1 + y2) / 2}
                textAnchor='middle'
                dominantBaseline='middle'
                fill='#fff'
                fontSize='12'
              >
                {displayDimension(stage.label)} · {formatNumber(stage.value)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function GaugeView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const row = result.rows?.[0] ?? {};
  const fields = yFields(encoding);
  const hasRatio = fields.some(isPercentageMetric);
  const metrics = hasRatio ? fields.filter(m => !/^(?:total_)?(?:batches|samples?|sample_count|record_count|count|denominator|numerator)$/i.test(m.field)) : fields;
  if (!metrics.length) return <EmptyVisual note='没有可展示的指标' />;
  if(metrics.some(metric=>numericValue(row[metric.field])===undefined))return <EmptyVisual note="指标值未知，不能绘制为零"/>;
  const size = 320;
  const cx = size / 2;
  const cy = 150;
  const r = 120;
  return (
    <div><div className='product-gauge-grid' aria-label='仪表盘'>
      {metrics.map((metric, index) => {
        const raw = numericValue(row[metric.field]);
        const value = raw === undefined ? 0 : raw;
        const asPercent = isPercentageMetric(metric);
        // Percentage metrics keep the literal 0-100 scale; anything else gets an
        // upper bound derived from the value so the needle and the axis agree.
        const max = asPercent ? 100 : niceCeiling(Math.max(value, 0));
        const clamped = Math.max(0, Math.min(max, value));
        const pct = max > 0 ? (clamped / max) * 100 : 0;
        const start = Math.PI;
        const end = 2 * Math.PI;
        const angle = start + (end - start) * (pct / 100);
        const ex = cx + Math.cos(angle) * r;
        const ey = cy + Math.sin(angle) * r;
        const color = SERIES_COLORS[index % SERIES_COLORS.length];
        return (
          <div className='product-gauge-card' key={metric.field}>
            <svg viewBox={`0 0 ${size} ${size * 0.68}`} role='img' aria-label={`${metric.label || metric.field}: ${formatNumber(value)}${metric.unit || (asPercent ? '%' : '')}`}>
              <path
                d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
                fill='none'
                stroke='#e9eef3'
                strokeWidth='18'
                strokeLinecap='round'
              />
              {/* 半圆仪表任意时刻扫过角 ≤180°，large-arc-flag 必须恒为 0：
                  一旦置 1，SVG 会改用另一个圆心画出 360-θ 的大弧，蓝弧绕到下半圈。 */}
              <path
                d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${ex} ${ey}`}
                fill='none'
                stroke={color}
                strokeWidth='18'
                strokeLinecap='round'
              />
              <text x={cx} y={cy - 22} textAnchor='middle' fontSize='34' fontWeight='700' fill='var(--product-text)'>
                {formatNumber(value)}
                {metric.unit || (asPercent ? '%' : '')}
              </text>
              <text x={cx} y={cy + 2} textAnchor='middle' fontSize='12' fill='var(--product-text)'>
                {metric.label || metric.field}
              </text>
              <text x={cx - r} y={cy + 22} textAnchor='middle' fontSize='10' fill='var(--product-text)'>
                0
              </text>
              <text x={cx + r} y={cy + 22} textAnchor='middle' fontSize='10' fill='var(--product-text)'>
                {asPercent ? '100%' : `${formatNumber(max)}${metric.unit ?? ''}`}
              </text>
            </svg>
          </div>
        );
      })}
    </div>{metrics.length < fields.length && <p>仪表盘展示比率指标；样本数等计算依据保留在数据页。</p>}</div>
  );
}

function WaterfallView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const valRef = yFields(encoding)[0];
  const items = source.map(row => ({
    label: String(row[xField] ?? ''),
    value: numericValue(row[valRef?.field ?? '']) ?? 0,
  }));
  if (!items.length) return <EmptyVisual note='没有可绘制的增减数据' />;
  let cumulative = 0;
  const bars = items.map(item => {
    const start = cumulative;
    cumulative += item.value;
    return { ...item, start, end: cumulative };
  });
  const all = bars.flatMap(bar => [bar.start, bar.end]);
  const min = Math.min(...all, 0);
  const max = Math.max(...all, 1);
  const width = 640;
  const height = 360;
  const padding = { left: 56, right: 20, top: 20, bottom: 54 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const scaleY = (value: number) => height - padding.bottom - ((value - min) / (max - min || 1)) * plotH;
  const barW = plotW / bars.length;
  return (
    <div className='product-waterfall' aria-label='瀑布图'>
      <svg className='product-waterfall-svg' viewBox={`0 0 ${width} ${height}`} role='img'>
        <line x1={padding.left} x2={width - padding.right} y1={scaleY(0)} y2={scaleY(0)} stroke='#cbd5e1' />
        {bars.map((bar, index) => {
          const yTop = scaleY(Math.max(bar.start, bar.end));
          const yBottom = scaleY(Math.min(bar.start, bar.end));
          const positive = bar.value >= 0;
          const color = positive ? '#0f9f94' : '#ef4444';
          return (
            <g key={`${bar.label}-${index}`}>
              <rect
                x={padding.left + index * barW + 2}
                y={yTop}
                width={Math.max(barW - 4, 1)}
                height={Math.max(yBottom - yTop, 1)}
                fill={color}
                fillOpacity='0.85'
              >
                <title>{`${bar.label}: ${bar.value >= 0 ? '+' : ''}${formatNumber(bar.value)}`}</title>
              </rect>
              <text
                className='axis-tick'
                x={padding.left + index * barW + barW / 2}
                y={height - padding.bottom + 16}
                textAnchor='middle'
              >
                {bar.label}
              </text>
            </g>
          );
        })}
      </svg>
      <div className='product-grouped-legend'>
        <span>
          <i style={{ background: '#0f9f94' }} />
          增加
        </span>
        <span>
          <i style={{ background: '#ef4444' }} />
          减少
        </span>
      </div>
    </div>
  );
}

function BoxplotView({ result, encoding }: { result: CanonicalResult; encoding: ChartSpec['encoding'] }) {
  const source = result.rows ?? [];
  const xField = fieldName(encoding.x);
  const valRef = yFields(encoding)[0];
  const valField = valRef?.field ?? '';
  const groups = new Map<string, number[]>();
  for (const row of source) {
    const label = String(row[xField] ?? '');
    const value = numericValue(row[valField]);
    if (value === undefined) continue;
    const bucket = groups.get(label) ?? [];
    bucket.push(value);
    groups.set(label, bucket);
  }
  if (!groups.size) return <EmptyVisual note='没有可绘制的分组分布数据' />;
  const stats = Array.from(groups.entries()).map(([label, values]) => {
    const sorted = [...values].sort((a, b) => a - b);
    const quantile = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * (sorted.length - 1)))];
    return {
      label,
      min: sorted[0],
      q1: quantile(0.25),
      median: quantile(0.5),
      q3: quantile(0.75),
      max: sorted[sorted.length - 1],
    };
  });
  const all = stats.flatMap(item => [item.min, item.max]);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const width = 640;
  const height = 340;
  const padding = { left: 56, right: 20, top: 20, bottom: 54 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const scaleY = (value: number) => height - padding.bottom - ((value - min) / (max - min || 1)) * plotH;
  const slotW = plotW / stats.length;
  return (
    <div className='product-boxplot' aria-label='箱线图'>
      <svg className='product-boxplot-svg' viewBox={`0 0 ${width} ${height}`} role='img'>
        {[0, 0.25, 0.5, 0.75, 1].map(tick => {
          const y = padding.top + tick * plotH;
          return <line className='grid' key={tick} x1={padding.left} x2={width - padding.right} y1={y} y2={y} />;
        })}
        {stats.map((item, index) => {
          const cx = padding.left + index * slotW + slotW / 2;
          const boxW = Math.min(slotW * 0.5, 44);
          const yMin = scaleY(item.max);
          const yMax = scaleY(item.min);
          const yQ1 = scaleY(item.q1);
          const yQ3 = scaleY(item.q3);
          const yMedian = scaleY(item.median);
          const color = SERIES_COLORS[index % SERIES_COLORS.length];
          return (
            <g key={item.label}>
              <line x1={cx} x2={cx} y1={yMin} y2={yQ1} stroke={color} strokeWidth='1.4' />
              <line x1={cx} x2={cx} y1={yQ3} y2={yMax} stroke={color} strokeWidth='1.4' />
              <line x1={cx - boxW * 0.4} x2={cx + boxW * 0.4} y1={yMin} y2={yMin} stroke={color} strokeWidth='1.4' />
              <line x1={cx - boxW * 0.4} x2={cx + boxW * 0.4} y1={yMax} y2={yMax} stroke={color} strokeWidth='1.4' />
              <rect
                x={cx - boxW / 2}
                y={yQ1}
                width={boxW}
                height={Math.max(yQ3 - yQ1, 1)}
                fill={color}
                fillOpacity='0.28'
                stroke={color}
                strokeWidth='1.4'
              />
              <line x1={cx - boxW / 2} x2={cx + boxW / 2} y1={yMedian} y2={yMedian} stroke={color} strokeWidth='2' />
              <text className='axis-tick' x={cx} y={height - padding.bottom + 16} textAnchor='middle'>
                {item.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function TableView({ result, alertRules }: { result: CanonicalResult; alertRules?: AlertRule[] }) {
  const columns = result.columns ?? [];
  const rows = result.rows ?? [];
  if (!columns.length) return <EmptyVisual note='结果不包含表格数据' />;
  const period = result.validation?.period_grain;
  const periodGrains = new Map<string, Parameters<typeof formatVerifiedPeriod>[1]>();
  if (period?.state === 'checked' && Array.isArray(period.columns)) {
    for (const column of period.columns) {
      if (typeof column?.field === 'string' && !column.part && isPeriodGrain(column.grain)) {
        periodGrains.set(column.field, column.grain);
      }
    }
  }

  const evalRule = (row: Record<string, unknown>, rule: AlertRule): boolean => {
    const value = numericValue(row[rule.column]);
    if (value === undefined) return false;
    switch (rule.op) {
      case '>':
        return value > rule.threshold;
      case '>=':
        return value >= rule.threshold;
      case '<':
        return value < rule.threshold;
      case '<=':
        return value <= rule.threshold;
      case '==':
        return value === rule.threshold;
      case '!=':
        return value !== rule.threshold;
      default:
        return false;
    }
  };

  const TRUNCATED_AT = rows.length;
  // 违规判定必须扫全量行：旧实现只判前 50 行，1000 行里第 200 行超阈值也完全没有提示。
  const violationIndexes = alertRules
    ? rows.reduce<number[]>((acc, row, index) => {
        if (alertRules.some(rule => evalRule(row, rule))) acc.push(index);
        return acc;
      }, [])
    : [];
  const hiddenViolations = violationIndexes.filter(index => index >= TRUNCATED_AT).length;

  return (
    <div className='product-table-wrap' role='region' aria-label='表格内容，可滚动查看完整数据' tabIndex={0}>
      {alertRules && violationIndexes.length > 0 && (
        <div className='product-alert-banner' role='alert'>
          <WarningOutlined />
          <span>
            发现 <strong>{violationIndexes.length}</strong> 条命中预警/异常规则的记录（共 {rows.length} 条）
            {hiddenViolations > 0 && `，其中 ${hiddenViolations} 条不在下方展示的前 ${TRUNCATED_AT} 行内`}
          </span>
        </div>
      )}
      {rows.length > TRUNCATED_AT && (
        <div className='product-table-notice' role='note'>
          共 {rows.length} 行数据，表格仅展示前 {TRUNCATED_AT} 行；完整数据请在「查看数据」页签查看或下载。
        </div>
      )}
      <table>
        <thead>
          <tr>
            {columns.map(column => (
              <th key={column}>{columnLabelOf(result.column_labels, column)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, TRUNCATED_AT).map((row, index) => {
            const hit = alertRules ? alertRules.some(rule => evalRule(row, rule)) : false;
            return (
              <tr key={index} className={hit ? 'product-table-row--alert' : undefined}>
                {columns.map(column => {
                  const cellHit = alertRules && alertRules.some(rule => rule.column === column && evalRule(row, rule));
                  const grain = periodGrains.get(column);
                  return (
                    <td key={column} className={cellHit ? 'product-table-cell--alert' : undefined}>
                      {row[column] == null && column === 'value' && (result as any).reason_code === 'UNKNOWN'
                        ? <span className='product-cell-unknown' title={nullReasonOf(result, row) || '无法计算'}>无法计算</span>
                        : grain ? formatVerifiedPeriod(dimensionLabel(row[column]), grain) : displayValue(row[column])}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const PIVOT_ROW_HEIGHT = 32;
const PIVOT_MIN_HEIGHT = 240;
const PIVOT_MAX_HEIGHT = 520;
const PIVOT_MAX_ROWS = 500;

const formatPivotValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '-';
  const numeric = typeof value === 'number' ? value : Number(value);
  if (typeof numeric === 'number' && Number.isFinite(numeric) && (typeof value === 'number' || String(value).trim() !== '')) {
    return Number.isInteger(numeric)
      ? numeric.toLocaleString('zh-CN')
      : numeric.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  }
  return String(value);
};

/** 透视表视图的口径说明。交叉表不抽样——44 条明细会全铺进 11×5 网格、一项不丢，
 *  所以明细表那套「共 44 项，仅显示前 20 项」搬过来就是错的（2026-09-17 真机）。
 *  算法与后端 charts._pivot_disclosure 一致：后端裁决为透视表时给的就是同一句，
 *  这里负责用户从类型胶囊手工切过来时的口径（那条路径后端不知情）。 */
function pivotDisclosureNote(pivot: PivotLayout, rawRowCount: number): string | null {
  const rowCount = pivot.cardinality?.rows ?? 0;
  const columnCount = pivot.cardinality?.columns ?? 0;
  if (!rowCount) return null;
  return columnCount > 1
    ? `原始 ${rawRowCount} 条明细，汇总为 ${rowCount} 行 × ${columnCount} 列交叉表`
    : `原始 ${rawRowCount} 条明细，汇总为 ${rowCount} 行`;
}

const s2Totals = (totals: PivotLayout['totals']) => {
  const map = (config: PivotTotalsConfig) => ({
    ...config,
    calcTotals: config.calcTotals?.aggregation
      ? { aggregation: config.calcTotals.aggregation.toUpperCase() }
      : undefined,
    calcSubTotals: config.calcSubTotals?.aggregation
      ? { aggregation: config.calcSubTotals.aggregation.toUpperCase() }
      : undefined,
  });
  return { row: map(totals.row), col: map(totals.col) };
};

function PivotTableView({ result, pivot }: { result: CanonicalResult; pivot: PivotLayout }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [rendered, setRendered] = useState(false);
  const rows = result.rows ?? [];
  const sliced = useMemo(() => rows.slice(0, PIVOT_MAX_ROWS), [rows]);

  useEffect(() => {
    let disposed = false;
    let instance: { destroy: () => void } | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let themeObserver: MutationObserver | null = null;
    let removeResolutionListener: (() => void) | undefined;
    // 失败状态必须随新结果重置：否则一次渲染失败后，之后所有透视表都顶着「已回退」黄条，只能刷新页面恢复。
    setFailure(null);
    setRendered(false);

    const bootstrap = async () => {
      const node = containerRef.current;
      if (!node) return;
      try {
        // Loaded on demand: S2 brings its own canvas stack, and this file is
        // pre-rendered on the server where no DOM exists.
        const { PivotSheet } = await import('@antv/s2');
        if (disposed || !containerRef.current) return;
        const width = Math.max(node.clientWidth || 640, 160);
        const pixelRatio = () => Math.max(1, window.devicePixelRatio || 1);
        let previousRatio = pixelRatio();
        const expectedRows = pivot.cardinality?.rows || sliced.length;
        const height = Math.min(
          PIVOT_MAX_HEIGHT,
          Math.max(PIVOT_MIN_HEIGHT, expectedRows * PIVOT_ROW_HEIGHT + 120),
        );
        const dataCfg = {
          fields: {
            rows: pivot.rows,
            columns: pivot.columns,
            values: pivot.values,
            valueInCols: pivot.valueInCols,
          },
          // meta 里给维度与度量都配上中文表头：只写 values 会让行/列维度顶着
          // region / product_category 这类英文列名（2026-09-17）。
          meta: [
            ...[...pivot.rows, ...pivot.columns].map(field => ({
              field,
              name: columnLabelOf(result.column_labels, field),
            })),
            ...pivot.values.map(field => ({
              field,
              name: columnLabelOf(result.column_labels, field),
              formatter: (value: unknown) => formatPivotValue(value),
            })),
          ],
          data: sliced,
        };
        // Only the fields below are relied upon, so the local shapes stay
        // independent of the exact S2 option typings.
        const options = {
          width,
          height,
          devicePixelRatio: previousRatio,
          hdAdapter: false,
          hierarchyType: pivot.hierarchyType,
          totals: s2Totals(pivot.totals),
          tooltip: { enable: false },
          interaction: { enableCopy: true, hoverHighlight: true },
        };
        const sheet = new PivotSheet(node, dataCfg as never, options as never);
        instance = sheet;
        // S2 1.55 uses G Canvas, whose default rounds fractional DPR up.
        // Keep this canvas at the iframe's actual resolution through zoom and resize.
        const canvas = sheet.container;
        const initialRatio = canvas.getPixelRatio();
        canvas.getPixelRatio = pixelRatio;
        if (initialRatio !== previousRatio) canvas.changeSize(width, height);
        const syncTheme=()=>{
        const style=getComputedStyle(node);
        const token=(name:string,fallback:string)=>style.getPropertyValue(name).trim()||fallback;
        const background=token('--dsw-alias-bg-layer-1','#fff');
        const foreground=token('--dsw-alias-label-primary','#25272a');
        const border=token('--dsw-alias-border-l2','#e5e5e5');
        const cell={cell:{backgroundColor:background,crossBackgroundColor:background,horizontalBorderColor:border,verticalBorderColor:border},text:{fill:foreground},bolderText:{fill:foreground},measureText:{fill:foreground},seriesText:{fill:foreground}};
        sheet.setThemeCfg({name:'gray',theme:{background:{color:background},dataCell:cell,rowCell:cell,colCell:cell,cornerCell:cell}});
        };
        syncTheme();
        await sheet.render();
        if (disposed) return;
        setRendered(true);
        let previousWidth = width;
        const syncSize = () => {
          if (disposed || !node.clientWidth) return;
          const nextWidth = Math.max(node.clientWidth, 160);
          const nextRatio = pixelRatio();
          if (nextWidth !== previousWidth) {
            sheet.changeSheetSize(nextWidth, height);
          } else if (nextRatio !== previousRatio) {
            canvas.changeSize(nextWidth, height);
          } else {
            return;
          }
          previousWidth = nextWidth;
          previousRatio = nextRatio;
          void sheet.render();
        };
        if (typeof ResizeObserver !== 'undefined') {
          resizeObserver = new ResizeObserver(syncSize);
          resizeObserver.observe(node);
        }
        const watchResolution = () => {
          removeResolutionListener?.();
          const media = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
          const changed = () => { watchResolution(); syncSize(); };
          media.addEventListener('change', changed);
          removeResolutionListener = () => media.removeEventListener('change', changed);
        };
        watchResolution();
        syncSize();
        themeObserver=new MutationObserver(()=>{syncTheme();void sheet.render();});
        themeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['style','class']});
      } catch (error) {
        if (!disposed) {
          setFailure(error instanceof Error ? error.message : '透视表渲染失败');
        }
      }
    };

    bootstrap();
    return () => {
      disposed = true;
      resizeObserver?.disconnect();themeObserver?.disconnect();
      removeResolutionListener?.();
      try {
        instance?.destroy();
      } catch {
        // A half-initialised sheet must never take the page down with it.
      }
      instance = null;
      if (containerRef.current) containerRef.current.innerHTML = '';
    };
  }, [pivot, sliced]);

  if (failure) {
    return (
      <Fragment>
        <div className='product-pivot-fallback' role='alert'>
          <WarningOutlined />
          <span>透视表渲染失败，已回退为明细表格：{failure}</span>
        </div>
        <TableView result={result} />
      </Fragment>
    );
  }

  return (
    <div className='product-pivot-wrap' data-chart-loading={rendered ? undefined : ''}>
      {(pivot.note || rows.length > PIVOT_MAX_ROWS) && (
        <div className='product-pivot-note'>
          {pivot.note || `共 ${rows.length} 行，透视表仅渲染前 ${PIVOT_MAX_ROWS} 行`}
        </div>
      )}
      <div ref={containerRef} className='product-pivot-canvas' />
    </div>
  );
}

function EmptyVisual({ note }: { note: string }) {
  return (
    <div className='product-visual-empty'>
      <BarChartOutlined />
      <strong>当前结果不适合绘图</strong>
      <span>{note} 表格结果仍可查看与导出。</span>
    </div>
  );
}

function SelectedBars({result, encoding, vertical, layout}: {
  result: CanonicalResult;
  encoding: ChartSpec['encoding'];
  vertical: boolean;
  layout?: string;
}) {
  const measures=yFields(encoding);
  if(layout==='scalar-bars'||layout==='measure-panels'){
    return <div className='product-measure-bars'>
      {measures.map(measure=><section className='product-measure-bars-panel' key={measure.field}>
        {(layout==='measure-panels'||encoding.x)&&measures.length>1&&<h4>{measure.label||measure.field}</h4>}
        <BarView result={result} encoding={{x:encoding.x,y:measure}} vertical={vertical}
          scalar={layout==='scalar-bars'}/>
      </section>)}
    </div>;
  }
  return <BarView result={result} encoding={encoding} vertical={vertical}/>;
}

interface ProductVisualizationProps {
  result: CanonicalResult;
  visualization?: ChartSpec | null;
  runId?: string;
}

function ProductVisualization({
  result,
  visualization,
  runId,
}: ProductVisualizationProps) {
  const spec: ChartSpec | null = useMemo(() => visualization ?? result.chart ?? null, [visualization, result.chart]);
  const chartType = spec?.type ?? 'table';
  const tableView = chartType === 'table' || chartType === 'alert-table' || (chartType === 'pivot-table' && !spec?.pivot);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [horizontalOverflow, setHorizontalOverflow] = useState(false);
  useEffect(() => {
    const root = viewportRef.current;
    if (!root) return;
    let frame = 0;
    let scrollboxes: Element[] = [];
    const schedule = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!root.clientWidth) return;
        setHorizontalOverflow(scrollboxes.some(node => node.scrollWidth > node.clientWidth + 1));
      });
    };
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    const observeContent = () => {
      scrollboxes = [root, ...root.querySelectorAll('.product-bar-wrap,.product-table-wrap,.product-pivot-wrap')];
      resize?.disconnect();
      // A scrollbox can keep its own width while its table or chart grows.
      for (const box of scrollboxes) {
        resize?.observe(box);
        for (const content of box.children) resize?.observe(content);
      }
      schedule();
    };
    observeContent();
    // The asynchronous pivot can replace its top-level canvas with a table.
    const pivot = chartType === 'pivot-table' && typeof MutationObserver !== 'undefined'
      ? new MutationObserver(observeContent) : null;
    pivot?.observe(root, {childList:true});
    if (!resize) window.addEventListener('resize', schedule);
    document.fonts?.addEventListener?.('loadingdone', schedule);
    return () => {
      cancelAnimationFrame(frame);
      resize?.disconnect();
      pivot?.disconnect();
      if (!resize) window.removeEventListener('resize', schedule);
      document.fonts?.removeEventListener?.('loadingdone', schedule);
    };
  }, [chartType, result, spec]);
  const originalEncoding = spec?.encoding ?? {};
  const originalMetrics = yFields(originalEncoding);
  const filteredMetrics = originalMetrics.some(isPercentageMetric)
    ? originalMetrics.filter(m => !/^(?:total_)?(?:batches|samples?|sample_count|record_count|count|denominator|numerator)$/i.test(m.field))
    : originalMetrics;
  const hideAuxiliary = chartType !== 'gauge' && !['table','alert-table','pivot-table'].includes(chartType) && filteredMetrics.length < originalMetrics.length;
  const encoding = hideAuxiliary ? {...originalEncoding,y:filteredMetrics} : originalEncoding;
  const disclosure = spec?.disclosure;
  // 口径说明按当前形态取：透视表视图不能沿用原图表那份 topN 文案（用户从类型胶囊
  // 切过来时 spec.disclosure 还是柱状图/热力图留下的「仅显示前 12 项（未排序）」）。
  const disclosureNote =
    chartType === 'pivot-table' && spec?.pivot
      ? pivotDisclosureNote(spec.pivot, (result.rows ?? []).length)
      : disclosure?.topN
        ? undefined // Historical recommendations predate complete, scrollable rendering.
        : disclosure?.note;

  const renderChart = () => {
    switch (chartType) {
      case 'kpi':
        return <KpiView result={result} encoding={encoding} />;
      case 'bar':
        return <SelectedBars result={result} encoding={encoding} vertical={false} layout={spec?.layout}/>;
      case 'column':
        return <SelectedBars result={result} encoding={encoding} vertical layout={spec?.layout}/>;
      case 'grouped-bar':
        return <GroupedBarView result={result} encoding={encoding} />;
      case 'stacked-bar':
        return <GroupedBarView result={result} encoding={encoding} stacked />;
      case 'percent-stacked-bar':
        return <GroupedBarView result={result} encoding={encoding} normalized />;
      case 'line':
        return <LineAreaView result={result} encoding={encoding} />;
      case 'area':
        return <LineAreaView result={result} encoding={encoding} area />;
      case 'pie':
        return <PieDonutView result={result} encoding={encoding} />;
      case 'donut':
        return <PieDonutView result={result} encoding={encoding} donut />;
      case 'scatter':
        return <ScatterBubbleView result={result} encoding={encoding} />;
      case 'bubble':
        return <ScatterBubbleView result={result} encoding={encoding} bubble />;
      case 'heatmap':
        return <HeatmapView result={result} encoding={encoding} />;
      case 'treemap':
        return <TreemapView result={result} encoding={encoding} />;
      case 'histogram':
        return <HistogramView result={result} encoding={encoding} />;
      case 'radar':
        return <RadarView result={result} encoding={encoding} />;
      case 'funnel':
        return <FunnelView result={result} encoding={encoding} />;
      case 'gauge':
        return <GaugeView result={result} encoding={encoding} />;
      case 'waterfall':
        return <WaterfallView result={result} encoding={encoding} />;
      case 'boxplot':
        return <BoxplotView result={result} encoding={encoding} />;
      case 'alert-table':
        return <TableView result={result} alertRules={spec?.alertRules} />;
      case 'pivot-table':
        // Without a server-decided layout there is nothing to pivot on.
        return spec?.pivot ? (
          <PivotTableView result={result} pivot={spec.pivot} />
        ) : (
          <TableView result={result} />
        );
      case 'table':
      default:
        return <TableView result={result} />;
    }
  };

  return (
    <div className='product-visualization'>
      {/* 标题由外层图表卡统一承载（段一 result-card-title / 段四分节标题），此处只
          保留数据口径说明，避免同一标题渲染两遍。图表类型由后端裁决；用户可从
          类型胶囊手工切换，切换后口径说明跟着当前形态走（见 disclosureNote）。 */}
      {disclosureNote && (
        <div className='product-visualization-heading'>
          <span className='product-disclosure-note'>{disclosureNote}</span>
        </div>
      )}
      {horizontalOverflow&&<p className='nw-scrolling-note'>左右滚动查看完整图表</p>}
      <div ref={viewportRef} className={`nw-chart-viewport${tableView ? ' nw-chart-viewport--table' : ''}`}
        role={tableView ? undefined : 'region'} aria-label={tableView ? undefined : '图表内容，可横向滚动查看完整数据'}
        tabIndex={tableView ? undefined : 0}>{renderChart()}</div>
      {hideAuxiliary && <p className='product-disclosure-note'>图表展示比率指标；样本数等计算依据保留在数据页。</p>}
    </div>
  );
}

export default memo(ProductVisualization);
