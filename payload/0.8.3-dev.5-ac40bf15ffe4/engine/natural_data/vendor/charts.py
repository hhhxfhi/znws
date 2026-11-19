"""Deterministic chart recommendation for ordinary query results.

The server produces a validated ``CanonicalChartV4`` display spec for a table
result.  The model only expresses an *intent* through ``expected_chart.type``;
the field references, series, alternatives and disclosures here are derived
exclusively from the actual returned columns and rows so that a result can never
be misrepresented by a chart that its data shape does not support.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .pivot import build_pivot_spec, pivot_hint, _cardinality

logger = logging.getLogger(__name__)

CHART_TYPES = (
    "kpi",
    "bar",
    "column",
    "grouped-bar",
    "stacked-bar",
    "percent-stacked-bar",
    "line",
    "area",
    "pie",
    "donut",
    "scatter",
    "bubble",
    "heatmap",
    "treemap",
    "histogram",
    "radar",
    "funnel",
    "gauge",
    "waterfall",
    "boxplot",
    "alert-table",
    "table",
    "pivot-table",
)

# Cap on the number of category rows a pie/donut may meaningfully encode.
# Unified with analysis_matrix._MAX_PIE_CARDINALITY and viz_rules (both 6).
PIE_CATEGORY_LIMIT = 6

# 用户显式要求饼图时的放宽上限。P 类可读性偏好不得否决显式指定，所以此处只
# 在小扇区确实不可读（> 12）时才降级，且降级目标不再是静默换图（见
# ``_demoted_family``）。取 12 与柱图 Top-N 口径一致（``_disclosure`` 默认 12）。
PIE_CATEGORY_LIMIT_EXPLICIT = 12

# 0.8.0 F4：默认图形的系列数可读性上限（与饼图 6 类口径一致）。趋势/分组对比
# 超过 6 条系列时，一张图无法辨读，默认改为表格（附透视表候选）；这是 P 类
# 可读性偏好，只约束默认与模型推断（L2/L3/L4），用户显式指定（L1）仍然优先。
SERIES_READABILITY_LIMIT = 6

# Chart families the rule engine understands for tabular results. ``preferred_family``
# (⑥ matrix preference) must be one of these to influence the recommendation.
_TABLE_FAMILIES = {
    "kpi", "bar", "line", "area", "pie", "scatter", "grouped-bar",
    "stacked-bar", "percent-stacked-bar", "heatmap", "treemap", "histogram",
    "radar", "funnel", "gauge", "waterfall", "boxplot", "table",
    "pivot-table",
}

# Common metric units, keyed by column name.
_UNITS: Dict[str, str] = {
    "poi_count": "个",
    "road_km": "公里",
    "area_km2": "平方公里",
    "road_density": "公里/平方公里",
    "count": "个",
}

# Question phrases that hint at composition / proportion.
_PROPORTION_HINTS = (
    "占比", "构成", "比例", "份额", "分布", "百分比", "组成",
    "composition", "share", "proportion", "ratio", "breakdown",
)

# Question phrases that hint at a cumulative / stacked meaning.
_STACK_HINTS = ("累计", "堆积", "合计", "堆叠", "累加", "cumulative", "stacked")

# Question phrases that hint at a 100% normalized stacked meaning.
_PERCENT_STACK_HINTS = (
    "占比", "构成", "百分比", "100%", "份额", "比例", "归一", "组成",
    "composition", "share", "percent", "proportion", "breakdown", "100 percent",
)

# Question phrases that hint at an alert / exception / threshold-breach table.
_ALERT_HINTS = (
    "异常", "预警", "告警", "超标", "超限", "超过", "低于阈值", "大于阈值",
    "exception", "alert", "anomaly", "breach", "outlier", "threshold",
)

# Question phrases that hint at a time trend.
_TREND_HINTS = ("趋势", "变化", "走势", "增长", "下降", "trend", "按月", "每月", "逐年")

# Question phrases that hint at a two-dimension cross / pivot matrix.
_HEATMAP_HINTS = ("交叉", "矩阵", "行列", "二维", "透视", "cross", "matrix", "pivot", "grid")

# Question phrases that hint at a numeric distribution.
_HISTOGRAM_HINTS = ("分布", "区间", "频数", "直方", "分箱", "distribution", "histogram", "bucket")

# Question phrases that hint at a multi-metric radar comparison.
_RADAR_HINTS = ("综合", "多维", "评分", "评估", "雷达", "radar", "spider")

# Question phrases that hint at a conversion funnel.
_FUNNEL_HINTS = ("转化", "漏斗", "流程", "conversion", "funnel")

# Question phrases that hint at a single-metric gauge / progress readout.
_GAUGE_HINTS = ("达成", "完成率", "进度", "目标", "gauge", "meter")

# Question phrases that hint at an increment / decrement breakdown.
_WATERFALL_HINTS = ("增减", "变动", "瀑布", "差额", "waterfall")

# Question phrases that hint at a grouped distribution.
_BOXPLOT_HINTS = ("箱线", "离散", "四分位", "异常值", "boxplot", "box")

# 列类型推断住在 coltypes（叶子模块，charts/features/pivot 共用一份，见该模块 docstring）。
# 这里一并 re-export ``_is_numeric`` / ``_parse_datetime`` / ``_looks_like_id``：它们历史上
# 由本模块定义，validation 与若干测试按旧路径引用，保持可达以免无谓的破坏性改动。
from .coltypes import (  # noqa: F401  (public re-export)
    _TIME_NAME_HINTS,
    _as_int,
    _column_kind,
    _is_numeric,
    _is_period_int_column,
    _looks_like_id,
    _parse_datetime,
)


def _field(column: str, label: Optional[str] = None) -> Dict[str, Any]:
    unit = _UNITS.get(column)
    caption = label or column
    explicit = re.search(r'[（(](本|张|件|家|家次|项|项次|条|人|万元|元|万次|次|%|％)[）)]$', caption)
    if explicit:
        unit = explicit.group(1)
    elif re.fullmatch(r'(?:有效|现有|当期|期末|甲级|乙级|测绘|资质|总|去重)*证书(?:总数|总量|数量|数|本数)', caption):
        unit = '本'
    elif re.fullmatch(r'(?:抽查|受检|被检|合格|不合格|有效|甲级|乙级|测绘|资质|去重|总)*单位(?:总数|数量|数)', caption):
        unit = '家'
    elif re.fullmatch(r'(?:有效|非空|空值|缺失|物理|去重|总)*记录(?:总数|数量|数)|(?:非空|空值|缺失|总)?行数', caption):
        unit = '条'
    return {
        "field": column,
        "label": caption,
        "unit": unit,
    }


def _alternatives(chart_type: str) -> List[str]:
    by_type = {
        "kpi": ["kpi", "table"],
        "bar": ["bar", "column", "pie", "treemap", "table"],
        "column": ["column", "bar", "pie", "treemap", "table"],
        "grouped-bar": ["grouped-bar", "stacked-bar", "percent-stacked-bar", "heatmap", "table"],
        "stacked-bar": ["stacked-bar", "percent-stacked-bar", "grouped-bar", "table"],
        "percent-stacked-bar": ["percent-stacked-bar", "stacked-bar", "grouped-bar", "table"],
        "line": ["line", "area", "bar", "table"],
        "area": ["area", "line", "bar", "table"],
        "pie": ["pie", "donut", "bar", "treemap", "table"],
        "donut": ["donut", "pie", "bar", "treemap", "table"],
        "scatter": ["scatter", "bubble", "table"],
        "bubble": ["bubble", "scatter", "table"],
        "heatmap": ["heatmap", "grouped-bar", "table"],
        "treemap": ["treemap", "pie", "bar", "table"],
        "histogram": ["histogram", "bar", "table"],
        "radar": ["radar", "grouped-bar", "table"],
        "funnel": ["funnel", "bar", "table"],
        "gauge": ["gauge", "kpi", "table"],
        "waterfall": ["waterfall", "bar", "table"],
        "boxplot": ["boxplot", "histogram", "bar", "table"],
        "alert-table": ["alert-table", "table"],
        "table": ["table", "alert-table"],
        "pivot-table": ["pivot-table", "table"],
    }
    return by_type.get(chart_type, ["table"])


def _category_count(
    rows: List[Dict[str, Any]], categorical: Sequence[str]
) -> int:
    """前端绘制时真正占用的分类数。

    两个分类维时前端按 x 维把长表透成矩阵（x 作分类、第二维作系列，见
    ProductVisualization.extractSeriesGroups），所以「展示多少项」取决于 x 维的基数，
    而不是原始明细行数：44 条明细会被铺成 11 行 × 5 列，一个分类都没少，此时按 44
    说「仅显示前 12 项」是错的（2026-09-17 真机）。
    """
    if len(categorical) >= 2:
        return _cardinality(rows, categorical[0])
    return len(rows)


def _disclosure(
    rows: List[Dict[str, Any]],
    top_n: Optional[int] = None,
    count: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """口径说明。``count`` 是前端实际绘制的分类数，缺省回落到原始行数——
    两分类维的图必须传它，否则数字与用户在图上看到的分类数对不上。"""
    total = len(rows) if count is None else count
    note = None
    # The renderer now carries every category in a scrollable container.
    # Legacy recommendations must not claim truncation that never occurred.
    if total > 12:
        note = f"共 {total} 项，完整展示；超出可视区域时可滚动查看"
    if note is None:
        return None
    return {"topN": None, "sampled": False, "note": note}


def _pivot_disclosure(
    rows: List[Dict[str, Any]], pivot: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """口径说明 for a pivot layout.

    ``_disclosure`` counts the **raw** result rows, so a 44-row result rendered as an
    11×5 crosstab would tell the user「共 44 项，仅显示前 20 项」while the pivot grid
    actually carries every combination — nothing is dropped.  Describe the crosstab
    itself instead of borrowing the detail-table wording (2026-09-17 真机发现).
    """
    cardinality = pivot.get("cardinality") or {}
    row_count = cardinality.get("rows") or 0
    column_count = cardinality.get("columns") or 0
    if not row_count:
        return None
    if column_count > 1:
        note = f"原始 {len(rows)} 条明细，汇总为 {row_count} 行 × {column_count} 列交叉表"
    else:
        note = f"原始 {len(rows)} 条明细，汇总为 {row_count} 行"
    return {"topN": None, "sampled": False, "note": note}


def _map_intent_to_family(intent: Optional[str]) -> Optional[str]:
    """Map the model's coarse intent to the chart family we can validate."""
    if not intent:
        return None
    if intent in {"scalar", "kpi"}:
        return "kpi"
    if intent in {"bar", "column"}:
        return "bar"
    if intent in {"line", "area"}:
        return "line"
    if intent in {"pie", "donut"}:
        return "pie"
    if intent in {"scatter", "bubble"}:
        return "scatter"
    if intent in {"grouped-bar", "stacked-bar", "percent-stacked-bar"}:
        return "grouped-bar"
    if intent in {"heatmap"}:
        return "heatmap"
    if intent in {"treemap"}:
        return "treemap"
    if intent in {"histogram"}:
        return "histogram"
    if intent in {"radar"}:
        return "radar"
    if intent in {"funnel"}:
        return "funnel"
    if intent in {"gauge"}:
        return "gauge"
    if intent in {"waterfall"}:
        return "waterfall"
    if intent in {"boxplot"}:
        return "boxplot"
    if intent in {"pivot-table", "pivot", "crosstab", "cross-tab"}:
        return "pivot-table"
    return None


