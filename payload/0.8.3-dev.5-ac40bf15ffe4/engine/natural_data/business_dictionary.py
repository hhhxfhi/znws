"""Project-owned, published business metadata. It never grants access or edits a DB."""
from __future__ import annotations
import copy
import json
import math
import re

KINDS = {'table', 'field', 'term', 'enum', 'time', 'relation'}
FIELDS = {'id', 'kind', 'name', 'description', 'source_id', 'table', 'column', 'synonyms',
          'values', 'grain', 'to_table', 'from_columns', 'to_columns', 'cardinality'}

_UNIT_AND_BASIS = r'\s*([^。；;\n]{1,30})[；;]\s*单位依据[：:]\s*([^\n]{1,300})'
_FIELD_UNIT = re.compile(r'计量单位[：:]' + _UNIT_AND_BASIS)
_ROW_UNIT = re.compile(r'记录计量单位[：:]' + _UNIT_AND_BASIS)
# Older published field descriptions can begin with an explicit unit and a
# comma or a semicolon. Require both the unit token and its explanatory text.
_LEGACY_FIELD_UNIT = re.compile(
    r'^(本|张|件|家次|家|户|项次|项|条|人次|人|个|台|套|笔|部|组|批|宗|份|辆|'
    r'箱|盒|包|瓶|袋|卷|桶|枚|颗|节|架|辆次|(?:人民币)?(?:万|亿)?元|万次|次|'
    r'%|％|个百分点|千?米|平方(?:千)?米|公顷|亩|吨|千克|克)\s*[，,；;]\s*(.+)'
)



def _declared_unit(mapping, kind, pattern):
    if mapping.get('kind') != kind:
        return None
    match = pattern.fullmatch(mapping.get('description') or '')
    if not match or not all(value.strip() for value in match.groups()):
        return None
    return match[1].strip(), match[2].strip()


def declared_field_unit(mapping):
    """Published field descriptions can carry an explicit, sourced unit."""
    if unit := _declared_unit(mapping, 'field', _FIELD_UNIT):
        return unit
    if mapping.get('kind') != 'field':
        return None
    if match := _LEGACY_FIELD_UNIT.match((mapping.get('description') or '').strip()):
        return match[1], '已发布字段说明：' + match[0][:280]
    return None


def declared_row_unit(mapping):
    """A table mapping may declare the unit of one physical row."""
    return _declared_unit(mapping, 'table', _ROW_UNIT)


def _text(value, label, required=False, maximum=1000):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError('业务映射：' + label + '格式无效')
    if required and not value.strip():
        raise ValueError('业务映射：请填写' + label)
    return value.strip()


