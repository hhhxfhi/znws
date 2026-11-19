"""0.7.0: a question that names a published metric is answered with that metric.

When the user's question names a published metric (name, alias or indicator code) and a
data_sql_query aggregates that metric's table, the SQL is refused and the error carries the
metric's id and full definition, so the model re-asks with data_metric_query. Writing SQL
ourselves let the model pick its own time field or filters and drift from the agreed caliber.

Queries listing rows or using unrelated tables remain available. A failed metric attempt
never authorizes an aggregate with different filters, including in later conversation turns.
"""
import json
import re

_REQUESTS_GROUP = re.compile(r'按(?:地市|城市|地区|区域|部门|单位|类别|分类)|各(?:地市|城市|地区|区域|部门|单位|类别)|分(?:地市|城市|地区|区域|部门|单位)')


def metric_tables(metric):
    used=set()
    for o in metric.get('operands') or []:
        if not isinstance(o,dict):continue
        if o.get('table'):used.add(o['table'])
        if isinstance(o.get('latest'),dict) and o['latest'].get('entity_table'):used.add(o['latest']['entity_table'])
    return used


def check_sql(engine, ctx, source_id, sql, dialect):
    """Raise METRIC_REQUIRED when the SQL aggregates the table of a metric the question names."""
    host = getattr(ctx, 'host_evidence', None) or {}
    text = host.get('user_text', '')
    if not text:
        return
    from .onboarding import metric_catalog
    from .catalog_view import enrich_metrics, metric_brief
    from .metric_search import exact_hits
    source = engine.source(ctx.project, source_id)
    from .temporal_scope import metric_month_request
    if host.get('recent_questions') and not re.search(r'按月|逐月|每月|月度', text) and metric_month_request(host) is not None:
        text = host['recent_questions'][-1] + ' ' + text
    metrics = enrich_metrics({**source, 'semantic_metrics': metric_catalog(source)[0]})
    review = [{**term, 'pending': True, 'id': term.get('code') or term['id'],
               'operands': (term.get('config') or term.get('proposed_config') or {}).get('operands') or [{'table':term.get('review_table','')}]}
              for term in source.get('terms', []) if term.get('origin') == 'excel' and term.get('status') == 'review' and (term.get('source_id') or (term.get('config') or term.get('proposed_config') or {}).get('source_id')) == source_id]
    hits = exact_hits(text, metrics + review)
    if not hits:
        return
    import sqlglot
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except (ValueError, sqlglot.errors.SqlglotError):
        raise ValueError('METRIC_SQL_UNVERIFIED: 无法解析 SQL 并核对指标表，请检查当前数据源方言。') from None
    aggregated = tree.find(sqlglot.exp.AggFunc) is not None or tree.find(sqlglot.exp.Group) is not None
    explicit_details = bool(re.search(r'明细|原始(?:记录|数据)|涉及的(?:订单|记录)|(?:列出|查看|展示).*(?:订单|原始记录)', text))
    if not aggregated and explicit_details:
        return
    from .vendor.plans import _cte_reference_ids
    refs = _cte_reference_ids(tree)
    authorized = [t['name'] for t in source['tables']]
    tables = set()
    for ref in tree.find_all(sqlglot.exp.Table):
        if id(ref) in refs: continue
        pieces = [p.name if p.args.get('quoted') else (p.name.upper() if dialect == 'oracle' else p.name.lower()) for p in ref.parts]
        matches = [name for name in authorized if name.split('.')[-len(pieces):] == pieces]
        tables.update(matches)
    required = []
    for m in hits:
        used = metric_tables(m)
        # Dimension/enum inspection is still available. A pre-aggregated monthly
        # value is a metric delivery even when SQL has no SUM or GROUP BY.
        value_columns = {o.get('column') for o in m.get('operands') or [] if o.get('column')}
        projects_value = any(
            expr.is_star or expr.find(sqlglot.exp.Star) is not None
            or any(c.name in value_columns for c in expr.find_all(sqlglot.exp.Column))
            for select in tree.find_all(sqlglot.exp.Select) for expr in select.expressions
        )
        if not aggregated and not projects_value and not m.get('pending'):
            continue
        if m.get('pending') and used & tables:
            raise ValueError('EXCEL_REVIEW_REQUIRED: 「'+m['name']+'」的 Excel 口径尚待复核，不能用 SQL 猜算。')
        if used & tables:
            required.append(m)
    if len(required) > 1:
        # Catalog order is not business authority. Different definitions may
        # share a name/table while differing in cutoff, population or status.
        # Expose every relevant choice rather than force the first old metric.
        from .catalog_index import fingerprint
        candidates = [{**metric_brief(m), 'read_request': {'source_id':source_id,'metric_id':m['id']},
                       'time_mode':m.get('time_mode'),
                       'time_fields':[{'table':o.get('table'),'column':o.get('time_column'),
                                       'latest':o.get('latest')} for o in m.get('operands') or []],
                       'filters':[f for o in m.get('operands') or [] for f in o.get('filters') or []]}
                      for m in sorted(required,key=lambda m:m['id'])]
        raise ValueError('METRIC_SELECTION_REQUIRED: 当前问题对应多个已发布定义，目录顺序不决定应使用哪个。'
                         '按原问的统计对象、时点/期间、状态及筛选读取完整定义；多项问题分别完成，'
                         '仅在业务范围仍无法区分时向用户澄清。不要选第一项或把相同数值视为口径一致。'
                         +json.dumps({'config_fingerprint':fingerprint(source),'candidates':candidates},ensure_ascii=False))
    for m in required:
        alternative = ''
        if not m.get('dimensions') and _REQUESTS_GROUP.search(text):
            alternative = ('该指标不提供额外分组。请先从 data_catalog 的已发布口径确认同一统计对象、时点、状态、计量单位和已授权明细。'
                           '口径明确允许明细分组时，用正式 data_sql_query 查询该明细表，不在已汇总的指标表虚构分组。'
                           '全体分母须单独核对同范围、同口径与跨组去重；仅能核实明细查询范围时，按该范围交付并说清覆盖。')
        raise ValueError(
            'METRIC_REQUIRED: 问题问的是已发布指标「%s」，要按登记口径查询：data_metric_query(source_id="%s", metric_id="%s")，'
            '不要自己写 SQL 聚合这张指标表（时间字段、筛选条件可能和口径不一致）。若已调用失败，请按登记口径修正参数。'
            '不支持的分组或期间应明确说明，不要在指标表重复调用或改写 SQL 绕过。%s口径：%s'
            % (m.get('name') or m['id'], source_id, m['id'], alternative, json.dumps(metric_brief(m), ensure_ascii=False)))


