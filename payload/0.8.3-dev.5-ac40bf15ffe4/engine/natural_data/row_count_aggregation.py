"""Prove that an authorized SQL result contains disjoint, published row counts."""
from datetime import date, timedelta
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .catalog_index import fingerprint
from .retrieval import normalize
from .vendor.dialect import sqlglot_name


KIND = 'row-count-aggregation'
FIELDS = {'kind', 'id', 'source_id', 'source_revision', 'table', 'dimension',
          'time_column', 'filters', 'unit', 'basis'}
_SCOPE_ALIASES = 'scope_aliases'
_DETAIL_ALIASES = 'detail_dimension_aliases'
_GROUPING = 'published_grouping'
_SCOPE_CHANGE = re.compile(r'取消|去掉|不(?:要|再)?(?:按|限|用|仅|只|含|包括)|排除|除去|剔除|去除|'
                           r'不区分|不限|改(?:查|为|用)|还是|或|对比|比较|另(?:外|查)|'
                           r'是否|是不是|算不算|\b(?:exclude|without|instead of|versus|or)\b')
_SUBGROUP_SEPARATOR = re.compile(r'\s*[-‐‑–—]\s*')
_MONTH_RANGE = re.compile(r'(?<!\d)(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*月?\s*(?:至|到|[-–—~～])\s*'
                          r'(?:(20\d{2})\s*(?:年|[-/])\s*)?(\d{1,2})\s*月(?!\s*\d)')
_EXTRA = re.compile(r'另(?:外|查)|同时|以及|分别|合计|总计|前\s*\d|只(?:看|统计|查)|仅(?:看|统计|查)|'
                    r'同比|环比|预测|截止|截至|按(?:大类|类别|行业|地区|地市|省|单位|组织)|'
                    r'(?:19|20)\d{2}|\d{1,2}\s*(?:月|[日号])|[\u4e00-\u9fff]{2,8}(?:省|自治区|市|区县)')
_ACTION_PREFIX = re.compile(r'\s*(?:请\s*)?(?:帮我\s*)?(?:(?:统计|列出|查询|查看|给出|展示)\s*)?[，,、：:\s]*')
_GROUP_INTENT = re.compile(r'(?:大类|分组|分类)')
_COUNT_INTENT = re.compile(r'批准案卷|(?:统计|计算|汇总|查看|列出|展示).{0,90}(?:数量|件数|记录数|案卷|计数|合计)')
_PRIOR_SCOPE = re.compile(r'上(?:一)?轮|刚才|原范围|同一范围|同口径')
_SAME_SCOPE_FIELD = r'(?:时间(?:范围)?|时段|统计期间|(?:业务|统计|数据)?范围|(?:审批|业务|办理)?事项|筛选(?:条件)?|统计对象|口径)'
_REGROUP_SCOPE = (r'(?:沿用|保持|保留)(?:刚才|上(?:一)?轮|原先|原来|原有)的?\s*'
                  + _SAME_SCOPE_FIELD + r'(?:\s*[、/和与及]\s*' + _SAME_SCOPE_FIELD + r'){0,3}\s*[,，。]\s*')
_REGROUP_DELIVERY = (r'(?:\s*[,，。]\s*(?:给出|列出|展示|显示|输出)?\s*(?:各类|每类)?\s*'
                     r'(?:件数|记录数|数量|条数)(?:\s*(?:及|与|和)\s*(?:占比|比例|份额))?)?'
                     r'(?:\s*(?:[,，。]|并|再|同时|且)*\s*(?:用|以|提供)\s*'
                     r'(?:饼(?:状|形)?图|环形图|圆环图|(?:横向|竖向|纵向)?(?:柱状|柱形|条形)图|数据表|表格)'
                     r'(?:\s*(?:展示|显示|输出|呈现))?)?')
_VIEW_ONLY = re.compile(r'(?:(?:请帮我|帮我|请)\s*)?'
                        r'(?:(?:把|将)\s*(?:刚才|上轮|上一轮|上面|上述)的?(?:结果|图表|图)\s*)?'
                        r'(?:用|以|改成|改为|换成|换为|切换到)\s*'
                        r'(?:饼(?:状|形)?图|环形图|圆环图|(?:横向|竖向|纵向)?(?:柱状|柱形|条形)图|数据表|表格)'
                        r'(?:的形式)?\s*(?:展示|显示|输出|呈现|就行)?\s*[。!]?$')
_GROUP_REMAINDER = re.compile(r'(?:(?:\s|[、，,。：:！？!?；;()（）]|'
                              r'请|帮我|统计|查询|列出|查看|给出|汇总|展示|输出|的|各|每|个|[一二两三四五六七八九十]|按|'
                              r'大类|大|分类|分组|应用领域|应用|领域|批准|案卷|记录数|件数|数量|总数|累计(?:值|量|件数)?|'
                              r'(?:占比|比例|份额)(?:多少)?|及|和|与|用|以|数据表|表格|饼状图|饼图|柱状图|柱形图|条形图|图表|形式|'
                              r'沿用上一轮|沿用上轮|沿用刚才|上轮|上一轮|刚才|原范围|同一范围|同口径|分别|汇总|总计))*')
_EXPLICIT_YEAR_CUTOFF = re.compile(r'(?:截至|截止(?:到)?)\s*(20\d{2})\s*年\s*(\d{1,2})\s*月(?:份)?'
                                  r'|(20\d{2})\s*年(?:度)?\s*(?:截至|截止(?:到)?)\s*(\d{1,2})\s*月(?:份)?')
_CURRENT_YEAR_SUFFIX = re.compile(r'\s*(?:当月\s*)?(?:的\s*)?(?:本年度|本年|今年)')
_RELATIVE_YEAR_CUTOFF = re.compile(r'(?:截至|截止(?:到)?)\s*(\d{1,2})\s*月(?:份)?\s*(?:当月)?\s*(?:的)?\s*(?:本年度|本年|今年)'
                                  r'|(?:本年度|本年|今年)\s*(?:截至|截止(?:到)?)\s*(\d{1,2})\s*月(?:份)?')


def validate(spec, source):
    table = next((t for t in source.get('tables', []) if t['name'] == spec.get('table')), {})
    columns = {c['name'] for c in table.get('columns', [])}
    filters = spec.get('filters')
    if (not FIELDS <= set(spec) or set(spec) - FIELDS - {_SCOPE_ALIASES, _DETAIL_ALIASES, _GROUPING}
            or spec.get('kind') != KIND or spec.get('source_id') != source['id']
            or type(spec.get('source_revision')) is not int or spec['source_revision'] < 0
            or not isinstance(spec.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', spec['id'])
            or not isinstance(spec.get('unit'), str) or not 0 < len(spec['unit'].strip()) <= 40
            or spec['unit'] in ('%', '％') or not isinstance(spec.get('basis'), str) or not 0 < len(spec['basis'].strip()) <= 500
            or any(not isinstance(spec.get(key), str) or spec[key] not in columns for key in ('dimension', 'time_column'))
            or spec['dimension'] == spec['time_column'] or not isinstance(filters, dict) or not 0 < len(filters) <= 20):
        raise ValueError('DERIVE_COUNT_CONFIG: 逐行计数必须绑定数据源、授权表、分类与时间字段、单位及来源依据。')
    for column, values in filters.items():
        if (column not in columns or column in (spec['dimension'], spec['time_column'])
                or not isinstance(values, list) or not 0 < len(values) <= 100
                or any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in values)
                or len(values) != len(set(values))):
            raise ValueError('DERIVE_COUNT_CONFIG: 必须明确登记同一统计总体的等值筛选。')
    for key, minimum, label in ((_SCOPE_ALIASES, 4, '统计范围别名'), (_DETAIL_ALIASES, 2, '细分维度别名')):
        if key not in spec:
            continue
        aliases = spec[key]
        if (not isinstance(aliases, list) or not 1 <= len(aliases) <= 10
                or any(not isinstance(alias, str) or not minimum <= len(alias.strip()) <= 120 for alias in aliases)
                or len({normalize(alias).strip() for alias in aliases}) != len(aliases)):
            raise ValueError(f'DERIVE_COUNT_CONFIG: {label}须填写1至10个互异的完整业务名称。')
    if _GROUPING in spec:
        _validate_grouping(spec[_GROUPING])


