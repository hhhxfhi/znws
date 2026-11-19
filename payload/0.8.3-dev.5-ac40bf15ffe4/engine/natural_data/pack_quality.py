"""Runtime checks for the surveying pack, including previously published copies.

All checks are expressions inside the same authorized metric query. A conflicting
row cannot be displayed as zero or promoted to a fact by a second read.
"""
from .semantic import q, table

INSPECTION = {'inspection_pass_rate', 'result_quality_pass_rate'}
MAP_NAMES = {
    'tianditu_service_count': ('CGFW_02_02', '本月天地图服务次数'),
    'tianditu_service_ytd': ('CGFW_02_01', '本年度天地图服务次数'),
    'tianditu_service_mom': ('CGFW_02_03', '本月天地图服务次数_环比'),
}
NEGATIVE = {'pcgzlpd': '不合格', 'zztjfhx': '不符合', 'chaqscztzrlsqk': '未落实'}


def inspection_components(metric, operand, bind):
    """Counts of complete/missing judgments in the same authorized query.

    These diagnose the published denominator; they never pick a new one.
    """
    if metric.id != 'inspection_pass_rate' or operand.id != 'total' or operand.filters or operand.aggregation != 'distinct':
        return {}
    passed = next((o for o in metric.operands if o.id == 'passed' and o.table == operand.table), None)
    if not passed or {f['column'].lower() for f in passed.filters} != set(NEGATIVE):
        return {}
    complete, unknown = [], []
    for f in passed.filters:
        if f['op'] not in ('eq', 'in'): return {}
        field = 'b.' + q(f['column'])
        allowed = ','.join(bind(v) for v in [*f['values'], NEGATIVE[f['column'].lower()]])
        complete.append(field+' IS NOT NULL')
        unknown.append(f'({field} IS NOT NULL AND {field} NOT IN ({allowed}))')
    entity = 'b.' + q(operand.column)
    return {'inspection_complete': 'COUNT(DISTINCT CASE WHEN '+' AND '.join(complete)+' THEN '+entity+' END)',
            'inspection_unknown': 'COUNT(DISTINCT CASE WHEN '+' OR '.join(unknown)+' THEN '+entity+' END)'}


def enabled(source, metric):
    return metric.id in (INSPECTION | MAP_NAMES.keys()) and any(
        str(row.get('origin', '')).startswith('pack:chdl-2026@')
        and (row.get('config') or {}).get('id') == metric.id
        and row['config'].get('version') == metric.version
        and row['config'].get('source_id') == metric.source_id for row in source.get('terms', []))


def indicator_table(source, name):
    matches = [t for t in source['tables'] if t['name'].split('.')[-1].lower() == name]
    return matches[0] if len(matches) == 1 else None


def fields(meta):
    return {c['name'].lower(): c['name'] for c in meta['columns']}


def operand_check(metric, operand, period, source, bind, literal):
    """Return aggregate failure condition, explanation and optional year-end cutoff."""
    if metric.id in INSPECTION and operand.id == 'total' and operand.table.split('.')[-1].lower()=='xxzy_zljdjcb':
        passed = next((o for o in metric.operands if o.id=='passed' and o.table==operand.table), None)
        unknown = []
        for f in passed.filters if passed else []:
            name = f['column'].lower()
            if name not in NEGATIVE or f['op'] not in ('eq','in'): continue
            field = 'b.' + q(f['column'])
            allowed = ','.join(bind(v) for v in [*f['values'], NEGATIVE[name]])
            unknown.append(f'({field} IS NULL OR {field} NOT IN ({allowed}))')
        if unknown:
            return 'MAX(CASE WHEN '+' OR '.join(unknown)+' THEN 1 ELSE 0 END)>0', '检查判定值与登记口径不一致或为空，请核对原始枚举和分母。', None
    if metric.id in MAP_NAMES and operand.table.split('.')[-1].lower()=='ch_zbzb':
        code, label = MAP_NAMES[metric.id]
        if not any(f['column'].lower()=='zbx_id' and f['values']==[code] for f in operand.filters): return None
        meta = next(t for t in source['tables'] if t['name']==operand.table)
        name = fields(meta).get('zbx_mc')
        if not name: return '1=1','缺少指标名称，无法核对指标编码。',None
        name = 'b.'+q(name)
        count = 12 if len(period)==4 and metric.id=='tianditu_service_count' else 1
        period_col = 'b.'+q(operand.time_column)
        cutoff = None
        if len(period)==4 and metric.id=='tianditu_service_ytd':
            textual = any(c['name']==operand.time_column and any(t in str(c.get('type','')).lower() for t in ('char','text','string')) for c in meta['columns'])
            cutoff = period_col+'='+literal(period+'12' if textual else int(period+'12'))
        value_col = 'b.'+q(operand.column)
        problem = (f'COUNT(*)<>{count} OR COUNT(DISTINCT {period_col})<>{count} OR COUNT(NULLIF(TRIM({value_col}),\'\'))<>{count} '
                   f'OR MAX(CASE WHEN {name}={bind(label)} THEN 0 ELSE 1 END)>0')
        return problem,'指标名称、有效值或期数与口径不符，请核对编码名称、重复记录和覆盖月份。',cutoff
    return None


