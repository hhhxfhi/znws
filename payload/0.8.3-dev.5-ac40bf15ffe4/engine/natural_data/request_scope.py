"""Server-owned request constraints; conservative proofs, explicit unknowns.

No model-authored state is accepted. SQL equivalence beyond supported forms is
reported as unknown, never silently promoted to a proof.
"""
import calendar
import copy
from datetime import date, timedelta
import re
import sqlglot
from sqlglot import exp
from .catalog_index import fingerprint
from .business_sql_checks import DIALECTS
from .day_bounds import inclusive_upper_day

LABELS = {'checked': '已检查符合', 'conflict': '发现冲突', 'unknown': '尚无法验证'}
CONTINUATION = re.compile(r'只(?:改|看|查)|仅(?:改|看|查)|改为|换成|其他.*不变|取消.*限制|那[么就]|继续|同样')
# A follow-up is an edit of the previous request, not a new question.  Customers
# rarely use the words a fixed list expects ("再看甲级", "改查乙级", "那2024年呢"),
# so the shape of the turn is used as evidence and the previous scope is the
# default.  A new topic is the only reason to drop it.
_FOLLOWUP = re.compile(
    r'再(?:看|查|统计|算|比|按|给|来)|改(?:查|看|成|为|按|用)|换(?:成|为|到|按)|'
    r'调(?:成|为|到)|变成|变为|加上|加个|增加|补充|去掉|去除|取消|不限|不再|'
    r'仍按|沿用|同一范围|同口径|原范围|其他(?:条件|范围)?不变|其余(?:条件|范围)?不变|'
    r'只(?:改|看|查|算|要)|仅(?:改|看|查|算|要)|(?:^|\n)那(?:么|就)?|'
    r'按.{1,10}(?:分|统计|汇总|看|排|列)')
_TOPIC_CHANGE = re.compile(r'换个问题|另一个问题|新问题|重新开始|从头(?:问|说|来)|先不管(?:前面|上面)|无关(?:的)?问题')
_KEEP_REST = re.compile(r'(?:其他|其余)(?:条件|范围)?不变|同一范围|仍按|沿用|原范围')


def _column_mapping(source, table_name, column_name):
    """Registered value->label mapping for one column, or None when absent."""
    for table in source.get('tables', []):
        if table.get('name') != table_name:
            continue
        for column in table.get('columns', []):
            if column.get('name') != column_name:
                continue
            return {str(item.get('value')): str(item.get('label'))
                    for item in column.get('business_values') or []}
    return None


def _value_labels(source, table_name, column_name, values):
    """Registered labels for a discovered filter, so user wording can be checked."""
    mapping = _column_mapping(source, table_name, column_name)
    if mapping is None:
        return [str(value) for value in values]
    return [mapping.get(str(value), str(value)) for value in values] + [str(value) for value in values]


def _provenance_unknown(source, table_name, column_name, value):
    """True when the catalog offers no wording bridge for this filter value.

    A value the user would have had to type verbatim (user-language text, or a
    code with a registered label) is established as model-added when it never
    appears in the user's words.  An opaque code without any registered label
    may carry a wording the server cannot see -- the user said "排除作废" while
    the SQL pinned 'voided' -- so its provenance stays unknown and the
    constraint is kept.
    """
    text_value = str(value)
    if any('\u4e00' <= ch <= '\u9fff' for ch in text_value):
        return False
    mapping = _column_mapping(source, table_name, column_name)
    return mapping is None or text_value not in mapping


def _prune_discovered(constraints, text, source):
    """A filter the model added to its own SQL is evidence, not user intent.

    It stays inspectable for the turn that produced it, but it is inherited only
    when the user actually named one of its values (this turn, or the turn whose
    own words produced it).  Otherwise a TOP-N or a partial slice silently
    becomes the next turn's population -- the 325-of-345 denominator error.

    Two provenances stay unknown and are kept: a constraint with no recorded
    quote (the host may simply not have supplied the user text), and a value
    the catalog cannot render into user wording (an opaque code with no
    registered label -- the user may have named it as "作废" while the SQL
    pinned 'voided').  Dropping evidence the server cannot disprove would turn
    an unknown into a silent reset -- and the next turn would answer a
    different population without ever saying so.
    """
    kept = []
    for constraint in constraints:
        if constraint.get('kind') != 'filter' or not constraint.get('discovered'):
            kept.append(constraint)
            continue
        quote = (constraint.get('evidence') or {}).get('quote') or ''
        if not quote.strip():
            kept.append(constraint)
            continue
        haystack = text + '\n' + quote
        table_name, column_name = constraint.get('table'), constraint.get('column')
        values = constraint.get('values') or []
        labels = _value_labels(source, table_name, column_name, values)
        if any(label and label in haystack for label in labels):
            kept.append(constraint)
            continue
        if any(_provenance_unknown(source, table_name, column_name, value) for value in values):
            kept.append(constraint)
    return kept


def _topic_change(previous, text, source):
    """Only a clearly new subject releases the previous scope."""
    if _TOPIC_CHANGE.search(text):
        return True
    prior_tables = {c.get('table') for c in previous.get('constraints', []) if c.get('table')}
    if _KEEP_REST.search(text):
        return False
    for table in source.get('tables', []):
        if table.get('name') in prior_tables:
            continue
        names = [table.get('name', '').rsplit('.', 1)[-1], table.get('label'), table.get('business_name')]
        if any(isinstance(name, str) and len(name) >= 4 and name in text for name in names):
            return True
    return False


def recent_scope(engine, ctx, source, host=None):
    """The last delivered scope of this session, used when a task was recreated.

    Small models frequently start a new task for a follow-up, which drops the
    task-held scope.  The session keeps the last verified scope so the turn can
    still inherit it; a real topic change is rejected by _topic_change.

    It is only offered to the immediately following turn (or a retry of the same
    request).  Independent questions in one session must not inherit it, and a
    stored scope without a turn is not used as a guess.
    """
    if not engine or not ctx or not getattr(ctx, 'session_id', None):
        return None
    try:
        _, artifact = engine.artifacts.head(ctx.project, 'session:' + ctx.session_id, 'work.turn_scope')
    except Exception:
        return None
    payload = (artifact or {}).get('payload') or {}
    scope = payload.get('scope')
    if not isinstance(scope, dict) or not scope.get('constraints'):
        return None
    if payload.get('source_id') != source.get('id'):
        return None
    if payload.get('config_fingerprint') != fingerprint(source):
        return None
    host = host or {}
    stored_turn, turn = payload.get('turn'), host.get('turn')
    stored_ref, user_ref = payload.get('user_ref'), host.get('user_ref')
    adjacent = isinstance(stored_turn, int) and isinstance(turn, int) and turn == stored_turn + 1
    retry = bool(stored_ref) and stored_ref == user_ref
    if not (adjacent or retry):
        return None
    return scope


_ONLY_CURRENT_COUNT = re.compile(r'\s*(?:请\s*)?(?:只|仅)(?:算|计算|统计|查|看)\s*(?:当前(?:的)?月份|当前月|本月|这个月)'
                                 r'(?:的)?\s*累[计积](?:值|量|数)?\s*[。！!？?]?\s*')
_ONLY_FILTER_VALUE = re.compile(r'\s*(?:请\s*)?(?:只|仅)(?:看|查)\s*([^。！？?!，,；;\n]+?)\s*[。！？?!]?\s*')
_THROUGHPUT_MEASURE = re.compile(r'(?:办理|处理|处置)(?:总)?(?:数量|量(?![级化])|数(?![据值字额目]))')
_SHARE_DENOMINATOR = re.compile(r'(?:各|分别)?占总(?:数量|量|数(?![据值字额目]))')


def _explicit_count_text(text):
    """Leave only possible entity counts for the noun-head heuristic."""
    # An action count or the denominator of a share does not name the entity.
    # Keep a boundary so nouns on either side cannot turn into a new name.
    return _SHARE_DENOMINATOR.sub('，', _THROUGHPUT_MEASURE.sub('，', str(text or '')))


def _is_registered_entity_fk(source, table_name, col_name):
    """Check if col_name is a registered foreign key referencing an entity table's single PK/unique key."""
    t_name = table_name.split('.')[-1].lower()
    c_name = col_name.lower()
    tables_by_name = {t['name'].split('.')[-1].lower(): t for t in source.get('tables', [])}
    for r in source.get('business_dictionary', []):
        if r.get('kind') != 'relation':
            continue
        t1 = r.get('table', '').split('.')[-1].lower()
        t2 = r.get('to_table', '').split('.')[-1].lower()
        from_cols = [c.lower() for c in r.get('from_columns', [])]
        to_cols = [c.lower() for c in r.get('to_columns', [])]
        card = r.get('cardinality', '')

        # Case A: T is t2 (child table), pointing to t1 (parent entity table)
        if t2 == t_name and to_cols == [c_name] and len(from_cols) == 1:
            parent_table = tables_by_name.get(t1)
            if parent_table:
                parent_pk = [str(k).lower() for k in (parent_table.get('primary_key') or [])]
                parent_uks = [[str(k).lower() for k in uk] for uk in (parent_table.get('unique_keys') or []) if len(uk) == 1]
                target_key = from_cols[0]
                if (len(parent_pk) == 1 and parent_pk[0] == target_key) or any(uk[0] == target_key for uk in parent_uks):
                    if card == 'one_to_many':
                        return True

        # Case B: T is t1 (child table), pointing to t2 (parent entity table)
        if t1 == t_name and from_cols == [c_name] and len(to_cols) == 1:
            parent_table = tables_by_name.get(t2)
            if parent_table:
                parent_pk = [str(k).lower() for k in (parent_table.get('primary_key') or [])]
                parent_uks = [[str(k).lower() for k in uk] for uk in (parent_table.get('unique_keys') or []) if len(uk) == 1]
                target_key = to_cols[0]
                if (len(parent_pk) == 1 and parent_pk[0] == target_key) or any(uk[0] == target_key for uk in parent_uks):
                    if card == 'many_to_one':
                        return True

    return False


def _extract_count_subjects(text):
    """Extract Chinese entity nouns requested for count aggregation."""
    from .metric_subject import projected_count_subjects
    t = _explicit_count_text(text)
    res = projected_count_subjects(t)
    for match in re.finditer(r'([一-鿿]{2,}?)(?:的)?(?:去重数|笔数|户数|人数|单位数|企业数|客户数)', t):
        for obj in re.split(r'和|以及|分别|与|、|或者|还是', match[1]):
            if len(obj) >= 2: res.add(obj[-2:])
    return res


_SEMANTIC_COL_HINTS = {
    '状态': ('status', 'state', 'zt', '状态'),
    '院区': ('campus', '院区'),
    '类型': ('type', 'kind', 'category', '类型', '类别'),
    '类别': ('type', 'kind', 'category', '类型', '类别'),
    '科室': ('dept', 'department', '科室', '部门'),
    '部门': ('dept', 'department', '科室', '部门'),
    '等级': ('grade', 'level', '等级', '级别'),
    '地区': ('region', 'area', 'city', 'province', '地区', '区域'),
}


