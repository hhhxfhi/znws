"""Forecast only a complete calendar series, with reproducible model selection.

No business names, metric identifiers or observation values select a model.
All candidates use the same withheld origins and the same model specification
in backtesting and publication. Missing periods are never filled with zero.
"""
import calendar
import datetime as dt
import math
import re
import unicodedata
import warnings
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MIN_ANNUAL_HISTORY = 5
_EXPLICIT_ACTUAL_AMOUNT = re.compile(r'实际(?:的)?(?:投入|投资|拨付|支出|出资)(?:总?金额|额|资金)?')


def require(condition,message):
    if not condition:raise ValueError('FORECAST_PREREQUISITE: '+message)


def calendar_series(rows,time_column,value_column,requested_grain='auto'):
    require(time_column,'请指定历史序列的时间字段。')
    require(rows,'没有可用的历史序列。')
    labels=[str(row.get(time_column,'')) for row in rows]
    grain=None
    if all(re.fullmatch(r'\d{4}-\d{2}',s) for s in labels):grain='month'
    elif all(re.fullmatch(r'\d{4}',s) for s in labels):grain='year'
    elif all(re.fullmatch(r'\d{4}-Q[1-4]',s) for s in labels):grain='quarter'
    elif all(re.fullmatch(r'\d{4}-\d{2}-\d{2}',s) for s in labels):
        try:dates=sorted(dt.date.fromisoformat(s) for s in labels)
        except ValueError:raise ValueError('FORECAST_PREREQUISITE: 日期无效。') from None
        if requested_grain=='month' or (requested_grain=='auto' and len({d.year*12+d.month for d in dates})==len(dates)
          and (all(d.day==1 for d in dates) or all(d.day==calendar.monthrange(d.year,d.month)[1] for d in dates))):grain='month'
        else:
            gaps={ (b-a).days for a,b in zip(dates,dates[1:]) }
            grain='week' if gaps=={7} else 'day'
    require(grain is not None,'时间粒度无法识别；请按年、季度、月、周或日聚合完整历史序列。')
    require(requested_grain in ('auto',grain),'请求粒度与实际序列不符，不能用年度合计预测某个月。')

    def index(label):
        year=int(label[:4])
        require(1<=year<=9999,'年份无效。')
        if grain=='year':return year
        if grain=='quarter':return year*4+int(label[-1])-1
        if grain=='month':
            month=int(label[5:7])
            require(1<=month<=12,'月份必须在1至12之间。')
            return year*12+month-1
        return dt.date.fromisoformat(label).toordinal()
    def label(index):
        if grain=='month':return f'{index//12:04}-{index%12+1:02}'
        if grain=='quarter':return f'{index//4:04}-Q{index%4+1}'
        if grain=='year':return f'{index:04}'
        return dt.date.fromordinal(index).isoformat()
    pairs=sorted((index(s),row.get(value_column)) for s,row in zip(labels,rows))
    require(len({i for i,_ in pairs})==len(pairs),'同一期间存在多个值；先按业务分组分别预测。')
    invalid=[label(i) for i,v in pairs if not(type(v) in (int,float) and math.isfinite(v))]
    require(not invalid,'历史数值缺失或无效（'+'、'.join(invalid[:12])+('等' if len(invalid)>12 else '')
            +'），不能补零或跳过；缺期已核实时如实报告具体期间。')
    step=7 if grain=='week' else 1
    for (left,_),(right,_) in zip(pairs,pairs[1:]):
        if right-left!=step:
            missing='、'.join(label(i) for i in range(left+step,min(right,left+4*step),step))
            require(False,'历史序列缺期或间隔不一致'+('，缺期包括 '+missing if missing else '')+
                    '。补查原范围；缺期已核实时如实报告，不补零。改用更短的训练窗口需用户明确确认。')
    return grain,pairs,step,index,label


