"""Keep explicit business populations when conventional SQL omits a condition."""

from datetime import date
import re

from sqlglot import exp, parse, errors

from .db_probe import _quoted_table, distinct_column_values, has_duplicate_keys
from .monthly_sql_scope import (
    _annual_request, _bounds, _has_date, _month_field, _source_table, monthly_sum_projection,
    without_redundant_month_limit,
)
from .retrieval import normalize
from .temporal_scope import confirmed_scope, conflicting_months, metric_month_request, month_request, month_scope
from .vendor.dialect import sqlglot_name


_MONTH = re.compile(r'按月|逐月|每月|各月|月度')
_PERIOD = re.compile(r'(?:19|20)\d{2}\s*年(?:度)?(?:\s*\d{1,2}\s*月(?:份)?'
                     r'(?:\s*(?:至|到|-)\s*(?:(?:19|20)\d{2}\s*年)?\d{1,2}\s*月(?:份)?)?)?(?:全年)?')
_ACTION = re.compile(r'^(?:请|帮我|给我|只|仅|把|将|统计|查询|查|看|展示|列出|汇总|分析|\s)+')
_CATEGORY = re.compile(r'平台|渠道|系统|类别|分类|类型|事项|专题|'
                       r'\b(?:platform|channel|system|category|type|topic)\b')
_BARE_LABEL = re.compile(r'[\w·・.（）()\- ]{2,40}')
_ASSESSMENT = re.compile(r'合格率|通过率')
_ANNUAL_ASSESSMENT = re.compile(
    r'(?:19|20)\d{2}\s*年(?:度)?\s*(?:的)?\s*(?P<subject>[\u4e00-\u9fff]{2,18}?)'
    r'(?:合格率|通过率)\s*(?:(?:是|为)?多少)?'
)
_UNASSESSED = frozenset(('待判定', '未判定', '未检', '待检', '撤回', '取消', '作废', '待审核', '未完成', ''))
_OUTCOMES = {'合格': '不合格', '通过': '不通过'}


def _single_fact(sql, source, *, allow_window=False, allow_projection_cte=False):
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return None
    try:
        statements = parse(sql, read=dialect)
    except errors.SqlglotError:
        return None
    if len(statements) != 1 or not isinstance(tree := statements[0], exp.Select):
        return None
    if allow_projection_cte and tree.args.get('with'):
        if (tree := _inline_projection_cte(tree)) is None:
            return None
    relations = list(tree.find_all(exp.Table))
    if (len(relations) != 1 or tree.args.get('joins') or tree.args.get('with')
            or tree.find(exp.Subquery) or not allow_window and tree.find(exp.Window)
            or not (table := _source_table(relations[0], source))):
        return None
    return tree, table, relations[0], dialect


def _inline_projection_cte(tree):
    """Follow a single chain of projections to the fact and its original filters."""
    wrapper = tree.args['with']
    if (not 1 <= len(wrapper.expressions) <= 2 or wrapper.args.get('recursive')
            or len({cte.alias for cte in wrapper.expressions}) != len(wrapper.expressions)
            or any(value for key, value in tree.args.items() if key not in {'expressions', 'from', 'with'})
            or any(node.comments for node in tree.walk())):
        return None
    for cte in reversed(wrapper.expressions):
        if not _substitute_projection_cte(tree, cte):
            return None
    tree.set('with', None)
    return tree


