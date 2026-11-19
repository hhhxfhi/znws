"""Resolve relative query dates from the Harness client's zone, not data coverage."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def temporal_context(client_zone, now=None):
    requested=client_zone
    try:
        zone=ZoneInfo(client_zone)
    except (ZoneInfoNotFoundError,ValueError,TypeError):
        zone=timezone.utc
        client_zone='UTC'
    instant=(now or datetime.now(timezone.utc)).astimezone(zone)
    return {'current_time':instant.isoformat(),'current_date':instant.date().isoformat(),
            'current_month':instant.strftime('%Y-%m'),'time_zone':client_zone,
            'zone_fallback':requested!=client_zone,
            'note':'今年、本月按当前客户端时间解析；最新可用月份按数据目录解析。二者不能互相替代；当前月可能不是完整月份。'}
