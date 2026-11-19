"""Published, source-bound work permit holding rates."""
import calendar
from datetime import date
import math
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from .catalog_view import entry_code
from .day_bounds import _explicit_days
from .snapshot_scope import _physical_table
from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name

KIND = 'holding_rate_scope'
_RATE = re.compile(r'(?:作业证|从业证|执业证|操作证|上岗证)[^，。！？；\n]{0,10}(?:持有率|持证率)')
_RANK = re.compile(r'地市|城市|各市|分市|跨市|全省|排行|排名|最高|最低|比较|对比')
_LOCAL = re.compile(r'([\u4e00-\u9fff]{2,10}市)(?!级|场|政府)')
_CITY_PREFIX = re.compile(r'^(?:(?:请问|查询|查看|统计|计算|核对|比较|对比|只看|仅看|以及|和|与|及|在|按|全|的))+')
_GENERIC_CITY = re.compile(r'(?:地市|城市|各市|分市|多市|两市|跨市|哪(?:一|些)?市)$')
_REGISTERED = re.compile(r'示例(?:为|适用于|仅覆盖)\s*([\u4e00-\u9fff]{2,5}市)\s*[、，,]\s*'
                         r'(20\d{2})\s*年\s*(\d{1,2})\s*月(?:底|末)')
_TIME = re.compile(r'20\d{2}|去年|前年|上月|本月|目前|现在|当前|今天|今日|昨天|截至|截止')
_UNKNOWN_CITY = re.compile(r'未知|未注明|未填|不详|无归属|^其他$|^null$', re.I)
_CITY_COLUMNS = ('地市', '审核通过人数', '持证人数', '持有率（%）')
_COVERAGE_COLUMN = '有审核记录的地市'
_COVERAGE_LIMIT = 100
_SINGLE_COMPONENTS = ('有效作业证数量（份）', '审核通过人员数量（人）')
_ASK_COMPONENTS = re.compile(r'持证人数|有效作业证(?:数量|数)|从业(?:总)?人数|审核(?:通过)?人员(?:总)?(?:数|数量)')
_DOCUMENTED_RATIO = re.compile(r'有效作业证数量\s*(?:[/／]|除以)\s*审核通过的?(?:全部)?(?:测绘技术)?人员(?:总数|数量)')
_DIRECT_RANK = re.compile(
    r'^(?:(?:请问|请(?:帮我)?查(?:询)?|请(?:帮我)?统计)\s*)?'
    r'(?:哪(?:一)?个(?:地市|城市)|哪(?:一)?市)的[^，。！？；\n]{0,24}'
    + _RATE.pattern + r'(?:最[高低]|排[在名]第[一1])[。！？!?]?$'
)
_SAME_DAY_RANK = re.compile(
    r'^(?:请)?按同一(?:个)?(?:可核实的)?(?:统计)?(?:时点|日期)(?:比较|对比)'
    r'(?P<region>[\u4e00-\u9fff]{2,8}?)?(?:各地市|各市|全省)[，,]\s*'
    r'(?:哪(?:一)?个(?:地市|城市)|哪(?:一)?市)的[^，。！？；\n]{0,24}'
    + _RATE.pattern + r'最[高低](?:[，,。！？!?]\s*(?:同时|并)\s*(?:给出|列出)\s*'
    r'(?:计入的)?持证人数(?:及|与|和)(?:从业总人数|从业人数|人员总数))?[。！？!?]?$'
)


def _automatic_single_city(source, quote):
    if _DIRECT_RANK.fullmatch(quote):
        return True
    if not (rank := _SAME_DAY_RANK.fullmatch(quote)):
        return False
    if not rank['region']:
        return True
    # For an explicitly named region, use the published population filters
    # already bound to this source. A different or mixed region cannot use
    # the registered single-city reference as its automatic partial answer.
    from .row_count_aggregation import _registered_count_specs
    regions = {value.removesuffix('省') for _, spec in _registered_count_specs(source)
               for values in spec['filters'].values() for value in values
               if re.fullmatch(r'[\u4e00-\u9fff]{2,8}省', value)}
    return regions == {rank['region'].removesuffix('省')}


