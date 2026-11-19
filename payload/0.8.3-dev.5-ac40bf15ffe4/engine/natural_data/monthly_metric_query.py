"""Resolve a single annual monthly series from the full published metric catalog."""

import re

from .catalog_view import version_key
from .metric_search import exact_hits, normalize, strip_unit
from .vendor.charts import _extract_explicit_chart


_MONTH = r'(?:按月|逐月|每月|各月|月度)'
_SERIES = re.compile(
    r'(?:(?:请(?:帮我)?|帮我)?(?:统计|查询|查看|展示|看)?\s*)'
    r'(?P<year>20\d{2})\s*年(?:全年)?\s*'
    r'(?P<label>[^。!?;,，\n]{2,70}?)(?:的)?\s*' + _MONTH
    + r'(?:数据|(?:变化)?(?:趋势|走势))(?:是怎样的|如何|怎么样|是多少)?'
)
_MONTH_LIST = re.compile(
    r'(?:请)?\s*(?:帮我|给我)?\s*(?:把|将)?\s*'
    r'(?P<year>20\d{2})\s*年(?:全年)?\s*'
    r'(?P<label>[^。!?;,，\n]{2,70}?)(?:的)?\s*' + _MONTH
    + r'(?:列出|列成表格?|列表|展示|显示|统计)'
)
_ANNUAL_TOTAL = re.compile(
    r'(?:(?:请(?:帮我)?|帮我)?(?:统计|查询|查看|计算|看)?\s*)'
    r'(?P<year>(?:19|20)\d{2})\s*年(?:全年)?\s*'
    r'(?P<label>[^。!?;,，\n]{2,70}?)(?:是?多少|有多少|共多少|总计多少)'
)
_SYNTHETIC_MEASURE_SUFFIX = re.compile(r'\((?:模拟|合成)[日周月季年](?:度)?(?:发生量|累计量|观测量|观测值|统计值)\)$')
_PRESENTATION = re.compile(
    r'(?:(?:并且?|同时)\s*)?(?:请)?\s*(?:用|以|提供|绘制|画)\s*'
    r'(?:横向柱状图|竖向柱状图|柱状图|条形图|折线图|面积图|数据表|表格)'
    r'(?:展示|呈现|输出)?'
)
_YEAR_TOTAL = re.compile(r'(?:并且?|同时)?\s*(?:请)?\s*(?:给出|给我|计算|列出|附上)\s*(?:全年|全年的|年度)(?:合计|总计|总数|总量)')
_COUNT_SUFFIX = {'件数': '件', '条数': '条', '笔数': '笔', '宗数': '宗'}
_ADDITIONAL = re.compile(r'同比|环比|对比|比较|预测|分组|占比|合计|累计|原因|各地|各市|不含|排除|仅限|截至|截止|另外')


def _matches(metric, label):
    unit = metric.get('unit')
    names = {normalize(strip_unit(name, unit)) for name in (
        metric.get('name'), metric.get('label'), *(metric.get('aliases') or ())
    ) if name}
    label = normalize(strip_unit(label, unit))
    if label in names:
        return True
    for suffix, unit in _COUNT_SUFFIX.items():
        if metric.get('unit') == unit and label.endswith(suffix) and _before_count_suffix(label, suffix) in names:
            return True
    return ('办理' in (metric.get('name') or '') and label.endswith('办理量')
            and _before_count_suffix(label, '办理量') in names)


def _before_count_suffix(label, suffix):
    return label.removesuffix(suffix).rstrip().removesuffix('的').rstrip()


def _annual_label(label, unit=None, *, synthetic=False):
    label = normalize(strip_unit(label, unit)).strip()
    if synthetic:
        label = _SYNTHETIC_MEASURE_SUFFIX.sub('', label).rstrip()
    return re.sub(r'(?:的?期间|的)(?=累计)', '', label)


def published_monthly_metric_query(source, host, metrics):
    publication = source.get('business_project') or {}
    question = normalize(host.get('user_text')).strip()
    clauses = [part.strip() for part in re.split(r'[。!?;,，\n]+', question) if part.strip()]
    if (not 1 <= len(clauses) <= 3
            or not host.get('user_ref') or not (source_id := source.get('id'))
            or not publication.get('id') or type(publication.get('version')) is not int):
        return None
    total = _ANNUAL_TOTAL.fullmatch(clauses[0])
    if total and '累计' in total['label']:
        request = total
    else:
        total = None
        request = _SERIES.fullmatch(clauses[0]) or _MONTH_LIST.fullmatch(clauses[0])
        if not request or _ADDITIONAL.search(clauses[0]):
            return None
    total_clauses = sum(bool(_YEAR_TOTAL.fullmatch(clause)) for clause in clauses[1:])
    chart_clauses = sum(bool(_PRESENTATION.fullmatch(clause)) for clause in clauses[1:])
    if (total_clauses > (0 if total else 1) or chart_clauses > 1
            or total_clauses + chart_clauses != len(clauses) - 1):
        return None

    year, label = request['year'], request['label'].strip()
    newest = {}
    for metric in sorted(metrics, key=version_key, reverse=True):
        newest.setdefault(metric['id'], metric)
    if total:
        candidates = [
            metric for metric in newest.values()
            if _annual_label(label, metric.get('unit')) in {
                _annual_label(name, metric.get('unit'), synthetic=source.get('synthetic') is True)
                for name in (metric.get('name'), metric.get('label'), *(metric.get('aliases') or ())) if name
            }
        ]
    else:
        candidates = [metric for metric in newest.values() if _matches(metric, label)]
    if len(candidates) != 1:
        return None
    metric, = candidates
    if any(other['id'] != metric['id'] for other in exact_hits(label, newest.values())):
        return None
    periods = [f'{year}-{month:02}' for month in range(1, 13)]
    operands = metric.get('operands') or []
    aggregations = ('count', 'sum') if total else ('count',)
    if (metric.get('time_mode') != 'period' or metric.get('group_required')
            or (total or total_clauses) and metric.get('additive') is not True
            or len(operands) != 1 or metric.get('relations') or operands[0].get('latest')
            or operands[0].get('aggregation') not in aggregations or metric.get('formula') != operands[0].get('id')
            or not set(periods) <= set(metric.get('coverage') or ())):
        return None

    query = {'source_id': source_id, 'metric_id': metric['id'], 'metric_version': metric['version'],
             'periods': periods, 'title': f"{year}年逐月{metric['name']}"}
    if chart := _extract_explicit_chart(question):
        query['chart_type'] = chart
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source_id,
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': query, **({'delivery': 'annual_total'} if total else {})}
