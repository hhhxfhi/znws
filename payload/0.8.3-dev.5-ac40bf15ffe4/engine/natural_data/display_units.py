"""Resolve result units from SQL projections and published source metadata."""
import re
import unicodedata
from decimal import Decimal
from functools import cache

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from .business_dictionary import declared_field_unit
from .catalog_index import fingerprint
from .metric_subject import physical_entity_parent
from .vendor.charts import _field
from .vendor.dialect import sqlglot_name


_COLUMN_UNIT = re.compile(r'[（(]\s*(本|张|件|家次|家|户|项次|项|条|人|个|台|套|笔|部|组|批|宗|份|辆|'
                          r'箱|盒|包|瓶|袋|卷|桶|枚|颗|节|架|辆次|人次|'
                          r'(?:人民币)?(?:万|亿)?元|万次|次|%|％|个百分点|千?米|平方(?:千)?米|公顷|亩|吨|千克|克)\s*[）)]\s*$'
                          r'|(?<=[\w\u4e00-\u9fff])[_＿](件|条|万元)\s*$')
_RATIO_ALIAS = re.compile(r'占比|比率|比例|率|百分比|百分点|(?:^|[_＿])(?:rate|ratio|percent|pct)(?:[_＿]|$)', re.I)
_SHARE_ALIAS = re.compile(r'(?:占比|比率|比例|份额|百分比)$|(?:^|_)(?:share|ratio|percent|pct)$', re.I)
_DIFFERENCE_ALIAS = re.compile(r'百分点|差额|差值|差异|(?:^|_)(?:pp|points?|diff|difference|delta)(?:_|$)', re.I)
_PERCENT_MARK = re.compile(r'[_＿]\s*(?:百分比|pct|percent)\s*$', re.I)
_RATIO_SQL = (exp.Div, exp.SafeDivide)
_PRESERVES_UNIT = (exp.Paren, exp.Min, exp.Max, exp.Sum, exp.Avg, exp.Round, exp.Abs, exp.Floor, exp.Ceil, exp.Neg)
_ASCII_IDENTIFIER_FOLD = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
_ROW_FILTER_NODES = (
    exp.And, exp.Or, exp.Not, exp.Paren, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
    exp.Is, exp.In, exp.Between, exp.Like, exp.ILike, exp.Tuple, exp.Column, exp.Identifier,
    exp.Literal, exp.Null, exp.Boolean,
)


def unit_label(label,unit):
    suffix=r'\s*[（(]\s*'+re.escape(unit)+r'\s*[）)]\s*$'
    while re.search(suffix,label):label=re.sub(suffix,'',label)
    return label+'（'+unit+'）'


def _encoding_fields(node):
    if isinstance(node, dict):
        if node.get('field'):
            yield node
        for child in node.values():
            yield from _encoding_fields(child)
    elif isinstance(node, list):
        for child in node:
            yield from _encoding_fields(child)


def _fields(result):
    return _encoding_fields((result.get('chart') or {}).get('encoding'))


def normalize_units(result):
    contract=result.get('metric_contract') or {}
    unit=contract.get('unit');field='value'
    ranking=result.get('profession_ranking_contract') or {}
    if not unit and ranking.get('kind')=='historical_profession_ranking':unit='家';field='单位数'
    if not unit or field not in result.get('columns',[]):return
    labels=result.setdefault('column_labels',{})
    labels[field]=unit_label(labels.get(field) or (result.get('title') if field=='value' else field) or '指标值',unit)
    for encoding in _fields(result):
        if encoding['field'] == field:
            encoding.update(label=labels[field], unit=unit)


def _evidence(unit, kind, basis):
    return {'unit': unit, 'kind': kind, 'basis': basis}


def _alias_unit(name):
    match = _COLUMN_UNIT.search(name)
    if not match:
        return None
    return match[1] or (None if _RATIO_ALIAS.search(name[:match.start()]) else match[2])


def _unit_key(unit):
    return unicodedata.normalize('NFKC', unit).strip().removeprefix('人民币')


