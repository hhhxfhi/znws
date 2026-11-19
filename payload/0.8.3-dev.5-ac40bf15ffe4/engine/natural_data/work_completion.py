"""Keep annual inventory evidence separate from work-volume measurements."""
from collections import defaultdict
import re
import unicodedata

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from .business_ambiguity import _column_name, _is_row_count
from .business_dictionary import declared_field_unit
from .catalog_view import entry_code
from .display_units import _PRESERVES_UNIT, _zero_default
from .fiscal_history import _read_sql, _registered_execution
from .published_scope import _conjuncts
from .read_limits import bounded_query, execution_basis
from .snapshot_scope import _physical_table
from .vendor.dialect import sqlglot_name

_WORK_MEASURE = re.compile(r'(?:验收|核验|交付)(?:实际)?完成(?:工作)?(?:量|率|度|百分比)|'
                           r'(?:实际|计划)(?:完成|验收|交付)(?:的)?(?:工作)?量|工作量完成(?:率|度|百分比)')
_FUND_MEASURE = re.compile(r'(?:资金|预算|经费)(?:的)?(?:执行|使用|支出|消耗|完成)(?:率|度|比例|百分比)')
_CURRENCY = re.compile(r'(?:人民币)?(?:万|亿)?元|CNY|RMB|USD|EUR', re.I)
_ANNUAL = re.compile(r'(?:请(?:帮我)?(?:查询|统计|计算|核对)?|帮我(?:查询|统计|计算)?|查询|统计|计算)?'
                     r'\s*((?:19|20)\d{2})\s*年(?:度)?\s*([\u4e00-\u9fff]{2,36}?)(?:的)?'
                     r'(?:平均|各(?:个|项)?|每(?:个|项)?|整体)?\s*(?=' + _WORK_MEASURE.pattern + ')')
_COUNT_TERM = re.compile(r'(?:本年度|本年|年度|上年度|上年)?'
                          r'([\u4e00-\u9fff]{2,32}?)(?:数量|总数|数)(?:[_ —-]*(?:同比|环比))?')
_AGENCY = re.compile(r'承担(?:（报送）)?单位|实施单位|负责单位|建设单位|责任单位|承办单位|执行单位')
_UNPUBLISHED = {'draft', 'review', 'pending_review'}
_SELECT = {'expressions', 'from', 'from_', 'where', 'order'}
_UNRELATED_REQUEST = re.compile(r'另(?:外|请|再查|查询)|同时(?:查询|统计|汇总|给出)|顺便')
_MEASURE_DECORATION = re.compile(r'^(?:请)?(?:列出|筛选|筛出|给出|展示)?\s*(?:其中|哪些|有多少)?'
                                 r'\s*(?:未达到|低于|少于|不足|高于|超过|大于|不低于|达到|不超过|[<>≤≥])'
                                 r'\s*\d+(?:\.\d+)?\s*%')


def _annual_request(question):
    text = unicodedata.normalize('NFKC', question).strip()
    hit = _ANNUAL.match(text)
    if not hit or len(set(re.findall(r'((?:19|20)\d{2})\s*年', text))) != 1:
        return None
    suffix = text[hit.end():]
    if _FUND_MEASURE.search(text) or re.search(r'截至|截止|[0-9]{1,2}\s*月|第[一二三四1-4]季度|季度|今年|去年|前年|'
                 r'仅(?:统计|计算|看)|只(?:统计|计算|看)|排除|剔除|不(?:含|包括|统计)|其中的|按(?:市|区|县|单位|地区)', suffix):
        return None
    # This plan supplements one work-measure request. Keep independently
    # requested results on the normal agent path.
    if _UNRELATED_REQUEST.search(suffix) or any(
            not (_WORK_MEASURE.search(clause) or _MEASURE_DECORATION.match(clause))
            for part in re.split(r'[。？！?!；;\n]', suffix) if (clause := part.strip())):
        return None
    return hit[1], hit[2]