def forecast(rows,time_column,value_column,options,*,series=None,open_year=None,value_kind='numeric'):
    import numpy as np
    grain,pairs,step,index,label=series or calendar_series(rows,time_column,value_column,options.forecast_grain)
    values=[float(v) for _,v in pairs];n=len(values)
    minimum_history=MIN_ANNUAL_HISTORY if grain=='year' else 12
    require(n>=minimum_history,'年度预测至少需要5个连续、同口径的完整年度。' if grain=='year' else
            '预测至少12点连续观测；两年同月值或单年合计不足以形成月度预测。')
    steps=1 if grain=='year' and 'forecast_steps' not in options.model_fields_set else options.forecast_steps
    if options.forecast_until:
        until=options.forecast_until
        shapes={'year':r'\d{4}','quarter':r'\d{4}-Q[1-4]','month':r'\d{4}-\d{2}',
                'week':r'\d{4}-\d{2}-\d{2}','day':r'\d{4}-\d{2}-\d{2}'}
        require(re.fullmatch(shapes[grain],until),'目标期间的格式必须与序列粒度一致。')
        distance=index(until)-pairs[-1][0]
        require(distance>0 and distance%step==0,'目标期间必须在历史末期之后且落在同一时间网格上。')
        steps=distance//step
        require('forecast_steps' not in options.model_fields_set or steps==options.forecast_steps,'目标期间与预测步数冲突。')
    # The client calendar may leave one open year between the last closed
    # observation and the requested target (e.g. history ends 2025, today is in
    # 2026, the user asks for 2027). Allow exactly that two-step annual horizon;
    # the intermediate open year is published as a projection, never an actual.
    open_skip=grain=='year' and steps==2 and open_year is not None and pairs[-1][0]+1==open_year
    require(grain!='year' or steps==1 or open_skip,'年度预测只支持历史末年后的下一年；请补齐相邻完整历史年度。')
    require(1<=steps<=24,'预测目标超出支持的24期范围，不能把近期预测标为远期目标。')
    period=options.seasonal_period if 'seasonal_period' in options.model_fields_set else {'month':12,'quarter':4}.get(grain)
    holdout=min(3,n//5);train_size=n-holdout
    minimum={'naive':2,'linear':8,'ets':2*period if period else math.inf,'arima':30,
             'snaive':period+2 if period else math.inf,
             'smean':2*period if period else math.inf,
             'holt':8,'theta':2*period if period else 5}
    requested=options.forecast_model
    names=['naive','snaive','smean','linear','holt','theta','ets'] if requested=='auto' else [requested]
    require(requested=='auto' or train_size>=minimum[requested],'指定模型的历史训练期不足以同时完成同模型回测；补齐历史或使用自动选择。')

    def fit(model,training,count,*,with_interval=True):
        data=np.asarray(training,dtype=float);length=len(data)
        def bootstrap_interval(fitted,spec):
            # Innovation uncertainty plus parameter uncertainty: residual
            # bootstrap refits pooled with the base simulation. Fixed seeds
            # keep publication reproducible; clamp to contain the point path.
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            base=np.asarray(fitted.simulate(count,repetitions=200,error='add',random_state=42))
            fittedvalues=np.asarray(fitted.fittedvalues,dtype=float)
            resid=data-fittedvalues
            rng=np.random.default_rng(42)
            paths=[base]
            for b in range(39):
                yb=fittedvalues+rng.choice(resid,size=length,replace=True)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        refit=ExponentialSmoothing(yb,**spec).fit()
                    if refit.mle_retvals is not None and not refit.mle_retvals.get('success',True):continue
                    paths.append(np.asarray(refit.simulate(count,repetitions=10,error='add',random_state=100+b)))
                except (ValueError,ArithmeticError,np.linalg.LinAlgError):
                    continue
            pool=np.concatenate(paths,axis=1)
            lo,hi=np.percentile(pool,[2.5,97.5],axis=1)
            # Bootstrap pools can skew to one side on short series; take the
            # farther side symmetrically so the published interval never
            # understates the uncertainty the bootstrap actually reached.
            half=np.maximum(predicted-lo,hi-predicted)
            return np.column_stack((predicted-half,predicted+half))
        if model=='linear':
            x=np.arange(length);coefficient=np.polyfit(x,data,1)
            predicted=np.polyval(coefficient,np.arange(length,length+count))
            residual=data-np.polyval(coefficient,x)
            from scipy.stats import t
            margin=t.ppf(.975,length-2)*np.std(residual,ddof=2)*np.sqrt(1+1/length+(np.arange(length,length+count)-(length-1)/2)**2/sum((x-(length-1)/2)**2))
            interval=np.column_stack((predicted-margin,predicted+margin));basis='linear_student_t_prediction'
        elif model=='naive':
            predicted=np.repeat(data[-1],count)
            margin=1.96*np.std(np.diff(data),ddof=1)*np.sqrt(np.arange(1,count+1))
            interval=np.column_stack((predicted-margin,predicted+margin));basis='random_walk_normal_approximation'
        elif model=='snaive':
            require(length>period,'季节朴素需要至少一个完整季节周期。')
            predicted=np.array([data[length-period+(h-1)%period] for h in range(1,count+1)])
            diffs=data[period:]-data[:-period]
            margin=1.96*np.std(diffs,ddof=1)*np.sqrt(np.ceil(np.arange(1,count+1)/period))
            interval=np.column_stack((predicted-margin,predicted+margin));basis='seasonal_naive_normal_approximation'
        elif model=='smean':
            from scipy.stats import t
            means=[];margins=[]
            for h in range(1,count+1):
                prior=data[(length+h-1)%period::period]
                require(len(prior)>=2,'历史同期均值需要至少两个同季节观测。')
                means.append(float(np.mean(prior)))
                margins.append(float(t.ppf(.975,len(prior)-1)*np.std(prior,ddof=1)*math.sqrt(1+1/len(prior))))
            predicted=np.array(means);margin=np.array(margins)
            interval=np.column_stack((predicted-margin,predicted+margin));basis='seasonal_mean_student_t_prediction'
        elif model=='holt':
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            spec={'trend':'add','seasonal':None,'initialization_method':'estimated'}
            fitted=ExponentialSmoothing(data,**spec).fit()
            require(fitted.mle_retvals is None or fitted.mle_retvals.get('success',True),'Holt 模型未收敛。')
            predicted=fitted.forecast(count)
            if with_interval is True:
                interval=bootstrap_interval(fitted,spec)
            elif with_interval=='innovation':
                sims=np.asarray(fitted.simulate(count,repetitions=200,error='add',random_state=42))
                lo,hi=np.percentile(sims,[2.5,97.5],axis=1)
                interval=np.column_stack((np.minimum(lo,predicted),np.maximum(hi,predicted)))
            else:
                interval=None
            basis='holt_bootstrap_prediction'
        elif model=='theta':
            from statsmodels.tools.sm_exceptions import ConvergenceWarning
            from statsmodels.tsa.forecasting.theta import ThetaModel
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    warnings.simplefilter('error',ConvergenceWarning)
                    fitted=ThetaModel(data,period=period or 1).fit(disp=False)
            except ConvergenceWarning:
                raise ValueError('Theta 模型未收敛。') from None
            predicted=np.asarray(fitted.forecast(count),dtype=float)
            interval=np.asarray(fitted.prediction_intervals(count,alpha=.05),dtype=float)
            basis='theta_model_prediction'
        elif model=='ets':
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            spec={'trend':'add','seasonal':'add','seasonal_periods':period,'initialization_method':'estimated'}
            fitted=ExponentialSmoothing(data,**spec).fit()
            require(fitted.mle_retvals is None or fitted.mle_retvals.get('success',True),'季节模型未收敛。')
            predicted=fitted.forecast(count)
            if with_interval is True:
                interval=bootstrap_interval(fitted,spec)
            elif with_interval=='innovation':
                sims=np.asarray(fitted.simulate(count,repetitions=200,error='add',random_state=42))
                lo,hi=np.percentile(sims,[2.5,97.5],axis=1)
                interval=np.column_stack((np.minimum(lo,predicted),np.maximum(hi,predicted)))
            else:
                interval=None
            basis='ets_bootstrap_prediction'
        else:
            require(all(0<=x<=3 for x in options.arima_order),'自回归模型阶数须在0至3之间。')
            from statsmodels.tsa.arima.model import ARIMA
            fitted=ARIMA(data,order=tuple(options.arima_order)).fit()
            require(fitted.mle_retvals.get('converged',False),'自回归模型未收敛。')
            estimate=fitted.get_forecast(count);predicted=estimate.predicted_mean;interval=estimate.conf_int(alpha=.05);basis='arima_model_prediction'
        require(np.isfinite(predicted).all(),'模型产生非有限预测。')
        if with_interval:
            require(np.isfinite(interval).all(),'模型产生非有限区间。')
            return list(map(float,predicted)),np.asarray(interval).tolist(),basis
        return list(map(float,predicted)),None,basis

    scores=[];eligible={};excluded=[]
    for model in names:
        if train_size<minimum[model]:
            excluded.append({'model':model,'reason':'insufficient_history_for_same_model_backtest'});continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',RuntimeWarning)
                errors=[];covers=[]
                for j in range(n-holdout,n):
                    point,band,_=fit(model,values[:j],1,with_interval='innovation')
                    errors.append(point[0]-values[j])
                    eps=1e-9*max(1.0,abs(values[j]))
                    covers.append(band[0][0]-eps<=values[j]<=band[0][1]+eps)
                output=fit(model,values,steps)
            # A count forecast whose interval lies wholly below zero is not
            # deliverable; in auto mode try the next candidate instead of
            # failing the whole request after selection. Explicit models keep
            # the honest refusal in display_values.
            if (requested=='auto' and value_kind=='nonnegative_discrete_count'
                    and not all(hi>=0 for _,hi in output[1])):
                excluded.append({'model':model,'reason':'infeasible_count_interval'});continue
            score={'model':model,'mae':sum(abs(e) for e in errors)/holdout,'rmse':math.sqrt(sum(e*e for e in errors)/holdout),
                   'coverage':sum(covers)/holdout}
            require(all(math.isfinite(score[k]) for k in ('mae','rmse')),'回测数值无效。')
            if steps>1 and n-holdout-steps+1>=minimum[model]:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',RuntimeWarning)
                    origins=list(range(n-holdout-steps+1,n-steps+1))
                    herr=[];hcovers=[]
                    for j in origins:
                        point,band,_=fit(model,values[:j],steps,with_interval='innovation')
                        herr.append(point[-1]-values[j+steps-1])
                        eps=1e-9*max(1.0,abs(values[j+steps-1]))
                        hcovers.append(band[-1][0]-eps<=values[j+steps-1]<=band[-1][1]+eps)
                hmae=sum(abs(e) for e in herr)/len(herr)
                hrms=math.sqrt(sum(e*e for e in herr)/len(herr))
                require(math.isfinite(hmae) and math.isfinite(hrms),'多步回测数值无效。')
                score['horizon']={'horizon_steps':steps,'rolling_origins':len(herr),'mae':hmae,'rmse':hrms,
                                  'coverage':sum(hcovers)/len(hcovers),
                                  'periods':[label(pairs[j+steps-1][0]) for j in origins]}
            scores.append(score);eligible[model]=output
        except (ValueError,ArithmeticError,np.linalg.LinAlgError,ImportError) as error:
            if requested!='auto':raise
            excluded.append({'model':model,'reason':'dependency_unavailable' if isinstance(error,ImportError) else 'fit_failed'})
    require(scores,'没有完成有效回测的模型，不能发布预测。')
    # When every candidate can be backtested at the delivered horizon with a
    # reliable training floor (two seasonal periods), select on that horizon
    # MAE; a 1-step tie is broken by horizon MAE under the same floor. Tiny
    # samples keep the 1-step criterion: horizon backtests there overfit.
    effective=n-holdout-steps+1
    floor=2*period if period else 8
    horizon_reliable=steps>1 and effective>=floor
    horizon_primary=horizon_reliable and all('horizon' in s for s in scores)
    def primary(s):return s['horizon']['mae'] if horizon_primary else s['mae']
    def cover(s):return s['horizon']['coverage'] if horizon_primary else s['coverage']
    best=min(primary(s) for s in scores)
    # Near-ties (5% relative) are broken by empirical backtest interval
    # coverage: a model whose intervals miss the withheld truth is not a
    # better forecast just because its point MAE is a hair lower.
    tied=[s for s in scores if primary(s)<=best+max(1e-9,0.05*best)]
    if len(tied)>1:
        cbest=max(cover(s) for s in tied)
        tied=[s for s in tied if cover(s)>=cbest-1e-9]
    chosen=tied[0]
    predicted,interval,basis=eligible[chosen['model']]
    periods=[label(pairs[-1][0]+step*j) for j in range(1,steps+1)]
    horizon=chosen.get('horizon')
    note=('年度样本仅五期，回测只有一个留出点，区间参考性有限。' if grain=='year' and n==5 else
          '回测留出点有限，预测区间受模型假设约束。')
    if steps>1:
        note+=(f'多步回测（{steps}步）平均绝对误差{horizon["mae"]:.2f}。' if horizon else
               f'历史不足以做同模型{steps}步回测，选模依据单步回测，区间受模型假设约束。')
    if open_skip:
        note+=f'{open_year:04}年为当前未结年，目标期距历史末年两步，区间按两步推算放大。'
    return {'schema_version':1,'kind':'forecast','model':chosen['model'],'requested_model':requested,'grain':grain,
            'origin':label(pairs[-1][0]),'history_start':label(pairs[0][0]),'history_count':n,'steps':steps,
            'publication_level':'reference_only' if holdout==1 else 'formal',
            **({'open_year_skipped':f'{open_year:04}'} if open_skip else {}),
            'history':{'periods':[label(i) for i,_ in pairs],'values':values},
            'sample_limits':{'minimum_history':minimum_history,'holdout_points':holdout,'note':note},
            'target_period':periods[-1],'forecast_periods':periods,'predicted':predicted,'interval_95':interval,
            'interval_basis':basis,'seasonal_period':period if chosen['model']=='ets' else None,
            'backtest':{**chosen,'rolling_origins':holdout,'training_minimum':train_size,
                        'periods':[label(i) for i,_ in pairs[-holdout:]],'same_model_specification':True},
            'horizon_backtest':horizon,
            'selection':{'criterion':('rolling_origin_horizon_mae' if horizon_primary else 'rolling_origin_mae')
                         if requested=='auto' else 'explicit_model',
                         'candidates':scores,'excluded':excluded}}


_PREDICT = re.compile(r'预测|预估|预计|展望')
# Negation may span a short phrase before the verb ("不打算做任何预测"), but
# never crosses a comma or sentence boundary into another clause.
_NEGATED = re.compile(r'(?:不做|无需|不要|不必|不作|不需|取消|不|无意)[^，,。；;\n]{0,12}$')
_ACTUAL_DATA = re.compile(r'基于|根据|采用|使用|查询|查看|(?:^|[，,])\s*(?:查|取|用|统计)|历史|实际|对照|截至|截止')
_YEAR_WORD = r'本年度|本年|今年|明年|下年度|下一年|下年|未来一年'
_TARGET = re.compile(
    r'(?<!\d)(?:(?P<month_year>\d{4}|' + _YEAR_WORD + r')\s*(?:年\s*|[-/]\s*)?(?P<month>\d{1,2})\s*月(?:份)?'
    r'|(?P<iso_year>\d{4})-(?P<iso_month>\d{2})(?![-\d])'
    r'|(?P<annual_year>\d{4})\s*年(?:度)?(?!\s*(?:\d|[一二三四五六七八九十]+月|年|Q[1-4]))'
    r'|(?P<relative>' + _YEAR_WORD + r'))')
_RELATIVE_TREND = re.compile(
    r'^\s*(?:请(?:问|帮我)?\s*)?(?P<horizon>(?:未来|今后|接下来(?:的)?)\s*'
    r'(?:[一1]\s*年|(?:12|十二)\s*个?月))\s*[,，:：]?\s*(?P<subject>[^。！？?!；;\n]{2,100}?)(?:吗|呢)?[。！？?!]?\s*$'
)
_TREND_WORD = re.compile(r'趋势|走势|走向|增长|下滑|上升|下降|增加|减少|持平')
_MONTHLY_FORECAST_FORMAT = re.compile(
    r'[。！？?!；;]\s*(?:请(?:帮我)?\s*)?(?:按月|逐月|每月|分月)(?:给出|提供)?'
    r'(?:预测|预估)(?:结果|值)?[。！？?!]?\s*$')


def _now(host,now=None):
    try:zone=ZoneInfo((host or {}).get('client_time_zone') or 'UTC')
    except (ValueError,ZoneInfoNotFoundError):zone=dt.timezone.utc
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(zone)


def _intent(clause):
    return [m for m in _PREDICT.finditer(clause) if not _NEGATED.search(clause[:m.start()])]


def relative_forecast_horizon(host, now=None):
    """Return the twelve forecast months after the latest calendar close."""
    text = (host or {}).get('user_text', '')
    match = _RELATIVE_TREND.fullmatch(_MONTHLY_FORECAST_FORMAT.sub('', text))
    if not match:
        return None
    subject = match['subject']
    if (not _TREND_WORD.search(subject) or _TARGET.search(subject)
            or re.search(r'不做预测|不要预测|无需预测|只看历史|另外|顺便|以及|同比|环比', subject)):
        return None
    current = _now(host, now)
    end_year, end_month = divmod(current.year * 12 + current.month + 10, 12)
    if end_year > 9999:
        return None
    return {'start': f'{current:%Y-%m}', 'end': f'{end_year:04}-{end_month + 1:02}',
            'span': match.span('horizon')}


def target_mentions(host,now=None):
    """Find forecast dates without using source query titles or options."""
    text=(host or {}).get('user_text','')
    candidates=[]
    for sentence in re.finditer(r'[^。；;\n！？!?]+',text):
        clause=sentence[0]
        verbs=[(m,bool(_NEGATED.search(clause[:m.start()]))) for m in _PREDICT.finditer(clause)]
        intents=[m for m,negated in verbs if not negated]
        matches=list(_TARGET.finditer(clause))
        for index,match in enumerate(matches):
            # The nearest preceding verb governs the target; a negated verb
            # blocks its own targets instead of letting an earlier verb adopt them.
            previous_verb=next(((m,negated) for m,negated in reversed(verbs) if m.end()<=match.start()),None)
            earlier=previous_verb[0] if previous_verb and not previous_verb[1] else None
            later=next((m for m in intents if m.start()>=match.end()),None)
            # A negated verb right after the target ("不做2028年预测") governs it;
            # an earlier verb must not adopt a cancelled target.
            next_verb=next(((m,negated) for m,negated in verbs if m.start()>=match.end()),None)
            if next_verb and next_verb[1] and re.fullmatch(r'[^，,]{0,12}',clause[match.end():next_verb[0].start()]):
                continue
            previous=clause[:match.start()]
            following=clause[match.end():]
            if re.match(r'\s*(?:\d{1,2}\s*[日号]|-\d{2})',following):
                continue  # Day cutoffs belong to acquisition unless a day-target contract exists.
            # Dates after a forecast verb are targets unless a new actual-data
            # clause introduces them. A comma is allowed in coordinated targets.
            prior_date=matches[index-1] if index else None
            actual_comparison=(earlier and prior_date and prior_date.start()>=earlier.end()
                               and re.match(r'\s*(?:的\s*)?(?:实际|历史|上期)',following))
            after_intent=earlier and not actual_comparison and not _ACTUAL_DATA.search(clause[earlier.end():match.start()])
            # Also accept “2027 年的预算预测” and “2027 年预计…”, but do
            # not reinterpret the last year of a preceding acquisition range.
            next_date=next((m for m in matches[index+1:] if later and m.start()>=later.end()),None)
            before_intent=later and (
                (not next_date or re.fullmatch(r'\s*(?:和|与|及|以及|或|或者|、|[，,])\s*',clause[later.end():next_date.start()]))
                and re.fullmatch(r'[^，,]{0,18}',clause[match.end():later.start()])
                and not re.search(r'(?:历史|记录|数据|实际值|同比|环比|趋势)',clause[match.end():later.start()])
                and not re.match(r'\s*(?:至|到|[-—–~～])\s*\d{4}',following)
                and not re.search(r'(?:基于|根据|按|用|从|查询|查看|统计|至|到|截至|截止)[^，,]{0,8}$',previous))
            if not (after_intent or before_intent):
                continue
            year=match['month_year'] or match['iso_year'] or match['annual_year'] or match['relative']
            if not year.isdigit():
                history_end=re.search(r'(?:截至|截止到?|至|到)\s*(\d{4})\s*年[^。；;]{0,22}$',text[:sentence.start()+match.start()])
                year=(int(history_end[1]) if year=='下一年' and history_end else _now(host,now).year)+(
                    year not in ('本年度','本年','今年'))
            year=int(year)
            require(1<=year<=9999,'用户指定的目标年份无效。')
            month=match['month'] or match['iso_month']
            if month:
                month=int(month)
                require(1<=month<=12,'用户指定的目标月份无效。')
            value=f'{year:04}-{month:02}' if month else f'{year:04}'
            candidates.append((value,(sentence.start()+match.start(),sentence.start()+match.end())))
    if not candidates and (window := relative_forecast_horizon(host, now)):
        candidates.append((window['end'], window['span']))
    return candidates


def requested_target(host,now=None):
    candidates={value for value,_ in target_mentions(host,now)}
    return next(iter(candidates)) if len(candidates)==1 else None


def historical_scope_text(host,mentions=None):
    text=(host or {}).get('user_text','')
    if mentions is None:
        mentions=target_mentions(host)
    for _,(start,end) in reversed(mentions):
        text=text[:start]+' '+text[end:]
    return text


_RECENT = re.compile(r'(?:最近|过去|近)\s*(\d{1,2}|[一二两三四五六七八九十])\s*(年|个月|月)')
_SMALL_NUMBERS = dict(zip('一二两三四五六七八九十',(1,2,2,3,4,5,6,7,8,9,10)))
_FULL_HISTORY = re.compile(r'(?:完整|全部|全量|所有|整段)\s*的?\s*(?:(?:已记录|可用)的?\s*)?'
                           r'(?:历史|历年|以往|往年|月度|年度|季度|逐月|逐年|训练|观测|实际记录|数据(?!源)|记录)')
_HISTORY_TOPIC = re.compile(r'历史|训练|观测|回测')
_HISTORY_RANGE = re.compile(
    r'(\d{4})(?:\s*[年/-]\s*(\d{1,2})(?!\d)\s*月?)?\s*年?\s*(?:至|到|[-—–~～])\s*'
    r'(\d{4})(?:\s*[年/-]\s*(\d{1,2})(?!\d)\s*月?)?\s*年?')
_HISTORY_CUTOFF = re.compile(r'(?:截至|截止到?)\s*(\d{4})\s*年(?:\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?)?')
_CLOSED_YEARS = re.compile(r'已确认完整年度[：:]\s*(\d{4}(?:\s*[、,，]\s*\d{4}){0,299})[；;]\s*依据[：:]\s*\S[^\n]*')
_CLOSED_MONTHS = re.compile(r'已确认完整月份[：:]\s*(\d{4}-\d{2}(?:\s*[、,，]\s*\d{4}-\d{2}){0,299})[；;]\s*依据[：:]\s*\S[^\n]*')
_SNAPSHOT = re.compile(r'存量|在册|余额|持证|保有量|有效[^，。；\n]{0,16}资质|'
                       r'资质[^，。；\n]{0,24}(?:单位|证书)(?:数量|数|家数)|'
                       r'(?:期末|月末|月底|年末|年底)[^，。；\n]{0,14}(?:库存|资产|负债|人数|数量)|snapshot', re.I)

_ANNUAL_TRAINING = re.compile(
    r'^(?:请\s*)?(?:仅|只)?(?:用|使用|采用|基于|根据|依据)\s*'
    r'(\d{4})\s*(?:年(?:度)?)?\s*(?:至|到|[-—–~])\s*(\d{4})\s*年(?:度)?'
)
_ANNUAL_COUNT = re.compile(r'^(?:这)?([一二两三四五六七八九十]|\d{1,2})\s*个?\s*(?:已结|已关账|完整)?年度的?\s*')
_ANNUAL_CLOSE_CHECK = re.compile(
    r'^(?:请\s*)?(?:核对|检查|确认)(?:每个|各个?|所有)?(?:训练|历史)?年度是否(?:已)?(?:结年|关账)$'
)
_ANNUAL_NUMERIC_TYPE = re.compile(r'^(?:smallint|integer|bigint|int|real|float|double|numeric|decimal|number)\b', re.I)
_ANNUAL_REQUEST_CONNECTOR = re.compile(r'[，,\s]*(?:能否|能不能|可否|是否可以|是否能|可以|来|据此|以此)?\s*$')
_INVESTMENT_MEASURE = r'(?:实际(?:财政)?|财政(?:实际)?)?(?:投资|投入)(?:金额|额)?'
_ANNUAL_INVESTMENT_TREND = re.compile(
    r'^(?:请(?:帮我)?|帮我)?\s*(?:(?:查询|统计|分析|看)(?:一下)?)?\s*'
    r'(?:最近|过去|近)\s*(?:\d{1,2}|[一二两三四五六七八九十])\s*年(?:度)?(?:的)?\s*'
    r'(?P<subject>[\u4e00-\u9fffA-Za-z·（）() ]{0,48}?)(?:的)?'
    + _INVESTMENT_MEASURE + r'(?:的)?(?:变化)?(?:趋势|走势)(?:是)?(?:怎么样的?|如何|怎样|是什么)?'
    r'[\s,，。!?]*(?:并(?:且)?|再|同时)?(?:请)?(?:预测|预估)\s*'
    r'(?:下一年|明年|今年|本年度?|(?:19|20)\d{2}\s*年)(?:的)?\s*'
    + _INVESTMENT_MEASURE + r'(?:情况|是多少|为多少|多少|值)?[。!?]?$'
)
_ANNUAL_FORECAST_OUTPUT = re.compile(
    r'(?:点?(?:预测|预估)(?:值|结果)|(?:95\s*%\s*)?(?:(?:预测|置信)\s*)?区间|'
    r'(?:历史)?回测(?:表现|结果|误差|指标|情况)?|模型(?:选择)?|样本(?:限制|说明)|'
    r'(?:目前|本次|现有)?(?:能|可|已)(?:经)?(?:核实|查到|查询)(?:到|的)?(?:哪些)?(?:历史)?(?:数据|金额|记录))'
)


def _explicit_annual_training_years(host, *, now=None):
    if not host.get('user_ref') or host.get('clarifications'):
        return None
    text = unicodedata.normalize('NFKC', host.get('user_text') or '').strip(' 。！？!?')
    lead = re.match(r'^(?:请\s*)?(?:仅|只)?(?:用|使用|采用|基于|根据|依据|以)\s*', text)
    verbs = _intent(text)
    if not lead or not verbs:
        return None
    verb = verbs[0]
    training = text[lead.end():verb.start()].strip(' ，,')
    period = _HISTORY_RANGE.match(training)
    if (not period or any(period[i] for i in (2, 4))
            or re.search(r'另外|顺便|并查|列出|附上|分别|对比|或|截至|截止|[，,。；;!?\n]', training[period.end():])):
        return None
    if lead[0].rstrip().endswith('以') and not re.search(r'(?:为|作为)(?:历史)?(?:依据|基础|参考)$', training):
        return None
    # Every year named outside the one training range has to be the forecast target.
    if re.search(r'\d{4}', training[period.end():]):
        return None
    try:
        mentions = target_mentions({**host, 'user_text': text}, now=now)
    except ValueError:
        return None
    start, end = int(period[1]), int(period[3])
    if (len(mentions) != 1 or mentions[0][0] != str(end+1)
            or not 1 <= start <= end < _now(host, now).year or not MIN_ANNUAL_HISTORY <= end-start+1 <= 500):
        return None
    left, right = mentions[0][1]
    if verb.end() > left or text[verb.end():left].strip() or any(other.start()<left for other in verbs[1:]):
        return None
    subject, *outputs = re.split(r'[，,。；;!?\n]+', text[right:].strip())
    if not re.fullmatch(r'[\u4e00-\u9fffA-Za-z（）()· ]{0,60}', subject) or re.search(r'和|与|以及|另外|顺便|分别|并|同时', subject):
        return None
    for output in outputs:
        parts = re.sub(r'^(?:并且?|同时)?\s*(?:报告|给出|提供|说明|列出)\s*', '', output).split('、')
        if any(not _ANNUAL_FORECAST_OUTPUT.fullmatch(item.strip()) for part in parts for item in re.split(r'和|以及|及|与', part)):
            return None
    return tuple(f'{year:04}' for year in range(start,end+1))


def _rounded_annual_measure(value):
    from sqlglot import exp

    if isinstance(value, exp.Round):
        decimals = value.args.get('decimals')
        if decimals is None or isinstance(decimals, exp.Literal) and not decimals.is_string and re.fullmatch(r'[0-8]', decimals.this):
            return value.this.unnest()
    return value


def _relative_annual_training_years(host, *, now=None):
    from natural_data.fiscal_history import _FISCAL_AMOUNT

    if not host.get('user_ref') or host.get('clarifications'):
        return None
    text = unicodedata.normalize('NFKC', host.get('user_text') or '')
    verbs = _intent(text)
    if len(verbs) != 1 or not _FISCAL_AMOUNT.search(text) or re.search(
            r'另外|顺便|分别|再查|同时查|(?:含|包括|包含|计入)(?:本年|今年|未结年)', text):
        return None
    history = text[:verbs[0].start()]
    if _HISTORY_RANGE.search(history) or _HISTORY_CUTOFF.search(history) or _FULL_HISTORY.search(history):
        return None
    windows = list(_RECENT.finditer(history))
    if len(windows) != 1 or windows[0][2] != '年':
        return None
    word = windows[0][1]
    count = int(word) if word.isdigit() else _SMALL_NUMBERS[word]
    now = _now(host, now)
    try:
        target = requested_target(host, now)
    except ValueError:
        return None
    if not MIN_ANNUAL_HISTORY <= count < now.year or target not in (str(now.year), str(now.year+1)):
        return None
    return tuple(f'{year:04}' for year in range(now.year-count, now.year))


def annual_investment_history_query(source, host, *, now=None):
    """Offer the closed-year actual-investment read from the current catalog."""
    from sqlglot import exp
    from natural_data.business_dictionary import declared_field_unit
    from natural_data.sql_answer_contract import _ACTUAL
    from natural_data.vendor.dialect import sqlglot_name

    quote = host.get('user_text') or ''
    req = _ANNUAL_INVESTMENT_TREND.fullmatch(unicodedata.normalize('NFKC', quote).strip())
    publication = source.get('business_project') or {}
    dialect = sqlglot_name(source.get('dialect'))
    if (not req or not publication.get('id') or not publication.get('version')
            or dialect not in ('sqlite', 'postgres') or not (years := _relative_annual_training_years(host, now=now))
            or len(years) > 36):
        return None
    mappings = [mapping for mapping in source.get('business_dictionary', []) if mapping.get('source_id') == source['id']]
    subject = req['subject'].strip().removesuffix('的')
    matching_tables = {mapping['table'] for mapping in mappings if mapping.get('kind') == 'table'
                       and subject and subject in (mapping.get('name'), *(mapping.get('synonyms') or []))}
    tables = source.get('tables') or []
    if len(matching_tables) > 1 or not matching_tables and len(tables) != 1:
        return None
    selected = [table for table in tables if not matching_tables or table['name'] in matching_tables]
    if len(selected) != 1:
        return None
    table, = selected
    fields = {column['name']: column for column in table.get('columns', [])}
    clocks = [column for column in fields.values() if (axis := column.get('time_semantics') or {}).get('grain') == 'year'
              and (closed := _CLOSED_YEARS.fullmatch(axis.get('description') or ''))
              and set(years) <= set(re.findall(r'\d{4}', closed[1]))]
    measures = [(mapping, unit[0]) for mapping in mappings
                if mapping.get('kind') == 'field' and mapping.get('table') == table['name']
                and mapping.get('column') in fields
                and (label := mapping.get('name') or '') and _ACTUAL.fullmatch(label)
                and '投入' in label and not re.search(r'累计|余额|期末', mapping.get('description') or '')
                and _ANNUAL_NUMERIC_TYPE.match(fields[mapping['column']].get('type') or '')
                and (unit := declared_field_unit(mapping)) and re.fullmatch(r'(?:人民币)?(?:万|亿)?元', unit[0])]
    if len(clocks) != 1 or len(measures) != 1:
        return None
    time_column = clocks[0]['name']
    (measure, unit), = measures
    time_value = exp.column(time_column, quoted=True)
    amount = exp.column(measure['column'], quoted=True)
    relation = exp.to_table(table['name'], dialect=dialect).transform(
        lambda node: exp.to_identifier(node.name, quoted=True) if isinstance(node, exp.Identifier) else node)
    query = exp.select(exp.alias_(time_value, '年度', quoted=True),
                       exp.alias_(exp.Sum(this=amount), measure['name'], quoted=True)).from_(relation).where(
        exp.In(this=time_value, expressions=[exp.Literal.string(year) for year in years])
    ).group_by(time_value).order_by(time_value)
    exact = bool(matching_tables)
    request = {'source_id':source['id'], 'sql':query.sql(dialect=dialect),
               'title':f'{subject if exact else ""}实际财政投入年度汇总', 'purpose':'answer', 'chart_type':'column'}
    return {
        'schema_version':1, 'quote':quote, 'request_ref':host['user_ref'], 'source_id':source['id'],
        'publication':{key:publication[key] for key in ('id','version')},
        'connection_revision':source.get('connection_revision',0),
        'table':table['name'], 'amount_role':'actual_fiscal', 'unit':unit, 'closed_years':list(years),
        'scope': 'published_table_match' if exact else 'requires_subject_evidence',
        **({'query_request':request} if exact else {
            'candidate_query':request,
            'instruction':('当前目录已包含这个候选表的年度和金额字段、来源及单位。近年趋势只需汇总这些已结年度的实际投入。'
                            '核对候选表和原问主体。有依据且没有额外范围条件时，可以直接一次执行候选汇总。'
                            '业务主体或附加条件仍不清楚时，按实际需要核查该部分。不要为重复读已呈现的字段再次查目录。'),
        }),
    }


def closed_annual_forecast_read(sql, source, host):
    """Keep simple authorized annual aggregates inside the requested closed years."""
    from sqlglot import exp, parse
    from sqlglot.errors import SqlglotError
    from natural_data.display_units import direct_published_sums
    from natural_data.snapshot_scope import _physical_table
    from natural_data.vendor.dialect import sqlglot_name

    explicit = _explicit_annual_training_years(host)
    relative = _relative_annual_training_years(host) if not explicit else None
    years = explicit or relative
    dialect = sqlglot_name(source.get('dialect'))
    if not years or dialect not in ('postgres', 'sqlite') or not (source.get('business_project') or {}).get('version'):
        return sql
    try:
        statements = parse(sql, read=dialect)
    except SqlglotError:
        return sql
    if len(statements) != 1 or not isinstance(tree := statements[0], exp.Select):
        return sql
    if (tree.find(exp.CTE, exp.Subquery, exp.Join, exp.SetOperation, exp.Case, exp.Window, exp.Or)
            or any(node.comments for node in tree.walk())
            or any(value for key, value in tree.args.items() if key not in ('expressions','from','where','group','order'))):
        return sql
    relation = (tree.args.get('from') or exp.From()).this
    group = tree.args.get('group')
    where = tree.args.get('where')
    if (not (table := _physical_table(relation, source, dialect)) or not group or len(group.expressions) != 1
            or (relation.args.get('alias') or exp.TableAlias()).columns):
        return sql
    fields = next(item['columns'] for item in source['tables'] if item['name'] == table)
    confirmed = {column['name'] for column in fields if (semantics := column.get('time_semantics') or {}).get('grain') == 'year'
                 and (declaration := _CLOSED_YEARS.fullmatch(semantics.get('description','')))
                 and set(years) <= set(re.findall(r'\d{4}', declaration[1]))}
    projected = [(item, item.unalias().unnest()) for item in tree.expressions]
    time = [(item, value) for item, value in projected if isinstance(value, exp.Column) and value.name in confirmed
            and value.table in ('',relation.alias_or_name)]
    if (len(time)!=1 or any(not isinstance(_rounded_annual_measure(value), exp.AggFunc)
                            for item,value in projected if item is not time[0][0])):
        return sql
    item, clock = time[0]
    grouped = group.expressions[0].unnest()
    if not isinstance(grouped, exp.Column) or grouped.name not in (clock.name,item.alias_or_name) or grouped.table not in ('',relation.alias_or_name):
        return sql
    money = direct_published_sums(tree, source, source['dialect'])
    if not any(re.fullmatch(r'(?:人民币)?(?:万|亿)?元', item.get('unit') or '') for item in money.values()):
        return sql
    boundary = where.this.unnest() if where else None
    changed = False
    if relative and (not boundary or not any(col.name == clock.name for col in boundary.find_all(exp.Column))):
        bounds = exp.In(this=clock.copy(), expressions=[exp.Literal.string(year) for year in years])
        tree.set('where', exp.Where(this=exp.and_(boundary, bounds) if boundary else bounds))
        changed = True
    elif (not isinstance(boundary, (exp.In, exp.Between)) or not isinstance(field := boundary.this, exp.Column)
          or field.name != clock.name or field.table not in ('', relation.alias_or_name)):
        return sql
    else:
        def year(value):
            return isinstance(value, exp.Literal) and bool(re.fullmatch(r'\d{4}', value.this))

        if isinstance(boundary, exp.In):
            items = boundary.expressions
            if not all(year(item) for item in items) or not set(years) <= {item.this for item in items}:
                return sql
            keep = [item for item in items if item.this in years]
            changed = len(keep) != len(items)
            if changed:
                boundary.set('expressions', keep)
        else:
            low, high = boundary.args.get('low'), boundary.args.get('high')
            if not year(low) or not year(high) or not low.this <= years[0] <= years[-1] <= high.this:
                return sql
            changed = (low.this, high.this) != (years[0], years[-1])
            if changed:
                low.set('this', years[0])
                high.set('this', years[-1])
    if relative and (actual := _annual_actual_projection(tree, relation, table, fields, money, source, host)):
        tree.append('expressions', actual)
        changed = True
    return tree.sql(dialect=dialect) if changed else sql


def _annual_actual_projection(tree, relation, table, fields, sums, source, host):
    """Collect actual spending beside a budget sum on the same closed-year population."""
    from sqlglot import exp
    from natural_data.business_dictionary import declared_field_unit
    from natural_data.display_units import unit_label
    from natural_data.sql_answer_contract import _ACTUAL, _BUDGET

    question = unicodedata.normalize('NFKC', host.get('user_text') or '')
    if ('预算' in question or not re.search(r'(?:投资|投入)(?:金额|额)', question)
            or re.search(r'全部来源|各(?:类)?来源|全口径|非财政|总(?:投资|投入)|(?:投资|投入)总额|只看|仅看|按单位|占比|比较|对比|排名', question)):
        return None
    monetary_sums = [item for item in sums.values() if re.fullmatch(r'(?:人民币)?(?:万|亿)?元', item['unit'])]
    if len(monetary_sums) != 1 or not _BUDGET.fullmatch(monetary_sums[0]['label']):
        return None
    columns = {field['name']: field for field in fields}
    bindings = [mapping for mapping in source.get('business_dictionary', [])
                if mapping.get('kind') == 'field' and mapping.get('source_id') == source['id']
                and mapping.get('table') == table and mapping.get('column') in columns]
    actual = [mapping for mapping in bindings if _ACTUAL.fullmatch(mapping.get('name') or '')]
    if len(actual) != 1:
        return None
    [measure] = actual
    name, label = measure['column'], measure['name']
    unit = declared_field_unit(measure)
    field_type = columns[name].get('type') or ''
    if (not unit or not re.fullmatch(r'(?:人民币)?(?:万|亿)?元', unit[0])
            or not _ANNUAL_NUMERIC_TYPE.match(field_type)):
        return None
    where = tree.args.get('where')
    money_columns = {mapping['column'].casefold() for mapping in bindings
                     if _ACTUAL.fullmatch(mapping.get('name') or '') or _BUDGET.fullmatch(mapping.get('name') or '')}
    alias = unit_label(label, unit[0])
    if ((where and any(column.name.casefold() in money_columns for column in where.find_all(exp.Column)))
            or any(item.alias_or_name.casefold() == alias.casefold() for item in tree.expressions)):
        return None
    table_alias = relation.args['alias'].this.copy() if relation.alias else None
    value = exp.Column(this=exp.to_identifier(name, quoted=True), table=table_alias)
    return exp.alias_(exp.Sum(this=value), alias, quoted=True)


def annual_forecast_boundary(source, host, *, now=None):
    """Identify an explicit annual window ruled out before any data query."""
    publication = source.get('business_project') or {}
    request = host.get('user_ref')
    source_id = source.get('id')
    text = unicodedata.normalize('NFKC', host.get('user_text') or '').strip()
    if not (request and source_id and publication.get('id') and publication.get('version') and text):
        return None
    clauses = [clause.strip() for clause in re.split(r'[。！？!?;；\n]+', text) if clause.strip()]
    if not clauses or len(clauses) > 2 or len(clauses) == 2 and not _ANNUAL_CLOSE_CHECK.fullmatch(clauses[1]):
        return None
    sentence = clauses[0]
    training = _ANNUAL_TRAINING.match(sentence)
    verbs = _intent(sentence)
    if not training or len(verbs) != 1:
        return None
    verb, = verbs
    try:
        targets = target_mentions({**host, 'user_text': sentence}, now=now)
    except ValueError:
        return None
    if len(targets) != 1:
        return None
    (target, (left, right)), = targets
    if (len(target) != 4 or not training.end() < verb.start() <= verb.end() <= left
            or sentence[verb.end():left].strip()):
        return None
    start, end = map(int, training.groups())
    if not 1 <= start <= end < 9999 or int(target) != end + 1:
        return None
    measure_text = _ANNUAL_REQUEST_CONNECTOR.sub('', sentence[training.end():verb.start()]).strip()
    if count := _ANNUAL_COUNT.match(measure_text):
        declared = int(count[1]) if count[1].isdigit() else _SMALL_NUMBERS[count[1]]
        if declared != end - start + 1:
            return None
        measure_text = measure_text[count.end():]
    measure_text = re.sub(r'^(?:的|各年度的?)\s*|(?:的)?(?:历史)?(?:数据|记录)$', '', measure_text).strip()
    tail = re.sub(r'^\s*(?:的\s*)?(?:全年|年度)?\s*', '', sentence[right:])
    tail = re.sub(r'(?:的)?(?:预计)?(?:是多少|为多少|多少)$', '', tail).strip()
    if not measure_text or tail and not measure_text.endswith(tail):
        return None

    mappings = [item for item in source.get('business_dictionary', []) if item.get('source_id') == source_id]
    measures = [item for item in mappings if item.get('kind') == 'field'
                and measure_text in (item.get('name'), *(item.get('synonyms') or []))]
    if len(measures) != 1:
        return None
    measure, = measures
    time_zone = host.get('client_time_zone') or 'UTC'
    try:
        current_year = (now or dt.datetime.now(dt.timezone.utc)).astimezone(ZoneInfo(time_zone)).year
    except (ValueError, ZoneInfoNotFoundError):
        return None
    if end - start + 1 >= MIN_ANNUAL_HISTORY and end < current_year:
        return None
    tables = [table for table in source.get('tables', []) if table['name'] == measure.get('table')]
    if len(tables) != 1:
        return None
    fields = {field['name']: field for field in tables[0]['columns']}
    clocks = [item for item in mappings if item.get('kind') == 'time' and item.get('grain') == 'year'
              and item.get('table') == measure['table'] and item.get('column') in fields]
    if len(clocks) != 1 or measure.get('column') not in fields or clocks[0]['column'] == measure['column']:
        return None
    clock = clocks[0]
    if re.search(r'财年|财政年度|会计年度|学年|非自然年|滚动年度|跨年结算',
                 ' '.join((clock.get('name') or '', clock.get('description') or ''))):
        return None
    if not _ANNUAL_NUMERIC_TYPE.match(str(fields[measure['column']].get('type') or '')):
        return None
    return {'schema_version': 1, 'kind': 'annual_forecast_boundary', 'request_ref': request,
            'source_id': source_id, 'publication': {'id': publication['id'], 'version': publication['version']},
            'connection_revision': source.get('connection_revision', 0), 'target_period': target,
            'start_year': start, 'end_year': end, 'current_year': current_year, 'min_years': MIN_ANNUAL_HISTORY,
            'verified_not_closed_years': list(range(max(start, current_year), end + 1))}


def _same_publication(result,source):
    return (source is not None and result.get('source_id')==source.get('id')
            and result.get('business_project')==source.get('business_project')
            and (result.get('source_provenance') or {}).get('connection_revision')==source.get('connection_revision'))


def snapshot_history_coverage(result, source, host):
    """Record the closed-month coverage of a fully queried published stock series."""
    horizon = relative_forecast_horizon(host)
    contract = result.get('metric_contract') or {}
    spec = contract.get('semantic_spec') or {}
    scope = contract.get('scope') or {}
    operands = spec.get('operands') or []
    receipt = result.get('receipt') or {}
    if (not horizon or not host.get('user_ref')
            or not _same_publication({**result, 'source_id': receipt.get('source_id')}, source)
            or result.get('status') != 'succeeded' or result.get('truncated')
            or receipt.get('read_only') is not True
            or (result.get('validation') or {}).get('state') == 'conflict'
            or not operands or any(item.get('aggregation') != 'snapshot_sum' for item in operands)
            or scope.get('dimensions') or scope.get('filters')
            or not (publication := result.get('business_project'))):
        return None
    monthly = re.compile(r'(?:19|20)\d{2}-(?:0[1-9]|1[0-2])')
    declared = {period for period in (spec.get('coverage') or [])
                if monthly.fullmatch(period) and period < horizon['start']}
    facts = result.get('facts') or []
    periods = [fact.get('dimensions', {}).get('period') for fact in facts]
    if (not declared or len(facts) != result.get('row_count') or len(facts) != len(declared)
            or set(periods) != declared or declared != set(scope.get('periods') or [])):
        return None
    month = dt.date.fromisoformat(min(periods) + '-01')
    cutoff = dt.date.fromisoformat(horizon['start'] + '-01')
    expected = []
    while month < cutoff and len(expected) <= 120:
        expected.append(month.strftime('%Y-%m'))
        month = dt.date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    if not 2 <= len(expected) <= 120:
        return None
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': host['user_text'],
            'result_ref': result['result_ref'], 'source_id': source['id'], 'publication': publication,
            'target_period': horizon['end'], 'horizon_start': horizon['start'], 'months': expected}


