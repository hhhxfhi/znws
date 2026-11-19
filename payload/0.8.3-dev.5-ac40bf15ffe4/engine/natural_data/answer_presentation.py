"""Separate configuration audit status from the explanation of a computed answer."""
import re

_REGISTRATION = re.compile(
    r'(?:(?:当前|本次|本轮|本|该|此|所用|采用的)?(?:计算|统计|指标)?(?:口径|定义|计算规则))'
    r'(?:目前|当前|暂|仍|尚|仅|为|属于|属|是|处于|系|本身|由|按|系统|自动|临时|\s){0,16}'
    r'(?:推定(?:口径|定义)?|暂定(?:口径|定义)?|(?:尚未|未经|尚待|待|仍待|有待)(?:经)?(?:业务(?:方)?|相关(?:业务)?人员|人工)?(?:进一步)?(?:正式)?(?:确认|核对|核实|审定))'
    r'|(?:属(?:于)?|为|系|按|采用|使用|基于)(?:系统|自动|临时|当前)?(?:推定|暂定)(?:的)?(?:计算|统计)?(?:口径|定义|计算规则)'
    r'|(?:(?:当前|本次|本轮|本|该|此)?(?:SQL|查询|汇总|统计|计算|结果|数值)[^。；;，,\n]{0,16})?'
    r'(?:尚未|仍未|暂未|没有|未)(?:关联|绑定|对应|匹配)(?:到)?(?:任何)?(?:已确认的|正式|业务|已发布|已登记|配置)?(?:业务)?(?:指标|口径|定义)'
    r'|(?:尚未|仍未|暂未|没有|未)(?:配置|登记|注册|纳入|备案)(?:为|成|入)?(?:正式|已发布|发布|业务){0,2}(?:指标|口径|指标体系|指标库)'
    r'|(?:没有|尚无|未找到)(?:匹配的|对应的|相关的)?(?:已发布的?|已登记的?|预先配置的?|正式的)(?:业务)?(?:指标|口径|指标定义)'
    r'|(?:尚未|未经|尚待|待|仍待|有待)(?:经)?(?:业务(?:方)?|相关(?:业务)?人员|人工)(?:进一步)?(?:正式)?确认'
    r'|(?:请(?:[^。；;，,\n]{0,16}?)|需|须|尚待|仍待|有待|待)(?:业务(?:方)?|相关(?:业务)?人员|人工)?(?:进一步)?(?:核对|核实|确认|审定)(?:无误)?(?:后|之后)?(?:再)?(?:正式)?(?:采用|报送|作为正式(?:报送|统计)?(?:值|结果|口径)?(?:采用|使用|报送)?)'
    r'|(?:(?:该|此|本次|当前|上述)(?:数值|结果|数据)|(?:数值|结果))(?:仍|尚|暂)?(?:尚未|未经|尚待|待)(?:业务(?:方)?|人工)?(?:正式)?确认(?:(?:后|之后)?(?:再)?(?:正式)?(?:采用|报送|作为正式(?:报送|统计)?(?:值|结果)?(?:采用|使用|报送)?))?'
)

_QUOTED = re.compile(r'“[^”\n]*”|「[^」\n]*」|『[^』\n]*』|‘[^’\n]*’|"[^"\n]*"|\'[^\'\n]*\'|`[^`\n]*`')
_META_OR_NEGATION = re.compile(
    r'(?:为什么|为何|怎么会|为啥|干嘛|原话|以前(?:曾)?显示|之前(?:曾)?显示|上次(?:报告)?(?:说|显示|提示)|此前(?:曾)?(?:显示|提示)'
    r'|(?:不再|不会|并未|并没有|未再|不)(?:显示|提示|输出|展示|要求|需要)'
    r'|(?:无需|不需要|不必|免去|取消|去掉|去除|删除|剥离|并非|不是)(?:业务(?:方)?|人工)?(?:确认|核对|提示|推定))'
)
_DANGLING_LEAD_IN = re.compile(
    r'^(?:[-*>#]+\s*)?(?:(?:口径)?说明[：:]\s*)?(?:以上为|本次|本轮|当前)?[^，,。；;]{0,24}(?:基础查询|查询|统计|计算)(?:的)?(?:统计|计算)?(?:口径|定义)(?:[（(][^）)]*[）)])?\s*[，,]?\s*$'
)
_COMPANION_CAVEAT = re.compile(r'^(?:[-*>#]+\s*)?(?:(?:上述|以上)?(?:结果|数据|数值))?仅供参考[，,。；;！!]*$')
_FENCE_OPEN = re.compile(r'^\s*(`{3,}|~{3,})')
_SENTENCES = re.compile(r'[^。；;！!？?]+[。；;！!？?]*|[。；;！!？?]+')
_DATA_GAP = re.compile(
    r'权限不足|无(?:查询|访问|读取)权限|未(?:获|获得)(?:查询|访问|读取)?权限|未授权|分母(?:为|是)\s*(?:0(?!\d)|零)'
    r'|(?:缺少|缺失|没有|尚无|未提供|未保存|未覆盖)[^。；;，,\n]{0,26}'
    r'(?:历史(?:记录|数据|时点|快照)?|观测|数据记录|字段|分子|分母|关联(?:关系|键)|(?:日|月|年)(?:的)?(?:数据|记录))'
    r'|(?:历史(?:数据|记录|快照)|所需字段|分母)[^。；;，,\n]{0,15}(?:缺失|未保存|未覆盖|缺少|不足)'
)


