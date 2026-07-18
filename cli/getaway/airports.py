"""Airport timezone helpers."""

from datetime import date, datetime
from functools import cache
from zoneinfo import ZoneInfo

import airportsdata


@cache
def _db() -> dict:
    return airportsdata.load("IATA")


def zone(iata: str) -> ZoneInfo:
    return ZoneInfo(_db()[iata]["tz"])


def local_today(iata: str, now: datetime) -> date:
    return now.astimezone(zone(iata)).date()
