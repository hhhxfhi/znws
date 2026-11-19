"""Plan a published comparison separately from a second metric choice."""

import hashlib
import json
import re
import unicodedata

from .metric_search import normalize, strip_unit


_ANNUAL_COMPARISON = re.compile(
    r'(?:请(?:帮我)?(?:查询|统计|比较)?|帮我(?:查询|统计|比较)?)?'
    r'(?P<current>20\d{2})年\s*(?P<label>[\u4e00-\u9fff]{2,36}?)\s*'
    r'(?:比|与)\s*(?P<baseline>去年|上年|20\d{2}年)\s*'
    r'(?:相比)?(?:高还是低|多还是少|变化多少|增减多少|相差多少|(?:的)?变化(?:幅度)?(?:有多大|是多少))'
)
_CHANGE = re.compile(r'(?:变化(?:幅度|率)?|增减(?:幅度|量)?|差额|增长率|降幅|涨幅)(?:有多大|是多少|多少|如何)')
_SECOND_METRIC = re.compile(
    r'(?:(?:另外|此外)[,，]?\s*|(?:再|并)(?:查询|统计))?'
    r'(?P<year>20\d{2})年\s*(?P<label>[\u4e00-\u9fff]{2,32}?)(?:是多少|有多少|多少)'
)
_MONTHLY_COMPARISON = re.compile(
    r'(?:请(?:帮我)?(?:查询|统计|比较)?|帮我(?:查询|统计|比较)?)?'
    r'(?P<current>20\d{2}年\d{1,2}月)\s*(?P<label>[\u4e00-\u9fff]{2,36}?)\s*'
    r'比\s*(?P<baseline>(?:20\d{2}年)?\d{1,2}月)\s*(?:变化多少|增减多少|相差多少|多还是少)'
)
_GROUP_COMPARISON = re.compile(
    r'(?P<groups>[\u4e00-\u9fff]{1,24}(?:(?:和|与|及|、)[\u4e00-\u9fff]{1,24}){1,5}?)'
    r'(?P<count>[两二三四五六2-6])(?:类|组)\s*(?:在)?(?P<current>(?:20\d{2}年)?\d{1,2}月)'
    r'(?:分别|各自|各)占(?:总量|总数|总额)多少[,、]\s*'
    r'(?:较|比)(?P<baseline>(?:20\d{2}年)?\d{1,2}月)(?:分别|各自|各)(?:增减|变化)多少[,、]\s*'
    r'(?:总变化|总体变化|总量变化|净变化)主要来自(?:哪一类|哪类|哪个类别|哪一组)'
)
_GROUP_COUNT = dict(zip('两二三四五六23456', (2, 2, 3, 4, 5, 6, 2, 3, 4, 5, 6)))


def _month(value, year=None):
    match = re.fullmatch(r'(?:(20\d{2})年)?(\d{1,2})月', value)
    if match and (year := match[1] or year) and 1 <= (number := int(match[2])) <= 12:
        return f'{year}-{number:02}'


def independent_clauses(text):
    text = unicodedata.normalize('NFKC', str(text or '')).strip()
    clauses = [part.strip() for part in re.split(r'[。!?;\n]+', text) if part.strip()]
    if len(clauses) not in (2, 3) or not (other := _SECOND_METRIC.fullmatch(clauses[-1])):
        return None
    if (comparison := _ANNUAL_COMPARISON.fullmatch(clauses[0])) and (
            len(clauses) == 2 or _CHANGE.fullmatch(clauses[1])):
        current, baseline = comparison['current'], comparison['baseline']
        baseline = str(int(current) - 1) if baseline in ('去年', '上年') else baseline[:4]
        if current != baseline:
            return comparison['label'], (baseline, current), other['label'], other['year'], ()
    if len(clauses) != 3 or not (comparison := _MONTHLY_COMPARISON.fullmatch(clauses[0])) or not (
            grouped := _GROUP_COMPARISON.fullmatch(clauses[1])):
        return None
    current = _month(comparison['current'])
    if not current or not (baseline := _month(comparison['baseline'], current[:4])) or current == baseline:
        return None
    if '年' not in comparison['baseline'] and baseline >= current:
        return None
    if _month(grouped['current'], current[:4]) != current or _month(grouped['baseline'], baseline[:4]) != baseline:
        return None
    groups = tuple(re.split(r'和|与|及|、', grouped['groups']))
    if len(set(groups)) == len(groups) == _GROUP_COUNT[grouped['count']]:
        return comparison['label'], (baseline, current), other['label'], other['year'], groups


def _metric_name(value):
    return re.sub(r'\s+', '', normalize(value)).replace('的', '')


def _matches(metric, label):
    names = (strip_unit(metric.get('name'), metric.get('unit')), strip_unit(metric.get('label'), metric.get('unit')),
             *(metric.get('aliases') or ()))
    return any(_metric_name(name) == _metric_name(label) for name in names)


def _choice_label(metric):
    name = metric['name']
    bare = strip_unit(name, metric.get('unit'))
    return bare if bare and _metric_name(bare) != _metric_name(name) else name


def _newest_covering(metrics, periods):
    from .catalog_view import version_key

    selected = {}
    for metric in sorted(metrics, key=version_key, reverse=True):
        selected.setdefault(metric['id'], metric)
    return [metric for metric in selected.values()
            if metric.get('time_mode') == 'period' and set(periods) <= set(metric.get('coverage') or ())]


