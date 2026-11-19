"""列类型推断的**唯一实现**（无内部依赖的叶子模块）。

为什么单独成模块：这段推断被三条链路同时消费——图表裁决（``charts``）、结果特征
计算（``features``）、透视布局（``pivot``）。三者过去各存一份，且 ``features`` 那份
"先按列名判时间"，与另外两份"先按值判"**语义相反**：``stat_year``（integer 年份）
在 charts 侧是 numeric、在 features 侧是 datetime，于是时间序列问数在图表侧被判成
"两个数值列"，折线图永远出不来，退化成散点/表格（2026-09-17 定位）。

``pivot`` 不能 import ``charts``（``charts`` 反过来 import ``pivot``，会成环），
这正是当初各自复制一份的起因。把实现下沉到这个不依赖任何内部模块的叶子模块，
三条链路引用同一份，也就没有再次漂移的余地。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, Sequence

# 列名里出现这些词，说明它可能是一条时间轴。
_TIME_NAME_HINTS = (
    "date", "time", "month", "year", "quarter", "week",
    "日期", "时间", "月份", "年份", "年月", "季度",
)

# Column names that denote an identifier / code rather than a measure.  An id
# column (poi_id, 行政区代码, 邮编, ...) must never be treated as a numeric metric.
_ID_NAME_HINTS = (
    "id", "code", "编号", "编码", "邮编", "postal", "代码", "行政区划代码", "区划代码",
)

# 整数值时间列的判定规则：列名提示 -> 该时间单位的合法值域。
# 只凭列名判时间会把 ``year_over_year_rate``（比率）一起卷进来；只凭值域又会把
# ``unit_count`` 里的 2024 当成年份。两个条件同时成立才认。
_YEAR_MIN, _YEAR_MAX = 1900, 2100
_PERIOD_INT_RANGES = (
    (("year", "年份", "年度", "年"), _YEAR_MIN, _YEAR_MAX),
    (("month", "月份", "月度"), 1, 12),
    (("quarter", "季度"), 1, 4),
    (("week", "周", "week"), 1, 53),
)


def _looks_like_id(name_lower: str) -> bool:
    for hint in _ID_NAME_HINTS:
        if (
            name_lower == hint
            or name_lower.endswith(hint)
            or f"_{hint}" in name_lower
            or f"-{hint}" in name_lower
        ):
            return True
    return False


def _is_numeric(value: Any) -> bool:
    from .metric_semantics import number
    return number(value) is not None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    # 整数值的年份（``stat_year = 2015``）。只在真正的年份区间内接受，避免把编号、
    # 数量列里的数字当时间；调用方通常已经通过列名把范围收窄。
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and _YEAR_MIN <= value <= _YEAR_MAX:
        return datetime(value, 1, 1)
    if isinstance(value, float) and value.is_integer() and _YEAR_MIN <= value <= _YEAR_MAX:
        return datetime(int(value), 1, 1)
    if isinstance(value, str):
        stripped = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(stripped, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(stripped)
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> Optional[int]:
    """整数值取整（int / 整值 float / 整数字符串），非整数返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
        return int(numeric) if numeric.is_integer() else None
    return None


def _is_period_int_column(name_lower: str, non_null: Sequence[Any]) -> bool:
    """列名含时间单位名 + 整数值全部落在该单位的合法区间 -> 整数值时间列。"""
    for hints, low, high in _PERIOD_INT_RANGES:
        if not any(hint in name_lower for hint in hints):
            continue
        integers = [_as_int(value) for value in non_null]
        if all(value is not None and low <= value <= high for value in integers):
            return True
    return False


def _is_code_like_numeric_strings(values: Sequence[Any]) -> bool:
    """只有带前导零的固定编码（如 '01', '002'）才算编码分类，普通数字串（如 '134'）是度量。"""
    str_vals = [str(v).strip() for v in values if isinstance(v, str) and v.strip().isdigit()]
    if not str_vals or len(str_vals) != len(values):
        return False
    return any(len(s) > 1 and s.startswith("0") for s in str_vals)


def _column_kind(name: str, values: Sequence[Any]) -> str:
    """把一列判成 ``numeric`` / ``datetime`` / ``categorical``。

    判定顺序（三条链路共用，顺序本身就是契约）：

    1. 列名像 id/code（``poi_id`` / ``行政区代码`` / ``邮编``）→ ``categorical``：
       编号绝不能被当成度量；
    2. 值全是数字 → ``numeric``（``update_count`` / ``response_time_ms`` /
       ``timeout_cnt``：列名带时间词也不影响它是个度量）。两个例外：
       * 列名指向某个时间单位、且整数值落在该单位合法值域（``stat_year`` 2015、
         ``month_no`` 1~12）→ ``datetime``；
       * 值带前导零的编码串（``"01"``、``"002"``）→ ``categorical``；
    3. 值全都能解析成日期 → ``datetime``；
    4. 其余 → ``categorical``。
    """
    name_lower = name.lower()
    if _looks_like_id(name_lower):
        return "categorical"
    non_null = [value for value in values if value is not None and value != ""]
    if non_null and all(_is_numeric(value) for value in non_null):
        if _is_period_int_column(name_lower, non_null):
            return "datetime"
        if _is_code_like_numeric_strings(non_null):
            return "categorical"
        return "numeric"
    if non_null and all(_parse_datetime(value) is not None for value in non_null):
        return "datetime"
    return "categorical"

