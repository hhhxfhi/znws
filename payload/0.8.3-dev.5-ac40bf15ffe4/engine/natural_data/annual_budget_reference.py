"""A separate budget reference for an explicitly ordered annual amount request."""
import re
import unicodedata


_PREFERRED = re.compile(
    r'(?:请(?:帮我)?\s*)?(?:优先|先)\s*(?:查询|查|核实|统计|看)\s*'
    r'(?P<first>(?:19|20)\d{2})\s*年(?:度)?\s*(?P<subject>[\u4e00-\u9fff]{2,32}?)(?:的)?'
    r'(?:实际)?(?:投入|投资|支出|拨付)(?:总?金额|额)\s*[,;。]?\s*'
    r'(?:如果|若|如)\s*(?P=first)\s*年(?:度)?\s*(?:没有数据|无数据|查不到)\s*[,;]?\s*'
    r'(?:则|就|再|改为|改)\s*(?:查询|查|核实|统计|看)\s*(?P<second>(?:19|20)\d{2})\s*年(?:度)?'
    r'(?:\s*[,;]?\s*(?:并|且|同时)?(?:注明|说明)(?:最终)?(?:使用|采用|取数|查询)的?年份)?\s*[。!?]?$'
)

_COMPARE = re.compile(
    r'(?:(?:刚才|前一题|上一(?:题|问|轮))(?:的)?不(?:查|看)了\s*[,，]\s*)?'
    r'(?:请(?:帮我)?\s*)?(?:(?:重新|改为)\s*)?(?:查询|查|看|核实|统计)?\s*'
    r'(?P<first>(?:19|20)\d{2})\s*年(?:度)?\s*(?P<subject>[\u4e00-\u9fff]{2,32}?)(?:的)?'
    r'(?P<measure>(?:实际)?(?:投入|投资|支出|拨付)(?:总?金额|额))\s*(?:比|较(?:于)?|相比(?:于)?|对比)\s*'
    r'(?:(?P<second>(?:19|20)\d{2})\s*年(?:度)?|去年|上(?:一)?年(?:度)?)'
    r'\s*(?:高还是低|高或低|高了还是低了|高多少|低多少|相差多少|差多少|变化多少)?'
    r'\s*(?:[,，]\s*)?(?:变化幅度(?:有多大|是多少|多少))?'
)
_CHANGE_DETAIL = re.compile(r'(?:变化幅度|增减幅度)(?:有多大|是多少|多少)')
_INDEPENDENT_QUANTITY = re.compile(r'(?:另查|另问|另看)?(?:19|20)\d{2}\s*年(?:度)?'
                                   r'[\u4e00-\u9fff]{2,32}?(?:率|比例|数量|量|数)(?:是)?多少')
_SCOPE_EDIT = re.compile(r'仅|只|剔除|排除|不含|按|分组|各(?:个|家|地区|单位)')
_PROJECT_SPENDING = re.compile(
    r'^(?:请(?:帮我)?\s*)?(?:查询|统计|计算|看)?\s*(?:本年度?|今年|当年度?)的?'
    r'(?P<subject>[\u4e00-\u9fff]{3,24}?)(?:的)?(?:项目数量|项目数|数量|个数)\s*[,、和及与]\s*'
    r'(?:实际)?(?:投资(?:金额|额)|投入(?:金额|额)|(?:累计)?(?:已支出|已使用)(?:资金|金额))'
    r'(?:分别)?(?:是|有)?多少[。!?]?$'
)