def _column_restriction_removed(text: str, names: list) -> bool:
    for n in names:
        if n and re.search(r'取消[^。\n]{0,12}' + re.escape(n) + r'[^。\n]{0,6}(?:限制|筛选)', text):
            return True
    lowered = [str(n).lower() for n in names if n]
    for cn_term, tokens in _SEMANTIC_COL_HINTS.items():
        if re.search(r'取消[^。\n]{0,12}' + re.escape(cn_term) + r'[^。\n]{0,6}(?:限制|筛选)', text):
            if any(tok in name for name in lowered for tok in tokens):
                return True
    return False


def _enum_restriction_removed(text: str, label: str) -> bool:
    """A registered value can name the filter the user wants to remove."""
    name = r'[「“"\']?' + re.escape(label) + r'[」”"\']?'
    return bool(re.search(r'(?:取消|去掉|解除)\s*(?:对\s*)?' + name + r'\s*的?\s*(?:限制|筛选)', text))


def _direct_relations(tree, source):
    """Resolve a direct SELECT to its actual authorized relations."""
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    if not isinstance(tree, exp.Select) or tree.find(exp.Subquery, exp.CTE):
        return None
    dialect = sqlglot_name(source.get('dialect'))
    normalized = normalize_identifiers(tree.copy(), dialect=dialect)
    from_ = normalized.args.get('from')
    if not from_:
        return None
    refs = [from_.this, *(join.this for join in normalized.args.get('joins') or [])]
    key = str.casefold if dialect in ('sqlite', 'mysql') else str
    relations = {}
    for ref in refs:
        table = _physical_table(ref, source, dialect)
        if not table or key(ref.alias_or_name) in relations:
            return None
        relations[key(ref.alias_or_name)] = table
    return normalized, relations, key


def _named_time_axis(source, table, column, text):
    from .retrieval import normalize

    full, short, field = table['name'], table['name'].rsplit('.', 1)[-1], column['name']
    names = [full, short, f'{full}.{field}', f'{short}.{field}', table.get('label'), table.get('business_name'),
             *(table.get('aliases') or []), field, column.get('label'), column.get('business_name'),
             column['time_semantics'].get('name'), *(column.get('aliases') or [])]
    for mapping in source.get('business_dictionary', []):
        if mapping.get('kind') == 'time' and mapping.get('table') == table['name'] and mapping.get('column') == column['name']:
            names.extend(mapping.get('synonyms') or [])
    question = normalize(text)
    return any(re.search(r'(?<![a-z0-9_.])' + re.escape(name) + r'(?![a-z0-9_.])', question)
               for label in names if len(name := normalize(label).strip()) >= 2)


def _catalog_time_default(source, table, column, text, sql):
    if _named_time_axis(source, table, column, text):
        return {}
    if sql:
        try:
            tree = sqlglot.parse_one(sql, read=DIALECTS.get(source.get('dialect'), source.get('dialect') or None))
        except sqlglot.errors.SqlglotError:
            tree = None
        if (selected := _direct_relations(tree, source)) and table['name'] in selected[1].values():
            return {}
    # A provisional catalog-wide default may have no bearing on a different
    # queried table. An explicit or previously matched axis remains binding.
    return {'catalog_time_default': True}