def derive(source, host, reference):
    text = reference['quote']
    if not _RATE.search(text):
        return None
    today = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
    days = _explicit_days(text, today)
    if days is None:
        # Only a holding-rate time question may select a date. A native answer
        # never supplies facts about who or which cities were observed.
        candidates = [_explicit_days(item.get('answer', ''), today)
                      for item in host.get('clarifications', [])
                      if item.get('call_id') and item.get('question_id')
                      and _RATE.search(item.get('question', ''))
                      and re.search(r'月末|月底|时点|截止日|日期', item['question'])]
        if candidates:
            days = [day for choice in candidates if choice for day in choice]
        elif _TIME.search(text):
            days = [today] if re.search(r'当前|目前|现在|今天|今日', text) and not re.search(r'20\d{2}|上月|本月|去年|前年', text) else []
    city_names = (_CITY_PREFIX.sub('', name) for name in _LOCAL.findall(text))
    cities = {name for name in city_names if name and not _GENERIC_CITY.search(name)}
    return {'kind': KIND, 'source_id': source['id'], 'evidence': reference,
            'ranking': bool(_RANK.search(text)), 'cities': sorted(cities),
            'snapshot_day': days[0].isoformat() if days is not None and len(days) == 1 else None,
            'date_unspecified': days is None, 'components_requested': bool(_ASK_COMPONENTS.search(text))}


def _shape(tree, source, dialect, *, scalar=False):
    if not isinstance(tree, exp.Select):
        return None
    tree = normalize_identifiers(tree.copy(), dialect=dialect)
    if scalar:
        if len(tree.expressions) != 1 or any(tree.args.get(k) for k in ('order', 'group', 'limit', 'offset')):
            return None
        tree.set('expressions', [tree.expressions[0].unalias()])
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for relation in tree.find_all(exp.Table):
        if not (relation.db or relation.catalog) and relation.name in ctes:
            continue
        physical = _physical_table(relation, source, dialect)
        if not physical:
            return None
        relation.set('this', exp.to_identifier(physical, quoted=True))
        relation.set('db', None)
        relation.set('catalog', None)
    return tree


def _parsed(sql, dialect):
    try:
        statements = sqlglot.parse(sql, read=dialect)
        return statements[0] if len(statements) == 1 else None
    except (sqlglot.errors.SqlglotError, TypeError):
        return None


def _publication(source):
    project = source.get('business_project') or {}
    if project.get('id') and project.get('version'):
        return {'project_id': project['id'], 'project_version': project['version']}
    return None


def _ratio_parts(tree, definition):
    """Resolve the two direct count inputs of the published rate."""
    from .raw_dimension_scope import _is_count

    if not _DOCUMENTED_RATIO.search(definition) or not isinstance(tree, exp.Select) or len(tree.expressions) != 1:
        return None
    projection = tree.expressions[0].unalias()
    if not isinstance(projection, exp.Round) or not isinstance(ratio := projection.this.unnest(), exp.Div):
        return None
    scaled, divisor = ratio.this.unnest(), ratio.expression.unnest()
    if (not isinstance(scaled, exp.Mul) or not isinstance(divisor, exp.Nullif)
            or not isinstance(factor := scaled.expression, exp.Literal) or not factor.is_number or float(factor.this) != 100
            or divisor.expression != exp.Literal.number(0)):
        return None
    operands = scaled.this.unnest(), divisor.this.unnest()
    relation = tree.args.get('from') or tree.args.get('from_')
    joins = tree.args.get('joins') or []
    if (not relation or len(joins) != 1 or joins[0].kind not in ('', 'CROSS')
            or set(joins[0].args) - {'this', 'kind'}):
        return None
    subqueries = (relation.this, joins[0].this)
    parts = []
    for operand in operands:
        if not isinstance(operand, exp.Column) or not operand.table or operand.db or operand.catalog:
            return None
        matches = [sub.this for sub in subqueries if isinstance(sub, exp.Subquery) and sub.alias == operand.table]
        if (len(matches) != 1 or not isinstance(sub := matches[0], exp.Select) or len(sub.expressions) != 1
                or sub.expressions[0].alias != operand.name or not _is_count(sub.expressions[0])
                or any(sub.args.get(key) for key in ('group', 'having', 'limit', 'offset'))):
            return None
        parts.append((operand, sub))
    if operands[0].table != operands[1].table:
        return parts
    return None


