"""Normalize proven-equivalent single-table detail SQL into grouped summaries.

When a user clearly asks for an additive summary grouped by business
dimension(s) (e.g. per store, department, customer, supplier) and the model
submits a plain single-table detail SELECT (without GROUP BY, AggFunc, JOIN,
LIMIT, or ambiguity), rewrite it into an equivalent GROUP BY + SUM(...) query
while preserving WHERE filters, bind parameters, NULL vs 0 semantics, and
entity IDs (so same-name different-ID entities are never merged).
"""
import re

import sqlglot
from sqlglot import exp

from .vendor.dialect import sqlglot_name


_DETAIL_REQUEST = re.compile(
    r'明细|流水|逐笔|逐条|逐单|清单|每笔|每条|每一笔|每一条|'
    r'合同号|订单号|单号|流水号|凭证号|发票号|运单号|批次号|记录号|明细表|'
    r'(?:记录|退款|订单|合同|交易|费用|采购|明细)列表'
)
_NON_SUMMABLE_REQUEST = re.compile(
    r'平均|均值|客单价|人均|单价|中位数|方差|标准差|率|占比|比例|百分比|%|％|'
    r'余额|库存|存量|期末|期初'
)
_GROUP_CUE = re.compile(
    r'(?:各|每个|各个)(?:门店|店|科室|部门|客户|供应商|单位|机构|地区|地市|城市|区县|省份|仓库|渠道|项目|产品|类别|类型|月|季度|年)'
    r'|(?:按|分|依照|根据)[\u4e00-\u9fffa-zA-Z0-9_、和与及]{1,24}?(?:汇总|统计|计算|分组|列出|列出来|聚合|求和)'
)
_ADDITIVE_REQUEST = re.compile(
    r'金额|总额|总金额|退款|费用|收入|支出|花费|成本|利润|销售额|营业额|回款|采购额|付款|收款|补贴|预算|拨付|投入|'
    r'数量|件数|总量|总数|总计|合计|多少元|多少钱|多少万元|多少亿元|多少件|多少吨|多少台|多少套'
)

_NON_ADDITIVE_COLUMN = re.compile(
    r'率|占比|比例|百分比|pct|percent|ratio|rate|均值|平均|avg|mean|'
    r'单价|均价|价格|price|unit_price|余额|库存|存量|累计|快照|得分|balance|stock|score',
    re.IGNORECASE,
)
_DETAIL_ID_COLUMN = re.compile(
    r'(?:合同|订单|流水|凭证|发票|运单|批次|记录|明细|交易|业务|单据)(?:号|编号|id|no|code)|'
    r'(?:^|_)(?:contract|order|refund|voucher|invoice|record|detail|tx|trans|flow|serial|seq)_(?:id|no|code|num)$|'
    r'^(?:id|no|code|seq|remark|note|备注|说明|创建时间|更新时间|created_at|updated_at)$',
    re.IGNORECASE,
)
_ENTITY_ID_COLUMN = re.compile(
    r'(?:门店|店铺|分店|科室|部门|客户|供应商|单位|机构|仓库|渠道|项目|地区|区域)\s*(?:id|编号|代码|编码)|'
    r'(?:^|_)(?:store|shop|branch|dept|department|customer|client|supplier|vendor|org|unit|warehouse|channel|project|region|city)_(?:id|code|no)$',
    re.IGNORECASE,
)
_ADDITIVE_COLUMN = re.compile(
    r'金额|总额|总金额|退款|费用|收入|支出|花费|成本|利润|销售|营业|回款|采购|付款|收款|补贴|预算|拨付|投入|数量|件数|总量|'
    r'(?:^|_)(?:amount|amt|fee|cost|revenue|income|expense|refund|payment|qty|quantity|total)(?:$|_)',
    re.IGNORECASE,
)
_TIME_GROUP_CUE = re.compile(
    r'按\s*(?:日|天|周|月|季度|年|时间|日期)|每\s*(?:日|天|周|月|季度|年)|各\s*(?:日|月|季度|年)|逐\s*(?:日|月|年)|分\s*(?:日|月|年)'
)
_DATE_DETAIL_COLUMN = re.compile(
    r'(?:日期|时间|时刻|时间戳|_date$|_at$|_time$|^date$|^time$)',
    re.IGNORECASE,
)
_ADDITIVE_UNITS = frozenset({'元', '万元', '亿元', '件', '台', '套', '吨', 'kg', '千克', '米', '平方米', '亩', '次', '笔', '份', '张', '本', '条'})
_NUMERIC_TYPES = ('int', 'real', 'float', 'double', 'numeric', 'decimal', 'number')


