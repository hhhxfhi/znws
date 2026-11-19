"""Bind an explicit correction to the same native request and population.

The model can propose a replacement reference, never a verified state. Scope
identity does not prove business correctness; normal validation still applies.
"""
import copy
import re
import sqlglot
from sqlglot import exp
from .catalog_index import fingerprint
from .vendor.dialect import sqlglot_name


def population(sql, dialect, parameters=None):
    tree=sqlglot.parse_one(sql,read=sqlglot_name(dialect))
    if not isinstance(tree,exp.Select) or any(tree.find_all(exp.Subquery,exp.CTE,exp.Union)):
        return None
    relation = tree.args.get('from')
    if not isinstance(relation, exp.From):
        return None
    # Keep every predicate/join. Changing an aggregate expression or a
    # presentation grouping on the same underlying fields can be a correction;
    # changing a year, region, source or population is a separate comparison.
    aliases={p.alias:p.this for p in tree.expressions if isinstance(p,exp.Alias)}
    fields=set()
    for group in tree.args.get('group').expressions if tree.args.get('group') else []:
        if isinstance(group,exp.Literal) and group.is_int:
            index=int(group.this)-1
            if not 0<=index<len(tree.expressions):return None
            group=tree.expressions[index]
        elif isinstance(group,exp.Column) and not group.table and group.name in aliases:
            group=aliases[group.name]
        fields.update(c.sql() for c in group.find_all(exp.Column))
    sql_part = lambda name: tree.args[name].sql() if tree.args.get(name) else ''
    return fingerprint({'from':relation.sql(),
                        'joins':[j.sql() for j in tree.args.get('joins') or []],
                        'where':sql_part('where'),'having':sql_part('having'),'qualify':sql_part('qualify'),
                        'distinct':sql_part('distinct'),'limit':sql_part('limit'),'offset':sql_part('offset'),
                        'order':sql_part('order') if any(tree.args.get(key) for key in ('limit','offset','distinct')) else '',
                        'grouped':tree.args.get('group') is not None,
                        'group_fields':sorted(fields),'parameters':parameters or []})


def bind(engine,ctx,source,sql,purpose,replaces=None,parameters=None):
    request=(ctx.host_evidence or {}).get('user_ref')
    key=population(sql,source['dialect'],parameters)
    lineage={'schema_version':1,'request_ref':copy.deepcopy(request),'session_id':ctx.session_id,
             'source_id':source['id'],'config_fingerprint':fingerprint(source),
             'population_fingerprint':key,'purpose':purpose,'replaces_result_ref':None,
             'relationship':'independent_result','semantic_verification':'not_implied'}
    if not replaces:return lineage
    previous=engine.get(ctx.project,replaces)
    old=previous.get('result_lineage') or {}
    if purpose!='answer' or old.get('purpose')!='answer' or previous.get('status')!='succeeded':
        raise ValueError('RESULT_REPLACEMENT_SCOPE: 只能修正成功的正式答复结果，探索记录不能提升为答案。')
    if not request or request!=old.get('request_ref') or ctx.session_id!=old.get('session_id'):
        raise ValueError('RESULT_REPLACEMENT_SCOPE: 修正必须属于同一宿主原始请求和会话；其他轮次或旧记录保留为独立结果。')
    if source['id']!=old.get('source_id') or lineage['config_fingerprint']!=old.get('config_fingerprint'):
        raise ValueError('RESULT_REPLACEMENT_SCOPE: 数据源或配置已变化，不能覆盖原结果的适用范围。')
    if not key or key!=old.get('population_fingerprint'):
        raise ValueError('RESULT_REPLACEMENT_SCOPE: 尚不能证明统计总体与分组字段未变；不同期间、地区、过滤及复杂查询保留为独立结果。')
    lineage.update(replaces_result_ref=replaces,relationship='requested_correction',
                   checks={'same_request':'checked','same_source_configuration':'checked','same_population':'checked'})
    return lineage


_MONTH = re.compile(r'[0-9]{4}-(?:0[1-9]|1[0-2])')
_CUMULATIVE = re.compile(r'累计|累积')


def presentation_title(title, result):
    """Align a model title with a proven direct monthly flow measure."""
    contract = result.get('metric_contract') or {}
    if (len(_CUMULATIVE.findall(title)) != 1
            or result.get('status') not in (None, 'succeeded')
            or any(contract.get(k) for k in ('detail', 'ratio', 'top_n', 'cumulative_percent', 'latest_related'))
            or re.search(r'同比|环比|对比|比较', title)
            or _CUMULATIVE.search(contract.get('name') or '')):
        return title
    spec = contract.get('semantic_spec') or {}
    operands = spec.get('operands') or []
    if spec:
        if len(operands) != 1 or spec.get('time_mode') != 'period':
            return title
        operand = operands[0]
        if (not operand.get('time_column') or operand.get('latest')
                or operand.get('aggregation') not in ('count', 'sum')
                or spec.get('formula') != operand.get('id')):
            return title
    elif contract.get('aggregation') != 'period_flow' or contract.get('snapshot_rule') != '期间发生量':
        return title
    requested = {str(period) for period in contract.get('periods') or []}
    rows = result.get('rows') or []
    if (len(requested) < 2 or any(not _MONTH.fullmatch(p) for p in requested)
            or any('cumulative' in row for row in rows)):
        return title
    periods = {str(row.get('period')) for row in rows}
    if len(periods) < 2 or not periods <= requested:
        return title
    replacement = '' if re.search(r'每(?:个)?月|逐月|按月|月度|各月|当月', title) else '逐月'
    return _CUMULATIVE.sub(replacement, title, count=1)
