"""Conservative business intent checks over host-supplied user messages.

Not a general language parser. Ambiguity stays with the Harness clarification UI.
Host evidence is outside model tool schemas and cannot be declared by the model.
"""
import re
import json
from .temporal_scope import validate_confirmed_periods, confirmed_scope

def validate_grade_confirmation(grade,host,required=False):
 text=host.get('confirmation','')
 choices=set(re.findall(r'([甲乙])级',text))
 if len(choices)!=1 or re.search(r'不选|不要|不确认|不确定|或者|还是|可能',text):
  if required:raise ValueError('NEEDS_CLARIFICATION: 等级条件冲突，请通过原生提问确认甲级或乙级。')
  return
 expected=next(iter(choices))+'级'
 if grade!=expected:raise ValueError('INTENT_SCOPE_MISMATCH: 用户已确认'+expected+'；请修订任务和计划，不要重复询问相同条件。')

_ISSUANCE = r'(?:今年|本年|当年|该年)(?:度)?(?:所有|全部|实际)?发证(?:的)?单位(?:数量|数)?'
_FIRST_TOPIC = r'首次(?:取得|获证|发证)|初次获证|新单位|新企业|(?:今年|本年|当年|该年|20\d{2}年)(?:度)?(?:所有|全部|实际)?发证'
_SCOPE_QUESTION = r'^(?:请问|请)?(?:(?:统计|采用|选择|确认|用|按|哪个|哪种|什么|哪一|的)*口径)[？?：:。！!\s]*$'


def _issuance_choice(host):
    """Native answers retain their question; do not treat a concatenated transcript as consent."""
    records = host.get('clarifications') or []
    if not records:  # Legacy hosts: only a single unambiguous standalone answer.
        lines = (host.get('confirmation') or '').strip().splitlines()
        if len(lines) != 1: return False
        records = [{'question':'口径？','answer':lines[0]}]
    for record in reversed(records):
        q = re.sub('[“”‘’「」『』\"\']', '', record.get('question','')).strip()
        if not (re.search(_FIRST_TOPIC,q) or re.fullmatch(_SCOPE_QUESTION,q)): continue
        answer = re.sub('[“”‘’「」『』\"\']', '', record.get('answer','')).strip()
        if not re.search(_FIRST_TOPIC,q) and not re.search(_FIRST_TOPIC,answer): continue
        # A question about geography or renewals can repeat the selected metric's name.
        # Its yes/no answer only changes that filter, not the earlier choice of metric.
        ancillary = re.search(r'(?:是否|要不要|需不需要|要|是否需要)?(?:包[含括]|含|排除|剔除|计入|算入|包含在内)|(?:算|计|含)不(?:算|计|含)|哪个地市', q)
        if ancillary and not re.search(r'是否(?:改为|改)?按|还是|哪(?:一|种)口径',q) and not re.search(_FIRST_TOPIC,answer):
            continue
        # Closed affirmative clause; a separate exclusion (e.g. 不要算换证的) is not a veto.
        # A native option often spells out the requested calendar year and adds
        # an explanatory parenthesis. Never treat a different year as consent.
        target_years = set(re.findall(r'20\d{2}', host.get('user_text','')))
        selected_years = set(re.findall(r'20\d{2}', answer))
        if selected_years and selected_years != target_years: return False
        normalized = re.sub(r'\s+', '', answer)
        if selected_years:
            normalized = re.sub(r'20\d{2}年(?:当年|本年|该年)?', '当年', normalized)
        choice = r'(?:(?:同意|可以|请|确认|就|改为|改|选择|采用|按照|按|用|统计)\s*)*' + _ISSUANCE + r'(?:来)?(?:算|计算|口径|统计|为准)*(?:即可|就行|吧)?'
        clauses = [x.strip() for x in re.split(r'[，,；;。！!（）()\n]',normalized) if x.strip()]
        if any(re.search(r'还是|或者|不确定|[?？]|(?:不|别|并非|拒绝).{0,8}'+_ISSUANCE, x) for x in clauses): return False
        if any(re.fullmatch(choice,x) for x in clauses):
            # Multiple incompatible selections, including a later correction, do not grant consent.
            return answer if not any(re.search(r'首次|初次',x) and not re.match(r'(不要|不按|不算|不统计|不是|不区分|未区分)',x) for x in clauses) else False
        # “是” only confirms a single explicit proposal, never either half of a choice.
        if re.fullmatch(r'(?:确认|是|可以|同意|是的|对|好的)[。！!]?',answer):
            return q if (re.search(r'(?:是否|可以|能否)(?:改为|改)?按'+_ISSUANCE,q) and not re.search(r'或|还是|不按|不要|不能|不采用|首次.{0,6}[？?]',q)) else False
        if re.search(r'首次|初次|发证|口径|还没定|不确定|拒绝',answer) or re.fullmatch(r'(?:否|不|不是|不行|不要|不同意|不可以)[。！!]?',answer):
            return False
        # A follow-up asking about a different constraint may repeat the original topic.
        if not re.search(r'省外|省内|地区|区域|范围|换证|地市',q+' '+answer): return False
    return False


