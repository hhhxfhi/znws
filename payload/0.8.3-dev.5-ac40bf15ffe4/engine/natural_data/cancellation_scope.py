"""Bind cancellation counts/rankings to verifiable completion events.

The authorized catalog identifies the case, specialty, and event roles.
An actual cancellation needs an explicit cancellation outcome and its date.
Published application definitions can support an application ranking.
"""
import re
import unicodedata
from datetime import date, timedelta

from sqlglot import exp, parse_one
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.scope import Scope, traverse_scope

from .snapshot_scope import _physical_table
from .vendor.dialect import sqlglot_name

KIND = 'cancellation_event'

_COUNT_INTENT = re.compile(r'数量|件数|次数|案卷数|多少|最多|最少|最高|最低|排名|排行|占比|分布|统计')
_SPECIALTY_CONTEXT = re.compile(r'专业|类别|资质')
_EXPLICIT_APP = re.compile(r'申请注销|注销申请')
# Historical snapshot / holding questions use cancellation only as an exclusion.
_SNAPSHOT_GUARD = re.compile(r'排除[^。；\n]{0,14}注销|注销[^。；\n]{0,10}(?:排除|剔除|不计)|'
                             r'(?:有效|持证|持有|在册|存量)[^。；\n]{0,12}(?:单位|资质|数量|多少|几家)|'
                             r'持有率|截至|期末')
_EVENT_VALUE = re.compile(r'^(?:申请注销|注销申请)$')
_COMPLETED_LABEL = re.compile(r'已注销|注销完成|注销生效|同意注销')
_CASE_KEY = re.compile(r'^(?:案卷号|案卷标识|案卷编号|记录唯一标识|业务唯一标识)$')
_SPECIALTY_COLUMN = re.compile(r'^(?:专业类别|资质专业|专业范围|测绘专业|专业分类)$')
_EVENT_DATE = re.compile(r'办理日期|办结日期|注销日期|注销完成日期|完成日期')
_INTAKE_DATE = re.compile(r'受理日期|受理时间')
_COLONS = ('：', ':')
_EXPANDERS = {'json_each', 'jsonb_array_elements_text', 'regexp_split_to_table',
              'string_to_table', 'unnest'}
_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')


def _labels(column):
    return [n for n in (column.get('label'), column.get('business_name')) if n]


def _intent(text):
    text = unicodedata.normalize('NFKC', text or '')
    if '注销' not in text or not _COUNT_INTENT.search(text) or not _SPECIALTY_CONTEXT.search(text):
        return None
    if _SNAPSHOT_GUARD.search(text):
        return None
    return text


def _published_application_roles(source, tables):
    from .business_ambiguity import _column_name as physical_column
    from .event_dimension_scope import _event_records, _published_pairs

    labels = ('申请注销', '注销申请')
    for table, value, tree, relation, atom, term in _event_records(_published_pairs(source), source, labels, tables):
        if not _INTAKE_DATE.search(term.get('definition') or ''):
            continue
        date_fields = {physical_column(_unwrap(part.this), relation)
                       for part in _and_atoms(tree.args['where'].this)
                       if isinstance(part, (exp.GTE, exp.LT)) and _day_literal(part.expression)}
        if len(date_fields) != 1 or None in date_fields:
            continue
        axis = date_fields.pop()
        if axis not in tables[table]:
            continue
        lower, upper = _date_bounds(_and_atoms(tree.args['where'].this), axis.casefold())
        if lower and upper and lower < upper:
            prop = atom.expression if isinstance(atom.this, exp.Literal) else atom.this
            if field := physical_column(prop, relation):
                yield table, field, value, axis