def _substitute_projection_cte(tree, cte):
    relation = tree.args.get('from')
    if (tree.args.get('where') or not relation or not isinstance(reference := relation.this, exp.Table)
            or not isinstance(reference.this, exp.Identifier) or reference.db or reference.catalog
            or reference.name != cte.alias or cte.alias_column_names or cte.args.get('materialized')
            or not isinstance(inner := cte.this, exp.Select)
            or any(value for key, value in inner.args.items() if key not in {'expressions', 'from', 'where'})):
        return False
    base = inner.args.get('from')
    if (not base or not isinstance(base.this, exp.Table)
            or inner.find(exp.Subquery, exp.SetOperation, exp.Window)
            or any(select.find(exp.Window) for select in tree.expressions)):
        return False
    aggregates = [bool(select.find(exp.AggFunc)) for select in inner.expressions]
    rolled_up = bool(aggregates) and all(aggregates)
    if any(aggregates) and (not rolled_up or any(select.find(exp.AggFunc) for select in tree.expressions)):
        return False
    projections = {}
    for projection in inner.expressions:
        value = projection.unalias().unnest()
        if (not projection.alias_or_name or projection.alias_or_name in projections
                or not (rolled_up or isinstance(value, (exp.Column, exp.Case)))):
            return False
        projections[projection.alias_or_name] = value
    outer_columns = [column for select in tree.expressions for column in select.find_all(exp.Column)]
    if any(column.db or column.catalog or column.table not in ('', reference.alias_or_name)
           or column.name not in projections for column in outer_columns):
        return False
    for column in outer_columns:
        column.replace(projections[column.name].copy())
    tree.set('from', base.copy())
    where = inner.args.get('where')
    tree.set('where', where.copy() if where else None)
    return True


def _column_names(column):
    return [column.get(key) for key in ('name', 'label', 'business_name')
            if column.get(key)] + list(column.get('aliases') or [])


def _labels(engine, ctx, source, table, column):
    declared = column.get('business_values') or []
    if declared:
        return [(str(item['value']), str(item['label'])) for item in declared
                if 'value' in item and 'label' in item]
    values = distinct_column_values(engine, ctx, source, table['name'], column['name'], limit=51)
    return [(value, value) for value in values] if values and len(values) < 51 else []


def _qualified_name(text, column):
    for part in re.split(r'[,，。！!？?；;\n]+', normalize(text)):
        part = _MONTH.split(part, maxsplit=1)[0]
        phrase = _ACTION.sub('', _PERIOD.sub('', part)).strip(' 的')
        for name in sorted(map(normalize, _column_names(column)), key=len, reverse=True):
            if phrase.endswith(name):
                qualifier = phrase.removesuffix(name).strip(' 的')
                if _BARE_LABEL.fullmatch(qualifier):
                    return qualifier
    return None


def _monthly_dimension(engine, ctx, source, table, text, measure_name):
    columns = table['columns']
    measure = next((col for col in columns if col['name'] == measure_name), None)
    if not measure or not (qualifier := _qualified_name(text, measure)):
        return None
    candidates = [col for col in columns if col.get('business_values')
                  or _CATEGORY.search(' '.join(_column_names(col)).replace('_', ' '))]
    # Bound discovery on the fact table selected by the statement.
    if len(candidates) > 6:
        return None
    exact, stems = set(), set()
    for column in candidates:
        for value, label in _labels(engine, ctx, source, table, column):
            if normalize(label) == qualifier:
                exact.add((column['name'], value))
            elif normalize(re.split(r'[·・（(]', label, maxsplit=1)[0]).strip() == qualifier:
                stems.add((column['name'], value))
    choices = exact or stems
    return next(iter(choices)) if len(choices) == 1 else None


def _observed_monthly_grain(engine, ctx, source, table, axis, category):
    name = table['name']
    key = [axis, category]
    duplicates = has_duplicate_keys(engine, ctx, source, name, key, require_nonnull=True)
    if duplicates is not True:
        return duplicates is False
    channels = [col['name'] for col in table['columns'] if col['name'] not in key
                and any(re.search(r'渠道$|(?:^|_)channel(?:_name|_id)?$', label, re.I) for label in _column_names(col))]
    if len(channels) != 1:
        return False
    channel, = channels
    values = distinct_column_values(engine, ctx, source, name, channel)
    # Rows with all-channel aggregates or unknown channels cannot be added
    # alongside individual channel observations.
    if not values or not 2 <= len(values) < 51 or any(not value.strip() or re.search(
            r'汇总|合计|全部|所有|全渠道|总量|总计|未知|未分类|^(?:all|total|overall|unknown|summary)$', value.strip(), re.I)
            for value in values):
        return False
    return has_duplicate_keys(engine, ctx, source, name, [*key, channel], require_nonnull=True) is False


