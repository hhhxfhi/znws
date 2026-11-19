"""Authorize existing receipts without rewriting their workspace namespace."""
import hashlib
from .projects import local_principal
from .vendor.plans import PolicyContext, validate_plan

def authorize_legacy(engine, business, policy, actor, workspace, rid, operation):
    cfg=engine.config()
    if actor!=local_principal() or workspace not in cfg.get('projects',{}):
        raise PermissionError('历史结果不属于当前用户。')
    result=engine.raw(workspace,rid)
    sid=result['source_id']
    if sid not in cfg['projects'][workspace]:raise PermissionError('历史数据源已撤销授权。')
    from .source_history import history_source
    migration=cfg.get('source_migrations',{}).get(sid)
    source=history_source(engine,cfg['sources'],sid)
    if not source:raise PermissionError('历史数据源已移除。')
    # Use the migrated source owner project, never another project's broader grant.
    identity=str(source.get('project_id') or sid)
    bid=migration['business_project'] if migration else 'p-'+hashlib.sha256(identity.encode()).hexdigest()[:16]
    project=business.require(business.config(),actor,bid)
    version=next((v for v in project['versions'] if v['version']==project['published_version']),None)
    target_sid=migration['source_id'] if migration else sid
    if not version or project.get('archived') or target_sid not in version['config']['source_ids']:
        raise PermissionError('历史数据源未在当前已发布项目中授权。')
    allowed=version['config']['tables'].get(target_sid,[])
    if migration:allowed=[old for old,new in migration['tables'].items() if new in allowed]
    if result.get('sql'):
        parameters=[]
        if result.get('plan_ref'):
            parameters=engine.artifacts.get(workspace,result['plan_ref'])['payload'].get('query',{}).get('parameters',[])
        validate_plan({'mode':'sql','plan_id':'legacy-read','workspace_id':'legacy-read','datasource_ref':sid,'logical_sql':result['sql'],'parameters':parameters, 'expected_chart':{'type':'table','title':'历史授权校验'},'limit':500},
            PolicyContext(workspace_id='legacy-read',allowed_datasource_refs=[sid],allowed_relations=allowed,db_types={sid:source['dialect']}))
    elif result['status']=='succeeded':
        raise PermissionError('历史结果缺少查询依据，不能验证当前表权限。')
    policy.require('business/'+bid,'data','visualize' if operation=='visualize' else 'inspect')
    policy.require(workspace,'data','visualize' if operation=='visualize' else 'inspect')
    return workspace
