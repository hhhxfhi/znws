"""Validate the source and requested grain of formal query results."""
import re
import unicodedata
from dataclasses import dataclass, replace
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, build_scope, traverse_scope

_CONTEXT = re.compile(r'(?:^|_)(year|month|quarter|period|target|threshold|rank_order|code)(?:$|_)|年份|年度|月份|季度|阈值|目标值|排序', re.I)
_NUMERIC = re.compile(r'[-+]?\d[\d,]*(?:\.\d+)?[%％]?')
_KEEP_MISSING = re.compile(
    r'(?:缺(?:失|测|值)|空值|未(?:登记|填写|记录|上报)|没(?:有)?(?:登记|填写|记录|上报))[^。！？；\n]{0,24}'
    r'(?:保留(?:为)?空|留空|(?:不(?:能|要|得)?|禁止)补(?:成|为)?[零0])'
    r'|(?:不(?:能|要|得)?|禁止)(?:把|将)?(?:缺(?:失|测|值)|空值|未记录)[^。！？；\n]{0,16}补(?:成|为)?[零0]'
)
_GROUP_CUE = re.compile(r'(?:按\s*(?:(?:各(?:个)?|每个)\s*)?|(?:各(?:个)?|每个)\s*)')
_MEASURE_GROUPING = re.compile(r'分档|分箱|区间|划档|明细|逐条|'
                               r'(?:按|以)[^，。；\n]{1,32}(?:和|与|及|、)[^，。；\n]{1,20}(?:分组|分层|统计|汇总|计算|列出)')
_NUMERIC_DB_TYPE = re.compile(r'^(?:SMALLINT|BIGINT|INTEGER|INT\d*|REAL|DOUBLE(?: PRECISION)?|FLOAT\d*|DECIMAL|NUMERIC)'
                              r'(?:\(\s*\d+(?:\s*,\s*\d+)?\s*\))?$', re.I)
_EXPLICIT_RATIO = re.compile(r'(\w{2,32})\s*(?:占|除以|÷)\s*(\w{2,24}?)'
                             r'\s*(?:的(?:百分比|比例|比率|占比)|(?:×|乘以)\s*100\s*%)')
_ONLY_RATIO = re.compile(r'([^().。?!;\n]{2,140})\(\s*' + _EXPLICIT_RATIO.pattern + r'\s*\)[。?!]?')
_OTHER_RATIO_REQUEST = re.compile(r'或者|或是|分别|另外|同时|以及|并且|一并|顺便|再查|替代|改用')
_AMOUNT_SUBJECT = re.compile(r'^(?:累计|本期|当期|同期|总)?([\u4e00-\u9fff]{2,24}?)(?:总|累计)?'
                              r'(?:金额|价值|费用|成本|收入|支出|价格|总额|额)$')


@dataclass(frozen=True)
class _FormulaField:
    names: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    summed: bool = False


def _amount_name(name):
    text = unicodedata.normalize('NFKC', str(name or ''))
    text = re.sub(r'[\s「」“”]', '', text)
    return re.sub(r'(?:\((?:元|万元|亿元|人民币)\)|合计|总计|累计)$', '', text)


def _amount_subject(name):
    match = _AMOUNT_SUBJECT.fullmatch(_amount_name(name))
    return match[1] if match else None


def _zero_or_null(node):
    return (node is None or isinstance(node, exp.Null)
            or isinstance(node, exp.Literal) and node.is_number and float(node.this) == 0)


