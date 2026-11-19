"""Expose components of the same authorized query while retaining quality holds."""
import ast
import math
from .semantic import Metric
from .pack_quality import enabled


def _ratio_operand_pair(spec):
    operands = {o['id']: o for o in spec.get('operands', [])}
    if len(operands) != 2:
        return None
    try:
        node = ast.parse(spec.get('formula', ''), mode='eval').body
    except SyntaxError:
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if isinstance(node.right, ast.Constant) and node.right.value == 100: node = node.left
        elif isinstance(node.left, ast.Constant) and node.left.value == 100: node = node.right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = node.left, node.right
        if isinstance(left, ast.BinOp) and isinstance(left.op, ast.Mult):
            if isinstance(left.right, ast.Constant) and left.right.value == 100: left = left.left
            elif isinstance(left.left, ast.Constant) and left.left.value == 100: left = left.right
        if isinstance(left, ast.Name) and isinstance(right, ast.Name) and {left.id, right.id} == set(operands):
            return left.id, right.id
    return None


def build(rows, contract, source, rid):
    spec = contract.get('semantic_spec') or {}
    operands = {o['id']: o for o in spec.get('operands', [])}
    pair = _ratio_operand_pair(spec)
    if not pair:
        return
    num_id, den_id = pair
    is_builtin_inspection = spec.get('id') == 'inspection_pass_rate'
    if is_builtin_inspection:
        if not enabled(source, Metric.model_validate(spec)):
            return
        if set(operands) != {'passed', 'total'} or any(o.get('aggregation') != 'distinct' for o in operands.values()):
            return
        allowed = {'pcgzlpd': {'合格','批合格'}, 'zztjfhx': {'符合'}, 'chaqscztzrlsqk': {'落实','符合'}}
        filters = operands['passed'].get('filters', [])
        if len(filters) != 3 or {f['column'].lower() for f in filters} != set(allowed) or any(
                f['op'] not in ('eq','in') or not set(f['values']) <= allowed[f['column'].lower()] for f in filters):
            return
    else:
        has_conflict = any(r.get('__quality_reason') == '指标值表与明细的抽查单位数或合格口径不一致，请先复核两套数。' for r in rows)
        if not has_conflict and not spec.get('reconciliation'):
            return
        if any(o.get('aggregation') not in ('distinct', 'count', 'sum') for o in operands.values()):
            return
    checks = []
    claims = []
    for i, row in enumerate(rows):
        reason = row.get('__quality_reason')
        incomplete = (is_builtin_inspection and row.get('__op_inspection_unknown') == 0
                      and type(row.get('__op_inspection_complete')) in (int,float)
                      and row.get('__op_inspection_complete') < row.get('__op_' + den_id, 0))
        if reason and reason != '指标值表与明细的抽查单位数或合格口径不一致，请先复核两套数。' and not incomplete:
            continue
        numerator, denominator = row.get('__op_' + num_id), row.get('__op_' + den_id)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in (numerator, denominator)):
            continue
        if is_builtin_inspection and (any(int(v) != v for v in (numerator, denominator)) or numerator > denominator):
            continue
        rate = round(100 * numerator / denominator, 2) if denominator and not incomplete else None
        dims = {k: v for k, v in row.items() if not k.startswith('__') and k != 'value'}
        status = 'incomplete' if incomplete else 'conflict' if reason else 'consistent'
        extra = {}
        if is_builtin_inspection:
            if incomplete:
                complete = row['__op_inspection_complete']
                extra = {'complete_count': complete, 'incomplete_count': denominator-complete}
                message = f"{row.get('period','')}抽查{denominator:g}家，有完整三项结论{complete:g}家，其中明确三项全部合格{numerator:g}家；其余{denominator-complete:g}家缺少完整结论。总体合格率无法确认，需补齐结论并确认分母，不能把缺失判成不合格或擅自排除。"
            else:
                message = f"{row.get('period','')}按登记条件复核：三项全部合格{numerator:g}家，抽查{denominator:g}家，明细复核率" + (f'{rate:.2f}%。' if rate is not None else '因分母为零无法计算。')
        else:
            message = f"{row.get('period','')}按登记条件复核：分子{numerator:g}，分母{denominator:g}，明细复核率" + (f'{rate:.2f}%。' if rate is not None else '因分母为零无法计算。')
        if not incomplete: message += ('明细与指标值表不一致，正式指标仍待复核；此值仅为明细复核结果。' if reason else '与本次指标校验一致。')
        checks.append({'dimensions': dims, 'numerator': numerator, 'denominator': denominator, 'detail_rate': rate, 'status': status, 'note': message, **extra})
        claims.append({'claim_id': f'{rid}:inspection:{i}', 'operation': 'inspection_reconciliation', 'result_ref': rid, 'fact_refs': [f'{rid}:{i}'],
                       'numerator': numerator, 'denominator': denominator, 'detail_rate': rate, 'verified_text': message, **extra})
    if checks:
        return {
            'reconciliation_version': 2,
            'status': 'incomplete' if any(r['status'] == 'incomplete' for r in checks) else 'conflict' if any(r['status'] == 'conflict' for r in checks) else 'consistent',
            'rows': checks,
            'claims': claims,
            'instruction': '直接引用已核算的分子、分母与明细复核率。存在冲突时明确正式指标待复核，正文中不要输出英文状态码（如 conflict）或底层物理字段名（如 jcnd、bjdw）；不重复SQL查询，不要求用户再次授权普通核算。',
        }

