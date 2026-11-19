"""Compute within-month shares from a complete SQL count partition."""
import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from sqlglot import exp, parse, parse_one
from sqlglot.errors import SqlglotError

from .display_units import _physical_row_count
from .event_dimension_scope import _FIELD_LABEL, _dimension_fields, _published_pairs
from .metric_subject import COUNT_UNITS
from .period_grain import _group_projection, _projected_periods, requested_grain
from .qualification_scope import _column, _membership, _where
from .snapshot_scope import _physical_table
from .vendor.dialect import sqlglot_name


_MONTH = re.compile(r'(?:19|20)\d{2}-(?:0[1-9]|1[0-2])')
_SHARE = re.compile(r'占比|比例|份额|比重|百分比')
_LEADER = re.compile(r'哪(?:一|几)?(?:个|种|类|项)|最高|最大|最多|较[高大]|第[一1]|居首|领先')
_OTHER = re.compile(r'同比|环比|预测|另外|另查|再查|以及|同时|和全年|及全年|全年(?:合计|汇总|榜首|最高)|'
                    r'总体(?:合计|汇总|榜首|最高)|前\s*[2-9两二三四五六七八九十]|最(?:低|少|小)')
_SAFE_INTEGER = 2**53 - 1
_COUNT_LABEL = re.compile(r'^(?:本年度?|今年|当月|月度|年度|每月)?(?P<subject>[\u4e00-\u9fff]{4,40}?)'
                          r'(?:业务)?(?:的)?(?P<event>批准|受理|办结|完成)?(?:案卷|事项|记录|工单)(?:数量|数|量)$')
_REGION = re.compile(r'[\u4e00-\u9fff]{2,8}(?:省|自治区|市)$')
_REGION_CHANGE = re.compile(r'全国|跨省|省外|外省|省内外|不限地区|不区分地区|全地区')


def _requested(question):
    return (_SHARE.search(question) and _LEADER.search(question) and not _OTHER.search(question)
            and requested_grain({'user_text': question}) == 'month')


def _published_counts(source, question, dialect):
    tables = {table['name']: table for table in source.get('tables', [])}
    for term, example in _published_pairs(source):
        name = (term.get('name') or '').split(maxsplit=1)[-1]
        match = _COUNT_LABEL.fullmatch(name)
        if (not match or match['subject'] not in question or match['event'] and match['event'] not in question
                or not example.get('logical_sql')):
            continue
        definition = term.get('definition') or ''
        if not (unit := re.match(r'单位[：:]\s*([^。；;]+)[。；;]', definition)) or unit[1] not in COUNT_UNITS:
            continue
        if any(item.get('status') in ('draft', 'review', 'pending_review') for item in (term, example)):
            continue
        try:
            registered = parse_one(example['logical_sql'], read=dialect)
        except SqlglotError:
            continue
        origin = registered.args.get('from')
        if (not isinstance(registered, exp.Select) or not origin or len(registered.expressions) != 1
                or any(value for key, value in registered.args.items() if key not in {'expressions', 'from', 'where'})
                or not (table := _physical_table(origin.this, source, dialect))
                or not _physical_row_count(registered.expressions[0].unalias().unnest())):
            continue
        resolve = _column(tables[table], origin.this)
        filters = set()
        periods = []
        for atom in _where(registered):
            if (isinstance(atom, (exp.GTE, exp.LT)) and (field := resolve(atom.this)) and
                    (isinstance(value := atom.expression, exp.Literal) and value.is_string
                     or isinstance(value, exp.Cast) and isinstance(value.this, exp.Literal) and value.this.is_string)):
                day = value.this if isinstance(value, exp.Literal) else value.this.this
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                    periods.append((field, type(atom)))
                    continue
            selection = _membership(atom, resolve)
            if not selection or len(selection[1]) != 1:
                break
            field, values = selection
            value, = values
            if not isinstance(value, str) or value not in definition:
                break
            filters.add((field, value))
        else:
            # One of the registered dimensions must explicitly name this business in the request.
            if (len(periods) == 2 and periods[0][0] == periods[1][0] and {kind for _, kind in periods} == {exp.GTE, exp.LT}
                    and any(value in question and not _REGION.fullmatch(value) for _, value in filters)):
                yield table, periods[0][0], tuple(sorted(filters)), unit[1], match['subject'], match['event'] or ''


