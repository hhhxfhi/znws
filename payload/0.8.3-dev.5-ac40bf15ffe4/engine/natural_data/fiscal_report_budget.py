"""Compile a conservative fiscal budget read from paired published queries."""
from collections import defaultdict
from datetime import date, timedelta
import re
from typing import NamedTuple
import unicodedata

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from .catalog_index import fingerprint
from .catalog_view import comparison_budget_baseline, entry_code
from .fiscal_history import (_decimal_column, _nonmultiplying_report_operand, _query_fingerprint, _read_sql,
                             _report_shape, _zero_default_input)
from .snapshot_scope import _physical_table
from .temporal_context import temporal_context


_AMOUNT = r'(?:投资(?:金额|额)|(?:(?:项目)?预算(?:金额|总额|额)))'
_COUNT = r'(?:项目数量|项目数|数量|个数)'
_ASK = r'(?:是多少|有多少|多少|(?:是)?(?:怎样|如何|怎么样)(?:的)?)'
_CURRENT = r'(?:本年度?|今年|当年度?)'
_START = r'^(?:请(?:帮我)?(?:查询|统计|计算|看)?\s*)?'
_SUBJECT = r'(?P<province>省级)?(?P<subject>[\u4e00-\u9fff]{3,24}?)的?'
_SINGLE = re.compile(_START + _CURRENT + '的?' + _SUBJECT + _COUNT + r'\s*[,、，和及与]\s*' + _AMOUNT
                     + r'(?:分别)?' + _ASK + r'[。!?？]?$')
_ANNUAL = re.compile(_START + r'近(?:五|5)年(?:度)?的?' + _SUBJECT + _AMOUNT + r'(?:变化)?趋势'
                     + _ASK + r'\s*[,。!?？；;]?\s*(?:并|以及|再)?\s*(?:请)?预测(?:下(?:一)?年|明年)(?:度)?的?'
                     + _AMOUNT + r'(?:情况|趋势)?[。!?？]?$')
_COUNT_TERM = re.compile(r'^'+_CURRENT+r'(.+?)(?:数量|个数|数)$')
_BUDGET_TERM = re.compile(r'^'+_CURRENT+r'(.+?)预算(?:金额|总额|额)$')
_UNREVIEWED = {'draft', 'review', 'pending_review'}
_NUMERIC = re.compile(r'^(?:(?:big|small)?int(?:eger)?|numeric|decimal|text|varchar|character varying)(?:\([\d, ]+\))?$', re.I)
_LOCAL_DATE = re.compile(r'^(?:date|timestamp(?:\(\d+\))?(?: without time zone)?|text|varchar|character varying)(?:\(\d+\))?$', re.I)
_COUNT_UNIT = re.compile(r'^\s*(?:计量)?单位\s*[：:]\s*(个|项|件|条|笔|户|家|次|台)(?=[。；;，,\s]|$)')


class _Population(NamedTuple):
    table: str
    year: str
    year_value: str
    year_text: bool
    unit: str
    allowed: tuple[str, ...]


def _publication_bindings(source, subject):
    examples = defaultdict(list)
    terms = {'budget': [], 'count': []}
    sid = source['id']
    for example in source.get('examples', []):
        if (example.get('source_id') == sid and example.get('origin') and example.get('id')
                and example.get('status') not in _UNREVIEWED and not example.get('parameters')
                and example.get('logical_sql') and (code := entry_code(example, 'question'))):
            examples[example['origin'], code].append(example)
    for term in source.get('terms', []):
        if (term.get('source_id') != sid or not term.get('id') or not term.get('origin')
                or term.get('status') in _UNREVIEWED or term.get('clarification') or term.get('pending_clarification')
                or term.get('config') or not (code := entry_code(term, 'name'))):
            continue
        name = re.sub(r'^[A-Z0-9_-]+\s+', '', term['name']).strip()
        definition = term.get('definition') or ''
        if not definition or re.search(r'推定|待确认|不可计算', definition):
            continue
        for kind, pattern in (('budget', _BUDGET_TERM), ('count', _COUNT_TERM)):
            parsed = pattern.fullmatch(name)
            if not parsed or parsed[1] not in (subject, subject.removeprefix('省级')):
                continue
            matching = examples.get((term['origin'], code), ())
            # A query without a geographic qualifier cannot inherit a
            # province-only published definition (or the converse).
            if len(matching) == 1 and (kind != 'count' or bool('省级' in definition) == subject.startswith('省级')):
                terms[kind].append((term, matching[0]))
    if all(len(value) == 1 for value in terms.values()) and terms['count'][0][0]['origin'] == terms['budget'][0][0]['origin']:
        return terms['count'][0], terms['budget'][0]
    return None


