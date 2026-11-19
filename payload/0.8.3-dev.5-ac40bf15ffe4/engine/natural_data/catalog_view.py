"""Bound model-visible catalogs without cutting JSON or exposing spill files.

The immutable planning artifact always retains the complete authorized catalog.
Only the tool's view is paged. Query permission checks never use this summary.

Paging is deterministic:
- With several sources and no source_id, the page lists sources only (business
  description, counts and a detail request) and pages by source_offset.
- With source_id (or a single source), tables page by table_offset and
  metrics by metric_offset; source business context comes on the first page.
"""
import copy
import re
import json
from functools import cache
from itertools import islice

MAX_BYTES = 24000
METRIC_LISTS = ['semantic_metrics', 'metric_definitions', 'unavailable_metrics']
SOURCE_SKIP = {'tables', 'terms', 'relations', 'semantic_metrics_note', 'metric_groups', 'metric_groups_note', *METRIC_LISTS}
SOURCE_TEXT_LIMIT = 2000


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def attach_dimension_choice(view, choice, *, single_authorized_source):
    source, = view['sources']
    automatic = bool(single_authorized_source and choice['publication']['id'])
    source['dimension_business_choice'] = {**choice, 'automatic': automatic}
    if size(view) <= MAX_BYTES:
        return
    if automatic:
        compact = {**source}
        for field in ('example_matches', 'caliber_matches', 'starter_questions',
                      'configuration_guidance', 'catalog_entries'):
            compact.pop(field, None)
            if size({**view, 'sources': [compact]}) <= MAX_BYTES:
                view['sources'] = [compact]
                return
    source.pop('dimension_business_choice')


def clip(text, limit, hint='请用 table_name 或 metric_id 读取完整内容'):
    if not isinstance(text, str) or len(text) <= limit:
        return text
    # Mark the cut so a partial code list or definition is never read as complete.
    return text[:limit] + f'…（已截断，原文{len(text)}字；{hint}）'


def brief(value, fields, limit=180):
    return {k: (clip(v, limit) if isinstance(v, str) and k in ['label', 'description', 'definition', 'formula', 'reason', 'message'] else copy.deepcopy(v))
            for k, v in value.items() if k in fields}


def clip_deep(value, limit):
    if isinstance(value, str):
        return clip(value, limit)
    if isinstance(value, list):
        return [clip_deep(v, limit) for v in value]
    if isinstance(value, dict):
        result = {k: clip_deep(v, limit) for k, v in value.items()}
        sql = value.get('logical_sql')
        if isinstance(sql, str) and len(sql) > limit and value.get('read_request'):
            result.pop('logical_sql')
            result.update(sql_preview=clip(sql, min(limit, SQL_PREVIEW_CHARS), ENTRY_HINT), complete=False)
        return result
    return value


def source_context(source):
    """Business context of a source: everything except tables and metric lists."""
    # 0.6.3: the source was already focused on the question (focus_business).
    context = {k: copy.deepcopy(v) for k, v in source.items() if k not in SOURCE_SKIP}
    return clip_deep(context, SOURCE_TEXT_LIMIT)


# 0.6.3: business context is retrieved by question instead of sent whole.
# Terms, examples, skills and layers were all included on every catalog call
# (each clipped to 2000 characters, no count limit), so a configured project paid
# for its whole glossary on every question.
TERM_LIMIT, EXAMPLE_LIMIT, SKILL_LIMIT = 6, 3, 3
TERM_CHARS, EXAMPLE_CHARS, SKILL_CHARS = 160, 120, 160
FIRST_SQL_CHARS, SQL_PREVIEW_CHARS = 1500, 180
ENTRY_HINT = '请使用此条目的 read_request 读取全文'
CONTEXT_BUDGET = 3000  # characters for all retrieved business entries of a source
LEGACY_CODE = re.compile(r'^([A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)[\s:：]+')
_BUDGET_COMPARISON = re.compile(r'^(?:本年度?|当年)?(?P<subject>.+?)(?:项目)?预算(?:金额|总额|数额)?[_ -]?(?:同比|较上年|较去年)$')
_YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})\s*年')
_ANNUAL_FALLBACK = re.compile(r'优先|若(?:无|没有)|如果[^，。；\n]{0,18}(?:无|没有|缺)|没有数据|查不到')
_NON_ANNUAL_BUDGET = re.compile(r'同比|环比|较(?:上|去|前)年|对比|截至|截止|上半年|下半年|季度|\d+\s*月|逐月|每月|按月')


def pick(items, question, text_of, limit):
    from .retrieval import rank, named
    ranked = rank(items, question, lambda item: [(text_of(item),1)], named)
    return [(score,item) for priority,score,item in ranked[:limit] if not question or priority or score]


def entry_references(source):
    from .catalog_index import entries, fingerprint, read_request

    version = fingerprint(source)
    refs = {(e['kind'], json.dumps(e['content'], sort_keys=True, ensure_ascii=False)): e['entry_ref'] for e in entries(source)}

    def reference(kind, value):
        ref = refs.get((kind, json.dumps(value, sort_keys=True, ensure_ascii=False)))
        return {'entry_ref': ref, 'read_request': read_request(source['id'], ref, version)} if ref else {}

    return version, reference


def entry_code(item, title_key):
    code = item.get('metric_code') or item.get('code')
    if isinstance(code, str) and code.strip():
        return code.strip().casefold()
    # Previously published pack examples store their codes only in their titles.
    # Resolve that format only for pack entries and never infer from similar names.
    if str(item.get('origin') or '').startswith('pack:'):
        match = LEGACY_CODE.match(item.get(title_key) or '')
        if match:
            return match[1].casefold()


def pick_examples(source, question, term_hits):
    examples = [e for e in source.get('examples') or [] if e.get('source_id') in (None, '', source['id'])]
    ranked = [e for _, e in pick(examples, question, lambda e: e.get('question', ''), len(examples))]
    if not question:
        return ranked[:EXAMPLE_LIMIT]
    by_code = {}
    for example in examples:
        code = entry_code(example, 'question')
        if code and example.get('source_id') == source['id']:
            by_code.setdefault(code, []).append(example)
    # Prefer an exact code explicitly requested by the caller. Otherwise link
    # the two best matching term definitions to their own published examples.
    explicit = [e for code, group in by_code.items()
                if re.search(r'(?<![a-z0-9_-])' + re.escape(code) + r'(?![a-z0-9_-])', question, re.I)
                for e in group]
    paired = []
    for _, term in term_hits[:2]:
        code = entry_code(term, 'name')
        group = by_code.get(code, [])
        if term.get('source_id') == source['id'] and len(group) == 1 and group[0].get('origin') == term.get('origin'):
            paired.append(group[0])
    prioritized = []
    for example in [*explicit, *paired, *ranked]:
        if example not in prioritized:
            prioritized.append(example)
        if len(prioritized) >= EXAMPLE_LIMIT:
            break
    return prioritized


def example_brief(example, reference, sql_limit):
    result = {**reference, 'question': clip(example.get('question', ''), EXAMPLE_CHARS, ENTRY_HINT)}
    if example.get('origin'):
        result['origin'] = example['origin']
    if code := entry_code(example, 'question'):
        result['metric_code'] = code
    sql = example.get('logical_sql')
    if sql:
        result['complete'] = sql_limit is not None and len(sql) <= sql_limit
        if result['complete']:
            result['logical_sql'] = sql
        elif sql_limit is not None:
            result['sql_preview'] = clip(sql, SQL_PREVIEW_CHARS, ENTRY_HINT)
    return result