def project_spending_guidance(source, host):
    """Use the published project population and spending operand independently of its budget."""
    from datetime import date, timedelta

    from .fiscal_history import _EXECUTION_RATE, _LEGACY_CURRENCY, _ReportAxis, _latest_execution_sql, _registered_execution
    from .temporal_context import temporal_context

    host = host or {}
    quote = host.get('user_text') or ''
    question = unicodedata.normalize('NFKC', quote).strip()
    publication = source.get('business_project') or {}
    if (not host.get('user_ref') or host.get('clarifications') or host.get('source_selection')
            or not publication.get('id') or not publication.get('version')
            or not (request := _PROJECT_SPENDING.fullmatch(question)) or _SCOPE_EDIT.search(question)):
        return None
    registered = _registered_execution(source, request['subject'] + '资金执行率')
    if not registered:
        return None
    spent, budget, _, subject, _ = registered
    subject = _EXECUTION_RATE.sub('', subject).strip().removesuffix('项目')
    if request['subject'].removesuffix('项目') != subject:
        return None
    units = {match[1] for field in source.get('business_dictionary', [])
             if (field.get('source_id'), field.get('kind'), field.get('table'), field.get('column'))
             == (source['id'], 'field', spent.table, spent.fields[0])
             if (match := _LEGACY_CURRENCY.search(field.get('description') or ''))}
    if len(units) != 1:
        return None
    unit = next(iter(units)).removeprefix('人民币')
    clock = temporal_context(host.get('client_time_zone') or 'UTC')
    if clock['zone_fallback']:
        return None
    today = date.fromisoformat(clock['current_date'])
    start, end = today.replace(month=1, day=1).isoformat(), (today + timedelta(days=1)).isoformat()
    axis = _ReportAxis(spent.table, spent.key, spent.time, spent.fields[0], budget.fields[0], start, end)
    inner = _latest_execution_sql(source, registered, axis, overall_only=True, spending_only=True,
                                  include_partial=True, collapse_agreeing_spend=True)
    if not inner:
        return None
    columns = {'projects': '项目数量（个）', 'covered': '有支出报送项目数（个）',
               'amount': f'累计已支出金额（{unit}）', 'latest': '最新报送日期'}
    sql = (f'SELECT COALESCE("项目数量", 0) AS "{columns["projects"]}", '
           f'COALESCE("可核算项目数", 0) AS "{columns["covered"]}", '
           f'COALESCE("已使用资金", "可核算项目已使用资金") AS "{columns["amount"]}", '
           '"最新报送日期" FROM (' + inner + ') AS report')
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': quote, 'source_id': source['id'],
            'publication': {key: publication[key] for key in ('id', 'version')},
            'connection_revision': source.get('connection_revision', 0),
            'query_request': {'source_id': source['id'], 'purpose': 'answer', 'chart_type': 'table', 'sql': sql,
                              'title': f'{today.year}年度{subject}项目数量与累计已支出金额'},
            'columns': columns, 'unit': unit, 'as_of_date': today.isoformat(), 'period_kind': 'current_project_spending'}


def comparison_report_amounts(source, host):
    """Bind an explicitly anchored closed-year comparison to the published report axis."""
    comparison = _report_comparison(source, host)
    return comparison[0] if comparison else None


def _report_comparison(source, host):
    from datetime import date

    from .fiscal_history import _EXECUTION_RATE, _ReportAxis, _latest_execution_sql, _registered_execution
    from .temporal_context import temporal_context

    host = host or {}
    text = unicodedata.normalize('NFKC', host.get('user_text') or '').strip()
    clauses = [part.strip() for part in re.split(r'[。!?;\n]+', text) if part.strip()]
    if not clauses or host.get('clarifications') or not (match := _COMPARE.fullmatch(clauses[0])):
        return None
    if re.search(r'拨付|财政|拨款', clauses[0]) or not (registered := _registered_execution(source, None)):
        return None
    spent, budget, _, label, _ = registered
    subject = _EXECUTION_RATE.sub('', label).strip().removesuffix('项目')
    if match['subject'].removeprefix('省级').removesuffix('项目') != subject:
        return None
    if any(not _CHANGE_DETAIL.fullmatch(part) and not _INDEPENDENT_QUANTITY.fullmatch(part) for part in clauses[1:]):
        return None
    first = int(match['first'])
    second = int(match['second']) if match['second'] else first - 1
    current = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
    if first == second or max(first, second) >= current.year:
        return None
    unit = None
    for row in source.get('business_dictionary', []):
        if (row.get('kind'), row.get('table'), row.get('column')) != ('field', spent.table, spent.fields[0]):
            continue
        declared = re.search(r'单位为(万元|亿元|元)', row.get('description') or '')
        if declared:
            unit = declared[1]
            break
    suffix = f'（{unit}）' if unit else ''
    money = '累计已支出金额' + suffix
    selects = []
    for year in (second, first):
        start = date(year, 1, 1).isoformat()
        stop = date(year + 1, 1, 1).isoformat()
        axis = _ReportAxis(spent.table, spent.key, spent.time, spent.fields[0], budget.fields[0], start, stop)
        inner = _latest_execution_sql(source, registered, axis, overall_only=True, spending_only=True)
        if not inner:
            return None
        selects.append(f'SELECT {year} AS "年度", "项目数量", "可核算项目数", '
                       f'"已使用资金" AS "{money}" FROM ({inner}) AS period_reports')
    series = '\nUNION ALL\n'.join(selects)
    actual = f'target."{money}"'
    previous = f'base."{money}"'
    sql = f'''WITH reports AS ({series})
SELECT target.*,
 CASE WHEN target."年度"={first} THEN ROUND({actual}-{previous},2) END AS "变动金额{suffix}",
 CASE WHEN target."年度"={first} THEN ROUND(({actual}-{previous})*100.0/NULLIF({previous},0),2)
 END AS "变动幅度（%）"
FROM reports target CROSS JOIN (SELECT "{money}" FROM reports WHERE "年度"={second}) base
ORDER BY target."年度"'''
    query = {'source_id': source['id'], 'purpose': 'answer', 'chart_type': 'table',
             'title': f'{second}年与{first}年项目报送累计已支出金额', 'sql': sql}
    observation = {'periods': [str(second), str(first)], 'unit': unit,
                   'columns': {'period': '年度', 'projects': '项目数量', 'covered': '可核算项目数',
                               'amount': money, 'change': '变动金额' + suffix, 'rate': '变动幅度（%）'},
                   'additional_questions': [clause for clause in clauses[1:]
                                            if _INDEPENDENT_QUANTITY.fullmatch(clause)]}
    return query, observation


