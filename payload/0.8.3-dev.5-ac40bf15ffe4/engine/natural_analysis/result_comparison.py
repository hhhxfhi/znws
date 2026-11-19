"""Combine authorized, complete metric facts only when their executed contracts agree."""
from copy import deepcopy


def combine(left, left_rows, right, right_rows):
    def signature(result):
        contract=deepcopy(result.get('metric_contract') or {})
        source=(result.get('receipt') or {}).get('source_id') or (contract.get('scope') or {}).get('source_id') or (contract.get('semantic_spec') or {}).get('source_id')
        if not source or not contract.get('id') or not contract.get('version') or not contract.get('unit'):
            raise ValueError('COMPARISON_SCOPE: 结果缺少已执行来源、指标版本或单位，不能跨结果比较。')
        if contract.get('detail') or contract.get('top_n') or contract.get('cumulative_percent'):
            raise ValueError('COMPARISON_SCOPE: 明细或排名截取结果不能跨结果拼接。')
        contract.pop('periods',None)
        contract.get('scope',{}).pop('periods',None)
        return source,contract
    if signature(left)!=signature(right):
        raise ValueError('COMPARISON_SCOPE: 两份结果的来源、指标版本、单位、筛选或维度不一致，不能比较。请重新查询相同口径。')
    if left['result_ref']==right['result_ref']:
        raise ValueError('COMPARISON_SCOPE: 同一结果直接分析，不重复提供comparison_result_ref。')
    rows=left_rows+right_rows
    ids=[r['id'] for r in rows]
    cells=[tuple(sorted(r['dimensions'].items())) for r in rows]
    if len(ids)!=len(set(ids)) or len(cells)!=len(set(cells)):
        raise ValueError('COMPARISON_SCOPE: 两份结果存在重复期间和分组，不能重复计入。')
    unit=left['metric_contract']['unit']
    if any(r.get('unit')!=unit for r in rows):
        raise ValueError('COMPARISON_SCOPE: 事实单位与已执行指标不一致。')
    merged=deepcopy(left)
    periods=sorted(set(left['metric_contract'].get('periods',[])+right['metric_contract'].get('periods',[])))
    merged['metric_contract']['periods']=periods
    merged['metric_contract'].setdefault('scope',{})['periods']=periods
    merged['facts']=rows;merged['row_count']=len(rows)
    return merged,rows
