"""Check the population and professional segment of a published qualification snapshot."""
import re
import unicodedata

import sqlglot
from sqlglot import exp

from .catalog_index import fingerprint
from .catalog_view import entry_code
from .snapshot_scope import _historical_request, _physical_table, _snapshot_tables
from .vendor.dialect import sqlglot_name

PROVINCE = 'province_scope'
PROFESSION = 'grade_profession_scope'
DUAL = 'published_dual_grade_scope'
KINDS = {PROVINCE, PROFESSION, DUAL}
_CITY = re.compile(r'[一-鿿]{2,5}市')
_CITY_ROLE = re.compile(r'(?:所在|所属)?(?:地市|地级市|城市)(?:名称)?')
_GRADE_ROLE = re.compile(r'(?:测绘)?资质等级|资格等级')
_PROFESSION_ROLE = re.compile(r'(?:专业类别|资质专业|专业范围)')
_ENTITY_ROLE = re.compile(r'(?:申请|持证|资质|测绘)?单位(?:名称|编号|标识|唯一键)|企业名称|机构名称')
_ENTITY_ID_ROLE = re.compile(r'(?:申请|持证|资质|测绘)?(?:单位|企业|机构)(?:编号|标识|唯一键)')
_GRADE = re.compile(r'甲级|乙级')
_DUAL_GRADES = {'甲级乙级', '甲乙级'}
_DUAL = re.compile(r'甲\s*级?\s*(?:和|与|及|和同时|、|/|\+)?\s*乙\s*级?[^。；\n]{0,8}(?:双证|双资质|双等级)|'
                   r'(?:同时|均|都)\s*(?:有效)?持有\s*甲级\s*(?:和|与|及|、|/|\+)\s*乙级')
_EXACT = re.compile(r'(?:按|依照|采用|遵循)\s*(?:(?:现行|当前|业务|已|的)\s*)*(?:登记|注册|发布|既有|现有)\s*(?:的|业务)?\s*(?:统计)?口径')
_CHANGE = re.compile(r'(?:不再|不必|不用|不要|不按|脱离|忽略|改用|按新|采用新|另按|自定义|合并|计入|纳入|也算|包括|修改)'
                     r'[^。；\n]{0,10}(?:口径|写法|取值|甲乙级|甲级乙级)')
_NAMED_CITY = re.compile(r'(?:^|[，,、；。\n]|统计|查询|核算|按|查|在|看|给出|全|当前|目前)'
                         r'\s*([一-鿿]{2,5}市)(?!级|场|政府)')
_NULL_LABELS = {'', 'null', '未注明', '地区未注明'}
_FUZZY = (exp.Like, exp.ILike, exp.Glob, exp.RegexpLike, exp.RegexpILike)


def _atoms(node):
    node = _unwrap(node)
    if isinstance(node, exp.And):
        return [*_atoms(node.this), *_atoms(node.expression)]
    return [node] if node is not None else []


def _unwrap(node):
    return node.unnest() if isinstance(node, exp.Expression) else node


def _where(select):
    where = select.args.get('where')
    return _atoms(where.this if where else None)


def _literal(node):
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
    return None


def _membership(node, column):
    def pin(value):
        if isinstance(value, exp.Literal) and value.is_int:
            return int(value.this)
        return _literal(value)
    if isinstance(node, exp.EQ):
        for left, right in ((node.this, node.expression), (node.expression, node.this)):
            if (name := column(left)) and (value := pin(right)) is not None:
                return name, {value}
    elif isinstance(node, exp.In) and not node.args.get('query') and (name := column(node.this)):
        values = {pin(item) for item in node.expressions}
        if values and None not in values:
            return name, values
    return None


def _select_population(tree, source, dialect):
    """Recognize a single real table, optionally projected through one direct CTE."""
    if not isinstance(tree, exp.Select) or tree.find(exp.Join, exp.Subquery, exp.SetOperation):
        return None
    ctes = list(tree.find_all(exp.CTE))
    if len(ctes) > 1:
        return None
    inner = ctes[0].this if ctes else tree
    outer_from = tree.args.get('from')
    physical_from = inner.args.get('from') if isinstance(inner, exp.Select) else None
    if not outer_from or not physical_from or not (table := _physical_table(physical_from.this, source, dialect)):
        return None
    if ctes and (not isinstance(outer_from.this, exp.Table)
                 or outer_from.this.db or outer_from.this.name != ctes[0].alias_or_name
                 or ctes[0].alias_column_names or any(inner.args.get(k) for k in ('group', 'having', 'limit', 'offset', 'distinct'))):
        return None
    return inner, physical_from.this, table


