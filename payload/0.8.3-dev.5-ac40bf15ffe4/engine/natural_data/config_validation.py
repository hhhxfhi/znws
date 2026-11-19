"""Administrator-only, append-only example validation, separate from activation."""
import copy
import hashlib
import json
import threading
import time
import uuid
import re
from pathlib import Path
from .catalog_index import fingerprint
from natural_work.store import ConflictError

_LOCK=threading.Lock()


def dependency_fingerprint(source, kind, item_id):
    """Unrelated examples and tables do not invalidate an executed example."""
    from sqlglot import parse_one, exp
    from .business_sql_checks import DIALECTS
    if kind=='relation':
        item=next((r for r in source.get('business_dictionary',[]) if r.get('id')==item_id and r.get('kind')=='relation'),None)
        names={item.get('table'),item.get('to_table')} if item else set()
    else:
        item=next((e for e in source.get('examples',[]) if e.get('id')==item_id),None)
        try:
            ast=parse_one(item['logical_sql'],read=DIALECTS.get(source['dialect'],source['dialect']))
            names={'.'.join(p.name for p in table.parts) for table in ast.find_all(exp.Table)}
        except Exception:
            # Unknown dependencies are conservative; never manufacture freshness.
            return fingerprint(source)
    return fingerprint({'source_id':source['id'],'dialect':source['dialect'],'item':item,
        'tables':[t for t in source.get('tables',[]) if t['name'] in names],
        'mappings':[m for m in source.get('business_dictionary',[]) if {m.get('table'),m.get('to_table')}&names],
        'connection_revision':source.get('connection_revision')})


def data_identity(source):
    # SQLite fixtures have an observable local revision. Remote databases need
    # a new run; configuration equality alone never establishes data freshness.
    if source.get('dialect')!='sqlite':return None
    path=Path(source.get('path',''))
    digest=hashlib.sha256()
    for file in [path,Path(str(path)+'-wal')]:
        if not file.is_file():continue
        if file.stat().st_size>256*1024*1024:return None
        before=file.stat()
        with file.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        after=file.stat()
        if (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size):return None
    return digest.hexdigest() if path.is_file() else None


def diagnostics(source):
    issues=[];aliases={}
    for table in source.get('tables',[]):
        if re.search(r'^(?:information_schema\.|pg_catalog\.|sqlite_|sys\.)',table['name'],re.I):
            issues.append({'kind':'system_table','table':table['name'],'message':'可能是系统表；请按业务需要核对授权，不会自动撤权。'})
        if not table.get('label') and not table.get('business_description'):
            issues.append({'kind':'missing_description','table':table['name'],'message':'缺少业务说明；可在现有字段映射中补充。'})
        for col in table.get('columns',[]):
            if not any(col.get(k) for k in ('label','business_description','business_name','time_semantics')):
                issues.append({'kind':'missing_field_description','table':table['name'],'column':col['name'],'message':'字段缺少业务含义，请补充统计对象、单位或时间含义。'})
            for alias in [col.get('label'),*(col.get('aliases') or [])]:
                if alias:aliases.setdefault(alias,[]).append(table['name']+'.'+col['name'])
            if col.get('business_values') and len({x['label'] for x in col['business_values']})<len(col['business_values']):
                issues.append({'kind':'enum_ambiguity','table':table['name'],'column':col['name'],'message':'一个业务名称对应多个编码。'})
    issues += [{'kind':'alias_ambiguity','name':k,'fields':v,'message':'同名字段位于不同对象，请核对业务含义。'} for k,v in aliases.items() if len(v)>1]
    from .business_ambiguity import reference_issues
    issues.extend(reference_issues(source))
    issues.extend({'kind':'pending_clarification','name':t.get('name'),'message':t.get('clarification_review_reason','澄清选项待修复。')}
                  for t in source.get('terms',[]) if t.get('pending_clarification') and not t.get('clarification'))
    return issues