def _share_alias(name):
    name = unicodedata.normalize('NFKC', name).strip()
    unit = _COLUMN_UNIT.search(name)
    if unit and unit[1] == '%':
        name = name[:unit.start()].rstrip()
    return bool(_SHARE_ALIAS.search(name) and not _DIFFERENCE_ALIAS.search(name))


def _row_count(node):
    if isinstance(node, exp.Filter):
        node = node.this
    if not isinstance(node, exp.Count) or node.expressions:
        return False
    operand = node.this.unnest() if node.this is not None else None
    return (isinstance(operand, exp.Star) and not any(operand.args.values())
            or isinstance(operand, exp.Literal) and bool(operand.this))


def _zero_default(node):
    return (isinstance(node, exp.Coalesce) and not node.args.get('is_nvl')
            and len(node.expressions) == 1 and isinstance(node.expressions[0], exp.Literal)
            and node.expressions[0].is_number and Decimal(node.expressions[0].this) == 0)


def _number_is(node, value):
    node = node.unnest()
    return isinstance(node, exp.Literal) and node.is_number and Decimal(node.this) == value


def _times_100(node):
    node = node.unnest()
    if isinstance(node, exp.Mul):
        if _number_is(node.this, 100):
            return node.expression.unnest()
        if _number_is(node.expression, 100):
            return node.this.unnest()
    return None


def _percentage_aggregate(node):
    if _row_count(node):
        return True
    return (isinstance(node, (exp.Count, exp.Sum)) and not node.expressions
            and isinstance(node.this, exp.Column) and not node.this.is_star)


def _scope_column(column, scope, key):
    if not isinstance(column, exp.Column) or column.is_star or column.db or column.catalog:
        return None
    selected = {key(alias): ref for alias, (_, ref) in scope.selected_sources.items()}
    refs = [selected.get(key(column.table))] if column.table else selected.values()
    matches = []
    for ref in refs:
        if not isinstance(ref, Scope) or not isinstance(ref.expression, exp.Select):
            return None
        items = ref.expression.selects
        names = ref.outer_columns or [item.alias_or_name for item in items]
        if len(names) != len(items) or any(item.is_star for item in items):
            return None
        matches.extend((ref, index) for index, name in enumerate(names) if key(name) == key(column.name))
    return matches[0] if len(matches) == 1 else None


def _cross_join_total(numerator, denominator, scope, key, *, dialect=None):
    joins = scope.expression.args.get('joins') or []
    if len(joins) != 1 or len(scope.selected_sources) != 2 or joins[0].kind != 'CROSS':
        return False
    if any(joins[0].args.get(arg) for arg in ('on', 'using', 'side', 'method')):
        return False
    part = _scope_column(numerator, scope, key)
    total = _scope_column(denominator, scope, key)
    if not part or not total or part[0] is total[0]:
        return False
    grouped, count_index = part
    scalar, _ = total
    if _sums_grouped_projection(part, scalar, key):
        return True
    return bool(dialect and len(scalar.selected_sources) == 1
                and isinstance(next(iter(scalar.selected_sources.values()))[1], exp.Table)
                and _scalar_row_count_total(grouped.expression.selects[count_index].unalias().unnest(),
                                            scalar.expression, grouped.expression, dialect))


def _sums_grouped_projection(part, scalar, key):
    grouped, index = part
    if not grouped.expression.args.get('group') or not _percentage_aggregate(grouped.expression.selects[index].unalias()):
        return False
    # The scalar must sum exactly that grouped projection without filtering,
    # deduplicating or replacing any of its rows.
    if (len(scalar.selected_sources) != 1 or len(scalar.expression.selects) != 1
            or any(value for arg, value in scalar.expression.args.items() if arg not in {'expressions', 'from', 'from_'})):
        return False
    aggregate = scalar.expression.selects[0].unalias().unnest()
    if not isinstance(aggregate, exp.Sum) or aggregate.expressions:
        return False
    return _scope_column(aggregate.this, scalar, key) == part


