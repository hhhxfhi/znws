"""Bind a requested period grain to supported SQL or published metric evidence."""
import re
import unicodedata
from dataclasses import dataclass, replace
from operator import add, mul, sub

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from .vendor.dialect import sqlglot_name


_FISCAL_QUARTER_NAME = r'(?:财季|(?:财年|财政(?:年度)?|财务|会计(?:年度)?)季度)'
_QUARTER_NAME = r'(?:自然季度|日历季度|' + _FISCAL_QUARTER_NAME + r'|季度)'
_REQUESTS = {
    'year': r'按\s*(?:年(?:份|度)?(?!\s*[末龄级底])|自然年(?!\s*[末底]))|逐年|每(?:个)?年(?:份|度)?|各年(?:份|度)?',
    'quarter': r'按(?:照)?\s*(?:' + _QUARTER_NAME + r'|季(?!节))|逐(?:财)?季|(?:每(?:个)?|各)' + _QUARTER_NAME,
    'month': r'按\s*月(?:份|度)?(?!\s*末)|逐月|每(?:个)?月|各月(?:份|度)?',
    'day': r'按\s*(?:日期|天|日(?!期|历))|逐日|每天|每日',
}
_PERIOD_WORDS = {'year': '年度', 'quarter': '季度', 'month': '月度', 'day': '日度',
                 'summary': '未按时间分组的汇总', 'mixed': '混合时间粒度'}
_NEGATION = re.compile(r'(?:不用|不要|无需|不需|不必|不|别|取消)\s*$')
_RANKING_BY_CATEGORY = re.compile(r'(?:按|以)\s*(?:年度|年份|年)\s*(?:的)?(?:总量|合计|总数|累计值?)\s*(?:进行)?(?:排名|排行|排序|降序|升序)')
_FORECAST = re.compile(r'预测|预估|预计|展望')
_FORECAST_HISTORY = re.compile(r'历史|过去|近[一二三四五六七八九十0-9]|实际|训练|观测|往年|以往|查(?:询|看)|用.{0,12}(?:数据|记录)|(?:19|20)\d{2}\s*年?\s*(?:到|至|[-—–~])\s*(?:19|20)\d{2}')
_CONTINUING = re.compile(r'其他.{0,6}不变|其[余它].{0,6}不变|(?:只|仅)(?:改|看|查)|(?:时间|统计)(?:范围|期间)改为|那(?:么)?(?:看|查)')
_QUARTER_PREFIX = r'(?:(?:按(?:照)?|逐|每(?:个)?|各)\s*)?'
_FISCAL_QUARTER = re.compile(_QUARTER_PREFIX + _FISCAL_QUARTER_NAME)
_NATURAL_QUARTER = re.compile(_QUARTER_PREFIX + r'(?:自然|日历)季度')
_FISCAL_YEAR = r'(?:财年|财政年度|财务年度|会计年度)'
_START_DATE = (r'(?P<month>1[0-2]|0?[1-9]|十[一二]?|[一二三四五六七八九])\s*月'
               r'(?:\s*(?P<day>\d{1,2}|[一二三四五六七八九十]+)\s*[日号])?')
_FISCAL_START = (
    re.compile(_FISCAL_YEAR + r'\s*(?:的)?\s*(?:从|自|起于|(?:起始|开始)(?:月份?)?\s*(?:为|是|在)?)\s*(?:每年)?\s*' + _START_DATE),
    re.compile(_FISCAL_YEAR + r'\s*(?:从|按|以)?\s*(?:每年)?\s*' + _START_DATE + r'\s*(?:开始|起算|起计|为起点)'),
)
_DATE_TYPE = re.compile(r'^(?:DATE|DATETIME(?:2|OFFSET)?|(?:SMALL)?DATETIME|TIMESTAMP(?:TZ)?)(?:\(\d+\))?(?: (?:WITH(?: LOCAL)?|WITHOUT) TIME ZONE)?$', re.I)
_SQLITE_TEXT_TYPE = re.compile(r'^(?:TEXT|(?:VAR)?CHAR(?:ACTER)?)(?:\(\d+\))?$', re.I)
_GRAINS = {'year': 'year', 'yy': 'year', 'yyyy': 'year', 'quarter': 'quarter', 'q': 'quarter',
           'month': 'month', 'mm': 'month', 'day': 'day', 'dd': 'day', 'date': 'day', 'datetime': 'day'}
