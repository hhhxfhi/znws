"""Keep missing fiscal amounts out of annual forecast histories."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import re
from typing import NamedTuple
import unicodedata

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name


_FISCAL_AMOUNT = re.compile(r'预算|(?:投资|投入|拨款)(?:总)?(?:金额|额)|经费(?:实际)?(?:支出|拨付)|'
                            r'财政(?:资金)?(?:实际)?(?:拨付|支出|投入)|实拨(?:金额)?')
_EXPLICIT_YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*年')
_SUBANNUAL_AMOUNT = re.compile(r'环比|截至|截止|半年|季度|\d+\s*月|逐月|每月|按月')
_RELATIVE_AMOUNT_YEAR = re.compile(r'(?P<current>今年|本年(?:度)?|本财年)|'
                                   r'(?P<previous>去年|上(?:一)?年(?:度)?|上(?:一)?个?财年)|'
                                   r'(?P<earlier>前年|上上年(?:度)?)')
_ANNUAL_COMPARISON = re.compile(r'比较|对比|相比|相较|相差|差多少|变动|变化|增减|高还是低|'
                                r'(?:比|较(?:于)?)\s*(?:去年|前年|上(?:一)?年(?:度)?|上(?:一)?个?财年)')
_ANNUAL_FALLBACK = re.compile(r'(?:无观测|无数据|没有(?:数据|记录|观测)|查不到)[^。；;\n]{0,16}(?:再|改|才|则|就)\s*(?:查|核实|查询)')
_SKIP_COMPARISON = re.compile(r'(?:不(?:要|用|必|需|做|看|再)?|无需|无须|取消)\s*(?:同比|比较|对比|相比|相较|比)')
_YEAR_FIELD = re.compile(r'(?:[^\d%（）()]{0,16})?(?:年度|年份|财年)$')
_BUDGET_AMOUNT = re.compile(r'[^%（）()]{0,16}预算(?:总额|总金额|金额|数额)$')
_BUDGET_OTHER = re.compile(r'实际|已使用|已支出|已拨付|执行|剩余|余额|差额|未使用|占比|同比|环比')
_CURRENCY_UNIT = re.compile(r'(?:人民币)?(?:万|亿)?元')
_LEGACY_CURRENCY = re.compile(r'(?<!非)单位(?:为|是|[：:])\s*((?:人民币)?(?:万|亿)?元)(?=[。；;，,\s]|$)')
_WRAPPERS = (exp.Paren, exp.Cast, exp.TryCast, exp.Round, exp.Filter, exp.Window, exp.Neg, exp.Abs,
             exp.Nullif, exp.Trim, exp.Replace, exp.RegexpReplace)
_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod)


class Projection(NamedTuple):
    summed: bool = False
    defaulted: bool = False
    null_filled_sum: bool = False
    source_value: bool = False


def _is_zero(node):
    if node is None:
        return False
    node = node.unnest()
    while isinstance(node, exp.Neg) or isinstance(node, exp.Cast) and node.to.this in exp.DataType.NUMERIC_TYPES:
        node = node.this.unnest()
    return isinstance(node, exp.Literal) and node.is_number and Decimal(node.this) == 0


def _decimal_column(node):
    node = node.unnest() if node is not None else None
    if isinstance(node, exp.Cast) and node.to.this == exp.DataType.Type.DECIMAL and not node.to.expressions:
        node = node.this.unnest()
    return node if isinstance(node, exp.Column) else None


def _null_case(node):
    if not isinstance(node, exp.Case) or node.this or len(node.args['ifs']) != 1:
        return None
    branch = node.args['ifs'][0]
    predicate = branch.this.unnest()
    when_null, when_present = branch.args.get('true'), node.args.get('default')
    if isinstance(predicate, exp.Not):
        predicate = predicate.this.unnest()
        when_null, when_present = when_present, when_null
    if isinstance(predicate, exp.Is) and isinstance(predicate.expression, exp.Null) and when_present is not None:
        return predicate.this.unnest(), when_null, when_present.unnest()
    return None


def _zero_default_input(node):
    if isinstance(node, exp.Coalesce) and len(node.expressions) == 1 and _is_zero(node.expressions[0]):
        return node.this
    if (parts := _null_case(node)) is not None:
        checked, when_null, when_present = parts
        if _is_zero(when_null) and checked in (when_present, _decimal_column(when_present)):
            return when_present
    return None


def _fills_null_sum(tree, dialect):
    """Follow explicit zero defaults along the value paths of returned sums."""
    normalize_identifiers(tree, dialect=sqlglot_name(dialect))
    key = str.casefold if dialect == 'sqlite' else str
    projections = {}
    scalar_projections = {}

    def combined(entries):
        return Projection(any(entry.summed for entry in entries), any(entry.defaulted for entry in entries),
                          any(entry.null_filled_sum for entry in entries), any(entry.source_value for entry in entries))

    def flags(node, inputs):
        while isinstance(node, _WRAPPERS):
            node = node.this
        if isinstance(node, exp.Sum):
            value = flags(node.this, inputs)
            return value._replace(summed=True, null_filled_sum=value.defaulted or value.null_filled_sum)
        if isinstance(node, exp.Subquery):
            return scalar_projections.get(id(node.this), Projection())
        if isinstance(node, exp.Column) and not (node.db or node.catalog):
            source = inputs.get(key(node.table)) if node.table else (
                next(iter(inputs.values())) if len(inputs) == 1 else None)
            return (source or {}).get(key(node.name), Projection(source_value=True))
        if isinstance(node, (exp.Case, exp.If, exp.Coalesce)):
            if isinstance(node, exp.Case):
                branches = [branch.args.get('true') for branch in node.args.get('ifs', [])] + [node.args.get('default')]
            elif isinstance(node, exp.If):
                branches = [node.args.get('true'), node.args.get('false')]
            else:
                branches = [node.this, *node.expressions]
            value = combined([flags(branch, inputs) for branch in branches])
            # Predicates and numeric indicators do not supply the amount. In a
            # pivot, an ELSE 0 alongside a real field can hide a missing period.
            zero = value.source_value and any(_is_zero(branch) for branch in branches)
            return value._replace(defaulted=value.defaulted or zero,
                                  null_filled_sum=value.null_filled_sum or (value.summed and zero))
        if isinstance(node, _ARITHMETIC):
            return combined([flags(node.this, inputs), flags(node.expression, inputs)])
        if isinstance(node, exp.Distinct):
            return combined([flags(item, inputs) for item in node.expressions])
        return Projection(source_value=isinstance(node, exp.Column))

    for scope in traverse_scope(tree):
        if isinstance(scope.expression, exp.SetOperation):
            branches = [list(projections.get(id(branch), {}).items()) for branch in scope.union_scopes]
            output = {}
            for columns in zip(*branches):
                name, _ = columns[0]
                output[name] = combined([entry for _, entry in columns])
        elif isinstance(scope.expression, exp.Select):
            inputs = {key(name): projections.get(id(ref)) if isinstance(ref, Scope) else None
                      for name, (_, ref) in scope.selected_sources.items()}
            output = {}
            items = scope.expression.selects
            names = scope.outer_columns or [item.alias_or_name for item in items]
            for name, item in zip(names, items):
                if item.is_star:
                    ref = inputs.get(key(item.table)) if isinstance(item, exp.Column) else (
                        next(iter(inputs.values())) if len(inputs) == 1 else None)
                    output.update(ref or {})
                else:
                    output[key(name)] = flags(item.unalias(), inputs)
        else:
            continue
        if scope.is_root:
            return any(entry.null_filled_sum for entry in output.values())
        projections[id(scope)] = output
        # A nested SELECT contributes only through a consumed scalar value.
        if scope.is_subquery and isinstance(scope.expression, exp.Select) and len(scope.expression.selects) == len(output) == 1:
            scalar_projections[id(scope.expression)] = next(iter(output.values()))
    return False


def _legacy_budget_axis(sql, source, period, host, year):
    from .business_ambiguity import _column_name, _physical_select
    from .catalog_view import comparison_budget_baseline
    from .published_scope import _conjuncts, _membership
    from .retrieval import normalize

    question = normalize(host.get('user_text') or '')
    baseline = comparison_budget_baseline(source, year, lambda subject: subject in question)
    shape = _physical_select(sql, source)
    if not baseline or not shape:
        return None
    _, _, reference = baseline
    tree, relation, table = shape
    if reference['table'] != table or period[0] != table or reference['filters'].get(period[1]) != [year]:
        return None
    where = tree.args.get('where')
    if not where or tree.args.get('limit') or tree.args.get('offset') or tree.args.get('having'):
        return None
    filters = {}
    for predicate in _conjuncts(where.this):
        match = _membership(predicate, lambda node: _column_name(node, relation))
        if not match or match[0] != 'set' or match[1] in filters:
            return None
        filters[match[1]] = sorted(match[2])
    if period[1] not in filters or not all(re.fullmatch(r'\d{4}', value) for value in filters.pop(period[1])):
        return None
    if filters != {k: v for k, v in reference['filters'].items() if k != period[1]}:
        return None
    return reference['column']


def _annual_measurements(sql, source, *, host=None, recent_closed_year=None):
    """Bind grouped money sums to published physical fields through simple aliases."""
    from natural_analysis.forecasting import _rounded_annual_measure
    from .business_dictionary import declared_field_unit
    from .snapshot_scope import _physical_table
    from .sql_answer_contract import _ACTUAL, _BUDGET

    publication = source.get('business_project') or {}
    if not source.get('id') or not publication.get('id') or not publication.get('version'):
        return None
    dialect = source['dialect']
    try:
        tree = _read_sql(sql, sqlglot_name(dialect))
        scopes = traverse_scope(tree)
    except SqlglotError:
        return None
    if not scopes or not isinstance(tree, exp.Select) or not (group := tree.args.get('group')):
        return None
    if len(group.expressions) != 1 or any(value for key, value in group.args.items() if key != 'expressions'):
        return None
    key = str.casefold if dialect == 'sqlite' else str
    fields = {(table['name'], key(column['name'])): column for table in source.get('tables', [])
              for column in table.get('columns', [])}
    mapped = {(mapping['table'], key(mapping['column'])): mapping for mapping in source.get('business_dictionary', [])
              if mapping.get('kind') == 'field' and mapping.get('source_id') == source['id']}

    def origin(node, scope):
        node = node.unnest()
        if not isinstance(node, exp.Column) or node.is_star or node.db or node.catalog:
            return None
        selected = scope.selected_sources
        entry = selected.get(node.table) if node.table else (next(iter(selected.values())) if len(selected) == 1 else None)
        if not entry:
            return None
        _, relation = entry
        if isinstance(relation, Scope) and isinstance(relation.expression, exp.Select):
            items = relation.expression.selects
            names = relation.outer_columns or [item.alias_or_name for item in items]
            matches = [item for name, item in zip(names, items) if key(name) == key(node.name)]
            if len(matches) == 1 and not any(item.is_star for item in items):
                return origin(matches[0].unalias(), relation)
        if table := _physical_table(relation, source, dialect):
            identity = table, key(node.name)
            return identity if identity in fields else None
        return None

    root = scopes[-1]
    grouped = group.expressions[0].unnest()
    items = tree.selects
    axis = [item for index, item in enumerate(items, 1) if item.unalias().unnest() == grouped
            or isinstance(grouped, exp.Literal) and grouped.is_int and int(grouped.this) == index]
    if len(axis) != 1 or not (period := origin(axis[0].unalias(), root)):
        return None
    time_column = axis[0].alias_or_name
    if not time_column:
        return None
    legacy_budget = None
    if not ((fields[period].get('time_semantics') or {}).get('grain') in ('year', 'annual')
            or _YEAR_FIELD.fullmatch((mapped.get(period) or {}).get('name') or '')):
        if not host or not recent_closed_year or not (
                legacy_budget := _legacy_budget_axis(sql, source, period, host, recent_closed_year)):
            return None
    names = [key(item.alias_or_name) for item in items]
    if len(set(names)) != len(names) or any(item.is_star for item in items):
        return None
    measurements = {}
    for item in items:
        node = _rounded_annual_measure(item.unalias().unnest())
        if not isinstance(node, exp.Sum) or node.expressions:
            continue
        operand = node.this.unnest()
        if (parts := _null_case(operand)) is not None:
            checked, when_null, when_present = parts
            operand = _decimal_column(when_present)
            if (operand is None or checked != operand or when_null is not None and not isinstance(when_null.unnest(), exp.Null)
                    or not _nonmultiplying_report_operand(root, operand, source, dialect)):
                continue
        else:
            operand = _decimal_column(operand)
            if operand is None:
                continue
        measure_field = origin(operand, root)
        if legacy_budget and measure_field != (period[0], legacy_budget):
            continue
        field = mapped.get(measure_field)
        if not field:
            continue
        label = unicodedata.normalize('NFKC', field.get('name') or '').strip()
        if _BUDGET.fullmatch(label) or _BUDGET_AMOUNT.fullmatch(label) and not _BUDGET_OTHER.search(label):
            role = 'budget'
        elif _ACTUAL.fullmatch(label):
            role = 'actual_fiscal'
        else:
            continue
        declared = declared_field_unit(field)
        legacy = set(_LEGACY_CURRENCY.findall(field.get('description') or '')) if not declared else set()
        unit = declared[0] if declared else next(iter(legacy)) if len(legacy) == 1 else ''
        if _CURRENCY_UNIT.fullmatch(unit) and item.alias_or_name:
            measurements[item.alias_or_name] = {'role': role, 'label': label, 'unit': unit}
    return {'time_column': time_column, 'measurements': measurements} if measurements else None


def annual_fiscal_window(host, *, now=None, sql=None, source=None):
    """Describe the annual forecast calendar without asserting data completeness."""
    host = host or {}
    if not _FISCAL_AMOUNT.search(str(host.get('user_text') or '')):
        return None
    from natural_analysis.forecasting import _explicit_annual_training_years, _relative_annual_training_years, requested_target

    now = now if now is not None else datetime.now(timezone.utc)
    target = requested_target(host, now)
    if not target or len(target) != 4:
        return None
    current_date = temporal_context(host.get('client_time_zone', 'UTC'), now)['current_date']
    current_year = current_date[:4]
    result = {'schema_version': 1, 'state': 'checked', 'basis': 'client_calendar',
              'current_date': current_date, 'current_open_year': current_year,
              'last_closed_year': f'{int(current_year) - 1:04}', 'target_period': target}
    history = _explicit_annual_training_years(host, now=now) or _relative_annual_training_years(host, now=now)
    if history:
        result['requested_years'] = list(history)
    if sql and source:
        publication = source.get('business_project') or {}
        if host.get('user_ref') and source.get('id') and publication.get('id') and publication.get('version'):
            result.update(request_ref=host['user_ref'], source_id=source['id'],
                          publication={key:publication[key] for key in ('id', 'version')})
        if observation := _annual_measurements(sql, source, host=host, recent_closed_year=result['last_closed_year']):
            result.update(observation)
    return result


def explicit_annual_amount_years(question):
    """Return distinct annual amount periods in the order the user named them."""
    text = unicodedata.normalize('NFKC', question or '')
    if not _FISCAL_AMOUNT.search(text) or _SUBANNUAL_AMOUNT.search(text):
        return ()
    return tuple(dict.fromkeys(_EXPLICIT_YEAR.findall(text)))


def _annual_amount_comparison(question):
    if len(explicit_annual_amount_years(question)) >= 2:
        return True
    text = unicodedata.normalize('NFKC', question or '')
    for clause in re.split(r'[。！？!?；;\n]+', text):
        if not _FISCAL_AMOUNT.search(clause) or _SUBANNUAL_AMOUNT.search(clause):
            continue
        clause = _SKIP_COMPARISON.sub('', clause)
        years = _EXPLICIT_YEAR.findall(clause)
        relative = {match.lastgroup for match in _RELATIVE_AMOUNT_YEAR.finditer(clause)}
        if len(set(years)) >= 2 or '同比' in clause:
            return True
        if (years and relative or len(relative) > 1) and (_ANNUAL_COMPARISON.search(clause) or _ANNUAL_FALLBACK.search(clause)):
            return True
    return False


def require_observed_fiscal_history(sql, dialect, host):
    """Keep missing amounts out of annual forecasts, comparisons and fallbacks."""
    forecast = annual_fiscal_window(host)
    question = (host or {}).get('user_text') or ''
    report = bool(_EXECUTION_RATE.search(question) or '执行率' in question and re.search(r'资金|预算', question))
    if not (forecast or report or _annual_amount_comparison(question)):
        return
    tree = parse_one(sql, read=sqlglot_name(dialect))
    if _fills_null_sum(tree, dialect):
        if report:
            raise ValueError('FISCAL_HISTORY_UNOBSERVED: 资金执行率使用同一项目、同一截止时点的最新报送。'
                             '请保留无报送的金额和验收状态为空，合计同时返回已报送的项目数。'
                             '未覆盖总体时保留已核实的项目数量，覆盖完整且实际记载为零时展示 0。')
        purpose = '趋势或预测' if forecast else '年度对比或年份回退'
        boundary = ('没有足够连续且已结年的可比观测时，只展示可核实金额并说明无法预测。' if forecast else
                    '分别核实各年同范围、同计量角色的来源；缺少可比数据时先给已核实金额，保留实际投入与预算的区分。')
        raise ValueError(f'FISCAL_HISTORY_UNOBSERVED: 年度预算或投入的缺失金额不能以 0 进入{purpose}。'
                         '请去掉金额聚合内外的缺失补零，保留实际记载的 0，未匹配或未报送的金额留空。'
                         + boundary)


_EXECUTION_RATE = re.compile(r'(?:资金|预算)执行率')
_CURRENT_YEAR = re.compile(r'本(?:年(?:度)?|财政年度)|今(?:年|年度)|当年(?:度)?')
_EXPLICIT_EXECUTION_MONTH = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*年\s*(0?[1-9]|1[0-2])\s*月')
_OTHER_PERIOD = re.compile(r'[一二三四五六七八九十0-9]{1,3}\s*月|'
                           r'\d{4}[-/]\d{1,2}|季度|半年|逐月|每月|按月|本月|上(?:个)?月|'
                           r'年前|上年度?|去年|前年|[一二三四五六七八九十0-9]{1,3}[号日]|'
                           r'同比|环比|对比|比较|预测|年末|年底|样例|示例|未来')
_FIXED_CUTOFF = re.compile(r'(?:截至|截止(?:于)?)\s*(\d{4}-\d{2}-\d{2})')
_PROVINCIAL_SCOPE = re.compile(r'示例为全省合计|(?:统计口径|统计范围)(?:为|是|[：:])\s*省级')


class _ReportBasis(NamedTuple):
    table: str
    key: str
    time: str
    fields: tuple[str, ...]


class _CurrentExecution(NamedTuple):
    spent: _ReportBasis
    budget: _ReportBasis
    cutoff: str
    subject: str
    example: exp.Expression


class _ReportAxis(NamedTuple):
    table: str
    key: str
    time: str
    spending: str
    budget: str
    start: str
    stop: str


def _annual_execution_day(host):
    question = unicodedata.normalize('NFKC', host.get('user_text') or '')
    if not _EXECUTION_RATE.search(question) or _OTHER_PERIOD.search(question):
        return None
    # Only real host-recorded replies can supply a cutoff that the question did not.
    if any(item.get('call_id') and item.get('question_id') and
           _OTHER_PERIOD.search(unicodedata.normalize('NFKC', item.get('answer') or ''))
           for item in host.get('clarifications', [])):
        return None
    today = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
    years = set(re.findall(r'(?<!\d)((?:19|20)\d{2})\s*年', question))
    if (_CURRENT_YEAR.search(question) or years == {str(today.year)}) and not years - {str(today.year)}:
        return question, today
    return None


def _read_sql(sql, dialect):
    return normalize_identifiers(parse_one(sql, read=dialect), dialect=dialect)


def _query_fingerprint(tree, dialect):
    return sha256(normalize_identifiers(tree.copy(), dialect=dialect).sql(dialect=dialect).encode()).hexdigest()


def _report_shape(scope, source, dialect):
    """A registered latest-report read names one physical table, key and order."""
    from .snapshot_scope import _physical_table

    select = scope.expression
    if not isinstance(select, exp.Select) or len(scope.selected_sources) != 1:
        return None
    alias, (_, relation) = next(iter(scope.selected_sources.items()))
    if not (table := _physical_table(relation, source, dialect)):
        return None

    def column(node):
        if isinstance(node, exp.Column) and node.table in ('', alias) and not (node.db or node.catalog):
            return node.name
        return None

    distinct = select.args.get('distinct')
    partition = distinct.args.get('on') if distinct else None
    if partition:
        keys, order = partition.expressions, select.args.get('order')
    else:
        windows = [item.unalias() for item in select.expressions if isinstance(item.unalias(), exp.Window)
                   and isinstance(item.unalias().this, exp.RowNumber)]
        if len(windows) != 1:
            return None
        keys, order = windows[0].args['partition_by'], windows[0].args.get('order')
    if len(keys) != 1 or not (key := column(keys[0])) or not order:
        return None
    sort = order.expressions
    if len(sort) == 2 and column(sort[0].this) == key:
        sort = sort[1:]
    if len(sort) != 1 or not sort[0].args.get('desc') or not (time := column(sort[0].this)) or time == key:
        return None
    fields = tuple(sorted({name for item in select.expressions if (name := column(item.unalias()))} - {key, time}))
    return _ReportBasis(table, key, time, fields)


def _nonmultiplying_report_operand(scope, operand, source, dialect):
    """Require at most one matching report row per outer row for a nullable sum."""
    joins = scope.expression.args.get('joins') or []
    if not joins:
        return len(scope.selected_sources) == 1 and all(
            isinstance(ref, exp.Table) for _, ref in scope.selected_sources.values())
    if len(joins) != 1 or len(scope.selected_sources) != 2:
        return False
    join = joins[0]
    on = join.args.get('on')
    report_alias = join.this.alias_or_name
    if join.side != 'LEFT' or join.kind or join.method or join.args.get('using') or not isinstance(on, exp.EQ):
        return False
    if operand.table != report_alias:
        return False
    left = scope.expression.args.get('from') or scope.expression.args.get('from_')
    left_alias = left.this.alias_or_name
    if not isinstance(scope.selected_sources[left_alias][1], exp.Table):
        return False
    relation = scope.selected_sources[report_alias][1]
    if not isinstance(relation, Scope):
        return False
    select = relation.expression
    if any(value for key, value in select.args.items() if key not in ('expressions', 'from', 'from_', 'distinct', 'order')):
        return False
    distinct = select.args.get('distinct')
    if not distinct or not distinct.args.get('on') or not (shape := _report_shape(relation, source, dialect)):
        return False
    sources = [item for item in select.selects if item.alias_or_name == shape.key]
    if len(sources) != 1 or not isinstance(sources[0].unalias(), exp.Column) or sources[0].unalias().name != shape.key:
        return False
    operands = [item.unnest() for item in (on.this, on.expression)]
    return all(isinstance(item, exp.Column) and not (item.db or item.catalog) for item in operands) and (
        {item.table for item in operands} == {left_alias, report_alias}
        and any(item.table == report_alias and item.name == shape.key for item in operands))


def _ratio_branches(tree, source, dialect):
    root = next((scope for scope in traverse_scope(tree) if scope.is_root), None)
    divisions = [node for node in tree.find_all(exp.Div) if node.find_ancestor(exp.Select) is tree]
    if not root or len(divisions) != 1:
        return None
    out = []
    for operand in (divisions[0].this, divisions[0].expression):
        # A side of the ratio must take its inputs from just one report branch.
        names = {ref.name for ref in operand.find_all(exp.Table) if not (ref.db or ref.catalog)}
        names.update(col.table for col in operand.find_all(exp.Column) if col.table)
        if len(names) != 1 or not isinstance(branch := root.sources.get(next(iter(names))), Scope):
            return None
        reports = [report for scope in traverse_scope(branch.expression)
                   if (report := _report_shape(scope, source, dialect))]
        if len(reports) != 1:
            return None
        out.append(reports[0])
    return out


def _public_subject(name):
    name = re.sub(r'^[a-zA-Z0-9_-]+\s+', '', name)
    return re.sub(r'的|整体|总体', '', _CURRENT_YEAR.sub('', name)).strip()


def _execution_basis(tree, source, dialect, cutoff, subject):
    reports = _ratio_branches(tree, source, dialect)
    if (reports and reports[0][:3] == reports[1][:3]
            and all(len(r.fields) == 1 for r in reports) and reports[0].fields != reports[1].fields):
        return _CurrentExecution(*reports, cutoff, subject, tree)


def _sample_execution_sql(source, registered, *, organization=None):
    try:
        cutoff = date.fromisoformat(registered.cutoff)
    except ValueError:
        return None
    spent, budget, _, _, _ = registered
    axis = _ReportAxis(spent.table, spent.key, spent.time, spent.fields[0], budget.fields[0],
                       cutoff.replace(month=1, day=1).isoformat(), (cutoff + timedelta(days=1)).isoformat())
    return _latest_execution_sql(source, registered, axis, overall_only=True, include_partial=True,
                                 organization=organization)


def _registered_execution(source, question):
    from .catalog_view import entry_code

    if not source.get('business_project', {}).get('version'):
        return None
    dialect = sqlglot_name(source.get('dialect'))
    sid = source.get('id')
    examples = defaultdict(list)
    for e in source.get('examples', []):
        if sid and e.get('source_id') == sid and e.get('origin') and (code := entry_code(e, 'question')):
            examples[e['origin'], code].append(e)
    matches = {}
    requested = re.sub(r'的|整体|总体|总的', '', question) if question is not None else None
    # Select annual overall definitions with a paired, same-publication
    # example. A unit suffix, draft or unrelated execution rate cannot supply a contract.
    for term in source.get('terms', []):
        name, definition = term.get('name') or '', term.get('definition') or ''
        if (term.get('source_id') != sid or not term.get('origin') or term.get('status') in ('draft', 'review', 'pending_review')
                or not _EXECUTION_RATE.search(name) or not _CURRENT_YEAR.search(name)
                or not _FIXED_CUTOFF.search(definition) or not (code := entry_code(term, 'name'))):
            continue
        name = _public_subject(name)
        # Deliberately require the complete business subject when the user named one.
        if not name or requested is not None and name not in requested:
            continue
        entries = examples.get((term['origin'], code), [])
        if len(entries) != 1:
            continue
        example = entries[0]
        if example.get('status') in ('draft', 'review', 'pending_review') or example.get('parameters') or not example.get('logical_sql'):
            continue
        config = term.get('config') if isinstance(term.get('config'), dict) else {}
        scope_sql = config.get('workbook_scope_sql') if config.get('kind') == 'funding-execution' else None
        cutoff = _FIXED_CUTOFF.search(definition)[1]
        if requested is not None and '省级' + name in requested and _PROVINCIAL_SCOPE.search(definition):
            name = '省级' + name
        try:
            tree = _read_sql(scope_sql, 'postgres') if scope_sql else _read_sql(example['logical_sql'], dialect)
            registered = _execution_basis(tree, source, dialect, cutoff, name)
            if registered and scope_sql:
                sample = _sample_execution_sql(source, registered)
                if not sample or _read_sql(sample, dialect) != _read_sql(example['logical_sql'], dialect):
                    continue
        except SqlglotError:
            continue
        if not registered:
            continue
        key = registered[:4]
        if key in matches:
            return None
        matches[key] = registered
    if len(matches) == 1:
        return next(iter(matches.values()))
    return None


def current_execution_guidance(source, host):
    host = host or {}
    question = unicodedata.normalize('NFKC', host.get('user_text') or '')
    recent = host.get('recent_questions') or []
    if (recent and not host.get('clarifications') and '执行率' in question
            and re.search(r'已验收(?:项目)?(?:占比|比例)|项目验收率', question)
            and re.search(r'该月|同月|这个月|上述月份', question)
            and not re.search(r'\d+\s*月|另(?:改|换)|截至|仅|只看|分别|排除|按(?:地市|单位|日期|状态)', question)):
        inherited = {**host, 'user_text': recent[-1], 'recent_questions': []}
        previous = current_execution_guidance(source, inherited)
        if (previous and previous.get('target_month') and previous.get('query_request')
                and (registered := _registered_execution(source, inherited['user_text']))
                and (status := _published_acceptance_status(source, registered.spent.table))):
            axis = _ReportAxis(previous['report_table'], previous['entity_key'], previous['time_column'],
                               previous['spending_field'], previous['budget_field'],
                               previous['start_inclusive'], previous['end_exclusive'])
            if sql := _latest_execution_sql(source, registered, axis, overall_only=True, acceptance=status):
                previous['query_request'] = {**previous['query_request'], 'sql': sql,
                                             'title': previous['target_month']+'项目资金与验收进展'}
                previous['includes_acceptance'] = True
                return previous
    request = _annual_execution_day(host)
    target_month = None
    if request:
        question, today = request
    else:
        months = list(_EXPLICIT_EXECUTION_MONTH.finditer(question))
        if not _EXECUTION_RATE.search(question) or len(months) != 1:
            return None
        year, month = map(int, months[0].groups())
        target_month = date(year, month, 1)
        month_end = (target_month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        real_day = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
        if target_month > real_day:
            return None
        today = min(month_end, real_day)
    if not (registered := _registered_execution(source, question)):
        return None
    spent, budget, cutoff, subject, _ = registered
    guidance = {'report_table': spent.table, 'entity_key': spent.key, 'time_column': spent.time,
            'spending_field': spent.fields[0], 'budget_field': budget.fields[0],
            'start_inclusive': today.replace(month=1, day=1).isoformat(),
            'end_exclusive': (today + timedelta(days=1)).isoformat(), 'example_cutoff': cutoff,
            'instruction': ('示例截止日仅用于复现样例。沿用已发布项目清单的年度、承担单位和关联条件；在本年度当前日期内，'
                            '先按项目取最新有效报送，再以同一份记录汇总支出和预算，回传实际最新报送日期。'
                            '用整体支出合计除以预算合计，并按同一批项目分单位核算排名。缺报、并列最新报送或零分母须显式核实，不能补零。')}
    # More specific requests can add a unit, choose a different scope, or need a
    # particular chart. They still get guidance without a generated query.
    # Only the published overall question and its explicit unit-ranking
    # follow-up can use this request without dropping user filters.
    clauses = [part.strip() for part in re.split(r'[。！？?!\n]', question) if part.strip()]
    ranking = not target_month and len(clauses) == 2 and re.fullmatch(
        r'哪(?:家|个)单位(?:的)?(?:资金|预算)执行率最高', clauses[1])
    if len(clauses) != 1 and not ranking:
        return guidance
    before = re.sub(r'^(?:请(?:帮我)?|帮我)?(?:查询|统计|计算)?\s*', '', clauses[0])
    if target_month:
        before = re.sub(r'^截至\s*', '', before)
        before = _EXPLICIT_EXECUTION_MONTH.sub('', before, count=1).strip()
    elif not _CURRENT_YEAR.search(before):
        before = re.sub(rf'^{today.year}\s*年(?:度)?\s*', '', before)
    if _public_subject(re.sub(r'(?:是)?多少$', '', before)) != subject:
        return guidance
    axis = _ReportAxis(spent.table, spent.key, spent.time, spent.fields[0], budget.fields[0],
                       guidance['start_inclusive'], guidance['end_exclusive'])
    if recipe := _latest_execution_sql(source, registered, axis, overall_only=not ranking,
                                       include_partial=target_month is None):
        title = f'{today.year}年度' + ('各单位及' if ranking else '') + '整体资金执行率'
        if target_month:
            guidance['target_month'] = target_month.isoformat()[:7]
            title = f'{target_month.year}年{target_month.month}月整体资金执行率'
        guidance['query_request'] = {'source_id': source['id'], 'sql': recipe,
                                     'title': title, 'purpose': 'answer'}
        guidance['instruction'] += '使用 query_request 直接查询。整体执行率按两项合计计算。'
    return guidance


def _published_acceptance_status(source, table):
    """Use an authorized field and its published state labels."""
    from .display_units import _table_identity

    labels = ('未验收未归档', '已验收未归档', '已归档')
    enum = re.compile(r'([A-Za-z0-9_-]+)\s*(?P<blank>或空)?\s*(?:表示|为|[：:])\s*(' + '|'.join(labels) + ')')
    matches = []
    tables = {value['name']: value for value in source.get('tables', [])}
    for item in source.get('business_dictionary', []):
        if item.get('kind') != 'field' or not item.get('column'):
            continue
        if _table_identity(item.get('table') or '', tables, str) != table:
            continue
        column = next((col for col in tables[table]['columns'] if col['name'] == item['column']), None)
        if column is None:
            continue
        values = {value.get('label'): value.get('value') for value in column.get('business_values') or []}
        blank = False
        if not values:
            declared = list(enum.finditer(item.get('description') or ''))
            values = {match[3]: match[1] for match in declared}
            blank = any(match['blank'] and match[3] == labels[0] for match in declared)
        states = tuple(values.get(label) for label in labels)
        if all(isinstance(value, (str, int)) and str(value).strip() for value in states) and len(set(states)) == 3:
            matches.append((column['name'], *map(str, states), blank))
    return matches[0] if len(matches) == 1 else None


def _entity_scope(tree, source, report, dialect):
    """Prove identical annual and organization filters in both report branches."""
    from .business_ambiguity import _column_name
    from .published_scope import _conjuncts
    from .snapshot_scope import _physical_table

    contracts = []
    for scope in traverse_scope(tree):
        select = scope.expression
        if not isinstance(select, exp.Select) or len(select.args.get('joins') or []) != 1:
            continue
        inputs = scope.selected_sources
        physical = [ref for _, ref in inputs.values() if isinstance(ref, exp.Table)]
        derived = [ref for _, ref in inputs.values() if isinstance(ref, Scope)]
        if len(inputs) != 2 or len(physical) != 1 or len(derived) != 1:
            continue
        inner = [shape for ref in traverse_scope(derived[0].expression)
                 if (shape := _report_shape(ref, source, dialect)) and shape[:3] == report[:3]]
        if len(inner) != 1:
            continue
        entity = physical[0]
        table = _physical_table(entity, source, dialect)
        join = select.args['joins'][0].args.get('on')
        where = select.args.get('where')
        groups = select.args.get('group')
        if not table or not isinstance(join, exp.EQ) or not where or not groups or len(groups.expressions) != 1:
            return None
        report_alias = next(alias for alias, (_, ref) in inputs.items() if ref is derived[0])
        left, right = join.this, join.expression
        if isinstance(left, exp.Column) and left.table == report_alias:
            left, right = right, left
        key = _column_name(left, entity)
        group = _column_name(groups.expressions[0], entity)
        if not key or not group or not isinstance(right, exp.Column) or right.table != report_alias or right.name != report.key:
            return None
        atoms = _conjuncts(where.this)
        years = [(name, literal) for atom in atoms if isinstance(atom, exp.EQ)
                 for name, literal in [(_column_name(atom.this, entity), atom.expression)]
                 if name and isinstance(literal, exp.Literal) and re.fullmatch(r'(?:19|20)\d{2}', literal.this)]
        eligible = [tuple(sorted(literal.this for literal in atom.expressions)) for atom in atoms
                    if isinstance(atom, exp.In) and _column_name(atom.this, entity) == group
                    and 0 < len(atom.expressions) <= 30
                    and all(isinstance(item, exp.Literal) and item.is_string for item in atom.expressions)]
        if len(atoms) != 2 or len(years) != 1 or len(eligible) != 1:
            return None
        year, example = years[0]
        contracts.append((table, key, group, year, example.this, example.is_string, eligible[0]))
    if len(contracts) == 2 and contracts[0] == contracts[1]:
        return contracts[0]
    return None


def _latest_execution_sql(source, registered, axis, *, overall_only=False, spending_only=False, include_partial=False,
                          acceptance=None, organization=None, collapse_agreeing_spend=False):
    dialect = sqlglot_name(source.get('dialect'))
    spent, _, _, _, tree = registered
    if dialect not in ('sqlite', 'postgres') or not (scope := _entity_scope(tree, source, spent, dialect)):
        return None
    entity, key, group, year, _, year_text, values = scope
    keys = {t['name']: {c['name']: c.get('type', '') for c in t.get('columns', [])} for t in source.get('tables', [])}
    if not {key, group, year} <= keys[entity].keys() or not {axis.key, axis.time, axis.spending, axis.budget} <= keys[axis.table].keys():
        return None
    # Text normalization uses the same local-date inference as the published
    # examples; do not reinterpret a timestamp with a configured zone.
    time_type = keys[axis.table][axis.time].lower()
    if 'timestamptz' in time_type or 'time zone' in time_type and 'without time zone' not in time_type:
        return None
    q = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    table = lambda name: '.'.join(q(part) for part in name.split('.'))
    text = lambda value: exp.Literal.string(value).sql(dialect=dialect)
    reports = table(axis.table)
    report_key, time, used, total = map(q, (axis.key, axis.time, axis.spending, axis.budget))
    project_key, unit, period = map(q, (key, group, year))
    yr = text(axis.start[:4]) if year_text else axis.start[:4]
    start, end = map(text, (axis.start, axis.stop))
    agencies = ', '.join(map(text, values))
    if organization is not None and organization not in values:
        return None
    if dialect == 'sqlite':
        clock, amount, allocated = f'NW_LOCAL_TIMESTAMP(r.{time})', f'NW_DECIMAL({used}, 1)', f'NW_DECIMAL({total}, 0)'
    else:
        clock = f'CAST(r.{time} AS TIMESTAMP)'
        amount = f"CAST(NULLIF(REGEXP_REPLACE(CAST({used} AS TEXT), '[^0-9.\\\\-]', '', 'g'), '') AS NUMERIC)"
        allocated = f"CAST(NULLIF(TRIM(CAST({total} AS TEXT)), '') AS NUMERIC)"
    if spending_only:
        allocated = 'CAST(NULL AS NUMERIC)'
    unique_report = 'COUNT(*) = 1'
    report_rows = 'COUNT(*)'
    entity_rows = 'COUNT(*)'
    if collapse_agreeing_spend:
        agreeing = f'COUNT(*) = COUNT({amount}) AND MIN({amount}) = MAX({amount})'
        unique_report = f'({unique_report} OR ({agreeing}))'
        report_rows = f'CASE WHEN {unique_report} THEN 1 ELSE COUNT(*) END'
        entity_rows = f'COUNT(DISTINCT {unit})'
    # The exact two published branches establish entity scope and money fields.
    # Keep all latest candidates until ties are counted. Missing input stays NULL
    # both for that unit and across the full population.
    selected = text(organization or '整体')
    overall = f" WHERE unit={selected}" if overall_only else ''
    complete_amounts = 'spent IS NOT NULL' if spending_only else 'spent IS NOT NULL AND budget IS NOT NULL'
    paired = 'entity_rows = 1 AND report_rows = 1 AND ' + complete_amounts
    partial_sums = (f', SUM(spent) FILTER (WHERE {paired}) AS paired_spent, '
                    f'SUM(budget) FILTER (WHERE {paired}) AS paired_budget') if include_partial else ''
    partial_columns = ', paired_spent, paired_budget' if include_partial else ''
    partial_totals = ', SUM(paired_spent), SUM(paired_budget)' if include_partial else ''
    partial_projection = (''',
 CASE WHEN covered<projects THEN ROUND(paired_spent,2) END AS "可核算项目已使用资金",
 CASE WHEN covered<projects THEN ROUND(paired_budget,2) END AS "可核算项目预算总额",
 CASE WHEN covered<projects THEN ROUND(100.0*paired_spent/NULLIF(paired_budget,0),2) END AS "可核算项目资金执行率（%）"'''
                          if include_partial else '')
    state_read = latest_state = state_counts = state_columns = state_totals = state_projection = ''
    if acceptance:
        field, unaccepted, accepted, archived, blank_is_unaccepted = acceptance
        state = f'TRIM(CAST(r.{q(field)} AS TEXT))'
        if blank_is_unaccepted:
            state = f"COALESCE(NULLIF({state}, ''), {text(unaccepted)})"
        state_read = f', {state} AS acceptance_state'
        latest_state = ', CASE WHEN COUNT(*)=1 THEN MAX(acceptance_state) END AS acceptance_state'
        valid = 'entity_rows=1 AND report_rows=1'
        possible = ', '.join(map(text, (unaccepted, accepted, archived)))
        accepted_or_archived = ', '.join(map(text, (accepted, archived)))
        state_counts = (f', COUNT(*) FILTER (WHERE {valid} AND acceptance_state IN ({possible})) AS known_state'
                        f', COUNT(*) FILTER (WHERE {valid} AND acceptance_state IN ({accepted_or_archived})) AS accepted'
                        f', COUNT(*) FILTER (WHERE {valid} AND acceptance_state={text(archived)}) AS archived')
        state_columns = ', known_state, accepted, archived'
        state_totals = ', SUM(known_state), SUM(accepted), SUM(archived)'
        state_projection = ''',
 known_state AS "验收状态已知项目数",
 CASE WHEN known_state>0 THEN accepted END AS "已验收项目数",
 CASE WHEN known_state>0 THEN archived END AS "已归档项目数",
 CASE WHEN known_state=projects THEN ROUND(100.0 * accepted / NULLIF(projects,0),2) END AS "已验收项目占比（%）"'''
    return f'''WITH eligible AS (
 SELECT {project_key} AS entity_id, MIN({unit}) AS unit, {entity_rows} AS entity_rows
 FROM {table(entity)} WHERE {period} = {yr} AND {unit} IN ({agencies}) GROUP BY {project_key}
), ranked AS (
 SELECT r.{report_key} AS entity_id, {clock} AS report_date, {used}, {total}{state_read},
  DENSE_RANK() OVER (PARTITION BY r.{report_key} ORDER BY {clock} DESC) AS n
 FROM {reports} r JOIN eligible e ON r.{report_key} = e.entity_id
 WHERE {clock} >= {start} AND {clock} < {end}
), latest AS (
 SELECT entity_id, MAX(report_date) AS report_date, {report_rows} AS report_rows,
  CASE WHEN {unique_report} THEN MAX({amount}) END AS spent,
  CASE WHEN COUNT(*) = 1 THEN MAX({allocated}) END AS budget{latest_state}
 FROM ranked WHERE n = 1 GROUP BY entity_id
), sums AS (
 SELECT unit, COUNT(*) AS projects, MAX(report_date) AS report_date,
  COUNT(*) FILTER (WHERE {paired}) AS covered,
  SUM(spent) AS spent, SUM(budget) AS budget{partial_sums}{state_counts}
 FROM eligible LEFT JOIN latest USING (entity_id) GROUP BY unit
), totals AS (
 SELECT unit, projects, report_date, covered, spent, budget{partial_columns}{state_columns} FROM sums
 UNION ALL SELECT '整体', SUM(projects), MAX(report_date), SUM(covered), SUM(spent), SUM(budget){partial_totals}{state_totals} FROM sums
)
SELECT unit AS "承担单位", projects AS "项目数量", covered AS "可核算项目数", report_date AS "最新报送日期",
 CASE WHEN covered=projects THEN ROUND(spent,2) END AS "已使用资金",
 CASE WHEN covered=projects THEN ROUND(budget,2) END AS "项目预算总额",
 CASE WHEN covered=projects THEN ROUND(100.0 * spent / NULLIF(budget,0),2) END AS "资金执行率（%）"{partial_projection}{state_projection}
FROM totals{overall} ORDER BY CASE WHEN unit='整体' THEN 0 ELSE 1 END, "资金执行率（%）" DESC NULLS LAST, unit'''


PARTIAL_EXECUTION_COLUMNS = ('可核算项目已使用资金', '可核算项目预算总额', '可核算项目资金执行率（%）')


def _report_operand(node, field):
    """Recognize the period units used in workbook examples and ordinary SQL."""
    wrappers = (exp.Cast, exp.TryCast, exp.Date, exp.TsOrDsToTimestamp)
    while isinstance(node := node.unnest(), wrappers):
        node = node.this
    def direct(value):
        while isinstance(value := value.unnest(), wrappers):
            value = value.this
        return isinstance(value, exp.Column) and field(value)

    if direct(node):
        return 'day'
    month_formats = {'%m': 'month', '%Y-%m': 'year_month', '%Y-%m-%d': 'day'}
    if isinstance(node, exp.TimeToStr) and (fmt := node.args.get('format')) and direct(node.this):
        return month_formats.get(fmt.this)
    if isinstance(node, exp.Extract) and str(node.this).casefold() == 'month' and direct(node.expression):
        return 'month'
    if isinstance(node, exp.Left) and direct(node.this) and isinstance(node.expression, exp.Literal):
        return {'7': 'year_month', '10': 'day'}.get(node.expression.this)
    if isinstance(node, exp.Substring) and direct(node.this) and isinstance(node.args.get('start'), exp.Literal) \
            and isinstance(node.args.get('length'), exp.Literal):
        return {('6', '2'): 'month', ('1', '7'): 'year_month', ('1', '10'): 'day'}.get(
            (node.args['start'].this, node.args['length'].this))
    if isinstance(node, exp.Anonymous) and node.name.casefold() == 'nw_report_month' \
            and len(node.expressions) == 1 and direct(node.expressions[0]):
        return 'month'
    if isinstance(node, exp.Anonymous) and node.name.casefold() == 'nw_local_timestamp' \
            and len(node.expressions) == 1 and direct(node.expressions[0]):
        return 'day'
    if isinstance(node, exp.Bracket) and isinstance(match := node.this.unnest(), exp.Anonymous) \
            and match.name.casefold() == 'regexp_match' and len(match.expressions) == 2 and direct(match.expressions[0]) \
            and isinstance(pattern := match.expressions[1], exp.Literal) and pattern.this == r'^[0-9]{4}-([0-9]+)' \
            and len(node.expressions) == 1 and isinstance(node.expressions[0], exp.Literal) and node.expressions[0].this == '0':
        return 'month'
    return None


def _fixed_end(value, unit, today, strict):
    while isinstance(value := value.unnest(), (exp.Cast, exp.TryCast)):
        value = value.this
    if not isinstance(value, exp.Literal):
        return None
    text = str(value.this)
    if unit == 'month' and re.fullmatch(r'0?[1-9]|1[0-2]', text):
        month = int(text)
        return month - int(strict) < today.month
    if unit == 'year_month' and re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', text):
        year, month = map(int, text.split('-'))
        return (year, month - 1 if strict else month) < (today.year, today.month)
    if unit == 'day' and re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[ T])', text):
        try:
            day = date.fromisoformat(text[:10])
        except ValueError:
            return None
        return (day - timedelta(days=1) if strict else day) < today
    return None


def require_current_execution_period(sql, source, host, parameters=None):
    """Reject unsupported fixed early cutoffs on the published report axis."""
    if not (guidance := current_execution_guidance(source, host)):
        return
    from .snapshot_scope import _physical_table

    today = date.fromisoformat(guidance['end_exclusive']) - timedelta(days=1)
    dialect = sqlglot_name(source.get('dialect'))
    tree = _read_sql(sql, dialect)
    publication = source.get('business_project') or {}
    recipe = guidance.get('query_request')
    if recipe and not parameters and publication.get('id') and tree == _read_sql(recipe['sql'], dialect):
        from .catalog_index import fingerprint

        return {'state': 'configured', 'basis': 'published_configuration', 'match': 'validated_current_execution_sql',
                'source_id': source['id'], 'project_id': publication['id'], 'project_version': publication['version'],
                'start_inclusive': guidance['start_inclusive'], 'end_exclusive': guidance['end_exclusive'],
                **({'target_month': guidance['target_month']} if guidance.get('target_month') else {}),
                **({'includes_acceptance': True} if guidance.get('includes_acceptance') else {}),
                'definition_fingerprint': fingerprint({'publication': publication, 'recipe': recipe['sql']}),
                'query_fingerprint': _query_fingerprint(tree, dialect),
                'numerical_acceptance': 'not_evaluated'}
    if not _annual_execution_day(host):
        return
    if parameters:
        bound = parameters if isinstance(parameters, dict) else {
            p['name']: p.get('value') for p in parameters if isinstance(p, dict) and 'name' in p}
        tree = tree.transform(lambda node: exp.convert(bound[node.name]) if isinstance(node, (exp.Placeholder, exp.Parameter))
                              and node.name in bound else node)

    def bad_cutoff(where, period):
        from .published_scope import _conjuncts
        for comparison in _conjuncts(where.this):
            if not isinstance(comparison, (exp.LT, exp.LTE, exp.GT, exp.GTE, exp.EQ, exp.Between)):
                continue
            if comparison.find_ancestor(exp.Where) is not where:
                continue
            if isinstance(comparison, exp.Between):
                field, value = comparison.this, comparison.args['high']
            else:
                field, value = comparison.this, comparison.expression
                if isinstance(comparison, (exp.GT, exp.GTE)) or isinstance(comparison, exp.EQ) and period(value):
                    field, value = value, field
            if (unit := period(field)) and _fixed_end(value, unit, today, isinstance(comparison, (exp.LT, exp.GT))):
                return True
        return False

    for scope in traverse_scope(tree):
        if not isinstance(scope.expression, exp.Select) or not (where := scope.expression.args.get('where')):
            continue
        relations = scope.selected_sources
        aliases = {alias for alias, (_, r) in relations.items() if _physical_table(r, source, dialect) == guidance['report_table']}
        if not aliases:
            continue

        def field(node):
            return (isinstance(node, exp.Column) and not (node.db or node.catalog) and node.name == guidance['time_column']
                    and (node.table in aliases or not node.table and len(relations) == 1))

        if bad_cutoff(where, lambda node: _report_operand(node, field)):
            raise ValueError('FISCAL_EXECUTION_CUTOFF: 当前请求未指定历史截止，SQL 却固定截到较早日期。'
                             '请在截至当前日期的授权范围内取最新可用报送。' + guidance['instruction'])
