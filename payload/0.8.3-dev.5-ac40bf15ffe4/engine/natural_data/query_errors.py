"""Classify SQL execution errors using native diagnostics and local control state."""
from __future__ import annotations

import sqlite3


_POSTGRES_DIALECTS = {'postgresql', 'postgis', 'tbase', 'kingbase'}


def classify_query_failure(error, *, dialect, cancelled, executing, sqlite_timed_out=False):
    if cancelled:
        return 'CANCELLED'
    if not executing:
        return 'EXECUTION_ERROR'

    if dialect in _POSTGRES_DIALECTS:
        diag = getattr(error, 'diag', None)
        state = getattr(diag, 'sqlstate', None) or getattr(error, 'pgcode', None) or getattr(error, 'sqlstate', None)
        if state in {'42703', '42P01'}:
            return 'SCHEMA_MISMATCH'
        if state == '57014':
            message = (getattr(diag, 'message_primary', None) or str(error)).strip().casefold()
            if message == 'canceling statement due to statement timeout':
                return 'TIMEOUT'
            if message == 'canceling statement due to user request':
                return 'CANCELLED'

    if dialect == 'sqlite' and isinstance(error, sqlite3.Error):
        code = getattr(error, 'sqlite_errorcode', None)
        if sqlite_timed_out and code == sqlite3.SQLITE_INTERRUPT:
            return 'TIMEOUT'
        if str(error).lower().startswith(('no such column:', 'no such table:')):
            return 'SCHEMA_MISMATCH'

    if dialect == 'mysql' and error.args:
        code = error.args[0]
        if type(code) is int:
            if code in {1051, 1054, 1146}:
                return 'SCHEMA_MISMATCH'
            if code in {1969, 3024}:
                return 'TIMEOUT'
            if code == 1317:
                return 'CANCELLED'

    if dialect == 'oracle' and error.args:
        code = getattr(error.args[0], 'code', None)
        if isinstance(code, int) and code in {904, 942}:
            return 'SCHEMA_MISMATCH'
        if code == 1013:
            return 'CANCELLED'

    return 'EXECUTION_ERROR'
