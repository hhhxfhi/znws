"""0.7.0: the same business concept can be a metric in several databases with different
definitions. 抽检合格率 is one example: 资质单位监督检查合格率 in one database,
成果质检记录合格率算术平均 in another. 财政投入 is another: 财政基础测绘投入金额 vs
区级基础测绘实际支出.

The catalog lists these collisions. A query whose question names such a concept, without
saying which database (or which specific metric) it means, must be clarified first. The
user's choice then holds for the rest of the session.
"""
import re
import threading

DIALECT_NAMES = {'postgresql': 'PostgreSQL', 'postgis': 'PostGIS', 'tbase': 'TBase', 'kingbase': 'Kingbase',
                 'mysql': 'MySQL', 'sqlite': 'SQLite', 'oracle': 'Oracle', 'dameng': '达梦'}
METRIC_LISTS = ('semantic_metrics', 'metric_definitions')
# (concept, how a question says it, how a metric name says it)
CONCEPTS = [
    ('合格率', re.compile(r'合格率|合格情况|合格比例'), re.compile(r'合格率')),
    ('投入/支出', re.compile(r'投入|支出|经费|投资|拨款|投了|花了'), re.compile(r'投入|支出|经费|投资')),
    ('服务/调用次数', re.compile(r'服务次数|调用次数|服务量|调用量|服务了多少'), re.compile(r'服务次数|调用次数')),
]
BOTH = re.compile(r'(两个|两|各个|每个|所有|全部)(库|数据库|数据源)')
_lock = threading.Lock()
_choices = {}  # (project, session) -> {concept: source_id}


def _metrics(source):
    term_aliases = {
        (t.get('config') or {}).get('id'): [*(t.get('aliases') or []), *(t.get('synonyms') or [])]
        for t in (source.get('terms') or [])
        if isinstance(t, dict) and isinstance(t.get('config'), dict) and (t.get('config') or {}).get('id')
    }
    for key in METRIC_LISTS:
        for m in source.get(key) or []:
            name = m.get('name') or m.get('label')
            if name:
                aliases = [*(m.get('aliases') or []), *term_aliases.get(m.get('id'), [])]
                yield str(name), m.get('id'), [str(a).strip() for a in aliases if a and str(a).strip()]


def collisions(sources):
    """{concept: [{source_id, source_name, metric}]} for concepts held by two or more sources.
    A metric name or alias shared by two sources is its own concept."""
    found = {}
    names = {}
    shared_aliases = {}
    for s in sources:
        for name, _, aliases in _metrics(s):
            names.setdefault(name, []).append(s)
            for concept, _, in_name in CONCEPTS:
                if in_name.search(name):
                    found.setdefault(concept, []).append({'source_id': s['id'], 'source_name': s.get('name') or s['id'], 'metric': name})
            for alias in dict.fromkeys(aliases):
                if alias == name or any(in_name.search(alias) or q.search(alias) for _, q, in_name in CONCEPTS):
                    continue
                shared_aliases.setdefault(alias, []).append((s, name))
    for name, holders in names.items():
        if len({s['id'] for s in holders}) > 1:
            found[name] = [{'source_id': s['id'], 'source_name': s.get('name') or s['id'], 'metric': name} for s in holders]
    for alias, holders in shared_aliases.items():
        if len({s['id'] for s, _ in holders}) > 1 and alias not in found:
            found[alias] = [{'source_id': s['id'], 'source_name': s.get('name') or s['id'], 'metric': mname} for s, mname in holders]
    return {c: v for c, v in found.items() if len({x['source_id'] for x in v}) > 1}


def _aliases(source):
    name = str(source.get('name') or '')
    base = re.sub(r'(数据)?库$', '', name)
    out = {name, base, DIALECT_NAMES.get(source.get('dialect'), source.get('dialect') or '')}
    out |= set(re.findall(r'[A-Za-z][A-Za-z0-9_]+|[一-鿿]{2,}', base))
    return {a.lower() for a in out if len(a) >= 2}


def mentioned_sources(text, sources):
    """Ids of sources the text names, using only aliases unique to one source."""
    text = (text or '').lower()
    aliases = {s['id']: _aliases(s) for s in sources}
    hits = set()
    for sid, own in aliases.items():
        others = set().union(*(a for k, a in aliases.items() if k != sid)) if len(aliases) > 1 else set()
        if any(a in text for a in own - others):
            hits.add(sid)
    return hits


def _bigrams(text):
    return {text[i:i + 2] for i in range(len(text) - 1) if re.fullmatch(r'[一-鿿]{2}', text[i:i + 2])}


