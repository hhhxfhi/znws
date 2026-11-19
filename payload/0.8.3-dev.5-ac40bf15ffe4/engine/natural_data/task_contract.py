"""Data execution bindings. Generic work state remains domain-agnostic."""
import hashlib
import json
import re
import time
from natural_work.store import ConflictError, encode


def put_bound_plan(engine, project, payload, *, catalog_source, producer, parents):
    # Scope the stable key by project. A catalog change creates a new snapshot.
    kind = 'data.catalog-source-snapshot'
    snapshot_ref = hashlib.sha256(encode([kind, project, catalog_source]).encode()).hexdigest()
    with engine.artifacts.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        engine.artifacts.put(project, kind, catalog_source, producer=kind + '@1.0.0',
                             ref=snapshot_ref, connection=connection)
        return engine.artifacts.put(project, 'data.bound-plan',
                                    {**payload, 'catalog_source_ref': snapshot_ref},
                                    producer=producer, parents=[*parents, snapshot_ref],
                                    connection=connection)


def bound_catalog_source(engine, project, plan):
    payload = plan['payload']
    if 'catalog_source' in payload:
        return payload['catalog_source']
    ref = payload.get('catalog_source_ref')
    if ref not in plan['parents']:
        raise ValueError('STALE_SOURCE: 查询计划缺少数据源目录快照，需重新计划。')
    snapshot = engine.artifacts.get(project, ref)
    if snapshot['kind'] != 'data.catalog-source-snapshot':
        raise ValueError('STALE_SOURCE: 查询计划的数据源目录快照类型有误，需重新计划。')
    return snapshot['payload']


def task_snapshot(engine, ctx, task_id, revision):
    if not ctx.session_id:
        raise ValueError('TASK_REQUIRED: 执行必须绑定宿主会话。')
    current, head = engine.artifacts.head(ctx.project, 'session:'+ctx.session_id, 'work.tasks')
    task = next((t for t in (head['payload']['tasks'] if head else []) if t['task_id']==task_id), None)
    if task is None:
        raise ValueError('TASK_REQUIRED: 本会话没有这个任务；请用 work_read 查看任务，或用 work_create(capability_id="data") 新建。')
    if task['capability_id']!='data':
        # 0.6.2: small models copied the catalog's metric_mapping (e.g. provincial-monthly/1)
        # into capability_id and then retried blindly; say exactly what to do.
        raise ValueError('TASK_REQUIRED: 任务的 capability_id 必须是 "data"（当前是 "'+str(task['capability_id'])[:60]+'"，这不是能力编号）。'
                         '请用 work_create(capability_id="data", set_fields 写入 metric 和 periods) 新建任务，再用新的 task_id 调用。')
    if revision < task.get('revision',current) or revision>current:
        raise ConflictError('STALE_TASK: 本任务条件已变化，请 work_read 获取最新修订并重新生成计划。')
    if task['state'] not in ['ready','running','partially_delivered','delivered']:
        raise ValueError('NEEDS_CLARIFICATION: 待澄清、阻塞或取消的任务不能执行。')
    validate_dependency(engine, ctx, task)
    return task, head['ref']

def session_branch_claim(engine, ctx):
    """0.6.3: the latest branch judgement (analysis_compute branch_compare) made in
    this session, or None. Evidence only: the claim's source result must come from a
    plan bound to this session."""
    store=engine.artifacts
    with store.connect() as c:
        rows=c.execute("SELECT ref,payload FROM artifacts WHERE project=? AND kind='analysis.claims' ORDER BY created_at DESC LIMIT 40",(ctx.project,)).fetchall()
    for ref,payload in rows:
        try:payload=json.loads(payload)
        except (TypeError,ValueError):continue
        branch=[x for x in payload.get('claims',[]) if x.get('operation')=='branch_compare' and type(x.get('matched')) is bool]
        if len(branch)!=1 or not payload.get('source_result'):continue
        try:
            result=engine.get(ctx.project,payload['source_result'])
            plan=store.get(ctx.project,result['plan_ref'])
            bound=[store.get(ctx.project,p) for p in plan['parents']]
        except Exception:continue
        if any(b['kind']=='data.bound-plan' and b['payload'].get('session_id')==ctx.session_id for b in bound):
            return {'artifact_ref':ref,'claim_id':branch[0]['claim_id'],'matched':branch[0]['matched']}
    return None

