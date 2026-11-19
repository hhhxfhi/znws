"""Executable non-spatial matrix. All values are computed from retained datasets."""
from __future__ import annotations
import json
import math
import re
import statistics as stats
from collections import defaultdict
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from .matrix import CAPABILITIES,chart_family

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Constraint(Strict):
    coefficients:list[float]=Field(min_length=1,max_length=100)
    relation:Literal['lte','eq']='lte'
    bound:float
class Options(Strict):
    threshold:float|None=None
    benchmark:float|None=None
    target:float|None=None
    top_n:int=Field(default=5,ge=1,le=100)
    window:int=Field(default=3,ge=2,le=60)
    seasonal_period:int=Field(default=12,ge=2,le=52)
    clusters:int=Field(default=3,ge=2,le=10)
    forecast_steps:int=Field(default=3,ge=1,le=24)
    forecast_model:Literal['auto','naive','arima','ets','linear','snaive','smean','theta','holt']=Field(default='auto',description='默认依据同窗口回测自动选择；无需业务用户选择技术算法。用户明确指定方法时再设定。')
    forecast_until:str|None=Field(default=None,description='预测目标期，与时间粒度一致。月度YYYY-MM，年度YYYY且只支持历史末年后的下一年。按真实历史末期计算步数。')
    forecast_grain:Literal['auto','year','quarter','month','week','day']='auto'
    arima_order:list[int]=Field(default_factory=lambda:[1,1,0],min_length=3,max_length=3)
    weights:list[float]=Field(default_factory=list,max_length=20)
    scenario_multiplier:float=1.0
    scenario_offset:float=0.0
    constraints:list[Constraint]=Field(default_factory=list,max_length=100)
    bounds:list[list[float]]=Field(default_factory=list,max_length=100)
    maximize:bool=False
    chart_style:Literal['donut','stacked','percent_stacked']|None=Field(default=None,description='构成的图表样式：环形、堆叠或百分比堆叠；堆叠需要 dimensions')
class Method(Strict):
    result_ref:str
    method_id:str=Field(description='analysis_list 列出的方法 id（matrix.编号）或项目技能 custom.标识')
    method_version:str='1.0.0'
    columns:list[str]=Field(default_factory=list,max_length=20,description='数值字段；回归首列为因变量，相关需两列，效率需产出和投入两列')
    dimensions:list[str]=Field(default_factory=list,max_length=6)
    time_column:str|None=None
    comparison_result_ref:str|None=None
    options:Options=Field(default_factory=Options)

# 0.7.0: the method list the model and the 配置管理 page see. Chinese names only; merged
# methods keep their old ids as aliases so saved skills and earlier results still work.
NAMES={0:'现状与总览（合计、计数、均值、极值）',3:'下钻（按维度分解）',4:'趋势（走向、峰值、谷值）',5:'同比/环比变化',
 6:'波动（方差、标准差、变异系数）',7:'周期（季节指数）',8:'拐点（走势转折）',9:'分组对比（差值、比值）',10:'排名（前几名、后几名）',
 11:'基准与差距（与基准值比较）',13:'构成（占比；图表可选环形、堆叠、百分比堆叠）',16:'分布（直方图、分位数、中位数）',
 17:'集中度（前3/前5占比、集中指数）',18:'二八分析（累计占比）',19:'分群（规则、聚类）',20:'交叉分析（两维透视）',
 21:'变化贡献（各部分对变化的贡献）',22:'驱动因素（回归）',23:'层级诊断（逐级分解）',24:'异常识别（四分位距、标准分）',
 26:'风险评分（加权）',27:'阈值与达标（超过阈值、持续期数、达标率）',29:'目标完成（完成率、差距）',30:'绩效评分（加权指数）',
 31:'效率（产出/投入）',32:'时效（耗时分位、时限内占比）',33:'相关性',34:'关联规则（共现）',35:'漏斗（阶段转化）',
 36:'流程（阶段耗时）',37:'瓶颈（阶段耗时）',38:'队列留存',42:'预测（时间序列）',43:'情景模拟（假设计算）',44:'优化（线性规划）'}
