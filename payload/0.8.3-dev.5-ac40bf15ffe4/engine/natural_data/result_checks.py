"""Checks on a basic-query result (0.6.2).

- alias_labels: Chinese header for an English SQL alias such as valid_cert_count.
- value_warnings: a bounded rate outside 0-100% or a negative count means the SQL is wrong.
- period_coverage / period_warnings: the result's years against the question's years,
  so data for 2026 is not reported as 2025.

All checks are advisory: they add text for the model, they never block a query. The
engine stores them under the result's private `checks` key; only the model receipt
(model_view.attach_values) shows them, so NATURAL_DATA_MODEL_ROWS=0 turns them off too.
"""
import re

# Each English word maps to Chinese. A name is translated only when every word is known,
# so a header is never half Chinese, half English. Ambiguous words (area, first, day)
# are left out on purpose.
WORDS = {
    'total': '总', 'all': '全部', 'count': '数量', 'cnt': '数量', 'num': '数量', 'number': '数量', 'qty': '数量',
    'sum': '合计', 'avg': '平均', 'average': '平均', 'mean': '平均', 'max': '最大', 'min': '最小', 'median': '中位数',
    'rate': '率', 'ratio': '比例', 'pct': '占比', 'percent': '百分比', 'percentage': '百分比', 'share': '占比',
    'diff': '差额', 'delta': '变化量', 'change': '变化', 'growth': '增长', 'yoy': '同比', 'mom': '环比',
    'year': '年份', 'yr': '年份', 'month': '月份', 'date': '日期', 'quarter': '季度',
    'period': '期间', 'time': '时间', 'start': '开始', 'end': '结束', 'issue': '发证', 'expire': '到期',
    'city': '地市', 'county': '区县', 'district': '区县', 'province': '省份', 'region': '地区',
    'grade': '等级', 'level': '等级', 'type': '类型', 'category': '类别', 'kind': '类别', 'class': '类别',
    'status': '状态', 'state': '状态', 'name': '名称', 'code': '编码', 'id': '编号', 'no': '编号',
    'cert': '证书', 'certs': '证书', 'certificate': '证书', 'certificates': '证书', 'license': '资质', 'qualification': '资质',
    'unit': '单位', 'units': '单位', 'company': '单位', 'companies': '单位', 'org': '单位', 'orgs': '单位', 'enterprise': '单位',
    'project': '项目', 'projects': '项目', 'item': '事项', 'items': '事项', 'case': '案卷', 'cases': '案卷',
    'amount': '金额', 'money': '金额', 'fund': '资金', 'funds': '资金', 'budget': '预算', 'expense': '支出', 'spend': '支出', 'spent': '实际支出',
    'cost': '费用', 'investment': '投入', 'invest': '投入', 'income': '收入',
    'pass': '合格', 'passed': '合格', 'qualified': '合格', 'fail': '不合格', 'failed': '不合格', 'unqualified': '不合格',
    'valid': '有效', 'effective': '有效', 'active': '有效', 'invalid': '无效', 'expired': '过期', 'cancelled': '注销', 'canceled': '注销',
    'approved': '批准', 'rejected': '不予批准', 'done': '办结', 'finished': '办结', 'completed': '完成',
    'completion': '完成', 'accepted': '验收', 'acceptance': '验收', 'ontime': '按时', 'overdue': '超时',
    'inspection': '抽检', 'sample': '抽检', 'checked': '已检查', 'record': '记录', 'records': '记录',
    'service': '服务', 'services': '服务', 'visit': '访问', 'visits': '访问', 'review': '审核', 'reviews': '审核',
    'distinct': '去重', 'unique': '去重', 'new': '新增', 'normal': '正常', 'abnormal': '异常', 'other': '其他',
    'major': '专业', 'profession': '专业', 'specialty': '专业',
}
COUNT_WORDS = {'count', 'cnt', 'num', 'number', 'qty'}
PREFIX_WORDS = {'avg', 'average', 'mean', 'max', 'min', 'median'}


def _words(name):
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', str(name))
    return [w for w in re.split(r'[_\s\-.]+', name.lower()) if w]


def translate(name):
    """Chinese header for an English alias, or None when any word is unknown or the
    word order would make the label misleading."""
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_\s\-.]*', name):
        return None
    words = _words(name)
    if len(words) < 2 or any(w not in WORDS for w in words):
        return None
    counts = [i for i, w in enumerate(words) if w in COUNT_WORDS]
    if counts and counts != [len(words) - 1]:
        return None  # count_valid would read "数量有效"; leave it to the model
    head, total = [], False
    for w in words:
        if w == 'total':
            total = True
        elif w in PREFIX_WORDS:
            head.insert(0, WORDS[w])
        else:
            head.append(WORDS[w])
    if total:
        if counts:
            head = head[:-1] + ['总数']  # total_cert_count -> 证书总数
        else:
            head = ['总'] + head  # total_amount -> 总金额
    parts = [p for i, p in enumerate(head) if i == 0 or p != head[i - 1]]
    text = ''.join(parts).replace('年份月份', '年月')
    return text if len(text) >= 2 else None


