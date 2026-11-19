"""Bound an explicit event ranking to the authorized, published event schema."""
from collections import defaultdict
from datetime import date
import re

import sqlglot
from sqlglot import exp

from .business_ambiguity import _FIELD_LABEL, _column_name, _is_row_count, _physical_select, dimension_business_choice
from .catalog_view import entry_code
from .published_scope import _conjuncts
from .qualification_scope import _select_population
from .snapshot_scope import _physical_table
from .vendor.dialect import sqlglot_name

_RANKING = re.compile(
    r'^(?:请(?:帮我)?|帮我|想知道|本年度|今年|本年|上年度|去年|本月|上月|20\d{2}年|[\s，,])*'
    r'(?:哪个|哪一类|哪类|哪种)(?:的)?(?P<dimension>[\u4e00-\u9fff]{2,16}?)的'
    r'(?P<event>[\u4e00-\u9fff]{2,12}?)(?:案卷|记录|业务)?(?:数量|件数|次数|数)(?:最多|最少|最高|最低)[。？！?!]?$'
)


def _published_pairs(source):
    pairs = {}
    for kind, title in (('terms', 'name'), ('examples', 'question')):
        items = defaultdict(list)
        for row in source.get(kind, []):
            if (row.get('source_id') == source['id'] and row.get('origin') and row.get('status') != 'review'
                    and (code := entry_code(row, title))):
                items[row['origin'], code].append(row)
        pairs[kind] = items
    for key, terms in pairs['terms'].items():
        examples = pairs['examples'].get(key, [])
        if len(terms) == len(examples) == 1:
            yield terms[0], examples[0]


def _dimension_match(label, requested):
    if label == requested:
        return True
    root = re.sub(r'(?:类别|类型)$', '', label)
    wanted = re.sub(r'(?:类别|类型)$', '', requested)
    return len(root) >= 2 and (wanted == root or wanted.endswith('资质' + root))


def _event_records(pairs, source, labels, tables):
    for term, example in pairs:
        if not any(label in term.get('name', '') for label in labels):
            continue
        shape = _physical_select(example.get('logical_sql') or '', source)
        if not shape:
            continue
        tree, relation, table = shape
        if len(tree.expressions) != 1 or not _is_row_count(tree.expressions[0]) or not tree.args.get('where'):
            continue
        for atom in _conjuncts(tree.args['where'].this):
            if not isinstance(atom, exp.EQ):
                continue
            for field, value in ((atom.this, atom.expression), (atom.expression, atom.this)):
                if (isinstance(value, exp.Literal) and value.is_string and value.this in labels
                        and value.this in (term.get('definition') or '')
                        and _column_name(field, relation) in tables[table]):
                    yield table, value.this, tree, relation, atom, term


def _annual_event_total(source, host, table, label, records):
    from .current_month import date_axis as physical_date_axis
    from .monthly_sql_scope import _annual_year
    from .published_cast_date_axis import date_axis as cast_date_axis

    if not (annual := _annual_year(host, host['user_text'])):
        return None
    year, _ = annual
    if not 1900 <= year < 9999:
        return None
    dialect = sqlglot_name(source['dialect'])
    candidates = set()
    for name, event, tree, relation, event_filter, term in records:
        if (name != table['name'] or event != label or term.get('status') == 'draft'
                or any(value and key not in ('expressions', 'from', 'where') for key, value in tree.args.items())
                or not (unit := re.match(r'单位[：:]\s*(件|条|笔|次|宗|单)\s*(?:[。；;.]|$)', term.get('definition') or ''))):
            continue
        date_filters = [atom for atom in _conjuncts(tree.args['where'].this) if atom is not event_filter]
        if len(date_filters) != 2 or {type(atom) for atom in date_filters} != {exp.GTE, exp.LT}:
            continue
        axis = physical_date_axis(tree, source, table, dialect) or cast_date_axis(tree, source, table, dialect)
        if not axis or not all(axis[1][:2]) or axis[1][2]:
            continue
        # Rebind the published event filter to the user's annual interval.
        statement = tree.copy()
        ref = statement.args['from'].this
        ref.replace(exp.to_table(table['name'], dialect=dialect, alias=relation.alias))
        atoms = _conjuncts(statement.args['where'].this)
        for atom in atoms:
            if isinstance(atom, (exp.GTE, exp.LT)):
                day = date(year + isinstance(atom, exp.LT), 1, 1).isoformat()
                value = exp.Literal.string(day)
                atom.set('expression', exp.cast(value, 'date') if dialect == 'postgres' else value)
        column = f'数量（{unit[1]}）'
        statement.set('expressions', [exp.alias_(exp.Count(this=exp.Star()), column, quoted=True)])
        candidates.add((statement.sql(dialect=dialect), column, unit[1]))
    if len(candidates) != 1:
        return None
    sql, column, unit = candidates.pop()
    return {'year': year, 'count_column': column, 'unit': unit,
            'query_request': {'source_id': source['id'], 'title': f'{year} 年{label}数量', 'sql': sql, 'purpose': 'answer'}}