BRANCH_SHAPE=('BRANCH_SCHEMA: branch_dependency 的格式应为 {"kind":"claim","artifact_ref":"<analysis_compute 返回的 artifact_ref>","claim_id":"<claim_id>","expected":true}。'
 'analysis_compute(operation=branch_compare) 的回执里有现成的 branch_dependency，原样写入即可。')

def resolve_branch_shorthand(engine, ctx, dependency):
    """0.6.3: small models often store the comparison call itself (result_ref,
    operation, fact ids) instead of the claim reference. Resolve it to the one
    branch judgement already published for that result; the branch still runs only
    when that judgement matched, so nothing is taken on the model's word."""
    if dependency.get('kind') in ('claim','no_match'):return dependency
    ref=dependency.get('artifact_ref')
    if ref:
        try:artifact=engine.artifacts.get(ctx.project,ref)
        except Exception:artifact=None
    else:
        result_ref=dependency.get('result_ref')
        if not result_ref:raise ValueError(BRANCH_SHAPE)
        try:_,head=engine.artifacts.head(ctx.project,result_ref,'analysis.claims')
        except Exception:head=None
        artifact=None
        for parent in (head or {}).get('parents',[]) if head else []:
            try:candidate=engine.artifacts.get(ctx.project,parent)
            except Exception:continue
            if candidate.get('kind')=='analysis.claims':artifact={**candidate,'ref':parent}
        ref=artifact and artifact.get('ref')
    if not artifact or artifact.get('kind')!='analysis.claims':raise ValueError(BRANCH_SHAPE)
    branch=[c for c in artifact['payload'].get('claims',[]) if c.get('operation')=='branch_compare' and type(c.get('matched')) is bool]
    if len(branch)!=1:raise ValueError(BRANCH_SHAPE)
    expected=dependency.get('expected',True)
    return {'kind':'claim','artifact_ref':ref,'claim_id':branch[0]['claim_id'],'expected':expected if type(expected) is bool else True}

