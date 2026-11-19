"""Bind a registered case-count ranking to its original grouping dimension."""
from datetime import date, timedelta
import re

from sqlglot import exp
import sqlglot

from .retrieval import normalize
from .row_count_aggregation import _PRIOR_SCOPE, _SCOPE_CHANGE, _group_names, _group_window, named_scope, subgroup_names, validate

KIND = 'raw_dimension_grouping'
_FIRST = r'(?:排(?:在|名)?|位列|位居)(?:第[一1]|首位)|居首'
_RANK = re.compile(r'最多|最少|最高|最低|最大|最小|排名|排行|\btop\s*\d+\b|' + _FIRST)
_COUNT = re.compile(r'数量|数目|件数|记录数|案卷数|批准量|办理量|多少|\bcount\b')
_MERGE = re.compile(r'大类|归并|合并|重分类|重分组|归为|分成.{0,4}类')
_ALL = re.compile(r'(?:改查|统计|查询|按|看)?\s*(?:所有|全部|全量)(?:业务|审批|事项|案卷|记录)|不限(?:业务|审批|事项|类别)|取消.{0,8}(?:事项|业务).{0,6}(?:筛选|限制)')
_OTHER_MEASURE = re.compile(r'去重|预测|同比|环比|平均|中位|金额|比率|通过率|占比|比例|对比|比较')
_MIXED = re.compile(r'另(?:外|查)|顺便|以及|分别|同时(?:查询|统计|列出)|再(?:查询|统计)|[。；;！!？?\n].+\S')
_PERIOD = re.compile(r'年度|本年|今年|当年|去年|上年|前年|累计|\d{4}年|\d{1,2}月')
_SUBSET = re.compile(r'(?:仅|只)(?:看|查|统计)|排除|不包括|剔除|不含')

_WHICH = r'哪(?:一)?(?:个|类|种|项|些)'
_RANK_END = re.compile(_WHICH + r'[^。！？?!；;\n]{1,65}?(最多|最高|最少|最低|' + _FIRST + r')[。！？?!\s]*$')
_DIRECT_OTHER = re.compile(r'另(?:外|查)|同时|分别|以及|并且|(?:并|还)(?:统计|查|比较)|去重|按(?:年|季|月|周|天)'
                           r'|每个?月|月度|各月|本月|当月|上月|前\s*[一二两三四五六七八九十\d]+(?:名|位|项|类)'
                           r'|等于|大于|小于|不少于|至少|阈值|名单|明细|导出|下载')
_EXPLICIT_SOURCE = re.compile(r'数据源|数据库|数据集|数据表|表名|库[里中]')
_UNREGISTERED_REGION = re.compile(r'全国|省外|外省|跨省|全[省市区县]|[\u4e00-\u9fff]{2,8}(?:省|自治区|市|县)(?!级|份|域|场|会)')


def _fixed_window(text, host):
    from .monthly_sql_scope import _NARROW, _RELATIVE_YEAR, _YEAR

    if window := _group_window(text, host):
        (first, last), start, stop = window
        if not any(pattern.search(text[:first] + ' ' + text[last:]) for pattern in (_NARROW, _RELATIVE_YEAR, _YEAR)):
            return start, stop


def _candidate_specs(source):
    if not (source.get('business_project') or {}).get('version'):
        return
    sid = source['id']
    for term in source.get('terms', []):
        spec = term.get('config') or {}
        if (spec.get('kind') != 'row-count-aggregation' or spec.get('source_id') != sid
                or term.get('source_id', sid) != sid or term.get('status') in ('draft', 'review')
                or spec.get('source_revision') != source.get('target_revision', 0)):
            continue
        try:
            validate(spec, source)
        except ValueError:
            continue
        yield term, spec


