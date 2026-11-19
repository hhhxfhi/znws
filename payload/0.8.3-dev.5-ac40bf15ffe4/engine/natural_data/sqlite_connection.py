"""Read-only SQLite connections and explicit adapters for bundled SQL variants."""
from datetime import datetime
from decimal import Decimal
import math
from pathlib import Path
import re
import sqlite3

FUNCTIONS = frozenset({'nw_report_month', 'nw_decimal', 'nw_local_timestamp'})
_MONTH = re.compile(r'^[0-9]{4}-([0-9]+)')
_NON_AMOUNT = re.compile(r'[^0-9.\-]')
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')
_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?)?')


def _report_month(value):
    """Match the anchored digit capture used in the source SQL before its INT cast."""
    match = _MONTH.match(str(value))
    if not match:
        return None
    digits = match[1].lstrip('0') or '0'
    if len(digits) > 10 or int(digits) > 2**31 - 1:
        raise ValueError('报送月份超出整数范围')
    return int(digits)


def _decimal(value, strip_noise):
    """Apply a strict decimal conversion, with optional workbook money cleanup."""
    if value is None:
        return None
    if strip_noise not in (0, 1):
        raise ValueError('金额转换模式无效')
    text = str(value).strip()
    if strip_noise:
        text = _NON_AMOUNT.sub('', text)
        if not text:
            return None
    if not _DECIMAL.fullmatch(text):
        raise ValueError('金额不是有效数字')
    parsed = Decimal(text)
    if -(2**63) <= parsed < 2**63 and parsed == parsed.to_integral_value():
        return int(parsed)
    number = float(parsed)
    if not math.isfinite(number) or Decimal(str(number)) != parsed:
        raise ValueError('金额超出 SQLite 可表示精度')
    return number


def _local_timestamp(value):
    """Normalize ISO dates and local timestamps to microsecond comparison keys."""
    if value is None:
        return None
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError('日期需要 ISO 日期或本地时间')
    return datetime.fromisoformat(value).isoformat(sep=' ', timespec='microseconds')


def connect_readonly(path: Path, *, timeout=5):
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=timeout)
    conn.create_function('nw_report_month', 1, _report_month, deterministic=True)
    conn.create_function('nw_decimal', 2, _decimal, deterministic=True)
    conn.create_function('nw_local_timestamp', 1, _local_timestamp, deterministic=True)
    return conn
