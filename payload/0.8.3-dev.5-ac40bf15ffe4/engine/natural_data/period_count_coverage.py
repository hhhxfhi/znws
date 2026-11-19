"""Distinguish empty fact periods from conditional zero in a scalar count."""
from datetime import date

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from .monthly_sql_scope import _DATE_TYPE, _WindowPostgres, _literal_day, _month_field
from .vendor.dialect import sqlglot_name


def _terms(predicate):
    if predicate is None:
        return []
    predicate = predicate.unnest()
    if isinstance(predicate, exp.And):
        return [*_terms(predicate.this), *_terms(predicate.expression)]
    return [predicate]


def _month_bounds(predicate, table, alias, dialect):
    columns = {(item['name'].casefold() if dialect == 'sqlite' else item['name']) for item in table['columns']
               if _DATE_TYPE.fullmatch(item.get('type') or '')}
    def field(column):
        return (column.name if isinstance(column, exp.Column) and not (column.db or column.catalog)
                and column.table in ('', alias) and column.name in columns else None)

    bounds, equal_months, components, remaining = {}, [], {}, []
    for atom in _terms(predicate):
        column = atom.this
        if (isinstance(atom, (exp.GTE, exp.LT)) and (name := field(column))
                and (day := _literal_day(atom.expression, dialect)) is not None):
            bounds.setdefault(name, []).append((atom, day))
            continue
        if dialect == 'postgres' and isinstance(atom, exp.EQ):
            operand, value = atom.this.unnest(), atom.expression.unnest()
            if isinstance(operand, (exp.Literal, exp.Cast)):
                operand, value = value, operand
            if (isinstance(operand, (exp.TimestampTrunc, exp.DateTrunc)) and (name := field(_month_field(operand, dialect)))
                    and (day := _literal_day(value, dialect)) is not None and day.day == 1):
                equal_months.append((name, day, atom))
                continue
            if (isinstance(operand, exp.Extract) and (name := field(operand.expression))
                    and (part := str(operand.this).casefold()) in ('year', 'month')
                    and isinstance(value, exp.Literal) and value.is_int):
                components.setdefault(name, []).append((part, int(value.this), atom))
                continue
        remaining.append(atom)
    if len(bounds) + len(equal_months) + len(components) != 1:
        return None
    if bounds:
        name, terms = next(iter(bounds.items()))
        starts = [(atom, day) for atom, day in terms if isinstance(atom, exp.GTE)]
        stops = [(atom, day) for atom, day in terms if isinstance(atom, exp.LT)]
        if len(starts) != 1 or len(stops) != 1:
            return None
        (start_atom, start), (stop_atom, stop) = starts[0], stops[0]
        if start.day != 1 or stop.day != 1 or stop.year * 12 + stop.month != start.year * 12 + start.month + 1:
            return None
        condition = exp.and_(start_atom.copy(), stop_atom.copy())
    elif equal_months:
        name, start, atom = equal_months[0]
        condition = atom.copy()
    else:
        name, terms = next(iter(components.items()))
        parts = {key: value for key, value, _ in terms}
        if len(terms) != 2 or set(parts) != {'year', 'month'} or not 1 <= parts['year'] <= 9999 or not 1 <= parts['month'] <= 12:
            return None
        start = date(parts['year'], parts['month'], 1)
        condition = exp.and_(*(atom.copy() for _, _, atom in terms))
    if any(c.name == name for atom in remaining for c in atom.find_all(exp.Column)):
        return None
    if start.year == 9999 and start.month == 12:
        return None
    return start, date(start.year + int(start.month == 12), start.month % 12 + 1, 1), condition


def _count_predicate(node):
    if isinstance(node, exp.Filter) and isinstance(node.this, exp.Count) and isinstance(node.expression, exp.Where):
        return node.this, node.expression.this
    if isinstance(node, (exp.Count, exp.Sum)) and isinstance(node.this, exp.Case):
        case = node.this
        branches = case.args.get('ifs') or []
        if case.this is not None or len(branches) != 1 or not isinstance(operand := branches[0].args.get('true'), exp.Literal) or not operand.is_int:
            return node, None
        default = case.args.get('default')
        if (isinstance(node, exp.Count) and (default is None or isinstance(default, exp.Null))
                or isinstance(node, exp.Sum) and operand.this == '1' and isinstance(default, exp.Literal)
                and default.is_int and default.this == '0'):
            return node, branches[0].this
    return node, None


def _authorized_table(ref, source, dialect):
    if not isinstance(ref, exp.Table) or not isinstance(ref.this, exp.Identifier):
        return None
    path = '.'.join(part.name for part in ref.parts)
    key = str.casefold if dialect == 'sqlite' else str
    matches = [item for item in source.get('tables', [])
               if key(item['name']) == key(path) or not ref.db and key(item['name']).endswith('.' + key(path))]
    return matches[0] if len(matches) == 1 else None


