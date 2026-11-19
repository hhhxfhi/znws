"""Reject explicit count-object mismatches against administrator-owned names.

This is a conservative Chinese noun-head check, not an intent classifier. It
never selects another metric or guesses a query. Published aliases can establish
equivalence; compound, unnamed and non-count measures are left to normal planning.
"""
import re

COUNT_UNITS = {'个', '家', '人', '户', '本', '张', '份', '件', '条', '笔', '台', '辆'}
_COUNT_CLASSIFIER = '[' + ''.join(sorted(COUNT_UNITS)) + '项位名枚套宗]'
# Organisation nouns customers use for one another. They are never equal by
# default: a published alias settles it, otherwise the user is asked.
NEAR_SUBJECTS = {'企业', '单位', '机构', '公司'}
_GENERIC_ROW_SUBJECTS = {'记录', '数据', '结果', '行'}
_GENERIC_COUNT_LABEL = re.compile(
    r'(?:总计|合计|小计|总|有效|无效|实际|新增|符合条件|匹配|返回|统计|当前|全部|去重|记录|数据|结果|行|的|\s)*'
    r'(?:数量|总量|总数|[件条笔个]?数|计数)'
)
_BARE_ROW_COUNT = re.compile(
    r'(?:记录|数据|结果)(?:的)?(?:总)?行数$'
    r'|(?:^|统计|返回|查询|计算|查看|查找|查|算|看|的)(?:总)?(?:行|条)数$'
)
_RANKED_CATEGORY = r'(?:哪(?:一)?(?:个|种|类|项|组)|什么(?:类别|类型))[^，。；;！？!?\n]{1,26}?的'
_RANK_WORD = r'(?:最多|最少|最高|最低|最大|最小|排(?:名)?第一)'
_ROW_RANK = re.compile(_RANKED_CATEGORY + r'\s*(?:件数|条数|笔数)\s*' + _RANK_WORD)
_PERSON_RANK = re.compile(_RANKED_CATEGORY + r'\s*人数\s*' + _RANK_WORD)
_COUNT_BEFORE_AMOUNT = re.compile(
    r'([一-鿿]{2,}?)(?:一共|总共|合计|各自|分别|又|各|共)?\s*(?:有多少|共多少)\s*'
    + _COUNT_CLASSIFIER + r'?\s*(?=$|[，,。；;！？!?\n])'
)
_COUNT_AFTER_AMOUNT = re.compile(r'(?:有多少|共多少)\s*(?:' + _COUNT_CLASSIFIER + r'\s*|(?!' + _COUNT_CLASSIFIER
                                 + r'))([一-鿿]{2,})\s*(?=$|[，,。；;！？!?\n])')
_COUNT_BY_DISTINCT = re.compile(r'按([一-鿿]{2,}?)去重(?:后|再)?(?:统计|计算|查询|查看)?(?:总数量|数量|总数|计数)(?=$|[，,。；;！？!?\s])')
_COUNT_ACTIONS = {'统计', '查询', '计算', '查看', '去重', '合计', '总计', '各占', '变化', '增减', '相差'}
# These describe the action applied to a record. A classifier suffix such
# as 件数 labels its measure without naming the underlying counted entity.
_COUNTED_ACTIVITY = {'批准', '受理', '办理', '办结', '处理', '处置', '签发', '发放', '归档', '交付'}
_ACTIVITY_COUNT_LABEL = re.compile(
    r'(?:总|有效|实际|新增|累计|本期|当期|已|未|的|\s)*(?:'
    + '|'.join(sorted(_COUNTED_ACTIVITY)) + r')(?:的)?(?:总量|总数|数量|量|'
    + _COUNT_CLASSIFIER + r'?数)'
)
_COUNT_WORDS = {'数量', '总数', '总量', '件数', '条数', '行数'}
_DISTRIBUTED_GROUP = re.compile(r'(?:各(?:个)?|每(?:一)?[个]?|逐)(?:大?类|类别|类型|组|项)$')
_COUNTED_STATE = {'生效', '失效', '启用', '停用', '验收', '签收', '入库', '出库', '结案', '报废'}
_EVENT_PREDICATE = re.compile(
    r'(?P<entity>[一-鿿]{2,}?)(?:(?:已经|正在|尚未|仍未|已|未)?(?:完成|通过|被)[一-鿿]{2,}'
    r'|(?:已经|正在|尚未|仍未|已|未)?(?:' + '|'.join(sorted(_COUNTED_STATE)) + r'))$'
)
_ONCE_PER_OBJECT = re.compile(
    r'(?:同一(?:' + _COUNT_CLASSIFIER + r')?|每(?:一)?' + _COUNT_CLASSIFIER + r')'
    r'(?P<entity>[一-鿿]{2,18}?)'
    r'(?:(?:在)?(?:同一(?:个)?|每(?:个)?|当|本)(?:月(?:份)?|周|季度|年(?:度)?|期|天|日)(?:内|中)?)?'
    r'(?:只|仅|最多)(?:计算|统计|计数|计|算)(?:一|1)次'
)


