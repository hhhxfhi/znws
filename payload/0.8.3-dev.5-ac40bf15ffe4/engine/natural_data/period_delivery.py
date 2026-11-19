"""Bounded composition of existing metric execution and deterministic analysis.

This is a domain convenience operation, not a second agent loop. No period,
business condition or threshold is inferred here; callers supply typed inputs.
"""
import math
import re
import hashlib
from dataclasses import replace


def comparison_periods(current, baseline):
    # A caller may supply the whole selection plus an explicit baseline. There
    # is no inference when that complete baseline can be removed unambiguously.
    if len(current)==len(set(current)) and len(baseline)==len(set(baseline)) and set(baseline)<set(current):
        remainder=[p for p in current if p not in baseline]
        if len(remainder)==len(baseline):current=remainder
    def validate(values, field):
        if not values:
            raise ValueError(f'COMPARISON_PERIODS: 缺少 {field}。periods 必须显式填写用户要求的本期月份数组，comparison.baseline_periods 填基期月份数组；title 中的日期不能代替参数。请补全后重试，不要重复缺失参数的调用。')
        if len(values)!=len(set(values)):
            raise ValueError(f'COMPARISON_PERIODS: {field} 含重复月份；periods 填本期，comparison.baseline_periods 填基期，两者内部均不可重复。')
        if any(not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])',p) for p in values):
            raise ValueError('COMPARISON_PERIODS: 期间合计比较请逐月填写YYYY-MM；单个年度或时点比较使用analysis_compute。')
        ordered=sorted(values);indexes=[int(p[:4])*12+int(p[5:]) for p in ordered]
        if indexes!=list(range(indexes[0],indexes[0]+len(indexes))):
            raise ValueError('COMPARISON_PERIODS: periods填连续本期月份，comparison.baseline_periods填等长连续基期；不能将非连续月份当完整区间。')
        return ordered
    current,baseline=validate(current,'periods'),validate(baseline,'comparison.baseline_periods')
    if len(current)!=len(baseline) or set(current)&set(baseline):
        raise ValueError('COMPARISON_PERIODS: 本期与基期月份数须相同且不能重叠。')
    return current,baseline


def require_comparison(engine,ctx,request,current,invoke):
    if invoke is None:
        raise ValueError('COMPARISON_UNAVAILABLE: 当前调用入口未接入分析执行；请分别查询并调用analysis_compute。')
    if getattr(engine,'policy',None):engine.policy.require(ctx.project,'analysis','compute')
    if request.change_threshold is not None and not math.isfinite(request.change_threshold):
        raise ValueError('COMPARISON_THRESHOLD: 阈值必须是有限数值。')
    return comparison_periods(current,request.baseline_periods)


def require_additive(contract):
    from natural_analysis.operators_v2 import non_additive_reason
    if not contract.get('additive_over_time') or non_additive_reason(contract):
        raise ValueError('COMPARISON_SCOPE: 此指标不能按月份直接相加；比率、平均值、快照或跨期去重需采用对应业务口径，不能用期间合计比较。')


def complete_comparison(engine,ctx,result,request,current,baseline,invoke):
    rid=result['result_ref']
    args={'result_ref':rid,'operation':'aggregate_compare','baseline_periods':baseline,'current_periods':current}
    if request.change_threshold is not None:args.update(change_threshold=request.change_threshold,comparison=request.operator)
    needed=['period_values','period_changes','period_totals','total_change']
    if request.change_threshold is not None:needed.append('threshold_decision')
    delivery={'kind':'period_comparison','required':needed,'completed':['period_values'],
              'current_periods':current,'baseline_periods':baseline,'status':'partial'}
    claims=[];analysis_ref=None
    try:
        key='comparison:'+hashlib.sha256((ctx.request_key or rid).encode()).hexdigest()
        calculated=invoke(args,replace(ctx,request_key=key))
        claims=calculated['claims'];analysis_ref=calculated['artifact_ref']
        stored=engine.get(ctx.project,rid).get('verified_claims') or claims
        totals=[c for c in stored if c.get('operation')=='aggregate_compare']
        if not totals:raise ValueError('COMPARISON_INCOMPLETE: 未生成期间合计结论。')
        delivery['completed']=needed.copy()
        if any(c.get('change_percent') is None for c in totals):
            delivery['completed'].remove('total_change')
            delivery['reason']='基期为零或变化率未知，无法计算合计变化率。'
        if request.change_threshold is not None and any(c.get('change_matched') is None for c in totals):
            delivery['completed'].remove('threshold_decision')
            delivery['reason']='基期为零或变化率未知，无法判断阈值。'
        delivery['status']='complete' if delivery['completed']==needed else 'partial'
    except ValueError as error:
        # Keep the successful authorized result visible. Policy failures are not
        # caught, and an unknown numerical value is never replaced by zero.
        delivery['reason']=str(error)[:600]
    delivery['missing']=[key for key in needed if key not in delivery['completed']]
    delivery['note']=('计算及对照表已完成，直接引用结论，不再重复查询或调用计算。' if not delivery['missing']
                      else '查询结果已保留；说明尚未完成的计算及原因，不将未知值补零，不重复相同计算。')
    revision,_=engine.artifacts.head(ctx.project,rid,'data.delivery')
    engine.artifacts.publish(ctx.project,rid,'data.delivery',{'delivery':delivery},expected_revision=revision,
                             producer='data.period-comparison@1.0.0',parents=[analysis_ref or result['artifact_ref']])
    return {'delivery':delivery,'claims':claims,**({'analysis_ref':analysis_ref} if analysis_ref else {})}
