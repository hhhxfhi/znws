"""Check temporal invariants, not equality to a pack's example SQL.

This adapter only applies to the known certificate schema. Other sources use
the ordinary authorized, read-only SQL pipeline. Encoding, joins, dimensions
and business calculations remain available to the query author.
"""
from datetime import date
import sqlglot
from sqlglot import exp
from .day_bounds import inclusive_upper_day


def _all_paths(node, predicate):
    if node is None:
        return False
    if isinstance(node, (exp.Where, exp.Paren)):
        return _all_paths(node.this, predicate)
    if isinstance(node, exp.And):
        return _all_paths(node.this, predicate) or _all_paths(node.expression, predicate)
    if isinstance(node, exp.Or):
        return _all_paths(node.this, predicate) and _all_paths(node.expression, predicate)
    return predicate(node)


def compatible(sql, dialect, spec, end):
    """Accept independently written SQL with per-record snapshot predicates.

    Conservative on temporal scope: equivalent expressions this checker cannot
    establish receive a repairable diagnostic, never a silently substituted SQL.
    """
    tree = sqlglot.parse_one(sql, read=dialect)
    cert = spec['cert_table'].split('.')[-1].lower()
    refs = [t for t in tree.find_all(exp.Table) if t.name.lower() == cert]
    if not refs:
        return False

    def bound(node, alias, field, upper):
        if not isinstance(node, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
            return False
        left, right = node.this, node.expression
        direct = isinstance(node, (exp.LT, exp.LTE)) if upper else isinstance(node, (exp.GT, exp.GTE))
        column, value = (left, right) if direct else (right, left)
        cols = list(column.find_all(exp.Column))
        if len(cols) != 1 or cols[0].name.lower() != field or cols[0].table and cols[0].table.lower() != alias:
            return False
        # Cast/date extraction is supported; arithmetic may change the cutoff.
        if not isinstance(column, (exp.Column, exp.Cast, exp.Date)):
            return False
        if any(value.find_all(exp.Column)):
            return False
        if upper:
            return inclusive_upper_day(value, strict=isinstance(node, (exp.LT, exp.GT)), today=date.today()) == end
        # An inclusive expiry at the requested date; no guessing time-zone or
        # non-ISO storage semantics. The model can CAST a verified date field.
        if not isinstance(node, (exp.GTE, exp.LTE)):
            return False
        while isinstance(value, (exp.Cast, exp.Paren)):
            value = value.this
        return isinstance(value, exp.Literal) and str(value.this) == end.isoformat()

    for ref in refs:
        select = ref.find_ancestor(exp.Select)
        if select is None:
            return False
        where = select.args.get('where')
        alias = ref.alias_or_name.lower()
        if not all(_all_paths(where, lambda n, field=field, upper=upper: bound(n, alias, field, upper))
                   for field, upper in [('fzsj', True), ('yxq', False)]):
            return False
    return True