def validate_dependency(engine, ctx, task):
    """Evaluate immutable evidence, never a model-authored boolean assertion."""
    raw=task['fields'].get('branch_dependency')
    if not raw:return
    try: dependency=json.loads(raw)
    except (TypeError,ValueError):raise ValueError('BRANCH_SCHEMA: 分支依赖必须是JSON对象。') from None
    if not isinstance(dependency,dict):raise ValueError('BRANCH_SCHEMA: 分支依赖必须是JSON对象。')
    dependency=resolve_branch_shorthand(engine,ctx,dependency)
    kind=dependency.get('kind')
    if kind=='no_match':
        if set(dependency)!={'kind','result_ref'}:raise ValueError('BRANCH_SCHEMA: no_match需要result_ref。')
        result=engine.get(ctx.project,dependency['result_ref'])
        allowed=result.get('status')=='succeeded' and result.get('reason_code')=='NO_MATCH' and result.get('row_count')==0
    elif kind=='claim':
        if set(dependency)!={'kind','artifact_ref','claim_id','expected'} or type(dependency['expected']) is not bool:
            raise ValueError('BRANCH_SCHEMA: claim需要artifact_ref、claim_id和布尔expected。')
        artifact=engine.artifacts.get(ctx.project,dependency['artifact_ref'])
        if artifact['kind']!='analysis.claims':raise ValueError('BRANCH_SCHEMA: 必须引用分析结论产物。')
        matches=[c for c in artifact['payload'].get('claims',[]) if c.get('claim_id')==dependency['claim_id']]
        if len(matches)!=1 or type(matches[0].get('matched')) is not bool:
            raise ValueError('BRANCH_SCHEMA: 分支必须引用确定性阈值判断。')
        result=engine.get(ctx.project,artifact['payload']['source_result'])
        allowed=matches[0]['matched'] is dependency['expected']
    else:raise ValueError(BRANCH_SHAPE)
    # Evidence must originate in a plan belonging to this session, not a different chat.
    plan=engine.artifacts.get(ctx.project,result['plan_ref'])
    parents=[engine.artifacts.get(ctx.project,p) for p in plan['parents']]
    bound=[p for p in parents if p['kind']=='data.bound-plan']
    if len(bound)!=1 or bound[0]['payload'].get('session_id')!=ctx.session_id:
        raise ValueError('BRANCH_SCOPE: 依赖不属于当前会话的绑定计划。')
    origin=bound[0]['payload']
    _,head=engine.artifacts.head(ctx.project,'session:'+ctx.session_id,'work.tasks')
    parent=next((t for t in (head['payload']['tasks'] if head else []) if t['task_id']==origin['task_id']),None)
    snapshot=engine.artifacts.get(ctx.project,origin['task_ref'])
    original=next((t for t in snapshot['payload']['tasks'] if t['task_id']==origin['task_id']),None)
    # 0.6.3: a small model often turns the totals task itself into the drill-down
    # task. Its fields then change by design; the judgement on the earlier, immutable
    # result still decides the branch.
    self_bound=origin['task_id']==task.get('task_id')
    if not parent or not original or (not self_bound and parent['fields']!=original['fields']) or parent['state'] in ['cancelled','needs_clarification','blocked']:
        raise ConflictError('STALE_BRANCH: 前置任务已改变，必须重新计算分支。')
    if not allowed:raise ValueError('BRANCH_NOT_TAKEN: 前置结果不满足分支条件，禁止执行。')

def saved_periods(saved):
    """0.6.3: a task may hold periods as a JSON array or, as small models often write,
    a plain list such as "2024-12,2025-01" or "2025". Read both the same way."""
    if not saved:return None
    if isinstance(saved,list):return [str(x) for x in saved]
    try:value=json.loads(saved)
    except (TypeError,ValueError):value=None
    if isinstance(value,list):return [str(x) for x in value]
    if isinstance(value,(int,str)):saved=str(value)
    found=re.findall(r'\d{4}(?:-\d{2})?',str(saved))
    if not found:raise ValueError('TASK_SCHEMA: periods 写成 JSON 数组，例如 ["2025"] 或 ["2025-11","2025-12"]。')
    return found

def validate_report_task(task, args, host=None):
    fields=task['fields']
    for key in ['metric','periods','city','grade','profession','category']:
        supplied=getattr(args,key)
        saved=fields.get(key)
        if key=='periods':
            saved=saved_periods(saved)
        if supplied != saved:
            raise ValueError('TASK_SCOPE_MISMATCH: '+key+' 与已保存任务不一致；先修订该任务，独立指标必须新建子任务。')
    # Known contradictory inputs cannot be made ready by a model declaration.
    q=(host or {}).get('user_text') or args.question
    conflict=re.search(r'只看([甲乙])级[^。；;]*[，,][^。；;]*([乙甲])级',q)
    if conflict and conflict[1]!=conflict[2]:
        from .intent_contract import validate_grade_confirmation
        validate_grade_confirmation(args.grade,host or {},required=True)
    from .temporal_scope import validate_month_request
    validate_month_request(host or {'user_text':q}, args.periods)

