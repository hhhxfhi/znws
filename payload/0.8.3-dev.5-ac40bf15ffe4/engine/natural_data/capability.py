"""Data capability adapter. Domain rules stay here, not in the shared runtime."""
import time
from .vendor.dialect import sqlglot_name
from typing import ClassVar, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from natural_work.runtime import Capability, Operation
from .task_contract import put_bound_plan

Chart = Literal['table','kpi','bar','column','grouped-bar','stacked-bar','percent-stacked-bar','line','area','pie','donut','scatter','bubble','heatmap','treemap','histogram','radar','funnel','gauge','waterfall','boxplot','alert-table','pivot-table']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Catalog(Input):
    entry_ref: str | None = Field(default=None, max_length=100, description='按目录 entry_ref 读取完整业务条目，必须带 source_id；表也可用 table:<完整表名>。长条目续页原样使用回执 read_request')
    entry_version: str | None = Field(default=None, max_length=64, description='原样使用该条目 read_request.entry_version；完整旧指纹也可读取')
    entry_offset: int = Field(default=0, ge=0)
    entry_limit: int = Field(default=6000, ge=1, le=6000)
    source_id: str | None = Field(default=None, description='可选的数据源 ID')
    source_offset: int = Field(default=0, ge=0)
    source_limit: int = Field(default=16, ge=1, description='每页来源数，最多32，超出按32处理')
    table_name: str | None = Field(default=None, description='查看该来源的授权表字段，优先用目录中的完整表名；该来源内唯一的裸表名也可用')
    table_offset: int = Field(default=0, ge=0)
    table_limit: int = Field(default=200, ge=1, description='每页表数，默认200：表清单只含表名，放得下就一次列全；超出按200处理')
    column_offset: int = Field(default=0, ge=0)
    column_limit: int = Field(default=50, ge=1, description='每页字段数，最多80')
    metric_id: str | None = Field(default=None, description='可选指标ID，配合 source_id 获取完整口径')
    metric_offset: int = Field(default=0, ge=0)
    metric_limit: int = Field(default=8, ge=1, description='每页指标数，最多16')
    view: Literal['full', 'metrics'] = Field(default='full', description='metrics：只查指标，按question/metric_group返回最多10个完整口径')
    metric_group: str | None = Field(default=None, max_length=60, description='指标分组名，见目录metric_groups')
    question: str | None = Field(default=None, max_length=2000, description='用户的原始问题或要找的业务名词。传入后只返回最相关的业务术语(caliber_matches)和示例(example_matches)；不传时按本轮用户问题检索')


class Parameter(Input):
    name: str
    type: Literal['string','integer','number','boolean','date','datetime']
    value: str


class Alert(Input):
    column: str
    op: Literal['>','>=','<','<=','==','!=']
    threshold: float
    severity: Literal['warn','error'] = 'warn'


class Query(Input):
    catalog_ref: str = Field(min_length=1, description='data_catalog 返回的目录产物引用；必须先获取真实目录')
    source_id: str = Field(min_length=1, description='目录中已授权的数据源 ID')
    sql: str = Field(min_length=1, max_length=50000, description='只读 SQL，使用目录里的准确表名、字段和数据库方言')
    title: str = Field(min_length=1, max_length=160, description='分析的中文标题')
    question: str = Field(min_length=1, max_length=10000, description='用户的原始问题，保留指标口径与图表诉求')
    chart_type: Chart | None = Field(default=None,description='柱状图/柱形图使用column（竖向）；条形图使用bar（横向）；指标卡kpi，仪表盘gauge。')
    alert_rules: list[Alert] | None = Field(default=None, max_length=30)
    parameters: list[Parameter] | None = Field(default=None, max_length=100)


class Execute(Input):
    task_id: str
    task_revision: int = Field(ge=1)
    plan_ref: str = Field(description='data_plan 或 data_plan_query 返回的不可变计划引用；不重复传SQL或指标条件')


class PlanQuery(Query):
    task_id: str
    task_revision: int = Field(ge=1)


class PlanOutput(Input):
    plan_ref: str
    task_id: str
    task_revision: int
    metric_contract: dict | None = None
    note: str = '计划已绑定任务修订；执行时仍校验最新条件。'


class ResultInput(Input):
    result_ref: str = Field(min_length=1, max_length=128, description='已成功查询（data_sql_query、data_metric_query 或 data_query）回执中的 result_ref')


class Analyze(ResultInput):
    operators: list[Literal['compute_dispersion','compute_skewness','compute_quantiles','detect_outliers','compute_correlation','compute_concentration']] | None = None
    metric_column: str | None = None


class Visualize(ResultInput):
    kind: Chart = Field(description='仅选择当前回执 visualization_options.available_types 中的类型；不支持的图形不会修改原结果。')
    expected_revision: int = Field(ge=0, description='工具回执中最新 view_revision；版本冲突时重新读取状态，不能强制覆盖')


class Inspect(ResultInput):
    pass


class DeriveGroup(Input):
    name: str = Field(min_length=1, max_length=120, description='派生分组名（用户确认的归并名称），各组不得重复')
    members: list[str] = Field(min_length=1, max_length=200, description='该组包含的维度取值，必须逐字来自原事实的真实取值；不得与其他组重叠，全部组合起来必须恰好覆盖该维度的所有取值')


class DeriveAggregation(ResultInput):
    dimension: str = Field(min_length=1, max_length=120, description='已成功结果的分类维度：用 facts.dimensions 中的真实维度 id，或逐行计数 SQL 结果中分类列的实际列名。')
    groups: list[DeriveGroup] = Field(min_length=2, max_length=20, description='用户已确认的映射：每组 name+members。未确认时服务端返回需展示给用户的映射提案；未知/重叠/遗漏/非可加口径会被拒绝')
    title: str = Field(min_length=1, max_length=160, description='派生结果的中文标题')


class CatalogOutput(Input):
    sources: list[dict]
    catalog_view: dict | None = None
    project: str
    catalog_ref: str
    expires_in_seconds: int
    temporal_context: dict
    readiness: dict
    # 0.7.0：多个库里有同一概念的不同指标时，目录带上这份清单（source_choice），先问用户用哪个库。
    # 合约里漏了这一项时，只要接入的库有同名指标，每次查看目录都会失败。
    same_name_metrics: dict | None = None


class ResultOutput(Input):
    # 0.7.0: declared first so it comes before warnings and other hints in the receipt.
    metric_hint: str | None = None
    validation: dict | None = None
    result_lineage: dict | None = None
    inspection_reconciliation: dict | None = None
    analysis_options: dict | None = None
    business_project: dict | None = None
    answer_contract: dict | None = None
    detail_summary: dict | None = None
    facts: list[dict] | None = None
    metric_contract: dict | None = None
    source_provenance: dict | None = None
    reason_code: str | None = None
    result_ref: str
    status: Literal['succeeded', 'failed', 'cancelled', 'execution_unknown', 'running']
    title: str | None = None
    row_count: int | None = None
    truncated: bool | None = None
    quality: dict | None = None
    metrics: dict | None = None
    receipt: dict | None = None
    error: str | None = None
    visualization: dict | None = None
    visualization_options: dict | None = None
    analysis_scope: str | None = None
    operators: dict | None = None
    artifact_ref: str | None = None
    plan_ref: str | None = None
    analysis_ref: str | None = None
    view_ref: str | None = None
    view_revision: int | None = None
    available_views: list[str] | None = None
    export_format: str | None = None
    note: str | None = None
    repair: dict | None = None
    report: dict | None = None
    values: dict | None = None
    task_id: str | None = None
    task_revision: int | None = None
    purpose: str | None = None
    query_identity: str | None = None
    warnings: list[str] | None = None
    period_coverage: list[dict] | None = None
    header_hint: str | None = None
    null_reasons: list[dict] | None = None
    claims: list[dict] | None = None
    delivery: dict | None = None


class Lenient(BaseModel):
    # One-step tools accept "3" for 3 and 3 for "3": small models often mix them.
    model_config = ConfigDict(extra='forbid', coerce_numbers_to_str=True)


class ScopeProposal(Input):
    table: str = Field(min_length=1, max_length=200)
    column: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=2000)
    interpretation: str = Field(min_length=1, max_length=1000)