_ORDER = {'year': 0, 'quarter': 1, 'month': 2, 'day': 3}
_FORMATS = {'%Y': 'year', '%Y-%m': 'month', '%Y/%m': 'month', '%Y%m': 'month',
            '%Y-%m-%d': 'day', '%Y/%m/%d': 'day', '%Y%m%d': 'day', '%Y-"Q"Q': 'quarter'}


def _clarified_texts(host):
    return [unicodedata.normalize('NFKC', item['answer']) for item in host.get('clarifications', [])
            if item.get('call_id') and item.get('question_id') and isinstance(item.get('answer'), str)]


def _quarter_calendar(text):
    def present(pattern):
        return any(not _NEGATION.search(text[:match.start()]) for match in pattern.finditer(text))

    if present(_FISCAL_QUARTER):
        return 'fiscal'
    if present(_NATURAL_QUARTER):
        return 'natural'
    for pattern in _FISCAL_START:
        for match in pattern.finditer(text):
            if _NEGATION.search(re.sub(r'按(?:照)?\s*$', '', text[:match.start()])):
                continue
            january_first = match['month'] in ('1', '01', '一') and match['day'] in (None, '1', '01', '一')
            return 'natural' if january_first else 'fiscal'
    return None


def _fiscal_quarter_requested(host):
    text = unicodedata.normalize('NFKC', str(host.get('user_text') or ''))
    calendar = next((basis for answer in reversed(_clarified_texts(host)) if (basis := _quarter_calendar(answer))), None)
    calendar = calendar or _quarter_calendar(text)
    if not calendar and _CONTINUING.search(text) and not re.search(r'取消|重新|明细', text):
        previous = (host.get('recent_questions') or [])[-1:]
        if previous:
            calendar = _quarter_calendar(unicodedata.normalize('NFKC', str(previous[0])))
    return calendar == 'fiscal'


def requested_grain(host):
    """A date filter or a forecast target does not prescribe a result axis."""
    host = host or {}
    text = unicodedata.normalize('NFKC', str(host.get('user_text') or ''))
    # Prefer an actual user answer from the native clarification over earlier text.
    answers = _clarified_texts(host)

    def find(value):
        grains = set()
        for clause in re.split(r'[，,。；;\n!?！？]', value):
            for pattern in _FISCAL_START:
                clause = pattern.sub('', clause)
            for grain, pattern in _REQUESTS.items():
                for match in re.finditer(pattern, clause):
                    before = clause[:match.start()]
                    if _NEGATION.search(before) or _RANKING_BY_CATEGORY.match(clause, match.start()):
                        continue
                    intent = _FORECAST.search(clause)
                    if _FORECAST.search(value) and (intent and intent.start() < match.start()
                                                    or not _FORECAST_HISTORY.search(clause)):
                        # A forecast's input grouping is only bound when it is
                        # independently stated as a grouping of actual history.
                        continue
                    grains.add(grain)
        return grains

    confirmed = set().union(*(find(answer) for answer in answers))
    grains = confirmed or find(text)
    if not grains and _CONTINUING.search(text) and not re.search(r'取消|重新|明细', text):
        previous = (host.get('recent_questions') or [])[-1:]
        grains = find(str(previous[0])) if previous else set()
    return next(iter(grains)) if len(grains) == 1 else None


def _evidence(requested, actual, columns, state, reason):
    result = {'schema_version': 1, 'requested': requested, 'actual': actual, 'columns': columns,
              'state': state, 'reason': reason}
    if state == 'checked':
        composite = any(column.get('part') and column['grain'] == actual for column in columns)
        result['axis_fields'] = [column['field'] for column in columns if column['grain'] == actual
                                 or composite and column['grain'] == 'year']
    return result