def _renewals_excluded(host, choice):
    exclude = r'(?:不要(?:算|含|统计)?|排除|剔除|不含|不算|不统计|不包[括含])(?:办理|了|的)?换证'
    for record in reversed(host.get('clarifications') or []):
        q, answer = record.get('question',''), record.get('answer','').strip().rstrip('。！!')
        if not re.search(_FIRST_TOPIC,q) and '换证' not in q and not re.fullmatch(_SCOPE_QUESTION,q): continue
        if re.search(exclude,answer): return True
        if re.search(r'(?:包括|包含|计入)换证',answer): return False
        if '换证' not in q or re.search('还是|或者',q): continue
        if answer in ('是','是的','对','同意','确认','不','否','不是'):
            yes = answer in ('是','是的','对','同意','确认')
            if re.search(r'排除|剔除|不计入|不包括|不包含',q): return yes
            if re.search(r'包括|包含|计入',q): return not yes
    return bool(re.search(exclude,choice))


def _first_field(column):
    # Descriptions can explicitly say “不能证明首次获证”; a mention is not a declaration.
    label = str(column.get('label') or '').strip()
    if re.fullmatch(r'(?:单位|资质)?(?:首次|初次)(?:取得(?:测绘)?资质|获证|发证|领证)(?:的)?(?:日期|时间|年月|年份)', label):
        return 'date'
    if re.fullmatch(r'(?:是否(?:首次|初次)(?:获证|领证|发证)?|(?:首次|初次)(?:获证|发证)(?:标志|标识))', label):
        return 'flag'


def _first_evidence(tree, tables):
    """Require a restrictive first-issue predicate on the actual counted table.

    More complex query shapes need confirmation. A column in ORDER BY, an unrelated
    subquery, a negation or an OR branch is not evidence about the population counted.
    """
    from sqlglot import exp
    if not isinstance(tree, exp.Select) or len(tables) != 1: return False
    refs = list(tree.find_all(exp.Table))
    if len(refs) != 1 or sum(1 for _ in tree.find_all(exp.Select)) != 1: return False
    fields = {c['name'].casefold():c for c in tables[0].get('columns',[]) if _first_field(c)}
    where = tree.args.get('where')
    if not fields or not where: return False
    def constrained(n):
        if isinstance(n, exp.Paren): return constrained(n.this)
        if isinstance(n, exp.And): return constrained(n.this) or constrained(n.expression)
        if isinstance(n, exp.Or): return constrained(n.this) and constrained(n.expression)
        if not isinstance(n.this, exp.Column) or (n.this.table and n.this.table != refs[0].alias_or_name): return False
        c = fields.get(n.this.name.casefold())
        if not c: return False
        def date_value(v):
            if isinstance(v,exp.Cast) and v.to.sql()=='DATE': v=v.this
            if isinstance(v,(exp.Placeholder,exp.Parameter)): return True
            return isinstance(v,exp.Literal) and bool(re.fullmatch(r'(?:19|20)\d{2}(?:-?\d{2}){0,2}',str(v.this)))
        date_field = _first_field(c) == 'date'
        if date_field:
            if isinstance(n, exp.Between): return date_value(n.args['low']) and date_value(n.args['high'])
            return type(n) in (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE) and date_value(n.expression)
        if type(n) not in (exp.EQ, exp.Is): return False
        v=n.expression
        return (isinstance(v,exp.Boolean) and v.this is True) or (isinstance(v,exp.Literal) and v.this in ('1','是','首次','初次'))
    return constrained(where.this)


