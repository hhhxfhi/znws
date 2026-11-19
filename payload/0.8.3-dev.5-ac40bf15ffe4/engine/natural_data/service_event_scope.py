"""Resolve which event dates an on-time completion population."""

from datetime import date
import re

from sqlglot import exp
from unicodedata import normalize

from .monthly_sql_scope import _bounds, _has_date
from .observed_sql_scope import _column_names, _indicator_predicate, _single_fact
from .request_scope import _and_atoms
from .vendor.dialect import sqlglot_name


_TIMELINESS = re.compile(r'(?:按时|准时)办结率|办结时效|已办结事项的?时效')
_INTAKE = re.compile(r'受理(?:日期|时间)|(?:^|\s)(?:accepted|received)_(?:at|date)(?:$|\s)', re.I)
_CLOSED = re.compile(r'(?:实际)?办结(?:日期|时间)|(?:^|\s)(?:closed|completed)_(?:at|date)(?:$|\s)', re.I)
_DUE = re.compile(r'应办结|计划办结|承诺办结|预计办结|时限|due|deadline', re.I)
_COHORT = re.compile(r'\d{1,2}\s*月(?:份)?(?:内|所|的)?\s*受理|(?:该月|当月|本月|按)\s*受理|受理月份|受理批次|按受理')
_DEADLINE_COHORT = re.compile(r'按(?:照)?(?:应办结|承诺办结)|\d{1,2}\s*月(?:份)?(?:内|所|的)?\s*应办结|应办结月份')
_EXPLICIT_OTHER_WINDOW = re.compile(r'去年|今年|本年|季度|半年|同比|环比|各月|按月|逐月|截止|截至|以来')
_MONTH = re.compile(r'((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月')
_CLOSING_CUTOFF = re.compile(
    r'(?:截至|截止(?:到)?)\s*((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月(?:份|末|底)?\s*(?:已经|已)办结')
_INTAKE_METRIC = re.compile(r'^受理(?:事项|案件|工单|业务)?(?:总)?(?:数|量|件数)|^办结率')
_TIMELY_SHARE = re.compile(r'^已办结(?:事项|案件|工单|业务)?(?:中|的)(?:按时|准时)办结(?:占比|率)')
_TIME_RATE = re.compile(r'^(?:按时|准时)办结率')
_COMPLETED_COUNT = re.compile(r'^已办结(?:事项|案件|工单|业务)?(?:总)?(?:数|量|件数)(?:\(.*\))?$')
_TIMELY_COUNT = re.compile(r'^(?:按时|准时)办结(?:事项|案件|工单|业务)?(?:数|量|件数)(?:\(.*\))?$')


def _requested_window(host):
    text = host.get('user_text') or ''
    if (not _TIMELINESS.search(text) or _DEADLINE_COHORT.search(text)
            or _EXPLICIT_OTHER_WINDOW.search(text) and not _COHORT.search(text)):
        return None
    # For an explicitly dated intake cohort, a closing cutoff is a second
    # role, not a competing interpretation of the intake month.
    interval = re.split(r'截至|截止', text, maxsplit=1)[0] if _COHORT.search(text) else text
    mentions = {tuple(map(int, pair)) for pair in _MONTH.findall(interval)}
    if not mentions and re.search(r'该月|同月|上述月份|这个月|当月', text):
        for recent in reversed((host.get('recent_questions') or [])[-5:]):
            mentions = {tuple(map(int, pair)) for pair in _MONTH.findall(recent)}
            if mentions:
                break
    if len(mentions) != 1 or re.search(r'\d{1,2}月\s*(?:至|到|—|–|-)\s*\d', interval):
        return None
    year, month = next(iter(mentions))
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1), date(year + (month == 12), month % 12 + 1, 1), bool(_COHORT.search(text))


def _time_roles(table):
    roles = {'intake': [], 'close': [], 'due': []}
    for column in table['columns']:
        names = ' '.join(_column_names(column) + [(column.get('time_semantics') or {}).get('name') or ''])
        if not _has_date(table, column['name']):
            continue
        for role, pattern in (('due', _DUE), ('intake', _INTAKE), ('close', _CLOSED)):
            if pattern.search(names):
                roles[role].append(column['name'])
                break
    if all(len(fields) == 1 for fields in roles.values()):
        return tuple(fields[0] for fields in roles.values())


def _event_scope(sql, source, host):
    period = _requested_window(host or {})
    if not period or not (fact := _single_fact(sql, source, allow_projection_cte=True)):
        return None
    tree, table, _, dialect = fact
    if not tree.find(exp.AggFunc) or not tree.args.get('where') or not (roles := _time_roles(table)):
        return None
    intake, close, due = roles
    # The close/deadline comparison in the aggregate ties this fact table
    # to timeliness, rather than any unrelated population of applications.
    if not any(isinstance(node.this, exp.Column) and isinstance(node.expression, exp.Column)
               and {node.this.name, node.expression.name} == {close, due}
               for node in tree.find_all(exp.LT, exp.LTE, exp.GT, exp.GTE)):
        return None
    return tree, dialect, period, intake, close, due


