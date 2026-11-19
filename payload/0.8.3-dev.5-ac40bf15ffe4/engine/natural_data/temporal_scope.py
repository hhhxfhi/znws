"""Normalize trusted native clarification answers to calendar-month scopes.

This deliberately does not interpret arbitrary business dates or model prose.
An unsupported/negative answer remains unresolved instead of granting consent.
"""
import html
import re
import unicodedata
from datetime import date


def validate_year_end(host):
    """An unspecified year-end is not the last observed month or last year in the DB."""
    host = host or {}
    text = host.get('user_text', '')
    if not re.search(r'年底|年末', text): return
    if re.search(r'20\d{2}|今年|本年|去年|上年|上个年度|本年度|前年|明年|每年|各年|历年', text): return
    if confirmed_scope(host) is not None: return
    raise ValueError('NEEDS_CLARIFICATION: “年底/年末”尚未指定年份。请用原生提问确认哪一年年底；不得以最新数据月份、上年末或当前有效单位数代替。')


def month_scope(value, *, answer=True):
    text = unicodedata.normalize('NFKC', html.unescape(value or '')).replace('\u00a0', ' ')
    if re.search(r'不(?:是|要|选|看|含|对|行|用|包括|确认|同意)|并非|取消|排除|剔除|扣除|去掉|除了|除去|以外|之外|别(?:用|选|看|按)|^\s*(?:否|非)|非\s*20\d{2}|待定|不确定|或者|还是|或许|可能|如果|假设|\d{1,2}[日号]|20\d{2}[-/]\d{1,2}[-/]\d{1,2}', text):
        return None
    # A user's answer phrased as a question ("2025年吗？") is not consent. The
    # native question itself ends with "？", so only answers are checked.
    if answer and re.search(r'吗|[?？]', text):
        return None
    # Quarters, halves and open-ended ranges are not calendar-month answers this
    # parser can resolve; leave them unresolved instead of widening to a year.
    if re.search(r'(?<![A-Za-z])[QqHh][1-4](?![0-9])|季度|半年|至今|迄今|到现在|到目前|截至|截止|以来', text):
        return None
    # Native option labels commonly write the year once before parentheses.
    text = re.sub(r'(20\d{2})\s*年\s*\(\s*(\d{1,2})\s*月?\s*(到|至|—|–|~|～|-)\s*(\d{1,2})\s*月\s*\)',
                  r'\1年\2月\3\4月', text)
    # ISO month and Chinese month ranges, including an omitted second year.
    pattern = r'(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*月?\s*(?:到|至|—|–|~|～|-)\s*(?:(20\d{2})\s*(?:年|[-/]))?\s*(\d{1,2})\s*月?'
    match = re.search(pattern, text)
    if match:
        y, m, y2, m2 = match.groups(); y, m, y2, m2 = int(y), int(m), int(y2 or y), int(m2)
        if not (1 <= m <= 12 and 1 <= m2 <= 12): return None
        start, end = y * 12 + m - 1, y2 * 12 + m2 - 1
        if not 0 <= end - start < 120: return None
        # Do not silently ignore additional years outside the chosen range.
        if set(map(int, re.findall(r'20\d{2}', text))) - {y, y2}: return None
        return frozenset(f'{n // 12}-{n % 12 + 1:02}' for n in range(start, end + 1))
    months = re.findall(r'(20\d{2})\s*(?:年|[-/])\s*(\d{1,2})\s*月?', text)
    if months:
        if any(not 1 <= int(m) <= 12 for _, m in months): return None
        remainder=re.sub(r'20\d{2}\s*(?:年|[-/])\s*\d{1,2}\s*月?', '', text)
        if re.search(r'\d{1,2}\s*月',remainder): return None
        return frozenset(f'{y}-{int(m):02}' for y, m in months)
    years = set(re.findall(r'(20\d{2})\s*年?', text))
    if len(years) == 1 and not re.search(r'季度|半年|截至|截止|以来|月|日', text):
        year = next(iter(years))
        return frozenset(f'{year}-{m:02}' for m in range(1, 13))
    return None


def confirmed_scope(host):
    records = host.get('clarifications', [])
    scopes = []
    for record in records:
        answer = record.get('answer', '')
        scope = month_scope(answer)
        # A selected "确认" can resolve the native question's explicit range.
        if scope is None and re.fullmatch(r'\s*(?:确认|是|同意|按此范围|按上述范围)\s*[。.!！]?\s*', answer):
            scope = month_scope(record.get('question', ''), answer=False)
        if scope: scopes.append(scope)
    if not records:
        scope = month_scope(host.get('confirmation', ''))
        if scope: scopes.append(scope)
    # Several conflicting answers must not become an arbitrary first/last choice.
    return scopes[0] if scopes and all(s == scopes[0] for s in scopes) else None


