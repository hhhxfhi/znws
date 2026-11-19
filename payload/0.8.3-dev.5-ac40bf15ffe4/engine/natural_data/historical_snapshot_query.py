"""Read-only whole-province certificate and professional-unit query recipes."""
import re
import unicodedata
from datetime import date, timedelta

import sqlglot
from sqlglot import exp

from .day_bounds import _explicit_days
from .semantic import q, table
from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name


_CERTIFICATE_SUBJECT = re.compile(r'全省有效([\u4e00-\u9fff]{2,28})证书')
_CERTIFICATE_TABLE = re.compile(r'证书|许可证|certificate|(?:^|[_.])licen[cs]es?(?:[_.]|$)', re.I)
_CERTIFICATE_KEY = re.compile(r'证(?:书)?号|证书标识|(?:^|[\W_])(?:cert(?:ificate)?|licen[cs]e|credential|permit)[_\s]*'
                              r'(?:no|id|number|key|code)(?=$|[\W_])|(?:^|[\W_])zsbh(?=$|[\W_])', re.I)
_CERTIFICATE_DATE = re.compile(r'发证|有效期|到期|失效|注销日期|颁发|issued|issuance|grant|expir|revok|'
                               r'(?:^|[\W_])(?:fzsj|yxq)(?=$|[\W_])', re.I)
_SNAPSHOT_WHEN = r'20\d{2}(?:年\d{1,2}月(?:\d{1,2}[日号])?(?:底|末)?|年(?:底|末)|[-/]\d{1,2}(?:[-/]\d{1,2})?)'


def _normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or ''))


def _certificate_share(text, roles, spec):
    grades = '|'.join(map(re.escape, sorted(spec['grades'], key=len, reverse=True)))
    certificate = '(?:' + re.escape(roles['subject_name']) + ')?证书'
    base = r'(?:全部|所有|全体)(?:的)?有效' + certificate + r'(?:总数)?'
    numerator = r'(?:有效)?(?P<grade>' + grades + r')(?:有效)?(?:' + certificate + r'(?:数|数量)?)?'
    ratio = r'(?:占' + base + r'(?:的)?(?:百分比|比例|比重|占比)|在' + base + r'中(?:的)?(?:占比|比例|比重))'
    match = re.match(numerator + ratio + r'(?:是)?(?:多少)?(?=$|[。!?;,])', text)
    if not match:
        return None
    tail = text[match.end():].strip('。!?;,')
    level = re.escape(match['grade'])
    labels = [r'(?:有效)?' + level + r'(?:有效)?' + certificate + r'(?:数|数量)',
              r'(?:全部)?有效' + certificate + r'(?:总数|数量)']
    if tail and not re.fullmatch(r'(?:请)?(?:同时|也|并)?(?:列出|给出|展示)(?:'
                                 + labels[0] + r'(?:和|与|及|、)' + labels[1] + '|'
                                 + labels[1] + r'(?:和|与|及|、)' + labels[0] + ')', tail):
        return None
    return match['grade']