def monthly_observation_plan(engine, ctx, source, host, metrics):
    """Offer a period choice only for a uniquely supported observed count series."""
    from .metric_search import exact_hits

    quote = host.get('user_text') or ''
    request = host.get('user_ref')
    publication = source.get('business_project') or {}
    dialect = sqlglot_name(source.get('dialect'))
    selected = confirmed_scope(host)
    if (not request or not publication.get('id') or not publication.get('version')
            or dialect not in ('sqlite', 'postgres') or not conflicting_months(quote)
            or exact_hits(quote, metrics)):
        return None
    clauses = [part.strip() for part in re.split(r'[。！？!?；;，,\n]+', normalize(quote)) if part.strip()]
    if len(clauses) != 2:
        return None
    business, calendar = clauses
    if (not _MONTH.search(calendar) or not re.fullmatch(
            r'(?:请|按月|逐月|每月|各月|月度|统计|查看|看|查询|展示|汇总|分析|的|变化|走势|趋势|全年)*',
            _PERIOD.sub('', calendar).replace(' ', ''))):
        return None
    windows = [month_scope(clause, answer=False) for clause in clauses]
    if any(not window or len(window) != 12 or min(window)[-2:] != '01' for window in windows):
        return None
    if selected is not None and selected not in windows:
        return None
    if re.search(r'累计|累积|日均|月均|平均|峰值|去重|排除|不含|预测', business):
        return None

    candidates = []
    for table in source.get('tables', []):
        columns = table.get('columns') or []
        dates = [col['name'] for col in columns if (col.get('type') or '').upper() == 'DATE'
                 and ((col.get('time_semantics') or {}).get('grain') == 'month'
                      or any(re.search(r'月份?$|(?:^|_)month(?:_date|_day)?$', name, re.I)
                             for name in _column_names(col)))]
        if len(dates) != 1:
            continue
        for measure in columns:
            name = measure['name']
            if (not re.fullmatch(r'(?:BIGINT|INTEGER|INT[248]?|SMALLINT)', measure.get('type') or '', re.I)
                    or not any(re.search(r'(?:次数|调用量|访问量)(?:[（(]次[）)])?$', name)
                               for name in _column_names(measure))
                    or measure.get('aggregation', 'sum') != 'sum'
                    or not (subject := _qualified_name(business, measure))):
                continue
            candidates.append((table, dates[0], name, subject))
    if not 1 <= len(candidates) <= 4:
        return None
    matches = []
    for table, axis, value, subject in candidates:
        if dimension := _monthly_dimension(engine, ctx, source, table, business, value):
            matches.append((table, axis, value, subject, dimension))
    if len(matches) != 1:
        return None
    table, axis, value, subject, (category, binding) = matches[0]
    # A row must represent one observation for the category and physical period.
    # Unit-count labels do not by themselves justify summing overlapping snapshots.
    if not _observed_monthly_grain(engine, ctx, source, table, axis, category):
        return None
    label = subject + re.sub(r'[（(]次[）)]$', '', value)
    boundary = {'schema_version': 1, 'request_ref': request, 'quote': quote,
                'source_id': source['id'], 'connection_revision': source.get('connection_revision', 0),
                'publication': {key: publication[key] for key in ('id', 'version')}}
    if selected is None:
        if host.get('clarifications'):
            return None
        options = [{'label': f'{min(window)[:4]}年1月至12月', 'description': f'按月统计{label}。'} for window in windows]
        return {**boundary, 'question': {'id': 'monthly-observation-period', 'header': '选择统计年份',
                'question': f'问题里提到了两个统计年份，想看哪一年的{label}？', 'options': options}}

    year = int(min(selected)[:4])
    ident = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    field = ident(axis)
    month = f"TO_CHAR({field}, 'YYYY-MM')" if dialect == 'postgres' else f"STRFTIME('%Y-%m', {field})"
    start, stop = f"'{year}-01-01'", f"'{year + 1}-01-01'"
    if dialect == 'postgres':
        start, stop = 'DATE ' + start, 'DATE ' + stop
    where = exp.EQ(this=exp.column(category, quoted=True), expression=exp.Literal.string(binding)).sql(dialect=dialect)
    sql = (f'SELECT {month} AS "月份", SUM({ident(value)}) AS {ident(label + "（次）")} '
           f'FROM {_quoted_table(table["name"], dialect)} WHERE {field} >= {start} AND {field} < {stop} '
           f'AND {where} GROUP BY 1 ORDER BY 1')
    return {**boundary, 'query_request': {'source_id': source['id'], 'sql': sql, 'purpose': 'answer',
                                          'title': f'{year}年逐月{label}'}}


