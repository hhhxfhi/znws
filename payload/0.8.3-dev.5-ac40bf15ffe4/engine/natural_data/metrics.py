"""Versioned business metrics; mapping is explicit, not inferred from table names.
Only the synthetic provincial adapter is registered. Existing sources retain free SQL.
"""
from datetime import date
import calendar
import re
from typing import Literal
from pydantic import Field
from .capability import Input, Chart
from .metric_registry import resolve_mapping, validate_period, validate_mapping_fields, SNAPSHOTS

METRICS={
 'certificates':('有效测绘资质证书量','certificate_monthly','COUNT(DISTINCT certificate_id)','个','valid=1','month'),
 'professional_units':('有效专业资质单位数量','profession_monthly','COUNT(DISTINCT unit_id)','个','valid=1','month'),
 'inspection_pass_rate':('资质单位监督检查合格率','unit_inspection','100.0*SUM(passed)/NULLIF(COUNT(passed),0)','%','completed=1','inspection_date'),
 'project_investment':('基础测绘实际投入金额','basic_project_monthly','SUM(investment)','万元',"admin_level='省级' AND project_type='基础测绘'",'month'),
 'fiscal_investment':('财政基础测绘投入金额','basic_project_monthly','SUM(fiscal_investment)','万元',"admin_level='省级' AND project_type='基础测绘'",'month'),
 'confidential_approvals':('涉密测绘成果利用审批办理量','government_cases','COUNT(DISTINCT case_id)','件',"closed_date<>'' AND service_type='涉密测绘成果利用审批'",'closed_date'),
 'project_execution':('基础测绘资金执行率','basic_project_monthly','100.0*SUM(spent)/NULLIF(SUM(budget),0)','%',"admin_level='省级' AND project_type='基础测绘'",'month'),
 'project_acceptance':('已验收项目占比','basic_project_monthly','100.0*SUM(accepted)/NULLIF(COUNT(*),0)','%',"admin_level='省级' AND project_type='基础测绘'",'month'),
 'project_work_completion':('项目平均验收工作量完成率','basic_project_monthly','AVG(100.0*accepted_work/NULLIF(planned_work,0))','%',"admin_level='省级' AND project_type='基础测绘'",'month'),
 'project_work_weighted':('项目整体加权验收工作量完成率','basic_project_monthly','100.0*SUM(accepted_work)/NULLIF(SUM(planned_work),0)','%',"admin_level='省级' AND project_type='基础测绘'",'month'),
 'rejection_count':('不予批准案卷数量及记录原因','government_cases','COUNT(DISTINCT case_id)','件',"closed_date<>'' AND approved=0",'closed_date'),
 'approval_rate':('政务办结批准率','government_cases','100.0*SUM(approved)/NULLIF(COUNT(approved),0)','%',"closed_date<>''",'closed_date'),
 'on_time_rate':('政务按时办结率','government_cases','100.0*SUM(on_time)/NULLIF(COUNT(on_time),0)','%',"closed_date<>''",'closed_date'),
 'map_reviews':('地图审核办理量','map_reviews','COUNT(DISTINCT review_id)','件',"completed_date<>''",'completed_date'),
 'tianditu_services':('天地图服务次数','tianditu_monthly','SUM(service_count)/10000.0','万次',"platform_id='tianditu-guangdong-test'",'month'),
 'tianditu_users':('天地图月末服务用户量','tianditu_monthly','SUM(users)','个',"platform_id='tianditu-guangdong-test'",'month'),
}
from .source_metrics import METRICS as SOURCE_METRICS
METRICS.update(SOURCE_METRICS)

class Report(Input):
 catalog_ref: str
 source_id: str
 metric: str = Field(min_length=1,max_length=100,description='仅选择当前来源目录metric_definitions中的ID；自定义指标使用data_semantic_plan。')
 periods: list[str] = Field(min_length=1,max_length=36,description='明确的年份或月份，如2025或2025-12；不要省略时间。同比传两个年份，月趋势传12个月。快照年度取12月。')
 group_by: Literal['none','city','grade','profession','profession_grade','category','ownership','reason','service_type'] = Field(default='none',description='city所有指标可用；grade只证书/专业；profession和profession_grade只professional_units；category只map_reviews；reason只rejection_count；service_type只政务；ownership只证书/专业。')
 city: str | None = None
 grade: Literal['甲级','乙级'] | None = None
 profession: str | None = None
 category: Literal['省级','部委托'] | None = None
 detail: bool = Field(default=False,description='仅需名单/案卷/项目明细时true；平均完成率先detail=false，低于阈值名单另取detail=true，不额外取全部或改阈值。')
 below: float | None = Field(default=None,description='对指标值过滤严格小于；例低于60%')
 top_n: int | None = Field(default=None,ge=1,le=100)
 cumulative_percent: float | None = Field(default=None,gt=0,le=100,description='按值降序取累计份额达到阈值的最小前缀，边界并列全部纳入')
 question: str = Field(min_length=1,max_length=10000)
 chart_type: Chart | None = Field(default=None,description='柱状图/柱形图使用column（竖向）；条形图使用bar（横向）；指标卡kpi，仪表盘gauge。按用户明确要求选择，不能混用名称。')


