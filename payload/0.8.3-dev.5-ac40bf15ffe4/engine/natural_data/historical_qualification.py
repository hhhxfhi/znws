"""Historical certificate and professional-unit snapshots."""
import calendar
import re
from datetime import date, timedelta
import sqlglot
from sqlglot import exp
from .semantic import table
from .pack_quality import indicator_table, fields

PROFESSIONS = ('大地测量','测绘航空摄影','摄影测量与遥感','地理信息系统工程','工程测量','海洋测绘','界线与不动产测绘','地图编制','导航电子地图制作','互联网地图服务')
CITIES = re.compile('广州|深圳|珠海|汕头|佛山|韶关|湛江|肇庆|江门|茂名|惠州|梅州|汕尾|河源|阳江|清远|东莞|中山|潮州|揭阳|云浮')
AS_OF = re.compile(r'(?<!\d)(20\d{2})\s*(?:年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*[日号])?|[-/](\d{1,2})(?:[-/](\d{1,2}))?)(?!\d)')
TOP_COUNT = re.compile(r'(?:排名)?前\s*(10|[1-9]|[一二两三四五六七八九十])\s*(?:个|项|位|名)?\s*(?:的)?\s*专业')
_LEGACY_ORIGIN = re.compile(r'pack:chdl-2026@1(?:\.\d+){0,2}')
_HISTORY_COLUMNS = ('certificate_key', 'issued_column', 'expiry_column', 'cancel_key',
                    'cancelled_column', 'cancel_date_column', 'unit_key', 'province_column')
_HISTORY_FIELDS = set(_HISTORY_COLUMNS) | {'subject_name', 'cancelled_value', 'province_value'}
_LEGACY_SNAPSHOT_ROLES = dict(zip(_HISTORY_COLUMNS, ('zsbh', 'fzsj', 'yxq', 'zsbh', 'sfzx', 'slrq', 'dwid', 'szqy')))


def historical_rules_enabled(source, spec):
    """Legacy compatibility does not grant a newer workbook a historical dataset."""
    return spec['custom'] or any(_LEGACY_ORIGIN.fullmatch(str(t.get('origin', '')))
                                 for t in source.get('terms', []))


def validate_qualification_config(raw):
    """Validate administrator-owned metadata at the connection save boundary."""
    if raw is None:
        return None
    scalar = {'cert_table', 'cancel_table', 'grade_column', 'profession_column', 'profession_separator'} | _HISTORY_FIELDS
    allowed = {'professions', 'cities', 'grades', 'guarded_tables', 'guarded_metrics'} | scalar
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError('HISTORICAL_CONFIG: 历史资质配置字段无效。')
    result = {}
    for key, value in raw.items():
        items = [value] if key in scalar else value
        if not isinstance(items, list) or not 1 <= len(items) <= 100 or any(
                not isinstance(x, str) or not x.strip() or len(x) > 200 or any(ord(c) < 32 for c in x) for x in items):
            raise ValueError('HISTORICAL_CONFIG: 表名须为非空文本，词典与保护范围须为非空文本数组。')
        cleaned = list(dict.fromkeys(x.strip() for x in items))
        if key.endswith(('_column', '_key')) and not re.fullmatch(r'[a-z_][a-z0-9_]*', cleaned[0]):
            raise ValueError('HISTORICAL_CONFIG: 字段名须使用授权目录中的小写物理标识。')
        if key == 'profession_separator' and cleaned[0] not in (',', '、', ';', '；', '|'):
            raise ValueError('HISTORICAL_CONFIG: 专业分隔符须为逗号、顿号、分号或竖线。')
        if key == 'cities' and any(not x.rstrip('市') for x in cleaned):
            raise ValueError('HISTORICAL_CONFIG: 城市名称无效。')
        result[key] = cleaned[0] if key in scalar else cleaned
    return result


def qualification_spec(source=None):
    """Return metadata-driven historical qualification rules for a source, falling back to surveying defaults."""
    raw = (source or {}).get('historical_qualification')
    cfg = raw if isinstance(raw, dict) else {}
    professions = tuple(cfg.get('professions') or PROFESSIONS)
    city_list = cfg.get('cities')
    cities_re = re.compile('|'.join(re.escape(c.rstrip('市')) for c in city_list)) if city_list else CITIES
    grades = tuple(cfg.get('grades') or ('甲级', '乙级'))
    return {
        'custom': bool(cfg),
        'professions': professions,
        'cities_re': cities_re,
        'grades': grades,
        'cert_table': cfg.get('cert_table', 'proj_zzinfo'),
        'cancel_table': cfg.get('cancel_table', 'proj_zzdwsq'),
        'grade_column': cfg.get('grade_column', 'hzqzzdj'),
        'profession_column': cfg.get('profession_column', 'zylb'),
        'profession_separator': cfg.get('profession_separator'),
        'guarded_tables': tuple(dict.fromkeys(str(t).split('.')[-1].lower() for t in [
            *(cfg.get('guarded_tables') or (() if cfg else ('xxzy_qschzzdwb', 'ch_zbzb'))),
            cfg.get('cert_table', 'proj_zzinfo'), cfg.get('cancel_table', 'proj_zzdwsq')])),
        'guarded_metrics': tuple(cfg.get('guarded_metrics') or ('qualified_units_current', 'qualified_units_month_end', 'indicator_value_yearly')),
    }