def _registered_first_period(metric, tables):
    """Generated metrics use CTEs. Inspect the compiler's validated operands, not a CTE name."""
    if not metric or metric.get('time_mode') != 'period': return False
    first_dates = {(t['name'], c['name']) for t in tables for c in t.get('columns', [])
                   if _first_field(c) == 'date'}
    ops = metric.get('operands') or []
    return bool(ops and all((o['table'], o.get('time_column')) in first_dates for o in ops))


def first_issuance_notice(source, host):
    """Describe missing declared evidence once, respecting an explicit refusal to substitute."""
    host=host or {};text=host.get('user_text','')
    if not re.search(r'首次.{0,8}(获证|发证)|新单位.{0,12}(资质|证书)',text) or _issuance_choice(host):return
    if any(_first_field(c) for t in source.get('tables',[]) for c in t.get('columns',[])):return
    relevant=[r.get('answer','') for r in host.get('clarifications',[]) if re.search(_FIRST_TOPIC,r.get('question','')) or re.fullmatch(_SCOPE_QUESTION,r.get('question','').strip())]
    refused=any(re.search(r'(?:不要|不采用|不接受|不改|拒绝).{0,15}(?:发证|替代)|必须.{0,8}首次|不能.{0,12}(?:首次|识别).{0,12}(?:说明|缺)',s) for s in [text,*relevant])
    return {'code':'FIRST_EVIDENCE_MISSING','action':'explain' if refused else 'clarify',
            'message':'当前授权目录未声明首次获证日期或可靠标志，也未保证完整历史。普通发证日期可能包含换证、延续；最早可见记录不能证明首次。'+
            ('用户已要求不足时说明或拒绝替代，请直接说明需要首次获证事件、可靠标志或经确认的完整历史，不再询问是否改按当年发证，不给首次获证数量。' if refused else '请确认是否接受当年发证单位数；不接受时说明上述数据缺项，不继续试算首次数量。')}


def _excludes_renewals(tree, tables, metric=None):
    """Prove this particular exclusion from a declared event-type field (and its raw codes)."""
    from sqlglot import exp
    def restricts(table, col, op, values):
        c = next((c for c in table.get('columns',[]) if c['name'].casefold()==col.casefold()),{})
        label = str(c.get('label') or '')
        if not re.fullmatch(r'(?:发证|业务|办件|证书|申请)(?:业务)?(?:类型|类别|事由|事项)',label): return False
        mapping = {str(x['value']):str(x['label']) for x in c.get('business_values') or [] if 'value' in x and 'label' in x}
        excluded = {k for k,v in mapping.items() if '换证' in v} if mapping else {'换证'}
        vals = set(map(str,values))
        if op in ('ne','not_in'): return bool(excluded and excluded <= vals)
        return op in ('eq','in') and bool(mapping and vals and vals <= mapping.keys() and not any('换证' in mapping[v] for v in vals))
    if metric:
        by_name={t['name']:t for t in tables}
        return bool(metric['operands']) and all(any(restricts(by_name.get(o['table'],{}),f['column'],f['op'],f['values'])
                              for f in o.get('filters',[])) for o in metric['operands'])
    refs=list(tree.find_all(exp.Table))
    if len(tables)!=1 or len(refs)!=1 or sum(1 for _ in tree.find_all(exp.Select))!=1: return False
    def predicate(n):
        if isinstance(n,exp.Paren): return predicate(n.this)
        if isinstance(n,exp.And): return predicate(n.this) or predicate(n.expression)
        if isinstance(n,exp.Or): return predicate(n.this) and predicate(n.expression)
        op='not_in' if isinstance(n,exp.Not) and isinstance(n.this,exp.In) else None
        if op: n=n.this
        else: op={exp.In:'in',exp.NEQ:'ne',exp.EQ:'eq'}.get(type(n))
        if not op or not isinstance(n.this,exp.Column) or (n.this.table and n.this.table!=refs[0].alias_or_name): return False
        vals=n.expressions if isinstance(n,exp.In) else [n.expression]
        return bool(vals and all(isinstance(v,exp.Literal) for v in vals) and restricts(tables[0],n.this.name,op,[v.this for v in vals]))
    where=tree.args.get('where')
    return bool(where and predicate(where.this))


