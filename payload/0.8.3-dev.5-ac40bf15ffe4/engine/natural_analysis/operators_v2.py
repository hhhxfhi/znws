"""Grouped deterministic analysis. Periods and populations never mix implicitly."""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
import calendar
import re
from .claims import trend_summary

def group_key(f):
    return tuple(sorted((k,v) for k,v in f['dimensions'].items() if k!='period'))

def difference(b,a,operation):
    delta=a['value']-b['value']
    return {'operation':operation,'baseline':b,'current':a,'difference':delta,
            'difference_unit':'百分点' if a['unit']=='%' else a['unit'],
            # Divide by |baseline| so growth from a negative base is positive
            # (-100 -> -50 is +50%), matching methods.py and the direction text.
            'change_percent':100*delta/abs(b['value']) if b['value'] else None,'zero_base':b['value']==0,
            'negative_base':b['value']<0}

def non_additive_reason(contract):
    """Why values of this metric cannot be summed across rows, or None."""
    if contract.get('ratio') or contract.get('unit')=='%':
        return '比率不能相加；整体比率必须重新按分子分母计算。'
    if contract.get('mean_kind'):
        return '平均值不能相加或求占比；请按原始分子分母重新计算整体口径。'
    spec=contract.get('semantic_spec') or {}
    aggregations={o.get('aggregation','sum') for o in spec.get('operands',[])}
    if 'single_value' in aggregations:
        return '来源已统计值未声明可加性，不能相加或求占比；请先核定合并口径。'
    if aggregations & {'avg','min','max'}:
        return '平均值、最大值或最小值不能相加或求占比；请改用可加的原始口径。'
    if 'distinct' in aggregations:
        return '去重计数在各分组间可能重复，不能直接相加或求占比；请按整体重新去重统计。'
    return None


def _group_additive(contract, group_rows):
    if non_additive_reason(contract) is not None:
        return False
    overlapping = contract.get('overlapping_dimensions', ['profession'] if contract.get('id') == 'professional_units' else [])
    if overlapping and any(len({f['dimensions'].get(field) for f in group_rows}) > 1 for field in overlapping):
        return False
    if 'additive' in contract:
        return contract['additive'] is True
    return contract.get('additive_over_time') is True

def aggregate(facts,period):
    digest=hashlib.sha256(json.dumps([f['id'] for f in facts]).encode()).hexdigest()[:16]
    return {'id':'derived:'+digest,'dimensions':{'period':period,'aggregation':'分类合计'},
            'value':sum(f['value'] for f in facts),'unit':facts[0]['unit'],'derived_from':[f['id'] for f in facts]}

