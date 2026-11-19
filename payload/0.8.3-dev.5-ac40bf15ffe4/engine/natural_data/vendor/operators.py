"""声明式确定性算子库 for Analysis Skills.

从原生 ``skills/csv-data-analysis/scripts/csv_analyzer.py`` 迁移而来的统计
方法论（中位数 / 标准差 / 变异系数 / 分位数 / 偏度 / 离群点 / 相关性），
重写为**声明式算子**以适配产品版地基。

设计约束（四条，违反其一即破坏产品地基）：

1. **纯标准库** —— 不引入 numpy / pandas。``features.py`` 同样是纯标准库，
   确定性算子层是安全关键路径，不应扩大依赖面与版本漂移风险。
2. **零代码执行** —— 算子由技能定义的 ``feature_rules`` 声明式启用，
   不接受任意表达式、脚本路径或可调用对象。
3. **永不抛错** —— 数据不足或形态不符时返回可读说明，交由 LLM 在 prompt 中
   据实声明「该项数据不足以判断」，而不是让整条问数链路失败。
4. **确定性** —— 同一输入必得同一输出，可审计、可复现。

算子清单与参数见 :data:`OPERATORS`；``skills/schema.py`` 的白名单由本模块
**派生**（``supported_keys()``），不允许两处硬编码各自维护。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

__all__ = [
    "OPERATORS",
    "OperatorSpec",
    "METRIC_LABELS",
    "META_KEYS",
    "metric_label",
    "operator_catalog",
    "supported_keys",
    "supported_params",
    "run_operators",
    "describe_operators",
]


# ---------------------------------------------------------------------------
# 基础工具（纯标准库）
# ---------------------------------------------------------------------------


def _numeric_text(text: str) -> str:
    """Normalize numeric text: strip whitespace and thousands separators.

    与 ``features._num`` 保持同一口径（那里已支持 ``"1,234"``），否则同一份结果集
    在基础特征里算得出、在算子里算不出。
    """
    return text.strip().replace(",", "")


def _is_number(value: Any) -> bool:
    """True when the value can be used as a number.

    SQL result sets routinely carry numerics as **strings**: the live product
    result set is ``{"区县": "越秀区", "项目数量": "134"}``, not ``134``.
    The first implementation only accepted ``int``/``float``, so every operator
    silently degraded to 「数据不足」on real data while unit tests (which used
    native numbers) passed — 2026-09-24, diagnosed via ``metrics=5`` in the
    server log against an expected 26.
    """
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        text = _numeric_text(value)
        if not text:
            return False
        try:
            float(text)
        except ValueError:
            return False
        return True
    return False


def _to_float(value: Any) -> Optional[float]:
    """Coerce a numeric value (native number or numeric string) to float."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(_numeric_text(value))
        except ValueError:
            return None
    return None


def _fmt(value: Optional[float], digits: int = 2) -> str:
    """Format a number for prompt embedding; '' when unavailable."""
    if value is None:
        return ""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""
    rounded = round(float(value), digits)
    if rounded == int(rounded) and abs(rounded) < 1e15:
        return str(int(rounded))
    return f"{rounded:.{digits}f}".rstrip("0").rstrip(".")


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return numerator / denominator


def _quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated quantile (matches numpy/pandas default 'linear')."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    position = (n - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[int(position)])
    span = sorted_values[upper] - sorted_values[lower]
    return float(sorted_values[lower]) + span * (position - lower)


def _skewness(values: Sequence[float]) -> Optional[float]:
    """Adjusted Fisher-Pearson skewness (aligned with ``pandas.Series.skew``)."""
    n = len(values)
    if n < 3:
        return None
    mean = sum(values) / n
    m2 = sum((x - mean) ** 2 for x in values) / n
    m3 = sum((x - mean) ** 3 for x in values) / n
    if m2 <= 0:
        return 0.0
    g1 = m3 / (m2 ** 1.5)
    return math.sqrt(n * (n - 1)) / (n - 2) * g1


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    dev_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    dev_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if dev_x <= 0 or dev_y <= 0:
        return None
    return covariance / (dev_x * dev_y)


