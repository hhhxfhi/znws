"""F4-2: traceable derived aggregation over immutable facts.

A customer may ask for a coarse grouping ("大类") of a fine-grained metric
result.  The engine may sum the *immutable published facts* into groups only
after the user has actually confirmed the exact mapping through the host's
native clarification channel.  Tool arguments and model assertions are never
confirmations (same rule as business_ambiguity / intent_contract).

Guarantees:
  * the source result, its facts and the registered metric are never modified;
  * nothing is auto-published to business configuration;
  * unknown / overlapping / missing members, non-additive semantics, missing
    unit, null or non-finite values, truncated fact sets are all rejected;
  * the derived result records input fact_ids, periods, mapping, denominator,
    unit and derivation method, and is an ordinary result usable by text,
    table and pie alike.

An exact match to an authorized, published classification can use that
definition directly. Other mappings require the native confirmation above.
"""
import hashlib
import json
import math
import re
import uuid

from natural_analysis.operators_v2 import non_additive_reason

MAX_GROUPS = 20
MAX_MEMBERS = 200

_AFFIRMATIVE = re.compile(r'(?:确认|确认归类|确认无误|是|是的|对|对的|同意|可以|好的|好|没问题|就这样|按此执行|按这个映射|按此映射)[。！!～~]?')
_NEGATIVE = re.compile(r'不|别|否|取消|拒绝|还是|或者|可能|不确定|？|\?|再想想|等等')
# Member/group text must stay a plain label so the proposal lines are unambiguous.
_BAD_LABEL = re.compile(r'[、：:\n\r]')


def canonical_mapping(dimension, groups):
    return {'dimension': dimension,
            'groups': [{'name': g['name'], 'members': sorted(g['members'])} for g in groups]}


def _group_line(group):
    return group['name'] + '：' + '、'.join(sorted(group['members']))


def proposal_text(result, dimension, groups, unit, periods, *, sql_basis=None):
    """The exact mapping the host must show the user before confirmation."""
    if sql_basis:
        start, stop = sql_basis['start'], sql_basis['stop_exclusive']
        period = f'{start} 至 {stop} 前'
    else:
        period = '、'.join(periods) if periods else '全部'
    lines = ['将结果「' + str(result.get('title', '查询结果')) + '」按维度「' + dimension +
             '」的取值派生汇总为 ' + str(len(groups)) + ' 组（对不可变事实逐组求和；单位：' + unit +
             '；期间：' + period + '）：']
    for i, group in enumerate(groups, 1):
        lines.append(str(i) + '. ' + _group_line(group))
    lines.append('确认该映射？')
    return '\n'.join(lines)


def _require_confirmation(host, proposal, question_id):
    """Only a host-owned native clarification record can confirm the mapping.

    The question identifies the precise result and displays the entire mapping.
    Unrelated questions neither confirm nor revoke it.
    """
    records = (host or {}).get('clarifications') or []
    for record in reversed(records):
        if not (record.get('call_id') and record.get('question_id') == question_id
                and str(record.get('question') or '').strip() == proposal):
            continue
        answer = str(record.get('answer') or '').strip()
        if _AFFIRMATIVE.fullmatch(answer):
            return {'call_id': record['call_id'], 'question_id': question_id,
                    'basis': 'native_question_response'}
        if _NEGATIVE.search(answer):
            raise ValueError('DERIVE_MAPPING_REVOKED: 用户已拒绝或撤回该映射确认，不能派生汇总。'
                             '请保留原结果，等待用户提供新的映射。')
        break
    raise ValueError(f'NEEDS_CLARIFICATION: 通过 ask_user_question 将 id 原样填为 {question_id}，'
                     'question 原样填为换行后的完整提案，提供「确认归类」和「取消」两个选项。用户确认后以相同结果和映射重试。\n'
                     + proposal)


def _published_mapping(grouping, universe, groups):
    if not grouping:
        return None
    from .retrieval import normalize

    categories = [*grouping['prefixes'], grouping['other']]
    names = {normalize(alias): category['label'] for category in categories
             for alias in [category['label'], *category['aliases']]}
    expected = {member: next((category['label'] for category in grouping['prefixes']
                              if member.startswith(category['prefix'])), grouping['other']['label'])
                for member in universe}
    normalized = []
    used = set()
    for group in groups:
        name = names.get(normalize(group['name']))
        if not name or name in used or any(expected[member] != name for member in group['members']):
            return None
        used.add(name)
        normalized.append({'name': name, 'members': group['members']})
    return normalized


