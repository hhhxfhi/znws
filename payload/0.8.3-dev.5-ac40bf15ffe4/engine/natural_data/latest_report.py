"""Latest related facts, selected before aggregation, with same-query quality gates.

No business table names or metric codes belong here. The entity population and
report population remain separate; a report's state cannot change which report
is latest. SQL uses portable scalar functions and windows, not DISTINCT ON.
"""
from datetime import date, timedelta
import calendar
import math
import hashlib
import re

QUALITY_MESSAGES = {
    'entity': '统计范围内实体键为空或重复，不能确认一实体一行，请修正实体粒度后重试。',
    'date': '相关报送存在与已配置日期格式或时区规则不符的值，不能确定截止时点的最新记录；未截掉时间、静默忽略无效日期或猜测时区。',
    'tie': '存在并列最新报送，所配置的排序字段仍不能唯一确定一条；请补充业务确认的版本/顺序字段或修正重复记录。',
    'missing': '统计范围内有实体缺少截止时点可用报送；当前缺报策略为未知，不能按零或其他状态处理。',
    'value': '最新报送的金额或筛选字段为空/无效，不能据此给出完整合计；未把无效文本金额转成零。',
}


def timezone_minutes(value):
    match=re.fullmatch(r'([+-])(\d{2}):(\d{2})',value or '')
    if not match or int(match[2])>14 or int(match[3])>59 or int(match[2])==14 and int(match[3])!=0 or value=='-00:00':
        raise ValueError('SEMANTIC_LATEST_TIMEZONE: datetime/timestamp 必须配置明确固定业务时区 timezone_offset（如 +08:00 或 +00:00）；不猜测服务器时区，不支持未知偏移 -00:00 或夏令时地区规则。')
    return (1 if match[1]=='+' else -1)*(int(match[2])*60+int(match[3]))