def _read_sql(result,source=None):
    from sqlglot import errors, parse_one
    from natural_data.vendor.dialect import sqlglot_name

    from natural_data.read_limits import execution_basis

    sql=result.get('sql')
    basis=result.get('execution_basis') or {}
    if sql and basis.get('policy_sql') and execution_basis(basis['policy_sql'],sql)==basis:
        sql=basis['policy_sql']
    if sql:
        try:return parse_one(sql,read=sqlglot_name(source['dialect']) if source else None)
        except errors.SqlglotError:pass
    return None


def published_measure_label(result,source,value_column):
    """Bind a direct SQL projection to the field name in the same publication."""
    from sqlglot import exp
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from natural_data.vendor.dialect import sqlglot_name

    if (not source.get('id') or not (source.get('business_project') or {}).get('version')
            or source.get('connection_revision') is None or not _same_publication(result,source)
            or (result.get('receipt') or {}).get('read_only') is not True):
        return None
    # Use the executed SQL so the proof cannot come from an unbound older plan.
    tree=_read_sql({'sql':result.get('sql')},source)
    if (not isinstance(tree,exp.Select) or tree.args.get('joins')
            or tree.find(exp.CTE,exp.Subquery,exp.SetOperation)):
        return None
    tree=normalize_identifiers(tree,dialect=sqlglot_name(source['dialect']))
    from_clause=tree.args.get('from')
    table=from_clause.this if from_clause else None
    if not isinstance(table,exp.Table) or not isinstance(table.this,exp.Identifier):
        return None
    alias=table.args.get('alias')
    if alias and alias.args.get('columns'):
        return None
    columns=result.get('columns') or []
    if (columns.count(value_column)!=1 or len(tree.expressions)!=len(columns)
            or any(item.is_star for item in tree.expressions)):
        return None
    key=str.casefold if source['dialect']=='sqlite' else str
    projection=tree.expressions[columns.index(value_column)]
    column=projection.unalias().unnest()
    if key(projection.alias_or_name)!=key(value_column):
        return None
    if not isinstance(column,exp.Column):
        # A simple SUM of one published field keeps that field's identity (e.g. a
        # per-project ledger summed to an annual total). Reuse the existing
        # published-sum measurement contract instead of a new label rule; the
        # binding comes from the executed projection, never from the alias text.
        from natural_data.display_units import direct_published_sums
        sums={key(name):entry for name,entry in direct_published_sums(tree,source,source['dialect']).items()}
        entry=sums.get(key(value_column))
        return entry.get('label') if entry else None
    if (column.table and key(column.table)!=key(table.alias_or_name)
            or column.db and key(column.db)!=key(table.db)
            or column.catalog and key(column.catalog)!=key(table.catalog)):
        return None
    path=key('.'.join(part.name for part in table.parts))
    tables=[item for item in source.get('tables',[]) if key(item['name'])==path
            or not table.db and not table.catalog and key(item['name']).endswith('.'+path)]
    if len(tables)!=1 or not any(key(field['name'])==key(column.name) for field in tables[0]['columns']):
        return None
    labels=[mapping.get('name') for mapping in source.get('business_dictionary',[])
            if mapping.get('kind')=='field' and mapping.get('source_id')==source['id']
            and mapping.get('table')==tables[0]['name'] and key(mapping.get('column') or '')==key(column.name)]
    return labels[0] if len(labels)==1 and labels[0] else None


