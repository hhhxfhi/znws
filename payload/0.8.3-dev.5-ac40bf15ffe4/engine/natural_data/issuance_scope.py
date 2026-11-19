"""Bind a confirmed annual issuance count to source metadata and the requested scope."""
import re
from datetime import date
from sqlglot import exp
from .temporal_context import temporal_context


def validate_issuance_scope(tree, tables, host, metric=None, periods=None, parameters=None):
    message = ('EVENT_SCOPE_MISMATCH: 已确认按当年发证单位统计。请按所选年份的发证日期使用完整期间，'
               '沿用用户指定地区，并按目录中的单位标识去重；不能改数发证记录或全部历史。')
    text = host.get('user_text', '')
    years = set(re.findall(r'20\d{2}', text))
    if len(years) > 1:
        raise ValueError('NEEDS_CLARIFICATION: 请确认发证统计年份。')
    current = int(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'][:4])
    year = int(next(iter(years))) if years else current - (2 if '前年' in text else 1 if re.search('去年|上年', text) else 0)
    start, end = date(year, 1, 1), date(year + 1, 1, 1)
    if len(tables) != 1:
        raise ValueError(message)
    fields = {c['name']: c for c in tables[0].get('columns', [])}
    issue_dates = {name for name, col in fields.items()
                   if re.fullmatch(r'(?:证书)?(?:发证|签发|颁证|颁发)(?:日期|时间)', col.get('label') or '')}
    identities = [name for label in (r'单位(?:id|ID|编号|标识)', r'统一社会信用代码', r'单位名称')
                  for name, col in fields.items() if re.fullmatch(label, col.get('label') or '')]
    if not issue_dates or not identities:
        raise ValueError(message + ' 当前目录还需声明发证日期和单位标识字段。')
    identity = identities[0]
    explicit_places = re.sub(r'全省|本省|该省', '', text)
    regions = [(v, {'所在区域', '所在省份', '省份', '省'}) for v in re.findall(r'黑龙江省|[\u4e00-\u9fff]{2}省', explicit_places)]
    from .historical_qualification import CITIES
    regions.extend((city + '市', {'所在城市', '城市', '地市', '市'}) for city in CITIES.findall(text))
    region_fields = []
    for value, labels in regions:
        candidates = {name for name, col in fields.items() if col.get('label') in labels}
        if not candidates:
            raise ValueError(message + ' 当前目录还需声明地区字段。')
        region_fields.append((value, candidates))

    def quoted(name):
        return '"' + name.replace('"', '""') + '"'

    # Supply the known field names and exact bounds so a small model can repair
    # a rejected query without guessing a broader province or date condition.
    date_column = quoted(sorted(issue_dates)[0])
    guidance = [f'{date_column} >= \'{start.isoformat()}\' AND {date_column} < \'{end.isoformat()}\'']
    for value, names in region_fields:
        guidance.append(quoted(sorted(names)[0]) + " = '" + value.replace("'", "''") + "'")
    message += ' 单位聚合使用 COUNT(DISTINCT ' + quoted(identity) + ')，范围条件使用 ' + ' AND '.join(guidance) + '。请据此修订查询后重试，无须重复确认。'

    if metric:
        operands = metric.get('operands') or []
        if (metric.get('time_mode') != 'period' or list(periods or []) != [str(year)]
                or len(operands) != 1 or metric.get('formula') != operands[0]['id']):
            raise ValueError(message)
        operand = operands[0]
        if (operand.get('aggregation') != 'distinct' or operand.get('column') != identity
                or operand.get('time_column') not in issue_dates):
            raise ValueError(message)
        for value, columns in region_fields:
            if not any(f['column'] in columns and f['op'] in ('eq', 'in') and f['values'] == [value]
                       for f in operand.get('filters', [])):
                raise ValueError(message)
        return

    refs = list(tree.find_all(exp.Table))
    if (not isinstance(tree, exp.Select) or len(refs) != 1
            or sum(1 for _ in tree.find_all(exp.Select)) != 1 or tree.args.get('joins')
            or tree.args.get('group') or tree.args.get('having') or len(tree.expressions) != 1):
        raise ValueError(message)
    count = tree.expressions[0]
    if isinstance(count, exp.Alias): count = count.this
    if not isinstance(count, exp.Count) or not isinstance(count.this, exp.Distinct) or len(count.this.expressions) != 1:
        raise ValueError(message)

    def unit_column(node):
        if isinstance(node, (exp.Paren, exp.Cast)): return unit_column(node.this)
        if isinstance(node, exp.Coalesce):
            return unit_column(node.this) and all(
                isinstance(c, exp.Column) and c.name in identities for c in node.expressions)
        return isinstance(node, exp.Column) and node.name == identity and (not node.table or node.table == refs[0].alias_or_name)

    if not unit_column(count.this.expressions[0]):
        raise ValueError(message)
    bindings = {p['name']: p['value'] for p in parameters or []}

    def literal(node):
        if isinstance(node, exp.Cast): return literal(node.this)
        if isinstance(node, exp.Placeholder): return str(bindings.get(node.name, ''))
        return str(node.this) if isinstance(node, exp.Literal) else ''

    def as_date(node):
        value = literal(node)
        try:
            if re.fullmatch(r'\d{8}', value): return date(int(value[:4]), int(value[4:6]), int(value[6:]))
            return date.fromisoformat(value)
        except ValueError:
            return None

    def conjuncts(node):
        if isinstance(node, exp.Paren): yield from conjuncts(node.this)
        elif isinstance(node, exp.And):
            yield from conjuncts(node.this)
            yield from conjuncts(node.expression)
        else: yield node

    where = tree.args.get('where')
    predicates = list(conjuncts(where.this)) if where else []
    bounds = {}
    geography = set()
    for node in predicates:
        columns = list(node.find_all(exp.Column))
        if not columns: continue
        if any(c.table and c.table != refs[0].alias_or_name for c in columns):
            raise ValueError(message)
        involved_dates = {c.name for c in columns} & issue_dates
        if involved_dates:
            if not isinstance(node.this, exp.Column) or len(columns) != 1:
                raise ValueError(message)
            col = node.this.name
            bound = bounds.setdefault(col, set())
            if isinstance(node, exp.GTE) and as_date(node.expression) == start: bound.add('start')
            elif isinstance(node, exp.LT) and as_date(node.expression) == end: bound.add('end')
            else: raise ValueError(message + ' 请使用发证日期 >= 年初 AND 发证日期 < 次年年初。')
        for value, names in region_fields:
            if not {c.name for c in columns} & names: continue
            values = node.expressions if isinstance(node, exp.In) else [node.expression]
            if (not isinstance(node, (exp.EQ, exp.In)) or not isinstance(node.this, exp.Column)
                    or node.this.name not in names or [literal(v) for v in values] != [value]):
                raise ValueError(message)
            geography.add(value)
    if not any(b == {'start', 'end'} for b in bounds.values()) or geography != {v for v, _ in region_fields}:
        raise ValueError(message)
