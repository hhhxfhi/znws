"""Versioned report layouts over immutable results. Edits never requery a database."""
from __future__ import annotations
import copy
import json
import math
import statistics
import uuid
from collections import defaultdict
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from natural_work.store import ConflictError
from .semantic import Filter

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Measure(Strict):
    column:str
    aggregation:Literal['sum','count','distinct','mean','min','max','weighted_mean']='sum'
    weight_column:str|None=None
class Cell(Strict):
    id:str=Field(min_length=1,max_length=60)
    result_ref:str
    title:str=Field(min_length=1,max_length=160)
    row_dimensions:list[str]=Field(default_factory=list,max_length=6)
    column_dimensions:list[str]=Field(default_factory=list,max_length=4)
    measures:list[Measure]=Field(min_length=1,max_length=10)
    filters:list[Filter]=Field(default_factory=list,max_length=30)
    chart_type:Literal['table','column','bar','line','pie','heatmap','pivot-table']='pivot-table'
    sort_by:str|None=None
    descending:bool=False
    row_limit:int=Field(default=500,ge=1,le=500)
class Spec(Strict):
    title:str=Field(min_length=1,max_length=160)
    columns:int=Field(default=2,ge=1,le=4)
    cells:list[Cell]=Field(min_length=1,max_length=12)
class Create(Strict):
    spec:Spec
    attach_to_result_ref:str|None=None
    expected_attachment_revision:int=Field(default=0,ge=0)
class Edit(Strict):
    result_ref:str
    expected_revision:int=Field(ge=1)
    spec:Spec
class Read(Strict):result_ref:str
AGGREGATIONS={'sum':'合计','count':'计数','distinct':'去重计数','mean':'算术平均','min':'最小值','max':'最大值','weighted_mean':'加权平均'}

def build_cell(engine,project,cell):
    result=engine.get(project,cell.result_ref)
    if result.get('report'):raise ValueError('REPORT_NESTING: 报表单元必须引用查询结果')
    if result['status']!='succeeded' or result.get('truncated'):raise ValueError('REPORT_DATA: 需要完整成功的结果')
    columns=result['columns'];dims=cell.row_dimensions+cell.column_dimensions
    if len(set(dims))!=len(dims) or any(d not in columns for d in dims):raise ValueError('REPORT_DIMENSIONS: 无效或重复维度')
    if cell.sort_by and cell.sort_by not in dims+[m.column for m in cell.measures]:raise ValueError('REPORT_SORT: 排序字段不在报表中')
    rows=result['rows']
    for f in cell.filters:
        if f.dimension not in columns:raise ValueError('REPORT_FILTER: 筛选字段不存在')
        if f.op!='in' and len(f.values)!=1:raise ValueError('REPORT_FILTER: 单值筛选需要一个值')
        def matches(r):
            raw=r.get(f.dimension)
            if raw is None:return False
            if type(raw) in (int,float):
                try:values=[float(x) for x in f.values]
                except ValueError:raise ValueError('REPORT_FILTER: 数值字段需数值筛选') from None
            else:raw=str(raw);values=f.values
            return raw in values if f.op=='in' else {'eq':lambda:raw==values[0],'ne':lambda:raw!=values[0],'gt':lambda:raw>values[0],'gte':lambda:raw>=values[0],'lt':lambda:raw<values[0],'lte':lambda:raw<=values[0]}[f.op]()
        rows=[r for r in rows if matches(r)]
    grouped=defaultdict(list)
    for row in rows:grouped[tuple(row.get(d) for d in dims)].append(row)
    out=[];labels={k:result.get('column_labels',{}).get(k,k) for k in dims};measure_columns=[]
    for m in cell.measures:
        if m.column not in columns or m.weight_column and m.weight_column not in columns:raise ValueError('REPORT_MEASURE: 字段不存在')
        key=m.aggregation+'_'+m.column
        if key in measure_columns:raise ValueError('REPORT_MEASURE: 重复度量')
        measure_columns.append(key);labels[key]=AGGREGATIONS[m.aggregation]+' · '+result.get('column_labels',{}).get(m.column,m.column)
        contract=result.get('metric_contract',{})
        if m.aggregation=='sum' and (contract.get('ratio') or ('period' not in dims and len({r.get('period') for r in rows})>1 and not contract.get('additive_over_time',False))):raise ValueError('REPORT_NONADDITIVE: 比率或跨期快照不能求和；保留期间或选择明确的均值/加权口径')
    for key,rs in grouped.items():
        row=dict(zip(dims,key))
        for m,outcol in zip(cell.measures,measure_columns):
            values=[r.get(m.column) for r in rs]
            if m.aggregation=='count':value=len(values)
            elif m.aggregation=='distinct':value=len({json.dumps(v,sort_keys=True,ensure_ascii=False) for v in values if v is not None})
            elif any(type(v) not in (int,float) or not math.isfinite(v) for v in values):value=None
            elif m.aggregation=='weighted_mean':
                if not m.weight_column:raise ValueError('REPORT_WEIGHT: 需指定实际返回的权重字段')
                weights=[r.get(m.weight_column) for r in rs]
                if any(type(w) not in (int,float) or not math.isfinite(w) or w<0 for w in weights):value=None
                else:value=sum(v*w for v,w in zip(values,weights))/sum(weights) if sum(weights) else None
            else:value={'sum':sum,'mean':statistics.mean,'min':min,'max':max}[m.aggregation](values)
            row[outcol]=value
        out.append(row)
    if cell.sort_by:
        field=cell.sort_by if cell.sort_by in dims else next(k for k in measure_columns if k.endswith('_'+cell.sort_by))
        out.sort(key=lambda r:(r.get(field) is None,r.get(field) if r.get(field) is not None else ''),reverse=cell.descending)
    if len(out)>cell.row_limit:raise ValueError('REPORT_LIMIT: 聚合结果超过所选行数，先筛选；不会保存截断报表')
    note='统计只覆盖筛选后的保留结果；缺失值保留为未知。'
    if any(m.aggregation=='mean' for m in cell.measures):note+='均值为返回观测的算术平均，不代替总体加权口径。'
    if any(m.aggregation=='weighted_mean' for m in cell.measures):note+='加权均值只使用指定的实际权重。'
    payload={'title':cell.title,'columns':dims+measure_columns,'rows':out,'column_labels':labels,'status':'succeeded','row_count':len(out),'source_provenance':result.get('source_provenance',{}),'receipt':result.get('receipt'),'source_result':result['result_ref'],'note':note}
    from .vendor.charts import recommend_chart
    payload['chart']=recommend_chart(payload['columns'],out,explicit_type=cell.chart_type,title=cell.title,labels=labels)
    if cell.chart_type=='pivot-table':
        row_count=len({tuple(r.get(d) for d in cell.row_dimensions) for r in out})
        col_count=len({tuple(r.get(d) for d in cell.column_dimensions) for r in out})
        # Cells are already aggregated. Hidden totals avoid summing ratios or means.
        totals={axis:{'showGrandTotals':False,'showSubTotals':False,
                      'subTotalsDimensions':[],'calcTotals':{'aggregation':'MAX'}}
                for axis in ('row','col')}
        payload['chart']={'type':'pivot-table','encoding':{},'pivot':{
            'rows':cell.row_dimensions,'columns':cell.column_dimensions,
            'values':measure_columns,'valueInCols':True,
            'aggregations':{k:'max' for k in measure_columns},'totals':totals,
            'hierarchyType':'grid','cardinality':{'rows':row_count,'columns':col_count,'cells':row_count*col_count},
            'aggregated':True,'note':payload['note']},'allowedAlternatives':['pivot-table','table']}
    return payload,result['artifact_ref']