def sql_period_evidence(engine, ctx, source, sql, validation):
    from .approval_event_scope import approval_date_evidence
    from .annual_budget_reference import report_spending_evidence
    from .catalog_index import fingerprint
    from .fiscal_history import annual_fiscal_window
    from .fiscal_report_budget import report_budget_evidence
    from .period_grain import require_grain, sql_evidence
    from .service_event_scope import completion_period_evidence
    host = ctx.host_evidence or {}
    if approval := approval_date_evidence(sql, source, host):
        validation = {**validation, 'date_role': approval}
    evidence = sql_evidence(sql, source, host)
    if (evidence and evidence['reason']=='missing_time_axis' and host.get('user_ref')
            and ctx.session_id):
        config = fingerprint(source)
        for ref in (host.get('current_answer_refs') or [])[-4:]:
            if not isinstance(ref, str):
                continue
            try:
                prior = engine.raw(ctx.project, ref)
            except PermissionError:
                continue
            lineage = prior.get('result_lineage') or {}
            proof = (prior.get('validation') or {}).get('period_grain') or {}
            if (prior['status']=='succeeded' and not prior.get('truncated') and prior.get('row_count',0)
                    and proof.get('state')=='checked' and proof.get('actual')==evidence['requested']
                    and lineage.get('session_id')==ctx.session_id and lineage.get('request_ref')==host['user_ref']
                    and lineage.get('source_id')==source['id'] and lineage.get('config_fingerprint')==config
                    and lineage.get('purpose')=='answer'):
                evidence = {**evidence, 'state':'auxiliary', 'source_result_ref':ref}
                break
    proof = require_grain(evidence)
    if proof:
        validation = {**validation, 'period_grain': proof}
    budget = report_budget_evidence(sql, source, host)
    if budget:
        validation = {**validation, 'published_report_budget': budget}
    if spending := report_spending_evidence(sql, source, host):
        validation = {**validation, 'published_report_spending': spending}
    if completion := completion_period_evidence(sql, source, host):
        validation = {**validation, 'completion_period': completion}
    if fiscal := annual_fiscal_window(host, sql=sql, source=source):
        if budget and budget['period'] != 'historical_reference':
            fiscal.update(time_column=budget['time_column'], measurements=budget['measurements'],
                          request_ref=host['user_ref'], source_id=source['id'],
                          publication={'id':budget['project_id'], 'version':budget['project_version']})
        validation = {**validation, 'annual_fiscal_window': fiscal}
    return validation


def metric_period_evidence(periods, host, validation=None):
    from .period_grain import metric_evidence, require_grain
    proof = require_grain(metric_evidence(periods, host))
    if not proof:
        return validation
    return {**(validation or {'schema_version':1,'state':'unknown','label':'尚无法验证','checks':[]}),
            'period_grain':proof}


