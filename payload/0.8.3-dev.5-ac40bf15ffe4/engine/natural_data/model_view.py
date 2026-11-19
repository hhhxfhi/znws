"""What the model sees from a query result (0.6.1).

Customers run a local model, so small result tables are sent to the model as
values. Without them the model had to call a statistics tool once per number
and could not answer "which month is highest". Large results are cut to a
character budget; the result card always shows the full table.

Only tool receipts go through this module. The result page and API keep using
DataEngine.public() unchanged.
"""
import json
import math
import re
import os
import unicodedata
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from .metric_subject import COUNT_UNITS


def _setting(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def limits():
    # NATURAL_DATA_MODEL_ROWS=0 turns values off (the pre-0.6.1 behaviour).
    return _setting('NATURAL_DATA_MODEL_ROWS', 50), _setting('NATURAL_DATA_MODEL_CHARS', 6000)


def _cell(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, Decimal):
        return float(value) if value == value.to_integral_value() or abs(value) < 1e15 else str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def values_preview(result):
    """Return {'columns','rows','row_count','complete'} or None when disabled."""
    max_rows, max_chars = limits()
    if not max_rows or result.get('status') != 'succeeded':
        return None
    if result.get('metric_contract', {}).get('detail'):
        # Business detail rows stay on the server (threshold summaries only).
        return None
    columns = list(result.get('columns') or [])
    rows = result.get('rows') or []
    out, used = [], len(json.dumps(columns, ensure_ascii=False))
    for row in rows[:max_rows]:
        cells = [_cell(row.get(c)) for c in columns]
        size = len(json.dumps(cells, ensure_ascii=False)) + 1
        if out and used + size > max_chars:
            break
        out.append(cells)
        used += size
    total = result.get('row_count', len(rows))
    complete = len(out) == total and not result.get('truncated')
    preview = {'columns': columns, 'rows': out, 'row_count': total, 'complete': complete}
    if not complete:
        preview['note'] = (f'共 {total} 行，回执只含前 {len(out)} 行；完整数据在结果卡中。'
                           '合计、均值等整体统计用 analysis_field_statistics，不要只按这几行心算。')
    return preview


def _grouped_count_share(full, value, preview, source, *, policy_sql=None):
    contract = value.get('answer_contract') or {}
    receipt = full.get('receipt') or {}
    result_ref = value['result_ref']
    if (contract.get('kind') != 'sql_aggregate' or contract.get('complete') is not True
            or contract.get('result_ref') != result_ref or not preview['complete']
            or receipt.get('read_only') is not True or not receipt.get('query_hash')
            or receipt.get('source_id') != source.get('id')
            or (full.get('validation') or {}).get('state') == 'conflict'
            or (full.get('checks') or {}).get('value_warnings') or full.get('period_count_execution_basis')
            or not 2 <= len(preview['rows']) <= 50):
        return None
    from .display_units import grouped_count_share
    from .read_limits import execution_basis

    sql = policy_sql or full['sql']
    if policy_sql is None and (basis := full.get('execution_basis')):
        policy_sql = basis.get('policy_sql')
        if not isinstance(policy_sql, str) or execution_basis(policy_sql, sql) != basis:
            return None
        sql = policy_sql
    columns = preview['columns']
    shape = grouped_count_share(sql, source['dialect'], columns)
    if not shape:
        return None
    category_index, count_index, share_index, digits = shape
    units = (value.get('analysis_options') or {}).get('column_units') or {}
    count_column, share_column = columns[count_index], columns[share_index]
    unit = units.get(count_column)
    if not unit or unit in {'%', '％', '百分点'} or units.get(share_column) != '%':
        return None
    labels, counts, percentages = set(), [], []
    for row in preview['rows']:
        category, count, percentage = row[category_index], row[count_index], row[share_index]
        if not isinstance(category, str) or not (label := unicodedata.normalize('NFKC', category).strip().casefold()):
            return None
        if (label in labels or type(count) not in (int, float) or not 0 <= count <= 2**53 - 1 or int(count) != count
                or type(percentage) not in (int, float) or not 0 <= percentage <= 100):
            return None
        labels.add(label)
        counts.append(int(count))
        percentages.append(Decimal(str(percentage)))
    total = sum(counts)
    if not 0 < total <= 2**53 - 1:
        return None
    tolerance = Decimal(5).scaleb(-digits - 1) + Decimal('1e-12')
    if any(abs(percentage - Decimal(100 * count) / total) > tolerance for count, percentage in zip(counts, percentages)):
        return None
    return {'schema_version': 1, 'result_ref': result_ref, 'category_column': columns[category_index],
            'count_column': count_column, 'share_column': share_column, 'denominator': total,
            'unit': unit, 'rounding_digits': digits}


def derive_count_partition_shares(result, evidence):
    """Derive the requested share from a complete, disjoint row-count result."""
    contract = result.get('sql_answer_contract') or {}
    partition = contract.get('row_count_partition') or {}
    scope = (result.get('request_scope') or {}).get('request') or {}
    lineage = result.get('result_lineage') or {}
    question = str(scope.get('quote') or '')
    if (not partition.get('group_fingerprint') or not scope.get('user_ref')
            or scope['user_ref'] != lineage.get('request_ref') or lineage.get('purpose') != 'answer'
            or (result.get('validation') or {}).get('state') == 'conflict' or result.get('truncated')
            or not re.search(r'占比|比例|百分比|份额', question)
            or re.search(r'同比|环比|预测|另查|另外|再查|对比|明细|排名|排行|最多|最少', question)):
        return None
    decimals = re.search(r'保留\s*([0-9一二三四五六七八九十两]+)\s*位小数', question)
    digits = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6}
    if decimals:
        raw = decimals[1]
        precision = int(raw) if raw.isascii() and raw.isdecimal() and len(raw) == 1 else digits.get(raw, -1)
        if not 0 <= precision <= 6:
            return None
    else:
        precision = 2
    category, count = partition.get('category_column'), partition.get('count_column')
    share = '本次查询占比'
    if len(result['columns']) != 2 or set(result['columns']) != {category, count} or share in result['columns']:
        return None
    unit = evidence.get(count, {}).get('unit')
    rows = result['rows']
    if unit not in COUNT_UNITS or not 2 <= len(rows) <= 50:
        return None
    labels, counts = set(), []
    for row in rows:
        label, amount = row.get(category), row.get(count)
        if not isinstance(label, str) or not (name := unicodedata.normalize('NFKC', label).strip().casefold()):
            return None
        if (name in labels or type(amount) not in (int, float)
                or not 0 <= amount <= 2**53 - 1 or int(amount) != amount):
            return None
        labels.add(name)
        counts.append(int(amount))
    total = sum(counts)
    if not 0 < total <= 2**53 - 1:
        return None
    quantum = Decimal(1).scaleb(-precision)
    percentages = [float((Decimal(100 * amount) / total).quantize(quantum, rounding=ROUND_HALF_UP)) for amount in counts]
    proof = {'schema_version': 1, 'result_ref': partition['result_ref'], 'category_column': category,
             'count_column': count, 'share_column': share, 'denominator': total, 'unit': unit,
             'rounding_digits': precision, 'basis': 'complete_row_count_partition'}
    return share, percentages, proof