def validate_confirmed_periods(periods, host, *, required=False):
    scope = confirmed_scope(host)
    if scope is None:
        if required:
            raise ValueError('NEEDS_CLARIFICATION: 统计年份条件冲突，请通过原生提问确认实际期间（如2025年1月到12月）。')
        return
    supplied = set()
    for period in periods:
        resolved = month_scope(period)
        if resolved: supplied.update(resolved)
    # A named YoY/MoM analysis may legitimately include comparison baselines.
    text = host.get('user_text', '')
    allowed = set(scope)
    if re.search(r'同比|去年|上年|上一年', text):
        allowed.update(f'{int(p[:4])-1}{p[4:]}' for p in scope)
    if '环比' in text:
        first = min(scope); n = int(first[:4]) * 12 + int(first[5:]) - 2
        allowed.add(f'{n // 12}-{n % 12 + 1:02}')
    if not set(scope) <= supplied or not supplied <= allowed:
        raise ValueError('TIME_SCOPE_MISMATCH: 用户已确认期间 '+min(scope)+' 至 '+max(scope)+'；请修订任务和计划的periods后执行，不要再次询问相同期间。')
    if re.search(r'按月|逐月', text) and any(re.fullmatch(r'20\d{2}', p) for p in periods):
        raise ValueError('TIME_SCOPE_MISMATCH: 用户要求逐月结果，请将已确认期间展开为月份，不要重复提问。')


def conflicting_months(text):
    # An explicit span across New Year is one coherent scope.
    remainder = re.sub(r'20\d{2}\s*(?:年|[-/])\s*\d{1,2}\s*月?\s*(?:到|至|—|–|~|～|-)\s*20\d{2}\s*(?:年|[-/])\s*\d{1,2}\s*月?', '', text)
    return (bool(re.search(r'20\d{2}', remainder)) and len(set(re.findall(r'20\d{2}', text))) > 1
            and bool(re.search(r'只看|仅看|只统计|仅统计|限定', text))
            and bool(re.search(r'按月|逐月|趋势', text))
            and not re.search(r'同比|对比|比较|两年', text))


def month_request(host):
    """Only a real answer to the conflicting question can select one of its periods."""
    host = host or {}
    text = host.get('user_text', '')
    if conflicting_months(text):
        scope = confirmed_scope(host)
        if scope is None:
            raise ValueError('NEEDS_CLARIFICATION: 统计年份条件冲突，请用 ask_user_question 让用户选择实际期间；收到选择后按已确认月份查询。')
        return scope
    recent = host.get('recent_questions') or []
    # A short, explicit correction in a new user turn is also authoritative. Do not
    # carry a past conflict into a new question about a different business subject.
    if recent and conflicting_months(recent[-1]) and re.fullmatch(
            r'[\s\d年月从到至以按为准统计只看就选确认请全的间期间使用，,。.!！\-/–—~～]+', text):
        return month_scope(text)


def metric_month_request(host):
    """Resolve explicit monthly metric scopes independently of the business domain.

    Raw SQL can use daily fields, so its separate SQL contract is not widened by
    this parser. Unsupported exclusions/comparison baselines are not guessed.
    """
    host = host or {}
    selected = month_request(host)
    if selected is not None:
        return selected
    text = unicodedata.normalize('NFKC', host.get('user_text', ''))
    if re.search(r'同比|环比|对比|比较|不(?:是|要|选|看|含|用|包括|确认)|并非|取消|排除|剔除|可能|或许|还是|或者|假设|如果|截至|截止|以来|至今', text):
        return None
    monthly = bool(re.search(r'按月|逐月|每月|月度', text))
    recent = (host.get('recent_questions') or [])[-1:]
    correction = bool(re.fullmatch(r'[\s\d年月从到至以按为准统计只看就选确认请全的间期使用改成那呢第季度上下半一二三四QqHh，,。.!！?？\-/–—~～]+', text))
    if not monthly and correction and recent and re.search(r'按月|逐月|每月|月度', recent[0]):
        monthly = True
        if not re.search(r'20\d{2}', text):
            years = set(re.findall(r'20\d{2}', recent[0]))
            if len(years) != 1:
                return None
            text = next(iter(years)) + '年' + re.sub(r'^\s*(?:改为|改成|那|以|按|只看)\s*', '', text)
    if not monthly:
        return None
    quarters = list(re.finditer(r'(20\d{2})\s*年?\s*(?:第?([一二三四1-4])季度|[Qq]([1-4])|([上下])半年|[Hh]([12]))', text))
    if quarters:
        if len(quarters) != 1:
            return None
        match = quarters[0]
        if re.search(r'20\d{2}|\d+\s*月|季度|半年|[QqHh][1-4]', text[:match.start()] + text[match.end():]):
            return None
        year, quarter, qnum, half, hnum = match.groups()
        if quarter or qnum:
            n = int(qnum or quarter) if (qnum or quarter).isdigit() else '一二三四'.index(quarter) + 1
            first, size = (n - 1) * 3 + 1, 3
        else:
            first, size = (1 if half == '上' or hnum == '1' else 7), 6
        return frozenset(f'{year}-{m:02}' for m in range(first, first + size))
    # A second interval/month cannot silently disappear behind the first match.
    span = re.search(r'20\d{2}\s*(?:年|[-/])\s*\d{1,2}\s*月?\s*(?:到|至|—|–|~|～|-)\s*(?:20\d{2}\s*(?:年|[-/]))?\s*\d{1,2}\s*月?', text)
    if span and re.search(r'20\d{2}|\d{1,2}\s*月', text[:span.start()] + text[span.end():]):
        return None
    return month_scope(text, answer=False)


