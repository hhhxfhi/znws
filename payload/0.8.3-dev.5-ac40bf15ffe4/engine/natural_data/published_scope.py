"""Check that a single-table SQL keeps a published business scope named by the request.

The scope comes only from a published row-count-aggregation term: an explicit
scope alias or the uniquely named measure of a whole-population query. No
table, column or enumeration is known here. Only an aggregate over one
registered fact table with a conjunctive WHERE is judged. Independent category
labels in SELECT or GROUP BY do not change the WHERE population. A recognized calendar
join can supply that fact aggregate; arbitrary joins, CTEs and other opaque
predicates are reported as unverified.
"""
import re
from datetime import date

from sqlglot import exp

from .retrieval import normalize
from .row_count_aggregation import _registered_count_specs, named_scope, subgroup_names

KIND = 'published_scope'
_FUZZY = (exp.Like, exp.ILike, exp.Glob, exp.RegexpLike, exp.RegexpILike)
_WILDCARD = re.compile(r'[%_]')
_ALL = re.compile(r'(?:改查|改(?:为|成)(?:统计|查询|看|查)?|换成(?:统计|查询|看|查)?)\s*(?:全部|所有|全量)(?P<object>[^。；;，,\n]*)')
_DIMENSION = re.compile(r'月份|年份|季度|期间|日期|时间|地市|地区|字段|维度')
_CONTINUITY = re.compile(r'(?:其他|其余)(?:条件|范围)?不变|同一范围|仍按|沿用')
_PROVINCE_TARGET = r'(?:全国|省内外|省外|外省|跨省|[\u4e00-\u9fff]{2,5}(?:省(?!份|级|会)|自治区))'
_PROVINCE_SCOPE_CHANGE = re.compile(
    r'(?:^|[。；;，,!?]\s*|继续\s*)(?:请\s*)?(?:改(?:查|看|为|成|统计)|换(?:成|查)|只(?:查|统计|看)|仅(?:查|统计|看))\s*'
    r'(?:查|看|统计|按|到|为)?\s*(?P<place>'+_PROVINCE_TARGET+r')(?!环比|代码)'
    r'|(?:^|[。；;，,!?]\s*|继续\s*)(?:请\s*)?(?:排除|不含|不包括|剔除)\s*(?P<exclude>本省|省内|'+_PROVINCE_TARGET+r')')
_YEAR = r'(?:本年度?|今年|当年|去年|上年度?|(?:19|20)\d{2}\s*年(?:度)?)'
_ACTION = r'(?:(?:请\s*)?(?:帮我|你|您)?\s*)'
_YEAR_COUNT = re.compile(_ACTION + r'(?:统计|查询|查一下|计算|汇总|列出|查看)?\s*' + _YEAR + r'\s*(?P<label>[^,。!?;\n]{2,90})')
_MONTH_COUNT = re.compile(r'\s*(?:业务\s*)?(?:的\s*)?(?:每(?:一)?个?月|各月|逐月|按月|月度)\s*(?:新增\s*)?的?\s*')
_HISTORY_COUNT = re.compile(_ACTION + r'(?:(?:预测|预估|预计)\s*)?(?:基于|根据|依据|使用|用)\s*'
                            r'(?:近|最近|过去)\s*(?:\d{1,2}|[一二两三四五六七八九十])\s*(?:年|个月|月)\s*的?\s*'
                            r'(?P<label>[^,。!?;\n]{2,90})')
_FORECAST_COUNT = re.compile(_ACTION + r'(?:预测|预估|预计)\s*(?:' + _YEAR +
                             r'|明年|下一年)(?:\s*\d{1,2}\s*月(?:份)?)?\s*的?\s*(?P<label>[^,。!?;\n]{1,75})')
_COUNT_NOUN = re.compile(r'(?P<noun>案卷|记录|单据|事件)?(?:累计|合计)?(?:总数量|总数|总量|数量|数据量|数)$')