def _where_columns(tree, name):
    where = tree.args.get('where')
    return [column for column in where.find_all(exp.Column) if column.name == name] if where else []


def _requested_months(host):
    if months := metric_month_request(host):
        return months
    if not conflicting_months(host.get('user_text', '')) and (annual := _annual_request(host)) and annual[0] == annual[1]:
        return frozenset(f'{annual[0]}-{month:02}' for month in range(1, 13))
    return None


def _requested_dimension(tree, table, host, engine, ctx, source):
    sums = list(tree.find_all(exp.Sum))
    if (len(sums) != 1 or not isinstance(measure := sums[0].this, exp.Column)
            or not tree.args.get('group')):
        return None
    texts = [host['user_text']]
    if ((recent := host.get('recent_questions')) and month_request(host) is not None
            and conflicting_months(recent[-1]) and not conflicting_months(texts[0])):
        texts.append(recent[-1])
    for text in texts:
        if dimension := _monthly_dimension(engine, ctx, source, table, text, measure.name):
            return dimension
    return None


def _format_month(tree, source, table, months, dialect):
    """Use a month label on a proven physical calendar range."""
    if not (group := tree.args.get('group')):
        return False
    first, last = min(months), max(months)
    if month_scope(first+'至'+last) != months:
        return False
    start = date.fromisoformat(first+'-01')
    tail = date.fromisoformat(last+'-01')
    stop = date(tail.year + (tail.month == 12), tail.month % 12 + 1, 1)
    for index, projection in enumerate(tree.selects, 1):
        value = projection.unalias()
        axis = _month_field(value, dialect)
        if (axis is None and isinstance(value, exp.Anonymous) and value.name.upper() == 'DATE_FORMAT'
                and len(value.expressions) == 2 and isinstance(value.expressions[1], exp.Literal)
                and value.expressions[1].this == '%Y-%m'):
            axis = value.expressions[0]
        if not isinstance(axis, exp.Column) or not _has_date(table, axis.name):
            continue
        if (_bounds(tree, source, axis, dialect, midnight_timestamp=True) != (start, stop, False)
                or not projection.alias):
            continue
        nodes = [term for term in group.expressions if term == value]
        if not nodes and not any(
            isinstance(term, exp.Column) and term.name == projection.alias
            or isinstance(term, exp.Literal) and term.is_int and int(term.this) == index
            for term in group.expressions
        ):
            continue
        label = exp.TimeToStr(this=axis.copy(), format=exp.Literal.string('%Y-%m'))
        if label == value:
            return False
        for expr in [*nodes, *tree.find_all(exp.Ordered)]:
            if isinstance(expr, exp.Ordered):
                if expr.this == value:
                    expr.set('this', label.copy())
            else:
                expr.replace(label.copy())
        value.replace(label)
        return True
    return False


def preserve_monthly_population(sql, source, host, engine, ctx):
    """Keep the named category and use a calendar-month label for the proven window."""
    months = _requested_months(host or {})
    if not months or not (fact := _single_fact(sql, source)):
        return sql
    tree, table, _, dialect = fact
    normalized = without_redundant_month_limit(tree, source, months, dialect)
    changed = normalized is not tree
    tree = normalized
    dimension = _requested_dimension(tree, table, host, engine, ctx, source)
    if dimension and not _where_columns(tree, dimension[0]):
        column, value = dimension
        tree = tree.where(exp.EQ(this=exp.column(column, quoted=True), expression=exp.Literal.string(value)), copy=False)
        changed = True
    formatted = _format_month(tree, source, table, months, dialect)
    if observed := monthly_sum_projection(tree, source, allow_default_zero=True):
        projection, aggregate = observed
        if isinstance(projection.unalias().unnest(), exp.Coalesce):
            projection.set('this', aggregate.copy())
            changed = True
    return tree.sql(dialect=dialect) if changed or formatted else sql