def _absolute_snapshot(text, roles, spec, today, *, prior_province=False):
    day = _explicit_days(text, today)
    if not day or len(day) != 1 or day[0] >= today:
        return None
    region = '|'.join(map(re.escape, ('全省', roles['province_value'])))
    prefix = r'(?:请(?:帮我)?(?:统计|查询|查一下)?|帮我(?:统计|查询))?(?:截至|截止)?' + _SNAPSHOT_WHEN + r'[,]?(?:' + region + r')'
    subject = re.escape(roles['subject_name'])
    grades = '|'.join(map(re.escape, sorted(spec['grades'], key=len, reverse=True)))
    amount = r'(?:(?:有)?多少(?:个|张|本|家)?|数量|总数|合计|数)'
    share_intro = (
        r'(?:请(?:帮我)?(?:统计|计算|查询)?|帮我(?:统计|计算|查询))?'
        r'(?:以' + _SNAPSHOT_WHEN + r'(?:为|作为)(?:统计时点|统计截止日|时点|基准日)'
        r'|(?:截至|截止)?' + _SNAPSHOT_WHEN + r')[,]?(?:' + region + r')'
        r'(?:的)?(?:全部)?有效' + subject + r'证书(?:中|里)[,]?'
    )
    if intro := re.match(share_intro, text):
        if level := _certificate_share(text[intro.end():], roles, spec):
            return 'certificate_share', day[0], [level], None
    certificate = re.fullmatch(prefix + '有效' + subject + r'证书' + amount + r'(?P<tail>.*)', text)
    if certificate:
        tail = [part for part in re.split(r'[。!?;,]+', certificate['tail']) if part]
        selected = [re.fullmatch(r'(?:有效)?(' + grades + r')(?:有效)?证书' + amount, part) for part in tail]
        if all(selected) and len({part[1] for part in selected}) == len(selected):
            return 'certificate', day[0], [grade for grade in spec['grades'] if any(m[1] == grade for m in selected)], None
    matrix = re.fullmatch(prefix + r'(?:各|所有|全部)专业(?P<grades>(?:' + grades + r')(?:(?:和|与|及|、|/)(?:'
                          + grades + r'))*)' + subject + r'单位(?:分别)?' + amount
                          + r'(?:[。!?;,]+按["“「]?专业[xX×*]等级["”」]?列出)?[。!?;,]*', text)
    if matrix:
        selected = re.split(r'和|与|及|、|/', matrix['grades'])
        if len(set(selected)) == len(selected):
            return 'unit', day[0], [grade for grade in spec['grades'] if grade in selected], None
    professions = '|'.join(map(re.escape, sorted(spec['professions'], key=len, reverse=True)))
    scope = '(?:' + region + ')' + ('?' if prior_province else '')
    single_profession = re.fullmatch(
        r'(?:请(?:帮我)?)?(?:重新|再)?(?:查询|查|统计|看)?(?:截至|截止)?' + _SNAPSHOT_WHEN + ',?'
        + scope + r'(?P<profession>' + professions + r')(?:专业)?的?有效' + subject + r'单位[,]?'
        + r'(?P<levels>(?:' + grades + r')(?:(?:和|与|及|、|/)(?:' + grades + r'))*)'
        + r'(?:分别|各)?' + amount + r'[。!?;,]*', text)
    if single_profession:
        selected = re.split(r'和|与|及|、|/', single_profession['levels'])
        if len(set(selected)) == len(selected):
            return ('unit', day[0], [level for level in spec['grades'] if level in selected],
                    single_profession['profession'])
    return None


def _unit_refinement(text, spec):
    grades = '|'.join(map(re.escape, sorted(spec['grades'], key=len, reverse=True)))
    professions = '|'.join(map(re.escape, sorted(spec['professions'], key=len, reverse=True)))
    if profession := re.fullmatch(r'(?:再)?(?:只|仅)(?:看|查)(' + professions + r')(?:专业)?[。!?]*', text):
        return 'profession', profession[1], None
    if grade := re.fullmatch(r'(?:再)?(?:只|仅)(?:看|查)(' + grades + r')[。!?]*', text):
        return 'grade', grade[1], None
    if change := re.fullmatch(r'改查(' + grades + r'),并取消(' + professions
                              + r')(?:专业)?的限制,月份不变[。!?]*', text):
        return 'clear_profession', change[1], change[2]
    return None


def _snapshot_request(host, roles, spec):
    today = date.fromisoformat(temporal_context(host.get('client_time_zone') or 'UTC')['current_date'])
    current = _normalized(host.get('user_text'))
    if absolute := _absolute_snapshot(current, roles, spec, today):
        return absolute
    recent = host.get('recent_requests') or []
    if recent and not host.get('clarifications'):
        previous = recent[-1]
        continuation = re.match(r'^(?:其中|上述(?:结果)?中|刚才的结果中)[,]?', current)
        if continuation and previous.get('user_ref') and not previous.get('clarifications'):
            anchor = _absolute_snapshot(_normalized(previous.get('text')), roles, spec, today)
            level = _certificate_share(current[continuation.end():], roles, spec)
            if level and anchor and anchor[0] in ('certificate', 'certificate_share'):
                return 'certificate_share', anchor[1], [level], None
    if prior := _absolute_snapshot(current, roles, spec, today, prior_province=True):
        if host.get('clarifications'):
            return None
        collapsed = host.get('historical_snapshot_recent_requests') or []
        if (recent and recent[-1].get('text') == host.get('user_text')
                and isinstance(collapsed, list) and collapsed and collapsed[-1] == recent[-1]):
            recent = collapsed
        professions = '|'.join(map(re.escape, spec['professions']))
        grades = '|'.join(map(re.escape, spec['grades']))
        retained_month = (r'月份(?:还是|仍是|仍为)' + _SNAPSHOT_WHEN + r',再(?:看|查)(?:' + professions
                          + r')(?:专业)?的?(?:' + grades + r')单位(?:数量|数)[。!?]*')
        for item in reversed(recent):
            phrase = _normalized(item.get('text'))
            if not item.get('user_ref') or item['user_ref'] == host.get('user_ref') or item.get('clarifications'):
                return None
            if previous := _absolute_snapshot(phrase, roles, spec, today):
                return prior if previous[0] == 'unit' and previous[1] == prior[1] else None
            if previous := _absolute_snapshot(phrase, roles, spec, today, prior_province=True):
                if previous[0] != 'unit' or previous[1] != prior[1]:
                    return None
                continue
            if (_unit_refinement(phrase, spec) or re.fullmatch(retained_month, phrase)
                    and _explicit_days(phrase, today) == [prior[1]]):
                continue
            return None
        return None
    resume = re.fullmatch(r'(?:请)?继续(?:完成)?(?:上一题|上个问题|原问题|未完成的回答)[。!?]*', current)
    if not resume and not _unit_refinement(current, spec):
        return None
    history = [item.get('text') for item in recent] if recent else host.get('recent_questions') or []
    changes = [] if resume else [_unit_refinement(current, spec)]
    for phrase in reversed(history):
        text = _normalized(phrase)
        if anchor := _absolute_snapshot(text, roles, spec, today):
            break
        if not (change := _unit_refinement(text, spec)):
            return None
        changes.append(change)
    else:
        return None
    subject, cutoff, grades, profession = anchor
    if subject != 'unit' and changes:
        return None
    for kind, value, old_profession in reversed(changes):
        if kind == 'profession':
            profession = value
        elif kind == 'grade':
            grades = [value]
        elif old_profession == profession:
            grades, profession = [value], None
        else:
            return None
    return subject, cutoff, grades, profession


