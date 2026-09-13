"""
Время в системе.

Правило одно: хранить в UTC, показывать человеку по Маниле. Часовой пояс
всего сервера при этом не меняется — на Леново рядом работают другие
проекты, и глобальный сдвиг сломал бы их расписания.

Филиппины не переходят на летнее время, смещение постоянное (UTC+8),
поэтому при отсутствии базы часовых поясов в системе используется
фиксированное смещение — результат тот же.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

MANILA_TZ_NAME = "Asia/Manila"
_MANILA_FALLBACK = timezone(timedelta(hours=8), "PHT")


def manila_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(MANILA_TZ_NAME)
    except Exception:  # noqa: BLE001 - нет tzdata: постоянное смещение подходит
        return _MANILA_FALLBACK


def parse_utc(value: str) -> Optional[datetime]:
    """Разбирает ISO-время. Наивное значение считается UTC."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        try:
            moment = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def to_manila(value: str | datetime | None) -> Optional[datetime]:
    moment = value if isinstance(value, datetime) else parse_utc(str(value or ""))
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(manila_tz())


def manila_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(manila_tz())


def manila_stamp(value: str | datetime | None = None) -> str:
    """Человекочитаемая метка времени по Маниле."""
    moment = to_manila(value) if value is not None else manila_now()
    if moment is None:
        return ""
    return moment.strftime("%d.%m.%Y %H:%M") + " по Маниле"


def manila_date(value: str | datetime | None = None) -> str:
    moment = to_manila(value) if value is not None else manila_now()
    return moment.strftime("%d.%m.%Y") if moment else ""