def term_brief(term, score, reference, question):
    result = {**reference, 'name': term.get('name'),
              'definition': clip(term.get('definition', ''), TERM_CHARS, ENTRY_HINT)}
    if question:
        result['relevance'] = round(score, 2)
    if code := entry_code(term, 'name'):
        result['metric_code'] = code
    return result


def matching_sql_examples(source, question):
    """Small, source-bound alternative for a view with no structured metrics."""
    terms = [t for t in source.get('terms', []) if t.get('source_id') in (None, '', source['id'])]
    hits = pick(terms, question, lambda t: str(t.get('name', '')) + ' ' + str(t.get('definition', '')), TERM_LIMIT)
    examples = pick_examples(source, question, hits)
    if not examples:
        return {}
    _, reference = entry_references(source)

    # The two best examples can describe different parts of the same request.
    # Keep both runnable when short; remaining SQL stays behind its read request.
    matches = [example_brief(e, reference('example', e), FIRST_SQL_CHARS if i < 2 else SQL_PREVIEW_CHARS)
               for i, e in enumerate(examples)]
    codes = {entry_code(e, 'question') for e in examples} - {None}
    definitions = [term_brief(t, score, reference('term', t), question)
                   for score, t in hits if entry_code(t, 'name') in codes]
    return {'example_matches': matches, 'caliber_matches': definitions,
            'business_context_note': '当前来源未发布结构化指标。以下是与本次检索匹配的已发布 SQL 示例和口径线索。'
            '核对用户要求的范围与期间后使用 data_sql_query；示例只显示预览时按 read_request 读取全文。'}


def _budget_base(example, term, source, year):
    """A separately queryable budget denominator from a published year comparison."""
    from sqlglot import exp, parse_one
    from sqlglot.errors import SqlglotError
    from .display_units import direct_published_sums, _zero_default
    from .monthly_sql_scope import _source_table
    from .published_scope import _conjuncts, _membership
    from .sql_answer_contract import _BUDGET
    from .vendor.dialect import sqlglot_name

    dialect = source['dialect']
    try:
        tree = parse_one(example['logical_sql'], read=sqlglot_name(dialect))
    except SqlglotError:
        return None
    if not isinstance(tree, exp.Select) or len(tree.expressions) != 1:
        return None
    ratios = [ratio for ratio in tree.expressions[0].find_all(exp.Div)
              if isinstance(ratio.expression, exp.Nullif) and ratio.expression.expression == exp.Literal.number(0)]
    if len(ratios) != 1:
        return None
    ratio = ratios[0]
    base_column = ratio.expression.this
    if (not isinstance(base_column, exp.Column) or not base_column.table
            or not any(sub.expression == base_column for sub in ratio.this.find_all(exp.Sub))):
        return None
    candidates = [node.this for node in tree.find_all(exp.Subquery) if node.alias == base_column.table
                  and node.find_ancestor(exp.Select) is tree]
    if len(candidates) != 1 or not isinstance(base := candidates[0], exp.Select) or len(base.expressions) != 1:
        return None
    item = base.expressions[0]
    if item.alias_or_name != base_column.name:
        return None
    agg = item.unalias()
    if _zero_default(agg):
        agg = agg.this
    if not isinstance(agg, exp.Sum) or not isinstance(agg.this, exp.Column) or agg.this.is_star:
        return None
    field = agg.this.name
    # A fixed year literal alone could be a code or an amount. Require the
    # same published definition to associate this year with the physical field.
    definition = term.get('definition') or ''
    if not re.search(r'(?<!\d)' + re.escape(year) + r'\s*年[^。；;\n]{0,80}(?<!\w)'
                     + re.escape(field) + r'(?!\w)', definition, re.I):
        return None
    if not (measure := direct_published_sums(base, source, dialect).get(item.alias_or_name)) or not _BUDGET.fullmatch(measure['label']):
        return None
    refs = list(base.find_all(exp.Table))
    if (len(refs) != 1 or not (table := _source_table(refs[0], source)) or not base.args.get('where')
            or any(base.args.get(key) is not None for key in ('group', 'having', 'limit', 'offset', 'distinct'))):
        return None
    columns = {column['name'] for column in table['columns']}

    def physical_column(node):
        if (isinstance(node, exp.Column) and node.name in columns and not (node.db or node.catalog)
                and (not node.table or node.table == refs[0].alias_or_name)):
            return node.name

    filters = {}
    for predicate in _conjuncts(base.args['where'].this):
        match = _membership(predicate, physical_column)
        if not match or match[0] != 'set' or match[1] in filters:
            return None
        filters[match[1]] = sorted(match[2])
    if sum(values == [year] for values in filters.values()) != 1:
        return None
    label, unit = measure['label'], measure['unit']
    query = base.copy()
    relation = query.args['from'].this if query.args.get('from') else query.args['from_'].this
    qualified = exp.to_table('.'.join('"'+part.replace('"', '""')+'"' for part in table['name'].split('.')),
                             dialect=sqlglot_name(dialect))
    for key in ('this', 'db', 'catalog'):
        relation.set(key, qualified.args.get(key))
    query.set('expressions', [exp.alias_(agg.copy(), f'{label}（{unit}）', quoted=True)])
    sql = query.sql(dialect=sqlglot_name(dialect))
    if len(sql) > FIRST_SQL_CHARS:
        return None
    return {'sql': sql, 'table': table['name'], 'column': field, 'filters': filters, 'unit': unit, 'label': label}


def comparison_budget_baseline(source, year, matches_subject, *, published_origin=None):
    """Resolve one independently labelled, source-bound published budget baseline."""
    from .retrieval import normalize

    matches = {}
    for term in source.get('terms', []):
        if term.get('source_id') != source['id'] or not term.get('origin') or not (code := entry_code(term, 'name')):
            continue
        if term.get('status') in ('draft', 'review', 'pending_review') or published_origin and term['origin'] != published_origin:
            continue
        name = re.sub(r'^' + re.escape(code) + r'\s*[:：]?\s*', '', normalize(term.get('name')), count=1)
        if not (scope := _BUDGET_COMPARISON.fullmatch(name)) or len(scope['subject']) < 2 or not matches_subject(scope['subject']):
            continue
        for example in source.get('examples', []):
            if (example.get('source_id') == source['id'] and example.get('origin') == term['origin']
                    and example.get('status') not in ('draft', 'review', 'pending_review')
                    and not example.get('parameters') and entry_code(example, 'question') == code and isinstance(example.get('logical_sql'), str)
                    and (candidate := _budget_base(example, term, source, year))):
                matches.setdefault(candidate['sql'], (scope['subject'], example, candidate))
    if len(matches) != 1:
        return None
    return next(iter(matches.values()))