class AskSql(Lenient):
    safe_validation_messages: ClassVar[dict[str, str]] = {
        'query_choice': 'QUERY_CHOICE: 必须且只能提供 sql（完整只读SQL）或 registered_query（目录已登记的查询ID）中的一项；不能省略查询内容。'
    }
    replaces_result_ref: str | None = Field(default=None, max_length=128, description='修正本轮已成功但错误的同范围查询时，填写旧 result_ref；跨期间/地区比较不要填写。服务端验证关系，旧结果保留追溯。')
    scope_proposal: list[ScopeProposal] | None = Field(default=None, max_length=20, description='可选范围建议，仅含授权字段、真实原文引用和解释；不允许 confirmed/verified/status，未传也检查原问。')
    source_id: str = Field(min_length=1, description='data_catalog 中的数据源 ID')
    sql: str | None = Field(default=None, min_length=1, max_length=50000, description='参数名必须为 sql，值是一条完整只读SQL文本，例如 {"sql":"SELECT COUNT(*) AS 数量 FROM 已授权表"}。不是工具名称，不要填写 data_sql_query，也不要使用 sqlTool/sql_tool。按目录字段和示例编写实际查询，期间保持用户要求；采用 registered_query 时省略 sql。')
    registered_query: Literal['historical_professional_units'] | None = Field(default=None, description='与 sql 二选一。登记的历史测绘专业资质查询应传 "registered_query":"historical_professional_units"；参数名和值分开，不能拼接成键，不能把值填成字符串null。期间和范围取自当前用户原问。')
    title: str = Field(min_length=1, max_length=160, description='结果的中文标题')
    chart_type: Chart | None = Field(default=None, description='可选：柱状图column（竖向），条形图bar（横向），折线line，饼图pie，指标卡kpi，表格table')
    task_id: str | None = Field(default=None, description='只在修改本会话已有查询时填写其 task_id；新问题不填，会自动新建任务')
    purpose: Literal['answer', 'explore'] | None = Field(default=None, description='answer（默认）：结果用于回答用户；explore：只为查看取值、核对字段或试探数据范围，结果不放进回答下方的结果卡')

    @model_validator(mode='after')
    def exactly_one_query(self):
        if bool(self.sql) == bool(self.registered_query):
            raise PydanticCustomError('query_choice', self.safe_validation_messages['query_choice'])
        return self

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        # Publish the same exclusive choice enforced above. Descriptions alone
        # allowed small models to send neither query repeatedly (native Q11).
        schema = super().model_json_schema(*args, **kwargs)
        branches = []
        for selected, excluded in [('sql', 'registered_query'), ('registered_query', 'sql')]:
            branch = {**schema, 'properties': {k: v for k, v in schema['properties'].items() if k != excluded},
                      'required': [*schema.get('required', []), selected]}
            branches.append(branch)
        return {'oneOf': branches, **({'$defs': schema['$defs']} if '$defs' in schema else {})}

    @field_validator('sql')
    @classmethod
    def sql_text(cls,value):
        # A tool/identifier or lone keyword is malformed input, not a failed
        # database operation. Full SQL still passes the ordinary AST policy.
        import re
        if value is not None and (not value.strip() or re.fullmatch(r'[\w.\-]+',value.strip())):
            raise ValueError('sql 必须是完整的只读SQL语句，不能是工具名称或单个关键字。')
        return value


class AskFilter(Lenient):
    dimension: str = Field(description='必须逐字使用该指标目录 dimensions 中的 id，不是任意地区名或物理字段。未列出的 region/city 等维度不可自行添加。')
    op: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in']
    values: list[str] = Field(min_length=1, max_length=100)


class PeriodComparison(Lenient):
    baseline_periods: list[str] = Field(min_length=1,max_length=60,description='基期逐月YYYY-MM，与periods本期等长且互不重叠；一次查询两期并算逐月及合计同比')
    change_threshold: float | None = Field(default=None,description='可选：合计变化率百分比阈值，例如10表示10%；不填则不判断阈值')
    operator: Literal['>','>=','<','<=','=='] = Field(default='>',description='与合计变化率阈值比较的运算符，严格按用户要求填写')


class AskMetric(Lenient):
    source_id: str = Field(min_length=1, description='data_catalog 中的数据源 ID')
    metric_id: str = Field(min_length=1, description='目录 semantic_metrics 中的指标 id')
    metric_version: str | None = Field(default=None, description='该指标在目录中的 version；目录只列了名称时可省略，取已发布的最新版本')
    periods: list[str] = Field(default_factory=list, max_length=60, description='必须是真正的JSON字符串数组，如 ["2025-01","2025-02"] 或 ["2025"]；不能传字符串null、不能改名periods_。有comparison时填本期月份；全量口径传 []')
    group_by: list[str] = Field(default_factory=list, max_length=6, description='额外业务分组维度 id，来自指标的 dimensions。系统已自动按 periods 逐期返回，按月/年统计无需再填 period；仅兼容显式 period，不接受自造 month/year 等维度。')
    filters: list[AskFilter] = Field(default_factory=list, max_length=30, description='只添加用户要求且该指标 dimensions 已登记的筛选。定义已固定的地区/类别无需重复筛选；未登记该维度时不得自造 region 等过滤条件。无额外筛选时省略。')
    title: str = Field(min_length=1, max_length=160, description='结果的中文标题')
    chart_type: Chart | None = Field(default=None,description='仅用户指定图形时填写，如column柱状图、table表格；否则省略本字段。不要生成chart_type_error等自造参数。')
    task_id: str | None = Field(default=None, description='只在修改本会话已有查询时填写；新问题不填')
    comparison: PeriodComparison | None = Field(default=None,description='月份比较对象，例如 periods=["2025-12"]、comparison={"baseline_periods":["2025-11"]}。同比基期填上年同期月份。不能传字符串"环比"/"同比"，也不要添加type/current_periods。可加指标一次返回原值、差额、变化率和合计。若参数修正仍失败，可省略comparison查询完整两期，再调用analysis_compute核算，保留同一指标口径。')


# 0.6.3: the registered metric fixes the caliber (filters such as the platform id), so
# mapped tables stay behind the metric flow; the message says exactly how to use it.
METRIC_REQUIRED_SQL=('METRIC_REQUIRED: 这张表已登记业务指标，不能用自由SQL查询，名单和明细也走指标流程。'
 '请按指标流程：data_catalog(source_id=该来源) 查看 metric_definitions 选指标 → work_create(capability_id="data", set_fields 写 metric 和 periods，periods 用 JSON 数组如 ["2025"]) → data_plan → data_report。'
 '问某年的值 periods 填年份；各月或环比填逐月（环比加上一年12月）；按类别、地市、等级拆分用 group_by；名单用 detail=true；同比、环比、差额、排名再用 analysis_compute。')