def derive(source, host, previous=None, proposal=None, *, engine=None, ctx=None, sql=None, parameters=None, published_grouping=None):
    text = (host or {}).get('user_text', '')
    clarification = '\n'.join(x.get('answer', '') for x in (host or {}).get('clarifications', []) if x.get('call_id') and x.get('question_id'))
    original = text
    if clarification: text += '\n' + clarification
    reference = {'user_ref': (host or {}).get('user_ref'), 'quote': original,
                 'clarifications': copy.deepcopy((host or {}).get('clarifications', []))}
    prior_current_count = not clarification and _ONLY_CURRENT_COUNT.fullmatch(original)
    prior_constraints = copy.deepcopy((previous or {}).get('constraints', []))
    # The task's own scope is the source of truth.  A turn edits it unless the
    # user clearly started another subject; the closed word list is only one of
    # the signals, because "再看", "改查" and "那…呢" are ordinary follow-ups.
    topic_change = bool(prior_constraints) and _topic_change(previous or {}, original, source)
    explicit_followup = bool(CONTINUATION.search(text) or prior_current_count)
    shaped_followup = bool(_FOLLOWUP.search(original))
    continuing = bool(prior_constraints) and not topic_change and (explicit_followup or shaped_followup)
    inherited = _prune_discovered(prior_constraints, text, source) if continuing else []
    if prior_current_count and ((previous or {}).get('request') or {}).get('user_ref') != reference['user_ref']:
        inherited = []  # An omitted task or a selected old task must use the same verified prior result.
    constraints = []
    unresolved = []

    # 1. Dimension filters and grouping
    removed_columns = []
    for table in source.get('tables', []):
        for col in table.get('columns', []):
            names = [col['name'], col.get('label'), col.get('business_name'), (col.get('time_semantics') or {}).get('name'), *(col.get('aliases') or [])]
            key = {'table': table['name'], 'column': col['name']}
            # A cancellation removes only a named dimension, not every filter.
            removed = _column_restriction_removed(text, names)
            if removed:
                inherited = [x for x in inherited if not (x.get('table') == key['table'] and x.get('column') == key['column'])]
                removed_columns.append((key['table'], key['column']))
                continue
            values = []
            excluded = []
            labels = []
            for enum in col.get('business_values') or []:
                label = str(enum.get('label', ''))
                if not label or label not in text: continue
                if _enum_restriction_removed(text, label):
                    inherited = [x for x in inherited if (x.get('table'), x.get('column')) != (key['table'], key['column'])]
                    removed_columns.append((key['table'], key['column']))
                    continue
                if len(label) == 1 and not any(n and re.search(re.escape(n) + r'\s*(?:为|是|等于|：|:)?\s*[“"\']?' + re.escape(label), text) for n in names if n != label):
                    # A calendar suffix (年/月/日), a character inside another business word, etc. is not an enum selection.
                    continue
                before = text[:text.index(label)][-8:]
                (excluded if re.search(r'排除|不含|不包括|除去|不要', before) else values).append(str(enum['value']))
                labels.append(label)
            # Bare enum labels such as “全省” or “合格” name a business
            # value, not a particular table. Restrict them to that table
            # when SQL uses it; explicit table/field selections remain hard.
            source_bound = not clarification and not any(
                n and n not in labels and n in text
                for n in [*names, table['name'], table.get('label'), *(table.get('aliases') or [])])
            for op, vs in [('in', values), ('not_in', excluded)]:
                if vs:
                    constraints.append({**key, 'kind': 'filter', 'op': op, 'values': sorted(set(vs)), 'evidence': reference,
                                        **({'enum_source_bound': True} if source_bound else {})})
            if any(n and re.search(r'按\s*' + re.escape(n), text) for n in names):
                constraints.append({**key, 'kind': 'group', 'evidence': reference})

    # 1b. A published business scope named in the request (aliases and filters come from configuration).
    from .published_scope import derive as derive_published_scope, geographic_refinement, superseded as scope_superseded
    inherited = [x for x in inherited if x['kind'] != 'published_scope' or not scope_superseded(x, text, removed_columns, source)]
    inherited = [y for x in inherited if (y := geographic_refinement(x, text, reference) if x['kind']=='published_scope' else x)]
    published = derive_published_scope(source, text, reference)
    if published:
        constraints.append(published)
    elif prior_current_count and not any(c['kind']=='published_scope' for c in inherited):
        from .published_scope import previous_grouping_scope
        if inherited_grouping := previous_grouping_scope(engine, ctx, source, reference):
            constraints.append(inherited_grouping)
    from .raw_dimension_scope import KIND as raw_dimension_kind, derive as derive_raw_dimension
    inherited = [x for x in inherited if x['kind'] != raw_dimension_kind]
    if raw_dimension := derive_raw_dimension(source, host or {}, reference, published, engine=engine, ctx=ctx):
        constraints.append(raw_dimension)

    from .qualification_scope import KINDS as qualification_kinds, derive as derive_qualification_scope
    # These narrow checks require a positive scope in the current request.
    inherited = [x for x in inherited if x['kind'] not in qualification_kinds]
    constraints.extend(derive_qualification_scope(source, text, reference))
    from .holding_rate_scope import KIND as holding_rate_kind, derive as derive_holding_rate
    inherited = [x for x in inherited if x['kind'] != holding_rate_kind]
    if holding_rate := derive_holding_rate(source, host or {}, reference):
        constraints.append(holding_rate)
    from .cancellation_scope import KIND as cancellation_kind, derive as derive_cancellation
    inherited = [x for x in inherited if x['kind'] != cancellation_kind]
    constraints.extend(derive_cancellation(source, text, reference, host=host))

    # 2. Statistical count subjects & distinct grain
    # Read actual registered primary keys, unique keys, and entity grain columns.
    # Chinese noun names serve only as candidates; multiple candidates remain unresolved/unknown.
    from .metric_subject import once_count_subjects, physical_entity_parent
    subjects = _extract_count_subjects(text)
    once = once_count_subjects(text)
    explicit_distinct = bool(re.search(r'去重|各不同|唯一', text))

    for subj in subjects:
        user_explicit_distinct = explicit_distinct or subj in once
        matched_any = False
        for table in source.get('tables', []):
            pk = [str(k).lower() for k in (table.get('primary_key') or [])]
            t_names = [table['name'], table.get('label'), table.get('business_name'), *(table.get('aliases') or [])]
            t_match = any(n and (n == subj or subj in n or n in subj) for n in t_names)

            # Look for candidate columns matching the subject
            candidate_cols = [c for c in table.get('columns', []) if any(n and (n == subj or subj in n or n in subj) for n in [c['name'], c.get('label'), c.get('business_name'), *(c.get('aliases') or [])])]

            if candidate_cols:
                matched_any = True
                pk_matches = [c for c in candidate_cols if c['name'].lower() in pk and len(pk) == 1]
                uk_matches = [c for c in candidate_cols if any(c['name'].lower() in [str(k).lower() for k in uk] and len(uk) == 1 for uk in table.get('unique_keys', []))]
                fk_matches = {c['name']: c for c in candidate_cols
                              if _is_registered_entity_fk(source, table['name'], c['name']) or
                              (subj in once and physical_entity_parent(source, table['name'], c['name']))}

                candidate_map = {**{c['name']: c for c in pk_matches + uk_matches}, **fk_matches}
                proven_candidates = list(candidate_map.values())
                target_col = None

                if len(proven_candidates) == 1:
                    target_col = proven_candidates[0]
                elif len(candidate_cols) == 1:
                    target_col = candidate_cols[0]
                else:
                    # Multiple candidates and none is unique proven entity key -> keep unresolved
                    unresolved.append(f'统计对象“{subj}”在表“{table["name"]}”中匹配到多个候选字段（{[c["name"] for c in candidate_cols]}），未登记唯一实体键，字段粒度尚无法唯一确定。')
                    continue

                col_name = target_col['name']
                is_single_pk = (len(pk) == 1 and col_name.lower() in pk)
                is_unique = any([str(k).lower() for k in uk] == [col_name.lower()] for uk in table.get('unique_keys', []))
                is_fk = col_name in fk_matches

                if is_single_pk or is_unique:
                    distinct_req = user_explicit_distinct
                elif is_fk:
                    distinct_req = True
                elif user_explicit_distinct:
                    distinct_req = True
                else:
                    # No registered PK or relationship FK to an entity -> cannot prove entity grain
                    distinct_req = False
                    unresolved.append(f'字段“{table["name"]}.{col_name}”未登记主键或实体关联外键，无法证明其为去重实体标识。')

                constraints.append({
                    'kind': 'count_subject',
                    'subject': subj,
                    'table': table['name'],
                    'column': col_name,
                    'is_pk': is_single_pk,
                    'entity_proven': is_single_pk or is_unique or is_fk or user_explicit_distinct,
                    'composite_pk': pk if len(pk) > 1 else None,
                    'distinct_required': distinct_req,
                    **({'once_per_entity': True} if subj in once and is_fk and not (is_single_pk or is_unique) else {}),
                    'evidence': reference
                })
            elif t_match:
                matched_any = True
                if len(pk) == 1:
                    constraints.append({
                        'kind': 'count_subject',
                        'subject': subj,
                        'table': table['name'],
                        'column': pk[0],
                        'is_pk': True,
                        'composite_pk': None,
                        'distinct_required': user_explicit_distinct,
                        'evidence': reference
                    })
                elif len(pk) > 1:
                    constraints.append({
                        'kind': 'count_subject',
                        'subject': subj,
                        'table': table['name'],
                        'column': None,
                        'is_pk': False,
                        'composite_pk': pk,
                        'distinct_required': user_explicit_distinct,
                        'evidence': reference
                    })
                else:
                    constraints.append({
                        'kind': 'count_subject',
                        'subject': subj,
                        'table': table['name'],
                        'column': None,
                        'is_pk': False,
                        'composite_pk': None,
                        'distinct_required': user_explicit_distinct,
                        'evidence': reference
                    })

        if not matched_any:
            unresolved.append(f'用户明确统计“{subj}”，但未配置对应实体主键或去重字段。')

    # 3. Time Scope & Snapshot vs Period
    from natural_analysis.forecasting import historical_scope_text, relative_forecast_horizon, target_mentions
    from .current_month import bind as bind_current_month, intent as current_month_intent
    current_month = (published_grouping or {}).get('current_month') or current_month_intent(host)
    inherited = [x for x in inherited if x['kind'] != 'current_month' and not (current_month and x['kind'] == 'time')]
    time_host = {**(host or {}), 'user_text': text}
    mentions = target_mentions(time_host)
    targets = {value for value, _ in mentions}
    forecast_target = next(iter(targets)) if len(targets) == 1 else None
    forecast_horizon = relative_forecast_horizon(time_host)
    time_text = text
    if mentions:
        time_text = historical_scope_text(time_host, mentions)
        # In a forecast request, current/selected data sources are scope qualifiers.
        # Preserve actual time cues such as 当前余额 and 截至.
        time_text = re.sub(r'(?:当前|目前|现在)\s*((?:(?:已)?授权的?|所选|选定)\s*(?:库|数据[源库集])|数据[源库集])',
                           r'\1', time_text)
    years = list(dict.fromkeys(re.findall(r'(?<!\d)((?:19|20)\d{2})年?', time_text)))
    months = list(dict.fromkeys(re.findall(r'(?<!\d)((?:19|20)\d{2})[-/年](\d{1,2})月?', time_text)))
    month_strs = [f"{y}-{int(m):02d}" for y, m in months]

    # Conversational "当前/目前/现在" preceding an explicit period (e.g. "当前2025年订单") is not a snapshot
    has_explicit_snapshot_word = bool(re.search(r'截至|截止|期末|年底|年末|月底|月末|存量|余额|在册|有效', time_text))
    is_conversational_current = bool(re.search(r'(?:当前|目前|现在)\s*(?:20\d{2}|去年|前年)', time_text) or
                                    ((years or month_strs) and not has_explicit_snapshot_word))
    is_snapshot = has_explicit_snapshot_word or (bool(re.search(r'当前|目前|现在|今天|今日', time_text)) and not is_conversational_current)

    from .day_bounds import snapshot_day
    snapshot_host = {**time_host, 'user_text': time_text} if mentions else host
    cutoff_day = snapshot_day(snapshot_host) if is_snapshot else None

    time_cols = [(t, c) for t in source.get('tables', []) for c in t.get('columns', []) if c.get('time_semantics')]
    time_default = (_catalog_time_default(source, *time_cols[0], text, sql)
                    if len(time_cols) == 1 and (years or month_strs or is_snapshot) else {})
    if current_month:
        if current := bind_current_month(source, current_month, reference, host=host, engine=engine, ctx=ctx, sql=sql):
            constraints.append(current)
        else:
            unresolved.append(f'用户要求当前月份 {current_month["period"]}（{current_month["time_zone"]}），未能核对唯一业务时间字段。')
    elif years or month_strs or is_snapshot:
        if len(time_cols) == 0:
            unresolved.append('用户指定了时间范围，但授权数据源中未配置任何业务时间字段（time_semantics），无法验证时间条件。')
        elif len(time_cols) == 1:
            table, col = time_cols[0]
            constraints.append({
                'kind': 'time',
                'table': table['name'],
                'column': col['name'],
                'mode': 'snapshot' if is_snapshot else 'period',
                'cutoff': cutoff_day.isoformat() if cutoff_day else None,
                'years': years,
                'months': month_strs,
                'grain': col['time_semantics'].get('grain'),
                'evidence': reference,
                **time_default,
            })
        elif len(time_cols) > 1:
            unresolved.append('用户指定时间条件，但没有唯一的已配置业务时间字段。')

    # 4. Comparison periods (YoY / MoM)
    # Business ratio (占比、比例、份额) does NOT equal time comparison
    is_ratio = bool(re.search(r'占比|比例|占.{0,10}总|占.{0,10}比|份额|完成率|覆盖率', text))
    time_comp_words = bool(re.search(r'同比|环比|较上年|较去年|较前年|比上年|比去年|较上月|较前月|比上月', text) or
                           (re.search(r'较|比|对比|相较|相比', text) and re.search(r'同期|上年|去年|前年|上月|上季度', text)))
    is_comp = time_comp_words and not (is_ratio and not re.search(r'同比|环比|较上年|较去年|较上月', text))

    if is_comp and (years or month_strs):
        curr, base = None, None
        target_grain = 'year'
        if month_strs:
            target_grain = 'month'
            if len(month_strs) >= 2:
                curr, base = max(month_strs), min(month_strs)
            elif len(month_strs) == 1:
                curr = month_strs[0]
                y, m = int(curr[:4]), int(curr[5:7])
                if re.search(r'环比|较上月|比上月|较前月', text):
                    base = f"{y-1:04d}-12" if m == 1 else f"{y:04d}-{m-1:02d}"
                else:
                    base = f"{y-1:04d}-{m:02d}"
        elif years:
            target_grain = 'year'
            if len(years) >= 2:
                curr, base = max(years), min(years)
            elif len(years) == 1 and re.search(r'同比|较上年|较去年|比上年|比去年', text):
                curr = years[0]
                base = str(int(curr) - 1)

        if curr and base:
            if len(time_cols) == 1:
                table, col = time_cols[0]
                constraints.append({
                    'kind': 'comparison',
                    'table': table['name'],
                    'column': col['name'],
                    'current': curr,
                    'baseline': base,
                    'grain': col['time_semantics'].get('grain'),
                    'target_grain': target_grain,
                    'op': 'mom' if '环比' in text else 'yoy',
                    'evidence': reference,
                    **time_default,
                })
            elif len(time_cols) > 1:
                unresolved.append('用户要求期间对比，但没有唯一的已配置业务时间字段。')

    # 5. Hierarchical scope
    h_match = re.search(r'([一-鿿]{2,12}?)(?:及|含)?(?:下属|下级|子部门|所有子)', text)
    if h_match:
        parent = h_match[1]
        relations = [r for r in source.get('business_dictionary', []) if r.get('kind') == 'relation']
        has_h_rel = any(r.get('table') == r.get('to_table') or any('parent' in str(c).lower() for c in r.get('to_columns', [])) for r in relations)
        constraints.append({
            'kind': 'hierarchy',
            'parent': parent,
            'configured': has_h_rel,
            'evidence': reference
        })
        if not has_h_rel:
            unresolved.append('层级范围需要已配置关系或完整标准示例，简单名称等值无法证明覆盖下属。')

    # Ambiguous business enums
    ambiguous = {tuple(c.get('values', [])) for c in constraints if c['kind'] == 'filter'
                 and len({(x['table'], x['column']) for x in constraints if x['kind'] == 'filter' and x.get('values') == c['values']}) > 1}
    if ambiguous:
        unresolved.append('相同业务枚举命中多个对象，字段范围尚无法唯一确定。')
        constraints = [c for c in constraints if c['kind'] != 'filter' or tuple(c['values']) not in ambiguous]

    # An explicit re-grouping in a continuation turn replaces the inherited grouping axis.
    # Without this, "改为按大类看，其他不变" keeps the old 细类 group next to the new 大类 group,
    # and the stale fine-grain group blocks the correct coarse-only SQL (BUSINESS_SCOPE_CONFLICT),
    # forcing the model back to the fine grain the user just asked to leave. Non-grouping
    # constraints (filters, time, count subject) are still inherited via "其他不变".
    new_groups = [x for x in constraints if x['kind'] == 'group']
    group_changed = bool(new_groups) and bool(re.search(r'改为|换成|改成|改按|重新|重按|改看', text))
    if continuing and group_changed:
        inherited = [x for x in inherited if x['kind'] != 'group']

    if sql and engine is not None:
        from .enum_discovery import extract_verified_filters
        existing_filter_cols = {(x.get('table'), x.get('column')) for x in constraints if x.get('kind') == 'filter'}
        discovered_filters = extract_verified_filters(sql, source, parameters)
        if inherited and not clarification and (narrowed := _ONLY_FILTER_VALUE.fullmatch(original)):
            label = narrowed[1].strip('「」“”\"\' ')
            focus = {(x['table'], x['column']) for x in discovered_filters if x['op'] == 'in' and x['values'] == [label]}
            if len(focus) == 1:
                # A new literal narrowing on one field is not evidence that the
                # user also requested changing another already bound filter.
                existing_filter_cols.update((x['table'], x['column']) for x in inherited
                                            if x['kind'] == 'filter' and x.get('discovered')
                                            and (x['table'], x['column']) not in focus)
        for discovered in discovered_filters:
            if (discovered['table'], discovered['column']) not in existing_filter_cols:
                constraints.append({**discovered, 'evidence': reference})

    replaced = {(x['kind'], x.get('table'), x.get('column')) for x in constraints}
    inherited = [x for x in inherited if (x['kind'], x.get('table'), x.get('column')) not in replaced]

    if proposal:
        for item in proposal:
            if item.quote not in text:
                raise ValueError('SCOPE_EVIDENCE_MISMATCH: 范围建议必须引用真实原问或原生澄清原文。')
            if not any(t['name'] == item.table and any(c['name'] == item.column for c in t.get('columns', [])) for t in source.get('tables', [])):
                raise ValueError('SCOPE_FIELD_NOT_AUTHORIZED: 范围建议字段不在当前授权目录。')
        unresolved.append('模型范围建议仅为线索，未被服务端解析的建议尚无法验证。')

    return {'schema_version': 1, 'request': reference, 'constraints': [*inherited, *constraints],
            'unresolved': unresolved, 'config_fingerprint': fingerprint(source),
            **({'current_month': current_month} if current_month else {}),
            **({'forecast_target': {'period': forecast_target, 'grain': 'year' if len(forecast_target) == 4 else 'month',
                                    **({'horizon_start': forecast_horizon['start']}
                                       if forecast_horizon and forecast_horizon['end'] == forecast_target else {}),
                                    'evidence': reference}} if forecast_target else {}),
            'inherited': bool(inherited), 'proposal': [x.model_dump() for x in proposal or []]}