def _population(select, source, *, join=False):
    from .published_scope import _conjuncts

    keys = {'expressions', 'from', 'from_', 'where', *(['joins'] if join else [])}
    if not isinstance(select, exp.Select) or any(v for k, v in select.args.items() if k not in keys):
        return None
    relation = select.args.get('from') or select.args.get('from_')
    relation = relation.this if relation else None
    if not (physical := _physical_table(relation, source, 'postgres')) or len(select.expressions) != 1:
        return None
    if not (where := select.args.get('where')) or len(atoms := _conjuncts(where.this)) != 2:
        return None
    if any(not isinstance(a.this, exp.Column) or a.this.table not in ('', relation.alias_or_name)
           or a.this.db or a.this.catalog for a in atoms):
        return None
    annual = [a for a in atoms if isinstance(a, exp.EQ) and isinstance(a.expression, exp.Literal)
              and re.fullmatch(r'20[0-9]{2}', a.expression.this)]
    units = [a for a in atoms if isinstance(a, exp.In) and not a.args.get('query')
             and 0 < len(a.expressions) <= 30 and all(isinstance(x, exp.Literal) and x.is_string for x in a.expressions)]
    if len(annual) != 1 or len(units) != 1 or annual[0].this.name == units[0].this.name:
        return None
    unit = units[0]
    return _Population(physical, annual[0].this.name, annual[0].expression.this,
                       annual[0].expression.is_string, unit.this.name, tuple(sorted({x.this for x in unit.expressions})))


def _budget_recipe(source, count, budget):
    try:
        counts, sums = [_read_sql(e['logical_sql'], 'postgres') for _, e in (count, budget)]
    except SqlglotError:
        return None
    if not (population := _population(counts, source)) or _population(sums, source, join=True) != population:
        return None
    row_count = counts.expressions[0].unalias()
    if (not isinstance(row_count, exp.Count) or row_count.expressions or
            (not isinstance(row_count.this, exp.Star) and row_count.this != exp.Literal.number(1))):
        return None
    amount = sums.expressions[0].unalias()
    if not isinstance(amount, exp.Sum) or amount.expressions:
        return None
    operand = amount.this.unnest()
    field = _decimal_column(_zero_default_input(operand) or operand)
    scopes = traverse_scope(sums)
    if (not field or not scopes or not sums.args.get('joins')
            or not _nonmultiplying_report_operand(scopes[-1], field, source, 'postgres')):
        return None
    root = scopes[-1]
    left, right = sums.args['joins'][0].args['on'].this, sums.args['joins'][0].args['on'].expression
    entity_key = (right if left.table == field.table else left).name
    report_ref = root.selected_sources[field.table][1]
    report = _report_shape(report_ref, source, 'postgres')
    if report.fields != (field.name,):
        return None
    fields = {(t['name'], c['name']): str(c.get('type') or '') for t in source['tables'] for c in t['columns']}
    if ((population.table, entity_key) not in fields or (population.table, population.year) not in fields
            or (population.table, population.unit) not in fields
            or not _NUMERIC.fullmatch(fields.get((report.table, field.name), ''))
            or not _LOCAL_DATE.fullmatch(fields.get((report.table, report.time), ''))):
        return None
    mapping = [m for m in source.get('business_dictionary', []) if m.get('kind') == 'field' and m.get('source_id') == source['id']
               and m.get('table') == report.table and m.get('column') == field.name]
    from .fiscal_history import _LEGACY_CURRENCY
    from .business_dictionary import declared_field_unit
    from .sql_answer_contract import _BUDGET
    if len(mapping) != 1 or not _BUDGET.fullmatch(label := str(mapping[0].get('name') or '')):
        return None
    unit = declared_field_unit(mapping[0])
    units = {unit[0]} if unit else set(_LEGACY_CURRENCY.findall(mapping[0].get('description') or ''))
    if len(units) != 1 or not re.fullmatch(r'(?:人民币)?(?:万|亿)?元', unit := units.pop()):
        return None
    count_definition, count_example = count
    unit_text = count_definition.get('definition') or ''
    count_unit = _COUNT_UNIT.match(unit_text)
    if not count_unit:
        return None
    declared = re.search(r'[（(]([^）)]+)[）)]$', counts.expressions[0].alias_or_name)
    if declared and declared[1] != count_unit[1]:
        return None
    return population, entity_key, report, field.name, label, unit, count_unit[1]