def historical_query_recipe(source, host):
    """Return an optional formal snapshot query bound to the published source."""
    from .historical_snapshot_query import recipe

    spec = qualification_spec(source)
    config = source.get('historical_qualification') or {}
    if _HISTORY_FIELDS <= config.keys() and {'cert_table', 'cancel_table', 'grade_column',
                                            'profession_column', 'grades', 'professions'} <= config.keys():
        return recipe(source, host, spec, config)
    if config:
        return None
    origins = {term.get('origin') for term in source.get('terms', [])
               if _LEGACY_ORIGIN.fullmatch(str(term.get('origin', '')))}
    if origins:
        roles = {**_LEGACY_SNAPSHOT_ROLES, 'subject_name': '测绘资质', 'cancelled_value': '是', 'province_value': '广东省'}
        return recipe(source, host, spec, roles, legacy_origins=origins)



def ranking_request(text, end, spec=None):
    spec=spec or qualification_spec()
    """Bind the requested calculation only after matching the authorized count query."""
    top = TOP_COUNT.search(text)
    if not top or not re.search(r'占比|比例|占.{0,25}总数',text): return
    token = top[1].replace('两','二')
    n = int(token) if token.isdigit() else '一二三四五六七八九十'.index(token)+1
    # Only the grades in the ranking clause are denominators. A comparison
    # clause can request the *other* grade for that same leading profession.
    clause = re.split(r'再查|另查|另外|再比|再给|然后',text,maxsplit=1)[0]
    grades = [g for g in spec['grades'] if g in clause]
    if spec['grades']==('甲级','乙级') and re.search(r'甲[、和与]?乙级|甲乙两级',clause): grades=list(spec['grades'])
    if not grades: return
    return dict(prefix=n,grades=grades,all_grades=list(spec['grades']),professions=list(spec['professions']),cutoff=end.isoformat(),
                all_ten=bool(re.search(r'十个专业|十大专业|10个专业',text)),
                compare=bool(re.search(r'最多|第一|首位',text) and re.search(r'相差|差额|差多少|差距|差值',text) and len(spec['grades'])==2 and all(g in text for g in spec['grades'])))


def ranking_contract(rid, result, request):
    """Derive every published number from this one complete, persisted data result."""
    rows=result['rows']
    if result.get('truncated') or not rows or result['columns']!=['专业','等级','单位数']: return
    professions=request.get('professions') or PROFESSIONS
    groups={g:{} for g in request.get('all_grades') or ('甲级','乙级')}
    for i,row in enumerate(rows):
        grade,profession,value=row['等级'],row['专业'],row['单位数']
        if (grade not in groups or profession not in professions or profession in groups[grade]
                or type(value) not in (int,float) or value<0 or int(value)!=value): return
        groups[grade][profession]=(int(value),i)
    statements=[]
    def add(grade,role,value,unit,description,indices):
        statements.append(dict(statement_id=f'{rid}:rank:{len(statements)}',grade=grade,role=role,value=value,
                               unit=unit,text=description,fact_refs=[f'{rid}:{i}' for i in indices]))
    for grade in request['grades']:
        ranked=sorted(groups[grade].items(),key=lambda x:(-x[1][0],x[0]))
        n=request['prefix']
        if len(ranked)<n or request['all_ten'] and len(ranked)!=len(professions): continue
        total=sum(value for _,(value,_) in ranked)
        if not total: continue
        head=ranked[:n]; subtotal=sum(value for _,(value,_) in head)
        indices=[i for _,(_,i) in ranked]; percent=round(100*subtotal/total,2)
        add(grade,'total',total,'家次',f'{grade}各专业计次合计{total}家次。',indices)
        add(grade,'top_count',subtotal,'家次',f'{grade}前{n}个专业合计{subtotal}家次。',[i for _,(_,i) in head])
        add(grade,'share',percent,'%',f'{grade}前{n}个专业占全部{len(ranked)}个专业计次的比例为{subtotal} ÷ {total} × 100% = {percent:.2f}%。',indices)
        if request['compare']:
            other=next((g for g in groups if g!=grade),None)
            if other is None:continue
            for profession,(first,i) in ranked:
                if first!=ranked[0][1][0]: break
                if profession not in groups[other]: continue
                second,j=groups[other][profession]; gap=abs(first-second)
                comparison=(f'{other}比{grade}多{gap}家' if second>first else f'{grade}比{other}多{gap}家') if gap else '两级数量相同'
                add(grade,'difference',gap,'家',f'{grade}数量居首的{profession}，{grade}{first}家、{other}{second}家，{comparison}。',[i,j])
    if statements:
        return dict(schema_version=1,kind='historical_profession_ranking',result_ref=rid,cutoff=request['cutoff'],unit='家',
                    complete=True,statements=statements,limitations=['同一单位跨专业分别计次。'])