def budget_reference(source, question, reference=None):
    """Find a source-bound annual budget reference without treating it as fiscal spending."""
    from .retrieval import normalize
    from .sql_answer_contract import _FISCAL_REQUEST

    from .fiscal_history import explicit_annual_amount_years

    text = normalize(question)
    years = tuple(dict.fromkeys(_YEAR.findall(text)))
    actual_fiscal = bool(_FISCAL_REQUEST.search(text) and '财政预算' not in text)
    if not years or not (actual_fiscal or '预算' in text):
        return None
    multiple = len(years) > 1
    if multiple:
        publication = source.get('business_project') or {}
        if not publication.get('id') or not publication.get('version') or not explicit_annual_amount_years(text):
            return None
    elif _NON_ANNUAL_BUDGET.search(text):
        return None
    requested_measure = 'actual_fiscal' if actual_fiscal else 'budget'
    if reference is None:
        _, reference = entry_references(source)
    matches = []
    for year in years:
        baseline = comparison_budget_baseline(source, year, lambda subject: subject in text)
        if baseline:
            subject, example, candidate = baseline
            matches.append({**candidate, **reference('example', example), 'source_id': source['id'], 'year': year,
                            'title': f'{year}年{subject}项目预算', 'requested_measure': requested_measure,
                            'kind': 'budget_reference', 'schema_version': 1, 'basis': 'published_comparison_baseline',
                            'instruction': ('原已发布同比基期可单独查同期间的项目预算，保留 SQL 中已登记的业务范围和原问条件；'
                                            '无观测时金额留空。实际财政投入或拨付需要资金来源及实际发生证据，'
                                            '缺少时说明无法核实，项目预算仅作同范围参考。')})
    if not matches:
        return None
    primary = matches[0]
    if not multiple:
        return primary
    missing_recipes = [year for year in years if all(match['year'] != year for match in matches)]
    mode = 'fallback' if _ANNUAL_FALLBACK.search(text) else 'comparison'
    order = '、'.join(years)
    operation = (f'按用户顺序 {order} 年核实所问金额，首选年份同口径确无观测后才回退。' if mode == 'fallback' else
                 f'核实 {order} 年同一范围及同一金额角色。缺少可比年时保留已核实事实，不计算变化幅度。')
    return {**primary, 'role': 'budget', 'query_mode': mode, 'requested_years': list(years),
            'unresolved_years': missing_recipes,
            **({'fallback_references': matches[1:]} if len(matches) > 1 else {}),
            'instruction': (operation + f'可单独查询 {primary["year"]} 年已发布预算基期，保留其全部筛选与用户额外限制。'
                            '预算数值按项目预算单独给出。实际投入需要同范围的发生记录。未匹配已发布基期的年份仍可按授权来源继续核实，'
                            '不能据此判定缺数据，也不能把不同范围或不同金额角色相加。')}


def scope_matches(source, question, reference=None):
    from .row_count_aggregation import named_scope

    selected = named_scope(source, question)
    if not selected:
        return []
    term, alias = selected
    spec = term['config']
    if reference is None:
        _, reference = entry_references(source)
    row = {**reference('term', term), 'name': alias, 'table': spec['table'], 'time_column': spec['time_column'],
           'unit': spec['unit'], 'basis': spec['basis'], 'filters': copy.deepcopy(spec['filters']), 'complete': True}
    if size(row) > SOURCE_TEXT_LIMIT:
        row.pop('filters')
        row['complete'] = False
    row['instruction'] = ('该业务范围由原问明确点名。按 filters 中每个字段的登记值筛选，值之间取其一，字段之间同时满足。'
                          '保留用户要求的期间和其他条件；相近示例的统计范围可能更广。' if row['complete'] else
                          '该业务范围由原问明确点名。请用 read_request 读取完整 filters 后再查询。')
    return [row]


_SNAPSHOT_INTENT = re.compile(r'有效|在册|在用|在岗|在籍|存量|余额|保有|历史(?:快照|时点)|期末|年末|月末')
_RELATED_STATE = re.compile(r'^(?:本|当|上|历)?(?:月末|期末|年末)|(?:的)?(?:总数量|数量|总数|总量|件数|个数|数|量)$')
_SAMPLE_LOCATION = re.compile(r'示例\s*(?:地区\s*)?(?:为|是|使用|以)\s*([一-鿿]{2,12}?(?:省|市|自治区|区|县))(?=[\s，,。；;）)]|$)')
_SNAPSHOT_DECLARATION = re.compile(r'当前(?:有效)?快照')


def snapshot_boundaries(source, question, temporal=None, table_name=None):
    """Report a declared table boundary, without claiming source-wide absence."""
    if not (publication := source.get('business_project')) or not publication.get('version') or not question:
        return []
    if not (table_name or _SNAPSHOT_INTENT.search(question) or re.search(r'全省|全市|全区', question)):
        return []
    if not any(_SNAPSHOT_DECLARATION.search(term.get('definition') or '') for term in source.get('terms', [])) and not any(
            mapping.get('kind') in ('table', 'time') and _SNAPSHOT_DECLARATION.search(mapping.get('description') or '')
            for mapping in source.get('business_dictionary', [])):
        return []
    from .snapshot_scope import _historical_request, _snapshot_tables
    from .vendor.dialect import sqlglot_name
    host = {'user_text':question, 'client_time_zone':(temporal or {}).get('time_zone', 'UTC')}
    if not _historical_request(host):
        return []
    from .metric_subject import projected_count_subjects, requested_subjects
    requested = requested_subjects(host)
    declared, examples = _snapshot_tables(source, sqlglot_name(source.get('dialect')))
    reference = None
    rows = []
    for table in sorted(declared):
        term, _ = examples.get(table, ({}, None))
        label = term.get('name') or next((t.get('label') or t['name'] for t in source['tables'] if t['name']==table), table)
        subjects = projected_count_subjects(label)
        domain = _RELATED_STATE.sub('', LEGACY_CODE.sub('', label))
        related = len(domain) >= 4 and (domain in question or domain[:-2] in question and _SNAPSHOT_INTENT.search(question))
        # Match the actual request to the declaration's object, or an explicit
        # historical stock/state request. The search keyword is not authority.
        if table_name != table and (table_name or not (related or subjects & requested)):
            continue
        boundary = {'table':table, 'state':'current_snapshot_only', 'applies_to':'listed_table',
                    'publication':{key:publication[key] for key in ('id','version') if key in publication},
                    'published_subjects':sorted(subjects), 'requested_subjects':sorted(requested),
                    'instruction':('该授权表只提供当前有效快照，不能用于原问的历史时点。应使用已核实的同口径历史来源；'
                                   '没有本轮可核实证据时，说明原问暂无法核实。采样、改统计对象或缩小地区都不能补足历史。')}
        if term:
            if reference is None:
                _, reference = entry_references(source)
            boundary.update(reference('term', term), published_measure=clip(label, 180))
            if match := _SAMPLE_LOCATION.search(term.get('definition') or ''):
                boundary['example_location'] = match[1]
                boundary['instruction'] += '示例地区只是示例条件，不能确认用户的省域，也不能证明全省覆盖。'
        if related and subjects and requested and subjects.isdisjoint(requested) and len(requested)==1:
            boundary['subject_mismatch'] = True
            boundary['instruction'] += '保留用户要求的统计对象，不能用该表登记口径的对象替代。'
        rows.append(boundary)
        if len(rows)==2:
            break
    return rows


