"""Private loopback transport. Only the authenticated Harness plugin holds the token."""
import hmac
import os
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from .core import DataEngine
from .capability import capability
from .catalog_index import fingerprint
from .projects import BusinessProjects, local_principal, policy_key
from natural_work.runtime import CapabilityRuntime, Context
from natural_work.store import ConflictError

engine = DataEngine(Path(os.environ["NATURAL_DATA_HOME"]))
runtime = CapabilityRuntime(engine.artifacts)
runtime.register(capability(engine,analysis_invoke=lambda args,ctx:runtime.invoke('analysis','compute',args,ctx)))
from natural_analysis.capability import capability as analysis_capability
from natural_analysis.capability import definitions as analysis_definitions
from natural_work.tasks import capability as task_capability
runtime.register(analysis_capability(engine))
runtime.register(task_capability(engine.artifacts))
from natural_knowledge.capability import capability as knowledge_capability
runtime.register(knowledge_capability(engine.artifacts))
from natural_work.policy import CapabilityPolicy
policy = CapabilityPolicy(engine.home/'capability-policy.json', {
 'work':{'title':'任务','requires':[]}, 'data':{'title':'问数','requires':['work']},
 'analysis':{'title':'分析技能','requires':['data']},
 # 0.6.3: knowledge retrieval has no tools in the shipped profile; off unless enabled.
 'knowledge':{'title':'知识检索','requires':[],'default':'disabled'}},
 # 0.6.3: named skills run through analysis_run, which the compact tool profile hides.
 # 0.7.0: methods rarely used in 测绘 work start off; settings an administrator already made are kept.
 skill_defaults={'distribution_profile':False,'economic_efficiency':False,'anomaly_attribution':False,**{'matrix.'+str(i):False for i in __import__('natural_analysis.methods',fromlist=['DEFAULT_OFF']).DEFAULT_OFF}},skill_registry=lambda:analysis_definitions()+__import__('natural_analysis.methods',fromlist=['all_custom_definitions']).all_custom_definitions(engine))
runtime.policy=policy
engine.policy=policy
business = BusinessProjects(engine)
engine.business_projects = business
policy.scope_key = business.policy_scope
from .connections import Connections
connections = Connections(engine,business)
from .caliber_excel import ExcelImports
excel = ExcelImports(engine,business)
from .admin import Administration
admin = Administration(engine,business)
business.migrate_policy(policy)
runtime.recover()
# Keep health and startup responsive. A turn needs one stable publication
# through catalog lookup, query execution and follow-up analysis.
_startup_packs_ready = Event()


def _apply_builtin_packs():
    import json, sys
    try:
        from .presets import apply_builtin_packs
        apply_builtin_packs(engine, business, connections, log=lambda row: print('builtin-pack', json.dumps(row, ensure_ascii=False), file=sys.stderr, flush=True))
    except Exception as error:
        print('builtin-pack failed', error, file=sys.stderr, flush=True)
    finally:
        _startup_packs_ready.set()


Thread(target=_apply_builtin_packs, daemon=True).start()
TOKEN = os.environ["NATURAL_DATA_TOKEN"]
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

def _recover_pending_delete():
    # Recovery is also performed at startup and inside connection mutations.
    # Keep interrupted revocation fail-closed without acquiring a global lock
    # or scanning credentials on every ordinary request.
    with engine.lock:
        connections.recover_delete()

@app.middleware("http")
async def authorize(request: Request, call_next):
    headers = [value for key,value in request.scope['headers'] if key.lower()==b'authorization']
    if len(headers)!=1 or not hmac.compare_digest(headers[0], ('Bearer '+TOKEN).encode('utf-8')):
        return PlainTextResponse("Unauthorized", status_code=401)
    if connections.deletion_path.exists():
        await run_in_threadpool(_recover_pending_delete)
    return await call_next(request)