def _total_label(text, unit):
    from .metric_search import strip_unit

    label = normalize(strip_unit(text, unit)).strip()
    label = re.sub(r'^[a-z][a-z0-9]*(?:_[a-z0-9]+)+\s+', '', label)
    label = re.sub(r'^' + _YEAR + r'\s*', '', label)
    label = re.sub(r'\s*(?:情况|是多少|有多少)\s*$', '', label)
    unit_count = re.search(re.escape(normalize(unit)) + r'数$', label)
    suffix = unit_count or _COUNT_NOUN.search(label)
    if not suffix:
        return None
    subject = re.sub(r'\s+|的', '', label[:suffix.start()]).removesuffix('业务')
    return (subject, None if unit_count else suffix['noun']) if len(subject) >= 3 else None


def registered_total(source, text, reference):
    """Bind a simple annual count or training request by the declared measure."""
    from .monthly_metric_query import _PRESENTATION, _YEAR_TOTAL

    publication = source.get('business_project') or {}
    if not publication.get('id') or type(publication.get('version')) is not int:
        return None
    # A clarification answers a question this same turn; it does not cancel the
    # declared total.  Match the user's own wording (not the appended answer) so
    # the population is bound, then let the caller apply the chosen value.
    asked_text = reference.get('quote') or text
    clauses = [part.strip() for part in re.split(r'[,。!?;\n]+', normalize(asked_text)) if part.strip()]
    if not clauses:
        return None
    forecast = None
    if annual := _YEAR_COUNT.fullmatch(clauses[0]):
        if any(not (_PRESENTATION.fullmatch(part) or _YEAR_TOTAL.fullmatch(part)) for part in clauses[1:]):
            return None
        asked = _MONTH_COUNT.sub('', annual['label'], count=1)
    elif (len(clauses) == 2 and (history := _HISTORY_COUNT.fullmatch(clauses[0]))
          and (target := _FORECAST_COUNT.fullmatch(clauses[1]))):
        asked = history['label']
        forecast = target['label']
    else:
        return None

    candidates = []
    for term, spec in _registered_count_specs(source):
        if (spec.get('scope_aliases') or term.get('status') == 'pending_review'
                or term.get('clarification') or term.get('pending_clarification')):
            continue  # Existing explicit subset selection owns these terms.
        unit = spec['unit']
        # The declared location can be repeated explicitly. Other locations
        # and any added condition remain in the name and prevent a match.
        label = asked
        for values in spec['filters'].values():
            if _province_filter(values):
                label = label.removeprefix(normalize(values[0])).strip()
        if not (query := _total_label(label, unit)):
            continue
        names = (term['name'], *(term.get('aliases') or ()))
        for name in names:
            registered = _total_label(name, unit)
            if not registered or registered[0] != query[0] or query[1] and query[1] != registered[1]:
                continue
            if forecast:
                target_label = _total_label(forecast, unit)
                target_subject = target_label[0] if target_label else forecast.removesuffix('量').strip()
                if (len(target_subject) < 2 or not registered[0].endswith(target_subject)
                        or target_label and target_label[1] and target_label[1] != registered[1]):
                    continue
            candidates.append((term, spec))
            break
    if len(candidates) != 1:
        return None
    term, spec = candidates[0]
    # A clarification may move the region; that is a different population, not
    # an answered question, so the declared total is not bound.
    clarification = ' '.join(str(item.get('answer') or '') for item in reference.get('clarifications') or [])
    if clarification and _clarified_other_region(spec['filters'], clarification):
        return None
    constraint = {**_constraint(source, spec, term['name'], reference), 'whole_total': True,
                  'time_column': spec['time_column']}
    constraint['user_refined'] = _named_filter_columns(source, constraint, asked_text)
    return constraint