def validate_acquisition_query(sql, source, host, *, compiled_metric=None, periods=None, parameters=None):
 """Receiving a certificate is not submitting an application; use catalog metadata."""
 import sqlglot
 from sqlglot import exp
 text=(host or {}).get('user_text','')
 annual_issuance=bool(re.search(r'(?:当年|本年|今年|该年|20\d{2}年).{0,12}发证.{0,4}单位',text)
                      and not re.search(r'首次|新单位|新企业|月|季度|半年|分组|各市|按市|名单|专业',text))
 if not annual_issuance and not re.search(r'(拿到|获得|取得|获发|获证).{0,12}(资质|证书)|首次.{0,8}(获证|发证)',text):return
 from .vendor.dialect import sqlglot_name
 try:
  statements=sqlglot.parse(sql,read=sqlglot_name(source.get('dialect')))
  if len(statements)!=1 or not isinstance(statements[0],exp.Query):raise ValueError()
  tree=statements[0]
 except (ValueError,sqlglot.errors.SqlglotError):
  raise ValueError('EVENT_SQL_UNVERIFIED: 无法解析获证查询并核对数据口径，请按当前数据源方言修正查询。') from None
 used={t.name.casefold() for t in tree.find_all(exp.Table)}
 tables=[t for t in source.get('tables',[]) if t['name'].split('.')[-1].casefold() in used]
 # Aggregate-only application data cannot establish whether or when a unit received a certificate.
 if tables and all(re.search(r'申请|受理',t.get('label') or '') for t in tables) and any(tree.find_all(exp.AggFunc)):
  raise ValueError('EVENT_SCOPE_MISMATCH: 用户问取得资质/获证的单位，申请或受理记录不能证明已获证。请查询实际发证/批准数据，按单位去重；“新单位”还需首次取得的依据。若目录无此证据，明确说明无法确认，不得用申请件数或申请单位数代替。')
 if annual_issuance:
  from .issuance_scope import validate_issuance_scope
  validate_issuance_scope(tree,tables,host,compiled_metric,periods,parameters)
  if _renewals_excluded(host,text) and not _excludes_renewals(tree,tables,compiled_metric):
   raise ValueError('EVENT_FILTER_REQUIRED: 用户要求排除换证，请使用已声明的发证类型字段和取值筛选；缺少依据时直接说明。')
 if re.search(r'新单位|新企业|首次',text):
  choice=_issuance_choice(host or {})
  if choice and _renewals_excluded(host,choice) and not _excludes_renewals(tree,tables,compiled_metric):
   raise ValueError('EVENT_FILTER_REQUIRED: 已确认按本年发证单位统计，无须再问统计口径；用户同时要求排除换证，当前查询未能证明这一点。请根据目录中的发证业务类型和取值筛选；无区分依据则说明不能给出排除换证后的数量。')
  if not (_first_evidence(tree,tables) or _registered_first_period(compiled_metric,tables) or choice):
   notice=first_issuance_notice(source,host)
   if notice and notice['action']=='explain':
    raise ValueError(notice['code']+': '+notice['message'])
   raise ValueError('NEEDS_CLARIFICATION: 现有发证日期不能证明首次获证（可能含延续、换证），目录也未保证历史完整。MIN、NOT EXISTS 或排除库内旧记录只能得到最早可见记录，不能替代首次获证证据。请通过原生提问确认是否改按“本年发证单位数”统计；若必须统计首次取得的新单位，需明确的首次获证字段或已确认的完整历史，目前不能给出该数量。')
  if choice:
   from .issuance_scope import validate_issuance_scope
   validate_issuance_scope(tree,tables,host,compiled_metric,periods,parameters)