def focus_business(source, question, select_metrics_by_question=True, table_name=None, scope_question=None):
    """A copy of the source whose business context holds only entries relevant to
    the question, within CONTEXT_BUDGET; counts tell the model what was left out."""
    from .catalog_index import summaries, relation_paths
    original = source
    source = copy.deepcopy(original)
    version, reference = entry_references(source)
    if matches := scope_matches(source, scope_question, reference):
        source['scope_matches'] = matches
    if budget := budget_reference(source, scope_question, reference):
        source['budget_reference'] = budget
    graph = relation_paths(source, question, table_name)
    source['relationship_context'] = {k: v for k, v in graph.items() if k != 'relations'}
    if select_metrics_by_question and isinstance(source.get('semantic_metrics'), list):
        enriched = enrich_metrics(source)
        layered = metric_layers(enriched, question)
        source['semantic_metrics'] = layered.pop('semantic_metrics')
        source.update(layered)
    # 0.7.0: project analysis skills are listed by analysis_list; only plain method notes stay here.
    source['business_skills'] = [x for x in source.get('business_skills') or [] if (x.get('config') or {}).get('kind') != 'analysis-skill']
    terms = source.get('terms') or []
    metric_terms = [t for t in terms if isinstance(t.get('config'), dict) and t['config'].get('kind') == 'semantic-metric']
    plain = [t for t in terms if t not in metric_terms]
    term_hits = pick(plain, question, lambda t: str(t.get('name', '')) + ' ' + str(t.get('definition', '')), TERM_LIMIT)
    matches = [term_brief(t, sc, reference('term', t), question) for sc, t in term_hits]
    examples = [example_brief(e, reference('example', e), FIRST_SQL_CHARS if question and i == 0 else SQL_PREVIEW_CHARS)
                for i, e in enumerate(pick_examples(source, question, term_hits))]
    skills = [{'name': x.get('name'), 'description': clip(x.get('description', ''), SKILL_CHARS)}
              for _, x in pick(source.get('business_skills') or [], question, lambda x: str(x.get('name', '')) + ' ' + str(x.get('description', '')), SKILL_LIMIT)]
    layers = [{'name': x.get('name'), 'description': clip(x.get('description', ''), SKILL_CHARS)}
              for _, x in pick(source.get('business_layers') or [], question, lambda x: str(x.get('name', '')) + ' ' + str(x.get('description', '')), SKILL_LIMIT)]
    groups = {'caliber_matches': matches, 'example_matches': examples, 'business_skills': skills, 'business_layers': layers}
    # Keep the total within budget: drop the least relevant entry of the largest group.
    while sum(size(g) for g in groups.values()) > CONTEXT_BUDGET * 3 and any(groups.values()):
        largest = max(groups, key=lambda k: size(groups[k]))
        groups[largest].pop()
    shown_refs = {item['entry_ref'] for key in ('caliber_matches', 'example_matches')
                  for item in groups[key] if 'entry_ref' in item}
    source['catalog_entries'] = summaries(original, question, 4, version=version, exclude=shown_refs)
    for entry in source['catalog_entries']:
        entry.pop('config_fingerprint', None)
    counts = {'terms': len(plain), 'examples': len(source.get('examples') or []),
              'business_skills': len(source.get('business_skills') or []), 'business_layers': len(source.get('business_layers') or [])}
    relations = graph['relations']
    for key in ('terms', 'examples', 'business_skills', 'business_layers', 'business_dictionary'):
        source.pop(key, None)
    if relations:
        # Configured joins, compact; the table detail page lists the ones it takes part in.
        source['relations'] = [{'name': r.get('name'), 'tables': [r['table'], r['to_table']],
                                'join': r['table'] + '.(' + ','.join(r['from_columns']) + ') = ' + r['to_table'] + '.(' + ','.join(r['to_columns']) + ')',
                                'cardinality': r.get('cardinality')} for r in relations[:20]]
    if metric_terms:
        # 0.7.0: a short index for choosing a source; the metric list itself carries the details.
        named = [t for _, t in pick(metric_terms, question, lambda t: str(t.get('name', '')) + ' ' + str(t.get('definition', '')), METRIC_NAME_LIMIT)] or metric_terms[:METRIC_NAME_LIMIT]
        source['metric_names'] = [{'id': t['config'].get('id'), 'name': t.get('name')} for t in named]
        if len(metric_terms) > len(named):
            source['metric_names_note'] = '共%d个业务指标，这里列出与问题最相关的%d个' % (len(metric_terms), len(named))
    for key, value in groups.items():
        if value:
            source[key] = value
    shown = {'terms': len(groups['caliber_matches']), 'examples': len(groups['example_matches']),
             'business_skills': len(groups['business_skills']), 'business_layers': len(groups['business_layers'])}
    if any(counts[k] > shown[k] for k in counts):
        source['business_context_note'] = ('业务配置共有术语 %d 条、示例 %d 条、分析技能 %d 条、业务分层 %d 条，这里只列出与问题最相关的部分；'
            '需要其他内容时，用 data_catalog(source_id=…, question=要找的业务名词) 再检索。字段的同义词、枚举对照和时间口径在 table_name 详情里。'
            % (counts['terms'], counts['examples'], counts['business_skills'], counts['business_layers']))
    return source


def metric_rule(metric):
    """additive and time_mode as one short marker."""
    if any(isinstance(o,dict) and o.get('latest') for o in metric.get('operands') or []):
        return '按实体取期末最新报送，不可跨期相加'
    rule = '可相加' if metric.get('additive') else '不可直接相加'
    return ('不分期间，' + rule) if metric.get('time_mode') == 'all' else rule


def metric_brief(metric):
    """One metric on a catalog page. 0.7.0: fields that only repeat a default are left out
    (kind semantic-metric, a formula that is just the single operand, empty lists), additive and
    time_mode become one marker (rule) and coverage is one short string; no information is
    dropped. Applying it twice gives the same entry."""
    item = brief(metric, {'id', 'name', 'label', 'version', 'unit', 'kind', 'state', 'reason', 'error', 'action',
                          'definition', 'formula', 'also_in', 'group_required', 'aliases', 'rule'}, limit=300)
    if 'additive' in metric or 'time_mode' in metric:
        item['rule'] = metric_rule(metric)
    if metric.get('reconciliation'):
        item['reconciliation'] = True
    for key in ('group_required', 'also_in', 'aliases'):
        if not item.get(key):
            item.pop(key, None)
    if item.get('kind') == 'semantic-metric':
        item.pop('kind')
    operands = metric.get('operands') if isinstance(metric.get('operands'), list) else []
    if len(operands) == 1 and isinstance(operands[0], dict) and item.get('formula') == operands[0].get('id'):
        item.pop('formula')
    if item.get('label') == item.get('name'):
        item.pop('label', None)
    dims = [d.get('id') if isinstance(d, dict) else d for d in metric.get('dimensions') or [] if isinstance(d, (dict, str))]
    if dims:
        item['dimensions'] = dims
    coverage = metric.get('coverage')
    if isinstance(coverage, list) and coverage:
        periods = sorted(map(str, coverage))
        years = [p for p in periods if re.fullmatch(r'\d{4}', p)]
        months = [p for p in periods if re.fullmatch(r'\d{4}-\d{2}', p)]
        if years and months and len(years) + len(months) == len(periods):
            # Mixed annual/monthly entries are not a longer monthly interval.
            item['coverage'] = '年度：%s~%s，共%d年期；月度：%s~%s，共%d月期' % (years[0], years[-1], len(years), months[0], months[-1], len(months))
        else:
            item['coverage'] = periods[0] if len(periods) == 1 else '%s~%s，共%d期' % (periods[0], periods[-1], len(periods))
    elif isinstance(coverage, str) and coverage:
        item['coverage'] = coverage
    return item


# 0.7.0: metrics in three layers, sized for hundreds of metrics. The metric part of a catalog
# page has a fixed ceiling whatever the number of metrics:
#   metric_groups      every group (≤ GROUP_LIMIT, most relevant first) with its size;
#   semantic_metrics   the FULL_METRICS most relevant in full (more, up to FORCED_LIMIT, when the
#                      question names them), then up to NAMED_METRICS more by id and name;
#   the rest           not listed; data_catalog(view='metrics', question=…, metric_group=…) finds them.
FULL_METRICS, FORCED_LIMIT, NAMED_METRICS, GROUP_LIMIT, GROUP_SHORT = 6, 10, 8, 20, 12
METRIC_NAME_LIMIT = 12
SEARCH_LIMIT = 10
# Ceiling for the metric part including its note, in estimated tokens. The estimate rarely
# undercounts, so real pages stay at or below this; typical pages come out around 1,300-1,500.
METRIC_PART_TOKENS = 1800
NOTE_RESERVE = 200