def certificate_catalog_boundary(source, host, spec):
    """Prove that the authorized catalog offers no matching historical certificate path."""
    from .business_ambiguity import matching_rules
    from .catalog_view import snapshot_boundaries
    from .event_dimension_scope import _registered_path
    from .snapshot_forecast_scope import _time_bearing

    quote = host.get('user_text') or ''
    subject = _CERTIFICATE_SUBJECT.search(_normalized(quote))
    publication = source.get('business_project') or {}
    if (not subject or not host.get('user_ref') or not source.get('id') or not publication.get('id')
            or not publication.get('version') or host.get('clarifications') or spec['custom']
            or source.get('metric_mapping') or any(matching_rules(source, quote))):
        return None
    roles = {'province_value': '全省', 'subject_name': subject[1]}
    clock = temporal_context(host.get('client_time_zone') or 'UTC')
    if clock['zone_fallback'] or not (request := _absolute_snapshot(_normalized(quote), roles, spec,
                                                                  date.fromisoformat(clock['current_date']))):
        return None
    kind, cutoff, grades, _ = request
    inventory = source.get('tables') or []
    if (kind not in ('certificate', 'certificate_share') or not inventory or any(not t.get('columns') or not isinstance(t.get('foreign_keys'), list)
                                                      for t in inventory)):
        return None
    tables = {t['name']: t for t in inventory}
    snapshots = {entry['table'] for entry in snapshot_boundaries(source, quote, clock)
                 if entry['state'] == 'current_snapshot_only' and entry.get('subject_mismatch')
                 and entry.get('published_subjects') == ['单位'] and entry.get('requested_subjects') == ['证书']}
    if len(snapshots) != 1 or not snapshots <= tables.keys():
        return None
    temporal = {name for name, record in tables.items() if _time_bearing(record)}
    current, = snapshots
    if current in temporal or _registered_path(source, current, temporal, tables):
        return None
    # A possible authorized alternative is enough to keep the normal search path.
    # Absence of an explicit registration, by itself, cannot justify the answer.
    mappings = {(row['table'], row.get('column')): row for row in source.get('business_dictionary', [])
                if row.get('source_id') == source['id']}
    for name, table_info in tables.items():
        if name == current:
            continue
        table_mapping = mappings.get((name, None), mappings.get((name, ''), {}))
        table_label = ' '.join(str(table_mapping.get(key) or '') for key in ('name', 'description'))
        labels = [' '.join((column['name'], str(column.get('label') or ''), str(column.get('business_name') or ''),
                            str(mappings.get((name, column['name']), {}).get('name') or '')))
                  for column in table_info['columns']]
        if (_CERTIFICATE_TABLE.search(' '.join((name, table_info.get('label') or '', table_label)))
                or any(_CERTIFICATE_KEY.search(label.strip()) for label in labels)
                and any(_CERTIFICATE_DATE.search(label.strip()) for label in labels)):
            return None
    matching = lambda name: (roles['subject_name'] in name and '证书' in name
                             or re.search(r'有效.{0,8}证书|证书.{0,8}历史', name))
    if (any(matching(row.get('name') or '') for row in source.get('terms', []))
            or any(matching(row.get('question') or '') for row in source.get('examples', []))
            or any(matching(row.get('name') or '') and row.get('time_mode') == 'period'
                   for row in source.get('semantic_metrics', []))):
        return None
    return {'schema_version': 1, 'state': 'unverified_historical_certificate', 'request_ref': host['user_ref'],
            'source_id': source['id'], 'publication': {key: publication[key] for key in ('id', 'version')},
            'subject_label': roles['subject_name']+'证书', 'scope': '全省', 'cutoff': cutoff.isoformat(), 'grades': grades,
            'evidence': {'kind': 'no_authorized_historical_certificate_path', 'current_snapshot_only': True,
                         'inventory_checked': True}}


