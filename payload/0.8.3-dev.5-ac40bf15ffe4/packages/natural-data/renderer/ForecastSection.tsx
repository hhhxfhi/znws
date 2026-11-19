import React, {useEffect, useRef, useState} from 'react';
import {getSvgAxisTickFont, measureTextWidth, selectAxisTicks} from './chart-axis';
import {forecastBasisPresentation, forecastExclusionPresentation, forecastMethodPresentation} from '../forecast-presentation.js';
import type {ForecastView} from './forecast-model';
import {forecastAmountLabel, forecastErrorLabel, isCurrencyForecastUnit} from '../forecast-presentation.js';

const formatValue = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 6});
const formatTiny = new Intl.NumberFormat('zh-CN', {notation: 'scientific', maximumSignificantDigits: 6});
const formatAxis = new Intl.NumberFormat('zh-CN', {notation: 'compact', maximumFractionDigits: 2});
const numberLabel = (value: number) =>
  value !== 0 && Math.abs(value) < .000001 ? formatTiny.format(value) : formatValue.format(value);

export default function ForecastSection({forecast}: {forecast: ForecastView}) {
  const {history, predictions, model, methodExplanation, unit} = forecast;
  const money = isCurrencyForecastUnit(unit);
  const label = money ? forecastAmountLabel : numberLabel;
  const last = history[history.length - 1];
  const first = predictions[0], target = predictions[predictions.length - 1];
  const viewport = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [font, setFont] = useState<string>();

  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    let active = true;
    const measure = () => {
      if (!active) return;
      const available = Math.round(node.clientWidth);
      if (available > 0) setWidth(Math.max(300, available));
      setFont(getSvgAxisTickFont(11, node));
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(node);
    document.fonts?.ready.then(measure).catch(() => {});
    document.fonts?.addEventListener('loadingdone', measure);
    return () => {
      active = false;
      observer?.disconnect();
      document.fonts?.removeEventListener('loadingdone', measure);
    };
  }, []);

  const periods = [...history, ...predictions].map(point => point.period);
  const values = history.map(point => point.value).concat(predictions.flatMap(point => [point.value, ...point.interval]));
  const rawMin = Math.min(...values), rawMax = Math.max(...values);
  // Scale before subtracting so finite endpoints on both sides of zero cannot overflow.
  const magnitude = Math.max(Math.abs(rawMin), Math.abs(rawMax)) || 1;
  const spanMin = rawMin / magnitude, spanMax = rawMax / magnitude;
  const delta = spanMax === spanMin ? .2 : 0;
  const yMin = spanMin - delta, yMax = spanMax + delta;
  const padding = {left: money ? 83 : 54, right: 16, top: 14, bottom: 36};
  const height = 214, bottom = height - padding.bottom;
  const x = (i: number) => padding.left + (width - padding.left - padding.right) * i / (periods.length - 1);
  const y = (v: number) => bottom - (bottom - padding.top) * ((v / magnitude - yMin) / (yMax - yMin));
  const point = (i: number, v: number) => `${x(i)},${y(v)}`;
  const forecastPoints = predictions.map((p, i) => point(history.length + i, p.value));
  const upper = predictions.map((p, i) => point(history.length + i, p.interval[1]));
  const lower = predictions.map((p, i) => point(history.length + i, p.interval[0])).reverse();
  const ticks = selectAxisTicks({labels: periods, encodingX: {field: 'period'}, width, padding,
    measureWidth: font ? text => measureTextWidth(text, undefined, font) : undefined});
  const quantity = (v: number) => label(v) + (unit || '');
  const errorQuantity = (v: number) => forecastErrorLabel(v) + (unit || '');
  const observed = `${history[0].period} 至 ${last.period}`;

  return <section className="nw-forecast" aria-label="预测">
    <h3>预测结论</h3>
    <p className="nw-forecast-summary">
      <strong>{target.period} {forecast.measureLabel && `${forecast.measureLabel} `}预测值约 {quantity(target.value)}</strong>
      <span>95% 预测区间 {quantity(target.interval[0])} 至 {quantity(target.interval[1])}</span>
    </p>
    <p className="nw-forecast-meta">基于 {history.length} 期历史数据，采用{model}，{methodExplanation}。</p>
    {forecast.openYear && <p className="nw-forecast-meta">{forecast.openYear} 年尚未结年，图中为模型推算。训练截至 {last.period} 年。</p>}
    {money && target.interval[0] < 0 && target.interval[1] > 0 && <p className="nw-forecast-meta">预测区间跨过零，反映当前估算范围较宽。</p>}
    {target.interval[0] === target.interval[1] && <p className="nw-forecast-meta">预测区间两端在当前显示精度下重合。</p>}
    {forecast.backtestPoints === 1 && <p className="nw-forecast-meta">历史回测只有 1 期，预测区间仅供参考。</p>}
    {forecast.publicationLevel === 'reference_only' &&
      <p className="nw-forecast-meta nw-forecast-grade">试算参考：回测仅 1 个留出点，不作为可引用结论。</p>}
    <div className="nw-forecast-legend">
      <span><i className="nw-forecast-key-history" aria-hidden="true"/>历史实测</span>
      <span><i className="nw-forecast-key-prediction" aria-hidden="true"/>模型预测</span>
      <span><i className="nw-forecast-key-interval" aria-hidden="true"/>95% 预测区间</span>
    </div>
    <div className="nw-forecast-viewport" ref={viewport} tabIndex={0} role="region" aria-label="预测图表">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
           aria-label={`从 ${observed} 的历史实测预测到 ${target.period}${unit ? '，单位' + unit : ''}`}>
        {[0, .25, .5, .75, 1].map(ratio => {
          const position = padding.top + (bottom - padding.top) * ratio;
          const tickValue = (yMax - (yMax - yMin) * ratio) * magnitude;
          return <g key={ratio}>
            <line className="nw-forecast-grid" x1={padding.left} y1={position} x2={width - padding.right} y2={position}/>
            {Number.isFinite(tickValue) && <text x={padding.left - 8} y={position + 4} textAnchor="end">
              {money ? label(tickValue) : tickValue !== 0 && Math.abs(tickValue) < .000001 ? formatTiny.format(tickValue) : formatAxis.format(tickValue)}
            </text>}
          </g>;
        })}
        {forecast.backtestPoints > 0 && history.length > forecast.backtestPoints &&
          <rect className="nw-forecast-holdout" x={x(history.length - 1 - forecast.backtestPoints)} y={padding.top}
                width={x(history.length - 1) - x(history.length - 1 - forecast.backtestPoints)}
                height={bottom - padding.top}/>}
        {rawMin < 0 && rawMax > 0 && <line className="nw-forecast-zero" x1={padding.left} x2={width-padding.right} y1={y(0)} y2={y(0)}><title>零刻度</title></line>}
        <line className="nw-forecast-origin" x1={x(history.length - 1)} x2={x(history.length - 1)}
              y1={padding.top} y2={bottom}/>
        {predictions.length > 1
          ? <polygon className="nw-forecast-interval" points={[...upper, ...lower].join(' ')}/>
          : <line className="nw-forecast-single-interval" x1={x(history.length)} x2={x(history.length)}
                  y1={y(first.interval[0])} y2={y(first.interval[1])}/>}
        <polyline className="nw-forecast-history" points={history.map((p, i) => point(i, p.value)).join(' ')}/>
        {history.map((p, i) => <circle className="nw-forecast-history-point" key={p.period} cx={x(i)} cy={y(p.value)} r={2}>
          <title>{`${p.period} 实测 ${quantity(p.value)}`}</title></circle>)}
        <polyline className="nw-forecast-prediction" strokeDasharray="5 5"
                  points={[point(history.length - 1, last.value), ...forecastPoints].join(' ')}/>
        {predictions.map((p, i) => <circle className="nw-forecast-point" key={p.period}
          cx={x(history.length + i)} cy={y(p.value)} r={3}>
          <title>{`${p.period} 预测 ${quantity(p.value)}，区间 ${quantity(p.interval[0])} 至 ${quantity(p.interval[1])}`}</title>
        </circle>)}
        {ticks.map(tick => <text key={tick.index} className="nw-forecast-axis-tick axis-tick-x"
          x={tick.x} y={bottom + 18} textAnchor={tick.textAnchor}>
          {tick.displayLabel}<title>{tick.formattedLabel}</title>
        </text>)}
      </svg>
    </div>
    <details className="nw-forecast-details">
      <summary>查看各期预测和回测</summary>
      <p className="nw-forecast-meta">
        历史 {observed} · 回测 {forecast.backtestPoints} 期
        {unit ? ` · 单位 ${unit}` : ' · 数值单位未登记'}
      </p>
      <p className="nw-forecast-meta">平均绝对误差 {errorQuantity(forecast.mae)}，均方根误差 {errorQuantity(forecast.rmse)}。</p>
      {forecast.sampleNote && <p className="nw-forecast-meta">{forecast.sampleNote}</p>}
      {forecast.basis && <p className="nw-forecast-meta">区间依据：{forecastBasisPresentation(forecast.basis) ?? forecast.basis}</p>}
      {forecast.candidates.length > 0 && <p className="nw-forecast-meta">回测候选：{forecast.candidates
        .map(c => forecastMethodPresentation(c.model)?.name ?? c.model).join('、')}</p>}
      {forecast.excluded.length > 0 && <p className="nw-forecast-meta">排除：{forecast.excluded.map(e => {
        const m = forecastMethodPresentation(e.model), r = forecastExclusionPresentation(e.reason);
        return m && r ? `${m.name}（${r}）` : null;
      }).filter(Boolean).join('、')}</p>}
      {forecast.steps > 1 && <p className="nw-forecast-meta">{forecast.horizon
        ? `多步回测（${forecast.horizon.horizon_steps}步）留出${forecast.horizon.rolling_origins}期，平均绝对误差${errorQuantity(forecast.horizon.mae)}。`
        : `历史不足以做同模型${forecast.steps}步回测，选模依据单步回测。`}</p>}
      <div className="product-table-wrap">
        <table>
          <thead><tr><th>期间</th><th>预测值{unit && `（${unit}）`}</th><th>95% 预测区间{unit && `（${unit}）`}</th></tr></thead>
          <tbody>{predictions.map(p => <tr key={p.period}>
            <th scope="row">{p.period}</th><td>{label(p.value)}</td>
            <td>{p.interval.map(label).join(' 至 ')}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </section>;
}