def estimate_tokens(value):
    """Rough Qwen-family token count of a JSON value: CJK characters about 0.95 each, other
    characters about one per 2.5 (fitted to measured catalog pages so it rarely undercounts; it can overcount by up to about a quarter)."""
    text = json.dumps(value, ensure_ascii=False)
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    return int(cjk * 0.95 + (len(text) - cjk) / 2.5)


def enrich_metrics(source):
    """Name, aliases, code and group from the metric's business term row (matched by config.id).
    Without a group, the business name of the metric's first table is used."""
    rows = {(t.get('config') or {}).get('id'): t for t in source.get('terms') or [] if (t.get('config') or {}).get('kind') == 'semantic-metric'}
    labels = {t.get('name'): t.get('label') or t.get('name') for t in source.get('tables') or []}
    out = []
    for m in source.get('semantic_metrics') or []:
        row = rows.get(m.get('id'), {})
        table = next(((o.get('latest') or {}).get('entity_table') or o.get('table') for o in m.get('operands') or [] if isinstance(o, dict) and o.get('table')), None)
        aliases = [*(row.get('aliases') or []), *(row.get('synonyms') or [])]
        out.append({**m, 'name': m.get('name') or row.get('name') or m.get('id'), **({'aliases': aliases} if aliases else {}),
                    **({'code': row['code']} if row.get('code') else {}), **({'group_summary': row['group_summary']} if row.get('group_summary') else {}),
                    'group': row.get('group') or (str(labels.get(table) or table).split('：')[0] if table else '其他')})
    return out


def asked_collision(question, m):
    """A same-name concept the question asks about: its metrics stay in full so the
    choose-the-database question can be asked with real options."""
    if not m.get('also_in'):
        return False
    from .source_choice import CONCEPTS
    name = str(m.get('name') or m.get('label') or '')
    return any(q.search(question) and n.search(name) for _, q, n in CONCEPTS)


def name_entry(m):
    return {'id': m.get('id'), 'name': m.get('name') or m.get('label') or m.get('id')}


def group_list(metrics, question, shown):
    groups = {}
    for m in metrics:
        g = groups.setdefault(m.get('group') or '其他', {'name': m.get('group') or '其他', 'count': 0, 'shown': 0, 'members': [], 'summary': m.get('group_summary')})
        g['count'] += 1
        g['shown'] += m.get('id') in shown
        g['members'].append(str(m.get('name') or m.get('id')))
    from .retrieval import rank, named
    ordered = [item for _,_,item in rank(list(groups.values()), question,
        lambda g: [(g['name']+' '+' '.join(g['members']),1)], named)]
    limit = GROUP_LIMIT if len(ordered) <= GROUP_LIMIT else GROUP_SHORT
    out = []
    for g in ordered[:limit]:
        item = {'name': g['name'], 'count': g['count']}
        if g['count'] - g['shown']:
            item['not_shown'] = g['count'] - g['shown']
            item['summary'] = g['summary'] or '含' + '、'.join(strip_unit_name(x) for x in g['members'][:2]) + ('等' if g['count'] > 2 else '')
        out.append(item)
    return out, len(ordered) - len(out)


def strip_unit_name(name):
    return re.sub(r'[（(][^）)]*[）)]', '', str(name)).strip()


def metric_layers(metrics, question):
    """semantic_metrics (full, then names), metric_groups and notes for one source."""
    from .metric_search import rank_metrics, exact_hits
    import os
    # Test switches for end-to-end comparisons (not for production use):
    # NW_CATALOG_ALL_METRICS=1 lists every metric in full (the "list everything" baseline);
    # NW_CATALOG_FULL_METRICS=n shrinks the full layer to exercise names, groups and search.
    if os.environ.get('NW_CATALOG_ALL_METRICS') == '1':
        # The note keeps the paged view from cutting this list back to one page.
        return {'semantic_metrics': [metric_brief(m) for m in metrics], 'semantic_metrics_note': '全部%d个指标完整列出。' % len(metrics)}
    full_limit = int(os.environ.get('NW_CATALOG_FULL_METRICS') or FULL_METRICS)
    if len(metrics) <= full_limit:
        return {'semantic_metrics': [metric_brief(m) for m in metrics]}
    question = question or ''
    ranked = rank_metrics(question, metrics)
    forced = [m for m in (exact_hits(question, metrics) if question else []) + [m for m in metrics if question and asked_collision(question, m)]]
    forced = list({id(m): m for m in forced}.values())[:FORCED_LIMIT]
    full = forced + [m for m in ranked if m not in forced][:max(0, full_limit - len(forced))]
    named = [m for m in ranked if m not in full][:NAMED_METRICS]
    entries = [metric_brief(m) for m in full] + [name_entry(m) for m in named]
    groups, more_groups = group_list(metrics, question, {m.get('id') for m in full})
    layered = {'semantic_metrics': entries, 'metric_groups': groups}
    # Hard ceiling for the metric part. Full definitions always stay. What goes first: the
    # summaries of less relevant groups, then groups beyond GROUP_SHORT, then the named
    # metrics, then the least relevant groups.
    over = lambda: estimate_tokens(layered) > METRIC_PART_TOKENS - NOTE_RESERVE
    for g in reversed(layered['metric_groups'][5:]):
        if not over():
            break
        g.pop('summary', None)
    while over() and len(layered['metric_groups']) > GROUP_SHORT:
        layered['metric_groups'].pop()
        more_groups += 1
    while over() and len(layered['semantic_metrics']) > len(full):
        layered['semantic_metrics'].pop()
    while over() and len(layered['metric_groups']) > 1:
        layered['metric_groups'].pop()
        more_groups += 1
    hidden = len(metrics) - len(layered['semantic_metrics'])
    layered['semantic_metrics_note'] = (
        '共%d个指标、%d个分组：最相关的%d个给出完整口径，另列出%d个的名称，其余%d个没有列出。'
        '找其他指标用 data_catalog(source_id=…, view="metrics", question=指标说法) 或加 metric_group=分组名，每次最多返回%d个完整口径。'
        '只列名称或没列出的指标也可以直接用 data_metric_query 查询（版本可省略），参数不对时报错会附上完整口径。'
        % (len(metrics), len(groups) + more_groups, len(full), len(layered['semantic_metrics']) - len(full), hidden, SEARCH_LIMIT))
    if more_groups:
        layered['metric_groups_note'] = '另有%d个分组没有列出' % more_groups
    return layered


def search_metrics(metrics, question, group=None):
    """view="metrics": up to SEARCH_LIMIT metrics in full, filtered by group and ranked by the question."""
    from .metric_search import rank_metrics
    pool = [m for m in metrics if not group or group in str(m.get('group') or '')]
    ranked = rank_metrics(question or '', pool)
    return [metric_brief(m) for m in ranked[:SEARCH_LIMIT]], len(pool)


ENUM_LIMIT, ALIAS_LIMIT = 30, 8


def column_detail(column):
    """0.6.3: a column on the table detail page keeps its business configuration
    (synonyms, description, value labels, time meaning), within fixed limits."""
    item = brief(column, {'name', 'type', 'label'}, limit=600)
    if column.get('aliases'):
        item['aliases'] = [str(a) for a in column['aliases'][:ALIAS_LIMIT]]
    if column.get('business_description'):
        item['business_description'] = clip(column['business_description'], 160)
    values = column.get('business_values')
    if isinstance(values, list) and values:
        item['business_values'] = [{'value': v.get('value'), 'label': clip(str(v.get('label', '')), 40)} for v in values[:ENUM_LIMIT] if isinstance(v, dict)]
        if len(values) > ENUM_LIMIT:
            item['business_values_note'] = '共%d项，仅列前%d项' % (len(values), ENUM_LIMIT)
    if isinstance(column.get('time_semantics'), dict):
        t = column['time_semantics']
        item['time_semantics'] = {'name': t.get('name'), 'grain': t.get('grain'), **({'description': clip(t['description'], 120)} if t.get('description') else {})}
    return item


