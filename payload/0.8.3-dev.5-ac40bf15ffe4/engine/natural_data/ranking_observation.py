"""Measure the last observed day in the same statement as a count or ranking."""
from datetime import date
import re

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from .vendor.dialect import sqlglot_name


_OBSERVED = '__nw_ranking_observed_through'
_RANKING = re.compile(r'最多|最少|最高|最低|排名|排行|(?:排(?:在|名)?|位列|位居)(?:第[一1]|首位)|居首')
_SELECT_ARGS = {'expressions', 'from', 'where', 'group', 'order'}
_OBSERVATION_QUESTION = re.compile(r'[,，]\s*(?:数据|记录|观测)的?(?:截至|截止(?:到)?)\s*'
                                   r'(?:哪(?:一)?(?:天|日)|什么日期|何时)[。!?？]?\s*$')


def published_observed_ranking(source, host):
    """Use a published case count and its sibling category examples together."""
    from .catalog_view import entry_code
    from .db_probe import _quoted_table
    from .display_units import _row_count
    from .monthly_sql_scope import _annual_year, _has_date
    from .observed_sql_scope import _single_fact
    from .published_cast_date_axis import _bounds, _day_value, _source_examples
    from .published_scope import _total_label
    from .request_scope import _and_atoms
    from .retrieval import normalize

    quote = host.get('user_text') or ''
    text = normalize(quote).strip()
    publication = source.get('business_project') or {}
    date_question = _OBSERVATION_QUESTION.search(text)
    if (not host.get('user_ref') or host.get('clarifications') or not date_question
            or not publication.get('id') or not publication.get('version') or not (annual := _annual_year(host, text))):
        return None
    parts = text[:date_question.start()].split('?')
    if len(parts) != 2:
        return None
    request = re.fullmatch(
        r'(?:本年度?|今年|当年|(?:19|20)\d{2}\s*年)\s*(?P<region>[\u4e00-\u9fff]{2,4}省)?'
        r'(?P<subject>[\u4e00-\u9fff]{3,30})按(?P<group>(?:申请|办理)?事项|业务类型|业务类别|项目名称)'
        r'(?:分别|各自)?(?:有|是|为)?多少(?P<unit>件|条|笔|宗)', parts[0])
    ranking = re.fullmatch(r'(?:数量|件数|记录数)?(最[多少])的?(?:是)?哪(?:一)?(?:个|类|种|项)([\u4e00-\u9fff]{2,9})',
                           parts[1].strip())
    if not request or not ranking or not request['group'].endswith(ranking[2]):
        return None
    terms = [term for term in source.get('terms', []) if term.get('origin') and term.get('source_id') == source['id']
             and term.get('status') not in ('draft', 'review')]
    by_code = {(term['origin'], entry_code(term, 'name')): term for term in terms}
    region = request['region']
    regional_origins = {term['origin'] for term in terms if region and region in term.get('definition', '')
                        and '区域口径' in term.get('definition', '')}
    examples = list(_source_examples(source))
    dialect = sqlglot_name(source.get('dialect'))
    compact = lambda value: re.sub(r'业务|\s+', '', value).removeprefix('测绘').removesuffix('办理')
    candidates = []
    for item in examples:
        term = by_code.get((item['origin'], entry_code(item, 'question')))
        if (not term or not (name := _total_label(term['name'], request['unit']))
                or compact(name[0]) != compact(request['subject'])
                or not re.search(r'单位[：:]\s*'+request['unit'], term.get('definition', ''))
                or region and item['origin'] not in regional_origins):
            continue
        fact = _single_fact(item['logical_sql'], source)
        if not fact:
            continue
        tree, table, relation, _ = fact
        if len(tree.expressions) != 1 or not _row_count(tree.expressions[0].unalias()) or not tree.args.get('where'):
            continue
        if region and any(re.search(r'所属省|省份|省域|地区|地市|^(?:province|region|city|prefecture)(?:_|$)',
                                    str(column.get(key) or ''), re.I)
                          for column in table['columns'] for key in ('name', 'label', 'business_name')):
            continue  # Let the normal query path apply an available regional filter.
        atoms = list(_and_atoms(tree.args['where'].this))
        if len(atoms) != 2 or {type(atom) for atom in atoms} != {exp.GTE, exp.LT}:
            continue
        lower = next(atom for atom in atoms if isinstance(atom, exp.GTE))
        operand = _day_value(lower.this, dialect) or lower.this
        if not isinstance(operand, exp.Column) or not (window := _bounds(
                tree, relation, operand.name, dialect, registered=True)) or window[2]:
            continue
        groups = {}
        for sibling in examples:
            sibling_term = by_code.get((sibling['origin'], entry_code(sibling, 'question')))
            if (sibling['origin'] != item['origin'] or not sibling_term
                    or not sibling_term['name'].split(' ', 1)[-1].startswith(term['name'].split(' ', 1)[-1]+'_')):
                continue
            matched = _single_fact(sibling['logical_sql'], source)
            if (not matched or matched[1] != table or not matched[0].args.get('where')
                    or len(matched[0].expressions) != 1 or not _row_count(matched[0].expressions[0].unalias())):
                continue
            conditions = list(_and_atoms(matched[0].args['where'].this))
            if len(conditions) != 3 or _bounds(matched[0], matched[2], operand.name, dialect, registered=True) != window:
                continue
            filters = [atom for atom in conditions
                       if isinstance(atom, exp.EQ) and isinstance(atom.this, exp.Column)
                       and isinstance(atom.expression, exp.Literal) and atom.expression.is_string]
            if len(filters) == 1:
                predicate, = filters
                groups.setdefault(predicate.this.name, set()).add(predicate.expression.this)
        dimensions = [name for name, labels in groups.items()
                      if len(labels) >= 2 and name in {column['name'] for column in table['columns']}]
        if len(dimensions) == 1:
            candidates.append((table['name'], dimensions[0], operand.name,
                               _day_value(lower.this, dialect) is not None or not _has_date(table, operand.name)))
    if len(set(candidates)) != 1:
        return None
    table, category, axis, cast = candidates[0]
    year = annual[0]
    ident = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    day = ident(axis)
    if cast:
        day = f'CAST({day} AS DATE)' if dialect == 'postgres' else f'DATE({day})'
    bound = lambda year: ('DATE ' if dialect == 'postgres' else '') + f"'{year}-01-01'"
    direction = 'DESC' if ranking[1] == '最多' else 'ASC'
    category_value = f"COALESCE(NULLIF(TRIM({ident(category)}), ''), '未注明')"
    sql = (f'SELECT {category_value} AS {ident(request["group"])}, COUNT(1) AS {ident("数量（" + request["unit"] + "）")} '
           f'FROM {_quoted_table(table, dialect)} WHERE {day} >= {bound(year)} AND {day} < {bound(year + 1)} '
           f'GROUP BY 1 ORDER BY 2 {direction}, 1 ASC')
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': quote, 'source_id': source['id'],
            'connection_revision': source.get('connection_revision', 0),
            'publication': {k: publication[k] for k in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'sql': sql, 'purpose': 'answer',
                              'title': f'{year}年{request["subject"]}按{request["group"]}统计'}}