def _labels(source, spec):
    table = next(t for t in source['tables'] if t['name'] == spec['table'])
    field = next(c for c in table['columns'] if c['name'] == spec['dimension'])
    grouping = spec.get('published_grouping') or {}
    names = [spec['dimension'], *spec.get('detail_dimension_aliases', []),
             field.get('label'), *field.get('aliases', [])]
    # Request labels can also name the measure (e.g. an event count). Only
    # categorical wording helps resolve the grouping dimension.
    names.extend(label for label in grouping.get('request_labels', [])
                 if re.search(r'领域|类别|类型|用途|行业|渠道|专业|方向|维度|地区|等级', label))
    return {normalize(name) for name in names if isinstance(name, str) and len(name.strip()) >= 2}


def _named_rank(text, labels):
    for label in labels:
        words = {label}
        if label.endswith('应用领域'):
            words.add('领域')  # A shorthand only for an actually registered domain dimension.
        for word in words:
            if re.search(r'(?:' + _WHICH + r'|按(?:照)?|各|每)\s*(?:个|项)?\s*'
                         r'(?:(?:具体|细分|原始)\s*(?:的\s*)?)?' + re.escape(word), text):
                return True
    return False


def _prior_population(engine, ctx, source, candidates):
    """Match the actual previous count's complete predicates, never a label guess."""
    if engine is None or ctx is None:
        return None, None
    from .row_count_aggregation import _previous_group_input, _matches
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name
    prior = _previous_group_input(engine, ctx, source)
    if not prior:
        return None, None
    ref, _, sql, _ = prior
    dialect = sqlglot_name(source['dialect'])
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None, None
    tables = {_physical_table(item, source, dialect) for item in tree.find_all(exp.Table)}
    if not any(item[1]['table'] in tables for item in candidates):
        return None, None
    if (isinstance(tree, exp.Select) and len(tree.expressions) == 1 and _is_count(tree.expressions[0])
            and not tree.args.get('group')):
        # A scalar COUNT has the same population predicates as its grouped
        # form. Normalize syntax only to share the strict predicate validator;
        # no rows or evidence are manufactured by this transformation.
        normalized = []
        for item in candidates:
            spec = item[1]
            shape = tree.copy()
            shape.set('expressions', [exp.column(spec['dimension'], quoted=True), tree.expressions[0].copy()])
            shape.set('group', exp.Group(expressions=[exp.column(spec['dimension'], quoted=True)]))
            if _matches(shape, source, spec, None, exact_filters=True):
                normalized.append(item)
        matches = normalized
    else:
        matches = [item for item in candidates if any(
            _matches(tree, source, item[1], None, exact_filters=True, monthly_total=monthly)
            for monthly in (False, True))]
    return ref, matches[0] if len(matches) == 1 else None