def validate_mappings(rows, sources, authorized):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('业务映射条目必须为列表且不超过1000项')
    seen, targets, aliases = set(), set(), {}
    result = []
    for number, raw in enumerate(rows, 1):
        if not isinstance(raw, dict) or set(raw) - FIELDS:
            raise ValueError(f'业务映射第{number}项包含不支持的字段')
        row = copy.deepcopy(raw)
        rid = _text(row.get('id'), '标识', True, 100)
        if rid in seen: raise ValueError('业务映射标识重复')
        seen.add(rid)
        kind = row.get('kind')
        if not isinstance(kind,str) or kind not in KINDS: raise ValueError('不支持的业务映射类型')
        row['name'] = _text(row.get('name'), '业务名称', True, 200)
        row['description'] = _text(row.get('description', ''), '业务说明', maximum=3000)
        if kind == 'field' and row['description'].startswith(('计量单位：', '计量单位:')) and not declared_field_unit(row):
            raise ValueError(row['name'] + '：请按“计量单位：单位；单位依据：来源”登记完整单位。')
        if kind == 'table' and row['description'].startswith(('记录计量单位：', '记录计量单位:')) and not declared_row_unit(row):
            raise ValueError(row['name'] + '：请按“记录计量单位：单位；单位依据：来源”登记完整单位。')
        sid = row.get('source_id')
        if not isinstance(sid, str) or sid not in authorized or sid not in sources:
            raise ValueError(row['name'] + '：数据源未授权')
        tables = {t['name']: t for t in sources[sid]['tables'] if t['name'] in authorized[sid]}
        table = row.get('table')
        if not isinstance(table, str) or table not in tables:
            raise ValueError(row['name'] + '：表不存在或未授权')
        columns = {c['name']: c for c in tables[table]['columns']}
        column = row.get('column', '')
        if not isinstance(column,str): raise ValueError('物理字段标识必须为文本')
        if kind not in {'table', 'relation'} and (not isinstance(column, str) or column not in columns):
            raise ValueError(row['name'] + '：字段不存在 ' + str(column))
        target = (kind, sid, table, column)
        if target in targets: raise ValueError(row['name'] + '：同一类型的物理对象重复配置')
        targets.add(target)
        words = row.get('synonyms', [])
        if not isinstance(words, list) or len(words) > 50:
            raise ValueError(row['name'] + '：同义词过多或格式无效')
        row['synonyms'] = [_text(w, '同义词', True, 200) for w in words]
        if len(set(w.casefold() for w in row['synonyms'])) != len(words):
            raise ValueError(row['name'] + '：同义词重复')
        if kind in {'field', 'term'}:
            for word in [row['name'], *row['synonyms']]:
                key = (sid, table, word.casefold())
                if key in aliases and aliases[key] != column:
                    raise ValueError('同义词有歧义：' + word + '对应多个字段，请人工确认')
                aliases[key] = column
        if kind == 'enum':
            values = row.get('values')
            if not isinstance(values, list) or not values or len(values) > 300:
                raise ValueError(row['name'] + '：请填写1—300项枚举映射')
            keys = set()
            for value in values:
                if not isinstance(value, dict) or not {'value','label'} <= set(value) or set(value)-{'value','label','type'}:
                    raise ValueError(row['name'] + '：枚举必须包含原始值与业务名称')
                v = value['value']
                dtype=value.get('type')
                if dtype is not None:
                    if dtype not in ('text','number','boolean'): raise ValueError('枚举编码类型无效')
                    if dtype=='text': v=str(v)
                    elif dtype=='number':
                        if isinstance(v,bool) or not isinstance(v,(str,int,float)) or not str(v).strip(): raise ValueError('枚举数字不能为空')
                        try: v=float(v)
                        except (ValueError,OverflowError): raise ValueError('枚举数字格式无效') from None
                        if math.isfinite(v) and v.is_integer(): v=int(v)
                    elif type(v) is not bool:
                        if v not in ('true','false'): raise ValueError('布尔枚举请填写true或false')
                        v=v=='true'
                    value['value']=v
                if type(v) not in (str, int, float, bool) or (isinstance(v, str) and len(v) > 500):
                    raise ValueError('枚举值必须为明确类型的文本、数字或布尔值')
                if isinstance(v, float) and not math.isfinite(v): raise ValueError('枚举数字必须有限')
                key = (type(v).__name__, json.dumps(v, ensure_ascii=False))
                if key in keys: raise ValueError('枚举原始值重复')
                keys.add(key)
                value['label'] = _text(value['label'], '枚举业务名称', True, 200)
        if kind == 'time' and row.get('grain') not in ('date', 'month', 'year', 'datetime'):
            raise ValueError(row['name'] + '：请选择明确的时间粒度')
        if kind == 'relation':
            other = row.get('to_table')
            if not isinstance(other,str) or other not in tables: raise ValueError(row['name'] + '：关联目标表未授权')
            source_keys, target_keys = row.get('from_columns'), row.get('to_columns')
            if not isinstance(source_keys, list) or not isinstance(target_keys, list) or not 1 <= len(source_keys) <= 10 or len(source_keys) != len(target_keys):
                raise ValueError('关联键必须为等长的1—10个字段')
            if any(not isinstance(c,str) or c not in columns for c in source_keys) or any(not isinstance(c,str) or c not in {x['name'] for x in tables[other]['columns']} for c in target_keys):
                raise ValueError('关联字段不存在或未授权')
            def unique(t, cols):
                return any(set(k) == set(cols) for k in t.get('unique_keys', []) + ([t['primary_key']] if t.get('primary_key') else []))
            if row.get('cardinality') not in ('many_to_one', 'one_to_one'):
                raise ValueError('只支持经唯一键约束的多对一或一对一关系')
            if not unique(tables[other], target_keys) or (row['cardinality'] == 'one_to_one' and not unique(tables[table], source_keys)):
                raise ValueError('关联键缺少数据库唯一约束，不能保证不重复统计')
        result.append(row)
    return result


def apply_mappings(source, rows):
    """Add business metadata to an already filtered authorized source copy."""
    tables = {t['name']: t for t in source['tables']}
    for table in tables.values():
        table.pop('row_unit', None)
    dictionary = []
    for item in rows:
        if item['source_id'] != source['id'] or item['table'] not in tables: continue
        row = copy.deepcopy(item)
        table = tables[row['table']]
        column = next((c for c in table['columns'] if c['name'] == row.get('column')), None)
        if row['kind'] not in {'table', 'relation'} and column is None: continue
        if row['kind'] == 'relation' and row['to_table'] not in tables: continue
        dictionary.append(row)
        if row['kind'] == 'table':
            table.update(label=row['name'], business_description=row.get('description', ''), aliases=row.get('synonyms', []))
            row_unit = declared_row_unit(row)
            if row_unit:
                unit, basis = row_unit
                table['row_unit'] = {'unit': unit, 'basis': basis}
            else:
                table.pop('row_unit', None)
        if row['kind']=='term': column['aliases']=list(dict.fromkeys([*column.get('aliases',[]),row['name'],*row.get('synonyms',[])]))
        if row['kind']=='field':
            column.update(label=row['name'], business_description=row.get('description', ''), aliases=row.get('synonyms', []))
        if row['kind'] == 'enum':
            column['business_values'] = row['values']
            column['business_name'] = row['name']
        if row['kind'] == 'time': column['time_semantics'] = {'name':row['name'], 'grain':row['grain'], 'description':row.get('description', '')}
        description = row['name'] + '对应' + row['table'] + ('.' + row['column'] if row.get('column') else '')
        if row.get('synonyms'): description += '；同义词：' + '、'.join(row['synonyms'])
        if row['kind'] == 'enum': description += '；枚举：' + json.dumps(row['values'], ensure_ascii=False)
        if row['kind'] == 'time': description += '；时间粒度：' + row['grain']
        if row['kind'] == 'relation': description += '；关系：' + ','.join(row['from_columns']) + ' → ' + row['to_table'] + '.' + ','.join(row['to_columns']) + '（' + row['cardinality'] + '）'
        if row.get('description'): description += '；' + row['description']
        source['terms'].append({'id':'mapping-' + row['id'], 'name':row['name'], 'definition':description})
    source['business_dictionary'] = dictionary
    return source