def alias_labels(columns, labels):
    """Labels for SQL aliases that are not catalog columns and have no label yet."""
    out = {}
    for column in columns or []:
        if column in (labels or {}):
            continue  # a catalog column keeps its own label (or its own name)
        label = translate(column)
        if label:
            out[column] = label
    return out


def english_headers(columns, labels):
    """English SQL aliases still shown as-is. Raw catalog columns (e.g. pinyin zsbh)
    are the database's own names and are not flagged."""
    return [c for c in columns or [] if re.fullmatch(r'[A-Za-z0-9_\s\-.]+', str(c)) and c not in (labels or {})]


def _tokens(text):
    return set(_words(re.sub(r'[^A-Za-z0-9_]+', '_', str(text))))


# Rates that cannot exceed 100% (a share of a whole). Execution or completion rates can.
BOUNDED_RATE = re.compile(r'合格|占比|比例|覆盖|通过|达标|及格|批准率|办结率|按时')
BOUNDED_TOKENS = {'pass', 'share', 'ratio', 'pct', 'percent', 'percentage', 'proportion', 'coverage'}
RATE_TOKENS = BOUNDED_TOKENS | {'rate'}
NOT_RATE = re.compile(r'频率|功率|汇率|利率|效率')
COUNT_CN = re.compile(r'(?:个数|件数|次数|总数|户数|人数|项数|家数|本数|张数|数量|(?<![系指分倍编])数)(?:\n|$)')
COUNT_TOKENS = {'count', 'cnt', 'num', 'number', 'qty', 'times'}
# Changes, nets and amounts can be negative; none of them is a range error.
SIGNED = re.compile(r'增长|变化|变动|同比|环比|增幅|降幅|增减|增量|减少|差|净|利润|盈亏|金额|余额')
SIGNED_TOKENS = {'growth', 'change', 'yoy', 'mom', 'delta', 'diff', 'net', 'profit', 'amount', 'money', 'balance', 'loss'}


def _numbers(rows, column):
    out = []
    for row in rows or []:
        value = row.get(column) if isinstance(row, dict) else None
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def value_warnings(columns, rows, labels=None):
    warnings = []
    for column in columns or []:
        name = str(column) + '\n' + str((labels or {}).get(column, ''))
        tokens = _tokens(column)
        values = _numbers(rows, column)
        if not values:
            continue
        shown = (labels or {}).get(column) or column
        if SIGNED.search(name) or tokens & SIGNED_TOKENS:
            continue
        bounded = bool(BOUNDED_RATE.search(name) or tokens & BOUNDED_TOKENS)
        is_rate = bounded or ('率' in name and not NOT_RATE.search(name)) or 'rate' in tokens
        # 0.7.1: 「审核通过总人数」「合格单位数」 are counts that only mention a rate word.
        if is_rate and '率' not in name and not tokens & (RATE_TOKENS - {'pass', 'coverage'}) \
                and (COUNT_CN.search(name) or tokens & COUNT_TOKENS):
            bounded = is_rate = False
        if is_rate:
            low, high = min(values), max(values)
            fraction = high <= 1.0 and low >= -1.0
            limit, tolerance = (1.0, 1e-4) if fraction else (100.0, 0.01)
            if low < -tolerance or (bounded and high > limit + tolerance):
                bad = low if low < -tolerance else high
                shown_value = round(bad * 100 if fraction else bad, 4)
                warnings.append(f'「{shown}」出现 {shown_value}%，不在 0–100% 范围内，SQL 的分子分母很可能写错了'
                                '（例如分子大于分母或用 1 减错了值）。请核对 SQL，修正后再回答，不要直接引用这个数值。')
        elif (COUNT_CN.search(name) or tokens & COUNT_TOKENS) and min(values) < 0:
            warnings.append(f'「{shown}」出现负数 {min(values):g}，计数不可能为负，请核对 SQL 中的相减或关联条件后再回答。')
    return warnings


# A year in the question needs a year context: 2025年, 2025-12, 2025/12, 2025.12.
YEAR = re.compile(r'(?<![\d.])(19[5-9]\d|20\d\d)(?=\s*(?:年|-\d|/\d|\.\d))')
# Questions about validity at a point or over a span: dates outside the year are normal.
RANGE_WORDS = re.compile(r'截至|截止|以来|以前|以后|之前|之后|至今|有效|到期|期内|累计|存量|在册|仍')
UNFIXED_YEAR = re.compile(r'同比|环比|同期|上期|下期|去[年月]|前年|今年|明年|本[年月季]|'
                          r'[上下前后](?:一|个)?(?:年|月|季)|(?:近|过去|最近|未来|往后)\s*[一二三四五六七八九十两\d]+[年月季]|'
                          r'历年|往年|预测|预估|外推|例如|比如|示例|为例|不含|排除')
PERIOD_TOKENS = {'year', 'yr', 'nf', 'nd', 'month', 'yf', 'period', 'date', 'dt', 'rq', 'sj'}
PERIOD_CN = re.compile(r'(?:年|年份|年度|月份|日期|时间|期间)(?:\n|$)')
NOT_PERIOD = re.compile(r'数|量|金额|count|times|amount|total', re.I)
PERIOD_VALUE = re.compile(r'^(19[5-9]\d|20\d\d)(?:年|(?:[-/.年](\d{1,2})(?:[-/.月](\d{1,2})日?)?(?:[ T].*)?月?))?$')