def execute_bound(engine, ctx, args, mode):
    task, task_ref=task_snapshot(engine,ctx,args.task_id,args.task_revision)
    artifact=engine.artifacts.get(ctx.project,args.plan_ref)
    payload=artifact['payload']
    if artifact['kind']!='data.bound-plan' or payload.get('mode') not in ('report','query','semantic'):
        raise ValueError('PLAN_TYPE_MISMATCH: 计划类型不符。')
    if payload.get('session_id')!=ctx.session_id or payload.get('task_id')!=args.task_id or payload.get('task_revision')!=task.get('revision'):
        raise ConflictError('STALE_TASK: 计划不属于本任务最新修订。')
    catalog=engine.artifacts.get(ctx.project,payload['query']['catalog_ref'])
    if time.time()-catalog['created_at']>1800:raise ValueError('STALE_CATALOG: 目录已过期，请重新计划。')
    source=engine.catalog(ctx.project,payload['query']['source_id'])['sources']
    if source != bound_catalog_source(engine, ctx.project, artifact):
        raise ConflictError('STALE_SOURCE: 数据源配置已变化，需重新计划。')
    # Saved plans may be executed from a later user turn with a different scope.
    from .metric_limitations import require_available
    from .vendor.dialect import sqlglot_name
    query_plan = payload['query']
    execution_sql = query_plan.get('sql')
    purpose = payload.get('purpose', 'answer')
    metric = query_plan.get('metric_contract') or {}
    require_available(source[0], metric_id=metric.get('id', ''), title=query_plan.get('title', ''),
                      question=ctx.host_evidence.get('user_text') or query_plan.get('question', ''),
                      sql=query_plan.get('sql', ''), dialect=sqlglot_name(source[0]['dialect']), purpose=purpose)
    if payload.get('purpose') != 'explore':
        from .business_ambiguity import validate_business_choice
        semantic = payload['query'].get('metric_contract') or {}
        choice_source = engine.source(ctx.project,payload['query']['source_id'])
        choice_context = ctx.host_evidence
        if payload['mode'] == 'semantic':
            from .independent_metric_comparison import comparison_choice_context
            scope = semantic.get('scope') or {}
            choice_context = comparison_choice_context(engine, ctx, choice_source, semantic.get('id'),
                semantic.get('version'), semantic.get('periods', []), scope.get('dimensions'), scope.get('filters'))
        validate_business_choice(choice_source,choice_context,
                                 semantic.get('id') if isinstance(semantic,dict) else None,
                                 sql=query_plan.get('sql') if payload['mode']=='query' else None)
    from .temporal_scope import normalize_sqlite_year_dates, validate_month_request, validate_month_sql, validate_quarter_sql, validate_quarter_text_dates
    active_scope = None
    if payload['mode'] == 'query':
        from .row_count_aggregation import require_detail_query, require_grouping_query
        detail_proof = require_detail_query(payload['query']['sql'], source[0], ctx.host_evidence, purpose=purpose)
        grouping_proof = require_grouping_query(engine, ctx, source[0], payload['query']['sql'], purpose=purpose)
        validate_month_sql(ctx.host_evidence, payload['query']['sql'], source[0], purpose=purpose)
        if payload.get('purpose') != 'explore':
            validate_quarter_sql(ctx.host_evidence, payload['query']['sql'], source[0], payload['query'].get('parameters'))
            # Keep the saved plan's exact request SQL. Revalidate and recover
            # only against this execution's current request and evidence.
            execution_sql = validate_quarter_text_dates(engine, ctx, query_plan['sql'], source[0],
                                                        query_plan.get('parameters'), recover=purpose == 'answer')
            from .historical_qualification import check_certificate_dates
            from .business_sql_checks import require_enum_values, DIALECTS
            from .enum_discovery import require_discovered_enum_values
            from .observed_sql_scope import require_monthly_population
            scoped_source=engine.source(ctx.project,payload['query']['source_id'])
            dialect=source[0].get('dialect')
            from .query_evidence import require_formula_basis, require_grouping_evidence, require_missing_values
            if purpose == 'answer':
                from .sql_correlation import normalize_scalar_totals
                execution_sql = normalize_sqlite_year_dates(engine, ctx, execution_sql, source[0])
                execution_sql = normalize_scalar_totals(execution_sql, source[0], engine=engine, ctx=ctx)
                require_missing_values(query_plan['sql'], sqlglot_name(dialect), ctx.host_evidence.get('user_text', ''))
                require_grouping_evidence(query_plan['sql'], scoped_source, ctx.host_evidence.get('user_text', ''))
                from .raw_value_grouping import require_raw_dimension_grouping
                require_raw_dimension_grouping(query_plan['sql'], scoped_source, ctx.host_evidence.get('user_text', ''),
                                               published_grouping=grouping_proof)
                require_formula_basis(query_plan['sql'], scoped_source, ctx.host_evidence.get('user_text', ''))
            check_certificate_dates(scoped_source,ctx.host_evidence,payload['query']['sql'],DIALECTS.get(dialect,dialect))
            require_enum_values(payload['query']['sql'],scoped_source)
            require_discovered_enum_values(payload['query']['sql'], scoped_source, engine=engine, ctx=ctx, parameters=payload['query'].get('parameters'))
            require_monthly_population(payload['query']['sql'], scoped_source, ctx.host_evidence, engine, ctx,
                                       payload['query'].get('parameters'))
            from .fiscal_history import require_current_execution_period, require_observed_fiscal_history
            require_observed_fiscal_history(payload['query']['sql'], dialect, ctx.host_evidence)
            funding_proof = require_current_execution_period(payload['query']['sql'], source[0], ctx.host_evidence,
                                                             payload['query'].get('parameters'))
            from .request_scope import derive, require_valid
            active_scope = derive(source[0], ctx.host_evidence, payload['query'].get('request_scope'),
                                  engine=engine, ctx=ctx, sql=payload['query']['sql'],
                                  parameters=payload['query'].get('parameters'), published_grouping=grouping_proof)
            from .monthly_sql_scope import bind as bind_monthly_window
            if window := bind_monthly_window(ctx.host_evidence, payload['query']['sql'], source[0]):
                active_scope['monthly_sql_window'] = window
            active_validation = require_valid(payload['query']['sql'], source[0], active_scope,
                                              payload['query'].get('parameters'), published_grouping=grouping_proof, engine=engine, ctx=ctx)
            if detail_proof:
                active_validation['published_detail'] = detail_proof
            if grouping_proof:
                active_validation['published_grouping'] = grouping_proof
            if funding_proof:
                active_validation['current_execution'] = funding_proof
            assessment = (query_plan.get('validation') or {}).get('assessment_rate') or {}
            if (assessment.get('request_ref') and assessment['request_ref'] == ctx.host_evidence.get('user_ref')
                    and assessment.get('source_id') == source[0].get('id')):
                active_validation['assessment_rate'] = assessment
            active_validation = sql_period_evidence(engine, ctx, source[0], payload['query']['sql'], active_validation)
    else:
        validate_month_request(ctx.host_evidence, payload['query']['metric_contract'].get('periods') or [])
    REPEATS=engine.__dict__.setdefault('_repeat_guard',{})
    query={**query_plan,'bound_plan_ref':artifact['ref'],'expected_catalog_source':source}
    if payload['mode']=='query':
        query['sql']=execution_sql
    key=repeat_key(ctx,query)
    previous=REPEATS.get(key)
    now=time.time()
    if previous and now-previous['last']<=REPEAT_WINDOW and previous['count']>=REPEAT_LIMIT:
        # Receipts carry no row values by design, so a model that wants the
        # numbers may re-run the same query in a loop. Stop that and say how to
        # read values instead of hitting the customer database again.
        raise ValueError('REPEATED_QUERY: 本会话刚以完全相同的条件成功查询过 '+str(previous['count'])+' 次，结果 result_ref='+previous['result_ref']+' 仍可用，不再重复访问数据库。'
                         '上次回执的 values 已含结果数值，请直接据此回答；values 不完整时用 analysis_field_statistics 做整体统计。')
    if active_scope is not None:
        query.update(request_scope=active_scope, validation=active_validation)
    elif payload['mode']!='query':
        metric_validation = metric_period_evidence(metric.get('periods') or [], ctx.host_evidence, query.get('validation'))
        if metric_validation:
            query['validation'] = metric_validation
    execution_key='bound:'+artifact['ref']
    if payload['mode']=='query':
        from .result_lineage import bind
        from .catalog_index import fingerprint
        query['result_lineage']=bind(engine,ctx,source[0],query['sql'],payload.get('purpose','answer'),
            (query.get('result_lineage') or {}).get('replaces_result_ref'),query.get('parameters'))
        if retry_ref := ctx.host_evidence.get('sql_timeout_retry_ref'):
            query['sql_timeout_retry_ref'] = retry_ref
        # A saved plan can be run by another request. Its cached receipt must
        # not retain the previous turn's correction authority.
        execution_key+=':request:'+fingerprint(query['result_lineage'])
        if query['sql'] != query_plan['sql']:
            execution_key+=':normalized:'+fingerprint(query['sql'])
    elif proof := (query.get('validation') or {}).get('period_grain'):
        from .catalog_index import fingerprint
        execution_key+=':grain:'+fingerprint(proof)
    result=engine.query(ctx.project,query,execution_key)
    if payload['mode']=='query' and purpose=='answer':
        validate_quarter_text_dates(engine,ctx,query['sql'],source[0],query.get('parameters'),result=result)
    if result.get('status')=='succeeded':
        entry=REPEATS.get(key) if previous and now-previous['last']<=REPEAT_WINDOW else None
        REPEATS[key]={'count':(entry['count'] if entry else 0)+1,'last':now,'result_ref':result['result_ref']}
        for stale in [k for k,v in REPEATS.items() if now-v['last']>REPEAT_WINDOW]:REPEATS.pop(stale,None)
    return result