def validate_month_request(host, periods):
    scope = metric_month_request(host)
    if scope is not None and (set(periods) != scope or len(periods) != len(scope)):
        raise ValueError('TIME_SCOPE_MISMATCH: 已确认按月查询 '+min(scope)+' 至 '+max(scope)+'。请逐月列出该范围的 periods，无须再次询问。')


def _month_discovery_probe(sql, source):
    from sqlglot import exp, parse_one, errors
    from .vendor.dialect import sqlglot_name

    try:
        tree = parse_one(sql, read=sqlglot_name(source.get('dialect')))
    except (ValueError, errors.SqlglotError):
        return False
    if (not isinstance(tree, exp.Select) or not tree.args.get('limit') or tree.args.get('group')
            or len(list(tree.find_all(exp.Select))) != 1 or tree.find(exp.AggFunc)):
        return False
    month_fields = {column['name'].casefold() for table in source.get('tables', []) for column in table['columns']
                    if (column.get('time_semantics') or {}).get('grain') == 'month'}
    where = tree.args.get('where')
    return where is None or not any(col.name.casefold() in month_fields for col in where.find_all(exp.Column))


def validate_month_sql(host, sql, source, *, purpose='answer'):
    # A field lookup can establish whether a period choice is useful at all.
    if (purpose == 'explore' and conflicting_months((host or {}).get('user_text', ''))
            and confirmed_scope(host) is None and _month_discovery_probe(sql, source)):
        return
    scope = month_request(host)
    if scope is None:
        return
    from sqlglot import exp, parse_one, errors
    from .vendor.dialect import sqlglot_name
    dialect = sqlglot_name(source.get('dialect'))
    msg = ('TIME_SCOPE_MISMATCH: 已确认 '+min(scope)+' 至 '+max(scope)+
           '。请按同一范围重查。月份字段用对应月份值，日期字段用月初至下一月初的右开区间；按月汇总，无须再询问用户。')
    try:
        tree = parse_one(sql, read=dialect)
    except (ValueError, errors.SqlglotError):
        raise ValueError(msg) from None
    if not isinstance(tree, exp.Select) or sum(1 for _ in tree.find_all(exp.Select)) != 1:
        raise ValueError(msg)
    refs = list(tree.find_all(exp.Table))
    if len(refs) != 1 or tree.args.get('joins') or not tree.args.get('where') or tree.args.get('offset'):
        raise ValueError(msg)
    limit = tree.args.get('limit')
    if limit and not (isinstance(limit.expression, exp.Literal) and limit.expression.is_int
                      and int(limit.expression.this) >= len(scope)):
        raise ValueError(msg)
    ref = refs[0]
    names = [str(t['name']).casefold() for t in source.get('tables', [])]
    matched = [i for i, name in enumerate(names) if name == '.'.join(p.name.casefold() for p in ref.parts)
               or (len(ref.parts) == 1 and name.split('.')[-1] == ref.name.casefold())]
    if len(matched) != 1:
        raise ValueError(msg)
    fields = {c['name'].casefold() for c in source['tables'][matched[0]]['columns']
              if (c.get('time_semantics') or {}).get('grain') == 'month'}
    def parts(node):
        if isinstance(node, exp.Paren):
            yield from parts(node.this)
        elif isinstance(node, exp.And):
            yield from parts(node.this)
            yield from parts(node.expression)
        else:
            yield node
    restrictions = [p for p in parts(tree.args['where'].this)
                    if any(c.name.casefold() in fields for c in p.find_all(exp.Column))]
    def month(node):
        if not isinstance(node, exp.Literal):
            return None
        m = re.fullmatch(r'(20\d{2})[-/]?(0[1-9]|1[0-2])', str(node.this))
        return '-'.join(m.groups()) if m else None
    if len(restrictions) == 1:
        predicate = restrictions[0]
        if isinstance(predicate.this, exp.Column) and predicate.this.name.casefold() in fields:
            if isinstance(predicate, exp.In) and {month(v) for v in predicate.expressions} == scope:
                return
            if isinstance(predicate, exp.Between):
                lo, hi = month(predicate.args['low']), month(predicate.args['high'])
                if lo and hi and month_scope(lo+'至'+hi) == scope:
                    return
    from .monthly_sql_scope import _bounds, _month_field, without_redundant_month_limit

    # An authorized physical date needs no monthly semantic registration to
    # prove an exact, consecutive month window.
    tree = without_redundant_month_limit(tree, source, scope, dialect)
    group = tree.args.get('group')
    first, last = min(scope), max(scope)
    if group and month_scope(first+'至'+last) == scope:
        start = date.fromisoformat(first+'-01')
        tail = date.fromisoformat(last+'-01')
        stop = date(tail.year + (tail.month == 12), tail.month % 12 + 1, 1)
        for index, projection in enumerate(tree.selects, 1):
            field = _month_field(projection.unalias(), dialect)
            grouped = any(item == projection.unalias()
                          or isinstance(item, exp.Column) and item.name == projection.alias
                          or isinstance(item, exp.Literal) and item.is_int and int(item.this) == index
                          for item in group.expressions)
            if field and grouped and _bounds(tree, source, field, dialect, midnight_timestamp=True) == (start, stop, False):
                return
    raise ValueError(msg)