# ---------------------------------------------------------------------------
# 分级判定（直接迁移 csv_analyzer 的阈值口径，保持跨版本一致）
# ---------------------------------------------------------------------------


def classify_skewness(value: float) -> str:
    """偏度分级（迁移自 ``csv_analyzer.classify_skewness``，阈值不得改动）。"""
    magnitude = abs(value)
    if magnitude >= 1:
        return "明显偏态"
    if magnitude >= 0.5:
        return "中度偏态"
    return "近似对称"


def classify_cv(value: float) -> str:
    """变异系数分级（迁移自 ``csv_analyzer.classify_cv``，阈值不得改动）。"""
    if value >= 100:
        return "极高波动"
    if value >= 50:
        return "高波动"
    if value >= 20:
        return "中等波动"
    return "低波动"


# ---------------------------------------------------------------------------
# 列选择
# ---------------------------------------------------------------------------


@dataclass
class _Series:
    """A numeric column extracted from the result set."""

    column: str
    labels: List[str]
    values: List[float]

    def sorted_values(self) -> List[float]:
        return sorted(self.values)


def _label_column(columns: Sequence[str], rows: Sequence[Mapping[str, Any]], exclude: str) -> str:
    """Pick the first categorical-looking column to label numeric values with."""
    for column in columns:
        if column == exclude:
            continue
        sample = [row.get(column) for row in rows[:5]]
        populated = [value for value in sample if value not in (None, "")]
        if populated and not all(_is_number(value) for value in populated):
            return column
    return ""


def _pick_series(
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    requested: Optional[str] = None,
) -> Optional[_Series]:
    """Resolve the metric column (explicit request, else first numeric column)."""
    if not columns or not rows:
        return None
    if requested:
        if requested not in columns:
            return None
        target = requested
    else:
        target = ""
        for column in columns:
            sample = next(
                (row.get(column) for row in rows if _is_number(row.get(column))), None
            )
            if sample is not None:
                target = column
                break
        if not target:
            return None

    label_col = _label_column(columns, rows, target)
    labels: List[str] = []
    values: List[float] = []
    for index, row in enumerate(rows):
        # 数值可能是字符串形式（见 _is_number 说明），统一走 _to_float 归一
        converted = _to_float(row.get(target))
        if converted is None:
            continue
        values.append(converted)
        if label_col:
            labels.append(str(row.get(label_col) or f"第{index + 1}行"))
        else:
            labels.append(f"第{index + 1}行")
    if not values:
        return None
    return _Series(column=target, labels=labels, values=values)