def _column(table, relation):
    fields = {c['name'].casefold(): c['name'] for c in table['columns']}

    def resolve(node):
        node = _unwrap(node)
        if (isinstance(node, exp.Column) and (not node.table or node.table.casefold() == relation.alias_or_name.casefold())
                and (not node.db or node.db == relation.db) and (not node.catalog or node.catalog == relation.catalog)):
            return fields.get(node.name.casefold())
        return None
    return resolve


def _registered_examples(source, dialect, tables):
    terms = {}
    for item in source.get('terms', []):
        if (item.get('source_id') == source['id'] and item.get('status') != 'review' and item.get('origin')
                and (code := entry_code(item, 'name'))):
            terms.setdefault((item['origin'], code), []).append(item)
    for item in source.get('examples', []):
        key = item.get('origin'), entry_code(item, 'question')
        paired = terms.get(key, ())
        if item.get('source_id') != source['id'] or len(paired) != 1:
            continue
        term = paired[0]
        if not re.search(r'资质[^\n]{0,16}单位|单位[^\n]{0,16}资质', term.get('name') or ''):
            continue
        try:
            tree = sqlglot.parse_one(item['logical_sql'], read=dialect)
        except sqlglot.errors.SqlglotError:
            continue
        if (shape := _select_population(tree, source, dialect)) and shape[2] in tables:
            yield term, item, tree, shape


def _snapshot_bindings(source):
    if not source.get('business_project', {}).get('version'):
        return {}
    dialect = sqlglot_name(source['dialect'])
    declared, _ = _snapshot_tables(source, dialect)
    tables = {table['name']: table for table in source.get('tables', []) if table['name'] in declared}
    roles = {name: {'city': set(), 'grade': set(), 'composite': set(), 'entity': set(), 'grades': {},
                    'examples': [], 'dual': []}
             for name in tables}
    for name, table in tables.items():
        bindings = roles[name]
        keys = [table['primary_key']] if table.get('primary_key') else []
        keys += table.get('unique_keys') or []
        bindings['entity'].update(key[0] for key in keys if len(key) == 1)
        for col in table['columns']:
            labels = (col.get('label'), col.get('business_name'), *(col.get('aliases') or []))
            if any(label and _CITY_ROLE.fullmatch(label) for label in labels):
                bindings['city'].add(col['name'])
            if any(label and _GRADE_ROLE.fullmatch(label) for label in labels):
                bindings['grade'].add(col['name'])
            if any(label and _ENTITY_ROLE.fullmatch(label) for label in labels):
                bindings['entity'].add(col['name'])
            if (any(label and _PROFESSION_ROLE.fullmatch(label) for label in labels)
                    and re.search(r'分(?:等)?级|分段', col.get('business_description') or '')
                    and '甲级' in col['business_description'] and '乙级' in col['business_description']):
                bindings['composite'].add(col['name'])
    for term, example, tree, (inner, relation, table) in _registered_examples(source, dialect, tables):
        bindings = roles[table]
        column = _column(tables[table], relation)
        definition = term.get('definition') or ''
        types = set(_GRADE.findall(term['name']))
        dual_definition = bool(_DUAL.search(term['name']) and _DUAL.search(definition))
        dual_pins, other_pins = [], []
        for atom in _where(inner):
            membership = _membership(atom, column)
            if not membership:
                continue
            name, values = membership
            if all(isinstance(value, str) and _CITY.fullmatch(value) and value in definition for value in values):
                bindings['city'].add(name)
            if dual_definition:
                field = next(col for col in tables[table]['columns'] if col['name'] == name)
                confirmed_codes = {v.get('value') for v in field.get('business_values', [])
                                   if isinstance(v.get('label'), str) and (_DUAL.search(v['label']) or v['label'] in _DUAL_GRADES)}
                if values and (values <= _DUAL_GRADES or values <= confirmed_codes):
                    dual_pins.append((name, values))
                else:
                    other_pins.append((name, values))
            if values <= {'甲级', '乙级', *_DUAL_GRADES} and len(types) == 1 and next(iter(types)) in values:
                bindings['grade'].add(name)
                grade = next(iter(types))
                if any(value in _DUAL_GRADES for value in values) and re.search(r'(?<!不)含甲乙双证|(?<!不)含甲乙双资质', definition):
                    bindings['grades'].setdefault((name, grade), set()).update(values)
        if (inner is tree and len(dual_pins) == 1 and len(dual_pins) + len(other_pins) == len(_where(tree))
                and _simple_count(tree, column, bindings['entity']) and (unit := re.match(r'单位[：:]\s*(家|户|个)(?=[。.;；\s]|$)', definition))):
            name, values = dual_pins[0]
            bindings['grade'].add(name)
            bindings['dual'].append((name, values, unit[1], example, other_pins))
        segments = {}
        if re.search(r'专业.*分段', definition):
            for projection in inner.expressions:
                value = projection.unalias()
                if (name := column(_segment(value, '甲级'))) is not None or (name := column(_segment(value, '乙级'))) is not None:
                    bindings['composite'].add(name)
                    segments[projection.alias_or_name] = name
        if segments and len(types) == 1:
            for atom in _where(tree):
                if (isinstance(atom, exp.Like) and isinstance(atom.this, exp.Column)
                        and atom.this.name in segments and (value := _literal(atom.expression))
                        and re.fullmatch(r'%[^%_]+%', value) and value[1:-1] in definition):
                    bindings['examples'].append((next(iter(types)), value[1:-1], example))
    return {name: bindings for name, bindings in roles.items()
            if len(bindings['grade']) == 1 and (bindings['dual'] or len(bindings['city']) == 1 or len(bindings['composite']) == 1)}