def version_key(metric):
    try:
        return tuple(int(x) for x in str(metric.get('version', '')).split('.'))
    except ValueError:
        return ()


LIMITS = {'source_limit': 32, 'table_limit': 200, 'column_limit': 80, 'metric_limit': 16}
# 0.6.2: a table list has names only (~60 bytes a table), so list them all when
# they fit the 24 KB page; the size guard below still shortens oversized pages.
# With 40 a page, the certificate table of a 141-table source was on page 4 and
# the model gave up before reaching it.
TABLE_PAGE = 200
PREVIEW_COLUMNS = 12
import re as _re
# Keys and audit columns say nothing about what a table holds.
GENERIC_COLUMN = _re.compile(r'^(?:r?id|uuid|guid|pk|projectno|create_?time|update_?time|create_?by|update_?by|del_?flag|is_?deleted|sort|version|tenant_?id)$', _re.I)

# 0.6.2: put the tables that match the question on the first page.


# Staging, backup and dated copies of a business table (ods_x, x_bak_20261029, x_20260302,
# x_correct, x_error, x_tmp). The model picked such copies and counted partial data.
COPY_PATTERNS = [(_re.compile(r'^ods_(.+)$'), r'\1'), (_re.compile(r'^(.+?)_(?:bak|backup|copy|old|tmp|temp|his|history)(?:_\d+)?$'), r'\1'),
                 (_re.compile(r'^(.+?)_\d{6,8}(?:_after|_before)?$'), r'\1'), (_re.compile(r'^(.+?)_(?:correct|error|err|fix)$'), r'\1')]


def copy_of(name, names):
    """The base table this name looks like a copy of, when that base table exists."""
    for pattern, repl in COPY_PATTERNS:
        if pattern.match(name or ''):
            base = pattern.sub(repl, name)
            if base != name and base in names:
                return base
    return None


def rank_tables(tables, question):
    """The table view uses the same scorer as full entries, before paging."""
    from .retrieval import rank, named
    def fields(table):
        return [(json.dumps(table,ensure_ascii=False),1)]
    def exact(table, query):
        if re.search(r'(?<![\w])'+re.escape(str(table.get('name') or ''))+r'(?![\w])',query,re.I):return 2
        return int(named(table,query) or any(named(c,query) for c in table.get('columns',[])))
    ordered = [item for _,_,item in rank(tables,question,fields,exact)]
    return ordered, ordered != list(tables)


def metric_page(value, args, question, scope_question=None):
    """A bounded metric view for one source, with SQL examples where applicable."""
    sources = value.get('sources', [])
    if args.source_id:
        sources = [s for s in sources if s.get('id') == args.source_id]
        if not sources:
            raise PermissionError('CATALOG_ENTRY_NOT_FOUND: 当前数据源未授权。')
    if len(sources) != 1:
        raise ValueError('CATALOG_SELECTION_REQUIRED: view="metrics" 需要指定 source_id。')
    src = sources[0]
    row = brief(src, {'id', 'name', 'business_project', 'query_modes'})
    if matches := scope_matches(src, scope_question):
        row['scope_matches'] = matches
    if not getattr(args, 'metric_id', None) and (budget := budget_reference(src, scope_question)):
        row['budget_reference'] = budget
    if type(src.get('synthetic')) is bool:
        row['source_context'] = {'synthetic': src['synthetic']}
    from .business_ambiguity import matching_rules
    rules = list(matching_rules(src, question or ''))
    if rules:
        row['clarifications'] = [{'term': t['name'], **r} for t, r in rules[:6]]

    if getattr(args, 'metric_id', None):
        target_id = args.metric_id
        from .catalog_index import entries, fingerprint, read_request
        try:
            fp = fingerprint(src)
            refs = {(e['kind'], e['content'].get('id'), str(e['content'].get('version', ''))): e['entry_ref']
                    for e in entries(src) if e['kind'] in ('metric', 'metric_definition')}
        except Exception:
            refs = {}
            fp = None

        def format_versions(chosen, kind):
            if not chosen:
                return []
            newest = copy.deepcopy(chosen[0])
            if 'entry_ref' not in newest and fp and (kind, newest.get('id'), str(newest.get('version', ''))) in refs:
                newest['entry_ref'] = refs[(kind, newest.get('id'), str(newest.get('version', '')))]
                newest['config_fingerprint'] = fp
            older = [brief(m, {'id', 'version', 'state', 'reason'}) for m in chosen[1:]]
            for item in [newest, *older]:
                ref = refs.get((kind, item.get('id'), str(item.get('version', ''))))
                if ref:
                    item['entry_ref'] = ref
                    item['config_fingerprint'] = fp
                    item['read_request'] = read_request(src['id'], ref, fp)
            return [newest] + older

        sem_pool = enrich_metrics(src)
        sem_matches = sorted([m for m in sem_pool if m.get('id') == target_id], key=version_key, reverse=True)
        leg_pool = src.get('metric_definitions') or []
        leg_matches = sorted([m for m in leg_pool if isinstance(m, dict) and m.get('id') == target_id], key=version_key, reverse=True)
        unavail_pool = src.get('unavailable_metrics') or []
        unavail_matches = sorted([m for m in unavail_pool if isinstance(m, dict) and m.get('id') == target_id], key=version_key, reverse=True)

        row['semantic_metrics'] = format_versions(sem_matches, 'metric')
        if leg_matches:
            row['metric_definitions'] = format_versions(leg_matches, 'metric_definition')
        if unavail_matches:
            row['unavailable_metrics'] = format_versions(unavail_matches, 'unavailable_metric')

        total = len(sem_matches) + len(leg_matches) + len(unavail_matches)
        if total > 0:
            row['semantic_metrics_note'] = '已精确筛选指标「%s」，共找到%d个版本定义。' % (target_id, total)
        else:
            row['semantic_metrics_note'] = '未在当前数据源中找到指标「%s」的口径定义。' % target_id
    else:
        metrics = enrich_metrics(src)
        found, total = search_metrics(metrics, question, getattr(args, 'metric_group', None))
        row['semantic_metrics'] = found
        row['semantic_metrics_note'] = ('分组「%s」' % args.metric_group if getattr(args, 'metric_group', None) else '全部') + \
            '共%d个指标，这里按问题列出最相关的%d个完整口径。' % (total, len(found))
        if not found and getattr(args, 'metric_group', None):
            row['metric_groups'], _ = group_list(metrics, question, set())
            row['semantic_metrics_note'] = '没有名为「%s」的分组；可用的分组见 metric_groups。' % args.metric_group
        if (not metrics and not src.get('metric_definitions') and not args.metric_group
                and question and question.strip() and src.get('examples')):
            row.update(matching_sql_examples(src, question))

    result = {k: copy.deepcopy(value[k]) for k in ['project', 'read_only', 'readiness', 'temporal_context', 'catalog_ref', 'expires_in_seconds', 'same_name_metrics'] if k in value}
    result['sources'] = [row]
    result['catalog_view'] = {'paged': False, 'view': 'metrics'}
    return result


