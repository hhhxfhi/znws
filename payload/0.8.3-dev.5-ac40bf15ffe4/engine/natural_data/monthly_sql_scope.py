"""Prove a narrow calendar window and keep missing rows distinct from coverage."""

from datetime import date, datetime
import math
import re
import unicodedata

from sqlglot import exp, parse, parse_one
from sqlglot.dialects.postgres import Postgres
from sqlglot.errors import SqlglotError

from .request_scope import _and_atoms, _extract_count_subjects
from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name


_MONTHLY = re.compile(r'每个?月|逐月|按月|月度|各月')
_YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*年')
_YEAR_NUMBER = r'(?<!\d)(?:19|20)\d{2}(?!\d)'
_YEAR_SEQUENCE = re.compile(_YEAR_NUMBER + r'\s*(?:年\s*)?(?:[、,，/~—–-]|到|至|与|和|及)\s*' + _YEAR_NUMBER)
_EXPLICIT_YEAR_LIST = re.compile(_YEAR_NUMBER + r'\s*(?:年)?'
    + r'(?:\s*(?:[、,，/~—–-]|到|至|与|和|及)\s*' + _YEAR_NUMBER + r'\s*(?:年)?)+')
_RELATIVE_YEAR = re.compile(r'本年度|本年|今年|当年度?|上年度|上年|去年|前年')
_NARROW = re.compile(r'\d{1,2}\s*月|\d{4}[-/]\d{1,2}|季度|半年|截至|截止|至今|迄今|(?:到|至)(?:当前|目前|现在|今天|本月)|为止|当前日期|已覆盖|已报送|可用月份|有记录的月份|以来|之前|之后|不含|排除|同比|环比|比较|对比|预测')
_EXPLICIT_GAPS = re.compile(r'(?:缺测|缺期|缺失|未观测|未记录|无数据)[^，。；;\n]{0,12}(?:留空|保留为空|不补零|不填零)')
_PAIRED_MONTHS = re.compile(r'(?<!\d)(20\d{2})\s*年\s*(\d{1,2})\s*月'
                            r'[^\d，,。；;！？?\n]{0,38}?(?:相较于|对比|比较|比|较|与|和|及|、)\s*'
                            r'(?:(20\d{2})\s*年\s*|(同年)\s*)?(\d{1,2})\s*月(?!\s*\d)')
_MONTH_VALUE = re.compile(r'((?:19|20)\d{2}-(?:0[1-9]|1[0-2]))(?:-01(?:[ T]00:00:00(?:\.0+)?(?:Z|[+-]00(?::?00)?)?)?)?')
_DATE_TYPE = re.compile(r'^(?:DATE|DATETIME(?:2)?|TIMESTAMP)(?:\(\d+\))?(?: WITHOUT TIME ZONE)?$', re.I)
_ZERO = re.compile(r'(?:0+(?:\.0*)?|\.0+)(?:e[+-]?\d+)?', re.I)
_CALENDAR_ERROR = ('MONTHLY_COVERAGE_UNKNOWN: 月历的 LEFT JOIN 不能证明未匹配月份有业务覆盖。'
                   '沿用已发布示例中的地区、事项等业务过滤，直接按事实月份分组；'
                   '如需保留全部月份，先按月汇总事实再左连月历，直接返回右侧计数以保留 NULL。'
                   '无观测月份不可补成 0。')


class _WindowPostgres(Postgres):
    class Parser(Postgres.Parser):
        # SQLGlot's standard builder drops DATE_TRUNC's third (timezone)
        # argument. Keep that call opaque to the calendar proof.
        FUNCTIONS = {**Postgres.Parser.FUNCTIONS, 'DATE_TRUNC': lambda args: (
            Postgres.Parser.FUNCTIONS['DATE_TRUNC'](args) if len(args) == 2
            else exp.Anonymous(this='DATE_TRUNC', expressions=args))}


def _annual_request(host):
    from .temporal_scope import metric_month_request

    months = metric_month_request(host)
    if months and len(months) == 12:
        first = min(months)
        year = int(first[:4])
        if first == f'{year}-01' and max(months) == f'{year}-12':
            today = temporal_context(host.get('client_time_zone', 'UTC'))['current_date']
            return year, year, today[:7]
    text = _EXPLICIT_GAPS.sub(' ', unicodedata.normalize('NFKC', host.get('user_text', '')))
    if not _MONTHLY.search(text):
        return None
    if annual := _annual_year(host, text):
        year, today = annual
        return year, year, today.strftime('%Y-%m')
    matches = list(_EXPLICIT_YEAR_LIST.finditer(text))
    if len(matches) != 1 or _RELATIVE_YEAR.search(text):
        return None
    match, = matches
    remaining = text[:match.start()] + ' ' + text[match.end():]
    excluded = re.search(r'不(?:要|统计|计入|包括|含)|排除|剔除|除外|除去|去掉', text)
    if ('年' not in match[0] or _NARROW.search(remaining) or _YEAR.search(remaining)
            or excluded):
        return None
    years = sorted({int(year) for year in re.findall(_YEAR_NUMBER, match[0])})
    if len(years) < 2 or years != list(range(years[0], years[-1] + 1)):
        return None
    today = temporal_context(host.get('client_time_zone', 'UTC'))['current_date']
    return years[0], years[-1], today[:7]


def _annual_year(host, text):
    # Asking for the latest observed day does not set the SQL's end date.
    text = re.sub(r'[,，。；;]\s*(?:再|并)?\s*(?:请)?\s*(?:说明|告诉我)?\s*'
                  r'(?:数据|记录|观测)(?:的)?(?:截至|截止(?:到)?)\s*'
                  r'(?:哪(?:一)?天|哪(?:一)?日|什么日期|何时)[。!?？\s]*$', '', text)
    if _NARROW.search(text) or _YEAR_SEQUENCE.search(text):
        return None
    years, relative = set(_YEAR.findall(text)), set(_RELATIVE_YEAR.findall(text))
    if len(years) > 1 or years and relative or not (years or relative):
        return None
    today = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
    if years:
        return int(next(iter(years))), today
    shifts = {0 if word in ('本年度', '本年', '今年', '当年度', '当年')
              else 2 if word == '前年' else 1 for word in relative}
    if len(shifts) == 1:
        return today.year - shifts.pop(), today
    return None


def _month_operand(node):
    if isinstance(node, exp.TimeToStr):
        fmt = node.args.get('format')
        if not isinstance(fmt, exp.Literal) or fmt.this != '%Y-%m':
            return None
        node = node.this
        if isinstance(node, exp.StrToDate):
            dt_fmt = node.args.get('format')
            dpipe = node.this
            if (isinstance(dt_fmt, exp.Literal) and dt_fmt.this == '%Y-%m-%d'
                    and isinstance(dpipe, exp.DPipe)
                    and isinstance(dpipe.expression, exp.Literal) and dpipe.expression.is_string
                    and dpipe.expression.this == '-01'
                    and isinstance(dpipe.this, exp.Left)
                    and isinstance(dpipe.this.args.get('expression'), exp.Literal)
                    and dpipe.this.args['expression'].is_int
                    and dpipe.this.args['expression'].this == '7'
                    and isinstance(dpipe.this.this, exp.TimeToStr)
                    and isinstance(dpipe.this.this.args.get('format'), exp.Literal)
                    and dpipe.this.this.args['format'].this == '%Y-%m-%d'):
                node = dpipe.this.this.this
        if isinstance(node, exp.TsOrDsToTimestamp):
            node = node.this
        elif (isinstance(node, exp.Cast) and isinstance(node.args.get('to'), exp.DataType)
              and node.args['to'].this in (
                  exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP,
                  exp.DataType.Type.TIMESTAMPNTZ, exp.DataType.Type.DATETIME,
              )):
            node = node.this
    elif isinstance(node, (exp.TimestampTrunc, exp.DateTrunc)):
        if str(node.args.get('unit')).strip("'").lower() != 'month':
            return None
        node = node.this
    elif isinstance(node, exp.Substring):
        args = [node.args.get(key) for key in ('start', 'length')]
        if any(not isinstance(arg, exp.Literal) or not arg.is_int for arg in args) or [a.this for a in args] != ['1', '7']:
            return None
        node = node.this
    else:
        return None
    return node


def _month_field(node, dialect=None):
    field = _month_operand(node)
    if (dialect == 'postgres' and isinstance(node, exp.Substring)
            and isinstance(field, exp.Cast) and isinstance(field.to, exp.DataType)
            and field.to.this in (exp.DataType.Type.TEXT, exp.DataType.Type.VARCHAR)
            and not field.to.expressions):
        # _bounds still requires the input to be a physical local date or timestamp.
        field = field.this
    if (dialect == 'postgres' and isinstance(node, exp.TimeToStr)
            and isinstance(field, exp.TimestampTrunc)):
        # Both operations preserve the same calendar month only for the
        # physical local date/timestamp checked by _bounds.
        field = _month_operand(field)
    if (dialect == 'postgres' and isinstance(node, exp.Cast)
            and isinstance(node.to, exp.DataType) and node.to.this == exp.DataType.Type.DATE
            and not node.to.expressions
            and isinstance(node.this, (exp.TimestampTrunc, exp.DateTrunc))
            and not node.this.args.get('zone')):
        # date_trunc('month', clock)::date groups the same calendar month as the
        # bare trunc; a zone argument would shift the month and stays unrecognized.
        field = _month_operand(node.this)
    return field if isinstance(field, exp.Column) else None