def _count_components(tree, dialect, definition):
    if not (parts := _ratio_parts(tree, definition)):
        return None
    operands = [operand for operand, _ in parts]
    expanded = tree.copy()
    expanded.set('expressions', [
        *(exp.alias_(operand.copy(), label, quoted=True) for operand, label in zip(operands, _SINGLE_COMPONENTS)),
        expanded.expressions[0],
    ])
    return expanded.sql(dialect=dialect), expanded


def _single_city(source, dialect, scope):
    terms = {}
    for term in source.get('terms') or []:
        if (term.get('source_id') != source['id'] or not term.get('origin') or term.get('status') == 'review'
                or not _RATE.search(term.get('name') or '') or not (code := entry_code(term, 'name'))):
            continue
        terms.setdefault((term['origin'], code), []).append(term)
    for example in source.get('examples') or []:
        matched = terms.get((example.get('origin'), entry_code(example, 'question')), [])
        if example.get('source_id') != source['id'] or len(matched) != 1:
            continue
        definition = matched[0].get('definition') or ''
        pins = _REGISTERED.findall(definition)
        if len(pins) != 1:
            continue
        city, year, month = pins[0]
        try:
            day = date(int(year), int(month), calendar.monthrange(int(year), int(month))[1]).isoformat()
        except ValueError:
            continue
        if not (scope['date_unspecified'] or scope['snapshot_day'] == day) or scope['cities'] and city not in scope['cities']:
            continue
        sql = example.get('logical_sql')
        tree = _parsed(sql, dialect)
        # A published date and city must also be present in the exact registered query.
        if not tree or not tree.find(exp.Div) or not (shape := _shape(tree, source, dialect, scalar=True)):
            continue
        literals = {lit.this for lit in tree.find_all(exp.Literal) if lit.is_string}
        if day not in literals or not ({city, '%' + city + '%'} & literals):
            continue
        proof = {'mode': 'registered_single_city', 'snapshot_day': day, 'cities': [city],
                 'comparison_eligible': False, 'basis': definition.splitlines()[0]}
        if scope['components_requested'] and (enhanced := _count_components(tree, dialect, definition)):
            expanded_sql, expanded_tree = enhanced
            if expanded_shape := _shape(expanded_tree, source, dialect):
                yield expanded_sql, expanded_shape, {**proof, 'component_columns': list(_SINGLE_COMPONENTS)}
                continue
        yield sql, shape, proof


def _identifier(name, dialect):
    return exp.to_identifier(name, quoted=True).sql(dialect=dialect)