def _published_population(source, tree, question, dialect, shape):
    relation = tree.args['from'].this
    table = _physical_table(relation, source, dialect)
    fields = next(row for row in source['tables'] if row['name'] == table)
    category = tree.expressions[shape[1]].unalias().unnest().name.casefold()
    populations = {filters for bound, time, filters, _, _, _ in _published_counts(source, question, dialect)
                   if bound == table and time == shape[3].column and all(field.casefold() != category for field, _ in filters)}
    if len(populations) != 1:
        return None
    expected, = populations
    return expected, _column(fields, relation)


def _other_geography(question, filters):
    # A different explicitly named region belongs to the user's request.
    remaining = question
    for _, value in filters:
        if _REGION.fullmatch(value):
            remaining = remaining.replace(value, '')
    return re.search(r'(?<!全)[\u4e00-\u9fff]{2,8}(?:省(?!级)|自治区|市)', remaining)


def catalog_plan(source, host):
    from .monthly_sql_scope import _annual_year

    question = (host or {}).get('user_text') or ''
    publication = source.get('business_project') or {}
    request = (host or {}).get('user_ref')
    dialect = sqlglot_name(source['dialect'])
    temporal_text = re.sub(r'占比较[高大]', '占比居前', question)
    if (not request or not publication.get('id') or not publication.get('version')
            or (host or {}).get('clarifications') or dialect not in ('postgres', 'sqlite')
            or re.search(r'只(?:看|查|统计)|仅(?:看|查|统计)|排除|除外|不含|不包括|不计入', question)
            or not _requested(question) or not (annual := _annual_year(host, temporal_text))):
        return None
    candidates = set(_published_counts(source, question, dialect))
    if len(candidates) != 1:
        return None
    table, time, filters, unit, subject, event = candidates.pop()
    all_regions = bool(re.search(r'全国|不限地区|省内外|不区分地区|全地区', question))
    if not all_regions and (_REGION_CHANGE.search(question) or _other_geography(question, filters)):
        return None
    if all_regions:
        filters = tuple((field, value) for field, value in filters if not _REGION.fullmatch(value))
    schema = next(row for row in source['tables'] if row['name'] == table)
    dimensions = defaultdict(set)
    eligible = {column['name'] for column in schema['columns']} - {time, *(field for field, _ in filters)}
    for mapping in source.get('business_dictionary', []):
        if mapping.get('table') == table and mapping.get('kind') in ('field', 'term', 'enum'):
            for label in (mapping.get('name'), *(mapping.get('synonyms') or [])):
                if label and re.fullmatch(r'[\u4e00-\u9fff]{2,14}', label) and label in question:
                    dimensions[mapping.get('column')].add(label)
    pairs = list(_published_pairs(source))
    labels = {label for term, _ in pairs for label, field in _FIELD_LABEL.findall(term.get('definition') or '')
              if label in question and field in eligible}
    for label in labels:
        for _, field, name in _dimension_fields(pairs, source, label, {table:eligible}):
            dimensions[field].add(name)
    fields = set(dimensions) & eligible
    if len(fields) != 1:
        return None
    category, = fields
    label = max(dimensions[category], key=lambda value: (len(value), value))
    year, _ = annual
    time_sql = exp.column(time, quoted=True).sql(dialect=dialect)
    period = (f"TO_CHAR({time_sql}, 'YYYY-MM')" if dialect == 'postgres'
              else f"STRFTIME('%Y-%m', {time_sql})")
    col = exp.column(category, quoted=True).sql(dialect=dialect)
    relation = exp.to_table(table, quoted=True, dialect=dialect).sql(dialect=dialect)
    where = [exp.EQ(this=exp.column(field, quoted=True), expression=exp.Literal.string(value)).sql(dialect=dialect)
             for field, value in filters]
    for operator, bound in (('>=', year), ('<', year + 1)):
        value = exp.Literal.string(f'{bound:04}-01-01')
        if dialect == 'postgres':
            value = exp.cast(value, 'DATE')
        where.append(f'{time_sql} {operator} {value.sql(dialect=dialect)}')
    sql = (f'SELECT {period} AS "月份", {col} AS "{label}", COUNT(1) AS "{event}数量（{unit}）" '
           f'FROM {relation} WHERE ' + ' AND '.join(where) + ' GROUP BY 1, 2 ORDER BY 1, 2')
    if not _shape(parse_one(sql, read=dialect), source):
        return None
    scope = '授权地区' if all_regions else '、'.join(val for _, val in filters if _REGION.fullmatch(val))
    return {'schema_version': 1, 'request_ref': request, 'source_id': source['id'],
            'publication': {key:publication[key] for key in ('id', 'version')},
            'connection_revision': source.get('connection_revision', 0), 'quote': question,
            'query_request': {'source_id': source['id'], 'sql': sql, 'purpose': 'answer',
                              'title': f'{year} 年{scope}{subject}{event}数量按月及{label}统计'}}


