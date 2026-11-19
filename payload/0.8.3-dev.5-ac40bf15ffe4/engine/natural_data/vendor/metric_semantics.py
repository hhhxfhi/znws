"""Conservative aggregation semantics shared by summaries and model features."""
import math
import re

_COUNT_WITH_UNIT = re.compile(r'(?<![指系分倍均参序编总账])数\s*[（(]\s*(?:件|次|人|户|家|本|张|批|项|个|条|宗|份|台|套)\s*[）)]\s*$')


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value.replace(',', '') if isinstance(value, str) else value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def is_additive(column: str) -> bool:
    """Never sum ratios, averages, precision, scores or snapshot balances.

    Aliases without a declared business meaning remain non-additive. A SQL SUM
    alias is not sufficient evidence when it still names a rate/average.
    """
    name = column.lower()
    if re.search(r'rate|ratio|percent|pct|share|density|average|avg|mean|median|accuracy|precision|error|score|balance|stock|率|占比|比例|比重|份额|百分|密度|浓度|单价|均|精度|误差|评分|得分|存量|库存|结存|余额|指数', name):
        return False
    return bool(_COUNT_WITH_UNIT.search(name) or re.search(r'^n$|output|count|cnt|total|sum|num|quantity|amount|budget|cost|expense|expenditure|revenue|area|volume|length|points|点位数|项目数|记录数|条数|产值|数量|个数|金额|面积|体量|长度|总数|合计', name))