ALIASES={'matrix.1':'matrix.0','matrix.2':'matrix.0','matrix.12':'matrix.11','matrix.14':'matrix.13','matrix.15':'matrix.13','matrix.25':'matrix.24','matrix.28':'matrix.27'}
STYLE_INDEX={'donut':13,'stacked':14,'percent_stacked':15}
# Rarely needed in 测绘 work: off by default; an administrator can turn each on per project.
DEFAULT_OFF={6,8,17,18,19,20,22,23,26,30,31,33,34,35,36,37,38,43,44}
DEFAULT_REQUIRES='完整、可比较的已授权结果；不从截断样本推断总体'
REQUIRES={
 4:'一个等间隔时间序列，至少3个观测',5:'同口径、同分组的两个完整结果（comparison_result_ref）',6:'至少3个同口径观测',7:'至少两个完整周期的等间隔序列',8:'至少5个等间隔观测',
 11:'需要基准值 options.benchmark',19:'不同数值至少 options.clusters 个',20:'两个分组维度',21:'同口径、同分组的两个期间结果（comparison_result_ref）',
 22:'因变量及至少一个数值特征；样本数大于特征数的两倍',23:'明确的层级维度，提供最细一级数据',26:'指标、权重 options.weights 和阈值 options.threshold',
 27:'需要阈值 options.threshold',29:'需要目标值 options.target',30:'数值指标和同样多的非负权重 options.weights',31:'同单位的产出、投入两列',
 32:'非负耗时字段和时限 options.threshold',33:'两个数值字段、至少3对完整观测',34:'两个分类字段、至少3条记录',35:'dimensions 给唯一阶段列，columns 给阶段计数',
 36:'一个阶段维度及非负耗时',37:'一个阶段维度及非负耗时',38:'队列、年龄及存留数三列；每个队列要有年龄0的基期',
 42:'年度需至少5个连续可比完整年度，仅预测下一年；月度需至少12个连续观测，按原问核对更长窗口。明确目标期，默认auto回测选模。存量需连续同口径快照；查询失败不能当作缺数据。季节朴素自17个月（季度自12点）、历史同期均值自27个月（季度自12点）、Theta自6个年度入围回测；Holt 非季节趋势自12点入围，服务短序列与周/日；不足时披露排除原因',43:'用户给出的变化倍数和增量，只做模拟',44:'数值列为目标系数，需要约束和上下界；不修改外部系统'}

# Names a model may use instead of the id ("share", "构成", "trend" …).
NAME_ALIASES={**{k:'matrix.0' for k in ('summary','overview','aggregate','现状','总览')},**{k:'matrix.3' for k in ('drilldown','drill_down','下钻')},
 **{k:'matrix.4' for k in ('trend','time_series','趋势')},**{k:'matrix.5' for k in ('yoy','mom','period_compare','change','同比','环比','变化')},
 **{k:'matrix.7' for k in ('seasonal','seasonality','周期')},**{k:'matrix.9' for k in ('compare','comparison','对比')},
 **{k:'matrix.10' for k in ('rank','ranking','topn','排名')},**{k:'matrix.11' for k in ('benchmark','gap','基准','差距')},
 **{k:'matrix.13' for k in ('share','composition','proportion','part_to_whole','构成','占比')},**{k:'matrix.16' for k in ('distribution','histogram','分布')},
 **{k:'matrix.21' for k in ('contribution','贡献')},**{k:'matrix.24' for k in ('anomaly','outlier','异常')},
 **{k:'matrix.27' for k in ('threshold','compliance','阈值','达标')},**{k:'matrix.29' for k in ('target','目标')},
 **{k:'matrix.32' for k in ('duration','sla','时效')},**{k:'matrix.42' for k in ('forecast','prediction','预测','时间序列预测','时序预测','time_series_forecast')}}

def public_id(method_id):
    key=str(method_id or '').strip()
    resolved=ALIASES.get(key) or NAME_ALIASES.get(key.lower())
    if resolved:return resolved
    # Models often prefix a name with the registry namespace ("matrix.预测").
    # Resolve the name part deterministically; numeric ids stay untouched.
    low=key.lower()
    if low.startswith('matrix.') and not low[7:].isdigit():
        return NAME_ALIASES.get(low[7:]) or key
    return key

def compute_index(method_id,options=None):
    """Index used for the calculation: aliases keep their original behaviour; 构成 takes its chart style."""
    if not method_id.startswith('matrix.'):method_id=public_id(method_id)
    i=int(method_id.split('.')[1])
    if public_id(method_id)=='matrix.13' and getattr(options,'chart_style',None):
        return STYLE_INDEX[options.chart_style]
    return i

def definitions():
    """Public methods only (merged ids appear once, under their kept id)."""
    out=[]
    for i,c in enumerate(CAPABILITIES):
        if i not in NAMES:continue
        out.append({'id':'matrix.'+str(i),'version':'1.0.0','title':NAMES[i],'category':c.category.removesuffix('类'),
                    'requires':REQUIRES.get(i,DEFAULT_REQUIRES),'default_enabled':i not in DEFAULT_OFF,'required_metrics':[],
                    'chart_type':chart_family(c),'framework':list(c.framework)})
    return out

