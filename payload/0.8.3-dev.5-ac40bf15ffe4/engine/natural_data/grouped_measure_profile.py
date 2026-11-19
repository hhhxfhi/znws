"""Evidence for complete grouped averages over zero and missing observations."""
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .catalog_index import fingerprint


def _group_input(root):
    """Inline one non-aggregating CTE projection while retaining its row scope."""
    select = root.expression
    if len(root.selected_sources) == 1:
        alias, (_, source) = next(iter(root.selected_sources.items()))
        if isinstance(source, Scope):
            inner = source.expression
            allowed = {'from', 'from_', 'joins', 'where', 'expressions'}
            if (len(root.cte_sources) != 1 or select.args.get('where')
                    or not isinstance(inner, exp.Select) or source.outer_columns
                    or any(value for key, value in inner.args.items() if key not in allowed)
                    or not all(isinstance(relation, exp.Table) for _, relation in source.selected_sources.values())):
                return None
            named = {item.alias_or_name: item.unalias().unnest() for item in inner.selects}
            if (len(named) != len(inner.selects) or not named or not all(named)
                    or any(not isinstance(node, exp.Column) or node.is_star for node in named.values())):
                return None

            def column(node):
                if isinstance(node, exp.Column) and node.table in ('', alias) and not (node.db or node.catalog):
                    return named.get(node.name)
                return None

            return inner, column
    if not root.cte_sources and all(isinstance(relation, exp.Table) for _, relation in root.selected_sources.values()):
        return select, lambda node: node if isinstance(node, exp.Column) and not node.is_star else None
    return None


def _numeric_condition(node, column):
    node = node.unnest()
    if isinstance(node, exp.And):
        expressions = (node.this.unnest(), node.expression.unnest())
        for guarded, condition in (expressions, expressions[::-1]):
            target = guarded.this.unnest() if isinstance(guarded, exp.Not) else None
            if (isinstance(target, exp.Is) and isinstance(target.expression, exp.Null)
                    and isinstance(condition, (exp.EQ, exp.GT)) and column(target.this) == column(condition.this)):
                return _numeric_condition(condition, column)
        return None
    if not isinstance(node, (exp.EQ, exp.GT, exp.Is)):
        return None
    value = column(node.this.unnest())
    if value is None:
        return None
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        return 'missing', value
    if node.expression == exp.Literal.number(0):
        return ('zero' if isinstance(node, exp.EQ) else 'positive'), value
    return None


def _conditional_count(expression, column):
    if isinstance(expression, exp.Filter) and isinstance(count := expression.this, exp.Count):
        if isinstance(count.this, exp.Star) and isinstance(expression.expression, exp.Where):
            return _numeric_condition(expression.expression.this, column)
        return None
    if not isinstance(expression, (exp.Sum, exp.Count)) or expression.this is None:
        return None
    case = expression.this.unnest()
    if not isinstance(case, exp.Case):
        return None
    branches = case.args.get('ifs') or []
    if case.this is not None or len(branches) != 1 or branches[0].args.get('true') != exp.Literal.number(1):
        return None
    default = case.args.get('default')
    if (isinstance(expression, exp.Sum) and default != exp.Literal.number(0)
            or isinstance(expression, exp.Count) and default is not None and not isinstance(default, exp.Null)):
        return None
    return _numeric_condition(branches[0].this, column)


def grouped_measure_profile(tree):
    """Describe a single grouped profile without using aliases as metric evidence."""
    allowed = {'expressions', 'from', 'from_', 'joins', 'with', 'with_', 'where', 'group', 'order'}
    if (not isinstance(tree, exp.Select) or not tree.args.get('group')
            or len(tree.expressions) not in range(4, 7) or not any(tree.find_all(exp.Avg))
            or any(value for key, value in tree.args.items() if key not in allowed)):
        return None
    root = next((scope for scope in traverse_scope(tree) if scope.is_root), None)
    if not root or not root.selected_sources or not (binding := _group_input(root)):
        return None
    population, column = binding
    group = tree.args['group']
    if len(group.expressions) != 1 or any(value for name, value in group.args.items() if name != 'expressions'):
        return None
    # The group projection and grouping expression must address the same fact.
    by = group.expressions[0].unnest()
    if by == exp.Literal.number(1):
        by = tree.expressions[0].unalias().unnest()
    if not (key := column(by)):
        return None
    indices, signatures = {}, []
    measures = {}
    for index, item in enumerate(tree.expressions):
        value = item.unalias().unnest()
        if value.find(exp.Subquery, exp.Window, exp.Anonymous):
            return None
        field = value.this.unnest() if isinstance(value, exp.DPipe) and value.expression == exp.Literal.string('') else value
        if column(field) == key:
            role, signature = 'group', key.sql()
        else:
            avg = value.this.unnest() if isinstance(value, exp.Round) else value
            if isinstance(avg, exp.Avg) and (input_field := column(avg.this.unnest())) is not None:
                digits = value.args.get('decimals') if isinstance(value, exp.Round) else None
                if isinstance(value, exp.Round) and not (isinstance(digits, exp.Literal) and digits.is_int):
                    return None
                role, signature = 'average', [input_field.sql(), digits.sql() if digits is not None else None]
                measures[role] = input_field
            elif counted := _conditional_count(value, column):
                role, input_field = counted
                measures[role] = input_field
                signature = input_field.sql()
            elif isinstance(value, exp.Count) and isinstance(value.this, (exp.Star, exp.Literal)):
                if not isinstance(value.this, exp.Star) and value.this != exp.Literal.number(1):
                    return None
                role, signature = 'total', 'rows'
            elif isinstance(value, exp.Count) and isinstance(value.this, exp.Distinct) and len(value.this.expressions) == 1:
                identity = column(value.this.expressions[0])
                if identity is None:
                    return None
                role, signature = 'total', ['distinct',identity.sql()]
            else:
                return None
        if role in indices:
            return None
        indices[role] = index
        signatures.append((role, signature))
    if not {'group','average','zero','missing'} <= indices.keys():
        return None
    if any(field != measures['average'] for field in measures.values()):
        return None
    shape = {name: ([item.sql() for item in node] if isinstance(node,list) else node.sql())
             for name,node in population.args.items() if name in ('from','from_','joins','where') and node}
    return {'schema_version':1,'roles':indices,
            'signature':fingerprint({'scope':shape,'projections':sorted(signatures)})}
