"""Tie monthly counts of approved cases to the underlying business event."""

from datetime import date
import re

from sqlglot import exp

from .monthly_sql_scope import _grouped_month, _has_date, _not_null_operand
from .observed_sql_scope import _column_names, _single_fact
from .request_scope import _and_atoms
from .vendor.dialect import sqlglot_name


_MONTHLY = re.compile(r'每(?:一|个)?个?月|各月|逐月|按月|分月|月度|新增.{0,12}月|月.{0,12}新增')
_APPROVED_COUNT = re.compile(r'(?:获批|批准|核准)(?:的)?(?:案卷|件数|数量|总?[量数]|多少|了?\s*几件|案件|事项)')
_APPROVED_COUNT_ALT = re.compile(r'新增(?:的)?(?:批准|获批|核准)(?!率)')
_REJECTED_COUNT = re.compile(r'(?:不予批准|未批准|不批准|驳回)(?:的)?(?:案卷|件数|数量|总?[量数]|多少|案件|事项)')
_AXIS_REQUEST = re.compile(r'(?:按|以|改用)\s*(?:实际)?(批准|获批|核准|受理|办结)(?:日期|时间|月份?|口径)')
_TIME_ROLE = {
    'approval': re.compile(r'^(?:实际)?(?:批准|获批|核准)(?:日期|时间|时点)$|^(?:approval|approved)_(?:date|time|at)$', re.I),
    'intake': re.compile(r'^受理(?:日期|时间|时点)$|^(?:accepted|received|intake)_(?:date|time|at)$', re.I),
    'completion': re.compile(r'^(?:实际)?办结(?:日期|时间|时点)$|^(?:closed|completed|finish)_(?:date|time|at)$', re.I),
}
_LABEL = {'approval': '批准日期', 'intake': '受理日期', 'completion': '办结日期'}
_DECISION = re.compile(r'是否批准|批准状态|是否获批|审批结果|审批决定|案卷结果|^(?:approved|approval_status|approval_result)$', re.I)
_AFFIRMATIVE = {'批准', '已批准', '予以批准', '同意批准', '核准', '已获批', 'approved'}
_NEGATIVE = {'未批准', '不予批准', '不批准', '未获批', '拒绝', 'rejected', 'denied'}
_GROUP_RANKING = re.compile(
    r'(?:请(?:帮我)?|帮我)?(?:统计|查询|查看)?\s*(?:本年度?|今年)\s*'
    r'(?P<business>[\u4e00-\u9fff]{2,20}?)业务(?:的)?(?:累计)?(?:的)?'
    r'(?:批准|获批|核准)(?:的)?案卷(?:中|里)[，,\s]*'
    r'(?:哪个|哪一类|哪类|哪种)(?P<dimension>[\u4e00-\u9fff]{2,14}?)的'
    r'案卷(?:数量|件数|数)(?P<direction>最多|最少)[。？！?!]?$'
)


def _approved_count(text):
    return not _REJECTED_COUNT.search(text) and (_APPROVED_COUNT.search(text) or _APPROVED_COUNT_ALT.search(text))


def _requested_role(host):
    from natural_analysis.forecasting import target_mentions

    text = host.get('user_text') or ''
    axes = {_axis_role(name) for name in _AXIS_REQUEST.findall(text)}
    if len(axes) > 1:
        return None
    if not _approved_count(text):
        # A short explicit axis change can inherit only the immediately
        # preceding approved-count question.
        recent = (host.get('recent_questions') or [])[-1:]
        if not axes or not any(_approved_count(previous) and _MONTHLY.search(previous) for previous in recent):
            return None
    if axes:
        return next(iter(axes))
    if _MONTHLY.search(text) or any(len(target) == 7 for target, _ in target_mentions(host)):
        return 'approval'
    return None


def _axis_role(name):
    return {'受理': 'intake', '办结': 'completion'}.get(name, 'approval')


