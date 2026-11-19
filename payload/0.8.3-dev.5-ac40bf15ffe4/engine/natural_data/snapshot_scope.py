"""A published current snapshot cannot answer a historical population request."""
import re
import unicodedata
from datetime import date

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, build_scope

from .catalog_view import entry_code
from .day_bounds import snapshot_day
from .metric_limitations import _has_separate_comparison, _normalized, _pattern, entries
from .temporal_context import temporal_context
from .vendor.dialect import sqlglot_name


_CURRENT_ONLY = re.compile(r'(?:^|[。；;，,]\s*|为|是|只(?:保留|保存|提供)|仅(?:保留|保存|提供))'
                           r'\s*当前(?:有效)?快照(?=[，,。；;\s]|$)')
_OLDER_MONTH = re.compile(r'(?<!\d)(20\d{2})\s*(?:年\s*(\d{1,2})\s*月|[-/](\d{1,2})(?=[-/]|\b))')
_OLDER_YEAR = re.compile(r'(?<!\d)(20\d{2})\s*年')
_RELATIVE_PAST = re.compile(r'昨天|昨日|前天|上(?:个)?(?:月|季度)|上(?:一)?年|去年|前年|历史(?:时点|快照|存量)|历年(?:期末|年末|存量)')
_REFINEMENT = re.compile(r'^\s*(?:请)?(?:继续(?:完成)?(?:上一题|上个问题|原问题|未完成的回答)?|'
                         r'(?:只|再)?(?:看|查|统计)\S{1,20}(?:市|地区|区域|专业|等级)|'
                         r'那|改为|改成|换成)[。！!？?\s]*$')


def _historical_request(host):
    text = unicodedata.normalize('NFKC', host.get('user_text') or '')
    if not text:
        return False
    if retry := re.match(r'^继续完成第(\d+)轮未完成的回答。', text):
        text = next((item.get('text', '') for item in reversed(host.get('recent_requests') or [])
                     if item.get('turn') == int(retry[1])), text)
    elif _REFINEMENT.fullmatch(text) and host.get('recent_questions'):
        text = host['recent_questions'][-1]
    for item in host.get('clarifications', []):
        if item.get('call_id') and item.get('question_id') and re.search(r'哪年|年份|年度|期间|月底|月末|截止|截至|日期', item.get('question', '')):
            text += '\n' + item.get('answer', '')
    today = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
    cutoff = snapshot_day({**host, 'user_text': text, 'recent_questions': []})
    if cutoff and cutoff < today:
        return True
    if _RELATIVE_PAST.search(text):
        return True
    months = _OLDER_MONTH.findall(text)
    if any(1 <= int(m or iso) <= 12 and (int(y), int(m or iso)) < (today.year, today.month)
           for y, m, iso in months):
        return True
    return any(int(year) < today.year for year in _OLDER_YEAR.findall(text))


def _physical_table(ref, source, dialect):
    if not isinstance(ref, exp.Table) or not isinstance(ref.this, exp.Identifier):
        return None
    from .display_units import _table_identity
    key = str.casefold if dialect == 'sqlite' else str
    tables = {key(t['name']): t['name'] for t in source.get('tables', [])}
    name = _table_identity('.'.join(part.name for part in ref.parts), tables, key)
    return tables[name] if name else None


def _snapshot_tables(source, dialect):
    source_id = source.get('id')
    declared = {mapping['table'] for mapping in source.get('business_dictionary', [])
                if mapping.get('kind') in ('table', 'time') and mapping.get('source_id') == source_id
                and _CURRENT_ONLY.search(mapping.get('description') or '')}
    examples_by_table = {}
    for term in source.get('terms', []):
        if (not term.get('origin') or term.get('source_id') != source_id or term.get('status') == 'review'
                or not _CURRENT_ONLY.search(term.get('definition') or '') or not (code := entry_code(term, 'name'))):
            continue
        # Older workbook imports keep the glossary and executable example as
        # separate rows. Only the exact source, origin and code form a binding.
        examples = [example for example in source.get('examples', []) if example.get('source_id') == source_id
                    and example.get('origin') == term['origin'] and entry_code(example, 'question') == code]
        if len(examples) != 1 or not (sql := examples[0].get('logical_sql')):
            continue
        try:
            tree = normalize_identifiers(sqlglot.parse_one(sql, read=dialect), dialect=dialect)
        except sqlglot.errors.SqlglotError:
            continue  # Old stored examples must still parse before they supply a declaration.
        refs = list(tree.find_all(exp.Table))
        if len(refs) == 1 and (table := _physical_table(refs[0], source, dialect)):
            declared.add(table)
            examples_by_table[table] = (term, tree)
    return declared, examples_by_table


