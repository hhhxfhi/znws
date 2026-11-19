"""Require a choice for administrator-declared ambiguous business terms.

Only host-owned human messages and native question receipts resolve a term.
Metric titles, tool arguments and model assertions are never confirmations.
"""
import re
from collections import defaultdict


_MIXED_SCOPE = re.compile(r'[\n。；;]|分别|各自|同时(?:查|算|统计)|另外|另查|再(?:查|算|统计)|并(?:查|列|计算|分析|统计)|对比|比较|以及')
_DIMENSION = re.compile(r'(?:哪个|哪种|哪一类|按)\s*([^，,。；！？\n]{2,14}?)(?:的|分组|分类|排名|统计|最多|最少)')
_BUSINESS_COUNT = re.compile(r'^(?:本年度|本年|当月|月末|近三年|近两年|上年度|上年|全年|本月|上月)?'
                             r'([\u4e00-\u9fff]{2,20}?)(?:业务)?(?:累计)?(?:批准|办理|受理)?'
                             r'(?:案卷|记录)(?:数量|总量|件数|总数|数)(?:[_（(].*)?$')
_FIELD_LABEL = re.compile(r'(?:^|[。；;，,、]|且|按|其中)([\u4e00-\u9fff]{2,12})[（(]([A-Za-z_][A-Za-z_0-9]*)[）)]')
_KNOWN_DIMENSION = re.compile(r'形式|类型|类别|领域|用途|事项|状态|专业|地域|地区|地市|机构|等级')
_EMPTY_LABEL = re.compile(r'(?:分类)?(?:未注明|未填写|未登记|未分类|未知|缺失|不详|空值)')
_EXPLICIT_EMPTY = re.compile(r'空值|空白|留空|未注明|未填写|缺失|未知|其他|归并|合并')
_CASE_KEY_LABEL = re.compile(r'案卷(?:号|编号|标识|唯一标识)|case_(?:key|id|no|number)', re.I)
_CASE_QUANTITY = re.compile(r'案卷(?:的)?(?:数量|件数|数|最多|最少|有多少)')


def _physical_select(sql, source):
    """Return the one authorized physical table and scope of a direct SELECT."""
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source.get('dialect'))
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = normalize_identifiers(statements[0], dialect=dialect)
    if not isinstance(tree, exp.Select) or tree.find(exp.Join, exp.Subquery, exp.CTE, exp.SetOperation):
        return None
    refs = list(tree.find_all(exp.Table))
    if len(refs) != 1 or not (table := _physical_table(refs[0], source, dialect)):
        return None
    return tree, refs[0], table


def _column_name(node, relation):
    from sqlglot import exp
    if isinstance(node, exp.Column) and (not node.table or node.table == relation.alias_or_name) and not (node.db or node.catalog):
        return node.name
    return None


def _is_row_count(projection):
    from sqlglot import exp
    expression = projection.unalias().unnest()
    return isinstance(expression, exp.Count) and (isinstance(expression.this, exp.Star)
            or isinstance(expression.this, exp.Literal) and expression.this.is_int and expression.this.this == '1')


def _case_key(table, source):
    declared = {entry.get('column') for entry in source.get('business_dictionary', [])
                if entry.get('source_id') == source['id'] and entry.get('table') == table['name']
                and entry.get('kind') == 'field' and _CASE_KEY_LABEL.fullmatch(entry.get('name') or '')}
    for column in table['columns']:
        if any(_CASE_KEY_LABEL.fullmatch(label or '') for label in (column.get('label'), column.get('business_name'))):
            declared.add(column['name'])
    declared &= {column['name'] for column in table['columns']}
    if declared:
        return next(iter(declared)) if len(declared) == 1 else None
    key = table.get('primary_key') or []
    if len(key) == 1 and _CASE_KEY_LABEL.fullmatch(key[0]):
        return key[0]
    return None


def _counts_cases(projection, relation, key):
    from sqlglot import exp
    count = projection.unalias().unnest()
    return bool(key and isinstance(count, exp.Count) and isinstance(count.this, exp.Distinct)
                and len(count.this.expressions) == 1 and _column_name(count.this.expressions[0], relation) == key)