def derive(source, host, reference, published_scope=None, *, engine=None, ctx=None):
    text = normalize(host.get('user_text')).strip().rstrip('。！？!?')
    if not text or _MIXED.search(text) or _OTHER_MEASURE.search(text):
        return None
    if not published_scope and _SCOPE_CHANGE.search(text):
        return None  # A changed/negated population belongs to the normal scope resolver.
    if published_scope and published_scope.get('geographic_refined'):
        return None  # The normal geographic resolver owns the explicitly changed slice.
    selected = named_scope(source, text)
    selected_id = selected[0]['config']['id'] if selected else None
    ranked = bool(_RANK.search(text) and _COUNT.search(text))
    specs = list(_candidate_specs(source))
    if not specs:
        return None
    labels_by_field = {}
    for _, spec in specs:
        key = spec['table'], spec['dimension']
        labels_by_field.setdefault(key, set()).update(_labels(source, spec))
    candidates = []
    for term, spec in specs:
        labels = labels_by_field[spec['table'], spec['dimension']]
        grouping = spec.get('published_grouping') or {}
        merge = (bool(_MERGE.search(text)) and any(label in text for label in labels)
                 or bool(grouping and _group_names(text, grouping)))
        if not merge and not (ranked and _named_rank(text, labels)):
            continue
        named = bool((published_scope and published_scope['scope_id'] == spec['id']) or selected_id == spec['id'])
        # Reaching here already proves the request names this recipe's own dimension
        # (or its published category wording), and the recipe is anchored to one
        # authorized table. Relevance is therefore established by the user's words,
        # not by how many other recipes the source happens to publish.
        candidates.append((term, spec, named, merge))
    if len({(s['table'], s['dimension']) for _, s, _, _ in candidates}) != 1:
        return None
    if _SUBSET.search(text) or any(any(part in text for part in subgroup_names(spec)) for _, spec, _, _ in candidates):
        return None  # Explicit subpopulation edits belong to the normal scope resolver.
    window = _fixed_window(text, host) if ranked and not _MERGE.search(text) else None
    previous_ref = None
    if any(named for _, _, named, _ in candidates):
        candidates = [item for item in candidates if item[2]]
    elif _ALL.search(text) or window and not _PRIOR_SCOPE.search(text):
        candidates = [item for item in candidates if not item[1].get('scope_aliases')]
    else:
        previous_ref, inherited = _prior_population(engine, ctx, source, candidates)
        if previous_ref:
            if not inherited:
                return None  # An unmatched prior population cannot justify a narrower default.
            candidates = [inherited]
        else:
            # An aliased recipe is a subset, not a default for every question
            # over the same table. Use an unscoped definition, or leave a new
            # population to the normal authorized SQL route.
            candidates = [item for item in candidates if not item[1].get('scope_aliases')]
    if len(candidates) != 1:
        return None
    _, spec, _, merge = candidates[0]
    if merge:
        window = None
    return {'kind': KIND, 'source_id': source['id'], 'scope_id': spec['id'], 'table': spec['table'],
            'column': spec['dimension'], 'time_column': spec['time_column'], 'filters': spec['filters'],
            'unit': spec['unit'], 'basis': spec['basis'], 'request': 'published_or_derived' if merge else 'raw_ranking',
            'requires_period': bool(_PERIOD.search(text) or window),
            **({'window': [day.isoformat() for day in window]} if window else {}),
            'evidence': reference, **({'source_result_ref': previous_ref} if previous_ref else {})}


def period_ranking_query(engine, ctx, source):
    """Offer one executable query only after the request's ranking scope is checked."""
    from .monthly_sql_scope import _annual_aggregate_window, _annual_year
    from .request_scope import derive as derive_scope, inspect_sql
    from .row_count_aggregation import _group_dialect, _population_sql
    from .source_choice import mentioned_sources

    host = ctx.host_evidence or {}
    quote = host.get('user_text') or ''
    text = normalize(quote).strip()
    publication = source.get('business_project') or {}
    if (not ctx.session_id or not host.get('user_ref') or host.get('clarifications')
            or not publication.get('id') or not publication.get('version')
            or not _COUNT.search(text) or not (rank := _RANK_END.search(text))
            or _DIRECT_OTHER.search(text) or _SCOPE_CHANGE.search(text)
            or _EXPLICIT_SOURCE.search(text) or mentioned_sources(text, [source])):
        return None
    fixed = _fixed_window(text, host)
    annual = _annual_year(host, text) if not fixed else None
    if not (fixed or annual):
        return None
    scope = derive_scope(source, host, engine=engine, ctx=ctx)
    ranks = [c for c in scope['constraints'] if c['kind'] == KIND and c['request'] == 'raw_ranking']
    if len(ranks) != 1:
        return None
    constraint, = ranks
    # Explicit geography or a narrower value has to go through the normal
    # query path unless its value is already inside the checked population.
    without_values = text
    for values in constraint['filters'].values():
        if len(values) == 1:
            without_values = without_values.replace(normalize(values[0]), '')
    if _UNREGISTERED_REGION.search(without_values):
        return None
    if not (dialect := _group_dialect(source, constraint)):
        return None
    spec = next(spec for _, spec in _candidate_specs(source) if spec['id'] == constraint['scope_id']
                and spec['table'] == constraint['table'] and spec['dimension'] == constraint['column'])
    names = sorted(_labels(source, spec), key=lambda label: (-len(label), label))
    label = next((name for name in names if name in text and 2 <= len(name) <= 45), '分类')
    count_label = '数量（' + spec['unit'] + '）'
    if count_label == label:
        return None
    ident = lambda value: exp.to_identifier(value, quoted=True).sql(dialect=dialect)
    start, stop = fixed or (date(annual[0], 1, 1), date(annual[0] + 1, 1, 1))
    order = 'ASC' if rank[1] in ('最少', '最低') else 'DESC'
    sql = (f'SELECT {ident(spec["dimension"])} AS {ident(label)}, COUNT(1) AS {ident(count_label)} '
           + _population_sql(spec, start, stop, dialect) + f' GROUP BY 1 ORDER BY 2 {order}, 1 ASC')
    try:
        validation = inspect_sql(sql, source, scope, engine=engine, ctx=ctx) if fixed or _annual_aggregate_window(host, sql, source) else None
    except (ValueError, sqlglot.errors.SqlglotError):
        return None
    if (not validation or validation['state'] == 'conflict'
            or not any(item['state'] == 'checked' and item.get('constraint') == constraint for item in validation['checks'])):
        return None
    title_period = f'{start:%Y-%m}至{stop - timedelta(days=1):%Y-%m}' if fixed else f'{start.year} 年'
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': quote,
            'session_id': ctx.session_id, 'source_id': source['id'],
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'title': f'{title_period}{label}数量排名',
                              'sql': sql, 'purpose': 'answer'}}