def _requested(text):
    text = unicodedata.normalize('NFKC', text or '')
    if (not (re.search(r'资质', text) and '单位' in text and re.search(r'数量|多少|几家|单位数|统计', text))
            or re.search(r'预测|未来|名单|排行|最多|同比|环比|新增|注销|分别|对比|证书数', text)):
        return []
    if _EXACT.search(text) and _DUAL.search(text) and not _CHANGE.search(text):
        return text, DUAL
    grades = set(_GRADE.findall(text))
    if len(grades) != 1 or re.search(r'仅持|纯甲级|纯乙级|不含甲乙|排除甲乙|仅限甲级|仅限乙级', text):
        return []
    return text, next(iter(grades))


def _intent(text):
    if not (request := _requested(text)):
        return None
    text, grade = request
    if grade == DUAL:
        return (DUAL,), grade
    constraints = []
    if (re.search(r'全(?:广东)?省|本省', text)
            and not re.search(r'排除|不包括|不含|只看|仅看|仅统计|地市(?:有值|非空|不为空)|[一-鿿]{2,5}市(?:的|内|范围)', text)):
        constraints.append(PROVINCE)
    if re.search(r'专业|业务范围', text):
        constraints.append(PROFESSION)
    return (constraints, grade) if constraints else None


def _dual_reference(bindings, text):
    candidates = [(name, *definition) for name, entry in bindings.items() for definition in entry['dual']]
    by_code, by_name = [], []
    for candidate in candidates:
        example = candidate[4]
        code = entry_code(example, 'question')
        # Match the registered name without the leading code and example date.
        label = re.split(r'[（(][^）)]*示例', example['question'], maxsplit=1)[0].split('：', 1)[-1].strip()
        if code and re.search(r'(?<![A-Za-z0-9_-])' + re.escape(code) + r'(?![A-Za-z0-9_-])', text, re.I):
            by_code.append(candidate)
        if label and label in text:
            by_name.append(candidate)
    return by_code or by_name, candidates


def _registered_cities(reference, bindings):
    table, _, _, _, _, filters = reference
    if not filters:
        return frozenset()
    # Other business predicates are not city defaults.
    if len(filters) == 1:
        field, values = filters[0]
        if field in bindings[table]['city'] and all(isinstance(value, str) for value in values):
            return frozenset(values)
    return None


def _constraints(source, reference, bindings, kinds, grade):
    if grade == DUAL:
        text = reference.get('quote') or ''
        requested, candidates = _dual_reference(bindings, text)
        pins = {(name, field, frozenset(values), unit) for name, field, values, unit, _, _ in (requested or candidates)}
        if not pins:
            return []
        if len(pins) != 1:
            return [{'kind': DUAL, 'source_id': source['id'], 'evidence': reference, 'state': 'ambiguous'}]
        name, field, values, unit = next(iter(pins))
        labels = {label for candidate in bindings[name]['dual'] for _, values in candidate[4]
                  for label in values if isinstance(label, str) and label in text and _CITY.fullmatch(label)}
        # Prefer a city explicitly named by a matching published example.
        if labels:
            named = labels | {name for name in _NAMED_CITY.findall(text) if not any(name.endswith(known) for known in labels)}
        else:
            named = set(_NAMED_CITY.findall(text))
        registered = _registered_cities(requested[0], bindings) if len(requested) == 1 else None
        if not named and registered is not None and not re.search(r'全省|全市|全量|全部地市|各地市|所有地市|不限地区|不限定地市', text):
            named = set(registered)
        return [{'kind': DUAL, 'table': name, 'column': field, 'values': sorted(values, key=str), 'unit': unit,
                 'cities': sorted(named), 'source_id': source['id'], 'evidence': reference}]
    return [{'kind': kind, 'table': name, 'grade': grade, 'source_id': source['id'], 'evidence': reference}
            for name, fields in bindings.items()
            for kind in kinds if len(fields['city' if kind == PROVINCE else 'composite']) == 1]