def validate_latest(o, m, source, field):
    l = o.latest
    if l.date_format in ('datetime','timestamp'):
        timezone_minutes(l.timezone_offset)
        typ=next(str(c.get('type','')) for t in source['tables'] if t['name']==o.table for c in t['columns'] if c['name']==o.time_column)
        if source['dialect']!='sqlite' and not re.search(r'char|text|string|clob',typ,re.I):
            raise ValueError('SEMANTIC_LATEST_TIMEZONE: 当前 datetime/timestamp 合约读取明确 ISO 文本；原生时间类型须通过已审核的视图提供规范文本和时区，不能依赖数据库会话的隐式格式或时区转换。')
    elif l.timezone_offset is not None or l.date_only!='unknown':
        raise ValueError('SEMANTIC_LATEST_TIMEZONE: 纯日期或月份合约不使用 timezone_offset 或 date_only 转换。')
    if m.time_mode != 'period' or o.aggregation not in ('sum', 'count', 'distinct', 'avg', 'min', 'max'):
        raise ValueError('SEMANTIC_LATEST: 最新报送只支持有期间的合计、计数、去重、均值和极值。')
    if o.entity_columns or o.time_format:
        raise ValueError('SEMANTIC_LATEST: 实体键和报送日期格式只在 latest 中配置，不能混用旧快照参数。')
    if l.entity_table == o.table or len(l.entity_key) != len(l.report_key):
        raise ValueError('SEMANTIC_LATEST: 实体表和报送表须不同，复合关联键须一一对应。')
    if len(set(l.entity_key)) != len(l.entity_key) or len(set(l.report_key)) != len(l.report_key):
        raise ValueError('SEMANTIC_LATEST: 关联键不能重复。')
    for c in l.entity_key: field(l.entity_table, c)
    for c in l.report_key: field(o.table, c)
    if any(d.table == o.table for d in m.dimensions):
        raise ValueError('SEMANTIC_LATEST: 最新报送只按实体侧维度分组/筛选；报送状态请配置为操作数筛选，避免过滤掉缺报诊断。')
    if l.entity_year_column: field(l.entity_table, l.entity_year_column)
    for f in l.entity_filters:
        if (set(f) != {'column','op','values'} or f['op'] not in ('eq','ne','gt','gte','lt','lte','in')
                or not isinstance(f['values'], list) or not 1 <= len(f['values']) <= 100
                or any(type(v) not in (str,int,float,bool) or isinstance(v,float) and not math.isfinite(v) for v in f['values'])
                or f['op'] != 'in' and len(f['values']) != 1):
            raise ValueError('SEMANTIC_FILTER: 无效实体筛选合约')
        field(l.entity_table, f['column'])
        if f['column'] == l.entity_year_column:
            raise ValueError('SEMANTIC_LATEST: 动态实体年度不能同时固定筛选。')
    if len({x.column for x in l.tie_breakers}) != len(l.tie_breakers):
        raise ValueError('SEMANTIC_LATEST: 并列排序字段不能重复。')
    for x in l.tie_breakers: field(o.table, x.column)
    for c in l.blank_defaults:
        field(o.table, c)
        if c == o.time_column or c in l.report_key or c == o.column or any(x.column == c for x in l.tie_breakers):
            raise ValueError('SEMANTIC_LATEST: 空值默认值只用于状态筛选，不能改写键、日期、排序或数值字段。')
        if c not in {f['column'] for f in o.filters}:
            raise ValueError('SEMANTIC_LATEST: 空值默认值必须对应已登记的报送筛选。')
    for c,values in l.allowed_values.items():
        field(o.table,c)
        if c not in {f['column'] for f in o.filters} or not 1<=len(values)<=100 or len(set(values))!=len(values):
            raise ValueError('SEMANTIC_LATEST: 状态允许值须对应报送筛选，且为非空、不重复的有限列表。')
        if c in l.blank_defaults and l.blank_defaults[c] not in values:
            raise ValueError('SEMANTIC_LATEST: 空状态默认值不在允许值列表中。')
    for p, value in l.cutoff_dates.items():
        try: d = date.fromisoformat(value)
        except ValueError: raise ValueError('SEMANTIC_LATEST: 截止日期须为有效 YYYY-MM-DD。') from None
        if p not in m.coverage or value != d.isoformat() or not value.startswith(p):
            raise ValueError('SEMANTIC_LATEST: 截止日期必须落在对应已登记期间内。')
        if l.date_format in ('year_month','yyyymm') and (not l.cutoff_inclusive or d.day != calendar.monthrange(d.year,d.month)[1]):
            raise ValueError('SEMANTIC_LATEST: 月份字段只能确认含月末的截止，无法判断月中或不含当日。')
    for t in source['tables']:
        if t['name'] in (o.table, l.entity_table) and any(c['name'].startswith('__nw_l_') for c in t['columns']):
            raise ValueError('SEMANTIC_LATEST: 来源字段与内部最新报送诊断字段重名。')


def _digits(expr):
    cleaned = expr
    for d in '0123456789': cleaned = f"REPLACE({cleaned},'{d}','')"
    return f"(LENGTH({expr})>0 AND COALESCE(LENGTH({cleaned}),0)=0)"


def _date_parts(raw, fmt, native_date=False):
    """Guard casts explicitly; never let SQLite's CAST('bad')=0 pick a record."""
    if native_date:
        return [f'CAST(EXTRACT({part} FROM {raw}) AS INTEGER)' for part in ('YEAR','MONTH','DAY')]
    s = f'TRIM(CAST({raw} AS VARCHAR(256)))'
    y = f'SUBSTRING({s},1,4)'
    if fmt == 'yyyymm':
        mon, day = f'SUBSTRING({s},5,2)', "'1'"
        shape = f'LENGTH({s})=6'
    elif fmt == 'year_month':
        mon, day = f'SUBSTRING({s},6)', "'1'"
        shape = f"LENGTH({s}) BETWEEN 6 AND 7 AND SUBSTRING({s},5,1)='-'"
    else:
        rest = f'SUBSTRING({s},6)'
        pos = f"POSITION('-' IN {rest})"
        # Non-negative lengths also keep invalid values safe on PostgreSQL.
        mon = f'SUBSTRING({rest},1,CASE WHEN {pos}>0 THEN {pos}-1 ELSE 0 END)'
        day = f'SUBSTRING({rest},{pos}+1)'
        shape = f"LENGTH({s}) BETWEEN 8 AND 10 AND SUBSTRING({s},5,1)='-' AND {pos} BETWEEN 2 AND 3 AND LENGTH({day}) BETWEEN 1 AND 2"
    good = shape+' AND '+_digits(y)+' AND '+_digits(mon)+' AND '+_digits(day)
    return [f'CASE WHEN {good} THEN CAST({x} AS INTEGER) ELSE NULL END' for x in (y,mon,day)]


