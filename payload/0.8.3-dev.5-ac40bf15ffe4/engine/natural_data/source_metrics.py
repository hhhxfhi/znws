"""Reviewed metrics for the original district/national source, with explicit gaps."""
METRICS={
 'district_budget_execution':('区级基础测绘预算执行率','natural_resources.budget_execution','100.0*SUM(actual_wan)/NULLIF(SUM(budget_wan),0)','%',"budget_subject='基础测绘经费'",'exec_year'),
 'district_basic_spending':('区级基础测绘实际支出','natural_resources.budget_execution','SUM(actual_wan)','万元',"budget_subject='基础测绘经费'",'exec_year'),
 'result_quality_sample_mean':('成果质检记录合格率算术平均','natural_resources.result_quality_check','AVG(pass_rate)','%','1=1','check_year'),
 'district_service_calls':('区域数据共享服务调用次数','natural_resources.data_sharing_service','SUM(call_count)','次','1=1','stat_month'),
 'gnss_availability_mean':('GNSS站月数据可用率算术平均','natural_resources.gnss_station_monthly','AVG(availability_rate)','%','1=1','stat_month'),
 'national_qualified_units':('全国测绘资质单位总数','national_stats.survey_year_stats','MAX(qual_unit_total)','家','1=1','stat_year'),
 'national_grade_units':('全国分等级测绘资质单位数量','national_stats.survey_qual_grade','SUM(unit_count)','家','1=1','stat_year'),
 'national_ownership_units':('全国分所有制测绘资质单位数量','national_stats.survey_unit_ownership','SUM(unit_count)','家','1=1','stat_year'),
}
DEFINITION={'version':'1.0.0','metrics':METRICS,'grain':{
 'natural_resources.budget_execution':['exec_id'], 'natural_resources.result_quality_check':['check_id'],
 'natural_resources.data_sharing_service':['service_id'],'natural_resources.gnss_station_monthly':['record_no'],
 'national_stats.survey_year_stats':['stat_year'],'national_stats.survey_qual_grade':['stat_year','grade'],
 'national_stats.survey_unit_ownership':['stat_year','ownership']},'partial':True}
LIMITS={
 'result_quality_sample_mean':'成果质检记录比率的算术平均；缺少逐记录合格样本数，不能代替已确认的资质单位监督检查合格率。',
 'gnss_availability_mean':'站月可用率算术平均；缺少应观测小时，不能推断总体加权可用率。',
 'district_basic_spending':'区级基础测绘经费实际支出，不是省级项目全部来源实际投入。',
 'district_budget_execution':'实际支出合计/预算合计；不能比较为验收项目进度。',
 'district_service_calls':'当前区域数据共享服务调用，不自动等同天地图服务次数。'}