def _is_count(projection):
    count = projection.unalias().unnest()
    if not isinstance(count, exp.Count) or count.expressions:
        return False
    return ((isinstance(count.this, exp.Star) and not any(count.this.args.values()))
            or count.this == exp.Literal.number(1))


def _month_window_projection(item, column, time_column):
    from .monthly_sql_scope import _month_field

    node = item.unalias().unnest()
    return (isinstance(node, (exp.Min, exp.Max)) and not node.expressions
            and (column(node.this.unnest()) == time_column or column(_month_field(node.this.unnest())) == time_column))


def _raw_or_empty(node, column, name):
    node = node.unnest()
    if column(node) == name:
        return True

    def empty_test(condition):
        condition = condition.unnest()
        if isinstance(condition, exp.Or):
            return empty_test(condition.this) and empty_test(condition.expression)
        if isinstance(condition, exp.Is) and isinstance(condition.expression, exp.Null):
            return column(condition.this.unnest()) == name
        if isinstance(condition, exp.EQ) and condition.expression == exp.Literal.string(''):
            operand = condition.this.unnest()
            if isinstance(operand, exp.Trim) and not any(value for key, value in operand.args.items() if key != 'this'):
                operand = operand.this.unnest()
            return column(operand) == name
        return False

    def blank_marker(label):
        return (isinstance(label, exp.Literal) and label.is_string
                and bool(re.fullmatch(r'(?:未注明|未填写|未分类|未知|缺失|空值|无数据)(?:[（(].{1,12}[）)])?', label.this)))

    if isinstance(node, exp.Coalesce) and len(node.expressions) == 1 and blank_marker(node.expressions[0]):
        inner = node.this.unnest()
        return column(inner) == name or (isinstance(inner, exp.Nullif)
               and inner.expression == exp.Literal.string('') and column(inner.this.unnest()) == name)
    if isinstance(node, exp.Case) and node.this is None and column(node.args.get('default')) == name:
        branches = node.args.get('ifs') or []
        return bool(branches) and all(empty_test(branch.this) and blank_marker(branch.args.get('true')) for branch in branches)
    return False


