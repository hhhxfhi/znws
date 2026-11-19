"""Shared product readiness, scoped discovery and portable business configuration.

No connection secrets or business rows are returned. Inventory never grants access.
"""
import copy
import importlib.util
import json
from .semantic import validate as validate_metric


def metric_catalog(source):
    from .metric_limitations import entries as limited_metrics
    available, unavailable = [], list(limited_metrics(source))
    limited_ids = {entry['id'].casefold() for entry in unavailable}
    seen = set()
    for row in source.get('terms', []):
        spec = row.get('config', {})
        if not isinstance(spec,dict):
            unavailable.append({'id':row.get('id',''),'version':'','name':row.get('name',''),
                'state':'needs_configuration','reason':'指标结构化配置必须是对象，请修复后发布。','action':'business'})
            continue
        if spec.get('kind') != 'semantic-metric' or spec.get('source_id') != source['id']:
            continue
        try:
            from .caliber_excel import excel_review_reason
            reason = excel_review_reason(row)
            if reason: raise ValueError('EXCEL_REVIEW_REQUIRED: '+reason)
            metric = validate_metric(spec, source)
            key = (metric.id, metric.version)
            if key in seen:
                raise ValueError('SEMANTIC_DUPLICATE: 同一指标版本重复，请在业务口径中保留唯一条目。')
            seen.add(key)
            available.append(metric.model_dump())
        except ValueError as exc:
            unavailable.append({'id': spec.get('id', ''), 'version': spec.get('version', ''),
                                'name': row.get('name', ''), 'state': 'needs_configuration',
                                'reason': str(exc)[:400], 'action': 'business',
                                'message': '该指标暂不可用，请在设置 → 智能问数 → 业务口径中修复并发布。'})
    # Ambiguous duplicate versions are all unavailable; never select the first one.
    bad = {(x['id'], x['version']) for x in unavailable}
    return [x for x in available if (x['id'], x['version']) not in bad and x['id'].casefold() not in limited_ids], unavailable


def describe(source):
    from .metric_registry import resolve_mapping
    from .metrics import METRICS
    from .source_metrics import METRICS as SOURCE_METRICS
    value = copy.deepcopy(source)
    metrics, invalid = metric_catalog(source)
    value.update(semantic_metrics=metrics, unavailable_metrics=invalid, metric_definitions=[])
    value['unavailable_clarifications']=[{'name':t['name'],'reason':t.get('clarification_review_reason','引用的指标尚未就绪。')}
                                       for t in source.get('terms',[]) if t.get('pending_clarification') and not t.get('clarification')]
    legacy = False
    try:
        name, mapping = resolve_mapping(source)
        definitions = mapping.get('metrics', {k: v for k, v in METRICS.items() if k not in SOURCE_METRICS})
        authorized = {t['name'].split('.')[-1] for t in source.get('tables', [])}
        unavailable_ids = {entry['id'].casefold() for entry in invalid if entry.get('kind') == 'metric-limitation'}
        value['metric_definitions'] = [{'id': k, 'name': v[0], 'unit': v[3]} for k, v in definitions.items()
                                       if v[1].split('.')[-1] in authorized and k.casefold() not in unavailable_ids]
        legacy = bool(value['metric_definitions'])
        if name == 'provincial-monthly/1':
            value['confirmed_aliases'] = {'抽检合格率': 'inspection_pass_rate', '验收完成量': 'project_work_completion',
                                          '基础测绘投入金额': 'project_investment'}
            from pathlib import Path
            guidance=Path(__file__).resolve().parents[2]/'packages/natural-data/legacy-metrics.md'
            if guidance.exists():value['legacy_guidance']=guidance.read_text()
        value['business_guidance'] = '仅当前来源使用已登记的指标定义、别名与默认条件；不得迁移到其他来源。'
    except ValueError:
        pass
    value['query_modes'] = {'basic_query': bool(source.get('tables')),
                            'semantic_metrics': bool(metrics), 'legacy_metrics': legacy}
    value['configuration_guidance'] = {
        'message': '基础问数可直接使用授权表和字段；业务含义不明确时澄清。需要固定业务指标时，在业务口径中配置、校验并发布。',
        'settings_section': 'natural-data', 'action': 'business', 'required_role': 'editor',
        'coverage_note': '指标声明的覆盖期间与源库实际数据范围分别检查；不把缺配置或缺数据当零。'}
    value['starter_questions'] = [{'question': '查看“' + (t.get('label') or t['name']) + '”的字段说明',
                                    'kind': 'catalog', 'table': t['name']} for t in source.get('tables', [])[:3]]
    return value