def report_spending_evidence(sql, source, host):
    """Certify the configured report computation, without inferring fiscal payment."""
    from sqlglot.errors import SqlglotError

    from .catalog_index import fingerprint
    from .fiscal_history import _query_fingerprint, _read_sql
    from .vendor.dialect import sqlglot_name

    publication = source.get('business_project') or {}
    if ('可核算项目数' not in sql or not host.get('user_ref')
            or not publication.get('id') or not publication.get('version') or host.get('source_selection')):
        return None
    current = project_spending_guidance(source, host)
    if current:
        query = current['query_request']
        observation = {key: current[key] for key in ('columns', 'unit', 'as_of_date', 'period_kind')}
    elif comparison := _report_comparison(source, host):
        query, observation = comparison
    else:
        return None
    dialect = sqlglot_name(source['dialect'])
    try:
        tree = _read_sql(sql, dialect)
        if tree != _read_sql(query['sql'], dialect):
            return None
    except SqlglotError:
        return None
    return {'state': 'configured', 'basis': 'published_configuration',
            'match': 'validated_report_spending_sql', 'source_id': source['id'],
            'project_id': publication['id'], 'project_version': publication['version'],
            'request_ref': host['user_ref'], 'query_fingerprint': _query_fingerprint(tree, dialect),
            'definition_fingerprint': fingerprint({'publication': publication, 'query': query}),
            'role': 'reported_expenditure', **observation}


def preferred_budget_read(source, host, reference):
    """Prove the source, subject and exact preferred-year population before automation."""
    question = unicodedata.normalize('NFKC', (host or {}).get('user_text') or '').strip()
    request = _PREFERRED.fullmatch(question)
    reference = reference or {}
    publication = source.get('business_project') or {}
    revision = source.get('connection_revision', 0)
    if (not request or not host.get('user_ref') or host.get('clarifications') or host.get('source_selection')
            or request['first'] == request['second'] or not publication.get('id') or not publication.get('version')
            or reference.get('basis') != 'published_comparison_baseline' or reference.get('role') != 'budget'
            or reference.get('query_mode') != 'fallback' or reference.get('requested_measure') != 'actual_fiscal'
            or reference.get('requested_years') != [request['first'], request['second']]
            or reference.get('year') != request['first'] or reference.get('source_id') != source.get('id')):
        return None
    year = request['first']
    subject = request['subject']
    canonical = subject.removeprefix('省级').removesuffix('项目')
    if not canonical or reference.get('title') != f'{year}年{canonical}项目预算':
        return None
    if subject.startswith('省级'):
        from .fiscal_report_budget import _publication_bindings
        from .work_completion import _annual_populations
        from .vendor.dialect import sqlglot_name

        definitions = _publication_bindings(source, f'省级{canonical}项目')
        if not definitions:
            return None
        origin = definitions[0][0]['origin']
        comparison = reference.get('read_request') or {}
        from .catalog_view import comparison_budget_baseline, entry_references
        _, identify = entry_references(source)
        binding = comparison_budget_baseline(source, year, lambda value: value == canonical, published_origin=origin)
        if not binding or identify('example', binding[1]).get('read_request') != comparison:
            return None
        populations = _annual_populations(source, sqlglot_name(source['dialect']), year, canonical + '项目')
        if len(populations) != 1:
            return None
        table, (period, _, _), organization = next(iter(populations))
        if not organization or table != reference['table']:
            return None
        field, values = organization
        if reference['filters'] != {period: [year], field: sorted(values)}:
            return None
    column = f'{reference["label"]}（{reference["unit"]}）'
    return {
        'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source['id'],
        'publication': {key: publication[key] for key in ('id', 'version')}, 'connection_revision': revision,
        'year': year, 'fallback_year': request['second'], 'column': column, 'unit': reference['unit'],
        'query_request': {'source_id': source['id'], 'sql': reference['sql'], 'purpose': 'answer',
                          'title': reference['title'], 'chart_type': 'table'},
    }