def _validate_grouping(grouping):
    message = 'DERIVE_COUNT_CONFIG: 发布分类必须登记互不重叠的前缀、一个其余类及明确的未分类标签。'
    if (not isinstance(grouping, dict) or set(grouping) != {'prefixes', 'other', 'unclassified', 'basis', 'request_labels'}
            or not isinstance(grouping['prefixes'], list) or not 1 <= len(grouping['prefixes']) <= 12
            or not isinstance(grouping['request_labels'], list) or not 1 <= len(grouping['request_labels']) <= 8
            or any(not isinstance(name, str) or not re.fullmatch(r'[\w\u4e00-\u9fff-]{2,60}', name)
                   for name in grouping['request_labels'])
            or not isinstance(grouping['basis'], str) or not 0 < len(grouping['basis'].strip()) <= 500):
        raise ValueError(message)

    def label(value):
        return isinstance(value, str) and value == value.strip() and bool(re.fullmatch(r'[\w\u4e00-\u9fff-]{1,40}', value))

    def names(row, extra):
        if (not isinstance(row, dict) or set(row) != {'label', 'aliases', *extra} or not label(row['label'])
                or not isinstance(row['aliases'], list) or len(row['aliases']) > 8
                or any(not label(name) for name in row['aliases'])):
            raise ValueError(message)
        return [normalize(name) for name in (row['label'], *row['aliases'])]

    labels = [*names(grouping['other'], ())]
    prefixes = []
    for row in grouping['prefixes']:
        labels.extend(names(row, ('prefix',)))
        prefix = row['prefix']
        if not label(prefix) or '_' in prefix or any(prefix.casefold().startswith(p) or p.startswith(prefix.casefold()) for p in prefixes):
            raise ValueError(message)
        prefixes.append(prefix.casefold())
    if not label(grouping['unclassified']) or len(set([*labels, normalize(grouping['unclassified'])])) != len(labels) + 1:
        raise ValueError(message)


def subgroup_names(spec):
    """Names of declared subgroups: the part of a filter value after a hyphen."""
    return {part for values in spec['filters'].values() for value in values
            for part in _SUBGROUP_SEPARATOR.split(normalize(value), maxsplit=1)[1:] if len(part) >= 4}


def _registered_count_specs(source):
    for term in source.get('terms', []):
        spec = term.get('config') or {}
        if (spec.get('kind') != KIND or spec.get('source_id') != source['id']
                or term.get('source_id', source['id']) != source['id']
                or term.get('status') in ('draft', 'review')
                or spec.get('source_revision') != source.get('target_revision', 0)):
            continue
        try:
            validate(spec, source)
        except ValueError:
            continue
        yield term, spec


def _published_specs(source):
    return ((term, spec) for term, spec in _registered_count_specs(source) if spec.get(_SCOPE_ALIASES))


def _published_count_grouping(source, population, original=None):
    """Find a single classification of the same registered fact dimension."""
    if not (source.get('business_project') or {}).get('version'):
        return None
    if original is not None and (
        source.get('business_project') != original.get('business_project')
        or source.get('connection_revision') != original.get('connection_revision')
    ):
        return None
    saved = [spec for _, spec in _registered_count_specs(original)] if original is not None else None
    mappings = {}
    for _, candidate in _registered_count_specs(source):
        grouping = candidate.get(_GROUPING)
        if (grouping and (saved is None or candidate in saved)
                and all(candidate[key] == population[key] for key in ('table', 'dimension', 'time_column', 'unit'))):
            mappings[fingerprint(grouping)] = grouping
    return next(iter(mappings.values())) if len(mappings) == 1 else None


def named_scope(source, question):
    """Find one published population explicitly named in a positive request."""
    text = normalize(question)
    if not text:
        return None
    selected = []
    for term, spec in _published_specs(source):
        matches = []
        for alias in spec[_SCOPE_ALIASES]:
            name = normalize(alias).strip()
            # Chinese words may directly neighbor the name. A hyphen begins a
            # more specific named item. English names retain word boundaries.
            left = r'(?<![a-z0-9_-])' if name[0].isascii() else ''
            for match in re.finditer(left + re.escape(name) + r'(?!\s*[-‐‑–—]|[a-z0-9_])', text):
                before, after = text[:match.start()], text[match.end():]
                if (re.search(r'(?:非|未|除)\s*$', before) or _SCOPE_CHANGE.search(before + ' ' + after)
                        or re.search(r'^(?:\s|的)*[（(]|^(?:\s|的)*(?:中|之中)的', after)
                        or re.search(r'(?:和|与|及|、)\s*(?:全部|所有|全量)', before + ' ' + after)):
                    continue
                matches.append(alias)
        if not matches:
            continue
        # The request can name a declared subgroup in its surrounding words.
        # The parent population is not evidence for choosing that subgroup.
        if any(name in text for name in subgroup_names(spec)):
            continue
        selected.append((term, max(matches, key=len)))
    return selected[0] if len(selected) == 1 else None


def _atoms(node):
    if isinstance(node, (exp.Where, exp.Paren)):
        return _atoms(node.this)
    if isinstance(node, exp.And):
        return _atoms(node.this) + _atoms(node.expression)
    return [node]


def _value(node):
    return str(node.this) if isinstance(node, exp.Literal) and node.is_string else None