def _selected_rows(select, source, dialect):
    if (not isinstance(select, exp.Select)
            or any(value for key, value in select.args.items() if key not in _SELECT)):
        return None
    origin = select.args.get('from') or select.args.get('from_')
    relation = origin.this if origin else None
    if (not (table := _physical_table(relation, source, dialect))
            or relation.alias_column_names or any(relation.args.get(key) for key in ('sample', 'version'))):
        return None
    where = select.args.get('where')
    predicates = _conjuncts(where.this) if where else []
    equalities, groups = [], []
    for predicate in predicates:
        column = _column_name(predicate.this, relation)
        if not column:
            return None
        if (isinstance(predicate, exp.EQ) and isinstance(literal := predicate.expression, exp.Literal)
                and re.fullmatch(r'(?:19|20)\d{2}', literal.this)):
            equalities.append((column, literal.this, literal.is_string))
        elif (isinstance(predicate, exp.In) and not predicate.args.get('query') and predicate.expressions
              and all(isinstance(item, exp.Literal) and item.is_string for item in predicate.expressions)):
            groups.append((column, frozenset(item.this for item in predicate.expressions)))
        else:
            return None
    if len(equalities) == 1 and len(groups) <= 1:
        return table, relation, equalities[0], groups[0] if groups else None
    return None


def _annual_populations(source, dialect, year, subject):
    """Pair a published count with its exact annual branch, including a legacy baseline."""
    sid = source['id']
    examples = defaultdict(list)
    for example in source.get('examples', []):
        if (example.get('source_id') == sid and example.get('origin') and example.get('logical_sql')
                and example.get('status') not in _UNPUBLISHED and not example.get('parameters')):
            examples[example['origin'], entry_code(example, 'question')].append(example)
    populations = set()
    for term in source.get('terms', []):
        label = re.sub(r'^[A-Za-z0-9_-]+\s+', '', term.get('name') or '')
        definition = term.get('definition') or ''
        code = entry_code(term, 'name')
        if (term.get('source_id') != sid or not code or term.get('status') in _UNPUBLISHED
                or term.get('pending_clarification') or term.get('clarification')
                or not (count := _COUNT_TERM.fullmatch(label)) or count[1] != subject
                or re.search(r'不可计算|无数据支撑|推定|待确认', definition)):
            continue
        paired = examples.get((term.get('origin'), code), [])
        if len(paired) != 1:
            continue
        try:
            tree = _read_sql(paired[0]['logical_sql'], dialect)
        except SqlglotError:
            continue
        # Only direct COUNT(*) and COUNT(1) branches describe row counts.
        for select in tree.find_all(exp.Select):
            if len(select.expressions) != 1 or not _is_row_count(select.expressions[0]):
                continue
            shape = _selected_rows(select, source, dialect)
            if not shape or shape[2][1] != year:
                continue
            table, _, period, organization = shape
            if organization and not _AGENCY.search(definition):
                continue
            populations.add((table, period, organization))
    return populations


def inventory_query(source, host):
    """Offer a read-only inventory recipe, never a substitute work measure."""
    request = _annual_request(host.get('user_text') or '')
    publication = source.get('business_project') or {}
    revision = source.get('connection_revision', 0)
    if (not request or not host.get('user_ref') or not publication.get('id') or not publication.get('version')
            or host.get('clarifications') or host.get('source_selection') or source.get('dialect') not in (
                'postgres', 'postgresql', 'sqlite')):
        return None
    dialect = sqlglot_name(source['dialect'])
    definitions = _annual_populations(source, dialect, *request)
    if len(definitions) != 1:
        return None
    table, period, organization = definitions.pop()
    year, subject = request
    field, _, text_year = period
    q = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    literal = lambda value: exp.Literal.string(value).sql(dialect=dialect)
    column = q(field)
    period_value = literal(year) if text_year else year
    columns = [f'{column} AS {q("年度")}']
    where = [f'{column} = {period_value}']
    if organization:
        group, values = organization
        columns.append(q(group) + ' AS ' + q('单位'))
        where.append(q(group) + ' IN (' + ', '.join(map(literal, sorted(values))) + ')')
    sql = 'SELECT ' + ', '.join(columns) + ' FROM ' + '.'.join(map(q, table.split('.'))) + ' WHERE ' + ' AND '.join(where)
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source['id'],
            'publication': {key: publication[key] for key in ('id', 'version')}, 'connection_revision': revision,
            'query_request': {'source_id': source['id'], 'purpose': 'answer', 'sql': sql,
                              'title': f'{year}年{subject}清单', 'chart_type': 'table'}}


