"""Diagnose SQL column references when their intended scope can be proven."""
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope
from .vendor.dialect import sqlglot_name


def _require_cte_from(tree, dialect):
    if (not isinstance(tree, exp.Select) or not tree.ctes or tree.args.get('from')
            or tree.args.get('from_') or tree.args.get('joins')):
        return

    # Exact spelling is enough for a diagnosis across dialects. Leave case or
    # quoting mismatches to the database and its existing recovery path.
    def key(identifier):
        return identifier.name

    # Use only columns of this SELECT. A column inside an independent scalar
    # subquery is already allowed to read that CTE in its own FROM clause.
    local = [column for column in tree.find_all(exp.Column) if column.find_ancestor(exp.Select) is tree]
    output = {key(item.args['alias']) for item in tree.expressions if isinstance(item, exp.Alias)}
    order = tree.args.get('order')
    alias_refs = {id(ordered.this) for ordered in (order.expressions if order else [])
                  if isinstance(ordered.this, exp.Column) and not ordered.this.table
                  and isinstance(ordered.this.this, exp.Identifier) and key(ordered.this.this) in output}
    columns = [column for column in local if id(column) not in alias_refs]
    if not columns or any(column.db or column.catalog or not isinstance(column.this, exp.Identifier) for column in columns):
        return
    fields = {key(column.this) for column in columns}
    qualifiers = {key(column.args['table']) for column in columns if column.table}
    candidates = []
    for cte in tree.ctes:
        alias = cte.args['alias']
        if qualifiers and qualifiers != {key(alias.this)}:
            continue
        if not isinstance(cte.this, exp.Select):
            return  # An opaque competing CTE could supply these columns.
        names = alias.args.get('columns') or []
        items = cte.this.expressions
        if names:
            if len(names) != len(items) or any(item.is_star for item in items):
                return
        else:
            names = [item.args['alias'] if isinstance(item, exp.Alias) else item.this
                     if isinstance(item, exp.Column) else None for item in items]
        if not names or any(not isinstance(name, exp.Identifier) for name in names):
            return
        projected = {key(name) for name in names}
        if len(projected) != len(names):
            return
        if fields <= projected:
            candidates.append(alias.this)
    if len(candidates) == 1:
        relation = candidates[0].sql(dialect=dialect)
        raise ValueError(f'SCHEMA_MISMATCH: 外层 SELECT 引用了 CTE「{relation}」的字段，但本层缺少 FROM。'
                         f'请在外层查询补 FROM {relation}，保留原有筛选和统计表达式。')


def validate_correlation(sql, source):
    dialect = sqlglot_name(source['dialect'])
    tree = sqlglot.parse_one(sql, read=dialect)
    _require_cte_from(tree, dialect)
    tables = {t['name'].split('.')[-1].lower(): {c['name'].lower() for c in t['columns']}
              for t in source.get('tables', [])}
    for scope in traverse_scope(tree):
        if scope.parent is None or not scope.is_subquery:
            continue
        for eq in scope.expression.find_all(exp.EQ):
            if eq.find_ancestor(exp.Select) is not scope.expression:
                continue
            a, b = eq.this, eq.expression
            if not (isinstance(a, exp.Column) and isinstance(b, exp.Column)
                    and a.name.lower() == b.name.lower() and bool(a.table) != bool(b.table)):
                continue
            qualified = a if a.table else b
            ref = scope.sources.get(qualified.table)
            if isinstance(ref, exp.Table) and qualified.name.lower() in tables.get(ref.name.lower(), set()):
                raise ValueError('SQL_CORRELATION: 子查询条件 '+eq.sql()+' 中未限定的字段会绑定到子查询本表，'
                                 '实际变成本表字段与自身比较，可能把全部记录排除或全部保留。'
                                 '请给外层表和内层表都设置别名，明确写 内层别名.字段 = 外层别名.字段；'
                                 '保留原日期和业务条件，不要把空结果当作没有数据。')


def _outer_total(scope):
    if scope.parent is None or not scope.is_subquery or not isinstance(scope.expression, (exp.Select, exp.SetOperation)):
        return None
    scalar = scope.expression.parent
    if not isinstance(scalar, exp.Subquery):
        return None
    aggregate = scalar.find_ancestor(exp.Sum, exp.Select, exp.In, exp.Exists, exp.Any, exp.All)
    if isinstance(aggregate, exp.Sum) and aggregate.find_ancestor(exp.Select) is scope.parent.expression:
        operand = aggregate.this
        while isinstance(operand, exp.Paren):
            operand = operand.this
        return scalar, operand


def _intrinsic_single_row(select):
    limit = select.args.get('limit')
    order = select.args.get('order')
    if isinstance(limit, exp.Limit) and (limit.expression == exp.Literal.number(0)
            or (limit.expression == exp.Literal.number(1) and order and order.expressions)):
        return True
    if not isinstance(select, exp.Select) or select.args.get('group') is not None or len(select.expressions) != 1:
        return False
    if not (select.args.get('from') or select.args.get('from_')):
        return True
    value = select.expressions[0].unalias().unnest()
    # A constant expresses presence; a second matching row cannot change it.
    if isinstance(value, (exp.Literal, exp.Boolean, exp.Null)):
        return True
    return any(aggregate.find_ancestor(exp.Select, exp.Window) is select
               for aggregate in value.find_all(exp.AggFunc))


def _single_source(scope):
    if not isinstance(scope.expression, exp.Select) or scope.expression.args.get('joins'):
        return None
    refs = scope.selected_sources
    return next(iter(refs.items())) if len(refs) == 1 else None