_CALENDAR_QUARTER = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*(?:年\s*)?(?:第?\s*([一二三四1-4])\s*季度|[Qq]\s*([1-4])(?![\dA-Za-z]))')
_OTHER_TIME = re.compile(r'(?<!\d)(?:19|20)\d{2}(?!\d)|\d{1,2}\s*月|[Qq]\s*[1-4]\b|季度|同期|半年|同比|环比|'
                         r'今年|本年|去年|上年|前年|明年|上月|本月|下月|截至|截止|至今|以来|年末|季末|季初')
_FISCAL_CALENDAR = re.compile(r'财年|财[政务](?:年度?|季度)|会计(?:年度?|季度)|财季|非自然季度|\bFY\s*\d{2,4}', re.I)
_MULTIPLE_TASKS = re.compile(r'另外(?:也|再|顺便)?(?:查|统计|给出|看)|另查|再查|顺便(?:查|统计|看)|分别(?:查|统计)|同时(?:查|比较)|并(?:再)?查|预测')
_MIDNIGHT = re.compile(r'\d{4}-\d{2}-\d{2}(?:[ T]00:00:00(?:\.0+)?)?')
_STORED_DAY = re.compile(r'([0-9]{4})([-/])([0-9]{2})\2([0-9]{2})')


def _calendar_quarter(host):
    text = unicodedata.normalize('NFKC', host.get('user_text') or '')
    matches = list(_CALENDAR_QUARTER.finditer(text))
    if len(matches) != 1 or _FISCAL_CALENDAR.search(text) or _MULTIPLE_TASKS.search(text):
        return None
    match, = matches
    before, after = text[:match.start()], text[match.end():]
    if (_OTHER_TIME.search(before + ' ' + after)
            or re.search(r'(?:不(?:查|统计|看|含|包括|按|要|是)|并非|排除|剔除)\s*$', before)
            or re.match(r'\s*(?:之前|以前|之后|以后|以外|除外|初|末|底)', after)):
        return None
    y, chinese, numeric = match.groups()
    year = int(y)
    number = int(numeric or chinese) if (numeric or chinese).isdigit() else '一二三四'.index(chinese) + 1
    start = date(year, number * 3 - 2, 1)
    stop = date(year + (number == 4), number % 4 * 3 + 1, 1)
    punctuation = r'[，,。；;！!？?\n]'
    return start, stop, re.split(punctuation, after, maxsplit=1)[0], re.split(punctuation, before)[-1][-38:]


