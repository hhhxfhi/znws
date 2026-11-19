"""Validate an explicit current calendar month against a proven query time field."""

from datetime import date
import re
import unicodedata

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from .catalog_index import fingerprint
from .read_limits import execution_basis
from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name


_CURRENT_MONTH = re.compile(r'当前(?:的)?月份(?![末底])|当前月(?![份度末底])|本月(?![末底])|这个月(?![末底])')
_MONTH_REFINEMENT = re.compile(
    r'(?:请\s*)?(?:帮我\s*)?(?:(?:只|仅)\s*)?(?:(?:计算|算|统计|汇总|查(?:询)?|看(?:一下)?|取)\s*)?'
    r'(?:当前(?:的)?月(?:份)?|本月|当月|这个月)\s*(?:的|至今|到(?:目前|现在)为止)?\s*'
    r'累[计积](?:值|量|数|件数|数量|记录数)?\s*(?:有?多少|即可|就行|呢)?[。!?]?'
)

_OTHER_PERIOD = re.compile(r'最新.{0,8}(?:月份|有数据)|最近.{0,8}(?:有数据|可用月份)|'
                           r'上月|上个月|去年|前年|同期|同比|环比|预测|'
                           r'(?<!\d)(?:19|20)\d{2}(?:\s*年|[-/]\d{1,2})|(?<!\d)\d{1,2}\s*月')
_REFINEMENT = re.compile(r'^(?:请)?(?:(?:只|仅)(?:算|查|看|取|统计)|改为|换成)|刚才|上一轮|原口径')
_CURRENT_CUMULATIVE = re.compile(r'(?:(?:当前|目前|截至现在)(?:的)?|(?:本年|今年)(?:的)?)累[计积]')
_YEAR_CONTEXT = re.compile(r'本年度|本年|今年|当年度?')
_NOT_FLOW = re.compile(r'余额|在册|存量|快照|截至[\d一二三四五六七八九十]+|截止|最近|可用月份|往年|历年|累计同比|月累计')


def month_refinement(question):
    """Identify a period-only follow-up, with no additional measure or population."""
    return bool(_MONTH_REFINEMENT.fullmatch(unicodedata.normalize('NFKC', question).strip()))

def calendar_month(host):
    clock = temporal_context(host.get('client_time_zone', 'UTC'))
    if clock['zone_fallback']:
        return None
    today = date.fromisoformat(clock['current_date'])
    first = today.replace(day=1)
    stop = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return {'period': first.strftime('%Y-%m'), 'calendar_date': today.isoformat(), 'time_zone': clock['time_zone'],
            'starts_inclusive': [first.isoformat()], 'end_exclusive': stop.isoformat(), 'mode': 'month'}


def intent(host):
    """Use only host-provided human text and calendar, independently of stored data."""
    host = host or {}
    quote = unicodedata.normalize('NFKC', host.get('user_text') or '')
    explicit_month = bool(_CURRENT_MONTH.search(quote) or month_refinement(quote))
    current_cumulative = (not explicit_month and bool(_CURRENT_CUMULATIVE.search(quote)) and not _NOT_FLOW.search(quote)
                          and bool(_YEAR_CONTEXT.search(quote) or any(_YEAR_CONTEXT.search(text) or re.search(r'(?<!\d)20\d{2}\s*年', text)
                              for text in (host.get('recent_questions') or [])[-8:])))
    if not (explicit_month or current_cumulative) or _OTHER_PERIOD.search(quote):
        return None
    if not (month := calendar_month(host)):
        return None
    first, = month['starts_inclusive']
    annual_start = first[:4] + '-01-01'
    if re.search(r'累计|累积', quote):
        if current_cumulative or re.search(r'今年|本年|年度累计|年内累计', quote):
            return {**month, 'starts_inclusive': [annual_start], 'mode': 'year_to_date'}
        if annual_start != first:
            return {**month, 'starts_inclusive': [first, annual_start], 'mode': 'month_or_year_to_date'}
    return month