def derive(source, text, reference):
    if not (intent := _intent(text)):
        return []
    return _constraints(source, reference, _snapshot_bindings(source), *intent)


def catalog_guidance(source, host, *, automatic=False):
    text = host.get('user_text') or ''
    from .holding_rate_scope import derive as derive_holding_rate, guidance as holding_rate_guidance
    if rate := derive_holding_rate(source, host, {'user_ref': host.get('user_ref'), 'quote': text}):
        return holding_rate_guidance(source, rate, automatic=automatic)
    if not (intent := _intent(text)):
        return None
    bindings = _snapshot_bindings(source)
    constraints = _constraints(source, {'user_ref': host.get('user_ref'), 'quote': text}, bindings, *intent)
    if intent[1] == DUAL:
        return _dual_guidance(constraints, bindings, text)
    if not constraints:
        return None
    if any(c['kind'] == PROVINCE for c in constraints):
        return {'instruction': '全省问题用同一授权快照、相同等级和单位去重键统计总数。保留地市为空的记录，并单列为地区未注明。'
                               '无法从快照确认全省总体完整性。'}
    matches = [example for c in constraints for grade, profession, example in bindings[c['table']]['examples']
               if grade == c['grade'] and profession in text]
    if len(matches) == 1:
        return {'published_example': matches[0]['logical_sql'],
                'instruction': '沿用已发布示例中该等级的专业分段。按用户要求核对地区、统计对象和去重键，再正式查询。'
                               '不要只取第一个等级段，也不要按整列模糊匹配。'}
    return {'instruction': '先按已发布定义明确目标等级，再按等级标签位置截取对应专业段；只在目标段匹配。'}


def province_count_query(source, host):
    """Expand a published scalar grade count to the requested authorized snapshot."""
    quote = host.get('user_text') or ''
    publication = source.get('business_project') or {}
    ref = host.get('user_ref')
    if (not ref or host.get('clarifications') or not publication.get('id') or not publication.get('version')
            or re.search(r'按|以及|另外|名单|明细|分布|占比|[，,；;\n]', quote)
            or _historical_request(host) or not (intent := _intent(quote)) or intent[0] != [PROVINCE]):
        return None
    grade = intent[1]
    bindings = _snapshot_bindings(source)
    if len(bindings) != 1:
        return None
    table, = bindings
    roles = bindings[table]
    physical = next(item for item in source['tables'] if item['name'] == table)
    dialect = sqlglot_name(source['dialect'])

    candidates = set()
    for term, _, tree, (inner, relation, _) in _registered_examples(source, dialect, {table}):
        definition = term.get('definition') or ''
        column = _column(physical, relation)
        count = tree.expressions[0].unalias() if tree.expressions else None
        if (tree is not inner or set(_GRADE.findall(term['name'])) != {grade}
                or not (unit := re.match(r'单位[：:]\s*(家|户|个)[。；;.]', definition))
                or not _simple_count(tree, column, roles['entity'])
                or any(value and key not in ('expressions', 'from', 'where') for key, value in tree.args.items())):
            continue
        filters = _where(tree)
        retained = []
        for atom in filters:
            membership = _membership(atom, column)
            if membership and membership[0] in roles['grade'] and grade in membership[1]:
                retained.append(atom.copy())
            elif (not membership or membership[0] not in roles['city'] or len(membership[1]) != 1
                  or not re.search(r'(?:示例|样例)为\s*' + re.escape(str(next(iter(membership[1])))), definition)):
                break
        else:
            if len(retained) != 1:
                continue
            identifiers = {c['name'] for c in physical['columns'] if any(
                label and _ENTITY_ID_ROLE.fullmatch(label)
                for label in (c.get('label'), c.get('business_name'), *(c.get('aliases') or [])))}
            if len(identifiers) == 1 and _count_column(count, column) is None:
                count.set('this', exp.Distinct(expressions=[exp.column(next(iter(identifiers)), quoted=True)]))
            elif len(identifiers) > 1 and _count_column(count, column) is None:
                continue
            tree.set('where', exp.Where(this=retained[0]))
            name = '.'.join(exp.to_identifier(part, quoted=True).sql(dialect=dialect) for part in table.split('.'))
            qualified = exp.to_table(name, dialect=dialect)
            qualified.set('alias', relation.args.get('alias'))
            relation.replace(qualified)
            title = grade + '资质单位数（授权快照）'
            tree.set('expressions', [exp.alias_(count, grade + '资质单位数（' + unit[1] + '）', quoted=True)])
            candidates.add((tree.sql(dialect=dialect), title))
    if len(candidates) != 1:
        return None
    sql, title = candidates.pop()
    return {'schema_version': 1, 'request_ref': ref, 'quote': quote, 'source_id': source['id'],
            'connection_revision': source.get('connection_revision', 0),
            'publication': {key: publication[key] for key in ('id', 'version')},
            'query_request': {'source_id': source['id'], 'purpose': 'answer', 'title': title, 'sql': sql}}


