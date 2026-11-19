"""Plan one observed month series for a named two-year count comparison."""
import re

from sqlglot import exp

from .monthly_sql_scope import _has_date
from .retrieval import normalize
from .vendor.dialect import sqlglot_name


_YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*年(?:度)?')
_MONTHS = re.compile(r'1\s*月\s*(?:至|到|[-—–])\s*12\s*月|全年')
_ALLOWED = re.compile(r'(?:请|帮我|统计|查询|对比|比较|按月(?:份)?|逐月|每月|各月|的|和|与|及|'
                      r'用|以|把|将|两个?年(?:度|份)?|分别|并列|分列|两列|'
                      r'展示|呈现|列出|给出|数据表|表格|\s|[，,。！？!?、：:])*')


def plan(source, host):
    quote = host.get('user_text') or ''
    text = normalize(quote).strip()
    publication = source.get('business_project') or {}
    dialect = sqlglot_name(source.get('dialect'))
    if (not host.get('user_ref') or host.get('clarifications') or not publication.get('id')
            or not publication.get('version') or dialect not in ('sqlite', 'postgres')
            or not re.search(r'按月|逐月|每月', text) or not re.search(r'比较|对比', text)
            or not re.search(r'并列|分列|两列', text) or not _MONTHS.search(text)):
        return None
    years = sorted(set(_YEAR.findall(text)))
    if len(years) != 2:
        return None
    remaining = _MONTHS.sub(' ', _YEAR.sub(' ', text))
    mappings = [m for m in source.get('business_dictionary', []) if m.get('source_id') == source['id']]
    candidates = []
    for mapping in mappings:
        label = mapping.get('name')
        if mapping.get('kind') != 'table' or not isinstance(label, str) or len(label) < 3:
            continue
        count_name = re.escape(normalize(label)) + r'(?:件数|条数|记录数|数量|数)'
        if re.search(count_name, remaining) and _ALLOWED.fullmatch(re.sub(count_name, ' ', remaining, count=1)):
            candidates.append(mapping)
    if len(candidates) != 1:
        return None
    mapping, = candidates
    table = next((item for item in source['tables'] if item['name'] == mapping.get('table')), None)
    # A documented time axis and unique entity key make each row one countable
    # record. Ambiguous axes and other subject qualifiers keep the normal route.
    if not table or not table.get('primary_key'):
        return None
    dates = [item['column'] for item in mappings if item.get('kind') == 'time'
             and item.get('grain') == 'date' and item.get('table') == table['name']
             and _has_date(table, item['column'])]
    if len(set(dates)) != 1:
        return None
    ident = lambda name: exp.to_identifier(name, quoted=True).sql(dialect=dialect)
    field = ident(dates[0])
    relation = '.'.join(map(ident, table['name'].split('.')))
    period = f"STRFTIME('%Y-%m', {field})" if dialect == 'sqlite' else f"TO_CHAR({field}, 'YYYY-MM')"
    cast = '' if dialect == 'sqlite' else 'DATE '
    ranges = [f"({field} >= {cast}'{y}-01-01' AND {field} < {cast}'{int(y)+1}-01-01')" for y in years]
    if int(years[1]) == int(years[0])+1:
        ranges = [f"{field} >= {cast}'{years[0]}-01-01' AND {field} < {cast}'{int(years[1])+1}-01-01'"]
    label = mapping['name'] + '数'
    sql = f'SELECT {period} AS "月份", COUNT(1) AS {ident(label)} FROM {relation} WHERE ' + ' OR '.join(ranges) + ' GROUP BY 1 ORDER BY 1'
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'quote': quote,
            'source_id': source['id'], 'connection_revision': source.get('connection_revision', 0),
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'sql': sql,
                              'title': f'{years[0]} 年和 {years[1]} 年{label}逐月比较', 'purpose': 'answer',
                              **({'chart_type': 'table'} if re.search(r'表格|数据表', text) else {})}}
