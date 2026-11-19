"""Analysis Capability Matrix: intent routing + framework + chart rules.

Derived from the local DB-GPT analysis_matrix.py (MIT; see THIRD_PARTY_LICENSE.txt).
Natural Work uses these declarative definitions through native Harness method
selection, not the original product's planner or orchestration loop. Spatial
rows remain provenance only and are excluded from executable registrations.

The single source of truth for the 智能问数 analysis framework, encoded from the
product spec 分析目的表 (45 rows).  It implements the 7-layer pipeline:

    ① Analysis Category -> ② Intent -> ③ Primary Method -> ④ Sub Method
     -> ⑤ Analysis Framework (answer template) -> ⑥ Visualization Rules
     -> ⑦ Visualization

Division of labour (per the spec):
* ①~④ are judged by the LLM inside the planner contract (only the compact
  ``INTENT_TAXONOMY`` is injected into the prompt, never this whole file).
* ⑤ is a configuration template recalled deterministically here.
* ⑥ is the rule engine (charts.py) which validates the model's chart
  recommendation against the actual data shape; this module only supplies the
  matrix's primary/fallback chart as a *preference*.
* ⑦ is the rule engine's verdict.

Nothing in this module ever talks to a model: every function is deterministic
so the routing is reproducible and auditable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Capability matrix (45 rows) ------------------------------------------------
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisCapability:
    """One row of the analysis capability matrix."""

    category: str            # ① 分析大类
    intent: str              # ② 分析目的
    primary_method: str      # ③ 主分析方法
    sub_methods: Tuple[str, ...]  # ④ 子方法 / 算子
    framework: Tuple[str, ...]    # ⑤ 模型回答分析框架（①~⑥ 步骤）
    chart_condition: str          # 图表选择条件（数据形态前提）
    primary_chart: str            # 首选图表
    fallback_charts: Tuple[str, ...]  # 降级 / 备选
    chart_key: str = ""           # 同一意图下多个变体的区分键（如 stacked）

    def key(self) -> str:
        return self.chart_key or self.primary_chart


def _cap(
    category: str,
    intent: str,
    primary_method: str,
    sub_methods: List[str],
    framework: List[str],
    chart_condition: str,
    primary_chart: str,
    fallback_charts: List[str],
    chart_key: str = "",
) -> AnalysisCapability:
    return AnalysisCapability(
        category=category,
        intent=intent,
        primary_method=primary_method,
        sub_methods=tuple(sub_methods),
        framework=tuple(framework),
        chart_condition=chart_condition,
        primary_chart=primary_chart,
        fallback_charts=tuple(fallback_charts),
        chart_key=chart_key,
    )


CAPABILITIES: List[AnalysisCapability] = [
    # ---- 概览描述类 --------------------------------------------------------
    _cap("概览描述类", "现状", "Aggregate",
         ["Sum", "Count", "Distinct", "Avg", "Ratio", "Snapshot"],
         ["①总体结论", "②核心值", "③主要分布", "④高低对象", "⑤总结"],
         "单值", "KPI", ["Table"]),
    _cap("概览描述类", "总览", "Multi-Metric Summary",
         ["KPI Summary", "Descriptive Stats"],
         ["①整体结论", "②核心KPI", "③主要变化", "④重点维度", "⑤异常风险", "⑥关注项"],
         "多指标", "KPI Group / Dashboard", ["Table"]),
    _cap("概览描述类", "明细", "Detail Query",
         ["Filter", "Select", "Pagination"],
         ["①查询范围", "②记录数量", "③主要记录", "④关键字段", "⑤特殊记录", "⑥范围说明"],
         "明细记录", "Table", []),
    _cap("概览描述类", "下钻", "Drill-down",
         ["Hierarchy", "Dimension Breakdown"],
         ["①当前对象", "②下一级分布", "③主要子项", "④高低对象", "⑤异常子项", "⑥下一层"],
         "分类维度", "Bar / Table", ["Horizontal Bar"]),
    # ---- 时间变化类 --------------------------------------------------------
    _cap("时间变化类", "趋势", "Time Series",
         ["Time Bucket", "Slope", "Peak", "Trough"],
         ["①趋势方向", "②起终变化", "③高低点", "④关键拐点", "⑤波动", "⑥当前状态"],
         "单序列或少量序列", "Line", ["Area", "Table"]),
    _cap("时间变化类", "变化", "Period Comparison",
         ["YoY", "MoM", "QoQ", "WoW", "Custom"],
         ["①变化结论", "②两期值", "③增减值", "④增减率", "⑤主要变化对象", "⑥总结"],
         "单指标两期", "KPI + Change", ["Bar", "Line"]),
    _cap("时间变化类", "波动", "Volatility Analysis",
         ["Variance", "Std Dev", "CV", "Rolling Volatility"],
         ["①稳定程度", "②最大波动阶段", "③波动区间", "④最大涨跌", "⑤高波动期", "⑥当前状态"],
         "时间序列", "Line", ["Box Plot"]),
    _cap("时间变化类", "周期", "Seasonal Analysis",
         ["Period Group", "Seasonal Index"],
         ["①周期是否存在", "②高发期", "③低发期", "④周期差异", "⑤重复规律", "⑥当前周期"],
         "多周期时间数据", "Heatmap / Line", ["Table"]),
    _cap("时间变化类", "拐点", "Change Point",
         ["Turning Point", "Slope Change"],
         ["①是否转向", "②拐点时间", "③前状态", "④后状态", "⑤幅度", "⑥当前阶段"],
         "连续时间序列", "Line + Marker", ["Table"]),
    # ---- 横向比较类 --------------------------------------------------------
    _cap("横向比较类", "对比", "Grouped Comparison",
         ["Group By", "Difference", "Ratio"],
         ["①高低结论", "②对象值", "③差值/倍数", "④最大差异", "⑤整体差异", "⑥重点对象"],
         "类别较少", "Bar", ["Horizontal Bar"]),
    _cap("横向比较类", "排名", "Ranking",
         ["Sort", "Rank", "TopN", "BottomN"],
         ["①排名结论", "②TopN", "③BottomN", "④首尾差", "⑤集中度", "⑥重点对象"],
         "≥多个分类对象", "Horizontal Bar", ["Table"]),
    _cap("横向比较类", "基准", "Benchmark Comparison",
         ["Benchmark", "Deviation", "Index"],
         ["①与基准关系", "②当前vs基准", "③偏离", "④高于基准", "⑤低于基准", "⑥总结"],
         "存在标准/均值", "Bar + Reference", ["Bullet"]),
    _cap("横向比较类", "差距", "Gap Analysis",
         ["Absolute Gap", "Relative Gap"],
         ["①差距结论", "②当前值", "③绝对差", "④相对差", "⑤最大差距", "⑥主要方向"],
         "多对象差距", "Bar", ["Waterfall", "Table"]),
    # ---- 结构分布类 --------------------------------------------------------
    _cap("结构分布类", "构成", "Part-to-Whole",
         ["Share", "Percentage", "TopN Share"],
         ["①构成结论", "②最大类别", "③主要类别", "④Top3集中", "⑤长尾", "⑥结构总结"],
         "类别≤6且组成完整整体", "Donut", ["Horizontal Bar"]),
    _cap("结构分布类", "构成", "Part-to-Whole",
         ["Stacked Share"],
         ["①构成结论", "②最大类别", "③主要类别", "④Top3集中", "⑤长尾", "⑥结构总结"],
         "多对象之间比较构成", "Stacked Bar", ["Table"], chart_key="stacked"),
    _cap("结构分布类", "构成", "Part-to-Whole",
         ["Percent Stacked"],
         ["①构成结论", "②最大类别", "③主要类别", "④Top3集中", "⑤长尾", "⑥结构总结"],
         "多期构成变化", "100% Stacked Bar", ["Line", "Table"], chart_key="percent_stacked"),
    _cap("结构分布类", "分布", "Distribution Analysis",
         ["Histogram", "Quantile", "Median"],
         ["①分布形态", "②集中区间", "③均值/中位数", "④离散程度", "⑤极端值", "⑥总结"],
         "连续数值", "Histogram", ["Box Plot"]),
    _cap("结构分布类", "集中", "Concentration Analysis",
         ["CR3", "CR5", "HHI", "TopN Share"],
         ["①集中与否", "②Top占比", "③集中对象", "④长尾", "⑤集中水平", "⑥判断"],
         "分类×可加总指标", "Horizontal Bar", ["Pareto"]),
    _cap("结构分布类", "二八", "Pareto Analysis",
         ["Sort", "Cumulative Share"],
         ["①头部集中", "②累计贡献", "③达到80%的对象数", "④核心对象", "⑤长尾", "⑥总结"],
         "分类×可加总值", "Pareto Chart", ["Horizontal Bar"]),
    _cap("结构分布类", "分群", "Segmentation",
         ["Rules", "Clustering"],
         ["①群体划分", "②规模", "③群体特征", "④高值群", "⑤低值群", "⑥差异"],
         "多特征对象", "Scatter", ["Heatmap", "Bar"]),
    _cap("结构分布类", "交叉", "Cross Tabulation",
         ["Pivot", "Cross Group"],
         ["①整体关系", "②主要组合", "③高值组合", "④低值组合", "⑤结构差异", "⑥重点区域"],
         "2个分类维度", "Heatmap", ["Stacked Bar", "Table"]),
    # ---- 诊断分析类 --------------------------------------------------------
    _cap("诊断分析类", "贡献", "Contribution Analysis",
         ["Delta Contribution", "Contribution Rate"],
         ["①总体变化", "②正贡献", "③负贡献", "④贡献率", "⑤头部贡献", "⑥来源总结"],
         "总变化+维度增量", "Waterfall", ["Horizontal Bar"]),
    _cap("诊断分析类", "驱动", "Driver Analysis",
         ["Regression", "Feature Importance"],
         ["①目标变化", "②主要因素", "③方向", "④影响排序", "⑤因素组合", "⑥非因果声明"],
         "多变量", "Horizontal Bar", ["Scatter"]),
    _cap("诊断分析类", "根因", "Diagnostic Tree",
         ["Drill-down", "Rule Tree"],
         ["①问题现象", "②首层异常", "③逐层下钻", "④主要来源", "⑤证据", "⑥不确定项"],
         "多级维度", "Tree", ["Waterfall", "Bar"]),
    # ---- 异常风险类 --------------------------------------------------------
    _cap("异常风险类", "异常", "Anomaly Detection",
         ["Threshold", "Z-score", "IQR", "TS Anomaly"],
         ["①异常与否", "②异常对象", "③偏离程度", "④前后变化", "⑤集中维度", "⑥排查方向"],
         "时间异常", "Line + Marker", ["Table"]),
    _cap("异常风险类", "异常", "Anomaly Detection",
         ["IQR", "Z-score"],
         ["①异常与否", "②异常对象", "③偏离程度", "④前后变化", "⑤集中维度", "⑥排查方向"],
         "横截面异常", "Box Plot / Bar", ["Table"], chart_key="cross_section"),
    _cap("异常风险类", "风险", "Risk Scoring",
         ["Rules", "Weighted Score"],
         ["①总体风险", "②高风险对象", "③风险指标", "④风险分布", "⑤集中领域", "⑥重点关注"],
         "风险分值", "Bar / Heatmap", ["KPI", "Map"]),
    _cap("异常风险类", "阈值", "Threshold Detection",
         ["Above", "Below", "Duration"],
         ["①是否超限", "②对象", "③当前vs阈值", "④超限幅度", "⑤持续时间", "⑥预警对象"],
         "明确阈值", "Bullet / Bar+Line", ["KPI"]),
    _cap("异常风险类", "达标", "Compliance",
         ["Pass Rate", "Rule Check"],
         ["①达标情况", "②达标率", "③未达标对象", "④标准差距", "⑤不达标类型", "⑥重点对象"],
         "标准明确", "KPI + Bar", ["Table"]),
    # ---- 目标绩效类 --------------------------------------------------------
    _cap("目标绩效类", "目标", "Target Gap",
         ["Completion", "Gap", "Time Progress"],
         ["①完成结论", "②实际vs目标", "③完成率", "④剩余差距", "⑤时间vs任务", "⑥风险"],
         "单目标", "Progress / KPI", ["Bullet"]),
    _cap("目标绩效类", "绩效", "Performance Scoring",
         ["Weighted Score", "Index"],
         ["①综合结论", "②核心指标", "③优势", "④短板", "⑤排名", "⑥评价"],
         "多指标同一对象", "Radar / Scorecard", ["Bar"]),
    _cap("目标绩效类", "效率", "Efficiency Analysis",
         ["Output/Input", "Unit Efficiency"],
         ["①整体效率", "②投入产出", "③单位效率", "④高效对象", "⑤低效对象", "⑥差异"],
         "投入+产出", "Scatter", ["Bar"]),
    _cap("目标绩效类", "时效", "Duration Analysis",
         ["Mean", "Median", "Percentile", "SLA"],
         ["①整体时效", "②平均/中位", "③达标率", "④超时对象", "⑤超时程度", "⑥重点问题"],
         "时长数据", "Histogram / Box Plot", ["KPI", "Bar"]),
    # ---- 关系分析类 --------------------------------------------------------
    _cap("关系分析类", "相关", "Correlation",
         ["Pearson", "Spearman"],
         ["①相关与否", "②方向", "③强度", "④典型点", "⑤异常点", "⑥非因果声明"],
         "2个数值变量", "Scatter", ["Table"]),
    _cap("关系分析类", "关联", "Association",
         ["Co-occurrence", "Association Rule"],
         ["①主要组合", "②高频共现", "③关联强度", "④典型组合", "⑤特殊组合", "⑥总结"],
         "分类共现", "Heatmap", ["Network", "Table"]),
    # ---- 流程分析类 --------------------------------------------------------
    _cap("流程分析类", "漏斗", "Funnel Analysis",
         ["Stage Count", "Conversion"],
         ["①总转化", "②各环节量", "③转化率", "④最大流失", "⑤累计流失", "⑥瓶颈"],
         "顺序阶段", "Funnel", ["Bar"]),
    _cap("流程分析类", "流程", "Process Analysis",
         ["Stage", "Path", "Duration"],
         ["①整体流程", "②环节处理量", "③环节耗时", "④异常环节", "⑤流转路径", "⑥总结"],
         "流转关系", "Sankey", ["Timeline", "Bar"]),
    _cap("流程分析类", "瓶颈", "Bottleneck Analysis",
         ["Queue", "Stage Duration"],
         ["①瓶颈与否", "②瓶颈环节", "③耗时积压", "④环节差异", "⑤影响范围", "⑥优化重点"],
         "环节耗时", "Bar", ["Funnel", "Sankey"]),
    # ---- 群体周期类 --------------------------------------------------------
    _cap("群体周期类", "队列", "Cohort Analysis",
         ["Cohort Matrix", "Retention Curve"],
         ["①队列表现", "②批次差异", "③随时间变化", "④最好/最差", "⑤变化速度", "⑥规律"],
         "Cohort×Period", "Cohort Heatmap", ["Line"]),
    # ---- 空间分析类 --------------------------------------------------------
    _cap("空间分析类", "空间", "Geographic Distribution",
         ["Geo Group", "Geo Ranking"],
         ["①总体格局", "②高值区", "③低值区", "④集中区", "⑤区域差距", "⑥重点区域"],
         "面状行政区", "Choropleth Map", ["Bar"]),
    _cap("空间分析类", "空间", "Geographic Distribution",
         ["Point Distribution"],
         ["①总体格局", "②高值区", "③低值区", "④集中区", "⑤区域差距", "⑥重点区域"],
         "经纬度点", "Symbol Map", ["Table"], chart_key="points"),
    _cap("空间分析类", "热点", "Hotspot Detection",
         ["Getis-Ord", "KDE"],
         ["①热点与否", "②热点区", "③冷点区", "④强度", "⑤范围", "⑥重点区域"],
         "空间统计条件满足", "Hotspot Map", ["Map"]),
    # ---- 预测决策类 --------------------------------------------------------
    _cap("预测决策类", "预测", "Forecasting",
         ["ARIMA", "ETS", "ML", "TS Model"],
         ["①历史趋势", "②预测值", "③方向", "④预测区间", "⑤不确定性", "⑥风险"],
         "时间序列预测", "Forecast Line", ["Table"]),
    _cap("预测决策类", "情景", "Scenario Simulation",
         ["What-if Calculation"],
         ["①基准情景", "②假设", "③模拟值", "④差异", "⑤关键变量", "⑥结论"],
         "多情景", "Grouped Bar / Line", ["Waterfall"]),
    _cap("预测决策类", "优化", "Optimization",
         ["LP", "Constraint Solving"],
         ["①当前状态", "②目标", "③约束", "④方案", "⑤改善", "⑥代价边界"],
         "多方案", "Table / Bar", ["Pareto Frontier"]),
]

# Same intent can carry several variants (构成×3 / 异常×2 / 空间×2); the index
# keeps the first (base) row and a secondary lookup for sub-method matching.
_BY_INTENT: Dict[Tuple[str, str], List[AnalysisCapability]] = {}
for _row in CAPABILITIES:
    _BY_INTENT.setdefault((_row.category, _row.intent), []).append(_row)

CATEGORIES: Tuple[str, ...] = tuple(
    dict.fromkeys(row.category for row in CAPABILITIES)
)
INTENTS: Tuple[str, ...] = tuple(dict.fromkeys(row.intent for row in CAPABILITIES))

# Compact taxonomy injected into the planner prompt (spec §四：运行时不要整张
# 40 多行矩阵给 LLM，只给意图表）。
INTENT_TAXONOMY: Dict[str, List[str]] = {}
for _row in CAPABILITIES:
    INTENT_TAXONOMY.setdefault(_row.category, [])
    if _row.intent not in INTENT_TAXONOMY[_row.category]:
        INTENT_TAXONOMY[_row.category].append(_row.intent)

DEFAULT_CATEGORY = "概览描述类"
DEFAULT_INTENT = "现状"
SPATIAL_CATEGORY = "空间分析类"
SPATIAL_INTENT = "空间"

# ---------------------------------------------------------------------------
# Deterministic resolution ----------------------------------------------------
# ---------------------------------------------------------------------------


def taxonomy_text() -> str:
    """Render the compact intent taxonomy for the planner prompt (JSON-ish)."""
    lines = ["analysis_intents:"]
    for category, intents in INTENT_TAXONOMY.items():
        lines.append(f"  {category}: [{', '.join(intents)}]")
    return "\n".join(lines)


def categories_text() -> str:
    return " / ".join(CATEGORIES)


def lookup(category: Optional[str], intent: Optional[str]) -> Optional[AnalysisCapability]:
    """Return the base capability for (category, intent), or None."""
    if not category or not intent:
        return None
    return _BY_INTENT.get((category, intent), [None])[0]


def _normalize(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def match_sub_method(
    capability: AnalysisCapability, sub_method: Optional[str]
) -> AnalysisCapability:
    """Pick the variant whose sub-method matches (e.g. 构成+Percent Stacked).

    Matching is *exact* (normalized equality) and prefers the non-base variants,
    so a narrow sub-method name (Stacked Share / Percent Stacked / IQR / Z-score)
    is not swallowed by a broader base row via a reverse substring.  Only as a
    last resort do we allow a one-directional substring, and even then the base
    row is skipped so it cannot absorb every variant.
    """
    if not sub_method:
        return capability
    needle = _normalize(sub_method)
    if not needle:
        return capability
    candidates = _BY_INTENT.get((capability.category, capability.intent), [capability])
    base_first = candidates[0] if candidates else capability
    variants = candidates[1:] if len(candidates) > 1 else []
    # Pass 1: exact normalized match against the non-base variants first.
    for candidate in variants:
        if any(needle == _normalize(method) for method in candidate.sub_methods):
            return candidate
    # Pass 2: exact normalized match against the base row (e.g. 构成+Share).
    if any(needle == _normalize(method) for method in base_first.sub_methods):
        return base_first
    # Pass 3: one-directional substring, skipping the base row.
    for candidate in variants:
        if any(needle in _normalize(method) for method in candidate.sub_methods):
            return candidate
    return capability


def resolve_analysis(
    fields: Optional[Dict[str, Any]],
    mode: str = "sql",
) -> AnalysisCapability:
    """Turn untrusted planner ``analysis`` fields into a valid capability.

    The model only judges ①~④; this function repairs any mismatch (unknown
    category/intent, cross-category intent) deterministically and never fails:
    the default is 概览描述类/现状 for SQL and 空间分析类/空间 for spatial.
    """
    if mode != "sql":
        capability = lookup(SPATIAL_CATEGORY, SPATIAL_INTENT)
        if capability is not None:
            return capability
    fields = fields or {}
    category = str(fields.get("category") or "").strip()
    intent = str(fields.get("intent") or "").strip()
    primary_method = str(fields.get("primary_method") or "").strip()
    sub_method = str(fields.get("sub_method") or "").strip()

    capability = lookup(category, intent)
    if capability is None and intent:
        # Intent exists under another category: repair the category.
        for candidate in CAPABILITIES:
            if candidate.intent == intent:
                capability = candidate
                category = candidate.category
                break
    if capability is None and category:
        # Unknown intent but a valid category: use that category's first
        # capability (e.g. 时间变化类 -> 趋势), not the global default.
        for candidate in CAPABILITIES:
            if candidate.category == category:
                capability = candidate
                break
    if capability is None:
        capability = lookup(DEFAULT_CATEGORY, DEFAULT_INTENT)
        if capability is None:  # pragma: no cover - matrix always contains it
            capability = CAPABILITIES[0]
    if primary_method and not capability.primary_method == primary_method:
        # Trust the matrix's method for the resolved intent (template is ⑤).
        pass
    return match_sub_method(capability, sub_method or None)


def framework_text(capability: AnalysisCapability) -> str:
    """Render ⑤ answer framework as the numbered Chinese template."""
    steps = " -> ".join(capability.framework)
    return f"分析主方法：{capability.primary_method}；回答框架：{steps}"


# ---------------------------------------------------------------------------
# ⑥ Visualization rules: matrix primary chart -> rule engine chart family ------
# ---------------------------------------------------------------------------

# Primary chart names that map to more than one rule-engine family keep the
# first as preference; the rule engine still validates the data shape.
_CHART_FAMILY: Dict[str, str] = {
    "KPI": "kpi",
    "KPI Group / Dashboard": "kpi",
    "KPI + Change": "kpi",
    "KPI + Bar": "kpi",
    "Progress / KPI": "gauge",
    "Table": "table",
    "Table / Bar": "table",
    "Bar / Table": "bar",
    "Bar": "bar",
    "Bar + Reference": "bar",
    "Bar / Heatmap": "bar",
    "Bar+Line": "bar",
    "Horizontal Bar": "bar",
    "Grouped Bar / Line": "grouped-bar",
    "Stacked Bar": "stacked-bar",
    "100% Stacked Bar": "percent-stacked-bar",
    "Line": "line",
    "Line + Marker": "line",
    "Area": "area",
    "Forecast Line": "line",
    "Heatmap / Line": "heatmap",
    "Heatmap": "heatmap",
    "Cohort Heatmap": "heatmap",
    "Donut": "pie",
    "Pareto Chart": "bar",
    "Histogram": "histogram",
    "Histogram / Box Plot": "histogram",
    "Box Plot / Bar": "boxplot",
    "Scatter": "scatter",
    "Waterfall": "waterfall",
    "Funnel": "funnel",
    "Radar / Scorecard": "radar",
    "Bullet / Bar+Line": "bar",
    "Tree": "bar",
    "Sankey": "bar",
    "Choropleth Map": "map",
    "Symbol Map": "map",
    "Hotspot Map": "map",
}

# Chart families the rule engine (charts.py) actually understands for tabular
# results. ``map`` is only valid for spatial plans and is filtered out here.
_TABLE_FAMILIES = {
    "kpi", "bar", "line", "area", "pie", "scatter", "grouped-bar",
    "stacked-bar", "percent-stacked-bar", "heatmap", "treemap", "histogram",
    "radar", "funnel", "gauge", "waterfall", "boxplot", "table",
}


def chart_family(capability: AnalysisCapability, *, spatial: bool = False) -> Optional[str]:
    """Map the matrix's primary chart to a rule-engine chart family.

    Returns None when the preferred chart has no tabular family (e.g. a map for
    an SQL result), letting the existing rule engine decide on its own.
    """
    family = _CHART_FAMILY.get(capability.primary_chart)
    if family is None:
        # First family that the engine understands (compound like "Bullet").
        for fallback in capability.fallback_charts:
            family = _CHART_FAMILY.get(fallback)
            if family is not None:
                break
    if spatial:
        return family
    return family if family in _TABLE_FAMILIES else None


def chart_preference_note(capability: AnalysisCapability) -> str:
    """Human-readable ⑥/⑦ summary for the output protocol."""
    fallback = " / ".join(capability.fallback_charts) if capability.fallback_charts else "—"
    return f"首选 {capability.primary_chart}；数据形态不支持时降级 {fallback}"