def _dual_guidance(constraints, bindings, text):
    if not constraints:
        return None
    c, = constraints
    if c.get('state') == 'ambiguous':
        return {'instruction': '已发布双证示例的适用字段或取值不一致。请明确本次采用的登记定义，再执行统计。'}
    named, candidates = _dual_reference(bindings, text)
    matches = [candidate[4] for candidate in (named or candidates)
               if candidate[0] == c['table'] and candidate[1] == c['column'] and candidate[2] == set(c['values'])
               and _registered_cities(candidate, bindings) == set(c['cities'])]
    return {**({'published_example': matches[0]['logical_sql']} if len(matches) == 1 else {}),
            'published_exact_values': c['values'], 'region_scope': c['cities'], 'unit': c['unit'],
            'instruction': '按已发布双证示例的精确等级取值和用户指定的地区正式计数。若符合条件的数量是 0，交付本次口径的 0；'
                           '类似等级原值只有在登记为同一口径后才能纳入。'}


def _segment(node, grade):
    """Prove the exact two-label slice, including reverse order and no match."""
    if not isinstance(node, exp.Case) or node.this is not None or len(node.args.get('ifs') or []) != 2:
        return None
    missing, bounded = node.args['ifs']
    other = '乙级' if grade == '甲级' else '甲级'
    no_match = _unwrap(missing.this)
    argument = _unwrap(node.args.get('default'))
    if (_literal(missing.args.get('true')) != '' or not isinstance(no_match, exp.EQ)
            or no_match.expression != exp.Literal.number(0) or not isinstance(argument, exp.Substring)
            or argument.args.get('length') is not None):
        return None
    pos, after = _unwrap(no_match.this), _unwrap(bounded.this)
    if (not isinstance(pos, exp.StrPosition) or _literal(pos.args.get('substr')) not in {grade + ':', grade + '：'}
            or not isinstance(after, exp.GT) or _unwrap(after.expression) != pos):
        return None
    other_pos = _unwrap(after.this)
    if (not isinstance(other_pos, exp.StrPosition) or other_pos.this != pos.this
            or _literal(other_pos.args.get('substr')) not in {other + ':', other + '：'}
            or pos.args.get('position') or other_pos.args.get('position')):
        return None
    start = exp.Add(this=pos.copy(), expression=exp.Literal.number(3))
    length = exp.Sub(this=exp.Sub(this=other_pos.copy(), expression=pos.copy()), expression=exp.Literal.number(3))
    limited = _unwrap(bounded.args.get('true'))
    if (argument.this == pos.this and argument.args.get('start') == start and isinstance(limited, exp.Substring)
            and limited.this == pos.this and limited.args.get('start') == start and limited.args.get('length') == length):
        return pos.this
    return None


def _region_column(node, column):
    node = _unwrap(node)
    if ((isinstance(node, exp.Coalesce) and all(_literal(value) is not None
            and _literal(value).casefold() in _NULL_LABELS for value in node.expressions))
            or (isinstance(node, exp.Nullif) and _literal(node.expression) == '')
            or (isinstance(node, exp.Trim) and not node.expression)):
        return _region_column(node.this, column)
    return column(node)


def _filters(tree):
    yield from _where(tree)
    for node in tree.expressions:
        for filtered in node.find_all(exp.Filter):
            if isinstance(filtered.expression, exp.Where):
                yield from _atoms(filtered.expression.this)


def _grade_filter(atoms, column, bindings, grade):
    names = bindings['grade']
    selected = [item for atom in atoms if (item := _membership(atom, column)) and item[0] in names]
    if not selected:
        return None
    name = selected[0][0]
    effective = set.intersection(*(values for _, values in selected))
    expected = bindings['grades'].get((name, grade), set())
    if expected and effective != expected:
        published = '、'.join(str(value) for value in sorted(expected, key=str))
        return 'conflict', '等级筛选与已发布口径不一致。请保留当前地区、专业段及单位去重键，并使用等级取值 '+published+'。'
    if expected and effective == expected:
        return 'checked', '等级枚举与已发布的等级范围一致。'
    return None