def annual_monthly_count_query(source, host):
    """Use the registered whole-population count for an unambiguous month series."""
    from .approval_event_scope import _requested_role
    from .monthly_sql_scope import _annual_year
    from .row_count_aggregation import _group_dialect, _population_sql

    quote = host.get('user_text') or ''
    text = normalize(quote).strip()
    publication = source.get('business_project') or {}
    ref = host.get('user_ref')
    if (not ref or host.get('clarifications')
            or not _MONTH_COUNT.search(text) or not (annual := _annual_year(host, text))
            or _requested_role(host) in ('intake', 'completion')
            or not (scope := registered_total(source, text, {'user_ref': ref, 'quote': quote}))
            or scope['user_refined']):
        return None
    spec = next(spec for _, spec in _registered_count_specs(source) if spec['id'] == scope['scope_id'])
    if not (dialect := _group_dialect(source, spec)):
        return None
    ident = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    field = ident(spec['time_column'])
    month = f"TO_CHAR({field}, 'YYYY-MM')" if dialect == 'postgres' else f"STRFTIME('%Y-%m', {field})"
    year = annual[0]
    name = re.sub(r'^[A-Za-z0-9_]+\s+', '', scope['alias'])
    name = re.sub(r'^' + _YEAR + r'\s*', '', name)
    measure = ident(f"{name}（{spec['unit']}）")
    sql = (f'SELECT {month} AS "月份", COUNT(1) AS {measure} '
           + _population_sql(spec, date(year, 1, 1), date(year + 1, 1, 1), dialect) + ' GROUP BY 1 ORDER BY 1')
    return {'schema_version': 1, 'request_ref': ref, 'quote': quote,
            'source_id': source['id'], 'connection_revision': source.get('connection_revision', 0),
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'sql': sql, 'title': f'{year}年逐月{name}', 'purpose': 'answer'}}


def relative_monthly_count_query(engine, ctx, source, host):
    """Take closed observations of a published total or a named category."""
    from natural_analysis.forecasting import (
        _MONTHLY_FORECAST_FORMAT, relative_forecast_horizon, relative_month_window, target_mentions,
    )

    from .observed_sql_scope import _labels
    from .row_count_aggregation import _group_dialect

    quote = host.get('user_text') or ''
    publication = source.get('business_project') or {}
    request = host.get('user_ref')
    if not request or not publication.get('id') or not publication.get('version') or host.get('clarifications'):
        return None
    horizon = relative_forecast_horizon(host)
    if not horizon:
        window = relative_month_window(host, target_mentions(host))
        scope = registered_total(source, quote, {'user_ref': request, 'quote': quote}) if window else None
        if not scope or scope['user_refined']:
            return None
        spec = next(spec for _, spec in _registered_count_specs(source) if spec['id'] == scope['scope_id'])
        if not (dialect := _group_dialect(source, spec)):
            return None
        label = re.sub(r'^' + _YEAR + r'\s*', '', re.sub(r'^[A-Za-z0-9_]+\s+', '', scope['alias']))
        return _training_count_recipe(source, host, spec, label, dialect, date.fromisoformat(window['start_inclusive']),
                                      date.fromisoformat(window['end_exclusive']))
    wording = normalize(_MONTHLY_FORECAST_FORMAT.sub('', quote)[horizon['span'][1]:]).lstrip(' ,的').strip()
    if not (label := re.fullmatch(
            r'(?P<subject>[\u4e00-\u9fff]{3,60}?)(?:案卷|记录|单据|事件)?(?:件数|笔数|数量|数据量|量|数)\s*'
            r'(?:预计|预估)?\s*(?:是|是否|会|会不会|会有|有没有|会否)?\s*'
            r'(?:变化|增长|上升|下滑|下降|减少|增加|持平|走向|趋势|走势)(?:的)?(?:趋势|走势)?(?:如何|吗)?[。!?]?\s*',
            wording)):
        return None
    qualified = re.sub(r'(?:这个|这种|这一种)?(?:形式|类别|类型)的|的|\s+', '', label['subject'])
    candidates = []
    for term, spec in _registered_count_specs(source):
        if spec.get('scope_aliases') or not (registered := _total_label(term['name'], spec['unit'])):
            continue
        subject, _ = registered
        subjects = {subject, subject.removesuffix('批准')}
        prefixes = {qualified.removesuffix(name) for name in subjects if qualified.endswith(name) and qualified != name}
        if not prefixes or not (dialect := _group_dialect(source, spec)):
            continue
        table = next(table for table in source['tables'] if table['name'] == spec['table'])
        dimension = next(col for col in table['columns'] if col['name'] == spec['dimension'])
        for value, category in _labels(engine, ctx, source, table, dimension):
            if normalize(category) in prefixes:
                candidates.append((spec, subject, value, category, dialect))
    if len(candidates) != 1:
        return None
    spec, subject, value, category, dialect = candidates[0]
    pop = {**spec, 'filters': {**spec['filters'], spec['dimension']: [value]}}
    return _training_count_recipe(source, host, pop, f'{category}{subject}数量', dialect, None,
                                  date.fromisoformat(horizon['start'] + '-01'))