def _direct_origins(ref, names, source):
    """Trace key names only through single-table projections."""
    from .snapshot_scope import _physical_table

    if isinstance(ref, Scope):
        if not (selected := _single_source(ref)) or ref.outer_columns or ref.expression.args.get('group'):
            return None
        alias, (_, upstream) = selected
        projections = ref.expression.selects
        if len(projections) == 1 and projections[0].is_star:
            return _direct_origins(upstream, names, source)
        mapped = []
        for name in names:
            items = [item.unalias().unnest() for item in projections if item.alias_or_name.casefold() == name.casefold()]
            if len(items) != 1 or not isinstance(column := items[0], exp.Column) or column.is_star or column.db or column.catalog:
                return None
            if column.table and column.table.casefold() != alias.casefold():
                return None
            mapped.append(column.name)
        return _direct_origins(upstream, mapped, source)
    if not (name := _physical_table(ref, source, 'sqlite')):
        return None
    table = next(t for t in source['tables'] if t['name'] == name)
    by_name = {column['name'].casefold(): column for column in table['columns']}
    if all(name.casefold() in by_name for name in names):
        return table, [by_name[name.casefold()] for name in names]
    return None


def _correlation_keys(scope, inner_alias, outer_alias):
    from .join_grain import _and_atoms

    where = scope.expression.args.get('where')
    keys = []
    for atom in _and_atoms(where.this if where else None):
        if not isinstance(atom, exp.EQ):
            continue
        left, right = (value.unnest() for value in (atom.this, atom.expression))
        if any(not isinstance(col, exp.Column) or col.db or col.catalog or col.is_star for col in (left, right)):
            continue
        sides = (left.table.casefold(), right.table.casefold())
        if sides == (inner_alias.casefold(), outer_alias.casefold()):
            keys.append((left.name, right.name, True))
        elif sides == (outer_alias.casefold(), inner_alias.casefold()):
            keys.append((right.name, left.name, False))
    if len({a.casefold() for a, _, _ in keys}) == len({b.casefold() for _, b, _ in keys}) == len(keys):
        return keys
    return []


def _additive_child_value(value, inner_alias, inner_ref, source):
    columns = list(value.find_all(exp.Column))
    scalar_functions = (exp.Case, exp.If, exp.Cast, exp.TryCast, exp.Coalesce, exp.Round,
                        exp.Abs, exp.Nullif, exp.Replace, exp.Trim)
    return (bool(columns) and not value.find(exp.Select, exp.Window, exp.AggFunc)
            and not any(not isinstance(fn, scalar_functions) for fn in value.find_all(exp.Func))
            and all(not (col.db or col.catalog or col.is_star) and col.table.casefold() in ('', inner_alias.casefold())
                    for col in columns)
            and _direct_origins(inner_ref, [col.name for col in columns], source) is not None)


def normalize_scalar_totals(sql, source, *, engine=None, ctx=None):
    """SQLite's scalar SELECT keeps its first row. Preserve additive child rows."""
    if sqlglot_name(source['dialect']) != 'sqlite' or not re.search(r'\bsum\s*\(', sql, re.I):
        return sql
    tree = sqlglot.parse_one(sql, read='sqlite')
    changed = False
    for scope in traverse_scope(tree):
        enclosing = _outer_total(scope)
        if not enclosing or _intrinsic_single_row(scope.expression):
            continue
        outer = scope.parent
        outer_aliases = {name.casefold() for name in outer.selected_sources}
        if not any(col.table.casefold() in outer_aliases for col in scope.external_columns if col.table):
            continue
        scalar, operand = enclosing
        inner_input, outer_input = _single_source(scope), _single_source(outer)
        keys = []
        if inner_input and outer_input:
            inner_alias, (_, inner_ref) = inner_input
            outer_alias, (_, outer_ref) = outer_input
            keys = _correlation_keys(scope, inner_alias, outer_alias)
            if keys:
                inner_keys, outer_keys, left_sides = zip(*keys)
                inner = _direct_origins(inner_ref, inner_keys, source)
                parent = _direct_origins(outer_ref, outer_keys, source)
                if inner and parent and (shape := _scalar_additivity(inner, parent, left_sides, engine, ctx, source)):
                    child_single, parent_single = shape
                    if child_single:
                        continue
                    select = scope.expression
                    if (parent_single and scalar is operand and isinstance(select, exp.Select) and len(select.expressions) == 1
                            and not any(select.args.get(k) for k in ('distinct','group','having','limit','offset','order','qualify'))
                            and _additive_child_value(select.expressions[0].unalias().unnest(), inner_alias, inner_ref, source)
                            and all(col.table.casefold() == outer_alias.casefold() for col in scope.external_columns if col.table)):
                        select.set('expressions', [exp.Sum(this=select.expressions[0].unalias().unnest().copy())])
                        changed = True
                        continue
        error = '外层 SUM 的相关子查询可能返回多行，SQLite 只取首行。'
        if inner_input and outer_input and keys:
            names = '、'.join(inner_keys)
            error += f'请先按子表键「{names}」对原数值求 SUM，再按原关联键连接并按原条件汇总。'
        else:
            error += '请先按完整关联键对原子表数值求 SUM，再回连主表汇总。'
        raise ValueError('SQL_CORRELATION: '+error+'保留原时间范围及筛选，缺测保留为空。若该子查询本意只取一条，请写明确定的取行规则。')
    return tree.sql(dialect='sqlite') if changed else sql


def _scalar_additivity(inner, outer, left_sides, engine, ctx, source):
    from .db_probe import sqlite_scalar_additivity

    if engine is None or ctx is None:
        return None
    relation, child_keys = inner
    parent, parent_keys = outer
    return sqlite_scalar_additivity(engine, ctx, source, relation['name'], parent['name'],
                                     [c['name'] for c in child_keys], [c['name'] for c in parent_keys], left_sides)