def _literal_day(node, dialect, *, midnight_timestamp=False):
    if isinstance(node, exp.Cast):
        if dialect == 'sqlite' or node.to.this not in (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP):
            return None
        if (midnight_timestamp and node.to.this == exp.DataType.Type.TIMESTAMP
                and isinstance(node.this, exp.Literal) and node.this.is_string
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]00:00:00(?:\.0+)?', node.this.this)):
            node = exp.Literal.string(node.this.this[:10])
        else:
            node = node.this
    if isinstance(node, exp.Literal) and node.is_string and re.fullmatch(r'\d{4}-\d{2}-\d{2}', node.this):
        try:
            return date.fromisoformat(node.this)
        except ValueError:
            pass
    return None


def _source_table(ref, source):
    if not isinstance(ref, exp.Table) or not isinstance(ref.this, exp.Identifier):
        return None
    from .display_units import _table_identity
    tables = {t['name'].casefold(): t for t in source.get('tables', [])}
    name = _table_identity('.'.join(part.name for part in ref.parts), tables, str.casefold)
    return tables[name] if name else None


def _has_date(table, field):
    return any(c['name'].casefold() == field.casefold() and _DATE_TYPE.fullmatch(c.get('type') or '')
               for c in table['columns'])


def _not_null_operand(atom):
    if isinstance(atom, exp.Not) and isinstance(test := atom.this.unnest(), exp.Is) and isinstance(test.expression, exp.Null):
        return test.this.unnest()
    return None


def _bounds(tree, source, column, dialect, *, midnight_timestamp=False):
    refs = list(tree.find_all(exp.Table))
    if (len(refs) != 1 or tree.args.get('joins') or any(tree.find_all(exp.CTE, exp.Subquery))
            or not (table := _source_table(refs[0], source))):
        return None
    ref = refs[0]

    def is_field(field):
        return (isinstance(field, exp.Column) and field.name.casefold() == column.name.casefold()
                and not (field.db or field.catalog)
                and (not field.table or field.table.casefold() == ref.alias_or_name.casefold()))

    if not is_field(column) or not _has_date(table, column.name):
        return None
    lower, upper = [], []
    where = tree.args.get('where')
    predicate = where.this if where else None
    atoms = _and_atoms(predicate)
    recognized = set()
    for atom in atoms:
        if is_field(field := _not_null_operand(atom)):
            # A matching range already excludes NULL on this axis.
            recognized.add(id(field))
            continue
        if dialect == 'postgres' and isinstance(atom, exp.EQ):
            for extraction, literal in ((atom.this, atom.expression), (atom.expression, atom.this)):
                if not isinstance(literal, exp.Literal):
                    continue
                field = None
                if isinstance(extraction, exp.Extract) and extraction.name.upper() == 'YEAR' and literal.is_int:
                    field = extraction.expression
                elif (isinstance(extraction, exp.TimeToStr) and extraction.args.get('format') == exp.Literal.string('%Y')
                      and literal.is_string and re.fullmatch(r'(?:19|20)\d{2}', literal.this)):
                    field = extraction.this
                # An explicit DATE cast keeps the calendar year of the same
                # registered date field. Preserve the AST node ID for the
                # coverage check below.
                if (isinstance(field, exp.Cast) and isinstance(field.args.get('to'), exp.DataType)
                        and field.args['to'].this == exp.DataType.Type.DATE):
                    field = field.this
                if is_field(field) and 1900 <= int(literal.this) <= 2099:
                    year = int(literal.this)
                    lower.append(date(year, 1, 1))
                    upper.append(date(year + 1, 1, 1))
                    recognized.add(id(field))
                    break
        bound = _literal_day(atom.expression, dialect, midnight_timestamp=midnight_timestamp)
        if isinstance(atom, exp.GTE) and is_field(atom.this) and bound:
            lower.append(bound)
            recognized.add(id(atom.this))
        elif isinstance(atom, exp.LT) and is_field(atom.this) and bound:
            upper.append(bound)
            recognized.add(id(atom.this))
    unknown = bool(tree.args.get('having') or tree.args.get('limit') or tree.args.get('offset')) or (
        predicate is not None and any(is_field(field) and id(field) not in recognized
                                      for field in predicate.find_all(exp.Column)))
    return max(lower, default=None), min(upper, default=None), unknown


def _case_month_window(node, dialect):
    """Accept CASE labels only when every branch covers exactly its own month."""
    if (not isinstance(node, exp.Case) or node.this is not None
            or (node.args.get('default') is not None and not isinstance(node.args['default'], exp.Null))
            or not 1 <= len(node.args['ifs']) <= 36):
        return None
    windows = []
    field = None
    for branch in node.args['ifs']:
        label = branch.args.get('true')
        if not isinstance(label, exp.Literal) or not label.is_string or not re.fullmatch(r'(?:19|20)\d{2}-(?:0[1-9]|1[0-2])', label.this):
            return None
        start = date.fromisoformat(label.this + '-01')
        stop = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        atoms = _and_atoms(branch.this)
        if len(atoms) != 2 or {type(atom) for atom in atoms} != {exp.GTE, exp.LT}:
            return None
        for atom in atoms:
            if (not isinstance(atom.this, exp.Column) or (field is not None and atom.this != field)
                    or _literal_day(atom.expression, dialect) != (start if isinstance(atom, exp.GTE) else stop)):
                return None
            field = atom.this
        windows.append((start, stop))
    windows.sort()
    if any(previous[1] != following[0] for previous, following in zip(windows, windows[1:])):
        return None
    return field, windows[0][0], windows[-1][1]


def _integer_month_field(node):
    """Recognize a Gregorian YYYYMM projection only to request a calendar label."""
    node = node.unnest()
    if not isinstance(node, exp.Add) or not isinstance(year := node.this.unnest(), exp.Mul):
        return None
    scale = year.expression.unnest()
    if not isinstance(scale, exp.Literal) or not scale.is_int or scale.this != '100':
        return None
    fields = []
    for part, piece in (('YEAR', year.this), ('MONTH', node.expression)):
        piece = piece.unnest()
        if isinstance(piece, exp.Cast) and piece.to.this in (exp.DataType.Type.INT, exp.DataType.Type.BIGINT):
            piece = piece.this.unnest()
        if not isinstance(piece, exp.Extract) or piece.name.upper() != part or not isinstance(piece.expression, exp.Column):
            return None
        fields.append(piece.expression)
    return fields[0] if fields[0] == fields[1] else None


def _grouped_month(tree, *, monthly_only=False, alias_group=False, dialect=None, case_month=False, integer_month=False):
    if not isinstance(tree, exp.Select) or not (group := tree.args.get('group')):
        return None
    def date_field(node):
        field = _month_field(node, dialect)
        if field is None and case_month and (window := _case_month_window(node, dialect)):
            return window[0]
        if field is None and integer_month:
            return _integer_month_field(node)
        return field
    projections = [(i, item, field) for i, item in enumerate(tree.expressions, 1)
                   if item.alias_or_name and (field := date_field(item.unalias())) is not None]
    if len(projections) != 1:
        return None
    index, projection, field = projections[0]
    if (monthly_only and len(group.expressions) != 1
            or any(value for key, value in group.args.items() if key != 'expressions')):
        return None
    projected = projection.unalias()
    formatted_month = _month_operand(projected) if dialect == 'postgres' and isinstance(projected, exp.TimeToStr) else None
    # TO_CHAR(DATE_TRUNC('month', date_col), 'YYYY-MM') displays exactly the
    # same group as the inner month key. _bounds still checks the physical
    # date type and the actual time column.
    grouping_keys = (projected, formatted_month) if isinstance(formatted_month, exp.TimestampTrunc) else (projected,)
    if not any(item in grouping_keys or isinstance(item, exp.Literal) and item.is_int and item.this == str(index)
               or isinstance(item, exp.Column) and not item.table and item.name == projection.alias
               and not isinstance(projected, exp.Case) and (not monthly_only or alias_group)
               for item in group.expressions):
        return None
    return projection, field