def _bindings(source):
    """Discover cancellation event roles from registered catalog semantics."""
    tables = {t['name']: {c['name'] for c in t['columns']} for t in source.get('tables', [])}
    published = None
    out = []
    for table in source.get('tables', []):
        columns = table.get('columns', [])
        specialty = [c['name'] for c in columns if any(_SPECIALTY_COLUMN.fullmatch(n) for n in _labels(c))]
        keys = [c['name'] for c in columns if any(_CASE_KEY.fullmatch(n) for n in _labels(c))]
        if not keys:
            # A published one-record-per-case table may describe its key by
            # the storage role alone (for example, 主键).
            pk = table.get('primary_key') or []
            keys = pk if len(pk) == 1 else [c['name'] for c in columns if '主键' in _labels(c)]
        if len(specialty) != 1 or len(keys) != 1:
            continue
        types = {(c['name'], str(v['value'])) for c in columns for v in c.get('business_values') or []
                 if v.get('value') is not None and (_EVENT_VALUE.fullmatch(str(v['value']))
                     or _EVENT_VALUE.fullmatch(str(v.get('label', ''))))}
        intake = [c['name'] for c in columns
                  if (ts := c.get('time_semantics')) and _INTAKE_DATE.search(ts.get('name') or '')]
        if not types or not intake:
            if published is None:
                published = list(_published_application_roles(source, tables))
            local = [(field, value, axis) for name, field, value, axis in published if name == table['name']]
            if not types:
                types = {(field, value) for field, value, _ in local}
            if not intake:
                intake = sorted({axis for field, value, axis in local if (field, value) in types})
        if len(types) != 1:
            continue
        type_column, type_value = types.pop()
        status = []
        for c in columns:
            if c['name'] == type_column:
                continue
            completed = sorted({str(v['value']) for v in c.get('business_values') or []
                                if v.get('value') is not None and _COMPLETED_LABEL.fullmatch(
                                    str(v.get('label') or v['value']).strip())})
            if completed:
                status.append((c['name'], completed))
        dates = [c['name'] for c in columns
                 if (ts := c.get('time_semantics')) and _EVENT_DATE.search(ts.get('name') or '')
                 and re.search(r'注销', (ts.get('name') or '') + (ts.get('description') or ''))]
        out.append({'table': table['name'], 'type_column': type_column, 'type_value': type_value,
                    'specialty_column': specialty[0], 'dedup_key': keys[0],
                    'status': status[0] if len(status) == 1 else None,
                    'event_date': dates[0] if len(dates) == 1 else None,
                    'intake_date': intake[0] if len(intake) == 1 else None})
    return out


def derive(source, text, reference, host=None):
    if not _intent(text):
        return []
    bindings = _bindings(source)
    if len(bindings) != 1:
        return []  # Ambiguous event sources stay with the generic unknown checks.
    binding = bindings[0]
    explicit_application = bool(_EXPLICIT_APP.search(text))
    verifiable = bool(binding['status'] and binding['event_date'])
    mode = 'completed' if verifiable and not explicit_application else 'application'
    if mode == 'application' and not binding['intake_date']:
        return []
    from .monthly_sql_scope import _annual_year
    annual = _annual_year(host or {}, text)
    if mode == 'completed':
        reason = '来源已登记可核实的注销完成状态与办结日期，实际注销按完成事件统计。'
    elif explicit_application:
        reason = '用户明确统计申请注销对象，按申请口径交付。'
    else:
        reason = '当前可核实申请注销案卷。实际注销量需要对应的注销生效状态和日期。'
    return [{'kind': KIND, 'source_id': source['id'], 'table': binding['table'],
             'type_column': binding['type_column'], 'type_value': binding['type_value'],
             'mode': mode, 'reason': reason, 'requested_application': explicit_application,
             'status_column': binding['status'][0] if mode == 'completed' else None,
             'status_values': binding['status'][1] if mode == 'completed' else None,
             'event_date_column': binding['event_date'] if mode == 'completed' else None,
             'intake_date_column': binding['intake_date'],
             'dedup_key': binding['dedup_key'], 'specialty_column': binding['specialty_column'],
             'year': annual[0] if annual else None, 'evidence': reference}]


def _column_label(source, table_name, column_name):
    table = next((t for t in source.get('tables', []) if t['name'] == table_name), None)
    column = next((c for c in (table or {}).get('columns', []) if c['name'] == column_name), None)
    labels = [*_labels(column or {}), ((column or {}).get('time_semantics') or {}).get('name')]
    labels = [n for n in labels if n]
    return labels[0] if labels else column_name


