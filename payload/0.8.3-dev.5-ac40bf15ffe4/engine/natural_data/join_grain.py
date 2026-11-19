"""Join cardinality & measure grain verification for unfamiliar schemas.

Proves whether each joined relation is unique on its join key(s) using:
1. Registered business relations (when present),
2. Physical PRIMARY KEY / UNIQUE constraints,
3. CTE / derived subquery GROUP BY or DISTINCT structure,
4. Bounded read-only actual data duplicate checks (when physical keys do not prove uniqueness).

Detects 1:N / N:M fan-out that inflates parent-side aggregates (SUM, AVG,
non-distinct COUNT, and SUM(DISTINCT ...)) and provides an executable CTE
pre-aggregation recovery path.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from .db_probe import has_duplicate_keys
from .vendor.dialect import sqlglot_name


def _table_meta_map(source: dict) -> dict[str, dict]:
    out = {}
    for t in source.get('tables', []):
        name = str(t.get('name', '')).lower()
        if not name:
            continue
        out[name] = t
        short = name.split('.')[-1]
        if short != name:
            if short in out and out[short] is not t:
                out[short] = None  # ambiguous across schemas
            else:
                out[short] = t
    return out


def _registered_unique_sides(source: dict, left_table: str, right_table: str, pairs: list[tuple[str, str]]):
    """Return (left_unique, right_unique) from registered relations if an exact match exists, else (None, None)."""
    pair_set = {frozenset(((left_table, lc), (right_table, rc))) for lc, rc in pairs}
    for r in source.get('business_dictionary', []):
        if r.get('kind') != 'relation':
            continue
        t1 = str(r.get('table', '')).split('.')[-1].lower()
        t2 = str(r.get('to_table', '')).split('.')[-1].lower()
        if {t1, t2} != {left_table, right_table}:
            continue
        expected = {
            frozenset(((t1, str(f).lower()), (t2, str(t).lower())))
            for f, t in zip(r.get('from_columns') or [], r.get('to_columns') or [])
        }
        if not expected or not (expected <= pair_set):
            continue
        card = r.get('cardinality')
        if card == 'one_to_one':
            return True, True
        if card == 'one_to_many':
            return (True, False) if t1 == left_table else (False, True)
        if card == 'many_to_one':
            return (False, True) if t1 == left_table else (True, False)
    return None, None


def _physical_table_unique(table_meta: dict, cols: list[str]) -> bool:
    col_set = {c.lower() for c in cols}
    pk = [str(k).lower() for k in (table_meta.get('primary_key') or []) if k]
    if pk and set(pk) <= col_set:
        return True
    for uk in (table_meta.get('unique_keys') or []):
        uk_set = {str(k).lower() for k in uk if k}
        if uk_set and uk_set <= col_set:
            return True
    return False


def _distinct_on_unique(scope: Scope, partition: exp.Tuple, cols: list[str]) -> bool:
    """A projected PostgreSQL partition key survives DISTINCT ON as a unique tuple."""
    if len(scope.selected_sources) != 1 or not partition.expressions:
        return False
    projections = [normalize_identifiers(item.copy(), dialect='postgres') for item in scope.expression.selects]
    items = [item.unalias().unnest() for item in projections]
    # A non-column expression can shadow an input name or return several rows.
    if not all(isinstance(item, exp.Column) and not (item.is_star or item.db or item.catalog) for item in items):
        return False
    names = [item.alias_or_name for item in projections]
    if any(not name for name in names) or len(set(names)) != len(names):
        return False
    outer = scope.outer_columns
    # Scope retains outer alias spellings without the quoted/unquoted distinction.
    if len(outer) > len(names) or any(name != name.lower() for name in outer):
        return False
    outputs = [*outer, *names[len(outer):]]
    if len(set(outputs)) != len(outputs):
        return False

    def same_column(item, key):
        return item.name == key.name and (not item.table or not key.table or item.table == key.table)

    for expression in partition.expressions:
        key = normalize_identifiers(expression.copy(), dialect='postgres').unnest()
        if not isinstance(key, exp.Column) or key.db or key.catalog or key.is_star:
            return False
        if not key.table and key.name in names and not same_column(items[names.index(key.name)], key):
            # An unqualified name could resolve to a different SELECT alias.
            return False
        if not any(output in cols and same_column(item, key) for output, item in zip(outputs, items)):
            return False
    return True


def _scope_unique_on(scope_obj: Scope, cols: list[str], table_map: dict[str, dict], *, engine=None, ctx=None, source=None) -> bool | None:
    """Return True if a CTE/subquery scope is guaranteed unique on `cols`, False if known non-unique, None if unknown."""
    sel = scope_obj.expression
    if not isinstance(sel, exp.Select):
        return None
    col_set = {c.lower() for c in cols}

    # Map output alias -> underlying expression
    alias_expr = {}
    for item in sel.selects:
        out_name = item.alias_or_name
        if out_name:
            inner = item.this if isinstance(item, exp.Alias) else item
            alias_expr[out_name.lower()] = inner

    group = sel.args.get('group')
    if group and not (group.args.get('grouping_sets') or group.args.get('cube') or group.args.get('rollup')):
        group_names = set()
        simple_group = True
        for g in group.expressions:
            if isinstance(g, exp.Column):
                g_name = g.name.lower()
                # Check if g matches an output alias directly or via column expression
                matched_out = [k for k, v in alias_expr.items()
                               if k == g_name or (isinstance(v, exp.Column) and v.name.lower() == g_name)]
                if matched_out:
                    group_names.add(matched_out[0])
                else:
                    group_names.add(g_name)
            else:
                simple_group = False
                break
        if simple_group and group_names and group_names <= col_set:
            return True

    if distinct := sel.args.get('distinct'):
        partition = distinct.args.get('on')
        if partition:
            if sqlglot_name((source or {}).get('dialect')) == 'postgres' and _distinct_on_unique(scope_obj, partition, cols):
                return True
        elif alias_expr and set(alias_expr) <= col_set:
            return True

    # Single-table passthrough subquery without joins or aggregations
    if not sel.args.get('joins') and not group and not list(sel.find_all(exp.AggFunc)):
        tables = [ref for ref in scope_obj.sources.values() if isinstance(ref, exp.Table)]
        if len(tables) == 1:
            tname = '.'.join(part.name for part in tables[0].parts).lower()
            meta = table_map.get(tname)
            if meta:
                mapped_cols = []
                for c in cols:
                    expr = alias_expr.get(c.lower())
                    if isinstance(expr, exp.Column):
                        mapped_cols.append(expr.name.lower())
                    else:
                        mapped_cols.append(c.lower())
                return _ref_unique_on(tables[0], mapped_cols, table_map, engine=engine, ctx=ctx, source=source)

    return None


def _ref_unique_on(ref, cols: list[str], table_map: dict[str, dict], *, engine=None, ctx=None, source=None) -> bool | None:
    if isinstance(ref, Scope):
        return _scope_unique_on(ref, cols, table_map, engine=engine, ctx=ctx, source=source)
    if isinstance(ref, exp.Table):
        tname = '.'.join(part.name for part in ref.parts).lower()
        meta = table_map.get(tname)
        if not meta:
            return None
        if _physical_table_unique(meta, cols):
            return True
        if engine is not None and ctx is not None and source is not None:
            dup = has_duplicate_keys(engine, ctx, source, meta['name'], cols)
            if dup is not None:
                return not dup
        # Fallback without engine: if table has a PK that is disjoint from join cols and has an FK on join cols
        pk = {str(k).lower() for k in (meta.get('primary_key') or []) if k}
        if pk and not (pk <= {c.lower() for c in cols}):
            for fk in (meta.get('foreign_keys') or []):
                fk_cols = {str(c).lower() for c in (fk.get('constrained_columns') or [])}
                if fk_cols and fk_cols <= {c.lower() for c in cols}:
                    return False
    return None


def _and_atoms(node) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, exp.Paren):
        return _and_atoms(node.this)
    if isinstance(node, exp.And):
        return [*_and_atoms(node.this), *_and_atoms(node.expression)]
    return [node]


def _resolve_col_alias(col: exp.Column, scope_sources: dict, table_map: dict[str, dict] | None = None) -> str | None:
    if col.table:
        key = col.table.lower()
        return key if key in scope_sources else None
    if not table_map:
        return None
    col_name = col.name.lower()
    owners = []
    for alias, ref in scope_sources.items():
        if isinstance(ref, exp.Table):
            meta = table_map.get('.'.join(part.name for part in ref.parts).lower())
            if meta and any(str(c.get('name', '')).lower() == col_name for c in meta.get('columns', [])):
                owners.append(alias)
        elif isinstance(ref, Scope) and isinstance(ref.expression, exp.Select):
            if any((s.alias_or_name or '').lower() == col_name for s in ref.expression.selects):
                owners.append(alias)
    return owners[0] if len(owners) == 1 else None


def _extract_join_edges(scope_obj: Scope, table_map: dict[str, dict], *, engine=None, ctx=None, source=None):
    """Build join graph edges between aliases in `scope_obj`.

    Returns list of dicts:
      {'left': alias_l, 'right': alias_r, 'left_cols': [...], 'right_cols': [...],
       'left_unique': bool|None, 'right_unique': bool|None}
    """
    sel = scope_obj.expression
    joins = sel.args.get('joins') or []
    if not joins:
        return []

    sources = {k.lower(): v for k, v in scope_obj.sources.items()}
    postgres = sqlglot_name((source or {}).get('dialect')) == 'postgres'

    def join_key(column):
        return column.name if postgres and column.this.args.get('quoted') else column.name.lower()

    edges = []
    for join in joins:
        on = join.args.get('on')
        if on is None:
            continue
        by_pair: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for atom in _and_atoms(on):
            if not isinstance(atom, exp.EQ):
                continue
            if not (isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Column)):
                continue
            a1 = _resolve_col_alias(atom.this, sources, table_map)
            a2 = _resolve_col_alias(atom.expression, sources, table_map)
            if not a1 or not a2 or a1 == a2:
                continue
            if a1 > a2:
                a1, a2 = a2, a1
                c1, c2 = join_key(atom.expression), join_key(atom.this)
            else:
                c1, c2 = join_key(atom.this), join_key(atom.expression)
            by_pair.setdefault((a1, a2), []).append((c1, c2))

        for (a1, a2), pairs in by_pair.items():
            left_cols = [p[0] for p in pairs]
            right_cols = [p[1] for p in pairs]
            ref1, ref2 = sources[a1], sources[a2]
            u1, u2 = (None, None)
            if isinstance(ref1, exp.Table) and isinstance(ref2, exp.Table):
                u1, u2 = _registered_unique_sides(
                    source or {},
                    ref1.name.lower(),
                    ref2.name.lower(),
                    [(a.lower(), b.lower()) for a, b in pairs],
                )
            if u1 is None:
                u1 = _ref_unique_on(ref1, left_cols, table_map, engine=engine, ctx=ctx, source=source)
            if u2 is None:
                u2 = _ref_unique_on(ref2, right_cols, table_map, engine=engine, ctx=ctx, source=source)
            edges.append({
                'left': a1,
                'right': a2,
                'left_cols': left_cols,
                'right_cols': right_cols,
                'left_unique': u1,
                'right_unique': u2,
            })
    return edges


def _reachable_without(edges, start: str, skip_idx: int) -> set[str]:
    visited = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for i, e in enumerate(edges):
            if i == skip_idx:
                continue
            if e['left'] == cur and e['right'] not in visited:
                visited.add(e['right'])
                stack.append(e['right'])
            elif e['right'] == cur and e['left'] not in visited:
                visited.add(e['left'])
                stack.append(e['left'])
    return visited


def _fanned_by(edges) -> dict[str, list[tuple[str, list[str]]]]:
    """Map each alias -> list of (multiplier_alias, join_cols_on_multiplier) that multiply its rows."""
    fanned: dict[str, list[tuple[str, list[str]]]] = {}
    for i, e in enumerate(edges):
        if e['right_unique'] is False:
            for alias in _reachable_without(edges, e['left'], i):
                fanned.setdefault(alias, []).append((e['right'], e['right_cols']))
        if e['left_unique'] is False:
            for alias in _reachable_without(edges, e['right'], i):
                fanned.setdefault(alias, []).append((e['left'], e['left_cols']))
    return fanned


def _table_display(ref, alias: str) -> str:
    if isinstance(ref, exp.Table):
        return ref.name
    return alias


def _count_star_preserves_fact_rows(scope, edges, table_map):
    """One equality join to a unique right key does not multiply FROM rows."""
    select = scope.expression
    joins = select.args.get('joins') or []
    outer_from = select.args.get('from') or select.args.get('from_')
    if len(joins) != 1 or len(edges) != 1 or outer_from is None or not isinstance(outer_from.this, exp.Table):
        return False
    fact = outer_from.this.alias_or_name.lower()
    physical = next((ref for alias, (_, ref) in scope.selected_sources.items() if alias.lower() == fact), None)
    if not isinstance(physical, exp.Table) or not table_map.get('.'.join(part.name for part in physical.parts).lower()):
        return False
    join = joins[0]
    if join.side not in ('', 'LEFT') or join.kind not in ('', 'INNER', 'OUTER'):
        return False
    right = join.this.alias_or_name.lower()
    edge = edges[0]
    if len(_and_atoms(join.args.get('on'))) != len(edge['left_cols']):
        return False
    if (edge['left'], edge['right']) == (fact, right):
        return edge['right_unique'] is True
    return (edge['left'], edge['right']) == (right, fact) and edge['left_unique'] is True


def check_join_grain(sql: str, source: dict, *, engine=None, ctx=None) -> list[dict]:
    """Return grain conflict findings if any join fan-out inflates parent-side aggregates."""
    if not sql or not isinstance(source, dict):
        return []
    dialect = sqlglot_name(source.get('dialect'))
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return []

    table_map = _table_meta_map(source)
    findings = []
    seen_msgs = set()

    for scope_obj in traverse_scope(tree):
        sel = scope_obj.expression
        if not isinstance(sel, exp.Select):
            continue
        edges = _extract_join_edges(scope_obj, table_map, engine=engine, ctx=ctx, source=source)
        if not edges:
            continue
        fanned = _fanned_by(edges)
        if not fanned:
            continue

        sources = {k.lower(): v for k, v in scope_obj.sources.items()}
        # Inspect aggregates belonging directly to this SELECT (not nested subqueries)
        for agg in sel.find_all(exp.Sum, exp.Avg, exp.Count):
            if agg.find_ancestor(exp.Select) is not sel:
                continue
            if isinstance(agg, exp.Count):
                # COUNT(DISTINCT col) is safe; COUNT(*) or COUNT(col) on a fanned-out table is inflated
                if isinstance(agg.this, exp.Distinct):
                    continue
                if isinstance(agg.this, exp.Star):
                    if _count_star_preserves_fact_rows(scope_obj, edges, table_map):
                        continue
                    multipliers = [(a, ms[0]) for a, ms in fanned.items() if ms]
                    if multipliers:
                        parent_alias, (mult_alias, mult_cols) = multipliers[0]
                        mult_name = _table_display(sources.get(mult_alias), mult_alias)
                        join_keys = ', '.join(mult_cols)
                        msg = (
                            f'明细表「{mult_name}」按 ({join_keys}) 关联后存在一对多放大，'
                            f'直接执行 COUNT(*) 会重复计数。请使用 COUNT(DISTINCT 主表主键) 或先按 ({join_keys}) 在 CTE 中预聚合「{mult_name}」。'
                        )
                        if msg not in seen_msgs:
                            seen_msgs.add(msg)
                            findings.append({'area': 'grain', 'state': 'conflict', 'label': '发现冲突', 'detail': msg})
                    continue

            # SUM / SUM(DISTINCT ...) / AVG / non-distinct COUNT(col)
            is_distinct_sum = isinstance(agg, exp.Sum) and isinstance(agg.this, exp.Distinct)
            cols = [c for c in agg.find_all(exp.Column) if c.find_ancestor(exp.Select) is sel]
            for col in cols:
                alias = _resolve_col_alias(col, sources, table_map)
                if not alias or alias not in fanned:
                    continue
                parent_name = _table_display(sources.get(alias), alias)
                mult_alias, mult_cols = fanned[alias][0]
                mult_name = _table_display(sources.get(mult_alias), mult_alias)
                join_keys = ', '.join(mult_cols)
                agg_sql = agg.sql(dialect=dialect)
                if is_distinct_sum:
                    msg = (
                        f'主表「{parent_name}」与明细表「{mult_name}」按 ({join_keys}) 关联后为一对多关系，'
                        f'禁止使用 {agg_sql}（SUM(DISTINCT ...) 会误删不同实体的相同金额）。'
                        f'请改用 CTE 先按 ({join_keys}) 预聚合「{mult_name}」（例如：WITH child_agg AS (SELECT {join_keys}, SUM(...) FROM {mult_name} GROUP BY {join_keys})），'
                        f'再与「{parent_name}」LEFT JOIN。'
                    )
                else:
                    msg = (
                        f'主表「{parent_name}」与明细表「{mult_name}」按 ({join_keys}) 关联后为一对多关系（{mult_name} 存在多条明细），'
                        f'直接对「{parent_name}」计算 {agg_sql} 会导致父表金额/计数被重复放大。'
                        f'禁止使用 SUM(DISTINCT ...) 掩盖重复；请改用 CTE 先按 ({join_keys}) 预聚合「{mult_name}」'
                        f'（例如：WITH child_agg AS (SELECT {join_keys}, SUM(...) AS ... FROM {mult_name} GROUP BY {join_keys})），'
                        f'再与「{parent_name}」LEFT JOIN，或将主表与子表分别聚合后再关联。'
                    )
                if msg not in seen_msgs:
                    seen_msgs.add(msg)
                    findings.append({'area': 'grain', 'state': 'conflict', 'label': '发现冲突', 'detail': msg})
                    break

    return findings
