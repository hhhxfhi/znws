"""Apply the row budget in SQL, before a DBAPI driver can buffer the result."""
import hashlib
from sqlglot import exp, parse_one
from decimal import Decimal, InvalidOperation
from .vendor.dialect import sqlglot_name


def execution_basis(policy_sql, executed_sql):
    """Record which validated SQL a stored row-limited statement executed."""
    digest = lambda text: hashlib.sha256(text.encode('utf-8')).hexdigest()
    return {'policy_sql': policy_sql, 'policy_sql_sha256': digest(policy_sql), 'executed_sql_sha256': digest(executed_sql)}


def bounded_query(sql, db_type, limit, parameters):
    name = sqlglot_name(db_type)
    tree = parse_one(sql, read=name)
    cap = min(500, max(1, int(limit))) + 1  # one extra row proves truncation
    bounded = exp.Literal.number(cap)
    existing = tree.args.get('limit')
    if existing is not None:
        options = existing.args.get('limit_options')
        if options and (options.args.get('percent') or options.args.get('with_ties')):
            raise ValueError('ROW_LIMIT: 请使用固定行数的 LIMIT/FETCH，不支持百分比或 WITH TIES。')
        count = existing.args.get('expression') or existing.args.get('count')
        while isinstance(count, exp.Paren): count = count.this
        if count is None and isinstance(existing, exp.Fetch): count = exp.Literal.number(1)
        unlimited = name=='postgres' and (isinstance(count, exp.Null) or
            isinstance(count, (exp.Var,exp.Column)) and count.name.upper()=='ALL')
        if unlimited: return tree.limit(cap).sql(dialect=name)
        if isinstance(count, exp.Literal) and not count.is_string or isinstance(count, exp.Neg) and isinstance(count.this,exp.Literal):
            try:
                number = Decimal(count.sql())
                value = (cap if number>=cap else -1 if number<0 else int(number)) if number.is_finite() and number==number.to_integral_value() else None
            except InvalidOperation: value = None
        elif isinstance(count, exp.Placeholder):
            value = parameters.get(count.name)
        else:
            value = None
        if type(value) is int:
            if value >= 0: bounded = exp.Literal.number(min(cap,value))
            elif db_type!='sqlite': raise ValueError('ROW_LIMIT: 查询行数不能为负数。')
        elif name in {'postgres','oracle','sqlite'} and count is not None:
            # Let the database evaluate legal expressions/subqueries with their
            # original parameter types. Do not approximate them in Python.
            bounded = exp.Least(this=exp.Literal.number(cap),expressions=[count.copy()])
            if name=='sqlite':
                # Use SQLite's LIMIT conversion itself: MIN(integer, text) uses
                # storage-class ordering and CAST would silently accept invalid
                # text. Count a bounded synthetic sequence limited by the exact
                # original expression (evaluated once). NULL/fractional/invalid
                # counts retain SQLite's error, and any negative count is capped.
                bounded = exp.Subquery(this=parse_one(
                    'SELECT COUNT(*) FROM (WITH RECURSIVE __product_limit_rows AS '
                    '(SELECT 1 AS n UNION ALL SELECT n+1 FROM __product_limit_rows WHERE n < '+str(cap)+') '
                    'SELECT n FROM __product_limit_rows LIMIT '+count.sql(dialect='sqlite')+')',read='sqlite'))
        else:
            raise ValueError('ROW_LIMIT: 此数据库的查询行数必须是整数或整数绑定参数。')
    # Replace only the outer query limit; ORDER BY, CTEs, unions, offsets and
    # duplicate output names keep their original meaning (no SELECT * wrapper).
    return tree.limit(bounded).sql(dialect=name)
