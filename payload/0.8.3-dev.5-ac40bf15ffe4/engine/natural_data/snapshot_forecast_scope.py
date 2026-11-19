"""Describe a same-population snapshot history gap in the authorized catalog."""
import calendar
from datetime import date
import re
import unicodedata

import sqlglot
from sqlglot import exp

from .business_ambiguity import _column_name, _is_row_count, matching_rules
from .event_dimension_scope import _published_pairs, _registered_path
from .qualification_scope import _column, _membership, _select_population, _snapshot_bindings, _where
from .snapshot_scope import _physical_table, _snapshot_tables
from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name


_WINDOW = r'(?:未来|接下来)(?:的)?(?:一|1)年(?:内)?'
_STOCK = r'(?:资质单位(?:数量|数|数据量)|资质的(?:持证)?单位(?:数据量|数量|数)|持证单位(?:数量|数|数据量))'
_QUESTION = re.compile(r'^(?P<horizon>'+_WINDOW+r')[，,\s]*(?P<subject>[\u4e00-\u9fff]{2,36}?)'
                       +_STOCK+r'(?:会|是否会|是否|预计会)?(?:增加|减少|增长|下降|增减|变化)(?:吗|多少|百分之几)?[。？！?!]?$')
_PREFIX = re.compile(r'^(?:请(?:问|帮我(?:预测|判断)?)?|想知道|能否预测)\s*')
_NAME = re.compile(r'^(?P<profession>[\u4e00-\u9fff]{2,20})专业[_\s]+(?P<grade>[\u4e00-\u9fff]{1,4}级)'
                   r'[_\s]+资质单位(?:数量|数)$')
_MIXED = re.compile(r'或者|还是|以及|分别|另外|顺便|同时|并(?:给|查|统)|、|除外|不包括|变更')
_HISTORY = re.compile(r'历史|档案|存量|快照|有效期|生效|失效|年末|月末|期末|连续')
_STOCK_OBJECT = re.compile(r'资质单位|持证单位|许可证单位')
_FLOW = re.compile(r'(?:利用审批办理案卷|申请(?:案卷|记录|次数|单位属地|单位数)|受理(?:案卷|记录|次数|件数))')
_TIME = re.compile(r'(?:^|[^a-z])(?:date|time|timestamp|datetime|snapshot|period|year|month|as_of)(?=$|[^a-z])'
                   r'|日期|时间|年月|时点|期间|月份', re.I)
_SNAPSHOT_AXIS = re.compile(r'快照(?:日|时间|期)|统计(?:日|年月|期)|(?:月|季|年|期)末|历史(?:时点|期)|'
                            r'(?:^|[_ ])(?:as_of|asof|snapshot|period|month|year)(?:$|[_ ])', re.I)
_TABLE_HISTORY = re.compile(r'(?:资质|测绘|许可证|qualification|certificat|permit|licen[cs]).*'
                            r'(?:档案|历史|存量|快照|有效期|history|archive|snapshot)|'
                            r'(?:档案|历史|存量|快照|history|archive|snapshot).*(?:资质|许可证|qualification|certificat|permit|licen[cs])',
                            re.I)
_CITY = re.compile(r'[\u4e00-\u9fff]{2,8}市')


def _query(host):
    text = unicodedata.normalize('NFKC', (host.get('user_text') or '').strip())
    if _MIXED.search(text):
        return None
    return _QUESTION.fullmatch(_PREFIX.sub('', text))


def _reading(sql, dialect):
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    return statements[0] if len(statements) == 1 and isinstance(statements[0], exp.Query) else None


