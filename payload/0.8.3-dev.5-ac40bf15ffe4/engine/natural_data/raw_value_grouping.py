"""Keep physical dimension members when the user asks for them individually."""
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from .enum_discovery import _resolve_physical_col, _table_meta_map
from .period_grain import _group_projection
from .raw_dimension_scope import _raw_or_empty
from .retrieval import normalize
from .row_count_aggregation import _registered_count_specs
from .snapshot_scope import _physical_table
from .vendor.dialect import sqlglot_name


_INDIVIDUAL = re.compile(r'(?:各(?:个)?|每(?:一)?个|逐(?:一|个)?|按(?:照)?\s*(?:原始|原有|实际))\s*')
_ONE_ROW_AXIS = re.compile(r'(?:按(?:照)?|以)\s*(.{1,30}?)(?:\s*(?:各|每个))?\s*(?:一行|逐个|逐一)')
_BUCKET = re.compile(r'归(?:为|入|并为)|合并(?:成|到|为)|合为|划为|分(?:成|为)[^，。；]{0,8}类|按[^，。；]{1,18}大类')
_NEGATIVE = re.compile(r'(?:不(?:要|用|得|许|必|再|能)?|禁止|别|无[须需]|无需)[^，。；!?]{0,20}$')
_CLAUSE = re.compile(r'[，,。；;！？!?\n]')
_CITY_AXIS = {'城市', '地市', '市级行政区'}


def _axis_label(value):
    label = normalize(value).strip()
    return '城市' if label in _CITY_AXIS else label


def _explicit_buckets(question):
    for clause in _CLAUSE.split(question):
        if any(not _NEGATIVE.search(clause[:match.start()]) for match in _BUCKET.finditer(clause)):
            return True
    return False


def _case_origin(node, scope, key):
    names = set()
    for _ in range(8):
        names.add(node.alias_or_name)
        node = node.unalias().unnest()
        if isinstance(node, exp.Case):
            return node, scope, names
        if not isinstance(node, exp.Column) or node.is_star or node.db or node.catalog:
            return None
        names.add(node.name)
        refs = [ref for alias, (_, ref) in scope.selected_sources.items()
                if not node.table or key(alias) == key(node.table)]
        if len(refs) != 1 or not isinstance(child := refs[0], Scope) or not isinstance(child.expression, exp.Select):
            return None
        items = child.expression.selects
        labels = child.outer_columns or [item.alias_or_name for item in items]
        matching = [item for label, item in zip(labels, items) if key(label) == key(node.name)]
        if len(matching) != 1:
            return None
        node, scope = matching[0], child
    return None


def _value_test(node):
    node = node.unnest()
    if isinstance(node, (exp.And, exp.Or)):
        return _value_test(node.this) and _value_test(node.expression)
    if isinstance(node, exp.Is):
        return isinstance(node.this.unnest(), exp.Column) and isinstance(node.expression, exp.Null)
    if isinstance(node, exp.In):
        return (isinstance(node.this.unnest(), exp.Column) and bool(node.expressions) and not node.args.get('query')
                and all(isinstance(item, exp.Literal) and item.is_string for item in node.expressions))
    if isinstance(node, (exp.EQ, exp.Like, exp.ILike)):
        operands = node.this.unnest(), node.expression.unnest()
        pairs = (operands, operands[::-1]) if isinstance(node, exp.EQ) else (operands,)
        for value, literal in pairs:
            if not isinstance(literal, exp.Literal) or not literal.is_string:
                continue
            if isinstance(value, exp.Column):
                return True
            if isinstance(node, exp.EQ) and literal.this == '' and isinstance(value, exp.Trim):
                return isinstance(value.this.unnest(), exp.Column)
    return False


