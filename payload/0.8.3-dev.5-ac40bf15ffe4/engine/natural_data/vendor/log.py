"""Logging helpers for the product layer.

The product layer previously had no logging at all, which made every failure
invisible: a policy rejection looked identical to a flaky LLM call. This module
keeps the setup idempotent and provides redaction helpers so that call sites can
log freely without leaking query parameters or credentials.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, Optional

# Every logger the product layer creates hangs off this prefix, so operators can
# raise or silence the whole subsystem with a single logging configuration.
LOGGER_PREFIX = "dbgpt.product"

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DEFAULT_LEVEL = "INFO"
_MAX_SQL_CHARS = 300
_MAX_VALUE_CHARS = 120

_WS_RE = re.compile(r"\s+")
# Inline literals are stripped before anything reaches a log sink. Generated SQL
# frequently embeds real values ("WHERE name = '...'"), and logs are a place where
# that kind of data silently outlives its retention policy.
_SQL_LITERAL_RE = re.compile(r"'[^']*'")

_configured = False


def configure_logging(force: bool = False) -> None:
    """Attach a stderr handler to the product logger tree, once.

    Safe to call from import time and from startup hooks. Library code never
    installs handlers itself, so this stays opt-in for the serving process.
    """

    global _configured
    if _configured and not force:
        return
    level_name = (
        os.environ.get("DBGPT_PRODUCT_LOG_LEVEL") or _DEFAULT_LEVEL
    ).upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger(LOGGER_PREFIX)
    root.setLevel(level)
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(handler)
    # Do not double-print via the root logger when the app configures one.
    root.propagate = os.environ.get("DBGPT_PRODUCT_LOG_PROPAGATE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, e.g. ``dbgpt.product.plans``."""

    if not name.startswith(LOGGER_PREFIX):
        name = f"{LOGGER_PREFIX}.{name.lstrip('.')}"
    return logging.getLogger(name)


def sql_digest(sql: Optional[str], limit: int = _MAX_SQL_CHARS) -> str:
    """Collapse and truncate SQL for logging.

    Whitespace is folded so multi-line generated SQL stays on one log line, single
    quoted literals are blanked out, and the result is cut hard at ``limit``
    characters. Bound parameters never appear in the statement, but generated SQL
    often inlines real values, so they are removed here rather than per call site.
    """

    if not sql:
        return ""
    collapsed = _WS_RE.sub(" ", _SQL_LITERAL_RE.sub("''", str(sql))).strip()
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}...<truncated {len(collapsed) - limit} chars>"


def param_names(parameters: Optional[Iterable[Any]]) -> str:
    """Render only parameter *names*, never their values."""

    if not parameters:
        return "-"
    names = []
    for parameter in parameters:
        name = getattr(parameter, "name", None)
        if name is None and isinstance(parameter, dict):
            name = parameter.get("name")
        if name is not None:
            names.append(str(name))
    return ",".join(names) if names else "-"


def safe(value: Any, limit: int = _MAX_VALUE_CHARS) -> str:
    """Render a value for logs with control characters and length bounded."""

    if value is None:
        return ""
    text = _WS_RE.sub(" ", str(value)).strip()
    if len(text) > limit:
        return f"{text[:limit]}...<truncated>"
    return text


def event_fields(**kwargs: Any) -> Dict[str, Any]:
    """Build a flat extra dict, dropping empty values to keep lines short."""

    return {key: value for key, value in kwargs.items() if value not in (None, "", [])}
