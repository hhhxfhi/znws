"""Business configuration checks for model-written SQL.

Known labels used as stored codes are rejected before answer execution.
Other disagreements retain the original non-blocking reminders below.

- An enum field filtered by a value that is not in its value table. When the
  value is a business label, the reminder names the raw value it stands for.
- Two tables joined on columns other than the configured relation.

Only plain comparisons and simple ON conditions are inspected; anything else is
left alone, so a reminder is never raised on a guess.
"""
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

DIALECTS = {'postgresql': 'postgres', 'tbase': 'postgres', 'kingbase': 'postgres', 'dameng': 'oracle'}
MAX_NOTES = 4


def require_enum_values(sql, source):
    """Reject a known label used as a code before an answer query executes.

    Only published mappings are authoritative. Unknown codes may be omitted
    from a partial map, so they remain the existing reminder's responsibility.
    Scope-preserving projections (including MIN/MAX of codes) retain lineage;
    CASE-generated labels and arbitrary transformations do not.
    """
    from .business_dictionary import declared_field_unit
    from .display_units import require_published_units

    tables = {str(t['name']).lower(): t for t in source.get('tables', [])}
    has_units = any(t.get('row_unit') for t in tables.values()) or any(
        declared_field_unit(m) for m in source.get('business_dictionary', []))
    has_enums = any(c.get('business_values') for t in tables.values() for c in t.get('columns', []))
    if not (has_units or has_enums):
        return
    tree = sqlglot.parse_one(sql, read=DIALECTS.get(source.get('dialect'), source.get('dialect') or None))
    if has_units:
        require_published_units(tree.copy() if has_enums else tree, source)
    if not has_enums:
        return
    projections = {}

    def physical(ref):
        key = '.'.join(p.name for p in ref.parts).lower()
        if key in tables:
            return tables[key]
        matches = [t for name, t in tables.items() if name.split('.')[-1] == key]
        return matches[0] if len(matches) == 1 else None

    def from_source(ref, name):
        if isinstance(ref, Scope):
            return projections.get(id(ref), {}).get(name.lower())
        if isinstance(ref, exp.Table):
            table = physical(ref)
            return next((c for c in (table or {}).get('columns', []) if str(c['name']).lower() == name.lower()), None)

    def origin(node, scope):
        while isinstance(node, (exp.Alias, exp.Paren, exp.Min, exp.Max)):
            node = node.this
        if not isinstance(node, exp.Column):
            return None
        sources = [ref for alias, ref in scope.sources.items() if alias.lower() == node.table.lower()] if node.table else scope.sources.values()
        found = [c for ref in sources if (c := from_source(ref, node.name)) is not None]
        return found[0] if len(found) == 1 else None

    def pattern_matches(pattern, value):
        # SQL LIKE patterns have no implicit regex semantics. Escapes need a
        # dialect-specific interpretation and are deliberately left alone.
        if '\\' in pattern:
            return False
        import re
        expression = ''.join('.*' if c == '%' else '.' if c == '_' else re.escape(c) for c in pattern)
        return re.fullmatch(expression, value, re.S) is not None

    for scope in traverse_scope(tree):
        select = scope.expression
        projections[id(scope)] = {item.alias_or_name.lower(): column for item in select.selects
            if item.alias_or_name and (column := origin(item, scope)) is not None}
        for node in select.find_all(exp.EQ, exp.NEQ, exp.In, exp.Like, exp.ILike):
            if node.find_ancestor(exp.Select) is not select:
                continue
            column, values = origin(node.this, scope), None
            if isinstance(node, exp.In):
                values = [str(item.this) for item in node.expressions] if node.expressions and all(isinstance(item, exp.Literal) for item in node.expressions) else None
            elif isinstance(node.expression, exp.Literal):
                values = [str(node.expression.this)]
            elif isinstance(node.this, exp.Literal) and isinstance(node, (exp.EQ, exp.NEQ)):
                column, values = origin(node.expression, scope), [str(node.this.this)]
            mapping = (column or {}).get('business_values') or []
            if not mapping or not values:
                continue
            raw = {str(v['value']): str(v['label']) for v in mapping if 'value' in v and 'label' in v}
            for value in values:
                if isinstance(node, (exp.Like, exp.ILike)):
                    matches = lambda v: pattern_matches(value.casefold() if isinstance(node, exp.ILike) else value,
                        v.casefold() if isinstance(node, exp.ILike) else v)
                    if any(matches(code) for code in raw):
                        continue
                    codes = [code for code, label in raw.items() if matches(label)]
                else:
                    if value in raw:
                        continue
                    codes = [code for code, label in raw.items() if label == value]
                if codes:
                    raise ValueError('ENUM_VALUE_MISMATCH: 字段「'+str(column.get('label') or column['name'])+
                        '」存储枚举编码；不能用业务名称「'+value+'」直接匹配。已发布映射对应原值 '+
                        '、'.join(repr(code) for code in codes)+
                        '。请按目录中的原始编码修正条件后重试，保留同一日期、地区与统计对象；不要把不匹配产生的零当作业务结果。')


