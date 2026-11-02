"""Schedules for scheduled prompts: cron expressions in an IANA time zone.

Pure functions, no Discord or Redis. Everything takes and returns aware UTC
datetimes; the zone only matters for working out *when* a cron expression
fires, which is done in local time so "9am every day" stays 9am across a DST
change.

The rules from the Phase 5 plan live here:

* **Hourly at most** (`MIN_INTERVAL_SECONDS`). Cron can space runs unevenly --
  `0,30 9 * * *` fires twice in half an hour, then waits a day -- so
  `validate_schedule` checks the gaps between the next `GAP_CHECK_RUNS`
  occurrences rather than trying to read the expression.
* **A missed run runs once late if it's within half its interval**
  (`should_run_late`). The interval is the gap from the missed run to the one
  after it: 30 minutes of grace for an hourly job, 12 hours for a daily one.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

MIN_INTERVAL_SECONDS = 60 * 60

# Enough occurrences to cover a day of hourly runs twice over, and a month of
# daily ones. An expression irregular enough to hide a short gap beyond that is
# not something anyone will ask for in chat.
GAP_CHECK_RUNS = 48


# Steps next_run_after may take past times that aren't after `after`: at most
# the few occurrences inside a repeated fall-back hour.
_MAX_STEPS = 10


class ScheduleError(ValueError):
    """A schedule that can't be used. The message is fit to show a user."""


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ScheduleError(f"Unknown time zone: {tz!r}. Use a name like America/Los_Angeles.") from e


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("expected an aware datetime")
    return dt.astimezone(timezone.utc)


def next_run_after(cron: str, tz: str, after: datetime) -> datetime:
    """The first time strictly after `after` that `cron` fires in `tz`, in UTC.

    croniter is stepped through *naive* local wall-clock times and the zone is
    attached afterwards. Handing croniter an aware datetime gets DST wrong: on
    the day the clocks change, "9am daily" came out an hour off. Attaching the
    zone ourselves means a time the spring-forward gap skips (2:30am) runs at
    the equivalent instant just after it, a time the fall-back hour repeats
    runs once, at its first occurrence, and anything not after `after` -- the
    repeated hour again -- is stepped past.
    """
    zone = _zone(tz)
    after = _utc(after)
    itr = croniter(cron, after.astimezone(zone).replace(tzinfo=None))
    for _ in range(_MAX_STEPS):
        candidate = _utc(itr.get_next(datetime).replace(tzinfo=zone))
        if candidate > after:
            return candidate
    raise ScheduleError(f"Couldn't work out the next run of {cron!r}.")


def validate_schedule(cron: str, tz: str, now: datetime) -> None:
    """Raise ScheduleError unless `cron` in `tz` is a usable schedule."""
    _zone(tz)
    if len(cron.split()) != 5:
        raise ScheduleError(
            "A schedule needs exactly five cron fields: minute hour day month weekday."
        )
    if not croniter.is_valid(cron):
        raise ScheduleError(f"Not a valid cron expression: {cron!r}.")

    previous = next_run_after(cron, tz, now)
    for _ in range(GAP_CHECK_RUNS - 1):
        following = next_run_after(cron, tz, previous)
        if (following - previous).total_seconds() < MIN_INTERVAL_SECONDS:
            raise ScheduleError("Scheduled prompts can run at most once an hour.")
        previous = following


def should_run_late(cron: str, tz: str, scheduled: datetime, now: datetime) -> bool:
    """Whether a run that was due at `scheduled` should still happen at `now`.

    True when `now` is within half the gap between `scheduled` and the run
    after it. An on-time run -- a tick a few seconds after `scheduled` -- is
    just the smallest case of this.
    """
    scheduled = _utc(scheduled)
    following = next_run_after(cron, tz, scheduled)
    grace = (following - scheduled) / 2
    return timedelta(0) <= _utc(now) - scheduled <= grace


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """An ISO-8601 string from the store as an aware UTC datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