def _table(tree, source):
    from .monthly_sql_scope import _source_table
    if not isinstance(tree, exp.Select) or tree.args.get('joins') or any(tree.find_all(exp.CTE, exp.Subquery)):
        return None
    refs = list(tree.find_all(exp.Table))
    return _source_table(refs[0], source) if len(refs) == 1 else None


def _parse(sql, dialect):
    try:
        return parse_one(sql, read=dialect) if sql else None
    except SqlglotError:
        return None


def date_axis(tree, source, table, dialect, *, published_column=None):
    """Prefer a unique registered date field, otherwise one unambiguous physical axis."""
    from .monthly_sql_scope import _bounds, _has_date
    dates = [column for column in table.get('columns', []) if _has_date(table, column['name'])]
    declared = [column for column in dates if column.get('time_semantics')]
    choices = [column for column in dates if column['name'] == published_column] if published_column else declared or dates
    if len(choices) != 1:
        return None
    column = choices[0]
    if window := _bounds(tree, source, exp.column(column['name']), dialect):
        return column, window
    return None


def _previous_answer(engine, ctx, host):
    recent = host.get('recent_questions') or []
    if (not engine or not ctx or not ctx.session_id or not recent or not host.get('user_ref')
            or not _REFINEMENT.search((host.get('user_text') or '').strip())):
        return None
    try:
        if getattr(engine, 'policy', None):
            engine.policy.require_history(ctx.project)
        _, head = engine.artifacts.head(ctx.project, 'session:' + ctx.session_id, 'work.tasks')
    except PermissionError:
        return None
    tasks = [t for t in (head or {}).get('payload', {}).get('tasks', [])
             if t.get('capability_id') == 'data' and t.get('result_refs')]
    for task in sorted(tasks, key=lambda t: t['revision'], reverse=True):
        request = (task.get('request_scope') or {}).get('request') or {}
        if not request.get('user_ref') or (request.get('quote') != recent[-1] and request['user_ref'] != host['user_ref']):
            continue
        for ref in reversed(task.get('result_refs') or []):
            if not ref.startswith('data:'):
                continue
            try:
                result = engine.raw(ctx.project, ref.removeprefix('data:'))
            except PermissionError:
                continue
            lineage = result.get('result_lineage') or {}
            saved_request = (result.get('request_scope') or {}).get('request') or {}
            if (lineage.get('session_id') == ctx.session_id and lineage.get('purpose') == 'answer'
                    and saved_request.get('user_ref') and saved_request['user_ref'] != host['user_ref']
                    and lineage.get('request_ref') == saved_request['user_ref']
                    and saved_request.get('quote') == recent[-1]):
                return result
    return None


def _policy_tree(result, dialect):
    sql = result.get('sql') or ''
    basis = result.get('execution_basis') or {}
    policy = basis.get('policy_sql')
    if not sql or not policy or execution_basis(policy, sql) != basis:
        return None
    return _parse(policy, dialect)


def _count_scope(tree, source, table, field):
    from .monthly_sql_scope import _literal_day
    from .period_count_coverage import _terms
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    dialect = sqlglot_name(source.get('dialect'))
    if (not tree or not table or _table(tree, source) != table or dialect not in ('postgres', 'sqlite')
            or any(value and key not in ('expressions', 'from', 'where', 'group', 'order') for key, value in tree.args.items())
            or tree.find(exp.Window, exp.Placeholder, exp.Parameter, exp.Exists)):
        return None
    relation = tree.args['from'].this
    if any(relation.args.get(key) for key in ('sample', 'version')) or relation.alias_column_names:
        return None
    counts = [(item, value) for item in tree.expressions if isinstance(value := item.unalias().unnest(), exp.Count)]
    if len(counts) != 1:
        return None
    item, count = counts[0]
    if count.expressions or not (isinstance(count.this, exp.Star) and not any(count.this.args.values())
            or isinstance(count.this, exp.Literal) and count.this.is_int and count.this.this == '1'):
        return None
    filters = []
    where = tree.args.get('where')
    for atom in _terms(where.this if where else None):
        if (isinstance(atom, (exp.GTE, exp.LT)) and isinstance(atom.this, exp.Column)
                and atom.this.name.casefold()==field.casefold() and _literal_day(atom.expression, dialect)):
            continue
        # Only simple same-table equalities and enumerations are reusable.
        # In particular, no OR, date transformation, function or inferred subset.
        if (not isinstance(atom, (exp.EQ, exp.In)) or not isinstance(atom.this, exp.Column)
                or atom.this.table not in ('', relation.alias_or_name) or atom.this.db or atom.this.catalog):
            return None
        values = [atom.expression] if isinstance(atom, exp.EQ) else atom.expressions
        if not values or not all(isinstance(value, exp.Literal) for value in values):
            return None
        column = normalize_identifiers(atom.this.copy(), dialect=dialect).name
        filters.append((column, tuple(sorted((value.is_string, value.this) for value in values))))
    output = normalize_identifiers(item.copy(), dialect=dialect).alias_or_name if dialect == 'postgres' else item.alias_or_name
    return tuple(sorted(filters)), output