REPEAT_WINDOW=600
REPEAT_LIMIT=2

def repeat_key(ctx,query):
    import hashlib,json
    content={k:query.get(k) for k in ('source_id','sql','parameters','mode','semantic','metric','periods','group_by','filters','city','grade','profession','category','detail','below','top_n','cumulative_percent')}
    lineage = query.get('result_lineage') or {}
    request = (ctx.host_evidence or {}).get('user_ref')
    content.update(request_ref=request, purpose=lineage.get('purpose'))
    return (ctx.project,ctx.session_id,hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest())

def preserve_delivery_dimensions(engine,ctx,task,args):
    """Filter-only follow-ups keep the task's original presentation dimensions.

    Fixed dimensions may be omitted from a projection, but removing the filter
    restores the original grouping. This state is scoped to project/session/task.
    """
    subject='session:'+ctx.session_id+':task:'+task['task_id']
    revision,head=engine.artifacts.head(ctx.project,subject,'data.delivery-contract')
    dimensions=lambda group: set() if group=='none' else set(group.split('_')) if group=='profession_grade' else {group}
    proposed=dimensions(args.group_by)
    text=ctx.host_evidence.get('user_text','')+' '+ctx.host_evidence.get('confirmation','')
    filter_only=bool(re.search(r'只看|仅看|改查|取消.*限制|月份不变|类别不变|其他条件不变',text))
    explicit_view=bool(re.search(r'按.+(?:列出|分组|统计|分布)|合计|汇总|总数|不分|不再按',text))
    if head and filter_only and not explicit_view:
        original=set(head['payload']['dimensions'])
        fixed={d for d in original if getattr(args,d,None) is not None}
        if not original-fixed <= proposed or not proposed <= original:
            raise ValueError('DELIVERY_SCOPE_MISMATCH: 仅调整筛选不能丢弃原分组要求；取消筛选后应恢复分组维度 '+','.join(sorted(original-fixed))+'。请保留原交付维度重新计划。')
        return
    # The first plan establishes the domain delivery contract. An explicit new
    # presentation request can revise it without affecting another task.
    if not head or explicit_view or not filter_only:
        engine.artifacts.publish(ctx.project,subject,'data.delivery-contract',{'dimensions':sorted(proposed),'task_id':task['task_id']},expected_revision=revision,producer='data.delivery@1.0.0',parents=[])