def _annual_sql_history(result,source,time_column,value_column,labels):
    """Use a published complete-period declaration and a comparable direct projection."""
    from sqlglot import exp

    if not _same_publication(result,source) or (result.get('receipt') or {}).get('read_only') is not True:
        return False
    tree=_read_sql(result,source)
    if not isinstance(tree,exp.Select) or tree.find(exp.CTE,exp.Subquery,exp.Join,exp.SetOperation,exp.Case,exp.Window,exp.Or):
        return False
    refs=list(tree.find_all(exp.Table))
    if len(refs)!=1 or not isinstance(refs[0].this,exp.Identifier):return False
    ref=refs[0]
    path='.'.join(part.name for part in ref.parts)
    tables=[t for t in source.get('tables',[]) if t['name']==path or not ref.db and t['name'].endswith('.'+path)]
    # The natural annual aggregation may carry extra published columns (e.g. a
    # project count beside the amount). Bind the selected time/measure pair; the
    # whole query is still restricted to one table without joins/subqueries/etc.
    if len(tables)!=1 or len(tree.expressions)!=len(result['columns']):return False
    if (time_column==value_column or result['columns'].count(time_column)!=1
            or result['columns'].count(value_column)!=1):return False
    time=tree.expressions[result['columns'].index(time_column)].unalias().unnest()
    value=_rounded_annual_measure(tree.expressions[result['columns'].index(value_column)].unalias().unnest())
    if not isinstance(time,exp.Column) or time.table not in ('',ref.alias_or_name):return False
    semantics=next((c.get('time_semantics') for c in tables[0]['columns'] if c['name']==time.name),None) or {}
    declaration=_CLOSED_YEARS.fullmatch(semantics.get('description','')) if semantics.get('grain')=='year' else None
    if not declaration or not set(labels)<=set(re.findall(r'\d{4}',declaration[1])):return False
    # Require one fixed projected measure with no year-specific substitutions.
    if not isinstance(value,(exp.Column,exp.Count,exp.Sum,exp.Avg,exp.Min,exp.Max)):return False
    if not isinstance(value,(exp.Column,exp.Count)) and not isinstance(value.this,exp.Column):return False
    unit=(result.get('column_unit_evidence') or {}).get(value_column) or {}
    return unit.get('kind') in ('published_field','published_row') or _direct_count(value)