def _compare(requested, actual, columns, *, fiscal_quarter=False):
    if fiscal_quarter and actual in (None, 'quarter'):
        return _evidence(requested, None, columns, 'unknown', 'fiscal_quarter_unverified')
    if actual is None:
        return _evidence(requested, None, columns, 'unknown', 'unproven_time_axis')
    if actual == requested:
        return _evidence(requested, actual, columns, 'checked', 'matched')
    reason = 'missing_time_axis' if actual == 'summary' else 'different_time_grain'
    return _evidence(requested, actual, columns, 'conflict', reason)


def require_grain(evidence):
    if evidence and evidence['state'] == 'conflict':
        if evidence['reason'] == 'invalid_natural_quarter':
            raise ValueError('PERIOD_GRAIN_MISMATCH: 季度表达式没有按自然季度分桶（1–3月、4–6月、7–9月、10–12月）。'
                             "SQLite 可在同一日期列上使用 strftime('%Y', 日期列) || '-Q' || "
                             "((CAST(strftime('%m', 日期列) AS INTEGER) - 1) / 3 + 1)。"
                             '保留原筛选，SELECT 和 GROUP BY 使用同一分桶。')
        want, actual = (_PERIOD_WORDS[evidence[key]] for key in ('requested', 'actual'))
        raise ValueError(f'PERIOD_GRAIN_MISMATCH: 用户要求按{want}输出，当前结果为{actual}。'
                         f'请保留原统计范围和业务口径，在 SELECT 和 GROUP BY 中使用同一{want}分桶，'
                         '或使用已发布指标的对应期间。图表、表格和正文使用同一组结果。')
    return evidence


@dataclass(frozen=True)
class Period:
    grain: str
    table: str
    column: str
    part: bool = False
    grouped: bool = False
    invalid: bool = False


def _format_grain(node):
    fmt = node.args.get('format')
    if isinstance(fmt, exp.Literal) and fmt.is_string:
        return _FORMATS.get(fmt.this)
    return None


def _literal_int(node):
    return int(node.this) if isinstance(node, exp.Literal) and node.is_int else None


def _period(node, lookup):
    """Resolve the period of a selected expression without reading its alias."""
    node = node.unnest()
    if isinstance(node, exp.Column) and not node.is_star:
        return lookup(node)
    operand, grain, part = node.this, None, False
    if isinstance(node, (exp.TsOrDsToDate, exp.TsOrDsToTimestamp)):
        return _period(operand, lookup)
    if isinstance(node, exp.StrToDate):
        return _period(operand, lookup) if _format_grain(node) else None
    if isinstance(node, exp.DPipe):
        suffix = node.expression
        if isinstance(suffix, exp.Literal) and suffix.is_string and suffix.this == '-01':
            period = _period(operand, lookup)
            if period and not period.part and period.grain == 'month':
                return period
        return None
    if isinstance(node, exp.Cast):
        cast_to = node.args.get('to')
        if not isinstance(cast_to, exp.DataType):
            return None
        period = _period(operand, lookup)
        if cast_to.this in (exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR, exp.DataType.Type.CHAR,
                            exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP,
                            exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPNTZ,
                            exp.DataType.Type.DATETIME) or (period and (period.part or period.grain == 'year')
                                                       and cast_to.this in exp.DataType.INTEGER_TYPES):
            return period
        return None
    if isinstance(node, (exp.DateTrunc, exp.TimestampTrunc)):
        grain = _GRAINS.get(str(node.args.get('unit')).strip("'").lower())
    elif isinstance(node, exp.TimeToStr):
        grain = _format_grain(node)
    elif isinstance(node, exp.Extract):
        grain = _GRAINS.get(str(node.this).strip("'").lower())
        operand, part = node.expression, grain in ('quarter', 'month')
    elif isinstance(node, (exp.Year, exp.Quarter, exp.Month)):
        grain = type(node).__name__.lower()
        part = grain != 'year'
    elif isinstance(node, exp.Date):
        zone = node.args.get('zone')
        grain = {'start of year': 'year', 'start of month': 'month'}.get(zone.this) if isinstance(zone, exp.Literal) else 'day'
        if node.expressions:
            return None
    elif isinstance(node, (exp.Substring, exp.Left)):
        size = _literal_int(node.args.get('length' if isinstance(node, exp.Substring) else 'expression'))
        if isinstance(node, exp.Left) or _literal_int(node.args.get('start')) == 1:
            grain = {4: 'year', 7: 'month', 10: 'day'}.get(size)
    if not grain or not isinstance(operand, exp.Expression):
        return None
    base = _period(operand, lookup)
    if base is None or base.part or _ORDER[grain] > _ORDER[base.grain]:
        return None
    return Period(grain, base.table, base.column, part, base.grouped and grain == base.grain, base.invalid)


