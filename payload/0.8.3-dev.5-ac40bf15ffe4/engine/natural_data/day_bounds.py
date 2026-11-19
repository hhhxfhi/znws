"""Calendar-day bounds for snapshot queries; independent of table and metric names."""
import calendar
import re
from datetime import date, timedelta

from sqlglot import exp
from .temporal_context import temporal_context


def snapshot_day(host):
    """Resolve one explicit day/month-end or the current valid snapshot.

    Unsupported periods and explicit event-date restrictions are not guessed.
    Relative days use the native client's zone, never the machine's SQL zone.
    """
    host = host or {}
    today = date.fromisoformat(temporal_context(host.get('client_time_zone', 'UTC'))['current_date'])
    phrases = [*host.get('recent_questions', []), host.get('user_text', '')]
    for text in reversed(phrases):
        if re.search(r'(?:发证|签发|颁发)(?:时间|日期)?[^，。；]{0,12}(?:早于|之前|当年|年度|期间|之后|以来)', text):
            return None
        # An explicit calendar day wins over conversational "现在/当前/目前"
        # ("我现在想查2025年底"). A second day word ("今天", "至今") makes
        # it a comparison or a range, which is not one snapshot.
        explicit = _explicit_days(text, today)
        if explicit is not None:
            if len(explicit) != 1 or re.search(r'今天|今日|昨天|昨日|至今', text):
                return None
            return explicit[0]
        if re.search(r'今天|今日|当前|目前|现在|至今', text):
            return today - timedelta(days=1) if re.search(r'(?:今天|今日)(?:之前|以前|前)', text) else today
        if re.search(r'昨天|昨日', text):
            return today - timedelta(days=1)
        if re.search(r'20\d{2}|去年|前年|上月|本月|季度|年度|年末|年底', text):
            return None
    return today


def _explicit_days(text, today):
    """Explicit calendar days in one phrase; None when it names none.

    An empty list means a date is named but is not one closed day (a range
    or month-start/mid wording); callers must not guess such a snapshot.
    """
    days = []
    for y, m, chinese_day, iso_day in re.findall(
            r'(?<!\d)(20\d{2})(?:年|[-/])(\d{1,2})(?:月(?:\s*(\d{1,2})[日号])?|[-/](\d{1,2}))?', text):
        try:
            year, month = int(y), int(m)
            days.append(date(year, month, int(chinese_day or iso_day) if chinese_day or iso_day else calendar.monthrange(year, month)[1]))
        except ValueError:
            return []
    for y in re.findall(r'(?<!\d)(20\d{2})\s*年\s*(?:年底|年末|底|末)', text):
        days.append(date(int(y), 12, 31))
    for word in re.findall(r'(去年|前年)\s*(?:年底|年末|底|末)', text):
        days.append(date(today.year - (1 if word == '去年' else 2), 12, 31))
    if re.search(r'上(?:个)?月\s*(?:月底|月末|底|末)', text):
        days.append(today.replace(day=1) - timedelta(days=1))
    if not days:
        return None
    if re.search(r'月初|月中|上旬|中旬|下旬|之前|以前|之后|以来', text):
        return []
    return days


def inclusive_upper_day(value, *, strict, today):
    """Map < next-day / <= day to the included date; unknown expressions stay unknown."""
    while isinstance(value, (exp.Cast, exp.Paren)):
        value = value.this
    if isinstance(value, exp.CurrentDate):
        bound = today
    elif isinstance(value, exp.Literal) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value.this)):
        try:
            bound = date.fromisoformat(str(value.this))
        except ValueError:
            return None
    else:
        return None
    return bound - timedelta(days=1) if strict else bound