def _sql(recipe, today, annual):
    population, key, report, amount, label, unit, count_unit = recipe
    q = lambda name: exp.to_identifier(name, quoted=True).sql(dialect='postgres')
    table = lambda name: '.'.join(q(part) for part in name.split('.'))
    text = lambda value: exp.Literal.string(value).sql(dialect='postgres')
    yr = lambda n: text(str(n)) if population.year_text else str(n)
    year, cutoff = today.year, (today + timedelta(days=1)).isoformat()
    years = ', '.join(yr(n) for n in range(year-4 if annual else year, year+1))
    values = ', '.join(map(text, population.allowed))
    # A malformed date might be the latest report. Keep it visible as an
    # uncertainty; a finite earlier amount cannot silently replace it.
    iso = ("'^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
           "([ T]([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]([.][0-9]{1,6})?)?$'")
    return f'''WITH requested AS (
 SELECT {q(key)} AS entity, {q(population.year)} AS year
 FROM {table(population.table)} WHERE {q(population.year)} IN ({years}) AND {q(population.unit)} IN ({values})
), identity AS (
 SELECT p.{q(key)} AS entity, COUNT(*) AS copies FROM {table(population.table)} p
 WHERE EXISTS (SELECT 1 FROM requested e WHERE e.entity = p.{q(key)}) GROUP BY p.{q(key)}
), eligible AS (
 SELECT e.entity, e.year, i.copies FROM requested e LEFT JOIN identity i USING (entity)
), raw AS (
 SELECT r.{q(report.key)} AS entity, TRIM(CAST(r.{q(report.time)} AS TEXT)) AS raw_time,
        TRIM(CAST(r.{q(amount)} AS TEXT)) AS raw_amount
 FROM {table(report.table)} r
 WHERE EXISTS (SELECT 1 FROM eligible e WHERE e.entity = r.{q(report.key)})
), normalized AS (
 SELECT entity, CASE WHEN raw_time ~ {iso} AND LEFT(raw_time,4) <> '0000' THEN
     CASE WHEN EXTRACT(MONTH FROM (MAKE_DATE(CAST(LEFT(raw_time,4) AS INTEGER),
       CAST(SUBSTRING(raw_time,6,2) AS INTEGER),1) + CAST(SUBSTRING(raw_time,9,2) AS INTEGER) - 1))
       = CAST(SUBSTRING(raw_time,6,2) AS INTEGER) THEN CAST(raw_time AS TIMESTAMP) END END AS reported,
   CASE WHEN raw_amount ~ '^[+-]?[0-9]{{1,28}}([.][0-9]{{1,10}})?$' THEN CAST(raw_amount AS NUMERIC) END AS amount
 FROM raw
), ranked AS (
 SELECT entity, reported, amount, DENSE_RANK() OVER (PARTITION BY entity ORDER BY reported DESC NULLS FIRST) AS ranking
 FROM normalized WHERE reported IS NULL OR reported < TIMESTAMP {text(cutoff)}
), latest AS (
 SELECT entity, CASE WHEN COUNT(*)=1 AND COUNT(reported)=1 THEN MAX(amount) END AS amount
 FROM ranked WHERE ranking=1 GROUP BY entity
)
SELECT year AS "年度", CASE WHEN COUNT(*)=COUNT(entity) AND MAX(copies)=1 THEN COUNT(*) END AS {q('项目数量（'+count_unit+'）')},
 COUNT(amount) FILTER (WHERE copies=1) AS {q('可核算项目数（'+count_unit+'）')},
 CASE WHEN COUNT(*)=COUNT(amount) AND COUNT(*)=COUNT(entity) AND MAX(copies)=1 THEN SUM(amount) END AS {q(label+'（'+unit+'）')}
FROM eligible LEFT JOIN latest USING (entity) GROUP BY year ORDER BY year'''