def _refinement(text, spec=None):
    """Only human refinements may inherit a historical population, never a new topic."""
    text=text.strip()
    if '资质' in text and '单位' in text and AS_OF.search(text):
        return False
    if re.search(r'换个问题|另一个问题|不查了|改问|首次|获证|发证|受理|申请|审批|订单|天地图|服务次数|投入|资金',text):
        return False
    if re.fullmatch(r'(?:请)?继续(?:完成)?(?:上一题|上个问题|原问题|未完成的回答|(?:(?:前面|之前|刚才)(?:的)?)?(?:全省统计|统计|回答|查询))?[。！!]?|继续完成第\d+轮未完成的回答。先复用本会话已成功的查询结果和工序回执；除非结果确实缺失或条件改变，不要重放查询。无法确认的解释请明确说明。',text):
        return True
    return bool(re.match(r'^(?:先完成前面(?:的)?全省(?:的)?那道题|再?只看|改(?:为|成|查)|换(?:成|为)|再只查|仅看|那|仍|截止|截至|时间|日期)',text)
                or re.match(r'^(?:以|按|选|选择)\s*20\d{2}(?:年|[-/])',text)
                or re.fullmatch(r'(?:按|选|选择)?\s*20\d{2}(?:年(?:底|末)?)?[。！!]?|(?:按|选|选择)?\s*20\d{2}(?:年\d{1,2}月\d{1,2}[日号]|[-/]\d{1,2}[-/]\d{1,2})[。！!]?',text)
                or re.fullmatch(r'(?:'+(spec or qualification_spec())['cities_re'].pattern+r')市?呢[？?]?',text))


def request_scope(host, spec=None):
    spec = spec or qualification_spec()
    professions = spec['professions']
    cities_re = spec['cities_re']
    current = (host or {}).get('user_text','')
    records=(host or {}).get('recent_requests',[])
    native=re.match(r'^继续完成第(\d+)轮未完成的回答。',current)
    if native:
        index=next((i for i in range(len(records)-1,-1,-1) if records[i].get('turn')==int(native[1])),None)
        if index is None:return [current],None
        current=records[index]['text'];records=records[:index+1]
    def time_answers(items):
        return [r.get('answer','').strip() for r in items if re.search(r'哪年|年份|年度|年底|年末|截止|截至|日期',r.get('question','')) and _refinement(r.get('answer',''),spec)]
    history=[]
    if records:
        for record in records:
            history.extend([record['text'],*time_answers(record.get('clarifications',[]))])
        if native:
            # The target turn's own confirmed date belongs to this retry too.
            current='继续';
    else:history=(host or {}).get('recent_questions',[])
    def province_resume(phrase):
        return bool(re.match(r'^(?:(?:请)?继续(?:完成)?(?:前面|之前)(?:的)?全省统计|先完成前面(?:的)?全省(?:的)?那道题)',phrase))
    cleaned=[]
    for phrase in [*history,current]:
        if province_resume(phrase):
            anchor=next((i for i in range(len(cleaned)-1,-1,-1) if re.search(r'全省|广东省',cleaned[i]) and not province_resume(cleaned[i])),None)
            if anchor is None:return [phrase],None
            cutoff=anchor+1
            # Keep confirmations and refinements of that province request, but
            # discard a later city task when the human explicitly resumes it.
            for previous in cleaned[cutoff:]:
                if cities_re.search(previous) or not _refinement(previous,spec):break
                cutoff+=1
            cleaned=cleaned[:cutoff]
        cleaned.append(phrase)
    history,current=cleaned[:-1],cleaned[-1]
    chain = [current]
    # Only inherit explicit refinements, stopping at the last independent request.
    for prev in reversed(history):
        if not _refinement(chain[0],spec): break
        chain.insert(0,prev)
    # Only a native answer to the cutoff/year question may replace the date.
    # Unrelated confirmations (e.g. city selection) never become a year choice.
    chain.extend(time_answers((host or {}).get('clarifications',[])))
    text = ' '.join(chain)
    if not ('资质' in text and ('单位' in text or '测绘资质' in text and '数量' in text) and ('专业' in text or any(p in text for p in professions))): return
    for phrase in reversed(chain):
        matches = list(AS_OF.finditer(phrase))
        if not matches:
            ending=re.search(r'(?<!\d)(20\d{2})年(?:底|末)',phrase)
            if not ending and re.search(r'年底|年末',text):
                ending=re.fullmatch(r'(?:按|选|选择)?\s*(20\d{2})(?:年)?[。！!]?\s*',phrase)
            if ending and len(re.findall(r'20\d{2}',phrase))==1:
                return chain,date(int(ending[1]),12,31)
            # An explicit but unsupported replacement is not consent to the old date.
            if re.search(r'20\d{2}|今年|去年|前年|上月|本月|今天|昨天|昨日|年底|年末',phrase):
                return chain,None
            continue
        # This query describes one snapshot. Never turn a range or a day into a different cutoff.
        if len(matches) != 1 or re.search(r'(?:[日号月]\s*(?:至|到|和|及|、|[-~～—])\s*\d|月(?:初|中|上旬|中旬|下旬|上半|下半)|月\s*\d+\s*(?:时|点))',phrase):
            return chain, None
        year, cm, cd, im, iday = matches[0].groups()
        y, m, day = int(year), int(cm or im), cd or iday
        if re.match(r'\s*(?:\d|[Tt]\d|以前|之前|之后|以后|以来|起至|至今|至|到\s*\d)',phrase[matches[0].end():]):
            return chain, None
        try:
            return chain, date(y, m, int(day) if day else calendar.monthrange(y,m)[1])
        except ValueError:
            return chain, None
    if re.search(r'截至|截止|年底|年末',text):return chain,None