def _scalar_group_total(numerator, denominator, scope, key):
    if len(scope.selected_sources) != 1:
        return False
    if isinstance(denominator, exp.Subquery):
        denominator = denominator.this
    if not isinstance(denominator, exp.Select):
        return False
    part = _scope_column(numerator, scope, key)
    scalar = next((child for child in scope.subquery_scopes if child.expression is denominator), None)
    return bool(part and scalar and _sums_grouped_projection(part, scalar, key))


def _scaled_percentage_terms(node, name):
    if not _share_alias(name):
        return None
    node = node.unnest()
    digits = None
    if isinstance(node, exp.Round):
        decimals = node.args.get('decimals')
        if decimals is not None and (not isinstance(decimals, exp.Literal) or not decimals.is_int):
            return None
        digits = int(decimals.this) if decimals is not None else 0
        node = node.this.unnest()
    scaled_ratio = _times_100(node)
    ratio = scaled_ratio if scaled_ratio is not None else node
    if not isinstance(ratio, exp.Div):
        return None
    numerator = ratio.this.unnest() if scaled_ratio is not None else _times_100(ratio.this)
    if numerator is None:
        return None
    denominator = ratio.expression.unnest()
    if isinstance(denominator, exp.Nullif) and _number_is(denominator.expression, 0):
        denominator = denominator.this.unnest()
    return numerator, denominator, digits


def _unpartitioned_sum(node):
    if not isinstance(node, exp.Window) or any(value for key, value in node.args.items() if key not in {'this', 'over'}):
        return None
    aggregate = node.this.unnest()
    if not isinstance(aggregate, exp.Sum) or aggregate.expressions:
        return None
    return aggregate.this.unnest()


def _row_identifier(dialect):
    if dialect == 'sqlite':
        # SQLite name resolution folds ASCII letters only.
        return lambda identifier: identifier.name.translate(_ASCII_IDENTIFIER_FOLD)
    return lambda identifier: normalize_identifiers(identifier.copy(), dialect=sqlglot_name(dialect)).name


def _local_row_columns(expression, table, identifier_name):
    alias = table.args.get('alias')
    local_name = identifier_name(alias.this if alias else table.this)
    expression = expression.copy()
    for column in expression.find_all(exp.Column):
        qualifier = column.args.get('table')
        if (column.db or column.catalog or column.is_star
                or qualifier and identifier_name(qualifier) != local_name):
            return None
        column.set('this', exp.to_identifier(identifier_name(column.this)))
        column.set('table', None)
    return expression


def _row_count_population(select, identifier_name, *, grouped=False):
    """Bind a simple row filter to its one physical relation."""
    allowed = {'expressions', 'from', 'from_', 'where'}
    if grouped:
        allowed |= {'group', 'order'}
        group = select.args.get('group')
        if not group or not group.expressions or any(value for arg, value in group.args.items() if arg != 'expressions'):
            return None
    if any(value for arg, value in select.args.items() if arg not in allowed):
        return None
    relation = select.args.get('from') or select.args.get('from_')
    table = relation.this if relation else None
    if (not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier)
            or any(value for arg, value in table.args.items() if arg not in {'this', 'db', 'catalog', 'alias'})):
        return None
    alias = table.args.get('alias')
    if alias and alias.args.get('columns'):
        return None
    name = tuple(identifier_name(part) for part in table.parts)
    where = select.args.get('where')
    if where is None:
        return name, None
    if not all(isinstance(node, _ROW_FILTER_NODES) for node in where.this.walk()):
        return None
    predicate = _local_row_columns(where.this, table, identifier_name)
    return (name, predicate) if predicate is not None else None


def _physical_row_count(node):
    return (isinstance(node, exp.Count) and _row_count(node)
            and (isinstance(node.this.unnest(), exp.Star) or _number_is(node.this, 1)))


