"""Keep the confirmation boundary on SQL aggregates and their history reads."""
import re
from collections import defaultdict
from functools import lru_cache
from hashlib import sha256

import sqlglot
from sqlglot import exp
from natural_work.store import encode
from .catalog_index import fingerprint
from .catalog_view import entry_code
from .display_units import direct_published_sums, row_count_partition
from .read_limits import execution_basis
from .vendor.dialect import sqlglot_name


_BUDGET = re.compile(r'^(?:项目|工程|合同|年度)?预算(?:总额|总金额|金额|数额)?$')
_ACTUAL = re.compile(r'财政(?:资金)?(?:实际)?(?:拨付(?:金额)?|投入(?:金额)?|支出(?:金额)?)|实际财政(?:拨付|投入|支出)(?:金额)?')
_INVESTMENT_AMOUNT = r'(?:实际)?(?:投入|投资|支出|拨付)(?:总?金额|额|多少)'
_FISCAL_REQUEST = re.compile(r'(?:财政|政府)[^，。；\n]{0,10}(?:拨付|支出|投入|投了|投多少|实际出资)|'
                             + _INVESTMENT_AMOUNT)
_INVESTMENT_TITLE = re.compile(_INVESTMENT_AMOUNT)
_PENDING = '当前SQL汇总未关联已确认的业务指标；计算口径为推定，尚待业务确认。'
_CONFIGURED = '本次查询按已发布的业务口径执行。'
_INFERRED = re.compile(r'推定|(?:尚待|待|未经|尚未)(?:业务|人工)?确认|不可计算')


def _sql_shape(tree):
    """Keep predicates and operators exact; ignore only a scalar count's display label."""
    if (not isinstance(tree, exp.Select) or len(tree.expressions) != 1
            or any(value for key, value in tree.args.items() if key not in ('expressions', 'from', 'where'))
            or not isinstance(count := tree.expressions[0].unalias(), exp.Count)):
        return tree
    alias = tree.expressions[0].alias
    if alias and any(col.name.casefold() == alias.casefold() for col in tree.find_all(exp.Column)):
        return tree
    value = count.this
    if isinstance(value, exp.Literal) and value.is_int and value.this == '1':
        count = count.copy()
        count.set('this', exp.Star())
    return tree.copy().select(count, append=False, copy=False)


@lru_cache(maxsize=512)
def _published_shape(sql, dialect):
    try:
        return _sql_shape(sqlglot.parse_one(sql, read=dialect))
    except sqlglot.errors.SqlglotError:
        return None


def _published_definition(tree, dialect, source, parameters):
    publication = source.get('business_project') or {}
    sid = source.get('id')
    # Currently a published example is validated without bind parameters. Do
    # not assume a parameterized statement is identical to an inline literal.
    if (not publication.get('id') or not publication.get('version') or not sid or parameters
            or tree.find(exp.Placeholder, exp.Parameter) or not tree.find(exp.Table)):
        return None

    def index(rows, title):
        pairs = defaultdict(list)
        for row in rows:
            origin = row.get('origin')
            if row.get('source_id') == sid and origin and row.get('id') and (code := entry_code(row, title)):
                pairs[(origin, code)].append(row)
        return pairs

    terms = index(source.get('terms', []), 'name')
    examples = index(source.get('examples', []), 'question')
    target = _sql_shape(tree)
    matches = []
    for key in terms.keys() & examples.keys():
        if len(terms[key]) != 1 or len(examples[key]) != 1:
            continue
        term, example = terms[key][0], examples[key][0]
        definition = term.get('definition') or ''
        sql = example.get('logical_sql')
        if (not definition or _INFERRED.search(definition) or term.get('pending_clarification') or term.get('clarification')
                or (term.get('config') or {}).get('kind') == 'metric-limitation'
                or any(row.get('status') in ('draft', 'review', 'pending_review') for row in (term, example))
                or not sql or example.get('parameters') or target != _published_shape(sql, dialect)):
            continue
        matches.append((term, example))
        if len(matches) > 1:
            return None
    if not matches:
        return None
    term, example = matches[0]
    return {'state': 'configured', 'basis': 'published_configuration', 'match': 'validated_sql',
            'source_id': sid, 'project_id': publication['id'], 'project_version': publication['version'],
            'term_id': term['id'], 'example_id': example['id'], 'origin': example['origin'],
            'definition_fingerprint': fingerprint([term, example]), 'numerical_acceptance': 'not_evaluated'}