def _group_projection(group, dimension, index, table, dialect):
    key = group.expressions[0].unnest()
    value = dimension.unalias().unnest()
    if key == value or isinstance(key, exp.Literal) and key.is_int and key.this == str(index + 1):
        return True
    if not dimension.alias or not isinstance(key, exp.Column) or key.table or key.db or key.catalog:
        return False
    normalized = normalize_identifiers(dimension.copy(), dialect=dialect).alias
    return (normalize_identifiers(key.copy(), dialect=dialect).name == normalized
            and not any(c['name'].casefold() == normalized.casefold() for c in table['columns']))


def _day_expression(tree, source, table, dialect, window):
    from .current_month import date_axis
    from .published_cast_date_axis import _column, _day_value, date_axis as cast_date_axis
    from .request_scope import _and_atoms

    physical = date_axis(tree, source, table, dialect)
    axis = physical if physical and all(physical[1][:2]) else cast_date_axis(tree, source, table, dialect)
    if axis is None:
        return None
    column, (start, stop, unknown) = axis
    if (unknown or not start or not stop or column['name'] != window.get('date_column')
            or start.isoformat() != window.get('start_inclusive') or stop.isoformat() != window.get('end_exclusive')):
        return None

    relation = tree.args['from'].this
    for atom in _and_atoms(tree.args['where'].this):
        if not isinstance(atom, (exp.GTE, exp.GT, exp.LT, exp.LTE, exp.Between)):
            continue
        operand = atom.this.unnest()
        field = _day_value(operand, dialect) if axis is not physical else operand
        if field is None or not (ref := _column(field, relation)) or ref.name.casefold() != column['name'].casefold():
            continue
        if axis is physical and dialect == 'sqlite':
            return exp.Date(this=operand.copy()), column
        return exp.cast(operand.copy(), 'date') if axis is physical else operand.copy(), column
    return None