def require_monthly_population(sql, source, host, engine, ctx, parameters=None):
    if not _requested_months(host or {}) or not (fact := _single_fact(sql, source)):
        return
    tree, table, _, dialect = fact
    if not (dimension := _requested_dimension(tree, table, host, engine, ctx, source)):
        return
    column, value = dimension
    from .enum_discovery import extract_verified_filters
    filters = extract_verified_filters(sql, source, parameters)
    if not any(f['table'] == table['name'] and f['column'] == column
               and f['op'] == 'in' and f['values'] == [value] for f in filters):
        literal = exp.Literal.string(value).sql(dialect=dialect)
        raise ValueError(f'BUSINESS_SCOPE_CONFLICT: 请保留原问业务对象，按 {column} = {literal} 筛选，沿用用户已选期间。')


def preserve_assessed_population(sql, source, host, engine, ctx):
    """Exclude clearly unadjudicated records from an outcome rate's denominator."""
    text = (host or {}).get('user_text') or ''
    if (not _ASSESSMENT.search(text) or re.search(r'撤回|取消|作废|待判定|待检|另按|所有记录', text)
            or not (fact := _single_fact(sql, source))):
        return sql
    tree, table, _, dialect = fact
    if not tree.find(exp.Div) or not tree.find(exp.Count):
        return sql
    conditions = set()
    where = tree.args.get('where')
    for node in tree.find_all(exp.EQ):
        if (isinstance(node.this, exp.Column) and isinstance(node.expression, exp.Literal)
                and node.expression.this in _OUTCOMES and node.find_ancestor(exp.Filter, exp.Case)
                and (where is None or node.find_ancestor(exp.Where) is not where)):
            conditions.add((node.this.name, node.expression.this))
    if len(conditions) != 1:
        return sql
    name, positive = conditions.pop()
    negative = _OUTCOMES[positive]
    column = next((col for col in table['columns'] if col['name'] == name), None)
    if not column or _where_columns(tree, name):
        return sql
    labels = _labels(engine, ctx, source, table, column)
    if not labels or any(value != label or label not in {positive, negative} | _UNASSESSED for value, label in labels):
        return sql
    predicate = exp.In(this=exp.column(name, quoted=True),
                       expressions=[exp.Literal.string(item) for item in (positive, negative)])
    return tree.where(predicate, copy=False).sql(dialect=dialect)


def _indicator_predicate(value):
    """Recognize row counts expressed as a filtered count or binary indicator."""
    if isinstance(value, exp.Filter) and isinstance(value.this, exp.Count) and isinstance(value.this.this, exp.Star):
        return value.expression.this if isinstance(value.expression, exp.Where) else None
    if (type(value) not in (exp.Count, exp.Sum) or not isinstance(case := value.this, exp.Case)
            or case.this is not None or len(case.args['ifs']) != 1):
        return None
    branch, = case.args['ifs']
    on_match, on_miss = branch.args.get('true'), case.args.get('default')
    if on_match != exp.Literal.number(1):
        return None
    if (type(value) is exp.Sum and on_miss != exp.Literal.number(0)
            or type(value) is exp.Count and on_miss is not None and not isinstance(on_miss, exp.Null)):
        return None
    return branch.this


def _positive_indicator(predicate, positive):
    if (isinstance(predicate, (exp.EQ, exp.Like)) and isinstance(predicate.this, exp.Column)
            and isinstance(predicate.expression, exp.Literal) and predicate.expression.is_string):
        literal = predicate.expression.this
        if (isinstance(predicate, exp.EQ) and literal == positive
                or isinstance(predicate, exp.Like) and literal == positive + '%'):
            return predicate.this
    return None


