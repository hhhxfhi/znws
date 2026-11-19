"""Authorized, bounded read-only database probes for unfamiliar schemas."""
from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from time import monotonic

from sqlglot import exp

from .vendor.dialect import sqlglot_name


def _authorized_table_and_cols(engine, ctx, source, table_name, columns):
    if engine is None or ctx is None or not isinstance(source, dict):
        return None, None, None
    current = engine.source(ctx.project, source['id'])
    if (current.get('connection_revision') != source.get('connection_revision')
            or current.get('business_project') != source.get('business_project')):
        return None, None, None
    requested = str(table_name).casefold()
    matches = [t for t in current.get('tables', [])
               if str(t.get('name', '')).casefold() == requested]
    if not matches and '.' not in requested:
        matches = [t for t in current.get('tables', [])
                   if str(t.get('name', '')).rsplit('.', 1)[-1].casefold() == requested]
    table_meta = matches[0] if len(matches) == 1 else None
    if not table_meta:
        return None, None, None
    col_map = {str(c.get('name', '')).lower(): c['name'] for c in table_meta.get('columns', []) if c.get('name')}
    resolved_cols = []
    for col in columns:
        actual = col_map.get(str(col).lower())
        if not actual:
            return None, None, None
        resolved_cols.append(actual)
    return current, table_meta['name'], resolved_cols


def _run_sqlite_probe(path: Path, table_name: str, allowed_cols: set[str], sql: str, *, timeout: float = 0.25):
    return _run_sqlite_columns_probe(path, {table_name.split('.')[-1]: allowed_cols}, sql, timeout=timeout)