def _training_count_recipe(source, host, spec, label, dialect, start, stop):
    from .row_count_aggregation import _population_sql

    publication = source['business_project']
    ident = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    date_field = ident(spec['time_column'])
    month = f"TO_CHAR({date_field}, 'YYYY-MM')" if dialect == 'postgres' else f"STRFTIME('%Y-%m', {date_field})"
    sql = (f'SELECT {month} AS "月份", COUNT(1) AS {ident(label + "（" + spec["unit"] + "）")} '
           + _population_sql(spec, start, stop, dialect) + ' GROUP BY 1 ORDER BY 1')
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': host['user_text'], 'source_id': source['id'],
            'connection_revision': source.get('connection_revision', 0),
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'title': f'{label}的月度历史', 'sql': sql, 'purpose': 'answer'}}


def _clarified_other_region(filters, text):
    """A clarification that names another province changes the population.

    The declared total keeps its registered filters; an answer that moves the
    region must not be bound to it.  Answers about period or grade leave the
    population alone and may still reuse the declaration.
    """
    registered = [normalize(values[0]) for values in filters.values() if _province_filter(values)]
    if not registered:
        return False
    normalized = normalize(text)
    if not re.search(r'省|自治区', normalized):
        return False
    return not any(name in normalized for name in registered)


def _named_filter_columns(source, constraint, text):
    """Columns the user explicitly names this turn, so a declared total may be split."""
    if not constraint or not constraint.get('whole_total'):
        return []
    table = next((item for item in source.get('tables', []) if item['name'] == constraint['table']), None)
    if not table:
        return []
    known = set(constraint.get('filters') or {})
    if constraint.get('time_column'):
        known.add(constraint['time_column'])
    named = []
    for column in table.get('columns', []):
        if column['name'] in known:
            continue
        if any(str(item.get('label') or '') and str(item.get('label')) in text
               for item in column.get('business_values') or []):
            named.append(column['name'])
    return named


def derive(source, text, reference):
    """The published scope explicitly named in a positive request, or None."""
    selected = named_scope(source, text)
    if not selected:
        return registered_total(source, text, reference)
    term, alias = selected
    constraint = geographic_refinement(_constraint(source, term['config'], alias, reference), text, reference)
    if constraint:
        # A declared total still belongs to the user: naming a registered value
        # (grade, specialty, ...) is a deliberate split, not a conflict.
        constraint['user_refined'] = _named_filter_columns(source, constraint, text)
    return constraint


def _constraint(source, spec, alias, reference):
    return {'kind': KIND, 'table': spec['table'], 'scope_id': spec['id'], 'alias': alias,
            'source_id': source['id'], 'source_revision': spec['source_revision'],
            'aliases': list(spec.get('scope_aliases') or []),
            'filters': {column: list(values) for column, values in spec['filters'].items()},
            'basis': spec['basis'], 'evidence': reference}


def _province_changed(filters, text):
    provinces = {normalize(values[0]) for values in filters.values() if _province_filter(values)}
    if not provinces:
        return False
    return any((match['place'] and match['place'] not in provinces)
               or (match['exclude'] and match['exclude'] in provinces | {'本省','省内'})
               for match in _PROVINCE_SCOPE_CHANGE.finditer(text))


def _province_filter(values):
    return len(values)==1 and isinstance(values[0], str) and values[0].endswith(('省','自治区'))