def _scalar_row_count_total(numerator, denominator, outer, dialect):
    """Prove COUNT(*) over the same row population in a scalar subquery."""
    if isinstance(denominator, exp.Subquery):
        denominator = denominator.this
    if not isinstance(denominator, exp.Select) or len(denominator.selects) != 1:
        return False
    if not all(_physical_row_count(count) for count in (numerator, denominator.selects[0].unalias().unnest())):
        return False
    identifier_name = _row_identifier(dialect)
    population = _row_count_population(outer, identifier_name, grouped=True)
    return population is not None and population == _row_count_population(denominator, identifier_name)


def _single_category_group(grouped, category, position):
    group = grouped.args.get('group')
    if not group or len(group.expressions) != 1 or any(value for name, value in group.args.items() if name != 'expressions'):
        return False
    if not (isinstance(category, exp.Column) and not category.is_star
            or isinstance(category, exp.Replace) and isinstance(category.this, exp.Column)
            and isinstance(category.expression, exp.Literal) and category.expression.is_string
            and isinstance(category.args.get('replacement'), exp.Literal) and category.args['replacement'].is_string):
        return False
    group_key = group.expressions[0].unnest()
    return group_key == category or (isinstance(group_key, exp.Literal) and group_key.is_int
                                     and int(group_key.this) == position + 1)


def _population_ignoring_category_null(relation, predicate, group):
    column = group.this if isinstance(group, exp.Replace) else group

    def split(node):
        if node is None:
            return []
        node = node.unnest()
        if isinstance(node, exp.And):
            return [*split(node.this), *split(node.expression)]
        return [node]

    def is_category_not_null(node):
        return (isinstance(node, exp.Not) and isinstance(test := node.this.unnest(), exp.Is)
                and test.this.unnest() == column and isinstance(test.expression, exp.Null))

    terms = split(predicate)
    remaining = [term for term in terms if not is_category_not_null(term)]
    if len(remaining) != len(terms):
        predicate = exp.and_(*remaining) if remaining else None
    return fingerprint([relation, predicate.sql(identify=True) if predicate is not None else None])


def row_count_partition(tree, dialect):
    """Prove that a scalar total and a single grouped count address the same rows."""
    if not isinstance(tree, exp.Select) or not 1 <= len(tree.selects) <= 3:
        return None
    columns = [item.alias_or_name for item in tree.selects]
    if not all(columns) or len(set(columns)) != len(columns):
        return None
    projections = [item.unalias().unnest() for item in tree.selects]
    counts = [i for i, node in enumerate(projections) if _physical_row_count(node)]
    if len(counts) != 1:
        return None
    count = counts[0]
    grouped = len(projections) > 1
    identifier_name = _row_identifier(dialect)
    if (population := _row_count_population(tree, identifier_name, grouped=grouped)) is None:
        return None
    relation, predicate = population
    proof = {'schema_version': 1, 'count_column': columns[count],
             'population_fingerprint': fingerprint([relation, predicate.sql(identify=True) if predicate is not None else None])}
    if not grouped:
        return proof
    if len(projections) == 3:
        if not (share := _grouped_count_share_shape(tree, dialect, columns)):
            return None
        category = share[0]
    else:
        category = 1 - count
    if not _single_category_group(tree, projections[category], category):
        return None
    table = (tree.args.get('from') or tree.args['from_']).this
    group = _local_row_columns(projections[category], table, identifier_name)
    if group is None:
        return None
    return {**proof, 'category_column': columns[category], 'group_fingerprint': fingerprint(group.sql(identify=True)),
            'non_null_category_population': _population_ignoring_category_null(relation, predicate, group)}


def _is_scaled_percentage(node, name, scope, key):
    if not (terms := _scaled_percentage_terms(node, name)):
        return False
    numerator, denominator, _ = terms
    if isinstance(numerator, exp.Column):
        return _scalar_group_total(numerator, denominator, scope, key) or _cross_join_total(numerator, denominator, scope, key)
    if not _percentage_aggregate(numerator):
        return False
    if isinstance(denominator, exp.Window):
        denominator = _unpartitioned_sum(denominator)
    return numerator == denominator