def _with_definition(contract, evidence):
    return {**contract, 'business_confirmation': 'configured', 'definition_evidence': evidence,
            'limitations': [_CONFIGURED, *(note for note in contract.get('limitations', []) if note != _PENDING)]}


def _scalar_sum_count(tree):
    """Describe two scalar projections without inferring what either measures."""
    if (not isinstance(tree, exp.Select) or len(tree.expressions) != 2
            or any(value for key, value in tree.args.items()
                   if key not in ('expressions', 'from', 'where', 'joins', 'with'))):
        return None
    columns = {}
    for item in tree.expressions:
        if not isinstance(item, exp.Alias) or not item.alias.strip():
            return None
        aggregate = item.this.unnest()
        if isinstance(aggregate, exp.Coalesce):
            if (len(aggregate.expressions) != 1 or not isinstance(aggregate.expressions[0].unnest(), exp.Null)
                    or not isinstance(aggregate.this.unnest(), exp.Sum)):
                return None
            aggregate = aggregate.this.unnest()
        if not isinstance(aggregate, (exp.Sum, exp.Count)) or aggregate.expressions or aggregate.this is None:
            return None
        operand = aggregate.this.unnest()
        direct_column = isinstance(operand, exp.Column) and not operand.is_star
        if isinstance(aggregate, exp.Sum) and direct_column:
            columns['amount_column'] = item.alias
        elif isinstance(aggregate, exp.Count) and (direct_column or isinstance(operand, exp.Star)):
            columns['count_column'] = item.alias
        else:
            return None
    if len(columns) != 2 or len({alias.strip().casefold() for alias in columns.values()}) != 2:
        return None
    return {'schema_version': 1, **columns}