def attach_values(engine, project, value, purpose=None):
    """Add a values preview (and 0.6.2 result checks) to a successful basic-query receipt."""
    if value.get('status') != 'succeeded' or value.get('facts') or not value.get('result_ref'):
        return value
    try:
        full = engine.get(project, value['result_ref'])
    except Exception:
        return value
    from .sql_answer_contract import fiscal_budget_guidance
    measurement_note = fiscal_budget_guidance(full.get('question',''), value.get('answer_contract'))
    if measurement_note:
        value = {**value, 'metric_hint': measurement_note}
    monthly_note = None
    if purpose != 'explore':
        from .holding_rate_scope import result_evidence as holding_rate_evidence
        if holding_rate := holding_rate_evidence(full):
            value = {**value, 'validation': {**(value.get('validation') or {}), 'published_holding_rate': holding_rate}}
        if monthly := (full.get('answer_contract') or {}).get('monthly_sql_coverage'):
            value = {**value, 'answer_contract': full['answer_contract']}
            monthly_note = monthly.get('note')
        if (window := (full.get('request_scope') or {}).get('monthly_sql_window')) and window.get('kind') == 'forecast_history':
            from .monthly_sql_scope import forecast_history
            if history := forecast_history(full, window):
                value = {**value, 'validation': {**(value.get('validation') or {}), 'forecast_history': history}}
    preview = values_preview(full)
    if preview is None:
        if monthly_note:
            return {**value, 'warnings': [*(value.get('warnings') or []), monthly_note]}
        return value
    value = {**value, 'values': preview}
    empty_months = None
    current_period_note = None
    if full.get('sql') and (full.get('receipt') or {}).get('source_id'):
        from .query_identity import query_identity
        source = engine.source(project, full['receipt']['source_id'])
        value['query_identity'] = query_identity(full['sql'], source['dialect'], source['id'])
        if purpose not in ('explore', 'probe'):
            from .work_completion import inventory_observation
            if inventory := inventory_observation(full, source):
                value['answer_contract'] = {**(value.get('answer_contract') or {}), 'work_inventory': inventory}
        if (purpose not in ('explore', 'probe') and preview['complete']
                and not (value.get('validation') or {}).get('forecast_history')
                and ((full.get('request_scope') or {}).get('forecast_target') or {}).get('grain') == 'month'):
            from .monthly_sql_scope import complete_monthly_forecast_history
            if history := complete_monthly_forecast_history(full, source):
                value['validation'] = {**(value.get('validation') or {}), 'forecast_history': history}
        if (purpose not in ('explore', 'probe') and preview['complete']
                and not (value.get('validation') or {}).get('forecast_history')
                and not (value.get('validation') or {}).get('annual_forecast_history')
                and ((full.get('request_scope') or {}).get('forecast_target') or {}).get('grain') == 'year'):
            from natural_analysis.forecasting import complete_annual_forecast_history
            if history := complete_annual_forecast_history(full, source):
                value['validation'] = {**(value.get('validation') or {}), 'annual_forecast_history': history}
                if history.get('reference_only'):
                    value['metric_hint'] = (f"本次金额字段登记为{history['measure_label']}。这些年度金额可单独展示，"
                                            "当前回执无法核实实际投入。查到同范围的实际支出或拨付证据后再做实际投入预测。")
        if purpose not in ('explore', 'probe') and (full.get('result_lineage') or {}).get('purpose') not in ('explore', 'probe'):
            share = (full.get('answer_contract') or {}).get('grouped_count_share')
            if share is None:
                share = _grouped_count_share(full, value, preview, source)
            if share:
                value['answer_contract'] = {**value['answer_contract'], 'grouped_count_share': share}
        if purpose != 'explore' and any((check.get('constraint') or {}).get('kind') == 'province_scope'
                                        and check.get('state') == 'checked' for check in (full.get('validation') or {}).get('checks', [])):
            from .qualification_scope import snapshot_region_evidence
            scoped = engine.catalog(project, source['id'])['sources'][0]
            if region := snapshot_region_evidence(full, scoped):
                value['validation'] = {**(value.get('validation') or {}), 'snapshot_region_coverage': region}
        if purpose != 'explore' and preview['complete'] and full.get('reason_code') == 'NO_MATCH':
            from .monthly_sql_scope import empty_grouped_months
            if empty_months := empty_grouped_months(full, source):
                value['validation'] = {**(value.get('validation') or {}), 'empty_grouped_months': empty_months}
        if purpose != 'explore' and preview['complete']:
            from .current_month import observation, observation_note
            def load_catalog():
                return engine.catalog(project, source['id'])['sources'][0]
            if proof := observation(engine, project, full, source, load_catalog=load_catalog):
                value['validation'] = {**(value.get('validation') or {}), 'current_period_observation': proof}
                current_period_note = observation_note(proof)
    # 0.6.2: range and period checks, and a nudge towards Chinese aliases. Exploration
    # probes (e.g. SELECT DISTINCT fzsj) skip the period check: they are not answers.
    from .result_checks import english_headers, outside_requested_years, period_warnings
    checks = full.get('checks') or {}
    coverage = checks.get('period_coverage') or []
    fix = list(checks.get('value_warnings') or [])
    notes = []
    if purpose == 'explore':
        notes.append('此结果仅用于探索字段取值，筛选范围可能比原问题更宽；不能直接当作原问题的业务统计。作答需保留原问题的期间、对象和组织范围，使用 purpose=answer 重新查询必要值，不把探索记录数移作正式统计。')
        if (full.get('sql') and (full.get('receipt') or {}).get('source_id')
                and any(isinstance(cell, str) and ('：' in cell or re.search(r'[^\W\d_]:\s*[^\W\d_]', cell))
                        for row in preview['rows'] for cell in row)):
            from .sql_guidance import distinct_text_sample
            if sample := distinct_text_sample(source['dialect']):
                notes.append('发现带标签文本，同一组中的第二个项目可能沿用组标签。若尚未确认分隔符，用授权字段替换占位符，探查较长的不同原值：'+sample)
    if purpose != 'explore':
        question = full.get('question', '')
        scope = full.get('request_scope') or {}
        if scope.get('forecast_target') and (quote := (scope.get('request') or {}).get('quote')):
            from natural_analysis.forecasting import historical_scope_text
            question = historical_scope_text({'user_text': quote})
        if empty_months and empty_months['request_periods_match']:
            period_fix = []
            notes.append('本次查询在 ' + '、'.join(empty_months['query_periods']) + ' 未返回符合筛选的观测。'
                         '仅能说明本次来源和查询范围，业务覆盖仍未核实；缺测不能用于计算差额、占比或排名。')
        else:
            period_fix, notes = period_warnings(question, full.get('row_count', 0), coverage)
            validation = value.get('validation') or {}
            request = (validation.get('request_scope') or scope).get('request') or {}
            request_ref = (value.get('result_lineage') or {}).get('request_ref')
            if (request_ref and request_ref == request.get('user_ref') and (quote := request.get('quote'))
                    and full.get('row_count', 0) > 0 and not full.get('truncated') and purpose != 'probe'
                    and (full.get('receipt') or {}).get('read_only') is True
                    and (years := outside_requested_years(quote, coverage))):
                value['validation'] = {**validation, 'result_period_scope': {
                    'state': 'outside_request', 'result_ref': value['result_ref'],
                    'request_ref': request_ref, 'requested_years': years,
                }}
        fix += period_fix
        from .result_checks import zero_result_note
        from .period_count_coverage import note as period_count_note
        from .qualification_scope import published_dual_grade_count
        if count_note := period_count_note(value.get('answer_contract')):
            notes.append(count_note)
        elif published_dual_grade_count(value) == 0:
            notes.append('本次登记等级在该筛选范围内未匹配到单位，统计为0。相似取值需经发布映射确认。')
        else:
            notes = notes + zero_result_note(full.get('columns') or [], full.get('rows') or [])
        if monthly_note:
            notes.append(monthly_note)
        if current_period_note:
            notes.append(current_period_note)
    # 0.6.3: reminders when the SQL disagrees with configured enum values or joins.
    if full.get('sql') and (full.get('receipt') or {}).get('source_id'):
        try:
            from .business_sql_checks import check_sql
            source = next((s for s in engine.catalog(project, full['receipt']['source_id'])['sources'] if s['id'] == full['receipt']['source_id']), None)
            fix += check_sql(full['sql'], source)
        except Exception:
            pass
    if fix or notes:
        value['warnings'] = fix + notes
    if coverage:
        value['period_coverage'] = coverage
    english = english_headers(preview['columns'], full.get('column_labels') or {})
    if english:
        value['header_hint'] = '表头 ' + '、'.join(english[:6]) + ' 是英文，用户看不懂；之后的查询请用中文列别名（如 AS 有效证书数）。'
    options = value.get('analysis_options')
    if isinstance(options, dict):
        options = {k: v for k, v in options.items() if k not in ('values_in_receipt', 'read_values')}
        options['values_in_receipt'] = True
        options['read_values'] = ('数值见 values（columns 为列名，rows 为各行）。用 column_units 对应列的单位作答，不要为读数重复查询。'
                                  if preview['complete'] else preview['note'])
        if fix:
            options['read_values'] = '回执 warnings 指出结果可能有误：先按提示核对或修正查询，再回答。'
        elif (value.get('answer_contract') or {}).get('kind')=='historical_profession_ranking':
            options['read_values'] = 'values 为专业分级明细；answer_contract.statements 已按该份明细算好合计、占比和同专业等级差。直接引用已核算值及单位回答，不需要再次查询或调用分析工具。'
        value['analysis_options'] = options
    return value