def table_business_context(source, table_name, question=''):
    """Find published entries tied to an exact authorized table."""
    if not source or not table_name:
        return {}
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
    from .vendor.dialect import sqlglot_name
    from .vendor.plans import PlanRejected, _cte_reference_ids

    sid = source['id']
    dialect = sqlglot_name(source.get('dialect', 'postgresql'))
    authorized = {t['name'] for t in source.get('tables', [])}
    if table_name not in authorized:
        return {}
    sqlite_case = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')

    def key(parts):
        return tuple(part.translate(sqlite_case) if dialect == 'sqlite' else part for part in parts)

    paths = {key(name.split('.')) for name in authorized}
    selected = key(table_name.split('.'))

    @cache
    def uses_table(sql):
        try:
            tree = normalize_identifiers(sqlglot.parse_one(sql, read=dialect), dialect=dialect)
            ctes = _cte_reference_ids(tree)
        except (sqlglot.errors.SqlglotError, PlanRejected, ValueError):
            return False
        referenced = [ref for ref in tree.find_all(exp.Table) if id(ref) not in ctes]
        if any(not all(isinstance(part, exp.Identifier) for part in ref.parts) for ref in referenced):
            return False
        used = {key(part.name for part in ref.parts) for ref in referenced}
        return selected in used and used <= paths

    examples = [e for e in source.get('examples', []) if e.get('source_id') == sid and e.get('logical_sql')]
    pack_examples = {}
    for example in examples:
        origin = str(example.get('origin') or '')
        if origin.startswith('pack:') and (code := entry_code(example, 'question')):
            pack_examples.setdefault((origin, code), []).append(example)
    def binds_term(term):
        config = term.get('config') or {}
        if ((term.get('source_id') or config.get('source_id')) != sid or term.get('status') == 'review'
                or config.get('kind') == 'semantic-metric'):
            return False
        declared = set(term.get('tables') or [])
        if declared:
            return table_name in declared and declared <= authorized
        # Older imported terms lack physical table metadata. Resolve only a
        # unique example from the same source, pack version and indicator code.
        paired = pack_examples.get((term.get('origin'), entry_code(term, 'name')), [])
        return len(paired) == 1 and uses_table(paired[0]['logical_sql'])

    terms = source.get('terms', [])
    ranked_terms = pick(terms, question, lambda t: f"{t.get('name', '')} {t.get('definition', '')}", len(terms))
    ranked_examples = pick(examples, question, lambda e: e.get('question', ''), len(examples))
    # Take the limit only after checking association, so unrelated textual
    # matches cannot displace valid entries. Parse a candidate at most once.
    term_hits = list(islice((hit for hit in ranked_terms if binds_term(hit[1])), 2))
    ex_hits = list(islice((hit for hit in ranked_examples if uses_table(hit[1]['logical_sql'])), 2))
    if not term_hits and not ex_hits:
        return {}
    _, reference = entry_references(source)
    out = {}
    if term_hits:
        out['caliber_matches'] = [term_brief(t, sc, reference('term', t), question) for sc, t in term_hits]
    if ex_hits:
        out['example_matches'] = [example_brief(e, reference('example', e), None) for _, e in ex_hits]
        out['example_note'] = '按 read_request 查看完整示例。示例期间仅用于说明口径，按本轮要求核对实际查询期间。'
    return out


