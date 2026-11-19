"""Deterministic pivot layout decisions for tabular query results.

The pivot table itself is rendered on the client by AntV S2; this module only
decides *how* a flat result set should be laid out: which columns become row
dimensions, which one becomes the column header dimension, which ones are
measures and how they must be aggregated.

Everything here is rule based. No model call, no randomness: the same result
set always produces the same layout, which keeps the "LLM never decides the
presentation" invariant of the product intact.

Two correctness rules matter and are easy to get wrong:

1. **Aggregation must not double count.** The result set coming back from SQL is
   usually *already* aggregated (``GROUP BY city, type``).  If every
   (row dims + column dim) combination appears exactly once, any aggregation
   function is equivalent to the identity, so we safely use ``sum``.  Only when
   combinations repeat do we have to pick a real aggregation, and then the
   column/question semantics decide (``avg`` for averages, ``max`` for peaks,
   ...) instead of blindly summing averages.
2. **A dimension with a huge cardinality must not go into the column header.**
   It would explode the table sideways.  Time-ish dimensions are preferred for
   the header (horizontal trend reading), otherwise the smallest dimension that
   still has more than one member wins, and anything above
   ``MAX_COLUMN_CARDINALITY`` stays on the row side.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .coltypes import _column_kind

# Question phrases that explicitly ask for a pivot / crosstab layout.  These are
# narrower than the chart hints for "交叉/矩阵" (which a heatmap also answers),
# so a user saying 透视表 always gets a pivot table.
PIVOT_HINTS = (
    "透视表", "透视", "交叉表", "多维表", "多维表格", "汇总表", "交叉汇总",
    "pivot table", "pivot", "crosstab", "cross tab", "cross-tab",
)

# Dimensions that read better across the column header (trend / period reading).
_COLUMN_TIME_HINTS = (
    "year", "quarter", "month", "week", "date", "time", "period",
    "年", "季", "月", "周", "日", "期", "时间",
)

# Aggregation inference, most specific first.  ``sum`` is the safe default
# because a duplicate-free result set makes every function equivalent.
_AGG_HINTS = (
    ("avg", ("avg", "average", "mean", "平均", "均值", "人均", "均价")),
    ("max", ("max", "maximum", "peak", "最大", "最高", "峰值")),
    ("min", ("min", "minimum", "最小", "最低")),
)

# Column names that explicitly denote a sum / count measure.  These win outright
# over a question-level hint so a global 「平均」does not flip an unrelated sum.
_SUM_NAME_HINTS = ("sum", "总量", "合计", "count", "数量", "总计", "汇总", "个数", "笔数")

# Layout guards.
MAX_COLUMN_CARDINALITY = 12
MAX_VALUES = 4
MAX_ROW_CARDINALITY = 200


def pivot_hint(question: str) -> bool:
    """True when the question explicitly asks for a pivot / crosstab layout."""
    if not question:
        return False
    lowered = question.lower()
    return any(hint in lowered for hint in PIVOT_HINTS)


def _cardinality(rows: Sequence[Dict[str, Any]], column: str) -> int:
    seen = set()
    for row in rows:
        value = row.get(column)
        if value is None or value == "":
            continue
        try:
            seen.add(value)
        except TypeError:  # unhashable payload (geojson dict, list, ...)
            seen.add(repr(value))
    return len(seen)


def _unique_combinations(
    rows: Sequence[Dict[str, Any]], dimensions: Sequence[str]
) -> bool:
    """True when no (dimension tuple) repeats, i.e. the set is already grouped."""
    if not dimensions:
        return False
    seen = set()
    for row in rows:
        key = []
        for dimension in dimensions:
            value = row.get(dimension)
            try:
                hash(value)
            except TypeError:
                value = repr(value)
            key.append(value)
        seen.add(tuple(key))
    return len(seen) == len(rows)


def _is_hierarchical(
    rows: Sequence[Dict[str, Any]], parent: str, child: str
) -> bool:
    """True when the child dimension is contained in the parent dimension.

    市 -> 省 is a containment: every 市 belongs to exactly one 省, so the pair
    renders as a collapsible tree.  省 x 类型 is not: the same 类型 appears under
    many 省, so it has to stay a flat grid.
    """
    mapping: Dict[Any, Any] = {}
    for row in rows:
        parent_value = row.get(parent)
        child_value = row.get(child)
        if parent_value is None or child_value is None:
            continue
        try:
            hash(child_value)
        except TypeError:
            return False
        existing = mapping.get(child_value)
        if existing is not None and existing != parent_value:
            return False
        mapping[child_value] = parent_value
    return len(mapping) > 1


def _agg_for(column: str, question: str = "") -> str:
    from .metric_semantics import is_additive
    column_lower = column.lower()
    if not is_additive(column) and any(h in column_lower for h in ("率", "rate", "ratio", "percent", "pct", "精度", "precision", "accuracy", "误差", "error")):
        return "avg"
    # 1) An explicit sum/count measure wins outright, even if the question
    #    mentions 「平均」somewhere else (e.g. 各城市各月总量与平均耗时 -> 总量=sum).
    if any(hint in column_lower for hint in _SUM_NAME_HINTS):
        return "sum"
    # 2) avg / max / min decided by the column name.
    for agg, hints in _AGG_HINTS:
        if any(hint in column_lower for hint in hints):
            return agg
    if not question:
        return "sum"
    # 3) Only fall back to the question when the column name is silent, and only
    #    when the aggregation hint sits next to this column's name.  A sentence
    #    level 「平均」must not pollute every unrelated measure.
    question_lower = question.lower()
    if column_lower and column_lower in question_lower:
        idx = question_lower.index(column_lower)
        window = question_lower[max(0, idx - 8): idx + len(column_lower) + 8]
        for agg, hints in _AGG_HINTS:
            if any(hint in window for hint in hints):
                return agg
    return "sum"


def _select_column_dimension(
    dimensions: Sequence[str],
    kinds: Dict[str, str],
    rows: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """Pick the dimension best suited for the column header area."""
    if not dimensions:
        return None

    time_like = [
        dimension
        for dimension in dimensions
        if kinds.get(dimension) == "datetime"
        or any(hint in dimension.lower() for hint in _COLUMN_TIME_HINTS)
    ]
    for candidate in time_like:
        if 1 < _cardinality(rows, candidate) <= MAX_COLUMN_CARDINALITY:
            return candidate

    others = [dimension for dimension in dimensions if dimension not in time_like]
    # Later columns usually carry the "compare across" dimension (``SELECT
    # 地区, 类型`` -> 类型 is what you want across the header), so walk backwards
    # and take the first one whose cardinality still fits the header area.
    for candidate in reversed(others):
        if 1 < _cardinality(rows, candidate) <= MAX_COLUMN_CARDINALITY:
            return candidate
    return None


def build_pivot_spec(
    columns: Sequence[str],
    rows: Sequence[Dict[str, Any]],
    kinds: Optional[Dict[str, str]] = None,
    question: str = "",
) -> Optional[Dict[str, Any]]:
    """Return a pivot layout for ``rows`` or ``None`` when pivoting is pointless.

    The returned payload is deliberately free of AntV S2 naming so the backend
    does not depend on a specific client library version:

    ``{rows, columns, values, valueInCols, aggregations, totals, hierarchyType,
    cardinality, aggregated, note}``
    """
    if not columns or not rows:
        return None

    if kinds is None:
        kinds = {}
        for column in columns:
            values = [row.get(column) for row in rows]
            kinds[column] = _infer_kind(column, values)

    dimensions = [
        column for column in columns if kinds.get(column) in ("categorical", "datetime")
    ]
    measures = [column for column in columns if kinds.get(column) == "numeric"]
    if not dimensions or not measures:
        return None

    values = [measure for measure in measures[:MAX_VALUES]]
    column_dimension = _select_column_dimension(dimensions, kinds, rows)
    row_dimensions = [
        dimension for dimension in dimensions if dimension != column_dimension
    ]
    if not row_dimensions and column_dimension:
        # A single dimension cannot be split: keep it on the row side.
        row_dimensions = [column_dimension]
        column_dimension = None
    column_dimensions = [column_dimension] if column_dimension else []

    # A16: every row dimension is empty -> nothing to pivot, hand back to the
    #     caller so it falls back to a plain table instead of an empty spec.
    if row_dimensions and all(
        (row.get(d) is None or row.get(d) == "")
        for d in row_dimensions
        for row in rows
    ):
        return None

    grouped = _unique_combinations(
        rows, list(row_dimensions) + list(column_dimensions)
    )
    if grouped:
        aggregations = {value: "sum" for value in values}
    else:
        aggregations = {value: _agg_for(value, question) for value in values}

    # Totals / subtotals aggregate *across already-derived cells*, so an avg
    # measure must stay an average: summing averages is a double-count bug
    # (「平均办理时长」总计 = 3.2 + 4.8 + 2.9 = 10.9).  The total aggregation is
    # therefore decided independently of the cell-level aggregation.
    def _total_agg(value: str) -> str:
        name = value.lower()
        if any(h in name for h in ("avg", "average", "mean", "平均", "均值", "人均", "均价")):
            return "avg"
        return "sum"

    from .metric_semantics import is_additive
    safe_totals = all(is_additive(value) for value in values)
    total_aggs = {value: _total_agg(value) for value in values}
    primary_agg = total_aggs.get(values[0], "sum")

    # A10: multiple row dimensions must use the *combination* cardinality.  A
    #     3 省 × 20 市 × 2 类型 = 120 行 set must not collapse to rows:3 and
    #     wrongly look like a small matrix fit for a heatmap.
    if len(row_dimensions) >= 2:
        row_cardinality = len(
            {tuple(row.get(d) for d in row_dimensions) for row in rows}
        )
    elif row_dimensions:
        row_cardinality = _cardinality(rows, row_dimensions[0])
    else:
        row_cardinality = len(rows)
    column_cardinality = (
        _cardinality(rows, column_dimension) if column_dimension else 1
    )

    hierarchy_type = "grid"
    if len(row_dimensions) >= 2 and _is_hierarchical(
        rows, row_dimensions[0], row_dimensions[1]
    ):
        hierarchy_type = "tree"

    note = None
    if row_cardinality > MAX_ROW_CARDINALITY:
        note = f"行维度共 {row_cardinality} 项，建议先筛选后再透视"

    if not safe_totals:
        note = (note + "；" if note else "") + "含比例、均值或不可加总指标，已隐藏总计与小计；重复单元格的均值仅为结果行简单平均。"

    return {
        "rows": list(row_dimensions),
        "columns": list(column_dimensions),
        "values": values,
        "valueInCols": True,
        "aggregations": aggregations,
        "totals": {
            "row": {
                "showGrandTotals": safe_totals,
                "showSubTotals": safe_totals and len(row_dimensions) >= 2,
                "subTotalsDimensions": list(row_dimensions[:-1])
                if len(row_dimensions) >= 2
                else [],
                "calcTotals": {"aggregation": primary_agg},
                "calcSubTotals": {"aggregation": primary_agg},
                "label": "总计",
                "subLabel": "小计",
                "reverseLayout": False,
                "reverseSubLayout": False,
            },
            "col": {
                "showGrandTotals": safe_totals and bool(column_dimensions),
                "showSubTotals": False,
                "subTotalsDimensions": [],
                "calcTotals": {"aggregation": primary_agg},
                "label": "总计",
                "subLabel": "小计",
                "reverseLayout": False,
                "reverseSubLayout": False,
            },
        },
        "hierarchyType": hierarchy_type,
        "cardinality": {
            "rows": row_cardinality,
            "columns": column_cardinality,
            "cells": row_cardinality * column_cardinality,
        },
        "aggregated": not grouped,
        "note": note,
    }


def _infer_kind(column: str, values: Sequence[Any]) -> str:
    """本模块缺 kinds 时的兜底推断。

    直接复用 ``coltypes._column_kind``（唯一实现）：这里过去另存了一份副本，且与
    charts 侧语义不一致——``stat_year`` 这类整数值年份会被判成 numeric，于是在透视
    布局里变成**度量**而不是维度（2026-09-17 收敛）。
    """
    return _column_kind(column, values)
