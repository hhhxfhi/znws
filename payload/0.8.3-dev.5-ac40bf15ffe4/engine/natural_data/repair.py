"""Bounded schema/dialect repair. Every retry enters the normal safety gateway."""
from __future__ import annotations
import difflib
import re
import sqlglot
from sqlglot import exp

MAX_ATTEMPTS=2

def diagnosis(exc,source):
    text=str(exc)
    patterns=[r'no such column:\s*([\w.]+)',r'column\s+"([\w.]+)"\s+does not exist',r"Unknown column '([\w.]+)'",r'no such table:\s*([\w.]+)']
    for pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match and len(match[1])<=200:return {'kind':'identifier','identifier':match[1]}
    return None

def candidate(sql,source,diagnostic):
    if not diagnostic or diagnostic.get('kind')!='identifier':return None
    dialect={'postgresql':'postgres','tbase':'postgres'}.get(source['dialect'],source['dialect'])
    tree=sqlglot.parse_one(sql,read=dialect)
    identifier=diagnostic['identifier'];qualifier,_,bad=identifier.rpartition('.')
    bad=bad or identifier
    tables={t.alias_or_name:t.name for t in tree.find_all(exp.Table)}
    options=[]
    for t in source['tables']:
        if qualifier and tables.get(qualifier,qualifier)!=t['name'].split('.')[-1]:continue
        if not qualifier and t['name'].split('.')[-1] not in tables.values():continue
        for c in t['columns']:options.append(c['name'])
    scores=sorted([(difflib.SequenceMatcher(None,bad.lower(),name.lower()).ratio(),name) for name in set(options) if name!=bad],reverse=True)
    if not scores or scores[0][0]<.72 or (len(scores)>1 and scores[0][0]-scores[1][0]<.12):return None
    good=scores[0][1];changed=False
    for c in tree.find_all(exp.Column):
        if c.name==bad and (not qualifier or c.table==qualifier):
            c.set('this',exp.Identifier(this=good,quoted=True));changed=True
    return tree.sql(dialect=dialect) if changed else None

def verify_repair(original,proposed,source,diagnostic):
    expected=candidate(original,source,diagnostic)
    if not expected:raise ValueError('REPAIR_UNSAFE: 没有唯一的字段修复；请刷新目录或修订任务，不能自动改变业务逻辑')
    dialect={'postgresql':'postgres','tbase':'postgres'}.get(source['dialect'],source['dialect'])
    if sqlglot.parse_one(expected,read=dialect)!=sqlglot.parse_one(proposed,read=dialect):raise ValueError('REPAIR_SCOPE_CHANGED: 修复不得改变来源、筛选、表达式、关联或业务口径')
    return expected

def execute(engine,project,args,key):
    original=engine._query_once(project,args,key)
    chain=[{'result_ref':original['result_ref'],'status':original['status'],'query_hash':original.get('receipt',{}).get('query_hash')}]
    current=original;sql=args['sql']
    for attempt in range(MAX_ATTEMPTS):
        if args.get('sql_timeout_retry_ref') or current['status']!='failed' or current.get('reason_code')!='SCHEMA_MISMATCH':break
        failed=engine.raw(project,current['result_ref']);source=engine.source(project,args['source_id'])
        new=candidate(sql,source,failed.get('repair_diagnostic'))
        if not new or new==sql:break
        # No change of source, bindings, scope or immutable semantic contract.
        verify_repair(sql,new,source,failed.get('repair_diagnostic'))
        parent=engine.artifacts.put(project,'data.repair',{'original_result':current['result_ref'],'from_sql':sql,'to_sql':new,'attempt':attempt+1,'limit':MAX_ATTEMPTS},producer='data.repair@1.0.0',parents=[failed['plan_ref']],ref='repair:'+current['result_ref']+':'+str(attempt+1))
        current=engine._query_once(project,{**args,'sql':new,'repair_parent_ref':parent},key+':repair:'+str(attempt+1));sql=new
        chain.append({'result_ref':current['result_ref'],'status':current['status'],'query_hash':current.get('receipt',{}).get('query_hash')})
    if len(chain)>1 or current['status']=='failed':
        raw=engine.raw(project,current['result_ref'])
        raw['repair']={'attempts':chain,'limit':MAX_ATTEMPTS,'repaired':len(chain)>1 and current['status']=='succeeded','eligible':bool(candidate(sql,engine.source(project,args['source_id']),raw.get('repair_diagnostic'))) if current['status']=='failed' else False}
        engine.save(project,current['result_ref'],current['status'],raw)
        current=engine.public(engine.get(project,current['result_ref']))
    return current