def _role_fields(table, source):
    # Pre-upgrade publications already bind the count date in metric config.
    # Reuse that binding even when the field's visible label is still physical.
    published = set()
    for term in source.get('terms', []):
        config = term.get('config') or {}
        if (config.get('kind') == 'row-count-aggregation' and config.get('table') == table['name']
                and re.search(r'按(?:实际)?批准(?:日期|时间)', config.get('basis') or '')):
            published.add(config.get('time_column'))
    fields = {role: [] for role in _TIME_ROLE}
    mapped = [entry for entry in source.get('business_dictionary', [])
              if entry.get('source_id') == source.get('id') and entry.get('table') == table['name']
              and entry.get('kind') in ('time', 'field')]
    for column in table['columns']:
        if not _has_date(table, column['name']):
            continue
        declared = [(column.get('time_semantics') or {}).get('name'), column.get('label'), column.get('business_name')]
        declared.extend(entry.get('name') for entry in mapped if entry.get('column') == column['name'])
        names = [name.strip() for name in declared if name]
        matches = [role for role, pattern in _TIME_ROLE.items() if any(pattern.fullmatch(name) for name in names)]
        if not matches and column['name'] in published:
            matches = ['approval']
        if not matches:
            matches = [role for role, pattern in _TIME_ROLE.items()
                       if any(pattern.fullmatch(name) for name in _column_names(column))]
        if len(matches) == 1:
            fields[matches[0]].append(column['name'])
    return fields


def _month_scope(sql, source, host):
    wanted = _requested_role(host or {})
    if not wanted or not (fact := _single_fact(sql, source)):
        return None
    tree, table, ref, dialect = fact
    if not tree.find(exp.Count) or not (grouped := _grouped_month(tree, alias_group=True, dialect=dialect)):
        return None
    projection, axis = grouped
    if isinstance((group_key := tree.args['group'].expressions[0]), exp.Column) and (
            group_key.name == projection.alias and any(c['name'] == group_key.name for c in table['columns'])):
        return None  # SQL GROUP BY can bind the physical field before the alias.
    return tree, table, ref, dialect, wanted, projection, axis.name


def _column_is(node, name, ref):
    return (isinstance(node, exp.Column) and node.name == name and not (node.db or node.catalog)
            and node.table in ('', ref.alias_or_name))


def _approval_status(table, ref):
    choices = []
    for column in table['columns']:
        if not any(_DECISION.search(name) for name in _column_names(column)):
            continue
        values = column.get('business_values') or []
        positive = [v['value'] for v in values if str(v.get('label', '')).strip().lower() in _AFFIRMATIVE]
        if len(positive) != 1 or not any(str(v.get('label', '')).strip().lower() in _NEGATIVE for v in values):
            continue
        choices.append(exp.EQ(this=exp.column(column['name'], table=ref.alias_or_name, quoted=True),
                              expression=exp.convert(positive[0])))
    return choices[0] if len(choices) == 1 else None


def _approval_population(tree, table, ref, fields):
    where = tree.args.get('where')
    atoms = _and_atoms(where.this) if where else []
    conditions = []
    if status := _approval_status(table, ref):
        if not any(isinstance(atom, exp.EQ) and any(
                _column_is(left, status.this.name, ref) and right == status.expression
                for left, right in ((atom.this, atom.expression), (atom.expression, atom.this))) for atom in atoms):
            conditions.append(status)
    if len(fields) == 1:
        date_field = fields[0]
        for atom in atoms:
            if _column_is(_not_null_operand(atom), date_field, ref):
                return conditions
            if isinstance(atom, (exp.LT, exp.LTE, exp.GT, exp.GTE, exp.EQ, exp.Between)) and (
                    _column_is(atom.this, date_field, ref) or _column_is(atom.expression, date_field, ref)):
                return conditions
        conditions.append(exp.Not(this=exp.Is(
            this=exp.column(date_field, table=ref.alias_or_name, quoted=True), expression=exp.Null())))
    return conditions