# ---- 0.6.1 one-step query tools -------------------------------------------
# The compact tool profile lets the model call one tool per query. The server
# still keeps the same audit chain (task revision -> bound plan -> execution),
# but it creates the task, takes the latest revision and binds the plan itself,
# so the model never copies SQL, catalog_ref, task_revision or plan_ref.

def _publish_tasks(engine, ctx, change):
    """Apply change(tasks, revision) -> task and publish, retrying lost races."""
    import copy
    if not ctx.session_id:
        raise ValueError('TASK_REQUIRED: 执行必须绑定宿主会话。')
    subject = 'session:' + ctx.session_id
    for _ in range(4):
        revision, head = engine.artifacts.head(ctx.project, subject, 'work.tasks')
        tasks = copy.deepcopy(head['payload']['tasks']) if head else []
        task, parents = change(tasks, revision)
        try:
            _, published = engine.artifacts.publish(ctx.project, subject, 'work.tasks', {'tasks': tasks},
                                                    expected_revision=revision, producer='data.ask@1.0.0', parents=parents)
        except ConflictError:
            continue
        return task, published
    raise ConflictError('STALE_TASK: 任务正被并发修改，请稍后重试。')


def upsert_query_task(engine, ctx, task_id, title, fields, clear=(), source_id=None, scope_proposal=None):
    """Create a data task, or refine an existing one, and return (task, revision)."""
    import uuid

    def change(tasks, revision):
        if task_id:
            task = next((t for t in tasks if t['task_id'] == task_id), None)
            if task is None or task.get('capability_id') != 'data':
                raise ValueError('TASK_REQUIRED: task_id 不属于当前会话的问数任务。新问题不要填 task_id，会自动新建任务。')
        else:
            task = {'task_id': uuid.uuid4().hex, 'title': title, 'capability_id': 'data', 'fields': {}, 'state': 'ready', 'result_refs': []}
            tasks.append(task)
        if source_id:
            from .request_scope import derive, recent_scope
            source = engine.catalog(ctx.project, source_id)['sources'][0]
            previous = task.get('request_scope') or recent_scope(engine, ctx, source, ctx.host_evidence)
            task['request_scope'] = derive(source, ctx.host_evidence, previous, scope_proposal)
        for key in clear:
            task['fields'].pop(key, None)
        task['fields'].update(fields)
        if title:
            task['title'] = title
        task['state'] = 'ready'
        task['revision'] = revision + 1
        return task, []
    return _publish_tasks(engine, ctx, change)