class Call(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal: str = Field(default="", max_length=200)
    project: str = Field(min_length=1, max_length=4096)
    args: dict = Field(default_factory=dict)
    session_id: str = Field(default="", max_length=256)
    host_evidence: dict = Field(default_factory=dict)
    request_key: str = Field(default="", max_length=256)

def _compute_build_id():
    from .build_info import current_build_id
    return current_build_id()


BUILD_ID = _compute_build_id()
from .build_info import current_version
PLUGIN_VERSION = current_version()


@app.get("/health")
def health():
    return {"product": "智能问数", "edition": "测绘版", "capability": "data", "version": PLUGIN_VERSION, "build_id": BUILD_ID, "data_contract": "2.4.0", "analysis_contract": "2.2.0", "non_spatial_methods": 42}


def _data_authorization_scope(actor, scope, policy_revision):
    with engine.lock:
        config = business._read_config()
        entry = config['scopes'].get(scope)
        if not entry or entry['actor'] != actor:
            raise PermissionError('结果不属于当前用户。')
        project = business.require(config, actor, entry['business_project'])
        version = project['published_version']
        if project.get('archived') or not version:
            raise PermissionError('该业务项目已停用或尚未发布。')
        live = next(v['config'] for v in project['versions'] if v['version'] == version)
        # Connections are published by atomically replacing sources.json. Use
        # its filesystem revision so the receipt check never reparses the inventory.
        try:
            stat = engine.config_path.stat()
            connections_revision = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        except FileNotFoundError:
            connections_revision = None
        return fingerprint([actor, project['members'][actor], project['id'], version,
                            live['source_ids'], live['tables'], connections_revision, policy_revision])


# Fields a manager client may keep and reuse while the server-side revision is unchanged.
_CONFIG_CACHED_FIELDS = ('draft', 'versions', 'review_candidates', 'review_reasons')


def _persisted_revision(path):
    """Filesystem revision of a persisted config file; None when nothing may be reused."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return fingerprint([str(path), stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size])


def _config_cache_revisions(engine, business, actor, snapshot, available, allowed_tables, file_revisions):
    """Tokens for the two heavy manager payloads, derived only from their real inputs.

    ``inventory`` is the authorized source catalogue: source rows come from sources.json and
    are filtered by the grants of the projects this actor can see. ``detail`` is one project's
    draft, version list and review hints: they come from business-projects.json plus the same
    inventory. Both therefore change whenever these files, the grants, the actor, or the running
    build change; an unchanged token proves the client already holds identical bytes.
    """
    sources, projects = file_revisions
    common = [BUILD_ID, PLUGIN_VERSION, actor, snapshot['revision']]
    inventory = fingerprint(common + ['inventory', snapshot['system_admin'], sources,
                                      sorted(available),
                                      {sid: sorted(tables) for sid, tables in allowed_tables.items()}]) if sources else ''
    detail = fingerprint(common + ['detail', snapshot.get('selected_project', ''), projects, sources]) if (
        sources and projects and snapshot.get('selected_project')) else ''
    return inventory, detail


@app.post("/v1/capabilities/{capability_id}/{operation}")
def invoke_capability(capability_id: str, operation: str, call: Call):
    try:
        data_scope = capability_id in {'data', 'analysis'}
        if data_scope:
            _startup_packs_ready.wait()
        with business.read_snapshot():
            actor = call.principal or local_principal()
            scope = business.context_scope(actor, call.project, call.session_id, required=data_scope)
            # Host-owned grant, never accepted from model arguments. Future capabilities
            # must be explicitly enabled here; registry membership alone grants nothing.
            active_policy = policy.snapshot(scope)
            states = active_policy['states']
            auth_scope = _data_authorization_scope(actor, scope, active_policy['revision']) if data_scope else ''
            value = runtime.invoke(capability_id, operation, call.args,
                                  Context(scope, call.request_key, frozenset(op.permission for cap in runtime.capabilities.values()
                                          if states[cap.id] != 'disabled' for op in cap.operations), call.session_id,
                                          call.host_evidence, authorization_scope=auth_scope))
            if data_scope:
                if auth_scope != _data_authorization_scope(actor, scope, policy.snapshot(scope)['revision']):
                    raise ConflictError('授权范围已变化，请使用当前授权重新发起调用。')
            elif scope.startswith('business/'):
                business.resolve(actor, scope)
            return {**value, "execution_scope": scope}
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        from .semantic import CoverageError
        if isinstance(exc, CoverageError):
            return JSONResponse(status_code=422, content={'detail': str(exc), 'coverage_evidence': exc.coverage_evidence})
        raise HTTPException(422, str(exc)) from None

@app.post("/v1/{operation}")
def invoke(operation: str, call: Call):
    a, p = call.args, call.project
    actor = call.principal or local_principal()
    try:
        if operation == 'runtime_contracts':
            # The active engine's validators are the sole source of native tool contracts.
            return {'build_id': BUILD_ID, 'capabilities': [runtime.manifest(key) for key in runtime.capabilities]}
        if operation == 'admin_validation':
            from .config_validation import validate_example
            return validate_example(admin,actor,a,call.host_evidence)
        if operation == 'admin_check': return admin.check_project(actor,a)
        if operation == 'connections': return connections.snapshot(actor)
        if operation == 'connection_test': return connections.test(actor,a)
        if operation == 'connection_update':
            return connections.update(actor,a)
        if operation in ('excel_template', 'excel_import', 'excel_history'):
            # 0.7.0: Excel caliber template and batch import (ported from the 测绘版). Writes only the draft.
            return {'excel_template': excel.template, 'excel_import': excel.import_, 'excel_history': excel.history}[operation](actor, a)
        if operation == 'business_packs':
            from .presets import list_packs
            return list_packs(engine, business.require(business.config(),actor,a.get('business_project'),'editor'))
        if operation == 'business_update' and a.get('action') == 'import_pack':
            # Built-in business pack → draft. Reads only aggregate time bounds; publish stays manual.
            from .presets import import_pack
            if getattr(engine,'policy',None): engine.policy.require('business/'+str(a.get('business_project','')),'data','query')
            draft, report = import_pack(engine, business, connections, actor, a)
            note = '全量替换项目业务口径' if a.get('replace_calibers') is True else '手动导入业务口径包'
            pending_pack = ({'id': report['pack'], 'version': report['version'], 'actor': actor,
                             'note': note, 'source_id': report['source_id'],
                             'replace_calibers': a.get('replace_calibers') is True}
                            if any(report['imported'].values()) else None)
            value = business.update(actor, 'draft', {**a, 'draft': draft}, pending_pack=pending_pack)
            value['pack_report'] = report
            return value
        if operation == 'business_update':
            value = business.update(actor, a.get('action'), a)
            published_changed = value.get('published_changed', False)
            if published_changed or a.get('action') in {'member','delete'}:
                # Stop affected reads before running optional pack discovery.
                # Scope IDs are generated by BusinessProjects, not model input.
                prefix = 'business/'+str(a.get('business_project',''))+'/'
                with engine.lock:
                    active_keys = [(scope,key) for scope,key in engine.active if scope.startswith(prefix)]
                for scope,key in active_keys:
                    engine.cancel(scope,key)
            if published_changed and a.get('action') in {'publish', 'save'}:
                from .presets import apply_builtin_packs
                applied = [row for row in apply_builtin_packs(engine, business, connections, only=a.get('business_project')) if row.get('imported')]
                if applied:
                    active = [row for row in applied if row['published']]
                    pending = [row for row in applied if not row['published']]
                    value = {**value, **business.snapshot(actor, summary=a.get('response_view') == 'manager', selected_project=a.get('business_project')), 'selected_project': a.get('business_project'),
                             'builtin_packs_applied': active,
                             'builtin_packs_pending': pending,
                             'notice': '已启用内置业务口径。' if active else '发现可用业务口径包，已导入草稿，请核对后生效。'}
            return value
        if operation == 'business_template':
            from .onboarding import export_template
            return export_template(business.require(business.config(),actor,a.get('business_project'),'editor'))
        if operation == 'inspect_periods':
            from .onboarding import inspect_periods
            return inspect_periods(engine,business,connections,actor,a)
        if operation == 'project_skills':
            from natural_analysis.capability import listed_definitions as definitions
            from .metric_registry import resolve_mapping
            from .metrics import METRICS
            project = business.require(business.config(),actor,a.get('business_project') or p)
            ids = project.get('draft',{}).get('source_ids',[]) if project['members'][actor] in {'admin','editor'} else next((v['config']['source_ids'] for v in project['versions'] if v['version']==project['published_version']),[])
            metric_ids = set()
            for source in engine.config()['sources']:
                if source['id'] not in ids: continue
                try:
                    name, definition = resolve_mapping(source)
                    metric_ids.update(METRICS if name=='provincial-monthly/1' else definition.get('metrics',{}))
                except ValueError: pass
            from .onboarding import project_readiness
            for source in project_readiness(engine,project)['sources']:
                metric_ids.update(m['id'] for m in source['semantic_metrics'])
            custom=[s for s in __import__('natural_analysis.methods',fromlist=['all_custom_definitions']).all_custom_definitions(engine) if s.get('business_project')==project['id']]
            return {'skills':[{**entry,'applicable':set(entry.get('required_metrics',[]))<=metric_ids,'enabled':policy.skill_enabled('business/'+project['id'],entry['id'])} for entry in definitions()+custom], 'revision':policy.config()['revision']}
        if operation == 'session_project':
            return business.session(actor, a.get('session_id') or call.session_id, a.get('business_project'), workspace=p)
        if operation == "policy":
            if p.startswith('p-'):
                business.require(business.config(), actor, p)
                p = 'business/' + p
            else:
                p = business.context_scope(actor, p, call.session_id, required=False)
            value = policy.snapshot(p)
            if p.startswith('personal/'):
                value['states'].update({'data':'disabled','analysis':'disabled'})
            # Tool lists are assembled before the first catalog call. Derive legacy
            # support from the current authorized project, never from a prior turn's
            # catalog receipt (which may belong to an old source/configuration).
            value['data_tool_support'] = {'legacy_metrics': False}
            if value['states'].get('data') == 'enabled' and p.startswith('business/'):
                from .onboarding import describe
                value['data_tool_support']['legacy_metrics'] = any(
                    bool(describe(source).get('metric_definitions'))
                    for source in engine.sources(p)
                )
            return value
        if operation == "policy_update":
            bid = a.get('business_project') or p
            cfg = business.config()
            if a.get('scope') == 'global':
                if actor not in cfg['admins']: raise PermissionError('只有系统管理员可以修改全局能力。')
            else: business.require(cfg, actor, bid, 'admin')
            p = 'business/' + bid
            # Query admission uses this same lock; no new call can slip between
            # revocation and the active-query cancellation scan.
            with engine.lock:
                before={scope:policy.snapshot(scope)['states']['data'] for scope in cfg['scopes']}
                value=policy.update(p,a.get('changes',{}),a['expected_revision'],a.get('scope','project'),a.get('skills'))
                for scope, state in before.items():
                    if state=='enabled' and policy.snapshot(scope)['states']['data']!='enabled':
                        for active_project,key in list(engine.active):
                            if active_project==scope: engine.cancel(scope,key)
            return value
        if operation in {'catalog','analyze','visualize','result','results','export','cancel','make_report','edit_report'}:
            with business.read_snapshot():
                if p.startswith('business/'):
                    p = business.resolve(actor,p)
                elif operation in {'result','results','export','visualize'} and actor==local_principal() and p in engine._read_config().get('projects',{}):
                    from .legacy_access import authorize_legacy
                    if operation=='results':
                        values=[]
                        with engine.lock:
                            for result in engine.list_results(p):
                                try: authorize_legacy(engine,business,policy,actor,p,result['result_ref'],operation)
                                except (PermissionError,ValueError): continue
                                values.append(result)
                        return values
                    with engine.lock:
                        authorize_legacy(engine,business,policy,actor,p,a['result_ref'],operation)
                        if operation=='result':return engine.get(p,a['result_ref'])
                        if operation=='export':return PlainTextResponse(engine.export_csv(p,a['result_ref']),media_type='text/csv')
                        return engine.visualize(p,a['result_ref'],a['kind'],a['expected_revision'],view_patch=True)
                else: p = business.resolve(actor,p,call.session_id)
                if operation in {'make_report','edit_report'}:
                    policy.require(p,'data',operation)
                    from . import reports
                    with engine.lock:
                        return reports.create(engine,p,reports.Create.model_validate(a),call.request_key) if operation=='make_report' else reports.edit(engine,p,reports.Edit.model_validate(a))
                if operation in {'catalog','analyze','visualize'}: policy.require(p,'data',operation)
                if operation in {'result','results','export'}: policy.require_history(p)
                if operation == "catalog": return engine.catalog(p, a.get("source_id"))
                if operation == "cancel": return engine.cancel(p, call.request_key)
                if operation == "analyze":
                    policy.require(p,'analysis','run')
                    return engine.analyze(p, a["result_ref"],a.get("operators"),a.get("metric_column"))
                if operation == "visualize": return engine.visualize(p, a["result_ref"], a["kind"], a["expected_revision"], view_patch=True)
                if operation == "result": return engine.get(p, a["result_ref"])
                if operation == "results": return engine.list_results(p)
                if operation == "export": return PlainTextResponse(engine.export_csv(p, a["result_ref"]), media_type="text/csv")
        if operation == "query": raise ValueError("请使用能力合约入口并提供目录引用。")
        if operation == "config":
            manager = a.get('view') == 'manager'
            before = (_persisted_revision(engine.config_path), _persisted_revision(business.path)) if manager else None
            # A manager read only projects inventory fields. Detach only the
            # selected inventory bytes when the client needs them.
            cfg = engine._read_config() if manager else engine.config()
            snapshot = business.snapshot(actor, summary=manager,
                                         selected_project=a.get('business_project', ''), workspace=call.project,
                                         inventory={s['id']: s for s in cfg['sources']})
            available = set(); allowed_tables = {}
            for project in snapshot['projects']:
                config = project.get('draft') or next((v['config'] for v in project['versions'] if v['version']==project['published_version']),{})
                available.update(config.get('source_ids',[]))
                for sid,tables in config.get('tables',{}).items(): allowed_tables.setdefault(sid,set()).update(tables)
            after = (_persisted_revision(engine.config_path), _persisted_revision(business.path)) if manager else None
            # A write during composition must never stamp old bytes with a new revision.
            stable = manager and before == after and all(after)
            inventory_revision, detail_revision = (_config_cache_revisions(engine, business, actor, snapshot,
                                                                          available, allowed_tables, after) if stable
                                                   else ('', ''))
            # A client that echoes the current tokens already holds these bytes, so skip the
            # expensive composition instead of re-serializing the whole catalogue.
            reuse_inventory = bool(inventory_revision) and a.get('cached_inventory') == inventory_revision
            reuse_detail = bool(detail_revision) and a.get('cached_detail') == detail_revision
            inventory = []
            if not reuse_inventory:
                for source in cfg['sources']:
                    if not snapshot['system_admin'] and source['id'] not in available: continue
                    tables = source['tables'] if snapshot['system_admin'] else [t for t in source['tables'] if t['name'] in allowed_tables.get(source['id'],set())]
                    inventory.append({**{k:source[k] for k in ('id','name','description','dialect','origin','synthetic','target_revision') if k in source},'enabled':source.get('connection_enabled',True),'table_count':len(tables),'tables':[{k:t[k] for k in ('name','label','columns','primary_key','unique_keys','foreign_keys') if k in t} for t in tables]})
            selected = next((p for p in snapshot['projects'] if p['id'] == snapshot.get('selected_project')), None)
            if selected is not None and manager:
                if reuse_detail:
                    for field in _CONFIG_CACHED_FIELDS: selected.pop(field, None)
                    selected['detail_reused'] = True
                selected['detail_revision'] = detail_revision
            from .onboarding import diagnostics
            value = {**snapshot, 'plugin_version':PLUGIN_VERSION,'build_id':BUILD_ID,'edition':'测绘版',
                    'environment':diagnostics() if snapshot['system_admin'] else None,
                    'legacy_sync_available':False, 'identity_mode':'single-operator',
                    'inventory_revision':inventory_revision}
            if reuse_inventory:
                value['inventory_elided'] = True
            else:
                value['sources'] = deepcopy(inventory) if manager else inventory
            return value
        if operation == "test":
            project = business.require(business.config(),actor,a.get('business_project'),'editor')
            if a['source_id'] not in project['draft']['source_ids']: raise PermissionError('数据源未接入当前项目草稿。')
            return engine.test_source(a["source_id"])
        if operation == "bind":
            raise ValueError('工作区绑定已停用，请在业务项目中管理数据范围。')
        raise HTTPException(404)
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)[:300]) from None