def _snapshot(source, dialect, scope):
    # This optional published declaration describes a complete person-level
    # snapshot for the named cities. Status must represent explicit yes/no,
    # including people without a valid permit.
    for term in source.get('terms') or []:
        cfg = term.get('config') or {}
        if (term.get('source_id') != source['id'] or cfg.get('source_id') != source['id']
                or term.get('status') == 'review' or cfg.get('kind') != 'holding-rate-snapshot'
                or not _RATE.search(term.get('name') or '') or not str(term.get('definition') or '').strip()):
            continue
        day, cities = cfg.get('snapshot_day'), cfg.get('cities')
        try:
            if date.fromisoformat(day).isoformat() != day:
                continue
        except (TypeError, ValueError):
            continue
        if not (scope['date_unspecified'] or scope['snapshot_day'] == day):
            continue
        if (not isinstance(cities, list) or not 2 <= len(cities) <= 100
                or not all(isinstance(c, str) and 1 <= len(c) <= 100 and c == c.strip() and not _UNKNOWN_CITY.search(c) for c in cities)
                or len(set(cities)) != len(cities)):
            continue
        chosen = sorted(set(scope['cities']) or set(cities))
        if not set(chosen).issubset(cities) or (scope['ranking'] and len(chosen) < 2):
            continue
        table = next((t for t in source.get('tables', []) if t['name'] == cfg.get('table')), None)
        fields = [cfg.get(k) for k in ('person_column', 'city_column', 'day_column', 'status_column')]
        if not table or not all(isinstance(c, str) for c in fields) or len(set(fields)) != 4:
            continue
        columns = {c['name'] for c in table['columns']}
        keys = [table.get('primary_key') or [], *(table.get('unique_keys') or [])]
        if not set(fields).issubset(columns) or not any(set(k) in ({fields[0]}, {fields[0], fields[2]}) for k in keys):
            continue
        # The published mapping identifies the physical date and enum columns.
        mappings = {m.get('kind'): m for m in source.get('business_dictionary', [])
                    if m.get('source_id') == source['id'] and m.get('table') == table['name']
                    and ((m.get('kind') == 'time' and m.get('column') == fields[2])
                         or (m.get('kind') == 'enum' and m.get('column') == fields[3]))}
        if mappings.get('time', {}).get('grain') != 'date':
            continue
        status = {entry.get('label'): entry.get('value') for entry in mappings.get('enum', {}).get('values', [])}
        yes, no = status.get('持证'), status.get('未持证')
        if type(yes) not in (str, int) or type(yes) is not type(no) or yes == no:
            continue
        person, city, day_col, state = [_identifier(c, dialect) for c in fields]
        physical = '.'.join(_identifier(part, dialect) for part in table['name'].split('.'))
        lits = ', '.join(exp.Literal.string(city).sql(dialect=dialect) for city in chosen)
        yes, no = [exp.convert(value).sql(dialect=dialect) for value in (yes, no)]
        region, base, held, rate = [_identifier(c, dialect) for c in _CITY_COLUMNS]
        sql = (f'WITH covered AS (SELECT {city} AS city, COUNT(DISTINCT {person}) AS people, '
               f'COUNT(DISTINCT CASE WHEN {state} = {yes} THEN {person} END) AS holders '
               f'FROM {physical} WHERE {day_col} = \'{day}\' AND {city} IN ({lits}) GROUP BY {city} '
               f'HAVING COUNT(*) = COUNT(DISTINCT {person}) AND COUNT(*) = '
               f'COUNT(CASE WHEN {state} IN ({yes}, {no}) THEN 1 END)) '
               f'SELECT city AS {region}, people AS {base}, holders AS {held}, '
               f'ROUND(holders * 100.0 / NULLIF(people, 0), 2) AS {rate} FROM covered '
               f'ORDER BY {rate} DESC, city')
        if shape := _shape(_parsed(sql, dialect), source, dialect):
            yield sql, shape, {'mode': 'published_city_snapshot', 'snapshot_day': day, 'cities': chosen,
                               'comparison_eligible': len(chosen) >= 2, 'province_completeness': 'unverified',
                               'basis': term['definition']}


def _legacy_city_coverage(example, source, scope, dialect):
    # Audit the recorded approved-population coverage. Removing a sample city
    # can reveal other places, but the example cannot establish their rates.
    from .published_scope import _conjuncts

    if not scope['ranking'] or scope['cities'] or not _automatic_single_city(source, scope['evidence']['quote']):
        return None
    sql, _, proof = example
    if not isinstance(tree := _parsed(sql, dialect), exp.Select):
        return None
    if proof.get('component_columns'):
        tree.set('expressions', [tree.expressions[-1]])
    if not (parts := _ratio_parts(tree, proof['basis'])):
        return None
    _, denominator = parts[1]
    if (any(value for key, value in denominator.args.items() if key not in
            ('from', 'from_', 'expressions', 'joins', 'where')) or not denominator.args.get('where')
            or denominator.find(exp.Or, exp.Subquery)):
        return None
    filters = _conjuncts(denominator.args['where'].this)
    city, = proof['cities']
    matching = [atom for atom in filters if isinstance(atom, exp.EQ) and isinstance(atom.this, exp.Column)
                and atom.expression == exp.Literal.string(city)]
    if len(matching) != 1 or len(filters) < 2:
        return None
    city_pin, = matching
    column = city_pin.this
    tables = list(denominator.find_all(exp.Table))
    bindings = [table for table in tables if table.alias_or_name == column.table] if column.table else tables
    if len(bindings) != 1 or not (selected := _physical_table(bindings[0], source, dialect)):
        return None
    metadata = next(table for table in source['tables'] if table['name'] == selected)
    if not any((field['name'].casefold() if dialect == 'sqlite' else field['name']) ==
               (column.name.casefold() if dialect == 'sqlite' else column.name)
               for field in metadata.get('columns', [])):
        return None
    remaining = [atom.copy() for atom in filters if atom is not city_pin]
    coverage = denominator.copy()
    coverage.set('expressions', [exp.alias_(column.copy(), _COVERAGE_COLUMN, quoted=True)])
    coverage.where(*remaining, exp.Not(this=exp.Is(this=column.copy(), expression=exp.Null())),
                   append=False, copy=False)
    coverage.distinct(copy=False).order_by(column.copy(), copy=False).limit(_COVERAGE_LIMIT + 1, copy=False)
    sql = coverage.sql(dialect=dialect)
    if shape := _shape(_parsed(sql, dialect), source, dialect):
        return sql, shape, {
            'mode': 'registered_city_coverage', 'snapshot_day': proof['snapshot_day'],
            'comparison_eligible': False, 'cities': [], 'sample_city': city,
        }
    return None