def _shape_matches_current_example(tree, example, table):
    if not isinstance(tree, exp.Select) or not isinstance(example, exp.Select):
        return False
    from .result_lineage import population

    def counting_projection(select):
        columns = select.expressions
        if len(columns) != 1 or not isinstance(count := columns[0].unalias().unnest(), exp.Count):
            return False
        return isinstance(count.this, exp.Star) or isinstance(count.this, exp.Literal) and count.this.is_int and count.this.this == '1'

    if not (counting_projection(tree) and counting_projection(example)):
        return False
    def identity(select):
        # The parser resolved both references to this authorized relation. A
        # quoted or unqualified spelling of that same name is equivalent.
        select = select.copy()
        relation = select.args.get('from')
        if not relation or not isinstance(relation.this, exp.Table):
            return None
        relation.this.replace(exp.to_table(table, alias=relation.this.alias or None))
        return population(select.sql(), None)

    requested = identity(tree)
    return requested is not None and requested == identity(example)


def _is_declared_current_component(tree, examples, source, host, dialect):
    # A matching published base-count example can answer the current portion of
    # a registered current+comparison request. Its example period is not a
    # historical snapshot date and never grants a standalone historical query.
    question = _normalized(host.get('user_text', ''))
    matches = [match for entry in entries(source) for match in _pattern(entry).finditer(question)]
    relation = tree.args.get('from')
    table = _physical_table(relation.this, source, dialect) if relation else None
    if not matches or table not in examples:
        return False
    term, example = examples[table]
    return (_has_separate_comparison({**source, 'terms': [term]}, question, matches)
            and _shape_matches_current_example(tree, example, table))


def _uses_snapshot_population(scope, source, dialect, declared, visited):
    if scope is None or id(scope) in visited:
        return False
    visited.add(id(scope))

    def uses(ref):
        if isinstance(ref, Scope):
            return _uses_snapshot_population(ref, source, dialect, declared, visited)
        return _physical_table(ref, source, dialect) in declared

    select = scope.expression
    if isinstance(select, exp.SetOperation):
        return any(uses(branch) for branch in scope.union_scopes)
    if not isinstance(select, exp.Select):
        return False
    from_ = select.args.get('from')
    if from_:
        primary = scope.selected_sources.get(from_.this.alias_or_name)
        if primary and uses(primary[1]):
            return True
    # A SELECT's scalar subquery can itself be the requested historical value.
    return any(child.expression.find_ancestor(exp.Subquery) in tuple(select.selects) and uses(child)
               for child in scope.subquery_scopes)


def check_current_snapshot(source, host, sql, dialect=None):
    host = host or {}
    if not source.get('business_project', {}).get('version') or not _historical_request(host):
        return
    dialect = dialect or sqlglot_name(source.get('dialect'))
    declared, examples = _snapshot_tables(source, dialect)
    if not declared:
        return
    tree = normalize_identifiers(sqlglot.parse_one(sql, read=dialect), dialect=dialect)
    if (_uses_snapshot_population(build_scope(tree), source, dialect, declared, set())
            and not _is_declared_current_component(tree, examples, source, host, dialect)):
        raise ValueError('SNAPSHOT_HISTORY_REQUIRED: 已发布口径说明本次查询来源为当前有效快照。'
                         '当前记录无法核实用户指定的过去期间，请使用同口径、带可信时点的历史快照，'
                         '或可完整还原该时点的历史记录。缺少依据时说明历史结果无法核实，不返回当前数量代替，也不按零处理。')