def reference_check(metric, source, bind, value_sql):
    """Compare the declared provincial, ungrouped inspection measure to available references."""
    if metric.id not in INSPECTION: return None
    ops = {o.id:o for o in metric.operands}
    if set(ops)!={'total','passed'} or any(o.table.split('.')[-1].lower()!='xxzy_zljdjcb' for o in ops.values()): return None
    ref = indicator_table(source,'ch_zbzb')
    if not ref: return None
    cols = fields(ref)
    if not {'tjqy_id','tjsj','zbx_id','zbz'} <= cols.keys(): return None
    col = lambda key:'r.'+q(cols[key])
    scope = col('tjqy_id')+'='+bind(440000)+' AND CAST('+col('tjsj')+' AS VARCHAR)=keys.'+q('period')
    checks = [('CHZZ_06_01',q('__m_total')+'.value',0)]
    checks.append(('CHZZ_06_05',value_sql,0.011) if metric.id=='inspection_pass_rate' else ('CHZZ_06_02',q('__m_passed')+'.value',0))
    issues = []
    for code, calculated, tolerance in checks:
        raw = 'CAST(NULLIF(TRIM('+col('zbz')+"),'') AS DECIMAL(30,8))"
        issues.append('(SELECT CASE WHEN COUNT(*)>1 OR (COUNT(*)=1 AND (MAX('+raw+') IS NULL OR ABS(MAX('+raw+')-('+calculated+'))>'+str(tolerance)+')) THEN 1 ELSE 0 END FROM '+table(ref['name'])+' r WHERE '+scope+' AND '+col('zbx_id')+'='+bind(code)+')=1')
    return ' OR '.join(issues)


def check_inspection_route(source, host, *, sql=None, dialect=None, metric_id=None):
    """A generic indicator lookup cannot silently stand in for the selected inspection rate."""
    import re
    import sqlglot
    from sqlglot import exp
    if not any(str(t.get('origin','')).startswith('pack:chdl-2026@') and
               (t.get('config') or {}).get('id') in INSPECTION for t in source.get('terms',[])): return
    text = (host or {}).get('user_text','')
    if not re.search(r'抽检合格率|总体检查合格率|总体合格率|成果质检合格率',text): return
    available = {(t.get('config') or {}).get('id') for t in source['terms'] if str(t.get('origin','')).startswith('pack:chdl-2026@')}
    if metric_id in INSPECTION and INSPECTION <= available and '抽检合格率' in text:
        answers = [r.get('answer','') for r in host.get('clarifications',[]) if re.search('抽检|质量|质检|检查|合格率',r.get('question',''))]
        choice = answers[-1] if answers else text
        # A selected native option may explain the three components in
        # parentheses. That description is not a second selected metric.
        # Only prioritize a single explicit metric name in the option heading;
        # ambiguous or negated choices remain unresolved.
        heading = re.split(r'[（(]', choice, maxsplit=1)[0] if answers else choice
        named_overall = bool(re.search('(?:质量监督检查总体|总体检查|总体)合格率', heading))
        named_quality = bool(re.search('成果质检合格率', heading))
        if named_overall != named_quality:
            overall, quality = named_overall, named_quality
        else:
            overall = bool(re.search('总体|三项',choice)); quality = bool(re.search('成果质检|批成果',choice))
        if overall == quality or re.search('不确定|还是|不按|不要',choice):
            raise ValueError('NEEDS_CLARIFICATION: 抽检合格率有总体检查合格率、成果质检合格率两个已发布口径，请用原生提问让用户选择后查询。')
        if metric_id != ('inspection_pass_rate' if overall else 'result_quality_pass_rate'):
            raise ValueError('INTENT_SCOPE_MISMATCH: 请按已选的抽检口径查询。')
    if metric_id == 'indicator_value_yearly':
        raise ValueError('METRIC_REQUIRED: 请按用户选择查询总体检查合格率或成果质检合格率，不能用通用指标值覆盖明细核对结果。')
    if not sql: return
    tree = sqlglot.parse_one(sql,read=dialect)
    used = {t.name.lower() for t in tree.find_all(exp.Table)}
    if 'xxzy_zljdjcb' in used and tree.find(exp.AggFunc):
        raise ValueError('METRIC_REQUIRED: 请按用户选择的总体或成果质检合格率查询，不能绕过枚举和指标值核对。')
    if 'ch_zbzb' not in used: return
    if any(re.search(r'CHZZ_06|抽检合格率|检查合格率|质检合格',str(lit.this),re.I) for lit in tree.find_all(exp.Literal)):
        raise ValueError('METRIC_REQUIRED: 请使用所选的检查合格率指标核对指标值表与明细；两者冲突时报告待复核。')