def _comparison_report_coverage(source, subject, years):
    from sqlglot import exp

    from .fiscal_history import _EXECUTION_RATE, _entity_scope, _registered_execution
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source['dialect'])
    if dialect not in ('postgres', 'sqlite') or not (registered := _registered_execution(source, None)):
        return None
    spent, _, _, label, tree = registered
    if _EXECUTION_RATE.sub('', label).strip().removesuffix('项目') != subject:
        return None
    if not (scope := _entity_scope(tree, source, spent, dialect)):
        return None
    entity, key, group, year_column, _, year_text, groups = scope

    def quote(value):
        return exp.to_identifier(value, quoted=True).sql(dialect=dialect)

    def table(value):
        return '.'.join(map(quote, value.split('.')))

    def literal(value):
        return exp.Literal.string(value).sql(dialect=dialect)

    units = ', '.join(map(literal, groups))
    result_columns = ('项目年度', '有资金报送记录')
    selects = []
    for year in years:
        saved_year = literal(year) if year_text else year
        # Check report existence for those projects at any reporting date.
        # A late or malformed report must leave actual-amount discovery open.
        selects.append(f'''SELECT {literal(year)} AS {quote(result_columns[0])}, CASE WHEN EXISTS (
    SELECT 1 FROM {table(entity)} p JOIN {table(spent.table)} r ON r.{quote(spent.key)} = p.{quote(key)}
    WHERE p.{quote(year_column)} = {saved_year} AND p.{quote(group)} IN ({units})
) THEN 1 ELSE 0 END AS {quote(result_columns[1])}''')
    return {'columns': list(result_columns), 'query_request': {'source_id': source['id'],
            'sql': '\nUNION ALL\n'.join(selects), 'purpose': 'explore', 'title': '核对项目资金报送覆盖',
            'chart_type': 'table'}}


def comparison_budget_read(source, host, reference):
    """Read a registered annual budget only when its annual population matches exactly."""
    from .catalog_view import comparison_budget_baseline
    from .fiscal_history import _read_sql
    from .sql_answer_contract import _FISCAL_REQUEST
    from .vendor.dialect import sqlglot_name
    from .work_completion import _annual_populations, _selected_rows

    question = unicodedata.normalize('NFKC', (host or {}).get('user_text') or '').strip()
    clauses = [part.strip() for part in re.split(r'[。!?;\n]+', question) if part.strip()]
    reference = reference or {}
    publication = source.get('business_project') or {}
    if (not clauses or not (request := _COMPARE.fullmatch(clauses[0])) or not host.get('user_ref')
            or host.get('clarifications') or host.get('source_selection') or not publication.get('id')
            or not publication.get('version') or reference.get('schema_version') != 1
            or reference.get('kind') != 'budget_reference' or reference.get('basis') != 'published_comparison_baseline'
            or reference.get('source_id') != source['id'] or reference.get('requested_measure') != 'actual_fiscal'
            or reference.get('query_mode') not in (None, 'comparison') or reference.get('fallback_references')):
        return None
    other_questions = [clause for clause in clauses[1:] if not _CHANGE_DETAIL.fullmatch(clause)]
    if any(not _INDEPENDENT_QUANTITY.fullmatch(clause) or _FISCAL_REQUEST.search(clause)
           or _SCOPE_EDIT.search(clause) for clause in other_questions):
        return None

    first = request['first']
    second = request['second'] or str(int(first) - 1)
    year = reference.get('year')
    if first == second or year not in (first, second) or reference.get('requested_years', [first, second]) != [first, second]:
        return None
    subject = request['subject'].removesuffix('项目')
    if not (binding := comparison_budget_baseline(source, year, lambda value: value == subject)):
        return None
    _, _, budget = binding
    if any(reference.get(key) != value for key, value in budget.items()):
        return None
    dialect = sqlglot_name(source['dialect'])
    shape = _selected_rows(_read_sql(budget['sql'], dialect), source, dialect)
    if not shape:
        return None
    table, _, period, organization = shape
    if _annual_populations(source, dialect, year, subject + '项目') != {(table, period, organization)}:
        return None

    coverage = _comparison_report_coverage(source, subject, (first, second))
    budget_entry = reference.get('read_request') or {}
    amount_measure = '实际' + request['measure'].removeprefix('实际')
    return {
        'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source['id'],
        'publication': {key: publication[key] for key in ('id', 'version')},
        'connection_revision': source.get('connection_revision', 0),
        'year': year, 'compared_year': second if year == first else first,
        'other_questions': other_questions,
        'amount_lookup': {'question': subject + amount_measure, 'measure': amount_measure},
        **({'budget_entry': budget_entry} if budget_entry.get('source_id') == source['id']
           and budget_entry.get('entry_ref') and budget_entry.get('entry_version') else {}),
        'organization_count': len(organization[1]) if organization else None,
        'column': f'{budget["label"]}（{budget["unit"]}）', 'unit': budget['unit'],
        'query_request': {'source_id': source['id'], 'sql': budget['sql'], 'purpose': 'answer',
                          'title': reference['title'], 'chart_type': 'table'},
        **({'report_coverage': coverage} if coverage else {}),
    }
