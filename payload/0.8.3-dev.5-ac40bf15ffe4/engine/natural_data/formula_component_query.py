"""Plan a directly provable amount from an explicit, partly available formula."""
import re
import unicodedata

from .query_evidence import _ONLY_RATIO, _OTHER_RATIO_REQUEST, _amount_name, _amount_subject
from .semantic import q, table


_GROUPED_YEAR = re.compile(
    r'(?:请(?:帮我)?)?(?:计算|查询|统计|给出)?\s*(?P<year>20\d{2})年\s*'
    r'(?:各|每个|按)(?P<group>[\w]{2,24}?)的[\w]{2,35}(?:率|占比|百分比)\s*$'
)
_DATE_NAME = re.compile(r'日期|(?:^|_)date(?:$|_)|(?:^|_)day(?:$|_)', re.I)
_SHARED_IDENTITY = re.compile(r'(?:编号|标识|号)$|_(?:id|key|code|no)$', re.I)
_NUMERIC = re.compile(r'INT|REAL|DOUBLE|FLOAT|DECIMAL|NUMERIC', re.I)
_TEXT = re.compile(r'TEXT|CHAR|CLOB', re.I)


def _field_names(relation, column, source):
    names = [column['name'], column.get('label'), column.get('business_name'), *(column.get('aliases') or [])]
    names.extend(row.get('name') for row in source.get('business_dictionary', [])
                 if row.get('kind') == 'field' and row.get('source_id') == source.get('id')
                 and row.get('table') == relation['name'] and row.get('column') == column['name'])
    return {_amount_name(name) for name in names if name}


def _group_link(fact, axis):
    if fact is axis:
        return None
    primary = axis.get('primary_key') or []
    if len(primary) != 1:
        return None
    target, = primary
    fact_columns = {column['name']: column for column in fact['columns']}
    declared = {tuple(fk.get('constrained_columns') or ()) for fk in fact.get('foreign_keys', [])
                if fk.get('referred_table') in (axis['name'], axis['name'].split('.')[-1])
                and fk.get('referred_columns') == primary}
    if declared:
        return next(((key[0], target) for key in declared
                     if len(declared) == len(key) == 1 and key[0] in fact_columns), None)
    # An unambiguous shared named identifier may join to a physical single-column
    # primary key. Keep unmatched facts in an unassigned group.
    if target not in fact_columns or not _SHARED_IDENTITY.search(target) or target in (fact.get('primary_key') or []):
        return None
    key = next((col for col in axis['columns'] if col['name'] == target), None)
    if key and str(key.get('type') or '').upper() == str(fact_columns[target].get('type') or '').upper():
        return target, target
    return None


def formula_component_query(source, host):
    """Return an authorized, single-year amount query when all roles are unique."""
    publication = source.get('business_project') or {}
    question = unicodedata.normalize('NFKC', host.get('user_text') or '').strip()
    if source.get('dialect') != 'sqlite' or not host.get('user_ref') or host.get('clarifications'):
        return None
    match = _ONLY_RATIO.fullmatch(question)
    if not match or _OTHER_RATIO_REQUEST.search(match[1]):
        return None
    heading = _GROUPED_YEAR.fullmatch(match[1])
    numerator, denominator = (_amount_name(part) for part in match.groups()[1:])
    if not heading or numerator == denominator or not (_amount_subject(numerator) and _amount_subject(denominator)):
        return None
    # Special time, product, region or state qualifications belong to normal
    # scope planning. Only an unqualified annual grouping fits this recipe.
    if re.search(r'财政年度|财年|仅|只|剔除|不含|排除|期间|按.+(?:日期|时间)', question):
        return None
    if not publication.get('id') or not publication.get('version'):
        return None
    group = heading['group']
    fields = [(relation, col, _field_names(relation, col, source))
              for relation in source.get('tables', []) for col in relation.get('columns', [])]
    if any(denominator in names for _, _, names in fields):
        return None
    amounts = [(relation, col) for relation, col, names in fields if numerator in names]
    groups = [(relation, col) for relation, col, names in fields if group in names]
    if len(amounts) != 1 or len(groups) != 1:
        return None
    (fact, amount), = amounts
    (axis, dimension), = groups
    dates = [(col, names) for relation, col, names in fields if relation is fact
             and (re.match(r'DATE|TIMESTAMP|DATETIME', str(col.get('type') or ''), re.I)
                  or any(_DATE_NAME.search(name) for name in names))]
    if len(dates) != 1 or not _TEXT.search(str(dimension.get('type') or '')):
        return None
    (date, _), = dates
    sql = ' FROM ' + table(fact['name']) + ' AS f'
    dimension_sql = 'f.' + q(dimension['name'])
    if axis is not fact:
        link = _group_link(fact, axis)
        if not link:
            return None
        left, right = link
        sql += ' LEFT JOIN ' + table(axis['name']) + ' AS g ON f.' + q(left) + ' = g.' + q(right)
        dimension_sql = 'g.' + q(dimension['name'])
    amount_sql = 'f.' + q(amount['name'])
    dtype = str(amount.get('type') or '')
    if _TEXT.search(dtype):
        amount_sql = 'nw_decimal(' + amount_sql + ', 0)'
    elif not _NUMERIC.search(dtype):
        return None
    year = int(heading['year'])
    stored = "REPLACE(TRIM(f." + q(date['name']) + "), '/', '-')"
    business_day = f'SUBSTR({stored}, 1, 10)'
    # Check the timestamp but bucket by the stored business day. An offset
    # near midnight must not move a reported business date into another year.
    sql += (f" WHERE DATE({stored}) IS NOT NULL AND DATE({business_day}, '+0 days') = {business_day}"
            f" AND {business_day} >= '{year}-01-01' AND {business_day} < '{year+1}-01-01'"
            ' GROUP BY ' + dimension_sql + ' ORDER BY ' + q(group))
    sql = 'SELECT ' + dimension_sql + ' AS ' + q(group) + ', SUM(' + amount_sql + ') AS ' + q(numerator) + sql
    return {'schema_version': 1, 'request_ref': host['user_ref'], 'source_id': source['id'],
            'publication': {key: publication[key] for key in ('id', 'version')},
            'year': year, 'date_label': date.get('label') or date['name'],
            'numerator': numerator, 'denominator': denominator,
            'query_request': {'source_id': source['id'], 'title': f'{year}年各{group}已记录{numerator}',
                              'sql': sql, 'purpose': 'answer'}}
