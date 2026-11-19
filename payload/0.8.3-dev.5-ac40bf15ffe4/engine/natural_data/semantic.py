"""Published, declarative metrics. Models select contracts; they cannot author them."""
from __future__ import annotations
from .vendor.dialect import sqlglot_name
import ast
import copy
import json
import re
from collections import deque
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
class LatestOrder(Strict):
    column:str
    direction:Literal['asc','desc']='desc'
class LatestRelated(Strict):
    """Select a report per entity before applying value/status filters."""
    entity_table:str
    entity_key:list[str]=Field(min_length=1,max_length=10)
    report_key:list[str]=Field(min_length=1,max_length=10)
    entity_filters:list[dict]=Field(default_factory=list,max_length=30)
    entity_year_column:str|None=None
    date_format:Literal['date','year_month','yyyymm','datetime','timestamp']
    # Explicit fixed business offset for period boundaries. datetime is local
    # wall time; timestamp carries Z or a numeric offset in every row.
    timezone_offset:str|None=None
    date_only:Literal['unknown','start_of_day']='unknown'
    window:Literal['history','year_to_date','period']
    tie_breakers:list[LatestOrder]=Field(default_factory=list,max_length=5)
    cutoff_dates:dict[str,str]=Field(default_factory=dict,max_length=60)
    cutoff_inclusive:bool=True
    missing:Literal['unknown','zero','exclude']='unknown'
    null_value:Literal['unknown','zero']='unknown'
    blank_defaults:dict[str,str]=Field(default_factory=dict,max_length=10)
    allowed_values:dict[str,list[str]]=Field(default_factory=dict,max_length=10)

class Operand(Strict):
    id:str=Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
    table:str
    column:str|None=None
    aggregation:Literal['sum','count','distinct','avg','min','max','snapshot_sum','single_value']='sum'
    time_column:str|None=None
    entity_columns:list[str]=Field(default_factory=list,max_length=10)
    filters:list[dict]=Field(default_factory=list,max_length=30)
    # 0.6.4: indicator tables keep the period as 202601 / 2026 (integer or text) and
    # the value as text. Both are optional; omitted means the old behaviour.
    time_format:Literal['date','yyyymm','yyyy']|None=None
    numeric_text:bool=False
    latest:LatestRelated|None=None
class Dimension(Strict):
    id:str=Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
    table:str
    column:str
class Relation(Strict):
    from_table:str
    from_columns:list[str]=Field(min_length=1,max_length=10)
    to_table:str
    to_columns:list[str]=Field(min_length=1,max_length=10)
    cardinality:Literal['many_to_one','one_to_one']
class Metric(Strict):
    kind:Literal['semantic-metric']='semantic-metric'
    id:str=Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,63}$')
    version:str=Field(pattern=r'^\d+\.\d+\.\d+$')
    source_id:str
    unit:str=Field(min_length=1,max_length=30)
    definition:str=Field(min_length=1,max_length=3000)
    operands:list[Operand]=Field(min_length=1,max_length=10)
    formula:str=Field(min_length=1,max_length=500)
    dimensions:list[Dimension]=Field(default_factory=list,max_length=12)
    relations:list[Relation]=Field(default_factory=list,max_length=30)
    coverage:list[str]=Field(default_factory=list,max_length=300)
    additive:bool=False
    reconciliation:bool=False
    source_revision:int|None=Field(default=None,ge=0)
    time_mode:Literal['period','all']='period'
    # 0.7.0: dimensions whose items must not be added together (overlapping categories,
    # different units). A plan without them is rejected instead of returning a wrong total.
    group_required:list[str]=Field(default_factory=list,max_length=6)
class Filter(Strict):
    dimension:str
    op:Literal['eq','ne','gt','gte','lt','lte','in']
    values:list[str]=Field(min_length=1,max_length=100)
class SemanticPlan(Strict):
    catalog_ref:str
    source_id:str
    metric_id:str
    metric_version:str
    periods:list[str]=Field(default_factory=list,max_length=60)
    group_by:list[str]=Field(default_factory=list,max_length=6)
    filters:list[Filter]=Field(default_factory=list,max_length=30)
    title:str=Field(min_length=1,max_length=160)
    question:str=Field(min_length=1,max_length=10000)
    chart_type:str|None=None
    task_id:str
    task_revision:int=Field(ge=1)