def _prior_count_results(engine, project, session, request_ref, source):
    if not session or not request_ref or not (publication := source.get('business_project')):
        return
    try:
        if getattr(engine, 'policy', None):
            engine.policy.require_history(project)
        _, head = engine.artifacts.head(project, 'session:' + session, 'work.tasks')
    except PermissionError:
        return
    catalog_fingerprint = fingerprint(source)
    tasks = [task for task in (head or {}).get('payload', {}).get('tasks', []) if task.get('capability_id')=='data' and task.get('result_refs')]
    for task in sorted(tasks, key=lambda task: task['revision'], reverse=True)[:20]:
        for artifact in reversed(task['result_refs'][-4:]):
            if not artifact.startswith('data:'):
                continue
            try:
                prior = engine.raw(project, artifact.removeprefix('data:'))
            except PermissionError:
                continue
            lineage = prior.get('result_lineage') or {}
            scope = prior.get('request_scope') or {}
            if (prior.get('status')!='succeeded' or prior.get('truncated')
                    or prior.get('row_count')!=len(prior.get('rows') or []) or (prior.get('receipt') or {}).get('read_only') is not True
                    or lineage.get('purpose')!='answer' or lineage.get('session_id')!=session
                    or not lineage.get('request_ref') or lineage['request_ref']==request_ref
                    or lineage['request_ref']!=(scope.get('request') or {}).get('user_ref')
                    or not scope.get('request',{}).get('quote')
                    or any(value.get('source_id')!=source['id'] for value in (prior, lineage, prior.get('receipt') or {}))
                    or prior.get('business_project')!=publication or lineage.get('config_fingerprint')!=catalog_fingerprint
                    or (prior.get('validation') or {}).get('state')=='conflict'
                    or (prior.get('source_provenance') or {}).get('connection_revision')!=source.get('connection_revision')):
                continue
            yield prior


def _matching_annual_series(engine, project, session, host, source, tree, table, field):
    """Yield complete prior month facts for the exact same authorization and predicates."""
    from .monthly_sql_scope import summarize
    if not (population := _count_scope(tree, source, table, field['name'])):
        return
    dialect = sqlglot_name(source.get('dialect'))
    current_year = temporal_context(host.get('client_time_zone') or 'UTC')['current_month'][:4]
    for prior in _prior_count_results(engine, project, session, host.get('user_ref'), source):
        window = prior['request_scope'].get('monthly_sql_window') or {}
        if (not window or window.get('kind') or window.get('query_window', {}).get('state') != 'checked'
                or window.get('query_window', {}).get('start_inclusive') != current_year+'-01-01'):
            continue
        prior_tree = _policy_tree(prior, dialect)
        if not prior_tree or not (axis := date_axis(prior_tree, source, table, dialect)) or axis[0]['name']!=field['name']:
            continue
        prior_population = _count_scope(prior_tree, source, table, field['name'])
        if not prior_population or prior_population[0] != population[0]:
            continue
        summary = summarize(prior, window)
        if summary and summary.get('observations'):
            yield summary, prior, axis[0]