def previous_grouping_scope(engine, ctx, source, reference):
    """Recover predicates only from the previous proven grouping, never from a task draft."""
    from .row_count_aggregation import _group_dialect, _group_sql, _previous_group_input, _published_specs
    from sqlglot import parse_one
    from sqlglot.errors import SqlglotError
    from datetime import date

    if not engine or not ctx or not (previous := _previous_group_input(engine, ctx, source)):
        return None
    ref, result, sql, _ = previous
    contract = result.get('sql_answer_contract') or result.get('answer_contract') or {}
    proof = contract.get('published_grouping') or {}
    publication = source.get('business_project') or {}
    expected = {'schema_version':1, 'result_ref':ref, 'request_ref':result['result_lineage']['request_ref'],
                'source_id':source['id'], 'source_revision':source.get('target_revision', 0),
                'publication':{key:publication.get(key) for key in ('id','version')}}
    if (not publication.get('id') or not publication.get('version') or contract.get('complete') is not True
            or contract.get('business_confirmation')!='configured'
            or any(proof.get(key)!=value for key,value in expected.items())):
        return None
    try:
        start, stop = (date.fromisoformat(proof[key]) for key in ('start_inclusive','end_exclusive'))
    except (KeyError, TypeError, ValueError):
        return None
    matches = []
    for _, spec in _published_specs(source):
        if not spec.get('published_grouping') or not (dialect := _group_dialect(source, spec)):
            continue
        try:
            if parse_one(sql, read=dialect)==parse_one(_group_sql(spec, start, stop, dialect), read=dialect):
                matches.append(spec)
        except SqlglotError:
            continue
    if len(matches)==1:
        spec = matches[0]
        return _constraint(source, spec, spec['scope_aliases'][0], reference)
    return None


def superseded(constraint, text, removed_columns, source):
    """An inherited scope ends when the turn renames, narrows or cancels it."""
    if any(name == constraint['table'] and column in constraint['filters'] for name, column in removed_columns):
        return True
    if constraint.get('source_id') != source['id'] or constraint.get('source_revision') != source.get('target_revision', 0):
        return True
    if not any(spec.get('id') == constraint['scope_id'] and spec.get('source_id') == source['id']
               and spec.get('source_revision') == constraint['source_revision']
               and spec.get('table') == constraint['table'] and (spec.get('filters') == constraint['filters']
                    or constraint.get('geographic_refined') is True and {column:values
                        for column,values in (spec.get('filters') or {}).items() if not _province_filter(values)}==constraint['filters'])
               and (spec.get('scope_aliases') or []) == constraint['aliases']
               for term in source.get('terms', []) if (spec := term.get('config'))):
        return True
    normalized = normalize(text)
    names = [normalize(a).strip() for a in constraint.get('aliases', [])]
    if any(name and name in normalized for name in names):
        return True
    if (not _CONTINUITY.search(normalized) and (change := _ALL.search(normalized))
            and not _DIMENSION.search(change['object'])):
        return True
    return any(part in normalized for part in subgroup_names(constraint))


def geographic_refinement(constraint, text, reference):
    """A region change releases that slice while preserving other business filters."""
    if not _province_changed(constraint['filters'], normalize(text)):
        return constraint
    filters = {column:values for column,values in constraint['filters'].items() if not _province_filter(values)}
    return {**constraint, 'filters':filters, 'evidence':reference, 'geographic_refined':True} if filters else None


def _conjuncts(node):
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    if isinstance(node, exp.And):
        return _conjuncts(node.this) + _conjuncts(node.expression)
    return [node] if node is not None else []


def _disjuncts(node):
    if isinstance(node, exp.Paren):
        return _disjuncts(node.this)
    if isinstance(node, exp.Or):
        return _disjuncts(node.this) + _disjuncts(node.expression)
    return [node]


def _literal(node):
    return str(node.this) if isinstance(node, exp.Literal) else None


def _membership(node, is_column):
    """('set', column, values) | ('fuzzy', column, pattern) | None for one predicate."""
    if isinstance(node, exp.Paren):
        return _membership(node.this, is_column)
    if isinstance(node, exp.Or):
        parts = [_membership(item, is_column) for item in _disjuncts(node)]
        if all(p and p[0] == 'set' for p in parts) and len({p[1] for p in parts}) == 1:
            return 'set', parts[0][1], set().union(*(p[2] for p in parts))
        return None
    if isinstance(node, exp.EQ):
        for left, right in ((node.this, node.expression), (node.expression, node.this)):
            if (column := is_column(left)) and (value := _literal(right)) is not None:
                return 'set', column, {value}
        return None
    if isinstance(node, exp.In):
        values = [_literal(item) for item in node.expressions]
        if (column := is_column(node.this)) and values and None not in values and not node.args.get('query') \
                and not node.args.get('unnest') and not node.args.get('field'):
            return 'set', column, set(values)
        return None
    if isinstance(node, _FUZZY) and (column := is_column(node.this)):
        pattern = _literal(node.expression)
        if isinstance(node, (exp.Like, exp.ILike)) and pattern is not None and not _WILDCARD.search(pattern):
            return None  # LIKE without wildcards may still be case insensitive.
        return 'fuzzy', column, pattern
    return None