def catalog_guidance(source, host):
    text = (host or {}).get('user_text') or ''
    constraints = derive(source, text, {'user_ref': (host or {}).get('user_ref'), 'quote': text}, host=host)
    if not constraints:
        return None
    c = constraints[0]
    split_rule = ('专业拆分：先按；拆等级段，仅当段含：或:前缀时剥离前缀（无前缀段原样保留），'
                  '再按、拆专业项，去掉尾部脱敏标记（如。***）；不得用 split_part(段,\'：\',2) '
                  '或等价的无前缀丢弃写法。')
    dedup_rule = f'计数按（{c["dedup_key"]}, 归一专业）去重，同一案卷重复报送同一专业只计一次；单位：件。合计与占比用同一份最终结果。'
    if c['mode'] == 'completed':
        status_label = _column_label(source, c['table'], c['status_column'])
        date_label = _column_label(source, c['table'], c['event_date_column'])
        instruction = (f'统计实际完成注销：过滤 {c["type_column"]}=「{c["type_value"]}」且 '
                       f'{c["status_column"]}∈{c["status_values"]}（已登记完成状态「{status_label}」）；'
                       f'所问期间按{date_label}（{c["event_date_column"]}，注销完成日期）筛选。'
                       + split_rule + dedup_rule +
                       '正文写明统计对象为实际完成注销事件；仅申请、在办、驳回不计入。')
    else:
        intake = c['intake_date_column']
        axis = f'所问期间按{_column_label(source, c["table"], intake)}（{intake}）筛选'
        instruction = (f'{c["reason"]} 直接交付申请注销案卷排名。'
                       f'过滤 {c["type_column"]}=「{c["type_value"]}」，{axis}。'
                       + split_rule + dedup_rule +
                       '正文和结果标题使用「申请注销案卷」。')
    return {'mode': c['mode'], 'object': ('实际完成注销事件' if c['mode'] == 'completed' else '申请注销案卷（申请口径）'),
            'year': c['year'], 'table': c['table'], 'dedup_key': c['dedup_key'],
            'specialty_column': c['specialty_column'], 'instruction': instruction}


def ranking_plan(source, host):
    from .semantic import q, table

    request = (host or {}).get('user_ref')
    text = (host or {}).get('user_text') or ''
    publication = source.get('business_project') or {}
    dialect = sqlglot_name(source['dialect'])
    if (not request or not publication.get('id') or not publication.get('version')
            or (host or {}).get('clarifications') or dialect not in ('sqlite', 'postgres')
            or not re.search(r'最多|数量最高|排行|排名|注销分布', text)
            or re.search(r'最少|最低|地市|地区|省外|[市区县]|另外|以及|分别|同时|仅|只看|只查|排除|不含|明细', text)
            or not (constraints := derive(source, text, {'user_ref': request, 'quote': text}, host=host))):
        return None
    scope, = constraints
    year = scope['year']
    if not year or year >= 9999:
        return None
    completed = scope['mode'] == 'completed'
    axis = q(scope['event_date_column'] if completed else scope['intake_date_column'])
    key, specialty = q(scope['dedup_key']), q(scope['specialty_column'])

    def literal(value):
        return exp.Literal.string(value).sql(dialect=dialect)

    def bound(y):
        value = literal(f'{y:04}-01-01')
        return f'CAST({value} AS DATE)' if dialect == 'postgres' else value

    def split(value, delimiter, alias, output):
        if dialect == 'postgres':
            return f"REGEXP_SPLIT_TO_TABLE({value}, {literal(delimiter)}) AS {alias}({output})", f'{alias}.{output}'
        separators = ('；', ';') if delimiter == '[；;]' else ('、',)
        quoted = f'JSON_QUOTE({value})'
        for separator in separators:
            quoted = f"REPLACE({quoted}, {literal(separator)}, '\",\"')"
        return f"JSON_EACH('[' || {quoted} || ']') AS {alias}", f'{alias}.value'

    where = [q(scope['type_column']) + ' = ' + literal(scope['type_value']),
             f'{axis} >= {bound(year)}', f'{axis} < {bound(year + 1)}']
    if completed:
        where.append(q(scope['status_column']) + ' IN (' + ', '.join(map(literal, scope['status_values'])) + ')')
    stages, section = split(specialty, '[；;]', 'parts', 'segment')
    entries, entry = split('body', '、', 'labels', 'item')
    position = lambda marker: (f"STRPOS({section}, '{marker}')" if dialect == 'postgres'
                              else f"INSTR({section}, '{marker}')")
    strip_grade = (f"CASE WHEN {position('：')} > 0 THEN SUBSTR({section}, {position('：')} + 1) "
                   f"WHEN {position(':')} > 0 THEN SUBSTR({section}, {position(':')} + 1) ELSE {section} END")
    clean = f"TRIM(REPLACE({entry}, '。***', ''))"
    subject = '实际注销' if completed else '申请注销'
    sql = (f'WITH population AS (SELECT {key}, {specialty} FROM {table(scope["table"])} WHERE ' + ' AND '.join(where)
           + f'), sections AS (SELECT {key}, {strip_grade} AS body FROM population CROSS JOIN {stages}), '
           f'normalized AS (SELECT {key}, {clean} AS specialty FROM sections CROSS JOIN {entries}) '
           f'SELECT specialty AS "专业类别", COUNT(DISTINCT {key}) AS "{subject}案卷数（件）" FROM normalized '
           "WHERE LENGTH(specialty) > 1 AND specialty NOT IN ('未注明专业', '未知', '未填写') GROUP BY 1 ORDER BY 2 DESC, 1")
    checked = inspect(parse_one(sql, read=dialect), source, scope)
    if not checked or checked[0] != 'checked':
        return None
    return {'schema_version': 1, 'request_ref': request, 'quote': text, 'source_id': source['id'],
            'publication': {key:publication[key] for key in ('id', 'version')},
            'connection_revision': source.get('connection_revision', 0),
            'query_request': {'source_id': source['id'], 'sql': sql, 'purpose': 'answer',
                              'chart_type': 'bar', 'title': f'{year} 年测绘资质{subject}专业排名'}}