def _city_filters(tree, column, definition, city_fields):
    """Separate a sample parameter from a published population restriction."""
    scopes = []
    for atom in _where(tree):
        if not (selection := _membership(atom, column)):
            if any(column(field) in city_fields for field in atom.find_all(exp.Column)):
                scopes.append((atom, None, None, None))
            continue
        field, values = selection
        if field not in city_fields and not any(isinstance(value, str) and _CITY.fullmatch(value) for value in values):
            continue
        value = next(iter(values)) if len(values) == 1 else None
        if not isinstance(value, str) or not _CITY.fullmatch(value):
            scopes.append((atom, field, None, None))
            continue
        city = r'\s*[“"]?' + re.escape(value)
        if re.search(r'(?:适用|统计|限定)(?:的)?(?:地区|区域|范围)(?:仅限|为|是)' + city, definition):
            mode = 'definition'
        elif re.search(r'示例(?:范围)?(?:为|是|使用)' + city, definition):
            mode = 'example'
        else:
            mode = None
        scopes.append((atom, field, value, mode))
    return scopes


def _current_query(tree, inner, relation, column, roles, definition, requested_scope, dialect):
    city_fields = roles.get('city') or set()
    filters = _city_filters(inner, column, definition, city_fields)
    if inner is not tree and any(item for item in _city_filters(tree, lambda _: None, definition, set())):
        return None
    if len(filters) > 1 or filters and not filters[0][3]:
        return None
    retained = list(_where(inner))
    scope = requested_scope or '授权范围'
    if filters:
        atom, field, example_city, mode = filters[0]
        if mode == 'definition':
            if requested_scope and requested_scope != example_city:
                return None
            scope = example_city
        else:
            retained.remove(atom)
            city_fields = {field}
    if requested_scope and (not filters or filters[0][3] != 'definition'):
        if len(city_fields) != 1:
            return None
        retained.append(exp.EQ(this=exp.column(next(iter(city_fields)), table=relation.alias_or_name, quoted=True),
                               expression=exp.Literal.string(requested_scope)))
    inner.set('where', exp.Where(this=exp.and_(*retained)) if retained else None)
    # Where the catalog identifies a single unit key, duplicate reports of that unit count once.
    keys = roles.get('entity') or set()
    if len(keys) == 1:
        key = exp.column(next(iter(keys)), table=relation.alias_or_name, quoted=True)
        if inner is not tree:
            projected_name = '_snapshot_unit'
            if any(item.alias_or_name.casefold() == projected_name for item in inner.expressions):
                return None
            inner.select(exp.alias_(key, projected_name, quoted=True), append=True, copy=False)
            key = exp.column(projected_name, quoted=True)
        tree.expressions[0].unalias().set('this', exp.Distinct(expressions=[key]))
    return scope, tree.sql(dialect=dialect)


def _current_candidates(source, pairs, tables, snapshots, subject, dialect):
    bindings = _snapshot_bindings(source)
    for term, example in pairs:
        name = unicodedata.normalize('NFKC', (term.get('name') or '').split(maxsplit=1)[-1])
        if not (label := _NAME.fullmatch(name)):
            continue
        profession, grade = label['profession'], label['grade']
        title = grade+profession
        if not subject.endswith(title):
            continue
        prefix = subject[:-len(title)].removesuffix('的')
        if prefix and prefix not in ('全省', '本省') and not _CITY.fullmatch(prefix):
            continue
        definition = term.get('definition') or ''
        unit = re.match(r'单位[：:]\s*(家|户|个)\s*(?:[。；;.]|$)', definition)
        if not unit or profession not in definition or grade not in definition:
            continue
        tree = _reading(example.get('logical_sql') or '', dialect)
        if not tree or not (shape := _select_population(tree, source, dialect)):
            continue
        inner, relation, table = shape
        if (table not in snapshots or len(tree.expressions) != 1 or not _is_row_count(tree.expressions[0])
                or any(tree.args.get(key) for key in ('group', 'having', 'limit', 'offset', 'distinct', 'qualify'))
                or set(term.get('tables') or [table]) != {table}):
            continue
        column = _column(tables[table], relation)
        pin = any((value := _membership(atom, column)) and value[1] == {grade} for atom in _where(inner))
        selections = [clause for select in (inner, tree) if (clause := select.args.get('where'))]
        profession_used = any(item.is_string and item.this.strip('%') == profession
                              for clause in selections for item in clause.find_all(exp.Literal))
        if not pin or not profession_used:
            continue
        requested_scope = prefix if _CITY.fullmatch(prefix) else ''
        if not (query := _current_query(tree, inner, relation, column, bindings.get(table, {}), definition,
                                       requested_scope, dialect)):
            continue
        scope, sql = query
        if prefix in ('全省', '本省') and scope != '授权范围':
            continue
        yield table, profession, grade, scope, sql, tree.expressions[0].alias, unit[1]