def once_count_subjects(text):
    """Bind a one-per-object requirement to the explicit object in its clause."""
    return {match['entity'][-2:] for clause in re.split(r'[，,。；;！？!?\n]', str(text or ''))
            if (match := _ONCE_PER_OBJECT.fullmatch(clause.strip()))}


def physical_entity_parent(source, table_name, column):
    """Resolve one inspected single-column FK to a unique key on an authorized table."""
    tables = {table['name']: table for table in source.get('tables', [])}
    table = tables.get(table_name, {})
    parents = set()
    for link in table.get('foreign_keys') or []:
        child = link.get('constrained_columns') or []
        parent_key = link.get('referred_columns') or []
        name = link.get('referred_table')
        if child != [column] or len(parent_key) != 1 or not name:
            continue
        schema = link.get('referred_schema') or table_name.rpartition('.')[0]
        name = f'{schema}.{name}' if schema else name
        parent = tables.get(name, {})
        keys = [parent.get('primary_key') or [], *(parent.get('unique_keys') or [])]
        if parent_key in keys:
            parents.add(name)
    return tables[next(iter(parents))] if len(parents) == 1 else None


def _after_amount(value):
    if (match := _EVENT_PREDICATE.fullmatch(value)) and match['entity'] not in _COUNTED_STATE:
        return match['entity']
    return value


def count_subjects(text):
    text = re.sub(r'[（(][^）)]*[）)]', '', str(text or '')).replace('总数量','总数')
    # “资质的单位数据量” still counts the units holding the qualification.
    text = re.sub(r'(资质)的((?:持证)?单位)(?:数据量|数量|总量|总数|数)', r'\1\2数量', text)
    text = re.sub(r'(持证单位|资质单位|证书)(?:的)?(?:期末|月末|年末)?存量', r'\1数量', text)
    # In "which category has the most records/people", the interrogative
    # phrase names the grouping key. The measure follows 的.
    people = bool(_PERSON_RANK.search(text))
    text = _ROW_RANK.sub('', text)
    text = _PERSON_RANK.sub('', text)
    subjects=set()
    # Words such as 测量 and 估量 do not name a counted entity.
    suffix = r'(?:的)?(?:数量|总量|总数|(?:个|家|人|户|本|张|份|件|条|笔|台|辆)数(?![量目据字值额])|(?<![常变参系分倍百千万十零几多少质重容含能流产作测估丈计衡酌尽])(?:数|量)(?=$|[，。；;！？!?\s的及和与或分]|是多少|有多少))'
    for clause in re.split(r'[。；;！？!?\n]',text):
        # Conjunctions in an unrelated explanatory clause do not erase a count request.
        matches = list(re.finditer(r'([一-鿿]{2,}?)'+suffix,clause))
        matches.extend(m for pattern in (_COUNT_BEFORE_AMOUNT, _COUNT_BY_DISTINCT) for m in pattern.finditer(clause))
        named = [m[1] for m in matches]
        named.extend(_after_amount(m[1]) for m in _COUNT_AFTER_AMOUNT.finditer(clause))
        for phrase in named:
            objects=re.split(r'和|以及|分别|与|、|或者|还是',phrase)
            subjects.update(x[-2:] for x in objects if len(x)>=2 and x[-2:] not in _COUNT_WORDS
                            and not _DISTRIBUTED_GROUP.search(x))
    return (subjects - _COUNT_ACTIONS - _COUNTED_ACTIVITY) | ({'人数'} if people else set())