def _and_atoms(node):
    if isinstance(node, exp.Paren): return _and_atoms(node.this)
    if isinstance(node, exp.And): return _and_atoms(node.this) + _and_atoms(node.expression)
    # OR cannot establish that an individual operand applies to every row.
    if isinstance(node, exp.Or): return []
    return [node] if node is not None else []


def _inherited_fact_filter_findings(tree, source, scope):
    """Verify prior filters on a physical fact table with an optional joined aggregate."""
    ref = (scope.get('request') or {}).get('user_ref')
    inherited = [c for c in scope.get('constraints', []) if c['kind'] == 'filter' and c.get('discovered')
                 and (prior := (c.get('evidence') or {}).get('user_ref')) and prior != ref]
    if not ref or not inherited or not isinstance(tree, exp.Select) or tree.find(exp.Subquery, exp.Exists):
        return {}
    if any(tree.args.get(key) for key in ('having', 'qualify', 'limit', 'offset')):
        return {}

    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from .enum_discovery import extract_verified_filters
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    dialect = sqlglot_name(source.get('dialect'))
    if dialect not in ('sqlite', 'postgres'):
        return {}
    tree = normalize_identifiers(tree.copy(), dialect=dialect)
    outer_from = tree.args.get('from') or tree.args.get('from_')
    fact = outer_from.this if outer_from is not None else None
    table = _physical_table(fact, source, dialect)
    if not table or not any(c['table'] == table for c in inherited):
        return {}
    if any(cte.alias_or_name == fact.name for cte in tree.find_all(exp.CTE)):
        return {}
    if sum(_physical_table(item, source, dialect) == table for item in tree.find_all(exp.Table)) != 1:
        return {}
    alias = fact.alias_or_name
    joins = tree.args.get('joins') or []
    if len(joins) > 1:
        return {}
    for join in joins:
        on = join.args.get('on')
        if join.side != 'LEFT' or not isinstance(join.this, exp.Table) or on is None or on.find(exp.Or):
            return {}
        pair = {alias, join.this.alias_or_name}
        if not all(isinstance(atom, exp.EQ) and isinstance(atom.this, exp.Column)
                   and isinstance(atom.expression, exp.Column) and {atom.this.table, atom.expression.table} == pair
                   for atom in _and_atoms(on)):
            return {}
    if not any(isinstance(a.this, exp.Column) and a.this.table == alias
               for column in tree.expressions for a in column.find_all(exp.Sum, exp.Avg, exp.Count)):
        return {}
    where = tree.args.get('where')
    where_columns = list(where.find_all(exp.Column)) if where is not None else []
    if where is not None and (where.find(exp.Or) or any(col.table != alias for col in where_columns)):
        return {}

    # Optional-side filters do not prove a restriction on the preserved fact.
    # A conditional measure, grouped column or uncertain predicate can have a
    # different population, so leave those to the established unknown checks.
    projections = [*tree.expressions, tree.args.get('group')]
    output_columns = {col.name for expr in projections if expr is not None
                      for col in expr.find_all(exp.Column) if col.table in ('', alias)}
    filters = extract_verified_filters(tree.sql(dialect=dialect), source)
    findings = {}
    for c in inherited:
        col = c['column']
        if c['table'] != table or col in output_columns:
            continue
        occurrences = sum(field.name == col for field in where_columns)
        selected = [item for item in filters if item['table'] == table and item['column'] == col]
        if occurrences == 0:
            findings[(table, col)] = 'conflict'
        elif occurrences == 1 and len(selected) == 1:
            [item] = selected
            findings[(table, col)] = 'checked' if item['op'] == c['op'] and item['values'] == c['values'] else 'conflict'
    return findings


def _is_row_count(count):
    value = count.this
    return isinstance(value, exp.Star) and not any(value.args.values()) or (
        isinstance(value, exp.Literal) and value.is_int and value.this == '1'
    )


def _published_row_subjects(tree, table, source, dialect, requested):
    """Find declarations and published entity-count examples within the queried population."""
    from .display_units import _alias_unit
    from .metric_subject import COUNT_UNITS, projected_count_subjects
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

    def subjects(name):
        return projected_count_subjects(_explicit_count_text(name))

    declared = set()
    for mapping in source.get('business_dictionary') or []:
        if (mapping.get('kind') == 'table' and mapping.get('source_id') == source.get('id')
                and mapping.get('table') == table['name']):
            # The label of a table can name its topic without establishing its grain.
            # An explicit count-object phrase (e.g. 客户数量) is a stronger declaration.
            declared.update(subjects(mapping.get('name')))
    def filters(where):
        def unqualified(node):
            if isinstance(node, exp.Column):
                return exp.column(node.name, quoted=node.this.args.get('quoted'))
            return node

        if where is None:
            return {}
        if where.find(exp.Or):
            return None
        result = {}
        for atom in _and_atoms(where.this):
            node = normalize_identifiers(atom.transform(unqualified), dialect=dialect)
            result[node.sql(dialect=dialect)] = node
        return result

    def date_lower_bound(node):
        if not isinstance(node, (exp.GT, exp.GTE)) or not isinstance(node.this, exp.Column):
            return None
        value = node.expression
        if isinstance(value, exp.Cast) and value.to.this == exp.DataType.Type.DATE:
            value = value.this
        elif dialect == 'sqlite' and isinstance(value, exp.Date) and not value.expressions and not value.args.get('zone'):
            value = value.this
        if not isinstance(value, exp.Literal) or not value.is_string or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value.this):
            return None
        try:
            return node.this.name, date.fromisoformat(value.this), isinstance(node, exp.GT)
        except ValueError:
            return None

    current = filters(tree.args.get('where'))
    if current is None:
        return declared
    predicates = []
    for projection in tree.expressions:
        node = projection.unalias()
        if isinstance(node, exp.Count):
            predicates.append(current)
        elif isinstance(node, exp.Filter) and (local := filters(node.expression)) is not None:
            predicates.append({**current, **local})
    # The SQL being checked may count a broader population than the example
    # (e.g. all cities versus one). A contradictory or unproven extra filter
    # cannot establish that the requested population has the example's grain.
    table_name = table['name'].casefold()
    bare = table_name.split('.')[-1]
    unqualified_unique = sum(t['name'].casefold().split('.')[-1] == bare for t in source.get('tables', [])) == 1
    matching = set()
    classified = set()
    for example in source.get('examples') or []:
        name = example.get('question') or ''
        sql = example.get('logical_sql') or ''
        if example.get('source_id') != source.get('id') or bare not in sql.casefold():
            continue
        described = subjects(name)
        if len(described) != 1:
            continue
        try:
            sample = sqlglot.parse_one(sql, read=dialect)
        except (ValueError, sqlglot.errors.SqlglotError):
            continue
        if not isinstance(sample, exp.Select) or len(sample.expressions) != 1 or sample.find(exp.Join, exp.CTE, exp.Subquery):
            continue
        output = sample.expressions[0]
        count = output.unalias()
        registered = filters(sample.args.get('where'))
        if not isinstance(count, exp.Count) or not _is_row_count(count):
            continue
        relations = list(sample.find_all(exp.Table))
        if len(relations) != 1:
            continue
        name = '.'.join(p.name.casefold() for p in relations[0].parts)
        if not (name == table_name or unqualified_unique and name == bare):
            continue
        # A competing published grain makes a table-wide inference unsafe.
        matching.update(described & requested)
        # For an explicitly classified count, the query may cover a broader
        # population. A generic count unit only supports the existing narrow
        # overlap check below.
        if registered is None or _alias_unit(output.alias) not in COUNT_UNITS - {'条', '件', '个', '笔'}:
            continue
        bounds = {bound for node in registered.values() if (bound := date_lower_bound(node))}
        if any(all((candidate := date_lower_bound(where[key])) and
                   any(column == candidate[0] and (day, strict) >= candidate[1:] for column, day, strict in bounds)
                   for key in where.keys() - registered.keys()) for where in predicates):
            classified.update(described)
    return declared | classified | matching


def _published_row_count_subjects(tree, table, source, dialect, requested):
    """Read published row-count examples for the same table and a contained scope."""
    from .metric_subject import projected_count_subjects
    from .vendor.plans import _cte_reference_ids

    # The only supported scope proof is a shared conjunction with equal
    # predicates. Cases with OR, columns on both sides, or other expressions
    # need their own domain evidence and are left to ordinary scope checking.
    def equal_filters(select):
        where = select.args.get('where')
        conditions = _and_atoms(where.this) if where else []
        filters = set()
        for cond in conditions:
            if not isinstance(cond.this, exp.Column):
                return None
            if isinstance(cond, exp.EQ) and isinstance(cond.expression, exp.Literal):
                values = (cond.expression,)
            elif isinstance(cond, exp.In) and cond.expressions and all(isinstance(v, exp.Literal) for v in cond.expressions):
                values = cond.expressions
            else:
                return None
            filters.add((cond.this.name.lower(), frozenset((v.is_string, v.this) for v in values)))
        return filters if not where or filters else None

    current = equal_filters(tree)
    if not current:
        return set()
    name = table['name'].lower()
    collected = set()
    overlaps = False
    for example in source.get('examples') or []:
        sql = example.get('logical_sql') or ''
        if (example.get('source_id') != source.get('id') or not sql
                or name.split('.')[-1] not in sql.lower()):
            continue
        subjects = projected_count_subjects(_explicit_count_text(example.get('question')))
        if not subjects:
            continue
        try:
            sample = sqlglot.parse_one(sql, read=dialect)
        except (ValueError, sqlglot.errors.SqlglotError):
            continue
        if not isinstance(sample, exp.Select) or len(sample.expressions) != 1:
            continue
        count = sample.expressions[0].unalias()
        if not isinstance(count, exp.Count) or not _is_row_count(count):
            continue
        if sample.find(exp.Join):
            continue
        cte_refs = _cte_reference_ids(sample)
        physical = {'.'.join(p.name.lower() for p in ref.parts)
                    for ref in sample.find_all(exp.Table) if id(ref) not in cte_refs}
        if len(physical) != 1 or not any(name == ref or name.endswith('.' + ref) for ref in physical):
            continue
        if len(subjects) != 1:
            # An ambiguous name cannot prove a conflicting object. If it
            # actually names the requested object, it can rule out a rejection.
            collected.update(subjects & requested)
            continue
        collected.update(subjects)
        registered = equal_filters(sample)
        if not sample.find(exp.Subquery, exp.CTE) and registered is not None and current <= registered:
            overlaps = True
    return collected if overlaps else set()