def _date_label(source, table, column, dialect):
    from .catalog_view import entry_code
    from .current_month import _parse, _table
    from .published_cast_date_axis import _source_examples

    direct = (column.get('time_semantics') or {}).get('name') or column.get('label') or ''
    if re.fullmatch(r'[\u4e00-\u9fff]{2,20}', direct):
        return direct
    pattern = re.compile(r'(?:^|[，,。；;：:\n])\s*([\u4e00-\u9fff]{0,10}(?:日期|时间))\s*[（(]\s*'
                         + re.escape(column['name']) + r'\s*[）)]', re.IGNORECASE)
    matched = {(term['origin'], entry_code(term, 'name')): label[1] for term in source.get('terms', [])
               if term.get('origin') and term.get('source_id') == source['id'] and term.get('status') != 'review'
               and (label := pattern.search(term.get('definition') or ''))}
    labels = set()
    for example in _source_examples(source):
        label = matched.get((example['origin'], entry_code(example, 'question')))
        if label and _table(_parse(example.get('logical_sql'), dialect), source) == table:
            labels.add(label)
    if len(labels) == 1:
        return labels.pop()


def bound_annual_ranking(sql, source, host):
    """Complete a lower-only annual count window when its date axis is proven."""
    from .current_month import date_axis
    from .display_units import _row_count
    from .monthly_sql_scope import _annual_year
    from .observed_sql_scope import _single_fact
    from .published_cast_date_axis import _column, _day_value, date_axis as cast_date_axis
    from .request_scope import _and_atoms

    quote = host.get('user_text') or ''
    if (not host.get('user_ref') or not (source.get('business_project') or {}).get('version')
            or not _RANKING.search(quote) or not re.search(r'(?:数据|记录|观测)的?(?:截至|截止)', quote)
            or not (annual := _annual_year(host, quote)) or not (fact := _single_fact(sql, source))):
        return sql
    tree, table, relation, dialect = fact
    group = tree.args.get('group')
    if (not group or len(group.expressions) != 1 or len(tree.expressions) != 2
            or any(value and key not in _SELECT_ARGS for key, value in tree.args.items())):
        return sql
    dimensions = [item for item in tree.expressions if not item.find(exp.AggFunc)]
    counts = [item.unalias().unnest() for item in tree.expressions if item not in dimensions]
    if (len(dimensions) != 1 or len(counts) != 1 or not isinstance(counts[0], exp.Count) or not _row_count(counts[0])
            or not _group_projection(group, dimensions[0], tree.expressions.index(dimensions[0]), table, dialect)):
        return sql
    physical = date_axis(tree, source, table, dialect)
    axis = physical or cast_date_axis(tree, source, table, dialect, require_upper=False)
    year, _ = annual
    if not axis or axis[1] != (date(year, 1, 1), None, False):
        return sql
    column, _ = axis
    for atom in _and_atoms(tree.args['where'].this):
        if not isinstance(atom, exp.GTE):
            continue
        field = atom.this if physical else _day_value(atom.this, dialect)
        ref = _column(field, relation) if field is not None else None
        if ref is None or ref.name.casefold() != column['name'].casefold():
            continue
        upper = exp.Literal.string(date(year + 1, 1, 1).isoformat())
        if dialect == 'postgres':
            upper = exp.cast(upper, 'date')
        return tree.where(exp.LT(this=atom.this.copy(), expression=upper), copy=False).sql(dialect=dialect)
    return sql


