"""Calendar rules that are not about Discord or Redis.

Duplicate detection, the daily model budget, event ids, and the plain-words
rendering the slash command and the agent tool both use.
"""

import datetime as dt
import re
import secrets
from typing import Iterable, List, Optional, Sequence

from bot.domain.calendar.models import DEFAULT_TIMEZONE, CalendarEvent, zone

DEFAULT_TIMEZONE_NAME = DEFAULT_TIMEZONE

# Model calls per server per local day. The regex gate keeps most chat away
# from the model; this is the backstop for a very busy server or a gate that
# turns out to be leakier than expected. Comfortably more than a day's real
# plans, and still pennies.
DAILY_MODEL_BUDGET = 400

# Past events are kept this long so "what did we have last week" still works,
# then trimmed on the next write.
KEEP_PAST_DAYS = 90

DEFAULT_UPCOMING_DAYS = 14
MAX_UPCOMING_DAYS = 365

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"the", "an", "of", "for", "and", "at", "on", "in", "to", "with"}


def new_event_id() -> str:
    """Six lowercase hex characters: short enough to type into /calendar remove."""
    return secrets.token_hex(3)


def title_tokens(title: str) -> set:
    return {w for w in _WORD.findall((title or "").lower()) if len(w) > 1 and w not in _STOPWORDS}


def is_duplicate(candidate_title: str, candidate_date: str, existing: Iterable[CalendarEvent]) -> Optional[CalendarEvent]:
    """The existing event this one repeats, if any.

    Same day and one title inside the other, or the shorter title's words
    mostly present in the longer: "game night" and "Game night at Sam's" are
    one event; "game night" and "game stream" are not.
    """
    tokens = title_tokens(candidate_title)
    lowered = (candidate_title or "").lower().strip()
    for event in existing:
        if event.date != candidate_date:
            continue
        other = (event.title or "").lower().strip()
        if lowered and other and (lowered in other or other in lowered):
            return event
        other_tokens = title_tokens(event.title)
        if not tokens or not other_tokens:
            continue
        contained = len(tokens & other_tokens) / float(min(len(tokens), len(other_tokens)))
        if contained >= 0.6:
            return event
    return None


def local_day(now: dt.datetime, tz_name: str) -> str:
    return now.astimezone(zone(tz_name)).strftime("%Y%m%d")


def upcoming_window(now: dt.datetime, tz_name: str, days: int) -> "tuple":
    """(start_epoch, end_epoch) covering today through `days` days ahead, local."""
    days = max(1, min(int(days), MAX_UPCOMING_DAYS))
    tz = zone(tz_name)
    local = now.astimezone(tz)
    start = dt.datetime(local.year, local.month, local.day, tzinfo=tz)
    end = start + dt.timedelta(days=days)
    return start.timestamp(), end.timestamp()


def past_cutoff_epoch(now: dt.datetime) -> float:
    return (now - dt.timedelta(days=KEEP_PAST_DAYS)).timestamp()


def is_over(event: CalendarEvent, now: dt.datetime) -> bool:
    """True once the event's last day has ended in its own zone."""
    tz = zone(event.timezone)
    today = now.astimezone(tz).date()
    return event.last_day() < today


# --------------------------------------------------------------------- display


def _time_words(event: CalendarEvent) -> str:
    if not event.time:
        return ""
    hour, minute = (int(p) for p in event.time.split(":")[:2])
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return "%d:%02d%s" % (hour12, minute, suffix) if minute else "%d%s" % (hour12, suffix)


def day_heading(date_iso: str, today: Optional[dt.date] = None) -> str:
    day = dt.date.fromisoformat(date_iso)
    label = "%s %s %d" % (day.strftime("%a"), day.strftime("%b"), day.day)
    if today is not None:
        if day == today:
            label += " (today)"
        elif day == today + dt.timedelta(days=1):
            label += " (tomorrow)"
    return label


def describe_event(event: CalendarEvent, show_id: bool = True) -> str:
    """One line: "7pm — Game night — by Sam `a1b2c3`"."""
    parts: List[str] = []
    when = _time_words(event)
    if event.end_date and event.end_date != event.date:
        try:
            last_day = dt.date.fromisoformat(event.end_date)
            last = "%s %d" % (last_day.strftime("%b"), last_day.day)
            when = ("through %s" % last) if not when else "%s, through %s" % (when, last)
        except ValueError:
            pass
    if when:
        parts.append(when)
    parts.append(event.title)
    tail = []
    if event.author_name:
        tail.append("by %s" % event.author_name)
    if show_id and event.id:
        tail.append("`%s`" % event.id)
    line = " — ".join(parts)
    if tail:
        line += "  ·  " + ", ".join(tail)
    return line


def render_upcoming(
    events: Sequence[CalendarEvent],
    now: dt.datetime,
    tz_name: str,
    show_ids: bool = True,
    with_links: bool = False,
) -> str:
    """Events grouped by day, in order. '' when there are none."""
    if not events:
        return ""
    today = now.astimezone(zone(tz_name)).date()
    ordered = sorted(events, key=lambda e: (e.date, e.time or "", e.title.lower()))
    lines: List[str] = []
    current = None
    for event in ordered:
        if event.date != current:
            current = event.date
            lines.append("**%s**" % day_heading(current, today))
        line = "• " + describe_event(event, show_id=show_ids)
        if with_links and event.jump_url:
            line += " [↗](%s)" % event.jump_url
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------- typed input

_NUMERIC = re.compile(r"^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$")
_CLOCK_IN = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$", re.IGNORECASE)


def parse_date_input(text: str, today: dt.date) -> Optional[dt.date]:
    """A day from what someone typed: 2026-12-05, 12/5, 12/5/2026, 'today', 'tomorrow'.

    A month/day with no year is the next time that day comes around.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return None
    if raw == "today":
        return today
    if raw == "tomorrow":
        return today + dt.timedelta(days=1)
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        pass
    match = _NUMERIC.match(raw)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    year_text = match.group(3)
    try:
        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000
            return dt.date(year, month, day)
        candidate = dt.date(today.year, month, day)
        if candidate < today:
            candidate = dt.date(today.year + 1, month, day)
        return candidate
    except ValueError:
        return None


def parse_time_input(text: Optional[str]) -> Optional[str]:
    """'7pm', '7:30 PM', '19:00' -> 'HH:MM'. None for blank, '' for unparseable."""
    raw = (text or "").strip().lower().replace(".", "")
    if not raw or raw in ("all day", "all-day", "none"):
        return None
    match = _CLOCK_IN.match(raw)
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "")[:1]
    if meridiem:
        if hour < 1 or hour > 12:
            return ""
        hour = hour % 12 + (12 if meridiem == "p" else 0)
    if hour > 23 or minute > 59:
        return ""
    return "%02d:%02d" % (hour, minute)