def _inspect_dual(tree, source, dialect, constraint):
    if constraint.get('state') == 'ambiguous':
        return 'conflict', '已发布双证定义存在适用字段或枚举差异。请先明确当前登记口径，再给出正式统计。'
    field = constraint['column']
    target = set(constraint['values'])
    issue = ('conflict', f'已登记双证等级的精确取值为 {", ".join(repr(v) for v in constraint["values"])}。'
                         '请沿用该取值、授权快照与用户地区做正式计数。相近原值需另经登记确认，不能用来替换精确计数结果。')
    if not (shape := _select_population(tree, source, dialect)) or shape[0] is not tree or shape[2] != constraint['table']:
        return issue
    _, relation, table = shape
    bindings = _snapshot_bindings(source)[table]
    column = _column(next(t for t in source['tables'] if t['name'] == table), relation)
    if not _simple_count(tree, column, bindings['entity']):
        return issue
    effective, chosen = None, None
    for atom in _where(tree):
        membership = _membership(atom, column)
        if membership and membership[0] == field:
            current = membership[1]
            if current - target:
                return issue
            effective = current if effective is None else effective & current
        elif membership and membership[0] in bindings['city']:
            chosen = membership[1] if chosen is None else chosen & membership[1]
        elif not _nonempty_key(atom, column, bindings['entity']):
            return issue
    if effective != target or (chosen or set()) != set(constraint['cities']):
        return issue
    return 'checked', '正式计数使用已登记双证精确取值，结果只代表本次授权快照与地区筛选。'


def _snapshot_unit_count_issue(tree, inner, relation, table, source, dialect):
    physical = next(r for r in source['tables'] if r['name'] == table)
    identifiers = {col['name'] for col in physical['columns']
                   if any(label and _ENTITY_ID_ROLE.fullmatch(label)
                          for label in (col.get('label'), col.get('business_name'), *(col.get('aliases') or [])))}
    if len(identifiers) != 1:
        return None
    key, = identifiers
    if physical.get('primary_key') == [key] or any(
            indexes == [key] and col['name'] == key and col.get('nullable') is False
            for indexes in physical.get('unique_keys') or [] for col in physical['columns']):
        return None  # No repeated (or missing) identities are possible in the direct scan.
    counts_rows = any(isinstance(count := p.unalias().unnest(), exp.Count)
                     and (isinstance(count.this, exp.Star) and not any(count.this.args.values())
                          or count.this == exp.Literal.number(1)) for p in tree.expressions)
    if not counts_rows:
        return None
    from .sql_answer_contract import _published_definition
    if _published_definition(tree, dialect, source, None):
        return None  # Execute the exact published measure within its original population.
    if inner is not tree:
        column = _column(physical, relation)
        if not any(column(p.unalias()) == key for p in inner.expressions):
            return 'conflict', '该资质快照已登记单位标识“'+key+'”。请在中间查询保留该标识，再按其去重计数，不能以记录行数代替单位量。'
    return 'conflict', '该资质快照已登记单位标识“'+key+'”，一单位可能有多行。请保留本次等级、地区和专业条件，按单位标识去重计数。'