def _condition(column, values):
    quoted = ["'" + value.replace("'", "''") + "'" for value in values]
    return f'{column} = {quoted[0]}' if len(quoted) == 1 else f'{column} IN ({", ".join(quoted)})'


def _judge(column, expected, sets, fuzzy, opaque, alias, *, whole_total=False):
    """One filter column: (state, detail). Detail names the original published values."""
    registered = f'已发布范围“{alias}”要求 {_condition(column, sorted(expected))}'
    if opaque:
        return 'unknown', f'{column} 的条件包含无法静态证明的写法（函数、NOT、范围、嵌套 OR 或参数）；{registered}，需独立核对。'
    exact = set.intersection(*sets) if sets else None
    if fuzzy and not (exact is not None and exact == expected):
        return 'conflict', (f'{column} 使用 LIKE/模糊匹配代替已发布的精确取值集合，会包含未登记的取值或遗漏登记值。'
                            f'请按原口径修正：{_condition(column, sorted(expected))}。')
    if fuzzy:
        return 'unknown', f'{column} 在精确取值集合之外还有模糊条件，范围可能被额外收窄；{registered}。'
    if exact is None:
        return 'conflict', (f'SQL 未按 {column} 限定，漏掉了原问点名的已发布业务范围“{alias}”，会把范围外记录一并统计。'
                            f'请按原口径修正：{_condition(column, sorted(expected))}，其余期间等条件保持不变。')
    if exact == expected:
        return 'checked', f'{column} 的筛选与已发布范围“{alias}”一致。'
    extra = sorted(exact - expected)
    if extra:
        return 'conflict', (f'{column} 的筛选放宽或偏离了已发布范围“{alias}”：多出 {extra[:3]}'
                            f'{"等" if len(extra) > 3 else ""}。请按原口径修正：{_condition(column, sorted(expected))}。')
    if not exact:
        return 'conflict', f'{column} 的多个条件互相排斥，结果恒为空；请按原口径修正：{_condition(column, sorted(expected))}。'
    if whole_total:
        return 'conflict', f'{column} 只覆盖已发布总量的部分取值。请按原口径修正：{_condition(column, sorted(expected))}。'
    return 'unknown', (f'{column} 只覆盖已发布范围“{alias}”的部分取值 {sorted(exact)[:3]}，可能是分项查询；'
                       '不能把该结果当作整个业务范围的合计。')


def _opaque_cases(tree, ref):
    cases = list(tree.find_all(exp.Case))
    if not cases:
        return False
    # Only independently projected/grouped categories over the actual fact table
    # can be ignored. Conditional amounts or later row/group filters are opaque.
    source = tree.args.get('from')
    if (source is None or source.this is not ref or ref.args.get('sample') or not tree.args.get('group')
            or tree.args.get('having') or tree.args.get('qualify')):
        return True
    for case in cases:
        if case.find_ancestor(exp.AggFunc) or case.find(exp.AggFunc):
            return True
        clause = case
        while clause.parent is not tree:
            clause = clause.parent
        if clause.arg_key not in ('expressions', 'group'):
            return True
    # Some dialects accept projection aliases in WHERE, so even a CASE in
    # SELECT may indirectly restrict the input rows.
    where = tree.args.get('where')
    if where:
        aliases = {item.alias.casefold() for item in tree.expressions if item.alias and item.find(exp.Case)}
        return any(not col.table and col.name.casefold() in aliases for col in where.find_all(exp.Column))
    return False