def _dimension_fields(pairs, source, requested, tables):
    for mapping in source.get('business_dictionary', []):
        table, field, name = (mapping.get(k, '') for k in ('table', 'column', 'name'))
        if (mapping.get('kind') in ('field', 'term', 'enum') and mapping.get('source_id') == source['id']
                and field in tables.get(table, ()) and _dimension_match(name, requested)):
            yield table, field, name
    dialect = sqlglot_name(source['dialect'])
    for term, example in pairs:
        matches = [(name, field) for name, field in _FIELD_LABEL.findall(term.get('definition') or '')
                   if _dimension_match(name, requested)]
        if not matches:
            continue
        try:
            tree = sqlglot.parse_one(example.get('logical_sql') or '', read=dialect)
        except sqlglot.errors.SqlglotError:
            continue
        if not (shape := _select_population(tree, source, dialect)):
            continue
        tree, relation, table = shape
        used = {_column_name(column, relation) for column in tree.find_all(exp.Column)}
        for name, field in matches:
            if field in used and field in tables[table]:
                yield table, field, name


def _registered_path(source, start, targets, tables):
    edges = defaultdict(set)

    def link(left, right):
        if left in tables and right in tables:
            edges[left].add(right)
            edges[right].add(left)

    for mapping in source.get('business_dictionary', []):
        if mapping.get('kind') == 'relation' and mapping.get('source_id') == source['id']:
            link(mapping.get('table'), mapping.get('to_table'))
    for table in source['tables']:
        schema = table['name'].rpartition('.')[0]
        for key in table['foreign_keys']:
            target = key.get('referred_table')
            if not target:
                continue
            target_schema = key.get('referred_schema') or schema
            link(table['name'], (target_schema + '.' if target_schema else '') + target)
    for term in source.get('terms', []):
        spec = term.get('config') or {}
        if spec.get('kind') == 'semantic-metric' and spec.get('source_id') == source['id'] and term.get('status') != 'review':
            for relation in spec.get('relations', []):
                link(relation.get('from_table'), relation.get('to_table'))

    pending, visited = [start], {start}
    while pending:
        current = pending.pop()
        if current in targets:
            return True
        neighbors = edges[current] - visited
        visited.update(neighbors)
        pending.extend(neighbors)
    return False


def _has_published_route(pairs, source, event_table, field_tables, event, dimension):
    dialect = sqlglot_name(source['dialect'])
    for term, example in pairs:
        definition = (term.get('name') or '') + ' ' + (term.get('definition') or '')
        if event in definition and dimension in definition:
            return True
        try:
            tree = sqlglot.parse_one(example.get('logical_sql') or '', read=dialect)
        except sqlglot.errors.SqlglotError:
            continue
        if tree.find(exp.Join):
            joined = {_physical_table(table, source, dialect) for table in tree.find_all(exp.Table)}
            if event_table in joined and joined & field_tables:
                return True
    for term in source.get('terms', []):
        spec = term.get('config') or {}
        if spec.get('kind') != 'semantic-metric' or spec.get('source_id') != source['id'] or term.get('status') == 'review':
            continue
        definition = (term.get('name') or '') + ' ' + (spec.get('definition') or '')
        if event in definition and dimension in definition:
            return True
    return False


def event_dimension_boundary(source, host):
    """Report only the missing registered path for one explicitly scoped ranking."""
    request = host.get('user_ref')
    publication = source.get('business_project') or {}
    original = (host.get('user_text') or '').strip()
    if (not request or not publication.get('id') or not publication.get('version')
            or not (question := _RANKING.fullmatch(original))
            or re.search(r'或者|还是|分别|以及|另外|顺便|同时|、', original)):
        return None
    inventory = source.get('tables') or []
    if not inventory or any(not isinstance(table.get('foreign_keys'), list) or not table.get('columns') for table in inventory):
        return None
    tables = {table['name']: {col['name'] for col in table['columns']} for table in inventory}
    pairs = list(_published_pairs(source))
    event = question['event']
    # A nearby application example can identify the relevant source, but
    # cannot establish any completed-event value or substitute a statistic.
    records = list(_event_records(pairs, source, {event, '申请' + event}, tables))
    events = {(table, label) for table, label, *_ in records}
    fields = set(_dimension_fields(pairs, source, question['dimension'], tables))
    if len(events) != 1 or not fields or dimension_business_choice(source, host):
        return None
    (event_table, label), = events
    dimensions = {name for _, _, name in fields}
    if len(dimensions) != 1:
        return None
    dimension, = dimensions
    if tables[event_table] & {field for _, field, _ in fields}:
        return None
    for table in inventory:
        if table['name'] == event_table and any(
                _dimension_match(name, question['dimension']) for col in table['columns']
                for name in (col['name'], col.get('label') or '', col.get('business_name') or '', *(col.get('aliases') or []))):
            return None
    field_tables = {table for table, _, _ in fields}
    if _registered_path(source, event_table, field_tables, tables):
        return None
    # A published query or metric that expressly covers both remains a valid
    # candidate even if it uses a derived projection or a separately named view.
    if _has_published_route(pairs, source, event_table, field_tables, event, dimension):
        return None
    boundary = {'schema_version': 1, 'state': 'unverified_event_dimension', 'request_ref': request,
                'source_id': source['id'], 'publication': {key: publication[key] for key in ('id', 'version')},
                'event': label, 'dimension': dimension,
                'evidence': {'kind': 'no_registered_event_dimension_path', 'same_table': False, 'registered_relation': False}}
    table = next(table for table in inventory if table['name'] == event_table)
    if total := _annual_event_total(source, host, table, label, records):
        boundary['event_total'] = total
        boundary['connection_revision'] = source.get('connection_revision', 0)
    return boundary
