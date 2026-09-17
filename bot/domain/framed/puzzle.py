"""Framed puzzle numbers, calendar days, and scores.

framed.wtf numbers its daily puzzle by calendar days since launch: #1 was
2022-03-12, and the next puzzle unlocks at the player's local midnight. This
server plays on Pacific time, so every date here is a calendar date in the
configured timezone, and a result counts for the day it was posted.

A score is the guess that got it (1-6), or 0 for a miss. Points are 7 - score
for a solve and 0 for a miss: a 1 is worth 6 points, a 6 is worth 1.
"""

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FRAMED_EPOCH = date(2022, 3, 12)
DEFAULT_TIMEZONE = "America/Los_Angeles"
FAIL = 0
MAX_GUESSES = 6

# A day is final this long after its midnight. Message timestamps come from
# Discord, not the posting client, so this only covers clock skew on our side.
DAY_GRACE = timedelta(minutes=10)


def get_tz(name: Optional[str]) -> tzinfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def puzzle_for_date(day: date) -> int:
    return (day - FRAMED_EPOCH).days + 1


def date_for_puzzle(puzzle: int) -> date:
    return FRAMED_EPOCH + timedelta(days=puzzle - 1)


def local_date(moment: datetime, tz: tzinfo) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).date()


def day_start(day: date, tz: tzinfo) -> datetime:
    """Local midnight at the start of `day`, as an aware UTC datetime."""
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(timezone.utc)


def day_end(day: date, tz: tzinfo) -> datetime:
    return day_start(day + timedelta(days=1), tz)


def latest_complete_day(now: datetime, tz: tzinfo) -> date:
    """The most recent day whose results can no longer change."""
    return local_date(now - DAY_GRACE, tz) - timedelta(days=1)


def date_range(start: date, end: date) -> Iterator[date]:
    """Every date from start to end, inclusive."""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def points_for(score: int) -> int:
    if 1 <= score <= MAX_GUESSES:
        return MAX_GUESSES + 1 - score
    return 0


def format_score(score: int) -> str:
    return str(score) if 1 <= score <= MAX_GUESSES else "X"