def normalize_published_population(sql, source, host):
    """Restore explicitly published scope for a regrouping of that same approved population."""
    question = (host or {}).get('user_text') or ''
    if (not _requested(question) or _REGION_CHANGE.search(question) or (host or {}).get('clarifications')
            or not (source.get('business_project') or {}).get('version')):
        return sql
    dialect = sqlglot_name(source['dialect'])
    try:
        statements = parse(sql, read=dialect)
    except SqlglotError:
        return sql
    if (len(statements) != 1 or not (shape := _shape(tree := statements[0], source, allow_category_filter=True))
            or any(node.comments for node in tree.walk())
            or not (population := _published_population(source, tree, question, dialect, shape))):
        return sql
    expected, resolve = population
    # An explicit other geography is a new user population; the project default gives way.
    if _other_geography(question, expected):
        return sql
    actual = _where(tree)
    occupied = {resolve(col) for atom in actual for col in atom.find_all(exp.Column)}
    additions = [exp.EQ(this=exp.column(field, quoted=True), expression=exp.Literal.string(value))
                 for field, value in expected if field not in occupied]
    if not additions:
        return sql
    tree.where(*additions, copy=False)
    return tree.sql(dialect=dialect)


def _shape(tree, source, *, allow_category_filter=False):
    allowed = {'expressions', 'from', 'where', 'group', 'order'}
    if (not isinstance(tree, exp.Select) or len(tree.expressions) != 3
            or any(value for key, value in tree.args.items() if key not in allowed)
            or tree.find(exp.Subquery, exp.Join, exp.Window, exp.SetOperation)):
        return None
    dialect = source['dialect']
    relation = tree.args.get('from')
    group = tree.args.get('group')
    if (not relation or not (table := _physical_table(relation.this, source, sqlglot_name(dialect)))
            or not group or len(group.expressions) != 2
            or any(value for key, value in group.args.items() if key != 'expressions')):
        return None
    fields = {c['name'].casefold() for row in source['tables'] if row['name'] == table for c in row['columns']}
    indices = [_group_projection(node.unnest(), tree.expressions, {'table':fields}, str.casefold)
               for node in group.expressions]
    if None in indices or len(set(indices)) != 2:
        return None
    projections, _, uncertain = _projected_periods(tree.copy(), source, dialect)
    months = [i for i in indices if (period := projections[i]) and period.grain == 'month'
              and period.grouped and not period.part and not period.invalid]
    if uncertain or len(months) != 1:
        return None
    month, = months
    category, = set(indices) - {month}
    count, = set(range(3)) - set(indices)
    category_node = tree.expressions[category].unalias().unnest()
    if (not isinstance(category_node, exp.Column) or category_node.name.casefold() not in fields
            or projections[category] is not None
            or not _physical_row_count(tree.expressions[count].unalias().unnest())):
        return None
    # A query pre-filtered to some of the candidate categories cannot establish the full ranking.
    where = tree.args.get('where')
    if (where and not allow_category_filter
            and any(col.name.casefold() == category_node.name.casefold() for col in where.find_all(exp.Column))):
        return None
    return month, category, count, projections[month]


