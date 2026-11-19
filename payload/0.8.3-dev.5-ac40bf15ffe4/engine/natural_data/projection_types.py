"""Retain physical types through aliases; a numeric-looking code is not a measure."""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope
from .vendor.dialect import sqlglot_name


def projected_types(sql, dialect, source):
    tables = {t['name'].lower(): t for t in source.get('tables', [])}
    projected = {}
    def physical(ref):
        name='.'.join(p.name for p in ref.parts).lower()
        matches=[t for n,t in tables.items() if n==name or n.endswith('.'+name)]
        return matches[0] if len(matches)==1 else None
    def column_type(node, scope):
        if isinstance(node, (exp.Alias, exp.Paren, exp.Min, exp.Max)):
            return column_type(node.this, scope)
        if isinstance(node, exp.Cast):
            target=node.args['to'].sql()
            # A gateway/text serialization of a known numeric measure does not
            # turn it into an identifier. Preserve codes whose origin is text.
            import re
            origin=column_type(node.this,scope)
            if re.search(r'CHAR|TEXT|STRING',target,re.I) and origin and re.search(r'INT|NUMERIC|DECIMAL|REAL|DOUBLE|FLOAT|NUMBER',origin,re.I):
                return origin
            return target
        if isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Stddev, exp.Variance)):
            return 'NUMERIC'
        # String/array aggregation does not turn codes into quantitative values.
        if isinstance(node, exp.AggFunc):return 'TEXT'
        if not isinstance(node, exp.Column):return None
        refs=[v for k,v in scope.sources.items() if k.lower()==node.table.lower()] if node.table else scope.sources.values()
        types=[]
        for ref in refs:
            if isinstance(ref, Scope):
                value=projected.get(id(ref),{}).get(node.name.lower())
            elif isinstance(ref, exp.Table):
                col=next((c for c in (physical(ref) or {}).get('columns',[]) if c['name'].lower()==node.name.lower()),None)
                value=(col.get('type') or ('TEXT' if col.get('business_values') else None)) if col else None
            else:value=None
            if value:types.append(value)
        return types[0] if len(types)==1 else None
    scopes=list(traverse_scope(sqlglot.parse_one(sql,read=sqlglot_name(dialect))))
    for scope in scopes:
        output={}
        for item in scope.expression.selects:
            if item.is_star:
                refs = [v for k,v in scope.sources.items() if k.lower()==item.table.lower()] if isinstance(item, exp.Column) and item.table else scope.sources.values()
                for ref in refs:
                    if isinstance(ref, Scope):output.update(projected.get(id(ref), {}))
                    elif isinstance(ref, exp.Table):
                        output.update({c['name'].lower():c['type'] for c in (physical(ref) or {}).get('columns', []) if c.get('type')})
            elif item.alias_or_name and (kind:=column_type(item,scope)):
                output[item.alias_or_name.lower()]=kind
        if scope.outer_columns:
            output={alias.lower():output.get(item.alias_or_name.lower()) for alias,item in zip(scope.outer_columns,scope.expression.selects)
                    if output.get(item.alias_or_name.lower())}
        projected[id(scope)]=output
    return projected.get(id(scopes[-1]),{}) if scopes else {}