def inventory_observation(result, source):
    """Bind a complete read to the single configured annual population."""
    request = _annual_request(result.get('question') or '')
    lineage = result.get('result_lineage') or {}
    receipt = result.get('receipt') or {}
    publication = source.get('business_project') or {}
    rows = result.get('rows') or []
    if (not request or lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
            or not lineage.get('session_id') or not lineage.get('config_fingerprint')
            or not lineage.get('population_fingerprint') or not publication.get('id') or not publication.get('version')
            or publication != result.get('business_project')
            or source.get('connection_revision', 0) != (result.get('source_provenance') or {}).get('connection_revision', 0)
            or not receipt.get('query_hash') or receipt.get('read_only') is not True
            or lineage.get('source_id') != source['id'] or receipt.get('source_id') != source['id']
            or result.get('truncated') or (result.get('validation') or {}).get('state') == 'conflict'
            or not 0 < len(rows) <= 80 or len(rows) != result.get('row_count')):
        return None
    dialect = sqlglot_name(source['dialect'])
    try:
        sql = result['sql']
        if basis := result.get('execution_basis'):
            policy = basis.get('policy_sql')
            if not isinstance(policy, str) or execution_basis(policy, sql) != basis:
                return None
            executed = _read_sql(sql, dialect)
            limit = executed.args.get('limit')
            cap = limit.args.get('expression') if limit else None
            # Only the engine's outer row budget may differ from the proved SQL.
            if not isinstance(cap, exp.Literal) or not cap.is_int or not len(rows) < int(cap.this) <= 501:
                return None
            if _read_sql(bounded_query(policy, source['dialect'], int(cap.this) - 1, {}), dialect) != executed:
                return None
            sql = policy
        tree = _read_sql(sql, dialect)
    except SqlglotError:
        return None
    if not (shape := _selected_rows(tree, source, dialect)):
        return None
    table, relation, period, actual_org = shape
    if period[1] != request[0] or tree.find(exp.Placeholder, exp.Parameter):
        return None
    columns = result.get('columns') or []
    if len(tree.expressions) != len(columns) or len(set(columns)) != len(columns):
        return None
    projected = defaultdict(list)
    for item, name in zip(tree.expressions, columns):
        if not (column := _column_name(item.unalias().unnest(), relation)):
            return None
        projected[column].append(name)
    definitions = _annual_populations(source, dialect, *request)
    if len(definitions) != 1:
        return None
    expected_table, expected_period, expected_org = definitions.pop()
    if table != expected_table or period != expected_period or (actual_org is not None and actual_org != expected_org):
        return None
    organizations = set()
    if expected_org:
        matches = projected.get(expected_org[0], [])
        if len(matches) != 1:
            return None
        names = [row.get(matches[0]) for row in rows]
        if not all(isinstance(name, str) for name in names):
            return None
        organizations = set(names)
        if not organizations <= expected_org[1]:
            return None
    subject = request[1]
    return {'schema_version': 1, 'kind': 'published_annual_population', 'result_ref': result['result_ref'],
            'source_id': source['id'], 'request_ref': lineage['request_ref'], 'session_id': lineage['session_id'],
            'year': request[0], 'subject': subject, 'row_count': len(rows),
            **({'organization_count': len(organizations)} if organizations else {})}