def relation_query(source, relation):
    from sqlglot import exp
    from .business_sql_checks import DIALECTS
    dialect=DIALECTS.get(source['dialect'],source['dialect'])
    def q(value):return exp.to_identifier(value,quoted=True).sql(dialect=dialect)
    def table(value):return '.'.join(q(part) for part in value.split('.'))
    left,right=table(relation['table']),table(relation['to_table'])
    a,b=relation['from_columns'],relation['to_columns']
    equality=' AND '.join('l.'+q(x)+'=r.'+q(y) for x,y in zip(a,b))
    null_a=' OR '.join(q(x)+' IS NULL' for x in a)
    null_b=' OR '.join(q(x)+' IS NULL' for x in b)
    nonnull=' AND '.join('l.'+q(x)+' IS NOT NULL' for x in a)
    counts={
        'source_rows':f'SELECT COUNT(*) FROM {left}',
        'target_rows':f'SELECT COUNT(*) FROM {right}',
        'source_null_keys':f'SELECT COUNT(*) FROM {left} WHERE {null_a}',
        'target_null_keys':f'SELECT COUNT(*) FROM {right} WHERE {null_b}',
        'source_duplicate_key_groups':f'SELECT COUNT(*) FROM (SELECT '+','.join(q(x) for x in a)+f' FROM {left} GROUP BY '+','.join(q(x) for x in a)+' HAVING COUNT(*)>1) duplicate_source',
        'target_duplicate_key_groups':f'SELECT COUNT(*) FROM (SELECT '+','.join(q(x) for x in b)+f' FROM {right} GROUP BY '+','.join(q(x) for x in b)+' HAVING COUNT(*)>1) duplicate_target',
        'orphan_source_rows':f'SELECT COUNT(*) FROM {left} l WHERE {nonnull} AND NOT EXISTS (SELECT 1 FROM {right} r WHERE {equality})',
    }
    return 'SELECT '+','.join('('+sql+') AS '+q(name) for name,sql in counts.items())