def _has_grouped_summary_intent(user_text: str) -> bool:
    if not user_text:
        return False
    text = user_text.strip()
    if _DETAIL_REQUEST.search(text) or _NON_SUMMABLE_REQUEST.search(text):
        return False
    return bool(_GROUP_CUE.search(text) and _ADDITIVE_REQUEST.search(text))


def _table_column_map(source: dict, table_name: str) -> dict:
    target = table_name.split('.')[-1].casefold()
    for table in source.get('tables') or []:
        name = str(table.get('name') or '').split('.')[-1].casefold()
        if name == target:
            return {
                str(col.get('name') or '').casefold(): col
                for col in (table.get('columns') or [])
                if col.get('name')
            }
    return {}


def _unwrap_measure(node: exp.Expression) -> exp.Expression | None:
    cur = node
    while isinstance(cur, (exp.Paren, exp.Cast, exp.Round)):
        cur = cur.this
    if isinstance(cur, exp.Column):
        return cur
    return None


def _classify_projection(proj: exp.Expression, col_map: dict, user_text: str) -> str:
    alias = proj.alias_or_name or ''
    expr = proj.unalias()
    base_col = _unwrap_measure(expr)
    col_name = base_col.name if base_col else ''
    meta = col_map.get(col_name.casefold(), {}) if col_name else {}
    label = str(meta.get('label') or alias or col_name)
    unit = str(meta.get('unit') or '').strip()
    col_type = str(meta.get('type') or '').lower()
    parts = [p for p in (alias, col_name, label) if p]

    if any(_NON_ADDITIVE_COLUMN.search(p) for p in parts) or unit in {'%', '％', '百分点'}:
        return 'non_additive'
    if any(_ENTITY_ID_COLUMN.search(p) for p in parts):
        return 'entity_id'
    if any(_DETAIL_ID_COLUMN.search(p) for p in parts):
        # Only drop as detail_id if the user did not explicitly ask to group by it.
        if alias and alias in user_text:
            return 'dimension'
        return 'detail_id'
    if any(_DATE_DETAIL_COLUMN.search(p) for p in parts) and not _TIME_GROUP_CUE.search(user_text):
        return 'detail_id'
    if base_col is not None and not expr.find(exp.Div):
        is_numeric = any(t in col_type for t in _NUMERIC_TYPES)
        if unit in _ADDITIVE_UNITS or any(_ADDITIVE_COLUMN.search(p) for p in parts):
            return 'measure'
    return 'dimension'


def _physical_sources(tree, source):
    """Resolve only tables actually read, with no ambiguous short-name lookup."""
    out = {}
    for ref in tree.find_all(exp.Table):
        full = '.'.join(part.name for part in ref.parts).casefold()
        matches = [t for t in source.get('tables', []) if t.get('name', '').casefold() == full]
        if not matches and not ref.db and not ref.catalog:
            matches = [t for t in source.get('tables', [])
                       if t.get('name', '').rsplit('.', 1)[-1].casefold() == full]
        if len(matches) != 1:
            return {}
        out[ref.alias_or_name.casefold()] = matches[0]
    return out


def _many_to_one_joins(tree, source):
    """Every appended relation must be provably unique on its join keys."""
    from sqlglot.optimizer.scope import build_scope
    from .join_grain import _extract_join_edges, _table_meta_map
    scope = build_scope(tree)
    if scope is None:
        return False
    edges = _extract_join_edges(scope, _table_meta_map(source), source=source)
    from_ = tree.args.get('from_') or tree.args.get('from')
    seen = {from_.this.alias_or_name.casefold()}
    for join in tree.args.get('joins') or []:
        alias = join.this.alias_or_name.casefold()
        matches = [e for e in edges if (
            e['left'] == alias and e['right'] in seen and e['left_unique'] is True
            or e['right'] == alias and e['left'] in seen and e['right_unique'] is True)]
        if not matches or str(join.args.get('side') or '').upper() in ('RIGHT', 'FULL'):
            return False
        seen.add(alias)
    return True


def _projection_meta(proj, tables):
    column = _unwrap_measure(proj.unalias())
    if column is None:
        return None, None, None
    found = []
    for alias, table in tables.items():
        if column.table and column.table.casefold() != alias:
            continue
        for meta in table.get('columns', []):
            if meta.get('name', '').casefold() == column.name.casefold():
                found.append((alias, table, meta))
    return found[0] if len(found) == 1 else (None, None, None)