def object_statement(request_scope, source, tree):
    """The receipt's statistical-object disclosure for a bound cancellation query."""
    request = (request_scope or {}).get('request') or {}
    constraints = [c for c in (request_scope or {}).get('constraints', [])
                   if c.get('kind') == KIND and c.get('source_id') == source.get('id')
                   and (c.get('evidence') or {}).get('user_ref') == request.get('user_ref')]
    if not request.get('user_ref') or len(constraints) != 1:
        return None
    c = constraints[0]
    if (inspect(tree, source, c) or (None,))[0] != 'checked':
        return None
    if c['mode'] == 'completed':
        date_label = _column_label(source, c['table'], c['event_date_column'])
        statement = (f'统计对象为实际完成注销事件：{c["type_value"]}且已登记完成状态'
                     f'（{_column_label(source, c["table"], c["status_column"])}），'
                     f'按{date_label}落在所问期间，按案卷与归一专业去重；仅申请、在办、驳回不计入。')
        date_axis = c['event_date_column']
    else:
        statement = '本次按受理日期统计申请注销案卷，同一案卷的同一专业只计一次。'
        date_axis = c['intake_date_column']
    columns = {('count' if isinstance(p.unalias(), exp.Count) else 'specialty'): p.output_name
               for p in tree.expressions}
    return {'schema_version': 1, 'mode': c['mode'], 'object': ('实际完成注销事件' if c['mode'] == 'completed' else '申请注销案卷（申请口径）'),
            'date_axis': date_axis, 'dedup': f'({c["dedup_key"]}, 归一专业)', 'year': c['year'],
            'columns': columns, 'requested_application': c['requested_application'],
            'basis': c['reason'], 'statement': statement}


# ---------------- SQL inspection ----------------

def _unwrap(node):
    while isinstance(node, (exp.Paren, exp.Cast)):
        node = node.this
    return node


def _and_atoms(node):
    if isinstance(node, exp.Paren):
        return _and_atoms(node.this)
    if isinstance(node, exp.And):
        return _and_atoms(node.this) + _and_atoms(node.expression)
    return [node] if node is not None else []


def _is_expander(ref):
    if isinstance(ref, exp.Table):
        ref = ref.this
    if isinstance(ref, Scope) and isinstance(ref.expression, exp.Lateral):
        ref = ref.expression.this
    return isinstance(ref, exp.Func) and _func_name(ref) in _EXPANDERS


def _used_scope_path(tree, source, dialect, table):
    """Follow the answer's actual population to the one authorized fact read."""
    try:
        scopes = traverse_scope(tree)
        scope = scopes[-1] if scopes else None
        path = []
        while scope and isinstance(scope.expression, exp.Select):
            if scope in path:
                return None
            path.append(scope)
            relations = [(alias, ref) for alias, (_, ref) in scope.selected_sources.items()
                         if not _is_expander(ref)]
            if len(relations) != 1 or any(scope.expression.args.get(key) for key in ('offset', 'limit', 'qualify')):
                return None
            alias, ref = relations[0]
            if not isinstance(ref, Scope):
                return (path, alias) if _physical_table(ref, source, dialect) == table else None
            scope = ref
    except OptimizeError:
        return None
    return None