def compile_source_report(args,source,mapping):
 from .metric_registry import validate_period
 if args.metric not in mapping['metrics']:raise ValueError('METRIC_GAP: 此来源缺少该指标的粒度或口径字段，不能用其他指标替代。')
 title,table,expr,unit,base,timefield=mapping['metrics'][args.metric];meta=next((t for t in source['tables'] if t['name']==table),None)
 if not meta:raise ValueError('SCHEMA_MISMATCH: 审核指标所需表不在授权目录')
 columns={c['name'] for c in meta['columns']};grain=mapping['grain'][table]
 dims={'none':[],'city':['region'],'grade':['grade'],'ownership':['ownership']}.get(args.group_by)
 if dims is None or not set(dims+grain+[timefield])<=columns:raise ValueError('SCHEMA_MISMATCH: 此指标不支持该分组或缺少粒度字段')
 if args.detail or args.top_n or args.cumulative_percent or args.profession or args.category:raise ValueError('METRIC_GAP: 此来源尚未登记该明细或筛选语义')
 params=[]
 def bind(v):
  name='p'+str(len(params));params.append({'name':name,'type':'string','value':str(v)});return ':'+name
 filter_parts=[]
 if args.city:
  if 'region' not in columns:raise ValueError('METRIC_GAP: 此指标无区域字段')
  if args.city not in source.get('coverage',{}).get('cities',[]):raise ValueError('UNKNOWN: 区域不在审核后的行政字典')
  filter_parts.append('region='+bind(args.city))
 if args.grade:
  if 'grade' not in columns:raise ValueError('METRIC_GAP: 此指标无等级字段')
  filter_parts.append('grade='+bind(args.grade))
 import re
 fields=set(re.findall(r'\b[a-z_][a-z_0-9]*\b',expr))-{'sum','max','nullif','avg'}
 fields={f for f in fields if f.upper() not in {'SUM','MAX','NULLIF','AVG'}}
 fields.update(re.findall(r'\b[a-z_][a-z_0-9]*\b',re.sub(r"'[^']*'",'',base)))
 if not fields<=columns:raise ValueError('SCHEMA_MISMATCH: 指标操作数缺失')
 key=" || '|' || ".join('CAST('+k+' AS TEXT)' for k in grain)
 guard='COUNT(DISTINCT ('+key+'))=COUNT(*)' + ''.join(' AND COUNT('+f+')=COUNT(*)' for f in sorted(fields))
 expr='CASE WHEN '+guard+' THEN '+expr+' ELSE NULL END';parts=[]
 for period in dict.fromkeys(args.periods):
  validate_period(period,source,table)
  if len(period)==7 and timefield.endswith('year'):raise ValueError('METRIC_GAP: 此表只有年度粒度，不能推断月份')
  condition=('CAST('+timefield+' AS TEXT)='+bind(period)) if timefield.endswith('year') else 'substr('+timefield+',1,'+str(len(period))+')='+bind(period)
  aliases=','.join(field+(' AS city' if field=='region' else '') for field in dims)
  sql='SELECT '+bind(period)+' AS period,'+(aliases+',' if aliases else '')+expr+' AS value FROM '+table+' WHERE '+' AND '.join([base,condition,*filter_parts])
  if dims:sql+=' GROUP BY '+','.join(dims)
  sql+=' HAVING COUNT(*)>0';parts.append(sql)
 sql='SELECT * FROM ('+' UNION ALL '.join(parts)+') AS metric_rows'
 if args.below is not None:sql+=' WHERE value < '+bind(args.below)
 sql+=' ORDER BY period'+(','+','.join('city' if d=='region' else d for d in dims) if dims else '')
 snapshot=args.metric.startswith('national_');limits=[LIMITS[args.metric]] if args.metric in LIMITS else []
 contract={'id':args.metric,'version':'1.0.0','unit':unit,'formula':expr,'periods':args.periods,'group_by':args.group_by,'filters':{k:getattr(args,k) for k in ['city','grade'] if getattr(args,k)},'detail':False,'source_mapping':source['metric_mapping'],'mapping_version':mapping['version'],'ratio':unit=='%','aggregation':'snapshot' if snapshot else 'period_flow','additive_over_time':not snapshot and unit!='%','snapshot_rule':'年度已披露值' if snapshot else '期间发生量','limitations':limits,'scope':{'source_id':source['id'],'periods':args.periods,'group_by':args.group_by},'synthetic':bool(source.get('synthetic')),'coverage':source.get('coverage',{})}
 from .definition_evidence import definition_evidence
 contract['name']=title
 contract['confirmation']=definition_evidence(source,contract)
 contract['business_confirmation']=contract['confirmation']['state']
 contract['executed_scope']={'table':table,'base_filter':base,'filters':contract['filters'],'grain':grain}
 return {'catalog_ref':args.catalog_ref,'source_id':args.source_id,'sql':sql,'parameters':params,'title':title,'question':args.question,'chart_type':args.chart_type or ('bar' if dims else 'line' if len(args.periods)>1 else 'kpi'),'metric_contract':contract,'publish_facts':True},contract