def inspect(tree, source, constraint):
    if constraint.get('source_id') != source['id']:
        return None
    dialect = sqlglot_name(source['dialect'])
    if constraint['kind'] == DUAL:
        return _inspect_dual(tree, source, dialect, constraint)
    table = constraint['table']
    if not any(_physical_table(ref, source, dialect) == table for ref in tree.find_all(exp.Table)):
        return None
    bindings = _snapshot_bindings(source).get(table)
    if not bindings:
        return None
    kind, grade = constraint['kind'], constraint['grade']
    uncertain = ('unknown', '当前查询不可证明完整保留授权快照的地区范围和单位去重。'
                 if kind == PROVINCE else '当前查询未验证目标等级专业段。请用同一已发布单表来源，按等级标签位置截取后再匹配。')
    if not (shape := _select_population(tree, source, dialect)) or shape[2] != table:
        return uncertain
    inner, relation, _ = shape
    if issue := _snapshot_unit_count_issue(tree, inner, relation, table, source, dialect):
        return issue
    column = _column(next(t for t in source['tables'] if t['name'] == table), relation)
    filters = list(_filters(inner))
    grade_check = _grade_filter(filters, column, bindings, grade)
    if grade_check and grade_check[0] == 'conflict':
        return grade_check
    if kind == PROVINCE:
        if inner is not tree:
            return uncertain
        for count in tree.find_all(exp.Count):
            if any(column(field) in bindings['city'] | bindings['grade'] for field in count.find_all(exp.Column)):
                return 'conflict', '统计单位数须使用单位标识或已发布的有效快照计数。按资质等级或地市取值去重会少算单位。'
        def region(node):
            return _region_column(node, column) in bindings['city']
        for atom in filters:
            membership = _membership(atom, lambda node: _region_column(node, column) if region(node) else None)
            if (membership and membership[1] - _NULL_LABELS
                    or isinstance(atom, exp.Not) and isinstance(atom.this, exp.Is)
                    and isinstance(atom.this.expression, exp.Null) and region(atom.this.this)
                    or isinstance(atom, (exp.NEQ, exp.NullSafeNEQ)) and (
                        region(atom.this) and (isinstance(atom.expression, exp.Null) or _literal(atom.expression) in _NULL_LABELS)
                        or region(atom.expression) and (isinstance(atom.this, exp.Null) or _literal(atom.this) in _NULL_LABELS))):
                return 'conflict', '全省请求的 SQL 缩小了地市筛选范围。请保留当前授权快照里的全部地区，空地区单独注明。'
            if any(column(c) in bindings['city'] for c in atom.find_all(exp.Column)):
                return uncertain
        if any(tree.args.get(k) for k in ('having', 'qualify', 'limit', 'offset')) or tree.find(exp.Window, exp.Filter):
            return uncertain
        def allowed_filter(atom):
            match = _membership(atom, column)
            return ((match and match[0] in bindings['grade'])
                    or _nonempty_key(atom, column, bindings['entity']))
        if grade_check and all(allowed_filter(atom) for atom in filters):
            if any(_count_column(projection, column) in bindings['entity'] for projection in tree.expressions):
                return 'checked', '查询未按地市收窄，并保留了授权快照的单位去重键。全省总体覆盖未验证。'
            if any(isinstance(p.unalias().unnest(), exp.Count) for p in tree.expressions):
                return 'checked', '已核对正式结果的统计范围来自当前授权快照，未使用地市筛选。单位去重和全省总体覆盖仍需核对。'
        return uncertain

    wanted_text = constraint.get('evidence', {}).get('quote') or ''
    composite = bindings['composite']
    for atom in filters:
        if isinstance(atom, _FUZZY) and _region_column(atom.this, column) in composite:
            return 'conflict', '完整专业列的模糊匹配会跨等级误计。按已发布的等级标签截取专业段后再筛选。'
        if any(column(part.this) in composite and part.args.get('part_index') == exp.Literal.number(1)
               for part in atom.find_all(exp.SplitPart)):
            return 'conflict', '只取第一个等级段会遗漏逆序记录。请按等级标签找到目标段。'
    if inner is not tree:
        projected = {p.alias_or_name: p.unalias() for p in inner.expressions if p.alias_or_name}
        outer_alias = tree.args['from'].this.alias_or_name
        for atom in _where(tree):
            if not isinstance(atom, _FUZZY) or not isinstance(atom.this, exp.Column):
                continue
            if atom.this.table and atom.this.table != outer_alias:
                continue
            value = projected.get(atom.this.name)
            if (column(value) in composite or any(column(part.this) in composite and
                    part.args.get('part_index') == exp.Literal.number(1) for part in (value.find_all(exp.SplitPart) if value is not None else ()))):
                return 'conflict', '查询没有隔离目标等级的专业段。使用等级标签定位相应专业段，再在其中匹配。'
            pattern = _literal(atom.expression)
            if (isinstance(atom, exp.Like) and column(_segment(value, grade)) in composite and pattern
                    and re.fullmatch(r'%[^%_]+%', pattern) and pattern[1:-1] in wanted_text):
                return 'checked', '专业筛选明确匹配了目标等级段。'
    return uncertain


def _count_column(node, column):
    value = node.unalias().unnest()
    if isinstance(value, exp.Count) and isinstance(value.this, exp.Distinct) and len(value.this.expressions) == 1:
        return column(value.this.expressions[0])
    return None


def _simple_count(tree, column, entity):
    if (len(tree.expressions) != 1 or any(tree.args.get(k) for k in ('group', 'having', 'qualify', 'limit', 'offset'))
            or not isinstance(count := tree.expressions[0].unalias().unnest(), exp.Count)):
        return False
    return (isinstance(count.this, exp.Star) and not any(count.this.args.values())
            or count.this == exp.Literal.number(1)
            or _count_column(count, column) in entity)