def _quarter_bound(node, dialect, parameters):
    from sqlglot import exp
    while isinstance(node, exp.Paren):
        node = node.this
    if (dialect == 'postgres' and isinstance(node, exp.Cast)
            and node.to.this in (exp.DataType.Type.DATE, exp.DataType.Type.TIMESTAMP)):
        node = node.this
    if isinstance(node, (exp.Placeholder, exp.Parameter)):
        value = parameters.get(str(node.this))
    else:
        value = node.this if isinstance(node, exp.Literal) and node.is_string else None
    if isinstance(value, str) and _MIDNIGHT.fullmatch(value):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return None


def _quarter_axes(root, source, dialect, parameters):
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope
    from .request_scope import _and_atoms
    from .snapshot_scope import _physical_table

    key = str.casefold if dialect == 'sqlite' else str
    declared_axes = {}
    for table in source.get('tables', []):
        for field in table['columns']:
            declared = field.get('time_semantics') or {}
            if declared.get('grain') in ('date', 'datetime') and not _FISCAL_CALENDAR.search(declared.get('description') or ''):
                label = re.sub(r'(?:日期时间|时间戳|日期|时间)$', '', declared.get('name') or '')
                declared_axes[(table['name'], key(field['name']))] = label
    referenced, windows, visited = set(), [], set()

    def visit(scope):
        tree = scope.expression
        joins = tree.args.get('joins') or []
        if (id(scope) in visited or not isinstance(tree, exp.Select)
                or any(join.side in ('RIGHT', 'FULL') for join in joins)):
            return
        visited.add(id(scope))
        selected = scope.selected_sources
        optional = {key(join.this.alias_or_name) for join in joins if join.side == 'LEFT'}
        fields = {}
        for alias, (_, origin) in selected.items():
            if key(alias) in optional:
                continue
            if isinstance(origin, Scope):
                visit(origin)
                continue
            table = _physical_table(origin, source, dialect)
            for identity in declared_axes:
                if identity[0] == table:
                    referenced.add(identity)
                    fields[(key(alias), identity[1])] = identity
        where = tree.args.get('where')
        bounds = {}
        for atom in _and_atoms(where.this if where is not None else None):
            if not isinstance(atom, (exp.GTE, exp.LT, exp.LTE, exp.GT)):
                continue
            column, boundary = atom.this, atom.expression
            if isinstance(atom, (exp.LTE, exp.GT)):
                column, boundary = boundary, column
            if not isinstance(column, exp.Column) or column.db or column.catalog:
                continue
            if not column.table and len(selected) != 1:
                continue
            alias, field = key(column.table or next(iter(selected), '')), key(column.name)
            if (identity := fields.get((alias, field))) and (day := _quarter_bound(boundary, dialect, parameters)):
                lower, upper = bounds.setdefault((alias, identity), ([], []))
                (lower if isinstance(atom, (exp.GTE, exp.LTE)) else upper).append(day)
        windows.extend((identity, max(lo), min(hi)) for (_, identity), (lo, hi) in bounds.items() if lo and hi)

    visit(root)
    return declared_axes, referenced, windows


def validate_quarter_sql(host, sql, source, parameters=None):
    """Reject a provably wrong window on an authorized, published quarter axis."""
    request = _calendar_quarter(host or {})
    if not request or not (source.get('business_project') or {}).get('version'):
        return
    from sqlglot import parse_one
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from sqlglot.optimizer.scope import build_scope
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return
    try:
        root = build_scope(normalize_identifiers(parse_one(sql, read=dialect), dialect=dialect))
        if root is None:
            return
        bound = {row['name']: row.get('value') for row in parameters or [] if isinstance(row, dict) and 'name' in row}
        axes, referenced, windows = _quarter_axes(root, source, dialect, bound)
    except SqlglotError:
        return
    first, following, after, before = request
    # An explicitly named event axis outranks another date on a joined table.
    # When more than one period or window remains plausible, leave it unverified.
    named = [(after.index(label), identity) for identity, label in axes.items() if label and label in after]
    if not named:
        named = [(len(before) - before.rindex(label) - len(label), identity)
                 for identity, label in axes.items() if label and label in before]
    best = min((position for position, _ in named), default=None)
    selected = {identity for position, identity in named if position == best} if named else referenced
    if len(selected) != 1 or not selected <= referenced:
        return
    actual = {(lo, hi) for identity, lo, hi in windows if identity in selected}
    if len(actual) != 1:
        return
    start, stop = next(iter(actual))
    if (start, stop) != (first, following):
        raise ValueError(f'TIME_SCOPE_MISMATCH: 原问要求完整自然季度 {first}（含）至 {following}（不含），'
                         f'当前查询为 {start}（含）至 {stop}（不含）。'
                         '请保留已授权业务条件与原时间字段，按原问日期范围重新执行。')