def _time_bearing(table):
    return any(column.get('time_semantics') or _TIME.search(' '.join(str(column.get(key) or '')
               for key in ('name', 'type', 'label', 'business_name'))) for column in table['columns'])


def _has_alternative_history(source, pairs, tables, snapshots, current, profession, dialect):
    if source.get('historical_qualification') or source.get('metric_mapping'):
        return True
    # Issuance, application and expiry dates on a current snapshot describe
    # its remaining rows. Only an observation axis suggests past populations.
    def has_snapshot_axis(table):
        return any(_SNAPSHOT_AXIS.search(' '.join(str(column.get(key) or '') for key in ('name','label','business_name'))
                   + ' ' + str(column.get('time_semantics') or '')) for column in table['columns'])

    if any(has_snapshot_axis(tables[name]) for name in snapshots):
        return True
    temporal = {name for name, table in tables.items() if name not in snapshots and _time_bearing(table)}
    for term, example in pairs:
        name = term.get('name') or ''
        definition = term.get('definition') or ''
        if (not _STOCK_OBJECT.search(name) or _FLOW.search(name)
                or not (_HISTORY.search(name+' '+definition) or profession in name)):
            continue
        tree = _reading(example.get('logical_sql') or '', dialect)
        if not tree:
            continue
        refs = {_physical_table(item, source, dialect) for item in tree.find_all(exp.Table)}
        if refs & temporal:
            return True
    for term in source.get('terms', []):
        spec = term.get('config') or {}
        name = term.get('name') or ''
        # Older project packs name the historical relation in period-end
        # metrics even when they do not have an executable snapshot operand.
        # Keep the catalog search open for that authorized relation.
        if (term.get('source_id') == source['id'] and term.get('status') not in ('draft', 'review')
                and _STOCK_OBJECT.search(name) and _HISTORY.search(name) and not _FLOW.search(name)
                and (set(term.get('tables') or ()) & tables.keys()) - snapshots):
            return True
        if (spec.get('kind') != 'semantic-metric' or spec.get('source_id') != source['id']
                or term.get('status') in ('draft', 'review')):
            continue
        label = ' '.join((term.get('name') or '', spec.get('definition') or ''))
        if (spec.get('time_mode') == 'period' and _STOCK_OBJECT.search(label) and not _FLOW.search(term.get('name') or '')
                or any(operand.get('table') == current and operand.get('time_column') for operand in spec.get('operands', []))):
            return True
    if _registered_path(source, current, temporal, tables):
        return True
    for item in temporal:
        label = ' '.join(str(tables[item].get(key) or '') for key in ('name', 'label', 'business_description'))
        if profession in label or _TABLE_HISTORY.search(label):
            return True
    return False