def validate_conditional_delivery(args,task,host,engine=None,ctx=None):
 """Keep an explicit conditional drill-down out of the total-only else branch.

 Unsupported language is left to clarification; these checks do not claim to
 parse arbitrary business prose. Use host text, never the model's rewritten question.
 """
 text=host.get('user_text','')
 parts=re.split(r'否则|不然',text,maxsplit=1)
 if len(parts)!=2 or not re.search(r'如果|若|如高于|如低于',parts[0]):return
 drill=r'拆分|分类|分组|按.+(?:类|市|专业|等级)|明细|名单'
 guarded=bool(re.search(drill,parts[0]))
 total_only=bool(re.search(r'只(?:需|要)?(?:列|给|看|展示|返回)|仅(?:列|给|看|展示|返回)',parts[1])) and not re.search(drill,parts[1])
 is_drill=args.group_by!='none' or args.detail or args.category is not None
 if not (guarded and total_only and is_drill):return
 try:dep=json.loads(task['fields'].get('branch_dependency','{}'))
 except (TypeError,ValueError):dep={}
 if not isinstance(dep,dict) or dep.get('kind')!='claim' or dep.get('expected') is not True:
  # 0.6.3: without a hand-written binding, use the session's own branch judgement.
  if engine is not None and ctx is not None:
   from .task_contract import session_branch_claim
   judged=session_branch_claim(engine,ctx)
   if judged and judged['matched'] is True:return
   if judged and judged['matched'] is False:
    raise ValueError('BRANCH_NOT_TAKEN: 条件判断结果为不成立，按用户要求只回答两期总量和差额，不要拆分。')
  raise ValueError('BRANCH_CONTRACT_REQUIRED: 这是"条件成立才拆分"的问题，拆分前要先判断条件：先查两期总量，再用 analysis_compute(operation=branch_compare，填 baseline_fact_id、current_fact_id，threshold=0，comparison=">") 判断；判断成立后直接重新 data_plan 拆分即可。条件不成立时不要拆分，只答两期总量和差额。')

def requested_metrics(text):
 clauses=[x for x in re.split(r'[。？?；;\n]+',text) if x.strip()]
 fiscal=[x for x in clauses if '财政' in x and ('基础测绘' in x or '投入' in x)]
 total=[x for x in clauses if '基础测绘' in x and '投入' in x and ('财政' not in x or '全部' in x)]
 return fiscal,total