def prepare(sql, source, lineage, request_scope):
    """Keep the WHERE and grouping intact and measure the latest matching day."""
    scope, lineage = request_scope or {}, lineage or {}
    request = scope.get('request') or {}
    window = scope.get('monthly_sql_window') or {}
    publication = source.get('business_project') or {}
    sid, ref = source.get('id'), request.get('user_ref')
    if (not sid or not ref
            or lineage.get('purpose') != 'answer' or lineage.get('request_ref') != ref
            or not lineage.get('session_id') or lineage.get('source_id') != sid
            or not lineage.get('config_fingerprint') or lineage['config_fingerprint'] != scope.get('config_fingerprint')
            or not publication.get('id') or not publication.get('version')):
        return None
    publication = {key: publication[key] for key in ('id', 'version')}
    if window.get('kind') == 'annual_aggregate':
        if (window.get('state') != 'checked' or window.get('source_id') != sid or window.get('request_ref') != ref
                or window.get('publication') != publication):
            return None
    else:
        fixed = [item for item in scope.get('constraints', []) if item.get('kind') == 'raw_dimension_grouping'
                 and item.get('request') == 'raw_ranking' and item.get('source_id') == sid
                 and (item.get('evidence') or {}).get('user_ref') == ref and len(item.get('window') or []) == 2]
        if len(fixed) != 1:
            return None
        constraint, = fixed
        start, stop = constraint['window']
        window = {'kind': 'fixed_range', 'request_ref': ref, 'source_id': sid, 'publication': publication,
                  'table': constraint['table'], 'date_column': constraint['time_column'],
                  'start_inclusive': start, 'end_exclusive': stop}
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('postgres', 'sqlite'):
        return None
    try:
        tree = parse_one(sql, read=dialect)
    except SqlglotError:
        return None
    from .current_month import _table
    from .display_units import _row_count
    if (not isinstance(tree, exp.Select) or not 1 <= len(tree.expressions) <= 4
            or any(value and key not in _SELECT_ARGS for key, value in tree.args.items())
            or tree.find(exp.Window, exp.Subquery, exp.CTE)
            or not (table := _table(tree, source)) or table['name'] != window.get('table')):
        return None
    group = tree.args.get('group')
    scalar = len(tree.expressions) == 1
    if scalar:
        if group or window['kind'] != 'annual_aggregate' or _RANKING.search(request['quote']):
            return None
    elif (not _RANKING.search(request['quote']) or not group or len(group.expressions) != 1
          or any(value for key, value in group.args.items() if key != 'expressions')):
        return None
    relation = tree.args['from'].this
    if relation.alias_column_names or any(relation.args.get(key) for key in ('sample', 'version')):
        return None
    counts = [i for i, projection in enumerate(tree.expressions)
              if isinstance(count := projection.unalias().unnest(), exp.Count) and _row_count(count)]
    if len(counts) != 1 or any(i.name.casefold() == _OBSERVED for i in tree.find_all(exp.Identifier)):
        return None
    count_index, = counts
    dimension_index = None
    if not scalar:
        dimensions = [i for i, projection in enumerate(tree.expressions)
                      if i != count_index and projection.find(exp.Column)
                      and not projection.find(exp.AggFunc, exp.Explode, exp.Unnest)
                      and _group_projection(group, projection, i, table, dialect)]
        if len(dimensions) != 1:
            return None
        dimension_index, = dimensions
    if (any(c['name'].casefold() == _OBSERVED for c in table['columns'])
            or not (axis := _day_expression(tree, source, table, dialect, window))):
        return None
    expression, column = axis
    added = [item for i, item in enumerate(tree.expressions) if i not in (count_index, dimension_index)]
    if any(not isinstance(value := item.unalias().unnest(), (exp.Min, exp.Max))
           or value.expressions or value.this.unnest() != expression for item in added):
        return None
    tree.select(exp.alias_(exp.Max(this=expression), _OBSERVED), append=True, copy=False)
    proof = {k: window[k] for k in ('request_ref', 'source_id', 'publication', 'start_inclusive', 'end_exclusive')}
    if window['kind'] == 'fixed_range':
        proof['period_grain'] = 'month'
    if label := _date_label(source, table, column, dialect):
        proof['date_label'] = label
    if added:
        proof['date_aggregate_columns'] = [item.alias_or_name for item in added]
    return tree.sql(dialect=dialect), (proof, dimension_index, count_index)


def extract(rows, columns, plan, result_ref, *, complete):
    """Remove the server-only projection before chart, values, and artifact creation."""
    proof, dimension_index, count_index = plan
    columns.remove(_OBSERVED)
    days = [row.pop(_OBSERVED, None) for row in rows]
    if not complete or not days:
        return None
    try:
        dates = [date.fromisoformat(day).isoformat() for day in days]
    except (ValueError, TypeError):
        return None
    if any(not proof['start_inclusive'] <= day < proof['end_exclusive'] for day in dates):
        return None
    return {**proof, 'schema_version': 1, 'result_ref': result_ref, 'count_column': columns[count_index],
            'observed_through': max(dates),
            **({'category_column': columns[dimension_index]} if dimension_index is not None else {'shape': 'total'})}
