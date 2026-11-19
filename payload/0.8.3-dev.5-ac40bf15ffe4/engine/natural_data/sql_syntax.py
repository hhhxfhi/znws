"""Actionable SQL syntax feedback before domain checks parse the query."""
import sqlglot
from sqlglot.errors import SqlglotError


def validate_sql_syntax(sql, dialect):
    # This is not authorization. The normal read-only/table/function policy
    # still validates the complete query before it can reach a database.
    try:
        sqlglot.parse(sql, read=dialect)
    except SqlglotError as error:
        details = getattr(error, 'errors', [])
        first = details[0] if details else {}
        line, column = first.get('line'), first.get('col')
        location = f'（第{line}行、第{column}列附近）' if isinstance(line, int) and isinstance(column, int) else ''
        # Parser diagnostics include literal values and SQL fragments. Only
        # expose a location and static repair guidance, never the raw error.
        raise ValueError('SQL_SYNTAX: SQL语法无法解析' + location + '。请检查列别名、括号和逗号；'
                         '含括号、空格或单位的别名应加标识符引号，例如 PostgreSQL/SQLite 使用双引号 '
                         'AS "有效证书数(本)"，MySQL 使用反引号。修正后再查询，不要重复原SQL。') from None
