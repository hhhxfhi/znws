"""Deterministic data feature computation between the DataFrame and the LLM.

The Text2SQL result is reduced to a compact set of computed features before any
LLM explanation happens. The model never has to do arithmetic itself: it only
interprets these numbers. This keeps the text analysis grounded in the actual
result and stable across model changes.

Features computed:
* row/column shape,
* per-numeric-column total / mean / min / max,
* top-N categories by count and by a chosen numeric metric (with share %),
* time trend when a datetime column exists (first vs last, delta %, direction),
* period comparisons when the data carries consecutive periods: month-over-month
  and year-over-year (the latter only when the same month of the previous year is
  actually present in the result).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from .charts import _column_kind, _parse_datetime
from .metric_semantics import is_additive, number

_TOP_DEFAULT = 5

# 算子指标的中文标签（``metric_label``）与元数据键（``META_KEYS``）定义在算子库
# ``skills/operators.py``，渲染处**延迟导入**——本模块被 ``skills.dynamic`` 导入，
# 若在模块顶层反向导入 ``skills`` 包会形成循环依赖。标签只维护一份，避免
# 「prompt 里的说法」与「降级输出里的说法」漂移。


def _num(value: Any) -> Optional[float]:
    return number(value)


def _fmt(value: float) -> str:
    if value != value:  # NaN
        return "—"
    if abs(value) >= 10000:
        return f"{value:,.0f}"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _period_key(value: Any) -> Optional[str]:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}"


def compute_features(
    columns: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    *,
    top_n: int = _TOP_DEFAULT,
) -> Dict[str, Any]:
    """Return a compact JSON-friendly feature report for the result set."""
    if not columns or not rows:
        return {"row_count": len(rows), "column_count": len(columns), "empty": True}

    kinds = {column: _column_kind(column, [row.get(column) for row in rows]) for column in columns}
    numeric_cols = [column for column in columns if kinds[column] == "numeric"]
    datetime_cols = [column for column in columns if kinds[column] == "datetime"]
    categorical_cols = [column for column in columns if kinds[column] == "categorical"]

    report: Dict[str, Any] = {
        "row_count": len(rows),
        "column_count": len(columns),
        "columns": list(columns),
        "kinds": kinds,
    }

    # -- numeric stats --------------------------------------------------------
    numeric_stats = []
    for column in numeric_cols[:6]:
        values = [_num(row.get(column)) for row in rows]
        values = [value for value in values if value is not None]
        if not values:
            continue
        total = sum(values)
        average = total / len(values)
        minimum, maximum = min(values), max(values)
        label = _category_label_for(categorical_cols, rows, maximum, column)
        min_label = _category_label_for(categorical_cols, rows, minimum, column)
        numeric_stats.append(
            {
                "column": column,
                "total": total if is_additive(column) else None,
                "aggregation": "sum" if is_additive(column) else "row_mean",
                "mean": average,
                "min": minimum,
                "min_label": min_label,
                "max": maximum,
                "max_label": label,
            }
        )
    if numeric_stats:
        report["numeric_stats"] = numeric_stats

    # -- top-N by count and by metric -----------------------------------------
    top = []
    if categorical_cols:
        cat = categorical_cols[0]
        counts: Dict[str, int] = {}
        for row in rows:
            value = row.get(cat)
            key = str(value) if value is not None and str(value).strip() else "(空)"
            counts[key] = counts.get(key, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        top.append(
            {
                "column": cat,
                "mode": "count",
                "items": [{"label": label, "value": count} for label, count in ordered],
                "total": len(rows),
            }
        )
        if numeric_cols and len(counts) >= 2 and is_additive(numeric_cols[0]):
            metric = numeric_cols[0]
            by_metric: Dict[str, float] = {}
            for row in rows:
                value = row.get(cat)
                key = str(value) if value is not None and str(value).strip() else "(空)"
                metric_value = _num(row.get(metric))
                if metric_value is not None:
                    by_metric[key] = by_metric.get(key, 0.0) + metric_value
            all_ordered_metric = sorted(by_metric.items(), key=lambda kv: (-kv[1], kv[0]))
            ordered_metric = all_ordered_metric[:top_n]
            total_metric = sum(by_metric.values()) or 1.0
            min_item = None
            if len(all_ordered_metric) > 1:
                last_label, last_val = all_ordered_metric[-1]
                min_item = {
                    "label": last_label,
                    "value": last_val,
                    "share_pct": round(last_val / total_metric * 100, 1),
                }
            top_item: Dict[str, Any] = {
                "column": cat,
                "mode": "metric",
                "metric": metric,
                "items": [
                    {
                        "label": label,
                        "value": value,
                        "share_pct": round(value / total_metric * 100, 1),
                    }
                    for label, value in ordered_metric
                ],
                "total": total_metric,
            }
            if min_item:
                top_item["min_item"] = min_item
            top.append(top_item)
    if top:
        report["top"] = top

    # -- time trend -----------------------------------------------------------
    if datetime_cols and numeric_cols and is_additive(numeric_cols[0]):
        trend = _time_trend(datetime_cols[0], numeric_cols[0], rows)
        if trend is not None:
            report["trend"] = trend

    # -- period comparisons (MoM / YoY) --------------------------------------
    if datetime_cols and numeric_cols and is_additive(numeric_cols[0]):
        comparisons = _period_comparisons(datetime_cols[0], numeric_cols[0], rows)
        if comparisons:
            report["comparisons"] = comparisons

    return report


def _category_label_for(
    categorical_cols: Sequence[str], rows: Sequence[Dict[str, Any]], value: float, metric: str
) -> Optional[str]:
    """Find the category label whose metric value equals ``value`` (for max)."""
    if not categorical_cols:
        return None
    cat = categorical_cols[0]
    for row in reversed(rows):
        if _num(row.get(metric)) == value:
            raw = row.get(cat)
            if raw is not None and str(raw).strip():
                return str(raw)
    return None


def _time_points(
    time_col: str, metric_col: str, rows: Sequence[Dict[str, Any]]
) -> List[tuple]:
    points = []
    for row in rows:
        parsed = _parse_datetime(row.get(time_col))
        value = _num(row.get(metric_col))
        if parsed is None or value is None:
            continue
        points.append((parsed, value))
    return points


def _granularity(parsed_values: Sequence[datetime]) -> str:
    """时间列粒度：全为 1 月 1 日 -> 年；全为某月 1 日 -> 月；否则日。

    粒度决定两件事：聚合键（年 ``2015`` / 月 ``2015-03`` / 日 ``2015-03-04``）和
    能不能说「环比」——年度数据上的环比（相邻两个月）是错的，只有同比成立。
    """
    if all(point.month == 1 and point.day == 1 for point in parsed_values):
        return "year"
    if all(point.day == 1 for point in parsed_values):
        return "month"
    return "day"


def _period_label(parsed: datetime, granularity: str) -> str:
    if granularity == "year":
        return f"{parsed.year:04d}"
    if granularity == "month":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    return parsed.date().isoformat()


def _time_trend(
    time_col: str, metric_col: str, rows: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    points = _time_points(time_col, metric_col, rows)
    if len(points) < 2:
        return None
    granularity = _granularity([parsed for parsed, _ in points])
    buckets: Dict[str, float] = {}
    for parsed, value in points:
        key = _period_label(parsed, granularity)
        buckets[key] = buckets.get(key, 0.0) + value
    ordered = sorted(buckets.items())
    if len(ordered) < 2:
        return None
    first_key, first_value = ordered[0]
    last_key, last_value = ordered[-1]
    delta = last_value - first_value
    delta_pct = (delta / first_value * 100) if first_value else None
    direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
    return {
        "time_column": time_col,
        "metric": metric_col,
        "granularity": granularity,
        "start": {"period": first_key, "value": first_value},
        "end": {"period": last_key, "value": last_value},
        "delta": delta,
        "delta_pct": delta_pct,
        "direction": direction,
    }


def _period_comparisons(
    time_col: str, metric_col: str, rows: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Month-over-month and year-over-year deltas, computed only when the data
    actually contains the previous period (MoM) / same month last year (YoY).

    年粒度（``stat_year`` 这类整数值年份）只有同比有意义：相邻两条记录本就相差
    一年，再叫它「环比」就是错的表述。所以年粒度只出同比。
    """

    points = _time_points(time_col, metric_col, rows)
    if len(points) < 2:
        return []
    granularity = _granularity([parsed for parsed, _ in points])
    monthly: Dict[str, float] = {}
    for parsed, value in points:
        key = (
            f"{parsed.year:04d}"
            if granularity == "year"
            else f"{parsed.year:04d}-{parsed.month:02d}"
        )
        monthly[key] = monthly.get(key, 0.0) + value

    ordered = sorted(monthly.items())
    if len(ordered) < 2:
        return []
    comparisons: List[Dict[str, Any]] = []

    # 环比：相邻两个月的对比（年粒度不成立，跳过）。
    current_key, current_value = ordered[-1]
    previous_key, previous_value = ordered[-2]
    if granularity != "year" and current_key != previous_key:
        delta = current_value - previous_value
        comparisons.append(
            {
                "type": "month_over_month",
                "metric": metric_col,
                "current": {"period": current_key, "value": current_value},
                "previous": {"period": previous_key, "value": previous_value},
                "delta": delta,
                "delta_pct": (delta / previous_value * 100) if previous_value else None,
            }
        )
    if granularity == "year":
        # 年粒度：相邻两年就是同比。
        if current_key != previous_key:
            delta = current_value - previous_value
            comparisons.append(
                {
                    "type": "year_over_year",
                    "metric": metric_col,
                    "current": {"period": current_key, "value": current_value},
                    "previous": {"period": previous_key, "value": previous_value},
                    "delta": delta,
                    "delta_pct": (delta / previous_value * 100) if previous_value else None,
                }
            )
        return comparisons

    # 同比：去年同月（仅当数据里真的有去年同月才计算）。
    current_year, current_month = (int(part) for part in current_key.split("-"))
    last_year_key = f"{current_year - 1:04d}-{current_month:02d}"
    if last_year_key in monthly and last_year_key != current_key:
        last_year_value = monthly[last_year_key]
        delta = current_value - last_year_value
        comparisons.append(
            {
                "type": "year_over_year",
                "metric": metric_col,
                "current": {"period": current_key, "value": current_value},
                "previous": {"period": last_year_key, "value": last_year_value},
                "delta": delta,
                "delta_pct": (delta / last_year_value * 100) if last_year_value else None,
            }
        )
    return comparisons