def _mask_quotes(text):
    return _QUOTED.sub(' __Q__ ', text)


def _segments(text, pattern):
    masked = _QUOTED.sub(lambda m: '_' * len(m[0]), text)
    return [text[m.start():m.end()] for m in re.finditer(pattern, masked)]


def _is_protected_sentence(sentence):
    plain = sentence.replace('**', '').strip()
    if not plain:
        return False
    if re.search(r'[？?][”」』’"\'\s]*$', plain):
        return True
    if _META_OR_NEGATION.search(plain):
        return True
    return False


def _keep_gap_from_registration(clause):
    masked = _mask_quotes(clause)
    if not _DATA_GAP.search(masked):
        return ''
    if masked != clause:
        return clause
    parts = re.split(r'并且|而且|但是|且|但', clause)
    facts = [part for part in parts if not _REGISTRATION.search(part.replace('**', ''))]
    if not any(_DATA_GAP.search(part) for part in facts):
        return clause
    end = re.search(r'[，,。；;！!？?]+$', clause.rstrip())
    return re.sub(r'[，,。；;！!？?]+$', '', '且'.join(facts).strip()) + (end[0] if end else '')


def _clean_sentence(sentence):
    if _is_protected_sentence(sentence):
        return sentence
    masked = _mask_quotes(sentence.replace('**', '').strip())
    if not _REGISTRATION.search(masked):
        return sentence
    clauses = _segments(sentence, r'[^，,]+[，,]*|[，,]+')
    kept = []
    removed = False
    for clause in clauses:
        plain = clause.replace('**', '').strip()
        if not _META_OR_NEGATION.search(plain) and _REGISTRATION.search(_mask_quotes(plain)):
            kept_gap = _keep_gap_from_registration(clause)
            if kept_gap:
                kept.append(kept_gap)
            removed |= kept_gap != clause
        else:
            kept.append(clause)
    if not removed:
        return sentence
    if not kept:
        return ''
    if all(_COMPANION_CAVEAT.match(c.replace('**', '').strip()) for c in kept):
        return ''
    kept = [c for c in kept if not _COMPANION_CAVEAT.match(c.replace('**', '').strip())]
    if not kept:
        return ''
    joined = ''.join(kept).strip()
    if _DANGLING_LEAD_IN.match(joined.replace('**', '')):
        return ''
    term_match = re.search(r'[。；;！!？?]+$', sentence.strip())
    if term_match and re.search(r'[，,、]+$', joined):
        joined = re.sub(r'[，,、]+$', term_match.group(0), joined)
    return joined


def calculation_text(text):
    """Strip system registration/confirmation notices while preserving data, tables, quotes, and real limitations."""
    out_lines = []
    fence = None
    for line in str(text or '').split('\n'):
        fence_match = _FENCE_OPEN.match(line)
        if fence is None and fence_match:
            fence = (fence_match.group(1)[0], len(fence_match.group(1)))
            out_lines.append(line)
            continue
        if fence is not None:
            if re.match(rf'^\s*{re.escape(fence[0])}{{{fence[1]},}}\s*$', line):
                fence = None
            out_lines.append(line)
            continue
        if re.match(r'^\s*>', line) or re.match(r'^\s*\|.*\|\s*$', line):
            out_lines.append(line)
            continue
        sentences = _segments(line, _SENTENCES)
        if not sentences:
            out_lines.append(line)
            continue
        cleaned = [_clean_sentence(s) for s in sentences]
        if cleaned == sentences:
            out_lines.append(line)
            continue
        joined = ''.join(cleaned).strip()
        joined = re.sub(r'^(?:[-*>#]+\s*)?(?:口径说明|说明)[：:]\s*$', '', joined)
        out_lines.append(joined)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(out_lines)).strip()


def calculation_notes(notes):
    """Preserve real limitations (missing rows, units, coverage), omit registration notices.

    This is a presentation of old or new receipts, not a change to their stored
    confirmation state or their configured business definition.
    """
    out = []
    for note in notes or []:
        if not isinstance(note, str):
            continue
        cleaned = calculation_text(note)
        if cleaned:
            out.append(cleaned)
    return out


def calculation_contract(contract):
    if not isinstance(contract, dict) or 'limitations' not in contract:
        return contract
    return {**contract, 'limitations': calculation_notes(contract.get('limitations'))}
