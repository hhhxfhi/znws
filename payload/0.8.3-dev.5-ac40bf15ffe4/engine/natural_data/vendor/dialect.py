"""Database dialect abstraction for read-only query execution.

The product executes only validated SELECT/CTE statements, but the connection
URL, SQLGlot dialect, read-only guard, timeout guard, metadata projection query,
identifier quoting and version probe all differ between engines.  This module
isolates those concerns behind a single ``SqlDialect`` object so that
PostgreSQL/PostGIS remains the reference implementation and MySQL (and future
engines) can be added without touching the execution or policy code.

Spatial (PostGIS) execution stays PostgreSQL-only; a ``mysql`` datasource is
therefore limited to ordinary SQL querying (no GIS layers).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import URL

# --- 显式 search_path（2026-09-15 全维度审查 P2） ---------------------------
# SQL 白名单允许省略 schema 的裸表名（由数据库按 search_path 解析），因此运行库
# 的 search_path 必须是**确定的**：否则一个与白名单同名的表出现在 search_path
# 更靠前的 schema 时，解析结果会随连接/角色配置漂移，出现「查询成功但读了别的
# 表」。默认 None = 保持驱动/角色的既有行为（不改变现有可用性）；生产部署建议
# 显式固定，例如 "natural_resources,public"。
_SEARCH_PATH_OVERRIDE: Optional[str] = None
_SEARCH_PATH_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def set_search_path_override(value: Optional[str]) -> None:
    """设置（或清除）PostgreSQL 会话 search_path。

    只接受合法的 schema 标识符，非法输入整体忽略并保留原值——这个字符串会进
    ``SET search_path`` 语句，绝不允许拼接任意内容。
    """

    global _SEARCH_PATH_OVERRIDE
    parts: List[str] = []
    for part in str(value or "").split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        if not _SEARCH_PATH_IDENT.match(cleaned):
            return
        parts.append(cleaned)
    _SEARCH_PATH_OVERRIDE = ",".join(parts) or None


def search_path_override() -> Optional[str]:
    return _SEARCH_PATH_OVERRIDE


class SqlDialect:
    """Per-engine constants and helpers used by the executor and policy layer."""

    #: Product ``db_type`` value this dialect maps to.
    key: str = ""
    #: SQLAlchemy driver URL scheme.
    driver: str = ""
    #: SQLGlot dialect name (``read`` / ``dialect``).
    sqlglot: str = ""
    #: Identifier quote character used by the engine (for stripping quotes).
    quote_char: str = '"'
    #: Extra ``create_engine(connect_args=...)``.
    connect_args: Dict[str, Any] = {}
    #: Default relation schema when a bare table name is given.
    default_schema: str = "public"
    #: Whether the engine supports GIS layers (PostGIS).
    supports_gis: bool = False

    def engine_url(
        self,
        username: str,
        password: str,
        host: str,
        port: int,
        database: str,
    ) -> URL:
        return URL.create(
            self.driver,
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
        )

    def session_setup(self, timeout_seconds: int) -> List[str]:
        """SQL executed on the connection *before* ``begin()`` (session scope)."""
        return []

    def transaction_setup(self, timeout_seconds: int) -> List[str]:
        """SQL executed *after* ``begin()`` to harden the read-only transaction."""
        return []

    def columns_sql(self) -> str:
        """Query projecting column metadata for one ``schema.table`` relation."""
        raise NotImplementedError

    def constraints_sql(self) -> str:
        """Query projecting PK / FK / unique constraint rows for one relation."""
        raise NotImplementedError

    def table_comment_sql(self) -> str:
        """Query the table-level comment for one ``schema.table`` relation."""
        raise NotImplementedError

    def enums_sql(self) -> str:
        """Query ``(type_name, enum_label)`` pairs for a whole schema.

        Returns an empty string when the engine has no catalog-level enum
        support (e.g. MySQL exposes enum labels inside ``column_type`` instead).
        """
        return ""

    def database_type(self, column_row: Dict[str, Any]) -> str:
        """Extract the engine-specific type label from a columns row."""
        raise NotImplementedError

    def version_sql(self) -> str:
        """Probe returning ``(database, user)`` for the connectivity test."""
        raise NotImplementedError

    def quote(self, identifier: str) -> str:
        raise NotImplementedError


class PostgresDialect(SqlDialect):
    key = "postgresql"
    driver = "postgresql+psycopg2"
    sqlglot = "postgres"
    quote_char = '"'
    connect_args = {"connect_timeout": 8}
    default_schema = "public"
    supports_gis = True

    def session_setup(self, timeout_seconds: int) -> List[str]:
        # Connections pins the session's authorized schema for each transaction.
        # This optional setup is used only when a caller supplies an override.
        if not _SEARCH_PATH_OVERRIDE:
            return []
        return [f"SET search_path = {_SEARCH_PATH_OVERRIDE}"]

    def transaction_setup(self, timeout_seconds: int) -> List[str]:
        return [
            "SET TRANSACTION READ ONLY",
            f"SET LOCAL statement_timeout = '{timeout_seconds * 1000}ms'",
            # statement_timeout bounds one statement; the idle guard stops a
            # stalled worker from holding a read transaction and its locks forever.
            f"SET LOCAL idle_in_transaction_session_timeout = '{(timeout_seconds + 30) * 1000}ms'",
        ]

    def columns_sql(self) -> str:
        # col_description() reads the comment of a column; schema/table names are
        # bound as parameters and never interpolated into the statement.
        return (
            "SELECT c.column_name, c.data_type, c.udt_name, c.is_nullable, "
            "c.ordinal_position, "
            "col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, "
            "c.ordinal_position) AS column_comment "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = :schema_name AND c.table_name = :table_name "
            "ORDER BY c.ordinal_position"
        )

    def constraints_sql(self) -> str:
        # PK / FK / UNIQUE rows joined with the referenced (unique) constraint so
        # each foreign key carries its target table and column.
        return (
            "SELECT tc.constraint_type, tc.constraint_name, kcu.column_name, "
            "kcu.ordinal_position, rcu.table_schema AS ref_schema, "
            "rcu.table_name AS ref_table, rcu.column_name AS ref_column "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema AND tc.table_name = kcu.table_name "
            "LEFT JOIN information_schema.referential_constraints rc "
            "ON tc.constraint_name = rc.constraint_name "
            "AND tc.table_schema = rc.constraint_schema "
            "LEFT JOIN information_schema.key_column_usage rcu "
            "ON rc.unique_constraint_name = rcu.constraint_name "
            "AND rc.unique_constraint_schema = rcu.constraint_schema "
            "AND kcu.position_in_unique_constraint = rcu.ordinal_position "
            "WHERE tc.table_schema = :schema_name AND tc.table_name = :table_name "
            "AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE') "
            "ORDER BY tc.constraint_name, kcu.ordinal_position"
        )

    def table_comment_sql(self) -> str:
        return (
            "SELECT obj_description(format('%I.%I', :schema_name, :table_name)::regclass) "
            "AS table_comment"
        )

    def enums_sql(self) -> str:
        return (
            "SELECT t.typname AS type_name, e.enumlabel AS enum_label "
            "FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = :schema_name "
            "ORDER BY t.typname, e.enumsortorder"
        )

    def database_type(self, column_row: Dict[str, Any]) -> str:
        return str(column_row["udt_name"])

    def version_sql(self) -> str:
        return "SELECT current_database(), current_user"

    def quote(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'


class TBaseDialect(PostgresDialect):
    """TBASE (Tencent TDSQL-PG) — a PostgreSQL-compatible distributed database.

    TBASE exposes the PostgreSQL wire protocol plus ``information_schema`` /
    ``pg_catalog``, so the Postgres dialect's driver, metadata queries, quoting
    and read-only guards apply unchanged.

    Spatial (PostGIS) support is off by default because the stock TBASE image
    does not ship the PostGIS extension; flip ``supports_gis`` to ``True`` only
    when the target instance has PostGIS installed.
    """

    key = "tbase"
    supports_gis = False


class MySqlDialect(SqlDialect):
    key = "mysql"
    driver = "mysql+pymysql"
    sqlglot = "mysql"
    quote_char = "`"
    connect_args = {"connect_timeout": 8, "charset": "utf8mb4"}
    default_schema = ""  # MySQL uses the database name as the schema
    supports_gis = False

    def session_setup(self, timeout_seconds: int) -> List[str]:
        return [
            "SET SESSION TRANSACTION READ ONLY",
            f"SET SESSION MAX_EXECUTION_TIME = {timeout_seconds * 1000}",
        ]

    def transaction_setup(self, timeout_seconds: int) -> List[str]:
        # Re-asserted inside the transaction: a pooled connection may have had its
        # session state reset, and without this a runaway SELECT is unbounded.
        return [f"SET SESSION MAX_EXECUTION_TIME = {timeout_seconds * 1000}"]

    def columns_sql(self) -> str:
        return (
            "SELECT column_name, data_type, column_type, is_nullable, ordinal_position, "
            "column_comment "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema_name AND table_name = :table_name "
            "ORDER BY ordinal_position"
        )

    def constraints_sql(self) -> str:
        return (
            "SELECT tc.constraint_type, tc.constraint_name, kcu.column_name, "
            "kcu.ordinal_position, kcu.referenced_table_schema AS ref_schema, "
            "kcu.referenced_table_name AS ref_table, "
            "kcu.referenced_column_name AS ref_column "
            "FROM information_schema.table_constraints tc "
            "LEFT JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema AND tc.table_name = kcu.table_name "
            "WHERE tc.table_schema = :schema_name AND tc.table_name = :table_name "
            "AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY', 'UNIQUE') "
            "ORDER BY tc.constraint_name, kcu.ordinal_position"
        )

    def table_comment_sql(self) -> str:
        return (
            "SELECT table_comment FROM information_schema.tables "
            "WHERE table_schema = :schema_name AND table_name = :table_name"
        )

    def database_type(self, column_row: Dict[str, Any]) -> str:
        return str(column_row["column_type"])

    def version_sql(self) -> str:
        return "SELECT DATABASE(), CURRENT_USER()"

    def quote(self, identifier: str) -> str:
        return '`' + identifier.replace('`', '``') + '`'


_DIALECTS: Dict[str, SqlDialect] = {
    "postgresql": PostgresDialect(),
    "postgis": PostgresDialect(),
    "tbase": TBaseDialect(),
    "mysql": MySqlDialect(),
}


from ..additional_databases import register as _register_additional
_DIALECTS.update(_register_additional(SqlDialect, PostgresDialect))


def get_dialect(db_type: str) -> SqlDialect | None:
    """Return the dialect for a product ``db_type``, or ``None`` if unsupported."""
    return _DIALECTS.get((db_type or "").lower())


def sqlglot_name(db_type: str) -> str:
    adapter = get_dialect(db_type)
    return adapter.sqlglot if adapter else db_type


def default_schema_for(dialect: SqlDialect, database_name: str) -> str:
    """Return the effective schema for a bare relation name."""
    return dialect.default_schema or database_name