def assessed_rate_read(sql, source, host, engine, ctx):
    """Normalize a single assessment rate with a proven reporting window and enum."""
    question = (host or {}).get('user_text') or ''
    clauses = [part.strip() for part in re.split(r'[。！？!?；;，,\n]+', question) if _ASSESSMENT.search(part)]
    if (len(clauses) != 1 or not (request_ref := host.get('user_ref'))
            or re.search(r'占比|分组|按.{0,5}(?:判定|等级|区|市|项目|单位)|分别|去重|不同|撤回|取消|作废|待判定|待检|所有记录|同比|环比|趋势|\d+\s*月', clauses[0])
            or not (fact := _single_fact(sql, source, allow_window=True, allow_projection_cte=True))):
        return sql, None

    clause = clauses[0]
    years = set(re.findall(r'((?:19|20)\d{2})\s*年', clause) or re.findall(r'((?:19|20)\d{2})\s*年', question))
    if len(years) != 1:
        return sql, None
    year = int(years.pop())
    match = _ASSESSMENT.search(clause)
    positive = match[0][:-1]
    negative = _OUTCOMES[positive]
    tree, table, relation, dialect = fact
    if (any(value and key not in ('from', 'expressions', 'where', 'group', 'order') for key, value in tree.args.items())
            or tree.find(exp.Distinct)):
        return sql, None
    period = (date(year, 1, 1), date(year + 1, 1, 1), False)
    axes = [c['name'] for c in table['columns']
            if _has_date(table, c['name']) and _bounds(tree, source, exp.column(c['name'], quoted=True),
                                                      dialect, midnight_timestamp=True) == period]
    if len(axes) != 1:
        return sql, None

    group = tree.args.get('group')
    original_columns = {}
    if group:
        if len(group.expressions) != 1 or not isinstance(field := group.expressions[0], exp.Column):
            return sql, None
        if not any(p.unalias() == field for p in tree.expressions):
            return sql, None
    else:
        indicators = (_indicator_predicate(node) for p in tree.expressions
                      for node in p.find_all(exp.Filter, exp.Count, exp.Sum))
        fields = {column.name: column for predicate in indicators
                  if (column := _positive_indicator(predicate, positive)) is not None}
        if len(fields) != 1 or not tree.find(exp.Div):
            return sql, None
        field = next(iter(fields.values()))
        for projection in tree.expressions:
            value = projection.unalias().unnest()
            predicate = _indicator_predicate(value)
            if isinstance(value, exp.Literal) and value.is_int and int(value.this) == year:
                # The physical date predicate already proves this constant year.
                continue
            if isinstance(value, exp.Count) and type(value.this) is exp.Star:
                role = 'denominator'
            elif _positive_indicator(predicate, positive) == field:
                role = 'numerator'
            elif value.find(exp.Div) and _ASSESSMENT.search(projection.alias):
                role = 'rate'
            elif predicate is not None and set(predicate.find_all(exp.Column)) == {field}:
                # A model can emit several audit counts or rival denominators.
                # Keep the requested rate and its two operands for this result.
                continue
            else:
                return sql, None
            if not projection.alias:
                return sql, None
            original_columns[role] = projection.alias
        if len(tree.expressions) != 3 or set(original_columns) != {'numerator', 'denominator', 'rate'}:
            original_columns = {}
    if (field.db or field.catalog or field.table not in ('', relation.alias_or_name)
            or not (column := next((c for c in table['columns'] if c['name'] == field.name), None))):
        return sql, None
    if _where_columns(tree, field.name):
        return sql, None
    if group:
        counts = list(tree.find_all(exp.Count))
        if (not counts or any(type(count.this) is not exp.Star or count.expressions for count in counts)
                or any(isinstance(p.unalias(), exp.AggFunc) and type(p.unalias()) is not exp.Count for p in tree.expressions)):
            return sql, None
    entries = _labels(engine, ctx, source, table, column)
    if not entries or any(label not in {positive, negative} | _UNASSESSED for _, label in entries):
        return sql, None
    passed = [value for value, label in entries if label == positive]
    assessed = [value for value, label in entries if label in (positive, negative)]
    if not assessed:
        return sql, None
    if not passed:
        # An absent positive category is a real zero among observed assessed rows.
        passed = [positive] if all(value == label for value, label in entries) else []
        if not passed:
            return sql, None

    def count_of(values):
        predicate = exp.In(this=field.copy(), expressions=[exp.Literal.string(value) for value in values])
        return exp.Count(this=exp.Case(ifs=[exp.If(this=predicate, true=exp.Literal.number(1))]))

    numerator, denominator = count_of(passed), count_of(assessed)
    rate = exp.Round(this=exp.Div(this=exp.Mul(this=exp.Literal.number('100.0'), expression=numerator.copy()),
                                  expression=exp.Nullif(this=denominator.copy(), expression=exp.Literal.number(0)),
                                  typed=True),
                     decimals=exp.Literal.number(2))
    label = match[0]
    subject = re.sub(r'(?:19|20)\d{2}\s*年(?:度)?\s*', '', clause)
    if prefix := re.search(r'([\u4e00-\u9fff]{2,12})'+label+r'(?:是多少|为多少|多少|$)', subject):
        name = _ACTION.sub('', prefix[1].lstrip('的'))
        if name:
            label = name+label
    columns = {'numerator': positive+'数（条）', 'denominator': '已判定数（条）', 'rate': label+'（%）'}
    if original_columns:
        columns = {**original_columns, **{role:name for role,name in columns.items() if role not in original_columns}}
    expressions = {'numerator': numerator, 'denominator': denominator, 'rate': rate}
    tree.set('expressions', [exp.alias_(expressions[role], name, quoted=True) for role, name in columns.items()])
    tree.set('group', None)
    tree.set('order', None)
    proof = {'schema_version': 1, 'request_ref': request_ref, 'source_id': source['id'],
             'question_clause': normalize(clause), 'year': str(year), 'label': label,
             'positive_label': positive, 'columns': columns}
    return tree.sql(dialect=dialect), proof