def _count_output_conflict(tree, source, scope, queried_tables, origin):
    from .metric_subject import declared_subjects, projected_count_subjects

    quote = (scope.get('request') or {}).get('quote')
    acquisition_quote = quote or ''
    if (scope.get('forecast_target') and
            (training := re.search(r'(?:预测|预估|展望)\s*(?:基于|根据|用|按|使用|依据)([^，,。；;\n]+)[，,]\s*(?:预测|预估|展望)', acquisition_quote))):
        # The history describes the counted object. The following target may
        # abbreviate it to an action such as 批准量 or 受理量.
        acquisition_quote = re.sub(r'(?<=[一-鿿])(?:数据|记录)量(?:情况)?\s*$', '数量', training[1])
    requested = projected_count_subjects(_explicit_count_text(acquisition_quote))
    if re.search(r'多少', acquisition_quote) and not _extract_count_subjects(acquisition_quote) <= requested:
        return None
    if len(requested) != 1:
        return None
    aggregates = [agg for projection in tree.expressions for agg in projection.find_all(exp.AggFunc)]
    tables = [t for t in source.get('tables', []) if t['name'].split('.')[-1].lower() in queried_tables]
    wanted = next(iter(requested))
    if len(tables) == 1 and aggregates and all(isinstance(a, exp.Count) and _is_row_count(a) for a in aggregates):
        # Several counts may represent one requested entity split by condition.
        # Their column aliases cannot make the counted table a different entity.
        direct_counts = all(isinstance((node := p.unalias()), exp.Count) or
                            isinstance(node, exp.Filter) and isinstance(node.this, exp.Count)
                            for p in tree.expressions if p.find(exp.Count))
        table = tables[0]
        known = any(c['kind'] == 'count_subject' and c.get('table') == table['name'] and c['subject'] == wanted
                    and (c.get('is_pk') or c.get('entity_proven')) for c in scope.get('constraints', []))
        if direct_counts and not known:
            dialect = DIALECTS.get(source.get('dialect'), source.get('dialect') or None)
            declared = _published_row_subjects(tree, table, source, dialect, requested)
            if len(declared) == 1 and not requested & declared:
                existing = next(iter(declared))
                return (f'用户明确统计“{wanted}”，而当前表的已发布行计数口径为“{existing}”。'
                        f'当前查询没有核实“{wanted}”的实体键或同口径计数。请使用与原问题一致的授权数据；'
                        '缺少相应依据时说明无法核实，不用其他对象的记录数替代。')
    if len(aggregates) != 1 or not isinstance(aggregates[0], exp.Count):
        return None
    count = aggregates[0]
    output = count.parent
    direct = count in tree.expressions or isinstance(output, exp.Alias) and output in tree.expressions
    if not direct:
        return None

    # Only aliases registered on the counted table or its actual key can
    # establish that the different words mean the same entity.
    stated = projected_count_subjects(_explicit_count_text(output.alias)) if isinstance(output, exp.Alias) else set()
    if len(tables) == 1 and _is_row_count(count) and not tree.find(exp.Or):
        table = tables[0]
        known = any(
            c['kind'] == 'count_subject' and c.get('table') == table['name'] and c['subject'] == wanted
            for c in scope.get('constraints', [])
        )
        if not known:
            dialect = DIALECTS.get(source.get('dialect'), source.get('dialect') or None)
            alternatives = _published_row_count_subjects(tree, table, source, dialect, requested)
            if len(alternatives) == 1 and not requested & alternatives:
                existing = next(iter(alternatives))
                return (f'用户明确统计“{wanted}”，当前 SQL 的行计数与已发布示例的“{existing}”记录重叠，'
                        f'授权目录尚未给出能证明这里每一行就是“{wanted}”的定义或实体键。'
                        f'请按原问题选择具备“{wanted}”口径和标识的授权数据；'
                        '改写结果列名不能建立口径。若现有来源无相应数据，请说明该项目前无法计算。')
    if len(stated) != 1 or requested & stated:
        return None
    counted = count.this
    if isinstance(counted, exp.Distinct) and len(counted.expressions) == 1:
        counted = counted.expressions[0]
    if len(tables) == 1:
        table = tables[0]
        key = origin(counted)
        keys = [table.get('primary_key') or [], *(table.get('unique_keys') or [])]
        entity_key = key and any(len(k) == 1 and key[1] == k[0].lower() for k in keys)
        annotations = [table] if _is_row_count(count) or entity_key else []
        if entity_key:
            annotations += [col for col in table.get('columns', []) if col['name'].lower() == key[1]]
        for entity in annotations:
            names = [entity.get('business_name'), entity.get('label'), *(entity.get('aliases') or [])]
            synonyms = set().union(*(declared_subjects(name) for name in names if name))
            if (requested | stated) <= synonyms:
                return None

    produced = next(iter(stated))
    return (f'用户明确统计“{wanted}”，SQL 的计数结果列“{output.alias}”统计“{produced}”。'
            '请核对授权目录中原统计对象、计数键和业务范围后重新查询。仅改标题或列名无法核对计数口径；'
            '如果授权来源缺少原统计对象所需字段，请说明当前无法计算。')


def _group_terms(tree, inputs, fold):
    group = tree.args.get('group')
    if not group or any(value for name, value in group.args.items() if name != 'expressions'):
        return []
    terms = []
    for term in group.expressions:
        term = term.unnest()
        if isinstance(term, exp.Literal) and term.is_int:
            index = int(term.this) - 1
            if not 0 <= index < len(tree.expressions) or any(item.unalias().is_star for item in tree.expressions):
                return []
            term = tree.expressions[index].unalias().unnest()
        elif isinstance(term, exp.Column) and not term.table and fold(term.name) not in inputs:
            aliases = [item.unalias().unnest() for item in tree.expressions if fold(item.alias) == fold(term.name)]
            if len(aliases) != 1:
                return []
            term = aliases[0]
        terms.append(term)
    return terms


def _derived_count_conflict(tree, source, constraint):
    """Detect finer-date deduplication or a global first occurrence in place of per-period counting."""
    from sqlglot.optimizer.scope import Scope, build_scope
    from .period_grain import Period, _DATE_TYPE, _GRAINS, _ORDER, _period, requested_grain
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    if not isinstance(tree, exp.Select) or tree.args.get('joins'):
        return None
    counts = [item.unalias() for item in tree.expressions if isinstance(item.unalias(), exp.Count)]
    root = build_scope(tree)
    if len(counts) != 1 or not root or len(root.selected_sources) != 1:
        return None
    alias, (_, derived) = next(iter(root.selected_sources.items()))
    if not isinstance(derived, Scope) or derived.outer_columns or len(derived.selected_sources) != 1:
        return None
    inner = derived.expression
    if not isinstance(inner, exp.Select) or inner.args.get('joins'):
        return None
    _, (physical, relation) = next(iter(derived.selected_sources.items()))
    dialect = sqlglot_name(source.get('dialect'))
    if _physical_table(relation, source, dialect) != constraint['table']:
        return None
    fold = str.casefold if dialect == 'sqlite' else str

    def column_name(item):
        return fold(item.name) if isinstance(item, exp.Column) and not item.is_star else None

    def from_alias(column, expected):
        return (column_name(column) and not column.db and not column.catalog
                and (not column.table or fold(column.table) == fold(expected)))

    if any(item.is_star for item in inner.expressions):
        return None
    projections = {fold(item.alias_or_name): item.unalias().unnest() for item in inner.expressions}
    if len(projections) != len(inner.expressions) or not all(projections):
        return None
    entity = fold(constraint['column'])
    entity_fields = {name for name, item in projections.items() if column_name(item) == entity and from_alias(item, physical.alias_or_name)}
    if len(entity_fields) != 1:
        return None
    terms = _group_terms(tree, projections, fold)
    if not terms:
        return None
    table = next(item for item in source['tables'] if item['name'] == constraint['table'])
    columns = {fold(col['name']): col for col in table['columns']}

    def resolve_period(column, *, extreme=False):
        if not from_alias(column, alias) or column_name(column) in entity_fields:
            return None
        item = projections.get(column_name(column))
        if extreme:
            item = item.this if isinstance(item, (exp.Min, exp.Max)) and not item.expressions else None
        if not from_alias(item, physical.alias_or_name):
            return None
        col = columns.get(column_name(item), {})
        semantics = col.get('time_semantics') or {}
        grain = _GRAINS.get(semantics.get('grain')) if semantics else (
            'day' if _DATE_TYPE.fullmatch(col.get('type') or '') else None)
        return Period(grain, constraint['table'], column_name(column)) if grain else None

    count = counts[0]
    if _is_row_count(count) and isinstance(distinct := inner.args.get('distinct'), exp.Distinct) and not distinct.args.get('on'):
        group_fields = {column_name(term) for term in terms if from_alias(term, alias)}
        for term in terms:
            period = _period(term, resolve_period)
            if not period or period.part or period.column in group_fields:
                continue
            base = resolve_period(exp.column(period.column))
            if base and _ORDER[period.grain] < _ORDER[base.grain]:
                return '当前子查询按更细日期去重'

    quote = constraint['evidence'].get('quote') or ''
    expected = requested_grain({'user_text': quote})
    if expected is None or re.search(r'首次|第一次|首笔|首回|初次|初始|最早|最后|末次|最近一次|最新一次|首发', quote):
        return None
    inner_groups = _group_terms(inner, columns, fold)
    if len(inner_groups) != 1 or column_name(inner_groups[0]) != entity or not from_alias(inner_groups[0], physical.alias_or_name):
        return None
    operand = count.this
    if not _is_row_count(count) and (not isinstance(operand, exp.Distinct) or len(operand.expressions) != 1
            or not from_alias(operand.expressions[0], alias) or column_name(operand.expressions[0]) not in entity_fields):
        return None
    if len(terms) == 1:
        period = _period(terms[0], lambda column: resolve_period(column, extreme=True))
        if period and not period.part and period.grain == expected:
            return '当前查询先为每个对象选取全期间首条或末条日期再分组，遗漏同一对象在其他期间的记录'
    return None