def _tables(tree):
    """Alias (or name) -> real table name, lower case, last path segment."""
    out = {}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        out[(t.alias_or_name or name).lower()] = name
        out[name] = name
    return out


def _resolve(column, aliases, owners):
    """Real table of a column reference, or None when it cannot be told."""
    if column.table:
        return aliases.get(column.table.lower())
    tables = owners.get(column.name.lower(), set())
    return next(iter(tables)) if len(tables) == 1 else None


def _literals(node):
    if isinstance(node, exp.Literal):
        return [node.this]
    if isinstance(node, exp.Boolean):
        return ['true' if node.this else 'false']
    return None


def check_sql(sql, source):
    if not sql or not isinstance(source, dict):
        return []
    try:
        tree = sqlglot.parse_one(sql, read=DIALECTS.get(source.get('dialect'), source.get('dialect') or None))
    except Exception:
        return []
    aliases = _tables(tree)
    used = set(aliases.values())
    tables = {t['name'].split('.')[-1].lower(): t for t in source.get('tables', []) if t['name'].split('.')[-1].lower() in used}
    owners = {}
    for name, table in tables.items():
        for c in table.get('columns', []):
            owners.setdefault(str(c.get('name', '')).lower(), set()).add(name)
    notes = []
    # Enum values.
    enums = {(name, str(c['name']).lower()): c for name, t in tables.items() for c in t.get('columns', []) if isinstance(c.get('business_values'), list) and c['business_values']}
    if enums:
        for node in tree.find_all(exp.EQ, exp.NEQ, exp.In):
            column, values = None, None
            if isinstance(node, exp.In) and isinstance(node.this, exp.Column):
                column, values = node.this, [x for e in node.expressions for x in (_literals(e) or [None])]
            else:
                left, right = node.this, node.expression
                if isinstance(left, exp.Column) and _literals(right) is not None:
                    column, values = left, _literals(right)
                elif isinstance(right, exp.Column) and _literals(left) is not None:
                    column, values = right, _literals(left)
            if column is None or not values or None in values:
                continue
            table = _resolve(column, aliases, owners)
            c = enums.get((table, column.name.lower())) if table else None
            if not c:
                continue
            raw = {str(v.get('value')): str(v.get('label')) for v in c['business_values']}
            by_label = {label: value for value, label in raw.items()}
            for value in values:
                if str(value) in raw:
                    continue
                name = c.get('label') or c['name']
                if str(value) in by_label:
                    notes.append(f'字段「{name}」用业务名称「{value}」筛选，但库里存的是原始值「{by_label[str(value)]}」；请把条件改为 {c["name"]} = \'{by_label[str(value)]}\'。')
                else:
                    shown = '、'.join(f'{k}={v}' for k, v in list(raw.items())[:8])
                    notes.append(f'字段「{name}」的取值「{value}」不在业务配置的枚举对照中（可用：{shown}）；请核对筛选值。')
    # Configured relations.
    relations = [r for r in source.get('business_dictionary') or [] if r.get('kind') == 'relation']
    for join in tree.find_all(exp.Join):
        on = join.args.get('on')
        if on is None:
            continue
        pairs, sides = set(), set()
        for eq in on.find_all(exp.EQ):
            if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                a, b = _resolve(eq.this, aliases, owners), _resolve(eq.expression, aliases, owners)
                if a and b and a != b:
                    pairs.add(frozenset({(a, eq.this.name.lower()), (b, eq.expression.name.lower())}))
                    sides.add(frozenset({a, b}))
        for r in relations:
            ends = frozenset({r['table'].split('.')[-1].lower(), r['to_table'].split('.')[-1].lower()})
            if ends not in sides:
                continue
            expected = {frozenset({(r['table'].split('.')[-1].lower(), f.lower()), (r['to_table'].split('.')[-1].lower(), t.lower())}) for f, t in zip(r['from_columns'], r['to_columns'])}
            used_pairs = {p for p in pairs if frozenset(x for x, _ in p) == ends}
            if used_pairs != expected:
                configured = r['table'] + '.(' + ','.join(r['from_columns']) + ') = ' + r['to_table'] + '.(' + ','.join(r['to_columns']) + ')'
                notes.append(f'表 {r["table"]} 与 {r["to_table"]} 的关联方式和业务配置不一致：配置为 {configured}（{r.get("name") or "表关系"}）；请按配置重写关联条件。')
    return list(dict.fromkeys(notes))[:MAX_NOTES]