def _run_sqlite_columns_probe(path: Path, table_columns: dict[str, set[str]], sql: str, *, timeout=0.25):
    from .sqlite_connection import connect_readonly

    def authorize(action, target, name, database, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK
        if (action == sqlite3.SQLITE_READ and database == 'main'
                and name in table_columns.get(target, ()) and trigger is None):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    try:
        conn = connect_readonly(path, timeout=timeout)
        try:
            start = monotonic()
            conn.set_authorizer(authorize)
            conn.set_progress_handler(lambda: int(monotonic() - start > timeout), 200)
            return conn.execute(sql).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _run_sa_probe(engine, source, sql: str):
    from sqlalchemy import text
    from .connections import connection_url, read_connection

    try:
        url = connection_url(engine, source)
        with read_connection(source, url) as conn:
            if source.get('dialect') in ('postgresql', 'postgres', 'tbase'):
                conn.exec_driver_sql('SET LOCAL statement_timeout = 250')
            elif source.get('dialect') == 'oracle':
                conn.connection.driver_connection.call_timeout = 250
            return conn.execute(text(sql)).fetchall()
    except Exception:
        return None


def _probe_cache(engine, current, kind: str, table_name: str, cols: tuple[str, ...]):
    if engine is None:
        return None, None
    cache = engine.__dict__.setdefault('_blind_probe_cache', {})
    # Published project metadata is a dict. Freeze it rather than using an
    # unhashable key; expire data observations instead of caching them forever.
    publication = json.dumps(current.get('business_project'), sort_keys=True)
    path = Path(current['path']) if current.get('dialect') == 'sqlite' and current.get('path') else None
    try:
        stat = path.stat() if path else None
        wal = Path(str(path) + '-wal') if path else None
        wal_stat = wal.stat() if wal and wal.exists() else None
        snapshot = ((stat.st_mtime_ns, stat.st_size),
                    (wal_stat.st_mtime_ns, wal_stat.st_size) if wal_stat else None) if stat else None
    except OSError:
        return None, None
    key = (current.get('id'), current.get('connection_revision'), publication, snapshot,
           kind, table_name.lower(), tuple(c.lower() for c in cols))
    now = monotonic()
    for old, entry in list(cache.items()):
        if now - entry[0] > 1:
            cache.pop(old, None)
    if len(cache) >= 256:
        cache.clear()
    return cache, key


def _quoted_table(name, dialect):
    parts = name.split('.')
    table = exp.Table(this=exp.to_identifier(parts[-1], quoted=True))
    if len(parts) > 1:
        table.set('db', exp.to_identifier(parts[-2], quoted=True))
    if len(parts) > 2:
        table.set('catalog', exp.to_identifier(parts[-3], quoted=True))
    return table.sql(dialect=dialect)


def has_duplicate_keys(engine, ctx, source, table_name: str, columns: list[str] | tuple[str, ...],
                       *, require_nonnull=False) -> bool | None:
    """Check duplicate tuples and optionally count a missing key as a violation."""
    current, actual_table, actual_cols = _authorized_table_and_cols(engine, ctx, source, table_name, columns)
    if not current or not actual_table or not actual_cols:
        return None
    cache, key = _probe_cache(engine, current, 'dup-complete' if require_nonnull else 'dup', actual_table, tuple(actual_cols))
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        return cached[1]

    dialect = sqlglot_name(current.get('dialect'))
    tbl_expr = _quoted_table(actual_table, dialect)
    col_exprs = [exp.column(c, quoted=True).sql(dialect=dialect) for c in actual_cols]
    group_by = ', '.join(col_exprs)
    having = 'COUNT(*) > 1'
    where = ''
    if require_nonnull:
        having += ' OR ' + ' OR '.join(f'{c} IS NULL' for c in col_exprs)
    else:
        where = ' WHERE ' + ' AND '.join(f'{c} IS NOT NULL' for c in col_exprs)
    limit = 'FETCH FIRST 1 ROWS ONLY' if current.get('dialect') == 'oracle' or dialect == 'oracle' else 'LIMIT 1'
    sql = f'SELECT 1 FROM {tbl_expr}{where} GROUP BY {group_by} HAVING {having} {limit}'

    if current.get('dialect') == 'sqlite' and current.get('path'):
        rows = _run_sqlite_probe(Path(current['path']), actual_table, set(actual_cols), sql)
    else:
        rows = _run_sa_probe(engine, current, sql)
    if rows is None:
        return None
    result = len(rows) > 0
    if cache is not None:
        cache[key] = (monotonic(), result)
    return result


def distinct_column_values(engine, ctx, source, table_name: str, column_name: str, *, limit: int = 51) -> list[str] | None:
    """Return up to `limit` distinct non-null string values of an authorized column, or None if unavailable."""
    current, actual_table, actual_cols = _authorized_table_and_cols(engine, ctx, source, table_name, [column_name])
    if not current or not actual_table or not actual_cols:
        return None
    actual_col = actual_cols[0]
    cache, key = _probe_cache(engine, current, f'distinct:{limit}', actual_table, (actual_col,))
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        return cached[1]

    dialect = sqlglot_name(current.get('dialect'))
    tbl_expr = _quoted_table(actual_table, dialect)
    col_expr = exp.column(actual_col, quoted=True).sql(dialect=dialect)
    if dialect == 'oracle':
        sql = f'SELECT DISTINCT {col_expr} FROM {tbl_expr} WHERE {col_expr} IS NOT NULL FETCH FIRST {int(limit)} ROWS ONLY'
    else:
        sql = f'SELECT DISTINCT {col_expr} FROM {tbl_expr} WHERE {col_expr} IS NOT NULL LIMIT {int(limit)}'

    if current.get('dialect') == 'sqlite' and current.get('path'):
        rows = _run_sqlite_probe(Path(current['path']), actual_table, {actual_col}, sql)
    else:
        rows = _run_sa_probe(engine, current, sql)
    if rows is None:
        return None
    values = [str(r[0]) for r in rows if r and r[0] is not None]
    if cache is not None:
        cache[key] = (monotonic(), values)
    return values



def sqlite_scalar_additivity(engine, ctx, source, child, parent, child_keys, parent_keys, child_on_left):
    """Count at most two actual matches in both directions using SQLite's equality."""
    if not child_keys or len(child_keys) != len(parent_keys) or len(child_keys) != len(child_on_left):
        return None
    current, child_table, actual_child = _authorized_table_and_cols(engine, ctx, source, child, child_keys)
    parent_current, parent_table, actual_parent = _authorized_table_and_cols(engine, ctx, source, parent, parent_keys)
    if not (current and parent_current and child_table and parent_table and current.get('dialect') == 'sqlite'
            and current.get('path') and current['path'] == parent_current.get('path')):
        return None
    pairs = tuple(zip(actual_child, actual_parent, child_on_left))
    cache, key = _probe_cache(engine, current, 'scalar_matches', json.dumps([child_table,parent_table]),
                              tuple(json.dumps(pair) for pair in pairs))
    cached = cache.get(key) if cache is not None else None
    if cached is not None:
        return cached[1]

    def col(name, alias):
        return exp.column(name, table=alias, quoted=True).sql(dialect='sqlite')

    child_from = f'{_quoted_table(child_table, "sqlite")} AS "_child"'
    parent_from = f'{_quoted_table(parent_table, "sqlite")} AS "_parent"'
    conditions = []
    for inner_name, outer_name, inner_first in pairs:
        l, r = col(inner_name, '_child'), col(outer_name, '_parent')
        conditions.append(f'{l} = {r}' if inner_first else f'{r} = {l}')
    predicate = ' AND '.join(conditions)
    sql = (f'SELECT EXISTS(SELECT 1 FROM {parent_from} WHERE EXISTS('
           f'SELECT 1 FROM {child_from} WHERE {predicate} LIMIT 1 OFFSET 1)), '
           f'EXISTS(SELECT 1 FROM {child_from} WHERE EXISTS('
           f'SELECT 1 FROM {parent_from} WHERE {predicate} LIMIT 1 OFFSET 1))')
    allowed = {}
    allowed.setdefault(child_table.split('.')[-1], set()).update(actual_child)
    allowed.setdefault(parent_table.split('.')[-1], set()).update(actual_parent)
    rows = _run_sqlite_columns_probe(Path(current['path']), allowed, sql)
    if not rows or len(rows) != 1 or len(rows[0]) != 2 or any(value not in (0,1) for value in rows[0]):
        return None
    observation = tuple(not value for value in rows[0])
    if cache is not None:
        cache[key] = (monotonic(), observation)
    return observation