def _date(node):
    midnight_timestamp = False
    if isinstance(node, exp.Cast) and node.to.this in (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP):
        midnight_timestamp = node.to.this == exp.DataType.Type.TIMESTAMP
        node = node.this
    value = _value(node)
    # A timestamp literal proves the same calendar boundary only at midnight.
    # Timestamp-with-time-zone casts and nonmidnight bounds keep their precision.
    suffix = r'(?:[ T]00:00:00(?:\.0+)?)?' if midnight_timestamp else ''
    if value and re.fullmatch(r'\d{4}-\d{2}-\d{2}' + suffix, value):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _matches(tree, source, spec, dimension, *, exact_filters=False, monthly_total=False, ranking=False):
    """Return the selected columns and dates for a supported disjoint partition."""
    from .monthly_sql_scope import _month_field

    dialect = sqlglot_name(source.get('dialect'))
    allowed = {'expressions', 'from', 'from_', 'where', 'group', 'order'}
    if not isinstance(tree, exp.Select) or any(value and key not in allowed for key, value in tree.args.items()):
        return None
    relation = tree.args.get('from') or tree.args.get('from_')
    if not relation or not isinstance(relation.this, exp.Table) or not isinstance(relation.this.this, exp.Identifier):
        return None
    table = relation.this
    if any(value and key not in {'this', 'db', 'catalog', 'alias'} for key, value in table.args.items()):
        return None
    name = '.'.join(part.name for part in table.parts)
    matches = [t for t in source['tables'] if t['name'] == name or (not table.db and t['name'].endswith('.' + name))]
    if len(matches) != 1 or matches[0]['name'] != spec['table']:
        return None
    columns = {c['name'] for c in matches[0]['columns']}

    def column(node):
        if (isinstance(node, exp.Column) and node.name in columns and not (node.db or node.catalog)
                and (not node.table or node.table == table.alias_or_name)):
            return node.name
        return None

    filters, start, stop = {}, None, None
    for atom in _atoms(tree.args.get('where')):
        field = column(atom.this) if isinstance(atom, exp.Expression) else None
        if not field:
            return None
        if field == spec['time_column']:
            bound = _date(atom.expression)
            if isinstance(atom, exp.GTE) and bound and not start:
                start = bound
            elif isinstance(atom, exp.LT) and bound and not stop:
                stop = bound
            else:
                return None
        elif isinstance(atom, exp.EQ) and _value(atom.expression) is not None:
            if field in filters:
                return None
            filters[field] = {_value(atom.expression)}
        elif isinstance(atom, exp.In) and not atom.args.get('query') and atom.expressions:
            values = {_value(item) for item in atom.expressions}
            if field in filters or None in values:
                return None
            filters[field] = values
        else:
            return None
    if not (start and stop and start < stop) or any(filters.get(key) != set(values) for key, values in spec['filters'].items()):
        return None
    if exact_filters and set(filters) != set(spec['filters']):
        return None

    published_case = sqlglot.parse_one(_group_case(spec, dialect), read=dialect) if ranking and spec.get(_GROUPING) else None

    def normalize_columns(node):
        return node.unnest().transform(lambda part:
            exp.column(column(part), quoted=True) if isinstance(part, exp.Column) and column(part) else part)

    def role(node):
        node = node.unnest()
        if column(node) == spec['dimension']:
            return 'dimension'
        if isinstance(node, exp.Count) and not node.expressions and (
                type(node.this) is exp.Star and not any(node.this.args.values())
                or isinstance(node.this, exp.Literal) and node.this.is_int and node.this.this == '1'):
            return 'count'
        if isinstance(node, exp.TimeToStr) and _value(node.args.get('format')) == '%Y-%m':
            node = node.this
            if isinstance(node, exp.TsOrDsToTimestamp):
                node = node.this
            if column(node) == spec['time_column']:
                return 'month'
        if isinstance(node, (exp.TimestampTrunc, exp.DateTrunc)) and str(node.args.get('unit')).casefold() in ('month', "'month'"):
            if column(node.this) == spec['time_column']:
                return 'month'
        if exact_filters and column(_month_field(node, dialect)) == spec['time_column']:
            return 'month'
        if ranking:
            from .raw_dimension_scope import _month_window_projection, _raw_or_empty
            if (_raw_or_empty(node, column, spec['dimension'])
                    or published_case is not None and isinstance(node, exp.Case) and normalize_columns(node) == published_case):
                return 'dimension'
            if _month_window_projection(node, column, spec['time_column']):
                return 'observed_start' if isinstance(node, exp.Min) else 'observed_end'
        return None

    selected = {role(item.unalias()): item.alias_or_name for item in tree.expressions}
    projected = set(selected) - {'observed_start', 'observed_end'}
    if ranking:
        shapes = ({'dimension', 'count'},)
    elif monthly_total:
        shapes = ({'count', 'month'},)
    else:
        shapes = ({'dimension', 'count'}, {'dimension', 'count', 'month'})
    if (len(selected) != len(tree.expressions) or len(set(selected.values())) != len(selected)
            or not all(selected.values()) or projected not in shapes
            or (dimension is not None and selected['dimension'] != dimension)):
        return None
    group = tree.args.get('group')
    if not group or set(group.args) != {'expressions'}:
        return None
    def projection(item):
        if isinstance(item, exp.Literal) and item.is_int and 1 <= int(item.this) <= len(tree.expressions):
            return tree.expressions[int(item.this)-1].unalias()
        if isinstance(item, exp.Column) and not item.table and item.name not in columns:
            return next((p.unalias() for p in tree.expressions if p.alias == item.name), item)
        return item

    grouped = [projection(item) for item in group.expressions]
    group_roles = [role(item) for item in grouped]
    if len(group_roles) != len(projected) - 1 or set(group_roles) != projected - {'count'}:
        return None
    if ranking:
        category = next(item.unalias() for item in tree.expressions if item.alias_or_name == selected['dimension'])
        if normalize_columns(grouped[0]) != normalize_columns(category):
            return None
        if (order := tree.args.get('order')) and any(role(projection(item.this)) is None for item in order.expressions):
            return None
    return selected, start, stop


def monthly_definition(source, tree, scope):
    """Match a registered monthly count only with the same predicates and date axis."""
    request = scope.get('request') or {}
    window = scope.get('monthly_sql_window') or {}
    if ((window.get('query_window') or {}).get('state') != 'checked'
            or window.get('counts_rows') is not True
            or not (selected := named_scope(source, request.get('quote')))):
        return None
    term, _ = selected
    spec = term['config']
    if not (match := _matches(tree, source, spec, None, exact_filters=True, monthly_total=True)):
        return None
    columns, start, stop = match
    if (columns != {'month': window.get('month_column'), 'count': window.get('count_column')}
            or (start.isoformat(), stop.isoformat()) !=
               (window['query_window']['start_inclusive'], window['query_window']['end_exclusive'])):
        return None
    return term


def _detail_selection(source, host):
    from .monthly_sql_scope import _has_date
    from .period_grain import requested_grain

    text = normalize((host or {}).get('user_text', ''))
    if requested_grain(host) != 'month' or not (scope := named_scope(source, text)):
        return None
    term, alias = scope
    spec = term['config']
    names = [name for name in spec.get(_DETAIL_ALIASES, [])
             if re.search(r'(?:按|各|每|逐)\s*(?:个|项)?\s*' + re.escape(normalize(name)), text)]
    if not names or not (window := _MONTH_RANGE.search(text)):
        return None
    # Only complete month windows are supported. Leave extra dates, geographic
    # qualifiers and other breakdowns to the normal SQL route.
    without_window = text[:window.start()] + ' ' + text[window.end():]
    before, _, after = without_window.partition(normalize(alias))
    # Only remove singleton filters before the business name. Mentioning one
    # member of a multi-value filter can narrow the population.
    for raw in (values[0] for values in spec['filters'].values() if len(values) == 1):
        before = before.replace(normalize(raw), ' ')
    if not _ACTION_PREFIX.fullmatch(before) or _EXTRA.search(after):
        return None
    first_year, first_month, last_year, last_month = window.groups()
    y, m, ey, em = int(first_year), int(first_month), int(last_year or first_year), int(last_month)
    distance = (ey - y) * 12 + em - m
    if not (1 <= m <= 12 and 1 <= em <= 12 and 0 <= distance < 24):
        return None
    if any(item.get('call_id') and item.get('question_id') and item.get('answer')
           for item in (host or {}).get('clarifications', [])):
        return None  # The active native choice may refine the population or the period.
    table = next(t for t in source['tables'] if t['name'] == spec['table'])
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('postgres', 'sqlite') or not _has_date(table, spec['time_column']):
        return None
    stop = date(ey + int(em == 12), em % 12 + 1, 1)
    return spec, max(names, key=len), date(y, m, 1), stop, dialect