def grouped_count_share(sql, dialect, columns):
    """Identify one group, one row count, and its percentage of all such rows."""
    if len(columns) != 3 or len(set(columns)) != 3 or not any(map(_share_alias, columns)):
        return None
    try:
        tree = parse_one(sql, read=sqlglot_name(dialect))
    except SqlglotError:
        return None
    return _grouped_count_share_shape(tree, dialect, columns)


def _grouped_count_share_shape(tree, dialect, columns):
    if not isinstance(tree, exp.Select) or len(tree.selects) != 3:
        return None
    nested = tree.args.get('with') or tree.args.get('with_') or tree.args.get('joins')
    allowed = {'expressions', 'from', 'from_', 'order'}
    if any(value for name, value in tree.args.items()
           if name not in allowed | ({'with', 'with_', 'joins'} if nested else {'where', 'group'})):
        return None
    key = str.casefold if dialect == 'sqlite' else str
    scope = list(traverse_scope(tree))[-1] if nested else None
    nodes = [item.unalias().unnest() for item in tree.selects]
    for index, node in enumerate(nodes):
        if not (terms := _scaled_percentage_terms(node, columns[index])):
            continue
        count, denominator, digits = terms
        if digits is None or not 0 <= digits <= 6:
            continue
        count_indexes = [i for i, item in enumerate(nodes) if item == count]
        if len(count_indexes) != 1:
            continue
        count_index = count_indexes[0]
        category_index = 3 - index - count_index
        category = nodes[category_index]
        grouped = tree
        category_position = category_index
        if nested:
            if not (_scalar_group_total(count, denominator, scope, key)
                    or _cross_join_total(count, denominator, scope, key, dialect=dialect)):
                continue
            grouped_scope, count_position = _scope_column(count, scope, key)
            category_projection = _scope_column(category, scope, key)
            if not category_projection or category_projection[0] is not grouped_scope:
                continue
            grouped = grouped_scope.expression
            if (len(grouped_scope.selected_sources) != 1
                    or not isinstance(next(iter(grouped_scope.selected_sources.values()))[1], exp.Table)
                    or any(value for arg, value in grouped.args.items() if arg not in allowed | {'where', 'group'})):
                continue
            count = grouped.selects[count_position].unalias().unnest()
            category_position = category_projection[1]
            category = grouped.selects[category_position].unalias().unnest()
        elif _unpartitioned_sum(denominator) != count and not _scalar_row_count_total(count, denominator, tree, dialect):
            continue
        source = grouped.args.get('from') or grouped.args.get('from_')
        if (not source or not isinstance(source.this, exp.Table) or not isinstance(source.this.this, exp.Identifier)
                or not _physical_row_count(count) or not _single_category_group(grouped, category, category_position)):
            continue
        return category_index, count_index, index, digits
    return None


def _apply_alias(name, evidence, strict, *, ratio=False):
    unit = _alias_unit(name)
    if not unit or ratio and _COLUMN_UNIT.search(name)[2]:
        return evidence
    if evidence and evidence['kind'] != 'sql_row_count':
        if strict and _unit_key(unit) != _unit_key(evidence['unit']):
            raise ValueError(f'SQL_UNIT_MISMATCH: 结果列「{name}」与上游计量单位「{evidence["unit"]}」不一致。'
                             f'请核对计量单位，确需换算时请在查询中执行换算。单位依据：{evidence["basis"]}')
        return evidence
    return _evidence(unit, 'sql_alias', '查询列显式注明计量单位')


def _table_identity(name, tables, key):
    """Resolve a table spelling to one registered name across qualification styles.

    The catalog, the published dictionary and the executed SQL may spell the same
    physical table with or without a schema/database qualifier (a connection
    refresh re-introspects qualified names; pack imports may store bare names).
    Bind by identity only when exactly one registered table carries the final
    name part, and never hop between two differently qualified schemas.
    """
    norm = key(name)
    if norm in tables:
        return norm
    parts = norm.split('.')
    candidates = [path for path in tables if path.split('.')[-1] == parts[-1]]
    if len(candidates) != 1:
        return None
    path = candidates[0]
    registered = path.split('.')
    if len(parts) > 1 and len(registered) > 1 and parts[:-1] != registered[:-1]:
        return None
    return path