def project_readiness(engine, project, *, inventory=None):
    from .projects import project_source
    publication = next((v for v in project['versions'] if v['version'] == project['published_version']), None)
    published = publication['config'] if publication else None
    if inventory is None:
        inventory = {s['id']: s for s in engine.config()['sources']}
    issues, sources = [], []
    def issue(code, message, action, blocking=True):
        issues.append({'code': code, 'message': message, 'action': action, 'blocking': blocking})
    if not published:
        issue('PROJECT_NOT_PUBLISHED', '请完成数据范围授权，保存草稿并校验发布。', 'sources')
    active = published or project['draft']
    if not active['source_ids']:
        issue('NO_AUTHORIZED_SOURCE', '请接入数据库并为当前业务项目选择数据源及授权表。', 'sources')
    for sid in active['source_ids']:
        source = inventory.get(sid)
        if not source:
            issue('SOURCE_MISSING', '项目引用的数据源已移除，请修复数据范围并发布。', 'sources')
            continue
        tables = [t for t in source['tables'] if t['name'] in active['tables'].get(sid, [])]
        if not source.get('connection_enabled', True):
            issue('CONNECTION_DISABLED', source['name'] + '已停用，请管理员启用连接。', 'connections')
            continue
        if not tables:
            issue('NO_AUTHORIZED_TABLE', source['name'] + '没有可用授权表，请选择授权表并发布。', 'sources')
        enriched = describe({**project_source(source, publication), 'tables': tables, 'terms': active['terms']})
        # Never return credential-bearing source objects.
        sources.append({k: enriched[k] for k in ('id', 'name', 'query_modes', 'semantic_metrics',
                        'unavailable_metrics', 'metric_definitions', 'starter_questions')})
    ready = bool(published and any(s['query_modes']['basic_query'] for s in sources))
    count = sum(len(s['semantic_metrics']) + len(s['metric_definitions']) for s in sources)
    builtin_examples = sum(str(row.get('origin') or '').startswith('pack:') for row in active['examples'])
    if ready and not count and not builtin_examples:
        issue('NO_PUBLISHED_METRICS', '基础问数已就绪。尚未发布业务指标，可在业务口径中按需配置。', 'business', False)
    if any(m.get('state') == 'needs_configuration' for s in sources for m in s['unavailable_metrics']):
        issue('INVALID_METRICS', '部分指标已失效，请修复并重新发布；其他授权表仍可基础问数。', 'business', False)
    if any(m.get('state') == 'requires_comparable_history' for s in sources for m in s['unavailable_metrics']):
        issue('METRIC_REQUIRES_COMPARABLE_HISTORY', '部分指标缺少可比历史数据，当前不可计算。', 'business', False)
    if published and published != project['draft']:
        issue('UNPUBLISHED_CHANGES', '存在未发布的草稿，对话仍使用当前生效版本。', 'versions', False)
    return {'basic_query': {'state': 'ready' if ready else 'needs_configuration'},
            'business_metrics': {'state': 'ready' if count or builtin_examples else 'needs_configuration', 'count': count,
                                 **({'builtin_examples': builtin_examples} if builtin_examples else {})},
            'published_version': project['published_version'], 'issues': issues, 'sources': sources}