def build(result_ref, sql, dialect, *, complete, source=None, parameters=None, purpose=None,
          validated_definition=None, request_scope=None):
    name = sqlglot_name(dialect)
    tree = sqlglot.parse_one(sql, read=name)
    # A plain detail read has no inferred aggregate definition to disclose.
    # A registered metric receives its own contract and never calls this path.
    if not any(tree.find_all(exp.AggFunc)):
        return None
    limitations = []
    grouped_distinct = any(select.args.get('group') and any(
        count.find_ancestor(exp.Select) is select and isinstance(count.this, exp.Distinct)
        for count in select.find_all(exp.Count)) for select in tree.find_all(exp.Select))
    if grouped_distinct:
        limitations.append('按各组分别去重；跨组是否重叠未经验证，分组合计不等于跨组去重总数。')
    measurements = {}
    if source and source.get('business_dictionary'):
        for column, evidence in direct_published_sums(tree, source, dialect).items():
            label = evidence['label']
            if _BUDGET.fullmatch(label):
                role = 'budget'
            elif _ACTUAL.fullmatch(label):
                role = 'actual_fiscal'
            else:
                continue
            measurements[column] = {'label': label, 'unit': evidence['unit'], 'role': role,
                                    'basis': 'published_field_sum'}
    contract = {'schema_version': 1, 'kind': 'sql_aggregate', 'result_ref': result_ref,
            'business_confirmation': 'pending', 'complete': complete,
            'calculation_basis': {'kind':'executed_query', 'registration_required':False},
            **({'grouped_distinct': True} if grouped_distinct else {}),
            **({'measurements': measurements} if measurements else {}),
            'limitations': limitations, 'statements': []}
    if complete and purpose == 'answer' and source:
        if request_scope and not parameters:
            from .cancellation_scope import object_statement
            if cancellation := object_statement(request_scope, source, tree):
                contract['cancellation_object'] = cancellation
                contract['limitations'].append(cancellation['statement'])
        if scalar := _scalar_sum_count(tree):
            contract['scalar_sum_count'] = scalar
        if not parameters:
            if partition := row_count_partition(tree, dialect):
                contract['row_count_partition'] = {**partition, 'result_ref': result_ref}
            from .grouped_measure_profile import grouped_measure_profile
            if profile := grouped_measure_profile(tree):
                contract['grouped_measure_profile'] = profile
        definition = validated_definition or {}
        publication = source.get('business_project') or {}
        if (definition.get('match') in ('validated_current_execution_sql', 'validated_report_budget_sql',
                                        'validated_report_spending_sql')
                and definition.get('basis') == 'published_configuration' and definition.get('state') == 'configured'
                and definition.get('source_id') == source['id'] and definition.get('project_id') == publication.get('id')
                and definition.get('project_version') == publication.get('version') and not parameters):
            from .fiscal_history import _query_fingerprint

            if definition.get('query_fingerprint') == _query_fingerprint(tree, name):
                if definition['match'] in ('validated_report_budget_sql', 'validated_report_spending_sql'):
                    request = (request_scope or {}).get('request') or {}
                    if not request.get('user_ref') or request['user_ref'] != definition.get('request_ref'):
                        return contract
                if definition['match'] == 'validated_report_budget_sql':
                    contract['measurements'] = {column:{**entry, 'basis':'published_field_sum'}
                                                for column, entry in definition['measurements'].items()}
                if definition.get('period_kind') == 'current_project_spending':
                    contract['measurements'] = {definition['columns']['amount']: {
                        'label': '累计已支出金额', 'unit': definition['unit'],
                        'role': 'reported_expenditure', 'basis': 'published_project_reports'}}
                return _with_definition(contract, definition)
        if evidence := _published_definition(tree, name, source, parameters):
            return _with_definition(contract, evidence)
        if request_scope and not parameters and publication.get('id') and publication.get('version'):
            from .row_count_aggregation import monthly_definition
            if term := monthly_definition(source, tree, request_scope):
                return _with_definition(contract, {
                    'state': 'configured', 'basis': 'published_configuration', 'match': 'validated_monthly_count',
                    'source_id': source['id'], 'project_id': publication['id'], 'project_version': publication['version'],
                    'definition_fingerprint': fingerprint(term), 'term_id': term.get('id'),
                    'numerical_acceptance': 'not_evaluated',
                })
    return contract


def configured_grouping(contract, grouping, source, lineage):
    publication = source.get('business_project') or {}
    if (not grouping or not contract or contract.get('complete') is not True
            or lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
            or grouping.get('request_ref') != lineage['request_ref']
            or grouping.get('source_id') != source['id'] or lineage.get('source_id') != source['id']
            or not publication.get('id') or not publication.get('version')
            or any(grouping.get('publication', {}).get(key) != publication[key] for key in ('id', 'version'))):
        return contract
    return _with_definition(contract, {
        'state': 'configured', 'basis': 'published_configuration', 'match': 'validated_grouped_count',
        'source_id': source['id'], 'project_id': publication['id'], 'project_version': publication['version'],
        'definition_fingerprint': fingerprint({'configuration': lineage.get('config_fingerprint'),
                                               'population': lineage.get('population_fingerprint')}),
        'numerical_acceptance': 'not_evaluated',
    })