def _historical_reference(source, today, subject, definitions, recipe, primary_proof):
    """Read the latest closed year's separate budget only through its published basis."""
    from .retrieval import normalize

    year = str(today.year - 1)
    subject = normalize(subject).removeprefix('省级').removesuffix('项目')
    baseline = comparison_budget_baseline(source, year, lambda name: name == subject,
                                          published_origin=definitions[0][0]['origin'])
    if not baseline:
        return None
    _, example, candidate = baseline
    if candidate['table'] in (recipe[0].table, recipe[2].table):
        return None
    sql, label, unit = (candidate[k] for k in ('sql', 'label', 'unit'))
    column = f'{label}（{unit}）'
    keys = ('state', 'basis', 'match', 'source_id', 'project_id', 'project_version', 'request_ref')
    proof = {key: primary_proof[key] for key in keys}
    proof.update(period='historical_reference', year=year,
                 definition_fingerprint=fingerprint({'example': example, 'read': candidate}),
                 primary_definition_fingerprint=primary_proof['definition_fingerprint'],
                 query_fingerprint=_query_fingerprint(_read_sql(sql, 'postgres'), 'postgres'),
                 measurements={column: {'role': 'budget', 'label': label, 'unit': unit}},
                 numerical_acceptance='not_evaluated')
    query = {'source_id': source['id'], 'sql': sql, 'title': f'{year}年独立预算参考',
             'chart_type': 'table', 'purpose': 'answer'}
    return {'year': year, 'query_request': query, 'proof': proof}


def _closed_budget_trend(host, today, reference):
    # Use the same published population and direct budget SUM as the verified
    # baseline. Only the reporting axis changes from a year to the closed window.
    from .fiscal_history import annual_fiscal_window
    from .published_scope import _conjuncts
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    local = datetime.combine(today, time.min, tzinfo=ZoneInfo(host.get('client_time_zone') or 'UTC'))
    window = annual_fiscal_window(host, now=local)
    years = (window or {}).get('requested_years', [])
    if not years or years[-1] != reference['year']:
        return None
    tree = _read_sql(reference['query_request']['sql'], 'postgres')
    filters = _conjuncts(tree.args['where'].this)
    anchors = [atom for atom in filters if isinstance(atom, exp.EQ) and isinstance(atom.this, exp.Column)
               and isinstance(atom.expression, exp.Literal) and atom.expression.this == reference['year']]
    if len(anchors) != 1:
        return None
    anchor, = anchors
    axis = anchor.this
    literal = exp.Literal.string if anchor.expression.is_string else exp.Literal.number
    within = exp.In(this=axis.copy(), expressions=[literal(year) for year in years])
    tree.where(*(within if atom is anchor else atom.copy() for atom in filters), append=False, copy=False)
    tree.select(exp.alias_(axis.copy(), '年度', quoted=True), append=True, copy=False)
    tree.set('expressions', [tree.expressions[-1], *tree.expressions[:-1]])
    tree.group_by(axis.copy(), copy=False).order_by(axis.copy(), copy=False)
    sql = tree.sql(dialect='postgres')
    request = {**reference['query_request'], 'sql': sql, 'title': '已结年度项目预算趋势', 'chart_type': 'column'}
    proof = {**reference['proof'], 'period': 'closed_budget_trend', 'time_column': '年度',
             'requested_years': years, 'query_fingerprint': _query_fingerprint(_read_sql(sql, 'postgres'), 'postgres')}
    return {'query_request': request, 'proof': proof}