def known_method(method_id):
    return any(x['id']==public_id(method_id) for x in definitions())

def validate_custom(config):
    if set(config)-{'kind','version','method_id','columns','dimensions','time_column','options','framework'}:raise ValueError('CUSTOM_SKILL_SCHEMA: 未支持的技能字段')
    if config.get('kind')!='analysis-skill' or not __import__('re').fullmatch(r'\d+\.\d+\.\d+',config.get('version','')):raise ValueError('CUSTOM_SKILL_SCHEMA: 需要分析技能类型及语义版本')
    if not known_method(config.get('method_id','')):raise ValueError('CUSTOM_SKILL_METHOD: 只能复用已安装只读算子')
    Options.model_validate(config.get('options',{}))
    for k in ('columns','dimensions','framework'):
        if not isinstance(config.get(k,[]),list) or len(config.get(k,[]))>20 or any(not isinstance(x,str) or len(x)>300 for x in config.get(k,[])):raise ValueError('CUSTOM_SKILL_SCHEMA: '+k+'无效')
    return config

def custom_definitions(engine,project):
    result=[];seen=set()
    for s in engine.sources(project):
        for row in s.get('business_skills',[]):
            config=row.get('config',{})
            if config.get('kind')!='analysis-skill' or row['id'] in seen:continue
            validate_custom(config);seen.add(row['id'])
            base=next(x for x in definitions() if x['id']==public_id(config['method_id']))
            result.append({**base,'id':'custom.'+row['id'],'version':config['version'],'title':row['name'],'category':'项目技能','based_on':base['title'],'default_enabled':True,'config':config})
    return result

def all_custom_definitions(engine):
    business=getattr(engine,'business_projects',None)
    if not business:return []
    out=[]
    for project in business.config()['projects'].values():
        version=next((v for v in project['versions'] if v['version']==project['published_version']),None)
        if not version:continue
        for row in version['config']['skills']:
            spec=row.get('config',{})
            if spec.get('kind')!='analysis-skill':continue
            validate_custom(spec);base=next(x for x in definitions() if x['id']==public_id(spec['method_id']))
            out.append({**base,'id':'custom.'+row['id'],'version':spec['version'],'title':row['name'],'category':'项目技能','based_on':base['title'],'default_enabled':True,'business_project':project['id']})
    return out

def finite(value):return type(value) in (int,float) and math.isfinite(value)
def require(ok,message):
    if not ok:raise ValueError('ANALYSIS_PREREQUISITE: '+message)
def numeric(rows,column):
    require(rows and all(finite(r.get(column)) for r in rows),'数值字段包含缺失或非有限值：'+column)
    return [float(r[column]) for r in rows]
def quantile(values,p):
    v=sorted(values);x=(len(v)-1)*p;a=int(x);return v[a]+(v[min(a+1,len(v)-1)]-v[a])*(x-a)
def groups(rows,dimensions):
    result=defaultdict(list)
    for row in rows:result[tuple(row.get(d) for d in dimensions)].append(row)
    return result

def series(rows,time_column,column):
    import datetime as dt
    require(time_column is not None,'必须指定时间字段')
    pairs=sorted((str(r.get(time_column)),r[column]) for r in rows)
    require(len({x[0] for x in pairs})==len(pairs),'一个时间点对应多个分组，先筛选或分别分析')
    def index(s):
        if __import__('re').fullmatch(r'\d{4}-\d{2}',s):return int(s[:4])*12+int(s[5:])
        if __import__('re').fullmatch(r'\d{4}',s):return int(s)
        try:return dt.date.fromisoformat(s[:10]).toordinal()
        except ValueError:raise ValueError('ANALYSIS_TIME: 无法识别时间粒度') from None
    indexes=[index(x[0]) for x in pairs];gaps=[b-a for a,b in zip(indexes,indexes[1:])]
    require(gaps and len(set(gaps))==1 and gaps[0]>0,'时间间隔必须一致，不补造缺期值')
    return [x[0] for x in pairs],[x[1] for x in pairs]

BASIS_CN={'random_walk_normal_approximation':'随机游走残差正态近似（不含参数不确定性）',
 'linear_student_t_prediction':'Student-t 预测区间（含参数与杠杆不确定性）',
 'seasonal_naive_normal_approximation':'季节差分残差正态近似',
 'seasonal_mean_student_t_prediction':'同季节均值 Student-t 预测区间',
 'holt_bootstrap_prediction':'残差 bootstrap 重拟合+模拟池（含参数与创新不确定性）',
 'ets_bootstrap_prediction':'残差 bootstrap 重拟合+模拟池（含参数与创新不确定性）',
 'theta_model_prediction':'Theta 模型预测区间',
 'arima_model_prediction':'ARIMA 模型置信区间'}