def _prior_current_month_absence(priors, source, tree, table, field, month, cumulative_stop):
    """Reuse absence only when the earlier read covers the entire current portion."""
    if not (population := _count_scope(tree, source, table, field['name'])):
        return None
    dialect = sqlglot_name(source.get('dialect'))
    month_start = date.fromisoformat(month['period']+'-01')
    month_stop = date.fromisoformat(month['end_exclusive'])
    required_stop = min(cumulative_stop, month_stop)
    if required_stop <= month_start:
        return None
    for prior in priors:
        if (not (prior_tree := _policy_tree(prior, dialect))
                or not (axis := date_axis(prior_tree, source, table, dialect)) or axis[0] != field
                or not (prior_population := _count_scope(prior_tree, source, table, field['name']))
                or prior_population[0]!=population[0]):
            continue
        prior_start, prior_stop, unknown = axis[1]
        if unknown or prior_start != month_start or not prior_stop or not required_stop <= prior_stop <= month_stop:
            continue
        if prior_tree.args.get('group'):
            if prior.get('reason_code')!='NO_MATCH' or prior['row_count']!=0:
                continue
        else:
            contract = prior.get('sql_answer_contract') or prior.get('answer_contract') or {}
            proof = contract.get('period_count_coverage') or {}
            lineage = prior['result_lineage']
            identity = {'schema_version':1, 'result_ref':prior['result_ref'], 'request_ref':lineage['request_ref'],
                        'source_id':source['id'], 'publication':{key:prior['business_project'][key] for key in ('id','version')}}
            column = prior_population[1]
            entry = {'column':column,'period':month['period'],'start_inclusive':prior_start.isoformat(),
                     'end_exclusive':prior_stop.isoformat(),'state':'unobserved','observed_rows':0}
            entries = proof.get('columns') or []
            if (any(proof.get(key)!=value for key,value in identity.items()) or prior['row_count']!=1
                    or prior.get('columns')!=[column] or prior.get('rows')!=[{column:None}]
                    or len(entries)!=1 or any(entries[0].get(key)!=value for key,value in entry.items())):
                continue
        if any(check.get('state')=='checked' and (constraint := check.get('constraint') or {}).get('kind')=='current_month'
               and constraint.get('table')==table['name'] and constraint.get('column')==field['name']
               and constraint.get('period')==month['period'] for check in (prior.get('validation') or {}).get('checks', [])):
            return {'result_ref':prior['result_ref'],'period':month['period'],'state':'unobserved',
                    'start_inclusive':prior_start.isoformat(),'end_exclusive':prior_stop.isoformat()}
    return None


def _matching_published_grouping(source, start, stop, result, grouping):
    expected = {'schema_version':1, 'result_ref':result['result_ref'], 'request_ref':result['result_lineage']['request_ref'],
                'source_id':source['id'], 'source_revision':source.get('target_revision', 0),
                'publication':{key:source['business_project'][key] for key in ('id', 'version')},
                'start_inclusive':start.isoformat(), 'end_exclusive':stop.isoformat()}
    return all(grouping.get(key)==value for key,value in expected.items())


def _published_grouping_prior(priors, source, tree, table, field, start, stop, result, grouping):
    if not _matching_published_grouping(source, start, stop, result, grouping):
        return None
    population = _count_scope(tree, source, table, field['name'])
    dialect = sqlglot_name(source.get('dialect'))
    for prior in priors:
        if prior['result_ref']!=grouping.get('source_result_ref') or not population:
            continue
        tree = _policy_tree(prior, dialect)
        scope = _count_scope(tree, source, table, field['name'])
        if scope and scope[0]==population[0] and date_axis(tree, source, table, dialect)==(field, (start, stop, False)):
            return prior
    return None