def published_budget_guidance(source, host):
    host = host or {}
    question = unicodedata.normalize('NFKC', host.get('user_text') or '').strip()
    annual = _ANNUAL.fullmatch(question)
    match = annual or _SINGLE.fullmatch(question)
    publication = source.get('business_project') or {}
    if (not match or not host.get('user_ref') or host.get('clarifications') or source.get('dialect') != 'postgresql'
            or not source.get('id') or not publication.get('id') or not publication.get('version')):
        return None
    subject = (match['province'] or '') + match['subject']
    if not (definitions := _publication_bindings(source, subject)) or not (
            recipe := _budget_recipe(source, *definitions)):
        return None
    clock = temporal_context(host.get('client_time_zone') or 'UTC')
    if clock['zone_fallback']:
        return None
    today = date.fromisoformat(clock['current_date'])
    query = _sql(recipe, today, annual)
    label, unit, count_unit = recipe[-3:]
    column = label+'（'+unit+'）'
    # This proof identifies the active business rule; a NULL aggregate
    # separately discloses incomplete or ambiguous input coverage.
    proof = {'state': 'configured', 'basis': 'published_configuration', 'match': 'validated_report_budget_sql',
             'source_id': source['id'], 'project_id': publication['id'], 'project_version': publication['version'],
             'request_ref': host['user_ref'], 'definition_fingerprint': fingerprint(definitions),
             'query_fingerprint': _query_fingerprint(_read_sql(query, 'postgres'), 'postgres'),
             'time_column': '年度', 'count_unit': count_unit, 'count_column': '项目数量（'+count_unit+'）',
             'covered_column': '可核算项目数（'+count_unit+'）',
             'measurements': {column: {'role': 'budget', 'label': label, 'unit': unit}},
             'as_of_date': today.isoformat(), 'period': 'annual_history' if annual else 'current_year',
             'numerical_acceptance': 'not_evaluated'}
    reference = _historical_reference(source, today, subject, definitions, recipe, proof) if annual else None
    trend = _closed_budget_trend(host, today, reference) if reference else None
    if reference:
        proof['historical_reference'] = {k: reference['proof'][k] for k in ('year', 'query_fingerprint', 'definition_fingerprint')}
    return {'kind': 'published_report_budget', 'query_request': {'source_id': source['id'], 'sql': query,
            'title': ('各年度' if annual else f'{today.year}年度') + match['subject']+'数量与'+label,
            'chart_type': 'table', 'purpose': 'answer'}, 'proof': proof,
            'report_table': recipe[2].table, 'budget_field': recipe[3],
            **({'historical_reference': reference} if reference else {}),
            **({'closed_budget_trend': trend} if trend else {})}


def report_budget_evidence(sql, source, host):
    guidance = published_budget_guidance(source, host)
    if not guidance:
        return None
    tree = _read_sql(sql, 'postgres')
    fingerprint_for_sql = _query_fingerprint(tree, 'postgres')
    if fingerprint_for_sql == guidance['proof']['query_fingerprint']:
        return guidance['proof']
    for key in ('historical_reference', 'closed_budget_trend'):
        if (reference := guidance.get(key)) and fingerprint_for_sql == reference['proof']['query_fingerprint']:
            return reference['proof']
    if (any(_physical_table(table, source, 'postgres') == guidance['report_table'] for table in tree.find_all(exp.Table))
            and any(column.name == guidance['budget_field'] for total in tree.find_all(exp.Sum)
                    for column in total.find_all(exp.Column))):
        raise ValueError('FISCAL_BUDGET_QUERY_REQUIRED: 此处读的是已发布的项目预算字段，须核查项目标识重复、并列最新报送和缺失金额。'
                         '请用 data_catalog 已发布预算查询的 query_request；若另有已授权且同口径的实际投入记录，可以单独查询。')
    return None