def _relative_period_counts(text,grain):
    for match in _RECENT.finditer(text):
        count=int(match[1]) if match[1].isdigit() else _SMALL_NUMBERS[match[1]]
        if match[2]=='年' and grain=='month':count*=12
        elif match[2]=='年' and grain=='quarter':count*=4
        elif (match[2]=='年' and grain!='year') or (match[2]!='年' and grain!='month'):
            require(False,'请求的历史窗口与当前训练粒度无法核对，请明确训练期间。')
        yield count


def relative_month_window(host,mentions):
    """Resolve a single relative monthly training window ending before this month."""
    targets={value for value,_ in mentions}
    if len(targets)!=1 or len(target:=next(iter(targets)))!=7 or _confirmed_history(host,'month'):
        return None
    text=historical_scope_text(host,mentions)
    if _HISTORY_RANGE.search(text) or _HISTORY_CUTOFF.search(text):
        return None
    counts=set(_relative_period_counts(text,'month'))
    if len(counts)!=1 or not 1<=(count:=next(iter(counts)))<=120:
        return None
    now=_now(host)
    stop=now.year*12+now.month-1
    start=stop-count
    if start<12:
        return None
    periods=[f'{i//12:04}-{i%12+1:02}' for i in range(start,stop)]
    return {'target_period':target,'requested_periods':periods,'start_inclusive':periods[0]+'-01',
            'end_exclusive':f'{now:%Y-%m}-01'}