def require_raw_dimension_grouping(sql, source, question, *, published_grouping=None):
    """Keep the original scope and reject a lossy bucket on one requested field."""
    text = normalize(question)
    clauses = _CLAUSE.split(text)
    targets = [clause[match.end():].lstrip('「“"') for clause in clauses
               for match in _INDIVIDUAL.finditer(clause) if not _NEGATIVE.search(clause[:match.start()])]
    row_axes = {_axis_label(match[1].strip(' 「」“”"')) for clause in clauses
                for match in _ONE_ROW_AXIS.finditer(clause) if not _NEGATIVE.search(clause[:match.start()])}
    if published_grouping or not (targets or row_axes) or _explicit_buckets(text) or not re.search(r'\bcase\b', sql, re.I):
        return
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return
    key = str.casefold if dialect == 'sqlite' else str
    try:
        tree = normalize_identifiers(sqlglot.parse_one(sql, read=dialect), dialect=dialect)
        scopes = list(traverse_scope(tree))
        if not scopes:
            return
        active = {id(scopes[-1])}
        for scope in reversed(scopes):
            if id(scope) in active:
                active.update(id(ref) for _, ref in scope.selected_sources.values() if isinstance(ref, Scope))
    except (sqlglot.errors.SqlglotError, ValueError):
        return

    table_map, projections = _table_meta_map(source), {}
    for scope in scopes:
        select = scope.expression
        if id(scope) not in active or not isinstance(select, exp.Select):
            continue
        items = select.selects
        projections[id(scope)] = {
            name.lower(): physical for name, item in zip(scope.outer_columns or [i.alias_or_name for i in items], items)
            if name and (physical := _resolve_physical_col(item, scope, table_map, projections))
        }
        if not (group := select.args.get('group')):
            continue
        selected = {}
        for name, (_, ref) in scope.selected_sources.items():
            if isinstance(ref, Scope) and isinstance(ref.expression, exp.Select):
                cols = ref.outer_columns or [item.alias_or_name for item in ref.expression.selects]
            else:
                table = _physical_table(ref, source, dialect)
                cols = [item['name'] for item in (table_map.get((table or '').lower()) or {}).get('columns', [])]
            selected[key(name)] = {key(col) for col in cols}

        for term in group.expressions:
            term = term.unnest()
            index = _group_projection(term, items, selected, key)
            item = items[index] if index is not None else term
            if not (origin := _case_origin(item, scope, key)):
                continue
            case, case_scope, projection_names = origin
            branches = case.args.get('ifs') or []
            if not branches or not all(isinstance(branch.args.get('true'), exp.Literal)
                                       and branch.args['true'].is_string for branch in branches):
                continue
            if case.this is not None:
                if not all(isinstance(branch.this, exp.Literal) and branch.this.is_string for branch in branches):
                    continue
                columns = [case.this]
            else:
                if not all(_value_test(branch.this) for branch in branches):
                    continue
                columns = [column for branch in branches for column in branch.this.find_all(exp.Column)]
            resolve = lambda node: _resolve_physical_col(node, case_scope, table_map, projections)
            bindings = [resolve(column) for column in columns]
            if not bindings or not bindings[0] or any(binding != bindings[0] for binding in bindings):
                continue
            table, field = bindings[0]
            names = [field['name'], field.get('label'), *(field.get('aliases') or []), *projection_names]
            names.extend(mapping.get('name') for mapping in source.get('business_dictionary', [])
                         if mapping.get('source_id') == source['id'] and mapping.get('table') == table
                         and mapping.get('column') == field['name'])
            names.extend(alias for _, spec in _registered_count_specs(source)
                         if spec['table'] == table and spec['dimension'] == field['name']
                         for alias in spec.get('detail_dimension_aliases', []))
            labels = {label for name in names if len(label := normalize(name).strip()) >= 2}
            if not (any(_axis_label(label) in row_axes for label in labels)
                    or any(target.startswith(label) for target in targets for label in labels)):
                continue
            if _raw_or_empty(case, lambda node: field['name'] if resolve(node) == (table, field) else None, field['name']):
                continue
            column = field['name']
            raise ValueError(f'DIMENSION_BUCKET_CONFLICT: 原问要求按维度原值分别统计，当前 CASE 会改变或合并「{column}」取值。'
                             '请在同一数据源中按该原字段分组，保留原来的业务对象、日期和全部筛选。'
                             '确有缺失归属时，只标注空值并原样保留其他取值。勿重复执行原查询。')