def _profiles(source):
    from sqlglot import exp
    from .catalog_view import entry_code
    sid = source['id']
    tables = {table['name']: table for table in source.get('tables', [])}
    profiles = {name: {'names': set(), 'dimensions': defaultdict(set)} for name in tables}
    for mapping in source.get('business_dictionary', []):
        name, label = mapping.get('table'), mapping.get('name', '')
        if mapping.get('source_id') != sid or name not in tables or not label:
            continue
        profile = profiles[name]
        if mapping.get('kind') == 'table' and re.fullmatch(r'[\u4e00-\u9fff]{2,20}(?:业务)?', label):
            profile['names'].add(label.removesuffix('业务'))
        if mapping.get('kind') == 'field' and _KNOWN_DIMENSION.search(label):
            if any(col['name'] == mapping.get('column') for col in tables[name].get('columns', [])):
                profile['dimensions'][label].add(mapping['column'])

    def key(item, title):
        if item.get('source_id') == sid and item.get('origin') and item.get('status') not in ('draft', 'review'):
            if code := entry_code(item, title):
                return item['origin'], code
        return None

    terms, examples = defaultdict(list), defaultdict(list)
    for item in source.get('terms', []):
        if found := key(item, 'name'):
            terms[found].append(item)
    for item in source.get('examples', []):
        if found := key(item, 'question'):
            examples[found].append(item)
    for pair in terms.keys() & examples.keys():
        if len(terms[pair]) != 1 or len(examples[pair]) != 1:
            continue
        term = terms[pair][0]
        label = re.sub(r'^\S+[\s:：]+', '', term['name'], count=1)
        if not (business := _BUSINESS_COUNT.fullmatch(label)):
            continue
        if not (select := _physical_select(examples[pair][0].get('logical_sql') or '', source)):
            continue
        tree, relation, table = select
        declared = term.get('tables')
        if declared and set(declared) != {table}:
            continue
        if len(tree.expressions) != 1 or not _is_row_count(tree.expressions[0]):
            continue
        profile = profiles[table]
        profile['names'].add(business[1])
        physical = {col['name'] for col in tables[table].get('columns', [])}
        where = tree.args.get('where')
        used = {_column_name(col, relation) for col in where.find_all(exp.Column)} if where else set()
        for dimension, field in _FIELD_LABEL.findall(term.get('definition') or ''):
            if field in physical & used and _KNOWN_DIMENSION.search(dimension):
                profile['dimensions'][dimension].add(field)
    return profiles


