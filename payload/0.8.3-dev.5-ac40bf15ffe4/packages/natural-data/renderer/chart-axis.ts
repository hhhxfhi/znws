import { chinesePeriodLabel } from '../result-labels.js';

type PeriodGrain = 'year' | 'quarter' | 'month' | 'day';
type AxisField = { field: string; label?: string; grain?: PeriodGrain; part?: boolean };

export interface AxisPadding {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface AxisTick {
  index: number;
  rawLabel: string;
  formattedLabel: string;
  displayLabel: string;
  x: number;
  rawX: number;
  width: number;
  textAnchor: 'start' | 'middle' | 'end';
}

const ORDERED_FIELD_HINT =
  /(date|time|day|week|month|quarter|year|period|dt|ym|时间|日期|日|周|月|季|年|期)/i;
const BUCKET_DATE = /^(\d{4})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])(?:[T ]00:00(?::00(?:\.0+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?$/;
const QUARTERS = ['一', '二', '三', '四'];

export function isPeriodGrain(value: unknown): value is PeriodGrain {
  return value === 'year' || value === 'quarter' || value === 'month' || value === 'day';
}

/** Interpret only complete labels for a proved period. Keep irregular values visible. */
export function formatVerifiedPeriod(label: string, grain: PeriodGrain): string {
  if (grain === 'day') return label;
  const date = BUCKET_DATE.exec(label);
  if (grain === 'year') {
    const year = /^\d{4}$/.test(label) ? label : date?.[2] === '01' && date[3] === '01' ? date[1] : '';
    return year ? `${year}年` : label;
  }
  if (grain === 'quarter') {
    const quarter = /^(\d{4})(?:-Q|0?)([1-4])$/i.exec(label);
    if (quarter) return `${quarter[1]}年第${QUARTERS[Number(quarter[2]) - 1]}季度`;
    if (date?.[3] === '01' && ['01', '04', '07', '10'].includes(date[2])) {
      return `${date[1]}年第${QUARTERS[(Number(date[2]) - 1) / 3]}季度`;
    }
    return label;
  }
  const month = /^(\d{4})[-/]?(0[1-9]|1[0-2])$/.exec(label) || (date?.[3] === '01' ? date : null);
  return month ? `${month[1]}年${Number(month[2])}月` : label;
}

/** 维度值归一化 */
export const dimensionLabel = (value: unknown): string => String(value ?? '');

/** 格式化维度显示标签 */
export function displayDimension(label: string, ref?: AxisField): string {
  if (label === '') return '（空）';
  if (ref?.part) return label;
  if (isPeriodGrain(ref?.grain)) return formatVerifiedPeriod(label, ref.grain);
  return ref && ORDERED_FIELD_HINT.test(`${ref.field} ${ref.label ?? ''}`) ? chinesePeriodLabel(label) : label;
}

/** 标签截断 */
export const truncateLabel = (label: string, limit = 10): string =>
  label.length > limit ? `${label.slice(0, Math.max(limit - 1, 1))}…` : label;

/**
 * 估算文本在指定字号下的像素宽度。
 * CJK 表意字符按 1em (fontSize) 计算，英文字母与数字按比例计算。
 */
export function estimateTextWidth(text: string, fontSize = 11): number {
  if (!text) return 0;
  let width = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    // CJK 表意字符、扩展区及全角标点
    if (
      (code >= 0x4e00 && code <= 0x9fff) ||
      (code >= 0x3400 && code <= 0x4dbf) ||
      (code >= 0xf900 && code <= 0xfaff) ||
      (code >= 0xff00 && code <= 0xffef) ||
      (code >= 0x3000 && code <= 0x303f) ||
      (code >= 0x20000 && code <= 0x2a6df)
    ) {
      width += fontSize * 1.0;
    } else if (/[A-Z]/.test(text[i])) {
      width += fontSize * 0.72;
    } else if (/[a-z0-9]/.test(text[i])) {
      width += fontSize * 0.62;
    } else if (/[-_/:.]/.test(text[i])) {
      width += fontSize * 0.45;
    } else {
      width += fontSize * 0.62;
    }
  }
  return Math.ceil(width);
}

let sharedCanvasContext: CanvasRenderingContext2D | null = null;

/**
 * 重置画布度量上下文缓存（供测试使用）
 */
export function resetFontCache(): void {
  sharedCanvasContext = null;
}

/**
 * 获取与 SVG 实际 CSS 规则对齐的字体样式字符串。
 * 若传入具体实例 element，从该实例读取 computedStyle，绝不在 render 内插入 body 探针。
 */
export function getSvgAxisTickFont(customFontSize?: number, element?: Element | null): string {
  if (element && typeof window !== 'undefined' && window.getComputedStyle) {
    try {
      const target = element.querySelector<SVGTextElement>('text.axis-tick-x') || element;
      const cs = window.getComputedStyle(target);
      const weight = cs.fontWeight || '500';
      const size = cs.fontSize || `${customFontSize ?? 11}px`;
      const family =
        cs.fontFamily ||
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
      return `${weight} ${size} ${family}`;
    } catch {}
  }
  const size = customFontSize ?? 11;
  return `500 ${size}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
}

/**
 * 测量文本实际宽度。在浏览器环境下采用一致的 fontStyle 测量，
 * 非浏览器环境或异常时回退到确定性 estimateTextWidth。
 */
export function measureTextWidth(
  text: string,
  fontSize?: number,
  fontStyle?: string,
): number {
  if (!text) return 0;
  if (typeof document !== 'undefined') {
    try {
      if (!sharedCanvasContext) {
        const canvas = document.createElement('canvas');
        sharedCanvasContext = canvas.getContext('2d');
      }
      if (sharedCanvasContext) {
        sharedCanvasContext.font = fontStyle || getSvgAxisTickFont(fontSize);
        const metrics = sharedCanvasContext.measureText(text);
        if (metrics && metrics.width > 0) {
          return Math.ceil(metrics.width);
        }
      }
    } catch {}
  }

  // 非浏览器环境或测量异常：优先从 fontStyle 中解析字号
  let effectiveFontSize = fontSize ?? 11;
  if (fontStyle) {
    const matched = fontStyle.match(/\b(\d+(?:\.\d+)?)px\b/);
    if (matched) {
      const parsed = parseFloat(matched[1]);
      if (!isNaN(parsed) && parsed > 0) {
        effectiveFontSize = parsed;
      }
    }
  }
  return estimateTextWidth(text, effectiveFontSize);
}

/**
 * 计算刻度在经过两端边界防溢出夹取后的最终渲染 X 坐标。
 */
export function computeFinalX(rawX: number, width: number, halfW: number): number {
  const minX = halfW + 4;
  const maxX = width - halfW - 4;
  if (minX >= maxX) {
    return Math.round(width / 2);
  }
  return Math.max(minX, Math.min(maxX, rawX));
}

/**
 * 通用趋势图/分类轴刻度选择算法（纯函数）。
 * 根据最终渲染坐标（包含两端夹取）与文本真实占位进行碰撞判定，
 * 消除首尾与相邻刻度碰撞，并最大化保留首尾关键语义。
 */
export function selectAxisTicks({
  labels,
  encodingX,
  width,
  padding,
  minGap = 12,
  fontSize,
  fontStyle,
  measureWidth,
}: {
  labels: string[];
  encodingX?: AxisField;
  width: number;
  padding: AxisPadding;
  minGap?: number;
  fontSize?: number;
  fontStyle?: string;
  measureWidth?: (text: string) => number;
}): AxisTick[] {
  const N = labels.length;
  if (N === 0) return [];

  const plotW = Math.max(width - padding.left - padding.right, 10);
  const scaleX = (index: number) =>
    padding.left + (N <= 1 ? plotW / 2 : (index / (N - 1)) * plotW);

  // Layout is deterministic without a browser. The component explicitly injects
  // its instance's canvas measurement; tests and SSR use a stable fallback.
  const resolvedFontSize = fontSize ?? Number(fontStyle?.match(/\b(\d+(?:\.\d+)?)px\b/)?.[1] ?? 11);
  const measure = measureWidth ?? ((text: string) => estimateTextWidth(text, resolvedFontSize));

  // 预先测量所有候选标签的尺寸
  const items = labels.map((label, index) => {
    const rawLabel = dimensionLabel(label);
    const formattedLabel = displayDimension(rawLabel, encodingX);
    const displayLabel = truncateLabel(formattedLabel, 10);
    const textWidth = measure(displayLabel);
    const rawX = scaleX(index);
    return {
      index,
      rawLabel,
      formattedLabel,
      displayLabel,
      rawX,
      width: textWidth,
    };
  });

  if (N === 1) {
    const item = items[0];
    const finalX = computeFinalX(item.rawX, width, item.width / 2);
    return [
      {
        ...item,
        x: finalX,
        textAnchor: 'middle',
      },
    ];
  }

  // 必须基于【最终渲染坐标】（经过边界夹取之后）严格检查是否碰撞
  const hasCollisionsOnFinalX = (indices: number[]): boolean => {
    for (let i = 0; i < indices.length - 1; i++) {
      const a = items[indices[i]];
      const b = items[indices[i + 1]];
      const finalXA = computeFinalX(a.rawX, width, a.width / 2);
      const finalXB = computeFinalX(b.rawX, width, b.width / 2);
      const rightA = finalXA + a.width / 2;
      const leftB = finalXB - b.width / 2;
      if (rightA + minGap > leftB) {
        return true;
      }
    }
    return false;
  };

  // 两点边界情况：在最终坐标上校验碰撞
  if (N === 2) {
    if (!hasCollisionsOnFinalX([0, 1])) {
      return ensureZeroCollisions(buildTicks([0, 1], items, width), minGap);
    }
    // 宽度极小导致首尾夹取后仍碰撞时，保留末尾最新点
    return buildTicks([1], items, width);
  }

  // 目标刻度数量根据绘图区像素宽度动态自适应
  const targetCount = Math.max(2, Math.min(8, Math.round(plotW / 85)));

  interface Candidate {
    indices: number[];
    stride: number;
    score: number;
  }

  const candidates: Candidate[] = [];

  // 遍历所有候选步长 s
  for (let s = 1; s < N; s++) {
    const list: number[] = [];
    for (let idx = 0; idx < N; idx += s) {
      list.push(idx);
    }

    const end = N - 1;
    if (list[list.length - 1] !== end) {
      // 在最终坐标上检查末尾相邻项与 end 是否碰撞，循环剔除冲突项
      while (list.length > 0) {
        const prev = list[list.length - 1];
        const finalXPrev = computeFinalX(items[prev].rawX, width, items[prev].width / 2);
        const finalXEnd = computeFinalX(items[end].rawX, width, items[end].width / 2);
        const rightPrev = finalXPrev + items[prev].width / 2;
        const leftEnd = finalXEnd - items[end].width / 2;

        if (rightPrev + minGap > leftEnd) {
          // 最终坐标发生重叠，剔除该项以给 end 腾出空间
          list.pop();
        } else {
          break;
        }
      }
      list.push(end);
    }

    // 检查首端在夹取后是否与后续项碰撞，循环剔除冲突项
    if (list.length > 1 && list[0] === 0) {
      while (list.length > 1) {
        const finalX0 = computeFinalX(items[0].rawX, width, items[0].width / 2);
        const finalXNext = computeFinalX(items[list[1]].rawX, width, items[list[1]].width / 2);
        const right0 = finalX0 + items[0].width / 2;
        const leftNext = finalXNext - items[list[1]].width / 2;
        if (right0 + minGap > leftNext) {
          list.splice(1, 1);
        } else {
          break;
        }
      }
    }

    if (list.length > 0 && !hasCollisionsOnFinalX(list)) {
      const count = list.length;
      const countDiff = Math.abs(count - targetCount);

      // 计算步长方差（均匀性）
      const gaps: number[] = [];
      for (let i = 0; i < list.length - 1; i++) {
        gaps.push(list[i + 1] - list[i]);
      }
      const meanGap = (N - 1) / Math.max(count - 1, 1);
      const variance =
        gaps.length > 0
          ? gaps.reduce((acc, g) => acc + (g - meanGap) ** 2, 0) / gaps.length
          : 0;

      let bonus = 0;
      if ((N - 1) % s === 0) bonus += 4;
      if ([1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20].includes(s)) bonus += 2;

      const score = bonus - countDiff * 3 - variance * 1.5;
      candidates.push({ indices: list, stride: s, score });
    }
  }

  // 选取最高评分方案
  if (candidates.length > 0) {
    candidates.sort((a, b) => b.score - a.score);
    return ensureZeroCollisions(buildTicks(candidates[0].indices, items, width), minGap);
  }

  // 极窄或极长情况：若 [0, N - 1] 在最终坐标下无碰撞则保留两端
  if (!hasCollisionsOnFinalX([0, N - 1])) {
    return ensureZeroCollisions(buildTicks([0, N - 1], items, width), minGap);
  }

  // 最终兜底：仅保留末尾最新点
  return buildTicks([N - 1], items, width);
}

/**
 * 安全保障：对构建出的 ticks 做最终确定性验证，
 * 从后往前遍历并绝对保留末尾最新刻度，确保最终坐标绝对不发生重叠。
 */
function ensureZeroCollisions(ticks: AxisTick[], minGap: number): AxisTick[] {
  if (ticks.length <= 1) return ticks;
  const result: AxisTick[] = [ticks[ticks.length - 1]];
  for (let i = ticks.length - 2; i >= 0; i--) {
    const current = ticks[i];
    const next = result[0];
    const rightCurrent = current.x + current.width / 2;
    const leftNext = next.x - next.width / 2;
    if (rightCurrent + minGap <= leftNext) {
      result.unshift(current);
    }
  }
  return result;
}

/**
 * 依据索引列表与最终渲染坐标构建渲染刻度对象。
 */
function buildTicks(
  indices: number[],
  items: Array<{
    index: number;
    rawLabel: string;
    formattedLabel: string;
    displayLabel: string;
    rawX: number;
    width: number;
  }>,
  width: number,
): AxisTick[] {
  return indices.map(idx => {
    const item = items[idx];
    const halfW = item.width / 2;
    const x = computeFinalX(item.rawX, width, halfW);

    return {
      index: item.index,
      rawLabel: item.rawLabel,
      formattedLabel: item.formattedLabel,
      displayLabel: item.displayLabel,
      x,
      rawX: item.rawX,
      width: item.width,
      textAnchor: 'middle',
    };
  });
}