def bind(source, month, evidence, *, host, engine=None, ctx=None, sql=None):
    from .monthly_sql_scope import _bounds, _has_date
    dialect = sqlglot_name(source.get('dialect'))
    tree = _parse(sql, dialect)
    table = _table(tree, source)
    tables = [table] if table else source.get('tables', []) if not sql else []
    published = [(t, c) for t in tables for c in t.get('columns', []) if c.get('time_semantics')]

    def constraint(table, column, basis, **extra):
        return {'kind': 'current_month', **month, 'table': table['name'], 'column': column['name'],
                'grain': (column.get('time_semantics') or {}).get('grain'), 'basis': basis,
                'evidence': evidence, **extra}

    if (table and month.get('basis') == 'published_grouping' and month.get('source_id') == source['id']
            and month.get('table') == table['name']):
        field = next((column for column in table['columns'] if column['name'] == month['column']), None)
        if field and _has_date(table, field['name']):
            return constraint(table, field, 'published_grouping')

    if month.get('mode') == 'year_to_date' and not _YEAR_CONTEXT.search(host.get('user_text', '')):
        # The user's recent text is only a hint to try resolving the period.
        # A registered date on a different table/source cannot inherit it.
        if engine and ctx and table and (axis := date_axis(tree, source, table, dialect)):
            for _, prior, prior_axis in _matching_annual_series(engine, ctx.project, ctx.session_id, host, source, tree, table, axis[0]):
                return constraint(table, prior_axis, 'previous_result', source_result_ref=prior['result_ref'])
            scope = _count_scope(tree, source, table, axis[0]['name'])
            if scope:
                for prior in _prior_count_results(engine, ctx.project, ctx.session_id, host['user_ref'], source):
                    prior_tree = _policy_tree(prior, dialect)
                    if not prior_tree or not (prior_scope := _count_scope(prior_tree, source, table, axis[0]['name'])):
                        continue
                    if (scope[0]==prior_scope[0] and (previous_axis := date_axis(prior_tree, source, table, dialect))
                            and previous_axis[0]['name']==axis[0]['name'] and not previous_axis[1][2]
                            and all(previous_axis[1][:2])):
                        return constraint(table, axis[0], 'previous_result', source_result_ref=prior['result_ref'])
        return None
    if len(published) == 1:
        time_table, column = published[0]
        if _has_date(time_table, column['name']) or column['time_semantics'].get('grain') == 'month':
            return constraint(time_table, column, 'published_time')
    if not table:
        return None
    if (month.get('mode') == 'year_to_date' and tree.find(exp.Count, exp.Sum)
            and (axis := date_axis(tree, source, table, dialect)) and (axis[1][0] or axis[1][1])):
        return constraint(table, axis[0], 'sole_fact_time')
    if not (previous := _previous_answer(engine, ctx, host)):
        return None
    lineage = previous['result_lineage']
    if (previous.get('status') != 'succeeded' or previous.get('truncated')
            or previous.get('row_count') != len(previous.get('rows') or [])
            or not (previous.get('receipt') or {}).get('read_only')
            or previous.get('source_id') != source['id'] or lineage.get('source_id') != source['id']
            or previous.get('business_project') != source.get('business_project')
            or lineage.get('config_fingerprint') != fingerprint(source)
            or (previous.get('validation') or {}).get('state') == 'conflict'):
        return None
    prior_tree = _policy_tree(previous, dialect)
    if _table(prior_tree, source) != table:
        return None
    fields = []
    for column in table.get('columns', []):
        window = _bounds(prior_tree, source, exp.column(column['name']), dialect)
        if window and not window[2] and window[0] and window[1] and window[0] < window[1]:
            fields.append(column)
    if len(fields) == 1:
        return constraint(table, fields[0], 'previous_result', source_result_ref=previous['result_ref'])
    return None