def _table(source, name):
    exact = [item for item in source.get('tables', []) if item['name'] == name]
    if exact:
        return exact[0] if len(exact) == 1 else None
    matches = [item for item in source.get('tables', []) if item['name'].split('.')[-1] == name]
    return matches[0] if len(matches) == 1 else None


def _literal(value):
    return exp.convert(value).sql()


def _bound_field(alias, name):
    return alias + '.' + q(name)


def _population(cert, cancel, roles, cutoff, *, province=True):
    def c(role):
        return _bound_field('z', roles[role])

    def s(role):
        return _bound_field('s', roles[role])

    next_day = _literal((cutoff + timedelta(days=1)).isoformat())
    criteria = [
        c('certificate_key') + ' IS NOT NULL',
        c('issued_column') + ' < ' + next_day,
        c('expiry_column') + ' >= ' + _literal(cutoff.isoformat()),
        'NOT EXISTS (SELECT 1 FROM ' + table(cancel['name']) + ' AS s WHERE '
        + s('cancel_key') + ' = ' + c('certificate_key')
        + ' AND ' + s('cancelled_column') + ' = ' + _literal(roles['cancelled_value'])
        + ' AND ' + s('cancel_date_column') + ' < ' + next_day + ')',
    ]
    if province:
        criteria.append(c('province_column') + ' = ' + _literal(roles['province_value']))
    return ' FROM ' + table(cert['name']) + ' AS z WHERE ' + ' AND '.join(criteria)


def _legacy_example(source, cert, cancel, roles, origins, dialect):
    """The preexisting full-scope certificate example declares the historical relations."""
    for example in source.get('examples', []):
        if example.get('origin') not in origins or example.get('source_id') != source['id'] or not example.get('logical_sql'):
            continue
        label = example.get('question') or ''
        if '全省' not in label or '有效' not in label or roles['subject_name'] + '证书' not in label:
            continue
        days = _explicit_days(label, date.today())
        if not days or len(days) != 1:
            continue
        try:
            tree = sqlglot.parse_one(example['logical_sql'], read=dialect)
            expected = sqlglot.parse_one('SELECT COUNT(DISTINCT ' + _bound_field('z', roles['certificate_key']) + ')'
                                         + _population(cert, cancel, roles, days[0], province=False), read=dialect)
            # The published example may predate physical identifier quoting.
            for node in (*tree.find_all(exp.Identifier), *expected.find_all(exp.Identifier)):
                node.set('quoted', False)
            known = {ref.name: ref for ref in expected.find_all(exp.Table)}
            for ref in tuple(tree.find_all(exp.Table)):
                if ref.name in known and not (ref.db or ref.catalog):
                    ref.replace(known[ref.name].copy())
            if isinstance(tree, exp.Select) and len(tree.expressions) == 1:
                if tree.expressions[0].unalias() == expected.expressions[0] and tree.args == {
                        **expected.args, 'expressions': tree.expressions}:
                    return True
        except sqlglot.errors.SqlglotError:
            continue
    return False


def _mapped_labels(column, reference, labels):
    enums = column.get('business_values') or []
    if not enums:
        return reference
    clauses = []
    for label in labels:
        values = [_literal(item['value']) for item in enums if item['label'] == label]
        if not values:
            return None
        clauses.append('WHEN ' + reference + ' IN (' + ', '.join(values) + ') THEN ' + _literal(label))
    return 'CASE ' + ' '.join(clauses) + ' ELSE NULL END'