def selection_note(out):
    """Disclose why auto selection had fewer competitors; never guess reasons."""
    if out.get('requested_model')!='auto':return ''
    excluded=(out.get('selection') or {}).get('excluded') or []
    if not excluded:return ''
    models={'naive':'末值基线','linear':'线性趋势','ets':'季节平滑','arima':'自回归时间序列',
            'snaive':'季节朴素','smean':'历史同期均值','theta':'Theta 趋势','holt':'Holt 趋势'}
    reasons={'insufficient_history_for_same_model_backtest':'同模型回测历史不足','fit_failed':'拟合失败',
             'dependency_unavailable':'运行依赖缺失','infeasible_count_interval':'计数区间不可行'}
    groups={}
    for item in excluded:
        name=models.get(item.get('model'));reason=reasons.get(item.get('reason'))
        if not name or not reason:return ''
        groups.setdefault(reason,[]).append(name)
    if not groups or out.get('model') not in models:return ''
    return ('自动选择本次采用'+models[out['model']]+'；'
            +'；'.join('、'.join(names)+'因'+reason+'排除' for reason,names in groups.items())+'。')

def calculate(args,result,comparison=None):
    i=compute_index(args.method_id,args.options);require(i not in (39,40,41) and 0<=i<len(CAPABILITIES),'本期未启用空间方法')
    rows=result['rows'];require(result['status']=='succeeded' and not result.get('truncated') and rows,'需要完整成功且非空结果')
    cols=args.columns or (['value'] if 'value' in result['columns'] else [])
    require(cols and all(c in result['columns'] for c in cols),'请在 columns 里填结果中的数值字段；这个结果的字段有：'+'、'.join(map(str,result['columns'][:20])))
    require(all(d in result['columns'] for d in args.dimensions),'分组维度不在结果中')
    o=args.options;vals=numeric(rows,cols[0]);out={};notes=['仅描述当前完整结果，不证明因果或总体覆盖。']
    additive=result.get('metric_contract',{}).get('additive_over_time',result.get('metric_contract',{}).get('additive',False))
    if i in (13,14,15,17,18,21):
        require(result.get('metric_contract',{}).get('ratio') is not True,'比率不能直接求构成占比')
        require(all(v>=0 for v in vals),'构成/贡献需要非负基期量')
    if i in (0,1,2,3):
        out={'count':len(vals),'minimum':min(vals),'maximum':max(vals),'mean':stats.mean(vals),'median':stats.median(vals)}
        if additive:out['sum']=sum(vals)
        if i==1 or (i==0 and len(cols)>1):out['metrics']={c:{'min':min(v:=numeric(rows,c)),'max':max(v),'mean':stats.mean(v)} for c in cols}
        if i==2:out['row_count']=len(rows);notes.append('明细只在回答卡展示，模型不接收业务明细。')
        if i==3:
            require(args.dimensions,'下钻需要维度');out['groups']=[{'dimensions':list(k),'count':len(rs),'mean':stats.mean(numeric(rs,cols[0])),**({'sum':sum(numeric(rs,cols[0]))} if additive else {})} for k,rs in groups(rows,args.dimensions).items()]
    elif i==42:
        from .forecasting import display_values, forecast, measure_kind
        require(len(cols)==1,'一次预测只使用一个数值字段；多个指标请分别计算。')
        # Same-request calendar evidence: the receipt's checked client-calendar
        # window names the open year that may sit between history and target.
        window=(result.get('validation') or {}).get('annual_fiscal_window') or {}
        open_year=None
        clock=str(window.get('current_open_year') or '')
        if window.get('state')=='checked' and window.get('basis')=='client_calendar' and len(clock)==4 and clock.isdigit():
            open_year=int(clock)
        training,reuse=result.get('_forecast_training',(None,None))
        # Resolve the display kind before fitting so auto selection can skip
        # candidates whose count interval is not deliverable (same proof as
        # measure_kind below; invalid values still fail in calendar_series).
        # With a checked training series, judge the kind on the values actually
        # trained (the reused tail), not on the longer receipt rows.
        raw_values=[value for _,value in training[1]] if training is not None else [row.get(cols[0]) for row in rows]
        pre_kind='numeric'
        if all(type(v) in (int,float) and math.isfinite(v) for v in raw_values):
            pre_kind=measure_kind(result,cols[0],[float(v) for v in raw_values])[1]
        out=forecast(rows,args.time_column,cols[0],o,series=training,open_year=open_year,value_kind=pre_kind)
        if reuse:
            out['training_window']=reuse
        unit,kind=measure_kind(result,cols[0],out['history']['values'])
        out.update(source_result_ref=result.get('result_ref',args.result_ref),time_column=args.time_column,
                   value_column=cols[0],unit=unit,value_kind=kind,display=display_values(out,kind))
        notes.extend(['预测区间受模型假设约束，不是保证区间；回测表现不能保证未来准确。',out['sample_limits']['note'],
                      '自动选择只比较本次可用模型，回测和最终拟合采用相同设定。'])
        if kind=='nonnegative_discrete_count':
            notes.append('计数显示值取约数，预测区间向外取整并限制为非负；原拟合值及回测保留。')
    elif i in (4,6,7,8):
        labels,v=series(rows,args.time_column,cols[0])
        n=len(v);require(n>=3,'至少3个观测')
        slope=sum((x-(n-1)/2)*(y-stats.mean(v)) for x,y in enumerate(v))/sum((x-(n-1)/2)**2 for x in range(n))
        if i==4:out={'slope_per_period':slope,'peak':{'time':labels[v.index(max(v))],'value':max(v)},'trough':{'time':labels[v.index(min(v))],'value':min(v)}}
        elif i==6:
            sd=stats.pstdev(v);out={'variance':stats.pvariance(v),'stddev':sd,'cv':sd/abs(stats.mean(v)) if stats.mean(v) else None,'rolling':[{'time':labels[j],'stddev':stats.pstdev(v[j-o.window+1:j+1])} for j in range(o.window-1,n)]}
        elif i==7:
            require(n>=2*o.seasonal_period and n%o.seasonal_period==0,'周期分析需要至少两个完整周期')
            mean=stats.mean(v);require(mean!=0,'均值为零无法计算周期指数');out={'seasonal_period':o.seasonal_period,'indexes':[stats.mean(v[j::o.seasonal_period])/mean for j in range(o.seasonal_period)]}
        elif i==8:
            require(n>=5,'拐点需要至少5点');d=[b-a for a,b in zip(v,v[1:])];out={'turning_points':[{'time':labels[j],'value':v[j],'slope_before':d[j-1],'slope_after':d[j]} for j in range(1,n-1) if d[j-1]*d[j]<0],'largest_slope_change':max(({'time':labels[j],'change':d[j]-d[j-1]} for j in range(1,len(d))),key=lambda x:abs(x['change']))}
    elif i in (5,21):
        require(comparison is not None and comparison['status']=='succeeded' and not comparison.get('truncated'),'需要完整对照结果')
        contract=result.get('metric_contract',{});other=comparison.get('metric_contract',{})
        require(contract.get('id')==other.get('id') and contract.get('version')==other.get('version') and contract.get('unit')==other.get('unit') and result['source_id']==comparison['source_id'],'对照指标、版本、单位及来源必须一致')
        ds=args.dimensions
        old=groups(comparison['rows'],ds);new=groups(rows,ds)
        require(set(old)==set(new) and all(len(x)==1 for x in list(old.values())+list(new.values())),'对照分组必须一一匹配；缺组不补零')
        delta=[{'dimensions':list(k),'baseline':(b:=numeric(old[k],cols[0])[0]),'current':(c:=numeric(new[k],cols[0])[0]),'difference':c-b,'change_percent':100*(c-b)/abs(b) if b else None} for k in new]
        total=sum(x['difference'] for x in delta)
        if i==21:
            require(additive,'变化贡献必须是可加总指标');delta=[{**x,'contribution_percent':100*x['difference']/total if total else None} for x in delta]
        out={'comparisons':delta,'total_difference':total if additive else None}
    elif i in (9,10,11,12):
        ordered=sorted(range(len(vals)),key=lambda j:(-vals[j],j));rank={j:1+sum(v>vals[j] for v in vals) for j in ordered}
        out={'ranking':[{'row':j,'value':vals[j],'rank':rank[j]} for j in ordered[:o.top_n]],'gap':max(vals)-min(vals)}
        if i in (11,12):
            require(o.benchmark is not None,'需指定benchmark');out={'benchmark':o.benchmark,'differences':[v-o.benchmark for v in vals],'relative_percent':[100*(v-o.benchmark)/abs(o.benchmark) if o.benchmark else None for v in vals],'index':[100*v/o.benchmark if o.benchmark else None for v in vals]}
    elif i in (13,14,15,17,18):
        total=sum(vals);require(total>0,'总量必须大于零');s=sorted(vals,reverse=True);shares=[v/total for v in s]
        out={'total':total,'shares_percent':[100*v/total for v in vals],'cr3':100*sum(shares[:3]),'cr5':100*sum(shares[:5]),'hhi':sum(x*x for x in shares),'cumulative_percent':[100*sum(shares[:j+1]) for j in range(len(s))]}
        if i in (14,15):
            require(args.dimensions,'堆叠构成需分组维度');out['group_shares']=[{'dimensions':list(k),'total':sum(v:=numeric(rs,cols[0])),'percent':[100*x/sum(v) if sum(v) else None for x in v]} for k,rs in groups(rows,args.dimensions).items()]
    elif i==16:
        import numpy as np
        counts,edges=np.histogram(vals,bins='auto');out={'median':stats.median(vals),'q1':quantile(vals,.25),'q3':quantile(vals,.75),'histogram':{'counts':counts.tolist(),'edges':edges.tolist()}}
    elif i==19:
        import numpy as np
        from scipy.cluster.vq import kmeans2
        require(len(set(vals))>=o.clusters,'不同观测数不足以分群');matrix=np.array([numeric(rows,c) for c in cols]).T;scale=matrix.std(axis=0);require(all(scale>0),'常数特征不能分群')
        normalized=(matrix-matrix.mean(axis=0))/scale;centers,labels=kmeans2(normalized,o.clusters,minit='++',seed=42,iter=100)
        out={'algorithm':'标准化K均值聚类','clusters':o.clusters,'memberships':labels.tolist(),'centroids_original':(centers*scale+matrix.mean(axis=0)).tolist()};notes.append('聚类标签为统计分群，不代表业务类别或因果。')
    elif i in (20,23):
        require(len(args.dimensions)>=2 if i==20 else bool(args.dimensions),'需指定交叉/层级维度')
        out={'cells':[{'dimensions':list(k),'count':len(rs),'value':sum(numeric(rs,cols[0])) if additive else stats.mean(numeric(rs,cols[0]))} for k,rs in groups(rows,args.dimensions).items()],'aggregation':'sum' if additive else 'mean'}
        if i==23:notes.append('层级分解定位观测差异，未提供业务原因证据，不能声称已识别根因。')
    elif i==22:
        import numpy as np
        import statsmodels.api as sm
        require(len(cols)>=2 and len(rows)>2*len(cols),'OLS需充足样本和至少一个特征')
        x=sm.add_constant(np.array([numeric(rows,c) for c in cols[1:]]).T);require(np.linalg.matrix_rank(x)==x.shape[1],'回归特征共线')
        fit=sm.OLS(vals,x).fit();out={'model':'最小二乘回归','coefficients':dict(zip(['intercept',*cols[1:]],map(float,fit.params))),'pvalues':dict(zip(['intercept',*cols[1:]],map(float,fit.pvalues))),'r_squared':float(fit.rsquared),'sample_count':len(vals)};notes.append('系数是控制所列特征后的统计关联，不是因果驱动证明；未进行干预识别。')
    elif i in (24,25):
        require(len(vals)>=4,'异常检测至少4点');q1=quantile(vals,.25);q3=quantile(vals,.75);sd=stats.pstdev(vals);mean=stats.mean(vals)
        out={'method':'四分位距与标准分','lower':q1-1.5*(q3-q1),'upper':q3+1.5*(q3-q1),'outliers':[{'row':j,'value':v,'zscore':(v-mean)/sd if sd else 0} for j,v in enumerate(vals) if v<q1-1.5*(q3-q1) or v>q3+1.5*(q3-q1) or (sd and abs(v-mean)/sd>3)]}
    elif i in (26,30):
        require(len(o.weights)==len(cols) and all(w>=0 for w in o.weights) and sum(o.weights)>0,'需要同长度非负权重，权重和大于零')
        scores=[sum(w*row[c] for c,w in zip(cols,o.weights))/sum(o.weights) for row in rows];out={'weighted_scores':scores,'weights':o.weights}
        if i==26:require(o.threshold is not None,'风险评分需要阈值');out['risk_rows']=[j for j,s in enumerate(scores) if s>=o.threshold]
        notes.append('权重为所提供的规则假设，指标应先统一量纲；评分不等于已验证的风险概率。')
    elif i in (27,28):
        require(o.threshold is not None,'需指定threshold');matched=[j for j,v in enumerate(vals) if v>=o.threshold];out={'comparison':'>=','threshold':o.threshold,'matched_rows':matched,'matched_count':len(matched),'count':len(vals),'pass_percent':100*len(matched)/len(vals)}
        if args.time_column:
            labels,v=series(rows,args.time_column,cols[0]);runs=[];start=None
            for j,hit in enumerate([x>=o.threshold for x in v]+[False]):
                if hit and start is None:start=j
                if not hit and start is not None:runs.append({'start':labels[start],'end':labels[j-1],'periods':j-start});start=None
            out['duration']=runs
    elif i==29:
        require(o.target is not None and o.target>0,'目标必须大于零');out={'target':o.target,'completion_percent':[100*v/o.target for v in vals],'gap':[o.target-v for v in vals]}
    elif i==31:
        require(len(cols)==2,'效率需产出与投入字段');inputs=numeric(rows,cols[1]);require(all(v>=0 for v in inputs),'投入不能为负');out={'output_column':cols[0],'input_column':cols[1],'efficiency':[a/b if b else None for a,b in zip(vals,inputs)],'overall':sum(vals)/sum(inputs) if sum(inputs) else None};notes.append('投入产出单位比不是利润、因果效益或绩效等级。')
    elif i==32:
        require(min(vals)>=0 and o.threshold is not None,'耗时需非负且指定SLA');out={'mean':stats.mean(vals),'median':stats.median(vals),'p90':quantile(vals,.9),'p95':quantile(vals,.95),'sla':o.threshold,'within_sla_percent':100*sum(v<=o.threshold for v in vals)/len(vals)}
    elif i==33:
        from scipy.stats import pearsonr,spearmanr
        require(len(cols)==2 and len(vals)>=3,'相关需两列至少3对观测');b=numeric(rows,cols[1]);require(len(set(vals))>1 and len(set(b))>1,'常数列相关未定义');p=pearsonr(vals,b);s=spearmanr(vals,b);out={'pearson':float(p.statistic),'pearson_p':float(p.pvalue),'spearman':float(s.statistic),'spearman_p':float(s.pvalue)}
    elif i==34:
        require(len(args.dimensions)==2,'关联需两个分类字段');a,b=args.dimensions;total=len(rows);pairs=groups(rows,[a,b]);ca=groups(rows,[a]);cb=groups(rows,[b]);out={'rules':[{'pair':list(k),'support':len(rs)/total,'confidence':len(rs)/len(ca[(k[0],)]),'lift':len(rs)*total/(len(ca[(k[0],)])*len(cb[(k[1],)]))} for k,rs in pairs.items()]}
    elif i==35:
        require(len(args.dimensions)==1 and len({r[args.dimensions[0]] for r in rows})==len(rows) and min(vals)>=0,'漏斗需要唯一、有序阶段的非负计数');out={'stages':[{'stage':r[args.dimensions[0]],'count':vals[j],'conversion_percent':100*vals[j]/vals[j-1] if j and vals[j-1] else None} for j,r in enumerate(rows)],'overall_percent':100*vals[-1]/vals[0] if vals[0] else None};notes.append('使用数据返回的阶段顺序；阶段必须同队列，否则不能解释为真实转化率。')
    elif i in (36,37):
        require(len(args.dimensions)==1 and min(vals)>=0,'流程需要阶段和非负耗时');stages=[{'stage':k[0],'count':len(rs),'mean_duration':stats.mean(numeric(rs,cols[0])),'p95_duration':quantile(numeric(rs,cols[0]),.95)} for k,rs in groups(rows,args.dimensions).items()];out={'stages':stages,'bottleneck':max(stages,key=lambda s:s['mean_duration'])};notes.append('以当前阶段耗时定位瓶颈；没有事件链证据时不能推断完整流转路径。')
    elif i==38:
        require(len(args.dimensions)==2 and min(vals)>=0,'队列需队列ID、年龄和存留数');cohort,age=args.dimensions;cells=[]
        for key,rs in groups(rows,[cohort]).items():
            base=[r for r in rs if r[age] in (0,'0')];require(len(base)==1 and base[0][cols[0]]>0,'每队列需唯一、非零年龄0基期')
            require(len({r[age] for r in rs})==len(rs),'队列年龄重复');cells.extend({'cohort':key[0],'age':r[age],'retained':r[cols[0]],'retention_percent':100*r[cols[0]]/base[0][cols[0]]} for r in rs)
        out={'cohort_matrix':cells};notes.append('保留率要求同队列同实体口径，超过100%需核查重新激活或计数定义。')
    elif i==43:out={'scenario_multiplier':o.scenario_multiplier,'scenario_offset':o.scenario_offset,'simulated':[v*o.scenario_multiplier+o.scenario_offset for v in vals],'difference':[v*(o.scenario_multiplier-1)+o.scenario_offset for v in vals]};notes.append('这些是用户情景假设的算术结果，不是预测或已发生事实。')
    elif i==44:
        from scipy.optimize import linprog
        require(o.constraints and len(vals)<=100,'优化需要明确约束，变量最多100')
        require(all(len(c.coefficients)==len(vals) for c in o.constraints),'约束维度不匹配')
        if o.bounds:require(len(o.bounds)==len(vals) and all(len(b)==2 and b[0]<=b[1] for b in o.bounds),'变量边界无效')
        ub=[c for c in o.constraints if c.relation=='lte'];eq=[c for c in o.constraints if c.relation=='eq']
        fit=linprog([-v if o.maximize else v for v in vals],A_ub=[c.coefficients for c in ub] or None,b_ub=[c.bound for c in ub] or None,A_eq=[c.coefficients for c in eq] or None,b_eq=[c.bound for c in eq] or None,bounds=o.bounds or (0,None),method='highs',options={'time_limit':10})
        require(fit.success,'约束无可行最优解或求解未完成；状态 '+str(fit.status));out={'solver':'线性规划求解器','solution':fit.x.tolist(),'objective':float(-fit.fun if o.maximize else fit.fun),'constraints':len(o.constraints),'status':'optimal'};notes.append('最优解仅相对所给线性假设；不会执行采购、配置或其他外部修改。')
    else:raise ValueError('未知矩阵方法')
    # Strict JSON numeric proof, rejecting NaNs from estimators before publication.
    json.dumps(out,allow_nan=False)
    c=CAPABILITIES[i];name=NAMES.get(int(public_id('matrix.'+str(i)).split('.')[1]),c.intent)
    sample_count=out['history_count'] if out.get('kind')=='forecast' else len(rows)
    text=f'已执行{name}；有效观测{sample_count}条。'
    public_text=f'已完成{name.split("（")[0]}，使用{sample_count}条有效数据。'
    if out.get('kind')=='forecast':
        number=lambda value:f'{value:,.2f}'.rstrip('0').rstrip('.')
        period_text=lambda period:period+'年' if out['grain']=='year' else period
        unit=out['unit'] or ''
        approximately='约' if out['value_kind']=='nonnegative_discrete_count' else ''
        forecasts=[period_text(period)+'预测值'+approximately+number(value)+unit+'，95%预测区间'+number(lo)+'至'+number(hi)+unit
                   for period,value,(lo,hi) in zip(out['forecast_periods'],out['display']['predicted'],out['display']['interval_95'])]
        backtest=out['backtest']
        error=lambda value:'接近0' if 0 < value < 0.01 else number(value)
        public_text+='实际训练期间为'+period_text(out['history_start'])+'至'+period_text(out['origin'])+'。采用'+{'naive':'末值基线','linear':'线性趋势','ets':'季节平滑','arima':'自回归时间序列','snaive':'季节朴素','smean':'历史同期均值','theta':'Theta 趋势','holt':'Holt 趋势'}[out['model']]+'：'+'；'.join(forecasts)+'。'
        if reuse := out.get('training_window'):
            public_text+=f'从该次查询取得的 {reuse["available_periods"]} 期记录中使用最近 {sample_count} 期。'
        public_text+='历史回测：平均绝对误差'+error(backtest['mae'])+'，均方根误差'+error(backtest['rmse'])+'。预测不能保证未来结果。'
        if out['steps']>1:
            horizon=out.get('horizon_backtest')
            public_text+=(f'多步回测（{out["steps"]}步）平均绝对误差{number(horizon["mae"])}。' if horizon else
                          f'历史不足以做同模型{out["steps"]}步回测，选模依据单步回测，区间受模型假设约束。')
        if out.get('publication_level')=='reference_only':
            public_text+='回测仅1个留出点，本结果为试算参考，不作为可引用结论。'
        if out['value_kind']=='numeric' and re.fullmatch(r'(?:人民币)?(?:万|亿)?元', out['unit'] or '') \
                and any(lo<0 for lo,_ in out['display']['interval_95']):
            public_text+='预测区间包含负值，反映模型估算范围。'
        public_text+='区间依据：'+BASIS_CN[out['interval_basis']]+'。'
        if out.get('open_year_skipped'):
            public_text+=out['open_year_skipped']+'年为当前未结年，其数值为跨期推算，不是已发生观测；目标期为'+period_text(out['target_period'])+'。'
        public_text+=selection_note(out)
        text=public_text
    else:
        text+='计算结果：'+json.dumps(out,ensure_ascii=False,allow_nan=False)+'。'
    return {'public_text':public_text,'operation':'matrix_method','method_id':args.method_id,'method':name,'framework':list(c.framework),'chart_type':chart_family(c),'statistics':out,'sample_count':sample_count,'computed_text':text,'included_fact_ids':[f['id'] for f in result.get('facts',[])]},notes