def _projection_units(tree, source, dialect, *, strict=False, columns=None, direct_measurements=False, alias_units=True):
    """Follow only unit-preserving expressions through each selected SQL scope."""
    # PostgreSQL distinguishes quoted identifiers. SQLite folds quoted names too.
    tree = normalize_identifiers(tree, dialect=sqlglot_name(dialect))
    key = str.casefold if dialect == 'sqlite' else str
    tables = {key(t['name']): t for t in source.get('tables', [])}
    declared = {}
    for mapping in source.get('business_dictionary', []):
        if mapping.get('source_id') == source.get('id') and (unit := declared_field_unit(mapping)):
            mapping_name = key(mapping['table'])
            table_key = _table_identity(mapping_name, tables, key)
            if table_key is None:
                continue
            evidence = _evidence(unit[0], 'published_field', unit[1])
            if direct_measurements:
                evidence['label'] = _COLUMN_UNIT.sub('', mapping.get('name') or '').strip()
            table_fields = declared.setdefault(table_key, {})
            table_fields.setdefault(key(mapping['column']), {})[mapping_name] = evidence
    scopes = {}

    def physical(ref):
        if not isinstance(ref, exp.Table) or not isinstance(ref.this, exp.Identifier):
            return None
        name = _table_identity('.'.join(part.name for part in ref.parts), tables, key)
        return tables[name] if name else None

    @cache
    def fields_for(table_name, sql_name):
        # Retain each declaration's qualifier through the comparison with the SQL.
        fields = {}
        for column, mappings in declared.get(table_name, {}).items():
            if mapping_name := _table_identity(sql_name, mappings, key):
                fields[column] = mappings[mapping_name]
        return fields

    def from_source(ref):
        if isinstance(ref, Scope):
            return scopes.get(id(ref), {})
        table = physical(ref)
        return fields_for(key(table['name']), '.'.join(part.name for part in ref.parts)) if table else {}

    def measure(node, scope, selected, *, sole_table):
        wrappers = (exp.Paren, exp.Sum, exp.Round) if direct_measurements else _PRESERVES_UNIT
        while (isinstance(node, wrappers) or _zero_default(node)
               or not direct_measurements and (isinstance(node, exp.Filter) and isinstance(node.this, exp.AggFunc)
               or isinstance(node, exp.Cast) and node.args['to'].this in exp.DataType.NUMERIC_TYPES)):
            operand = node.this
            if (isinstance(node, (exp.Min, exp.Max, exp.Sum, exp.Avg)) and isinstance(operand, exp.Distinct)
                    and len(operand.expressions) == 1):
                if direct_measurements:
                    return None
                operand = operand.expressions[0]
            node = operand
        if _row_count(node) and selected:
            if sole_table and (row_unit := sole_table.get('row_unit')):
                return _evidence(row_unit['unit'], 'published_row', row_unit['basis'])
            return _evidence('条', 'sql_row_count', 'SQL 统计当前结果集的行数')
        if (not direct_measurements and sole_table and isinstance(node, exp.Count)
                and isinstance(node.this, exp.Distinct) and len(node.this.expressions) == 1
                and isinstance(column := node.this.expressions[0], exp.Column) and not column.db and not column.catalog
                and (not column.table or key(column.table) in selected)
                and (parent := physical_entity_parent(source, sole_table['name'], column.name))
                and (row_unit := parent.get('row_unit'))):
            return _evidence(row_unit['unit'], 'published_row', row_unit['basis'])
        if not isinstance(node, exp.Column) or node.is_star:
            return None
        if node.table:
            ref = selected.get(key(node.table))
            if (node.db or node.catalog) and (not isinstance(ref, exp.Table)
                    or node.db != ref.db or node.catalog != ref.catalog):
                return None
        elif len(selected) == 1:
            ref = next(iter(selected.values()))
        else:
            binding = _scope_column(node, scope, key)
            if not binding:
                return None
            ref, _ = binding
        return from_source(ref).get(key(node.name))

    output = {}
    for scope in traverse_scope(tree):
        output = {}
        if not isinstance(scope.expression, exp.Select):
            scopes[id(scope)] = output
            continue
        selected = {key(alias): ref for alias, (_, ref) in scope.selected_sources.items()}
        sole_table = physical(next(iter(selected.values()))) if len(selected) == 1 else None
        names = scope.outer_columns
        if (scope.is_root and columns is not None and len(columns) == len(scope.expression.selects)
                and all(not item.is_star for item in scope.expression.selects)):
            names = columns
        for index, item in enumerate(scope.expression.selects):
            if item.is_star:
                star = item.this if isinstance(item, exp.Column) else item
                ref = selected.get(key(item.table)) if isinstance(item, exp.Column) else (
                    next(iter(selected.values())) if len(selected) == 1 else None)
                if not names and not any(star.args.values()) and ref is not None and len(scope.expression.selects) == 1:
                    output.update(from_source(ref))
                continue
            name = names[index] if index < len(names) else item.alias_or_name
            if not name:
                continue
            evidence = measure(item.unalias(), scope, selected, sole_table=sole_table)
            ratio = any(node.find_ancestor(exp.Select) is scope.expression for node in item.find_all(*_RATIO_SQL))
            if item.alias and alias_units:
                evidence = _apply_alias(item.alias, evidence, strict, ratio=ratio)
            if (not evidence and not direct_measurements and selected and ratio
                    and _is_scaled_percentage(item.unalias(), name, scope, key)):
                evidence = _evidence('%', 'sql_scaled_percentage', '同一计量的聚合比例在 SQL 中显式乘以 100')
            # Retain None for duplicate projection names. They cannot be bound safely.
            normalized = key(name)
            if normalized in output:
                output[normalized] = None
            else:
                output[normalized] = _apply_alias(name, evidence, strict, ratio=ratio) if alias_units else evidence
        scopes[id(scope)] = output
    return output