def _closing_cutoff(host):
    text = host.get('user_text') or ''
    matches = _CLOSING_CUTOFF.findall(text)
    if len(matches) != 1:
        return None
    year, month = map(int, matches[0])
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1), date(year + (month == 12), month % 12 + 1, 1)


def catalog_plan(source, host):
    """Plan a timeliness rate when one authorized service fact is identifiable."""
    quote = host.get('user_text') or ''
    text = normalize('NFKC', quote).strip()
    publication = source.get('business_project') or {}
    dialect = sqlglot_name(source.get('dialect'))
    ref = host.get('user_ref')
    if (not ref or host.get('clarifications') or not publication.get('id') or not publication.get('version')
            or dialect not in ('sqlite', 'postgres') or not (window := _requested_window(host))
            or not re.search(r'政务(?:服务)?事项|政务服务', text)
            or any(_TIMELINESS.search(term.get('name') or '') and (term.get('config') or {}).get('kind') == 'semantic-metric'
                   and term.get('status') not in ('draft', 'review') for term in source.get('terms', []))):
        return None
    # Additional population filters, comparisons and analytical tasks stay
    # on the general route. This plan serves only a named month's timeliness.
    remaining = _TIMELINESS.sub('', _MONTH.sub('', _CLOSING_CUTOFF.sub('', text)))
    if not re.fullmatch(r'(?:请|帮我|另外|再|查询|统计|查看|查|显示|给出|该月|同月|当月|本月|这个月|'
                        r'政务(?:服务)?事项|政务服务|实际办结|已办结|受理|的|是多少|多少|说明|'
                        r'时效|表现|前面(?:的)?结果(?:也)?保留|保留前面(?:的)?结果|'
                        r'[\s，,。！？?!、；;])*', remaining):
        return None
    matches = [(table, roles) for table in source['tables'] if (roles := _time_roles(table))
               and any(re.fullmatch(r'(?:服务|办理|业务)?事项(?:名称|编号)?|service_(?:name|id)|case_id',
                                    name, re.I) for col in table['columns'] for name in _column_names(col))]
    if len(matches) != 1:
        return None
    (table, (intake, close, due)), = matches
    start, stop, cohort = window
    cutoff = _closing_cutoff(host) if cohort else None
    if cohort and not cutoff:
        return None
    ident = lambda value: exp.to_identifier(value, quoted=True).sql(dialect=dialect)
    day = lambda value: ("DATE " if dialect == 'postgres' else "") + exp.Literal.string(value.isoformat()).sql(dialect=dialect)
    relation = '.'.join(map(ident, table['name'].split('.')))
    axis, close, due = map(ident, (intake if cohort else close, close, due))
    filters = [f'{axis} >= {day(start)}', f'{axis} < {day(stop)}']
    if cutoff:
        filters.append(f'{close} < {day(cutoff[1])}')
    timely = f'COUNT(CASE WHEN {close} <= {due} THEN 1 END)'
    late = f'COUNT(CASE WHEN {close} > {due} THEN 1 END)'
    sql = (f'SELECT COUNT(1) AS "已办结事项数（件）", {timely} AS "按时办结事项数（件）", '
           f'{late} AS "逾期办结事项数（件）", '
           f'ROUND(100.0 * {timely} / NULLIF(COUNT({due}), 0), 2) AS "按时办结率（%）" '
           f'FROM {relation} WHERE ' + ' AND '.join(filters))
    title = f'{start.year}年{start.month}月' + ('受理事项办结时效' if cohort else '实际办结事项时效')
    return {'schema_version': 1, 'request_ref': ref, 'quote': quote, 'source_id': source['id'],
            'connection_revision': source.get('connection_revision', 0),
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'title': title, 'sql': sql, 'purpose': 'answer'}}


def _keep_cumulative_cutoff(tree, source, host, dialect, close):
    cutoff = _closing_cutoff(host)
    if not cutoff or _bounds(tree, source, exp.column(close), dialect, midnight_timestamp=True) != (*cutoff, False):
        return False
    # The month of the cutoff is an upper boundary. It is not an additional
    # restriction requiring completion during that month.
    atoms = _and_atoms(tree.args['where'].this)
    lower = [atom for atom in atoms if isinstance(atom, exp.GTE)
             and isinstance(atom.this, exp.Column) and atom.this.name == close]
    if len(lower) != 1:
        return False
    tree.set('where', exp.Where(this=exp.and_(*(atom.copy() for atom in atoms if atom is not lower[0]))))
    # A count limited to completed cases cannot be labelled as total intake.
    projections = [item for item in tree.expressions if not _INTAKE_METRIC.search(normalize('NFKC', item.alias))]
    if projections:
        tree.set('expressions', projections)
    return True