def _published_group(source, metric, groups, label):
    """Bind the complete named category set to one published metric dimension."""
    operands = metric.get('operands') or []
    if (len(operands) != 1 or metric.get('relations') or operands[0].get('latest')
            or operands[0].get('aggregation') != 'count' or metric.get('formula') != operands[0]['id']):
        return None
    table = next((item for item in source.get('tables', []) if item['name'] == operands[0]['table']), None)
    if not table:
        return None
    columns = {column['name']: column for column in table['columns']}
    matching = []
    for dimension in metric.get('dimensions') or []:
        if dimension['table'] != table['name'] or not (column := columns.get(dimension['column'])):
            continue
        enums = column.get('business_values') or []
        values = [item.get('value') for item in enums]
        if (len(values) != len(groups) or any(not isinstance(value, str) or not value.strip() or len(value) > 100
                                               for value in values) or len(set(values)) != len(groups)):
            continue
        labels = {_metric_name(item['label']) for item in enums}
        # A concise label can omit the same metric noun after every category.
        # Require the exact full set and a shared suffix present in the metric alias.
        first = _metric_name(groups[0])
        suffixes = [item[len(first):] for item in labels if item.startswith(first)]
        if any((not suffix or len(suffix) >= 2 and suffix in _metric_name(label))
               and {_metric_name(group) + suffix for group in groups} == labels for suffix in suffixes):
            matching.append((dimension['id'], values))
    if len(matching) == 1:
        return matching[0]


def independent_metric_comparison(source, host, metrics):
    """Use the complete authorized catalog and the host-owned user request."""
    host = host or {}
    clauses = independent_clauses(host.get('user_text'))
    publication = source.get('business_project') or {}
    request_ref = host.get('user_ref')
    source_id = source.get('id')
    if not clauses or not request_ref or not source_id or not publication.get('id') or not publication.get('version'):
        return None
    comparison, (baseline, current), second, year, groups = clauses
    candidates = _newest_covering([metric for metric in metrics if _matches(metric, comparison)], (baseline, current))
    if len(candidates) != 1:
        return None
    metric, = candidates
    if not metric.get('additive') or metric.get('unit') == '%' or metric.get('group_required'):
        return None
    grouping = _published_group(source, metric, groups, comparison) if groups else None
    if groups and not grouping:
        return None
    from .business_ambiguity import matching_rules, reference_issues

    options = []
    matching = list(matching_rules(source, second))
    if matching:
        if len(matching) != 1:
            return None
        (term, rule), = matching
        if not term.get('clarification') or any(issue['term_id'] == term.get('id') for issue in reference_issues(source)):
            return None
        active = {m['id']: m for m in _newest_covering(metrics, (year,))}
        for choice in rule['choices']:
            ids = choice.get('metric_ids') or ()
            if len(ids) != 1 or ids[0] not in active:
                return None
            options.append((choice['label'], active[ids[0]]))
    else:
        options = [(_choice_label(m), m) for m in _newest_covering([m for m in metrics if _matches(m, second)], (year,))]
    if (not 2 <= len(options) <= 4 or len({name for name, _ in options}) != len(options)
            or len({m['id'] for _, m in options}) != len(options) or any(m['id'] == metric['id'] for _, m in options)
            or any(m.get('group_required') for _, m in options)):
        return None

    def query(item, periods):
        return {'source_id': source_id, 'metric_id': item['id'], 'metric_version': item['version'],
                'periods': list(periods), 'title': item['name']}

    choices = [{'label': label, 'query_request': query(item, (year,))} for label, item in options]
    if any(len(item['label']) > 100 or not re.search(r'[\u4e00-\u9fff]', item['label']) for item in choices):
        return None
    identity = [request_ref, source_id, publication['id'], publication['version'], second, year, choices]
    signature = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    comparison_query = query(metric, (baseline, current))
    compared = {'query_request': comparison_query, 'baseline': baseline, 'current': current}
    if grouping:
        dimension, categories = grouping
        comparison_query['group_by'] = [dimension]
        compared['categories'] = categories
    return {'schema_version': 1, 'request_ref': request_ref, 'source_id': source_id,
            'publication': {key: publication[key] for key in ('id', 'version')},
            'comparison': compared,
            'choice': {'id': 'metric-choice-' + signature, 'header': '确认统计口径',
                       'question': f'“{year}年{second}”按哪一种口径统计？', 'options': choices}}


def comparison_choice_context(engine, ctx, source, metric_id, version, periods, groups, filters):
    """An unresolved separate term must not affect an exactly bound metric."""
    from .business_ambiguity import matching_rules

    host = ctx.host_evidence or {}
    text = host.get('user_text') or ''
    if filters or len(periods) != 2 or not any(matching_rules(source, text)):
        return host
    clauses = independent_clauses(text)
    if not clauses:
        return host
    from .catalog_view import enrich_metrics
    from .onboarding import describe

    sources = engine.catalog(ctx.project)['sources']
    if len(sources) != 1 or sources[0]['id'] != source['id']:
        return host
    described = describe(sources[0])
    plan = independent_metric_comparison(described, host, enrich_metrics(described))
    if not plan:
        return host
    query = plan['comparison']['query_request']
    if (metric_id != query['metric_id'] or version != query['metric_version']
            or set(periods) != set(query['periods']) or list(groups or []) != query.get('group_by', [])):
        return host
    return {**host, 'user_text': clauses[0]}