def _formula_value(node, fields, key):
    """Resolve one monetary input or one ratio without inventing expression lineage."""
    node = node.unalias().unnest()
    if isinstance(node, exp.Column) and not (node.db or node.catalog or node.is_star):
        name = key(node.name)
        matches = [visible[name] for alias, visible in fields.items()
                   if name in visible and (not node.table or alias == key(node.table))]
        return matches[0] if len(matches) == 1 else None
    if isinstance(node, exp.Div):
        left, right = (_formula_value(part, fields, key) for part in (node.this, node.expression))
        return (left, right) if isinstance(left, _FormulaField) and isinstance(right, _FormulaField) else None
    if isinstance(node, exp.Sum):
        value = _formula_value(node.this, fields, key)
        return replace(value, summed=True) if isinstance(value, _FormulaField) else None
    if isinstance(node, exp.Round) or (
        isinstance(node, exp.Cast) and node.args['to'].this in exp.DataType.NUMERIC_TYPES
    ):
        return _formula_value(node.this, fields, key)
    if isinstance(node, (exp.Nullif, exp.Coalesce)):
        fallbacks = [node.expression] if isinstance(node, exp.Nullif) else node.expressions
        if fallbacks and all(isinstance(value, exp.Literal) and value.is_number and float(value.this) == 0 for value in fallbacks):
            return _formula_value(node.this, fields, key)
    if isinstance(node, exp.Case):
        branches = [branch.args.get('true') for branch in node.args.get('ifs', ())]
        branches.append(node.args.get('default'))
        values = [_formula_value(branch, fields, key) for branch in branches if not _zero_or_null(branch)]
        if values and isinstance(values[0], _FormulaField) and all(value == values[0] for value in values):
            return values[0]
    if isinstance(node, exp.Mul):
        for factor, value in ((node.this, node.expression), (node.expression, node.this)):
            if isinstance(factor, exp.Literal) and factor.is_number:
                return _formula_value(value, fields, key)
    return None


def _formula_outputs(tree, source, dialect):
    from .snapshot_scope import _physical_table

    key = str.casefold if dialect == 'sqlite' else str
    names_by_field = {}
    for mapping in source.get('business_dictionary', []):
        if mapping.get('kind') == 'field' and mapping.get('source_id') == source.get('id') and mapping.get('name'):
            names_by_field.setdefault((mapping.get('table'), mapping.get('column')), []).append(mapping['name'])
    physical = {}
    for table in source.get('tables', []):
        physical[table['name']] = {}
        for col in table.get('columns', []):
            names = [col['name'], col.get('label'), col.get('business_name'), *(col.get('aliases') or [])]
            names.extend(names_by_field.get((table['name'], col['name']), []))
            physical[table['name']][key(col['name'])] = _FormulaField(tuple(name for name in names if name))

    outputs = {}
    for scope in traverse_scope(tree):
        if not isinstance(scope.expression, exp.Select):
            continue
        fields = {key(alias): outputs.get(id(ref)) if isinstance(ref, Scope)
                  else physical.get(_physical_table(ref, source, dialect))
                  for alias, (_, ref) in scope.selected_sources.items()}
        items = scope.expression.selects
        names = [key(name) for name in (scope.outer_columns or [item.alias_or_name for item in items])]
        if (None in fields.values() or len(names) != len(items) or len(set(names)) != len(names)
                or any(item.is_star for item in items)):
            continue
        projected = {}
        for name, item in zip(names, items):
            value = _formula_value(item, fields, key)
            projected[name] = replace(value, aliases=(*value.aliases, name)) if isinstance(value, _FormulaField) else value
        if scope.expression is tree:
            return projected.values()
        outputs[id(scope)] = projected
    return ()


def require_formula_basis(sql, source, question):
    """Keep an unrelated monetary total out of the requested formula."""
    text = unicodedata.normalize('NFKC', question or '')
    matches = _EXPLICIT_RATIO.findall(text)
    if len(matches) != 1:
        return
    numerator, denominator = (_amount_name(part) for part in matches[0])
    expected = _amount_subject(denominator)
    if not expected:
        return
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return
    try:
        tree = normalize_identifiers(sqlglot.parse_one(sql, read=dialect), dialect=dialect)
        if not isinstance(tree, exp.Select) or tree.find(exp.SetOperation, exp.Window):
            return
        values = list(_formula_outputs(tree, source, dialect))
    except sqlglot.errors.SqlglotError:
        return

    def matches_numerator(field):
        return any(numerator.endswith(name) for name in map(_amount_name, field.names) if len(name) >= 2)

    denominator_aliases = {denominator, '分母', 'denominator'}
    pairs = [value for value in values if isinstance(value, tuple)]
    # Models can fetch the two operands with SQL and divide them in a later
    # analysis tool or in prose. The projected denominator needs the same proof.
    fields = [value for value in values if isinstance(value, _FormulaField)]
    known_numerator = next((field for field in fields if matches_numerator(field)), None)
    single_ratio = _ONLY_RATIO.fullmatch(text.strip())
    requested_basis_present = any(_amount_name(item.alias_or_name) in denominator_aliases for item in tree.selects)
    only_ratio_operands = single_ratio and not _OTHER_RATIO_REQUEST.search(single_ratio[1]) and not requested_basis_present
    if known_numerator:
        pairs.extend((known_numerator, field) for field in fields if field is not known_numerator)
    else:
        pairs.extend((None, field) for field in fields
                     if denominator in set(map(_amount_name, field.aliases)))
    for left, right in pairs:
        labeled_basis = bool(denominator_aliases & set(map(_amount_name, right.aliases)))
        if not right.summed or not (labeled_basis or only_ratio_operands):
            continue
        if left is not None and (not matches_numerator(left) or (only_ratio_operands and matches_numerator(right))):
            continue
        if denominator in {_amount_name(name) for name in right.names}:
            continue
        declared = []
        for name in right.names:
            for relation in re.finditer(r'(?:将|把|用|以)\s*[「“]?' + re.escape(name) + r'[」”]?\s*'
                                        r'(?:作为|视为|当作|替代|代替)\s*[「“]?' + re.escape(denominator), text):
                before = re.split(r'[。！？；，]', text[:relation.start()])[-1]
                if not re.search(r'不(?:能|要|可|应|得|用)|禁止|是否', before):
                    declared.append(name)
        known = [(name, subject) for name in right.names if (subject := _amount_subject(name))]
        if declared or not known or any(set(subject) & set(expected) for _, subject in known):
            continue
        if only_ratio_operands and not labeled_basis and any(subject in single_ratio[1] for _, subject in known):
            continue
        actual = known[0][0]
        raise ValueError(f'FORMULA_BASIS_MISMATCH: 用户要求用「{denominator}」作为分母，SQL 实际用「{actual}」的合计。'
                         '请按原对象、期间及筛选查指定分母；暂缺时只查同范围的分组和可核实分子，并说明缺少的字段。')