def bind_annual_group_window(tree, table, ref, source, host):
    """Bind a confirmed annual approval grouping to the caller's calendar day."""
    from datetime import date, timedelta

    from .business_ambiguity import _column_name, _physical_select
    from .event_dimension_scope import _published_pairs
    from .current_month import date_axis
    from .published_cast_date_axis import date_axis as cast_date_axis
    from .temporal_context import temporal_context

    text = host.get('user_text') or ''
    if (not _approved_count(text) or not re.search(r'本年(?:度)?|今年', text)
            or _AXIS_REQUEST.search(text) or re.search(r'月|季度|半年|截至|截止|同比|20\d{2}|去年|上年|全年', text)):
        return False
    roles = _role_fields(table, source)
    axis = {field: False for field in roles['approval']}
    dialect = sqlglot_name(source['dialect'])
    if not axis:
        # Older publications may describe the approval clock only in the
        # registered count example. Its literal interval is an example.
        for term, example in _published_pairs(source):
            if not _approved_count(term.get('name') or '') or not re.search(
                    r'批准(?:时间|日期)|获批(?:时间|日期)', term.get('definition') or ''):
                continue
            documented = _physical_select(example.get('logical_sql') or '', source)
            if documented and documented[2] == table['name']:
                statement = documented[0]
                for column in table['columns']:
                    if date_axis(statement, source, table, dialect, published_column=column['name']):
                        axis[column['name']] = False
                if binding := cast_date_axis(statement, source, table, dialect):
                    axis[binding[0]['name']] = True
    if len(axis) != 1:
        return False
    field, = axis
    today = date.fromisoformat(temporal_context(host.get('client_time_zone') or 'UTC')['current_date'])
    where = tree.args.get('where')
    clocks = {col['name'] for col in table['columns'] if _has_date(table, col['name'])} | {field}
    other_conditions = []
    for atom in _and_atoms(where.this) if where else []:
        used_clocks = {_column_name(column, ref) for column in atom.find_all(exp.Column)} & clocks
        if not used_clocks:
            other_conditions.append(atom.copy())
            continue
        simple = isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.Between))
        if (used_clocks != {field} or atom.find(exp.Or, exp.Subquery)
                or not (simple or _column_is(_not_null_operand(atom), field, ref))):
            raise ValueError('APPROVAL_WINDOW: 本年度批准案卷按批准日期累计，其他时间条件须有用户依据。')
    day_axis = exp.column(field, table=ref.alias_or_name, quoted=True)
    if axis[field]:
        day_axis = exp.Date(this=day_axis) if dialect == 'sqlite' else exp.cast(day_axis, 'date')
    bounds = (today.replace(month=1, day=1), today + timedelta(days=1))
    for compare, bound in zip((exp.GTE, exp.LT), bounds):
        literal = exp.Literal.string(bound.isoformat())
        right = exp.cast(literal, 'date') if dialect != 'sqlite' else literal
        other_conditions.append(compare(this=day_axis.copy(), expression=right))
    tree.set('where', exp.Where(this=exp.and_(*other_conditions)))
    if conditions := _approval_population(tree, table, ref, [field]):
        tree.where(*conditions, copy=False)
    return tuple(bound.isoformat() for bound in bounds)


def confirmed_grouping_choice(source, host, choice):
    """Advertise a runnable option, then return its query only after a native selection."""
    from .business_ambiguity import _dimension_selection

    match = _GROUP_RANKING.fullmatch((host.get('user_text') or '').strip())
    dialect = sqlglot_name(source.get('dialect'))
    if (dialect not in ('sqlite', 'postgres') or not match or match['business'] != choice['requested_business']
            or match['dimension'] != choice['dimension']):
        return choice
    selected = _dimension_selection(host, choice)
    options = []
    plan = None
    for option in choice['options']:
        query = _approved_grouping_query(source, host, option, dialect)
        options.append({**option, 'approval_query_available': True} if query else option)
        if query and option == selected:
            plan = {**query, 'selected_label': option['label'], 'direction': match['direction']}
    return {**choice, 'options': options, **({'confirmed_approval_query': plan} if plan else {})}


