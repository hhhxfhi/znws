"""Actual-value enum/categorical discovery, mismatch protection, and filter binding."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .db_probe import distinct_column_values
from .vendor.dialect import sqlglot_name

_DATE_LIKE = re.compile(r'^\d{4}(?:[-/.]\d{1,2}(?:[-/.]\d{1,2})?)?(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$')
_NUMERIC_LIKE = re.compile(r'^-?\d+(?:\.\d+)?$')
_DATE_COL_HINTS = ('date', 'time', 'rq', 'sj', 'nf', 'yf', 'year', 'month', 'day', '日期', '时间', '年份', '月份')
_STATE_COLUMN = re.compile(r'(?:^|_)(?:status|state|zt|code)(?:$|[ _])|状态|编码|代码', re.I)


def _translation_risk(col_meta, requested, stored):
    # A small observed set is not a declared enum domain. An absent category,
    # person or published filter may legitimately produce no matching rows.
    # Only a state/code column with a cross-language literal is a translation
    # conflict; ordinary empty-result predicates remain executable.
    declared = str(col_meta.get('type') or '').lower().startswith('enum')
    state = _STATE_COLUMN.search(str(col_meta.get('name') or '') + ' ' + str(col_meta.get('label') or ''))
    return bool((declared or state) and re.search(r'[\u3400-\u9fff]', requested)
                and stored and all(not re.search(r'[\u3400-\u9fff]', v) for v in stored))


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
                out[short] = None
            else:
                out[short] = t
    return out


def _resolve_physical_col(node, scope: Scope, table_map: dict[str, dict], projections: dict):
    while isinstance(node, (exp.Alias, exp.Paren)):
        node = node.this
    if not isinstance(node, exp.Column):
        return None
    col_name = node.name.lower()
    if node.table:
        refs = [ref for alias, ref in scope.sources.items() if alias.lower() == node.table.lower()]
    else:
        refs = list(scope.sources.values())
    found = []
    for ref in refs:
        if isinstance(ref, Scope):
            mapped = projections.get(id(ref), {}).get(col_name)
            if mapped is not None:
                found.append(mapped)
        elif isinstance(ref, exp.Table):
            meta = table_map.get('.'.join(part.name for part in ref.parts).lower())
            if meta:
                col_meta = next((c for c in meta.get('columns', []) if str(c.get('name', '')).lower() == col_name), None)
                if col_meta:
                    found.append((meta['name'], col_meta))
    return found[0] if len(found) == 1 else None


def _literal_strings(node, bound_params: dict) -> list[str] | None:
    if isinstance(node, exp.Literal):
        if node.is_number:
            return None
        return [str(node.this)]
    if isinstance(node, exp.Placeholder):
        key = str(node.name or node.this or '').lstrip(':')
        if key in bound_params and bound_params[key] is not None and not isinstance(bound_params[key], (int, float, bool)):
            return [str(bound_params[key])]
    return None


def _pattern_matches(pattern: str, value: str, *, case_insensitive: bool) -> bool:
    if '\\' in pattern:
        return False
    regex = ''.join('.*' if c == '%' else '.' if c == '_' else re.escape(c) for c in pattern)
    flags = re.S | (re.I if case_insensitive else 0)
    return re.fullmatch(regex, value, flags) is not None


def _looks_like_date_col(col_meta: dict, distinct_vals: list[str]) -> bool:
    cname = str(col_meta.get('name', '')).lower()
    if any(h in cname for h in _DATE_COL_HINTS):
        return True
    if distinct_vals and all(_DATE_LIKE.match(v.strip()) for v in distinct_vals[:10]):
        return True
    return False


def require_discovered_enum_values(sql: str, source: dict, *, engine=None, ctx=None, parameters=None) -> None:
    """Block categorical/status filters whose literal does not exist in actual stored DB values.

    Skips columns with configured `business_values` (already handled by `require_enum_values`).
    Never guesses codes for unknown labels and never treats unrecognized codes as empty data.
    """
    if not sql or not isinstance(source, dict) or engine is None or ctx is None:
        return
    dialect = sqlglot_name(source.get('dialect'))
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return

    bound = {p['name']: p.get('value') for p in (parameters or []) if isinstance(p, dict) and 'name' in p}
    table_map = _table_meta_map(source)
    projections = {}

    for scope in traverse_scope(tree):
        sel = scope.expression
        if not isinstance(sel, exp.Select):
            continue
        projections[id(scope)] = {
            item.alias_or_name.lower(): mapped
            for item in sel.selects
            if item.alias_or_name and (mapped := _resolve_physical_col(item, scope, table_map, projections)) is not None
        }
        for node in sel.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike):
            if node.find_ancestor(exp.Select) is not sel:
                continue
            if node.find_ancestor(exp.Case) or not node.find_ancestor(exp.Where, exp.Join, exp.Having):
                continue
            col_ref, values = None, None
            if isinstance(node, exp.In):
                col_ref = _resolve_physical_col(node.this, scope, table_map, projections)
                if node.expressions:
                    extracted = [_literal_strings(e, bound) for e in node.expressions]
                    if all(x is not None for x in extracted):
                        values = [x[0] for x in extracted]
            elif isinstance(node, (exp.EQ, exp.NEQ)):
                col_ref = _resolve_physical_col(node.this, scope, table_map, projections)
                values = _literal_strings(node.expression, bound)
                if col_ref is None and values is None:
                    col_ref = _resolve_physical_col(node.expression, scope, table_map, projections)
                    values = _literal_strings(node.this, bound)
            else:
                col_ref = _resolve_physical_col(node.this, scope, table_map, projections)
                values = _literal_strings(node.expression, bound)

            if not col_ref or not values:
                continue
            table_name, col_meta = col_ref
            if col_meta.get('business_values'):
                continue
            if not (str(col_meta.get('type') or '').lower().startswith('enum')
                    or _STATE_COLUMN.search(str(col_meta.get('name') or '') + ' ' + str(col_meta.get('label') or ''))):
                continue
            if any(_DATE_LIKE.match(v.strip()) or _NUMERIC_LIKE.match(v.strip()) for v in values):
                continue

            distinct_vals = distinct_column_values(engine, ctx, source, table_name, col_meta['name'], limit=51)
            if not distinct_vals or len(distinct_vals) > 50:
                continue
            if _looks_like_date_col(col_meta, distinct_vals):
                continue

            stored_set = set(distinct_vals)
            is_like = isinstance(node, (exp.Like, exp.ILike))
            ci = isinstance(node, exp.ILike)
            unmatched = []
            for v in values:
                if is_like:
                    if not any(_pattern_matches(v, dv, case_insensitive=ci) for dv in distinct_vals):
                        unmatched.append(v)
                else:
                    if v not in stored_set:
                        unmatched.append(v)

            unmatched = [v for v in unmatched if _translation_risk(col_meta, v, distinct_vals)]
            if unmatched:
                shown = ', '.join(repr(x) for x in distinct_vals[:20])
                col_disp = f'{table_name}.{col_meta["name"]}'
                bad_disp = '、'.join(repr(x) for x in unmatched)
                raise ValueError(
                    f'ENUM_VALUE_MISMATCH: 字段「{col_disp}」中不存在筛选值 {bad_disp}；'
                    f'库内实际存储的唯一取值为：[{shown}]。'
                    f'若实际取值与用户条件存在明确语义对应（如英文编码对应中文状态），请改用真实存储值重写 SQL 后重试，并保留原有日期、地区与筛选条件；'
                    f'若实际取值为无法确定业务含义的未知编码（如无语义数字或字母代码），严禁猜测编码，也严禁将不匹配当作无数据，请向用户说明实际取值并请求确认。'
                )


def _and_atoms(node) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, (exp.Where, exp.Paren)):
        return _and_atoms(node.this)
    if isinstance(node, exp.And):
        return [*_and_atoms(node.this), *_and_atoms(node.expression)]
    return [node]


def extract_verified_filters(sql: str, source: dict, parameters=None) -> list[dict]:
    """Extract literal EQ/NEQ/IN/NOT IN filters from top-level WHERE for scope inheritance."""
    if not sql or not isinstance(source, dict):
        return []
    dialect = sqlglot_name(source.get('dialect'))
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return []
    if not isinstance(tree, exp.Select):
        return []

    bound = {p['name']: p.get('value') for p in (parameters or []) if isinstance(p, dict) and 'name' in p}
    table_map = _table_meta_map(source)
    projections = {}
    scopes = list(traverse_scope(tree))
    for sc in scopes[:-1]:
        if isinstance(sc.expression, exp.Select):
            projections[id(sc)] = {
                item.alias_or_name.lower(): mapped
                for item in sc.expression.selects
                if item.alias_or_name and (mapped := _resolve_physical_col(item, sc, table_map, projections)) is not None
            }
    top_scope = scopes[-1] if scopes else None
    if top_scope is None:
        return []

    out = []
    for atom in _and_atoms(tree.args.get('where')):
        negative = isinstance(atom, (exp.NEQ, exp.Not))
        expr = atom.this if isinstance(atom, exp.Not) else atom
        if not isinstance(expr, (exp.EQ, exp.NEQ, exp.In)):
            continue
        col_ref, values = None, None
        if isinstance(expr, exp.In):
            col_ref = _resolve_physical_col(expr.this, top_scope, table_map, projections)
            if expr.expressions:
                extracted = [_literal_strings(e, bound) for e in expr.expressions]
                if all(x is not None for x in extracted):
                    values = [x[0] for x in extracted]
        else:
            col_ref = _resolve_physical_col(expr.this, top_scope, table_map, projections)
            values = _literal_strings(expr.expression, bound)
            if col_ref is None and values is None:
                col_ref = _resolve_physical_col(expr.expression, top_scope, table_map, projections)
                values = _literal_strings(expr.this, bound)
        if not col_ref or not values:
            continue
        table_name, col_meta = col_ref
        if any(_DATE_LIKE.match(v.strip()) or _NUMERIC_LIKE.match(v.strip()) for v in values):
            continue
        if _looks_like_date_col(col_meta, values):
            continue
        out.append({
            'table': table_name,
            'column': col_meta['name'],
            'kind': 'filter',
            'op': 'not_in' if negative else 'in',
            'values': sorted(set(values)),
            'discovered': True,
        })
    return out