def published_snapshot_history_query(source, described, host):
    """Read the published monthly stock definition for the requested object."""
    from natural_analysis.forecasting import relative_forecast_horizon

    host = host or {}
    publication = source.get('business_project') or {}
    if (not host.get('user_ref') or not publication.get('id') or not publication.get('version')
            or host.get('clarifications') or not (question := _query(host))
            or not (horizon := relative_forecast_horizon(host))):
        return None
    subject = question['subject']
    matches = []
    for term in source.get('terms', []):
        name = unicodedata.normalize('NFKC', term.get('name') or '')
        spec = term.get('config') or {}
        if (not isinstance(spec, dict) or spec.get('kind') != 'semantic-metric'
                or not _STOCK_OBJECT.search(name) or subject not in name
                or spec.get('source_id') != source['id'] or spec.get('time_mode') != 'period'
                or not spec.get('operands') or any(item.get('aggregation') != 'snapshot_sum' for item in spec['operands'])
                or spec.get('group_required')
                or (not _CITY.search(subject) and _CITY.search(name+' '+(spec.get('definition') or '')))
                or not any(metric['id'] == spec.get('id') and metric['version'] == spec.get('version')
                           for metric in described.get('semantic_metrics', []))):
            continue
        closed = sorted(set(p for p in (spec.get('coverage') or [])
                            if re.fullmatch(r'(?:19|20)\d{2}-(?:0[1-9]|1[0-2])', p) and p < horizon['start']))
        if 2 <= len(closed) <= 60:
            matches.append((name, spec, closed))
    if len(matches) != 1:
        return None
    name, spec, closed = matches[0]
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': host['user_text'],
            'source_id': source['id'], 'connection_revision': source.get('connection_revision', 0),
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'metric_id': spec['id'], 'metric_version': spec['version'],
                              'periods': closed, 'title': name[:160], 'chart_type': 'line'}}


def snapshot_forecast_boundary(source, host):
    """Report only a verified current scope and a missing registered history route."""
    host = host or {}
    publication = source.get('business_project') or {}
    if (not host.get('user_ref') or not source.get('id') or not publication.get('id') or not publication.get('version')
            or host.get('clarifications') or not (question := _query(host))
            or any(matching_rules(source, host.get('user_text') or ''))):
        return None
    inventory = source.get('tables') or []
    if not inventory or any(not table.get('columns') or not isinstance(table.get('foreign_keys'), list) for table in inventory):
        return None
    tables = {table['name']: table for table in inventory}
    dialect = sqlglot_name(source['dialect'])
    snapshots = _snapshot_tables(source, dialect)[0] & tables.keys()
    if not snapshots:
        return None
    pairs = [(term, example) for term, example in _published_pairs(source)
             if term.get('status') != 'draft' and example.get('status') != 'draft']
    candidates = set(_current_candidates(source, pairs, tables, snapshots, question['subject'], dialect))
    if len(candidates) != 1:
        return None
    (table, profession, grade, scope, sql, column, unit), = candidates
    if _has_alternative_history(source, pairs, tables, snapshots, table, profession, dialect):
        return None
    calendar_context = temporal_context(host.get('client_time_zone') or 'UTC')
    if calendar_context['zone_fallback']:
        return None
    start = date.fromisoformat(calendar_context['current_date'])
    if start.year == 9999:
        return None
    stop = start.replace(year=start.year+1, day=min(start.day, calendar.monthrange(start.year+1, start.month)[1]))
    subject = grade+profession+'资质单位'
    snapshot = {'scope': scope}
    if unicodedata.normalize('NFKC', column).endswith('('+unit+')'):
        snapshot.update(count_column=column, unit=unit, query_request={
            'source_id': source['id'], 'sql': sql, 'purpose': 'answer', 'title': scope+'当前'+subject+'数量',
        })
    return {'schema_version': 1, 'state': 'unverified_snapshot_history', 'request_ref': host['user_ref'],
            'source_id': source['id'], 'publication': {key: publication[key] for key in ('id', 'version')},
            'connection_revision': source.get('connection_revision', 0), 'subject': subject,
            'window': {'text': question['horizon'], 'start_inclusive': start.isoformat(), 'end_exclusive': stop.isoformat()},
            'current_snapshot': snapshot,
            'evidence': {'kind': 'no_registered_comparable_snapshot_history'}}