def assessment_catalog_query(source, described, clause, host, engine, ctx):
    """Plan a separate annual rate when catalog fields identify the event and outcome."""
    from .db_probe import _quoted_table

    if not (match := _ANNUAL_ASSESSMENT.fullmatch(normalize(clause))):
        return None
    subject = match['subject']
    metrics = [*described.get('semantic_metrics', []), *described.get('metric_definitions', [])]
    for metric in metrics:
        names = [metric.get('name') or '', *(metric.get('aliases') or [])]
        if any(subject in normalize(name) and _ASSESSMENT.search(normalize(name)) for name in names):
            return None
    if subject + '合格率' in described.get('confirmed_aliases', {}) and described.get('query_modes', {}).get('legacy_metrics'):
        return None
    dialect = sqlglot_name(source['dialect'])
    if dialect not in ('postgres', 'sqlite'):
        return None
    bindings = []
    for table in source['tables']:
        dates, outcomes = set(), set()
        for column in table['columns']:
            names = set(map(normalize, _column_names(column)))
            if _has_date(table, column['name']) and names & {subject + '日期', subject + '时间'}:
                dates.add(column['name'])
            if names & {subject + '判定', subject + '结论', subject + '结果'}:
                outcomes.add(column['name'])
        if dates and outcomes:
            bindings.extend((table, day, outcome) for day in dates for outcome in outcomes)
    if len(bindings) != 1:
        return None
    (table, day, outcome), = bindings
    year = int(match[0][:4])
    field = exp.column(outcome, quoted=True).sql(dialect=dialect)
    axis = exp.column(day, quoted=True).sql(dialect=dialect)
    relation = _quoted_table(table['name'], dialect)
    sql = (f'SELECT {field}, COUNT(*) AS "记录数" FROM {relation} '
           f"WHERE {axis} >= '{year}-01-01' AND {axis} < '{year + 1}-01-01' GROUP BY {field}")
    _, proof = assessed_rate_read(sql, source, host, engine, ctx)
    if proof is None:
        return None
    return {'source_id': source['id'], 'sql': sql, 'title': str(year) + '年' + proof['label'], 'purpose': 'answer'}