def paged_catalog(value, args, question=''):
    # 0.6.1: an oversized page size is clamped, not rejected. Small models often
    # ask for 60 tables at once; the old hard limit (16) returned a bare error.
    args = args.model_copy(update={k: min(getattr(args, k), cap) for k, cap in LIMITS.items()})
    scope_question = question
    question = getattr(args, 'question', None) or question
    temporal = value.get('temporal_context')
    if getattr(args, 'entry_ref', None):
        from .catalog_index import read_entry
        if not args.source_id:
            raise ValueError('CATALOG_SELECTION_REQUIRED: 条目读取必须指定 source_id。')
        source = next((s for s in value.get('sources', []) if s['id'] == args.source_id), None)
        if source is None:
            raise PermissionError('CATALOG_ENTRY_NOT_FOUND: 当前数据源未授权。')
        entry = read_entry(source, args.entry_ref, args.entry_version, args.entry_offset, args.entry_limit)
        if entry['complete']:
            entry.pop('content_text')
        row = {'id': args.source_id, 'entry': entry}
        if budget := budget_reference(source, scope_question):
            row['budget_reference'] = budget
        if boundaries := snapshot_boundaries(source, scope_question, temporal):
            row['snapshot_boundaries'] = boundaries
        return {**value, 'sources': [row]}
    if getattr(args, 'view', 'full') == 'metrics':
        result = metric_page(value, args, question, scope_question)
        for row in result['sources']:
            source = next(s for s in value['sources'] if s['id'] == row['id'])
            if boundaries := snapshot_boundaries(source, scope_question, temporal):
                row['snapshot_boundaries'] = boundaries
        return result
    if args.table_name:
        from .catalog_index import resolve_table_name
        if not args.source_id:
            raise ValueError('CATALOG_SELECTION_REQUIRED: 查看表字段时请同时指定 source_id。')
        source = next((s for s in value.get('sources', []) if s.get('id') == args.source_id), None)
        if source is None:
            raise PermissionError('CATALOG_ENTRY_NOT_FOUND: 当前数据源未授权。')
        table_name = resolve_table_name(source, args.table_name)
        args = args.model_copy(update={'table_name': table_name})
        value = {**value, 'sources': [source]}
    raw_sources = {s.get('id'): s for s in value.get('sources', [])}
    sources = []
    for source in value.get('sources', []):
        row = focus_business(source, question, not args.metric_id, args.table_name, scope_question)
        if boundaries := snapshot_boundaries(source, scope_question, temporal, args.table_name):
            row['snapshot_boundaries'] = boundaries
        sources.append(row)
    value = {**value, 'sources': sources}
    forced = any([args.table_name, args.metric_id, args.source_offset, args.table_offset, args.column_offset, args.metric_offset,
                  args.source_limit != 16, args.table_limit != TABLE_PAGE, args.column_limit != 50, args.metric_limit != 8])
    if size(value) <= MAX_BYTES and not forced:
        return value
    if (args.table_name or args.metric_id) and not args.source_id:
        raise ValueError('CATALOG_SELECTION_REQUIRED: 查看表字段或指标详情时请同时指定 source_id。')
    if args.source_id and args.source_offset:
        raise ValueError('CATALOG_SELECTION_CONFLICT: 已指定 source_id 时不使用 source_offset。')
    all_sources = value.get('sources', [])
    source_by_id = {s.get('id'): s for s in all_sources}
    total_sources = len(all_sources)
    single = bool(args.source_id) or total_sources == 1
    sources = all_sources if single else all_sources[args.source_offset:args.source_offset + args.source_limit]
    output = []
    for source in sources:
        row = brief(source, {'id', 'name', 'dialect', 'read_only', 'query_modes', 'metric_mapping', 'target_revision', 'snapshot_boundaries'})
        tables = source.get('tables', [])
        row['table_count'] = len(tables)
        for key in METRIC_LISTS:
            metrics = source.get(key, [])
            row[key + '_count'] = len(metrics) if isinstance(metrics, list) else 0
        first_page = not (args.table_offset or args.metric_offset or args.table_name or args.metric_id)
        if not single:
            # Source list page: no table offsets, so traversal cannot skip tables.
            row['source_context'] = source_context(source)
            row['detail_request'] = {'source_id': source['id']}
            if source.get('metric_groups'):
                row['metric_groups'] = [{'name': g['name'], 'count': g['count']} for g in source['metric_groups'][:GROUP_SHORT]]
            output.append(row)
            continue
        if first_page:
            row['source_context'] = source_context(source)
        if not args.table_name and not args.metric_id:
            tables, ranked = rank_tables(tables, question)
            if ranked:
                row['table_order'] = '按与问题的相关度排序（依据表说明、字段中文名和业务同义词）'
        selected = [t for t in tables if t['name'] == args.table_name] if args.table_name else ([] if args.metric_id else tables[args.table_offset:args.table_offset + args.table_limit])
        if args.table_name and not selected:
            raise ValueError('CATALOG_TABLE_NOT_FOUND: 该来源没有此授权表，请使用目录中的准确表名。')
        row['tables'] = []
        names = {t.get('name') for t in tables}
        for table in selected:
            item = brief(table, {'name', 'label'})
            if table.get('aliases'):
                item['aliases'] = [str(a) for a in table['aliases'][:5 if not args.table_name else 8]]
            if args.table_name and table.get('business_description'):
                item['business_description'] = clip(table['business_description'], 300)
            columns = table.get('columns', [])
            item['column_count'] = len(columns)
            base = copy_of(table.get('name', ''), names) if not args.table_name else None
            if base:
                item['copy_of'] = base  # 0.6.2: likely a copy/staging table; prefer the base table
            if not args.table_name and not table.get('label'):
                # 0.6.2: an unlabeled table shows its first field names, so the model can
                # tell a real business table (zsbh, fzsj, yxq) from a placeholder (zd1, zd2).
                shown = [c for c in columns if not GENERIC_COLUMN.match(str(c.get('name', '')))][:PREVIEW_COLUMNS]
                item['columns_preview'] = ','.join(c['name'] + ('（' + clip(c['label'], 12) + '）' if c.get('label') and c['label'] != c['name'] else '')
                                                   for c in shown)
            if args.table_name:
                item['columns'] = [column_detail(c) for c in columns[args.column_offset:args.column_offset + args.column_limit]]
                relations = [r for r in source.get('relations', []) if table['name'] in r['tables']]
                if relations:
                    item['relations'] = [{k: r[k] for k in ('name', 'join', 'cardinality')} for r in relations[:10]]
                item['next_column_offset'] = args.column_offset + len(item['columns']) if args.column_offset + len(item['columns']) < len(columns) else None
                constraints = table.get('constraints', {})
                if size(constraints) <= 4096:
                    item['constraints'] = copy.deepcopy(constraints)
                else:
                    item['constraints_deferred'] = True
                    item['constraint_note'] = '关联约束过大；基础单表问数可用，复杂关联请使用已发布语义口径，不推测关联。'
                if not args.column_offset and (tbl_ctx := table_business_context(raw_sources.get(source.get('id')), args.table_name, question)):
                    item.update(tbl_ctx)
            row['tables'].append(item)
        row['next_table_offset'] = args.table_offset + len(selected) if not (args.table_name or args.metric_id) and args.table_offset + len(selected) < len(tables) else None
        if not args.table_name and selected:
            # One hint per page instead of a detail_request on every table.
            row['table_detail'] = ('表清单只含表名（无说明的表附前几个字段名）；查看某张表的全部字段：data_catalog(source_id="'+source['id']+'", table_name=表名)。'
                                   '标有 copy_of 的是副本或中间表（ods_、_bak、日期后缀、_correct、_error 等），数据可能不全，优先用 copy_of 指向的正式表。')
        for key in METRIC_LISTS:
            metrics = source.get(key, [])
            if not isinstance(metrics, list):
                metrics = []
            if args.metric_id:
                chosen = sorted([m for m in metrics if m.get('id') == args.metric_id], key=version_key, reverse=True)
                # Full detail for the newest version; older versions are named only.
                row[key] = [copy.deepcopy(m) for m in chosen[:1]] + [brief(m, {'id', 'version', 'state', 'reason'}) for m in chosen[1:]]
                row[key + '_next_offset'] = None
            elif args.table_name:
                row[key] = []
                row[key + '_next_offset'] = 0 if metrics else None
            else:
                if key == 'semantic_metrics' and source.get('semantic_metrics_note') and not args.metric_offset:
                    row[key] = [metric_brief(m) for m in metrics]
                    row['semantic_metrics_note'] = source['semantic_metrics_note']
                    for extra in ('metric_groups', 'metric_groups_note'):
                        if source.get(extra):
                            row[extra] = source[extra]
                    row[key + '_next_offset'] = None
                    continue
                chosen = metrics[args.metric_offset:args.metric_offset + args.metric_limit]
                row[key] = [metric_brief(m) for m in chosen]
                row[key + '_next_offset'] = args.metric_offset + len(chosen) if args.metric_offset + len(chosen) < len(metrics) else None
        output.append(row)
    result = {k: copy.deepcopy(value[k]) for k in ['project', 'read_only', 'readiness', 'temporal_context', 'catalog_ref', 'expires_in_seconds', 'same_name_metrics'] if k in value}
    result['sources'] = output
    next_source = None if single else (args.source_offset + len(output) if args.source_offset + len(output) < total_sources else None)
    result['catalog_view'] = {'paged': True, 'source_count': total_sources, 'next_source_offset': next_source,
        'instruction': ('这是完整 JSON 分页视图，不需要读取临时文件或切换工作目录。' +
                        ('多来源时先按 next_source_offset 浏览来源，再用 detail_request 的 source_id 读取该来源的表和指标。' if not single else
                         '用同一 data_catalog 的 source_id 加 table_offset（见 next_table_offset）、table_name（含 next_column_offset）、metric_offset（见 *_next_offset）或 metric_id 继续读取。') +
                        'catalog_ref 的规划目录仍保留全部授权结构。')}
    if size(result) > MAX_BYTES and not single and len(output) > 1:
        # Narrow whole source records; never slice serialized JSON.
        while len(result['sources']) > 1 and size(result) > MAX_BYTES:
            result['sources'].pop()
        result['catalog_view']['next_source_offset'] = args.source_offset + len(result['sources']) if args.source_offset + len(result['sources']) < total_sources else None
    if size(result) > MAX_BYTES and args.table_name:
        # Field pages and their continuation have priority over optional links.
        for row in result['sources']:
            for table in row['tables']:
                for field in ('caliber_matches', 'example_matches', 'example_note'):
                    table.pop(field, None)
    if size(result) > MAX_BYTES and single:
        # Long column notes first get the standard 180-character view (marked).
        for row in result['sources']:
            for table in row.get('tables', []):
                for column in table.get('columns', []):
                    if isinstance(column.get('label'), str) and len(column['label']) > 180:
                        original = next(c for t in source_by_id[row['id']].get('tables', []) if t['name'] == table['name']
                                        for c in t.get('columns', []) if c['name'] == column['name'])
                        column['label'] = clip(original.get('label'), 180)
    if size(result) > MAX_BYTES and single:
        for row in result['sources']:
            if 'source_context' in row:
                row['source_context'] = clip_deep(row['source_context'], 300)
                row['source_context_clipped'] = True
    if size(result) > MAX_BYTES and single and not args.table_name:
        # Field previews go first; every table name stays listed.
        for row in result['sources']:
            for table in row.get('tables', []):
                table.pop('columns_preview', None)
    if size(result) > MAX_BYTES and single and not args.table_name:
        # Long table notes on a long page: list fewer tables, never fail.
        for row in result['sources']:
            while len(row.get('tables', [])) > 1 and size(result) > MAX_BYTES:
                row['tables'].pop()
            if 'tables' in row:
                shown = args.table_offset + len(row['tables'])
                row['next_table_offset'] = shown if shown < row['table_count'] else None
    if size(result) > MAX_BYTES:
        raise ValueError('CATALOG_PAGE_TOO_LARGE: 本页仍过大，请指定 source_id 和 table_name，并减小 table_limit、column_limit 或 metric_limit；不要读取临时文件或猜测字段。')
    return result