# ---------------------------------------------------------------------------
# 显式图表指定通道（L1）
# ---------------------------------------------------------------------------
# 用户在本轮问题里点名要某一种图（"用饼图"、"画成折线"、"as a bar chart"）。
# 这张表是**确定性词表**，不经过 LLM——因此"用户说的"与"模型猜的"在系统里
# 第一次有了可区分的身份。
#
# 键 = 问题里出现的图表名词（匹配前统一小写），值 = 规则引擎认识的族名。
# 保留用户指定的图形类型。适配规则可以共享族名，渲染仍使用原类型。
_EXPLICIT_CHART_HINTS: Dict[str, str] = {
    # 柱状 / 条形
    "柱状图": "column", "柱形图": "column", "柱图": "column", "条形图": "bar",
    "横向柱状图": "bar", "横向柱形图": "bar",
    "竖向柱状图": "column", "纵向柱状图": "column",
    "column chart": "column", "vertical bar chart": "column",
    "bar chart": "bar", "barchart": "bar",
    # 折线 / 面积
    "折线图": "line", "折线": "line", "趋势线": "line", "曲线图": "line",
    "line chart": "line", "linechart": "line",
    "面积图": "area", "区域图": "area", "area chart": "area",
    # 饼 / 环
    "饼图": "pie", "饼状图": "pie", "饼形图": "pie",
    "pie chart": "pie", "piechart": "pie",
    "环形图": "donut", "圆环图": "donut", "甜甜圈图": "donut", "甜甜圈": "donut",
    "donut chart": "donut", "ring chart": "donut",
    # 分组 / 堆积柱（长词必须优先命中，见 _EXPLICIT_CHART_PHRASES）
    "百分比堆积柱状图": "percent-stacked-bar", "百分比堆叠柱状图": "percent-stacked-bar",
    "100%堆积柱状图": "percent-stacked-bar", "100%堆叠柱状图": "percent-stacked-bar",
    "百分比堆积柱形图": "percent-stacked-bar", "百分比柱状图": "percent-stacked-bar",
    "百分比堆积图": "percent-stacked-bar", "百分比堆叠图": "percent-stacked-bar",
    "percent stacked bar": "percent-stacked-bar",
    "堆积柱状图": "stacked-bar", "堆叠柱状图": "stacked-bar",
    "堆积柱形图": "stacked-bar", "堆叠图": "stacked-bar", "堆积图": "stacked-bar",
    "stacked bar": "stacked-bar",
    "分组柱状图": "grouped-bar", "分组柱形图": "grouped-bar", "簇状柱状图": "grouped-bar",
    "grouped bar": "grouped-bar",
    # 散点 / 气泡
    "散点图": "scatter", "散点": "scatter", "scatter plot": "scatter",
    "气泡图": "bubble", "气泡": "bubble", "bubble chart": "bubble",
    # 其余按族名直呼
    "雷达图": "radar", "radar chart": "radar",
    "漏斗图": "funnel", "funnel chart": "funnel",
    "仪表盘": "gauge", "仪表图": "gauge", "gauge chart": "gauge",
    "瀑布图": "waterfall", "waterfall chart": "waterfall",
    "箱线图": "boxplot", "箱形图": "boxplot", "盒须图": "boxplot",
    "热力图": "heatmap", "热图": "heatmap",
    "矩形树图": "treemap", "树状图": "treemap", "树图": "treemap",
    "直方图": "histogram",
    "透视表": "pivot-table", "交叉表": "pivot-table", "数据透视": "pivot-table",
    "pivot table": "pivot-table", "crosstab": "pivot-table",
    "指标卡": "kpi",
    # 只收「表格 / 明细表」这种明确的图表名词：裸「明细」在"各区项目明细对比"
    # 这类句子里只是想看细节，并不等于要表格，误判会把对比图换成表格。
    "表格": "table", "明细表": "table", "数据表": "table", "汇总表": "table", "统计表": "table",
}