def _timestamp_frame(s, fmt, business_offset):
    """Separate offset parsing from component validation to keep SQL bounded."""
    z=f"SUBSTRING({s},LENGTH({s}),1)='Z'"
    zone=f'SUBSTRING({s},CASE WHEN LENGTH({s})>=6 THEN LENGTH({s})-5 ELSE 1 END,6)'
    hh,mm=f'SUBSTRING({zone},2,2)',f'SUBSTRING({zone},5,2)'
    offset_shape=f"LENGTH({zone})=6 AND SUBSTRING({zone},1,1) IN ('+','-') AND SUBSTRING({zone},4,1)=':' AND {_digits(hh)} AND {_digits(mm)}"
    offset_h=f'CASE WHEN {offset_shape} THEN CAST({hh} AS INTEGER) ELSE NULL END'
    offset_m=f'CASE WHEN {offset_shape} THEN CAST({mm} AS INTEGER) ELSE NULL END'
    offset_valid=f"({offset_h}) BETWEEN 0 AND 14 AND ({offset_m}) BETWEEN 0 AND 59 AND (({offset_h})<14 OR ({offset_m})=0) AND {zone}<>'-00:00'"
    offset=f"CASE WHEN {z} THEN 0 WHEN {offset_valid} THEN (CASE WHEN SUBSTRING({zone},1,1)='-' THEN -1 ELSE 1 END)*(({offset_h})*60+({offset_m})) ELSE NULL END"
    body=f'SUBSTRING({s},1,CASE WHEN LENGTH({s})>=20 THEN LENGTH({s})-(CASE WHEN {z} THEN 1 ELSE 6 END) ELSE 0 END)' if fmt=='timestamp' else s
    return body,offset if fmt=='timestamp' else str(business_offset)


def _timestamp_parts(body, offset):
    """ISO text with microsecond precision; malformed/ambiguous values stay NULL."""
    fractional=f'SUBSTRING({body},21)'
    shape=f"SUBSTRING({body},5,1)='-' AND SUBSTRING({body},8,1)='-' AND SUBSTRING({body},11,1) IN ('T',' ') AND SUBSTRING({body},14,1)=':' AND SUBSTRING({body},17,1)=':' AND (LENGTH({body})=19 OR (LENGTH({body}) BETWEEN 21 AND 26 AND SUBSTRING({body},20,1)='.' AND {_digits(fractional)}))"
    raw_parts=[f'SUBSTRING({body},{start},{length})' for start,length in [(1,4),(6,2),(9,2),(12,2),(15,2),(18,2)]]
    valid=shape+' AND '+' AND '.join(_digits(x) for x in raw_parts)
    parts=[f'CASE WHEN {valid} THEN CAST({x} AS INTEGER) ELSE NULL END' for x in raw_parts]
    scale='CASE LENGTH('+fractional+') '+' '.join(f'WHEN {n} THEN {10**(6-n)}' for n in range(1,7))+' ELSE NULL END'
    fraction=f'CASE WHEN {valid} THEN CASE WHEN LENGTH({body})=19 THEN 0 ELSE CAST({fractional} AS BIGINT)*({scale}) END ELSE NULL END'
    return [*parts,fraction,offset]


def _instant_key(y, mon, day, hour, minute, second, fraction, offset):
    leap=f'(MOD({y},400)=0 OR (MOD({y},4)=0 AND MOD({y},100)<>0))'
    ordinal=f'365*({y}-1)+FLOOR(({y}-1)/4.0)-FLOOR(({y}-1)/100.0)+FLOOR(({y}-1)/400.0)+FLOOR((367*{mon}-362)/12.0)+(CASE WHEN {mon}<=2 THEN 0 WHEN {leap} THEN -1 ELSE -2 END)+{day}'
    return f'(CAST(({ordinal}) AS BIGINT)*86400000000+CAST(({hour}*3600+{minute}*60+{second}-{offset}*60) AS BIGINT)*1000000+{fraction})'