def _value_inside_cast(node):
    node = node.unnest()
    while isinstance(node, exp.Cast) and isinstance(to := node.args.get('to'), exp.DataType) and not to.expressions and (
        to.this in exp.DataType.INTEGER_TYPES or to.this in (exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR)
    ):
        node = node.this.unnest()
    return node


def _sqlite_calendar_column(node, format_string, lookup):
    node = _value_inside_cast(node)
    if not isinstance(node, exp.TimeToStr) or node.args.get('format') != exp.Literal.string(format_string):
        return None
    date_input = node.this
    if (not isinstance(date_input, exp.TsOrDsToTimestamp) or set(date_input.args) != {'this'}
            or not isinstance(column := date_input.this.unnest(), exp.Column)):
        return None
    origin = lookup(column, sqlite_text=True)
    return origin if origin and origin.grain == 'day' and not origin.part else None


_SQLITE_INT_OPERATORS = {exp.Add: add, exp.Sub: sub, exp.Mul: mul}
_MONTHS = tuple(range(1, 13))
_NATURAL_QUARTERS = tuple((month - 1) // 3 + 1 for month in _MONTHS)


def _sqlite_month_values(node, lookup):
    """Evaluate a bounded integer month expression across all calendar months."""
    node = _value_inside_cast(node)
    if (number := _literal_int(node)) is not None and abs(number) <= 1000:
        return None, (number,) * len(_MONTHS)
    if origin := _sqlite_calendar_column(node, '%m', lookup):
        return origin, _MONTHS
    operator = _SQLITE_INT_OPERATORS.get(type(node))
    if not operator and not (isinstance(node, exp.Div) and node.args.get('typed')):
        return None
    left = _sqlite_month_values(node.this, lookup)
    right = _sqlite_month_values(node.expression, lookup)
    if not left or not right or left[0] and right[0] and left[0] != right[0]:
        return None
    if operator:
        values = tuple(operator(a, b) for a, b in zip(left[1], right[1]))
    elif 0 not in right[1]:
        # SQLite integer division truncates toward zero.
        values = tuple((abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1) for a, b in zip(left[1], right[1]))
    else:
        return None
    return (left[0] or right[0], values) if all(abs(value) <= 1000000 for value in values) else None


def _composed_quarter(node, lookup, dialect):
    node = node.unnest()
    if isinstance(node, exp.Add) and isinstance(year_factor := node.this.unnest(), exp.Mul) and (
        _literal_int(year_factor.expression.unnest()) in (10, 100)
    ):
        year_expr, quarter_expr = year_factor.this, node.expression
    elif isinstance(node, exp.DPipe) and isinstance(prefix := node.this.unnest(), exp.DPipe) and (
        prefix.expression == exp.Literal.string('-Q')
    ):
        year_expr, quarter_expr = prefix.this, node.expression
    else:
        return None
    date_columns = set(year_expr.find_all(exp.Column))
    if len(date_columns) != 1 or date_columns != set(quarter_expr.find_all(exp.Column)):
        return None  # Different physical references, including self-join aliases, can name different rows.
    year_value, quarter_value = _value_inside_cast(year_expr), _value_inside_cast(quarter_expr)
    year, quarter = _period(year_value, lookup), _period(quarter_value, lookup)
    if (isinstance(year_value, (exp.Extract, exp.Year, exp.TimeToStr))
            and isinstance(quarter_value, (exp.Extract, exp.Quarter))
            and year and year.grain == 'year' and not year.part and quarter and quarter.grain == 'quarter' and quarter.part
            and (year.table, year.column) == (quarter.table, quarter.column)):
        return Period('quarter', year.table, year.column)
    if dialect != 'sqlite' or len(list(quarter_expr.walk())) > 40:
        return None
    year = _sqlite_calendar_column(year_expr, '%Y', lookup)
    months = _sqlite_month_values(quarter_expr, lookup)
    if not year or not months or months[0] != year:
        return None
    return Period('quarter', year.table, year.column, invalid=months[1] != _NATURAL_QUARTERS)


def _period_key(terms):
    complete = [term for term in terms if not term.part]
    parts = [term for term in terms if term.part]
    if any(not any((term.table, term.column) == (key.table, key.column) and key.grain == 'year'
                   for key in complete) for term in parts):
        return None
    return max((t.grain for t in terms), key=_ORDER.get, default=None)


def _group_projection(grouped, items, selected, key):
    index = _literal_int(grouped)
    if index is not None:
        return index - 1 if 1 <= index <= len(items) else None
    physical = isinstance(grouped, exp.Column) and any(key(grouped.name) in fields for fields in selected.values())
    return next((index for index, item in enumerate(items) if item.unalias().unnest() == grouped
                 or isinstance(grouped, exp.Column) and not grouped.table and not physical and item.alias == grouped.name), None)


def _projected_periods(tree, source, dialect):
    """Resolve unambiguous physical columns and simple scoped projections."""
    key = str.casefold if dialect == 'sqlite' else str
    normalize_identifiers(tree, dialect=sqlglot_name(dialect))
    tables = {key(table['name']): table for table in source.get('tables', [])}
    scopes = {}

    def source_fields(ref, *, sqlite_text=False):
        if isinstance(ref, Scope):
            return scopes.get(id(ref), {})
        if not isinstance(ref, exp.Table) or not isinstance(ref.this, exp.Identifier):
            return {}
        path = key('.'.join(part.name for part in ref.parts))
        matches = [t for name,t in tables.items() if name == path or not ref.db and name.endswith('.' + path)]
        if len(matches) != 1:
            return {}
        table = matches[0]
        fields = {}
        for column in table.get('columns', []):
            grain = _GRAINS.get((column.get('time_semantics') or {}).get('grain'))
            column_type = column.get('type') or ''
            if not grain and (_DATE_TYPE.fullmatch(column_type) or sqlite_text and _SQLITE_TEXT_TYPE.fullmatch(column_type)):
                grain = 'day'
            fields[key(column['name'])] = Period(grain, table['name'], column['name']) if grain else None
        return fields

    projections, groups, uncertain = [], [], False
    for scope in traverse_scope(tree):
        if not isinstance(scope.expression, exp.Select):
            continue
        relations = {key(alias): ref for alias, (_, ref) in scope.selected_sources.items()}
        selected = {alias: source_fields(ref) for alias, ref in relations.items()}
        sqlite_text_fields = None

        def lookup(column, *, sqlite_text=False):
            nonlocal sqlite_text_fields
            if column.db or column.catalog:
                return None
            fields_by_alias = selected
            if sqlite_text:
                # SQLite can store ISO dates as TEXT. Only a supported date
                # extractor uses these origins, never a raw numeric/text axis.
                if sqlite_text_fields is None:
                    sqlite_text_fields = {alias: source_fields(ref, sqlite_text=True) for alias, ref in relations.items()}
                fields_by_alias = sqlite_text_fields
            if column.table:
                return fields_by_alias.get(key(column.table), {}).get(key(column.name))
            possible = [fields[key(column.name)] for fields in fields_by_alias.values() if key(column.name) in fields]
            return possible[0] if len(possible) == 1 else None

        def projected_period(node):
            period = _period(node, lookup) or _composed_quarter(node, lookup, dialect)
            if period is None and dialect == 'sqlite' and (origin := _sqlite_calendar_column(node, '%Y-%m', lookup)):
                return replace(origin, grain='month')
            return period

        items = scope.expression.selects
        projections = [projected_period(item.unalias()) for item in items]
        group = scope.expression.args.get('group')
        uncertain = bool(group and any(value for name,value in group.args.items() if name != 'expressions'))
        groups = []
        for term in group.expressions if group and not uncertain else ():
            grouped = term.unnest()
            index = _group_projection(grouped, items, selected, key)
            period = projections[index] if index is not None else projected_period(grouped)
            expression = items[index].unalias().unnest() if index is not None else grouped
            if period:
                grouped_period = replace(period, grouped=True)
                groups.append(grouped_period)
                if index is not None:
                    projections[index] = grouped_period
            elif not isinstance(expression, (exp.Column, exp.Literal)):
                uncertain = True
        names = scope.outer_columns or [item.alias_or_name for item in items]
        fields = {}
        for name, period in zip(names, projections):
            if name and name != '*':
                name = key(name)
                fields[name] = None if name in fields else period
        scopes[id(scope)] = fields
    return projections, groups, uncertain


def sql_evidence(sql, source, host):
    requested = requested_grain(host)
    if not requested:
        return None
    fiscal_quarter = requested == 'quarter' and _fiscal_quarter_requested(host)

    def compare(actual, columns):
        return _compare(requested, actual, columns, fiscal_quarter=fiscal_quarter)

    dialect = source.get('dialect')
    try:
        tree = parse_one(sql, read=sqlglot_name(dialect))
        if not isinstance(tree, exp.Select) or tree.find(exp.SetOperation):
            return compare(None, [])
        names = [item.alias_or_name for item in tree.expressions]
        projections, groups, uncertain = _projected_periods(tree, source, dialect)
    except (ValueError, SqlglotError):
        return compare(None, [])
    if not projections or any(item.is_star for item in tree.expressions):
        return compare(None, [])
    if requested == 'quarter' and not fiscal_quarter and any(period and period.grouped and period.invalid for period in [*groups, *projections]):
        return _evidence(requested, None, [], 'conflict', 'invalid_natural_quarter')
    axes = [{'field': name, 'grain': period.grain, **({'part': True} if period.part else {})}
            for name,period in zip(names,projections) if name and period]
    projected = _period_key([period for period in projections if period])
    grouped = _period_key([period for period in projections if period and period.grouped])
    effective = _period_key(groups)
    if uncertain:
        return compare(None, axes)
    if effective and (not grouped or _ORDER[effective] > _ORDER[grouped]):
        return compare(effective if effective != requested else 'summary', axes)
    if projected and (projected == grouped or projected != requested):
        if projected == requested and any(period and not name for name,period in zip(names,projections)):
            return compare(None, axes)
        return compare(projected, axes)
    # A scalar aggregate conclusively lacks an axis. A bare grouped text or
    # numeric column may be a calendar key even in a source with no mapping.
    if (not axes and not tree.args.get('group') and not tree.find(exp.CTE, exp.Subquery, exp.Join, exp.Window)
            and all(isinstance(item.unalias(), (exp.AggFunc, exp.Literal)) for item in tree.expressions)
            and any(isinstance(item.unalias(), exp.AggFunc) for item in tree.expressions)):
        return compare('summary', [])
    return compare(None, axes)


def metric_evidence(periods, host):
    requested = requested_grain(host)
    if not requested:
        return None
    fiscal_quarter = requested == 'quarter' and _fiscal_quarter_requested(host)
    shapes = {'year': r'\d{4}', 'quarter': r'\d{4}-Q[1-4]',
              'month': r'\d{4}-(?:0[1-9]|1[0-2])', 'day': r'\d{4}-\d{2}-\d{2}'}
    actual = {next((grain for grain,shape in shapes.items() if re.fullmatch(shape, str(period))), None)
              for period in periods or []}
    if None in actual:
        return _compare(requested, None, [], fiscal_quarter=fiscal_quarter)
    if not actual:
        return _compare(requested, 'summary', [])
    if len(actual) != 1:
        return _compare(requested, 'mixed', [])
    grain = next(iter(actual))
    return _compare(requested, grain, [{'field': 'period', 'grain': grain}], fiscal_quarter=fiscal_quarter)