def diagnostics():
    from .connections import TYPES
    return {'python': 'ready', 'drivers': [{'id': key, 'name': item[0],
             **__import__('natural_data.additional_databases',fromlist=['driver_status']).driver_status(key,item[2])} for key, item in TYPES.items()],
            'model': '由宿主的模型设置管理；请在宿主确认模型可调用并支持工具。',
            'note': '连接与结构检查不证明业务口径正确或数据完整。'}


def export_template(project):
    config = copy.deepcopy(project['draft'])
    aliases = {sid: 'source_' + str(i + 1) for i, sid in enumerate(config['source_ids'])}
    for group in ('terms', 'examples', 'skills', 'layers'):
        for row in config[group]:
            # Fields outside the product contract (including imported legacy payloads)
            # are deliberately omitted from portable templates.
            for key in list(row):
                if key not in {'id', 'name', 'definition', 'question', 'logical_sql', 'description', 'source_id', 'config', 'clarification'}:
                    row.pop(key)
            if row.get('source_id') in aliases: row['source_id'] = aliases[row['source_id']]
            spec = row.get('config', {})
            if spec.get('kind') not in {'semantic-metric','analysis-skill','metric-limitation','row-count-aggregation'}:
                row.pop('config',None)
                continue
            if spec.get('source_id') in aliases: spec['source_id'] = aliases[spec['source_id']]
            spec.pop('source_revision', None)
    # Templates contain business rules and SQL examples, never connections, users,
    # grants, histories, credentials or absolute database paths.
    exported={'format': 'ask-data-business-template', 'version': 1,
            'sources': [{'alias': alias, 'tables': config['tables'][sid]} for sid, alias in aliases.items()],
            'terms': config['terms'], 'examples': config['examples'], 'skills': config['skills'],
            'layers': []}
    if config.get('mappings'):
        exported.update(version=2,mappings=copy.deepcopy(config['mappings']))
        for row in exported['mappings']: row['source_id']=aliases[row['source_id']]
    return exported


def import_template(engine, project, template, bindings):
    import uuid
    if not isinstance(template, dict) or set(template)-{'format','version','sources','terms','examples','skills','layers','mappings'} or not {'format','version','sources','terms','examples','skills','layers'}<=set(template):
        raise ValueError('TEMPLATE_SCHEMA: 业务模板格式无效。')
    if template['format'] != 'ask-data-business-template' or template['version'] not in (1,2):
        raise ValueError('TEMPLATE_VERSION: 不支持的业务模板版本。')
    if len(json.dumps(template, ensure_ascii=False)) > 300000:
        raise ValueError('TEMPLATE_SIZE: 业务模板过大。')
    if not isinstance(template['sources'],list) or len(template['sources'])>100 or any(
            not isinstance(s,dict) or set(s)!={'alias','tables'} or not isinstance(s['alias'],str) or
            not isinstance(s['tables'],list) or any(not isinstance(t,str) for t in s['tables']) for s in template['sources']):
        raise ValueError('TEMPLATE_SCHEMA: 模板来源与表列表格式无效。')
    aliases = {s['alias'] for s in template['sources']}
    if len(aliases)!=len(template['sources']) or not isinstance(bindings,dict) or set(bindings) != aliases or any(not isinstance(sid,str) or sid not in project['draft']['source_ids'] for sid in bindings.values()):
        raise ValueError('TEMPLATE_BINDING: 请将每个模板来源绑定到当前项目已授权的数据源；导入不会新增授权。')
    draft = copy.deepcopy(project['draft'])
    inventory = {s['id']: s for s in engine.config()['sources']}
    for group in ('terms', 'examples', 'skills'):
        if not isinstance(template[group], list): raise ValueError('TEMPLATE_SCHEMA: 配置条目必须是列表。')
        for entry in template[group]:
            if not isinstance(entry,dict):raise ValueError('TEMPLATE_SCHEMA: 每个配置条目必须是对象。')
            row = copy.deepcopy(entry); row['id'] = uuid.uuid4().hex
            if 'source_id' in row: row['source_id'] = bindings[row['source_id']]
            spec = row.get('config', {})
            if spec.get('kind') in {'semantic-metric','row-count-aggregation'}:
                if not isinstance(spec.get('source_id'), str) or spec['source_id'] not in bindings:
                    raise ValueError('TEMPLATE_BINDING: 结构化业务口径来源未绑定。')
                spec['source_id'] = bindings[spec['source_id']]
                spec['source_revision'] = inventory[spec['source_id']].get('target_revision', 0)
            if spec.get('kind') == 'metric-limitation':
                if not isinstance(spec.get('source_id'), str) or spec['source_id'] not in bindings:
                    raise ValueError('TEMPLATE_BINDING: 不可计算指标来源未绑定。')
                spec['source_id'] = bindings[spec['source_id']]
            draft[group].append(row)
    if 'mappings' in template:
        if not isinstance(template['mappings'],list): raise ValueError('TEMPLATE_SCHEMA: 业务映射必须为列表')
        draft.setdefault('mappings',[])
        for item in template['mappings']:
            if not isinstance(item,dict) or item.get('source_id') not in bindings: raise ValueError('TEMPLATE_BINDING: 业务映射来源未绑定')
            row=copy.deepcopy(item);row['id']=uuid.uuid4().hex;row['source_id']=bindings[row['source_id']]
            draft['mappings'].append(row)
    return draft