def _period_named(column, label):
    name = str(column) + '\n' + str(label or '')
    if NOT_PERIOD.search(name):
        return False
    return bool(PERIOD_CN.search(name) or _tokens(column) & PERIOD_TOKENS)


def _period(value, by_name):
    """'2025' or '2025-03' from a cell. Bare years count only in a period-named column,
    so a count of 2025 certificates is not read as the year 2025."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if by_name and float(value).is_integer() and 1950 <= value <= 2099:
            return f'{int(value):04d}'
        return None
    if isinstance(value, str):
        m = PERIOD_VALUE.match(value.strip())
        if m and (m.group(2) or by_name):
            return m.group(1) + (f'-{int(m.group(2)):02d}' if m.group(2) else '')
    return None


def period_coverage(columns, rows, labels=None):
    """[{'column','from','to'}] for every column that holds years or dates."""
    rows = [r for r in rows or [] if isinstance(r, dict)]
    out = []
    for column in columns or []:
        by_name = _period_named(column, (labels or {}).get(column))
        cells = [row.get(column) for row in rows if row.get(column) not in (None, '')]
        values = [v for v in (_period(c, by_name) for c in cells) if v]
        if not values or len(values) < 0.8 * len(cells):
            continue
        out.append({'column': column, 'from': min(values), 'to': max(values)})
    return out


def question_years(text):
    return sorted(set(YEAR.findall(text or '')))


def outside_requested_years(question, coverage):
    """Prove disjointness only for an explicitly fixed year window."""
    years = question_years(question)
    if not years or not coverage or RANGE_WORDS.search(question) or UNFIXED_YEAR.search(question):
        return []
    # Treat the years as an encompassing interval. A range written with two
    # endpoints may include intervening years that are not named in the text.
    if any(c['from'][:4] <= years[-1] and years[0] <= c['to'][:4] for c in coverage):
        return []
    return years


def period_warnings(question, row_count, coverage):
    """(fix_warnings, notes). Fix warnings ask the model to correct the query; notes only
    say how to answer."""
    years = question_years(question)
    if not years:
        return [], []
    if not row_count:
        return [], [f'问题涉及 {"、".join(years)} 年，本次查询没有匹配记录。先用 purpose=explore 核对筛选列的取值格式'
                    '（例如日期存成“2025年12月”还是“2025-12”）；确认无误后如实回答“没有数据”，'
                    '不要改用其他年份、其他对象或全表合计代答。']
    if not outside_requested_years(question, coverage):
        return [], []
    spans = '；'.join(f'「{c["column"]}」{c["from"]} 至 {c["to"]}' for c in coverage[:3])
    return [f'结果的日期列（{spans}）都不包含问题中的 {"、".join(years)} 年。'
            '请核对时间条件；如果该年份确实没有数据，如实说明，不要用其他年份的数据回答。'], []


def zero_result_note(columns, rows):
    """A one-row result whose numbers are all 0 or empty (e.g. COUNT(...) = 0) usually
    means a filter value or date format did not match, not that there is no data."""
    if len(rows or []) != 1 or not isinstance(rows[0], dict):
        return []
    values = [rows[0].get(c) for c in columns or []]
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers or any(v not in (0, None) for v in values):
        return []
    return ['结果全为 0。先用 purpose=explore 核对筛选条件里字段的实际取值和格式（例如日期写法、状态值、等级写法），'
            '确认条件无误后再回答“为 0”；不要连续多次试探，核对一次后就修正或如实回答。']


def null_reasons(rows, spec, labels=None):
    """Split internal "__op_<id>" operand columns off metric rows and explain empty values.

    Returns (clean_rows, reasons) where reasons[i] is a short Chinese reason for row i
    whose value is None (e.g. "预算无记录", "预算为 0"), or None.
    """
    import ast
    operands = {o.get('id'): o for o in (spec or {}).get('operands') or []}
    denominators = set()
    try:
        tree = ast.parse((spec or {}).get('formula') or '', mode='eval')
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                denominators |= {n.id for n in ast.walk(node.right) if isinstance(n, ast.Name)}
    except SyntaxError:
        pass

    def name(op_id):
        o = operands.get(op_id) or {}
        label = (labels or {}).get(o.get('column'))
        if label and label != o.get('column'):
            return label
        return WORDS.get(op_id) or op_id

    clean, reasons = [], []
    for row in rows or []:
        ops = {k[5:]: v for k, v in row.items() if k.startswith('__op_')}
        clean.append({k: v for k, v in row.items() if not k.startswith('__op_')})
        if row.get('value') is not None or not ops:
            reasons.append(None)
            continue
        parts = []
        for op_id, v in ops.items():
            if v is None:
                parts.append(name(op_id) + '无记录')
            elif op_id in denominators and v == 0:
                parts.append(name(op_id) + '为 0')
        reasons.append('、'.join(parts) or None)
    return clean, reasons