def _population_sql(spec, start, stop, dialect):
    quote = lambda value: exp.to_identifier(value, quoted=True).sql(dialect=dialect)
    text = lambda value: exp.Literal.string(value).sql(dialect=dialect)
    table = '.'.join(quote(part) for part in spec['table'].split('.'))
    field = quote(spec['time_column'])
    day = lambda value: ('DATE ' if dialect == 'postgres' else '') + text(value.isoformat())
    filters = [f'{field} >= {day(start)}'] if start else []
    filters.append(f'{field} < {day(stop)}')
    for column, values in spec['filters'].items():
        if len(values) == 1:
            filters.append(f'{quote(column)} = {text(values[0])}')
        else:
            filters.append(f"{quote(column)} IN ({', '.join(map(text, values))})")
    return f'FROM {table} WHERE ' + ' AND '.join(filters)


def _detail_sql(spec, label, start, stop, dialect):
    quote = lambda value: exp.to_identifier(value, quoted=True).sql(dialect=dialect)
    field, dimension = quote(spec['time_column']), quote(spec['dimension'])
    month = f"TO_CHAR({field}, 'YYYY-MM')" if dialect == 'postgres' else f"STRFTIME('%Y-%m', {field})"
    return (f'SELECT {month} AS {quote("月份")}, {dimension} AS {quote(label)}, '
            f'COUNT(1) AS {quote("数量（" + spec["unit"] + "）")} '
            + _population_sql(spec, start, stop, dialect) + ' GROUP BY 1, 2 ORDER BY 1, 2')


def detail_query(source, host):
    """A copyable tool call for one explicitly selected published scope."""
    if not (selection := _detail_selection(source, host)):
        return None
    spec, label, start, stop, _ = selection
    return {'kind': 'published_detail_count', 'basis': spec['basis'],
            'query_request': {'source_id': source['id'], 'sql': _detail_sql(*selection),
                              'title': f'{start:%Y-%m}至{stop - timedelta(days=1):%Y-%m}按月、按{label}统计',
                              'purpose': 'answer'},
            'instruction': ('可将 query_request 直接传给 data_sql_query。按登记字段的原始细值与事实月份分组，'
                            '保留全部已观测值。直接读取事实月份，不生成月历或使用大类映射。')}