def validate_example(admin, actor, args, host=None):
    if args.get('action') in {'review_sessions','authorize_session','review_history','capture_review','stage_review_example'}:
        from .configuration_reviews import review_action
        return review_action(admin,actor,args,host)
    engine,business=admin.engine,admin.business
    cfg=business.config();project=business.require(cfg,actor,args.get('business_project'),'editor')
    if args.get('expected_revision') is not None and args['expected_revision']!=cfg['revision']:
        raise ConflictError('项目已修改，请刷新后验证。')
    session='admin-validation:'+project['id']
    workspace='admin-validation/'+project['id']
    business.session(actor,session,project['id'],workspace)
    scope=business.resolve(actor,workspace,session)
    sources=engine.catalog(scope)['sources']
    subject='configuration-validation:'+project['id']
    revision,head=engine.artifacts.head(scope,subject,'data.configuration-validation')
    records=copy.deepcopy(head['payload']['records']) if head else []
    by_id={s['id']:s for s in sources}
    def present(record):
        source=by_id.get(record['source_id'])
        current_config=dependency_fingerprint(source,record.get('kind','example'),record.get('example_id') or record.get('relation_id')) if source and record.get('dependency_fingerprint') else fingerprint(source)
        stale=not source or current_config!=record.get('dependency_fingerprint',record['config_fingerprint'])
        current=data_identity(engine.source(scope,source['id'])) if source else None
        stale=stale or (current is not None and current!=record.get('data_fingerprint'))
        return {**record,'freshness':'expired' if stale else 'current' if current else 'unknown',
                'invalid_reason':'配置、授权、结构或数据已变化，请复测。' if stale else '远端数据变更状态未知，请按需复测。' if not current else ''}
    def save_record(record):
        for attempt in range(5):
            latest_revision,latest=engine.artifacts.head(scope,subject,'data.configuration-validation')
            previous=list(latest['payload']['records']) if latest else []
            existing_idx=next((i for i,r in enumerate(previous) if r.get('id')==record.get('id')),None)
            if existing_idx is not None:
                updated_records=list(previous)
                updated_records[existing_idx]=record
            else:
                updated_records=[*previous,record]
            try:
                engine.artifacts.publish(scope,subject,'data.configuration-validation',{'records':updated_records},
                    expected_revision=latest_revision,producer='data.configuration-validation@0.8.0',parents=[])
                return present(record)
            except ConflictError:
                if attempt==4:
                    raise
                time.sleep(0.05*(attempt+1))

    if args.get('action')=='impact':
        from .configuration_impact import compute_impact
        target_version=args.get('version')
        if target_version is not None:
            ver=next((v for v in project.get('versions',[]) if v['version']==target_version),None)
            if not ver:raise ValueError('指定的历史版本不存在。')
            pred=business.previous_version(project, ver['version'])
            before_cfg=pred['config'] if pred else {}
            after_cfg=ver['config']
        else:
            live=next((v for v in project.get('versions',[]) if v['version']==project.get('published_version')),None)
            before_cfg=live['config'] if live else {}
            after_cfg=args.get('draft',project.get('draft',{}))
        presented_records=[present(r) for r in records]
        sources_info={s['id']:s for s in sources}
        impact_result=compute_impact(before_cfg,after_cfg,presented_records,sources_info)
        return {'impact':impact_result,'business_project':project['id']}

    if args.get('action')=='start_trial':
        trial=(host or {}).get('trial_session',{})
        user_sessions={k[len(actor)+1:]:v for k,v in cfg['sessions'].items() if k.startswith(actor+':') and v.get('business_project')==project['id']}
        if str(trial.get('session_id')) not in user_sessions:
            raise PermissionError('原生试问会话未绑定当前用户的该业务项目。')
        examples=[(s,e) for s in sources for e in s.get('examples',[]) if e.get('id')==args.get('example_id')]
        if len(examples)!=1:raise ValueError('代表问题不存在、不唯一或未生效。')
        source,example=examples[0]
        record={'id':uuid.uuid4().hex,'kind':'native_trial','example_id':example['id'],'source_id':source['id'],
                'user':actor,
                'question':example['question'],'session_id':trial['session_id'],'workspace':trial['workspace'],
                'turn':trial.get('turn'),
                'config_fingerprint':fingerprint(source),'dependency_fingerprint':dependency_fingerprint(source,'example',example['id']),
                'data_fingerprint':data_identity(engine.source(scope,source['id'])),
                'expected_fingerprint':fingerprint(example.get('expected_rows')),'at':time.time(),
                'state':'试问已提交','answer_match':None,'termination':'in_progress'}
        return {'record':save_record(record)}

    if args.get('action')=='review_trial':
        review=(host or {}).get('session_review',{})
        origin=next((r for r in records if r['id']==args.get('trial_id') and r.get('kind')=='native_trial'),None)
        if not origin or review.get('session_id')!=origin['session_id'] or review.get('workspace')!=origin['workspace']:
            raise PermissionError('需要本次原生试问的宿主会话证据。')
        if origin.get('user') and origin.get('user')!=actor:
            raise PermissionError('非本人发起的原生试问，无法复核。')
        user_sessions={k[len(actor)+1:]:v for k,v in cfg['sessions'].items() if k.startswith(actor+':') and v.get('business_project')==project['id']}
        if review.get('session_id') not in user_sessions:
            raise PermissionError('需要当前用户在本项目中发起的原生试问会话证据。')
        rev_turn=review.get('turn')
        if rev_turn is None or not isinstance(rev_turn, int):
            raise ValueError('缺少宿主会话轮次证据，不能核对试问结果。')
        if origin.get('turn') is not None and rev_turn!=origin.get('turn'):
            raise ValueError('试问轮次不一致，必须核对发起试问时的对应轮次。')
        if review.get('question')!=origin['question']:
            raise ValueError('请选择代表问题原始轮次，不能用后续问题替代验证。')
        source=by_id.get(origin['source_id'])
        if not source:raise ValueError('数据源未授权或已移除。')
        example=next((e for e in (source or {}).get('examples',[]) if e.get('id')==origin['example_id']),None)
        result_scope=business.resolve(actor,origin['workspace'],origin['session_id'])
        results=[]
        for ref in review.get('result_refs',[])[:100]:
            try:result=engine.get(result_scope,ref)
            except (ValueError,PermissionError):continue
            if result.get('source_id')==origin['source_id'] and result.get('purpose')!='explore':results.append(result)
        expected=(example or {}).get('expected_rows')
        completed=review.get('termination')=='completed'
        matching=[r['result_ref'] for r in results if r.get('status')=='succeeded' and not r.get('truncated') and isinstance(expected,list) and r.get('rows')==expected]
        fresh=present(origin)['freshness']=='current'
        # Matching rows establish result equality, not semantic correctness of prose.
        # Keep answer_match unknown until a separate independent answer review.
        executed=completed and any(r.get('status')=='succeeded' and not r.get('truncated') for r in results)
        record={**origin,'id':uuid.uuid4().hex,'kind':'native_trial_review','trial_id':origin['id'],
                'turn':origin.get('turn') or rev_turn,
                'at':time.time(),'termination':review.get('termination','in_progress'),'answer':review.get('answer',''),
                'result_refs':[r['result_ref'] for r in results],'matching_result_refs':matching,
                'result_match':bool(matching) if isinstance(expected,list) and executed and fresh else None,
                'answer_quality':(host or {}).get('answer_quality',{}),'answer_match':None,
                'state':'执行已验证' if executed else '试问未完成',
                'basis':'结果与预先保存的独立预期核对；模型文字和反馈不作为真值。'}
        return {'record':save_record(record)}

    if args.get('action')=='prepare_trial':
        examples=[(s,e) for s in sources for e in s.get('examples',[]) if e.get('id')==args.get('example_id')]
        if len(examples)!=1:raise ValueError('代表问题不存在、不唯一或未生效。')
        source,example=examples[0]
        return {'question':example['question'],'business_project':project['id'],'source_id':source['id'],
                'config_fingerprint':fingerprint(source),'example_id':example['id']}

    if args.get('action','history') in ('history','diagnose'):
        from .catalog_index import summaries
        question=str(args.get('question',''))[:2000]
        return {'records':[present(r) for r in records], 'diagnostics':{s['id']:diagnostics(s) for s in sources},
                'retrieval':{s['id']:summaries(s,question,8) for s in sources} if question else {},
                'relations':[{'id':r['id'],'name':r['name'],'source_id':s['id'],'cardinality':r['cardinality'],
                              'keys':list(zip(r['from_columns'],r['to_columns']))}
                             for s in sources for r in s.get('business_dictionary',[]) if r.get('kind')=='relation'],
                'states':['配置已生效','执行已验证','答案已核对'],'limits':{'rows':100,'seconds':30,'concurrency':1}}

    if args.get('action')=='relation':
        matches=[(s,r) for s in sources for r in s.get('business_dictionary',[]) if r.get('kind')=='relation' and r.get('id')==args.get('relation_id')]
        if len(matches)!=1:raise ValueError('关联不存在、不唯一或未生效。')
        source,relation=matches[0]
        if not _LOCK.acquire(blocking=False):raise ValueError('已有诊断执行中，请稍后重试。')
        try:
            if getattr(engine,'policy',None):engine.policy.require(scope,'data','query')
            started=time.time();before=data_identity(engine.source(scope,source['id']))
            result=engine.query(scope,{'source_id':source['id'],'sql':relation_query(source,relation),'title':'关联质量诊断','limit':1},'relation-diagnostic:'+uuid.uuid4().hex)
            full=engine.get(scope,result['result_ref']);rows=full.get('rows',[])
            conflicts=bool(rows and (rows[0]['target_duplicate_key_groups'] or relation['cardinality']=='one_to_one' and rows[0]['source_duplicate_key_groups']))
            after=data_identity(engine.source(scope,source['id']))
            executed=result['status']=='succeeded' and bool(rows) and time.time()-started<=30
            state='发现冲突' if executed and conflicts else '已检查符合' if executed and before==after else '尚无法验证'
            record={'id':uuid.uuid4().hex,'kind':'relation','relation_id':relation['id'],'source_id':source['id'],
                    'question':relation['name'],'state':'执行已验证' if executed else '配置已生效','checks_state':state,'checks':rows,
                    'config_fingerprint':fingerprint(source),'dependency_fingerprint':dependency_fingerprint(source,'relation',relation['id']),
                    'data_fingerprint':before,'data_changed_during_run':before!=after,'result_ref':result['result_ref'],
                    'failure':None if executed else result.get('reason_code','INCOMPLETE'),'at':time.time(),'elapsed_seconds':round(time.time()-started,3)}
            return {'state':state,'checks':rows,'result_ref':result['result_ref'],'record':save_record(record),
                    'note':'仅验证本次关联键与基数；空键和孤儿会影响覆盖，不证明整题口径正确。','config_fingerprint':fingerprint(source)}
        finally:_LOCK.release()

    if args.get('action')!='run':raise ValueError('不支持的验证动作。')
    examples=[(s,e) for s in sources for e in s.get('examples',[]) if e.get('id')==args.get('example_id')]
    if len(examples)!=1:raise ValueError('示例不存在、不唯一或未生效。')
    source,example=examples[0]
    if not _LOCK.acquire(blocking=False):raise ValueError('已有诊断执行中，请稍后重试。')
    try:
        started=time.time();before=data_identity(engine.source(scope,source['id']))
        if getattr(engine,'policy',None):engine.policy.require(scope,'data','query')
        from .request_scope import derive, require_valid
        try:
            validation=require_valid(example['logical_sql'],source,derive(source,{'user_text':example['question'],'user_ref':'admin-example:'+example['id']}))
            result=engine.query(scope,{'source_id':source['id'],'sql':example['logical_sql'],'question':example['question'],
                'title':'示例验证','limit':100,'validation':validation},'admin-validation:'+uuid.uuid4().hex)
            full=engine.get(scope,result['result_ref'])
        except ValueError as error:
            code=re.match(r'^([A-Z][A-Z_]+):',str(error))
            result={'status':'failed','reason_code':code.group(1) if code else 'VALIDATION_ERROR'}
            full={}
        after=data_identity(engine.source(scope,source['id']))
        expected=example.get('expected_rows')
        executed=result['status']=='succeeded' and not result.get('truncated') and time.time()-started<=30
        verified=executed and isinstance(expected,list) and full.get('rows')==expected and before is not None and before==after
        record={'id':uuid.uuid4().hex,'kind':'example','example_id':example['id'],'source_id':source['id'],'question':example['question'],
                'config_fingerprint':fingerprint(source),'dependency_fingerprint':dependency_fingerprint(source,'example',example['id']),
                'data_fingerprint':before,'result_ref':result.get('result_ref'),
                'result_fingerprint':fingerprint(full.get('rows')), 'expected_fingerprint':fingerprint(expected) if isinstance(expected,list) else None,
                'state':'答案已核对' if verified else '执行已验证' if executed else '配置已生效',
                'answer_match':True if verified else False if executed and isinstance(expected,list) else None,
                'elapsed_seconds':round(time.time()-started,3),'at':time.time(),
                'basis':'管理员预先保存的独立 expected_rows；不使用模型答案生成预期。',
                'failure':None if executed else result.get('reason_code','INCOMPLETE'),
                'data_changed_during_run':before!=after}
        return {'record':save_record(record),'result':result}
    finally:_LOCK.release()