# 匹配必须按词长降序：``分组柱状图``/``堆积柱状图`` 都含 ``柱状图``，
# ``百分比堆积柱`` 含 ``堆积柱``——先匹配短词会把用户的精确指定降级成粗族。
_EXPLICIT_CHART_PHRASES: List[Any] = sorted(
    _EXPLICIT_CHART_HINTS.items(), key=lambda item: -len(item[0])
)
_CHART_TERM = re.compile('|'.join(re.escape(phrase) for phrase, _ in _EXPLICIT_CHART_PHRASES))
_CHART_EXCLUDED = re.compile(r'(?:不要|不用|无需|不需要|不必|去掉|instead of|without)\s*(?:(?:使用|生成|显示|画|用)\s*)?$')
_CHART_REPLACE = re.compile(r'(?:改|换)(?:成|为|用)|(?:只|仅)(?:要|用|给)')

# 每种图表能**真实表达**的度量个数上限。用户显式指定时，超出的度量收敛到上限，
# 而不是因为"字段太多"把整张图降级成表格——用户要的是图，就给图，只是度量取前 N 个。
_MEASURE_CAP: Dict[str, int] = {
    "pie": 1, "donut": 1, "treemap": 1, "funnel": 1, "waterfall": 1,
    "boxplot": 1, "histogram": 1, "heatmap": 1,
    "gauge": 3,
    "radar": 6,
    "bar": 4, "column": 4, "line": 4, "area": 4,
    "grouped-bar": 4, "stacked-bar": 4, "percent-stacked-bar": 4,
    "scatter": 2, "bubble": 3,
}


def _extract_explicit_chart(question: str) -> Optional[str]:
    """抽出用户**显式点名**的图表族；没点名返回 ``None``。

    纯词表匹配，没有模型参与——命中即代表"用户说的"，因此可以安全地压在模型
    推断与 ⑥ 矩阵偏好之上。语义暗示（占比 / 趋势 / 对比 / 分布）仍然走各自的
    ``_*_HINTS`` 表，属于低一级的推断，不在本函数职责内。
    """
    if not question:
        return None
    text = unicodedata.normalize('NFKC', question).casefold()
    overrides = list(_CHART_REPLACE.finditer(text))
    if overrides and _CHART_TERM.search(text[overrides[-1].end():]):
        text = text[overrides[-1].end():]
    choices = []
    for match in _CHART_TERM.finditer(text):
        if (_CHART_EXCLUDED.search(text[max(0, match.start()-24):match.start()])
                or re.match(r'\s*(?:不要了|不用了|不需要了)', text[match.end():])):
            continue
        choices.append(_EXPLICIT_CHART_HINTS[match[0]])
    return next((kind for kind in choices if kind != 'table'), choices[0] if choices else None)