def require_detail_query(sql, source, host, *, purpose='answer'):
    if not (selection := _detail_selection(source, host)):
        return
    spec, label, start, stop, dialect = selection
    try:
        from .monthly_sql_scope import _WindowPostgres
        tree = sqlglot.parse_one(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    except SqlglotError:
        return  # The ordinary SQL syntax check supplies the parse error.
    if purpose == 'explore' and not tree.find(exp.GenerateSeries, exp.ExplodingGenerateSeries):
        return  # Genuine read-only checks of field values are still available.
    match = _matches(tree, source, spec, None, exact_filters=True)
    if match and set(match[0]) == {'month', 'dimension', 'count'} and match[1:] == (start, stop):
        if (purpose == 'answer' and host.get('user_ref')
                and match[0]['count'].endswith(('（'+spec['unit']+'）', '('+spec['unit']+')', '_'+spec['unit']))):
            return {**_proof_source(source, host, start, stop), 'month_column': match[0]['month'],
                    'category_column': match[0]['dimension'], 'count_column': match[0]['count'], 'unit': spec['unit']}
        return None
    raise ValueError('DETAIL_GRAIN_MISMATCH: 本查询模板覆盖的原问明确按月份与「' + label + '」原始细值计数。'
                     '按已发布范围直接执行下列 SQL，或用等价分组；无需生成月历、用 CASE 合并分类或再问口径。'
                     '请以 purpose=answer 执行。SQL：' + _detail_sql(*selection))


def _group_names(text, grouping, *, named_population=False):
    requested = {normalize(name): row['label'] for row in [*grouping['prefixes'], grouping['other']]
                 for name in (row['label'], *row['aliases'])}
    token = r'(?:' + '|'.join(re.escape(name) for name in sorted(requested, key=len, reverse=True)) + r')'
    listing = '(' + token + r'(?:\s*[、,，]\s*' + token + r'){' + str(len(grouping['prefixes'])) + '})'
    patterns = [r'(?:按|依)\s*' + listing]
    # A named business population and a grouping label can anchor an
    # enumerated subject such as "食品、日化、其他三大类各有多少件".
    if named_population:
        patterns.append(listing + r'(?=\s*(?:[一二两三四五六七八九十]+)?\s*(?:大类|类别|分类|分组))')
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            named = [requested[name] for name in re.split(r'\s*[、,，]\s*', match[1])]
            if len(named) == len(set(named)):
                return match.span()
    return None


def _group_window(text, host):
    from .temporal_context import temporal_context

    suffix = None
    if match := _MONTH_RANGE.search(text):
        y, m, ey, em = match.groups()
        year, first, end_year, last = int(y), int(m), int(ey or y), int(em)
    elif match := _EXPLICIT_YEAR_CUTOFF.search(text):
        a, b, c, d = match.groups()
        year = end_year = int(a or c)
        first, last = 1, int(b or d)
        suffix = _CURRENT_YEAR_SUFFIX.match(text, match.end())
        if suffix and year != date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date']).year:
            return None
    elif match := _RELATIVE_YEAR_CUTOFF.search(text):
        year = end_year = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date']).year
        first, last = 1, int(match[1] or match[2])
    else:
        return None
    if not 1 <= first <= 12 or not 1 <= last <= 12 or not 0 <= (end_year - year) * 12 + last - first < 24:
        return None
    span = (match.start(), suffix.end() if suffix else match.end())
    return span, date(year, first, 1), date(end_year + int(last == 12), last % 12 + 1, 1)


def _previous_group_input(engine, ctx, source, *, after_view=False, prior_offset=-1):
    """Read the immediately preceding human request and its delivered count."""
    host = ctx.host_evidence or {}
    recent = host.get('recent_questions') or []
    if not ctx.session_id or not recent or not hasattr(engine, 'artifacts'):
        return None
    # A pure presentation request carries no new population or period. An
    # intervening formal result still prevents inheriting the older query.
    offset = -2 if after_view and len(recent) >= 2 and _VIEW_ONLY.fullmatch(normalize(recent[-1]).strip()) else prior_offset
    if len(recent) < -offset:
        return None
    previous = recent[offset]
    try:
        if getattr(engine, 'policy', None):
            engine.policy.require_history(ctx.project)
        _, tasks = engine.artifacts.head(ctx.project, 'session:' + ctx.session_id, 'work.tasks')
    except PermissionError:
        return None
    delivered = [t for t in (tasks or {}).get('payload', {}).get('tasks', [])
                 if t.get('capability_id') == 'data' and t.get('state') == 'delivered' and t.get('result_refs')]
    if not delivered:
        return None
    current_ref = host.get('user_ref')
    recent_requests = host.get('recent_requests') or []
    previous_ref = recent_requests[offset].get('user_ref') if len(recent_requests) >= -offset else None
    result = None
    for task in sorted(delivered, key=lambda item: item['revision'], reverse=True):
        # Tools in the current request may already have delivered a probe or
        # another independent result. They cannot replace the preceding human.
        origin = ((task.get('request_scope') or {}).get('request') or {}).get('user_ref')
        if current_ref and origin == current_ref:
            continue
        if len(task['result_refs']) != 1 or not task['result_refs'][0].startswith('data:'):
            return None
        ref = task['result_refs'][0].removeprefix('data:')
        try:
            candidate = engine.raw(ctx.project, ref)
        except PermissionError:
            return None
        owner = (candidate.get('result_lineage') or {}).get('request_ref')
        if current_ref and owner == current_ref:
            continue
        if previous_ref and owner != previous_ref:
            return None  # Never hunt into an earlier human request.
        request = (candidate.get('request_scope') or {}).get('request') or {}
        if request.get('quote') != previous:
            return None
        if (candidate.get('result_lineage') or {}).get('purpose') == 'explore':
            continue  # A probe by that same human is not the delivered answer.
        result = candidate
        break
    if result is None:
        return None
    lineage, scope = result.get('result_lineage') or {}, result.get('request_scope') or {}
    request = scope.get('request') or {}
    if (result.get('status') != 'succeeded' or result.get('truncated') or not result.get('rows')
            or result.get('row_count') != len(result['rows']) or not (result.get('receipt') or {}).get('read_only')
            or lineage.get('session_id') != ctx.session_id or lineage.get('purpose') != 'answer'
            or lineage.get('source_id') != source['id'] or result.get('source_id') != source['id']
            or result.get('business_project') != source.get('business_project')
            or lineage.get('config_fingerprint') != fingerprint(source)
            or request.get('quote') != previous or not request.get('user_ref')
            or lineage.get('request_ref') != request['user_ref'] or lineage.get('request_ref') == host.get('user_ref')
            or (result.get('validation') or {}).get('state') == 'conflict'):
        return None
    from .read_limits import execution_basis
    sql = result.get('sql') or ''
    if basis := result.get('execution_basis'):
        policy_sql = basis.get('policy_sql')
        if not policy_sql or execution_basis(policy_sql, sql) != basis:
            return None
        sql = policy_sql
    return (ref, result, sql, previous) if sql else None


def _previous_detail(engine, ctx, source, start, stop):
    """Verify the previously delivered, explicitly named monthly detail."""
    host = ctx.host_evidence or {}
    if not (prior := _previous_group_input(engine, ctx, source)):
        return None
    ref, result, sql, previous = prior
    selection = _detail_selection(source, {**host, 'user_text': previous, 'clarifications': []})
    if not selection or selection[2:4] != (start, stop):
        return None
    spec, _, _, _, dialect = selection
    if _GROUPING not in spec:
        return None
    published = [f for f in (result.get('validation') or {}).get('checks', [])
                 if (f.get('constraint') or {}).get('scope_id') == spec['id']]
    if not published or any(f.get('state') != 'checked' for f in published):
        return None
    try:
        match = _matches(sqlglot.parse_one(sql, read=dialect), source, spec, None, exact_filters=True)
    except SqlglotError:
        return None
    if not match or set(match[0]) != {'month', 'dimension', 'count'} or match[1:] != (start, stop):
        return None
    return spec, dialect, ref


def _repeated_grouping_clause(text, previous):
    prefix = normalize(previous).strip().rstrip('。!?').rstrip()
    if not prefix or not text.startswith(prefix):
        return None
    # A separator is required so a changed word in the original question
    # cannot be treated as an appended grouping instruction.
    tail = re.fullmatch(r'\s*[,。!?;:]\s*((?:按|按照|依照).+)', text[len(prefix):])
    return tail[1] if tail else None


def _grouping_clause(text, grouping, *, repeated=False):
    action = r'(?:分|分组|统计|汇总|归类)?' if repeated else r'(?:统计|汇总|归类|分组)'
    labels = '|'.join(re.escape(normalize(label)) for label in grouping['request_labels'])
    plain = (r'(?:按|按照|依照)\s*(?:' + labels + r')\s*的?\s*(?:大类|分类|分组)\s*(?:重新)?\s*' + action)
    # A follow-up may explicitly keep the previous predicates and request a
    # table or chart. The caller still verifies the preceding result and its SQL.
    if not repeated:
        plain = (r'(?:' + _REGROUP_SCOPE + r')?(?:改|重新)?' + plain + _REGROUP_DELIVERY)
    return re.fullmatch(r'\s*(?:请\s*)?(?:帮我\s*)?' + plain + r'\s*[。!?]?\s*', text)


def _previous_regrouping(engine, ctx, source, text, *, repeated=False, prior=None, current_month=None):
    """Use a published grouping only when it covers the same prior population."""
    candidates = []
    for _, spec in _registered_count_specs(source):
        grouping = _published_count_grouping(source, spec)
        if grouping and _grouping_clause(text, grouping, repeated=repeated) and (dialect := _group_dialect(source, spec)):
            candidates.append(({**spec, _GROUPING: grouping}, dialect))
    if not candidates:
        return None
    prior = prior or _previous_group_input(engine, ctx, source)
    if not prior:
        return None
    ref, result, sql, previous = prior
    window = _group_window(normalize(previous), ctx.host_evidence) if repeated else None
    matches = []
    for spec, dialect in candidates:
        try:
            match = _matches(sqlglot.parse_one(sql, read=dialect), source, spec, None,
                             exact_filters=True, ranking=repeated)
        except SqlglotError:
            continue
        if not match or set(match[0].values()) != set(result.get('columns', [])):
            continue
        if current_month:
            start, stop = (day.isoformat() for day in match[1:])
            if start != current_month['starts_inclusive'][0] or not (
                current_month['calendar_date'] <= stop <= current_month['end_exclusive']
            ):
                continue
        if repeated:
            if window and match[1:] != window[1:]:
                continue
            if month := (result.get('request_scope') or {}).get('current_month'):
                start, stop = (day.isoformat() for day in match[1:])
                if start not in month['starts_inclusive'] or not month['calendar_date'] <= stop <= month['end_exclusive']:
                    continue
        matches.append((spec, dialect, ref, *match[1:]))
    return matches[0] if len(matches) == 1 else None


def _group_case(spec, dialect):
    grouping = spec[_GROUPING]
    text = lambda value: exp.Literal.string(value).sql(dialect=dialect)
    column = exp.to_identifier(spec['dimension'], quoted=True).sql(dialect=dialect)
    cases = [f'WHEN {column} IS NULL THEN {text(grouping["unclassified"])}']
    cases += [f'WHEN {column} LIKE {text(row["prefix"] + "%")} THEN {text(row["label"])}' for row in grouping['prefixes']]
    cases += [f'ELSE {text(grouping["other"]["label"])}']
    return f'CASE {" ".join(cases)} END'


def _group_sql(spec, start, stop, dialect):
    quote = lambda value: exp.to_identifier(value, quoted=True).sql(dialect=dialect)
    return (f'SELECT {_group_case(spec, dialect)} AS {quote("大类")}, '
            f'COUNT(1) AS {quote("数量（" + spec["unit"] + "）")} '
            + _population_sql(spec, start, stop, dialect) + ' GROUP BY 1 ORDER BY 2 DESC, 1')


def _proof_source(source, host, start, stop):
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source['id'],
            'source_revision': source.get('target_revision', 0),
            'publication': {k: (source.get('business_project') or {}).get(k) for k in ('id', 'version')},
            'start_inclusive': start.isoformat(), 'end_exclusive': stop.isoformat()}


def _group_dialect(source, spec):
    from .monthly_sql_scope import _has_date

    dialect = sqlglot_name(source.get('dialect'))
    table = next(t for t in source['tables'] if t['name'] == spec['table'])
    return dialect if dialect in ('postgres', 'sqlite') and _has_date(table, spec['time_column']) else None