def without_redundant_month_limit(tree, source, months, dialect):
    """A single observed row per requested month cannot exhaust this LIMIT."""
    from .temporal_scope import month_scope

    limit = tree.args.get('limit')
    if (not limit or not isinstance(limit.expression, exp.Literal) or not limit.expression.is_int
            or int(limit.expression.this) < len(months)
            or any(limit.args.get(k) for k in ('limit_options', 'expressions'))
            or any(tree.args.get(k) for k in ('having', 'qualify', 'offset', 'distinct'))):
        return tree
    first, last = min(months), max(months)
    if month_scope(first + '至' + last) != months:
        return tree
    candidate = tree.copy()
    candidate.set('limit', None)
    if not (grouped := _grouped_month(candidate, monthly_only=True, alias_group=True, dialect=dialect)):
        return tree
    if not (_plain_count_projection(candidate, source, alias_group=True)
            or monthly_sum_projection(candidate, source, allow_default_zero=True)):
        return tree
    group = candidate.args['group'].expressions[0]
    physical = (candidate.args.get('from') or candidate.args.get('from_'))
    table = _source_table(physical.this, source) if physical else None
    if not table or isinstance(group, exp.Column) and any(
            c['name'].casefold() == group.name.casefold() for c in table['columns']):
        return tree
    start = date.fromisoformat(first + '-01')
    tail = date.fromisoformat(last + '-01')
    stop = date(tail.year + (tail.month == 12), tail.month % 12 + 1, 1)
    if _bounds(candidate, source, grouped[1], dialect, midnight_timestamp=True) != (start, stop, False):
        return tree
    return candidate


def _requested_pair(quote):
    """Use an explicit comparison in one clause, with no cross-year guessing."""
    pairs = set()
    for year, first, second_year, same_year, second in _PAIRED_MONTHS.findall(quote):
        first, second = int(first), int(second)
        if not (1 <= first <= 12 and 1 <= second <= 12):
            continue
        if not (second_year or same_year) and abs(first - second) != 1:
            continue
        pair = tuple(sorted({f'{year}-{first:02}', f'{second_year or year}-{second:02}'}))
        if len(pair) == 2:
            pairs.add(pair)
    return list(next(iter(pairs))) if len(pairs) == 1 else []


def empty_grouped_months(result, source):
    """Attest an executed query window and its empty result, not source coverage."""
    from sqlglot.errors import SqlglotError
    from .read_limits import execution_basis

    lineage = result.get('result_lineage') or {}
    scope = result.get('request_scope') or {}
    request = scope.get('request') or {}
    receipt = result.get('receipt') or {}
    publication = result.get('business_project') or {}
    source_id = source.get('id')
    if (result.get('status') != 'succeeded' or result.get('reason_code') != 'NO_MATCH'
            or result.get('truncated') is not False or result.get('row_count') != 0 or result.get('rows') != []
            or not result.get('result_ref') or receipt.get('read_only') is not True or lineage.get('purpose') != 'answer'
            or (result.get('validation') or {}).get('state') == 'conflict'
            or not source_id or any(item.get('source_id') != source_id for item in (result, receipt, lineage))
            or not lineage.get('request_ref') or lineage['request_ref'] != request.get('user_ref') or not request.get('quote')
            or not lineage.get('config_fingerprint') or lineage['config_fingerprint'] != scope.get('config_fingerprint')
            or not publication.get('id') or not publication.get('version') or publication != source.get('business_project')
            or source.get('connection_revision') != (result.get('source_provenance') or {}).get('connection_revision')):
        return None
    sql = result.get('sql')
    basis = result.get('execution_basis') or {}
    if not sql:
        return None
    if basis:
        if not basis.get('policy_sql') or execution_basis(basis['policy_sql'], sql) != basis:
            return None
        sql = basis['policy_sql']
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('postgres', 'sqlite'):
        return None
    try:
        tree = parse_one(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    except SqlglotError:
        return None
    allowed = {'expressions', 'from', 'where', 'group', 'order'}
    if (not isinstance(tree, exp.Select) or any(value and key not in allowed for key, value in tree.args.items())
            or tree.find(exp.Subquery, exp.Exists, exp.Window, exp.Placeholder, exp.Parameter)):
        return None
    if not (grouped := _grouped_month(tree, dialect=dialect, case_month=True)):
        return None
    month, field = grouped
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    time_column = normalize_identifiers(month.copy(), dialect=dialect).alias_or_name if dialect == 'postgres' else month.alias_or_name
    if time_column not in (result.get('columns') or []):
        return None
    count_group = tree.args['group']
    for expression in count_group.expressions:
        expression = expression.unnest()
        if isinstance(expression, exp.Literal) and expression.is_int:
            position = int(expression.this)
            if not 1 <= position <= len(tree.expressions):
                return None
            expression = tree.expressions[position - 1].unalias().unnest()
        if isinstance(expression, (exp.Literal, exp.Boolean, exp.Null)):
            return None
    counts = [item.unalias().unnest() for item in tree.expressions]
    if not any(type(count) is exp.Count and not count.expressions and (
            isinstance(count.this, exp.Star) and not any(count.this.args.values())
            or isinstance(count.this, exp.Literal) and count.this.is_int and count.this.this == '1') for count in counts):
        return None
    if not (bounds := _bounds(tree, source, field, dialect)):
        return None
    start, stop, unknown = bounds
    if unknown or not start or not stop or start.day != 1 or stop.day != 1:
        return None
    if isinstance(month.unalias(), exp.Case) and _case_month_window(month.unalias(), dialect)[1:] != (start, stop):
        return None
    first, last = start.year * 12 + start.month - 1, stop.year * 12 + stop.month - 1
    if not 0 < last - first <= 36:
        return None
    query_periods = [f'{i // 12:04}-{i % 12 + 1:02}' for i in range(first, last)]
    requested = _requested_pair(unicodedata.normalize('NFKC', request['quote']))
    return {'schema_version': 1, 'state': 'unobserved', 'result_ref': result['result_ref'],
            'request_ref': request['user_ref'], 'source_id': source_id,
            'publication': {key: publication[key] for key in ('id', 'version')},
            'start_inclusive': start.isoformat(), 'end_exclusive': stop.isoformat(), 'time_column': time_column,
            'query_periods': query_periods, 'requested_periods': requested, 'request_periods_match': query_periods == requested}


def _calendar_key(tree):
    if (not isinstance(tree, exp.Select) or len(tree.expressions) != 1
            or any(value for key, value in tree.args.items() if key not in ('expressions', 'from'))):
        return None
    projection = tree.expressions[0]
    node = projection.unalias()
    formatted = isinstance(node, exp.TimeToStr)
    series = _month_operand(node) if formatted else node
    if relation := tree.args.get('from'):
        ref = relation.this
        if (not isinstance(ref, exp.Table) or ref.db not in ('', 'pg_catalog')
                or ref.catalog or not isinstance(series, exp.Column)):
            return None
        names = ref.alias_column_names or [ref.alias_or_name or 'generate_series']
        if len(names) != 1 or series.name != names[0] or series.table not in ('', ref.alias_or_name):
            return None
        series = ref.this
    if isinstance(series, exp.Cast) and series.to.this == exp.DataType.Type.DATE:
        series = series.this.unnest()
    if not projection.alias or not isinstance(series, exp.GenerateSeries):
        return None
    start, end = (series.args.get(key) for key in ('start', 'end'))
    for bound in (start, end):
        if not (isinstance(bound, exp.Cast) and bound.to.this == exp.DataType.Type.DATE
                and isinstance(bound.this, exp.Literal) and bound.this.is_string
                and _MONTH_VALUE.fullmatch(bound.this.this)):
            return None
    if not _one_month(series.args.get('step')):
        return None
    return projection.alias, formatted


def _one_month(interval):
    return (isinstance(interval, exp.Interval) and isinstance(interval.this, exp.Literal)
            and interval.this.this == '1' and str(interval.args.get('unit')).casefold() == 'month')


def _local_date_cast(node):
    node = node.unnest()
    if isinstance(node, exp.Cast) and node.to.this in (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP):
        return node.this.unnest()
    return node


def _range_joined_date(on, table, alias, raw_calendar_key):
    if raw_calendar_key is None:
        return set()
    atoms = _and_atoms(on)
    starts = {a.this.name for a in atoms if isinstance(a, exp.GTE) and isinstance(a.this, exp.Column)
              and _is_column(a.this, alias, a.this.name) and _local_date_cast(a.expression) == raw_calendar_key}
    stops = {a.this.name for a in atoms if isinstance(a, exp.LT) and isinstance(a.this, exp.Column)
             and _is_column(a.this, alias, a.this.name) and isinstance(end := _local_date_cast(a.expression), exp.Add)
             and end.this == raw_calendar_key and _one_month(end.expression)}
    return {name for name in starts & stops if _has_date(table, name)}


def _direct_calendar_counts(tree, source, right, on, is_calendar_month, month, raw_calendar_key, group_key=None):
    """Recognize a fact-table outer join that counts unmatched month groups."""
    if not (table := _source_table(right, source)) or not (group := tree.args.get('group')):
        return None
    if len(group.expressions) != 1 or any(v for k, v in group.args.items() if k != 'expressions'):
        return None
    grouped = group.expressions[0]
    if not (is_calendar_month(grouped) or grouped in (raw_calendar_key, group_key) or isinstance(grouped, exp.Literal)
            and grouped.is_int and grouped.this == str(tree.expressions.index(month) + 1)):
        return None
    alias = right.alias_or_name
    matched_date_fields = set()

    def cast_date(node):
        if isinstance(node, exp.Cast) and node.to.this == exp.DataType.Type.DATE:
            return node.this.unnest()
        return None

    def same_month(left, right_month):
        right_date, left_date = cast_date(right_month), cast_date(left)
        if isinstance(right_date, (exp.DateTrunc, exp.TimestampTrunc)):
            field = _month_field(right_date)
            calendar_month = (raw_calendar_key is not None and left_date == raw_calendar_key) or (
                isinstance(left_date, exp.DPipe) and is_calendar_month(left_date.this)
                and isinstance(left_date.expression, exp.Literal)
                and left_date.expression.is_string and left_date.expression.this == '-01')
        else:
            field = _month_field(right_month) if isinstance(right_month, exp.TimeToStr) else None
            calendar_month = is_calendar_month(left)
        if calendar_month and field is not None and field.table == alias and _has_date(table, field.name):
            matched_date_fields.add(field.name)
            return True
        return False

    for atom in _and_atoms(on):
        if isinstance(atom, exp.EQ) and not same_month(atom.this, atom.expression):
            same_month(atom.expression, atom.this)
    joined_by_range = _range_joined_date(on, table, alias, raw_calendar_key)
    matched_date_fields.update(joined_by_range)
    if not matched_date_fields:
        return None

    def right_count(node):
        if type(node) is not exp.Count or node.expressions:
            return False
        arg = node.this
        if isinstance(arg, exp.Distinct) and len(arg.expressions) == 1:
            arg = arg.expressions[0]
        return (isinstance(arg, exp.Column) and arg.table == alias and not (arg.db or arg.catalog)
                or isinstance(arg, exp.Star) or isinstance(arg, exp.Literal) and arg.is_int)

    def nullable_case(node):
        if not isinstance(node, exp.Case) or node.this or not right_count(node.args.get('default')):
            return False
        conditions = node.args.get('ifs') or []
        if len(conditions) != 1 or not isinstance(conditions[0].args.get('true'), exp.Null):
            return False
        check = conditions[0].this
        if not isinstance(check, exp.EQ):
            return False
        required_fields = matched_date_fields | set(table.get('primary_key') or [])
        return any(type(count) is exp.Count and not count.expressions
                   and isinstance(count.this, exp.Column)
                   and _is_column(count.this, alias, count.this.name)
                   and count.this.name in required_fields
                   and isinstance(zero, exp.Literal) and zero.is_number and _ZERO.fullmatch(zero.this)
                   for count, zero in ((check.this, check.expression), (check.expression, check.this)))

    def nullif_count(node):
        return (isinstance(node, exp.Nullif) and right_count(node.this)
                and isinstance(node.this.this, exp.Column)
                and isinstance(node.expression, exp.Literal) and node.expression.is_number
                and _ZERO.fullmatch(node.expression.this))

    for item in tree.expressions:
        if right_count(item.unalias().unnest()):
            raise ValueError(_CALENDAR_ERROR)
    counts = [item.alias_or_name for item in tree.expressions
              if item.alias_or_name and (nullable_case(item.unalias().unnest()) or nullif_count(item.unalias().unnest()))]
    # A join predicate matches month labels; it does not certify source coverage.
    return (month.alias_or_name, counts, (None, None, True), None) if counts else None


def _literal_month_column(tree):
    """A hand-built month axis has only unique 1..12 constants in UNION ALL."""
    if not isinstance(tree, exp.Union):
        return None
    branches = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Union) and node.args.get('distinct') is False and not any(
                value for key, value in node.args.items() if key not in ('this', 'expression', 'distinct')):
            stack.extend((node.expression, node.this))
        elif isinstance(node, exp.Select) and len(node.expressions) == 1 and not any(
                value for key, value in node.args.items() if key != 'expressions'):
            branches.append(node.expressions[0])
        else:
            return None
        if len(stack) + len(branches) > 12:
            return None
    values = [item.unalias() for item in branches]
    if any(not isinstance(value, exp.Literal) or not value.is_int or not 1 <= int(value.this) <= 12 for value in values):
        return None
    return branches[0].alias if len({int(value.this) for value in values}) == len(values) else None