def _approved_grouping_query(source, host, option, dialect):
    from .business_ambiguity import _case_key, _is_row_count, _physical_select
    from .event_dimension_scope import _published_pairs

    table = next(table for table in source['tables'] if table['name'] == option['table'])
    key = _case_key(table, source)
    dimension = re.search(r'“([^”]+)”', option['description'])
    if not key or not dimension or key == option['column']:
        return None
    label = dimension[1]
    business = option['label'].split('业务', 1)[0]
    direct = any(entry.get('kind') == 'table' and entry.get('source_id') == source['id']
                 and entry.get('table') == table['name'] and entry.get('name', '').removesuffix('业务') == business
                 for entry in source.get('business_dictionary', []))
    templates = {}
    for term, example in _published_pairs(source):
        if business not in term.get('name', '') or not _approved_count(term.get('name', '')):
            continue
        shape = _physical_select(example.get('logical_sql') or '', source)
        if not shape or shape[2] != table['name']:
            continue
        template, ref, _ = shape
        if (len(template.expressions) != 1 or not _is_row_count(template.expressions[0])
                or any(value and field not in ('expressions', 'from', 'from_', 'where') for field, value in template.args.items())
                or template.find(exp.Placeholder, exp.Parameter)
                or (where := template.args.get('where')) and any(
                    _column_is(column, option['column'], ref) or _column_is(column, key, ref)
                    for column in where.find_all(exp.Column))):
            return None
        # The named publication may include an approved population filter.
        # Retain it and rebind its sample dates to the actual request below.
        templates[template.sql(dialect=dialect)] = template
    if len(templates) > 1 or not (templates or direct):
        return None
    tree = next(iter(templates.values())).copy() if templates else exp.select().from_(
        exp.to_table(table['name'], dialect=dialect).transform(
            lambda node: exp.to_identifier(node.name, quoted=True) if isinstance(node, exp.Identifier) else node))
    relation = tree.find(exp.Table)
    dimension_column = exp.column(option['column'], table=relation.alias_or_name, quoted=True)
    case_column = exp.column(key, table=relation.alias_or_name, quoted=True)
    count_column = '案卷数量（件）'
    tree = tree.select(exp.alias_(dimension_column, label, quoted=True),
                       exp.alias_(exp.Count(this=exp.Distinct(expressions=[case_column])), count_column, quoted=True),
                       append=False).group_by(dimension_column).order_by(
                           exp.Ordered(this=exp.column(count_column, quoted=True), desc=True), dimension_column)
    try:
        bounds = bind_annual_group_window(tree, table, relation, source, host)
    except ValueError:
        return None
    if not bounds:
        return None
    start, end = bounds
    scope = {'start_inclusive': start, 'end_exclusive': end, 'category_column': label, 'count_column': count_column}
    return {**scope, 'query_request': {'source_id': source['id'], 'sql': tree.sql(dialect=dialect),
                                       'title': f'{start[:4]}年度已批准{business}案卷按{label}统计', 'purpose': 'answer'}}


def catalog_plan(engine, ctx, source, host):
    """Plan a fixed monthly approval count when physical date roles are unique."""
    from .db_probe import _quoted_table, distinct_column_values, has_duplicate_keys
    from .monthly_sql_scope import _annual_year
    from .retrieval import normalize
    from .vendor.dialect import sqlglot_name

    quote = host.get('user_text') or ''
    text = normalize(quote).strip()
    publication = source.get('business_project') or {}
    dialect = sqlglot_name(source.get('dialect'))
    if (not host.get('user_ref') or host.get('clarifications')
            or not publication.get('id') or not publication.get('version') or dialect not in ('sqlite', 'postgres')
            or _requested_role(host) != 'approval' or not (annual := _annual_year(host, text))):
        return None
    request = re.fullmatch(r'(?:请)?(?:帮我)?(?:统计|查询|查看|列出)?\s*(?:本年度?|今年|当年|20\d{2}\s*年)\s*'
                           r'(?P<subject>[\u4e00-\u9fff]{3,32}?)(?:业务)?的?(?:每(?:一|个)?月|逐月|按月|各月)'
                           r'(?:新增|新批)(?:的)?(?:批准|获批|核准)(?:案卷|事项|件)?(?:数量|件数|数|量)[。!?]?', text)
    if not request:
        return None
    subject = request['subject'].removesuffix('业务')
    candidates = []
    for table in source.get('tables', []):
        axes = _role_fields(table, source)
        if len(axes['approval']) != 1 or not (axes['intake'] or axes['completion']):
            continue
        keys = [col['name'] for col in table['columns'] if any(re.fullmatch(
            r'案卷(?:编号|标识)|审批单号|(?:case|application)_(?:id|no)', label, re.I) for label in _column_names(col))]
        dimensions = [col['name'] for col in table['columns'] if any(re.fullmatch(
            r'事项(?:名称|类别)?|业务(?:名称|类型)|(?:service|application)_(?:type|name)', label, re.I)
            for label in _column_names(col))]
        if len(keys) != 1 or len(dimensions) != 1:
            continue
        candidates.append((table, axes['approval'][0], keys[0], dimensions[0]))
    if len(candidates) != 1:
        return None
    table, axis, key, category = candidates[0]
    values = distinct_column_values(engine, ctx, source, table['name'], category)
    if not values or len(values) >= 51:
        return None
    exact = [value for value in values if normalize(value).removesuffix('业务') == subject]
    excluded = tuple(prefix + subject for prefix in ('非', '非属', '不属', '不属于', '取消'))
    candidates = exact or [value for value in values if normalize(value).endswith(subject)
                           and not normalize(value).startswith(excluded)]
    if len(candidates) != 1 or has_duplicate_keys(engine, ctx, source, table['name'], [key], require_nonnull=True) is not False:
        return None
    value = candidates[0]
    year = annual[0]
    field = exp.column(axis, quoted=True).sql(dialect=dialect)
    month = f"TO_CHAR({field}, 'YYYY-MM')" if dialect == 'postgres' else f"STRFTIME('%Y-%m', {field})"
    literal = lambda day: ('DATE ' if dialect == 'postgres' else '') + f"'{day.isoformat()}'"
    predicate = exp.EQ(this=exp.column(category, quoted=True), expression=exp.Literal.string(value)).sql(dialect=dialect)
    sql = (f'SELECT {month} AS "月份", COUNT(1) AS "新增批准件数（件）" FROM {_quoted_table(table["name"], dialect)} '
           f'WHERE {field} >= {literal(date(year, 1, 1))} AND {field} < {literal(date(year + 1, 1, 1))} '
           f'AND {predicate} GROUP BY 1 ORDER BY 1')
    sql = normalize_approval_population(sql, source, host)
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': quote, 'source_id': source['id'],
            'connection_revision': source.get('connection_revision', 0),
            'publication': {name: publication[name] for name in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'sql': sql, 'purpose': 'answer',
                              'title': f'{year}年{value}按批准日期统计'}}