def publish(engine,project,spec,rid,expected_revision,key=''):
    if len({c.id for c in spec.cells})!=len(spec.cells):raise ValueError('REPORT_DUPLICATE: 单元标识重复')
    cells=[];parents=[]
    for cell in spec.cells:
        value,parent=build_cell(engine,project,cell);cells.append({**value,'id':cell.id});parents.append(parent)
    layout={'spec':spec.model_dump(),'cells':cells,'title':spec.title,'columns':spec.columns}
    head,revision=engine.artifacts.publish(project,rid,'data.report-layout',layout,expected_revision=expected_revision,producer='data.report-layout@1.0.0',parents=list(dict.fromkeys(parents)))
    return {'report_revision':revision,'report_ref':head,'spec':spec.model_dump(),'cell_count':len(cells)}

def create(engine,project,args,key):
    if key:
        with engine.connect() as c:old=c.execute('SELECT id FROM runs WHERE project=? AND request_key=?',(project,key)).fetchone()
        if old:return engine.public(engine.get(project,old[0]))
    rid=uuid.uuid4().hex
    key=key or 'report:'+rid
    first=engine.get(project,args.spec.cells[0].result_ref)
    # Only insert a durable run after all source cells and semantics have validated.
    metadata=publish(engine,project,args.spec,rid,0,key)
    payload={k:copy.deepcopy(first[k]) for k in ('source_id','source_provenance','business_project','sql','receipt','plan_ref') if k in first}
    payload.update(title=args.spec.title,columns=[],rows=[],row_count=0,truncated=False,report=metadata)
    with engine.connect() as c:c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',(rid,project,key,'report:'+rid,'succeeded',json.dumps(payload,ensure_ascii=False)))
    if args.attach_to_result_ref:
        if args.attach_to_result_ref not in {c.result_ref for c in args.spec.cells}:raise ValueError('REPORT_ATTACHMENT: 仅可附加到当前报表输入结果')
        engine.artifacts.publish(project,args.attach_to_result_ref,'data.report-display',{'result_ref':rid},expected_revision=args.expected_attachment_revision,producer='data.report-display@1.0.0',parents=[metadata['report_ref']])
    return engine.public(engine.get(project,rid))

def edit(engine,project,args):
    current=engine.get(project,args.result_ref)
    if not current.get('report'):raise ValueError('REPORT_TYPE: 需要多维报表结果')
    publish(engine,project,args.spec,args.result_ref,args.expected_revision)
    return engine.public(engine.get(project,args.result_ref))

def restore(engine,project,result):
    revision,head=engine.artifacts.head(project,result['result_ref'],'data.report-layout')
    if not head:return result
    spec=Spec.model_validate(head['payload']['spec'])
    # Recheck every parent under the current grants, including after publication/revocation.
    for cell in spec.cells:engine.get(project,cell.result_ref)
    result['report']={**head['payload'],'report_revision':revision,'report_ref':head['ref']}
    result['title']=spec.title;result['row_count']=sum(c['row_count'] for c in result['report']['cells'])
    result['columns']=['cell','dimension','measure','value'];result['rows']=[]
    for cell in result['report']['cells']:
        dimensions=set(next(c for c in spec.cells if c.id==cell['id']).row_dimensions+next(c for c in spec.cells if c.id==cell['id']).column_dimensions)
        for row in cell['rows']:
            for measure in set(cell['columns'])-dimensions:result['rows'].append({'cell':cell['title'],'dimension':json.dumps({d:row[d] for d in dimensions},ensure_ascii=False),'measure':measure,'value':row[measure]})
    return result