def direct_published_sums(tree, source, dialect):
    """Resolve only simple sums of published fields, independent of SQL aliases."""
    if (not isinstance(tree, exp.Select) or tree.args.get('joins')
            or tree.find(exp.CTE, exp.Subquery, exp.Window, exp.Union)):
        return {}
    tree = normalize_identifiers(tree, dialect=sqlglot_name(dialect))
    key = str.casefold if dialect == 'sqlite' else str
    names = {}
    for column in tree.expressions:
        node = column.unalias()
        while isinstance(node, (exp.Paren, exp.Round)) or _zero_default(node):
            node = node.this
        if isinstance(node, exp.Sum) and not isinstance(node.this, exp.Distinct):
            names[key(column.alias_or_name)] = column.alias_or_name
    if not names:
        return {}
    projected = _projection_units(tree, source, dialect, direct_measurements=True)
    return {names[column]: evidence for column, evidence in projected.items()
            if column in names and evidence and evidence['kind'] == 'published_field' and evidence.get('label')}


def require_published_units(tree, source):
    _projection_units(tree, source, source['dialect'], strict=True)


def _legacy_view_units(result):
    """Old explicit view metadata is useful; old heuristic units are not evidence."""
    labels = result.get('column_labels') or {}
    saved_units = {}
    for encoding in _fields(result):
        field = encoding['field']
        unit = encoding.get('unit')
        if unit and unit != _field(field, encoding.get('label') or labels.get(field)).get('unit'):
            saved_units.setdefault(field, set()).add(unit)
    return {field: _evidence(next(iter(units)), 'saved_view', '历史结果保存的计量单位')
            for field, units in saved_units.items() if len(units) == 1}