def _missing_months(tree, source, period, observed):
    from .monthly_sql_scope import _bounds

    bounds = _bounds(tree, source, exp.column(period.column), sqlglot_name(source['dialect']))
    if not bounds:
        return []
    start, stop, unknown = bounds
    if unknown or not start or not stop or start.day != 1 or stop.day != 1:
        return []
    first, last = start.year * 12 + start.month - 1, stop.year * 12 + stop.month - 1
    if not 0 < last - first <= 60:
        return []
    months = [f'{index // 12:04}-{index % 12 + 1:02}' for index in range(first, last)]
    return [month for month in months if month not in observed] if observed.keys() <= set(months) else []


def derive(result, units, sql, source):
    request = (result.get('request_scope') or {}).get('request') or {}
    question = str(request.get('quote') or '')
    lineage = result.get('result_lineage') or {}
    receipt = result.get('receipt') or {}
    contract = result.get('sql_answer_contract') or {}
    if (not _requested(question)
            or contract.get('kind') != 'sql_aggregate' or contract.get('complete') is not True
            or not request.get('user_ref') or request['user_ref'] != lineage.get('request_ref')
            or lineage.get('purpose') != 'answer' or receipt.get('read_only') is not True
            or receipt.get('source_id') != source['id'] or result.get('truncated')
            or (result.get('validation') or {}).get('state') == 'conflict'):
        return None
    tree = parse_one(sql, read=sqlglot_name(source['dialect']))
    if not (shape := _shape(tree, source)):
        return None
    month_idx, category_idx, count_idx, axis = shape
    month, category, count = (result['columns'][i] for i in (month_idx, category_idx, count_idx))
    rows = result.get('rows') or []
    unit = units.get(count, {}).get('unit')
    if unit not in COUNT_UNITS or not 1 <= len(rows) <= 500:
        return None
    groups = defaultdict(dict)
    for row in rows:
        period, label, n = row.get(month), row.get(category), row.get(count)
        if not isinstance(period, str) or not _MONTH.fullmatch(period):
            return None
        if (not isinstance(label, str) or not label.strip() or type(n) is not int
                or not 0 <= n <= _SAFE_INTEGER or label in groups[period]):
            return None
        groups[period][label] = n
    totals = {period:sum(counts.values()) for period, counts in groups.items()}
    if any(total > _SAFE_INTEGER for total in totals.values()):
        return None
    quantum = Decimal('0.01')
    shares = {}
    for period, counts in groups.items():
        total = totals[period]
        shares[period] = {label:float((Decimal(100 * n) / total).quantize(quantum, rounding=ROUND_HALF_UP))
                          if total else None for label, n in counts.items()}
    leaders = []
    for period, counts in sorted(groups.items()):
        top = max(counts.values())
        leaders.append({'period': period, 'total': totals[period], 'count': top,
                        'categories': sorted(label for label, n in counts.items() if n == top and top > 0),
                        'share': next(shares[period][label] for label, n in counts.items() if n == top)})
    return ([shares[row[month]][row[category]] for row in rows],
            {'schema_version': 1, 'result_ref': contract['result_ref'], 'unit': unit, 'month_column': month,
             'category_column': category, 'count_column': count, 'leaders': leaders,
             'unobserved': _missing_months(tree, source, axis, groups),
             'basis': 'complete_monthly_row_count_partition'})