def inspect(tree, source, constraint):
    from .monthly_sql_scope import _bounds, _has_date
    from .request_scope import _and_atoms
    month = constraint['period']
    table = _table(tree, source)
    field = constraint['column']
    if not table or table['name'] != constraint['table']:
        return 'unknown', f'用户要求当前月份 {month}，此查询的业务时间字段未能静态核对。'
    today = date.fromisoformat(constraint['calendar_date'])
    starts = [date.fromisoformat(start) for start in constraint['starts_inclusive']]
    next_month = date.fromisoformat(constraint['end_exclusive'])
    lower, upper, unknown = None, None, True
    if _has_date(table, field):
        lower, upper, unknown = _bounds(tree, source, exp.column(field), sqlglot_name(source.get('dialect')))
    elif constraint.get('grain') == 'month' and (where := tree.args.get('where')):
        relation = next(tree.find_all(exp.Table))
        restrictions = [c for c in where.find_all(exp.Column) if c.name.casefold() == field.casefold()]
        for atom in _and_atoms(where.this):
            if (isinstance(atom, exp.EQ) and len(restrictions) == 1 and atom.this is restrictions[0]
                    and not (atom.this.db or atom.this.catalog)
                    and (not atom.this.table or atom.this.table.casefold() == relation.alias_or_name.casefold())
                    and isinstance(atom.expression, exp.Literal) and atom.expression.is_string
                    and re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', atom.expression.this)):
                lower = date.fromisoformat(atom.expression.this + '-01')
                upper = date(lower.year + (lower.month == 12), lower.month % 12 + 1, 1)
                unknown = False
                break
    month_start = today.replace(day=1)
    if (upper and upper <= month_start) or (lower and lower >= next_month):
        return 'conflict', f'当前月份是 {month}（{constraint["time_zone"]}），SQL 的时间条件会排除本月。请按当前月重新筛选。'
    if unknown:
        return 'unknown', f'用户要求当前月份 {month}，此查询的时间表达式尚无法证明与当前月等价。'
    # An explicit calendar-month window or month-to-date ending on the host day
    # are both usable. A cumulative request may also cover this year's start.
    if lower in starts and upper is not None and today <= upper <= next_month:
        return 'checked', f'核对当前月份 {month}（{constraint["time_zone"]}）的实际时间边界。'
    bounds = '或'.join(constraint['starts_inclusive'])
    return 'conflict', (f'用户要求当前月份 {month}（{constraint["time_zone"]}），'
                        f'请用业务时间字段 {field} 从 {bounds} 起筛选，截止本月末或当前日期。')


def observation(engine, project, result, source, *, load_catalog):
    """Describe a bound run and reuse earlier complete facts without another query."""
    lineage = result.get('result_lineage') or {}
    scope = result.get('request_scope') or {}
    request = scope.get('request') or {}
    publication = result.get('business_project') or {}
    receipt = result.get('receipt') or {}
    ref, source_id = result.get('result_ref'), source.get('id')
    if (not ref or lineage.get('purpose') != 'answer' or not lineage.get('session_id')
            or not request.get('user_ref') or lineage.get('request_ref') != request['user_ref']
            or not publication.get('id') or not publication.get('version') or publication != source.get('business_project')
            or result.get('status')!='succeeded' or result.get('truncated') or result.get('row_count')!=len(result.get('rows') or [])
            or not source_id or any(item.get('source_id')!=source_id for item in (result, lineage, receipt))
            or receipt.get('read_only') is not True or lineage.get('config_fingerprint')!=scope.get('config_fingerprint')
            or (result.get('source_provenance') or {}).get('connection_revision')!=source.get('connection_revision')):
        return None
    checks = result.get('validation') or {}
    month = scope.get('current_month')
    annual = scope.get('monthly_sql_window') or {}
    grouping = (result.get('answer_contract') or {}).get('published_grouping') or {}
    requested_period = grouping.get('requested_period')
    if not month and annual.get('kind')!='annual_aggregate' and not (requested_period or grouping.get('source_result_ref')):
        return None
    if checks.get('state')=='conflict' or any(check.get('state')!='checked' for check in checks.get('checks', [])
                                         if (check.get('constraint') or {}).get('kind')=='published_scope'):
        return None
    dialect = sqlglot_name(source.get('dialect'))
    tree = _policy_tree(result, dialect)
    if not (table := _table(tree, source)):
        return None
    published_column = None
    if month and month.get('basis') == 'published_grouping' and month.get('table') == table['name']:
        published_column = month['column']
    axis = date_axis(tree, source, table, dialect, published_column=published_column)
    if not month and (axis is None or not all(axis[1][:2])):
        from .published_cast_date_axis import date_axis as published_cast_date_axis
        axis = published_cast_date_axis(tree, source, table, dialect)
    if not axis:
        return None
    column, (start, stop, unknown) = axis
    if not start or not stop or unknown or start >= stop:
        return None
    state, kind = 'coverage_unknown', 'annual'
    planning_source, priors = None, None
    if month:
        if not any(check.get('state')=='checked' and (constraint := check.get('constraint') or {}).get('kind')=='current_month'
                   and constraint.get('table')==table['name'] and constraint.get('column')==column['name']
                   for check in checks.get('checks', [])):
            # A previously checked published query may have narrowed the time
            # window even when the general current-month binder lacked a series.
            if (month.get('mode')!='year_to_date' or start.isoformat() not in month['starts_inclusive']
                    or stop.isoformat()!=month['end_exclusive']
                    or not (planning_source := load_catalog())
                    or fingerprint(planning_source)!=lineage['config_fingerprint']):
                return None
            priors = list(_prior_count_results(engine, project, lineage['session_id'], lineage['request_ref'], planning_source))
            if grouping:
                if not _published_grouping_prior(priors, planning_source, tree, table, column, start, stop, result, grouping):
                    return None
            elif not any(check.get('state')=='checked' and (constraint := check.get('constraint') or {}).get('kind')=='raw_dimension_grouping'
                         and constraint.get('table')==table['name'] and constraint.get('time_column')==column['name']
                         for check in checks.get('checks', [])):
                return None
        calendar_month = month['period']
        time_zone = month['time_zone']
        month_start = date.fromisoformat(calendar_month+'-01')
        kind = 'year_to_date' if start.month==1 and (start<month_start or month.get('mode')=='year_to_date') else 'current_month'
    elif requested_period:
        calendar_month = requested_period['calendar_month']
        month_start = date.fromisoformat(calendar_month+'-01')
        if (start.day!=1 or stop.day!=1 or stop>month_start
                or not _matching_published_grouping(source, start, stop, result, grouping)):
            return None
        time_zone = requested_period['time_zone']
        kind = 'fixed_range'
    else:
        annual_request = request['user_ref']
        if annual.get('kind')!='annual_aggregate':
            planning_source = load_catalog()
            if not planning_source or fingerprint(planning_source)!=lineage['config_fingerprint']:
                return None
            priors = list(_prior_count_results(engine, project, lineage['session_id'], lineage['request_ref'], planning_source))
            if not (prior := _published_grouping_prior(priors, planning_source, tree, table, column, start, stop, result, grouping)):
                return None
            annual_request = prior['result_lineage']['request_ref']
            annual = prior['request_scope'].get('monthly_sql_window') or {}
        if (annual.get('kind')!='annual_aggregate' or annual.get('state')!='checked'
                or annual.get('request_ref')!=annual_request or annual.get('source_id')!=source_id
                or annual.get('publication')!={key:publication[key] for key in ('id','version')}
                or annual.get('table')!=table['name'] or annual.get('date_column')!=column['name']
                or annual.get('start_inclusive')!=start.isoformat() or annual.get('end_exclusive')!=stop.isoformat()):
            return None
        time_zone = annual['time_zone']
        calendar_month = temporal_context(time_zone)['current_month'] if annual_request!=request['user_ref'] else annual['calendar_month']
        if annual_request!=request['user_ref'] and calendar_month[:4]!=annual['calendar_month'][:4]:
            return None
        month_start = date.fromisoformat(calendar_month+'-01')
    period_counts = (result.get('answer_contract') or {}).get('period_count_coverage') or {}
    entries = period_counts.get('columns') or []
    if (kind=='current_month' and period_counts.get('result_ref')==ref and period_counts.get('request_ref')==request['user_ref']
            and period_counts.get('source_id')==source_id and period_counts.get('publication')=={key:publication[key] for key in ('id','version')}
            and entries and all(entry.get('period')==calendar_month for entry in entries)):
        states = {entry.get('state') for entry in entries}
        if len(states)==1 and next(iter(states)) in ('unobserved', 'observed_zero', 'observed'):
            state = next(iter(states))
    elif (_count_scope(tree, source, table, column['name']) and result.get('reason_code')=='NO_MATCH'
            and tree.args.get('group') and not result.get('rows')):
        state = 'unobserved'
    proof = {'schema_version':1,'kind':kind,'state':state,'request_ref':request['user_ref'],'result_ref':ref,
             'source_id':source_id,'publication':{key:publication[key] for key in ('id','version')},
             'start_inclusive':start.isoformat(),'end_exclusive':stop.isoformat(),'calendar_month':calendar_month}
    if ((count_scope := _count_scope(tree, source, table, column['name'])) and (planning_source := planning_source or load_catalog())
            and fingerprint(planning_source)==lineage['config_fingerprint']):
        planning_table = _table(tree, planning_source)
        if kind=='year_to_date' and planning_table:
            if priors is None:
                priors = _prior_count_results(engine, project, lineage['session_id'], lineage['request_ref'], planning_source)
            if absence := _prior_current_month_absence(priors, planning_source, tree, planning_table, column, month, stop):
                proof['prior_month_absence'] = absence
        prior_request = {**request, 'client_time_zone':time_zone}
        for summary, prior, _ in _matching_annual_series(engine, project, lineage['session_id'], prior_request,
                                                         planning_source, tree, planning_table, column):
            if summary['query_window'].get('start_inclusive') != month_start.replace(month=1).isoformat():
                continue
            window = summary['query_window']
            first_day, end_day = start.isoformat(), stop.isoformat()
            same_window = window.get('start_inclusive') == first_day and window.get('end_exclusive') == end_day
            whole_month_slice = (kind != 'current_month' and start.day == stop.day == 1
                                 and window['start_inclusive'] <= first_day and end_day <= window['end_exclusive'])
            if kind=='fixed_range' and not whole_month_slice:
                continue
            requested_months = {m for m in summary['requested_months']
                                if not whole_month_slice or first_day <= m+'-01' < end_day}
            months = {item['period']: item['value'] for item in summary['observations']
                      if item['period'] <= calendar_month and (not whole_month_slice or item['period'] in requested_months)}
            if calendar_month in months or kind=='fixed_range':
                values = [row.get(count_scope[1]) for row in result['rows']]
                if (not (same_window or whole_month_slice) or not values
                        or not all(type(value) is int for value in values) or sum(values) != sum(months.values())):
                    if kind=='fixed_range':
                        continue
                    months.pop(calendar_month)
            if not months:
                continue
            prior_observed = {'result_ref':prior['result_ref'],'from':min(months),'to':max(months),'observed_total':sum(months.values()),
                              'unit':summary['unit'],'missing_closed_months':sorted(m for m in requested_months
                                  if m<calendar_month and m not in months)}
            proof['prior_observed'] = prior_observed
            break
    return proof


def observation_note(proof):
    state, kind = proof['state'], proof['kind']
    prior = proof.get('prior_observed')
    note = ''
    if state=='unobserved':
        note = '本次查询范围未返回观测记录，不能据此填零或计算排名。'
    elif state=='observed_zero':
        note = '同范围存在实际观测，对应条件计数确为零。'
    elif kind in ('annual', 'year_to_date'):
        note = '查询日期是筛选范围，实际观测时间以查到的记录为准。'
    if absence := proof.get('prior_month_absence'):
        note += (f'此前同口径查询在 {absence["start_inclusive"]} 至 '
                 f'{absence["end_exclusive"]}（不含）未返回观测，完整累计仍待核实。')
    if prior:
        gaps = ('；其中缺少 '+ '、'.join(prior['missing_closed_months'])+' 的观测') if prior['missing_closed_months'] else ''
        note += (f'此前同一授权来源、同一业务筛选的月度查询在 {prior["from"]} 至 {prior["to"]} 有观测，'
                 f'这些月份合计 {prior["observed_total"]} {prior["unit"]}{gaps}。')
        if kind=='fixed_range':
            return note
        if prior['to'] == proof['calendar_month']:
            note += f'{proof["calendar_month"]} 尚未结月，合计包含该月已查到的记录。'
        else:
            note += f'前次月度查询的观测截止不能作为本轮结果的截止。{proof["calendar_month"]} 尚未结月。'
    return note