def _text_quarter_axis(sql, source, request, parameters, *, preserve_identifiers=False):
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from sqlglot.optimizer.scope import build_scope, Scope
    from .request_scope import _and_atoms
    from .snapshot_scope import _physical_table

    try:
        tree = parse_one(sql, read='sqlite')
        if not preserve_identifiers:
            normalize_identifiers(tree, dialect='sqlite')
        root = build_scope(tree)
    except SqlglotError:
        return None
    if root is None:
        return None
    tables = {t['name']: {f['name'].casefold(): f for f in t['columns']} for t in source['tables']}
    first, following, _, _ = request
    identities = {}

    def visit(scope):
        select = scope.expression
        joins = select.args.get('joins') or []
        if not isinstance(select, exp.Select) or any(join.side in ('RIGHT', 'FULL') for join in joins):
            return
        optional = {join.this.alias_or_name.casefold() for join in joins if join.side == 'LEFT'}
        selected = scope.selected_sources
        physical = {}
        for alias, (_, origin) in selected.items():
            if alias.casefold() in optional:
                continue
            if isinstance(origin, Scope):
                visit(origin)
            elif name := _physical_table(origin, source, 'sqlite'):
                physical[alias.casefold()] = name
        if not (where := select.args.get('where')):
            return
        candidates = {}
        for atom in _and_atoms(where.this):
            if not isinstance(atom, (exp.GTE, exp.LT)) or not isinstance(atom.this, exp.Column):
                continue
            column = atom.this
            if column.db or column.catalog or not column.table and len(selected) != 1:
                continue
            alias = (column.table or next(iter(selected), '')).casefold()
            table = physical.get(alias)
            field = tables.get(table, {}).get(column.name.casefold()) or {}
            if (field.get('type') or '').upper() not in ('TEXT', 'VARCHAR', 'CHAR', 'CLOB'):
                continue
            if _FISCAL_CALENDAR.search((field.get('time_semantics') or {}).get('description') or ''):
                continue
            bound = _quarter_bound(atom.expression, 'sqlite', parameters)
            if bound:
                identity = (alias, table, field['name'])
                candidates.setdefault(identity, []).append((atom, bound))
        for (_, table, field), bounds in candidates.items():
            if {(type(atom), bound) for atom, bound in bounds} == {(exp.GTE, first), (exp.LT, following)}:
                identities.setdefault((table, field), []).append((select, bounds))

    visit(root)
    if len(identities) == 1:
        identity, matches = next(iter(identities.items()))
        bounds = matches[0][1] if len(matches) == 1 and matches[0][0] is tree else []
        return identity, tree, bounds
    return None


def _normalize_text_quarter(tree, bounds, parameters):
    """Normalize only the two matched comparisons in one simple WHERE."""
    from sqlglot import exp

    if len(bounds) != 2 or tree.find(exp.Subquery, exp.CTE, exp.SetOperation, exp.Exists):
        return None
    where = tree.args['where']
    field = bounds[0][0].this.name.casefold()
    if (where.find(exp.Or) or sum(column.name.casefold() == field for column in where.find_all(exp.Column)) != 2
            or any(join.side or join.kind not in ('', 'INNER') or join.method
                   for join in (tree.args.get('joins') or []))):
        return None
    for atom, day in bounds:
        boundary = atom.expression.unnest()
        value = (parameters.get(str(boundary.this)) if isinstance(boundary, (exp.Placeholder, exp.Parameter))
                 else boundary.this)
        # date() emits YYYY-MM-DD. Other valid literal encodings such as
        # midnight timestamps need a different comparison to preserve the day.
        if value != day.isoformat():
            return None
    for atom, _ in bounds:
        atom.set('this', exp.Date(this=exp.Replace(this=atom.this.copy(),
            expression=exp.Literal.string('/'), replacement=exp.Literal.string('-'))))
    return tree.sql(dialect='sqlite')