def scope_fields(chain,end,spec=None):
    """Replay explicit refinements into separate fields; replacements do not union cities."""
    spec=spec or qualification_spec()
    professions=spec['professions']
    cities_re=spec['cities_re']
    all_grades=spec['grades']
    grades=[];pros=[];cities=set();city_requested=False;ranking=None;names=False
    for phrase in chain:
        mentioned=[p for p in professions if p in phrase]
        if re.search(r'十个专业|十大专业|10个专业|所有专业|全部专业',phrase):pros=[]
        elif '取消' in phrase and mentioned:pros=[p for p in pros if p not in mentioned]
        elif mentioned:pros=mentioned
        levels=[g for g in all_grades if g in phrase]
        if levels:grades=levels
        current=ranking_request(phrase,end,spec)
        if current:ranking=current
        elif ranking:
            # A short grade/top-N refinement inherits the other requested calculations.
            clause=re.split(r'再查|另查|再给|差值|相差',phrase,maxsplit=1)[0]
            primary=[g for g in all_grades if g in clause]
            if primary:ranking={**ranking,'grades':primary}
            top=TOP_COUNT.search(phrase)
            if top:
                token=top[1].replace('两','二')
                ranking={**ranking,'prefix':int(token) if token.isdigit() else '一二三四五六七八九十'.index(token)+1}
        found=set(cities_re.findall(phrase))
        if found or '地市' in phrase or '市' in phrase:
            cities=found;city_requested=True
        elif re.search(r'全省|广东省|本省',phrase):
            cities=set();city_requested=False
        if '名单' in phrase:names=True
        elif re.search(r'数量|排名|多少家|单位数',phrase):names=False
    if ranking and ranking['compare']:grades=list(all_grades[:2])
    return dict(grades=grades,professions=pros,cities=cities,city_requested=city_requested,ranking=ranking,names=names)