def inspect(tree, constraint, source, simple):
    """Findings for one published scope: a list of (state, detail)."""
    from .monthly_sql_scope import _source_table, calendar_fact_select
    matching = [ref for ref in tree.find_all(exp.Table)
                if (table := _source_table(ref, source)) and table['name'] == constraint['table']]
    if not matching:
        if simple and constraint.get('whole_total'):
            return [('conflict', f'已发布总量“{constraint["alias"]}”须按登记的事实表查询。请保留原对象与期间。')]
        return []  # The scope belongs to another table or data source; leave that query alone.
    alias = constraint['alias']
    if not simple and len(matching) == 1 and (facts := calendar_fact_select(tree, source)) is not None:
        if any(ref is matching[0] for ref in facts.find_all(exp.Table)):
            tree, simple = facts, True
    if (not simple or len(matching) != 1 or tree.args.get('joins')
            or tree.find(exp.Filter, exp.Window) or _opaque_cases(tree, matching[0])):
        return [('unknown', f'含 CTE、子查询、关联、集合运算或条件表达式的查询无法静态证明已发布范围“{alias}”被完整保留，记为未验证。')]
    if not any(projection.find(exp.AggFunc) for projection in tree.expressions):
        return [('unknown', f'当前查询没有聚合计算（明细或探查读取），不据此判定已发布范围“{alias}”。')]

    table = next(item for item in source['tables'] if item['name'] == constraint['table'])
    tracked = ({item['name'] for item in table['columns']} - {constraint['time_column']}
               if constraint.get('whole_total') else set(constraint['filters']))
    columns = {column.lower(): column for column in tracked}
    ref = matching[0]

    def is_column(node):
        if (isinstance(node, exp.Column) and node.name.lower() in columns
                and (not node.table or node.table.casefold() == ref.alias_or_name.casefold())
                and (not node.db or node.db.casefold() == ref.db.casefold())
                and (not node.catalog or node.catalog.casefold() == ref.catalog.casefold())):
            return columns[node.name.lower()]
        return None

    sets, fuzzy, opaque, proved_extra = {}, {}, set(), set()
    where = tree.args.get('where')
    for atom in _conjuncts(where.this if where else None):
        found = _membership(atom, is_column)
        if found and found[0] == 'set':
            sets.setdefault(found[1], []).append(found[2])
        elif found:
            fuzzy.setdefault(found[1], []).append(found[2])
        else:
            fields = {c for node in atom.find_all(exp.Column) if (c := is_column(node))}
            if (constraint.get('whole_total') and isinstance(atom, (exp.NEQ, exp.GT, exp.GTE, exp.LT,
                    exp.LTE, exp.Between, exp.Is, exp.Not)) and len(fields) == 1):
                proved_extra.update(fields - constraint['filters'].keys())
            opaque.update(fields)
    if tree.args.get('having'):
        opaque.update(c for node in tree.args['having'].find_all(exp.Column) if (c := is_column(node)))

    if constraint.get('whole_total'):
        extra = sorted(proved_extra | ((sets.keys() | fuzzy.keys()) - constraint['filters'].keys()))
        refined = set(constraint.get('user_refined') or [])
        if extra and set(extra) <= refined:
            return [('unknown', f'SQL 按用户本轮指定的分项字段 {", ".join(extra)} 计算，'
                      f'已不是登记总量“{alias}”的完整总体；请按分项交付，不要称为总体合计。')]
        if extra:
            return [('conflict', f'SQL 为已发布总量“{alias}”额外增加了筛选字段 {", ".join(extra)}。'
                      '请保留登记的完整总体和原期间，只有用户改范围时才用分项。')]
        if opaque - constraint['filters'].keys():
            return [('unknown', f'SQL 有未能核实的额外筛选，不能证明已完整保留已发布总量“{alias}”。')]

    results = [(column, *_judge(column, set(values), sets.get(column), fuzzy.get(column), column in opaque, alias,
                                whole_total=constraint.get('whole_total', False)))
               for column, values in constraint['filters'].items()]
    if all(state == 'checked' for _, state, _ in results):
        return [('checked', f'已发布业务范围“{alias}”的 {len(results)} 个筛选字段均与登记值一致。')]
    return [(state, detail) for _, state, detail in results if state != 'checked']