HINT_LIMIT = 3
HINT_CHARS = 40  # per metric: with id, name and marker about 60 tokens


def sql_hint(engine, ctx, source_id, sql, dialect):
    """Second level: the SQL aggregates a table that has published metrics the question did not
    name. Nothing is refused; the receipt names up to HINT_LIMIT of them, short, most relevant
    first, so the model can switch to data_metric_query when one of them is what was asked."""
    host = getattr(ctx, 'host_evidence', None) or {}
    import sqlglot
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return None
    if not (any(True for _ in tree.find_all(sqlglot.exp.AggFunc)) or tree.find(sqlglot.exp.Group) is not None):
        return None
    tables = {t.name.lower() for t in tree.find_all(sqlglot.exp.Table)}
    from .onboarding import metric_catalog
    from .catalog_view import enrich_metrics, metric_rule
    from .metric_search import rank_metrics
    source = engine.source(ctx.project, source_id)
    metrics = [m for m in enrich_metrics({**source, 'semantic_metrics': metric_catalog(source)[0]})
               if {t.split('.')[-1].lower() for t in metric_tables(m)} & tables]
    if not metrics:
        return None
    picked = rank_metrics(host.get('user_text', ''), metrics)[:HINT_LIMIT]
    def line(m):
        definition = str(m.get('definition') or '')
        pending = '（推定，待确认）' if '待确认' in definition or '待确认' in str(m.get('name') or '') else ''
        definition = definition if len(definition) <= HINT_CHARS else definition[:HINT_CHARS] + '…'
        return '%s「%s」：%s（%s）%s' % (m['id'], m.get('name') or m['id'], definition, metric_rule(m), pending)
    return ('这张表有已登记的指标。如果用户问的就是其中之一，请改用 data_metric_query(metric_id=…) 按登记口径重新查询；其他问题按授权字段和用户意图查询计算。定义的审核状态仅用于配置管理，不在回答中追加业务确认要求：'
            + '；'.join(line(m) for m in picked) + ('。另有%d个，见目录' % (len(metrics) - len(picked)) if len(metrics) > len(picked) else '。'))