def check_certificate_dates(source, host, sql, dialect=None, parameters=None):
    """Reject unbounded issue/cancellation/expiry dates in a direct valid-certificate count.

    A certificate's issue date preceding its expiry is not evidence that it
    existed at the requested cutoff. Inspect direct counts and conditional
    counts, and report all date constraint conflicts together with complete
    repair boundaries in one turn to avoid repeated model retries.
    """
    from .snapshot_scope import check_current_snapshot
    check_current_snapshot(source, host, sql, dialect)
    approved = historical_query_recipe(source, host or {})
    if approved and approved.get('share') and (
            parameters or sqlglot.parse_one(sql, read=dialect) != sqlglot.parse_one(approved['sql'], read=dialect)):
        raise ValueError('HISTORICAL_CERTIFICATE_SCOPE: 同一时点的等级证书数与总数必须来自同一历史证书范围。'
                         '使用 data_catalog 返回的历史证书查询，按证号去重并保持日期、地区及统计对象一致。')
    spec = qualification_spec(source)
    if not historical_rules_enabled(source, spec):
        return
    text = ' '.join([*(host or {}).get('recent_questions', []), (host or {}).get('user_text', '')])
    if not re.search(r'有效.{0,20}证书|证书.{0,20}有效', text):
        return
    from .day_bounds import snapshot_day, inclusive_upper_day
    from .temporal_context import temporal_context
    expected = date.fromisoformat(approved['cutoff']) if approved and approved.get('share') else snapshot_day(host)
    today = date.fromisoformat(temporal_context((host or {}).get('client_time_zone', 'UTC'))['current_date'])
    tree = sqlglot.parse_one(sql, read=dialect)
    if parameters:
        bound = {p['name']: p.get('value') for p in parameters or [] if isinstance(p, dict) and 'name' in p}
        def bind(node):
            if isinstance(node, (exp.Placeholder, exp.Parameter)):
                k = str(node.this) if getattr(node, 'this', None) else ''
                if k in bound:
                    return exp.convert(bound[k])
            return node
        tree = tree.transform(bind)

    configuration = source.get('historical_qualification') or {}
    roles = configuration if _HISTORY_FIELDS <= configuration.keys() else _LEGACY_SNAPSHOT_ROLES
    certificate_key = roles['certificate_key']
    issued_field, expiry_field = roles['issued_column'], roles['expiry_column']
    cancel_date_field = roles['cancel_date_column']

    conflicts = []
    cancel_table_name = spec['cancel_table'].split('.')[-1].lower()
    cert_table_name = spec['cert_table'].split('.')[-1].lower()
    next_day_str = (expected + timedelta(days=1)).isoformat() if expected else ''
    expected_str = expected.isoformat() if expected else ''

    # 1. Helper functions for recursive boolean scope inspection
    def parse_date_literal(val):
        while isinstance(val, (exp.Cast, exp.Paren)):
            val = val.this
        if isinstance(val, exp.CurrentDate):
            return today
        if isinstance(val, exp.Literal) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(val.this)):
            try: return date.fromisoformat(str(val.this))
            except ValueError: return None
        return None

    def check_leaf_bound(node, alias, field_name, expected_type, expected_date):
        if not isinstance(node, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
            return False, None

        left, right = node.this, node.expression
        op_type = type(node)

        # Normalize operands so Column is always on the left and value on the right
        if not isinstance(left, exp.Column) and isinstance(right, exp.Column):
            reversed_map = {
                exp.LT: exp.GT,
                exp.LTE: exp.GTE,
                exp.GT: exp.LT,
                exp.GTE: exp.LTE,
            }
            op_type = reversed_map[op_type]
            col, val = right, left
        elif isinstance(left, exp.Column):
            col, val = left, right
        else:
            return False, None

        if col.name.lower() != field_name or (col.table and col.table.lower() != alias):
            return False, None
        # A column-to-column validity condition is neither a cutoff proof nor
        # a contradictory literal bound. Require a separate date boundary.
        if isinstance(val, exp.Column):
            return False, None

        if expected_type == 'upper':
            if op_type in (exp.LT, exp.LTE):
                strict = (op_type == exp.LT)
                end = inclusive_upper_day(val, strict=strict, today=today)
                if end is not None:
                    if expected_date is None or end == expected_date:
                        return True, None
                    return False, f"mismatch:{end}"
                return False, "unbound_or_unrecognized"
            elif op_type in (exp.GT, exp.GTE):
                return False, "inverted_direction"
        elif expected_type == 'lower':
            if op_type in (exp.GTE, exp.GT):
                strict = (op_type == exp.GT)
                d = parse_date_literal(val)
                if d is not None:
                    lower = d + timedelta(days=1) if strict else d
                    if expected_date is None or lower == expected_date:
                        return True, None
                    return False, f"mismatch:{lower}"
                return False, "unbound_or_unrecognized"
            elif op_type in (exp.LTE, exp.LT):
                return False, "inverted_direction"

        return False, None

    def eval_boolean_bound(node, alias, field_name, expected_type, expected_date):
        if node is None:
            return False, None
        if isinstance(node, (exp.Where, exp.Paren)):
            return eval_boolean_bound(node.this, alias, field_name, expected_type, expected_date)
        if isinstance(node, exp.And):
            l_ok, l_err = eval_boolean_bound(node.this, alias, field_name, expected_type, expected_date)
            r_ok, r_err = eval_boolean_bound(node.expression, alias, field_name, expected_type, expected_date)
            # If either side reported a recognized conflict on this field, preserve the diagnostic!
            # Do not discard mismatch error when another leaf is correct (e.g. yxq >= cutoff AND yxq >= cutoff+1year)
            if l_err or r_err:
                return False, (l_err or r_err)
            return (l_ok or r_ok), None
        if isinstance(node, exp.Or):
            l_ok, l_err = eval_boolean_bound(node.this, alias, field_name, expected_type, expected_date)
            r_ok, r_err = eval_boolean_bound(node.expression, alias, field_name, expected_type, expected_date)
            is_ok = l_ok and r_ok  # Both branches must satisfy the boundary; an OR branch cannot bypass
            err = l_err or r_err
            return is_ok, err if not is_ok else None
        return check_leaf_bound(node, alias, field_name, expected_type, expected_date)

    # 2. Inspect cancellation conditions within boolean scope
    if expected:
        for select in tree.find_all(exp.Select):
            cancel_aliases = {t.alias_or_name.lower() for t in select.find_all(exp.Table)
                if t.find_ancestor(exp.Select) is select and t.name.lower() == cancel_table_name}
            if not cancel_aliases:
                continue
            for cancel_alias in cancel_aliases:
                # Check WHERE of this select
                slrq_ok, slrq_err = eval_boolean_bound(select.args.get('where'), cancel_alias, cancel_date_field, 'upper', expected)
                if not slrq_ok:
                    # Also check join ON condition if joined
                    for join in select.find_all(exp.Join):
                        if join.find_ancestor(exp.Select) is select:
                            j_ok, j_err = eval_boolean_bound(join.args.get('on'), cancel_alias, cancel_date_field, 'upper', expected)
                            if j_ok:
                                slrq_ok = True
                                slrq_err = None
                                break
                            elif j_err:
                                slrq_err = j_err
                if not slrq_ok:
                    if slrq_err:
                        conflicts.append({
                            'field': 'slrq',
                            'message': (f"注销排除条件与统计截止日不一致。本次须排除截至{expected_str}（含当天）的注销记录；"
                                        f"请使用 slrq < '{next_day_str}'（或日期型 slrq <= '{expected_str}'）")
                        })
                    else:
                        conflicts.append({
                            'field': 'slrq',
                            'message': (f"注销排除条件缺少受理日期截止限制。本次须排除截至{expected_str}（含当天）的注销记录；"
                                        f"请使用 slrq < '{next_day_str}'（或日期型 slrq <= '{expected_str}'）")
                        })

    # 3. Inspect certificate table fzsj and yxq
    for select in tree.find_all(exp.Select):
        aliases = {t.alias_or_name.lower() for t in select.find_all(exp.Table)
                   if t.find_ancestor(exp.Select) is select and t.name.lower() == cert_table_name}
        if not aliases:
            continue
        if select.args.get('group') and any(tree.find_all(exp.Count)):
            dated_aggregates = [a for a in select.find_all(exp.Min, exp.Max)
                if a.find_ancestor(exp.Select) is select and any(c.name.lower() in {issued_field, expiry_field} for c in a.find_all(exp.Column))]
            if dated_aggregates:
                conflicts.append({
                    'field': 'aggregate_range',
                    'message': '不得用不同记录的最早发证日和最晚到期日拼出有效区间；先按每条证书记录的发证日和到期日筛选，再按证书编号去重。'
                })

        for count in select.find_all(exp.Count):
            if count.find_ancestor(exp.Select) is not select: continue
            for column in count.find_all(exp.Column):
                if column.name.lower() != certificate_key: continue
                alias = column.table.lower() if column.table else (next(iter(aliases)) if len(aliases) == 1 else '')
                if alias not in aliases: continue

                # Evaluate fzsj within boolean scope
                fzsj_ok, fzsj_err = eval_boolean_bound(select.args.get('where'), alias, issued_field, 'upper', expected)
                if not fzsj_ok:
                    case = column.find_ancestor(exp.Case)
                    if case is not None and case.find_ancestor(exp.Count) is count:
                        branches = case.args.get('ifs') or []
                        if branches and (case.args.get('default') is None or isinstance(case.args['default'], exp.Null)):
                            if all(eval_boolean_bound(b.this, alias, issued_field, 'upper', expected)[0] for b in branches):
                                fzsj_ok = True
                if not fzsj_ok:
                    if fzsj_err:
                        conflicts.append({
                            'field': 'fzsj',
                            'message': (f"有效证书统计必须按请求时点限定发证日期；严格早于截止日会漏掉当天发证，包含截止日次日会计入未来记录。"
                                        f" 本次截止日为{expected_str}，请使用 fzsj < '{next_day_str}'（或日期型 fzsj <= '{expected_str}'）")
                        })
                    else:
                        conflicts.append({
                            'field': 'fzsj',
                            'message': ('有效证书统计缺少发证日截止条件；fzsj <= yxq只说明发证早于到期，不能排除未来才发证的记录。'
                                        + (f"本次截止日为{expected_str}，请在同一证书记录上限定 fzsj < '{next_day_str}'（或日期型 fzsj <= '{expected_str}'）" if expected else ""))
                        })

                # Evaluate yxq within boolean scope
                if expected:
                    yxq_ok, yxq_err = eval_boolean_bound(select.args.get('where'), alias, expiry_field, 'lower', expected)
                    if not yxq_ok:
                        case = column.find_ancestor(exp.Case)
                        if case is not None and case.find_ancestor(exp.Count) is count:
                            branches = case.args.get('ifs') or []
                            if branches and (case.args.get('default') is None or isinstance(case.args['default'], exp.Null)):
                                if all(eval_boolean_bound(b.this, alias, expiry_field, 'lower', expected)[0] for b in branches):
                                    yxq_ok = True
                    if not yxq_ok:
                        if yxq_err:
                            msg = "有效期筛选方向错误，不能使用小于截止日；" if yxq_err == 'inverted_direction' else "有效期截止条件与统计截止日不一致。"
                            conflicts.append({
                                'field': 'yxq',
                                'message': (f"{msg}本次须确保有效期覆盖截止日{expected_str}；"
                                            f"请使用 yxq >= '{expected_str}'（或 yxq > '{(expected - timedelta(days=1)).isoformat()}'）")
                            })
                        else:
                            conflicts.append({
                                'field': 'yxq',
                                'message': (f"有效证书统计缺少有效期截止条件；本次截止日为{expected_str}，请使用 yxq >= '{expected_str}'"
                                            f"（或 yxq > '{(expected - timedelta(days=1)).isoformat()}'）确保覆盖截止日。")
                            })

    # Deduplicate conflicts
    seen = set()
    unique_conflicts = []
    for c in conflicts:
        if c['field'] not in seen:
            seen.add(c['field'])
            unique_conflicts.append(c)

    if unique_conflicts:
        if len(unique_conflicts) == 1:
            c = unique_conflicts[0]
            repair_hint = f"，yxq覆盖截止日；沿用原地区、等级与已配置注销条件重新查询，不要改写日期解释来保留旧数值。" if c['field'] == 'fzsj' and expected else "，保留其他查询条件后重新执行。"
            raise ValueError(f"CERTIFICATE_DATE_SCOPE: {c['message']}{repair_hint}")
        else:
            items = '；'.join(f"{i+1}. {c['message']}" for i, c in enumerate(unique_conflicts))
            msg = (f"CERTIFICATE_DATE_SCOPE: 有效证书统计发现以下日期约束冲突：{items}。"
                   + (f" 本次截止日为{expected_str}，完整修复边界要求：发证日期使用 fzsj < '{next_day_str}'（或日期型 fzsj <= '{expected_str}'），"
                      f"有效期使用 yxq >= '{expected_str}' 覆盖截止日，注销排除使用 slrq < '{next_day_str}'（或日期型 slrq <= '{expected_str}'）；"
                      if expected else "")
                   + "请同时修正上述日期边界，沿用原统计对象与地区、等级等其他筛选条件重新查询，不要改写日期解释来保留旧数值。")
            raise ValueError(msg)


def check_historical_units(source,host,*,sql=None,dialect=None,metric_id=None,registered=False):
    spec=qualification_spec(source)
    if not historical_rules_enabled(source, spec):
        return
    scope=request_scope(host,spec=spec)
    if not scope: return
    chain,end=scope
    def configured_table(name):
        exact = [t for t in source['tables'] if t['name'] == name]
        return exact[0] if len(exact) == 1 else indicator_table(source,name.lower())
    cert=configured_table(spec['cert_table'])
    cancels=configured_table(spec['cancel_table'])
    if metric_id and metric_id not in spec['guarded_metrics']: return
    if sql:
        tree=sqlglot.parse_one(sql,read=dialect)
        if not any(t.name.lower() in spec['guarded_tables'] for t in tree.find_all(exp.Table)): return
    if end is None:
        raise ValueError('NEEDS_CLARIFICATION: 请确认一个有效的统计截止日，以便按该日核对资质。')
    if sql and not registered:
        approved = historical_query_recipe(source, host)
        if (approved and approved['subject'] == 'unit'
                and tree == sqlglot.parse_one(approved['sql'], read=dialect)):
            return None
        from .historical_sql import compatible
        if compatible(sql, dialect, spec, end):
            # AI may adapt real encodings, hierarchical regions, multi-valued
            # fields and additional predicates. A pack example is not a SQL
            # allowlist. Normal authorization, enums and metric checks still run.
            return scope_fields(chain, end, spec=spec)['ranking']
    text = ' '.join(chain)
    if (re.search('区县|街道|[\\u4e00-\\u9fff]{2}(?:区|县)(?!域)|所有制|国有|民营|注册资本|省外|剔除|人员|法人|排除|之外|以外|除.+外|不含|不包括|不要|不看|各地市|按地市|各市|按城市|同比|环比',text)
            or any(region not in ('全省','广东省','本省') for region in re.findall(r'[\u4e00-\u9fff]{1,2}省',text) if not region.endswith(('全省','本省')))):
        raise ValueError('HISTORICAL_SCOPE: 当前登记的期末专业单位查询不能证明这个附加条件，请核实范围后查询。')
    grade_column, profession_column = spec['grade_column'], spec['profession_column']
    required = {'zsbh','fzsj','yxq',grade_column,profession_column,'dwid','dwmc','szcs'}
    if not cert or not cancels or not required <= fields(cert).keys() or not {'zsbh','sfzx','slrq'} <= fields(cancels).keys():
        raise ValueError('HISTORICAL_SCOPE: 授权目录缺少按期末还原有效单位所需的证书或注销字段，不能用当前快照回答。')
    # Use the pack's published physical spelling; custom upper-case schemas need their own verified mapping.
    if not all(c['name']==c['name'].lower() for t in (cert,cancels) for c in t['columns'] if c['name'].lower() in required | {'sfzx','slrq'}):
        raise ValueError('HISTORICAL_SCOPE: 请核对历史证书字段的物理标识后再按期末统计。')
    resolved=scope_fields(chain,end,spec=spec)
    grades,pros=resolved['grades'],resolved['professions']
    names=resolved['names']
    def quoted(values): return ','.join("'"+v.replace("'", "''")+"'" for v in values)
    def label_expression(column, labels):
        # Use the administrator's published raw-value mapping, not an assumed
        # numeric-code convention or model-authored SQL. Without a mapping the
        # existing literal-label behavior remains unchanged.
        metadata=next(c for c in cert['columns'] if c['name']==column)
        mapping=metadata.get('business_values') or []
        groups={label:[str(v['value']) for v in mapping if v.get('label')==label] for label in labels}
        clauses=[f'WHEN CAST(z.{column} AS VARCHAR) IN ({quoted(values)}) THEN {quoted([label])}'
                 for label,values in groups.items() if values]
        return 'CASE '+' '.join(clauses)+f' ELSE CAST(z.{column} AS VARCHAR) END' if clauses else 'z.'+column
    grade_expr=label_expression(grade_column,spec['grades'])
    profession_expr='z.__profession' if spec['profession_separator'] else label_expression(profession_column,spec['professions'])
    where=[]
    if pros: where.append(profession_expr+' IN ('+quoted(pros)+')')
    if grades: where.append(grade_expr+' IN ('+quoted(grades)+')')
    # City must come from a human, and use an exact bounded city value.
    cities = resolved['cities']
    cities_re = spec['cities_re']
    if resolved['city_requested']:
        if not cities:
            records = host.get('clarifications') or []
            answer = next((r.get('answer','') for r in reversed(records) if re.search('市|地区',r.get('question',''))), '')
            if not records: answer = host.get('confirmation') or ''
            # A legacy reply must contain one city, not a concatenated transcript or a veto.
            if re.fullmatch(r'\s*(?:选|选择|查|只看)?(?:'+cities_re.pattern+r')市?(?:[（(]推荐[）)])?[。！!]?\s*',answer):
                cities = set(cities_re.findall(answer))
        if len(cities)!=1: raise ValueError('NEEDS_CLARIFICATION: 请通过原生提问确认要查哪个地市。')
        where.append('z.szcs = '+quoted([cities.pop()+'市']))
    cert_table=table(cert['name']); cancel_table=table(cancels['name'])
    next_day = end + timedelta(days=1)
    # A profession/grade/city is supported by its own effective record. Never
    # combine an old issue date with a future renewal's expiry or attributes.
    base = (f"WITH valid AS (SELECT * FROM {cert_table} WHERE zsbh IS NOT NULL "
            f"AND fzsj < '{next_day}' AND yxq >= '{end}') ")
    population_table = 'valid'
    if spec['profession_separator']:
        # Explicit administrator-owned encoding, exact tokens only. A substring
        # must not turn e.g. a different profession into this profession.
        separator=spec['profession_separator']
        metadata=next(c for c in cert['columns'] if c['name']==profession_column)
        mapping=metadata.get('business_values') or []
        selections=[]
        for profession in spec['professions']:
            tokens=[str(v['value']) for v in mapping if v.get('label')==profession] or [profession]
            if any(separator in token or any(x in token for x in ('%', '_')) for token in tokens):
                raise ValueError('HISTORICAL_CONFIG: 专业编码不能包含分隔符或通配符，请核对已发布枚举。')
            matches=[f"({quoted([separator])} || TRIM(z.{profession_column}) || {quoted([separator])}) LIKE {quoted(['%'+separator+token+separator+'%'])}" for token in tokens]
            selections.append('SELECT z.*, '+quoted([profession])+' AS __profession FROM valid z WHERE '+' OR '.join(matches))
        base=base.rstrip()+', expanded AS ('+' UNION ALL '.join(selections)+') '
        population_table='expanded'
    where.insert(0,f"NOT EXISTS (SELECT 1 FROM {cancel_table} s WHERE s.zsbh=z.zsbh AND s.sfzx='是' AND s.slrq < '{next_day}')")
    population=' FROM '+population_table+' z WHERE '+' AND '.join(where)
    if names: recipe=base+'SELECT DISTINCT z.dwmc AS 单位名称'+population+' AND z.dwmc IS NOT NULL ORDER BY z.dwmc'
    else:
        order=grade_expr
        rank=resolved['ranking']
        if rank and len(rank['grades'])==1:
            order='CASE WHEN '+grade_expr+'='+quoted(rank['grades'])+' THEN 0 ELSE 1 END'
        recipe=base+'SELECT '+profession_expr+' AS 专业, '+grade_expr+' AS 等级, COUNT(DISTINCT COALESCE(CAST(z.dwid AS VARCHAR(255)),z.dwmc)) AS 单位数'+population+' GROUP BY '+profession_expr+','+grade_expr+' ORDER BY '+order+',单位数 DESC,'+profession_expr
    if registered:
        return recipe
    if sql and tree==sqlglot.parse_one(recipe,read=dialect):
        return resolved['ranking'] if not names else None
    raise ValueError('HISTORICAL_SCOPE: 历史有效单位不能用当前快照或未限定日期的记录回答。请在证书源记录的 WHERE 中同时限定发证日早于截止日次日、到期日覆盖截止日，再按已知注销口径和单位去重；保留用户的地区、专业、等级及附加条件。可先用 purpose=explore 核对实际字段取值，再用 purpose=answer 执行修正后的 SQL。下方仅为推定口径参考，编码、复合字段或地区格式不同时必须调整，不必与模板一致。\n'+recipe)