def prepare(sql, source, lineage, request_scope):
    """Add same-statement evidence only to unambiguous scalar monthly counts."""
    publication = source.get('business_project') or {}
    request = (request_scope or {}).get('request') or {}
    if (not lineage or lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
            or lineage['request_ref'] != request.get('user_ref') or not request.get('quote')
            or not publication.get('id') or not publication.get('version') or not source.get('id')
            or lineage.get('source_id') != source['id']):
        return None
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return None
    try:
        tree = parse_one(sql, read=_WindowPostgres if dialect == 'postgres' else dialect)
    except SqlglotError:
        return None
    if (not isinstance(tree, exp.Select) or any(v and k not in ('expressions', 'from', 'where', 'order') for k, v in tree.args.items())
            or tree.find(exp.Subquery, exp.Window, exp.Placeholder, exp.Parameter, exp.Exists)
            or not (relation := tree.args.get('from'))
            or any(relation.this.args.get(key) for key in ('sample', 'version'))
            or relation.this.alias_column_names):
        return None
    ref = normalize_identifiers(relation.this.copy(), dialect=dialect)
    if not (table := _authorized_table(ref, source, dialect)):
        return None
    names = {projection.alias_or_name.casefold() for projection in tree.expressions}
    if not 1 <= len(names) <= 16 or len(names) != len(tree.expressions) or '' in names:
        return None
    # Resolve comparisons using dialect identifier rules without rewriting the
    # user's actual projections or changing the output casing.
    alias = ref.alias_or_name
    probes = {}
    columns = []
    for projection in list(tree.expressions):
        count, predicate = _count_predicate(projection.unalias().unnest())
        if (not isinstance(count, (exp.Count, exp.Sum)) or count.expressions or isinstance(count.this, exp.Distinct)
                or isinstance(count, exp.Sum) and predicate is None):
            continue
        where = tree.args.get('where')
        if predicate is None and not where:
            continue
        terms = predicate if predicate is not None else where.this
        normalized = normalize_identifiers(terms.copy(), dialect=dialect)
        bounds = _month_bounds(normalized, table, alias, dialect)
        if not bounds:
            continue
        start, stop, conditions = bounds
        key = conditions.sql(dialect=dialect)
        probe = probes.get(key)
        if probe is None:
            index = len(probes)
            probe = f'__nw_month_observed_{index}'
            while probe.casefold() in names:
                index += 1
                probe = f'__nw_month_observed_{index}'
            names.add(probe.casefold())
            probes[key] = probe
            case = exp.Case(ifs=[exp.If(this=conditions, true=exp.Literal.number(1))])
            tree.append('expressions', exp.alias_(exp.Count(this=case), probe))
        projected_name = normalize_identifiers(projection.copy(), dialect=dialect).alias_or_name if dialect == 'postgres' else projection.alias_or_name
        columns.append({'column': projected_name, 'period': start.strftime('%Y-%m'),
                        'start_inclusive': start.isoformat(), 'end_exclusive': stop.isoformat(), 'probe': probe})
    if not columns:
        return None
    return tree.sql(dialect=dialect), columns, {'schema_version': 1, 'request_ref': lineage['request_ref'],
                                                'source_id': source['id'], 'publication': {k: publication[k] for k in ('id', 'version')}}


def apply(rows, columns, plan, result_ref):
    """Remove internal probes and expose only confirmed coverage states."""
    entries, scope = plan
    probes = {entry['probe'] for entry in entries}
    evidence, raw = [], {}
    if len(rows) == 1:
        row = rows[0]
        for entry in entries:
            column = entry['column']
            observed, value = row.get(entry['probe']), row.get(column)
            if type(observed) is not int or observed < 0:
                continue
            if value is None and observed == 0:
                state = 'unobserved'
            elif type(value) is int and 0 <= value <= observed:
                state = 'observed' if value else 'observed_zero' if observed else 'unobserved'
            else:
                continue
            if state == 'unobserved':
                if value is not None:
                    raw[column] = value
                row[column] = None
            evidence.append({**{k: v for k, v in entry.items() if k != 'probe'}, 'state': state, 'observed_rows': observed})
    for row in rows:
        for probe in probes:
            row.pop(probe, None)
    columns[:] = [name for name in columns if name not in probes]
    return ({**scope, 'result_ref': result_ref, 'columns': evidence} if evidence else None), raw


def note(contract):
    evidence = (contract or {}).get('period_count_coverage') or {}
    columns = evidence.get('columns', [])
    periods = sorted({entry['period'] for entry in columns if entry['state'] == 'unobserved'})
    if periods:
        return ('本次查询在 ' + '、'.join(periods) + ' 未观察到同范围事实。对应列保留为空，'
                '不能据此确认业务量为零，也不能使用这些空值计算占比或跨期变化。')
    if any(entry['state'] == 'observed_zero' for entry in columns):
        return '同期间已查到事实，但对应条件计数为 0；这只证明当前查询的条件结果，未验证整期报送完整性。'
    return None