def _matching_group_request(text, spec, window_span, alias=''):
    grouping = spec.get(_GROUPING)
    if not grouping or not (_GROUP_INTENT.search(text) or any(normalize(label) in text for label in grouping['request_labels'])):
        return False
    groups = _group_names(text, grouping, named_population=bool(alias))
    count = re.compile(r'各(?:自)?(?:有)?多少\s*' + re.escape(normalize(spec['unit'])))
    if not groups or not (_COUNT_INTENT.search(text) or count.search(text)):
        return False
    remaining = text
    for a, b in sorted((window_span, groups), reverse=True):
        remaining = remaining[:a] + ' ' + remaining[b:]
    remaining = count.sub(' ', remaining, count=1)
    if alias:
        remaining = remaining.replace(normalize(alias), ' ', 1)
        for values in spec['filters'].values():
            if len(values) == 1:
                remaining = remaining.replace(normalize(values[0]), ' ', 1)
    for label in sorted(grouping['request_labels'], key=len, reverse=True):
        remaining = remaining.replace(normalize(label), ' ')
    return bool(_GROUP_REMAINDER.fullmatch(remaining))


def _listed_grouping(engine, ctx, source, text, window_span):
    # A named classification plus its complete set of labels can select one
    # published population. A generic label list or inherited scope cannot.
    if _PRIOR_SCOPE.search(text):
        return None

    def matches(candidate):
        for _, spec in _published_specs(candidate):
            grouping = spec.get(_GROUPING)
            if (grouping and any(normalize(label) in text for label in grouping['request_labels'])
                    and _matching_group_request(text, spec, window_span)
                    and (dialect := _group_dialect(candidate, spec))):
                yield candidate['id'], spec, dialect

    if not any(matches(source)):
        return None
    recent = (ctx.host_evidence or {}).get('recent_questions') or []
    if recent and named_scope(source, recent[-1]):
        return None  # A preceding named population needs the verified detail result checked above.
    candidates = [match for item in engine.catalog(ctx.project)['sources'] for match in matches(item)]
    if len(candidates) == 1 and candidates[0][0] == source['id']:
        _, spec, dialect = candidates[0]
        return spec, dialect
    return None


def _same_published_grouping_sql(actual, expected):
    if actual == expected:
        return True
    # Output aliases may change. The complete projection expressions,
    # population, grouping and ordering must remain identical.
    if (type(actual) is not exp.Select or len(actual.expressions) != len(expected.expressions)
            or any(not isinstance(item, exp.Alias) for item in actual.expressions)
            or len({item.alias for item in actual.expressions}) != len(actual.expressions)):
        return False
    def without_alias(tree):
        shaped = tree.copy()
        shaped.set('expressions', [item.unalias().copy() for item in tree.expressions])
        return shaped

    return without_alias(actual) == without_alias(expected)


def _previous_published_grouping(engine, ctx, source, *, after_view=False):
    """Recover the prior publication's exact grouped SQL and population."""
    if not (prior := _previous_group_input(engine, ctx, source, after_view=after_view)):
        return None
    ref, result, sql, _ = prior
    contract = result.get('sql_answer_contract') or result.get('answer_contract') or {}
    proof = contract.get('published_grouping') or {}
    publication = source.get('business_project') or {}
    expected = {'schema_version': 1, 'result_ref': ref, 'request_ref': result['result_lineage']['request_ref'],
                'source_id': source['id'], 'source_revision': source.get('target_revision', 0),
                'publication': {key: publication.get(key) for key in ('id', 'version')}}
    if (contract.get('complete') is not True or contract.get('business_confirmation') != 'configured'
            or any(proof.get(key) != value for key, value in expected.items())):
        return None
    try:
        start, stop = (date.fromisoformat(proof[key]) for key in ('start_inclusive', 'end_exclusive'))
    except (KeyError, TypeError, ValueError):
        return None
    read = sqlglot_name(source.get('dialect'))
    try:
        actual = sqlglot.parse_one(sql, read=read)
    except SqlglotError:
        return None
    matches = []
    for _, spec in _registered_count_specs(source):
        grouping = _published_count_grouping(source, spec)
        if not grouping or not (dialect := _group_dialect(source, spec)):
            continue
        candidate = {**spec, _GROUPING: grouping}
        expected_sql = sqlglot.parse_one(_group_sql(candidate, start, stop, dialect), read=dialect)
        if _same_published_grouping_sql(actual, expected_sql):
            matches.append((candidate, dialect, ref, result, start, stop))
    return matches[0] if len(matches) == 1 else None


def _previous_grouping_for_cutoff(engine, ctx, source, text, window_span):
    prior = _previous_published_grouping(engine, ctx, source, after_view=True)
    if prior and _matching_group_request(text, prior[0], window_span):
        return prior[:3]
    return None


def _previous_total_for_cutoff(engine, ctx, source, text, window_span, start, stop):
    """Regroup a named, verified monthly total from the immediately prior request."""
    from .published_scope import registered_total

    host = ctx.host_evidence or {}
    records = host.get('recent_requests') or []
    previous = records[-1] if records else {}
    if (type(host.get('turn')) is not int or previous.get('turn') != host['turn'] - 1
            or not previous.get('user_ref') or previous.get('clarifications')
            or not (prior := _previous_group_input(engine, ctx, source))):
        return None
    ref, result, sql, quote = prior
    request = (result.get('request_scope') or {}).get('request') or {}
    if (previous['user_ref'] != request.get('user_ref') or previous.get('text') != quote
            or not (scope := registered_total(source, quote, request))):
        return None
    matches = []
    for _, saved in _registered_count_specs(source):
        if saved['id'] != scope['scope_id'] or saved['table'] != scope['table']:
            continue
        grouping = _published_count_grouping(source, saved)
        if not grouping or not (dialect := _group_dialect(source, saved)):
            continue
        spec = {**saved, _GROUPING: grouping}
        if not _matching_group_request(text, spec, window_span):
            continue
        try:
            match = _matches(sqlglot.parse_one(sql, read=dialect), source, spec, None,
                             exact_filters=True, monthly_total=True)
        except SqlglotError:
            continue
        if (match and set(match[0].values()) == set(result.get('columns', []))
                and match[1] <= start < stop <= match[2]):
            matches.append((spec, dialect, ref))
    return matches[0] if len(matches) == 1 else None


_CURRENT_RANKING = re.compile(
    r'(?:请\s*)?(?:帮我\s*)?(?:想\s*)?(?:计算|统计|查询|查|看看|看(?:一下)?)?\s*'
    r'(?:当前|目前|现在|截至现在)(?:的)?\s*累[计积]的?\s*'
    r'(?P<subject>[^，,。!?:;\n]+?)\s*(?:里面|中|里)[，,]?\s*'
    r'哪(?:一)?(?:个|类|种|项)\s*(?P<dimension>[^，,。!?:;\n]+?)\s*的?'
    r'(?:案卷)?(?:数量|件数|记录数|数目|数)\s*(?P<direction>最多|最高|最少|最低)')
_GROUPING_SUFFIX = re.compile(r'[,。!?;:]\s*((?:按|按照|依照).+)$')


def _current_ranking(question):
    return _CURRENT_RANKING.fullmatch(normalize(question).strip().rstrip('。!?'))


def _matches_current_ranking(source, spec, rank):
    from .raw_dimension_scope import _labels

    if rank['dimension'].strip().rstrip('的') not in _labels(source, spec):
        return False

    def count_subject(label):
        label = normalize(label).strip()
        label = re.sub(r'^[a-z][a-z_0-9-]*\s+', '', label)
        label = re.sub(r'^(?:本年度|本年|今年|当年)(?:的)?', '', label)
        return re.sub(r'(?:总?数量|总?件数|记录数|总量|数目)$', '', label).strip()

    subject = rank['subject'].strip()
    return any(subject == count_subject(name) for term, saved in _registered_count_specs(source)
               if saved['id'] == spec['id'] and saved['table'] == spec['table']
               for name in [term.get('name'), *(term.get('aliases') or [])] if name)