def recipe(source, host, spec, roles, *, legacy_origins=None):
    publication = source.get('business_project') or {}
    if not publication.get('id') or not publication.get('version') or not source.get('id') or not (host or {}).get('user_ref'):
        return None
    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres') or not (request := _snapshot_request(host, roles, spec)):
        return None
    subject, cutoff, grades, profession_filter = request
    share = subject == 'certificate_share'
    if share:
        subject = 'certificate'
    cert = _table(source, spec['cert_table'])
    cancel = _table(source, spec['cancel_table'])
    if not cert or not cancel or cert['name'] == cancel['name']:
        return None
    cert_columns = {item['name']: item for item in cert['columns']}
    cancel_columns = {item['name']: item for item in cancel['columns']}
    grade_column, profession_column = spec['grade_column'], spec['profession_column']
    required = {roles[key] for key in ('certificate_key', 'issued_column', 'expiry_column', 'province_column')}
    if grades:
        required.add(grade_column)
    if subject == 'unit':
        required.update((roles['unit_key'], profession_column))
    if not required <= cert_columns.keys() or not {
            roles['cancel_key'], roles['cancelled_column'], roles['cancel_date_column']} <= cancel_columns.keys():
        return None
    if legacy_origins and not _legacy_example(source, cert, cancel, roles, legacy_origins, dialect):
        return None
    key = _bound_field('z', roles['certificate_key' if subject == 'certificate' else 'unit_key'])
    if grades:
        grade = _mapped_labels(cert_columns[grade_column], _bound_field('z', grade_column), spec['grades'])
        if not grade:
            return None
    projections = {}
    group = ''
    invalid = []
    if grades:
        valid_grades = ', '.join(map(_literal, spec['grades']))
        invalid.extend([grade + ' IS NULL', grade + ' NOT IN (' + valid_grades + ')'])
    population = _population(cert, cancel, roles, cutoff)
    if subject == 'certificate':
        unit = '个'
        projections['统计时点'] = _literal(cutoff.isoformat())
        total = 'COUNT(DISTINCT ' + key + ')'
        projections['有效证书总数（个）'] = total
    else:
        if spec['profession_separator']:
            return None
        unit = '家'
        profession = _mapped_labels(cert_columns[profession_column], _bound_field('z', profession_column), spec['professions'])
        if not profession:
            return None
        projections['专业名称'] = profession
        group = ' GROUP BY ' + profession
        valid_professions = ', '.join(map(_literal, spec['professions']))
        invalid.extend([key + ' IS NULL', 'TRIM(CAST(' + key + " AS VARCHAR)) = ''",
                        profession + ' IS NULL', profession + ' NOT IN (' + valid_professions + ')'])
    single_grade = subject == 'unit' and len(grades) == 1
    if single_grade:
        projections['等级'] = _literal(grades[0])
        projections['单位数（家）'] = 'COUNT(DISTINCT ' + key + ')'
    else:
        for level in grades:
            label = level + ('证书数（个）' if subject == 'certificate' else '单位数（家）')
            count = 'COUNT(DISTINCT CASE WHEN ' + grade + ' = ' + _literal(level) + ' THEN ' + key + ' END)'
            if share:
                incomplete = ('EXISTS (SELECT 1' + population + ' AND (' + ' OR '.join(invalid) + '))'
                              + ' OR EXISTS (SELECT 1' + population + ' GROUP BY ' + key
                              + ' HAVING COUNT(DISTINCT ' + grade + ') > 1)')
                count = 'CASE WHEN ' + incomplete + ' THEN NULL ELSE ' + count + ' END'
                projections[label] = count
                projections[level + '占全部有效证书（%）'] = 'ROUND(100.0 * (' + count + ') / NULLIF(' + total + ', 0), 2)'
            else:
                projections[label] = count
    sql = 'SELECT ' + ', '.join(value + ' AS ' + q(label) for label, value in projections.items())
    sql += population
    if invalid and not share:
        # One unknown identity or category poisons the comparable population,
        # rather than silently dropping the affected group from a complete list.
        sql += ' AND NOT EXISTS (SELECT 1' + population + ' AND (' + ' OR '.join(invalid) + '))'
    if profession_filter:
        sql += ' AND ' + profession + ' = ' + _literal(profession_filter)
    if single_grade:
        sql += ' AND ' + grade + ' = ' + _literal(grades[0])
    sql += group + ' HAVING COUNT(*) > 0'
    if group:
        sql += ' ORDER BY ' + (q('单位数（家）') + ' DESC, ' if single_grade else '') + q('专业名称')
    title = cutoff.isoformat() + ' 全省有效' + roles['subject_name'] + ('证书' if subject == 'certificate' else '单位按专业和等级')
    return {'sql': sql, 'title': title, 'subject': subject, 'cutoff': cutoff.isoformat(), 'unit': unit, 'share': share,
            'columns': list(projections), 'basis': 'registered_inference' if legacy_origins else 'registered_definition'}
