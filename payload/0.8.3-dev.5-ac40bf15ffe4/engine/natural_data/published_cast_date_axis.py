"""Recognize an authorized text date through its published SQL conversion."""
from collections import Counter
from datetime import timedelta
import re

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from .catalog_view import entry_code

_TEXT_TYPE = re.compile(r'(?:TEXT|(?:N?VARCHAR2?|CHARACTER VARYING|N?CHAR|CHARACTER)(?:\(\d+\))?)', re.I)
_OPERATIONS = (exp.GTE, exp.GT, exp.LT, exp.LTE, exp.Between)
_DAY = timedelta(days=1)


def _relation(tree, source, table):
    from .monthly_sql_scope import _source_table
    if (not isinstance(tree, exp.Select) or tree.args.get('joins')
            or tree.find(exp.Subquery, exp.CTE, exp.SetOperation)):
        return None
    ref = tree.args.get('from')
    if not ref or _source_table(ref.this, source) != table or ref.this.alias_column_names:
        return None
    if ref.this.args.get('sample') or ref.this.args.get('version'):
        return None
    return ref.this


def _column(node, relation):
    node = node.unnest()
    if (isinstance(node, exp.Column) and not (node.db or node.catalog)
            and (not node.table or node.table.casefold() == relation.alias_or_name.casefold())):
        return node
    return None


def _day_value(node, dialect):
    node = node.unnest()
    if (dialect == 'postgres' and type(node) is exp.Cast and node.to.is_type('date')
            and not any(node.args.get(key) for key in ('format', 'safe', 'action', 'default'))):
        return node.this.unnest()
    if (dialect == 'sqlite' and type(node) is exp.Date and not node.args.get('zone')
            and not node.expressions and not node.args.get('format')):
        return node.this.unnest()
    return None


def _calendar_literal(node, dialect):
    from .monthly_sql_scope import _literal_day
    value = _day_value(node, dialect)
    return _literal_day(value if value is not None else node.unnest(), dialect) if node is not None else None


def _registered_typed_day(node, dialect):
    node = node.unnest() if node is not None else None
    return _calendar_literal(node, dialect) if node is not None and _day_value(node, dialect) is not None else None


def _literal_upper_cap(node, dialect):
    if dialect != 'postgres' or not isinstance(node, exp.Least):
        return None
    values = [node.this, *node.expressions]
    if len(values) != 2 or sum(type(value.unnest()) is exp.CurrentDate for value in values) != 1:
        return None
    return next((day for value in values if (day := _registered_typed_day(value, dialect))), None)


def _bounds(tree, relation, field, dialect, *, registered=False, allow_upper_cap=False, require_upper=True):
    from .monthly_sql_scope import _not_null_operand
    from .request_scope import _and_atoms
    where = tree.args.get('where')
    if where is None:
        return None
    lowers, uppers, recognized = [], [], set()
    for atom in _and_atoms(where.this):
        if (operand := _not_null_operand(atom)) is not None:
            column = _column(_day_value(operand, dialect) or operand, relation)
            if column is not None and column.name.casefold() == field.casefold():
                recognized.add(id(column))
            continue
        if not isinstance(atom, _OPERATIONS):
            continue
        value = _day_value(atom.this, dialect)
        literal = _calendar_literal
        if value is None:
            if not registered:
                continue
            value, literal = atom.this, _registered_typed_day
        if (column := _column(value, relation)) is None or column.name.casefold() != field.casefold():
            continue
        if isinstance(atom, exp.Between):
            lo, hi = (literal(atom.args.get(key), dialect) for key in ('low', 'high'))
            if not lo or not hi:
                continue
            try:
                upper = hi + _DAY
            except OverflowError:
                continue
            lowers.append(lo)
            uppers.append(upper)
        elif day := (literal(atom.expression, dialect) or (
                _literal_upper_cap(atom.expression, dialect)
                if allow_upper_cap and isinstance(atom, (exp.LT, exp.LTE)) else None)):
            try:
                day += _DAY if isinstance(atom, (exp.GT, exp.LTE)) else timedelta(0)
            except OverflowError:
                continue
            (lowers if isinstance(atom, (exp.GTE, exp.GT)) else uppers).append(day)
        else:
            continue
        recognized.add(id(column))
    if not lowers or require_upper and not uppers:
        return None
    unknown = (any(tree.args.get(key) for key in ('having', 'qualify', 'limit', 'offset'))
               or any((column := _column(ref, relation)) is not None and column.name.casefold() == field.casefold()
                      and id(column) not in recognized for ref in where.find_all(exp.Column)))
    return max(lowers), min(uppers, default=None), unknown


def _source_examples(source):
    """Yield only unique term/example pairs from the same effective publication."""
    sid = source['id']

    def index(rows, title):
        for row in rows:
            if row.get('source_id') == sid and row.get('status') != 'review' and row.get('origin'):
                if code := entry_code(row, title):
                    yield (row['origin'], code), row

    terms = Counter(key for key, _ in index(source.get('terms', []), 'name'))
    examples = list(index(source.get('examples', []), 'question'))
    totals = Counter(key for key, _ in examples)
    return (example for key, example in examples if terms[key] == totals[key] == 1)


def date_axis(tree, source, table, dialect, *, allow_upper_cap=False, require_upper=True):
    """Return a single proven physical text column and its normalized date bounds."""
    if (dialect not in ('postgres', 'sqlite') or not (source.get('business_project') or {}).get('version')
            or not (relation := _relation(tree, source, table))):
        return None
    candidates = {column['name']: (column, bounds) for column in table.get('columns', [])
                  if _TEXT_TYPE.fullmatch(str(column.get('type') or '').strip())
                  and (bounds := _bounds(tree, relation, column['name'], dialect, allow_upper_cap=allow_upper_cap,
                                         require_upper=require_upper)) is not None}
    if len(candidates) != 1:
        return None
    field = next(iter(candidates))
    for example in _source_examples(source):
        try:
            statement = parse_one(example.get('logical_sql') or '', read=dialect)
        except SqlglotError:
            continue
        if ((ref := _relation(statement, source, table))
                and (bounds := _bounds(statement, ref, field, dialect, registered=True)) and not bounds[2]):
            return candidates[field]
    return None