def _explicit_month_before_ranking(host):
    from .current_month import month_refinement

    recent = host.get('recent_requests') or []
    questions = host.get('recent_questions') or []
    turn = host.get('turn')
    if len(recent) < 2 or len(questions) < 2 or type(turn) is not int:
        return None
    original, bridge = recent[-2:]
    refs = [original.get('user_ref'), bridge.get('user_ref'), host.get('user_ref')]
    if (not all(isinstance(ref, str) and ref for ref in refs) or len(set(refs)) != 3
            or (original.get('turn'), bridge.get('turn')) != (turn-2, turn-1)
            or (original.get('text'), bridge.get('text')) != tuple(questions[-2:])
            or original.get('clarifications') or bridge.get('clarifications')
            or not month_refinement(original['text'])):
        return None
    return original, bridge


def _prior_month_before_text_only_ranking(engine, ctx, source):
    """Resolve an explicit current-month scope across one completed ranking restatement."""
    from .current_month import calendar_month

    host = ctx.host_evidence or {}
    history = _explicit_month_before_ranking(host)
    if not history or history[1].get('completed_without_tools') is not True:
        return None
    original, _ = history
    if not (prior := _previous_group_input(engine, ctx, source, prior_offset=-2)):
        return None
    ref, result, _, _ = prior
    if result.get('result_ref') != ref or result.get('result_lineage', {}).get('request_ref') != original['user_ref']:
        return None
    month = calendar_month(host)
    saved = (result.get('request_scope') or {}).get('current_month') or {}
    if (not month or not all(saved.get(key) == month[key] for key in ('time_zone','period','end_exclusive'))
            or not saved.get('starts_inclusive') or month['starts_inclusive'][0] != saved['starts_inclusive'][0]):
        return None
    return prior, month


def _regroup_text_only_ranking(engine, ctx, source, text, recent):
    if not recent or not _GROUP_INTENT.search(text) or not (previous_rank := _current_ranking(recent[-1])):
        return None
    current_rank = previous_rank
    clause = text
    if suffix := _GROUPING_SUFFIX.search(text):
        if not (current_rank := _current_ranking(text[:suffix.start()])):
            return None
        clause = suffix[1]
    if not (continuation := _prior_month_before_text_only_ranking(engine, ctx, source)):
        return None
    prior, month = continuation
    if not (match := _previous_regrouping(engine, ctx, source, clause, repeated=True, prior=prior, current_month=month)):
        return None
    if (not _matches_current_ranking(source, match[0], previous_rank)
            or current_rank is not previous_rank and not _matches_current_ranking(source, match[0], current_rank)):
        return None
    direction = '最多' if current_rank['direction'] in ('最多', '最高') else '最少'
    return match, month, direction


def _grouping_current_month(engine, ctx, source, text, recent):
    """Replace just the time window, or rank within the last proven current month."""
    from .current_month import calendar_month, month_refinement

    refine = month_refinement(text)
    suffix = _GROUPING_SUFFIX.search(text)
    rank = _current_ranking(text[:suffix.start()] if suffix else text)
    short = _current_ranking(recent[-1]) if recent and not rank and _GROUP_INTENT.search(text) else None
    if not (refine or rank or short):
        return None
    host = ctx.host_evidence or {}
    records = host.get('recent_requests') or []
    previous = records[-1] if records else {}
    turn = host.get('turn')
    if (not recent or previous.get('text') != recent[-1] or previous.get('clarifications')
            or previous.get('completed_without_tools') is True
            or not previous.get('user_ref') or previous['user_ref'] == host['user_ref']
            or type(turn) is not int or previous.get('turn') != turn - 1):
        return None
    if not (month := calendar_month(host)) or not (prior := _previous_published_grouping(engine, ctx, source)):
        return None
    spec, dialect, ref, result, start, stop = prior
    if refine:
        start = date.fromisoformat(month['starts_inclusive'][0])
        stop = date.fromisoformat(month['end_exclusive'])
        return (spec, dialect, ref, start, stop), month, None

    ranking = rank or short
    clause = suffix[1] if suffix else text if short else None
    if (not _matches_current_ranking(source, spec, ranking)
            or clause and not _grouping_clause(clause, spec[_GROUPING], repeated=True)):
        return None
    saved = (result.get('request_scope') or {}).get('current_month') or {}
    if (any(saved.get(key) != month[key] for key in ('period', 'time_zone', 'end_exclusive'))
            or start.isoformat() != month['starts_inclusive'][0]
            or not month['calendar_date'] <= stop.isoformat() <= month['end_exclusive']):
        return None
    direction = '最多' if ranking['direction'] in ('最多', '最高') else '最少'
    return (spec, dialect, ref, start, stop), month, direction


def grouping_query(engine, ctx, source):
    """Resolve only a named published grouping for a precise scope and period."""
    host = ctx.host_evidence or {}
    text = normalize(host.get('user_text', '')).strip()
    if (not host.get('user_ref') or _SCOPE_CHANGE.search(text)
            or any(c.get('answer') for c in host.get('clarifications', []))):
        return None
    recent = host.get('recent_questions') or []
    repeated = _repeated_grouping_clause(text, recent[-1]) if recent else None
    previous_ref = None
    requested_period = None
    current_month = ranking_direction = None
    if (regrouped := _grouping_current_month(engine, ctx, source, text, recent)
            or _regroup_text_only_ranking(engine, ctx, source, text, recent)):
        (spec, dialect, previous_ref, start, stop), current_month, ranking_direction = regrouped
    elif repeated:
        rank = _current_ranking(recent[-1])
        if rank and _explicit_month_before_ranking(host):
            from .current_month import calendar_month
            if not (current_month := calendar_month(host)):
                return None
            ranking_direction = '最多' if rank['direction'] in ('最多', '最高') else '最少'
        previous = _previous_regrouping(engine, ctx, source, repeated, repeated=True, current_month=current_month)
        if not previous:
            return None
        spec, dialect, previous_ref, start, stop = previous
        if current_month and not _matches_current_ranking(source, spec, rank):
            return None
    elif window := _group_window(text, host):
        from .temporal_context import temporal_context
        clock = temporal_context(host.get('client_time_zone', 'UTC'))
        if clock.get('zone_fallback') is False and clock.get('time_zone') and clock.get('current_month'):
            requested_period = {'time_zone': clock['time_zone'], 'calendar_month': clock['current_month']}
        window_span, start, stop = window
        alias = ''
        if explicit := named_scope(source, text):
            term, alias = explicit
            spec = term['config']
            if (not (dialect := _group_dialect(source, spec))
                    or any(candidate['id'] != source['id'] and named_scope(candidate, text)
                           for candidate in engine.catalog(ctx.project)['sources'])):
                return None
            if _PRIOR_SCOPE.search(text):
                prior = _previous_detail(engine, ctx, source, start, stop)
                if not prior or prior[0] != spec:
                    return None
                previous_ref = prior[2]
        elif previous := _previous_grouping_for_cutoff(engine, ctx, source, text, window_span):
            spec, dialect, previous_ref = previous
        elif previous := _previous_detail(engine, ctx, source, start, stop):
            spec, dialect, previous_ref = previous
        elif previous := _previous_total_for_cutoff(engine, ctx, source, text, window_span, start, stop):
            spec, dialect, previous_ref = previous
        elif listed := _listed_grouping(engine, ctx, source, text, window_span):
            spec, dialect = listed
        else:
            return None
        if not _matching_group_request(text, spec, window_span, alias):
            return None
    elif previous := _previous_regrouping(engine, ctx, source, text):
        spec, dialect, previous_ref, start, stop = previous
    else:
        return None
    grouping = spec[_GROUPING]
    proof = {**_proof_source(source, host, start, stop),
             'category_column': '大类', 'count_column': '数量（' + spec['unit'] + '）', 'unit': spec['unit'],
             'labels': [*(row['label'] for row in grouping['prefixes']), grouping['other']['label']],
             'unclassified_label': grouping['unclassified'],
             **({'requested_period': requested_period} if requested_period else {}),
             **({'current_month': {**current_month, 'basis': 'published_grouping', 'source_id': source['id'],
                                   'table': spec['table'], 'column': spec['time_column']}} if current_month else {}),
             **({'ranking_direction': ranking_direction} if ranking_direction else {}),
             **({'source_result_ref': previous_ref} if previous_ref else {})}
    return {'kind': 'published_grouped_count', 'basis': grouping['basis'],
            'query_request': {'source_id': source['id'], 'sql': _group_sql(spec, start, stop, dialect),
                              'title': f'{start:%Y-%m}至{stop - timedelta(days=1):%Y-%m}各大类统计', 'purpose': 'answer'},
            'instruction': ('将 query_request 直接传给 data_sql_query。该查询一次返回已发布分类的计数及未分类，分母覆盖所选总体。'
                            '新分组作为新结果保留，不填写 replaces_result_ref。用户自定义分类使用原生确认。'),
            'proof': proof}