def compute(args,result,rows):
    op=args.operation;contract=result['metric_contract'];claims=[]
    if op=='aggregate_compare':
        return aggregate_compare(args,result,rows)
    if op=='sum':
        reason=non_additive_reason(contract)
        if reason:raise ValueError(reason)
        selected=rows
        if args.fact_ids is not None:
            ids=args.fact_ids
            if not ids or len(ids)!=len(set(ids)):
                raise ValueError('合计事实ID不能为空或重复。')
            selected=[f for f in rows if f['id'] in ids]
            if len(selected)!=len(ids):raise ValueError('合计包含不属于当前结果的事实ID。')
        periods={f['dimensions'].get('period') for f in selected}
        if not selected:
            raise ValueError('没有可合计的事实，不能将缺失结果当作零。')
        cells=[(group_key(f),f['dimensions'].get('period')) for f in selected]
        if len(cells)!=len(set(cells)):
            raise ValueError('合计存在同一维度同一期间的重复事实，不能重复相加。')
        units={f.get('unit',contract.get('unit')) for f in selected}
        if len(units)>1 or contract.get('unit') is not None and units!={contract['unit']}:
            raise ValueError('合计事实的单位与指标口径不一致。')
        intervals=[]
        for period in periods:
            # A single full-population result has no calendar interval to overlap.
            # This permission comes from the executed metric contract, never the caller.
            if periods == {'全部'} and (contract.get('semantic_spec') or {}).get('time_mode') == 'all' and contract.get('additive') is True:
                continue
            value=str(period)
            if re.fullmatch(r'\d{4}',value):
                year=int(value);bounds=(datetime(year,1,1),datetime(year,12,31))
            elif re.fullmatch(r'\d{4}-\d{2}',value):
                year,month=map(int,value.split('-'))
                bounds=(datetime(year,month,1),datetime(year,month,calendar.monthrange(year,month)[1]))
            else:
                raise ValueError('无法确认合计期间是否重叠。')
            if any(bounds[0]<=end and start<=bounds[1] for start,end in intervals):
                raise ValueError('合计期间重叠，不能把年度和其包含的月份重复相加。')
            intervals.append(bounds)
        if len(periods)>1 and not contract.get('additive_over_time'):
            raise ValueError('存量快照不能跨期相加。')
        overlapping=contract.get('overlapping_dimensions',['profession'] if contract['id']=='professional_units' else [])
        overlap=any(len({f['dimensions'].get(field) for f in selected})>1 for field in overlapping)
        occurrence_basis=getattr(args,'sum_basis','non_overlapping')=='category_occurrences'
        if overlap and not occurrence_basis:
            raise ValueError('分类之间存在重复实体，不能称作去重总数。用户需要分类计次合计时显式使用sum_basis=category_occurrences，并保留计次口径。')
        dimensions={k:sorted({str(f['dimensions'].get(k)) for f in selected}) for k in set().union(*(f['dimensions'] for f in selected))}
        return [{'operation':'sum','value':math.fsum(f['value'] for f in selected),
                 'sum_basis':'category_occurrences' if overlap else 'non_overlapping',
                 'included_fact_ids':[f['id'] for f in selected], 'dimensions':dimensions,
                 'selection':'完整结果' if len(selected)==len(rows) else '所选事实', 'count':len(selected)}]
    if op=='month_over_month':
        groups=defaultdict(list)
        for f in rows:groups[group_key(f)].append(f)
        for key,group in sorted(groups.items()):
            series=[]
            for b,a in zip(sorted(group,key=lambda f:f['dimensions']['period']),sorted(group,key=lambda f:f['dimensions']['period'])[1:]):
                bp=datetime.strptime(b['dimensions']['period'],'%Y-%m');ap=datetime.strptime(a['dimensions']['period'],'%Y-%m')
                adjacent=(ap.year-bp.year)*12+ap.month-bp.month==1
                claim=difference(b,a,op)
                if not adjacent:claim['change_percent']=None
                pct=claim['change_percent']
                claim.update(adjacent=adjacent,anomaly_threshold=args.threshold,anomaly_comparison='<',anomaly=None if pct is None or args.threshold is None else pct < -abs(args.threshold))
                series.append(claim)
            summary=trend_summary(series);summary['dimensions']=dict(key)
            claims.extend(series+[summary])
        return claims
    if op in ['period_compare','reconcile']:
        periods=sorted({f['dimensions'].get('period') for f in rows})
        def resolve(period):
            if period in periods:return period
            matches=[p for p in periods if p==str(period)+'-12'] if contract.get('aggregation')=='snapshot' else []
            return matches[0] if len(matches)==1 else period
        baseline=resolve(args.baseline_period);current=resolve(args.current_period)
        if baseline is None and current is None and len(periods)==2:baseline,current=periods
        if baseline is None or current is None or baseline==current:raise ValueError('比较需要不同的明确基期和本期。')
        before={group_key(f):f for f in rows if f['dimensions'].get('period')==baseline}
        after={group_key(f):f for f in rows if f['dimensions'].get('period')==current}
        if not before or before.keys()!=after.keys():raise ValueError('两期的维度集合不完整一致，不能把缺失当零。')
        claims=[difference(before[k],after[k],op) for k in sorted(before)]
        if len(before)>1 and _group_additive(contract, list(before.values())):
            claims.append(difference(aggregate(list(before.values()),baseline),aggregate(list(after.values()),current),op))
        if op=='reconcile':
            if args.stated_value is None:raise ValueError('复核需要待核对的差额stated_value。')
            for c in claims:c.update(stated_value=args.stated_value,matches=abs(c['difference']-args.stated_value)<.005)
        return claims
    if op=='compare_values':
        def get(ref):
            exact=[f for f in rows if f['id']==ref]
            if exact:return exact[0]
            matches=[f for f in rows if ref is not None and str(ref) in map(str,f['dimensions'].values())]
            return matches[0] if len(matches)==1 else None
        b,a=get(args.baseline_fact_id),get(args.current_fact_id)
        if b is None or a is None or b['id']==a['id']:raise ValueError('比较需提供两个不同的事实ID或已保存sum的claim_id；另一份同口径结果的引用还须填comparison_result_ref。多月合计同比用aggregate_compare，填baseline_periods/current_periods；不要重复提交无效引用。')
        return [difference(b,a,op)]
    if op in ['branch_compare','threshold_filter']:
        if op=='branch_compare' and (args.baseline_fact_id or args.current_fact_id):
            before=next((f for f in rows if f['id']==args.baseline_fact_id),None)
            after=next((f for f in rows if f['id']==args.current_fact_id),None)
            if before is None or after is None or before['id']==after['id']:
                raise ValueError('分支比较需要两个不同的有效事实ID。')
            claim=difference(before,after,op)
            delta=claim['difference'];t=0 if args.threshold is None else args.threshold
            v=t if math.isclose(delta,t,rel_tol=0,abs_tol=1e-9) else delta
            matched={'<':v<t,'<=':v<=t,'>':v>t,'>=':v>=t,'==':v==t}[args.comparison]
            return [{**claim,'comparison':args.comparison,'threshold':t,'matched':matched,'comparison_basis':'current_minus_baseline'}]
        if args.threshold is None:raise ValueError('缺少明确阈值threshold。')
        for f in rows:
            v=args.threshold if math.isclose(f['value'],args.threshold,rel_tol=0,abs_tol=1e-9) else f['value'];t=args.threshold
            matched={'<':v<t,'<=':v<=t,'>':v>t,'>=':v>=t,'==':v==t}[args.comparison]
            claims.append({'fact':f,'threshold':t,'comparison':args.comparison,'matched':matched,'difference':f['value']-t})
        return claims
    if op in ['rank','share','cumulative_share']:
        if op!='rank' and (contract['unit']=='%' or any(f['value']<0 for f in rows)):raise ValueError('比率和负值不适合直接求份额。')
        if op!='rank' and non_additive_reason(contract) and not any('denominator' in f for f in rows):raise ValueError(non_additive_reason(contract))
        groups=defaultdict(list)
        for f in rows:groups[f['dimensions'].get('period')].append(f)
        for period,group in sorted(groups.items()):
            denominators={f['denominator'] for f in group if 'denominator' in f}
            if len(denominators)>1:raise ValueError('分母不一致。')
            total=next(iter(denominators)) if denominators else sum(f['value'] for f in group)
            ordered=sorted(group,key=lambda f:(-f['value'],f['id']));cumulative=0
            prefix=[]
            for f in ordered:
                prefix.append(f['id'])
                cumulative+=f['value'];c={'fact':f,'rank':1+sum(x['value']>f['value'] for x in group)}
                if op!='rank':c.update(share_percent=100*f['value']/total if total else None,denominator=total)
                if op=='cumulative_share':c.update(cumulative_percent=100*cumulative/total if total else None,cumulative_value=cumulative,included_fact_ids=list(prefix),prefix_count=len(prefix))
                claims.append(c)
        return claims
    raise ValueError('未登记的分析算子。')