def derive(engine, project, args, host):
    source = engine.get(project, args.result_ref)
    if source.get('status') != 'succeeded':
        raise ValueError('DERIVE_SOURCE: 只能对已成功的查询结果派生汇总。')
    facts = source.get('facts') or []
    contract = source.get('metric_contract') or {}
    if not facts and not contract:
        from .row_count_aggregation import facts_from_sql
        facts, contract = facts_from_sql(engine, project, source, args.dimension)
    if not facts:
        raise ValueError('DERIVE_SOURCE: 该结果没有完整事实，请先取得可加指标结果。')
    if source.get('truncated') or len(facts) != source.get('row_count'):
        raise ValueError('DERIVE_INCOMPLETE: 事实不完整（结果被截断或与行数不一致），不能对部分事实求和；请缩小期间或分组后重新查询。')
    if not contract.get('id') or not contract.get('unit'):
        raise ValueError('DERIVE_SEMANTICS: 结果没有已登记指标口径（单位与可加性未知），不能派生汇总；请对已登记指标的查询结果派生。')
    reason = non_additive_reason(contract)
    if reason:
        raise ValueError('DERIVE_SEMANTICS: ' + reason)
    if contract.get('additive') is not True and not (contract.get('additive') is None and contract.get('additive_over_time') is True):
        raise ValueError('DERIVE_SEMANTICS: 该指标声明不可加，不能跨分类求和；请按业务口径重新登记或改用原始分子分母。')
    dimension = args.dimension
    if dimension in (contract.get('overlapping_dimensions') or []):
        raise ValueError('DERIVE_SEMANTICS: 维度「' + dimension + '」登记为可重叠口径，跨取值求和会重复计数，不能派生。')

    groups = [{'name': g.name, 'members': list(g.members)} for g in args.groups]
    if sum(len(group['members']) for group in groups) > MAX_MEMBERS:
        raise ValueError('DERIVE_MAPPING: 单次最多处理 200 个分类标签，请先缩小范围。')
    for group in groups:
        if _BAD_LABEL.search(group['name']) or any(_BAD_LABEL.search(m) or not m.strip() for m in group['members']):
            raise ValueError('DERIVE_MAPPING: 分组名与成员必须是普通文本标签，不能包含顿号、冒号或换行。')
    if len({g['name'] for g in groups}) != len(groups):
        raise ValueError('DERIVE_MAPPING: 分组名不能重复。')

    universe, cells = {}, set()
    for fact in facts:
        dims = fact.get('dimensions') or {}
        if dimension not in dims:
            available = sorted({k for f in facts for k in (f.get('dimensions') or {})})
            raise ValueError('DERIVE_DIMENSION: 事实不含维度「' + dimension + '」；该结果可用维度：' + '、'.join(available) + '。')
        if dims[dimension] is None or fact.get('unit') != contract['unit']:
            raise ValueError('DERIVE_SEMANTICS: 分类或单位未能核对，不能合并。')
        other_dimensions = set(dims) - {dimension, 'period'}
        if other_dimensions & set(contract.get('overlapping_dimensions') or []):
            raise ValueError('DERIVE_SEMANTICS: 原结果含重叠统计维度，请先按不重叠的统计范围查询。')
        cell = json.dumps(dims, sort_keys=True, ensure_ascii=False)
        if cell in cells:
            raise ValueError('DERIVE_SEMANTICS: 存在同一维度、同一期间的重复事实，不能直接求和。')
        cells.add(cell)
        universe.setdefault(str(dims[dimension]), []).append(fact)

    seen = {}
    overlaps = []
    for group in groups:
        for member in group['members']:
            if member in seen:
                overlaps.append(member)
            seen[member] = group['name']
    if overlaps:
        raise ValueError('DERIVE_OVERLAP: 以下取值被分入多个组，求和会重复计数：' + '、'.join(sorted(set(overlaps))) + '。请修正映射。')
    unknown = sorted(set(seen) - set(universe))
    if unknown:
        raise ValueError('DERIVE_UNKNOWN: 以下成员不在原事实的维度取值中：' + '、'.join(unknown) + '。只能使用真实取值，不得编造。')
    missing = sorted(set(universe) - set(seen))
    if missing:
        raise ValueError('DERIVE_MISSING: 以下维度取值未被任何组覆盖：' + '、'.join(missing)
                         + '。先核对这些取值的归属，已覆盖项无需重新归类。提交完整映射后再确认汇总。')

    for fact in facts:
        value = fact.get('value')
        if value is None:
            raise ValueError('DERIVE_NULL: 事实 ' + str(fact.get('id')) + ' 的值为空，不能按零求和；请先补齐或排除该期间后重新查询。')
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('DERIVE_VALUE: 事实 ' + str(fact.get('id')) + ' 的值不是有限数值，不能求和。')

    periods = sorted({str(f['dimensions']['period']) for f in facts if 'period' in (f.get('dimensions') or {})}) \
        or [str(p) for p in (contract.get('periods') or [])]
    if len(periods) > 1 and (contract.get('additive_over_time') is not True or
            any(re.fullmatch(r'\d{4}', p) and any(month.startswith(p + '-') for month in periods) for p in periods)):
        raise ValueError('DERIVE_PERIOD: 多期事实不可跨期相加，或期间重叠；请先按单期或不重叠发生期间查询。')
    unit = contract['unit']
    sql_basis = contract.get('source_sql_count_basis')
    if sql_basis and sum(f['value'] for f in facts) > 2**53 - 1:
        raise ValueError('DERIVE_VALUE: 逐行计数合计超出表图可精确表示的范围，不能近似交付。')
    grouping = contract.get('published_grouping') if sql_basis else None
    published = _published_mapping(grouping, universe, groups)
    if published is not None:
        groups = published
    identity = {'source': args.result_ref, 'mapping': canonical_mapping(dimension, groups)}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if published is not None:
        confirmation = {'basis': 'published_grouping', 'definition': grouping['basis']}
        mapping_note = '按已发布分类汇总本次查询结果。'
    else:
        proposal = proposal_text(source, dimension, groups, unit, periods, sql_basis=sql_basis)
        confirmation = _require_confirmation(host, proposal, 'derive-' + fingerprint[:24])
        mapping_note = '按用户确认的分类汇总本次查询结果。'

    rows, derived_facts, lineage_groups = [], [], []
    for group in groups:
        members = sorted(group['members'])
        member_facts = sorted((f for m in members for f in universe[m]), key=lambda f: str(f.get('id')))
        values = [f['value'] for f in member_facts]
        value = sum(values) if all(type(v) is int for v in values) else math.fsum(values)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        fact_ids = [f['id'] for f in member_facts]
        fid = 'derived:' + hashlib.sha256(json.dumps([args.result_ref, group['name'], fact_ids], ensure_ascii=False).encode()).hexdigest()[:16]
        rows.append({'category': group['name'], 'value': value})
        derived_facts.append({'id': fid, 'dimensions': {'category': group['name']}, 'value': value,
                              'unit': unit, 'derived_from': fact_ids})
        lineage_groups.append({'name': group['name'], 'members': members, 'fact_ids': fact_ids, 'value': value})

    lineage = {'kind': 'derived_aggregation', 'method': 'sum', 'source_result_ref': args.result_ref,
               'dimension': dimension, 'groups': lineage_groups, 'periods': periods, 'unit': unit,
               'denominator': None, 'fact_ids': [f['id'] for f in facts], 'confirmation': confirmation,
               **({'source_sql_count_basis': sql_basis} if sql_basis else {}),
               'note': mapping_note}
    key = 'derive:' + fingerprint
    if sql_basis:
        total = sum(r['value'] for r in rows)
        if total != sum(fact['value'] for fact in facts):
            raise ValueError('DERIVE_VALUE: 分组总数与原结果不一致。')
        shares = {r['category']: 100 * r['value'] / total for r in rows} if total else {}
        lineage.update(group_total=total, denominator=total,
                       groups=[{**g, 'share_percent': shares.get(g['name'])} for g in lineage_groups])
        for fact in derived_facts:
            fact['denominator'] = total
    from .vendor.charts import recommend_chart
    rid = uuid.uuid4().hex
    labels = {'category': '分组', 'value': '合计值'}
    derived_contract = {'id': 'derived:' + fingerprint[:12], 'unit': unit, 'periods': periods,
                        'scope': {'dimensions': ['category']}, 'formula': 'sum(facts.value) by confirmed mapping',
                        'derived': True, 'source_metric_id': contract['id'], 'additive': True,
                        'limitations': [mapping_note]}
    payload = {'title': args.title, 'source_id': source.get('source_id'),
               'source_provenance': source.get('source_provenance', {}),
               'columns': ['category', 'value'], 'rows': rows, 'row_count': len(rows), 'truncated': False,
               'column_labels': labels, 'status': 'succeeded',
               'quality': {'status': 'available', 'empty': False},
               'metric_contract': derived_contract, 'facts': derived_facts,
               'result_lineage': lineage, 'derivation': lineage,
               'receipt': {'source_result_ref': args.result_ref, 'derived': True, 'read_only': True, 'method': 'sum'},
               'chart': recommend_chart(['category', 'value'], rows, title=args.title, labels=labels)}
    if sql_basis:
        group_text = '、'.join(f"{row['category']}{row['value']}{unit}" for row in rows)
        payload['verified_claims'] = [{'operation': 'derived_aggregation',
            'public_text': f'按已确认映射汇总：{group_text}。合计{total}{unit}，为分组占比的分母。',
            'value': total, 'unit': unit, 'fact_refs': [f['id'] for f in derived_facts]}]
    if source.get('business_project'):
        payload['business_project'] = source['business_project']
    with engine.connect() as c:
        c.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?)',
                  (rid, project, key, 'derive:' + fingerprint, 'succeeded',
                   json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)))
        rid = c.execute('SELECT id FROM runs WHERE project=? AND request_key=?', (project, key)).fetchone()[0]
    if source.get('artifact_ref'):
        saved = engine.raw(project, rid)
        engine.artifacts.put(project, 'data.derived', saved['result_lineage'], producer='data.derive_aggregation@1.0.0',
                            ref='derive:' + rid, parents=[source['artifact_ref']])
    return engine.public(engine.get(project, rid))