def _reuse_monthly_history(result, source, series, host, mentions):
    """Select a trailing window on an immutable, same-turn query result."""
    grain, pairs, step, index, label = series
    if grain != 'month' or not (requested := relative_month_window(host, mentions)):
        return series, None
    count = len(requested['requested_periods'])
    if len(pairs) <= count or label(pairs[-1][0]) >= _now(host).strftime('%Y-%m'):
        return series, None
    lineage = result.get('result_lineage') or {}
    scope = result.get('request_scope') or {}
    receipt = result.get('receipt') or {}
    publication = (source or {}).get('business_project') or {}
    if (result.get('status') != 'succeeded' or result.get('truncated') or result.get('row_count') != len(pairs)
            or not result.get('result_ref') or receipt.get('read_only') is not True
            or not publication.get('id') or not publication.get('version') or not _same_publication(result, source)
            or lineage.get('purpose') != 'answer' or receipt.get('source_id') != source['id']
            or lineage.get('source_id') != source['id'] or not host.get('user_ref')
            or lineage.get('request_ref') != host['user_ref'] or not scope.get('config_fingerprint')
            or scope['config_fingerprint'] != lineage.get('config_fingerprint')):
        return series, None
    # The input is already ordered and checked for gaps, duplicates and NULLs.
    # Keep the original terminal observation and use the same period indices
    # for month labels and date-valued axes.
    tail = pairs[-count:]
    return (grain, tail, step, index, label), {
        'schema_version': 1, 'kind': 'monthly_training_window', 'selection': 'trailing_requested_periods',
        'result_ref': result['result_ref'], 'requested_periods': count, 'available_periods': len(pairs),
        'available_start': label(pairs[0][0]), 'history_start': label(tail[0][0]), 'origin': label(tail[-1][0]),
    }


def _verify_requested_window(labels,grain,text,*,series=None,host=None):
    exact=False
    for count in _relative_period_counts(text,grain):
        if len(labels)>count:
            suggestion=f'{labels[-count]} 至 {labels[-1]}'
            closed_note=''
            if series is not None and host is not None and grain in ('year','month'):
                _,pairs,step,index,label=series
                now=_now(host)
                closed=now.year-1 if grain=='year' else now.year*12+now.month-2
                if pairs[-1][0]>closed:
                    end=min(pairs[-1][0],closed)
                    suggestion=f'{label(end-(count-1)*step)} 至 {label(end)}'
                    closed_note=f'最新已结期为 {label(closed)}，未结期不得计入训练。'
            require(False,f'原问要求近 {count} 个连续历史期，当前训练包含 {len(labels)} 个（{labels[0]}至{labels[-1]}）。'
                    f'请将训练窗口限定为截至最新已结期的最近 {count} 个连续期（如 {suggestion}），再执行预测。'+closed_note)
        require(len(labels)==count,f'原问要求 {count} 个连续历史期，当前训练包含 {len(labels)} 个（{labels[0]}至{labels[-1]}）。'
                '请补齐所需期间；若需缩短范围，先明确可用期间并取得用户确认。')
        exact=True
    for start,start_month,end,end_month in _HISTORY_RANGE.findall(text):
        if grain not in ('month','year'):continue
        if grain=='year' and (start_month or end_month):
            require(False,'历史窗口含非完整年度的起止月，不能训练年度预测。')
        first=f'{start}-{int(start_month or 1):02}' if grain=='month' else start
        last=f'{end}-{int(end_month or 12):02}' if grain=='month' else end
        require((labels[0],labels[-1])==(first,last),f'训练实际覆盖 {labels[0]} 至 {labels[-1]}，与原问历史窗 {first} 至 {last} 不符。')
        exact=True
    cutoff=_HISTORY_CUTOFF.search(text)
    last=None
    cutoff_date=None
    if cutoff and grain in ('year','month'):
        year,month,day=cutoff.groups()
        year,month=int(year),int(month or 12)
        require(1<=year<=9999 and 1<=month<=12,'历史截止日期无效。')
        month_end=calendar.monthrange(year,month)[1]
        day=int(day or month_end)
        require(1<=day<=month_end,'历史截止日期无效。')
        cutoff_date=dt.date(year,month,day)
        if grain=='year':
            last=f'{year-int((month,day)!=(12,31)):04}'
        else:
            if day<month_end:
                year,month=divmod(year*12+month-2,12);month+=1
            last=f'{year:04}-{month:02}'
        require(labels[-1]<=last,'训练数据包含用户指定历史截止日之后或未关闭的期间。')
    return exact,last,cutoff_date


def _confirmed_history(host,grain):
    from natural_data.temporal_scope import confirmed_scope

    records=[entry for entry in (host or {}).get('clarifications',[]) if entry.get('call_id') and entry.get('question_id')
             and _HISTORY_TOPIC.search(str(entry.get('question',''))+' '+str(entry.get('answer','')))]
    scope=confirmed_scope({'clarifications':records})
    if not scope or grain not in ('month','year'):
        return None
    if grain=='month':
        return scope
    years={month[:4] for month in scope}
    return years if len(scope)==12*len(years) else None


def _authorized_upper_bound(atom,fields,key,grain,last,cutoff):
    """Only a demonstrably matching upper bound can implement a user cutoff."""
    from sqlglot import exp
    from natural_data.day_bounds import inclusive_upper_day

    if not last or not isinstance(atom,(exp.LT,exp.LTE,exp.GT,exp.GTE)):
        return False
    field,value=atom.this.unnest(),atom.expression.unnest()
    if isinstance(atom,(exp.GT,exp.GTE)):
        field,value=value,field
    if not isinstance(field,exp.Column) or key(field.name) not in fields:
        return False
    strict=isinstance(atom,(exp.LT,exp.GT))
    if isinstance(value,exp.Literal):
        if grain=='year' and re.fullmatch(r'\d{4}',value.this):
            return int(value.this)-int(strict)==int(last)
        if grain=='month' and re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',value.this):
            year,month=map(int,value.this.split('-'))
            if strict:
                year,month=divmod(year*12+month-2,12);month+=1
            return f'{year:04}-{month:02}'==last
    while isinstance(value,exp.Cast) and value.to.this in (exp.DataType.Type.DATE,exp.DataType.Type.TIMESTAMP):
        value=value.this.unnest()
    if not isinstance(value,exp.Literal) or not value.is_string:
        return False
    if cutoff is None or strict and value.this=='0001-01-01':
        return False
    return inclusive_upper_day(value,strict=strict,today=dt.date.min)==cutoff


def _complete_sql_history(result,source,time_column,grain,last,cutoff):
    """Trace the selected time axis through one unambiguous chain of SQL sources."""
    from sqlglot import exp
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from sqlglot.optimizer.scope import Scope,build_scope
    from natural_data.request_scope import _and_atoms
    from natural_data.vendor.dialect import sqlglot_name

    tree=_read_sql(result,source)
    if not isinstance(tree,exp.Select) or not source:
        return False
    dialect=sqlglot_name(source.get('dialect'))
    key=str.casefold if dialect=='sqlite' else str
    scope=build_scope(normalize_identifiers(tree,dialect=dialect))
    names={key(time_column)}
    visited=set()
    try:
        while scope is not None and id(scope) not in visited:
            visited.add(id(scope))
            select=scope.expression
            if not isinstance(select,exp.Select) or any(select.args.get(arg) for arg in ('limit','offset','having','qualify','sample','joins')):
                return False
            if select.find(exp.Window) or len(scope.selected_sources)!=1:
                return False
            alias,(_,relation)=next(iter(scope.selected_sources.items()))
            names_and_projections=list(zip(map(key,scope.outer_columns or [item.alias_or_name for item in select.expressions]),select.expressions))
            projections=dict(names_and_projections)
            if len(projections)!=len(names_and_projections):
                return False
            fields=set()
            for name in names:
                projection=projections.get(name)
                if projection is None and '*' in projections:
                    fields.add(name)
                    continue
                expression=projection.unalias().unnest() if projection is not None else None
                if expression is None or expression.find(exp.Query,exp.Case,exp.AggFunc):
                    return False
                columns=list(expression.find_all(exp.Column))
                if not columns or any(c.table and key(c.table)!=key(alias) for c in columns):
                    return False
                fields.update(key(c.name) for c in columns)
            where=select.args.get('where')
            if where and where.find(exp.Query):
                return False
            time_fields=fields|names
            for atom in _and_atoms(where.this if where else None):
                if (any(key(c.name) in time_fields for c in atom.find_all(exp.Column))
                        and not _authorized_upper_bound(atom,fields,key,grain,last,cutoff)):
                    return False
            if isinstance(relation,exp.Table):
                return isinstance(relation.this,exp.Identifier) and not (relation.args.get('sample') or relation.args.get('version'))
            if not isinstance(relation,Scope):
                return False
            scope,names=relation,fields
    except SqlglotError:
        pass
    return False


def _published_complete_month(result,source,time_column,period):
    if not _same_publication(result,source) or (result.get('receipt') or {}).get('read_only') is not True:
        return False
    if not (source.get('business_project') or {}).get('version'):
        return False
    contract=result.get('metric_contract') or {}
    spec=contract.get('semantic_spec') or {}
    if time_column=='period' and period in (spec.get('coverage') or []) and period in (contract.get('periods') or []):
        return True
    from sqlglot import exp
    from natural_data.period_grain import _projected_periods
    from natural_data.vendor.dialect import sqlglot_name

    tree=_read_sql(result,source)
    if not isinstance(tree,exp.Select) or tree.find(exp.Join,exp.SetOperation):
        return False
    columns=result['columns']
    if time_column not in columns or len(tree.expressions)!=len(columns) or any(item.is_star for item in tree.expressions):
        return False
    projected,_,_=_projected_periods(tree,source,sqlglot_name(source['dialect']))
    axis=projected[columns.index(time_column)] if len(projected)==len(columns) else None
    if not axis or axis.grain!='month' or axis.part:
        return False
    field=next((column for table in source['tables'] if table['name']==axis.table for column in table['columns']
                if column['name']==axis.column),{})
    declaration=_CLOSED_MONTHS.fullmatch((field.get('time_semantics') or {}).get('description',''))
    return bool(declaration and period in re.findall(r'\d{4}-\d{2}',declaration[1]))