def published_dual_grade_count(value):
    """Return the exact scalar count only from a complete, bound, formal receipt."""
    preview = value.get('values') or {}
    rows = preview.get('rows')
    lineage = value.get('result_lineage') or {}
    receipt = value.get('receipt') or {}
    if (value.get('status') != 'succeeded' or not value.get('result_ref') or value.get('truncated')
            or value.get('purpose') == 'explore' or lineage.get('purpose') != 'answer'
            or receipt.get('read_only') is not True or not lineage.get('request_ref')
            or not lineage.get('config_fingerprint') or lineage.get('source_id') != receipt.get('source_id')
            or preview.get('complete') is not True or preview.get('row_count') != 1
            or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], list) or len(rows[0]) != 1
            or len(preview.get('columns') or []) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0):
        return None
    for check in (value.get('validation') or {}).get('checks', []):
        c = check.get('constraint') or {}
        if (check.get('state') == 'checked' and c.get('kind') == DUAL
                and c.get('source_id') == lineage['source_id'] and c.get('unit') in ('家', '户', '个')
                and (c.get('evidence') or {}).get('user_ref') == lineage['request_ref']):
            return rows[0][0]
    return None


def _nonempty_key(atom, column, keys):
    return (isinstance(atom, exp.Not) and isinstance(atom.this, exp.Is)
            and isinstance(atom.this.expression, exp.Null) and column(atom.this.this) in keys)


def snapshot_region_evidence(full, source):
    """Project facts for the formal result bound to the current planning catalog source."""
    lineage = full.get('result_lineage') or {}
    scope = full.get('request_scope') or {}
    if (full.get('status') != 'succeeded' or full.get('truncated') or not full.get('result_ref')
            or lineage.get('purpose') != 'answer' or not lineage.get('request_ref')
            or lineage.get('source_id') != source.get('id')
            or scope.get('config_fingerprint') != lineage.get('config_fingerprint')
            or full.get('business_project') != source.get('business_project')):
        return None
    for check in (full.get('validation') or {}).get('checks') or []:
        constraint = check.get('constraint') or {}
        if (check.get('state') == 'checked' and constraint.get('kind') == PROVINCE
                and constraint.get('source_id') == source['id']
                and (constraint.get('evidence') or {}).get('user_ref') == lineage['request_ref']):
            break
    else:
        return None
    if fingerprint(source) != lineage['config_fingerprint']:
        return None
    proof = {'schema_version': 1, 'result_ref': full['result_ref'], 'request_ref': lineage['request_ref'],
             'source_id': source['id'], 'population': 'authorized_snapshot', 'province_completeness': 'unverified'}
    dialect = sqlglot_name(source['dialect'])
    try:
        tree = sqlglot.parse_one(full.get('sql') or '', read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    if not (shape := _select_population(tree, source, dialect)) or shape[0] is not tree:
        return None
    _, relation, table = shape
    bindings = _snapshot_bindings(source).get(table)
    if not bindings or table != constraint['table']:
        return None
    groups = tree.args['group'].expressions if tree.args.get('group') else []
    if not groups:
        return proof
    column = _column(next(t for t in source['tables'] if t['name'] == table), relation)
    if len(groups) != 1 or len(tree.expressions) != 2 or len(full.get('columns') or []) != 2:
        return proof
    group = groups[0]
    index = next((i for i, p in enumerate(tree.expressions) if _region_column(p.unalias(), column) in bindings['city']), None)
    if index is None or _count_column(tree.expressions[1 - index], column) not in bindings['entity']:
        return proof
    selection = tree.expressions[index]
    if not (group == selection.unalias() or group == exp.Literal.number(index + 1)
            or isinstance(group, exp.Column) and not group.table and group.name == selection.alias and not column(group)):
        return proof
    region_name, count_name = full['columns'][index], full['columns'][1 - index]
    rows = full.get('rows') or []
    if not rows or len(rows) != full.get('row_count') or any(type(row.get(count_name)) is not int or row[count_name] < 0 for row in rows):
        return proof
    known = sorted({row[region_name] for row in rows if isinstance(row.get(region_name), str)
                    and row[region_name].strip().casefold() not in _NULL_LABELS})
    unknown = [row[count_name] for row in rows if row.get(region_name) is None
               or isinstance(row[region_name], str) and row[region_name].strip().casefold() in _NULL_LABELS]
    return {**proof, 'region_column': region_name, 'regions': known,
            **({'unlocated_count': unknown[0]} if len(unknown) == 1 else {})}