def validate_host_intent(args,host):
 text=host.get('user_text','');confirmed=host.get('confirmation','')
 if not text:return
 # A broad project request is not consent to choose all project metrics. Keep
 # independent explicit questions executable while this domain asks for scope.
 project_metrics={'project_investment','fiscal_investment','project_execution','project_acceptance','project_work_completion','project_work_weighted'}
 if args.metric in project_metrics:
  project_clauses=[c for c in re.split(r'[。？?；;\n]|另外',text) if '基础测绘' in c and re.search(r'情况|分析|看看|了解',c)]
  explicit=r'投入|投资|资金|预算|支出|验收|完成率|完成量|全部指标|所有指标|各项指标'
  if project_clauses and not any(re.search(explicit,c) for c in project_clauses) and not re.search(explicit,confirmed):
   raise ValueError('NEEDS_CLARIFICATION: 基础测绘项目情况尚未明确关注指标，请先交付其他已明确问题，再通过原生提问确认投入、资金执行或验收等分析范围；不得自行选择多个指标并宣布全部完成。')
 validate_explicit_annual_period(args,text,confirmed)
 years=set(re.findall(r'(20\d{2})年',text))
 if len(years)==1 and '环比' in text and '月' in text and args.metric=='tianditu_services':
  year=next(iter(years));periods=set(args.periods)
  if {f'{year}-{month:02}' for month in range(1,13)} <= periods and f'{int(year)-1}-12' not in periods:
   raise ValueError('COMPARISON_BASELINE_REQUIRED: 全年逐月环比需要上一年12月作为1月基期；请在同一指标任务中补齐基期后执行。若来源无该基期，明确报告1月无法核验，不能宣称全年已完整分析。')

 if '分布' in text and args.metric=='professional_units' and not re.search(r'地市|城市|地区|区域|按专业|各专业|按等级|按资质等级|按数量|直方|频数|频率|分位|离散|统计分布',text+' '+confirmed):
  raise ValueError('NEEDS_CLARIFICATION: “分布”可能指地域、专业或统计分布；请先确认分布维度，不能默认选择地市。')
 fiscal,total=requested_metrics(text)
 if args.metric=='fiscal_investment' and fiscal and total and len(args.periods)>1 and not any(re.search('同比|去年|上一年|比较|对比|变化',s) for s in fiscal):
  raise ValueError('INTENT_SCOPE_MISMATCH: 财政金额与全部投入比较是独立要求；当前多期比较应绑定project_investment，不可沿用财政筛选。')
 conflict=re.search(r'只(?:看|统计|要|限)[^。；;\n]*?([甲乙])级[^。；;\n]*?([甲乙])级',text)
 if conflict and conflict[1]!=conflict[2]:
  validate_grade_confirmation(getattr(args,'grade',None),host,required=True)
 from .temporal_scope import validate_month_request
 validate_month_request(host, getattr(args, 'periods', ()))

def validate_explicit_annual_period(args,text,confirmed):
 """A named annual flow must not inherit a preceding task's December filter.

 Restrict the check to a single explicit year in the metric's own clause.
 Month/quarter/range expressions and unresolved comparisons remain outside this
 conservative rule. A native confirmation may explicitly refine the period.
 """
 aliases={
  'inspection_pass_rate':r'抽检合格率|监督检查合格率',
  'confidential_approvals':r'涉密测绘成果.*(?:审批|办理)',
  'map_reviews':r'地图审核',
  'tianditu_services':r'天地图.*服务次数',
  'on_time_rate':r'按时办结率',
  'approval_rate':r'批准率',
  'rejection_count':r'不予批准|不批准',
 }
 alias=aliases.get(args.metric)
 if not alias:return
 for clause in re.split(r'[。；;\n]|另外|然后|再用',text):
  years=set(re.findall(r'(20\d{2})年',clause))
  if len(years)!=1 or not re.search(alias,clause):continue
  if re.search(r'月|季度|半年|截至|截止|同比|去年|上年|前年|以来|至|到',clause):continue
  year=next(iter(years))
  from .temporal_scope import month_scope
  scope=month_scope(confirmed)
  if scope and {p[:4] for p in scope}=={year}:
   validate_confirmed_periods(args.periods,{'confirmation':confirmed,'user_text':text})
   continue
  periods=set(args.periods)
  if periods=={year}:continue
  # 0.6.3: the clause names no month, so twelve monthly rows do not answer it; a
  # yearly rate is recomputed over the whole year, not averaged from the months.
  if periods=={f'{year}-{m:02}' for m in range(1,13)}:
   raise ValueError('INTENT_PERIOD_MISMATCH: 用户问的是'+year+'年全年的值，不是各月。请把任务的 periods 改为 ["'+year+'"]（按全年重新计算，比率不是12个月的平均），再生成计划。')
  raise ValueError('INTENT_PERIOD_MISMATCH: 用户明确要求该期间发生量指标的'+year+'年；不能继承旧任务的单月条件。请把 periods 改为 ["'+year+'"] 后生成计划；若用户另有期间要求，先取得原生确认。')