def _same_sqlite_join_comparison(source, parent_key, joined_key, engine, ctx):
    """Confirm that both physical keys use the same plain SQLite comparison."""
    from contextlib import closing
    from pathlib import Path
    import sqlite3
    from .sqlite_connection import connect_readonly

    if source.get('dialect') != 'sqlite' or engine is None or ctx is None:
        return False
    loaded = engine.source(ctx.project, source['id'])
    if any(loaded.get(field) != source.get(field) for field in ('connection_revision', 'business_project')):
        return False
    keys = (parent_key, joined_key)
    path = loaded.get('path')
    if not path:
        return False
    try:
        with closing(connect_readonly(Path(path).resolve(), timeout=0.25)) as conn:
            definitions = dict(conn.execute(
                "SELECT name, sql FROM main.sqlite_schema WHERE type='table' AND name IN (?, ?)",
                (parent_key[0], joined_key[0])))
            if any(not re.match(r'CREATE\s+TABLE\s', definitions.get(table) or '', re.I)
                   or re.search(r'\bCOLLATE\b', definitions[table], re.I) for table, _ in keys):
                return False
            if conn.execute("SELECT name FROM pragma_table_xinfo(?) WHERE pk>0", (parent_key[0],)).fetchall() != [(parent_key[1],)]:
                return False
            types = [conn.execute(
                "SELECT upper(type) FROM pragma_table_xinfo(?) WHERE name=? AND hidden=0", (table, field)
            ).fetchone() for table, field in keys]
    except (OSError, sqlite3.Error):
        return False
    return types[0] == types[1] and types[0] in {('TEXT',), ('INTEGER',), ('INT',)}


def _joined_count_candidates(tree, source, constraints, *, engine=None, ctx=None):
    """Resolve which count constraints are met by a count joining the same entity key."""
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from .snapshot_scope import _physical_table
    from .vendor.dialect import sqlglot_name

    joins = tree.args.get('joins') or []
    if len(joins) != 1 or not constraints:
        return set()
    join = joins[0]
    if join.side or join.method or join.kind not in ('', 'INNER') or not join.args.get('on'):
        return set()
    dialect = sqlglot_name(source.get('dialect'))
    tree = normalize_identifiers(tree.copy(), dialect=dialect)
    join = tree.args['joins'][0]
    outer_from = tree.args.get('from') or tree.args.get('from_')
    if not outer_from:
        return set()
    refs = [outer_from.this, join.this]
    tables = {ref.alias_or_name: _physical_table(ref, source, dialect) for ref in refs}
    if len(tables) != 2 or None in tables.values() or len(set(tables.values())) != 2:
        return set()

    metadata = {table['name']: table for table in source['tables']}
    fold = str.casefold if dialect == 'sqlite' else str

    def qualified(column):
        column = column.unnest()
        if (not isinstance(column, exp.Column) or column.is_star or column.db or column.catalog
                or column.table not in tables):
            return None
        physical = tables[column.table]
        fields = [c['name'] for c in metadata[physical].get('columns', []) if fold(c['name']) == fold(column.name)]
        return (physical, fields[0]) if len(fields) == 1 else None

    targets = []
    counts = list(tree.find_all(exp.Count))
    for count in counts:
        if not isinstance(count.this, exp.Distinct) or len(count.this.expressions) != 1:
            return set()
        target = qualified(count.this.expressions[0])
        if target is None:
            return set()
        targets.append(target)
    if not targets or len(set(targets)) != 1:
        return set()
    counted = targets[0]
    on = join.args['on']
    atoms = _and_atoms(on)
    equalities = {frozenset((qualified(atom.this), qualified(atom.expression)))
                  for atom in atoms if isinstance(atom, exp.EQ)}
    key_constraints = [c for c in constraints if c.get('is_pk') and c.get('distinct_required')]
    if metadata[counted[0]].get('primary_key') == [counted[1]]:
        # The chosen primary key is the requested entity. Ignore the other
        # joined table's same-subject, non-key candidate rather than count both.
        subjects = {c['subject'] for c in key_constraints if (c['table'], c.get('column')) == counted}
        return {(c['subject'], c['table'], c['column']) for c in constraints
                if c['subject'] in subjects and not c.get('is_pk') and c.get('distinct_required')
                and c.get('column') and c['table'] != counted[0]
                and frozenset((counted, (c['table'], c['column']))) in equalities}

    literal = (exp.Literal, exp.Boolean, exp.Null)
    if (on.find(exp.Or) or tree.find(exp.Window, exp.Filter)
            or any(count.find_ancestor(exp.Select) is not tree for count in counts)
            or any(not (isinstance(atom, exp.Binary) and isinstance(atom, exp.Predicate))
                   or any(not (isinstance(side.unnest(), literal) or qualified(side))
                          for side in (atom.this, atom.expression))
                   for atom in atoms)):
        return set()
    joined_pairs = [pair for pair in equalities if None not in pair and len({endpoint[0] for endpoint in pair}) == 2]
    if len(joined_pairs) != 1 or counted not in joined_pairs[0]:
        return set()
    primary = next(key for key in joined_pairs[0] if key != counted)
    matching = {(c['subject'], c['table'], c['column']) for c in key_constraints
                if (c['table'], c.get('column')) == primary and c.get('entity_proven') is not False}
    if matching and metadata[primary[0]].get('primary_key') == [primary[1]]:
        if _same_sqlite_join_comparison(source, primary, counted, engine, ctx):
            return matching
    return set()