def inspect(tree, source, constraint, published_grouping=None):
    from .published_scope import _condition, _conjuncts, _membership
    from .row_count_aggregation import _date
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source['dialect'])
    table, target = constraint['table'], constraint['column']
    if constraint['source_id'] != source['id']:
        return None
    matching = any(_physical_table(ref, source, dialect) == table for ref in tree.find_all(exp.Table))
    if constraint['request'] == 'published_or_derived':
        if not matching:
            return None
        if published_grouping:
            return None  # The executor matched the complete published recipe before this check.
        return 'conflict', '本轮要求重新归类。请采用已发布分组，或对已取得的原始分组结果调用 data_derive_aggregation，按完整映射确认后汇总。不得在 SQL 中自拟归并。'

    required = ('本轮要求按已发布维度“' + target + '”的原始值计数排名。请按该原始字段直接分组，保留全部原值和并列；'
                '使用已发布总体筛选 ' + '、'.join(_condition(name, values) for name, values in constraint['filters'].items()) + '。')
    if constraint['requires_period']:
        required += '保留原问的起始日（含）和结束边界（不含）。SQL窗口与数据覆盖是两回事，只能如实报告已核实的观测范围。'
    allowed = {'expressions', 'from', 'from_', 'where', 'group', 'order'}
    if (not isinstance(tree, exp.Select) or any(value and key not in allowed for key, value in tree.args.items())
            or not 2 <= len(tree.expressions) <= 4 or not (group := tree.args.get('group'))
            or len(group.expressions) != 1 or any(value for key, value in group.args.items() if key != 'expressions')):
        return 'conflict', required
    relation = tree.args.get('from') or tree.args.get('from_')
    ref = relation.this if relation else None
    if (_physical_table(ref, source, dialect) != table or len(list(tree.find_all(exp.Table))) != 1
            or tree.find(exp.Subquery, exp.Window, exp.Filter)
            or any(value and key not in ('this', 'db', 'catalog', 'alias') for key, value in ref.args.items())):
        return 'conflict', required

    columns = {col['name'] for item in source['tables'] if item['name'] == table for col in item['columns']}

    def column(node):
        if (isinstance(node, exp.Column) and node.name in columns and not (node.db or node.catalog)
                and (not node.table or node.table == ref.alias_or_name)):
            return node.name
        return None

    values = [(index, item) for index, item in enumerate(tree.expressions)
              if _raw_or_empty(item.unalias(), column, target)]
    if (sum(_is_count(item) for item in tree.expressions) != 1 or len(values) != 1
            or any(not (_is_count(item) or _raw_or_empty(item.unalias(), column, target)
                        or _month_window_projection(item, column, constraint['time_column'])) for item in tree.expressions)):
        return 'conflict', required
    (index, projection), = values
    key, = group.expressions
    if key == exp.Literal.number(index + 1) or (isinstance(key, exp.Column) and not key.table
            and key.name not in columns and key.name == projection.alias):
        key = projection.unalias()
    projected = projection.unalias().unnest()
    if not _raw_or_empty(projected, column, target) or not (key.unnest() == projected or column(key.unnest()) == column(projected) == target):
        return 'conflict', required

    where = tree.args.get('where')
    filters, bounds = {}, {}
    for atom in _conjuncts(where.this if where else None):
        if (column(atom.this) == constraint['time_column'] and isinstance(atom, (exp.GTE, exp.LT))
                and (day := _date(atom.expression)) and type(atom) not in bounds):
            bounds[type(atom)] = day.isoformat()
            continue  # The normal period validator owns the date window.
        match = _membership(atom, column)
        if not match or match[0] != 'set' or match[1] not in constraint['filters'] or match[1] in filters:
            return 'conflict', required
        filters[match[1]] = match[2]
    if filters != {name: set(values) for name, values in constraint['filters'].items()}:
        return 'conflict', required
    if constraint['requires_period'] and set(bounds) != {exp.GTE, exp.LT}:
        return 'conflict', required
    if constraint.get('window') and constraint['window'] != [bounds.get(exp.GTE), bounds.get(exp.LT)]:
        return 'conflict', required
    return 'checked', '已核对原始分组、已发布总体筛选及保留全部并列。SQL 日期窗口不证明实际数据覆盖。'