def _column_name(node, qualifier=None):
    node = _unwrap(node)
    if (isinstance(node, exp.Column) and not (node.is_star or node.db or node.catalog)
            and (not qualifier or not node.table or node.table.casefold() == qualifier.casefold())):
        return node.name.casefold()
    return None


def _membership(atom, name, qualifier):
    def pin(value):
        value = _unwrap(value)
        if isinstance(value, exp.Literal):
            return value.this if value.is_string else None
        return None
    if isinstance(atom, exp.EQ):
        for left, right in ((atom.this, atom.expression), (atom.expression, atom.this)):
            if _column_name(left, qualifier) == name and (value := pin(right)) is not None:
                return {value}
    elif isinstance(atom, exp.In) and not atom.args.get('query') and _column_name(atom.this, qualifier) == name:
        values = {pin(item) for item in atom.expressions}
        if values and None not in values:
            return values
    return None


def _day_literal(node):
    node = _unwrap(node)
    if isinstance(node, exp.Date):
        node = _unwrap(node.this)
    if isinstance(node, exp.Literal) and node.is_string and _DAY.fullmatch(node.this):
        try:
            return date.fromisoformat(node.this)
        except ValueError:
            return None
    return None


def _date_bounds(atoms, name, qualifier=None):
    lower, upper = [], []
    reversed_operator = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE}
    for atom in atoms:
        operator = type(atom)
        if operator not in reversed_operator:
            continue
        field, value = atom.this, atom.expression
        if _column_name(value, qualifier) == name:
            field, value = value, field
            operator = reversed_operator[operator]
        if _column_name(field, qualifier) != name or not (day := _day_literal(value)):
            continue
        if operator in (exp.GT, exp.LTE):
            if not isinstance(field, exp.Cast) or field.to.this != exp.DataType.Type.DATE or day == date.max:
                continue
            day += timedelta(days=1)
        (lower if operator in (exp.GT, exp.GTE) else upper).append(day)
    return max(lower, default=None), min(upper, default=None)


def _func_name(node):
    return str(node.this if isinstance(node, exp.Anonymous) else node.sql_name()).casefold()


def _scope_nodes(path, *kinds):
    return (node for scope in path for node in scope.expression.find_all(*kinds)
            if node.find_ancestor(exp.Select) is scope.expression)


def _expands_specialty(path, specialty):
    return any(_func_name(node) in _EXPANDERS
               and any(_column_name(col) == specialty.casefold() for col in node.find_all(exp.Column))
               for node in _scope_nodes(path, exp.Func))


def _prefix_drop(path):
    """Provably unsafe grade-prefix stripping: unguarded split_part(...,'：',2)
    or CASE WHEN instr(x,'：')>0 THEN ... ELSE '' END (sqlite translation)."""
    for node in _scope_nodes(path, exp.SplitPart):
        delimiter = _unwrap(node.args.get('delimiter'))
        index = _unwrap(node.args.get('part_index'))
        if (isinstance(delimiter, exp.Literal) and delimiter.this in _COLONS
                and isinstance(index, exp.Literal) and index.is_int and int(index.this) >= 2):
            return True
    for node in _scope_nodes(path, exp.Case):
        default = _unwrap(node.args.get('default'))
        if not (isinstance(default, exp.Literal) and default.is_string and default.this == ''):
            continue
        for condition in node.args.get('ifs') or []:
            for pos in condition.find_all(exp.StrPosition):
                substr = _unwrap(pos.args.get('substr'))
                if isinstance(substr, exp.Literal) and substr.this in _COLONS:
                    return True
    return False