def inspect_periods(engine, business, connections, actor, args):
    """Read only authorized aggregate time bounds; observed bounds imply no completeness."""
    from .connections import read_connection
    from sqlalchemy import text
    project=business.require(business.config(),actor,args.get('business_project'),'editor')
    if getattr(engine,'policy',None):engine.policy.require('business/'+project['id'],'data','query')
    sid=args.get('source_id');name=args.get('table');column=args.get('column')
    draft=project['draft']
    if sid not in draft['source_ids'] or name not in draft['tables'].get(sid,[]):
        raise PermissionError('请先保存项目授权范围草稿，再检查该表的时间范围。')
    source=next((s for s in engine.config()['sources'] if s['id']==sid and s.get('connection_enabled',True)),None)
    table=next((t for t in (source or {}).get('tables',[]) if t['name']==name),None)
    if not table or column not in {c['name'] for c in table['columns']}:
        raise ValueError('时间字段不在当前授权目录，请刷新结构后重新选择。')
    from .semantic import table as quote_table,q
    import sqlglot
    query='SELECT MIN('+q(column)+') AS first_value, MAX('+q(column)+') AS latest_value FROM '+quote_table(name)
    query=sqlglot.parse_one(query,read='postgres').sql(dialect={'postgresql':'postgres','tbase':'postgres'}.get(source['dialect'],source['dialect']))
    revision=source.get('connection_revision',0)
    try:
        with read_connection(source,connections.url(source)) as conn: row=conn.execute(text(query)).mappings().one()
    except Exception:raise ValueError('时间范围检查失败，请检查连接、字段类型和权限；未改变业务覆盖配置。') from None
    current_project=business.require(business.config(),actor,project['id'],'editor')
    if sid not in current_project['draft']['source_ids'] or name not in current_project['draft']['tables'].get(sid,[]):raise PermissionError('检查期间授权范围发生变化，请重新检查。')
    if getattr(engine,'policy',None):engine.policy.require('business/'+project['id'],'data','query')
    current=next((s for s in engine.config()['sources'] if s['id']==sid),{})
    if not current.get('connection_enabled',True) or current.get('connection_revision',0)!=revision:raise ValueError('检查期间连接发生变化，请重新检查。')
    return {'first_observed':str(row['first_value']) if row['first_value'] is not None else None,
            'latest_observed':str(row['latest_value']) if row['latest_value'] is not None else None,
            'complete_years':None,'message':'这是当前字段的实际最早和最晚值，不能据此认定期间连续或全年完整；请核实后填写已确认期间。'}