def render_features_zh(report: Dict[str, Any]) -> str:
    """Render the feature report as concise Chinese sentences.

    This is the deterministic analysis fallback AND the context text handed to
    the explanation LLM, so the numbers it mentions are exactly the numbers the
    model is expected to reason about.
    """

    if report.get("empty"):
        return "查询未返回数据。"
    parts = [f"共 {report.get('row_count', 0)} 行 {report.get('column_count', 0)} 列。"]
    for stat in report.get("numeric_stats", []):
        min_label_str = f"（{stat['min_label']}）" if stat.get("min_label") else ""
        max_label_str = f"（{stat['max_label']}）" if stat.get("max_label") else ""
        parts.append(
            f"{stat['column']}" + (f"合计 {_fmt(stat['total'])}，" if stat.get('total') is not None else "不可加总，") + f"结果行简单均值 {_fmt(stat['mean'])}，"
            f"最小 {_fmt(stat['min'])}{min_label_str}，最大 {_fmt(stat['max'])}{max_label_str}。"
        )
    for item in report.get("top", []):
        if item.get("mode") == "count":
            labels = "、".join(f"{i['label']}{i['value']}个" for i in item["items"][:3])
            parts.append(f"按{item['column']}分布：{labels} 等。")
        elif item.get("mode") == "metric":
            labels = "、".join(
                f"{i['label']}{_fmt(i['value'])}（{i['share_pct']}%）"
                for i in item["items"][:3]
            )
            min_suffix = ""
            if item.get("min_item"):
                m = item["min_item"]
                min_suffix = f"，最低为{m['label']}{_fmt(m['value'])}（{m['share_pct']}%）"
            parts.append(f"按{item['metric']}排序：{labels} 等{min_suffix}。")
    trend = report.get("trend")
    if trend:
        start, end = trend["start"], trend["end"]
        change = (
            f"增幅 {_fmt(trend['delta_pct'])}%" if trend["delta_pct"] is not None else ""
        )
        direction = {"up": "上升", "down": "下降", "flat": "持平"}[trend["direction"]]
        parts.append(
            f"{trend['metric']}从 {start['period']} 的 {_fmt(start['value'])} "
            f"{direction}至 {end['period']} 的 {_fmt(end['value'])}{change}。"
        )
    for comparison in report.get("comparisons", []):
        label = "环比" if comparison["type"] == "month_over_month" else "同比"
        current, previous = comparison["current"], comparison["previous"]
        change = (
            f"{_fmt(comparison['delta_pct'])}%"
            if comparison["delta_pct"] is not None
            else "—"
        )
        parts.append(
            f"{label}：{current['period']}（{_fmt(current['value'])}）较 "
            f"{previous['period']}（{_fmt(previous['value'])}）变动 {change}。"
        )
    # 自定义算子产出（``skills/operators.py``）必须在此渲染，否则算子"算了也白算"。
    # 2026-09-24 发现该缺口：distribution_profile 已启用中位数/标准差/偏度/分位数
    # 算子，但模型收到的是「标准差未提供」，只能改用基于排序的估算值——确定性
    # 指标被静默丢弃，正是本层要杜绝的失效模式。
    # 延迟导入算子库的标签与元数据键（见文件头部关于循环依赖的说明）
    from .skills.operators import META_KEYS, metric_label

    custom_metrics = report.get("custom_metrics") or {}
    rendered = [
        f"{metric_label(key)} {value}"
        for key, value in custom_metrics.items()
        if key not in META_KEYS and value is not None and str(value).strip()
    ]
    if rendered:
        block = (
            "算子指标（确定性精确值，引用时必须原样使用，"
            "禁止取整、禁止改写为「约/大约/估算」）："
            + "；".join(rendered)
            + "。"
        )
        # 前置到「共 N 行 M 列」之后：实测置于末尾时会被工序级长指令稀释，
        # 模型转而自行估算（中位数真值 95 被写成「约 100」，且与均值的大小关系写反）。
        parts = parts[:1] + [block] + parts[1:]
    return "".join(parts)