GENERIC = re.compile(r'金额|数量|总数|次数|合计|平均|算术|总值|比例|情况|实际')


def implied_sources(text, holders, concept_words=None):
    """Sources whose specific metric the question already names: at least two word pairs
    from that metric's name that no other holder's metric shares (资质单位…, 成果质检…).
    The concept words themselves (合格率, 投入…) and generic words never count."""
    pairs = {x['source_id']: set() for x in holders}
    for x in holders:
        name = GENERIC.sub('|', concept_words.sub('|', x['metric']) if concept_words else x['metric'])
        pairs[x['source_id']] |= _bigrams(name)
    hits = set()
    for sid, own in pairs.items():
        others = set().union(*(p for k, p in pairs.items() if k != sid))
        if len({p for p in own - others if p in text}) >= 2:
            hits.add(sid)
    return hits


def asked_concepts(text, found):
    out = []
    for concept, holders in found.items():
        rule = next((q for c, q, _ in CONCEPTS if c == concept), None)
        if (rule and rule.search(text)) or (not rule and concept in text):
            out.append(concept)
    return out


def annotate(sources):
    """Mark colliding metrics in described sources; return the top-level summary."""
    found = collisions(sources)
    for s in sources:
        for key in METRIC_LISTS:
            for m in s.get(key) or []:
                name = m.get('name') or m.get('label')
                others = sorted({x['source_name'] + '：' + x['metric'] for c, v in found.items()
                                 if any(y['source_id'] == s['id'] and y['metric'] == name for y in v)
                                 for x in v if x['source_id'] != s['id']})
                if others:
                    m['also_in'] = others
    if not found:
        return None
    return {'metrics': [{'concept': c, 'sources': [x['source_name'] + '：' + x['metric'] for x in v]} for c, v in found.items()],
            'rule': '这些业务概念在多个库里各有指标，口径不同，不能混用。问题没有说明用哪个库（或哪个具体指标）时，'
                    '先用原生提问让用户选择（选项写「库名：指标名」），不要自己选库，也不要两个都查；回答时写明数据来自哪个库。'}


def validate_source_choice(engine, ctx, source_id, describe):
    """Raise when the question names a colliding concept but not the database."""
    host = getattr(ctx, 'host_evidence', None) or {}
    text, confirmed = host.get('user_text', ''), host.get('confirmation', '')
    if not text:
        return
    sources = [describe(s) for s in engine.catalog(ctx.project)['sources']]
    found = collisions(sources)
    asked = asked_concepts(text, found)
    if not asked:
        return
    by_id = {s['id']: s.get('name') or s['id'] for s in sources}
    said = mentioned_sources(text + '\n' + confirmed, sources)
    key = (ctx.project, getattr(ctx, 'session_id', '') or '')
    with _lock:
        remembered = dict(_choices.get(key, {}))
        for concept in asked:
            holders = found[concept]
            ids = {x['source_id'] for x in holders}
            if source_id not in ids:
                continue
            words = next((n for c, _, n in CONCEPTS if c == concept), None)
            named = (said & ids) or implied_sources(text + '\n' + confirmed, holders, words)
            # The user asked for several databases (by name, or "两个库"): each one is fine.
            if len(named) > 1 or (not named and BOTH.search(text + '\n' + confirmed)):
                if len(named) > 1 and source_id not in named:
                    raise ValueError('SOURCE_MISMATCH: 用户点名的是「%s」，当前计划用的库不在其中。'
                                     % '」「'.join(by_id.get(x, x) for x in named))
                continue
            if len(named) == 1:
                remembered[concept] = next(iter(named))
            choice = remembered.get(concept)
            if choice and choice != source_id:
                metric = next((x['metric'] for x in holders if x['source_id'] == choice), concept)
                raise ValueError('SOURCE_MISMATCH: 用户要的是「%s」的「%s」，当前计划用的是「%s」。请改用「%s」，不要换库。'
                                 % (by_id.get(choice, choice), metric, by_id.get(source_id, source_id), by_id.get(choice, choice)))
            if not choice:
                options = '、'.join(x['source_name'] + '：' + x['metric'] for x in holders)
                raise ValueError('NEEDS_CLARIFICATION: 「%s」在多个库里各有指标，口径不同（%s）。问题没有说明用哪个，'
                                 '请先用原生提问让用户选择（选项就写上面这几项），不要自己选库，也不要两个都查。'
                                 % (concept, options))
        if remembered:
            _choices[key] = remembered