def verify_history(result,source,time_column,value_column,series,host,mentions):
    grain,pairs,_,_,label=series
    labels=[label(i) for i,_ in pairs]
    minimum=MIN_ANNUAL_HISTORY if grain=='year' else 12
    require((result.get('validation') or {}).get('state')!='conflict' and not (result.get('checks') or {}).get('value_warnings'),
            '历史结果仍有范围或数值冲突，请核对后再预测。')
    require(result.get('purpose')!='explore' and (result.get('result_lineage') or {}).get('purpose')!='explore',
            '探索查询不能作为正式预测的训练序列，请按原问范围执行答复查询。')
    if source and not result.get('metric_contract'):
        from sqlglot import exp
        from natural_data.published_scope import inspect, registered_total

        if scope := registered_total(source, host.get('user_text') or '', host):
            tree = _read_sql(result, source)
            checks = inspect(tree, scope, source, isinstance(tree, exp.Select) and not tree.find(exp.CTE, exp.Subquery)) if tree else []
            require(_same_publication(result, source) and checks and all(state == 'checked' for state, _ in checks),
                    f'训练数据尚无法证明保留已发布总量「{scope["alias"]}」的完整总体。请按登记范围重新查原期间。')
    history_text=historical_scope_text(host,mentions)
    exact_window,last,cutoff=_verify_requested_window(labels,grain,history_text,series=series,host=host)
    contract=result.get('metric_contract') or {}
    spec=contract.get('semantic_spec') or {}
    current_month=_now(host).strftime('%Y-%m')
    if (window := relative_forecast_horizon(host)) and (window['end'], window['span']) in mentions:
        month, year = int(window['start'][5:]), int(window['start'][:4])
        last_close = (dt.date(year, month, 1) - dt.timedelta(days=1)).strftime('%Y-%m')
        require(grain == 'month' and labels[-1] == last_close,
                f'未来一年趋势需要截至最近已结月 {last_close} 的连续月度记录。')
    if grain=='month' and _RECENT.search(history_text) and labels[-1]==current_month:
        require(_published_complete_month(result,source,time_column,current_month),
                f'{current_month} 尚未结月，当前来源未发布该月已完整的依据。请以最近已结月为末期重新核对完整窗口；'
                '部分月不能作为整月历史观测。')
    if any(not _NEGATED.search(history_text[:match.start()]) for match in _FULL_HISTORY.finditer(history_text)):
        confirmed=_confirmed_history(host,grain)
        if confirmed:
            require(set(labels)==confirmed,'训练范围与用户已确认的历史期间不符。')
        elif not exact_window:
            if spec.get('coverage'):
                expected={period for period in spec['coverage'] if len(period)==len(labels[0]) and (not last or period<=last)}
                complete=set(labels)==expected
            else:
                complete=_complete_sql_history(result,source,time_column,grain,last,cutoff)
            require(complete,'原问要求完整历史，当前结果无法证明未裁剪原时间范围。请使用保留原业务筛选的完整历史；'
                    '已核实缺期时直接说明无法预测。若改用较短窗口，须由用户明确指定或确认起止期间。')
    operands=spec.get('operands') or []
    snapshots=bool(operands) and all(o.get('aggregation')=='snapshot_sum' for o in operands)
    if _SNAPSHOT.search(history_text) or (snapshots and value_column=='value'):
        require(snapshots and value_column=='value' and time_column=='period' and len(labels)>=minimum
                and set(labels)<=set(spec.get('coverage') or []) and set(labels)<=set(contract.get('periods') or []),
                '存量预测需要已登记的连续历史快照；单次当前快照或同期业务发生量不能代替。')
    if grain=='year':
        require(len(labels)>=MIN_ANNUAL_HISTORY,'年度预测至少需要5个连续、同口径的完整年度。')
        require(int(labels[-1])<_now(host).year,'当前或未来年度尚未结年，缺可核对的完整年度观测。')
        registered=(value_column=='value' and time_column=='period' and spec.get('time_mode')=='period'
                    and set(labels)<=set(spec.get('coverage') or []) and set(labels)<=set(contract.get('periods') or []))
        require(registered or _annual_sql_history(result,source,time_column,value_column,labels),
                '尚无法证明各年度采用完整的同一来源、单位和统计口径；请用已发布年度指标，或在年度时间字段登记“已确认完整年度：2020、2021…；依据：…”后核对查询。')


def _forecast_amount_role(result, label, column):
    from natural_data.sql_answer_contract import _ACTUAL, _BUDGET

    contract = result.get('sql_answer_contract') or {}
    measure = (contract.get('measurements') or {}).get(column) or {}
    if (contract.get('result_ref') == result.get('result_ref') and measure.get('basis') == 'published_field_sum'
            and measure.get('role') in ('budget', 'actual_fiscal')):
        return measure['role']
    metric = result.get('metric_contract') or {}
    if column == 'value' and metric.get('role') in ('budget', 'actual_fiscal'):
        return metric['role']
    if _ACTUAL.fullmatch(label or '') or _EXPLICIT_ACTUAL_AMOUNT.fullmatch(label or ''):
        return 'actual_fiscal'
    return 'budget' if _BUDGET.fullmatch(label or '') else None


def _requested_forecast_amount_role(host, mentions):
    from natural_data.sql_answer_contract import _ACTUAL

    if len(mentions) != 1 or len(mentions[0][0]) != 4:
        return None
    text = host.get('user_text') or ''
    start, end = mentions[0][1]
    tail = unicodedata.normalize('NFKC', text[end:].strip())
    subject = re.split(r'[，,。！!？?；;\n]', tail, maxsplit=1)[0]
    if _ACTUAL.search(subject) or _EXPLICIT_ACTUAL_AMOUNT.search(subject):
        return 'actual_fiscal'
    if '预算' in subject:
        return 'budget'
    historical = unicodedata.normalize('NFKC', text[:start])
    if _ACTUAL.search(historical) or _EXPLICIT_ACTUAL_AMOUNT.search(historical):
        return 'actual_fiscal'
    return None


def _forecast_role_conflict(requested, actual):
    return (requested == 'actual_fiscal' and actual != requested
            or requested == 'budget' and actual == 'actual_fiscal')


def _bound_forecast_amount_role(result, host, mentions):
    explicit = _requested_forecast_amount_role(host, mentions)
    if explicit or len(mentions) != 1 or len(mentions[0][0]) != 4:
        return explicit
    question = host.get('user_text') or ''
    if ('预算' in question or not re.search(r'(?:投资|投入)(?:总)?(?:金额|额)', question)):
        return None
    contract = result.get('sql_answer_contract') or {}
    measures = (contract.get('measurements') or {}).values()
    if contract.get('result_ref') == result.get('result_ref') and {
            m.get('role') for m in measures if m.get('basis') == 'published_field_sum'
    } >= {'actual_fiscal', 'budget'}:
        return 'actual_fiscal'
    return None


def annual_amount_reference_label(result, source):
    """Return the declared monetary label when an actual-amount query selected a different field."""
    from natural_data.display_units import direct_published_sums

    scope = result.get('request_scope') or {}
    request = scope.get('request') or {}
    target = scope.get('forecast_target') or {}
    if (target.get('grain') != 'year' or target.get('evidence') != request
            or (result.get('result_lineage') or {}).get('purpose') != 'answer'
            or not _same_publication(result, source)):
        return None
    host = {'user_text': request.get('quote') or '', 'user_ref': request.get('user_ref')}
    try:
        mentions = target_mentions(host)
    except ValueError:
        return None
    if _requested_forecast_amount_role(host, mentions) != 'actual_fiscal':
        return None
    measures = [(column, binding) for column, binding in direct_published_sums(_read_sql(result, source), source,
                 source['dialect']).items() if re.fullmatch(r'(?:人民币)?(?:万|亿)?元', binding.get('unit') or '')]
    if len(measures) != 1:
        return None
    (column, binding), = measures
    if _forecast_amount_role(result, binding['label'], column) == 'actual_fiscal':
        return None
    return column, binding['label']


def _annual_forecast_gap(result, source, time_column, value_column, label, host, mentions):
    from sqlglot import exp

    scope = result.get('request_scope') or {}
    if scope.get('unresolved') or any(item.get('kind') != 'time' for item in scope.get('constraints') or []):
        return None
    history = historical_scope_text(host, mentions)
    spans = _HISTORY_RANGE.findall(history)
    if len(spans) != 1 or not label or label not in history:
        return None
    first, first_month, last, last_month = spans[0]
    start, end = int(first), int(last)
    if (first_month or last_month or not 1 <= start <= end < _now(host).year
            or not MIN_ANNUAL_HISTORY <= end - start + 1 <= 500 or int(mentions[0][0]) != end + 1):
        return None
    requested = [f'{year:04}' for year in range(start, end + 1)]
    rows = result['rows']
    periods = [str(row.get(time_column)) for row in rows]
    if len(set(periods)) != len(periods) or not set(periods) <= set(requested):
        return None
    amounts = [row.get(value_column) for row in rows]
    if any(amount is not None and (type(amount) not in (int, float) or not math.isfinite(amount)) for amount in amounts):
        return None
    observed = sorted(period for period, amount in zip(periods, amounts) if amount is not None)
    missing = sorted(set(requested) - set(observed))
    if not observed or not missing or not _annual_sql_history(result, source, time_column, value_column, observed):
        return None
    tree = _read_sql(result, source)
    if any(value for key, value in tree.args.items() if key not in ('expressions', 'from', 'where', 'group', 'order')):
        return None
    time = tree.expressions[result['columns'].index(time_column)].unalias().unnest()
    where = tree.args.get('where')
    clause = where.this.unnest() if where else None
    def same_axis(column):
        return isinstance(column, exp.Column) and column.name == time.name and column.table in ('', time.table)
    if isinstance(clause, exp.In) and same_axis(clause.this):
        years = {item.this for item in clause.expressions if isinstance(item, exp.Literal)}
        if len(years) != len(clause.expressions) or years != set(requested):
            return None
    elif isinstance(clause, exp.Between) and same_axis(clause.this):
        low, high = clause.args.get('low'), clause.args.get('high')
        if not isinstance(low, exp.Literal) or not isinstance(high, exp.Literal) or (low.this, high.this) != (first, last):
            return None
    else:
        return None
    return observed, {'requested_years': requested, 'missing_years': missing}


