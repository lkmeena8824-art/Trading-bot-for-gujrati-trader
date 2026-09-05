"""Market clock and quality-control primitives.

This module intentionally has no network or Telegram dependency. Keeping the
clock and slot rules pure makes the most important safety rules easy to test.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from config import (
    HOLIDAYS,
    MARKET_CLOSE_TIME,
    MARKET_OPEN_TIME,
    MARKET_TIMEZONE,
    NO_TRADE_CHECK_TIME,
    TRADE_WINDOWS,
)

MARKET_TZ = ZoneInfo(MARKET_TIMEZONE)


def now_ist() -> datetime:
    return datetime.now(MARKET_TZ)


def as_market_time(value: datetime | None = None) -> datetime:
    value = value or now_ist()
    if value.tzinfo is None:
        return value.replace(tzinfo=MARKET_TZ)
    return value.astimezone(MARKET_TZ)


def market_day(value: datetime | None = None) -> str:
    return as_market_time(value).date().isoformat()


def is_market_day(value: datetime | None = None) -> bool:
    current = as_market_time(value)
    return current.weekday() < 5 and current.date().isoformat() not in HOLIDAYS


def slot_for_time(value: datetime | None = None) -> str | None:
    current = as_market_time(value)
    if not is_market_day(current):
        return None
    current_time = current.time().replace(second=0, microsecond=0)
    for slot, (start, end) in TRADE_WINDOWS.items():
        if start <= current_time <= end:
            return slot
    return None


def is_trade_window(value: datetime | None = None) -> bool:
    return slot_for_time(value) is not None


def is_market_session_open(value: datetime | None = None) -> bool:
    current = as_market_time(value)
    if not is_market_day(current):
        return False
    current_time = current.time().replace(second=0, microsecond=0)
    return MARKET_OPEN_TIME <= current_time <= MARKET_CLOSE_TIME


def is_no_trade_checkpoint(value: datetime | None = None) -> bool:
    current = as_market_time(value)
    return current.time().hour == NO_TRADE_CHECK_TIME.hour and current.time().minute == NO_TRADE_CHECK_TIME.minute


def describe_slot(value: datetime | None = None) -> str:
    return slot_for_time(value) or "OUTSIDE_WINDOW"


def daily_trade_message() -> str:
    return "🛡️ NO TRADE ZONE: Market choppy hai. Aaj capital protect karna hi sabse badi jeet hai. Kal dhamaka hoga."