def normalize_approval_population(sql, source, host):
    scope = _month_scope(sql, source, host)
    if not scope:
        return sql
    tree, table, ref, dialect, wanted, _, axis = scope
    roles = _role_fields(table, source)
    if axis not in roles[wanted] or any(node.comments for node in tree.walk()):
        return sql
    if conditions := _approval_population(tree, table, ref, roles['approval']):
        tree.where(*conditions, copy=False)
        return tree.sql(dialect=dialect)
    return sql


def approval_date_evidence(sql, source, host):
    scope = _month_scope(sql, source, host)
    if not scope:
        return None
    tree, table, ref, dialect, wanted, projection, axis = scope
    roles = _role_fields(table, source)
    actual = next((role for role, fields in roles.items() if axis in fields), None)
    if not actual and not roles[wanted]:
        return None
    if actual != wanted:
        fields = '、'.join(roles[wanted])
        target = f'已登记的{_LABEL[wanted]}（{fields}）' if fields else f'能证明{_LABEL[wanted]}的授权字段'
        raise ValueError(f'DATE_ROLE_MISMATCH: 请使用{target}归月并筛选期间，保留原业务范围及批准案卷。'
                         f'当前使用{_LABEL.get(actual, "其他日期")}（{axis}）。')
    where = tree.args.get('where')
    if where and not _AXIS_REQUEST.search((host or {}).get('user_text') or ''):
        other_dates = {field for role, fields in roles.items() if role != wanted for field in fields}
        for bound in where.find_all(exp.LT, exp.LTE, exp.GT, exp.GTE, exp.Between):
            if any(column.name in other_dates for column in bound.find_all(exp.Column)):
                raise ValueError(f'DATE_ROLE_MISMATCH: 期间过滤也应使用{_LABEL[wanted]}（{axis}），否则会漏算跨月案卷。')
    if conditions := _approval_population(tree, table, ref, roles['approval']):
        predicate = exp.and_(*conditions).sql(dialect=dialect)
        raise ValueError(f'DATE_ROLE_MISMATCH: {_LABEL[wanted]}归月只统计已批准的案卷，'
                         f'请保留授权字段或发布状态的条件 {predicate}。')
    return {'schema_version': 1, 'state': 'checked', 'role': wanted, 'field': axis, 'meaning': _LABEL[wanted],
            'month_column': projection.alias_or_name, 'request_ref': (host or {}).get('user_ref'), 'source_id': source['id']}