def aggregate_compare(args,result,rows):
    """Compare matching complete period sets, preserving each population separately."""
    from .capability import Compute
    baseline,current=args.baseline_periods,args.current_periods
    if not baseline or not current or len(baseline)!=len(current):
        raise ValueError('AGGREGATE_SCOPE: 请提供等长、完整的baseline_periods和current_periods。')
    if any(getattr(args,k,None) is not None for k in ('fact_ids','baseline_fact_id','current_fact_id','baseline_period','current_period')):
        raise ValueError('AGGREGATE_SCOPE: 汇总比较只用两组期间列表，不混用单个事实或期间参数。')
    def ordinals(periods):
        if len(periods)!=len(set(periods)):raise ValueError('AGGREGATE_SCOPE: 期间不能重复。')
        if not all(re.fullmatch(r'\d{4}-\d{2}',p) for p in periods):
            raise ValueError('AGGREGATE_SCOPE: 汇总比较需要YYYY-MM格式的完整月份列表。')
        try:
            parsed=[datetime.strptime(p,'%Y-%m') for p in sorted(periods)]
        except ValueError:raise ValueError('AGGREGATE_SCOPE: 期间不是有效月份。')
        values=[p.year*12+p.month for p in parsed]
        if values!=list(range(values[0],values[-1]+1)):
            raise ValueError('AGGREGATE_SCOPE: 所选月份不连续，不能将缺月当作完整区间。')
        return values
    a,b=ordinals(baseline),ordinals(current)
    if set(baseline)&set(current) or len({y-x for x,y in zip(a,b)})!=1:
        raise ValueError('AGGREGATE_SCOPE: 两组期间须一一对应且不重叠。')
    before,after=sorted(baseline),sorted(current)
    groups=defaultdict(dict)
    for f in rows:
        period=f['dimensions'].get('period')
        if period not in set(before+after):continue
        key=group_key(f)
        if period in groups[key]:raise ValueError('AGGREGATE_SCOPE: 同一维度的月份重复。')
        groups[key][period]=f
    if not groups:raise ValueError('AGGREGATE_SCOPE: 当前结果缺少所选期间数据。')
    claims=[]
    for key,values in sorted(groups.items()):
        if set(values)!=set(before+after):
            raise ValueError('AGGREGATE_SCOPE: 两组期间的分组或月份不完整，不能把缺失当零。')
        selections=[[values[p] for p in periods] for periods in (before,after)]
        totals=[]
        for periods,selected in zip((before,after),selections):
            ids=[f['id'] for f in selected]
            total=compute(Compute(result_ref=args.result_ref,operation='sum',fact_ids=ids),result,rows)[0]
            totals.append({'id':'derived:'+hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:16],
                           'value':total['value'],'unit':result['metric_contract']['unit'],
                           'dimensions':{**dict(key),'period':periods[0]+'至'+periods[-1]},'derived_from':ids})
        monthly=[difference(values[p],values[q],'period_compare') for p,q in zip(before,after)]
        summary=difference(*totals,'aggregate_compare')
        net=summary['difference']
        extreme=(max if net>0 else min)(c['difference'] for c in monthly) if net else None
        summary['major_changes']=[c for c in monthly if c['difference']==extreme] if net else []
        summary['baseline_periods']=before;summary['current_periods']=after
        claims.extend(monthly+[summary])
    return claims