def projected_count_subjects(label):
    name = re.sub(r'[（(][^）)]*[）)]', '', str(label or '')).strip()
    name = re.sub(r'[_\s]+[' + ''.join(sorted(COUNT_UNITS)) + r']$', '', name)
    subjects = set()
    for clause in re.split(r'[，。；;！？!?\n]', name):
        clause = clause.strip()
        # A request for the number of rows has no separate business entity.
        if _GENERIC_COUNT_LABEL.fullmatch(clause) or _BARE_ROW_COUNT.search(clause):
            continue
        subjects.update(count_subjects(clause))
        for match in re.finditer(r'([一-鿿]{2,})去重数(?=$|\s)', clause):
            subjects.add(match[1][-2:])
    return subjects - _GENERIC_ROW_SUBJECTS


def requested_subjects(host):
    text = (host or {}).get('user_text', '')
    subjects = count_subjects(text)
    if subjects:
        return subjects
    # Only a date-only refinement inherits the immediately preceding count
    # request. A new topic or a new object never borrows old scope.
    remainder = re.sub(r'我|需要|想|要|看|查|查询|统计|截止|截至|到|按|改为|换成|只看|仅看|的|数据|今天|今日|现在|当前|本月|今年|去年|上月|昨天|[\d年月日\s，,。.!！?？/\-]', '', text)
    if text.strip() and not remainder:
        recent = (host or {}).get('recent_questions') or []
        if recent:
            return count_subjects(recent[-1])
    return set()


def declared_subjects(name):
    # A bare alias such as "企业" names the counted object without "数量".
    subjects = count_subjects(name)
    if (not subjects and re.fullmatch(r'[一-鿿]{2,}', name or '')
            and name not in _COUNTED_ACTIVITY and not _ACTIVITY_COUNT_LABEL.fullmatch(name)
            and not _GENERIC_COUNT_LABEL.fullmatch(name)):
        subjects = {name[-2:]}
    return subjects


def confirmed_by_user(host, requested, declared):
    for record in reversed((host or {}).get('clarifications') or []):
        question, answer = record.get('question') or '', record.get('answer') or ''
        if requested in question and any(d in question for d in declared):
            if re.search(r'不是|不对|不按|不同意|不可以|不确认|否|另行|还是|[?？]', answer):
                return False
            named = any(d in answer for d in declared)
            if requested in answer and not named:
                return False
            return named or bool(re.search(r'是|对|确认|同意|可以|该指标', answer))
    return False


def validate_counting_subject(source, contract, host):
    if contract.get('unit') not in COUNT_UNITS or contract.get('ratio'):
        return
    requested = requested_subjects(host)
    if len(requested) != 1:
        return
    if requested == {'人数'} and contract['unit'] == '人':
        return
    names = [contract.get('name', ''), *(contract.get('aliases') or [])]
    for term in source.get('terms') or []:
        if (term.get('config') or {}).get('id') == contract.get('id'):
            names += [term.get('name', ''), *(term.get('aliases') or []), *(term.get('synonyms') or [])]
    declared = set().union(*(declared_subjects(name) for name in names))
    if declared and requested.isdisjoint(declared):
        subject = next(iter(requested))
        near = sorted(declared & NEAR_SUBJECTS)
        if subject in NEAR_SUBJECTS and near:
            if confirmed_by_user(host, subject, near):
                return
            raise ValueError('NEEDS_CLARIFICATION: 用户统计“' + subject + '”，所选已发布指标“' +
                             str(contract.get('name') or contract['id']) + '”统计“' + '、'.join(near) +
                             '”；业务口径未登记二者为同一对象。请通过 ask_user_question 确认是否按“' +
                             str(contract.get('name') or contract['id']) + '”统计（选项如：按该指标统计；不是，按“' + subject +
                             '”另行查询），收到确认后再查询；不得自行视为相同。')
        raise ValueError('METRIC_SUBJECT: 用户明确统计“' + next(iter(requested)) + '”，所选已发布指标“' +
                         str(contract.get('name') or contract['id']) + '”统计“' + '、'.join(sorted(declared)) +
                         '”，不能互相替代。请重新 data_catalog，question 保留用户原问的统计对象；选择对象一致的指标，若无对应指标则查看授权表后用 data_sql_query 按原对象统计。不要改写用户的问题或套用本指标数值。')
