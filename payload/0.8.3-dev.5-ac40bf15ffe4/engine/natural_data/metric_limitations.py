"""Source-scoped availability declarations from published business configuration."""
import re
import unicodedata


KIND = 'metric-limitation'
MISSING_HISTORY = 'requires_comparable_history'
_FIELDS = {'kind', 'id', 'source_id', 'status', 'reason', 'unit', 'aliases'}
_COMPARISON_JOIN = re.compile(r'''[\s)\]」』”"']*(?:和|与|及|以及|、|并且|同时)[\s,，]*(?:环比|同比)(?:变化率|增长率|增幅)?[\s(\[「『“"']*''')


def validate(row, source_ids):
    spec = row['config']
    source_id = spec.get('source_id')
    if not isinstance(source_id, str) or source_id not in source_ids or row.get('source_id', source_id) != source_id:
        raise ValueError('METRIC_LIMITATION_CONFIG: 不可计算指标必须绑定本项目的同一数据源。')
    if set(spec) - _FIELDS or spec.get('status') != MISSING_HISTORY:
        raise ValueError('METRIC_LIMITATION_CONFIG: 不可计算指标状态或字段无效。')
    if not isinstance(spec.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', spec['id']):
        raise ValueError('METRIC_LIMITATION_CONFIG: 指标标识无效。')
    for field, maximum in [('reason', 400), ('unit', 40)]:
        value = spec.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError('METRIC_LIMITATION_CONFIG: 请填写不可计算原因和单位。')
    aliases = spec.get('aliases', [])
    if not isinstance(aliases, list) or len(aliases) > 16 or any(
            not isinstance(alias, str) or not alias.strip() or len(alias) > 160 for alias in aliases):
        raise ValueError('METRIC_LIMITATION_CONFIG: 指标别名须为非空文本数组。')


def entries(source):
    """Use the same records for catalog discovery and execution validation."""
    for row in source.get('terms', []):
        spec = row.get('config')
        if not isinstance(spec, dict) or spec.get('kind') != KIND or spec.get('source_id') != source['id']:
            continue
        try:
            validate(row, {source['id']})
        except ValueError:
            # Invalid legacy records have no authority over executable queries.
            continue
        yield {'id': spec['id'], 'version': '', 'name': row['name'], 'kind': KIND,
               'state': spec['status'], 'unit': spec['unit'], 'reason': spec['reason'],
               'aliases': spec.get('aliases', []), 'action': 'business'}


def _normalized(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).strip()).casefold()


def _pattern(entry):
    names = sorted({_normalized(value) for value in [entry['id'], entry['name'], *entry['aliases']]}, key=len, reverse=True)
    # ASCII identifiers must be whole. Chinese names can follow a time or place
    # directly. Only complete configured names and aliases are considered.
    return re.compile('|'.join((r'(?<![a-z0-9_])' if re.match(r'[a-z0-9_]', name) else '') + re.escape(name)
                               + (r'(?![a-z0-9_])' if re.search(r'[a-z0-9_]$', name) else '') for name in names))


def _has_separate_comparison(source, question, limited_mentions):
    # For "现值（已登记编码）和环比（受限编码）", confirm the left-hand
    # mention against the published terms. The comparison label or conjunction
    # alone is insufficient to treat a single unavailable metric as a mixed one.
    if not limited_mentions:
        return False
    for row in source.get('terms', []):
        spec = row.get('config') or {}
        if not isinstance(spec, dict) or spec.get('kind') == KIND:
            continue
        names = [row.get('name'), spec.get('id'), *(row.get('aliases') or []), *(spec.get('aliases') or [])]
        for value in names:
            if not isinstance(value, str) or not value.strip() or _normalized(value) not in question:
                continue
            pattern = _pattern({'id': value, 'name': value, 'aliases': []})
            for available in pattern.finditer(question):
                if any(available.end() <= limited.start()
                       and _COMPARISON_JOIN.fullmatch(question[available.end():limited.start()])
                       for limited in limited_mentions):
                    return True
    return False


def _named_targets(sql, dialect):
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError

    try:
        tree = parse_one(sql, read=dialect)
    except SqlglotError:
        return []  # SQL syntax and policy are checked separately.
    aliases = [node.alias for node in tree.find_all(exp.Alias) if node.alias]
    literals = [node.this for node in tree.find_all(exp.Literal) if node.is_string]
    return [_normalized(value) for value in [*aliases, *literals]]


def require_available(source, *, metric_id='', title='', question='', sql='', dialect=None, purpose='answer'):
    limitations = list(entries(source))
    if not limitations:
        return
    selected = _normalized(metric_id)
    targets = _named_targets(sql, dialect) if sql else []
    title_text, question_text = _normalized(title), _normalized(question)
    patterns = [(entry, _pattern(entry)) for entry in limitations]
    # A multipart request can still query its available components. An
    # explicitly selected metric, output alias or title remains authoritative.
    remainder = question_text
    for _, pattern in patterns:
        remainder = pattern.sub(' [指标] ', remainder)
    multi_request = re.search(r'分别|以及|并且|同时|另外|再(?:查|算|给|看|列)|还(?:要|想|有)|(?:和|与|及|、)\s*\[指标\]|\[指标\]\s*(?:和|与|及|、)', remainder)
    if not multi_request and _COMPARISON_JOIN.search(question_text):
        mentions = [match for _, pattern in patterns for match in pattern.finditer(question_text)]
        multi_request = _has_separate_comparison(source, question_text, mentions)
    for entry, pattern in patterns:
        targeted = pattern.fullmatch(selected) or pattern.search(title_text) or any(pattern.fullmatch(name) for name in targets)
        sole_request = purpose != 'explore' and not selected and not multi_request and pattern.search(question_text)
        if targeted or sole_request:
            raise ValueError(f"METRIC_REQUIRES_COMPARABLE_HISTORY: 「{entry['name']}」当前不可计算。{entry['reason']}补齐同口径历史数据并复核发布后再计算；缺失不等于0。其余有数据的指标可以分别查询。")