def _single_city_holding_definition(result, source, tree):
    from .holding_rate_scope import KIND, inspect, result_evidence

    lineage = result['result_lineage']
    scope = result.get('request_scope') or {}
    request = scope.get('request') or {}
    if (not lineage.get('session_id') or request.get('user_ref') != lineage['request_ref']
            or (result.get('validation') or {}).get('state') == 'conflict'):
        return None
    proof = result_evidence(result)
    if not proof or proof['mode'] != 'registered_single_city' or proof['comparison_eligible'] is not False:
        return None
    constraints = [constraint for constraint in scope.get('constraints', [])
                   if constraint.get('kind') == KIND and constraint.get('source_id') == source['id']
                   and constraint.get('evidence') == request]
    if len(constraints) != 1:
        return None
    check = inspect(tree, source, constraints[0])
    if not check or check[0] != 'checked' or not check[2]:
        return None
    definition = check[2]
    if any(proof.get(key) != expected for key, expected in definition.items()):
        return None
    return {'state': 'configured', 'basis': 'published_configuration', 'match': 'validated_sql',
            'source_id': source['id'], 'project_id': definition['project_id'],
            'project_version': definition['project_version'],
            'definition_fingerprint': fingerprint([definition, tree.sql()]),
            'numerical_acceptance': 'not_evaluated'}


def restore(result, source, plan, policy_sql):
    """Recover a proven display status without rewriting the saved receipt."""
    contract = result.get('sql_answer_contract') or {}
    lineage = result.get('result_lineage') or {}
    receipt = result.get('receipt') or {}
    project = result.get('business_project') or {}
    current = source.get('business_project') or {}
    query = (plan.get('payload') or {}).get('query') or {}
    sid = source.get('id')
    if (contract.get('kind') != 'sql_aggregate' or contract.get('business_confirmation') != 'pending'
            or contract.get('result_ref') != result.get('result_ref') or contract.get('complete') is not True
            or result.get('status') != 'succeeded' or result.get('truncated') or result.get('period_count_execution_basis')
            or not project.get('id') or not project.get('version')
            or any(project.get(key) != current.get(key) for key in ('id', 'version'))
            or (result.get('source_provenance') or {}).get('connection_revision') != source.get('connection_revision')
            or not sid or any(key != sid for key in (result.get('source_id'), lineage.get('source_id'),
                                                      receipt.get('source_id'), query.get('datasource_ref')))
            or not receipt.get('query_hash') or receipt.get('read_only') is not True
            or lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
            or not lineage.get('config_fingerprint')
            or plan.get('kind') != 'data.plan' or plan.get('producer') != 'data.validate@1.0.0'
            or plan.get('ref') != 'plan:' + result['result_ref'] or query.get('logical_sql') != policy_sql):
        return None
    if plan.get('digest') != sha256(encode(plan['payload']).encode()).hexdigest():
        return None
    actual = result.get('sql')
    if actual != policy_sql and (not actual or result.get('execution_basis') != execution_basis(policy_sql, actual)):
        return None
    dialect = sqlglot_name(source['dialect'])
    tree = sqlglot.parse_one(policy_sql, read=dialect)
    if evidence := _published_definition(tree, dialect, source, query.get('parameters')):
        return _with_definition(contract, evidence)
    if not query.get('parameters') and not tree.find(exp.Placeholder, exp.Parameter):
        if evidence := _single_city_holding_definition(result, source, tree):
            return _with_definition(contract, evidence)
    return None


def budget_title(title, contract):
    measures = list(((contract or {}).get('measurements') or {}).values())
    if len(measures) != 1 or measures[0]['role'] != 'budget':
        return title
    if _ACTUAL.search(title):
        corrected = _ACTUAL.sub(measures[0]['label'], title)
    elif '预算' not in title:
        corrected = _INVESTMENT_TITLE.sub(measures[0]['label'], title)
    else:
        return title
    return re.sub(r'[（(]预算(?:总额|金额)?[）)]', '', corrected)


def fiscal_budget_guidance(question, contract):
    measures = ((contract or {}).get('measurements') or {}).values()
    roles = {measure.get('role') for measure in measures}
    if roles != {'budget'} or not _FISCAL_REQUEST.search(question or '') or '财政预算' in question:
        return None
    return ('本次金额属于已发布字段的项目预算。只作为同范围预算参考，标题及正文使用“项目预算”；'
            '用户问实际财政拨付、财政投入或支出时，说明现有回执无法核实该金额，另需财政来源与实际发生数据。')