def sql_units(result, sql, dialect, *, source=None, historical=False):
    if result.get('metric_contract') or result.get('profession_ranking_contract'):
        return {}
    source = source or {}
    projected = {}
    checked_count = None
    count_unit = None
    if ('count' in sql.casefold() or source.get('business_dictionary')
            or any(_alias_unit(c) or _share_alias(c) for c in result['columns'])):
        try:
            tree = parse_one(sql, read=sqlglot_name(dialect))
            columns = result['columns']
            # The count and percentage use the same physical rows once this
            # shape is proven. Model-generated aliases cannot change that unit.
            share = (_grouped_count_share_shape(tree, dialect, columns) if len(columns) == 3
                     and len(set(columns)) == 3 and any(map(_share_alias, columns)) else None)
            projected = _projection_units(tree, source, dialect, columns=columns, alias_units=not share)
            if any(item and item['kind'] == 'sql_row_count' for item in projected.values()):
                count_unit = _checked_population_unit(result, source, tree)
            if share:
                checked_count = columns[share[1]]
                column = columns[share[2]]
                key = column.casefold() if dialect == 'sqlite' else column
                projected[key] = _evidence('%', 'sql_scaled_percentage', '同一计量的聚合比例在 SQL 中显式乘以 100')
        except SqlglotError:
            pass
    saved = _legacy_view_units(result) if historical else {}
    evidence = {}
    for column in result['columns']:
        item = projected.get(column.casefold() if dialect == 'sqlite' else column)
        if item and item['kind'] == 'sql_row_count' and count_unit:
            item = _evidence(count_unit, 'published_row', '已核对发布范围的逐行计数单位')
        if not item or item['kind'] == 'sql_row_count' and column != checked_count:
            # A suffix declaration needs its actual SQL projection so rate
            # expressions cannot acquire a count or currency unit by naming.
            alias = _COLUMN_UNIT.search(column)
            item = ((_apply_alias(column, None, False) if alias and alias[1] else None) or saved.get(column) or item)
        if not item and (result.get('text_numbers') or {}).get(column) == 'percent':
            item = _evidence('%', 'numeric_text', '返回值以百分比文本表示')
        if item:
            evidence[column] = item
    return evidence


def _checked_population_unit(result, source, tree):
    if not isinstance(tree, exp.Select) or len(list(tree.find_all(exp.Table))) != 1 or tree.args.get('joins'):
        return None
    scopes = [check['constraint'] for check in (result.get('validation') or {}).get('checks', [])
              if check.get('state') == 'checked' and (check.get('constraint') or {}).get('kind') == 'published_scope']
    if not scopes:
        return None
    from .row_count_aggregation import _registered_count_specs

    units = {spec['unit'] for _, spec in _registered_count_specs(source)
             if any(spec['id'] == scope.get('scope_id') and spec['table'] == scope.get('table')
                    and spec['source_revision'] == scope.get('source_revision') for scope in scopes)}
    return next(iter(units)) if len(units) == 1 else None


def apply_sql_units(result, evidence):
    if result.get('metric_contract') or result.get('profession_ranking_contract'):
        return
    captions = result.setdefault('column_labels', {})
    measures = (result.get('sql_answer_contract') or result.get('answer_contract') or {}).get('measurements') or {}
    for column, entry in evidence.items():
        label = (measures.get(column) or {}).get('label') or captions.get(column) or column
        if entry['kind'] in {'sql_row_count', 'published_row'} and re.fullmatch(r'(?i)count\s*\(\s*[*1]\s*\)', column):
            label = '记录数'
        if entry['kind'] == 'sql_scaled_percentage':
            label = _PERCENT_MARK.sub('', label)
        label = _COLUMN_UNIT.sub('', label).rstrip()
        captions[column] = unit_label(label, entry['unit'])
    for encoding in _fields(result):
        field = encoding['field']
        if field in evidence:
            encoding.update(label=captions[field], unit=evidence[field]['unit'])
        elif encoding.get('unit') == _field(field, encoding.get('label') or captions.get(field)).get('unit'):
            encoding.pop('unit', None)


def normalize_sql_units(result, sql, dialect):
    evidence = sql_units(result, sql, dialect, historical=True)
    apply_sql_units(result, evidence)
    return {column: entry['unit'] for column, entry in evidence.items()}