def compile_report(args, source):
 mapping_id, mapping = resolve_mapping(source)
 if args.metric not in METRICS:raise ValueError('METRIC_GAP: 当前来源没有此兼容指标，请查看目录中的结构化指标或使用基础查询。')
 if mapping.get("metrics"):
  from .source_metrics import compile_source_report
  return compile_source_report(args,source,mapping)
 if args.metric in SOURCE_METRICS:raise ValueError("METRIC_GAP: 该指标未映射到此来源")
 title,table,expr,unit,base,timefield=METRICS[args.metric]
 parameters=[];parts=[]
 def bind(value):
  name='p'+str(len(parameters));parameters.append({'name':name,'type':'number' if isinstance(value,(int,float)) else 'string','value':value});return ':'+name
 dims={'none':[],'profession_grade':['profession','grade']}.get(args.group_by,[args.group_by])
 columns=validate_mapping_fields(source,table,[expr,base,timefield])
 # Required operand gaps must not silently disappear through SUM/AVG/COUNT.
 operands={
  'inspection_pass_rate':['passed'],'project_investment':['investment'],
  'fiscal_investment':['fiscal_investment'],'project_execution':['spent','budget'],
  'project_acceptance':['accepted'],'project_work_completion':['accepted_work','planned_work'],'project_work_weighted':['accepted_work','planned_work'],
  'approval_rate':['approved'],'on_time_rate':['on_time'],
  'tianditu_services':['service_count'],'tianditu_users':['users']}.get(args.metric,[])
 grain=mapping['grain'][table]
 if not set(grain)<=columns:raise ValueError('SCHEMA_MISMATCH: 缺少已登记粒度键。')
 key=" || '|' || ".join(grain)
 guard=' AND '.join(['COUNT(DISTINCT ('+key+'))=COUNT(*)',*('COUNT('+field+')=COUNT(*)' for field in operands)])
 if args.metric=='project_work_completion':guard+=' AND MIN(planned_work)>0'
 if guard:expr='CASE WHEN '+guard+' THEN '+expr+' ELSE NULL END'
 if any(d not in columns for d in dims):raise ValueError('该指标不支持所选分组')
 filters=[]
 coverage=source.get('coverage',{})
 if args.city and coverage.get('cities') and args.city not in coverage['cities']:raise ValueError('UNKNOWN: 地市不在已登记行政范围，不能当作零；全省汇总请不填city')
 if args.profession and coverage.get('professions') and args.profession not in coverage['professions']:raise ValueError('UNKNOWN: 专业名称不在授权字典，请核对')
 for key in ['city','grade','profession','category']:
  value=getattr(args,key)
  if value is not None:
   if key not in columns:raise ValueError('该指标不支持筛选 '+key)
   filters.append(key+'='+bind(value))
 for period in dict.fromkeys(args.periods):
  validate_period(period,source,table)
  annual=len(period)==4
  snapshot=table in SNAPSHOTS or args.metric=='tianditu_users'
  key=period+'-12' if annual and snapshot else period
  if timefield=='month' and (snapshot or not annual):condition=timefield+'='+bind(key)
  else:condition='substr('+timefield+',1,'+str(len(key))+')='+bind(key)
  where=' AND '.join([base,condition,*filters])
  label=bind(period)+' AS period'
  if args.detail:
   if args.metric in ['certificates','professional_units']:
    fields=['unit_id','unit_name','city','grade']+(['profession'] if args.metric=='professional_units' else ['certificate_id'])
    query='SELECT DISTINCT '+label+','+','.join(fields)+' FROM '+table+' WHERE '+where
   elif table=='basic_project_monthly':
    value={'project_work_completion':'100.0*accepted_work/NULLIF(planned_work,0)','project_investment':'investment','fiscal_investment':'fiscal_investment','project_execution':'100.0*spent/NULLIF(budget,0)','project_acceptance':'100.0*accepted','project_work_weighted':'100.0*accepted_work/NULLIF(planned_work,0)'}[args.metric]
    query='SELECT '+label+',project_id,project_name,city,'+value+' AS value FROM '+table+' WHERE '+where
   elif table=='government_cases':
    query='SELECT '+label+',case_id,city,result,reason,closed_date FROM '+table+' WHERE '+where+" AND approved=0"
   else:raise ValueError('该指标没有登记明细输出')
  else:
   query='SELECT '+label+','+('' if not dims else ','.join(dims)+',')+expr+' AS value FROM '+table+' WHERE '+where
   if dims:query+=' GROUP BY '+','.join(dims)
   query+=' HAVING COUNT(*) > 0'
  parts.append(query)
 sql='SELECT * FROM ('+' UNION ALL '.join(parts)+') AS metric_rows'
 if args.below is not None:
  if args.detail and table!='basic_project_monthly':raise ValueError('此名单不支持数值阈值')
  sql+=' WHERE value < '+bind(args.below)
 if args.cumulative_percent is not None:
  if args.detail or len(args.periods)!=1 or not dims or unit=='%':raise ValueError('累计份额需要单期、可加数量的分组结果')
  pct=bind(args.cumulative_percent)
  sql='WITH ranked AS (SELECT *,SUM(value) OVER (ORDER BY value DESC ROWS UNBOUNDED PRECEDING) AS cumulative,SUM(value) OVER () AS total FROM ('+sql+') AS base) SELECT * FROM ranked WHERE value >= (SELECT value FROM ranked WHERE cumulative >= total*'+pct+'/100.0 ORDER BY cumulative LIMIT 1) ORDER BY value DESC'
 elif args.top_n:
  if args.detail:raise ValueError('明细名单不可静默截断为Top，请先汇总排名')
  if unit=='%' or len(args.periods)!=1:raise ValueError('排名须单期；比率排名请先完整查询再analysis_compute rank')
  sql='SELECT *, SUM(value) OVER () AS total FROM ('+sql+') AS ranked_values ORDER BY value DESC,'+(','.join(dims) if dims else 'period')+' LIMIT '+str(args.top_n)
 else:sql+=' ORDER BY period'+(','+','.join(dims) if dims else '')
 contract={'id':args.metric,'version':'2.0.0','name':title,'definition_status':'兼容映射已登记；业务定义待确认'+('；合成测试数据' if source.get('synthetic') else ''),'unit':unit,'formula':expr,'periods':args.periods,'snapshot_rule':'年末12月快照' if snapshot else '期间发生量','filters':{k:getattr(args,k) for k in ['city','grade','profession','category'] if getattr(args,k) is not None},'group_by':args.group_by,'detail':args.detail,'top_n':args.top_n,'cumulative_percent':args.cumulative_percent,'synthetic':bool(source.get('synthetic')), 'source_mapping':mapping_id, 'mapping_version':mapping['version'], 'aggregation':'snapshot' if snapshot else 'period_flow', 'additive_over_time':not snapshot and unit!='%', 'ratio':unit=='%', 'mean_kind':'arithmetic_project_mean' if args.metric=='project_work_completion' else 'weighted_by_planned_work' if args.metric=='project_work_weighted' else None, 'scope':{'source_id':source['id'],'periods':args.periods,'group_by':args.group_by}, 'limitations':['项目完成率为项目算术平均，不证明多数项目或总体加权工作量进度。'] if args.metric=='project_work_completion' else [], 'coverage':source.get('coverage',{})}
 from .definition_evidence import definition_evidence
 contract['confirmation']=definition_evidence(source,contract)
 contract['business_confirmation']=contract['confirmation']['state']
 contract['executed_scope']={'table':table,'base_filter':base,'filters':contract['filters'],'grain':mapping['grain'][table]}
 contract['overlapping_dimensions']=['profession'] if args.metric=='professional_units' else []
 return {'catalog_ref':args.catalog_ref,'source_id':args.source_id,'sql':sql,'parameters':parameters,'title':title+('（合成测试）' if source.get('synthetic') else ''),'question':args.question,'chart_type':args.chart_type or ('table' if args.detail else 'grouped-bar' if dims and len(args.periods)>1 else 'line' if len(args.periods)>1 else 'bar' if dims else 'kpi'),'metric_contract':contract,'publish_facts':not args.detail},contract