def _all_numeric_columns(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> List[str]:
    result: List[str] = []
    for column in columns:
        if any(_is_number(row.get(column)) for row in rows):
            result.append(column)
    return result


def _series_for_column(
    column: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> Optional[_Series]:
    return _pick_series(columns, rows, requested=column)


# ---------------------------------------------------------------------------
# 算子实现
# ---------------------------------------------------------------------------


def _op_dispersion(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """中位数 / 标准差 / 变异系数 + 波动分级。"""
    series = _pick_series(columns, rows, params.get("metric_column"))
    if series is None or len(series.values) < 2:
        return {"dispersion": "数据不足（需至少 2 个数值）"}

    mean = statistics.fmean(series.values)
    median = statistics.median(series.values)
    try:
        stdev = statistics.stdev(series.values)
    except statistics.StatisticsError:
        return {"dispersion": "数据不足（无法计算标准差）"}

    cv = _safe_div(stdev, abs(mean))
    metrics: Dict[str, Any] = {
        "dispersion_column": series.column,
        "mean": _fmt(mean),
        "median": _fmt(median),
        "std": _fmt(stdev),
    }
    if cv is not None:
        cv_percent = abs(cv) * 100
        metrics["cv"] = f"{_fmt(cv_percent, 1)}%"
        metrics["cv_level"] = classify_cv(cv_percent)
    # 均值与中位数的相对位置是分布形态的粗略指示；与三阶矩偏度可能不一致
    # （数据轻微左偏时偏度仍可能为小正数），故输出成带方向的整句而非裸数字，
    # 避免 LLM 把两个方向相反的指标都当成强证据。
    if median:
        diff = mean - median
        if diff > 0:
            metrics["mean_vs_median"] = f"均值高于中位数 {_fmt(abs(diff))}（右偏迹象）"
        elif diff < 0:
            metrics["mean_vs_median"] = f"均值低于中位数 {_fmt(abs(diff))}（左偏迹象）"
        else:
            metrics["mean_vs_median"] = "均值与中位数相等"
        metrics["mean_over_median"] = _fmt(_safe_div(mean, median) or 0, 3)
    return metrics


def _op_skewness(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """偏度系数 + 偏态分级。"""
    series = _pick_series(columns, rows, params.get("metric_column"))
    if series is None or len(series.values) < 3:
        return {"skewness": "数据不足（需至少 3 个数值）"}
    value = _skewness(series.values)
    if value is None:
        return {"skewness": "数据不足（无法计算偏度）"}
    level = classify_skewness(value)
    # 分级为「近似对称」时不再强调方向：0.04 这类极小正值报「右偏」会让 LLM
    # 写出与分级自相矛盾的结论。
    if level == "近似对称":
        direction = "近似对称（方向不显著）"
    elif value > 0:
        direction = "右偏（长尾在高值）"
    else:
        direction = "左偏（长尾在低值）"
    return {
        "skewness_column": series.column,
        "skewness": _fmt(value, 3),
        "skewness_level": level,
        "skewness_direction": direction,
    }


def _op_quantiles(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """分位数（p5/p25/p75/p95）与四分位距，并定位中位区间对象。"""
    series = _pick_series(columns, rows, params.get("metric_column"))
    if series is None or len(series.values) < 4:
        return {"quantiles": "数据不足（需至少 4 个数值）"}

    ordered = series.sorted_values()
    q25 = _quantile(ordered, 0.25)
    q75 = _quantile(ordered, 0.75)
    metrics: Dict[str, Any] = {
        "quantile_column": series.column,
        "p5": _fmt(_quantile(ordered, 0.05)),
        "q25": _fmt(q25),
        "q75": _fmt(q75),
        "p95": _fmt(_quantile(ordered, 0.95)),
    }
    if q25 is not None and q75 is not None:
        metrics["iqr"] = _fmt(q75 - q25)

    paired = sorted(zip(series.values, series.labels), key=lambda item: item[0])
    if len(paired) >= 3:
        metrics["min_object"] = f"{paired[0][1]}({_fmt(paired[0][0])})"
        metrics["max_object"] = f"{paired[-1][1]}({_fmt(paired[-1][0])})"
    return metrics


def _op_outliers(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """IQR 法离群点检测（Tukey fence），返回具体对象而非仅计数。"""
    series = _pick_series(columns, rows, params.get("metric_column"))
    if series is None or len(series.values) < 4:
        return {"outliers": "数据不足（需至少 4 个数值）"}

    multiplier_raw = params.get("iqr_multiplier", 1.5)
    try:
        multiplier = float(multiplier_raw)
    except (TypeError, ValueError):
        multiplier = 1.5
    if multiplier <= 0:
        return {"outliers": f"iqr_multiplier 非法（{multiplier_raw}），应为正数"}

    ordered = series.sorted_values()
    q25 = _quantile(ordered, 0.25)
    q75 = _quantile(ordered, 0.75)
    if q25 is None or q75 is None:
        return {"outliers": "数据不足（无法计算四分位）"}
    iqr = q75 - q25
    if iqr <= 0:
        return {
            "outlier_count": "0",
            "outliers": "无（四分位距为 0，数据高度集中）",
        }

    lower = q25 - multiplier * iqr
    upper = q75 + multiplier * iqr
    flagged = [
        (value, label)
        for value, label in zip(series.values, series.labels)
        if value < lower or value > upper
    ]
    flagged.sort(key=lambda item: abs(item[0] - statistics.median(ordered)), reverse=True)

    metrics: Dict[str, Any] = {
        "outlier_rule": f"IQR×{_fmt(multiplier, 1)} 围栏 [{_fmt(lower)} , {_fmt(upper)}]",
        "outlier_count": str(len(flagged)),
    }
    if flagged:
        metrics["outliers"] = "、".join(f"{label}({_fmt(value)})" for value, label in flagged[:5])
    else:
        metrics["outliers"] = "无（全部落在围栏内）"
    return metrics


def _op_correlation(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """Pearson 相关系数，输出绝对值最高的若干对。"""
    numeric = _all_numeric_columns(columns, rows)
    if len(numeric) < 2:
        return {"correlation": "数值列不足 2 个，无法计算相关性"}

    series_map: Dict[str, _Series] = {}
    for column in numeric:
        series = _series_for_column(column, columns, rows)
        if series is not None and len(series.values) >= 3:
            series_map[column] = series
    if len(series_map) < 2:
        return {"correlation": "有效数值列不足，无法计算相关性"}

    top_pairs_raw = params.get("top_pairs", 3)
    try:
        top_pairs = max(1, int(top_pairs_raw))
    except (TypeError, ValueError):
        top_pairs = 3

    pairs: List[Tuple[float, str, str]] = []
    names = list(series_map.keys())
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            a, b = series_map[left], series_map[right]
            shared = min(len(a.values), len(b.values))
            if shared < 3:
                continue
            coefficient = _pearson(a.values[:shared], b.values[:shared])
            if coefficient is None:
                continue
            pairs.append((coefficient, left, right))

    if not pairs:
        return {"correlation": "无法计算（数据长度不足或存在零方差列）"}

    pairs.sort(key=lambda item: abs(item[0]), reverse=True)
    formatted = [
        f"{left}×{right}={_fmt(coefficient, 2)}"
        for coefficient, left, right in pairs[:top_pairs]
    ]
    strongest = pairs[0][0]
    return {
        "correlation_method": "pearson",
        "correlation_pairs": "、".join(formatted),
        "correlation_strongest": _fmt(strongest, 2),
        "correlation_strength": (
            "强相关" if abs(strongest) >= 0.7 else ("中度相关" if abs(strongest) >= 0.4 else "弱相关")
        ),
    }


def _op_concentration(
    columns: Sequence[str], rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> Dict[str, Any]:
    """头部集中度（原 ``compute_concentration`` 行为保持不变，含 top_n 语义）。"""
    top_n_raw = params.get("top_n", 3)
    try:
        top_n = int(top_n_raw)
    except (TypeError, ValueError):
        top_n = 3
    if top_n < 1:
        return {"concentration": f"top_n 非法（{top_n_raw}），应为正整数"}

    series = _pick_series(columns, rows, params.get("metric_column"))
    if series is None:
        return {"concentration": "无数值列可用于集中度计算"}

    from .metric_semantics import is_additive
    if not is_additive(series.column):
        return {"concentration": "比例、均值或未声明可加性的指标不计算总量占比"}

    ordered = sorted(zip(series.values, series.labels), key=lambda item: item[0], reverse=True)
    positives = [value for value, _ in ordered if value > 0]
    total = sum(positives)
    if total <= 0:
        return {"concentration": "数值列合计为 0，无法计算集中度"}

    head = sum(value for value, _ in ordered[:top_n] if value > 0)
    ratio = round(head / total * 100, 1)
    metrics: Dict[str, Any] = {
        "concentration_column": series.column,
        f"top_{top_n}_concentration_ratio": f"{ratio}%",
    }
    if len(ordered) > top_n and top_n > 0:
        metrics["head_objects"] = "、".join(
            f"{label}({_fmt(value)})" for value, label in ordered[:top_n]
        )
    return metrics


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorSpec:
    """One declaratively-enabled deterministic operator."""

    key: str
    label: str
    description: str
    params: Tuple[str, ...]
    fn: Callable[
        [Sequence[str], Sequence[Mapping[str, Any]], Mapping[str, Any]], Dict[str, Any]
    ]


OPERATORS: Dict[str, OperatorSpec] = {
    "compute_dispersion": OperatorSpec(
        key="compute_dispersion",
        label="离散程度",
        description="中位数 / 标准差 / 变异系数 + 波动分级（低/中等/高/极高）",
        params=("metric_column",),
        fn=_op_dispersion,
    ),
    "compute_skewness": OperatorSpec(
        key="compute_skewness",
        label="分布偏态",
        description="偏度系数 + 偏态分级，并给出右偏/左偏方向",
        params=("metric_column",),
        fn=_op_skewness,
    ),
    "compute_quantiles": OperatorSpec(
        key="compute_quantiles",
        label="分位数与四分位距",
        description="p5 / q25 / q75 / p95、IQR，以及最大最小对象定位",
        params=("metric_column",),
        fn=_op_quantiles,
    ),
    "detect_outliers": OperatorSpec(
        key="detect_outliers",
        label="离群点检测",
        description="Tukey IQR 围栏法，输出具体离群对象与偏离值",
        params=("metric_column", "iqr_multiplier"),
        fn=_op_outliers,
    ),
    "compute_correlation": OperatorSpec(
        key="compute_correlation",
        label="相关性",
        description="Pearson 相关系数，输出绝对值最高的若干列对与强度分级",
        params=("top_pairs",),
        fn=_op_correlation,
    ),
    "compute_concentration": OperatorSpec(
        key="compute_concentration",
        label="头部集中度",
        description="前 N 位对象占总量比例（原有算子，行为保持不变）",
        params=("top_n", "metric_column"),
        fn=_op_concentration,
    ),
}

#: 所有算子的具名参数（用于 schema 校验，避免把参数名当算子名误报）
_PARAM_KEYS: Tuple[str, ...] = tuple(
    sorted({param for spec in OPERATORS.values() for param in spec.params})
)

#: 顶层保留键（控制开关，不参与算子执行）
_CONTROL_KEYS: Tuple[str, ...] = ("metric_column",)


def supported_keys() -> Tuple[str, ...]:
    """All legal top-level keys for ``feature_rules`` (operators + params)."""
    return tuple(sorted(set(OPERATORS) | set(_PARAM_KEYS) | set(_CONTROL_KEYS)))


def supported_params() -> Tuple[str, ...]:
    """All legal parameter names (for error messages)."""
    return tuple(sorted(set(_PARAM_KEYS) | set(_CONTROL_KEYS)))


#: 指标键 → 中文标签。**单一来源**，供两处共用：① prompt 渲染
#: （``features.render_features_zh``）；② 降级输出（``dynamic.deterministic_fallback``）。
#: 未登记的键回退为原名，算子库演进时无需同步维护标签表。
METRIC_LABELS: Dict[str, str] = {
    "mean": "均值",
    "median": "中位数",
    "std": "标准差",
    "cv": "变异系数",
    "cv_level": "波动分级",
    "mean_vs_median": "均值与中位数关系",
    "mean_over_median": "均值/中位数",
    "skewness": "偏度",
    "skewness_level": "偏态分级",
    "skewness_direction": "偏态方向",
    "p5": "5%分位",
    "q25": "25%分位",
    "q75": "75%分位",
    "p95": "95%分位",
    "iqr": "四分位距",
    "min_object": "最小值对象",
    "max_object": "最大值对象",
    "outlier_rule": "离群判定围栏",
    "outlier_count": "离群点数量",
    "outliers": "离群对象",
    "correlation_method": "相关方法",
    "correlation_pairs": "相关列对",
    "correlation_strongest": "最强相关系数",
    "correlation_strength": "相关强度",
    "head_objects": "头部对象",
    "dispersion_column": "离散度统计列",
    "skewness_column": "偏度统计列",
    "quantile_column": "分位统计列",
    "concentration_column": "集中度统计列",
}

#: 仅供程序判定的元数据键 —— 它们说明「统计的是哪一列」而非业务数值，
#: 不进入 prompt 正文，也不进入降级输出正文。
META_KEYS: FrozenSet[str] = frozenset(
    {
        "dispersion_column",
        "skewness_column",
        "quantile_column",
        "concentration_column",
        "correlation_method",
    }
)

#: 降级输出时把算子指标归入对应框架步骤的关键词提示。
#: 顺序即优先级；一个指标可命中多个步骤，不做去重（宁可重复也不漏报）。
STEP_METRIC_HINTS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("质量", "完整", "概况", "体检", "记录"), ("mean", "median")),
    (("偏态", "形态", "对称", "分布"), ("skewness", "skewness_level", "skewness_direction", "mean_vs_median")),
    (("离散", "波动", "差异", "集中趋势"), ("std", "cv", "cv_level")),
    (("极端", "最大", "最小", "峰"), ("max_object", "min_object")),
    (("分位", "区间", "中间", "跨度"), ("q25", "q75", "p5", "p95", "iqr")),
    (("集中", "头部", "二八", "梯队", "格局"), ("head_objects",)),
    (("离群", "异常", "偏离"), ("outlier_count", "outliers", "outlier_rule")),
    (("相关", "关系", "关联"), ("correlation_pairs", "correlation_strongest", "correlation_strength")),
)


def metric_label(key: str) -> str:
    """指标键的中文标签（未登记时回退原名）。"""
    label = METRIC_LABELS.get(key)
    if label:
        return label
    # 动态键：``top_{n}_concentration_ratio``
    prefix, suffix = "top_", "_concentration_ratio"
    if key.startswith(prefix) and key.endswith(suffix):
        middle = key[len(prefix) : -len(suffix)]
        if middle.isdigit():
            return f"前{middle}位集中度"
    return key


def operator_catalog() -> List[Dict[str, Any]]:
    """结构化算子目录，供管理端渲染声明式算子编辑器。

    管理端**不得硬编码算子清单**——否则算子库演进后编辑器会与后端漂移，
    正是 AGENTS.md §6.4 记载的那类「查询成功但结果怪」的隐性缺陷。
    """
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "params": list(spec.params),
        }
        for spec in OPERATORS.values()
    ]


def describe_operators() -> str:
    """Human-readable operator catalogue, for error messages and docs."""
    lines = []
    for spec in OPERATORS.values():
        params = f"（参数：{', '.join(spec.params)}）" if spec.params else ""
        lines.append(f"{spec.key} —— {spec.label}：{spec.description}{params}")
    return "；".join(lines)


def run_operators(
    feature_rules: Mapping[str, Any],
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Execute every operator enabled by ``feature_rules``.

    Args:
        feature_rules: Declarative switchboard, e.g.
            ``{"compute_dispersion": True, "compute_concentration": True, "top_n": 5}``.
        columns: Result-set column names.
        rows: Result-set rows.

    Returns:
        Flattened ``custom_metrics`` dict. Never raises: an operator that cannot
        run reports why in-place, so the LLM can state the gap explicitly.
    """
    metrics: Dict[str, Any] = {}
    if not feature_rules or not columns or not rows:
        return metrics

    for key, spec in OPERATORS.items():
        if not feature_rules.get(key):
            continue
        try:
            produced = spec.fn(columns, rows, feature_rules)
        except Exception as exc:  # noqa: BLE001 - 确定性算子绝不阻断问数
            metrics[f"{key}_error"] = f"算子执行失败：{type(exc).__name__}"
            continue
        if produced:
            metrics.update(produced)
    return metrics