METRIC_READ = ('数值见 facts：dimensions 是维度，value 是数值。value 为空表示无法计算，原因见 null_reason（没有 null_reason 时说明原因未确认），'
               '不是 0。直接据此回答，不要为读数再调用工具。')


def monthly_observations(value):
    """Verified observations for one complete requested monthly series.

    Completeness means exactly the registered query scope, never treating a
    missing month as zero or adding nonadditive values across periods.
    """
    facts = value.get('facts') or []
    if not 2 <= len(facts) <= 120 or value.get('truncated'):
        return None
    rows = {}
    for fact in facts:
        dims, n = fact.get('dimensions') or {}, fact.get('value')
        if set(dims) != {'period'} or not isinstance(dims['period'], str) or type(n) not in (float, int) or not math.isfinite(n):
            return None
        rows[dims['period']] = n
    periods = sorted(rows)
    if len(periods) != len(facts) or any(not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', p) for p in periods):
        return None
    year = periods[0][:4]
    annual = periods == [f'{year}-{m:02}' for m in range(1, 13)]
    contract = value.get('metric_contract') or {}
    expected = (contract.get('scope') or {}).get('periods') or contract.get('periods')
    if expected is not None and (len(expected) != len(periods) or set(expected) != set(periods)):
        return None
    if expected is None and not annual:
        return None
    indexes = [int(p[:4]) * 12 + int(p[5:]) for p in periods]
    if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
        return None
    numbers = [rows[p] for p in periods]
    unit = contract.get('unit') or facts[0].get('unit', '')
    def months(indexes):
        return '、'.join((periods[i][:4] + '年' if periods[0][:4] != periods[-1][:4] else '') + str(int(periods[i][5:])) + '月' for i in indexes)
    high, low = max(numbers), min(numbers)
    scope_text = year + '年' if annual else periods[0] + ' 至 ' + periods[-1] + '期间'
    def display(number):
        decimal = Decimal(str(number))
        if unit in COUNT_UNITS and decimal == decimal.to_integral_value():
            return format(decimal.to_integral_value(), 'f')
        return format(decimal, 'f') if isinstance(number, Decimal) else str(number)
    lines = [f"{scope_text}最高为{months(i for i,n in enumerate(numbers) if n == high)}（{display(high)}{unit}），最低为{months(i for i,n in enumerate(numbers) if n == low)}（{display(low)}{unit}）。"]
    drops = [i for i in range(1, len(numbers)) if numbers[i] < numbers[i-1]]
    if drops:
        lines.append('较上月下降的月份有' + months(drops) + '。')
    if contract.get('additive_over_time') and not contract.get('ratio'):
        total = sum(Decimal(str(n)) for n in numbers)
        count = '十二' if annual else str(len(numbers))
        lines.append(f'{count}个月合计{display(total)}{unit}。')
    return {'result_ref': value['result_ref'], 'periods': periods, 'verified_text': ''.join(lines)}


def for_model(value):
    """Drop receipt content that only repeats other fields.

    answer_contract.statements repeats every fact with its dimensions and a
    sentence. The answer guard needs each statement's value, so keep
    statement_id and value and drop the copies.
    """
    # 0.6.2: metric facts carry their values; the 0.5.x "values are not in the
    # receipt" hint would send the model to read them again.
    if isinstance(value.get('metric_contract'),dict) and 'semantic_spec' in value['metric_contract']:
        # Execution retains the complete definition; the model already has the
        # identity, business definition, scope, unit and additivity flags. Repeating
        # SQL operands and all registered coverage months only inflates each step.
        value={**value,'metric_contract':{k:v for k,v in value['metric_contract'].items() if k!='semantic_spec'}}
    options = value.get('analysis_options')
    if value.get('facts') and isinstance(options, dict) and options.get('mode') == 'metric_facts':
        value = {**value, 'analysis_options': {**options, 'values_in_receipt': True, 'read_values': METRIC_READ,
            'note':'这是指标facts结果：analysis_compute只传result_ref、operation及必要事实/期间参数，不填column；合计用operation=sum。不要转SQL字段统计绕过指标口径。',
            'period_comparison':'分别查询的同口径结果：analysis_compute填result_ref和comparison_result_ref。多月同比用aggregate_compare，填baseline_periods/current_periods；一次返回逐月与合计。合计变化率条件用change_threshold和comparison。'}}
    contract = value.get('answer_contract')
    if isinstance(contract, dict) and contract.get('statements'):
        slim = []
        for s in contract['statements']:
            if s.get('fact_refs') == [s.get('statement_id')] and 'value' in s:
                slim.append({'statement_id': s['statement_id'], 'value': s['value']})
            else:
                slim.append(s)
        value = {**value, 'answer_contract': {**contract, 'statements': slim}}
    if value.get('result_ref') and (observation := monthly_observations(value)):
        value = {**value, 'answer_contract': {**(value.get('answer_contract') or {}), 'monthly_observations': observation}}
    metric = value.get('metric_contract') or {}
    guidance = ['正文及进度说明使用中文业务名称、期间、地区名称和单位；不附物理字段名、指标编码、地区编码或内部编号。用户明确要求技术详情时才展示这些信息。']
    validation = value.get('validation') or {}
    if region := validation.get('snapshot_region_coverage'):
        note = '数量仅覆盖本次查到的授权资质快照，省域总体完整性尚未核实。'
        if 'unlocated_count' in region:
            note += '地区未注明的单位单独列示，不归到具体城市。'
        guidance.append(note)
    holding_rate = validation.get('published_holding_rate') or {}
    if (holding_rate.get('mode') == 'registered_single_city' and holding_rate.get('comparison_eligible') is False
            and any((check.get('constraint') or {}).get('kind') == 'holding_rate_scope'
                    and (check.get('constraint') or {}).get('ranking') is True
                    for check in validation.get('checks') or [])):
        guidance.append('先交付已核实的单城持有率及统计时点。跨市比较还缺同日、同口径的分子分母、地市覆盖和人员归属，'
                        '请说明目前无法判断哪个地市最高。')
    if any(check.get('state') == 'unknown' and (check.get('constraint') or {}).get('kind') == 'published_scope'
           for check in validation.get('checks') or []):
        guidance.append('当前查询尚未验证所点名的已发布业务范围。部分枚举、模糊匹配或复杂结构不能当作该范围的全量结果。'
                        '优先用登记的完整取值和可核对的聚合查询；只需分项时明确报告该分项和覆盖范围。')
    from .answer_presentation import calculation_contract
    if isinstance(value.get('answer_contract'), dict):
        value = {**value, 'answer_contract':calculation_contract(value['answer_contract'])}
    guidance.append('直接回答已查得的数据。已有业务定义时按定义计算；未预置指标时按用户意图和授权字段计算，必要时简述实际统计对象、期间、筛选和算法。'
                    'business_confirmation、confirmation等是配置审计状态，不是回答前置条件；正文不提未登记、未关联指标、推定口径或要求业务确认。'
                    '数据缺失、分母为零、不同统计对象等实际限制仍需具体说明，不编造数据或声称经过人工确认。')
    if value.get('result_ref'):
        value = {**value, 'analysis_options': {**(value.get('analysis_options') or {}), 'business_guidance': guidance}}
    reconciliation = value.get('inspection_reconciliation') or {}
    rows = reconciliation.get('rows') or []
    facts = value.get('facts') or []
    # A quality hold applies to the formal value, not to the independently
    # verified components already returned by this same authorized query.
    # Do not advertise completion for partial/mismatched scopes.
    complete = bool(rows and facts and not value.get('truncated') and len(rows) == len(facts)
                    and all(r.get('dimensions') == f.get('dimensions') for r, f in zip(rows, facts))
                    and all(type(r.get(k)) in (int, float) and math.isfinite(r[k]) for r in rows for k in ('numerator', 'denominator')))
    if complete:
        summary = ' '.join(r.get('note', '') for r in rows)
        instruction = (('本次完整性核对已完成，但检查结论有缺失，不计算总体比例。直接据此回答：' if reconciliation.get('status')=='incomplete' else '本次分子、分母及明细复核已完成。直接据此回答：') + summary
                       + ' facts中的空值只表示正式指标未通过质量核对，不表示缺少明细核算。'
                       '不再查表结构、手写SQL或补查其他指标；这些不会解除正式指标的质量限制。'
                       '没有具体错误来源的证据时，不断言公式写反或分母误用；明细复核率不是正式指标的上下限。'
                       + ' '.join(guidance))
        value = {**value, 'metric_hint': instruction,
                 'analysis_options': {**(value.get('analysis_options') or {}), 'reconciliation_complete': True, 'read_values': instruction}}
    return value