def _literal_calendar_count(tree, source, left, right, on, calendar_tree):
    column = _literal_month_column(calendar_tree)
    if not column:
        return None
    for projection in tree.expressions:
        date_key = _month_operand(projection.unalias())
        if not isinstance(date_key, exp.Anonymous) or date_key.name.upper() != 'MAKE_DATE' or len(date_key.expressions) != 3:
            continue
        year, month, day = date_key.expressions
        if (not (isinstance(year, exp.Literal) and year.is_int and 1 <= int(year.this) <= 9999)
                or not (isinstance(day, exp.Literal) and day.is_int and day.this == '1')
                or not _is_column(month, left.alias_or_name, column)):
            continue
        return _direct_calendar_counts(tree, source, right, on, lambda node: node == projection.unalias(),
                                       projection, date_key, group_key=month)
    return None


def _calendar_month_join(tree, source, dialect):
    """Check a generated month axis joined to observed facts."""
    with_ = tree.args.get('with')
    from_ = tree.args.get('from')
    joins = tree.args.get('joins') or []
    if not (not (with_ and with_.args.get('recursive')) and from_ and len(joins) == 1
            and joins[0].args.get('side') == 'LEFT' and joins[0].args.get('on')):
        return None
    left, right = from_.this, joins[0].this
    if not (isinstance(left, (exp.Table, exp.Subquery)) and isinstance(right, (exp.Table, exp.Subquery))):
        return None
    ctes = {cte.alias: cte.this for cte in with_.expressions if not cte.alias_column_names} if with_ else {}

    def bound_select(ref):
        if ref.alias_column_names:
            return None
        if isinstance(ref, exp.Subquery):
            return ref.this if ref.alias else None
        return ctes.get(ref.name) if not (ref.db or ref.catalog) else None

    calendar_tree = bound_select(left)
    calendar = _calendar_key(calendar_tree)
    on = joins[0].args['on']
    if not calendar:
        return _literal_calendar_count(tree, source, left, right, on, calendar_tree)
    calendar_alias, formatted = calendar

    def is_calendar_month(node):
        if not formatted:
            if not isinstance(node, exp.TimeToStr):
                return False
            node = _month_operand(node)
        return _is_column(node, left.alias_or_name, calendar_alias)

    months = [item for item in tree.expressions if is_calendar_month(item.unalias()) and item.alias_or_name]
    if len(months) != 1:
        return None
    fact_tree = bound_select(right)
    if fact_tree is None:
        calendar_key = _month_operand(months[0].unalias()) if not formatted else None
        return _direct_calendar_counts(tree, source, right, on, is_calendar_month, months[0], calendar_key)
    grouped = _grouped_month(fact_tree, monthly_only=True)
    if not grouped or not isinstance(on, exp.EQ):
        return None
    month, field = grouped
    if not isinstance(month.unalias(), exp.TimeToStr) or not (bounds := _bounds(fact_tree, source, field, dialect)):
        return None
    counts = {item.alias for item in fact_tree.expressions if item.alias
              and type(item.unalias().unnest()) is exp.Count}
    if not any(is_calendar_month(a) and _is_column(b, right.alias_or_name, month.alias_or_name)
               for a, b in ((on.this, on.expression), (on.expression, on.this))):
        return None
    nullable_counts = []
    for item in tree.expressions:
        node = item.unalias().unnest()
        if (isinstance(node, exp.Coalesce) and len(node.expressions) == 1
                and any(_is_column(node.this, right.alias_or_name, count) for count in counts)
                and isinstance(zero := node.expressions[0], exp.Literal)
                and zero.is_number and _ZERO.fullmatch(zero.this)):
            raise ValueError(_CALENDAR_ERROR)
        if item.alias_or_name and any(_is_column(node, right.alias_or_name, count) for count in counts):
            nullable_counts.append(item.alias_or_name)
    if nullable_counts:
        return months[0].alias_or_name, nullable_counts, bounds, fact_tree
    return None


def calendar_fact_select(tree, source):
    """An outer month calendar with no extra filters over pre-aggregated facts."""
    if (not isinstance(tree, exp.Select)
            or any(value and key not in ('with', 'from', 'joins', 'expressions', 'order')
                   for key, value in tree.args.items())
            or any(isinstance(item.unalias().unnest(), (exp.Coalesce, exp.Count)) for item in tree.expressions)):
        return None
    calendar = _calendar_month_join(tree, source, sqlglot_name(source.get('dialect')))
    return calendar[3] if calendar else None