def normalize_completion_month(sql, source, host):
    scope = _event_scope(sql, source, host)
    if not scope:
        return sql
    tree, dialect, (start, stop, intake_cohort), intake, close, due = scope
    changed = False
    other_axis = None
    if not intake_cohort:
        candidates = [field for field in (intake, due) if _bounds(
            tree, source, exp.column(field), dialect, midnight_timestamp=True) == (start, stop, False)]
        other_axis = candidates[0] if len(candidates) == 1 else None
    if other_axis:
        if any(field.name == close for field in tree.args['where'].find_all(exp.Column)):
            return sql
        for atom in _and_atoms(tree.args['where'].this):
            if (isinstance(atom, (exp.GTE, exp.LT, exp.Not)) and
                    any(field.name == other_axis for field in atom.find_all(exp.Column))):
                atom.transform(lambda node: exp.column(close, table=node.table or None, quoted=True)
                               if isinstance(node, exp.Column) and node.name == other_axis else node, copy=False)
                changed = True
    if not tree.args.get('group') and not tree.args.get('order') and _bounds(
            tree, source, exp.column(intake if intake_cohort else close), dialect,
            midnight_timestamp=True) == (start, stop, False):
        if intake_cohort and not re.search(r'同时|另外|分别|以及|受理(?:总量|总数|了多少|多少|数量|量)', host.get('user_text') or ''):
            changed = _keep_cumulative_cutoff(tree, source, host, dialect, close) or changed
        changed = _use_completed_rate(tree, host, close, due, intake_cohort) or changed
    return tree.sql(dialect=dialect) if changed else sql


def _use_completed_rate(tree, host, close, due, intake_cohort):
    """Use the completed count as the denominator of the requested timeliness rate."""
    def label(item):
        return normalize('NFKC', item.alias)

    completed = []
    for item in tree.expressions:
        value = item.unalias().unnest()
        if not _COMPLETED_COUNT.fullmatch(label(item)):
            continue
        if isinstance(value, exp.Count) and isinstance(value.this, exp.Column) and value.this.name == close:
            completed.append(value)
        elif (predicate := _indicator_predicate(value)) is not None and (
                any(column.name == close for column in predicate.find_all(exp.Column))
                or any(literal.is_string and literal.this == '已办结' for literal in predicate.find_all(exp.Literal))):
            completed.append(value)
    if len(completed) != 1:
        return False
    denominator, = completed
    changed = False
    rates = [item for item in tree.expressions if _TIME_RATE.search(label(item))]
    for item in rates:
        divisions = list(item.find_all(exp.Div))
        if len(divisions) != 1:
            continue
        original = divisions[0].expression.unnest()
        count = original.this if isinstance(original, exp.Nullif) else original
        if isinstance(count, exp.Count) and isinstance(count.this, exp.Star):
            original.replace(exp.Nullif(this=denominator.copy(), expression=exp.Literal.number(0)))
            changed = True
    if not rates:
        timely = [item.unalias().unnest() for item in tree.expressions if _TIMELY_COUNT.fullmatch(label(item))
                  and (predicate := _indicator_predicate(item.unalias().unnest())) is not None
                  and {close, due} <= {column.name for column in predicate.find_all(exp.Column)}]
        if len(timely) == 1:
            rate = exp.Round(this=exp.Div(
                this=exp.Mul(this=exp.Literal.number('100.0'), expression=timely[0].copy()),
                expression=exp.Nullif(this=denominator.copy(), expression=exp.Literal.number(0)), typed=True),
                decimals=exp.Literal.number(2))
            tree.append('expressions', exp.alias_(rate, '按时办结率（%）', quoted=True))
            changed = True
    if not intake_cohort and not re.search(r'受理|(?<!按时)(?<!准时)办结率|\bintake\b', host.get('user_text') or ''):
        # An actual-completion query cannot also answer an intake-volume question.
        columns = [col for col in tree.expressions if not _INTAKE_METRIC.search(label(col))]
        if changed and any(_TIME_RATE.search(label(col)) for col in columns):
            columns = [col for col in columns if not _TIMELY_SHARE.search(label(col))]
        if columns and len(columns) != len(tree.expressions):
            tree.set('expressions', columns)
            changed = True
    return changed


def completion_period_evidence(sql, source, host):
    scope = _event_scope(sql, source, host)
    if not scope:
        return None
    tree, dialect, (start, stop, intake_cohort), intake, close, _ = scope
    field = intake if intake_cohort else close
    if _bounds(tree, source, exp.column(field), dialect, midnight_timestamp=True) != (start, stop, False):
        raise ValueError(f'EVENT_SCOPE_MISMATCH: 按用户所问的{"受理批次" if intake_cohort else "实际办结月份"}计算办结时效。'
                         f'请在 {field} 上保留 {start} 至 {stop} 的左闭右开期间条件。')
    extra = {}
    if intake_cohort and (cutoff := _closing_cutoff(host)):
        _, stop_completion = cutoff
        if _bounds(tree, source, exp.column(close), dialect, midnight_timestamp=True) != (None, stop_completion, False):
            raise ValueError(f'EVENT_SCOPE_MISMATCH: 保留原受理月份，只纳入 {close} 早于 {stop_completion} 的已办结事项。'
                             '截止月份只限定上界。')
        extra['completed_before'] = stop_completion.isoformat()
    return {'state': 'checked', 'field': field, 'role': 'intake' if intake_cohort else 'completion',
            'start_inclusive': start.isoformat(), 'end_exclusive': stop.isoformat(),
            'meaning': '受理批次' if intake_cohort else '实际办结月份', **extra}