def _hard_blocked(
    family: str,
    numeric: Sequence[str],
    categorical: Sequence[str],
    datetime_cols: Sequence[str],
    rows: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """C 类硬约束：数据形态在物理上撑不起该图，或画出来必然误导。

    这是**唯一**允许否决显式指定的层。可读性偏好（字段太多、扇区太多、类别
    太多）属于 P 类，写在 ``recommend_chart`` 的规则里并带 ``not explicit``
    守卫——"我觉得不好看"没有资格否掉用户的明确要求。
    """
    if family in {"table", "pivot-table"}:
        return None
    dims = list(datetime_cols) + list(categorical)
    if family in {"scatter", "bubble"}:
        needed = 3 if family == "bubble" else 2
        if len(numeric) < needed:
            return f"需要 {needed} 个数值度量，当前只有 {len(numeric)} 个"
    if family == "radar" and len(numeric) < 3:
        return f"雷达图需要至少 3 个数值度量，当前只有 {len(numeric)} 个"
    if family in {
        "bar", "column", "grouped-bar", "stacked-bar", "percent-stacked-bar",
        "line", "area", "heatmap",
    } and not dims:
        return "需要一个维度作为横轴，当前结果只有数值列"
    if family == "heatmap" and len(dims) < 2:
        return "热力图需要两个维度"
    if family in {"pie", "donut"}:
        if not categorical:
            return "需要一个分类维度（当前只有时间列或数值列）"
        measure = numeric[0] if numeric else None
        if measure and any(
            isinstance(row.get(measure), (int, float)) and row.get(measure) < 0
            for row in rows
        ):
            return "数据含负值，无法表达部分与整体的关系"
        card = _cardinality(rows, categorical[0]) or 0
        if card > PIE_CATEGORY_LIMIT_EXPLICIT:
            # 扇区一旦截断，占比合计就不再等于整体——这是失真，不是不好看，
            # 所以它属于硬约束而不是可读性偏好。
            return (
                f"分类数 {card} 超过 {PIE_CATEGORY_LIMIT_EXPLICIT}，"
                "截断扇区会使占比合计不等于整体"
            )
    if family == "histogram" and dims:
        return "直方图要求单一数值分布，当前结果含维度列"
    if family == "kpi" and len(rows) != 1:
        return f"指标卡要求结果为单行，当前有 {len(rows)} 行"
    return None


def _demoted_family(
    family: str,
    numeric: Sequence[str],
    categorical: Sequence[str],
    datetime_cols: Sequence[str],
    rows: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """显式指定被硬约束挡住时，退到**最接近的诚实表达**。

    只保证"能画且不撒谎"，不保证用户满意——所以调用方必须把降级记进日志。
    """
    dims = list(datetime_cols) + list(categorical)
    if family in {"pie", "donut"}:
        # 扇区太多时，树图是同样诚实（矩形面积仍然合计为整体）但可读的替代。
        card = _cardinality(rows, categorical[0]) if categorical else 0
        if card and card > PIE_CATEGORY_LIMIT_EXPLICIT:
            return "treemap"
        return "bar" if dims and numeric else None
    if family == "treemap":
        return "bar" if dims and numeric else None
    if family in {"scatter", "bubble"}:
        if len(numeric) >= 2:
            return "scatter"
        return "bar" if dims and numeric else None
    if family == "radar":
        if dims and len(numeric) >= 2:
            return "grouped-bar"
        return "bar" if dims and numeric else None
    if family == "heatmap":
        return "grouped-bar" if dims and numeric else None
    if family in {
        "bar", "column", "grouped-bar", "stacked-bar", "percent-stacked-bar", "line", "area",
    }:
        # 没有横轴维度：单行数值退 KPI（行数不为 1 时规则链会自然兜底成表格）。
        return "kpi"
    return None


def _cap_measures(family: str, numeric: List[str]) -> List[str]:
    """把度量收敛到该图表能真实表达的上限（仅显式指定路径使用）。"""
    cap = _MEASURE_CAP.get(family)
    if cap is None or len(numeric) <= cap:
        return numeric
    return numeric[:cap]


def recommend_chart(
    columns: List[str],
    rows: List[Dict[str, Any]],
    expected_type: Optional[str] = None,
    question: str = "",
    title: str = "",
    alert_rules: Optional[List[Dict[str, Any]]] = None,
    preferred_family: Optional[str] = None,
    labels: Optional[Mapping[str, str]] = None,
    explicit_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a validated ``CanonicalChartV4`` non-map spec for tabular data.

    裁决优先级（高 → 低），只有最高层允许否决用户：

    ``L0`` 硬约束（``_hard_blocked``：画不出 / 会撒谎）→ ``L1`` 用户显式指定
    （``question`` 里点名的图表名词，见 ``_extract_explicit_chart``）→ ``L2``
    模型的 ``expected_type`` → ``L3`` ``preferred_family``（⑥ 矩阵偏好）→
    ``L4`` 数据形态规则兜底。

    ``L2`` 只在 ``L1`` 缺席时生效，``L3`` 只在 ``L1``/``L2`` 都没表态时补位。
    P 类可读性偏好（字段过多、扇区过多）写在下面的规则里并带 ``not explicit``
    守卫——它们没有资格否掉用户的明确要求。
    """

    # 列的中文标签由调用方（service）从 schema 投影解析后传进来，放进 encoding 的
    # label 里，前端图例 / 坐标轴就不用显示 product_count 这类英文列名了。
    # 没有映射时保持原行为（label == 列名）。
    label_map = dict(labels or {})

    def _f(column: str, label: Optional[str] = None) -> Dict[str, Any]:
        return _field(column, label or label_map.get(column))

    def _to_alert_table(spec: Dict[str, Any]) -> Dict[str, Any]:
        if alert_rules and spec.get("type") == "table":
            return {
                **spec,
                "type": "alert-table",
                "alertRules": alert_rules,
                "allowedAlternatives": ["alert-table", "table"],
            }
        return spec

    if not columns or not rows:
        return _to_alert_table(
            {
                "type": "table",
                "title": title,
                "encoding": {},
                "allowedAlternatives": ["table"],
            }
        )

    # Drop duplicate column names: a repeated measure (["数量", "数量"]) must not
    # be counted as two distinct numerics that would wrongly trigger a scatter.
    columns = list(dict.fromkeys(columns))

    # A long table may put different physical units in the same value column.
    # A shared numeric axis would compare e.g. people and percentages. Retain
    # the full table instead of silently treating the unit column as a label.
    unit_columns = [c for c in columns if str(label_map.get(c, c)).strip().lower() in ('单位', '计量单位', 'unit', 'units')]
    if any(len({str(row.get(c)).strip() for row in rows if row.get(c) not in (None, '')}) > 1 for c in unit_columns):
        return _to_alert_table({'type': 'table', 'title': title, 'encoding': {}, 'allowedAlternatives': ['table'],
            'disclosure': {'topN': None, 'sampled': False, 'note': '各行计量单位不同，使用表格分别展示，避免共用数值轴造成误解。'}})

    kinds = {column: _column_kind(column, [row.get(column) for row in rows]) for column in columns}
    numeric = [column for column in columns if kinds[column] == "numeric"]
    datetime_cols = [column for column in columns if kinds[column] == "datetime"]
    categorical = [column for column in columns if kinds[column] == "categorical"]

    raw_intent = expected_type
    intent = _map_intent_to_family(raw_intent)
    # ---- 显式指定通道（L1）------------------------------------------------
    # 用户点名要某一种图时，该指定压过模型的语义推断（L2）与 ⑥ 矩阵偏好（L3），
    # 只让位于硬约束（L0）。词表匹配是确定性的，所以这里不存在"把模型猜的当成
    # 用户说的"这种身份混淆——那正是过去无法实现"显式优先"的根因。
    # Natural Work presentation commands are structured, not synthetic questions.
    explicit_raw = explicit_type if explicit_type is not None else _extract_explicit_chart(question)
    if explicit_raw == "table":
        # "用表格"是合法且总是可行的显式指定，但没有对应族名
        # （_map_intent_to_family 不认 table），所以在此单独立案。
        explicit_family = "table"
    elif explicit_raw is not None:
        # 硬约束必须用**原样族名**判定：pie/donut、line/area、scatter/bubble 在
        # 归一化时会被折叠成同一族，而"气泡图需要三个数值度量"这类约束只有原样
        # 族名才携带——折成 scatter 之后只剩"两个"，于是一张**没有 size 编码**
        # 的气泡图会被画出来，比降级更糟。
        blocked = _hard_blocked(
            explicit_raw, numeric, categorical, datetime_cols, rows
        )
        if blocked:
            demoted = _demoted_family(
                explicit_raw, numeric, categorical, datetime_cols, rows
            )
            logger.info(
                "chart.explicit.demoted requested=%s demoted=%s reason=%s",
                explicit_raw,
                demoted,
                blocked,
            )
            explicit_raw = demoted
        explicit_family = _map_intent_to_family(explicit_raw)
        if explicit_family is None:
            explicit_raw = None
    else:
        explicit_family = None
    explicit = explicit_family is not None
    if explicit:
        # 度量收敛到该图能真实表达的上限，取代"字段太多 → 一律表格"的旧行为。
        # 上限必须按**原样族名**查表：bubble 的上限是 3、scatter 是 2，用归一后
        # 的族名会把气泡图的第三个度量（size）截掉，画出一张没有大小的气泡图。
        numeric = _cap_measures(explicit_raw, numeric)
        raw_intent = explicit_raw
        intent = explicit_family
    # ⑥ 矩阵首选图表偏好：仅当既没有显式指定、模型也没给出可识别的图表族时补位
    # （模型推荐，规则引擎裁决——裁决依据始终是下面的数据形态）。
    # 注意：``table`` 是模型未表态时的兜底值（CONTRACT 里 expected_chart.type 的
    # 默认项），不代表真实意图，不能挤掉矩阵偏好——否则「分布→Histogram」这类
    # 目的→图表映射会被无信息量的 table 永远旁路（2026-09-12 定位）。
    if not explicit and (intent is None or intent == "table"):
        # ⑥ 矩阵首选图表偏好必须经过归一到规则引擎认识的族名；原样塞入
        # percent-stacked-bar / stacked-bar / area / table 等不会被任何规则
        # 白名单识别，反而逐条落空降级为表格。
        normalized = _map_intent_to_family(preferred_family)
        if normalized is not None:
            raw_intent = preferred_family
            intent = normalized
    bar_type = "column" if raw_intent == "column" else "bar"
    proportion = any(hint in question for hint in _PROPORTION_HINTS)
    stack = any(hint in question for hint in _STACK_HINTS)
    percent_stack = any(hint in question for hint in _PERCENT_STACK_HINTS)
    alert_hint = any(hint in question for hint in _ALERT_HINTS)
    heatmap_hint = any(hint in question for hint in _HEATMAP_HINTS)
    histogram_hint = any(hint in question for hint in _HISTOGRAM_HINTS)
    radar_hint = any(hint in question for hint in _RADAR_HINTS)
    funnel_hint = any(hint in question for hint in _FUNNEL_HINTS)
    gauge_hint = any(hint in question for hint in _GAUGE_HINTS)
    waterfall_hint = any(hint in question for hint in _WATERFALL_HINTS)
    boxplot_hint = any(hint in question for hint in _BOXPLOT_HINTS)

    # The pivot layout is deterministic and derived from the same result set, so
    # it is computed once and attached to any spec that can reasonably offer
    # "switch to pivot table" as an alternative.
    pivot_cache: List[Optional[Dict[str, Any]]] = [None]

    def _with_pivot(spec: Dict[str, Any]) -> Dict[str, Any]:
        if pivot_cache[0] is None:
            pivot_cache[0] = build_pivot_spec(columns, rows, kinds, question)
        pivot = pivot_cache[0]
        if not pivot:
            return spec
        merged = {**spec, "pivot": pivot}
        alternatives = list(merged.get("allowedAlternatives") or [])
        if "pivot-table" not in alternatives:
            alternatives.append("pivot-table")
        merged["allowedAlternatives"] = alternatives
        return merged

    def _pivot_spec(pivot: Dict[str, Any]) -> Dict[str, Any]:
        encoding: Dict[str, Any] = {}
        if pivot["rows"]:
            encoding["x"] = _f(pivot["rows"][0])
        if pivot["columns"]:
            encoding["series"] = _f(pivot["columns"][0])
        if pivot["values"]:
            encoding["y"] = [_f(value) for value in pivot["values"]]
        # 回切候选：只给 ["pivot-table", "table"] 的话，前端把 table 归进「数据」页签
        # 语义后，用户切进透视表就再也回不来。按这个交叉表真正能表达的图形给候选——
        # 单列维 + 单度量正好是热力图的 encoding 契约（x=行维度、series=列维度、
        # y=度量），其余交给分组柱状图（2026-09-17 真机核查）。
        if len(pivot.get("columns") or []) == 1 and len(pivot.get("values") or []) == 1:
            back_alternatives = _alternatives("heatmap")
        else:
            back_alternatives = _alternatives("grouped-bar")
        alternatives = ["pivot-table"] + [
            candidate for candidate in back_alternatives if candidate != "table"
        ] + ["table"]
        spec: Dict[str, Any] = {
            "type": "pivot-table",
            "title": title,
            "encoding": encoding,
            "pivot": pivot,
            "allowedAlternatives": alternatives,
            "disclosure": _pivot_disclosure(rows, pivot),
        }
        return spec

    def _series_table(series_count: int) -> Dict[str, Any]:
        # F4：系列数超出可读上限时的默认兜底——完整表格 + 透视表候选，并说明
        # 原因与出路。只有非显式（L1 缺席）的默认/推断路径会到这里。
        base = _disclosure(rows) or {"topN": None, "sampled": False, "note": None}
        disclosure = {**base, "note": f"系列数 {series_count} 超过 {SERIES_READABILITY_LIMIT} 条，一张图难以辨读，默认用表格完整展示；可切换透视表，或明确要求折线图/分组柱状图。"}
        return _with_pivot(
            _to_alert_table(
                {
                    "type": "table",
                    "title": title,
                    "encoding": {},
                    "allowedAlternatives": ["table"],
                    "disclosure": disclosure,
                }
            )
        )

    if not numeric:
        return _to_alert_table(
            {
                "type": "table",
                "title": title,
                "encoding": {},
                "allowedAlternatives": ["table"],
            }
        )

    # 0t. 显式指定表格：总是可行，也必须尊重——"给我一张明细表"是一个明确
    #     诉求，不是降级请求。
    if explicit_raw == "table":
        return _with_pivot(
            _to_alert_table(
                {
                    "type": "table",
                    "title": title,
                    "encoding": {},
                    "allowedAlternatives": ["table"],
                    "disclosure": _disclosure(rows),
                }
            )
        )

    # 0p. Explicit pivot request -> pivot table.  This is decided *before* the
    #     "too many fields" demotion below: a wide result is exactly what a pivot
    #     table handles well and what no chart can render faithfully.
    if pivot_hint(question) or intent == "pivot-table":
        pivot = pivot_cache[0] or build_pivot_spec(columns, rows, kinds, question)
        if pivot:
            pivot_cache[0] = pivot
            return _pivot_spec(pivot)

    # 0. 字段太多 -> 表格。**P 类（可读性偏好）规则**：超过可视化可读范围的字段数
    #    （>8 列或 >4 个数值列）降级为表格。它只在没有显式指定时生效——用户明确
    #    要某种图时，度量已在上面收敛到该图的表达能力上限，多余的列也不会进入
    #    encoding，图表依然忠实，没有理由再把它换成表格。
    if len(columns) > 8 or len(numeric) > 4:
        if not explicit:
            return _with_pivot(
                _to_alert_table(
                    {
                        "type": "table",
                        "title": title,
                        "encoding": {},
                        "allowedAlternatives": ["table"],
                        "disclosure": _disclosure(rows),
                    }
                )
            )

    # 1p. Single-metric progress / attainment -> gauge.
    if len(numeric) >= 1 and len(rows) <= 3 and (gauge_hint or intent == "gauge"):
        encoding = {"y": [_f(column) for column in numeric[:3]]}
        if categorical:
            encoding["x"] = _f(categorical[0])
        return {
            "type": "gauge",
            "title": title,
            "encoding": encoding,
            "allowedAlternatives": _alternatives("gauge"),
            "disclosure": _disclosure(rows),
        }

    # 1r. Multi-metric radar comparison.
    if len(numeric) >= 3 and (radar_hint or intent == "radar"):
        encoding = {"y": [_f(column) for column in numeric[:6]]}
        if categorical:
            encoding["x"] = _f(categorical[0])
        return {
            "type": "radar",
            "title": title,
            "encoding": encoding,
            "allowedAlternatives": _alternatives("radar"),
            "disclosure": _disclosure(rows),
        }

    # 1f. Conversion funnel.
    if categorical and len(numeric) >= 1 and (funnel_hint or intent == "funnel"):
        return {
            "type": "funnel",
            "title": title,
            "encoding": {"x": _f(categorical[0]), "y": _f(numeric[0])},
            "allowedAlternatives": _alternatives("funnel"),
            "disclosure": _disclosure(rows),
        }

    # 1w. Increment / decrement waterfall.
    if categorical and len(numeric) == 1 and (waterfall_hint or intent == "waterfall"):
        return {
            "type": "waterfall",
            "title": title,
            "encoding": {"x": _f(categorical[0]), "y": _f(numeric[0])},
            "allowedAlternatives": _alternatives("waterfall"),
            "disclosure": _disclosure(rows),
        }

    # 1b. Grouped distribution -> boxplot.
    if categorical and len(numeric) == 1 and (boxplot_hint or intent == "boxplot"):
        return {
            "type": "boxplot",
            "title": title,
            "encoding": {"x": _f(categorical[0]), "y": _f(numeric[0])},
            "allowedAlternatives": _alternatives("boxplot"),
            "disclosure": _disclosure(rows),
        }

    # 1. Single-row / single-metric result -> KPI.
    if len(rows) == 1 and len(numeric) <= 4 and intent in {None, "kpi"}:
        encoding = {"y": [_f(column) for column in numeric[:4]]}
        if categorical:
            encoding["x"] = _f(categorical[0])
        return {
            "type": "kpi",
            "title": title,
            "encoding": encoding,
            "allowedAlternatives": _alternatives("kpi"),
            "disclosure": _disclosure(rows),
        }

    # 2. 时间维可用折线、面积、柱状或条形图。
    if datetime_cols and numeric and intent in {None, "line", "bar", "area"}:
        # F4：长表 期间×类别 的系列数超过可读上限时，默认表格而不是几十条折线。
        # 显式指定（L1）不受此限——用户点名要折线图时仍画折线图。
        if (
            not explicit
            and len(categorical) == 1
            and len(numeric) == 1
            and _cardinality(rows, categorical[0]) > SERIES_READABILITY_LIMIT
        ):
            return _series_table(_cardinality(rows, categorical[0]))
        if explicit_raw in {"line", "area"}:
            chart_type = explicit_raw
        elif intent == "bar":
            chart_type = bar_type
        else:
            chart_type = "area" if stack else "line"
        encoding: Dict[str, Any] = {
            "x": _f(datetime_cols[0]),
            "y": [_f(column) for column in numeric[:3]],
        }
        if len(categorical) == 1 and len(numeric) == 1:
            encoding["series"] = _f(categorical[0])
        return {
            "type": chart_type,
            "title": title,
            "encoding": encoding,
            "allowedAlternatives": _alternatives(chart_type),
            "disclosure": _disclosure(rows),
        }

    # 3. Composition -> pie / donut (only for a small category set).
    if categorical and len(numeric) == 1 and (proportion or intent == "pie"):
        # Use the *real* category cardinality, not the row count: 8 rows of 2
        # cities must not produce 8 repeated sectors.  Negative measures (净增长)
        # cannot be honestly shown as a part-to-whole, so skip the pie/ring.
        cat_card = _cardinality(rows, categorical[0])
        measure_values = [row.get(numeric[0]) for row in rows]
        has_negative = any(
            isinstance(v, (int, float)) and v < 0 for v in measure_values if v is not None
        )
        # P 类上限：无显式指定时沿用 6；用户点名要饼图时放宽到 12——"扇区偏多"
        # 是可读性意见，不是物理不可能，没有资格否掉用户的明确要求。
        pie_limit = PIE_CATEGORY_LIMIT_EXPLICIT if explicit else PIE_CATEGORY_LIMIT
        if cat_card and cat_card <= pie_limit and not has_negative:
            # 用户点名了哪一种就画哪一种：饼图与环形图在归一化时会折叠成同一族
            # （只为过白名单），渲染时必须还原用户的原话。
            if explicit_raw in {"pie", "donut"}:
                chart_type = explicit_raw
            else:
                chart_type = "donut"
            return {
                "type": chart_type,
                "title": title,
                "encoding": {"x": _f(categorical[0]), "y": _f(numeric[0])},
                "allowedAlternatives": _alternatives(chart_type),
                "disclosure": _disclosure(rows),
            }

    # 3b. Composition with too many categories -> treemap.
    if (
        categorical
        and len(numeric) == 1
        and (len(rows) > PIE_CATEGORY_LIMIT or explicit_raw == "treemap")
        and (proportion or intent == "treemap")
    ):
        return {
            "type": "treemap",
            "title": title,
            "encoding": {"x": _f(categorical[0]), "y": _f(numeric[0])},
            "allowedAlternatives": _alternatives("treemap"),
            "disclosure": _disclosure(rows),
        }

    # 0.6.2: grouped bars start at 0 and drop negative values, so signed multi-measure
    # data (net changes) would render as mostly empty tracks. Default to a table with a
    # pivot alternative; an explicit grouped-bar request still gets the chart.
    signed_measures = any(
        isinstance(row.get(column), (int, float)) and not isinstance(row.get(column), bool) and row.get(column) < 0
        for row in rows for column in numeric
    )
    if categorical and len(numeric) >= 2 and signed_measures and not explicit and intent in {None, "grouped-bar", "bar"}:
        return _with_pivot(
            {
                "type": "table",
                "title": title,
                "encoding": {},
                "allowedAlternatives": ["table", "grouped-bar"],
                "disclosure": _disclosure(rows),
            }
        )

    # 4. Multi-series comparison -> grouped / stacked / 100% stacked bars.
    if categorical and len(numeric) >= 2 and intent in {None, "grouped-bar", "bar"}:
        # F4：第二分类维作为系列时，系列数超上限默认表格（显式指定仍优先）。
        if (
            not explicit
            and len(categorical) >= 2
            and _cardinality(rows, categorical[1]) > SERIES_READABILITY_LIMIT
        ):
            return _series_table(_cardinality(rows, categorical[1]))
        if explicit_raw in {"grouped-bar", "stacked-bar", "percent-stacked-bar"}:
            # stacked-bar / percent-stacked-bar 归一后都是 grouped-bar，白名单过得了，
            # 但渲染要还原用户点的那一种。
            chart_type = explicit_raw
        elif percent_stack:
            chart_type = "percent-stacked-bar"
        elif stack:
            chart_type = "stacked-bar"
        else:
            chart_type = "grouped-bar"
        encoding = {
            "x": _f(categorical[0]),
            "y": [_f(column) for column in numeric[:4]],
        }
        if len(categorical) >= 2:
            encoding["series"] = _f(categorical[1])
        # 两分类维时前端按 x 维把长表透成矩阵，分类数不等于原始行数。
        category_count = _category_count(rows, categorical)
        top_n = 12 if category_count > 12 else None
        return _with_pivot(
            {
                "type": chart_type,
                "title": title,
                "encoding": encoding,
                "allowedAlternatives": _alternatives(chart_type),
                "disclosure": _disclosure(rows, top_n, count=category_count),
            }
        )

    # 4b. Two-dimension cross matrix -> heatmap.  When the matrix grows past what
    #     colour blocks can express (>8 rows or >8 columns) a pivot table is the
    #     honest rendering, so the heatmap hands over instead of shrinking.
    if (
        len(categorical) >= 2
        and len(numeric) == 1
        and (heatmap_hint or intent in {"heatmap", "pivot-table"})
    ):
        category_count = _category_count(rows, categorical)
        top_n = 12 if category_count > 12 else None
        pivot = pivot_cache[0] or build_pivot_spec(columns, rows, kinds, question)
        if pivot:
            pivot_cache[0] = pivot
            cardinality = pivot.get("cardinality") or {}
            if cardinality.get("rows", 0) > 8 or cardinality.get("columns", 0) > 8:
                # 透视表把全部组合铺进网格，不抽样，所以不吃 heatmap 的 top_n 口径。
                return _pivot_spec(pivot)
        return _with_pivot(
            {
                "type": "heatmap",
                "title": title,
                "encoding": {
                    "x": _f(categorical[0]),
                    "series": _f(categorical[1]),
                    "y": _f(numeric[0]),
                },
                "allowedAlternatives": _alternatives("heatmap"),
                "disclosure": _disclosure(rows, top_n, count=category_count),
            }
        )

    # 5. Correlation -> scatter / bubble.  A categorical (label) column is common
    #    on correlation data and is carried as a colour/series mapping rather than
    #    blocking the scatter (the old `not categorical` guard made it dead code).
    if len(numeric) >= 2 and intent in {None, "scatter", "bubble"}:
        if explicit_raw in {"scatter", "bubble"}:
            chart_type = explicit_raw
        else:
            chart_type = "bubble" if len(numeric) >= 3 else "scatter"
        encoding = {
            "x": _f(numeric[0]),
            "y": _f(numeric[1]),
        }
        if len(numeric) >= 3:
            encoding["size"] = _f(numeric[2])
        if categorical:
            encoding["color"] = _f(categorical[0])
        return {
            "type": chart_type,
            "title": title,
            "encoding": encoding,
            "allowedAlternatives": _alternatives(chart_type),
            "disclosure": _disclosure(rows),
        }

    # 5b. Numeric distribution -> histogram.
    if (
        len(numeric) == 1
        and not categorical
        and len(rows) > 20
        and (histogram_hint or intent == "histogram")
    ):
        return {
            "type": "histogram",
            "title": title,
            "encoding": {"x": _f(numeric[0])},
            "allowedAlternatives": _alternatives("histogram"),
            "disclosure": _disclosure(rows),
        }

    # 6. Single category + single metric -> bar or column.
    if categorical and len(numeric) == 1:
        # F4：两分类维（第二维作系列）超上限时默认表格；单维条形不受影响。
        if (
            not explicit
            and len(categorical) >= 2
            and _cardinality(rows, categorical[1]) > SERIES_READABILITY_LIMIT
        ):
            return _series_table(_cardinality(rows, categorical[1]))
        chart_type = bar_type
        # 两分类维时下面会改走 grouped-bar 并按 x 维透视，分类数按 x 基数算。
        category_count = _category_count(rows, categorical)
        top_n = 12 if category_count > 12 else None
        encoding = {"x": _f(categorical[0]), "y": _f(numeric[0])}
        if len(categorical) >= 2:
            # 两个分类维度 + 单度量：不能静默丢掉第二个维度。用 grouped-bar 把第二
            # 维度作为 series，并附带透视表兜底，避免 x 轴取值重复、维度丢失。
            chart_type = "grouped-bar"
            encoding["series"] = _f(categorical[1])
        return _with_pivot(
            {
                "type": chart_type,
                "title": title,
                "encoding": encoding,
                "allowedAlternatives": _alternatives(chart_type),
                "disclosure": _disclosure(rows, top_n, count=category_count),
            }
        )

    # 7. A lone metric with a small result set -> bar, otherwise table.
    if len(numeric) == 1 and len(rows) <= 20:
        if categorical:
            return {
                "type": "bar",
                "title": title,
                "encoding": {"x": _f(categorical[0]), "y": _f(numeric[0])},
                "allowedAlternatives": _alternatives("bar"),
                "disclosure": _disclosure(rows),
            }
        # 没有分类维度时，把同一度量同时放在 x/y 轴是错的（x==y）。退化为表格，
        # 让原始数字如实呈现，而不是被一张自比的柱图误导。
        return _to_alert_table(
            {
                "type": "table",
                "title": title,
                "encoding": {},
                "allowedAlternatives": ["table"],
                "disclosure": _disclosure(rows),
            }
        )

    return _with_pivot(
        _to_alert_table(
            {
                "type": "table",
                "title": title,
                "encoding": {},
                "allowedAlternatives": ["table"],
            }
        )
    )