def require_calendar_observations(tree, source):
    """Reject a recognized month calendar that replaces unmatched observations."""
    dialect = sqlglot_name(source.get('dialect'))
    if dialect == 'postgres':
        for select in tree.find_all(exp.Select):
            _calendar_month_join(select, source, dialect)
            if (not select.find(exp.ExplodingGenerateSeries)
                    or not any(join.side == 'LEFT' for join in select.args.get('joins') or [])):
                continue
            for projection in select.expressions:
                value = projection.unalias().unnest()
                if isinstance(value, exp.Count):
                    raise ValueError(_CALENDAR_ERROR)
                if (isinstance(value, exp.Coalesce) and isinstance(value.this.unnest(), exp.Count)
                        and any(isinstance(item, exp.Literal) and item.is_number and _ZERO.fullmatch(item.this)
                                for item in value.expressions)):
                    raise ValueError(_CALENDAR_ERROR)


def _is_column(node, table, name):
    return isinstance(node, exp.Column) and node.table == table and node.name == name and not (node.db or node.catalog)


def _plain_count_projection(tree, source, *, alias_group, integer_month=False):
    """One month plus one additive COUNT, with no other grouping dimension."""
    grouped = _grouped_month(tree, monthly_only=True, alias_group=alias_group, integer_month=integer_month,
                             dialect=sqlglot_name(source.get('dialect')) if alias_group else None)
    if not grouped or len(tree.expressions) != 2 or tree.args.get('distinct') or tree.args.get('qualify'):
        return None
    month, field = grouped
    group_key = tree.args['group'].expressions[0]
    if isinstance(group_key, exp.Column):
        refs = list(tree.find_all(exp.Table))
        table = _source_table(refs[0], source) if len(refs) == 1 else None
        if not table or any(c['name'].casefold() == group_key.name.casefold() for c in table['columns']):
            return None  # A physical GROUP BY field wins over a projection alias.
    projection = next(item for item in tree.expressions if item is not month)
    count = projection.unalias().unnest()
    if not projection.alias_or_name or projection.alias_or_name == month.alias_or_name or type(count) is not exp.Count:
        return None
    operand = count.this.unnest() if count.this is not None else None
    if count.expressions or isinstance(operand, exp.Distinct):
        return None
    if isinstance(operand, exp.Star) and not any(operand.args.values()) or isinstance(operand, exp.Literal) and operand.is_int and operand.this == '1':
        return projection, True
    refs = list(tree.find_all(exp.Table))
    table = _source_table(refs[0], source) if len(refs) == 1 else None
    if not isinstance(operand, exp.Column) or operand.is_star or not table:
        return None
    columns = {c['name'] for c in table['columns']}
    if (operand.db or operand.catalog or operand.table not in ('', refs[0].alias_or_name)
            or operand.name not in columns):
        return None
    # The verified date window guarantees this column is non-null for every
    # participating row. A different column count may exclude null rows.
    return projection, operand.name == field.name


def _count_column(tree, source, month_column, facts=None):
    if facts is None:
        checked = _plain_count_projection(tree, source, alias_group=True)
        if checked:
            projection, counts_rows = checked
            return {'count_column': projection.alias_or_name, 'counts_rows': counts_rows}
        if observed := monthly_sum_projection(tree, source):
            projection, _ = observed
            return {'count_column': projection.alias_or_name, 'counts_rows': False,
                    'nullable_count_columns': [projection.alias_or_name], 'observed_sum': True}
        return {}
    checked = _plain_count_projection(facts, source, alias_group=False)
    if not checked or calendar_fact_select(tree, source) is None or len(tree.expressions) != 2:
        return {}
    projection, counts_rows = checked
    result = next((item for item in tree.expressions if item.alias_or_name != month_column), None)
    value = result.unalias().unnest() if result is not None else None
    if isinstance(value, exp.Column) and result.alias_or_name and value.name == projection.alias_or_name:
        return {'count_column': result.alias_or_name, 'counts_rows': counts_rows}
    return {}


def monthly_sum_projection(tree, source, *, allow_default_zero=False):
    """Verify a monthly sum comes from a single physical observation column."""
    dialect = sqlglot_name(source.get('dialect'))
    grouped = _grouped_month(tree, monthly_only=True, alias_group=True, dialect=dialect)
    if (not grouped or len(tree.expressions) != 2 or tree.find(exp.Window, exp.Subquery, exp.Distinct)
            or tree.args.get('joins') or tree.args.get('with')):
        return None
    month, _ = grouped
    projection = next(item for item in tree.expressions if item is not month)
    if not projection.alias or projection.alias == month.alias_or_name:
        return None
    refs = list(tree.find_all(exp.Table))
    table = _source_table(refs[0], source) if len(refs) == 1 else None
    if not table:
        return None
    if (isinstance(key := tree.args['group'].expressions[0], exp.Column)
            and any(c['name'].casefold() == key.name.casefold() for c in table['columns'])):
        return None
    value = projection.unalias().unnest()
    if (allow_default_zero and isinstance(value, exp.Coalesce) and len(value.expressions) == 1
            and isinstance(zero := value.expressions[0], exp.Literal) and zero.is_number and _ZERO.fullmatch(zero.this)):
        value = value.this.unnest()
    if (type(value) is not exp.Sum or value.expressions or not isinstance(field := value.this, exp.Column)
            or field.db or field.catalog or field.table not in ('', refs[0].alias_or_name)
            or not any(c['name'] == field.name for c in table['columns'])):
        return None
    return projection, value