def q(name):
    if not isinstance(name,str) or not name or len(name)>200:raise ValueError('SEMANTIC_IDENTIFIER: 字段无效')
    return '"'+name.replace('"','""')+'"'
def table(name):return '.'.join(q(part) for part in name.split('.'))
def columns(source):return {t['name']:{c['name'] for c in t['columns']} for t in source['tables']}
def formula_sql(formula,ids,zero_fill=frozenset()):
    try:node=ast.parse(formula,mode='eval').body
    except SyntaxError:raise ValueError('SEMANTIC_FORMULA: 无效公式') from None
    def walk(n):
        if isinstance(n,ast.Name) and n.id in ids:
            ref=q('__m_'+n.id)
            # A count/sum operand with no row for this group and period counted
            # nothing, so it is 0. A row whose value is NULL stays NULL (unknown).
            if n.id in zero_fill:return '(CASE WHEN '+ref+'.'+q('period')+' IS NULL THEN 0 ELSE '+ref+'.value END)'
            return ref+'.value'
        if isinstance(n,ast.Constant) and type(n.value) in (int,float) and abs(n.value)<=1e12:return str(n.value)
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):return ('-' if isinstance(n.op,ast.USub) else '+')+'('+walk(n.operand)+')'
        if isinstance(n,ast.BinOp) and isinstance(n.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            a,b=walk(n.left),walk(n.right)
            if isinstance(n.op,ast.Div):return '('+a+' * 1.0 / NULLIF('+b+',0))'
            return '('+a+{ast.Add:'+',ast.Sub:'-',ast.Mult:'*'}[type(n.op)]+b+')'
        raise ValueError('SEMANTIC_FORMULA: 仅允许登记的操作数及四则运算')
    return walk(node)
def period_bounds(p,fmt,textual):
    """Half-open [start, end) bounds of a YYYY or YYYY-MM period for the time field's format."""
    y=int(p[:4]);month=int(p[5:]) if len(p)==7 else None
    if fmt in ('yyyymm','yyyy'):
        if fmt=='yyyy':
            if month:raise ValueError('SEMANTIC_TIME: 该指标的时间字段只有年度，不能按月统计；periods 请填年份。')
            start,end=y,y+1
        elif month:start,end=y*100+month,((y+1)*100+1 if month==12 else y*100+month+1)
        else:start,end=y*100+1,(y+1)*100+1
        return (str(start),str(end)) if textual else (start,end)
    nxt=str(y+1) if not month else (str(y+1)+'-01' if month==12 else p[:5]+f'{month+1:02d}')
    if textual:return p,nxt
    return (p+('-01-01' if not month else '-01'),nxt+('-01-01' if not month else '-01'))

def validate(metric,source):
    m=Metric.model_validate(metric);cs=columns(source)
    if any(o.aggregation == 'single_value' for o in m.operands):
        if len(m.operands) != 1 or m.formula.strip() != m.operands[0].id or m.additive:
            raise ValueError('SEMANTIC_SINGLE_VALUE: 已统计值只读取一个操作数原值，不支持直接合并、公式再计算或标记可加。')
    if m.time_mode=='period' and not m.coverage:raise ValueError('SEMANTIC_COVERAGE: 请填写已确认可查询的期间。')
    if m.time_mode=='all' and m.coverage:raise ValueError('SEMANTIC_COVERAGE: 全量口径不填写期间。')
    if (m.source_revision or 0)!=source.get('target_revision',0):raise ValueError('SEMANTIC_TARGET_CHANGED: 数据库目标已变化，请重新核对指标字段和口径并发布。')
    if m.source_id!=source['id']:raise ValueError('SEMANTIC_SOURCE: 口径必须归属该来源')
    if len({x.id for x in m.operands})!=len(m.operands) or len({d.id for d in m.dimensions})!=len(m.dimensions):raise ValueError('SEMANTIC_DUPLICATE: 标识重复')
    def field(t,c):
        if t not in cs or c not in cs[t]:raise ValueError('SEMANTIC_SCHEMA: 授权目录中不存在表或字段 '+t+'.'+str(c))
    for o in m.operands:
        if o.table not in cs:raise ValueError('SEMANTIC_SCHEMA: 未授权表')
        if o.aggregation!='count' and not o.column:raise ValueError('SEMANTIC_SCHEMA: 必须指定数值字段')
        if o.column:field(o.table,o.column)
        if o.time_column:field(o.table,o.time_column)
        if m.time_mode=='period' and not o.time_column:raise ValueError('SEMANTIC_TIME: 期间指标必须指定时间字段；无期间指标可选择全量口径。')
        if m.time_mode=='all' and o.aggregation=='snapshot_sum':raise ValueError('SEMANTIC_TIME: 快照指标必须指定期间。')
        if o.time_format=='yyyy' and any(len(p)==7 for p in m.coverage):raise ValueError('SEMANTIC_TIME: 年度时间字段的指标只能登记年份期间。')
        if o.numeric_text and o.aggregation in ('count','distinct'):raise ValueError('SEMANTIC_SCHEMA: 计数不需要“数值存为文本”。')
        for c in o.entity_columns:field(o.table,c)
        if o.aggregation=='snapshot_sum' and not o.entity_columns:raise ValueError('SEMANTIC_GRAIN: 快照必须指定实体粒度')
        if o.aggregation=='snapshot_sum':
            t=next(t for t in source['tables'] if t['name']==o.table)
            keys=t.get('unique_keys',[])+([t['primary_key']] if t.get('primary_key') else [])
            if not any(set(k)<=set(o.entity_columns+[o.time_column]) for k in keys):raise ValueError('SEMANTIC_GRAIN: 快照实体与时间必须具有唯一键，避免任意选择同一时点的重复记录')
        for f in o.filters:
            if set(f)!={'column','op','values'} or f['op'] not in ('eq','ne','gt','gte','lt','lte','in') or not isinstance(f['values'],list) or not f['values'] or len(f['values'])>100 or any(type(v) not in (str,int,float,bool) for v in f['values']):raise ValueError('SEMANTIC_FILTER: 无效筛选合约')
            field(o.table,f['column'])
        if o.latest:
            from .latest_report import validate_latest
            validate_latest(o, m, source, field)
    for d in m.dimensions:field(d.table,d.column)
    for r in m.relations:
        if len(r.from_columns)!=len(r.to_columns):raise ValueError('SEMANTIC_RELATION: 复合关联键长度不等')
        for c in r.from_columns:field(r.from_table,c)
        for c in r.to_columns:field(r.to_table,c)
        # A unique target key is a database guarantee, not a model assertion.
        t=next(t for t in source['tables'] if t['name']==r.to_table)
        keys=t.get('unique_keys',[])+([t['primary_key']] if t.get('primary_key') else [])
        if not any(set(k)==set(r.to_columns) for k in keys):raise ValueError('SEMANTIC_FANOUT: 关联目标必须有登记的主键或唯一键')
        if r.cardinality=='one_to_one':
            t=next(t for t in source['tables'] if t['name']==r.from_table)
            keys=t.get('unique_keys',[])+([t['primary_key']] if t.get('primary_key') else [])
            if not any(set(k)==set(r.from_columns) for k in keys):raise ValueError('SEMANTIC_FANOUT: 一对一来源必须有唯一键')
    formula_sql(m.formula,{o.id for o in m.operands})
    if any(g not in {d.id for d in m.dimensions} for g in m.group_required):raise ValueError('SEMANTIC_DIMENSION: 必须分组的维度不在指标维度中')
    for p in m.coverage:
        if not re.fullmatch(r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?',p):raise ValueError('SEMANTIC_COVERAGE: 覆盖范围使用YYYY或YYYY-MM')
    if m.reconciliation:
        from .inspection_reconciliation import _ratio_operand_pair
        if not _ratio_operand_pair(m.model_dump()) or any(o.aggregation not in ('distinct','count','sum') for o in m.operands):
            raise ValueError('SEMANTIC_RECONCILIATION: 对账需要两个计数或合计操作数，公式为 a / b 或 a / b * 100。')
    return m

def path(start,end,relations):
    if start==end:return []
    queue=deque([(start,[],{start})]);found=[];depth=None
    while queue:
        at,steps,seen=queue.popleft()
        if depth is not None and len(steps)>=depth:continue
        for r in relations:
            edge=None
            if r.from_table==at:edge=(r.to_table,r.from_columns,r.to_columns)
            elif r.to_table==at and r.cardinality=='one_to_one':edge=(r.from_table,r.to_columns,r.from_columns)
            if edge is None or edge[0] in seen:continue
            chain=steps+[(at,*edge)]
            if edge[0]==end:depth=len(chain);found.append(chain)
            else:queue.append((edge[0],chain,seen|{edge[0]}))
    shortest=[x for x in found if len(x)==depth]
    if not shortest:raise ValueError('SEMANTIC_PATH: 无无扩增关联路径；多对多及一对多下钻必须另建聚合口径')
    if len(shortest)!=1:raise ValueError('SEMANTIC_AMBIGUOUS_PATH: 存在多个同长度关联路径，请在设置中消除歧义')
    return shortest[0]

def definitions(source):
    from .onboarding import metric_catalog
    return [Metric.model_validate(spec) for spec in metric_catalog(source)[0]]

def compile_plan(args,source):
    m=next((x for x in definitions(source) if x.id==args.metric_id and x.version==args.metric_version),None)
    if not m:raise ValueError('SEMANTIC_METRIC: 指标尚未发布或已失效，请查看目录unavailable_metrics，并在设置 → 智能问数 → 业务口径中修复、校验发布。')
    dims={d.id:d for d in m.dimensions}
    if len(set(args.periods))!=len(args.periods) or any(p not in m.coverage for p in args.periods):raise ValueError('OUT_OF_COVERAGE: 未登记覆盖期间，不将缺失视为零')
    if m.time_mode=='period' and not args.periods:raise ValueError('SEMANTIC_TIME: 请提供已登记的统计期间。')
    if m.time_mode=='all' and args.periods:raise ValueError('SEMANTIC_TIME: 全量口径不接受期间，需按期间统计时另配置时间字段。')
    # Every time-based metric already returns one row per requested period.
    # An explicit built-in period grouping is equivalent, never a new business
    # dimension. Keep duplicate/unknown dimensions and all filters strict.
    if len(set(args.group_by))==len(args.group_by) and m.time_mode=='period' and 'period' not in dims and 'period' in args.group_by:
        args=args.model_copy(update={'group_by':[d for d in args.group_by if d!='period']})
    if len(set(args.group_by))!=len(args.group_by) or any(d not in dims for d in args.group_by) or any(f.dimension not in dims for f in args.filters):raise ValueError('SEMANTIC_DIMENSION: 维度无效或重复')
    missing=[g for g in m.group_required if g not in args.group_by]
    if missing:raise ValueError('SEMANTIC_GROUP_REQUIRED: 这个指标的各分项不能相加，请在 group_by 里加上 '+'、'.join(missing)+'（可再用 filters 只取需要的分项）。')
    params=[];param_index={}
    def bind(v):
        # Repeated config values (blank defaults, quality messages, shared
        # filters) reference one named parameter instead of spending the V3
        # parameter budget once per occurrence.
        key=(type(v).__name__,v)
        if key in param_index:return ':'+param_index[key]
        name='s'+str(len(params));param_index[key]=name
        params.append({'name':name,'type':'string' if isinstance(v,str) else 'number','value':v});return ':'+name
    def literal(v):
        # Period labels and their bounds derive from registered coverage
        # (YYYY / YYYY-MM) through fixed arithmetic, never from free user text.
        # Rendering them as SQL literals keeps long month lists inside the V3
        # parameter contract instead of weakening that contract.
        # Accepted shapes: YYYY / YYYY-MM / YYYY-MM-01 (date bounds), 6-digit
        # YYYYMM (textual yyyymm bounds and the tianditu year-end cutoff) and
        # 8-digit YYYYMMDD. All are digits/hyphens or the constant 全部 — no SQL
        # metacharacters — so inlining is injection-safe; free text still raises.
        if isinstance(v,int):return str(v)
        if isinstance(v,str) and re.fullmatch(r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?(?:-01)?|20\d{4}(?:\d{2})?|全部',v):return "'"+v+"'"
        raise ValueError('SEMANTIC_LITERAL: 期间字面量必须是登记的YYYY/YYYY-MM期间或其固定边界')
    def predicate(col,op,values):
        if op!='in' and len(values)!=1:raise ValueError('SEMANTIC_FILTER: 单值运算需要一个值')
        if op=='in':return col+' IN ('+','.join(bind(v) for v in values)+')'
        return col+{'eq':'=','ne':'<>','gt':'>','gte':'>=','lt':'<','lte':'<='}[op]+bind(values[0])
    from . import pack_quality
    guarded = pack_quality.enabled(source, m)
    quality_messages = {}
    single_value_operands = set()
    latest_checks = []
    latest_cache = {}
    diagnostics = {}
    ctes=[];keys=['period',*args.group_by]
    for operand in m.operands:
        period_queries=[]
        for p in (args.periods if m.time_mode=='period' else ['全部']):
            if operand.latest:
                from .latest_report import compile_period, QUALITY_MESSAGES
                period_queries.append(compile_period(operand, m, args, p, source, bind, predicate, ctes, latest_cache, literal))
                if p == args.periods[0]:
                    latest_checks.extend((q('__m_'+operand.id)+'.'+q('__latest_'+key)+'=1', message) for key,message in QUALITY_MESSAGES.items())
                continue
            aliases={operand.table:'b'};joins=[]
            for dim in [dims[x] for x in dict.fromkeys(args.group_by+[f.dimension for f in args.filters])]:
                for start,target,left,right in path(operand.table,dim.table,m.relations):
                    if target in aliases:continue
                    alias='j'+str(len(aliases));aliases[target]=alias
                    joins.append(' LEFT JOIN '+table(target)+' '+alias+' ON '+' AND '.join(aliases[start]+'.'+q(a)+'='+alias+'.'+q(b) for a,b in zip(left,right)))
            col=lambda t,c:aliases[t]+'.'+q(c)
            conditions=[predicate(col(operand.table,f['column']),f['op'],f['values']) for f in operand.filters]
            conditions += [predicate(col(dims[f.dimension].table,dims[f.dimension].column),f.op,f.values) for f in args.filters]
            datecol=col(operand.table,operand.time_column) if operand.time_column else None
            # Text time fields such as 'YYYY-MM' compare as strings: '2025-03' is
            # not >= '2025-03-01'. Bound text fields by the period prefix instead.
            ttype=next((str(c.get('type') or '') for t in source['tables'] if t['name']==operand.table for c in t['columns'] if c['name']==operand.time_column),'')
            textual=bool(re.search(r'char|text|string|clob',ttype,re.I))
            start,end=period_bounds(p,operand.time_format,textual) if p!='全部' else (None,None)
            value_col=lambda c:('CAST(NULLIF(TRIM('+c+'),'+"''"+') AS NUMERIC)' if operand.numeric_text else c)
            if operand.aggregation=='snapshot_sum':
                # Snapshot selects each entity at the latest observation before the period end.
                cutoff=end
                conditions.append(datecol+' < '+literal(cutoff))
                partition=','.join(q(c) for c in operand.entity_columns)
                base='(SELECT *, ROW_NUMBER() OVER (PARTITION BY '+partition+' ORDER BY '+q(operand.time_column)+' DESC) AS "__snapshot_rank" FROM '+table(operand.table)+' WHERE '+q(operand.time_column)+' < '+literal(cutoff)+') b'
                conditions.append('b."__snapshot_rank"=1')
                agg='SUM('+value_col(col(operand.table,operand.column))+')'
            else:
                if m.time_mode=='period':
                    if operand.time_format == 'yyyy':
                        # A year code is exact, not a text prefix: '202501' must
                        # never masquerade as the annual record '2025'.
                        conditions.append(datecol+' = '+literal(start))
                    else:
                        conditions.append(datecol+' >= '+literal(start))
                        conditions.append(datecol+' < '+literal(end))
                base=table(operand.table)+' b'
                raw=value_col(col(operand.table,operand.column)) if operand.column else '*'
                if operand.aggregation == 'single_value':
                    # MAX is only a SQL carrier for a sole physical row. Multiple
                    # rows are suppressed in this same query, even if equal. Keep
                    # text unchanged until Python checks it: SQLite CAST('bad')=0.
                    agg='MAX('+col(operand.table,operand.column)+')'
                else:
                    agg='COUNT(DISTINCT '+raw+')' if operand.aggregation=='distinct' else operand.aggregation.upper()+'('+raw+')'
            diagnostic = ''
            if operand.aggregation == 'single_value':
                diagnostic += ',CASE WHEN COUNT(*)=1 THEN 0 ELSE 1 END AS "__single_invalid"'
                single_value_operands.add(operand.id)
            if guarded:
                components = pack_quality.inspection_components(m, operand, bind)
                diagnostics.update({key: operand.id for key in components})
                diagnostic += ''.join(','+value+' AS '+q('__'+key) for key,value in components.items())
                check = pack_quality.operand_check(m, operand, p, source, bind, literal)
                if check:
                    failure, message, cutoff = check
                    quality_messages[operand.id] = message
                    diagnostic += ',CASE WHEN '+failure+' THEN 1 ELSE 0 END AS "__invalid"'
                    if cutoff: conditions.append(cutoff)
            groups=[col(dims[d].table,dims[d].column) for d in args.group_by]
            projection=[literal(p)+' AS period']+[c+' AS '+q(d) for c,d in zip(groups,args.group_by)]
            period_queries.append('SELECT '+','.join(projection)+','+agg+' AS value'+diagnostic+' FROM '+base+''.join(joins)+(' WHERE '+' AND '.join(conditions) if conditions else '')+(' GROUP BY '+','.join(groups) if groups else ''))
        ctes.append(q('__m_'+operand.id)+' AS ('+' UNION ALL '.join(period_queries)+')')
    keysql=','.join(q(k) for k in keys)
    ctes.append('keys AS ('+' UNION '.join('SELECT '+keysql+' FROM '+q('__m_'+o.id) for o in m.operands)+')')
    join=''.join(' LEFT JOIN '+q('__m_'+o.id)+' ON '+' AND '.join('(keys.'+q(k)+'='+q('__m_'+o.id)+'.'+q(k)+' OR (keys.'+q(k)+' IS NULL AND '+q('__m_'+o.id)+'.'+q(k)+' IS NULL))' for k in keys) for o in m.operands)
    # 0.6.2: multi-operand metrics also return each operand (internal "__op_" columns,
    # removed by the engine) so an empty value can say which operand is missing or zero.
    operand_cols=''.join(','+q('__m_'+o.id)+'.value AS '+q('__op_'+o.id) for o in m.operands) if len(m.operands)>1 else ''
    operand_cols += ''.join(','+q('__m_'+op)+'.'+q('__'+key)+' AS '+q('__op_'+key) for key,op in diagnostics.items())
    value_sql = formula_sql(m.formula,{o.id for o in m.operands},frozenset(o.id for o in m.operands if o.aggregation in ('sum','count','distinct')) if len(m.operands)>1 else frozenset())
    checks = [(q('__m_'+op)+'."__invalid"=1', msg) for op,msg in quality_messages.items()]
    checks += [(q('__m_'+op)+'."__single_invalid"=1', '已统计值在该期间及筛选范围内必须恰好一条；当前缺失或存在重复记录，请核对版本/地区/期间，不能自动相加或取最大值。') for op in sorted(single_value_operands)]
    checks += latest_checks
    if guarded and quality_messages and not args.group_by and not args.filters:
        conflict = pack_quality.reference_check(m,source,bind,value_sql)
        if conflict: checks.append((conflict, '指标值表与明细的抽查单位数或合格口径不一致，请先复核两套数。'))
    result = 'SELECT '+','.join('keys.'+q(k) for k in keys)+operand_cols+','+value_sql+' AS value'
    if checks:
        result += ',CASE '+ ' '.join('WHEN '+condition+' THEN '+bind(msg) for condition,msg in checks)+' ELSE NULL END AS "__quality_reason"'
    result += ' FROM keys'+join
    if checks:
        ctes.append('checked AS ('+result+')')
        visible = [*keys,*(['__op_'+o.id for o in m.operands] if operand_cols else []),*['__op_'+key for key in diagnostics],'__quality_reason']
        result = 'SELECT '+','.join(q(k) for k in visible)+',CASE WHEN "__quality_reason" IS NULL THEN value ELSE NULL END AS value FROM checked'
    sql = 'WITH '+','.join(ctes)+' '+result+' ORDER BY '+','.join(('' if checks else 'keys.')+q(k) for k in keys)
    import sqlglot
    sql=sqlglot.parse_one(sql,read='postgres').sql(dialect=sqlglot_name(source['dialect']))
    if latest_checks and len(sql)>100000:
        raise ValueError('SEMANTIC_QUERY_SIZE: 最新报送查询的期间或操作数过多，请分批查询，不能删减业务筛选来绕过长度限制。')
    if len(params)>100:
        # The V3 execution contract caps bound parameters at 100. Periods no
        # longer consume that budget; only caliber filters/checks do. Refuse
        # with the real reason instead of a generic contract mismatch.
        raise ValueError('SEMANTIC_QUERY_PARAMS: 绑定参数超过执行契约上限100（当前'+str(len(params))+'个），来自口径中的筛选或核对配置。请分批查询，或请管理员精简口径筛选；不能放宽执行契约。')
    ratio=any(isinstance(n,ast.BinOp) and isinstance(n.op,ast.Div) and any(isinstance(x,ast.Name) for x in ast.walk(n.right)) for n in ast.walk(ast.parse(m.formula,mode='eval')))
    # 0.6.3: the metric's own filters (configured in the caliber) are stated in the
    # receipt, so the answer can say which records were counted.
    labels={(t['name'],c['name']):c.get('label') or c['name'] for t in source['tables'] for c in t['columns']}
    ops={'eq':'等于','in':'属于','ne':'不等于','gt':'大于','gte':'不小于','lt':'小于','lte':'不大于'}
    default_filters=[f"{labels.get((o.table,f['column']),f['column'])} {ops.get(f['op'],f['op'])} {'、'.join(map(str,f.get('values') or []))}".strip() for o in m.operands for f in o.filters]
    default_filters += [f"实体.{labels.get((o.latest.entity_table,f['column']),f['column'])} {ops.get(f['op'],f['op'])} {'、'.join(map(str,f.get('values') or []))}".strip() for o in m.operands if o.latest for f in o.latest.entity_filters]
    contract={'id':m.id,'version':m.version,**({'default_filters':list(dict.fromkeys(default_filters))} if default_filters else {}),'periods':args.periods,'formula':m.formula,'unit':m.unit,'definition':m.definition,'additive':m.additive and not ratio,'additive_over_time':m.additive and not ratio and all(o.aggregation in ('sum','count') for o in m.operands),'ratio':ratio,'scope':{'periods':args.periods,'dimensions':args.group_by,'filters':[f.model_dump() for f in args.filters]},'limitations':['无法确认的数值保留为空，不按零处理。']+(['比例不能直接相加。'] if ratio else [])+(['库存按期末值统计，不按月累加。'] if any(o.aggregation=='snapshot_sum' for o in m.operands) else [])+(['平均数和去重后的数量不能直接跨期相加。'] if any(o.aggregation in ('avg','distinct') for o in m.operands) else []),'semantic_spec':m.model_dump()}
    if any(o.aggregation == 'single_value' for o in m.operands):
        contract['limitations'].append('读取来源已统计值；同一期间与分组必须唯一，不自动合并重复记录，也不推定来源计算公式或跨期可加性。')
    if any(o.latest for o in m.operands):
        contract['additive_over_time'] = False
        contract['limitations'].append('先按实体取截止时点的最新报送，再筛选状态并统计；期末值不能跨期相加。重复实体、并列最新记录、无效日期或金额保留未知。')
        contract['latest_related'] = [{'operand':o.id, **o.latest.model_dump(), 'report_table':o.table, 'time_column':o.time_column, 'report_filters':o.filters} for o in m.operands if o.latest]
        if any(o.latest and o.latest.date_format in ('datetime','timestamp') for o in m.operands):
            contract['limitations'].append('时间排序保留微秒，期间边界使用配置的固定时区；不推断夏令时。纯日期只有明确配置 start_of_day 时才解释为该时区当日零点，否则保持未知。')
    names = [term.get('name') for term in source.get('terms', []) if (term.get('config') or {}).get('id') == m.id and term.get('name')]
    if len(set(names)) == 1:
        contract['name'] = names[0]
    from .definition_evidence import definition_evidence
    contract['confirmation'] = definition_evidence(source, contract, next((t['config'] for t in source.get('terms', []) if (t.get('config') or {}).get('id') == m.id and (t.get('config') or {}).get('version') == m.version), m.model_dump()))
    contract['business_confirmation'] = contract['confirmation']['state']
    name = contract.get('name', m.id)
    period_title = ('、'.join(args.periods) + ' ' + name if len(args.periods) in (1, 2)
                    else name + f'（{len(args.periods)} 期）' if args.periods else name)
    return {'source_id':source['id'],'sql':sql,'parameters':params,'title':period_title+('（合成测试）' if source.get('synthetic') else ''),'question':args.question,'chart_type':args.chart_type,'catalog_ref':args.catalog_ref,'metric_contract':contract,'publish_facts':True},contract


class CoverageError(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.coverage_evidence = evidence


def probe_uncovered_periods(engine, source, metric_spec, periods):
    """Read-only row-count probe for uncovered periods on a metric's operand tables.
    Never modifies metric coverage or treats missing coverage as a published zero."""
    try:
        clean_spec = {k: v for k, v in (metric_spec or {}).items() if k in Metric.model_fields}
        m = Metric.model_validate(clean_spec)
    except Exception:
        return None
    if m.time_mode != 'period':
        return None
    if any(o.latest for o in m.operands):
        # A physical report count is not a count of latest, scoped entities.
        return None
    covered = set(m.coverage or [])
    uncovered = [p for p in dict.fromkeys(periods or []) if p not in covered and re.fullmatch(r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?', str(p))]
    if not uncovered:
        return None
    import sqlite3
    from pathlib import Path
    import sqlglot
    from sqlalchemy import text
    dialect = sqlglot_name(source.get('dialect', 'sqlite'))
    counts = {}
    try:
        # A calendar-year request must receive evidence for all twelve months.
        # Keep larger probes bounded, and explicitly name anything not checked.
        for p in uncovered[:24]:
            total_rows = 0
            probed_any = False
            for operand in m.operands:
                if not operand.time_column:
                    continue
                ttype = next((str(c.get('type') or '') for t in source.get('tables', []) if t['name'] == operand.table for c in t.get('columns', []) if c['name'] == operand.time_column), '')
                textual = bool(re.search(r'char|text|string|clob', ttype, re.I))
                try:
                    start, end = period_bounds(p, operand.time_format, textual)
                except ValueError:
                    continue
                params = {'s0': start, 's1': end}
                conds = [f'b.{q(operand.time_column)} = :s0'] if operand.time_format == 'yyyy' else [f'b.{q(operand.time_column)} >= :s0', f'b.{q(operand.time_column)} < :s1']
                for idx, f in enumerate(operand.filters):
                    col = f"b.{q(f['column'])}"
                    op = f['op']
                    vals = f.get('values') or []
                    if op == 'in':
                        placeholders = []
                        for v_idx, v in enumerate(vals):
                            k = f'f_{idx}_{v_idx}'
                            params[k] = v
                            placeholders.append(':' + k)
                        conds.append(f"{col} IN ({','.join(placeholders)})")
                    elif len(vals) == 1:
                        k = f'f_{idx}'
                        params[k] = vals[0]
                        op_sql = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}[op]
                        conds.append(f'{col}{op_sql}:{k}')
                raw_sql = f"SELECT COUNT(*) AS cnt FROM {table(operand.table)} b WHERE {' AND '.join(conds)}"
                query = sqlglot.parse_one(raw_sql, read='postgres').sql(dialect=dialect)
                if source.get('dialect') == 'sqlite' and source.get('path'):
                    db_path = Path(source['path']).resolve()
                    if not db_path.exists():
                        continue
                    conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
                    try:
                        conn.execute('PRAGMA query_only=ON')
                        row = conn.execute(query, params).fetchone()
                        total_rows += int(row[0] or 0) if row else 0
                        probed_any = True
                    finally:
                        conn.close()
                elif engine is not None and source.get('credential_ref'):
                    from .connections import read_connection, connection_url
                    url = connection_url(engine, source)
                    with read_connection(source, url) as conn:
                        row = conn.execute(text(query), params).fetchone()
                        total_rows += int(row[0] or 0) if row else 0
                        probed_any = True
            if probed_any:
                counts[p] = total_rows
    except Exception:
        return None
    if not counts:
        return None
    parts = []
    for p, cnt in counts.items():
        if cnt == 0:
            parts.append(f'期间 {p} 在授权底表中物理记录数为 0 条（underlying_row_count=0）')
        else:
            parts.append(f'期间 {p} 在授权底表中存在 {cnt} 条物理记录（underlying_row_count={cnt}）')
    unprobed = [p for p in uncovered if p not in counts]
    summary = '底表实查核实：' + '；'.join(parts) + '。已核实期间未登记进指标 coverage，按口径边界不将未覆盖期间直接计为已发布指标值（也不视为指标值 0），无需再用 SQL 重复探查已核实期间的底表。'
    if unprobed:
        summary += '以下期间尚未实查，不能外推为零记录：' + '、'.join(unprobed) + '。'
    return {
        'schema_version': 1,
        'source_id': source['id'],
        'source_name': source.get('name', source['id']),
        'metric_id': m.id,
        'metric_version': m.version,
        'metric_name': metric_spec.get('name', m.id),
        'requested_periods': list(periods),
        'uncovered_periods': list(counts.keys()),
        'unprobed_periods': unprobed,
        'probe_complete': not unprobed,
        'period_row_counts': counts,
        'all_zero': all(v == 0 for v in counts.values()),
        'summary': summary,
    }