def inspect_sql(sql, source, scope, parameters=None, *, published_grouping=None, engine=None, ctx=None):
    raw_tree = sqlglot.parse_one(sql, read=DIALECTS.get(source.get('dialect'), source.get('dialect') or None))
    bound = {p['name']: p.get('value') for p in parameters or [] if isinstance(p, dict) and 'name' in p}
    def bind(node):
        if isinstance(node, (exp.Placeholder, exp.Parameter)):
            k = str(node.this) if getattr(node, 'this', None) else ''
            if k in bound:
                return exp.convert(bound[k])
        return node
    tree = raw_tree.transform(bind)
    findings = []
    def add(area, state, detail, **extra):
        findings.append({'area': area, 'state': state, 'label': LABELS[state], 'detail': detail, **extra})

    from .temporal_context import temporal_context
    today = date.fromisoformat(temporal_context((source or {}).get('client_time_zone', 'UTC'))['current_date'])

    simple = isinstance(tree, exp.Select) and not any(tree.find_all(exp.Subquery, exp.CTE))
    complex_scope = not simple or tree.find(exp.Case) is not None
    inherited_fact = _inherited_fact_filter_findings(tree, source, scope) if complex_scope else {}
    tables = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
    def origin(c):
        if not isinstance(c, exp.Column): return None
        if c.table: return (tables.get(c.table.lower()), c.name.lower())
        matches = [t['name'].split('.')[-1].lower() for t in source.get('tables', []) if t['name'].split('.')[-1].lower() in tables.values() and any(x['name'].lower() == c.name.lower() for x in t.get('columns', []))]
        return (matches[0], c.name.lower()) if len(matches) == 1 else None

    atoms = _and_atoms(tree.args.get('where').this if tree.args.get('where') else None)
    queried_tables = set(tables.values())
    has_joins = bool(list(tree.find_all(exp.Join)))
    kinds = {c['kind'] for c in scope.get('constraints', [])}
    relation_scope = _direct_relations(raw_tree, source) if simple and kinds & {'group', 'time', 'comparison'} else None
    group_fields = set()
    if relation_scope:
        select, related_tables, key = relation_scope
        names = {table['name']: {key(c['name']) for c in table.get('columns', [])} for table in source.get('tables', [])}
        inputs = set().union(*(names[table] for table in related_tables.values()))

        def physical_origin(column):
            if not isinstance(column, exp.Column) or column.is_star:
                return None
            field = key(column.name)
            qualified = key('.'.join(part.name for part in column.parts[:-1])) if column.db or column.catalog else None
            matches = [table for alias, table in related_tables.items() if field in names[table]
                       and (not column.table or key(column.table) == alias)
                       and (not qualified or key(table) == qualified)]
            return (matches[0], field) if len(matches) == 1 else None

        if 'group' in kinds:
            # A bound parameter is a constant, not a SQL projection ordinal.
            terms = _group_terms(select, inputs, key)
            if not terms and (group := select.args.get('group')):
                terms = group.expressions  # Preserve direct field checks for other GROUP BY forms.
            group_fields = {physical_origin(term) for term in terms}
        parsed_where = select.args.get('where')
        physical_where = parsed_where.transform(bind) if parsed_where is not None else None
        physical_atoms = _and_atoms(physical_where.this if physical_where else None)
    scoped_enums = [c for c in scope.get('constraints', []) if c['kind'] == 'filter' and c.get('enum_source_bound')]
    referenced_enum_tables = set()
    if scoped_enums:
        from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
        from sqlglot.optimizer.scope import traverse_scope
        from .snapshot_scope import _physical_table
        from .vendor.dialect import sqlglot_name

        dialect = sqlglot_name(source.get('dialect'))
        referenced_enum_tables = {physical for select in traverse_scope(normalize_identifiers(tree.copy(), dialect=dialect))
                                  for _, ref in select.selected_sources.values()
                                  if (physical := _physical_table(ref, source, dialect))}

    if simple and not has_joins and len(queried_tables) == 1:
        if conflict := _count_output_conflict(tree, source, scope, queried_tables, origin):
            add('grain', 'conflict', conflict)

    # Check if count constraints match queried tables
    count_constraints = [c for c in scope.get('constraints', []) if c['kind'] == 'count_subject']
    joined_candidates = (_joined_count_candidates(tree, source, count_constraints, engine=engine, ctx=ctx)
                         if simple and has_joins else set())
    if count_constraints and not any(c.get('table', '').split('.')[-1].lower() in queried_tables for c in count_constraints):
        subj = count_constraints[0]['subject']
        # Name matches propose candidate tables; their absence is not proof
        # that another authorized table cannot represent the requested entity.
        add('grain', 'unknown', f"用户明确统计“{subj}”，当前查询未使用目录名称匹配的候选表，需结合实际实体定义核对。")

    for c in scope.get('constraints', []):
        if c['kind'] == 'current_month':
            from .current_month import inspect as inspect_current_month
            state, detail = inspect_current_month(tree, source, c)
            add('scope', state, detail, constraint=c)
            continue

        # 1. Hierarchy scope check
        if c['kind'] == 'hierarchy':
            parent = c['parent']
            eq_atoms = [a for a in atoms if isinstance(a, exp.EQ) and (
                (isinstance(a.this, exp.Literal) and str(a.this.this) == parent) or
                (isinstance(a.expression, exp.Literal) and str(a.expression.this) == parent))]
            has_subquery = bool(list(tree.args.get('where').find_all(exp.Exists, exp.Subquery)) if tree.args.get('where') else [])
            cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
            used_ctes = bool(cte_names and any(t.name.lower() in cte_names for t in tree.find_all(exp.Table)))
            has_hierarchy_structure = has_subquery or has_joins or used_ctes

            where_node = tree.args.get('where')
            in_atoms = [a for a in (where_node.find_all(exp.In) if where_node else [])
                        if any(parent in str(x.this) for x in a.expressions if isinstance(x, exp.Literal))]

            if eq_atoms and not has_hierarchy_structure:
                add('scope', 'conflict',
                    f"用户要求包含“{parent}及下属部门”，SQL仅在表上等值匹配单一名称“{parent}”，遗漏了下属层级范围；请通过层级关联或递归保留下属范围，不能只匹配单一名称。",
                    constraint=c)
            elif in_atoms or has_hierarchy_structure:
                # IN subordinates or qualified unknown structure => unknown, cannot classify as conflict!
                add('scope', 'unknown', f"SQL包含层级查询结构或下属集合，静态AST无法证明递归闭包完整性；需由独立业务口径核对。", constraint=c)
            else:
                any_parent_mention = any(parent in str(lit.this) for lit in (where_node.find_all(exp.Literal) if where_node else []))
                if any_parent_mention:
                    add('scope', 'unknown', f"SQL包含非等值层级条件，静态AST无法证明递归闭包完整性；需由独立业务口径核对。", constraint=c)
                else:
                    add('scope', 'conflict', f"用户要求包含“{parent}及下属部门”，SQL缺少对应层级筛选条件。", constraint=c)
            continue

        # 1b. Published business scope: filters must keep the registered population
        if c['kind'] == 'published_scope':
            from .published_scope import inspect as inspect_published_scope
            for state, detail in inspect_published_scope(tree, c, source, simple):
                add('scope', state, detail, constraint=c)
            continue

        from .raw_dimension_scope import KIND as raw_dimension_kind, inspect as inspect_raw_dimension
        if c['kind'] == raw_dimension_kind:
            if result := inspect_raw_dimension(tree, source, c, published_grouping):
                add('scope', *result, constraint=c)
            continue

        from .qualification_scope import KINDS as qualification_kinds, inspect as inspect_qualification_scope
        if c['kind'] in qualification_kinds:
            if result := inspect_qualification_scope(tree, source, c):
                add('scope', *result, constraint=c)
            continue
        from .holding_rate_scope import KIND as holding_rate_kind, inspect as inspect_holding_rate
        if c['kind'] == holding_rate_kind:
            if result := inspect_holding_rate(tree, source, c):
                state, detail, proof = result
                add('scope', state, detail, constraint=c, **({'holding_rate': proof} if proof else {}))
            continue
        from .cancellation_scope import KIND as cancellation_kind, inspect as inspect_cancellation
        if c['kind'] == cancellation_kind:
            if result := inspect_cancellation(tree, source, c):
                add('scope', *result, constraint=c)
            continue

        # 2. Count subject and entity grain check
        if c['kind'] == 'count_subject':
            c_table = c.get('table', '').split('.')[-1].lower()
            if c_table and c_table not in queried_tables:
                continue
            if (c['subject'], c['table'], c.get('column')) in joined_candidates:
                if c.get('is_pk'):
                    add('grain', 'checked', f"关联字段与“{c['subject']}”的单列主键等值，已核对去重计数。", constraint=c)
                continue

            # Complex CTEs or pre-aggregations
            if not simple and c.get('once_per_entity') and (conflict := _derived_count_conflict(tree, source, c)):
                add('grain', 'conflict', f"用户明确每个“{c['subject']}”同一期间只统计一次，{conflict}。"
                    f"请在原期间分桶内使用 COUNT(DISTINCT {c['column']})，避免同一对象跨日重复计入。", constraint=c)
                continue
            if not simple or c.get('entity_proven') is False:
                reason = '包含CTE或子查询的复杂查询' if not simple else '字段未登记主键或实体关联外键'
                add('grain', 'unknown', f"{reason}，统计对象“{c['subject']}”等价性尚无法静态证明。", constraint=c)
                continue

            counts = list(tree.find_all(exp.Count))
            if not counts:
                if list(tree.find_all(exp.Sum)):
                    add('grain', 'unknown', f"SQL使用 SUM 预聚合计算“{c['subject']}”，等价性尚无法静态证明。", constraint=c)
                    continue
                # Exploratory / detail SELECT without COUNT must be unknown, not conflict!
                add('grain', 'unknown', f"当前查询未包含计数聚合（可能为明细或探索性查询），统计对象“{c['subject']}”粒度尚无法静态证明。", constraint=c)
                continue

            # Check for COUNT(CASE ...)
            case_counts = [cnt for cnt in counts if list(cnt.find_all(exp.Case))]
            if case_counts:
                add('grain', 'unknown', f"COUNT中包含CASE条件表达式，非全量实体去重，等价性尚无法静态证明。", constraint=c)
                continue

            target = (c_table, c['column'].lower()) if c.get('column') else None
            valid_distinct = False
            has_wrong_distinct = False
            has_star_or_plain = False

            for cnt in counts:
                is_distinct = bool(cnt.args.get('distinct')) or isinstance(cnt.this, exp.Distinct)
                col_nodes = list(cnt.find_all(exp.Column))
                if is_distinct:
                    if target and any(origin(col) == target or (not col.table and col.name.lower() == target[1]) for col in col_nodes):
                        valid_distinct = True
                    else:
                        has_wrong_distinct = True
                else:
                    has_star_or_plain = True

            if c.get('distinct_required'):
                if valid_distinct:
                    add('grain', 'checked', f"核对按“{c['subject']}”实体去重计数（COUNT(DISTINCT {c.get('column')})）。", constraint=c)
                else:
                    add('grain', 'conflict',
                        f"用户明确统计“{c['subject']}”去重实体数量，SQL使用 COUNT(*) 或未去重计数会重复统计同一{c['subject']}；请使用 COUNT(DISTINCT {c.get('column')}) 去重统计。",
                        constraint=c)
            else:
                if has_wrong_distinct:
                    add('grain', 'conflict', f"用户要求统计“{c['subject']}”，SQL统计了其他对象的去重数，不能替代原统计对象。", constraint=c)
                elif has_joins and has_star_or_plain:
                    # Multi-table join with COUNT(*) might amplify rows
                    add('grain', 'unknown', f"多表关联查询使用 COUNT(*) 可能会因关联放大行数，等价性尚无法静态证明。", constraint=c)
                else:
                    add('grain', 'checked', f"核对统计“{c['subject']}”记录数符合要求。", constraint=c)
            continue

        target = (c['table'].split('.')[-1].lower(), c['column'].lower()) if c.get('table') and c.get('column') else None
        if c.get('enum_source_bound') and c['table'] not in referenced_enum_tables:
            add('scope', 'unknown', '问句范围无法从本次查询所用来源字段验证。已登记的枚举只适用于其原表，'
                '请按当前查询对象核对实际区域或说明覆盖范围。', constraint=c)
            continue
        if complex_scope:
            state = inherited_fact.get((c.get('table'), c.get('column'))) if c['kind'] == 'filter' else None
            if state:
                add('scope', state, '核对续问保留的事实表筛选。' if state == 'checked' else
                    '续问保留的事实表筛选缺失或已改变。请在同一事实表的 WHERE 中保留原值。', constraint=c)
            else:
                add('scope', 'unknown', '复杂表达式的范围等价尚无法验证。', constraint=c)
            continue

        if c['kind'] == 'group':
            if not relation_scope:
                add('scope', 'unknown', '无法唯一核对查询使用的物理关系和请求分组字段。', constraint=c)
                continue
            checked = (c['table'], key(c['column'])) in group_fields
            add('scope', 'checked' if checked else 'conflict', '核对用户要求的分组字段。', constraint=c)
            continue

        selected = []
        local_atoms = atoms
        resolve = origin
        scoped_where = tree.args.get('where')
        if c['kind'] in ('time', 'comparison') and relation_scope:
            if c['table'] not in related_tables.values():
                add('scope', 'unknown' if c.get('catalog_time_default') else 'conflict',
                    '目录中唯一登记的时间字段属于本次查询未使用的表，尚无法核实本次查询的时间区间。'
                    if c.get('catalog_time_default') else '本次查询未使用原问明确指定或沿用的时间字段所在表。',
                    constraint=c)
                continue
            target = (c['table'], key(c['column']))
            resolve, local_atoms, scoped_where = physical_origin, physical_atoms, physical_where
        # An optional fact table can be filtered in ON while dimensions with no
        # matching facts remain visible. This proves the SUM's input, not that
        # the preserved dimension population itself has the requested status.
        if c['kind'] == 'filter' and simple and target and (joins := tree.args.get('joins')):
            join = joins[-1]
            where = tree.args.get('where')
            aggregates = list(tree.find_all(exp.AggFunc))
            def optional_measure(aggregate):
                value = aggregate.this
                if isinstance(value, exp.Cast): value = value.this
                mapped = origin(value)
                return isinstance(aggregate, exp.Sum) and isinstance(value, exp.Column) and bool(mapped) and mapped[0] == target[0]
            if (join.side == 'LEFT' and isinstance(join.this, exp.Table)
                    and tables.get(join.this.alias_or_name.lower()) == target[0]
                    and aggregates and all(optional_measure(a) for a in aggregates)
                    and not tree.args.get('having')
                    and not (where and any(origin(col) and origin(col)[0] == target[0] for col in where.find_all(exp.Column)))):
                local_atoms = [*atoms, *_and_atoms(join.args.get('on'))]
        for atom in local_atoms:
            if isinstance(atom, (exp.EQ, exp.NEQ)) and isinstance(atom.this, exp.Literal) and resolve(atom.expression) == target:
                atom = type(atom)(this=atom.expression.copy(), expression=atom.this.copy())
            if resolve(atom.this) == target or isinstance(atom, exp.Not) and resolve(atom.this.this) == target:
                selected.append(atom)

        if c['kind'] == 'filter':
            found = []
            for node in selected:
                negative = isinstance(node, (exp.NEQ, exp.Not))
                expr = node.this if isinstance(node, exp.Not) else node
                if isinstance(expr, (exp.EQ, exp.NEQ)) and isinstance(expr.expression, exp.Literal):
                    values = [str(expr.expression.this)]
                elif isinstance(expr, exp.In) and all(isinstance(x, exp.Literal) for x in expr.expressions):
                    values = [str(x.this) for x in expr.expressions]
                else:
                    continue
                if negative == (c['op'] == 'not_in'):
                    found.append(set(values))
            checked = any(v == set(c['values']) for v in found)
            decidable = any(isinstance(a, (exp.EQ, exp.NEQ, exp.In)) or isinstance(a, exp.Not) and isinstance(a.this, exp.In) for a in selected)
            add('scope', 'checked' if checked else 'conflict' if decidable or not selected else 'unknown',
                '筛选与原问核对。' if checked else '筛选缺失、扩大或暂不能证明等价。', constraint=c)

        # 3. Time bounds: Snapshot vs Period, Months, Years
        elif c['kind'] == 'time':
            if c.get('mode') == 'snapshot' and c.get('cutoff'):
                cutoff_str = c['cutoff']
                try:
                    cutoff_d = date.fromisoformat(cutoff_str)
                except ValueError:
                    cutoff_d = None

                if c.get('grain') == 'year' and cutoff_d:
                    # Static YYYY/year snapshot allows equality to cutoff year
                    year_match = any(isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal) and str(a.expression.this) == str(cutoff_d.year) for a in selected)
                    other_year = any(isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal) and re.fullmatch(r'\d{4}', str(a.expression.this)) and str(a.expression.this) != str(cutoff_d.year) for a in selected)
                    if year_match:
                        add('scope', 'checked', f"核对静态年度快照年份（{cutoff_d.year}）等值筛选符合要求。", constraint=c)
                    elif other_year:
                        add('scope', 'conflict', f"用户要求查询截至{cutoff_str}的时点快照，SQL使用了非截止年份的等值筛选；请按正确时点筛选。", constraint=c)
                    else:
                        add('scope', 'conflict' if not selected else 'unknown', f"核对时点截止年份（{cutoff_d.year}）筛选条件。", constraint=c)
                    continue

                # Grain is not year: inspect upper bound using inclusive_upper_day
                upper_match = False
                upper_mismatch = None
                for a in selected:
                    if isinstance(a, (exp.LT, exp.LTE)):
                        strict = isinstance(a, exp.LT)
                        end = inclusive_upper_day(a.expression, strict=strict, today=today)
                        if end is not None:
                            if cutoff_d and end == cutoff_d:
                                upper_match = True
                            else:
                                upper_mismatch = end

                # Single year equality on a date-grain column is a whole-year period, not a snapshot
                year_eq = any(isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal) and re.fullmatch(r'(?:19|20)\d{2}', str(a.expression.this)) for a in selected)
                if year_eq:
                    add('scope', 'conflict', f"用户要求查询截至{cutoff_str}的时点存量，SQL使用了整年等值筛选，会遗漏或错误包含非截止日记录；请按截止日时点条件筛选。", constraint=c)
                elif upper_match:
                    add('scope', 'checked', f"核对时点截止日（{cutoff_str}）筛选边界符合要求。", constraint=c)
                elif upper_mismatch:
                    add('scope', 'conflict', f"用户要求查询截至{cutoff_str}的时点存量，SQL上界（{upper_mismatch.isoformat()}）与截止日不一致；请按截止日时点条件筛选。", constraint=c)
                else:
                    add('scope', 'conflict' if not selected else 'unknown', f"核对时点截止日（{cutoff_str}）筛选条件。", constraint=c)
                continue

            elif c.get('months') and len(c['months']) == 1:
                month_str = c['months'][0]
                y, m = int(month_str[:4]), int(month_str[5:7])
                last_day = date(y, m, calendar.monthrange(y, m)[1])
                first_day = date(y, m, 1)

                if c.get('grain') == 'year':
                    checked = any(isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal) and str(a.expression.this) == str(y) for a in selected)
                    add('scope', 'checked' if checked else 'conflict', f"核对年度字段（{y}）符合月份所在年份要求。" if checked else f"用户要求查询{month_str}，SQL缺少对应年份限定。", constraint=c)
                elif c.get('grain') == 'month':
                    checked = any(isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal) and str(a.expression.this) == month_str for a in selected)
                    add('scope', 'checked' if checked else 'conflict', f"核对业务时间月份（{month_str}）符合要求。" if checked else f"用户要求查询{month_str}，SQL缺少该月份限定。", constraint=c)
                else:
                    # Exact date half-open or closed interval
                    lower_ok = False
                    for a in selected:
                        if isinstance(a, exp.GTE) and isinstance(a.expression, exp.Literal) and str(a.expression.this) in (f"{month_str}-01", month_str):
                            lower_ok = True
                        elif isinstance(a, exp.GT) and isinstance(a.expression, exp.Literal) and str(a.expression.this) == (first_day - timedelta(days=1)).isoformat():
                            lower_ok = True
                    upper_ok = False
                    upper_mismatch = None
                    for a in selected:
                        if isinstance(a, (exp.LT, exp.LTE)):
                            end = inclusive_upper_day(a.expression, strict=isinstance(a, exp.LT), today=today)
                            if end == last_day:
                                upper_ok = True
                            elif end is not None:
                                upper_mismatch = end
                    if lower_ok and upper_ok:
                        add('scope', 'checked', f"核对业务时间月份（{month_str}）精确区间（{first_day.isoformat()} 至 {last_day.isoformat()}）符合要求。", constraint=c)
                    elif upper_mismatch:
                        add('scope', 'conflict', f"用户要求查询{month_str}，SQL上界（{upper_mismatch.isoformat()}）与月份结束日（{last_day.isoformat()}）不一致，扩大或缩小了时间区间。", constraint=c)
                    else:
                        add('scope', 'conflict' if not selected else 'unknown', f"用户要求查询{month_str}，SQL缺少该月份限定或时间区间不正确。", constraint=c)
                continue

            elif c.get('years') and len(c['years']) == 1:
                year = c['years'][0]
                if c.get('grain') == 'year':
                    checked = any(isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal) and str(a.expression.this) == year for a in selected)
                else:
                    lower = any(isinstance(a, exp.GTE) and isinstance(a.expression, exp.Literal) and str(a.expression.this) in (year + '-01-01', year + '-01') for a in selected)
                    upper_match = any(isinstance(a, (exp.LT, exp.LTE)) and inclusive_upper_day(a.expression, strict=isinstance(a, exp.LT), today=today) == date(int(year), 12, 31) for a in selected)
                    checked = lower and upper_match
                add('scope', 'checked' if checked else 'unknown' if selected else 'conflict', '核对业务时间字段及区间边界。', constraint=c)
                continue

            add('scope', 'unknown' if selected else 'conflict', '核对业务时间字段及区间边界。', constraint=c)

        # 4. Comparison period check
        elif c['kind'] == 'comparison':
            curr = str(c['current'])
            base = str(c['baseline'])
            # Year-grain data cannot prove requested month scope
            if c.get('target_grain') == 'month' and c.get('grain') == 'year':
                add('scope', 'unknown', f"配置的时间字段粒度为年（year），无法证明用户要求的月份级别比较范围（{curr} vs {base}）。", constraint=c)
                continue

            # Preserve boolean semantics: conjunction intersects, disjunction
            # unions only two proven finite sets. Mere literal presence proves nothing.
            unsupported = object()
            def periods(node):
                if node is None: return None
                if isinstance(node, (exp.Where, exp.Paren)): return periods(node.this)
                if isinstance(node, (exp.And, exp.Or)):
                    left, right = periods(node.this), periods(node.expression)
                    if left is unsupported or right is unsupported: return unsupported
                    if isinstance(node, exp.Or):
                        return left | right if isinstance(left, set) and isinstance(right, set) else unsupported
                    if left is None: return right
                    if right is None: return left
                    return left & right
                if isinstance(node, exp.EQ):
                    if resolve(node.this) == target and isinstance(node.expression, exp.Literal): return {str(node.expression.this)}
                    if resolve(node.expression) == target and isinstance(node.this, exp.Literal): return {str(node.this.this)}
                if isinstance(node, exp.In) and resolve(node.this) == target and node.expressions and all(isinstance(x, exp.Literal) for x in node.expressions):
                    return {str(x.this) for x in node.expressions}
                return unsupported if any(resolve(x) == target for x in node.find_all(exp.Column)) else None
            positive_values = periods(scoped_where)
            if not simple or not isinstance(positive_values, set):
                add('scope', 'unknown', '比较期间包含尚无法证明的条件；需结合各次查询结果核对。', constraint=c)
                continue

            has_both = (curr in positive_values and base in positive_values)
            has_curr = (curr in positive_values)
            has_base = (base in positive_values)

            if has_both:
                add('scope', 'checked', f"核对比较基期{base}与本期{curr}均已纳入查询范围。", constraint=c)
            elif has_curr and not has_base:
                # Legitimate split query for current period
                add('scope', 'unknown', f"单次查询仅覆盖比较周期的本期（{curr}），可能为分步查询；尚无法单独验证完整比较范围。", constraint=c)
            elif has_base and not has_curr:
                # Legitimate split query for baseline period
                add('scope', 'unknown', f"单次查询仅覆盖比较周期的基期（{base}），可能为分步查询；尚无法单独验证完整比较范围。", constraint=c)
            else:
                add('scope', 'conflict', f"用户要求对比{base}与{curr}，SQL缺少相关期间筛选条件。", constraint=c)

    relations = [r for r in source.get('business_dictionary', []) if r.get('kind') == 'relation']
    for join in tree.find_all(exp.Join):
        if not simple or not isinstance(join.this, exp.Table):
            add('relations', 'unknown', '派生表关联需要独立口径证明。')
            continue
        atoms_on = _and_atoms(join.args.get('on'))
        pairs = {frozenset((origin(a.this), origin(a.expression))) for a in atoms_on if isinstance(a, exp.EQ) and origin(a.this) and origin(a.expression)}
        sides = {frozenset(x[0] for x in p) for p in pairs}
        for ends in sides:
            candidates = [r for r in relations if frozenset((r['table'].split('.')[-1].lower(), r['to_table'].split('.')[-1].lower())) == ends]
            if not candidates:
                add('relations', 'unknown', '关联没有已登记的业务键。')
                continue
            matching = [r for r in candidates if {frozenset(((r['table'].split('.')[-1].lower(), f.lower()), (r['to_table'].split('.')[-1].lower(), t.lower()))) for f, t in zip(r['from_columns'], r['to_columns'])} <= pairs]
            add('relations', 'checked' if matching else 'conflict', '核对登记关联的全部复合键。')
            for r in matching:
                repeated = r['table'] if r.get('cardinality') == 'one_to_many' else r['to_table'] if r.get('cardinality') == 'many_to_one' else None
                if repeated and any(origin(x.this) and origin(x.this)[0] == repeated.split('.')[-1].lower() for x in tree.find_all(exp.Sum)):
                    add('grain', 'conflict', '已登记关联会重复聚合一侧金额；请先按实体聚合或使用存在性查询。')

    from .join_grain import check_join_grain
    for grain_finding in check_join_grain(sql, source, engine=engine, ctx=ctx):
        findings.append(grain_finding)

    for detail in scope.get('unresolved', []):
        add('scope', 'unknown', detail)
    if not findings:
        add('scope', 'unknown', '当前原问没有可确定性解析的范围约束。')
    add('semantics', 'unknown', '本检查不证明任意 SQL 的完整业务语义；数值真值需独立核对。')
    state = 'conflict' if any(f['state'] == 'conflict' for f in findings) else 'unknown' if any(f['state'] == 'unknown' for f in findings) else 'checked'
    return {'schema_version': 1, 'state': state, 'label': LABELS[state], 'checks': findings, 'request_scope': scope}


def require_valid(sql, source, scope, parameters=None, *, published_grouping=None, engine=None, ctx=None):
    validation = inspect_sql(sql, source, scope, parameters, published_grouping=published_grouping, engine=engine, ctx=ctx)
    errors = [x for x in validation['checks'] if x['state'] == 'conflict']
    if errors:
        import json
        raise ValueError('BUSINESS_SCOPE_CONFLICT: ' + json.dumps({'conflicts': errors, 'action': '保留真实用户范围，修正 SQL；不要原参数重试。', 'max_sql_repairs': 2}, ensure_ascii=False))
    return validation