def _annual_aggregate_window(host, sql, source):
    """Check an explicit annual flow when the executed query has a proven date axis."""
    quote = unicodedata.normalize('NFKC', host.get('user_text') or '')
    publication = source.get('business_project') or {}
    if (not host.get('user_ref') or not publication.get('version')
            or _MONTHLY.search(quote) or re.search(r'有效|在册|存量|余额|期末|年末|月末|财政.*(?:投入|支出|拨付)', quote)
            or not (annual := _annual_year(host, quote))):
        return
    from .current_month import _table, date_axis
    from .published_cast_date_axis import date_axis as published_cast_date_axis
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('postgres', 'sqlite'):
        return
    tree = parse_one(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    if (not isinstance(tree, exp.Select) or not tree.find(exp.Count, exp.Sum) or not (table := _table(tree, source))):
        return
    axis = date_axis(tree, source, table, dialect)
    capped_by_database_date = False
    if axis is None or not all(axis[1][:2]):
        # A literal inside LEAST(date, CURRENT_DATE) proves an upper cap,
        # never the exact execution window in the database's local clock.
        axis = published_cast_date_axis(tree, source, table, dialect)
        if not axis or axis[1][2]:
            axis = published_cast_date_axis(tree, source, table, dialect, allow_upper_cap=True)
            capped_by_database_date = True
    if not axis:
        return
    start, stop, unknown = axis[1]
    if unknown or not start or not stop:
        return
    year, today = annual
    first, following = date(year, 1, 1), date(year + 1, 1, 1)
    earliest_stop = today if year == today.year else following
    if start != first or not earliest_stop <= stop <= following:
        from .catalog_view import entry_code
        # Explicitly replaying a registered code with its published SQL is
        # distinct from a fresh annual request generated from that example.
        if not _YEAR.search(quote) and not re.search(r'全(?:年|省|市)|整个年度', quote):
            for example in source.get('examples', []):
                registered = example.get('logical_sql')
                if (example.get('source_id') != source.get('id') or not registered
                        or not (code := entry_code(example, 'question'))
                        or not re.search(r'(?<![a-z0-9_])'+re.escape(code)+r'(?![a-z0-9_])', quote, re.I)):
                    continue
                try:
                    if parse_one(registered, read=_WindowPostgres if dialect=='postgres' else dialect) == tree:
                        return
                except SqlglotError:
                    continue
        raise ValueError(f'TIME_SCOPE_MISMATCH: 原问要求{year}年范围，SQL 实际为 {start}（含）至 {stop}（不含）。'
                         f'请保留同一业务条件，从 {first}（含）开始查询至 {following}（不含）'
                         + ('或客户端当前日期。示例截止日不代表原问范围。' if year == today.year else '。示例截止日不代表原问范围。'))
    if capped_by_database_date:
        return
    return {'kind':'annual_aggregate','schema_version':1,'state':'checked','request_ref':host['user_ref'],
            'source_id':source['id'],'publication':{key:publication[key] for key in ('id','version')},
            'start_inclusive':start.isoformat(),'end_exclusive':stop.isoformat(),'calendar_month':today.strftime('%Y-%m'),
            'table':table['name'],'date_column':axis[0]['name'],'time_zone':host.get('client_time_zone') or 'UTC'}


def normalize_full_year_count(sql, source, host):
    """Keep an implicit full year when a count query uses a day-discovery result as its cutoff."""
    from .current_month import date_axis
    from .observed_sql_scope import _single_fact

    text = unicodedata.normalize('NFKC', (host or {}).get('user_text') or '')
    if (not (host or {}).get('user_ref') or not (source.get('business_project') or {}).get('version')
            or re.search(r'有效|在册|存量|余额|期末|年末|月末|快照', text)
            or not (period := _annual_year(host, text)) or not (fact := _single_fact(sql, source))):
        return sql
    tree, table, relation, dialect = fact
    if (any(node.comments for node in tree.walk()) or relation.alias_column_names
            or any(relation.args.get(key) for key in ('sample', 'version'))
            or any(value for key, value in tree.args.items() if key not in ('expressions', 'from', 'where', 'group', 'order'))):
        return sql
    if tree.args.get('group'):
        grouped = _grouped_month(tree, alias_group=True, dialect=dialect)
        if not _MONTHLY.search(text) or not grouped or not _plain_count_projection(tree, source, alias_group=True):
            return sql
        name = grouped[1].name
        bounds = _bounds(tree, source, grouped[1], dialect, midnight_timestamp=True)
    else:
        if (_MONTHLY.search(text) or len(tree.expressions) != 1
                or not isinstance(tree.expressions[0].unalias().unnest(), exp.Count)
                or not (axis := date_axis(tree, source, table, dialect))):
            return sql
        name, bounds = axis[0]['name'], axis[1]
    year, today = period
    first, following = date(year, 1, 1), date(year + 1, 1, 1)
    if not bounds or bounds[0] != first or bounds[1] or not (where := tree.args.get('where')):
        return sql

    def on_axis(column):
        return (isinstance(column, exp.Column) and column.name.casefold() == name.casefold()
                and not (column.db or column.catalog)
                and (not column.table or column.table.casefold() == relation.alias_or_name.casefold()))

    allowed, upper = set(), []
    for atom in _and_atoms(where.this):
        if on_axis(field := _not_null_operand(atom)):
            allowed.add(id(field))
        if not isinstance(atom, (exp.GTE, exp.LTE)) or not on_axis(atom.this):
            continue
        day = _literal_day(atom.expression, dialect, midnight_timestamp=True)
        if isinstance(atom, exp.GTE) and day == first:
            allowed.add(id(atom.this))
        if isinstance(atom, exp.LTE):
            if day is None and dialect == 'postgres':
                stamp = atom.expression
                if isinstance(stamp, exp.Cast) and stamp.to.is_type('timestamp'):
                    stamp = stamp.this
                if isinstance(stamp, exp.Literal) and stamp.is_string and re.fullmatch(
                        r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?', stamp.this):
                    try:
                        day = datetime.fromisoformat(stamp.this).date()
                    except ValueError:
                        pass
            if day is not None and first <= day < min(today, following):
                upper.append(atom)
                allowed.add(id(atom.this))
    if len(upper) != 1 or any(on_axis(col) and id(col) not in allowed for col in where.find_all(exp.Column)):
        return sql
    upper[0].replace(exp.LT(this=upper[0].this.copy(), expression=exp.Literal.string(following.isoformat())))
    return tree.sql(dialect=dialect)


_MONTHLY_INCREMENT = re.compile(
    r'(?:每(?:一)?个?月|各月|逐月|按月|月度)[^。；;！!？?\n]{0,12}?(?:新增|增量|发生量)'
    r'|(?:新增|增量)[^。；;！!？?\n]{0,12}?(?:每(?:一)?个?月|各月|逐月|按月|月度)')
_MONTHLY_INCREMENT_ERROR = (
    'MONTHLY_INCREMENT_REQUIRED: 原问要的是每月新增量，也就是每个月的当月发生量。'
    '当前 SQL 用带 ORDER BY 的窗口函数把计数累加，逐月结果会变成年初至今的累计值，'
    '与上一问的累计口径看起来一样。请去掉窗口累加，按月份分组直接输出当月计数'
    '（GROUP BY 月份，不再套 SUM(...) OVER (ORDER BY ...)）。')


def _running_count(tree):
    """Name of an ordered window over a count, i.e. a running total, or None.

    An unordered ``OVER ()`` is a share/denominator and stays legitimate; only a
    frame ordered in time turns this month's increment into a year-to-date sum.
    """
    for item in tree.expressions:
        node = item.unalias()
        if not isinstance(node, exp.Window) or not node.args.get('order'):
            continue
        if node.find(exp.Count) or node.find(exp.Sum):
            return item.alias_or_name or '累计值'
    return None


def require_monthly_increment(host, sql, source):
    """Refuse a running total where the request named a per-month increment."""
    text = (host or {}).get('user_text') or ''
    if not _MONTHLY_INCREMENT.search(text):
        return
    dialect = sqlglot_name(source.get('dialect'))
    try:
        tree = parse_one(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    except SqlglotError:
        return
    if isinstance(tree, exp.Select) and _running_count(tree):
        raise ValueError(_MONTHLY_INCREMENT_ERROR)


def closed_forecast_read(sql, source, host):
    """Keep the same authorized monthly query and cap its history at the last close."""
    from natural_analysis.forecasting import _HISTORY_CUTOFF, _HISTORY_RANGE, _RECENT, relative_forecast_horizon

    horizon = relative_forecast_horizon(host)
    dialect = sqlglot_name(source.get('dialect'))
    if not horizon or dialect not in ('postgres', 'sqlite'):
        return sql
    try:
        statements = parse(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    except SqlglotError:
        return sql
    if len(statements) != 1 or not isinstance(tree := statements[0], exp.Select):
        return sql
    if (tree.find(exp.Window) or any(tree.args.get(key) for key in ('sample', 'qualify'))
            or any(node.comments for node in tree.walk())):
        return sql
    grouped = _grouped_month(tree, monthly_only=True, alias_group=True, dialect=dialect)
    if not grouped or not (bounds := _bounds(tree, source, grouped[1], dialect, midnight_timestamp=True)):
        return sql
    start, stop, unknown = bounds
    cutoff = date.fromisoformat(horizon['start'] + '-01')
    if unknown or stop == cutoff:
        return sql
    if stop and stop < cutoff:
        text = (host or {}).get('user_text') or ''
        if (not start or any(pattern.search(text) for pattern in (_HISTORY_RANGE, _HISTORY_CUTOFF, _RECENT))
                or re.search(r'(?:19|20)\d{2}\s*年|\d{4}-\d{2}', text)):
            return sql
        where = tree.args.get('where')
        if not where:
            return sql
        ends = [atom for atom in _and_atoms(where.this) if isinstance(atom, exp.LT)
                and isinstance(atom.this, exp.Column) and atom.this.name.casefold() == grouped[1].name.casefold()
                and _literal_day(atom.expression, dialect, midnight_timestamp=True) == stop]
        if len(ends) != 1:
            return sql
        ends[0].set('expression', exp.Literal.string(cutoff.isoformat()))
        return tree.sql(dialect=dialect)
    tree.where(exp.LT(this=grouped[1].copy(), expression=exp.Literal.string(cutoff.isoformat())), copy=False)
    return tree.sql(dialect=dialect)


def bind(host, sql, source):
    host = host or {}
    require_monthly_increment(host, sql, source)
    from natural_analysis.forecasting import relative_month_window, target_mentions
    mentions = target_mentions(host)
    if mentions:
        dialect = sqlglot_name(source.get('dialect'))
        if dialect != 'postgres':
            return None
        tree = parse_one(sql, read=_WindowPostgres)
        require_calendar_observations(tree, source)
        if relative := relative_month_window(host, mentions):
            return _forecast_history_window(host, tree, source, dialect, relative)
        return None
    requested = _annual_request(host)
    if requested is None:
        if annual := _annual_aggregate_window(host, sql, source):
            return annual
        if _MONTHLY.search(host.get('user_text', '')) and _EXPLICIT_GAPS.search(host.get('user_text', '')):
            dialect = sqlglot_name(source.get('dialect'))
            if dialect == 'postgres':
                require_calendar_observations(parse_one(sql, read=_WindowPostgres), source)
        return None
    first_year, last_year, current_month = requested
    dialect = sqlglot_name(source.get('dialect'))
    tree = parse_one(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    nullable_counts, facts = [], None
    if isinstance(tree, exp.Select) and (calendar := _calendar_month_join(tree, source, dialect)):
        month_column, nullable_counts, bounds, facts = calendar
    elif grouped := _grouped_month(tree, dialect=dialect):
        projection, field = grouped
        month_column = projection.alias_or_name
        bounds = _bounds(tree, source, field, dialect)
    else:
        return None
    start, stop, unknown = bounds or (None, None, True)

    expected_start, expected_stop = date(first_year, 1, 1), date(last_year + 1, 1, 1)
    narrowed = (start is not None and start > expected_start) or (stop is not None and stop < expected_stop)
    widened = (start is not None and start < expected_start) or (stop is not None and stop > expected_stop)
    if narrowed or (first_year != last_year and widened):
        period = f'{first_year}年' if first_year == last_year else f'{first_year}年至{last_year}年'
        raise ValueError(f'TIME_SCOPE_MISMATCH: 原问要求{period}逐月，已确认 SQL 日期边界与原问不符。'
                         f'按同一授权范围使用 {expected_start}（含）至 {expected_stop}（不含）。'
                         '查询范围正确后，缺少结果的月份仍需说明覆盖未知。')
    window = {'state': 'checked' if start == expected_start and stop == expected_stop and not unknown else 'unknown'}
    if start is not None:
        window['start_inclusive'] = start.isoformat()
    if stop is not None:
        window['end_exclusive'] = stop.isoformat()
    count = _count_column(tree, source, month_column, facts) if window['state'] == 'checked' else {}
    return {'requested_months': [f'{year}-{month:02}' for year in range(first_year, last_year + 1)
                                 for month in range(1, 13)],
            'month_column': month_column, 'current_month': current_month, 'query_window': window,
            **({'nullable_count_columns': nullable_counts} if nullable_counts else {}), **count}


def _forecast_history_window(host, tree, source, dialect, relative):
    publication = source.get('business_project') or {}
    if (not host.get('user_ref') or not source.get('id') or not publication.get('id') or not publication.get('version')
            or not isinstance(tree, exp.Select) or any(tree.args.get(arg) for arg in ('sample', 'distinct', 'qualify'))):
        return None
    grouped = _grouped_month(tree, alias_group=True, dialect=dialect, integer_month=True)
    if not grouped or not (bounds := _bounds(tree, source, grouped[1], dialect, midnight_timestamp=True)):
        return None
    start, stop, unknown = bounds
    expected_start = date.fromisoformat(relative['start_inclusive'])
    expected_stop = date.fromisoformat(relative['end_exclusive'])
    count = _plain_count_projection(tree, source, alias_group=True, integer_month=True)
    counts_rows = bool(count and count[1])
    integer_month = _integer_month_field(grouped[0].unalias()) is not None
    if ((start is not None and start > expected_start)
            or (stop is not None and stop != expected_stop)
            or (counts_rows and not unknown and (start is None or stop is None or integer_month))):
        raise ValueError(f'FORECAST_HISTORY_WINDOW: 原问需 {len(relative["requested_periods"])} 个已结月份，'
                         f'请保留同一业务筛选，按 {expected_start}（含）至 {expected_stop}（不含）一次查询实际月度历史。'
                         '直接按事实月份分组并使用 YYYY-MM 月份标签，未观察月份不补零。')
    if unknown or start is None or stop != expected_stop or not counts_rows:
        return None
    if start < expected_start and (start.day != 1 or (stop.year - start.year) * 12 + stop.month - start.month > 500):
        return None
    return {'kind': 'forecast_history', 'schema_version': 1, 'state': 'checked', **relative,
            **({'available_start': start.strftime('%Y-%m')} if start < expected_start else {}),
            'request_ref': host['user_ref'], 'source_id': source['id'],
            'publication': {key: publication[key] for key in ('id', 'version')},
            'time_column': grouped[0].alias_or_name, 'value_column': count[0].alias_or_name}


def forecast_history(result, window):
    lineage = result.get('result_lineage') or {}
    publication = result.get('business_project') or {}
    if (result.get('status') != 'succeeded' or result.get('truncated')
            or (result.get('receipt') or {}).get('read_only') is not True
            or (result.get('validation') or {}).get('state') == 'conflict'
            or (result.get('checks') or {}).get('value_warnings')
            or lineage.get('purpose') != 'answer' or not result.get('result_ref')
            or lineage.get('request_ref') != window['request_ref']
            or result.get('source_id') != lineage.get('source_id') or lineage.get('source_id') != window['source_id']
            or any(publication.get(key) != value for key, value in window['publication'].items())):
        return None
    time, value = window['time_column'], window['value_column']
    rows = result.get('rows') or []
    if len(rows) != result.get('row_count') or set(result.get('columns') or []) != {time, value}:
        return None
    requested = set(window['requested_periods'])
    # A declared wider lower bound must be a well-formed month label no later
    # than the requested window start; fail closed otherwise. Leading rows stay
    # out of missing_periods (which describes the requested window only).
    available = window.get('available_start')
    if available is not None and (not _MONTH_VALUE.fullmatch(available) or available > min(requested)):
        return None
    first = available or min(requested)
    last = max(requested)
    observed = set()
    for row in rows:
        period = _MONTH_VALUE.fullmatch(str(row.get(time)))
        count = row.get(value)
        # Each grouped row count has at least one qualifying fact. A zero
        # here cannot come from that projection; absent groups stay absent.
        if (not period or not first <= period[1] <= last or period[1] in observed
                or type(count) not in (int, float) or not math.isfinite(count) or count <= 0 or int(count) != count):
            return None
        observed.add(period[1])
    return {**{key: value for key, value in window.items() if key != 'kind'}, 'result_ref': result['result_ref'],
            'observed_periods': sorted(observed), 'missing_periods': sorted(requested - observed)}


_COMPLETE_MONTHLY_INPUT = re.compile(
    r'^(?:请(?:你)?\s*)?(?:基于|根据|依据|使用|用|按)\s*'
    r'(?:当前(?:授权)?(?:数据库|数据源|库)\s*的?\s*)?'
    r'(?:完整|全部|全量|所有)\s*的?\s*(?:月度|逐月|每月)\s*的?\s*'
    r'([^，,。；;！？?\n]{1,60}?)\s*[，,]?\s*(?:预测|预估|预计|展望)'
)


def _published_monthly_projection(result, source, subject):
    from natural_analysis.forecasting import _read_sql

    tree = _read_sql(result, source)
    if not isinstance(tree, exp.Select) or any(
            value and key not in ('expressions', 'from', 'from_', 'order')
            for key, value in tree.args.items()):
        return None
    if len(tree.expressions) != 2 or any(len(projection.find_all(exp.Column).__iter__().__class__.__name__) == 0
                                         for projection in []):
        return None
    ref = (tree.args.get('from') or tree.args.get('from_'))
    ref = ref.this if ref else None
    table = _source_table(ref, source)
    if not table or any(value and key not in ('this', 'db', 'catalog', 'alias')
                        for key, value in ref.args.items()):
        return None
    alias = ref.args.get('alias')
    if alias and alias.args.get('columns'):
        return None

    case_key = str.casefold if source['dialect'] == 'sqlite' else str
    fields = {}
    for projection in tree.expressions:
        column = projection.unalias().unnest()
        if (not isinstance(column, exp.Column) or column.is_star or column.db or column.catalog
                or (column.table and case_key(column.table) != case_key(ref.alias_or_name))):
            return None
        fields[case_key(column.name)] = projection.alias_or_name
    if sorted(fields.values()) != sorted(result.get('columns') or []):
        return None

    mappings = [mapping for mapping in source.get('business_dictionary', [])
                if mapping.get('source_id') == source['id']]
    measures = [mapping for mapping in mappings if mapping.get('kind') == 'field'
                and unicodedata.normalize('NFKC', mapping.get('name') or '').strip() == subject]
    if len(measures) != 1 or measures[0].get('table') != table['name']:
        return None
    months = [mapping for mapping in mappings if mapping.get('kind') == 'time'
              and mapping.get('table') == table['name'] and mapping.get('grain') == 'month']
    if len(months) != 1:
        return None
    month_field = case_key(months[0].get('column') or '')
    value_field = case_key(measures[0].get('column') or '')
    physical_fields = {case_key(column['name']) for column in table['columns']}
    if month_field == value_field or {month_field, value_field} != set(fields) or not fields.keys() <= physical_fields:
        return None
    return fields[month_field], fields[value_field], table['name'], measures[0]['column']


def _direct_measure_scope(scope, subject, table, column):
    constraints = scope.get('constraints') or []
    unresolved = scope.get('unresolved') or []
    if not constraints:
        return not unresolved
    if len(constraints) != 1:
        return False
    constraint, = constraints
    # A field label like "业务量" can be parsed as an entity count. The only
    # ignorable hint here is that same projected measure without a distinct key.
    return (constraint.get('kind') == 'count_subject'
            and constraint.get('table') == table and constraint.get('column') == column
            and constraint.get('subject') in _extract_count_subjects(subject)
            and constraint.get('entity_proven') is False and constraint.get('distinct_required') is False
            and not constraint.get('composite_pk')
            and unresolved == [f'字段“{table}.{column}”未登记主键或实体关联外键，无法证明其为去重实体标识。'])


def complete_monthly_forecast_history(result, source):
    """Prove interior gaps in a complete, direct, published monthly series."""
    from natural_analysis.forecasting import _same_publication

    scope = result.get('request_scope') or {}
    target = scope.get('forecast_target') or {}
    request = scope.get('request') or {}
    lineage = result.get('result_lineage') or {}
    publication = source.get('business_project') or {}
    revision = source.get('connection_revision')
    receipt = result.get('receipt') or {}
    if (result.get('status') != 'succeeded' or result.get('truncated') or not result.get('result_ref')
            or receipt.get('read_only') is not True or lineage.get('purpose') != 'answer'
            or receipt.get('source_id') != source['id'] or lineage.get('source_id') != source['id']
            or not _same_publication(result, source) or not publication.get('id')
            or not publication.get('version') or revision is None
            or (result.get('validation') or {}).get('state') == 'conflict'
            or (result.get('checks') or {}).get('value_warnings')
            or not scope.get('config_fingerprint') or scope['config_fingerprint'] != lineage.get('config_fingerprint')
            or scope.get('proposal') or scope.get('inherited')
            or target.get('grain') != 'month' or target.get('evidence') != request or request.get('clarifications')
            or not request.get('user_ref') or lineage.get('request_ref') != request['user_ref']):
        return None
    quote = unicodedata.normalize('NFKC', request.get('quote') or '').strip()
    intent = _COMPLETE_MONTHLY_INPUT.match(quote)
    target_period = target.get('period') or ''
    if not intent or not (period_match := _MONTH_VALUE.fullmatch(target_period)) or target_period != period_match[1]:
        return None
    subject = intent[1].strip()
    if not (projection := _published_monthly_projection(result, source, subject)):
        return None
    time_column, value_column, table, column = projection
    if not _direct_measure_scope(scope, subject, table, column):
        return None
    rows = result.get('rows') or []
    if not 2 <= len(rows) <= 120 or len(rows) != result.get('row_count'):
        return None
    observed = set()
    for row in rows:
        value = row.get(value_column)
        period = _MONTH_VALUE.fullmatch(str(row.get(time_column)))
        if (not period or period[1] in observed or type(value) not in (int, float)
                or not math.isfinite(value)):
            return None
        observed.add(period[1])
    first, last = min(observed), max(observed)
    if last >= target_period:
        return None
    start = int(first[:4]) * 12 + int(first[5:]) - 1
    end = int(last[:4]) * 12 + int(last[5:])
    if end - start > 120:
        return None
    span = [f'{month//12:04}-{month%12+1:02}' for month in range(start, end)]
    missing = [period for period in span if period not in observed]
    if not missing:
        return None
    return {'kind': 'observed_span', 'schema_version': 1, 'state': 'checked',
            'result_ref': result['result_ref'], 'source_id': source['id'], 'request_ref': request['user_ref'],
            'publication': {key: publication[key] for key in ('id', 'version')}, 'connection_revision': revision,
            'subject': subject, 'target_period': target_period, 'time_column': time_column, 'value_column': value_column,
            'start_inclusive': first + '-01', 'end_exclusive': f'{end//12:04}-{end%12+1:02}-01',
            'span_periods': span, 'observed_periods': sorted(observed), 'missing_periods': missing}


def forecast_history_title(result, window):
    title = result['title']
    prefix = re.match(r'^近\s*[一二两三四五六七八九十0-9]+\s*个?月(?:份)?', title)
    if not prefix or not (proof := forecast_history(result, window)):
        return title
    if proof.get('available_start') and (observed := proof['observed_periods']):
        return f'{observed[0]} 至 {observed[-1]} 的{title[prefix.end():].lstrip()}'
    return f'近{len(proof["requested_periods"])}个月{title[prefix.end():]}'


def _observed_count(result, contract, periods):
    field = contract.get('count_column')
    if (not field or not periods or len(periods) != len(set(periods)) or contract['query_window']['state'] != 'checked'
            or not set(periods) <= set(contract['requested_months']) or result.get('status') != 'succeeded'
            or set(result.get('columns') or []) != {contract['month_column'], field}
            or (result.get('checks') or {}).get('value_warnings')):
        return {}
    validation = result.get('validation') or {}
    if validation.get('state') == 'conflict':
        return {}
    def scope_checks(constraint):
        return [check for check in validation.get('checks', []) if check.get('constraint') == constraint]

    for constraint in (result.get('request_scope') or {}).get('constraints', []):
        if constraint.get('kind') == 'published_scope':
            checks = scope_checks(constraint)
            if not checks or any(check.get('state') != 'checked' for check in checks):
                return {}
    if any(check.get('state') != 'checked' for check in validation.get('checks', [])
           if (check.get('constraint') or {}).get('kind') == 'published_scope'):
        return {}
    entry = (result.get('column_unit_evidence') or {}).get(field) or {}
    unit = entry.get('unit') if entry.get('kind') in ('published_row', 'published_field', 'sql_alias', 'sql_row_count') else None
    if not unit and contract.get('counts_rows'):
        unit = '条'
    if unit in ('%', '％', '百分点', '个百分点'):
        return {}
    observations = []
    for period, row in zip(periods, result['rows']):
        value = row.get(field)
        if value is None and field in contract.get('nullable_count_columns', []):
            continue
        if type(value) not in (int, float) or not 0 <= value <= (2**53 - 1) or int(value) != value:
            return {}
        observations.append({'period': period, 'value': int(value)})
    total = sum(item['value'] for item in observations)
    if not observations or total > (2**53 - 1):
        return {}
    if not unit:
        # Zero still distinguishes an observed value from a missing month.
        # Missing units prevent delivery of a derived total, not that check.
        return {'observed_zero_months': sorted(item['period'] for item in observations if item['value'] == 0)}
    return {'observations': sorted(observations, key=lambda item: item['period']), 'observed_total': total, 'unit': unit}


def historical_display_window(result, sql, source):
    # For persisted results predating this proof, the original quote and the
    # same authorized publication are required. Relative dates must keep
    # their original runtime interpretation, so they are never reparsed here.
    lineage = result.get('result_lineage') or {}
    scope = result.get('request_scope') or {}
    request = scope.get('request') or {}
    quote = request.get('quote') or ''
    if (lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
            or lineage['request_ref'] != request.get('user_ref') or not _YEAR.search(quote)
            or (result.get('validation') or {}).get('state') == 'conflict'):
        return None
    try:
        window = bind({'user_text': quote, 'user_ref': request['user_ref']}, sql, source)
        return window if window and not window.get('kind') else None
    except ValueError:
        # A newly recognized mismatch should not make an old card unreadable.
        return None


def summarize(result, contract):
    if contract.get('kind'):
        return None
    column = contract['month_column']
    nullable_counts = contract.get('nullable_count_columns', [])
    if not {column, *nullable_counts} <= set(result.get('columns', [])):
        return None
    periods = set()
    labels = []
    placeholders = set()
    rows = result.get('rows') or []
    for row in rows:
        month = _MONTH_VALUE.fullmatch(str(row.get(column)))
        if not month:
            return None
        labels.append(month[1])
        if nullable_counts and all(row.get(field) is None for field in nullable_counts):
            placeholders.add(month[1])
        else:
            periods.add(month[1])
    requested = set(contract['requested_months'])
    out = {**contract, 'returned_months': sorted(periods), 'coverage': 'unknown'}
    if periods:
        out['returned_range'] = {'from': min(periods), 'to': max(periods)}
    notes = []
    if placeholders - periods:
        out['calendar_unobserved_months'] = sorted(placeholders - periods)
        notes.append('这些月份没有可汇总的数值，保留为空。' if contract.get('observed_sum')
                     else '月历占位行未匹配事实，计数保留为空。')
    if contract['query_window']['state'] == 'unknown':
        notes.append('尚无法证明 SQL 的日期窗口覆盖原问所要月份；已返回月份只代表这次查询。')
    if not result.get('truncated') and len(rows) == result.get('row_count'):
        out.update(_observed_count(result, contract, labels))
        unreturned = requested - periods
        current = contract['current_month']
        out.update(unreturned_months=sorted(unreturned),
                   elapsed_unreturned_months=sorted(m for m in unreturned if m < current),
                   current_month_unclosed=current if current in requested else None,
                   future_months=sorted(m for m in requested if m > current))
        past = out['elapsed_unreturned_months']
        if past:
            notes.append('未返回事实的已过月份为' + '、'.join(past) + '，来源覆盖未知。')
        if out['current_month_unclosed']:
            notes.append(current + ('尚未结月，当前查询未返回记录。' if current in unreturned else '尚未结月，返回数据仅供截至当前查询参考。'))
        if out['future_months']:
            notes.append('尚未到期的月份为' + '、'.join(out['future_months']) + '。')
        if periods - requested:
            out['outside_requested_months'] = sorted(periods - requested)
            notes.append('结果含原问年份以外的月份，请核对 SQL 范围。')
    if notes:
        out['note'] = ''.join(notes) + '按实际返回期间交付，未返回月份不补零，合计只涵盖已返回期间。'
    return out