def _amount_input(node):
    """Unwrap functions that do not change a projected amount into another measure."""
    if isinstance(node, _PRESERVES_UNIT) or _zero_default(node):
        return node.this
    if isinstance(node, exp.Mul):
        if isinstance(node.this, exp.Literal) and node.this.is_number:
            return node.expression
        if isinstance(node.expression, exp.Literal) and node.expression.is_number:
            return node.this
    if isinstance(node, (exp.Cast, exp.TryCast)) and node.to.this in exp.DataType.NUMERIC_TYPES:
        return node.this
    if isinstance(node, exp.Nullif) and isinstance(node.expression, exp.Literal):
        return node.this
    if isinstance(node, exp.Trim) and (node.expression is None or isinstance(node.expression, exp.Literal)):
        return node.this
    if isinstance(node, exp.RegexpReplace):
        pattern, replacement = node.expression, node.args.get('replacement')
        flags = node.args.get('modifiers')
        if (isinstance(pattern, exp.Literal) and pattern.is_string
                and pattern.this.replace('\\', '') in ('[^0-9.-]', '[^0-9-.]')
                and isinstance(replacement, exp.Literal) and replacement.is_string and replacement.this == ''
                and (flags is None or isinstance(flags, exp.Literal) and flags.this == 'g')):
            return node.this
    return None


def _amount_origins(node, scope, source, dialect, depth=0):
    if depth >= 32:
        return None
    while (inner := _amount_input(node)) is not None:
        node = inner
    if isinstance(node, (exp.Add, exp.Sub)):
        left = _amount_origins(node.this, scope, source, dialect, depth+1)
        right = _amount_origins(node.expression, scope, source, dialect, depth+1)
        return left | right if left and right else None
    if not isinstance(node, exp.Column) or node.is_star or node.db or node.catalog:
        return None
    inputs = scope.selected_sources
    selected = inputs.get(node.table) if node.table else next(iter(inputs.values()), None) if len(inputs) == 1 else None
    if not selected:
        return None
    _, relation = selected
    if isinstance(relation, Scope) and isinstance(relation.expression, exp.Select):
        if any(item.is_star for item in relation.expression.expressions):
            return None
        expressions = relation.expression.expressions
        names = relation.outer_columns or [item.alias_or_name for item in expressions]
        matches = [item for name, item in zip(names, expressions) if name == node.name]
        return _amount_origins(matches[0].unalias(), relation, source, dialect, depth+1) if len(matches) == 1 else None
    if table := _physical_table(relation, source, dialect):
        return {(table, node.name)}
    return None


def require_work_measure(sql, source, question, *, title=''):
    """Reject proven money substitutions while leaving normal SQL recovery available."""
    question = unicodedata.normalize('NFKC', question)
    if not _WORK_MEASURE.search(question) or '/' not in sql:
        return
    dialect = sqlglot_name(source['dialect'])
    try:
        tree = _read_sql(sql, dialect)
        scopes = traverse_scope(tree)
    except SqlglotError:
        return
    if not scopes or not isinstance(tree, exp.Select):
        return
    monetary = {(item['table'], item['column']) for item in source.get('business_dictionary', [])
                if item.get('source_id') == source['id'] and (unit := declared_field_unit(item)) and _CURRENCY.fullmatch(unit[0])}
    execution = _registered_execution(source, None)
    if execution:
        monetary.update((branch.table, name) for branch in execution[:2] for name in branch.fields)
    if not monetary:
        return
    root = scopes[-1]
    separate_funding = _FUND_MEASURE.search(question)
    for projection in tree.expressions:
        label = projection.alias
        if separate_funding and (_FUND_MEASURE.search(label) or not (_WORK_MEASURE.search(label) or _WORK_MEASURE.search(title))):
            continue
        for division in projection.find_all(exp.Div):
            if division.find_ancestor(exp.Select) is not tree:
                continue
            if all((origins := _amount_origins(operand, root, source, dialect)) and origins <= monetary
                   for operand in (division.this, division.expression)):
                raise ValueError('WORK_COMPLETION_BASIS_MISMATCH: 这个比例使用的是支出、预算等金额字段，'
                                 '表示资金使用程度。验收或交付的完成量需要同一对象已核实的工作量及计划工作量。'
                                 '继续查询对应工作量，或先给出本轮已查到的事实。')