def _derived_numeric_aggregate(item, scope, table_meta):
    from .enum_discovery import _resolve_physical_col

    value = item.unalias().unnest()
    while isinstance(value, (exp.Round, exp.Cast)):
        if isinstance(value, exp.Cast) and value.args['to'].this not in exp.DataType.NUMERIC_TYPES:
            return False
        value = value.this.unnest()
    if isinstance(value, exp.Count):
        return True
    if not isinstance(value, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        return False
    operand = value.this.unnest()
    if isinstance(operand, exp.Cast):
        return operand.args['to'].this in exp.DataType.NUMERIC_TYPES
    origin = _resolve_physical_col(operand, scope, table_meta, {})
    return bool(origin and _NUMERIC_DB_TYPE.fullmatch(origin[1].get('type') or ''))


def _joined_aggregate_per_group(tree, scope, axis_alias, axis_name, measure_alias, table_meta, key):
    """Prove that the join returns the same aggregate for every row of an outer group."""
    from .enum_discovery import _resolve_physical_col

    joins = tree.args.get('joins') or []
    if len(joins) != 1:
        return False
    join = joins[0]
    condition = join.args.get('on')
    if (join.side not in ('', 'LEFT') or join.kind not in ('', 'INNER', 'OUTER') or condition is None
            or not isinstance(equality := condition.unnest(), exp.EQ)
            or key(join.this.alias_or_name) != measure_alias):
        return False
    fields = (equality.this.unnest(), equality.expression.unnest())
    if not all(isinstance(field, exp.Column) and not (field.db or field.catalog) for field in fields):
        return False
    ends = {key(field.table): field for field in fields}
    if set(ends) != {axis_alias, measure_alias} or key(ends[axis_alias].name) != key(axis_name):
        return False
    child = next((ref for alias, (_, ref) in scope.selected_sources.items() if key(alias) == measure_alias), None)
    if not isinstance(child, Scope) or len(child.selected_sources) != 1 or not (group := child.expression.args.get('group')):
        return False
    if len(group.expressions) != 1 or any(value for name, value in group.args.items() if name != 'expressions'):
        return False
    items = child.expression.selects
    names = child.outer_columns or [item.alias_or_name for item in items]
    projected = next((item for name, item in zip(names, items) if key(name) == key(ends[measure_alias].name)), None)
    if projected is None:
        return False
    grouped = group.expressions[0].unnest()
    if isinstance(grouped, exp.Literal) and grouped.is_int and 1 <= int(grouped.this) <= len(items):
        grouped = items[int(grouped.this) - 1]
    origin = _resolve_physical_col(ends[axis_alias], scope, table_meta, {})
    # The very same physical column preserves the affinity and collation on
    # both sides. A different join key can split equal display labels.
    return bool(origin and origin == _resolve_physical_col(projected, child, table_meta, {})
                == _resolve_physical_col(grouped, child, table_meta, {}))


def require_grouping_evidence(sql, source, question):
    """Reject a proved aggregate value splitting one specifically requested dimension."""
    cues = list(_GROUP_CUE.finditer(question or ''))
    if len(cues) != 1 or _MEASURE_GROUPING.search(question):
        return
    focus = question[cues[0].end():].lstrip('「“')
    from .enum_discovery import _table_meta_map
    from .period_grain import _group_projection
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return
    key = str.casefold if dialect == 'sqlite' else str
    try:
        tree = normalize_identifiers(sqlglot.parse_one(sql, read=dialect), dialect=dialect)
        if (not isinstance(tree, exp.Select) or not (group := tree.args.get('group')) or len(group.expressions) < 2
                or any(value for name, value in group.args.items() if name != 'expressions')
                or not any(item.find(exp.AggFunc) for item in tree.expressions)
                or tree.find(exp.SetOperation, exp.Window)):
            return
        scope = build_scope(tree)
        selected = scope.selected_sources if scope else {}
    except sqlglot.errors.SqlglotError:
        return
    tables = {table['name']: table for table in source.get('tables', [])}
    table_meta = _table_meta_map(source)
    columns, derived = {}, {}
    for alias, (_, ref) in selected.items():
        alias = key(alias)
        if isinstance(ref, Scope) and isinstance(ref.expression, exp.Select):
            items = ref.expression.selects
            fields = [key(name) for name in (ref.outer_columns or [item.alias_or_name for item in items])]
            if len(fields) != len(items) or len(set(fields)) != len(fields) or any(not name or name == '*' for name in fields):
                return
            columns[alias] = set(fields)
            derived[alias] = {name for name, item in zip(fields, items) if _derived_numeric_aggregate(item, ref, table_meta)}
        elif table := _physical_table(ref, source, dialect):
            columns[alias] = {key(column['name']) for column in tables[table].get('columns', [])}
        else:
            return

    dimensions, leaked = [], []
    for term in group.expressions:
        term = term.unnest()
        index = _group_projection(term, tree.expressions, columns, key)
        item = tree.expressions[index] if index is not None else term
        value = item.unalias().unnest()
        if not isinstance(value, exp.Column) or value.db or value.catalog or value.is_star:
            return
        name = key(value.name)
        aliases = [a for a, names in columns.items() if name in names and (not value.table or a == key(value.table))]
        if len(aliases) != 1:
            return
        alias = aliases[0]
        label = item.alias_or_name
        if alias not in derived:
            dimensions.append((label, value.name, alias))
        elif name in derived[alias]:
            leaked.append((label, value.name, alias))
        else:
            return
    def requested(labels):
        return any(focus.startswith(label) for label in labels[:2] if len(label) >= 2)
    if len(dimensions) == 1 and leaked and requested(dimensions[0]) and not any(requested(labels) for labels in leaked):
        label, axis_name, axis_alias = dimensions[0]
        metric_aliases = {alias for _, _, alias in leaked}
        if len(metric_aliases) == 1 and _joined_aggregate_per_group(
                tree, scope, axis_alias, axis_name, next(iter(metric_aliases)), table_meta, key):
            return
        raise ValueError(f'BUSINESS_SCOPE_CONFLICT: 分组粒度与原问不符。原问按「{label}」汇总，SQL 将子查询的聚合数值'
                         '作为额外分组字段，可能把同一组拆成多行。请按原事实和原筛选在该维度聚合；平均值应使用原始非空值'
                         '或同口径分子与分母重新计算，保留实际零和缺测。')


def require_missing_values(sql, dialect, question):
    if not question or not _KEEP_MISSING.search(question):
        return

    def zero(node):
        while isinstance(node, (exp.Paren, exp.Cast, exp.Neg)):
            node = node.this
        return isinstance(node, exp.Literal) and re.fullmatch(r'[+-]?0+(?:\.0+)?', str(node.this))

    def null_condition(node):
        while isinstance(node, exp.Paren):
            node = node.this
        return isinstance(node, exp.Is) and isinstance(node.expression, exp.Null) and bool(node.find(exp.Column))

    tree = sqlglot.parse_one(sql, read=dialect)
    # Check SELECT expressions only. Calculating a count with CASE ... ELSE 0
    # remains valid when the predicate is about membership, not missing data.
    for select in tree.find_all(exp.Select):
        for projection in select.expressions:
            for node in projection.find_all(exp.Coalesce, exp.If):
                fill = (isinstance(node, exp.Coalesce) and node.this.find(exp.Column) is not None
                        and any(zero(fallback) for fallback in node.expressions))
                if isinstance(node, exp.If):
                    condition = node.this.unnest()
                    if isinstance(condition, exp.Not):
                        fallback = node.args.get('false')
                        if isinstance(node.parent, exp.Case) and len(node.parent.args.get('ifs') or []) == 1:
                            fallback = node.parent.args.get('default')
                        fill = null_condition(condition.this) and zero(fallback)
                    else:
                        fill = null_condition(condition) and zero(node.args.get('true'))
                if fill:
                    raise ValueError('NULL_VALUE_POLICY: 用户要求缺值保留为空。保留真实零值，去掉把 NULL 填成 0 的表达式；'
                                     '按原条件重查，正文、表格和图表使用同一正式结果。')


def require_result_evidence(sql, dialect):
    tree = sqlglot.parse_one(sql,read=dialect)
    tainted = {}
    scopes = list(traverse_scope(tree))

    def literal_value(node, scope):
        if isinstance(node, exp.Alias): node = node.this
        if isinstance(node, exp.Column):
            sources = [scope.sources.get(node.table)] if node.table else scope.sources.values()
            return any(isinstance(src, Scope) and node.name.lower() in tainted.get(id(src), set()) for src in sources)
        if isinstance(node, exp.Literal):
            return bool(_NUMERIC.fullmatch(str(node.this).strip()))
        if isinstance(node, exp.Placeholder):
            return True
        if isinstance(node, exp.AggFunc):
            # A count measures rows. MAX(427) merely repeats a supplied value.
            return not isinstance(node, exp.Count) and isinstance(node.this, exp.Expression) and literal_value(node.this, scope)
        if isinstance(node,(exp.Cast,exp.Paren,exp.Neg)):
            return literal_value(node.this,scope)
        if isinstance(node,(exp.Add,exp.Sub,exp.Div,exp.Mul)):
            return literal_value(node.this,scope) and literal_value(node.expression,scope)
        if isinstance(node, exp.Coalesce) and isinstance(node.this, exp.Literal):
            return literal_value(node.this, scope)
        if isinstance(node, exp.Subquery):
            child = next((s for s in scopes if s.expression is node.this), None)
            return bool(child and tainted.get(id(child)))
        # Scalar functions and CASE must derive their result from a real column
        # or a row-count aggregate. Recurse so wrappers cannot hide constants.
        children = list(node.iter_expressions())
        numeric = any(literal_value(child, scope) for child in children)
        dynamic = any(not literal_value(col, scope) for col in node.find_all(exp.Column))
        counted = any(isinstance(agg, exp.Count) for agg in node.find_all(exp.AggFunc))
        return numeric and not dynamic and not counted

    def reject(): raise ValueError('RESULT_LITERAL: 结果数值必须由授权数据计算。请用原字段聚合、已有指标或带来源的分析计算，不要把结论数字写进 SELECT。')

    for scope in scopes:
        select = scope.expression
        if isinstance(select, exp.SetOperation):
            # Set operations take output names from the first branch, by position.
            branches = scope.union_scopes
            bad = {col.alias_or_name.lower() for i,col in enumerate(select.selects) if any(
                i<len(b.expression.selects) and b.expression.selects[i].alias_or_name.lower() in tainted.get(id(b),set()) for b in branches)}
        elif isinstance(select, exp.Values):
            names = select.alias_column_names
            bad = {name.lower() for i,name in enumerate(names) if any(i<len(row.expressions) and literal_value(row.expressions[i],scope) for row in select.expressions)}
        else:
            bad = set()
            for col in select.selects:
                if col.is_star:
                    sources = [scope.sources.get(col.table)] if isinstance(col,exp.Column) and col.table else scope.sources.values()
                    for src in sources:
                        if isinstance(src, Scope): bad.update(tainted.get(id(src),set()))
                elif literal_value(col,scope): bad.add(col.alias_or_name.lower())
        tainted[id(scope)] = bad
        if scope is scopes[-1]:
            if any(not _CONTEXT.search(name) for name in bad): reject()
            if bad and all(col.alias_or_name.lower() in bad for col in select.selects): reject()