def _probe_dates(engine, ctx, source, table, column):
    # Never let sample text authorize a new field. The exact physical name is
    # independently resolved in both the candidate and the stored read.
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import build_scope, Scope
    from .catalog_index import fingerprint
    from .read_limits import execution_basis
    from .snapshot_scope import _physical_table

    host = ctx.host_evidence or {}
    refs = host.get('current_exploration_refs')
    if not isinstance(refs, list) or not refs or not host.get('user_ref') or not ctx.session_id:
        return []
    if getattr(engine, 'policy', None):
        engine.policy.require_history(ctx.project)
    current = fingerprint(source)
    samples = []
    for ref in refs[-4:]:
        if not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{32}', ref):
            continue
        try:
            result = engine.raw(ctx.project, ref)
        except PermissionError:
            continue
        lineage, receipt = result.get('result_lineage') or {}, result.get('receipt') or {}
        if (result['status'] != 'succeeded' or receipt.get('read_only') is not True
                or result.get('source_id') != source['id'] or receipt.get('source_id') != source['id']
                or lineage.get('source_id') != source['id'] or lineage.get('purpose') not in ('explore', 'probe')
                or lineage.get('session_id') != ctx.session_id or lineage.get('request_ref') != host['user_ref']
                or lineage.get('config_fingerprint') != current or result.get('business_project') != source.get('business_project')):
            continue
        sql = result.get('sql')
        basis = result.get('execution_basis') or {}
        if (policy := basis.get('policy_sql')) and sql and execution_basis(policy, sql) == basis:
            sql = policy
        if not sql:
            continue
        try:
            scope = build_scope(parse_one(sql, read='sqlite'))
        except SqlglotError:
            continue
        if scope is None:
            continue
        for index, output in enumerate(result.get('columns') or []):
            field_index, projection_scope = index, scope
            # Follow direct projected fields through a simple derived table;
            # an alias, computed expression or other table name proves nothing.
            while isinstance(projection_scope.expression, exp.Select):
                node = projection_scope.expression
                if node.args.get('joins') or len(projection_scope.selected_sources) != 1 or field_index >= len(node.expressions):
                    break
                alias, (_, upstream) = next(iter(projection_scope.selected_sources.items()))
                field = node.expressions[field_index].unalias().unnest()
                if (not isinstance(field, exp.Column) or field.db or field.catalog
                        or field.table and field.table.casefold() != alias.casefold()):
                    break
                if not isinstance(upstream, Scope):
                    if _physical_table(upstream, source, 'sqlite') == table and field.name.casefold() == column.casefold():
                        samples.extend(row.get(output) for row in (result.get('rows') or [])[:32])
                    break
                if upstream.outer_columns:
                    break
                matches = [i for i, item in enumerate(upstream.expression.expressions)
                           if item.alias_or_name.casefold() == field.name.casefold()]
                if len(matches) != 1:
                    break
                field_index, projection_scope = matches[0], upstream
    return samples


