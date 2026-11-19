from typing import Literal
import math
import re
from pydantic import BaseModel,ConfigDict,Field
from natural_work.runtime import Capability,Operation
from .claims import finalize, model_claims, trend_summary

# 0.6.3: consecutive number-read failures in one session before telling the model to stop.
NUMERIC_READ_LIMIT=2

MODEL_CLAIMS=30
class Input(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
class ListSkills(Input):pass
class Compute(Input):
 result_ref:str
 comparison_result_ref:str|None=Field(default=None,description='两个分开查询的同口径指标结果，比较时填另一份 result_ref；服务端校验来源、口径、单位和范围，禁止任意合并。')
 column:str|None=Field(default=None,description='SQL结果做share/rank/month_over_month/branch_compare时填写实际数值列，指标facts不填。SQL环比还需time_column；SQL单值阈值比较选择明确标注百分比的列，如数值76.03表示76.03%。')
 time_column:str|None=Field(default=None,description='仅用于SQL结果的month_over_month。填写实际返回的月份列，值为YYYY-MM，同一范围内每个月恰有一行且月份连续。')
 operation:Literal['period_compare','aggregate_compare','share','rank','threshold_filter','branch_compare','reconcile','month_over_month','compare_values','cumulative_share','sum']=Field(
     description='计算类型。两个fact数值比较用compare_values；单个fact与业务阈值比较用branch_compare+threshold；文本声称数值核对用reconcile+stated_value。'
 )
 fact_ids:list[str]|None=None
 sum_basis:Literal['non_overlapping','category_occurrences']='non_overlapping'
 baseline_fact_id:str|None=Field(default=None,description='compare_values的基期fact_id或本结果sum返回的claim_id；branch_compare在双fact差额比较时用fact_id。')
 current_fact_id:str|None=Field(default=None,description='本期fact_id或本结果sum的claim_id；两个引用须同时提供。多个期间合计比较优先用aggregate_compare。')
 baseline_periods:list[str]|None=Field(default=None,max_length=120,description='aggregate_compare的完整基期列表，如["2024-04","2024-05","2024-06"]。')
 current_periods:list[str]|None=Field(default=None,max_length=120,description='aggregate_compare的完整本期列表，如["2025-04","2025-05","2025-06"]；一次返回各期差额、变化率与合计比较，不先sum。')
 baseline_period:str|None=None
 current_period:str|None=None
 threshold:float|None=Field(default=None,description='业务阈值。用于branch_compare（单个fact或SQL单行百分比字段与业务阈值判断）或threshold_filter；百分比阈值60%填60。compare_values比较两个fact不使用threshold。')
 change_threshold:float|None=Field(default=None,description='仅aggregate_compare：判断合计变化率是否满足comparison，例如同比超过10%填10、comparison填>。')
 comparison:Literal['<','<=','>','>=','==']='<'
 stated_value:float|None=Field(default=None,description='仅用于reconcile核对文本声称数值；比较两个fact用compare_values，单个fact与业务阈值比较用branch_compare+threshold，不要将stated_value当作threshold。')
class Skill(Input):
 result_ref:str
 skill_id:str
 version:str='1.0.0'
class FieldStatistics(Input):
 result_ref:str
 column:str=Field(min_length=1,max_length=200)
 operation:Literal['sum','mean','min','max','count','distinct_count']
class Output(Input):
 status:Literal['succeeded','failed']='succeeded'
 artifact_ref:str|None=None
 result_ref:str|None=None
 claims:list[dict]=Field(default_factory=list)
 skills:list[dict]|None=None
 limitations:list[str]=Field(default_factory=list)
 claims_total:int|None=None
 branch_dependency:str|None=None
 branch_note:str|None=None
 note:str|None=None
SKILLS=[{'id':'distribution_profile','version':'1.0.0','title':'分布分析','category':'通用分析','required_metrics':[],'requires':'同量纲分组事实','operators':['rank','share'],'causal':False},
 {'id':'economic_efficiency','version':'1.0.0','title':'资金执行分析','category':'业务分析','required_metrics':['project_execution'],'requires':'project_execution合约；不能用投入金额直接当效能','operators':['rank'],'causal':False},
 {'id':'anomaly_attribution','version':'1.0.0','title':'统计异常识别','category':'通用分析','required_metrics':[],'requires':'至少4个同量纲事实','operators':['IQR'],'causal':False}]

EXTRA_SKILLS={}
def register_skill(definition,handler):
 required={'id','version','permission','input_kind','output_kind','requires','test_pack','limitations'}
 if not required <= set(definition):raise ValueError('技能缺少合约字段')
 if definition['permission']!='data.read' or definition['input_kind']!='data.dataset' or definition['output_kind']!='analysis.claims':raise ValueError('当前分析适配器仅接收只读数据产物并输出结论')
 key=(definition['id'],definition['version'])
 if key in EXTRA_SKILLS or any((x['id'],x['version'])==key for x in SKILLS):raise ValueError('重复技能版本')
 EXTRA_SKILLS[key]=(dict(definition),handler)

def definitions(engine=None,project=None):
 """Everything a project switch can refer to, including the three legacy named skills."""
 return SKILLS+listed_definitions(engine,project)

def listed_definitions(engine=None,project=None):
 """0.7.0: what the 配置管理 page and analysis_list show — public methods (merged ids once,
 Chinese names), registered plugin skills and the project's own skills. The legacy named
 skills stay runnable through analysis_run for old references but are not listed."""
 from .methods import definitions as matrix,custom_definitions
 return [x[0] for x in EXTRA_SKILLS.values()]+matrix()+(custom_definitions(engine,project) if engine and project else [])

# 0.7.0: what the model gets from analysis_list — enabled methods only, short Chinese entries.
DIVISION=('分工：指标查询的同比环比、排名、占比和阈值判断用 analysis_compute；'
          '单份SQL月序列的环比用 analysis_compute 的 month_over_month+column+time_column，'
          'SQL单行百分比与阈值比较用 branch_compare+column+threshold+comparison。'
          '其他基础分析用 analysis_method，method_id 从下方已启用的方法选择。')
def model_list(engine,project):
 from .methods import DEFAULT_REQUIRES
 enabled=lambda sid:not getattr(engine,'policy',None) or engine.policy.skill_enabled(project,sid)
 items=[]
 for d in listed_definitions(engine,project):
  if not enabled(d['id']):continue
  item={'id':d['id'],'name':d.get('title') or d['id'],'category':d.get('category','')}
  if d['id'].startswith('custom.'):
   cfg=d.get('config',{})
   preset=[*(['字段 '+'、'.join(cfg['columns'])] if cfg.get('columns') else []),*(['时间 '+cfg['time_column']] if cfg.get('time_column') else []),*(['分组 '+'、'.join(cfg['dimensions'])] if cfg.get('dimensions') else [])]
   item.update(version=d['version'],based_on=d.get('based_on',''),**({'preset':'；'.join(preset)} if preset else {}))
  elif d.get('requires') and d['requires']!=DEFAULT_REQUIRES:
   item['requires']=d['requires']
  items.append(item)
 return {'skills':items,'note':DIVISION}

def capability(engine):
 def read_result(ref,ctx):
  if ref.startswith('data:'):
   artifact=engine.artifacts.get(ctx.project,ref)
   if artifact['kind']!='data.dataset':raise ValueError('分析需要数据集产物。')
   ref=artifact['payload']['result_ref']
  return engine.get(ctx.project,ref)
 def facts(args,ctx):
  r=read_result(args.result_ref,ctx)
  if r['status']!='succeeded' or r.get('truncated'):raise ValueError('必须使用完整且成功的结果；截断样本不能冒充全量分析')
  rows=r.get('facts')
  if not rows and r.get('metric_contract',{}).get('detail'):
   # Keep business identifiers and detail rows inside the server. Only a
   # deterministic summary and opaque row references leave this boundary.
   rows=[{'id':r['result_ref']+':'+str(i),'dimensions':{'period':row.get('period')},'value':row.get('value'),'unit':r['metric_contract']['unit']} for i,row in enumerate(r['rows'])]
  if not rows:raise ValueError('NO_ANALYSIS_FACTS: SQL结果使用实际列名。占比、排名填column；单份月序列环比用month_over_month填column和time_column；单行百分比阈值判断用branch_compare填column、threshold和comparison。简单合计、均值用analysis_field_statistics。')
  if any(type(f['value']) not in (int,float) or not math.isfinite(f['value']) for f in rows):raise ValueError('UNKNOWN_VALUE: 结果包含未知或非有限数值；不能将其视为零，具体原因需查阅查询依据。')
  if len(rows)!=r['row_count']:raise ValueError('事实投影未覆盖全部结果，请缩小范围')
  return r,rows
 def publish(ctx,r,claims,operation,limitations=()):
  claims=finalize(claims,r['metric_contract'])
  payload={'claims':claims,'limitations':list(limitations),'metric_contract':r['metric_contract'],'source_result':r['result_ref']}
  ref=engine.artifacts.put(ctx.project,'analysis.claims',payload,producer='analysis.'+operation+'@1.0.0',parents=list(dict.fromkeys([r['artifact_ref']]+[c['comparison_artifact_ref'] for c in claims if c.get('comparison_artifact_ref')])))
  revision,head=engine.artifacts.head(ctx.project,r['result_ref'],'analysis.claims')
  combined={x['claim_id']:x for x in (head['payload'].get('claims',[]) if head else [])}
  combined.update({x['claim_id']:x for x in claims})
  engine.artifacts.publish(ctx.project,r['result_ref'],'analysis.claims',{'verified_claims':list(combined.values()),'claims':list(combined.values()),'claim_limitations':list(limitations)},expected_revision=revision,producer='analysis.claims@2.0.0',parents=[ref])
  # All claims stay in the artifact and the result card. The model gets the
  # first MODEL_CLAIMS so ranking/MoM over many rows cannot overflow context.
  out={'artifact_ref':ref,'result_ref':r['result_ref'],'claims':model_claims(claims[:MODEL_CLAIMS]),'limitations':list(limitations)}
  # 0.6.3: a branch judgement hands back the exact value a conditional drill-down
  # task needs, so a small model copies it instead of inventing a shape.
  branch=[c for c in claims if c.get('operation')=='branch_compare' and type(c.get('matched')) is bool]
  if len(branch)==1:
   import json as _json
   out['branch_dependency']=_json.dumps({'kind':'claim','artifact_ref':ref,'claim_id':branch[0]['claim_id'],'expected':True},ensure_ascii=False)
   out['branch_note']=('条件成立，可以直接按要求拆分（重新 data_plan 即可）。' if branch[0]['matched'] else '条件不成立，不要拆分，按用户的"否则"要求回答。')
  if len(claims)>MODEL_CLAIMS:out.update(claims_total=len(claims),note=f'共{len(claims)}条结论，回执只含前{MODEL_CLAIMS}条；完整结论在结果卡中。回答时只引用回执中的结论。')
  return out
 def compute_sql_comparison(args,ctx,raw,calculate):
  rows=raw.get('rows') or []
  if (raw['status']!='succeeded' or raw.get('truncated') or not rows
      or len(rows)!=raw.get('row_count') or not raw.get('sql')
      or (raw.get('receipt') or {}).get('read_only') is not True):
   raise ValueError('ANALYSIS_PREREQUISITE: 需要完整、非空且成功的只读SQL结果。')
  if (raw.get('validation') or {}).get('state')=='conflict' or (raw.get('checks') or {}).get('value_warnings'):
   raise ValueError('ANALYSIS_PREREQUISITE: 查询结果存在待修正的范围或数值警告，请先核查。')
  lineage=raw.get('result_lineage') or {}
  if raw.get('purpose')=='explore' or lineage.get('purpose')=='explore':
   raise ValueError('ANALYSIS_SCOPE: 探索查询不能用于正式比较，请使用与原问范围一致的答复查询。')
  if lineage.get('session_id') and lineage['session_id']!=ctx.session_id:
   raise ValueError('ANALYSIS_SCOPE: 比较结果须来自当前会话。')
  columns=raw['columns']
  if args.column not in columns:
   raise ValueError('ANALYSIS_COLUMN: column 必须是实际返回的数值字段。')
  from .methods import finite
  values=[row.get(args.column) for row in rows]
  if not all(finite(value) for value in values):
   raise ValueError('UNKNOWN_VALUE: 数值列存在空值、文本或非有限值，不能补零。')
  from natural_data.chart_selection import heterogeneous_units
  if heterogeneous_units(raw):
   raise ValueError('ANALYSIS_UNIT: 结果含不同计量单位，须先按同一单位查询。')
  label=(raw.get('column_labels') or {}).get(args.column) or args.column
  # Use an explicit percentage label. A bare “率” or a value between 0 and
  # 1 does not tell us whether the SQL returned a fraction or a percentage.
  suffix=re.search(r'[（(]([^()（）\r\n]{1,16})[）)]\s*$',label)
  unit=suffix[1].strip() if suffix and not suffix[1].strip().isdecimal() else ''
  if unit in ('%','％','百分比') or any(
      re.search(r'(?:[%％]|百分比|(?:^|[_\s])percent(?:age)?)\s*$',name,re.I)
      for name in (args.column,label)):
   unit='%'
  monthly=args.operation=='month_over_month'
  if monthly:
   if args.time_column not in columns or args.time_column==args.column:
    raise ValueError('ANALYSIS_TIME: time_column 必须是实际返回的月份字段。')
   periods=[row.get(args.time_column) for row in rows]
   if len(periods)<2 or any(not isinstance(p,str) or not re.fullmatch(r'(?!0000)[0-9]{4}-(?:0[1-9]|1[0-2])',p) for p in periods):
    raise ValueError('ANALYSIS_TIME: 环比至少需要两个YYYY-MM格式的月份。')
   if len(set(periods))!=len(periods):
    raise ValueError('ANALYSIS_TIME: 同一个月只能有一行，请先按同一统计范围查询。')
   months=[12*int(p[:4])+int(p[5:]) for p in periods]
   if max(months)-min(months)+1!=len(months):
    raise ValueError('ANALYSIS_TIME: 月份不连续，缺月不能补零或作为上月。')
   if any(any(row.get(c)!=rows[0].get(c) for row in rows[1:])
          for c in columns if c not in (args.column,args.time_column)):
    raise ValueError('ANALYSIS_SCOPE: 其他返回字段随月份变化。请按同一统计范围查询月份和所选数值列。')
  else:
   if len(rows)!=1:
    raise ValueError('ANALYSIS_PREREQUISITE: SQL阈值比较需要单行结果，请先按原问查询单个比率。')
   if unit!='%':
    raise ValueError('ANALYSIS_UNIT: 阈值比较需选择明确以百分数表示的实际列，如“执行率（%）”；不会将小数猜测为百分数。')
   if args.threshold is None or not math.isfinite(args.threshold):
    raise ValueError('ANALYSIS_THRESHOLD: 请提供与字段单位一致的有限百分比阈值，如60%填60。')
   periods=[None]
  projected=[
   {'id':f"{raw['result_ref']}:{i}",'value':value,'unit':unit,
    'dimensions':{'period':period} if monthly else {'column':label}}
   for i,(period,value) in enumerate(zip(periods,values))
  ]
  contract={'id':'sql_field_comparison','version':'1.0.0','unit':unit,
            'scope':{'column':args.column,'source_id':raw['source_id']}}
  if monthly:contract['scope']['time_column']=args.time_column
  r={**raw,'metric_contract':contract}
  claims=calculate(args,r,projected)
  if any(claim.get(key) is not None and not finite(claim[key])
         for claim in claims for key in ('difference','change_percent')):
   raise ValueError('UNKNOWN_VALUE: 差额或变化率超出可计算范围。')
  for claim in claims:
   claim['column']=args.column
   if monthly:claim['time_column']=args.time_column
  if not monthly:claims[0].update(operation='branch_compare',difference_unit='百分点')
  notes=['仅比较同一完整查询结果中的指定字段，沿用原字段数值和单位。']
  from natural_data.answer_presentation import calculation_notes
  notes.extend(calculation_notes((raw.get('sql_answer_contract') or {}).get('limitations')))
  return publish(ctx,r,claims,args.operation,limitations=notes)
 def compute(args,ctx):
  from .operators_v2 import compute as calculate
  # `value` is the canonical metric fact value, not a separate SQL statistic.
  # Accept that redundant selector only after resolving the authorized result;
  # the ordinary fact path still enforces completeness, additivity and scope.
  if args.column=='value':
   source=read_result(args.result_ref,ctx)
   if source.get('facts') and source.get('metric_contract') and 'value' in source.get('columns',[]):
    args=args.model_copy(update={'column':None})
  if args.column is not None:
   incompatible=('comparison_result_ref','baseline_fact_id','current_fact_id','baseline_periods',
                 'current_periods','baseline_period','current_period','change_threshold','stated_value')
   if (args.operation not in ('share','rank','month_over_month','branch_compare')
       or any(getattr(args,k) is not None for k in incompatible)
       or args.fact_ids
       or args.operation!='branch_compare' and args.threshold is not None
       or args.operation!='month_over_month' and args.time_column is not None):
    raise ValueError('ANALYSIS_COLUMN: SQL字段分析支持share、rank、month_over_month或单行百分比branch_compare，不能混用指标事实或分开查询结果。')
   raw=read_result(args.result_ref,ctx)
   if raw.get('metric_contract') or raw.get('facts'):raise ValueError('ANALYSIS_COLUMN: 指标结果使用facts分析，不通过SQL字段绕过指标口径。')
   if args.operation in ('month_over_month','branch_compare'):
    return compute_sql_comparison(args,ctx,raw,calculate)
   from .methods import Method
   return method(Method(result_ref=raw['result_ref'],method_id='matrix.13' if args.operation=='share' else 'matrix.10',columns=[args.column]),ctx)
  if args.time_column is not None:raise ValueError('ANALYSIS_COLUMN: SQL环比需要同时填写实际数值列column和月份列time_column。')
  if args.change_threshold is not None and (args.operation!='aggregate_compare' or not math.isfinite(args.change_threshold)):
   raise ValueError('ANALYSIS_THRESHOLD: change_threshold仅支持合计变化率比较，且必须为有限数值。')
  r,rows=facts(args,ctx)
  comparison=None
  if args.comparison_result_ref:
   from .result_comparison import combine
   if args.operation not in ('aggregate_compare','period_compare','compare_values','branch_compare'):
    raise ValueError('COMPARISON_SCOPE: comparison_result_ref仅用于同口径期间或数值比较。')
   comparison,other=facts(args.model_copy(update={'result_ref':args.comparison_result_ref}),ctx)
   r,rows=combine(r,rows,comparison,other)
  if args.operation=='compare_values':
   # Only server-persisted sums from this authorized result may become operands.
   # Recalculate from immutable source facts rather than trusting a supplied value.
   derived=[]
   for origin in ([r,comparison] if comparison else [r]):
    _,head=engine.artifacts.head(ctx.project,origin['result_ref'],'analysis.claims')
    sums={c['claim_id']:c for c in (head or {}).get('payload',{}).get('claims',[]) if c.get('operation')=='sum'}
    for ref in (args.baseline_fact_id,args.current_fact_id):
     if ref not in sums:continue
     claim=sums[ref]
     fresh=calculate(Compute(result_ref=origin['result_ref'],operation='sum',fact_ids=claim['included_fact_ids'],sum_basis=claim.get('sum_basis','non_overlapping')),r,rows)[0]
     derived.append({'id':ref,'value':fresh['value'],'unit':r['metric_contract']['unit'],
                     'dimensions':{k:'、'.join(v) for k,v in fresh['dimensions'].items()},'derived_from':fresh['included_fact_ids']})
   rows=rows+derived
  if r['metric_contract'].get('detail'):
   if args.operation!='threshold_filter':raise ValueError('DETAIL_ANALYSIS_UNSUPPORTED: 明细仅支持服务端阈值汇总；不向模型返回业务明细。')
   checks=calculate(args,r,rows)
   vals=[f['value'] for f in rows]
   claims=[{'operation':'detail_threshold_summary','comparison':args.comparison,'threshold':args.threshold,'count':len(rows),'matched_count':sum(c['matched'] for c in checks),'minimum':min(vals),'maximum':max(vals),'included_fact_ids':[f['id'] for f in rows]}]
   return publish(ctx,r,claims,args.operation,limitations=['只验证当前结果内的明细；不证明结果外不存在其他项目。'])
  claims=calculate(args,r,rows)
  if comparison:
   for claim in claims:claim['comparison_artifact_ref']=comparison['artifact_ref'];claim['comparison_result_ref']=comparison['result_ref']
  if args.change_threshold is not None:
   from operator import lt,le,gt,ge,eq
   for claim in claims:
    if claim.get('operation')=='aggregate_compare':
     value=claim.get('change_percent')
     claim.update(change_threshold=args.change_threshold,change_comparison=args.comparison,
                  change_matched=None if value is None else {'<':lt,'<=':le,'>':gt,'>=':ge,'==':eq}[args.comparison](value,args.change_threshold))
  return publish(ctx,r,claims,args.operation,limitations=r['metric_contract'].get('limitations',[]))
 def field_statistics(args,ctx):
  import statistics
  ref=args.result_ref
  if ref.startswith('data:'):
   artifact=engine.artifacts.get(ctx.project,ref)
   if artifact['kind']!='data.dataset':raise ValueError('统计需要数据集产物。')
   ref=artifact['payload']['result_ref']
  r=engine.get(ctx.project,ref)
  if r['status']!='succeeded' or r.get('truncated'):raise ValueError('ANALYSIS_PREREQUISITE: 需要完整成功的查询结果，截断结果不能作总体统计。')
  if args.column not in r.get('columns',[]):raise ValueError('ANALYSIS_COLUMN: 请选择实际返回的字段。')
  values=[row.get(args.column) for row in r.get('rows',[])]
  missing=sum(v is None for v in values)
  if args.operation=='count': value=len(values)-missing
  elif args.operation=='distinct_count': value=len({json_value(v) for v in values if v is not None})
  else:
   text=next((v for v in values if isinstance(v,str)),None)
   if text is not None:raise ValueError(f'NON_NUMERIC_COLUMN: 字段「{args.column}」是文本（例如“{text[:20]}”），不能做数值统计；改写 SQL 加 CAST 不会改变结果类型。请改用 analysis_options.numeric_columns 中的字段，或用 distinct_count/count，或直接按结果卡内容回答。')
   if not values or missing or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise ValueError(f'UNKNOWN_VALUE: 字段「{args.column}」为空或有 {missing} 个空值，不能补零统计。')
   contract=r.get('metric_contract',{})
   if args.operation=='sum' and (contract.get('ratio') or contract.get('aggregation')=='snapshot' or contract.get('additive_over_time') is False or 'snapshot_sum' in str(contract.get('semantic_spec',{}))):raise ValueError('NON_ADDITIVE: 当前指标为比率、快照或不可加指标，不能绕过口径求和。')
   value={'sum':sum,'mean':statistics.mean,'min':min,'max':max}[args.operation](values)
  if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('UNKNOWN_VALUE: 统计结果不是有限数值。')
  title={'sum':'合计','mean':'算术平均','min':'最小值','max':'最大值','count':'非空记录数','distinct_count':'不同值数量'}[args.operation]
  r.setdefault('metric_contract',{'id':'field_statistics','version':'1.0.0','unit':'原字段单位','scope':{'column':args.column}})
  text=f"当前完整查询结果中，{args.column}的{title}为{value:,.6f}".rstrip('0').rstrip('.')+f"；结果共{len(values)}行，空值{missing}行。"
  notes=['仅按当前结果字段统计；单位和业务含义沿用原字段，不自动推断实体去重、总体覆盖或因果。']
  claim={'operation':'field_statistics','column':args.column,'statistic':args.operation,'value':value,'count':len(values),'missing_count':missing,'computed_text':text}
  return publish(ctx,r,[claim],'field_statistics',notes)
 def json_value(value):
  import json
  return json.dumps(value,sort_keys=True,ensure_ascii=False)
 for entry in SKILLS:
  entry.update(permission='data.read',input_kind='data.dataset',output_kind='analysis.claims',test_pack='analysis/'+entry['id']+'/1',limitations=['仅已授权的完整同口径事实；不推断因果。'])
 def method(args,ctx):
  from .methods import calculate,custom_definitions,Method
  selected_id,selected_version=args.method_id,args.method_version
  if not args.method_id.startswith('custom.') and args.method_version!='1.0.0':raise ValueError('UNKNOWN_METHOD_VERSION: 当前方法版本不可用')
  from .methods import public_id,known_method
  if not args.method_id.startswith('custom.') and not known_method(args.method_id):
   from .methods import definitions as _all
   usable=[d for d in _all() if not getattr(engine,'policy',None) or engine.policy.skill_enabled(ctx.project,d['id'])]
   raise ValueError('UNKNOWN_METHOD: 没有「'+args.method_id+'」这个方法。method_id 请用编号，可用的有：'+'；'.join(d['id']+' '+d['title'].split('（')[0] for d in usable))
  if not args.method_id.startswith('custom.'):
   resolved=public_id(args.method_id)
   if resolved!=args.method_id:args=args.model_copy(update={'method_id':resolved});selected_id=resolved
  if getattr(engine,'policy',None) and not engine.policy.skill_enabled(ctx.project,public_id(args.method_id)):raise PermissionError('SKILL_DISABLED: 当前方法已停用；可用方法见 analysis_list')
  if args.method_id.startswith('custom.'):
   entry=next((x for x in custom_definitions(engine,ctx.project) if x['id']==args.method_id and x['version']==args.method_version),None)
   if not entry:raise ValueError('UNKNOWN_SKILL: 未发布的项目技能')
   spec=entry['config']
   args=Method.model_validate({**args.model_dump(),'method_id':spec['method_id'],'columns':spec.get('columns',[]),'dimensions':spec.get('dimensions',[]),'time_column':spec.get('time_column'),'options':spec.get('options',{})})
  ref=args.result_ref
  if ref.startswith('data:'):
   artifact=engine.artifacts.get(ctx.project,ref)
   if artifact['kind']!='data.dataset':raise ValueError('方法需要数据集')
   ref=artifact['payload']['result_ref']
  r=engine.get(ctx.project,ref)
  comparison=engine.get(ctx.project,args.comparison_result_ref) if args.comparison_result_ref else None
  from .methods import compute_index
  measure_label=None
  if compute_index(args.method_id,args.options)==42:
   from .forecasting import bind_target,forecast_training_rows,published_measure_label
   source=engine.source(ctx.project,r['source_id'])
   r={**r}  # Training selection is request-local. Publish claims against the original receipt.
   args=bind_target(args,ctx.host_evidence,result=r,source=source)
   if not r.get('metric_contract'):
    measure_label=published_measure_label(r,source,(args.columns or ['value'])[0])
   # The claim trains on exactly the requested window; the receipt keeps the
   # full authorized observations (leading prefix declared by available_start).
   training_rows=forecast_training_rows(r,args,ctx.host_evidence,source)
  else:
   training_rows=None
  r.setdefault('metric_contract',{'id':'field_statistics','unit':'原字段单位','version':'1.0.0','periods':[],'scope':{},'limitations':['按当前字段值计算，不进行无依据的单位换算。']})
  claim,notes=calculate(args,{**r,'rows':training_rows} if training_rows is not None else r,comparison)
  if measure_label:
   claim['statistics'].update(measure_label=measure_label,measure_label_basis='published_field')
   if claim['statistics'].get('kind')=='forecast':
    # Carry the same published measure identity into the narrative so the
    # answer cannot present a budget series as actual investment (or vice versa).
    prefix='本次预测按已核实的'+measure_label+'口径。'
    claim['public_text']=prefix+claim['public_text']
    claim['computed_text']=prefix+claim['computed_text']
  claim['method_id']=selected_id;claim['method_version']=selected_version
  if comparison:claim['comparison_artifact_ref']=comparison['artifact_ref']
  return publish(ctx,r,[claim],'method.'+args.method_id,notes)

 def run(args,ctx):
  entry=next((s for s in SKILLS+[x[0] for x in EXTRA_SKILLS.values()] if s['id']==args.skill_id and s['version']==args.version),None)
  if entry is None:raise ValueError('UNKNOWN_SKILL: 未注册的技能或版本')
  if getattr(engine,'policy',None) and not engine.policy.skill_enabled(ctx.project,args.skill_id):raise PermissionError('SKILL_DISABLED: 此业务项目已停用此分析技能')
  r,rows=facts(args,ctx);skill=args.skill_id;vals=[f['value'] for f in rows];claims=[];limitations=[]
  if r['metric_contract'].get('detail'):raise ValueError('DETAIL_ANALYSIS_UNSUPPORTED: 此技能需要分组事实；明细可先用threshold_filter进行服务端阈值汇总。')
  extra=EXTRA_SKILLS.get((skill,args.version))
  if extra is not None:
   claims,limitations=extra[1](r,rows)
  elif skill=='distribution_profile':
   ordered=sorted(rows,key=lambda f:-f['value']);claims=[{'skill_id':skill,'version':args.version,'count':len(vals),'minimum':min(vals),'maximum':max(vals),'mean':sum(vals)/len(vals),'top':ordered[:5]}]
   limitations=['仅描述当前同口径结果的分布；不推断总体原因。']
  elif skill=='economic_efficiency':
   if r['metric_contract']['id']!='project_execution':raise ValueError('资金执行分析需要project_execution合约；金额本身不能证明效能')
   claims=[{'skill_id':skill,'version':args.version,'execution_rates':rows}];limitations=['资金执行率不等于投入产出效益；缺少产出与目标证据，不形成绩效因果结论。']
  else:
   if len(vals)<4:raise ValueError('异常分析至少需要4个可比较观测值')
   import statistics
   q1,_,q3=statistics.quantiles(vals,n=4,method='inclusive');low=q1-1.5*(q3-q1);high=q3+1.5*(q3-q1)
   claims=[{'skill_id':skill,'version':args.version,'method':'IQR','lower':low,'upper':high,'outliers':[f for f in rows if f['value']<low or f['value']>high]}];limitations=['已完成统计异常识别；未提供原因证据，因此未完成因果归因。']
  # Skill summaries are conclusions over the complete input fact set. Preserve
  # these references for auditing even when the skill outputs only a summary.
  for claim in claims:
   claim.setdefault('included_fact_ids',[f['id'] for f in rows])
  return publish(ctx,r,claims,'skill.'+skill,limitations)
 # 0.6.3: after repeated failures to read numbers in one session, say plainly to
 # stop, so the model does not keep rewriting SQL and retrying.
 failures={}
 NUMERIC_FAILURES=('UNKNOWN_VALUE','NON_NUMERIC_COLUMN','NO_ANALYSIS_FACTS')
 def guarded(handler):
  def run_guarded(args,ctx):
   key=(ctx.project,getattr(ctx,'session_id',''))
   try:
    value=handler(args,ctx)
   except ValueError as error:
    if not str(error).startswith(NUMERIC_FAILURES):raise
    failures[key]=failures.get(key,0)+1
    if failures[key]>=NUMERIC_READ_LIMIT:
     raise ValueError('NUMERIC_READ_STOP: 已连续 '+str(failures[key])+' 次读不到可计算的数值（最近一次：'+str(error)[:160]+'）。不要再改写 SQL 或重复统计；直接用已成功的结果卡回答，写明哪些数值无法核算及原因。') from None
    raise
   failures.pop(key,None)
   return value
  return run_guarded
 return Capability('analysis','2.3.0','分析技能',(
  Operation('field_statistics','基础字段统计','对完整授权查询结果的指定字段计算合计、均值、极值、非空数或不同值数，无需业务指标映射。数值缺失不补零；不绕过已有比率和快照口径。',FieldStatistics,guarded(field_statistics),'data.read','analysis.claims',Output),
  Operation('method','执行完整分析方法','对基础查询（SQL）结果执行 analysis_list 列出的方法或项目技能。单份SQL月序列环比和单行百分比阈值差用 analysis_compute。读取保留数据计算，不返回原始明细；缺输入拒绝，预测提供回测和区间，驱动分析不证明因果。',__import__('natural_analysis.methods',fromlist=['Method']).Method,guarded(method),'data.read','analysis.claims',Output,timeout_ms=60000),
  Operation('list','可用分析技能','获取本项目已启用的分析方法、必要条件和与 analysis_compute 的分工。',ListSkills,lambda a,c:model_list(engine,c.project),'data.read','analysis.catalog',Output),
  Operation('compute','确定性计算','SQL结果计算环比用month_over_month（填实际数值列column和月份列time_column，月份须连续），单行百分比阈值差用branch_compare（填实际百分比列column、threshold与comparison，如低于60%填60和<）。SQL占比、排名用share/rank填column。指标查询按完整事实计算：两个fact比较用compare_values（填baseline_fact_id与current_fact_id），单个fact阈值判断用branch_compare+threshold。多月/季度同比用aggregate_compare和baseline_periods/current_periods；单期用period_compare；合计用sum，已有sum可把claim_id交给compare_values。stated_value仅用于核对文本数值（reconcile）。分开查询同口径指标填comparison_result_ref，合计同比阈值填change_threshold。',Compute,guarded(compute),'data.read','analysis.claims',Output),
  Operation('run','执行分析技能','执行具名、版本化分析技能并产生可追溯结论。异常归因缺原因证据时仅完成异常识别。',Skill,run,'data.read','analysis.claims',Output),
 ))
