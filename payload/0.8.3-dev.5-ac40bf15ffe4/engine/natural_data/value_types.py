"""把以文本传回的数字列还原为数值。

有的数据库网关（如当前接入的 TBase）把结果集的每一列都声明为 text，驱动因此返回字符串
"51"、"51.0"、"1,234.5"。统计、图表和回答核验都只认数值，模型读不到数就会反复改写 SQL 重试。

只转换能可靠识别为数字的列：
- 列里每个非空值都是数字写法（可带千分位、正负号、小数、科学计数，或全部带 %）；
- 带前导零的值（如 "0123"、"007"）说明是编码，整列不转；
- 超过 15 位有效数字的整数（证件号、信用代码）整列不转；
- 目录登记为文本类型的同名字段不转；名称像编码、代码、编号、区划、电话，或年份、月份、期间的列不转；
- 空字符串在转换后的列里视为空值。已经是数值的值保持不变。
"""
import math
import re

_NUMBER = re.compile(r'^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?$|^[+-]?\.\d+$')
_LEADING_ZERO = re.compile(r'^[+-]?0\d')
_TEXT_TYPE = re.compile(r'char|text|string|clob|uuid|json|enum|bool|date|time|bytea|binary', re.I)
_NUMERIC_TYPE = re.compile(r'int|numeric|decimal|number|real|double|float|money|serial', re.I)
_IDENTIFIER_NAME = re.compile(
    r'(?:^|_)(?:id|code|no|zip|zipcode|postcode|phone|tel|mobile|xzqh|bm|dm|bh)$'
    r'|编码|代码|编号|代号|区划|证号|号码|电话|手机|邮编|身份证|信用代码|账号|卡号', re.I)
# 年份、月份、期间是分组维度，"2025" 保持文本，图表才不会把它当成度量。
_PERIOD_NAME = re.compile(r'(?:^|_)(?:year|yr|month|period|quarter|date|day|nf|yf)$|^(?:year|month|period)|年份|年度|月份|期间|季度|日期|^年$|^月$', re.I)


def _catalog_types(source):
    types = {}
    for table in (source or {}).get('tables') or []:
        for column in table.get('columns') or []:
            types.setdefault(column.get('name'), set()).add(str(column.get('type') or ''))
    return types


def _parse(text):
    """Return (number, is_percent) for a reliable numeric spelling, else None."""
    value = text.strip()
    percent = value.endswith('%')
    if percent:
        value = value[:-1].strip()
    if not _NUMBER.match(value) or _LEADING_ZERO.match(value):
        return None
    plain = value.replace(',', '')
    if not re.search(r'[.eE]', plain):
        if len(plain.lstrip('+-')) > 15:
            return None
        return int(plain), percent
    result = float(plain)
    return (result, percent) if math.isfinite(result) else None


def numeric_text_columns(columns, rows, source=None, *, projected=None):
    """Columns whose string values all parse as numbers, with the parsed rows.

    Returns (rows, converted) where converted maps each converted column to
    'number' or 'percent'. Rows are new dicts; the input is not modified.
    """
    catalog = _catalog_types(source)
    converted = {}
    parsed_columns = {}
    for column in columns:
        name = str(column)
        if name.startswith('__op_'):
            continue
        declared = {projected[name.lower()]} if projected and name.lower() in projected else catalog.get(name, set())
        if any(_TEXT_TYPE.search(t) and not _NUMERIC_TYPE.search(t) for t in declared):
            continue
        if not any(_NUMERIC_TYPE.search(t) for t in declared) and (_IDENTIFIER_NAME.search(name) or _PERIOD_NAME.search(name)):
            continue
        values = [row.get(column) for row in rows]
        texts = [v for v in values if isinstance(v, str) and v.strip()]
        if not texts:
            continue
        if any(not (v is None or isinstance(v, (int, float)) and not isinstance(v, bool) or isinstance(v, str)) for v in values):
            continue
        parsed = {}
        ok = True
        for text in texts:
            hit = _parse(text)
            if hit is None:
                ok = False
                break
            parsed[text] = hit
        if not ok:
            continue
        flags = {percent for _, percent in parsed.values()}
        if len(flags) > 1:
            continue  # "45%" next to "0.45" is ambiguous
        converted[column] = 'percent' if flags == {True} else 'number'
        parsed_columns[column] = parsed
    if not converted:
        return rows, {}
    out = []
    for row in rows:
        new = dict(row)
        for column, parsed in parsed_columns.items():
            value = new.get(column)
            if isinstance(value, str):
                new[column] = parsed[value][0] if value.strip() else None
        out.append(new)
    return out, converted