def _routes(source, scope, dialect):
    if _publication(source):
        snapshots = list(_snapshot(source, dialect, scope))
        examples = list(_single_city(source, dialect, scope))
        # Multiple applicable declarations cannot supply an implicit choice.
        if len(snapshots) == 1:
            return snapshots + (examples if len(examples) == 1 else [])
        if len(examples) == 1:
            coverage = _legacy_city_coverage(examples[0], source, scope, dialect)
            return examples + ([coverage] if coverage else [])
        return []
    return []


def guidance(source, scope, *, automatic=False):
    routes = _routes(source, scope, sqlglot_name(source['dialect']))
    if not routes:
        return None
    sql, _, proof = routes[0]
    instruction = ('按登记的同日人员快照核算所列地市。仅对具有可核对持证和未持证状态的地市比较。'
                   '按实际回执注明覆盖与缺失的地市。' if proof['comparison_eligible'] else
                   '按原登记示例正式查询。回执只适用于所示单城及日期；用于部分回答时需说明跨市排名尚不能核实。')
    rate = {**proof, 'published_example': sql, 'instruction': instruction}
    request = scope['evidence'].get('user_ref')
    example = (scope['date_unspecified'] and not scope['cities'] and not proof['comparison_eligible']
               and _automatic_single_city(source, scope['evidence']['quote']))
    snapshot = proof['comparison_eligible'] and scope['ranking']
    if automatic and request and (snapshot or example):
        coverage = next((sql for sql, _, proof in routes if proof['mode'] == 'registered_city_coverage'), None)
        location = '可核实地市' if snapshot else proof['cities'][0]
        measure = '证件记录数与审核人员记录数之比' if coverage else '作业证持有率'
        rate.update(schema_version=1, request_ref=request, source_id=source['id'],
                    publication={'id': source['business_project']['id'], 'version': source['business_project']['version']},
                    connection_revision=source.get('connection_revision', 0),
                    query_request={'source_id': source['id'], 'title': f"{location} {proof['snapshot_day']} {measure}",
                                   'sql': sql, 'purpose': 'answer'})
        if coverage:
            rate['coverage_request'] = {'source_id': source['id'], 'title': '审核人员记录地市覆盖',
                                        'sql': coverage, 'purpose': 'answer', 'chart_type': 'table'}
    return {'holding_rate': rate}