def dimension_business_choice(source, host):
    """Expose a narrow, current human scope conflict as a native question."""
    import hashlib
    import json
    host = host or {}
    text = (host.get('user_text') or '').strip().rstrip('。！？?!')
    request = host.get('user_ref')
    publication = source.get('business_project') or {}
    if (not request or not publication.get('version') or not text or _MIXED_SCOPE.search(text)
            or re.search(r'不(?:按|查|统计)|取消|改用|替代|改成', text)):
        return None
    dimensions = set(_DIMENSION.findall(text))
    if len(dimensions) != 1 or not _KNOWN_DIMENSION.search(dimension := next(iter(dimensions))):
        return None
    profiles = _profiles(source)
    hits = [(table, name) for table, profile in profiles.items() for name in profile['names'] if name in text]
    direct = {table for table, _ in hits}
    if len(direct) != 1:
        return None
    requested, = direct
    if dimension in profiles[requested]['dimensions']:
        return None
    matching = [table for table, profile in profiles.items() if table != requested
                and len(profile['dimensions'].get(dimension, ())) == 1 and profile['names']]
    if len(matching) != 1:
        return None
    target, = matching
    if any(mapping.get('kind') == 'relation' and mapping.get('source_id') == source['id']
           and {mapping.get('table'), mapping.get('to_table')} == {requested, target}
           for mapping in source.get('business_dictionary', [])):
        return None
    business = max((name for table, name in hits if table == requested), key=lambda name: (len(name), name))
    alternate = min(profiles[target]['names'], key=lambda name: (len(name), name))
    if business == alternate:
        return None
    choices = [{'label': alternate + '业务', 'description': '按已发布的“' + dimension + '”分组，保留原统计期间。',
                'table': target, 'column': next(iter(profiles[target]['dimensions'][dimension]))}]
    own_dimensions = [(label, columns) for label, columns in profiles[requested]['dimensions'].items() if len(columns) == 1]
    for label, columns in sorted(own_dimensions, key=lambda item: (len(item[0]), item[0]))[:2]:
        choices.append({'label': business + '业务（按' + label + '）',
                        'description': '按该业务的“' + label + '”分组，保留原统计期间。',
                        'table': requested, 'column': next(iter(columns))})
    if len(choices) == 1:
        return None
    sig = hashlib.sha256(json.dumps([request, text, source['id'], publication, choices], ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]
    question = f'“{dimension}”已登记在{alternate}业务。{business}业务未登记这一分组维度。您要选哪一项业务口径，{alternate}业务还是保留{business}业务？'
    return {'schema_version': 1, 'question_id': 'biz-dim-' + sig, 'request_ref': request, 'dimension': dimension,
            'requested_business': business,
            'publication': {'id': publication.get('id'), 'version': publication['version']},
            'connection_revision': source.get('connection_revision', 0),
            'question': question, 'header': '确认业务口径', 'options': choices, 'source_id': source['id'],
            'instruction': '将 question_id 用作 ask_user_question 的 id，原样提供 question 和全部选项的 label、description。收到真实选择后，用该选项指定的授权表和分组列查询。'}


def _dimension_selection(host, evidence):
    def label(text):
        return re.sub(r'\s*[（(]\s*(?:推荐|Recommended)\s*[）)]\s*$', '', (text or '').strip(), flags=re.I)
    for receipt in reversed(host.get('clarifications') or []):
        if (not receipt.get('call_id') or receipt.get('question_id') != evidence['question_id']
                or receipt.get('question') != evidence['question']):
            continue
        selected = receipt.get('selected_labels') or []
        if len(selected) > 1:
            return None
        picked = label(selected[0] if selected else receipt.get('answer'))
        return next((option for option in evidence['options'] if option['label'] == picked), None)
    return None


def _dimension_group(tree, relation, table, source, choice, *, count_cases):
    from sqlglot import exp
    if (table != choice['table'] or not (group := tree.args.get('group')) or len(group.expressions) != 1
            or any(group.args.get(key) for key in ('grouping_sets', 'cube', 'rollup'))):
        return None
    physical = next(t for t in source['tables'] if t['name'] == table)
    key = _case_key(physical, source) if count_cases else None
    if not any(_is_row_count(item) or _counts_cases(item, relation, key) for item in tree.expressions):
        return None
    item, = group.expressions
    if isinstance(item, exp.Literal) and item.is_int and 1 <= int(item.this) <= len(tree.expressions):
        return tree.expressions[int(item.this) - 1].unalias()
    if isinstance(item, exp.Column) and not item.table:
        columns = physical['columns']
        if not any(col['name'] == item.name for col in columns):
            aliases = [p.unalias() for p in tree.expressions if p.alias == item.name]
            if len(aliases) == 1:
                return aliases[0]
    return item


def _matches_dimension_query(sql, source, choice, *, count_cases):
    from sqlglot import exp
    if not sql or not (result := _physical_select(sql, source)):
        return False
    tree, relation, table = result
    if (item := _dimension_group(tree, relation, table, source, choice, count_cases=count_cases)) is None:
        return False
    column = choice['column']

    def is_dimension(item):
        item = item.unnest()
        if _column_name(item, relation) == column:
            return True
        # Allow labeling blanks without regrouping different non-empty values.
        if (not isinstance(item, exp.Coalesce) or len(item.expressions) != 1
                or not isinstance(item.expressions[0], exp.Literal) or not item.expressions[0].is_string):
            return False
        blank = item.this
        return (isinstance(blank, exp.Nullif) and isinstance(blank.expression, exp.Literal)
                and blank.expression.is_string and blank.expression.this == ''
                and _column_name(blank.this, relation) == column)

    return is_dimension(item) and any(item == p.unalias() for p in tree.expressions)


def canonicalize_confirmed_dimension_sql(sql, source, host):
    """Keep the selected business, requested period and raw dimension labels."""
    from sqlglot import exp
    from .vendor.dialect import sqlglot_name

    if not sql:
        return sql
    evidence = dimension_business_choice(source, host)
    if not evidence or not (choice := _dimension_selection(host, evidence)):
        return sql
    if not (result := _physical_select(sql, source)):
        return sql
    tree, relation, table = result
    count_cases = bool(_CASE_QUANTITY.search((host or {}).get('user_text') or ''))
    item = _dimension_group(tree, relation, table, source, choice, count_cases=count_cases)
    allowed = {'expressions', 'from', 'from_', 'where', 'group', 'order'}
    changed = False
    if _matches_dimension_query(sql, source, choice, count_cases=count_cases) or isinstance(item, exp.Coalesce):
        from .approval_event_scope import bind_annual_group_window
        physical = next(candidate for candidate in source['tables'] if candidate['name'] == table)
        changed = bind_annual_group_window(tree, physical, relation, source, host)
    normalized = tree.sql(dialect=sqlglot_name(source['dialect'])) if changed else sql
    if _EXPLICIT_EMPTY.search((host or {}).get('user_text') or ''):
        return normalized
    if (len(tree.expressions) != 2 or any(value and key not in allowed for key, value in tree.args.items())
            or not isinstance(item, exp.Coalesce) or len(item.expressions) != 1
            or not isinstance(marker := item.expressions[0], exp.Literal) or not marker.is_string
            or not _EMPTY_LABEL.fullmatch(marker.this.removeprefix(evidence['dimension']))):
        return normalized
    column = item.this
    if isinstance(column, exp.Nullif):
        if column.expression != exp.Literal.string(''):
            return normalized
        column = column.this
        if isinstance(column, exp.Trim) and not any(value for key, value in column.args.items() if key != 'this'):
            column = column.this
    if _column_name(column, relation) != choice['column']:
        return normalized
    # The user selected a grouping dimension. A display placeholder may
    # collide with a real label, and TRIM may collapse two different labels.
    # Preserve every value. Explicit blank-handling requests stay untouched.
    projection = next((p for p in tree.expressions if isinstance(p, exp.Alias) and p.this == item), None)
    if projection is None:
        return normalized
    original = item.copy()
    projection.set('this', column.copy())
    key, = tree.args['group'].expressions
    if key == original:
        key.replace(column.copy())
    if order := tree.args.get('order'):
        for key in order.expressions:
            if key.this == original:
                key.set('this', column.copy())
    unmerged = tree.sql(dialect=sqlglot_name(source['dialect']))
    return unmerged if _matches_dimension_query(unmerged, source, choice, count_cases=count_cases) else normalized


def _validate_forecast_business(sql, source, host):
    """Reject a competing published business when the current history names one."""
    from natural_analysis.forecasting import relative_month_window, target_mentions
    from .monthly_sql_scope import _grouped_month
    from .vendor.dialect import sqlglot_name

    text = (host.get('user_text') or '').strip().rstrip('。！？?!')
    if (not sql or not text or not (source.get('business_project') or {}).get('version')
            or not host.get('user_ref') or _MIXED_SCOPE.search(text)
            or not (mentions := target_mentions(host)) or not relative_month_window(host, mentions)
            or not (result := _physical_select(sql, source))):
        return
    tree, _, table = result
    dialect = sqlglot_name(source['dialect'])
    if (len(tree.expressions) != 2 or not any(_is_row_count(item) for item in tree.expressions)
            or not _grouped_month(tree, monthly_only=True, alias_group=True, dialect=dialect)):
        return
    profiles = _profiles(source)
    named = {name: {alias for alias in profile['names'] if alias in text} for name, profile in profiles.items()}
    matches = [(name, max(aliases, key=lambda alias: (len(alias), alias))) for name, aliases in named.items() if aliases]
    if len(matches) != 1 or not profiles[table]['names']:
        return
    requested, label = matches[0]
    if requested != table:
        raise ValueError(f'METRIC_SUBJECT: 本轮预测的历史业务为“{label}”，当前 SQL 使用了其他已发布业务表。'
                         f'请按当前原问重新读取 data_catalog，再使用授权表 {requested}，核实该业务的时间字段与筛选，'
                         '按原训练窗口查询月度实际观测。')


def validate_rule(rule):
    def words(values, minimum=0):
        return isinstance(values,list) and minimum <= len(values) <= 20 and all(isinstance(s,str) and 0 < len(s.strip()) <= 120 for s in values)
    if not isinstance(rule,dict) or not words(rule.get('triggers'),1):
        raise ValueError('术语澄清规则需要非空 triggers 列表。')
    choices=rule.get('choices')
    if not isinstance(choices,list) or not 2 <= len(choices) <= 20:
        raise ValueError('术语澄清规则需要 2 至 20 个 choices。')
    for choice in choices:
        if not isinstance(choice,dict) or not words([choice.get('label')],1) or any(not words(choice[k]) for k in ('aliases','metric_ids') if k in choice):
            raise ValueError('澄清选项需要 label，可选 aliases 与 metric_ids 必须是文本列表。')
    if len({c['label'] for c in choices}) != len(choices):
        raise ValueError('澄清选项名称不能重复。')


def matching_rules(source, text):
    for term in source.get('terms') or []:
        rule = term.get('clarification') or term.get('pending_clarification')
        if not isinstance(rule, dict):
            continue
        triggers = rule.get('triggers') or []
        choices = rule.get('choices') or []
        if len(choices) >= 2 and any(isinstance(t, str) and t and t in text for t in triggers):
            yield term, rule


_TOTAL = re.compile(r'累计|累积|合计|总计|总(?:额|量|数|金额|次数)')
_MEAN = re.compile(r'平均|均值|(?:日|周|月|季|年|人|户|台)均')


def _explicit_aggregation(text):
    total, mean = bool(_TOTAL.search(text)), bool(_MEAN.search(text))
    if total == mean:
        return None
    return 'total' if total else 'mean'


def _declared_aggregation(metric):
    operands = metric['operands']
    if len(operands) != 1 or metric['formula'].strip() != operands[0]['id']:
        return None
    aggregation = operands[0]['aggregation']
    executable = {'sum': 'total', 'count': 'total', 'distinct': 'total', 'avg': 'mean'}.get(aggregation)
    # A stored rate or an indirect formula may legitimately use another reducer.
    declared = _explicit_aggregation(metric['definition'])
    return executable if declared == executable else None


def reference_issues(source):
    if not source.get('id'):
        return []  # Pure wording checks may not carry a source catalog.
    from .onboarding import metric_catalog
    from .metric_registry import resolve_mapping
    from .metrics import METRICS
    semantic_modes = defaultdict(set)
    for metric in metric_catalog(source)[0]:
        semantic_modes[metric['id']].add(_declared_aggregation(metric))
    legacy = set()
    try:
        _,mapping=resolve_mapping(source)
        authorized={t['name'] for t in source.get('tables',[])}
        legacy.update(k for k,v in mapping.get('metrics',METRICS).items() if v[1] in authorized)
    except ValueError:pass
    available = set(semantic_modes) | legacy
    issues=[]
    for term in source.get('terms',[]):
        for choice in (term.get('clarification') or {}).get('choices',[]):
            missing=set(choice.get('metric_ids',[]))-available
            if missing:issues.append({'kind':'unreachable_metric','term_id':term.get('id'),'name':term.get('name'),
                'choice':choice['label'],'metric_ids':sorted(missing),'source_id':source['id'],
                'message':'澄清选项引用的指标不在本来源的已授权有效目录中，请修正来源或指标配置。'})
            for label in dict.fromkeys([choice['label'], *choice.get('aliases', [])]):
                expected = _explicit_aggregation(label)
                if expected is None:
                    continue
                opposite = 'mean' if expected == 'total' else 'total'
                invalid = sorted(metric_id for metric_id in choice.get('metric_ids', [])
                                 if metric_id not in legacy and semantic_modes.get(metric_id) == {opposite})
                if invalid:
                    issues.append({'kind': 'clarification_aggregation_mismatch', 'term_id': term.get('id'),
                                   'name': term.get('name'), 'choice': label, 'metric_ids': invalid, 'source_id': source['id'],
                                   'message': f'澄清选项“{label}”与绑定指标的累计或平均统计方式不一致，请修正绑定。'})
    return issues


def _choice(text, choices):
    # A native option answer is the option label, possibly with "（推荐）" or a
    # parenthesized description. That exact selection needs no interpretation.
    heading = re.sub(r'\s*[（(][^）)]*[）)]\s*$', '', re.sub(r'\s*[（(]\s*(?:推荐|Recommended)\s*[）)]', '', text.strip(), flags=re.I))
    exact = [c for c in choices if heading in (c['label'], *c.get('aliases', []))]
    if len(exact) == 1:
        return exact[0]
    # A refusal, question or two alternatives is not a selection.
    if re.search(r'不确定|不确认|不选|不要|不是|不按|暂不|还是|或者|[?？]', text):
        return None
    # "在线服务次数" also contains "服务次数". An occurrence lying inside a
    # longer occurrence of another choice is not a second selection.
    spans = [(m.start(), m.start() + len(alias), n) for n, c in enumerate(choices)
             for alias in {c['label'], *c.get('aliases', [])} if alias
             for m in re.finditer(re.escape(alias), text)]
    selected = {n for start, end, n in spans
                if not any(o != n and s <= start and end <= e and e - s > end - start for s, e, o in spans)}
    return choices[selected.pop()] if len(selected) == 1 else None


def validate_business_choice(source, host, metric_id=None, *, sql=None):
    host = host or {}
    text = host.get('user_text') or ''
    selected_configured_choice = False
    for term, rule in matching_rules(source, text):
        if term.get('pending_clarification') and not term.get('clarification'):
            raise ValueError('CONFIGURATION_REFERENCE_GAP: 术语“'+term['name']+'”的选项尚未生效：'+term.get('clarification_review_reason','请先配置可用指标。')+' 不要反复澄清或使用相近指标代答。')
        broken=[i for i in reference_issues(source) if i['term_id']==term.get('id')]
        if broken:
            if broken[0]['kind'] == 'clarification_aggregation_mismatch':
                raise ValueError('CONFIGURATION_REFERENCE_GAP: '+broken[0]['message'])
            raise ValueError('CONFIGURATION_REFERENCE_GAP: 术语“'+term['name']+'”的澄清选项引用了当前来源不可用的指标。请管理员在配置诊断中修复引用；不要重复澄清或改用相近指标代答。')
        choices = rule['choices']
        selected = _choice(text, choices)
        for record in reversed(host.get('clarifications') or []):
            question = record.get('question') or ''
            labels = [term.get('name', ''), *rule['triggers'], *[c['label'] for c in choices]]
            if any(label and label in question for label in labels):
                candidate = _choice(record.get('answer') or '', choices)
                if candidate or re.search(r'口径|指标|选择|哪一项|哪种|含义|是指|指的是',question):
                    selected = candidate
                    break
                # A later year, date or region answer does not revoke a
                # previous metric choice just because the question names it.
        if not selected:
            raise ValueError('NEEDS_CLARIFICATION: 已发布术语“' + term['name'] + '”中的“' +
                             '、'.join(rule['triggers']) + '”对应不同统计口径：' +
                             '；'.join(c['label'] for c in choices) + '。请通过 ask_user_question 让用户选择，再按实际选择查询；不得自行将概括用语替换为某个指标。')
        selected_configured_choice = True
        if metric_id and 'metric_ids' in selected and metric_id not in selected['metric_ids']:
            raise ValueError('METRIC_SUBJECT: 用户已确认“' + selected['label'] + '”，所选指标不符合该口径；请使用已发布指标 ' +
                             ('、'.join(selected['metric_ids']) if selected['metric_ids'] else '目录核对；本选项没有声明可替代的指标，应按授权字段查询') + '，无需重复确认。')
    if evidence := dimension_business_choice(source, host):
        if not (selected := _dimension_selection(host, evidence)):
            choices = '；'.join(option['label'] + '（' + option['description'] + '）' for option in evidence['options'])
            raise ValueError('NEEDS_CLARIFICATION: ' + evidence['question'] + ' ' + choices
                             + '。请用当前来源目录的 dimension_business_choice 原样调用 ask_user_question。问题 ID '
                             + evidence['question_id'] + '。')
        count_cases = bool(_CASE_QUANTITY.search(text))
        if not _matches_dimension_query(sql, source, selected, count_cases=count_cases):
            table = next(item for item in source['tables'] if item['name'] == selected['table'])
            key = _case_key(table, source) if count_cases else None
            measure = '按已登记案卷键 '+key+' 去重计数' if key else '行计数'
            raise ValueError('METRIC_SUBJECT: 已确认“' + selected['label'] + '”。按同一来源的授权表 '
                             + selected['table'] + '，在 SELECT 和 GROUP BY 中直接使用原字段 ' + selected['column']
                             + '，' + measure + '，保持原期间和筛选。分组保留原始标签及空值，无需重新确认。')
    elif not selected_configured_choice:
        _validate_forecast_business(sql, source, host)