def normalize_group_summary_sql(sql: str, source: dict, host: dict | None) -> str:
    """Rewrite a provably-equivalent single-table detail SELECT into GROUP BY + SUM."""
    if not sql or not source or not host:
        return sql
    user_text = str(host.get('user_text') or '').strip()
    if not _has_grouped_summary_intent(user_text):
        return sql

    dialect = sqlglot_name(source.get('dialect', 'sqlite'))
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return sql

    if not isinstance(tree, exp.Select):
        return sql
    allowed_args = {'expressions', 'from', 'from_', 'where', 'order', 'joins'}
    if any(value and key not in allowed_args for key, value in tree.args.items()):
        return sql
    if tree.find(exp.AggFunc, exp.Window, exp.CTE, exp.Subquery, exp.Union):
        return sql
    if any(e.is_star for e in tree.expressions) or len(tree.expressions) < 2:
        return sql

    from_clause = tree.args.get('from_') or tree.args.get('from')
    if not from_clause or not isinstance(from_clause.this, exp.Table):
        return sql
    tables = _physical_sources(tree, source)
    if not tables or tree.args.get('joins') and not _many_to_one_joins(tree, source):
        return sql

    dims, entity_ids, measures, details = [], [], [], []
    for proj in tree.expressions:
        alias, table, meta = _projection_meta(proj, tables)
        col_map = {meta['name'].casefold(): meta} if meta else {}
        role = _classify_projection(proj, col_map, user_text)
        if role == 'non_additive':
            return sql
        if role == 'measure':
            measures.append(proj)
        elif role == 'detail_id':
            details.append(proj)
        elif role == 'entity_id':
            entity_ids.append(proj)
        else:
            label = str((meta or {}).get('label') or proj.alias_or_name or '')
            noun = re.sub(r'(?:名称|名字|名)$', '', label)
            if not (label and label in user_text or noun and noun in user_text
                    or _TIME_GROUP_CUE.search(user_text) and re.search(r'月|年|季度|日期', label)):
                return sql
            dims.append(proj)

    if len(measures) != 1 or not dims:
        return sql
    if tree.args.get('joins'):
        measure_alias, _, _ = _projection_meta(measures[0], tables)
        if measure_alias != from_clause.this.alias_or_name.casefold():
            return sql

    # Preserve entity IDs (same-name different-ID safety) alongside dimensions.
    group_projs = [*entity_ids, *dims]
    measure_proj = measures[0]
    measure_expr = measure_proj.unalias().copy()
    measure_alias = measure_proj.alias_or_name

    new_selects = [p.copy() for p in group_projs]
    sum_expr = exp.Sum(this=measure_expr)
    if measure_alias:
        new_selects.append(exp.alias_(sum_expr, measure_alias, quoted=isinstance(measure_proj, exp.Alias) and measure_proj.args.get('alias').quoted))
    else:
        new_selects.append(sum_expr)

    new_tree = tree.copy()
    new_tree.set('expressions', new_selects)
    group_exprs = [p.unalias().copy() for p in group_projs]
    # An entity's display name is not its identity. Retain its physical key in
    # GROUP BY even when the model only projected the requested display name.
    for proj in dims:
        alias, table, meta = _projection_meta(proj, tables)
        if (meta and tree.args.get('joins') and alias != from_clause.this.alias_or_name.casefold()
                and re.search(r'名称|名字|姓名|(?:^|_)name$', str(meta.get('label') or meta['name']), re.I)):
            for key in table.get('primary_key') or []:
                expr = exp.column(key, table=alias, quoted=True)
                if not any(expr == previous for previous in group_exprs):
                    group_exprs.append(expr)
    new_tree.set('group', exp.Group(expressions=group_exprs))

    # Keep only ORDER BY items that still exist in the grouped projection.
    if order := new_tree.args.get('order'):
        kept_aliases = {p.alias_or_name.casefold() for p in group_projs if p.alias_or_name}
        kept_exprs = {p.unalias().sql(dialect=dialect).casefold() for p in group_projs}
        kept_orders = []
        for ordered in order.expressions:
            target = ordered.this
            target_sql = target.sql(dialect=dialect).casefold()
            if isinstance(target, exp.Column) and target.name.casefold() in kept_aliases:
                kept_orders.append(ordered)
            elif target_sql in kept_exprs:
                kept_orders.append(ordered)
            elif measure_alias and isinstance(target, exp.Column) and target.name.casefold() == measure_alias.casefold():
                kept_orders.append(exp.Ordered(this=exp.Sum(this=measure_expr.copy()), desc=ordered.args.get('desc'), nulls_first=ordered.args.get('nulls_first')))
        if kept_orders:
            order.set('expressions', kept_orders)
        else:
            new_tree.set('order', None)

    return new_tree.sql(dialect=dialect)