def _group_counts(tree, key):
    groups = tree.args.get('group')
    if not groups or len(groups.expressions) != 1 or len(tree.expressions) != 2:
        return None
    for index, projection in enumerate(tree.expressions):
        count = projection.unalias()
        if not isinstance(count, exp.Count):
            continue
        group = groups.expressions[0].unnest()
        label = tree.expressions[1 - index]
        if not (label.unalias() == group or isinstance(group, exp.Column) and group.name == label.alias
                or isinstance(group, exp.Literal) and group.is_int and int(group.this) == 2 - index):
            continue
        distinct = count.this
        if isinstance(distinct, exp.Distinct) and len(distinct.expressions) == 1:
            counted = distinct.expressions[0]
            if _column_name(counted) == key.casefold():
                return True
            if isinstance(counted, exp.DPipe):
                used = {_column_name(col) for col in counted.find_all(exp.Column)}
                grouped = {_column_name(col) for col in label.find_all(exp.Column)}
                if key.casefold() in used and used <= (grouped | {key.casefold()}):
                    return True
        return False
    return None


def inspect(tree, source, constraint):
    if constraint.get('source_id') != source['id']:
        return None
    dialect = sqlglot_name(source['dialect'])
    scoped = _used_scope_path(tree, source, dialect, constraint['table'])
    if not scoped:
        return None
    path, alias = scoped
    fact_where = path[-1].expression.args.get('where')
    atoms = _and_atoms(fact_where.this if fact_where else None)
    issues, checked = [], []
    type_column = constraint['type_column'].casefold()
    type_values = _membership_all(atoms, type_column, alias)
    if not any(values == {constraint['type_value']} for values in type_values):
        issues.append(f'统计对象为「{constraint["type_value"]}」案卷；查询缺少 '
                      f'{constraint["type_column"]}=「{constraint["type_value"]}」筛选，其他业务类型不能计入注销。')
    else:
        checked.append(f'已核对事件类型筛选 {constraint["type_column"]}=「{constraint["type_value"]}」。')
    if constraint['mode'] == 'completed':
        status_column = constraint['status_column'].casefold()
        allowed = set(constraint['status_values'])
        status_values = _membership_all(atoms, status_column, alias)
        if not status_values:
            label = _column_label(source, constraint['table'], constraint['status_column'])
            issues.append(f'该来源已登记可核实的注销完成状态（{constraint["status_column"]}「{label}」∈{sorted(allowed)}）。'
                          '统计实际注销数量必须过滤完成状态；仅申请（含在办、驳回）不是实际完成注销，不能冒充注销量。')
        elif set.intersection(*status_values) != allowed:
            issues.append(f'完成状态筛选应完整覆盖 {constraint["status_column"]}∈{sorted(allowed)}。')
        else:
            checked.append('已核对注销完成状态筛选。')
    axis = constraint['event_date_column'] if constraint['mode'] == 'completed' else constraint['intake_date_column']
    lower, upper = _date_bounds(atoms, axis.casefold(), alias)
    year = constraint.get('year')
    if year:
        expected = (date(year, 1, 1), date(year + 1, 1, 1))
        label = _column_label(source, constraint['table'], axis)
        if (lower, upper) == expected:
            checked.append(f'已核对年度窗口按{label}（{axis}）[{expected[0]}, {expected[1]})。')
        else:
            issues.append(f'年度窗口按{label}（{axis}）[{expected[0]}, {expected[1]})。'
                          f'当前下界 {lower or "缺失"}、上界 {upper or "缺失"}。')
    expansion = _expands_specialty(path, constraint['specialty_column'])
    dedup = _group_counts(tree, constraint['dedup_key'])
    if expansion:
        if _prefix_drop(path):
            issues.append('专业拆分按「：」取第2段（split_part 或等价的 CASE 置空写法）会丢弃无等级前缀的专业项；'
                          '须保留无前缀项：仅当存在前缀分隔符时剥离，否则原样保留。')
        else:
            checked.append('专业拆分保留无前缀项。')
        if dedup is False:
            issues.append(f'专业展开后按行计数（COUNT(1)/COUNT(*)）会把同一案卷重复报送的同一专业重复计入；'
                          f'请按（{constraint["dedup_key"]}, 归一专业）去重计数，'
                          f'如 COUNT(DISTINCT {constraint["dedup_key"]} || 分隔符 || 归一专业)。')
        elif dedup:
            checked.append(f'已核对按案卷键 {constraint["dedup_key"]} 与专业组合去重计数。')
    if issues:
        return 'conflict', ' '.join(issues)
    if expansion and dedup and lower and upper and lower < upper:
        return 'checked', ' '.join(checked)
    return None


def _membership_all(atoms, name, qualifier):
    return [values for atom in atoms if (values := _membership(atom, name, qualifier)) is not None]