def complete_annual_forecast_history(result, source):
    """Prove a complete closed-year annual series on a formal answer receipt.

    Annual twin of complete_monthly_forecast_history: reuse the existing annual
    training contract (calendar_series + verify_history) instead of a new semantic
    rule, so a deterministic dispatch can trigger forecasting from real same-scope
    evidence. The receipt may carry extra published columns; exactly one published
    time/measure pair must satisfy the contract.
    """
    scope = result.get('request_scope') or {}
    target = scope.get('forecast_target') or {}
    request = scope.get('request') or {}
    lineage = result.get('result_lineage') or {}
    publication = source.get('business_project') or {}
    revision = source.get('connection_revision')
    receipt = result.get('receipt') or {}
    if (result.get('status') != 'succeeded' or result.get('truncated') or not result.get('result_ref')
            or receipt.get('read_only') is not True or lineage.get('purpose') != 'answer'
            or result.get('purpose') == 'explore'
            or receipt.get('source_id') != source.get('id') or lineage.get('source_id') != source.get('id')
            or not _same_publication(result, source) or not publication.get('id')
            or not publication.get('version') or revision is None
            or (result.get('validation') or {}).get('state') == 'conflict'
            or (result.get('checks') or {}).get('value_warnings')
            or not scope.get('config_fingerprint') or scope['config_fingerprint'] != lineage.get('config_fingerprint')
            or scope.get('proposal') or scope.get('inherited')
            or target.get('grain') != 'year' or target.get('evidence') != request or request.get('clarifications')
            or not request.get('user_ref') or lineage.get('request_ref') != request['user_ref']):
        return None
    target_period = target.get('period') or ''
    if not re.fullmatch(r'\d{4}', target_period):
        return None
    columns = result.get('columns') or []
    rows = result.get('rows') or []
    if not 1 <= len(rows) <= 500 or len(rows) != result.get('row_count') or len(columns) < 2:
        return None
    host = {'user_text': request.get('quote') or '', 'user_ref': request.get('user_ref'),
            'clarifications': request.get('clarifications') or [],
            'client_time_zone': source.get('client_time_zone') or 'UTC'}
    try:
        mentions = target_mentions(host)
    except ValueError:
        return None
    if {value for value, _ in mentions} != {target_period}:
        return None
    requested_role = _bound_forecast_amount_role(result, host, mentions)
    measures = [c for c in columns if columns.count(c) == 1 and published_measure_label(result, source, c)]
    candidates = [(t, v) for t in columns if columns.count(t) == 1 for v in measures if v != t]
    contract = result.get('metric_contract') or {}
    if contract and columns.count('period') == 1 and columns.count('value') == 1:
        candidates.append(('period', 'value'))
    found = []
    for time_column, value_column in candidates:
        measure_label = published_measure_label(result, source, value_column) or contract.get('name')
        role = _forecast_amount_role(result, measure_label, value_column)
        reference_only = _forecast_role_conflict(requested_role, role)
        if reference_only and requested_role != 'actual_fiscal':
            continue
        gap = {}
        try:
            series = calendar_series(rows, time_column, value_column, 'year')
            verify_history(result, source, time_column, value_column, series, host, mentions)
        except ValueError:
            missing = _annual_forecast_gap(result, source, time_column, value_column, measure_label, host, mentions)
            if not missing:
                continue
            labels, gap = missing
            values = [float(row[value_column]) for row in rows if row.get(value_column) is not None]
        else:
            _, pairs, _, _, label = series
            labels = [label(i) for i, _ in pairs]
            values = [float(value) for _, value in pairs]
        unit, value_kind = measure_kind(result, value_column, values)
        basis = 'registered_metric' if contract and value_column == 'value' else 'published_field'
        if reference_only and (gap or not measure_label or basis != 'published_field'
                               or not re.fullmatch(r'(?:人民币)?(?:万|亿)?元', unit or '')):
            continue
        found.append({'time_column': time_column, 'value_column': value_column, 'observed_years': labels,
                      'history_start': labels[0], 'origin': labels[-1], 'history_count': len(labels),
                      'unit': unit, 'value_kind': value_kind, 'measure_label': measure_label,
                      'measure_label_basis': basis, 'measure_role': role,
                      **({'reference_only': True} if reference_only else {}), **gap})
    found = [candidate for candidate in found if not candidate.get('reference_only')] or found
    if len(found) != 1:
        return None
    return {'kind': 'annual_closed_years', 'schema_version': 1, 'state': 'checked',
            'result_ref': result['result_ref'], 'source_id': source['id'], 'request_ref': request['user_ref'],
            'publication': {key: publication[key] for key in ('id', 'version')}, 'connection_revision': revision,
            'target_period': target_period, **found[0]}


def _direct_count(node):
    from sqlglot import exp

    while isinstance(node,exp.Paren) or isinstance(node,exp.Filter) and isinstance(node.this,exp.Count):
        node=node.this
    return isinstance(node,exp.Count)


def measure_kind(result,value_column,values):
    """Resolve a display unit and prove discreteness from retained metadata."""
    from sqlglot import exp

    contract=result.get('metric_contract') or {}
    spec=contract.get('semantic_spec') or {}
    evidence=(result.get('column_unit_evidence') or {}).get(value_column) or {}
    unknown_units=(None,'','原字段单位','各指标单位','见单位','混合')
    unit=contract.get('unit') if value_column=='value' else None
    if unit in unknown_units:
        unit=evidence.get('unit')
    if unit in unknown_units:
        unit=None
    count_units = {'家', '户', '个', '件', '条', '宗', '笔', '人', '项', '次', '张', '份', '本', '台', '套'}
    count=any(value_column=='value' and spec.get('formula')==o.get('id')
              and (o.get('aggregation') in ('count','distinct')
                   or o.get('aggregation') == 'snapshot_sum' and unit in count_units) for o in spec.get('operands') or [])
    if not count and (result.get('receipt') or {}).get('read_only') is True:
        tree=_read_sql(result)
        columns=result['columns']
        if (isinstance(tree,exp.Select) and tree.args.get('from') and len(tree.expressions)==len(columns)
                and not any(item.is_star for item in tree.expressions)):
            count=_direct_count(tree.expressions[columns.index(value_column)].unalias())
    count=count and all(v>=0 and v.is_integer() for v in values)
    return unit,'nonnegative_discrete_count' if count else 'numeric'


def display_values(output,value_kind):
    """Round only published counts; preserve the computed fit and backtest."""
    predicted=output['predicted'];intervals=output['interval_95']
    if value_kind!='nonnegative_discrete_count':
        return {'predicted':predicted,'interval_95':intervals}
    require(all(hi>=0 for _,hi in intervals),'预测区间完全落在非负计数的可行范围之外，当前模型不能给出可用结果。'
            '可改用自动选择（forecast_model=auto）尝试其他可行模型。')
    def at_integer(value):
        nearest = round(value)
        return nearest if abs(value-nearest) <= 4 * math.ulp(1.0) * max(1, abs(value)) else value
    return {'predicted':[max(0,math.floor(v+.5)) for v in predicted],
            'interval_95':[[max(0,math.floor(at_integer(lo))),math.ceil(at_integer(hi))] for lo,hi in intervals]}


def forecast_training_rows(result,args,host,source):
    """Training rows for a forecast: the checked wider-history prefix is trimmed.

    Relative windows are otherwise count-verified, not date-aligned, so only a
    window that actually declared available_start may trim leading rows.
    Trailing rows (e.g. an open month) stay and must still pass verify_history.
    """
    rows=result.get('rows') or []
    if source and args.time_column and result.get('sql'):
        from natural_data.monthly_sql_scope import bind as _monthly_bind

        try:
            window=_monthly_bind(host,result['sql'],source)
        except ValueError:
            window=None
        if window and window.get('kind')=='forecast_history' and window.get('available_start'):
            first=window['requested_periods'][0]
            if any(str(row.get(args.time_column))<first for row in rows):
                rows=[row for row in rows if str(row.get(args.time_column))>=first]
    return rows


def bind_target(args,host,*,result=None,source=None):
    text=(host or {}).get('user_text','')
    mentions=target_mentions(host)
    targets={value for value,_ in mentions}
    require(len(targets)<=1,'同一预测请求包含多个目标期；请明确单一目标后再计算。')
    options=args.options
    if targets:
        target=next(iter(targets))
        grain='year' if len(target)==4 else 'month'
        metric = (result or {}).get('metric_contract') or {}
        spec = metric.get('semantic_spec') or {}
        operands = spec.get('operands') or []
        window = relative_forecast_horizon(host) if grain == 'month' else None
        if (window and window['end'] == target and source and result
                and _same_publication(result, source) and (result.get('receipt') or {}).get('read_only') is True
                and operands and all(item.get('aggregation') == 'snapshot_sum' for item in operands)
                and args.time_column in (None, 'period') and args.columns in ([], ['value'])
                and set(result.get('columns') or ()) == {'period', 'value'}):
            # The relative window comes from the user's calendar. A planner's
            # guess at an absolute month or step count cannot alter it.
            options = options.model_copy(update={'forecast_until': target, 'forecast_grain': 'month', 'forecast_steps': 12})
            args = args.model_copy(update={'options': options, 'time_column': 'period', 'columns': ['value']})
        require(not options.forecast_until or options.forecast_until==target,'预测目标与用户原问不一致。')
        require(options.forecast_grain in ('auto',grain),'用户要求'+('年度' if grain=='year' else '月度')+'预测，不能使用其他粒度。')
        options=options.model_copy(update={'forecast_until':target,'forecast_grain':grain})
        args=args.model_copy(update={'options':options})
    elif text.strip():
        require(False,'原问没有可确定的单一预测目标期，请明确年份或月份后再计算。')
    if result is not None and result.get('rows'):
        raw_cols=list(args.columns or ['value'])
        if args.time_column and args.time_column in raw_cols:
            non_time_cols=[c for c in raw_cols if c!=args.time_column]
            require(len(non_time_cols)==1,
                    f'请在 columns 中填写 1 个数值字段（不要把时间列 {args.time_column} 放入 columns）。')
            args=args.model_copy(update={'columns':non_time_cols})
        value_column=(args.columns or ['value'])[0]
        if source and (requested_role := _bound_forecast_amount_role(result, host, mentions)):
            label = published_measure_label(result, source, value_column)
            role = _forecast_amount_role(result, label, value_column)
            require(not _forecast_role_conflict(requested_role, role),
                    '年度预测需要与原问一致的已核实金额序列。项目预算不能代替实际投入。')
        if source and (tree:=_read_sql(result,source)) is not None:
            from natural_data.approval_event_scope import approval_date_evidence
            from natural_data.monthly_sql_scope import require_calendar_observations
            from natural_data.vendor.dialect import sqlglot_name

            require_calendar_observations(tree,source)
            if role := approval_date_evidence(tree.sql(dialect=sqlglot_name(source['dialect'])), source, host):
                require(args.time_column == role['month_column'], '预测必须使用与本问日期口径一致的历史月份字段。')
        training=calendar_series(result['rows'],args.time_column,value_column,options.forecast_grain)
        training, reuse = _reuse_monthly_history(result, source, training, host, mentions)
        if not reuse:
            # Checked wider-history prefix: trim leading rows only when the
            # trailing-window reuse did not already select the requested tail.
            # Reuse binds row_count to the full series, so it must see untrimmed
            # rows; the two paths never both apply to the same training window.
            rows=forecast_training_rows(result,args,host,source)
            if len(rows)!=len(result.get('rows') or []):
                training=calendar_series(rows,args.time_column,value_column,options.forecast_grain)
        if training[0]=='year' and options.forecast_until and re.fullmatch(r'\d{4}',options.forecast_until):
            _,pairs,step,index,_=training
            if index(options.forecast_until)-pairs[-1][0]==2*step:
                # A two-step annual horizon is only the open-year skip when the
                # intermediate year is still open on the live client calendar.
                require(pairs[-1][0]+1==_now(host).year,
                        '年度预测只支持历史末年后的下一年；请补齐相邻完整历史年度。')
        verify_history(result,source,args.time_column,value_column,training,host,mentions)
        if reuse:
            result['_forecast_training'] = (training, reuse)
    return args
