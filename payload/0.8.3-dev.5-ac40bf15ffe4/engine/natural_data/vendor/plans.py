"""Untrusted plan contracts and policy validation for SQL and vector GIS."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .dialect import get_dialect
from .log import get_logger, param_names, safe, sql_digest

logger = get_logger("plans")

GIS_OPERATIONS = {
    "FEATURE_SEARCH",
    "NEAREST",
    "WITHIN_DISTANCE",
    "WITHIN",
    "CONTAINS",
    "INTERSECTS",
    "BUFFER",
    "SPATIAL_JOIN",
    "AGGREGATE_BY_REGION",
    "MEASURE",
    "MAP_LAYER",
}
MAP_MODES = {"lonlat", "geojson", "vector-tile"}
# 系统目录清单要覆盖到能被裸名（依赖 search_path）访问到的那些，
# 否则关系白名单一旦放宽就会直接暴露元数据与账号信息。
FORBIDDEN_SYSTEM_RELATION = re.compile(
    r"\b(pg_catalog|information_schema|pg_toast|pg_authid|pg_shadow"
    r"|pg_class|pg_proc|pg_namespace|pg_user|pg_roles|pg_database|pg_settings"
    r"|pg_stat_activity|pg_stat_user_tables|pg_stat_user_indexes|pg_indexes"
    r"|pg_tables|pg_views|pg_matviews|pg_sequences|pg_constraint|pg_attribute"
    r"|pg_type|pg_enum|pg_extension|pg_statistic|pg_trigger|pg_rewrite)\b",
    re.IGNORECASE,
)
FORBIDDEN_GIS_TEXT = re.compile(
    r"(?i)(\bselect\b|\binsert\b|\bupdate\b|\bdelete\b|\bst_[a-z0-9_]+|\bsrid\b|postgres(?:ql)?://|https?://|jdbc:)",
)
# Keyword blacklists can never be exhaustive, so privileged side-channel functions are
# denied explicitly. A read-only transaction does NOT stop pg_read_file / dblink / pg_sleep.
FORBIDDEN_SQL_FUNCTIONS = frozenset(
    {
        # server file system access
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_logdir_ls",
        # 目录列举（PG 9.6+/10+ 的 ls 家族）
        "pg_ls_waldir",
        "pg_ls_logdir",
        "pg_ls_tmpdir",
        "pg_ls_archive_statusdir",
        "pg_filenode_relation",
        # outbound connections
        "dblink",
        "dblink_exec",
        "dblink_open",
        "dblink_close",
        "dblink_get_connections",
        # denial of service
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        # denial of service —— MySQL/MariaDB：SLEEP() 不受 MAX_EXECUTION_TIME 约束，
        # 只读事务也拦不住，单个请求就能长占一个 worker（2026-09-15 审查 P1）。
        "sleep",
        "benchmark",
        # 命名锁：可被用来跨会话互相阻塞
        "get_lock",
        "release_lock",
        "is_free_lock",
        "is_used_lock",
        "master_pos_wait",
        # UDF / 扩展侧信道（自建 UDF 常见于 sys_exec / sys_eval）
        "sys_exec",
        "sys_eval",
        # SQLite 扩展文件读写（本地兜底数据源）
        "load_extension",
        "readfile",
        "writefile",
        # session / backend control
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_create_restore_point",
        "pg_switch_wal",
        "pg_backup_start",
        "pg_backup_stop",
        # configuration mutation
        "set_config",
        # arbitrary query execution via XML
        "query_to_xml",
        "query_to_xmlschema",
        "cursor_to_xml",
        # large objects
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "loread",
        "lowrite",
        # advisory locks can block other sessions
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_try_advisory_lock",
        # MySQL server-side file read / write（只读事务挡不住 LOAD_FILE）
        "load_file",
        "load_data",
        "load_xml",
        # PostgreSQL 通知 / 监听侧信道
        "pg_notify",
    }
)

# ---------------------------------------------------------------------------
# 函数白名单（2026-09-23 根治「黑名单可被等价拼写绕过」）
# ---------------------------------------------------------------------------
# 黑名单的本质缺陷是「枚举坏名字」——而同一个函数有无限多种等价拼写
# （引号、U& 转义、schema 限定……），永远补不完。白名单反过来：只放行认识的名字。
#
# 关键设计（决定误杀率）：
#   只校验 sqlglot 的 **exp.Anonymous** 节点。
#   sqlglot 认识的标准语法会被解析成专用节点（COUNT→exp.Count、
#   CAST→exp.Cast、EXTRACT→exp.Extract、ROW_NUMBER→exp.RowNumber、
#   CASE→exp.Case 等），这些是 SQL 语法本身的构造，天然只读，不需要也不应该
#   逐个登记——否则每加一个方言关键字都要改白名单。
#   只有 sqlglot **不认识**的函数才落到 Anonymous（如 ST_Area、RANK、
#   PERCENTILE_CONT），这也正是唯一存在「任意函数名」风险的地方。
#
# 列表范围 = 本产品承诺的分析能力所需函数：
#   标准聚合 / 统计 / 窗口 / 字符串 / 时间 / 数学 / 类型转换 / JSON / 数组 /
#   PostGIS 空间算子（分析矩阵的 ⑦ 可视化与 ⑤ 框架依赖它们）。
# 缺少某个函数会在策略层被拦成明确报错（报错里带函数名），便于按需补充。
ALLOWED_SQL_FUNCTIONS = frozenset(
    {
        # --- 标准聚合与统计 ---
        "count", "sum", "avg", "min", "max", "array_agg", "string_agg",
        "stddev", "stddev_pop", "stddev_samp", "variance", "var_pop", "var_samp",
        "corr", "covar_pop", "covar_samp", "regr_slope", "regr_intercept",
        "regr_r2", "regr_count", "percentile_cont", "percentile_disc",
        "mode", "bool_and", "bool_or", "every", "bit_and", "bit_or",
        "json_agg", "jsonb_agg", "json_object_agg", "jsonb_object_agg",
        # --- 排名 / 窗口（sqlglot 未识别时落 Anonymous）---
        "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
        "row_number", "lag", "lead", "first_value", "last_value", "nth_value",
        "width_bucket",
        # --- 字符串 ---
        "concat", "concat_ws", "lower", "upper", "initcap", "length", "char_length",
        "character_length", "octet_length", "bit_length", "trim", "btrim", "ltrim",
        "rtrim", "lpad", "rpad", "substr", "substring", "left", "right", "replace",
        "translate", "position", "strpos", "split_part", "regexp_replace",
        "regexp_match", "regexp_matches", "regexp_split_to_array", "regexp_split_to_table",
        "repeat", "reverse", "md5", "to_hex", "quote_ident", "quote_literal",
        "starts_with", "format", "chr", "ascii", "encode", "decode",
        # --- 时间 ---
        "date_trunc", "date_part", "extract", "age", "now", "current_date",
        "current_time", "current_timestamp", "localtimestamp", "clock_timestamp",
        "statement_timestamp", "transaction_timestamp", "to_char", "to_date",
        "to_timestamp", "to_number", "make_date", "make_time", "make_timestamp",
        "make_interval", "justify_days", "justify_hours", "justify_interval",
        "date_bin", "timezone", "isfinite",
        # --- 数学 ---
        "abs", "ceil", "ceiling", "floor", "round", "trunc", "sign", "mod", "power",
        "pow", "sqrt", "cbrt", "exp", "ln", "log", "log10", "greatest", "least",
        "random", "pi", "degrees", "radians", "div", "gcd", "lcm", "factorial",
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
        # --- 类型 / NULL ---
        "cast", "coalesce", "nullif", "to_int", "to_float", "to_decimal",
        # --- JSON ---
        "json_each", "json_tree", "json_quote",
        "json_build_object", "jsonb_build_object", "json_build_array",
        "jsonb_build_array", "json_extract_path", "jsonb_extract_path",
        "json_extract_path_text", "jsonb_extract_path_text", "json_array_length",
        "jsonb_array_length", "json_object_keys", "jsonb_object_keys",
        "json_typeof", "jsonb_typeof", "to_json", "to_jsonb", "jsonb_set",
        "jsonb_insert", "jsonb_pretty", "row_to_json",
        # --- 数组 ---
        "unnest", "array_length", "array_position", "array_remove", "array_cat",
        "array_append", "array_prepend", "array_to_string", "string_to_array",
        "generate_series", "generate_subscripts", "cardinality", "array_dims",
        # --- PostGIS 空间（分析矩阵空间类算子依赖）---
        "st_area", "st_length", "st_distance", "st_perimeter", "st_centroid",
        "st_x", "st_y", "st_astext", "st_asgeojson", "st_geomfromtext",
        "st_geometrytype", "st_srid", "st_transform", "st_buffer", "st_contains",
        "st_within", "st_intersects", "st_dwithin", "st_intersection",
        "st_union", "st_difference", "st_symdifference", "st_envelope",
        "st_extent", "st_makevalid", "st_isvalid", "st_simplify", "st_buffer",
        "st_numgeometries", "st_geometryn", "st_pointonsurface", "st_boundary",
        "st_convexhull", "st_closestpoint", "st_lineinterpolatepoint",
        "st_line_locate_point", "st_azimuth", "st_clusterdbscan",
        "st_clusterkmeans", "st_snaptogrid", "st_flipcoordinates",
        "st_force2d", "st_collect", "st_multi", "st_dump", "st_dumpoints",
    }
)

#: 运行期可放行的额外函数（运维紧急放行用，逗号分隔，大小写不敏感）。
#: 存在的意义：白名单误杀时不必改动代码重新发版。
ALLOWED_FUNCTION_OVERRIDE_ENV = "PRODUCT_SQL_EXTRA_FUNCTIONS"
# Matches :name bind placeholders but skips the PostgreSQL :: cast operator.
SQL_PLACEHOLDER = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")
# Crude single-quoted literal strip, used only so that placeholders inside string
# literals are not mistaken for bind parameters.
SQL_STRING_LITERAL = re.compile(r"'[^']*'")


class TypedParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
    type: Literal["string", "integer", "number", "boolean", "date", "datetime"]
    value: Any


class AlertRule(BaseModel):
    """A single threshold rule used to flag abnormal / exceptional rows in a table."""

    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=128)
    op: Literal[">", ">=", "<", "<=", "==", "!="]
    threshold: float
    severity: Literal["warn", "error"] = "error"


class ChartIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "table",
        "scalar",
        "kpi",
        "line",
        "area",
        "bar",
        "column",
        "grouped-bar",
        "stacked-bar",
        "percent-stacked-bar",
        "pie",
        "donut",
        "scatter",
        "bubble",
        "alert-table",
        "map",
    ]
    title: str = Field(max_length=160)
    map_mode: Optional[Literal["lonlat", "geojson", "vector-tile"]] = None
    alert_rules: Optional[List[AlertRule]] = None

    @model_validator(mode="after")
    def validate_map_mode(self):
        if self.type == "map" and self.map_mode not in MAP_MODES:
            raise ValueError("map output requires an approved map mode")
        if self.type != "map" and self.map_mode is not None:
            raise ValueError("map_mode is only valid for map output")
        return self


class AnalysisIntent(BaseModel):
    """①~④ analysis routing judged by the planner; template recall is ⑤.

    Optional and never enforced at plan time (a legacy plan without it stays
    valid); the product service resolves it deterministically via the analysis
    capability matrix.
    """

    model_config = ConfigDict(extra="forbid")
    category: Optional[str] = Field(default=None, max_length=32)
    intent: Optional[str] = Field(default=None, max_length=32)
    primary_method: Optional[str] = Field(default=None, max_length=64)
    sub_method: Optional[str] = Field(default=None, max_length=64)
    metric: Optional[str] = Field(default=None, max_length=128)
    dimensions: List[str] = Field(default_factory=list, max_length=20)
    time_scope: Optional[str] = Field(default=None, max_length=128)
    comparison_type: Optional[Literal["yoy", "mom", "qoq", "wow", "custom"]] = None


class SQLPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["sql"]
    plan_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    datasource_ref: str = Field(min_length=1, max_length=128)
    logical_sql: str = Field(min_length=1, max_length=100_000)
    parameters: List[TypedParameter] = Field(default_factory=list, max_length=100)
    evidence: List[str] = Field(default_factory=list, max_length=50)
    expected_chart: ChartIntent
    analysis: Optional[AnalysisIntent] = None
    limit: int = Field(default=500, ge=1, le=500)


class SpatialObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["POINT", "BBOX", "GAZETTEER", "FEATURE"]
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    bbox: Optional[List[float]] = Field(default=None, min_length=4, max_length=4)
    ref: Optional[str] = Field(default=None, max_length=256)


class SpatialPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["spatial"]
    plan_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    operation: Literal[
        "FEATURE_SEARCH",
        "NEAREST",
        "WITHIN_DISTANCE",
        "WITHIN",
        "CONTAINS",
        "INTERSECTS",
        "BUFFER",
        "SPATIAL_JOIN",
        "AGGREGATE_BY_REGION",
        "MEASURE",
        "MAP_LAYER",
    ]
    primary_layer_ref: str = Field(min_length=1, max_length=128)
    secondary_layer_ref: Optional[str] = Field(default=None, max_length=128)
    spatial_object: Optional[SpatialObject] = None
    filters: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    distance_m: Optional[float] = Field(default=None, gt=0, le=50_000)
    evidence: List[str] = Field(default_factory=list, max_length=50)
    expected_chart: ChartIntent
    analysis: Optional[AnalysisIntent] = None
    limit: int = Field(default=500, ge=1, le=500)

    @model_validator(mode="after")
    def validate_operation_inputs(self):
        if self.operation in {"NEAREST", "WITHIN_DISTANCE", "BUFFER"}:
            if self.spatial_object is None or self.distance_m is None:
                raise ValueError(f"{self.operation} requires object and distance")
        if self.operation in {
            "WITHIN",
            "CONTAINS",
            "INTERSECTS",
            "SPATIAL_JOIN",
            "AGGREGATE_BY_REGION",
        } and not self.secondary_layer_ref:
            raise ValueError(f"{self.operation} requires secondary_layer_ref")
        if self.expected_chart.type != "map":
            raise ValueError("spatial plans require a map result")
        if FORBIDDEN_GIS_TEXT.search(self.model_dump_json()):
            raise ValueError("spatial plans cannot contain SQL, ST_ text, SRID, or URLs")
        return self


UntrustedQueryPlanV3 = Union[SQLPlan, SpatialPlan]
PLAN_ADAPTER = TypeAdapter(UntrustedQueryPlanV3)


class PolicyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    allowed_datasource_refs: List[str] = Field(default_factory=list)
    allowed_layer_refs: List[str] = Field(default_factory=list)
    allowed_relations: List[str] = Field(default_factory=list)
    db_types: Dict[str, str] = Field(default_factory=dict)
    relation_schema: Optional[str] = None


class ValidatedExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str
    mode: Literal["sql", "spatial"]
    workspace_id: str
    compiled_hash: str
    row_budget: int = 500
    byte_budget: int = 5 * 1024 * 1024
    timeout_seconds: int = 30
    sanitized_plan: Dict[str, Any]


class PlanRejected(ValueError):
    pass


def _strip_string_literals(sql: str) -> str:
    """Blank out single-quoted literals so inner ':' is not read as a placeholder."""

    return SQL_STRING_LITERAL.sub("''", sql)


def _has_sql_comment(sql: str, *, hash_comments: bool) -> bool:
    """Find comment openers outside quoted strings and quoted identifiers.

    Quote handling differs by dialect (backslash escapes, double quotes as
    strings or identifiers). Scan with and without backslash escapes and treat
    a comment found by either reading as a comment, so a quote the database
    reads differently cannot hide `/*! ... */` (MySQL executable comments).
    """

    for backslash in (False, True):
        quote = None
        i = 0
        while i < len(sql):
            ch = sql[i]
            if quote:
                if backslash and ch == "\\" and quote in "'\"":
                    i += 2
                    continue
                if ch == quote:
                    if sql[i + 1:i + 2] == quote:
                        i += 2
                        continue
                    quote = None
            elif ch in "'\"`":
                quote = ch
            elif ch == "[":
                quote = "]"
            elif sql.startswith("--", i) or sql.startswith("/*", i) or (hash_comments and ch == "#"):
                return True
            i += 1
        if quote:
            # Unterminated quote: the database may read the rest as SQL.
            rest = sql[sql.rfind(quote):]
            if "--" in rest or "/*" in rest or (hash_comments and "#" in rest):
                return True
    return False


_PG_UNICODE_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{4})|\+([0-9a-fA-F]{6}))")
_PG_ESCAPE_CHARS = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def _decode_pg_unicode_escape(text: str) -> str:
    """Decode PostgreSQL ``U&'...'`` identifier escapes into literal characters.

    PostgreSQL folds ``U&"pg\\005fread_file"`` to the identifier ``pg_read_file``, so
    the policy engine must fold it the same way before comparing against the ban
    list -- otherwise the quoted/escaped spelling of a forbidden function is a free
    pass. Other backslash forms are decoded too (octal, hex, ``\\\\`` and the C-style
    single character escapes) so no spelling survives into the comparison.
    """

    if "\\" not in text:
        return text
    out: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "\\" or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = text[index + 1]
        # \XXXX (4 hex) or \+XXXXXX (6 hex) -> one Unicode code point
        match = _PG_UNICODE_ESCAPE.match(text, index)
        if match:
            codepoint = int(match.group(1) or match.group(2), 16)
            if codepoint <= 0x10FFFF:
                out.append(chr(codepoint))
            index = match.end()
            continue
        # \ooo (1-3 octal digits) -> one byte
        octal = re.match(r"\\([0-7]{1,3})", text[index:])
        if octal:
            out.append(chr(int(octal.group(1), 8)))
            index += octal.end()
            continue
        # \xhh (1-2 hex digits) -> one byte
        hexa = re.match(r"\\x([0-9a-fA-F]{1,2})", text[index:])
        if hexa:
            out.append(chr(int(hexa.group(1), 16)))
            index += hexa.end()
            continue
        if nxt in _PG_ESCAPE_CHARS:
            out.append(_PG_ESCAPE_CHARS[nxt])
        elif nxt == "\\":
            out.append("\\")
        else:
            # Unknown escape: keep the character, drop the backslash.
            out.append(nxt)
        index += 2
    return "".join(out)


def _function_name(func: Any) -> Optional[str]:
    """Normalize one call node to the bare function name the engine will resolve.

    Two spellings used to slip past the ban list (2026-09-23 audit, reproduced):

    * ``exp.Anonymous.this`` is an ``exp.Identifier`` when the SQL quotes the name.
      ``str()`` on it returns the *quoted* text ``'"pg_read_file"'``, which never
      compares equal to the bare ban-list entry. Taking ``Identifier.this`` yields
      the real identifier.
    * PostgreSQL ``U&"..."`` escapes let ``pg\\005fread_file`` resolve to
      ``pg_read_file``, so the escape has to be decoded before comparing.

    sqlglot already drops any schema qualifier (``pg_catalog.pg_read_file`` arrives as
    ``pg_read_file``), so no extra handling is needed for that form.
    """

    from sqlglot import exp

    if isinstance(func, exp.Anonymous):
        raw = func.this
    else:
        raw = func.sql_name()
    if isinstance(raw, exp.Identifier):
        raw = raw.this
    if raw is None:
        return None
    name = _decode_pg_unicode_escape(str(raw)).strip().lower()
    return name or None


def _iter_function_names(node: Any) -> set:
    """Collect every SQL function invoked in the parsed tree, normalized.

    sqlglot reports unknown functions as ``exp.Anonymous`` whose ``sql_name()`` is the
    literal string "ANONYMOUS", so the real name has to be read from ``this``.
    """

    from sqlglot import exp

    names = set()
    for func in node.find_all(exp.Func):
        name = _function_name(func)
        if name:
            names.add(name)
    return names


def _iter_unknown_function_names(node: Any) -> set:
    """Collect only the functions sqlglot did **not** recognize (``exp.Anonymous``).

    This is the set the allow-list has to gate. sqlglot parses standard SQL
    constructs into dedicated nodes -- ``COUNT``→``exp.Count``, ``CAST``→
    ``exp.Cast``, ``EXTRACT``→``exp.Extract``, ``ROW_NUMBER``→``exp.RowNumber``,
    ``CASE``→``exp.Case`` -- which are grammar, not user-chosen function names, and
    are read-only by construction. Only unrecognized names (``ST_Area``, ``RANK``,
    ``PERCENTILE_CONT`` …) can be arbitrary, so only they need vetting. Gating the
    dedicated nodes too would force registering every dialect keyword and produce
    false rejections on ordinary queries.
    """

    from sqlglot import exp

    names = set()
    for func in node.find_all(exp.Anonymous):
        name = _function_name(func)
        if name:
            names.add(name)
    return names


def allowed_function_names() -> frozenset:
    """Effective allow-list: built-in names plus the operator override env var."""

    override = os.environ.get(ALLOWED_FUNCTION_OVERRIDE_ENV) or ""
    if not override.strip():
        return ALLOWED_SQL_FUNCTIONS
    extra = {
        item.strip().lower()
        for item in override.split(",")
        if item.strip()
    }
    return ALLOWED_SQL_FUNCTIONS | extra


def _cte_reference_ids(tree: Any) -> set:
    """Return ids of Table nodes that the database resolves to an in-scope CTE."""

    try:
        from sqlglot import exp
        from sqlglot.optimizer.scope import Scope, traverse_scope

        scopes = traverse_scope(tree)
    except Exception as exc:
        raise PlanRejected("SQL CTE scope cannot be resolved") from exc
    refs = set()
    for scope in scopes:
        for table in scope.tables:
            if table.args.get("db") or table.args.get("catalog") or not isinstance(table.this, exp.Identifier):
                continue
            source = scope.cte_sources.get(table.name)
            if isinstance(source, Scope) and source.is_cte:
                refs.add(id(table))
    return refs


_MAX_GENERATED_CALENDAR_MONTHS = 500


def _literal_month_start(node: Any) -> Optional[date]:
    from sqlglot import exp

    if (type(node) is not exp.Cast or not isinstance(node.to, exp.DataType)
            or node.to.this != exp.DataType.Type.DATE or node.to.expressions
            or not isinstance(node.this, exp.Literal) or not node.this.is_string
            or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-01", node.this.this)):
        return None
    try:
        return date.fromisoformat(node.this.this)
    except ValueError:
        return None


def _validate_calendar_table(table: Any) -> None:
    """Accept only the built-in PostgreSQL table function for a bounded month calendar."""
    from sqlglot import exp

    schema = table.args.get("db")
    series = table.this
    if (str(series.meta.get("name", "")).lower() != "generate_series" or table.catalog
            or schema is not None and not (isinstance(schema, exp.Identifier) and schema.name == "pg_catalog")):
        raise PlanRejected("SQL calendar must use the PostgreSQL built-in pg_catalog.generate_series")
    start = _literal_month_start(series.args.get("start"))
    end = _literal_month_start(series.args.get("end"))
    step = series.args.get("step")
    if not (series.args.get("is_end_exclusive") is None and start and end
            and isinstance(step, exp.Interval) and isinstance(step.this, exp.Literal)
            and step.this.this == "1" and str(step.args.get("unit")).upper() == "MONTH"):
        raise PlanRejected(
            "SQL calendar requires literal DATE 'YYYY-MM-01' start and end values and INTERVAL '1 month' as its three arguments; "
            "alternatively query observed months with GROUP BY"
        )
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if not 1 <= months <= _MAX_GENERATED_CALENDAR_MONTHS:
        raise PlanRejected(f"SQL calendar must cover 1 to {_MAX_GENERATED_CALENDAR_MONTHS} months")


def _validate_sql(plan: SQLPlan, context: PolicyContext) -> str:
    sql = plan.logical_sql.strip()
    if not re.match(r"(?is)^\s*(SELECT|WITH)\b", sql):
        raise PlanRejected("only SELECT or CTE queries are allowed")
    db_type = context.db_types.get(plan.datasource_ref, "postgresql")
    dialect = get_dialect(db_type)
    sqlglot_name = dialect.sqlglot if dialect else "sqlite" if db_type == "sqlite" else "postgres"
    # PostgreSQL uses # as an operator, including #> and #>> for JSON paths.
    hash_comments = sqlglot_name != "postgres"
    # 先剥离字符串字面量再查注释：既补上 MySQL 的 `#`，也避免把
    # `WHERE name = 'a--b'` 这类正常字面量误判成注释。
    literal_free_sql = _strip_string_literals(sql)
    if "--" in literal_free_sql or "/*" in literal_free_sql or (hash_comments and "#" in literal_free_sql):
        raise PlanRejected("SQL comments are not allowed")
    # The regex strip above ignores identifiers quoted with ` or ". A quote
    # inside one could hide a MySQL executable comment, so scan quote-aware too.
    if "/*!" in sql or "/*+" in sql or _has_sql_comment(sql, hash_comments=hash_comments):
        raise PlanRejected("SQL comments are not allowed")
    if "__product_limit" in sql:
        raise PlanRejected("reserved parameter name")
    placeholders = set(SQL_PLACEHOLDER.findall(literal_free_sql))
    parameters = {parameter.name for parameter in plan.parameters}
    if placeholders != parameters:
        raise PlanRejected("SQL placeholders and typed parameters must match")
    # Fail closed: an empty projection means nothing may be queried, rather than
    # skipping parse and allowlist checks entirely (the previous fail-open branch).
    if not context.allowed_relations:
        raise PlanRejected("workspace has no authorized relations for SQL querying")

    engine_label = dialect.key if dialect else "PostgreSQL"
    supports_calendar = db_type in {"postgresql", "postgres", "postgis"}
    try:
        from sqlglot import Dialect, exp
        from sqlglot.tokens import TokenType
    except ImportError as exc:  # pragma: no cover - dependency is pinned in the image
        # No regex fallback: the fallback could not see comma joins or subqueries,
        # silently downgrading the policy engine with no operator signal.
        raise PlanRejected("SQL policy engine is unavailable") from exc

    try:
        parser_dialect = Dialect.get_or_raise(sqlglot_name)
        if supports_calendar:
            # The PostgreSQL parser maps additional spellings to the same node.
            # Keep the original function name for the narrow calendar exception.
            parser_dialect.PRESERVE_ORIGINAL_NAMES = True
        tokens = parser_dialect.tokenize(sql)
        statements = parser_dialect.parser().parse(tokens, sql)
    except Exception as exc:
        raise PlanRejected(f"SQL cannot be parsed as {engine_label}") from exc
    if any(token.comments for token in tokens):
        raise PlanRejected("SQL comments are not allowed")
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise PlanRejected(f"only one {engine_label} query is allowed")
    tree = statements[0]
    # Inspect operations, not words: `comment`, `AS do` and 'Set A' are data.
    # Recurse because a SELECT root can contain a modifying CTE or SELECT INTO.
    mutation_types = tuple(getattr(exp, name) for name in (
        'DML', 'DDL', 'Into', 'Command', 'Set', 'Use', 'Transaction', 'Commit',
        'Rollback', 'Grant', 'Revoke', 'Attach', 'Detach', 'Pragma', 'Copy', 'LoadData'
    ) if hasattr(exp, name))
    if any(isinstance(node, mutation_types) for node in tree.walk()):
        raise PlanRejected('SQL contains a prohibited operation')
    if tree.find(exp.SessionParameter) or tree.find(exp.Parameter):
        raise PlanRejected('SQL session and system variables are not allowed')

    # Qualified vendor packages can reuse an otherwise allowed name (for example
    # DBMS_LOB.SUBSTR). A bare function allow-list must not authorize the package.
    for qualified in tree.find_all(exp.Dot):
        if isinstance(qualified.expression, exp.Func):
            namespace = _decode_pg_unicode_escape(qualified.this.sql()).replace('"', '').replace('`', '').lower().split('.')[-1]
            if namespace.startswith(('dbms_', 'utl_', 'sf_', 'sp_')):
                raise PlanRejected('SQL calls a privileged vendor package')

    blocked = _iter_function_names(tree) & FORBIDDEN_SQL_FUNCTIONS
    if blocked:
        raise PlanRejected(
            "SQL calls a prohibited function: " + ", ".join(sorted(blocked))
        )
    # 函数白名单（fail-closed）：黑名单只拦「已知坏名字」，白名单保证「没登记过
    # 的名字一律不放行」。只校验 sqlglot 未识别的 Anonymous 节点——它认识的
    # 标准构造（COUNT/CAST/EXTRACT/CASE…）是只读语法，不参与判定。
    unknown = _iter_unknown_function_names(tree)
    allowed_functions = allowed_function_names()
    if db_type == 'sqlite':
        from ..sqlite_connection import FUNCTIONS
        allowed_functions |= FUNCTIONS | {'datetime', 'julianday', 'time', 'unixepoch'}
    unauthorized = unknown - allowed_functions
    if unauthorized:
        raise PlanRejected(
            "SQL calls a function outside the allow-list: "
            + ", ".join(sorted(unauthorized))
            + "（请使用当前插件版本支持的只读函数）"
        )

    if tree.find(exp.Lock):
        # FOR UPDATE / FOR SHARE / LOCK IN SHARE MODE take row locks on the
        # customer's database even inside a read-only transaction.
        raise PlanRejected("SQL locking reads are not allowed")
    # Grants are physical catalog names. Quoting is syntax in SQL only.
    oracle = db_type in {"oracle", "dameng"}
    ascii_lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    physical = {tuple(name.split(".", 1)) for name in context.allowed_relations}
    bind_postgres = db_type in {"postgresql", "postgres", "postgis", "tbase", "kingbase"} and context.relation_schema is not None
    if bind_postgres:
        physical = {parts if len(parts) > 1 else (context.relation_schema, *parts) for parts in physical}
    if db_type == "sqlite":
        physical = {tuple(part.translate(ascii_lower) for part in parts) for parts in physical}
    # Case folding must also precede CTE lookup. A quoted alias can otherwise
    # conceal an identically spelled unquoted physical table from the allowlist.
    for identifier in tree.find_all(exp.Identifier):
        name = identifier.name
        if db_type == "sqlite":
            identifier.set("this", name.translate(ascii_lower))
        elif not identifier.args.get("quoted") and db_type != "mysql":
            identifier.set("this", name.upper() if oracle else name.translate(ascii_lower))
    # Only references that resolve to a CTE in scope are exempt. A same-named
    # schema-qualified table, a self-reference inside a non-recursive CTE, or a
    # forward reference is a real table and must pass the allowlist.
    cte_refs = _cte_reference_ids(tree)
    tables = [table for table in tree.find_all(exp.Table) if id(table) not in cte_refs]
    # Built-in value expansion reads an expression, not a new physical table.
    # Still inspect every nested physical reference; never exempt arbitrary UDFs
    # or schema-qualified functions on the strength of a matching function name.
    value_functions = {'sqlite': {'json_each', 'json_tree'},
                       'postgresql': {'regexp_split_to_table', 'json_array_elements', 'jsonb_array_elements', 'json_array_elements_text', 'jsonb_array_elements_text'},
                       'postgres': {'regexp_split_to_table', 'json_array_elements', 'jsonb_array_elements', 'json_array_elements_text', 'jsonb_array_elements_text'}}
    allowed_value_functions = value_functions.get(db_type, value_functions.get('postgresql', set()) if db_type in ('tbase','kingbase') else set())
    calendars = {}
    if supports_calendar:
        for table in tables:
            if isinstance(table.this, exp.ExplodingGenerateSeries):
                _validate_calendar_table(table)
                calendars[id(table)] = table

    def value_expansion(t):
        return id(t) in calendars or (
            not t.db and not t.catalog and isinstance(t.this, exp.Anonymous) and t.this.name.lower() in allowed_value_functions
        )

    if not any(not value_expansion(t) for t in tables):
        raise PlanRejected("SQL does not reference any relation")
    changed = False
    for table in tables:
        if value_expansion(table):
            continue
        if FORBIDDEN_SYSTEM_RELATION.search(table.sql(dialect=sqlglot_name)):
            raise PlanRejected('SQL contains a prohibited relation')
        if not all(isinstance(part, exp.Identifier) for part in table.parts):
            raise PlanRejected("SQL table reference cannot be matched to a physical relation")
        actual = tuple(part.name for part in table.parts)
        matches = {allowed for allowed in physical if actual and allowed[-len(actual):] == actual}
        if not matches:
            if oracle:
                raise PlanRejected('SQL references a physical relation outside the authorized Oracle/DM catalog')
            raise PlanRejected(
                "SQL references a relation outside the workspace projection"
            )
        if bind_postgres and len(actual) == 1:
            if len(matches) != 1:
                raise PlanRejected("SQL relation needs an explicit authorized schema")
            schema, _ = next(iter(matches))
            table.set("db", exp.to_identifier(schema, quoted=True))
            changed = True
    if calendars:
        # A quoted mixed-case identifier is a different PostgreSQL function.
        if any(token.token_type == TokenType.IDENTIFIER and token.text.lower() == "generate_series"
               and token.text != "generate_series" and following.token_type == TokenType.L_PAREN
               for token, following in zip(tokens, tokens[1:])):
            raise PlanRejected("SQL calendar must use the PostgreSQL built-in pg_catalog.generate_series")
        # Bind newly admitted table calls to the built-in, independently of the
        # connection's search_path. The executor already renders this same AST
        # dialect to apply its row budget.
        for table in calendars.values():
            table.set("db", exp.to_identifier("pg_catalog"))
        changed = True
    return tree.sql(dialect=sqlglot_name) if changed else plan.logical_sql


def _plan_target(plan: Any) -> str:
    """Datasource or layer a plan points at, for log lines only."""

    return str(
        getattr(plan, "datasource_ref", None)
        or getattr(plan, "primary_layer_ref", None)
        or "-"
    )


def validate_plan(
    raw_plan: Dict[str, Any], context: PolicyContext
) -> ValidatedExecutionPlan:
    raw_mode = raw_plan.get("mode") if isinstance(raw_plan, dict) else None
    raw_plan_id = raw_plan.get("plan_id") if isinstance(raw_plan, dict) else None
    try:
        plan = PLAN_ADAPTER.validate_python(raw_plan)
    except Exception as exc:
        logger.warning(
            "policy.reject reason=contract_mismatch mode=%s plan_id=%s detail=%s",
            safe(raw_mode),
            safe(raw_plan_id),
            safe(exc),
        )
        if isinstance(exc, ValidationError):
            # A generic refusal sent the 0.7.8 field team guessing at limits.
            # Name the failing field (never row values) so the first failure is
            # actionable; the rejection itself is unchanged.
            errors = exc.errors()
            if raw_mode == "sql":
                errors = [e for e in errors if e["loc"] and str(e["loc"][0]) == "SQLPlan"] or errors
            fields = "; ".join(
                ".".join(str(part) for part in e["loc"]) + ": " + e["msg"][:120]
                for e in errors[:3]
            )
            raise PlanRejected("candidate plan does not match V3 contract (" + fields + ")") from exc
        raise PlanRejected("candidate plan does not match V3 contract") from exc
    try:
        return _validate_authorized_plan(plan, context)
    except PlanRejected as exc:
        # A rejection used to be indistinguishable from a flaky planner call, which
        # sent weeks of debugging down the wrong path. Every refusal now says why.
        logger.warning(
            "policy.reject reason=%s mode=%s plan_id=%s workspace=%s target=%s "
            "params=%s sql=%s",
            safe(exc),
            safe(getattr(plan, "mode", None)),
            safe(getattr(plan, "plan_id", None)),
            safe(plan.workspace_id),
            _plan_target(plan),
            param_names(getattr(plan, "parameters", None)),
            sql_digest(getattr(plan, "logical_sql", None)),
        )
        raise


def _validate_authorized_plan(
    plan: Any, context: PolicyContext
) -> ValidatedExecutionPlan:
    if plan.workspace_id != context.workspace_id:
        raise PlanRejected("workspace mismatch")
    if isinstance(plan, SQLPlan):
        if plan.datasource_ref not in context.allowed_datasource_refs:
            raise PlanRejected("datasource is not authorized")
        sql = _validate_sql(plan, context)
        if sql != plan.logical_sql:
            plan = plan.model_copy(update={"logical_sql": sql})
    else:
        refs = {plan.primary_layer_ref}
        if plan.secondary_layer_ref:
            refs.add(plan.secondary_layer_ref)
        if not refs.issubset(set(context.allowed_layer_refs)):
            raise PlanRejected("GIS layer is not authorized")
    sanitized = plan.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(sanitized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ValidatedExecutionPlan(
        plan_id=plan.plan_id,
        mode=plan.mode,
        workspace_id=plan.workspace_id,
        compiled_hash=digest,
        sanitized_plan=sanitized,
    )