def inspect(tree, source, scope):
    if scope['source_id'] != source['id']:
        return None
    dialect = sqlglot_name(source['dialect'])
    routes = _routes(source, scope, dialect)
    for _, expected, proof in routes:
        scalar = proof['mode'] == 'registered_single_city' and not proof.get('component_columns')
        if _shape(tree, source, dialect, scalar=scalar) == expected:
            detail = ('查询使用登记口径核对地市覆盖，名单不提供同日持有率。'
                      if proof['mode'] == 'registered_city_coverage'
                      else '查询与已发布的持有率口径及适用范围一致。')
            return 'checked', detail, {**proof, **_publication(source)}
    # Only an explicit comparison requires proof of comparable populations.
    # Other single-city questions can still use their own appropriate query.
    if not scope['ranking']:
        return None
    detail = ('跨市持有率需要同一统计日、已发布地市覆盖及同一人员归属的去重分子分母。'
              '不能以单位名称拼接不同总体，也不能把未覆盖地市或缺少持证记录推为 0。')
    if routes:
        detail += '请使用授权目录给出的持有率登记查询，并按回执所示范围交付。'
    return 'conflict', detail, None


def result_evidence(full):
    lineage = full.get('result_lineage') or {}
    scope = full.get('request_scope') or {}
    if (full.get('status') != 'succeeded' or full.get('truncated') or lineage.get('purpose') != 'answer'
            or not full.get('result_ref') or not lineage.get('request_ref')
            or scope.get('config_fingerprint') != lineage.get('config_fingerprint')):
        return None
    publication = full.get('business_project') or {}
    for check in (full.get('validation') or {}).get('checks', []):
        constraint = check.get('constraint') or {}
        if (check.get('state') == 'checked' and (proof := check.get('holding_rate'))
                and constraint.get('kind') == KIND and constraint.get('source_id') == lineage.get('source_id')
                and (constraint.get('evidence') or {}).get('user_ref') == lineage['request_ref']
                and proof.get('project_id') == publication.get('id') and proof.get('project_version') == publication.get('version')):
            break
    else:
        return None
    rows, columns = full.get('rows') or [], full.get('columns') or []
    if len(rows) != full.get('row_count') or not rows:
        return None
    if proof['mode'] == 'registered_city_coverage':
        if columns != [_COVERAGE_COLUMN] or len(rows) > _COVERAGE_LIMIT:
            return None
        labels = [row.get(_COVERAGE_COLUMN) for row in rows]
        if not all(isinstance(city, str) for city in labels):
            return None
        cities = sorted({label.strip() for label in labels if _LOCAL.fullmatch(label.strip())})
        if not cities:
            return None
        return {**proof, 'cities': cities, 'schema_version': 1, 'result_ref': full['result_ref'],
                'request_ref': lineage['request_ref'], 'source_id': lineage['source_id']}
    if proof['mode'] == 'published_city_snapshot':
        if tuple(columns) != _CITY_COLUMNS:
            return None
        cities = [row.get(columns[0]) for row in rows]
        expected = set(proof['cities'])
        if (not all(isinstance(city, str) for city in cities) or len(set(cities)) != len(cities)
                or not set(cities).issubset(expected)):
            return None
        for row in rows:
            _, people, holders, rate = (row.get(col) for col in columns)
            if (type(people) is not int or type(holders) is not int or not 0 <= holders <= people or people <= 0
                    or not _rate_value(rate) or abs(float(rate) - holders * 100 / people) > 0.005001):
                return None
        proof = {**proof, 'cities': sorted(cities), 'expected_cities': sorted(expected),
                 'missing_cities': sorted(expected - set(cities)), 'comparison_eligible': len(cities) >= 2}
    else:
        components = proof.get('component_columns') or []
        if len(columns) != len(components) + 1 or len(rows) != 1 or not _rate_value(rows[0].get(columns[-1])):
            return None
        if components:
            if tuple(components) != _SINGLE_COMPONENTS or columns[:2] != components:
                return None
            issued, people, rate = (rows[0].get(column) for column in columns)
            if (type(issued) is not int or type(people) is not int or not 0 <= issued <= people or people <= 0
                    or abs(float(rate) - issued * 100 / people) > 0.005001):
                return None
    return {**proof, 'schema_version': 1, 'result_ref': full['result_ref'],
            'request_ref': lineage['request_ref'], 'source_id': lineage['source_id'], 'rate_column': columns[-1]}


def _rate_value(value):
    try:
        number = float(value)
        return not isinstance(value, (str, bool)) and math.isfinite(number) and 0 <= number <= 100
    except (TypeError, ValueError, OverflowError):
        return False