def capability(engine, *, analysis_invoke=None):
    from .metrics import Report, compile_report
    from .metric_limitations import require_available
    from .semantic import SemanticPlan, compile_plan, definitions as semantic_definitions
    class PlanReport(Report):
        task_id: str
        task_revision: int = Field(ge=1)
    def catalog(args, ctx):
        planning = engine.catalog(ctx.project, args.source_id)
        from .onboarding import describe
        value = {**planning, 'sources': [describe(source) for source in planning['sources']]}
        from .source_choice import annotate
        every = value['sources'] if not args.source_id else [describe(s) for s in engine.catalog(ctx.project)['sources']]
        same_name = annotate(every)
        if args.source_id and same_name:
            annotate(value['sources'] + [s for s in every if s['id'] != args.source_id])
        if same_name:
            value['same_name_metrics'] = same_name
        from .temporal_context import temporal_context
        from .catalog_view import paged_catalog, size, MAX_BYTES
        # Build the view first so a rejected page stores nothing, then reuse an
        # identical recent catalog artifact instead of storing a copy per page.
        view = paged_catalog({**value, 'catalog_ref': '', 'expires_in_seconds': 1800,
                'readiness': {'basic_query': any(s['query_modes']['basic_query'] for s in value['sources']),
                              'business_metrics': sum(len(s['semantic_metrics'])+len(s['metric_definitions']) for s in value['sources']),
                              'message': '原问匹配已发布指标时使用登记定义。没有匹配指标或示例时，读取同一授权来源的相关表、字段与关联，直接完成只读基础查询，再核算。仅在数据核对后仍有影响结果的业务选择时澄清。'},
                'temporal_context':temporal_context(ctx.host_evidence.get('client_time_zone','UTC'))}, args, question_of(ctx, ''))
        from natural_analysis.forecasting import annual_forecast_boundary, annual_investment_history_query, relative_month_window, target_mentions
        host = ctx.host_evidence or {}
        if (mentions := target_mentions(host)) and (window := relative_month_window(host, mentions)):
            view['temporal_context']['requested_forecast_history'] = {
                'target_period': window['target_period'],
                'period_count': len(window['requested_periods']),
                'start_inclusive': window['start_inclusive'],
                'end_exclusive': window['end_exclusive'],
                'instruction': '按用户原范围先查询该窗口内实际观测，缺月留空。若查完确认不完整，说明缺期；改短窗口需要用户明确同意。',
            }
        ref, age = engine.artifacts.put_recent(ctx.project, 'data.catalog', planning, producer='data.catalog@1.0.0', max_age=1200)
        from .business_ambiguity import dimension_business_choice
        from .fiscal_history import current_execution_guidance
        from .fiscal_report_budget import published_budget_guidance
        from .intent_contract import first_issuance_notice
        from .qualification_scope import catalog_guidance as qualification_guidance
        from .row_count_aggregation import detail_query, grouping_query
        for item in view.get('sources',[]):
            scoped=next((s for s in planning['sources'] if s['id']==item['id']),None)
            if scoped:
                from .sql_guidance import guidance
                item['sql_guidance'] = guidance(scoped['dialect'])
                if recipe := detail_query(scoped, ctx.host_evidence):
                    item['detail_query'] = recipe
                if recipe := grouping_query(engine, ctx, scoped):
                    item['published_grouping_query'] = {k: v for k, v in recipe.items() if k != 'proof'}
                if guidance := qualification_guidance(scoped, host, automatic=len(every) == 1):
                    item['qualification_scope'] = guidance
                    if guidance.get('holding_rate', {}).get('query_request') and size(view) > MAX_BYTES:
                        compact = {k: v for k, v in item.items() if k not in
                                   ('example_matches', 'caliber_matches', 'catalog_entries', 'starter_questions')}
                        if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                            view['sources'] = [compact]
                        else:
                            guidance['holding_rate'].pop('query_request')
                from .cancellation_scope import catalog_guidance as cancellation_guidance
                if cguide := cancellation_guidance(scoped, host):
                    item['cancellation_scope'] = cguide
                if (len(every) == 1 and not (args.table_name or args.entry_ref or args.metric_id)
                        and (funding := current_execution_guidance(scoped, host))):
                    item['current_execution_guidance'] = funding
                    if funding.get('query_request') and size(view) > MAX_BYTES:
                        compact = {k: v for k, v in item.items()
                                   if k not in ('example_matches', 'caliber_matches', 'catalog_entries')}
                        replacement = {**view, 'sources': [compact]}
                        if size(replacement) <= MAX_BYTES:
                            view = replacement
                    if size(view) > MAX_BYTES:
                        item.pop('current_execution_guidance')
                if len(every) == 1 and not (args.table_name or args.entry_ref or args.metric_id):
                    from .annual_budget_reference import _report_comparison, project_spending_guidance
                    from .observed_sql_scope import assessment_catalog_query
                    if comparison := _report_comparison(scoped, host):
                        spending, observation = comparison
                        compact = {k: v for k, v in item.items()
                                   if k not in ('example_matches', 'caliber_matches', 'catalog_entries', 'starter_questions')}
                        compact['comparison_report_amounts'] = spending
                        clauses = observation['additional_questions']
                        if len(clauses) == 1 and (assessment := assessment_catalog_query(
                                scoped, every[0], clauses[0], host, engine, ctx)):
                            compact['comparison_assessment_query'] = assessment
                        replacement = {**view, 'sources': [compact]}
                        if size(replacement) > MAX_BYTES:
                            compact.pop('comparison_assessment_query', None)
                        if size(replacement) <= MAX_BYTES:
                            view = replacement
                    elif spending := project_spending_guidance(scoped, host):
                        compact = {k: v for k, v in item.items()
                                   if k not in ('example_matches', 'caliber_matches', 'catalog_entries', 'starter_questions')}
                        compact['project_spending_query'] = spending
                        compact['connection_revision'] = spending['connection_revision']
                        compact['business_project'] = spending['publication']
                        replacement = {**view, 'sources': [compact]}
                        if size(replacement) > MAX_BYTES:
                            for key in ('source_context', 'tables', 'metric_definitions', 'unavailable_metrics', 'semantic_metrics'):
                                compact.pop(key, None)
                                if size(replacement) <= MAX_BYTES:
                                    break
                        if size(replacement) <= MAX_BYTES:
                            view = replacement
                if (len(every) == 1 and not (args.table_name or args.entry_ref or args.metric_id)
                        and not view['sources'][0].get('project_spending_query')
                        and (budget := published_budget_guidance(scoped, host))):
                    condensed = {k: v for k, v in item.items() if k not in ('example_matches', 'caliber_matches', 'catalog_entries')}
                    trend = budget.get('closed_budget_trend')
                    guidance = {k: v for k, v in budget.items()
                                if k not in ('proof', 'historical_reference', 'closed_budget_trend')}
                    if trend:
                        guidance['closed_budget_trend'] = trend['query_request']
                    if reference := budget.get('historical_reference'):
                        guidance['historical_reference'] = {
                            'query_request': reference['query_request'], **budget['proof']['historical_reference']}
                    condensed['published_budget_guidance'] = guidance
                    replacement = {**view, 'sources': [condensed]}
                    if trend and size(replacement) > MAX_BYTES:
                        condensed['published_budget_guidance'] = {'kind': guidance['kind'],
                                                                  'closed_budget_trend': guidance['closed_budget_trend']}
                    if size(replacement) <= MAX_BYTES:
                        view = replacement
            notice=first_issuance_notice(scoped,ctx.host_evidence) if scoped else None
            if notice:item['first_issuance_evidence']=notice
            if scoped and __import__('re').search('合格率|三项.*合格|抽查单位',ctx.host_evidence.get('user_text','')):
                from .pack_quality import enabled
                from .semantic import Metric
                metrics=[t.get('config') for t in scoped.get('terms',[]) if (t.get('config') or {}).get('id')=='inspection_pass_rate']
                if metrics and enabled(scoped,Metric.model_validate(metrics[0])):
                    item['reconciliation_query']={'tool':'data_metric_query','source_id':item['id'],'metric_id':'inspection_pass_rate',
                        'instruction':'复核总体合格率、抽查单位数及三项全部合格数时，查询此指标。inspection_reconciliation返回同一次授权查询的分子、分母、明细复核率和冲突状态；正式指标被质量检查拦下时也可据此解释，勿用SQL绕过。'}
        from .historical_qualification import check_historical_units, qualification_spec
        for item in view.get('sources', []):
            scoped = next((s for s in planning['sources'] if s['id'] == item['id']), None)
            if scoped:
                try:
                    recipe = check_historical_units(scoped, ctx.host_evidence, registered=True)
                except ValueError:
                    continue  # Missing fields or a human choice must be resolved at execution.
                if recipe:
                    guarded = qualification_spec(scoped)['guarded_tables']
                    item['registered_query'] = {'tool':'data_sql_query', 'source_id':item['id'],
                        'registered_query':'historical_professional_units','title':'期末专业资质单位',
                        'guarded_tables':[t['name'] for t in scoped['tables'] if t['name'].split('.')[-1].lower() in guarded],
                        'authority':'inferred_template',
                        'assumptions':['等级与专业采用目录映射或完整标签','专业分隔方式已配置','地区字段为单一城市名称'],
                        'instruction':'可选的期末单位查询模板，计算口径仍待确认，并非唯一允许的SQL。先核对实际字段取值；数字编码、带等级前缀的多专业文本、地区路径或附加范围不适配时，用 purpose=explore 核对后自行生成 sql，以 purpose=answer 执行。不得省略用户条件或将空结果直接解释为没有业务数据。'}
        if len(every) == len(planning['sources']) == len(view.get('sources', [])) == 1 and not (
                args.table_name or args.entry_ref or args.metric_id):
            from .catalog_view import enrich_metrics
            from .independent_metric_comparison import independent_metric_comparison
            from .monthly_metric_query import published_monthly_metric_query
            described, = every
            metrics = enrich_metrics(described)
            key = 'independent_metric_comparison'
            plan = independent_metric_comparison(described, host, metrics)
            if not plan:
                key = 'published_monthly_metric_query'
                plan = published_monthly_metric_query(described, host, metrics)
            if plan:
                item, = view['sources']
                item[key] = plan
                if size(view) > MAX_BYTES:
                    compact = {k: v for k, v in item.items() if k not in ('example_matches', 'caliber_matches', 'catalog_entries')}
                    replacement = {**view, 'sources': [compact]}
                    if size(replacement) <= MAX_BYTES:
                        view = replacement
                    else:
                        item.pop(key)
        view['catalog_ref'] = ref
        view['expires_in_seconds'] = max(0, int(1800 - age))
        if len(view.get('sources', [])) == len(planning['sources']) == 1:
            source_id = view['sources'][0]['id']
            scoped = next((source for source in planning['sources'] if source['id'] == source_id), None)
            if scoped and (choice := dimension_business_choice(scoped, host)):
                from .catalog_view import attach_dimension_choice
                if len(every) == 1 and not (args.table_name or args.entry_ref or args.metric_id):
                    from .approval_event_scope import confirmed_grouping_choice
                    choice = confirmed_grouping_choice(scoped, host, choice)
                attach_dimension_choice(view, choice, single_authorized_source=len(every) == 1)
            elif scoped and len(every) == 1:
                from .cancellation_scope import ranking_plan as cancellation_ranking_plan
                from .event_dimension_scope import event_dimension_boundary
                from .monthly_group_share import catalog_plan as monthly_group_share_plan
                from .snapshot_forecast_scope import published_snapshot_history_query, snapshot_forecast_boundary
                if (not (args.table_name or args.entry_ref or args.metric_id)
                        and (boundary := annual_forecast_boundary(scoped, host))):
                    key = 'annual_forecast_boundary'
                elif boundary := event_dimension_boundary(scoped, host):
                    key = 'event_dimension_boundary'
                elif (not (args.table_name or args.entry_ref or args.metric_id)
                      and (boundary := published_snapshot_history_query(scoped, every[0], host))):
                    key = 'snapshot_history_query'
                elif boundary := snapshot_forecast_boundary(scoped, host):
                    key = 'snapshot_forecast_boundary'
                elif (not (args.table_name or args.entry_ref or args.metric_id)
                      and (boundary := cancellation_ranking_plan(scoped, host))):
                    key = 'cancellation_ranking_query'
                elif (not (args.table_name or args.entry_ref or args.metric_id)
                      and (boundary := monthly_group_share_plan(scoped, host))):
                    key = 'monthly_group_share_query'
                else:
                    key = None
                if key:
                    item = view['sources'][0]
                    item[key] = boundary
                    if size(view) > MAX_BYTES:
                        compact = {k: v for k, v in item.items() if k not in ('catalog_entries', 'example_matches')}
                        replacement = {**view, 'sources': [compact]}
                        if size(replacement) <= MAX_BYTES:
                            view = replacement
                        else:
                            item.pop(key)
            if (scoped and len(every) == 1 and host.get('user_ref')
                    and not (args.table_name or args.entry_ref or args.metric_id)
                    and not view['sources'][0].get('dimension_business_choice')
                    and (publication := scoped.get('business_project'))):
                from .historical_qualification import historical_query_recipe
                if recipe := historical_query_recipe(scoped, host):
                    item = view['sources'][0]
                    item['historical_qualification_query'] = {
                        'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source_id,
                        'publication': {key: publication[key] for key in ('id', 'version')},
                        'cutoff': recipe['cutoff'], 'subject': recipe['subject'], 'share': recipe['share'],
                        'query_request': {'source_id': source_id, 'title': recipe['title'],
                                          'sql': recipe['sql'], 'purpose': 'answer'},
                    }
                    if size(view) > MAX_BYTES:
                        compact = {k: v for k, v in item.items() if k not in (
                            'catalog_entries', 'example_matches', 'caliber_matches', 'registered_query')}
                        if size({**view, 'sources': [compact]}) > MAX_BYTES and compact.get('source_context'):
                            # A scoped query already binds this request to the authorized
                            # publication. Discovery examples remain available through paging.
                            compact['source_context'] = {k: v for k, v in compact['source_context'].items()
                                if k not in ('catalog_entries', 'example_matches', 'caliber_matches',
                                             'starter_questions', 'metric_names')}
                        if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                            view['sources'] = [compact]
                        else:
                            item.pop('historical_qualification_query')
                else:
                    from .historical_snapshot_query import certificate_catalog_boundary
                    if boundary := certificate_catalog_boundary(scoped, host, qualification_spec(scoped)):
                        item = view['sources'][0]
                        item['historical_certificate_boundary'] = boundary
                        if size(view) > MAX_BYTES:
                            compact = {k: v for k, v in item.items() if k not in ('catalog_entries', 'example_matches', 'caliber_matches')}
                            if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                                view['sources'] = [compact]
                            else:
                                item.pop('historical_certificate_boundary')
            if (scoped and len(every) == 1 and not (args.table_name or args.entry_ref or args.metric_id
                                                     or args.source_offset or args.column_offset or args.table_offset or args.metric_offset or args.entry_offset)
                    and not view['sources'][0].get('dimension_business_choice')):
                from .annual_budget_reference import comparison_budget_read, preferred_budget_read
                item = view['sources'][0]
                # Budget entry tokens are issued from the described catalog, which
                # retains the same authorized tables and published definitions.
                described, = value['sources']
                reference = item.get('budget_reference') or (item.get('source_context') or {}).get('budget_reference')
                key, recipe = 'preferred_budget_read', preferred_budget_read(described, host, reference)
                if not recipe:
                    key, recipe = 'comparison_budget_read', comparison_budget_read(described, host, reference)
                if recipe:
                    item[key] = recipe
                    if size(view) > MAX_BYTES:
                        compact = {k: v for k, v in item.items() if k not in (
                            'catalog_entries', 'example_matches', 'caliber_matches', 'starter_questions')}
                        compact['connection_revision'] = recipe['connection_revision']
                        for field in ('tables', 'source_context', 'unavailable_metrics'):
                            if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                                break
                            compact.pop(field, None)
                        if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                            view['sources'] = [compact]
                        else:
                            item.pop(key)
            if scoped and len(every) == 1 and not view['sources'][0].get('dimension_business_choice'):
                from .work_completion import inventory_query
                if recipe := inventory_query(scoped, host):
                    item = view['sources'][0]
                    item['work_inventory_query'] = recipe
                    if size(view) > MAX_BYTES:
                        compact = {**item}
                        for field in ('example_matches', 'caliber_matches', 'starter_questions'):
                            compact.pop(field, None)
                            if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                                view['sources'] = [compact]
                                break
                        else:
                            item.pop('work_inventory_query')
            if (scoped and len(every) == 1 and not (args.table_name or args.entry_ref or args.metric_id)
                    and not any(view['sources'][0].get(key) for key in (
                        'dimension_business_choice', 'independent_metric_comparison', 'historical_qualification_query',
                        'published_monthly_metric_query', 'current_execution_guidance', 'published_budget_guidance'))):
                from .formula_component_query import formula_component_query
                from .metric_search import exact_hits

                partial = formula_component_query(scoped, host)
                if partial and not exact_hits(host.get('user_text') or '', metrics):
                    item = view['sources'][0]
                    item['formula_component_query'] = partial
                    if size(view) > MAX_BYTES:
                        item.pop('formula_component_query')
            if (scoped and len(every) == 1 and args.view == 'full'
                    and not (args.source_id or args.table_name or args.entry_ref or args.metric_id or args.source_offset)
                    and not any(view['sources'][0].get(key) for key in (
                        'dimension_business_choice', 'published_grouping_query', 'independent_metric_comparison',
                        'historical_qualification_query', 'published_monthly_metric_query', 'current_execution_guidance',
                        'published_budget_guidance', 'formula_component_query'))):
                from .approval_event_scope import catalog_plan as approval_count_plan
                from .monthly_count_comparison import plan as monthly_comparison_plan
                from .observed_sql_scope import monthly_observation_plan
                from .published_scope import annual_monthly_count_query, relative_monthly_count_query
                from .qualification_scope import province_count_query
                from .ranking_observation import published_observed_ranking
                from .raw_dimension_scope import period_ranking_query
                from .service_event_scope import catalog_plan as service_completion_plan
                if recipe := monthly_observation_plan(engine, ctx, scoped, host, metrics):
                    key = 'monthly_observation_period'
                elif recipe := relative_monthly_count_query(engine, ctx, scoped, host):
                    key = 'relative_monthly_count_query'
                elif recipe := annual_monthly_count_query(scoped, host):
                    key = 'annual_monthly_count_query'
                elif recipe := approval_count_plan(engine, ctx, scoped, host):
                    key = 'approval_monthly_count_query'
                elif recipe := province_count_query(scoped, host):
                    key = 'province_snapshot_count_query'
                elif recipe := service_completion_plan(scoped, host):
                    key = 'service_completion_query'
                elif recipe := published_observed_ranking(scoped, host):
                    key = 'published_observed_ranking_query'
                elif recipe := period_ranking_query(engine, ctx, scoped):
                    key = 'raw_dimension_ranking_query'
                else:
                    recipe, key = monthly_comparison_plan(scoped, host), 'monthly_count_comparison_query'
                if recipe:
                    item = view['sources'][0]
                    item[key] = recipe
                    if size(view) > MAX_BYTES:
                        # The checked query outranks suggested examples on the first page.
                        compact = {k: v for k, v in item.items() if k not in
                                   ('example_matches', 'caliber_matches', 'catalog_entries', 'starter_questions')}
                        if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                            view['sources'] = [compact]
                        else:
                            item.pop(key)
            if (scoped and len(every) == 1 and args.view == 'full'
                    and not (args.table_name or args.entry_ref or args.metric_id or args.source_offset
                             or args.table_offset or args.column_offset or args.metric_offset)
                    and not any(every[0]['query_modes'][mode] for mode in ('semantic_metrics','legacy_metrics'))):
                item = view['sources'][0]
                if (not any(item.get(key) for key in ('dimension_business_choice','independent_metric_comparison',
                        'scope_matches','current_execution_guidance','annual_forecast_boundary'))
                        and (recipe := annual_investment_history_query(scoped, host))):
                    published = scoped['tables']
                    shown = item.get('tables', [])
                    mappings = scoped.get('business_dictionary', [])
                    terms = scoped.get('terms', [])
                    generated_terms = {'mapping-' + mapping['id'] for mapping in mappings}
                    complete = (len(published) == len(shown) == 1 and shown[0]['name'] == recipe['table']
                                and shown[0].get('columns') == published[0].get('columns')
                                and not any(scoped.get(key) for key in ('examples', 'relations', 'business_layers', 'business_skills'))
                                and all(term.get('id') in generated_terms for term in terms)
                                and all(mapping.get('kind') in ('time', 'field', 'table') for mapping in mappings))
                    if complete:
                        recipe['inline_catalog_complete'] = True
                    compact = {'annual_fiscal_history_query': recipe, **item}
                    if complete:
                        # The embedded table is the same authorized table entry, including its
                        # fields. Reading the table or its field references adds no evidence.
                        compact = {key: value for key, value in compact.items()
                                   if key not in ('starter_questions', 'table_detail', 'caliber_matches', 'catalog_entries')}
                        compact['tables'] = published
                        compact['terms'] = terms
                        compact['catalog_entries'] = []
                    replacement = {**view, 'sources': [compact]}
                    if complete and 'catalog_view' in replacement:
                        replacement['catalog_view'] = {**replacement['catalog_view'],
                            'instruction': '本页已列出该来源的全部授权表和字段。业务主体待核实时，核对已有字段和实际数据。'}
                    if size(replacement) <= MAX_BYTES:
                        view = replacement
        return view

    def prepare_query(args, ctx, *, purpose='answer', replaces_result_ref=None, assessment=None):
        from .temporal_scope import validate_year_end
        validate_year_end(getattr(ctx, 'host_evidence', None))
        artifact = engine.artifacts.get(ctx.project, args.catalog_ref)
        if artifact['kind'] != 'data.catalog' or time.time() - artifact['created_at'] > 1800:
            raise ValueError('目录引用类型不符或已过期，请重新调用 data_catalog。')
        source = engine.catalog(ctx.project, args.source_id)['sources']
        original = [s for s in artifact['payload']['sources'] if s['id'] == args.source_id]
        if not source or source != original:
            raise ValueError('数据源配置已变化或不属于该目录，请重新调用 data_catalog。')
        from .sql_syntax import validate_sql_syntax
        dialect = sqlglot_name(source[0]['dialect'])
        validate_sql_syntax(args.sql, dialect)
        from .row_count_aggregation import require_detail_query, require_grouping_query
        detail_proof = require_detail_query(args.sql, source[0], ctx.host_evidence, purpose=purpose)
        grouping_proof = require_grouping_query(engine, ctx, source[0], args.sql, purpose=purpose)
        require_available(source[0], title=args.title, question=question_of(ctx, args.question),
                          sql=args.sql, dialect=dialect, purpose=purpose)
        values = args.model_dump(exclude_none=True)
        for p in values.get('parameters', []):
            if p['type'] in ('integer', 'number'):
                p['value'] = int(p['value']) if p['type'] == 'integer' else float(p['value'])
            elif p['type'] == 'boolean':
                if p['value'] not in ('true', 'false'):
                    raise ValueError('布尔绑定参数必须是 true 或 false')
                p['value'] = p['value'] == 'true'
        from .source_choice import validate_source_choice
        from .onboarding import describe
        validate_source_choice(engine, ctx, args.source_id, describe)
        from .temporal_scope import normalize_sqlite_year_dates, validate_month_sql, validate_quarter_sql, validate_quarter_text_dates
        validate_month_sql(ctx.host_evidence, args.sql, source[0], purpose=purpose)
        executable_sql = args.sql
        if purpose != 'explore':
            validate_quarter_sql(ctx.host_evidence, args.sql, source[0], values.get('parameters'))
            executable_sql = validate_quarter_text_dates(engine, ctx, args.sql, source[0], values.get('parameters'),
                                                         recover=purpose == 'answer')
            if purpose == 'answer':
                executable_sql = normalize_sqlite_year_dates(engine, ctx, executable_sql, source[0])
        from .intent_contract import validate_acquisition_query
        validate_acquisition_query(args.sql, source[0], getattr(ctx, 'host_evidence', None), parameters=values.get('parameters'))
        from .task_contract import task_snapshot
        task, task_ref = task_snapshot(engine,ctx,args.task_id,args.task_revision)
        if task['fields'].get('query') != args.sql:
            raise ValueError('TASK_SCOPE_MISMATCH: 自由SQL必须与任务query字段完全一致。')
        if task['fields'].get('metric') or task['fields'].get('semantic'):
            raise ValueError('METRIC_REQUIRED: 当前任务已选择业务指标，请使用相应指标计划；独立基础查询另建query任务。')
        from .metric_registry import resolve_mapping
        try: _,mapping=resolve_mapping(engine.source(ctx.project,args.source_id))
        except ValueError: pass
        else:
            import sqlglot
            dialect=sqlglot_name(source[0]['dialect'])
            relations={t.name.lower() for t in sqlglot.parse_one(args.sql,read=dialect).find_all(sqlglot.exp.Table)}
            if purpose != 'explore' and any(table.split('.')[-1].lower() in relations for table in mapping['grain']):raise ValueError(METRIC_REQUIRED_SQL)
        # 0.7.0: after the legacy rule (so one question is never refused twice): a question that
        # names a published metric is answered with that metric, not with SQL aggregating its table.
        from .historical_qualification import check_historical_units, check_certificate_dates
        funding_proof = None
        if purpose != 'explore':
            from .business_ambiguity import validate_business_choice
            validate_business_choice(engine.source(ctx.project,args.source_id),ctx.host_evidence,sql=args.sql)
            check_certificate_dates(engine.source(ctx.project,args.source_id),ctx.host_evidence,args.sql,sqlglot_name(source[0]['dialect']),parameters=values.get('parameters'))
            from .business_sql_checks import require_enum_values
            from .enum_discovery import require_discovered_enum_values
            from .observed_sql_scope import require_monthly_population
            scoped_source = engine.source(ctx.project, args.source_id)
            require_enum_values(args.sql, scoped_source)
            require_discovered_enum_values(args.sql, scoped_source, engine=engine, ctx=ctx, parameters=values.get('parameters'))
            require_monthly_population(args.sql, scoped_source, ctx.host_evidence, engine, ctx, values.get('parameters'))
            from .fiscal_history import require_current_execution_period, require_observed_fiscal_history
            require_observed_fiscal_history(args.sql, source[0]['dialect'], ctx.host_evidence)
            funding_proof = require_current_execution_period(args.sql, source[0], ctx.host_evidence, values.get('parameters'))
        # Exploratory reads must be able to inspect date/grade/profession
        # values when a registered query returns no rows. They retain every
        # normal authorization and read-only check and are marked as explore.
        ranking=None if purpose=='explore' else check_historical_units(engine.source(ctx.project,args.source_id),ctx.host_evidence,sql=args.sql,dialect=sqlglot_name(source[0]['dialect']))
        if ranking: values['profession_ranking']=ranking
        from .metric_route import check_sql
        if purpose != 'explore':
            check_sql(engine, ctx, args.source_id, args.sql, sqlglot_name(source[0]['dialect']))
        from .pack_quality import check_inspection_route
        if purpose != 'explore':
            check_inspection_route(engine.source(ctx.project,args.source_id),ctx.host_evidence,sql=args.sql,dialect=sqlglot_name(source[0]['dialect']))
        from .sql_correlation import normalize_scalar_totals, validate_correlation
        validate_correlation(args.sql, source[0])
        if purpose == 'answer':
            executable_sql = normalize_scalar_totals(executable_sql, source[0], engine=engine, ctx=ctx)
        from .query_evidence import require_formula_basis, require_grouping_evidence, require_missing_values, require_result_evidence
        require_result_evidence(args.sql, sqlglot_name(source[0]['dialect']))
        if purpose == 'answer':
            from .work_completion import require_work_measure
            require_work_measure(args.sql, source[0], ctx.host_evidence.get('user_text', ''), title=args.title)
            require_missing_values(args.sql, dialect, ctx.host_evidence.get('user_text', ''))
            require_grouping_evidence(args.sql, source[0], ctx.host_evidence.get('user_text', ''))
            from .raw_value_grouping import require_raw_dimension_grouping
            require_raw_dimension_grouping(args.sql, source[0], ctx.host_evidence.get('user_text', ''),
                                           published_grouping=grouping_proof)
            require_formula_basis(args.sql, source[0], ctx.host_evidence.get('user_text', ''))
        from .request_scope import derive, require_valid
        from .task_contract import sql_period_evidence
        request_scope = derive(source[0], ctx.host_evidence, task.get('request_scope'),
                               engine=engine if purpose != 'explore' else None, ctx=ctx, sql=args.sql,
                               parameters=values.get('parameters'), published_grouping=grouping_proof)
        if purpose != 'explore':
            from .monthly_sql_scope import bind as bind_monthly_window
            if window := bind_monthly_window(ctx.host_evidence, args.sql, source[0]):
                request_scope['monthly_sql_window'] = window
        values['request_scope'] = request_scope
        from .result_lineage import bind as bind_lineage
        values['result_lineage'] = bind_lineage(engine,ctx,source[0],executable_sql,purpose,replaces_result_ref,values.get('parameters'))
        if purpose != 'explore':
            validation = require_valid(args.sql, source[0], request_scope, values.get('parameters'),
                                       published_grouping=grouping_proof, engine=engine, ctx=ctx)
            if detail_proof:
                validation['published_detail'] = detail_proof
            if grouping_proof:
                validation['published_grouping'] = grouping_proof
            if funding_proof:
                validation['current_execution'] = funding_proof
            if assessment:
                validation['assessment_rate'] = assessment
            values['validation'] = sql_period_evidence(engine, ctx, source[0], args.sql, validation)
        for k in ['task_id','task_revision']: values.pop(k,None)
        ref=put_bound_plan(engine,ctx.project,{'mode':'query','purpose':purpose,'query':values,'session_id':ctx.session_id,'task_id':args.task_id,'task_ref':task_ref,'task_revision':task.get('revision')},catalog_source=source,producer='data.plan@2.0.0',parents=[task_ref,args.catalog_ref])
        return {'plan_ref':ref,'task_id':args.task_id,'task_revision':args.task_revision}

    def prepare_report(args, ctx):
        from .temporal_scope import validate_year_end
        validate_year_end(getattr(ctx, 'host_evidence', None))
        artifact = engine.artifacts.get(ctx.project, args.catalog_ref)
        source = engine.source(ctx.project, args.source_id)
        if artifact['kind'] != 'data.catalog' or time.time()-artifact['created_at']>1800:
            raise ValueError('目录已过期或类型不正确')
        if source not in artifact['payload']['sources']:
            # Catalog intentionally excludes credentials and filesystem paths.
            current = engine.catalog(ctx.project,args.source_id)['sources']
            if current != [s for s in artifact['payload']['sources'] if s['id']==args.source_id]:
                raise ValueError('数据源配置已变化，请重新获取目录')
        require_available(source, metric_id=args.metric)
        query_args, contract = compile_report(args, source)
        from .business_ambiguity import validate_business_choice
        validate_business_choice(source,ctx.host_evidence)
        from .task_contract import task_snapshot, validate_report_task, preserve_delivery_dimensions
        task, task_ref = task_snapshot(engine,ctx,args.task_id,args.task_revision)
        validate_report_task(task,args,ctx.host_evidence)
        from .intent_contract import validate_host_intent, validate_conditional_delivery
        from .source_choice import validate_source_choice
        from .onboarding import describe
        validate_source_choice(engine, ctx, args.source_id, describe)
        from .intent_contract import validate_acquisition_query
        validate_acquisition_query(query_args['sql'], source, ctx.host_evidence, parameters=query_args.get('parameters'))
        validate_host_intent(args,ctx.host_evidence)
        validate_conditional_delivery(args,task,ctx.host_evidence,engine,ctx)
        preserve_delivery_dimensions(engine,ctx,task,args)
        ref=put_bound_plan(engine,ctx.project,{'mode':'report','query':query_args,'session_id':ctx.session_id,'task_id':args.task_id,'task_ref':task_ref,'task_revision':task.get('revision')},catalog_source=engine.catalog(ctx.project,args.source_id)['sources'],producer='data.plan@2.0.0',parents=[task_ref,args.catalog_ref])
        return {'plan_ref':ref,'task_id':args.task_id,'task_revision':args.task_revision,'metric_contract':contract}

    def prepare_semantic(args,ctx):
        import json
        from .temporal_scope import validate_year_end
        validate_year_end(getattr(ctx, 'host_evidence', None))
        from .task_contract import task_snapshot
        artifact=engine.artifacts.get(ctx.project,args.catalog_ref)
        current=engine.catalog(ctx.project,args.source_id)['sources']
        if artifact['kind']!='data.catalog' or time.time()-artifact['created_at']>1800 or current!=[s for s in artifact['payload']['sources'] if s['id']==args.source_id]:
            raise ValueError('STALE_CATALOG: 请重新获取目录')
        from .source_choice import validate_source_choice
        from .onboarding import describe
        validate_source_choice(engine, ctx, args.source_id, describe)
        task,task_ref=task_snapshot(engine,ctx,args.task_id,args.task_revision)
        selection=args.model_dump(exclude={'catalog_ref','title','question','chart_type','task_id','task_revision'})
        try:saved=json.loads(task['fields'].get('semantic','null'))
        except ValueError:raise ValueError('TASK_SCHEMA: semantic必须是JSON对象') from None
        if saved!=selection:raise ValueError('TASK_SCOPE_MISMATCH: semantic合约与任务不一致')
        semantic_source=engine.source(ctx.project,args.source_id)
        require_available(semantic_source, metric_id=args.metric_id, title=args.title)
        from .temporal_scope import validate_month_request
        validate_month_request(ctx.host_evidence, args.periods)
        from .business_ambiguity import validate_business_choice
        from .independent_metric_comparison import comparison_choice_context
        choice_context = comparison_choice_context(engine, ctx, semantic_source, args.metric_id, args.metric_version,
                                                   args.periods, args.group_by, args.filters)
        validate_business_choice(semantic_source,choice_context,args.metric_id)
        from .historical_qualification import check_historical_units
        check_historical_units(semantic_source,ctx.host_evidence,metric_id=args.metric_id)
        from .pack_quality import check_inspection_route
        check_inspection_route(semantic_source,ctx.host_evidence,metric_id=args.metric_id)
        query_args,contract=compile_plan(args,semantic_source)
        from .metric_subject import validate_counting_subject
        validate_counting_subject(semantic_source, contract, ctx.host_evidence)
        from .intent_contract import validate_acquisition_query
        validate_acquisition_query(query_args['sql'], current[0], ctx.host_evidence, compiled_metric=contract['semantic_spec'], periods=args.periods)
        ref=put_bound_plan(engine,ctx.project,{'mode':'semantic','query':query_args,'session_id':ctx.session_id,'task_id':args.task_id,'task_ref':task_ref,'task_revision':task.get('revision')},catalog_source=current,producer='data.semantic-plan@1.0.0',parents=[task_ref,args.catalog_ref])
        return {'plan_ref':ref,'task_id':args.task_id,'task_revision':args.task_revision,'metric_contract':contract}

    from .model_view import attach_values, for_model

    def report(args,ctx):
        from .task_contract import execute_bound
        return for_model(execute_bound(engine,ctx,args,'report'))

    def query(args,ctx):
        from .task_contract import execute_bound
        return for_model(attach_values(engine, ctx.project, execute_bound(engine,ctx,args,'query')))

    def fresh_catalog(ctx, source_id):
        planning = engine.catalog(ctx.project, source_id)
        if not any(s['id'] == source_id for s in planning['sources']):
            raise PermissionError('SOURCE_NOT_FOUND: 该数据源不在当前授权目录中，请使用 data_catalog 返回的 source_id。')
        ref, _ = engine.artifacts.put_recent(ctx.project, 'data.catalog', planning, producer='data.catalog@1.0.0', max_age=1200)
        return ref

    def question_of(ctx, title):
        return (ctx.host_evidence.get('user_text') or '').strip()[:10000] or title

    def deliver(ctx, task, revision, result, *, purpose='answer'):
        from .task_contract import mark_task_delivered, remember_turn_scope
        if result.get('status') == 'succeeded':
            stored = engine.get(ctx.project, result['result_ref'])
            scope_update = stored.get('request_scope') if purpose != 'explore' else None
            revision = mark_task_delivered(engine, ctx, task['task_id'], result['result_ref'], request_scope=scope_update) or revision
            if scope_update:
                remember_turn_scope(engine, ctx, scope_update, stored.get('source_id') or result.get('source_id'),
                                    host=ctx.host_evidence)
        return {**result, 'task_id': task['task_id'], 'task_revision': revision}

    def ask_sql(args, ctx):
        from .task_contract import upsert_query_task, execute_bound
        from .row_count_aggregation import require_grouping_query
        catalog_ref = fresh_catalog(ctx, args.source_id)
        if bool(args.sql) == bool(args.registered_query):
            raise ValueError('QUERY_CHOICE: 请在 sql 和 registered_query 中选择一项。自由查询使用参数 sql，值为完整 SELECT/CTE 语句，不是工具名称；根据已授权目录的字段和示例填写实际查询，不能省略 SQL 后声称已经执行。')
        if args.registered_query:
            from .historical_qualification import check_historical_units
            recipe = check_historical_units(engine.source(ctx.project,args.source_id),ctx.host_evidence,registered=True)
            if not recipe:
                raise ValueError('HISTORICAL_SCOPE: 本题或该来源没有可用的期末专业单位登记查询，请按授权目录和用户范围查询。')
            args = args.model_copy(update={'sql':recipe})
        assessment = None
        if (args.purpose or 'answer') == 'answer':
            from natural_analysis.forecasting import closed_annual_forecast_read
            from .business_ambiguity import canonicalize_confirmed_dimension_sql
            from .monthly_sql_scope import closed_forecast_read, normalize_full_year_count
            from .monthly_group_share import normalize_published_population as normalize_monthly_group_scope
            from .observed_sql_scope import assessed_rate_read, preserve_assessed_population, preserve_monthly_population
            from .ranking_observation import bound_annual_ranking
            from .service_event_scope import normalize_completion_month
            from .approval_event_scope import normalize_approval_population
            source = engine.source(ctx.project, args.source_id)
            normalized = canonicalize_confirmed_dimension_sql(args.sql, source, ctx.host_evidence)
            normalized = closed_forecast_read(normalized, source, ctx.host_evidence)
            normalized = closed_annual_forecast_read(normalized, source, ctx.host_evidence)
            normalized = preserve_monthly_population(normalized, source, ctx.host_evidence, engine, ctx)
            normalized, assessment = assessed_rate_read(normalized, source, ctx.host_evidence, engine, ctx)
            if assessment:
                args = args.model_copy(update={'title': assessment['year']+'年'+assessment['label']})
            else:
                normalized = preserve_assessed_population(normalized, source, ctx.host_evidence, engine, ctx)
            normalized = normalize_completion_month(normalized, source, ctx.host_evidence)
            normalized = normalize_approval_population(normalized, source, ctx.host_evidence)
            normalized = normalize_full_year_count(normalized, source, ctx.host_evidence)
            normalized = bound_annual_ranking(normalized, source, ctx.host_evidence)
            normalized = normalize_monthly_group_scope(normalized, source, ctx.host_evidence)
            if normalized != args.sql:
                args = args.model_copy(update={'sql': normalized})
            from .group_summary_sql import normalize_group_summary_sql
            grouped_sql = normalize_group_summary_sql(args.sql, source, ctx.host_evidence)
            if grouped_sql != args.sql:
                args = args.model_copy(update={'sql': grouped_sql})
        if (args.replaces_result_ref or args.task_id) and require_grouping_query(
                engine, ctx, engine.catalog(ctx.project, args.source_id)['sources'][0],
                args.sql, purpose=args.purpose or 'answer'):
            # A verified regroup is a fresh result. Preserve the earlier card
            # even when a model supplied its reference as a correction target.
            args = args.model_copy(update={'replaces_result_ref': None, 'task_id': None})
        purpose = args.purpose or 'answer'
        target_task_id = None if purpose == 'explore' else args.task_id
        task, revision = upsert_query_task(engine, ctx, target_task_id, args.title, {'query': args.sql}, clear=('metric', 'semantic', 'periods'), source_id=args.source_id, scope_proposal=args.scope_proposal)
        plan = prepare_query(PlanQuery(catalog_ref=catalog_ref, source_id=args.source_id, sql=args.sql, title=args.title,
                                       question=question_of(ctx, args.title), chart_type=args.chart_type,
                                       task_id=task['task_id'], task_revision=revision), ctx, purpose=purpose,
                             replaces_result_ref=args.replaces_result_ref, assessment=assessment)
        result = execute_bound(engine, ctx, Execute(task_id=task['task_id'], task_revision=revision, plan_ref=plan['plan_ref']), 'query')
        receipt = for_model(attach_values(engine, ctx.project, deliver(ctx, task, revision, result, purpose=purpose), purpose=args.purpose))
        from .metric_route import sql_hint
        source = engine.catalog(ctx.project, args.source_id)['sources']
        hint = sql_hint(engine, ctx, args.source_id, args.sql, sqlglot_name(source[0]['dialect'])) if source and receipt.get('status') == 'succeeded' else None
        return {**({'metric_hint': hint} if hint else {}), **receipt, 'purpose': args.purpose or 'answer'}

    def ask_metric(args, ctx):
        import json
        # 0.7.0: a metric the catalog only named can be queried directly. The version defaults to
        # the newest published one, and a rejected plan comes back with the metric's full definition.
        from .onboarding import metric_catalog
        from .catalog_view import enrich_metrics, metric_brief, version_key
        from .metric_search import strip_unit
        source_obj = engine.source(ctx.project, args.source_id)
        require_available(source_obj, metric_id=args.metric_id, title=args.title)
        published, unavailable = metric_catalog(source_obj)
        enriched = enrich_metrics({**source_obj, 'semantic_metrics': published})
        versions = sorted([m for m in enriched if m['id'] == args.metric_id], key=version_key, reverse=True)
        if not versions:
            target = args.metric_id.strip()
            exact_name_ids = {
                m['id'] for m in enriched
                if target in {
                    str(m.get('name') or '').strip(),
                    strip_unit(m.get('name'), m.get('unit')),
                    str(m.get('code') or '').strip(),
                } - {''}
            }
            alias_ids = {
                m['id'] for m in enriched
                if target in {str(a).strip() for a in (m.get('aliases') or []) if a}
            }
            resolved_ids = exact_name_ids if len(exact_name_ids) == 1 else (alias_ids if not exact_name_ids and len(alias_ids) == 1 else set())
            if len(resolved_ids) == 1:
                resolved_id = next(iter(resolved_ids))
                args = args.model_copy(update={'metric_id': resolved_id})
                versions = sorted([m for m in enriched if m['id'] == resolved_id], key=version_key, reverse=True)
        if not versions:
            from .metric_search import exact_hits, normalize
            question = (ctx.host_evidence or {}).get('user_text', '')
            require_available(source_obj, question=question)
            selected = normalize(args.metric_id).strip()
            invalid = [m for m in unavailable if m.get('state') == 'needs_configuration']
            identified = [m for m in invalid if selected in {
                normalize(m.get('id')), normalize(m.get('name')), normalize(strip_unit(m.get('name'), m.get('unit')))}]
            if not identified:
                identified = exact_hits(question, invalid)
            if identified:
                raise ValueError('METRIC_CONFIGURATION_REQUIRED: 当前所问的登记指标暂不可用。'
                                 '请核对同一来源目录中的 unavailable_metrics，保留已核实的其他部分。')
            matches = exact_hits(question, enriched)
            if matches:
                ids = '、'.join(dict.fromkeys(m['id'] for m in matches[:6]))
                raise ValueError('SEMANTIC_METRIC: 当前原问对应已发布指标 ' + ids +
                                 '。请按目录核对实际定义并使用对应的 metric_id，保留原期间和筛选。')
            if not source_obj.get('tables'):
                raise ValueError('METRIC_NOT_REGISTERED: 当前来源没有匹配的已发布指标，目录也没有可查的授权表。')
            raise ValueError('METRIC_NOT_REGISTERED: 当前没有匹配的已发布指标。请用已取得的 data_catalog 回执核对同一授权来源的相关表、字段、单位和关联，必要时读取表详情。'
                             '有依据即可用 data_sql_query 执行正式只读 SQL，完整工具档使用 data_plan_query 与 data_query。'
                             '沿用原问的对象、期间和筛选，基于正式回执核算；缺少分子、分母或关系时先交付已核实部分。')
        if not args.metric_version or not any(m['version'] == args.metric_version for m in versions):
            if args.metric_version and len(versions) > 1:
                raise ValueError('SEMANTIC_METRIC: 指标「' + args.metric_id + '」没有版本 ' + args.metric_version + '，已发布的版本有：' + '、'.join(m['version'] for m in versions) + '。')
            args = args.model_copy(update={'metric_version': versions[0]['version']})
        selected = next(m for m in versions if m['version'] == args.metric_version)
        catalog_ref = fresh_catalog(ctx, args.source_id)
        try:
            return ask_metric_checked(args, ctx, catalog_ref, source_obj)
        except ValueError as exc:
            msg = str(exc)
            if 'OUT_OF_COVERAGE' in msg:
                from .semantic import probe_uncovered_periods, CoverageError
                probe = probe_uncovered_periods(engine, source_obj, selected, args.periods)
                if probe and probe.get('summary'):
                    msg = msg + ' ' + probe['summary']
                    raise CoverageError(msg + ' 这个指标的完整口径：' + json.dumps(metric_brief(selected), ensure_ascii=False), probe) from None
            raise ValueError(msg + ' 这个指标的完整口径：' + json.dumps(metric_brief(selected), ensure_ascii=False)) from None

    def ask_metric_checked(args, ctx, catalog_ref, source):
        import json
        from .task_contract import upsert_query_task, execute_bound
        from .period_delivery import require_comparison, require_additive, complete_comparison
        current,baseline=(require_comparison(engine,ctx,args.comparison,args.periods,analysis_invoke)
                          if args.comparison else (list(args.periods),[]))
        selection = {'source_id': args.source_id, 'metric_id': args.metric_id, 'metric_version': args.metric_version,
                     'periods': sorted(current+baseline) if args.comparison else current, 'group_by': list(args.group_by), 'filters': [f.model_dump() for f in args.filters]}
        task, revision = upsert_query_task(engine, ctx, args.task_id, args.title, {'semantic': json.dumps(selection, ensure_ascii=False)},
                                           clear=('metric', 'query', 'periods'))
        plan = prepare_semantic(SemanticPlan(catalog_ref=catalog_ref, title=args.title, question=question_of(ctx, args.title),
                                             chart_type=args.chart_type, task_id=task['task_id'], task_revision=revision, **selection), ctx)
        if args.comparison:require_additive(plan['metric_contract'])
        result = execute_bound(engine, ctx, Execute(task_id=task['task_id'], task_revision=revision, plan_ref=plan['plan_ref']), 'report')
        if args.comparison and result.get('status')=='succeeded':
            result={**result,**complete_comparison(engine,ctx,result,args.comparison,current,baseline,analysis_invoke)}
        receipt = for_model(deliver(ctx, task, revision, result))
        if not args.comparison:
            from natural_analysis.forecasting import snapshot_history_coverage
            if coverage := snapshot_history_coverage(result, source, ctx.host_evidence or {}):
                receipt['validation'] = {**(receipt.get('validation') or {}), 'snapshot_forecast_history': coverage}
        from .temporal_scope import metric_month_request
        requested = metric_month_request(ctx.host_evidence)
        if requested is not None:
            receipt['answer_contract'] = {**(receipt.get('answer_contract') or {}), 'requested_months': sorted(requested)}
        return receipt

    def analyze(args, ctx):
        return engine.analyze(ctx.project, args.result_ref, args.operators, args.metric_column)

    def visualize(args, ctx):
        result = engine.visualize(ctx.project, args.result_ref, args.kind, args.expected_revision)
        if (result.get('answer_contract') or {}).get('published_grouping'):
            result = attach_values(engine, ctx.project, result)
        return for_model(result)

    def inspect(args, ctx):
        value = engine.get(ctx.project, args.result_ref)
        return for_model({**engine.public(value), 'available_views': value.get('chart', {}).get('allowedAlternatives', [])})

    def derive_aggregation(args, ctx):
        from .derived_aggregation import derive
        return for_model(derive(engine, ctx.project, args, ctx.host_evidence))

    def export(args, ctx):
        value = engine.get(ctx.project, args.result_ref)
        if value['status'] != 'succeeded':
            raise ValueError('只能导出成功的查询结果')
        return {**engine.public(value), 'export_format': 'csv', 'note': '下载入口提供给用户；原始数据不能读取到模型上下文。'}

    return Capability('data', '2.4.0', '问数', (
        Operation('catalog', '查看数据目录', '发现项目授权的数据源、字段、业务口径，生成短期有效的目录引用。查询前必须调用。', Catalog, catalog, 'data.read', 'data.catalog', CatalogOutput),
        Operation('plan', '准备兼容指标计划', '仅当当前来源目录metric_definitions包含该指标时使用。新客户无此定义时使用基础查询或已发布结构化指标。绑定任务最新修订，条件必须一致。', PlanReport, prepare_report, 'data.read', 'data.bound-plan', PlanOutput),
        Operation('plan_query', '准备基础查询计划', '新数据库完成授权即可使用，无需预设指标。按真实目录编写只读SQL，任务只保存query，不要求metric或periods。已选择的业务指标和旧映射表仍走指标计划。', PlanQuery, prepare_query, 'data.read', 'data.bound-plan', PlanOutput),
        Operation('semantic_plan','准备复杂指标计划','选择目录内已发布的semantic_metrics指标及版本，支持多操作数、复合关联、快照、加权和比例。任务semantic字段保存source_id、metric_id、metric_version、periods、group_by、filters的JSON。不得自行编造口径。',SemanticPlan,prepare_semantic,'data.read','data.bound-plan',PlanOutput),
        Operation('report', '按指标查询', '优先使用版本化指标查询：明确期间、分组、筛选、名单和阈值。返回维度与数值配对事实；仅已登记数据源可用，不得跨源替代。', Execute, report, 'data.read', 'data.dataset', ResultOutput),
        Operation('query', '安全查询', '根据有效目录引用执行只读查询。返回结果与视图版本；原始明细只向用户展示。', Execute, query, 'data.read', 'data.dataset', ResultOutput),
        Operation('analyze', '统计分析', '对成功结果执行确定性统计，生成独立分析产物。', Analyze, analyze, 'data.read', 'data.analysis', ResultOutput),
        Operation('visualize', '调整图表', '只调整展示，不重新查库。携带最新 view_revision；不兼容或版本冲突时保留原图。', Visualize, visualize, 'data.read', 'data.view', ResultOutput),
        Operation('sql_query', '基础查询', '原问无匹配的已发布指标时，按当前目录的授权表和实际字段编写只读 SQL。一次完成建任务、校验与执行，回执 values 含数值。列别名用中文业务名。新问题不填task_id；改条件时填原task_id；仅核对字段或取值时purpose填explore。', AskSql, ask_sql, 'data.read', 'data.dataset', ResultOutput),
        Operation('metric_query', '业务指标查询', '仅查询当前目录 semantic_metrics 中适用本问的已发布指标，返回带维度的数值 facts。查不到匹配的已发布指标时，读取同一授权来源的明细字段后做基础查询。', AskMetric, ask_metric, 'data.read', 'data.dataset', ResultOutput),
        Operation('derive_aggregation', '受控派生汇总', '基于已成功的可加指标事实或有发布依据的完整逐行计数结果，按用户原生确认的映射求和，供正文、表格和饼图复用。收到 NEEDS_CLARIFICATION 时把返回的 id 和完整提案原样传给 ask_user_question，用户确认后以相同结果和映射重试。未知、重叠、遗漏及非可加口径拒绝；保留原指标和已发布配置。', DeriveAggregation, derive_aggregation, 'data.read', 'data.derived', ResultOutput),
        Operation('inspect', '查看结果状态','读取结果状态、最新视图版本与可用图形，不返回原始明细。冲突后先调用。', Inspect, inspect, 'data.read', 'data.result-summary', ResultOutput),
    ))