def require_grouping_query(engine, ctx, source, sql, *, purpose='answer'):
    if purpose == 'explore' or not (route := grouping_query(engine, ctx, source)):
        return None
    read = sqlglot_name(source.get('dialect'))
    try:
        actual = sqlglot.parse_one(sql, read=read)
    except SqlglotError:
        return None
    expected = sqlglot.parse_one(route['query_request']['sql'], read=read)
    if actual == expected:
        return route['proof']
    if route['proof'].get('current_month') and _same_published_grouping_sql(actual, expected):
        category, count = (item.alias for item in actual.expressions)
        return {**route['proof'], 'category_column': category, 'count_column': count}
    raise ValueError('PUBLISHED_GROUPING_REQUIRED: 原问选择同范围的已发布大类。使用以下一次查询，未分类单列；'
                     '新分组创建新结果，不填写 replaces_result_ref。SQL：' + route['query_request']['sql'])


def grouping_result(result, proof):
    """Publish counts and full-population denominator from the same SQL result."""
    if (not proof or result.get('status') not in (None, 'succeeded') or result.get('truncated')
            or (result.get('checks') or {}).get('value_warnings')):
        return None
    rows = result.get('rows') or []
    category, count = proof['category_column'], proof['count_column']
    if len(rows) != result.get('row_count') or set(result.get('columns') or []) != {category, count} or not rows:
        return None
    labels, unclassified = proof['labels'], proof['unclassified_label']
    allowed = {*labels, unclassified}
    values = {}
    for row in rows:
        label, n = row.get(category), row.get(count)
        if label not in allowed or label in values or type(n) is not int or not 0 < n <= 2**53 - 1:
            return None
        values[label] = n
    denominator = sum(values.values())
    if not 0 < denominator <= 2**53 - 1:
        return None
    return {**{k: v for k, v in proof.items() if k != 'labels'}, 'result_ref': result['result_ref'],
            'case_count': len(rows), 'known_counts': [{'label': label, 'count': values.get(label, 0)} for label in labels],
            'unclassified_count': values.get(unclassified, 0), 'denominator': denominator}


def facts_from_sql(engine, project, result, dimension):
    error = ('DERIVE_SEMANTICS: 该结果没有可派生的已发布计数依据。仅接受同一授权源、已登记口径下的完整逐行计数；'
             '去重、比率、连接及截断结果不能直接相加。')
    if not result.get('sql') or not result.get('plan_ref') or not result.get('rows'):
        raise ValueError(error)
    if result.get('truncated') or len(result['rows']) != result.get('row_count'):
        raise ValueError('DERIVE_INCOMPLETE: 查询结果不完整，不能对部分分组求和。')
    if (result.get('result_lineage') or {}).get('purpose') != 'answer':
        raise ValueError(error)
    source = engine.source(project, result['source_id'])
    plan = engine.artifacts.get(project, result['plan_ref'])
    if plan['kind'] != 'data.plan' or not plan['parents']:
        raise ValueError(error)
    bound = engine.artifacts.get(project, plan['parents'][0])
    if bound['kind'] != 'data.bound-plan' or bound['payload'].get('mode') != 'query':
        raise ValueError(error)
    from .task_contract import bound_catalog_source
    old_sources = bound_catalog_source(engine, project, bound) or []
    original = next((s for s in old_sources if s['id'] == source['id']), {})
    published = [t.get('config') for t in original.get('terms', [])]
    query = plan['payload']['query']
    from .read_limits import execution_basis
    sql = query['logical_sql']
    if (query.get('parameters') or query.get('datasource_ref') != source['id'] or not original
            or result['sql'] != sql and result.get('execution_basis') != execution_basis(sql, result['sql'])):
        raise ValueError(error)
    tree = sqlglot.parse_one(sql, read=sqlglot_name(source['dialect']))
    candidates = []
    exact = []
    for row in source.get('terms', []):
        spec = row.get('config') or {}
        if spec.get('kind') != KIND or spec not in published or spec.get('source_revision') != source.get('target_revision', 0):
            continue
        validate(spec, source)
        if match := _matches(tree, source, spec, dimension):
            candidate = (spec, match)
            candidates.append(candidate)
            if _matches(tree, source, spec, dimension, exact_filters=True):
                exact.append(candidate)
    if exact:
        candidates = exact
    if len(candidates) != 1:
        raise ValueError(error)
    spec, (names, start, stop) = candidates[0]
    if set(result['columns']) != set(names.values()):
        raise ValueError(error)
    facts, seen = [], set()
    last_day = stop - timedelta(days=1)
    date_window = f'{start.isoformat()} 至 {last_day.isoformat()}'
    for i, row in enumerate(result['rows']):
        category, value = row.get(names['dimension']), row.get(names['count'])
        if not isinstance(category, str) or not category.strip():
            raise ValueError('DERIVE_DIMENSION: 分类有空值或非文本标签；请先核对其归属，不能并入其他类。')
        period = date_window
        if 'month' in names:
            raw = str(row.get(names['month']))
            if not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])(?:-01(?:[ T]00:00:00(?:\+00:00)?)?)?', raw):
                raise ValueError('DERIVE_PERIOD: 查询月份无法核对，不能合并。')
            period = raw[:7]
            if not start.strftime('%Y-%m') <= period <= last_day.strftime('%Y-%m'):
                raise ValueError('DERIVE_PERIOD: 查询月份超出已执行范围，不能合并。')
        if (category, period) in seen or type(value) is not int or value < 0:
            raise ValueError(error)
        seen.add((category, period))
        facts.append({'id': f"{result['result_ref']}:{i}", 'dimensions': {dimension: category, 'period': period},
                      'value': value, 'unit': spec['unit']})
    grouping = _published_count_grouping(source, spec, original) if exact else None
    return facts, {'id': spec['id'], 'unit': spec['unit'], 'additive': True, 'additive_over_time': True,
                   **({_GROUPING: grouping} if grouping else {}),
                   'source_sql_count_basis': {'publication': result.get('business_project', {}), 'definition': spec['basis'],
                       'definition_fingerprint': fingerprint(spec), 'sql_fingerprint': fingerprint(sql),
                       'start': start.isoformat(), 'stop_exclusive': stop.isoformat(), 'filters': spec['filters']}}