def _number(raw):
    # An explicit decimal, with optional sign; units/separators/NaN/Inf are not
    # stripped. Their interpretation belongs in a reviewed source conversion.
    s = f'TRIM(CAST({raw} AS VARCHAR(256)))'
    body = f"CASE WHEN SUBSTRING({s},1,1) IN ('+','-') THEN SUBSTRING({s},2) ELSE {s} END"
    digits = f"REPLACE(({body}),'.','')"
    dot = f"POSITION('.' IN ({body}))"
    integer_digits = f'CASE WHEN {dot}>0 THEN {dot}-1 ELSE LENGTH({body}) END'
    fraction_digits = f'CASE WHEN {dot}>0 THEN LENGTH({body})-{dot} ELSE 0 END'
    valid = f"LENGTH({s}) BETWEEN 1 AND 100 AND {_digits(digits)} AND LENGTH({body})-LENGTH({digits})<=1 AND ({integer_digits})<=28 AND ({fraction_digits})<=10"
    # Explicit scale: MySQL's unqualified DECIMAL defaults to scale zero. Values
    # outside this portable text-decimal range stay unknown instead of rounding.
    return f'CASE WHEN {valid} THEN CAST({s} AS DECIMAL(38,10)) ELSE NULL END'


def compile_period(o, m, args, period, source, bind, predicate, ctes, cache, literal):
    from .semantic import q, table, path
    l = o.latest
    digest=hashlib.sha256(o.id.encode()).hexdigest()[:12]
    prefix = '__latest_'+digest+'_'+period.replace('-','_')
    shared_prefix='__latest_'+digest+'_'+period[:4]+'_shared'
    def add(suffix, query, shared=False):
        name = q((shared_prefix if shared else prefix)+'_'+suffix); ctes.append(name+' AS ('+query+')'); return name
    def column(alias, name): return alias+'.'+q(name)
    def match(left, right):
        return ' AND '.join(column(left,a)+'='+column(right,b) for a,b in zip(l.entity_key,l.report_key))
    rk = ','.join(q(c) for c in l.report_key)
    cache_key=(o.id,period[:4])
    if cache_key not in cache:
        entity_conditions = [predicate(column('e',f['column']),f['op'],f['values']) for f in l.entity_filters]
        if l.entity_year_column:
            typ = next(c.get('type','') for t in source['tables'] if t['name']==l.entity_table for c in t['columns'] if c['name']==l.entity_year_column)
            year = period[:4] if re.search('char|text|string|clob',str(typ),re.I) else int(period[:4])
            entity_conditions.append(column('e',l.entity_year_column)+'='+literal(year))
        entity = add('entities','SELECT e.*,COUNT(*) OVER (PARTITION BY '+','.join(column('e',c) for c in l.entity_key)+') AS "__nw_l_entities" FROM '+table(l.entity_table)+' e'+(' WHERE '+' AND '.join(entity_conditions) if entity_conditions else ''),True)
        types={c['name']:str(c.get('type','')) for t in source['tables'] if t['name']==o.table for c in t['columns']}
        native_date=source['dialect']!='sqlite' and l.date_format=='date' and types[o.time_column].upper()=='DATE'
        timed=l.date_format in ('datetime','timestamp')
        part_names=('year','month','day','hour','minute','second','fraction','offset') if timed else ('year','month','day')
        report_source=table(o.table)
        if timed:
            text=f'TRIM(CAST({column("r",o.time_column)} AS VARCHAR(256)))'
            report_source=add('text','SELECT r.*,'+text+' AS "__nw_l_timestamp_text" FROM '+report_source+' r',True)
            text='r."__nw_l_timestamp_text"'
            if l.date_only=='start_of_day':
                suffix='T00:00:00'+(l.timezone_offset if l.date_format=='timestamp' else '')
                text=f'CASE WHEN LENGTH({text})=10 THEN '+text+'||'+bind(suffix)+' ELSE '+text+' END'
            report_source=add('normalized_time','SELECT r.*,'+text+' AS "__nw_l_timestamp" FROM '+report_source+' r',True)
            body,offset=_timestamp_frame(column('r','__nw_l_timestamp'),l.date_format,timezone_minutes(l.timezone_offset))
            report_source=add('clock','SELECT r.*,'+body+' AS "__nw_l_clock",'+offset+' AS "__nw_l_zone" FROM '+report_source+' r',True)
        parts = _timestamp_parts(column('r','__nw_l_clock'),column('r','__nw_l_zone')) if timed else _date_parts(column('r',o.time_column),l.date_format,native_date)
        normalized = ''
        if o.column and o.aggregation not in ('count','distinct'):
            raw=column('r',o.column)
            native_number=source['dialect']!='sqlite' and re.search(r'^(?:int(?:eger)?|smallint|bigint|tinyint|numeric|decimal|number|float|double|real)(?:\b|\()',types[o.column],re.I)
            value=raw if native_number else _number(raw)
            if l.null_value=='zero':
                empty=raw+' IS NULL' if native_number else f'COALESCE(LENGTH(TRIM(CAST({raw} AS VARCHAR(256)))),0)=0'
                value=f'CASE WHEN {empty} THEN 0 ELSE ({value}) END'
            normalized=','+value+' AS "__nw_l_value"'
        parsed = add('parts','SELECT r.*,'+','.join(x+' AS '+q('__nw_l_'+n) for n,x in zip(part_names,parts))+normalized+' FROM '+report_source+' r WHERE EXISTS (SELECT 1 FROM '+entity+' e WHERE '+match('e','r')+')',True)
        y,mon,day = ('"__nw_l_'+n+'"' for n in ('year','month','day'))
        days = f'CASE WHEN {mon}=2 THEN CASE WHEN MOD({y},400)=0 OR (MOD({y},4)=0 AND MOD({y},100)<>0) THEN 29 ELSE 28 END WHEN {mon} IN (4,6,9,11) THEN 30 ELSE 31 END'
        dkey = f'CASE WHEN {y} BETWEEN 1900 AND 9999 AND {mon} BETWEEN 1 AND 12 AND {day} BETWEEN 1 AND ({days}) THEN {y}*10000+{mon}*100+{day} ELSE NULL END'
        if timed:
            hour,minute,second,fraction,offset=('"__nw_l_'+n+'"' for n in ('hour','minute','second','fraction','offset'))
            valid=f'{y} BETWEEN 1900 AND 9999 AND {mon} BETWEEN 1 AND 12 AND {day} BETWEEN 1 AND ({days}) AND {hour} BETWEEN 0 AND 23 AND {minute} BETWEEN 0 AND 59 AND {second} BETWEEN 0 AND 59 AND {fraction} BETWEEN 0 AND 999999 AND {offset} IS NOT NULL'
            dkey=f'CASE WHEN {valid} THEN '+_instant_key(y,mon,day,hour,minute,second,fraction,offset)+' ELSE NULL END'
        dated = add('dated','SELECT r.*,'+dkey+' AS "__nw_l_date" FROM '+parsed+' r',True)
        bad = add('bad','SELECT '+rk+',MAX(CASE WHEN "__nw_l_date" IS NULL THEN 1 ELSE 0 END) AS "__nw_l_bad_date" FROM '+dated+' GROUP BY '+rk,True)
        cache[cache_key]=(entity,dated,bad)
    entity,dated,bad=cache[cache_key]
    year = int(period[:4]); month = int(period[5:]) if len(period)==7 else None
    if period in l.cutoff_dates:
        end = date.fromisoformat(l.cutoff_dates[period]) + timedelta(days=int(l.cutoff_inclusive))
    else:
        end = date(year+1,1,1) if not month or month==12 else date(year,month+1,1)
    def bound_key(d):
        return d.toordinal()*86400000000-timezone_minutes(l.timezone_offset)*60000000 if l.date_format in ('datetime','timestamp') else d.year*10000+d.month*100+d.day
    bounds = ['"__nw_l_date"<'+literal(bound_key(end))]
    if l.window != 'history':
        begin = date(year,month if month and l.window=='period' else 1,1)
        bounds.append('"__nw_l_date">='+literal(bound_key(begin)))
    order = ['"__nw_l_date" DESC']
    for x in l.tie_breakers:
        c = q(x.column)
        order += [f'CASE WHEN {c} IS NULL THEN 1 ELSE 0 END ASC', c+' '+x.direction.upper()]
    ranked = add('ranked','SELECT r.*,DENSE_RANK() OVER (PARTITION BY '+rk+' ORDER BY '+','.join(order)+') AS "__nw_l_rank" FROM '+dated+' r WHERE '+' AND '.join(bounds))
    latest = add('top','SELECT r.*,COUNT(*) OVER (PARTITION BY '+rk+') AS "__nw_l_ties" FROM '+ranked+' r WHERE "__nw_l_rank"=1')
    base = entity+' e LEFT JOIN '+latest+' b ON '+match('e','b')+' LEFT JOIN '+bad+' z ON '+match('e','z')
    dims = {d.id:d for d in m.dimensions}; aliases = {o.table:'b',l.entity_table:'e'}; joins=[]
    for d in [dims[x] for x in dict.fromkeys(args.group_by+[f.dimension for f in args.filters])]:
        if d.table in aliases: continue
        # Entity-side dimension joins remain subject to normal unique-key validation.
        for start,target,left,right in path(l.entity_table,d.table,m.relations):
            if target in aliases: continue
            alias='j'+str(len(aliases)); aliases[target]=alias
            joins.append(' LEFT JOIN '+table(target)+' '+alias+' ON '+' AND '.join(column(aliases[start],a)+'='+column(alias,b) for a,b in zip(left,right)))
    def report_col(c):
        raw = column('b',c)
        return 'COALESCE(NULLIF(TRIM(CAST('+raw+" AS VARCHAR(256))),''),"+bind(l.blank_defaults[c])+')' if c in l.blank_defaults else raw
    selected = ' AND '.join(predicate(report_col(f['column']),f['op'],f['values']) for f in o.filters) or '1=1'
    present = 'b."__nw_l_ties" IS NOT NULL'
    raw = column('b',o.column) if o.column else None
    value = 'b."__nw_l_value"' if raw and o.aggregation not in ('count','distinct') else raw
    if o.aggregation == 'count':
        counted = '1' if not o.column else f'CASE WHEN {raw} IS NULL THEN 0 ELSE 1 END'
        agg = f'COALESCE(SUM(CASE WHEN {present} AND ({selected}) THEN {counted} ELSE 0 END),0)'
    elif o.aggregation == 'distinct':
        agg = f'COUNT(DISTINCT CASE WHEN {present} AND ({selected}) THEN {raw} ELSE NULL END)'
    else:
        # Excluded/missing records must not lower a mean or minimum. Explicit
        # missing=zero includes the zero only when no report could be selected.
        fallback = '0' if l.missing == 'zero' else 'NULL'
        item = f'CASE WHEN {present} AND ({selected}) THEN ({value}) WHEN NOT ({present}) THEN {fallback} ELSE NULL END'
        agg = o.aggregation.upper()+'('+item+')'
        if o.aggregation == 'sum': agg = 'COALESCE('+agg+',0)'
    value_bad = []
    for f in o.filters:
        c=report_col(f['column'])
        value_bad.append(f'({c} IS NULL)')
        if '' not in f['values']:
            value_bad.append(f'(COALESCE(LENGTH(TRIM(CAST({c} AS VARCHAR(256)))),0)=0)')
    for name,values in l.allowed_values.items():
        value_bad.append('NOT ('+predicate(report_col(name),'in',values)+')')
    if value and o.aggregation not in ('count','distinct'):
        value_bad.append(f'(({selected}) AND (({value}) IS NULL OR ABS({value})>1e308))')
    invalids = {
        'entity': 'e."__nw_l_entities">1 OR '+' OR '.join(column('e',c)+' IS NULL' for c in l.entity_key),
        'date': 'z."__nw_l_bad_date"=1',
        'tie': 'b."__nw_l_ties">1',
        'missing': 'NOT ('+present+')' if l.missing=='unknown' else '1=0',
        'value': present+' AND ('+(' OR '.join(value_bad) or '1=0')+')',
    }
    diagnostic = ''.join(',COALESCE(MAX(CASE WHEN '+condition+' THEN 1 ELSE 0 END),0) AS '+q('__latest_'+key) for key,condition in invalids.items())
    groups = [column(aliases[dims[d].table],dims[d].column) for d in args.group_by]
    scope = [predicate(column(aliases[dims[f.dimension].table],dims[f.dimension].column),f.op,f.values) for f in args.filters]
    projection = [literal(period)+' AS period']+[c+' AS '+q(d) for c,d in zip(groups,args.group_by)]
    return 'SELECT '+','.join(projection)+','+agg+' AS value'+diagnostic+' FROM '+base+''.join(joins)+(' WHERE '+' AND '.join(scope) if scope else '')+(' GROUP BY '+','.join(groups) if groups else '')