def remember_turn_scope(engine, ctx, scope, source_id, host=None):
    """Keep the last delivered scope for the session, not just for one task.

    A follow-up may arrive on a freshly created task; the task-held scope is then
    empty and every condition would silently reset.  This is the session-level
    backup, written after a successful delivery and read by upsert_query_task.
    The turn and request id make it usable only by the next turn (or a retry).
    """
    if not scope or not scope.get('constraints') or not source_id or not getattr(ctx, 'session_id', None):
        return
    host = host or {}
    try:
        current, _ = engine.artifacts.head(ctx.project, 'session:' + ctx.session_id, 'work.turn_scope')
        engine.artifacts.publish(
            ctx.project, 'session:' + ctx.session_id, 'work.turn_scope',
            {'scope': scope, 'source_id': source_id,
             'config_fingerprint': scope.get('config_fingerprint'),
             'turn': host.get('turn'), 'user_ref': host.get('user_ref')},
            expected_revision=current, producer='data.turn-scope@1.0.0', parents=[])
    except Exception:
        # Losing the backup must never fail a delivered answer.
        return


def mark_task_delivered(engine, ctx, task_id, result_ref, request_scope=None):
    """Record the successful result on its task. Never fails the query itself."""
    try:
        try:
            engine.artifacts.get(ctx.project, result_ref)
            ref = result_ref
        except PermissionError:
            ref = 'data:' + result_ref
            engine.artifacts.get(ctx.project, ref)

        def change(tasks, revision):
            task = next((t for t in tasks if t['task_id'] == task_id), None)
            if task is None:
                raise ValueError('TASK_REQUIRED')
            task['result_refs'] = list(dict.fromkeys(task.get('result_refs', []) + [ref]))
            if request_scope:
                task['request_scope'] = request_scope
            task['state'] = 'delivered'
            task['revision'] = revision + 1
            return task, [ref]
        task, revision = _publish_tasks(engine, ctx, change)
        return revision
    except Exception:
        return None