def _fresh_dates(engine, ctx, source, table, column):
    """Sample one exact SQLite field, with a small row and execution budget."""
    from pathlib import Path
    import sqlite3
    from time import monotonic
    from sqlglot import exp
    from .sqlite_connection import connect_readonly

    current = engine.source(ctx.project, source['id'])
    if (current.get('connection_revision') != source.get('connection_revision')
            or current.get('business_project') != source.get('business_project')
            or current.get('dialect') != 'sqlite' or not current.get('path')
            or not any(t['name'] == table and any(f['name'] == column for f in t['columns']) for t in current['tables'])):
        return []
    field = exp.column(column, quoted=True).sql(dialect='sqlite')
    relation = exp.table_(table, quoted=True).sql(dialect='sqlite')
    def authorize(action, target, name, database, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_OK if (action == sqlite3.SQLITE_READ and database == 'main'
            and target == table and name == column and trigger is None) else sqlite3.SQLITE_DENY
    try:
        conn = connect_readonly(Path(current['path']), timeout=.04)
        try:
            start = monotonic()
            conn.set_authorizer(authorize)
            conn.set_progress_handler(lambda: int(monotonic() - start > .04), 100)
            return [row[0] for row in conn.execute(f'SELECT {field} FROM main.{relation} WHERE {field} IS NOT NULL LIMIT 24')]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def normalize_sqlite_year_dates(engine, ctx, sql, source):
    """Use observed calendar-date storage in a SQLite year predicate."""
    if source.get('dialect') != 'sqlite' or 'datetime' not in sql.lower():
        return sql
    text = unicodedata.normalize('NFKC', (ctx.host_evidence or {}).get('user_text') or '')
    years = set(re.findall(r'(?<!\d)((?:19|20)\d{2})\s*年', text))
    if len(years) != 1 or _FISCAL_CALENDAR.search(text) or re.search(r'格式|标准日期|字符串|严格', text):
        return sql

    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import build_scope
    from .snapshot_scope import _physical_table
    try:
        tree = parse_one(sql, read='sqlite')
        scope = build_scope(tree)
    except SqlglotError:
        return sql
    if not scope or not isinstance(tree, exp.Select) or not (where := tree.args.get('where')):
        return sql
    atoms = where.this.flatten() if isinstance(where.this, exp.And) else [where.this]
    for atom in atoms:
        if not isinstance(atom, exp.EQ) or not isinstance(atom.this, exp.TimeToStr):
            continue
        target, format_string = atom.expression, atom.this.args.get('format')
        if not isinstance(target, exp.Literal) or not target.is_string or target.this not in years:
            continue
        if not isinstance(format_string, exp.Literal) or format_string.this != '%Y':
            continue
        formatter = atom.this.this
        cast = formatter.this if isinstance(formatter, exp.TsOrDsToTimestamp) else formatter
        if not isinstance(cast, exp.Anonymous) or cast.name.lower() != 'datetime' or len(cast.expressions) != 1:
            continue
        column, = cast.expressions
        if not isinstance(column, exp.Column) or column.db or column.catalog:
            continue
        sources = scope.selected_sources
        alias = column.table or (next(iter(sources)) if len(sources) == 1 else '')
        origin = next((ref for name, (_, ref) in sources.items() if name.casefold() == alias.casefold()), None)
        table = _physical_table(origin, source, 'sqlite')
        field = next((f['name'] for t in source['tables'] if t['name'] == table for f in t['columns']
                      if f['name'].casefold() == column.name.casefold()), None)
        if not field:
            continue
        samples = _fresh_dates(engine, ctx, source, table, field)
        if not any(isinstance(value, str) and (match := _STORED_DAY.fullmatch(value)) and match[2] == '/'
                   for value in samples):
            continue
        cleaned = exp.Replace(this=column.copy(), expression=exp.Literal.string('/'), replacement=exp.Literal.string('-'))
        calendar = exp.Anonymous(this='date', expressions=[cleaned.copy(), exp.Literal.string('+0 days')])
        pattern = '[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]'
        valid = exp.and_(exp.Glob(this=column.copy(), expression=exp.Literal.string(pattern)),
                         exp.EQ(this=calendar, expression=cleaned.copy()))
        cast.set('expressions', [exp.Case(ifs=[exp.If(this=valid, true=cleaned)], default=column.copy())])
        return tree.sql(dialect='sqlite')
    return sql


def validate_quarter_text_dates(engine, ctx, sql, source, parameters=None, *, result=None, recover=False):
    """Verify ISO bounds and recover a proved date-format mismatch when requested."""
    if source.get('dialect') != 'sqlite':
        return sql
    refs = (ctx.host_evidence or {}).get('current_exploration_refs')
    if result is None and not refs or result is not None and result.get('status') != 'succeeded':
        return sql
    request = _calendar_quarter(ctx.host_evidence or {})
    if not request:
        return sql
    bound = {row['name']: row.get('value') for row in parameters or [] if isinstance(row, dict) and 'name' in row}
    axis = _text_quarter_axis(sql, source, request, bound, preserve_identifiers=recover)
    if not axis:
        return sql
    (table, column), tree, comparisons = axis
    if result is None:
        values = _probe_dates(engine, ctx, source, table, column)
    else:
        if result.get('reason_code') != 'NO_MATCH':
            from sqlglot import exp
            if (result.get('row_count') != 1 or not isinstance(tree, exp.Select)
                    or len(tree.expressions) != 1 or not isinstance(tree.expressions[0].unalias(), exp.Count)):
                return sql
            stored = engine.raw(ctx.project, result['result_ref'])
            if list(stored['rows'][0].values()) != [0]:
                return sql
        values = _fresh_dates(engine, ctx, source, table, column)
    first, following, _, _ = request
    formats, unrecognized = set(), False
    for value in values:
        if value is None:
            continue
        match = _STORED_DAY.fullmatch(value) if isinstance(value, str) else None
        try:
            if match:
                year, separator, month, day = match.groups()
                if first <= date(int(year), int(month), int(day)) < following:
                    formats.add(separator)
                continue
        except ValueError:
            pass
        unrecognized = True
    if '/' not in formats:
        return sql
    # Normalizing both supported formats preserves a mixed column; slash
    # bounds alone would lose the ISO dates in that same quarter.
    if recover and result is None and not unrecognized:
        if normalized := _normalize_text_quarter(tree, comparisons, bound):
            return normalized
    sample = '同时有 YYYY/MM/DD 和 YYYY-MM-DD' if '-' in formats else '存在 YYYY/MM/DD'
    unknown = '。还观察到无法识别的值，需先核实其日期含义' if unrecognized else ''
    raise ValueError(f'TIME_SCOPE_MISMATCH: 已授权日期字段「{column}」在原问期间{sample}文本。'
                     f'按原字段直接比较 ISO 文本会漏掉记录。请保留同一字段、关联与业务条件，'
                     f'仅把日期条件改成 date(replace(日期字段, \'/\', \'-\')) >= \'{first}\' '
                     f'且 < \'{following}\'{unknown}。')
